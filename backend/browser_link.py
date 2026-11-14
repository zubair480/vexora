"""Band readings that arrive from the dashboard's own Bluetooth connection.

The dashboard connects to the band in the browser with the Vexo kit's JavaScript library (the
same way the kit's dashboard does) and posts the readings here, so ToolSense, exposure and the
planner run on them. This module never connects to anything. Owned by the integration session.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Optional

from . import config, event_bus
from .models import Event, ImuBatch, ImuSample

STALE_S = 1.5

_lock = threading.Lock()
_state: dict[str, Any] = {
    "connected": False, "band_name": None, "firmware": None, "battery": None,
    "battery_state": None, "configured_hz": None, "received_samples": 0,
    "missing_samples": 0, "last_n": None, "last_seen": None, "last_error": None,
}
_times: deque = deque(maxlen=600)   # sample times (s) for the measured rate


def _publish(kind: str, payload: dict) -> None:
    ctx = config.current_shift()
    event_bus.bus.publish(Event(kind, payload, "band", ctx["shift_id"], ctx["worker_id"]))


def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def hello(band_name: Optional[str], band_number: Optional[int], firmware: Optional[str] = None,
          motion_hz: Optional[float] = None, battery: Optional[dict] = None) -> dict:
    """The dashboard says it has connected. Refused unless it is the team's own band."""
    expected = config.band_number()            # raises if VEXO_BAND is unset
    if band_number != expected:
        raise ValueError("That is not this team's band, so its readings are refused.")
    with _lock:
        _state.update(connected=True, band_name=band_name, firmware=firmware,
                      configured_hz=motion_hz, received_samples=0, missing_samples=0,
                      last_n=None, last_seen=time.time(), last_error=None)
        if battery:
            _state.update(battery=battery.get("percent"), battery_state=battery.get("state"))
        _times.clear()
    _publish("device_connected", {"band_name": band_name, "firmware": firmware, "battery": _state["battery"],
                                  "transport": "browser-ble", "reason": "connected from the dashboard"})
    return status()


def goodbye(reason: str = "closed") -> dict:
    with _lock:
        was = _state["connected"]
        _state["connected"] = False
    if was:
        _publish("device_disconnected", {"band_name": _state["band_name"], "firmware": _state["firmware"],
                                         "battery": _state["battery"], "transport": "browser-ble",
                                         "reason": reason})
    return status()


def ingest(samples: list[dict], battery: Optional[dict] = None) -> dict:
    """Readings in the JavaScript SDK's shape: `t` and `time` in milliseconds, accel g, gyro deg/s."""
    with _lock:
        if not _state["connected"]:
            raise RuntimeError("Send /api/band/browser/hello first.")
    out: list[ImuSample] = []
    missing = 0
    with _lock:
        last_n = _state["last_n"]
        for s in samples:
            n = s.get("n")
            n = int(n) if isinstance(n, (int, float)) else None
            if n is not None and last_n is not None and n > last_n + 1:
                missing += n - last_n - 1      # a jump in the counter = readings lost on the way
            if n is not None:
                last_n = n
            sample_time = _num(s.get("time")) / 1000.0
            out.append(ImuSample(n=n, t=_num(s.get("t")), time=sample_time,
                                 ax=_num(s.get("ax")), ay=_num(s.get("ay")), az=_num(s.get("az")),
                                 gx=_num(s.get("gx")), gy=_num(s.get("gy")), gz=_num(s.get("gz"))))
            _times.append(sample_time)
        _state["last_n"] = last_n
        _state["missing_samples"] += missing
        _state["received_samples"] += len(out)
        _state["last_seen"] = time.time()
        if battery:
            _state.update(battery=battery.get("percent"), battery_state=battery.get("state"))
        configured = _state["configured_hz"] or 0.0
    if out:
        event_bus.bus.publish_imu(ImuBatch(out, float(configured), _effective_hz(), "band"))
    return {"accepted": len(out), "missing_samples": missing}


def _effective_hz() -> float:
    if len(_times) < 2 or _times[-1] <= _times[0]:
        return 0.0
    return round((len(_times) - 1) / (_times[-1] - _times[0]), 2)


def status() -> dict:
    with _lock:
        s = dict(_state)
    received, missing = s["received_samples"], s["missing_samples"]
    stale = not s["connected"] or s["last_seen"] is None or time.time() - s["last_seen"] > STALE_S
    return {
        "connected": bool(s["connected"]), "reconnecting": False, "band_name": s["band_name"],
        "firmware": s["firmware"], "battery": s["battery"], "battery_state": s["battery_state"],
        "transport": "browser-ble", "configured_hz": s["configured_hz"],
        "effective_hz": _effective_hz(), "missing_samples": missing,
        "completeness": round(received / (received + missing), 4) if received + missing else None,
        "stale": stale, "source": "band" if s["connected"] else None,
        "received_samples": received, "last_error": s["last_error"],
    }


def active() -> bool:
    return bool(_state["connected"])
