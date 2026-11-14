import pytest
from fastapi.testclient import TestClient

from backend import config, database
from backend.models import Event


def test_event_roundtrip(test_bus):
    seen = []
    test_bus.subscribe(seen.append, types=["defect_reported"])
    test_bus.publish(Event("defect_reported", {"asset_id": "A"}, "manual", "s1", "w1"))
    test_bus.publish(Event("sensor_quality", {"stale": False}, "band", "s1", "w1"))
    test_bus.drain()
    assert [e.type for e in seen] == ["defect_reported"]
    stored = database.list_events("s1")
    assert [e["type"] for e in stored] == ["defect_reported"]   # sensor_quality is live only
    assert stored[0]["payload"] == {"asset_id": "A"}


def test_unknown_type_and_source_rejected():
    with pytest.raises(ValueError):
        Event("made_up", {}, "band", "s", "w")
    with pytest.raises(ValueError):
        Event("defect_reported", {}, "guess", "s", "w")


def test_band_number_is_never_defaulted(monkeypatch):
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.delenv("VEXO_BAND", raising=False)
    with pytest.raises(config.BandNumberMissing):
        config.band_number()


def test_app_starts(tmp_db):
    from backend.main import app
    with TestClient(app) as client:
        assert client.get("/api/health").json()["ok"] is True
        assert client.get("/api/events").json() == []
