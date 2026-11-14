"""Planner and shift-plan tests (be-hav). Expected numbers are hand-derived in comments.

UK HSE points method: points = 2 * a_hv^2 * trigger hours; 100 = action value, 400 = limit value.
The current exposure is controlled by monkeypatching `exposure.compute`.
"""
import pytest

from backend import config, exposure, planner, tasks
from backend.models import Event

SHIFT = config.current_shift()["shift_id"]
WORKER = config.current_shift()["worker_id"]
REVIEW_KINDS = {"review_tool_choice", "review_work_method", "review_task_duration",
                "review_engineering_controls"}
CONTRACT_KEYS = {"current_points", "task_id", "additional_points", "projected_points",
                 "crosses_action", "crosses_limit", "reasons", "options", "requires_approval"}


def set_current(monkeypatch, total_points, status="ok"):
    """Make `exposure.compute` report a fixed current figure; forbid `recompute`."""
    def fake_compute(shift_id):
        return {"label": exposure.LABEL, "shift_id": shift_id, "total_points": total_points,
                "status": status, "entries": [], "reasons": []}

    def no_recompute(shift_id):
        raise AssertionError("planner must never call exposure.recompute")

    monkeypatch.setattr(exposure, "compute", fake_compute)
    monkeypatch.setattr(exposure, "recompute", no_recompute, raising=False)


def all_text(rec):
    parts = list(rec["reasons"])
    for option in rec["options"] + rec["blocked_options"]:
        parts.append(option.get("description", ""))
        parts.append(option.get("reason", ""))
    return " ".join(parts).lower()


def kinds(rec):
    return [o["kind"] for o in rec["options"]]


# ------------------------------------------------------------------ tasks

def test_seed_plan_present_and_ordered(tmp_db):
    plan = tasks.list_tasks(SHIFT)
    assert [t["task_id"] for t in plan] == ["task-idler-s3", "task-bracket-s3", "task-guard-s3"]
    assert [t["priority"] for t in plan] == [1, 2, 3]
    idler = tasks.get_task("task-idler-s3")
    assert idler == {
        "task_id": "task-idler-s3", "shift_id": SHIFT,
        "title": "Replace seized idler at Station 3", "asset": "CV-102 / Station 3 / idler",
        "tool_id": "impact_wrench", "expected_trigger_minutes": 25.0, "priority": 1,
        "required_qualification": "mechanical_fitter", "dependencies": [], "status": "pending",
    }
    bracket = tasks.get_task("task-bracket-s3")
    assert bracket["tool_id"] == "angle_grinder" and bracket["expected_trigger_minutes"] == 15.0
    assert bracket["dependencies"] == ["task-idler-s3"]
    guard = tasks.get_task("task-guard-s3")
    assert guard["tool_id"] is None and guard["expected_trigger_minutes"] == 0.0
    assert guard["dependencies"] == ["task-bracket-s3"]
    assert tasks.get_task("no-such-task") is None
    assert tasks.list_tasks("some-other-shift") == []


def test_ordering_by_priority_then_task_id(tmp_db):
    tasks.upsert_task("task-b", "shift-x", "B", None, None, 0, priority=2)
    tasks.upsert_task("task-a", "shift-x", "A", None, None, 0, priority=2)
    tasks.upsert_task("task-z", "shift-x", "Z", None, None, 0, priority=1)
    assert [t["task_id"] for t in tasks.list_tasks("shift-x")] == ["task-z", "task-a", "task-b"]


def test_dependencies_gate_next_task_and_set_status(tmp_db):
    assert tasks.next_task(SHIFT)["task_id"] == "task-idler-s3"
    # the bracket task is second in priority but gated on the idler task
    tasks.upsert_task("task-idler-s3", SHIFT, "Replace seized idler at Station 3",
                      "CV-102 / Station 3 / idler", "impact_wrench", 25, priority=5,
                      required_qualification="mechanical_fitter")
    assert tasks.next_task(SHIFT)["task_id"] == "task-idler-s3"   # others still gated

    done = tasks.set_status("task-idler-s3", "done")
    assert done["status"] == "done"
    assert tasks.next_task(SHIFT)["task_id"] == "task-bracket-s3"
    assert [t["task_id"] for t in tasks.list_tasks(SHIFT)] == ["task-bracket-s3", "task-guard-s3"]
    assert len(tasks.list_tasks(SHIFT, include_done=True)) == 3   # seeding did not undo "done"

    tasks.set_status("task-bracket-s3", "done")
    assert tasks.next_task(SHIFT)["task_id"] == "task-guard-s3"
    tasks.set_status("task-guard-s3", "done")
    assert tasks.next_task(SHIFT) is None
    assert tasks.set_status("task-guard-s3", "pending")["status"] == "pending"

    with pytest.raises(ValueError):
        tasks.set_status("task-guard-s3", "approved")
    with pytest.raises(KeyError):
        tasks.set_status("no-such-task", "done")
    with pytest.raises(ValueError):
        tasks.upsert_task("task-bad", SHIFT, "Bad", None, "impact_wrench", -1)


