"""BeltGuard backend (S2 band-backend).

FastAPI on 127.0.0.1:8765:  "/" = the dashboard (../frontend),  "/ws" = live WebSocket,
"/api/*" = REST (see CONTRACT.md). SQLite in ../data/beltguard.db.

    python backend/app.py            real band 07 over Bluetooth
    python backend/app.py --sim      the kit's pretend band, every message labelled "sim"

Band callbacks never block: they post into an asyncio queue (events.Hub). One consumer task
does the database writes and broadcasts; OpenAI / Deepgram work runs in threads.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import re
import sys
import threading
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
FRONTEND = ROOT / "frontend"
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))


def load_env(path: Path) -> None:
    """Read KEY=value lines from beltguard/.env into the environment (an existing variable wins).
    Never prints a value."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and not os.environ.get(k):
            os.environ[k] = v


load_env(ROOT / ".env")

import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

import band as bandmod  # noqa: E402
import db as dbmod  # noqa: E402
from events import Hub  # noqa: E402

ACTION, LIMIT = 100.0, 400.0
OPEN_WO = ("draft", "planned", "in_progress")


def log(*parts) -> None:
    print("[app]", *parts, flush=True)


def fmt_minutes(m) -> str:
    if m is None:
        return "?"
    m = float(m)
    if m < 1:
        return "under 1 min"
    h, mm = divmod(int(round(m)), 60)
    return ("%d h %02d min" % (h, mm)) if h else ("%d min" % mm)


def heartbeat(msg: str) -> None:
    """Append a line to the team's shared heartbeat log (ops/heartbeat.log), like every session does."""
    try:
        with open(ROOT / "ops" / "heartbeat.log", "a", encoding="utf-8") as f:
            f.write("%s [S2 band-backend] %s\n" % (time.strftime("%H:%M:%S"), msg))
    except OSError:
        pass
    log(msg)


# ---------------------------------------------------------------------------------------
# S4 / S3 modules, hot-reloaded when their files change; small fallbacks until they exist.
# ---------------------------------------------------------------------------------------
class Modules:
    def __init__(self) -> None:
        self.mods: dict = {}
        self.keys: dict = {}

    def get(self, name: str):
        p = BASE / ("%s.py" % name)
        key = p.stat().st_mtime if p.exists() else None
        if key is None:
            return None
        if self.keys.get(name) != key:
            self.keys[name] = key
            try:
                mod = self.mods.get(name) or sys.modules.get(name)   # after a failed reload: reload, never stale
                self.mods[name] = importlib.reload(mod) if mod else importlib.import_module(name)
                log("loaded %s.py" % name)
            except Exception as e:
                log("%s.py not usable yet: %s: %s" % (name, type(e).__name__, e))
                self.mods.pop(name, None)
        return self.mods.get(name)

    def status(self) -> dict:
        return {n: ("ok" if self.get(n) else "fallback") for n in ("exposure", "inspection", "planner", "intents")}


NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}


def fallback_extract(text: str, stations: list, conveyor: dict, worker: dict, active_station_id=None) -> dict:
    """Used only while inspection.py (S4) is missing or broken."""
    t = text.lower()
    st = None
    m = re.search(r"\b(?:station|stn|s)\s*[-#]?\s*(\d|one|two|three|four|five)\b", t)
    if m:
        v = m.group(1)
        num = int(v) if v.isdigit() else NUMBER_WORDS[v]
        st = next((s for s in stations if s["number"] == num), None)
    if st is None:
        hits = {s["id"] for s in stations for c in (s.get("components") or []) if c in t}
        if len(hits) == 1:
            st = next(s for s in stations if s["id"] in hits)
    if st is None and active_station_id:                 # "idler seized" at the selected station
        st = next((s for s in stations if s["id"] == active_station_id), None)
    if st is None:
        return {"status": "clarify", "source": "rules",
                "question": "Which station on CV-102 is this? Say a station number from 1 to 5."}
    comp = next((c for c in (st.get("components") or []) if c in t), st["name"])
    cond = "seized" if "seiz" in t else "damaged" if "damag" in t else "worn" if "worn" in t else "defect reported"
    return {"status": "ok", "source": "rules", "station_id": st["id"], "component": comp, "condition": cond,
            "severity": "high" if cond == "seized" else "medium",
            "work_order": {"title": "Replace %s at %s" % (comp, st["id"]), "priority": "P2",
                           "priority_reason": "rules fallback", "steps": ["Isolate and verify (authorized person)",
                                                                          "Replace component", "Test run"],
                           "requires_isolation": True, "tool_id": "impact_wrench", "est_trigger_min": 6}}


def fallback_recommend(current: float, planned: dict, tools: list) -> dict:
    tool = next((t for t in tools if t.get("id") == planned.get("tool_id")), None)
    a = float(tool["vibration_m_s2"]) if tool else 0.0
    added = 2 * a * a * float(planned.get("est_trigger_min") or 0) / 60.0
    proj = current + added
    status = "over_limit" if proj >= LIMIT else "concern" if proj >= ACTION - 20 else "ok"
    return {"projected_points": proj, "added_points": added, "status": status, "source": "rules",
            "headline": "Projected %.0f points after this job" % proj,
            "actions": ["Review a lower-vibration method", "Split the task across shifts"],
            "requires_supervisor_review": True}


