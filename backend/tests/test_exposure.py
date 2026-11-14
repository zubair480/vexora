"""be-hav: exposure maths, honesty rules, event path, tool profiles.

Every expected number is derived by hand in a comment:
    points = 2 * a_hv^2 * hours          A(8) = 2.5 * sqrt(points / 100)
Seeded tool values used below: impact_wrench 8.0, angle_grinder 6.0, low_vibration_wrench 4.0,
needle_scaler None.
"""
import math

import pytest

from backend import exposure, tool_profiles
from backend.models import Event

SHIFT = "shift-test"
RESULT_KEYS = {
    "label", "shift_id", "total_points", "a8", "level", "status", "entries", "reasons",
    "confirmed_points", "inferred_points", "confirmed_s", "inferred_s", "excluded_sessions",
    "contains_simulated", "sources", "action_points", "limit_points", "session_source", "computed_at",
}
ENTRY_KEYS = {
    "entry_id", "kind", "session_id", "tool_id", "tool_name", "a_hv_ms2", "a_hv_source",
    "duration_s", "gap_s", "trigger_s", "duration_basis", "confirmed_s", "inferred_s", "confidence",
    "completeness", "confirmation", "source", "simulated", "points", "status", "reasons",
}


@pytest.fixture(autouse=True)
def no_toolsense(monkeypatch):
    """Default: the be-toolsense module is not reachable (as in this branch)."""
    monkeypatch.setattr(exposure, "_toolsense_sessions", lambda shift_id: None)


def fake_sessions(monkeypatch, sessions):
    monkeypatch.setattr(exposure, "_toolsense_sessions", lambda shift_id: [dict(s) for s in sessions])


def session(session_id, tool_id, duration_s, **extra):
    base = {
        "session_id": session_id, "start_ts": 1000.0, "end_ts": 1000.0 + duration_s,
        "duration_s": duration_s, "tool_id": tool_id, "confidence": 0.9, "completeness": 1.0,
        "confirmation": "confirmed", "source": "band",
    }
    base.update(extra)
    return base


# ------------------------------------------------------------ formulas

def test_hse_reference_points():
    # 2.5 m/s2 for 8 h: 2 * 6.25 * 8 = 100 points -> A(8) = 2.5 * sqrt(1) = 2.5 (action value)
    assert exposure.points(2.5, 8 * 3600) == pytest.approx(100.0)
    assert exposure.a8(100.0) == pytest.approx(2.5)
    # 5 m/s2 for 8 h: 2 * 25 * 8 = 400 points -> A(8) = 2.5 * sqrt(4) = 5.0 (limit value)
    assert exposure.points(5.0, 8 * 3600) == pytest.approx(400.0)
    assert exposure.a8(400.0) == pytest.approx(5.0)
    # 10 m/s2 for 30 min: 2 * 100 * 0.5 = 100 points
    assert exposure.points(10.0, 30 * 60) == pytest.approx(100.0)


def test_more_hand_derived_points():
    # 8 m/s2 for 25 min: 2 * 64 * (25/60) = 128 * 0.416667 = 53.3333
    assert exposure.points(8.0, 25 * 60) == pytest.approx(53.3333, abs=1e-4)
    # 4 m/s2 for 1 h: 2 * 16 * 1 = 32
    assert exposure.points(4.0, 3600) == pytest.approx(32.0)
    # 6 m/s2 for 15 min: 2 * 36 * 0.25 = 18
    assert exposure.points(6.0, 15 * 60) == pytest.approx(18.0)
    # zero trigger time = 0 points; A(8) of 0 = 0; A(8) of 25 points = 2.5 * 0.5 = 1.25
    assert exposure.points(8.0, 0) == 0.0
    assert exposure.a8(0.0) == 0.0
    assert exposure.a8(25.0) == pytest.approx(1.25)


@pytest.mark.parametrize("a, seconds", [(-1.0, 60), (8.0, -1), (float("nan"), 60), (8.0, float("inf"))])
def test_points_rejects_bad_input(a, seconds):
    with pytest.raises(ValueError):
        exposure.points(a, seconds)


