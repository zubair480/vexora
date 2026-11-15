#!/usr/bin/env python3
"""BeltGuard end-to-end check (S5 qa-verify).

Drives one inspection-and-repair shift against the RUNNING backend (sim mode) and prints a
PASS / WARN / FAIL table. It never starts or stops the backend and never touches the band.

    python beltguard/qa/e2e.py                 # http://127.0.0.1:8765, keeps the current shift
    python beltguard/qa/e2e.py --reset         # POST /api/reset first (new shift)
    python beltguard/qa/e2e.py --tool-seconds 8 --base http://127.0.0.1:8765

Sequence (CONTRACT.md): settle the sim on "idle" -> GET /api/state -> POST /api/station S3 ->
POST /api/report (defect) -> POST /api/report (vague, expect clarify) -> POST /api/tool
impact_wrench -> sim scene "tool" for ~8 s, then "idle" -> POST /api/repair/plan S5.
A WebSocket listener records every /ws message for the whole run; the SQLite file is opened
read-only at the end. Full results go to beltguard/qa/last_run.json. Exit code 0 = no FAIL,
1 = FAIL, 2 = not run: when the backend is on the real band the test stops after GET /api/health,
so it never writes test defects into a live shift (--allow-band overrides). --db-only runs just the
read-only SQLite audit, which is safe at any time.

The settle step and the "seconds" on the closing idle scene keep the sim's own auto loop
(walk/idle/gesture/walk/tool/idle) from starting a tool session in the middle of the test.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import httpx
except ImportError:  # urllib fallback below
    httpx = None

from websockets.asyncio.client import connect as ws_connect

QA = Path(__file__).resolve().parent
ROOT = QA.parent
DB_PATH = ROOT / "data" / "beltguard.db"

SOURCES = {"band", "user", "ai", "rules", "system", "sim"}
LANES = {"walking", "inspection", "tool", "exposure", "work_order", "system"}
CONN_STATES = {"connected", "connecting", "disconnected", "simulated", "error"}
DETECTOR_STATES = {"idle", "walking", "gesture", "candidate", "tool_active", "unresolved"}
STATION_STATUS = {"ok", "inspected", "defect", "active"}
WO_STATUS = {"draft", "planned", "in_progress", "awaiting_verification"}
TS_STATUS = {"active", "confirmed", "unresolved", "rejected"}
SEVERITY = {"low", "medium", "high", "critical"}
EXPO_STATUS = {"below_action", "above_action", "above_limit"}
REC_STATUS = {"ok", "concern", "over_limit"}
PINNED_KINDS = {"defect", "wo_draft", "wo_planned", "wo_in_progress", "threshold", "projection", "clarify", "shift_start"}
CONTRACT_TABLES = ["workers", "conveyors", "stations", "inspection_events", "defects", "tool_profiles",
                   "tool_sessions", "exposure_records", "work_orders", "shift_events"]
SEED_STATIONS = [("S1", "Tail pulley & loading zone", 0.08), ("S2", "Carry idlers & belt tracking", 0.30),
                 ("S3", "Return idlers", 0.52), ("S4", "Take-up & belt tension", 0.74),
                 ("S5", "Head/drive pulley & discharge", 0.94)]

DEFECT_TEXT = "Conveyor CV-102, station three, return idler seized, needs replacement."
VAGUE_TEXT = "something is broken"
TOOL_ID = "impact_wrench"
PLAN = {"station_id": "S5", "task": "Replace drive pulley lagging", "tool_id": TOOL_ID, "est_trigger_min": 30}


def short(x, n: int = 160) -> str:
    s = x if isinstance(x, str) else json.dumps(x, default=str)
    return s if len(s) <= n else s[: n - 3] + "..."


def num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


# ------------------------------------------------------------------------------------------
class Results:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, area: str, check: str, owner: str, status: str, detail="") -> bool:
        self.rows.append({"n": len(self.rows) + 1, "status": status, "area": area, "check": check,
                          "owner": owner, "detail": detail if isinstance(detail, str) else short(detail, 400)})
        return status == "PASS"

    def check(self, area: str, check: str, owner: str, ok, detail="", warn_only: bool = False) -> bool:
        return self.add(area, check, owner, "PASS" if ok else ("WARN" if warn_only else "FAIL"), detail)

    def counts(self) -> dict:
        out = {"PASS": 0, "WARN": 0, "FAIL": 0, "SKIP": 0}
        for r in self.rows:
            out[r["status"]] = out.get(r["status"], 0) + 1
        return out


class Http:
    def __init__(self, base: str, timeout: float = 60.0) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.client = httpx.AsyncClient(base_url=self.base, timeout=timeout) if httpx else None

    async def request(self, method: str, path: str, body=None):
        t0 = time.time()
        if self.client is not None:
            r = await self.client.request(method, path, json=body)
            code, text = r.status_code, r.text
        else:
            code, text = await asyncio.to_thread(self._urllib, method, path, body)
        try:
            data = json.loads(text) if text else None
        except ValueError:
            data = text
        return code, data, time.time() - t0

    def _urllib(self, method, path, body):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    async def get(self, path):
        return await self.request("GET", path)

    async def post(self, path, body=None):
        return await self.request("POST", path, body if body is not None else {})

    async def close(self):
        if self.client is not None:
            await self.client.aclose()


class WsLog:
    """Records every /ws message as (arrival time, parsed message)."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.msgs: list[tuple[float, dict]] = []
        self.errors: list[str] = []
        self.connected = asyncio.Event()
        self.t_open = None
        self.t_close = None

    async def run(self) -> None:
        try:
            async with ws_connect(self.url, max_size=64 * 2 ** 20, open_timeout=5, ping_interval=None) as ws:
                self.t_open = time.time()
                self.connected.set()
                async for raw in ws:
                    t = time.time()
                    try:
                        m = json.loads(raw)
                    except ValueError:
                        m = {"__raw__": str(raw)[:200]}
                    self.msgs.append((t, m if isinstance(m, dict) else {"__raw__": short(m)}))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.errors.append(repr(e))
            self.connected.set()
        finally:
            self.t_close = time.time()

    async def wait_for(self, pred, after: float = 0.0, timeout: float = 5.0):
        """First message that arrived at/after `after` and matches `pred` -> (t, msg), else None."""
        end = time.time() + timeout
        i = 0
        while True:
            while i < len(self.msgs):
                t, m = self.msgs[i]
                i += 1
                if t >= after:
                    try:
                        if pred(m):
                            return t, m
                    except Exception:
                        pass
            if time.time() >= end:
                return None
            await asyncio.sleep(0.05)


