"""The blocking wrapper, vexo_open.sync.Band."""
import atexit
import contextlib
import inspect
import io
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from vexo_open import Band as AsyncBand
from vexo_open import SimTransport, VexoError
from vexo_open.band import Storage as AsyncStorage
from vexo_open.sync import Band, Storage


def wait_until(check, seconds=3.0):
    end = time.time() + seconds
    while not check() and time.time() < end:
        time.sleep(0.01)
    return check()


class SyncTest(unittest.TestCase):
    def test_has_every_public_method(self):
        for async_class, sync_class in ((AsyncBand, Band), (AsyncStorage, Storage)):
            for name, fn in inspect.getmembers(async_class, inspect.isfunction):
                if not name.startswith("_"):
                    self.assertTrue(callable(getattr(sync_class, name, None)), name)
                    self.assertFalse(inspect.iscoroutinefunction(getattr(sync_class, name)), name)
        for name in ("number", "name", "info", "connected", "reconnecting", "streams", "transport_kind"):
            self.assertIsInstance(getattr(Band, name), property)
        self.assertEqual(Band.set_led.__doc__, AsyncBand.set_led.__doc__)

    def test_methods_work(self):
        band = Band.connect(42, transport=SimTransport(42, realtime=False, wipe_ms=0, firmware="1.0"))
        try:
            self.assertEqual((band.number, band.name, band.connected, band.transport_kind),
                             (42, "Vexo-HACK-42", True, "sim"))
            self.assertEqual(band.info.firmware, "1.0.0")
            self.assertIsNone(band.set_led("#ff8800", brightness=0.5))
            self.assertEqual(band.get_led().r, 255)
            self.assertEqual(band.get_name(), "Vexo-HACK-42")
            self.assertEqual(band.configure(battery=True)["battery"], True)
            self.assertTrue(band.streams["battery"])
            self.assertEqual(band.set_motion(rate=50).rate, 50)
            self.assertEqual(band.command("STREAM_GET").active, 16)
            band.storage.put(7, "hello")
            self.assertEqual(band.storage.get_text(7), "hello")
            self.assertEqual(band.storage.get(7), b"hello")
            self.assertIsNone(band.storage.get(8))
            self.assertEqual(band.storage.info().used_keys, 1)
            band.storage.delete(7)
            band.storage.wipe()
            with self.assertRaises(VexoError) as caught:
                band.set_led("nope")
            self.assertEqual(caught.exception.code, "bad-colour")
            band.configure(audio=True)
            with self.assertRaises(VexoError) as caught:
                band.identify()
            self.assertEqual(caught.exception.code, "denied")
        finally:
            band.disconnect()
        band.disconnect()  # twice is fine
        self.assertFalse(band.connected)
        with self.assertRaises(VexoError) as caught:
            band.get_led()
        self.assertEqual(caught.exception.code, "not-connected")

    def test_connect_errors_come_through(self):
        before = threading.active_count()
        with self.assertRaises(VexoError) as caught:
            Band.connect(42, transport=SimTransport(17, realtime=False))
        self.assertEqual(caught.exception.code, "wrong-band")
        with self.assertRaises(VexoError) as caught:
            Band.connect(42, transport="telepathy")
        self.assertEqual(caught.exception.code, "bad-transport")
        self.assertTrue(wait_until(lambda: threading.active_count() <= before))  # no thread left behind

    def test_handler_gets_events_from_realtime_sim(self):
        band = Band.connect(42, transport="sim")
        samples, threads = [], set()

        @band.on("motion")
        def on_motion(m):
            samples.append(m)
            threads.add(threading.current_thread())

        band.configure(motion=100)
        self.assertTrue(band.wait(0.5))  # still connected afterwards
        self.assertGreater(len(samples), 10)
        self.assertTrue(0.8 < samples[0].az < 1.2)
        self.assertNotIn(threading.current_thread(), threads)  # handlers run on the background thread
        band.off("motion", on_motion)
        time.sleep(0.05)
        count = len(samples)
        band.wait(0.2)
        self.assertEqual(len(samples), count)
        band.disconnect()

    def test_handler_may_call_band_methods(self):
        sim = SimTransport(42)
        band = Band.connect(42, transport=sim)
        results, threads = [], set()

        def on_tap(e):
            threads.add(threading.current_thread())
            results.append(band.set_led("#00ff00"))        # must not freeze
            results.append(band.get_led().g)               # an answer, inside a handler
            results.append(band.storage.put(1, "tapped"))
            results.append(band.storage.get_text(1))
            results.append(band.get_name())
            started = time.time()
            results.append(band.wait(0.2))                 # waits here too, and the data keeps coming
            results.append(time.time() - started >= 0.19)

        band.on("tap", on_tap)
        band.configure(events=True)
        band._runner.loop.call_soon_threadsafe(sim.tap, "tap")
        self.assertTrue(wait_until(lambda: len(results) == 7))
        self.assertEqual(results, [None, 255, None, "tapped", "Vexo-HACK-42", True, True])
        self.assertNotIn(band._runner.thread, threads)     # not on the band's own thread
        self.assertNotIn(threading.current_thread(), threads)
        self.assertEqual(sim.user_led["g"], 255)
        self.assertEqual(band.get_led().g, 255)            # the main thread still gets answers
        self.assertEqual(band.storage.get_text(1), "tapped")

        # An error in a call made from a handler is printed, not lost and not fatal.
        band.off("tap", on_tap)
        band.on("tap", lambda e: band.set_led("not a colour"))
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            band._runner.loop.call_soon_threadsafe(sim.tap, "tap")
            self.assertTrue(wait_until(lambda: "bad-colour" in errors.getvalue() or "colour" in errors.getvalue()))
        self.assertIn("Problem (bad-colour): ", errors.getvalue())     # a band problem: one sentence
        self.assertIn('in your "tap" handler)', errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())
        self.assertEqual(band.get_name(), "Vexo-HACK-42")
        band.disconnect()

    def test_handler_gets_an_answer_while_the_main_program_waits(self):
        # The case a student writes: the main program sits in wait(), the handler asks the band.
        sim = SimTransport(42)
        band = Band.connect(42, transport=sim)
        band.set_led("#0000ff")
        seen = []
        band.on("tap", lambda e: seen.append(band.get_led().b))
        band.on("tap", lambda e: seen.append(band.get_battery().percent))
        band.configure(events=True)
        band._runner.loop.call_soon_threadsafe(sim.tap, "tap")
        started = time.time()
        self.assertTrue(band.wait(0.5))
        self.assertLess(time.time() - started, 2)          # nothing froze
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0], 255)
        self.assertIsInstance(seen[1], int)
        band.disconnect()

    def test_handlers_run_one_at_a_time_in_order(self):
        sim = SimTransport(42)
        band = Band.connect(42, transport=sim)
        order, busy = [], []

        def slow(e):
            busy.append(1)
            order.append((e.kind, len(busy)))
            time.sleep(0.05)
            band.get_name()
            busy.pop()

        band.on("event", slow)
        band.configure(events=True)
        for kind in ("tap", "double-tap", "tap", "free-fall"):
            band._runner.loop.call_soon_threadsafe(sim.tap, kind)
        self.assertTrue(band.wait(0.1))                    # wait() returns after the handlers of what arrived
        self.assertEqual(order, [("tap", 1), ("double-tap", 1), ("tap", 1), ("free-fall", 1)])
        band.disconnect()

    def test_off_drops_events_still_waiting_in_line(self):
        sim = SimTransport(42)
        band = Band.connect(42, transport=sim)
        seen = []

        def once(e):
            seen.append(e.kind)
            band.off("event", once)
            time.sleep(0.05)

        band.on("event", once)
        band.configure(events=True)
        for _ in range(3):
            band._runner.loop.call_soon_threadsafe(sim.tap, "tap")
        band.wait(0.3)
        self.assertEqual(seen, ["tap"])
        band.disconnect()

    def test_disconnect_runs_the_last_handlers_first(self):
        band = Band.connect(42, transport=SimTransport(42))
        seen = []
        band.on("disconnect", lambda e: (time.sleep(0.05), seen.append(e.reason)))
        band.disconnect()
        self.assertEqual(seen, ["closed"])
        self.assertFalse(band._handlers._thread.is_alive())

    def test_transport_methods_are_safe_from_the_main_program_and_from_a_handler(self):
        # band.transport.drop_link() is what examples/python/reconnect.py does in a tap handler
        sim = SimTransport(42)
        band = Band.connect(42, transport=sim, auto_reconnect=True, reconnect_seconds=5)
        band._band._reconnect_pause = 0.02
        events = []
        band.on("reconnecting", lambda e: events.append("reconnecting"))
        band.on("reconnected", lambda e: events.append("reconnected"))
        band.on("tap", lambda e: band.transport.drop_link())
        band.configure(events=True)
        self.assertEqual(repr(band.transport), repr(sim))
        self.assertEqual(band.transport.number, sim.number)       # plain values go straight through
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertTrue(band.transport.tap("tap"))             # from the main program
            self.assertTrue(wait_until(lambda: events == ["reconnecting", "reconnected"]))
            band.transport.drop_link()                              # and directly
            self.assertTrue(wait_until(lambda: events == ["reconnecting", "reconnected"] * 2))
        self.assertEqual(errors.getvalue(), "")
        band.transport.reachable = False                            # setting a value reaches the real transport
        self.assertFalse(sim.reachable)
        band.transport.reachable = True
        band.disconnect()

    def test_async_handler_still_works(self):
        sim = SimTransport(42)
        band = Band.connect(42, transport=sim)
        seen = []

        async def on_tap(e):
            seen.append(threading.current_thread() is band._runner.thread)

        band.on("tap", on_tap)
        band.configure(events=True)
        band._runner.loop.call_soon_threadsafe(sim.tap, "tap")
        self.assertTrue(wait_until(lambda: seen == [True]))
        band.disconnect()

    def test_handler_may_disconnect(self):
        sim = SimTransport(42)
        band = Band.connect(42, transport=sim)
        band.on("tap", lambda e: band.disconnect())
        band.configure(events=True)
        band._runner.loop.call_soon_threadsafe(sim.tap, "tap")
        started = time.time()
        self.assertFalse(band.wait(5))
        self.assertLess(time.time() - started, 3)
        self.assertTrue(wait_until(lambda: not band._runner.running))
        band.disconnect()

    def test_wait_returns_when_the_band_disconnects(self):
        sim = SimTransport(42)
        band = Band.connect(42, transport=sim)
        reasons = []
        band.on("disconnect", lambda e: reasons.append(e.reason))
        started = time.time()
        self.assertTrue(band.wait(0.2))
        self.assertGreaterEqual(time.time() - started, 0.19)
        band.restart()  # the band goes away 200 ms later
        started = time.time()
        self.assertFalse(band.wait())  # no number: would wait forever if the band stayed
        self.assertLess(time.time() - started, 3)
        self.assertEqual(reasons, ["restart"])
        self.assertFalse(band.connected)
        self.assertFalse(band.wait(5))  # already gone: returns at once
        with self.assertRaises(VexoError) as caught:
            band.get_led()
        self.assertEqual(caught.exception.code, "not-connected")
        band.disconnect()

    def test_wait_keeps_waiting_through_a_reconnect(self):
        sim = SimTransport(42)
        band = Band.connect(42, transport=sim, auto_reconnect=True, reconnect_seconds=5)
        self.assertEqual((band._band.auto_reconnect, band._band.reconnect_seconds), (True, 5))  # passed on
        self.assertFalse(band.reconnecting)
        band._band._reconnect_pause = 0.02
        on_loop = band._runner.loop.call_soon_threadsafe
        events, samples = [], []
        for name in ("reconnecting", "reconnected", "disconnect"):
            band.on(name, lambda e, name=name: events.append((name, e)))
        band.on("motion", samples.append)
        band.set_led("#00ff00")
        band.configure(motion=100)

        def drop():
            sim.reachable = False
            sim.drop_link()
        on_loop(drop)
        threading.Timer(0.4, lambda: on_loop(setattr, sim, "reachable", True)).start()  # back after 0.4 s
        started = time.time()
        self.assertFalse(band.wait(0.2))  # waits the whole time: a dropped link does not end wait()
        self.assertGreaterEqual(time.time() - started, 0.19)
        self.assertEqual(events, [("reconnecting", dict(reason="lost", seconds=5))])
        self.assertTrue(band.reconnecting)
        self.assertFalse(band.connected)
        self.assertEqual(repr(band), "<Band Vexo-HACK-42 reconnecting>")
        with self.assertRaises(VexoError) as caught:
            band.get_led()
        self.assertEqual(caught.exception.code, "not-connected")
        self.assertIn("reconnecting", str(caught.exception))
        self.assertTrue(band.wait(1.0))  # ...and afterwards the band is connected again
        self.assertGreaterEqual(time.time() - started, 1.19)
        self.assertEqual([name for name, _ in events], ["reconnecting", "reconnected"])
        self.assertFalse(band.reconnecting)
        self.assertEqual(band.get_led().g, 255)  # the light was set again
        self.assertEqual(band.streams["motion"], 100)
        del samples[:]
        band.wait(0.2)
        self.assertGreater(len(samples), 5)  # and the data flows again
        band.disconnect()
        self.assertEqual(events[2:], [("disconnect", dict(reason="closed"))])

    def test_wait_ends_when_reconnecting_gives_up(self):
        sim = SimTransport(42)
        band = Band.connect(42, transport=sim, auto_reconnect=True, reconnect_seconds=0.3)
        band._band._reconnect_pause = 0.02
        reasons = []
        band.on("disconnect", reasons.append)

        def drop():
            sim.reachable = False
            sim.drop_link()
        band._runner.loop.call_soon_threadsafe(drop)
        started = time.time()
        self.assertFalse(band.wait())  # no number: ends only when the band is gone for good
        self.assertTrue(0.25 <= time.time() - started < 2)
        self.assertEqual(reasons, [dict(reason="lost", reconnect="gave-up")])
        self.assertFalse(band.reconnecting)
        band.disconnect()

    def test_disconnect_stops_a_reconnect(self):
        for how in ("disconnect", "atexit", "with"):
            sim = SimTransport(42)
            # Only this band's own exit handler is run below. atexit._run_exitfuncs() would
            # run the handlers of everything in the process, also of bands other tests left.
            at_exit = []
            with mock.patch.object(atexit, "register", at_exit.append):
                band = Band.connect(42, transport=sim, auto_reconnect=True, reconnect_seconds=60)
            self.assertEqual(len(at_exit), 1)
            band._band._reconnect_pause = 0.02
            events = []
            for name in ("reconnecting", "reconnected", "disconnect"):
                band.on(name, lambda e, name=name: events.append((name, dict(e))))

            def drop():
                sim.reachable = False
                sim.drop_link()
            band._runner.loop.call_soon_threadsafe(drop)
            self.assertTrue(wait_until(lambda: band.reconnecting))
            thread, task = band._runner.thread, band._band._reconnect_task
            if how == "disconnect":
                band.disconnect()
            elif how == "atexit":
                at_exit[0]()  # what Python does when the program ends
            else:
                with band:
                    pass
            self.assertEqual(events, [("reconnecting", dict(reason="lost", seconds=60)),
                                      ("disconnect", dict(reason="closed"))], how)
            self.assertFalse(band.reconnecting or band.connected)
            self.assertTrue(task.done())
            self.assertFalse(thread.is_alive())
            self.assertFalse(band.wait(5))  # returns at once
            band.disconnect()

    def test_disconnect_takes_its_exit_handler_away(self):
        added, removed = [], []
        with mock.patch.object(atexit, "register", added.append), \
                mock.patch.object(atexit, "unregister", removed.append):
            band = Band.connect(42, transport=SimTransport(42, realtime=False))
            self.assertEqual((len(added), removed), (1, []))
            band.disconnect()
            self.assertEqual(removed, added)
            added[0]()  # running it after all (the program ends) is harmless
        self.assertFalse(band.connected)

    def test_ctrl_c_ends_wait_quietly(self):
        band = Band.connect(42, transport=SimTransport(42, realtime=False))
        real_wait = band._gone.wait

        def interrupted(timeout=None):
            raise KeyboardInterrupt()
        band._gone.wait = interrupted
        self.assertTrue(band.wait())  # no traceback, just returns
        band._gone.wait = real_wait
        band.disconnect()

    def test_context_manager(self):
        sim = SimTransport(42, realtime=False)
        with Band.connect(42, transport=sim) as band:
            band.set_led("#0000ff")
            self.assertTrue(sim.is_open)
            thread = band._runner.thread
        self.assertFalse(sim.is_open)
        self.assertFalse(band.connected)
        self.assertFalse(thread.is_alive())

    def test_repr(self):
        with Band.connect(42, transport=SimTransport(42, realtime=False)) as band:
            self.assertEqual(repr(band), "<Band Vexo-HACK-42 connected>")


