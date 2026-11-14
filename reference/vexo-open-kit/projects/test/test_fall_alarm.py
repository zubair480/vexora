"""Runs the fall alarm against the pretend band and checks the story it tells.

    python3 -m unittest discover -s projects/test
"""
import os
import tempfile
import unittest

import running


def run(*args):
    with tempfile.TemporaryDirectory() as home:
        done = running.run(os.path.join("fall-alarm", "fall_alarm.py"), home, *args)
        folder = os.path.join(home, ".vexo-open")
        done.left_behind = os.listdir(folder) if os.path.isdir(folder) else []
        return done


class FallAlarm(unittest.TestCase):

    def test_three_falls_cancel_false_alarm_and_alert(self):
        done = run("912", "--sim", "--check", "1", "--countdown", "2", "--seconds", "17")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stderr, "")
        lines = [line for line in done.stdout.splitlines() if not line.startswith("  ")]
        self.assertEqual(lines[0], "Connected to Vexo-HACK-912. Watching for falls. Stop with Ctrl+C.")
        told = [line.split(" at ")[0] if line.startswith("Fall at") else line for line in lines[1:]]
        self.assertEqual(told, [
            "Fall",                                                     # 1: lies still, cancelled by a double tap
            "No movement for 1 seconds. Double-tap the band to cancel!",
            "Double tap: alarm cancelled. Watching again.",
            "Fall",                                                     # 2: gets up
            "The wearer is moving. No alarm.",
            "Fall",                                                     # 3: nobody reacts
            "No movement for 1 seconds. Double-tap the band to cancel!",
            lines[-2],
            "Double-tap the band to reset.",
        ])
        self.assertRegex(lines[-2], r"^ALERT: fall at \d\d:\d\d:\d\d and no reaction\.")
        self.assertIn("  alarm in 2...\n  alarm in 1...\nALERT", done.stdout)
        self.assertEqual(done.left_behind, [], "a project that stores nothing writes no file")

    def test_a_real_band_needs_its_number(self):
        done = run()
        self.assertEqual(done.returncode, 2)
        self.assertIn("give your band number", done.stderr)


if __name__ == "__main__":
    unittest.main()
