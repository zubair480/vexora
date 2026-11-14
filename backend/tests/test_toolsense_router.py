"""toolsense HTTP endpoints on a temp DB and temp models / recordings folders."""
import time

import pytest
from fastapi.testclient import TestClient

from backend import config, event_bus, tool_sessions
from backend.main import app   # imported at collection time: the service wires to the global bus


@pytest.fixture()
def client(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODELS", tmp_path / "models")
    monkeypatch.setattr(config, "RECORDINGS", tmp_path / "recordings")
    (tmp_path / "recordings").mkdir()
    tool_sessions.get_service().reload_classifier()      # now looks at the empty temp models dir
    with TestClient(app) as c:
        yield c
        event_bus.bus.drain()


def _row(session_id, end_ts=1010.0, shift_id=None):
    shift = config.current_shift()
    row = {"session_id": session_id, "shift_id": shift_id or shift["shift_id"],
           "worker_id": shift["worker_id"], "start_ts": 1000.0, "end_ts": end_ts,
           "duration_s": None if end_ts is None else end_ts - 1000.0, "tool_id": None,
           "confidence": 0.9, "completeness": 1.0, "confirmation": "tentative",
           "source": "synthetic", "model": "rules", "reason": None, "updated_at": time.time()}
    tool_sessions._save(row)
    return row


def test_state_uses_rules_without_a_model(client):
    body = client.get("/api/toolsense/state").json()
    assert body["model"] == "rules" and body["estimated"] is True
    assert body["activity"] == "unknown" and body["in_session"] is False
    assert body["model_info"]["model"] == "rules"
    assert {"confidence", "tool_probability", "probabilities", "completeness", "stale", "source",
            "session_id", "last_sample_time", "updated_at"} <= set(body)


def test_sessions_and_confirm(client):
    assert client.get("/api/toolsense/sessions").json() == []
    _row("done-1")
    _row("open-1", end_ts=None)
    _row("other-1", shift_id="shift-other")
    listed = client.get("/api/toolsense/sessions").json()
    assert sorted(s["session_id"] for s in listed) == ["done-1", "open-1"]
    other = client.get("/api/toolsense/sessions", params={"shift_id": "shift-other"}).json()
    assert [s["session_id"] for s in other] == ["other-1"]

    ok = client.post("/api/toolsense/sessions/done-1/confirm",
                     json={"tool_id": "grinder-1", "confirmed": True})
    assert ok.status_code == 200
    assert ok.json()["confirmation"] == "confirmed" and ok.json()["tool_id"] == "grinder-1"
    stored = {s["session_id"]: s for s in client.get("/api/toolsense/sessions").json()}
    assert stored["done-1"]["confirmation"] == "confirmed"

    event_bus.bus.drain()                                # the app's bus persisted the republish
    events = client.get("/api/events").json()
    assert [e["type"] for e in events] == ["tool_session_ended"]
    assert events[0]["source"] == "synthetic" and events[0]["payload"]["tool_id"] == "grinder-1"

    assert client.post("/api/toolsense/sessions/open-1/confirm",
                       json={"tool_id": None, "confirmed": True}).status_code == 409
    assert client.post("/api/toolsense/sessions/missing/confirm",
                       json={"tool_id": None, "confirmed": False}).status_code == 404


def test_report_without_training(client):
    body = client.get("/api/toolsense/report").json()
    assert body["status"] == "no_report"
    assert body["collection"]["complete"] is False


def test_train_on_empty_recordings(client, tmp_path):
    assert client.post("/api/toolsense/train").json()["status"] == "no_data"
    assert client.post("/api/toolsense/train", json={"include_synthetic": False}).json()["status"] == "no_data"
    assert not list((tmp_path / "models").glob("*.joblib"))       # no model written
    assert client.get("/api/toolsense/report").json()["status"] == "no_data"
    assert client.get("/api/toolsense/state").json()["model"] == "rules"
