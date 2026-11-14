import time

from fastapi.testclient import TestClient


def _samples(start_n, count, hz=50.0, t0=None):
    t0 = t0 or time.time() * 1000
    return [{"n": start_n + i, "t": i * 1000 / hz, "time": t0 + i * 1000 / hz,
             "ax": 0.0, "ay": 0.0, "az": 1.0, "gx": 0.0, "gy": 0.0, "gz": 0.0} for i in range(count)]


def test_browser_band_feeds_the_pipeline(tmp_db, monkeypatch):
    from backend import browser_link, config
    from backend.main import app
    monkeypatch.setattr(config, "band_number", lambda: 123)
    with TestClient(app) as c:
        assert c.post("/api/band/browser/samples", json={"samples": _samples(0, 5)}).status_code == 409
        wrong = c.post("/api/band/browser/hello", json={"band_name": "x", "band_number": 5})
        assert wrong.status_code == 403                       # only the team's own band
        ok = c.post("/api/band/browser/hello", json={"band_name": "Vexo-HACK-123", "band_number": 123,
                                                    "firmware": "1.1", "motion_hz": 50})
        assert ok.status_code == 200 and ok.json()["connected"] is True
        t0 = time.time() * 1000
        c.post("/api/band/browser/samples", json={"samples": _samples(0, 50, t0=t0)})
        r = c.post("/api/band/browser/samples", json={"samples": _samples(60, 50, t0=t0 + 1200)}).json()
        assert r["missing_samples"] == 10                      # counter jumped 49 -> 60
        status = c.get("/api/band/status").json()
        assert status["connected"] and status["transport"] == "browser-ble" and status["missing_samples"] == 10
        recent = c.get("/api/band/imu/recent", params={"seconds": 5}).json()
        assert recent["source"] == "band" and len(recent["samples"]) > 0
        assert abs(recent["samples"][-1]["time"] - (t0 + 1200 + 49 * 20) / 1000) < 0.01   # seconds
        assert c.get("/api/shift").json()["band"]["connected"] is True
        c.post("/api/band/browser/bye")
        assert c.get("/api/band/status").json()["connected"] is False
    browser_link.goodbye()
