"""band endpoints. Owned by the be-band session (see CONTRACT.md).

Live band (vexo_bridge), labelled recordings (recorder), replay of recordings (replay) and the
recent IMU window for the UI. Replay is never presented as a live band: `connected` stays false
and `source` says "replay".
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .. import config, event_bus
from ..models import ImuBatch

router = APIRouter(prefix="/api/band", tags=["band"])

UNITS = {"t": "band clock, milliseconds", "time": "epoch seconds", "accel": "g", "gyro": "deg/s"}
FIELDS = ("n", "t", "time", "ax", "ay", "az", "gx", "gy", "gz")
STALE_AFTER_S = 1.0


def _num(value):
    """JSON has no NaN/inf: an axis that is off travels as null."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class RecentImu:
    """Last ~60 s of samples. The bus callback only appends under a lock."""

    def __init__(self, maxlen: int = 30000) -> None:
        self._lock = threading.Lock()
        self._samples: deque = deque(maxlen=maxlen)
        self._source: Optional[str] = None
        self._configured_hz: Optional[float] = None
        self._effective_hz: Optional[float] = None
        self._last_batch_at: Optional[float] = None   # wall clock of the last batch received

    def on_batch(self, batch: ImuBatch) -> None:
        with self._lock:
            if batch.source != self._source:
                self._samples.clear()        # never show one source's samples under another's label
            self._samples.extend(batch.samples)
            self._source = batch.source
            self._configured_hz = batch.configured_hz
            self._effective_hz = batch.effective_hz
            self._last_batch_at = time.time()

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()
            self._source = self._configured_hz = self._effective_hz = self._last_batch_at = None

    def snapshot(self, seconds: float, max_points: int) -> dict:
        with self._lock:
            samples = list(self._samples)
            source, configured, effective = self._source, self._configured_hz, self._effective_hz
            last = self._last_batch_at
        if samples:
            cutoff = samples[-1].time - seconds
            first = len(samples) - 1
            while first > 0 and samples[first - 1].time >= cutoff:   # walk back from the newest
                first -= 1
            samples = samples[first:]
        step = max(1, math.ceil(len(samples) / max_points))
        picked = samples[::-1][::step][::-1]   # every step-th real sample, newest always kept
        return {
            "samples": [{name: _num(getattr(s, name)) for name in FIELDS} for s in picked],
            "source": source,
            "configured_hz": _num(configured),
            "effective_hz": _num(effective),
            "stale": not samples or last is None or time.time() - last > STALE_AFTER_S,
            "units": UNITS,
            "downsample": step,
        }


recent = RecentImu()
_subscribed_bus = None
_subscribe_lock = threading.Lock()


def _ensure_subscribed() -> None:
    """The bus has no unsubscribe: subscribe once per bus object (tests swap `event_bus.bus`)."""
    global _subscribed_bus
    with _subscribe_lock:
        bus = event_bus.bus
        if bus is not _subscribed_bus:
            bus.subscribe_imu(recent.on_batch)
            _subscribed_bus = bus


_ensure_subscribed()


