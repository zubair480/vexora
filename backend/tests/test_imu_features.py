"""Tests for backend.imu_features. Synthetic signals only (see test_tool_synth_helpers)."""
from __future__ import annotations

import numpy as np
import pytest

from backend import imu_features as f
from backend.models import ImuSample
from backend.tests.test_tool_synth_helpers import T0, drop, make, write_recording


def med(arr, **kw) -> dict[str, float]:
    """Median of each feature over the windows of a recording."""
    windows = f.extract_windows(arr, **kw)
    assert windows
    return dict(zip(f.FEATURE_NAMES, np.median(f.feature_matrix(windows), axis=0)))


def tones(seconds: float, hz: float) -> np.ndarray:
    """The same noise-free underlying signal (1.8 Hz + 12 Hz) sampled at `hz`."""
    count = int(round(seconds * hz))
    s = np.arange(count) / hz
    out = np.zeros((count, 9))
    out[:, 0], out[:, 1], out[:, 2] = np.arange(count), s, T0 + s
    out[:, 3] = 0.30 * np.sin(2 * np.pi * 1.8 * s)
    out[:, 4] = 0.20 * np.sin(2 * np.pi * 12.0 * s + 0.5)
    out[:, 5] = 1.0 + 0.10 * np.sin(2 * np.pi * 1.8 * s + 1.0)
    out[:, 6] = 60 * np.sin(2 * np.pi * 1.8 * s)
    out[:, 8] = 20 * np.sin(2 * np.pi * 12.0 * s)
    return out


def test_feature_names_stable_unique_and_sized():
    assert len(set(f.FEATURE_NAMES)) == len(f.FEATURE_NAMES)
    assert f.COLUMNS == ("n", "t", "time", "ax", "ay", "az", "gx", "gy", "gz")
    assert set(f.QUALITY_FEATURES) <= set(f.FEATURE_NAMES)
    for lo, hi in f.ANALYSIS_BANDS_HZ:
        assert f"band_power_{lo:g}_{hi:g}" in f.FEATURE_NAMES
    for name in ("acc_mag_mean", "dyn_acc_rms", "acc_peak", "signal_energy", "gyro_mag_mean",
                 "dom_freq_1", "dom_freq_2", "high_freq_ratio", "spectral_entropy"):
        assert name in f.FEATURE_NAMES
    w = f.extract_windows(make("walking", 5))[0]
    assert w.values.shape == (len(f.FEATURE_NAMES),)
    assert list(w.as_dict()) == list(f.FEATURE_NAMES)
    assert w.bandwidth_hz == pytest.approx(w.sample_hz / 2)


def test_features_for_model_drops_quality_and_bands_above_nyquist():
    at50 = f.features_for_model(50)
    assert not set(f.QUALITY_FEATURES) & set(at50)
    assert "band_power_15_25" in at50 and "band_power_25_50" not in at50
    assert "band_power_15_25" in f.features_for_model(49.9)      # a nominal 50 Hz band
    assert "band_power_25_50" in f.features_for_model(104)
    assert "band_power_50_100" not in f.features_for_model(104)
    assert f.features_for_model(208) == tuple(n for n in f.FEATURE_NAMES if n not in f.QUALITY_FEATURES)
    assert set(at50) <= set(f.features_for_model(416))


def test_stillness():
    m = med(make("still", 10))
    assert m["acc_mag_mean"] == pytest.approx(1.0, abs=0.01)
    assert m["dyn_acc_rms"] < 0.02
    assert m["signal_energy"] < 1e-3
    assert m["completeness"] == 1.0 and m["saturated"] == 0.0


def test_walking_is_low_frequency():
    m = med(make("walking", 12))
    assert 0.5 <= m["dom_freq_1"] <= 3.0
    others = sum(m[f"band_power_{lo:g}_{hi:g}"] for lo, hi in f.ANALYSIS_BANDS_HZ[1:])
    assert m["band_power_0.5_3"] > 5 * others
    assert m["high_freq_ratio"] < 0.2


def test_tool_like_is_high_frequency():
    m = med(make("tool", 12))
    assert m["dom_freq_1"] > 8.0
    assert m["high_freq_ratio"] > 0.8
    assert m["band_power_8_15"] + m["band_power_15_25"] > 10 * m["band_power_0.5_3"]
    assert m["dyn_acc_rms"] > 10 * med(make("still", 12))["dyn_acc_rms"]