def test_workers_and_unknown_qualifications_round_trip(tmp_db):
    workers = {w["worker_id"]: w for w in tasks.list_workers()}
    assert workers["worker-1"]["qualifications"] == ["mechanical_fitter"]
    assert workers["worker-1"]["qualifications_verified"] is True
    assert workers["worker-2"]["qualifications_verified"] is True
    assert workers["worker-3"]["qualifications"] is None          # unknown, not []
    assert workers["worker-3"]["qualifications_verified"] is False

    saved = tasks.upsert_worker("worker-9", "Worker 9", None)
    assert saved["qualifications"] is None and saved["qualifications_verified"] is False
    assert tasks.get_worker("worker-9")["qualifications"] is None
    assert tasks.upsert_worker("worker-9", "Worker 9", [], True)["qualifications"] == []
    assert tasks.get_worker("nobody") is None


def test_alternatives_only_when_configured(tmp_db):
    alts = tasks.alternatives_for("impact_wrench")
    assert len(alts) == 1
    assert alts[0]["kind"] == "tool" and alts[0]["alt_tool_id"] == "low_vibration_wrench"
    assert alts[0]["source"] == "configured"
    assert tasks.alternatives_for("angle_grinder") == []
    assert tasks.alternatives_for(None) == []
    added = tasks.add_alternative("angle_grinder", method="Cold-cut with a hand saw")
    assert added["kind"] == "method" and added["alt_tool_id"] is None
    assert tasks.alternatives_for("angle_grinder") == [added]
    with pytest.raises(ValueError):
        tasks.add_alternative("angle_grinder")
    with pytest.raises(ValueError):
        tasks.add_alternative("angle_grinder", alt_tool_id="x", method="y")


# ------------------------------------------------------------------ projection

def test_worked_example_crosses_action(tmp_db, monkeypatch):
    set_current(monkeypatch, 85.0)
    rec = planner.evaluate(SHIFT, "task-idler-s3", publish=False)
    # impact_wrench 8 m/s2 for 25 min: 2 * 8^2 * (25/60) = 128 * 0.41667 = 53.33
    assert rec["a_hv_ms2"] == 8.0 and rec["expected_trigger_minutes"] == 25.0
    assert rec["current_points"] == 85.0
    assert rec["additional_points"] == pytest.approx(53.33, abs=0.005)
    # 85 + 53.33 = 138.33
    assert rec["projected_points"] == pytest.approx(138.33, abs=0.005)
    # A(8) = 2.5 * sqrt(138.33 / 100) = 2.5 * 1.1762 = 2.94
    assert rec["projected_a8"] == pytest.approx(2.94, abs=0.005)
    assert rec["projected_level"] == "above_action"
    assert rec["projection_available"] is True
    assert rec["crosses_action"] is True       # 85 < 100 <= 138.33
    assert rec["crosses_limit"] is False       # 138.33 < 400
    assert rec["requires_approval"] is True
    assert rec["status"] == "pending" and rec["decision"] is None
    assert rec["label"] == exposure.LABEL
    assert rec["task_title"] == "Replace seized idler at Station 3"
    assert rec["tool_name"] == "Impact wrench" and rec["a_hv_source"]
    assert rec["current_status"] == "ok"
    assert "action value" in all_text(rec)
    assert "safe" not in all_text(rec)


