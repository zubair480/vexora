"""Recorder tests: synthetic batches only, no band, recordings written to a temp folder."""
import csv
import json

import pytest

from backend import config, recorder, replay
from backend.models import ImuBatch, ImuSample

HZ = 50.0
T0 = 1_700_000_000.0


def make_samples(count, n0=0, time0=T0, hz=HZ, with_n=True):
    return [ImuSample(n0 + i if with_n else None, 1000.0 + (n0 + i) * 1000.0 / hz, time0 + i / hz,
                      0.01, -0.02, 1.0, 0.1, 0.2, 0.3) for i in range(count)]


def batch(samples, source="synthetic"):
    return ImuBatch(samples=samples, configured_hz=HZ, effective_hz=HZ, source=source)


@pytest.fixture()
def rec(test_bus, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RECORDINGS", tmp_path)
    r = recorder.Recorder()
    yield r
    if r.active:
        r.stop()


def publish(bus, samples, size=10, source="synthetic"):
    for i in range(0, len(samples), size):
        assert bus.publish_imu(batch(samples[i:i + size], source))
    bus.drain()


def test_round_trip(rec, test_bus, tmp_path):
    status = rec.start("stationary", wearer="tester", notes="unit test", band_name="test-band")
    assert status["recording"] is True and rec.active
    sid = status["session_id"]
    assert sid.startswith("stationary-") and recorder.SESSION_ID_RE.match(sid)
    publish(test_bus, make_samples(100))
    assert rec.status()["samples"] == 100
    meta = rec.stop()
    assert not rec.active and rec.status()["recording"] is False

    with open(tmp_path / sid / "samples.csv", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["n", "t", "time", "ax", "ay", "az", "gx", "gy", "gz"]
    assert len(rows) == 101
    assert rows[1][0] == "0" and float(rows[1][2]) == T0 and float(rows[1][5]) == 1.0

    on_disk = json.loads((tmp_path / sid / "meta.json").read_text())
    assert on_disk == meta
    assert list(meta)[:12] == recorder.META_KEYS
    assert set(recorder.META_KEYS) == {"session_id", "label", "tool_id", "band_name", "wearer",
                                       "configured_hz", "effective_hz", "missing_samples",
                                       "started_at", "ended_at", "source", "notes"}
    assert meta["label"] == "stationary" and meta["band_name"] == "test-band"
    assert meta["wearer"] == "tester" and meta["tool_id"] is None and meta["notes"] == "unit test"
    assert meta["source"] == "synthetic" and meta["configured_hz"] == HZ
    assert meta["effective_hz"] == pytest.approx(HZ, rel=1e-3)
    assert meta["missing_samples"] == 0 and meta["missing_samples_known"] is True
    assert meta["samples"] == 100 and meta["data_gaps"] == []
    assert meta["ended_at"] >= meta["started_at"]

    data, loaded = replay.load_recording(sid)
    assert data.shape == (100, 9) and loaded == meta
    assert data[0, 0] == 0 and data[-1, 0] == 99
    assert data[-1, 2] == pytest.approx(T0 + 99 / HZ, abs=1e-6)
    assert data[5, 3:].tolist() == [0.01, -0.02, 1.0, 0.1, 0.2, 0.3]


def test_missing_samples_from_n_jump(rec, test_bus):
    rec.start("walking", session_id="jump")
    # samples 50..54 were lost on the air: n jumps and time moves on by the same amount
    publish(test_bus, make_samples(50) + make_samples(50, n0=55, time0=T0 + 55 / HZ))
    meta = rec.stop()
    assert meta["missing_samples"] == 5 and meta["missing_samples_known"] is True
    assert meta["data_gaps"] == [] and meta["samples"] == 100
    assert meta["effective_hz"] < HZ   # the loss shows up as a lower measured rate


def test_reconnect_is_a_gap_not_missing(rec, test_bus):
    rec.start("tool_active", tool_id="grinder-1", session_id="reconnect")
    first, second = make_samples(100), make_samples(100, n0=0, time0=T0 + 99 / HZ + 3.0)
    publish(test_bus, first + second)
    meta = rec.stop()
    assert meta["missing_samples"] == 0
    assert len(meta["data_gaps"]) == 1
    gap = meta["data_gaps"][0]
    assert gap["start"] == pytest.approx(T0 + 99 / HZ) and gap["end"] - gap["start"] == pytest.approx(3.0)
    assert meta["effective_hz"] == pytest.approx(HZ, rel=1e-3)   # not diluted by the 3 s silence
    assert meta["tool_id"] == "grinder-1" and meta["samples"] == 200


def test_unknown_n_and_mixed_source(rec, test_bus):
    rec.start("gesture", session_id="no-n", configured_hz=100.0)
    publish(test_bus, make_samples(20, with_n=False), source="synthetic")
    publish(test_bus, make_samples(20, time0=T0 + 20 / HZ, with_n=False), source="replay")
    meta = rec.stop()
    assert meta["missing_samples"] == 0 and meta["missing_samples_known"] is False
    assert meta["source"] == "mixed" and meta["configured_hz"] == 100.0   # explicit argument wins
    data, _ = replay.load_recording("no-n")
    assert data.shape == (40, 9) and all(v != v for v in data[:, 0])      # empty n -> NaN


def test_bad_label_and_bad_session_id(rec):
    with pytest.raises(ValueError):
        rec.start("drilling")
    for bad in ("../x", "a/b", "", ".hidden", "x" * 90):
        with pytest.raises(ValueError):
            rec.start("stationary", session_id=bad)
    assert not rec.active


def test_double_start_and_stop_when_idle(rec):
    with pytest.raises(RuntimeError):
        rec.stop()
    rec.start("stationary", session_id="one")
    with pytest.raises(RuntimeError):
        rec.start("stationary", session_id="two")
    meta = rec.stop()
    assert meta["samples"] == 0 and meta["source"] is None and meta["effective_hz"] is None


def test_batches_ignored_when_not_recording(rec, test_bus):
    rec.start("stationary", session_id="first")
    publish(test_bus, make_samples(10))
    assert rec.stop()["samples"] == 10
    publish(test_bus, make_samples(30, n0=10, time0=T0 + 10 / HZ))   # nobody is recording
    assert rec.status() == {"recording": False, "session_id": None, "label": None, "tool_id": None,
                            "wearer": None, "samples": 0, "started_at": None}
    rec.start("stationary", session_id="second")
    publish(test_bus, make_samples(7, n0=40, time0=T0 + 40 / HZ))
    assert rec.stop()["samples"] == 7   # subscribed once: no duplicate rows
    assert replay.load_recording("first")[0].shape == (10, 9)
    assert replay.load_recording("second")[0].shape == (7, 9)


def test_list_recordings_and_fixture_flag(rec, test_bus, tmp_path):
    rec.start("stationary", session_id="older")
    rec.stop()
    rec.start("walking", session_id="newer")
    publish(test_bus, make_samples(5))
    rec.stop()
    fixture = tmp_path / "fixtures" / "fx"
    fixture.mkdir(parents=True)
    (fixture / "meta.json").write_text(json.dumps({"session_id": "fx", "label": "stationary",
                                                    "started_at": 1.0, "source": "synthetic"}))
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "meta.json").write_text("{not json")
    listed = recorder.list_recordings()
    assert [m["session_id"] for m in listed] == ["newer", "older", "fx"]
    assert [m["fixture"] for m in listed] == [False, False, True]


def test_singleton():
    assert recorder.get_recorder() is recorder.get_recorder()
