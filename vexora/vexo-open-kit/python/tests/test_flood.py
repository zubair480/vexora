"""A band that sends as fast as it can while the handler is slow must not use up the
computer: the line of waiting events has a cap, the oldest data events are dropped and
counted, commands are still answered, and the program stops when it says it will."""
import asyncio
import contextlib
import io
import os
import signal
import subprocess
import sys
import tempfile
import time
import tracemalloc
import unittest

from vexo_open import Band as AsyncBand
from vexo_open import band as band_module
from vexo_open import codec, sync
from vexo_open.sync import Band
from vexo_open.transports.sim import SimTransport

PYTHON = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BATTERY = codec.encode_stream("battery", dict(percent=50, state=0, millivolts=3900))
NOTICE = codec.encode_notice("LOW_BATTERY", 14)


class Flood(SimTransport):
    """A pretend band that also sends battery frames as fast as the computer can take them."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sent = 0
        self.most_waiting = 0
        self.handlers = None            # the wrapper's line of waiting events, to measure it

    async def open(self):
        await super().open()
        asyncio.ensure_future(self._flood())

    async def _flood(self):
        while self.is_open:
            for _ in range(100):
                self.on_frame(BATTERY)
            self.sent += 100
            if self.handlers is not None:
                self.most_waiting = max(self.most_waiting, self.handlers._events, len(self.handlers._line))
            await asyncio.sleep(0)


class SlowHandlerUnderFlood(unittest.TestCase):
    def test_the_line_stays_under_its_cap_and_everything_else_keeps_working(self):
        self.assertEqual(sync.MAX_WAITING, 5000)
        sim = Flood(42, auto_tap=False)
        said = io.StringIO()
        tracemalloc.start()
        try:
            with contextlib.redirect_stderr(said):
                band = Band.connect(42, transport=sim)
                sim.handlers = band._handlers
                seen, ends = [], []

                def slow(event):
                    seen.append(event)
                    time.sleep(0.01)
                before = sim.sent                    # frames that came before there was a handler
                band.on("battery", slow)
                band.on("disconnect", ends.append)

                started = time.monotonic()
                self.assertTrue(band.wait(1.5))
                waited = time.monotonic() - started
                self.assertTrue(1.5 <= waited < 2.0, "wait(1.5) took %.2f s" % waited)   # on the computer's clock

                # a command sent during the flood still gets its answer
                band.set_led("#00ff00")
                self.assertEqual(band.get_led().g, 255)
                self.assertEqual(band.get_name(), "Vexo-HACK-42")

                band.wait(1.0)
                sent = sim.sent
                dropped = band.dropped_events
                self.assertGreater(sent, 20000, "the flood was a flood")
                self.assertLessEqual(sim.most_waiting, sync.MAX_WAITING + 2)       # + a marker of wait() or two
                self.assertGreater(dropped, 0)
                self.assertGreaterEqual(dropped, sent - before - sync.MAX_WAITING - len(seen) - 200)   # all but what waits or ran
                _, peak = tracemalloc.get_traced_memory()
                self.assertLess(peak, 40e6, "memory stays bounded: %.1f MB" % (peak / 1e6))

                # the program's end: on time, and "disconnect" is not one of the dropped events
                started = time.monotonic()
                band.disconnect()
                self.assertLess(time.monotonic() - started, 1.0)
                self.assertEqual([e.reason for e in ends], ["closed"])
                self.assertFalse(band._handlers._thread.is_alive())
                handled = len(seen)
                time.sleep(0.1)
                self.assertEqual(len(seen), handled, "no handler runs after disconnect() returned")
        finally:
            tracemalloc.stop()
        self.assertEqual(said.getvalue().count("events are waiting"), 1, said.getvalue())      # one line, not one per frame
        self.assertEqual(len(said.getvalue().splitlines()), 1, said.getvalue())
        self.assertIn("band.dropped_events", said.getvalue())

    def test_the_oldest_data_goes_first_notices_last_and_link_events_never(self):
        runner = sync._Runner()
        try:
            line = sync._Handlers(runner)
            put = lambda event, n: line._put((None, event, None, n))
            put("disconnect", 0)
            put("notice", 0)
            for n in range(sync.MAX_WAITING - 2):
                put("motion", n)
            self.assertEqual((line._events, line.dropped), (sync.MAX_WAITING, 0))
            with contextlib.redirect_stderr(io.StringIO()) as said:
                put("motion", "new")
                put("audio", "new")
            self.assertEqual((line._events, line.dropped), (sync.MAX_WAITING, 2))
            self.assertEqual([(i[1], i[3]) for i in list(line._line)[:3]], [("disconnect", 0), ("notice", 0), ("motion", 2)])
            self.assertEqual([(i[1], i[3]) for i in list(line._line)[-2:]], [("motion", "new"), ("audio", "new")])
            self.assertEqual(len(said.getvalue().splitlines()), 1)
            # Only notices and link events left: the notice goes, the link events stay.
            line._line.clear()
            line._events = 0
            for n in range(sync.MAX_WAITING - 1):
                put("reconnecting", n)
            put("notice", "old")
            put("notice", "new")
            self.assertEqual([i[3] for i in line._line if i[1] == "notice"], ["new"])
            self.assertEqual(line._events, sync.MAX_WAITING)
        finally:
            runner.stop()


PROGRAM = """import asyncio, sys, time
sys.path.insert(0, %r)
from vexo_open import codec
from vexo_open.sync import Band
from vexo_open.transports.sim import SimTransport

