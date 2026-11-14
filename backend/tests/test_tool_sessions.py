"""Tool session detector + live service. Synthetic signals only (source="synthetic")."""
import time

import numpy as np
import pytest

from backend import database, tool_sessions
from backend.models import ACTIVITIES, Event
from backend.tool_classifier import RuleClassifier
from backend.tool_sessions import DetectorConfig, ToolDetector, ToolSessionService, detect_sessions

from .test_tool_synth_helpers import T0, drop, make, sequence, to_batches

CONTRACT_SESSION_KEYS = {"session_id", "start_ts", "end_ts", "duration_s", "tool_id", "confidence",
                         "completeness", "confirmation"}
SHIFT = {"shift_id": "shift-test", "worker_id": "worker-test"}


class Scripted:
    """Fake classifier: the test sets the tool probability, features are ignored."""
    name = "scripted"
    info = {"model": "scripted", "trained": False}

    def __init__(self):
        self.p = 0.0

    def predict_proba(self, X):
        row = np.zeros(len(ACTIVITIES))
        row[ACTIVITIES.index("tool_use")] = self.p
        row[ACTIVITIES.index("stationary")] = 1.0 - self.p
        return np.tile(row, (len(X), 1))


def run_script(script, **cfg):
    """script[k] is the raw tool probability of the hop at T0 + 0.5 * k (k >= 1)."""
    clf, hz = Scripted(), 50
    det = ToolDetector(clf, DetectorConfig(**cfg))
    data = make("still", len(script) * 0.5, hz)
    out = []
    for k, p in enumerate(script):
        clf.p = p
        out += det.feed(data[k * 25:(k + 1) * 25])
    return out, det


def kinds(transitions, kind):
    return [t for t in transitions if t["kind"] == kind]


def run_rules(arr, **cfg):
    det = ToolDetector(RuleClassifier(), DetectorConfig(**cfg))
    out = det.feed(arr)
    return out, det


# ---- pure detector, scripted probabilities ---------------------------------------------------
def test_hysteresis_dip_between_thresholds_keeps_session():
    script = [0] * 4 + [0.9] * 10 + [0.5] * 6 + [0.9] * 4 + [0] * 6
    out, det = run_script(script, smoothing=0.0)
    started, ended = kinds(out, "session_started"), kinds(out, "session_ended")
    assert len(started) == 1 and len(ended) == 1
    assert started[0]["end_ts"] is None and started[0]["session_id"] == ended[0]["session_id"]
    # edges come from the raw probability: first tool hop (2.0 s) - 0.35, last (11.5 s) - 1.15
    assert ended[0]["start_ts"] == pytest.approx(T0 + 1.65, abs=0.03)
    assert ended[0]["end_ts"] == pytest.approx(T0 + 10.35, abs=0.03)
    assert ended[0]["duration_s"] == pytest.approx(8.7, abs=0.05) and ended[0]["missing_s"] == 0
    assert ended[0]["reason"] == "activity_stopped"
    assert 0.5 < ended[0]["confidence"] <= 0.9 and ended[0]["completeness"] > 0.9
    assert det.state()["in_session"] is False


def test_session_started_only_after_min_duration_and_backdated():
    out, det = run_script([0] * 4 + [0.9] * 6, smoothing=0.0)
    assert kinds(out, "session_started") == []           # under 3 s of tool so far
    assert det.state()["in_session"] is False
    assert det.flush() == [{"kind": "activity", "activity": "unknown", "confidence": 0.0,
                            "tool_probability": 0.0, "ts": pytest.approx(T0 + 4.98),
                            "model": "scripted"}]        # the candidate is dropped silently
    out, det = run_script([0] * 4 + [0.9] * 10, smoothing=0.0)
    started = kinds(out, "session_started")
    assert len(started) == 1 and started[0]["start_ts"] == pytest.approx(T0 + 1.65, abs=0.03)
    assert 3.0 <= started[0]["duration_s"] < 3.6 and started[0]["model"] == "scripted"
    state = det.state()
    assert state["in_session"] and state["session_id"] == started[0]["session_id"]
    assert state["activity"] == "tool_use" and state["model"] == "scripted"
    ended = kinds(det.flush(), "session_ended")
    assert len(ended) == 1 and ended[0]["reason"] == "end_of_data"
    assert det.state()["activity"] == "unknown" and not det.state()["in_session"]


