"""Band 07 bridge (S2 band-backend). The ONLY code in BeltGuard that talks to the band.

Uses the official Vexo Open Python library (the plain/sync form: the library runs its own
asyncio loop on a background thread and our handlers on a second thread). Our handlers only
put things in queues; a worker thread here does the per-sample maths (bias, lost samples,
detector, raw recording) and hands ~10 small messages a second to the server via `post`.
"""
from __future__ import annotations

import asyncio
import collections
import importlib
import itertools
import json
import math
import queue
import sys
import threading
import time
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
KIT = ROOT / "vexo-open-kit"
for p in (str(BASE), str(KIT / "python"), str(KIT / "examples" / "python")):
    if p not in sys.path:
        sys.path.insert(0, p)

from vexo_open.sync import Band, VexoError  # noqa: E402  (the kit's library, no install needed)

BAND = 7                               # the team's band number (Vexo-HACK-07): the one place it is written
GYRO_BIAS = (0.45, -1.82, 0.55)        # deg/s, measured on band 07 at rest (2026-11-14)
RAW_DIR = ROOT / "data" / "raw"
SAMPLE_SPEECH = KIT / "dashboard" / "speech" / "sample-speech.opus"
LABELS = {"walking", "gesture", "tool", "idle", "none"}
_UIDS = itertools.count(1)   # every source gets its own id: the server shows only the live one
SAMPLE_SINK = None           # set by the server: callable(row tuple) that records each real reading in SQLite


def log(*parts) -> None:
    print("[band]", *parts, flush=True)


def _import_speech():
    """The kit's Deepgram helper. Importing it installs the kit's one-sentence error hooks;
    keep the server's normal tracebacks."""
    hooks = (sys.excepthook, threading.excepthook)
    try:
        import speech  # vexo-open-kit/examples/python/speech.py
        return speech
    finally:
        sys.excepthook, threading.excepthook = hooks


# ---------------------------------------------------------------------------------------
# Detector slot: S3's detector.py when present (reloaded when it changes), else a tiny stub.
# ---------------------------------------------------------------------------------------
class StubDetector:
    """Placeholder until S3's detector.py exists: windowed RMS with hysteresis."""

    def __init__(self, rate_hz: float = 50.0, config: dict | None = None) -> None:
        self.rate = rate_hz
        self.win = collections.deque()
        self.state, self.conf = "idle", 0.0
        self.features: dict = {}
        self.last_eval = None
        self.hot_since = None
        self.cold_since = None
        self.active_start = None

    def update(self, s) -> list[dict]:
        mag = math.sqrt(s.ax * s.ax + s.ay * s.ay + s.az * s.az)
        gmag = math.sqrt(s.gx * s.gx + s.gy * s.gy + s.gz * s.gz)
        self.win.append((s.t, mag, gmag))
        while self.win and s.t - self.win[0][0] > 1.0:
            self.win.popleft()
        if self.last_eval is not None and s.t - self.last_eval < 0.25:
            return []
        self.last_eval = s.t
        mags = [w[1] for w in self.win]
        mean = sum(mags) / len(mags)
        acc_rms = math.sqrt(sum((m - mean) ** 2 for m in mags) / len(mags))
        gyro_rms = math.sqrt(sum(w[2] ** 2 for w in self.win) / len(self.win))
        self.features = {"acc_rms": round(acc_rms, 4), "gyro_rms": round(gyro_rms, 2), "hf_ratio": None}
        hot = acc_rms > 0.25 and gyro_rms < 60
        events: list[dict] = []
        prev = self.state
        if hot:
            self.cold_since = None
            self.hot_since = self.hot_since or s.t
            if self.state != "tool_active":
                if s.t - self.hot_since >= 2.0:
                    self.state, self.conf = "tool_active", min(1.0, 0.6 + acc_rms)
                    self.active_start = self.hot_since
                    events.append({"kind": "tool_start", "t": self.active_start, "confidence": self.conf})
                else:
                    self.state, self.conf = "candidate", 0.5
        else:
            self.hot_since = None
            if self.state == "tool_active":
                self.cold_since = self.cold_since or s.t
                if s.t - self.cold_since >= 1.0:
                    trig = self.cold_since - self.active_start
                    events.append({"kind": "tool_stop", "t_start": self.active_start, "t_end": self.cold_since,
                                   "trigger_s": trig, "confidence": self.conf,
                                   "status": "confirmed" if self.conf >= 0.75 else "unresolved"})
                    self.state = "idle"
            else:
                if acc_rms > 0.08 and gyro_rms > 15:
                    self.state, self.conf = "walking", 0.5
                elif gyro_rms > 150:
                    self.state, self.conf = "gesture", 0.5
                else:
                    self.state, self.conf = "idle", 0.7
        if self.state != prev:
            events.append({"kind": "activity", "state": self.state, "confidence": self.conf})
        return events

    def snapshot(self) -> dict:
        return {"state": self.state, "confidence": round(self.conf, 3), "features": self.features}


