"""Work order PDF, live (S1 side session): every new work order gets a one-page PDF the supervisor opens.

install(S) is called from fleet.py (it hot-reloads; app.py does not). It is idempotent and:
  * puts GET /api/work_orders/{wo_id}/pdf      (application/pdf, inline; rendered by wo_pdf.py on request)
    and GET /api/work_orders/recent?limit=8   (newest first, each with pdf_url) at the FRONT of the router,
    so the "/" static mount never shadows them;
  * adds pdf_url to snapshot()["work_orders"];
  * starts one watcher task: for each work order created after install it
      - adds a "Work order PDF sent to supervisor · WO-xxxx" row to the job agent's actions (dashboard
        "BeltGuard did") and broadcasts it as {"type":"agent_action"},
      - broadcasts {"type":"work_order_pdf", id, url, title, station_id, ts, worker_id} (control room),
      - writes a shift event (kind wo_pdf_sent) with refs.pdf_url.
The watcher re-installs itself when this file changes, so later fixes here need no fleet.py edit.
Nothing here approves, closes or verifies work: the PDF is a draft and a person decides.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import time
import types

PDF_PATH = "/api/work_orders/{wo_id}/pdf"
LIST_PATH = "/api/work_orders/recent"
SENT_TITLE = "Work order PDF sent to supervisor"


def pdf_url(wo_id: str) -> str:
    return "/api/work_orders/%s/pdf" % wo_id


def _log(*a) -> None:
    print("[wo_live]", *a, flush=True)


def _rows(S, sql: str, params=()) -> list:
    try:
        return list(S.store.query(sql, params) or [])
    except Exception as e:
        _log("query failed:", repr(e))
        return []


def _as_list(v) -> list:
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.strip().startswith("["):
        try:
            return json.loads(v)
        except Exception:
            return []
    return []


def _pdf_mod(S):
    mods = getattr(S, "mods", None)
    mod = mods.get("wo_pdf") if mods is not None else None      # app's mtime hot reload
    if mod is None:
        mod = importlib.import_module("wo_pdf")
    return mod


# ---------------------------------------------------------------------------------- data for one PDF
def gather(S, wo: dict) -> dict:
    wo_id = wo.get("id")
    stations = {s.get("id"): s for s in (S.store.stations() or [])}
    st = stations.get(wo.get("station_id")) or {}
    conveyor = S.store.conveyor() or {}
    worker = S.store.worker() or {}
    defect = (S.store.get("defects", wo.get("defect_id")) if wo.get("defect_id") else None) or {}

    tr = _rows(S, "SELECT * FROM transcripts WHERE work_order_id = ? ORDER BY ts LIMIT 1", (wo_id,))
    tr = tr[0] if tr else {}
    said_text = _key_sentences(tr.get("text") or defect.get("transcript"))
    voice = (tr.get("input") == "voice") if tr else (wo.get("source") in ("band", "band-mic"))
    said = {"text": said_text, "ts": tr.get("ts") or defect.get("ts") or wo.get("ts"),
            "source": "band-mic" if voice else "typed"} if said_text else None

    # job steps so far: the job agent's checklist when it is this job (or any steps it has), else the drafted steps
    steps = []
    agent = getattr(S, "job_agent", None)
    job, actions = {}, []
    try:
        snap = agent.snapshot() if agent is not None else {}
        job, actions = (snap or {}).get("job") or {}, (snap or {}).get("agent_actions") or []
    except Exception as e:
        _log("job agent snapshot failed:", repr(e))
    if job.get("steps"):
        steps = [{"title": x.get("title") or x.get("text"), "status": x.get("status") or "todo"} for x in job["steps"]]
    if not steps:
        steps = [{"title": str(x), "status": "planned"} for x in _as_list(wo.get("steps"))]

    ss, _until, _live = _shift_window(S, wo)
    iso = _rows(S, "SELECT ts, title FROM shift_events WHERE ts >= ? AND kind = 'isolation_claim' ORDER BY ts DESC LIMIT 1",
                (ss,))
    if iso:
        isolation = "Reported by the technician at %s. A claim only: an authorized person verifies isolation." % \
                    time.strftime("%H:%M", time.localtime(float(iso[0]["ts"])))
    else:
        isolation = "Not reported yet. Isolation must be verified by an authorized person before work starts."

    tool, exposure, summary = None, {"action": 100, "limit": 400}, {}
    try:
        records = _rows(S, "SELECT * FROM exposure_records WHERE ts >= ? AND ts < ? ORDER BY ts", (ss, _until))
        assignment, _verdict = S.assignment_view(records)
        summary = S.exposure_summary(records) or {}
        t = (assignment or {}).get("tool") or {}
        if t:
            tool = {"id": t.get("id"), "name": t.get("name")}
        pts = float(summary.get("total_points") or 0.0)
        trig = sum(float((v or {}).get("trigger_s") or 0) for v in (summary.get("by_tool") or {}).values()) / 60.0
        action, limit = float(summary.get("action") or 100), float(summary.get("limit") or 400)
        exposure = {"points": round(pts, 1), "trigger_min": round(trig, 1), "action": action, "limit": limit,
                    "band": "within" if pts < action else ("action" if pts < limit else "over")}
    except Exception as e:
        _log("exposure for PDF failed:", repr(e))

    parts = [str(a.get("text") or a.get("title")) for a in actions
             if str(a.get("kind") or "") == "parts_request" and (a.get("text") or a.get("title"))][:5]

    return {
        "wo_id": wo_id, "status": "draft", "title": wo.get("title"),   # BeltGuard's PDF is always a draft: a person decides
        "priority": wo.get("priority"), "priority_reason": wo.get("priority_reason"),
        "conveyor": {"id": conveyor.get("id") or wo.get("conveyor_id") or "CV-102", "name": conveyor.get("name")},
        "station": {"id": st.get("id") or wo.get("station_id"), "name": st.get("name")},
        "component": defect.get("component"), "condition": defect.get("condition"), "severity": defect.get("severity"),
        "said": said, "job_steps": steps, "isolation": isolation, "tool": tool, "exposure": exposure, "parts": parts,
        "technician": worker.get("id") or "W-01", "technician_name": worker.get("name"), "band": "07",
        "created_ts": wo.get("ts"), "record": _record(S, wo, steps, summary),
    }


# ---------------------------------------------------------------------------------- shift record (S0 18:45)
def _shift_window(S, wo: dict):
    """(start, end, still_running) of the shift this work order was created in: shift_start events bound it."""
    now = time.time()
    t_wo = float(wo.get("ts") or now)
    a = _rows(S, "SELECT MAX(ts) AS ts FROM shift_events WHERE kind = 'shift_start' AND ts <= ?", (t_wo + 1,))
    since = float((a[0] or {}).get("ts") or 0) if a else 0.0
    if not since:
        since = float(S.shift_start()) if hasattr(S, "shift_start") else t_wo - 3600
    b = _rows(S, "SELECT MIN(ts) AS ts FROM shift_events WHERE kind = 'shift_start' AND ts > ?", (t_wo + 1,))
    nxt = float((b[0] or {}).get("ts") or 0) if b else 0.0
    return since, (nxt or now), not nxt


def _record(S, wo: dict, job_steps: list, summary: dict) -> dict:
    """Everything band 07 recorded in the shift this work order belongs to (to now, if it is still running):
    every transcript line in full, the motion sensor minute by minute, tool checkout and use, exposure, job steps
    and every shift event. wo_pdf draws it as pages 2+."""
    try:
        now = time.time()
        since, until, live = _shift_window(S, wo)
        names = {r.get("id"): r.get("name") for r in _rows(S, "SELECT id, name FROM tool_profiles")}

        said = _rows(S, "SELECT ts, text, input, intent, response, acted FROM transcripts WHERE ts >= ? AND ts < ? "
                        "ORDER BY ts", (since, until))
        events = _rows(S, "SELECT ts, lane, kind, title, detail FROM shift_events WHERE ts >= ? AND ts < ? "
                          "AND kind NOT IN ('transcript', 'shift_start') ORDER BY ts", (since, until))
        outs = _rows(S, "SELECT ts, worker_id, tool_id, returned_ts FROM tool_assignments WHERE ts < ? AND "
                        "(returned_ts >= ? OR (returned_ts IS NULL AND ts >= ?)) ORDER BY ts", (until, since, since - 43200))
        ses = _rows(S, "SELECT * FROM tool_sessions WHERE COALESCE(wall_start, ts) >= ? AND COALESCE(wall_start, ts) < ? "
                       "ORDER BY COALESCE(wall_start, ts)", (since, until))
        sessions = [{"start": x.get("wall_start") or x.get("ts"),
                     "end": x.get("wall_end") if x.get("t_end") is not None or x.get("status") != "active" else None,
                     "tool": names.get(x.get("tool_id")) or x.get("tool_id"), "trigger_s": x.get("trigger_s"),
                     "confidence": x.get("confidence"), "points": x.get("points"), "status": x.get("status")}
                    for x in ses]
        conn = dict(getattr(S, "connection", None) or {})
        sensor = {"link": conn.get("transport") or conn.get("link"), "rate": conn.get("rate"),
                  "sensors": conn.get("sensors"), "lost": conn.get("lost_total"), "raw_file": conn.get("raw_file")}
        sensor.update(_motion(S, since, until, sessions))

        by_tool = {}
        for tid, v in ((summary or {}).get("by_tool") or {}).items():
            if isinstance(v, dict):
                by_tool[tid] = dict(v, name=names.get(tid) or tid)
        exposure = {k: (summary or {}).get(k) for k in ("total_points", "action", "limit", "status", "method")}
        exposure["by_tool"] = by_tool

        steps = [{"title": x.get("title"), "status": x.get("status") or "todo", "detail": x.get("detail") or ""}
                 for x in (job_steps or []) if isinstance(x, dict)]
        return {"since": since, "until": until, "live": live, "made": now,
                "transcript": said, "sensor": sensor, "checkouts": [dict(c, tool=names.get(c.get("tool_id"))
                                                                         or c.get("tool_id")) for c in outs],
                "sessions": sessions, "exposure": exposure, "steps": steps, "events": events}
    except Exception as e:
        _log("shift record for PDF failed:", repr(e))
        return {}


def _motion(S, since: float, until: float, sessions: list) -> dict:
    """motion_samples between since and until, per minute (5 min for shifts over 3 h): sample count, movement
    (spread of the wrist acceleration, g rms), rotation (gyro, deg/s rms), peak |a| and the tool time inside it.
    Uses its own read-only connection (this runs in a worker thread) and finds the id range by binary search, so
    the 5M-row table is never scanned."""
    import math
    import sqlite3
    from urllib.request import pathname2url
    path = getattr(getattr(S, "store", None), "path", None)
    if not path:
        return {}
    try:
        con = sqlite3.connect("file:%s?mode=ro" % pathname2url(os.path.abspath(str(path))), uri=True, timeout=5)
    except Exception:
        con = sqlite3.connect(str(path), timeout=5)
    try:
        cur = con.cursor()
        lo, hi = cur.execute("SELECT MIN(id), MAX(id) FROM motion_samples").fetchone()
        if lo is None:
            return {}

        def first_id(t: float) -> int:
            x, y = lo, hi + 1
            while x < y:
                m = (x + y) // 2
                r = cur.execute("SELECT ts FROM motion_samples WHERE id >= ? ORDER BY id LIMIT 1", (m,)).fetchone()
                if r is None or r[0] >= t:
                    y = m
                else:
                    x = m + 1
            return x

        i0, i1 = first_id(since), first_id(until)
        bucket = 60 if until - since <= 3 * 3600 else 300
        rows = cur.execute(
            "SELECT CAST(ts / ? AS INTEGER), COUNT(*), SUM(ax), SUM(ay), SUM(az), SUM(ax*ax), SUM(ay*ay), SUM(az*az), "
            "SUM(gx*gx + gy*gy + gz*gz), MAX(ax*ax + ay*ay + az*az), MIN(ts), MAX(ts) FROM motion_samples "
            "WHERE id >= ? AND id < ? GROUP BY 1 ORDER BY 1", (bucket, i0, i1)).fetchall()
    finally:
        con.close()
    now = time.time()
    minutes, total, first, last = [], 0, None, None
    for k, n, sx, sy, sz, sxx, syy, szz, sg, pk, ta, tb in rows:
        if not n:
            continue
        var = (sxx / n - (sx / n) ** 2) + (syy / n - (sy / n) ** 2) + (szz / n - (sz / n) ** 2)
        b0, b1 = k * bucket, (k + 1) * bucket
        tool_s, tool = 0.0, ""
        for x in sessions:
            s0 = float(x.get("start") or 0)
            s1 = float(x.get("end") or now)
            ov = min(b1, s1) - max(b0, s0)
            if s0 and ov > 0:
                tool_s += ov
                tool = tool or (x.get("tool") or "tool")
        minutes.append({"t": b0, "n": n, "move_g": round(math.sqrt(max(var, 0.0)), 3),
                        "rot_dps": round(math.sqrt(max(sg / n, 0.0)), 1), "peak_g": round(math.sqrt(max(pk or 0, 0.0)), 2),
                        "tool_s": round(tool_s), "tool": tool})
        total += n
        first = ta if first is None else min(first, ta)
        last = tb if last is None else max(last, tb)
    out = {"samples": total, "first_ts": first, "last_ts": last, "minutes": minutes, "bucket_s": bucket}
    if total and first is not None and last and last > first:
        out["avg_hz"] = round(total / (last - first))
    return out


def render(S, wo_id: str):
    wo = S.store.get("work_orders", wo_id)
    if not wo:
        return None, None
    mod = _pdf_mod(S)
    data = gather(S, wo)
    name = mod.pdf_filename(data) if hasattr(mod, "pdf_filename") else "BeltGuard-%s.pdf" % wo_id
    return mod.build_work_order_pdf(data), name


# ---------------------------------------------------------------------------------- routes
def _routes(S):
    from fastapi import HTTPException
    from fastapi.responses import Response

    async def work_order_pdf(wo_id: str):
        try:
            pdf, name = await asyncio.to_thread(render, S, wo_id)
        except Exception as e:
            _log("PDF failed for", wo_id, repr(e))
            raise HTTPException(500, "work order PDF failed: %s" % type(e).__name__)
        if pdf is None:
            raise HTTPException(404, "no work order %s" % wo_id)
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": 'inline; filename="%s"' % name, "Cache-Control": "no-store"})

    async def work_orders_recent(limit: int = 8):
        limit = max(1, min(int(limit or 8), 50))
        ss = S.shift_start() if hasattr(S, "shift_start") else 0
        stations = {s.get("id"): s.get("name") for s in (S.store.stations() or [])}
        rows = _rows(S, "SELECT id, ts, title, station_id, priority, status, source FROM work_orders WHERE ts >= ? "
                        "ORDER BY ts DESC LIMIT ?", (ss, limit))
        return {"rows": [dict(r, station_name=stations.get(r.get("station_id")), worker_id="W-01",
                              pdf_url=pdf_url(r["id"])) for r in rows]}

    return work_order_pdf, work_orders_recent


def _fastapi_app(S):
    mod = sys.modules.get(type(S).__module__)
    return getattr(mod, "app", None)


def _put_route(fapp, path: str, endpoint, name: str) -> None:
    from fastapi.routing import APIRoute
    routes = fapp.router.routes
    routes[:] = [r for r in routes if not (isinstance(r, APIRoute) and r.path == path)]
    fapp.router.add_api_route(path, endpoint, methods=["GET"], name=name)
    routes.insert(0, routes.pop())                     # ahead of app.mount("/", StaticFiles)


def _patch_snapshot(S) -> None:
    base = type(S).snapshot

    def snapshot(self):
        snap = base(self)
        try:
            for w in snap.get("work_orders") or []:
                if isinstance(w, dict) and w.get("id"):
                    w["pdf_url"] = pdf_url(w["id"])
        except Exception:
            pass
        return snap
    S.snapshot = types.MethodType(snapshot, S)


# ---------------------------------------------------------------------------------- the watcher
async def announce(S, wo: dict) -> None:
    wo_id, url = wo["id"], pdf_url(wo["id"])
    now = time.time()
    text = "%s · %s" % (SENT_TITLE, wo_id)
    detail = "In the control room as a new work order with the PDF. A person decides."
    # refs point at THIS work order's own sentence (or none): never the job agent's last utterance (S2's catch),
    # because the dashboard puts the action's chip on the transcript line whose id matches.
    tr = _rows(S, "SELECT id FROM transcripts WHERE work_order_id = ? ORDER BY ts LIMIT 1", (wo_id,))
    refs = {"work_order_id": wo_id}
    if tr and tr[0].get("id"):
        refs["transcript_id"] = tr[0]["id"]
    a = None
    agent = getattr(S, "job_agent", None)
    if agent is not None and hasattr(agent, "_action"):
        try:
            a = agent._action("work_order_pdf", text, "sent", now, detail=detail, work_order_id=wo_id,
                              pdf_url=url, url=url, short="PDF sent to supervisor")
            a["refs"] = dict(refs)                       # same dict object as in agent.actions: fixes it there too
        except Exception as e:
            _log("agent action failed:", repr(e))
    if a is None:
        a = {"id": "PDF-%s" % wo_id, "ts": now, "kind": "work_order_pdf", "text": text, "title": text, "detail": detail,
             "status": "sent", "refs": dict(refs), "pdf_url": url, "url": url,
             "short": "PDF sent to supervisor"}
    await S.hub.broadcast({"type": "agent_action", "source": "system", "action": a})
    await S.hub.broadcast({"type": "work_order_pdf", "source": "system", "id": wo_id, "url": url,
                           "title": wo.get("title"), "station_id": wo.get("station_id"), "priority": wo.get("priority"),
                           "ts": wo.get("ts") or now, "worker_id": "W-01",
                           "text": "New work order %s from W-01" % wo_id})
    await S.event("work_order", "wo_pdf_sent", text, detail=wo.get("title") or "", station_id=wo.get("station_id"),
                  refs={"work_order_id": wo_id, "pdf_url": url}, source="system")
    _log("announced", wo_id, url)


async def watch(S, key) -> None:
    me = os.path.abspath(__file__)
    while True:
        await asyncio.sleep(1.0)
        try:
            k = os.path.getmtime(me)
            if k != key:                                 # this file changed: re-install from the new code
                key = k
                try:
                    importlib.reload(sys.modules[__name__]).install(S)
                    return
                except Exception as e:
                    _log("reload failed, keeping the running version:", repr(e))
            since = getattr(S, "_wo_pdf_since", time.time())
            rows = _rows(S, "SELECT * FROM work_orders WHERE ts > ? ORDER BY ts LIMIT 10", (since,))
            for wo in rows:
                S._wo_pdf_since = max(float(wo.get("ts") or 0), getattr(S, "_wo_pdf_since", 0))
                seen = S.__dict__.setdefault("_wo_pdf_seen", set())
                if wo["id"] in seen:
                    continue
                seen.add(wo["id"])
                await announce(S, wo)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _log("watch tick failed:", repr(e))


def _key_sentences(text):
    """The sentences about the fault, not the chatter around them (Deepgram can merge a minute of talk into one
    'sentence'). Keeps the sentences that are a defect report or name a part or station; else the last two."""
    import re
    if not text:
        return text
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", str(text)) if p.strip()]
    if len(parts) <= 2:
        return str(text).strip()
    try:
        import inspection
        is_report = lambda p: inspection.is_inspection_report(p, allow_trigger=False)
    except Exception:
        is_report = lambda p: False
    keep = [p for p in parts if is_report(p) or re.search(
        r"(?i)station|idler|roller|bearing|pulley|guard|belt|skirt|scraper|seized|ceased|worn|cracked|broken|leak", p)]
    return " ".join((keep or parts)[-3:])


def install(S) -> dict:
    """Idempotent. Call on the event loop (fleet.py's Fleet.__init__ runs inside the app's fleet loop)."""
    fapp = _fastapi_app(S)
    if fapp is None:
        raise RuntimeError("FastAPI app not found next to %s" % type(S).__module__)
    pdf_ep, list_ep = _routes(S)
    _put_route(fapp, PDF_PATH, pdf_ep, "work_order_pdf")
    _put_route(fapp, LIST_PATH, list_ep, "work_orders_recent")
    try:                                                 # Ali's work-order flow (merged from main), S0 17:40
        import workorder_live
        _log("Ali's work-order flow mounted:", importlib.reload(workorder_live).install(fapp))
    except Exception as e:
        _log("Ali's work-order flow NOT mounted:", repr(e))
    _patch_snapshot(S)
    if not hasattr(S, "_wo_pdf_since"):                  # announce only work orders created from now on
        last = _rows(S, "SELECT MAX(ts) AS ts FROM work_orders")
        S._wo_pdf_since = float((last[0] or {}).get("ts") or 0) if last else time.time()
    old = getattr(S, "_wo_pdf_task", None)
    if old is not None and not old.done():
        old.cancel()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = getattr(S.hub, "loop", None)
    S._wo_pdf_task = loop.create_task(watch(S, os.path.getmtime(os.path.abspath(__file__))))
    _log("installed: %s, %s, watcher on" % (PDF_PATH, LIST_PATH))
    return {"pdf": PDF_PATH, "list": LIST_PATH}