def test_rate_independence_50_vs_200():
    a, b = med(tones(12, 50)), med(tones(12, 200))
    skip = set(f.QUALITY_FEATURES) | {"band_power_25_50", "band_power_50_100", "dom_freq_1", "dom_freq_2"}
    for name in f.FEATURE_NAMES:
        if name not in skip:
            assert a[name] == pytest.approx(b[name], rel=0.2, abs=2e-3), name
    assert abs(a["dom_freq_1"] - b["dom_freq_1"]) <= 0.7
    assert abs(a["dom_freq_2"] - b["dom_freq_2"]) <= 0.7
    assert a["band_power_25_50"] == 0.0 and a["band_power_50_100"] == 0.0   # above Nyquist at 50 Hz
    # band power is in g^2: the 12 Hz tone of amplitude 0.2 g carries 0.02 g^2
    assert b["band_power_8_15"] == pytest.approx(0.02, rel=0.2)
    # and the synthetic helper's kinds agree across rates on the headline features
    for kind in ("walking", "tool"):
        x, y = med(make(kind, 12, 50)), med(make(kind, 12, 200))
        assert abs(x["dom_freq_1"] - y["dom_freq_1"]) <= 0.7
        assert x["dyn_acc_rms"] == pytest.approx(y["dyn_acc_rms"], rel=0.2)
        assert x["high_freq_ratio"] == pytest.approx(y["high_freq_ratio"], abs=0.05)


@pytest.mark.parametrize("hz", [50, 104, 208, 416])
def test_window_count_matches_duration(hz):
    arr = make("walking", 10, hz)
    windows = f.extract_windows(arr, window_s=1.5, overlap=0.5)
    assert len(windows) == 12                       # floor((10 - 1.5) / 0.75) + 1
    assert len(f.extract_windows(arr, window_s=2.0, overlap=0.0)) == 5
    for w in windows:
        assert w.sample_hz == pytest.approx(hz, rel=1e-3)
        assert w.n_samples == pytest.approx(1.5 * hz, abs=1)
        assert w.completeness == pytest.approx(1.0, abs=0.01)
        assert w.end_time - w.start_time == pytest.approx(1.5, abs=2 / hz)
    assert np.diff([w.start_time for w in windows]) == pytest.approx(0.75, abs=2 / hz)


def test_gap_lowers_completeness_and_invents_nothing():
    full = make("tool", 12)
    arr = drop(full, 4.0, 7.0)
    windows = f.extract_windows(arr)
    assert f.measure_rate(arr) == pytest.approx(50, rel=1e-3)
    assert min(w.completeness for w in windows) < 0.75
    assert max(w.completeness for w in windows) == 1.0
    times = arr[:, 2]
    for w in windows:
        inside = int(((times >= w.start_time) & (times <= w.end_time)).sum())
        assert w.n_samples == inside <= 76          # only samples that exist
        assert w.completeness == pytest.approx(min(1.0, w.n_samples / 75), abs=0.02)
        assert not (T0 + 4.0 <= w.start_time < T0 + 7.0)   # never starts inside the gap
    # windows lying wholly inside the gap are skipped, not filled
    assert len(windows) < len(f.extract_windows(full))


def test_lost_samples_seen_through_n_only():
    arr = make("walking", 6)
    keep = np.ones(len(arr), bool)
    keep[100:110] = False
    arr = arr[keep]
    arr[:, 2] = T0 + np.arange(len(arr)) / 50.0     # time looks contiguous, n still jumps
    assert min(w.completeness for w in f.extract_windows(arr, hz=50)) < 0.95


def test_saturation_flag():
    arr = make("walking", 6)
    assert not any(w.saturated for w in f.extract_windows(arr))
    clipped = arr.copy()
    clipped[150:160, 3] = f.ACCEL_SATURATION_G
    windows = f.extract_windows(clipped)
    assert any(w.saturated for w in windows) and not all(w.saturated for w in windows)
    assert [w.as_dict()["saturated"] for w in windows] == [float(w.saturated) for w in windows]
    gyro = arr.copy()
    gyro[10, 7] = -f.GYRO_SATURATION_DPS
    assert f.extract_windows(gyro)[0].saturated
    assert all(w.saturated for w in f.extract_windows(arr, accel_sat_g=0.5))     # per-call override
    assert not any(w.saturated for w in f.extract_windows(clipped, accel_sat_g=16))


def test_measure_rate_with_lost_samples_and_jitter():
    rng = np.random.default_rng(1)
    for hz in (50, 104, 208, 416):
        arr = make("walking", 10, hz)
        assert f.measure_rate(arr) == pytest.approx(hz, rel=1e-3)
        lossy = arr[rng.random(len(arr)) > 0.3]                     # 30 % lost at random
        assert f.measure_rate(lossy) == pytest.approx(hz, rel=0.02)
        jit = arr.copy()                                            # BLE-like bursts: late delivery
        jit[:, 2] = np.sort(jit[:, 2] + rng.uniform(0, 0.02, len(jit)))
        assert f.measure_rate(jit) == pytest.approx(hz, rel=0.02)
    arr = make("walking", 10, 50)
    assert f.measure_rate(drop(arr, 3, 6)) == pytest.approx(50, rel=0.01)
    restarted = np.vstack([arr[:250], arr[250:]])
    restarted[250:, 0] -= 250                                       # n restarts at 0 (reconnect)
    restarted[250:, 2] += 5.0
    assert f.measure_rate(restarted) == pytest.approx(50, rel=0.01)