class DetectorSlot:
    def __init__(self, rate_hz: float) -> None:
        self.rate = rate_hz
        self.key = None
        self.module = None
        self.det = None
        self.kind = "stub"
        self.load(force=True)

    def _key(self):
        files = [BASE / "detector.py", BASE / "detector_config.json"]
        return tuple(f.stat().st_mtime if f.exists() else None for f in files)

    def load(self, force: bool = False, rate_hz: float | None = None) -> bool:
        if rate_hz:
            force = force or rate_hz != self.rate
            self.rate = rate_hz
        key = self._key()
        if not force and key == self.key:
            return False
        self.key = key
        if key[0] is not None:
            try:
                self.module = importlib.reload(self.module) if self.module else importlib.import_module("detector")
                self.det = self.module.ToolDetector(rate_hz=self.rate)
                self.kind = "detector.py"
                log("detector: using S3 detector.py at", self.rate, "Hz")
                return True
            except Exception as e:  # half-written file: keep running on the stub
                log("detector.py not usable yet (%s: %s); using the stub" % (type(e).__name__, e))
        self.det = StubDetector(rate_hz=self.rate)
        self.kind = "stub"
        return True

    def sample(self, t, n, ax, ay, az, gx, gy, gz):
        cls = getattr(self.module, "MotionSample", None) if self.kind == "detector.py" else None
        if cls is not None:
            try:
                return cls(t=t, n=n, ax=ax, ay=ay, az=az, gx=gx, gy=gy, gz=gz)
            except TypeError:
                pass
        return types.SimpleNamespace(t=t, n=n, ax=ax, ay=ay, az=az, gx=gx, gy=gy, gz=gz)


