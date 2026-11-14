"""vexo_open.sync: a band problem (VexoError) raised inside a handler is shown as one
sentence, like one that ends the main program, and the program goes on. A bug in the
handler keeps its traceback. Each case runs a small program of its own, as a student would."""
import os
import subprocess
import sys
import tempfile
import unittest

PYTHON = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROGRAM = """import sys
sys.path.insert(0, %r)
from vexo_open.sync import Band, VexoError
from vexo_open.transports.sim import SimTransport
band = Band.connect(42, transport=SimTransport(42, auto_tap=False))
band.configure(events=True, mic_level=True)
""" % PYTHON
END = """band.transport.tap("tap")
band.wait(0.5)
print("still running, connected:", band.connected)
print("name:", band.get_name())
band.disconnect()
"""


class HandlerErrors(unittest.TestCase):
    def run_program(self, handler_lines, env=None):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "app.py"), "w", encoding="utf-8") as f:
                f.write(PROGRAM + handler_lines + END)
            env = dict({k: v for k, v in os.environ.items() if k != "VEXO_TRACEBACK"}, **(env or {}))
            return subprocess.run([sys.executable, "app.py"], cwd=folder, timeout=60, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

    GOES_ON = "still running, connected: True\nname: Vexo-HACK-42\n"

    def test_a_band_problem_in_a_handler_is_one_sentence_and_the_program_goes_on(self):
        # the LED while the microphone is on: the band refuses, the handler does not catch it
        done = self.run_program('def on_tap(e):\n    band.set_led("#ff0000")\n    print("not reached")\n'
                                'band.on("tap", on_tap)\n')
        lines = done.stderr.splitlines()
        self.assertRegex(lines[0], r"^Problem \(denied\): .*microphone")
        self.assertEqual(lines[1:], ['  (line 8 of app.py, in your "tap" handler)'])
        self.assertNotIn("Traceback", done.stderr + done.stdout)
        self.assertEqual((done.returncode, done.stdout), (0, self.GOES_ON))

    def test_a_lambda_handler_and_a_problem_found_before_sending(self):
        done = self.run_program('band.on("tap", lambda e: band.set_led("not a colour"))\n')
        self.assertRegex(done.stderr, r'^Problem \(bad-colour\): .*\n  \(line 7 of app\.py, in your "tap" handler\)\n$')
        self.assertNotIn("Traceback", done.stderr)
        self.assertEqual((done.returncode, done.stdout), (0, self.GOES_ON))

    def test_every_time_the_handler_fails_it_says_so_again(self):
        done = self.run_program('band.on("tap", lambda e: band.storage.put(5000, "x"))\n'
                                'band.transport.tap("tap")\nband.wait(0.3)\n')
        self.assertEqual(done.stderr.count("Problem ("), 2, done.stderr)
        self.assertEqual(done.stderr.count('in your "tap" handler'), 2)
        self.assertNotIn("Traceback", done.stderr)
        self.assertEqual((done.returncode, done.stdout), (0, self.GOES_ON))

    def test_a_bug_in_a_handler_keeps_its_traceback_and_the_program_goes_on(self):
        done = self.run_program('def on_tap(e):\n    x = 1 / 0\nband.on("tap", on_tap)\n')
        self.assertIn('Error in your "tap" handler:', done.stderr)
        self.assertIn("Traceback (most recent call last):", done.stderr)
        self.assertIn("x = 1 / 0", done.stderr)
        self.assertIn("ZeroDivisionError", done.stderr)
        self.assertNotIn("Problem (", done.stderr)
        self.assertEqual((done.returncode, done.stdout), (0, self.GOES_ON))

    def test_the_full_traceback_of_a_band_problem_in_a_handler_can_be_asked_for(self):
        done = self.run_program('band.on("tap", lambda e: band.set_led("#ff0000"))\n', env={"VEXO_TRACEBACK": "1"})
        self.assertIn('Error in your "tap" handler:', done.stderr)
        self.assertIn("Traceback (most recent call last):", done.stderr)
        self.assertIn("VexoError", done.stderr)
        self.assertNotIn("Problem (", done.stderr)
        self.assertEqual((done.returncode, done.stdout), (0, self.GOES_ON))

    def test_a_caught_problem_in_a_handler_is_the_handlers_own_business(self):
        done = self.run_program('def on_tap(e):\n    try:\n        band.set_led("#ff0000")\n'
                                '    except VexoError as error:\n        print("caught", error.code)\n'
                                'band.on("tap", on_tap)\n')
        self.assertEqual((done.returncode, done.stderr, done.stdout), (0, "", "caught denied\n" + self.GOES_ON))

    def test_an_async_handler_that_calls_the_band_without_await(self):
        # An `async def` handler runs on the band's own thread: a plain call there cannot wait,
        # so its error arrives later. It is shown as the sentence too.
        done = self.run_program('async def on_tap(e):\n    band.set_led("#ff0000")\nband.on("tap", on_tap)\n')
        self.assertRegex(done.stderr, r"^Problem \(denied\): .*microphone.*\n$")
        self.assertNotIn("Traceback", done.stderr)
        self.assertEqual((done.returncode, done.stdout), (0, self.GOES_ON))


if __name__ == "__main__":
    unittest.main()
