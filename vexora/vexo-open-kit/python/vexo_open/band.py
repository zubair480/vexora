"""Band: the friendly API. It turns calls like set_led("#00ff00") into contract commands
(codec.py), sends them one at a time over a transport, and turns the band's data into events.
"""
import asyncio
import inspect
import math
import re
import sys
import time as _time
import traceback
from typing import Callable, Dict, List, Optional, Union

from . import codec
from .codec import CAPABILITIES, EVENT_KINDS, LED_PATTERNS, STREAM_BITS, band_name
from .transport import Transport, VexoError
from .transports.session_watch import SESSION_ENDED, SessionWatch, session_ended_text
from .stream_watch import STREAM_TAKEN, StreamWatch

TIMEOUT = 2.0  # seconds to wait for the band's answer
# An `async def` handler runs as a task. At most this many may be running at once (the same
# number as the USB transport's MAX_WAITING): beyond it, a new data event is not given to
# `async def` handlers (it is dropped, band.dropped_events counts), so slow handlers cannot
# use up the computer's memory. "disconnect", "reconnecting" and "reconnected" are always
# given. Plain handlers run at once, inside the call that brought the frame: nothing waits.
MAX_WAITING = 5000
KEPT_EVENTS = ("disconnect", "reconnecting", "reconnected", "stream-taken")
DROP_HELP = ("Your async handlers are slower than the band's data: %d are still running. From now on new "
             "events are dropped while that many run. Keep handlers short. band.dropped_events counts "
             "the dropped events.")
# The times a band's clock can hold (contract 5.8): 2020-01-01 up to 2100-01-01, in ms since 1970.
CLOCK_MIN_MS, CLOCK_MAX_MS = 1577836800000, 4102444800000
EXPECT_CLOSE_SECONDS = 3.0  # after restart() or sleep(), a close within this time is the expected one

# Auto-reconnect sends these settings again on the new link, in this order (the light
# before the streams, because the microphone streams lock the light), then the streams.
RESTORE_ORDER = ("IMU_SET", "EVENT_SET", "MIC_SET_GAIN", "RADIO_SET_TX_POWER", "TEMP_SET_INTERVAL", "LED_SET")

# configure() key -> contract stream name
STREAM_KEYS = {"motion": "motion", "events": "event", "activity": "activity", "audio": "audio",
               "battery": "battery", "temperature": "temp", "link": "link", "mic_level": "mic_level"}
EVENT_GROUPS = ["tap", "double-tap", "free-fall", "tilt", "orientation", "motion"]  # index = mask bit
# Every name band.on() knows: the data events, one for each gesture, and the link's own.
EVENT_NAMES = (("motion", "event") + tuple(EVENT_KINDS.values())
               + ("activity", "audio", "battery", "temperature", "link", "mic-level", "notice",
                  "disconnect", "reconnecting", "reconnected", "stream-taken"))


def _stream_names(mask: int) -> list:
    """Stream mask -> the configure() names, e.g. ["motion", "mic_level"]. Both motion streams are "motion"."""
    names = [key for key, stream in STREAM_KEYS.items() if mask >> STREAM_BITS[stream] & 1]
    if mask >> STREAM_BITS["motion2"] & 1 and "motion" not in names:
        names.insert(0, "motion")
    return names


def _stream_taken_text(streams: list, stopped: bool, link: str) -> str:
    """The sentence of the "stream-taken" event. link is this program's own link: "ble", "serial" or another."""
    other = {"ble": "the cable (USB)", "serial": "Bluetooth"}.get(link, "the band's other link")
    one = len(streams) == 1
    what = 'the "%s" stream' % streams[0] if one else "the streams " + ", ".join('"%s"' % name for name in streams)
    it = "this stream" if one else "these streams"
    return ("No data arrives any more for %s, though this program did not stop it and the link is up. "
            "Another program on %s took %s%s: USB and Bluetooth programs on one band share its streams, "
            "its settings and its light, and a stream goes to the program that started it last. "
            "%s get the data back, call band.take_back()."
            % (what, other, it, " and has stopped since" if stopped else "",
               "To" if stopped else "Close the other program first; then, to"))


def check_event(event) -> None:
    """Raise VexoError "bad-event" for a name band.on() does not know (a typo
    would otherwise wait for ever for something that never comes)."""
    if isinstance(event, str) and event in EVENT_NAMES:
        return
    near = event.strip().lower().replace("_", "-") if isinstance(event, str) else ""
    near = {"events": "event", "miclevel": "mic-level", "doubletap": "double-tap"}.get(near, near)
    hint = ' Did you mean "%s"?' % near if near in EVENT_NAMES else ""
    raise VexoError("bad-event", 'There is no event called "%s".%s Events: %s.'
                    % (event, hint, ", ".join(EVENT_NAMES)))
MOTION_RATES = (10, 25, 50, 100)  # what firmware 1.0 can do, always with both sensors
MOTION2_RATES = codec.MOTION2_RATES  # firmware 1.1: these, up to what the link allows
SENSORS = {"accel": 1, "gyro": 2, "both": 3}  # configure(sensors=...) -> the contract's number
SENSOR_NAMES = {code: name for name, code in SENSORS.items()}
CAP_FIELDS = {"accel": "max_accel_hz", "gyro": "max_gyro_hz", "both": "max_both_hz"}  # in MOTION2_GET
USB_CAPS = {"accel": 1666, "gyro": 833, "both": 833}  # what the cable allows (named in the "too fast" help)
MOTION_BITS = 1 << STREAM_BITS["motion"] | 1 << STREAM_BITS["motion2"]  # either one carries "motion"
# What a band with protocol 1.1 adds (band.info.features).
FEATURES = ["fast-motion", "intervals", "clock"]
# Fast motion times that would go backwards (see _fast_motion_times).
FAST_T_RESTART_MS = 1000  # a stamp this far behind: the band's counter started again
FAST_T_LEAST_STEP = 1 / 8  # the least step between two samples, in sample periods ...
FAST_T_LEAST_MS = 0.001  # ... and in milliseconds
# configure() keys that take seconds between readings (firmware 1.1; temperature also on 1.0)
INTERVAL_KEYS = ("activity", "battery", "temperature", "link", "mic_level")
FASTEST = "fastest"  # instead of a number: the highest motion rate, or the shortest interval, this band can do
# Firmware 1.0 sends these streams at one fixed pace (contract 4.3), in seconds; 0 = only when it changes.
V10_PACE = {"activity": 1.0, "battery": 0.0, "link": 1.0, "mic_level": 0.1}
# Auto-reconnect on firmware 1.1 also sets the clock again (first), and sends MOTION2_SET
# and every STREAM_SET_INTERVAL again after RESTORE_ORDER, before the streams.
FACES = ["+x", "-x", "+y", "-y", "+z", "-z"]
ACTIVITY_STATES = ["still", "walk", "run"]
BATTERY_STATES = ["battery", "charging", "charged"]
PHYS = {1: "1M", 2: "2M"}

# What to tell the person at the keyboard for each status the band can answer with.
STATUS_HELP = {
    "UNKNOWN_OPCODE": "The band does not know the command {name}. Its firmware may be older than this library.",
    "BAD_LENGTH": "The band did not understand {name} (wrong number of bytes).",
    "BAD_ARG": "The band does not allow one of the values given to {name}. Nothing was changed.",
    "BUSY": "The band's storage is still busy. Wait a moment and try again.",
    "NOT_AVAILABLE": "That part is not working on this band. band.info.capabilities lists what is.",
    "DENIED": "The band refused {name} for a safety reason.",
    "NOT_FOUND": "Nothing is stored under that key.",
    "NO_SPACE": "The band's storage is full. Delete something first (band.storage.delete or wipe).",
    "IO_ERROR": "The band reported a sensor or storage fault. Try again; if it keeps happening, restart the band.",
}
LINK_STREAM_HELP = ("Nothing in that configure call was started. The \"link\" stream (signal strength) only "
                    "exists over Bluetooth: on the USB cable leave link out and call configure again. "
                    "(Over Bluetooth this answer means a part of the band is not working; "
                    "band.info.capabilities lists what is.)")
WRONG_LENGTH_HELP = ("The band answered %s with %d bytes of data where the contract has %s, so that answer "
                     "was not used, and no answer of the right length came within %g seconds. Try again. If "
                     "it happens every time, this kit and the band's firmware do not match: get the newest kit.")
BAND_ZERO_HELP = ("Band numbers are 1 to 999. 0 is what a band has before it gets a number: "
                  "ask an organiser to number it.")
RECONNECTING_HELP = ("The link to the band dropped and the library is reconnecting. Wait for the "
                     "\"reconnected\" event, then try again.")
MIC_LIGHT_HELP = ("The band refused {name} because the microphone is on. The solid blue light is "
                  "the \"mic is on\" sign and nothing may change it. Turn the microphone off first with "
                  "configure(audio=False, mic_level=False), then set the light.")
# Contract 5.2: 1666 Hz never together with the gyroscope. Only reached with raw commands:
# configure() on firmware 1.1 uses the fast motion stream alone.
NO_GYRO_AT_1666 = ("The band refused {name}: motion at 1666 samples per second never runs together with the "
                   "gyroscope, and the MOTION stream of firmware 1.0 uses the gyroscope. Stop one of the two "
                   "motion streams, use a lower rate, or switch the gyroscope off (IMU_SET with "
                   "gyro_range_dps 0). Nothing was changed.")
