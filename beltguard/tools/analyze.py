"""Learn tool-detector thresholds from labelled band recordings (session S3).

Reads the backend's raw recordings (beltguard/data/raw/*.jsonl, one JSON object per band reading:
t, n, ax, ay, az, gx, gy, gz, rate, label, src), computes the detector's own features on sliding
windows, prints per-label feature distributions, searches thresholds, then replays every recording
through the real ToolDetector and prints a confusion matrix plus tool-session timing errors.

    python tools/analyze.py                      # all real recordings, report only
    python tools/analyze.py --write              # also write backend/detector_config.json
    python tools/analyze.py data/raw/x.jsonl     # specific files
    python tools/analyze.py --include-sim        # include simulator files (never written to the config)
"""
from __future__ import annotations

import argparse
import glob
import itertools
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))
import detector as D  # noqa: E402
sys.path.insert(0, HERE)
import label_session as LS  # noqa: E402

LABELS = ("idle", "walking", "gesture", "tool")
PRED = ("idle", "walking", "gesture", "tool", "unresolved")
FEATS = ("acc_rms", "hf_rms", "hf_ratio", "rot_rms", "gyro_rms", "period", "crest", "zcr_hz", "peak_hz", "hf_peak_hz", "hf_peak_frac")


# ------------------------------------------------------------------ loading
def load(path: str):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if "accel" in d and isinstance(d["accel"], list):        # batched form, just in case
                t0, dt = float(d.get("t0", d.get("t", 0))), float(d.get("dt", 0.02))
                gy = d.get("gyro") or [None] * len(d["accel"])
                for i, a in enumerate(d["accel"]):
                    g = gy[i] if gy and gy[i] is not None else (None, None, None)
                    rows.append({"t": t0 + i * dt, "n": None, "ax": a[0], "ay": a[1], "az": a[2],
                                 "gx": g[0], "gy": g[1], "gz": g[2], "rate": d.get("rate"), "label": d.get("label", "none")})
            elif "ax" in d and "t" in d:
                rows.append(d)
    return rows


def arrays(rows):
    t = np.array([float(r["t"]) for r in rows])
    acc = np.array([[float(r["ax"] or 0), float(r["ay"] or 0), float(r["az"] or 0)] for r in rows])
    gyro = np.array([[np.nan if r.get(k) is None else float(r[k]) for k in ("gx", "gy", "gz")] for r in rows])
    return t, acc, gyro


def runs(rows, max_gap: float):
    """Contiguous stretches with one label and no time gap."""
    out, cur = [], []
    for r in rows:
        if cur and (r.get("label") != cur[-1].get("label") or float(r["t"]) - float(cur[-1]["t"]) > max_gap
                    or float(r["t"]) <= float(cur[-1]["t"]) - 1.0):
            out.append(cur)
            cur = []
        cur.append(r)
    if cur:
        out.append(cur)
    return out


def window_features(run, cfg, trim_lo=1.0, trim_hi=0.5):
    t, acc, gyro = arrays(run)
    if len(t) < 16:
        return []
    feats = []
    te = t[0] + max(cfg["window_s"], trim_lo)
    while te <= t[-1] - trim_hi:
        hi = int(np.searchsorted(t, te, side="right"))
        lo = int(np.searchsorted(t, te - cfg["context_s"], side="left"))
        f = D.compute_features(t[lo:hi], acc[lo:hi], gyro[lo:hi], cfg)
        if f:
            feats.append(f)
        te += cfg["hop_s"]
    return feats


# ------------------------------------------------------------------ stats + search
def pct(xs, q):
    xs = [x for x in xs if x is not None]
    return float(np.percentile(xs, q)) if xs else float("nan")


FEAT_KEYS = ("hf_rms", "hf_ratio", "rot_rms", "period", "crest", "zcr_hz", "hf_peak_frac")