def test_level_thresholds():
    assert exposure.level(None) == "unknown"
    assert exposure.level(0.0) == "below_action"
    assert exposure.level(99.99) == "below_action"
    assert exposure.level(100.0) == "above_action"
    assert exposure.level(399.99) == "above_action"
    assert exposure.level(400.0) == "above_limit"


# ------------------------------------------------------------ compute

def test_sum_of_confirmed_sessions(tmp_db, monkeypatch):
    fake_sessions(monkeypatch, [
        session("s1", "impact_wrench", 1500),          # 8 m/s2, 25 min: 2*64*(1500/3600) = 53.3333
        session("s2", "angle_grinder", 900),           # 6 m/s2, 15 min: 2*36*0.25        = 18
        session("s3", "low_vibration_wrench", 3600),   # 4 m/s2, 1 h:    2*16*1           = 32
    ])
    result = exposure.compute(SHIFT)
    # 53.3333 + 18 + 32 = 103.3333 -> 103.33; A(8) = 2.5 * sqrt(1.033333) = 2.5 * 1.016530 = 2.5413
    assert result["total_points"] == pytest.approx(103.33)
    assert result["a8"] == pytest.approx(2.5413, abs=1e-3)
    assert result["level"] == "above_action"
    assert result["status"] == "ok"
    assert result["session_source"] == "tool_sessions"
    assert result["confirmed_points"] == pytest.approx(103.33)
    assert result["inferred_points"] == 0
    assert result["confirmed_s"] == 6000 and result["inferred_s"] == 0
    assert [e["points"] for e in result["entries"]] == [pytest.approx(53.33), 18.0, 32.0]
    assert set(result) >= RESULT_KEYS
    for entry in result["entries"]:
        assert set(entry) >= ENTRY_KEYS
        assert entry["status"] == "ok" and entry["duration_basis"] == "confirmed"
        assert entry["a_hv_source"] == tool_profiles.EXAMPLE_SOURCE


def test_missing_tool_value_is_never_invented(tmp_db, monkeypatch):
    fake_sessions(monkeypatch, [
        session("none", None, 600),
        session("unknown", "mystery_tool", 600),
        session("novalue", "needle_scaler", 600),
    ])
    result = exposure.compute(SHIFT)
    assert len(result["entries"]) == 3
    for entry in result["entries"]:
        assert entry["points"] is None
        assert entry["a_hv_ms2"] is None
        assert entry["status"] == "needs_confirmation"
        assert entry["reasons"]
    # entries exist but none has points: no number at all
    assert result["status"] == "unavailable"
    assert result["total_points"] is None and result["a8"] is None and result["level"] == "unknown"

    # alongside one usable session the total is a flagged partial figure
    fake_sessions(monkeypatch, [session("ok", "angle_grinder", 900), session("novalue", "needle_scaler", 600)])
    result = exposure.compute(SHIFT)
    assert result["total_points"] == pytest.approx(18.0)   # 2*36*0.25 = 18, nothing added for the scaler
    assert result["status"] == "needs_confirmation"
    assert any("partial" in reason for reason in result["reasons"])


def test_gap_time_is_not_exposure_time(tmp_db, monkeypatch):
    fake_sessions(monkeypatch, [session("g1", "impact_wrench", 600, gap_s=120)])
    result = exposure.compute(SHIFT)
    entry = result["entries"][0]
    # 600 s - 120 s gap = 480 s: 2 * 64 * (480/3600) = 128 * 0.133333 = 17.0667 -> 17.07
    assert entry["trigger_s"] == 480 and entry["gap_s"] == 120
    assert result["total_points"] == pytest.approx(17.07)
    # same with the alternative key name
    fake_sessions(monkeypatch, [session("g2", "impact_wrench", 600, data_gap_s=120)])
    assert exposure.compute(SHIFT)["total_points"] == pytest.approx(17.07)
    # a classifier trigger estimate can never exceed duration - gap (600 - 120 = 480 s)
    fake_sessions(monkeypatch, [session("g3", "impact_wrench", 600, gap_s=120, trigger_s=5000)])
    assert exposure.compute(SHIFT)["entries"][0]["trigger_s"] == 480
    # a classifier trigger estimate below the connected time is used as given (gap not subtracted twice)
    fake_sessions(monkeypatch, [session("g5", "impact_wrench", 600, gap_s=120, trigger_s=300)])
    assert exposure.compute(SHIFT)["entries"][0]["trigger_s"] == 300
    # gap longer than the session: floored at 0 s -> 0 points
    fake_sessions(monkeypatch, [session("g4", "impact_wrench", 600, gap_s=900)])
    result = exposure.compute(SHIFT)
    assert result["entries"][0]["trigger_s"] == 0 and result["total_points"] == 0


