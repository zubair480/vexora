"""Shared by the Python projects in this folder. It does three small jobs:

  1. lets Python find the kit's library (so there is nothing to install),
  2. reads the command line, the same way for every project:

       python3 fall_alarm.py 42          band 42 over Bluetooth
       python3 fall_alarm.py 42 --usb    band 42 on the USB cable
       python3 fall_alarm.py --sim       a pretend band, no hardware

     (on Windows type `python` instead of `python3`),
  3. shows a problem with the band as one clear sentence instead of a long traceback,
  4. keeps a faulty band from filling the computer: Inbox() holds what the handlers
     report and never grows past INBOX_MOST, and clock() turns a time from the band into
     text without failing on a time that no calendar has.

The band number is never written in a project: it always comes from the command line.
"""
import argparse
import sys
from collections import deque
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Where is the kit's library? This folder is in one of two places:
#   vexo-open-kit/projects/shared      inside the kit, as it came
#   my-project/shared                  copied into your own folder, with the kit next to it
for _library in (HERE.parent.parent / "python", HERE.parent / "vexo-open-kit" / "python"):
    if (_library / "vexo_open").is_dir():
        sys.path.insert(0, str(_library))
        break
else:
    raise SystemExit("The kit's library was not found: the folder with this project and `shared` must have "
                     "the kit in it under the name vexo-open-kit (my-project/vexo-open-kit).")
from vexo_open import codec  # noqa: E402
from vexo_open.sync import VexoError  # noqa: E402
from vexo_open.transports.sim import SimTransport  # noqa: E402


def band_number(text):
    """"42", "07" or the full name "Vexo-HACK-42" -> 42. Anything else: say what to type.
    The same forms as the kit's examples take (examples/python/kit.py). Band numbers are
    1 to 999: a band that says 0 has no number yet."""
    text = text.strip()
    if text.isascii() and text.isdigit() and len(text) <= 3:
        number = int(text)
    else:
        number = codec.band_number_of(codec.NAME_PREFIX + text[len(codec.NAME_PREFIX):]
                                      if text.lower().startswith(codec.NAME_PREFIX.lower()) else text)
    if number is None:
        raise argparse.ArgumentTypeError('"%s" is not a band number. Type just the number on your band or its bag, '
                                         'for example 42.' % text)
    if number == 0:
        raise argparse.ArgumentTypeError("Band numbers are 1 to 999. 0 is what a band has before it gets a number: "
                                         "ask an organiser to number it.")
    return number


def args(*more):
    """Returns args.number, args.transport and args.seconds, plus one value for every
    extra option. An extra option is (name, default, help), e.g. ("--every", 60.0, "seconds").
    With --sim, args.transport is the kit's pretend band (also as args.pretend), ready to
    be directed; otherwise args.pretend is None."""
    p = argparse.ArgumentParser()
    p.add_argument("number", nargs="?", type=band_number, help="the number on your band, e.g. 42")
    p.add_argument("--sim", action="store_true", help="use a pretend band (no hardware needed)")
    p.add_argument("--usb", action="store_true", help="use the USB cable instead of Bluetooth")
    p.add_argument("--seconds", type=float, default=None, help="stop after this many seconds")
    for name, default, text in more:
        if isinstance(default, bool):
            p.add_argument(name, action="store_true", help=text)
        else:
            p.add_argument(name, default=default, type=type(default), help=text)
    a = p.parse_args()
    a.pretend = None
    a.transport = "serial" if a.usb else "ble"
    if a.sim:
        number = 42 if a.number is None else a.number      # a pretend band can have any number from 1 to 999
        # auto_tap=False: it does nothing by itself, the project's story decides what happens.
        # store_file=True: it keeps its storage between two runs, like a real band, in a
        # file only you can read (.vexo-open/pretend-band-NUMBER.json in your home folder).
        try:
            a.pretend = a.transport = SimTransport(number, auto_tap=False, store_file=True)
        except ValueError as problem:
            p.error(str(problem))
    elif a.number is None:
        p.error("give your band number (the XX in Vexo-HACK-XX), or --sim for a pretend band")
    return a


INBOX_MOST = 1000   # the most reports an Inbox keeps. A band sends tens a second; the loop empties it 10 to 20 times a second.

KEPT_MOST = 100     # the most link news (lost, back, gone) an Inbox keeps; the library sends a few per drop of the link


class Inbox:
    """What the handlers report, waiting for the program's main loop:

        happened = start.Inbox()
        band.on("tap", lambda e: happened.put(("tap", e)))      # in a handler: only put it in
        for what, e in happened.take(): ...                     # in the main loop: take out what is there

    It never holds more than INBOX_MOST reports: when a band sends faster than the loop
    reads, the oldest are dropped (`dropped` counts them). And take() gives only what is
    there at that moment, so the loop always gets on to its next step and can look at its
    own clock: `--seconds` and Ctrl+C work however fast the band sends.

    News about the link itself ("reconnecting", "reconnected", "disconnect") comes from the
    library, not from the band. Put it in with keep=True: such a report is never pushed
    out by the band's data, and take() gives it first."""

    def __init__(self, most=INBOX_MOST):
        self._reports = deque(maxlen=most)
        self._kept = deque(maxlen=KEPT_MOST)
        self._put = 0
        self._taken = 0

    def put(self, report, keep=False):
        if keep:
            self._kept.append(report)       # link news: a few per drop of the link, never the band's own data
            return
        self._put += 1
        self._reports.append(report)        # a full deque drops its oldest by itself

    def take(self):
        reports = []
        for _ in range(len(self._kept)):
            try:
                reports.append(self._kept.popleft())
            except IndexError:
                break
        kept = len(reports)
        for _ in range(len(self._reports)):
            try:
                reports.append(self._reports.popleft())
            except IndexError:              # dropped as the oldest in the meantime
                break
        self._taken += len(reports) - kept
        return reports

    @property
    def dropped(self):
        """How many reports were dropped because the inbox was full."""
        return max(0, self._put - self._taken - len(self._reports))

    def __len__(self):
        return len(self._reports) + len(self._kept)


def clock(seconds):
    """A time from the band (seconds since 1970, like time.time()) as text: "14:03:27".
    Anyone who connects can set a band's clock, so the number can be anything: one that
    is no time of day at all is shown plainly, as "time 1e+300", and nothing fails."""
    try:
        return datetime.fromtimestamp(seconds).strftime("%H:%M:%S")
    except (OverflowError, OSError, ValueError, TypeError):
        return "time %.6g" % seconds if isinstance(seconds, (int, float)) else "time unknown"


def story(band, seconds, text, action):
    """Pretend band only: `seconds` from now, print `text` and run `action`. The action
    directs the pretend band with the kit's own calls, for example
        story(band, 2, "the band falls", lambda: sim.tap("free-fall"))
    Everything it can do is listed under "The pretend band" in docs/api.md.
    The action runs on the band's own thread, so inside it `sim.tap(...)` is right.
    Anywhere else in a plain program write band.transport.tap(...) instead: that hands
    the call over to that thread."""
    def step():
        print("  [pretend band] " + text, flush=True)
        action()
    band.transport.after(seconds, step)


def _short_error(kind, error, trace):
    """Show a band problem as one clear sentence instead of a long traceback."""
    if isinstance(error, VexoError):
        print("Problem (%s): %s" % (error.code, error), file=sys.stderr)
    else:
        sys.__excepthook__(kind, error, trace)


sys.excepthook = _short_error