def is_event(kind: str, station_id: str | None = None, **refs):
    kinds = {kind} if isinstance(kind, str) else set(kind)

    def pred(m: dict) -> bool:
        if m.get("type") != "event":
            return False
        ev = m.get("event") or {}
        if ev.get("kind") not in kinds:
            return False
        if station_id is not None and ev.get("station_id") != station_id:
            return False
        r = ev.get("refs") or {}
        return all(r.get(k) == v for k, v in refs.items())
    return pred


# ------------------------------------------------------------------------------------------
def snapshot_problems(st) -> list[str]:
    """Differences between a snapshot `state` and the CONTRACT.md shape."""
    p: list[str] = []
    if not isinstance(st, dict):
        return ["state is %s, expected an object" % type(st).__name__]

    def need(obj, keys, where) -> bool:
        if not isinstance(obj, dict):
            p.append("%s is %s, expected object" % (where, type(obj).__name__))
            return False
        miss = [k for k in keys if k not in obj]
        if miss:
            p.append("%s missing %s" % (where, miss))
        return True

    def row_meta(obj, where):
        if not isinstance(obj, dict):
            return
        if not num(obj.get("ts")):
            p.append("%s.ts=%r not a number" % (where, obj.get("ts")))
        if obj.get("source") not in SOURCES:
            p.append("%s.source=%r not in contract set" % (where, obj.get("source")))

    need(st, ["worker", "conveyor", "stations", "active_station", "tools", "active_tool", "defects", "work_orders",
              "tool_sessions", "exposure", "recommendation", "timeline", "connection", "mic", "detector"], "state")
    need(st.get("worker"), ["id", "name"], "worker")
    if isinstance(st.get("worker"), dict) and st["worker"].get("id") != "W-01":
        p.append("worker.id=%r, seed says W-01" % st["worker"].get("id"))
    need(st.get("conveyor"), ["id", "name"], "conveyor")
    if isinstance(st.get("conveyor"), dict) and st["conveyor"].get("id") != "CV-102":
        p.append("conveyor.id=%r, seed says CV-102" % st["conveyor"].get("id"))

    stations = st.get("stations")
    if not isinstance(stations, list):
        p.append("stations is not a list")
    else:
        for s in stations:
            w = "stations[%s]" % (s.get("id") if isinstance(s, dict) else "?")
            if need(s, ["id", "number", "name", "pos", "status"], w) and s.get("status") not in STATION_STATUS:
                p.append("%s.status=%r not in %s" % (w, s.get("status"), sorted(STATION_STATUS)))
    for t in st.get("tools") or []:
        need(t, ["id", "name", "vibration_m_s2", "source"], "tools[%s]" % (t.get("id") if isinstance(t, dict) else "?"))
    if st.get("active_tool") is not None:
        need(st["active_tool"], ["id", "name", "vibration_m_s2", "source"], "active_tool")
    if not isinstance(st.get("tools"), list):
        p.append("tools is not a list")

    for d in st.get("defects") or []:
        w = "defects[%s]" % d.get("id")
        need(d, ["id", "ts", "source", "station_id", "component", "condition", "severity", "transcript"], w)
        row_meta(d, w)
        if d.get("severity") not in SEVERITY:
            p.append("%s.severity=%r not in %s" % (w, d.get("severity"), sorted(SEVERITY)))
    for o in st.get("work_orders") or []:
        w = "work_orders[%s]" % o.get("id")
        need(o, ["id", "ts", "source", "defect_id", "station_id", "title", "priority", "status", "steps",
                 "requires_isolation", "tool_id", "est_trigger_min"], w)
        row_meta(o, w)
        if o.get("status") not in WO_STATUS:
            p.append("%s.status=%r not in %s" % (w, o.get("status"), sorted(WO_STATUS)))
        if not isinstance(o.get("steps"), list):
            p.append("%s.steps is %s, expected list" % (w, type(o.get("steps")).__name__))
        if o.get("requires_isolation") is not True:
            p.append("%s.requires_isolation=%r, contract says true" % (w, o.get("requires_isolation")))
    for s in st.get("tool_sessions") or []:
        w = "tool_sessions[%s]" % s.get("id")
        need(s, ["id", "ts", "source", "t_start", "t_end", "trigger_s", "tool_id", "station_id", "work_order_id",
                 "confidence", "status", "points"], w)
        row_meta(s, w)
        if s.get("status") not in TS_STATUS:
            p.append("%s.status=%r not in %s" % (w, s.get("status"), sorted(TS_STATUS)))

    ex = st.get("exposure")
    if need(ex, ["total_points", "action", "limit", "status", "records"], "exposure"):
        if ex.get("action") != 100 or ex.get("limit") != 400:
            p.append("exposure action/limit = %r/%r, expected 100/400" % (ex.get("action"), ex.get("limit")))
        if ex.get("status") not in EXPO_STATUS:
            p.append("exposure.status=%r not in %s" % (ex.get("status"), sorted(EXPO_STATUS)))
        if not isinstance(ex.get("records"), list):
            p.append("exposure.records is not a list")
        if not num(ex.get("total_points")):
            p.append("exposure.total_points=%r not a number" % ex.get("total_points"))

    rec = st.get("recommendation")
    if rec is not None:
        if not isinstance(rec, dict):
            p.append("recommendation is %s, expected object or null" % type(rec).__name__)
        elif rec.get("requires_supervisor_review") is not True:
            p.append("recommendation.requires_supervisor_review=%r" % rec.get("requires_supervisor_review"))

    tl = st.get("timeline")
    if not isinstance(tl, list):
        p.append("timeline is not a list")
    else:
        # Contract: last 200 events. S2 (13:30) also pins up to 60 key events so walks can't push them out.
        plain = [e for e in tl if isinstance(e, dict) and e.get("kind") not in PINNED_KINDS]
        if len(plain) > 200 or len(tl) > 260:
            p.append("timeline has %d events (%d unpinned), contract says last 200 (+60 pinned)" % (len(tl), len(plain)))
        bad = 0
        for e in tl:
            if not isinstance(e, dict) or any(k not in e for k in ("id", "ts", "source", "lane", "kind", "title")) \
                    or e.get("lane") not in LANES or e.get("source") not in SOURCES or not num(e.get("ts")):
                bad += 1
        if bad:
            p.append("%d timeline events miss id/ts/source/lane/kind/title or use an unknown lane/source" % bad)

    c = st.get("connection")
    if need(c, ["state", "band", "rate", "lost_total"], "connection"):
        if c.get("state") not in CONN_STATES:
            p.append("connection.state=%r not in %s" % (c.get("state"), sorted(CONN_STATES)))
        if c.get("band") != 7:
            p.append("connection.band=%r, must be 7" % c.get("band"))
    if need(st.get("mic"), ["on"], "mic") and not isinstance(st["mic"].get("on"), bool):
        p.append("mic.on=%r not a bool" % st["mic"].get("on"))
    if need(st.get("detector"), ["state", "confidence"], "detector") and st["detector"].get("state") not in DETECTOR_STATES:
        p.append("detector.state=%r not in %s" % (st["detector"].get("state"), sorted(DETECTOR_STATES)))
    return p


