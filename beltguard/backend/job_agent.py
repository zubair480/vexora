"""BeltGuard job agent: proactive intelligence in the background. Owner: S4 hav-brain.

The technician works and talks; the band times the tools. The agent keeps the job checklist current
("this is done, this is left") and drafts the follow-up work, without being asked:

    agent = JobAgent()
    changes = agent.on_transcript(text, state, report=None, transcript_id=None)   # every final band-mic sentence
    changes = agent.on_tool_session(session, state)           # every tool-session update (active / confirmed / ...)
    changes = agent.on_state(state)                           # optional, e.g. each snapshot: exposure thresholds
    agent.snapshot() -> {"job": {...}, "agent_actions": [...]}

changes = {"job": <job dict or None>, "steps": [ids changed], "actions": [new actions], "say": "<short sentence>" | None}

Ticking is strict (an always-on mic hears chat, rants and people reading the screen aloud):
- ORDER: a step ticks only when every earlier step is done. An out-of-order phrase never ticks; it becomes a
  needs_review action ("Heard 'guard's back on' but 'Replace idler' isn't done yet: confirm?").
- CLAUSES: a job phrase counts only in a short clause (14 words or fewer) that is not a question or a negation.
  Saying a step's name is the instruction: "Isolate and lock out", "Remove the guard", "Refit the guard" tick it;
  a request ("Can you remove the guard?") marks it in progress. Sentences about the screen, dashboard, Claude,
  GitHub, code, sessions or transcripts never tick anything. A job phrase inside a long clause or a long utterance
  is ambiguous: needs_review ("Heard 'isolated': confirm?"), never a tick. Repeating a done step is a silent no-op.
- EVIDENCE: every tick records it ("heard 15:31: 'conveyor's isolated'" / "band: impact wrench 0:42 trigger").
- "Job done" marks the job awaiting supervisor verification. The agent NEVER closes work, never verifies
  isolation, never signs off. Every action is done (it did it), draft (for a person) or needs_review.
- A self-audit un-ticks any step that breaks these rules (e.g. ticked by an older version), with a needs_review note.
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
ORDER = [sid for sid, _title in STEPS]
TITLES = dict(STEPS)
# SPOKEN STEPS (S0, 16:15, the user's design): the job starts with NO steps; every step is created from what the
# technician says, as they say it (verb + object), and tense sets its status. The old strict fixed-checklist path
# (_on_transcript / ORDER / _audit) stays below, unused while this is True.
SPOKEN_STEPS = True
HINTS = {"isolate": "Say: “Conveyor's isolated and locked out”", "guard_off": "Say: “Guard's off”",
         "replace": "Say: “New idler fitted”", "guard_on": "Say: “Guard's back on”", "test_run": "Say: “Test run good”",
         "signoff": "Say: “Job done”, then the supervisor signs off"}
SHORT_STATION = {"S1": "tail pulley", "S2": "carry idlers", "S3": "return idlers", "S4": "take-up", "S5": "head pulley"}

NOTIFY_POINTS = 80          # 80 % of the 100-point action value
MAX_CLAUSE_WORDS = 14
MAX_UTTERANCE_WORDS = 25    # a long utterance is conversation: a job phrase inside it asks, never ticks


def _rx(pattern: str) -> re.Pattern:
    return re.compile(r"(?<![a-z'])(?:" + pattern + r")(?![a-z])")


_NEGATED = _rx(r"not|isn'?t|aren'?t|wasn'?t|haven'?t|hasn'?t|never|before|until|need(?:s)?|need to|to be|should|has to|"
               r"must|going to|gonna|will|can'?t|cannot|don'?t")
_TRUE_NEG = _rx(r"not|isn'?t|aren'?t|wasn'?t|haven'?t|hasn'?t|never|don'?t|can'?t|cannot")
_REQUEST = re.compile(r"^(?:hey |ok |okay )?(?:can|could|would|will) (?:you|we|someone|somebody)(?![a-z])|^(?:please|let'?s)(?![a-z])")
_QUESTION = re.compile(r"^(?:is|are|has|have|did|do|does|can|could|should|will|was|were|what|where|when|why|how|who|any)\b|"
                       r"\byet$")
# talk about the software, the screen or the session: never evidence for a job step
_META = re.compile(r"(?<![a-z])(?:screen|dashboard|claude|github|git|session|sessions|transcript|transcripts|code|commit|"
                   r"push|pushed|deploy|deployed|laptop|slides?|demo|api|website|browser|tab|terminal|prompt|chatgpt|"
                   r"openai|app)(?![a-z])")


def _label_norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower().replace("&", " and ")).strip()


# reading the checklist aloud is not doing the work
_LABELS = sorted({_label_norm(title) for _sid, title in STEPS} |
                 {"remove isolation and test", "remove isolation", "isolate and lock out"}, key=len, reverse=True)

P = {
    "job_done": _rx(r"(?:job|work|repair|task)(?:'s| is| has been| was)? (?:done|complete|completed|finished)|all done|"
                    r"finished the (?:job|repair|work|task)|that'?s the job done"),
    "test_run": _rx(r"isolation (?:is )?(?:off|removed|lifted)|remov(?:e|ed|ing) (?:the )?isolation|remove the locks?|"
                    r"de ?isolated|de-isolated|"
                    r"locks? (?:are |is )?(?:off|removed)|test run|test-run|testing the (?:belt|conveyor)|running a test|"
                    r"(?:belt|conveyor)'?s? (?:is )?running (?:again|fine|ok|okay)"),
    "guard_on": _rx(r"refit(?:ting)? (?:the )?guard|reinstall(?:ing)? (?:the )?guard|put (?:the )?guard back|"
                    r"guard(?:'s| is| has been)? (?:back on|back in place|refitted|fitted|reinstalled|back)|"
                    r"(?:refitted|reinstalled) the guard|(?:put|putting|got) the guard back(?: on)?|guard back on"),
    "replace_done": _rx(r"replace (?:the )?(?:return |old )?(?:idler|roller)|"
                        r"(?:idler|roller)s?(?:'s| is| has been| now)? (?:replaced|changed|swapped|fitted|installed|done)|"
                        r"(?:idler|roller)(?:'s| is| has been) in(?=[,.;!?]|$| now| place)|"
                        r"(?:replaced|changed|swapped|fitted|installed|put in) the (?:return |new |old |replacement )?(?:idler|roller)|"
                        r"new (?:idler|roller|one)(?:'s| is)? (?:fitted|installed|done)|"
                        r"new (?:idler|roller|one)(?:'s| is)? in(?=[,.;!?]|$| now| place)"),
    "replace_start": _rx(r"(?:replacing|changing|swapping|changing out|fitting) the (?:return |new |old )?(?:idler|roller)|"
                         r"starting (?:on )?the (?:idler|roller)|(?:idler|roller)(?:'s| is) coming out|"
                         r"pulling the (?:old )?(?:idler|roller)|(?:old )?(?:idler|roller)(?:'s| is)? out(?=[,.;!?]|$| now)"),
    "guard_off": _rx(r"remov(?:e|ing) (?:the )?guard|take (?:the )?guard off|"
                     r"guard(?:'s| is| has been)? (?:off|removed|out)|removed the guard|(?:taking|took|take|got) the guard off|"
                     r"guard (?:is )?coming off"),
    "isolate": _rx(r"isolate|isolating|lock(?:ing)? (?:it |the conveyor |the belt )?out|"
                   r"isolated|isolation (?:is )?(?:on|done|in place|complete|applied)|locked out|locked it out|lock(?:ed)?-out|"
                   r"lockout(?: (?:is )?(?:on|done|applied|in place))?|loto(?: (?:is )?(?:on|done|applied|in place))?|"
                   r"tagged out|locks? (?:are |is )?on|locked and tagged"),
    "tool_use": _rx(r"(?:using|running|started|starting) the (?:hammer(?: drill)?|drill|impact(?: wrench)?|wrench|rattle gun|"
                    r"(?:angle )?grinder|breaker|power jack|jack|tool)"),
    "stopped": _rx(r"(?:conveyor|belt)(?:'s| is| has)? (?:stopped|been stopped|down|off)|stopped the (?:conveyor|belt)"),
}
KEYS = ("job_done", "test_run", "guard_on", "replace_done", "replace_start", "tool_use", "guard_off", "isolate", "stopped")
STEP_OF = {"test_run": "test_run", "guard_on": "guard_on", "replace_done": "replace", "replace_start": "replace",
           "tool_use": "replace", "guard_off": "guard_off", "isolate": "isolate"}
SAY_DONE = {"isolate": "Isolation logged; an authorized person must verify it.", "guard_off": "Guard off noted.",
            "replace": "Idler replacement noted.", "guard_on": "Guard refitted noted.", "test_run": "Test run noted."}
DETAIL = {"isolation": "Needs review · an authorized person verifies isolation",
          "work_order": "Draft · the supervisor reviews it", "parts_request": "Draft · stores confirm it",
          "exposure": "Posted to the supervisor", "job_done": "Needs review · the supervisor verifies and closes",
          "missing_evidence": "Needs review · the supervisor checks these steps",
          "safety_check": "Needs review · check before carrying on", "confirm_step": "Needs review · not ticked",
          "untick": "Needs review · un-ticked by the strict rules", "signoff": "Done by the supervisor"}
DETAIL_BY_STATUS = {"done": "Done by BeltGuard", "draft": "Draft · for a person to confirm", "needs_review": "Needs review"}

PARTS = [  # component (from inspection) -> what stores should pick
    ("return idler", "return idler 152 mm × 1 from stores"), ("carry idler", "carry idler set 152 mm × 1 from stores"),
    ("impact idler", "impact idler 152 mm × 1 from stores"), ("training idler", "training idler 152 mm × 1 from stores"),
    ("idler bearing", "idler bearing × 2 from stores"), ("idler", "idler 152 mm × 1 from stores"),
    ("belt splice", "mechanical belt splice kit × 1 from stores"), ("pulley lagging", "pulley lagging kit × 1 from stores"),
    ("skirting", "skirt rubber 3 m from stores"), ("belt cleaner", "belt cleaner blade × 1 from stores"),
    ("guard", "guard panel × 1 from stores"), ("bearing", "bearing × 1 from stores"),
]


# ---- SPOKEN STEPS extraction: maintenance actions (verb + object) from one short clause, no network, ~0.1 ms
_FUTURE = _rx(r"going to|gonna|will|i'?ll|we'?ll|need to|needs to|have to|has to|should|must|let'?s|plan to|about to|"
              r"get(?:ting)? (?:the |this |that )?(?:job|work|repair|task) done")    # "get the job done" is an intention
WORK_STEPS = {"guard_off", "replace", "guard_on"}
# the laptop speaks BeltGuard's replies; the band mic can hear them. Never act on our own words.
_ECHO = re.compile(r"^(?:safety check|safety |warning |done |added |in progress |to do |isolation logged|marked complete|job complete|"
                   r"band evidence|beltguard did|conveyor stopped noted|an authorized person)")
_GEN = re.compile(r"(?<![a-z])(?P<verb>tighten|torque|grease|lubricate|inspect|check|clean|align|adjust|install|fit|change|"
                  r"swap|remove|replace|measure|secure|unbolt|bolt|weld|repair|fix|test|restart|tension|clear|lift|lower|mount)"
                  r"(?P<sfx>ed|d|ing|s)?(?: up| down| out| off| back)?\s+(?P<obj>[a-z0-9' ]+)")
_NOUNS = {"belt", "idler", "roller", "pulley", "bearing", "guard", "bolt", "skirt", "skirting", "scraper", "cleaner", "chute",
          "motor", "gearbox", "drive", "coupling", "splice", "lagging", "tension", "conveyor", "frame", "hopper", "liner",
          "chain", "sprocket", "seal", "shaft", "brake", "isolator", "walkway", "spillage", "takeup", "nut", "bracket",
          "plate", "panel", "cover", "alignment", "tracking"}
_ARTS = {"the", "a", "an", "that", "this", "those", "these", "all", "its", "their", "our", "my", "some"}
_STOPW = {"and", "then", "but", "so", "because", "now", "please", "yeah", "okay", "ok", "right", "first", "next", "too",
          "again", "at", "on", "in", "for", "with", "to", "of", "it", "them", "there", "here", "is", "are", "was"}
_CANON = {"change": "Replace", "swap": "Replace", "fit": "Install", "mount": "Install", "fix": "Repair", "lubricate": "Grease"}


def _singular(w: str) -> str:
    return w[:-1] if w.endswith("s") and w[:-1] in _NOUNS else w


def _spoken_actions(clause: str) -> list:
    """[(step_id, title, tense)] for the maintenance actions in one short clause. tense: done | progress | plan.
    Known job actions keep their usual names; any other equipment action becomes 'Verb object'."""
    # Deepgram often drops the apostrophe: "Guards off" / "guards back on" mean "guard's off" / "guard's back on"
    clause = re.sub(r"(?<![a-z])guards (?=off|on|back|removed|out|refitted|fitted|reinstalled)", "guard's ", clause)
    clause = re.sub(r"(?<![a-z])took (?:out|off) ", "removed ", clause)               # "took out the guard" = removed it
    clause = re.sub(r"(?<![a-z])taking (?:out|off) ", "removing ", clause)
    clause = re.sub(r"(?<![a-z])takes? (?:out|off) ", "remove ", clause)
    fixed, seen = [], set()
    for key in KEYS:                                  # every known action in the clause, not just the first
        m = P[key].search(clause)
        if not m:
            continue
        if key == "job_done":
            item = ("signoff", TITLES["signoff"], "done")
        elif key == "stopped":
            item = ("stop", "Stop conveyor CV-102", "done")
        elif key == "tool_use":
            tool = re.search(r"hammer drill|hammer|drill|impact wrench|impact|wrench|rattle gun|angle grinder|grinder|breaker|"
                             r"power jack|jack", clause)
            name = tool.group(0) if tool else "tool"
            item = ("tool:" + name.replace(" ", "_"), "Use the %s" % name, "progress")
        else:
            words = m.group(0)                        # "removing the guard" is in progress; "guard's off" is done
            doing = (re.search(r"(?<![a-z])[a-z]{3,}ing(?![a-z])", words)
                     and not re.search(r"running (?:again|fine|ok|okay)", words))
            item = (STEP_OF[key], TITLES[STEP_OF[key]], "progress" if (key == "replace_start" or doing) else "done")
        if item[0] not in seen:
            seen.add(item[0])
            fixed.append(item)
    if fixed:
        return fixed
    out = []
    for m in _GEN.finditer(clause):
        words = []
        for w in m.group("obj").split():
            if w in _STOPW:
                break
            if w in _ARTS and not words:
                continue
            words.append(w)
            if len(words) >= 4:
                break
        nouns = [i for i, w in enumerate(words) if _singular(w) in _NOUNS]
        if not nouns:
            continue
        verb, sfx = m.group("verb"), m.group("sfx") or ""
        canon = _CANON.get(verb, verb.capitalize())
        phrase = " ".join(words[: nouns[-1] + 1][-3:])
        tense = "done" if sfx in ("ed", "d") else "progress" if sfx == "ing" else "plan"
        noun = _singular(words[nouns[-1]])
        known = {("Replace", "idler"): "replace", ("Replace", "roller"): "replace", ("Remove", "guard"): "guard_off",
                 ("Install", "guard"): "guard_on"}.get((canon, noun))
        if known:                                     # same action as a known step: one step, never two
            out.append((known, TITLES[known], tense))
        else:
            out.append(("gen:%s:%s" % (canon.lower(), noun), "%s %s" % (canon, phrase), tense))
    return out


def _clean(text: str) -> str:
    _woke, rest = inspection.strip_wake_word(text or "")
    t = rest.lower().replace("’", "'").replace("-", " ")
    t = re.sub(r"[^a-z0-9',.;!? ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _clauses(t: str) -> list[str]:
    return [c.strip() for c in re.split(r"[,.;!?]| and | but | then ", t) if c.strip()]


def _reads_a_label(t: str) -> bool:
    norm = " %s " % _label_norm(t)
    return any(" %s " % label in norm for label in _LABELS if len(label.split()) >= 2)


def _hhmm(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def _mmss(seconds) -> str:
    s = int(round(float(seconds or 0)))
    return "%d:%02d" % (s // 60, s % 60)


def _quote(evidence: Optional[str]) -> str:
    found = re.search(r"“(.*)”", evidence or "")
    return found.group(1) if found else ""


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
        self._warned: set = set()
        self._transcript_id = None
        self._ensure_job({})                     # eager: the checklist is visible from the start of the shift

    # ------------------------------------------------------------ public API
    def on_transcript(self, text: str, state: Optional[dict] = None, report: Optional[dict] = None,
                      transcript_id: Optional[str] = None) -> dict:
        self._transcript_id = transcript_id                  # goes into refs of the actions this sentence causes
        try:
            return (self._on_spoken if SPOKEN_STEPS else self._on_transcript)(text, state or {}, report)
        finally:
            self._transcript_id = None

    def on_tool_session(self, session: dict, state: Optional[dict] = None) -> dict:
        if SPOKEN_STEPS:
            return self._on_tool_spoken(session, state or {})
        state = state or {}
        now = self.clock()
        self._ensure_job(state)
        actions = self._audit(now)
        changed, said = [], None
        session = session or {}
        station = session.get("station_id")
        if self.job and (not self.job.get("station_id") or not station or station == self.job["station_id"]):
            tool = exposure.get_tool(session.get("tool_id"), state.get("tools") or exposure.load_tool_profiles())
            name = re.sub(r"\s*\d+/\d+\"? drive", "", (tool or {}).get("name") or session.get("tool_id") or "tool").lower()
            status = session.get("status")
            step = self._step("replace")
            blocker = self._first_open_before("replace")
            if status in ("active", "confirmed") and blocker and step["status"] != "done":
                before_isolation = blocker["id"] == "isolate"
                if self._warn_once("band_before_" + blocker["id"]):
                    actions.append(self._action("safety_check", "Safety: tool running before isolation was logged. Isolate and "
                                                "lock out first." if before_isolation else "Tool running before “%s” was "
                                                "logged: check the guard." % blocker["title"], "needs_review", now))
                said = ("Safety check: isolate and lock out the conveyor first." if before_isolation
                        else "Say “%s” when it is done." % blocker["title"])
            elif status == "active" and step["status"] != "done":
                if self._set_step("replace", "in_progress", "band: %s running" % name, now, "band"):
                    changed.append("replace")
                    actions.append(self._action("checklist", "Marked “Replace idler” in progress: the band feels the %s "
                                                "running." % name, "done", now, detail="Done · from the band"))
            elif status == "confirmed":
                pts = session.get("points")
                ev = "band: %s %s trigger%s (confirmed)" % (name, _mmss(session.get("trigger_s")),
                                                             ", %.1f pts" % float(pts) if pts is not None else "")
                if self._set_step("replace", step["status"] if step["status"] == "done" else "in_progress", ev, now, "band"):
                    changed.append("replace")
                    actions.append(self._action("checklist", "Logged %s on “Replace idler”." % ev.replace("band: ", "band evidence: "),
                                                "done", now, detail="Done · from the band"))
        actions += self._exposure_actions(state, now)
        return self._changes(changed, actions, said)

    def on_state(self, state: Optional[dict] = None) -> dict:
        state = state or {}
        now = self.clock()
        self._ensure_job(state)
        return self._changes([], self._audit(now) + self._exposure_actions(state, now), None)

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
            job["steps"] = [dict(s, label=s["title"], hint="" if SPOKEN_STEPS else (s.get("hint") or HINTS.get(s["id"], "")))
                            for s in job["steps"]]                                   # label: S8 alias
            job.update(self._progress())
        return {"job": job, "agent_actions": [dict(a) for a in self.actions[:50]]}

    # ------------------------------------------------------------ speech
    def _on_transcript(self, text: str, state: dict, report: Optional[dict]) -> dict:
        now = self.clock()
        t = _clean(text)
        if not t:
            return self._changes([], [], None)
        self._ensure_job(state)
        actions = self._audit(now)
        changed, said = [], None
        quote = (text or "").strip()
        if _META.search(t):
            # talk about the software, the screen or the session: no ticks, no drafts
            actions += self._exposure_actions(state, now)
            return self._changes(changed, actions, None)

        matched, ambiguous, leftover, requested = [], [], [], set()
        long_utterance = len(t.split()) > MAX_UTTERANCE_WORDS
        for clause in _clauses(t):
            hit = False
            request = bool(_REQUEST.search(clause))          # "can you remove the guard?" -> in progress
            blocked = _TRUE_NEG.search(clause) if request else (_NEGATED.search(clause) or _QUESTION.search(clause))
            if not blocked:
                for key in KEYS:
                    if P[key].search(clause):
                        hit = True
                        target = ambiguous if (long_utterance or len(clause.split()) > MAX_CLAUSE_WORDS) else matched
                        if key not in target:
                            target.append(key)
                        if request:
                            requested.add(key)
            if not hit:
                leftover.append(clause)

        # a defect heard: draft the work order (if the backend has not) and a parts request. Only the clauses that
        # were not job progress count, and only real problem words (seized, worn, ...).
        defect_text = " ".join(leftover)
        rules = inspection._rules_extract(defect_text) if defect_text else {}
        if (defect_text and inspection.is_inspection_report(defect_text, allow_trigger=False)
                and rules.get("condition") not in (None, "needs replacement", "needs repair")):
            actions += self._defect_actions(defect_text, state, report, now)

        for key in ambiguous:                                 # a job phrase buried in a long clause: ask, never tick
            step = self._step(STEP_OF.get(key, "signoff"))
            if step["status"] != "done" and self._warn_once("ambiguous_" + step["id"]):
                actions.append(self._action("confirm_step", "Heard “%s”: confirm “%s”?" % (quote[:60], step["title"]),
                                            "needs_review", now))

        evidence = "heard %s: “%s”" % (_hhmm(now), quote[:80])
        for key in matched:
            if key == "job_done":
                actions += self._job_done(now, evidence)
                changed.append("signoff")
                said = self._summary_sentence(prefix="Marked complete; awaiting supervisor verification. ")
                continue
            if key == "stopped":                              # stopping the belt is not isolating it: prompt, never tick
                if self._step("isolate")["status"] != "done":
                    if self._warn_once("stopped"):
                        actions.append(self._action("safety_check", "Conveyor stopped noted: isolate and lock out before work "
                                                    "starts.", "needs_review", now))
                    said = said or "Conveyor stopped. Next: isolate and lock out."
                continue
            step_id = STEP_OF[key]
            status = "in_progress" if (key in ("replace_start", "tool_use") or key in requested) else "done"
            step = self._step(step_id)
            if step["status"] == "done":
                continue
            blocker = self._first_open_before(step_id)
            if blocker:                                       # strict order: never tick ahead; say why, out loud
                acts, line = self._out_of_order(step, blocker, quote, now)
                actions += acts
                said = said or line
                continue
            if self._set_step(step_id, status, evidence, now, "voice"):
                changed.append(step_id)
                if key == "isolate" and status == "done":
                    actions.append(self._action("isolation", "Isolation logged; an authorized person must verify it.",
                                                "needs_review", now))
                else:
                    actions.append(self._action("checklist", "Ticked “%s” from what you said." % step["title"]
                                                if status == "done" else "Marked “%s” in progress from what you said."
                                                % step["title"], "done", now, detail="Done · " + evidence))
                if status == "done":
                    said = said or self._next_sentence(SAY_DONE.get(step_id, "Noted."))
                elif key == "tool_use":
                    said = said or "Tool use noted: “Replace idler” is in progress; the band is timing it."
                else:
                    said = said or "Idler replacement started; the band is timing it."
        actions += self._exposure_actions(state, now)
        return self._changes(changed, actions, said)

    def _out_of_order(self, step: dict, blocker: dict, quote: str, now: float) -> tuple:
        """Proactive safety instead of silent blocking: (actions, spoken line). The action is logged once per job."""
        if blocker["id"] == "isolate":
            kind, text = "safety_check", "Safety: work started before isolation was logged. Isolate and lock out first."
            say = "Safety check: isolate and lock out the conveyor first."
        elif step["id"] == "test_run" and self._step("guard_on")["status"] != "done":
            kind, text = "safety_check", "Safety: test run heard before the guard was refitted. Check before the belt is restarted."
            say = "The guard isn't refitted yet: refit it before the test run."
        elif blocker["id"] == "guard_off":
            kind, text = "confirm_step", "Guard removal not logged yet: say “Guard's off” when it is."
            say = "Say “Guard's off” once the guard is removed."
        else:
            kind = "confirm_step"
            text = "Heard “%s” for “%s”, but “%s” isn't done yet: confirm?" % (quote[:50], step["title"], blocker["title"])
            say = "“%s” isn't done yet." % blocker["title"]
        acts = [self._action(kind, text, "needs_review", now)] if self._warn_once("order_%s_%s" % (step["id"], blocker["id"])) else []
        return acts, say

    # ------------------------------------------------------------ SPOKEN STEPS (S0, 16:15)
    def _on_spoken(self, text: str, state: dict, report: Optional[dict]) -> dict:
        """Steps are created from what the technician says, as they say it. Each short clause is read for a maintenance
        action (verb + object); tense sets the status; a repeat only ever moves a step forward."""
        now = self.clock()
        raw = (text or "").strip()
        t = _clean(text)
        if not t:
            return self._changes([], [], None)
        self._ensure_job(state)
        if _META.search(t):                                   # talk about the software or the screen: nothing
            return self._changes([], self._exposure_actions(state, now), None)
        actions, changed, said, leftover = [], [], None, []
        evidence = "heard %s: “%s”" % (_hhmm(now), raw[:80])
        for sentence in re.split(r"(?<=[.!?])\s+", t):
            if _ECHO.search(sentence):                        # BeltGuard's own spoken reply, heard by the band mic
                continue
            asked = sentence.rstrip().endswith("?")
            request = bool(_REQUEST.search(sentence))
            parts = re.split(r"[,.;!?]| and | but | then | so |(?=(?<![a-z])(?:can|could|would|will) you(?![a-z]))|"
                             r"(?=(?<![a-z])please(?![a-z]))", sentence)       # live words come without punctuation
            for clause in [c.strip() for c in parts if c and c.strip()]:
                req = request or bool(_REQUEST.search(clause))
                if (len(clause.split()) > MAX_CLAUSE_WORDS or _TRUE_NEG.search(clause)
                        or (asked and not req and _QUESTION.search(clause))):   # "is the guard off?" asks, not tells
                    leftover.append(clause)
                    continue
                hits = _spoken_actions(clause)
                if not hits:
                    leftover.append(clause)
                    continue
                for step_id, title, tense in hits:
                    status = self._spoken_status(tense, req or asked, clause)
                    acts, line, did = self._apply_spoken(step_id, title, status, evidence, now)
                    actions += acts
                    if did and step_id not in changed:
                        changed.append(step_id)
                    if line and (said is None or line.startswith("Safety")):
                        said = line
        defect_text = " ".join(leftover)
        rules = inspection._rules_extract(defect_text) if defect_text else {}
        if (defect_text and inspection.is_inspection_report(defect_text, allow_trigger=False)
                and rules.get("condition") not in (None, "needs replacement", "needs repair")):
            actions += self._defect_actions(defect_text, state, report, now)
        actions += self._exposure_actions(state, now)
        return self._changes(changed, actions, said)

    def _spoken_status(self, tense: str, asking: bool, clause: str) -> str:
        if tense == "done" and not asking and not _FUTURE.search(clause):
            return "done"
        if tense == "progress":
            return "in_progress"
        busy = any(s["status"] == "in_progress" for s in self.job["steps"])
        return "left" if busy else "in_progress"

    def _apply_spoken(self, step_id: str, title: str, status: str, evidence: str, now: float) -> tuple:
        """(actions, spoken line, changed) for one heard action."""
        if step_id == "signoff":                              # "job done": the job is complete (no approvals, user 16:41)
            if status != "done":                              # "get the job done" / "is the job done?": not a report
                return [], None, False
            if self.job.get("status") == "complete":
                return [], None, False
            self.job["status"] = "complete"
            n = sum(1 for s in self.job["steps"] if s["status"] == "done")
            acts = [self._action("job_complete", "Job complete: %d step%s done." % (n, "" if n == 1 else "s"), "done", now,
                                 detail="Done · " + evidence)]
            return acts, "Job complete.", True
        same = next((s for s in self.job["steps"] if s["title"].lower() == title.lower()), None)
        if same:                                              # semantic dedupe: one step per action, whatever the id
            step_id = same["id"]
        existed = self._step(step_id) is not None
        step = self._step(step_id, title)
        rank = {"left": 0, "in_progress": 1, "done": 2}
        if existed and rank[status] <= rank.get(step["status"], 0):
            return [], None, False                            # a repeat never moves a step back
        step.update({"status": status, "evidence": evidence, "ts": now, "source": "voice"})
        word = {"done": "done", "in_progress": "in progress", "left": "to do"}[status]
        acts = [self._action("checklist", ("Added step: %s (%s)" if not existed else "%s: %s") % (
            (title, word) if not existed else (word.capitalize(), title)), "done", now, detail="Done · " + evidence)]
        line = None
        if step_id == "isolate" and status == "done":
            line = "Isolation logged."
        elif status == "done":
            line = "Done: %s." % title
        elif not existed:
            line = "Added: %s." % title
        if (step_id in WORK_STEPS or step_id.startswith(("gen:", "tool:"))) and status != "left":
            iso = self._step("isolate")
            if (not iso or iso["status"] != "done") and self._warn_once("work_before_isolation"):
                acts.append(self._action("safety_check", "Warning: work started before isolation was logged. Isolate and "
                                         "lock out CV-102 first.", "done", now, detail="Warning · spoken to the technician"))
                line = "Safety check: isolate and lock out the conveyor first."
        return acts, line, True

    def _on_tool_spoken(self, session: dict, state: dict) -> dict:
        """A band tool session is evidence for the step in progress (or creates 'Use the <tool>')."""
        now = self.clock()
        self._ensure_job(state)
        session = session or {}
        station, status = session.get("station_id"), session.get("status")
        if (not self.job or status not in ("active", "confirmed")
                or (self.job.get("station_id") and station and station != self.job["station_id"])):
            return self._changes([], self._exposure_actions(state, now), None)
        tool = exposure.get_tool(session.get("tool_id"), state.get("tools") or exposure.load_tool_profiles())
        name = re.sub(r"\s*\d+/\d+\"? drive", "", (tool or {}).get("name") or session.get("tool_id") or "tool").lower()
        actions, changed, said = [], [], None
        target = (next((s for s in self.job["steps"] if s["status"] == "in_progress"), None)
                  or self._step("tool:%s" % (session.get("tool_id") or "tool"), "Use the %s" % name))
        if status == "active":
            ev = "band: %s running" % name
        else:
            pts = session.get("points")
            ev = "band: %s %s trigger%s (confirmed)" % (name, _mmss(session.get("trigger_s")),
                                                         ", %.1f pts" % float(pts) if pts is not None else "")
        if self._set_step(target["id"], "done" if target["status"] == "done" else "in_progress", ev, now, "band"):
            changed.append(target["id"])
            actions.append(self._action("checklist", "Band evidence on “%s”: %s." % (target["title"], ev[6:]), "done", now,
                                        detail="Done · from the band"))
        iso = self._step("isolate")
        if (not iso or iso["status"] != "done") and self._warn_once("band_before_isolation"):
            actions.append(self._action("safety_check", "Warning: tool running before isolation was logged. Isolate and lock "
                                        "out first.", "done", now, detail="Warning · spoken to the technician"))
            said = "Safety check: isolate and lock out the conveyor first."
        actions += self._exposure_actions(state, now)
        return self._changes(changed, actions, said)

    # ------------------------------------------------------------ job
    def _ensure_job(self, state: dict) -> None:
        wo = self._active_work_order(state)
        if self.job and wo and self.job.get("work_order_id") != wo.get("id") and self.job.get("status") != "awaiting_verification":
            if not self.job.get("work_order_id"):          # the default job now has a real work order behind it
                self.job.update({"work_order_id": wo["id"], "title": self._title(wo, state), "station_id": wo.get("station_id")})
            return
        if self.job:
            untouched = all(st["status"] == "left" for st in self.job["steps"]) and not self.job.get("work_order_id")
            here = state.get("active_station")
            if untouched and here and here != self.job.get("station_id"):     # follow the selected station until work starts
                self.job.update({"station_id": here, "title": self._title(None, state, here)})
            return
        self._job_n += 1
        station = (wo or {}).get("station_id") or state.get("active_station") or "S1"
        self.job = {"id": "JOB-%d" % self._job_n, "work_order_id": (wo or {}).get("id"),
                    "title": self._title(wo, state, station), "station_id": station, "conveyor_id": self.conveyor_id,
                    "status": "open", "opened_ts": self.clock(),
                    "steps": [] if SPOKEN_STEPS else
                    [{"id": sid, "title": title, "status": "left", "evidence": None, "ts": None, "source": None,
                      "hint": HINTS[sid]} for sid, title in STEPS]}

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
        station = station or "S1"
        return "Replace return idler · %s %s %s" % (self.conveyor_id, station, SHORT_STATION.get(station, ""))

    def _step(self, step_id: str, title: Optional[str] = None) -> Optional[dict]:
        """The step with this id; given a title, it is created (appended) when missing."""
        step = next((s for s in self.job["steps"] if s["id"] == step_id), None)
        if step is None and title:
            step = {"id": step_id, "title": title, "status": "left", "evidence": None, "ts": None, "source": None,
                    "hint": "", "created_ts": self.clock()}
            self.job["steps"].append(step)
        return step

    def _first_open_before(self, step_id: str) -> Optional[dict]:
        """The first earlier step that is not done (None: this step may tick now). No fixed order with spoken steps."""
        if SPOKEN_STEPS:
            return None
        for sid in ORDER[:ORDER.index(step_id)]:
            if self._step(sid)["status"] != "done":
                return self._step(sid)
        return None

    def _warn_once(self, what: str) -> bool:
        """True the first time a warning comes up for this job (no repeats on every sentence)."""
        warned = getattr(self, "_warned", None)
        if warned is None:                       # instances migrated from an older class have no _warned yet
            warned = self._warned = set()
        key = ((self.job or {}).get("id"), what)
        if key in warned:
            return False
        warned.add(key)
        return True

    def _set_step(self, step_id: str, status: str, evidence: str, ts: float, source: str) -> bool:
        if not self.job:
            return False
        step = self._step(step_id, TITLES.get(step_id, step_id))
        if step["status"] == status and step["evidence"] == evidence:
            return False
        if step["status"] == "done" and status != "done":
            return False                                        # never un-tick a step on weaker evidence
        step.update({"status": status, "evidence": evidence, "ts": ts, "source": source})
        return True

    def _audit(self, now: float) -> list:
        """Un-tick any step that breaks the strict rules (out of order, or 'heard' evidence about the software or a long
        ramble), e.g. ticked by an older version of this agent. Each un-tick is a needs_review action."""
        if SPOKEN_STEPS or not self.job or self.job.get("status") in ("awaiting_verification", "verified"):
            return []
        out = []
        for sid in ORDER[:-1]:                                  # sign-off is a person's, never audited away
            step = self._step(sid)
            if step["status"] == "left" or step.get("source") == "user":
                continue
            why = None
            said = _quote(step.get("evidence")) if step.get("source") == "voice" else ""
            if said and (_META.search(said.lower()) or len(said.split()) > 2 * MAX_CLAUSE_WORDS):
                why = "the words heard were not about doing the job"
            elif self._first_open_before(sid):
                why = "it was ticked before “%s”" % self._first_open_before(sid)["title"]
            if why:
                step.update({"status": "left", "evidence": None, "ts": None, "source": None})
                out.append(self._action("untick", "Un-ticked “%s”: %s. Say it again when it is done." % (step["title"], why),
                                        "needs_review", now))
        return out

    def _progress(self) -> dict:
        steps = self.job["steps"]
        done = [s for s in steps if s["status"] == "done"]
        left = [s for s in steps if s["status"] != "done"]
        return {"progress": {"done": len(done), "total": len(steps)}, "summary": self._summary(done, left)}

    @staticmethod
    def _summary(done: list, left: list) -> str:
        if not left and not done:
            return "No steps yet: say what you're doing."
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
        detail = extra.pop("detail", None) or DETAIL.get(kind) or DETAIL_BY_STATUS.get(status, "")
        refs = {k: extra.get(k) for k in ("work_order_id", "parts_request_id") if extra.get(k)}
        if getattr(self, "_transcript_id", None):
            refs["transcript_id"] = self._transcript_id
        a = {"id": "ACT-%d" % self._act_n, "ts": ts, "kind": kind, "text": text, "title": text, "detail": detail,
             "status": status, "refs": refs}
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
            out.append(self._action("exposure", "Action value reached (%.0f points): supervisor notified." % total,
                                    "done", now))
        return out

    def _changes(self, steps: list, actions: list, say: Optional[str]) -> dict:
        snap = self.snapshot()
        return {"job": snap["job"] if (steps or actions) else None, "steps": steps, "actions": actions, "say": say}
