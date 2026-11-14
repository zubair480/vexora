# Shared by every example in this folder. It does two small jobs:
#   1. lets Python find the library in ../../python (so there is nothing to install),
#   2. reads the command line:  python3 led.py 42          band 42 over Bluetooth
#                               python3 led.py 42 --usb    band 42 on the USB cable
#                               python3 led.py --sim       a pretend band, no hardware
# A band problem that ends an example is shown as one sentence, not as a traceback. That
# is the library's doing (vexo_open.sync), so your own programs get it too.
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "python"))
from vexo_open import codec  # noqa: E402
from vexo_open.sync import VexoError, short_errors  # noqa: E402

short_errors()   # also for a problem before the first Band.connect

# A voice for the pretend band: 16 seconds of a person reading aloud (public domain), in the kit.
SAMPLE_SPEECH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "dashboard", "speech",
                             "sample-speech.opus")


def sample_speech():
    """SAMPLE_SPEECH as you would type it in the folder you are in."""
    try:
        return os.path.relpath(SAMPLE_SPEECH)
    except ValueError:               # Windows: the kit is on another drive
        return os.path.abspath(SAMPLE_SPEECH)


# For the examples that listen:  --say "words"  takes typed words in place of the microphone.
SAY = ("--say", None, 'typed words instead of the microphone, for example --say "make it red" (no Deepgram key needed)')


def voice(args):
    """Extra options for Band.connect in an example that listens. A pretend band has no
    microphone, so with --sim it plays the kit's sample recording (or the file given with
    --replay) as its sound, and args.seconds is cut to the length of that recording so the
    words are heard once. With a real band: nothing extra."""
    if args.transport != "sim":
        return {}
    from vexo_open import audio
    packets = audio.ogg_packets(getattr(args, "replay", None) or SAMPLE_SPEECH)
    args.seconds = min(args.seconds, len(packets) / 50)       # 50 packets a second
    return {"audio_packets": packets}


def plain(text):
    """Text that came from a band, safe to print. Whoever connected before you wrote it, so
    it can hold characters that steer a terminal (move the cursor, hide a line, change the
    window title). Each of those is shown as its code instead, for example \\x1b."""
    if text is None:
        return None
    steering = {0x200e, 0x200f, 0x2028, 0x2029, 0x202a, 0x202b, 0x202c, 0x202d, 0x202e, 0x2066, 0x2067, 0x2068, 0x2069}
    return "".join("\\x%02x" % ord(c) if ord(c) < 0x20 or 0x7f <= ord(c) <= 0x9f
                   else "\\u%04x" % ord(c) if ord(c) in steering else c for c in str(text))


def band_number(text):
    """"42", "07" or the full name "Vexo-HACK-42" -> 42. Anything else: say what to type."""
    text = text.strip()
    if text.isascii() and text.isdigit() and len(text) <= 3:
        return int(text)
    number = codec.band_number_of(codec.NAME_PREFIX + text[len(codec.NAME_PREFIX):]
                                  if text.lower().startswith(codec.NAME_PREFIX.lower()) else text)
    if number is None:
        raise argparse.ArgumentTypeError('"%s" is not a band number. Type just the number on your band or its bag, '
                                         'for example 42.' % text)
    return number


def args(seconds=None, more=()):
    """Returns args.number, args.transport ("ble", "serial" or "sim") and args.seconds.
    seconds: how long the example runs if --seconds is not given (None = until Ctrl+C).
    more: extra options for one example, as (name, default, help) tuples."""
    p = argparse.ArgumentParser()
    p.add_argument("number", nargs="?", type=band_number, help="the number on your band, e.g. 42")
    p.add_argument("--sim", dest="transport", action="store_const", const="sim", default="ble",
                   help="use a pretend band (no hardware needed)")
    p.add_argument("--usb", dest="transport", action="store_const", const="serial",
                   help="use the USB cable instead of Bluetooth")
    p.add_argument("--seconds", type=float, default=seconds, help="how long to run")
    for name, default, text in more:
        p.add_argument(name, default=default, help=text)
    found = p.parse_args()
    if found.number is None and found.transport == "ble":     # Bluetooth needs to know which band
        typed = "%s %s" % ("python" if os.name == "nt" else "python3", sys.argv[0])   # the command as it was typed
        p.error("add your band number, or --sim for a pretend band:   %s 42   or   %s --sim" % (typed, typed))
    return found
