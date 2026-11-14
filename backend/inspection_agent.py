"""Spoken defect report -> inspection, defect and work-order draft. Owned by be-voice.

    "Station 3, return idler 14, bearing seized. Needs replacement."

`process_transcript` asks the kit's `ai.extract` (unmodified helper, strict JSON schema) which
conveyor, station, asset, condition and action the worker *said*, then checks the asset against
the local registry (`assets.resolve`). The registry decides, not the model:

- clean match   -> `inspection_recorded`, `defect_reported`, `work_order_created` (draft)
- ambiguous / unknown asset -> only `inspection_recorded` with `needs_clarification=true` and a
  question; no defect, no work order, until `clarify(...)` completes it
- AI call failed / no key -> an honest error dict; nothing is recorded or guessed

Honesty: the condition is stored as reported ("reported bearing seized"). The AI extracts words;
it never verifies a defect. `verification_status` stays "unverified" until a human reviews the
work order through the API (`review_work_order`).
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Optional

from . import assets, config, database, event_bus, work_orders
from .models import Event

SEVERITIES = ("low", "medium", "high", "unknown")
HONESTY = ("Fields were extracted by AI from the worker's own words and matched against the "
           "asset register. The defect is reported, not verified, until a human reviews it.")

_NULLABLE = {"type": ["string", "null"]}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["conveyor_id", "station_id", "asset_reference", "defect_reported", "condition",
                 "requested_action", "severity"],
    "properties": {
        "conveyor_id": _NULLABLE,
        "station_id": _NULLABLE,
        "asset_reference": _NULLABLE,
        "defect_reported": {"type": "boolean"},
        "condition": _NULLABLE,
        "requested_action": _NULLABLE,
        "severity": {"type": "string", "enum": list(SEVERITIES)},
    },
}
INSTRUCTIONS = (
    "A conveyor maintenance worker dictated an inspection report. Fill in only what the worker "
    "actually said; use null for anything not said. Never guess or invent an identifier. "
    "conveyor_id: the conveyor named, like 'CV-102', else null. "
    "station_id: the station as spoken, like 'station 3', else null. "
    "asset_reference: the component named, in the worker's words with its number, like "
    "'return idler 14', else null. "
    "defect_reported: true only if the worker reported a fault or abnormal condition. "
    "condition: the fault in the worker's words, short, like 'bearing seized', else null. "
    "requested_action: the action the worker asked for, like 'replace', else null. "
    "severity: low, medium or high only if the worker's words make it clear, otherwise 'unknown'."
)

database.register_schema(
    """
    CREATE TABLE IF NOT EXISTS inspections (
        inspection_id TEXT PRIMARY KEY, shift_id TEXT NOT NULL, worker_id TEXT NOT NULL,
        transcript TEXT NOT NULL, input_mode TEXT NOT NULL, source TEXT NOT NULL,
        conveyor_id TEXT, station_id TEXT, asset_id TEXT, asset_reference TEXT, station_reference TEXT,
        defect_reported INTEGER NOT NULL, condition TEXT, requested_action TEXT, severity TEXT,
        verification_status TEXT NOT NULL, needs_clarification INTEGER NOT NULL,
        clarification_question TEXT, candidates TEXT NOT NULL, asset_resolution TEXT,
        defect_id TEXT, work_order_id TEXT, created_ts REAL NOT NULL, updated_ts REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS inspections_shift ON inspections (shift_id, created_ts);
    CREATE TABLE IF NOT EXISTS defects (
        defect_id TEXT PRIMARY KEY, inspection_id TEXT NOT NULL, shift_id TEXT NOT NULL,
        worker_id TEXT NOT NULL, conveyor_id TEXT, station_id TEXT, asset_id TEXT NOT NULL,
        condition TEXT NOT NULL, severity TEXT NOT NULL, requested_action TEXT, source TEXT NOT NULL,
        verification_status TEXT NOT NULL, reviewed_by TEXT, created_ts REAL NOT NULL,
        updated_ts REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS defects_shift ON defects (shift_id, created_ts);
    """
)


class InspectionNotFound(LookupError):
    pass


# ---------------------------------------------------------------- AI extraction (kit helper)

def _kit_ai():
    config.add_kit_to_path()
    import ai   # the kit's helper, unmodified
    return ai


def _clean(value) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value if value and value.lower() not in ("null", "none", "unknown", "n/a") else None


def extract_fields(text: str) -> dict:
    """One `ai.extract` call. Raises on any failure (missing key, network, refusal, odd answer)."""
    config.load_env()                      # OPENAI_API_KEY from the repo's .env, if not in the env
    ai = _kit_ai()
    answer = ai.extract(text, SCHEMA, INSTRUCTIONS)
    if not isinstance(answer, dict) or set(SCHEMA["required"]) - set(answer):
        raise ValueError("The AI answer did not have the expected fields.")
    severity = answer.get("severity")
    return {
        "conveyor_id": _clean(answer.get("conveyor_id")),
        "station_id": _clean(answer.get("station_id")),
        "asset_reference": _clean(answer.get("asset_reference")),
        "defect_reported": answer.get("defect_reported") is True,
        "condition": _clean(answer.get("condition")),
        "requested_action": _clean(answer.get("requested_action")),
        "severity": severity if severity in SEVERITIES else "unknown",
    }


# ---------------------------------------------------------------- storage

def _row(row) -> Optional[dict]:
    if row is None:
        return None
    item = dict(row)
    for name in ("needs_clarification", "defect_reported"):
        if name in item:
            item[name] = bool(item[name])
    if "candidates" in item:
        item["candidates"] = json.loads(item["candidates"] or "[]")
    return item


def _query(sql: str, args: tuple = ()) -> list[dict]:
    con = database.connect()
    try:
        return [_row(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def _write(sql: str, args: tuple) -> int:
    con = database.connect()
    try:
        with con:
            return con.execute(sql, args).rowcount
    finally:
        con.close()


def get_inspection(inspection_id: str) -> Optional[dict]:
    rows = _query("SELECT * FROM inspections WHERE inspection_id = ?", (inspection_id,))
    return rows[0] if rows else None


def list_inspections(shift_id: Optional[str] = None) -> list[dict]:
    if shift_id:
        return _query("SELECT * FROM inspections WHERE shift_id = ? ORDER BY created_ts, rowid", (shift_id,))
    return _query("SELECT * FROM inspections ORDER BY created_ts, rowid")


def get_defect(defect_id: str) -> Optional[dict]:
    rows = _query("SELECT * FROM defects WHERE defect_id = ?", (defect_id,))
    return rows[0] if rows else None


def list_defects(shift_id: Optional[str] = None) -> list[dict]:
    if shift_id:
        return _query("SELECT * FROM defects WHERE shift_id = ? ORDER BY created_ts, rowid", (shift_id,))
    return _query("SELECT * FROM defects ORDER BY created_ts, rowid")


# ---------------------------------------------------------------- helpers

def _reported(condition: Optional[str], defect_reported: bool = False) -> Optional[str]:
    """The condition as a claim by the worker, never as a finding: 'reported bearing seized'."""
    if not condition:
        return "reported fault, condition not stated" if defect_reported else None
    return condition if condition.lower().startswith("reported") else "reported " + condition


def _question(match: assets.AssetMatch, reference: Optional[str]) -> str:
    if match.status == "ambiguous" and len(match.candidates) > 1:
        options = " or ".join("%s (%s)" % (c["asset_id"], c.get("station_id")) for c in match.candidates)
        return "Which asset did you mean: %s?" % options
    if match.status == "ambiguous" and match.candidates:
        only = match.candidates[0]
        return "%s Please confirm the asset: is it %s at %s?" % (
            match.reason.rstrip(". ") + ".", only["asset_id"], only.get("station_id"))
    if match.status == "unknown" and match.conveyor_id is None and match.reason:
        return ("%s This register only covers %s. If the report is about %s, say the station and "
                "the asset." % (match.reason.rstrip(". ") + ".", assets.CONVEYOR_ID, assets.CONVEYOR_ID))
    if not reference:
        return "Which asset is this about? Say the station and the asset, for example 'station 3, return idler 14'."
    return ("'%s' is not in the %s asset register. Which asset is it? Say the station and the "
            "asset, or pick one from the list." % (reference, assets.CONVEYOR_ID))


def _priority(severity: str) -> str:
    return severity if severity in ("low", "medium", "high") else "unset"


def _publish(type_: str, payload: dict, row: dict) -> None:
    event_bus.bus.publish(Event(type_, payload, "ai", row["shift_id"], row["worker_id"],
                                station_id=row.get("station_id"), asset_id=row.get("asset_id")))


def _inspection_payload(row: dict) -> dict:
    return {name: row[name] for name in (
        "inspection_id", "transcript", "conveyor_id", "station_id", "asset_id", "condition",
        "requested_action", "verification_status", "needs_clarification", "clarification_question",
        "severity", "defect_reported", "candidates", "asset_reference", "asset_resolution",
        "input_mode")}


def _result(status: str, inspection: dict, defect: Optional[dict] = None,
            work_order: Optional[dict] = None) -> dict:
    return {
        "status": status,
        "inspection": inspection,
        "defect": defect,
        "work_order": work_order,
        "needs_clarification": inspection["needs_clarification"],
        "clarification_question": inspection["clarification_question"],
        "candidates": inspection["candidates"],
        "note": HONESTY,
    }


def _error(code: str, message: str, transcript: str) -> dict:
    return {"status": "error", "error": code, "message": message, "transcript": transcript,
            "inspection": None, "defect": None, "work_order": None,
            "note": "Nothing was recorded. No record is created from a guess."}


def _raise_defect_and_work_order(row: dict) -> tuple[dict, dict]:
    """A matched inspection that reports a fault becomes an unverified defect and a draft."""
    now = time.time()
    defect_id = "DEF-" + uuid.uuid4().hex[:8].upper()
    asset = assets.get(row["asset_id"]) or {}
    _write("INSERT INTO defects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
           (defect_id, row["inspection_id"], row["shift_id"], row["worker_id"], row["conveyor_id"],
            row["station_id"], row["asset_id"], row["condition"], row["severity"],
            row["requested_action"], "ai", "unverified", None, now, now))
    task = "%s: %s (%s) at %s, %s. Reported by worker, not yet verified." % (
        (row["requested_action"] or "Inspect and assess").capitalize(),
        asset.get("name") or row["asset_id"], row["asset_id"], row["station_id"], row["condition"])
    order = work_orders.create(
        defect_id=defect_id, inspection_id=row["inspection_id"], asset_id=row["asset_id"],
        station_id=row["station_id"], conveyor_id=row["conveyor_id"], task=task,
        priority=_priority(row["severity"]), shift_id=row["shift_id"], worker_id=row["worker_id"],
        source="ai")
    _write("UPDATE inspections SET defect_id = ?, work_order_id = ?, updated_ts = ? WHERE inspection_id = ?",
           (defect_id, order["work_order_id"], now, row["inspection_id"]))
    return get_defect(defect_id), order


def _publish_recorded(inspection: dict, defect: Optional[dict], order: Optional[dict]) -> None:
    _publish("inspection_recorded", _inspection_payload(inspection), inspection)
    if defect and order:
        _publish("defect_reported", {
            "defect_id": defect["defect_id"], "inspection_id": inspection["inspection_id"],
            "asset_id": defect["asset_id"], "station_id": defect["station_id"],
            "condition": defect["condition"], "severity": defect["severity"],
            "verification_status": defect["verification_status"]}, inspection)
        _publish("work_order_created", {
            "work_order_id": order["work_order_id"], "defect_id": defect["defect_id"],
            "inspection_id": inspection["inspection_id"], "asset_id": order["asset_id"],
            "task": order["task"], "priority": order["priority"], "status": order["status"]}, inspection)


# ---------------------------------------------------------------- public flow

def process_transcript(text: str, shift_id: str, worker_id: str,
                       station_hint: Optional[str] = None, input_mode: str = "typed") -> dict:
    """Turn one report (typed or spoken) into records and events. Works with no band."""
    transcript = " ".join((text or "").split())
    if not transcript:
        return _error("empty_transcript", "There was no text to process.", transcript)
    try:
        fields = extract_fields(transcript)
    except Exception as error:   # ai.AiError (no key, network, refusal...) or an unreadable answer
        message = getattr(error, "message", None) or str(error) or error.__class__.__name__
        return _error("ai_unavailable", "The AI extraction did not run: %s" % message, transcript)

    station_reference = fields["station_id"] or _clean(station_hint)
    conveyor = fields["conveyor_id"] or config.current_shift()["conveyor_id"]
    match = assets.resolve(fields["asset_reference"], station=station_reference, conveyor_id=conveyor)
    matched = match.status == "matched"
    now = time.time()
    inspection_id = "INS-" + uuid.uuid4().hex[:8].upper()
    _write("INSERT INTO inspections VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        inspection_id, shift_id, worker_id, transcript, input_mode, "ai",
        match.conveyor_id if matched else None,
        match.station_id if matched else assets.resolve_station(station_reference),
        match.asset_id if matched else None,
        fields["asset_reference"], station_reference,
        int(fields["defect_reported"]), _reported(fields["condition"], fields["defect_reported"]), fields["requested_action"],
        fields["severity"], "unverified", int(not matched),
        None if matched else _question(match, fields["asset_reference"]),
        json.dumps([] if matched else match.candidates),
        "registry_match" if matched else None, None, None, now, now))
    inspection = get_inspection(inspection_id)
    defect = order = None
    if matched and inspection["defect_reported"] and inspection["condition"]:
        defect, order = _raise_defect_and_work_order(inspection)
        inspection = get_inspection(inspection_id)
    _publish_recorded(inspection, defect, order)
    return _result("recorded" if matched else "needs_clarification", inspection, defect, order)


def clarify(inspection_id: str, text_or_asset_id: str) -> dict:
    """The worker answers the clarification question with an asset ID or spoken words.

    Deterministic (registry only, no AI). On a match the inspection is completed and the
    defect and draft work order are created; otherwise the question stays open.
    """
    inspection = get_inspection(inspection_id)
    if inspection is None:
        raise InspectionNotFound("No inspection with id %r." % inspection_id)
    if not inspection["needs_clarification"]:
        return dict(_result("recorded", inspection, get_defect(inspection["defect_id"] or ""),
                            work_orders.get(inspection["work_order_id"] or "")),
                    message="This inspection is already complete; nothing was changed.")
    answer = " ".join((text_or_asset_id or "").split())
    match = _match_answer(inspection, answer)
    now = time.time()
    if match is None or match.status != "matched":
        question = _question(match, answer) if match is not None else inspection["clarification_question"]
        candidates = match.candidates if match is not None and match.candidates else inspection["candidates"]
        _write("UPDATE inspections SET clarification_question = ?, candidates = ?, updated_ts = ? "
               "WHERE inspection_id = ?", (question, json.dumps(candidates), now, inspection_id))
        return _result("needs_clarification", get_inspection(inspection_id))
    won = _write(
        "UPDATE inspections SET conveyor_id = ?, station_id = ?, asset_id = ?, needs_clarification = 0, "
        "clarification_question = NULL, candidates = '[]', asset_resolution = 'worker_clarified', "
        "updated_ts = ? WHERE inspection_id = ? AND needs_clarification = 1",
        (match.conveyor_id, match.station_id, match.asset_id, now, inspection_id))
    inspection = get_inspection(inspection_id)
    if not won:      # another request completed it first: create and publish nothing here
        return dict(_result("recorded", inspection, get_defect(inspection["defect_id"] or ""),
                            work_orders.get(inspection["work_order_id"] or "")),
                    message="This inspection is already complete; nothing was changed.")
    defect = order = None
    if inspection["defect_reported"] and inspection["condition"] and not inspection["defect_id"]:
        defect, order = _raise_defect_and_work_order(inspection)
        inspection = get_inspection(inspection_id)
    _publish_recorded(inspection, defect, order)
    return _result("recorded", inspection, defect, order)


_YES = {"yes", "yep", "yeah", "correct", "confirm", "confirmed", "affirmative"}


def _from_registry(asset_id: str, query: str, reason: str) -> Optional[assets.AssetMatch]:
    found = assets.get(asset_id)
    if not found:
        return None
    return assets.AssetMatch(status="matched", query=query, asset_id=found["asset_id"],
                             station_id=found["station_id"], conveyor_id=found["conveyor_id"],
                             name=found["name"], reason=reason)


def _match_answer(inspection: dict, answer: str) -> Optional[assets.AssetMatch]:
    """Map the worker's answer to one registry asset, or None / a non-match to keep asking.

    An exact registry ID always wins (that is what a pick list sends). When the question
    offered candidates, spoken words may only choose among those candidates.
    """
    if not answer:
        return None
    exact = _from_registry(answer, answer, "exact asset ID")
    if exact:
        return exact
    candidates = inspection["candidates"]
    if candidates:
        ids = [c["asset_id"] for c in candidates]
        words = set("".join(ch if ch.isalnum() else " " for ch in answer.lower()).split())
        if len(ids) == 1 and words & _YES:
            return _from_registry(ids[0], answer, "worker confirmed the asset")
        known = {w for w in words if any(w in i.lower().split("-") for i in ids)}
        chosen = [i for i in ids if known and known <= set(i.lower().split("-"))]
        if len(chosen) == 1 and len(ids) > 1:
            return _from_registry(chosen[0], answer, "worker chose among the candidates")
    found = assets.resolve(answer)                        # "return idler 14"
    if found.status != "matched":
        station = assets.resolve_station(answer)          # "station 3" answers "which idler 14?"
        if station and inspection["asset_reference"]:
            found = assets.resolve(inspection["asset_reference"], station=station)
    if found.status != "matched" and inspection["station_reference"]:
        found = assets.resolve(answer, station=inspection["station_reference"])
    if found.status == "matched" and candidates and found.asset_id not in [c["asset_id"] for c in candidates]:
        return None                                       # not one of the offered assets: keep asking
    return found


def review_work_order(work_order_id: str, action: str, reviewer: str, note: Optional[str] = None) -> dict:
    """A human review action (submit / approve / reject). Only the API calls this.

    Approving marks the defect "verified" by that named human; rejecting marks it "rejected".
    Submitting changes nothing about the defect: it stays "unverified".
    """
    order = work_orders.review(work_order_id, action, reviewer, note)
    status = {"approved": "verified", "rejected": "rejected"}.get(order["status"])
    if status and order.get("defect_id"):
        now = time.time()
        _write("UPDATE defects SET verification_status = ?, reviewed_by = ?, updated_ts = ? WHERE defect_id = ?",
               (status, order["reviewed_by"], now, order["defect_id"]))
        _write("UPDATE inspections SET verification_status = ?, updated_ts = ? WHERE inspection_id = ?",
               (status, now, order["inspection_id"]))
    return {"work_order": order, "defect": get_defect(order.get("defect_id") or "")}
