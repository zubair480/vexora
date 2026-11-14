# Step diary: a diary of steps and activity that lives ON THE BAND. Every minute it saves
# one small entry in the band's storage, so the diary survives a dropped link, a closed
# laptop and a restart of this program.
#
# Typed in my-project/, the folder that holds the kit:
#   python3 vexo-open-kit/projects/step-diary/diary.py 42             band 42 over Bluetooth: record
#   python3 vexo-open-kit/projects/step-diary/diary.py 42 --show      print the diary that is on the band
#   python3 vexo-open-kit/projects/step-diary/diary.py 42 --clear     delete the diary (only this project's boxes)
#   python3 vexo-open-kit/projects/step-diary/diary.py --sim --every 3     a pretend band, one entry every 3 seconds
#
# Kit features: activity (steps, still / walk / run), storage, the real time on every
# event, auto-reconnect.
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))   # the folder `shared`, next to this project's folder
try:
    import start  # noqa: E402  finds the kit's library and reads the command line
except ModuleNotFoundError:
    raise SystemExit("The folder `shared` is missing: copy it from vexo-open-kit/projects/shared, next to this project's folder.") from None
from vexo_open.sync import Band, VexoError  # noqa: E402

# The numbers to tune. Where the diary lives in the band's storage (boxes are numbered
# 0..999): the entries go round in a ring, after box 459 comes box 400 again, so the
# oldest entry is overwritten and the band never fills up.
EVERY_SECONDS = 60.0    # seconds in one diary entry
FIRST_BOX = 400         # entries are in boxes 400..459
BOXES = 60
COUNT_BOX = 460         # how many entries were ever written; it tells where the ring is now
GAP_SECONDS = 2.5       # two readings further apart than this: the link was down, the state in between is unknown
WAITING_MOST = BOXES    # the most finished entries that wait in the computer for the link; the oldest go first
STATES = ("still", "walk", "run")

# Storage is NOT private: anyone who connected to the band before you can have written
# anything into these boxes. So an entry holds numbers only, never a name, and what comes
# back is checked before it is used: an entry is exactly five whole numbers (and ",1" at
# the end when the link dropped during it), and the count is one whole number. Everything
# else is skipped, never printed and never run.
ENTRY = re.compile(r"([0-9]{1,10}),([0-9]{1,6}),([0-9]{1,5}),([0-9]{1,5}),([0-9]{1,5})(,1)?")
# An entry with a dropped link looks odd: "12 steps, still 1s". The steps are all there
# (the band kept counting), but the seconds without the link are in no column. So such an
# entry gets this label, when it is saved and in --show.
DROPPED = "(link dropped: steps complete, seconds away not counted)"
COUNT = re.compile(r"[0-9]{1,9}")
YEAR_2020, YEAR_2100 = 1577836800, 4102444800      # a start time outside these is not a real entry

args = start.args(("--every", EVERY_SECONDS, "seconds in one diary entry"),
                  ("--show", False, "print the diary that is on the band, then stop"),
                  ("--clear", False, "delete the diary from the band, then stop"))
if not 1 <= args.every <= 86400:
    raise SystemExit("--every must be 1 second or more (at most 86400, one day).")


def read_box(box, pattern):
    """The text in a storage box if it has exactly the form we wrote, otherwise None."""
    raw = band.storage.get(box)                     # bytes, or None when the box is empty
    if raw is None or len(raw) > 40:
        return None
    try:
        return pattern.fullmatch(raw.decode("ascii"))
    except UnicodeDecodeError:
        return None                                 # not text at all


def read_count():
    found = read_box(COUNT_BOX, COUNT)
    return int(found.group()) if found else 0


def read_diary():
    """The entries on the band, oldest first. One entry is five numbers as text:
    start time (seconds since 1970), steps, seconds still, seconds walking, seconds running.
    Here a sixth is added: 1 when the link dropped during the entry, otherwise 0."""
    count, entries = read_count(), []
    for n in range(max(0, count - BOXES), count):
        found = read_box(FIRST_BOX + n % BOXES, ENTRY)
        if found and YEAR_2020 <= int(found.group(1)) < YEAR_2100:
            entries.append([int(part) for part in found.groups()[:5]] + [1 if found.group(6) else 0])
    return entries


