"""BeltGuard tool-use detector (session S3).

Pure Python + numpy. The detector itself does no I/O; `load_config()` is the only file helper and runs
once, at construction, when no config is passed.

    det = ToolDetector(rate_hz=50.0)          # loads detector_config.json next to this file if present
    for m in readings:                        # one call per band reading, in arrival order
        events = det.update(MotionSample(t=m.t / 1000, n=m.n, ax=m.ax, ay=m.ay, az=m.az,
                                         gx=m.gx, gy=m.gy, gz=m.gz))   # gyro already bias-corrected
    det.snapshot()   # {"state", "confidence", "features", "scores", "session", ...}

What it does, and what it does not do:
- It detects *candidate* power-tool activity from wrist accel + gyro, and times it with the band's own
  sample clock (motion.t). Gaps come from motion.n. It never names a tool: tool identity comes only
  from the technician's selection.
- It does not measure hand-transmitted vibration. Exposure = selected tool-profile magnitude x the
  trigger time estimated here, a screening estimate (see exposure.py).
- Ambiguous evidence is reported as "unresolved", so the UI can ask the technician to confirm.

Robustness: `t` given in milliseconds by mistake is detected and scaled; `n=None` (slow stream) is fine;
gyro `None` (accel-only fast mode) is fine; duplicate or batched timestamps are spread at the nominal
rate; a time gap longer than `max_gap_s` restarts the window, and one longer than `session_gap_end_s`
ends a running tool session at the last good reading.

States: idle | walking | gesture | candidate | tool_active | unresolved
Events: {"kind":"activity","state","confidence"}
        {"kind":"tool_start","t","confidence"}
        {"kind":"tool_stop","t_start","t_end","trigger_s","confidence","status":"confirmed"|"unresolved",...}
"""
from __future__ import annotations

import copy
import json
import math
import os
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

__all__ = ["MotionSample", "ToolDetector", "DEFAULT_CONFIG", "STATES", "load_config",
           "compute_features", "score_features"]

STATES = ("idle", "walking", "gesture", "candidate", "tool_active", "unresolved")


@dataclass
class MotionSample:
    t: float                    # band seconds, from motion.t (ms / 1000)
    n: Optional[int]            # sample counter, from motion.n (None on the slow stream)
    ax: float                   # g
    ay: float
    az: float
    gx: Optional[float] = 0.0   # deg/s, bias-corrected (None in accel-only mode)
    gy: Optional[float] = 0.0
    gz: Optional[float] = 0.0


DEFAULT_CONFIG: dict = {
    # windows (band-clock seconds)
    "window_s": 1.0,            # feature window
    "hop_s": 0.25,              # evaluate every hop
    "context_s": 3.0,           # longer context for walking periodicity
    "buffer_s": 4.0,            # samples kept (edge refinement looks back this far)
    "min_fill": 0.75,           # fraction of a window needed before the first evaluation
    "max_gap_s": 0.5,           # a time gap longer than this restarts the window
    "session_gap_end_s": 2.0,   # a gap longer than this ends a running tool session
    "time_unit": "auto",        # "s" | "ms" | "auto"
    "gyro_bias": [0.0, 0.0, 0.0],  # extra bias to remove (the backend already removes band-07 bias)
    # spectral bands
    "hf_lo_hz": 8.0,
    "hf_hi_hz": 400.0,          # capped at 0.95 x Nyquist
    "rot_hi_hz": 3.0,           # gyro below this = deliberate wrist rotation
    # tool evidence
    "hf_on_g": 0.03,            # high-frequency accel RMS where tool evidence is 50 %
    "hf_width_dec": 0.12,
    "hf_ratio_center": 0.5,     # real band 07 (13:58): handling the band ran 0.5-0.8; buzz is mostly HF
    "hf_ratio_width": 0.08,
    "rot_max_dps": 30.0,        # steady grip: deliberate rotation below this (real handling: 30-56 deg/s)
    "rot_width_dps": 10.0,
    "crest_center": 6.0,        # taps and claps are spiky; tool vibration is not
    "crest_width": 0.8,
    "zcr_center_hz": 6.0,
    "zcr_width_hz": 1.5,
    "tonal_weight": 0.0,        # 0 = off; analyze.py turns it on per rate when real data shows a motor line
    "tonal_center": 0.35,
    "tonal_width": 0.06,
    "period_center": 0.5,
    "period_width": 0.08,
    "lf_gate_g": 0.03,
    "lf_gate_dps": 20.0,
    # walking / gesture / idle
    "walk_period_center": 0.45,
    "walk_motion_g": 0.04,
    "gesture_acc_g": 0.06,
    "gesture_gyro_dps": 45.0,
    "idle_acc_g": 0.025,
    "idle_gyro_dps": 15.0,
    "idle_gyro_width": 5.0,
    # state machine
    "tool_on": 0.55,
    "tool_hold": 0.40,
    "tool_off": 0.35,
    "ambiguous_lo": 0.30,
    "min_tool_s": 2.0,          # sustained this long -> tool_active
    "promote_median": 0.45,     # ...and the median evidence of the candidate's windows at least this
    "gap_merge_s": 1.5,         # pauses shorter than this stay in one session (active_s excludes them)
    "cand_drop_s": 1.5,         # = gap_merge_s, so a pulsed buzz (phone ring: ~1 s on / 1 s off) builds up
    "env_frac": 0.6,            # edge threshold floor = env_frac x hf_on_g
    "edge_frac": 0.4,           # ...raised to edge_frac x this session's own vibration envelope
    "max_backtrack_s": 0.75,
    "confirm_conf": 0.75,       # tool_stop "confirmed" at or above, else "unresolved"
    "debounce_windows": 2,
    # DEMO (Vexora 19:58): hard shaking of the wrist counts as the tool running, so the exposure card climbs in
    # step with the LED stages without a real power tool (same 0.15 g as the LED alert). Still needs 2 s sustained
    # (min_tool_s); short swings, walking and drops do not count. Shown as "demo time" in the UI. 0 = off.
    "demo_shake_g": 0.15,
    # per-rate overrides written by tools/analyze.py: {"50": {...}, "208": {...}}
    "by_rate": {},
}

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "detector_config.json")


