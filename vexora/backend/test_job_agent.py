"""Tests for backend/job_agent.py (S4 hav-brain).   python backend/test_job_agent.py"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import job_agent as _job_agent  # noqa: E402
from job_agent import JobAgent  # noqa: E402

# S0 16:20: these tests cover the LEGACY fixed checklist (still in job_agent.py behind SPOKEN_STEPS = False).
# The live design (SPOKEN STEPS, steps created from speech) is covered by test_spoken_steps.py.
try:
    import pytest

    @pytest.fixture(autouse=True)
    def _legacy_checklist(monkeypatch):
        monkeypatch.setattr(_job_agent, "SPOKEN_STEPS", False)
except ImportError:                                  # plain `python test_job_agent.py`: switched in __main__
    pytest = None

STATIONS = [
    {"id": "S1", "number": 1, "name": "Tail pulley & loading zone"},
    {"id": "S2", "number": 2, "name": "Carry idlers & belt tracking"},
    {"id": "S3", "number": 3, "name": "Return idlers"},
    {"id": "S4", "number": 4, "name": "Take-up & belt tension"},
    {"id": "S5", "number": 5, "name": "Head/drive pulley & discharge"},
]
STATE = {"stations": STATIONS, "active_station": "S3", "work_orders": [], "exposure": {"total_points": 3.0},
         "conveyor": {"id": "CV-102"}, "worker": {"id": "W-01"}}


class Clock:
    def __init__(self):
        self.t = 1794700000.0

    def __call__(self):
        self.t += 7
        return self.t


def agent():
    return JobAgent(clock=Clock())


def ready():
    """An agent with isolation and the guard off logged, ready for the idler."""
    a = agent()
    a.on_transcript("Conveyor's isolated and locked out", STATE)
    a.on_transcript("Guard's off", STATE)
    return a


def statuses(a):
    return {s["id"]: s["status"] for s in a.snapshot()["job"]["steps"]}


def kinds(c):
    return [x["kind"] for x in c["actions"]]


def test_eager_job_with_hints():
    job = agent().snapshot()["job"]                                          # before any call: never None
    assert job and job["title"] == "Replace return idler · CV-102 S1 tail pulley" and job["progress"]["done"] == 0, job
    hints = {s["id"]: s["hint"] for s in job["steps"]}
    assert hints["isolate"] == "Say: “Conveyor's isolated and locked out”" and hints["test_run"] == "Say: “Test run good”"
    assert all(hints.values())


def test_default_job():
    a = agent()
    a.on_state(STATE)
    job = a.snapshot()["job"]
    assert job["title"] == "Replace return idler · CV-102 S3 return idlers" and job["station_id"] == "S3", job
    assert [s["id"] for s in job["steps"]] == ["isolate", "guard_off", "replace", "guard_on", "test_run", "signoff"]
    assert all(s["status"] == "left" for s in job["steps"]) and job["progress"] == {"done": 0, "total": 6}
    assert job["summary"].startswith("0 done, 6 left: Isolate & lock out CV-102")
    assert job["steps"][0]["label"] == "Isolate & lock out CV-102"                     # alias for S8


def test_job_follows_work_order():
    a = agent()
    wo = {"id": "WO-1012", "station_id": "S5", "title": "Replace worn head pulley — CV-102 S5 Head/drive", "status": "draft",
          "ts": 5}
    a.on_state(dict(STATE, work_orders=[wo], active_station="S5"))
    job = a.snapshot()["job"]
    assert job["work_order_id"] == "WO-1012" and job["title"] == "Replace worn head pulley" and job["station_id"] == "S5"


def test_demo_sentences_in_order():
    a = agent()
    c = a.on_transcript("Conveyor's isolated and locked out", STATE)
    assert c["steps"] == ["isolate"] and "isolation" in kinds(c) and c["say"].endswith("Next: remove guard."), c
    assert a.on_transcript("Guard's off", STATE)["steps"] == ["guard_off"]
    c = a.on_transcript("replacing the idler now", STATE)
    assert c["steps"] == ["replace"] and statuses(a)["replace"] == "in_progress"
    assert not {"work_order", "parts_request"} & set(kinds(c)) and c["say"] == "Idler replacement started; the band is timing it."
    assert a.on_transcript("New idler fitted", STATE)["steps"] == ["replace"] and statuses(a)["replace"] == "done"
    assert a.on_transcript("Guard's back on", STATE)["steps"] == ["guard_on"]
    assert a.on_transcript("Test run good", STATE)["steps"] == ["test_run"]
    assert a.snapshot()["job"]["summary"] == "5 done, 1 left: Supervisor sign-off."
    c = a.on_transcript("Job done", STATE)
    job = a.snapshot()["job"]
    assert job["status"] == "awaiting_verification" and statuses(a)["signoff"] == "in_progress", job
    assert any("never closes" in x["text"] for x in c["actions"]) and job["status"] != "closed"
    assert c["say"].startswith("Marked complete; awaiting supervisor verification.")
    checklist = [x for x in a.snapshot()["agent_actions"] if x["kind"] == "checklist"]
    assert len(checklist) >= 4 and all(x["status"] == "done" for x in checklist)       # "BeltGuard did" fills up


def test_rant_and_screen_reading_never_tick():
    a = agent()
    for text in ("Why the hell are you taking so much time? GitHub code is pushed, is it isolated or what",
                 "Remove isolation and test run", "Remove isolation and test", "Isolate & lock out CV-102",
                 "the dashboard says the guard's off", "Claude, the session transcript shows locked out"):
        c = a.on_transcript(text, STATE)
        assert c["steps"] == [] and c["actions"] == [], (text, c)
    assert all(s == "left" for s in statuses(a).values())


def test_chat_questions_negations_change_nothing():
    a = agent()
    for text in ("Hi, can you hear me? Keep transcribing", "testing testing one two", "the conveyor is not isolated",
                 "we need to isolate it first", "the idler needs to be replaced", "is the guard off yet",
                 "the idler is in bad shape", "what's the band number"):
        c = a.on_transcript(text, STATE)
        assert c["steps"] == [], (text, c)
    assert all(s == "left" for s in statuses(a).values())


def test_long_clause_is_ambiguous_not_a_tick():
    a = agent()
    c = a.on_transcript("yeah mate the whole conveyor over by the big shed was isolated earlier this morning by the night crew", STATE)
    assert c["steps"] == [] and kinds(c) == ["confirm_step"] and c["actions"][0]["status"] == "needs_review", c
    assert c["actions"][0]["text"].startswith("Heard “") and "confirm “Isolate & lock out CV-102”?" in c["actions"][0]["text"]


def test_long_utterance_never_ticks():
    a = agent()
    a.on_transcript("Conveyor's isolated and locked out", STATE)
    said = ("so yeah I pick the tool, right, I start the tool and then he says to me, guard's off, and I'm like, "
            "bro can you use some other thing because this one is really heavy and loud")
    c = a.on_transcript(said, STATE)
    assert c["steps"] == [] and kinds(c) == ["confirm_step"] and statuses(a)["guard_off"] == "left", c
    c = a.on_transcript("In the ENA, Yeah. Remove isolation and test run. Bro, can you use some other one", STATE)
    assert c["steps"] == [] and c["actions"] == [], c                       # reading a step label: ignored


def test_strict_order_with_spoken_safety():
    a = agent()
    c = a.on_transcript("Guard's back on", STATE)
    assert c["steps"] == [] and kinds(c) == ["safety_check"] and "before isolation" in c["actions"][0]["text"], c
    c = a.on_transcript("Guard's off", STATE)                                  # before isolation
    assert c["steps"] == [] and kinds(c) == ["safety_check"] and "isolat" in c["actions"][0]["text"], c
    c = a.on_transcript("Test run good", STATE)
    assert c["steps"] == [] and c["say"] == "Safety check: isolate and lock out the conveyor first.", c
    again = a.on_transcript("Test run good", STATE)
    assert again["actions"] == [] and again["say"]                          # logged once per job, still spoken
    assert all(s == "left" for s in statuses(a).values())
    b = ready()
    c = b.on_transcript("Test run good", STATE)                              # idler and guard still open
    assert c["steps"] == [] and c["say"].startswith("The guard isn't refitted yet"), c
    assert b.on_transcript("New idler fitted", STATE)["steps"] == ["replace"]


def test_tool_use_and_wider_phrases():
    a = agent()
    c = a.on_transcript("So I'm using the hammer", STATE)                   # before isolation: the proactive moment
    assert c["steps"] == [] and kinds(c) == ["safety_check"], c
    assert c["actions"][0]["text"] == "Safety: work started before isolation was logged. Isolate and lock out first."
    assert c["say"] == "Safety check: isolate and lock out the conveyor first."
    b = ready()
    c = b.on_transcript("So I'm using the hammer", STATE)
    assert c["steps"] == ["replace"] and statuses(b)["replace"] == "in_progress" and "using the hammer" in \
        b.snapshot()["job"]["steps"][2]["evidence"], c
    assert b.on_transcript("Can you replace the idler?", STATE)["steps"] == []        # a question never ticks
    for text in ("LOTO applied", "lockout's on", "I've locked it out"):
        x = agent()
        assert x.on_transcript(text, STATE)["steps"] == ["isolate"], text
    y = ready()
    assert y.on_transcript("old idler's out", STATE)["steps"] == ["replace"] and statuses(y)["replace"] == "in_progress"
    z = agent()
    c = z.on_transcript("conveyor stopped", STATE)
    assert c["steps"] == [] and kinds(c) == ["safety_check"] and c["say"] == "Conveyor stopped. Next: isolate and lock out.", c


def test_band_respects_order():
    a = agent()
    c = a.on_tool_session({"id": "TS-1", "station_id": "S3", "tool_id": "impact_wrench", "status": "active"}, STATE)
    assert c["steps"] == [] and kinds(c) == ["safety_check"] and statuses(a)["replace"] == "left", c
    assert c["say"] == "Safety check: isolate and lock out the conveyor first."
    b = ready()
    c = b.on_tool_session({"id": "TS-1", "station_id": "S3", "tool_id": "impact_wrench", "status": "active"}, STATE)
    step = b.snapshot()["job"]["steps"][2]
    assert c["steps"] == ["replace"] and step["status"] == "in_progress" and step["evidence"] == "band: impact wrench running"
    b.on_tool_session({"id": "TS-1", "station_id": "S3", "tool_id": "impact_wrench", "status": "confirmed",
                       "trigger_s": 42, "points": 0.29}, STATE)
    step = b.snapshot()["job"]["steps"][2]
    assert step["evidence"] == "band: impact wrench 0:42 trigger, 0.3 pts (confirmed)" and step["source"] == "band", step
    other = b.on_tool_session({"id": "TS-2", "station_id": "S5", "tool_id": "drill", "status": "active"}, STATE)
    assert other["steps"] == []                                               # another station is not this job


def test_replace_done_phrasings():
    for text in ("new idler fitted", "idler's in", "fitted the new roller", "new one's in place", "idler replaced",
                 "installed the replacement idler", "the idler is done", "Okay, new roller installed."):
        a = ready()
        c = a.on_transcript(text, STATE)
        assert c["steps"] == ["replace"] and statuses(a)["replace"] == "done", (text, c)
    for text in ("the idler is in bad shape", "the idler needs to be replaced", "fitting the new idler"):
        a = ready()
        a.on_transcript(text, STATE)
        assert statuses(a)["replace"] != "done", text


def test_progress_and_defect_in_one_sentence():
    a = agent()
    a.on_transcript("Conveyor's isolated and locked out", STATE)
    c = a.on_transcript("Guard's off, the return idler at station three is seized.", STATE)
    assert c["steps"] == ["guard_off"] and "work_order" in kinds(c) and "parts_request" in kinds(c), c
    assert next(x for x in c["actions"] if x["kind"] == "parts_request")["text"].startswith("PR-2041: return idler 152 mm")
    b = agent()
    b.on_transcript("Conveyor's isolated and locked out", STATE)
    c = b.on_transcript("Guard's off, the return idler at station three is seized.", STATE,
                        report={"status": "ok", "work_order": {"id": "WO-1011"},
                                "defect": {"component": "Return idler", "condition": "seized", "station_id": "S3"}})
    wo = next(x for x in c["actions"] if x["kind"] == "work_order")
    assert wo["work_order_id"] == "WO-1011" and wo["refs"]["work_order_id"] == "WO-1011", c["actions"]


def test_job_done_flags_missing_evidence():
    a = agent()
    a.on_transcript("conveyor isolated", STATE)
    c = a.on_transcript("that's the job done", STATE)
    missing = [x for x in c["actions"] if x["kind"] == "missing_evidence"]
    assert missing and "Remove guard" in missing[0]["text"] and "Refit guard" in missing[0]["text"], c["actions"]


def test_defect_drafts():
    a = agent()
    c = a.on_transcript("return idler seized at station three", STATE)
    by = {x["kind"]: x for x in c["actions"]}
    assert by["work_order"]["status"] == "draft"
    assert by["parts_request"]["text"] == "PR-2041: return idler 152 mm × 1 from stores (draft, for stores to confirm)."
    assert by["parts_request"]["refs"]["parts_request_id"] == "PR-2041" and by["parts_request"]["detail"].startswith("Draft")


def test_exposure_notifies_once():
    a = agent()
    c = a.on_state(dict(STATE, exposure={"total_points": 82.0}))
    assert [x["text"] for x in c["actions"]] == ["Supervisor notified: 82 of 100 points today; rotation suggested."]
    assert a.on_state(dict(STATE, exposure={"total_points": 85.0}))["actions"] == []
    c = a.on_state(dict(STATE, verdict={"total_points": 101.0}))
    assert c["actions"] and c["actions"][0]["status"] == "needs_review" and "Action value" in c["actions"][0]["text"]


def test_audit_unticks_bad_ticks():
    a = agent()
    a.on_state(STATE)
    steps = {s["id"]: s for s in a.job["steps"]}                               # what an older version left behind
    steps["isolate"].update(status="done", source="voice", evidence="heard 15:20: “Why the hell are you taking so much time… "
                            "GitHub code is pushed”")
    steps["test_run"].update(status="done", source="voice", evidence="heard 15:41: “remove isolation and test”")
    c = a.on_state(STATE)
    assert statuses(a)["isolate"] == "left" and statuses(a)["test_run"] == "left", a.snapshot()["job"]["steps"]
    assert kinds(c).count("untick") == 2 and all(x["status"] == "needs_review" for x in c["actions"])


def test_aliases_refs_and_snapshot():
    a = agent()
    c = a.on_transcript("BeltGuard, conveyor isolated", STATE, transcript_id="TR-9")
    assert c["steps"] == ["isolate"]
    act = c["actions"][0]
    assert act["title"] == act["text"] and act["detail"] and act["refs"] == {"transcript_id": "TR-9"}, act
    snap = a.snapshot()
    json.dumps(snap)
    assert all("label" in s for s in snap["job"]["steps"]) and snap["job"]["progress"]["done"] == 1
    assert a.supervisor_sign_off("K. Lee")["steps"] == ["signoff"] and a.snapshot()["job"]["status"] == "verified"


if __name__ == "__main__":
    _job_agent.SPOKEN_STEPS = False                  # legacy checklist tests (see the note at the top)
    failures = 0
    for name, fn in [(n, f) for n, f in list(globals().items()) if n.startswith("test_") and callable(f)]:
        try:
            fn()
            print("PASS %s" % name)
        except Exception as error:
            failures += 1
            print("FAIL %s: %r" % (name, error))
    print("%d failed" % failures if failures else "all passed")
    sys.exit(1 if failures else 0)
