# Band leash: never leave your bag behind. Put the band on the bag and keep this program
# running on your laptop. It watches the signal strength: the band's light and the
# terminal show near / getting far / far, and when the link is lost it shouts.
#
# Typed in my-project/, the folder that holds the kit:
#   python3 vexo-open-kit/projects/band-leash/leash.py 42        band 42 over Bluetooth
#   python3 vexo-open-kit/projects/band-leash/leash.py --sim     a pretend band that walks away and comes back
#
# Kit features: link (signal strength), LED patterns, the shortest stream interval
# ("fastest"), the real time on every reading, auto-reconnect.
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))   # the folder `shared`, next to this project's folder
try:
    import start  # noqa: E402  finds the kit's library and reads the command line
except ModuleNotFoundError:
    raise SystemExit("The folder `shared` is missing: copy it from vexo-open-kit/projects/shared, next to this project's folder.") from None
from vexo_open.sync import Band, VexoError  # noqa: E402

# The numbers to tune. Signal strength is in dBm, always below zero. Measured on one band on
# a desk: about -18 to -19 next to the laptop, at the band's usual radio power.
# That is where the meter is full. NEAR_DBM and FAR_DBM are still guesses that work on the
# pretend band: nobody has walked away with a real band yet. Do that walk test in your room.
METER_FULL_DBM = -20.0  # the meter is full at this signal: the band lies next to the laptop (measured)
METER_EMPTY_DBM = -95.0  # and empty at this one (a guess: about where the link is lost)
NEAR_DBM = -60.0        # a stronger signal than this is near (a guess, needs the walk test)
FAR_DBM = -80.0         # a weaker signal than this is far (a guess, needs the walk test)
SMOOTH_SECONDS = 2.0    # the signal jumps around, so we use the average of the last 2 seconds
READINGS_MOST = 100     # and of at most this many readings (a band sends 10 a second: 20 in 2 seconds)
SLACK_DBM = 3           # the signal must go this far past a border before the zone changes (no flicker)
LIGHTS = {"near": ("#00ff00", "solid", 1000), "getting far": ("#ffcc00", "blink", 600), "far": ("#ff0000", "flash", 200)}

args = start.args(("--near", NEAR_DBM, "dBm: a stronger signal than this is near"),
                  ("--far", FAR_DBM, "dBm: a weaker signal than this is far"))
if not -120 <= args.far < args.near <= 0:
    raise SystemExit("--near and --far are dBm below zero, and near is the bigger one: for example --near -60 --far -80.")


def zone_for(signal, zone):
    """near, getting far or far. The borders move a little against a change: the zone you
    are in is harder to leave. Without that a signal right on a border flips all the time."""
    if zone is None:                    # the first reading: the borders as they are
        near, far = args.near, args.far
    else:
        near = args.near - SLACK_DBM if zone == "near" else args.near + SLACK_DBM
        far = args.far + SLACK_DBM if zone == "far" else args.far - SLACK_DBM
    return "near" if signal >= near else "far" if signal < far else "getting far"


def meter(signal, zone):
    """One line of text: -72 dBm [######------] getting far"""
    part = (signal - METER_EMPTY_DBM) / (METER_FULL_DBM - METER_EMPTY_DBM)     # 0 = empty, 1 = full
    full = max(0, min(12, round(12 * part)))
    return "%4d dBm [%s%s] %s" % (signal, "#" * full, "-" * (12 - full), zone)


def light(zone):
    """Set the LED for a zone. While a dropped link is coming back, commands fail with
    not-connected: skip the light then, the program must keep running."""
    try:
        colour, pattern, period = LIGHTS[zone]
        band.set_led(colour, pattern=pattern, period=period)
    except VexoError as e:
        if e.code not in ("not-connected", "timeout"):   # timeout: the band went out of range a moment before the link is declared lost
            raise


if args.usb:
    print("Signal strength needs Bluetooth: the cable has no radio signal to measure. Run it without --usb.")
    raise SystemExit

