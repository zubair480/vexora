"""S0 band probe: what does band 07's beta firmware really do? Writes ops/band_probe.jsonl.

Waits up to 150 s for band 07 to be free (the kit dashboard tab must disconnect first),
then tests info, motion limits, ranges, every rate, events, activity, LED and mic,
restores sane settings and disconnects so the backend can take the band.
"""
import json
import math
import statistics
import sys
import time
from pathlib import Path

from vexo_open.sync import Band

BAND = 7  # team band; never any other number
OUT = Path(__file__).with_name("band_probe.jsonl")
out = OUT.open("w", encoding="utf-8")


def log(test, **kw):
    rec = {"ts": round(time.time(), 3), "test": test, **kw}
    line = json.dumps(rec, default=str)
    print(line, flush=True)
    out.write(line + "\n")
    out.flush()


def attempt(test, fn):
    try:
        r = fn()
        log(test, ok=True, result=r)
        return r
    except Exception as e:  # report every failure, keep testing
        log(test, ok=False, error=f"{type(e).__name__}: {e}")
        return None


band = None
deadline = time.time() + 600
while time.time() < deadline:
    try:
        band = Band.connect(BAND, "ble")
        break
    except Exception as e:
        log("connect-wait", ok=False, error=f"{type(e).__name__}: {e}")
        time.sleep(4)
if band is None:
    log("connect", ok=False, error="band 07 not free after 600 s")
    sys.exit(1)
number = getattr(band, "number", None)
log("connect", ok=True, number=number)
if number not in (BAND, None):
    log("wrong-band", ok=False, error=f"connected to {number}, disconnecting")
    band.disconnect()
    sys.exit(2)

for name in ("get_info", "get_radio", "get_battery", "get_motion", "get_events", "get_mic", "get_led"):
    attempt(name, getattr(band, name))
limits = attempt("motion_limits", band.motion_limits)
attempt("set_ranges_16g_2000dps", lambda: band.set_motion(accel_range=16, gyro_range=2000))
attempt("get_motion_after_ranges", band.get_motion)

samples = []
band.on("motion", samples.append)
counts = {}


def counter(name):
    def f(_m):
        counts[name] = counts.get(name, 0) + 1
    return f


def measure(label, seconds=3.0, **cfg):
    samples.clear()
    applied = attempt(f"configure:{label}", lambda: band.configure(**cfg))
    band.wait(seconds)
    ss = list(samples)
    res = {"cfg": cfg, "applied": applied, "count": len(ss)}
    if len(ss) >= 2:
        span = ss[-1].time - ss[0].time
        res["measured_hz"] = round((len(ss) - 1) / span, 1) if span > 0 else None
        if getattr(ss[0], "n", None) is not None and getattr(ss[-1], "n", None) is not None:
            res["lost"] = ss[-1].n - ss[0].n + 1 - len(ss)

        def col(f):
            return [getattr(m, f) for m in ss if getattr(m, f, None) is not None]

        ax, ay, az = col("ax"), col("ay"), col("az")
        if ax:
            mag = [math.sqrt(a * a + b * b + c * c) for a, b, c in zip(ax, ay, az)]
            res["acc_mag_mean_g"] = round(statistics.fmean(mag), 4)
            res["acc_mag_std_mg"] = round(statistics.pstdev(mag) * 1000, 2)
        gx, gy, gz = col("gx"), col("gy"), col("gz")
        if gx:
            res["gyro_mean_dps"] = [round(statistics.fmean(v), 3) for v in (gx, gy, gz)]
            res["gyro_std_dps"] = [round(statistics.pstdev(v), 3) for v in (gx, gy, gz)]
        res["first_sample"] = {k: getattr(ss[0], k, None) for k in ("t", "time", "n", "ax", "ay", "az", "gx", "gy", "gz")}
    log(f"measure:{label}", ok=len(ss) > 0, result=res)
    return res


lim_both = getattr(limits, "both", None) if limits is not None else None
lim_acc = getattr(limits, "accel", None) if limits is not None else None
measure("50-both", motion=50, sensors="both")
for r in (208, 416):
    if lim_both is None or r <= lim_both:
        measure(f"{r}-both", motion=r, sensors="both")
    else:
        log(f"skip:{r}-both", ok=False, error=f"motion_limits both={lim_both}")
for r in (833, 1666):
    if lim_acc is None or r <= lim_acc:
        measure(f"{r}-accel", motion=r, sensors="accel")
    else:
        log(f"skip:{r}-accel", ok=False, error=f"motion_limits accel={lim_acc}")

for ev in ("event", "activity", "audio", "mic-level", "battery", "link"):
    attempt(f"on:{ev}", lambda ev=ev: (band.on(ev, counter(ev)), "registered")[1])
measure("50-both+events+activity", seconds=4, motion=50, sensors="both", events=True, activity=True, battery=2)
log("stream-counts-no-audio", ok=True, result=dict(counts))

attempt("set_led_orange_mic_off", lambda: band.set_led(255, 90, 0))
attempt("get_led", band.get_led)

counts.clear()
measure("208-both+audio", seconds=4, motion=208, sensors="both", audio=True, mic_level=0.1)
log("stream-counts-audio", ok=True, result=dict(counts))
attempt("set_led_during_audio_expect_fail", lambda: band.set_led(0, 180, 80))
measure("416-both+audio", seconds=3, motion=416, sensors="both", audio=True)
attempt("audio_off", lambda: band.configure(audio=False, mic_level=False))

attempt("restore_50_both_events", lambda: band.configure(motion=50, sensors="both", events=True, activity=False, battery=False))
attempt("led_off", band.led_off)
attempt("disconnect", band.disconnect)
log("done", ok=True)
