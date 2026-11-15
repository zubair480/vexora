"""Runs every Python example against the pretend band and checks what it prints.

    python3 -m unittest discover -s examples/test
"""
import datetime
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest

EXAMPLES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(EXAMPLES, "python")

# The short examples: the whole program, without imports, comments and blank lines, must
# stay at 10 lines or fewer (led.py: 11, it says what it does before each of its four steps).
# The longer ones have their own limit.
MAX_LINES = {
    "python/led.py": 11, "python/motion.py": 10, "python/fast_motion.py": 22, "python/taps.py": 10, "python/storage.py": 10,
    "python/reconnect.py": 15,
    "web/led.js": 10, "web/taps.js": 10, "node/hello.mjs": 10,
    "node/fast-motion.mjs": 25, "python/motion_graph.py": 25, "web/motion-graph.js": 25, "python/audio_to_text.py": 41,
    "python/ask_the_band.py": 12, "python/voice_commands.py": 24, "python/meeting_notes.py": 20,
}


# No test here uses a real speech-service key, also when the person running them has one set.
NO_KEY = {k: v for k, v in os.environ.items() if k not in ("DEEPGRAM_API_KEY", "OPENAI_API_KEY", "OPENAI_MODEL")}


def run(name, *args, env=None, timeout=60, folder=None):
    """Run one example from an empty folder (so it cannot depend on where it is started)."""
    with tempfile.TemporaryDirectory() as empty:
        folder = folder or empty
        done = subprocess.run([sys.executable, os.path.join(PY, name)] + list(args), cwd=folder,
                              env=dict(NO_KEY, **(env or {})), timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    return done


def code_lines(path):
    with open(path, encoding="utf-8") as f:
        lines = [line.strip() for line in f]
    return [line for line in lines
            if line and not line.startswith(("#", "//", "import ", "from "))]


class PythonExamples(unittest.TestCase):
    def ok(self, name, *args, **kw):
        done = run(name, *args, **kw)
        self.assertEqual(done.returncode, 0, "%s failed:\n%s\n%s" % (name, done.stdout, done.stderr))
        self.assertNotIn("Traceback", done.stderr)
        return done.stdout

    def test_led(self):
        out = self.ok("led.py", "--sim", "--seconds", "0.2")
        self.assertIn("Connected to Vexo-HACK-42", out)
        for colour in ("#ff0000", "#00ff00", "#0000ff"):
            self.assertIn("LED " + colour, out)
        # and it says what it is doing during the last part, when nothing else is printed
        self.assertTrue(out.endswith("LED #ffffff, blinking for 0.2 seconds, then off\n"), out)
        self.assertIn("blinking for 3 seconds", self.ok("led.py", "--sim"))

    def test_band_number_is_used(self):
        self.assertIn("Connected to Vexo-HACK-07", self.ok("led.py", "7", "--sim", "--seconds", "0"))

    def test_motion(self):
        reading = r"^(\d\d:\d\d:\d\d\.\d\d\d)   x [+-]\d\.\d\d   y [+-]\d\.\d\d   z [+-]\d\.\d\d$"
        out = self.ok("motion.py", "--sim", "--seconds", "1.5")
        times = re.findall(reading, out, re.M)   # each line starts with the real time of the reading
        self.assertGreaterEqual(len(times), 20)
        now = datetime.datetime.now()
        first = datetime.datetime.combine(now.date(), datetime.time.fromisoformat(times[0]))
        self.assertLess(abs((now - first).total_seconds()) % 86400, 60)   # the computer's clock, just now
        self.assertEqual(times, sorted(times))
        more = self.ok("motion.py", "--sim", "--seconds", "1.5", "--rate", "100")
        self.assertGreaterEqual(len(re.findall(reading, more, re.M)), 100)
        self.assertGreater(len(re.findall(reading, self.ok("motion.py", "--sim", "--seconds", "1", "--rate",
                                                            "fastest"), re.M)), 300)

    def test_fast_motion(self):
        out = self.ok("fast_motion.py", "--sim", "--seconds", "1.5")
        self.assertIn("This link allows: accel 833, gyro 833, both 416 readings a second.", out)
        asked, measured = re.search(r"Asked for (\d+) a second, measured (\d+\.\d) a second\.", out).groups()
        self.assertEqual(asked, "833")
        self.assertTrue(800 < float(measured) < 833, measured)   # the pretend sensor's clock is a little slow
        self.assertIn("Readings lost on the way: 0\n", out)
        # "Asked for 833, measured 865.2" read like a fault on a real band: the output says it is not
        self.assertIn("A few percent apart is normal, not a fault", out)
        self.assertIn("m.time and m.t, is measured by the band and is the truth", out)

    def test_taps(self):
        out = self.ok("taps.py", "--sim", "--seconds", "8.6")   # the pretend band taps at 4 s and 8 s
        self.assertIn("The band felt: tap\n", out)
        self.assertIn("The band felt: double-tap\n", out)

    def test_storage(self):
        out = self.ok("storage.py", "--sim")
        self.assertIn("next person who connects to this band can read, change or erase", out)
        self.assertIn("Found in box 7: None", out)
        self.assertIn("read back: hello from Vexo-HACK-42", out)
        self.assertIn("Boxes in use: 1", out)
        self.assertIn("Removed it again: box 7 is empty for the next team.", out)

    def test_storage_leaves_nothing_on_the_band_for_the_next_team(self):
        # Seen on a real band: the example's text was still there for the next team. Run it
        # twice on one pretend band that keeps its storage in a file: the second run finds box 7 empty.
        with tempfile.TemporaryDirectory() as folder:
            wrapper = os.path.join(folder, "run_storage.py")
            with open(wrapper, "w") as f:
                f.write("import runpy, sys\nsys.path.insert(0, %r)\nimport kit\n"
                        "from vexo_open.sync import Band\nreal = Band.connect\n"
                        "Band.connect = classmethod(lambda cls, number, transport: real(42, transport='sim', store_file=%r))\n"
                        "sys.argv = ['storage.py', '--sim']\nrunpy.run_path(%r, run_name='__main__')\n"
                        % (PY, os.path.join(folder, "band.json"), os.path.join(PY, "storage.py")))
            for _ in range(2):
                done = subprocess.run([sys.executable, wrapper], capture_output=True, text=True, timeout=60)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertIn("Found in box 7: None", done.stdout)
                self.assertIn("Boxes in use: 1", done.stdout)

    def test_text_from_a_band_is_printed_without_its_control_characters(self):
        # Text in storage was written by whoever connected before. It must not reach the
        # terminal raw: an escape sequence in it could move the cursor, hide a line or set the title.
        sys.path.insert(0, PY)
        try:
            import kit
        finally:
            sys.path.remove(PY)
        self.assertEqual(kit.plain("hello from Vexo-HACK-42"), "hello from Vexo-HACK-42")
        self.assertEqual(kit.plain("caf\u00e9 \u4f60\u597d"), "caf\u00e9 \u4f60\u597d")       # other languages stay as they are
        self.assertIsNone(kit.plain(None))
        self.assertEqual(kit.plain("\x1b]0;title\x07a\x1b[2Jb\r\nc\td\x7f\x9b1m\x00"),
                         "\\x1b]0;title\\x07a\\x1b[2Jb\\x0d\\x0ac\\x09d\\x7f\\x9b1m\\x00")
        self.assertEqual(kit.plain("abc\u202edef\u2028"), "abc\\u202edef\\u2028")
        for code in list(range(0x20)) + list(range(0x7f, 0xa0)):
            self.assertEqual(kit.plain(chr(code)), "\\x%02x" % code)
        # and storage.py itself, with a band whose box 7 holds such a text
        evil = "\x1b]0;gone\x07\x1b[2K\rFound in box 7: nothing\n"
        with tempfile.TemporaryDirectory() as folder:
            wrapper = os.path.join(folder, "run_storage.py")
            with open(wrapper, "w", encoding="utf-8") as f:
                f.write("import runpy, sys\nsys.path.insert(0, %r)\nimport kit\nfrom vexo_open.sync import Band\n"
                        "real = Band.connect\n\n\ndef connect(*a, **k):\n    band = real(*a, **k)\n"
                        "    band.storage.put(7, %r)\n    return band\n\n\nBand.connect = connect\n"
                        "sys.argv = ['storage.py', '--sim']\nrunpy.run_path(%r, run_name='__main__')\n"
                        % (PY, evil, os.path.join(PY, "storage.py")))
            done = subprocess.run([sys.executable, wrapper], cwd=folder, timeout=60,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertIn("Found in box 7: \\x1b]0;gone\\x07\\x1b[2K\\x0dFound in box 7: nothing\\x0a\n", done.stdout)
        for code in ("\x1b", "\x07", "\r"):
            self.assertNotIn(code, done.stdout)

    def test_nothing_grows_with_what_the_band_sends(self):
        # fast_motion.py counts its readings and keeps two; audio_to_text.py keeps at most
        # 50 packets for each second it was asked to listen; the graphs keep a fixed number.
        def text(name):
            with open(os.path.join(EXAMPLES, name), encoding="utf-8") as f:
                return f.read()
        self.assertNotIn(".append", text("python/fast_motion.py"))
        self.assertIn("most = min(int(args.seconds * 50) + 50, ", text("python/audio_to_text.py"))
        self.assertIn("    if ogg.frames < most:\n", text("python/audio_to_text.py"))
        self.assertIn("maxlen=200", text("python/motion_graph.py"))
        self.assertIn(".slice(-KEEP)", text("web/motion-graph.js"))
        self.assertIn(".slice(0, 20)", text("web/taps.js"))
        for name in ("python/motion.py", "python/taps.py", "python/reconnect.py", "python/led.py", "python/storage.py",
                     "node/hello.mjs", "node/fast-motion.mjs", "web/led.js"):
            self.assertNotRegex(text(name), r"\.(append|push)\(|\[\]", name)   # no list at all

    def test_audio_saves_an_ogg_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "sound.ogg")
            out = self.ok("audio_to_text.py", "--sim", "--seconds", "1", "--out", path, "--text", "no")
            with open(path, "rb") as f:
                data = f.read()
        self.assertIn("solid blue", out)
        self.assertIn("refuses LED commands", out)
        # a pretend band with no recording: the program says it is silent and names a real file to play
        self.assertNotIn("Speak now", out)
        self.assertIn("The pretend band has no microphone: it sends silence. To hear it work, add:   --replay ", out)
        self.assertRegex(out, r"(?m)--replay \S+sample-speech\.opus$")
        self.assertIn("Listening to Vexo-HACK-42 for 1 seconds.\n", out)
        self.assertRegex(out, r"Saved 1\.\d seconds of sound to .*sound\.ogg \(0 packets lost")
        self.assertTrue(data.startswith(b"OggS"))
        self.assertIn(b"OpusHead", data[:64])
        self.assertGreater(len(data), 2000)   # about 3 kB for one second

    def test_audio_saves_into_the_folder_you_are_in(self):
        with tempfile.TemporaryDirectory() as folder:
            before = set(os.listdir(PY))
            out = self.ok("audio_to_text.py", "--sim", "--seconds", "0.5", "--text", "no", folder=folder)
            self.assertEqual(os.listdir(folder), ["recording.ogg"])
            self.assertEqual(set(os.listdir(PY)), before, "nothing new in the kit's examples folder")
        self.assertIn("seconds of sound to recording.ogg (", out)

    def test_audio_replays_the_voice_file_in_the_kit(self):
        # the command the program itself suggests, typed in a project folder with the kit somewhere else
        with tempfile.TemporaryDirectory() as folder:
            silent = self.ok("audio_to_text.py", "--sim", "--seconds", "0.2", "--text", "no", folder=folder)
            voice = re.search(r"add:   --replay (\S+)$", silent, re.M).group(1)
            self.assertTrue(os.path.isfile(os.path.join(folder, voice)), voice)
            out = self.ok("audio_to_text.py", "--sim", "--seconds", "1", "--text", "no", "--replay", voice, folder=folder)
            self.assertNotIn("Speak now", out)
            self.assertNotIn("sends silence", out)
            sys.path.insert(0, PY)
            try:
                from ogg_opus import read_packets
            finally:
                sys.path.remove(PY)
            got = read_packets(os.path.join(folder, "recording.ogg"))
            self.assertEqual(got, read_packets(os.path.join(folder, voice))[:len(got)])
            self.assertGreaterEqual(len(got), 40)

    def test_audio_without_a_key_says_so_kindly_and_still_saves_the_sound(self):
        with tempfile.TemporaryDirectory() as folder:
            out = self.ok("audio_to_text.py", "--sim", "--seconds", "0.3", folder=folder)
            self.assertEqual(os.listdir(folder), ["recording.ogg"])
        self.assertIn("No text this time. Transcription needs a Deepgram key: see docs/deepgram.md. ", out)
        self.assertIn("environment variable DEEPGRAM_API_KEY", out)
        self.assertIn("The sound is still saved", out)
        self.assertNotIn("sent over the internet", out)          # nothing was sent, and nothing claims it was
        self.assertRegex(out, r"Saved 0\.\d seconds of sound to recording\.ogg")

    def test_audio_with_a_key_prints_the_text_from_the_service(self):
        # a pretend Deepgram on this computer and a made-up key: nothing reaches the internet
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        try:
            from deepgram_mock import MockDeepgram, launcher
        finally:
            sys.path.pop(0)
        with MockDeepgram() as server, tempfile.TemporaryDirectory() as folder:
            done = subprocess.run([sys.executable, "-c", launcher(server.url, PY), os.path.join(PY, "audio_to_text.py"),
                                   "--sim", "--seconds", "1.5", "--replay", os.path.join(os.path.dirname(EXAMPLES), "dashboard", "speech", "sample-speech.opus")],
                                  cwd=folder, env=dict(NO_KEY, DEEPGRAM_API_KEY="k-test"), timeout=60,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
        out = done.stdout
        self.assertLess(out.index("everything the band's microphone hears is sent over the internet to Deepgram"),
                        out.index("Listening to Vexo-HACK-42"))
        self.assertIn("Speech is turned into text by Deepgram (deepgram.com), online (model nova-3).", out)
        self.assertIn("Heard: Sentence 1.\n", out)
        self.assertRegex(out, r"(?m)^Text: Sentence 1\. .*The end\.$")
        self.assertNotIn("k-test", out)
        self.assertGreaterEqual(len(server.sound), 70)
        self.assertNotIn("k-test", server.requests[0]["path"])
        self.assertIn("mip_opt_out=true", server.requests[0]["path"])

    def test_audio_replays_a_file_through_the_pretend_band(self):
        sys.path.insert(0, PY)
        try:
            from ogg_opus import OggOpusWriter, read_packets
        finally:
            sys.path.remove(PY)
        sent = [bytes([0xB8, n, n ^ 0x5A]) + bytes(n % 7) for n in range(25)]
        with tempfile.TemporaryDirectory() as folder:
            source, copy = os.path.join(folder, "in.ogg"), os.path.join(folder, "out.ogg")
            ogg = OggOpusWriter(source)
            for seq, packet in enumerate(sent):
                ogg.write(seq, packet)
            ogg.close()
            self.ok("audio_to_text.py", "--sim", "--seconds", "1", "--replay", source,
                    "--out", copy, "--text", "no")
            got = read_packets(copy)
        # The pretend band plays the file in a loop; the example listens to it once and stops
        # at its end, so a run longer than the file does not add its first words a second time.
        self.assertEqual(got, sent)

    @unittest.skipUnless(importlib.util.find_spec("matplotlib"), "matplotlib is not installed")
    def test_motion_graph(self):
        with tempfile.TemporaryDirectory() as folder:
            out = self.ok("motion_graph.py", "--sim", "--seconds", "1.5",
                          env={"MPLBACKEND": "Agg", "MPLCONFIGDIR": folder})
        self.assertRegex(out, r"Drew the graph\. Last reading: x [+-]\d\.\d\d  y [+-]\d\.\d\d  z \+[01]\.\d\d")

    @unittest.skipIf(importlib.util.find_spec("matplotlib"), "matplotlib is installed")
    def test_motion_graph_says_what_to_install(self):
        done = run("motion_graph.py", "--sim", "--seconds", "1")
        self.assertEqual(done.returncode, 1)
        self.assertIn("pip install matplotlib", done.stderr)

    def test_no_band_number_says_what_to_add(self):
        # The command in the message is the one the student typed, path and all: in
        # my-project/ that is "python3 vexo-open-kit/examples/python/led.py", not "python3 led.py".
        python = "python" if os.name == "nt" else "python3"
        with tempfile.TemporaryDirectory() as project:
            os.symlink(os.path.dirname(EXAMPLES), os.path.join(project, "vexo-open-kit"))
            for typed in ("vexo-open-kit/examples/python/led.py", os.path.join(PY, "led.py")):
                done = subprocess.run([sys.executable, typed], cwd=project, timeout=60,   # Bluetooth with no band number
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
                self.assertEqual(done.returncode, 2)
                self.assertEqual(done.stdout, "")
                self.assertIn("led.py: error: add your band number, or --sim for a pretend band:   "
                              "%s %s 42   or   %s %s --sim\n" % (python, typed, python, typed), done.stderr)
                self.assertNotIn("Traceback", done.stderr)
                self.assertNotIn("Problem", done.stderr)

    def test_the_band_number_may_be_the_full_name_and_a_wrong_one_says_what_to_type(self):
        for typed in ("42", "07", "Vexo-HACK-42", "vexo-hack-42"):
            done = run("led.py", typed, "--sim")
            self.assertEqual((done.returncode, done.stderr), (0, ""), typed)
            self.assertIn("Connected to Vexo-HACK-%s" % typed[-2:], done.stdout, typed)
        for typed in ("abc", "4x", "1234", "Vexo-HACK-", "HACK-42"):
            done = run("led.py", typed, "--sim")
            self.assertEqual(done.returncode, 2, typed)
            self.assertIn('led.py: error: argument number: "%s" is not a band number. Type just the number on your '
                          'band or its bag, for example 42.' % typed, done.stderr)
            self.assertNotIn("invalid int value", done.stderr)
            self.assertNotIn("Traceback", done.stderr)

    def test_a_problem_is_one_sentence_not_a_traceback(self):
        done = run("motion.py", "--sim", "--rate", "7", "--seconds", "1")   # a rate the band does not have
        self.assertEqual(done.returncode, 1)
        lines = done.stderr.splitlines()
        self.assertRegex(lines[0], r"^Problem \(bad-arg\): ")
        self.assertRegex(lines[1], r"^  \(line \d+ of motion\.py\)$")
        self.assertEqual(len(lines), 2)
        self.assertNotIn("Traceback", done.stderr)


class Housekeeping(unittest.TestCase):
    def test_examples_stay_short(self):
        for name, limit in MAX_LINES.items():
            count = len(code_lines(os.path.join(EXAMPLES, name)))
            self.assertLessEqual(count, limit, "%s has %d lines of code (limit %d)" % (name, count, limit))

    def test_every_example_is_in_the_table(self):
        with open(os.path.join(EXAMPLES, "README.md"), encoding="utf-8") as f:
            table = f.read()
        for folder, ending in (("python", ".py"), ("web", ".html"), ("node", ".mjs")):
            for name in self.examples(folder, ending):
                self.assertIn("%s/%s" % (folder, name), table)

    def test_every_example_has_a_length_limit(self):
        for folder, ending in (("python", ".py"), ("web", ".js"), ("node", ".mjs")):
            for name in self.examples(folder, ending):
                self.assertIn("%s/%s" % (folder, name), MAX_LINES)

    @staticmethod
    def examples(folder, ending):
        """The kit's own examples in one folder. A file a student saved there is not one:
        only files that git knows count. In a copy without git (a ZIP download) nobody can
        tell the two apart, so there only the examples named in MAX_LINES count."""
        helpers = {"kit.py", "ogg_opus.py", "kit.js", "kit.mjs", "speech.py", "speech_accuracy.py", "tiny_websocket.py",
                   "ai.py", "check_keys.py"}
        names = [name for name in sorted(os.listdir(os.path.join(EXAMPLES, folder)))
                 if name.endswith(ending) and name not in helpers]
        tracked = kit_files()
        if tracked is None:
            return [name for name in names if "%s/%s" % (folder, name) in MAX_LINES]
        return [name for name in names if "examples/%s/%s" % (folder, name) in tracked]

    def test_a_students_file_in_the_examples_folder_breaks_nothing(self):
        # Someone saves first.py next to led.py. The table and the length limits are about
        # the kit's examples, so both checks must still pass, here in the real kit ...
        name = "zz_a_students_first_program.py"
        path = os.path.join(PY, name)
        self.assertFalse(os.path.exists(path))
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("import kit\nprint('mine')\n" + "x = 1\n" * 60)
            self.assertNotIn(name, self.examples("python", ".py"))
            self.assertIn("led.py", self.examples("python", ".py"))
            self.test_every_example_is_in_the_table()
            self.test_every_example_has_a_length_limit()
        finally:
            os.remove(path)

    def test_a_students_file_breaks_nothing_in_a_copy_without_git(self):
        # ... and in an unzipped copy, where there is no git to ask.
        import shutil
        with tempfile.TemporaryDirectory() as folder:
            copy = os.path.join(folder, "vexo-open-kit", "examples")
            skip = shutil.ignore_patterns("__pycache__", "*.ogg", "*.pyc")
            for part in ("python", "web", "node"):
                shutil.copytree(os.path.join(EXAMPLES, part), os.path.join(copy, part), ignore=skip)
            os.makedirs(os.path.join(copy, "test"))
            shutil.copy(os.path.abspath(__file__), os.path.join(copy, "test", "test_examples.py"))
            shutil.copy(os.path.join(EXAMPLES, "README.md"), os.path.join(copy, "README.md"))
            for part, name in (("python", "first.py"), ("web", "mine.js"), ("web", "mine.html"), ("node", "mine.mjs")):
                with open(os.path.join(copy, part, name), "w", encoding="utf-8") as f:
                    f.write("x = 1\n" * 60)
            done = subprocess.run(
                [sys.executable, "-m", "unittest", "test_examples.Housekeeping.test_examples_stay_short",
                 "test_examples.Housekeeping.test_every_example_is_in_the_table",
                 "test_examples.Housekeeping.test_every_example_has_a_length_limit"],
                cwd=os.path.join(copy, "test"), env=dict(os.environ, GIT_CEILING_DIRECTORIES=folder),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Ran 3 tests", done.stderr)


def kit_files():
    """The paths git keeps for this kit ("examples/python/led.py", ...), or None when this
    copy of the kit is not a git repository of its own (or git is not installed)."""
    kit = os.path.dirname(EXAMPLES)
    try:
        top = subprocess.run(["git", "-C", kit, "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, universal_newlines=True)
        if top.returncode != 0 or os.path.realpath(top.stdout.strip()) != os.path.realpath(kit):
            return None
        listed = subprocess.run(["git", "-C", kit, "ls-files", "--", "examples"], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, universal_newlines=True)
    except OSError:
        return None
    return set(listed.stdout.split("\n")) if listed.returncode == 0 else None

if __name__ == "__main__":
    unittest.main()
