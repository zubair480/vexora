"""inspection_agent + router with the kit's `ai.extract` faked. No band, no network."""
import os

import pytest
from fastapi.testclient import TestClient

from backend import assets, config, database, inspection_agent, work_orders

EXAMPLE = "Station 3, return idler 14, bearing seized. Needs replacement."
SEIZED = {"conveyor_id": None, "station_id": "station 3", "asset_reference": "return idler 14",
          "defect_reported": True, "condition": "bearing seized", "requested_action": "replace",
          "severity": "high"}


class FakeAi:
    """Stands in for the kit's `ai` module: records calls, returns a canned answer or raises."""

    class AiError(Exception):
        def __init__(self, message):
            super().__init__(message)
            self.code, self.message = "ai", message

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.calls = answer, error, []

    def extract(self, text, schema, instructions="Fill in the fields from the text."):
        self.calls.append((text, schema, instructions))
        if self.error:
            raise self.error
        return dict(self.answer)


@pytest.fixture()
def fake_ai(monkeypatch):
    def install(answer=None, error=None):
        fake = FakeAi(answer, error)
        monkeypatch.setattr(inspection_agent, "_kit_ai", lambda: fake)
        return fake
    monkeypatch.setattr(config, "load_env", lambda: None)
    return install


def events(bus, shift="shift-t"):
    bus.drain()
    return database.list_events(shift)


def test_example_sentence_creates_inspection_defect_and_draft(test_bus, fake_ai):
    fake = fake_ai(SEIZED)
    result = inspection_agent.process_transcript(EXAMPLE, "shift-t", "worker-t")

    assert fake.calls[0][0] == EXAMPLE
    assert fake.calls[0][1] is inspection_agent.SCHEMA        # strict schema went to the kit helper
    assert result["status"] == "recorded"
    ins, defect, order = result["inspection"], result["defect"], result["work_order"]
    assert (ins["conveyor_id"], ins["station_id"], ins["asset_id"]) == ("CV-102", "S3", "RETURN-IDLER-14")
    assert ins["transcript"] == EXAMPLE and ins["source"] == "ai"
    assert ins["condition"] == "reported bearing seized"       # recorded as reported, not as a finding
    assert ins["verification_status"] == "unverified" and ins["needs_clarification"] is False
    assert defect["verification_status"] == "unverified" and defect["asset_id"] == "RETURN-IDLER-14"
    assert order["status"] == "draft" and order["defect_id"] == defect["defect_id"]
    assert "verified" not in result["note"].replace("not verified", "")

    stored = events(test_bus)
    assert [e["type"] for e in stored] == ["inspection_recorded", "defect_reported", "work_order_created"]
    for event in stored:
        assert event["source"] == "ai"
        assert (event["station_id"], event["asset_id"]) == ("S3", "RETURN-IDLER-14")
    recorded, reported, created = (e["payload"] for e in stored)
    for name in ("inspection_id", "transcript", "conveyor_id", "station_id", "asset_id", "condition",
                 "requested_action", "verification_status", "needs_clarification", "clarification_question"):
        assert name in recorded
    assert recorded["verification_status"] == "unverified" and recorded["needs_clarification"] is False
    assert reported["inspection_id"] == recorded["inspection_id"] == ins["inspection_id"]
    assert set(reported) >= {"defect_id", "inspection_id", "asset_id", "station_id", "condition",
                             "severity", "verification_status"}
    assert reported["verification_status"] == "unverified"
    assert created["defect_id"] == reported["defect_id"] and created["status"] == "draft"
    assert set(created) >= {"work_order_id", "defect_id", "asset_id", "task", "priority", "status"}


def test_station_hint_resolves_a_bare_idler_number(test_bus, fake_ai):
    fake_ai(dict(SEIZED, station_id=None, asset_reference="idler fourteen"))
    result = inspection_agent.process_transcript("idler fourteen bearing seized", "shift-t", "w",
                                                 station_hint="station three")
    assert result["status"] == "recorded"
    assert result["inspection"]["asset_id"] == "RETURN-IDLER-14"


