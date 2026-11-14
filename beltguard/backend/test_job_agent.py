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

from job_agent import JobAgent  # noqa: E402

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


def statuses(a):
    return {s["id"]: s["status"] for s in a.snapshot()["job"]["steps"]}


def test_default_job():
    a = agent()
    a.on_state(STATE)
    job = a.snapshot()["job"]
    assert job["title"] == "Replace return idler at S3 Return idlers" and job["station_id"] == "S3", job
    assert [s["id"] for s in job["steps"]] == ["isolate", "guard_off", "replace", "guard_on", "test_run", "signoff"]
    assert all(s["status"] == "left" for s in job["steps"]) and job["progress"] == {"done": 0, "total": 6}
    assert job["summary"].startswith("0 done, 6 left: Isolate & lock out CV-102")


def test_job_follows_work_order():
    a = agent()
    wo = {"id": "WO-1012", "station_id": "S5", "title": "Replace worn head pulley — CV-102 S5 Head/drive", "status": "draft",
          "ts": 5}
    a.on_state(dict(STATE, work_orders=[wo], active_station="S5"))
    job = a.snapshot()["job"]
    assert job["work_order_id"] == "WO-1012" and job["title"] == "Replace worn head pulley" and job["station_id"] == "S5"


def test_spoken_sequence():
    a = agent()
    c = a.on_transcript("Conveyor's isolated and locked out", STATE)
    assert c["steps"] == ["isolate"] and statuses(a)["isolate"] == "done", c
    assert any(x["kind"] == "isolation" and x["status"] == "needs_review" and "authorized person" in x["text"] for x in c["actions"])
    assert "heard" in a.snapshot()["job"]["steps"][0]["evidence"] and c["say"].endswith("Next: remove guard."), c["say"]
    assert a.on_transcript("guard's off", STATE)["steps"] == ["guard_off"]
    c = a.on_transcript("replacing the idler now", STATE)
    assert c["steps"] == ["replace"] and statuses(a)["replace"] == "in_progress" and c["actions"] == [], c   # not a new defect
    assert c["say"] == "Idler replacement started; the band is timing it."
    assert a.on_transcript("new idler's in.", STATE)["steps"] == ["replace"] and statuses(a)["replace"] == "done"
    assert a.on_transcript("guard back on", STATE)["steps"] == ["guard_on"]
    assert a.on_transcript("isolation off, test run", STATE)["steps"] == ["test_run"]
    assert a.snapshot()["job"]["summary"] == "5 done, 1 left: Supervisor sign-off."
    c = a.on_transcript("job done", STATE)
    job = a.snapshot()["job"]
    assert job["status"] == "awaiting_verification" and statuses(a)["signoff"] == "in_progress", job
    assert any("never closes" in x["text"] for x in c["actions"]) and job["status"] != "closed"
    assert c["say"].startswith("Marked complete; awaiting supervisor verification.")


def test_replace_done_phrasings():
    for text in ("new idler fitted", "idler's in", "fitted the new roller", "new one's in place", "idler replaced",
                 "installed the replacement idler", "the idler is done", "Okay, new roller installed."):
        a = agent()
        c = a.on_transcript(text, STATE)
        assert c["steps"] == ["replace"] and statuses(a)["replace"] == "done", (text, c)
    for text in ("the idler is in bad shape", "the idler needs to be replaced", "fitting the new idler"):
        a = agent()
        a.on_transcript(text, STATE)
        assert statuses(a)["replace"] != "done", text


def test_job_done_flags_missing_evidence():
    a = agent()
    a.on_transcript("conveyor isolated", STATE)
    c = a.on_transcript("that's the job done", STATE)
    missing = [x for x in c["actions"] if x["kind"] == "missing_evidence"]
    assert missing and "Remove guard" in missing[0]["text"] and "Refit guard" in missing[0]["text"], c["actions"]


def test_chat_and_negations_change_nothing():
    a = agent()
    for text in ("Hi, can you hear me? Keep transcribing", "testing testing one two", "the conveyor is not isolated",
                 "we need to isolate it first", "the idler needs to be replaced", "is the guard off yet",
                 "the idler is in bad shape", "what's the band number"):
        c = a.on_transcript(text, STATE)
        assert c["steps"] == [], (text, c)
    assert all(s == "left" for s in statuses(a).values())


