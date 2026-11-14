"""Replay tests: committed synthetic fixtures and temp recordings only, never a band."""
import json
import time

import numpy as np
import pytest

from backend import config, recorder, replay

STATIONARY = "synthetic_stationary"
TOOL_ACTIVE = "synthetic_tool_active"


@pytest.fixture()
def captured(test_bus):
    batches, events = [], []
    test_bus.subscribe_imu(batches.append)
    test_bus.subscribe(events.append)
    return batches, events


def all_samples(batches):
    return [s for b in batches for s in b.samples]


def write_recording(folder, times, meta=None):
    folder.mkdir(parents=True)
    lines = ["n,t,time,ax,ay,az,gx,gy,gz"]
    lines += [f"{i},{1000 + i * 20},{t!r},0.0,0.0,1.0,0.0,0.0,0.0" for i, t in enumerate(times)]
    (folder / "samples.csv").write_text("\n".join(lines) + "\n")
    if meta is not None:
        (folder / "meta.json").write_text(json.dumps(meta))


def test_load_committed_fixtures():
    for sid, label, tool in ((STATIONARY, "stationary", None), (TOOL_ACTIVE, "tool_active", "synthetic-tool")):
        data, meta = replay.load_recording(sid)
        assert data.shape == (300, 9) and data.dtype == np.float64
        assert meta["label"] == label and meta["tool_id"] == tool
        assert meta["source"] == "synthetic" and meta["band_name"] is None and meta["wearer"] is None
        assert "synthetic" in meta["notes"].lower()
        assert list(meta)[:12] == recorder.META_KEYS
        assert data[:, 0].tolist() == list(range(300))
        assert np.allclose(np.diff(data[:, 2]), 0.02, atol=1e-6)
        assert abs(data[:, 5].mean() - 1.0) < 0.05
    still, active = replay.load_recording(STATIONARY)[0], replay.load_recording(TOOL_ACTIVE)[0]
    assert still[:, 3:6].std(axis=0).max() < 0.02 and active[:, 5].std() > 0.1
    assert replay.recording_dir(STATIONARY) == config.RECORDINGS / "fixtures" / STATIONARY
    assert any(m["session_id"] == STATIONARY and m["fixture"] for m in recorder.list_recordings())


def test_run_blocking_publishes_everything_as_replay(captured, test_bus):
    batches, events = captured
    source = replay.ReplaySource(TOOL_ACTIVE)
    before = time.time()
    assert source.run_blocking(realtime=False) == 300
    test_bus.drain()
    samples = all_samples(batches)
    assert len(samples) == 300 and len(batches) == pytest.approx(60, abs=2)
    assert all(b.source == "replay" for b in batches)
    assert all(b.configured_hz == 50.0 and b.effective_hz == 50.0 for b in batches)
    assert [s.n for s in samples] == list(range(300))

    assert events and all(e.source == "replay" for e in events)
    kinds = [e.type for e in events]
    assert kinds[0] == "device_connected" and kinds[-1] == "device_disconnected"
    assert kinds.count("device_connected") == 1 and kinds.count("device_disconnected") == 1
    assert 5 <= kinds.count("sensor_quality") <= 7
    connected, disconnected = events[0], events[-1]
    assert connected.payload == {"band_name": None, "firmware": None, "battery": None,
                                 "transport": "replay", "reason": "replay-started",
                                 "session_id": TOOL_ACTIVE, "label": "tool_active",
                                 "recorded_source": "synthetic", "speed": 1.0}
    assert disconnected.payload["reason"] == "replay-finished"
    assert connected.shift_id == config.current_shift()["shift_id"]
    quality = next(e for e in events if e.type == "sensor_quality").payload
    assert quality == {"configured_hz": 50.0, "effective_hz": 50.0, "missing_samples": 0,
                       "completeness": 1.0, "saturated": False, "stale": False}

    status = source.status()
    assert status["running"] is False and status["samples_sent"] == status["samples_total"] == 300
    assert status["progress"] == 1.0 and status["source"] == "replay"
    assert status["recorded_source"] == "synthetic" and status["label"] == "tool_active"
    assert before <= samples[0].time <= time.time()


def test_time_rebasing_preserves_spacing(captured, test_bus):
    batches, _ = captured
    data, _ = replay.load_recording(STATIONARY)
    replay.ReplaySource(STATIONARY, speed=5.0).run_blocking(realtime=False)
    test_bus.drain()
    samples = all_samples(batches)
    times = np.array([s.time for s in samples])
    assert abs(times[0] - time.time()) < 5.0            # rebased to now, not the recorded epoch
    assert np.allclose(times - times[0], data[:, 2] - data[0, 2], atol=1e-5)   # speed never rescales it
    assert [s.t for s in samples] == data[:, 1].tolist()   # band clock kept as recorded
    assert [s.az for s in samples] == data[:, 5].tolist()


