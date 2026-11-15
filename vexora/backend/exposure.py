"""BeltGuard hand-arm vibration (HAV) exposure maths. Owner: S4 hav-brain.

SCREENING ESTIMATE, NOT A CERTIFIED HAV MEASUREMENT.

UK HSE exposure points system (Control of Vibration at Work Regulations 2005, HSE L140):

    points = 2 x a^2 x T

    a  the tool's vibration magnitude in m/s2, taken from the selected tool profile
       (tool_profiles.json records the source of every value)
    T  trigger time in hours, estimated from the tool-use episode the wrist IMU detected

    100 points = exposure action value (EAV), the same as A(8) = 2.5 m/s2
    400 points = exposure limit value  (ELV), the same as A(8) = 5.0 m/s2

The Vexo band is not a vibration meter. Its accelerometer detects candidate tool use and
times it. It never measures the hand-transmitted vibration that the points depend on.

Records passed to daily_summary() may carry a "status":
    counted:   "confirmed", or no status at all (a plain exposure record)
    pending:   "unresolved", "active", "pending"  (shown separately until the technician confirms)
    excluded:  "rejected"
"""
from __future__ import annotations

import json
import math
import os
from typing import Iterable, Optional

ACTION_POINTS = 100
LIMIT_POINTS = 400
ACTION_A8_M_S2 = 2.5
LIMIT_A8_M_S2 = 5.0

SCREENING_LABEL = "SCREENING ESTIMATE - not a certified HAV measurement"
METHOD = ("HSE points = 2 x a^2 x T(h). a comes from the selected tool profile (source recorded); "
          "T is the trigger time the wrist IMU detected. The band does not measure vibration at the hand.")

PROFILES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tool_profiles.json")

_PENDING = {"unresolved", "active", "pending"}
_EXCLUDED = {"rejected"}