DENIED_HELP = {
    "IMU_SET": NO_GYRO_AT_1666,
    "MOTION2_SET": "The band refused {name}: that is more samples per second than this link allows "
                   "while motion is streaming. band.motion_limits() says how fast it can go. Nothing was changed.",
    "LED_SET": MIC_LIGHT_HELP,
    "IDENTIFY": MIC_LIGHT_HELP,
    "SET_BAND_NUMBER": "The band number can only be changed over the USB cable, not over Bluetooth. "
                       "Connect with transport=\"serial\" and try again.",
}


class Message(dict):
    """Data from the band. Use m["ax"] or m.ax, whichever you like."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


def computer_time() -> float:
    """The computer's clock: seconds since 1970. The library reads the time only through
    this function, so a test can replace it (vexo_open.band.computer_time = ...)."""
    return _time.time()


def _clock_set(d: dict) -> bool:
    """True when a TIME_SET / TIME_GET answer holds a time that can be used. A band that
    says "set" with a time outside what the contract allows (a faulty band) is treated
    like one whose clock is not set: times then come from this computer's clock."""
    return bool(d["set"]) and CLOCK_MIN_MS <= d["unix_ms"] < CLOCK_MAX_MS


def _bad(message: str, code: str = "bad-arg") -> VexoError:
    return VexoError(code, message)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _seconds(ms: float) -> str:
    """100 -> "0.1", 3600000 -> "3600": milliseconds written as seconds."""
    return "%g" % (ms / 1000)


def _make_transport(kind, number, options: dict) -> Transport:
    if isinstance(kind, Transport):
        return kind
    try:
        if kind == "ble":
            from .transports.ble import BleTransport
            return BleTransport(number, **options)
        if kind == "serial":
            from .transports.serial import SerialTransport
            return SerialTransport(**options)
        if kind == "sim":
            from .transports.sim import SimTransport
            return SimTransport(42 if number is None else number, **options)
    except TypeError as e:
        raise _bad("That option does not exist for transport \"%s\": %s" % (kind, e))
    raise VexoError("bad-transport", "Unknown transport %r. Use \"ble\", \"serial\", \"sim\" or your own "
                    "Transport object." % (kind,))


def _colour(colour, g, b) -> tuple:
    """"#ff8800" or (255, 136, 0) -> (255, 136, 0)"""
    if isinstance(colour, str):
        m = re.match(r"^#?([0-9a-fA-F]{6})$", colour.strip())
        if not m:
            raise _bad("A colour looks like \"#ff8800\", got \"%s\"." % colour, "bad-colour")
        v = int(m.group(1), 16)
        return v >> 16, (v >> 8) & 255, v & 255
    rgb = (colour, g, b)
    if not all(isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 255 for c in rgb):
        raise _bad("Give a colour like \"#ff8800\", or three whole numbers 0..255: red, green, blue.",
                   "bad-colour")
    return rgb


def _fast_motion_times(last: Optional[float], f: dict) -> List[float]:
    """The time t (band milliseconds, 0 .. 2**32, with a fraction) of every sample in one
    fast motion frame. last is the t of the sample before this frame on the same link, or None.

    The band's own times are used as they are (contract: sample i at t_ms + i x period_us / 1000)
    whenever the frame starts after last. That is every frame after a lost one and after a
    restart of the stream, so a gap in t is the gap the band reported.

    The stamp is in whole milliseconds and the period is an estimate, so now and then a frame
    is stamped at or before the time the frame before it ended on. Used as they are, the times
    would step backwards. Such a frame is spread evenly from last up to the band's own time
    for its last sample: it ends where the band says, and no time is repeated or goes back.
    If even that last time is not after last, the frame follows on in steps of 1/8 period.
    A stamp a second or more behind is the band's counter itself starting again: it is followed."""
    period, count = f["period_us"] / 1000, len(f["samples"])
    if not count:
        return []
    first, step = f["t_ms"], period
    if last is not None:
        ahead = (f["t_ms"] - last) % 2 ** 32  # the counter starts again at 0 after 2**32 ms
        if ahead >= 2 ** 31:
            ahead -= 2 ** 32
        if -FAST_T_RESTART_MS < ahead <= 0:
            step = max((ahead + (count - 1) * period) / count, period * FAST_T_LEAST_STEP, FAST_T_LEAST_MS)
            first = last + step
    return [(first + i * step) % 2 ** 32 for i in range(count)]


def _battery(d: dict) -> Message:
    state = BATTERY_STATES[d["state"]] if d["state"] < len(BATTERY_STATES) else d["state"]
    return Message(percent=d["percent"], millivolts=d["millivolts"], state=state,
                   charging=d["state"] == 1)