def test_rise_between_thresholds_never_starts():
    out, _ = run_script([0] * 4 + [0.55] * 16 + [0] * 4, smoothing=0.0)
    assert kinds(out, "session_started") == [] and kinds(out, "session_ended") == []


def test_short_burst_is_dropped_silently():
    out, det = run_script([0] * 4 + [0.9] * 3 + [0] * 8, smoothing=0.0)
    assert kinds(out, "session_started") == [] and kinds(out, "session_ended") == []
    flushed = det.flush()                                # only the activity reset, no session
    assert [t["kind"] for t in flushed] == ["activity"] and flushed[0]["activity"] == "unknown"
    assert det.flush() == []


def test_one_hop_below_stop_does_not_end_session():
    out, _ = run_script([0] * 4 + [0.9] * 8 + [0.1] + [0.9] * 6 + [0] * 6, smoothing=0.0)
    ended = kinds(out, "session_ended")
    assert len(ended) == 1 and ended[0]["duration_s"] == pytest.approx(6.2, abs=0.05)


def test_ema_smoothing_and_low_confidence_unknown():
    out, _ = run_script([0] * 4 + [0.95] * 12 + [0] * 6)          # default smoothing 0.5
    ended = kinds(out, "session_ended")
    assert len(ended) == 1 and ended[0]["duration_s"] == pytest.approx(4.7, abs=0.05)
    assert ended[0]["start_ts"] == pytest.approx(T0 + 1.65, abs=0.03)   # smoothing lag undone
    out, det = run_script([0] * 5 + [0.5] * 6, smoothing=0.0, min_confidence=0.6)
    assert det.state()["activity"] == "unknown"                   # 0.5 / 0.5: nothing convincing
    activities = [t["activity"] for t in kinds(out, "activity")]
    assert activities and activities[0] == "stationary" and activities[-1] == "unknown"


def test_activity_transitions_only_on_change():
    out, _ = run_script([0] * 10, smoothing=0.0)
    assert [t["activity"] for t in kinds(out, "activity")] == ["stationary"]


# ---- pure detector, rule baseline on the synthetic helper ------------------------------------
def test_still_gives_no_session():
    out, det = run_rules(make("still", 20))
    assert kinds(out, "session_started") == []
    assert det.state()["activity"] == "stationary"


def test_walking_gives_no_session():
    out, det = run_rules(make("walking", 30))
    assert kinds(out, "session_started") == []
    assert det.state()["activity"] == "walking"
    assert detect_sessions(make("walking", 30), RuleClassifier()) == []


def test_gesture_gives_no_session():
    assert detect_sessions(make("gesture", 30, seed=3), RuleClassifier()) == []


@pytest.mark.parametrize("hz,onset,seconds", [(50, 8.0, 20), (104, 8.27, 20), (208, 8.13, 10),
                                              (208, 8.4, 60), (416, 8.0, 5), (50, 8.4, 4)])
def test_tool_between_still_is_one_session(hz, onset, seconds):
    arr = sequence([("still", onset), ("tool", seconds), ("still", 8)], hz=hz, seed=7)
    sessions = detect_sessions(arr, RuleClassifier())
    assert len(sessions) == 1
    s = sessions[0]
    assert abs(s["start_ts"] - (T0 + onset)) <= 0.5
    assert abs(s["end_ts"] - (T0 + onset + seconds)) <= 0.5
    assert abs(s["duration_s"] - seconds) <= 0.6
    assert s["missing_s"] == 0 and s["model"] == "rules"
    assert s["duration_s"] == pytest.approx(s["end_ts"] - s["start_ts"])
    assert 0.5 < s["confidence"] <= 1.0 and s["completeness"] > 0.9
    assert len(s["session_id"]) == 32


@pytest.mark.parametrize("seconds,onset", [(1.5, 6.0), (2.0, 6.27), (2.5, 6.0), (2.5, 6.4)])
def test_tool_burst_shorter_than_min_duration(seconds, onset):
    arr = sequence([("still", onset), ("tool", seconds), ("still", 6)])
    assert detect_sessions(arr, RuleClassifier()) == []


