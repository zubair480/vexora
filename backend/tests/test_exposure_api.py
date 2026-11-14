"""HTTP tests for /api/exposure (router -> exposure / planner / tasks / tool_profiles)."""
import time

import pytest
from fastapi.testclient import TestClient

from backend import exposure, tool_profiles

API = "/api/exposure"
TASK = "task-idler-s3"


@pytest.fixture()
def client(tmp_db):
    from backend.main import app
    with TestClient(app) as c:      # lifespan starts the global bus
        yield c
        _settle()


def _settle() -> None:
    """Let the global bus finish handlers triggered by the last request."""
    from backend import event_bus
    event_bus.bus.drain()
    time.sleep(0.05)


def _rec(client, rec_id):
    recs = client.get(f"{API}/plan/recommendations").json()["recommendations"]
    return next(r for r in recs if r["recommendation_id"] == rec_id)


def test_current_has_label_and_status(client):
    r = client.get(f"{API}/current")
    assert r.status_code == 200
    body = r.json()
    assert body["label"] == exposure.LABEL
    assert "not certified measurement" in body["label"]
    assert body["status"] in ("ok", "needs_confirmation", "unavailable")
    assert body["shift_id"] == "shift-demo"
    assert client.get(f"{API}/current", params={"shift_id": "other"}).json()["shift_id"] == "other"


def test_tools_lists_seeds_with_source(client):
    body = client.get(f"{API}/tools").json()
    assert body["label"] == exposure.LABEL
    tools = {t["tool_id"]: t for t in body["tools"]}
    assert {"impact_wrench", "angle_grinder", "low_vibration_wrench", "needle_scaler"} <= set(tools)
    assert tools["impact_wrench"]["a_hv_ms2"] == 8.0
    assert tools["impact_wrench"]["source"] == tool_profiles.EXAMPLE_SOURCE
    assert tools["needle_scaler"]["a_hv_ms2"] is None
    assert tools["needle_scaler"]["source"] == tool_profiles.NO_VALUE_SOURCE


def test_put_tool_sets_validates_and_clears(client):
    r = client.put(f"{API}/tools/needle_scaler",
                   json={"name": "Needle scaler", "a_hv_ms2": 11.5, "source": "Manufacturer declared"})
    assert r.status_code == 200
    assert r.json()["a_hv_ms2"] == 11.5
    assert r.json()["source"] == "Manufacturer declared"
    tools = {t["tool_id"]: t for t in client.get(f"{API}/tools").json()["tools"]}
    assert tools["needle_scaler"]["a_hv_ms2"] == 11.5

    # a value needs a source
    r = client.put(f"{API}/tools/needle_scaler", json={"name": "Needle scaler", "a_hv_ms2": 9.0})
    assert r.status_code == 422
    # non-positive values are refused
    for bad in (0, -3.0):
        r = client.put(f"{API}/tools/needle_scaler",
                       json={"name": "Needle scaler", "a_hv_ms2": bad, "source": "x"})
        assert r.status_code == 422
    tools = {t["tool_id"]: t for t in client.get(f"{API}/tools").json()["tools"]}
    assert tools["needle_scaler"]["a_hv_ms2"] == 11.5     # rejected writes changed nothing

    # null clears the value
    r = client.put(f"{API}/tools/needle_scaler", json={"name": "Needle scaler", "a_hv_ms2": None})
    assert r.status_code == 200
    assert r.json()["a_hv_ms2"] is None
    assert r.json()["source"] == tool_profiles.NO_VALUE_SOURCE


def test_manual_entry_counts_points(client):
    r = client.post(f"{API}/manual", json={"tool_id": "impact_wrench", "minutes": 25})
    assert r.status_code == 200
    assert r.json()["label"] == exposure.LABEL
    body = client.get(f"{API}/current").json()
    # points = 2 * a^2 * hours = 2 * 8^2 * (25/60) = 2 * 64 * 25 / 60 = 53.33
    assert body["total_points"] == pytest.approx(53.33, abs=0.005)
    assert body["level"] == "below_action"
    assert len(body["entries"]) == 1
    entry = body["entries"][0]
    assert entry["kind"] == "manual"
    assert entry["source"] == "manual"
    assert entry["tool_id"] == "impact_wrench"
    assert entry["a_hv_ms2"] == 8.0
    assert entry["points"] == pytest.approx(53.33, abs=0.005)


@pytest.mark.parametrize("minutes", [0, -5, "abc"])
def test_manual_invalid_minutes_422(client, minutes):
    r = client.post(f"{API}/manual", json={"tool_id": "impact_wrench", "minutes": minutes})
    assert r.status_code == 422
    assert client.get(f"{API}/current").json()["entries"] == []


