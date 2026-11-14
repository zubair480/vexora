"""Band router tests. The bridge is always a fake: nothing here connects to a band."""
import math
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import config, recorder, replay, vexo_bridge
from backend.models import ImuBatch, ImuSample
from backend.routers import band

KEYS = {"n", "t", "time", "ax", "ay", "az", "gx", "gy", "gz"}
STATUS = {"connected": False, "reconnecting": False, "band_name": None, "firmware": None,
          "battery": None, "transport": None, "configured_hz": None, "effective_hz": None,
          "missing_samples": 0, "completeness": None, "stale": True, "source": "band",
          "saturated": False, "sensors": None, "gaps": [], "dropped_events": 0, "last_error": None}


class FakeBridge:
    def __init__(self, raises=None):
        self.calls, self.raises, self.connected = [], raises, False

    def status(self):
        live = {"band_name": "fake-band", "configured_hz": 50, "effective_hz": 49.5,
                "transport": "sim", "stale": False} if self.connected else {}
        return {**STATUS, **live, "connected": self.connected}

    def start(self, transport="ble", motion_hz=50, sensors="both"):
        self.calls.append(("start", transport, motion_hz, sensors))
        if self.raises:
            raise self.raises
        self.connected = True
        return self.status()

    def stop(self):
        self.calls.append(("stop",))
        self.connected = False
        return self.status()


def _stop_everything():
    try:
        replay.stop_replay()
    except Exception:
        pass
    try:
        recorder.get_recorder().stop()
    except Exception:
        pass


@pytest.fixture()
def env(test_bus, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.delenv("VEXO_BAND", raising=False)
    monkeypatch.setattr(config, "RECORDINGS", tmp_path)
    bridge = FakeBridge()
    monkeypatch.setattr(vexo_bridge, "get_bridge", lambda: bridge)
    _stop_everything()
    band.recent.clear()
    band._ensure_subscribed()   # the app does this at import; here the bus was just swapped
    app = FastAPI()
    app.include_router(band.router)
    with TestClient(app) as client:
        yield client, bridge, test_bus
    _stop_everything()
    band.recent.clear()


def samples(count, hz=50.0, start_n=0, t0=None, **override):
    t0 = time.time() - count / hz if t0 is None else t0
    out = []
    for i in range(count):
        values = dict(n=start_n + i, t=(start_n + i) * 1000.0 / hz, time=t0 + i / hz,
                      ax=0.01 * i, ay=0.0, az=1.0, gx=0.5, gy=-0.5, gz=0.0)
        values.update(override)
        out.append(ImuSample(**values))
    return out


def publish(bus, items, source="band", size=50):
    for i in range(0, len(items), size):
        assert bus.publish_imu(ImuBatch(items[i:i + size], 50.0, 49.0, source))
    bus.drain()


def record(client, bus, count=100, label="walking"):
    started = client.post("/api/band/record/start", json={"label": label, "wearer": "tester"})
    assert started.status_code == 200, started.text
    publish(bus, samples(count))
    stopped = client.post("/api/band/record/stop")
    assert stopped.status_code == 200, stopped.text
    return stopped.json()


def test_status_with_nothing_connected(env):
    client, _, _ = env
    body = client.get("/api/band/status").json()
    assert body["connected"] is False and body["source"] is None
    assert body["band_number_set"] is False
    assert body["recording"]["recording"] is False
    assert body["replay"]["running"] is False
    assert set(STATUS) <= set(body)


def test_connect_without_band_number_real_bridge(env, monkeypatch):
    client, _, _ = env
    real = vexo_bridge.VexoBridge()
    with pytest.raises(config.BandNumberMissing):
        real.start()
    monkeypatch.setattr(vexo_bridge, "get_bridge", lambda: real)
    response = client.post("/api/band/connect")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "band-number-missing"
    assert real.status()["connected"] is False


def test_connect_without_band_number_fake(env):
    client, bridge, _ = env
    bridge.raises = config.BandNumberMissing("no band number")
    response = client.post("/api/band/connect", json={"transport": "sim"})
    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "band-number-missing", "message": "no band number"}


def test_connect_success_and_disconnect(env):
    client, bridge, _ = env
    response = client.post("/api/band/connect", json={"transport": "sim", "motion_hz": 100, "sensors": "accel"})
    assert response.status_code == 200 and response.json()["connected"] is True
    assert bridge.calls == [("start", "sim", 100, "accel")]
    status = client.get("/api/band/status").json()
    assert status["connected"] is True and status["source"] == "band"
    assert client.post("/api/band/connect").status_code == 200          # body is optional
    assert bridge.calls[-1] == ("start", "ble", 50, "both")
    response = client.post("/api/band/disconnect")
    assert response.status_code == 200 and response.json()["connected"] is False
    assert bridge.calls[-1] == ("stop",)


def test_connect_errors(env):
    client, bridge, _ = env
    error = vexo_bridge.BridgeError("not-found", "band not found")
    bridge.raises = error
    response = client.post("/api/band/connect")
    assert response.status_code == 502
    assert response.json()["detail"] == {"code": "not-found", "message": "band not found"}
    error.code = "already-running"
    assert client.post("/api/band/connect").status_code == 409
    bridge.raises = ValueError("bad rate")
    assert client.post("/api/band/connect").status_code == 422
    assert client.post("/api/band/connect", json={"transport": "wifi"}).status_code == 422


