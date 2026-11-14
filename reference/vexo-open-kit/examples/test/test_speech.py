"""Tests for examples/python/speech.py (the interface, the Deepgram engine), tiny_websocket.py
and speech_accuracy.py.

    python3 -m unittest discover -s examples/test

Nothing here talks to the internet and no real key is used: the engine is pointed at a
pretend Deepgram on this computer (deepgram_mock.py), with a made-up key. The one test
that calls the real service is in test_deepgram_live.py and is skipped unless asked for.
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import socket
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(os.path.dirname(HERE), "python")
KIT = os.path.dirname(os.path.dirname(PY))
sys.path.insert(0, PY)
sys.path.insert(0, HERE)
import speech  # noqa: E402
import speech_accuracy  # noqa: E402
import tiny_websocket  # noqa: E402
from deepgram_mock import SERVER_WORDS, MockDeepgram  # noqa: E402

HAS_AV = importlib.util.find_spec("av") is not None
SILENCE = bytes([0xBB, 0x41, 37]) + bytes(37)       # the packet the pretend band sends
KEY = "k-test"                                      # a made-up key (never a real one in a test)
NEEDS_KEY = "Transcription needs a Deepgram key: see docs/deepgram.md."
SETTINGS = ("model=nova-3&language=en&encoding=opus&sample_rate=16000&channels=1"
            "&interim_results=true&smart_format=true&endpointing=100&mip_opt_out=true")


@contextlib.contextmanager
def pretend_deepgram(key=KEY, **how):
    """speech.py talks to a pretend Deepgram on this computer, with a made-up key (or none)."""
    env = {k: v for k, v in os.environ.items() if k != speech.KEY_NAME}
    if key:
        env[speech.KEY_NAME] = key
    with MockDeepgram(**how) as server, mock.patch.object(speech.DeepgramSpeech, "URL", server.url), \
            mock.patch.dict(os.environ, env, clear=True), tempfile.TemporaryDirectory() as empty, \
            mock.patch.object(sys, "argv", [os.path.join(empty, "program.py")]), \
            contextlib.redirect_stdout(io.StringIO()) as printed, contextlib.redirect_stderr(io.StringIO()) as errors:
        before = os.getcwd()
        os.chdir(empty)                              # no .env of the person running the tests is read
        server.folder, server.printed, server.errors = empty, printed, errors
        try:
            yield server
        finally:
            os.chdir(before)


class Deepgram(unittest.TestCase):
    """The engine against a pretend Deepgram: what it sends, what it says, where the key goes."""

    def test_it_is_the_engine_of_the_kit(self):
        self.assertEqual(list(speech.ENGINES), ["deepgram"])
        self.assertIsInstance(speech.create(), speech.DeepgramSpeech)
        self.assertIsInstance(speech.create("deepgram", language="de"), speech.DeepgramSpeech)
        self.assertEqual(speech.DeepgramSpeech.URL, "wss://api.deepgram.com/v1/listen")

    def test_packets_go_out_as_they_are_and_text_comes_back(self):
        packets = [bytes([0xB8, n, n ^ 0x5A]) + bytes(n % 7) for n in range(60)]
        heard = []
        with pretend_deepgram() as server:
            ears = speech.create()
            self.assertIs(ears.start(lambda text, final: heard.append((text, final))), ears)
            for seq, packet in enumerate(packets):
                ears.feed(packet, seq)
            text = ears.stop()
            printed = server.printed.getvalue()
        self.assertEqual(server.sound, packets)                       # one packet, one message, unchanged
        self.assertEqual(server.texts, [{"type": "CloseStream"}])
        self.assertEqual(heard, [("Sent", False), ("Sentence 1.", True), ("The end.", True)])
        self.assertEqual(text, "Sentence 1. The end.")
        self.assertEqual(ears.text, text)
        # who gets the sound is said before any is sent
        self.assertIn("everything the band's microphone hears is sent over the internet to Deepgram", printed)
        self.assertIn("Tell the people near the band.", printed)

    def test_audio_events_are_taken_too(self):
        class Event:
            opus, seq = b"\xb8\x01\x02", 7
        with pretend_deepgram() as server:
            ears = speech.create().start()
            ears.feed(Event())
            ears.stop()
        self.assertEqual(server.sound, [Event.opus])

    def test_the_address_and_where_the_key_travels(self):
        with pretend_deepgram() as server:
            speech.create().start().stop()
        (request,) = server.requests
        self.assertEqual(request["path"], "/v1/listen?" + SETTINGS)
        self.assertEqual(request["headers"]["authorization"], "Token " + KEY)
        self.assertNotIn(KEY, request["path"])                        # never in the address
        self.assertIn("mip_opt_out=true", request["path"])

    def test_options_reach_the_address_but_the_fixed_ones_cannot_be_changed(self):
        with pretend_deepgram() as server:
            speech.create("deepgram", language="de", model="nova-2", endpointing=300, keyterm="Vexo").start().stop()
        path = server.requests[0]["path"]
        for part in ("model=nova-2", "language=de", "endpointing=300", "keyterm=Vexo", "mip_opt_out=true", "encoding=opus"):
            self.assertIn(part, path)
        # where the sound goes, the key and the "do not keep it" setting are not options
        for name in ("url", "key", "mip_opt_out", "callback", "callback_method", "encoding", "sample_rate"):
            with self.assertRaises(speech.SpeechError) as caught:
                speech.create("deepgram", **{name: "x"})
            self.assertIn("'%s' cannot be set here" % name, str(caught.exception))
        with self.assertRaises(speech.SpeechError):
            speech.create("deepgram", send="ogg")

    def test_without_a_key_it_says_so_kindly_and_sends_nothing(self):
        with pretend_deepgram(key=None) as server:
            with self.assertRaises(speech.SpeechError) as caught:
                speech.create().start()
            printed = server.printed.getvalue()
        said = str(caught.exception)
        self.assertTrue(said.startswith(NEEDS_KEY), said)
        for part in ("environment variable DEEPGRAM_API_KEY", "export DEEPGRAM_API_KEY=...", "set DEEPGRAM_API_KEY=...",
                     "a file called .env", "Never put it in your code or in git."):
            self.assertIn(part, said)
        self.assertEqual(caught.exception.code, "speech")
        self.assertEqual(server.requests, [])                          # no connection was opened
        self.assertEqual(printed, "")                                 # and no "the sound is sent" line

    def test_the_key_from_a_dot_env_file_in_the_folder_you_are_in(self):
        for line in ("DEEPGRAM_API_KEY=k-env\n", "# mine\nOTHER=1\nexport DEEPGRAM_API_KEY='k-env'\n",
                     ' DEEPGRAM_API_KEY = "k-env" \n'):
            with pretend_deepgram(key=None) as server:
                with open(os.path.join(server.folder, ".env"), "w", encoding="utf-8") as f:
                    f.write(line)
                speech.create().start().stop()
            self.assertEqual(server.requests[0]["headers"]["authorization"], "Token k-env", line)

    def test_the_environment_variable_wins_over_the_file(self):
        with pretend_deepgram(key="k-var") as server:
            with open(os.path.join(server.folder, ".env"), "w", encoding="utf-8") as f:
                f.write("DEEPGRAM_API_KEY=k-file\n")
            speech.create().start().stop()
        self.assertEqual(server.requests[0]["headers"]["authorization"], "Token k-var")

    def test_a_key_with_a_space_or_a_line_break_is_refused_before_anything_is_sent(self):
        for bad in ("k te st", "k\ttest", "k-t\r\nX-Other: 1", "k-tést"):
            with pretend_deepgram(key=bad) as server:
                with self.assertRaises(speech.SpeechError) as caught:
                    speech.create().start()
            self.assertIn("has a space or a strange character in it", str(caught.exception))
            self.assertNotIn(bad, str(caught.exception))
            self.assertEqual(server.requests, [])

    def test_a_refusal_shows_our_sentence_and_the_number_never_the_servers_words_or_the_key(self):
        expected = {401: "did not accept the key", 402: "no credit left. Tell an organiser", 403: "not allowed to use this model",
                    429: "Too many connections with this key", 400: "did not understand the settings",
                    500: "Deepgram refused the connection."}
        for status, words in expected.items():
            with pretend_deepgram(refuse=status) as server:
                with self.assertRaises(speech.SpeechError) as caught:
                    speech.create().start()
                shown = str(caught.exception) + server.printed.getvalue() + server.errors.getvalue()
            self.assertIn(words, shown)
            self.assertIn("(status %d)" % status, shown)
            self.assertNotIn(SERVER_WORDS, shown)
            self.assertNotIn(KEY, shown)

    def test_no_way_to_reach_the_service_is_one_sentence_with_a_hint(self):
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))                              # a port where nobody listens
            closed = "ws://127.0.0.1:%d/v1/listen" % taken.getsockname()[1]
        with pretend_deepgram(), mock.patch.object(speech.DeepgramSpeech, "URL", closed):
            with self.assertRaises(speech.SpeechError) as caught:
                speech.create().start()
        said = str(caught.exception)
        self.assertIn("Could not reach 127.0.0.1", said)
        self.assertIn("this network may block this kind of connection (a WebSocket)", said)
        self.assertNotIn(KEY, said)

    def test_an_error_message_from_the_service_is_not_repeated(self):
        with pretend_deepgram(error=True) as server:
            ears = speech.create().start()
            ears.feed(SILENCE, 0)
            time.sleep(0.2)
            text = ears.stop(wait=2)
            shown = text + server.printed.getvalue() + server.errors.getvalue()
        self.assertEqual(ears.error, "it reported an error")          # our words; the text that did arrive is kept
        self.assertEqual(text, "The end.")
        self.assertNotIn(SERVER_WORDS, shown)

    def test_a_connection_that_drops_keeps_the_text_so_far(self):
        heard = []
        with pretend_deepgram(drop_after=60) as server:
            ears = speech.create().start(lambda text, final: heard.append((text, final)))
            for seq in range(60):
                ears.feed(SILENCE, seq)
            self.assertTrue(server.closed.wait(5))
            time.sleep(0.2)
            ears.feed(SILENCE, 60)                                    # more sound after the drop: no crash
            text = ears.stop(wait=1)
            errors = server.errors.getvalue()
        self.assertEqual(text, "Sentence 1.")
        self.assertIn("Speech problem: Deepgram stopped listening (the connection closed). The text so far is kept.", errors)

    def test_it_says_it_is_still_there_while_no_sound_arrives(self):
        with pretend_deepgram() as server, mock.patch.object(speech.DeepgramSpeech, "KEEP_ALIVE", 0.2):
            ears = speech.create().start()
            time.sleep(1.6)                                           # the microphone is off: nothing is fed
            ears.stop()
        kinds = [message["type"] for message in server.texts]
        self.assertGreaterEqual(kinds.count("KeepAlive"), 1)
        self.assertEqual(kinds[-1], "CloseStream")
        self.assertEqual(server.sound, [])

    def test_plain_samples_need_the_pcm_form(self):
        with pretend_deepgram() as server:
            ears = speech.create().start()
            with self.assertRaises(speech.SpeechError) as caught:
                ears.feed_pcm(bytes(640))
            ears.stop()
            self.assertIn('create it with send="pcm"', str(caught.exception))
            ears = speech.create("deepgram", send="pcm").start()
            ears.feed_pcm(bytes(640))
            ears.feed_pcm(b"")
            ears.stop()
        self.assertEqual(server.sound, [bytes(640)])
        self.assertIn("encoding=linear16&sample_rate=16000&channels=1", server.requests[-1]["path"])

    @unittest.skipUnless(HAS_AV, "needs: python3 -m pip install av")
    def test_band_packets_are_decoded_for_the_pcm_form(self):
        with pretend_deepgram() as server:
            ears = speech.create("deepgram", send="pcm").start()
            for seq in range(5):
                ears.feed(SILENCE, seq)
            ears.stop()
        self.assertTrue(3200 - 64 <= len(b"".join(server.sound)) <= 3200)     # 5 packets of 20 ms as 16 bit samples

    def test_unknown_engine(self):
        with self.assertRaises(speech.SpeechError) as caught:
            speech.create("nothing")
        said = str(caught.exception)
        self.assertIn("Unknown speech engine 'nothing'", said)
        self.assertIn("The engines here are: deepgram.", said)
        self.assertIn('speech.ENGINES["my-name"] = MyClass', said)
        self.assertIn("speech.create(MyClass)", said)

    def test_no_speech_model_and_no_other_service_is_in_the_kit(self):
        for name in ("local", "elevenlabs", "whisper", "parakeet"):
            with self.assertRaises(speech.SpeechError):
                speech.create(name)
        for gone in ("speech_online.py", "speech_deepgram.py"):
            self.assertFalse(os.path.exists(os.path.join(PY, gone)), gone)
        with open(os.path.join(PY, "speech.py"), encoding="utf-8") as f:
            code = f.read()
        for word in ("whisper", "parakeet", "onnx", "moonshine", "huggingface", "elevenlabs", "load_model", "LocalSpeech"):
            self.assertNotIn(word, code.lower() if word.islower() else code, word)

    def test_the_code_has_one_address_and_reads_the_key_in_one_place(self):
        with open(os.path.join(PY, "speech.py"), encoding="utf-8") as f:
            code = f.read()
        self.assertEqual(re.findall(r"wss?://[^\s\"']+", code), ["wss://api.deepgram.com/v1/listen"])
        self.assertEqual(code.count("os.environ"), 1)
        self.assertNotRegex(code, r"print\([^)]*\bkey\b")


class Connection(unittest.TestCase):
    """tiny_websocket.py: the small connection helper."""

    def test_a_refusal_carries_the_number_only(self):
        with MockDeepgram(refuse=402) as server:
            with self.assertRaises(tiny_websocket.WebSocketError) as caught:
                tiny_websocket.WebSocket(server.url, headers={"Authorization": "Token " + KEY})
        self.assertEqual(caught.exception.status, 402)
        self.assertEqual(str(caught.exception), "The service refused the connection (status 402).")

    def test_a_line_break_in_a_header_is_refused(self):
        with MockDeepgram() as server:
            with self.assertRaises(tiny_websocket.WebSocketError):
                tiny_websocket.WebSocket(server.url, headers={"Authorization": "Token a\r\nHost: other"})
            time.sleep(0.1)
            self.assertEqual(server.requests, [])

    def test_frames_both_ways(self):
        sent = tiny_websocket.frame(2, bytes(range(200)), mask=True)
        at = [0]

        def read(n):
            at[0] += n
            return sent[at[0] - n:at[0]]
        self.assertEqual(tiny_websocket.read_frame(read), (True, 2, bytes(range(200))))
        self.assertEqual(tiny_websocket.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_a_message_that_is_too_long_is_not_read(self):
        head = bytes([0x82, 127]) + (tiny_websocket.MOST_MESSAGE + 1).to_bytes(8, "big")
        at = [0]

        def read(n):
            at[0] += n
            return head[at[0] - n:at[0]]
        with self.assertRaises(ValueError):
            tiny_websocket.read_frame(read)


class Fake(speech.Speech):
    """A team's own engine: counts the sound and says so."""
    name = "fake"

    def __init__(self, greeting="heard"):
        self.greeting = greeting

    def start(self, on_text=None):
        super().start(on_text)
        self.count = 0
        return self

    def feed_pcm(self, pcm):
        self.count += len(pcm)
        self._say("%s %d" % (self.greeting, self.count), final=False)

    def stop(self):
        self._say("%s %d bytes" % (self.greeting, self.count), final=True)
        return self.text