# ---------------------------------------------------------------------------------------
class BeltGuard:
    def __init__(self, sim: bool) -> None:
        self.sim_mode = sim
        self.store = dbmod.Store()
        self.hub = Hub()
        self.mods = Modules()
        self.tools: list[dict] = []
        self.tools_key = None
        self.load_tools()
        self.store.seed(self.tools)
        self.samples = dbmod.SampleWriter()              # every real reading -> SQLite motion_samples
        self.active_session_id = None
        self.close_orphan_sessions("backend restarted mid-session")
        bandmod.SAMPLE_SINK = self.samples.add
        self.bridge: bandmod.BandBridge | None = None
        self.probes: dict = {}          # "ble"/"usb" -> BandBridge searching for band 07
        self.probe_status: dict = {}
        self._promote_lock = threading.Lock()
        self.search_links: set = {"usb"}  # USB by default; Bluetooth is the dashboard's (browser) link
        self.dev_sim = sim              # --sim: developer pretend band; never the default
        self.ingests: set = set()       # open /ws/ingest sockets (dashboard pages); one of them may be live
        self.rate_pending = False
        self.mic_state = {"on": False, "source": "band", "via": None, "since_ts": None}   # POST /api/mic/state
        self.break_since = None
        self.job_agent = None
        self._job_agent_mod = None
        self.suppress_starts_until = 0.0   # detector starts ignored until then (after a tool return)
        self.awaiting_confirmation_until = 0.0   # a bare yes/no confirms only while we are asking
        self.ready = asyncio.Event()             # set when startup is complete (/api/* waits for it)
        self.ignore_stop = False           # the detector's stop for a session we ended or ignored
        self.active_station = self.store.meta("active_station")
        self.active_tool_id = self.store.meta("active_tool")
        self.recommendation = self.store.meta("recommendation")
        a = self.open_assignment()                       # one rule: an active tool always has an open checkout
        if a:
            self.active_tool_id = a["tool_id"]           # restore the open checkout from SQLite
        elif self.active_tool_id:
            self.active_tool_id = None
            self.store.set_meta("active_tool", None, "system")
        self.connection = {"state": "disconnected", "band": bandmod.BAND, "transport": None, "link": None,
                           "rate": None, "lost_total": 0, "simulated": False,
                           "detail": "No band link yet: press Connect band 07 in the dashboard"}
        self.detector = {"state": "idle", "confidence": 0.0, "features": {}}
        self.active_session_id = None
        self.activity_seg = None
        self.led_key = None
        self.last_transcript = ""
        self._snap_pending = False

    # ---- tools -------------------------------------------------------------------------
    def load_tools(self) -> bool:
        path = BASE / "tool_profiles.json"
        key = path.stat().st_mtime if path.exists() else None
        if key == self.tools_key and self.tools:
            return False
        self.tools_key = key
        tools = []
        if key is not None:
            try:
                ex = self.mods.get("exposure")
                if ex and hasattr(ex, "load_tool_profiles"):
                    tools = ex.load_tool_profiles()
                else:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    tools = raw.get("tools", []) if isinstance(raw, dict) else raw
            except Exception as e:
                log("tool_profiles.json not usable yet:", e)
        if not tools:
            tools = [{"id": "impact_wrench", "name": "Impact wrench 1/2\" (PLACEHOLDER)", "vibration_m_s2": 5.0,
                      "source": "PLACEHOLDER until S4 tool_profiles.json loads"}]
        self.tools = [t for t in tools if isinstance(t, dict) and t.get("id")]
        log("tool profiles:", ", ".join(t["id"] for t in self.tools))
        return True

    def tool(self, tool_id):
        return next((t for t in self.tools if t.get("id") == tool_id), None) if tool_id else None

    async def watch_tools(self) -> None:
        while True:
            await asyncio.sleep(3)
            try:
                if self.load_tools():
                    self.store.sync_tools(self.tools)
                    self.changed()
            except Exception as e:
                log("tool reload failed:", e)

    # ---- sources -----------------------------------------------------------------------
    # One active source at a time (self.bridge): the dashboard's browser link (IngestBridge), a
    # Python link over USB or Bluetooth (BandBridge), or the pretend band with --sim (dev only).
    # With no source the connection is "disconnected": nothing simulated is ever shown by default.
    def start_bridge(self, sim: bool) -> None:
        self.sim_mode = sim
        self.bridge = bandmod.BandBridge(self.hub.post, sim=sim)
        self.bridge.start()

    def live_link(self):
        b = self.bridge
        return b if (b is not None and not b.sim and b.band is not None) else None

    def start_search(self) -> list:
        """Look for band 07 on the links in self.search_links (USB by default; Bluetooth only when asked,
        because the dashboard's browser link uses Bluetooth). The first link to connect takes over."""
        if self.live_link() is not None:
            return []
        started = []
        for key in sorted(self.search_links):
            if key == "ble" and self.ingests:            # a Band link page owns Bluetooth: Chrome and bleak share
                continue                                 # the Windows stack, so Python stays off it meanwhile
            if key not in self.probes:
                p = bandmod.BandBridge(self.hub.post, sim=False, on_ready=self._promote,
                                       transport="serial" if key == "usb" else "ble")
                self.probes[key] = p
                p.start()
                started.append(key)
        return started

    async def stop_search(self) -> None:
        probes, self.probes, self.probe_status = list(self.probes.values()), {}, {}
        for p in probes:
            await asyncio.to_thread(p.stop)

    def close_orphan_sessions(self, why: str) -> list:
        """No tool session may stay "active" without a live owner (a restart or a shift reset leaves one
        behind otherwise, and the dashboard would count it as tool use for ever). Leftovers become
        "unresolved": the technician confirms or rejects them, like any ambiguous session."""
        closed = []
        for r in self.store.query("SELECT id FROM tool_sessions WHERE status = 'active'"):
            if r["id"] != self.active_session_id:
                self.store.update("tool_sessions", r["id"], status="unresolved")
                self.store.insert("shift_events", "system", lane="tool", kind="tool_unresolved",
                                  title="Tool activity %s left open: confirm or reject" % r["id"],
                                  detail=why, refs={"tool_session_id": r["id"]})
                closed.append(r["id"])
        if closed:
            log("closed orphaned tool sessions (%s): %s" % (why, ", ".join(closed)))
        return closed

    def _drop_session(self) -> None:
        if self.active_session_id:                       # a session never continues on another source
            self.store.update("tool_sessions", self.active_session_id, status="unresolved")
            self.active_session_id = None

    async def set_source(self, mode: str) -> dict:
        """mode "band": search USB + Bluetooth from Python · "usb": USB only · "sim": pretend band (dev) ·
        "off": no source."""
        if mode == "sim":
            self.search_links = set()
            await self.stop_search()
            old, self.bridge = self.bridge, None
            if old is not None:
                await asyncio.to_thread(old.stop)
            self._drop_session()
            self.led_key = None
            self.start_bridge(True)
        elif mode in ("band", "usb"):
            self.search_links = {"usb", "ble"} if mode == "band" else {"usb"}
            if self.bridge is not None and self.bridge.sim:
                pass                                     # dev: the sim stays until a real link takes over
            started = self.start_search()
            if started:
                await self.event("system", "band_search", "Searching for band 07 (%s)" % " + ".join(
                    "Bluetooth" if k == "ble" else "USB" for k in started), source="system")
        elif mode == "off":
            self.search_links = set()
            await self.stop_search()
            old, self.bridge = self.bridge, None
            if old is not None:
                await asyncio.to_thread(old.stop)
            self._drop_session()
            self.connection = {"state": "disconnected", "band": bandmod.BAND, "transport": None, "link": None,
                               "rate": None, "lost_total": 0, "simulated": False,
                               "detail": "No band link: press Connect band 07 in the Band link tab"}
        return {"mode": mode, "searching": sorted(self.probes),
                "source": self.bridge.link if self.bridge is not None else None}

    def _promote(self, new) -> bool:
        """Runs on a probe's thread the moment band 07 is connected over a Python link.
        Returns False when another live link already owns the band (that probe then lets go)."""
        with self._promote_lock:
            cur = self.bridge
            if cur is not None and not cur.sim and cur is not new and cur.band is not None:
                return False
            others = [p for p in self.probes.values() if p is not new]
            self.probes, self.probe_status = {}, {}
            old, self.bridge = cur, new
            self.sim_mode = False
            self.led_key = None
            self._drop_session()
        for p in others:
            p.stop()
        if old is not None and old is not new:
            old.stop()
        self.hub.post({"kind": "promoted", "source": "band", "link": new.link})
        heartbeat("BAND LIVE (%s) · real band 07 samples now drive the dashboard" % new.link)
        return True

    async def on_link_lost(self, why: str) -> None:
        """A live Python link was lost for good (the library's own reconnect gave up)."""
        await self.event("system", "band_lost", "Band 07 link lost", detail=why or "", source="system")
        heartbeat("band 07 link lost (%s) · disconnected, searching again" % (why or "?"))
        old, self.bridge = self.bridge, None
        if old is not None:
            await asyncio.to_thread(old.stop)
        self._drop_session()
        if self.dev_sim:
            self.start_bridge(True)
        self.start_search()

    # ---- the dashboard's browser link (/ws/ingest) ---------------------------------------
    async def ingest_attach(self) -> "bandmod.IngestBridge":
        """A page opened /ws/ingest. Nothing changes until it says "connected": opening a socket
        never takes the band away from a link that is already live."""
        ing = bandmod.IngestBridge(self.hub.post, asyncio.get_running_loop())
        self.ingests.add(ing)
        probe = self.probes.pop("ble", None)             # pause the Python Bluetooth search while the page is open
        if probe is not None:
            self.probe_status.pop("ble", None)
            await asyncio.to_thread(probe.stop)
            log("Python Bluetooth search paused: a Band link page opened /ws/ingest")
        return ing

    def real_link_live(self) -> bool:
        b = self.live_link()
        return b is not None and not getattr(b, "test", False)

    async def ingest_connection(self, ing, msg: dict) -> None:
        state = msg.get("state")
        if state == "connected":
            if self.bridge is not ing:
                if (msg.get("test") or ing.test) and self.real_link_live():
                    raise ValueError("a real band 07 link is live: test ingest refused")
                await self.stop_search()                 # the band takes one link: stop Python searching
                old, self.bridge = self.bridge, None
                if old is not None:
                    if isinstance(old, bandmod.IngestBridge):
                        old.mark_disconnected("replaced by a newer dashboard link")   # its socket may stay open
                    else:
                        await asyncio.to_thread(old.stop)
                self._drop_session()
                self.led_key = None
                self.bridge = ing
            resumed = self.bridge is ing and ing.state == "reconnecting"
            ing.mark_connected(msg)
            if resumed:                                  # the page's library got the band back
                await self.event("system", "band_live", "Band 07 link back", source=ing.src)
                return
            if not ing.test:
                heartbeat("BAND LIVE (browser-ble) · band 07 streams from the dashboard (Chrome Web Bluetooth) via /ws/ingest")
            await self.event("system", "band_live", "Band 07 connected from the dashboard (Chrome Web Bluetooth)",
                             source=ing.src)
        elif state == "disconnected":
            was_live = self.bridge is ing and ing.band is not None
            reason = msg.get("reason")
            if was_live and reason and reason != "closed":
                # the band link dropped and the page's library is reconnecting: show that, keep the link
                ing.mark_reconnecting("Band link dropped (%s): the dashboard is reconnecting" % reason)
                return
            ing.mark_disconnected(msg.get("detail") or "browser link closed")
            if was_live:
                self._drop_session()
                await self.event("system", "band_lost", "Browser link to band 07 closed", source=ing.src)
                self.start_search()

    async def ingest_detach(self, ing) -> None:
        was_live = self.bridge is ing and ing.band is not None
        self.ingests.discard(ing)
        if not self.ingests and not was_live and self.live_link() is None:
            self.start_search()                          # last page gone: Python may search Bluetooth again
        await asyncio.to_thread(ing.stop, was_live)      # quiet when live: the grace period reports instead
        if self.bridge is not ing:
            return
        if was_live:
            # A socket blip (dashboard reload, bridge tab hiccup): say "reconnecting" for up to 10 s and
            # only then "disconnected", unless a new dashboard link takes over meanwhile.
            ing.state, ing.detail = "reconnecting", "Dashboard link dropped: waiting up to 10 s for it to come back"
            self.hub.post({"kind": "connection", "source": ing.src, **ing.status()})
            asyncio.create_task(self._ingest_grace(ing, 10.0))
            return
        self.bridge = None
        self._drop_session()
        self.start_search()

    async def _ingest_grace(self, ing, seconds: float) -> None:
        await asyncio.sleep(seconds)
        if self.bridge is not ing:                       # a new dashboard link took over meanwhile
            return
        self.bridge = None
        self._drop_session()
        ing.state, ing.detail = "disconnected", "Dashboard link closed: press Connect band 07"
        self.hub.post({"kind": "connection", "source": ing.src, **ing.status()})
        await self.event("system", "band_lost", "Browser link to band 07 closed", source=ing.src)
        self.start_search()

    def desired_rate(self):
        """BAND_FACTS: 416 Hz both = 0 lost over Bluetooth; with audio on, both sensors must stay at 208.
        The dashboard's browser link is pinned at 416 (no detector swaps or n restarts mid-demo), and
        drops to 208 only while the page reports its mic is on. Python links: 416 for tool work, else 50."""
        b = self.bridge
        mic = bool(b and b.mic_on)
        if isinstance(b, bandmod.IngestBridge):
            return (208, "both") if mic else (416, "both")
        if b is not None and not b.sim:                  # Python link: 416 both, audio included (BAND_FACTS)
            return (208, "both") if b.audio_demoted else (416, "both")
        if self.active_tool_id:
            return (208, "both") if mic else (416, "both")
        return (50, "both")

    async def apply_rate(self) -> None:
        b = self.bridge
        if not b or b.band is None:
            return
        if self.active_session_id:                       # never reset the detector mid tool session
            self.rate_pending = True
            return
        self.rate_pending = False
        want = self.desired_rate()
        if (b.rate, b.sensors) == want:
            return
        browser = isinstance(b, bandmod.IngestBridge)
        if browser and b._requested == want:             # already asked the page; it reports the new rate
            return
        try:
            await asyncio.to_thread(b.set_rate, *want)
            await self.event("system", "rate", ("Asked the dashboard link for %d Hz %s" if browser else "Motion %d Hz %s")
                             % want, detail="tool selected" if self.active_tool_id else "default rate", source="system")
        except Exception as e:
            log("rate change to %s failed: %s" % (want, e))

    # ---- state helpers -----------------------------------------------------------------
    def shift_start(self) -> float:
        return float(self.store.meta("shift_start", 0) or 0)

    def exposure_summary(self, records: list[dict]) -> dict:
        ex = self.mods.get("exposure")
        summary = None
        if ex:
            try:
                summary = ex.daily_summary(records)
            except Exception as e:
                log("exposure.daily_summary failed:", e)
        if not summary:
            total = sum(float(r.get("points") or 0) for r in records)
            summary = {"total_points": total,
                       "status": "above_limit" if total >= LIMIT else "above_action" if total >= ACTION else "below_action"}
        out = {k: v for k, v in summary.items() if k != "records"}
        out.update({"total_points": float(summary.get("total_points") or 0.0), "action": ACTION, "limit": LIMIT,
                    "records": records})
        out.setdefault("label", "SCREENING ESTIMATE — not a certified HAV measurement")
        return out

    def open_work_order(self, station_id):
        ss = self.shift_start()
        rows = self.store.query("SELECT * FROM work_orders WHERE ts >= ? AND status IN ('draft','planned','in_progress') "
                                "ORDER BY ts DESC", (ss,))
        for r in rows:
            if station_id and r.get("station_id") == station_id:
                return r
        return rows[0] if rows else None

    def snapshot(self) -> dict:
        ss = self.shift_start()
        stations = self.store.stations()
        defects = self.store.since("defects", ss)
        wos = self.store.since("work_orders", ss)
        sessions = self.store.since("tool_sessions", ss)
        records = self.store.since("exposure_records", ss)
        timeline = self.store.since("shift_events", ss, limit=200)
        # Keep the shift's key events even after many walking/tool events have scrolled past.
        pinned = self.store.query("SELECT * FROM shift_events WHERE ts >= ? AND kind IN ('defect','wo_draft','wo_planned',"
                                  "'wo_in_progress','threshold','projection','clarify','shift_start') "
                                  "ORDER BY ts DESC LIMIT 60", (ss,))
        if pinned:
            seen = {e["id"] for e in timeline}
            timeline = sorted(timeline + [e for e in pinned if e["id"] not in seen], key=lambda e: e["ts"])
        defect_st = {d["station_id"] for d in defects}
        visited = {r["station_id"] for r in self.store.query(
            "SELECT DISTINCT station_id FROM inspection_events WHERE ts >= ?", (ss,))}
        for s in stations:
            s["status"] = ("active" if s["id"] == self.active_station else "defect" if s["id"] in defect_st
                           else "inspected" if s["id"] in visited else "ok")
        assignment, verdict = self.assignment_view(records)
        conn = dict(self.connection)
        if self.bridge:                                  # live values straight from the bridge
            live = self.bridge.status()
            conn.update({k: live.get(k) for k in ("state", "detail", "link", "rate", "sensors", "lost_total", "battery",
                                                   "rssi", "mic", "label", "led", "detector", "raw_file", "simulated",
                                                   "limits")})
        conn["band"] = bandmod.BAND
        if self.probes:                                  # searching for the real band (sim on screen)
            links = {k: v for k, v in self.probe_status.items() if k in self.probes}
            conn["probe"] = {"state": "connecting", "links": links,
                             "detail": " · ".join("%s: %s" % ("Bluetooth" if k == "ble" else "USB", v.get("detail"))
                                                  for k, v in links.items())}
        else:
            conn["probe"] = None
        mic_on = bool(self.bridge and self.bridge.mic_on)
        return {
            "worker": self.store.worker(),
            "conveyor": self.store.conveyor(),
            "stations": stations,
            "active_station": self.active_station,
            "tools": self.tools,
            "active_tool": self.tool(self.active_tool_id),
            "defects": defects,
            "work_orders": wos,
            "tool_sessions": sessions,
            "exposure": self.exposure_summary(records),
            "assignment": assignment,
            "break_since": self.break_since,
            "job": self._job_view((self.job_agent.snapshot() if self.job_agent else {}).get("job")),
            "agent_actions": (self.job_agent.snapshot() if self.job_agent else {}).get("agent_actions") or [],
            "verdict": verdict,
            "recommendation": self.recommendation,
            "timeline": timeline,
            "connection": conn,
            "mic": {"on": bool(mic_on or (self.mic_state["on"] and self.live_link() is not None)),
                    "source": "band", "via": self.mic_state.get("via"), "since_ts": self.mic_state.get("since_ts")},
            "detector": {"state": self.detector.get("state"), "confidence": self.detector.get("confidence")},
            "shift_start": ss,
            "modules": self.mods.status(),
            "detector_impl": self.bridge.detector.kind if self.bridge else None,
            "last_transcript": self.last_transcript,
        }

    def changed(self) -> None:
        if self._snap_pending:
            return
        self._snap_pending = True
        asyncio.get_running_loop().call_later(0.1, lambda: asyncio.ensure_future(self._send_snapshot()))

    async def _send_snapshot(self) -> None:
        self._snap_pending = False
        await self.hub.broadcast({"type": "snapshot", "source": "system", "state": self.snapshot()})

    async def event(self, lane: str, kind: str, title: str, detail: str = "", station_id=None, refs=None,
                    source: str = "system", ts: float | None = None) -> dict:
        extra = {"ts": ts} if ts else {}
        row = self.store.insert("shift_events", source, lane=lane, kind=kind, title=title, detail=detail,
                                station_id=station_id, refs=refs or {}, **extra)
        await self.hub.broadcast({"type": "event", "source": source, "event": row})
        self.changed()
        return row

    async def update_led(self) -> None:
        if not self.bridge or self.mic_state.get("on"):   # the band refuses LED commands while its mic is on
            return
        records = self.store.since("exposure_records", self.shift_start())
        status = self.exposure_summary(records).get("status")
        if self.active_session_id:
            want = ("#ffb000", "blink", 400, 1.0)
        elif status == "above_limit":
            want = ("#ff1a1a", "blink", 250, 1.0)
        elif status == "above_action":
            want = ("#ff1a1a", "breathe", 1500, 1.0)
        else:
            want = ("#00ff66", "solid", 1000, 0.25)
        if want != self.led_key:
            self.led_key = want
            await asyncio.to_thread(self.bridge.set_led, *want)

    # ---- the consumer ------------------------------------------------------------------
    async def consume(self) -> None:
        while True:
            item = await self.hub.get()
            try:
                await self.handle(item)
            except Exception:
                log("handler error:\n" + traceback.format_exc())

    async def handle(self, item: dict) -> None:
        kind = item.pop("kind", None)
        src = item.get("source", "system")
        uid = item.pop("uid", None)
        if kind in ("motion", "activity", "detector", "connection", "transcript", "utterance", "band_gesture",
                    "notice") and uid is not None:
            live_uid = self.bridge.uid if self.bridge is not None else None
            if uid != live_uid and not (kind == "connection" and live_uid is None):
                return                                   # an old, displaced or test source: never on screen
        if kind == "motion":
            await self.hub.broadcast({"type": "motion", **item})
        elif kind == "activity":
            self.detector = {"state": item.get("state"), "confidence": item.get("confidence"),
                             "features": item.get("features") or {}}
            await self.hub.broadcast({"type": "activity", **item})
            await self.track_activity(item.get("state"), src)
        elif kind == "detector":
            await self.on_detector(item.get("event") or {}, src)
        elif kind == "connection":                       # (only the live source's status gets here: uid check above)
            self.connection = {k: item.get(k) for k in ("state", "band", "link", "transport", "rate", "sensors",
                                                         "lost_total", "battery", "rssi", "mic", "label", "led",
                                                         "detail", "simulated", "limits", "detector", "raw_file")}
            await self.hub.broadcast({"type": "connection", **item})
            if item.get("state") in ("connected", "simulated"):
                self.led_key = None
                await self.update_led()
                await self.apply_rate()
                b = self.bridge
                if (b is not None and not isinstance(b, bandmod.IngestBridge) and not b.voice_wanted
                        and (not b.sim or os.environ.get("BELTGUARD_SIM_VOICE"))):
                    asyncio.create_task(self.start_python_voice())
            elif (item.get("state") in ("disconnected", "error") and src == "band"
                  and item.get("link") in ("ble", "usb") and item.get("detail") != "stopped"
                  and not str(item.get("detail") or "").endswith("closed")
                  and self.bridge is not None and self.bridge.link == item.get("link")):
                asyncio.create_task(self.on_link_lost(item.get("detail") or ""))
            self.changed()
        elif kind == "band_probe":
            link = item.get("link")
            if link not in self.probes:                  # a probe that was already stopped
                return
            self.probe_status[link] = {k: item.get(k) for k in ("state", "detail")}
            await self.hub.broadcast({"type": "band_probe", **item})
            if item.get("state") == "gave-up":
                self.probes.pop(link, None)
                await self.event("system", "band_search_failed",
                                 "%s search stopped: staying on SIMULATED data" % ("USB" if link == "usb" else "Bluetooth"),
                                 detail=item.get("detail") or "", source="system")
            self.changed()
        elif kind == "promoted":
            via = "USB cable" if item.get("link") == "usb" else "Bluetooth"
            await self.event("system", "band_live", "Band 07 connected over %s: live wrist data" % via, source="band")
        elif kind == "transcript":
            b = self.bridge
            if item.get("final") and b is not None and getattr(b, "voice_wanted", False):
                text = (item.get("text") or "").strip()
                if text:                                 # one complete sentence: record it and act on it
                    asyncio.create_task(self.report(text[:2000], "band", "voice"))
                return
            if item.get("final"):
                self.last_transcript = item.get("text", "")
            await self.hub.broadcast({"type": "transcript", **item})
        elif kind == "utterance":
            if self.bridge is not None and getattr(self.bridge, "voice_wanted", False):
                return                                   # always-on voice already reported each sentence
            text = (item.get("text") or "").strip()
            if text:
                asyncio.create_task(self.report(text, src, "voice"))
            else:
                await self.event("inspection", "no_speech", "Voice report: nothing heard", source=src)
        elif kind == "band_gesture":
            asyncio.create_task(self.band_event(item.get("gesture") or "double-tap", src))
        elif kind == "notice":
            await self.event("system", "band_notice", "Band notice: %s" % item.get("notice"),
                             detail=str(item.get("message") or item.get("arg") or ""), source=src)
        elif kind == "sim_scene":
            await self.hub.broadcast({"type": "sim_scene", **item})

    async def track_activity(self, state, src) -> None:
        now = time.time()
        if self.activity_seg is None:
            self.activity_seg = (state, now)
            return
        prev_state, since = self.activity_seg
        if state == prev_state:
            return
        self.activity_seg = (state, now)
        dur = now - since
        if prev_state == "walking" and dur >= 3:
            await self.event("walking", "walk", "Walked %.0f s" % dur, station_id=self.active_station,
                             refs={"start": since, "end": now}, source=src)
        elif prev_state == "gesture" and dur >= 0.5:
            pass  # gestures are shown live, not logged

    # ---- detector events -> tool sessions -> exposure ----------------------------------
    async def on_detector(self, ev: dict, src: str) -> None:
        k = ev.get("kind")
        if k == "tool_start":
            if time.time() < self.suppress_starts_until:   # the return gesture itself must not start a session
                self.ignore_stop = True
                return
            self.ignore_stop = False
            if self.active_session_id:
                return
            wo = self.open_work_order(self.active_station)
            tool = self.tool(self.active_tool_id)
            row = self.store.insert("tool_sessions", src, worker_id=dbmod.WORKER["id"], conveyor_id=dbmod.CONVEYOR["id"],
                                    station_id=self.active_station, work_order_id=wo["id"] if wo else None,
                                    tool_id=self.active_tool_id, t_start=ev.get("t"),
                                    wall_start=ev.get("wall") or time.time(), confidence=ev.get("confidence"),
                                    status="active")
            self.active_session_id = row["id"]
            asyncio.create_task(self.job_tool(row))
            await self.event("tool", "tool_start", "Tool activity detected%s" % (" · %s" % tool["name"] if tool else ""),
                             detail="Wrist accel+gyro, confidence %.2f. Tool identity: %s." % (
                                 float(ev.get("confidence") or 0),
                                 "selected by technician" if tool else "NOT SELECTED, select the tool to confirm"),
                             station_id=self.active_station, refs={"tool_session_id": row["id"]}, source=src)
            if wo and wo["status"] in ("draft", "planned"):
                self.store.update("work_orders", wo["id"], status="in_progress")
                await self.event("work_order", "wo_in_progress", "%s in progress" % wo["id"],
                                 detail=wo.get("title") or "", station_id=wo.get("station_id"),
                                 refs={"work_order_id": wo["id"]}, source=src)
            await self.update_led()
            self.changed()
        elif k == "tool_stop":
            if self.ignore_stop and not self.active_session_id:   # stop of a session we ended/ignored: drop it
                self.ignore_stop = False
                return
            sid, self.active_session_id = self.active_session_id, None
            t_start, t_end = ev.get("t_start"), ev.get("t_end")
            trig = float(ev.get("trigger_s") or ((t_end or 0) - (t_start or 0)) or 0)
            if not sid:
                wo = self.open_work_order(self.active_station)
                sid = self.store.insert("tool_sessions", src, worker_id=dbmod.WORKER["id"],
                                        conveyor_id=dbmod.CONVEYOR["id"], station_id=self.active_station,
                                        work_order_id=wo["id"] if wo else None, tool_id=self.active_tool_id,
                                        t_start=t_start, wall_start=ev.get("wall_start"), status="active")["id"]
            row = self.store.get("tool_sessions", sid)
            tool = self.tool(row.get("tool_id") or self.active_tool_id)
            status = ev.get("status") or "unresolved"
            why = ""
            if status == "confirmed" and tool is None:
                status, why = "unresolved", "no tool selected"
            row = self.store.update("tool_sessions", sid, t_end=t_end, wall_end=ev.get("wall_end") or time.time(),
                                    trigger_s=trig, confidence=ev.get("confidence"), status=status,
                                    tool_id=tool["id"] if tool else None)
            if status == "confirmed":
                await self.event("tool", "tool_stop", "Tool use %.0f s · %s" % (trig, tool["name"]),
                                 detail="Trigger time from band timestamps; confidence %.2f" % float(ev.get("confidence") or 0),
                                 station_id=row.get("station_id"), refs={"tool_session_id": sid}, source=src)
                await self.add_exposure(row, tool, trig, src)
            else:
                await self.event("tool", "tool_unresolved", "Unresolved tool activity %.0f s: confirm or reject" % trig,
                                 detail=why or "Detector evidence ambiguous (confidence %.2f)" % float(ev.get("confidence") or 0),
                                 station_id=row.get("station_id"), refs={"tool_session_id": sid}, source=src)
                await self.ask_confirmation(row, tool, trig)
            await self.update_led()
            if self.rate_pending:                        # a rate change waited for this session to close
                await self.apply_rate()
            asyncio.create_task(self.job_tool(self.store.get("tool_sessions", sid)))
            self.changed()

    async def add_exposure(self, session: dict, tool: dict, trigger_s: float, source: str) -> dict:
        ex = self.mods.get("exposure")
        a = float(tool["vibration_m_s2"])
        hours = max(0.0, trigger_s) / 3600.0
        try:
            pts = float(ex.points(a, hours)) if ex else 2 * a * a * hours
        except Exception as e:
            log("exposure.points failed:", e)
            pts = 2 * a * a * hours
        ss = self.shift_start()
        before = self.exposure_summary(self.store.since("exposure_records", ss))["total_points"]
        rec = self.store.insert("exposure_records", source, worker_id=dbmod.WORKER["id"], tool_session_id=session["id"],
                                tool_id=tool["id"], vibration_m_s2=a, vib_source=tool.get("source"),
                                trigger_s=trigger_s, points=pts, status="confirmed",
                                method="SCREENING ESTIMATE: 2 x a^2 x T, a from tool profile, T from wrist-detected trigger time")
        self.store.update("tool_sessions", session["id"], points=pts)
        after = self.exposure_summary(self.store.since("exposure_records", ss))["total_points"]
        await self.event("exposure", "exposure", "+%.1f pts · day %.1f / 100" % (pts, after),
                         detail="%s %.1f m/s² × %.0f s trigger (screening estimate)" % (tool["name"], a, trigger_s),
                         station_id=session.get("station_id"), refs={"exposure_record_id": rec["id"],
                                                                     "tool_session_id": session["id"]}, source=source)
        asyncio.create_task(self.job_state())
        for mark, name in ((ACTION, "Exposure action value (100 pts) reached"), (LIMIT, "Exposure limit value (400 pts) reached")):
            if before < mark <= after:
                await self.event("exposure", "threshold", name, detail="Screening estimate. Supervisor review required.",
                                 station_id=session.get("station_id"), source="system")
                self.send_band_alert("%d points" % mark)
                await self.assistant(name + ". Supervisor review required (screening estimate).", "threshold", "system")
        return rec

    # ---- inspection report -> defect + work order ---------------------------------------
    async def report(self, text: str, source: str, input_kind: str = "typed") -> dict:
        """Record and act on one sentence (intents, defect extraction), then let S4's job agent tick off the
        job in the background, then re-broadcast the final transcript with what was done (`acted`)."""
        res = await self._report_core(text, source, input_kind)
        res = res if isinstance(res, dict) else {}
        changes = await self.job_transcript(text, res)
        said = (changes or {}).get("say")
        steps = (changes or {}).get("steps") or []
        reply = res.get("response_text")
        if said and (steps or not reply or str(reply).startswith("Sorry, I didn't catch")):
            if not reply or str(reply).startswith("Sorry, I didn't catch"):
                await self.assistant(said, "job_agent", "rules", refs={"transcript_id": res.get("transcript_id")})
                res["response_text"] = said
        acted = self._acted(res, changes)
        tid = res.get("transcript_id")
        if tid:
            fields = {"acted": acted}
            if res.get("response_text") and res.get("response_text") != reply:
                fields["response"] = res["response_text"]
            self.store.update("transcripts", tid, **fields)
        await self.hub.broadcast({"type": "transcript", "source": source, "input": input_kind, "text": text,
                                  "final": True, "pending": False, "acted": acted, "transcript_id": tid,
                                  "reply": res.get("response_text")})
        res["acted"] = acted
        return res

    def _acted(self, res: dict, changes) -> str | None:
        """A short line saying what BeltGuard did with a sentence (None = nothing: chat)."""
        bits = []
        st = res.get("status")
        if res.get("intent") and res.get("intent") not in ("none", "report_defect", "chat") and st == "ok":
            bits.append(str(res.get("response_text") or res.get("intent"))[:90])
        elif st == "ok" and res.get("defect"):
            d, w = res.get("defect") or {}, res.get("work_order") or {}
            bits.append("Defect %s at %s, %s drafted" % (d.get("id"), d.get("station_id"), w.get("id")))
        elif st == "clarify" and res.get("question"):
            bits.append("Asked: " + str(res["question"])[:80])
        if changes:
            job = self.job_agent.snapshot().get("job") if self.job_agent else None
            titles = {x.get("id"): x.get("title") for x in ((job or {}).get("steps") or [])}
            for sid in changes.get("steps") or []:
                bits.append("Step: %s" % titles.get(sid, sid))
            for a in changes.get("actions") or []:
                if a.get("kind") in ("work_order", "parts_request", "job_done", "isolation"):
                    bits.append(str(a.get("text"))[:80])
        return " · ".join(bits) if bits else None

    async def _report_core(self, text: str, source: str, input_kind: str = "typed") -> dict:
        """Every utterance (band mic via the page, the Python mic, or typed) is recorded in `transcripts`
        and acted on: a defect + work order, a question back, or nothing for chit-chat. `source` is who
        said it (band-mic / band / user) and goes on every row; `extracted_by` (ai / rules) says what
        turned the words into a record."""
        heard = time.time()
        stations = self.store.stations()
        conveyor, worker = self.store.conveyor(), self.store.worker()
        self.last_transcript = text
        active = self.active_station
        tr = self.store.insert("transcripts", source, ts=heard, text=text, result_status="pending", station_id=active,
                               input=input_kind)
        await self.hub.broadcast({"type": "transcript", "source": source, "input": input_kind, "text": text,
                                  "final": True, "pending": True, "acted": None, "transcript_id": tr["id"]})
        short = text[:90] + ("…" if len(text) > 90 else "")
        title = "%s: “%s”" % ("Heard" if input_kind == "voice" else "Typed", short)
        im = self.mods.get("intents")                    # S4: spoken commands that DO things
        parsed = None
        strict = "band" if input_kind == "voice" else "typed"   # S4: band mic is strict, typed is lenient
        if im is not None and hasattr(im, "parse_intent"):
            ctx = self.intent_context()
            try:
                try:
                    parsed = await asyncio.to_thread(im.parse_intent, text, ctx, source=strict)
                except TypeError:
                    parsed = await asyncio.to_thread(im.parse_intent, text, ctx)
            except Exception as e:
                log("parse_intent failed:", repr(e))
            parsed = parsed if isinstance(parsed, dict) else None
        name = ((parsed or {}).get("intent") or (parsed or {}).get("name")) or None
        if parsed and parsed.get("ignore"):              # band chat: recorded, nothing extracted, nothing said
            self.store.update("transcripts", tr["id"], result_status="ignored", intent=name or "chat",
                              extracted_by=parsed.get("source") if parsed.get("source") in ("ai", "rules") else "rules")
            await self.event("inspection", "transcript", title, detail="Chat: no action", station_id=active,
                             refs={"transcript_id": tr["id"]}, source=source, ts=heard)
            return {"status": "ignored", "intent": name or "chat", "response_text": None, "defect": None,
                    "work_order": None, "question": None, "transcript_id": tr["id"]}
        if parsed and name not in (None, "none", "unknown", "defect_report", "report_defect"):
            return await self.run_intent(parsed, name, source, tr, heard, title)
        # S4 wake word: extract from the words after "BeltGuard"; lenient when addressed or typed, strict otherwise
        extract_text = ((parsed or {}).get("text") or text).strip() or text
        extract_source = (parsed or {}).get("extract_source") or strict
        res = await self._report_extract(extract_text, source, tr, heard, title, active, stations, conveyor,
                                         worker, extract_source)
        res.setdefault("intent", name)
        res.setdefault("response_text", None)
        if parsed is not None and hasattr(im, "respond"):   # S4 voices the outcome ("" = say nothing)
            try:
                say = await asyncio.to_thread(im.respond, parsed, res, self.intent_context())
            except Exception as e:
                log("intents.respond failed:", repr(e))
                say = None
            self.store.update("transcripts", tr["id"], intent=name or "none", response=say or None)
            if say:
                res["response_text"] = say
                await self.assistant(say, name or "report_defect", "rules", refs={"transcript_id": tr["id"]})
        return res

    async def _report_extract(self, text, source, tr, heard, title, active, stations, conveyor, worker,
                              strict: str = "typed") -> dict:
        """Defect extraction for one recorded utterance: defect + work order, a question back, or nothing."""
        ins = self.mods.get("inspection")
        try:
            if ins:
                try:
                    res = await asyncio.to_thread(ins.extract_defect, text, stations, conveyor, worker,
                                                  active_station_id=active, source=strict)
                except TypeError:                         # older signatures
                    try:
                        res = await asyncio.to_thread(ins.extract_defect, text, stations, conveyor, worker,
                                                      active_station_id=active)
                    except TypeError:
                        res = await asyncio.to_thread(ins.extract_defect, text, stations, conveyor, worker)
            else:
                res = fallback_extract(text, stations, conveyor, worker, active)
        except Exception as e:
            log("extract_defect failed, using rules:", repr(e))
            res = fallback_extract(text, stations, conveyor, worker, active)
        res = res or {}
        esrc = res.get("source") or "ai"
        if res.get("status") == "ignored":               # chit-chat: recorded, one quiet timeline line, no action
            q = res.get("question") or res.get("reason")
            self.store.update("transcripts", tr["id"], result_status="ignored", question=q, extracted_by=esrc)
            await self.event("inspection", "transcript", title, detail="Not a defect report: no action",
                             station_id=active, refs={"transcript_id": tr["id"]}, source=source, ts=heard)
            return {"status": "ignored", "defect": None, "work_order": None, "question": q, "transcript_id": tr["id"]}
        ids = {s["id"] for s in stations}
        if res.get("status") != "ok" or res.get("station_id") not in ids:
            q = res.get("question") or "Which station on CV-102 is this? Say a station number from 1 to 5."
            self.store.update("transcripts", tr["id"], result_status="clarify", question=q, extracted_by=esrc)
            await self.event("inspection", "transcript", title, detail="Question back: " + q, station_id=active,
                             refs={"transcript_id": tr["id"]}, source=source, ts=heard)
            await self.hub.broadcast({"type": "clarify", "source": esrc, "question": q, "transcript_id": tr["id"]})
            await self.event("inspection", "clarify", "Clarification needed", detail=q, source=esrc)
            return {"status": "clarify", "defect": None, "work_order": None, "question": q, "transcript_id": tr["id"]}
        st = res["station_id"]
        ie = self.store.insert("inspection_events", source, worker_id=worker["id"], conveyor_id=conveyor["id"],
                               station_id=st, kind="defect_report", note=text)
        d = self.store.insert("defects", source, inspection_event_id=ie["id"], worker_id=worker["id"],
                              conveyor_id=conveyor["id"], station_id=st, component=res.get("component"),
                              condition=res.get("condition"), severity=res.get("severity"), transcript=text,
                              extracted_by=esrc)
        w = res.get("work_order") or {}
        wo = self.store.insert("work_orders", source, defect_id=d["id"], conveyor_id=conveyor["id"], station_id=st,
                               title=w.get("title") or "Repair %s" % (res.get("component") or st),
                               priority=w.get("priority"), priority_reason=w.get("priority_reason"), status="draft",
                               steps=w.get("steps") or [], requires_isolation=True, tool_id=w.get("tool_id"),
                               est_trigger_min=w.get("est_trigger_min"), data=w, extracted_by=esrc)
        self.store.update("transcripts", tr["id"], result_status="ok", defect_id=d["id"], work_order_id=wo["id"],
                          extracted_by=esrc, station_id=st)
        await self.event("inspection", "transcript", title,
                         detail="Defect %s logged at %s · %s drafted" % (d["id"], st, wo["id"]), station_id=st,
                         refs={"transcript_id": tr["id"], "defect_id": d["id"], "work_order_id": wo["id"]},
                         source=source, ts=heard)
        stname = next(s["name"] for s in stations if s["id"] == st)
        await self.event("inspection", "defect", "Defect %s · %s: %s %s" % (d["id"], st, d.get("component") or "",
                                                                          d.get("condition") or ""),
                         detail="%s · severity %s" % (stname, d.get("severity")), station_id=st,
                         refs={"defect_id": d["id"], "inspection_event_id": ie["id"]}, source=esrc)
        await self.event("work_order", "wo_draft", "%s drafted · %s" % (wo["id"], wo.get("title")),
                         detail="Priority %s. Isolation must be verified by an authorized person. Draft: needs review."
                                % (wo.get("priority") or "?"), station_id=st, refs={"work_order_id": wo["id"]},
                         source=esrc)
        if not self.active_station:
            await self.select_station(st, "system")
        if wo.get("tool_id") and wo.get("est_trigger_min"):
            await self.plan({"station_id": st, "task": wo.get("title"), "tool_id": wo["tool_id"],
                             "est_trigger_min": wo["est_trigger_min"], "work_order_id": wo["id"]}, "system",
                            mark_planned=False)
        self.changed()
        return {"status": "ok", "defect": d, "work_order": wo, "question": None, "transcript_id": tr["id"]}

    # ---- planning ----------------------------------------------------------------------
    async def plan(self, planned: dict, source: str, mark_planned: bool = True) -> dict:
        ss = self.shift_start()
        current = self.exposure_summary(self.store.since("exposure_records", ss))["total_points"]
        pl = self.mods.get("planner")
        try:
            rec = await asyncio.to_thread(pl.recommend, current, planned, self.tools) if pl \
                else fallback_recommend(current, planned, self.tools)
        except Exception as e:
            log("planner.recommend failed, using fallback:", repr(e))
            rec = fallback_recommend(current, planned, self.tools)
        rec = dict(rec or {})
        rec.setdefault("requires_supervisor_review", True)
        rec.update({"planned": planned, "current_points": current, "ts": time.time()})
        self.recommendation = rec
        self.store.set_meta("recommendation", rec, rec.get("source") or "rules")
        tool = self.tool(planned.get("tool_id"))
        if mark_planned and planned.get("work_order_id"):
            self.store.update("work_orders", planned["work_order_id"], status="planned",
                              tool_id=planned.get("tool_id"), est_trigger_min=planned.get("est_trigger_min"))
            await self.event("work_order", "wo_planned", "%s planned · %s" % (planned["work_order_id"], planned.get("task") or ""),
                             detail="%s, %s min trigger time" % (tool["name"] if tool else planned.get("tool_id"),
                                                                 planned.get("est_trigger_min")),
                             station_id=planned.get("station_id"), refs={"work_order_id": planned["work_order_id"]},
                             source=source)
        await self.event("exposure", "projection", "Projected %.0f pts (+%.0f) · %s" % (
            float(rec.get("projected_points") or 0), float(rec.get("added_points") or 0), rec.get("status")),
                         detail=(rec.get("headline") or "") + " · Supervisor review required",
                         station_id=planned.get("station_id"), refs={"work_order_id": planned.get("work_order_id")},
                         source=rec.get("source") or "rules")
        self.changed()
        return rec

    # ---- user actions ------------------------------------------------------------------
    async def select_station(self, station_id, source: str = "user") -> dict:
        st = self.store.get("stations", station_id) if station_id else None
        if station_id and not st:
            raise HTTPException(404, "unknown station %s" % station_id)
        self.active_station = station_id
        self.store.set_meta("active_station", station_id, source)
        if st:
            self.store.insert("inspection_events", source, worker_id=dbmod.WORKER["id"],
                              conveyor_id=dbmod.CONVEYOR["id"], station_id=station_id, kind="station_selected",
                              note=st["name"])
            await self.event("inspection", "station", "At %s · %s" % (station_id, st["name"]), station_id=station_id,
                             source=source)
        self.changed()
        return {"active_station": station_id}

    async def select_tool(self, tool_id, source: str = "user", log: bool = True) -> dict:
        tool = self.tool(tool_id)
        if tool_id and not tool:
            raise HTTPException(404, "unknown tool %s" % tool_id)
        self.active_tool_id = tool_id
        self.store.set_meta("active_tool", tool_id, source)
        if log and tool:
            await self.event("tool", "tool_selected", "Tool selected · %s" % tool["name"],
                             detail="%.1f m/s² (%s)" % (float(tool["vibration_m_s2"]), tool.get("source", "")),
                             station_id=self.active_station, source=source)
        elif log:
            await self.event("tool", "tool_cleared", "Tool put away", station_id=self.active_station, source=source)
        if self.active_session_id and tool:
            self.store.update("tool_sessions", self.active_session_id, tool_id=tool_id)
        await self.apply_rate()                          # 416 Hz for tool work, 50 Hz when put away
        self.changed()
        return {"active_tool": tool}

    # ---- tool checkout -> live monitoring -> exposure verdict ---------------------------
    def open_assignment(self):
        rows = self.store.query("SELECT * FROM tool_assignments WHERE worker_id = ? AND returned_ts IS NULL "
                                "ORDER BY ts DESC LIMIT 1", (dbmod.WORKER["id"],))
        return rows[0] if rows else None

    async def checkout(self, tool_id: str) -> dict:
        tool = self.tool(tool_id)
        if not tool:
            raise HTTPException(404, "unknown tool %s" % tool_id)
        prev = self.open_assignment()
        if prev and prev.get("tool_id") == tool_id:
            return {"assignment": self.assignment_view()[0]}
        if prev:                                         # one tool at a time: hand the old one back first
            await self.return_tool(quiet_clear=True)
        worker = self.store.worker()
        a = self.store.insert("tool_assignments", "user", worker_id=worker["id"], tool_id=tool_id, returned_ts=None)
        if self._agent() is not None and not (self.job_agent.snapshot() or {}).get("job"):
            self.ensure_job_now()                        # tool locked: the job checklist must be there
        await self.select_tool(tool_id, "user", log=False)
        await self.event("tool", "tool_checkout", "%s assigned to %s" % (tool["name"], worker["id"]),
                         detail="%.1f m/s² (%s). Screening estimate: points = 2 x a² x trigger hours."
                                % (float(tool["vibration_m_s2"]), tool.get("source", "")),
                         station_id=self.active_station, refs={"assignment_id": a["id"]}, source="user")
        self.changed()
        return {"assignment": self.assignment_view()[0]}

    async def end_active_session(self, why: str) -> dict | None:
        """End a running tool session now (a tool return): trigger time from its wall-clock start; confirmed
        only if the detector was confident (>= 0.75) and the tool is known, else unresolved for the technician."""
        sid = self.active_session_id
        if not sid:
            return None
        self.active_session_id = None
        self.ignore_stop = True                          # the detector's own stop for it comes later: ignore
        row = self.store.get("tool_sessions", sid)
        if not row:
            return None
        now = time.time()
        trig = max(0.0, now - float(row.get("wall_start") or now))
        tool = self.tool(row.get("tool_id") or self.active_tool_id)
        conf = float(row.get("confidence") or 0)
        status = "confirmed" if (tool and conf >= 0.75) else "unresolved"
        row = self.store.update("tool_sessions", sid, wall_end=now, trigger_s=trig, status=status,
                                tool_id=tool["id"] if tool else None)
        await self.event("tool", "tool_stop" if status == "confirmed" else "tool_unresolved",
                         ("Tool use %.0f s ended by %s" % (trig, why)) if status == "confirmed" else
                         ("Tool activity %s ended by %s (%.0f s): confirm or reject" % (sid, why, trig)),
                         station_id=row.get("station_id"), refs={"tool_session_id": sid}, source="user")
        if status == "confirmed":
            await self.add_exposure(row, tool, trig, "band")
        else:
            await self.ask_confirmation(row, tool, trig)
        await self.update_led()
        asyncio.create_task(self.job_tool(row))
        return row

    async def return_tool(self, quiet_clear: bool = False) -> dict:
        ended = await self.end_active_session("tool return")    # a return ALWAYS closes: session first
        self.suppress_starts_until = time.time() + 3.0
        a = self.open_assignment()
        if not a:
            tool = self.tool(self.active_tool_id)
            if not tool:
                raise HTTPException(409, "no tool is checked out")
            # The tool was selected (older UI path), not checked out: put it away all the same.
            records = self.store.since("exposure_records", self.shift_start())
            mine = [r for r in records if r.get("tool_id") == tool["id"] and r.get("status") != "rejected"]
            trig = sum(float(r.get("trigger_s") or 0) for r in mine)
            pts = sum(float(r.get("points") or 0) for r in mine)
            await self.event("tool", "tool_return", "%s returned by %s" % (tool["name"], dbmod.WORKER["id"]),
                             detail="No checkout record (the tool was selected, not checked out). Today with this tool: "
                                    "%.0f s trigger time, %.1f points (screening estimate)" % (trig, pts),
                             station_id=self.active_station, source="user")
            if not quiet_clear:
                await self.select_tool(None, "user", log=False)
            self.changed()
            return {"returned": None, "tool_id": tool["id"], "note": "no open checkout; active tool cleared",
                    "trigger_s_today_with_this_tool": round(trig, 1), "points_with_this_tool": round(pts, 2)}
        self.store.update("tool_assignments", a["id"], returned_ts=time.time())
        tool = self.tool(a.get("tool_id")) or {"id": a.get("tool_id"), "name": a.get("tool_id")}
        records = self.store.since("exposure_records", self.shift_start())
        mine = [r for r in records if r.get("tool_id") == tool["id"] and r.get("status") != "rejected"]
        trig = sum(float(r.get("trigger_s") or 0) for r in mine)
        pts = sum(float(r.get("points") or 0) for r in mine)
        await self.event("tool", "tool_return", "%s returned by %s" % (tool["name"], a.get("worker_id")),
                         detail="Today with this tool: %.0f s trigger time, %.1f points (screening estimate)" % (trig, pts),
                         station_id=self.active_station, refs={"assignment_id": a["id"]}, source="user")
        if not quiet_clear:
            await self.select_tool(None, "user", log=False)
        self.changed()
        return {"returned": a["id"], "trigger_s_today_with_this_tool": round(trig, 1),
                "points_with_this_tool": round(pts, 2)}

    def assignment_view(self, records=None):
        """(assignment, verdict) for the snapshot: the checked-out tool, today's trigger time and points
        with it, the live session, and the day's verdict from S4's exposure.daily_summary."""
        if records is None:
            records = self.store.since("exposure_records", self.shift_start())
        a = self.open_assignment()
        tool = self.tool(a["tool_id"]) if a else self.tool(self.active_tool_id)
        vib = float(tool["vibration_m_s2"]) if tool and tool.get("vibration_m_s2") is not None else None
        ex = self.mods.get("exposure")
        summary = None
        if ex:
            try:
                summary = ex.daily_summary(records, next_vibration_m_s2=vib)
            except Exception as e:
                log("daily_summary failed:", e)
        if not summary:
            total = sum(float(r.get("points") or 0) for r in records if r.get("status") != "rejected")
            left = lambda target: (max(0.0, target - total) / (2 * vib * vib) * 60.0) if vib else None
            summary = {"total_points": total, "minutes_to_action": left(ACTION), "minutes_to_limit": left(LIMIT),
                       "status": "above_limit" if total >= LIMIT else "above_action" if total >= ACTION else "below_action",
                       "by_tool": {}}
        total = float(summary.get("total_points") or 0)
        live = None
        if self.active_session_id:
            row = self.store.get("tool_sessions", self.active_session_id)
            if row and row.get("wall_start"):
                elapsed = max(0.0, time.time() - float(row["wall_start"]))
                prov = (2 * vib * vib * elapsed / 3600.0) if vib else None
                live = {"tool_session_id": row["id"], "elapsed_s": round(elapsed, 1),
                        "provisional_points": round(prov, 3) if prov is not None else None}
        assignment = None
        if a:
            mine = (summary.get("by_tool") or {}).get(a["tool_id"], {})
            trig = float(mine.get("trigger_s") or 0.0)
            assignment = {"id": a["id"], "tool": tool, "worker_id": a.get("worker_id"), "since_ts": a["ts"],
                          "trigger_s_today_with_this_tool": round(trig, 1),
                          "points_with_this_tool": round(float(mine.get("points") or 0.0), 3),
                          "active_session": live,
                          "trigger_s_including_live": round(trig + (live["elapsed_s"] if live else 0.0), 1)}
        state = {"above_limit": "limit", "above_action": "action"}.get(summary.get("status"), "within")
        m_act, m_lim = summary.get("minutes_to_action"), summary.get("minutes_to_limit")
        name = tool["name"] if tool else None
        if state == "within":
            headline = "Within the action value: %.0f of 100 points today." % total
            if name and m_act is not None:
                headline += " About %s more trigger time with the %s before 100." % (fmt_minutes(m_act), name)
        elif state == "action":
            headline = "Action value reached: %.0f points today. Supervisor review required." % total
            if name and m_lim is not None:
                headline += " %s more with the %s reaches the 400-point limit." % (fmt_minutes(m_lim), name)
        else:
            headline = ("Limit value reached: %.0f points today. Supervisor review required before any more "
                        "vibrating-tool work." % total)
        verdict = {"state": state, "headline": headline, "total_points": round(total, 2),
                   "minutes_to_action": m_act, "minutes_to_limit": m_lim,
                   "basis_tool_id": tool["id"] if tool else None, "basis_vibration_m_s2": vib,
                   "provisional_points": live["provisional_points"] if live else None,
                   "action": ACTION, "limit": LIMIT,
                   "label": "SCREENING ESTIMATE — not a certified HAV measurement"}
        return assignment, verdict

    async def verdict_ticker(self) -> None:
        """While a tool session runs, push the live assignment/verdict every second."""
        while True:
            await asyncio.sleep(1.0)
            if not self.active_session_id or not self.hub.clients:
                continue
            try:
                assignment, verdict = self.assignment_view()
                await self.hub.broadcast({"type": "verdict", "source": "system", "assignment": assignment,
                                          "verdict": verdict})
            except Exception as e:
                log("verdict ticker:", e)

    async def set_mic(self, on: bool, source: str = "user") -> dict:
        if not self.bridge or self.bridge.band is None:
            raise HTTPException(409, "band is not connected")
        try:
            res = await asyncio.to_thread(self.bridge.set_mic, bool(on))
        except Exception as e:
            msg = str(e) or type(e).__name__
            await self.event("system", "mic_error", "Voice report unavailable", detail=msg, source="system")
            raise HTTPException(409, msg)
        await self.event("inspection", "mic", "Voice report: listening" if on else "Voice report: stopped",
                         detail="Band microphone audio is sent to Deepgram while listening." if on else "",
                         station_id=self.active_station, source=source)
        if not on:
            await self.update_led()
        await self.apply_rate()                          # 208 Hz while the mic is on, back up when it is off
        self.changed()
        return res

    async def set_mic_state(self, on: bool, via: str) -> dict:
        """Passive: the Band link page reports that the band's mic is listening (voice is always on while
        the band is connected). Recorded for the snapshot only; no command goes to the band."""
        if on != self.mic_state["on"]:
            self.mic_state = {"on": on, "source": "band", "via": via, "since_ts": time.time()}
            await self.event("inspection", "mic", "Listening on band 07" if on else "Band 07 mic off",
                             detail=("Every sentence is recorded and checked for defects" if on else ""),
                             station_id=self.active_station, source="band")
            await self.hub.broadcast({"type": "mic", "source": "band", "on": on, "via": via,
                                      "since_ts": self.mic_state["since_ts"]})
            if not on:
                self.led_key = None                  # LED re-applies on the next state change (no command now)
            self.changed()
        else:
            self.mic_state["via"] = via
        return dict(self.mic_state)

    # ---- voice intents, band gestures, band alerts --------------------------------------
    async def assistant(self, text: str, intent=None, source: str = "system", refs=None) -> None:
        msg = {"type": "assistant", "source": source if source in ("ai", "rules", "system", "band", "user", "sim")
               else "system", "text": text, "intent": intent}
        if refs:
            msg["refs"] = refs
        await self.hub.broadcast(msg)

    def send_band_alert(self, why: str) -> None:
        """Flash the band: the page pauses the mic, blinks red for 4 s, then resumes the mic."""
        b = self.bridge
        if isinstance(b, bandmod.IngestBridge) and b.band is not None:
            b.send_cmd({"type": "command", "cmd": "alert", "colour": "#EF4444", "pattern": "blink", "seconds": 4,
                        "reason": why})
        elif b is not None and b.band is not None:                   # Python link: pause audio, flash, resume
            self.led_key = None
            asyncio.create_task(asyncio.to_thread(b.alert, "#EF4444", 4.0))

    def latest_unresolved_session(self, max_age_s: float = 600.0):
        """Only sessions that ENDED in the last 10 minutes can be confirmed by double-tap or a spoken yes
        (S8's prompt window); older ones stay unresolved and are confirmed from the Details list."""
        rows = self.store.query("SELECT * FROM tool_sessions WHERE ts >= ? AND status = 'unresolved' "
                                "AND COALESCE(wall_end, wall_start, ts) >= ? ORDER BY ts DESC LIMIT 1",
                                (self.shift_start(), time.time() - max_age_s))
        return rows[0] if rows else None

    async def start_python_voice(self) -> None:
        """Python link = the hands-free path: mic always on -> Deepgram -> every sentence into report()."""
        b = self.bridge
        if b is None or b.voice_wanted:
            return
        try:
            await asyncio.to_thread(b.start_voice)
        except Exception as e:
            await self.event("system", "mic_error", "Voice unavailable on the Python link", detail=str(e)[:200],
                             source="system")
            return
        self.mic_state = {"on": True, "source": "band", "via": "python", "since_ts": time.time()}
        await self.event("inspection", "mic", "Listening on band 07", detail="Always on: every sentence is recorded "
                         "and checked", station_id=self.active_station, source="band")
        await self.hub.broadcast({"type": "mic", "source": "band", "on": True, "via": "python",
                                  "since_ts": self.mic_state["since_ts"]})
        if not b.sim:
            heartbeat("VOICE LIVE (python) · band 07 mic -> Deepgram -> /api/report (intents first), always on")
        self.changed()

    async def ask_confirmation(self, row: dict, tool, trig: float) -> None:
        """Ask by voice; a bare yes/no only counts inside this window (S4: awaiting_confirmation)."""
        self.awaiting_confirmation_until = time.time() + 30.0
        what = tool["name"] if tool else "a tool"
        await self.assistant("Was that %s, %.0f seconds? Say yes or no, or double-tap to confirm." % (what, trig),
                             "ask_confirmation", "system", refs={"tool_session_id": row["id"]})

    # ---- S4's job agent: proactive checklist + background actions -----------------------
    def new_job_agent(self):
        ja = self.mods.get("job_agent")
        try:
            self.job_agent = ja.JobAgent() if ja is not None else None
        except Exception as e:
            log("job agent not started:", repr(e))
            self.job_agent = None
        self._job_agent_mod = ja
        self.ensure_job_now()
        return self.job_agent

    def ensure_job_now(self) -> None:
        """The job checklist exists from the start of a shift (all six steps to do), not only after the first
        sentence: create it on the agent and broadcast {type:"job"}."""
        a = self.job_agent
        if a is None:
            return
        try:
            if not (a.snapshot() or {}).get("job"):
                a.on_state(self.intent_context())
            job = (a.snapshot() or {}).get("job")
            loop = self.hub.loop
            if job and loop is not None and loop.is_running():
                loop.create_task(self.hub.broadcast({"type": "job", "source": "rules", "job": self._job_view(job)}))
                self.changed()
        except Exception as e:
            log("ensure_job_now failed:", repr(e))

    def _agent(self):
        """ONE JobAgent for W-01, kept across hot reloads of job_agent.py: when the class changes, the running
        checklist (job, actions, counters) moves into an instance of the new class, so new code applies live."""
        ja = self.mods.get("job_agent")
        if ja is None:
            return self.job_agent
        if self.job_agent is None:
            return self.new_job_agent()
        cls = getattr(ja, "JobAgent", None)
        if cls is not None and type(self.job_agent) is not cls:
            try:
                fresh = cls()
                fresh.__dict__.update(self.job_agent.__dict__)
                self.job_agent, self._job_agent_mod = fresh, ja
                log("job agent moved to the reloaded class (checklist kept)")
            except Exception as e:
                log("job agent class migration failed, keeping the old instance:", repr(e))
        return self.job_agent

    async def job_transcript(self, text: str, res: dict):
        agent = self._agent()
        if agent is None:
            return None
        try:
            report = res if (res.get("status") == "ok" and res.get("defect")) else None   # S4: only a created defect/WO
            changes = await asyncio.to_thread(agent.on_transcript, text, self.intent_context(), report)
        except Exception as e:
            log("job_agent.on_transcript failed:", repr(e))
            return None
        await self.apply_job_changes(changes)
        return changes

    async def job_tool(self, row: dict) -> None:
        agent = self._agent()
        if agent is None or not row:
            return
        try:
            changes = await asyncio.to_thread(agent.on_tool_session, dict(row), self.intent_context())
        except Exception as e:
            log("job_agent.on_tool_session failed:", repr(e))
            return
        await self.apply_job_changes(changes)

    async def job_state(self) -> None:
        agent = self._agent()
        if agent is None or not hasattr(agent, "on_state"):
            return
        try:
            changes = await asyncio.to_thread(agent.on_state, self.intent_context())
        except Exception as e:
            log("job_agent.on_state failed:", repr(e))
            return
        await self.apply_job_changes(changes)

    async def apply_job_changes(self, changes) -> None:
        """Show what the agent did and apply the few effects that belong to the backend. The agent never closes
        work: "job done" only moves the work order to awaiting_verification."""
        if not isinstance(changes, dict):
            return
        for a in reversed(changes.get("actions") or []):
            kind = a.get("kind")
            if kind == "job_done":
                job = (self.job_agent.snapshot().get("job") if self.job_agent else None) or {}
                wo_id = job.get("work_order_id")
                if wo_id and self.store.get("work_orders", wo_id):
                    self.store.update("work_orders", wo_id, status="awaiting_verification")
            await self.event("work_order" if kind in ("work_order", "parts_request", "job_done", "missing_evidence",
                                                      "signoff", "isolation") else "system",
                             "agent_action", "Agent: %s" % a.get("text"), detail="%s · %s" % (kind, a.get("status")),
                             station_id=self.active_station, refs={"action_id": a.get("id")}, source="rules")
            await self.hub.broadcast({"type": "agent_action", "source": "rules", "action": a})
        if changes.get("job"):
            await self.hub.broadcast({"type": "job", "source": "rules", "job": self._job_view(changes["job"])})
        if changes.get("actions") or changes.get("job"):
            self.changed()

    @staticmethod
    def _job_view(job):
        if not job:
            return None
        j = dict(job)
        j["wo_id"] = j.get("work_order_id")
        j["steps"] = [dict(st, text=st.get("title")) for st in (j.get("steps") or [])]
        return j

    async def band_event(self, kind: str, src: str) -> None:
        if kind == "double-tap":
            s = self.latest_unresolved_session()
            if not s:
                await self.assistant("Double-tap: nothing to confirm", "confirm_session", "system")
                return
            try:
                row = await self.confirm_session(s["id"], {"confirmed": True})
                text = "Confirmed by double-tap: %s, %.0f s, +%.2f pts" % (
                    s["id"], float(row.get("trigger_s") or 0), float(row.get("points") or 0))
            except HTTPException as e:
                text = "Double-tap: could not confirm %s (%s)" % (s["id"], e.detail)
            await self.event("tool", "double_tap", text, station_id=s.get("station_id"),
                             refs={"tool_session_id": s["id"]}, source=src)
            await self.assistant(text, "confirm_session", "system", refs={"tool_session_id": s["id"]})
        elif kind == "free-fall":
            st = self.store.get("stations", self.active_station) if self.active_station else None
            where = "%s %s" % (st["id"], st["name"]) if st else "conveyor CV-102"
            text = "Possible drop or fall near %s: check on %s" % (where, self.store.worker()["id"])
            await self.event("system", "safety", text, detail="Band free-fall event. A person must check; BeltGuard "
                             "only reports it.", station_id=self.active_station, source=src)
            await self.assistant(text, "safety_check", "system")
        # tap, tilt, orientation: ignored

    def intent_context(self) -> dict:
        """S4's intents read the snapshot (exposure, stations, tools, tool_sessions, work_orders, ...)."""
        snap = self.snapshot()
        snap.update({"active_tool_id": self.active_tool_id, "unresolved_session": self.latest_unresolved_session(),
                     "awaiting_confirmation": time.time() < self.awaiting_confirmation_until,
                     "open_work_orders": [w for w in snap.get("work_orders", [])
                                          if w.get("status") in ("draft", "planned", "in_progress")]})
        return snap

    async def run_intent(self, parsed: dict, name: str, source: str, tr: dict, heard: float, title: str) -> dict:
        im = self.mods.get("intents")
        if parsed.get("clarify"):                        # S4 needs more words: ask, do NOT act
            result = {"ok": False, "clarify": parsed.get("clarify"), "message": parsed.get("clarify")}
            respond_with = None
        else:
            try:
                result = await self.execute_intent(name, parsed.get("slots") or {}, source)
            except Exception as e:
                log("intent %s failed: %r" % (name, e))
                result = {"ok": False, "error": str(e), "message": "That did not work: %s" % e}
            respond_with = result if "error" not in result else {"error": result["error"]}
        response = None
        if im is not None and hasattr(im, "respond"):
            try:
                response = await asyncio.to_thread(im.respond, parsed, respond_with, self.intent_context())
            except Exception as e:
                log("intents.respond failed:", repr(e))
        response = response or result.get("message") or ("Done." if result.get("ok") else "I could not do that.")
        status = "ok" if result.get("ok") else "clarify"
        isrc = parsed.get("source") if parsed.get("source") in ("ai", "rules") else "rules"
        self.store.update("transcripts", tr["id"], result_status=status, intent=name, response=response,
                          extracted_by=isrc, question=None if result.get("ok") else response,
                          station_id=self.active_station)
        await self.event("inspection", "transcript", title, detail=response, station_id=self.active_station,
                         refs={"transcript_id": tr["id"], "intent": name}, source=source, ts=heard)
        await self.assistant(response, name, isrc, refs={"transcript_id": tr["id"]})
        return {"status": status, "intent": name, "response_text": response, "result": result, "defect": None,
                "work_order": None, "question": None if result.get("ok") else response, "transcript_id": tr["id"]}

    async def execute_intent(self, name: str, slots: dict, source: str) -> dict:
        try:
            if name == "select_station":
                st = self.store.get("stations", str(slots.get("station_id") or ""))
                if not st:
                    return {"ok": False, "message": "Which station? Say a number from 1 to 5."}
                raw = await self.select_station(st["id"], source)
                return {**raw, "ok": True, "station_id": st["id"], "station_name": st["name"],
                        "message": "At %s, %s." % (st["id"], st["name"])}
            if name == "tool_checkout":
                tool = self.tool(slots.get("tool_id"))
                if not tool:
                    return {"ok": False, "message": "Which tool? For example hammer drill, impact wrench or angle grinder."}
                raw = await self.checkout(tool["id"])
                v = self.assignment_view()[1]
                return {**raw, "ok": True, "tool_id": tool["id"], "tool_name": tool["name"], "verdict": v,
                        "message": "%s checked out. %s" % (tool["name"], v["headline"])}
            if name == "tool_return":
                if not self.open_assignment() and not self.active_tool_id:
                    return {"ok": False, "message": "No tool is checked out."}
                r = await self.return_tool()
                return {"ok": True, **r, "message": "Tool returned: %.0f s trigger time, %.1f points with it today."
                        % (r.get("trigger_s_today_with_this_tool") or 0, r.get("points_with_this_tool") or 0)}
            if name in ("confirm_session", "reject_session"):
                s = self.store.get("tool_sessions", str(slots.get("tool_session_id") or "")) \
                    or self.latest_unresolved_session()
                ended = float((s or {}).get("wall_end") or (s or {}).get("wall_start") or (s or {}).get("ts") or 0)
                if s and s.get("status") == "unresolved" and time.time() - ended > 600:
                    s = self.latest_unresolved_session()    # too old to confirm by voice (10-min window)
                if not s or s.get("status") not in ("unresolved", "active"):
                    return {"ok": False, "message": "There is no unconfirmed tool activity."}
                data = {"confirmed": name == "confirm_session"}
                if slots.get("trigger_s"):
                    data["trigger_s"] = float(slots["trigger_s"])
                row = await self.confirm_session(s["id"], data)
                if name == "reject_session":
                    return {**row, "ok": True, "session_id": s["id"], "message": "Tool activity %s rejected." % s["id"]}
                return {**row, "ok": True, "session_id": s["id"], "trigger_s": row.get("trigger_s"), "points": row.get("points"),
                        "message": "Confirmed %s: %.0f s, +%.2f points." % (
                            s["id"], float(row.get("trigger_s") or 0), float(row.get("points") or 0))}
            if name == "job_complete":
                wo = self.store.get("work_orders", str(slots.get("work_order_id") or "")) or \
                    self.open_work_order(self.active_station)
                if not wo:
                    return {"ok": False, "message": "Which work order is complete?"}
                self.store.update("work_orders", wo["id"], status="awaiting_verification")
                await self.event("work_order", "wo_complete_reported", "%s reported complete: awaiting verification"
                                 % wo["id"], detail="Reported by the technician. An authorized person must verify "
                                 "it before it is closed.", station_id=wo.get("station_id"),
                                 refs={"work_order_id": wo["id"]}, source=source)
                self.changed()
                return {"ok": True, "work_order_id": wo["id"],
                        "message": "%s is awaiting verification. A supervisor must verify it." % wo["id"]}
            if name == "isolation_claim":
                wo = self.store.get("work_orders", str(slots.get("work_order_id") or "")) or \
                    self.open_work_order(self.active_station)
                await self.event("work_order", "isolation_claim", "Isolation reported by technician%s" % (
                    (" for " + wo["id"]) if wo else ""),
                    detail="Logged only. An authorized person must verify isolation; BeltGuard never authorizes it.",
                    station_id=self.active_station, refs={"work_order_id": wo["id"]} if wo else {}, source=source)
                return {"ok": True, "work_order_id": wo["id"] if wo else None,
                        "message": "Isolation claim logged. An authorized person must verify it before work starts."}
            if name == "take_break":
                mins = slots.get("minutes")
                self.break_since = time.time()
                await self.event("system", "break", "Break started%s" % (" (%s min)" % mins if mins else ""),
                                 station_id=self.active_station, source=source)
                self.changed()
                return {"ok": True, "minutes": mins, "message": "Break logged."}
            if name == "help":
                return {"ok": True, "message": "You can say: I am at station three. Check out the hammer drill. "
                        "Return the tool. Confirm. Job done. Isolated. Taking a break. How much exposure today."}
            if name in ("status_query", "exposure_query"):     # no action: respond reads snapshot["exposure"]
                v = self.assignment_view()[1]
                return {"ok": True, "verdict": v, "tool_id": slots.get("tool_id"), "message": v["headline"]}
        except HTTPException as e:
            return {"ok": False, "message": str(e.detail)}
        return {"ok": False, "message": "I can't do that by voice yet."}

    async def confirm_session(self, sid: str, data: dict) -> dict:
        self.awaiting_confirmation_until = 0.0
        row = self.store.get("tool_sessions", sid)
        if not row:
            raise HTTPException(404, "unknown tool session %s" % sid)
        if row.get("status") == "confirmed":
            return row
        if not data.get("confirmed", True):
            row = self.store.update("tool_sessions", sid, status="rejected")
            await self.event("tool", "tool_rejected", "Tool activity %s rejected by technician" % sid,
                             station_id=row.get("station_id"), refs={"tool_session_id": sid}, source="user")
            self.changed()
            return row
        tool = self.tool(data.get("tool_id") or row.get("tool_id") or self.active_tool_id)
        if not tool:
            raise HTTPException(409, "select the tool that was used before confirming")
        trig = float(data.get("trigger_s") or row.get("trigger_s") or 0)
        row = self.store.update("tool_sessions", sid, status="confirmed", tool_id=tool["id"], trigger_s=trig)
        await self.event("tool", "tool_confirmed", "Tool use %.0f s confirmed · %s" % (trig, tool["name"]),
                         station_id=row.get("station_id"), refs={"tool_session_id": sid}, source="user")
        await self.add_exposure(row, tool, trig, "user")
        await self.update_led()
        row = self.store.get("tool_sessions", sid)
        asyncio.create_task(self.job_tool(row))
        self.changed()
        return row

    async def reset_shift(self) -> dict:
        now = time.time()
        self.store.set_meta("shift_start", now, "user")
        self.store.set_meta("recommendation", None, "user")
        self.store.set_meta("active_station", None, "user")
        self.recommendation, self.active_station, self.active_session_id = None, None, None
        self.close_orphan_sessions("shift reset mid-session")
        self.new_job_agent()                             # a new shift starts a new job checklist
        await self.event("system", "shift_start", "New shift started", source="user")
        await self.update_led()
        self.changed()
        return {"shift_start": now}