def test_ambiguous_asset_asks_and_creates_no_work_order(test_bus, fake_ai):
    fake_ai(dict(SEIZED, station_id=None, asset_reference="idler 14"))
    result = inspection_agent.process_transcript("idler 14 bearing seized", "shift-t", "w")

    assert result["status"] == "needs_clarification"
    assert result["defect"] is None and result["work_order"] is None
    ins = result["inspection"]
    assert ins["asset_id"] is None and ins["needs_clarification"] is True
    assert "CARRY-IDLER-14" in ins["clarification_question"] and "RETURN-IDLER-14" in ins["clarification_question"]
    assert {c["asset_id"] for c in ins["candidates"]} == {"CARRY-IDLER-14", "RETURN-IDLER-14"}
    stored = events(test_bus)
    assert [e["type"] for e in stored] == ["inspection_recorded"]
    assert stored[0]["payload"]["needs_clarification"] is True and stored[0]["asset_id"] is None
    assert inspection_agent.list_defects() == [] and work_orders.list_work_orders() == []

    # the worker answers with the station: now it completes
    done = inspection_agent.clarify(ins["inspection_id"], "station 3")
    assert done["status"] == "recorded" and done["inspection"]["asset_id"] == "RETURN-IDLER-14"
    assert done["inspection"]["asset_resolution"] == "worker_clarified"
    assert done["defect"]["verification_status"] == "unverified" and done["work_order"]["status"] == "draft"
    types = [e["type"] for e in events(test_bus)]
    assert types == ["inspection_recorded", "inspection_recorded", "defect_reported", "work_order_created"]
    # answering again changes nothing and creates no second work order
    again = inspection_agent.clarify(ins["inspection_id"], "CARRY-IDLER-14")
    assert again["inspection"]["asset_id"] == "RETURN-IDLER-14"
    assert len(work_orders.list_work_orders()) == 1 and len(events(test_bus)) == 4


def test_unknown_asset_is_never_invented(test_bus, fake_ai):
    fake_ai(dict(SEIZED, asset_reference="return idler 99"))
    result = inspection_agent.process_transcript("Station 3, return idler 99, bearing seized", "shift-t", "w")
    assert result["status"] == "needs_clarification"
    assert result["inspection"]["asset_id"] is None and result["work_order"] is None
    assert "return idler 99" in result["clarification_question"]
    assert [e["type"] for e in events(test_bus)] == ["inspection_recorded"]
    assert work_orders.list_work_orders() == []

    still = inspection_agent.clarify(result["inspection"]["inspection_id"], "the big roller thing")
    assert still["status"] == "needs_clarification" and work_orders.list_work_orders() == []
    assert len(events(test_bus)) == 1
    done = inspection_agent.clarify(result["inspection"]["inspection_id"], "RETURN-IDLER-13")
    assert done["status"] == "recorded" and done["work_order"]["asset_id"] == "RETURN-IDLER-13"


def test_an_id_the_model_made_up_is_checked_against_the_registry(test_bus, fake_ai):
    fake_ai(dict(SEIZED, asset_reference="RETURN-IDLER-77", conveyor_id="CV-102"))
    result = inspection_agent.process_transcript(EXAMPLE, "shift-t", "w")
    assert result["status"] == "needs_clarification" and result["inspection"]["asset_id"] is None
    assert assets.get("RETURN-IDLER-77") is None


def test_no_fault_reported_records_inspection_only(test_bus, fake_ai):
    fake_ai(dict(SEIZED, defect_reported=False, condition=None, requested_action=None, severity="unknown"))
    result = inspection_agent.process_transcript("Station 3, return idler 14, running fine", "shift-t", "w")
    assert result["status"] == "recorded" and result["defect"] is None and result["work_order"] is None
    assert [e["type"] for e in events(test_bus)] == ["inspection_recorded"]


@pytest.mark.parametrize("error", [
    FakeAi.AiError("This needs an OpenAI key: see \"Audio to text to AI\" in AGENTS.md."),
    FakeAi.AiError("openai: Could not reach api.openai.com."),
    OSError("network down"),
])
def test_ai_failure_is_an_honest_error_not_a_guess(test_bus, fake_ai, error):
    fake_ai(error=error)
    result = inspection_agent.process_transcript(EXAMPLE, "shift-t", "w")
    assert result["status"] == "error" and result["error"] == "ai_unavailable"
    assert result["inspection"] is None and result["defect"] is None and result["work_order"] is None
    assert result["transcript"] == EXAMPLE
    assert events(test_bus) == []
    assert inspection_agent.list_inspections() == [] and work_orders.list_work_orders() == []


def test_malformed_ai_answer_is_an_error(test_bus, fake_ai):
    fake_ai({"asset_reference": "return idler 14"})
    result = inspection_agent.process_transcript(EXAMPLE, "shift-t", "w")
    assert result["status"] == "error" and events(test_bus) == []