clock = start.clock     # a time from the band as text, "14:03:27"; event times are like time.time()


def whole_seconds(spent):
    """The seconds in each state as whole numbers that add up to the rounded sum. Rounding
    each one by itself can lose a second: 2.5 + 2.5 would be saved as 2 + 2."""
    whole = {state: int(spent[state]) for state in STATES}
    left = round(sum(spent.values())) - sum(whole.values())
    for state in sorted(STATES, key=lambda s: whole[s] - spent[s])[:left]:      # biggest remainder first
        whole[state] += 1
    return tuple(min(whole[state], 99999) for state in STATES)


band = Band.connect(args.number, args.transport, auto_reconnect=True)

if args.show:
    diary = read_diary()
    print("Diary on %s: %d entries." % (band.name, len(diary)))
    most = max([entry[1] for entry in diary] + [1])
    print("  %-9s %5s %6s %6s %6s" % ("start", "steps", "still", "walk", "run"))
    for begun, steps, still, walk, run, dropped in diary:
        bar = "#" * round(30 * steps / most)
        print(("  %-9s %5d %5ds %5ds %5ds  %s" % (clock(begun), steps, still, walk, run, bar)
               + ("  " + DROPPED if dropped else "")).rstrip())
    sums = [sum(column) for column in zip(*diary)] or [0] * 5
    print("  %-9s %5d %5ds %5ds %5ds" % (("total",) + tuple(sums[1:5])))

if args.clear:                                      # after --show, so both together show it one last time
    for box in list(range(FIRST_BOX, FIRST_BOX + BOXES)) + [COUNT_BOX]:
        if band.storage.get(box) is not None:       # deleting an empty box fails with not-found
            band.storage.delete(box)                # never wipe(): other projects may store things too
    print("Diary deleted from %s." % band.name)

if args.show or args.clear:
    band.disconnect()
    raise SystemExit

count = read_count()
on_band = len(read_diary())     # entries --show would print now
print("Connected to %s. %d entries are already on the band (see them with --show)." % (band.name, on_band))
print("Recording one entry every %g seconds. Stop with Ctrl+C." % args.every)

if args.pretend:
    # With --sim nobody walks, so this little story plays. Times are seconds from now.
    # `sim` is the kit's pretend band; set_activity() and drop_link() direct it.
    sim, every = args.pretend, args.every
    band.transport.set_activity("still")
    start.story(band, 1.25 * every, "the wearer starts walking", lambda: sim.set_activity("walk"))
    start.story(band, 2.5 * every, "the wearer runs", lambda: sim.set_activity("run"))
    start.story(band, 3.5 * every, "runs out of range: the link drops for 2 seconds", lambda: sim.drop_link(2))
    start.story(band, 4.75 * every, "the wearer sits down", lambda: sim.set_activity("still"))
    start.story(band, 6.25 * every, "the wearer walks again", lambda: sim.set_activity("walk"))
    if args.seconds is None:
        args.seconds = 7 * every + 3        # the story is over then; a real band runs until Ctrl+C

# Handlers run one at a time on the library's background thread, so they stay short: they
# only put what happened into this inbox. The loop below, on the main thread, does the
# counting and the saving.
happened = start.Inbox()      # holds at most start.INBOX_MOST reports; when a band floods it, the oldest go
band.on("activity", lambda e: happened.put(("reading", e)))
band.on("reconnecting", lambda e: happened.put(("link-lost", e), keep=True))
band.on("reconnected", lambda e: happened.put(("link-back", e), keep=True))
band.on("disconnect", lambda e: happened.put(("gone", e), keep=True))
band.configure(activity=True)       # nothing arrives until you turn it on; then one reading a second