# ---------------------------------------------------------------------------------------
S: BeltGuard | None = None


@asynccontextmanager
async def lifespan(_app):
    S.hub.bind(asyncio.get_running_loop())
    consumer = asyncio.create_task(S.consume())
    watcher = asyncio.create_task(S.watch_tools())
    ticker = asyncio.create_task(S.verdict_ticker())
    async def fleet_loop():                              # supervisor fleet: W-01 real + W-02..W-05 SIMULATED
        path, key, mod, task = BASE / "fleet.py", None, None, None
        while True:
            k = path.stat().st_mtime if path.exists() else None
            if k != key:                                 # fleet.py changed (or first start): (re)load it
                key = k
                try:
                    mod = importlib.reload(mod) if mod else importlib.import_module("fleet")
                    new_fleet = mod.Fleet(S)
                    if task:
                        task.cancel()
                    S.fleet, task = new_fleet, asyncio.create_task(new_fleet.run())
                    log("fleet layer (re)loaded")
                except Exception as e:
                    log("fleet layer not (re)loaded:", repr(e))
            await asyncio.sleep(5)
    fleet_task = asyncio.create_task(fleet_loop())
    asyncio.create_task(S.job_state())                   # the job checklist is visible from the start
    if S.dev_sim:                                        # developer pretend band only with --sim
        S.start_bridge(sim=True)
    if S.search_links:
        async def search_band():
            await asyncio.sleep(2 if S.dev_sim else 0.5)
            started = S.start_search()
            if started:
                log("searching for band 07 over", " + ".join(started))
        asyncio.create_task(search_band())
    S.ready.set()
    yield
    consumer.cancel()
    watcher.cancel()
    ticker.cancel()
    if fleet_task:
        fleet_task.cancel()
    await S.stop_search()
    if S.bridge:
        await asyncio.to_thread(S.bridge.stop)
    await asyncio.to_thread(S.samples.stop)              # write what is still queued


