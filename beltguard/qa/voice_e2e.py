#!/usr/bin/env python3
"""BeltGuard voice-driven shift check (S5 qa-verify). Runs on a COPY of the database.

    python beltguard/qa/voice_e2e.py            # isolated, in-process: never touches the live shift
    python beltguard/qa/voice_e2e.py --keep     # keep the temp DB copy for inspection

How it stays isolated
  * copies data/beltguard.db with SQLite's backup API (source opened read-only) into a temp dir;
  * sets BELTGUARD_DB to the copy BEFORE importing backend/app.py (db.py honours it), so the store and
    the motion-sample writer only ever touch the copy;
  * replaces the app's lifespan with one that starts only the event consumer: no band bridge, no
    USB/Bluetooth search, no sim; silences app.heartbeat (the shared ops log);
  * drives the real FastAPI routes with Starlette's TestClient (no network server, no port);
  * the band link is a `test: true` /ws/ingest client, which the backend labels source "sim".

What it checks
  Since S4's 14:40 wake-word change, band-mic commands are addressed ("BeltGuard, ...", also "Hey BeltGuard",
  "Belt guard"); unaddressed commands and team chatter must stay silent and change nothing, while a clear
  defect report is heard without the wake word.
  For each spoken sentence (POST /api/report {"text", "source": "band-mic"}): the intent, the state change
  (station, tool checkout, work-order status), response_text, the WS assistant line, the transcript row
  (source "band", input "voice"), and the honesty rules (AI never verifies isolation, never closes a work
  order). Then band events over /ws/ingest: free-fall -> safety event + assistant line; double-tap with
  nothing pending -> "nothing to confirm"; double-tap with an unresolved session -> confirmed + exposure
  record with points = 2 a^2 T. Then a contract audit of every broadcast and of the copy DB, and whether
  the page the live index.html loads handles {"type":"assistant"}.
Exit code 0 = no FAIL. Results also go to beltguard/qa/last_voice_run.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

QA = Path(__file__).resolve().parent
ROOT = QA.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
LIVE_DB = ROOT / "data" / "beltguard.db"
sys.path.insert(0, str(QA))
import e2e  # noqa: E402  (Results, print_table, SOURCES, check_db)

Results, print_table, SOURCES = e2e.Results, e2e.print_table, e2e.SOURCES


def copy_db(dst_dir: Path) -> Path:
    dst = dst_dir / "beltguard-voice-test.db"
    src = sqlite3.connect("file:" + urllib.request.pathname2url(str(LIVE_DB)) + "?mode=ro", uri=True, timeout=10)
    out = sqlite3.connect(str(dst))
    with out:
        src.backup(out)
    src.close()
    try:
        out.execute("DELETE FROM motion_samples")      # large and irrelevant for these checks
        out.commit()
    except sqlite3.Error:
        pass
    out.close()
    return dst


def load_isolated_app(db_path: Path, sim: bool = False):
    """sim=True also starts S2's pretend band (kit SimTransport, no hardware) so qa/e2e.py can run against it."""
    os.environ["BELTGUARD_DB"] = str(db_path)
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    import db as dbmod
    if Path(dbmod.DB_PATH).resolve() != db_path.resolve():
        raise SystemExit("REFUSING TO RUN: db.py did not honour BELTGUARD_DB (would write the live DB)")
    import app as A
    A.heartbeat = lambda *a, **k: None               # never write the team's shared heartbeat log
    A.bandmod.RAW_DIR = db_path.parent / "raw"       # never write S3's data/raw (its label runs watch it)
    # Hard guarantee: this process never opens a real band link (USB or Bluetooth). Only the kit's SimTransport.
    blocked: list = []
    real_bridge = A.bandmod.BandBridge

    class QABridge(real_bridge):                     # IngestBridge keeps its own (original) base class
        def __init__(self, *a, **k):
            if not k.get("sim", a[1] if len(a) > 1 else False):
                blocked.append(("BandBridge", time.time()))
                raise RuntimeError("QA isolation: refusing a real band connection")
            super().__init__(*a, **k)
    A.bandmod.BandBridge = QABridge
    try:
        S = A.BeltGuard(sim=False)
    except TypeError:
        S = A.BeltGuard()
    if Path(S.store.path).resolve() != db_path.resolve():
        raise SystemExit("REFUSING TO RUN: the app store is not the copy")
    S.search_links = set()                           # never search for band 07 (USB is the app default)

    def no_search(*_a, **_k):
        blocked.append(("start_search", time.time()))
        return []
    S.start_search = no_search
    S.qa_blocked = blocked
    A.S = S
    sent: list[tuple[float, dict]] = []
    orig = S.hub.broadcast

    async def recording(msg):
        await orig(msg)
        sent.append((time.time(), json.loads(json.dumps(msg, default=str))))
    S.hub.broadcast = recording

    @asynccontextmanager
    async def isolated_lifespan(_app):              # the real lifespan minus the band search; sim band only if asked
        S.hub.bind(asyncio.get_running_loop())
        tasks = [asyncio.create_task(S.consume())]
        for name in ("watch_tools", "verdict_ticker"):
            if hasattr(S, name):
                tasks.append(asyncio.create_task(getattr(S, name)()))
        if sim:
            if hasattr(S, "set_source"):
                await S.set_source("sim")
            else:
                S.start_bridge(True)
            S.search_links = set()
        if hasattr(S, "ready"):                     # S2 14:44: /api/* waits up to 3 s for this per request
            S.ready.set()
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            if S.bridge is not None:
                await asyncio.to_thread(S.bridge.stop)
    A.app.router.lifespan_context = isolated_lifespan
    return A, S, sent


