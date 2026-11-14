from fastapi.testclient import TestClient


def test_shift_is_honest_when_empty(tmp_db):
    from backend.main import app
    with TestClient(app) as client:
        data = client.get("/api/shift").json()
    assert data["kpis"]["beltHealth"] is None            # no source, so no number
    assert data["defect"] is None and data["workOrder"] is None
    assert data["band"]["connected"] is False and data["band"]["live"] is False
    assert data["exposure"]["action"] == 100 and data["exposure"]["limit"] == 400


def test_voice_report_reaches_the_dashboard_shape(test_bus, monkeypatch):
    from backend import inspection_agent, shift_view
    monkeypatch.setattr(inspection_agent, "extract_fields", lambda text: {
        "conveyor_id": "CV-102", "station_id": "Station 3", "asset_reference": "return idler 14",
        "condition": "bearing seized", "requested_action": "replacement", "severity": "high",
        "defect_reported": True})
    out = inspection_agent.process_transcript(
        "Station 3, return idler 14, bearing seized. Needs replacement.", "shift-demo", "worker-1")
    test_bus.drain()
    data = shift_view.build("shift-demo")
    if out.get("status") == "recorded":
        assert data["defect"]["assetId"] == "RETURN-IDLER-14"
        assert data["defect"]["verificationStatus"] == "unverified"
        assert data["workOrder"]["status"] == "draft"
        s3 = next(s for s in data["conveyor"]["stations"] if s["id"] == "S3")
        assert s3["status"] == "defect"
        assert {e["kind"] for e in data["events"]} >= {"inspection", "defect", "workorder"}
    else:
        raise AssertionError(out)
