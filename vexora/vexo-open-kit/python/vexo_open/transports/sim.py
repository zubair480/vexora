"""A pretend band. It speaks the real protocol (codec.py), including the error answers,
so every program in this kit runs with no hardware.

    sim = SimTransport(42)
    band = await Band.connect(42, transport=sim)     # or simply transport="sim"

Tests use realtime=False and move the band's clock themselves with sim.step(ms).
It is a band with firmware 1.1 (fast motion, stream intervals, a clock). SimTransport(42,
firmware="1.0") behaves like the older firmware, which knows none of those.

Left alone it makes up its own data: a gentle swing, a slow walk, a voice that comes and
goes, a tap every 4 seconds. To make things happen when you say so, direct it:

    sim.tap("double-tap")                              # also free-fall, tilt, orientation, motion-start, motion-stop
    sim.play_motion(lambda s: dict(gz=200), seconds=0.5)   # half a second of turning; s = seconds into the motion
    sim.set_loudness(0.3)                              # a steady loudness, 0 (silence) to 1
    sim.play_loudness([0.95])                          # one loud reading: a clap
    sim.set_rssi(-85)                                  # signal strength in dBm
    sim.set_activity("run")                            # or "walk", "still"
    sim.set_battery(12)                                # percent
    sim.drop_link(seconds=3)                           # the link drops; the band cannot be found for 3 seconds
    sim.after(2, lambda: sim.tap("free-fall"))         # in 2 seconds of band time

In a plain (vexo_open.sync) program call these as band.transport.tap(...): that runs them
on the band's own thread, from the main program and from a handler alike.

The band's counter (the `t` of every reading) keeps counting while nothing is connected,
like a real band's, and starts again at 0 when the band restarts.
"""
import asyncio
import json
import math
import os
import sys
import tempfile
import time
import traceback

from .. import codec
from ..codec import STREAM_BITS
from ..transport import Transport, VexoError, not_found_help

STORE_BYTES = 242164  # bytes free when the storage is empty, as on a real band
STORE_WRITES_PER_SECOND = 10  # more writes (put, delete) than this in one second: BUSY
LED_OFF = dict(r=0, g=0, b=0, brightness=0, pattern=0, period_ms=1000)
MIC_LIGHT = dict(r=0, g=0, b=255, brightness=255, pattern=1, period_ms=1000, source="mic")
IDENTIFY_LIGHT = dict(r=255, g=255, b=255, brightness=255, pattern=2, period_ms=250, source="identify")
EVENT_MASK_BIT = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 5}  # event code -> bit in event_mask

# ---- firmware 1.1 ----
# Fastest MOTION2 rate per link, for accel alone, gyro alone and both sensors.
MOTION2_CAPS = {"ble": dict(max_accel_hz=833, max_gyro_hz=833, max_both_hz=416),
                "usb": dict(max_accel_hz=1666, max_gyro_hz=833, max_both_hz=833)}
MOTION2_CAP_OF = {1: "max_accel_hz", 2: "max_gyro_hz", 3: "max_both_hz"}  # sensors value -> its cap
MOTION2_FRAME_MS = 50  # a MOTION2 frame holds the samples of this many milliseconds
# From 208 a second up the motion chip runs on its own clock, which is never exactly right.
# This pretend chip is 0.8 % slow, so its measured period_us is a little longer than
# 1000000 / rate: a program that assumes the nominal rate drifts, as with a real band.
CHIP_CLOCK = 1.008
# Stream bit -> (shortest, longest, default) milliseconds between frames. BATTERY may
# also be 0 = only when it changes; MIC_LEVEL must be a multiple of 20.
INTERVALS = {2: (100, 3600000, 1000), 4: (1000, 3600000, 0), 5: (100, 3600000, 5000),
             6: (100, 3600000, 1000), 7: (20, 10000, 100)}
INTERVAL_STREAMS = {2: "activity", 4: "battery", 5: "temp", 6: "link", 7: "mic_level"}
TIME_MIN_MS, TIME_MAX_MS = 1577836800000, 4102444800000  # 2020-01-01 and 2100-01-01

# ---- directing the pretend band ----
REST = dict(ax=0.0, ay=0.0, az=1.0, gx=0.0, gy=0.0, gz=0.0)  # lying flat: 1 g straight up, no turning
# set_activity(state): state -> (code, steps a minute, motion in milli-g)
ACTIVITIES = {"still": (0, 0, 12), "walk": (1, 110, 300), "run": (2, 165, 600)}
BATTERY_STATES = ("battery", "charging", "charged")
USB_PAUSE_MS = 200  # a computer that took nothing for this long: stream frames are given up (contract 2.2)
USB_GIVE_UP_MS = 5200  # ... and for this long: the band ends the USB session (firmware 1.1.3)
USB_KEPT_MOST = 64  # the most frames the pretend band keeps for a computer that does not read
RESPONSE_FRAME = 0x02  # the type byte of a response frame
KEPT_TIMERS = ("battery-drain", "script", "listen", "give-up")  # what goes on when only the USB session ends
STORE_FOLDER = ".vexo-open"  # store_file=True keeps the storage in this folder in the user's home folder


def store_path(number: int) -> str:
    """Where SimTransport(number, store_file=True) keeps the pretend band's storage:
    .vexo-open/pretend-band-<number>.json in your home folder."""
    return os.path.join(os.path.expanduser("~"), STORE_FOLDER, "pretend-band-%d.json" % int(number))


def _curve(source, seconds):
    """One entry of a play_motion / play_loudness queue."""
    if seconds is not None and (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                                or not seconds > 0):
        raise ValueError("seconds is how long to play it, a number above 0 (or left out), got %r." % (seconds,))
    if isinstance(source, (list, tuple)):
        return dict(values=list(source), fn=None, seconds=seconds, at=0.0, i=0)
    fn = source if callable(source) else (lambda s: source)  # one value: held steady
    return dict(values=None, fn=fn, seconds=seconds, at=0.0, i=0)


def _queue(queue: list, entry: dict) -> None:
    """Add a curve behind the ones that are waiting. One that would never end by itself
    (a function with no `seconds`) makes way for it."""
    if queue and queue[-1]["fn"] is not None and queue[-1]["seconds"] is None:
        queue.pop()
    if entry["fn"] is not None or entry["values"]:
        queue.append(entry)


def _take(queue: list, step: float):
    """The next value of the curve that is playing, or None when nothing is. Moves the
    curve on by `step` seconds (the time between two readings)."""
    while queue:
        e = queue[0]
        try:
            value = e["fn"](e["at"]) if e["fn"] is not None else e["values"][e["i"]]
        except Exception:
            print("Error in a function given to the pretend band:", file=sys.stderr)
            traceback.print_exc()
            queue.pop(0)
            continue
        e["at"] += step
        e["i"] += 1
        over = e["seconds"] is not None and e["at"] >= e["seconds"] - 1e-9
        if over or (e["values"] is not None and e["i"] >= len(e["values"])):
            queue.pop(0)
        return value
    return None


