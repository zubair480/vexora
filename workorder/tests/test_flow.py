"""Tests for the work-order flow. Each test uses a temp SQLite file (see conftest.py); no network."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from workorder import flow, providers, store

FULL = {
    "asset_id": "HEAD-PULLEY-7", "problem": "Lagging worn, belt slipping", "task": "Replace pulley lagging",
    "priority": "medium", "repair_minutes": 90, "technician": "Sam Ortiz", "approved_by": "Dana Lee",
    "conveyor_id": "CV-201", "station_id": "S1",
}
ORDER = ["draft", "checks", "schedule", "work_order", "calendar", "verify"]


def names(record):
    return [s["name"] for s in record["steps"]]


def blocked_reason(record):
    assert record["status"] == "BLOCKED"
    last = record["steps"][-1]
    assert last["name"] == "blocked" and last["status"] == "blocked"
    return last["detail"]


def assert_nothing_created(record):
    assert "work_order" not in names(record)
    assert "calendar" not in names(record)
    assert not record.get("ref")
    assert not record.get("window_start")


# ---------------------------------------------------------------- 1. happy path
def test_happy_path_explicit_request(client):
    response = client.post("/api/workorder/run", json=FULL)
    assert response.status_code == 200
    record = response.json()
    assert record["status"] == "VERIFIED"
    assert record["id"] == "WO-0001"
    assert names(record) == ORDER
    assert all(s["status"] == "done" for s in record["steps"])
    assert record["sample"] is False
    assert record["request"]["asset_id"] == "HEAD-PULLEY-7"
    begin = datetime.fromisoformat(record["window_start"])
    end = datetime.fromisoformat(record["window_end"])
    assert end - begin == timedelta(minutes=90)
    assert begin > datetime.now(timezone.utc)
    cal = record["calendar"]
    assert cal["booked"] is False
    assert cal["google_link"].startswith("https://calendar.google.com/")
    assert "HEAD-PULLEY-7" in cal["google_link"]
    assert cal["ics_path"] == "/api/workorder/WO-0001/event.ics"
    assert record["ref"] == "WO-0001" and record["system"] == "beltguard"


# ---------------------------------------------------------------- 2. no body
def test_no_body_uses_sample_and_blocks_without_approval(client):
    response = client.post("/api/workorder/run")
    assert response.status_code == 200
    record = response.json()
    assert record["sample"] is True
    assert record["request"]["asset_id"] == flow.SAMPLE["asset_id"]
    assert "approved_by" in blocked_reason(record)
    assert names(record) == ["draft", "blocked"]
    assert record["reached"] == "DRAFT"
    assert_nothing_created(record)


def test_flow_run_none_is_sample_and_blocked():
    record = flow.run(None)
    assert record["sample"] is True
    assert "approved_by" in blocked_reason(record)


# ---------------------------------------------------------------- 3. sample + approval
def test_sample_with_approval_is_verified(client):
    record = client.post("/api/workorder/run", json={"approved_by": "Dana Lee"}).json()
    assert record["status"] == "VERIFIED"
    assert record["sample"] is True
    assert names(record) == ORDER
    assert record["steps"][0]["evidence"] == {"sample_data": True}


# ---------------------------------------------------------------- 4. guards
@pytest.mark.parametrize("key", ["asset_id", "problem", "task"])
def test_missing_required_field_blocks(key):
    request = {k: v for k, v in FULL.items() if k != key}
    record = flow.run(request)
    assert record["sample"] is False
    assert key in blocked_reason(record)
    assert_nothing_created(record)


@pytest.mark.parametrize("key", ["asset_id", "problem", "task"])
def test_blank_required_field_blocks(key):
    record = flow.run({**FULL, key: "   "})
    assert record["sample"] is False
    assert key in blocked_reason(record)
    assert record["reached"] == "DRAFT"
    assert_nothing_created(record)


def test_bad_priority_blocks():
    record = flow.run({**FULL, "priority": "urgent"})
    assert "priority" in blocked_reason(record)
    assert_nothing_created(record)


@pytest.mark.parametrize("minutes, word", [(0, "between"), (721, "between"), ("soon", "number")])
def test_bad_repair_minutes_blocks(minutes, word):
    record = flow.run({**FULL, "repair_minutes": minutes})
    reason = blocked_reason(record)
    assert "repair_minutes" in reason and word in reason
    assert_nothing_created(record)


def test_non_numeric_repair_minutes_rejected_by_api(client):
    response = client.post("/api/workorder/run", json={**FULL, "repair_minutes": "soon"})
    assert response.status_code == 422
    assert store.list_all() == []


def test_window_start_in_past_blocks():
    record = flow.run({**FULL, "window_start": "2020-01-01T08:00:00Z"})
    assert "past" in blocked_reason(record)
    assert record["reached"] == "CHECKED"
    assert_nothing_created(record)


def test_window_start_not_a_timestamp_blocks():
    record = flow.run({**FULL, "window_start": "tomorrow morning"})
    reason = blocked_reason(record)
    assert "not a timestamp" in reason and "tomorrow morning" in reason
    assert_nothing_created(record)


def test_guards_never_call_the_external_provider(monkeypatch):
    calls = []
    monkeypatch.setenv("WORKORDER_PROVIDER", "odoo")
    monkeypatch.setattr(providers.OdooProvider, "_call", lambda self, *a: calls.append(a) or [1])
    for bad in ({"priority": "urgent"}, {"repair_minutes": 0}, {"window_start": "2020-01-01T08:00:00Z"},
                {"window_start": "nope"}, {"asset_id": " "}, {"approved_by": None}):
        request = {**FULL, **bad}
        assert flow.run(request)["status"] == "BLOCKED"
    assert calls == []


# ---------------------------------------------------------------- 5. next_window
def test_next_window_default_on_the_hour():
    now = datetime(2030, 3, 4, 10, 0, 0, tzinfo=timezone.utc)
    begin, end = flow.next_window(60, now=now)
    assert begin == datetime(2030, 3, 4, 10, 30, tzinfo=timezone.utc)
    assert end - begin == timedelta(minutes=60)


@pytest.mark.parametrize("now", [
    datetime(2030, 3, 4, 10, 7, 0, tzinfo=timezone.utc),
    datetime(2030, 3, 4, 10, 29, 0, tzinfo=timezone.utc),
    datetime(2030, 3, 4, 10, 31, 0, tzinfo=timezone.utc),
    datetime(2030, 3, 4, 23, 45, 0, tzinfo=timezone.utc),
])
def test_next_window_default_is_half_hour_at_least_30_minutes_ahead(now):
    begin, end = flow.next_window(45, now=now)
    assert begin - now >= timedelta(minutes=30)
    assert begin - now < timedelta(minutes=60)
    assert begin.minute in (0, 30) and begin.second == 0 and begin.microsecond == 0
    assert end - begin == timedelta(minutes=45)


def test_next_window_with_seconds_is_still_30_minutes_ahead():
    now = datetime(2030, 3, 4, 10, 0, 30, tzinfo=timezone.utc)
    begin, _ = flow.next_window(60, now=now)
    assert begin - now >= timedelta(minutes=30)


def test_next_window_explicit_future_start_is_honoured():
    now = datetime(2030, 3, 4, 10, 0, 0, tzinfo=timezone.utc)
    begin, end = flow.next_window(20, now=now, start="2030-03-05T14:15:00Z")
    assert begin == datetime(2030, 3, 5, 14, 15, tzinfo=timezone.utc)
    assert end == datetime(2030, 3, 5, 14, 35, tzinfo=timezone.utc)


def test_next_window_explicit_start_errors():
    now = datetime(2030, 3, 4, 10, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(flow.Blocked, match="past"):
        flow.next_window(20, now=now, start="2030-03-04T09:00:00+00:00")
    with pytest.raises(flow.Blocked, match="not a timestamp"):
        flow.next_window(20, now=now, start="half past nine")


def test_explicit_window_start_through_the_flow():
    record = flow.run({**FULL, "window_start": "2031-06-01T09:00:00+02:00"})
    assert record["status"] == "VERIFIED"
    assert datetime.fromisoformat(record["window_start"]) == datetime(2031, 6, 1, 7, 0, tzinfo=timezone.utc)
    assert datetime.fromisoformat(record["window_end"]) == datetime(2031, 6, 1, 8, 30, tzinfo=timezone.utc)


# ---------------------------------------------------------------- 6. ids and listing
def test_ids_increase_and_list_is_newest_first(client):
    first = client.post("/api/workorder/run", json=FULL).json()
    second = client.post("/api/workorder/run", json={**FULL, "asset_id": "TAIL-PULLEY-2"}).json()
    third = client.post("/api/workorder/run").json()          # blocked records get an id too
    assert [first["id"], second["id"], third["id"]] == ["WO-0001", "WO-0002", "WO-0003"]
    listed = client.get("/api/workorder").json()
    assert [r["id"] for r in listed] == ["WO-0003", "WO-0002", "WO-0001"]
    assert listed[0]["status"] == "BLOCKED" and listed[1]["request"]["asset_id"] == "TAIL-PULLEY-2"


def test_get_work_order_and_unknown_id(client):
    created = client.post("/api/workorder/run", json=FULL).json()
    fetched = client.get(f"/api/workorder/{created['id']}")
    assert fetched.status_code == 200 and fetched.json() == created
    missing = client.get("/api/workorder/WO-9999")
    assert missing.status_code == 404
    assert "WO-9999" in missing.json()["detail"]


def test_database_is_the_temp_file(isolated):
    flow.run(FULL)
    assert store.db_path() == isolated / "workorders-test.db"
    assert store.db_path().exists()


# ---------------------------------------------------------------- 7. calendar file
def test_event_ics(client):
    request = {**FULL, "problem": "Lagging worn, belt slipping; noisy", "window_start": "2031-06-01T09:00:00+02:00"}
    record = client.post("/api/workorder/run", json=request).json()
    assert record["status"] == "VERIFIED"
    response = client.get(record["calendar"]["ics_path"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert f'{record["id"]}.ics' in response.headers["content-disposition"]
    lines = response.text.split("\r\n")
    assert lines[0] == "BEGIN:VCALENDAR" and "BEGIN:VEVENT" in lines and "END:VEVENT" in lines
    assert "DTSTART:20310601T070000Z" in lines
    assert "DTEND:20310601T083000Z" in lines
    assert f"UID:{record['id']}@beltguard" in lines
    description = next(line for line in lines if line.startswith("DESCRIPTION:"))
    assert record["id"] in description
    assert "Lagging worn\\, belt slipping\\; noisy" in description
    assert "worn, belt" not in description and "slipping; noisy" not in description
    assert "LOCATION:CV-201 S1" in lines


def test_event_ics_404_for_blocked_and_unknown(client):
    blocked = client.post("/api/workorder/run").json()
    assert blocked["status"] == "BLOCKED"
    response = client.get(f"/api/workorder/{blocked['id']}/event.ics")
    assert response.status_code == 404
    assert blocked["id"] in response.json()["detail"]
    assert client.get("/api/workorder/WO-9999/event.ics").status_code == 404


# ---------------------------------------------------------------- 8. Odoo provider
@pytest.fixture
def odoo(monkeypatch):
    """Odoo selected and `_call` replaced; returns the list of recorded calls and a settable read-back name."""
    monkeypatch.setenv("WORKORDER_PROVIDER", "odoo")
    monkeypatch.setenv("ODOO_URL", "https://odoo.invalid/")
    monkeypatch.setenv("ODOO_API_KEY", "test-key")
    state = {"calls": [], "name": None, "error": None}

    def fake_call(self, model, method, body):
        state["calls"].append((model, method, body))
        if state["error"]:
            raise providers.ProviderError(state["error"])
        if method == "create":
            return [42]
        name = state["name"] if state["name"] is not None else body_name(state)
        return [{"id": 42, "name": name, "schedule_date": "2031-06-01 07:00:00"}]

    def body_name(st):
        return st["calls"][0][2]["vals_list"][0]["name"]

    monkeypatch.setattr(providers.OdooProvider, "_call", fake_call)
    return state


def test_odoo_create_and_verify(odoo):
    assert isinstance(providers.work_order_provider(), providers.OdooProvider)
    record = flow.run({**FULL, "window_start": "2031-06-01T09:00:00+02:00"})
    assert record["status"] == "VERIFIED"
    assert record["ref"] == "maintenance.request:42" and record["system"] == "odoo"
    assert names(record) == ORDER
    (model, method, body), (model2, method2, body2) = odoo["calls"]
    assert (model, method) == ("maintenance.request", "create")
    vals = body["vals_list"][0]
    assert vals["name"].startswith(record["id"]) and "HEAD-PULLEY-7" in vals["name"]
    assert vals["schedule_date"] == "2031-06-01 07:00:00"
    assert vals["duration"] == 1.5 and vals["priority"] == "2"
    assert (model2, method2) == ("maintenance.request", "read") and body2["ids"] == [42]
    work_order = record["steps"][3]["evidence"]
    assert work_order["url"].startswith("https://odoo.invalid/odoo/") and work_order["url"].endswith("/42")


def test_odoo_verify_fails_when_name_does_not_contain_asset(odoo):
    odoo["name"] = "WO-0001: something else entirely"
    record = flow.run(FULL)
    reason = blocked_reason(record)
    assert "verification failed" in reason and "HEAD-PULLEY-7" in reason
    assert record["reached"] == "CALENDAR_READY"
    assert "verify" not in names(record)


def test_odoo_verify_unit(odoo):
    provider = providers.OdooProvider()
    now = datetime(2031, 6, 1, 7, 0, tzinfo=timezone.utc)
    odoo["calls"].append(("maintenance.request", "create", {"vals_list": [{"name": "WO-0001: x — HEAD-PULLEY-7"}]}))
    assert provider.verify("maintenance.request:42", FULL, now, now) is None
    odoo["name"] = "unrelated"
    assert "HEAD-PULLEY-7" in provider.verify("maintenance.request:42", FULL, now, now)


def test_odoo_provider_error_blocks_at_scheduled(odoo):
    odoo["error"] = "Odoo maintenance.request.create returned 404"
    record = flow.run(FULL)
    assert blocked_reason(record) == "Odoo maintenance.request.create returned 404"
    assert record["reached"] == "SCHEDULED"
    assert names(record) == ["draft", "checks", "schedule", "blocked"]
    assert not record.get("ref") and "calendar" not in record


def test_odoo_empty_create_result_blocks(odoo, monkeypatch):
    monkeypatch.setattr(providers.OdooProvider, "_call", lambda self, *a: [])
    record = flow.run(FULL)
    assert "record id" in blocked_reason(record)
    assert record["reached"] == "SCHEDULED"


def test_odoo_not_configured_blocks(monkeypatch):
    monkeypatch.setenv("WORKORDER_PROVIDER", "odoo")          # ODOO_URL unset: the real _call must refuse
    record = flow.run(FULL)
    assert "not configured" in blocked_reason(record)
    assert record["reached"] == "SCHEDULED"
    assert_nothing_created(record)


def test_ref_is_kept_when_verification_fails_after_creation(odoo):
    odoo["name"] = "wrong record"
    record = flow.run(FULL)
    assert record["status"] == "BLOCKED"
    assert record.get("ref") == "maintenance.request:42"


# ---------------------------------------------------------------- 9. page
def test_page_route(client):
    index = Path(providers.__file__).parent / "static" / "index.html"
    if not index.exists():
        pytest.skip("workorder/static/index.html does not exist yet (another agent is writing it)")
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
