"""Labelled IMU recorder: writes what arrives on the bus to recordings/<session_id>/.

Format (CONTRACT.md): samples.csv with header n,t,time,ax,ay,az,gx,gy,gz and meta.json.
The bus callback runs on the bus thread: it only appends rows and updates counters under a lock.
"""
from __future__ import annotations

import csv
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from . import config, event_bus
from .models import LABELS, ImuBatch

CSV_HEADER = ["n", "t", "time", "ax", "ay", "az", "gx", "gy", "gz"]
META_KEYS = ["session_id", "label", "tool_id", "band_name", "wearer", "configured_hz", "effective_hz",
             "missing_samples", "started_at", "ended_at", "source", "notes"]
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,79}[A-Za-z0-9_-])?$")   # no trailing dot (Windows)
SEGMENT_GAP_S = 1.0   # a longer silence between two samples is a link gap, not a slow rate
UNITS = {"n": "sample counter", "t": "band clock, milliseconds", "time": "epoch seconds",
         "ax,ay,az": "g", "gx,gy,gz": "deg/s"}


def valid_session_id(session_id: str) -> str:
    if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        raise ValueError(f"bad session_id {session_id!r}")
    return session_id


class Recorder:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._bus = None          # the bus object we are subscribed to
        self._active = False
        self._info: dict = {}
        self._file = None
        self._writer = None
        self._reset_stats()

    def _reset_stats(self) -> None:
        self._count = 0
        self._prev: Optional[tuple] = None   # (n, time) of the last sample
        self._intervals = 0                  # sample-to-sample steps inside segments
        self._duration = 0.0                 # seconds covered by those steps
        self._missing = 0
        self._missing_known = True
        self._gaps: list[dict] = []
        self._sources: list[str] = []
        self._batch_hz: Optional[float] = None

    @property
    def active(self) -> bool:
        return self._active

    def _ensure_subscribed(self) -> None:
        bus = event_bus.bus
        if bus is not self._bus:   # no unsubscribe on the bus: once per bus object
            bus.subscribe_imu(self._on_batch)
            self._bus = bus

    def start(self, label: str, tool_id: Optional[str] = None, wearer: Optional[str] = None,
              notes: str = "", band_name: Optional[str] = None, configured_hz: Optional[float] = None,
              session_id: Optional[str] = None) -> dict:
        if label not in LABELS:
            raise ValueError(f"label must be one of {', '.join(LABELS)}")
        with self._lock:
            if self._active:
                raise RuntimeError("already recording")
            if session_id is None:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                session_id = f"{label}-{stamp}-{uuid.uuid4().hex[:4]}"
            valid_session_id(session_id)
            if session_id == "fixtures":
                raise ValueError("bad session_id 'fixtures'")
            folder = config.RECORDINGS / session_id
            if (folder / "samples.csv").exists():
                raise ValueError(f"recording {session_id!r} already exists")
            folder.mkdir(parents=True, exist_ok=True)
            self._file = open(folder / "samples.csv", "w", newline="", encoding="utf-8")
            self._writer = csv.writer(self._file)
            self._writer.writerow(CSV_HEADER)
            self._file.flush()
            self._reset_stats()
            self._info = {"session_id": session_id, "label": label, "tool_id": tool_id,
                          "band_name": band_name, "wearer": wearer, "configured_hz": configured_hz,
                          "notes": notes or "", "started_at": time.time(), "folder": folder}
            self._ensure_subscribed()
            self._active = True
            return self.status()

    def _on_batch(self, batch: ImuBatch) -> None:
        with self._lock:
            if not self._active or event_bus.bus is not self._bus:
                return
            if batch.source not in self._sources:
                self._sources.append(batch.source)
            if batch.configured_hz:
                self._batch_hz = batch.configured_hz
            rows = []
            for s in batch.samples:
                rows.append(["" if s.n is None else int(s.n), repr(float(s.t)), repr(float(s.time)),
                             s.ax, s.ay, s.az, s.gx, s.gy, s.gz])
                self._account(s.n, float(s.time))
            self._writer.writerows(rows)

    def _account(self, n: Optional[int], t: float) -> None:
        self._count += 1
        if n is None:
            self._missing_known = False
        if self._prev is not None:
            prev_n, prev_t = self._prev
            reset = n is not None and prev_n is not None and n < prev_n
            dt = t - prev_t
            if reset or dt > SEGMENT_GAP_S:
                self._gaps.append({"start": prev_t, "end": t})   # link gap: never counted as data
            else:
                self._intervals += 1
                self._duration += max(dt, 0.0)
                if n is not None and prev_n is not None:
                    self._missing += max(0, n - prev_n - 1)
        self._prev = (n, t)

    def stop(self) -> dict:
        with self._lock:
            if not self._active:
                raise RuntimeError("not recording")
            self._active = False
            self._file.flush()
            self._file.close()
            self._file = self._writer = None
            info = self._info
            configured = info["configured_hz"] if info["configured_hz"] is not None else self._batch_hz
            effective = round(self._intervals / self._duration, 3) if self._duration > 0 else None
            source = None if not self._sources else self._sources[0] if len(self._sources) == 1 else "mixed"
            meta = {
                "session_id": info["session_id"], "label": info["label"], "tool_id": info["tool_id"],
                "band_name": info["band_name"], "wearer": info["wearer"],
                "configured_hz": configured, "effective_hz": effective,
                "missing_samples": self._missing,
                "started_at": info["started_at"], "ended_at": time.time(),
                "source": source, "notes": info["notes"],
                "samples": self._count, "missing_samples_known": self._missing_known,
                "data_gaps": list(self._gaps), "units": UNITS,
            }
            (info["folder"] / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
            return meta

    def status(self) -> dict:
        with self._lock:
            info = self._info if self._active else {}
            return {"recording": self._active, "session_id": info.get("session_id"),
                    "label": info.get("label"), "tool_id": info.get("tool_id"),
                    "wearer": info.get("wearer"), "samples": self._count if self._active else 0,
                    "started_at": info.get("started_at")}


_recorder: Optional[Recorder] = None
_recorder_lock = threading.Lock()


def get_recorder() -> Recorder:
    global _recorder
    with _recorder_lock:
        if _recorder is None:
            _recorder = Recorder()
        return _recorder


def list_recordings() -> list[dict]:
    """Every readable meta.json (raw recordings and committed fixtures), newest first."""
    found = []
    for root, fixture in ((config.RECORDINGS, False), (config.RECORDINGS / "fixtures", True)):
        if not root.is_dir():
            continue
        for folder in root.iterdir():
            try:
                meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
                if not isinstance(meta, dict):
                    continue
            except (OSError, ValueError):
                continue
            meta.setdefault("session_id", folder.name)
            meta["fixture"] = fixture
            found.append(meta)
    found.sort(key=lambda m: m.get("started_at") if isinstance(m.get("started_at"), (int, float)) else 0.0,
               reverse=True)
    return found