def _received(arr, start_s, end_s, hz=50.0):
    rel = arr[:, 2] - T0
    return float(((rel >= start_s) & (rel < end_s)).sum()) / hz


def test_short_dropouts_are_not_tool_time():
    arr = sequence([("still", 5), ("tool", 30), ("still", 5)])
    for at in np.arange(8.0, 35.0, 3.0):                 # 0.6 s lost every 3 s, below gap_s
        arr = drop(arr, at, at + 0.6)
    received = _received(arr, 5, 35)
    assert 24.0 < received < 25.0
    sessions = detect_sessions(arr, RuleClassifier())
    assert sessions
    total = sum(s["duration_s"] for s in sessions)
    assert received - 3.0 < total <= received + 0.6
    assert sum(s["missing_s"] for s in sessions) > 4.0
    for s in sessions:
        assert s["duration_s"] == pytest.approx(s["end_ts"] - s["start_ts"] - s["missing_s"])


def test_single_short_gap_is_subtracted():
    arr = drop(sequence([("still", 5), ("tool", 20), ("still", 5)]), 14, 14.8)
    sessions = detect_sessions(arr, RuleClassifier())
    assert len(sessions) == 1
    s = sessions[0]
    assert s["missing_s"] == pytest.approx(0.8, abs=0.05)
    assert 19.2 - 0.6 <= s["duration_s"] <= 19.2 + 0.6
    assert s["end_ts"] - s["start_ts"] > s["duration_s"]


def test_small_backward_time_steps_are_tolerated():
    arr = sequence([("still", 5), ("tool", 10), ("still", 5)])
    for i in range(0, len(arr), 20):                     # every other packet stamped 40 ms late,
        arr[i:i + 10, 2] += 0.04                         # so it overlaps the next one
    assert (np.diff(arr[:, 2]) < 0).any()
    sessions = detect_sessions(arr, RuleClassifier())
    assert len(sessions) == 1 and sessions[0]["reason"] == "activity_stopped"
    assert abs(sessions[0]["duration_s"] - 10) <= 0.6
    out, det = run_rules(make("still", 6))               # control: a big backward step IS a gap
    assert det.state()["activity"] == "stationary"
    out = det.feed(make("still", 1, t0=T0 + 3, n0=300))
    assert [t["activity"] for t in kinds(out, "activity")] == ["unknown"]


def test_counter_unknown_and_rows_without_time():
    arr = sequence([("still", 5), ("tool", 10), ("still", 5)])
    arr[:, 0] = np.nan                                   # n unknown: time alone drives everything
    sessions = detect_sessions(arr, RuleClassifier())
    assert len(sessions) == 1 and abs(sessions[0]["duration_s"] - 10) <= 0.6
    gappy = drop(arr, 9, 9.8)                            # loss is still seen without n
    assert detect_sessions(gappy, RuleClassifier())[0]["missing_s"] == pytest.approx(0.8, abs=0.05)

    det = ToolDetector(RuleClassifier())
    bad = make("still", 4)
    bad[:, 2] = np.nan
    for _ in range(50):
        assert det.feed(bad) == []
    assert det._buf is None and det.state()["last_sample_time"] is None     # nothing piles up
    mixed = sequence([("still", 5), ("tool", 10), ("still", 5)])
    mixed[::7, 2] = np.nan                               # a few unplaceable rows are dropped
    assert len(detect_sessions(mixed, RuleClassifier())) == 1


def test_model_is_the_one_that_answered(monkeypatch):
    """A loaded model can be bypassed for a window (rate guard): report what really answered."""
    from backend import tool_classifier
    real = tool_classifier.classify

    def guarded(clf, window):
        return {**real(clf, window), "model": "rules", "note": "rate guard"}

    monkeypatch.setattr(tool_classifier, "classify", guarded)
    out, det = run_script([0] * 4 + [0.9] * 10, smoothing=0.0)
    assert det.classifier.name == "scripted" and det.state()["model"] == "rules"
    assert kinds(out, "session_started")[0]["model"] == "rules"
    assert all(t["model"] == "rules" for t in kinds(out, "activity"))


