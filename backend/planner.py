"""HAV planning recommendations (be-hav).

`evaluate` projects what a planned task would add to the current estimated exposure (UK HSE
points method) and lists options for a supervisor to review. It never approves anything, never
reassigns anyone and never invents a vibration value: a magnitude only ever comes from a
configured `ToolProfile.a_hv_ms2`. A recommendation stays `pending` until a human calls `decide`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
import weakref
from typing import Optional

from . import config, database, event_bus, exposure, tasks, tool_profiles
from .models import Event

log = logging.getLogger("beltguard.planner")

database.register_schema(
    """
    CREATE TABLE IF NOT EXISTS hav_recommendations (
        id TEXT PRIMARY KEY, shift_id TEXT NOT NULL, task_id TEXT NOT NULL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL, payload TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', decided_by TEXT, decided_at REAL,
        decision_note TEXT
    );
    CREATE INDEX IF NOT EXISTS hav_recommendations_task
        ON hav_recommendations (shift_id, task_id, status);
    """
)

DECISIONS = ("approved", "dismissed")
REVIEW_KINDS = ("review_tool_choice", "review_work_method", "review_task_duration",
                "review_engineering_controls")

_write_lock = threading.Lock()
_installed: "weakref.WeakSet" = weakref.WeakSet()


def _r2(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 2)


def _fmt(value: float) -> str:
    return f"{value:.1f}"


def _number(value) -> Optional[float]:
    """A finite float, or None. Never a guess."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _option(kind: str, description: str, **extra) -> dict:
    option = {"kind": kind, "description": description, "requires_approval": True}
    option.update(extra)
    return option


def _review_options(tool_name: Optional[str], minutes: float) -> list[dict]:
    tool = tool_name or "the planned tool"
    return [
        _option("review_tool_choice",
                f"Review whether {tool} is the right tool for this task or whether a "
                "lower-vibration tool with a configured value can do the job."),
        _option("review_work_method",
                "Review the work method: check whether the job can be done with less or no "
                "hand-held powered tool use."),
        _option("review_task_duration",
                f"Review the planned trigger time ({_fmt(minutes)} min): check whether it can be "
                "shortened or split across the plan."),
        _option("review_engineering_controls",
                "Review engineering controls: tool condition and maintenance, consumables, "
                "jigs or supports that reduce grip force and trigger time."),
    ]


def _alternative_options(task: dict, minutes: float, current: Optional[float]) -> list[dict]:
    """Only alternatives a person configured for this tool; nothing is inferred."""
    options: list[dict] = []
    for alt in tasks.alternatives_for(task.get("tool_id")):
        note = f" Note: {alt['note']}." if alt.get("note") else ""
        if alt.get("kind") == "tool" and alt.get("alt_tool_id"):
            profile = tool_profiles.get(alt["alt_tool_id"])
            alt_name = profile.name if profile else alt["alt_tool_id"]
            alt_a = _number(profile.a_hv_ms2) if profile else None
            alt_additional = alt_projected = None
            if alt_a is not None:
                alt_additional = exposure.points(alt_a, minutes * 60.0)
                if current is not None:
                    alt_projected = current + alt_additional
                description = (
                    f"Configured alternative tool: {alt_name} at {alt_a:g} m/s² for "
                    f"{_fmt(minutes)} min would add an estimated {_fmt(alt_additional)} points"
                    + (f" (projected {_fmt(alt_projected)} points)" if alt_projected is not None else "")
                    + ". A supervisor must confirm it is available and suitable." + note)
            else:
                description = (
                    f"Configured alternative tool: {alt_name}. Its vibration value is not "
                    "configured, so no projection is given; enter the manufacturer's declared or "
                    "measured value first." + note)
            options.append(_option(
                "alternative_tool", description,
                alternative_id=alt["id"], alt_tool_id=alt["alt_tool_id"], alt_tool_name=alt_name,
                a_hv_ms2=alt_a, a_hv_source=profile.source if profile else None,
                additional_points=_r2(alt_additional), projected_points=_r2(alt_projected),
                source=alt.get("source", "configured")))
        elif alt.get("kind") == "method" and alt.get("method"):
            options.append(_option(
                "alternative_method",
                f"Configured alternative method: {alt['method']}. No exposure figure is "
                "projected for it; a supervisor must review it." + note,
                alternative_id=alt["id"], method=alt["method"],
                source=alt.get("source", "configured")))
    return options


