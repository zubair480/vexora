"""Shared fixtures: every test gets its own SQLite file and can never reach a network."""
import urllib.request

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKORDER_DB", str(tmp_path / "workorders-test.db"))
    for name in ("WORKORDER_PROVIDER", "ODOO_URL", "ODOO_API_KEY", "ODOO_DB"):
        monkeypatch.delenv(name, raising=False)

    def no_network(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    return tmp_path


@pytest.fixture
def client():
    from workorder.app import app
    return TestClient(app)