band = Band.connect(args.number, args.transport, auto_reconnect=True)
print("Connected to %s. Near is above %g dBm, far is below %g dBm. Stop with Ctrl+C." % (band.name, args.near, args.far))

if args.pretend:
    # With --sim nobody walks anywhere, so this little story plays. Times are seconds from
    # now. `sim` is the kit's pretend band; set_rssi() and drop_link() direct it.
    sim = args.pretend
    band.transport.set_rssi(-48)
    start.story(band, 3, "you walk away from your bag", lambda: sim.set_rssi(-64))
    start.story(band, 5, "you reach the door", lambda: sim.set_rssi(-75))
    start.story(band, 8, "you are down the corridor", lambda: sim.set_rssi(-90))
    start.story(band, 12, "you leave the building: out of range for 4 seconds, then you run back",
                lambda: (sim.set_rssi(-50), sim.drop_link(4)))     # -50: when it is found again, it is near
    if args.seconds is None:
        args.seconds = 21       # the story is over then; a real band runs until Ctrl+C

# Handlers run one at a time on the library's background thread, so they stay short: they
# only put what happened into this inbox. The loop below, on the main thread, decides what
# to do.
happened = start.Inbox()      # holds at most start.INBOX_MOST reports; when a band floods it, the oldest go
band.on("link", lambda e: happened.put(("signal", e)))
band.on("reconnecting", lambda e: happened.put(("link-lost", e), keep=True))
band.on("reconnected", lambda e: happened.put(("link-back", e), keep=True))
band.on("disconnect", lambda e: happened.put(("gone", e), keep=True))
# Nothing arrives until you turn it on. "fastest" is the shortest interval the band allows:
# ten readings a second on firmware 1.1, one a second on firmware 1.0. The smoothing below
# goes by time, not by the count of readings, so both behave the same.
band.configure(link="fastest")

# The readings of the last SMOOTH_SECONDS: (band's time, computer's time, dBm). A reading
# leaves when it is too old by the band's time or by the computer's clock, or when there
# are more than READINGS_MOST. The computer's clock and the count are there because a
# faulty band can send any time, or the same time again and again: the list must not grow.
readings = deque(maxlen=READINGS_MOST)
zone = None         # "near", "getting far" or "far"; None = not known yet
signal = None       # the smoothed signal in dBm
shown = None        # the signal in the meter line printed last
lost_at = None      # computer time when the link was lost
end = None if args.seconds is None else time.time() + args.seconds

try:
    while end is None or time.time() < end:
        time.sleep(0.05)
        now = time.time()
        for what, e in happened.take():
            if what == "signal" and e.rssi is not None:     # None: the band had no reading this time
                readings.append((e.time, now, e.rssi))      # e.time is real time in seconds, like time.time()
                while not (0 <= e.time - readings[0][0] <= SMOOTH_SECONDS and now - readings[0][1] <= SMOOTH_SECONDS):
                    if len(readings) == 1:
                        break                               # the newest reading always stays
                    readings.popleft()
                signal = round(sum(dbm for _, _, dbm in readings) / len(readings))
                was, zone = zone, zone_for(signal, zone)
                if zone != was:
                    light(zone)
                    if zone == "far":
                        print("WARNING: the band is far away. Did you forget something?")
                if zone != was or abs(signal - shown) >= 3:     # do not fill the screen: only real changes
                    shown = signal
                    print(meter(signal, zone))
            elif what == "link-lost":
                lost_at = time.time()
                last = "no signal reading yet" if signal is None else "last signal %d dBm (%s)" % (signal, zone)
                print("\a*** LEFT BEHIND at %s! The link is lost, %s. ***" % (datetime.now().strftime("%H:%M:%S"), last))
                print("Looking for the band...")
            elif what == "link-back":
                print("Welcome back! The band was away for %.0f seconds." % (time.time() - lost_at))
                readings.clear()        # old readings say nothing about where the band is now
                zone = None             # so the next reading sets the light and prints the meter again
            elif what == "gone":
                raise SystemExit("The band did not come back (%s). Double-tap it and start the program again." % e.reason)
except KeyboardInterrupt:
    pass
band.disconnect()
