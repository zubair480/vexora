"""Live check: does the tool's vibration reach band 07? (session S3)

Reads the backend's live motion stream (WebSocket /ws, no second Bluetooth link) and prints a meter
twice a second: high-frequency vibration level, share above 100 Hz, dominant frequency, and whether the
band is moving at all. The first seconds are the baseline; "BUZZ SEEN" means the HF level is well above it.

    python tools/buzz_check.py                    # band ON the wrist, then buzz the phone in that palm
    python tools/buzz_check.py --seconds 40
    python tools/buzz_check.py --file data/raw/x.jsonl --fast    # replay a recording (testing)

Ask for the fast rate first if needed (the backend uses 416/s when a tool is selected).
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import math
import sys
import time

import numpy as np


class Meter:
    def __init__(self, baseline_s: float = 3.0):
        self.buf = collections.deque()          # (t, ax, ay, az)
        self.baseline_s = baseline_s
        self.base = []                          # baseline HF levels (g)
        self.t_first = None
        self.last_g = None

    def add(self, t: float, a) -> None:
        if self.t_first is None:
            self.t_first = t
        self.buf.append((t, a[0], a[1], a[2]))
        while self.buf and self.buf[0][0] < t - 1.0:
            self.buf.popleft()

    def read(self):
        if len(self.buf) < 16:
            return None
        arr = np.array(self.buf)
        t, acc = arr[:, 0], arr[:, 1:4]
        dur = t[-1] - t[0]
        if dur <= 0.3:
            return None
        fs = (len(t) - 1) / dur
        g = acc.mean(axis=0)
        ad = acc - g
        rms = math.sqrt(float((ad ** 2).sum(axis=1).mean()))
        win = np.hanning(len(ad))
        pw = (np.abs(np.fft.rfft(ad * win[:, None], axis=0)) ** 2).sum(axis=1)
        f = np.fft.rfftfreq(len(ad), 1.0 / fs)
        scale = 2.0 / (len(ad) * float((win ** 2).sum()))
        tot = float(pw[f >= 0.5].sum())
        hf = math.sqrt(float(pw[f >= 8.0].sum()) * scale)
        hi_share = float(pw[f >= 100.0].sum()) / tot if tot > 0 else 0.0
        k = int(np.argmax(np.where(f >= 8.0, pw, -1.0)))
        tilt = float(np.linalg.norm(g - self.last_g)) if self.last_g is not None else 0.0
        self.last_g = g
        if t[-1] - self.t_first <= self.baseline_s:
            self.base.append(hf)
        return {"fs": fs, "rms_mg": rms * 1000, "hf_mg": hf * 1000, "hi_share": hi_share, "peak_hz": float(f[k]),
                "tilt": tilt, "baseline": (sorted(self.base)[len(self.base) // 2] * 1000) if self.base else None,
                "in_baseline": t[-1] - self.t_first <= self.baseline_s}


STATS = {"yes": 0, "total": 0, "max_hf": 0.0, "base": None}


def judge(m: dict):
    base = m["baseline"] or 0.0
    if m["in_baseline"]:
        return "measuring baseline - keep still, phone OFF", None
    if m["hf_mg"] > max(15.0, 3.0 * base):
        if m["tilt"] < 0.05 and m["rms_mg"] < 400:
            return "BUZZ REACHING BAND: YES (%.0f mg)" % m["hf_mg"], True
        return "BUZZ REACHING BAND: YES? (%.0f mg, arm moving - hold still)" % m["hf_mg"], True
    if m["rms_mg"] < 12 and m["tilt"] < 0.01:
        return "BUZZ REACHING BAND: NO (%.0f mg) - band still: on the wrist?" % m["hf_mg"], False
    return "BUZZ REACHING BAND: NO (%.0f mg)" % m["hf_mg"], False


def show(m: dict, source: str) -> None:
    verdict, yes = judge(m)
    if yes is not None:
        STATS["total"] += 1
        STATS["yes"] += bool(yes)
        STATS["max_hf"] = max(STATS["max_hf"], m["hf_mg"])
        STATS["base"] = m["baseline"]
    bar = "#" * min(30, int(m["hf_mg"] / 5))
    sys.stdout.write("\r%-58s |%-30s| peak %5.1f Hz  >100Hz %3.0f%%  motion %6.1f mg  base %4.1f mg  %s %3.0f/s "
                     % (verdict, bar, m["peak_hz"], 100 * m["hi_share"], m["rms_mg"], m["baseline"] or 0.0,
                        source[:6], m["fs"]))
    sys.stdout.flush()


def summary() -> None:
    n, y = STATS["total"], STATS["yes"]
    if not n:
        print("\nSUMMARY: no readings after the baseline")
        return
    print("\nSUMMARY: buzz reached the band in %d of %d half-second readings (%.0f%%); peak HF %.1f mg vs baseline %.1f mg"
          " -> BUZZ REACHING BAND: %s" % (y, n, 100.0 * y / n, STATS["max_hf"], STATS["base"] or 0.0,
                                         "YES" if y >= 4 else "NO"))


async def live(url: str, seconds: float, meter: Meter) -> int:
    import websockets
    t_end = time.monotonic() + seconds
    last_show = 0.0
    async with websockets.connect(url, max_size=None) as ws:
        print("connected to", url, "- waiting for motion... (Ctrl-C to stop)")
        while time.monotonic() < t_end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            except asyncio.TimeoutError:
                sys.stdout.write("\r(no motion messages: is band 07 connected?)" + " " * 60)
                continue
            msg = json.loads(raw)
            if msg.get("type") != "motion" or not msg.get("accel"):
                continue
            t0, dt = float(msg.get("t0") or 0.0), float(msg.get("dt") or (1.0 / float(msg.get("rate") or 50)))
            for i, a in enumerate(msg["accel"]):
                meter.add(t0 + i * dt, a)
            now = time.monotonic()
            if now - last_show >= 0.5:
                last_show = now
                m = meter.read()
                if m:
                    show(m, msg.get("source", "?"))
    summary()
    return 0


def replay(path: str, fast: bool, meter: Meter) -> int:
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    last = None
    for r in rows:
        meter.add(float(r["t"]), (r["ax"], r["ay"], r["az"]))
        if last is None or r["t"] - last >= 0.5:
            last = r["t"]
            m = meter.read()
            if m:
                show(m, r.get("src", "file"))
                sys.stdout.write("\n" if fast else "")
                if not fast:
                    time.sleep(0.5)
    summary()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", default="ws://127.0.0.1:8765/ws")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--baseline", type=float, default=3.0)
    ap.add_argument("--file")
    ap.add_argument("--fast", action="store_true")
    a = ap.parse_args()
    meter = Meter(a.baseline)
    if a.file:
        return replay(a.file, a.fast, meter)
    try:
        return asyncio.run(live(a.backend, a.seconds, meter))
    except KeyboardInterrupt:
        summary()
        return 0
    except OSError as e:
        print("cannot reach the backend at %s (%s)" % (a.backend, e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