def to_arrays(by_label):
    F, y = {k: [] for k in FEAT_KEYS}, []
    for lab, fl in by_label.items():
        for f in fl:
            for k in FEAT_KEYS:
                v = f.get(k)
                F[k].append(np.nan if v is None else float(v))
            y.append(lab == "tool")
    return {k: np.array(v, dtype=float) for k, v in F.items()}, np.array(y, dtype=bool)


def tool_accuracy(by_label, cfg):
    F, y = to_arrays(by_label)
    if not len(y):
        return float("nan"), float("nan")
    hit = D.tool_scores_vec(F, cfg) >= cfg["tool_on"]
    tpr = float(hit[y].mean()) if y.any() else float("nan")
    tnr = float((~hit[~y]).mean()) if (~y).any() else float("nan")
    return tpr, tnr


def search(by_label, cfg):
    """Grid-search the tool-evidence thresholds. False tool claims cost more than misses."""
    F, y = to_arrays(by_label)
    if not y.any() or y.all():
        return {}, None
    tool_hf, non_hf = F["hf_rms"][y], F["hf_rms"][~y]
    lo = max(1e-4, min(np.percentile(non_hf, 50), np.percentile(tool_hf, 5)) * 0.5)
    hi = max(np.percentile(tool_hf, 60), np.percentile(non_hf, 99)) * 1.5
    hf_grid = np.geomspace(lo, hi, 24)
    # (centre, width): real handling at 416/s ran 0.85-0.88 while a buzz held still is >= 0.97
    ratio_grid = ((0.3, 0.08), (0.45, 0.08), (0.6, 0.08), (0.75, 0.04), (0.85, 0.03), (0.9, 0.02),
                  (0.93, 0.02), (0.96, 0.015))
    rot_grid = (10.0, 20.0, 40.0, 80.0, 140.0)
    tonal_grid = ((0.0, 0.35), (1.0, 0.2), (1.0, 0.35), (1.0, 0.5))
    # ties go to: no tonal gate, then the widest margin (hf_on midway in log scale between the non-tool
    # p95 and the tool p5; the decision point sits ~1.09 x hf_on), then values nearest the defaults
    hf_mid = math.sqrt(max(np.percentile(non_hf, 95), 1e-6) * max(np.percentile(tool_hf, 5), 1e-6)) / 1.09
    scored = []
    for hf_on, (rc, rw), rm, (tw, tc) in itertools.product(hf_grid, ratio_grid, rot_grid, tonal_grid):
        c = dict(cfg, hf_on_g=float(hf_on), hf_ratio_center=rc, hf_ratio_width=rw, rot_max_dps=rm,
                 tonal_weight=tw, tonal_center=tc)
        hit = D.tool_scores_vec(F, c) >= c["tool_on"]
        tpr, tnr = float(hit[y].mean()), float((~hit[~y]).mean())
        # exact score (no tolerance): a setting that lets even a few handling windows through is not a tie
        scored.append((round(0.6 * tnr + 0.4 * tpr, 6), hf_on, rc, rw, rm, tw, tc, tpr, tnr))  # false claims cost more
    top = max(s[0] for s in scored)
    pool = [s for s in scored if s[0] == top]
    # among the equally good settings keep margin on BOTH sides: no tonal gate if possible, then the
    # middle of the feasible ratio and rotation ranges, then hf_on nearest the log-midpoint
    if any(s[5] == 0.0 for s in pool):
        pool = [s for s in pool if s[5] == 0.0]
    rcs = sorted(set(s[2] for s in pool))
    pool = [s for s in pool if s[2] == rcs[len(rcs) // 2]]
    rms = sorted(set(s[4] for s in pool))
    pool = [s for s in pool if s[4] == rms[len(rms) // 2]]
    best_s = min(pool, key=lambda s: (abs(math.log(s[1] / hf_mid)), abs(s[6] - 0.35)))
    _, hf_on, rc, rw, rm, tw, tc, tpr, tnr = best_s
    best = {"hf_on_g": round(float(hf_on), 5), "hf_ratio_center": rc, "hf_ratio_width": rw, "rot_max_dps": rm,
            "tonal_weight": tw, "tonal_center": tc, "_tpr": round(tpr, 3), "_tnr": round(tnr, 3),
            "_feasible_ratio": [rcs[0], rcs[-1]], "_feasible_rot": [rms[0], rms[-1]]}
    return best, top


def lf_thresholds(by_label, cfg):
    """Idle / walking / gesture thresholds from percentiles (rate-independent)."""
    out = {}
    idle = by_label.get("idle", [])
    moving = [f for lab in ("walking", "gesture", "tool") for f in by_label.get(lab, [])]
    if idle and moving:
        a_hi, a_lo = pct([f["acc_rms"] for f in idle], 90), pct([f["acc_rms"] for f in moving], 10)
        out["idle_acc_g"] = round(float(np.clip(math.sqrt(max(a_hi, 1e-4) * max(a_lo, 1e-4)), 0.006, 0.08)), 4)
        gi = [f["gyro_rms"] for f in idle if f["gyro_rms"] is not None]
        gm = [f["gyro_rms"] for f in moving if f["gyro_rms"] is not None]
        if gi and gm:
            out["idle_gyro_dps"] = round(float(np.clip((pct(gi, 90) + pct(gm, 10)) / 2, 4.0, 40.0)), 2)
    walk, gest = by_label.get("walking", []), by_label.get("gesture", [])
    if walk and gest:
        pw, pg = pct([f["period"] for f in walk], 25), pct([f["period"] for f in gest], 75)
        c = float(np.clip((pw + pg) / 2, 0.25, 0.7))
        out["walk_period_center"] = round(c, 3)
        out["period_center"] = round(min(0.8, c + 0.05), 3)
    if walk:
        out["walk_motion_g"] = round(float(np.clip(pct([f["acc_rms"] for f in walk], 10) * 0.7, 0.01, 0.2)), 4)
    return out


# ------------------------------------------------------------------ end-to-end replay
def replay(rows, cfg, rate):
    det = D.ToolDetector(rate_hz=rate, config=cfg)
    preds, events, seen = [], [], 0
    for r in rows:
        s = D.MotionSample(t=float(r["t"]), n=r.get("n"), ax=float(r["ax"] or 0), ay=float(r["ay"] or 0),
                           az=float(r["az"] or 0), gx=r.get("gx"), gy=r.get("gy"), gz=r.get("gz"))
        events += det.update(s)
        if det.evals != seen:
            seen = det.evals
            st = det.snapshot()["state"]
            preds.append((det._last_t, "tool" if st in ("tool_active", "candidate") else st, r.get("label", "none")))
    events += det.finish()
    return preds, events


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--include-sim", action="store_true")
    ap.add_argument("--write", action="store_true", help="write backend/detector_config.json (real data only)")
    ap.add_argument("--out", default=None, help="write the config somewhere else")
    ap.add_argument("--since", type=float, default=None, help="only recordings modified in the last N minutes")
    a = ap.parse_args()

    files = a.files or sorted(glob.glob(os.path.join(ROOT, "data", "raw", "*.jsonl")))
    if not a.include_sim and not a.files:
        files = [f for f in files if not f.endswith("-sim.jsonl")]
    if a.since:
        files = [f for f in files if time.time() - os.path.getmtime(f) <= a.since * 60]
    if not files:
        print("No recordings. Record some first: python tools/label_session.py")
        return 2
    base = D._merge(D.DEFAULT_CONFIG, {})
    data, by_label, rates, sim = [], {lab: [] for lab in LABELS}, {}, False
    by_rate_label = {}
    for path in files:
        rows = load(path)
        if not rows:
            continue
        sim = sim or any(r.get("src") == "sim" for r in rows[:50])
        rate = max(set(r.get("rate") or 50 for r in rows), key=[r.get("rate") or 50 for r in rows].count)
        data.append((path, rows, float(rate)))
        for run in runs(rows, base["max_gap_s"]):
            lab = run[0].get("label", "none")
            if lab in by_label and lab != "idle":
                # the same live checks label_session.py runs: a band lying still is not "walking",
                # and a tool run where no vibration reached the band teaches the wrong thing
                ok, why = LS.verdict(lab, LS.quality([(0.0, float(r["t"]), r["ax"], r["ay"], r["az"]) for r in run]))
                if not ok:
                    print("  SKIPPING %s run %.1f-%.1f in %s: %s" % (lab, float(run[0]["t"]), float(run[-1]["t"]),
                                                                os.path.basename(path), why))
                    for r in run:
                        r["label"] = "invalid"
                    continue
            if lab in by_label:
                feats = window_features(run, base)
                by_label[lab] += feats
                by_rate_label.setdefault(int(rate), {x: [] for x in LABELS})[lab] += feats
                rates.setdefault(int(rate), 0)
                rates[int(rate)] += len(feats)
        print("loaded %-48s %7d readings  rate %s" % (os.path.basename(path), len(rows), rate))
    counts = {lab: len(v) for lab, v in by_label.items()}
    print("\nwindows per label:", counts, " rates:", rates)
    if not any(counts.values()):
        print("No labelled windows (all label 'none'). Use tools/label_session.py while recording.")
        return 2

    print("\nfeature p10 / p50 / p90 per label")
    print("%-9s" % "", "  ".join("%-20s" % k for k in FEATS))
    for lab in LABELS:
        fl = by_label[lab]
        if fl:
            print("%-9s" % lab, "  ".join("%-20s" % ("%.3g/%.3g/%.3g" % (pct([f[k] for f in fl], 10), pct([f[k] for f in fl], 50),
                                                                    pct([f[k] for f in fl], 90))) for k in FEATS))

    tuned = lf_thresholds(by_label, base)
    cfg = D._merge(base, tuned)
    tool_parts = {}
    for r, bl in sorted(by_rate_label.items()):          # tool thresholds are tuned per sample rate
        moving = len(bl["walking"]) + len(bl["gesture"])
        if len(bl["tool"]) < 20 or moving < 20:
            # tool vs resting-band only would push hf_on down to the noise floor and make every
            # movement a "tool": refuse until walking/gesture (handling) runs exist at this rate
            if bl["tool"]:
                print("\n@%s/s: %d tool windows, %d walking+gesture windows -> NOT tuning tool thresholds "
                      "(need >= 20 of each; record gesture/handling runs)" % (r, len(bl["tool"]), moving))
            continue
        part, _ = search(bl, cfg)
        if part:
            idle_hf = [f["hf_rms"] for f in bl["idle"]]
            floor = 3.0 * pct(idle_hf, 90) if idle_hf else 0.0
            if part["hf_on_g"] < floor:                  # never closer than 3x the resting noise
                print("  hf_on %.4f raised to the noise guard %.4f (3 x idle p90)" % (part["hf_on_g"], floor))
                part["hf_on_g"] = round(floor, 5)
            tool_parts[str(r)] = part
            print("\nbest tool thresholds @%s/s: %s" % (r, part))
    if not tool_parts:
        print("\n(no usable tool vs non-tool data yet, tool thresholds unchanged)")
    # replay with what will actually run: the live config's per-rate buckets + the newly tuned ones
    live_by = D.load_config().get("by_rate") or {}
    cfg["by_rate"] = dict(live_by, **{r: {k: v for k, v in p.items() if not k.startswith("_")}
                                      for r, p in tool_parts.items()})
    print("idle/walk/gesture thresholds:", tuned)

    tpr = tnr = float("nan")
    for r, bl in sorted(by_rate_label.items()):
        rc = D._merge(cfg, cfg["by_rate"].get(str(r), {}))
        tpr, tnr = tool_accuracy(bl, rc)
        print("window-level tool detection @%s/s: sensitivity %.2f, specificity %.2f" % (r, tpr, tnr))

    # end-to-end replay with the real state machine
    conf = {lab: {p: 0 for p in PRED} for lab in LABELS}
    false_conf = false_unres = 0
    hit_runs = missed_runs = 0
    print("\nreplay through ToolDetector (tool = candidate or tool_active):")
    for path, rows, rate in data:
        preds, events = replay(rows, cfg, rate)
        for (_, p, lab) in preds:
            if lab in conf and p in conf[lab]:
                conf[lab][p] += 1
        tool_runs = [(float(r[0]["t"]), float(r[-1]["t"])) for r in runs(rows, base["max_gap_s"]) if r[0].get("label") == "tool"]
        for e in events:
            if e["kind"] != "tool_stop":
                continue
            match = [tr for tr in tool_runs if e["t_start"] < tr[1] and e["t_end"] > tr[0]]
            if match:
                tr = match[0]
                print("  %s session %.1f-%.1f (%.1fs, %s %.2f) vs labelled %.1f-%.1f: start %+.2fs end %+.2fs"
                      % (os.path.basename(path)[:22], e["t_start"], e["t_end"], e["trigger_s"], e["status"], e["confidence"],
                         tr[0], tr[1], e["t_start"] - tr[0], e["t_end"] - tr[1]))
            else:
                false_conf += e["status"] == "confirmed"
                false_unres += e["status"] != "confirmed"
                print("  %s FALSE session %.1f-%.1f (%.1fs, %s %.2f) outside any tool run"
                      % (os.path.basename(path)[:22], e["t_start"], e["t_end"], e["trigger_s"], e["status"], e["confidence"]))
        missed = [tr for tr in tool_runs if not any(e["kind"] == "tool_stop" and e["t_start"] < tr[1] and e["t_end"] > tr[0] for e in events)]
        missed_runs += len(missed)
        hit_runs += len(tool_runs) - len(missed)
        for tr in missed:
            print("  %s MISSED tool run %.1f-%.1f" % (os.path.basename(path)[:22], tr[0], tr[1]))

    print("\nfalse tool sessions outside labelled tool runs: %d confirmed, %d unresolved" % (false_conf, false_unres))
    print("\nconfusion (rows = label, cols = detector state)")
    print("%-9s" % "", " ".join("%10s" % p for p in PRED), "   n")
    for lab in LABELS:
        tot = sum(conf[lab].values())
        if tot:
            print("%-9s" % lab, " ".join("%9.0f%%" % (100.0 * conf[lab][p] / tot) for p in PRED), "%5d" % tot)

    if a.write or a.out:
        out = a.out or D.CONFIG_PATH
        if sim and not a.out:
            out = os.path.join(HERE, "out", "detector_config.sim.json")
            print("\nSimulated data: NOT writing the real config; writing", out)
        existing = {}
        try:
            with open(out, encoding="utf-8") as fh:
                existing = json.load(fh)
        except (OSError, ValueError):
            pass
        by_rate = dict(existing.get("by_rate") or {})
        # write gate: the live demo keeps its current tool thresholds unless the new ones, replayed on
        # every recording, make no false CONFIRMED session and catch at least one valid tool run
        if tool_parts and (false_conf > 0 or hit_runs == 0):
            print("\nWRITE GATE: new tool thresholds NOT written (false confirmed %d, tool runs caught %d of %d);"
                  " keeping the existing by_rate" % (false_conf, hit_runs, hit_runs + missed_runs))
            tool_parts = {}
        elif tool_parts:
            print("\nWRITE GATE: passed (0 false confirmed, caught %d of %d tool runs)" % (hit_runs, hit_runs + missed_runs))
        for r, p in tool_parts.items():
            by_rate[r] = {k: v for k, v in p.items() if not k.startswith("_")}
        new = {k: v for k, v in existing.items() if k not in ("by_rate", "_meta")}
        new.update(tuned)
        new["by_rate"] = by_rate
        new["_meta"] = {"written": time.strftime("%Y-%m-%d %H:%M:%S"), "files": [os.path.basename(p) for p, _, _ in data],
                        "windows": counts, "rates": rates, "simulated": sim,
                        "window_tool_sensitivity": round(tpr, 3), "window_tool_specificity": round(tnr, 3)}
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(new, fh, indent=2)
        print("\nwrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