def load_config(path: Optional[str] = None) -> dict:
    """Read detector_config.json (keys starting with "_" are metadata). Missing or broken -> {}."""
    try:
        with open(path or CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return {k: v for k, v in data.items() if not k.startswith("_")} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _merge(base: dict, over: Optional[dict]) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if not str(k).startswith("_"):
            out[k] = v
    return out


# ---------------------------------------------------------------- small maths helpers
def _sig(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-min(x, 60.0)))
    z = math.exp(max(x, -60.0))
    return z / (1.0 + z)


def _sig_c(x: float, center: float, width: float) -> float:
    return _sig((x - center) / width)


def _sig_log(x: float, center: float, width_dec: float) -> float:
    return _sig((math.log10(max(x, 1e-9)) - math.log10(max(center, 1e-9))) / width_dec)


def _geo(pairs) -> float:
    tot = sum(w for _, w in pairs)
    return math.exp(sum(w * math.log(max(v, 1e-6)) for v, w in pairs) / tot)


def _movavg(x: np.ndarray, k: int) -> np.ndarray:
    k = int(k)
    if k <= 1 or len(x) < 2:
        return np.array(x, dtype=float, copy=True)
    k = min(k, len(x))
    if x.ndim == 2:
        return np.column_stack([_movavg(x[:, i], k) for i in range(x.shape[1])])
    ker = np.ones(k) / k
    return np.convolve(x, ker, mode="same") / np.convolve(np.ones(len(x)), ker, mode="same")


def _lowpass(x: np.ndarray, fs: float, seconds: float) -> np.ndarray:
    k = max(1, int(round(fs * seconds)))
    return _movavg(_movavg(x, k), k)


def _acf_peak(x: np.ndarray, fs: float, f_lo: float, f_hi: float) -> float:
    """Largest normalised autocorrelation for periods between 1/f_hi and 1/f_lo seconds."""
    n = len(x)
    if n < 8 or fs <= 0:
        return 0.0
    x = x - x.mean()
    lag_lo = max(1, int(math.floor(fs / f_hi)))
    lag_hi = min(int(math.ceil(fs / f_lo)), int(n * 0.65))
    best = 0.0
    for k in range(lag_lo, lag_hi + 1):
        a, b = x[:-k], x[k:]
        den = math.sqrt(float(np.dot(a, a)) * float(np.dot(b, b)))
        if den > 1e-12:
            best = max(best, float(np.dot(a, b)) / den)
    return best


