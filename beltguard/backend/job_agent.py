"""BeltGuard job agent: proactive intelligence in the background. Owner: S4 hav-brain.

The technician works and talks; the band times the tools. The agent keeps the job checklist current
("this is done, this is left") and drafts the follow-up work, without being asked:

    agent = JobAgent()
    changes = agent.on_transcript(text, state, report=None)   # every final band-mic sentence (no wake word needed)
    changes = agent.on_tool_session(session, state)           # every tool-session update (active / confirmed / ...)
    changes = agent.on_state(state)                           # optional, e.g. each snapshot: exposure thresholds
    agent.snapshot() -> {"job": {...}, "agent_actions": [...]}

changes = {"job": <job dict or None>, "steps": [ids changed], "actions": [new actions], "say": "<short sentence>" | None}

Rules it keeps:
- A step becomes "done" only with evidence: a strict job phrase heard ("heard 15:31: 'conveyor's isolated'") or the band
  ("band: impact wrench 0:42 trigger"). Chat never matches; nothing is ticked off on a guess.
- "Job done" marks the job awaiting supervisor verification. The agent NEVER closes work, never verifies isolation,
  never signs off: those steps stay with an authorized person / the supervisor.
- Every action it takes is marked done (it did it), draft (prepared for a person) or needs_review.
"""
from __future__ import annotations

import re
import time
from typing import Callable, Optional

try:  # works as backend.job_agent and as a top-level module with backend/ on sys.path
    from . import exposure, inspection  # type: ignore
except ImportError:
    import exposure  # type: ignore
    import inspection  # type: ignore

STEPS = [
    ("isolate", "Isolate & lock out CV-102"),
    ("guard_off", "Remove guard"),
    ("replace", "Replace idler"),
    ("guard_on", "Refit guard"),
    ("test_run", "Remove isolation & test run"),
    ("signoff", "Supervisor sign-off"),
]

NOTIFY_POINTS = 80          # 80 % of the 100-point action value


def _rx(pattern: str) -> re.Pattern:
    return re.compile(r"(?<![a-z'])(?:" + pattern + r")(?![a-z])")


# Strict job phrases, matched on lower-case text that keeps , . ; ! ? as clause breaks.
_NEGATED = _rx(r"not|isn'?t|aren'?t|wasn'?t|haven'?t|hasn'?t|never|before|until|need(?:s)?|need to|to be|should|has to|"
               r"must|going to|gonna|will|can'?t|cannot|don'?t")
_QUESTION = re.compile(r"^(?:is|are|has|have|did|do|does|can|could|should|will|was|were|what|where|when|why|how|who|any)\b|"
                       r"\byet$")
P = {
    "job_done": _rx(r"(?:job|work|repair|task)(?:'s| is| has been| was)? (?:done|complete|completed|finished)|all done|"
                    r"finished the (?:job|repair|work|task)|that'?s the job done"),
    "test_run": _rx(r"isolation (?:is )?(?:off|removed|lifted)|removed (?:the )?isolation|de ?isolated|de-isolated|"
                    r"locks? (?:are |is )?(?:off|removed)|test run|test-run|testing the (?:belt|conveyor)|running a test|"
                    r"(?:belt|conveyor)'?s? (?:is )?running (?:again|fine|ok|okay)"),
    "guard_on": _rx(r"guard(?:'s| is| has been)? (?:back on|back in place|refitted|fitted|reinstalled|back)|"
                    r"(?:refitted|reinstalled) the guard|(?:put|putting|got) the guard back(?: on)?|guard back on"),
    "replace_done": _rx(r"(?:idler|roller)s?(?:'s| is| has been| now)? (?:replaced|changed|swapped|fitted|installed|done)|"
                        r"(?:idler|roller)(?:'s| is| has been) in(?=[,.;!?]|$| now| place)|"
                        r"(?:replaced|changed|swapped|fitted|installed|put in) the (?:return |new |old |replacement )?(?:idler|roller)|"
                        r"new (?:idler|roller|one)(?:'s| is)? (?:fitted|installed|done)|"
                        r"new (?:idler|roller|one)(?:'s| is)? in(?=[,.;!?]|$| now| place)"),
    "replace_start": _rx(r"(?:replacing|changing|swapping|changing out|fitting) the (?:return |new |old )?(?:idler|roller)|"
                         r"starting (?:on )?the (?:idler|roller)|(?:idler|roller)(?:'s| is) coming out|"
                         r"pulling the (?:old )?(?:idler|roller)"),
    "guard_off": _rx(r"guard(?:'s| is| has been)? (?:off|removed|out)|removed the guard|(?:taking|took|take|got) the guard off|"
                     r"guard (?:is )?coming off"),
    "isolate": _rx(r"isolated|isolation (?:is )?(?:on|done|in place|complete|applied)|locked out|lock(?:ed)?-out|lockout (?:is )?"
                   r"(?:on|done|applied)|tagged out|locks? (?:are |is )?on|locked and tagged"),
}
KEYS = ("job_done", "test_run", "guard_on", "replace_done", "replace_start", "guard_off", "isolate")
SAY_DONE = {"isolate": "Isolation logged; an authorized person must verify it.", "guard_off": "Guard off noted.",
            "replace": "Idler replacement noted.", "guard_on": "Guard refitted noted.", "test_run": "Test run noted."}