# ---------------------------------------------------------------------------------------
class BandBridge:
    def __init__(self, post, sim: bool = False, on_ready=None, transport: str = "ble") -> None:
        self.post = post                  # thread-safe: hands a dict to the server's queue
        self.sim = sim
        # transport: "ble" (Bluetooth) or "serial" (the USB cable). The kit's serial finder only
        # takes ports whose USB strings say "Vexo Open", so Bluetooth COM ports are never opened.
        self.transport = "serial" if transport == "serial" else "ble"
        self.link = "sim" if sim else ("usb" if self.transport == "serial" else "ble")
        # Standby: search for the real band while another source (the sim) stays on screen.
        # on_ready(bridge) -> bool is called on this thread the moment the band is connected;
        # False means another link won the race, so this one lets go of the band.
        self.on_ready = on_ready
        self.standby = on_ready is not None
        self.src = "sim" if sim else "band"
        self.uid = next(_UIDS)
        self.session_id = None
        self.band = None
        self.director = None
        self.rate = 50
        self.sensors = "both"
        self.limits: dict = {}
        self.info: dict = {}
        self.state = "disconnected"
        self.detail = ""
        self.mic_on = False
        self.label = "none"
        self.lost_total = 0
        self.battery = None
        self.rssi = None
        self.led = None                   # last colour we set
        self._led_want = None
        self._ears = None
        self._audio_handler = None
        self.voice_wanted = False          # always-on voice (Python links): mic + Deepgram, kept alive
        self.voice_restarts = 0
        self.audio_demoted = False         # 416 both + audio lost samples: dropped to 208
        self._last_n = None
        self._wall_offset = None
        self._q: queue.SimpleQueue = queue.SimpleQueue()
        self._stop = threading.Event()
        self._det_rate = None
        self.detector = DetectorSlot(self.rate)
        self._raw = None
        self._raw_path = None
        self._lock = threading.RLock()

    # ---- status ------------------------------------------------------------------------
    def status(self) -> dict:
        return {"state": self.state, "band": BAND, "uid": self.uid, "link": self.link, "transport": self.link,
                "rate": self.rate, "sensors": self.sensors,
                "lost_total": self.lost_total, "battery": self.battery, "rssi": self.rssi,
                "mic": self.mic_on, "label": self.label, "led": self.led, "limits": self.limits,
                "detector": self.detector.kind, "detail": self.detail, "simulated": self.sim,
                "raw_file": self._raw_path.name if self._raw_path else None}

    def _conn(self, state: str, detail: str = "") -> None:
        self.state, self.detail = state, detail
        kind = "band_probe" if self.standby else "connection"
        self.post({"kind": kind, "source": self.src, **self.status()})

    # ---- lifecycle ---------------------------------------------------------------------
    def start(self) -> None:
        threading.Thread(target=self._run, name="beltguard-band", daemon=True).start()

    def _run(self) -> None:
        usb = self.transport == "serial"
        name = "Vexo-HACK-%02d" % BAND
        self._conn("connecting", "Connecting to %s%s" % (name, " (SIMULATED)" if self.sim else " over USB" if usb else ""))
        # real band: keep looking (tab closing, double-tap, cable). In standby the sim is on screen, so
        # keep trying in the background until the band turns up or someone switches the source.
        attempts = 1 if self.sim else (10 ** 9 if self.standby else 24)
        cap = "" if self.standby else "/%d" % attempts
        retry_codes = ("no-port", "port-failed", "timeout", "busy", "not-connected") if usb else \
                      ("not-found-nearby", "timeout", "busy")
        band = None
        for i in range(attempts):
            if self._stop.is_set():
                return
            try:
                if self.sim:
                    from vexo_open import audio
                    from vexo_open.transports.sim import SimTransport
                    voice = audio.ogg_packets(str(SAMPLE_SPEECH)) if SAMPLE_SPEECH.exists() else None
                    band = Band.connect(BAND, transport=SimTransport(BAND, auto_tap=False, audio_packets=voice))
                elif usb:
                    band = Band.connect(BAND, transport="serial", auto_reconnect=True)
                else:
                    band = Band.connect(BAND, auto_reconnect=True)
                break
            except VexoError as e:
                code = getattr(e, "code", "")
                retry = code in retry_codes and i < attempts - 1        # wrong-band etc.: stop for good
                if i < 3 or (i + 1) % 20 == 0:
                    log("%s connect attempt %d failed (%s): %s" % (self.link, i + 1, code or "?", str(e)[:160]))
                if not retry:
                    self._conn("gave-up" if self.standby else "error", str(e))
                    return
                self._conn("connecting", ("No band on USB yet (try %d%s)" % (i + 1, cap)) if usb else
                           ("Band %02d not found yet (try %d%s). Close any other tab or program connected to it, "
                            "then double-tap the band." % (BAND, i + 1, cap)))
            except Exception as e:  # bluetooth/serial stack problems etc.
                log("%s connect attempt %d failed: %r" % (self.link, i + 1, e))
                if i >= attempts - 1:
                    self._conn("error", "%s: %s" % (type(e).__name__, e))
                    return
                self._conn("connecting", "%s problem (%s), retrying (try %d%s)" % (
                    "USB" if usb else "Bluetooth", type(e).__name__, i + 1, cap))
            self._stop.wait(3 if usb else 5)
        if band is None:
            return
        if self._stop.is_set():                    # stopped while connecting: let go of the band
            band.disconnect()
            return
        if band.number != BAND:            # Band.connect checks this too; never touch another band
            band.disconnect()
            self._conn("error", "Connected band reports number %s, not %d. Disconnected." % (band.number, BAND))
            return
        self.band = band
        if self.standby:                   # found it: take over from the source on screen
            won = False
            try:
                won = self.on_ready(self) is not False
            except Exception as e:
                log("on_ready failed:", repr(e))
            if not won:                    # the other link got there first: let go
                self.band = None
                try:
                    band.disconnect()
                except Exception:
                    pass
                return
            self.standby = False
        try:
            info = band.info
            self.info = {k: getattr(info, k, None) for k in ("number", "protocol", "firmware", "features",
                                                               "hardware", "capabilities")}
            lim = band.motion_limits()
            self.limits = {k: getattr(lim, k, None) for k in ("rates", "accel", "gyro", "both", "transport")}
            log("connected", band.name, "info", self.info)
            log("motion_limits()", self.limits)
            band.on("motion", self._q.put)                       # the hot path: just queue it
            band.on("double-tap", lambda e: self.post({"kind": "band_gesture", "gesture": "double-tap",
                                                        "source": self.src, "uid": self.uid}))
            band.on("free-fall", lambda e: self.post({"kind": "band_gesture", "gesture": "free-fall",
                                                       "source": self.src, "uid": self.uid}))
            band.on("battery", self._on_battery)
            band.on("link", self._on_link)
            band.on("disconnect", lambda e: self._conn("disconnected", "link closed: %s" % getattr(e, "reason", "?")))
            band.on("reconnecting", lambda e: self._conn("connecting", "link lost, reconnecting"))
            band.on("reconnected", self._on_reconnected)
            band.on("notice", lambda e: self.post({"kind": "notice", "notice": getattr(e, "kind", "?"),
                                                    "arg": getattr(e, "arg", None), "source": self.src}))
            band.on("stream-taken", lambda e: self.post({"kind": "notice", "notice": "stream-taken",
                                                          "message": getattr(e, "message", ""), "source": self.src}))
            self._open_raw()
            threading.Thread(target=self._worker, name="beltguard-motion", daemon=True).start()
            band.set_motion(accel_range=16, gyro_range=2000)      # tool vibration clips at the 2 g default
            streams = dict(motion=50, sensors="both", events=True, battery=True)
            if band.transport_kind == "ble":
                streams["link"] = True
            band.configure(**streams)
            self.rate = 50
            if self.sim:
                from sim import SimDirector
                self.director = SimDirector(band.transport, self.post)
                self.director.start()
            self._conn("simulated" if self.sim else "connected",
                       "%s · accel 16 g · gyro 2000 dps · %d Hz both" % (band.name, self.rate))
        except Exception as e:
            log("setup failed:", repr(e))
            self._conn("error", "setup failed: %s" % e)

    def stop(self) -> None:
        self._stop.set()
        self.voice_wanted = False
        if self.director:
            self.director.stop()
        try:
            if self.mic_on:
                self.set_mic(False)
        except Exception:
            pass
        if self.band is not None:
            try:
                self.band.disconnect()
            except Exception:
                pass
        self.band = None
        self._close_raw()
        self._conn("disconnected", "stopped")

    # ---- small handlers (library handler thread) ---------------------------------------
    def _on_battery(self, e) -> None:
        self.battery = getattr(e, "percent", None)

    def _on_link(self, e) -> None:
        self.rssi = getattr(e, "rssi", None)

    def _on_reconnected(self, e) -> None:
        self._last_n = None
        self._conn("connected", "reconnected after %s tries" % getattr(e, "attempts", "?"))

    # ---- motion worker -----------------------------------------------------------------
    def _worker(self) -> None:
        acc, gyr = [], []
        t0 = t1 = n0 = None
        last_flush = last_act = last_check = last_raw_flush = time.monotonic()
        bias = (0.0, 0.0, 0.0) if self.sim else GYRO_BIAS
        while not self._stop.is_set():
            try:
                m = self._q.get(timeout=0.05)
            except queue.Empty:
                m = None
            if m is not None:
                t = m.t / 1000.0                                  # band clock, seconds
                n = m.n
                if n is not None:
                    if self._last_n is not None and n > self._last_n + 1:
                        self.lost_total += n - self._last_n - 1
                    self._last_n = n
                if getattr(m, "time", None):
                    self._wall_offset = m.time - t
                ax, ay, az = (m.ax or 0.0), (m.ay or 0.0), (m.az or 0.0)
                has_gyro = m.gx is not None
                gx = (m.gx - bias[0]) if has_gyro else 0.0
                gy = (m.gy - bias[1]) if has_gyro else 0.0
                gz = (m.gz - bias[2]) if has_gyro else 0.0
                if self._det_rate:                                  # rate changed: fresh detector
                    self.detector.load(force=True, rate_hz=self._det_rate)
                    self._det_rate = None
                try:
                    for ev in self.detector.det.update(self.detector.sample(t, n, ax, ay, az, gx, gy, gz)):
                        self._detector_event(ev)
                except Exception as e:
                    log("detector error:", repr(e))
                    self.detector.load(force=True)
                if t0 is None:
                    t0, n0 = t, n
                t1 = t
                acc.append([round(ax, 4), round(ay, 4), round(az, 4)])
                gyr.append([round(gx, 2), round(gy, 2), round(gz, 2)] if has_gyro else None)
                if self._raw:
                    self._raw.write(json.dumps({"t": round(t, 5), "n": n, "time": round(getattr(m, "time", 0) or 0, 4),
                                                "ax": ax, "ay": ay, "az": az,
                                                "gx": gx if has_gyro else None, "gy": gy if has_gyro else None,
                                                "gz": gz if has_gyro else None, "rate": self.rate,
                                                "label": self.label, "src": self.src}) + "\n")
                sink = SAMPLE_SINK
                if sink is not None and self.src == "band":     # real readings only, never sim or test
                    sink((getattr(m, "time", None) or time.time(), self.src, self.session_id, m.t, n,
                          m.ax, m.ay, m.az, m.gx, m.gy, m.gz, self.rate, self.label))
            now = time.monotonic()
            if acc and now - last_flush >= 0.1:
                self.post({"kind": "motion", "source": self.src, "uid": self.uid, "rate": self.rate, "sensors": self.sensors,
                           "t0": t0, "t1": t1, "dt": 1.0 / self.rate, "n0": n0, "lost_total": self.lost_total,
                           "accel": acc, "gyro": gyr})
                acc, gyr, t0, t1, n0 = [], [], None, None, None
                last_flush = now
            if now - last_act >= 0.25:
                try:
                    snap = self.detector.det.snapshot()
                except Exception:
                    snap = {"state": "unresolved", "confidence": 0.0, "features": {}}
                self.post({"kind": "activity", "source": self.src, "uid": self.uid, **snap})
                last_act = now
            if now - last_check >= 3.0:
                self.detector.load()                               # S3 edited detector.py: hot reload
                last_check = now
            if self._raw and now - last_raw_flush >= 2.0:
                self._raw.flush()
                last_raw_flush = now

    def _detector_event(self, ev: dict) -> None:
        ev = dict(ev)
        off = self._wall_offset
        if off is not None:
            if "t" in ev:
                ev["wall"] = ev["t"] + off
            if "t_start" in ev:
                ev["wall_start"] = ev["t_start"] + off
            if "t_end" in ev:
                ev["wall_end"] = ev["t_end"] + off
        self.post({"kind": "detector", "source": self.src, "uid": self.uid, "event": ev})

    # ---- raw recording for S3 ----------------------------------------------------------
    def _open_raw(self) -> None:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        self._raw_path = RAW_DIR / ("%s-%s.jsonl" % (time.strftime("%Y%m%d-%H%M%S"), self.link))
        self.session_id = self._raw_path.stem          # the same id in SQLite motion_samples and the jsonl name
        self._raw = open(self._raw_path, "a", encoding="utf-8", buffering=1 << 16)
        log("recording raw motion to", self._raw_path)

    def _close_raw(self) -> None:
        if self._raw:
            try:
                self._raw.close()
            except Exception:
                pass
        self._raw = None

    def set_label(self, label: str) -> None:
        if label not in LABELS:
            raise ValueError("label must be one of %s" % ", ".join(sorted(LABELS)))
        self.label = label

    # ---- controls (called from server threads via asyncio.to_thread) -------------------
    def _need_band(self):
        if self.band is None or not self.band.connected:
            raise RuntimeError("band is not connected")
        return self.band

    def set_led(self, colour: str, pattern: str = "solid", period: int = 1000, brightness: float = 1.0) -> bool:
        self._led_want = (colour, pattern, period, brightness)
        if self.mic_on or self.band is None:
            return False                                     # the library locks the LED while the mic is on
        try:
            self.band.set_led(colour, brightness=brightness, pattern=pattern, period=period)
            self.led = {"colour": colour, "pattern": pattern}
            return True
        except VexoError as e:
            log("LED refused:", e)
            return False

    def set_mic(self, on: bool) -> dict:
        band = self._need_band()
        with self._lock:
            if on == self.mic_on:
                return {"mic": self.mic_on}
            if on:
                speech = _import_speech()
                ears = speech.create()
                ears.start(self._on_text)                      # no key: SpeechError with one sentence
                self._ears = ears
                self._audio_handler = lambda a: ears.feed(a.opus, a.seq)
                band.on("audio", self._audio_handler)
                band.configure(audio=True)                     # light goes solid blue: mic on
                self.mic_on = True
                self.led = {"colour": "#0000ff", "pattern": "mic"}
                text = None
            else:
                try:
                    band.configure(audio=False)
                finally:
                    if self._audio_handler:
                        band.off("audio", self._audio_handler)
                    self._audio_handler = None
                    text = self._ears.stop() if self._ears else ""
                    self._ears = None
                    self.mic_on = False
                if self._led_want:
                    self.set_led(*self._led_want)
        if text is not None:
            self.post({"kind": "utterance", "source": self.src, "text": text or ""})
        self._conn(self.state, self.detail)
        return {"mic": self.mic_on}

    def _on_text(self, text: str, final: bool) -> None:
        self.post({"kind": "transcript", "source": self.src, "uid": self.uid, "text": text, "final": bool(final)})

    # ---- always-on voice (Python links) -------------------------------------------------
    def start_voice(self) -> dict:
        """Mic on for good: the band's Opus packets go to Deepgram (the kit's speech helper) and every final
        sentence comes back as a transcript. A watchdog restarts Deepgram when its connection dies and drops
        motion to 208 Hz both if samples start getting lost while audio streams."""
        self._need_band()
        self.voice_wanted = True
        self._voice_restart()
        threading.Thread(target=self._voice_watchdog, name="beltguard-voice", daemon=True).start()
        return {"voice": True}

    def _feed_audio(self, a) -> None:
        ears = self._ears
        if ears is not None:
            try:
                ears.feed(a.opus, a.seq)
            except Exception:
                pass                                       # a dead connection: the watchdog restarts it

    def _voice_restart(self) -> None:
        with self._lock:
            old, self._ears = self._ears, None
            if old is not None:
                try:
                    old.stop(wait=0.5)
                except Exception:
                    pass
            speech = _import_speech()
            ears = speech.create()
            ears.start(self._on_text)                      # no key: SpeechError with one sentence
            self._ears = ears
            if self._audio_handler is None:
                self._audio_handler = self._feed_audio
                self.band.on("audio", self._audio_handler)
            if not self.mic_on:
                self.band.configure(audio=True)            # light goes solid blue: mic on
                self.mic_on = True
                self.led = {"colour": "#0000ff", "pattern": "mic"}

    def _voice_watchdog(self) -> None:
        backoff, lost_seen, last_lost_check = 2.0, self.lost_total, time.monotonic()
        while self.voice_wanted and not self._stop.is_set():
            time.sleep(2.0)
            band = self.band
            if band is None or not getattr(band, "connected", False):
                continue                                   # reconnecting: the library restores audio itself
            ears = self._ears
            done = getattr(ears, "_done", None)
            if ears is None or (done is not None and done.is_set()):
                try:
                    self._voice_restart()
                    self.voice_restarts += 1
                    self.post({"kind": "notice", "notice": "voice-restarted", "source": self.src, "uid": self.uid,
                               "message": "Deepgram connection restarted (%d)" % self.voice_restarts})
                    backoff = 2.0
                except Exception as e:
                    self.post({"kind": "notice", "notice": "voice-error", "source": self.src, "uid": self.uid,
                               "message": str(e)[:200]})
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
            now = time.monotonic()
            if now - last_lost_check >= 10.0:              # 416 both + audio losing samples: go to 208
                if self.mic_on and not self.audio_demoted and self.sensors == "both" and self.rate > 208 \
                        and self.lost_total - lost_seen > 20:
                    try:
                        with self._lock:
                            self._configure_rate(208, "both")
                        self.audio_demoted = True
                        self.post({"kind": "notice", "notice": "rate-demoted", "source": self.src, "uid": self.uid,
                                   "message": "Lost %d samples at 416 Hz with audio: now 208 Hz both"
                                              % (self.lost_total - lost_seen)})
                        self._conn(self.state, "208 Hz both (audio on)")
                    except Exception as e:
                        log("rate drop failed:", e)
                lost_seen, last_lost_check = self.lost_total, now

    def alert(self, colour: str = "#EF4444", seconds: float = 4.0) -> None:
        """Flash the band: the LED is locked while the mic is on, so pause audio, blink, then resume."""
        band = self.band
        if band is None:
            return
        was_on = self.mic_on
        try:
            if was_on:
                band.configure(audio=False)
                self.mic_on = False
            band.set_led(colour, brightness=1.0, pattern="blink", period=250)
            self.led = {"colour": colour, "pattern": "alert"}
            time.sleep(seconds)
            if self._led_want and not was_on:
                c, pat, per, br = self._led_want
                band.set_led(c, brightness=br, pattern=pat, period=per)
        except VexoError as e:
            log("alert refused:", e)
        finally:
            if was_on:
                try:
                    band.configure(audio=True)
                    self.mic_on = True
                    self.led = {"colour": "#0000ff", "pattern": "mic"}
                except VexoError as e:
                    log("audio resume failed:", e)

    def _configure_rate(self, rate: int, sensors: str) -> None:
        self.band.configure(motion=rate, sensors=sensors)
        self.rate, self.sensors = rate, sensors
        self._last_n = None
        self._det_rate = float(rate)

    def set_rate(self, rate: int, sensors: str = "both") -> dict:
        self._need_band()
        if sensors not in ("both", "accel", "gyro"):
            raise ValueError('sensors must be "both", "accel" or "gyro"')
        rates = self.limits.get("rates") or []
        most = self.limits.get(sensors)
        if rates and rate not in rates:
            raise ValueError("rate must be one of %s" % rates)
        if most and rate > most:
            raise ValueError("%d per second with %s is more than this link allows (max %s)" % (rate, sensors, most))
        if self.mic_on and ((sensors == "both" and rate > 208) or rate > 416):
            raise ValueError("with the mic on, stay at 208 with both sensors or 416 with one")
        with self._lock:
            self._configure_rate(rate, sensors)
        self._conn(self.state, "%d Hz %s" % (rate, sensors))
        return {"rate": self.rate, "sensors": self.sensors}

    def sim_scene(self, scene: str, seconds: float | None = None) -> dict:
        if not self.director:
            raise RuntimeError("scenes exist only in --sim mode")
        return self.director.play(scene, seconds)

    def sim_auto(self, on: bool) -> dict:
        if not self.director:
            raise RuntimeError("the auto loop exists only in --sim mode")
        return self.director.set_auto(on)