def test_record_roundtrip_and_listing(env, tmp_path):
    client, _, bus = env
    meta = record(client, bus, count=120, label="walking")
    assert meta["label"] == "walking"
    folder = tmp_path / meta["session_id"]
    assert (folder / "meta.json").exists()
    lines = (folder / "samples.csv").read_text().strip().splitlines()
    assert lines[0] == "n,t,time,ax,ay,az,gx,gy,gz" and len(lines) == 121
    assert meta["samples"] == 120
    listed = client.get("/api/band/recordings").json()["recordings"]
    assert [r["session_id"] for r in listed] == [meta["session_id"]]


def test_record_errors(env):
    client, _, _ = env
    assert client.post("/api/band/record/start", json={"label": "juggling"}).status_code == 422
    assert client.post("/api/band/record/start", json={}).status_code == 422
    assert client.post("/api/band/record/stop").status_code == 409
    assert client.post("/api/band/record/start", json={"label": "stationary"}).status_code == 200
    assert client.post("/api/band/record/start", json={"label": "stationary"}).status_code == 409
    assert client.post("/api/band/record/stop").status_code == 200


def test_replay_feeds_recent_as_replay(env):
    client, _, bus = env
    meta = record(client, bus, count=100)
    band.recent.clear()
    response = client.post("/api/band/replay/start", json={"session_id": meta["session_id"], "speed": 50})
    assert response.status_code == 200, response.text
    body = {"samples": []}
    deadline = time.time() + 5
    while time.time() < deadline and not body["samples"]:
        time.sleep(0.05)
        bus.drain()
        body = client.get("/api/band/imu/recent?seconds=60").json()
    assert body["samples"] and body["source"] == "replay"
    assert all(set(s) == KEYS for s in body["samples"])
    times = [s["time"] for s in body["samples"]]
    assert times == sorted(times)
    status = client.get("/api/band/status").json()
    assert status["connected"] is False                       # replay is never a live band
    if status["replay"]["running"]:
        assert status["source"] == "replay"
        assert client.post("/api/band/connect").json()["detail"]["code"] == "replay-running"
    assert client.post("/api/band/replay/stop").status_code == 200
    assert client.get("/api/band/status").json()["source"] is None


def test_connect_refused_while_replay_runs(env, monkeypatch):
    client, bridge, _ = env
    monkeypatch.setattr(replay, "replay_status", lambda: {"running": True, "source": "replay"})
    response = client.post("/api/band/connect")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "replay-running"
    assert bridge.calls == []
    status = client.get("/api/band/status").json()
    assert status["source"] == "replay" and status["connected"] is False


def test_replay_errors(env):
    client, bridge, _ = env
    assert client.post("/api/band/replay/start", json={"session_id": "no-such-session"}).status_code == 404
    bridge.connected = True
    response = client.post("/api/band/replay/start", json={"session_id": "no-such-session"})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "band-connected"


def test_recent_downsamples_by_stride(env):
    client, _, bus = env
    items = samples(1000, hz=100.0)
    publish(bus, items, size=100)
    body = client.get("/api/band/imu/recent?seconds=60&max_points=100").json()
    got = body["samples"]
    assert 0 < len(got) <= 101 and body["downsample"] == 10
    times = [s["time"] for s in got]
    assert all(a < b for a, b in zip(times, times[1:]))
    assert got[-1]["n"] == items[-1].n                        # newest is always kept
    real = {s.n: s for s in items}
    assert all(real[s["n"]].ax == s["ax"] for s in got)       # real samples only, nothing invented
    assert body["source"] == "band" and body["stale"] is False
    assert body["configured_hz"] == 50.0 and body["effective_hz"] == 49.0
    assert body["units"]["accel"] == "g" and "milliseconds" in body["units"]["t"]
    window = client.get("/api/band/imu/recent?seconds=2&max_points=2000").json()["samples"]
    assert 195 <= len(window) <= 202 and window[-1]["n"] == items[-1].n
    clamped = client.get("/api/band/imu/recent?seconds=9999&max_points=1").json()
    assert len(clamped["samples"]) == 10 and clamped["downsample"] == 100


def test_recent_nan_becomes_null(env):
    client, _, bus = env
    publish(bus, samples(5, n=None, gx=math.nan, gy=math.nan, gz=math.nan), source="synthetic")
    body = client.get("/api/band/imu/recent").json()
    assert body["source"] == "synthetic" and len(body["samples"]) == 5
    assert all(s["gx"] is None and s["gz"] is None and s["n"] is None and s["az"] == 1.0
               for s in body["samples"])


def test_recent_empty_and_stale(env, monkeypatch):
    client, _, bus = env
    body = client.get("/api/band/imu/recent").json()
    assert body["samples"] == [] and body["stale"] is True and body["source"] is None
    publish(bus, samples(10))
    assert client.get("/api/band/imu/recent").json()["stale"] is False
    now = time.time()
    monkeypatch.setattr(band.time, "time", lambda: now + 5)
    assert client.get("/api/band/imu/recent").json()["stale"] is True


def test_recent_never_mixes_sources_under_one_label(env):
    client, _, bus = env
    publish(bus, samples(100), source="replay")
    publish(bus, samples(5), source="band")
    body = client.get("/api/band/imu/recent?seconds=60").json()
    assert body["source"] == "band" and len(body["samples"]) == 5    # the replayed ones are gone


def test_replay_refused_while_band_is_reconnecting(env, monkeypatch):
    client, bridge, _ = env
    monkeypatch.setattr(bridge, "status", lambda: {**STATUS, "connected": False, "reconnecting": True})
    response = client.post("/api/band/replay/start", json={"session_id": "anything"})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "band-connected"
