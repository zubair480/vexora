"""Windowed IMU features for the toolsense activity detector.

Rows are float64 (N, 9): n, t, time, ax, ay, az, gx, gy, gz (accel in g, gyro in deg/s,
`time` epoch seconds, `n` the band sample counter: a jump means lost samples, NaN if unknown).

The sampling rate is always measured from the data (the kit says the nominal rate is "only a
name"). Windows are laid out in seconds, spectra are reported in Hz and band powers in g^2, so
features are comparable across rates. Wrist acceleration here is activity evidence only: none
of these numbers is a tool vibration magnitude or an exposure measurement.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal

COLUMNS = ("n", "t", "time", "ax", "ay", "az", "gx", "gy", "gz")
ANALYSIS_BANDS_HZ = ((0.5, 3), (3, 8), (8, 15), (15, 25), (25, 50), (50, 100))   # fixed, in Hz
BAND_FEATURES = tuple(f"band_power_{lo:g}_{hi:g}" for lo, hi in ANALYSIS_BANDS_HZ)
FEATURE_NAMES: tuple[str, ...] = (
    "acc_mag_mean",      # mean |accel| (g), gravity included
    "acc_mag_std",
    "acc_mag_rms",
    "acc_mag_var",
    "dyn_acc_rms",       # RMS of |accel - gravity estimate| (g)
    "acc_peak",          # max |dynamic accel| (g)
    "signal_energy",     # mean squared dynamic accel (g^2 per sample: rate independent)
    "gyro_mag_mean",     # deg/s
    "gyro_mag_var",
    "dom_freq_1",        # Hz, strongest peak of the summed-axes dynamic accel PSD (DC excluded)
    "dom_freq_2",        # Hz, second peak (0.0 if there is none)
    *BAND_FEATURES,      # g^2 in each ANALYSIS_BANDS_HZ band; 0.0 if the band is above Nyquist
    "high_freq_ratio",   # power above 8 Hz / power in 0.5..min(25, Nyquist) Hz
    "spectral_entropy",  # 0..1 over 0.5..min(25, Nyquist) Hz
    "saturated",         # 0/1 quality flag
    "completeness",      # 0..1 quality flag
)
QUALITY_FEATURES = ("saturated", "completeness")   # in FEATURE_NAMES, but never model inputs
# The band's ranges after every connect (kit docs/api.md). A clipped reading sits at the range.
ACCEL_SATURATION_G: float = 2.0
GYRO_SATURATION_DPS: float = 2000.0

_SAT_MARGIN = 0.98        # at or above this fraction of full scale counts as clipped
_NYQUIST_SLACK = 1.05     # a band counts as "below Nyquist" if hi <= 1.05 * hz/2 (49.9 Hz bands exist)
_FREQ_STEP_HZ = 0.25      # zero-padded spectrum grid, the same at every rate
_SPEC_LO, _SPEC_HI, _HF_HZ = 0.5, 25.0, 8.0
_MIN_FRACTION = 0.25      # extract_windows skips windows with fewer samples than this
_LATEST_MIN_FRACTION = 0.6
_EPS = 1e-12
_IDX = {name: i for i, name in enumerate(FEATURE_NAMES)}


@dataclass
class WindowFeatures:
    start_time: float      # `time` of first sample in the window
    end_time: float        # `time` of last sample in the window
    sample_hz: float       # measured sampling rate used for the spectra
    bandwidth_hz: float    # valid bandwidth = sample_hz / 2
    n_samples: int
    completeness: float    # 0..1, received / expected samples in the window
    saturated: bool
    values: np.ndarray     # shape (len(FEATURE_NAMES),)

    def as_dict(self) -> dict[str, float]:
        return {name: float(v) for name, v in zip(FEATURE_NAMES, self.values)}


def _as_rows(arr) -> np.ndarray:
    a = np.asarray(arr, dtype=np.float64)
    if a.size == 0:
        return np.empty((0, len(COLUMNS)))
    return a.reshape(-1, len(COLUMNS))


def samples_to_array(samples) -> np.ndarray:
    """Iterable of models.ImuSample -> (N, 9). A missing `n` or sensor value becomes NaN."""
    rows = [[np.nan if getattr(s, c) is None else float(getattr(s, c)) for c in COLUMNS]
            for s in samples]
    return _as_rows(rows)


def load_samples_csv(path) -> np.ndarray:
    """Read a recording's samples.csv (header n,t,time,ax,...) -> (N, 9); empty `n` -> NaN."""
    with open(path, encoding="utf-8") as fh:
        lines = [ln.strip() for ln in fh if ln.strip()]
    if not lines:
        return _as_rows([])
    header = [h.strip().lstrip("#").strip() for h in lines[0].split(",")]
    if set(header) & set(COLUMNS):
        lines = lines[1:]
    else:
        header = list(COLUMNS)

    def num(text: str) -> float:
        try:
            return float(text)
        except ValueError:
            return np.nan

    out = np.full((len(lines), len(COLUMNS)), np.nan)
    for r, line in enumerate(lines):
        cells = dict(zip(header, line.split(",")))
        for c, name in enumerate(COLUMNS):
            out[r, c] = num(cells.get(name, "").strip())
    return out