# ---------------------------------------------------------------------------------------
class IngestBridge(BandBridge):
    """Band 07 samples streamed by the dashboard page over /ws/ingest: the page connects the band
    with Chrome Web Bluetooth through the kit's own JS library (frontend/bandlink.js).

    The samples go through exactly the same worker as the Python link: gyro bias, lost samples
    from `n`, the detector, the raw recording and the ~10/s motion batches, all with source "band".
    Commands for the band (LED, rate) go back to the page as {"type":"command", ...} messages;
    the page owns the Bluetooth link and decides whether to send them on."""

    LIMITS = {"rates": [10, 25, 50, 100, 208, 416, 833, 1666], "accel": 833, "gyro": 833, "both": 416,
              "transport": "browser-ble"}

    def __init__(self, post, loop) -> None:
        super().__init__(post, sim=False)
        self.link = "browser-ble"
        self.test = False
        self.limits = dict(self.LIMITS)
        self.loop = loop
        self.outbox: asyncio.Queue = asyncio.Queue()
        self._offset = None
        self._worker_started = False
        self._requested = None

    # ---- to the page -------------------------------------------------------------------
    def send_cmd(self, cmd: dict) -> None:
        """Thread-safe: queue a message for the page."""
        try:
            self.loop.call_soon_threadsafe(self.outbox.put_nowait, cmd)
        except RuntimeError:
            pass

    async def pump(self, ws) -> None:
        while True:
            cmd = await self.outbox.get()
            await ws.send_text(json.dumps(cmd))

    # ---- from the page -----------------------------------------------------------------
    def mark_connected(self, msg: dict) -> None:
        if msg.get("band") not in (None, BAND, str(BAND), "%02d" % BAND):
            raise ValueError("the page reports band %r, not %d" % (msg.get("band"), BAND))
        if msg.get("test"):                     # a test client: never recorded, labelled "sim" (contract source set)
            self.test, self.src = True, "sim"
        self.band = types.SimpleNamespace(connected=True, number=BAND, name="Vexo-HACK-%02d" % BAND,
                                          transport_kind="browser-ble")
        if msg.get("rate"):
            self.rate = int(msg["rate"])
        if msg.get("sensors"):
            self.sensors = msg["sensors"]
        self._last_n = None
        self._det_rate = float(self.rate)
        if not self._worker_started:
            if not self.test:
                self._open_raw()
            threading.Thread(target=self._worker, name="beltguard-ingest", daemon=True).start()
            self._worker_started = True
        self._conn("connected", "Chrome Web Bluetooth (dashboard) · Vexo-HACK-%02d · %d Hz %s"
                   % (BAND, self.rate, self.sensors))

    def mark_disconnected(self, detail: str = "browser link closed") -> None:
        self.band = None
        self._conn("disconnected", detail)

    def mark_reconnecting(self, detail: str) -> None:
        self._conn("reconnecting", detail)          # keeps self.band: the page is getting it back

    def on_rate(self, rate: int, sensors: str = "both") -> None:
        rate, sensors = int(rate), sensors or "both"
        if (rate, sensors) == (self.rate, self.sensors):
            return
        self.rate, self.sensors = rate, sensors
        self._last_n = None                      # n restarts when the rate or sensors change
        self._det_rate = float(rate)
        self._requested = None
        self._conn(self.state, "%d Hz %s (browser link)" % (rate, sensors))

    def feed(self, samples: list, rate=None, sensors=None) -> int:
        if rate:
            try:
                if int(rate) != self.rate or (sensors and sensors != self.sensors):
                    self.on_rate(int(rate), sensors or self.sensors)
            except (TypeError, ValueError):
                pass
        if not samples:
            return 0
        try:
            t_last = float(samples[-1]["t"]) / 1000.0
        except (KeyError, TypeError, ValueError, IndexError):
            return 0
        cand = time.time() - t_last              # laptop time minus band time, plus link delay
        if self._offset is None or cand < self._offset:
            self._offset = cand
        else:
            self._offset += (cand - self._offset) * 0.02

        def num(s, k):
            v = s.get(k)
            return None if v is None else float(v)

        count = 0
        for s in samples:
            try:
                t = float(s["t"])
            except (KeyError, TypeError, ValueError):
                continue
            tm = s.get("time")
            if tm:
                tm = float(tm)
                tm = tm / 1000.0 if tm > 1e11 else tm      # the JS library gives ms
            else:
                tm = t / 1000.0 + self._offset
            self._q.put(types.SimpleNamespace(t=t, n=s.get("n"), ax=num(s, "ax"), ay=num(s, "ay"), az=num(s, "az"),
                                              gx=num(s, "gx"), gy=num(s, "gy"), gz=num(s, "gz"), time=tm))
            count += 1
        return count

    # ---- controls: forwarded to the page ----------------------------------------------
    def set_led(self, colour: str, pattern: str = "solid", period: int = 1000, brightness: float = 1.0) -> bool:
        self._led_want = (colour, pattern, period, brightness)
        if self.band is None or self.mic_on:        # the band refuses LED commands while its mic is on
            return False
        self.send_cmd({"type": "command", "cmd": "led", "colour": colour, "pattern": pattern,
                       "period": period, "brightness": brightness})
        self.led = {"colour": colour, "pattern": pattern}
        return True

    def set_rate(self, rate: int, sensors: str = "both") -> dict:
        if self.band is None:
            raise RuntimeError("the browser link is not connected")
        most = self.limits.get(sensors)
        if rate not in self.limits["rates"] or (most and rate > most):
            raise ValueError("%d per second with %s is more than Bluetooth allows (max %s)" % (rate, sensors, most))
        self._requested = (int(rate), sensors)
        self.send_cmd({"type": "command", "cmd": "rate", "rate": int(rate), "sensors": sensors})
        return {"requested_rate": int(rate), "sensors": sensors, "via": "browser-ble"}

    def set_mic(self, on: bool) -> dict:
        if not on:
            return {"mic": False}
        raise RuntimeError("Voice reports are not wired over the browser link yet: type the report instead.")

    def stop(self, quiet: bool = False) -> None:
        self._stop.set()
        self.band = None
        self._close_raw()
        if not quiet:                               # quiet: the server reports "reconnecting" instead
            self._conn("disconnected", "stopped")
