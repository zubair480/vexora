"""docs/deepgram.md, followed the way a student does: the printed programs are saved in
my-project/ and run as printed, against a pretend Deepgram on this computer with a made-up
key. So the page cannot drift away from code that works.

    python3 -m unittest discover -s examples/test

Two changes are made to the printed text, and only these: the pretend band (the page's own
"No band?" change), and the address wss://api.deepgram.com/v1/listen becomes the pretend
server's. For speed, "10 seconds" becomes about one. Nothing here reaches the internet.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from deepgram_mock import SERVER_WORDS, MockDeepgram, launcher  # noqa: E402

NODE = shutil.which("node")
REAL = "wss://api.deepgram.com/v1/listen"
SETTINGS = ("model=nova-3&language=en&encoding=opus&sample_rate=16000&channels=1"
            "&interim_results=true&smart_format=true&endpointing=100&mip_opt_out=true")
KEY = "k-test"                                       # made up; never a real key in a test
NO_KEY = {k: v for k, v in os.environ.items() if k != "DEEPGRAM_API_KEY"}
SIM = 'transport="sim"'


def read(page):
    with open(os.path.join(KIT, *page.split("/")), encoding="utf-8") as f:
        return f.read()


PAGE = read("docs/deepgram.md")
FLAT = re.sub(r"\s+", " ", PAGE)


def section(heading):
    start = PAGE.index("\n## %s\n" % heading)
    end = PAGE.find("\n## ", start + 1)
    return PAGE[start:end if end != -1 else None]


def block(heading, language):
    (code,) = re.findall(r"^```%s\n(.*?)^```$" % language, section(heading), re.M | re.S)
    return code


def node_version():
    if not NODE:
        return 0
    return int(subprocess.run([NODE, "--version"], stdout=subprocess.PIPE, universal_newlines=True).stdout.strip("v\n").split(".")[0])


class Programs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.project = os.path.join(cls.temp.name, "my-project")
        copy = os.path.join(cls.project, "vexo-open-kit")
        skip = shutil.ignore_patterns("__pycache__", "tests", "test")
        for part in ("python", "examples/python", "js/src"):
            shutil.copytree(os.path.join(KIT, *part.split("/")), os.path.join(copy, *part.split("/")), ignore=skip)
        os.makedirs(os.path.join(copy, "dashboard", "speech"))
        shutil.copy(os.path.join(KIT, "dashboard", "speech", "sample-speech.opus"), os.path.join(copy, "dashboard", "speech"))
        cls.examples = os.path.join(copy, "examples", "python")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def save(self, name, code):
        with open(os.path.join(self.project, name), "w", encoding="utf-8") as f:
            f.write(code)
        return os.path.join(self.project, name)

    def run_program(self, words, key=KEY):
        env = dict(NO_KEY, **({"DEEPGRAM_API_KEY": key} if key else {}))
        with tempfile.TemporaryDirectory() as elsewhere:        # started from any folder
            return subprocess.run(words, cwd=elsewhere, env=env, timeout=60, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

    def pretend_band(self, code):
        self.assertNotIn(SIM, code, "the printed program is for a real band")
        self.assertEqual(code.count("Band.connect(42)"), 1)
        return code.replace("Band.connect(42)", "Band.connect(42, %s)" % SIM).replace("band.wait(10)", "band.wait(1.3)")

    def test_the_short_python_program(self):
        code = block("Python", "python")
        self.assertTrue(code.startswith("import sys\nfrom pathlib import Path\nsys.path.insert(0, "))
        own = code[code.index("ears = "):].strip().splitlines()
        self.assertLessEqual(len(own), 10, "the page promises about ten lines")
        self.assertIn("Save it as `listen.py` in\n`my-project/`, set the key as above, and run `python3 listen.py`", PAGE.replace("save it", "Save it"))
        program = self.save("listen.py", self.pretend_band(code))
        with MockDeepgram() as server:
            done = self.run_program([sys.executable, "-c", launcher(server.url, self.examples), program])
        self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
        lines = done.stdout.splitlines()
        self.assertIn("is sent over the internet to Deepgram", lines[0])      # said first, before any sound
        self.assertIn("Sentence 1.", lines)
        self.assertRegex(lines[-1], r"^Sentence 1\..*The end\.$")
        self.assertNotIn(KEY, done.stdout)
        self.assertEqual(server.requests[0]["path"], "/v1/listen?" + SETTINGS)
        self.assertGreaterEqual(len(server.sound), 50)
        # with no key: one kind sentence and the line of the program, before the microphone goes on
        done = self.run_program([sys.executable, program], key=None)
        self.assertEqual((done.returncode, done.stdout), (1, ""), done.stderr)
        self.assertTrue(done.stderr.startswith("Problem (speech): Transcription needs a Deepgram key: see docs/deepgram.md. "), done.stderr)
        self.assertIn("(line 8 of listen.py)", done.stderr)
        self.assertNotIn("Traceback", done.stderr)

    def test_forwarding_from_your_own_python(self):
        code = block("From your own code", "python")
        self.assertEqual("".join(re.findall(r'"([^"]*)"', code[code.index("URL = ("):code.index("print(")])), REAL + "?" + SETTINGS)
        self.assertIn('headers={"Authorization": "Token " + os.environ["DEEPGRAM_API_KEY"]}', code)
        self.assertLess(code.index("print(\"Note: the band's microphone is sent to Deepgram"), code.index("WebSocket(URL"))
        with MockDeepgram() as server:
            program = self.save("forward.py", self.pretend_band(code).replace(REAL, server.url))
            done = self.run_program([sys.executable, program])
        self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
        self.assertEqual(done.stdout.splitlines()[0], "Note: the band's microphone is sent to Deepgram while this runs.")
        self.assertIn("Sentence 1.", done.stdout.splitlines())
        (request,) = server.requests
        self.assertEqual(request["path"], "/v1/listen?" + SETTINGS)
        self.assertEqual(request["headers"]["authorization"], "Token " + KEY)
        self.assertGreaterEqual(len(server.sound), 50)
        self.assertTrue(all(0 < len(packet) < 200 for packet in server.sound))  # one band packet in one message, not joined
        self.assertEqual(server.texts, [{"type": "CloseStream"}])
        # a refused key ends the program without the key or the server's words on the screen
        with MockDeepgram(refuse=401) as server:
            program = self.save("forward.py", self.pretend_band(code).replace(REAL, server.url))
            done = self.run_program([sys.executable, program])
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("status 401", done.stderr)
        self.assertNotIn(SERVER_WORDS, done.stdout + done.stderr)
        self.assertNotIn(KEY, done.stdout + done.stderr)

    @unittest.skipUnless(node_version() >= 22, "needs Node 22 or newer (it has WebSocket built in)")
    def test_forwarding_from_your_own_javascript(self):
        code = block("From your own code", "js")
        self.assertEqual("".join(re.findall(r"'([^']*)'", code[code.index("const URL"):code.index("const key")])), REAL + "?" + SETTINGS)
        self.assertIn("new WebSocket(URL, ['token', key])", code)               # the key is not part of the address
        self.assertIn("save it as `forward.mjs` in `my-project/` and run\n`node forward.mjs`", PAGE)
        with MockDeepgram() as server:
            program = self.save("forward.mjs", code.replace(REAL, server.url).replace("setTimeout(done, 10000)", "setTimeout(done, 1500)"))
            done = subprocess.run([NODE, program], cwd=self.project, env=dict(NO_KEY, DEEPGRAM_API_KEY=KEY), timeout=60,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
        self.assertEqual(done.stdout.splitlines()[0], "Note: the band's microphone is sent to Deepgram while this runs.")
        self.assertIn("Sentence 1.", done.stdout.splitlines())
        (request,) = server.requests
        self.assertEqual(request["path"], "/v1/listen?" + SETTINGS)
        self.assertEqual(request["headers"]["sec-websocket-protocol"], "token, " + KEY)
        self.assertNotIn(KEY, request["path"])
        self.assertGreaterEqual(len(server.sound), 50)
        self.assertEqual(server.texts[-1], {"type": "CloseStream"})


class Page(unittest.TestCase):
    def test_the_address_is_the_one_the_kit_uses(self):
        self.assertIn("\n    %s?%s\n" % (REAL, SETTINGS), PAGE)
        sys.path.insert(0, os.path.join(KIT, "examples", "python"))
        try:
            import speech
        finally:
            sys.path.pop(0)
        from urllib.parse import urlencode
        self.assertEqual(speech.DeepgramSpeech.URL + "?" + urlencode(speech.DeepgramSpeech().query), REAL + "?" + SETTINGS)
        # every address on the page that carries settings keeps "do not keep the sound"
        self.assertGreaterEqual(PAGE.count("mip_opt_out=true"), 5)
        self.assertNotIn("mip_opt_out=false", PAGE)

    def test_what_the_page_must_say(self):
        for words in (
                # what it is, and the rule about other people
                "the microphone's sound leaves your laptop", "goes to Deepgram, a company",
                "**solid blue light** means the microphone is on", "Tell the people near the band before you switch it on",
                "Without a key nothing is sent", "no human voice through a band has been measured yet",
                # the key: where from, where it goes, where never
                "Each team gets its own key at the event", "export DEEPGRAM_API_KEY=...", "set DEEPGRAM_API_KEY=...",
                "a file called **`.env`**", "add `.env` to its `.gitignore` **before** you create the file",
                "typed into the key field", "only that page can see it", "never another team's",
                "in your code, in a commit, in a web page you share or host, in a screenshot or a screen share, "
                "in a chat or a forum, in a log or a printed message, in the band's storage",
                "tell an organiser straight away", "you get a new one",
                # the settings for the band's sound
                "one packet in one binary message", '{"type": "KeepAlive"}', '{"type": "CloseStream"}',
                # the errors
                "Status 401", "Status 402", "Status 429", "blocks WebSockets", "phone's hotspot",
                "never the text Deepgram sent with it, and never the key"):
            self.assertIn(words, FLAT, words)
        for gone in ("parakeet", "whisper", "moonshine", "elevenlabs", "local model"):
            self.assertNotIn(gone, FLAT.lower())

    def test_the_measured_numbers_are_on_the_page(self):
        for words in ("7 (5.6 %)", "64 (27.8 %)", "0.5 to 1 second", "about 0.4 seconds", "56 of 64", "230 words"):
            self.assertIn(words, FLAT, words)

    def test_the_other_pages_lead_here(self):
        self.assertIn("(docs/deepgram.md)", read("README.md"))
        self.assertIn("(deepgram.md)", read("docs/speech.md"))
        self.assertIn("(deepgram.md)", read("docs/getting-started.md"))
        self.assertIn("(../docs/deepgram.md)", read("examples/README.md"))
        agents = re.sub(r"\s+", " ", read("AGENTS.md"))
        for words in ("`docs/deepgram.md`", "`DEEPGRAM_API_KEY`", "Never ask the team to paste the key into the chat with you, "
                      "and never write it into a file yourself", "make sure `.env` is in that project's `.gitignore`",
                      "`new WebSocket(url, ['token', key])`", "tell an organiser at once", "Keep `mip_opt_out=true`",
                      "Transcription needs a Deepgram key: see docs/deepgram.md", "sends the sound to Deepgram, a company"):
            self.assertIn(words, agents, words)

    def test_the_kit_keeps_a_dot_env_file_out_of_git(self):
        ignored = read(".gitignore").splitlines()
        self.assertIn(".env", ignored)
        self.assertIn(".env.*", ignored)

    def test_no_key_shaped_text_on_the_pages(self):
        # the pages show where a key goes with three dots, never with something that looks like one
        for page in ("docs/deepgram.md", "docs/speech.md", "AGENTS.md", "examples/README.md", "docs/getting-started.md"):
            for found in re.findall(r"DEEPGRAM_API_KEY=(\S+)", read(page)):
                self.assertTrue(found.startswith(("...", "your-key")), "%s: %s" % (page, found))


if __name__ == "__main__":
    unittest.main()
