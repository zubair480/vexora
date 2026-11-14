"""Vexo band bridge. Owned by the be-band session (see CONTRACT.md).

One `VexoBridge` owns the connection to the team's band and turns it into:

- `ImuBatch` objects on `event_bus.bus.publish_imu`, about 10 times a second, `source="band"`;
- `device_connected` / `device_disconnected` events (with a reason, and the data gap on a reconnect);
- a `sensor_quality` event about once a second.

SDK calls (all from the unmodified kit, `reference/vexo-open-kit/`, see docs/api.md):
`Band.connect(number, transport, auto_reconnect=True)`, `band.number`, `band.name`, `band.info`
(`firmware`, `capabilities`, `features`), `band.motion_limits()` (`rates accel gyro both`),
`band.set_motion(accel_range=...)`, `band.get_motion()`, `band.on(name, handler)`,
`band.configure(motion=hz, sensors=..., battery=True)` (returns the streams actually on),
`band.set_led(...)`, `band.led_off()`, `band.disconnect()`.

Units, as the Python SDK delivers them (not changed here): `ImuSample.n` sample counter (None on
firmware 1.0), `t` band clock in MILLISECONDS, `time` epoch seconds, accel in g, gyro in deg/s.
An axis of a sensor that is switched off is NaN.

The band number comes only from `config.band_number()` (env VEXO_BAND). Without it `start()`
raises `config.BandNumberMissing` before anything is imported or connected.

Threads: the SDK calls our handlers on its own thread. Every handler only appends to one deque
and returns. A single "band-flush" thread does everything else (quality, publishing).

For the voice session (the LED is locked blue while the microphone streams, and LED commands are
refused with `denied`):

    bridge = vexo_bridge.get_bridge()
    with bridge.microphone() as band:      # raises BridgeError("not-connected") without a band
        text = speech.listen(band, 6)      # LED updates made meanwhile are queued...
    # ...and applied here, once audio is off.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional

from . import config, event_bus
from .models import Event, ImuBatch, ImuSample

log = logging.getLogger("beltguard.band")

TRANSPORTS = ("ble", "serial", "sim")
SENSORS = ("accel", "gyro", "both")
DEFAULT_HZ = 50
AUDIO_SAFE_HZ = {"both": 208, "accel": 416, "gyro": 416}   # docs/api.md: stay at or under this with audio on
FLUSH_SECONDS = 0.1
QUALITY_SECONDS = 1.0
STALE_SECONDS = 1.0
NAN = float("nan")


class BridgeError(RuntimeError):
    """`.code` is a short reason (the SDK's own code where there is one), `.message` says more."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class QualityTracker:
    """Loss, rate, saturation and staleness of one motion stream. Pure: no band, no threads.

    Feed every sample with `add`. `n` is the band's sample counter: a forward jump means lost
    samples; going backwards (or to 0) means the counter restarted (reconnect, new rate) and is
    not a loss. The effective rate is measured from the samples' own `time` over a short window,
    never taken from the configured rate. A silence longer than `stale_after` is a data gap with
    a start and an end, and the rate window restarts after it, so a gap is never averaged into
    "continuous" data.
    """

    def __init__(self, configured_hz: float = 0.0, stale_after: float = STALE_SECONDS,
                 window_s: float = 2.0) -> None:
        self.configured_hz = float(configured_hz)
        self.stale_after = stale_after
        self.window_s = window_s
        self.received = 0
        self.missing = 0
        self.saturated_samples = 0
        self.counter_resets = 0
        self.missing_known = True            # False once a sample has no counter (firmware 1.0)
        self._link_gap: Optional[dict] = None
        self.gaps: list[dict] = []           # {start, end (None while open), reason}
        self._last_n: Optional[int] = None
        self._last_time: Optional[float] = None      # sample time of the newest sample
        self._last_arrival: Optional[float] = None   # wall clock when it was fed
        self._window: deque = deque()                # (sample time, saturated)
        self._fresh = True

    # -- feeding
    def add(self, n: Optional[int], sample_time: float, saturated: bool = False,
            now: Optional[float] = None) -> None:
        now = sample_time if now is None else now
        open_gap = self._open_gap()
        if open_gap is not None:
            open_gap["end"] = sample_time
            self._new_segment()
        elif (not self._fresh and self._last_time is not None
              and sample_time - self._last_time > self.stale_after):
            self.gaps.append({"start": self._last_time, "end": sample_time, "reason": "no-data"})
            self._new_segment()
        if self._last_time is not None and sample_time < self._last_time:
            self._window.clear()                     # the clock stepped back: measure afresh
        if n is None:
            self.missing_known = False               # no counter: loss cannot be counted
        elif self._last_n is not None:
            if n > self._last_n:
                self.missing += n - self._last_n - 1
            elif n < self._last_n:                   # counter restarted: not a loss
                self.counter_resets += 1
                self._window.clear()
        self._fresh = False
        self._last_n = n
        self._last_time = sample_time
        self._last_arrival = now
        self.received += 1
        if saturated:
            self.saturated_samples += 1
        self._window.append((sample_time, saturated))
        while self._window and sample_time - self._window[0][0] > self.window_s:
            self._window.popleft()

    def gap_started(self, now: float, reason: str = "link-lost") -> dict:
        """The link went down. The gap starts at the last sample we really have."""
        gap = self._open_gap()
        if gap is None:
            gap = {"start": self._last_time if self._last_time is not None else now,
                   "end": None, "reason": reason}
            self.gaps.append(gap)
        self._link_gap = gap
        self._new_segment()
        return gap

    def gap_ended(self, now: float) -> Optional[dict]:
        """The link is back. The counter restarts at 0 on a new link. Returns the gap, also when
        the first samples of the new link arrived before this call and already closed it."""
        gap, self._link_gap = self._link_gap, None
        if gap is not None and gap["end"] is None:
            gap["end"] = now
            self._new_segment()
        return gap

    def _open_gap(self) -> Optional[dict]:
        return self.gaps[-1] if self.gaps and self.gaps[-1]["end"] is None else None

    def _new_segment(self) -> None:
        self._last_n = None
        self._fresh = True                   # the silence before this segment is already a gap
        self._window.clear()

    # -- reading
    def stale(self, now: Optional[float] = None) -> bool:
        if self._last_arrival is None or self._open_gap() is not None:
            return True
        now = time.time() if now is None else now
        return now - self._last_arrival > self.stale_after

    def effective_hz(self, now: Optional[float] = None) -> float:
        if self.stale(now) or len(self._window) < 2:
            return 0.0
        span = self._window[-1][0] - self._window[0][0]
        return round((len(self._window) - 1) / span, 2) if span > 0 else 0.0

    def completeness(self, now: Optional[float] = None) -> float:
        """Received / expected from the counter. Without a counter (firmware 1.0) loss is not
        countable: then it is the measured rate over the configured rate, flagged as not known."""
        if not self.missing_known:
            if not self.configured_hz:
                return 0.0
            return round(min(1.0, self.effective_hz(now) / self.configured_hz), 4)
        expected = self.received + self.missing
        return round(self.received / expected, 4) if expected else 0.0

    def snapshot(self, now: Optional[float] = None) -> dict:
        now = time.time() if now is None else now
        return {
            "configured_hz": self.configured_hz,
            "effective_hz": self.effective_hz(now),
            "missing_samples": self.missing,
            "missing_samples_known": self.missing_known,
            "completeness": self.completeness(now),
            "saturated": any(s for _, s in self._window),
            "stale": self.stale(now),
            "received_samples": self.received,
            "saturated_samples": self.saturated_samples,
            "counter_resets": self.counter_resets,
            "gaps": [dict(g) for g in self.gaps[-20:]],
        }


def _default_connect(number: int, transport: str, auto_reconnect: bool = True) -> Any:
    config.add_kit_to_path()
    from vexo_open.sync import Band   # the kit, unmodified
    return Band.connect(number, transport, auto_reconnect=auto_reconnect)


def _num(value: Any) -> float:
    return NAN if value is None else float(value)


class VexoBridge:
    def __init__(self, connect: Optional[Callable[..., Any]] = None) -> None:
        self._connect = connect or _default_connect   # tests pass a fake here
        self._lock = threading.RLock()
        self._drain_lock = threading.Lock()   # separate: stop() joins the flush thread under _lock
        # ImuSample, or (event name, message, when) from the SDK thread. Bounded: if the flush
        # thread ever stalls the oldest entries go, and show up as lost samples (a jump in n).
        self._inbox: deque = deque(maxlen=50000)
        self._band: Any = None
        self._thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self.tracker = QualityTracker()
        self._connected = False
        self._reconnecting = False
        self._down_published = True
        self._transport: Optional[str] = None
        self._band_name: Optional[str] = None
        self._firmware: Optional[str] = None
        self._capabilities: list[str] = []
        self._features: list[str] = []
        self._battery: Optional[dict] = None
        self._sensors: Optional[str] = None
        self._limits: Optional[dict] = None
        self._accel_limit = 2.0              # g; the band clips at its range (kit default 2 g)
        self._gyro_limit = 2000.0            # deg/s
        self._last_error: Optional[str] = None
        self._last_notice: Optional[dict] = None
        self._mic_depth = 0
        self._pending_led: Optional[dict] = None

    # ------------------------------------------------------------------ connect / disconnect
    def start(self, transport: str = "ble", motion_hz: int = DEFAULT_HZ, sensors: str = "both",
              accel_range_g: Optional[int] = 8) -> dict:
        """Connect to the team's band and start the motion stream. Blocks for a few seconds.

        `motion_hz` is clamped to what this link allows for the chosen sensors
        (`motion_limits()`); with the microphone on stay at or under 208 Hz with both sensors.
        `accel_range_g` (2/4/8/16, None = leave the band's 2 g default): hand tools clip at 2 g.
        """
        number = config.band_number()        # raises BandNumberMissing: nothing else happens
        if transport not in TRANSPORTS:
            raise ValueError("transport must be one of %s" % (TRANSPORTS,))
        if sensors not in SENSORS:
            raise ValueError("sensors must be one of %s" % (SENSORS,))
        if isinstance(motion_hz, bool) or not isinstance(motion_hz, int) or motion_hz <= 0:
            raise ValueError("motion_hz must be a positive whole number")
        with self._lock:
            if self._band is not None:
                raise BridgeError("already-running", "The band is already connected. Disconnect first.")
            self._last_error = None
            try:
                band = self._connect(number, transport, auto_reconnect=True)
            except Exception as exc:         # VexoError has .code; never try another number
                self._last_error = str(exc)
                raise BridgeError(getattr(exc, "code", "connect-failed"), str(exc)) from exc
            try:
                self._setup(band, number, transport, motion_hz, sensors, accel_range_g)
            except Exception as exc:
                self._last_error = str(exc)
                self._halt_thread()
                try:
                    band.disconnect()
                except Exception:
                    pass
                self._band = None
                self._connected = False
                if isinstance(exc, BridgeError):
                    raise
                raise BridgeError(getattr(exc, "code", "setup-failed"), str(exc)) from exc
        return self.status()

    def _setup(self, band: Any, number: int, transport: str, motion_hz: int, sensors: str,
               accel_range_g: Optional[int]) -> None:
        if band.number != number:
            raise BridgeError("wrong-band", "A different band answered. Not using it.")
        info = band.info
        caps = list(info.get("capabilities") or [])
        self._capabilities, self._features = caps, list(info.get("features") or [])
        self._firmware, self._band_name = info.get("firmware"), band.name
        self._transport = transport
        # capabilities first: only ask for sensors this band has
        have = [s for s in ("accel", "gyro") if s in caps]
        if not have:
            raise BridgeError("not-available", "This band reports no working motion sensor.")
        if sensors == "both" and len(have) == 1:
            sensors = have[0]
        elif sensors != "both" and sensors not in have:
            raise BridgeError("not-available", "This band's %s sensor is not working." % sensors)
        limits = band.motion_limits()
        self._limits = {k: limits.get(k) for k in ("rates", "accel", "gyro", "both", "transport")}
        cap = limits.get(sensors)
        if cap is None:                      # firmware 1.0: both sensors only
            sensors, cap = "both", limits.get("both")
        rates = sorted(r for r in (limits.get("rates") or []) if r <= min(motion_hz, cap or motion_hz))
        if not rates:
            raise BridgeError("bad-arg", "No motion rate at or under %s Hz on this link." % motion_hz)
        rate = rates[-1]
        if accel_range_g is not None:
            try:
                band.set_motion(accel_range=accel_range_g)
            except Exception as exc:
                self._last_error = "accel range not set: %s" % exc
        try:
            applied = band.get_motion()
            self._accel_limit = float(applied.get("accel_range") or 2)
            self._gyro_limit = float(applied.get("gyro_range") or 2000)
        except Exception:
            pass
        self.tracker = QualityTracker()
        self._inbox.clear()
        self._band = band
        self._battery = None
        band.on("motion", self._on_motion)
        for name in ("battery", "notice", "disconnect", "reconnecting", "reconnected"):
            band.on(name, self._enqueue(name))
        self._stop_flag = threading.Event()  # one per flush thread
        self._thread = threading.Thread(target=self._run, args=(self._stop_flag,), name="band-flush",
                                        daemon=True)
        self._thread.start()
        wanted: dict[str, Any] = {"motion": rate, "sensors": sensors}
        if "battery" in caps:
            wanted["battery"] = True
        streams = band.configure(**wanted)
        self.tracker.configured_hz = float(streams["motion"])   # what the band granted
        self._sensors = sensors
        self._connected, self._reconnecting, self._down_published = True, False, False
        self._publish("device_connected", reason="connected")

    def stop(self) -> dict:
        """Disconnect. Safe to call when nothing is connected."""
        with self._lock:
            band = self._band
            if band is None:
                return self.status()
            try:
                band.disconnect()            # also stops a reconnect in progress
            except Exception as exc:
                self._last_error = str(exc)
            self._halt_thread()
            self._drain()                    # whatever arrived last, including "disconnect"
            self._link_down("closed", final=True)
            self._band = None
            self._mic_depth, self._pending_led = 0, None
        return self.status()

    def _halt_thread(self) -> None:
        self._stop_flag.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    # ------------------------------------------------------------------ SDK thread: enqueue only
    def _on_motion(self, m: Any) -> None:
        self._inbox.append(ImuSample(m.n, m.t, m.time, _num(m.ax), _num(m.ay), _num(m.az),
                                     _num(m.gx), _num(m.gy), _num(m.gz)))

    def _enqueue(self, name: str) -> Callable[[Any], None]:
        def handler(message: Any) -> None:
            self._inbox.append((name, message, time.time()))
        return handler

    # ------------------------------------------------------------------ flush thread
    def _run(self, stop: threading.Event) -> None:
        next_quality = time.time() + QUALITY_SECONDS
        while not stop.wait(FLUSH_SECONDS):
            try:
                self._drain()
                if time.time() >= next_quality:
                    next_quality = time.time() + QUALITY_SECONDS
                    self._publish_quality()
            except Exception:
                log.exception("band flush failed")

    def _drain(self) -> None:
        """Move everything the handlers queued onto the bus, in arrival order."""
        with self._drain_lock:
            samples: list[ImuSample] = []
            while True:
                try:
                    item = self._inbox.popleft()
                except IndexError:
                    break
                if isinstance(item, ImuSample):
                    samples.append(item)
                    continue
                self._flush(samples)         # samples before the event belong before it
                samples = []
                self._handle(*item)
            self._flush(samples)

    def _flush(self, samples: list[ImuSample]) -> None:
        if not samples:
            return
        now = time.time()
        for s in samples:
            self.tracker.add(s.n, s.time, self._saturated(s), now)
        event_bus.bus.publish_imu(ImuBatch(samples, self.tracker.configured_hz,
                                           self.tracker.effective_hz(now), "band"))

    def _saturated(self, s: ImuSample) -> bool:
        """The band has no clipping flag: a clipped axis simply reads at its range (NaN compares False)."""
        a, g = 0.98 * self._accel_limit, 0.98 * self._gyro_limit
        return (any(abs(v) >= a for v in (s.ax, s.ay, s.az))
                or any(abs(v) >= g for v in (s.gx, s.gy, s.gz)))

    def _handle(self, name: str, message: Any, when: float) -> None:
        get = message.get if hasattr(message, "get") else (lambda k, d=None: getattr(message, k, d))
        if name == "battery":
            self._battery = {"percent": get("percent"), "state": get("state"), "charging": get("charging")}
        elif name == "notice":
            self._last_notice = {"kind": get("kind"), "arg": get("arg"), "time": when}
            log.warning("band notice: %s", get("kind"))
        elif name == "reconnecting":
            self._connected, self._reconnecting = False, True
            self.tracker.gap_started(when, "link-%s" % (get("reason") or "lost"))
            self._publish("device_disconnected", reason=get("reason") or "lost", reconnecting=True,
                          retry_seconds=get("seconds"))
        elif name == "reconnected":
            gap = self.tracker.gap_ended(when)
            self._connected, self._reconnecting, self._down_published = True, False, False
            failed = get("failed") or []
            if failed:
                self._last_error = "not restored after reconnect: %s" % ", ".join(map(str, failed))
            try:                             # the library sets the streams again: read what is on
                self.tracker.configured_hz = float(self._band.streams["motion"])
            except Exception:
                pass
            self._publish("device_connected", reason="reconnected", gap=dict(gap) if gap else None,
                          gap_seconds=get("seconds"), attempts=get("attempts"), not_restored=list(failed))
        elif name == "disconnect":
            reason = get("reason") or "closed"
            if get("reconnect"):
                reason = "%s (reconnect %s)" % (reason, get("reconnect"))
            self._link_down(reason, final=True)
            # the SDK has let go of the band for good: so do we, and connect can be called again
            self._stop_flag.set()
            self._thread = None
            self._band = None
            self._mic_depth, self._pending_led = 0, None

    def _link_down(self, reason: str, final: bool) -> None:
        was_reconnecting = self._reconnecting
        self._connected, self._reconnecting = False, False
        self.tracker.gap_started(time.time(), "link-%s" % reason.split(" ")[0])
        if not self._down_published or was_reconnecting:
            self._down_published = True
            self._publish("device_disconnected", reason=reason, reconnecting=False)

    # ------------------------------------------------------------------ publishing
    def _publish(self, kind: str, **extra: Any) -> None:
        shift = config.current_shift()
        payload = {"band_name": self._band_name, "firmware": self._firmware,
                   "battery": (self._battery or {}).get("percent"), "transport": self._transport,
                   "reason": extra.pop("reason", None), **extra}
        event_bus.bus.publish(Event(kind, payload, "band", shift["shift_id"], shift["worker_id"]))

    def _publish_quality(self) -> None:
        snap = self.tracker.snapshot()
        shift = config.current_shift()
        payload = {k: snap[k] for k in ("configured_hz", "effective_hz", "missing_samples", "completeness",
                                        "saturated", "stale", "missing_samples_known")}
        payload.update(connected=self._connected, reconnecting=self._reconnecting,
                       dropped_events=self._dropped_events())
        event_bus.bus.publish(Event("sensor_quality", payload, "band", shift["shift_id"], shift["worker_id"]))

    def _dropped_events(self) -> int:
        try:
            return int(self._band.dropped_events)   # readings the SDK dropped because handlers lagged
        except Exception:
            return 0

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        snap = self.tracker.snapshot()
        return {
            "connected": self._connected,
            "reconnecting": self._reconnecting,
            "band_name": self._band_name,
            "firmware": self._firmware,
            "battery": (self._battery or {}).get("percent"),
            "battery_state": (self._battery or {}).get("state"),
            "transport": self._transport,
            "configured_hz": snap["configured_hz"],
            "effective_hz": snap["effective_hz"],
            "missing_samples": snap["missing_samples"],
            "missing_samples_known": snap["missing_samples_known"],
            "completeness": snap["completeness"],
            "stale": snap["stale"] or not self._connected,
            "source": "band" if self._band is not None else None,
            "saturated": snap["saturated"],
            "sensors": self._sensors,
            "received_samples": snap["received_samples"],
            "gaps": snap["gaps"],
            "capabilities": list(self._capabilities),
            "features": list(self._features),
            "limits": self._limits,
            "accel_range_g": self._accel_limit,
            "dropped_events": self._dropped_events(),
            "microphone_on": self._mic_depth > 0,
            "led_queued": self._pending_led is not None,
            "last_notice": self._last_notice,
            "last_error": self._last_error,
        }

    # ------------------------------------------------------------------ LED and microphone
    @property
    def band(self) -> Any:
        """The connected SDK `Band`, or None. Use `microphone()` around any audio streaming."""
        return self._band

    @contextmanager
    def microphone(self) -> Iterator[Any]:
        """Hold this while the microphone (audio or mic_level) is on. Yields the SDK band.

        LED commands sent through `set_led` / `led_off` meanwhile are kept (last one wins) and
        applied when the block ends.
        """
        band = self._band
        if band is None or not self._connected:
            raise BridgeError("not-connected", "No band connected.")
        if self.tracker.configured_hz > AUDIO_SAFE_HZ.get(self._sensors or "both", 208):
            log.warning("motion at %s Hz with audio on: the kit recommends at most %s",
                        self.tracker.configured_hz, AUDIO_SAFE_HZ.get(self._sensors or "both"))
        with self._lock:
            self._mic_depth += 1
        try:
            yield band
        finally:
            with self._lock:
                self._mic_depth = max(0, self._mic_depth - 1)
            if self._mic_depth == 0:
                self._apply_pending_led()

    def set_led(self, colour: str, brightness: float = 1.0, pattern: str = "solid",
                period: int = 1000) -> dict:
        """Same arguments as the SDK's `band.set_led`. Queued while the microphone is on."""
        return self._led({"colour": colour, "brightness": brightness, "pattern": pattern, "period": period})

    def led_off(self) -> dict:
        return self._led({"off": True})

    def _led(self, command: dict) -> dict:
        band = self._band
        if band is None:
            raise BridgeError("not-connected", "No band connected.")
        with self._lock:
            if self._mic_depth > 0 or self._audio_on(band):
                self._pending_led = command
                return {"applied": False, "queued": True}
            try:
                self._send_led(band, command)
            except Exception as exc:
                if getattr(exc, "code", None) == "denied":    # the microphone holds the LED
                    self._pending_led = command
                    return {"applied": False, "queued": True}
                raise BridgeError(getattr(exc, "code", "led-failed"), str(exc)) from exc
            self._pending_led = None
            return {"applied": True, "queued": False}

    @staticmethod
    def _audio_on(band: Any) -> bool:
        try:
            streams = band.streams
            return bool(streams.get("audio") or streams.get("mic_level"))
        except Exception:
            return False

    @staticmethod
    def _send_led(band: Any, command: dict) -> None:
        if command.get("off"):
            band.led_off()
        else:
            band.set_led(command["colour"], brightness=command["brightness"],
                         pattern=command["pattern"], period=command["period"])

    def _apply_pending_led(self) -> None:
        with self._lock:
            command, band = self._pending_led, self._band
            if command is None or band is None:
                return
            try:
                self._send_led(band, command)
                self._pending_led = None
            except Exception as exc:         # still locked or link down: keep it for the next try
                log.warning("queued LED command not applied yet: %s", exc)


_bridge: Optional[VexoBridge] = None


def get_bridge() -> VexoBridge:
    """The one bridge of this process."""
    global _bridge
    if _bridge is None:
        _bridge = VexoBridge()
    return _bridge