# ---------------------------------------------------------------- features + scores
def compute_features(t: np.ndarray, acc: np.ndarray, gyro: np.ndarray, cfg: dict) -> Optional[dict]:
    """Features of the newest window. t (N,) band seconds ascending; acc (N,3) g; gyro (N,3) deg/s or NaN."""
    t_end = float(t[-1])
    w = t >= t_end - cfg["window_s"]
    tw, aw, gw = t[w], acc[w], gyro[w]
    n = len(tw)
    dur = float(tw[-1] - tw[0]) if n > 1 else 0.0
    if n < 8 or dur <= 0:
        return None
    fs = (n - 1) / dur
    nyq = fs / 2.0
    gyro_ok = not bool(np.isnan(gw).any())

    ad = aw - aw.mean(axis=0)                           # gravity (and slow posture) removed
    acc_rms = math.sqrt(float((ad ** 2).sum(axis=1).mean()))
    dt = np.diff(tw)
    dt = np.where(dt > 0, dt, 1.0 / fs)
    jerk = np.diff(aw, axis=0) / dt[:, None]
    jerk_rms = math.sqrt(float((jerk ** 2).sum(axis=1).mean())) if len(jerk) else 0.0

    win = np.hanning(n)
    spec = np.fft.rfft(ad * win[:, None], axis=0)
    pw = (np.abs(spec) ** 2).sum(axis=1)                # summed over the three axes
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    scale = 2.0 / (n * float((win ** 2).sum()))        # one-sided band power -> mean square (g^2)
    valid = freqs >= 0.5
    total = float(pw[valid].sum())
    hf_hi = min(cfg["hf_hi_hz"], 0.95 * nyq)
    hf_lo = min(cfg["hf_lo_hz"], 0.5 * hf_hi)
    hfb = (freqs >= hf_lo) & (freqs <= hf_hi)
    hf = float(pw[hfb].sum())
    hf_ratio = hf / total if total > 0 else 0.0
    hf_rms = math.sqrt(hf * scale)
    # tonality inside the HF band: a motor buzz is one sharp line, handling/knocks are broadband
    if hf > 0 and hfb.sum() >= 3:
        band = np.where(hfb, pw, 0.0)
        kb = int(np.argmax(band))
        hf_peak_hz = float(freqs[kb])
        hf_peak_frac = float(band[max(kb - 1, 0):kb + 2].sum()) / hf
    else:
        hf_peak_hz, hf_peak_frac = 0.0, 0.0
    if total > 0:
        k = int(np.argmax(np.where(valid, pw, -1.0)))
        peak_hz = float(freqs[k])
        peak_frac = float(pw[max(k - 1, 0):k + 2].sum()) / total
    else:
        peak_hz, peak_frac = 0.0, 0.0

    axis = int(np.argmax((ad ** 2).sum(axis=0)))
    sgn = np.signbit(ad[:, axis])
    zcr_hz = int(np.count_nonzero(sgn[1:] != sgn[:-1])) / (2.0 * dur)

    hp = ad - _lowpass(ad, fs, 0.1)
    hpm = np.sqrt((hp ** 2).sum(axis=1))
    hp_rms = math.sqrt(float((hpm ** 2).mean()))
    crest = float(hpm.max() / hp_rms) if hp_rms > 1e-9 else 0.0

    bias = np.asarray(cfg["gyro_bias"], dtype=float)
    if gyro_ok:
        g = gw - bias
        gyro_rms = math.sqrt(float((g ** 2).sum(axis=1).mean()))
        glp = _lowpass(g, fs, 1.0 / (2.0 * cfg["rot_hi_hz"]))
        rot_rms = math.sqrt(float((glp ** 2).sum(axis=1).mean()))
        gyro_hf_rms = math.sqrt(float(((g - glp) ** 2).sum(axis=1).mean()))
    else:
        gyro_rms = rot_rms = gyro_hf_rms = None

    # walking periodicity over a longer context, on low-passed signals decimated to ~25/s
    period, lf_rms = 0.0, 0.0
    c = t >= t_end - cfg["context_s"]
    tc, ac, gc = t[c], acc[c], gyro[c]
    durc = float(tc[-1] - tc[0]) if len(tc) > 1 else 0.0
    if len(tc) >= 16 and durc >= 1.5:
        fsc = (len(tc) - 1) / durc
        q = max(1, int(fsc // 25))
        fsd = fsc / q
        amag = np.sqrt((ac ** 2).sum(axis=1))
        xm = _lowpass(amag - amag.mean(), fsc, 0.2)[::q]
        acl = _lowpass(ac - ac.mean(axis=0), fsc, 0.2)
        lf_rms = math.sqrt(float((acl ** 2).sum(axis=1).mean()))
        xa = acl[::q, int(np.argmax((acl ** 2).sum(axis=0)))]
        cands = [max(_acf_peak(xm, fsd, 1.4, 2.6), _acf_peak(xm, fsd, 0.7, 1.3)),
                 max(_acf_peak(xa, fsd, 1.4, 2.6), _acf_peak(xa, fsd, 0.7, 1.3))]
        rot_lf = 0.0
        if not bool(np.isnan(gc).any()):
            gl = _lowpass(gc - bias, fsc, 0.2)
            gl = gl - gl.mean(axis=0)
            rot_lf = math.sqrt(float((gl ** 2).sum(axis=1).mean()))
            xg = gl[::q, int(np.argmax((gl ** 2).sum(axis=0)))]
            cands.append(max(_acf_peak(xg, fsd, 0.7, 1.3), _acf_peak(xg, fsd, 1.4, 2.6)))
        cands.sort(reverse=True)
        raw = (cands[0] + cands[1]) / 2.0            # two sensors must agree: chance peaks rarely do
        gate = max(_sig_log(lf_rms, cfg["lf_gate_g"], 0.15), _sig_log(rot_lf, cfg["lf_gate_dps"], 0.15))
        period = raw * gate

    return {"fs": fs, "n": n, "acc_rms": acc_rms, "jerk_rms": jerk_rms, "hf_rms": hf_rms,
            "hf_ratio": hf_ratio, "peak_hz": peak_hz, "peak_frac": peak_frac, "zcr_hz": zcr_hz,
            "hf_peak_hz": hf_peak_hz, "hf_peak_frac": hf_peak_frac,
            "crest": crest, "gyro_rms": gyro_rms, "rot_rms": rot_rms, "gyro_hf_rms": gyro_hf_rms,
            "period": period, "lf_rms": lf_rms, "gyro_ok": gyro_ok}


def score_features(f: dict, cfg: dict) -> dict:
    """0-1 evidence for each activity. tool = high-frequency energy x (mostly HF, steady grip,
    not periodic like steps, not spiky like taps, fast zero crossings)."""
    lowhf = 1.0 - _sig_c(f["hf_ratio"], 0.45, 0.08)
    hf = _sig_log(f["hf_rms"], cfg["hf_on_g"], cfg["hf_width_dec"])
    ratio = _sig_c(f["hf_ratio"], cfg["hf_ratio_center"], cfg["hf_ratio_width"])
    steady = 0.85 if f["rot_rms"] is None else 1.0 - _sig_c(f["rot_rms"], cfg["rot_max_dps"], cfg["rot_width_dps"])
    nonper = 1.0 - 0.8 * _sig_c(f["period"], cfg["period_center"], cfg["period_width"])
    crest = 1.0 - _sig_c(f["crest"], cfg["crest_center"], cfg["crest_width"])
    zcr = _sig_c(f["zcr_hz"], cfg["zcr_center_hz"], cfg["zcr_width_hz"])
    tonal = 1.0 - cfg["tonal_weight"] * (1.0 - _sig_c(f.get("hf_peak_frac", 1.0), cfg["tonal_center"], cfg["tonal_width"]))
    tool = hf * _geo([(ratio, 0.35), (steady, 0.2), (nonper, 0.2), (crest, 0.1), (zcr, 0.15)]) * tonal

    walk = (_sig_c(f["period"], cfg["walk_period_center"], 0.08)
            * _geo([(_sig_log(f["acc_rms"], cfg["walk_motion_g"], 0.15), 0.6), (lowhf, 0.4)]))

    g = f["gyro_rms"] if f["gyro_rms"] is not None else 0.0
    m = max(f["acc_rms"] / cfg["gesture_acc_g"], g / cfg["gesture_gyro_dps"])
    gesture = (_sig(math.log10(max(m, 1e-6)) / 0.12)
               * _geo([(1.0 - _sig_c(f["period"], cfg["walk_period_center"], 0.08), 0.6), (lowhf, 0.4)]))

    qa = 1.0 - _sig_log(f["acc_rms"], cfg["idle_acc_g"], 0.12)
    qg = 1.0 if f["gyro_rms"] is None else 1.0 - _sig_c(g, cfg["idle_gyro_dps"], cfg["idle_gyro_width"])
    idle = qa * qg
    return {"idle": idle, "walking": walk, "gesture": gesture, "tool": tool,
            "f_hf": hf, "f_ratio": ratio, "f_steady": steady, "f_nonper": nonper, "f_crest": crest, "f_zcr": zcr,
            "f_tonal": tonal}


def _sig_v(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60.0, 60.0)))