def test_gap_in_tool_use_splits_sessions():
    arr = drop(sequence([("still", 5), ("tool", 30), ("still", 5)]), 15, 21)   # 6 s lost mid tool
    out, det = run_rules(arr)
    sessions = kinds(out + det.flush(), "session_ended")
    gap_start, gap_end = T0 + 15, T0 + 21
    assert len(sessions) == 2
    assert sessions[0]["reason"] == "data_gap"
    assert sessions[0]["end_ts"] < gap_start and sessions[1]["start_ts"] >= gap_end
    for s in sessions:                                   # nothing spans the gap
        assert not (s["start_ts"] < gap_start and s["end_ts"] > gap_start)
    total = sum(s["duration_s"] for s in sessions)
    assert total <= 24.0 + 0.5                           # 24 s of tool data actually received
    assert total > 20.0
    assert abs(sessions[0]["start_ts"] - (T0 + 5)) <= 0.5
    assert sessions[0]["end_ts"] == pytest.approx(T0 + 15, abs=0.05)    # last sample before the gap
    assert abs(sessions[1]["end_ts"] - (T0 + 35)) <= 0.5


def test_counter_reset_is_a_gap():
    arr = sequence([("still", 3), ("tool", 16)])
    arr[arr[:, 2] >= T0 + 11, 0] -= 400                  # band reconnected: n restarts
    sessions = detect_sessions(arr, RuleClassifier())
    assert sessions and sessions[0]["reason"] == "data_gap"
    assert sessions[0]["end_ts"] < T0 + 11
    assert all(not (s["start_ts"] < T0 + 11 < s["end_ts"]) for s in sessions)


def test_low_completeness_is_unknown_and_no_tool_time():
    arr = sequence([("still", 2), ("tool", 20)])
    keep = np.random.default_rng(1).random(len(arr)) < 0.5        # half the samples lost
    assert len(detect_sessions(arr, RuleClassifier())) == 1       # control: intact data is tool use
    out, det = run_rules(arr[keep])
    assert kinds(out, "session_started") == []
    assert kinds(out, "activity") == []                           # never left "unknown"
    assert det.state()["last_sample_time"] == pytest.approx(arr[keep][-1, 2])
    state = det.state()
    assert state["activity"] == "unknown" and state["completeness"] < 0.75
    assert state["tool_probability"] == 0.0


def test_empty_and_state_shape():
    det = ToolDetector(RuleClassifier())
    assert det.feed(np.empty((0, 9))) == [] and det.flush() == []
    assert set(det.state()) == {"activity", "confidence", "tool_probability", "probabilities",
                                "completeness", "model", "in_session", "session_id",
                                "last_sample_time"}
    assert det.state()["activity"] == "unknown" and det.state()["model"] == "rules"


# ---- live service ----------------------------------------------------------------------------
@pytest.fixture()
def svc(test_bus):
    service = ToolSessionService(bus=test_bus, classifier=RuleClassifier(), shift=SHIFT)
    service.start()
    service.start()                                      # idempotent: still one subscription
    seen = []
    test_bus.subscribe(seen.append)
    yield service, seen, test_bus
    service.stop()


def feed(bus, arr, source="synthetic"):
    for batch in to_batches(arr, source=source):
        assert bus.publish_imu(batch)
    bus.drain(timeout=30)


def of_type(seen, type_):
    return [e for e in seen if e.type == type_]