def test_empty_text_never_reaches_the_ai(test_bus, fake_ai):
    fake = fake_ai(SEIZED)
    assert inspection_agent.process_transcript("   ", "shift-t", "w")["error"] == "empty_transcript"
    assert fake.calls == []


def test_real_kit_without_a_key_fails_honestly(test_bus, monkeypatch, tmp_path):
    """The unmodified kit `ai` helper, no key anywhere: an AiError before any network call."""
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)                    # the kit also looks for .env in the cwd
    result = inspection_agent.process_transcript(EXAMPLE, "shift-t", "w")
    assert result["status"] == "error" and result["error"] == "ai_unavailable"
    assert "OpenAI key" in result["message"]
    assert events(test_bus) == []


def test_records_survive_a_new_connection_and_review_is_human_only(test_bus, fake_ai):
    fake_ai(SEIZED)
    result = inspection_agent.process_transcript(EXAMPLE, "shift-t", "worker-t")
    ids = result["inspection"]["inspection_id"], result["defect"]["defect_id"], result["work_order"]["work_order_id"]
    test_bus.drain()

    database.reset_for_tests()                     # as after a restart: fresh connections
    assert inspection_agent.get_inspection(ids[0])["asset_id"] == "RETURN-IDLER-14"
    assert inspection_agent.get_defect(ids[1])["verification_status"] == "unverified"
    assert work_orders.get(ids[2])["status"] == "draft"
    assert assets.get("RETURN-IDLER-14")["station_id"] == "S3"

    with pytest.raises(work_orders.InvalidTransition):
        inspection_agent.review_work_order(ids[2], "approve", "Sam Supervisor")   # must be submitted first
    with pytest.raises(work_orders.InvalidTransition):
        inspection_agent.review_work_order(ids[2], "submit", "  ")                 # needs a named human
    submitted = inspection_agent.review_work_order(ids[2], "submit", "worker-t")
    assert submitted["work_order"]["status"] == "submitted"
    assert submitted["defect"]["verification_status"] == "unverified"             # still not verified
    approved = inspection_agent.review_work_order(ids[2], "approve", "Sam Supervisor")
    assert approved["work_order"]["status"] == "approved"
    assert approved["defect"]["verification_status"] == "verified"
    assert approved["defect"]["reviewed_by"] == "Sam Supervisor"


def test_router_end_to_end(test_bus, fake_ai, monkeypatch):
    from backend.main import app
    from backend.routers import inspection as router_module
    monkeypatch.setenv("BELTGUARD_SHIFT", "shift-t")
    monkeypatch.setattr(router_module, "bridge_band", lambda: None)
    fake_ai(SEIZED)
    client = TestClient(app)                       # no lifespan: the test bus is the bus

    listed = client.get("/api/inspection/assets").json()
    assert len(listed["stations"]) == 5
    assert any(a["asset_id"] == "RETURN-IDLER-14" and a["station_id"] == "S3" for a in listed["assets"])

    made = client.post("/api/inspection/report", json={"text": EXAMPLE})
    assert made.status_code == 200 and made.json()["status"] == "recorded"
    order_id = made.json()["work_order"]["work_order_id"]
    assert len(client.get("/api/inspection/inspections").json()) == 1
    assert client.get("/api/inspection/defects").json()[0]["verification_status"] == "unverified"
    assert client.get("/api/inspection/work-orders").json()[0]["status"] == "draft"

    url = "/api/inspection/work-orders/%s/review" % order_id
    assert client.post(url, json={"action": "approve", "reviewer": "Sam"}).status_code == 409
    assert client.post(url, json={"action": "submit", "reviewer": "Sam"}).json()["work_order"]["status"] == "submitted"
    assert client.post(url, json={"action": "reject", "reviewer": "Sam"}).json()["defect"]["verification_status"] == "rejected"
    assert client.post("/api/inspection/work-orders/WO-NOPE/review",
                       json={"action": "submit", "reviewer": "Sam"}).status_code == 404

    fake_ai(dict(SEIZED, station_id=None, asset_reference="idler 14"))
    unclear = client.post("/api/inspection/report", json={"text": "idler 14 seized"}).json()
    assert unclear["status"] == "needs_clarification"
    fixed = client.post("/api/inspection/%s/clarify" % unclear["inspection"]["inspection_id"],
                        json={"asset_id": "CARRY-IDLER-14"}).json()
    assert fixed["inspection"]["asset_id"] == "CARRY-IDLER-14" and fixed["work_order"]["status"] == "draft"
    assert client.post("/api/inspection/INS-NOPE/clarify", json={"text": "x"}).status_code == 404

    no_band = client.post("/api/inspection/listen", json={"seconds": 5})
    assert no_band.status_code == 409 and no_band.json()["detail"]["error"] == "no-band"

    fake_ai(error=FakeAi.AiError("openai: Could not reach api.openai.com."))
    failed = client.post("/api/inspection/report", json={"text": EXAMPLE})
    assert failed.status_code == 503 and failed.json()["status"] == "error"
    assert client.post("/api/inspection/report", json={"text": "  "}).status_code == 422


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="opt-in: set OPENAI_API_KEY for the live check")
def test_live_ai_extract_on_the_example_sentence(test_bus):
    """The real kit `ai.extract` (network, real key) on typed text. Still no band."""
    result = inspection_agent.process_transcript(EXAMPLE, "shift-t", "worker-t")
    assert result["status"] == "recorded", result
    ins = result["inspection"]
    assert (ins["conveyor_id"], ins["station_id"], ins["asset_id"]) == ("CV-102", "S3", "RETURN-IDLER-14")
    assert "seized" in ins["condition"] and ins["condition"].startswith("reported")
    assert result["defect"]["verification_status"] == "unverified"
    assert result["work_order"]["status"] == "draft"


