"""Tool-use session detection: a pure detector plus the live glue (bus + SQLite).

Everything here is an ESTIMATE of wrist activity ("probably using a tool"), for screening and
planning. Wrist acceleration is activity evidence only, never a tool vibration magnitude, and a
detected session is "tentative" until a human confirms it. Data gaps never become session time.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

from . import config, database, event_bus, imu_features, tool_classifier
from .models import ACTIVITIES, Event, ImuBatch

log = logging.getLogger("beltguard.toolsense")

_TOOL = ACTIVITIES.index("tool_use")
COLUMNS = ("session_id", "shift_id", "worker_id", "start_ts", "end_ts", "duration_s", "tool_id",
           "confidence", "completeness", "confirmation", "source", "model", "reason", "updated_at")
_NOT_IN_PAYLOAD = ("shift_id", "worker_id", "updated_at")

database.register_schema(
    """
    CREATE TABLE IF NOT EXISTS tool_sessions (
        session_id TEXT PRIMARY KEY, shift_id TEXT NOT NULL, worker_id TEXT NOT NULL,
        start_ts REAL NOT NULL, end_ts REAL, duration_s REAL, tool_id TEXT, confidence REAL,
        completeness REAL, confirmation TEXT NOT NULL, source TEXT NOT NULL, model TEXT,
        reason TEXT, updated_at REAL
    );
    CREATE INDEX IF NOT EXISTS tool_sessions_shift ON tool_sessions (shift_id, start_ts);
    """
)


BACKSTEP_S = 0.05        # backward `time` steps up to this are stamp jitter, not a gap
LOST_PERIODS = 2.5       # a `time` step longer than this many sample periods = lost samples


@dataclass
class DetectorConfig:
    window_s: float = 1.5
    hop_s: float = 0.5
    context_s: float = 4.0            # buffer kept for gravity estimation
    smoothing: float = 0.5            # EMA factor on class probabilities (per hop); 0 = none
    start_threshold: float = 0.65     # smoothed tool probability to open a candidate
    stop_threshold: float = 0.40      # lower: hysteresis
    min_duration_s: float = 3.0       # shorter candidates never become sessions
    stop_hold_s: float = 1.0          # must stay below stop_threshold this long to end
    min_confidence: float = 0.45      # below -> activity "unknown"
    min_completeness: float = 0.75    # window below -> "unknown", not counted as tool time
    gap_s: float = 1.0                # jump in sample `time` (or n reset) = data gap
    stale_s: float = 2.5              # no batch for this long (wall clock) = stale
    edge_overlap_s: float = 0.1       # tool content in a window that already flips the classifier.
    #                                   Measured for the rule baseline on SYNTHETIC signals only:
    #                                   an untuned guess until real recordings exist.


class ToolDetector:
    """Pure state machine: no bus, no DB, no wall clock. Time is the sample `time` column.

    The smoothed tool probability decides WHETHER a session exists (hysteresis, hold, minimum
    duration). WHEN it starts and ends comes from the raw per-hop probability, which has no
    smoothing lag: a window flips to "tool" once it holds about `edge_overlap_s` of tool
    motion, so the start is the first raw-tool hop minus that (and half a hop), and the end is
    the last raw-tool hop minus (window_s - edge_overlap_s) plus half a hop. Time lost inside
    a session (samples that never arrived) is subtracted from duration_s and reported as
    missing_s: gaps are never tool time.
    """

    def __init__(self, classifier=None, config: Optional[DetectorConfig] = None) -> None:
        self.classifier = classifier if classifier is not None else tool_classifier.load_active()
        self.config = config or DetectorConfig()
        self._last_sample_time: Optional[float] = None
        self._reset()

    def _reset(self) -> None:
        self._buf: Optional[np.ndarray] = None
        self._seg_start = 0.0
        self._next_hop = 0.0
        self._last_time: Optional[float] = None
        self._last_n: Optional[float] = None
        self._period: Optional[float] = None           # measured sample period, seconds
        self._gaps: list[tuple[float, float]] = []     # (last sample before, first after) a loss
        self._history: deque = deque(maxlen=4)         # (hop ts, raw tool probability)
        self._smooth: Optional[np.ndarray] = None
        self._activity = "unknown"
        self._confidence = 0.0
        self._tool_p = 0.0
        self._probs = {a: 0.0 for a in ACTIVITIES}
        self._completeness = 0.0
        self._model: Optional[str] = None              # model that answered the latest window
        self._last_end = -np.inf
        self._clear_candidate()

    def _clear_candidate(self) -> None:
        self._cand_start: Optional[float] = None
        self._last_above = 0.0                         # latest hop with raw tool p >= stop
        self._below: Optional[float] = None
        self._invalid_since: Optional[float] = None
        self._session_id: Optional[str] = None
        self._session_model: Optional[str] = None
        self._sum_p = self._sum_c = 0.0
        self._hops = 0

    # ---- public -------------------------------------------------------------------------
    def feed(self, arr: np.ndarray) -> list[dict]:
        """(N, 9) rows in arrival order -> list of transitions."""
        arr = np.asarray(arr, dtype=float)
        if arr.ndim != 2 or len(arr) == 0:
            return []
        arr = arr[np.isfinite(arr[:, 2])]              # a row without a time cannot be placed
        if len(arr) == 0:
            return []
        out: list[dict] = []
        t, n = arr[:, 2], arr[:, 0]
        with np.errstate(invalid="ignore"):
            dt = np.diff(t, prepend=t[0] if self._last_time is None else self._last_time)
            dn = np.diff(n, prepend=n[0] if self._last_n is None else self._last_n)
            gap = (dt > self.config.gap_s) | (dt < -BACKSTEP_S) | (dn < 0)   # NaN n compares False
        start = 0
        for cut in [*np.flatnonzero(gap).tolist(), len(arr)]:
            if cut > start:
                self._feed_contiguous(arr[start:cut], out)
            if cut < len(arr):
                out += self.flush("data_gap")
            start = cut
        return out

    def flush(self, reason: str = "end_of_data") -> list[dict]:
        """End the open session at the last sample, drop a candidate, reset; state -> unknown."""
        out: list[dict] = []
        last = self._last_time
        if self._cand_start is not None and last is not None:
            still_tool = bool(self._history) and self._history[-1][0] == self._last_above
            self._end(last if still_tool else self._edge_end(), reason, out)
        if self._activity != "unknown" and last is not None:
            out.append({"kind": "activity", "activity": "unknown", "confidence": 0.0,
                        "tool_probability": 0.0, "ts": last, "model": self._model_name()})
        self._reset()
        return out

    def state(self) -> dict:
        return {"activity": self._activity, "confidence": self._confidence,
                "tool_probability": self._tool_p, "probabilities": dict(self._probs),
                "completeness": self._completeness, "model": self._model_name(),
                "in_session": self._session_id is not None, "session_id": self._session_id,
                "last_sample_time": self._last_sample_time}

    # ---- internals ----------------------------------------------------------------------
    def _model_name(self) -> str:
        return self._model or getattr(self.classifier, "name", "unknown")

    def _feed_contiguous(self, seg: np.ndarray, out: list[dict]) -> None:
        cfg = self.config
        seg = seg.copy()
        fresh = self._buf is None
        # small backward steps: keep arrival order, clamp the step to 0
        seg[:, 2] = np.maximum.accumulate(np.maximum(seg[:, 2], -np.inf if fresh else self._last_time))
        if fresh:
            self._buf, self._seg_start = seg, float(seg[0, 2])
            self._next_hop = self._seg_start + cfg.hop_s
            t, n = seg[:, 2], seg[:, 0]
        else:
            self._buf = np.vstack([self._buf, seg])
            t, n = np.r_[self._last_time, seg[:, 2]], np.r_[self._last_n, seg[:, 0]]
        self._last_time = self._last_sample_time = float(seg[-1, 2])
        self._last_n = float(seg[-1, 0])
        times = self._buf[:, 2]
        if self._period is None and len(times) >= 3:
            steps = np.diff(times)
            steps = steps[steps > 0]
            self._period = float(np.median(steps)) if len(steps) else None
        if self._period and len(t) > 1:
            # lost samples: a long time step, unless the counter says nothing is missing
            lost = (np.diff(t) > LOST_PERIODS * self._period) & ~(np.diff(n) == 1)
            self._gaps += [(float(t[i]), float(t[i + 1])) for i in np.flatnonzero(lost)]
        while self._next_hop <= times[-1]:
            hi = int(np.searchsorted(times, self._next_hop, side="right"))
            lo = int(np.searchsorted(times, self._next_hop - cfg.context_s, side="left"))
            # a hop that falls inside a dropout has no real sample to stand on: skip it
            if hi > lo and self._next_hop - times[hi - 1] <= LOST_PERIODS * (self._period or np.inf):
                self._hop(self._buf[lo:hi], out)
            self._next_hop += cfg.hop_s
        self._buf = self._buf[int(np.searchsorted(times, times[-1] - cfg.context_s, side="left")):]

    def _hop(self, ctx: np.ndarray, out: list[dict]) -> None:
        cfg = self.config
        ts = float(ctx[-1, 2])
        window = imu_features.latest_window(ctx, cfg.window_s)
        self._completeness = float(window.completeness) if window is not None else 0.0
        if window is not None and window.sample_hz > 0:
            self._period = 1.0 / float(window.sample_hz)
        valid = window is not None and self._completeness >= cfg.min_completeness
        raw = 0.0
        if valid:
            result = tool_classifier.classify(self.classifier, window)
            probs = result["probabilities"]
            self._model = result.get("model") or getattr(self.classifier, "name", "unknown")
            p = np.array([float(probs.get(a, 0.0)) for a in ACTIVITIES])
            raw = float(p[_TOOL])
            a = cfg.smoothing
            self._smooth = p if self._smooth is None else a * self._smooth + (1 - a) * p
            best = int(np.argmax(self._smooth))
            self._confidence = float(self._smooth[best])
            self._tool_p = float(self._smooth[_TOOL])
            self._probs = {name: float(v) for name, v in zip(ACTIVITIES, self._smooth)}
            activity = ACTIVITIES[best] if self._confidence >= cfg.min_confidence else "unknown"
        else:   # too little / too patchy data: no claim from this hop
            activity, self._confidence, self._tool_p = "unknown", 0.0, 0.0
            self._probs = {name: 0.0 for name in ACTIVITIES}
        if activity != self._activity:
            self._activity = activity
            out.append({"kind": "activity", "activity": activity, "confidence": self._confidence,
                        "tool_probability": self._tool_p, "ts": ts, "model": self._model_name()})
        self._history.append((ts, raw))

        if self._cand_start is None:
            self._gaps = [g for g in self._gaps if g[1] > ts - 2 * cfg.context_s]
            if not (valid and self._tool_p >= cfg.start_threshold):
                return
            first, level = ts, (cfg.start_threshold + cfg.stop_threshold) / 2
            for hop_ts, hop_raw in list(self._history)[-2::-1]:    # undo the smoothing lag
                if hop_raw < level or first - hop_ts > 1.5 * cfg.hop_s:
                    break
                first = hop_ts
            self._cand_start = max(first - cfg.edge_overlap_s - cfg.hop_s / 2,
                                   self._seg_start, self._last_end)
            self._session_model, self._last_above = self._model_name(), ts
        if not valid:
            # brief bad quality inside a candidate: no claim either way. Sustained: end at the
            # last hop that really showed tool use.
            if self._invalid_since is None:
                self._invalid_since = ts
            if ts - self._invalid_since >= cfg.stop_hold_s + cfg.window_s:
                self._end(self._last_above, "low_quality", out)
            return
        self._invalid_since = None
        if raw >= cfg.stop_threshold:
            self._last_above = ts
        if self._tool_p < cfg.stop_threshold:
            if self._below is None:
                self._below = ts
            if ts - self._below >= cfg.stop_hold_s:
                self._end(self._edge_end(), "activity_stopped", out)
            return
        self._below = None
        self._sum_p += self._tool_p
        self._sum_c += self._completeness
        self._hops += 1
        if self._session_id is None:
            so_far = self._session(None, self._edge_end())
            if so_far["duration_s"] >= cfg.min_duration_s:
                self._session_id = so_far["session_id"] = uuid.uuid4().hex
                out.append({"kind": "session_started", **so_far})

    def _edge_end(self) -> float:
        """Tool end implied by the last raw-tool hop (see the class docstring)."""
        cfg = self.config
        return self._last_above - (cfg.window_s - cfg.edge_overlap_s) + cfg.hop_s / 2

    def _missing(self, start: float, end: float) -> float:
        period = self._period or 0.0
        return sum(max(0.0, min(b, end) - max(a, start) - period) for a, b in self._gaps)

    def _session(self, end_ts: Optional[float], now: float) -> dict:
        hops = max(self._hops, 1)
        stop = max(now if end_ts is None else end_ts, self._cand_start)
        missing = self._missing(self._cand_start, stop)
        return {"session_id": self._session_id, "start_ts": self._cand_start, "end_ts": end_ts,
                "duration_s": max(0.0, stop - self._cand_start - missing), "missing_s": missing,
                "confidence": self._sum_p / hops, "completeness": self._sum_c / hops,
                "model": self._session_model}

    def _end(self, end_ts: float, reason: str, out: list[dict]) -> None:
        if self._session_id is not None:   # a candidate that never became a session is dropped
            end_ts = max(end_ts, self._cand_start)
            out.append({"kind": "session_ended", **self._session(end_ts, end_ts), "reason": reason})
            self._last_end = end_ts
        self._clear_candidate()


def detect_sessions(arr, classifier=None, config: Optional[DetectorConfig] = None) -> list[dict]:
    """Offline: run the detector over a whole recording, return the session_ended dicts."""
    detector = ToolDetector(classifier, config)
    transitions = detector.feed(arr) + detector.flush("end_of_data")
    return [t for t in transitions if t["kind"] == "session_ended"]


# ---- SQLite ---------------------------------------------------------------------------------
def _save(row: dict) -> None:
    con = database.connect()
    try:
        with con:
            con.execute("INSERT OR REPLACE INTO tool_sessions VALUES (%s)" % ",".join("?" * len(COLUMNS)),
                        [row.get(c) for c in COLUMNS])
    finally:
        con.close()


def _get(session_id: str) -> Optional[dict]:
    con = database.connect()
    try:
        row = con.execute("SELECT * FROM tool_sessions WHERE session_id = ?", (session_id,)).fetchone()
    finally:
        con.close()
    return dict(row) if row else None


def _payload(row: dict) -> dict:
    payload = {k: v for k, v in row.items() if k not in _NOT_IN_PAYLOAD}
    if payload.get("reason") is None:
        payload.pop("reason", None)
    return payload


def list_sessions(shift_id: Optional[str] = None) -> list[dict]:
    """All sessions of a shift (default: the current one), ordered by start_ts."""
    shift_id = shift_id or config.current_shift()["shift_id"]
    con = database.connect()
    try:
        rows = con.execute("SELECT * FROM tool_sessions WHERE shift_id = ? ORDER BY start_ts ASC",
                           (shift_id,)).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def confirm(session_id: str, tool_id: Optional[str], confirmed: bool) -> dict:
    """A human confirms or rejects a finished session; republishes tool_session_ended."""
    row = _get(session_id)
    if row is None:
        raise KeyError(session_id)
    if row["end_ts"] is None:
        raise ValueError(f"session {session_id} is still open")
    if tool_id is not None:
        row["tool_id"] = tool_id
    row["confirmation"] = "confirmed" if confirmed else "rejected"
    row["updated_at"] = time.time()
    _save(row)
    event_bus.bus.publish(Event("tool_session_ended", {**_payload(row), "confirmed_by": "manual"},
                                row["source"], row["shift_id"], row["worker_id"]))
    return row


# ---- live service ---------------------------------------------------------------------------
class ToolSessionService:
    """Feeds IMU batches from the bus into a ToolDetector, stores sessions, publishes events."""

    def __init__(self, bus=None, classifier=None, config: Optional[DetectorConfig] = None,
                 shift: Optional[dict] = None) -> None:
        self._bus = bus
        self._shift = shift
        self.detector = ToolDetector(classifier, config)
        self._lock = threading.RLock()
        self._started = False
        self._stop = threading.Event()
        self._source: Optional[str] = None
        self._last_wall: Optional[float] = None
        self._stale = True                     # no data yet
        self._updated_at: Optional[float] = None
        self._row: Optional[dict] = None       # the open session's row

    @property
    def bus(self):
        return self._bus if self._bus is not None else event_bus.bus

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        try:
            self._close_orphans()
        except Exception:
            log.exception("could not close orphaned tool sessions")
        self.bus.subscribe_imu(self.on_batch)
        self.bus.subscribe(self.on_event, types=["device_disconnected"])
        self._stop.clear()
        threading.Thread(target=self._watchdog, name="toolsense-stale", daemon=True).start()

    def stop(self) -> None:
        """Stop the stale watchdog (the bus has no unsubscribe; used by tests)."""
        self._stop.set()

    def _close_orphans(self) -> None:
        """Rows of this shift left open by a previous process: close at the last known
        duration (reason "restart"), silently. Unknown time after that is not tool time."""
        if not config.DB_PATH.exists():
            return
        shift = self._shift or config.current_shift()
        own = self._row["session_id"] if self._row else ""
        con = database.connect()
        try:
            with con:
                con.execute(
                    "UPDATE tool_sessions SET end_ts = start_ts + COALESCE(duration_s, 0), "
                    "duration_s = COALESCE(duration_s, 0), reason = 'restart', updated_at = ? "
                    "WHERE shift_id = ? AND end_ts IS NULL AND session_id != ?",
                    (time.time(), shift["shift_id"], own))
        finally:
            con.close()

    def _watchdog(self) -> None:
        while not self._stop.wait(1.0):
            try:
                self.check_stale()
            except Exception:
                log.exception("stale check failed")

    def on_batch(self, batch: ImuBatch) -> None:
        """Runs on the bus imu thread, never on the band callback."""
        if not batch.samples:
            return
        arr = imu_features.samples_to_array(batch.samples)
        with self._lock:
            if self._source is not None and batch.source != self._source:
                self._apply(self.detector.flush("source_changed"), self._source)
            self._source = batch.source
            self._last_wall = self._updated_at = time.time()
            self._stale = False
            self._apply(self.detector.feed(arr), batch.source)

    def on_event(self, event: Event) -> None:
        if event.type == "device_disconnected":
            self._flush("disconnect")

    def check_stale(self, now: Optional[float] = None) -> bool:
        """No batch for stale_s of wall clock: close what is open, activity unknown."""
        now = time.time() if now is None else now
        with self._lock:
            if self._stale or self._last_wall is None:
                return True
            if now - self._last_wall > self.detector.config.stale_s:
                self._stale = True
                self._flush("stale")
            return self._stale

    def state(self) -> dict:
        with self._lock:
            stale = self.check_stale()
            clf = self.detector.classifier
            info = json.loads(json.dumps(getattr(clf, "info", {}), default=str))
            # "model" (from the detector) is the model that answered the latest window, which
            # can be the rules even when a forest is loaded; "model_info" is the loaded one.
            return {**self.detector.state(), "source": self._source, "stale": stale,
                    "model_info": info, "updated_at": self._updated_at}

    def reload_classifier(self) -> None:
        """Pick up a freshly trained model (or fall back to the rules)."""
        clf = tool_classifier.load_active()
        with self._lock:
            self.detector.classifier = clf

    # ---- internals ----------------------------------------------------------------------
    def _flush(self, reason: str) -> None:
        with self._lock:
            self._apply(self.detector.flush(reason), self._source or "band")
            self._updated_at = time.time()

    def _apply(self, transitions: list[dict], source: str) -> None:
        if not transitions:
            return
        shift = self._shift or config.current_shift()
        for tr in transitions:
            model = tr.get("model") or getattr(self.detector.classifier, "name", "unknown")
            if tr["kind"] == "activity":
                payload = {"activity": tr["activity"], "confidence": tr["confidence"],
                           "tool_probability": tr["tool_probability"], "model": model}
                kind = "activity_changed"
            else:
                started = tr["kind"] == "session_started"
                row = {} if started or self._row is None else self._row
                row.update({k: tr.get(k) for k in ("session_id", "start_ts", "end_ts", "duration_s",
                                                   "missing_s", "confidence", "completeness",
                                                   "reason")})
                row.setdefault("shift_id", shift["shift_id"])
                row.setdefault("worker_id", shift["worker_id"])
                row.update(tool_id=None, confirmation="tentative", source=row.get("source", source),
                           model=row.get("model", model), updated_at=time.time())
                self._row = row if started else None
                try:
                    _save(row)
                except Exception:
                    log.exception("could not store tool session %s", row["session_id"])
                payload = _payload(row)
                kind = "tool_session_started" if started else "tool_session_ended"
            self.bus.publish(Event(kind, payload, source, shift["shift_id"], shift["worker_id"]))


_service: Optional[ToolSessionService] = None
_service_lock = threading.Lock()


def get_service() -> ToolSessionService:
    global _service
    with _service_lock:
        if _service is None:
            _service = ToolSessionService()
        return _service


def start() -> ToolSessionService:
    service = get_service()
    service.start()
    return service
