"""The USB session watch: notices when the band ended the USB session by itself.

A band with firmware 1.1.3 ends the USB session of a computer that took no data at all for
about 5 seconds while streams were running (a program that was stuck). It sends no notice.
Afterwards the port is still open and commands are answered, but every stream is off and
the settings are back at their defaults. Without this watch a program would go on
believing it is connected while no data comes.

Only a transport with session_watch = True is watched (the USB cable). Over Bluetooth
nothing here runs: no timer and no command.

How it works, cheapest first:

1. Signs (they cost nothing, no command is sent):
   - streams that send on a timer are on, and no stream frame came for 2 seconds (or 3
     frame times of the fastest such stream, if that is longer). After a session end every
     stream is silent, so the fastest one shows it first;
   - this program itself was stopped for 3 seconds or more (the timer below came late);
   - an empty packet came in the middle of a session (the transport says so with
     on_session_hint). A band that paused for 200 ms sends one too, so this is only a
     reason to look at the stream frames for a moment;
   - only streams that are silent by nature are on (taps, battery on change) and nothing
     at all came for 30 seconds.
2. The check: one STREAM_GET. It is only sent after a sign, so never while data flows. If
   the band says a stream is off that this program switched on, the session was ended. If
   it says everything is still on, nothing happens, and the next check waits twice as long
   (at most 30 seconds) until a stream frame comes again.
3. The end: the port is closed and the link ends with reason "session-ended". With
   auto_reconnect the library opens the port again and sets everything again, exactly as
   after a pulled cable. Without it the program gets "disconnect".

Tested with the pretend band and fake ports only, not yet with a real band.
"""
import asyncio

from ..codec import STREAM_BITS

SESSION_ENDED = "session-ended"
SESSION_ENDED_TEXT = ("The band ended the USB session because this computer took no data for 5 seconds "
                      "(a page or program that was stuck). Streams were stopped.")

# The times of the watch, in seconds. Tests make them shorter on one band: band._watch.times.
WATCH_TIMES = dict(
    tick=1.0,      # how often the watch looks (a timer, nothing is sent)
    silence=2.0,   # streams on a timer are on and no stream frame came for this long: ask
    stall=3.0,     # the timer came this late: this program was stopped, ask once frames had time to come
    settle=0.5,    # after a sign, frames that come this soon may be old ones: they do not count
    quiet=30.0,    # only naturally silent streams are on and nothing came for this long: ask. Also the longest wait
)
FIRST_STREAM = 0x10  # frame types 0x10..0x1f are stream frames (contract section 4)


def session_ended_text(reconnecting: bool) -> str:
    """The sentence for the person: what happened and what happens next."""
    return SESSION_ENDED_TEXT + (" The library is connecting again and sets your streams and settings again."
                                 if reconnecting else " Connect again, and keep handlers short.")


def fastest_frame_seconds(band, mask: int):
    """The time between two frames of the fastest stream that sends on a timer, or None
    when no such stream is on (taps and "battery on change" are silent by nature)."""
    def on(stream: str) -> bool:
        return bool(mask >> STREAM_BITS[stream] & 1)
    ms = band._restore_intervals
    each = []
    if on("motion"):
        each.append(10 / (band._imu or {}).get("rate_hz", 25))  # 10 samples in a frame
    if on("motion2"):
        each.append(max(0.05, 1 / (band._m2 or {}).get("rate_hz", 25)))  # 50 ms in a frame, 1 sample or more
    if on("audio"):
        each.append(0.02)
    if on("mic_level"):
        each.append(ms.get(STREAM_BITS["mic_level"], 100) / 1000)
    if on("link"):
        each.append(ms.get(STREAM_BITS["link"], 1000) / 1000)  # Bluetooth only
    if on("activity"):
        each.append(ms.get(STREAM_BITS["activity"], 1000) / 1000)
    if on("temp"):
        whole = band._restore.get("TEMP_SET_INTERVAL", {}).get("seconds", 5)
        each.append(ms.get(STREAM_BITS["temp"], whole * 1000) / 1000)
    if on("battery") and ms.get(STREAM_BITS["battery"], 0) > 0:
        each.append(ms[STREAM_BITS["battery"]] / 1000)
    return min(each) if each else None