def test_recorded_gap_is_replayed_as_a_gap(captured, test_bus, tmp_path, monkeypatch):
    batches, events = captured
    monkeypatch.setattr(config, "RECORDINGS", tmp_path)
    times = [1000.0 + i * 0.02 for i in range(50)] + [1000.0 + 49 * 0.02 + 3.0 + i * 0.02 for i in range(50)]
    write_recording(tmp_path / "gappy", times, {"session_id": "gappy", "label": "walking", "source": "band",
                                                "band_name": "test-band", "configured_hz": 50.0,
                                                "effective_hz": 50.0, "missing_samples": 10, "samples": 90})
    source = replay.ReplaySource("gappy")
    assert source.run_blocking(realtime=False) == 100
    test_bus.drain()
    samples = all_samples(batches)
    steps = np.diff([s.time for s in samples])
    assert len(samples) == 100                           # nothing invented to fill the gap
    assert steps.max() == pytest.approx(3.0, abs=1e-5) and int(np.argmax(steps)) == 49
    assert np.allclose(np.delete(steps, 49), 0.02, atol=1e-5)
    # a recording made from a real band is still labelled replay when played back
    assert all(b.source == "replay" for b in batches) and all(e.source == "replay" for e in events)
    assert events[0].payload["recorded_source"] == "band" and events[0].payload["band_name"] == "test-band"
    quality = next(e for e in events if e.type == "sensor_quality").payload
    assert quality["completeness"] == pytest.approx(0.9) and quality["missing_samples"] == 10


def test_missing_meta_and_missing_n(captured, test_bus, tmp_path, monkeypatch):
    batches, _ = captured
    monkeypatch.setattr(config, "RECORDINGS", tmp_path)
    folder = tmp_path / "bare"
    folder.mkdir()
    (folder / "samples.csv").write_text("n,t,time,ax,ay,az,gx,gy,gz\n,5,10.0,0,0,1,0,0,nan\n,25,10.02,0,0,1,0,0,0\n")
    data, meta = replay.load_recording("bare")
    assert meta == {"session_id": "bare"} and data.shape == (2, 9)
    assert np.isnan(data[0, 0]) and np.isnan(data[0, 8])
    assert replay.ReplaySource("bare").run_blocking(realtime=False) == 2
    test_bus.drain()
    assert [s.n for s in all_samples(batches)] == [None, None]


def test_realtime_pacing(captured, test_bus):
    batches, _ = captured
    source = replay.ReplaySource(STATIONARY, speed=20.0)
    started = time.monotonic()
    assert source.run_blocking(realtime=True) == 300
    elapsed = time.monotonic() - started
    assert 0.2 < elapsed < 2.0      # 6 s of data at 20x is about 0.3 s
    test_bus.drain()
    assert len(all_samples(batches)) == 300


def test_start_and_stop_replay_thread(captured, test_bus):
    batches, events = captured
    assert replay.stop_replay()["source"] == "replay"
    status = replay.start_replay(STATIONARY, speed=1.0)
    assert status["running"] is True and status["source"] == "replay" and status["samples_total"] == 300
    time.sleep(0.4)
    assert replay.replay_status()["running"] is True
    stopped = replay.stop_replay()
    assert stopped["running"] is False and 0 < stopped["samples_sent"] < 300
    assert 0 < stopped["progress"] < 1
    test_bus.drain()
    assert len(all_samples(batches)) == stopped["samples_sent"]
    assert events[-1].type == "device_disconnected" and events[-1].payload["reason"] == "replay-stopped"
    assert replay.replay_status()["running"] is False

    # starting a new replay stops the current one first
    replay.start_replay(STATIONARY, speed=1.0)
    first = replay._current
    replay.start_replay(TOOL_ACTIVE, speed=50.0)
    assert first.running is False and replay.replay_status()["session_id"] == TOOL_ACTIVE
    deadline = time.time() + 3
    while replay.replay_status()["running"] and time.time() < deadline:
        time.sleep(0.02)
    assert replay.replay_status()["progress"] == 1.0
    replay.stop_replay()
    test_bus.drain()
    assert all(b.source == "replay" for b in batches) and all(e.source == "replay" for e in events)


def test_unknown_session_and_bad_ids():
    with pytest.raises(FileNotFoundError):
        replay.load_recording("no-such-recording")
    with pytest.raises(FileNotFoundError):
        replay.ReplaySource("no-such-recording")
    with pytest.raises(FileNotFoundError):
        replay.recording_dir("fixtures")
    for bad in ("../x", "fixtures/../../x", "", "a b", "..", "/abs"):
        with pytest.raises(ValueError):
            replay.recording_dir(bad)
        with pytest.raises(ValueError):
            replay.start_replay(bad)


def test_bad_speed():
    for speed in (0, -1, 100.5):
        with pytest.raises(ValueError):
            replay.ReplaySource(STATIONARY, speed=speed)