class Band:
    """One Vexo Open band.

        band = await Band.connect(42)
        band.on("motion", lambda m: print(m.ax, m.ay, m.az))
        await band.configure(motion=50)
        await band.set_led("#ff0000")

    Events: motion, event (plus tap, double-tap, free-fall, tilt, orientation, motion-start,
    motion-stop), activity, audio, battery, temperature, link, mic-level, notice, disconnect,
    reconnecting, reconnected. Every data event has `time`: when it was measured, in seconds
    since 1970 like time.time(). band.clock says where that comes from: "band" (the band's
    own clock, firmware 1.1) or "computer" (worked out from this computer's clock).

    With Band.connect(42, auto_reconnect=True) a link that drops is not the end: you get
    "reconnecting", the library looks for the band again, sets your light, settings and
    streams again, and you get "reconnected". "disconnect" then only comes when the link
    is closed for good. band.reconnecting is True while the library is looking.
    """

    @classmethod
    async def connect(cls, number=None, transport: Union[str, Transport] = "ble",
                      timeout: float = TIMEOUT, auto_reconnect: bool = False,
                      reconnect_seconds: float = 120, protocol: Optional[str] = None,
                      set_clock: bool = True, **transport_options) -> "Band":
        """Connect to band `number` (42 means "Vexo-HACK-42") and check it is the right one.

        transport: "ble" (Bluetooth), "serial" (USB cable, optional port="COM5"), "sim"
        (pretend band) or your own Transport object. number None accepts whatever band
        answers (USB cable and simulator only). timeout: seconds to wait for each answer.

        auto_reconnect=True: when the link drops, keep trying to get it back for
        reconnect_seconds (120 if left out). You get a "reconnecting" event with reason and
        seconds, then either "reconnected" (attempts, seconds, failed = settings the band
        refused) or "disconnect". Until "reconnected", band calls raise "not-connected".

        set_clock=False: do not set the band's clock to this computer's time (the library
        does that on every connect when the band has firmware 1.1). protocol="1.0": treat
        the band as one with firmware 1.0, whatever it reports: no fast motion, no
        intervals, no clock commands.
        """
        if not isinstance(auto_reconnect, bool):
            raise _bad("auto_reconnect must be True or False, got %r." % (auto_reconnect,))
        if isinstance(reconnect_seconds, bool) or not isinstance(reconnect_seconds, (int, float)) \
                or not reconnect_seconds > 0:
            raise _bad("reconnect_seconds is how many seconds to keep trying, a number above 0, got %r."
                       % (reconnect_seconds,))
        if protocol not in (None, "1.0"):
            raise _bad("protocol can only be \"1.0\" (treat the band as one with firmware 1.0) or left out, "
                       "got %r." % (protocol,))
        if not isinstance(set_clock, bool):
            raise _bad("set_clock must be True or False, got %r." % (set_clock,))
        if number is not None:
            try:
                wanted = int(band_name(number)[len(codec.NAME_PREFIX):])
            except ValueError as e:
                raise _bad(str(e))
        else:
            wanted = None
        # 0 is "number not set". Only the USB cable may reach such a band (so that it can be numbered).
        if wanted == 0 and (transport if isinstance(transport, str) else getattr(transport, "kind", None)) != "serial":
            raise _bad(BAND_ZERO_HELP)
        band = cls(_make_transport(transport, wanted, transport_options), timeout)
        band._protocol = protocol
        band._set_clock = set_clock
        await band.transport.open()
        band.connected = band._linked = True
        try:
            await band.get_info()
            if wanted is None or band.number == wanted:
                await band._sync_clock()
        except BaseException:
            await band.disconnect()
            raise
        if wanted is not None and band.number != wanted:
            await band.disconnect()
            error = VexoError("wrong-band", "This is %s, not %s. Check the number on the band, or connect "
                              "with the number it really has." % (band.name, band_name(wanted)))
            error.found = band.number  # the number this band really has (0 = not set)
            error.wanted = wanted
            raise error
        band.auto_reconnect = auto_reconnect  # only once the first connection stands
        band.reconnect_seconds = reconnect_seconds
        return band

    def __init__(self, transport: Transport, timeout: float = TIMEOUT) -> None:
        self.transport = transport
        self.timeout = timeout
        self.auto_reconnect = False
        self.reconnect_seconds = 120
        self.reconnecting = False  # True while the library is getting a dropped link back
        self.number: Optional[int] = None  # filled in by connect()
        self.name: Optional[str] = None
        self.info: Optional[Message] = None
        self.connected = False
        self.streams = self._streams_from_mask(0)
        self.clock = "computer"  # "band" once event times come from the band's own clock
        self.storage = Storage(self)
        self._handlers: Dict[str, List[Callable]] = {}
        self.dropped_events = 0  # events not given to `async def` handlers because MAX_WAITING of them still ran
        self._busy = 0  # `async def` handlers that have started and not finished
        self._imu: Optional[dict] = None  # last motion settings the band told us
        self._m2: Optional[dict] = None  # last fast-motion settings and limits (firmware 1.1)
        self._active = 0  # the stream mask the band last reported
        self._protocol: Optional[str] = None  # connect(protocol="1.0")
        self._set_clock = True  # connect(set_clock=...)
        self._v11 = False  # the band has protocol 1.1 or later and we may use it
        self._anchor = None  # (real time in ms, the band's t_ms at that same moment)
        self._answer_at = 0.0  # computer time when the last answer arrived
        self._audio_clock = None  # [time of the first audio frame, last seq, frames since]
        self._fast_t: Optional[float] = None  # t of the last fast motion sample on this link
        self._restore_intervals: Dict[int, int] = {}  # stream bit -> ms, to set again after a reconnect (the bit is one byte: 256 entries at most)
        self._seq = 0
        self._waiting = None  # (seq, command name, future) of the command in flight
        self._wrong = None
        self._lock = asyncio.Lock()  # one command at a time
        self._linked = False  # the transport is open (also while settings are being restored)
        self._expect_close = None  # (reason, until when) after restart() or sleep()
        self._critical = False  # the band said its battery is empty: it will not come back
        self._restore: Dict[str, dict] = {}  # what to set again after a reconnect: command -> args
        self._restore_mask = 0  # ...and the streams that were on
        self._reconnect_pause = 1.0  # seconds between two tries
        self._reconnect_task = None
        transport.on_frame = self._receive
        transport.on_close = self._closed
        self._watch = SessionWatch(self)  # USB only: notices a session the band ended (transports/session_watch.py)
        self._stream_watch = StreamWatch(self)  # notices a stream that another program took (stream_watch.py)

    @property
    def transport_kind(self) -> str:
        return self.transport.kind

    async def __aenter__(self) -> "Band":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()

    async def disconnect(self) -> None:
        """Close the link. Safe to call twice. Also stops a reconnect that is going on."""
        task, self._reconnect_task = self._reconnect_task, None
        if self.reconnecting:
            self.reconnecting = False
            if task is not None and task is not _current_task():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)  # let it stop before closing
            self._unlinked()
            self._forget()
            self._emit("disconnect", Message(reason="closed"))
        else:
            self._closed("closed")
        await self.transport.close()

    # ---- events ----

    def on(self, event: str, handler: Optional[Callable] = None):
        """Listen for an event: band.on("tap", fn), or as a decorator: @band.on("tap").
        The handler gets one Message. It may be a normal function or an async one.
        A name that is not an event raises VexoError "bad-event" and lists the events."""
        check_event(event)

        def add(fn: Callable) -> Callable:
            self._handlers.setdefault(event, []).append(fn)
            return fn
        return add if handler is None else add(handler)

    def off(self, event: str, handler: Callable) -> None:
        """Stop calling handler for event. A name that is not an event raises VexoError
        "bad-event", as in on(). (A handler that was never added: nothing happens.)"""
        check_event(event)
        if handler in self._handlers.get(event, []):
            self._handlers[event].remove(handler)

    def _emit(self, event: str, msg: Message) -> None:
        for fn in list(self._handlers.get(event, [])):
            try:
                full = self._busy >= MAX_WAITING and event not in KEPT_EVENTS
                if full and inspect.iscoroutinefunction(fn):
                    self._drop()
                    continue
                result = fn(msg)
                if inspect.isawaitable(result):
                    if full and inspect.iscoroutine(result):
                        result.close()  # a plain function that returned a coroutine: do not start it
                        self._drop()
                        continue
                    self._busy += 1
                    asyncio.ensure_future(result).add_done_callback(
                        lambda task, event=event: self._handler_done(event, task))
            except Exception:
                print('Error in your "%s" handler:' % event, file=sys.stderr)
                traceback.print_exc()

    def _drop(self) -> None:
        self.dropped_events += 1
        if self.dropped_events == 1:  # one line, the first time only
            print(DROP_HELP % MAX_WAITING, file=sys.stderr)

    def _handler_done(self, event: str, task) -> None:
        self._busy -= 1
        _report(event, task)

    # ---- sending commands ----

    async def command(self, name: str, **args) -> Message:
        """Send any command by its contract name and return the band's answer, e.g.
        command("IMU_SET", rate_hz=50, accel_range_g=2, gyro_range_dps=2000).
        Names and fields are in contract/contract.md; nothing is converted."""
        return Message(await self._command(name, **args))

    async def _command(self, name: str, **args) -> dict:
        return await self._exchange(name, args)

    def _check_link(self, restoring: bool) -> None:
        if self.reconnecting and not restoring:
            raise VexoError("not-connected", RECONNECTING_HELP)
        if not self._linked:
            raise VexoError("not-connected", "The band is not connected (any more). Connect again first.")

    async def _exchange(self, name: str, args: dict, restoring: bool = False) -> dict:
        """Send one command and wait for its answer. restoring=True is the library's own
        traffic during a reconnect; everything else is refused until the link is back."""
        self._check_link(restoring)  # at once, without waiting for the command in flight
        async with self._lock:
            self._check_link(restoring)
            seq = self._seq
            self._seq = (seq + 1) % 256
            try:
                frame = codec.encode_command(seq, name, args)
            except ValueError as e:
                raise _bad(str(e))
            future = asyncio.get_event_loop().create_future()
            self._waiting = (seq, name, future)
            self._wrong = None  # an answer to this command that had the wrong length
            try:
                await self.transport.send(frame)
                response = await asyncio.wait_for(future, self.timeout)
            except asyncio.TimeoutError:
                if self._wrong:
                    raise VexoError("timeout", WRONG_LENGTH_HELP % (
                        name, self._wrong["got"], self._wrong["expected"], self.timeout))
                raise VexoError("timeout", "%s did not answer %s within %g seconds. Is it still in range "
                                "and switched on?" % (self.name or "The band", name, self.timeout))
            finally:
                self._waiting = None
        status, data = response["status"], response["data"]
        if status != "OK":
            error = self._status_error(name, status)
            if (name == "STREAM_START" and status == "NOT_AVAILABLE"
                    and args.get("mask", 0) >> STREAM_BITS["link"] & 1):
                error = VexoError("not-available", LINK_STREAM_HELP)
            if name == "STREAM_START" and status == "DENIED" and args.get("mask", 0) & MOTION_BITS == 1:
                error = VexoError("denied", NO_GYRO_AT_1666.format(name=name))  # MOTION beside 1666 Hz
            error.status = status  # the band answered (the link itself is fine)
            raise error
        if name in RESTORE_ORDER or name == "MOTION2_SET":
            self._restore[name] = dict(args)  # accepted: set it again after a reconnect
        if name == "STREAM_SET_INTERVAL":
            self._restore_intervals[data["stream"]] = data["interval_ms"]
            if data["stream"] == STREAM_BITS["temp"]:
                self._restore.pop("TEMP_SET_INTERVAL", None)  # the same setting: the last one counts
        if name == "TEMP_SET_INTERVAL":
            self._restore_intervals.pop(STREAM_BITS["temp"], None)
        if name in ("IMU_SET", "IMU_GET"):
            self._imu = data
        if name in ("MOTION2_SET", "MOTION2_GET"):
            self._m2 = data
        # USB session watch: streams you switched on are off, so the band ended the session. Keep what to set again.
        if name == "STREAM_GET" and self._watch.ended(data["active"]):
            return data
        if name == "STREAM_START":
            self._stream_watch.started(args.get("mask", 0), data["active"])
        if name == "STREAM_STOP":
            self._stream_watch.stopped(args.get("mask", 0))
        if name == "STREAM_GET":
            self._stream_watch.answer(data["active"])
        if "active" in data:
            self._restore_mask = data["active"]
            if (data["active"] & ~self._active) >> STREAM_BITS["audio"] & 1:
                self._audio_clock = None  # audio started: its time counts from the first frame
            self._active = data["active"]
        self._show_streams()
        self._watch.update()  # USB session watch: runs only while streams are on
        self._stream_watch.update()  # stream watch: runs only while streams of this program are on
        if name == "GET_INFO" and self.clock != "band":
            self._anchor = (self._answer_at * 1000, data["uptime_ms"])  # an estimate from our own clock
        if name in ("TIME_SET", "TIME_GET") and self._v11:
            if _clock_set(data):
                self._anchor, self.clock = (data["unix_ms"], data["t_ms"]), "band"
            else:  # the band does not know the time: estimate it from our own clock
                self._anchor, self.clock = (self._answer_at * 1000, data["t_ms"]), "computer"
        return data

    def _show_streams(self) -> None:
        """Rebuild band.streams from what the band last told us."""
        told = self._m2 if self._active >> STREAM_BITS["motion2"] & 1 else self._imu
        self.streams = self._streams_from_mask(self._active, told["rate_hz"] if told else 25)

    @staticmethod
    def _status_error(name: str, status) -> VexoError:
        if not isinstance(status, str):
            return VexoError("unknown-status", "The band answered %s with status %s, which this library "
                             "does not know." % (name, status))
        text = DENIED_HELP.get(name) if status == "DENIED" else None
        return VexoError(status.lower().replace("_", "-"), (text or STATUS_HELP[status]).format(name=name))

    # ---- receiving ----

    def _receive(self, data: bytes) -> None:
        self._watch.heard(data[0] if data else 0)  # USB session watch: a stream frame is a sign of life
        self._stream_watch.heard(data[0] if data else 0)  # stream watch: this stream still arrives
        f = codec.decode_frame(data)
        if f is None:
            # An answer of the wrong length is never used. Over USB the start of an answer can
            # arrive cut off, and the whole answer follows with the same seq: keep waiting.
            wrong, w = codec.wrong_length_response(data), self._waiting
            if wrong and w and w[0] == wrong["seq"] and w[1] == wrong["opcode"]:
                self._wrong = wrong
            return
        kind = f["frame"]
        if kind == "response":
            w = self._waiting
            if (f["opcode"] in ("MOTION2_SET", "MOTION2_GET") and f["status"] == "OK"
                    and f["data"]["sensors"] not in SENSOR_NAMES):
                return  # sensors is not 1, 2 or 3: not a valid answer, ignored like one of the wrong length
            if w and w[0] == f["seq"] and w[1] == f["opcode"] and not w[2].done():
                self._answer_at = computer_time()
                w[2].set_result(f)
            return  # late or unexpected answers are ignored
        if kind == "notice":
            if f["code"] == "CRITICAL_BATTERY":
                self._critical = True  # the band switches its radio off: no point looking for it
            if isinstance(f["code"], str):
                self._emit("notice", Message(kind=f["code"].lower().replace("_", "-"), arg=f["arg"],
                                             time=computer_time()))
        elif kind == "motion":
            for i, s in enumerate(f["samples"]):
                t = f["t_ms"] + i * f["period_ms"]
                self._emit("motion", Message(
                    t=t,
                    ax=s["ax"] / 1000, ay=s["ay"] / 1000, az=s["az"] / 1000,   # milli-g -> g
                    gx=s["gx"] / 10, gy=s["gy"] / 10, gz=s["gz"] / 10,         # tenths -> degrees/second
                    n=None, time=self._real(t)))
        elif kind == "motion2":  # fast motion: the sensors that were not asked for are None
            times = _fast_motion_times(self._fast_t, f)
            for i, s in enumerate(f["samples"]):
                # ("t" is listed first so that the fields print in the same order as the old stream's)
                g = {k: s[k] / (1000 if k[0] == "a" else 10) if k in s else None
                     for k in ("t", "ax", "ay", "az", "gx", "gy", "gz")}
                self._emit("motion", Message(g, t=times[i], n=(f["sample_no"] + i) & 0xFFFFFFFF,
                                             time=self._real(times[i])))
            if times:
                self._fast_t = times[-1]
        elif kind == "event":
            name = EVENT_KINDS.get(f["event"])
            if name is None:
                return  # a kind of event this library does not know yet
            msg = Message(t=f["t_ms"], kind=name, arg=f["arg"], time=self._real(f["t_ms"]))
            if name == "orientation":
                msg["face"] = FACES[f["arg"]] if f["arg"] < len(FACES) else None
            self._emit("event", msg)
            self._emit(name, msg)
        elif kind == "activity":
            state = ACTIVITY_STATES[f["state"]] if f["state"] < len(ACTIVITY_STATES) else f["state"]
            self._emit("activity", Message(t=f["t_ms"], steps=f["steps_total"], cadence=f["cadence_spm"],
                                           state=state, motion=f["motion_mg"] / 1000,
                                           time=self._real(f["t_ms"])))
        elif kind == "audio":
            self._emit("audio", Message(seq=f["seq"], opus=f["opus"], time=self._audio_time(f["seq"])))
        elif kind == "battery":
            self._emit("battery", Message(_battery(f), time=computer_time()))
        elif kind == "temp":
            self._emit("temperature", Message(t=f["t_ms"], celsius=f["temp_mc"] / 1000,
                                              time=self._real(f["t_ms"])))
        elif kind == "link":
            self._emit("link", Message(t=f["t_ms"], rssi=None if f["rssi_dbm"] == 127 else f["rssi_dbm"],
                                       tx_power=f["tx_dbm"], phy=PHYS.get(f["phy"]), time=self._real(f["t_ms"])))
        elif kind == "mic_level":
            level = min(1.0, max(0.0, 1 - f["rms_db"] / 96))
            self._emit("mic-level", Message(t=f["t_ms"], peak_db=f["peak_db"], rms_db=f["rms_db"], level=level,
                                            time=self._real(f["t_ms"])))

    def _real(self, t_ms: int, extra_ms: float = 0.0) -> Optional[float]:
        """Real time (seconds since 1970) of the band time t_ms (+ extra_ms).

        The anchor is one moment at which we know both clocks. The band's counter wraps
        at 2**32 ms (49.7 days), so the distance to the anchor is taken modulo 2**32 and
        read as a signed number: frames from before and after the anchor both come out right."""
        if self._anchor is None:
            return None
        d = (t_ms - self._anchor[1]) % 2 ** 32
        if d >= 2 ** 31:
            d -= 2 ** 32
        return (self._anchor[0] + d + extra_ms) / 1000

    def _audio_time(self, seq: int) -> float:
        """Audio frames carry no time: the first one after a start gets its arrival time,
        each later one 20 ms more per step of seq (which wraps at 65536)."""
        c = self._audio_clock
        if c is None:
            c = self._audio_clock = [computer_time(), seq, 0]
        else:
            c[2] += (seq - c[1]) % 65536
            c[1] = seq
        return c[0] + c[2] * 0.02

    def _unlinked(self) -> None:
        """The link is gone: nothing is on any more and the command in flight fails."""
        self.connected = self._linked = False
        self._watch.stop()
        self._stream_watch.reset()
        self._active = 0
        self.streams = self._streams_from_mask(0)
        self._imu = self._m2 = None  # the band forgets its settings when the link closes
        self._fast_t = None
        self._audio_clock = None
        self.clock = "computer"  # until the next link has asked the band again
        if self._waiting and not self._waiting[2].done():
            self._waiting[2].set_exception(VexoError(
                "not-connected", "The band disconnected before it answered %s." % self._waiting[1]))

    def _forget(self) -> None:
        """Forget the settings a reconnect would set again."""
        self._restore = {}
        self._restore_intervals = {}
        self._restore_mask = 0

    def _closed(self, reason: str) -> None:
        if not self._linked:
            return
        self._unlinked()
        if self.reconnecting:
            return  # dropped again while reconnecting: the reconnect loop simply tries again
        expected, self._expect_close = self._expect_close, None
        if reason != "closed" and expected and asyncio.get_event_loop().time() <= expected[1]:
            reason = expected[0]  # the close that restart() or sleep() announced
        retry = reason in ("lost", SESSION_ENDED) and self.auto_reconnect and not self._critical
        # The band ended the USB session: the event also carries the sentence for the person.
        detail = dict(reason=reason)
        if reason == SESSION_ENDED:
            detail["message"] = session_ended_text(retry)
        if retry:
            self.reconnecting = True
            self._reconnect_task = asyncio.ensure_future(self._reconnect(asyncio.get_event_loop().time()))
            self._emit("reconnecting", Message(detail, seconds=self.reconnect_seconds))
            return
        self._forget()
        self._emit("disconnect", Message(detail))

    # ---- auto-reconnect ----

    async def _reconnect(self, dropped_at: float) -> None:
        """Try to get the link back until reconnect_seconds after the drop."""
        loop = asyncio.get_event_loop()
        deadline = dropped_at + self.reconnect_seconds
        wanted = self.number
        attempts = 0
        me = _current_task()

        def stopped() -> bool:
            # disconnect() takes _reconnect_task away before it cancels this task. The cancel
            # alone is not enough: when it arrives just as a try has ended, asyncio.wait_for
            # (Python 3.9 to 3.11) hands back the result of the try and the cancel is lost.
            return self._reconnect_task is not me
        while True:
            left = deadline - loop.time()
            if left <= 0:
                break
            attempts += 1
            try:
                result = await asyncio.wait_for(self._relink(wanted), left)  # one try, at most the time left
            except Exception:  # not there yet, or it dropped again
                if stopped():
                    return  # disconnect() closes the transport and tells the program
                await self._drop_transport()
                await asyncio.sleep(max(0, min(self._reconnect_pause, deadline - loop.time())))
                continue
            if stopped():
                return
            if isinstance(result, int):  # another band answered: stop for good
                await self._drop_transport()
                self._give_up(Message(reason="wrong-band", found=result, wanted=wanted))
                return
            self.connected = True
            self.reconnecting = False
            self._reconnect_task = None
            self._emit("reconnected", Message(attempts=attempts, seconds=round(loop.time() - dropped_at, 3),
                                              failed=result))
            return
        await self._drop_transport()
        self._give_up(Message(reason="lost", reconnect="gave-up"))

    async def _relink(self, wanted):
        """Open the link, check it is the same band and set everything again. Returns the
        list of settings the band refused, or the band's number if it is the wrong band."""
        await (getattr(self.transport, "reopen", None) or self.transport.open)()
        self._linked = True
        self._critical = False
        info = await self._exchange("GET_INFO", {}, restoring=True)
        if wanted is not None and info["band_no"] != wanted:
            return info["band_no"]
        self._set_info(info)
        failed = []
        if not await self._sync_clock(restoring=True):
            failed.append("TIME_SET" if self._set_clock else "TIME_GET")
        todo = [(name, self._restore[name]) for name in RESTORE_ORDER if name in self._restore]
        fast = [("MOTION2_SET", self._restore["MOTION2_SET"])] if "MOTION2_SET" in self._restore else []
        fast += [("STREAM_SET_INTERVAL", dict(stream=bit, interval_ms=ms))
                 for bit, ms in sorted(self._restore_intervals.items())]
        mask = self._restore_mask
        if self._v11:
            todo += fast
        else:  # it came back with firmware 1.0: what only 1.1 can do is reported, not sent
            failed += [name for name, _ in fast]
            if mask >> STREAM_BITS["motion2"] & 1:
                failed.append("STREAM_START")
            mask &= 0xFF
        if mask:
            todo.append(("STREAM_START", dict(mask=mask)))
        for name, args in todo:
            try:
                await self._exchange(name, args, restoring=True)
            except VexoError as e:
                if getattr(e, "status", None) is None:
                    raise  # no answer or the link dropped again: back to the retry loop
                failed.append(name)  # the band said no: skip it and report it
        return failed

    async def _drop_transport(self) -> None:
        self._unlinked()
        try:
            await self.transport.close()
        except Exception:
            pass  # it may already be gone

    def _give_up(self, event: Message) -> None:
        self.reconnecting = False
        self._reconnect_task = None
        self._forget()
        self._emit("disconnect", event)

    # ---- identity ----

    async def get_info(self) -> Message:
        """Ask the band who it is. Also refreshes band.info, band.number and band.name."""
        return self._set_info(await self._command("GET_INFO"))

    def _set_info(self, d: dict) -> Message:
        # Protocol 1.1 only adds to 1.0, and later 1.x versions only add to 1.1.
        self._v11 = d["proto_major"] == 1 and d["proto_minor"] >= 1 and self._protocol != "1.0"
        self.info = Message(
            number=d["band_no"],
            protocol="%d.%d" % (d["proto_major"], d["proto_minor"]),
            firmware="%d.%d.%d" % (d["fw_major"], d["fw_minor"], d["fw_patch"]),
            hardware=d["hw"],
            capabilities=[name for bit, name in enumerate(CAPABILITIES) if d["caps"] >> bit & 1],
            features=list(FEATURES) if self._v11 else [],
            max_frame=d["max_frame"], uptime_ms=d["uptime_ms"])
        self.number = d["band_no"]
        self.name = band_name(self.number) if self.number <= 999 else None
        return self.info

    async def get_name(self) -> str:
        """The name the band reports, e.g. "Vexo-HACK-42"."""
        return (await self._command("GET_NAME"))["name"]

    async def set_band_number(self, number: int) -> int:
        """Give the band a new number, 1..999. Only works over the USB cable."""
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 999:
            raise _bad("A band number is a whole number from 1 to 999, got %r." % (number,))
        self.number = (await self._command("SET_BAND_NUMBER", number=number))["number"]
        self.name = band_name(self.number)
        if self.info is not None:
            self.info["number"] = self.number
        return self.number

    async def take_back(self) -> dict:
        """Start again the streams that another program took (see the "stream-taken" event).
        A band's streams are shared by everything connected to it, on the USB cable and on
        Bluetooth, and a stream goes to the program that started it last: after this call
        that is this program, and the other program gets no more of it. Close the other
        program first, or the two keep taking the stream from each other. Settings are
        shared too: if the other program changed a rate or an interval, set it again with
        configure(). Returns band.streams. With nothing taken, nothing is sent."""
        mask = self._stream_watch.taken
        if mask:
            await self._command("STREAM_START", mask=mask)
        return self.streams

    @property
    def taken_streams(self) -> list:
        """The names of the streams that were taken and not started again since, e.g. ["motion"]."""
        return _stream_names(self._stream_watch.taken)

    def _streams_taken(self, lost: int, off: int) -> None:
        """The stream watch found streams of this program that go elsewhere now (off: the band stopped them)."""
        if not self._linked or self.reconnecting:
            return
        streams = _stream_names(lost)
        kind = self.transport.kind
        if kind == "sim":  # the pretend band says which link it imitates
            kind = "serial" if getattr(self.transport, "via", "ble") == "usb" else "ble"
        message = _stream_taken_text(streams, bool(off), kind)
        if not self._handlers.get(STREAM_TAKEN):
            print(message, file=sys.stderr)  # nobody listens: say it once, here
        self._emit(STREAM_TAKEN, Message(streams=streams, stopped=_stream_names(off), message=message))

    async def restart(self) -> None:
        """Restart the band. It disconnects a moment later (reason "restart"); the library
        does not reconnect by itself after this."""
        await self._command("RESTART")
        self._expect("restart")

    async def sleep(self) -> None:
        """Put the band to sleep: its streams and light go off. Over Bluetooth it also
        disconnects a moment later (reason "sleep"); wake it with a double-tap. On the USB
        cable the link stays open. The library does not reconnect by itself after this."""
        await self._command("SLEEP")
        self._expect("sleep")
        self._active = 0  # the band stops them on both links
        self._stream_watch.reset()
        self._show_streams()

    def _expect(self, reason: str) -> None:
        """The link drop that follows within a few seconds is expected: report it as
        `reason`, and do not set anything again afterwards."""
        self._expect_close = (reason, asyncio.get_event_loop().time() + EXPECT_CLOSE_SECONDS)
        self._forget()

    # ---- streams ----

    @staticmethod
    def _streams_from_mask(mask: int, rate: int = 25) -> dict:
        streams = {key: bool(mask >> STREAM_BITS[stream] & 1) for key, stream in STREAM_KEYS.items()}
        streams["motion"] = rate if mask & MOTION_BITS else 0  # the old or the fast motion stream
        return streams

    async def configure(self, **streams) -> dict:
        """Turn data on or off. Everything is off until you ask; streams you leave out keep
        their setting. Returns band.streams.

        motion: samples per second (True = keep the rate, 0/False = off). Firmware 1.0:
        10, 25, 50 or 100. Firmware 1.1: also 208, 416, 833 and 1666, as far as the link
        allows (band.motion_limits() tells), or "fastest".
        sensors: "accel", "gyro" or "both": what a motion sample holds (firmware 1.1;
        left out = unchanged, "both" at first). Fewer sensors allow a higher rate.
        events, audio: True/False.
        activity, battery, temperature, link, mic_level: True/False, or the seconds between
        readings, e.g. battery=60, mic_level=0.02 (firmware 1.1; on 1.0 only temperature
        takes a number, whole seconds 1..255). Or "fastest": the shortest interval this
        band can do. On firmware 1.0 that is the band's one fixed pace, so "fastest" then
        simply turns the stream on (temperature: every second).
        link (signal strength) only exists over Bluetooth: on the USB cable, asking for it
        raises "not-available" and nothing in that call is started.
        """
        streams = dict(streams)
        sensors = streams.pop("sensors", None)  # None = left out
        for key, value in streams.items():
            if key not in STREAM_KEYS:
                raise VexoError("bad-stream", 'There is no stream called "%s". Streams: %s.'
                                % (key, ", ".join(STREAM_KEYS)))
            if key == "motion" or isinstance(value, bool):
                continue
            if key not in INTERVAL_KEYS:
                raise VexoError("bad-stream", 'Stream "%s" must be True or False, got %r.' % (key, value))
            if isinstance(value, str) and value == FASTEST:
                continue
            if key == "temperature" and not self._v11:
                continue  # firmware 1.0 has its own rule for this one, checked below
            if not _is_number(value) or value < 0:
                raise VexoError("bad-arg" if key == "temperature" else "bad-stream",
                                'Stream "%s" must be True, False, the seconds between readings or '
                                '"fastest", got %r.' % (key, value))
        if sensors is not None and sensors not in SENSORS:
            raise _bad('sensors must be "accel", "gyro" or "both", got %r.' % (sensors,))
        motion = streams.get("motion")
        fastest = isinstance(motion, str) and motion == FASTEST
        new_rate = None if isinstance(motion, bool) or not motion or fastest else motion
        timed = {key: value for key, value in streams.items()
                 if key in INTERVAL_KEYS and not isinstance(value, bool) and value}
        if self._v11:
            return await self._configure_v11(streams, sensors, new_rate, fastest, timed)

        # ---- firmware 1.0: the motion stream of 1.0, and only temperature takes seconds ----
        if new_rate in MOTION2_RATES[len(MOTION_RATES):] or sensors not in (None, "both"):
            raise self._needs_v11(new_rate, sensors)
        if new_rate is not None and new_rate not in MOTION_RATES:
            raise _bad("motion must be 10, 25, 50 or 100 samples per second (or True / False), got %r."
                       % (motion,))
        if fastest:
            new_rate = MOTION_RATES[-1]
        seconds = timed.pop("temperature", None)
        if isinstance(seconds, str):
            seconds = 1  # "fastest": the shortest interval firmware 1.0 takes
        # "fastest" for the others: firmware 1.0 has one pace, so the stream is simply turned on
        timed = {key: value for key, value in timed.items() if not isinstance(value, str)}
        if seconds is not None and not (isinstance(seconds, int) and 1 <= seconds <= 255):
            raise _bad("temperature must be True, False or 1..255 seconds between readings, got %r."
                       % (streams["temperature"],))
        if timed:
            raise VexoError("not-available", "Seconds between readings for \"%s\" needs band firmware 1.1; "
                            "this band has %s. Use True for the standard rate."
                            % (sorted(timed)[0], self._firmware()))

        if motion and self._imu is None:
            await self._command("IMU_GET")  # learn the current rate and ranges
        if new_rate is not None and new_rate != self._imu["rate_hz"]:
            await self._command("IMU_SET", **dict(self._imu, rate_hz=new_rate))
        if seconds is not None:
            await self._command("TEMP_SET_INTERVAL", seconds=seconds)

        start = stop = 0
        for key, value in streams.items():
            bit = 1 << STREAM_BITS[STREAM_KEYS[key]]
            if value and (not self.streams[key] or self._stream_watch.taken & bit):
                start |= bit
            if not value and self.streams[key]:
                stop |= bit
        if start:
            await self._command("STREAM_START", mask=start)
        if stop:
            await self._command("STREAM_STOP", mask=stop)
        return self.streams

    async def _configure_v11(self, streams: dict, sensors, new_rate, fastest: bool, timed: dict) -> dict:
        """configure() for a band with firmware 1.1: motion goes through the fast motion
        stream (MOTION2), and the timed streams take seconds between readings."""
        if new_rate is not None and new_rate not in MOTION2_RATES:
            raise self._bad_rate(streams["motion"])
        motion = streams.get("motion")
        fast_bit = 1 << STREAM_BITS["motion2"]
        rate = None
        if motion or sensors is not None:
            rate, sensors = await self._fast_motion(new_rate, sensors, fastest)  # checked: nothing sent yet
            if (rate, SENSORS[sensors]) != (self._m2["rate_hz"], self._m2["sensors"]):
                await self._motion2("MOTION2_SET", (rate, sensors), rate_hz=rate, sensors=SENSORS[sensors])
        for key, seconds in timed.items():
            await self._send_interval(key, seconds)

        start = stop = 0
        for key, value in streams.items():
            bit = 1 << STREAM_BITS[STREAM_KEYS[key]]
            if key == "motion":  # on if either motion stream runs; started as the fast one
                again = self._stream_watch.taken & fast_bit  # on for the band, but going to another program
                start |= fast_bit if value and (not self._active & fast_bit or again) else 0
                stop |= 0 if value else self._active & MOTION_BITS
            elif value and (not self.streams[key] or self._stream_watch.taken & bit):
                start |= bit
            elif not value and self.streams[key]:
                stop |= bit
        if start & fast_bit:
            await self._motion2("STREAM_START", (rate, sensors), mask=start)
        elif start:
            await self._command("STREAM_START", mask=start)
        if stop:
            await self._command("STREAM_STOP", mask=stop)
        return self.streams

    # ---- fast motion (firmware 1.1) ----

    def _firmware(self) -> str:
        return self.info.firmware if self.info else "?"

    def _needs_v11(self, rate, sensors) -> VexoError:
        """The "not-available" for motion that only firmware 1.1 can do."""
        what = "one sensor alone" if sensors not in (None, "both") else ""
        if rate in MOTION2_RATES[len(MOTION_RATES):]:
            what = "%d per second%s" % (rate, ", or %s," % what if what else "")
        if self._protocol == "1.0" and self.info and self.info.protocol != "1.0":
            first = "You connected with protocol=\"1.0\", which"
        else:
            first = "This band's firmware (%s)" % self._firmware()
        return VexoError("not-available", "%s can do 10, 25, 50 or 100 samples per second with both sensors. "
                         "%s needs firmware 1.1." % (first, what[0].upper() + what[1:]))

    @staticmethod
    def _bad_rate(got) -> VexoError:
        return _bad("motion must be 10, 25, 50, 100, 208, 416, 833 or 1666 samples per second "
                    "(or True / False / \"fastest\"), got %r." % (got,))

    def _link_words(self) -> str:
        via = getattr(self.transport, "via", None)  # the pretend band says which link it plays
        if self.transport.kind == "serial" or via == "usb":
            return "the USB cable"
        if self.transport.kind == "ble" or via == "ble":
            return "Bluetooth here"
        return "this link"

    def _too_fast(self, rate: int, sensors: str) -> VexoError:
        """The "denied" for more samples per second than this link allows, with the way out."""
        cap = {name: self._m2[field] for name, field in CAP_FIELDS.items()} if self._m2 else None
        link = self._link_words()
        what = "both sensors" if sensors == "both" else "%s alone" % sensors
        if cap is None:
            text = "%d samples per second with %s is too much for %s. Use a lower rate." % (rate, what, link)
        else:
            ways = ['sensors="%s"' % name for name in ("accel", "gyro") if name != sensors and rate <= cap[name]]
            if link.startswith("Bluetooth") and rate <= USB_CAPS[sensors]:
                ways.append("the USB cable (up to %d with both, %d with accel alone)"
                            % (USB_CAPS["both"], USB_CAPS["accel"]))
            ways.append("a lower rate")
            text = ("%d samples per second with %s is too much for %s: the most is %d with both, %d with "
                    "accel alone, %d with gyro alone. Use %s. Nothing was changed."
                    % (rate, what, link, cap["both"], cap["accel"], cap["gyro"], ", or ".join(ways)))
        error = VexoError("denied", text)
        error.rate, error.sensors = rate, sensors
        return error

    async def _fast_motion(self, rate, sensors, fastest: bool = False) -> tuple:
        """Work out the (rate, sensors) a call asks for and check them against what this
        link allows. Asks the band for its limits the first time on a link. Sends no change."""
        if self._m2 is None:
            await self._command("MOTION2_GET")
        sensors = sensors or SENSOR_NAMES[self._m2["sensors"]]
        cap = self._m2[CAP_FIELDS[sensors]]
        rate = cap if fastest else self._m2["rate_hz"] if rate is None else rate
        if rate > cap:
            raise self._too_fast(rate, sensors)
        return rate, sensors

    async def _motion2(self, name: str, asked: tuple, **args) -> dict:
        """Send MOTION2_SET, or a STREAM_START with fast motion in it. If the band says the
        rate is too much for the link, say so in full."""
        try:
            return await self._command(name, **args)
        except VexoError as e:
            if getattr(e, "status", None) != "DENIED":
                raise
            rate, sensors = asked
            if rate == MOTION2_RATES[-1] and self._m2 and rate <= self._m2[CAP_FIELDS[sensors]]:
                # within what the link carries and still refused: the gyroscope is running for MOTION
                error = VexoError("denied", NO_GYRO_AT_1666.format(name=name))
            else:
                error = self._too_fast(rate, sensors)
            error.status = "DENIED"
            raise error

    async def motion_limits(self) -> Message:
        """How fast motion can go on this band and this link: rates (every rate the band
        knows), and the fastest one for accel alone, gyro alone and both (None = not
        possible), plus transport. Firmware 1.0: 10 to 100, both sensors only."""
        if not self._v11:
            return Message(rates=list(MOTION_RATES), accel=None, gyro=None, both=MOTION_RATES[-1],
                           transport=self.transport_kind)
        if self._m2 is None:
            await self._command("MOTION2_GET")
        return Message(rates=list(MOTION2_RATES), accel=self._m2["max_accel_hz"], gyro=self._m2["max_gyro_hz"],
                       both=self._m2["max_both_hz"], transport=self.transport_kind)

    # ---- seconds between readings (firmware 1.1) ----

    @staticmethod
    def _interval_key(stream) -> str:
        if stream not in STREAM_KEYS:
            raise VexoError("bad-stream", 'There is no stream called "%s". Streams with an interval: %s.'
                            % (stream, ", ".join(INTERVAL_KEYS)))
        if stream not in INTERVAL_KEYS:
            how = {"motion": " Set its rate with configure(motion=50).",
                   "events": " Events come when they happen.",
                   "audio": " Audio is always 50 packets a second."}[stream]
            raise _bad('Stream "%s" has no interval.%s Streams with an interval: %s.'
                       % (stream, how, ", ".join(INTERVAL_KEYS)))
        return stream

    def _interval_message(self, key: str, d: dict) -> Message:
        return Message(stream=key, seconds=d["interval_ms"] / 1000, min=d["min_ms"] / 1000,
                       max=d["max_ms"] / 1000)

    async def _send_interval(self, key: str, seconds: float, zero_too: bool = False) -> dict:
        """Send STREAM_SET_INTERVAL. If the band does not allow the value, ask it for the
        range and say that."""
        bit = STREAM_BITS[STREAM_KEYS[key]]
        if isinstance(seconds, str):  # "fastest": the shortest interval the band says it allows
            ms = (await self._command("STREAM_GET_INTERVAL", stream=bit))["min_ms"]
            return await self._command("STREAM_SET_INTERVAL", stream=bit, interval_ms=ms)
        ms = min(0xFFFFFFFF, round(seconds * 1000))
        if seconds > 0:
            ms = max(1, ms)
        try:
            return await self._command("STREAM_SET_INTERVAL", stream=bit, interval_ms=ms)
        except VexoError as e:
            if getattr(e, "status", None) != "BAD_ARG":
                raise
        d = await self._command("STREAM_GET_INTERVAL", stream=bit)
        text = '"%s" can come every %s to %s seconds' % (key, _seconds(d["min_ms"]), _seconds(d["max_ms"]))
        if key == "mic_level":
            text += ", in steps of 0.02"
        if key == "battery" and zero_too:
            text += " (or 0 = only when it changes)"
        error = _bad("%s, got %r. Nothing was changed." % (text, seconds))
        error.status = "BAD_ARG"
        raise error

    async def set_interval(self, stream: str, seconds: float) -> Message:
        """Seconds between two readings of a stream: activity, battery, temperature, link
        or mic_level, e.g. set_interval("battery", 60). Fractions are fine (0.1). For
        battery, 0 = only when it changes (the default). Does not turn the stream on or
        off. Returns stream, seconds, min, max. Needs firmware 1.1 (on 1.0 only
        temperature, in whole seconds 1..255).

        set_interval("mic_level", "fastest") asks for the shortest interval this band can
        do, on any firmware, and the answer says what you got. A band with firmware 1.0
        has one fixed pace for every stream but temperature: nothing is sent, and the
        answer holds that pace (min and max are the same number: it cannot be changed)."""
        key = self._interval_key(stream)
        fastest = isinstance(seconds, str) and seconds == FASTEST
        if not self._v11:
            if fastest and key != "temperature":
                self._check_link(False)
                return Message(stream=key, seconds=V10_PACE[key], min=V10_PACE[key], max=V10_PACE[key])
            if key != "temperature":
                raise self._interval_needs_v11(key)
            if fastest:
                seconds = 1
            if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 255:
                raise _bad("With firmware %s the temperature interval is a whole number of seconds, "
                           "1..255, got %r." % (self._firmware(), seconds))
            d = await self._command("TEMP_SET_INTERVAL", seconds=seconds)
            return Message(stream=key, seconds=float(d["seconds"]), min=1.0, max=255.0)
        if not fastest and (not _is_number(seconds) or seconds < 0):
            raise _bad("set_interval takes the seconds between readings, a number such as 0.5 or 60, "
                       "or \"fastest\", got %r." % (seconds,))
        return self._interval_message(key, await self._send_interval(key, seconds, zero_too=True))

    async def get_interval(self, stream: str) -> Message:
        """The seconds between two readings of a stream, and the range the band allows:
        stream, seconds, min, max. Needs firmware 1.1."""
        key = self._interval_key(stream)
        if not self._v11:
            raise self._interval_needs_v11(key)
        return self._interval_message(
            key, await self._command("STREAM_GET_INTERVAL", stream=STREAM_BITS[STREAM_KEYS[key]]))

    def _interval_needs_v11(self, key: str) -> VexoError:
        return VexoError("not-available", "The interval of \"%s\" needs band firmware 1.1; this band has %s%s."
                         % (key, self._firmware(), " (and you connected with protocol=\"1.0\")"
                            if self._protocol == "1.0" else ""))

    # ---- clock ----

    async def _sync_clock(self, restoring: bool = False) -> bool:
        """Firmware 1.1, on every new link: set the band's clock to this computer's time
        (or, with set_clock=False, only ask for it). The answer pairs real time with the
        band's own counter; _real() uses that pair. False = the band refused."""
        if not self._v11:
            return True  # firmware 1.0: the GET_INFO answer gave us an estimate
        try:
            if self._set_clock:
                await self._exchange("TIME_SET", dict(unix_ms=round(computer_time() * 1000)), restoring)
            else:
                await self._exchange("TIME_GET", {}, restoring)
        except VexoError as e:
            if getattr(e, "status", None) is None:
                raise  # no answer at all: that is a link problem
            return False  # e.g. this computer's clock is far off: keep the estimate
        return True

    def _time_message(self, d: dict) -> Message:
        if _clock_set(d):
            return Message(time=d["unix_ms"] / 1000, set=True, t=d["t_ms"], source="band")
        return Message(time=self._answer_at, set=False, t=d["t_ms"], source="computer")

    async def time(self) -> Message:
        """What time the band thinks it is: time (seconds since 1970, like time.time()),
        set (True if the band's clock was set), t (the band's own milliseconds counter at
        that moment) and source ("band", or "computer" when the band's clock is not set
        or it has firmware 1.0: then time is this computer's clock)."""
        if not self._v11:
            info = await self._command("GET_INFO")
            self._set_info(info)
            return self._time_message(dict(set=0, t_ms=info["uptime_ms"]))
        return self._time_message(await self._command("TIME_GET"))

    async def set_time(self, when: Optional[float] = None) -> Message:
        """Set the band's clock: to this computer's time, or to `when` (seconds since 1970).
        Returns the same as time(). The band keeps the clock until it restarts or its
        battery is empty. Needs firmware 1.1."""
        if not self._v11:
            raise VexoError("not-available", "Setting the band's clock needs band firmware 1.1; this band "
                            "has %s. Event times come from this computer's clock instead." % self._firmware())
        help_ = ("set_time takes seconds since 1970, like time.time(), for a date from 2020 to 2099, "
                 "got %r. Nothing was changed.")
        if when is not None and (not _is_number(when) or not 0 <= when < 2 ** 40):
            raise _bad(help_ % (when,))
        seconds = computer_time() if when is None else when
        try:
            return self._time_message(await self._command("TIME_SET", unix_ms=round(seconds * 1000)))
        except VexoError as e:
            if getattr(e, "status", None) != "BAD_ARG":
                raise
            error = _bad(help_ % (seconds,))
            error.status = "BAD_ARG"
            raise error

    # ---- LED ----

    async def set_led(self, colour, g=None, b=None, brightness: float = 1.0, pattern: str = "solid",
                      period: int = 1000) -> None:
        """set_led("#ff8800") or set_led(255, 136, 0). Options: brightness 0..1,
        pattern "solid", "blink", "breathe", "flash", "rainbow" or "off",
        period = milliseconds per cycle, 100..10000."""
        r, g, b = _colour(colour, g, b)
        if isinstance(brightness, bool) or not isinstance(brightness, (int, float)) or not 0 <= brightness <= 1:
            raise _bad("brightness goes from 0 (off) to 1 (full), got %r." % (brightness,))
        if pattern not in LED_PATTERNS:
            raise _bad("pattern must be one of: %s. Got %r." % (", ".join(LED_PATTERNS), pattern))
        if isinstance(period, bool) or not isinstance(period, int) or not 100 <= period <= 10000:
            raise _bad("period is the milliseconds per cycle, 100..10000, got %r." % (period,))
        await self._command("LED_SET", r=r, g=g, b=b, brightness=round(brightness * 255),
                            pattern=LED_PATTERNS.index(pattern), period_ms=period)

    async def led_off(self) -> None:
        """Turn the LED off."""
        await self._command("LED_SET", r=0, g=0, b=0, brightness=0, pattern=0, period_ms=1000)

    async def get_led(self) -> Message:
        """The LED setting you chose: r, g, b, brightness (0..1), pattern, period (ms)."""
        d = await self._command("LED_GET")
        pattern = LED_PATTERNS[d["pattern"]] if d["pattern"] < len(LED_PATTERNS) else d["pattern"]
        return Message(r=d["r"], g=d["g"], b=d["b"], brightness=d["brightness"] / 255,
                       pattern=pattern, period=d["period_ms"])

    async def identify(self, seconds: int = 5) -> None:
        """Blink the band white for some seconds (1..30) so you can see which one it is."""
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 30:
            raise _bad("identify takes whole seconds from 1 to 30, got %r." % (seconds,))
        await self._command("IDENTIFY", seconds=seconds)

    # ---- motion sensor ----

    async def set_motion(self, rate: Optional[int] = None, accel_range: Optional[int] = None,
                         gyro_range: Optional[int] = None, sensors: Optional[str] = None) -> Message:
        """Motion sensor settings; what you leave out keeps its value. rate: samples per
        second (10/25/50/100; firmware 1.1 also 208/416/833/1666 or "fastest", see
        motion_limits()), sensors "accel"/"gyro"/"both" (firmware 1.1), accel_range
        2/4/8/16 g, gyro_range 0 (off)/250/500/1000/2000 degrees/second.
        Returns the settings the band applied."""
        if sensors is not None and sensors not in SENSORS:
            raise _bad('sensors must be "accel", "gyro" or "both", got %r.' % (sensors,))
        fastest = isinstance(rate, str) and rate == "fastest"
        if self._v11:
            if rate is not None and not fastest and (isinstance(rate, bool) or rate not in MOTION2_RATES):
                raise self._bad_rate(rate)
            want, kind = await self._fast_motion(None if fastest else rate, sensors, fastest)
            if (want, SENSORS[kind]) != (self._m2["rate_hz"], self._m2["sensors"]):
                await self._motion2("MOTION2_SET", (want, kind), rate_hz=want, sensors=SENSORS[kind])
            if self._imu is None:
                await self._command("IMU_GET")
            if accel_range is not None or gyro_range is not None:  # the ranges are shared by both streams
                ranges = dict(self._imu)
                for field, value in (("accel_range_g", accel_range), ("gyro_range_dps", gyro_range)):
                    if value is not None:
                        ranges[field] = value
                await self._command("IMU_SET", **ranges)
            return self._motion_message()
        if rate in MOTION2_RATES[len(MOTION_RATES):] or sensors not in (None, "both"):
            raise self._needs_v11(rate, sensors)
        if fastest:
            rate = MOTION_RATES[-1]
        if self._imu is None:
            await self._command("IMU_GET")
        want = dict(self._imu)
        for field, value in (("rate_hz", rate), ("accel_range_g", accel_range), ("gyro_range_dps", gyro_range)):
            if value is not None:
                want[field] = value
        await self._command("IMU_SET", **want)
        return self._motion_message()

    async def get_motion(self) -> Message:
        """rate (per second), sensors ("accel", "gyro" or "both"), accel_range (g),
        gyro_range (degrees/second)."""
        await self._command("IMU_GET")
        if self._v11:
            await self._command("MOTION2_GET")
        return self._motion_message()

    def _motion_message(self) -> Message:
        d, fast = self._imu, self._m2 if self._v11 else None
        return Message(rate=fast["rate_hz"] if fast else d["rate_hz"],
                       sensors=SENSOR_NAMES.get(fast["sensors"]) if fast else "both",
                       accel_range=d["accel_range_g"], gyro_range=d["gyro_range_dps"])

    async def set_events(self, kinds: Optional[list] = None, tap_threshold: Optional[float] = None) -> Message:
        """Choose which motion events the band reports and how hard a tap must be.
        kinds: any of tap, double-tap, free-fall, tilt, orientation, motion.
        tap_threshold: in g, 0.2..8 (lower = more sensitive). Left out = unchanged."""
        want = await self._command("EVENT_GET") if kinds is None or tap_threshold is None else {}
        if kinds is not None:
            if isinstance(kinds, str):
                kinds = [kinds]
            unknown = [k for k in kinds if k not in EVENT_GROUPS]
            if unknown:
                raise _bad("Unknown event kind %r. Kinds: %s." % (unknown[0], ", ".join(EVENT_GROUPS)))
            want["event_mask"] = sum(1 << EVENT_GROUPS.index(k) for k in set(kinds))
        if tap_threshold is not None:
            if isinstance(tap_threshold, bool) or not isinstance(tap_threshold, (int, float)) \
                    or not 0.2 <= tap_threshold <= 8:
                raise _bad("tap_threshold is in g, from 0.2 to 8, got %r." % (tap_threshold,))
            want["tap_threshold_mg"] = round(tap_threshold * 1000)
        return self._events_message(await self._command("EVENT_SET", **want))

    async def get_events(self) -> Message:
        """kinds (list of names) and tap_threshold (g)."""
        return self._events_message(await self._command("EVENT_GET"))

    @staticmethod
    def _events_message(d: dict) -> Message:
        kinds = [name for bit, name in enumerate(EVENT_GROUPS) if d["event_mask"] >> bit & 1]
        return Message(kinds=kinds, tap_threshold=d["tap_threshold_mg"] / 1000)

    # ---- microphone, radio, battery ----

    async def set_mic_gain(self, db: float) -> float:
        """Microphone gain in dB, -20..+20 in half steps. Returns the gain the band applied."""
        if isinstance(db, bool) or not isinstance(db, (int, float)) or not -20 <= db <= 20:
            raise _bad("Mic gain is in dB, from -20 to 20, got %r." % (db,))
        d = await self._command("MIC_SET_GAIN", gain_half_db=round(db * 2) + 40)
        return (d["gain_half_db"] - 40) / 2

    async def get_mic(self) -> Message:
        """gain_db, sample_rate, frame_ms, channels, bitrate."""
        d = await self._command("MIC_GET")
        return Message(gain_db=(d["gain_half_db"] - 40) / 2, sample_rate=d["sample_rate"],
                       frame_ms=d["frame_ms"], channels=d["channels"], bitrate=d["bitrate"])

    async def set_tx_power(self, dbm: int) -> int:
        """Radio power in dBm: -20, -16, -12, -8, -4, 0 or 4. Returns what the band applied."""
        if isinstance(dbm, bool) or not isinstance(dbm, int):
            raise _bad("Radio power is one of -20, -16, -12, -8, -4, 0, 4 (dBm), got %r." % (dbm,))
        return (await self._command("RADIO_SET_TX_POWER", dbm=dbm))["dbm"]

    async def get_radio(self) -> Message:
        """tx_power (dBm), rssi (dBm), phy ("1M"/"2M"), interval_ms, mtu.
        Over USB with no Bluetooth link rssi and phy are None, interval_ms and mtu are 0."""
        d = await self._command("RADIO_GET")
        return Message(tx_power=d["tx_dbm"], rssi=None if d["rssi_dbm"] == 127 else d["rssi_dbm"],
                       phy=PHYS.get(d["phy"]), interval_ms=d["interval"] * 1.25, mtu=d["mtu"])

    async def get_battery(self) -> Message:
        """percent, millivolts, state ("battery", "charging", "charged"), charging (True/False)."""
        return _battery(await self._command("BATTERY_GET"))


