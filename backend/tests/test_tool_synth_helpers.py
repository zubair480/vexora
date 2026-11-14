"""Synthetic IMU signals for the toolsense tests. TESTS ONLY.

Nothing here is real band data. Every batch / recording made here carries source="synthetic".
The shapes are deliberately crude (clearly separable) so tests check the pipeline's logic,
not real-world accuracy. Never train a shipped model on these.

Rows are (n, t, time, ax, ay, az, gx, gy, gz): accel in g, gyro in deg/s.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from backend.models import ImuBatch, ImuSample

T0 = 1_700_000_000.0
KINDS = ("still", "walking", "gesture", "tool")
HEADER = "n,t,time,ax,ay,az,gx,gy,gz"


def make(kind: str, seconds: float, hz: float = 50.0, seed: int = 0,
         t0: float = T0, n0: int = 0) -> np.ndarray:
    """One (N, 9) synthetic segment of a single activity sampled at `hz`."""
    rng = np.random.default_rng(seed)
    count = int(round(seconds * hz))
    s = np.arange(count) / hz
    acc = np.zeros((count, 3))
    acc[:, 2] = 1.0                                    # gravity, 1 g on z
    gyro = np.zeros((count, 3))
    if kind == "still":
        acc += rng.normal(0, 0.004, acc.shape)
        gyro += rng.normal(0, 0.4, gyro.shape)
    elif kind == "walking":                            # arm swing about 0.9 Hz, steps about 1.8 Hz
        ph = rng.uniform(0, 2 * np.pi)
        acc[:, 0] += 0.30 * np.sin(2 * np.pi * 1.8 * s + ph)
        acc[:, 2] += 0.20 * np.sin(2 * np.pi * 1.8 * s + ph + 1.0) + 0.08 * np.sin(2 * np.pi * 3.6 * s)
        acc[:, 1] += 0.12 * np.sin(2 * np.pi * 0.9 * s + ph)
        gyro[:, 1] += 70 * np.sin(2 * np.pi * 0.9 * s + ph)
        gyro[:, 0] += 25 * np.sin(2 * np.pi * 1.8 * s)
        acc += rng.normal(0, 0.02, acc.shape)
        gyro += rng.normal(0, 3.0, gyro.shape)
    elif kind == "gesture":                            # short big low-frequency bursts, rests between
        acc += rng.normal(0, 0.006, acc.shape)
        gyro += rng.normal(0, 0.8, gyro.shape)
        start = 0.3
        while start < seconds - 0.2:
            length = rng.uniform(0.5, 0.9)
            m = (s >= start) & (s < start + length)
            env = np.sin(np.pi * (s[m] - start) / length) ** 2
            f = rng.uniform(1.0, 3.0)
            acc[m, 0] += 0.7 * env * np.sin(2 * np.pi * f * (s[m] - start))
            acc[m, 1] += 0.5 * env * np.cos(2 * np.pi * f * (s[m] - start))
            gyro[m, 2] += 220 * env * np.sin(2 * np.pi * f * (s[m] - start))
            gyro[m, 0] += 120 * env
            start += length + rng.uniform(0.8, 1.6)
    elif kind == "tool":                               # sustained vibration-like, well above gait band
        for f, a in ((14.0, 0.22), (19.0, 0.28), (23.0, 0.18)):
            if f < hz / 2:
                ph = rng.uniform(0, 2 * np.pi, 3)
                for axis in range(3):
                    acc[:, axis] += a * np.sin(2 * np.pi * f * s + ph[axis])
        acc += rng.normal(0, 0.06, acc.shape)
        gyro += rng.normal(0, 6.0, gyro.shape)
        gyro[:, 0] += 8 * np.sin(2 * np.pi * 17.0 * s)
    else:
        raise ValueError(kind)
    out = np.empty((count, 9))
    out[:, 0] = n0 + np.arange(count)
    out[:, 1] = s
    out[:, 2] = t0 + s
    out[:, 3:6] = acc
    out[:, 6:9] = gyro
    return out


def sequence(parts: list[tuple[str, float]], hz: float = 50.0, seed: int = 0,
             t0: float = T0) -> np.ndarray:
    """Concatenate (kind, seconds) segments into one continuous recording."""
    chunks, n, t = [], 0, 0.0
    for i, (kind, seconds) in enumerate(parts):
        seg = make(kind, seconds, hz, seed + i, t0 + t, n)
        seg[:, 1] += t
        chunks.append(seg)
        n += len(seg)
        t += len(seg) / hz
    return np.vstack(chunks)


def drop(arr: np.ndarray, start_s: float, end_s: float) -> np.ndarray:
    """Remove samples between start_s and end_s (relative to the first sample): a data gap.
    `n` and `time` keep their original values, so the gap is visible in both."""
    rel = arr[:, 2] - arr[0, 2]
    return arr[(rel < start_s) | (rel >= end_s)]


def to_batches(arr: np.ndarray, hz: float = 50.0, source: str = "synthetic",
               batch_size: int = 10) -> list[ImuBatch]:
    batches = []
    for i in range(0, len(arr), batch_size):
        rows = arr[i:i + batch_size]
        samples = [ImuSample(int(r[0]), float(r[1]), float(r[2]), *map(float, r[3:9])) for r in rows]
        batches.append(ImuBatch(samples, configured_hz=hz, effective_hz=hz, source=source))
    return batches


def write_recording(root: Path, session_id: str, label: str, arr: np.ndarray,
                    hz: float = 50.0, source: str = "synthetic", **extra) -> Path:
    """Write a recording in the CONTRACT.md format under root/<session_id>/."""
    folder = Path(root) / session_id
    folder.mkdir(parents=True, exist_ok=True)
    fmt = ["%d", "%.6f", "%.6f"] + ["%.6f"] * 6
    np.savetxt(folder / "samples.csv", arr, delimiter=",", header=HEADER, comments="", fmt=fmt)
    meta = {"session_id": session_id, "label": label, "tool_id": None, "band_name": "synthetic",
            "wearer": "synthetic", "configured_hz": hz, "effective_hz": hz, "missing_samples": 0,
            "started_at": float(arr[0, 2]), "ended_at": float(arr[-1, 2]), "source": source,
            "notes": "synthetic test signal, not band data"}
    meta.update(extra)
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return folder
