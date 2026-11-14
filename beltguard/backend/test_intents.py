"""Tests for backend/intents.py (S4 hav-brain).   python backend/test_intents.py

Band mic (default source): commands need the wake word "BeltGuard"; without it only a clear defect
report (named part + problem word) is heard and everything else is ignored. Typed input needs no wake word."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import time  # noqa: E402

import exposure  # noqa: E402
from intents import find_tools, parse_intent, respond, split_wake_word  # noqa: E402

NOW = time.time()

STATIONS = [
    {"id": "S1", "number": 1, "name": "Tail pulley & loading zone"},
    {"id": "S2", "number": 2, "name": "Carry idlers & belt tracking"},
    {"id": "S3", "number": 3, "name": "Return idlers"},
    {"id": "S4", "number": 4, "name": "Take-up & belt tension"},
    {"id": "S5", "number": 5, "name": "Head/drive pulley & discharge"},
]
TOOLS = exposure.load_tool_profiles()
CTX = {
    "stations": STATIONS, "tools": TOOLS, "active_station": "S3", "active_tool": None,
    "tool_sessions": [{"id": "TS-8", "status": "confirmed", "ts": NOW - 300},
                      {"id": "TS-9", "status": "unresolved", "ts": NOW - 120, "wall_end": NOW - 60}],
    "work_orders": [
        {"id": "WO-1", "station_id": "S3", "title": "Replace seized return idler — CV-102 S3 Return idlers",
         "status": "draft", "ts": 1},
        {"id": "WO-2", "station_id": "S5", "title": "Replace drive pulley lagging", "status": "planned", "ts": 2},
        {"id": "WO-0", "station_id": "S3", "title": "Old job", "status": "awaiting_verification", "ts": 0},
    ],
    "exposure": {"total_points": 2.5, "status": "below_action", "basis_tool_id": "impact_wrench"},
}
NO_SESSION = dict(CTX, tool_sessions=[], active_tool="impact_wrench")
S2_CTX = {"stations": STATIONS, "tools": TOOLS, "active_station": "S5", "active_tool_id": "hammer_drill",
          "assignment": None, "unresolved_session": {"id": "TS-42"}, "open_work_orders": CTX["work_orders"],
          "verdict": {"state": "within", "total_points": 12.0, "minutes_to_action": 22.0, "basis_tool_id": "hammer_drill"}}


def w(text):
    return "BeltGuard, " + text


def check(text, intent, ctx=CTX, clarify=False, source="band", wake=True, **slots):
    said = w(text) if (source == "band" and wake) else text
    r = parse_intent(said, ctx, source=source)
    assert r["intent"] == intent, (said, source, r)
    assert bool(r["clarify"]) == clarify, (said, r)
    for k, v in slots.items():
        assert r["slots"].get(k) == v, (said, k, r)
    return r


def test_wake_word_variants():
    variants = ["BeltGuard, ", "Belt guard, ", "belt-guard ", "Bell guard, ", "Belguard, ", "built guard ",
                "Hey BeltGuard, ", "OK belt guard, ", "Um, BeltGuard, ", "Beltguard: "]
    for prefix in variants:
        r = parse_intent(prefix + "I'm taking the hammer drill", CTX)
        assert r["intent"] == "tool_checkout" and r["slots"]["tool_id"] == "hammer_drill", (prefix, r)
        assert r["addressed"] is True and r["text"] == "I'm taking the hammer drill" and r["extract_source"] == "typed", r
    assert split_wake_word("BeltGuard, return idler seized") == (True, "return idler seized")
    assert split_wake_word("the belt guard is missing at station two")[0] is False      # a conveyor part, not the wake word
    assert parse_intent("BeltGuard", CTX)["intent"] == "help"
    unknown = parse_intent("BeltGuard, you're awesome", CTX)
    assert unknown["intent"] == "none" and unknown["ignore"] is False
    assert respond(unknown, None, CTX).startswith("Sorry, I didn't catch that")
    assert "Say BeltGuard first" in respond(parse_intent("BeltGuard, what can I say", CTX), {}, CTX)


def test_no_wake_word_is_silent():
    overheard = ["The API is not working, I can't log in to the account", "can't log in to the account",
                 "Hi, can you hear me? Keep transcribing", "what's the band number", "I'm at station three",
                 "taking the hammer drill home tonight", "how much time do I have", "yes", "it's done", "confirm",
                 "help", "I feel isolated", "something is broken", "my belt is loose", "I want to report a defect",
                 "the conveyor stopped working", "let me fix that", "is the demo done yet", ""]
    for text in overheard:
        r = parse_intent(text, CTX)
        assert r["intent"] == "none" and r["ignore"] is True, (text, r)
        assert respond(r, {}, CTX) == "", text


def test_yes_no_answers_need_no_wake_word_while_asked():
    asked = dict(CTX, awaiting_confirmation=True)
    for text, intent in (("Yes.", "confirm_session"), ("yeah it was", "confirm_session"), ("That's right", "confirm_session"),
                         ("No, it wasn't.", "reject_session"), ("nope", "reject_session"),
                         ("that wasn't the tool", "reject_session")):
        r = parse_intent(text, asked)
        assert r["intent"] == intent and r["slots"]["tool_session_id"] == "TS-9" and not r["ignore"], (text, r)
    assert parse_intent("yes", CTX)["ignore"] is True                                     # not asked: chat
    assert parse_intent("yes", dict(asked, tool_sessions=[]))["ignore"] is True           # nothing to confirm
    assert parse_intent("I'm at station three", asked)["ignore"] is True                  # other commands still need it


def test_defect_reports_heard_without_wake_word():
    for text in ("Conveyor CV-102, station three, return idler seized, needs replacement.",
                 "return idler seized at station three", "the bottom roller at three is chewing up the belt",
                 "the belt guard is missing at station two", "the idler needs to be replaced at station three"):
        r = parse_intent(text, CTX)
        assert r["intent"] == "report_defect" and r["ignore"] is False and r["extract_source"] == "band", (text, r)


def test_typed_needs_no_wake_word():
    check("taking the hammer drill", "tool_checkout", source="typed", tool_id="hammer_drill")
    check("I'm at station three", "select_station", source="typed", station_id="S3")
    check("something is broken", "report_defect", source="typed")
    assert parse_intent("BeltGuard, taking the drill", CTX, source="typed")["slots"]["tool_id"] == "drill"


def test_select_station():
    check("I'm at station three", "select_station", station_id="S3")
    check("Okay, I'm now at station 5.", "select_station", station_id="S5")
    check("at the return idlers", "select_station", station_id="S3")
    check("heading to the take-up", "select_station", station_id="S4")
    check("Station two.", "select_station", station_id="S2")
    check("taking a look at station three", "select_station", station_id="S3")
    check("I'm at station nine", "select_station", clarify=True)
    check("I'm at the idlers", "select_station", clarify=True)


def test_report_defect_addressed():
    check("return idler seized at station three", "report_defect")
    check("I want to report a defect", "report_defect")
    check("something is broken", "report_defect")


def test_tool_checkout():
    check("taking the hammer drill", "tool_checkout", tool_id="hammer_drill")
    check("Checking out the angle grinder.", "tool_checkout", tool_id="angle_grinder")
    check("unlock the breaker", "tool_checkout", tool_id="breaker_hammer")
    check("grab the jackhammer", "tool_checkout", tool_id="breaker_hammer")
    check("I'll grab the drill", "tool_checkout", tool_id="drill")
    check("can I check out the impact wrench", "tool_checkout", tool_id="impact_wrench")
    check("taking the one inch impact wrench", "tool_checkout", tool_id="impact_wrench_1in")
    check("taking the power jack", "tool_checkout", tool_id="power_jack")
    check("grabbing the belt lifter", "tool_checkout", tool_id="power_jack")
    check("taking the rattle gun", "tool_checkout", tool_id="impact_wrench")
    check("hammer drill", "tool_checkout", tool_id="hammer_drill")
    check("taking the hammer", "tool_checkout", clarify=True)
    check("check out a tool", "tool_checkout", clarify=True)
    check("taking the drill and the grinder", "tool_checkout", clarify=True)
    assert parse_intent(w("circuit breaker tripped"), CTX)["intent"] != "tool_checkout"
    assert parse_intent(w("taking the laser sword"), CTX)["intent"] == "none"     # never invent a tool


def test_tool_return():
    check("returning the drill", "tool_return", tool_id="drill")
    check("done with the hammer drill", "tool_return", tool_id="hammer_drill")
    check("putting the grinder away", "tool_return", tool_id="angle_grinder")
    check("finished with it", "tool_return", ctx=NO_SESSION, tool_id="impact_wrench")
    check("I'm done with the tool", "tool_return", clarify=True)
    check("returning to station three", "select_station", station_id="S3")


def test_exposure_query():
    for text in ("how much time do I have", "what's my exposure", "am I safe", "how many points am I on",
                 "how long can I keep going"):
        check(text, "exposure_query")
    check("how long can I use the breaker", "exposure_query", tool_id="breaker_hammer")


def test_confirm_reject():
    check("yes that was the tool", "confirm_session", tool_session_id="TS-9")
    check("confirm", "confirm_session", tool_session_id="TS-9")
    check("no, that wasn't the tool", "reject_session", tool_session_id="TS-9")
    check("that wasn't a tool", "reject_session", tool_session_id="TS-9")
    check("yes", "confirm_session", tool_session_id="TS-9")
    check("no", "reject_session", tool_session_id="TS-9")
    check("confirm", "confirm_session", ctx=NO_SESSION, clarify=True)
    assert parse_intent(w("yes"), NO_SESSION)["intent"] == "none"


def test_old_unresolved_sessions_are_out_of_reach():
    old = dict(CTX, tool_sessions=[{"id": "TS-1035", "status": "unresolved", "ts": NOW - 3600, "wall_end": NOW - 3500}])
    check("confirm", "confirm_session", ctx=old, clarify=True)                       # 14:2x session: no slot
    assert parse_intent("yes", dict(old, awaiting_confirmation=True))["ignore"] is True
    told_none = dict(CTX, unresolved_session=None)                                  # backend says: nothing recent
    check("yes that was the tool", "confirm_session", ctx=told_none, clarify=True)
    check("confirm", "confirm_session", ctx=dict(CTX, unresolved_session={"id": "TS-77"}), tool_session_id="TS-77")


def test_job_complete():
    check("idler replaced", "job_complete", work_order_id="WO-1", station_id="S3")
    check("job done at station five", "job_complete", work_order_id="WO-2", station_id="S5")
    check("finished the repair", "job_complete", work_order_id="WO-1")
    check("it's done", "job_complete", work_order_id="WO-1")
    check("job done", "job_complete", ctx=dict(CTX, work_orders=[]), clarify=True)


def test_isolation_break_help():
    check("conveyor isolated", "isolation_claim", station_id="S3", work_order_id="WO-1")
    check("conveyor's isolated", "isolation_claim")
    check("I've locked out the conveyor at station five", "isolation_claim", station_id="S5", work_order_id="WO-2")
    assert parse_intent(w("the conveyor is not isolated"), CTX)["intent"] != "isolation_claim"
    for text in ("taking a break", "going on lunch", "taking five"):
        check(text, "take_break")
    check("taking a ten minute break", "take_break", minutes=10)
    check("what can I say", "help")
    check("help", "help")


def test_s2_context_shape():
    check("done with it", "tool_return", ctx=S2_CTX, tool_id="hammer_drill")
    check("that wasn't the tool", "reject_session", ctx=S2_CTX, tool_session_id="TS-42")
    check("job done", "job_complete", ctx=S2_CTX, work_order_id="WO-2")
    say = respond({"intent": "status_query"}, {"ok": True}, S2_CTX)
    assert say.startswith("You're at 12 points, below the 100-point action value.") and "hammer drill" in say, say
    assert respond({"name": "tool_checkout", "slots": {"tool_id": "drill"}}, {"ok": False, "message": "Band not connected."},
                   S2_CTX) == "Band not connected."


def test_exposure_uses_tool_in_hand():
    fresh = dict(CTX, exposure={"total_points": 0, "basis_tool_id": None},
                 assignment={"id": "TA-1", "tool": {"id": "hammer_drill"}})
    p = parse_intent(w("How long can I keep going?"), fresh)
    assert p["intent"] == "exposure_query", p
    say = respond(p, {}, fresh)
    assert say == "You're at 0 points, below the 100-point action value. About 24 minutes left with the hammer drill " \
                  "before 100.", say
    assert "hammer drill" in respond(p, {}, dict(fresh, assignment=None, active_tool={"id": "hammer_drill"}))
    assert "breaker hammer" in respond(parse_intent(w("how long can I use the breaker"), fresh), {}, fresh)


def test_find_tools():
    assert find_tools("taking the hammer drill", TOOLS) == (["hammer_drill"], [])
    named, generic = find_tools("grab the hammer", TOOLS)
    assert named == [] and set(generic) == {"hammer_drill", "breaker_hammer", "chipping_hammer"}


def test_respond():
    say = respond(parse_intent(w("what's my exposure"), CTX), {}, CTX)
    assert say.startswith("You're at 2.5 points, below the 100-point action value.") and "impact wrench" in say, say
    say = respond(parse_intent(w("taking the hammer drill"), CTX), {}, CTX)
    assert say.startswith("Hammer drill assigned to you. I'll time it from your band.") and "minutes" in say, say
    say = respond(parse_intent(w("idler replaced"), CTX), {}, CTX)
    assert say == "WO-1 marked awaiting supervisor verification. BeltGuard never closes work on its own.", say
    assert "authorized person" in respond(parse_intent(w("conveyor isolated"), CTX), {}, CTX)
    assert respond(parse_intent(w("taking the hammer"), CTX), {}, CTX).startswith("Which one")
    ok = {"status": "ok", "defect": {"station_id": "S3", "component": "Return idler", "condition": "seized"},
          "work_order": {"id": "WO-7"}}
    say = respond(parse_intent("return idler seized at station 3", CTX), ok, CTX)
    assert say == "Logged seized return idler at station 3, return idlers. Work order WO-7 drafted for review.", say
    assert respond({"intent": "report_defect"}, {"status": "ignored", "reason": "all clear"}, CTX) == "Noted, all clear."
    assert respond({"intent": "report_defect"}, {"status": "ignored", "reason": "chat"}, CTX) == ""
    assert respond({"intent": "report_defect"}, {"status": "clarify", "question": "Which station?"}, CTX) == "Which station?"
    over = dict(CTX, exposure={"total_points": 150})
    assert "over the 100-point action value" in respond({"intent": "exposure_query"}, {}, over)
    assert respond({"intent": "tool_return", "slots": {"tool_id": "drill"}}, {"points_with_this_tool": 0.42}, CTX) \
        == "Drill returned. 0.4 points with it today."
    assert respond({"intent": "tool_checkout"}, {"error": "no band"}, CTX).startswith("Sorry")


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