def _fail(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _replay_running() -> bool:
    from .. import replay
    return bool(replay.replay_status().get("running"))


class ConnectBody(BaseModel):
    transport: Literal["ble", "serial", "sim"] = "ble"
    motion_hz: int = 50
    sensors: Literal["accel", "gyro", "both"] = "both"


class RecordStartBody(BaseModel):
    label: str
    tool_id: Optional[str] = None
    wearer: Optional[str] = None
    notes: str = ""


class ReplayStartBody(BaseModel):
    session_id: str
    speed: float = 1.0


@router.get("/status")
def status() -> dict:
    from .. import recorder, replay, vexo_bridge
    _ensure_subscribed()
    out = dict(vexo_bridge.get_bridge().status())
    from .. import browser_link
    if browser_link.active() and not out.get("connected"):
        out.update(browser_link.status())        # the dashboard holds the Bluetooth link
    replay_state = replay.replay_status()
    if not out.get("connected"):
        out["connected"] = False
        out["source"] = "replay" if replay_state.get("running") else None
    out["recording"] = recorder.get_recorder().status()
    out["replay"] = replay_state
    try:
        config.band_number()
        out["band_number_set"] = True
    except config.BandNumberMissing:
        out["band_number_set"] = False
    return out


@router.post("/connect")
def connect(body: Optional[ConnectBody] = None) -> dict:
    from .. import vexo_bridge
    _ensure_subscribed()
    body = body or ConnectBody()
    if _replay_running():
        raise _fail(409, "replay-running", "Stop the replay before connecting the band: live and replayed data must not mix.")
    try:
        return vexo_bridge.get_bridge().start(transport=body.transport, motion_hz=body.motion_hz,
                                              sensors=body.sensors)
    except config.BandNumberMissing as exc:
        raise _fail(409, "band-number-missing", str(exc))
    except vexo_bridge.BridgeError as exc:
        code = getattr(exc, "code", "bridge-error")
        raise _fail(409 if code == "already-running" else 502, code, getattr(exc, "message", str(exc)))
    except ValueError as exc:
        raise _fail(422, "bad-request", str(exc))


@router.post("/disconnect")
def disconnect() -> dict:
    from .. import vexo_bridge
    _ensure_subscribed()
    return vexo_bridge.get_bridge().stop()


@router.post("/record/start")
def record_start(body: RecordStartBody) -> dict:
    from .. import recorder, vexo_bridge
    _ensure_subscribed()
    bridge = vexo_bridge.get_bridge().status()
    live = bool(bridge.get("connected"))
    try:
        return recorder.get_recorder().start(
            body.label, tool_id=body.tool_id, wearer=body.wearer, notes=body.notes,
            band_name=bridge.get("band_name") if live else None,
            configured_hz=bridge.get("configured_hz") if live else None)
    except ValueError as exc:
        raise _fail(422, "bad-request", str(exc))
    except RuntimeError as exc:
        raise _fail(409, "already-recording", str(exc))


@router.post("/record/stop")
def record_stop() -> dict:
    from .. import recorder
    _ensure_subscribed()
    try:
        return recorder.get_recorder().stop()
    except RuntimeError as exc:
        raise _fail(409, "not-recording", str(exc))


@router.get("/recordings")
def recordings() -> dict:
    from .. import recorder
    _ensure_subscribed()
    return {"recordings": recorder.list_recordings()}


@router.post("/replay/start")
def replay_start(body: ReplayStartBody) -> dict:
    from .. import replay, vexo_bridge
    _ensure_subscribed()
    live = vexo_bridge.get_bridge().status()
    if live.get("connected") or live.get("reconnecting"):   # reconnecting: band data may resume any moment
        raise _fail(409, "band-connected", "Disconnect the band before starting a replay: live and replayed data must not mix.")
    try:
        return replay.start_replay(body.session_id, speed=body.speed)
    except FileNotFoundError as exc:
        raise _fail(404, "unknown-session", str(exc))
    except ValueError as exc:
        raise _fail(422, "bad-request", str(exc))


@router.post("/replay/stop")
def replay_stop() -> dict:
    from .. import replay
    _ensure_subscribed()
    return replay.stop_replay()


@router.get("/imu/recent")
def imu_recent(seconds: float = Query(5.0), max_points: int = Query(300)) -> dict:
    _ensure_subscribed()
    seconds = min(60.0, max(0.1, seconds)) if math.isfinite(seconds) else 5.0
    return recent.snapshot(seconds, min(2000, max(10, max_points)))


# ---- band connected in the browser (the dashboard's Connect Bluetooth button) -----------------
class BrowserHelloBody(BaseModel):
    band_name: Optional[str] = None
    band_number: Optional[int] = None
    firmware: Optional[str] = None
    motion_hz: Optional[float] = None
    battery: Optional[dict] = None


class BrowserSamplesBody(BaseModel):
    samples: list[dict]
    battery: Optional[dict] = None


@router.post("/browser/hello")
def browser_hello(body: BrowserHelloBody) -> dict:
    """The dashboard connected with the kit's JavaScript library and will post readings."""
    from .. import browser_link, vexo_bridge
    _ensure_subscribed()
    if _replay_running():
        raise _fail(409, "replay-running", "Stop the replay first: live and replayed data must not mix.")
    if vexo_bridge.get_bridge().status().get("connected"):
        raise _fail(409, "band-connected", "The Python bridge already holds this band.")
    try:
        return browser_link.hello(body.band_name, body.band_number, body.firmware, body.motion_hz, body.battery)
    except config.BandNumberMissing as exc:
        raise _fail(409, "band-number-missing", str(exc))
    except ValueError as exc:
        raise _fail(403, "wrong-band", str(exc))


@router.post("/browser/samples")
def browser_samples(body: BrowserSamplesBody) -> dict:
    from .. import browser_link
    try:
        return browser_link.ingest(body.samples, body.battery)
    except RuntimeError as exc:
        raise _fail(409, "not-connected", str(exc))


@router.post("/browser/bye")
def browser_bye(reason: str = "closed") -> dict:
    from .. import browser_link
    return browser_link.goodbye(reason)
