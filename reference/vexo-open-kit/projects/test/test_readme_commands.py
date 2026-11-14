"""Follows the project READMEs the way a student does. The student has one folder,
my-project/, with the kit inside it as my-project/vexo-open-kit/, one terminal in
my-project/ and one web server started there. The tests build exactly that in a temporary
place, then run every python command the READMEs print and fetch every
http://localhost:8000/... address they give (and the ones in the "opened as a file" help
of the three pages).

    python3 -m unittest discover -s projects/test -p 'test_readme_commands.py'

Two changes are made to the printed text, both described on the pages themselves: a
command for a real band (`... 42`) gets `--sim`, and 8000 becomes a free port. Every
program also gets a short `--seconds`. So nothing here touches Bluetooth or a USB port.
"""
import glob
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

import running

KIT = os.path.dirname(running.PROJECTS)
NAMES = sorted(name for name in os.listdir(running.PROJECTS)
               if os.path.isfile(os.path.join(running.PROJECTS, name, "README.md")))
READMES = ["README.md"] + [os.path.join(name, "README.md") for name in NAMES]
PAGES = sorted(os.path.relpath(page, running.PROJECTS) for page in glob.glob(os.path.join(running.PROJECTS, "*", "index.html")))
SERVER = "python3 -m http.server 8000 --bind 127.0.0.1"
ADDRESS = re.compile(r"http://localhost:8000(/[^\s<>`\"')]*)")
TEMP = None
MINE = None         # my-project/
HOME = None         # a home folder of its own: the pretend band keeps its storage there


def setUpModule():
    """my-project/ with the kit inside it under the name vexo-open-kit, as every page says.
    Next to the kit: `shared` and the six projects, copied out as "Make one your own" tells."""
    global TEMP, MINE, HOME
    TEMP = tempfile.TemporaryDirectory()
    MINE = os.path.join(TEMP.name, "my-project")
    HOME = os.path.join(TEMP.name, "home")
    os.makedirs(MINE)
    os.makedirs(HOME)
    inside = os.path.join(MINE, "vexo-open-kit")
    try:
        os.symlink(KIT, inside, target_is_directory=True)
    except (OSError, NotImplementedError):          # Windows without the right to make links: copy what is used
        skip = shutil.ignore_patterns("__pycache__", "node_modules", ".git")
        for part in ("python", os.path.join("js", "src"), "projects"):
            shutil.copytree(os.path.join(KIT, part), os.path.join(inside, part), ignore=skip)
    for name in ["shared"] + NAMES:
        shutil.copytree(os.path.join(running.PROJECTS, name), os.path.join(MINE, name),
                        ignore=shutil.ignore_patterns("__pycache__"))


def tearDownModule():
    TEMP.cleanup()


def read(page):
    with open(os.path.join(running.PROJECTS, page), encoding="utf-8") as f:
        return f.read()


def python_commands(text):
    """Every `python3 ...` a page prints: in backticks, or indented on a line of its own."""
    found = re.findall(r"`(python3 [^`\n]+)`", text) + re.findall(r"^ {4,}(python3 .*)$", text, re.M)
    return [command.strip() for command in found]