class Storage:
    """band.storage: a small key-value store on the band. Keys are numbers 0..999, values
    are 1..232 bytes. Anyone who connects next can read, change or wipe it.

    The band accepts 10 writes (put, delete) a second. When it answers "busy" the library
    waits a moment and sends again, so a loop of 30 puts just takes about 3 seconds."""

    def __init__(self, band: Band) -> None:
        self._band = band
        self._busy_pause = 0.1    # seconds to wait before sending again after "busy"
        self._busy_seconds = 3.0  # give up after being told "busy" for this long

    async def _command(self, name: str, **args) -> dict:
        """Send a storage command; while the band answers "busy", wait and send again."""
        loop = asyncio.get_event_loop()
        give_up = loop.time() + self._busy_seconds
        while True:
            try:
                return await self._band._command(name, **args)
            except VexoError as e:
                if e.code != "busy":
                    raise
                if loop.time() + self._busy_pause > give_up:
                    error = VexoError("busy", "The band's storage is still busy after %g seconds (it accepts "
                                      "10 writes a second). Try again later." % self._busy_seconds)
                    error.status = "BUSY"
                    raise error
            await asyncio.sleep(self._busy_pause)

    async def put(self, key: int, value) -> None:
        """Save text (stored as UTF-8) or bytes under key."""
        data = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        if not 1 <= len(data) <= 232:
            raise _bad("A stored value must be 1 to 232 bytes long; this one is %d." % len(data))
        await self._command("STORE_PUT", key=key, value=data)

    async def get(self, key: int) -> Optional[bytes]:
        """The bytes stored under key, or None if there is nothing."""
        try:
            return (await self._command("STORE_GET", key=key))["value"]
        except VexoError as e:
            if e.code == "not-found":
                return None
            raise

    async def get_text(self, key: int) -> Optional[str]:
        """Like get, but as text. None if there is nothing."""
        data = await self.get(key)
        return None if data is None else data.decode("utf-8", "replace")

    async def delete(self, key: int) -> None:
        """Delete key. Raises VexoError "not-found" if nothing is stored there."""
        await self._command("STORE_DEL", key=key)

    async def info(self) -> Message:
        """keys_max, value_max, free_bytes, used_keys."""
        return Message(await self._command("STORE_INFO"))

    async def wipe(self, timeout: float = 30) -> None:
        """Erase every key. Returns when the band has finished erasing (a few seconds), so
        you can put() again straight away. timeout: seconds to wait for that at most;
        after it, VexoError "timeout"."""
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not timeout > 0:
            raise _bad("timeout is the seconds to wait for the erase, a number above 0, got %r." % (timeout,))
        loop = asyncio.get_event_loop()
        give_up = loop.time() + timeout
        await self._command("STORE_WIPE")  # answers at once; the erasing goes on in the band
        while True:
            try:
                await self._band._command("STORE_INFO")  # answers "busy" until the erase is done
                return
            except VexoError as e:
                if e.code == "not-available":
                    error = VexoError("not-available", "The band could not erase its storage. Call "
                                      "band.storage.wipe() again.")
                    error.status = "NOT_AVAILABLE"
                    raise error
                if e.code != "busy":
                    raise
            if loop.time() + self._busy_pause > give_up:
                raise VexoError("timeout", "The band's storage is still erasing after %g seconds. Wait a "
                                "little, then check with band.storage.info()." % timeout)
            await asyncio.sleep(self._busy_pause)


def _current_task():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None  # not inside the event loop


def _report(event: str, task) -> None:
    """Print the error of an async handler; it must not stop the band's data."""
    if not task.cancelled() and task.exception() is not None:
        e = task.exception()
        print('Error in your "%s" handler:' % event, file=sys.stderr)
        traceback.print_exception(type(e), e, e.__traceback__)
