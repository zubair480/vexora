"""Replay a recording onto the bus as if it were arriving now.

Everything published here carries source="replay": it is never presented as a live band.
Recorded spacing is preserved exactly, so a recorded gap stays a gap and is never filled in.
"""
from __future__ import annotations

import csv
import json
import math
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

from . import config, event_bus
from .models import Event, ImuBatch, ImuSample
from .recorder import valid_session_id
from .vexo_bridge import QualityTracker   # pure: importing it never touches the kit or a band

SOURCE = "replay"


def recording_dir(session_id: str) -> Path:
    valid_session_id(session_id)
    for folder in (config.RECORDINGS / session_id, config.RECORDINGS / "fixtures" / session_id):
        if (folder / "samples.csv").is_file():
            return folder
    raise FileNotFoundError(f"no recording {session_id!r}")


def load_recording(session_id: str) -> tuple[np.ndarray, dict]:
    """(N, 9) float64 array with columns n,t,time,ax,ay,az,gx,gy,gz (missing n -> NaN), and meta."""
    folder = recording_dir(session_id)
    rows = []
    with open(folder / "samples.csv", newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader, None)   # header
        for row in reader:
            if len(row) >= 9:
                rows.append([float(cell) if cell.strip() else math.nan for cell in row[:9]])
    data = np.array(rows, dtype=np.float64).reshape(-1, 9)
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            raise ValueError("meta.json is not an object")
    except (OSError, ValueError):
        meta = {"session_id": session_id}
    return data, meta


class ReplaySource:
    def __init__(self, session_id: str, speed: float = 1.0, batch_hz: float = 10.0, loop: bool = False) -> None:
        if not (isinstance(speed, (int, float)) and 0 < speed <= 100):
            raise ValueError("speed must be > 0 and <= 100")
        if not batch_hz > 0:
            raise ValueError("batch_hz must be > 0")
        self.session_id = session_id
        self.speed = float(speed)
        self.batch_hz = float(batch_hz)
        self.loop = loop
        self._data, self._meta = load_recording(session_id)
        self._sent = 0
        self._running = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._running

    def status(self) -> dict:
        total = len(self._data)
        return {"running": self._running, "session_id": self.session_id, "speed": self.speed,
                "samples_total": total, "samples_sent": self._sent,
                "progress": min(1.0, self._sent / total) if total else 1.0,
                "source": SOURCE, "recorded_source": self._meta.get("source"),
                "label": self._meta.get("label"),
                "configured_hz": self._meta.get("configured_hz"),
                "effective_hz": self._meta.get("effective_hz")}

    def start(self) -> dict:
        with self._lock:
            if not self._running:
                self._stop.clear()
                self._running = True
                self._thread = threading.Thread(target=self._run, args=(True,), name="replay", daemon=True)
                self._thread.start()
        return self.status()

    def stop(self) -> dict:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
        return self.status()

    def run_blocking(self, realtime: bool = True) -> int:
        with self._lock:
            if self._running:
                raise RuntimeError("replay already running")
            self._stop.clear()
            self._running = True
        return self._run(realtime)

    # internals
    def _event(self, kind: str, payload: dict) -> None:
        shift = config.current_shift()
        event_bus.bus.publish(Event(kind, payload, source=SOURCE,
                                    shift_id=shift["shift_id"], worker_id=shift["worker_id"]))

    def _device_payload(self, reason: str) -> dict:
        return {"band_name": self._meta.get("band_name"), "firmware": None, "battery": None,
                "transport": "replay", "reason": reason, "session_id": self.session_id,
                "label": self._meta.get("label"), "recorded_source": self._meta.get("source"),
                "speed": self.speed}

    def _quality_payload(self, tracker: QualityTracker, now: float) -> dict:
        """Measured from the samples replayed so far (same tracker as the live bridge), not copied
        from meta.json. Saturation is not judged: the recording does not carry the sensor range."""
        snap = tracker.snapshot(now)
        return {k: snap[k] for k in ("configured_hz", "effective_hz", "missing_samples", "completeness",
                                     "saturated", "stale", "missing_samples_known")}

    def _chunks(self) -> list[tuple[int, int]]:
        """Index ranges, each covering 1/batch_hz seconds of recorded time."""
        times = self._data[:, 2]
        keys = np.floor((times - times[0]) * self.batch_hz + 1e-6)
        cuts = [0] + [i for i in range(1, len(keys)) if keys[i] != keys[i - 1]] + [len(keys)]
        return list(zip(cuts[:-1], cuts[1:]))

    def _run(self, realtime: bool) -> int:
        self._sent = 0
        reason = "replay-finished"
        try:
            self._event("device_connected", self._device_payload("replay-started"))
            data, meta = self._data, self._meta
            if len(data):
                times = data[:, 2]
                base = float(times[0])
                # one pass lasts the recording plus one nominal sample period (used when looping)
                span = float(times[-1]) - base + (1.0 / (meta.get("effective_hz") or meta.get("configured_hz") or 50.0))
                chunks = self._chunks()
                wall_start = time.time()
                clock_start = time.monotonic()
                offset = 0.0          # recorded seconds already replayed in earlier loops
                next_quality = 0.0
                tracker = QualityTracker(float(meta.get("configured_hz") or 0.0))
                last_rel = None
                while True:
                    for lo, hi in chunks:
                        rel = offset + float(times[lo]) - base
                        if last_rel is not None and rel - last_rel > tracker.stale_after:
                            # a recorded gap is replayed as a gap: say the stream is stale meanwhile
                            self._event("sensor_quality", self._quality_payload(
                                tracker, wall_start + last_rel + tracker.stale_after + 0.5))
                        if realtime:
                            wait = clock_start + rel / self.speed - time.monotonic()
                            if wait > 0 and self._stop.wait(wait):
                                break
                        if self._stop.is_set():
                            break
                        samples = [
                            ImuSample(None if math.isnan(r[0]) else int(r[0]), float(r[1]),
                                      wall_start + offset + (float(r[2]) - base),
                                      float(r[3]), float(r[4]), float(r[5]),
                                      float(r[6]), float(r[7]), float(r[8]))
                            for r in data[lo:hi]
                        ]
                        for s in samples:
                            tracker.add(s.n, s.time, False, s.time)
                        last_rel = samples[-1].time - wall_start
                        event_bus.bus.publish_imu(ImuBatch(samples=samples,
                                                           configured_hz=tracker.configured_hz,
                                                           effective_hz=tracker.effective_hz(samples[-1].time),
                                                           source=SOURCE))
                        self._sent += len(samples)
                        if rel >= next_quality:
                            self._event("sensor_quality", self._quality_payload(tracker, samples[-1].time))
                            next_quality = math.floor(rel) + 1.0
                    if self._stop.is_set() or not self.loop:
                        break
                    offset += span
            if self._stop.is_set():
                reason = "replay-stopped"
        finally:
            payload = self._device_payload(reason)
            payload["samples_sent"] = self._sent
            self._running = False
            self._event("device_disconnected", payload)
        return self._sent


_current: Optional[ReplaySource] = None
_current_lock = threading.Lock()


def start_replay(session_id: str, speed: float = 1.0) -> dict:
    global _current
    with _current_lock:
        source = ReplaySource(session_id, speed=speed)   # validates before stopping the current one
        if _current is not None:
            _current.stop()
        _current = source
        return source.start()


def stop_replay() -> dict:
    with _current_lock:
        if _current is None:
            return {"running": False, "source": SOURCE}
        return _current.stop()


def replay_status() -> dict:
    with _current_lock:
        if _current is None:
            return {"running": False, "source": SOURCE}
        return _current.status()