def needs_server(page):
    """The "opened as a file" help of a page, as the student reads it (no tags)."""
    found = re.search(r'<p id="needs-server">(.*?)</p>', read(page), re.S)
    assert found, "%s has no #needs-server text" % page
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", found.group(1)))


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class ReadmeCommands(unittest.TestCase):
    def run_printed(self, command, where):
        """One printed command, typed in my-project/, with the pretend band and a short run."""
        words = shlex.split(command)
        self.assertEqual(words[0], "python3", command)
        program = words[1]
        self.assertTrue(os.path.isfile(os.path.join(MINE, program)),
                        "%s: `%s` is typed in my-project/, and there is no file %s there" % (where, command, program))
        if "--sim" not in words:
            words.append("--sim")           # "Real band" commands: the same program, with the pretend band
        if "--show" not in words and "--clear" not in words and "--seconds" not in words:
            words += ["--seconds", "2"]
        env = dict(os.environ, HOME=HOME, USERPROFILE=HOME, PYTHONUNBUFFERED="1")
        done = subprocess.run([sys.executable] + words[1:], cwd=MINE, env=env, timeout=60, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual((done.returncode, done.stderr), (0, ""), "%s: `%s`\n%s" % (where, command, done.stdout))
        self.assertRegex(done.stdout, r"Connected to Vexo-HACK-\d+|Diary on Vexo-HACK-\d+", command)

    def test_every_python_command_of_the_readmes_runs_in_my_project(self):
        ran = set()
        for page in READMES:
            for command in python_commands(read(page)):
                if re.match(r"python3 -m (pip install \w+|unittest discover -s projects/test)$", command):
                    continue                # an install hint; the kit's own tests (typed in the kit folder, says the page)
                if re.match(r"python3 -m http\.server \d+ --bind 127\.0\.0\.1$", command):
                    continue                # the web server (8000, or 8001 when that is taken): the next tests start it
                with self.subTest(page=page, command=command):
                    self.run_printed(command, page)
                    ran.add(shlex.split(command)[1])
        # each Python project from inside the kit, and the copied one of "Make one your own"
        for program in ("vexo-open-kit/projects/band-leash/leash.py", "vexo-open-kit/projects/fall-alarm/fall_alarm.py",
                        "vexo-open-kit/projects/step-diary/diary.py", "fall-alarm/fall_alarm.py"):
            self.assertIn(program, ran)

    def test_the_readmes_name_one_folder_for_the_terminal(self):
        for page in READMES:
            text = read(page)
            self.assertIn("`my-project/`", text, page)
            self.assertNotRegex(text, r"[Ii]n the kit folder\b(?! itself)", page)
            for found in re.finditer(r"-m http\.server[^`\n]*", text):
                self.assertRegex(found.group(0), r"^-m http\.server \d+ --bind 127\.0\.0\.1$", page)
        for name in NAMES:
            self.assertRegex(read(os.path.join(name, "README.md")),
                             r"## Run it\n\nIn (your project folder )?`my-project/`, the folder that holds the kit", name)

    def test_the_opened_as_a_file_help_names_the_same_layout(self):
        self.assertEqual(len(PAGES), 3, PAGES)
        for page in PAGES:
            name = os.path.dirname(page)
            text = needs_server(page)
            self.assertIn("in your project folder (my-project, the folder that holds vexo-open-kit), run " + SERVER, text, page)
            self.assertIn("then open http://localhost:8000/vexo-open-kit/projects/%s/ (8000, or the port you started it with)" % name,
                          text, page)
            self.assertEqual([path.rstrip(".,") for path in ADDRESS.findall(text)], ["/vexo-open-kit/projects/%s/" % name, "/%s/" % name], page)


class ReadmeAddresses(unittest.TestCase):
    """One web server, started in my-project/ with the command the pages print."""

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        words = shlex.split(SERVER.replace("8000", str(cls.port)))
        cls.server = subprocess.Popen([sys.executable] + words[1:], cwd=MINE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/" % cls.port, timeout=5).close()
                return
            except OSError:
                time.sleep(0.1)
        cls.tearDownClass()
        raise AssertionError("the web server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(10)

    def status(self, path):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path), timeout=10) as answer:
                return answer.status, answer.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as problem:
            return problem.code, ""

    def test_the_server_command_is_the_one_the_pages_print(self):
        for page in ["README.md"] + [os.path.join(name, "README.md") for name in map(os.path.dirname, PAGES)]:
            self.assertIn(SERVER, read(page), page)

    def test_every_address_of_the_readmes_and_the_pages_answers(self):
        seen = set()
        places = [(page, read(page)) for page in READMES] + [(page, needs_server(page)) for page in PAGES]
        for page, text in places:
            for path in ADDRESS.findall(text):
                path = path.rstrip(".,")
                if path == "/...":
                    continue                # "and so on"
                with self.subTest(page=page, path=path):
                    code, body = self.status(path)
                    self.assertEqual(code, 200, "%s: http://localhost:8000%s does not exist with the server started in my-project/"
                                     % (page, path))
                    self.assertIn('<p id="needs-server">', body, "%s: %s is not a project page" % (page, path))
                    seen.add(path.split("?")[0])
        for name in map(os.path.dirname, PAGES):    # each page inside the kit, and as a copy next to it
            self.assertIn("/vexo-open-kit/projects/%s/" % name, seen)
            self.assertIn("/%s/" % name, seen)

    def test_what_a_page_loads_is_there_too_inside_the_kit_and_as_a_copy(self):
        # the page, its own program, the shared files, and the library the shared kit.js points at
        for name in map(os.path.dirname, PAGES):
            for base in ("/vexo-open-kit/projects/", "/"):
                code, body = self.status(base + name + "/")
                self.assertEqual(code, 200, base + name)
                for file in re.findall(r'(?:src|href)="([^"#:]+)"', body):
                    path = os.path.normpath(os.path.join(base + name, file)).replace(os.sep, "/")
                    self.assertEqual(self.status(path)[0], 200, path)
                self.assertEqual(self.status(base + "shared/kit.js")[0], 200)
        self.assertEqual(self.status("/vexo-open-kit/js/src/index.js")[0], 200)


class OldText(unittest.TestCase):
    """The text these tests were written against a 404 for: it must stay gone."""

    def test_no_address_or_command_of_a_server_in_the_kit_folder_is_left(self):
        old_address = "localhost:8000/" + "projects/"
        old_command = "python3 " + "projects/"
        for path in sorted(glob.glob(os.path.join(running.PROJECTS, "**", "*"), recursive=True)):
            if not path.endswith((".md", ".html", ".js", ".py", ".mjs")) or os.path.dirname(path) == os.path.dirname(os.path.abspath(__file__)):
                continue
            with open(path, encoding="utf-8") as f:
                text = f.read()
            where = os.path.relpath(path, running.PROJECTS)
            self.assertNotIn(old_address, text, where)
            self.assertNotIn(old_command, text, where)
            self.assertNotIn("In the kit " + "folder", text, where)


if __name__ == "__main__":
    unittest.main()
