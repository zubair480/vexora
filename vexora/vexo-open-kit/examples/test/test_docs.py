"""Follows the docs the way a student does: copies the printed programs and commands out of
the markdown files and runs them exactly as printed, in the folder the page names. For a
student that folder is my-project/, with the kit inside it as vexo-open-kit/: the tests
build exactly that (a copy, in a temporary place) and run there. Only the commands the
pages mark as "for the kit's maintainers" run in the kit folder itself.

    python3 -m unittest discover -s examples/test

The one change made to the printed text is the one the pages themselves describe for
"No band?": Band.connect(42) becomes Band.connect(42, transport="sim"). So nothing here
ever touches Bluetooth or a USB port. If a page changes into something that does not run,
these tests fail.
"""
import glob
import importlib.util
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NODE = shutil.which("node")
PAGES = ["README.md", "AGENTS.md", "docs/getting-started.md", "docs/api.md", "docs/speech.md", "docs/deepgram.md", "examples/README.md",
         "docs/bluetooth-contract.md",
         "contract/README.md", "contract/contract.md"]
# the pages a student (or a student's AI agent) works from
STUDENT_PAGES = ["README.md", "AGENTS.md", "docs/getting-started.md", "docs/api.md", "docs/speech.md", "docs/deepgram.md",
                 "docs/bluetooth-contract.md",
                 "examples/README.md"]
SIM = 'transport="sim"'
VOICE = "dashboard/speech/sample-speech.opus"     # the recording the pages name as the pretend band's voice
TEMP = None
PROJECT = None      # my-project/
KIT_COPY = None     # my-project/vexo-open-kit/


def setUpModule():
    """my-project/ with a copy of the kit inside it, as every page describes: the parts of
    the kit the printed programs and commands use. The real kit gets no new files."""
    global TEMP, PROJECT, KIT_COPY
    TEMP = tempfile.TemporaryDirectory()
    PROJECT = os.path.join(TEMP.name, "my-project")
    KIT_COPY = os.path.join(PROJECT, "vexo-open-kit")
    skip = shutil.ignore_patterns("__pycache__", "tests", "test", "*.ogg", "vendor", "speech")
    for part in ("python", "examples/python", "examples/node", "examples/web", "js/src", "dashboard"):
        shutil.copytree(os.path.join(KIT, *part.split("/")), os.path.join(KIT_COPY, *part.split("/")), ignore=skip)
    os.makedirs(os.path.join(KIT_COPY, "dashboard", "speech"))
    shutil.copy(os.path.join(KIT, *VOICE.split("/")), os.path.join(KIT_COPY, *VOICE.split("/")))
    sys.path.insert(0, os.path.join(KIT, "python"))     # for the tests that ask the library itself


def tearDownModule():
    sys.path.remove(os.path.join(KIT, "python"))
    TEMP.cleanup()


def read(page):
    with open(os.path.join(KIT, page), encoding="utf-8") as f:
        return f.read()


def blocks(text, language):
    """Every fenced code block of one language: (start, end, code)."""
    found = re.finditer(r"^```%s\n(.*?)^```$" % language, text, re.M | re.S)
    return [(m.start(), m.end(), m.group(1)) for m in found]


def section(text, heading):
    """The text under one heading, up to the next heading of the same or a higher level."""
    m = re.search(r"^(#+) %s[^\n]*\n" % re.escape(heading), text, re.M)
    assert m, "heading %r is gone" % heading
    end = re.search(r"^#{1,%d} " % len(m.group(1)), text[m.end():], re.M)
    return text[m.end():m.end() + end.start()] if end else text[m.end():]


def commands(text):
    """The command lines of a page: the lines printed indented, on their own."""
    lines = re.findall(r"^ {4,}((?:python3|node|PYTHONPATH=|\(cd ).*)$", text, re.M)
    return [line.strip() for line in lines]


def pretend(code):
    """The documented "No band?" change, and proof that no other kind of connect is left."""
    code = re.sub(r"Band\.connect\((\w+)\)", r"Band.connect(\1, %s)" % SIM, code)
    code = re.sub(r"Band\.connect\((\w+), (?!transport)", r"Band.connect(\1, %s, " % SIM, code)
    for call in re.findall(r"^[^#\n]*?(Band\.connect\([^)]*\))", code, re.M):
        assert SIM in call, "this connect would look for a real band: " + call
    return code


def run(command, cwd, timeout=60):
    """Run one printed command in `cwd`. `python3` and `node` mean the ones running the
    tests; NAME=value words in front become environment variables (as a shell would)."""
    words = shlex.split(command)
    env = {k: v for k, v in os.environ.items()                                # no test uses a real key
           if k not in ("DEEPGRAM_API_KEY", "OPENAI_API_KEY", "OPENAI_MODEL")}
    while "=" in words[0] and not words[0].startswith(("-", "/", ".")):
        name, value = words.pop(0).split("=", 1)
        env[name] = value
    words[0] = {"python3": sys.executable, "node": NODE}[words[0]]
    return subprocess.run(words, cwd=cwd, env=env, timeout=timeout, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)


