# Fast motion: as many readings a second as this link allows, and a check that none got lost.
# Needs band firmware 1.1 for more than 100 a second (an older band runs this at 100).
#   python3 fast_motion.py 42      python3 fast_motion.py 42 --usb      python3 fast_motion.py --sim
import kit
from vexo_open.sync import Band

args = kit.args(seconds=3)
band = Band.connect(args.number, args.transport)
limits = band.motion_limits()    # the fastest rate on this link for accel alone, gyro alone and both
print("This link allows: accel %s, gyro %s, both %s readings a second." % (limits.accel, limits.gyro, limits.both))
seen = {"count": 0, "first": None, "last": None}   # count the readings and keep two: nothing grows while it runs


def on_motion(m):
    seen["count"] += 1
    seen["first"] = seen["first"] or m
    seen["last"] = m


band.on("motion", on_motion)
streams = band.configure(motion="fastest", sensors="accel" if limits.accel else "both")
band.wait(args.seconds)
band.disconnect()

count, first, last = seen["count"], seen["first"], seen["last"]
if count < 2 or last.time <= first.time:
    raise SystemExit("Too few readings arrived to measure the rate. Run it for longer: --seconds 3")
seconds = last.time - first.time                                 # time is the real time of each reading
print("Asked for %d a second, measured %.1f a second." % (streams["motion"], (count - 1) / seconds))
print("(A few percent apart is normal, not a fault: the sensor counts with its own clock, which runs a little\n"
      " fast or slow. The time of each reading, m.time and m.t, is measured by the band and is the truth.)")
if first.n is not None:          # n numbers the readings: a jump means that many were lost on the way
    print("Readings lost on the way:", last.n - first.n + 1 - count)
