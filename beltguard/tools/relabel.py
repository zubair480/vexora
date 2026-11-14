"""Add labels to an existing raw recording after the fact (session S3).

Fallback when no live label runs happened: someone who saw what the wearer did says when, and this
writes a labelled COPY into tools/out/ (data/raw is never modified) for tools/analyze.py.

    python tools/relabel.py data/raw/20261114-143000-browser-ble.jsonl --tool 14:30:45-14:30:54
    python tools/relabel.py data/raw/X.jsonl --gesture 13:57:40-13:57:52 --tool 14:30:45-14:30:54 --rest idle
    python tools/analyze.py tools/out/*-labelled.jsonl

Intervals are local wall-clock HH:MM:SS (from each reading's "time"), or band-clock seconds with --band.
--rest idle labels the remaining quiet stretches (1 s accel RMS under --quiet-mg) as idle.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def parse_span(text: str, band: bool, day_ref: float):
    a, _, b = text.partition("-")
    if band:
        return float(a), float(b)
    lt = time.localtime(day_ref)

    def to_epoch(hms: str) -> float:
        h, m, s = (list(map(float, hms.split(":"))) + [0, 0])[:3]
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, int(h), int(m), 0, 0, 0, -1)) + s
    return to_epoch(a), to_epoch(b)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    for lab in ("tool", "walking", "gesture", "idle"):
        ap.add_argument("--" + lab, action="append", default=[], metavar="START-END")
    ap.add_argument("--band", action="store_true", help="intervals are band-clock seconds, not wall clock")
    ap.add_argument("--rest", choices=("idle", "none"), default="none")
    ap.add_argument("--quiet-mg", type=float, default=15.0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    rows = [json.loads(line) for line in open(a.file, encoding="utf-8") if line.strip()]
    if not rows:
        print("empty file")
        return 2
    key = "t" if a.band else "time"
    if not a.band and not rows[0].get("time"):
        print("this file has no wall-clock 'time'; use --band with band-clock seconds")
        return 2
    spans = []
    for lab in ("tool", "walking", "gesture", "idle"):
        for s in getattr(a, lab):
            lo, hi = parse_span(s, a.band, rows[0].get("time") or 0)
            spans.append((lo, hi, lab))
    # 1 s accel RMS for the quiet test
    quiet = [False] * len(rows)
    if a.rest == "idle":
        j = 0
        for i, r in enumerate(rows):
            while rows[j]["t"] < r["t"] - 1.0:
                j += 1
            seg = rows[j:i + 1]
            if len(seg) > 4:
                mx = [sum(x[k] for x in seg) / len(seg) for k in ("ax", "ay", "az")]
                rms = math.sqrt(sum(sum((x[k] - m) ** 2 for k, m in zip(("ax", "ay", "az"), mx)) for x in seg) / len(seg))
                quiet[i] = rms * 1000 < a.quiet_mg
    counts = {}
    for i, r in enumerate(rows):
        v = r.get(key)
        lab = next((l for lo, hi, l in spans if v is not None and lo <= v <= hi), None)
        if lab is None:
            lab = "idle" if quiet[i] else "none"
        r["label"] = lab
        counts[lab] = counts.get(lab, 0) + 1
    out = a.out or os.path.join(HERE, "out", os.path.basename(a.file).replace(".jsonl", "-labelled.jsonl"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print("wrote", out, counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