def _time_axis(arr: np.ndarray) -> np.ndarray | None:
    """Seconds for each row: `time` if usable, else the band counter `t`, else None."""
    for col, name in ((2, "time"), (1, "t")):
        x = arr[:, col]
        if len(x) >= 2 and np.all(np.isfinite(x)) and x[-1] > x[0] and np.ptp(x) > 0:
            d = np.diff(x)
            pos = d[d > 0]
            # models.ImuSample.t is seconds, the kit's own `t` is milliseconds: accept both.
            if name == "t" and len(pos) and np.median(pos) >= 0.5:
                return x / 1000.0
            return x
    return None


def measure_rate(arr) -> float:
    """Measured sampling rate in Hz (0.0 if it cannot be measured).

    Preferred: samples sent / elapsed time, i.e. sum(n_last - n_first) / sum(time_last -
    time_first) over runs where `n` increases: lost samples and delivery jitter do not bias
    it. Without a usable `n`: 1 / median(diff(time)). `t` replaces `time` if `time` is unusable.
    """
    a = _as_rows(arr)
    if len(a) < 2:
        return 0.0
    x = _time_axis(a)
    if x is None:
        return 0.0
    dt = np.diff(x)
    n = a[:, 0]
    dn = np.diff(n)
    ok = np.isfinite(dn) & (dn > 0) & (dt > 0)     # False at NaN, n resets and time steps back
    if ok.sum() >= max(3, 0.5 * len(dt)):
        hz = float(dn[ok].sum() / dt[ok].sum())
        if np.isfinite(hz) and hz > 0:
            return hz
    pos = dt[dt > 0]
    if not len(pos):
        return 0.0
    return float(1.0 / np.median(pos))


def estimate_gravity(acc, hz, cutoff_hz=0.5) -> np.ndarray:
    """(N, 3) gravity estimate: zero-phase low-pass of the acceleration.

    Falls back to the per-axis mean when the array is too short to filter (under one cutoff
    period) or the rate is unusable. Samples are treated as contiguous.
    """
    acc = np.asarray(acc, dtype=np.float64).reshape(-1, 3)
    if not len(acc):
        return acc.copy()
    acc = np.where(np.isfinite(acc), acc, 0.0)
    mean = np.broadcast_to(acc.mean(axis=0), acc.shape).copy()
    if not (np.isfinite(hz) and hz > 4 * cutoff_hz) or len(acc) < max(16, hz / cutoff_hz):
        return mean
    sos = signal.butter(2, cutoff_hz, btype="low", fs=hz, output="sos")
    padlen = int(min(len(acc) - 1, hz / cutoff_hz))
    out = signal.sosfiltfilt(sos, acc, axis=0, padtype="even", padlen=padlen)
    return out if np.all(np.isfinite(out)) else mean


