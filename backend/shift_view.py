"""Backend state -> the dashboard's `ShiftData` shape (`app/src/data/types.ts`).

Owned by the integration session. Every value comes from a recorded event, a backend table or
the live bridge. A value with no real source is `None` (the UI shows "—"); nothing is invented.
Each module is imported behind a guard, so a missing module gives an absent section, not a crash.
"""
from __future__ import annotations

import importlib
import time
from typing import Any, Optional

from . import config, database

LABEL = "Estimated HAV exposure — screening/planning, not certified measurement"
STATION_POSITIONS = {"S1": 0.06, "S2": 0.28, "S3": 0.5, "S4": 0.72, "S5": 0.94}
EVENT_KIND = {
    "inspection_recorded": "inspection", "defect_reported": "defect",
    "work_order_created": "workorder", "tool_session_ended": "tool",
    "exposure_updated": "exposure", "planning_recommendation": "exposure",
    "device_connected": "vibration", "device_disconnected": "vibration",
}


def _module(name: str):
    try:
        return importlib.import_module(f"backend.{name}")
    except Exception:
        return None


def _call(module, name: str, *args, default=None, **kwargs):
    try:
        return getattr(module, name)(*args, **kwargs) if module else default
    except Exception:
        return default


def _hhmm(ts: Optional[float]) -> Optional[str]:
    return time.strftime("%H:%M", time.localtime(ts)) if ts else None


def _title(asset_id: Optional[str]) -> Optional[str]:
    return asset_id.replace("-", " ").title() if asset_id else None


def _band() -> dict:
    bridge_mod = _module("vexo_bridge")
    status = {}
    if bridge_mod:
        try:
            status = bridge_mod.get_bridge().status()
        except Exception:
            status = {}
    replay = _module("replay")
    replay_on = bool(_call(replay, "is_running", default=False))
    number = None
    try:
        number = config.band_number()
    except config.BandNumberMissing:
        pass
    connected = bool(status.get("connected"))
    return {
        "source": "backend",
        "replay": replay_on,
        "number": number,
        "name": status.get("band_name"),
        "connected": connected,
        "reconnecting": bool(status.get("reconnecting")),
        "live": connected and not status.get("stale", True),
        "firmware": status.get("firmware"),
        "motionRate": status.get("configured_hz"),
        "effectiveRate": status.get("effective_hz"),
        "missingSamples": status.get("missing_samples"),
        "completeness": status.get("completeness"),
        "battery": ({"percent": status["battery"], "state": status.get("battery_state"),
                     "charging": status.get("battery_state") == "charging"}
                    if status.get("battery") is not None else None),
        "rssi": None,
        "lastGesture": None,
        "samples": [],            # the chart reads /api/band/imu/recent
        "accelRms": None,
        "gyroRms": None,
        "error": status.get("last_error"),
    }


def _activity() -> Optional[dict]:
    sessions = _module("tool_sessions")
    if not sessions:
        return None
    try:
        return sessions.get_service().state()
    except Exception:
        return None


