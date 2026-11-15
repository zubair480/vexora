"""BeltGuard exposure-aware planning. Owner: S4 hav-brain.

recommend(current_points, planned, tool_profiles) projects the HSE exposure points a planned
repair would add to the technician's day. When that raises a planning concern it lists
options for a supervisor to review: a lower-vibration method, splitting the task across
shifts, or qualified reassignment.

Nothing here is automatic. BeltGuard never reassigns people, never changes a safety
procedure and never authorizes work. Every result says "requires_supervisor_review": True.
SCREENING ESTIMATE, NOT A CERTIFIED HAV MEASUREMENT.
"""
from __future__ import annotations

import math
from typing import Optional

try:  # works as backend.planner and as a top-level module with backend/ on sys.path
    from . import exposure  # type: ignore
except ImportError:
    import exposure  # type: ignore

CONCERN_MARGIN = 20  # points below the action value that already count as a concern
REVIEW_NOTE = "Supervisor review required. BeltGuard does not reassign people, change procedures or authorize work."


def _fmt(points: float) -> str:
    return "%.1f" % points if points < 10 else "%.0f" % points


def _number(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def recommend(current_points: float, planned: dict, tool_profiles: list[dict]) -> dict:
    current = max(0.0, _number(current_points) or 0.0)
    planned = dict(planned or {})
    tools = list(tool_profiles or []) or exposure.load_tool_profiles()
    tool = exposure.get_tool(planned.get("tool_id"), tools)
    base = {
        "current_points": round(current, 2),
        "action": exposure.ACTION_POINTS,
        "limit": exposure.LIMIT_POINTS,
        "station_id": planned.get("station_id"),
        "task": planned.get("task"),
        "work_order_id": planned.get("work_order_id"),
        "requires_supervisor_review": True,
        "automatic_actions": [],
        "label": exposure.SCREENING_LABEL,
        "method": exposure.METHOD,
        "source": "rules",
    }

    if tool is None:
        return dict(base, status="concern", reason="unknown_tool", added_points=0.0, projected_points=round(current, 2),
                    headline="No tool profile for '%s': exposure for this job cannot be projected." % planned.get("tool_id"),
                    actions=["Confirm the tool and its vibration magnitude before planning this job.", REVIEW_NOTE],
                    action_details=[{"kind": "confirm_tool", "text": "Confirm the tool and its vibration magnitude."}],
                    tool=None, est_trigger_min=None, alternative=None)

    a = float(tool["vibration_m_s2"])
    est = _number(planned.get("est_trigger_min"))
    basis = "planned trigger time"
    if est is None:
        est = _number(tool.get("typical_trigger_min")) or 10.0
        basis = "tool profile typical trigger time (planning assumption)"
    added = exposure.points(a, est / 60.0)
    projected = current + added
    left_action = max(0.0, exposure.ACTION_POINTS - current)
    left_limit = max(0.0, exposure.LIMIT_POINTS - current)
    min_to_action = exposure.minutes_for_points(a, left_action)
    min_to_limit = exposure.minutes_for_points(a, left_limit)

    if projected >= exposure.LIMIT_POINTS:
        status, reason = "over_limit", "projected_above_limit"
    elif projected >= exposure.ACTION_POINTS:
        status, reason = "concern", "projected_above_action"
    elif projected >= exposure.ACTION_POINTS - CONCERN_MARGIN:
        status, reason = "concern", "projected_near_action"
    else:
        status, reason = "ok", "below_action"

    name = tool.get("name") or tool["id"]
    job = "%s for %s min" % (name, _fmt(est))
    if status == "ok":
        headline = "%s adds ~%s pts: day projected at %s pts, below the 100-point action value." % (
            job, _fmt(added), _fmt(projected))
    elif reason == "projected_near_action":
        headline = "Close to the action value: %s takes the day to ~%s of 100 pts." % (job, _fmt(projected))
    elif status == "concern":
        headline = "%s would take the day to ~%s pts, above the 100-point action value." % (job, _fmt(projected))
    else:
        headline = "%s would take the day to ~%s pts, above the 400-point limit value." % (job, _fmt(projected))

    alternative = None
    alt_spec = tool.get("lower_vibration_alternative") or None
    if alt_spec:
        alt_tool = exposure.get_tool(alt_spec.get("tool_id"), tools) if alt_spec.get("tool_id") else None
        alternative = {"tool_id": alt_spec.get("tool_id"), "method": alt_spec.get("method"), "note": alt_spec.get("note")}
        if alt_tool is not None:
            alt_added = exposure.points(float(alt_tool["vibration_m_s2"]), est / 60.0)
            alternative.update({"tool_name": alt_tool.get("name"), "vibration_m_s2": alt_tool["vibration_m_s2"],
                                "added_points": round(alt_added, 2), "projected_points": round(current + alt_added, 2),
                                "assumes": "same trigger time; a slower method may need longer"})

    details = []
    if status == "ok":
        details.append({"kind": "proceed_note", "text": "No exposure concern projected for this job (screening estimate). "
                                                        "The band will update the estimate from the real trigger time."})
        if alternative and alternative.get("projected_points") is not None and alternative["added_points"] < added / 2:
            details.append({"kind": "lower_vibration", "tool_id": alternative.get("tool_id"),
                            "projected_points": alternative["projected_points"],
                            "text": "Optional lower-vibration method: %s (~%s pts instead of %s)."
                                    % (alternative["method"], _fmt(alternative["added_points"]), _fmt(added))})
    else:
        if status == "over_limit":
            details.append({"kind": "replan", "text": "Projected total is above the 400-point exposure limit value, which UK "
                                                      "regulations say must not be exceeded. Re-plan with the supervisor before work starts."})
        if alternative:
            if alternative.get("projected_points") is not None:
                text = "Review a lower-vibration method: %s, about %s pts for the day instead of %s." % (
                    alternative["method"], _fmt(alternative["projected_points"]), _fmt(projected))
            else:
                text = "Review a lower-vibration method: %s." % alternative["method"]
            details.append({"kind": "lower_vibration", "tool_id": alternative.get("tool_id"),
                            "projected_points": alternative.get("projected_points"), "text": text})
        if left_action > 0 and math.isfinite(min_to_action):
            details.append({"kind": "split_shifts", "minutes_today": round(min_to_action, 1),
                            "text": "Split the task: about %s min of trigger time on the %s keeps today under the "
                                    "100-point action value; plan the rest for the next shift."
                                    % (_fmt(min_to_action), name.lower())})
        else:
            details.append({"kind": "minimise", "text": "Today is already at or above the action value: keep further "
                                                        "vibrating-tool use to a minimum and review controls."})
        details.append({"kind": "reassign", "text": "Consider reassignment to a qualified technician with lower exposure "
                                                    "today (supervisor decision: check competence and their own exposure)."})
    details.append({"kind": "review", "text": REVIEW_NOTE})

    return dict(base, status=status, reason=reason, headline=headline,
                actions=[d["text"] for d in details], action_details=details,
                added_points=round(added, 2), projected_points=round(projected, 2),
                tool={"id": tool["id"], "name": name, "vibration_m_s2": a, "source": tool.get("source"),
                      "demo_stand_in": bool(tool.get("demo_stand_in"))},
                est_trigger_min=round(est, 2), trigger_basis=basis,
                minutes_to_action_on_tool=round(min_to_action, 1) if math.isfinite(min_to_action) else None,
                minutes_to_limit_on_tool=round(min_to_limit, 1) if math.isfinite(min_to_limit) else None,
                alternative=alternative)