# configure() name -> contract stream name, for other_program_starts()
CONFIGURE_STREAMS = {"motion": "motion", "events": "event", "activity": "activity", "audio": "audio",
                     "battery": "battery", "temperature": "temp", "link": "link", "mic_level": "mic_level"}


class SimTransport(Transport):
    kind = "sim"

    def __init__(self, number: int = 42, realtime: bool = True, via: str = "ble",
                 audio_packets=None, on_led=None, wipe_ms: float = 1000, firmware: str = "1.1",
                 auto_tap: bool = True, store_file=None) -> None:
        """number: the band number it reports. realtime=False: no timers, call step(ms).
        via: "ble" or "usb", which link the pretend band believes it is on.
        audio_packets: list of Opus packets (bytes) to play in a loop instead of silence.
        on_led: function called with the light the band shows whenever it changes.
        wipe_ms: how long erasing the whole storage takes, in milliseconds of band time.
        firmware: "1.1", or "1.0" for a band that does not know fast motion, intervals or the clock.
        auto_tap: False = it does not tap itself every 4 seconds (only your own tap() calls count).
        store_file: where the pretend band's storage is kept between two runs of a program.
        None (the default): nowhere, it lives in memory and is gone when the program ends.
        A file name: that file (its folder must exist). True: store_path(number), a file in
        the folder .vexo-open in your home folder. The file is only readable by you.
        Like a real band's storage it is not a place for anything private."""
        super().__init__()
        if via not in ("ble", "usb"):
            raise VexoError("bad-arg", "SimTransport via must be \"ble\" or \"usb\", got %r." % (via,))
        if firmware not in ("1.0", "1.1"):
            raise VexoError("bad-arg", "SimTransport firmware must be \"1.0\" or \"1.1\", got %r." % (firmware,))
        codec.band_name(number)  # ValueError for a number that cannot be a band
        if not isinstance(auto_tap, bool):
            raise VexoError("bad-arg", "SimTransport auto_tap must be True or False, got %r." % (auto_tap,))
        self.firmware = firmware
        self.number = int(number)
        self.realtime = realtime
        self.auto_tap = auto_tap
        self.via = via
        self.audio_packets = list(audio_packets or [])
        self.on_led = on_led
        self.wipe_ms = wipe_ms
        # False = the band cannot be found (switched off, out of range): open() fails.
        # drop_link() leaves it True, like a real band that can be found again.
        self.reachable = True
        # True = something else is connected to the band over Bluetooth (the dashboard, a
        # teammate's program), so it cannot be found: open() fails with "not-found-nearby"
        # and the same sentence a real band gives, until you set it back to False.
        self.connected_elsewhere = False
        self._elsewhere = 0  # other_program_starts(): the streams that go to the band's other link
        # True = the library watches this pretend band the way it watches a band on the USB
        # cable (for a session the band ended). False until end_usb_session(), usb_pause()
        # or stop_reading() is used.
        self.session_watch = False
        self.on_session_hint = lambda: None  # assigned by Band: an empty packet came mid-session
        self._deaf = None  # stop_reading(): dict(since, ended, kept, dropped)
        self._new_session = False  # end_usb_session(): the next frame has one 00 in front of it
        # These survive disconnects, like on a real band.
        self.store = {}
        self._writes = []      # band time of each write accepted in the last second
        self._wipe_end = None  # band time at which a running wipe is finished
        self.now_ms = 0.0
        self.clock_offset = None  # real time minus band time in ms, once TIME_SET gave it; None = not set
        self.steps_total = 1200
        self.battery_percent = 87
        self.battery_state = 0  # 0 on battery, 1 charging, 2 charged
        self.is_open = False
        self._task = None
        self._shown = None
        self._tap_count = 0
        # What you directed (see "directing the pretend band" below). It all outlives a link.
        self._motions = []        # play_motion: the motions waiting to be played, one after the other
        self._directed = False    # play_motion was used: between two motions the band lies still
        self._loud = None         # set_loudness: the steady loudness, None = the made-up voice
        self._loudness = []       # play_loudness: the curves waiting to be played
        self._packets = []        # ... and their Opus packets, each sent once
        self._rssi_set = None     # set_rssi
        self._walk = None         # set_activity: dict(state, cadence, motion, since, part)
        self._plans = []          # after(): [band time it is due, action], soonest first
        self._away_until = None   # drop_link(seconds): band time from which it can be found again
        self._down_since = None   # realtime: the computer's clock when the link closed
        self._rebased = 0.0       # band time taken away by a restart during step()
        self._store_path = None
        self._saved = {}          # the storage as it is in the file
        if store_file is not None and store_file is not False:
            self._store_path = store_path(self.number) if store_file is True else os.fspath(store_file)
            folder = os.path.dirname(os.path.abspath(self._store_path))
            if store_file is True:
                os.makedirs(folder, mode=0o700, exist_ok=True)  # only you can look into it
            elif not os.path.isdir(folder):
                raise VexoError("bad-arg", "SimTransport store_file: the folder %s does not exist." % folder)
            self._load_store()
        self._reset()

    def _reset(self) -> None:
        """Settings go back to the contract's defaults whenever the link closes."""
        self._elsewhere = 0
        self.active = 0
        self.imu = dict(rate_hz=25, accel_range_g=2, gyro_range_dps=2000)
        self.events = dict(event_mask=0x3F, tap_threshold_mg=800)
        self.gain_half_db = 80
        self.tx_dbm = -8
        self.user_led = dict(LED_OFF)
        self.motion2 = dict(rate_hz=25, sensors=3)
        self.sample_no = 0  # running number of the next MOTION2 sample
        self.intervals = {bit: row[2] for bit, row in INTERVALS.items()}  # stream bit -> ms
        self.identifying = False
        self._audio_seq = 0
        self._timers = {}  # name -> [time it is due, repeat every ms (None = once), function]

    @property
    def temp_seconds(self):
        """Seconds between temperature readings (the same setting as intervals[5])."""
        seconds = self.intervals[5] / 1000
        return int(seconds) if seconds == int(seconds) else seconds

    # ---- the Transport interface ----

    async def open(self) -> None:
        self._catch_up()
        if self.connected_elsewhere:
            raise VexoError("not-found-nearby", not_found_help(codec.band_name(self.number)))
        if not self.reachable:
            raise VexoError("not-found-nearby", "Could not see %s: the pretend band is switched off "
                            "(its reachable setting is False)." % codec.band_name(self.number))
        if self._away_until is not None and self.now_ms < self._away_until:
            raise VexoError("not-found-nearby", "Could not see %s: the pretend band is out of range for "
                            "another %.1f seconds (drop_link was called with seconds)."
                            % (codec.band_name(self.number), (self._away_until - self.now_ms) / 1000))
        self._away_until = None
        self._down_since = None
        if self._task is not None:  # opened twice: stop the old clock first
            self._task.cancel()
            self._task = None
        self._reset()
        self._deaf = None
        self._new_session = False
        self.is_open = True
        # On the cable, bytes travel COBS-framed in both directions, as on a real one.
        self._to_band = codec.Deframer(synced=True)
        self._to_client = codec.Deframer(synced=True)
        self._every("battery-drain", 60000, self._drain_battery)
        if self.realtime:
            if self.auto_tap:
                self._every("script", 4000, self._scripted_tap)
            self._task = asyncio.ensure_future(self._run())

    async def close(self) -> None:
        self._shutdown(None)

    async def send(self, frame: bytes) -> None:
        if not self.is_open:
            raise VexoError("not-connected", "The pretend band is not connected.")
        frames = self._to_band.push(codec.frame_for_usb(frame)) if self.via == "usb" else [bytes(frame)]
        for f in frames:
            self._handle(f)

    def _emit(self, frame: bytes) -> None:
        if not self.is_open:
            return
        deaf = self._deaf
        if deaf is not None:  # stop_reading(): the computer takes nothing
            if deaf["ended"]:
                return
            if deaf["since"] is None:
                deaf["since"] = self.now_ms  # from now on the band has data waiting
                self._every("give-up", USB_GIVE_UP_MS, self._give_up, once=True)
            keep = self.now_ms - deaf["since"] < USB_PAUSE_MS or frame[0] == RESPONSE_FRAME
            if keep and len(deaf["kept"]) < USB_KEPT_MOST:
                deaf["kept"].append(frame)
            else:
                deaf["dropped"] = True
            return
        if self._new_session:  # the first frame of a session has one 00 in front of it
            self._new_session = False
            self._empty_packet()
        frames = self._to_client.push(codec.frame_for_usb(frame)) if self.via == "usb" else [frame]
        for f in frames:
            self.on_frame(f)

    def _shutdown(self, reason) -> None:
        if not self.is_open:
            return
        self.is_open = False
        self._down_since = time.monotonic() if self.realtime else None
        self._reset()
        self._show_led()
        if self._task is not None and self._task is not _current_task():
            self._task.cancel()
        self._task = None
        if reason:
            self.on_close(reason)

    # ---- the clock ----

    async def _run(self) -> None:
        loop = asyncio.get_event_loop()
        last = loop.time()
        while self.is_open:
            await asyncio.sleep(0.02)
            now = loop.time()
            self.step((now - last) * 1000)
            last = now

    def step(self, ms: float) -> None:
        """Let ms milliseconds pass on the band: sends whatever the streams produce meanwhile.
        The band's counter also moves while the link is closed (nothing is sent then)."""
        end = self.now_ms + ms
        self._rebased = 0.0
        while self.is_open:
            due = [(t[0], 0, name) for name, t in self._timers.items() if t[0] <= end]
            if self._plans and self._plans[0][0] <= end:
                due.append((self._plans[0][0], 1, ""))
            if not due:
                break
            at, plan, name = min(due)
            self.now_ms = max(self.now_ms, at)
            if plan:
                self._run_plan(self._plans.pop(0)[1])
            else:
                timer = self._timers[name]
                if timer[1] is None:
                    del self._timers[name]
                else:
                    timer[0] = at + timer[1]
                timer[2]()
            end -= self._rebased  # the band restarted: the rest of the time passes on its new counter
            self._rebased = 0.0
        self.now_ms = max(self.now_ms, end)
        self._storage_busy()  # a wipe that ended meanwhile empties the store
        self._save_store()

    def _catch_up(self) -> None:
        """realtime: a band goes on counting while nothing is connected. Add the time the
        link has been closed to the band's counter."""
        if self._down_since is not None:
            now = time.monotonic()
            self.now_ms += (now - self._down_since) * 1000
            self._down_since = now
            self._storage_busy()

    @staticmethod
    def _run_plan(action) -> None:
        try:
            action()
        except Exception:
            print("Error in a function given to the pretend band's after():", file=sys.stderr)
            traceback.print_exc()

    def _every(self, name: str, ms: float, fn, once: bool = False) -> None:
        self._timers[name] = [self.now_ms + ms, None if once else ms, fn]

    def _t(self) -> int:
        return int(self.now_ms) & 0xFFFFFFFF

    def _wave(self, seconds: float) -> float:
        """A sine wave from -1 to 1 that repeats every `seconds` of band time."""
        return math.sin(2 * math.pi * self.now_ms / (seconds * 1000))

    # ---- commands ----

    def _handle(self, frame: bytes) -> None:
        cmd = codec.decode_command(frame)
        if cmd is None:
            return  # not a command: a real band drops it with no reply
        name = cmd["opcode"]
        if not isinstance(name, str) or (self.firmware == "1.0" and name in codec.V11_COMMANDS):
            status, data = "UNKNOWN_OPCODE", None
        elif cmd.get("bad_length"):
            status, data = "BAD_LENGTH", None
        else:
            answer = getattr(self, "_do_" + name.lower())(cmd["args"])
            status, data = answer if isinstance(answer, tuple) else (answer, None)
        self._save_store()  # before the answer: what the band said it stored is on the disk
        self._emit(codec.encode_response(cmd["seq"], name, status, data))

    def _do_get_info(self, a):
        minor = 0 if self.firmware == "1.0" else 1
        return "OK", dict(proto_major=1, proto_minor=minor, fw_major=1, fw_minor=minor, fw_patch=0, hw=1,
                          band_no=self.number, caps=0x07FF, max_frame=codec.MAX_FRAME, uptime_ms=self._t())

    def _do_get_name(self, a):
        return "OK", dict(name=codec.band_name(self.number))

    def _do_set_band_number(self, a):
        if self.via != "usb":
            return "DENIED"
        if a["magic"] != codec.MAGIC or not 1 <= a["number"] <= 999:
            return "BAD_ARG"
        self.number = a["number"]
        return "OK", dict(number=self.number)

    def _do_identify(self, a):
        if self._mic_on():
            return "DENIED"
        if not 1 <= a["seconds"] <= 30:
            return "BAD_ARG"
        self.identifying = True
        self._every("identify", a["seconds"] * 1000, self._identify_done, once=True)
        self._show_led()
        return "OK"

    def _identify_done(self) -> None:
        self.identifying = False
        self._show_led()

    def _do_restart(self, a):
        self._every("power", 200, self._restart, once=True)
        return "OK"

    def _restart(self) -> None:
        self._restart_counter()
        self._shutdown("restart")

    def _restart_counter(self) -> None:
        """The band starts again: its clock is lost, and its counter (t_ms, uptime_ms)
        begins at 0. Storage, the band number and the step count stay."""
        self.clock_offset = None  # the clock is only kept while the band stays running
        was = self.now_ms
        self._rebased += was
        self.now_ms = 0.0
        self._writes = [t - was for t in self._writes]
        if self._wipe_end is not None:
            self._wipe_end -= was
        if self._away_until is not None:
            self._away_until -= was
        for plan in self._plans:
            plan[0] -= was
        if self._walk:
            self._walk["since"] -= was

    def _do_time_set(self, a):
        if not TIME_MIN_MS <= a["unix_ms"] < TIME_MAX_MS:
            return "BAD_ARG"  # e.g. seconds instead of milliseconds
        self.clock_offset = a["unix_ms"] - int(self.now_ms)
        return self._do_time_get(a)

    def _do_time_get(self, a):
        if self.clock_offset is None:
            return "OK", dict(set=0, unix_ms=0, t_ms=self._t())
        return "OK", dict(set=1, unix_ms=self.clock_offset + int(self.now_ms), t_ms=self._t())

    def _do_sleep(self, a):
        if self.via == "usb":  # the cable keeps the session open: only the streams and the light stop
            self._every("power", 200, self._usb_sleep, once=True)
        else:
            self._every("power", 200, lambda: self._shutdown("sleep"), once=True)
        return "OK"

    def _usb_sleep(self) -> None:
        self._set_streams(0)
        self.user_led = dict(LED_OFF)
        self._show_led()

    def _do_stream_start(self, a):
        if a["mask"] > (0xFF if self.firmware == "1.0" else 0x1FF):
            return "NOT_AVAILABLE"  # a stream this band does not have: nothing starts
        if self.via == "usb" and a["mask"] >> STREAM_BITS["link"] & 1:
            return "NOT_AVAILABLE"  # the LINK stream is Bluetooth only: nothing starts
        if a["mask"] >> STREAM_BITS["motion2"] & 1 and self._motion2_too_fast(self.motion2):
            return "DENIED"  # MOTION2 is set faster than this link allows: nothing starts
        if self._gyro_beside_1666(self.active | a["mask"], self.motion2, self.imu):
            return "DENIED"  # whichever of MOTION and MOTION2 comes second is refused: nothing starts
        self._set_streams(self.active | a["mask"])
        self._elsewhere &= ~a["mask"]  # a stream goes to the link that started it last: this one again
        return "OK", dict(active=self.active)

    def _do_stream_stop(self, a):
        self._set_streams(self.active & ~a["mask"])
        return "OK", dict(active=self.active)

    def _do_stream_get(self, a):
        return "OK", dict(active=self.active)

    def _interval_answer(self, bit: int):
        low, high, _ = INTERVALS[bit]
        return "OK", dict(stream=bit, interval_ms=self.intervals[bit], min_ms=low, max_ms=high)

    def _do_stream_set_interval(self, a):
        bit, ms = a["stream"], a["interval_ms"]
        if bit not in INTERVALS:
            return "BAD_ARG"  # MOTION, EVENT, AUDIO and MOTION2 are not sent on a timer
        low, high, _ = INTERVALS[bit]
        allowed = low <= ms <= high and (bit != 7 or ms % 20 == 0)
        if not allowed and not (bit == 4 and ms == 0):
            return "BAD_ARG"
        self._set_interval(bit, ms)
        return self._interval_answer(bit)

    def _do_stream_get_interval(self, a):
        if a["stream"] not in INTERVALS:
            return "BAD_ARG"
        return self._interval_answer(a["stream"])

    def _set_interval(self, bit: int, ms: int) -> None:
        """The new interval counts from now, also when the stream is already running."""
        self.intervals[bit] = ms
        stream = INTERVAL_STREAMS[bit]
        if self._on(stream):
            self._timers.pop(stream, None)
            if ms:  # battery with 0: only when it changes, no timer
                self._every(stream, ms, self._stream_plan()[stream][1])

    def _do_imu_set(self, a):
        if (a["rate_hz"] not in (10, 25, 50, 100) or a["accel_range_g"] not in (2, 4, 8, 16)
                or a["gyro_range_dps"] not in (0, 250, 500, 1000, 2000)):
            return "BAD_ARG"
        if self._gyro_beside_1666(self.active, self.motion2, a):
            return "DENIED"  # the gyroscope may not come on beside MOTION2 at 1666 Hz: nothing changes
        self.imu = dict(a)
        if "motion" in self._timers:
            self._every("motion", 10000 / a["rate_hz"], self._send_motion)
        return "OK", dict(self.imu)

    def _motion2_too_fast(self, setting: dict) -> bool:
        return setting["rate_hz"] > MOTION2_CAPS[self.via][MOTION2_CAP_OF[setting["sensors"]]]

    @staticmethod
    def _gyro_beside_1666(active: int, motion2: dict, imu: dict) -> bool:
        """1666 Hz never runs together with the gyroscope (contract 5.2). MOTION runs the
        gyroscope unless its range is 0, so MOTION with the gyroscope on and MOTION2 at
        1666 Hz never stream at once. True if they would, with these streams and settings."""
        both = active >> STREAM_BITS["motion"] & 1 and active >> STREAM_BITS["motion2"] & 1
        return bool(both) and motion2["rate_hz"] == codec.MOTION2_RATES[-1] and imu["gyro_range_dps"] != 0

    def _do_motion2_set(self, a):
        if a["rate_hz"] not in codec.MOTION2_RATES or a["sensors"] not in (1, 2, 3):
            return "BAD_ARG"
        if self._on("motion2") and self._motion2_too_fast(a):
            return "DENIED"  # too fast for the link it is streaming on: nothing changes
        if self._gyro_beside_1666(self.active, a, self.imu):
            return "DENIED"  # 1666 Hz while MOTION runs the gyroscope: nothing changes
        if a != self.motion2:
            self.motion2 = dict(a)
            if self._on("motion2"):  # a changed stream counts its samples from 0 again
                self.sample_no = 0
                self._every("motion2", self._motion2_frame()[1], self._send_motion2)
        return self._do_motion2_get(a)

    def _do_motion2_get(self, a):
        return "OK", dict(self.motion2, **MOTION2_CAPS[self.via])

    def _do_imu_get(self, a):
        return "OK", dict(self.imu)

    def _do_event_set(self, a):
        if a["event_mask"] > 0x3F or not 200 <= a["tap_threshold_mg"] <= 8000:
            return "BAD_ARG"
        self.events = dict(a)
        return "OK", dict(self.events)

    def _do_event_get(self, a):
        return "OK", dict(self.events)

    def _do_mic_set_gain(self, a):
        if a["gain_half_db"] > 80:
            return "BAD_ARG"
        self.gain_half_db = a["gain_half_db"]
        return "OK", dict(a)

    def _do_mic_get(self, a):
        return "OK", dict(gain_half_db=self.gain_half_db, sample_rate=16000, frame_ms=20, channels=1,
                          bitrate=24000)

    def _do_radio_set_tx_power(self, a):
        if a["dbm"] not in (-20, -16, -12, -8, -4, 0, 4):
            return "BAD_ARG"
        self.tx_dbm = a["dbm"]
        return "OK", dict(a)

    def _do_radio_get(self, a):
        if self.via == "usb":  # no Bluetooth link to measure
            return "OK", dict(tx_dbm=self.tx_dbm, rssi_dbm=127, phy=0, interval=0, mtu=0)
        return "OK", dict(tx_dbm=self.tx_dbm, rssi_dbm=self._rssi(), phy=2,
                          interval=12 if self._audio_on() else 24, mtu=247)

    def _do_led_set(self, a):
        if self._mic_on():
            return "DENIED"  # the mic light owns the LED
        uses_period = a["pattern"] >= 2
        if a["pattern"] > 5 or (uses_period and not 100 <= a["period_ms"] <= 10000):
            return "BAD_ARG"
        self.user_led = dict(a)
        self._show_led()
        return "OK"

    def _do_led_get(self, a):
        return "OK", dict(self.user_led)

    def _do_temp_set_interval(self, a):
        if a["seconds"] < 1:
            return "BAD_ARG"
        self._set_interval(STREAM_BITS["temp"], a["seconds"] * 1000)  # one setting, two commands
        return "OK", dict(a)

    def _do_battery_get(self, a):
        return "OK", self._battery()

    # Storage answers in this order: a wrong argument first (BAD_LENGTH, BAD_ARG), then BUSY
    # (a wipe is running, or too many writes this second), then NOT_FOUND / NO_SPACE / OK.

    @staticmethod
    def _cost(value: bytes) -> int:
        """Bytes one key uses: 8, plus its value rounded up to a multiple of 4."""
        return 8 + (len(value) + 3) // 4 * 4

    def _storage_busy(self) -> bool:
        """True while a wipe is running. The wipe is finished "by band time": when its
        end time has passed the store is emptied, also if the link closed meanwhile."""
        if self._wipe_end is not None and self.now_ms >= self._wipe_end:
            self.store.clear()
            self._wipe_end = None
        return self._wipe_end is not None

    def _too_many_writes(self) -> bool:
        self._writes = [t for t in self._writes if t > self.now_ms - 1000]
        return len(self._writes) >= STORE_WRITES_PER_SECOND

    def _do_store_info(self, a):
        if self._storage_busy():
            return "BUSY"
        used = sum(self._cost(v) for v in self.store.values())
        return "OK", dict(keys_max=1000, value_max=232, free_bytes=STORE_BYTES - used,
                          used_keys=len(self.store))

    def _do_store_put(self, a):
        if not 1 <= len(a["value"]) <= 232:
            return "BAD_LENGTH"
        if a["key"] > 999:
            return "BAD_ARG"
        if self._storage_busy() or self._too_many_writes():
            return "BUSY"
        used = sum(self._cost(v) for k, v in self.store.items() if k != a["key"])
        if used + self._cost(a["value"]) > STORE_BYTES:
            return "NO_SPACE"
        self.store[a["key"]] = a["value"]
        self._writes.append(self.now_ms)
        return "OK"

    def _do_store_get(self, a):
        if a["key"] > 999:
            return "BAD_ARG"
        if self._storage_busy():
            return "BUSY"
        if a["key"] not in self.store:
            return "NOT_FOUND"
        return "OK", dict(value=self.store[a["key"]])

    def _do_store_del(self, a):
        if a["key"] > 999:
            return "BAD_ARG"
        if self._storage_busy() or self._too_many_writes():
            return "BUSY"
        if a["key"] not in self.store:
            return "NOT_FOUND"
        del self.store[a["key"]]
        self._writes.append(self.now_ms)
        return "OK"

    def _do_store_wipe(self, a):
        if a["magic"] != codec.MAGIC:
            return "BAD_ARG"
        if self._storage_busy():
            return "BUSY"
        self._wipe_end = self.now_ms + self.wipe_ms  # answers at once; the erasing goes on
        self._storage_busy()  # wipe_ms=0: finished already
        return "OK"

    # ---- streams ----

    def _audio_on(self) -> bool:
        return bool(self.active >> STREAM_BITS["audio"] & 1)

    def _mic_on(self) -> bool:
        """The microphone is powered: audio or the loudness meter is running."""
        return self._audio_on() or self._on("mic_level")

    def _on(self, stream: str) -> bool:
        return bool(self.active >> STREAM_BITS[stream] & 1)

    def _stream_plan(self) -> dict:
        """stream -> (milliseconds between frames, function that sends one)"""
        return {
            "motion": (10000 / self.imu["rate_hz"], self._send_motion),  # 10 samples per frame
            "activity": (self.intervals[2], self._send_activity),
            "audio": (20, self._send_audio),
            "battery": (self.intervals[4], self._send_battery),  # 0 = only when it changes
            "temp": (self.intervals[5], self._send_temp),
            "link": (self.intervals[6], self._send_link),
            "mic_level": (self.intervals[7], self._send_mic_level),
            "motion2": (self._motion2_frame()[1], self._send_motion2),
        }

    def _set_streams(self, mask: int) -> None:
        started = mask & ~self.active
        self.active = mask
        self._elsewhere &= mask  # a stream that is off goes nowhere
        for stream, (ms, fn) in self._stream_plan().items():
            bit = 1 << STREAM_BITS[stream]
            if started & bit and ms:
                self._every(stream, ms, fn)
            elif not mask & bit:
                self._timers.pop(stream, None)
        if started >> STREAM_BITS["audio"] & 1:
            self._audio_seq = 0  # audio counts from 0 on every start
        if started >> STREAM_BITS["motion2"] & 1:
            self.sample_no = 0  # and MOTION2 numbers its samples from 0
        self._show_led()  # the mic light goes on before the first audio or loudness frame
        if started >> STREAM_BITS["battery"] & 1:
            self._send_battery()

    def _send(self, stream: str, **value) -> None:
        if self._elsewhere >> STREAM_BITS[stream] & 1:
            return  # this stream goes to the program on the other link
        self._emit(codec.encode_stream(stream, value))

    def _send_motion(self) -> None:
        period = 1000 // self.imu["rate_hz"]
        start = self.now_ms - 10 * period
        top_a = self.imu["accel_range_g"] * 1000   # milli-g
        top_g = self.imu["gyro_range_dps"] * 10    # tenths of a degree per second
        top = dict(a=top_a, g=top_g)
        samples = []
        for i in range(10):
            v = self._motion_reading(start + i * period, period / 1000)
            samples.append({f: int(max(-top[f[0]], min(top[f[0]], v[f]))) for f in v})
        self._send("motion", t_ms=int(start) & 0xFFFFFFFF, period_ms=period, samples=samples)

    def _motion_reading(self, at_ms: float, step: float) -> dict:
        """What the motion sensor reads at band time at_ms, in the band's units (milli-g and
        tenths of a degree per second): the motion you play, or the made-up swing.
        step: seconds to the next reading."""
        if self._directed:
            told = _take(self._motions, step)
            now = dict(REST, **told) if isinstance(told, dict) else REST
            return {f: round(now[f] * (1000 if f[0] == "a" else 10)) for f in REST}
        angle = 2 * math.pi * at_ms / 2000  # one gentle swing every 2 s
        return dict(ax=120 * math.sin(angle), ay=120 * math.cos(angle), az=1000 + 40 * math.sin(3 * angle),
                    gx=300 * math.cos(angle), gy=-300 * math.sin(angle), gz=100 * math.sin(2 * angle))

    def _motion2_frame(self) -> tuple:
        """(samples in one frame, milliseconds one frame covers, period_us) for the MOTION2 setting."""
        rate, both = self.motion2["rate_hz"], self.motion2["sensors"] == 3
        period_us = round(1000000 / rate * (CHIP_CLOCK if rate >= 208 else 1))
        fits = min((codec.MAX_FRAME - 15) // (12 if both else 6), 19 if both else 38)
        count = max(1, min(fits, rate * MOTION2_FRAME_MS // 1000))
        return count, count * period_us / 1000, period_us

    def _send_motion2(self) -> None:
        count, ms, period_us = self._motion2_frame()
        fields = codec.MOTION2_FIELDS[self.motion2["sensors"]]
        start = self.now_ms - ms
        top = dict(a=self.imu["accel_range_g"] * 1000, g=self.imu["gyro_range_dps"] * 10)  # gyro off: all 0
        samples = []
        for i in range(count):
            v = self._motion_reading(start + i * period_us / 1000, period_us / 1e6)  # the same as MOTION
            samples.append({f: int(max(-top[f[0]], min(top[f[0]], v[f]))) for f in fields})
        self._send("motion2", t_ms=int(start) & 0xFFFFFFFF, sample_no=self.sample_no & 0xFFFFFFFF,
                   period_us=period_us, sensors=self.motion2["sensors"], samples=samples)
        self.sample_no += count

    def _send_activity(self) -> None:
        if self._walk:  # set_activity: steps by the band's clock
            self._count_steps()
            self._send("activity", t_ms=self._t(), steps_total=self.steps_total & 0xFFFFFFFF,
                       cadence_spm=self._walk["cadence"], state=self._walk["state"],
                       motion_mg=self._walk["motion"])
            return
        self.steps_total += 2  # a slow walk
        self._send("activity", t_ms=self._t(), steps_total=self.steps_total, cadence_spm=110, state=1,
                   motion_mg=300 + int(40 * self._wave(7)))

    def _count_steps(self) -> None:
        """Bring steps_total up to date: the band counts by its clock, connected or not."""
        walk = self._walk
        if walk:
            steps = walk["part"] + (self.now_ms - walk["since"]) * walk["cadence"] / 60000
            self.steps_total += int(steps)
            walk["part"], walk["since"] = steps - int(steps), self.now_ms

    def _send_audio(self) -> None:
        if self._packets:
            opus = bytes(self._packets.pop(0))  # given to play_loudness: each one is sent once
        elif self.audio_packets:
            opus = self.audio_packets[self._audio_seq % len(self.audio_packets)]
        else:
            # A valid Opus packet of silence, padded to the size speech would have:
            # 0xBB = 20 ms mono frame, 0x41 = one frame plus padding, then n padding bytes.
            n = 50 + int(30 * self._wave(3))
            opus = bytes([0xBB, 0x41, n]) + bytes(n)
        self._send("audio", seq=self._audio_seq & 0xFFFF, opus=opus)
        self._audio_seq += 1

    def _battery(self) -> dict:
        return dict(percent=self.battery_percent, state=self.battery_state,
                    millivolts=3300 + 9 * self.battery_percent)

    def _send_battery(self) -> None:
        if self._on("battery"):
            self._send("battery", **self._battery())

    def _drain_battery(self) -> None:
        if self.battery_state == 0 and self.battery_percent > 1:
            self.set_battery(self.battery_percent - 1, 0)

    def _send_temp(self) -> None:
        celsius = 31 + 0.75 * self._wave(60)
        self._send("temp", t_ms=self._t(), temp_mc=round(celsius * 4) * 250)  # 0.25 degree steps

    def _rssi(self) -> int:
        if self._rssi_set is not None:
            return self._rssi_set
        return -55 + int(4 * self._wave(9))

    def _send_link(self) -> None:
        if self.via == "ble":  # the LINK stream is Bluetooth only
            self._send("link", t_ms=self._t(), rssi_dbm=self._rssi(), tx_dbm=self.tx_dbm, phy=2)

    def _send_mic_level(self) -> None:
        swell = max(0.0, self._wave(1.7)) * (0.6 + 0.4 * self._wave(5))  # comes and goes like speech
        gain_db = (self.gain_half_db - 40) / 2
        rms = int(max(0, min(96, 70 - 40 * swell - gain_db)))
        level = _take(self._loudness, self.intervals[7] / 1000)
        if not isinstance(level, (int, float)):
            level = self._loud
        if level is not None:  # directed: `level` is what a program reads at the default gain (+20 dB)
            rms = int(round(max(0.0, min(96.0, 96 * (1 - max(0.0, min(1.0, level))) + 20 - gain_db))))
        self._send("mic_level", t_ms=self._t(), peak_db=max(0, rms - 6), rms_db=rms)

    def _scripted_tap(self) -> None:
        self._tap_count += 1
        self.tap("double-tap" if self._tap_count % 2 == 0 else "tap")

    # ---- the light ----

    @property
    def led(self) -> dict:
        """The light the band shows right now: r, g, b, brightness, pattern, period_ms and
        source ("user" = your setting, "identify", or "mic" = the blue mic-is-on light)."""
        if self._mic_on():
            return dict(MIC_LIGHT)
        if self.identifying:
            return dict(IDENTIFY_LIGHT)
        light = dict(self.user_led, source="user")
        if self.battery_state == 0 and self.battery_percent < 15:
            light["brightness"] = min(64, light["brightness"])  # low battery dims the light
        return light

    def _show_led(self) -> None:
        state = self.led
        if state != self._shown:
            self._shown = state
            if self.on_led:
                self.on_led(dict(state))

    # ---- things a test or a web page can do to the pretend band ----

    def tap(self, kind="tap", arg: int = 0) -> bool:
        """Pretend something happened to the band: "tap", "double-tap", "free-fall", "tilt",
        "orientation" (arg = face 0..5), "motion-start", "motion-stop". Returns True if
        the band reported it (the event stream is on and this kind is switched on)."""
        codes = {name: code for code, name in codec.EVENT_KINDS.items()}
        code = codes.get(kind, kind)
        if code not in EVENT_MASK_BIT:
            raise ValueError("Unknown event kind %r. Kinds: %s." % (kind, ", ".join(codes)))
        if not self._on("event") or not self.events["event_mask"] >> EVENT_MASK_BIT[code] & 1:
            return False
        self._send("event", t_ms=self._t(), event=code, arg=arg)
        return True

    def set_battery(self, percent: int, state="battery") -> None:
        """Set the battery level, 0..100. state: "battery" (the default), "charging" or
        "charged" (or the contract's numbers 0, 1, 2). Below 15 % on battery the band
        sends its low-battery notice and dims the light."""
        if state in BATTERY_STATES:
            state = BATTERY_STATES.index(state)
        if isinstance(state, bool) or state not in (0, 1, 2):
            raise ValueError("set_battery state is \"battery\", \"charging\" or \"charged\", got %r." % (state,))
        self.battery_percent = max(0, min(100, int(percent)))
        self.battery_state = state
        self._show_led()
        if not self.intervals[4]:  # with an interval set, the next timed frame reports it
            self._send_battery()
        if self.battery_percent < 15:
            self._emit(codec.encode_notice("LOW_BATTERY", self.battery_percent))

    def drop_link(self, seconds=None) -> None:
        """Pretend the link dropped. The band can be found again at once (open() works),
        like a real one. drop_link(3): it cannot be found for the next 3 seconds, as if it
        was out of range, and comes back by itself. For "away until I say so" set
        reachable = False first and back to True later."""
        if seconds is not None:
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0:
                raise ValueError("drop_link seconds is how long the band stays away, 0 or more, got %r."
                                 % (seconds,))
            self._away_until = self.now_ms + seconds * 1000
        self._shutdown("lost")

    def other_program_starts(self, *names) -> None:
        """Pretend a second program, on the band's other link (the USB cable when this one
        is Bluetooth), started these streams: other_program_starts("motion", "mic_level").
        The names are the ones configure() takes. A stream goes to the program that started
        it last, so this program gets no more of them, with no notice; its link stays up,
        its commands are answered and STREAM_GET still says the streams are on. The library
        notices the silence after a few seconds ("stream-taken"); band.take_back() starts
        them for this program again."""
        mask = 0
        for name in names:
            if name not in CONFIGURE_STREAMS:
                raise ValueError('Unknown stream "%s". Streams: %s.' % (name, ", ".join(CONFIGURE_STREAMS)))
            if name == "motion":  # the motion stream that is on; otherwise the one this firmware's library would start
                both = 1 << STREAM_BITS["motion"] | 1 << STREAM_BITS["motion2"]
                mask |= self.active & both or 1 << STREAM_BITS["motion" if self.firmware == "1.0" else "motion2"]
            else:
                mask |= 1 << STREAM_BITS[CONFIGURE_STREAMS[name]]
        if not self.is_open:
            return
        self._set_streams(self.active | mask)
        self._elsewhere |= mask

    def other_program_closes(self) -> None:
        """Pretend that second program ended: the band stops the streams it had taken (a
        link that closes takes its streams with it). STREAM_GET now says they are off."""
        if self.is_open:
            self._set_streams(self.active & ~self._elsewhere)

    def end_usb_session(self) -> None:
        """Pretend the band ended the USB session by itself, as firmware 1.1.3 does when
        the computer took no data for 5 seconds: every stream stops, every setting goes back
        to its default (as with no Bluetooth client connected), and the band says nothing.
        The link stays open and commands are still answered. The next frame has one extra 00
        in front of it (an empty packet). From this call on the library watches this pretend
        band like a band on the USB cable: it notices the silence and ends the link with
        reason "session-ended"."""
        self.session_watch = True
        if not self.is_open:
            return
        self._set_streams(0)
        timers = self._timers
        self._reset()
        self._timers = {name: timer for name, timer in timers.items() if name in KEPT_TIMERS}
        if self._deaf is not None:
            self._deaf["kept"] = []  # an answer that was still waiting is thrown away
        self._new_session = True
        self._show_led()

    def usb_pause(self) -> None:
        """Pretend the band paused because the computer took nothing for 200 ms (firmware
        1.1.2 and later): one empty packet, then a "stream-dropped" notice for each stream
        that is on. The session goes on: streams and settings stay. (The library must NOT
        take this for a session end.)"""
        self.session_watch = True
        if not self.is_open:
            return
        self._empty_packet()
        self._say_dropped()

    def stop_reading(self, seconds: float) -> None:
        """Pretend this computer takes no data for `seconds` of band time (a program that is
        stuck), on a computer with no buffer of its own. The band behaves as firmware 1.1.3:
        after 200 ms it gives up stream frames (answers are kept), and once it has had data
        waiting for 5.2 seconds it ends the session, as end_usb_session() does. With no
        stream on nothing waits, and the session is never ended. When the time is over the
        computer reads again."""
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not seconds > 0:
            raise ValueError("stop_reading seconds is how long nothing is taken, a number above 0, got %r."
                             % (seconds,))
        self.session_watch = True
        if not self.is_open:
            return
        if self._deaf is None:
            self._deaf = dict(since=None, ended=False, kept=[], dropped=False)
        self._every("listen", seconds * 1000, self._read_again, once=True)

    def _give_up(self) -> None:
        """Nothing was taken for 5.2 seconds: the session ends."""
        if self._deaf is not None:
            self._deaf["ended"] = True
            self.end_usb_session()

    def _read_again(self) -> None:
        """stop_reading() is over: what the band kept arrives, after an empty packet if it paused."""
        deaf, self._deaf = self._deaf, None
        self._timers.pop("give-up", None)
        if deaf is None or deaf["since"] is None or deaf["ended"]:
            return
        if deaf["dropped"]:
            self._empty_packet()
        for frame in deaf["kept"]:
            self._emit(frame)
        if deaf["dropped"]:
            self._say_dropped()

    def _say_dropped(self) -> None:
        for bit in sorted(STREAM_BITS.values()):
            if self.active >> bit & 1:
                self._emit(codec.encode_notice("STREAM_DROPPED", bit))

    def _empty_packet(self) -> None:
        """One empty packet: a 00 on the pretend cable, and the sign the transport gives for it."""
        if self.via == "usb":
            self._to_client.push(b"\x00")
        try:
            self.on_session_hint()
        except Exception:
            print("Error in on_session_hint:", file=sys.stderr)
            traceback.print_exc()

    def power_cycle(self) -> None:
        """Pretend the band restarted by itself (its reset button, an empty battery that
        was charged again): the link is lost, the band's counter starts again at 0 and its
        clock is forgotten. Storage and the step count stay. The band can be found again
        at once. (band.restart() asks a band to do this; then the reason is "restart".)"""
        self._catch_up()
        self._restart_counter()
        self._shutdown("lost")

    def play_motion(self, source, seconds=None) -> None:
        """Play a motion instead of the made-up swing. source is one of:

        - a function of s, the seconds since this motion began. It returns a dict with any
          of ax ay az (in g) and gx gy gz (in degrees/second); what it leaves out is at
          rest (az 1, the others 0). Example: lambda s: dict(gz=200)
        - a list of such dicts: one for each sample the band takes, at whatever rate is on
        - one such dict: held steady

        seconds: how long it plays (left out: a list plays once to its end, a function or
        a single dict until you call play_motion again or stop_motion()). Motions queue
        up: a second call plays after the first has ended. Between and after them the band
        lies flat and still. A motion only moves on while the motion stream is on."""
        _queue(self._motions, _curve(source, seconds))
        self._directed = True

    def stop_motion(self) -> None:
        """Forget every motion from play_motion and go back to the made-up swing."""
        self._motions.clear()
        self._directed = False

    @property
    def moving(self) -> bool:
        """True while a motion from play_motion is playing or waiting."""
        return bool(self._motions)

    def set_loudness(self, level) -> None:
        """A steady loudness for the mic-level stream: the `level` a program reads at the
        band's default gain, 0 (silence) to 1 (as loud as it can measure). In a quiet room
        a real band reads about 0.5, with speech nearby about 0.7 to 0.8. None: back to
        the made-up voice that comes and goes."""
        if level is not None and (isinstance(level, bool) or not isinstance(level, (int, float))
                                  or not 0 <= level <= 1):
            raise ValueError("set_loudness takes a level from 0 (silence) to 1 (loudest), or None, got %r."
                             % (level,))
        self._loud = level

    def play_loudness(self, source, seconds=None, packets=None) -> None:
        """Play a loudness curve on the mic-level stream, on top of set_loudness. source:
        a function of s (seconds since it began) that returns a level 0..1, a list of
        levels (one for each reading; play_loudness([0.95]) is one clap), or one level.
        seconds and the queue work as in play_motion. Afterwards the loudness is the
        steady one again.

        packets: Opus packets (bytes, 20 ms each) for the audio stream. Each is sent once,
        in order, then the band goes back to audio_packets or silence. The pretend band
        cannot hear them: give a curve that fits the sound yourself."""
        _queue(self._loudness, _curve(source, seconds))
        self._packets.extend(bytes(p) for p in packets or [])

    def set_rssi(self, dbm) -> None:
        """The signal strength the band reports (link events and get_radio), in dBm:
        about -20 next to the computer, -90 far away. None: back to the made-up value
        around -55."""
        if dbm is not None and (isinstance(dbm, bool) or not isinstance(dbm, (int, float))
                                or not -127 <= dbm <= 20):
            raise ValueError("set_rssi takes dBm from -127 to 20 (for example -60), or None, got %r." % (dbm,))
        self._rssi_set = None if dbm is None else int(round(dbm))

    def set_activity(self, state="walk", cadence=None, steps=None) -> None:
        """What the wearer does: "still", "walk" or "run". cadence: steps a minute (left
        out: 0, 110, 165). The band then counts steps by its own clock, also while nothing
        is connected, like a real one. steps: set the lifetime step count to this number."""
        if state not in ACTIVITIES:
            raise ValueError("set_activity takes \"still\", \"walk\" or \"run\", got %r." % (state,))
        code, usual, motion = ACTIVITIES[state]
        if cadence is None:
            cadence = usual
        if isinstance(cadence, bool) or not isinstance(cadence, (int, float)) or not 0 <= cadence <= 255:
            raise ValueError("set_activity cadence is steps a minute, 0 to 255, got %r." % (cadence,))
        self._count_steps()  # what was walked so far, at the old pace
        if steps is not None:
            self.steps_total = int(steps)
        self._walk = dict(state=code, cadence=int(round(cadence)), motion=motion, since=self.now_ms, part=0.0)

    def after(self, seconds: float, action) -> None:
        """Call action() when `seconds` more have passed on the band's clock, for example
        sim.after(2, lambda: sim.tap("free-fall")). It runs while the link is open; if the
        link is down at that moment it runs right after the next connect."""
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0 or not callable(action):
            raise ValueError("after(seconds, action) takes a number of seconds, 0 or more, and a function.")
        self._plans.append([self.now_ms + seconds * 1000, action])
        self._plans.sort(key=lambda plan: plan[0])

    # ---- storage in a file (store_file) ----

    def _load_store(self) -> None:
        """Read the file. Anything that is not a store this band could hold is ignored:
        the band then starts empty, and the file is written over at the next change."""
        try:
            if os.path.getsize(self._store_path) > 4 * STORE_BYTES:
                return
            with open(self._store_path, encoding="utf-8") as f:
                saved = json.load(f)
            store = {int(key): bytes.fromhex(value) for key, value in saved.items()}
        except (OSError, ValueError, AttributeError, TypeError, RecursionError, MemoryError):
            return  # also a file nested so deep that reading it gives up
        fits = all(0 <= key <= 999 and 1 <= len(value) <= 232 for key, value in store.items())
        if fits and sum(self._cost(value) for value in store.values()) <= STORE_BYTES:
            self.store = store
            self._saved = dict(store)

    def _save_store(self) -> None:
        """Write the storage to its file when it has changed. The new file is written
        beside the old one under a name nobody can guess, readable by you only, and then
        put in its place in one step, so a reader never sees half a file."""
        if self._store_path is None or self.store == self._saved:
            return
        folder = os.path.dirname(os.path.abspath(self._store_path))
        handle, part = tempfile.mkstemp(dir=folder, prefix=".pretend-band-", suffix=".part")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as f:
                json.dump({str(key): bytes(value).hex() for key, value in sorted(self.store.items())}, f)
            os.replace(part, self._store_path)
        except BaseException:
            try:
                os.remove(part)
            except OSError:
                pass
            raise
        self._saved = dict(self.store)


def _current_task():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None  # not inside the event loop