def test_crossing_the_limit_never_reads_as_acceptable(tmp_db, monkeypatch):
    set_current(monkeypatch, 380.0)
    rec = planner.evaluate(SHIFT, "task-idler-s3", publish=False)
    # 380 + 53.33 = 433.33 >= 400
    assert rec["projected_points"] == pytest.approx(433.33, abs=0.005)
    assert rec["crosses_limit"] is True
    assert rec["crosses_action"] is False      # current was already above 100
    assert rec["projected_level"] == "above_limit"
    assert rec["requires_approval"] is True
    text = all_text(rec)
    assert "exposure limit value" in text and "supervisor review" in text
    for word in ("safe", "acceptable", "permitted", "allowed", "fine", "no action"):
        assert word not in text
    assert REVIEW_KINDS <= set(kinds(rec))


def test_above_action_below_limit_does_not_imply_under_limit_is_ok(tmp_db, monkeypatch):
    set_current(monkeypatch, 150.0)
    rec = planner.evaluate(SHIFT, "task-idler-s3", publish=False)
    # 150 + 53.33 = 203.33: already above action, below limit
    assert rec["projected_points"] == pytest.approx(203.33, abs=0.005)
    assert rec["crosses_action"] is False and rec["crosses_limit"] is False
    text = all_text(rec)
    assert "supervisor review" in text
    for word in ("safe", "acceptable", "within the limit", "below the exposure limit",
                 "under the limit"):
        assert word not in text
    assert REVIEW_KINDS <= set(kinds(rec))


def test_below_action_has_no_safe_wording(tmp_db, monkeypatch):
    set_current(monkeypatch, 10.0)
    rec = planner.evaluate(SHIFT, "task-idler-s3", publish=False)
    # 10 + 53.33 = 63.33 < 100
    assert rec["projected_points"] == pytest.approx(63.33, abs=0.005)
    assert rec["crosses_action"] is False and rec["crosses_limit"] is False
    assert rec["projected_level"] == "below_action"
    assert rec["requires_approval"] is True     # still never auto-approved
    text = all_text(rec)
    assert "safe" not in text and "acceptable" not in text
    assert "below the exposure action value" in text
    assert "as low as reasonably practicable" in text
    assert rec["options"] == [] and rec["blocked_options"] == []


def test_options_have_review_kinds_and_all_require_approval(tmp_db, monkeypatch):
    set_current(monkeypatch, 85.0)
    rec = planner.evaluate(SHIFT, "task-idler-s3", publish=False)
    assert REVIEW_KINDS <= set(kinds(rec))
    assert rec["options"]
    for option in rec["options"] + rec["blocked_options"]:
        assert option["requires_approval"] is True
        assert option["kind"] and option["description"]


def test_alternative_tool_only_when_configured(tmp_db, monkeypatch):
    set_current(monkeypatch, 85.0)
    rec = planner.evaluate(SHIFT, "task-idler-s3", publish=False)
    alts = [o for o in rec["options"] if o["kind"] == "alternative_tool"]
    assert len(alts) == 1
    alt = alts[0]
    assert alt["alt_tool_id"] == "low_vibration_wrench" and alt["a_hv_ms2"] == 4.0
    # low_vibration_wrench 4 m/s2 for 25 min: 2 * 4^2 * (25/60) = 32 * 0.41667 = 13.33
    assert alt["additional_points"] == pytest.approx(13.33, abs=0.005)
    # 85 + 13.33 = 98.33
    assert alt["projected_points"] == pytest.approx(98.33, abs=0.005)
    assert alt["requires_approval"] is True
    assert "alternative_method" not in kinds(rec)

    # angle_grinder 6 m/s2 for 15 min: 2 * 36 * 0.25 = 18; 90 + 18 = 108 -> crosses action
    set_current(monkeypatch, 90.0)
    grinder = planner.evaluate(SHIFT, "task-bracket-s3", publish=False)
    assert grinder["additional_points"] == pytest.approx(18.0, abs=0.005)
    assert grinder["projected_points"] == pytest.approx(108.0, abs=0.005)
    assert grinder["crosses_action"] is True
    assert REVIEW_KINDS <= set(kinds(grinder))
    assert "alternative_tool" not in kinds(grinder)
    assert "alternative_method" not in kinds(grinder)


