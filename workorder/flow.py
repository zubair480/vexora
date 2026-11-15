"""Work-order workflow: one click runs every step and records what really happened.

    draft -> checks -> schedule -> create work order -> calendar -> verify

The pattern comes from PlantAPI's engine: a small state machine, each step returns evidence, and the
engine re-checks results instead of trusting them. A step that cannot run is "blocked" with the real
reason; nothing is reported that did not happen.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import providers, store

STATUSES = ("DRAFT", "CHECKED", "SCHEDULED", "CREATED", "CALENDAR_READY", "VERIFIED", "BLOCKED")
PRIORITIES = ("low", "medium", "high")

SAMPLE = {   # used only when the caller sends nothing; the result is then marked sample=True
    "conveyor_id": "CV-102", "station_id": "S3", "asset_id": "RETURN-IDLER-14",
    "problem": "Return idler bearing seized (reported by worker, unverified)",
    "task": "Replace return idler and bearing", "priority": "high",
    "repair_minutes": 60, "technician": "Unassigned", "tool_id": None,
}


@dataclass
class Step:
    name: str
    status: str                      # done | blocked
    detail: str
    evidence: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "detail": self.detail,
                "evidence": self.evidence, "ts": self.ts}


class Blocked(Exception):
    pass


def check_request(req: dict) -> None:
    """Refuse a work order that cannot be acted on (PlantAPI's `checkOutput` idea)."""
    for key in ("asset_id", "problem", "task"):
        if not str(req.get(key) or "").strip():
            raise Blocked(f"'{key}' is missing: a work order needs an asset, a problem and a task")
    if req.get("priority") not in PRIORITIES:
        raise Blocked(f"priority must be one of {', '.join(PRIORITIES)}")
    try:
        minutes = int(req.get("repair_minutes"))
    except (TypeError, ValueError):
        raise Blocked("repair_minutes must be a number") from None
    if not 5 <= minutes <= 12 * 60:
        raise Blocked("repair_minutes must be between 5 and 720")
    if not str(req.get("approved_by") or "").strip():
        raise Blocked("approved_by is missing: a named person must approve the work order")


def next_window(repair_minutes: int, now: Optional[datetime] = None,
                start: Optional[str] = None) -> tuple[datetime, datetime]:
    """The repair window: the caller's start time, or the next half hour at least 30 minutes away."""
    now = now or datetime.now(timezone.utc)
    if start:
        try:
            begin = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError:
            raise Blocked(f"window start is not a timestamp: {start!r}") from None
        if begin.tzinfo is None:
            begin = begin.astimezone()
        if begin < now - timedelta(minutes=5):
            raise Blocked(f"window starts in the past ({start})")
    else:
        whole = now.replace(second=0, microsecond=0)
        if whole < now:
            whole += timedelta(minutes=1)                    # round up, so the gap is never under 30 min
        begin = whole + timedelta(minutes=30)
        begin += timedelta(minutes=(30 - begin.minute % 30) % 30)
    return begin, begin + timedelta(minutes=repair_minutes)


def run(request: Optional[dict] = None) -> dict:
    """Run the whole workflow once. Always returns the record, including when a step is blocked."""
    sample = not request or not any(request.get(k) for k in ("asset_id", "problem", "task"))
    given = {k: v for k, v in (request or {}).items() if v not in (None, "")}
    # Sample values stand in only when nothing was described; a partly filled request is never padded.
    defaults = SAMPLE if sample else {k: SAMPLE[k] for k in ("priority", "repair_minutes", "technician", "tool_id")}
    req = {"conveyor_id": None, "station_id": None, "asset_id": None, "problem": None, "task": None,
           **defaults, **given}
    steps: list[Step] = []
    record = store.create(req, sample=sample)
    status = "DRAFT"
    made: dict = {}
    steps.append(Step("draft", "done", f"Draft for {req.get('asset_id') or 'no asset'} at {req.get('station_id') or 'no station'}",
                      {"sample_data": sample}))
    try:
        check_request(req)
        status = "CHECKED"
        steps.append(Step("checks", "done", f"Approved by {req['approved_by']}; asset, task and priority present"))

        begin, end = next_window(int(req["repair_minutes"]), start=req.get("window_start"))
        status = "SCHEDULED"
        steps.append(Step("schedule", "done", f"Repair window {begin.isoformat()} to {end.isoformat()}",
                          {"window_start": begin.isoformat(), "window_end": end.isoformat(),
                           "technician": req["technician"]}))

        provider = providers.work_order_provider()
        created = provider.create(record["id"], req, begin, end)       # raises ProviderError
        status = "CREATED"
        made = {"ref": created["ref"], "system": provider.name}   # kept even if a later step blocks
        steps.append(Step("work_order", "done", f"Work order {created['ref']} created in {provider.name}", created))

        cal = providers.calendar_provider().schedule(record["id"], created["ref"], req, begin, end)
        status = "CALENDAR_READY" if cal["status"] == "ready" else "CREATED"
        steps.append(Step("calendar", "done", cal["detail"], cal))

        problem = provider.verify(created["ref"], req, begin, end)      # re-read, do not trust
        if problem:
            raise Blocked(f"verification failed: {problem}")
        status = "VERIFIED"
        steps.append(Step("verify", "done", f"Re-read {created['ref']} from {provider.name}: it matches"))
        store.update(record["id"], status=status, steps=[s.to_dict() for s in steps], ref=created["ref"],
                     system=provider.name, window_start=begin.isoformat(), window_end=end.isoformat(),
                     calendar=cal)
    except (Blocked, providers.ProviderError) as error:
        steps.append(Step("blocked", "blocked", str(error)))
        store.update(record["id"], status="BLOCKED", steps=[s.to_dict() for s in steps], reached=status, **made)
    return store.get(record["id"])