# A reading says what the wearer did since the reading before it. Two places need care, or
# every entry comes out a second short (5 readings are only 4 gaps):
# - the first reading has no reading before it: it stands for the time since `listening`;
# - the gap that an entry's end falls into is cut in two at the end: the part before goes
#   into that entry (`counted`), the rest into the next one.
total = None        # the band's step counter in the newest reading. It is a lifetime total.
total_before = None  # the same counter when the current entry began: the entry stores the difference
begun = None        # real time of the first reading in the current entry
last = None         # real time of the newest reading
state = None        # the state in the newest reading; None = not known (no reading yet, or the link was down)
seen = None         # the computer's time when the newest reading came in
counted = 0.0       # seconds after the newest reading that are in an entry already
listening = time.time()                 # the computer's time when the readings were turned on
spent = dict.fromkeys(STATES, 0.0)      # seconds in each state in the current entry
link_down = False   # True from "link lost" until "link is back"
dropped = False     # was the link down at any moment of the current entry?
waiting = []        # finished entries that are not on the band yet (the link may be down)
next_entry = time.time() + args.every
end = None if args.seconds is None else time.time() + args.seconds

try:
    while end is None or time.time() < end:
        time.sleep(0.1)

        # 1. What did the band report since the last look?
        for what, e in happened.take():
            if what == "reading":
                if total_before is None:
                    total_before = e.steps
                if begun is None:
                    # The band's clock can be wrong (anyone can set it). A time that is not
                    # between 2020 and 2100 could not be read back: use the computer's clock.
                    begun = e.time if YEAR_2020 <= e.time < YEAR_2100 else time.time()
                # e.time is real time in seconds. After a gap (link down) the state in between is unknown: count nothing.
                # One entry lasts args.every seconds on the computer's clock, so no state can have more.
                now = time.time()
                if last is None:
                    gap = now - listening               # the very first reading
                else:
                    gap = e.time - last
                if 0 < gap < GAP_SECONDS and e.state in spent:
                    spent[e.state] = min(spent[e.state] + max(0.0, gap - counted), args.every + GAP_SECONDS)
                total, last, state, seen, counted = e.steps, e.time, e.state, now, 0.0
            elif what == "link-lost":
                link_down = dropped = True
                state = None                            # what the wearer does now is unknown
                print("Link lost. Trying to get it back; the diary goes on.")
            elif what == "link-back":
                link_down = False
                print("Link is back after %.0f seconds. No step is lost: the band kept counting." % e.seconds)
            elif what == "gone":
                raise SystemExit("Disconnected (%s). Start the program again; the diary is still on the band." % e.reason)

        # 2. Is the entry finished? With no reading at all (link down) nothing is saved; the steps go into the next entry.
        if time.time() >= next_entry:
            # The seconds from the newest reading to the end of the entry belong to this entry.
            # The next reading will tell the state; we take the newest one we know.
            tail = 0.0 if seen is None else next_entry - seen - counted
            if state in spent and 0 < tail < GAP_SECONDS:
                spent[state] = min(spent[state] + tail, args.every + GAP_SECONDS)
                counted += tail
            next_entry += args.every
            if begun is not None:
                steps = max(0, total - total_before)    # below 0: the band's counter started again
                waiting.append((int(begun), min(steps, 999999)) + whole_seconds(spent) + (",1" if dropped else "",))
                del waiting[:-WAITING_MOST]             # the band holds 60 entries; more than that need not wait
                total_before, begun, spent = total, None, dict.fromkeys(STATES, 0.0)
            dropped = link_down                         # still away: the next entry has a part without link too

        # 3. Save what is waiting. While the link is down this fails with not-connected:
        #    the entry stays in the list and we try again on the next round.
        try:
            while waiting and band.connected:
                box = FIRST_BOX + count % BOXES
                band.storage.put(box, "%d,%d,%d,%d,%d%s" % waiting[0])
                count += 1
                if count >= 10 ** 9:                    # the count box holds 9 digits: go back to a small
                    count = BOXES + count % BOXES       # number that points at the same place in the ring
                band.storage.put(COUNT_BOX, str(count))
                begun_, steps, still, walk, run, flag = waiting.pop(0)
                on_band = min(on_band + 1, BOXES)
                print("%s  %3d steps   still %ds, walk %ds, run %ds   -> box %d%s"
                      % (clock(begun_), steps, still, walk, run, box, "   " + DROPPED if flag else ""))
        except VexoError as e:
            if e.code not in ("not-connected", "timeout"):   # timeout: the band went out of range a moment before the link is declared lost
                raise
except KeyboardInterrupt:
    pass
print("Stopped. %d entries are on the band. See them with --show." % on_band)
band.disconnect()