FRAME = codec.encode_stream("battery", dict(percent=50, state=0, millivolts=3900))

class Flood(SimTransport):
    async def open(self):
        await super().open()
        asyncio.ensure_future(self.flood())
    async def flood(self):
        while self.is_open:
            for _ in range(100):
                self.on_frame(FRAME)
            await asyncio.sleep(0)

band = Band.connect(42, transport=Flood(42, auto_tap=False))
band.on("battery", lambda e: time.sleep(0.01))
print("flooding", flush=True)
started = time.monotonic()
band.wait(SECONDS)
print("waited %.1f" % (time.monotonic() - started), flush=True)
print("dropped", band.dropped_events > 0, flush=True)
band.disconnect()
print("end", flush=True)
""".replace("%r", repr(PYTHON))


class AProgramUnderFlood(unittest.TestCase):
    def start(self, folder, seconds):
        with open(os.path.join(folder, "app.py"), "w", encoding="utf-8") as f:
            f.write(PROGRAM.replace("SECONDS", seconds))
        return subprocess.Popen([sys.executable, "app.py"], cwd=folder, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True)

    def test_wait_with_seconds_ends_the_program_on_time(self):
        with tempfile.TemporaryDirectory() as folder:
            program = self.start(folder, "2")
            self.assertEqual(program.stdout.readline(), "flooding\n")
            started = time.monotonic()
            out, err = program.communicate(timeout=30)
            took = time.monotonic() - started
        self.assertEqual((program.returncode, out), (0, "waited 2.0\ndropped True\nend\n"), err)
        self.assertLess(took, 4.0)
        self.assertEqual((err.count("events are waiting"), len(err.splitlines())), (1, 1), err)

    @unittest.skipIf(sys.platform == "win32", "Ctrl+C cannot be sent to another program on Windows this way")
    def test_ctrl_c_ends_the_program_promptly_with_no_traceback(self):
        with tempfile.TemporaryDirectory() as folder:
            program = self.start(folder, "")             # wait(): until Ctrl+C
            self.assertEqual(program.stdout.readline(), "flooding\n")
            time.sleep(1.5)
            started = time.monotonic()
            program.send_signal(signal.SIGINT)
            try:
                out, err = program.communicate(timeout=30)
            finally:
                program.kill()
            took = time.monotonic() - started
        self.assertLess(took, 2.0, "Ctrl+C took %.1f s" % took)
        self.assertEqual(program.returncode, 0, err)
        self.assertRegex(out, r"^waited 1\.\d\ndropped True\nend\n$")
        self.assertNotIn("Traceback", err)
        self.assertLessEqual(len(err.splitlines()), 1, err)


class AsyncHandlersUnderFlood(unittest.TestCase):
    def test_slow_async_handlers_are_capped_counted_and_link_events_still_arrive(self):
        async def main():
            sim = SimTransport(42, realtime=False, auto_tap=False)
            band = await AsyncBand.connect(42, transport=sim)
            release = asyncio.Event()
            started, plain, ends = [], [], []

            async def slow(event):
                started.append(event)
                await release.wait()

            async def on_end(event):
                ends.append(event.reason)
            band.on("battery", slow)
            band.on("battery", plain.append)             # a plain handler runs at once: never dropped
            band.on("disconnect", on_end)
            most = 0
            for burst in range(120):
                for _ in range(100):
                    sim.on_frame(BATTERY)
                most = max(most, band._busy, len(asyncio.all_tasks()))
                await asyncio.sleep(0)
            cap = band_module.MAX_WAITING
            self.assertEqual(cap, 5000)
            self.assertLessEqual(most, cap + 5)
            self.assertEqual((len(started), len(plain), band.dropped_events), (cap, 12000, 12000 - cap))
            await band.set_led("#00ff00")                # a command during the flood is answered
            self.assertEqual((await band.get_led()).g, 255)
            await band.disconnect()
            await asyncio.sleep(0)
            self.assertEqual(ends, ["closed"])           # given although the cap is reached
            release.set()
            await asyncio.sleep(0.05)
            self.assertEqual(band._busy, 0)
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            asyncio.run(main())
        self.assertEqual(len(said.getvalue().splitlines()), 1, said.getvalue())
        self.assertIn("band.dropped_events", said.getvalue())


if __name__ == "__main__":
    unittest.main()