def test_alternative_without_value_gives_no_number(tmp_db, monkeypatch):
    set_current(monkeypatch, 95.0)
    tasks.add_alternative("angle_grinder", alt_tool_id="needle_scaler")
    tasks.add_alternative("angle_grinder", method="Cold-cut with a hand saw")
    rec = planner.evaluate(SHIFT, "task-bracket-s3", publish=False)   # 95 + 18 = 113
    alt = next(o for o in rec["options"] if o["kind"] == "alternative_tool")
    assert alt["a_hv_ms2"] is None
    assert alt["additional_points"] is None and alt["projected_points"] is None
    assert "not configured" in alt["description"]
    method = next(o for o in rec["options"] if o["kind"] == "alternative_method")
    assert method["method"] == "Cold-cut with a hand saw" and method["requires_approval"] is True


def test_reassign_only_to_verified_qualified_worker(tmp_db, monkeypatch):
    set_current(monkeypatch, 85.0)
    tasks.upsert_worker("worker-4", "Worker 4", ["mechanical_fitter"], False)   # unverified
    tasks.upsert_worker("worker-5", "Worker 5", ["electrician"], True)          # wrong skill
    rec = planner.evaluate(SHIFT, "task-idler-s3", publish=False)

    offered = [o for o in rec["options"] if o["kind"] == "reassign"]
    assert [o["worker_id"] for o in offered] == ["worker-2"]
    assert offered[0]["requires_approval"] is True
    assert "not tracked here" in offered[0]["description"]
    assert "supervisor" in offered[0]["description"]

    blocked = {o["worker_id"]: o for o in rec["blocked_options"]}
    assert set(blocked) == {"worker-3", "worker-4", "worker-5"}
    assert "unknown" in blocked["worker-3"]["reason"]
    assert "not verified" in blocked["worker-4"]["reason"]
    assert "mechanical_fitter" in blocked["worker-5"]["reason"]
    # the current worker is never offered a reassignment to themselves
    everyone = [o.get("worker_id") for o in rec["options"] + rec["blocked_options"]]
    assert WORKER not in everyone


def test_tool_without_value_gives_no_invented_number(tmp_db, monkeypatch):
    set_current(monkeypatch, 85.0)
    tasks.upsert_task("task-scale", SHIFT, "Descale chute liner", "CV-102 / chute",
                      "needle_scaler", 20, priority=4)
    rec = planner.evaluate(SHIFT, "task-scale", publish=False)
    assert rec["projection_available"] is False
    assert rec["a_hv_ms2"] is None
    assert rec["additional_points"] is None and rec["projected_points"] is None
    assert rec["projected_a8"] is None and rec["projected_level"] == "unknown"
    assert rec["crosses_action"] is None and rec["crosses_limit"] is None
    assert rec["current_points"] == 85.0
    assert rec["requires_approval"] is True
    text = all_text(rec)
    assert "no vibration value configured" in text and "must enter" in text
    assert "safe" not in text
    assert REVIEW_KINDS <= set(kinds(rec))


def test_unknown_tool_profile_gives_no_number(tmp_db, monkeypatch):
    set_current(monkeypatch, 85.0)
    tasks.upsert_task("task-mystery", SHIFT, "Mystery job", None, "no_such_tool", 10)
    rec = planner.evaluate(SHIFT, "task-mystery", publish=False)
    assert rec["projection_available"] is False
    assert rec["additional_points"] is None and rec["projected_points"] is None


def test_current_exposure_unavailable_gives_no_projection(tmp_db, monkeypatch):
    set_current(monkeypatch, None, status="unavailable")
    rec = planner.evaluate(SHIFT, "task-idler-s3", publish=False)
    assert rec["projection_available"] is False
    assert rec["current_points"] is None and rec["current_status"] == "unavailable"
    # the task's own contribution is still computable: 2 * 64 * (25/60) = 53.33
    assert rec["additional_points"] == pytest.approx(53.33, abs=0.005)
    assert rec["projected_points"] is None and rec["projected_a8"] is None
    assert rec["projected_level"] == "unknown"
    assert rec["crosses_action"] is None and rec["crosses_limit"] is None
    assert "unavailable" in all_text(rec) and "safe" not in all_text(rec)
    assert REVIEW_KINDS <= set(kinds(rec))


def test_partial_current_figure_is_flagged(tmp_db, monkeypatch):
    set_current(monkeypatch, 85.0, status="needs_confirmation")
    rec = planner.evaluate(SHIFT, "task-idler-s3", publish=False)
    assert rec["current_status"] == "needs_confirmation"
    assert rec["projected_points"] == pytest.approx(138.33, abs=0.005)
    assert "partial" in all_text(rec)