def test_wrist_acceleration_is_never_used(tmp_db, monkeypatch):
    plain = session("w1", "impact_wrench", 1500)
    fake_sessions(monkeypatch, [plain])
    baseline = exposure.compute(SHIFT)
    fake_sessions(monkeypatch, [dict(plain, accel_rms_g=3.7, peak_g=15.9, a_hv_ms2=99.0, gyro_rms=400.0)])
    with_accel = exposure.compute(SHIFT)
    # still 8 m/s2 from the profile: 2*64*(1500/3600) = 53.33
    assert baseline["total_points"] == with_accel["total_points"] == pytest.approx(53.33)
    assert with_accel["entries"][0]["a_hv_ms2"] == 8.0
    assert "accel_rms_g" not in with_accel["entries"][0] and "peak_g" not in with_accel["entries"][0]

    # acceleration present but no tool value: still no number
    fake_sessions(monkeypatch, [
        session("w2", None, 600, accel_rms_g=3.7, peak_g=15.9),
        session("w3", "needle_scaler", 600, accel_rms_g=3.7, peak_g=15.9),
    ])
    result = exposure.compute(SHIFT)
    assert [e["points"] for e in result["entries"]] == [None, None]
    assert [e["a_hv_ms2"] for e in result["entries"]] == [None, None]
    assert result["total_points"] is None


def test_rejected_sessions_are_excluded(tmp_db, monkeypatch):
    fake_sessions(monkeypatch, [
        session("keep", "angle_grinder", 900),
        session("drop", "impact_wrench", 1500, confirmation="rejected"),
    ])
    result = exposure.compute(SHIFT)
    assert [e["session_id"] for e in result["entries"]] == ["keep"]
    assert result["excluded_sessions"] == 1
    assert result["total_points"] == pytest.approx(18.0)   # only the grinder: 2*36*0.25
    assert result["status"] == "ok"


def test_low_completeness_gives_no_number(tmp_db, monkeypatch):
    fake_sessions(monkeypatch, [
        session("good", "angle_grinder", 900, completeness=0.8),      # exactly 0.8 is usable
        session("partial", "impact_wrench", 1500, completeness=0.79),
        session("bad", "impact_wrench", 1500, completeness=0.49),
        session("unknown", "impact_wrench", 1500, completeness=None),
    ])
    result = exposure.compute(SHIFT)
    by_id = {e["session_id"]: e for e in result["entries"]}
    assert by_id["good"]["points"] == pytest.approx(18.0) and by_id["good"]["status"] == "ok"
    assert by_id["partial"]["points"] is None and by_id["partial"]["status"] == "needs_confirmation"
    assert by_id["bad"]["points"] is None and by_id["bad"]["status"] == "unavailable"
    assert by_id["unknown"]["points"] is None and by_id["unknown"]["status"] == "needs_confirmation"
    assert result["total_points"] == pytest.approx(18.0)   # only the usable session counts
    assert result["status"] == "needs_confirmation"


