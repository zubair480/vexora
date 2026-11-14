"""Shared fixtures. Owned by the integration session."""
import pytest

from backend import config, database
from backend.event_bus import EventBus


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    database.reset_for_tests()
    yield tmp_path / "test.db"
    database.reset_for_tests()


@pytest.fixture()
def test_bus(tmp_db, monkeypatch):
    """A started bus on a temp database, also installed as `event_bus.bus`."""
    from backend import event_bus
    bus = EventBus()
    monkeypatch.setattr(event_bus, "bus", bus)
    bus.start()
    yield bus
    bus.stop()