def _reassign_options(task: dict) -> tuple[list[dict], list[dict]]:
    """Reassignment is offered only to a verified, qualified worker. Never automatic."""
    options: list[dict] = []
    blocked: list[dict] = []
    current_worker = config.current_shift()["worker_id"]
    required = task.get("required_qualification")
    for worker in tasks.list_workers():
        if worker["worker_id"] == current_worker:
            continue
        base = {"kind": "reassign", "worker_id": worker["worker_id"],
                "worker_name": worker["name"], "requires_approval": True}
        quals = worker["qualifications"]
        if quals is None:
            reason = (f"{worker['name']}: qualifications are unknown, so reassignment is not "
                      "offered. A supervisor must record and verify them first.")
        elif not worker["qualifications_verified"]:
            reason = (f"{worker['name']}: qualifications are not verified, so reassignment is "
                      "not offered. A supervisor must verify them first.")
        elif required and required not in quals:
            reason = (f"{worker['name']}: does not hold the required qualification "
                      f"'{required}', so reassignment is not offered.")
        else:
            reason = None
        if reason is not None:
            blocked.append({**base, "reason": reason, "description": reason})
            continue
        qualification = f"holds the required qualification '{required}' (verified)" if required \
            else "has verified qualifications (this task lists no required qualification)"
        options.append({**base, "description": (
            f"Reassign this task to {worker['name']}, who {qualification}. "
            f"{worker['name']}'s own vibration exposure is not tracked here and must be checked "
            "by the supervisor before any reassignment.")})
    return options, blocked


def _source_of(current_result: dict) -> str:
    try:
        return exposure.event_source(current_result)
    except Exception:
        return "derived"