PYTHON = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROGRAM = """import sys
sys.path.insert(0, %r)
from vexo_open.sync import Band, VexoError
band = Band.connect(42, transport="sim")
band.on("disconnect", lambda e: print("Disconnected:", e.reason))
print("Connected to", band.name)
""" % PYTHON


class ErrorsThatEndTheProgram(unittest.TestCase):
    """A student's own program (not one of the kit's examples) that does not catch an error."""

    def run_program(self, last_lines, env=None):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "app.py"), "w", encoding="utf-8") as f:
                f.write(PROGRAM + last_lines)
            return subprocess.run([sys.executable, "app.py"], cwd=folder, timeout=60, env=dict(os.environ, **(env or {})),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

    def test_a_band_problem_is_one_sentence_and_the_band_is_disconnected(self):
        # the LED while the microphone is on: the band refuses, nobody catches it
        done = self.run_program('band.configure(mic_level=True)\nband.set_led("#ff0000")\nprint("not reached")\n')
        self.assertEqual(done.returncode, 1)
        lines = done.stderr.splitlines()
        self.assertRegex(lines[0], r"^Problem \(denied\): .*microphone")
        self.assertEqual(lines[1:], ["  (line 8 of app.py)"])
        self.assertNotIn("Traceback", done.stderr)
        self.assertEqual(done.stdout, "Connected to Vexo-HACK-42\nDisconnected: closed\n")

    def test_a_problem_before_any_band_is_connected(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "app.py"), "w", encoding="utf-8") as f:
                f.write("import sys\nsys.path.insert(0, %r)\nfrom vexo_open.sync import Band\n"
                        "band = Band.connect(42, transport='carrier-pigeon')\n" % PYTHON)
            done = subprocess.run([sys.executable, "app.py"], cwd=folder, timeout=60,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(done.returncode, 1)
        self.assertRegex(done.stderr, r"^Problem \(bad-transport\): Unknown transport 'carrier-pigeon'.*\n  \(line 4 of app\.py\)\n$")

    def test_a_bug_in_the_program_keeps_its_traceback_and_the_band_is_still_disconnected(self):
        done = self.run_program("x = 1 / 0\n")
        self.assertEqual(done.returncode, 1)
        self.assertIn("Traceback (most recent call last):", done.stderr)
        self.assertIn("x = 1 / 0", done.stderr)
        self.assertIn("ZeroDivisionError", done.stderr)
        self.assertNotIn("Problem (", done.stderr)
        self.assertEqual(done.stdout, "Connected to Vexo-HACK-42\nDisconnected: closed\n")

    def test_the_full_traceback_of_a_band_problem_can_be_asked_for(self):
        done = self.run_program('band.storage.put(5000, "x")\n', env={"VEXO_TRACEBACK": "1"})
        self.assertEqual(done.returncode, 1)
        self.assertIn("Traceback (most recent call last):", done.stderr)
        self.assertIn("VexoError", done.stderr)

    def test_a_caught_problem_is_the_programs_own_business(self):
        done = self.run_program('try:\n    band.storage.put(5000, "x")\nexcept VexoError as e:\n    print("caught", e.code)\n'
                                'band.disconnect()\n')
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertIn("caught bad-", done.stdout)

    def test_the_hook_is_installed_once_and_hands_other_errors_on(self):
        from vexo_open.sync import short_errors
        before = sys.excepthook
        seen = []
        sys.excepthook = lambda *args: seen.append(args[0])
        try:
            short_errors()
            ours = sys.excepthook
            short_errors()
            self.assertIs(sys.excepthook, ours)                      # not wrapped a second time
            ours(ValueError, ValueError("mine"), None)
            self.assertEqual(seen, [ValueError])
            said = io.StringIO()
            with contextlib.redirect_stderr(said):
                ours(VexoError, VexoError("timeout", "No answer."), None)
            self.assertEqual(said.getvalue(), "Problem (timeout): No answer.\n")
            self.assertEqual(seen, [ValueError])
        finally:
            sys.excepthook = before


if __name__ == "__main__":
    unittest.main()
