"""Acoustic anomaly screening: synthetic signals only (no real conveyor audio exists)."""
import json

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import acoustic_anomaly as aa
from backend import config
from backend.routers import acoustic as acoustic_router

SR = 16000
FORBIDDEN = ("bearing", "seized", "fault confirmed", "fault", "defect confirmed", "live vexo")


def hum(seed: int, seconds: float = 2.0, sr: int = SR) -> np.ndarray:
    """Steady motor-like hum: 120 Hz with harmonics plus a little broadband noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * sr)) / sr
    amp = 1.0 + rng.normal(0, 0.03)
    y = amp * (0.20 * np.sin(2 * np.pi * 120 * t + rng.uniform(0, 6.28))
               + 0.08 * np.sin(2 * np.pi * 240 * t + rng.uniform(0, 6.28))
               + 0.04 * np.sin(2 * np.pi * 360 * t + rng.uniform(0, 6.28)))
    return (y + rng.normal(0, 0.01, t.size)).astype(np.float32)


def altered(seed: int, seconds: float = 2.0, sr: int = SR) -> np.ndarray:
    """The same hum with an added 3 kHz tone and periodic impulses."""
    y = hum(seed, seconds, sr).copy()
    t = np.arange(y.size) / sr
    y += 0.10 * np.sin(2 * np.pi * 3000 * t).astype(np.float32)
    y[:: sr // 8] += 0.6
    return y


@pytest.fixture()
def store(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RECORDINGS", tmp_path / "recordings")
    return tmp_path


def add_refs(station: str, n: int, source: str = "synthetic") -> None:
    for i in range(n):
        aa.add_reference(station, hum(i), SR, source=source)


def assert_wording(obj) -> None:
    text = json.dumps(obj).lower()
    for word in FORBIDDEN:
        assert word not in text, word


def test_feature_shapes_and_finite():
    f = aa.extract_features(hum(0), SR)
    assert len(f["mfcc_mean"]) == aa.N_MFCC and len(f["mfcc_std"]) == aa.N_MFCC
    assert len(f["logmel_mean"]) == aa.N_MELS and len(f["logmel_std"]) == aa.N_MELS
    for key in ("rms_mean", "rms_std", "spectral_centroid_mean", "spectral_bandwidth_mean",
                "logmel_overall_mean"):
        assert np.isfinite(f[key])
    assert f["rms_mean"] > 0
    assert 0 < f["spectral_centroid_mean"] < SR / 2
    assert f["duration_s"] == pytest.approx(2.0)
    vec = aa.feature_vector(f)
    assert vec.shape == (2 * aa.N_MFCC + 2 * aa.N_MELS + 6,)
    assert np.all(np.isfinite(vec))
    json.dumps(f)  # JSON-serialisable


def test_features_follow_the_signal():
    plain, changed = aa.extract_features(hum(1), SR), aa.extract_features(altered(1), SR)
    assert changed["spectral_centroid_mean"] > plain["spectral_centroid_mean"]
    assert changed["rms_mean"] > plain["rms_mean"]


def test_silence_and_other_sample_rate_are_finite():
    assert np.all(np.isfinite(aa.feature_vector(aa.extract_features(np.zeros(SR), SR))))
    f = aa.extract_features(hum(2, sr=44100), 44100)
    assert f["sample_rate"] == 44100 and f["analysis_sample_rate"] == aa.TARGET_SR
    assert np.all(np.isfinite(aa.feature_vector(f)))


def test_numpy_fallback_matches_shapes():
    fr = aa._frames_numpy(aa._prepare(hum(3), SR))
    assert fr["mfcc"].shape[0] == aa.N_MFCC and fr["logmel"].shape[0] == aa.N_MELS
    for value in fr.values():
        assert np.all(np.isfinite(value))
    assert 100 < float(np.mean(fr["centroid"])) < SR / 2


def test_bad_audio_rejected():
    with pytest.raises(ValueError):
        aa.extract_features(np.zeros(100), SR)
    bad = hum(0).copy()
    bad[10] = np.nan
    with pytest.raises(ValueError):
        aa.extract_features(bad, SR)


def test_wav_round_trip(tmp_path):
    path = aa.save_wav(tmp_path / "clip.wav", hum(4), SR)
    y, sr = aa.load_wav(path)
    assert sr == SR and y.ndim == 1 and y.size == 2 * SR
    assert np.allclose(y, hum(4), atol=1e-3)
    assert len(aa.load_and_extract(path)["mfcc_mean"]) == aa.N_MFCC


def test_insufficient_reference(store):
    add_refs("ST-1", aa.MIN_REFERENCES - 1)
    result = aa.score_clip("ST-1", altered(50), SR, source="synthetic")
    assert result["status"] == "insufficient_reference"
    assert result["anomaly_score"] is None and result["method"] is None
    assert result["reference_count"] == aa.MIN_REFERENCES - 1
    assert result["experimental"] is True and result["source"] == "synthetic"
    assert "not a diagnosis" in result["confidence_note"]
    assert_wording(result)
    # another station's references do not count
    assert aa.score_clip("ST-2", hum(51), SR, source="synthetic")["reference_count"] == 0


def test_normal_scores_lower_than_altered_zscore(store):
    add_refs("ST-1", 8)
    normal = aa.score_clip("ST-1", hum(100), SR, source="synthetic")
    odd = aa.score_clip("ST-1", altered(101), SR, source="synthetic")
    assert normal["status"] == odd["status"] == "scored"
    assert normal["method"] == "zscore_distance" and normal["reference_count"] == 8
    assert odd["anomaly_score"] > 2 * normal["anomaly_score"]
    assert odd["anomaly_score"] > odd["reference_z_distance_max"]
    for result in (normal, odd):
        assert set(["anomaly_score", "method", "reference_count", "confidence_note",
                    "experimental", "source"]) <= set(result)
        assert result["experimental"] is True and result["live_detection"] is False
        assert "experimental" in result["confidence_note"].lower()
        assert "not a diagnosis" in result["confidence_note"]
        assert_wording(result)


def test_isolation_forest_with_enough_references(store):
    add_refs("ST-9", aa.ISOLATION_FOREST_MIN_REFERENCES)
    normal = aa.score_clip("ST-9", hum(200), SR, source="synthetic")
    odd = aa.score_clip("ST-9", altered(201), SR, source="synthetic")
    assert normal["method"] == odd["method"] == "isolation_forest"
    assert odd["anomaly_score"] > normal["anomaly_score"]
    assert odd["z_distance"] > normal["z_distance"]
    assert_wording(odd)


def test_sources_preserved_and_stored(store):
    add_refs("ST-1", 3, source="synthetic")
    add_refs("ST-1", 3, source="prerecorded")
    refs = aa.list_references("ST-1")
    assert [r["source"] for r in refs] == ["synthetic"] * 3 + ["prerecorded"] * 3
    for r in refs:
        assert r["audio_path"].endswith(".wav")
        assert str(store / "recordings" / "acoustic" / "ST-1") in r["audio_path"]
        assert aa.load_wav(r["audio_path"])[1] == SR
    result = aa.score_clip("ST-1", hum(300), SR, source="prerecorded")
    assert result["source"] == "prerecorded"
    assert result["reference_sources"] == {"synthetic": 3, "prerecorded": 3}
    stored = aa.list_results("ST-1")
    assert len(stored) == 1 and stored[0]["result_id"] == result["result_id"]
    assert stored[0]["source"] == "prerecorded" and stored[0]["live_detection"] is False
    assert aa.list_results("ST-other") == []


@pytest.mark.parametrize("source", ["band", "live", "", "vexo"])
def test_live_or_unknown_source_refused(store, source):
    with pytest.raises(ValueError):
        aa.add_reference("ST-1", hum(0), SR, source=source)
    with pytest.raises(ValueError):
        aa.score_clip("ST-1", hum(0), SR, source=source)
    assert aa.list_references() == [] and aa.list_results() == []


def test_station_id_cannot_escape_audio_folder(store):
    for bad in ("../x", "a/b", "", "..", "a\\b"):
        with pytest.raises(ValueError):
            aa.add_reference(bad, hum(0), SR, source="synthetic")


@pytest.fixture()
def client(store):
    app = FastAPI()
    app.include_router(acoustic_router.router)
    return TestClient(app)


def test_router_json_path_flow(client, store):
    paths = [str(aa.save_wav(store / "in" / f"ref{i}.wav", hum(i), SR)) for i in range(6)]
    odd_path = str(aa.save_wav(store / "in" / "odd.wav", altered(77), SR))
    ok_path = str(aa.save_wav(store / "in" / "ok.wav", hum(78), SR))

    early = client.post("/api/acoustic/score",
                        json={"station_id": "ST-3", "path": ok_path, "source": "prerecorded"})
    assert early.status_code == 200 and early.json()["status"] == "insufficient_reference"

    for p in paths:
        r = client.post("/api/acoustic/reference",
                        json={"station_id": "ST-3", "path": p, "source": "prerecorded"})
        assert r.status_code == 200, r.text
        assert r.json()["source"] == "prerecorded"
    listing = client.get("/api/acoustic/references", params={"station_id": "ST-3"}).json()
    assert listing["count"] == 6 and listing["experimental"] is True
    assert "features" not in listing["references"][0]

    ok = client.post("/api/acoustic/score",
                     json={"station_id": "ST-3", "path": ok_path, "source": "prerecorded"}).json()
    odd = client.post("/api/acoustic/score",
                      json={"station_id": "ST-3", "path": odd_path, "source": "synthetic"}).json()
    assert ok["status"] == "scored" and odd["anomaly_score"] > ok["anomaly_score"]
    assert ok["source"] == "prerecorded" and odd["source"] == "synthetic"

    results = client.get("/api/acoustic/results", params={"station_id": "ST-3"}).json()
    assert results["count"] == 3
    assert results["results"][0]["result_id"] == odd["result_id"]  # newest first
    assert_wording(results)
    assert_wording(listing)


def test_router_multipart_upload(client, store):
    if not acoustic_router._multipart_available():
        pytest.skip("python-multipart not installed")
    path = aa.save_wav(store / "in" / "up.wav", hum(5), SR)
    r = client.post("/api/acoustic/reference",
                    data={"station_id": "ST-4", "source": "synthetic"},
                    files={"file": ("up.wav", path.read_bytes(), "audio/wav")})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "synthetic" and r.json()["reference_count"] == 1
    r = client.post("/api/acoustic/score",
                    data={"station_id": "ST-4", "source": "synthetic"},
                    files={"file": ("up.wav", path.read_bytes(), "audio/wav")})
    assert r.status_code == 200 and r.json()["status"] == "insufficient_reference"


def test_router_errors(client, store):
    wav = str(aa.save_wav(store / "in" / "a.wav", hum(6), SR))
    post = lambda body: client.post("/api/acoustic/reference", json=body).status_code
    assert post({"station_id": "ST-5", "path": str(store / "missing.wav"),
                 "source": "synthetic"}) == 404
    assert post({"station_id": "ST-5", "path": wav, "source": "band"}) == 422
    assert post({"station_id": "ST-5", "path": wav}) == 422
    assert post({"station_id": "../x", "path": wav, "source": "synthetic"}) == 422
    assert post({"station_id": "ST-5", "source": "synthetic"}) == 422
    assert post({"station_id": "ST-5", "path": str(store / "notes.txt"),
                 "source": "synthetic"}) == 422
    junk = store / "in" / "junk.wav"
    junk.write_bytes(b"not audio")
    assert post({"station_id": "ST-5", "path": str(junk), "source": "synthetic"}) == 422
    assert client.get("/api/acoustic/references").json()["count"] == 0