def ws_message_problems(m: dict, t_recv: float) -> list[str]:
    """Contract checks for one /ws message."""
    p = []
    typ = m.get("type")
    if not isinstance(typ, str):
        return ["no 'type' (%s)" % short(m, 80)]
    if not num(m.get("ts")):
        p.append("%s: ts=%r not a number" % (typ, m.get("ts")))
    elif abs(m["ts"] - t_recv) > 5:
        p.append("%s: ts=%.3f is %.1f s off the laptop clock (expects unix seconds)" % (typ, m["ts"], m["ts"] - t_recv))
    if m.get("source") not in SOURCES:
        p.append("%s: source=%r not in %s" % (typ, m.get("source"), sorted(SOURCES)))
    if typ == "motion":
        miss = [k for k in ("rate", "t0", "dt", "n0", "lost_total", "accel", "gyro") if k not in m]
        if miss:
            p.append("motion missing %s" % miss)
        acc = m.get("accel")
        if not (isinstance(acc, list) and acc and all(isinstance(v, list) and len(v) == 3 for v in acc)):
            p.append("motion.accel is not a non-empty list of [x,y,z]")
        gy = m.get("gyro")
        if gy is not None and not (isinstance(gy, list) and all(isinstance(v, list) and len(v) == 3 for v in gy)):
            p.append("motion.gyro is not a list of [x,y,z]")
    elif typ == "activity":
        miss = [k for k in ("state", "confidence", "features") if k not in m]
        if miss:
            p.append("activity missing %s" % miss)
        if m.get("state") not in DETECTOR_STATES:
            p.append("activity.state=%r not a contract detector state" % m.get("state"))
    elif typ == "connection":
        if m.get("state") not in CONN_STATES:
            p.append("connection.state=%r" % m.get("state"))
        if m.get("band") != 7:
            p.append("connection.band=%r, must be 7" % m.get("band"))
    elif typ == "transcript":
        if "text" not in m or "final" not in m:
            p.append("transcript missing text/final")
    elif typ == "clarify":
        if not (isinstance(m.get("question"), str) and m["question"].strip()):
            p.append("clarify has no question")
    elif typ == "event":
        ev = m.get("event")
        if not isinstance(ev, dict):
            p.append("event.event is not an object")
        else:
            miss = [k for k in ("id", "ts", "source", "lane", "kind", "title", "detail", "station_id", "refs") if k not in ev]
            if miss:
                p.append("event %s missing %s" % (ev.get("kind"), miss))
            if ev.get("lane") not in LANES:
                p.append("event %s lane=%r not in %s" % (ev.get("kind"), ev.get("lane"), sorted(LANES)))
            if ev.get("source") not in SOURCES:
                p.append("event %s source=%r" % (ev.get("kind"), ev.get("source")))
            if not num(ev.get("ts")):
                p.append("event %s ts=%r" % (ev.get("kind"), ev.get("ts")))
    elif typ == "snapshot":
        sp = snapshot_problems(m.get("state"))
        if sp:
            p.append("snapshot: " + "; ".join(sp[:3]))
    return p


def db_uri(path: Path) -> str:
    return "file:" + urllib.request.pathname2url(str(path)) + "?mode=ro"


# ------------------------------------------------------------------------------------------
async def wait_detector_idle(http: Http, need_s: float = 2.0, timeout: float = 20.0):
    """Poll /api/state until the detector has said 'idle' for need_s seconds."""
    end = time.time() + timeout
    since = None
    last = None
    while time.time() < end:
        code, st, _ = await http.get("/api/state")
        last = ((st or {}).get("detector") or {}).get("state") if code == 200 else "HTTP %s" % code
        if last == "idle":
            since = since or time.time()
            if time.time() - since >= need_s:
                return True, last
        else:
            since = None
        await asyncio.sleep(0.4)
    return False, last