def test_service_publishes_contract_events_and_persists(svc):
    service, seen, bus = svc
    feed(bus, sequence([("still", 4), ("tool", 12), ("still", 5)]))
    started, ended = of_type(seen, "tool_session_started"), of_type(seen, "tool_session_ended")
    assert len(started) == 1 and len(ended) == 1          # subscribed once
    for event in started + ended:
        assert CONTRACT_SESSION_KEYS <= set(event.payload)
        assert event.source == "synthetic"
        assert event.shift_id == "shift-test" and event.worker_id == "worker-test"
        assert event.payload["tool_id"] is None and event.payload["confirmation"] == "tentative"
        assert event.payload["model"] == "rules" and event.payload["missing_s"] == 0
    assert started[0].payload["end_ts"] is None
    assert ended[0].payload["session_id"] == started[0].payload["session_id"]
    assert abs(ended[0].payload["duration_s"] - 12) <= 0.6
    assert abs(ended[0].payload["start_ts"] - (T0 + 4)) <= 0.5
    assert ended[0].payload["reason"] == "activity_stopped"

    activity = of_type(seen, "activity_changed")
    assert {"activity", "confidence", "tool_probability", "model"} <= set(activity[0].payload)
    assert "tool_use" in [e.payload["activity"] for e in activity]
    assert all(e.source == "synthetic" for e in activity)

    rows = tool_sessions.list_sessions("shift-test")
    assert len(rows) == 1 and set(rows[0]) == set(tool_sessions.COLUMNS)
    assert rows[0]["session_id"] == ended[0].payload["session_id"]
    assert rows[0]["end_ts"] == pytest.approx(ended[0].payload["end_ts"])
    assert rows[0]["source"] == "synthetic" and rows[0]["confirmation"] == "tentative"
    assert tool_sessions.list_sessions("another-shift") == []
    stored = database.list_events("shift-test", types=["tool_session_ended"])
    assert len(stored) == 1 and stored[0]["source"] == "synthetic"

    state = service.state()
    assert state["source"] == "synthetic" and state["stale"] is False
    assert state["model"] == "rules" and state["model_info"]["model"] == "rules"
    assert state["in_session"] is False and state["updated_at"] is not None


def test_service_replay_source_and_ordering(svc):
    service, seen, bus = svc
    feed(bus, sequence([("still", 3), ("tool", 8), ("still", 4), ("tool", 8), ("still", 4)]),
         source="replay")
    ended = of_type(seen, "tool_session_ended")
    assert len(ended) == 2 and all(e.source == "replay" for e in seen)
    rows = tool_sessions.list_sessions("shift-test")
    assert [r["source"] for r in rows] == ["replay", "replay"]
    assert rows[0]["start_ts"] < rows[1]["start_ts"]


def test_confirm_and_reject(svc):
    service, seen, bus = svc
    feed(bus, sequence([("still", 3), ("tool", 8), ("still", 4), ("tool", 8), ("still", 4)]))
    first, second = [r["session_id"] for r in tool_sessions.list_sessions("shift-test")]
    seen.clear()

    result = tool_sessions.confirm(first, "grinder-1", True)
    bus.drain()
    assert result["confirmation"] == "confirmed" and result["tool_id"] == "grinder-1"
    row = tool_sessions.list_sessions("shift-test")[0]
    assert row["confirmation"] == "confirmed" and row["tool_id"] == "grinder-1"
    events = of_type(seen, "tool_session_ended")
    assert len(events) == 1 and events[0].source == "synthetic"
    assert CONTRACT_SESSION_KEYS <= set(events[0].payload)
    assert events[0].payload["confirmation"] == "confirmed"
    assert events[0].payload["tool_id"] == "grinder-1"
    assert events[0].payload["confirmed_by"] == "manual"
    assert events[0].payload["session_id"] == first and events[0].shift_id == "shift-test"

    rejected = tool_sessions.confirm(second, None, False)
    bus.drain()
    assert rejected["confirmation"] == "rejected" and rejected["tool_id"] is None
    assert of_type(seen, "tool_session_ended")[-1].payload["confirmation"] == "rejected"
    assert tool_sessions.confirm(second, "drill-2", False)["tool_id"] == "drill-2"

    with pytest.raises(KeyError):
        tool_sessions.confirm("no-such-session", None, True)


def test_confirm_open_session_is_refused(svc):
    service, seen, bus = svc
    feed(bus, sequence([("still", 3), ("tool", 8)]))
    started = of_type(seen, "tool_session_started")
    assert len(started) == 1 and service.state()["in_session"] is True
    with pytest.raises(ValueError):
        tool_sessions.confirm(started[0].payload["session_id"], "grinder-1", True)