def low(x) -> str:
    return str(x or "").lower()


def find_wo(state: dict, station: str):
    rows = [w for w in state.get("work_orders") or [] if w.get("station_id") == station]
    return rows[-1] if rows else None


def assignment_of(state: dict):
    a = state.get("assignment")
    if isinstance(a, dict) and a:
        return a
    return None


# ------------------------------------------------------------------------------------------
# sentence -> expected intent + checks(before, after, body) -> list of (name, ok, detail, owner)
def chk_station(b, a, body):
    return [("state: active_station -> S3", a.get("active_station") == "S3",
             "active_station %r -> %r" % (b.get("active_station"), a.get("active_station")), "S2"),
            ("response names station 3", re.search(r"\bS3\b|station 3|station three|return idler", low(body.get("response_text"))),
             repr(body.get("response_text")), "S4")]


def chk_defect(b, a, body):
    d, wo = body.get("defect") or {}, body.get("work_order") or {}
    nb, na = len(b.get("defects") or []), len(a.get("defects") or [])
    return [("defect logged at S3 (return idler, seized)", d.get("station_id") == "S3" and "idler" in low(d.get("component"))
             and "seiz" in low(d.get("condition")), "defects %d->%d; %s %s/%s/%s" % (
                 nb, na, d.get("id"), d.get("station_id"), d.get("component"), d.get("condition")), "S4"),
            ("work-order draft linked", wo.get("defect_id") == d.get("id") and wo.get("status") in ("draft", "planned")
             and wo.get("requires_isolation") is True, "%s status=%r isolation=%r" % (
                 wo.get("id"), wo.get("status"), wo.get("requires_isolation")), "S2")]


def chk_checkout(b, a, body):
    at, asg = a.get("active_tool") or {}, assignment_of(a)
    return [("state: hammer drill checked out", at.get("id") == "hammer_drill"
             and (asg is None or asg.get("tool_id") in (None, "hammer_drill")),
             "active_tool=%r assignment=%s" % (at.get("id"), e2e.short(asg or {}, 90)), "S2"),
            ("response names the hammer drill", "hammer drill" in low(body.get("response_text")),
             repr(body.get("response_text")), "S4")]