def test_task_without_tool_adds_zero_points(tmp_db, monkeypatch):
    set_current(monkeypatch, 85.0)
    rec = planner.evaluate(SHIFT, "task-guard-s3", publish=False)
    # no tool, 0 minutes: 85 + 0 = 85
    assert rec["additional_points"] == 0.0 and rec["projected_points"] == 85.0
    assert rec["projection_available"] is True
    assert rec["crosses_action"] is False and rec["crosses_limit"] is False


def test_unknown_task_raises_key_error(tmp_db, monkeypatch):
    set_current(monkeypatch, 85.0)
    with pytest.raises(KeyError):
        planner.evaluate(SHIFT, "no-such-task", publish=False)


# ------------------------------------------------------------------ persistence / decisions

def test_pending_until_human_decision(tmp_db, monkeypatch):
    set_current(monkeypatch, 85.0)
    first = planner.evaluate(SHIFT, "task-idler-s3", publish=False)
    rec_id = first["recommendation_id"]
    assert first["status"] == "pending"

    # re-evaluation with a new current figure updates the same pending record
    set_current(monkeypatch, 90.0)
    second = planner.evaluate(SHIFT, "task-idler-s3", publish=False)
    assert second["recommendation_id"] == rec_id
    listed = planner.list_recommendations(SHIFT)
    assert len(listed) == 1
    assert listed[0]["status"] == "pending" and listed[0]["decided_by"] is None
    assert listed[0]["decided_at"] is None and listed[0]["decision"] is None
    # 90 + 53.33 = 143.33
    assert listed[0]["projected_points"] == pytest.approx(143.33, abs=0.005)
    assert listed[0]["updated_at"] >= listed[0]["created_at"]

    # reads, other evaluations and task changes never decide anything
    planner.evaluate(SHIFT, "task-bracket-s3", publish=False)
    tasks.set_status("task-idler-s3", "done")
    planner.get_recommendation(rec_id)
    assert {r["status"] for r in planner.list_recommendations(SHIFT)} == {"pending"}
    assert len(planner.list_recommendations(SHIFT, status="pending")) == 2
    assert planner.list_recommendations(SHIFT, status="approved") == []
    assert planner.list_recommendations("some-other-shift") == []
    assert planner.get_recommendation("no-such-id") is None


def test_decide_validates_and_is_final(tmp_db, monkeypatch):
    set_current(monkeypatch, 85.0)
    first = planner.evaluate(SHIFT, "task-idler-s3", publish=False)
    rec_id, rev = first["recommendation_id"], first["revision"]

    with pytest.raises(ValueError):
        planner.decide(rec_id, "auto", "Sam Supervisor", revision=rev)
    with pytest.raises(ValueError):
        planner.decide(rec_id, "approved", "", revision=rev)
    with pytest.raises(ValueError):
        planner.decide(rec_id, "approved", "   ", revision=rev)
    with pytest.raises(ValueError):
        planner.decide(rec_id, "approved", "Sam Supervisor")            # no revision quoted
    with pytest.raises(KeyError):
        planner.decide("no-such-id", "approved", "Sam Supervisor", revision=rev)
    assert planner.get_recommendation(rec_id)["status"] == "pending"   # nothing changed

    decided = planner.decide(rec_id, "approved", "Sam Supervisor", note="Use the low-vib wrench",
                             revision=rev)
    assert decided["status"] == "approved" and decided["decision"] == "approved"
    assert decided["decided_by"] == "Sam Supervisor"
    assert decided["decided_at"] is not None
    assert decided["decision_note"] == "Use the low-vib wrench"

    with pytest.raises(ValueError):
        planner.decide(rec_id, "dismissed", "Someone Else", revision=rev)
    assert planner.get_recommendation(rec_id)["status"] == "approved"

    # a later evaluate creates a new pending record; the decided one is unchanged
    set_current(monkeypatch, 200.0)
    new = planner.evaluate(SHIFT, "task-idler-s3", publish=False)
    assert new["recommendation_id"] != rec_id and new["status"] == "pending"
    old = planner.get_recommendation(rec_id)
    assert old == decided
    assert old["current_points"] == 85.0
    assert old["projected_points"] == pytest.approx(138.33, abs=0.005)
    assert len(planner.list_recommendations(SHIFT)) == 2
    assert [r["recommendation_id"] for r in planner.list_recommendations(SHIFT, "pending")] \
        == [new["recommendation_id"]]

    dismissed = planner.decide(new["recommendation_id"], "dismissed", "Sam Supervisor",
                               revision=new["revision"])
    assert dismissed["status"] == "dismissed" and dismissed["decision_note"] is None