def test_mostly_tentative_time_needs_confirmation(tmp_db, monkeypatch):
    fake_sessions(monkeypatch, [
        session("t1", "angle_grinder", 900, confirmation="tentative"),   # 2*36*0.25 = 18 (inferred)
        session("c1", "impact_wrench", 600),                             # 2*64*(600/3600) = 21.3333
    ])
    result = exposure.compute(SHIFT)
    # inferred share of counted time = 900 / (900 + 600) = 0.6 > 0.5
    assert result["status"] == "needs_confirmation"
    assert result["total_points"] == pytest.approx(39.33)   # 18 + 21.3333, still counted but flagged
    assert result["inferred_points"] == pytest.approx(18.0)
    assert result["confirmed_points"] == pytest.approx(21.33)
    assert result["inferred_s"] == 900 and result["confirmed_s"] == 600
    assert result["entries"][0]["duration_basis"] in ("inferred", "confirmed")
    assert {e["session_id"]: e["duration_basis"] for e in result["entries"]} == {"t1": "inferred", "c1": "confirmed"}

    fake_sessions(monkeypatch, [
        session("t2", "impact_wrench", 600, confirmation="tentative"),   # 21.3333 (inferred)
        session("c2", "angle_grinder", 900),                             # 18
    ])
    result = exposure.compute(SHIFT)
    # inferred share = 600 / 1500 = 0.4 <= 0.5
    assert result["status"] == "ok"
    assert result["total_points"] == pytest.approx(39.33)

    # missing confirmation is treated as tentative: 100 % inferred
    only = session("t3", "angle_grinder", 900)
    del only["confirmation"]
    fake_sessions(monkeypatch, [only])
    result = exposure.compute(SHIFT)
    assert result["status"] == "needs_confirmation" and result["total_points"] == pytest.approx(18.0)


def test_no_source_is_unavailable(tmp_db):
    result = exposure.compute(SHIFT)
    assert result["status"] == "unavailable"
    assert result["total_points"] is None and result["a8"] is None
    assert result["level"] == "unknown"
    assert result["session_source"] == "none"
    assert result["entries"] == [] and result["reasons"]
    assert result["label"] == exposure.LABEL


def test_reachable_source_with_no_sessions_is_zero_ok(tmp_db, monkeypatch):
    fake_sessions(monkeypatch, [])
    result = exposure.compute(SHIFT)
    assert result["status"] == "ok"
    assert result["total_points"] == 0 and result["a8"] == 0 and result["level"] == "below_action"


def test_simulated_sessions_are_labelled(tmp_db, monkeypatch):
    fake_sessions(monkeypatch, [
        session("r1", "angle_grinder", 900, source="replay"),
        session("b1", "angle_grinder", 900, source="band"),
    ])
    result = exposure.compute(SHIFT)
    by_id = {e["session_id"]: e for e in result["entries"]}
    assert by_id["r1"]["simulated"] is True and by_id["r1"]["source"] == "replay"
    assert by_id["b1"]["simulated"] is False
    assert result["contains_simulated"] is True
    assert result["sources"] == ["band", "replay"]
    assert exposure._event_source(result) == "replay"

    fake_sessions(monkeypatch, [
        session("r1", "angle_grinder", 900, source="replay"),
        session("y1", "angle_grinder", 900, source="synthetic"),
    ])
    result = exposure.compute(SHIFT)
    assert all(e["simulated"] for e in result["entries"])
    assert exposure._event_source(result) == "synthetic"

    fake_sessions(monkeypatch, [session("b1", "angle_grinder", 900)])
    result = exposure.compute(SHIFT)
    assert result["contains_simulated"] is False
    assert exposure._event_source(result) == "derived"


def test_every_result_carries_the_label(test_bus, monkeypatch):
    assert exposure.LABEL == "Estimated HAV exposure — screening/planning, not certified measurement"
    assert exposure.compute(SHIFT)["label"] == exposure.LABEL                       # unavailable
    assert exposure.recompute(SHIFT)["label"] == exposure.LABEL
    assert exposure.add_manual(SHIFT, "angle_grinder", 15)["label"] == exposure.LABEL
    fake_sessions(monkeypatch, [session("s1", "impact_wrench", 1500), session("s2", None, 60)])
    assert exposure.compute(SHIFT)["label"] == exposure.LABEL                       # needs_confirmation
    test_bus.drain()


# ------------------------------------------------------------ manual

def test_manual_entry_is_counted(test_bus):
    result = exposure.add_manual(SHIFT, "impact_wrench", 25, note="idler bolts")
    # 8 m/s2 for 25 min: 2 * 64 * (25/60) = 53.3333 -> 53.33
    assert result["total_points"] == pytest.approx(53.33)
    assert result["status"] == "ok" and result["level"] == "below_action"
    entry = result["entries"][0]
    assert entry["kind"] == "manual" and entry["source"] == "manual"
    assert entry["duration_basis"] == "manual" and entry["trigger_s"] == 1500
    assert entry["simulated"] is False and entry["completeness"] is None
    assert result["confirmed_s"] == 1500 and result["inferred_s"] == 0

    # a second manual entry adds: grinder 6 m/s2 for 15 min = 18 -> 71.33
    result = exposure.add_manual(SHIFT, "angle_grinder", 15)
    assert result["total_points"] == pytest.approx(71.33)
    assert exposure.compute(SHIFT)["total_points"] == pytest.approx(71.33)
    # another shift is untouched
    assert exposure.compute("other-shift")["status"] == "unavailable"
    test_bus.drain()