def _revision(payload: dict) -> str:
    """Fingerprint of what a reviewer sees; a decision must quote it (see `decide`)."""
    body = {k: v for k, v in payload.items() if k not in ("recommendation_id", "revision")}
    return hashlib.sha1(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def _build(shift_id: str, task: dict) -> dict:
    action, limit = config.ACTION_POINTS, config.LIMIT_POINTS
    current_result = exposure.compute(shift_id) or {}       # pure read: never recompute here
    current = _number(current_result.get("total_points_exact"))   # unrounded: no hidden crossing
    if current is None:
        current = _number(current_result.get("total_points"))
    contains_simulated = bool(current_result.get("contains_simulated"))
    provenance_unknown = bool(current_result.get("provenance_unknown"))
    current_status = current_result.get("status") or "unavailable"

    tool_id = task.get("tool_id")
    minutes = _number(task.get("expected_trigger_minutes")) or 0.0
    profile = tool_profiles.get(tool_id) if tool_id else None
    tool_name = profile.name if profile else tool_id
    a_hv = _number(profile.a_hv_ms2) if profile else None
    a_hv_source = profile.source if profile else None

    reasons: list[str] = []
    title = task.get("title") or task["task_id"]

    # additional points
    additional: Optional[float]
    if minutes > 0 and not tool_id:
        additional = None
        reasons.append(f"No projection: task '{title}' plans {_fmt(minutes)} min of trigger time "
                       "but names no tool. A person must say which tool will be used; no value "
                       "is assumed.")
    elif minutes <= 0:
        additional = 0.0
        reasons.append(f"Task '{title}' has no planned powered-tool trigger time, so it adds "
                       "0 points on this estimate.")
    elif a_hv is None:
        additional = None
        what = f"{tool_name} has no vibration value configured" if profile \
            else f"tool '{tool_id}' has no tool profile"
        reasons.append(f"No projection: {what}. A person must enter the manufacturer's declared "
                       "or measured vibration value; no value is assumed.")
    else:
        additional = exposure.points(a_hv, minutes * 60.0)
        reasons.append(f"Task '{title}': {tool_name} at {a_hv:g} m/s² for {_fmt(minutes)} min "
                       f"adds an estimated {_fmt(additional)} points (2 × a² × hours).")

    if current is None:
        reasons.append("No projection: the current exposure estimate is unavailable. A person "
                       "must supply or confirm the missing tool-use records before a figure "
                       "can be given.")
    elif current_status != "ok":
        reasons.append(f"The current figure ({_fmt(current)} points) is partial: some tool use "
                       "is unconfirmed or has no points yet, so the real exposure may be higher.")

    if contains_simulated:
        reasons.append("The current figure includes simulated (replay/synthetic) data: it is not "
                       "a live recording of this shift.")
    if provenance_unknown:
        reasons.append("Some tool-use records have no recorded provenance: not confirmed as live "
                       "band data.")

    projection_available = current is not None and additional is not None
    projected = crosses_action = crosses_limit = None
    if projection_available:
        projected = current + additional
        crosses_action = bool(current < action <= projected)
        crosses_limit = bool(current < limit <= projected)
        reasons.append(f"Current estimate {_fmt(current)} points + {_fmt(additional)} = "
                       f"projected {_fmt(projected)} points.")
        if projected >= limit:
            reasons.append(
                f"The projected estimate reaches the exposure limit value ({limit:g} points). "
                "The plan needs supervisor review before proceeding.")
        elif projected >= action:
            lead = "The projected estimate crosses" if crosses_action \
                else "The estimate is already at or above"
            reasons.append(
                f"{lead} the exposure action value ({action:g} points). The plan needs "
                "supervisor review before proceeding, and exposure should be reduced as low as "
                "reasonably practicable.")
        else:
            reasons.append(
                f"The projected estimate stays below the exposure action value ({action:g} "
                "points) on this estimate. Exposure should still be kept as low as reasonably "
                "practicable.")

    options: list[dict] = []
    blocked: list[dict] = []
    if not projection_available or projected >= action:
        options.extend(_review_options(tool_name, minutes))
        options.extend(_alternative_options(task, minutes, current))
        reassign, blocked = _reassign_options(task)
        options.extend(reassign)
    reasons.append("This is a planning estimate. Nothing is approved automatically: a "
                   "supervisor must decide.")

    return {
        "recommendation_id": None,
        "label": exposure.LABEL,
        "shift_id": shift_id,
        "task_id": task["task_id"],
        "task_title": task.get("title"),
        "tool_id": tool_id,
        "tool_name": tool_name,
        "a_hv_ms2": a_hv,
        "a_hv_source": a_hv_source,
        "expected_trigger_minutes": minutes,
        "current_points": _r2(current),
        "current_status": current_status,
        "contains_simulated": contains_simulated,
        "provenance_unknown": provenance_unknown,
        "current_sources": list(current_result.get("sources") or []),
        "_event_source": _source_of(current_result),
        "additional_points": _r2(additional),
        "projected_points": _r2(projected),
        "projected_a8": _r2(exposure.a8(projected)) if projected is not None else None,
        "projected_level": exposure.level(projected),
        "projection_available": projection_available,
        "crosses_action": crosses_action,
        "crosses_limit": crosses_limit,
        "reasons": reasons,
        "options": options,
        "blocked_options": blocked,
        "requires_approval": True,
        "status": "pending",
        "decision": None,
    }


def evaluate(shift_id: str, task_id: str, publish: bool = True) -> dict:
    """Project the task onto the current estimate and store/update the pending recommendation."""
    task = tasks.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    now = time.time()
    with _write_lock:
        # Built inside the lock: an older projection never overwrites a newer one.
        payload = _build(shift_id, task)
        source = payload.pop("_event_source", "derived")
        payload["revision"] = _revision(payload)
        con = database.connect()
        try:
            with con:
                row = con.execute(
                    "SELECT id FROM hav_recommendations WHERE shift_id = ? AND task_id = ? "
                    "AND status = 'pending' ORDER BY created_at ASC LIMIT 1",
                    (shift_id, task_id)).fetchone()
                if row:
                    payload["recommendation_id"] = row["id"]
                    con.execute(
                        "UPDATE hav_recommendations SET payload = ?, updated_at = ? "
                        "WHERE id = ? AND status = 'pending'",
                        (json.dumps(payload), now, row["id"]))
                else:
                    payload["recommendation_id"] = uuid.uuid4().hex
                    con.execute(
                        "INSERT INTO hav_recommendations "
                        "(id, shift_id, task_id, created_at, updated_at, payload, status) "
                        "VALUES (?,?,?,?,?,?, 'pending')",
                        (payload["recommendation_id"], shift_id, task_id, now, now,
                         json.dumps(payload)))
        finally:
            con.close()
    if publish:
        try:
            event_bus.bus.publish(Event(
                "planning_recommendation", json.loads(json.dumps(payload)), source,
                shift_id, config.current_shift()["worker_id"]))
        except Exception:
            log.exception("could not publish planning_recommendation")
    return payload


def _listed(row) -> dict:
    item = json.loads(row["payload"])
    status = row["status"]
    item.update({
        "recommendation_id": row["id"],
        "status": status,
        "decision": None if status == "pending" else status,
        "decided_by": row["decided_by"],
        "decided_at": row["decided_at"],
        "decision_note": row["decision_note"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    })
    return item


def list_recommendations(shift_id: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
    sql, args, where = "SELECT * FROM hav_recommendations", [], []
    if shift_id is not None:
        where.append("shift_id = ?")
        args.append(shift_id)
    if status is not None:
        where.append("status = ?")
        args.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, id ASC"
    con = database.connect()
    try:
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()
    return [_listed(r) for r in rows]


def get_recommendation(rec_id: str) -> Optional[dict]:
    con = database.connect()
    try:
        row = con.execute("SELECT * FROM hav_recommendations WHERE id = ?", (rec_id,)).fetchone()
    finally:
        con.close()
    return _listed(row) if row else None


def decide(rec_id: str, decision: str, decided_by: str, note: Optional[str] = None,
           revision: Optional[str] = None) -> dict:
    """A human decision. The only code path that changes a recommendation's status.

    `revision` must be the `revision` of the recommendation the person actually reviewed: a
    pending recommendation is re-evaluated when exposure changes, and a decision made against
    an older version is refused rather than applied to figures nobody saw.
    """
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError("revision is required: quote the revision of the recommendation reviewed")
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    if not isinstance(decided_by, str) or not decided_by.strip():
        raise ValueError("decided_by is required: a decision must name the person who made it")
    now = time.time()
    with _write_lock:
        con = database.connect()
        try:
            row = con.execute("SELECT status, payload FROM hav_recommendations WHERE id = ?",
                              (rec_id,)).fetchone()
            if row is None:
                raise KeyError(rec_id)
            if row["status"] == "pending" and \
                    json.loads(row["payload"]).get("revision") != revision.strip():
                raise ValueError("the recommendation changed since it was reviewed: reload it "
                                 "and decide on the current version")
            with con:
                cur = con.execute(
                    "UPDATE hav_recommendations SET status = ?, decided_by = ?, decided_at = ?, "
                    "decision_note = ?, updated_at = ? WHERE id = ? AND status = 'pending'",
                    (decision, decided_by.strip(), now, note, now, rec_id))
            if cur.rowcount == 0:
                raise ValueError(f"recommendation {rec_id} is already decided ({row['status']})")
            row = con.execute("SELECT * FROM hav_recommendations WHERE id = ?",
                              (rec_id,)).fetchone()
        finally:
            con.close()
    return _listed(row)


def _on_exposure_updated(event: Event) -> None:
    """Bus thread: re-evaluate the next task. Never raises."""
    try:
        task = tasks.next_task(event.shift_id)
        if task is not None:
            evaluate(event.shift_id, task["task_id"])
    except Exception:
        log.exception("automatic re-evaluation failed")


def install(bus=None) -> None:
    """Subscribe to `exposure_updated` on `bus` (or `event_bus.bus`). Idempotent per bus object."""
    target = bus if bus is not None else event_bus.bus
    if target in _installed:
        return
    target.subscribe(_on_exposure_updated, types=["exposure_updated"])
    _installed.add(target)
