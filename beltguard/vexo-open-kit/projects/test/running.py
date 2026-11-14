"""Shared by the Python project tests: runs a project the way a student would, as its own
program, but with a home folder of its own. The pretend band keeps its storage in
.vexo-open/ in the home folder (store_file=True), so a test never touches the pretend band
of the person who runs the tests."""
import os
import subprocess
import sys

PROJECTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(program, home, *args, timeout=60, cwd=None):
    """Run projects/<program> with `home` as the home folder, from the folder `cwd`
    (default: the home folder, so the program cannot depend on where it is started)."""
    env = dict(os.environ, HOME=home, USERPROFILE=home, PYTHONUNBUFFERED="1")
    path = program if os.path.isabs(program) else os.path.join(PROJECTS, program)
    return subprocess.run([sys.executable, path] + [str(a) for a in args], cwd=cwd or home, env=env, timeout=timeout,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)


def store_file(home, number):
    """Where the pretend band with this number keeps its storage."""
    return os.path.join(home, ".vexo-open", "pretend-band-%d.json" % number)