def chk_exposure_query(b, a, body):
    same = (b.get("active_station") == a.get("active_station")
            and (b.get("active_tool") or {}).get("id") == (a.get("active_tool") or {}).get("id")
            and len(b.get("defects") or []) == len(a.get("defects") or []))
    return [("response gives minutes left", re.search(r"\d+\s*(h|min|minutes|hours)", low(body.get("response_text"))),
             repr(body.get("response_text")), "S4"),
            ("no state change for a question", same, "station/tool/defects unchanged" if same else "something changed", "S2")]


def chk_return(b, a, body):
    at, asg = a.get("active_tool"), assignment_of(a)
    open_asg = asg is not None and low(asg.get("status")) in ("", "open", "active", "checked_out")
    return [("state: tool returned (no open checkout)", not at and not open_asg,
             "active_tool=%r assignment=%s" % ((at or {}).get("id") if isinstance(at, dict) else at,
                                               e2e.short(asg or {}, 90)), "S2"),
            ("response summarises the return", "return" in low(body.get("response_text")),
             repr(body.get("response_text")), "S4")]


def chk_job_done(b, a, body):
    wb, wa = find_wo(b, "S3") or {}, find_wo(a, "S3") or {}
    return [("state: S3 WO -> awaiting_verification", wa.get("status") == "awaiting_verification",
             "%s %r -> %r" % (wa.get("id"), wb.get("status"), wa.get("status")), "S2"),
            ("honesty: AI never closes a work order", low(wa.get("status")) not in ("closed", "done", "complete", "completed",
                                                                                     "verified"), "status=%r" % wa.get("status"), "S2"),
            ("response says a supervisor must verify", "verif" in low(body.get("response_text"))
             and "supervisor" in low(body.get("response_text")), repr(body.get("response_text")), "S4")]


def chk_isolation(b, a, body):
    txt = low(body.get("response_text"))
    bad = [p for p in ("isolation verified", "isolation confirmed", "safe to work", "safe to start", "you can start",
                       "conveyor is isolated", "isolation is complete") if p in txt]
    blob = json.dumps(a).lower()
    flags = re.findall(r'"(isolation_verified|isolated|isolation_confirmed)":\s*true', blob)
    wos = [(w.get("id"), w.get("requires_isolation")) for w in a.get("work_orders") or []]
    return [("honesty: response defers to an authorized person", "authori" in txt and "verif" in txt and not bad,
             repr(body.get("response_text")) + (" | forbidden: %s" % bad if bad else ""), "S4"),
            ("honesty: no 'isolation verified' state set by AI", not flags and all(r is True for _, r in wos),
             "flags=%s requires_isolation=%s" % (flags, [r for _, r in wos][-3:]), "S2")]


CORE = {"station": ("station",), "defect": ("defect", "seized", "report"), "tool": ("taking", "check out", "checkout"),
        "exposure": ("exposure", "how long"), "return": ("return", "done with"), "job done": ("job done", "complete")}


def chk_help(b, a, body):
    txt = low(body.get("response_text"))
    examples = txt.count("beltguard,")
    covered = [k for k, words in CORE.items() if any(w in txt for w in words)]
    same = b.get("active_station") == a.get("active_station") and len(b.get("defects") or []) == len(a.get("defects") or [])
    return [("help gives example commands", examples >= 2 or len(covered) >= 2, "%d examples, covers %s" % (examples, covered), "S4"),
            ("help covers the core commands (WARN only)", len(covered) >= 4,
             "covers %s of %s: %r" % (covered, list(CORE), body.get("response_text")), "S4", True),
            ("no state change for help", same, "unchanged" if same else "something changed", "S2")]


def snapshot_key(s: dict) -> tuple:
    """What an ignored utterance must never change."""
    return (s.get("active_station"), (s.get("active_tool") or {}).get("id") if isinstance(s.get("active_tool"), dict) else None,
            len(s.get("defects") or []), tuple((w.get("id"), w.get("status")) for w in s.get("work_orders") or []),
            bool(assignment_of(s)))


