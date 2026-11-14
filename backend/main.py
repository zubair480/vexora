"""FastAPI app. Shared file: owned by the integration session (see CONTRACT.md).

    python -m uvicorn backend.main:app --port 8010
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from . import config, database, shift_view
from .event_bus import bus
from .models import Event
from .routers import acoustic, band, exposure, inspection, toolsense

_clients: set[WebSocket] = set()
_loop: asyncio.AbstractEventLoop | None = None


def _fan_out(event: Event) -> None:
    """Runs on the bus thread: hand the event to the web loop, never block."""
    if _loop is None or not _clients:
        return
    text = json.dumps({"kind": "event", "event": event.to_dict()})
    for client in list(_clients):
        asyncio.run_coroutine_threadsafe(_send(client, text), _loop)


async def _send(client: WebSocket, text: str) -> None:
    try:
        await client.send_text(text)
    except Exception:
        _clients.discard(client)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop
    config.load_env()
    _loop = asyncio.get_running_loop()
    bus.subscribe(_fan_out)
    bus.start()
    yield
    bus.stop()


app = FastAPI(title="BeltGuard", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
                   allow_methods=["*"], allow_headers=["*"])
for module in (band, toolsense, inspection, exposure, acoustic):
    app.include_router(module.router)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "dropped_events": bus.dropped_events, "dropped_imu": bus.dropped_imu,
            **config.current_shift()}


@app.get("/api/events")
def events(shift_id: str | None = None, limit: int = 1000) -> list[dict]:
    return database.list_events(shift_id or config.current_shift()["shift_id"], limit=limit)


@app.get("/api/plan/optimise")
def plan_optimise(shift_id: str | None = None, exposure_cap: float = 100.0, max_plans: int = 3) -> dict:
    """CP-SAT alternative plans. Proposals only: nothing is assigned or approved."""
    from . import planner_cpsat
    return planner_cpsat.plan(shift_id or config.current_shift()["shift_id"], exposure_cap, max_plans)


@app.get("/api/shift")
def shift(shift_id: str | None = None) -> dict:
    """The dashboard's ShiftData, built only from recorded events and live state."""
    return shift_view.build(shift_id)


@app.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    await socket.accept()
    _clients.add(socket)
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(socket)
