"""End to end on REPLAY of a SYNTHETIC fixture: motion -> tool session -> human confirmation ->
estimated exposure -> planning recommendation -> voice defect -> work order -> /api/shift.
This proves the wiring, not real-world accuracy."""
import time

from fastapi.testclient import TestClient


def _wait(check, seconds=20.0):
    end = time.time() + seconds
    while time.time() < end:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    return check()


def test_whole_shift_on_replay(tmp_db, monkeypatch):
    from backend import event_bus, inspection_agent
    from backend.main import app
    monkeypatch.setattr(inspection_agent, "extract_fields", lambda text: {
        "conveyor_id": "CV-102", "station_id": "Station 3", "asset_reference": "return idler 14",
        "defect_reported": True, "condition": "bearing seized", "requested_action": "replacement",
        "severity": "high"})
    with TestClient(app) as c:
        # 1. voice report (typed path) -> defect + draft work order
        r = c.post("/api/inspection/report", json={"text": "Station 3, return idler 14, bearing seized. Needs replacement."})
        assert r.status_code == 200, r.text

        # 2. replayed motion -> candidate tool session
        r = c.post("/api/band/replay/start", json={"session_id": "synthetic_tool_active", "speed": 5})
        assert r.status_code == 200, r.text
        _wait(lambda: not c.get("/api/band/status").json().get("replay", {}).get("running", False) or None, 10)
        sessions = _wait(lambda: [s for s in c.get("/api/toolsense/sessions").json() if s.get("end_ts")], 15)
        c.post("/api/band/replay/stop")
        assert sessions, "replayed tool-like motion should end as one candidate session"
        session = sessions[0]
        assert session["confirmation"] == "tentative" and session["source"] in ("replay", "synthetic")
        assert 2.0 <= session["duration_s"] <= 7.0

        # 3. before a tool is confirmed there is no number, only "needs confirmation"
        before = c.get("/api/exposure/current").json()
        assert before["status"] != "ok" or not before["total_points"]

        # 4. a human confirms which tool -> estimated points from the configured tool value
        tools = c.get("/api/exposure/tools").json()
        tools = tools.get("tools", tools) if isinstance(tools, dict) else tools
        tool = next(t for t in tools if t.get("a_hv_ms2"))
        r = c.post(f"/api/toolsense/sessions/{session['session_id']}/confirm",
                   json={"tool_id": tool["tool_id"], "confirmed": True})
        assert r.status_code == 200, r.text
        event_bus.bus.drain()
        after = _wait(lambda: (lambda x: x if x.get("entries") and x["entries"][0].get("points") else None)(
            c.get("/api/exposure/current").json()), 5)
        entry = after["entries"][0]
        expected = 2 * tool["a_hv_ms2"] ** 2 * session["duration_s"] / 3600
        assert abs(entry["points"] - expected) < 0.05

        # 5. the dashboard object ties it together
        shift = c.get("/api/shift").json()
        assert shift["defect"]["assetId"] == "RETURN-IDLER-14"
        assert shift["defect"]["verificationStatus"] == "unverified"
        assert shift["workOrder"]["status"] == "draft"
        assert shift["tool"]["id"] == tool["tool_id"]
        assert next(s for s in shift["conveyor"]["stations"] if s["id"] == "S3")["status"] == "defect"
        kinds = {e["kind"] for e in shift["events"]}
        assert {"inspection", "defect", "workorder", "tool", "exposure"} <= kinds
        assert shift["kpis"]["beltHealth"] is None