def test_manual_entry_still_needs_a_tool_value(test_bus):
    result = exposure.add_manual(SHIFT, "needle_scaler", 10)
    assert result["entries"][0]["points"] is None
    assert result["entries"][0]["status"] == "needs_confirmation"
    assert result["total_points"] is None and result["status"] == "unavailable"
    result = exposure.add_manual(SHIFT, None, 10)
    assert [e["points"] for e in result["entries"]] == [None, None]
    test_bus.drain()


@pytest.mark.parametrize("minutes", [0, -5, float("nan"), float("inf"), None, "ten", True])
def test_manual_rejects_invalid_minutes(test_bus, minutes):
    with pytest.raises(ValueError):
        exposure.add_manual(SHIFT, "impact_wrench", minutes)
    assert exposure.compute(SHIFT)["entries"] == []


# ------------------------------------------------------------ events

def ended(session_id, tool_id, duration_s, confirmation="confirmed", source="band", **extra):
    payload = {
        "session_id": session_id, "start_ts": 1000.0, "end_ts": 1000.0 + duration_s,
        "duration_s": duration_s, "tool_id": tool_id, "confidence": 0.9, "completeness": 1.0,
        "confirmation": confirmation,
    }
    payload.update(extra)
    return Event("tool_session_ended", payload, source, SHIFT, "worker-1")


def drain(bus):
    bus.drain()
    bus.drain()


def test_event_path_publishes_exposure_updated(test_bus):
    seen = []
    test_bus.subscribe(seen.append, types=["exposure_updated"])
    exposure.install(test_bus)
    exposure.install(test_bus)   # idempotent: still one subscription, one update per event

    test_bus.publish(ended("ev1", "impact_wrench", 1500))
    drain(test_bus)
    assert len(seen) == 1
    event = seen[0]
    assert event.type == "exposure_updated" and event.shift_id == SHIFT
    assert event.source == "derived"
    assert {"total_points", "a8", "level", "entries", "status"} <= set(event.payload)
    assert event.payload["label"] == exposure.LABEL
    # 8 m/s2 for 25 min = 53.33
    assert event.payload["total_points"] == pytest.approx(53.33)
    assert event.payload["status"] == "ok" and event.payload["level"] == "below_action"
    assert event.payload["session_source"] == "events"
    assert event.payload["entries"][0]["source"] == "band"

    # second session: grinder 15 min = 18 -> 71.33
    test_bus.publish(ended("ev2", "angle_grinder", 900))
    drain(test_bus)
    assert len(seen) == 2
    assert seen[1].payload["total_points"] == pytest.approx(71.33)

    # the first session is later rejected: same session_id replaces the row and leaves the total
    test_bus.publish(ended("ev1", "impact_wrench", 1500, confirmation="rejected"))
    drain(test_bus)
    assert len(seen) == 3
    assert seen[2].payload["total_points"] == pytest.approx(18.0)
    assert seen[2].payload["excluded_sessions"] == 1
    assert [e["session_id"] for e in seen[2].payload["entries"]] == ["ev2"]
    assert exposure.compute(SHIFT)["total_points"] == pytest.approx(18.0)


def test_event_source_is_carried_for_replay(test_bus):
    seen = []
    test_bus.subscribe(seen.append, types=["exposure_updated"])
    exposure.install(test_bus)
    test_bus.publish(ended("rp1", "angle_grinder", 900, source="replay"))
    drain(test_bus)
    assert len(seen) == 1
    assert seen[0].source == "replay"                       # never presented as live
    assert seen[0].payload["contains_simulated"] is True
    entry = seen[0].payload["entries"][0]
    assert entry["simulated"] is True and entry["source"] == "replay"
    assert seen[0].payload["total_points"] == pytest.approx(18.0)

    test_bus.publish(ended("sy1", "angle_grinder", 900, source="synthetic"))
    drain(test_bus)
    assert seen[-1].source == "synthetic"