def build(shift_id: Optional[str] = None) -> dict[str, Any]:
    ctx = config.current_shift()
    shift_id = shift_id or ctx["shift_id"]
    assets = _module("assets")
    inspections = _module("inspection_agent")
    orders = _module("work_orders")
    exposure = _module("exposure")
    planner = _module("planner")
    tasks = _module("tasks")
    profiles = _module("tool_profiles")
    tool_sessions = _module("tool_sessions")

    defects = _call(inspections, "list_defects", shift_id, default=[]) or []
    open_defects = [d for d in defects if d.get("verification_status") != "rejected"]
    work_orders = _call(orders, "list_work_orders", shift_id, default=[]) or []
    inspected = {i.get("station_id") for i in _call(inspections, "list_inspections", shift_id, default=[]) or []}
    defect_stations = {d.get("station_id") for d in open_defects}

    stations = []
    for index, row in enumerate(_call(assets, "list_stations", ctx["conveyor_id"], default=[]) or [], start=1):
        sid = row.get("station_id")
        defect = next((d for d in open_defects if d.get("station_id") == sid), None)
        stations.append({
            "id": sid, "index": index, "name": f"Station {index}", "role": row.get("name"),
            "status": "defect" if sid in defect_stations else "ok",
            "inspected": sid in inspected,
            "position": STATION_POSITIONS.get(sid, index / 6),
            "asset": _title(defect.get("asset_id")) if defect else None,
            "note": (f"{defect.get('condition')} · unverified worker report" if defect else
                     ("Inspected this shift" if sid in inspected else "Not inspected this shift")),
            "vibrationDelta": None,       # no asset vibration sensor exists
        })

    result = _call(exposure, "compute", shift_id, default=None)
    total = result.get("total_points") if result else None
    recs = _call(planner, "list_recommendations", shift_id, default=[]) or []
    rec = recs[-1] if recs else None
    if rec and isinstance(rec.get("payload"), dict):
        rec = {**rec, **rec["payload"]}

    sessions = _call(tool_sessions, "list_sessions", shift_id, default=[]) or []
    activity = _activity()
    tool = None
    last = next((s for s in reversed(sessions) if s.get("tool_id")), None)
    if last:
        profile = _call(profiles, "get", last["tool_id"], default=None)
        seconds = sum(float(s.get("duration_s") or 0) for s in sessions if s.get("tool_id") == last["tool_id"]
                      and s.get("confirmation") != "rejected")
        tool = {
            "id": last["tool_id"], "name": getattr(profile, "name", None) or last["tool_id"], "type": None,
            "aHv": getattr(profile, "a_hv_ms2", None), "aHvSource": getattr(profile, "source", None),
            "minutesToday": round(seconds / 60, 1),
            "inUse": bool(activity and activity.get("in_session")),
            "confirmation": last.get("confirmation"),
        }

    defect = open_defects[-1] if open_defects else None
    order = work_orders[-1] if work_orders else None
    events = []
    for e in database.list_events(shift_id):
        kind = EVENT_KIND.get(e["type"])
        if not kind:
            continue
        p = e["payload"]
        events.append({
            "id": e["id"], "kind": kind, "type": e["type"], "source": e["source"],
            "start": _hhmm(p.get("start_ts") or e["ts"]), "end": _hhmm(p.get("end_ts")),
            "ts": e["ts"], "stationId": e.get("station_id"), "assetId": e.get("asset_id"),
            "title": e["type"].replace("_", " ").capitalize(),
            "detail": (p.get("condition") or p.get("task") or p.get("transcript")
                       or (f"{p['duration_s']:.0f} s, {p.get('confirmation')}" if p.get("duration_s") is not None else None)
                       or (f"{p['total_points']} pts (estimated)" if p.get("total_points") is not None else None)
                       or (f"projected {p['projected_points']} pts" if p.get("projected_points") is not None else None)
                       or p.get("reason") or ""),
        })

    workers = []
    for w in _call(tasks, "list_workers", default=[]) or []:
        mine = w.get("worker_id") == ctx["worker_id"]
        name = w.get("name") or w.get("worker_id")
        workers.append({
            "id": w.get("worker_id"), "name": name, "role": None,
            "initials": "".join(part[0] for part in str(name).split()[:2]).upper(),
            "onTask": mine, "exposurePoints": total if mine else None,   # only the band wearer is measured
        })

    return {
        "generatedAt": time.time(),
        "shiftId": shift_id,
        "replay": _band()["replay"] or bool(result and result.get("contains_simulated")),
        "conveyor": {"id": ctx["conveyor_id"], "name": None, "site": None, "pit": None, "lengthM": None,
                     "idlers": None, "inService": None, "beltHealth": None, "stations": stations},
        "shift": {"label": None, "start": None, "end": None},
        "workers": workers,
        "tool": tool,
        "activity": activity,
        "exposure": {
            "label": LABEL, "current": total,
            "projected": rec.get("projected_points") if rec else None,
            "action": config.ACTION_POINTS, "limit": config.LIMIT_POINTS,
            "a8": result.get("a8") if result else None,
            "status": result.get("status") if result else "unavailable",
            "level": result.get("level") if result else None,
            "entries": result.get("entries") if result else [],
            "reasons": result.get("reasons") if result else ["Exposure engine not available"],
        },
        "defect": ({
            "id": defect["defect_id"], "title": f"{_title(defect['asset_id'])}: {defect['condition']}",
            "asset": _title(defect["asset_id"]), "assetId": defect["asset_id"],
            "stationId": defect.get("station_id"), "severity": defect.get("severity"),
            "detail": defect.get("requested_action") or "", "detectedAt": _hhmm(defect.get("created_ts")),
            "verificationStatus": defect.get("verification_status"), "source": defect.get("source"),
        } if defect else None),
        "workOrder": ({
            "id": order["work_order_id"], "status": order["status"],
            "asset": f"{order.get('conveyor_id') or ctx['conveyor_id']} — {order.get('station_id')} / {_title(order['asset_id'])}",
            "task": order["task"], "priority": order["priority"], "planned": None, "assignee": None,
            "defectId": order.get("defect_id"),
        } if order else None),
        "recommendation": ({
            "id": rec.get("recommendation_id"),
            "headline": (f"Next planned task could raise estimated exposure to {rec.get('projected_points')} points — review required."
                         if rec.get("crosses_action") or rec.get("crosses_limit")
                         else f"Next planned task: estimated exposure {rec.get('projected_points')} points."),
            "detail": " ".join(rec.get("reasons") or []),
            "projectedPoints": rec.get("projected_points"), "deltaPoints": rec.get("additional_points"),
            "currentPoints": rec.get("current_points"), "taskId": rec.get("task_id"),
            "taskTitle": rec.get("task_title"), "options": rec.get("options") or [],
            "requiresApproval": True, "status": rec.get("status"),
            "primaryCta": "Review & Plan", "secondaryCta": "View Details",
        } if rec else None),
        "band": _band(),
        "events": events,
        "kpis": {
            "beltHealth": None,
            "activeDefects": len(open_defects),
            "workersOnTask": 1 if _band()["connected"] else None,
            "vibrationRisk": ({"below": "Normal", "action": "Elevated", "limit": "High"}.get(
                str(result.get("level"))) if result else None),
            "exposurePoints": total,
        },
    }
