#!/usr/bin/env python3
"""BeltGuard proactive-workflow check (S5 qa-verify). Runs on a COPY of the database, in-process, sim band.

    python beltguard/qa/proactive_e2e.py            # assertions + PASS/FAIL table
    python beltguard/qa/proactive_e2e.py --dump     # also print the job state after every step

S0's acceptance target (S4 job_agent.py + S2 wiring + S8 Live panel). The technician narrates a job on the band
mic (no wake word) and BeltGuard keeps the job checklist and drafts the paperwork itself:
  1  "Conveyor's isolated, lock's on."
  2  "Guard's off, the return idler at station three is seized."
  3  impact wrench checked out + ~10 s sim tool scene
  4  "New idler's in, guard back on."
  5  "Job done, testing the belt."
S0 16:15 SPOKEN STEPS design (job_agent.SPOKEN_STEPS = True): the job starts with ZERO steps; steps are created from
speech (verb + object, known names kept); a statement marks a step done, a request/question marks it in progress;
no fixed order; work before isolation -> safety_check; a repeat never moves a step back.
Asserts: zero steps at start; the spoken steps are created done, in spoken order, each with evidence; a step is in
progress while the band feels the tool; sign-off is awaiting verification (never done); no safety_check when isolation
came first; agent_actions include the WO draft, a parts-request DRAFT, the isolation note and "awaiting supervisor
verification" (nothing closed); every sentence reaches /ws as a transcript with `acted`; contract sources hold.
Phase 2 (fresh shift): "Guard's off." before isolation -> safety_check; "Can you grease the bearings?" -> in progress;
"Greased the bearings." -> done; the same request again leaves it done.
Isolation is the same as qa/voice_e2e.py (BELTGUARD_DB copy, no band search, real-band bridges refused).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

QA = Path(__file__).resolve().parent
sys.path.insert(0, str(QA))
import e2e  # noqa: E402
from voice_e2e import copy_db, load_isolated_app  # noqa: E402

Results, SOURCES = e2e.Results, e2e.SOURCES
SENTENCES = [
    "Conveyor's isolated, lock's on.",
    "Guard's off, the return idler at station three is seized.",
    None,                                   # tool scene goes here
    "New idler's in, guard back on.",
    "Job done, testing the belt.",
]


def low(x) -> str:
    return json.dumps(x, default=str).lower() if not isinstance(x, str) else x.lower()


def find_lists(state: dict) -> tuple[list, list, object]:
    """(checklist steps, agent actions, the job object) wherever S2 put them in the snapshot."""
    cands = [state]
    for k in ("job", "job_agent", "workflow", "live", "proactive"):
        if isinstance(state.get(k), dict):
            cands.insert(0, state[k])
    steps, actions, job = [], [], None
    for c in cands:
        for k in ("checklist", "steps", "job_steps"):
            v = c.get(k)
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v) and not steps:
                steps, job = v, c
        for k in ("agent_actions", "actions", "agent_log"):
            v = c.get(k)
            if isinstance(v, list) and all(isinstance(x, dict) for x in v) and not actions and v is not steps:
                actions = v
    return steps, actions, job


def step_title(s: dict) -> str:
    return str(s.get("title") or s.get("label") or s.get("name") or s.get("id") or "")


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
    info: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": "in-process, DB copy, sim band"}
    tmp = Path(tempfile.mkdtemp(prefix="bg-proactive-"))
    db_copy = copy_db(tmp)
    A, S, sent = load_isolated_app(db_copy, sim=True)
    has_agent = (Path(e2e.ROOT) / "backend" / "job_agent.py").exists()
    R.check("setup", "backend/job_agent.py present (S4)", "S4", has_agent, "present" if has_agent else "missing")
    from fastapi.testclient import TestClient

    timeline: list = []            # (label, steps, actions) after every move
    first_done: dict = {}          # step title -> (order index, evidence)
    saw_replace_in_progress = None
    tool_inprog: set = set()       # step titles in progress while the band feels the tool
    safety: dict = {}              # second phase (fresh shift): S0's other spoken-step rules
    with TestClient(A.app) as client:
        R.check("setup", "new shift on the copy", "S2", client.post("/api/reset").status_code == 200, "")

        def snap(label):
            st = client.get("/api/state").json()
            steps, actions, job = find_lists(st)
            timeline.append((label, steps, actions))
            for i, s in enumerate(steps):
                if str(s.get("status")).lower() in ("done", "complete", "completed") and step_title(s) not in first_done:
                    first_done[step_title(s)] = (len(first_done), s.get("evidence") or s.get("evidence_text") or s.get("by"))
            if args.dump:
                print("\n== %s" % label)
                print("   steps  :", [(step_title(s), s.get("status")) for s in steps])
                print("   actions:", [(a.get("kind") or a.get("type"), a.get("status"), str(a.get("title") or a.get("text") or "")[:60])
                                     for a in actions])
            return st, steps, actions

        snap("start")
        for idx, text in enumerate(SENTENCES):
            if text is None:                                  # the tool use, with the impact wrench checked out
                r = client.post("/api/tool/checkout", json={"tool_id": "impact_wrench", "worker_id": "W-01"})
                R.check("tool", "impact wrench checked out", "S2", r.status_code == 200, "HTTP %s" % r.status_code)
                time.sleep(3.0)          # checkout switches the sample rate (detector restarts); a real walk takes longer
                t0 = time.time()
                r = client.post("/api/sim/scene", json={"scene": "tool", "seconds": 12})
                R.check("tool", "sim tool scene 12 s", "S2", r.status_code == 200, "HTTP %s" % r.status_code)
                while time.time() - t0 < 12.5:
                    _, steps, _ = snap("tool scene +%.0fs" % (time.time() - t0)) if not args.dump else snap("tool scene")
                    for s in steps:
                        if str(s.get("status")).lower() in ("in_progress", "active", "doing"):
                            tool_inprog.add(step_title(s))
                            saw_replace_in_progress = saw_replace_in_progress or round(time.time() - t0, 1)
                    time.sleep(0.7)
                client.post("/api/sim/scene", json={"scene": "idle", "seconds": 15})
                stop = wait_for(sent, lambda m: m.get("type") == "event" and (m.get("event") or {}).get("kind") in
                                ("tool_stop", "tool_unresolved"), t0, 20)
                kinds = sorted({(m.get("event") or {}).get("kind") for tt, m in sent if tt >= t0 and m.get("type") == "event"})
                acts = sorted({m.get("state") for tt, m in sent if tt >= t0 and m.get("type") == "activity"})
                nmot = sum(1 for tt, m in sent if tt >= t0 and m.get("type") == "motion")
                detail = (((stop or {}).get("event", {}).get("title") if stop else "none within 20 s") +
                          " | events %s | activity %s | motion msgs %d" % (kinds, acts, nmot))
                if stop is not None and stop["event"]["kind"] == "tool_unresolved":   # sim noise: needs a confirm
                    R.add("tool", "tool session ended (confirmed by the detector)", "S3", "WARN", detail)
                else:
                    R.check("tool", "tool session ended (confirmed by the detector)", "S3", stop is not None, detail)
                snap("after tool")
                continue
            t0 = time.time()
            r = client.post("/api/report", json={"text": text, "source": "band-mic"})
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text}
            area = "say %d: %s" % (idx + 1, text[:26])
            R.check(area, "HTTP 200", "S2", r.status_code == 200, "status=%r intent=%r" % (body.get("status"), body.get("intent")))
            tr = wait_for(sent, lambda m: m.get("type") == "transcript" and m.get("text") == text, t0, 3)
            acted = wait_for(sent, lambda m: m.get("type") == "transcript" and m.get("text") == text and
                             (m.get("acted") or m.get("chip") == "acted" or m.get("status") == "acted"), t0, 4)
            R.check(area, "/ws transcript with an 'acted' flag", "S2", tr is not None and acted is not None,
                    "transcript=%s acted=%s" % (bool(tr), e2e.short({k: acted.get(k) for k in ("acted", "chip", "status")}, 60)
                                                if acted else None))
            snap("after say %d" % (idx + 1))

        final, steps, actions = snap("end")

        # ---- phase 2 (fresh shift): S0 16:15 spoken-steps rules --------------------------------------------
        def status_of(title_part, st):
            s = next((x for x in st if title_part in step_title(x).lower()), None)
            return s.get("status") if s else None
        client.post("/api/reset")
        _, st, _ = snap("phase 2: after reset")
        safety["reset_steps"] = len(st)
        n_act = len((client.get("/api/state").json().get("agent_actions")) or [])
        client.post("/api/report", json={"text": "Guard's off.", "source": "band-mic"})
        _, st, ac = snap("phase 2: guard off before isolation")
        safety["safety_check"] = next((a for a in ac[: max(0, len(ac) - n_act)] or ac
                                       if (a.get("kind") == "safety_check" and "isolat" in low(a))), None)
        client.post("/api/report", json={"text": "Can you grease the bearings?", "source": "band-mic"})
        _, st, _ = snap("phase 2: request")
        safety["request"] = status_of("grease", st)
        client.post("/api/report", json={"text": "Greased the bearings.", "source": "band-mic"})
        _, st, _ = snap("phase 2: statement")
        safety["statement"] = status_of("grease", st)
        client.post("/api/report", json={"text": "Can you grease the bearings?", "source": "band-mic"})
        _, st, _ = snap("phase 2: repeat request")
        safety["repeat"] = status_of("grease", st)
        safety["titles"] = [step_title(s) for s in st]

    # ---- checklist: S0 16:15 SPOKEN STEPS (zero steps at start; created from speech; tense sets status) ----------
    def find(part):
        return next((s for s in steps if part in step_title(s).lower()), None)
    start_steps = timeline[0][1] if timeline else []
    R.check("checklist", "job starts with zero steps", "S0", len(start_steps) == 0, "%d steps at start" % len(start_steps))
    want_done = [("isolate", "Isolate & lock out"), ("remove guard", "Remove guard"), ("replace idler", "Replace idler"),
                 ("refit guard", "Refit guard"), ("test run", "Remove isolation & test run")]
    for part, label in want_done:
        s = find(part)
        R.check("checklist", "step '%s' created + done (statement)" % label, "S0", s is not None and str(s.get("status")) == "done",
                repr((step_title(s), s.get("status")) if s else "not created"))
    so = find("sign")
    R.check("checklist", "sign-off: awaiting verification, never done", "S0",
            so is not None and str(so.get("status")) == "in_progress" and "verif" in low(so.get("evidence") or ""),
            repr((step_title(so), so.get("status"), e2e.short(so.get("evidence") or "", 60)) if so else "not created"))
    idx = {part: next((i for i, s in enumerate(steps) if part in step_title(s).lower()), None) for part, _ in want_done[:4]}
    order_ok = all(idx[p] is not None for p in idx) and [idx[p] for p, _ in want_done[:4]] == sorted(idx.values())
    R.check("checklist", "steps appear in the order they were spoken", "S0", order_ok, "positions %s" % idx)
    no_ev = [step_title(s) for s in steps if str(s.get("status")) == "done" and not (s.get("evidence"))]
    R.check("checklist", "every done step carries evidence", "S0", steps and not no_ev,
            "missing evidence: %s" % no_ev if no_ev else "%d done with evidence" % sum(1 for s in steps if s.get("status") == "done"))
    R.check("checklist", "a step is in progress while the band feels the tool", "S0", bool(tool_inprog),
            "in progress during the tool scene: %s" % sorted(tool_inprog) if tool_inprog else "none seen")
    lingering = [step_title(s) for s in steps if str(s.get("status")) == "in_progress" and "sign" not in step_title(s).lower()]
    R.check("checklist", "no step left 'in progress' after 'Job done' (WARN only)", "S0", not lingering,
            "still in progress: %s" % lingering if lingering else "none", warn_only=True)
    sc_main = [a for a in actions if a.get("kind") == "safety_check"]
    R.check("checklist", "no safety_check when isolation came first", "S0", not sc_main, "%d safety_check(s)" % len(sc_main))
    # phase 2
    R.check("rules", "reset starts a new job with zero steps", "S0", safety.get("reset_steps") == 0, "%s steps" % safety.get("reset_steps"))
    R.check("rules", "work before isolation -> safety_check", "S0", safety.get("safety_check") is not None,
            e2e.short((safety.get("safety_check") or {}).get("text") or "none", 100))
    R.check("rules", "request 'Can you grease the bearings?' -> in progress", "S0", safety.get("request") == "in_progress",
            "status=%r titles=%s" % (safety.get("request"), safety.get("titles")))
    R.check("rules", "statement 'Greased the bearings.' -> done", "S0", safety.get("statement") == "done", "status=%r" % safety.get("statement"))
    R.check("rules", "repeat request never moves a step back", "S0", safety.get("repeat") == "done", "status=%r" % safety.get("repeat"))

    # ---- agent actions ---------------------------------------------------------------------------------
    blob = [low(a) for a in actions]
    want = {
        "WO draft": lambda t: ("work order" in t or "wo-" in t or "work_order" in t) and "draft" in t,
        "parts request DRAFT": lambda t: "part" in t and "draft" in t,
        "isolation logged": lambda t: "isolat" in t and ("logged" in t or "noted" in t or "log" in t),
        "awaiting supervisor verification": lambda t: "awaiting" in t and "verif" in t,
    }
    for name, pred in want.items():
        hit = next((a for a, t in zip(actions, blob) if pred(t)), None)
        R.check("agent", "agent action: %s" % name, "S4", hit is not None, e2e.short(hit, 110) if hit else "not found")
    closed = [a for a, t in zip(actions, blob) if re.search(r'"status":\s*"(closed|complete|completed|verified)"', t)]
    wos = [w for w in final.get("work_orders") or []]
    R.check("honesty", "nothing closed by the agent (WO never closed)", "S4",
            not closed and all(str(w.get("status")).lower() not in ("closed", "complete", "completed", "verified") for w in wos),
            "WO statuses %s" % [(w.get("id"), w.get("status")) for w in wos][-3:])

    # ---- contract -------------------------------------------------------------------------------------------
    bad = [m.get("type") for _, m in sent if m.get("source") not in SOURCES or not e2e.num(m.get("ts"))]
    R.check("contract", "every broadcast: ts + source in contract set", "S2", not bad, "bad: %s" % bad[:5] if bad else "%d ok" % len(sent))
    e2e.DB_PATH = db_copy
    e2e.check_db(R, info)
    blocked = getattr(S, "qa_blocked", [])
    R.check("isolation", "no band search or real band link", "-", not getattr(S, "probes", {}) and
            not any(k == "BandBridge" for k, _ in blocked), "probes=%d blocked=%d" % (len(getattr(S, "probes", {}) or {}), len(blocked)))
    info["timeline"] = [(l, [(step_title(s), s.get("status")) for s in st], len(ac)) for l, st, ac in timeline]
    try:
        S.samples.stop()
        with S.store.lock:
            S.store.conn.close()
    except Exception:
        pass
    if not args.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    return R, info


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="BeltGuard proactive-workflow check on a DB copy")
    ap.add_argument("--dump", action="store_true", help="print the job state after every step")
    ap.add_argument("--keep", action="store_true", help="keep the temp DB copy")
    ap.add_argument("--json", default=str(QA / "last_proactive_run.json"))
    args = ap.parse_args()
    t0 = time.time()
    R, info = run(args)
    info["base"] = "in-process app on a DB copy (sim band)"
    e2e.print_table(R, info, time.time() - t0)
    c = R.counts()
    try:
        Path(args.json).write_text(json.dumps({"info": info, "counts": c, "rows": R.rows}, indent=2, default=str), encoding="utf-8")
    except OSError:
        pass
    return 1 if c["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