def test_disconnect_ends_open_session(svc):
    service, seen, bus = svc
    feed(bus, sequence([("still", 3), ("tool", 8)]))
    assert of_type(seen, "tool_session_ended") == []
    bus.publish(Event("device_disconnected", {"reason": "test"}, "synthetic", "shift-test", "worker-test"))
    bus.drain()
    ended = of_type(seen, "tool_session_ended")
    assert len(ended) == 1 and ended[0].payload["reason"] == "disconnect"
    assert ended[0].payload["end_ts"] <= T0 + 11
    assert tool_sessions.list_sessions("shift-test")[0]["end_ts"] is not None
    assert service.state()["activity"] == "unknown" and not service.state()["in_session"]


def test_check_stale_flushes(svc):
    service, seen, bus = svc
    assert service.check_stale() is True                 # no data yet: nothing live
    feed(bus, sequence([("still", 3), ("tool", 8)]))
    assert service.check_stale() is False
    assert service.check_stale(now=time.time() + 60) is True
    bus.drain()
    ended = of_type(seen, "tool_session_ended")
    assert len(ended) == 1 and ended[0].payload["reason"] == "stale"
    assert ended[0].payload["duration_s"] < 8.0           # the silent minute is not tool time
    state = service.state()
    assert state["stale"] is True and state["activity"] == "unknown"
    assert of_type(seen, "activity_changed")[-1].payload["activity"] == "unknown"


def test_source_change_flushes_first(svc):
    service, seen, bus = svc
    feed(bus, sequence([("still", 3), ("tool", 8)]))
    feed(bus, make("still", 1, t0=T0 + 11, n0=550), source="replay")
    ended = of_type(seen, "tool_session_ended")
    assert len(ended) == 1 and ended[0].payload["reason"] == "source_changed"
    assert ended[0].source == "synthetic"


def test_start_closes_orphans_of_the_shift_silently(test_bus):
    base = {"worker_id": "worker-test", "start_ts": 1000.0, "tool_id": None, "confidence": 0.9,
            "completeness": 1.0, "confirmation": "tentative", "source": "synthetic",
            "model": "rules", "reason": None, "updated_at": 1.0}
    tool_sessions._save({**base, "session_id": "orphan", "shift_id": "shift-test",
                         "end_ts": None, "duration_s": 3.5})
    tool_sessions._save({**base, "session_id": "other-shift", "shift_id": "shift-other",
                         "end_ts": None, "duration_s": 3.5})
    tool_sessions._save({**base, "session_id": "closed", "shift_id": "shift-test",
                         "start_ts": 900.0, "end_ts": 950.0, "duration_s": 50.0})
    seen = []
    test_bus.subscribe(seen.append)
    service = ToolSessionService(bus=test_bus, classifier=RuleClassifier(), shift=SHIFT)
    service.start()
    service.stop()
    test_bus.drain()
    rows = {r["session_id"]: r for r in tool_sessions.list_sessions("shift-test")}
    assert rows["orphan"]["end_ts"] == pytest.approx(1003.5) and rows["orphan"]["reason"] == "restart"
    assert rows["orphan"]["duration_s"] == pytest.approx(3.5)
    assert rows["closed"]["end_ts"] == 950.0 and rows["closed"]["reason"] is None
    assert tool_sessions.list_sessions("shift-other")[0]["end_ts"] is None
    assert seen == []                                    # nothing published
    assert tool_sessions.confirm("orphan", "grinder-1", True)["confirmation"] == "confirmed"


def test_service_reports_model_that_answered(svc, monkeypatch):
    from backend import tool_classifier
    real = tool_classifier.classify
    monkeypatch.setattr(tool_classifier, "classify",
                        lambda clf, window: {**real(clf, window), "model": "fallback-rules"})
    service, seen, bus = svc
    feed(bus, sequence([("still", 3), ("tool", 8)]))
    assert service.state()["model"] == "fallback-rules"
    assert service.state()["model_info"]["model"] == "rules"         # the loaded classifier
    assert of_type(seen, "tool_session_started")[0].payload["model"] == "fallback-rules"
    assert all(e.payload["model"] == "fallback-rules" for e in of_type(seen, "activity_changed"))


def test_service_without_start_and_module_singleton(tmp_db):
    service = ToolSessionService(classifier=RuleClassifier())    # no bus use until events happen
    assert service.state()["activity"] == "unknown" and service.state()["stale"] is True
    assert tool_sessions.get_service() is tool_sessions.get_service()
    assert tool_sessions.list_sessions() == []