# ------------------------------------------------------------------ bus

def test_evaluate_publishes_planning_recommendation(test_bus, monkeypatch):
    set_current(monkeypatch, 85.0)
    seen = []
    test_bus.subscribe(seen.append, types=["planning_recommendation"])
    rec = planner.evaluate(SHIFT, "task-idler-s3")
    test_bus.drain()
    assert len(seen) == 1
    event = seen[0]
    assert event.type == "planning_recommendation"
    assert event.shift_id == SHIFT and event.worker_id == WORKER
    assert event.source == "derived"
    assert CONTRACT_KEYS <= set(event.payload)
    assert event.payload["requires_approval"] is True
    assert event.payload["task_id"] == "task-idler-s3"
    assert event.payload["current_points"] == 85.0
    assert event.payload["additional_points"] == pytest.approx(53.33, abs=0.005)
    assert event.payload["projected_points"] == pytest.approx(138.33, abs=0.005)
    assert event.payload["crosses_action"] is True and event.payload["crosses_limit"] is False
    assert event.payload["recommendation_id"] == rec["recommendation_id"]
    assert event.payload["label"] == exposure.LABEL

    planner.evaluate(SHIFT, "task-idler-s3", publish=False)
    test_bus.drain()
    assert len(seen) == 1                        # publish=False publishes nothing


def test_exposure_updated_triggers_re_evaluation_without_a_loop(test_bus, monkeypatch):
    set_current(monkeypatch, 85.0)               # recompute raises if the planner ever calls it
    seen = []
    test_bus.subscribe(seen.append)
    planner.install(test_bus)
    planner.install(test_bus)                    # idempotent: still one subscription

    expected = tasks.next_task(SHIFT)["task_id"]
    assert expected == "task-idler-s3"
    test_bus.publish(Event("exposure_updated", {"total_points": 85.0, "status": "ok"},
                           "derived", SHIFT, WORKER))
    test_bus.drain()
    test_bus.drain()

    pending = planner.list_recommendations(SHIFT, status="pending")
    assert [r["task_id"] for r in pending] == [expected]
    assert pending[0]["status"] == "pending" and pending[0]["decided_by"] is None
    assert pending[0]["projected_points"] == pytest.approx(138.33, abs=0.005)
    types = [e.type for e in seen]
    assert types.count("exposure_updated") == 1           # no event loop
    assert types.count("planning_recommendation") == 1    # installed once, evaluated once

    # a second update re-evaluates the same pending record (no duplicates)
    set_current(monkeypatch, 95.0)
    test_bus.publish(Event("exposure_updated", {"total_points": 95.0, "status": "ok"},
                           "derived", SHIFT, WORKER))
    test_bus.drain()
    again = planner.list_recommendations(SHIFT)
    assert len(again) == 1
    assert again[0]["recommendation_id"] == pending[0]["recommendation_id"]
    # 95 + 53.33 = 148.33
    assert again[0]["projected_points"] == pytest.approx(148.33, abs=0.005)
    assert again[0]["status"] == "pending"


def test_re_evaluation_follows_the_next_task_and_survives_no_task(test_bus, monkeypatch):
    set_current(monkeypatch, 85.0)
    planner.install(test_bus)
    tasks.set_status("task-idler-s3", "done")
    test_bus.publish(Event("exposure_updated", {}, "derived", SHIFT, WORKER))
    test_bus.drain()
    assert [r["task_id"] for r in planner.list_recommendations(SHIFT)] == ["task-bracket-s3"]

    # a shift with no plan: nothing to evaluate, nothing raised
    test_bus.publish(Event("exposure_updated", {}, "derived", "shift-without-plan", WORKER))
    test_bus.drain()
    assert planner.list_recommendations("shift-without-plan") == []


# ------------------------------------------------------------------ review fixes

