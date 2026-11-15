# Measure how well speech-to-text works on YOUR recordings: give it a folder, get a table.
#
#   python3 speech_accuracy.py my-recordings                      Deepgram (needs your team's key)
#   python3 speech_accuracy.py my-recordings --engine mine        an engine you added to speech.py
#
# The folder holds sound files and a file "voices.json" that says what was really said:
#
#   [ {"file": "one.ogg", "text": "Turn on the light."},
#     {"file": "two.ogg", "text": "We need 42 chairs.", "group": "far away"} ]
#
# A sound file is an .ogg saved by audio_to_text.py (the band's own packets), or a .wav file
# (16000 samples a second, mono, 16 bit; reading it needs: python3 -m pip install av).
# "group" is optional; each group gets its own line.
#
# Every recording is played into the engine at the speed a band sends it, so a run takes
# as long as the recordings are. With Deepgram the recordings are SENT TO THAT COMPANY, and
# the time counts against your team's key: measure with a few short recordings.
#
# The score is the word error rate: the fewest words to change, add or remove to turn the
# heard text into the true text, divided by the number of true words. 0 % is perfect.
# Capital letters, punctuation and the word "and" do not count, and "42" equals "forty-two".
import argparse
import json
import os
import re
import sys
import time
import wave

ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen").split()
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def number_words(n: int) -> str:
    """42 -> "forty two", 2026 -> "two thousand twenty six" (0 to 999999)."""
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + (" " + ONES[n % 10] if n % 10 else "")
    if n < 1000:
        return ONES[n // 100] + " hundred" + (" " + number_words(n % 100) if n % 100 else "")
    return number_words(n // 1000) + " thousand" + (" " + number_words(n % 1000) if n % 1000 else "")


def words(text: str) -> list:
    """Text -> the words that are compared: lower case, no punctuation, numbers spelled out."""
    text = text.lower()
    text = re.sub(r"(\d),(\d{3})", r"\1\2", text)
    text = re.sub(r"\b\d{1,6}\b", lambda m: number_words(int(m.group())), text, flags=re.ASCII)
    text = text.replace("%", " percent ")
    text = re.sub(r"\band\b", " ", text, flags=re.ASCII)      # "one hundred and five" = "one hundred five"
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return text.replace("'", "").split()


def word_errors(reference: str, heard: str) -> tuple:
    """(wrong words, words in the reference)."""
    r, h = words(reference), words(heard)
    row = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        following = [i]
        for j in range(1, len(h) + 1):
            following.append(min(row[j] + 1, following[j - 1] + 1, row[j - 1] + (r[i - 1] != h[j - 1])))
        row = following
    return row[len(h)], len(r)


def read_sound(path):
    """A sound file -> (the band's packets or None, 16 kHz samples as bytes or None)."""
    from ogg_opus import read_packets
    if path.lower().endswith(".wav"):
        with wave.open(path, "rb") as f:
            if (f.getframerate(), f.getnchannels(), f.getsampwidth()) != (16000, 1, 2):
                raise SystemExit("%s must be 16000 samples a second, mono, 16 bit." % path)
            return None, f.readframes(f.getnframes())
    return read_packets(path), None


def through_engine(engine, packets, pcm, speed=1.0) -> str:
    """Play one recording into an engine from speech.py the way a band would: 50 pieces a second."""
    engine.start()
    pieces = packets if packets else [pcm[at:at + 640] for at in range(0, len(pcm), 640)]
    started = time.time()
    for count, piece in enumerate(pieces):
        if packets:
            engine.feed(piece, count & 0xFFFF)
        else:
            engine.feed_pcm(piece)
        wait = started + (count + 1) * 0.02 / speed - time.time()
        if wait > 0:
            time.sleep(wait)
    return engine.stop()


def measure(voices, sounds, to_text) -> dict:
    """Run every recording through to_text(packets, pcm). Returns {group: [wrong, words, sound s]}."""
    groups = {}
    for voice, (packets, pcm) in zip(voices, sounds):
        heard = to_text(packets, pcm)
        wrong, count = word_errors(voice["text"], heard)
        total = groups.setdefault(voice.get("group", "all"), [0, 0, 0.0])
        total[0] += wrong
        total[1] += count
        total[2] += len(packets) * 0.02 if packets else len(pcm) / 32000.0
        if wrong and ARGS.show:
            print("   %s: %d wrong. Heard: %s" % (voice["file"], wrong, heard))
    return groups


def main() -> None:
    import kit  # noqa: F401  (lets Python find the library)
    import speech
    with open(os.path.join(ARGS.folder, "voices.json"), encoding="utf-8") as f:
        voices = json.load(f)
    sounds = [read_sound(os.path.join(ARGS.folder, voice["file"])) for voice in voices]
    seconds = sum(len(k) * 0.02 if k else len(p) / 32000.0 for k, p in sounds)
    print("%d recordings, %.0f seconds of sound. This takes about %.0f seconds." % (len(voices), seconds, seconds / ARGS.speed))
    options = {"send": "pcm"} if ARGS.engine == "deepgram" and any(k is None for k, _p in sounds) else {}
    engine = speech.create(ARGS.engine, **options)
    groups = measure(voices, sounds, lambda packets, pcm: through_engine(engine, packets, pcm, ARGS.speed))
    print("%-12s %-36s %6s %6s %7s" % ("engine", "group", "wrong", "words", "errors"))
    for group, (wrong, count, _seconds) in groups.items():
        print("%-12s %-36s %6d %6d %6.1f%%" % (ARGS.engine, group[:36], wrong, count, 100.0 * wrong / max(1, count)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Word error rate of speech-to-text on your own recordings.")
    parser.add_argument("folder", help="folder with sound files and voices.json")
    parser.add_argument("--engine", default="deepgram", help="deepgram, or the name of an engine you added to "
                                                             "ENGINES in speech.py")
    parser.add_argument("--speed", type=float, default=1.0, help="play the recordings faster than a band does "
                                                                 "(an engine of your own may not mind; 1 is the band's pace)")
    parser.add_argument("--show", action="store_true", help="print what was heard when it is wrong")
    ARGS = parser.parse_args()
    try:
        main()
    except Exception as error:
        if type(error).__name__ not in ("SpeechError", "VexoError", "FileNotFoundError", "ValueError"):
            raise
        sys.exit("Problem: %s" % error)
