"""Ali's work-order flow (repo-root package `workorder/`, merged from main at 17:32) mounted into the live
Vexora server, without a restart. Called from wo_live.install (wo_live hot-reloads itself when saved).

Adds, ahead of the static-files mount:
    POST /api/workorder/run            the one "Work order" button: draft, checks, schedule, create, calendar, verify
    GET  /api/workorder                list
    GET  /api/workorder/{id}           one
    GET  /api/workorder/{id}/event.ics calendar file
    GET  /workorder                    Ali's page (prefilled from the URL when Vexora sends a voice work order)
"""
from __future__ import annotations

import importlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # repo root
PAGE = os.path.join(ROOT, "workorder", "static", "index.html")


def install(fapp) -> list:
    from fastapi.responses import FileResponse
    from fastapi.routing import APIRoute

    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    import workorder.store, workorder.providers, workorder.flow, workorder.app          # noqa: E401
    for name in ("workorder.store", "workorder.providers", "workorder.flow", "workorder.app"):
        importlib.reload(sys.modules[name])                 # pick up edits to Ali's files on every install
    woapp = sys.modules["workorder.app"]

    routes = fapp.router.routes
    routes[:] = [r for r in routes if not (isinstance(r, APIRoute)
                                           and (r.path.startswith("/api/workorder") or r.path == "/workorder"))]
    before = len(routes)
    for r in woapp.router.routes:
        if isinstance(r, APIRoute):
            fapp.router.add_api_route(r.path, r.endpoint, methods=sorted(r.methods), name="workorder_" + r.name)

    def workorder_page():
        return FileResponse(PAGE, headers={"Cache-Control": "no-store"})

    fapp.router.add_api_route("/workorder", workorder_page, methods=["GET"], name="workorder_page")
    added = routes[before:]
    del routes[before:]
    routes[0:0] = added                                     # ahead of app.mount("/", StaticFiles)
    return [r.path for r in added]
