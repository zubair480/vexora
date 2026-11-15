"""Tests for S4 hav-brain: exposure.py, tool_profiles.json, inspection.py, planner.py.

    python backend/test_brain.py          (or: python -m pytest backend/test_brain.py)

The rules path always runs. The live OpenAI test runs only when OPENAI_API_KEY is set
(environment or beltguard/.env); otherwise it prints SKIP.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import exposure  # noqa: E402
import inspection  # noqa: E402
import planner  # noqa: E402

STATIONS = [
    {"id": "S1", "number": 1, "name": "Tail pulley & loading zone", "pos": 0.08, "status": "ok"},
    {"id": "S2", "number": 2, "name": "Carry idlers & belt tracking", "pos": 0.30, "status": "ok"},
    {"id": "S3", "number": 3, "name": "Return idlers", "pos": 0.52, "status": "ok"},
    {"id": "S4", "number": 4, "name": "Take-up & belt tension", "pos": 0.74, "status": "ok"},
    {"id": "S5", "number": 5, "name": "Head/drive pulley & discharge", "pos": 0.94, "status": "ok"},
]
CONVEYOR = {"id": "CV-102", "name": "Overland conveyor CV-102"}
WORKER = {"id": "W-01", "name": "Technician"}
EXAMPLE = "Conveyor CV-102, station three, return idler seized, needs replacement."


def rules(text, **kw):
    return inspection.extract_defect(text, STATIONS, CONVEYOR, WORKER, force_rules=True, **kw)


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol


# ---------------------------------------------------------------- exposure

def test_points_math():
    assert close(exposure.points(2.5, 8), 100)        # A(8) 2.5 m/s2 = EAV
    assert close(exposure.points(5.0, 8), 400)        # A(8) 5 m/s2 = ELV
    assert close(exposure.points(10, 0.5), 100)
    assert close(exposure.points(5, 4 / 60), 2 * 25 * 4 / 60)
    assert exposure.points(7, 0) == 0
    assert close(exposure.minutes_for_points(10, 100), 30)
    assert close(exposure.minutes_for_points(5, 400), 480)
    assert exposure.minutes_for_points(0, 10) == math.inf
    assert close(exposure.a8_from_points(100), 2.5) and close(exposure.a8_from_points(400), 5.0)
    for bad in (-1, float("nan"), "x"):
        try:
            exposure.points(bad, 1)
        except ValueError:
            pass
        else:
            raise AssertionError("points accepted %r" % bad)


def test_daily_summary():
    empty = exposure.daily_summary([])
    assert empty["total_points"] == 0 and empty["status"] == "below_action" and empty["minutes_to_action"] is None
    assert "SCREENING" in empty["label"]
    s = exposure.daily_summary([{"tool_id": "impact_wrench", "vibration_m_s2": 5, "trigger_s": 600, "points": None},
                                {"tool_id": "chipping_hammer", "points": 95.0, "vibration_m_s2": 20, "status": "confirmed"}])
    assert close(s["total_points"], round(2 * 25 * (600 / 3600) + 95, 2)) and s["status"] == "above_action"
    assert s["minutes_to_action"] == 0 and s["basis_vibration_m_s2"] == 20
    assert close(s["minutes_to_limit"], round(60 * (400 - s["total_points"]) / (2 * 400), 1), 0.11)
    p = exposure.daily_summary([{"points": 50}, {"points": 60, "status": "unresolved"}, {"points": 999, "status": "rejected"}])
    assert p["total_points"] == 50 and p["pending_points"] == 60 and p["worst_case_points"] == 110
    assert p["status"] == "below_action" and p["worst_case_status"] == "above_action"
    assert exposure.daily_summary([{"points": 400}])["status"] == "above_limit"
    rec = exposure.exposure_record({"id": "impact_wrench", "vibration_m_s2": 5, "source": "HSE"}, 60)
    assert close(rec["points"], round(2 * 25 / 60, 3)) and rec["status"] == "confirmed"


def test_tool_profiles():
    tools = exposure.load_tool_profiles()
    ids = {t["id"] for t in tools}
    assert len(tools) >= 6, ids
    for checkout_id in ("drill", "hammer_drill", "impact_wrench", "angle_grinder", "power_jack", "breaker_hammer"):
        assert checkout_id in ids, checkout_id          # the six tools of S0's tool checkout
    assert len(ids) == len(tools), "duplicate tool ids"
    for t in tools:
        assert t["name"] and t["source"] and float(t["vibration_m_s2"]) > 0, t["id"]
        assert t.get("typical_trigger_min"), t["id"]
        alt = t.get("lower_vibration_alternative")
        if alt and alt.get("tool_id"):
            assert alt["tool_id"] in ids, (t["id"], alt["tool_id"])
    demo = [t for t in tools if t.get("demo_stand_in")]
    assert len(demo) == 1 and "DEMO" in demo[0]["source"] and demo[0]["stands_in_for"] in ids


# ---------------------------------------------------------------- inspection (rules path)

def test_example_sentence():
    r = rules(EXAMPLE)
    assert r["status"] == "ok", r
    assert r["station_id"] == "S3" and r["conveyor_id"] == "CV-102"
    assert "return idler" in r["component"].lower() and r["condition"] == "seized"
    assert r["severity"] == "high" and r["action"] == "replace" and r["source"] == "rules"
    wo = r["work_order"]
    for key in ("title", "priority", "priority_reason", "steps", "requires_isolation", "tool_id", "est_trigger_min"):
        assert key in wo, key
    assert wo["requires_isolation"] is True and wo["status"] == "draft"
    assert wo["tool_id"] == "impact_wrench" and wo["est_trigger_min"] > 0 and wo["est_points"] > 0
    assert "S3" in wo["title"] and "CV-102" in wo["title"]
    text = " ".join(wo["steps"])
    assert "verified by an authorized person" in text and "never closes" in text


def test_station_variants():
    cases = {
        "S3 return idler seized": "S3",
        "Station 3: bottom roller not turning, replace it": "S3",
        "third station, the idler bearing is screeching": "S3",
        "station tree, idler seized": "S3",
        "the take-up counterweight is jammed": "S4",
        "head pulley lagging worn out at the discharge": "S5",
        "tail pulley skirting worn": "S1",
        "carry idler at station two is seized": "S2",
        "conveyor C V one oh two, station five, gearbox leaking oil": "S5",
    }
    for text, sid in cases.items():
        r = rules(text)
        assert r["status"] == "ok" and r["station_id"] == sid, (text, r.get("station_id"), r.get("question"))


def test_clarify_paths():
    missing = rules("Found a seized idler, needs replacement.")
    assert missing["status"] == "clarify" and missing["reason"] == "station_missing" and "station" in missing["question"].lower()
    assert missing["partial"]["condition"] == "seized" and len(missing["options"]) == 5
    unknown = rules("Station seven, return idler seized.")
    assert unknown["status"] == "clarify" and unknown["reason"] == "station_unknown"
    conflict = rules("Station two, return idler seized.")
    assert conflict["status"] == "clarify" and conflict["reason"] == "station_conflict"
    ambiguous = rules("The return idler near the take-up is seized")
    assert ambiguous["status"] == "clarify" and ambiguous["reason"] == "station_ambiguous"
    wrong_conveyor = rules("CV-104 station three return idler seized")
    assert wrong_conveyor["status"] == "clarify" and wrong_conveyor["reason"] == "conveyor_mismatch"
    assert rules("")["status"] == "clarify"
    assert rules("okay, heading to the next one")["status"] == "ignored"
    assert rules("it's hot today")["status"] == "ignored"
    broken = rules("something is broken")                       # a problem word but no part: ask, don't ignore
    assert broken["status"] == "clarify" and broken["reason"] == "component_missing", broken
    assert "which station" in broken["question"]
    broken_at = rules("something is broken", active_station_id="S3")
    assert broken_at["status"] == "clarify" and broken_at["reason"] == "component_missing"
    assert "station 3" in broken_at["question"]
    assert rules("the conveyor is broken at station two")["reason"] == "component_missing"


def test_all_clear_fault_words_and_chitchat():
    clear = rules("station 3 looks fine, no damage")
    assert clear["status"] == "ignored" and clear["reason"] == "all clear", clear
    mixed = rules("return idler seized at station three, everything else is fine")
    assert mixed["status"] == "ok" and mixed["condition"] == "seized", mixed
    assert rules("no damage to the belt but the return idler at station 3 is seized")["condition"] == "seized"
    stopped = rules("the conveyor stopped working at station four")
    assert stopped["status"] == "clarify" and stopped["reason"] == "component_missing", stopped
    assert rules("station 5 gearbox failed")["condition"] == "fault"
    assert not inspection._rules_extract("checking out the angle grinder")["has_problem"]   # a tool name, not a noise
    assert not inspection._rules_extract("taking the rattle gun")["has_problem"]
    assert rules("station 2, the carry idler is grinding")["condition"] == "noisy"
    real_status, real_extract = inspection.ai_status, inspection._ai_extract

    def boom(*args):
        raise AssertionError("the model must not be called for chit-chat")

    inspection.ai_status = lambda: {"available": True, "model": "fake-model", "problem": None}
    inspection._ai_extract = boom
    try:
        r = inspection.extract_defect("okay, can you hear me", STATIONS, CONVEYOR, WORKER)
        assert r["status"] == "ignored" and r["source"] == "rules" and r["ai_error"] is None, r
    finally:
        inspection.ai_status, inspection._ai_extract = real_status, real_extract


def test_band_mic_gate():
    band = lambda text: inspection.extract_defect(text, STATIONS, CONVEYOR, WORKER, source="band", active_station_id="S1")
    for chat in ("Hi, can you hear me? Keep transcribing", "what's the band number", "let me fix that",
                 "something is broken", "it's hot in here"):
        r = band(chat)
        assert r["status"] == "ignored" and r["reason"] == "chat" and r["question"] is None, (chat, r)
    assert band(EXAMPLE)["status"] == "ok" and band(EXAMPLE)["station_id"] == "S3"
    asked = band("I want to report a defect")                     # explicit trigger passes the gate, then asks
    assert asked["status"] == "clarify" and asked["reason"] == "component_missing", asked
    assert rules("something is broken")["status"] == "clarify"    # typed stays lenient
    assert inspection.is_inspection_report("the bottom roller at three is chewing up the belt")
    woke = inspection.extract_defect("Vexora, the belt is torn at station two", STATIONS, CONVEYOR, WORKER, force_rules=True)
    assert woke["status"] == "ok" and woke["component"] == "Belt" and woke["station_id"] == "S2", woke   # not "Guard"
    assert rules("the belt guard is missing at station two")["component"] == "Guard"                     # a real part
    assert not inspection.is_inspection_report("what's the band number")


def test_report_without_condition_word():
    r = rules("Station three, return idler needs replacing")
    assert r["status"] == "ok" and r["station_id"] == "S3", r
    assert r["condition"] == "needs replacement" and r["action"] == "replace"
    hot = rules("station 5, the bearing is running hot")
    assert hot["status"] == "ok" and hot["condition"] == "overheating" and hot["severity"] == "high"


def test_ai_never_invents_component():
    real_status, real_extract = inspection.ai_status, inspection._ai_extract

    def fake(component, condition="broken"):
        return lambda text, st, conv, tools: {
            "is_defect_report": True, "conveyor_mentioned": None, "station_evidence": None, "station_number": None,
            "component": component, "condition": condition, "severity": "medium", "severity_reason": "AI",
            "action": "repair", "task_steps": ["Inspect it"], "suggested_tool_id": "none", "est_trigger_min": None,
            "summary": "AI summary"}

    inspection.ai_status = lambda: {"available": True, "model": "fake-model", "problem": None}
    try:
        inspection._ai_extract = fake("Component")
        r = inspection.extract_defect("something is broken", STATIONS, CONVEYOR, WORKER, active_station_id="S3")
        assert r["status"] == "clarify" and r["reason"] == "component_missing" and r["source"] == "ai", r
        inspection._ai_extract = fake("conveyor drive assembly")           # words the technician never said
        r = inspection.extract_defect("station three, it's making a noise", STATIONS, CONVEYOR, WORKER)
        assert r["status"] == "clarify" and r["reason"] == "component_missing" and "station 3" in r["question"], r
        inspection._ai_extract = fake("brake caliper", "cracked")           # unknown to the rules, but said
        r = inspection.extract_defect("station five, the brake caliper is cracked", STATIONS, CONVEYOR, WORKER)
        assert r["status"] == "ok" and r["component"] == "brake caliper" and r["station_id"] == "S5", r
        assert r["source"] == "ai" and r["severity"] == "high"               # rules raise "cracked" to high
    finally:
        inspection.ai_status, inspection._ai_extract = real_status, real_extract
    assert inspection._component_grounded("return idler", "station three return idler seized")
    assert not inspection._component_grounded("unknown", "something is broken")
    assert not inspection._component_grounded("something", "something is broken")


def test_active_station_fallback():
    r = rules("idler seized, needs replacing", active_station_id="S3")
    assert r["status"] == "ok" and r["station_id"] == "S3" and "selected" in r["evidence"]["station"]
    clash = rules("return idler seized", active_station_id="S2")
    assert clash["status"] == "clarify" and clash["reason"] == "station_conflict"
    spoken_wins = rules("station five gearbox leaking", active_station_id="S2")
    assert spoken_wins["status"] == "ok" and spoken_wins["station_id"] == "S5"


def test_severity_floor():
    assert rules("station 1, smoke coming off the tail pulley")["severity"] == "critical"
    assert rules("station 1, smoke coming off the tail pulley")["work_order"]["priority_code"] == "P1"
    assert rules("station 2, carry idler noisy")["severity"] == "medium"
    assert rules("station 1, build-up under the tail pulley")["work_order"]["tool_id"] == "chipping_hammer"


# ---------------------------------------------------------------- planner

def test_planner_thresholds():
    tools = exposure.load_tool_profiles()
    ok = planner.recommend(10, {"station_id": "S3", "task": "Idler change", "tool_id": "impact_wrench",
                                "est_trigger_min": 4}, tools)
    assert ok["status"] == "ok" and close(ok["added_points"], round(2 * 25 * 4 / 60, 2))
    near = planner.recommend(75, {"tool_id": "impact_wrench", "est_trigger_min": 10}, tools)
    assert near["status"] == "concern" and near["reason"] == "projected_near_action"       # 83.3 pts
    above = planner.recommend(50, {"tool_id": "chipping_hammer", "est_trigger_min": 20}, tools)
    assert above["status"] == "concern" and above["reason"] == "projected_above_action"     # 316.7 pts
    over = planner.recommend(50, {"tool_id": "needle_scaler", "est_trigger_min": 60}, tools)
    assert over["status"] == "over_limit" and close(over["projected_points"], 50 + 722)
    assert over["alternative"]["tool_id"] == "needle_scaler_vr" and close(over["alternative"]["projected_points"], 148)
    kinds = [d["kind"] for d in over["action_details"]]
    assert kinds[0] == "replan" and "lower_vibration" in kinds and "split_shifts" in kinds and "reassign" in kinds
    unknown = planner.recommend(5, {"tool_id": "laser_sword", "est_trigger_min": 5}, tools)
    assert unknown["status"] == "concern" and unknown["reason"] == "unknown_tool"
    default_time = planner.recommend(0, {"tool_id": "angle_grinder"}, tools)
    assert default_time["est_trigger_min"] == 6 and "assumption" in default_time["trigger_basis"]
    for r in (ok, near, above, over, unknown, default_time):
        assert r["requires_supervisor_review"] is True and r["automatic_actions"] == []
        assert isinstance(r["actions"], list) and all(isinstance(a, str) for a in r["actions"])
        assert r["actions"][-1].startswith("Supervisor review required")


# ---------------------------------------------------------------- live OpenAI (only with a key)

def test_live_ai():
    status = inspection.ai_status()
    if not status["available"]:
        print("    SKIP live OpenAI test: %s" % (status["problem"] or "")[:90])
        return
    r = inspection.extract_defect(EXAMPLE, STATIONS, CONVEYOR, WORKER)
    assert r["source"] == "ai", r.get("ai_error")
    assert r["status"] == "ok" and r["station_id"] == "S3", r
    print("    live OpenAI (%s, %d ms): %s | %s" % (r["model"], r["latency_ms"], r["work_order"]["title"],
                                                   r["work_order"]["priority"]))
    vague = inspection.extract_defect("Found a seized idler, needs replacement.", STATIONS, CONVEYOR, WORKER)
    assert vague["status"] == "clarify", vague  # the model may not invent a station


if __name__ == "__main__":
    failures = 0
    for name, fn in [(n, f) for n, f in list(globals().items()) if n.startswith("test_") and callable(f)]:
        try:
            fn()
            print("PASS %s" % name)
        except Exception as error:  # report every failure, then exit non-zero
            failures += 1
            print("FAIL %s: %r" % (name, error))
    print("%d failed" % failures if failures else "all passed")
    sys.exit(1 if failures else 0)