PARTS = [  # component (from inspection) -> what stores should pick
    ("return idler", "return idler 152 mm × 1 from stores"), ("carry idler", "carry idler set 152 mm × 1 from stores"),
    ("impact idler", "impact idler 152 mm × 1 from stores"), ("training idler", "training idler 152 mm × 1 from stores"),
    ("idler bearing", "idler bearing × 2 from stores"), ("idler", "idler 152 mm × 1 from stores"),
    ("belt splice", "mechanical belt splice kit × 1 from stores"), ("pulley lagging", "pulley lagging kit × 1 from stores"),
    ("skirting", "skirt rubber 3 m from stores"), ("belt cleaner", "belt cleaner blade × 1 from stores"),
    ("guard", "guard panel × 1 from stores"), ("bearing", "bearing × 1 from stores"),
]


def _clean(text: str) -> str:
    _woke, rest = inspection.strip_wake_word(text or "")
    t = rest.lower().replace("’", "'").replace("-", " ")
    t = re.sub(r"[^a-z0-9',.;!? ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _clauses(t: str) -> list[str]:
    return [c.strip() for c in re.split(r"[,.;!?]| and | but | then ", t) if c.strip()]


def _hhmm(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def _mmss(seconds) -> str:
    s = int(round(float(seconds or 0)))
    return "%d:%02d" % (s // 60, s % 60)


class JobAgent:
    def __init__(self, conveyor_id: str = "CV-102", clock: Callable[[], float] = time.time):
        self.conveyor_id = conveyor_id
        self.clock = clock
        self.job: Optional[dict] = None
        self.actions: list[dict] = []
        self._job_n = 1000
        self._pr_n = 2040
        self._act_n = 0
        self._notified: set = set()

    # ------------------------------------------------------------ public API
    def on_transcript(self, text: str, state: Optional[dict] = None, report: Optional[dict] = None) -> dict:
        state = state or {}
        now = self.clock()
        changed, actions = [], []
        t = _clean(text)
        if not t:
            return self._changes([], [], None)
        self._ensure_job(state)
        quote = (text or "").strip()
        said = None

        matched, leftover = [], []
        for clause in _clauses(t):
            hit = False
            if not (_NEGATED.search(clause) or _QUESTION.search(clause)):   # "not isolated", "is the guard off yet"
                for key in KEYS:
                    if P[key].search(clause):
                        hit = True
                        if key not in matched:
                            matched.append(key)
            if not hit:
                leftover.append(clause)

        # a defect heard: draft the work order (if the backend has not) and a parts request. Only the clauses that
        # were not job progress count ("guard's off, the return idler at station three is seized" has both), and only
        # real problem words (seized, worn, ...): "replacing the idler now" is progress, not a new defect.
        defect_text = " ".join(leftover)
        rules = inspection._rules_extract(defect_text) if defect_text else {}
        if (defect_text and inspection.is_inspection_report(defect_text, allow_trigger=False)
                and rules.get("condition") not in (None, "needs replacement", "needs repair")):
            actions += self._defect_actions(defect_text, state, report, now)
        evidence = "heard %s: “%s”" % (_hhmm(now), quote[:80])
        for key in matched:
            if key == "job_done":
                actions += self._job_done(now, evidence)
                changed.append("signoff")
                said = self._summary_sentence(prefix="Marked complete; awaiting supervisor verification. ")
                continue
            step_id = "replace" if key.startswith("replace") else key
            status = "in_progress" if key == "replace_start" else "done"
            if self._set_step(step_id, status, evidence, now, "voice"):
                changed.append(step_id)
                if key == "isolate":
                    actions.append(self._action("isolation", "Isolation logged; an authorized person must verify it.",
                                                "needs_review", now))
                if status == "done":
                    said = said or self._next_sentence(SAY_DONE.get(step_id, "Noted."))
                else:
                    said = said or "Idler replacement started; the band is timing it."
        actions += self._exposure_actions(state, now)
        return self._changes(changed, actions, said)

    def on_tool_session(self, session: dict, state: Optional[dict] = None) -> dict:
        state = state or {}
        now = self.clock()
        self._ensure_job(state)
        changed, actions = [], []
        session = session or {}
        station = session.get("station_id")
        if self.job and (not self.job.get("station_id") or not station or station == self.job["station_id"]):
            tool = exposure.get_tool(session.get("tool_id"), state.get("tools") or exposure.load_tool_profiles())
            name = (tool or {}).get("name") or session.get("tool_id") or "tool"
            name = re.sub(r"\s*\d+/\d+\"? drive", "", name).lower()
            status = session.get("status")
            step = self._step("replace")
            if status == "active" and step["status"] != "done":
                if self._set_step("replace", "in_progress", "band: %s running" % name, now, "band"):
                    changed.append("replace")
            elif status == "confirmed":
                pts = session.get("points")
                ev = "band: %s %s trigger%s (confirmed)" % (name, _mmss(session.get("trigger_s")),
                                                             ", %.1f pts" % float(pts) if pts is not None else "")
                if self._set_step("replace", step["status"] if step["status"] == "done" else "in_progress", ev, now, "band"):
                    changed.append("replace")
        actions += self._exposure_actions(state, now)
        return self._changes(changed, actions, None)

    def on_state(self, state: Optional[dict] = None) -> dict:
        state = state or {}
        self._ensure_job(state)
        return self._changes([], self._exposure_actions(state, self.clock()), None)

    def supervisor_sign_off(self, supervisor: str) -> dict:
        """Only a person signs off. Called from a supervisor's button, never by the agent itself."""
        now = self.clock()
        if not self.job:
            return self._changes([], [], None)
        self._set_step("signoff", "done", "signed off by %s at %s" % (supervisor, _hhmm(now)), now, "user")
        self.job["status"] = "verified"
        return self._changes(["signoff"], [self._action("signoff", "Signed off by %s." % supervisor, "done", now)], None)

    def snapshot(self) -> dict:
        job = dict(self.job) if self.job else None
        if job:
            job["steps"] = [dict(s) for s in job["steps"]]
            job.update(self._progress())
        return {"job": job, "agent_actions": [dict(a) for a in self.actions[:50]]}

    # ------------------------------------------------------------ job
    def _ensure_job(self, state: dict) -> None:
        wo = self._active_work_order(state)
        if self.job and wo and self.job.get("work_order_id") != wo.get("id") and self.job.get("status") != "awaiting_verification":
            if not self.job.get("work_order_id"):          # the default job now has a real work order behind it
                self.job.update({"work_order_id": wo["id"], "title": self._title(wo, state), "station_id": wo.get("station_id")})
            return
        if self.job:
            return
        self._job_n += 1
        station = (wo or {}).get("station_id") or state.get("active_station") or "S3"
        self.job = {"id": "JOB-%d" % self._job_n, "work_order_id": (wo or {}).get("id"),
                    "title": self._title(wo, state, station), "station_id": station, "conveyor_id": self.conveyor_id,
                    "status": "open", "opened_ts": self.clock(),
                    "steps": [{"id": sid, "title": title, "status": "left", "evidence": None, "ts": None, "source": None}
                              for sid, title in STEPS]}

    @staticmethod
    def _active_work_order(state: dict) -> Optional[dict]:
        wos = [w for w in (state.get("work_orders") or state.get("open_work_orders") or [])
               if w and w.get("status") not in ("closed", "awaiting_verification", "verified")]
        if not wos:
            return None
        here = [w for w in wos if w.get("station_id") == state.get("active_station")]
        return max(here or wos, key=lambda w: float(w.get("ts") or 0))

    @staticmethod
    def _station_name(state: dict, station_id: str) -> str:
        st = next((s for s in (state.get("stations") or inspection.DEFAULT_STATIONS) if s.get("id") == station_id), None)
        return "%s %s" % (station_id, st["name"]) if st else str(station_id)

    def _title(self, wo: Optional[dict], state: dict, station: Optional[str] = None) -> str:
        if wo and wo.get("title"):
            return str(wo["title"]).split(" — ")[0]
        return "Replace return idler at %s" % self._station_name(state, station or "S3")

    def _step(self, step_id: str) -> dict:
        return next(s for s in self.job["steps"] if s["id"] == step_id)

    def _set_step(self, step_id: str, status: str, evidence: str, ts: float, source: str) -> bool:
        if not self.job:
            return False
        step = self._step(step_id)
        if step["status"] == status and step["evidence"] == evidence:
            return False
        if step["status"] == "done" and status != "done":
            return False                                        # never un-tick a step on weaker evidence
        step.update({"status": status, "evidence": evidence, "ts": ts, "source": source})
        return True

    def _progress(self) -> dict:
        steps = self.job["steps"]
        done = [s for s in steps if s["status"] == "done"]
        left = [s for s in steps if s["status"] != "done"]
        return {"progress": {"done": len(done), "total": len(steps)}, "summary": self._summary(done, left)}

    @staticmethod
    def _summary(done: list, left: list) -> str:
        if not left:
            return "All %d steps done." % len(done)
        return "%d done, %d left: %s." % (len(done), len(left), ", ".join(s["title"] for s in left))

    def _summary_sentence(self, prefix: str = "") -> str:
        return prefix + self._progress()["summary"]

    def _next_sentence(self, head: str) -> str:
        nxt = next((s for s in self.job["steps"] if s["status"] != "done"), None) if self.job else None
        return head + (" Next: %s." % (nxt["title"][:1].lower() + nxt["title"][1:]) if nxt else "")

    def _job_done(self, now: float, evidence: str) -> list:
        if not self.job or self.job["status"] in ("awaiting_verification", "verified"):
            return []
        self.job["status"] = "awaiting_verification"
        self._set_step("signoff", "in_progress", "awaiting supervisor verification (%s)" % evidence, now, "voice")
        missing = [s["title"] for s in self.job["steps"] if s["id"] != "signoff" and s["status"] != "done"]
        out = [self._action("job_done", "Marked complete; awaiting supervisor verification. BeltGuard never closes work.",
                            "needs_review", now)]
        if missing:
            out.append(self._action("missing_evidence", "No evidence yet for: %s. The supervisor should check these."
                                    % ", ".join(missing), "needs_review", now))
        return out

    # ------------------------------------------------------------ proactive actions
    def _action(self, kind: str, text: str, status: str, ts: float, **extra) -> dict:
        self._act_n += 1
        a = {"id": "ACT-%d" % self._act_n, "ts": ts, "kind": kind, "text": text, "status": status}
        a.update({k: v for k, v in extra.items() if v is not None})
        self.actions.insert(0, a)
        return a

    def _defect_actions(self, t: str, state: dict, report: Optional[dict], now: float) -> list:
        out = []
        wo = (report or {}).get("work_order") or {}
        defect = (report or {}).get("defect") or {}
        if not defect:
            found = inspection.extract_defect(t, state.get("stations") or inspection.DEFAULT_STATIONS,
                                              state.get("conveyor") or {"id": self.conveyor_id}, state.get("worker") or {},
                                              active_station_id=state.get("active_station"), force_rules=True, source="typed")
            if found.get("status") == "ok":
                defect = {"component": found.get("component"), "condition": found.get("condition"),
                          "station_id": found.get("station_id")}
        if not defect.get("component"):
            return out
        comp = str(defect.get("component")).lower()
        where = defect.get("station_id") or state.get("active_station") or ""
        if wo.get("id"):
            out.append(self._action("work_order", "Work order %s drafted: %s %s at %s." % (wo["id"], defect.get("condition") or "",
                                    comp, where), "draft", now, work_order_id=wo["id"]))
        else:
            out.append(self._action("work_order", "Work order draft prepared: %s %s at %s." % (defect.get("condition") or "", comp,
                                    where), "draft", now))
        part = next((p for key, p in PARTS if key in comp), "%s × 1 from stores" % comp)
        self._pr_n += 1
        out.append(self._action("parts_request", "PR-%d: %s (draft, for stores to confirm)." % (self._pr_n, part), "draft", now,
                                parts_request_id="PR-%d" % self._pr_n))
        return out

    def _exposure_actions(self, state: dict, now: float) -> list:
        totals = []
        for source in (state.get("exposure"), state.get("verdict")):
            try:
                totals.append(float((source or {}).get("total_points") or 0))
            except (TypeError, ValueError):
                pass
        total = max(totals or [0.0])
        out = []
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if total >= NOTIFY_POINTS and ("notify", day) not in self._notified:
            self._notified.add(("notify", day))
            out.append(self._action("exposure", "Supervisor notified: %.0f of 100 points today; rotation suggested." % total,
                                    "done", now))
        if total >= exposure.ACTION_POINTS and ("action", day) not in self._notified:
            self._notified.add(("action", day))
            out.append(self._action("exposure", "Action value reached (%.0f points): supervisor review required." % total,
                                    "needs_review", now))
        return out

    def _changes(self, steps: list, actions: list, say: Optional[str]) -> dict:
        snap = self.snapshot()
        return {"job": snap["job"] if (steps or actions) else None, "steps": steps, "actions": actions, "say": say}