def _ambiguous(fake_ai, **overrides):
    fake_ai(dict(SEIZED, station_id=None, asset_reference="idler 14", **overrides))
    return inspection_agent.process_transcript("idler 14 bearing seized", "shift-t", "w")["inspection"]["inspection_id"]


def test_clarify_words_only_choose_among_the_offered_candidates(test_bus, fake_ai):
    first = _ambiguous(fake_ai)
    # "one" must not become asset number 1, and "motor" was never offered
    assert inspection_agent.clarify(first, "motor")["status"] == "needs_clarification"
    done = inspection_agent.clarify(first, "the return one")
    assert done["inspection"]["asset_id"] == "RETURN-IDLER-14"
    second = _ambiguous(fake_ai)
    assert inspection_agent.clarify(second, "carry")["inspection"]["asset_id"] == "CARRY-IDLER-14"
    assert {w["asset_id"] for w in work_orders.list_work_orders()} == {"RETURN-IDLER-14", "CARRY-IDLER-14"}


def test_conflicting_station_asks_for_confirmation_and_accepts_yes(test_bus, fake_ai):
    fake_ai(dict(SEIZED, station_id="station 2"))
    result = inspection_agent.process_transcript("Station 2, return idler 14, bearing seized", "shift-t", "w")
    assert result["status"] == "needs_clarification" and result["work_order"] is None
    assert "RETURN-IDLER-14" in result["clarification_question"]
    done = inspection_agent.clarify(result["inspection"]["inspection_id"], "Yes.")
    assert done["status"] == "recorded" and done["inspection"]["station_id"] == "S3"


def test_other_conveyor_is_not_described_as_cv102(test_bus, fake_ai):
    fake_ai(dict(SEIZED, conveyor_id="CV-999"))
    result = inspection_agent.process_transcript("CV-999 station 3 return idler 14 seized", "shift-t", "w")
    assert result["status"] == "needs_clarification" and result["work_order"] is None
    assert "CV-999" in result["clarification_question"]


def test_fault_without_a_stated_condition_still_raises_an_unverified_defect(test_bus, fake_ai):
    fake_ai(dict(SEIZED, condition=None))
    result = inspection_agent.process_transcript("Station 3, return idler 14 needs replacing", "shift-t", "w")
    assert result["defect"]["condition"] == "reported fault, condition not stated"
    assert result["defect"]["verification_status"] == "unverified" and result["work_order"]["status"] == "draft"


def test_racing_clarifications_create_one_defect_and_one_work_order(test_bus, fake_ai):
    import threading
    for _ in range(5):
        inspection_id = _ambiguous(fake_ai)
        threads = [threading.Thread(target=inspection_agent.clarify, args=(inspection_id, answer))
                   for answer in ("CARRY-IDLER-14", "RETURN-IDLER-14", "station 3")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        defects = [d for d in inspection_agent.list_defects() if d["inspection_id"] == inspection_id]
        orders = [w for w in work_orders.list_work_orders() if w["inspection_id"] == inspection_id]
        assert len(defects) == 1 and len(orders) == 1
        assert defects[0]["asset_id"] == orders[0]["asset_id"] == inspection_agent.get_inspection(inspection_id)["asset_id"]
