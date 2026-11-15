"""Work-order workflow service: one button, one endpoint.

    python -m uvicorn workorder.app:app --port 8020      then open http://127.0.0.1:8020/

`router` can be mounted into any other FastAPI app (`app.include_router(workorder.app.router)`).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import flow, providers, store

router = APIRouter(prefix="/api/workorder", tags=["workorder"])


class RunBody(BaseModel):
    approved_by: Optional[str] = None      # the person who clicked; required by the flow
    conveyor_id: Optional[str] = None
    station_id: Optional[str] = None
    asset_id: Optional[str] = None
    problem: Optional[str] = None
    task: Optional[str] = None
    priority: Optional[str] = None
    repair_minutes: Optional[int] = None
    technician: Optional[str] = None
    tool_id: Optional[str] = None
    window_start: Optional[str] = None     # ISO time; default is the next half hour


@router.post("/run")
def run(body: Optional[RunBody] = None) -> dict:
    """The Work order button: runs draft, checks, schedule, create, calendar and verify."""
    return flow.run(body.model_dump() if body else None)


@router.get("")
def list_work_orders() -> list[dict]:
    return store.list_all()


@router.get("/{wo_id}")
def get_work_order(wo_id: str) -> dict:
    row = store.get(wo_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"{wo_id} not found")
    return row


@router.get("/{wo_id}/event.ics")
def event_ics(wo_id: str) -> Response:
    row = store.get(wo_id)
    if not row or not row.get("window_start"):
        raise HTTPException(status_code=404, detail=f"{wo_id} has no scheduled window")
    text = providers.ics(wo_id, row.get("ref") or wo_id, row["request"],
                         datetime.fromisoformat(row["window_start"]), datetime.fromisoformat(row["window_end"]))
    return Response(text, media_type="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="{wo_id}.ics"'})


app = FastAPI(title="Vexora work-order flow")
app.add_middleware(CORSMiddleware, allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


@app.get("/")
def page() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")