async def run(args) -> tuple[Results, dict]:
    R = Results()
    info: dict = {"base": args.base, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    http = Http(args.base)
    ws_url = "ws" + args.base[len("http"):].rstrip("/") + "/ws"

    # ---- 0. preflight ---------------------------------------------------------------------
    try:
        code, health, _ = await http.get("/api/health")
    except Exception as e:
        R.add("preflight", "backend reachable", "S2", "FAIL", "%s: %r" % (args.base, e))
        await http.close()
        return R, info
    health = health if isinstance(health, dict) else {}
    R.check("preflight", "GET /api/health", "S2", code == 200 and health.get("ok"), "HTTP %s" % code)
    conn = health.get("connection") or {}
    sim = bool(conn.get("simulated")) or conn.get("state") == "simulated"
    R.add("preflight", "backend is in sim mode", "S2", "PASS" if sim else "SKIP",
          "connection.state=%r simulated=%r" % (conn.get("state"), conn.get("simulated")))
    mods = health.get("modules") or {}
    R.check("preflight", "S4 modules loaded (not fallback)", "S4", bool(mods) and all(v == "ok" for v in mods.values()),
            short(mods), warn_only=True)
    R.check("preflight", "S3 detector.py loaded (not stub)", "S3", health.get("detector") == "detector.py",
            "detector=%r" % health.get("detector"), warn_only=True)
    if not sim and not args.allow_band:
        # The test writes defects, work orders and plans into the current shift: never into a real-band shift.
        info["aborted"] = ("backend is on the real band (state=%r), so the test did not run: it would write test "
                           "defects into the live shift. Rerun once S2 switches back to sim "
                           "(POST /api/band/connect {\"mode\":\"sim\"}), or pass --allow-band." % conn.get("state"))
        R.add("preflight", "sim mode required (no writes to a live-band shift)", "-", "SKIP", info["aborted"])
        await http.close()
        return R, info

    ws = WsLog(ws_url)
    ws_task = asyncio.create_task(ws.run())
    try:
        await asyncio.wait_for(ws.connected.wait(), 6)
    except asyncio.TimeoutError:
        ws.errors.append("open timeout")
    R.check("ws", "connect %s" % ws_url, "S2", ws.t_open is not None and not ws.errors, "; ".join(ws.errors) or "open")
    first = await ws.wait_for(lambda m: True, timeout=3)
    R.check("ws", "first message on connect is a snapshot", "S2", first is not None and first[1].get("type") == "snapshot",
            "got %r" % (first[1].get("type") if first else None))

    if args.reset:
        code, body, _ = await http.post("/api/reset")
        R.check("setup", "POST /api/reset (new shift)", "S2", code == 200, short(body))

    # ---- settle the sim so its auto loop cannot start a tool session mid-test ---------------
    if sim:
        code, body, _ = await http.post("/api/sim/scene", {"scene": "idle", "seconds": 60})
        R.check("setup", "settle: sim scene idle 60 s", "S2", code == 200, "HTTP %s %s" % (code, short(body)))
        ok, last = await wait_detector_idle(http)
        R.check("setup", "detector idle before the test", "S3", ok, "detector.state=%r" % last, warn_only=True)

    # ---- 1. GET /api/state ----------------------------------------------------------------
    code, st0, dt = await http.get("/api/state")
    R.check("rest", "GET /api/state", "S2", code == 200 and isinstance(st0, dict), "HTTP %s in %.2fs" % (code, dt))
    st0 = st0 if isinstance(st0, dict) else {}
    probs = snapshot_problems(st0)
    R.check("contract", "snapshot shape matches CONTRACT.md", "S2", not probs, "; ".join(probs[:5]) or "all keys/types ok")
    got = [(s.get("id"), s.get("name"), s.get("pos")) for s in st0.get("stations") or []]
    R.check("contract", "seed: CV-102 stations S1..S5 (names, pos)", "S2", got == SEED_STATIONS,
            "ok" if got == SEED_STATIONS else short(got))
    tools = {t.get("id"): t for t in st0.get("tools") or [] if isinstance(t, dict)}
    tool = tools.get(TOOL_ID) or {}
    a = tool.get("vibration_m_s2")
    R.check("contract", "tool profile impact_wrench has a and source", "S4", num(a) and bool(tool.get("source")),
            "a=%r source=%r" % (a, short(tool.get("source") or "", 70)))
    a = float(a) if num(a) else 5.0

    # ---- 2. POST /api/station S3 -----------------------------------------------------------
    t = time.time()
    code, body, dt = await http.post("/api/station", {"station_id": "S3"})
    R.check("rest", "POST /api/station S3", "S2", code == 200 and (body or {}).get("active_station") == "S3",
            "HTTP %s %s" % (code, short(body)))
    ev = await ws.wait_for(is_event("station", station_id="S3"), after=t, timeout=3)
    R.check("ws", "event 'station' S3 pushed", "S2", ev is not None,
            "source=%r" % ev[1]["event"].get("source") if ev else "no event within 3 s")
    sn = await ws.wait_for(lambda m: m.get("type") == "snapshot" and (m.get("state") or {}).get("active_station") == "S3",
                           after=t, timeout=3)
    R.check("ws", "snapshot pushed after a state change", "S2", sn is not None,
            "%.2f s after POST" % (sn[0] - t) if sn else "no snapshot with active_station S3 within 3 s")

    # ---- 3. POST /api/report (clear defect) ---------------------------------------------------
    t = time.time()
    code, rep, dt = await http.post("/api/report", {"text": DEFECT_TEXT})
    rep = rep if isinstance(rep, dict) else {}
    d = rep.get("defect") or {}
    wo = rep.get("work_order") or {}
    info["defect_id"], info["work_order_id"] = d.get("id"), wo.get("id")
    R.check("inspection", "report -> status ok", "S4", code == 200 and rep.get("status") == "ok",
            "HTTP %s in %.1fs: %s" % (code, dt, short(rep, 120)))
    R.check("inspection", "defect at S3 (said 'station three')", "S4", d.get("station_id") == "S3",
            "station_id=%r source=%r" % (d.get("station_id"), d.get("source")))
    comp, cond = str(d.get("component") or ""), str(d.get("condition") or "")
    R.check("inspection", "component + condition extracted", "S4", "idler" in comp.lower() and "seiz" in cond.lower(),
            "component=%r condition=%r severity=%r" % (comp, cond, d.get("severity")))
    R.check("inspection", "severity is low|medium|high|critical", "S4", d.get("severity") in SEVERITY,
            "severity=%r" % d.get("severity"))
    wo_ok = (wo.get("status") == "draft" and wo.get("defect_id") == d.get("id") and wo.get("station_id") == "S3"
             and wo.get("requires_isolation") is True and isinstance(wo.get("steps"), list) and len(wo["steps"]) > 0
             and bool(wo.get("title")) and bool(wo.get("priority")))
    R.check("inspection", "work-order draft linked to defect", "S4", wo_ok,
            "%s status=%r defect=%r prio=%r steps=%d isolation=%r tool=%r est=%r title=%r" % (
                wo.get("id"), wo.get("status"), wo.get("defect_id"), wo.get("priority"), len(wo.get("steps") or []),
                wo.get("requires_isolation"), wo.get("tool_id"), wo.get("est_trigger_min"), short(wo.get("title") or "", 50)))
    steps_txt = " ".join(str(s) for s in wo.get("steps") or []).lower()
    R.check("honesty", "WO steps leave isolation to an authorized person", "S4",
            "isolat" in steps_txt and ("authori" in steps_txt or "competent" in steps_txt),
            short(wo.get("steps") or [], 140), warn_only=True)
    e1 = await ws.wait_for(is_event("defect", defect_id=d.get("id")), after=t, timeout=3) if d.get("id") else None
    e2 = await ws.wait_for(is_event("wo_draft", work_order_id=wo.get("id")), after=t, timeout=3) if wo.get("id") else None
    R.check("ws", "events 'defect' + 'wo_draft' pushed", "S2", e1 is not None and e2 is not None,
            "defect=%s wo_draft=%s" % (bool(e1), bool(e2)))

    # ---- 4. POST /api/report (vague) -> clarify -------------------------------------------------
    code, sb, _ = await http.get("/api/state")
    n_def = len((sb or {}).get("defects") or [])
    n_wo = len((sb or {}).get("work_orders") or [])
    t = time.time()
    code, rep2, dt = await http.post("/api/report", {"text": VAGUE_TEXT})
    rep2 = rep2 if isinstance(rep2, dict) else {}
    R.check("inspection", "'something is broken' -> clarify", "S4", code == 200 and rep2.get("status") == "clarify",
            "HTTP %s in %.1fs: %s" % (code, dt, short(rep2, 140)))
    cl = await ws.wait_for(lambda m: m.get("type") == "clarify", after=t, timeout=4)
    R.check("ws", "clarify message pushed with a question", "S2",
            cl is not None and isinstance(cl[1].get("question"), str) and cl[1]["question"].strip(),
            short(cl[1].get("question") or "", 120) if cl else "no clarify message within 4 s")
    code, sa, _ = await http.get("/api/state")
    n_def2, n_wo2 = len((sa or {}).get("defects") or []), len((sa or {}).get("work_orders") or [])
    R.check("inspection", "vague report creates no defect / WO", "S4", n_def2 == n_def and n_wo2 == n_wo,
            "defects %d->%d, work orders %d->%d" % (n_def, n_def2, n_wo, n_wo2))

    # ---- 5. POST /api/tool ---------------------------------------------------------------------
    # S2 (14:4x): /api/tool is a checkout/return, so re-selecting an already checked-out tool is a no-op.
    # Return any open checkout first so this step always produces a fresh checkout (sim mode only).
    code, cur, _ = await http.get("/api/state")
    if isinstance(cur, dict) and cur.get("active_tool"):
        await http.post("/api/tool", {"tool_id": None})
        await asyncio.sleep(0.3)
    t = time.time()
    code, body, _ = await http.post("/api/tool", {"tool_id": TOOL_ID})
    at = (body or {}).get("active_tool") or {}
    R.check("rest", "POST /api/tool impact_wrench", "S2", code == 200 and at.get("id") == TOOL_ID,
            "HTTP %s active_tool=%r" % (code, at.get("id")))
    ev = await ws.wait_for(is_event(("tool_selected", "tool_checkout")), after=t, timeout=3)   # S2 14:4x: /api/tool = checkout
    R.check("ws", "event 'tool_selected'/'tool_checkout' pushed", "S2", ev is not None,
            ev[1]["event"].get("kind") if ev else "none within 3 s")
    if num(at.get("vibration_m_s2")):
        a = float(at["vibration_m_s2"])

    # ---- 6. sim tool scene -> tool session -> exposure ---------------------------------------
    sid = None
    st_after = None
    if not sim:
        R.add("sim", "tool scene (needs sim mode)", "S2", "SKIP", "backend is not in sim mode")
    else:
        # S2 (15:xx): a checkout switches the band to 416 Hz, which restarts the detector. Give it the few seconds a
        # technician needs to walk to the station before the tool starts (otherwise the scene can go undetected).
        await asyncio.sleep(3.0)
        t0 = time.time()
        code, body, _ = await http.post("/api/sim/scene", {"scene": "tool"})
        t1 = time.time()
        R.check("sim", "POST /api/sim/scene tool", "S2", code == 200, "HTTP %s %s" % (code, short(body)))
        start = await ws.wait_for(is_event("tool_start"), after=t0 - 0.2, timeout=args.tool_seconds)
        rest = t0 + args.tool_seconds - time.time()
        if rest > 0:
            await asyncio.sleep(rest)
        t2 = time.time()
        code, body, _ = await http.post("/api/sim/scene", {"scene": "idle", "seconds": 15})
        t3 = time.time()
        scene_s = (t2 + t3) / 2 - (t0 + t1) / 2
        info["scene_s"] = round(scene_s, 2)
        R.check("sim", "POST /api/sim/scene idle", "S2", code == 200, "HTTP %s, tool scene ran %.2f s" % (code, scene_s))
        if start is None:   # late start: still look for it
            start = await ws.wait_for(is_event("tool_start"), after=t0 - 0.2, timeout=2)
        sev = start[1]["event"] if start else {}
        sid = (sev.get("refs") or {}).get("tool_session_id")
        info["tool_session_id"] = sid
        R.check("detector", "tool_start event during tool scene", "S3", start is not None and bool(sid),
                ("%.2f s after scene start, session %s, source=%r" % (start[0] - t1, sid, sev.get("source")))
                if start else "no tool_start within %.0f s" % args.tool_seconds)
        if start:
            R.check("detector", "tool_start latency <= 3 s", "S3", start[0] - t1 <= 3.0, "%.2f s" % (start[0] - t1),
                    warn_only=True)
            R.check("honesty", "sim tool events labelled source 'sim'", "S2", sev.get("source") == "sim",
                    "tool_start source=%r" % sev.get("source"))
        stop = None
        if sid:
            stop = await ws.wait_for(is_event(("tool_stop", "tool_unresolved"), tool_session_id=sid), after=t0, timeout=15)
        pev = stop[1]["event"] if stop else {}
        R.check("detector", "tool_stop event after idle (confirmed)", "S3", stop is not None and pev.get("kind") == "tool_stop",
                ("%s %.2f s after idle: %s" % (pev.get("kind"), stop[0] - t3, short(pev.get("title") or "", 60)))
                if stop else "no tool_stop for %s within 15 s" % sid)
        wip = await ws.wait_for(is_event("wo_in_progress", work_order_id=wo.get("id")), after=t0, timeout=1) if wo.get("id") else None
        R.check("ws", "linked WO moved to in_progress", "S2", wip is not None, "%s" % wo.get("id"), warn_only=True)
        xev = await ws.wait_for(is_event("exposure", tool_session_id=sid), after=t0, timeout=5) if sid else None
        R.check("ws", "event 'exposure' pushed for the session", "S2", xev is not None,
                short(xev[1]["event"].get("title") or "", 80) if xev else "none within 5 s")

        code, st_after, _ = await http.get("/api/state")
        st_after = st_after if isinstance(st_after, dict) else {}
        sess = next((s for s in st_after.get("tool_sessions") or [] if s.get("id") == sid), None) if sid else None
        R.check("tool", "tool session in snapshot", "S2", sess is not None, sid or "no session id")
        sess = sess or {}
        R.check("tool", "session linked to S3 + its work order", "S2",
                sess.get("station_id") == "S3" and sess.get("work_order_id") == wo.get("id") and wo.get("id"),
                "station=%r wo=%r (expected S3, %r)" % (sess.get("station_id"), sess.get("work_order_id"), wo.get("id")))
        R.check("tool", "session confirmed with selected tool", "S2",
                sess.get("status") == "confirmed" and sess.get("tool_id") == TOOL_ID,
                "status=%r tool=%r conf=%r" % (sess.get("status"), sess.get("tool_id"), sess.get("confidence")))
        trig = sess.get("trigger_s")
        if num(trig):
            err = abs(trig - scene_s)
            R.add("detector", "trigger time ~ scene length", "S3",
                  "PASS" if err <= 1.0 else ("WARN" if err <= 2.5 else "FAIL"),
                  "trigger_s=%.2f vs scene %.2f s (err %.2f s)" % (trig, scene_s, err))
        else:
            R.add("detector", "trigger time ~ scene length", "S3", "FAIL", "trigger_s=%r" % trig)
        R.check("honesty", "sim tool session row source 'sim'", "S2", sess.get("source") == "sim", "source=%r" % sess.get("source"))

        ex = st_after.get("exposure") or {}
        recs = [r for r in ex.get("records") or [] if r.get("tool_session_id") == sid] if sid else []
        R.check("exposure", "exposure record for the session", "S2", len(recs) == 1, "%d record(s)" % len(recs))
        r = recs[0] if recs else {}
        ra, rt, rp = r.get("vibration_m_s2"), r.get("trigger_s"), r.get("points")
        R.check("exposure", "a = selected tool profile value", "S2", num(ra) and abs(ra - a) < 1e-9,
                "record a=%r, profile a=%r, vib_source=%r" % (ra, a, short(r.get("vib_source") or "", 50)))
        if num(ra) and num(rt) and num(rp):
            want = 2.0 * ra * ra * rt / 3600.0
            R.check("exposure", "points = 2*a^2*T (T hours)", "S4", abs(rp - want) <= max(0.005, 0.005 * want),
                    "points=%.4f, 2*%.1f^2*%.2fs/3600=%.4f" % (rp, ra, rt, want))
        else:
            R.add("exposure", "points = 2*a^2*T (T hours)", "S4", "FAIL", "a=%r T=%r points=%r" % (ra, rt, rp))
        R.check("exposure", "record trigger_s = session trigger_s", "S2", num(rt) and num(trig) and abs(rt - trig) < 0.051,
                "record %r vs session %r" % (rt, trig))
        R.check("exposure", "session.points = record.points", "S2", num(rp) and num(sess.get("points"))
                and abs(sess["points"] - rp) < 1e-6, "session %r vs record %r" % (sess.get("points"), rp))
        R.check("honesty", "sim exposure record source 'sim'", "S2", r.get("source") == "sim", "source=%r" % r.get("source"))
        R.check("honesty", "record method says SCREENING ESTIMATE", "S2", "screening" in str(r.get("method") or "").lower(),
                short(r.get("method") or "", 90))
        total = ex.get("total_points")
        ssum = sum(float(x.get("points") or 0) for x in ex.get("records") or []
                   if str(x.get("status") or "").lower() not in ("rejected", "unresolved", "active", "pending"))
        R.check("exposure", "day total = sum of confirmed records", "S4", num(total) and abs(total - ssum) <= 0.011,
                "total=%r sum=%.3f over %d records" % (total, ssum, len(ex.get("records") or [])))
        if num(total):
            want_status = "above_limit" if total >= 400 else "above_action" if total >= 100 else "below_action"
            R.check("exposure", "day status matches 100/400 thresholds", "S4", ex.get("status") == want_status,
                    "status=%r total=%.2f" % (ex.get("status"), total))
        R.check("honesty", "exposure labelled SCREENING ESTIMATE", "S4",
                "screening estimate" in str(ex.get("label") or "").lower(), short(ex.get("label") or "", 80))

    # ---- 7. POST /api/repair/plan S5 --------------------------------------------------------------
    t = time.time()
    code, rec, dt = await http.post("/api/repair/plan", PLAN)
    rec = rec if isinstance(rec, dict) else {}
    R.check("planner", "POST /api/repair/plan S5", "S2", code == 200, "HTTP %s in %.2fs" % (code, dt))
    R.check("planner", "requires_supervisor_review is true", "S4", rec.get("requires_supervisor_review") is True,
            "requires_supervisor_review=%r" % rec.get("requires_supervisor_review"))
    added, proj, cur = rec.get("added_points"), rec.get("projected_points"), rec.get("current_points")
    want_added = 2.0 * a * a * PLAN["est_trigger_min"] / 60.0
    R.check("planner", "added_points = 2*a^2*T for 30 min", "S4", num(added) and abs(added - want_added) <= 0.02,
            "added=%r expected %.2f" % (added, want_added))
    R.check("planner", "projected = current + added", "S4",
            num(proj) and num(cur) and num(added) and abs(proj - (cur + added)) <= 0.03,
            "projected=%r current=%r added=%r" % (proj, cur, added))
    acts = rec.get("actions")
    R.check("planner", "status, headline, actions present", "S4",
            rec.get("status") in REC_STATUS and bool(rec.get("headline")) and isinstance(acts, list) and len(acts) > 0,
            "status=%r headline=%r actions=%d" % (rec.get("status"), short(rec.get("headline") or "", 70),
                                                 len(acts) if isinstance(acts, list) else -1))
    if num(proj):
        if proj >= 400:
            okst = rec.get("status") == "over_limit"
        elif proj >= 100:
            okst = rec.get("status") == "concern"
        else:
            okst = rec.get("status") in ("ok", "concern")
        R.check("planner", "status matches projected points", "S4", okst, "status=%r projected=%.2f" % (rec.get("status"), proj))
    text = (" ".join(str(x) for x in acts or []) + " " + str(rec.get("headline") or "")).lower()
    R.check("honesty", "plan says supervisor review required", "S4", "supervisor" in text, short(acts or [], 120))
    R.check("honesty", "AI takes no automatic actions", "S4", not rec.get("automatic_actions"),
            "automatic_actions=%r" % rec.get("automatic_actions"))
    pj = await ws.wait_for(is_event("projection", station_id="S5"), after=t, timeout=3)
    R.check("ws", "event 'projection' S5 pushed", "S2", pj is not None, "ok" if pj else "none within 3 s")
    code, st_end, _ = await http.get("/api/state")
    st_end = st_end if isinstance(st_end, dict) else {}
    srec = st_end.get("recommendation") or {}
    R.check("planner", "snapshot.recommendation = S5 plan", "S2",
            (srec.get("planned") or {}).get("station_id") == "S5" and srec.get("requires_supervisor_review") is True,
            "planned=%s" % short(srec.get("planned") or {}, 120))
    pwo = (rec.get("planned") or {}).get("work_order_id")
    wrow = next((w for w in st_end.get("work_orders") or [] if w.get("id") == pwo), {})
    R.check("planner", "S5 work order is 'planned'", "S2", wrow.get("station_id") == "S5" and wrow.get("status") == "planned",
            "%s station=%r status=%r" % (pwo, wrow.get("station_id"), wrow.get("status")))

    # ---- 8. timeline + whole-run WebSocket audit -------------------------------------------------
    await asyncio.sleep(0.5)
    tl_ids = {e.get("id") for e in st_end.get("timeline") or []}
    run_events = [m["event"] for (_, m) in ws.msgs if m.get("type") == "event" and isinstance(m.get("event"), dict)]
    missing = [e.get("kind") for e in run_events if e.get("id") not in tl_ids]
    R.check("contract", "every event of this run is in snapshot.timeline", "S2", run_events and not missing,
            "%d events, missing %s" % (len(run_events), missing[:6]) if missing else "%d events all present" % len(run_events))
    final_probs = snapshot_problems(st_end)
    R.check("contract", "final snapshot shape matches CONTRACT.md", "S2", not final_probs,
            "; ".join(final_probs[:5]) or "all keys/types ok")

    ws_task.cancel()
    try:
        await ws_task
    except (asyncio.CancelledError, Exception):
        pass
    msgs = ws.msgs
    by_type: dict = {}
    bad: dict = {}
    for (tr, m) in msgs:
        by_type[m.get("type")] = by_type.get(m.get("type"), 0) + 1
        for prob in ws_message_problems(m, tr):
            key = prob.split(":")[0] if ":" in prob else prob
            bad.setdefault(key, [0, prob])
            bad[key][0] += 1
    info["ws_counts"] = by_type
    R.check("ws", "every message has type, ts (unix s) and source", "S2",
            msgs and not any(k for k in bad if "ts=" in bad[k][1] or "source=" in bad[k][1] or "no 'type'" in bad[k][1]),
            "%d messages" % len(msgs) if not bad else "; ".join("%dx %s" % (v[0], v[1]) for v in list(bad.values())[:4]))
    R.check("ws", "message payloads match CONTRACT.md", "S2", msgs and not bad,
            "types %s" % short(by_type, 120) if not bad else "; ".join("%dx %s" % (v[0], v[1]) for v in list(bad.values())[:4]))
    dur = (ws.t_close or time.time()) - (ws.t_open or time.time())
    if dur > 5:
        mr = by_type.get("motion", 0) / dur
        ar = by_type.get("activity", 0) / dur
        R.check("ws", "motion ~10/s, activity ~4/s", "S2", 5 <= mr <= 20 and 1 <= ar <= 10,
                "motion %.1f/s, activity %.1f/s over %.0f s" % (mr, ar, dur), warn_only=True)
    sims = [m for (_, m) in msgs if m.get("type") in ("motion", "activity")]
    nonsim = sum(1 for m in sims if m.get("source") != "sim")
    if sim:
        R.check("honesty", "sim motion/activity labelled source 'sim'", "S2", sims and nonsim == 0,
                "%d of %d not 'sim'" % (nonsim, len(sims)))
    others = [m for (_, m) in msgs if m.get("type") == "sim_scene"]
    if others:
        R.check("honesty", "sim_scene messages labelled source 'sim'", "S2",
                all(m.get("source") == "sim" for m in others),
                "sources %s" % sorted({str(m.get("source")) for m in others}), warn_only=True)

    # ---- 9. SQLite audit ---------------------------------------------------------------------------
    check_db(R, info)
    await http.close()
    return R, info


def check_db(R: Results, info: dict) -> None:
    if not DB_PATH.exists():
        R.add("db", "data/beltguard.db exists", "S2", "FAIL", str(DB_PATH))
        return
    try:
        con = sqlite3.connect(db_uri(DB_PATH), uri=True, timeout=5)
    except sqlite3.Error as e:
        R.add("db", "open beltguard.db read-only", "S2", "FAIL", repr(e))
        return
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        missing = [t for t in CONTRACT_TABLES if t not in tables]
        R.check("db", "all 10 contract tables exist", "S2", not missing, "missing %s" % missing if missing else ", ".join(tables))
        lacking_all, nulls, badsrc, extra = [], [], [], []
        counts = {}
        srcq = ",".join("'%s'" % s for s in sorted(SOURCES))
        for t in tables:
            cols = [r[1] for r in con.execute('PRAGMA table_info("%s")' % t)]
            lacking = [c for c in ("id", "ts", "source") if c not in cols]
            counts[t] = con.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
            if lacking:
                (lacking_all if t in CONTRACT_TABLES else extra).append("%s lacks %s" % (t, lacking))
                continue
            n = con.execute('SELECT COUNT(*) FROM "%s" WHERE id IS NULL OR ts IS NULL OR source IS NULL' % t).fetchone()[0]
            if n:
                nulls.append("%s: %d rows" % (t, n))
            for src, k in con.execute('SELECT source, COUNT(*) FROM "%s" WHERE source NOT IN (%s) GROUP BY source' % (t, srcq)):
                badsrc.append("%s: %dx %r" % (t, k, src))
        info["db_rows"] = counts
        R.check("db", "contract tables have id, ts, source columns", "S2", not lacking_all, "; ".join(lacking_all) or "ok")
        R.check("db", "no row has NULL id/ts/source", "S2", not nulls, "; ".join(nulls) or "%d rows" % sum(counts.values()))
        R.check("db", "every source is band|user|ai|rules|system|sim", "S2", not badsrc, "; ".join(badsrc[:4]) or "ok")
        R.check("db", "extra tables also carry id/ts/source", "S2", not extra,
                "; ".join(extra) + " (internal key-value store, not a contract entity)" if extra else "none",
                warn_only=True)
        sid = info.get("tool_session_id")
        if sid:
            row = con.execute("SELECT source, station_id, work_order_id, status FROM tool_sessions WHERE id = ?", (sid,)).fetchone()
            x = con.execute("SELECT COUNT(*) FROM exposure_records WHERE tool_session_id = ?", (sid,)).fetchone()[0]
            R.check("db", "tool session + exposure persisted", "S2", row is not None and x == 1,
                    "%s row=%r exposure_records=%d" % (sid, row, x))
    finally:
        con.close()


# ------------------------------------------------------------------------------------------
def print_table(R: Results, info: dict, elapsed: float) -> None:
    rows = R.rows
    w_area = max(4, max((len(r["area"]) for r in rows), default=4))
    w_check = min(48, max(5, max((len(r["check"]) for r in rows), default=5)))
    line = "+-----+------+-%s-+-%s-+-------+-%s-+" % ("-" * w_area, "-" * w_check, "-" * 62)
    print(line)
    print("| %3s | %-4s | %-*s | %-*s | %-5s | %-62s |" % ("#", "res", w_area, "area", w_check, "check", "owner", "detail"))
    print(line)
    for r in rows:
        det = r["detail"].replace("\n", " ")
        det = det if len(det) <= 62 else det[:59] + "..."
        chk = r["check"] if len(r["check"]) <= w_check else r["check"][: w_check - 3] + "..."
        print("| %3d | %-4s | %-*s | %-*s | %-5s | %-62s |" % (r["n"], r["status"], w_area, r["area"], w_check, chk,
                                                              r["owner"], det))
    print(line)
    c = R.counts()
    verdict = "FAIL" if c["FAIL"] else ("NOT RUN" if info.get("aborted") else "PASS")
    if info.get("aborted"):
        print("NOT RUN: %s" % info["aborted"])
    print("%s  ·  %d PASS  %d WARN  %d FAIL  %d SKIP  ·  %.1f s  ·  %s" % (
        verdict, c["PASS"], c["WARN"], c["FAIL"], c["SKIP"], elapsed, info.get("base")))
    probs = [r for r in rows if r["status"] in ("FAIL", "WARN")]
    if probs:
        print("\nDetails for FAIL/WARN:")
        for r in probs:
            print("  #%d %s [%s] %s: %s" % (r["n"], r["status"], r["owner"], r["check"], r["detail"]))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="BeltGuard end-to-end check (sim mode)")
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--reset", action="store_true", help="POST /api/reset first (start a new shift)")
    ap.add_argument("--tool-seconds", type=float, default=8.0, help="length of the sim tool scene")
    ap.add_argument("--json", default=str(QA / "last_run.json"), help="where to write the full results")
    ap.add_argument("--allow-band", action="store_true",
                    help="run the write steps even when the backend is on the real band (pollutes that shift)")
    ap.add_argument("--db-only", action="store_true",
                    help="only the read-only SQLite audit (safe while the real band is live)")
    ap.add_argument("--db", default=None,
                    help="SQLite file to audit (default data/beltguard.db; use the copy when --base is "
                         "qa/isolated_server.py, whose path is in qa/.isolated_db)")
    args = ap.parse_args()
    global DB_PATH
    if args.db:
        DB_PATH = Path(args.db)
    t0 = time.time()
    if args.db_only:
        R, info = Results(), {"base": "sqlite (read-only)", "started": time.strftime("%Y-%m-%d %H:%M:%S")}
        check_db(R, info)
        print_table(R, info, time.time() - t0)
        if info.get("db_rows"):
            print("rows: " + ", ".join("%s %d" % kv for kv in sorted(info["db_rows"].items())))
        return 1 if R.counts()["FAIL"] else 0
    R, info = asyncio.run(run(args))
    elapsed = time.time() - t0
    print_table(R, info, elapsed)
    c = R.counts()
    verdict = "FAIL" if c["FAIL"] else ("NOT RUN" if info.get("aborted") else "PASS")
    out = {"info": info, "counts": c, "verdict": verdict, "elapsed_s": round(elapsed, 1), "rows": R.rows}
    if not info.get("aborted"):   # keep the last real run's results when the test was skipped
        try:
            Path(args.json).write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        except OSError as e:
            print("could not write %s: %s" % (args.json, e))
    return 1 if c["FAIL"] else (2 if info.get("aborted") else 0)


if __name__ == "__main__":
    sys.exit(main())
