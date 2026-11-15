"""Synthetic tests for detector.py (session S3).  Run:  python backend/test_detector.py   (or pytest)

Signals are generated at the band's real sample times, so a tool's motor frequency aliases exactly as
it would at a low sample rate. Every test passes config={} so it uses the built-in defaults, not the
thresholds tuned on real recordings.
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from detector import MotionSample, ToolDetector, load_config  # noqa: E402

T0, N0 = 100.0, 5000      # the band clock and counter never start at 0


def synth(segments, rate=50.0, actual=None, seed=1, gyro=True, time_ms=False):
    """segments: [(kind, seconds, params)] with kind idle|walk|gesture|tool. Returns samples, truth."""
    rng = np.random.default_rng(seed)
    fs = actual or rate
    out, truth = [], []
    tcur, n0 = T0, N0
    for seg in segments:
        kind, dur = seg[0], seg[1]
        p = seg[2] if len(seg) > 2 else {}
        m = int(round(dur * fs))
        tt = tcur + np.arange(m) / fs
        rel = tt - tcur
        acc = np.zeros((m, 3))
        acc[:, 2] = 1.0
        gy = np.zeros((m, 3))
        acc += rng.normal(0, 0.0025, (m, 3))
        gy += rng.normal(0, 0.07, (m, 3))
        if kind == "walk":
            f = p.get("step_hz", 1.9)
            acc[:, 2] += 0.18 * np.sin(2 * np.pi * f * rel)
            acc[:, 0] += 0.12 * np.sin(2 * np.pi * f / 2 * rel + 0.3)
            for ts in np.arange(0.1, dur, 1.0 / f):          # heel strikes: short damped ringing
                k = rel >= ts
                d = rel[k] - ts
                acc[k, 2] += 0.12 * np.exp(-d / 0.03) * np.sin(2 * np.pi * 15 * d)
            acc += rng.normal(0, 0.01, (m, 3))
            gy[:, 1] += 70 * np.sin(2 * np.pi * f / 2 * rel)
            gy[:, 0] += 15 * np.sin(2 * np.pi * f * rel + 1.0)
            gy += rng.normal(0, 3, (m, 3))
        elif kind == "gesture":
            tb = 0.3
            while tb < dur - 0.4:
                d = rng.uniform(0.6, 1.2)
                amp = rng.uniform(0.25, 0.5)
                fr = rng.uniform(1.0, 2.5)
                k = (rel >= tb) & (rel < tb + d)
                env = np.sin(np.pi * (rel[k] - tb) / d)
                acc[k, rng.integers(0, 3)] += amp * env * np.sin(2 * np.pi * fr * (rel[k] - tb))
                gy[k, rng.integers(0, 3)] += rng.uniform(100, 220) * env * np.sin(2 * np.pi * fr * (rel[k] - tb) + 0.5)
                tb += d + rng.uniform(0.8, 1.8)
        elif kind == "tool":
            a = p.get("amp", 1.0)
            fm = p.get("motor_hz", 137.0)
            ph = rng.uniform(0, 2 * np.pi, 3)
            for i in range(3):                                  # absolute time -> true aliasing
                acc[:, i] += a * 0.25 * np.sin(2 * np.pi * fm * tt + ph[i])
            acc += a * p.get("broadband", 0.12) * rng.normal(0, 1, (m, 3))
            acc[:, 0] += 0.02 * np.sin(2 * np.pi * 0.3 * rel)  # slow hand drift
            gy += a * 12 * rng.normal(0, 1, (m, 3))
            gy[:, 2] += 4 * np.sin(2 * np.pi * 0.2 * rel)
            if p.get("rotate"):                                 # wrist rotating hard while it buzzes
                gy[:, 0] += 150 * np.sin(2 * np.pi * 1.2 * rel)
                acc[:, 1] += 0.3 * np.sin(2 * np.pi * 1.2 * rel)
        for i in range(m):
            g = (float(gy[i, 0]), float(gy[i, 1]), float(gy[i, 2])) if gyro else (None, None, None)
            out.append(MotionSample(t=float(tt[i] * 1000 if time_ms else tt[i]), n=n0 + i,
                                    ax=float(acc[i, 0]), ay=float(acc[i, 1]), az=float(acc[i, 2]),
                                    gx=g[0], gy=g[1], gz=g[2]))
        truth.append((kind, tcur, tcur + m / fs))
        tcur += m / fs
        n0 += m
    return out, truth


def run(samples, rate=50.0, cfg=None):
    det = ToolDetector(rate_hz=rate, config={} if cfg is None else cfg)
    events, states = [], []
    seen = 0
    for s in samples:
        events += det.update(s)
        if det.evals != seen:
            seen = det.evals
            states.append((det._last_t, det.snapshot()["state"]))
    events += det.finish()
    return det, events, states


def frac(states, lo, hi, wanted):
    xs = [st for (t, st) in states if lo <= t <= hi]
    return (sum(1 for st in xs if st in wanted) / len(xs)) if xs else 0.0


def tool_pairs(events):
    starts = [e for e in events if e["kind"] == "tool_start"]
    stops = [e for e in events if e["kind"] == "tool_stop"]
    return starts, stops


# ----------------------------------------------------------------------------------- tests
def test_idle_is_idle():
    smp, _ = synth([("idle", 10)])
    _, ev, st = run(smp)
    assert not tool_pairs(ev)[0], ev
    assert frac(st, T0 + 1, T0 + 10, {"idle"}) > 0.9, st


def test_walking_is_walking():
    smp, _ = synth([("walk", 15)])
    _, ev, st = run(smp)
    assert not tool_pairs(ev)[0], ev
    f = frac(st, T0 + 3, T0 + 15, {"walking"})
    assert f > 0.7, f


def test_gestures_never_make_a_tool():
    smp, _ = synth([("gesture", 14)], seed=3)
    _, ev, st = run(smp)
    assert not tool_pairs(ev)[0], ev
    assert frac(st, T0 + 1, T0 + 14, {"tool_active"}) == 0.0
    assert frac(st, T0 + 1, T0 + 14, {"gesture", "idle"}) > 0.6, st


def test_tool_session_edges_50hz():
    smp, tr = synth([("idle", 4), ("tool", 12), ("idle", 4)])
    t0, t1 = tr[1][1], tr[1][2]
    _, ev, st = run(smp)
    starts, stops = tool_pairs(ev)
    assert len(starts) == 1 and len(stops) == 1, ev
    e = stops[0]
    assert abs(e["t_start"] - t0) < 0.5, (e, t0)
    assert abs(e["t_end"] - t1) < 0.5, (e, t1)
    assert abs(e["trigger_s"] - 12.0) < 0.75, e
    assert e["status"] == "confirmed", e
    assert frac(st, t0 + 2.5, t1 - 0.25, {"tool_active"}) > 0.95


def test_full_shift_two_sessions():
    smp, tr = synth([("walk", 10), ("idle", 2), ("gesture", 8), ("idle", 2), ("tool", 10),
                     ("walk", 8), ("idle", 2), ("tool", 5), ("idle", 3)], seed=5)
    _, ev, _ = run(smp)
    starts, stops = tool_pairs(ev)
    assert len(starts) == 2 and len(stops) == 2, [e for e in ev if e["kind"] != "activity"]
    for e, seg in zip(stops, (tr[4], tr[7])):
        assert abs(e["t_start"] - seg[1]) < 0.5, (e, seg)
        assert abs(e["t_end"] - seg[2]) < 0.5, (e, seg)


def test_short_burst_is_not_a_tool():
    smp, _ = synth([("idle", 4), ("tool", 1.2), ("idle", 4)])
    _, ev, _ = run(smp)
    assert not tool_pairs(ev)[0], ev


def test_short_pause_merges():
    smp, tr = synth([("idle", 3), ("tool", 6), ("idle", 0.5), ("tool", 6), ("idle", 4)])
    _, ev, _ = run(smp)
    starts, stops = tool_pairs(ev)
    assert len(stops) == 1, [e for e in ev if e["kind"] != "activity"]
    assert abs(stops[0]["t_start"] - tr[1][1]) < 0.5 and abs(stops[0]["t_end"] - tr[3][2]) < 0.5, stops


def test_long_pause_splits():
    smp, _ = synth([("idle", 3), ("tool", 5), ("idle", 3), ("tool", 5), ("idle", 4)])
    _, ev, _ = run(smp)
    assert len(tool_pairs(ev)[1]) == 2, [e for e in ev if e["kind"] != "activity"]


def test_unresolved_on_weak_buzz():
    # a weak buzz right at the evidence threshold: real but not convincing -> ask, don't claim
    smp, tr = synth([("idle", 3), ("tool", 12, {"amp": 0.085}), ("idle", 3)], seed=7)
    _, ev, st = run(smp)
    stops = tool_pairs(ev)[1]
    assert all(e["status"] != "confirmed" for e in stops), stops
    unresolved = frac(st, tr[1][1] + 1, tr[1][2], {"unresolved"})
    assert unresolved > 0.3 or any(e["status"] == "unresolved" for e in stops), (unresolved, stops)


def test_buzz_with_hard_wrist_rotation_is_never_confirmed():
    smp, tr = synth([("idle", 3), ("tool", 12, {"amp": 0.11, "rotate": True}), ("idle", 3)], seed=7)
    _, ev, _ = run(smp)
    assert all(e["status"] != "confirmed" for e in tool_pairs(ev)[1]), tool_pairs(ev)


def test_high_rate_416_finds_motor_peak():
    smp, tr = synth([("idle", 3), ("tool", 8, {"motor_hz": 137.0, "broadband": 0.04}), ("idle", 3)],
                    rate=416.0, actual=423.0)
    det, ev, _ = run(smp, rate=416.0)
    stops = tool_pairs(ev)[1]
    assert len(stops) == 1 and stops[0]["status"] == "confirmed", stops
    assert abs(stops[0]["peak_hz"] - 137.0) < 3.0, stops
    assert abs(stops[0]["t_start"] - tr[1][1]) < 0.5 and abs(stops[0]["t_end"] - tr[1][2]) < 0.5, stops


def test_phone_buzz_at_416():
    # DEMO stand-in: a phone on vibrate (~175 Hz motor, small amplitude) held in the palm
    smp, tr = synth([("idle", 3), ("tool", 10, {"motor_hz": 175.0, "amp": 0.35, "broadband": 0.03}),
                     ("idle", 3)], rate=416.0, actual=423.0, seed=11)
    _, ev, _ = run(smp, rate=416.0)
    stops = tool_pairs(ev)[1]
    assert len(stops) == 1, [e for e in ev if e["kind"] != "activity"]
    assert abs(stops[0]["trigger_s"] - 10.0) < 0.75, stops


def test_208hz_while_walking_then_tool():
    smp, tr = synth([("walk", 8), ("tool", 8), ("walk", 6)], rate=208.0, actual=211.0, seed=13)
    _, ev, st = run(smp, rate=208.0)
    stops = tool_pairs(ev)[1]
    assert len(stops) == 1, [e for e in ev if e["kind"] != "activity"]
    assert abs(stops[0]["t_start"] - tr[1][1]) < 0.5 and abs(stops[0]["t_end"] - tr[1][2]) < 0.5, stops
    assert frac(st, tr[0][1] + 3, tr[0][2] - 0.5, {"walking"}) > 0.6


def test_lost_samples_and_gap_end_session():
    smp, tr = synth([("idle", 3), ("tool", 10), ("idle", 3)])
    for s in smp[400:]:
        s.n += 7                                            # 7 readings lost on the way
    cut_lo = T0 + 3 + 5.0                                   # 3 s hole in the middle of the tool use
    smp = [s for s in smp if not (cut_lo <= s.t < cut_lo + 3.0)]
    det, ev, _ = run(smp)
    stops = tool_pairs(ev)[1]
    assert det.lost_total >= 7
    assert stops and stops[0]["reason"] == "data_gap", stops
    assert abs(stops[0]["t_end"] - cut_lo) < 0.5, stops


def test_pulsed_phone_ring_is_one_session():
    # DEMO stand-in rung on vibrate: ~0.9 s buzz / 0.9 s pause, at 416/s
    segs = [("idle", 3)]
    for _ in range(7):
        segs += [("tool", 0.9, {"motor_hz": 175.0, "amp": 0.35, "broadband": 0.03}), ("idle", 0.9)]
    segs += [("idle", 3)]
    smp, tr = synth(segs, rate=416.0, actual=423.0, seed=17)
    _, ev, _ = run(smp, rate=416.0)
    stops = tool_pairs(ev)[1]
    assert len(stops) == 1, [e for e in ev if e["kind"] != "activity"]
    first, last = tr[1][1], tr[-3][2]
    assert abs(stops[0]["t_start"] - first) < 0.5 and abs(stops[0]["t_end"] - last) < 0.5, (stops, first, last)
    assert abs(stops[0]["active_s"] - 7 * 0.9) < 1.5, stops                 # buzz-on time, pauses excluded


def test_counter_restart_is_not_lost_samples():
    smp, _ = synth([("idle", 4), ("tool", 4), ("idle", 3)], rate=208.0, actual=211.0)
    for s in smp[1000:]:
        s.n -= 990                                                          # rate change: n starts again
    det, ev, _ = run(smp, rate=208.0)
    assert det.lost_total == 0, det.lost_total
    assert len(tool_pairs(ev)[1]) == 1


def test_millisecond_timestamps_are_detected():
    smp, tr = synth([("idle", 3), ("tool", 6), ("idle", 3)], time_ms=True)
    _, ev, _ = run(smp)
    stops = tool_pairs(ev)[1]
    assert len(stops) == 1 and abs(stops[0]["t_start"] - tr[1][1]) < 0.5, stops


def test_accel_only_stream():
    smp, tr = synth([("idle", 3), ("tool", 6), ("idle", 3)], gyro=False)
    _, ev, _ = run(smp)
    stops = tool_pairs(ev)[1]
    assert len(stops) == 1 and abs(stops[0]["trigger_s"] - 6.0) < 0.75, stops


def test_duplicate_timestamps_and_none_counter():
    smp, _ = synth([("idle", 3), ("tool", 6), ("idle", 3)])
    for i in range(1, len(smp), 2):                         # batched delivery: pairs share a time
        smp[i].t = smp[i - 1].t
    for s in smp:
        s.n = None
    _, ev, _ = run(smp)
    assert len(tool_pairs(ev)[1]) == 1


def test_snapshot_shape_and_speed():
    smp, _ = synth([("walk", 5), ("tool", 5)], rate=416.0, actual=423.0)
    per_sample_us = float("inf")
    for _ in range(3):                                      # best of 3: the laptop is shared with other jobs
        det = ToolDetector(rate_hz=416.0, config={})
        t = time.perf_counter()
        for s in smp:
            det.update(s)
        per_sample_us = min(per_sample_us, (time.perf_counter() - t) / len(smp) * 1e6)
    snap = det.snapshot()
    assert set(("state", "confidence", "features")) <= set(snap)
    assert snap["state"] in ("idle", "walking", "gesture", "candidate", "tool_active", "unresolved")
    assert {"acc_rms", "gyro_rms", "hf_ratio"} <= set(snap["features"])
    assert per_sample_us < 400, per_sample_us              # 416/s needs < 2400 us; keep a big margin


def test_tuned_config_loads():
    cfg = load_config()
    det = ToolDetector(rate_hz=50.0)                        # default path: detector_config.json if present
    assert isinstance(cfg, dict) and det.cfg["window_s"] > 0


if __name__ == "__main__":
    failed = 0
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        t = time.perf_counter()
        try:
            fn()
            print("PASS %-42s %5.2fs" % (name, time.perf_counter() - t))
        except AssertionError as e:
            failed += 1
            msg = str(e)
            print("FAIL %-42s %s" % (name, (msg[:600] + "...") if len(msg) > 600 else msg))
    print("%d/%d passed" % (len(tests) - failed, len(tests)))
    sys.exit(1 if failed else 0)
