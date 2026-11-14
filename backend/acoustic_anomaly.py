"""Acoustic anomaly screening (EXPERIMENTAL, not a diagnosis).

Compares the sound of a clip with reference clips of normal operation recorded at the same
conveyor station and returns a statistical distance. It never names a cause and never confirms
a defect. Nothing here connects to a band or a microphone: input is a mono PCM float array or a
WAV file, and every clip carries a provenance `source` ("prerecorded" or "synthetic"). There is
no live capture path, so a result is never a live Vexo detection.

No events are published (the contract has no acoustic event type): results are returned and
stored in this module's own tables.

Scoring
- fewer than MIN_REFERENCES references   -> status "insufficient_reference", no score
- MIN_REFERENCES .. 19 references        -> method "zscore_distance"
- ISOLATION_FOREST_MIN_REFERENCES (20)+  -> method "isolation_forest" (z distance also reported)
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Optional

import numpy as np
from scipy import signal as _signal

from . import config, database

try:  # librosa is preferred; the numpy/scipy path below computes the same feature set
    import librosa as _librosa
    FEATURE_BACKEND = "librosa"
except Exception:  # pragma: no cover - depends on the environment
    _librosa = None
    FEATURE_BACKEND = "numpy-scipy"

TARGET_SR = 16000
N_FFT = 1024
HOP = 512
N_MELS = 40
N_MFCC = 13
MIN_CLIP_SECONDS = 0.5

MIN_REFERENCES = 5
ISOLATION_FOREST_MIN_REFERENCES = 20

VALID_SOURCES = ("prerecorded", "synthetic")
SCREENING_NOTE = "Acoustic anomaly screening, experimental, not a diagnosis."

database.register_schema(
    """
    CREATE TABLE IF NOT EXISTS acoustic_references (
        id TEXT PRIMARY KEY, station_id TEXT NOT NULL, source TEXT NOT NULL,
        audio_path TEXT, sample_rate INTEGER NOT NULL, duration_s REAL NOT NULL,
        feature_backend TEXT NOT NULL, features TEXT NOT NULL, created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS acoustic_references_station ON acoustic_references (station_id);
    CREATE TABLE IF NOT EXISTS acoustic_results (
        id TEXT PRIMARY KEY, station_id TEXT NOT NULL, source TEXT NOT NULL,
        audio_path TEXT, status TEXT NOT NULL, anomaly_score REAL, method TEXT,
        reference_count INTEGER NOT NULL, result TEXT NOT NULL, created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS acoustic_results_station ON acoustic_results (station_id);
    """
)


# ---------------------------------------------------------------------------- audio helpers

def load_wav(path) -> tuple[np.ndarray, int]:
    """Read a WAV file as mono float32 in [-1, 1]. Returns (samples, sample_rate)."""
    import soundfile as sf

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"audio file not found: {path}")
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return data.mean(axis=1).astype(np.float32), int(sr)


def save_wav(path, y, sr: int) -> Path:
    import soundfile as sf

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.clip(np.asarray(y, dtype=np.float32), -1.0, 1.0), int(sr),
             subtype="PCM_16")
    return path


def _prepare(y, sr: int) -> np.ndarray:
    y = np.asarray(y, dtype=np.float64)
    if y.ndim == 2:  # (samples, channels) -> mono
        y = y.mean(axis=1)
    if y.ndim != 1:
        raise ValueError("audio must be a mono PCM float array")
    if not sr or sr <= 0:
        raise ValueError("sample rate must be positive")
    if y.size < int(MIN_CLIP_SECONDS * sr):
        raise ValueError(f"clip is too short (need at least {MIN_CLIP_SECONDS} s)")
    if not np.all(np.isfinite(y)):
        raise ValueError("audio contains non-finite samples")
    if int(sr) != TARGET_SR:
        g = int(np.gcd(int(sr), TARGET_SR))
        y = _signal.resample_poly(y, TARGET_SR // g, int(sr) // g)
    return y.astype(np.float32)


# ------------------------------------------------------------------------ feature extraction

def _mel_filterbank() -> np.ndarray:
    def hz_to_mel(f):
        return 2595.0 * np.log10(1.0 + np.asarray(f, dtype=float) / 700.0)

    def mel_to_hz(m):
        return 700.0 * (10.0 ** (np.asarray(m, dtype=float) / 2595.0) - 1.0)

    freqs = np.linspace(0, TARGET_SR / 2, N_FFT // 2 + 1)
    edges = mel_to_hz(np.linspace(hz_to_mel(0), hz_to_mel(TARGET_SR / 2), N_MELS + 2))
    bank = np.zeros((N_MELS, freqs.size))
    for i in range(N_MELS):
        lo, mid, hi = edges[i], edges[i + 1], edges[i + 2]
        bank[i] = np.maximum(0, np.minimum((freqs - lo) / (mid - lo), (hi - freqs) / (hi - mid)))
    return bank


def _frames_numpy(y: np.ndarray) -> dict:
    """numpy/scipy implementation of the same frame-level features (used without librosa)."""
    from scipy.fft import dct

    freqs, _, spec = _signal.stft(y, fs=TARGET_SR, window="hann", nperseg=N_FFT,
                                  noverlap=N_FFT - HOP, boundary="zeros", padded=True)
    mag = np.abs(spec) * (N_FFT / 2)  # undo scipy's window-sum scaling, roughly librosa's scale
    power = mag ** 2
    logmel = 10.0 * np.log10(np.maximum(_mel_filterbank() @ power, 1e-10))
    mfcc = dct(logmel, type=2, axis=0, norm="ortho")[:N_MFCC]
    total = np.maximum(mag.sum(axis=0), 1e-12)
    centroid = (freqs[:, None] * mag).sum(axis=0) / total
    bandwidth = np.sqrt((((freqs[:, None] - centroid[None, :]) ** 2) * mag).sum(axis=0) / total)
    pad = np.pad(y, N_FFT // 2)
    n = 1 + (pad.size - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    rms = np.sqrt(np.mean(pad[idx].astype(np.float64) ** 2, axis=1))
    return {"logmel": logmel, "mfcc": mfcc, "rms": rms, "centroid": centroid,
            "bandwidth": bandwidth}


def _frames_librosa(y: np.ndarray) -> dict:
    mel = _librosa.feature.melspectrogram(y=y, sr=TARGET_SR, n_fft=N_FFT, hop_length=HOP,
                                          n_mels=N_MELS, power=2.0)
    logmel = _librosa.power_to_db(mel, ref=1.0, top_db=None)  # absolute level, so loudness counts
    return {
        "logmel": logmel,
        "mfcc": _librosa.feature.mfcc(S=logmel, n_mfcc=N_MFCC),
        "rms": _librosa.feature.rms(y=y, frame_length=N_FFT, hop_length=HOP)[0],
        "centroid": _librosa.feature.spectral_centroid(y=y, sr=TARGET_SR, n_fft=N_FFT,
                                                       hop_length=HOP)[0],
        "bandwidth": _librosa.feature.spectral_bandwidth(y=y, sr=TARGET_SR, n_fft=N_FFT,
                                                         hop_length=HOP)[0],
    }


def extract_features(y, sr: int) -> dict:
    """Features of one mono clip. Lists are plain floats so the dict is JSON-serialisable.

    Keys: mfcc_mean[13], mfcc_std[13], logmel_mean[40], logmel_std[40], logmel_overall_mean,
    logmel_overall_std, rms_mean, rms_std, spectral_centroid_mean/_std (Hz),
    spectral_bandwidth_mean/_std (Hz), duration_s, sample_rate (input), analysis_sample_rate,
    n_frames, feature_backend.
    """
    duration = float(np.asarray(y).shape[0]) / float(sr) if sr else 0.0
    clip = _prepare(y, sr)
    fr = _frames_librosa(clip) if _librosa is not None else _frames_numpy(clip)
    logmel = np.asarray(fr["logmel"], dtype=np.float64)
    mfcc = np.asarray(fr["mfcc"], dtype=np.float64)
    features = {
        "mfcc_mean": mfcc.mean(axis=1).tolist(),
        "mfcc_std": mfcc.std(axis=1).tolist(),
        "logmel_mean": logmel.mean(axis=1).tolist(),
        "logmel_std": logmel.std(axis=1).tolist(),
        "logmel_overall_mean": float(logmel.mean()),
        "logmel_overall_std": float(logmel.std()),
        "rms_mean": float(np.mean(fr["rms"])),
        "rms_std": float(np.std(fr["rms"])),
        "spectral_centroid_mean": float(np.mean(fr["centroid"])),
        "spectral_centroid_std": float(np.std(fr["centroid"])),
        "spectral_bandwidth_mean": float(np.mean(fr["bandwidth"])),
        "spectral_bandwidth_std": float(np.std(fr["bandwidth"])),
        "duration_s": duration,
        "sample_rate": int(sr),
        "analysis_sample_rate": TARGET_SR,
        "n_frames": int(logmel.shape[1]),
        "feature_backend": FEATURE_BACKEND,
    }
    return features


# (key, absolute floor on the per-feature reference spread)
_VECTOR_LAYOUT = (
    ("mfcc_mean", 0.5), ("mfcc_std", 0.5), ("logmel_mean", 0.5), ("logmel_std", 0.5),
    ("rms_mean", 1e-6), ("rms_std", 1e-6),
    ("spectral_centroid_mean", 1.0), ("spectral_centroid_std", 1.0),
    ("spectral_bandwidth_mean", 1.0), ("spectral_bandwidth_std", 1.0),
)
_RELATIVE_FLOOR = 0.05


def feature_vector(features: dict) -> np.ndarray:
    """Flat numeric vector used for scoring (length 2*13 + 2*40 + 6 = 112)."""
    parts = [np.atleast_1d(np.asarray(features[key], dtype=np.float64)) for key, _ in _VECTOR_LAYOUT]
    return np.concatenate(parts)


def _abs_floor(features: dict) -> np.ndarray:
    return np.concatenate([np.full(np.atleast_1d(features[key]).shape[0], floor)
                           for key, floor in _VECTOR_LAYOUT])


def load_and_extract(path) -> dict:
    y, sr = load_wav(path)
    return extract_features(y, sr)


# -------------------------------------------------------------------------------- storage

def _check_station(station_id: str) -> str:
    station_id = (station_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}", station_id) or ".." in station_id:
        raise ValueError("station_id must be 1-64 characters: letters, digits, '_', '-', '.'")
    return station_id


def _check_source(source: str) -> str:
    if source not in VALID_SOURCES:
        raise ValueError(
            f"source must be one of {list(VALID_SOURCES)}: this experimental module has no live "
            "capture path and never labels a clip as a live Vexo detection"
        )
    return source


def audio_root() -> Path:
    return Path(config.RECORDINGS) / "acoustic"


def _resolve_audio(y, sr, path):
    if path is not None:
        return load_wav(path)
    if y is None or sr is None:
        raise ValueError("give either a WAV path or samples with a sample rate")
    return np.asarray(y), int(sr)


def add_reference(station_id: str, y=None, sr: Optional[int] = None, *, path=None,
                  source: str, store_audio: bool = True) -> dict:
    """Store one clip of normal operation for a station (features in SQLite, audio as WAV under
    recordings/acoustic/<station_id>/). Returns the stored reference summary."""
    station_id = _check_station(station_id)
    source = _check_source(source)
    y, sr = _resolve_audio(y, sr, path)
    features = extract_features(y, sr)
    ref_id = "aref-" + uuid.uuid4().hex[:12]
    audio_path = None
    if store_audio:
        audio_path = str(save_wav(audio_root() / station_id / f"{ref_id}.wav", y, sr))
    created = time.time()
    con = database.connect()
    try:
        with con:
            con.execute(
                "INSERT INTO acoustic_references VALUES (?,?,?,?,?,?,?,?,?)",
                (ref_id, station_id, source, audio_path, int(sr), features["duration_s"],
                 features["feature_backend"], json.dumps(features), created),
            )
    finally:
        con.close()
    return {
        "reference_id": ref_id, "station_id": station_id, "source": source,
        "audio_path": audio_path, "sample_rate": int(sr), "duration_s": features["duration_s"],
        "feature_backend": features["feature_backend"], "created_at": created,
        "reference_count": reference_count(station_id), "experimental": True,
        "live_detection": False,
    }


def list_references(station_id: Optional[str] = None, include_features: bool = False) -> list[dict]:
    sql, args = "SELECT * FROM acoustic_references", []
    if station_id:
        sql += " WHERE station_id = ?"
        args.append(station_id)
    con = database.connect()
    try:
        rows = con.execute(sql + " ORDER BY created_at ASC, rowid ASC", args).fetchall()
    finally:
        con.close()
    out = []
    for row in rows:
        item = dict(row)
        features = json.loads(item.pop("features"))
        item["reference_id"] = item.pop("id")
        if include_features:
            item["features"] = features
        out.append(item)
    return out


def reference_count(station_id: str) -> int:
    con = database.connect()
    try:
        return int(con.execute(
            "SELECT COUNT(*) FROM acoustic_references WHERE station_id = ? AND feature_backend = ?",
            (station_id, FEATURE_BACKEND)).fetchone()[0])
    finally:
        con.close()


def list_results(station_id: Optional[str] = None, limit: int = 200) -> list[dict]:
    sql, args = "SELECT result FROM acoustic_results", []
    if station_id:
        sql += " WHERE station_id = ?"
        args.append(station_id)
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    args.append(int(limit))
    con = database.connect()
    try:
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()
    return [json.loads(row["result"]) for row in rows]


# -------------------------------------------------------------------------------- scoring

def _z_distance(vec: np.ndarray, mean: np.ndarray, spread: np.ndarray) -> float:
    return float(np.sqrt(np.mean(((vec - mean) / spread) ** 2)))


def score_features(features: dict, reference_features: list[dict]) -> dict:
    """Pure scoring: one clip's features against a station's reference features.

    `anomaly_score` is higher for clips less like the references. For "zscore_distance" it is
    the RMS z-score across features (about 1 for a clip like the references). For
    "isolation_forest" it is the negated sklearn `score_samples` (about 0.35-0.5 for typical
    clips, towards 1 for outliers); `z_distance` is reported alongside.
    """
    n = len(reference_features)
    if n < MIN_REFERENCES:
        return {
            "status": "insufficient_reference",
            "anomaly_score": None,
            "method": None,
            "reference_count": n,
            "min_references": MIN_REFERENCES,
            "confidence_note": (
                f"{SCREENING_NOTE} No score: {n} reference clip(s) of normal operation for this "
                f"station, at least {MIN_REFERENCES} are needed."
            ),
        }
    refs = np.vstack([feature_vector(f) for f in reference_features])
    vec = feature_vector(features)
    mean = refs.mean(axis=0)
    spread = np.maximum(refs.std(axis=0, ddof=1),
                        _abs_floor(features) + _RELATIVE_FLOOR * np.abs(mean))
    z_dist = _z_distance(vec, mean, spread)
    # how far the references sit from each other, for context (leave-one-in, so slightly low)
    ref_dists = [_z_distance(r, mean, spread) for r in refs]
    out = {
        "status": "scored",
        "reference_count": n,
        "z_distance": z_dist,
        "reference_z_distance_max": float(max(ref_dists)),
        "reference_z_distance_median": float(np.median(ref_dists)),
    }
    if n >= ISOLATION_FOREST_MIN_REFERENCES:
        from sklearn.ensemble import IsolationForest

        forest = IsolationForest(n_estimators=200, random_state=0)
        forest.fit((refs - mean) / spread)
        out["method"] = "isolation_forest"
        out["anomaly_score"] = float(-forest.score_samples(((vec - mean) / spread)[None, :])[0])
        confidence = "low to moderate"
    else:
        out["method"] = "zscore_distance"
        out["anomaly_score"] = z_dist
        confidence = "low"
    out["confidence_note"] = (
        f"{SCREENING_NOTE} Statistical distance from {n} reference clip(s) of normal operation "
        f"at this station ({out['method']}); confidence {confidence}. It does not identify a "
        "cause, is not validated on real conveyor audio, and needs a human to listen and inspect."
    )
    return out


def score_clip(station_id: str, y=None, sr: Optional[int] = None, *, path=None,
               source: str, store: bool = True, store_audio: bool = True) -> dict:
    """Score a clip against the station's stored references; store and return the result.

    Returns `{result_id, station_id, status, anomaly_score, method, reference_count,
    confidence_note, experimental: True, source, live_detection: False, ...}`. With too few
    references `status` is "insufficient_reference" and `anomaly_score` is None.
    """
    station_id = _check_station(station_id)
    source = _check_source(source)
    y, sr = _resolve_audio(y, sr, path)
    features = extract_features(y, sr)
    refs = [r for r in list_references(station_id, include_features=True)
            if r["feature_backend"] == features["feature_backend"]]
    result = score_features(features, [r["features"] for r in refs])
    result_id = "ares-" + uuid.uuid4().hex[:12]
    audio_path = None
    if store and store_audio:
        audio_path = str(save_wav(audio_root() / station_id / "scored" / f"{result_id}.wav", y, sr))
    sources: dict[str, int] = {}
    for r in refs:
        sources[r["source"]] = sources.get(r["source"], 0) + 1
    result.update({
        "result_id": result_id,
        "station_id": station_id,
        "source": source,
        "reference_sources": sources,
        "experimental": True,
        "live_detection": False,
        "screening_label": SCREENING_NOTE,
        "feature_backend": features["feature_backend"],
        "duration_s": features["duration_s"],
        "audio_path": audio_path,
        "created_at": time.time(),
    })
    if store:
        con = database.connect()
        try:
            with con:
                con.execute(
                    "INSERT INTO acoustic_results VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (result_id, station_id, source, audio_path, result["status"],
                     result["anomaly_score"], result["method"], result["reference_count"],
                     json.dumps(result), result["created_at"]),
                )
        finally:
            con.close()
    return result