class SessionWatch:
    def __init__(self, band) -> None:
        self.band = band
        self.times = dict(WATCH_TIMES)
        self.asked = 0  # how many times the watch asked the band (STREAM_GET). For tests
        self.ending = False  # True while the watch is closing the link
        self._timer = None
        self._asking = False
        self._tick_at = self._since = self._heard_at = 0.0
        self._hint_at = None
        self._wait = 1
        # A transport that sees the bytes can tell us about an empty packet.
        if hasattr(band.transport, "on_session_hint"):
            band.transport.on_session_hint = self.hint

    @staticmethod
    def _now() -> float:
        return asyncio.get_event_loop().time()

    def _believed(self) -> int:
        """The streams this program switched on (what auto-reconnect would start again)."""
        return self.band._restore_mask

    def _watching(self) -> bool:
        band = self.band
        return bool(band._linked and getattr(band.transport, "session_watch", False) and self._believed())

    def _arm(self) -> None:
        self._timer = asyncio.get_event_loop().call_later(self.times["tick"], self._tick)

    def update(self) -> None:
        """Called by Band after every answer: start the timer when streams are on, stop it when none is."""
        if not self._watching():
            return self.stop()
        if self._timer is not None:
            return
        self._tick_at = self._since = self._now()
        self._heard_at = 0.0
        self._hint_at = None
        self._wait = 1
        self._arm()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def heard(self, frame_type: int) -> None:
        """Called by Band for every frame: a stream frame is a sign of life."""
        if self._timer is not None and FIRST_STREAM <= frame_type < FIRST_STREAM + 16:
            self._heard_at = self._now()
            self._wait = 1

    def hint(self) -> None:
        """A sign that the session may have ended (an empty packet in the middle of a session)."""
        self.update()  # the pretend band becomes a watched one when end_usb_session() is called
        if self._timer is not None and self._hint_at is None:
            self._hint_at = self._now()

    def _tick(self) -> None:
        band, times = self.band, self.times
        now = self._now()
        late = now - self._tick_at - times["tick"]
        self._tick_at = now
        self._timer = None
        if not self._watching():
            return
        self._arm()
        if band.reconnecting or self._asking:
            return
        fastest = fastest_frame_seconds(band, self._believed())
        if late >= times["stall"]:
            if self._hint_at is None:
                self._hint_at = now  # this program was stopped: look once frames had time to come
        elif late >= times["tick"]:
            self._since = now  # a short hiccup: that time is not silence of the band
        if self._hint_at is not None:
            first = self._hint_at + times["settle"]  # frames before this may be old ones from a buffer
            if now < first + min(max(times["settle"], 1.5 * (fastest or 0)), times["quiet"]):
                return
            alive = self._heard_at >= first
            self._hint_at = None
            if not alive:  # data flows: no command
                asyncio.ensure_future(self._ask())
            return
        limit = times["quiet"] if fastest is None else max(times["silence"], 3 * fastest)
        if now - max(self._heard_at, self._since) >= min(times["quiet"], limit * self._wait):
            asyncio.ensure_future(self._ask())

    async def _ask(self) -> None:
        """The check: one STREAM_GET. Band._exchange hands the answer to ended()."""
        self._asking = True
        self.asked += 1
        try:
            await self.band._exchange("STREAM_GET", {})
        except Exception:
            pass  # no answer: the commands of the program show that soon enough
        self._asking = False
        self._since = self._now()
        self._wait = min(self._wait * 2, 64)

    def ended(self, active: int) -> bool:
        """Called by Band with the answer to every STREAM_GET, before it is used. True =
        streams this program switched on are off: the band ended the session. The link is
        then ended with reason "session-ended" and what auto_reconnect has to set again is kept."""
        band = self.band
        if not getattr(band.transport, "session_watch", False) or not band._linked or band.reconnecting:
            return False
        if not self._believed() & ~active:
            return False
        asyncio.get_event_loop().call_soon(lambda: asyncio.ensure_future(self.end()))  # after the answer went out
        return True

    async def end(self) -> None:
        """End the link the normal way, with reason "session-ended"."""
        band = self.band
        if not band._linked or band.reconnecting or self.ending:
            return
        self.ending = True
        self.stop()
        try:
            # Close the port properly: the band sees a close, and the next open starts a clean session.
            try:
                await band.transport.close()
            except Exception:
                pass
            band._closed(SESSION_ENDED)
        finally:
            self.ending = False
