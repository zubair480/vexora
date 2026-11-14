"""Runs the band leash against the pretend band and checks the story it tells.

    python3 -m unittest discover -s projects/test -p 'test_band_leash.py'
"""
import ast
import os
import re
import tempfile
import unittest

import running

BAND = 914
METER = re.compile(r"^ *(-\d+) dBm \[#*-*\] (near|getting far|far)$")


def run(*args):
    with tempfile.TemporaryDirectory() as home:
        return running.run(os.path.join("band-leash", "leash.py"), home, *args)


def zones(lines):
    """The zones of the meter lines, each change once: near, getting far, far, ..."""
    seen = []
    for line in lines:
        found = METER.match(line)
        if found and seen[-1:] != [found.group(2)]:
            seen.append(found.group(2))
    return seen


def meter_of_the_program():
    """The program's own meter() and the numbers at its top, without running the program:
    the plain `NAME = number` lines and the function are cut out of leash.py."""
    with open(os.path.join(running.PROJECTS, "band-leash", "leash.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    keep = [node for node in tree.body
            if (isinstance(node, ast.Assign) and isinstance(node.value, (ast.Constant, ast.UnaryOp)))
            or (isinstance(node, ast.FunctionDef) and node.name == "meter")]
    names = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "leash.py", "exec"), names)
    return names


class BandLeash(unittest.TestCase):
    def test_the_meter_is_full_next_to_the_laptop_and_not_before(self):
        # Measured on a real band: -18 to -19 dBm next to the laptop. The meter was full
        # at -35 already, so it could not show anything nearer than that.
        names = meter_of_the_program()
        cells = lambda dbm: names["meter"](dbm, "near").count("#")     # noqa: E731
        self.assertEqual(names["METER_FULL_DBM"], -20)
        self.assertEqual([cells(dbm) for dbm in (-18, -19, -20)], [12, 12, 12])
        self.assertLess(cells(-35), 12)
        self.assertLess(cells(-48), cells(-35))                 # where the pretend band starts
        self.assertEqual([cells(-95), cells(-120), cells(0)], [0, 0, 12])
        self.assertEqual([cells(dbm) for dbm in range(-100, 1)], sorted(cells(dbm) for dbm in range(-100, 1)))
        self.assertRegex(names["meter"](-72, "getting far"), METER)
        self.assertLess(names["FAR_DBM"], names["NEAR_DBM"])
        self.assertLess(names["NEAR_DBM"], names["METER_FULL_DBM"])

    def test_walks_away_is_left_behind_and_comes_back(self):
        done = run(str(BAND), "--sim")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stderr, "")
        lines = [line for line in done.stdout.splitlines() if not line.startswith("  [pretend band]")]
        self.assertEqual(lines[0], "Connected to Vexo-HACK-914. Near is above -60 dBm, far is below -80 dBm. Stop with Ctrl+C.")
        self.assertEqual(zones(lines), ["near", "getting far", "far", "near"])  # no flicker between zones
        self.assertLess(len(lines), 30, "too many lines: the meter must not fill the screen")

        warning = "WARNING: the band is far away. Did you forget something?"
        self.assertEqual(lines.count(warning), 1)
        self.assertRegex(lines[lines.index(warning) + 1], r"dBm \[#*-*\] far$")
        left = [i for i, line in enumerate(lines) if "LEFT BEHIND" in line]
        self.assertEqual(len(left), 1)
        self.assertGreater(left[0], lines.index(warning))
        self.assertRegex(lines[left[0]], r"^\a\*\*\* LEFT BEHIND at \d\d:\d\d:\d\d! The link is lost, last signal -(8|9)\d dBm \(far\)\. \*\*\*$")
        self.assertEqual(lines[left[0] + 1], "Looking for the band...")
        self.assertRegex(lines[left[0] + 2], r"^Welcome back! The band was away for [3-6] seconds\.$")
        self.assertRegex(lines[left[0] + 3], r"dBm \[#*-*\] near$")

    def test_borders_come_from_the_command_line(self):
        done = run(str(BAND), "--sim", "--near", "-40", "--far", "-58", "--seconds", "8")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stderr, "")
        self.assertEqual(zones(done.stdout.splitlines()), ["getting far", "far"])

    def test_the_cable_has_no_signal_strength(self):
        done = run(str(BAND), "--usb")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stderr, "")
        self.assertIn("Signal strength needs Bluetooth", done.stdout)

    def test_a_real_band_needs_its_number(self):
        done = run()
        self.assertEqual(done.returncode, 2)
        self.assertIn("give your band number", done.stderr)


if __name__ == "__main__":
    unittest.main()