def features_for_model(hz: float) -> tuple[str, ...]:
    """FEATURE_NAMES minus the quality flags minus band powers not fully below hz / 2."""
    nyq = hz / 2.0 * _NYQUIST_SLACK
    drop = set(QUALITY_FEATURES) | {name for name, (_, hi) in zip(BAND_FEATURES, ANALYSIS_BANDS_HZ)
                                    if hi > nyq}
    return tuple(name for name in FEATURE_NAMES if name not in drop)


def _spectrum(dyn: np.ndarray, hz: float) -> tuple[np.ndarray, np.ndarray]:
    """Summed-axes PSD (g^2/Hz) of the dynamic acceleration on a rate-independent grid."""
    n = len(dyn)
    if n < 8 or hz <= 0:
        return np.zeros(1), np.zeros(1)
    nfft = max(n, int(round(hz / _FREQ_STEP_HZ)))
    freqs, psd = signal.periodogram(dyn, fs=hz, window="hann", nfft=nfft, detrend="constant",
                                    scaling="density", axis=0)
    return freqs, np.nan_to_num(psd.sum(axis=1), nan=0.0, posinf=0.0, neginf=0.0)


def _dominant(freqs: np.ndarray, psd: np.ndarray) -> tuple[float, float]:
    """Two strongest spectral peaks at or above 0.5 Hz, at least 1 Hz apart."""
    m = freqs >= _SPEC_LO
    f, p = freqs[m], psd[m]
    if len(p) < 3 or p.max() <= _EPS:
        return 0.0, 0.0
    first = int(np.argmax(p))
    peaks, _ = signal.find_peaks(p)
    peaks = [i for i in peaks if abs(f[i] - f[first]) >= 1.0 and p[i] >= 0.05 * p[first]]
    second = float(f[max(peaks, key=lambda i: p[i])]) if peaks else 0.0
    return float(f[first]), second


def _window_features(rows: np.ndarray, dyn: np.ndarray, hz: float, window_s: float,
                     accel_sat_g: float, gyro_sat_dps: float) -> WindowFeatures:
    acc = np.nan_to_num(rows[:, 3:6])
    gyro = np.nan_to_num(rows[:, 6:9])
    count = len(rows)
    acc_mag = np.linalg.norm(acc, axis=1)
    dyn_mag = np.linalg.norm(dyn, axis=1)
    gyro_mag = np.linalg.norm(gyro, axis=1)
    energy = float(np.mean(dyn_mag ** 2))

    nyq = hz / 2.0
    freqs, psd = _spectrum(dyn, hz)
    df = freqs[1] - freqs[0] if len(freqs) > 1 else 0.0
    bands = []
    for lo, hi in ANALYSIS_BANDS_HZ:
        inside = (freqs >= lo) & (freqs < hi)
        bands.append(float(psd[inside].sum() * df) if hi <= nyq * _NYQUIST_SLACK else 0.0)
    top = min(_SPEC_HI, nyq)
    core = (freqs >= _SPEC_LO) & (freqs <= top)
    total = float(psd[core].sum() * df)
    hf_ratio, entropy = 0.0, 0.0
    if total > _EPS and core.sum() > 1:
        hf_ratio = float(psd[core & (freqs > _HF_HZ)].sum() * df / total)
        p = psd[core] / psd[core].sum()
        p = p[p > 0]
        entropy = float(-(p * np.log(p)).sum() / np.log(core.sum()))
    dom1, dom2 = _dominant(freqs, psd) if total > _EPS else (0.0, 0.0)

    saturated = bool(np.any(np.abs(acc) >= accel_sat_g * _SAT_MARGIN)
                     or np.any(np.abs(gyro) >= gyro_sat_dps * _SAT_MARGIN))
    expected = max(1.0, window_s * hz)
    completeness = min(1.0, count / expected)
    n = rows[:, 0]
    if count > 1 and np.all(np.isfinite(n)) and np.all(np.diff(n) > 0):   # lost samples seen in n
        completeness = min(completeness, count / float(n[-1] - n[0] + 1))
    completeness = float(np.clip(completeness, 0.0, 1.0))

    values = np.array([
        acc_mag.mean(), acc_mag.std(), np.sqrt(np.mean(acc_mag ** 2)), acc_mag.var(),
        np.sqrt(energy), dyn_mag.max(), energy,
        gyro_mag.mean(), gyro_mag.var(),
        dom1, dom2, *bands, hf_ratio, min(1.0, entropy),
        float(saturated), completeness,
    ], dtype=np.float64)
    values = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)
    return WindowFeatures(start_time=float(rows[0, 2]), end_time=float(rows[-1, 2]),
                          sample_hz=float(hz), bandwidth_hz=float(nyq), n_samples=count,
                          completeness=completeness, saturated=saturated, values=values)