def chk_ignored(b, a, body):
    return [("silent: status ignored, no response", body.get("status") == "ignored" and not body.get("response_text"),
             "status=%r response_text=%r" % (body.get("status"), body.get("response_text")), "S4"),
            ("no state change (station, tool, defects, WOs, checkout)", snapshot_key(b) == snapshot_key(a),
             "unchanged" if snapshot_key(b) == snapshot_key(a) else "%s -> %s" % (snapshot_key(b), snapshot_key(a)), "S2")]


# (text, accepted intents, checker, expect a spoken answer). S4 14:40: on the always-on band mic, commands need
# the wake word ("BeltGuard, ..."); only a clear defect report is heard without it; everything else is ignored.
CASES = [
    ("BeltGuard, I'm at station three", {"select_station"}, chk_station, True),
    ("Return idler seized, needs replacement", {"report_defect", "defect_report"}, chk_defect, True),
    ("I'm taking the hammer drill", {"chat", "none"}, chk_ignored, False),                 # no wake word
    ("Idler replaced, job done", {"chat", "none"}, chk_ignored, False),                    # no wake word
    ("We need to do something based on the conversations", {"chat", "none"}, chk_ignored, False),   # team chatter
    ("BeltGuard, I'm taking the hammer drill", {"tool_checkout"}, chk_checkout, True),
    ("BeltGuard, how long can I keep going?", {"exposure_query"}, chk_exposure_query, True),
    ("BeltGuard, done, returning the drill", {"tool_return"}, chk_return, True),
    ("BeltGuard, idler replaced, job done", {"job_complete"}, chk_job_done, True),
    ("Conveyor isolated", {"chat", "none"}, chk_ignored, False),                           # no wake word
    ("Belt guard, conveyor isolated", {"isolation_claim"}, chk_isolation, True),            # split wake word
    ("Hey BeltGuard, what can I say", {"help"}, chk_help, True),
]


def wait_for(sent: list, pred, after: float, timeout: float = 4.0):
    end = time.time() + timeout
    while time.time() < end:
        for t, m in list(sent):
            if t >= after and pred(m):
                return m
        time.sleep(0.05)
    return None