def tool_scores_vec(F: dict, cfg: dict) -> np.ndarray:
    """score_features(...)["tool"] for many windows at once (tools/analyze.py threshold search).
    F maps feature name -> numpy array; rot_rms is NaN where there was no gyro."""
    hf = _sig_v((np.log10(np.maximum(F["hf_rms"], 1e-9)) - math.log10(max(cfg["hf_on_g"], 1e-9))) / cfg["hf_width_dec"])
    ratio = _sig_v((F["hf_ratio"] - cfg["hf_ratio_center"]) / cfg["hf_ratio_width"])
    rot = F["rot_rms"]
    steady = np.where(np.isnan(rot), 0.85, 1.0 - _sig_v((np.nan_to_num(rot) - cfg["rot_max_dps"]) / cfg["rot_width_dps"]))
    nonper = 1.0 - 0.8 * _sig_v((F["period"] - cfg["period_center"]) / cfg["period_width"])
    crest = 1.0 - _sig_v((F["crest"] - cfg["crest_center"]) / cfg["crest_width"])
    zcr = _sig_v((F["zcr_hz"] - cfg["zcr_center_hz"]) / cfg["zcr_width_hz"])
    lg = lambda v: np.log(np.maximum(v, 1e-6))  # noqa: E731
    geo = np.exp(0.35 * lg(ratio) + 0.2 * lg(steady) + 0.2 * lg(nonper) + 0.1 * lg(crest) + 0.15 * lg(zcr))
    tonal = 1.0 - cfg["tonal_weight"] * (1.0 - _sig_v((F["hf_peak_frac"] - cfg["tonal_center"]) / cfg["tonal_width"]))
    return hf * geo * tonal


