"""Estimated hand-arm vibration exposure (be-hav), UK HSE points method.

    points = 2 * a_hv^2 * trigger hours        A(8) = 2.5 * sqrt(points / 100)
    100 points = exposure action value         400 points = exposure limit value

Screening / planning estimate only. The vibration magnitude comes ONLY from a configured
`ToolProfile.a_hv_ms2`; wrist acceleration from the band is never read here. Data gaps are never
exposure time. When an input is missing the result says so instead of inventing a number.
"""
from __future__ import annotations

import copy
import json
import logging
import math
import time
import uuid
import weakref

from . import config, database, event_bus, tool_profiles
from .models import Event

log = logging.getLogger("beltguard.exposure")

LABEL = "Estimated HAV exposure — screening/planning, not certified measurement"
MIN_COMPLETENESS_OK = 0.8           # below: entry needs_confirmation, no points
MIN_COMPLETENESS_USABLE = 0.5       # below: entry unavailable, no points
MAX_INFERRED_FRACTION = 0.5         # share of counted trigger time that is unconfirmed

SIMULATED_SOURCES = ("replay", "synthetic")

database.register_schema(
    """
    CREATE TABLE IF NOT EXISTS hav_sessions (
        session_id TEXT PRIMARY KEY, shift_id TEXT NOT NULL, worker_id TEXT,
        source TEXT, payload TEXT NOT NULL, updated_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS hav_sessions_shift ON hav_sessions (shift_id);
    CREATE TABLE IF NOT EXISTS hav_manual (
        id TEXT PRIMARY KEY, shift_id TEXT NOT NULL, worker_id TEXT, tool_id TEXT,
        minutes REAL NOT NULL, note TEXT, created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS hav_manual_shift ON hav_manual (shift_id);
    """
)


# ---------------------------------------------------------------- formulas

def _num(value) -> float | None:
    """A finite float, or None for anything else (missing, text, bool, nan, inf)."""
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def points(a_hv_ms2: float, trigger_seconds: float) -> float:
    """HSE exposure points: 2 * a_hv^2 * trigger time in hours."""
    a, seconds = _num(a_hv_ms2), _num(trigger_seconds)
    if a is None or seconds is None or a < 0 or seconds < 0:
        raise ValueError("a_hv_ms2 and trigger_seconds must be finite and not negative")
    return 2.0 * a * a * (seconds / 3600.0)


def a8(total_points: float) -> float:
    """Daily exposure A(8) in m/s2 from points: 2.5 * sqrt(points / 100)."""
    total = _num(total_points)
    if total is None or total < 0:
        raise ValueError("total_points must be finite and not negative")
    return 2.5 * math.sqrt(total / 100.0)


def level(points: float | None) -> str:
    value = _num(points)
    if value is None:
        return "unknown"
    if value >= config.LIMIT_POINTS:
        return "above_limit"
    if value >= config.ACTION_POINTS:
        return "above_action"
    return "below_action"


# ---------------------------------------------------------------- inputs

def _toolsense_sessions(shift_id: str) -> list[dict] | None:
    """Sessions from be-toolsense, or None when that source is not reachable."""
    try:
        from . import tool_sessions
        return [dict(s) for s in tool_sessions.list_sessions(shift_id)]
    except Exception:
        return None


def _stored_sessions(shift_id: str) -> list[dict]:
    con = database.connect()
    try:
        rows = con.execute(
            "SELECT * FROM hav_sessions WHERE shift_id = ? ORDER BY updated_at, session_id", (shift_id,)
        ).fetchall()
    finally:
        con.close()
    out = []
    for row in rows:
        try:
            session = json.loads(row["payload"])
        except Exception:
            session = {}
        if not isinstance(session, dict):
            session = {}
        session["session_id"] = row["session_id"]
        session["source"] = row["source"] or session.get("source")   # Event.source is authoritative
        out.append(session)
    return out


def _manual_rows(shift_id: str) -> list[dict]:
    con = database.connect()
    try:
        rows = con.execute(
            "SELECT * FROM hav_manual WHERE shift_id = ? ORDER BY created_at, id", (shift_id,)
        ).fetchall()
    finally:
        con.close()
    return [dict(row) for row in rows]