def test_toolsense_list_wins_over_stored_event(test_bus, monkeypatch):
    exposure.install(test_bus)
    test_bus.publish(ended("m1", None, 900, confirmation="tentative", source="replay"))
    drain(test_bus)
    assert exposure.compute(SHIFT)["total_points"] is None   # no tool yet: nothing invented
    # be-toolsense now reports the same session confirmed with a tool (no source key of its own)
    confirmed = session("m1", "angle_grinder", 900)
    del confirmed["source"]
    fake_sessions(monkeypatch, [confirmed])
    result = exposure.compute(SHIFT)
    assert len(result["entries"]) == 1
    assert result["total_points"] == pytest.approx(18.0)     # 2*36*0.25
    assert result["entries"][0]["source"] == "replay"        # provenance kept from the event
    assert result["contains_simulated"] is True


def test_subscriber_never_raises(test_bus):
    seen = []
    test_bus.subscribe(seen.append, types=["exposure_updated"])
    exposure.install(test_bus)
    test_bus.publish(Event("tool_session_ended", {"duration_s": 60}, "band", SHIFT, "worker-1"))
    drain(test_bus)
    assert seen == []                                        # no session_id: ignored, bus still alive
    test_bus.publish(ended("ok1", "angle_grinder", 900))
    drain(test_bus)
    assert len(seen) == 1


# ------------------------------------------------------------ tool profiles

def test_seeded_profiles(tmp_db):
    profiles = {p.tool_id: p for p in tool_profiles.list_profiles()}
    assert profiles["impact_wrench"].a_hv_ms2 == 8.0
    assert profiles["angle_grinder"].a_hv_ms2 == 6.0
    assert profiles["low_vibration_wrench"].a_hv_ms2 == 4.0
    assert profiles["needle_scaler"].a_hv_ms2 is None
    assert profiles["impact_wrench"].source == tool_profiles.EXAMPLE_SOURCE
    assert profiles["needle_scaler"].source == tool_profiles.NO_VALUE_SOURCE
    assert tool_profiles.get(None) is None
    assert tool_profiles.get("mystery_tool") is None
    assert set(tool_profiles.get("impact_wrench").to_dict()) == {"tool_id", "name", "a_hv_ms2", "source", "updated_at"}


def test_profile_upsert_validation(tmp_db):
    for bad in (0, -2.0, float("nan"), float("inf"), "8", True):
        with pytest.raises(ValueError):
            tool_profiles.upsert("drill", "Drill", bad, "Manufacturer declared")
    with pytest.raises(ValueError):
        tool_profiles.upsert("drill", "Drill", 5.0)            # a value needs a source
    with pytest.raises(ValueError):
        tool_profiles.upsert("drill", "Drill", 5.0, "   ")
    with pytest.raises(ValueError):
        tool_profiles.upsert("", "Drill", 5.0, "Manufacturer declared")
    assert tool_profiles.get("drill") is None                  # nothing stored by failed calls

    profile = tool_profiles.upsert("drill", "Drill", 5.0, "Manufacturer declared")
    assert profile.a_hv_ms2 == 5.0 and profile.source == "Manufacturer declared"
    assert tool_profiles.get("drill").a_hv_ms2 == 5.0
    # removing the value replaces the source, whatever was passed
    profile = tool_profiles.upsert("drill", "Drill", None, "Manufacturer declared")
    assert profile.a_hv_ms2 is None and profile.source == tool_profiles.NO_VALUE_SOURCE
    assert tool_profiles.get("drill").source == tool_profiles.NO_VALUE_SOURCE


