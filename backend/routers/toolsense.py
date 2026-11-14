"""toolsense endpoints. Owned by the be-toolsense session (see CONTRACT.md).

Everything returned here is an estimated activity classification, not a certified measurement.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config, tool_sessions

router = APIRouter(prefix="/api/toolsense", tags=["toolsense"])

# main.py only includes this router, so the live detector is wired here (idempotent).
tool_sessions.start()


class ConfirmBody(BaseModel):
    tool_id: Optional[str] = None
    confirmed: bool = True


class TrainBody(BaseModel):
    include_synthetic: bool = False


def _plain(obj):
    """JSON-safe copy (numpy scalars, paths...)."""
    return json.loads(json.dumps(obj, default=lambda o: o.item() if hasattr(o, "item") else str(o)))


@router.get("/state")
def state() -> dict:
    return {**tool_sessions.get_service().state(), "estimated": True}


@router.get("/sessions")
def sessions(shift_id: Optional[str] = None) -> list[dict]:
    return tool_sessions.list_sessions(shift_id)


@router.post("/sessions/{session_id}/confirm")
def confirm(session_id: str, body: ConfirmBody) -> dict:
    try:
        return tool_sessions.confirm(session_id, body.tool_id, body.confirmed)
    except KeyError:
        raise HTTPException(404, f"unknown tool session {session_id}")
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.post("/train")
def train_model(body: Optional[TrainBody] = None) -> dict:
    from .. import train
    report = train.run(include_synthetic=bool(body and body.include_synthetic), verbose=False)
    tool_sessions.get_service().reload_classifier()
    return _plain(report)


@router.get("/report")
def report() -> dict:
    from .. import train
    path = config.MODELS / train.REPORT_NAME
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return _plain({"status": "no_report",
                   "collection": train.collection_status(train.load_recordings())})
