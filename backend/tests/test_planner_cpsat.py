"""CP-SAT shift-plan proposals (be-hav). Expected numbers are hand-derived in comments.

Seed plan: idler = impact wrench 8 m/s² x 25 min = 2*64*(25/60) = 53.33 points;
bracket = angle grinder 6 m/s² x 15 min = 2*36*0.25 = 18 points (depends on idler);
guard = no tool, 0 points (depends on bracket). worker-1 (band wearer) and worker-2 are verified
mechanical fitters; worker-3 has unknown qualifications.
"""
import json

import pytest

from backend import config, database, event_bus, exposure, planner_cpsat, tasks, tool_profiles

SHIFT = config.current_shift()["shift_id"]
WEARER = config.current_shift()["worker_id"]


def set_current(monkeypatch, total_points, status="ok"):
    def fake_compute(shift_id):
        return {"label": exposure.LABEL, "shift_id": shift_id, "total_points": total_points,
                "status": status, "entries": [], "reasons": []}

    def forbidden(*args, **kwargs):
        raise AssertionError("planner_cpsat must not publish or recompute")

    monkeypatch.setattr(exposure, "compute", fake_compute)
    monkeypatch.setattr(exposure, "recompute", forbidden)
    monkeypatch.setattr(event_bus.bus, "publish", forbidden)


def assigned(plan_):
    return {a["task_id"]: a["worker_id"] for a in plan_["assignments"]}


def worker(plan_, worker_id):
    return next(w for w in plan_["workers"] if w["worker_id"] == worker_id)


def all_text(result):
    return json.dumps(result).lower()


def test_moves_high_vibration_task_to_qualified_second_worker(tmp_db, monkeypatch):
    set_current(monkeypatch, 60.0)          # wearer: 60 + 53.33 = 113.33 would cross the cap
    result = planner_cpsat.plan(SHIFT)
    assert result["feasible_under_cap"] is True and result["cap_relaxed"] is False
    best = result["plans"][0]
    got = assigned(best)
    assert got["task-idler-s3"] == "worker-2"
    # lowest maximum: both powered tasks on worker-2 (71.33) beats wearer at 78
    assert got["task-bracket-s3"] == "worker-2"
    assert worker(best, "worker-2")["projected_points"] == pytest.approx(71.33, abs=0.02)
    assert worker(best, WEARER)["projected_points"] == pytest.approx(60.0, abs=0.01)
    assert worker(best, WEARER)["current_basis"] == "estimated"
    assert worker(best, "worker-2")["current_basis"] == "not_measured"
    assert "not measured" in worker(best, "worker-2")["current_note"]
    assert all(w["below_cap"] for w in best["workers"])
    assert best["max_projected_points"] == pytest.approx(71.33, abs=0.02)


def test_alternative_plans_differ_and_all_need_approval(tmp_db, monkeypatch):
    set_current(monkeypatch, 0.0)
    result = planner_cpsat.plan(SHIFT, max_plans=3)
    assert result["requires_approval"] is True and result["status"] == "proposal"
    assert result["label"] == "Estimated HAV exposure — screening/planning, not certified measurement"
    assert 1 < len(result["plans"]) <= 3
    seen = set()
    for p in result["plans"]:
        assert p["requires_approval"] is True
        assert p["status"] == "proposal"
        assert p["label"] == result["label"]
        assert all(a["requires_approval"] is True for a in p["assignments"])
        seen.add(tuple(sorted(assigned(p).items())))
    assert len(seen) == len(result["plans"])
    # first plan is the best on the maximum: split 53.33 / 18
    assert result["plans"][0]["max_projected_points"] == pytest.approx(53.33, abs=0.02)
    assert len(planner_cpsat.plan(SHIFT, max_plans=1)["plans"]) == 1


def test_unknown_qualification_blocks_assignment(tmp_db, monkeypatch):
    set_current(monkeypatch, 0.0)
    tasks.upsert_worker("worker-2", "Worker 2", None, False)          # now unknown
    tasks.upsert_worker("worker-4", "Worker 4", ["mechanical_fitter"], False)   # unverified
    tasks.upsert_task("task-elec", SHIFT, "Isolate drive", "CV-102", None, 0.0, 2, "electrician")
    result = planner_cpsat.plan(SHIFT)
    for p in result["plans"]:
        assert set(assigned(p).values()) == {WEARER}
        assert "task-elec" not in assigned(p)
    blocked = {b["worker_id"]: b["reason"] for b in result["blocked_workers"]}
    assert set(blocked) == {"worker-2", "worker-3", "worker-4"}
    assert "unknown" in blocked["worker-2"] and "not verified" in blocked["worker-4"]
    reason = next(u["reason"] for u in result["unassigned"] if u["task_id"] == "task-elec")
    assert "electrician" in reason