def test_band_drives_the_replace_step():
    a = agent()
    c = a.on_tool_session({"id": "TS-1", "station_id": "S3", "tool_id": "impact_wrench", "status": "active"}, STATE)
    step = a.snapshot()["job"]["steps"][2]
    assert c["steps"] == ["replace"] and step["status"] == "in_progress" and step["evidence"] == "band: impact wrench running"
    a.on_tool_session({"id": "TS-1", "station_id": "S3", "tool_id": "impact_wrench", "status": "confirmed",
                       "trigger_s": 42, "points": 0.29}, STATE)
    step = a.snapshot()["job"]["steps"][2]
    assert step["evidence"] == "band: impact wrench 0:42 trigger, 0.3 pts (confirmed)" and step["source"] == "band", step
    other = a.on_tool_session({"id": "TS-2", "station_id": "S5", "tool_id": "drill", "status": "active"}, STATE)
    assert other["steps"] == []                                   # a tool at another station is not this job


def test_defect_drafts_work_order_and_parts_request():
    a = agent()
    c = a.on_transcript("return idler seized at station three", STATE)
    kinds = {x["kind"]: x for x in c["actions"]}
    assert kinds["work_order"]["status"] == "draft"
    assert kinds["parts_request"]["text"] == "PR-2041: return idler 152 mm × 1 from stores (draft, for stores to confirm)."
    c2 = a.on_transcript("return idler seized", STATE, report={"status": "ok", "work_order": {"id": "WO-1013"},
                                                              "defect": {"component": "Return idler", "condition": "seized",
                                                                         "station_id": "S3"}})
    wo = next(x for x in c2["actions"] if x["kind"] == "work_order")
    assert wo["work_order_id"] == "WO-1013" and "WO-1013" in wo["text"]
    assert next(x for x in c2["actions"] if x["kind"] == "parts_request")["parts_request_id"] == "PR-2042"


def test_progress_and_defect_in_one_sentence():
    a = agent()
    c = a.on_transcript("Guard's off, the return idler at station three is seized.", STATE)
    kinds = [x["kind"] for x in c["actions"]]
    assert c["steps"] == ["guard_off"] and "work_order" in kinds and "parts_request" in kinds, c
    assert next(x for x in c["actions"] if x["kind"] == "parts_request")["text"].startswith("PR-2041: return idler 152 mm")
    b = agent()
    c = b.on_transcript("Guard's off, the return idler at station three is seized.", STATE,
                        report={"status": "ok", "work_order": {"id": "WO-1011"},
                                "defect": {"component": "Return idler", "condition": "seized", "station_id": "S3"}})
    assert any(x["kind"] == "work_order" and x.get("work_order_id") == "WO-1011" for x in c["actions"]), c["actions"]
    assert agent().on_transcript("replacing the idler now", STATE)["actions"] == []


def test_exposure_notifies_once():
    a = agent()
    c = a.on_state(dict(STATE, exposure={"total_points": 82.0}))
    assert [x["text"] for x in c["actions"]] == ["Supervisor notified: 82 of 100 points today; rotation suggested."]
    assert a.on_state(dict(STATE, exposure={"total_points": 85.0}))["actions"] == []
    c = a.on_state(dict(STATE, verdict={"total_points": 101.0}))
    assert c["actions"] and c["actions"][0]["status"] == "needs_review" and "Action value" in c["actions"][0]["text"]


def test_wake_word_and_snapshot_serialisable():
    a = agent()
    assert a.on_transcript("BeltGuard, conveyor isolated", STATE)["steps"] == ["isolate"]
    snap = a.snapshot()
    json.dumps(snap)
    assert snap["agent_actions"][0]["kind"] == "isolation" and snap["job"]["progress"]["done"] == 1
    assert a.supervisor_sign_off("K. Lee")["steps"] == ["signoff"] and a.snapshot()["job"]["status"] == "verified"


if __name__ == "__main__":
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
