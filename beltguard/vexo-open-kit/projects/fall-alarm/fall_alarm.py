# Fall alarm: the band notices a fall. If the wearer then stays still, an alarm counts
# down; a double tap on the band cancels it. With no double tap, the alert goes out.
#
# Typed in my-project/, the folder that holds the kit:
#   python3 vexo-open-kit/projects/fall-alarm/fall_alarm.py 42        band 42 over Bluetooth
#   python3 vexo-open-kit/projects/fall-alarm/fall_alarm.py --sim     a pretend band that falls three times
#
# Kit features: free-fall, motion and double-tap events, activity (still / walk / run),
# LED patterns, the real time on every event, auto-reconnect.
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))   # the folder `shared`, next to this project's folder
try:
    import start  # noqa: E402  finds the kit's library and reads the command line
except ModuleNotFoundError:
    raise SystemExit("The folder `shared` is missing: copy it from vexo-open-kit/projects/shared, next to this project's folder.") from None
from vexo_open.sync import Band, VexoError  # noqa: E402

# The numbers to tune. They are guesses that work on the pretend band: nobody has tried
# them on a real fall yet.
CHECK_SECONDS = 5.0         # the wearer must stay still this long after a fall before the alarm starts
COUNTDOWN_SECONDS = 10.0    # time to cancel the alarm with a double tap
LANDING_SECONDS = 1.0       # movement in the first second after a fall is the landing, not "getting up"

args = start.args(("--check", CHECK_SECONDS, "seconds the wearer must stay still after a fall before the alarm starts"),
                  ("--countdown", COUNTDOWN_SECONDS, "seconds to cancel the alarm with a double tap"))
if not (0 < args.check <= 3600 and 1 <= args.countdown <= 3600):
    raise SystemExit("--check must be above 0 and --countdown 1 or more (seconds, at most 3600).")


def send_alert(fell_at):
    """The alert itself. Here it only prints. Make it yours: send a message, play a sound,
    call a web address, light up a second band..."""
    print("ALERT: fall at %s and no reaction. Somebody should go and look!" % fell_at)


def light(colour, pattern="solid", period=1000):
    """Set the LED. While a dropped link is coming back, commands fail with not-connected:
    skip the light then, the alarm itself must keep running."""
    try:
        band.set_led(colour, pattern=pattern, period=period)
    except VexoError as e:
        if e.code not in ("not-connected", "timeout"):   # timeout: the band went out of range a moment before the link is declared lost
            raise


band = Band.connect(args.number, args.transport, auto_reconnect=True)
print("Connected to %s. Watching for falls. Stop with Ctrl+C." % band.name)

if args.pretend:
    # With --sim nothing falls by itself, so this little story plays. Times are seconds
    # from now. `sim` is the kit's pretend band; tap() and set_activity() direct it.
    sim, check, countdown = args.pretend, args.check, args.countdown
    a = 2                                   # first fall: the wearer cancels the alarm
    start.story(band, a, "falls, then lies still", lambda: (sim.tap("free-fall"), sim.set_activity("still")))
    start.story(band, a + 1 + check + countdown / 2, "is double-tapped", lambda: sim.tap("double-tap"))
    b = a + 3 + check + countdown / 2       # second fall: the wearer gets up at once
    start.story(band, b, "falls again", lambda: sim.tap("free-fall"))
    start.story(band, b + 1 + check / 2, "moves: the wearer gets up", lambda: sim.tap("motion-start"))
    c = b + 3 + check / 2                   # third fall: nobody reacts
    start.story(band, c, "falls a third time and nobody reacts", lambda: sim.tap("free-fall"))
    if args.seconds is None:
        args.seconds = c + 3 + check + countdown    # the story is over then; a real band runs until Ctrl+C

# Handlers run one at a time on the library's background thread, so they stay short: they
# only put what happened into this inbox. The loop below, on the main thread, keeps the
# clock and decides what to do.
happened = start.Inbox()      # holds at most start.INBOX_MOST reports; when a band floods it, the oldest go
band.on("free-fall", lambda e: happened.put(("fall", e.time)))
band.on("motion-start", lambda e: happened.put(("moved", e.time)))
band.on("double-tap", lambda e: happened.put(("double-tap", e.time)))
band.on("activity", lambda e: e.state != "still" and happened.put(("moved", e.time)))
band.on("reconnecting", lambda e: happened.put(("link-lost", None), keep=True))
band.on("reconnected", lambda e: happened.put(("link-back", None), keep=True))
band.on("disconnect", lambda e: happened.put(("gone", e.reason), keep=True))
band.configure(events=True, activity=True)      # nothing arrives until you turn it on

# The light for each state. After a dropped link the library sets the last light again,
# which may be the light of an earlier state, so we set the right one when the link is back.
LIGHTS = {"watching": ("#00ff00", "breathe", 3000), "checking": ("#ff8800", "blink", 400),
          "alarm": ("#ff0000", "flash", 300), "alerted": ("#ff0000", "solid", 1000)}
state = "watching"       # "watching" -> "checking" -> "alarm" -> "alerted", and back to "watching"
since = 0.0              # when the current state began (computer time, seconds)
fell_at = ""             # clock time of the fall, as text
left_shown = None        # the countdown number printed last
light(*LIGHTS[state])
end = None if args.seconds is None else time.time() + args.seconds

try:
    while end is None or time.time() < end:
        time.sleep(0.05)
        now = time.time()

        # 1. What did the band report since the last look?
        for what, when in happened.take():
            if what == "fall" and state == "watching":
                fell_at = start.clock(when)     # e.time is real time, like time.time(); clock() makes it text
                state, since = "checking", now
                print("Fall at %s. Is the wearer moving?" % fell_at)
                light(*LIGHTS[state])
            elif what == "moved" and state == "checking" and now - since > LANDING_SECONDS:
                state = "watching"
                print("The wearer is moving. No alarm.")
                light(*LIGHTS[state])
            elif what == "double-tap" and state in ("alarm", "alerted"):
                state = "watching"
                print("Double tap: alarm cancelled. Watching again.")
                light(*LIGHTS[state])
            elif what == "link-lost":
                print("Link lost. Trying to get it back; the alarm keeps counting.")
            elif what == "link-back":
                print("Link is back.")
                light(*LIGHTS[state])
            elif what == "gone":
                raise SystemExit("Disconnected (%s). Start the program again." % when)

        # 2. Has enough time passed to move on?
        if state == "checking" and now - since >= LANDING_SECONDS + args.check:
            state, since, left_shown = "alarm", now, None
            print("No movement for %g seconds. Double-tap the band to cancel!" % args.check)
            light(*LIGHTS[state])
        if state == "alarm":
            left = args.countdown - (now - since)
            if left <= 0:
                state = "alerted"
                send_alert(fell_at)
                print("Double-tap the band to reset.")
                light(*LIGHTS[state])
            elif int(left + 0.999) != left_shown:
                left_shown = int(left + 0.999)
                print("  alarm in %d..." % left_shown)
except KeyboardInterrupt:
    pass
band.disconnect()
