"""Tests for SPOKEN STEPS in backend/job_agent.py (S0): job steps are created from what the technician says.
python -m pytest backend/test_spoken_steps.py -q      (or: python backend/test_spoken_steps.py)"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import job_agent as J  # noqa: E402

try:
    import pytest

    @pytest.fixture(autouse=True)
    def _spoken(monkeypatch):
        monkeypatch.setattr(J, "SPOKEN_STEPS", True)
except ImportError:
    pytest = None


class Clock:
    def __init__(self):
        self.t = 1794700000.0

    def __call__(self):
        self.t += 5
        return self.t


def agent():
    return J.JobAgent(clock=Clock())


def steps(a):
    return [(s["title"], s["status"]) for s in a.snapshot()["job"]["steps"]]


def test_job_starts_empty_without_hints():
    job = agent().snapshot()["job"]
    assert job and job["steps"] == [] and job["progress"] == {"done": 0, "total": 0}


def test_users_questions_create_steps_in_spoken_order():
    a = agent()
    a.on_transcript("Can you isolate and log out CV one zero two? Can you remove the guard? Can you remove isolation "
                    "and test run? Can you refit guard? Can you replace idler?", {})
    assert [t for t, _ in steps(a)] == ["Isolate & lock out CV-102", "Remove guard", "Remove isolation & test run",
                                        "Refit guard", "Replace idler"]
    assert steps(a)[0][1] == "in_progress" and all(s == "left" for _, s in steps(a)[1:])   # one in progress at a time


def test_statements_tick_done_and_garbled_asset_code_is_fine():
    a = agent()
    out = a.on_transcript("Isolate and lock out C B 102.", {})
    assert steps(a) == [("Isolate & lock out CV-102", "done")]
    assert "authorized person must verify" in out["say"]
    assert any(x["kind"] == "isolation" and x["status"] == "needs_review" for x in out["actions"])


def test_repeat_never_moves_a_step_back_and_makes_no_duplicates():
    a = agent()
    a.on_transcript("Guard's off.", {})
    out = a.on_transcript("Can you remove the guard?", {})
    assert steps(a).count(("Remove guard", "done")) == 1 and len(steps(a)) == 1 and out["steps"] == []


def test_work_before_isolation_gives_one_safety_nudge():
    a = agent()
    first = a.on_transcript("So I'm using the hammer.", {})
    assert steps(a) == [("Use the hammer", "in_progress")]
    assert first["say"].startswith("Safety check") and any(x["kind"] == "safety_check" for x in first["actions"])
    again = a.on_transcript("Remove the guard.", {})
    assert not any(x["kind"] == "safety_check" for x in again["actions"])                # once per job, not every sentence


def test_other_equipment_actions_become_steps():
    a = agent()
    a.on_transcript("Let's grease the bearings and check the belt tension.", {})
    assert ("Grease bearings", "left") in steps(a) or ("Grease bearings", "in_progress") in steps(a)
    assert any(t == "Check belt tension" for t, _ in steps(a))


def test_chat_and_software_talk_create_nothing():
    a = agent()
    for s in ["The WiFi is down, push the code to GitHub.", "I wanna hack a bus.", "Is the guard off?",
              "The guard isn't off yet."]:
        a.on_transcript(s, {})
    assert steps(a) == []


def test_job_done_awaits_a_person():
    a = agent()
    a.on_transcript("Conveyor's isolated and locked out. Guard's off. New idler fitted.", {})
    out = a.on_transcript("Job done.", {})
    snap = a.snapshot()["job"]
    assert snap["status"] == "awaiting_verification" and ("Supervisor sign-off", "in_progress") in steps(a)
    assert "awaiting supervisor verification" in out["say"]
    assert a.supervisor_sign_off("K. Lee")["steps"] == ["signoff"] and a.snapshot()["job"]["status"] == "verified"


def test_band_tool_session_is_evidence_for_the_step_in_progress():
    a = agent()
    a.on_transcript("Conveyor's isolated and locked out. I'm replacing the idler.", {})
    out = a.on_tool_session({"status": "confirmed", "tool_id": "impact_wrench", "trigger_s": 42, "points": 0.3}, {})
    replace = next(s for s in a.snapshot()["job"]["steps"] if s["title"] == "Replace idler")
    assert out["steps"] == ["replace"] and replace["source"] == "band" and "0:42 trigger" in replace["evidence"]


if __name__ == "__main__":
    J.SPOKEN_STEPS = True
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