app = FastAPI(title="BeltGuard", lifespan=lifespan)
# Only pages served from this machine may call the API: a website open in the same browser also
# connects from 127.0.0.1, so the client address alone proves nothing. WebSockets are not covered by
# CORS, so the two sockets check the Origin header themselves (local_origin below).
LOCAL_ORIGIN_RE = re.compile(r"^http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$")
app.add_middleware(CORSMiddleware, allow_origin_regex=LOCAL_ORIGIN_RE.pattern, allow_methods=["*"],
                   allow_headers=["*"])


def local_origin(conn) -> bool:
    """True for local scripts (no Origin) and pages served from this machine; False for other websites."""
    origin = conn.headers.get("origin")
    if conn.headers.get("sec-fetch-site") == "cross-site":
        return False
    return origin is None or bool(LOCAL_ORIGIN_RE.match(origin))


@app.middleware("http")
async def no_store(request: Request, call_next):
    """Sessions save frontend files constantly: never let the browser serve a stale copy. During startup,
    /api/* waits up to 3 s for the server to be ready instead of failing."""
    if request.url.path.startswith("/api/") and S is not None and not S.ready.is_set():
        try:
            await asyncio.wait_for(S.ready.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            pass
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


async def body(req: Request) -> dict:
    try:
        data = await req.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@app.get("/api/state")
async def api_state():
    return S.snapshot()


@app.get("/api/voice/config")
async def api_voice_config(req: Request):
    """S0: the Deepgram key from beltguard/.env for the Band link page's live transcript.
    Loopback clients only; re-reads .env on every call; never logs the value."""
    host = (req.client.host if req.client else "") or ""
    if host not in ("127.0.0.1", "::1", "localhost") or not local_origin(req):   # S2: also refuse other websites
        raise HTTPException(status_code=403, detail="loopback only")
    key = ""
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DEEPGRAM_API_KEY=") and "=" in line:
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if key:
        os.environ["DEEPGRAM_API_KEY"] = key       # lets the Python mic path use it too, no restart needed
    return {"deepgram_key": key, "has_key": bool(key)}


@app.get("/api/health")
async def api_health():
    st = S.samples.stats()
    return {"ok": True, "connection": S.connection, "modules": S.mods.status(),
            "detector": S.bridge.detector.kind if S.bridge else None, "clients": len(S.hub.clients),
            "queue_dropped": S.hub.dropped,
            "samples": {"written": st["written"], "queued": st["queued"], "last_error": st["last_error"]}}


@app.get("/api/fleet")
async def api_fleet():
    """Supervisor control room: W-01 is the real band 07; W-02..W-05 are SIMULATED (is_simulated, source sim)."""
    f = getattr(S, "fleet", None)
    if f is None:
        raise HTTPException(503, "fleet layer not running")
    return f.payload()


@app.get("/api/samples")
async def api_samples(limit: int = 500, session_id: str | None = None):
    """The newest real band readings from SQLite (newest first), so anyone can see the recording is real."""
    limit = max(1, min(int(limit), 5000))
    if session_id:
        rows = S.store.query("SELECT * FROM motion_samples WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                             (session_id, limit))
    else:
        rows = S.store.query("SELECT * FROM motion_samples ORDER BY id DESC LIMIT ?", (limit,))
    total = S.store.query("SELECT MAX(id) AS n FROM motion_samples")[0]["n"] or 0
    return {"total_rows": total, "returned": len(rows), "newest_first": True,
            "note": "Real band 07 readings as received: ax..az in g, gx..gz in deg/s (raw, before the gyro-bias "
                    "correction), t_band_ms on the band's own clock, ts on the laptop clock. Never simulated.",
            "writer": S.samples.stats(), "rows": rows}


@app.post("/api/station")
async def api_station(req: Request):
    return await S.select_station((await body(req)).get("station_id"))


@app.post("/api/tool")
async def api_tool(req: Request):
    """Legacy tool picker: the same path as checkout/return, so an active tool always has an assignment."""
    tool_id = (await body(req)).get("tool_id")
    if tool_id:
        res = await S.checkout(tool_id)
        return {"active_tool": S.tool(tool_id), **res}
    if S.open_assignment() or S.active_tool_id:
        res = await S.return_tool()
        return {"active_tool": None, **res}
    return {"active_tool": None}


@app.post("/api/tool/checkout")
async def api_tool_checkout(req: Request):
    tool_id = (await body(req)).get("tool_id")
    if not tool_id:
        raise HTTPException(400, "tool_id is required")
    return await S.checkout(tool_id)


@app.post("/api/tool/return")
async def api_tool_return():
    return await S.return_tool()


@app.post("/api/report")
async def api_report(req: Request):
    data = await body(req)
    text = (data.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text is empty")
    # Words from the band's mic: source "band" (the band is the source, contract set), input "voice".
    voice = data.get("source") in ("band-mic", "band") or data.get("input") == "voice"
    return await S.report(text[:2000], "band" if voice else "user", "voice" if voice else "typed")


@app.get("/api/transcripts")
async def api_transcripts(limit: int = 100):
    """Every utterance recorded (newest first) with what BeltGuard did with it."""
    limit = max(1, min(int(limit), 1000))
    rows = S.store.query("SELECT * FROM transcripts ORDER BY ts DESC LIMIT ?", (limit,))
    total = S.store.query("SELECT COUNT(*) AS n FROM transcripts")[0]["n"]
    return {"total": total, "returned": len(rows), "newest_first": True, "rows": rows}


@app.post("/api/mic")
async def api_mic(req: Request):
    return await S.set_mic(bool((await body(req)).get("on")))


@app.post("/api/mic/state")
async def api_mic_state(req: Request):
    """The Band link page reports its band-mic state. Passive: recorded in snapshot.mic, no band command."""
    data = await body(req)
    return await S.set_mic_state(bool(data.get("on")), str(data.get("source") or "band-link")[:40])


@app.post("/api/job/signoff")
async def api_job_signoff(req: Request):
    """A supervisor signs the job off (a person's button; the agent never does this)."""
    name = ((await body(req)).get("supervisor") or "Supervisor").strip()[:60]
    agent = S._agent()
    if agent is None:
        raise HTTPException(409, "job agent not running")
    changes = await asyncio.to_thread(agent.supervisor_sign_off, name)
    job = (agent.snapshot().get("job") or {})
    if job.get("work_order_id") and S.store.get("work_orders", job["work_order_id"]):
        S.store.update("work_orders", job["work_order_id"], status="verified")
    await S.apply_job_changes(changes)
    return {"job": S._job_view(agent.snapshot().get("job"))}


@app.post("/api/tool_session/{sid}/confirm")
async def api_confirm(sid: str, req: Request):
    return await S.confirm_session(sid, await body(req))


@app.post("/api/repair/plan")
async def api_plan(req: Request):
    data = await body(req)
    st, tool_id = data.get("station_id"), data.get("tool_id")
    if not S.store.get("stations", st or ""):
        raise HTTPException(400, "station_id must be one of S1..S5")
    if not S.tool(tool_id):
        raise HTTPException(400, "unknown tool_id")
    try:
        mins = float(data.get("est_trigger_min"))
    except (TypeError, ValueError):
        raise HTTPException(400, "est_trigger_min must be a number of minutes")
    task = (data.get("task") or "Planned repair").strip()
    wo = S.open_work_order(st)
    if not wo or wo.get("station_id") != st:
        wo = S.store.insert("work_orders", "user", conveyor_id=dbmod.CONVEYOR["id"], station_id=st, title=task,
                            priority="P3", priority_reason="planned by technician", status="draft", steps=[],
                            requires_isolation=True, tool_id=tool_id, est_trigger_min=mins, data={})
        await S.event("work_order", "wo_draft", "%s drafted · %s" % (wo["id"], task), station_id=st,
                      refs={"work_order_id": wo["id"]}, source="user")
    planned = {"station_id": st, "task": task, "tool_id": tool_id, "est_trigger_min": mins, "work_order_id": wo["id"]}
    return await S.plan(planned, "user")


@app.post("/api/label")
async def api_label(req: Request):
    label = (await body(req)).get("label") or "none"
    if not S.bridge:
        raise HTTPException(409, "no band source")
    try:
        S.bridge.set_label(label)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"label": label, "raw_file": S.bridge.status().get("raw_file")}


@app.post("/api/rate")
async def api_rate(req: Request):
    data = await body(req)
    if not S.bridge:
        raise HTTPException(409, "no band source")
    try:
        return await asyncio.to_thread(S.bridge.set_rate, int(data.get("rate") or 50), data.get("sensors") or "both")
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/sim/scene")
async def api_sim_scene(req: Request):
    data = await body(req)
    if not S.bridge:
        raise HTTPException(409, "no band source")
    try:
        return await asyncio.to_thread(S.bridge.sim_scene, data.get("scene") or "tool", data.get("seconds"))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/sim/auto")
async def api_sim_auto(req: Request):
    if not S.bridge:
        raise HTTPException(409, "no band source")
    try:
        return await asyncio.to_thread(S.bridge.sim_auto, bool((await body(req)).get("on", True)))
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.post("/api/band/connect")
async def api_band_connect(req: Request):
    mode = (await body(req)).get("mode") or "band"
    if mode not in ("band", "usb", "sim", "off"):
        raise HTTPException(400, 'mode must be "band" (Python USB + Bluetooth), "usb", "sim" (dev) or "off"')
    if mode == "sim" and not S.dev_sim:                  # never put the pretend band on a live dashboard
        raise HTTPException(403, "the pretend band is disabled on this backend (start it with --sim for development; "
                                 "tests use qa/isolated_server.py)")
    if any(i.band is not None for i in S.ingests) and mode in ("band", "usb"):
        return {"mode": mode, "note": "the dashboard's browser link is connected; Python links stay paused"}
    return await S.set_source(mode)


@app.post("/api/reset")
async def api_reset():
    return await S.reset_shift()


@app.post("/api/worker")
async def api_worker(req: Request):
    name = ((await body(req)).get("name") or "").strip()[:60]
    if not name:
        raise HTTPException(400, "name is empty")
    S.store.update("workers", dbmod.WORKER["id"], name=name)
    S.changed()
    return S.store.worker()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    if not local_origin(ws):                             # another website must not read the live feed
        await ws.close(code=1008)
        return
    await ws.accept()
    S.hub.clients.add(ws)
    try:
        await ws.send_text(json.dumps({"type": "snapshot", "ts": time.time(), "source": "system",
                                       "state": S.snapshot()}, default=str))
        await ws.send_text(json.dumps({"type": "connection", "ts": time.time(), "source": "system",
                                       **S.connection}, default=str))
        while True:
            await ws.receive_text()                    # clients may ping; nothing else expected
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        S.hub.clients.discard(ws)


@app.websocket("/ws/ingest")
async def ws_ingest(ws: WebSocket):
    """The dashboard page streams band 07 here (Chrome Web Bluetooth via the kit JS library):
    {"type":"connection","state":"connected"|"disconnected","transport":"browser-ble","band":7}
    {"type":"motion","rate":50,"sensors":"both","samples":[{"t":<band ms>,"n":..,"ax":..,..,"gz":..},...]}
    {"type":"rate","rate":416,"sensors":"both"}   {"type":"battery","percent":..}   {"type":"link","rssi":..}
    {"type":"mic","on":true|false}   (the page turned the band mic on/off: 208 Hz both while on, no LED)
    Back to the page: {"type":"command","cmd":"led"|"rate",...} and {"type":"ack",...}."""
    if not local_origin(ws):                             # another website must never inject "band" data
        await ws.close(code=1008)
        return
    await ws.accept()
    ing = await S.ingest_attach()
    sender = asyncio.create_task(ing.pump(ws))
    try:
        await ws.send_text(json.dumps({"type": "ack", "ready": True, "band": bandmod.BAND}))
        while True:
            try:
                msg = json.loads(await ws.receive_text())
            except (ValueError, TypeError):
                continue
            if not isinstance(msg, dict):
                continue
            t = msg.get("type")
            if t == "motion":
                if ing.band is None:                     # samples before a "connected" message
                    if S.live_link() is not None and S.bridge is not ing:
                        continue                         # another link owns the band: ignore these
                    try:
                        await S.ingest_connection(ing, {"state": "connected", "band": bandmod.BAND,
                                                        "rate": msg.get("rate"), "sensors": msg.get("sensors")})
                    except ValueError:
                        continue
                if S.bridge is ing:
                    ing.feed(msg.get("samples") or [], msg.get("rate"), msg.get("sensors"))
            elif t == "connection":
                try:
                    await S.ingest_connection(ing, msg)
                except ValueError as e:                  # not band 07: refuse it
                    await ws.send_text(json.dumps({"type": "error", "message": str(e)}))
            elif t == "rate":
                try:
                    ing.on_rate(int(msg.get("rate")), msg.get("sensors") or "both")
                except (TypeError, ValueError):
                    pass
            elif t == "mic":                             # the page turned the band's mic on/off (voice.js)
                on = bool(msg.get("on"))
                if on != ing.mic_on:
                    ing.mic_on = on
                    if S.bridge is ing:
                        if not on:
                            S.led_key = None             # the band takes LED commands again
                            await S.update_led()
                        await S.apply_rate()             # 208 Hz both while audio streams, 416 otherwise
                        S.changed()
            elif t == "event":                           # band gestures from the page (bandlink.js)
                if S.bridge is ing:
                    asyncio.create_task(S.band_event(str(msg.get("kind") or ""), ing.src))
            elif t == "battery":
                ing.battery = msg.get("percent")
            elif t == "link":
                ing.rssi = msg.get("rssi")
    except WebSocketDisconnect:
        pass
    except Exception:
        log("ingest error:\n" + traceback.format_exc())
    finally:
        sender.cancel()
        await S.ingest_detach(ing)


FRONTEND.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")


def main() -> None:
    global S
    p = argparse.ArgumentParser(description="BeltGuard backend")
    p.add_argument("--sim", action="store_true", help="developers only: the kit's pretend band (SIMULATED)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--ble", action="store_true", help="also search for band 07 over Bluetooth from Python")
    p.add_argument("--no-ble", action="store_true",
                   help="leave Bluetooth to the dashboard's browser link; Python only tries the USB cable")
    p.add_argument("--no-usb", action="store_true", help="do not try the USB cable")
    p.add_argument("--try-band", action="store_true", help="with --sim: search USB + Bluetooth and take over when found")
    a = p.parse_args()
    S = BeltGuard(sim=a.sim)
    links = set() if a.no_usb else {"usb"}
    if a.ble or (a.sim and a.try_band):                  # Python Bluetooth only on request: on this laptop it
        links.add("ble")                                 # never found band 07 and it fights Chrome's picker
    if a.sim and not a.try_band:                         # plain --sim: the pretend band only
        links = set()
    S.search_links = links
    log("BeltGuard on http://127.0.0.1:%d  (%s; Python searches: %s; dashboard link: /ws/ingest)" % (
        a.port, "SIMULATED band (dev)" if a.sim else "band %d, no simulated data" % bandmod.BAND,
        " + ".join(sorted(links)) or "none"))
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