def test_tasks_lists_seeds(client):
    body = client.get(f"{API}/tasks").json()
    assert [t["task_id"] for t in body["tasks"]] == [TASK, "task-bracket-s3", "task-guard-s3"]
    assert body["tasks"][0]["tool_id"] == "impact_wrench"
    assert body["tasks"][0]["expected_trigger_minutes"] == 25
    assert {w["worker_id"] for w in body["workers"]} == {"worker-1", "worker-2", "worker-3"}
    assert body["next_task_id"] == TASK


def test_plan_evaluate_pending_and_unknown_task(client):
    r = client.post(f"{API}/plan/evaluate", json={"task_id": TASK})
    assert r.status_code == 200
    rec = r.json()
    assert rec["task_id"] == TASK
    assert rec["requires_approval"] is True
    assert rec["status"] == "pending"
    assert rec["label"] == exposure.LABEL
    assert client.post(f"{API}/plan/evaluate", json={"task_id": "no-such-task"}).status_code == 404

    body = client.get(f"{API}/plan/recommendations").json()
    assert body["label"] == exposure.LABEL
    listed = [x for x in body["recommendations"] if x["recommendation_id"] == rec["recommendation_id"]]
    assert len(listed) == 1 and listed[0]["status"] == "pending"
    pending = client.get(f"{API}/plan/recommendations", params={"status": "pending"}).json()
    assert rec["recommendation_id"] in [x["recommendation_id"] for x in pending["recommendations"]]
    approved = client.get(f"{API}/plan/recommendations", params={"status": "approved"}).json()
    assert approved["recommendations"] == []


def test_decision_flow(client):
    rec_id = client.post(f"{API}/plan/evaluate", json={"task_id": TASK}).json()["recommendation_id"]
    url = f"{API}/plan/recommendations/{rec_id}/decision"

    rev = _rec(client, rec_id)["revision"]
    assert client.post(url, json={"decision": "approved", "revision": rev}).status_code == 422   # no decided_by
    assert client.post(url, json={"decision": "approved", "decided_by": "", "revision": rev}).status_code == 422
    assert client.post(url, json={"decision": "maybe", "decided_by": "Sam", "revision": rev}).status_code == 422
    # the reviewer must quote the version they saw: missing or stale revision is refused
    assert client.post(url, json={"decision": "approved", "decided_by": "Sam"}).status_code == 422
    assert client.post(url, json={"decision": "approved", "decided_by": "Sam",
                                  "revision": "stale"}).status_code == 422
    assert _rec(client, rec_id)["status"] == "pending"

    r = client.post(url, json={"decision": "approved", "decided_by": "Sam Supervisor", "note": "ok",
                               "revision": rev})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    assert r.json()["decided_by"] == "Sam Supervisor"
    assert _rec(client, rec_id)["status"] == "approved"

    # decided recommendations are immutable
    assert client.post(url, json={"decision": "dismissed", "decided_by": "Sam",
                                  "revision": rev}).status_code == 422
    assert _rec(client, rec_id)["status"] == "approved"

    r = client.post(f"{API}/plan/recommendations/no-such-id/decision",
                    json={"decision": "approved", "decided_by": "Sam", "revision": rev})
    assert r.status_code == 404


def _exercise_everything_but_decision(client):
    assert client.get(f"{API}/current").status_code == 200
    assert client.post(f"{API}/manual", json={"tool_id": "impact_wrench", "minutes": 300}).status_code == 200
    assert client.put(f"{API}/tools/impact_wrench", json={
        "name": "Impact wrench", "a_hv_ms2": 12.0, "source": "Manufacturer declared"}).status_code == 200
    assert client.get(f"{API}/tools").status_code == 200
    assert client.get(f"{API}/tasks").status_code == 200
    assert client.post(f"{API}/plan/evaluate", json={"task_id": TASK}).status_code == 200
    assert client.get(f"{API}/plan/recommendations").status_code == 200
    _settle()   # bus-side planner re-evaluations triggered by exposure_updated


def test_only_decision_endpoint_changes_status(client):
    rec_id = client.post(f"{API}/plan/evaluate", json={"task_id": TASK}).json()["recommendation_id"]

    _exercise_everything_but_decision(client)
    recs = client.get(f"{API}/plan/recommendations").json()["recommendations"]
    assert recs and all(r["status"] == "pending" for r in recs)     # nothing auto-approved
    assert _rec(client, rec_id)["status"] == "pending"

    r = client.post(f"{API}/plan/recommendations/{rec_id}/decision",
                    json={"decision": "dismissed", "decided_by": "Sam Supervisor",
                          "revision": _rec(client, rec_id)["revision"]})
    assert r.status_code == 200 and r.json()["status"] == "dismissed"

    _exercise_everything_but_decision(client)
    assert _rec(client, rec_id)["status"] == "dismissed"            # decided one stays as decided
    others = [r for r in client.get(f"{API}/plan/recommendations").json()["recommendations"]
              if r["recommendation_id"] != rec_id]
    assert others and all(r["status"] == "pending" for r in others)  # re-evaluation made a new pending one