def _number(value, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be a number, got %r" % (name, value)) from None
    if not math.isfinite(number) or number < 0:
        raise ValueError("%s must be a finite number >= 0, got %r" % (name, value))
    return number


def points(vibration_m_s2: float, trigger_hours: float) -> float:
    """HSE exposure points for one tool-use episode: 2 x a^2 x T(hours)."""
    a = _number(vibration_m_s2, "vibration_m_s2")
    t = _number(trigger_hours, "trigger_hours")
    return 2.0 * a * a * t


def minutes_for_points(vibration_m_s2: float, pts: float) -> float:
    """Trigger minutes on a tool of magnitude `vibration_m_s2` that add `pts` points.
    Returns math.inf for a tool with no vibration."""
    a = _number(vibration_m_s2, "vibration_m_s2")
    p = _number(pts, "pts")
    if a == 0:
        return math.inf
    return 60.0 * p / (2.0 * a * a)


def points_per_hour(vibration_m_s2: float) -> float:
    return points(vibration_m_s2, 1.0)


def a8_from_points(pts: float) -> float:
    """Daily exposure A(8) in m/s2 that corresponds to a points total (100 pts = 2.5 m/s2)."""
    return ACTION_A8_M_S2 * math.sqrt(_number(pts, "pts") / ACTION_POINTS)


def status_for(pts: float) -> str:
    """"below_action" | "above_action" | "above_limit". Reaching a value counts as above it."""
    if pts >= LIMIT_POINTS:
        return "above_limit"
    if pts >= ACTION_POINTS:
        return "above_action"
    return "below_action"


def _record_points(record: dict) -> float:
    if record.get("points") is not None:
        return _number(record["points"], "points")
    a = record.get("vibration_m_s2")
    if a is None:
        raise ValueError("an exposure record needs 'points' or 'vibration_m_s2' plus a trigger time: %r" % record)
    if record.get("trigger_hours") is not None:
        hours = _number(record["trigger_hours"], "trigger_hours")
    elif record.get("trigger_s") is not None:
        hours = _number(record["trigger_s"], "trigger_s") / 3600.0
    elif record.get("trigger_min") is not None:
        hours = _number(record["trigger_min"], "trigger_min") / 60.0
    else:
        raise ValueError("an exposure record needs trigger_hours, trigger_s or trigger_min: %r" % record)
    return points(a, hours)


def _trigger_s(record: dict) -> float:
    for key, scale in (("trigger_s", 1.0), ("trigger_min", 60.0), ("trigger_hours", 3600.0)):
        if record.get(key) is not None:
            try:
                return float(record[key]) * scale
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _minutes_to(target: float, total: float, vibration: Optional[float]) -> Optional[float]:
    if total >= target:
        return 0.0
    if not vibration:
        return None
    return round(minutes_for_points(vibration, target - total), 1)


def daily_summary(records: list[dict], next_vibration_m_s2: Optional[float] = None) -> dict:
    """Sum a shift's exposure records into one screening summary.

    minutes_to_action / minutes_to_limit are trigger minutes left on the "basis" tool: the
    one given in next_vibration_m_s2, else the most recent record's tool."""
    total = pending = 0.0
    counted = pending_n = 0
    by_tool: dict = {}
    basis_vibration = next_vibration_m_s2
    basis_tool = None
    for record in records or []:
        status = str(record.get("status") or "").lower()
        if status in _EXCLUDED:
            continue
        pts = _record_points(record)
        if status in _PENDING:
            pending += pts
            pending_n += 1
        else:
            total += pts
            counted += 1
            tool_id = record.get("tool_id") or "unknown"
            entry = by_tool.setdefault(tool_id, {"points": 0.0, "trigger_s": 0.0, "sessions": 0})
            entry["points"] = round(entry["points"] + pts, 3)
            entry["trigger_s"] = round(entry["trigger_s"] + _trigger_s(record), 1)
            entry["sessions"] += 1
        if next_vibration_m_s2 is None and record.get("vibration_m_s2") is not None:
            basis_vibration = float(record["vibration_m_s2"])
            basis_tool = record.get("tool_id")
    worst = total + pending
    return {
        "total_points": round(total, 2),
        "pending_points": round(pending, 2),
        "worst_case_points": round(worst, 2),
        "action": ACTION_POINTS,
        "limit": LIMIT_POINTS,
        "status": status_for(total),
        "worst_case_status": status_for(worst),
        "a8_m_s2": round(a8_from_points(total), 2),
        "minutes_to_action": _minutes_to(ACTION_POINTS, total, basis_vibration),
        "minutes_to_limit": _minutes_to(LIMIT_POINTS, total, basis_vibration),
        "basis_tool_id": basis_tool,
        "basis_vibration_m_s2": basis_vibration,
        "by_tool": by_tool,
        "sessions_counted": counted,
        "sessions_pending": pending_n,
        "label": SCREENING_LABEL,
        "method": METHOD,
    }


def exposure_record(tool: dict, trigger_s: float, *, status: str = "confirmed",
                    confidence: Optional[float] = None, tool_session_id=None) -> dict:
    """One exposure record for a tool session: the selected tool profile x the detected trigger time."""
    a = _number(tool.get("vibration_m_s2"), "vibration_m_s2")
    seconds = _number(trigger_s, "trigger_s")
    return {
        "tool_session_id": tool_session_id,
        "tool_id": tool.get("id"),
        "tool_name": tool.get("name"),
        "vibration_m_s2": a,
        "vibration_source": tool.get("source"),
        "demo_stand_in": bool(tool.get("demo_stand_in")),
        "trigger_s": round(seconds, 1),
        "trigger_hours": seconds / 3600.0,
        "points": round(points(a, seconds / 3600.0), 3),
        "status": status,
        "confidence": confidence,
        "method": METHOD,
        "label": SCREENING_LABEL,
    }


def load_tool_profiles(path: str = PROFILES_PATH) -> list[dict]:
    """The tool list from tool_profiles.json (a {"tools": [...]} object or a bare list)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    tools = data.get("tools", []) if isinstance(data, dict) else data
    return [tool for tool in tools if isinstance(tool, dict) and tool.get("id")]


def get_tool(tool_id: str, tool_profiles: Iterable[dict]) -> Optional[dict]:
    for tool in tool_profiles or []:
        if tool.get("id") == tool_id:
            return tool
    return None