class YourOwn(unittest.TestCase):
    """Plug in your own engine: by a registered name, or by passing the class."""

    def run_engine(self, engine):
        heard = []
        self.assertIs(engine.start(lambda text, final: heard.append((text, final))), engine)
        engine.feed_pcm(bytes(640))
        engine.feed_pcm(bytes(640))
        return engine.stop(), heard

    def test_registered_by_name(self):
        speech.ENGINES["fake"] = Fake
        try:
            text, heard = self.run_engine(speech.create("fake", greeting="got"))
        finally:
            del speech.ENGINES["fake"]
        self.assertEqual(text, "got 1280 bytes")
        self.assertEqual(heard, [("got 640", False), ("got 1280", False), ("got 1280 bytes", True)])
        with self.assertRaises(speech.SpeechError):
            speech.create("fake")

    def test_passed_as_a_class(self):
        engine = speech.create(Fake)
        self.assertIsInstance(engine, Fake)
        self.assertEqual(engine.label, "fake")
        self.assertEqual(self.run_engine(engine)[0], "heard 1280 bytes")

    @unittest.skipUnless(HAS_AV, "needs: python3 -m pip install av")
    def test_band_packets_are_decoded_for_it(self):
        engine = speech.create(Fake).start()
        for seq in range(5):
            engine.feed(SILENCE, seq)
        engine.stop()                                 # 5 packets of 20 ms; the very first may be a few samples short
        self.assertTrue(3200 - 64 <= engine.count <= 3200, engine.count)


