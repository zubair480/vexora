"""The stream watch: notices "my stream stopped arriving though I did not stop it".

A band has two links, Bluetooth and the USB cable, and one program can be on each. They
share the band: its settings, its light and its streams. A stream goes to the program that
started it last (contract section 2.3). So when a second program on the other link starts
"motion", the first program gets no motion any more, with no notice, while its link stays
up and its commands are still answered. When that second program ends, the band stops the
stream altogether. Without this watch a program would go on waiting with no data.

How it works:
1. The sign (it costs nothing, no command is sent): a stream this program started and did
   not stop has sent nothing for 3 seconds (or 3 of its frame times, if that is longer)
   while the link is up. Streams that are silent by nature (taps, "battery on change")
   give no such sign; while one of those is on, the watch asks every 30 seconds.
2. The check: one STREAM_GET. It is only sent after a sign, so never while data flows.
   - The band says the stream is off: something else stopped it (a program on the other
     link took it and then ended, or stopped it).
   - The band says it is on, and still nothing arrived: it now goes to the other link.
3. The news: the event "stream-taken" with streams, stopped and message. Nothing is taken
   back by itself (two programs would then fight over the stream for ever):
   band.take_back() starts the streams again for this program.

On the USB cable a stream that the band reports as off is handled by the session watch
(transports/session_watch.py): there it means the band ended the session.

Tested with the pretend band only, not yet with two programs on a real band.
"""
import asyncio

from .transports.session_watch import fastest_frame_seconds

STREAM_TAKEN = "stream-taken"

# The times of the watch, in seconds. Tests make them shorter on one band: band._stream_watch.times.
STREAM_WATCH_TIMES = dict(
    tick=1.0,     # how often the watch looks (a timer, nothing is sent)
    silence=3.0,  # a stream on a timer sent nothing for this long: ask
    quiet=30.0,   # a stream that is silent by nature is on: ask this often
)
FIRST_STREAM = 0x10  # frame type 0x10 + n carries stream bit n (contract section 4)


class StreamWatch:
    def __init__(self, band) -> None:
        self.band = band
        self.times = dict(STREAM_WATCH_TIMES)
        self.asked = 0  # how many times the watch asked the band (STREAM_GET). For tests
        self.mine = 0  # mask of the streams this program started and has not stopped
        self.taken = 0  # of those: the ones reported with "stream-taken" and not started again since
        self._heard = {}  # stream bit -> when its last frame came (or when it was started)
        self._timer = None
        self._asking = False
        self._tick_at = self._since = self._asked_at = 0.0

    @staticmethod
    def _now() -> float:
        return asyncio.get_event_loop().time()

    def started(self, mask: int, active: int) -> None:
        """Band: STREAM_START was accepted. mask is what was asked for, active what the band says is on."""
        now = self._now()
        self.mine |= mask & active
        self.taken &= ~mask
        for bit in range(16):
            if mask >> bit & 1:
                self._heard[bit] = now

    def stopped(self, mask: int) -> None:
        """Band: STREAM_STOP was accepted for mask."""
        self.mine &= ~mask
        self.taken &= ~mask

    def reset(self) -> None:
        """Band: the link is gone, or the band was put to sleep: nothing is on any more."""
        self.mine = self.taken = 0
        self.stop()

    def _watched(self) -> int:
        return self.mine & ~self.taken if self.band._linked else 0

    def _arm(self) -> None:
        self._timer = asyncio.get_event_loop().call_later(self.times["tick"], self._tick)

    def update(self) -> None:
        """Called by Band after every answer: the timer runs only while a stream of this program is on."""
        if not self._watched():
            return self.stop()
        if self._timer is not None:
            return
        self._tick_at = self._since = self._asked_at = self._now()
        self._arm()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def heard(self, frame_type: int) -> None:
        """Called by Band for every frame."""
        if FIRST_STREAM <= frame_type < FIRST_STREAM + 16:
            self._heard[frame_type - FIRST_STREAM] = self._now()

    def _silent(self, now: float) -> int:
        """The watched streams that send on a timer and have been silent for too long."""
        mask, watched = 0, self._watched()
        for bit in range(16):
            if not watched >> bit & 1:
                continue
            frame = fastest_frame_seconds(self.band, 1 << bit)
            if frame is None:
                continue  # silent by nature
            if now - max(self._heard.get(bit, 0.0), self._since) >= max(self.times["silence"], 3 * frame):
                mask |= 1 << bit
        return mask

    def _timed(self) -> int:
        return sum(1 << bit for bit in range(16) if fastest_frame_seconds(self.band, 1 << bit) is not None)

    def _tick(self) -> None:
        band, times = self.band, self.times
        now = self._now()
        late = now - self._tick_at - times["tick"]
        self._tick_at = now
        self._timer = None
        if not self._watched():
            return
        self._arm()
        if band.reconnecting or band._critical or self._asking:
            return
        if late >= times["tick"]:
            self._since = now  # this program itself was stopped: that time is not silence of the band
            return
        if self._silent(now):
            asyncio.ensure_future(self._ask())
            return
        # Streams that are silent by nature show nothing: ask now and then. (On the USB cable the session watch does.)
        natural = self._watched() & ~self._timed()
        if natural and not getattr(band.transport, "session_watch", False) and now - self._asked_at >= times["quiet"]:
            asyncio.ensure_future(self._ask())

    async def _ask(self) -> None:
        """The check: one STREAM_GET. Band._exchange hands the answer to answer()."""
        self._asking = True
        self.asked += 1
        self._asked_at = self._now()
        try:
            await self.band._exchange("STREAM_GET", {})
        except Exception:
            pass  # no answer: the link itself is in trouble, and the program hears about that
        self._asking = False

    def answer(self, active: int) -> None:
        """Called by Band with the answer to every STREAM_GET. A stream of this program
        that the band says is off, or that is on and still silent, was taken: tell the
        program once."""
        band = self.band
        if not band._linked or band.reconnecting or band._critical:
            return
        off = self._watched() & ~active
        lost = off | (self._silent(self._now()) & active)
        if not lost:
            return
        self.taken |= lost
        asyncio.get_event_loop().call_soon(band._streams_taken, lost, off)  # after the answer went out
