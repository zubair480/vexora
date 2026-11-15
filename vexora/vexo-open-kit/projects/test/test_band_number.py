"""The band number on the command line of every Python project: the same forms as the
kit's examples take (42, 07, Vexo-HACK-42), one friendly sentence for anything else, and
no band 0. Checked inside the kit and in a copy next to it (my-project/shared).

    python3 -m unittest discover -s projects/test -p 'test_band_number.py'
"""
import os
import shutil
import tempfile
import unittest

import running

KIT = os.path.dirname(running.PROJECTS)
NOT_A_NUMBER = 'error: argument number: "%s" is not a band number. Type just the number on your band or its bag, for example 42.'
ZERO = "error: argument number: Band numbers are 1 to 999. 0 is what a band has before it gets a number: ask an organiser to number it."


class BandNumber(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.home = os.path.join(cls.folder.name, "home")
        mine = os.path.join(cls.folder.name, "my-project")
        os.makedirs(cls.home)
        os.makedirs(mine)
        try:
            os.symlink(KIT, os.path.join(mine, "vexo-open-kit"), target_is_directory=True)
        except (OSError, NotImplementedError):
            shutil.copytree(os.path.join(KIT, "python", "vexo_open"), os.path.join(mine, "vexo-open-kit", "python", "vexo_open"),
                            ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("shared", "step-diary"):
            shutil.copytree(os.path.join(running.PROJECTS, name), os.path.join(mine, name), ignore=shutil.ignore_patterns("__pycache__"))
        # the program as it came, and the copy in my-project/ (which uses my-project/shared/start.py)
        cls.programs = (os.path.join("step-diary", "diary.py"), os.path.join(mine, "step-diary", "diary.py"))

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def show(self, program, number):
        return running.run(program, self.home, number, "--sim", "--show")

    def test_the_number_alone_with_a_zero_in_front_and_the_full_name(self):
        for program in self.programs:
            for typed, name in (("42", "Vexo-HACK-42"), ("07", "Vexo-HACK-07"), ("7", "Vexo-HACK-07"), ("007", "Vexo-HACK-07"),
                                ("Vexo-HACK-42", "Vexo-HACK-42"), ("vexo-hack-07", "Vexo-HACK-07"),
                                ("Vexo-HACK-123", "Vexo-HACK-123"), ("999", "Vexo-HACK-999"), ("1", "Vexo-HACK-01")):
                done = self.show(program, typed)
                self.assertEqual((done.returncode, done.stderr), (0, ""), typed)
                self.assertEqual(done.stdout.splitlines()[0], "Diary on %s: 0 entries." % name, typed)

    def test_anything_else_is_one_friendly_sentence(self):
        for program in self.programs:
            for typed in ("abc", "1000", "4.2", "Vexo-HACK-", "Vexo-HACK-7", "Vexo-HACK-1000", "Vexo-42", "\u0664\u0662", "4 2"):
                done = self.show(program, typed)
                self.assertEqual((done.returncode, done.stdout), (2, ""), typed)
                self.assertEqual(done.stderr.strip().splitlines()[-1].split(": ", 1)[1], NOT_A_NUMBER % typed, typed)
                self.assertNotIn("invalid int value", done.stderr)
                self.assertNotIn("Traceback", done.stderr)
        done = self.show(self.programs[0], "-5")        # a minus sign: argparse reads it as an option, and says so in one line
        self.assertEqual(done.returncode, 2)
        self.assertNotIn("Traceback", done.stderr)

    def test_zero_is_a_band_without_a_number(self):
        for program in self.programs:
            for typed in ("0", "00", "000", "Vexo-HACK-00", "Vexo-HACK-000"):
                done = self.show(program, typed)
                self.assertEqual((done.returncode, done.stdout), (2, ""), typed)
                self.assertEqual(done.stderr.strip().splitlines()[-1].split(": ", 1)[1], ZERO, typed)
        # a real band is refused the same way, before anything looks for it
        done = running.run(self.programs[0], self.home, "0")
        self.assertEqual(done.returncode, 2)
        self.assertIn("Band numbers are 1 to 999.", done.stderr)


if __name__ == "__main__":
    unittest.main()
