"""A faulty band must not be able to fill the computer or to keep a program running.
These tests flood a project's own handler (see flood.py) with reports as fast as the
computer can make them, all with the same time, and check three things: what the program
keeps stays under its cap, `--seconds` still ends it, and Ctrl+C still stops it. They also
give the programs a time that no calendar has.

    python3 -m unittest discover -s projects/test -p 'test_flood.py'
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

import running

FLOOD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flood.py")
sys.path.insert(0, os.path.join(running.PROJECTS, "shared"))
_hook = sys.excepthook
import start  # noqa: E402  (projects/shared/start.py: the Inbox and clock() under test)
sys.excepthook = _hook      # start.py shortens band problems for a program; a test run keeps its own
NOW = 1794484800.0
FLOODS = {      # program: (event, report, the list that must stay small, its cap)
    os.path.join("band-leash", "leash.py"): ("link", {"time": NOW, "rssi": -50}, "readings", 100),
    os.path.join("step-diary", "diary.py"): ("activity", {"time": NOW, "steps": 5, "state": "walk", "cadence": 0}, "waiting", 60),
    os.path.join("fall-alarm", "fall_alarm.py"): ("activity", {"time": NOW, "steps": 5, "state": "walk", "cadence": 0}, "happened", 1000),
}


class Flood(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = folder.name

    def start(self, program, event, report, *args, once=False):
        env = dict(os.environ, HOME=self.home, USERPROFILE=self.home, PYTHONUNBUFFERED="1")
        words = [sys.executable, FLOOD, os.path.join(running.PROJECTS, program), event, json.dumps(report),
                 "once" if once else "flood", "--", "919", "--sim"] + [str(a) for a in args]
        return subprocess.Popen(words, cwd=self.home, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True)

    def kept(self, out):
        line = [line for line in out.splitlines() if line.startswith("kept ")][-1]
        return {name: int(count) for name, count in (part.split("=") for part in line.split()[1:])}

    def test_under_a_flood_every_program_ends_after_its_seconds_and_keeps_little(self):
        for program, (event, report, name, cap) in FLOODS.items():
            with self.subTest(program=program):
                began = time.time()
                running_program = self.start(program, event, report, "--seconds", 3)
                try:
                    out, err = running_program.communicate(timeout=20)
                except subprocess.TimeoutExpired:
                    running_program.kill()
                    running_program.communicate()
                    self.fail("%s did not end after --seconds 3 while the band flooded it" % program)
                took = time.time() - began
                self.assertEqual((running_program.returncode, err), (0, ""), out)
                self.assertLess(took, 3 + 5, "--seconds 3 took %.1f seconds" % took)
                kept = self.kept(out)
                self.assertLessEqual(kept[name], cap + (start.KEPT_MOST if name == "happened" else 0), kept)
                self.assertLessEqual(kept["happened"], start.INBOX_MOST + start.KEPT_MOST, kept)

    @unittest.skipIf(os.name != "posix", "Ctrl+C is sent as a signal here")
    def test_under_a_flood_ctrl_c_ends_a_program_at_once(self):
        for program in (os.path.join("band-leash", "leash.py"), os.path.join("step-diary", "diary.py")):
            event, report, _, _ = FLOODS[program]
            with self.subTest(program=program):
                running_program = self.start(program, event, report)        # no --seconds: the story of the pretend band runs 20 seconds
                time.sleep(2.5)
                self.assertIsNone(running_program.poll(), "it ended by itself")
                pressed = time.time()
                running_program.send_signal(signal.SIGINT)
                try:
                    out, err = running_program.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    running_program.kill()
                    running_program.communicate()
                    self.fail("%s went on after Ctrl+C while the band flooded it" % program)
                self.assertLess(time.time() - pressed, 3)
                self.assertEqual((running_program.returncode, err), (0, ""), out)

    def test_a_time_no_calendar_has_is_shown_plainly_and_nothing_fails(self):
        for when in (1e18, 1e300, float("inf"), float("nan"), -1e18):
            with self.subTest(program="fall-alarm", when=when):
                done = self.start(os.path.join("fall-alarm", "fall_alarm.py"), "free-fall", {"time": when}, "--seconds", 1.5,
                                  "--check", 60, once=True)
                out, err = done.communicate(timeout=30)
                self.assertEqual((done.returncode, err), (0, ""), out)
                self.assertRegex(out, r"Fall at time (1e\+18|1e\+300|inf|nan|-1e\+18)\. Is the wearer moving\?")
            with self.subTest(program="step-diary", when=when):
                report = {"time": when, "steps": 5, "state": "walk", "cadence": 0}
                done = self.start(os.path.join("step-diary", "diary.py"), "activity", report, "--every", 1, "--seconds", 2.6, once=True)
                out, err = done.communicate(timeout=30)
                self.assertEqual((done.returncode, err), (0, ""), out)
                self.assertRegex(out, r"(?m)^\d\d:\d\d:\d\d +\d+ steps .* -> box 400", "the entry is saved with the computer's time")
                shown = running.run(os.path.join("step-diary", "diary.py"), self.home, 919, "--sim", "--show", "--clear")
                self.assertEqual((shown.returncode, shown.stderr), (0, ""), shown.stdout)
                self.assertNotIn("0 entries", shown.stdout.splitlines()[0])


class InboxAndClock(unittest.TestCase):
    def test_the_inbox_keeps_the_newest_and_counts_what_it_dropped(self):
        inbox = start.Inbox()
        for n in range(start.INBOX_MOST + 500):
            inbox.put(n)
        self.assertEqual(len(inbox), start.INBOX_MOST)
        self.assertEqual(inbox.dropped, 500)
        taken = inbox.take()
        self.assertEqual((taken[0], taken[-1], len(taken)), (500, start.INBOX_MOST + 499, start.INBOX_MOST))
        self.assertEqual((inbox.take(), len(inbox), inbox.dropped), ([], 0, 500))
        small = start.Inbox(most=3)
        for n in range(5):
            small.put(n)
        self.assertEqual(small.take(), [2, 3, 4])

    def test_link_news_is_never_pushed_out_by_a_flood_of_band_data(self):
        inbox = start.Inbox()
        inbox.put(("link-lost", None), keep=True)
        for n in range(3 * start.INBOX_MOST):
            inbox.put(("reading", n))
        inbox.put(("gone", "lost"), keep=True)
        taken = inbox.take()
        self.assertEqual(taken[:2], [("link-lost", None), ("gone", "lost")], "link news comes first, in its own order")
        self.assertEqual((len(taken), inbox.dropped, len(inbox)), (start.INBOX_MOST + 2, 2 * start.INBOX_MOST, 0))
        for n in range(start.KEPT_MOST + 50):
            inbox.put(n, keep=True)
        self.assertEqual(len(inbox), start.KEPT_MOST, "link news has a cap of its own")

    def test_take_gives_what_is_there_now_and_comes_back_while_a_handler_keeps_putting(self):
        import threading
        inbox, stop = start.Inbox(), threading.Event()

        def flood():
            while not stop.is_set():
                inbox.put("x")
        thread = threading.Thread(target=flood, daemon=True)
        thread.start()
        try:
            began = time.time()
            for _ in range(50):
                self.assertLessEqual(len(inbox.take()), start.INBOX_MOST)
            self.assertLess(time.time() - began, 5)
        finally:
            stop.set()
            thread.join(5)

    def test_clock_is_text_for_every_number(self):
        self.assertRegex(start.clock(NOW), r"^\d\d:\d\d:\d\d$")
        self.assertRegex(start.clock(0), r"^\d\d:\d\d:\d\d$")
        for when, text in ((1e18, "time 1e+18"), (1e300, "time 1e+300"), (float("inf"), "time inf"), (float("nan"), "time nan"),
                           (-1e300, "time -1e+300"), (None, "time unknown"), ("12", "time unknown")):
            self.assertEqual(start.clock(when), text)


if __name__ == "__main__":
    unittest.main()