def _store_session(event: Event) -> bool:
    payload = event.payload if isinstance(event.payload, dict) else {}
    session_id = payload.get("session_id")
    if not session_id:
        log.warning("tool_session_ended without session_id ignored")
        return False
    con = database.connect()
    try:
        with con:
            con.execute(
                "INSERT OR REPLACE INTO hav_sessions (session_id, shift_id, worker_id, source, payload, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (str(session_id), event.shift_id, event.worker_id, event.source,
                 json.dumps(payload), time.time()),
            )
    finally:
        con.close()
    return True


# ---------------------------------------------------------------- entries

def _tool_fields(tool_id, reasons: list[str]) -> tuple[str | None, float | None, str | None]:
    """(tool_name, a_hv_ms2, a_hv_source). The magnitude comes from the profile or not at all."""
    if not tool_id:
        reasons.append("Tool not identified: a person must confirm which tool was used.")
        return None, None, None
    profile = tool_profiles.get(tool_id)
    if profile is None:
        reasons.append(f"Tool '{tool_id}' has no profile: add it with the manufacturer's declared or measured value.")
        return None, None, None
    if profile.a_hv_ms2 is None:
        reasons.append(f"No vibration value configured for {profile.name}: enter the manufacturer's declared or measured value.")
        return profile.name, None, profile.source
    return profile.name, profile.a_hv_ms2, profile.source


def _lenient_num(value) -> float | None:
    """Like _num, but numeric text ("300") is read as a number rather than dropped."""
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    return _num(value)


def _confirmation(session: dict) -> str:
    value = session.get("confirmation")
    return value.strip().lower() if isinstance(value, str) and value.strip() else "tentative"


def _session_entry(session: dict) -> dict:
    reasons: list[str] = []
    session_id = session.get("session_id")
    tool_id = session.get("tool_id") or None
    start_ts, end_ts = _num(session.get("start_ts")), _num(session.get("end_ts"))
    duration = _num(session.get("duration_s"))
    if duration is None and start_ts is not None and end_ts is not None:
        duration = end_ts - start_ts
    if duration is not None and duration < 0:
        duration = None
    raw_gap = session.get("gap_s")
    if raw_gap is None:
        raw_gap = session.get("data_gap_s")
    gap = _lenient_num(raw_gap)
    gap_unreadable = raw_gap is not None and gap is None
    gap = max(0.0, gap or 0.0)
    given_trigger = _num(session.get("trigger_s"))
    confirmation = _confirmation(session)
    basis = "confirmed" if confirmation == "confirmed" else "inferred"
    source = session.get("source") or "unknown"
    completeness = _num(session.get("completeness"))

    blocked = False
    status = "ok"
    trigger = None
    if duration is None:
        blocked = True
        reasons.append("Session duration unknown: no trigger time can be counted.")
    else:
        # Gap / disconnected time is never exposure time.
        # A classifier trigger estimate is taken as measured on connected data only, so the
        # gap is not subtracted from it a second time; it is capped at the connected time.
        connected = max(0.0, duration - gap)
        trigger = connected if given_trigger is None else max(0.0, min(given_trigger, connected))
        if gap > 0:
            reasons.append(f"{gap:.0f} s of data gap not counted as exposure time.")

    if gap_unreadable:
        blocked = True
        reasons.append("Data-gap value could not be read: a person must confirm the trigger time.")

    tool_name, a_hv, a_hv_source = _tool_fields(tool_id, reasons)
    if a_hv is None:
        blocked = True

    if completeness is None:
        blocked = True
        reasons.append("Sensor data completeness unknown: a person must confirm the trigger time.")
    elif completeness < MIN_COMPLETENESS_USABLE:
        blocked = True
        status = "unavailable"
        reasons.append(f"Sensor data too incomplete ({completeness:.0%}): no estimate for this session.")
    elif completeness < MIN_COMPLETENESS_OK:
        blocked = True
        reasons.append(f"Sensor data incomplete ({completeness:.0%}): a person must confirm the trigger time.")

    if blocked:
        entry_points = None
        if status == "ok":
            status = "needs_confirmation"
    else:
        entry_points = points(a_hv, trigger)
        if basis == "inferred":
            reasons.append("Tool use inferred, not yet confirmed by a person.")
    if source in SIMULATED_SOURCES:
        reasons.append(f"Simulated data ({source}), not a live recording.")
    elif source == "unknown":
        reasons.append("Data provenance not recorded: not confirmed as a live band recording.")

    return {
        "entry_id": f"session-{session_id}",
        "kind": "session",
        "session_id": session_id,
        "tool_id": tool_id,
        "tool_name": tool_name,
        "a_hv_ms2": a_hv,
        "a_hv_source": a_hv_source,
        "duration_s": duration,
        "gap_s": gap,
        "trigger_s": trigger,
        "duration_basis": basis,
        "confirmed_s": (trigger or 0.0) if basis == "confirmed" else 0.0,
        "inferred_s": (trigger or 0.0) if basis == "inferred" else 0.0,
        "confidence": _num(session.get("confidence")),
        "completeness": completeness,
        "confirmation": confirmation,
        "source": source,
        "simulated": source in SIMULATED_SOURCES,
        "points": entry_points,
        "status": status,
        "reasons": reasons,
        "start_ts": start_ts,
        "end_ts": end_ts,
    }


