"""The one test that calls the real Deepgram. It is skipped unless you ask for it twice:
a key in DEEPGRAM_API_KEY, and VEXO_LIVE_DEEPGRAM=1. scripts/check.sh never runs it.

    VEXO_LIVE_DEEPGRAM=1 python3 -m unittest examples/test/test_deepgram_live.py

(with DEEPGRAM_API_KEY set in that terminal already). It sends the kit's own sample voice,
16 seconds of a public-domain reading, at the band's pace, and nothing else. The key is
read from the environment by speech.py and is never printed.
"""
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(KIT, "examples", "python"))
import speech  # noqa: E402
import speech_accuracy  # noqa: E402
from ogg_opus import read_packets  # noqa: E402

SAID = ("In a spell of dry weather, when the birds could find very little to drink, a thirsty crow found a pitcher "
        "with a little water in it. But the pitcher was high and had a narrow neck, and no matter how he tried, "
        "the crow could not reach the water.")
ASKED = bool(os.environ.get("DEEPGRAM_API_KEY")) and os.environ.get("VEXO_LIVE_DEEPGRAM") == "1"


@unittest.skipUnless(ASKED, "calls the real service: set DEEPGRAM_API_KEY and VEXO_LIVE_DEEPGRAM=1 to run it")
class Live(unittest.TestCase):
    def test_the_sample_voice_comes_back_as_text(self):
        packets = read_packets(os.path.join(KIT, "dashboard", "speech", "sample-speech.opus"))
        heard = []
        ears = speech.create().start(lambda text, final: heard.append((time.time(), final, text)))
        started = time.time()
        for count, packet in enumerate(packets):
            ears.feed(packet, count & 0xFFFF)
            wait = started + (count + 1) * 0.02 - time.time()
            if wait > 0:
                time.sleep(wait)
        text = ears.stop()
        wrong, words = speech_accuracy.word_errors(SAID, text)
        self.assertLessEqual(wrong, 4, "%d of %d words wrong: %s" % (wrong, words, text))
        guesses = [at for at, final, said in heard if not final and said]
        self.assertTrue(guesses and guesses[0] - started < 5, "no text arrived while the voice was speaking")
        self.assertNotIn(os.environ["DEEPGRAM_API_KEY"], text)


if __name__ == "__main__":
    unittest.main()
