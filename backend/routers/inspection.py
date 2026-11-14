"""inspection endpoints. Owned by the be-voice session (see CONTRACT.md)."""
from __future__ import annotations

import importlib
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import assets, config, inspection_agent, speech_engine, work_orders

router = APIRouter(prefix="/api/inspection", tags=["inspection"])


class ReportBody(BaseModel):
    text: str
    station_hint: Optional[str] = None
    shift_id: Optional[str] = None
    worker_id: Optional[str] = None


class ListenBody(BaseModel):
    seconds: float = Field(default=6.0, gt=0, le=speech_engine.MAX_SECONDS)
    station_hint: Optional[str] = None


class ClarifyBody(BaseModel):
    text: Optional[str] = None
    asset_id: Optional[str] = None


class ReviewBody(BaseModel):
    action: str                       # submit | approve | reject
    reviewer: str                     # the named human taking the action
    note: Optional[str] = None


def _shift(body=None) -> tuple[str, str]:
    shift = config.current_shift()
    return (getattr(body, "shift_id", None) or shift["shift_id"],
            getattr(body, "worker_id", None) or shift["worker_id"])


def _respond(result: dict):
    if result.get("status") == "error":
        code = 422 if result.get("error") == "empty_transcript" else 503
        return JSONResponse(status_code=code, content=result)
    return result


def bridge_band():
    """The band the be-band bridge already connected, or None. This module never connects."""
    try:
        bridge = importlib.import_module("backend.vexo_bridge")
    except Exception:
        return None
    try:
        return bridge.get_bridge().band          # None until the bridge has connected
    except Exception:
        return None


@router.post("/report")
def report(body: ReportBody):
    """A typed report: the whole flow with no band and no Deepgram key."""
    shift_id, worker_id = _shift(body)
    text = speech_engine.transcribe_typed(body.text)
    return _respond(inspection_agent.process_transcript(
        text, shift_id, worker_id, station_hint=body.station_hint, input_mode="typed"))


@router.post("/listen")
def listen(body: ListenBody):
    """Push-to-talk on the bridge's band. The LED is locked blue while this listens."""
    band = bridge_band()
    if band is None:
        raise HTTPException(status_code=409, detail={
            "error": "no-band",
            "message": "No band is connected, so nothing was heard. Connect the band from the "
                       "band page, or send the report as typed text to /api/inspection/report."})
    try:
        from .. import vexo_bridge
        # The bridge's guard marks the microphone as on and applies queued LED commands after.
        with vexo_bridge.get_bridge().microphone() as guarded:
            transcript = speech_engine.capture(guarded, body.seconds)
    except vexo_bridge.BridgeError as error:
        raise HTTPException(status_code=409, detail={"error": "no-band", "message": str(error)})
    except speech_engine.SpeechCaptureError as error:
        status = 409 if error.code in ("no-band", "busy") else 503
        raise HTTPException(status_code=status, detail={"error": error.code, "message": error.message})
    if not transcript:
        raise HTTPException(status_code=422, detail={
            "error": "nothing-heard", "message": "No speech was recognised. Nothing was recorded."})
    shift_id, worker_id = _shift()
    return _respond(inspection_agent.process_transcript(
        transcript, shift_id, worker_id, station_hint=body.station_hint, input_mode="voice"))


@router.post("/{inspection_id}/clarify")
def clarify(inspection_id: str, body: ClarifyBody):
    answer = body.asset_id or body.text
    if not (answer or "").strip():
        raise HTTPException(status_code=422, detail="Send `asset_id` or `text`.")
    try:
        return inspection_agent.clarify(inspection_id, answer)
    except inspection_agent.InspectionNotFound as error:
        raise HTTPException(status_code=404, detail=str(error))


@router.get("/inspections")
def inspections(shift_id: Optional[str] = None) -> list[dict]:
    return inspection_agent.list_inspections(shift_id or config.current_shift()["shift_id"])


@router.get("/defects")
def defects(shift_id: Optional[str] = None) -> list[dict]:
    return inspection_agent.list_defects(shift_id or config.current_shift()["shift_id"])


@router.get("/work-orders")
def list_work_orders(shift_id: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
    return work_orders.list_work_orders(shift_id or config.current_shift()["shift_id"], status)


@router.post("/work-orders/{work_order_id}/review")
def review(work_order_id: str, body: ReviewBody):
    """The only way a draft moves on: a named human's submit / approve / reject."""
    try:
        return inspection_agent.review_work_order(work_order_id, body.action, body.reviewer, body.note)
    except work_orders.WorkOrderNotFound as error:
        raise HTTPException(status_code=404, detail=str(error))
    except work_orders.InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))


@router.get("/assets")
def list_assets(station_id: Optional[str] = None) -> dict:
    return {"conveyor_id": assets.CONVEYOR_ID, "stations": assets.list_stations(),
            "assets": assets.list_assets(station_id=station_id)}
