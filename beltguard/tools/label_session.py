"""Record labelled motion runs for tuning the tool detector (session S3).

The backend owns the band. This script only tells it which label to stamp on the raw recording
(POST /api/label), with a countdown, so tools/analyze.py can learn real thresholds from band 07.
While each run records it also watches the live motion stream (WebSocket /ws) and checks the run:
a walking/gesture run where the band lies still, or a tool run where no vibration reaches the band,
is called out and repeated once automatically.

    python tools/label_session.py                              # walking 20, gesture 20, tool 25, idle 15, tool 25
    python tools/label_session.py --plan tool:20,idle:10 --repeat 2
    python tools/label_session.py --rate 416                   # ask the backend for 416/s first
    python tools/label_session.py --dry-run                    # print what it would send

Safety: the "tool" run means a SAFE stand-in (e.g. a phone on vibrate held in the palm) or a real tool
in an authorized setup. Never improvise with a dangerous tool for a recording.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import math
import sys
import threading
import time
import urllib.error
import urllib.request

import numpy as np

LABELS = ("walking", "gesture", "tool", "idle")
HOW = {
    "walking": "Band ON your wrist. Walk around normally, arms swinging. Turn at the ends.",
    "gesture": "Band ON your wrist. Talk with your hands: point, wave, pick things up, adjust the band, tap the table.",
    "tool": "Band ON your wrist. Press the BUZZING phone into the palm of that hand and hold it as you would a tool.",
    "idle": "Band ON your wrist. Stand or sit still, arm relaxed. Small natural movements are fine.",
}


def call(base: str, path: str, body: dict | None = None, dry: bool = False):
    if dry:
        print("   [dry-run] %s %s %s" % ("POST" if body is not None else "GET", path, json.dumps(body) if body else ""))
        return {}
    req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


class Stream(threading.Thread):
    """Collects live motion from the backend WebSocket: (monotonic arrival, band t, ax, ay, az)."""

    def __init__(self, url: str):
        super().__init__(daemon=True)
        self.url = url
        self.samples: collections.deque = collections.deque(maxlen=900 * 90)
        self.connected = False

    def run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        import websockets
        while True:
            try:
                async with websockets.connect(self.url, max_size=None) as ws:
                    self.connected = True
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") == "motion" and msg.get("accel"):
                            now = time.monotonic()
                            t0 = float(msg.get("t0") or 0.0)
                            dt = float(msg.get("dt") or 1.0 / float(msg.get("rate") or 50))
                            for i, a in enumerate(msg["accel"]):
                                self.samples.append((now, t0 + i * dt, a[0], a[1], a[2]))
            except Exception:
                self.connected = False
                await asyncio.sleep(1.0)

    def between(self, lo: float, hi: float):
        return [s for s in list(self.samples) if lo <= s[0] <= hi]


def quality(samples) -> dict | None:
    """Per 1 s window: motion RMS, HF (>= 8 Hz) RMS, tilt change. A window is 'still' when the band
    barely moves and its gravity direction does not change (a band lying on a table)."""
    if len(samples) < 50:
        return None
    arr = np.array([s[1:] for s in samples], dtype=float)
    t, acc = arr[:, 0], arr[:, 1:4]
    rms, hf, still, last_g = [], [], [], None
    lo = t[0] + 1.0                                   # skip the first second (getting into position)
    while lo + 1.0 <= t[-1]:
        m = (t >= lo) & (t < lo + 1.0)
        if m.sum() >= 16:
            a = acc[m]
            g = a.mean(axis=0)
            ad = a - g
            r = math.sqrt(float((ad ** 2).sum(axis=1).mean()))
            dur = t[m][-1] - t[m][0]
            fs = (m.sum() - 1) / dur if dur > 0 else 50.0
            win = np.hanning(len(ad))
            pw = (np.abs(np.fft.rfft(ad * win[:, None], axis=0)) ** 2).sum(axis=1)
            f = np.fft.rfftfreq(len(ad), 1.0 / fs)
            h = math.sqrt(float(pw[f >= 8.0].sum()) * 2.0 / (len(ad) * float((win ** 2).sum())))
            tilt = float(np.linalg.norm(g - last_g)) if last_g is not None else 0.0
            last_g = g
            rms.append(r * 1000)
            hf.append(h * 1000)
            still.append(r < 0.015 and tilt < 0.02)
        lo += 1.0
    if not rms:
        return None
    return {"windows": len(rms), "still_frac": sum(still) / len(still),
            "rms_med_mg": sorted(rms)[len(rms) // 2], "hf_med_mg": sorted(hf)[len(hf) // 2]}


def verdict(label: str, q: dict | None):
    if q is None:
        return True, "quality check unavailable (no live motion seen)"
    s = "motion %.0f mg, HF %.0f mg, still %.0f%% of the run" % (q["rms_med_mg"], q["hf_med_mg"], 100 * q["still_frac"])
    if label in ("walking", "gesture") and (q["still_frac"] > (0.5 if label == "walking" else 0.6)
                                           or q["rms_med_mg"] < (40.0 if label == "walking" else 20.0)):
        return False, "BAND LOOKED STILL (%s). It must be ON the wrist and moving." % s
    if label == "tool" and q["hf_med_mg"] < 15.0:
        return False, "NO VIBRATION REACHED THE BAND (%s). Press the buzzing phone into the banded palm." % s
    return True, "ok (%s)" % s


def parse_plan(text: str):
    plan = []
    for part in text.split(","):
        name, _, secs = part.strip().partition(":")
        if name not in LABELS:
            raise SystemExit("unknown label %r (use %s)" % (name, ", ".join(LABELS)))
        plan.append((name, float(secs or 30)))
    return plan


def bar(done: float, total: float, width: int = 30) -> str:
    k = int(width * min(1.0, done / total))
    return "[" + "#" * k + "-" * (width - k) + "] %4.1f / %.0f s" % (done, total)


def record(base: str, name: str, secs: float, a, stream) -> tuple:
    for k in range(a.countdown, 0, -1):
        print("      starting in %d...\a" % k)
        time.sleep(1)
    if a.sim:
        call(base, "/api/sim/scene", {"scene": "walk" if name == "walking" else name}, a.dry_run)
    call(base, "/api/label", {"label": name}, a.dry_run)
    t0 = time.monotonic()
    while (el := time.monotonic() - t0) < secs:
        sys.stdout.write("\r      %s %s " % (name.upper(), bar(el, secs)))
        sys.stdout.flush()
        time.sleep(0.2)
    call(base, "/api/label", {"label": "none"}, a.dry_run)
    print("\r      %s %s done\a" % (name.upper(), bar(secs, secs)))
    if stream is None:
        return True, "quality check off"
    return verdict(name, quality(stream.between(t0, t0 + secs)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", default="http://127.0.0.1:8765")
    ap.add_argument("--plan", default="walking:20,gesture:20,tool:25,idle:15,tool:25")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--rest", type=float, default=5.0, help="seconds between runs (label 'none')")
    ap.add_argument("--countdown", type=int, default=3)
    ap.add_argument("--retries", type=int, default=1, help="automatic redo of a run that fails its check")
    ap.add_argument("--no-check", action="store_true", help="skip the live signal checks")
    ap.add_argument("--rate", type=int, choices=(50, 104, 208, 416, 833), help="POST /api/rate first")
    ap.add_argument("--sensors", default="both", choices=("both", "accel"))
    ap.add_argument("--sim", action="store_true", help="also switch the backend simulator scene (pipeline test only)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    plan = parse_plan(a.plan) * max(1, a.repeat)
    base = a.backend.rstrip("/")

    try:
        state = call(base, "/api/state", dry=a.dry_run)
    except (urllib.error.URLError, OSError) as e:
        print("Backend not reachable at %s (%s). Start it first: python backend/app.py" % (base, e))
        return 2
    conn = (state or {}).get("connection", {})
    print("Backend %s · band %s · state %s · rate %s" % (base, conn.get("band"), conn.get("state"), conn.get("rate")))
    if conn.get("state") == "simulated" and not a.sim:
        print("WARNING: the backend is SIMULATED. Labels on simulated data cannot tune the real detector.")
    if a.rate:
        print("Asking for %d readings/s (%s)..." % (a.rate, a.sensors))
        try:
            print("  ->", call(base, "/api/rate", {"rate": a.rate, "sensors": a.sensors}, a.dry_run))
        except urllib.error.HTTPError as e:
            print("  rate refused (%s): %s" % (e.code, e.read()[:200]))
    stream = None
    if not (a.no_check or a.dry_run):
        stream = Stream(base.replace("http", "ws", 1) + "/ws")
        stream.start()
        time.sleep(1.5)
        print("Live signal check:", "on" if stream.connected else "waiting for the stream (runs continue regardless)")
    total = sum(s for _, s in plan) + a.rest * len(plan)
    print("\nPlan: %s  (~%.0f s). Ctrl-C stops and clears the label.\n" % (", ".join("%s %ds" % (n, s) for n, s in plan), total))

    results = []
    try:
        for i, (name, secs) in enumerate(plan, 1):
            tries = 0
            while True:
                print("(%d/%d) NEXT: %s for %d s%s\n      %s" % (i, len(plan), name.upper(), secs,
                                                              "  (redo)" if tries else "", HOW[name]))
                good, msg = record(base, name, secs, a, stream)
                print("      check:", msg)
                if good or tries >= a.retries:
                    results.append((name, good, msg))
                    break
                tries += 1
                print("      -> repeating this run once. Get ready.")
                time.sleep(2)
            if i < len(plan) and a.rest > 0:
                print("      rest %.0f s (label none)" % a.rest)
                time.sleep(a.rest)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        try:
            call(base, "/api/label", {"label": "none"}, a.dry_run)
            if a.sim:
                call(base, "/api/sim/scene", {"scene": "idle"}, a.dry_run)
        except Exception:
            pass
    bad = [r for r in results if not r[1]]
    print("\nSummary: %d runs, %d failed their check%s" % (len(results), len(bad), ":" if bad else "."))
    for name, _, msg in bad:
        print("  %s: %s" % (name, msg))
    print("\nDone. Now run:  python tools/analyze.py          (add --write to save detector_config.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