def test_decision_on_a_changed_recommendation_is_refused(tmp_db, monkeypatch):
    # Reviewer saw 10 + 53.33 = 63.33 (below action). Exposure then rises to 380:
    # 380 + 53.33 = 433.33 reaches the limit value. The old approval must not attach to it.
    set_current(monkeypatch, 10.0)
    seen = planner.evaluate(SHIFT, "task-idler-s3", publish=False)
    set_current(monkeypatch, 380.0)
    now = planner.evaluate(SHIFT, "task-idler-s3", publish=False)
    assert now["recommendation_id"] == seen["recommendation_id"]
    assert now["revision"] != seen["revision"] and now["crosses_limit"] is True
    with pytest.raises(ValueError):
        planner.decide(seen["recommendation_id"], "approved", "Sam", revision=seen["revision"])
    assert planner.get_recommendation(seen["recommendation_id"])["status"] == "pending"
    # an unchanged re-evaluation keeps the revision, so a decision on it still goes through
    again = planner.evaluate(SHIFT, "task-idler-s3", publish=False)
    assert again["revision"] == now["revision"]
    done = planner.decide(now["recommendation_id"], "dismissed", "Sam", revision=now["revision"])
    assert done["status"] == "dismissed"


def test_task_with_trigger_time_but_no_tool_gets_no_number(tmp_db, monkeypatch):
    set_current(monkeypatch, 20.0)
    tasks.upsert_task("t-notool", SHIFT, "Cut bracket, tool to be decided", None, None, 30)
    rec = planner.evaluate(SHIFT, "t-notool", publish=False)
    assert rec["projection_available"] is False
    assert rec["additional_points"] is None and rec["projected_points"] is None
    assert rec["crosses_action"] is None and rec["crosses_limit"] is None
    assert REVIEW_KINDS <= set(kinds(rec))
    assert "names no tool" in all_text(rec)


def test_rounded_current_figure_cannot_hide_a_crossing(tmp_db, monkeypatch):
    # real current 46.674 displays as 46.67. Task: 8 m/s2 for 24.9968 min
    # = 2 * 64 * 24.9968 / 60 = 53.32651. 46.674 + 53.32651 = 100.00051 -> crosses 100,
    # but 46.67 + 53.32651 = 99.99651 would not. The planner must use the unrounded figure.
    def fake_compute(shift_id):
        return {"total_points": 46.67, "total_points_exact": 46.674, "status": "ok",
                "entries": [], "reasons": []}
    monkeypatch.setattr(exposure, "compute", fake_compute)
    tasks.upsert_task("t-edge", SHIFT, "Edge case", None, "impact_wrench", 24.9968)
    rec = planner.evaluate(SHIFT, "t-edge", publish=False)
    assert rec["crosses_action"] is True and rec["projected_level"] == "above_action"


def test_recommendation_on_replay_data_is_labelled_with_real_exposure(test_bus, monkeypatch):
    # No mock of exposure.compute here: the planner reads the real engine.
    # Replay session, impact wrench 8 m/s2 for 2400 s: 2 * 64 * 2400/3600 = 85.33 points.
    # Task adds 53.33 -> 138.67 projected, crossing the action value.
    monkeypatch.setattr(exposure, "_toolsense_sessions", lambda shift_id: None)
    seen = []
    test_bus.subscribe(seen.append, types=["planning_recommendation"])
    exposure.install(test_bus)
    planner.install(test_bus)
    test_bus.publish(Event("tool_session_ended", {
        "session_id": "rp1", "start_ts": 0.0, "end_ts": 2400.0, "duration_s": 2400.0,
        "tool_id": "impact_wrench", "confidence": 0.9, "completeness": 0.99,
        "confirmation": "confirmed"}, "replay", SHIFT, WORKER))
    for _ in range(3):
        test_bus.drain()
    assert seen, "no planning_recommendation published"
    event = seen[-1]
    assert event.source == "replay"                       # never presented as live
    rec = event.payload
    assert rec["contains_simulated"] is True and "replay" in rec["current_sources"]
    assert "simulated" in " ".join(rec["reasons"]).lower()
    assert rec["current_points"] == pytest.approx(85.33, abs=0.005)
    assert rec["projected_points"] == pytest.approx(138.67, abs=0.005)
    assert rec["crosses_action"] is True and rec["status"] == "pending"