def _prepare(arr, hz):
    """Sorted rows, their time axis (s), measured rate and dynamic acceleration, or None."""
    a = _as_rows(arr)
    if len(a) < 2:
        return None
    rate = float(hz) if hz else measure_rate(a)
    if not np.isfinite(rate) or rate <= 0:
        return None
    x = _time_axis(a)
    if x is None:
        x = np.arange(len(a)) / rate            # no usable clock: place samples by the given rate
    elif np.any(np.diff(x) < 0):
        order = np.argsort(x, kind="stable")
        a, x = a[order], x[order]
    if not np.all(np.isfinite(a[:, 2])):        # keep start_time / end_time finite
        a = a.copy()
        a[:, 2] = x
    acc = np.nan_to_num(a[:, 3:6])
    return a, x, rate, acc - estimate_gravity(acc, rate)


def extract_windows(arr, window_s=1.5, overlap=0.5, hz=None, *, accel_sat_g=None,
                    gyro_sat_dps=None) -> list[WindowFeatures]:
    """Features for windows of `window_s` seconds laid on the time axis, `overlap` a fraction.

    Only whole windows are laid (a recording shorter than one window gives a single window).
    A window over a data gap keeps just the samples that exist, with completeness < 1; a
    window with fewer than ~25 % of the expected samples is skipped. Nothing is interpolated.
    """
    prep = _prepare(arr, hz)
    if prep is None or window_s <= 0:
        return []
    a, x, rate, dyn = prep
    sat_a = ACCEL_SATURATION_G if accel_sat_g is None else accel_sat_g
    sat_g = GYRO_SATURATION_DPS if gyro_sat_dps is None else gyro_sat_dps
    rel = x - x[0]
    eps = 0.25 / rate
    duration = rel[-1] + 1.0 / rate
    step = window_s * (1.0 - min(max(float(overlap), 0.0), 0.95))
    count = max(1, int(np.floor((duration - window_s) / step + 1e-6)) + 1)
    expected = window_s * rate
    out = []
    for k in range(count):
        start = k * step
        lo, hi = np.searchsorted(rel, [start - eps, start + window_s - eps])
        if hi - lo < max(2, _MIN_FRACTION * expected):
            continue
        out.append(_window_features(a[lo:hi], dyn[lo:hi], rate, window_s, sat_a, sat_g))
    return out


def latest_window(arr, window_s=1.5, hz=None, *, accel_sat_g=None,
                  gyro_sat_dps=None) -> WindowFeatures | None:
    """Features of the last `window_s` seconds of the buffer (gravity from the whole buffer).

    None if those seconds hold less than ~60 % of a window of samples.
    """
    prep = _prepare(arr, hz)
    if prep is None or window_s <= 0:
        return None
    a, x, rate, dyn = prep
    lo = int(np.searchsorted(x, x[-1] - window_s + 0.5 / rate))
    if len(a) - lo < max(2, _LATEST_MIN_FRACTION * window_s * rate):
        return None
    return _window_features(a[lo:], dyn[lo:], rate, window_s,
                            ACCEL_SATURATION_G if accel_sat_g is None else accel_sat_g,
                            GYRO_SATURATION_DPS if gyro_sat_dps is None else gyro_sat_dps)


def feature_matrix(windows, names=FEATURE_NAMES) -> np.ndarray:
    """(M, len(names)) matrix, columns picked by name from each window's values."""
    cols = [_IDX[name] for name in names]
    if not windows:
        return np.empty((0, len(cols)))
    return np.vstack([w.values for w in windows])[:, cols]