def _manual_entry(row: dict) -> dict:
    reasons: list[str] = ["Trigger time entered manually."]
    tool_id = row.get("tool_id") or None
    seconds = float(row["minutes"]) * 60.0
    tool_name, a_hv, a_hv_source = _tool_fields(tool_id, reasons)
    entry_points = points(a_hv, seconds) if a_hv is not None else None
    return {
        "entry_id": f"manual-{row['id']}",
        "kind": "manual",
        "session_id": None,
        "tool_id": tool_id,
        "tool_name": tool_name,
        "a_hv_ms2": a_hv,
        "a_hv_source": a_hv_source,
        "duration_s": seconds,
        "gap_s": 0.0,
        "trigger_s": seconds,
        "duration_basis": "manual",
        "confirmed_s": seconds,
        "inferred_s": 0.0,
        "confidence": None,
        "completeness": None,
        "confirmation": "confirmed",
        "source": "manual",
        "simulated": False,
        "points": entry_points,
        "status": "ok" if entry_points is not None else "needs_confirmation",
        "reasons": reasons,
        "note": row.get("note"),
        "worker_id": row.get("worker_id"),
        "created_at": row.get("created_at"),
    }


# ---------------------------------------------------------------- result

def compute(shift_id: str) -> dict:
    """Current estimate for the shift. Pure read: publishes nothing."""
    toolsense = _toolsense_sessions(shift_id)
    stored = _stored_sessions(shift_id)
    manual = _manual_rows(shift_id)

    merged: dict[str, dict] = {}
    for session in stored:
        merged[str(session["session_id"])] = session
    for session in toolsense or []:
        if not isinstance(session, dict) or not session.get("session_id"):
            continue
        key = str(session["session_id"])
        session = dict(session)
        if key in merged and merged[key].get("source"):
            session["source"] = merged[key]["source"]        # provenance recorded with the event wins
        merged[key] = session                                # toolsense list wins

    if toolsense is not None:
        session_source = "tool_sessions"
    elif stored:
        session_source = "events"
    else:
        session_source = "none"

    entries: list[dict] = []
    excluded = 0
    for session in merged.values():
        if _confirmation(session) == "rejected":
            excluded += 1
            continue
        entries.append(_session_entry(session))
    entries.sort(key=lambda e: (e["start_ts"] is None, e["start_ts"] or 0.0, str(e["session_id"])))
    entries.extend(_manual_entry(row) for row in manual)

    counted = [e for e in entries if e["points"] is not None]
    total = sum(e["points"] for e in counted)
    confirmed_points = sum(e["points"] for e in counted if e["duration_basis"] != "inferred")
    inferred_points = sum(e["points"] for e in counted if e["duration_basis"] == "inferred")
    confirmed_s = sum(e["confirmed_s"] for e in counted)
    inferred_s = sum(e["inferred_s"] for e in counted)
    counted_s = confirmed_s + inferred_s
    inferred_fraction = (inferred_s / counted_s) if counted_s > 0 else 0.0

    reasons: list[str] = []
    no_source = toolsense is None and not stored and not manual
    if no_source:
        status = "unavailable"
        reasons.append("No tool-use data source is available and nothing has been entered manually: "
                       "exposure cannot be estimated.")
    elif entries and not counted:
        status = "unavailable"
        reasons.append("No entry has both a confirmed tool vibration value and usable trigger time: "
                       "exposure cannot be estimated until a person supplies the missing information.")
    else:
        missing = len(entries) - len(counted)
        status = "ok"
        if missing:
            status = "needs_confirmation"
            reasons.append(f"{missing} of {len(entries)} entries have no points yet: the total is a partial "
                           "figure and the real exposure may be higher.")
        if inferred_fraction > MAX_INFERRED_FRACTION:
            status = "needs_confirmation"
            reasons.append(f"{inferred_fraction:.0%} of the counted trigger time is inferred and not yet "
                           "confirmed by a person.")
    if toolsense is None and not no_source:
        reasons.append("Tool-session detector not reachable: using stored session events and manual entries only.")
    if excluded:
        reasons.append(f"{excluded} rejected session(s) excluded.")
    contains_simulated = any(e["simulated"] for e in entries)
    if contains_simulated:
        reasons.append("Includes simulated (replay/synthetic) data: not a live recording.")
    provenance_unknown = any(e["source"] == "unknown" for e in entries)
    if provenance_unknown:
        reasons.append("Some sessions have no recorded provenance: not confirmed as live band data.")

    if status == "unavailable":
        total_points, a8_value, level_value = None, None, "unknown"
    else:
        total_points = round(total, 2)
        if level(total_points) != level(total):     # rounding never moves the figure across a threshold
            total_points = math.floor(total * 100) / 100
        a8_value = round(a8(total), 3)
        level_value = level(total)

    for entry in entries:
        if entry["points"] is not None:
            entry["points"] = round(entry["points"], 2)

    return {
        "label": LABEL,
        "shift_id": shift_id,
        "total_points": total_points,
        "total_points_exact": None if total_points is None else total,
        "a8": a8_value,
        "level": level_value,
        "status": status,
        "entries": entries,
        "reasons": reasons,
        "confirmed_points": round(confirmed_points, 2),
        "inferred_points": round(inferred_points, 2),
        "confirmed_s": confirmed_s,
        "inferred_s": inferred_s,
        "excluded_sessions": excluded,
        "contains_simulated": contains_simulated,
        "provenance_unknown": provenance_unknown,
        "sources": sorted({e["source"] for e in entries if e["source"]}),
        "action_points": config.ACTION_POINTS,
        "limit_points": config.LIMIT_POINTS,
        "session_source": session_source,
        "computed_at": time.time(),
    }