class Accuracy(unittest.TestCase):
    """speech_accuracy.py: the counting rule, and a folder of recordings through the engine."""

    def test_a_folder_of_recordings_through_the_engine(self):
        from ogg_opus import OggOpusWriter
        with pretend_deepgram() as server:
            folder = os.path.join(server.folder, "mine")
            os.mkdir(folder)
            ogg = OggOpusWriter(os.path.join(folder, "one.ogg"))
            for seq in range(50):
                ogg.write(seq, SILENCE)
            ogg.close()
            with open(os.path.join(folder, "voices.json"), "w", encoding="utf-8") as f:
                json.dump([{"file": "one.ogg", "text": "Sentence one."}], f)
            import argparse
            speech_accuracy.ARGS = argparse.Namespace(folder=folder, engine="deepgram", speed=50.0, show=True)
            speech_accuracy.main()
            printed = server.printed.getvalue()
        self.assertEqual(len(server.sound), 50)
        self.assertIn("1 recordings, 1 seconds of sound.", printed)
        self.assertRegex(printed, r"deepgram +all +0 +2 +0\.0%")

    def test_words(self):
        w = speech_accuracy.words
        self.assertEqual(w("The meeting starts at 9 30, room 318!"),
                         "the meeting starts at nine thirty room three hundred eighteen".split())
        self.assertEqual(w("One hundred and five; 1,500 cups & 42%"),
                         "one hundred five one thousand five hundred cups forty two percent".split())
        self.assertEqual(w("Don't  stop — it's 2026."), "dont stop its two thousand twenty six".split())
        self.assertEqual(w("16,000 times; 1234567 and 0"), "sixteen thousand times 1234567 zero".split())

    def test_word_errors(self):
        e = speech_accuracy.word_errors
        self.assertEqual(e("the quick brown fox", "The quick, brown fox."), (0, 4))
        self.assertEqual(e("the quick brown fox", "the quick round fox jumps"), (2, 4))
        self.assertEqual(e("turn on the light", ""), (4, 4))
        self.assertEqual(e("we need 42 chairs", "We need forty-two chairs"), (0, 5))