class Programs(unittest.TestCase):
    """The printed Python programs, saved where the page says and started as it says."""

    @classmethod
    def setUpClass(cls):
        cls.project, cls.kit = PROJECT, KIT_COPY      # my-project/ and my-project/vexo-open-kit/

    def save_and_run(self, folder, name, code, command, cwd):
        self.assertNotIn(SIM, re.sub(r"#.*", "", code), "the printed program is for a real band")
        with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
            f.write(pretend(code))
        done = run(command, cwd)
        self.assertEqual(done.returncode, 0, "%s\n%s\n%s" % (command, done.stdout, done.stderr))
        self.assertEqual(done.stderr, "")
        return done.stdout

    def test_the_program_as_printed_needs_the_pretend_band_change(self):
        # Guards this file: without the documented change the program would use Bluetooth.
        with self.assertRaises(AssertionError):
            pretend("band = Band.connect(42, transport=\"ble\")\n")
        self.assertEqual(pretend("b = Band.connect(BAND)   # x\n"), "b = Band.connect(BAND, %s)   # x\n" % SIM)
        self.assertEqual(pretend("Band.connect(42, auto_reconnect=True)\n"),
                         "Band.connect(42, %s, auto_reconnect=True)\n" % SIM)

    PATH_LINES = ('import sys\n'
                  'from pathlib import Path\n'
                  'sys.path.insert(0, str(Path(__file__).resolve().parent / "vexo-open-kit" / "python"))')

    def first_program(self):
        """(text of the section, the printed program, the printed command) of the guide's first program."""
        text = section(read("docs/getting-started.md"), "Write your own program")
        (start, end, code), = blocks(text, "python")
        # the page names the file, the folder and the command; take all three from it
        named = re.search(r"Save these lines as `([\w.]+)` in `my-project/`:\s*$", text[:start])
        self.assertTrue(named, "the page no longer says where to save the first program")
        command, = commands(text[end:])
        self.assertEqual(command, "python3 %s" % named.group(1))
        return text, named.group(1), code, command

    def test_getting_started_first_program(self):
        text, name, code, command = self.first_program()
        # the layout: the program in my-project/, the kit inside it, under exactly this name
        for line in ("    my-project/", "      vexo-open-kit/", "      first.py"):
            self.assertIn(line, text)
        setup = read("docs/getting-started.md")
        self.assertIn("unpack it inside\n   `my-project`", setup)
        self.assertIn("must be called\n   exactly `vexo-open-kit`", setup)
        # one home for the terminal: my-project/. No hopping into the kit folder and back.
        self.assertIn("In the same terminal (it is in `my-project/`), start the program:\n\n    python3 first.py", text)
        # (the one `cd ..` on the page is the cure in the problem table, for a terminal inside the kit)
        self.assertNotIn("cd ..", setup.split("## When something does not work")[0])
        self.assertIn("**Open a terminal in `my-project`**: the folder that holds the kit, not the kit folder\n   itself.", setup)
        self.assertIn("Every command on this page is typed there, in `my-project/`.", setup)
        self.assertIn('Change the connect line to `band = Band.connect(42, transport="sim")`', text)
        self.assertTrue(code.startswith(self.PATH_LINES), "the first three lines find the library from the file's own place")
        out = self.save_and_run(self.project, name, code, command, self.project)
        self.assertEqual(out, "Connected to Vexo-HACK-42\n")
        self.assertIn("It prints `Connected to Vexo-HACK-42`", text)

    def test_first_program_runs_from_any_folder(self):
        # the path to the library is built from the file's place, not from where it is started
        text, _name, code, _command = self.first_program()
        self.assertIn("They work from whatever folder you\nstart the program in", text)
        with tempfile.TemporaryDirectory() as elsewhere:
            path = os.path.join(self.project, "first_from_elsewhere.py")
            out = self.save_and_run(self.project, "first_from_elsewhere.py", code, "python3 " + shlex.quote(path), elsewhere)
            self.assertEqual(out, "Connected to Vexo-HACK-42\n")
        out = self.save_and_run(self.project, "first_from_kit.py", code, "python3 ../first_from_kit.py", self.kit)
        self.assertEqual(out, "Connected to Vexo-HACK-42\n")

    def test_first_program_in_another_layout_fails_the_way_the_page_says(self):
        text, _name, code, _command = self.first_program()
        self.assertIn("Without them, or when the kit folder has another name or is in\nanother place, "
                      "Python says `No module named 'vexo_open'`", text)
        rest = code[len(self.PATH_LINES):].split("\n", 1)[1]
        with tempfile.TemporaryDirectory() as beside:            # the program next to the kit, not above it
            for name, program in (("no_lines.py", rest), ("beside.py", code)):
                folder = self.project if name == "no_lines.py" else beside
                with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
                    f.write(pretend(program))
                done = run("python3 " + name, folder)
                self.assertIn("No module named 'vexo_open'", done.stderr, name)
        self.assertIn("| `No module named 'vexo_open'` | Python cannot find the library. Your program must be in "
                      "`my-project/`, the kit folder inside it must be called exactly `vexo-open-kit`",
                      read("docs/getting-started.md"))

    def test_getting_started_handler_asks_the_band_and_gets_an_answer(self):
        # the handler sample goes into first.py where the page says; get_led() inside the handler answers
        _text, _name, code, _command = self.first_program()
        text = section(read("docs/getting-started.md"), "Reacting to the band: handlers")
        (_s, end, lines), = blocks(text, "python")
        self.assertIn("Put these lines in `first.py` in place of the line `band.wait(3)`.", text[end:])
        self.assertIn("led = band.get_led()         # asking the band inside a handler works", lines)
        self.assertEqual(code.count("band.wait(3)                     # keep it on for 3 seconds\n"), 1)
        program = code.replace("band.wait(3)                     # keep it on for 3 seconds\n", lines)
        out = self.save_and_run(self.project, "first_handler.py", program, "python3 first_handler.py", self.project)
        self.assertEqual(out, "Connected to Vexo-HACK-42\ntap! the light is solid\n")
        for rule in ("Handlers run one at a time", "A handler may call the band and use the answer",
                     "While one handler runs, the next events wait", "An error in a handler is printed"):
            self.assertIn(rule, text)
        api = section(read("docs/api.md"), "Handlers: the rules")
        for rule in ("one at a time, in the order the events arrived", "`led = band.get_led()`",
                     "the next events wait in line", 'An error in a handler is printed (`Error in your "tap" handler:`',
                     "`band.disconnect()` in a handler ends `band.wait()`", "`async def on_tap(e): led = await band.get_led()`"):
            self.assertIn(rule, api)
        self.assertIn("17. **Python handlers run one at a time on a background thread.**", read("AGENTS.md"))

    def test_api_programs(self):
        # docs/api.md uses the same layout as every other page: programs in my-project/, the
        # same three lines at the top, started there.
        page = read("docs/api.md")
        where = section(page, "Where your code goes")
        for line in ("    my-project/", "      vexo-open-kit/", "      try.py"):
            self.assertIn(line, where)
        named = re.search(r"Save it\s+as `([\w.]+)` in `my-project/` and run it there", where)
        self.assertTrue(named, "the page no longer says where to save a program")
        name = named.group(1)
        self.assertIn("Type every command in `my-project/`.", where)
        command = [c for c in commands(where) if c.startswith("python3")]
        self.assertEqual(command, ["python3 %s" % name])
        self.assertIn('Write\n`Band.connect(42, transport="sim")`', where)
        self.assertNotIn("import kit", page)                    # the old advice: save it in the kit's examples/python/
        self.assertNotIn("examples/python/try.py", page)
        whole = [code for _s, _e, code in blocks(page, "python") if code.startswith(self.PATH_LINES)]
        self.assertEqual(len(whole), 4, "the whole programs on the page start with the three lines that find the library")
        real = [code for code in whole if SIM not in code and "SimTransport(" not in code]   # the two for a real band
        self.assertEqual(len(real), 2)
        outs = [self.save_and_run(self.project, name, code, command[0], self.project) for code in real]
        self.assertRegex(outs[0], r"^LED is \{.*'g': 255.*\}\n$")
        self.assertEqual(outs[1], "tap!\n")      # the pretend band taps itself after 4 seconds
        # every other block that imports the library is lines for such a program: it must not
        # set a path of its own, and no block may depend on the folder it is started in
        for _s, _e, code in blocks(page, "python"):
            if "sys.path" in code:
                self.assertTrue(code.startswith(self.PATH_LINES), "another way to find the library:\n" + code)
        self.assertEqual(real[0].splitlines()[:3], self.first_program()[2].splitlines()[:3])
        # near the top, once and clearly: how the `await` lines of the tables read in a plain program
        top = page[:page.index("## Where your code goes")]
        for words in ("**Python on this page is the plain form**", "(`from vexo_open.sync import Band`)",
                      '    band.set_led("#00ff00")\n    led = band.get_led()',
                      "**The async form, said once.**", "(`from vexo_open import Band`)",
                      '    await band.set_led("#00ff00")\n    led = await band.get_led()',
                      "`SyntaxError: 'await' outside function`", "[Python without async](#python-without-async)"):
            self.assertIn(words, top)
        self.assertLess(top.index("**Python on this page is the plain form**"), top.index("**The async form, said once.**"))

    def test_api_python_is_the_plain_form_everywhere_below_the_one_note(self):
        # A line copied from the page into a plain program must not be a SyntaxError: below
        # the note at the top, no Python block and no Python cell of a table says `await`.
        page = read("docs/api.md")
        below = page[page.index("## Where your code goes"):]
        for _s, _e, code in blocks(below, "python"):
            self.assertNotRegex(code, r"\bawait\b|\basync\b", "a Python block in the async form:\n" + code)
            compile(code, "docs/api.md", "exec")     # and it is Python
        cells = 0
        for row in re.findall(r"^\|.*\|$", below, re.M):
            cell = row.strip("|").split(" | ")
            if len(cell) == 3 and re.search(r"band\.\w+\(|Band\.connect\(", cell[2]):    # What | JavaScript | Python
                cells += 1
                self.assertNotIn("await", cell[2], row)
        self.assertGreaterEqual(cells, 12)
        # what the note says happens, happens
        with open(os.path.join(self.project, "awaited.py"), "w", encoding="utf-8") as f:
            f.write(self.PATH_LINES + '\nfrom vexo_open.sync import Band\nband = Band.connect(42, transport="sim")\n'
                                      'await band.set_led("#00ff00")\n')
        done = run("python3 awaited.py", self.project)
        self.assertIn("SyntaxError: 'await' outside function", done.stderr)
        # the async form the note describes exists and works with the same names
        with open(os.path.join(self.project, "async_form.py"), "w", encoding="utf-8") as f:
            f.write(self.PATH_LINES + """
import asyncio
from vexo_open import Band

async def main():
    band = await Band.connect(42, transport="sim")
    await band.set_led("#00ff00")
    led = await band.get_led()
    print(led.g)
    await asyncio.sleep(0.1)
    await band.disconnect()

asyncio.run(main())
""")
        done = run("python3 async_form.py", self.project)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "255\n", ""))

    def test_api_python_lines_about_configure_sound_fast_motion_and_time_run_as_printed(self):
        # The short Python blocks are lines "to put into a program". Each one is put into the
        # smallest plain program around it (the three path lines, the import, a connect to
        # the pretend band, the lines, a moment for events, disconnect) and run in my-project/.
        page = read("docs/api.md")
        head = self.PATH_LINES + '\nfrom vexo_open.sync import Band\nband = Band.connect(42, transport="sim")\n'
        tail = "band.wait(0.3)\nband.disconnect()\n"

        def lines_of(heading):
            found = [code for _s, _e, code in blocks(section(page, heading), "python") if not code.startswith(self.PATH_LINES)]
            for code in found:
                self.assertNotIn("Band.connect(", code)
            return found

        def go(name, code):
            with open(os.path.join(self.project, name), "w", encoding="utf-8") as f:
                f.write(head + code + tail)
            return run("python3 " + name, self.project)

        configure, intervals = lines_of("Turning data on: `configure`")
        for i, code in enumerate((configure, intervals)):
            done = go("lines_configure%d.py" % i, code + "print(sorted(k for k, on in band.streams.items() if on))\n")
            self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
            self.assertEqual(done.stdout, "%s\n" % (["battery", "events", "motion"], ["battery", "mic_level"])[i])

        fast, limits = lines_of("Fast motion")
        done = go("lines_fast.py", fast)
        self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
        samples = [line.split() for line in done.stdout.strip().split("\n")]
        self.assertGreater(len(samples), 50)
        self.assertEqual([int(s[0]) for s in samples], list(range(len(samples))))     # n counts 0, 1, 2, ...
        self.assertTrue(any("." in s[1] and not s[1].endswith(".0") for s in samples))   # t has a fraction
        self.assertTrue(all(-0.13 < float(s[2]) < 0.13 for s in samples))             # ax of the gentle swing
        done = go("lines_limits.py", limits)
        self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
        self.assertRegex(done.stdout, r"'accel': 833, 'gyro': 833, 'both': 416, 'transport': 'sim'")
        self.assertTrue(done.stdout.endswith("\n833\n"), done.stdout)

        (clock,) = lines_of("Time")
        done = go("lines_time.py", clock)
        self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
        said = re.match(r"^\{'time': (\d+\.\d+), 'set': True, 't': \d+, 'source': 'band'\}\nband\n$", done.stdout)
        self.assertTrue(said, done.stdout)
        self.assertLess(abs(float(said.group(1)) - __import__("time").time()), 60)

        (sound,) = lines_of("Sound: from Opus packets to samples")
        self.assertIn("from vexo_open.audio import PcmDecoder\n", sound)
        done = go("lines_sound.py", sound)
        if importlib.util.find_spec("av"):
            self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
            out = done.stdout.split("\n")
            self.assertEqual(out[0], "Loading the sound decoder...")
            self.assertEqual(out[1], "608 bytes of samples")      # "the very first feed() returns 304 samples"
            self.assertIn("640 bytes of samples", out)
        else:       # no `av`: one sentence with the install command the page names, and the line of the program
            self.assertEqual(done.returncode, 1)
            self.assertEqual(done.stdout, "Loading the sound decoder...\n")
            problem = done.stderr.splitlines()
            self.assertEqual(problem[0], "Problem (not-available): Decoding the band's sound needs the 'av' package. Install "
                                         "it once with:  python3 -m pip install av (on Windows: python -m pip install av)")
            self.assertRegex(problem[1], r"^  \(line \d+ of lines_sound\.py\)$")
            self.assertEqual(len(problem), 2)

    def test_api_voice_for_the_pretend_band(self):
        # "A voice for the pretend band": the Python program as printed, in my-project/, with the file the page names
        part = section(read("docs/api.md"), "A voice for the pretend band")
        self.assertIn("`%s`" % VOICE, part)
        self.assertTrue(os.path.isfile(os.path.join(KIT, *VOICE.split("/"))))
        (program,) = [code for _s, _e, code in blocks(part, "python")]
        self.assertTrue(program.startswith(self.PATH_LINES))
        self.assertIn('/ "vexo-open-kit" / "dashboard" / "speech" / "sample-speech.opus"', program)
        self.assertIn('Band.connect(42, transport="sim", audio_packets=audio.ogg_packets(voice))', program)
        with open(os.path.join(self.project, "voice.py"), "w", encoding="utf-8") as f:
            f.write(program)
        with tempfile.TemporaryDirectory() as elsewhere:           # started from another folder: the paths come from __file__
            done = run("python3 " + shlex.quote(os.path.join(self.project, "voice.py")), elsewhere)
        self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
        said = re.match(r"^(\d+) packets of a voice; the first one is (\d+) bytes\n$", done.stdout)
        self.assertTrue(said, done.stdout)
        self.assertTrue(90 <= int(said.group(1)) <= 120, said.group(1))   # 2 seconds, 50 a second (and what came while it disconnected)
        sys.path.insert(0, os.path.join(KIT, "python"))
        try:
            from vexo_open import audio
            packets = audio.ogg_packets(os.path.join(KIT, *VOICE.split("/")))
        finally:
            sys.path.remove(os.path.join(KIT, "python"))
        self.assertEqual(int(said.group(2)), len(packets[0]))             # the file's own packets, not silence
        self.assertIn("16\nseconds of a person reading aloud", part)
        self.assertTrue(15.5 < len(packets) / 50 < 16.5)
        # the same file and option are named where a student looks for sound: speech.md, the examples page, AGENTS.md
        for page, words in (("docs/speech.md", "audio_packets=audio.ogg_packets(voice)"),
                            ("docs/speech.md", "--replay vexo-open-kit/%s" % VOICE),
                            ("examples/README.md", "--replay vexo-open-kit/%s" % VOICE),
                            ("examples/README.md", "`audio_packets=`"),
                            ("AGENTS.md", "`%s`" % VOICE),
                            ("AGENTS.md", 'Band.connect(BAND, transport="sim", audio_packets=audio.ogg_packets(FILE))'),
                            ("AGENTS.md", "new SimTransport(BAND, { audioPackets: oggPackets(bytes) })")):
            self.assertIn(words, read(page), page)
        self.assertNotIn("some-speech.ogg", read("examples/README.md"))

    def test_replay_command_of_the_pages_plays_the_voice_file(self):
        # the one command docs/speech.md and examples/README.md print for "no band": as printed, in my-project/
        printed = "python3 vexo-open-kit/examples/python/audio_to_text.py --sim --seconds 16 --replay vexo-open-kit/%s" % VOICE
        from vexo_open import audio
        packets = len(audio.ogg_packets(os.path.join(KIT, *VOICE.split("/"))))
        for page in ("docs/speech.md", "examples/README.md"):
            self.assertIn("    %s\n" % printed, read(page), page)
            # the printed run covers the whole recording, and the page says why the text does not
            # begin a second time (test_examples.py proves the example stops at the end of the file)
            for seconds in re.findall(r"--seconds (\d+) --replay", read(page)):
                self.assertGreaterEqual(int(seconds) * 50, packets, page)
            self.assertIn("`audio_to_text.py` listens to it once", read(page), page)
        with tempfile.TemporaryDirectory() as project:              # recording.ogg lands in the folder you are in
            os.symlink(self.kit, os.path.join(project, "vexo-open-kit"))
            done = run(printed.replace("--seconds 16", "--seconds 1") + " --text no", project)
            self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
            self.assertEqual(sorted(os.listdir(project)), ["recording.ogg", "vexo-open-kit"])
        self.assertNotIn("sends silence", done.stdout)
        self.assertNotIn("Speak now", done.stdout)
        self.assertIn("seconds of sound to recording.ogg", done.stdout)

    def lines_program(self, lines):
        """Lines "to put into a program": under the first lines of the AGENTS.md program
        (the path, the import, the band number), in my-project/."""
        text = section(read("AGENTS.md"), "Shortest working program: Python")
        (_s, _e, code), = blocks(text, "python")
        head = code[:code.index("band = Band.connect(BAND)")]
        self.assertEqual(head.strip().splitlines()[-1].split("#")[0].strip(), "BAND = 7")
        return head + lines

    def test_api_pretend_band_lines(self):
        # The Python block of "The pretend band" is a whole program in the my-project/
        # layout: it runs exactly as printed. It states one rule for which object to call
        # (band.transport, always) and follows it.
        part = section(read("docs/api.md"), "The pretend band: making things happen")
        lines = [code for _s, _e, code in blocks(part, "python")]
        self.assertEqual(len(lines), 1)
        program = lines[0]
        self.assertTrue(program.startswith(self.PATH_LINES + "   # where the library is\nfrom vexo_open.sync import Band\n"
                                           "from vexo_open.transports.sim import SimTransport\n"))
        self.assertTrue(program.endswith("band.disconnect()\n"))
        self.assertIn("save it as `try.py` in `my-project/`", part)
        self.assertIn("sim = band.transport ", program)
        self.assertEqual(re.findall(r"^(\w+) = SimTransport\(", program, re.M), [], "the raw object gets no name to call it by")
        for call in re.findall(r"\b(\w+(?:\.\w+)?)\.(?:tap|after|set_loudness|play_loudness)\(", program):
            self.assertEqual(call, "sim")
        flat = re.sub(r"\s+", " ", part)
        for words in ("**One rule for which object to call.**",
                      "direct the pretend band through `band.transport`, always: in the main program, in a handler, and in a "
                      "function you give to `after`",
                      "Do not call the `SimTransport(...)` object you made directly",
                      "With `async` Python and in JavaScript there is one thread only"):
            self.assertIn(words, flat)
        self.assertNotIn("sim.tap(...)` while", flat)
        with open(os.path.join(self.project, "pretend_lines.py"), "w", encoding="utf-8") as f:
            f.write(program)
        done = run("python3 pretend_lines.py", self.project)
        self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
        out = done.stdout.split("\n")
        self.assertEqual(out[0], "tap")
        self.assertIn("loudness 48", out)   # the steady level 0.5
        self.assertIn("loudness 5", out)    # the clap
        self.assertEqual(out.count("tap"), 1)   # auto_tap=False: no tap of its own
        # every call the table names exists, in both spellings
        from vexo_open.transports.sim import SimTransport
        for name in ("tap", "play_motion", "stop_motion", "set_loudness", "play_loudness", "set_rssi", "set_battery",
                     "set_activity", "drop_link", "power_cycle", "after", "step"):
            self.assertTrue(callable(getattr(SimTransport, name)), name)
            self.assertIn("sim.%s(" % name, part)

    def test_agents_directs_the_pretend_band(self):
        # "No band at hand" points to the directing calls with a short example; it runs as printed
        part = section(read("AGENTS.md"), "No band at hand: the pretend band")
        direct = section(part, "Directing it: make things happen when you say so")
        (_s, _e, lines), = blocks(direct, "python")
        self.assertLessEqual(len(lines.strip().splitlines()), 6)
        self.assertIn("In `app.py`, in place of the connect line:", direct)
        program = self.lines_program(lines + "band.wait(0.5)\nprint(band.get_radio().rssi < 0)\nband.disconnect()\n")
        with open(os.path.join(self.project, "direct.py"), "w", encoding="utf-8") as f:
            f.write(program)
        done = run("python3 direct.py", self.project)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "it fell\nTrue\n", ""))
        self.assertIn('"The pretend band: making things\nhappen" in `docs/api.md`', direct)
        from vexo_open.transports.sim import SimTransport
        named = re.findall(r"`(\w+)\([^`]*\)`", direct.split("The calls:")[1].split("JavaScript:")[0])
        self.assertGreaterEqual(len(named), 10)
        for name in named:
            self.assertTrue(callable(getattr(SimTransport, name)), name)

    def test_agents_says_what_the_pretend_band_does_by_itself(self):
        # every number in "What the pretend band does by itself" (and in the table of docs/api.md) is what it does
        part = section(section(read("AGENTS.md"), "No band at hand: the pretend band"), "What the pretend band does by itself")
        api = section(read("docs/api.md"), "The pretend band: making things happen")
        flat = lambda text: re.sub(r"\s+", " ", text)
        for words in ("a gentle swing that repeats every 2 seconds", "within 0.12 g", "between 0.96 and 1.04",
                      "below 30 degrees/second", "a `tap` 4 seconds after connecting, a `double-tap` at 8 seconds",
                      "the `audio` stream is silence", "`steps` starts at 1200 and goes up by 2 with every reading",
                      "87 percent", "about 31", "about -55 dBm"):
            self.assertIn(words, flat(part))
        for words in ("within 0.12 g", "`az` 0.96 to 1.04", "a `tap` 4 seconds after connecting, a `double-tap` at 8 seconds",
                      "`steps` starts at 1200 and goes up by 2", "87 percent"):
            self.assertIn(words, flat(api))
        from vexo_open.sync import Band
        seen = dict(motion=[], event=[], activity=[], audio=[], battery=[], temperature=[], link=[])
        started = __import__("time").time()
        with Band.connect(42, transport="sim") as band:
            for name in seen:
                band.on(name, lambda e, name=name: seen[name].append((__import__("time").time() - started, e)))
            band.configure(motion=50, events=True, activity=True, audio=True, battery=True, temperature=1, link=True)
            band.wait(8.6)
        motion = [e for _t, e in seen["motion"]]
        self.assertTrue(all(abs(m.ax) <= 0.121 and abs(m.ay) <= 0.121 and 0.959 <= m.az <= 1.041 for m in motion))
        self.assertTrue(max(abs(m.ax) for m in motion) > 0.1 and max(m.az for m in motion) > 1.03)
        self.assertTrue(all(max(abs(m.gx), abs(m.gy), abs(m.gz)) <= 30 for m in motion))
        self.assertEqual([e.kind for _t, e in seen["event"]], ["tap", "double-tap"])
        self.assertTrue(3.5 < seen["event"][0][0] < 4.8 and 7.5 < seen["event"][1][0] < 8.6, seen["event"])
        steps = [e.steps for _t, e in seen["activity"]]
        self.assertEqual(steps[0], 1202)
        self.assertEqual({b - a for a, b in zip(steps, steps[1:])}, {2})
        self.assertTrue(all(105 <= e.cadence <= 115 and e.state == "walk" for _t, e in seen["activity"]))
        self.assertEqual(seen["battery"][0][1].percent, 87)
        self.assertTrue(all(30 <= e.celsius <= 32 for _t, e in seen["temperature"]))
        self.assertTrue(all(-60 <= e.rssi <= -50 for _t, e in seen["link"]))
        # silence: every packet is the 20 ms "nothing" frame with padding, no sound in it
        self.assertGreater(len(seen["audio"]), 300)
        self.assertTrue(all(e.opus[:2] == b"\xbb\x41" and not any(e.opus[3:]) for _t, e in seen["audio"]))

    def test_agents_gesture_from_raw_motion(self):
        # the shake detector: quiet on the pretend band's own swing, one "shake" for the motion that is played
        part = section(section(read("AGENTS.md"), "No band at hand: the pretend band"), "A gesture of your own, from raw motion")
        (_s, _e, lines), = blocks(part, "python")
        self.assertIn("`app.py` in place of everything below the line `BAND = 7`:", part)
        self.assertIn("play_motion(", lines)
        self.assertIn("math.sqrt(m.ax ** 2 + m.ay ** 2 + m.az ** 2)", lines)
        with open(os.path.join(self.project, "shake.py"), "w", encoding="utf-8") as f:
            f.write(self.lines_program(lines))
        done = run("python3 shake.py", self.project)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "shake\n", ""))
        # without the played motion: nothing, so the print really comes from the shake
        with open(os.path.join(self.project, "no_shake.py"), "w", encoding="utf-8") as f:
            f.write(self.lines_program("\n".join(line for line in lines.split("\n") if "play_motion(" not in line)))
        done = run("python3 no_shake.py", self.project)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))
        # the played shake goes up to 2.5 g: the sample widens the sensor's range first, so nothing is cut off
        self.assertIn("band.set_motion(accel_range=8) ", lines)
        self.assertLess(lines.index("band.set_motion(accel_range=8)"), lines.index("band.configure(motion=50)"))
        self.assertIn("az=1 + 1.5 * math.sin(40 * s)", lines)

    def test_the_default_sensor_range_is_stated_and_true(self):
        # 2 g and 2000 degrees/second after every connect; beyond that a reading is cut off; set_motion widens it
        flat = lambda page: re.sub(r"\s+", " ", read(page))
        for page, words in (
                ("AGENTS.md", "After every connect the accelerometer measures up to 2 g in each direction and the gyro up to "
                              "2000 degrees/second."),
                ("AGENTS.md", "`band.set_motion(accel_range=8)` (2, 4, 8 or 16 g; JavaScript `band.setMotion({ accelRange: 8 })`)"),
                ("AGENTS.md", "reads exactly 2.0"),
                ("docs/api.md", "**After every connect the ranges are 2 g and 2000 degrees/second.**"),
                ("docs/api.md", "`band.setMotion({ accelRange: 8 })` / `band.set_motion(accel_range=8)`"),
                ("docs/api.md", "at 2 g and 2000 degrees/second unless you widened the range"),
                ("docs/getting-started.md", "**The motion sensor measures up to 2 g.**"),
                ("docs/getting-started.md", "`band.set_motion(accel_range=8)` (2, 4, 8 or 16)")):
            self.assertIn(words, flat(page), page)
        from vexo_open.sync import Band, VexoError
        from vexo_open.transports.sim import SimTransport
        peaks = {}
        for widen in (False, True):
            with Band.connect(42, transport=SimTransport(42, auto_tap=False)) as band:
                self.assertEqual((band.get_motion().accel_range, band.get_motion().gyro_range), (2, 2000))   # every connect
                if widen:
                    band.set_motion(accel_range=8)
                    self.assertEqual(band.get_motion().accel_range, 8)
                seen = []
                band.on("motion", seen.append)
                band.configure(motion=100)
                band.transport.play_motion(lambda s: dict(az=2.5, gx=3000), seconds=0.3)
                band.wait(0.5)
                peaks[widen] = (max(m.az for m in seen), max(m.gx for m in seen))
                for wrong in (3, 0, 32):
                    with self.assertRaises(VexoError):
                        band.set_motion(accel_range=wrong)
        self.assertEqual(peaks[False], (2.0, 2000.0))       # cut off at the range: "reads exactly 2.0"
        self.assertEqual(peaks[True], (2.5, 2000.0))
        # no Python sample on the student pages plays a motion beyond the range it has set
        for page in STUDENT_PAGES:
            for _s, _e, code in blocks(read(page), "python"):
                widest = max([2] + [int(g) for g in re.findall(r"accel_range=(\d+)", code)])
                for played in re.findall(r"play_motion\((.*)\)", code):
                    for base, swing in re.findall(r"a[xyz]=(\d+(?:\.\d+)?) \+ (\d+(?:\.\d+)?) \* math\.sin", played):
                        self.assertLessEqual(float(base) + float(swing), widest, "%s: %s is beyond %d g" % (page, played, widest))
                    for value in re.findall(r"a[xyz]=(-?\d+(?:\.\d+)?)(?! \+)", played):
                        self.assertLessEqual(abs(float(value)), widest, "%s: %s is beyond %d g" % (page, played, widest))

    def test_speech_page_programs_find_speech_py_from_my_project(self):
        # docs/speech.md: no line that depends on the folder the program is started in; the
        # "Plug in your own service" program runs as printed, from another folder
        page = read("docs/speech.md")
        finder = 'sys.path.insert(0, str(Path(__file__).resolve().parent / "vexo-open-kit" / "examples" / "python"))'
        self.assertEqual(page.count(finder), 2)
        self.assertEqual(page.count("sys.path"), 2)
        (program,) = [code for _s, _e, code in blocks(section(page, "Plug in your own service"), "python")]
        self.assertTrue(program.startswith("import sys\nfrom pathlib import Path\n" + finder + "\nimport speech\n"))
        # as printed it shows itself working: the page names the file, the command and the output
        said = re.search(r"\nSave it as `([\w.]+)` in `my-project/` and run `python3 ([\w.]+)`\. It prints:\n\n((?:    .+\n)+)\n",
                         section(page, "Plug in your own service"))
        self.assertTrue(said and said.group(1) == said.group(2), "the page no longer says how to run the program and what it prints")
        expected = "".join(line[4:] + "\n" for line in said.group(3).splitlines())
        self.assertEqual(expected, "heard: 1.5 seconds of sound\n1.5 seconds of sound\n")
        with open(os.path.join(self.project, said.group(1)), "w", encoding="utf-8") as f:
            f.write(program)
        done = run("python3 " + said.group(1), self.project)                      # as the page says: in my-project/
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, expected, ""))
        with tempfile.TemporaryDirectory() as elsewhere:                          # and from any other folder
            done = run("python3 " + shlex.quote(os.path.join(self.project, said.group(1))), elsewhere)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, expected, ""))

    def test_speech_page_one_interface_program_is_whole_and_a_missing_key_is_one_sentence(self):
        # "One interface, one ready engine": the block that starts with `import sys` is a whole
        # program, as docs/api.md promises for such blocks. With no key it ends with one
        # sentence and the line of the program, like a band problem: no traceback.
        part = section(read("docs/speech.md"), "One interface, one ready engine")
        (program,) = [code for _s, _e, code in blocks(part, "python")]
        self.assertTrue(program.startswith("import sys\nfrom pathlib import Path\nsys.path.insert(0, "))
        named = re.search(r"A whole program; save it as `([\w.]+)` in\s+`my-project/` and run `python3 ([\w.]+)`:", part)
        self.assertTrue(named and named.group(1) == named.group(2), "the page no longer says where to save the program")
        for line in ("import speech ", "from vexo_open.sync import Band\n", "band = Band.connect(42)\n", "band.disconnect()\n"):
            self.assertIn(line, program)
        self.assertLess(program.index("ears.start("), program.index("Band.connect("), "a missing key stops it before the microphone goes on")
        # every other Python block on the page that starts with an import of sys is whole too
        for _s, _e, code in blocks(read("docs/speech.md"), "python"):
            if code.startswith("import sys"):
                compile(code, "docs/speech.md", "exec")
                self.assertTrue("Band.connect(" in code or "class MySpeech" in code, code)
        self.assertNotIn(SIM, program)
        with open(os.path.join(self.project, named.group(1)), "w", encoding="utf-8") as f:
            f.write(pretend(program))
        with tempfile.TemporaryDirectory() as elsewhere:
            done = run("python3 " + shlex.quote(os.path.join(self.project, named.group(1))), elsewhere)
        self.assertEqual((done.returncode, done.stdout), (1, ""), done.stderr)
        problem = done.stderr.splitlines()
        self.assertEqual(len(problem), 2, done.stderr)
        self.assertTrue(problem[0].startswith("Problem (speech): Transcription needs a Deepgram key: see docs/deepgram.md. "
                                              "Put your team's key in the environment variable DEEPGRAM_API_KEY "), problem[0])
        self.assertEqual(problem[1], "  (line 8 of %s)" % named.group(1))
        self.assertNotIn("Traceback", done.stderr)
        flat = re.sub(r"\s+", " ", part)
        self.assertIn("Problem (speech): Transcription needs a Deepgram key: see docs/deepgram.md. Put your team's key in ...", flat)
        self.assertIn("(line 8 of %s)" % named.group(1), part)
        self.assertIn("`speech.SpeechError` is a `VexoError` with the code `speech`", part)
        # caught, it is a VexoError with that code; and the full traceback is one environment variable away
        with open(os.path.join(self.project, "caught.py"), "w", encoding="utf-8") as f:
            f.write(program.split("ears = ")[0] + "from vexo_open.sync import VexoError\ntry:\n"
                    '    speech.create().start()\nexcept VexoError as e:\n    print(e.code, type(e).__name__)\n'
                    'speech.create("nothing-like-this")\n')
        done = run("python3 caught.py", self.project)
        self.assertEqual((done.returncode, done.stdout), (1, "speech SpeechError\n"))
        self.assertRegex(done.stderr, r"^Problem \(speech\): Unknown speech engine 'nothing-like-this'\..*\n  \(line 12 of caught\.py\)\n$")
        done = run("VEXO_TRACEBACK=1 python3 caught.py", self.project)
        self.assertIn("Traceback", done.stderr)

    def test_agents_program(self):
        text = section(read("AGENTS.md"), "Shortest working program: Python")
        (start, end, code), = blocks(text, "python")
        self.assertRegex(text[:start], r"`my-project/app\.py`:\s*$")
        self.assertIn("Run it in `my-project/`", text[end:])
        command, = commands(text[end:])
        self.assertEqual(command, "python3 app.py")
        self.assertIn('Pretend band: Band.connect(BAND, transport="sim")', code)
        self.assertTrue(code.startswith(self.PATH_LINES), "the same three lines as in the guide")
        self.assertEqual(code.splitlines()[:3], self.first_program()[2].splitlines()[:3][:2] + [code.splitlines()[2]])
        out = self.save_and_run(self.project, "app.py", code, command, self.project)
        self.assertEqual(out, "tap\n")
        self.assertIn("prints `tap` once", text)
        # "the program can be started from any folder": here from an empty one, by its full path
        self.assertIn("so\nthe program can be started from any folder", text)
        with tempfile.TemporaryDirectory() as elsewhere:
            done = run("python3 " + shlex.quote(os.path.join(self.project, "app.py")), elsewhere)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "tap\n", ""))
        # the layout the page draws is the layout used here, and the page allows no other
        layout = section(read("AGENTS.md"), "Where the team's code goes")
        for line in ("    my-project/", "      vexo-open-kit/", "      app.py", "      index.html"):
            self.assertIn(line, layout)
        self.assertIn("This is the only layout.", layout)
        self.assertIn("The kit is a folder inside the project folder, not next to it.", layout)
        self.assertNotIn("next to the kit", layout)
        self.assertIn("    mv vexo-open-kit my-project/\n", layout)

    def test_one_layout_on_every_page(self):
        # the kit inside my-project/, under the name vexo-open-kit: no page tells it another way
        for page in STUDENT_PAGES:
            text = read(page)
            self.assertNotIn("next to the kit", text, page)
            self.assertNotIn("import kit", text, page)                       # the old advice: save it in examples/python/
            self.assertNotRegex(text, r'sys\.path\.insert\(0, "', page)      # a path that depends on the working directory
            for code in [c for _s, _e, c in blocks(text, "python")] + re.findall(r"`([^`\n]*sys\.path[^`\n]*)`", text):
                for line in code.split("\n"):
                    if "sys.path" in line:       # always built from the program's own place, to the library or to speech.py
                        self.assertRegex(line, r'Path\(__file__\)\.resolve\(\)\.parent / "vexo-open-kit" / '
                                               r'("python"|"examples" / "python")\)\)', page)
            for place in re.findall(r"`([\w./-]*try\.(?:py|mjs))`", text):   # where the pages tell you to save a program
                self.assertEqual(os.path.dirname(place), "", "%s: save it in my-project/ itself, not in %s" % (page, place))
        for page in ("README.md", "AGENTS.md", "docs/getting-started.md"):     # each draws it: the kit one step inside
            drawn = re.search(r"^( +)my-project/[^\n]*\n\1  vexo-open-kit/", read(page), re.M)
            self.assertTrue(drawn, "%s does not draw my-project/ with vexo-open-kit/ inside it" % page)
        self.assertIn("Make a folder `my-project` with the kit\n  inside it", read("examples/README.md"))
        self.assertIn("      try.py", section(read("docs/api.md"), "Where your code goes"))

    def test_async_python_named_in_agents_exists(self):
        self.assertIn("`from vexo_open import Band` is the same API with `async` / `await`", read("AGENTS.md"))
        done = subprocess.run([sys.executable, "-c", "from vexo_open import Band; import inspect; "
                               "assert inspect.iscoroutinefunction(Band.set_led)"],
                              cwd=tempfile.gettempdir(), env=dict(os.environ, PYTHONPATH=os.path.join(KIT, "python")),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(done.returncode, 0, done.stderr)


class Commands(unittest.TestCase):
    """The command lists that need no hardware. A student's commands are typed in
    my-project/ and name the kit's files as vexo-open-kit/...; those run in the copy. The
    lists marked "for the kit's maintainers" run from the kit folder, as they say."""
    ran = {}

    def ok(self, command, cwd=None):
        if command.startswith("node ") and not NODE:
            self.skipTest("node is not installed")
        cwd = cwd or KIT
        if (cwd, command) not in self.ran:     # the same command is printed on several pages
            self.ran[cwd, command] = run(command, cwd)
        done = self.ran[cwd, command]
        self.assertEqual(done.returncode, 0, "%s\n%s\n%s" % (command, done.stdout, done.stderr))
        self.assertNotIn("Traceback", done.stderr)
        return done.stdout

    def in_my_project(self, command):
        """A student's command: typed in my-project/, the kit's files named as vexo-open-kit/..."""
        self.assertRegex(command, r"^(python3|node) vexo-open-kit/examples/(python|node)/[\w.-]+( |$)", command)
        return self.ok(command, PROJECT)

    def test_agents_examples_with_no_hardware(self):
        text = section(read("AGENTS.md"), "Check your work")
        self.assertIn("for the kit's maintainers", text)
        self.assertIn("typed from the kit folder (`my-project/vexo-open-kit`), not in `my-project/`", text)
        listed = commands(text.split("Examples that run with no hardware")[1])
        self.assertGreaterEqual(len(listed), 6)
        for command in listed:
            self.assertTrue("--sim" in command or command.startswith("node examples/node/"), command)
            self.assertTrue(self.ok(command).strip(), command + " printed nothing")
        # "from my-project/ put vexo-open-kit/ in front of the path"
        self.assertIn("from `my-project/`\nput `vexo-open-kit/` in front of the path", text)
        self.assertTrue(self.in_my_project(listed[0].replace(" examples/", " vexo-open-kit/examples/")).strip())

    def test_agents_pretend_band_commands(self):
        line = re.search(r"^- Kit examples: (.*)$", section(read("AGENTS.md"), "No band at hand: the pretend band"), re.M)
        self.assertTrue(line.group(1).endswith("(typed in `my-project/`)"), line.group(1))
        listed = [c for c in re.findall(r"`([^`]+)`", line.group(1)) if c != "my-project/"]
        self.assertEqual(len(listed), 2)
        for command in listed:
            self.assertIn("Connected to Vexo-HACK-42", self.in_my_project(command))

    def test_readme_quick_start(self):
        whole = read("README.md")
        setup = section(whole, "Set up")
        home = "    mkdir my-project\n    cd my-project\n"
        self.assertIn(home, setup)
        # first the kit gets its home, then the commands: all typed in my-project/
        self.assertLess(whole.index(home), whole.index("python3 "), "no command before the kit has its home")
        connect = section(whole, "Connect your band")
        self.assertIn("python3 -m http.server 8000 --bind 127.0.0.1", connect)
        self.assertIn("<http://localhost:8000/vexo-open-kit/dashboard/?band=42>", connect)
        self.assertIn("Double-tap the band", connect)
        example = section(whole, "Run an example")
        self.assertIn("python3 vexo-open-kit/examples/python/led.py --sim", example)
        self.assertLess(len(whole.splitlines()), 140, "the README stays one short page")

    def test_getting_started_no_band_command(self):
        m = re.search(r"No band\? Run `([^`]+)`\. Nothing needs to be installed", read("docs/getting-started.md"))
        self.assertIn("Connected to Vexo-HACK-42", self.in_my_project(m.group(1)))
        # the commands of the page with a band number are the same ones, in the same place
        listed = [c for c in commands(read("docs/getting-started.md")) if "examples/" in c]
        self.assertEqual(listed, ["python3 vexo-open-kit/examples/python/led.py 42",
                                  "python3 vexo-open-kit/examples/python/led.py 42 --usb"])
        self.assertEqual(m.group(1), listed[0].replace(" 42", " --sim"))

    def test_a_command_typed_inside_the_kit_fails_the_way_the_problem_table_says(self):
        # one step too deep: the kit's name appears twice in the path, and `cd ..` is the cure
        guide = read("docs/getting-started.md")
        row = re.search(r"^\| `can't open file '\.\.\.vexo-open-kit/vexo-open-kit/\.\.\.'` .*\|$", guide, re.M)
        self.assertTrue(row, "the row for a terminal inside the kit folder is gone")
        for words in ("Your terminal is inside the kit folder", "Type `cd ..` and run the command again",
                      "typed in `my-project/`", "`pwd` (Windows: `cd`)", '"File not found"'):
            self.assertIn(words, row.group(0))
        self.assertLess(guide.index("## When something does not work"), row.start())
        command = "python3 vexo-open-kit/examples/python/led.py --sim"
        self.assertIn("`%s`" % command, guide)
        done = run(command, KIT_COPY)                                  # typed inside the kit
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("can't open file", done.stderr)
        self.assertIn(os.path.join("vexo-open-kit", "vexo-open-kit", "examples"), done.stderr)
        done = run(command, os.path.join(KIT_COPY, ".."))              # after `cd ..`
        self.assertEqual((done.returncode, done.stderr), (0, ""))

    def test_examples_table(self):
        # "use --sim instead of the number", "add --seconds": both are in the page's own words
        page = read("examples/README.md")
        self.assertIn("use `--sim` instead of the number", page)
        self.assertIn("`--seconds 10`", page)
        self.assertIn("Run the commands in `my-project/`, the folder that holds the\nkit", page)
        self.assertNotIn("from the kit's top folder", page)
        rows = re.findall(r"^\| `(python|node)/([\w.]+)` \| (.*?) \| `([^`]+)` \|$", page, re.M)
        self.assertGreaterEqual(len(rows), 8)
        for folder, name, what, command in rows:
            self.assertIn(" vexo-open-kit/examples/%s/%s" % (folder, name), command)
            if "pip install" in what:
                continue                # needs an extra package; test_examples.py covers these
            if folder == "python":
                self.assertTrue(command.endswith(" 42"), command)
                command = command[:-3] + " --sim --seconds 1"
            if name == "audio_to_text.py":      # text needs a key; without one it still records
                with tempfile.TemporaryDirectory() as project:      # it saves recording.ogg where it is started
                    os.symlink(KIT_COPY, os.path.join(project, "vexo-open-kit"))
                    done = run(command + " --text no", project)
                    self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
                    self.assertIn("recording.ogg", os.listdir(project))
                continue
            if name in ("ask_the_band.py", "voice_commands.py", "meeting_notes.py"):
                # they need the team's OpenAI key; without one they stop at once and say so (test_ai.py runs them with one)
                done = run(command, PROJECT)
                self.assertEqual((done.returncode, done.stdout), (1, ""), command)
                self.assertRegex(done.stderr, r"^Problem \(ai\): This needs an OpenAI key: .*\n  \(line \d+ of %s\)\n$" % name)
                continue
            self.assertTrue(self.in_my_project(command).strip(), command)

    def test_reconnect_example(self):
        out = self.ok("python3 examples/python/reconnect.py --sim --seconds 6")
        self.assertRegex(out, r"Link lost\. Trying again for 120 seconds\.\nBack after \d+\.\d seconds\. "
                              r"The LED is green again\.\nDisconnected: closed\n$")

    def test_kit_test_commands_point_at_real_files(self):
        # these run the tests themselves, so here only: every path and pattern in them exists
        for page, heading in (("AGENTS.md", "Check your work"), ("CONTRIBUTING.md", "Tests"), ("examples/README.md", "Examples")):
            listed = [c for c in commands(section(read(page), heading)) if "test" in c]
            self.assertTrue(listed, page)
            # typed in the kit folder itself, and the page says so and for whom
            before = section(read(page), heading).split(listed[0])[0]
            self.assertRegex(before, r"[Ff]or the kit's\s+maintainers", page)
            self.assertIn("kit folder", before, page)
            for command in listed:
                folder = KIT
                inside = re.match(r"\(cd (\w+) && (.*)\)$", command)
                if inside:
                    folder, command = os.path.join(KIT, inside.group(1)), inside.group(2)
                words = shlex.split(command)
                self.assertIn(words[:2], (["node", "--test"], ["python3", "-m"]), command)
                paths = words[2:] if words[0] == "node" else [words[words.index("-s") + 1]]
                for path in paths:
                    self.assertTrue(glob.glob(os.path.join(folder, path)), "%s: nothing matches %s" % (page, path))

    def test_pages_named_with_localhost_exist(self):
        # One web server, started in my-project/: so every address a page gives is either
        # the team's own page (/index.html) or a file of the kit under /vexo-open-kit/.
        seen = 0
        for page in STUDENT_PAGES:
            text = read(page)
            for path in re.findall(r"http://localhost:8000(/[\w./-]*)", text):
                if path in ("/index.html", "/", "/...", "/vexo-open-kit/..."):
                    continue            # the team's own page in my-project/ (also built from its file name); "and so on"
                self.assertTrue(path.startswith("/vexo-open-kit/"),
                                "%s: %s is an address of a server in the kit folder, not in my-project/" % (page, path))
                target = os.path.join(KIT, path[len("/vexo-open-kit/"):])
                if path.endswith("/"):
                    target = os.path.join(target, "index.html")
                self.assertTrue(os.path.isfile(target), "%s: %s is not a file in the kit" % (page, path))
                seen += 1
            self.assertNotRegex(text, r"localhost:8000/(dashboard|examples)/", page)
        self.assertGreaterEqual(seen, 6)
        for path in re.findall(r"same server, `(/[\w./-]+)`", read("examples/README.md")):
            self.assertTrue(path.startswith("/vexo-open-kit/"), path)
            self.assertTrue(os.path.isfile(os.path.join(KIT, path[len("/vexo-open-kit/"):])), path)

    def test_one_web_server_in_my_project(self):
        # Wherever a student page prints the server command, the words around it say where:
        # my-project/. The kit folder is only for the maintainers' checks (port 8137).
        for page in STUDENT_PAGES + ["dashboard/speech/README.md"]:
            text = read(page)
            for found in re.finditer(r"-m http\.server (\d+)", text):
                around = text[max(0, found.start() - 400):found.end() + 300]
                if found.group(1) == "8137":
                    self.assertRegex(around, r"for the kit's maintainers", page)
                    self.assertIn("in the kit folder", around, page)
                elif found.group(1) == "8000":       # (8001 is the "port is taken" example next to it)
                    self.assertRegex(around, r"`my-project/`|my-project/|step 1", page)
                    self.assertNotRegex(around, r"[Ii]n the kit folder|from the kit folder|kit's top folder", page)
        guide = read("docs/getting-started.md")
        self.assertEqual(len(re.findall(r"^ {4,}python3 -m http\.server", guide, re.M)), 1, "one place starts the server")
        self.assertIn("it is the\n   one web server for everything on this page", guide)
        self.assertIn("Do not start a second one.", section(guide, "3. Your first web page"))
        self.assertIn("Start one server only, in `my-project/`", read("AGENTS.md"))
        for page in ("AGENTS.md", "README.md", "docs/getting-started.md", "examples/README.md", "docs/speech.md"):
            self.assertRegex(read(page), r"http://localhost:8000/vexo-open-kit/dashboard/\?band=(42|XX)", page)
        # the maintainers' checks in dashboard/test say the same in their first lines
        for name in ("browser-check.mjs", "listen-check.mjs"):
            with open(os.path.join(KIT, "dashboard", "test", name), encoding="utf-8") as f:
                head = f.read(900)
            self.assertIn("For the kit's maintainers", head, name)
            self.assertRegex(head, r"http\.server 8137 --bind 127\.0\.0\.1 +\(in the kit folder, in another terminal\)", name)


class Promises(unittest.TestCase):
    """What the pages say about storage, the cable and sleep, tried on the pretend band."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(KIT, "python"))
        from vexo_open.sync import Band, VexoError
        from vexo_open.transports.sim import SimTransport
        cls.Band, cls.VexoError, cls.Sim = Band, VexoError, SimTransport

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(os.path.join(KIT, "python"))

    def test_storage_as_documented(self):
        api = section(read("docs/api.md"), "Storage")
        with self.Band.connect(42, transport="sim") as band:
            self.assertIn("An empty store has 242,164 bytes free", api)
            self.assertEqual(band.storage.info().free_bytes, 242164)
            self.assertIn("a loop of\n  30 `put` calls works", api)
            for key in range(30):                       # faster than 10 a second: the library waits
                band.storage.put(key, "x" * 10)
            self.assertIn("8 bytes plus its value rounded up to a multiple of 4", api)
            self.assertEqual(band.storage.info().free_bytes, 242164 - 30 * (8 + 12))
            self.assertIn("so a `put` right after it works", api)
            band.storage.wipe()
            band.storage.put(1, "a")
            self.assertEqual(band.storage.info().used_keys, 1)

    def test_cable_as_documented(self):
        api = read("docs/api.md")
        with self.Band.connect(42, transport=self.Sim(42, via="usb")) as band:
            self.assertIn("over the USB cable `configure` fails with `not-available` and turns nothing on", api)
            with self.assertRaises(self.VexoError) as failed:
                band.configure(link=True, motion=50)
            self.assertEqual(failed.exception.code, "not-available")
            self.assertFalse(any(band.streams.values()))
            self.assertIn("gives `rssi` and `phy` `null` / `None`, `intervalMs` 0 and `mtu` 0", api)
            radio = band.get_radio()
            self.assertEqual((radio.rssi, radio.phy, radio.interval_ms, radio.mtu), (None, None, 0, 0))
            self.assertIn("Over the USB cable the link stays open and the band keeps answering", api)
            events = []
            band.on("disconnect", events.append)
            band.configure(motion=50)
            band.set_led("#ff0000")
            band.sleep()
            band.wait(0.5)
            self.assertTrue(band.connected)
            self.assertEqual(events, [])
            self.assertFalse(any(band.streams.values()))
            self.assertEqual(band.get_led().pattern, "off")

    def test_sleep_over_bluetooth_as_documented(self):
        self.assertIn("(`disconnect` with reason `sleep`)", read("docs/api.md"))
        band = self.Band.connect(42, transport="sim", auto_reconnect=True)
        events = []
        band.on("disconnect", lambda e: events.append(e.reason))
        band.on("reconnecting", lambda e: events.append("reconnecting"))
        band.sleep()
        band.wait(2)
        self.assertEqual(events, ["sleep"])             # and no reconnect after sleep()
        self.assertFalse(band.connected)
        band.disconnect()


class Wording(unittest.TestCase):
    """Things every page has to say the same way."""

    def kit_texts(self):
        """(path, text) of every text file of the kit: pages, programs, comments, messages."""
        endings = (".md", ".py", ".js", ".mjs", ".html", ".sh", ".css", ".json", ".toml", ".txt")
        for folder, folders, names in os.walk(KIT):
            folders[:] = [f for f in folders if f not in (".git", "vendor", "__pycache__", "node_modules")]
            for name in names:
                path = os.path.join(folder, name)
                if name.endswith(endings) and os.path.getsize(path) < 2000000:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        yield os.path.relpath(path, KIT), f.read()

    def test_the_dashboard_is_opened_from_the_local_server_and_the_number_is_on_the_bag(self):
        # No hosted dashboard, no printed sheet to scan: the band number is written on the bag
        # the band comes in, and the dashboard is opened from the web server in my-project/.
        for path, text in self.kit_texts():
            if path == os.path.join("examples", "test", "test_docs.py"):
                continue
            for old in ("github.io", "QR code", "team card", "your card"):
                self.assertNotIn(old.lower(), text.lower(), "%s still says %r" % (path, old))
        self.assertFalse(os.path.exists(os.path.join(KIT, "cards")))
        guide = section(read("docs/getting-started.md"), "1. See your band working")
        self.assertRegex(guide, r"(?s)^.*?\n1\. In the terminal, in `my-project/`, run:\n\n +python3 -m http\.server 8000 --bind 127\.0\.0\.1\n")
        self.assertIn("3. Open <http://localhost:8000/vexo-open-kit/dashboard/?band=42>", guide)
        self.assertIn("Your band number is written on the bag", read("docs/getting-started.md"))
        self.assertIn("the dashboard is opened from the team's own computer", read("AGENTS.md"))

    def test_agents_may_only_ever_connect_to_the_teams_own_band(self):
        agents = read("AGENTS.md")
        rule = re.sub(r"\s+", " ", agents.split("\n### Only the team's own band: hard rules\n")[1].split("\nBand 7 is called")[0])
        self.assertLess(agents.index("### Only the team's own band: hard rules"), agents.index("## Audio to text to AI"))
        for words in ("You MUST ask for the team's band number before you write or run anything that connects.",
                      "NEVER guess it, NEVER use 42",
                      "use that one value in every connect, every scan and every dashboard address",
                      "You MUST NEVER connect to, scan for, or send a command to any other band",
                      "NEVER write code that scans without a filter on the exact name `Vexo-HACK-NN`",
                      "NEVER write code that loops over band numbers",
                      "disconnect at once, change nothing on that band, and tell the user",
                      "you MUST NOT try another number",
                      "You MUST refuse to connect to another team's band",
                      "You MUST NEVER renumber a band"):
            self.assertIn(words, rule)
        self.assertEqual(len(re.findall(r"(?m)^\d\. ", agents.split("\n### Only the team's own band: hard rules\n")[1].split("\nBand 7 is called")[0])), 7)
        # the calls the rule names exist, and a wrong band is refused by the library itself
        from vexo_open.sync import Band
        from vexo_open import VexoError
        band = Band.connect(17, transport="sim")
        self.assertEqual((band.number, band.name), (17, "Vexo-HACK-17"))
        self.assertTrue(callable(band.set_band_number))
        band.disconnect()
        for page in ("README.md", "docs/getting-started.md"):
            self.assertIn("someone else has connected to it: restart it and tell a helper", re.sub(r"\s+", " ", read(page)), page)

    def test_42_is_called_an_example_at_the_top_of_every_page_with_commands(self):
        flat = lambda page: re.sub(r"\s+", " ", read(page).replace("\n> ", " "))
        for page in ("README.md", "docs/getting-started.md", "examples/README.md", "projects/README.md", "docs/deepgram.md",
                     "docs/speech.md", "AGENTS.md", "docs/bluetooth-contract.md"):
            self.assertIn("**42 is only our example. Wherever you see 42 in a command, an address or a name, type the number "
                          "written on your band's bag.** Band 7 is `Vexo-HACK-07`, and its dashboard address ends in `?band=7`.",
                          flat(page), page)
        self.assertIn("Never write 42 into a team's program", flat("AGENTS.md"))

    def test_the_web_server_is_always_started_for_this_computer_only(self):
        # Without --bind 127.0.0.1 the folder is served to everyone on the same network.
        seen, open_to_the_network = 0, []
        for path, text in self.kit_texts():
            if path == os.path.join("examples", "test", "test_docs.py"):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                for found in re.finditer(r"-m http\.server\b[^`\n<|)]*", line):
                    seen += 1
                    if not re.match(r"-m http\.server \d+ --bind 127\.0\.0\.1\b", found.group(0)):
                        open_to_the_network.append("%s:%d" % (path, number))
        self.assertGreaterEqual(seen, 10)
        self.assertEqual(open_to_the_network, [], "these lines start the web server without --bind 127.0.0.1")
        # said why, where the command is first shown, and what to do when the port is taken
        for page, why in (("docs/getting-started.md", "everyone on the same Wi-Fi can read every file in the folder"),
                          ("AGENTS.md", "everyone\n  on the same Wi-Fi (the whole event) can read every file in the folder"),
                          ("README.md", "everyone on the same Wi-Fi can read the folder"),
                          ("examples/README.md", "everyone on the same Wi-Fi can read the folder")):
            text = read(page)
            self.assertIn(why, text, page)
            self.assertIn("Address already in use", text, page)
            self.assertIn("8001", text, page)

    def test_the_web_server_command_works_as_printed_and_localhost_reaches_it(self):
        # the command from the pages, on a free port; then the address form the pages give
        import socket
        import time
        import urllib.request
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.assertIn("    python3 -m http.server 8000 --bind 127.0.0.1\n", read("AGENTS.md"))
        # started in my-project/, as the pages say: the kit's pages are under /vexo-open-kit/
        server = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"], cwd=PROJECT,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for host in ("localhost", "127.0.0.1"):
                for _ in range(50):
                    try:
                        for path in ("/vexo-open-kit/examples/web/led.html", "/vexo-open-kit/dashboard/?band=42",
                                     "/vexo-open-kit/js/src/index.js"):
                            with urllib.request.urlopen("http://%s:%d%s" % (host, port, path), timeout=5) as answer:
                                self.assertEqual(answer.status, 200, host + path)
                        break
                    except OSError:
                        time.sleep(0.1)
                else:
                    self.fail("http://%s:%d did not answer" % (host, port))
            # a second server on the same port: the error the pages name
            again = subprocess.run([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"], cwd=KIT,
                                   timeout=30, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            self.assertNotEqual(again.returncode, 0)
            self.assertRegex(again.stderr, r"OSError: .*(Address already in use|only one usage of each socket address)")
        finally:
            server.terminate()
            server.wait(10)

    def test_install_hints_have_one_form(self):
        # `python3 -m pip install NAME` everywhere (a bare `pip` is often not found), with the Windows form nearby
        for page in ("README.md", "AGENTS.md", "docs/getting-started.md", "examples/README.md"):
            text = read(page)
            for found in re.finditer(r"(?:\b(python3?) -m )?pip install\b", text):
                self.assertIn(found.group(1), ("python3", "python"), "%s: a bare `pip install`" % page)
        for page in ("README.md", "AGENTS.md", "docs/getting-started.md", "examples/README.md"):
            self.assertRegex(read(page), r"[Oo]n Windows,? type\s+`python`", page)
        sys.path.insert(0, os.path.join(KIT, "python"))
        try:
            import inspect
            from vexo_open import audio
            from vexo_open.transports import ble, serial
            for module, package in ((ble, "bleak"), (serial, "pyserial")):
                self.assertIn("Run: python3 -m pip install %s (on Windows: python -m pip install %s)" % (package, package),
                              re.sub(r'"\s+"', "", inspect.getsource(module)))
            for package in ("av", "numpy"):          # the sound helpers say it the same way
                self.assertIn("python3 -m pip install %s (on Windows: python -m pip install %s)" % (package, package),
                              re.sub(r'"\s+"', "", inspect.getsource(audio)))
        finally:
            sys.path.remove(os.path.join(KIT, "python"))

    def test_steps_is_a_lifetime_total_on_every_page(self):
        # the band sends a lifetime total (contract), the library hands it on unchanged,
        # the dashboard subtracts the first one: all three pages say exactly that
        self.assertIn("`steps_total` is a lifetime count that survives restarts; subtract the first value you see.",
                      read("contract/contract.md"))
        flat = lambda page: re.sub(r"\s+", " ", read(page))
        self.assertIn("`steps` (the band's lifetime total: it goes on from one connection to the next and is kept across "
                      "restarts. For steps since you connected, subtract the first `steps` you get)", flat("docs/api.md"))
        self.assertIn("`steps` in an `activity` event is the band's lifetime total", flat("AGENTS.md"))
        self.assertIn("It is not \"steps since the program connected\"", flat("AGENTS.md"))
        self.assertIn("The page also shows the steps since it connected", flat("AGENTS.md"))
        with open(os.path.join(KIT, "dashboard", "logic.js"), encoding="utf-8") as f:
            self.assertIn("'Steps since this page connected. The band counts them with its motion sensor and sends its total "
                          "once a second: a lifetime total", f.read())
        sys.path.insert(0, os.path.join(KIT, "python"))
        try:
            from vexo_open.sync import Band
            from vexo_open.transports.sim import SimTransport
            sim = SimTransport(42)
            totals = []
            for _ in range(2):                       # two connections to the same band: the count goes on
                with Band.connect(42, transport=sim) as band:
                    band.on("activity", lambda a: totals.append(a.steps))
                    band.configure(activity=0.1)
                    band.wait(0.35)
            self.assertGreaterEqual(len(totals), 4)
            self.assertGreater(totals[0], 1200)
            self.assertEqual(totals, sorted(totals))
            self.assertEqual(len(set(totals)), len(totals))
        finally:
            sys.path.remove(os.path.join(KIT, "python"))

    def test_what_the_pretend_band_needs_and_what_text_needs(self):
        agents = re.sub(r"\s+", " ", read("AGENTS.md"))
        self.assertNotIn("the pretend band needs nothing", agents)
        self.assertIn("the pretend band needs neither. Two things need more, with a real band and with the pretend one alike: "
                      "turning the microphone's sound into samples needs `python3 -m pip install av`, and turning it into text "
                      "needs the team's Deepgram key and the internet (nothing to install; see `docs/deepgram.md`)", agents)
        # text needs nothing installed: no page asks for a speech model or pins a package for it
        for page in STUDENT_PAGES:
            self.assertNotRegex(read(page), r"pip install [^`\n|)]*(onnx|whisper|==)", page)
        for page in ("docs/speech.md", "examples/README.md"):
            self.assertIn("Nothing to install", re.sub(r"\s+", " ", read(page)).replace("Nothing has to be installed", "Nothing to install"), page)
        # the wait for the sound decoder is said where sound is decoded
        for page in ("docs/api.md", "docs/speech.md"):
            self.assertRegex(read(page), r"first `PcmDecoder\(\)` of a (Python )?program takes a few seconds", page)
            self.assertIn('print("Loading the sound decoder...")', read(page), page)
        with open(os.path.join(KIT, "examples", "python", "speech.py"), encoding="utf-8") as f:
            self.assertIn('print("Loading the sound decoder (a few seconds, the first time)...")', f.read())
        api = read("docs/api.md")
        for example in ("examples/python/fast_motion.py", "examples/node/fast-motion.mjs"):
            self.assertIn("(../%s)" % example, section(api, "Fast motion"))

    def test_event_bands_run_firmware_1_1_0_every_page_says_the_same(self):
        # Every event band has firmware 1.1.0. The pages say that in one sentence, and say
        # nothing about later firmware or about how the kit was tested.
        flat = lambda page: re.sub(r"\s+", " ", read(page))
        self.assertIn("Event bands run firmware 1.1.0.", flat("docs/api.md"))
        self.assertIn("**Event bands run firmware 1.1.0.**", flat("AGENTS.md"))
        self.assertIn("**Event bands run firmware 1.1.0.**", flat("docs/getting-started.md"))
        self.assertIn("Event bands run firmware 1.1.0", flat("contract/contract.md"))
        for page in PAGES:
            for old in ("not yet confirmed on a real band", "not yet been confirmed on a real band", "less testing there",
                        "less testing on a real band", "not yet been run on a real band", "test bench", "1.1.1", "1.1.2",
                        "1.1.3", "## Revisions"):
                self.assertNotIn(old, flat(page), page)

    def test_av_has_no_version_on_any_page(self):
        # One rule: the decoder on its own is `python3 -m pip install av`, no version, on every
        # page and in the library's own message; "tested with 15.1.0 on Python 3.9 and 19.0.2
        # on Python 3.12".
        flat = lambda page: re.sub(r"\s+", " ", read(page))
        for page in STUDENT_PAGES:
            for found in re.finditer(r"pip install ([^`\n|)]*)", read(page)):
                words = found.group(1).split()
                for word in words:
                    self.assertFalse(word.split("==")[0] == "av" and "==" in word, "%s pins av: %s" % (page, found.group(0)))
            self.assertNotIn("av==15", read(page), page)
        for page in ("AGENTS.md", "docs/api.md", "docs/speech.md", "examples/README.md"):
            self.assertRegex(flat(page), r"[Tt]ested with 15\.1\.0 on Python 3\.9 and (with )?19\.0\.2 on Python 3\.12", page)
            self.assertIn("python3 -m pip install av", read(page).replace("av==", "AV=="), page)
        self.assertIn("    python3 -m pip install av\n", read("docs/speech.md"))
        from vexo_open import audio
        self.assertEqual(audio.INSTALL, "python3 -m pip install av (on Windows: python -m pip install av)")

    def test_the_library_is_never_installed_with_pip_and_the_kit_gets_its_home_in_one_order(self):
        # "Nothing to install" is the one story: no page offers `pip install` of the kit's own folder.
        for page in STUDENT_PAGES:
            self.assertNotRegex(read(page), r"pip install [^`\n]*(\./|vexo-open-kit|vexo[-_]open\b)", page)
        flat = lambda page: re.sub(r"\s+", " ", read(page))
        self.assertIn("The library is not installed with `pip`: it is used straight from the kit folder", flat("AGENTS.md"))
        self.assertIn("The library is not installed on your computer.", flat("docs/api.md"))
        self.assertIn("The library is not installed on your computer.", flat("docs/getting-started.md"))
        # one order on every page: first the folder my-project, then the kit goes inside it;
        # a kit that is there already is moved in
        self.assertIn("Make a folder for your project and put the kit inside it", flat("README.md"))
        self.assertIn("First the folder, then the kit inside it. Make a new folder called `my-project`.", flat("docs/getting-started.md"))
        self.assertIn("If the kit is on your computer already, move the whole kit folder into `my-project`.", flat("docs/getting-started.md"))
        self.assertIn("The order is always the same: first the folder `my-project/`, then the kit goes inside it. No kit on the "
                      "computer yet: make `my-project/`, then clone or unpack the kit inside it.", flat("AGENTS.md"))
        self.assertIn("    mkdir my-project\n    mv vexo-open-kit my-project/\n    cd my-project\n", read("AGENTS.md"))
        self.assertIn("    mkdir my-project\n    cd my-project\n", read("README.md"))

    def test_node_examples_are_run_one_way_on_every_page(self):
        # on every page: no argument, or the band number 42 as with the Python examples
        # (the first argument is the band number; the time is the named option --seconds)
        seen = 0
        for page in STUDENT_PAGES:
            for command in re.findall(r"node (?:vexo-open-kit/)?examples/node/[\w.-]+\.mjs[^`\n|]*", read(page)):
                seen += 1
                self.assertRegex(command, r"^node (vexo-open-kit/)?examples/node/[\w-]+\.mjs( 42)?$", "%s: %r" % (page, command))
        self.assertGreaterEqual(seen, 5)

    def test_the_band_number_is_asked_first_and_never_a_placeholder(self):
        rule = re.sub(r"\s+", " ", section(read("AGENTS.md"), "Rule 1: ask for the band number first"))
        for words in ("Before you write any code, ask the team:", "Wait for the answer before you build anything.",
                      "If you cannot ask (you run without a person, or as a helper of another agent), stop and hand the "
                      "question back as your answer.", "never build or run with a placeholder number",
                      "This goes for the pretend band too"):
            self.assertIn(words, rule)

    def test_every_opened_as_a_file_notice_allows_for_another_port(self):
        with open(os.path.join(KIT, "dashboard", "index.html"), encoding="utf-8") as f:
            notices = [f.read()]
        for name in sorted(glob.glob(os.path.join(KIT, "examples", "web", "*.html"))):
            with open(name, encoding="utf-8") as f:
                notices.append(f.read())
        (sample,) = blocks(section(read("AGENTS.md"), "Shortest working program: web page"), "html")
        self.assertEqual(len(notices), 4)
        for text in notices + [sample[2]]:
            self.assertEqual(text.count("(8000, or the port you started it with)"), 1)
            self.assertLess(text.index("http.server 8000 --bind 127.0.0.1"), text.index("(8000, or the port you started it with)"))

    def test_safety_rules_are_on_both_pages(self):
        agents = section(read("AGENTS.md"), "Safety: keys, text from a band, the microphone")
        guide = section(read("docs/getting-started.md"), "Stay safe")
        for text, name in ((agents, "AGENTS.md"), (guide, "docs/getting-started.md")):
            flat = re.sub(r"\s+", " ", text)
            for words in ("key", "commit", "log", "environment variable", "DEEPGRAM_API_KEY", "Deepgram, a company",   # no keys in code
                          "tell an organiser",
                          "Never run", "`textContent`, never with `innerHTML`",                        # band text is data
                          "solid blue light means the microphone is on", "before you record"):         # the microphone
                self.assertIn(words, flat, "%s: %s" % (name, words))
        self.assertIn("never into band storage", re.sub(r"\s+", " ", agents))
        self.assertIn("never store it on the band", re.sub(r"\s+", " ", guide))


class Links(unittest.TestCase):
    """Every link between the pages leads somewhere, and every #anchor is a real heading."""

    @staticmethod
    def anchors(text):
        text = re.sub(r"^```.*?^```$", "", text, flags=re.M | re.S)
        found = set()
        for heading in re.findall(r"^#+ (.*)$", text, re.M):
            slug = re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")
            found.add(slug)
        return found

    def test_links(self):
        for page in PAGES:
            text = re.sub(r"^```.*?^```$", "", read(page), flags=re.M | re.S)
            for target in re.findall(r"\]\(([^)\s]+)\)", text):
                if re.match(r"[a-z]+:", target):
                    continue            # another site
                path, _hash, anchor = target.partition("#")
                full = os.path.normpath(os.path.join(KIT, os.path.dirname(page), path)) if path else os.path.join(KIT, page)
                self.assertTrue(os.path.exists(full), "%s: link to %s, which does not exist" % (page, target))
                if anchor:
                    with open(full, encoding="utf-8") as f:
                        self.assertIn(anchor, self.anchors(f.read()), "%s: no heading for %s" % (page, target))

    def test_files_named_in_the_pages_exist(self):
        # `examples/python/led.py`, `js/src/`, `docs/api.md` ... written as code in the text
        roots = ("docs/", "examples/", "js/", "python/", "dashboard/", "contract/", "scripts/", ".githooks/")
        made_by_the_reader = set()
        for page in PAGES:
            for name in re.findall(r"`([\w./-]+)`", read(page)):
                if name.startswith(roots) and name not in made_by_the_reader:
                    places = (KIT, os.path.join(KIT, os.path.dirname(page)))   # from the kit folder, or from the page
                    self.assertTrue(any(os.path.exists(os.path.join(place, name)) for place in places),
                                    "%s names %s, which does not exist" % (page, name))


if __name__ == "__main__":
    unittest.main()