def event_source(result: dict) -> str:
    """Provenance for events derived from this result (used by the planner too)."""
    return _event_source(result)


def _event_source(result: dict) -> str:
    # Any simulated entry, counted or not, marks the whole update as simulated.
    counted = {e["source"] for e in result["entries"]}
    if "synthetic" in counted:
        return "synthetic"
    if "replay" in counted:
        return "replay"
    return "derived"


def recompute(shift_id: str) -> dict:
    """compute + publish `exposure_updated`. Returns the same dict."""
    result = compute(shift_id)
    event = Event(
        type="exposure_updated",
        payload=copy.deepcopy(result),
        source=_event_source(result),
        shift_id=shift_id,
        worker_id=config.current_shift()["worker_id"],
    )
    event_bus.bus.publish(event)
    return result


def add_manual(shift_id: str, tool_id: str | None, minutes: float, worker_id: str | None = None,
               note: str | None = None) -> dict:
    """Store a manually entered trigger time, then recompute."""
    value = _num(minutes)
    if value is None or value <= 0:
        raise ValueError("minutes must be a finite number greater than 0")
    if tool_id is not None and not isinstance(tool_id, str):
        raise ValueError("tool_id must be text or null")
    con = database.connect()
    try:
        with con:
            con.execute(
                "INSERT INTO hav_manual (id, shift_id, worker_id, tool_id, minutes, note, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, shift_id, worker_id or config.current_shift()["worker_id"],
                 (tool_id or None), value, note, time.time()),
            )
    finally:
        con.close()
    return recompute(shift_id)


# ---------------------------------------------------------------- bus

_installed: "weakref.WeakSet" = weakref.WeakSet()


def _on_session_ended(event: Event) -> None:
    # Runs on the bus thread: never raise.
    try:
        if _store_session(event):
            recompute(event.shift_id)
    except Exception:
        log.exception("exposure recompute failed after tool_session_ended")


def install(bus=None) -> None:
    """Subscribe to `tool_session_ended` on `bus` (default: event_bus.bus). Idempotent per bus."""
    target = bus if bus is not None else event_bus.bus
    if target in _installed:
        return
    target.subscribe(_on_session_ended, types=["tool_session_ended"])
    _installed.add(target)