class Docs(unittest.TestCase):
    """docs/speech.md: the printed programs run, and the promises it must make are there."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(os.path.dirname(os.path.dirname(PY)), "docs", "speech.md"), encoding="utf-8") as f:
            cls.page = f.read()

    def blocks(self):
        import re
        return re.findall(r"^```python\n(.*?)^```$", self.page, re.M | re.S)

    def test_plug_in_your_own_service_runs_as_printed(self):
        code = [b for b in self.blocks() if "class MySpeech" in b]
        self.assertEqual(len(code), 1)
        self.assertLessEqual(len([line for line in code[0].splitlines() if line.strip()]), 20)
        # The first lines find speech.py from the program's own place in my-project/ (that
        # layout is run for real in test_docs.py). Here the program gets a place of its own
        # and `import speech` gives the module these tests already loaded.
        self.assertIn('sys.path.insert(0, str(Path(__file__).resolve().parent / "vexo-open-kit" / "examples" / "python"))\n'
                      'import speech\n', code[0])
        before = list(sys.path)
        names = {"__file__": os.path.join(tempfile.gettempdir(), "my-project", "mine.py")}
        import contextlib
        import io
        printed = io.StringIO()
        try:
            with contextlib.redirect_stdout(printed):
                exec(code[0], names)
        finally:
            sys.path[:] = before
        # the block shows itself working; the page states this output under it (test_docs.py runs it as a file)
        self.assertEqual(printed.getvalue(), "heard: 1.5 seconds of sound\n1.5 seconds of sound\n")
        self.assertIn("    heard: 1.5 seconds of sound\n    1.5 seconds of sound\n", self.page)
        heard = []
        engine = speech.create("mine")
        engine.start(lambda text, final: heard.append((text, final)))
        engine.feed_pcm(bytes(32000))
        engine.feed_pcm(bytes(16000))
        self.assertEqual(engine.stop(), "1.5 seconds of sound")
        self.assertEqual(heard, [("1.5 seconds of sound", True)])
        del speech.ENGINES["mine"]

    def test_every_python_block_is_real_python(self):
        for code in self.blocks():
            compile(code, "docs/speech.md", "exec")

    def test_what_the_page_must_say(self):
        page = " ".join(self.page.split())
        self.assertIn("## In the browser", self.page)
        self.assertNotIn("To be written", self.page)
        self.assertNotIn("PLACEHOLDER", self.page)
        for needed in ("solid blue", "records everyone near the band", "BandAudio",
                       "the sound leaves your computer and goes to that company", "(deepgram.md)",
                       "dashboard/speech/README.md", "python3 -m pip install av", "environment variable", "`.env`",
                       "export DEEPGRAM_API_KEY=...", "set DEEPGRAM_API_KEY=...", NEEDS_KEY[:-1],
                       'speech.ENGINES["mine"] = MySpeech', "speech.create(MySpeech)", "--text no",
                       "tell an organiser"):
            self.assertIn(needed, page)
        for place in ("in your code", "in a commit", "in a log", "in the band's storage"):   # where a key never goes
            self.assertIn(place, page)
        served = [line for line in self.page.splitlines() if "http.server" in line]
        self.assertTrue(served)
        for line in served:
            self.assertIn("--bind 127.0.0.1", line)

    def test_no_speech_model_and_no_other_service_is_on_the_page(self):
        """Deepgram is the one speech-to-text of the kit: no page points at a path that is gone."""
        for name in ("README.md", "AGENTS.md", "docs/speech.md", "docs/deepgram.md", "docs/api.md",
                     "docs/getting-started.md", "examples/README.md"):
            with open(os.path.join(KIT, *name.split("/")), encoding="utf-8") as f:
                page = f.read().lower()
            for gone in ("parakeet", "whisper", "moonshine", "onnx", "hugging", "elevenlabs", "speech_online",
                         "speech_deepgram", "--model", "--engine deepgram", "speech_key", 'create("local"'):
                self.assertNotIn(gone, page, "%s still says %s" % (name, gone))


if __name__ == "__main__":
    unittest.main()
