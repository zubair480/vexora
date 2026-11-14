"""exposure endpoints. Owned by the be-hav session (see CONTRACT.md).

Estimated hand-arm vibration exposure (screening/planning, not certified measurement) and the
maintenance planner. Nothing here approves anything: a recommendation only changes status through
the decision endpoint, which needs a named human (`decided_by`).
"""
from __future__ import annotations

import logging
from typing import Callable, Optional, TypeVar

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config, exposure, planner, tasks, tool_profiles

log = logging.getLogger("beltguard.exposure_api")

router = APIRouter(prefix="/api/exposure", tags=["exposure"])

for _install in (exposure.install, planner.install):
    try:
        _install()
    except Exception:
        log.exception("could not install %s subscriber", getattr(_install, "__module__", "?"))

T = TypeVar("T")


def _shift(shift_id: Optional[str]) -> str:
    return shift_id or config.current_shift()["shift_id"]


def _message(exc: Exception) -> str:
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc)


def _call(fn: Callable[[], T]) -> T:
    """ValueError -> 422, KeyError -> 404."""
    try:
        return fn()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=_message(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_message(exc)) from exc


class ManualEntry(BaseModel):
    tool_id: Optional[str] = None
    minutes: float
    shift_id: Optional[str] = None
    worker_id: Optional[str] = None
    note: Optional[str] = None


class ToolProfileUpdate(BaseModel):
    name: Optional[str] = None
    a_hv_ms2: Optional[float]          # required key; null clears the value
    source: Optional[str] = None


class EvaluateRequest(BaseModel):
    task_id: str
    shift_id: Optional[str] = None


class DecisionRequest(BaseModel):
    decision: str
    decided_by: str
    revision: str          # the `revision` of the recommendation the person reviewed
    note: Optional[str] = None


@router.get("/current")
def current(shift_id: Optional[str] = None) -> dict:
    return _call(lambda: exposure.compute(_shift(shift_id)))


@router.post("/manual")
def manual(body: ManualEntry) -> dict:
    return _call(lambda: exposure.add_manual(_shift(body.shift_id), body.tool_id, body.minutes,
                                             worker_id=body.worker_id, note=body.note))


@router.get("/tools")
def tools() -> dict:
    return {"label": exposure.LABEL,
            "tools": [profile.to_dict() for profile in tool_profiles.list_profiles()]}


@router.put("/tools/{tool_id}")
def put_tool(tool_id: str, body: ToolProfileUpdate, shift_id: Optional[str] = None) -> dict:
    name = body.name
    if not name or not name.strip():
        existing = tool_profiles.get(tool_id)
        name = existing.name if existing else None
    if not name or not name.strip():
        raise HTTPException(status_code=422, detail="name is required for a new tool profile")
    profile = _call(lambda: tool_profiles.upsert(tool_id, name, body.a_hv_ms2, body.source))
    try:
        exposure.recompute(_shift(shift_id))
    except Exception:
        log.exception("recompute after tool profile change failed")
    return profile.to_dict()


@router.get("/tasks")
def list_tasks(shift_id: Optional[str] = None) -> dict:
    shift = _shift(shift_id)
    nxt = tasks.next_task(shift)
    return {"label": exposure.LABEL,
            "shift_id": shift,
            "tasks": tasks.list_tasks(shift),
            "workers": tasks.list_workers(),
            "next_task_id": nxt["task_id"] if nxt else None}


@router.post("/plan/evaluate")
def plan_evaluate(body: EvaluateRequest) -> dict:
    return _call(lambda: planner.evaluate(_shift(body.shift_id), body.task_id))


@router.get("/plan/recommendations")
def plan_recommendations(shift_id: Optional[str] = None, status: Optional[str] = None) -> dict:
    return {"label": exposure.LABEL,
            "recommendations": _call(lambda: planner.list_recommendations(_shift(shift_id), status))}


@router.post("/plan/recommendations/{rec_id}/decision")
def plan_decision(rec_id: str, body: DecisionRequest) -> dict:
    return _call(lambda: planner.decide(rec_id, body.decision, body.decided_by, body.note, body.revision))