def test_dependency_order_respected(tmp_db, monkeypatch):
    set_current(monkeypatch, 0.0)
    # a top-priority task that still has to wait for the lowest-priority one
    tasks.upsert_task("task-signoff", SHIFT, "Sign off", "CV-102", None, 0.0, 1, None,
                      ["task-guard-s3"])
    result = planner_cpsat.plan(SHIFT)
    assert result["plans"]
    for p in result["plans"]:
        order = p["order"]
        assert sorted(order) == sorted(assigned(p))
        assert order.index("task-idler-s3") < order.index("task-bracket-s3") \
            < order.index("task-guard-s3") < order.index("task-signoff")
        positions = {a["task_id"]: a["position"] for a in p["assignments"]}
        assert [positions[t] for t in order] == list(range(1, len(order) + 1))


def test_priority_orders_independent_tasks(tmp_db, monkeypatch):
    set_current(monkeypatch, 0.0)
    tasks.upsert_task("task-low", SHIFT, "Tidy", "CV-102", None, 0.0, 5, None)
    order = planner_cpsat.plan(SHIFT, max_plans=1)["plans"][0]["order"]
    assert order[0] == "task-idler-s3" and order[-1] == "task-low"


def test_task_without_tool_value_stays_unassigned_with_reason(tmp_db, monkeypatch):
    set_current(monkeypatch, 0.0)
    tasks.upsert_task("task-scale", SHIFT, "Descale chute", "CV-102", "needle_scaler", 10.0, 1,
                      "mechanical_fitter")
    tasks.upsert_task("task-after", SHIFT, "Paint chute", "CV-102", None, 0.0, 2, None,
                      ["task-scale"])
    tasks.upsert_task("task-notool", SHIFT, "Cut bolt", "CV-102", None, 5.0, 2, None)
    result = planner_cpsat.plan(SHIFT)
    reasons = {u["task_id"]: u["reason"] for u in result["unassigned"]}
    assert "no vibration value" in reasons["task-scale"]
    assert "no value is assumed" in reasons["task-scale"]
    assert "task-scale" in reasons["task-after"]            # dependent cannot be ordered
    assert "names no tool" in reasons["task-notool"]
    for p in result["plans"]:
        assert not {"task-scale", "task-after", "task-notool"} & set(assigned(p))
        assert set(assigned(p)) == {"task-idler-s3", "task-bracket-s3", "task-guard-s3"}
        assert {u["task_id"] for u in p["unassigned"]} == set(reasons)


def test_infeasible_cap_reported_honestly(tmp_db, monkeypatch):
    set_current(monkeypatch, 0.0)
    result = planner_cpsat.plan(SHIFT, exposure_cap=10.0)   # idler alone is 53.33
    assert result["feasible_under_cap"] is False and result["cap_relaxed"] is True
    assert any("relaxed" in r for r in result["reasons"])
    best = result["plans"][0]
    assert best["cap_relaxed"] is True and best["requires_approval"] is True
    assert any("relaxed" in r for r in best["reasons"])
    over = [w for w in best["workers"] if not w["below_cap"]]
    assert over and all(w["cap_excess_points"] > 0 for w in over)
    # minimum total excess: 53.33 and 18 on different workers -> (53.34-9.99)+(18-9.99) = 51.36
    assert sum(w["cap_excess_points"] for w in best["workers"]) == pytest.approx(51.36, abs=0.02)
    assert len(assigned(best)) == 3                         # work is still shown, not hidden


def test_wearer_already_over_cap_is_stated(tmp_db, monkeypatch):
    set_current(monkeypatch, 120.0)
    result = planner_cpsat.plan(SHIFT, max_plans=1)
    assert result["cap_relaxed"] is True
    best = result["plans"][0]
    got = assigned(best)
    assert got["task-idler-s3"] == got["task-bracket-s3"] == "worker-2"
    wearer = worker(best, WEARER)
    assert wearer["added_points"] == 0                      # no more exposure added to the wearer
    assert wearer["below_cap"] is False and wearer["projected_level"] == "above_action"


def test_never_claims_safety_and_never_persists(tmp_db, monkeypatch):
    set_current(monkeypatch, 0.0)
    tool_profiles.list_profiles()                           # existing seed-on-read, not the planner
    before = [(t["task_id"], t["status"]) for t in tasks.list_tasks(SHIFT, include_done=True)]
    con = database.connect()
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    con.close()
    result = planner_cpsat.plan(SHIFT, exposure_cap=400.0)
    text = all_text(result)
    assert "safe" not in text and "approved" not in text.replace("nothing is approved", "")
    con = database.connect()
    after = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    con.close()
    assert after == counts
    assert [(t["task_id"], t["status"]) for t in tasks.list_tasks(SHIFT, include_done=True)] == before
    for p in result["plans"]:
        for w in p["workers"]:
            assert any("as low as reasonably practicable" in n or "review" in n for n in w["notes"])


def test_unavailable_current_exposure_and_bad_arguments(tmp_db, monkeypatch):
    set_current(monkeypatch, None, status="unavailable")
    best = planner_cpsat.plan(SHIFT, max_plans=1)["plans"][0]
    wearer = worker(best, WEARER)
    assert wearer["current_points"] is None and wearer["current_basis"] == "unavailable"
    assert "unavailable" in wearer["current_note"]
    with pytest.raises(ValueError):
        planner_cpsat.plan(SHIFT, exposure_cap=0)
    with pytest.raises(ValueError):
        planner_cpsat.plan(SHIFT, max_plans=0)
