""""Audio to text to AI" in AGENTS.md, followed the way a student does: the keys are set
the way the page says, the printed commands name real files, and the printed program is
saved in my-project/ and run as printed, against a pretend Deepgram and a pretend OpenAI
on this computer with made-up keys. Nothing here reaches the internet.

    python3 -m unittest discover -s examples/test
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(os.path.dirname(HERE))
PY = os.path.join(KIT, "examples", "python")
sys.path.insert(0, HERE)
from deepgram_mock import MockDeepgram  # noqa: E402
from openai_mock import MockOpenAI, launcher  # noqa: E402

NO_KEY = {k: v for k, v in os.environ.items() if k not in ("DEEPGRAM_API_KEY", "OPENAI_API_KEY", "OPENAI_MODEL")}
AI_KEY, EARS_KEY = "k-ai-test", "k-ears-test"                        # made up
HEADING = "Audio to text to AI"
ANCHOR = "#audio-to-text-to-ai"


def read(page):
    with open(os.path.join(KIT, *page.split("/")), encoding="utf-8") as f:
        return f.read()


AGENTS = read("AGENTS.md")
START = AGENTS.index("\n## %s\n" % HEADING)
SECTION = AGENTS[START:AGENTS.index("\n## ", START + 1)]
FLAT = re.sub(r"\s+", " ", SECTION)


class Page(unittest.TestCase):
    def test_it_is_near_the_top_and_has_its_steps_in_order(self):
        self.assertEqual(re.findall(r"^## (.*)$", AGENTS[:START], re.M), ["Rule 1: ask for the band number first"])
        self.assertEqual(re.findall(r"^### (.*)$", SECTION, re.M), [
            "1. Set your Deepgram key (sound to text)", "2. Set your OpenAI key (text to AI)",
            "3. Or keep both keys in a `.env` file", "4. Check both keys (30 seconds, no band needed)",
            "5. Run the three examples", "6. Build your own", "Ideas: motion + voice + AI", "Keep the keys safe",
            "For coding agents"])

    def test_one_copy_of_the_steps_and_the_other_pages_lead_to_it(self):
        self.assertIn("(AGENTS.md%s)" % ANCHOR, read("README.md"))
        for page in ("docs/getting-started.md", "docs/deepgram.md", "examples/README.md"):
            self.assertIn("(../AGENTS.md%s)" % ANCHOR, read(page), page)
        for page in ("README.md", "docs/getting-started.md", "docs/deepgram.md", "docs/speech.md", "docs/api.md", "examples/README.md"):
            self.assertNotRegex(read(page), r"(export|set) OPENAI_API_KEY|\$env:OPENAI", page)     # said once, in AGENTS.md

    def test_how_each_key_is_set(self):
        for name in ("DEEPGRAM_API_KEY", "OPENAI_API_KEY"):
            for line in ("    export %s=...  " % name, '    $env:%s = "..."  ' % name, "    set %s=...  " % name):
                self.assertIn(line, SECTION)
        for words in ("Each team gets its own Deepgram key and its own OpenAI key from the organisers.",
                      "**Deepgram key for Live transcript**", "**Forget key** removes it",
                      "It never goes into a web page", "An environment variable wins over the file.",
                      "put a line `.env` into its `.gitignore` **before** you make the file",
                      "never pasted into a chat: not with a person, and not with an AI coding agent",
                      "tell an organiser at once", "Deleting the line afterwards does not help",
                      "Never ask for a key, never write one into a file, never print one."):
            self.assertIn(words, FLAT, words)
        # the dashboard's words are the dashboard's
        with open(os.path.join(KIT, "dashboard", "index.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertIn("Deepgram key for Live transcript", page)
        self.assertIn("Forget key", page)

    def test_no_key_shaped_text_and_an_empty_form(self):
        for page in ("AGENTS.md", "README.md", "docs/getting-started.md", "docs/deepgram.md", "examples/README.md"):
            for found in re.findall(r"(?:DEEPGRAM|OPENAI)_API_KEY ?= ?(\S+)", read(page)):
                self.assertTrue(found.startswith(("...", '"..."', "your-key")), "%s: %s" % (page, found))
            self.assertNotRegex(read(page), r"\bsk-[A-Za-z0-9]", page)
        self.assertEqual(re.findall(r"^[^#\n].*$", read("env.example"), re.M), ["DEEPGRAM_API_KEY=", "OPENAI_API_KEY="])
        self.assertIn("cp vexo-open-kit/env.example .env", SECTION)
        ignored = read(".gitignore").splitlines()
        self.assertIn(".env", ignored)
        tracked = subprocess.run(["git", "-C", KIT, "ls-files", ".env", ".env.*"], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, universal_newlines=True).stdout
        self.assertEqual(tracked, "")

    def test_the_errors_on_the_page_are_the_programs_own_words(self):
        sys.path.insert(0, PY)
        try:
            import ai
            import speech
        finally:
            sys.path.remove(PY)
        with open(os.path.join(PY, "ai.py"), encoding="utf-8") as f:
            code = f.read()
        for words in ("Transcription needs a Deepgram key", "This needs an OpenAI key", "Could not reach api.openai.com",
                      "cannot check the certificate"):
            self.assertIn(words, SECTION)
            self.assertIn(words, code + speech.find_key.__code__.co_consts.__repr__())
        self.assertIn("insufficient_quota", code)
        for status in ("status 401", "status 402", "status 429, insufficient_quota", "status 404"):
            self.assertIn(status, SECTION)
        self.assertEqual(sorted(k for k in speech.REFUSED if k in (401, 402, 429)), [401, 402, 429])
        self.assertEqual(sorted(k for k in ai.REFUSED if k in (400, 401, 404, 429)), [400, 401, 404, 429])
        # the model's name is the one in the code, and the page says how to change it
        self.assertEqual(set(re.findall(r"gpt-[\w.-]*\w", SECTION)), {re.search(r'or "(gpt-[\w.-]+)"', code).group(1)})
        self.assertIn("set the environment variable `OPENAI_MODEL`", FLAT)
        # what the check prints, as the page shows it
        with open(os.path.join(PY, "check_keys.py"), encoding="utf-8") as f:
            check = f.read()
        for words in ("Deepgram key: works. It heard: ", "OpenAI key: works. The model "):
            self.assertIn("    " + words, SECTION)
            self.assertIn(words.replace("The model ", "The model %s"), check)
        self.assertIn("NOT working", SECTION)

    def test_every_command_names_a_real_example_and_each_has_a_way_with_no_band(self):
        listed = re.findall(r"`(python3 vexo-open-kit/[^`]+)`", SECTION) + re.findall(r"^ {4,}(python3 vexo-open-kit/.*)$", SECTION, re.M)
        self.assertGreaterEqual(len(listed), 8)
        for command in listed:
            path = command.split()[1]
            self.assertTrue(os.path.isfile(os.path.join(KIT, path[len("vexo-open-kit/"):])), command)
        for name in ("ask_the_band.py", "voice_commands.py", "meeting_notes.py"):
            self.assertIn("`python3 vexo-open-kit/examples/python/%s 42`" % name, SECTION)
            self.assertIn("`python3 vexo-open-kit/examples/python/%s --sim`" % name, SECTION)
        self.assertRegex(SECTION, r'(?m)^ +python3 vexo-open-kit/examples/python/voice_commands\.py --sim --say "[^"]+"$')
        self.assertRegex(SECTION, r"(?m)^ +python3 vexo-open-kit/examples/python/check_keys\.py$")

    def test_eight_to_ten_ideas_and_each_is_short(self):
        ideas = re.findall(r"^\d+\. \*\*.*$", SECTION[SECTION.index("### Ideas"):SECTION.index("### Keep the keys safe")], re.M)
        self.assertTrue(8 <= len(ideas) <= 10, len(ideas))
        for idea in ideas:
            self.assertLess(len(idea), 150, idea)

    def test_the_program_as_printed_runs_from_my_project(self):
        (code,) = re.findall(r"^```python\n(.*?)^```$", SECTION, re.M | re.S)
        self.assertIn("Save it as\n`talk.py` in `my-project/` and run `python3 talk.py`", SECTION)
        self.assertIn('# pretend band: Band.connect(BAND, transport="sim")', code)
        code = code.replace("Band.connect(BAND)", 'Band.connect(BAND, transport="sim")')    # the page's own change
        self.assertNotRegex(code, r"(?m)^[^#\n]*Band\.connect\(BAND\)")
        with MockOpenAI() as server, MockDeepgram() as ears, tempfile.TemporaryDirectory() as project:
            os.symlink(KIT, os.path.join(project, "vexo-open-kit"))
            with open(os.path.join(project, "talk.py"), "w", encoding="utf-8") as f:
                f.write(code)
            done = subprocess.run([sys.executable, "-c", launcher(server.url, PY, ears.url), "talk.py"], cwd=project,
                                  env=dict(NO_KEY, DEEPGRAM_API_KEY=EARS_KEY, OPENAI_API_KEY=AI_KEY), timeout=60,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
        self.assertTrue(done.stdout.startswith("Tap the band, then speak.\n"), done.stdout)
        self.assertRegex(done.stdout, r"(?m)^You said: Sentence 1\. .*(Sentence \d|The end)\.$")
        self.assertRegex(done.stdout, r"(?m)^Answer: You said: Sentence 1\. ")
        self.assertGreaterEqual(len(ears.sound), 200)                 # five seconds of the band's sound
        self.assertEqual(server.requests[0]["body"]["instructions"], "Answer in one or two short sentences.")
        # the four calls of the table exist under those names
        sys.path.insert(0, PY)
        try:
            import ai
            import speech
        finally:
            sys.path.remove(PY)
        for call in ("speech.listen", "ai.ask", "ai.choose", "ai.extract", "ai.tool", "ai.request"):
            self.assertIn("`%s" % call, SECTION)
            self.assertTrue(callable(getattr({"speech": speech, "ai": ai}[call.split(".")[0]], call.split(".")[1])), call)

    def test_listening_inside_a_handler_hears_nothing_as_the_page_warns(self):
        self.assertIn("Call `speech.listen` in the main program, never inside a handler.", FLAT)
        program = ("import sys; sys.path.insert(0, %r)\nimport speech\nfrom vexo_open.sync import Band\n"
                   "band = Band.connect(42, 'sim')\nheard = []\n"
                   "band.on('tap', lambda e: heard.append(speech.listen(band, 1.2)))\n"
                   "band.configure(events=True)\nband.wait(6.5)\nband.disconnect()\nprint('heard', heard)\n" % PY)
        with MockOpenAI() as server, MockDeepgram() as ears, tempfile.TemporaryDirectory() as project:
            with open(os.path.join(project, "inside.py"), "w", encoding="utf-8") as f:
                f.write(program)
            done = subprocess.run([sys.executable, "-c", launcher(server.url, PY, ears.url), "inside.py"], cwd=project,
                                  env=dict(NO_KEY, DEEPGRAM_API_KEY=EARS_KEY), timeout=60,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertIn("heard ['']", done.stdout, done.stderr)
        self.assertEqual(ears.sound, [])


if __name__ == "__main__":
    unittest.main()