def run(args) -> tuple:
    R = Results()
    info: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": "in-process, DB copy"}
    tmp = Path(tempfile.mkdtemp(prefix="bg-voice-"))
    db_copy = copy_db(tmp)
    info["db_copy"] = str(db_copy)
    A, S, sent = load_isolated_app(db_copy)
    R.add("setup", "isolated app on a DB copy (no band, no server)", "-", "PASS", str(db_copy))
    from fastapi.testclient import TestClient

    with TestClient(A.app) as client:
        code = client.post("/api/reset").status_code
        R.check("setup", "new shift on the copy (POST /api/reset)", "S2", code == 200, "HTTP %s" % code)
        mods = client.get("/api/health").json().get("modules", {})
        info["modules"] = mods
        has_intents = S.mods.get("intents") is not None
        R.check("setup", "S4 intents.py loaded", "S4", has_intents, "intents module %s" % ("loaded" if has_intents else "missing"))

        # ---- spoken sentences ---------------------------------------------------------------
        for text, intents, checker, speaks in CASES:
            before = client.get("/api/state").json()
            t0 = time.time()
            r = client.post("/api/report", json={"text": text, "source": "band-mic"})
            dt = time.time() - t0
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text}
            after = client.get("/api/state").json()
            area = "voice: %s" % text[:28]
            intent = body.get("intent")
            R.check(area, "HTTP 200 + intent %s" % "/".join(sorted(intents)), "S4", r.status_code == 200 and intent in intents,
                    "HTTP %s in %.1fs, intent=%r status=%r" % (r.status_code, dt, intent, body.get("status")))
            rt = body.get("response_text")
            if speaks:
                R.check(area, "response_text present", "S2", isinstance(rt, str) and rt.strip(), e2e.short(rt or body, 120))
            for item in checker(before, after, body):
                name, ok, detail, owner = item[:4]
                R.check(area, name, owner, ok, detail, warn_only=len(item) > 4 and bool(item[4]))
            asst = wait_for(sent, lambda m: m.get("type") == "assistant", t0, 2.0 if speaks else 1.0)
            if speaks:
                R.check(area, "WS assistant line = response_text", "S2",
                        asst is not None and (not rt or asst.get("text") == rt) and asst.get("source") in SOURCES,
                        ("intent=%r source=%r" % (asst.get("intent"), asst.get("source"))) if asst else "no assistant message")
            else:
                R.check(area, "silent: no assistant line", "S2", asst is None,
                        "none" if asst is None else "said %r" % e2e.short(asst.get("text") or "", 80))
            tr_id = body.get("transcript_id")
            row = S.store.get("transcripts", tr_id) if tr_id else None
            R.check(area, "transcript row: source band, input voice", "S2",
                    row is not None and row.get("source") == "band" and row.get("input") == "voice",
                    "%s source=%r input=%r intent=%r" % (tr_id, (row or {}).get("source"), (row or {}).get("input"),
                                                        (row or {}).get("intent")))

        # ---- band events over /ws/ingest (test link) -------------------------------------------
        with client.websocket_connect("/ws/ingest") as ws:
            ack = ws.receive_json()
            R.check("ingest", "test link accepted (ack)", "S2", ack.get("type") == "ack", e2e.short(ack, 80))
            ws.send_json({"type": "connection", "state": "connected", "transport": "browser-ble", "band": 7, "test": True})
            live = wait_for(sent, lambda m: m.get("type") == "event" and (m.get("event") or {}).get("kind") == "band_live", 0, 3)
            R.check("ingest", "test link labelled source 'sim'", "S2", live is not None and (live.get("event") or {}).get("source") == "sim",
                    "band_live source=%r" % ((live or {}).get("event") or {}).get("source"))

            t0 = time.time()
            ws.send_json({"type": "event", "kind": "free-fall"})
            ev = wait_for(sent, lambda m: m.get("type") == "event" and (m.get("event") or {}).get("kind") == "safety", t0)
            e = (ev or {}).get("event") or {}
            R.check("band events", "free-fall -> safety event", "S2", ev is not None,
                    e2e.short(e.get("title") or "no safety event within 4 s", 100))
            R.check("band events", "safety: a person must check (no auto action)", "S2",
                    "person" in low(e.get("detail")) and "check" in low(e.get("detail")), e2e.short(e.get("detail") or "", 100))
            sa = wait_for(sent, lambda m: m.get("type") == "assistant" and m.get("intent") == "safety_check", t0)
            R.check("band events", "free-fall -> assistant line", "S2", sa is not None, e2e.short((sa or {}).get("text") or "none", 100))

            t0 = time.time()
            ws.send_json({"type": "event", "kind": "double-tap"})
            n0 = wait_for(sent, lambda m: m.get("type") == "assistant" and m.get("intent") == "confirm_session", t0)
            R.check("band events", "double-tap, nothing pending -> 'nothing to confirm'", "S2",
                    n0 is not None and "nothing" in low(n0.get("text")), e2e.short((n0 or {}).get("text") or "none", 100))

            sess = S.store.insert("tool_sessions", "sim", worker_id="W-01", conveyor_id="CV-102", station_id=S.active_station,
                                  tool_id="impact_wrench", t_start=100.0, t_end=112.0, trigger_s=12.0, confidence=0.5,
                                  status="unresolved")
            t0 = time.time()
            ws.send_json({"type": "event", "kind": "double-tap"})
            c1 = wait_for(sent, lambda m: m.get("type") == "assistant" and m.get("intent") == "confirm_session"
                          and sess["id"] in str(m.get("text")), t0)
            row = S.store.get("tool_sessions", sess["id"]) or {}
            rec = S.store.query("SELECT * FROM exposure_records WHERE tool_session_id = ?", (sess["id"],))
            want = 2 * 5.0 ** 2 * 12.0 / 3600.0
            R.check("band events", "double-tap confirms the unresolved session", "S2",
                    row.get("status") == "confirmed" and c1 is not None,
                    "%s status=%r · %s" % (sess["id"], row.get("status"), e2e.short((c1 or {}).get("text") or "no assistant line", 80)))
            R.check("band events", "exposure record = 2*a^2*T (12 s @ 5 m/s2)", "S4",
                    len(rec) == 1 and abs(float(rec[0].get("points") or 0) - want) < 0.005,
                    "%d record(s), points=%s, expected %.4f" % (len(rec), rec[0].get("points") if rec else None, want))

    # ---- isolation proof: nothing in this process touched a real band ----------------------------
    blocked = getattr(S, "qa_blocked", [])
    R.check("isolation", "no band search or real band link (blocked, never started)", "-",
            not getattr(S, "probes", {}) and not any(k == "BandBridge" for k, _ in blocked),
            "probes=%d, search requests blocked=%d, real-bridge attempts=%d" % (
                len(getattr(S, "probes", {}) or {}), sum(1 for k, _ in blocked if k == "start_search"),
                sum(1 for k, _ in blocked if k == "BandBridge")))

    # ---- contract audit of everything this run broadcast + wrote -----------------------------
    bad = []
    for t, m in sent:
        if not isinstance(m.get("type"), str) or not e2e.num(m.get("ts")) or m.get("source") not in SOURCES:
            bad.append("%s source=%r" % (m.get("type"), m.get("source")))
        if m.get("type") == "event" and ((m.get("event") or {}).get("source") not in SOURCES):
            bad.append("event %s source=%r" % ((m.get("event") or {}).get("kind"), (m.get("event") or {}).get("source")))
    by = {}
    for _, m in sent:
        by[m.get("type")] = by.get(m.get("type"), 0) + 1
    info["broadcasts"] = by
    R.check("contract", "every broadcast: type, ts, source in contract set", "S2", sent and not bad,
            ("%d messages %s" % (len(sent), e2e.short(by, 90))) if not bad else "; ".join(bad[:4]))
    e2e.DB_PATH = db_copy
    e2e.check_db(R, info)

    # ---- UI: does the page index.html loads handle {"type":"assistant"}? ------------------------
    html = (FRONTEND / "index.html").read_text(encoding="utf-8", errors="replace")
    scripts = [s.split("?")[0] for s in re.findall(r'<script[^>]+src="([^"]+)"', html)]
    handled = []
    for s in scripts:
        p = FRONTEND / s
        if p.exists() and re.search(r"""case\s+['"]assistant['"]|type\s*===\s*['"]assistant['"]""", p.read_text(encoding="utf-8", errors="replace")):
            handled.append(s)
    R.check("ui", "live page handles the assistant line", "S8", bool(handled),
            ("handled in %s" % handled) if handled else "index.html loads %s; none handles type 'assistant'" % scripts,
            warn_only=True)

    if not args.keep:
        # release the copy before deleting it (Windows will not delete open files)
        try:
            if hasattr(S.samples, "stop"):
                S.samples.stop()
            else:
                S.samples._stop.set()
                S.samples._thread.join(timeout=3)
        except Exception:
            pass
        try:
            with S.store.lock:
                S.store.conn.close()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)
        info["db_copy_removed"] = not tmp.exists()
    return R, info


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="BeltGuard voice-driven shift check on a DB copy")
    ap.add_argument("--keep", action="store_true", help="keep the temp DB copy")
    ap.add_argument("--json", default=str(QA / "last_voice_run.json"))
    args = ap.parse_args()
    t0 = time.time()
    R, info = run(args)
    info["base"] = "in-process app on " + info.get("db_copy", "?")
    print_table(R, info, time.time() - t0)
    c = R.counts()
    try:
        Path(args.json).write_text(json.dumps({"info": info, "counts": c, "verdict": "PASS" if not c["FAIL"] else "FAIL",
                                               "rows": R.rows}, indent=2, default=str), encoding="utf-8")
    except OSError:
        pass
    return 1 if c["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
