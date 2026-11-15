"""A project copied out of the kit into the student's own folder runs there unchanged:

    my-project/
      vexo-open-kit/     the kit
      shared/            copied from vexo-open-kit/projects/shared
      band-leash/        copied from vexo-open-kit/projects/band-leash

    python3 -m unittest discover -s projects/test -p 'test_my_project.py'
"""
import os
import shutil
import tempfile
import unittest

import running

KIT = os.path.dirname(running.PROJECTS)


class MyProject(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = os.path.join(folder.name, "home")
        self.mine = os.path.join(folder.name, "my-project")
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.mine, "vexo-open-kit"))
        for name in ("shared", "band-leash", "step-diary"):
            shutil.copytree(os.path.join(running.PROJECTS, name), os.path.join(self.mine, name))

    def test_copied_next_to_the_kit_it_runs_as_it_is(self):
        shutil.copytree(os.path.join(KIT, "python", "vexo_open"), os.path.join(self.mine, "vexo-open-kit", "python", "vexo_open"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        done = running.run(os.path.join(self.mine, "band-leash", "leash.py"), self.home, 915, "--sim", "--seconds", 2, cwd=self.mine)
        self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
        self.assertIn("Connected to Vexo-HACK-915.", done.stdout)
        done = running.run(os.path.join(self.mine, "step-diary", "diary.py"), self.home, 915, "--sim", "--show", cwd=self.home)
        self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
        self.assertIn("Diary on Vexo-HACK-915: 0 entries.", done.stdout)

    def test_without_the_kit_next_to_it_one_sentence_says_what_is_missing(self):
        done = running.run(os.path.join(self.mine, "band-leash", "leash.py"), self.home, "--sim", cwd=self.mine)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, "")
        self.assertEqual(len(done.stderr.strip().splitlines()), 1, done.stderr)
        self.assertIn("my-project/vexo-open-kit", done.stderr)


if __name__ == "__main__":
    unittest.main()