def test_measure_rate_fallbacks():
    arr = make("walking", 10, 104)
    no_n = arr.copy()
    no_n[:, 0] = np.nan
    assert f.measure_rate(no_n) == pytest.approx(104, rel=0.01)     # 1 / median(diff(time))
    assert f.measure_rate(drop(no_n, 3, 6)) == pytest.approx(104, rel=0.01)
    no_time = arr.copy()
    no_time[:, 2] = np.nan
    assert f.measure_rate(no_time) == pytest.approx(104, rel=0.01)  # falls back to t (seconds)
    t_ms = no_time.copy()
    t_ms[:, 1] *= 1000.0                                            # the kit's own t is in ms
    assert f.measure_rate(t_ms) == pytest.approx(104, rel=0.01)
    assert f.measure_rate(arr[:1]) == 0.0 and f.measure_rate(np.empty((0, 9))) == 0.0


def test_latest_window():
    arr = make("tool", 4)
    w = f.latest_window(arr, window_s=1.5)
    assert w is not None
    assert w.end_time == pytest.approx(arr[-1, 2])
    assert w.end_time - w.start_time == pytest.approx(1.5, abs=0.05)
    assert w.n_samples == 75 and w.completeness == 1.0
    assert w.as_dict()["dom_freq_1"] > 8
    assert f.latest_window(arr[:30]) is None                        # 0.6 s < 60 % of 1.5 s
    assert f.latest_window(arr[:50]) is not None                    # 1.0 s: allowed, incomplete
    assert f.latest_window(arr[:50]).completeness == pytest.approx(50 / 75, abs=0.02)
    assert f.latest_window(np.empty((0, 9))) is None
    gappy = drop(arr, 2.8, 3.6)                                     # hole inside the last window
    w = f.latest_window(gappy)
    assert w is None or w.completeness < 0.75
    assert f.latest_window(drop(arr, 3.0, 3.3)).completeness == pytest.approx(60 / 75, abs=0.03)


def test_gravity_estimate():
    arr = make("walking", 8)
    g = f.estimate_gravity(arr[:, 3:6], 50)
    assert g.shape == (len(arr), 3)
    assert np.abs(g[:, 2] - 1.0).max() < 0.12 and np.abs(g[:, 0]).max() < 0.12
    short = f.estimate_gravity(arr[:5, 3:6], 50)                    # too short: mean fallback
    assert short == pytest.approx(np.tile(arr[:5, 3:6].mean(axis=0), (5, 1)))
    assert f.estimate_gravity(np.empty((0, 3)), 50).shape == (0, 3)


def test_samples_to_array_and_feature_matrix():
    arr = make("gesture", 4)
    samples = [ImuSample(int(r[0]), *map(float, r[1:])) for r in arr]
    assert np.array_equal(f.samples_to_array(samples), arr)
    samples[0].n = None
    assert np.isnan(f.samples_to_array(samples)[0, 0])
    assert f.samples_to_array([]).shape == (0, 9)
    windows = f.extract_windows(arr)
    full = f.feature_matrix(windows)
    assert full.shape == (len(windows), len(f.FEATURE_NAMES))
    picked = f.feature_matrix(windows, ("completeness", "dyn_acc_rms"))
    assert np.array_equal(picked[:, 1], full[:, f.FEATURE_NAMES.index("dyn_acc_rms")])
    assert np.array_equal(picked[:, 0], [w.completeness for w in windows])
    assert f.feature_matrix([]).shape == (0, len(f.FEATURE_NAMES))
    assert f.feature_matrix([], f.features_for_model(50)).shape == (0, len(f.features_for_model(50)))


def test_csv_round_trip(tmp_path):
    arr = make("tool", 6, 104)
    folder = write_recording(tmp_path, "s1", "tool_active", arr, hz=104)
    back = f.load_samples_csv(folder / "samples.csv")
    assert back.shape == arr.shape
    assert back == pytest.approx(arr, abs=1e-5)
    assert f.measure_rate(back) == pytest.approx(104, rel=1e-3)
    assert f.feature_matrix(f.extract_windows(back)) == pytest.approx(
        f.feature_matrix(f.extract_windows(arr)), rel=1e-2, abs=1e-4)
    (tmp_path / "blank_n.csv").write_text("n,t,time,ax,ay,az,gx,gy,gz\n,0.0,10.0,0,0,1,0,0,0\n",
                                          encoding="utf-8")
    one = f.load_samples_csv(tmp_path / "blank_n.csv")
    assert one.shape == (1, 9) and np.isnan(one[0, 0]) and one[0, 5] == 1.0
    (tmp_path / "empty.csv").write_text("n,t,time,ax,ay,az,gx,gy,gz\n", encoding="utf-8")
    assert f.load_samples_csv(tmp_path / "empty.csv").shape == (0, 9)