def test_user_edit_of_seed_survives_later_access(tmp_db, monkeypatch):
    tool_profiles.upsert("impact_wrench", "Impact wrench", 5.5, "Manufacturer declared value")
    tool_profiles.upsert("needle_scaler", "Needle scaler", 12.0, "Measured on site")
    tool_profiles.list_profiles()                              # re-runs the INSERT OR IGNORE seed
    assert tool_profiles.get("impact_wrench").a_hv_ms2 == 5.5
    assert tool_profiles.get("impact_wrench").source == "Manufacturer declared value"
    assert tool_profiles.get("needle_scaler").a_hv_ms2 == 12.0
    assert tool_profiles.get("angle_grinder").a_hv_ms2 == 6.0  # untouched seed still there
    # the edited value is what exposure uses: 5.5 m/s2 for 1 h = 2 * 30.25 * 1 = 60.5
    fake_sessions(monkeypatch, [session("e1", "impact_wrench", 3600)])
    result = exposure.compute(SHIFT)
    assert result["total_points"] == pytest.approx(60.5)
    assert result["entries"][0]["a_hv_source"] == "Manufacturer declared value"
    assert math.isclose(result["a8"], 2.5 * math.sqrt(0.605), abs_tol=1e-3)   # 2.5 * 0.7778 = 1.9445


# ------------------------------------------------------------------ review fixes

def test_text_gap_and_capitalised_rejection_are_not_counted(tmp_db, monkeypatch):
    # gap given as text "300": 900 s - 300 s = 600 s at 8 m/s2 = 2 * 64 * 600/3600 = 21.33
    fake_sessions(monkeypatch, [session("t1", "impact_wrench", 900, gap_s="300")])
    result = exposure.compute(SHIFT)
    assert result["entries"][0]["trigger_s"] == 600
    assert result["total_points"] == pytest.approx(21.33)
    # unreadable gap: no number rather than counting the whole session
    fake_sessions(monkeypatch, [session("t2", "impact_wrench", 900, gap_s="some")])
    entry = exposure.compute(SHIFT)["entries"][0]
    assert entry["points"] is None and entry["status"] == "needs_confirmation"
    # "Rejected" in any capitalisation is excluded
    fake_sessions(monkeypatch, [session("t3", "impact_wrench", 900, confirmation="Rejected")])
    result = exposure.compute(SHIFT)
    assert result["entries"] == [] and result["excluded_sessions"] == 1


def test_unknown_provenance_is_flagged_not_assumed_live(tmp_db, monkeypatch):
    plain = session("p1", "impact_wrench", 900)
    plain.pop("source", None)
    fake_sessions(monkeypatch, [plain])
    result = exposure.compute(SHIFT)
    assert result["entries"][0]["source"] == "unknown"
    assert result["provenance_unknown"] is True
    assert any("provenance" in r.lower() for r in result["reasons"])


def test_event_provenance_beats_a_source_inside_the_payload(test_bus):
    seen = []
    test_bus.subscribe(seen.append, types=["exposure_updated"])
    exposure.install(test_bus)
    test_bus.publish(Event("tool_session_ended", {
        "session_id": "e1", "duration_s": 900.0, "tool_id": "impact_wrench", "confidence": 0.9,
        "completeness": 0.99, "confirmation": "confirmed", "source": "band"},
        "replay", SHIFT, "worker-1"))
    for _ in range(2):
        test_bus.drain()
    assert seen and seen[-1].source == "replay"
    assert seen[-1].payload["contains_simulated"] is True
    assert seen[-1].payload["entries"][0]["source"] == "replay"


def test_simulated_entry_without_points_still_marks_the_event(test_bus):
    seen = []
    test_bus.subscribe(seen.append, types=["exposure_updated"])
    exposure.install(test_bus)
    test_bus.publish(Event("tool_session_ended", {
        "session_id": "s1", "duration_s": 900.0, "tool_id": None, "confidence": 0.9,
        "completeness": 0.99, "confirmation": "tentative"}, "synthetic", SHIFT, "worker-1"))
    for _ in range(2):
        test_bus.drain()
    assert seen and seen[-1].source == "synthetic"


def test_rounding_never_shows_a_threshold_that_was_not_reached(tmp_db, monkeypatch):
    # 8 m/s2 for 2812.4 s: 2 * 64 * 2812.4/3600 = 99.9964 -> would round to 100.0, but the
    # action value is not reached, so the displayed figure stays below it.
    fake_sessions(monkeypatch, [session("r1", "impact_wrench", 2812.4)])
    result = exposure.compute(SHIFT)
    assert result["level"] == "below_action"
    assert result["total_points"] == 99.99
    assert result["total_points_exact"] == pytest.approx(99.9964, abs=1e-4)