def _run_start(t: np.ndarray, env: np.ndarray, thr: float, max_dip_s: float = 0.15) -> Optional[float]:
    """Start time of the newest run where env >= thr (dips shorter than max_dip_s allowed)."""
    above = np.nonzero(env >= thr)[0]
    if len(above) == 0:
        return None
    start = int(above[-1])
    for idx in above[::-1][1:]:
        if t[start] - t[idx] > max_dip_s:
            break
        start = int(idx)
    return float(t[start])


def _last_above(t: np.ndarray, env: np.ndarray, thr: float, since: float) -> Optional[float]:
    idx = np.nonzero((env >= thr) & (t >= since))[0]
    return float(t[idx[-1]]) if len(idx) else None


# ---------------------------------------------------------------- the detector
class ToolDetector:
    def __init__(self, rate_hz: float = 50.0, config: Optional[dict] = None):
        self.rate_hz = float(rate_hz) if rate_hz else 50.0
        self.cfg = _merge(DEFAULT_CONFIG, load_config() if config is None else config)
        self._rate_cfgs: dict = {}
        self.reset()

    # ---- public API
    def reset(self) -> None:
        self._buf: deque = deque()
        self._pending: list = []
        unit = self.cfg.get("time_unit", "auto")
        self._time_scale: Optional[float] = {"s": 1.0, "ms": 0.001}.get(unit)
        self._last_t: Optional[float] = None
        self._last_n: Optional[int] = None
        self._next_eval: Optional[float] = None
        self.lost_total = 0
        self.samples_total = 0
        self.gaps = 0
        self.evals = 0
        self._phase = "none"           # none | candidate | active
        self._sess: Optional[dict] = None
        self._last_active_t: Optional[float] = None
        self._display, self._display_conf = "idle", 0.0
        self._pending_label, self._pending_count = None, 0
        self._features: dict = {}
        self._scores: dict = {}
        self._fs: Optional[float] = None
        self._gyro_ok = True

    def set_rate(self, rate_hz: float) -> None:
        """Call when the backend changes the motion rate: restarts the window, keeps any session."""
        self.rate_hz = float(rate_hz)
        self._buf.clear()
        self._next_eval = None

    def update(self, s: MotionSample) -> list:
        if s is None or s.ax is None or s.ay is None or s.az is None or s.t is None:
            return []
        if self._time_scale is None:              # decide whether t is seconds or milliseconds
            self._pending.append(s)
            self._decide_unit()
            if self._time_scale is None:
                return []
            pend, self._pending = self._pending, []
            out: list = []
            for p in pend:
                out += self._ingest(p)
            return out
        return self._ingest(s)

    def finish(self) -> list:
        """End a running tool session now (disconnect, end of recording)."""
        if self._phase == "active":
            return self._end_session(self._last_active_t or self._last_t or 0.0, reason="flush", forced=True)
        self._phase, self._sess = "none", None
        return []

    def snapshot(self) -> dict:
        feats = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self._features.items()}
        sess = None
        if self._phase != "none" and self._sess is not None:
            sess = {"phase": self._phase, "t_start": round(self._sess["t_start"], 3),
                    "elapsed_s": round(max(0.0, (self._last_active_t or 0.0) - self._sess["t_start"]), 2)}
        return {"state": self._display, "confidence": round(self._display_conf, 3), "features": feats,
                "scores": {k: round(v, 3) for k, v in self._scores.items()}, "session": sess,
                "lost_total": self.lost_total, "samples_total": self.samples_total, "gaps": self.gaps}

    # ---- internals
    def _decide_unit(self) -> None:
        ts = [float(p.t) for p in self._pending]
        dts = [b - a for a, b in zip(ts, ts[1:]) if b > a]
        if dts:
            ratio = sorted(dts)[len(dts) // 2] * self.rate_hz   # ~1 for seconds, ~1000 for ms
            self._time_scale = 0.001 if ratio > 30.0 else 1.0
        elif len(self._pending) >= 10:
            self._time_scale = 1.0

    def _cfg_for(self, fs: float) -> dict:
        by = self.cfg.get("by_rate") or {}
        if not by or not fs:
            return self.cfg
        key = min(by.keys(), key=lambda k: abs(math.log(max(float(k), 1.0) / fs)))
        if abs(math.log(max(float(key), 1.0) / fs)) > math.log(1.5):
            return self.cfg                  # thresholds tuned at another rate do not transfer
        if key not in self._rate_cfgs:
            self._rate_cfgs[key] = _merge(self.cfg, by[key])
        return self._rate_cfgs[key]

    def _ingest(self, s: MotionSample) -> list:
        events: list = []
        cfg = self.cfg
        t = float(s.t) * self._time_scale
        g = tuple(np.nan if v is None else float(v) for v in (s.gx, s.gy, s.gz))
        if s.n is not None:
            n = int(s.n)
            if self._last_n is not None:
                d = (n - self._last_n) & 0xFFFFFFFF
                # the counter restarts when the backend changes rate/sensors: only count a jump as lost
                # readings when the band clock moved far enough to have produced them
                dt_n = t - self._last_t if self._last_t is not None else 0.0
                fs_n = self._fs or self.rate_hz
                if 1 < d <= max(2.0, 1.5 * dt_n * fs_n + 2):
                    self.lost_total += d - 1
            self._last_n = n
        if self._last_t is not None:
            dt = t - self._last_t
            if dt <= 0:
                if dt < -1.0:                        # band clock restarted
                    events += self._end_session(self._last_active_t or self._last_t, "clock_reset", True)
                    self._buf.clear()
                    self._next_eval = None
                else:                                # duplicate / batched timestamp
                    t = self._last_t + 1.0 / self.rate_hz
            elif dt > cfg["max_gap_s"]:
                self.gaps += 1
                if self._phase != "none" and dt > cfg["session_gap_end_s"]:
                    events += self._end_session(self._last_active_t or self._last_t, "data_gap", True)
                self._buf.clear()
                self._next_eval = None
        self._last_t = t
        self.samples_total += 1
        self._buf.append((t, float(s.ax), float(s.ay), float(s.az)) + g)
        lim = t - cfg["buffer_s"]
        while self._buf and self._buf[0][0] < lim:
            self._buf.popleft()
        if self._next_eval is None:
            self._next_eval = t + cfg["window_s"] * cfg["min_fill"]
        if t >= self._next_eval:
            self._next_eval += cfg["hop_s"]
            if self._next_eval <= t:
                self._next_eval = t + cfg["hop_s"]
            events += self._evaluate()
        return events

    def _evaluate(self) -> list:
        arr = np.array(self._buf, dtype=float)
        t, acc, gyro = arr[:, 0], arr[:, 1:4], arr[:, 4:7]
        span = float(t[-1] - t[0])
        fs0 = (len(t) - 1) / span if span > 0 else self.rate_hz
        cfg = self._cfg_for(fs0)
        f = compute_features(t, acc, gyro, cfg)
        if f is None:
            return []
        sc = score_features(f, cfg)
        self.evals += 1
        self._features, self._scores, self._fs, self._gyro_ok = f, sc, f["fs"], f["gyro_ok"]
        t_now = float(t[-1])
        s_tool = sc["tool"]
        demo_on = float(cfg.get("demo_shake_g") or 0.0) > 0 and f["acc_rms"] >= float(cfg.get("demo_shake_g") or 0.0)
        if demo_on:                                   # DEMO: hard shaking = tool running (see demo_shake_g)
            s_tool = sc["tool"] = max(s_tool, 0.9)
        events: list = []

        hp = acc - _lowpass(acc, f["fs"], 0.1)
        env = np.sqrt(_movavg((hp ** 2).sum(axis=1), max(1, int(round(0.2 * f["fs"])))))
        base_thr = cfg["env_frac"] * cfg["hf_on_g"]
        # this tool's own vibration level: the newest 0.4 s (the trigger fires as the tool begins,
        # so older parts of the window may still be walking or handling)
        env_recent = float(np.median(env[t >= t_now - min(0.4, cfg["window_s"])]))

        if self._phase == "none" and s_tool >= cfg["tool_on"]:
            thr = max(base_thr, cfg["edge_frac"] * env_recent)   # edges relative to this tool's own level
            ts = _run_start(t, env, thr)
            floor = t_now - cfg["window_s"] - cfg["max_backtrack_s"]
            ts = max(ts if ts is not None else t_now - cfg["window_s"] / 2.0, floor)
            self._phase = "candidate"
            self._sess = {"t_start": ts, "windows": [], "lost0": self.lost_total, "peaks": [], "hf": [],
                          "env_ref": env_recent}
            self._last_active_t = _last_above(t, env, thr, ts) or t_now

        if self._phase != "none":
            sess = self._sess
            if s_tool >= cfg["tool_on"]:
                sess["env_ref"] = 0.8 * sess["env_ref"] + 0.2 * env_recent
            thr = max(base_thr, cfg["edge_frac"] * sess["env_ref"])
            # the newest 0.4 s buzzing at this session's own level, with mostly high-frequency content,
            # keeps the session alive even when the 1 s window is still mostly pause (pulsed buzz)
            tail = t >= t_now - 0.4
            ad_tail = acc[tail] - acc[tail].mean(axis=0)
            den = float((ad_tail ** 2).sum())
            hp_frac = float((hp[tail] ** 2).sum()) / den if den > 1e-12 else 0.0
            if s_tool >= cfg["tool_off"] or (env_recent >= thr and hp_frac >= 0.5):
                la = _last_above(t, env, thr, sess["t_start"])
                if la is not None and la > self._last_active_t:
                    self._last_active_t = la
            if demo_on:
                self._last_active_t = max(self._last_active_t, t_now)
            in_win = t >= t_now - cfg["window_s"]
            on_frac = float((env[in_win] >= thr).mean()) if in_win.any() else 0.0
            sess["windows"].append((t_now - cfg["window_s"], t_now, s_tool, on_frac))
            if s_tool >= cfg["tool_hold"]:
                sess["peaks"].append(f["peak_hz"])
                sess["hf"].append(f["hf_rms"])
            # time the vibration is actually on (pauses inside the session excluded), sample-exact,
            # on a sharper envelope (0.06 s) so pulse edges are not smeared
            t_prev = sess.get("_t_prev", sess["t_start"])
            seg = (t > t_prev) & (t <= t_now)
            if seg.any() and demo_on:                 # DEMO: the whole hop counts while shaking hard
                sess["active"] = sess.get("active", 0.0) + (t_now - t_prev)
            elif seg.any():
                hp2 = (hp[seg] ** 2).sum(axis=1)
                env_fast = np.sqrt(_movavg(hp2, max(1, int(round(0.06 * f["fs"])))))
                thr_on = max(base_thr, 0.5 * sess["env_ref"])
                sess["active"] = sess.get("active", 0.0) + float((env_fast >= thr_on).mean()) * (t_now - t_prev)
            sess["_t_prev"] = t_now
            quiet = t_now - self._last_active_t
            if self._phase == "candidate":
                full = [w[2] for w in sess["windows"] if w[3] >= 0.8]
                pool = sorted(full if len(full) >= 3 else [w[2] for w in sess["windows"]])
                steady_evidence = pool[len(pool) // 2] >= cfg["promote_median"]   # spikes alone never promote
                if (self._last_active_t - sess["t_start"] >= cfg["min_tool_s"] and s_tool >= cfg["tool_hold"]
                        and steady_evidence):
                    self._phase = "active"
                    events.append({"kind": "tool_start", "t": round(sess["t_start"], 3),
                                   "confidence": round(self._confidence(sess, self._last_active_t), 3)})
                elif quiet > cfg["cand_drop_s"] and s_tool < cfg["tool_hold"]:
                    self._phase, self._sess = "none", None     # short burst: not a tool
            elif quiet > cfg["gap_merge_s"] and s_tool < cfg["tool_off"]:
                events += self._end_session(self._last_active_t, "stopped")

        inst, iconf = self._instant_label(sc, cfg)
        if self._phase == "active":
            disp, dconf = "tool_active", self._confidence(self._sess, self._last_active_t)
        elif self._phase == "candidate":
            disp, dconf = "candidate", s_tool
        else:
            if inst == self._pending_label:
                self._pending_count += 1
            else:
                self._pending_label, self._pending_count = inst, 1
            if (inst == self._display or self._pending_count >= cfg["debounce_windows"]
                    or self._display in ("candidate", "tool_active")):
                disp, dconf = inst, iconf
            else:
                disp, dconf = self._display, self._display_conf
        if disp != self._display:
            events.append({"kind": "activity", "state": disp, "confidence": round(dconf, 3)})
        self._display, self._display_conf = disp, dconf
        return events

    def _instant_label(self, sc: dict, cfg: dict):
        others = {"idle": sc["idle"], "walking": sc["walking"], "gesture": sc["gesture"]}
        ranked = sorted(others.items(), key=lambda kv: kv[1], reverse=True)
        (best, bs), (_, second) = ranked[0], ranked[1]
        if sc["tool"] >= cfg["ambiguous_lo"] and sc["tool"] >= bs - 0.15:
            return "unresolved", max(sc["tool"], bs)       # some tool evidence, not enough to call it
        if bs < 0.40 or (bs - second < 0.08 and bs < 0.55):
            return "unresolved", bs
        return best, bs

    def _confidence(self, sess: dict, t_end: float, forced: bool = False) -> float:
        ws = sess["windows"]
        inside = [(s, of) for (a, b, s, of) in ws if a >= sess["t_start"] - 0.05 and b <= t_end + 0.05]
        full = [s for (s, of) in inside if of >= 0.8]         # windows where the vibration is on:
        core = full if len(full) >= 2 else [s for (s, _) in inside]   # pauses are not evidence against
        if len(core) < 2:
            core = [w[2] for w in ws] or [0.0]
        mean_s = sum(core) / len(core)
        amb = sum(1 for s in core if s < self.cfg["tool_on"]) / len(core)
        conf = mean_s * (1.0 - 0.5 * amb)
        dur = max(0.0, t_end - sess["t_start"])
        lost = self.lost_total - sess["lost0"]
        expected = max(1.0, dur * (self._fs or self.rate_hz))
        conf *= 1.0 - min(0.5, 2.0 * lost / (lost + expected))
        if forced:
            conf *= 0.85
        if dur < 3.0:
            conf *= 0.92
        if not self._gyro_ok:
            conf *= 0.95
        return max(0.0, min(1.0, conf))

    def _end_session(self, t_end: float, reason: str, forced: bool = False) -> list:
        sess, was_active = self._sess, self._phase == "active"
        self._phase, self._sess = "none", None
        if sess is None or not was_active:
            return []                                   # candidates never reported
        t_start = sess["t_start"]
        t_end = max(float(t_end), t_start)
        conf = self._confidence(sess, t_end, forced)
        peaks = sorted(sess["peaks"])
        return [{"kind": "tool_stop", "t_start": round(t_start, 3), "t_end": round(t_end, 3),
                 "trigger_s": round(t_end - t_start, 3),
                 "active_s": round(min(t_end - t_start, sess.get("active", 0.0)), 3),
                 "confidence": round(conf, 3),
                 "status": "confirmed" if conf >= self.cfg["confirm_conf"] else "unresolved",
                 "reason": reason, "lost_samples": self.lost_total - sess["lost0"],
                 "peak_hz": round(peaks[len(peaks) // 2], 1) if peaks else None,
                 "hf_rms_g": round(sum(sess["hf"]) / len(sess["hf"]), 4) if sess["hf"] else None}]