def test_degenerate_inputs_never_give_nan():
    const = make("still", 4)
    const[:, 3:9] = 0.0                                             # exactly zero: no power at all
    zero_time = make("still", 2)
    zero_time[:, 1:3] = 0.0                                         # no usable clock
    nan_rows = make("walking", 4)
    nan_rows[20:25, 3:9] = np.nan
    cases = [const, make("still", 0.5), make("tool", 0.1), make("walking", 3, 10),
             nan_rows, drop(make("tool", 6), 0.5, 5.5), zero_time, make("walking", 4)[::-1]]
    for arr in cases:
        for hz in (None, 50):
            windows = f.extract_windows(arr, hz=hz)
            last = f.latest_window(arr, hz=hz)
            for w in windows + ([last] if last else []):
                assert np.all(np.isfinite(w.values))
                assert 0.0 <= w.completeness <= 1.0
                assert np.isfinite([w.start_time, w.end_time, w.sample_hz]).all()
    assert f.extract_windows(np.empty((0, 9))) == []
    assert f.extract_windows(const[:1]) == []
    assert f.extract_windows(zero_time) == [] and f.latest_window(zero_time) is None
    w = f.extract_windows(const)[0].as_dict()
    assert w["dom_freq_1"] == 0.0 and w["spectral_entropy"] == 0.0 and w["high_freq_ratio"] == 0.0


def test_measure_rate_with_burst_delivered_stamps():
    """Batched delivery: every sample of a batch carries the same (or nearly the same) stamp."""
    for hz, size in ((50, 10), (208, 16)):
        arr = make("walking", 10, hz)
        burst = arr.copy()
        burst[:, 2] = np.repeat(arr[::size, 2], size)[:len(arr)]        # identical stamps
        near = burst.copy()
        near[:, 2] += (np.arange(len(arr)) % size) * 1e-4               # near-identical stamps
        for stamped in (burst, near):
            assert f.measure_rate(stamped) == pytest.approx(hz, rel=0.02)
            assert f.measure_rate(stamped[:4 * hz]) == pytest.approx(hz, rel=0.03)   # live buffer
            assert f.measure_rate(drop(stamped, 3, 6)) == pytest.approx(hz, rel=0.03)
            no_n = stamped.copy()
            no_n[:, 0] = np.nan
            assert f.measure_rate(no_n) == pytest.approx(hz, rel=0.03)
            assert f.measure_rate(drop(no_n, 3, 6)) == pytest.approx(hz, rel=0.05)
            lossy = stamped[np.random.default_rng(3).random(len(arr)) > 0.3]
            assert f.measure_rate(lossy) == pytest.approx(hz, rel=0.03)  # n still sees the loss
    one_burst = make("walking", 0.2, 50)
    one_burst[:, 2] = T0                                                # no elapsed time at all
    assert f.measure_rate(one_burst) == pytest.approx(50, rel=0.1)      # falls back to t


def test_given_hz_is_the_truth_for_expected_samples():
    """Without `n`, heavy loss makes the measured rate (and so completeness) optimistic;
    a caller that knows the rate passes hz and gets the honest completeness."""
    arr = make("walking", 20)
    arr = arr[np.random.default_rng(5).random(len(arr)) > 0.6]          # 60 % lost
    arr[:, 0] = np.nan
    assert f.measure_rate(arr) < 40                                     # cannot be known without n
    windows = f.extract_windows(arr, hz=50)
    assert windows and all(w.sample_hz == 50 for w in windows)
    assert np.mean([w.completeness for w in windows]) == pytest.approx(0.4, abs=0.06)
    assert max(w.completeness for w in windows) < 0.65
    last = f.latest_window(arr, hz=50)
    assert last is None or last.completeness < 0.65
    dense = f.latest_window(make("walking", 4)[::2], hz=50)             # every second sample
    assert dense is None                                                # 50 % < 60 % of a window
    third = make("walking", 4)
    third = third[np.arange(len(third)) % 4 != 0]                       # 75 % present
    third[:, 0] = np.nan
    assert f.latest_window(third, hz=50).completeness == pytest.approx(0.75, abs=0.03)
