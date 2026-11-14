"""A second program on the band's other link takes a stream (contract 2.3: a stream goes
to the transport that last started it), and a band that is already connected elsewhere.
The library must notice the silence (stream_watch.py), say so with the event
"stream-taken", and get the stream back with take_back(). Everything runs against the
pretend band: not against two programs on a real band.
"""
import asyncio
import contextlib
import io
import time
import types
import unittest
from unittest import mock

from vexo_open import Band, SimTransport, VexoError
from vexo_open.sync import Band as PlainBand
from vexo_open.transports.ble import BleTransport

FAST = dict(tick=0.02, silence=0.15, quiet=0.3)  # the watch, 20 times faster


def run(coro):
    return asyncio.run(coro)


async def connect(**options):
    sim = SimTransport(42, auto_tap=False, **options)
    band = await Band.connect(42, transport=sim)
    band._stream_watch.times.update(FAST)
    taken = []
    band.on("stream-taken", taken.append)
    return band, sim, taken


async def until(check, seconds=3.0):
    end = time.time() + seconds
    while not check() and time.time() < end:
        await asyncio.sleep(0.01)
    return check()


class SecondProgram(unittest.TestCase):
    def test_another_program_takes_motion_and_take_back_gets_it_back(self):
        async def go():
            band, sim, taken = await connect()
            seen = dict(motion=0, level=0)
            band.on("motion", lambda m: seen.update(motion=seen["motion"] + 1))
            band.on("mic-level", lambda m: seen.update(level=seen["level"] + 1))
            await band.configure(motion=50, mic_level=True)
            self.assertTrue(await until(lambda: seen["motion"] and seen["level"]))
            await asyncio.sleep(0.4)  # data flows: the watch asks nothing
            self.assertEqual((band._stream_watch.asked, taken), (0, []))

            sim.other_program_starts("motion")  # e.g. motion.py --usb beside a dashboard on Bluetooth
            self.assertTrue(await until(lambda: len(taken) == 1))
            self.assertEqual((taken[0].streams, taken[0].stopped), (["motion"], []))
            self.assertIn("Another program on the cable (USB) took this stream", taken[0].message)
            self.assertIn("share its streams, its settings and its light", taken[0].message)
            self.assertIn("take_back()", taken[0].message)
            self.assertTrue(band.connected)
            self.assertEqual(band.taken_streams, ["motion"])
            self.assertEqual(band.streams["motion"], 50)  # the band still streams it, only not to us
            before = seen["level"]
            seen["motion"] = 0
            await asyncio.sleep(0.3)
            self.assertEqual(seen["motion"], 0)
            self.assertGreater(seen["level"], before)  # the other stream is still ours
            self.assertEqual(len(taken), 1)  # said once, not every few seconds

            await band.take_back()
            self.assertEqual(band.taken_streams, [])
            self.assertTrue(await until(lambda: seen["motion"] > 0))
            await asyncio.sleep(0.4)
            self.assertEqual(len(taken), 1)
            await band.disconnect()
        run(go())

    def test_the_other_program_ended_so_the_band_stopped_the_stream(self):
        async def go():
            band, sim, taken = await connect()
            seen = []
            band.on("motion", seen.append)
            await band.configure(motion=50)
            self.assertTrue(await until(lambda: seen))
            sim.other_program_starts("motion")
            sim.other_program_closes()
            self.assertTrue(await until(lambda: len(taken) == 1))
            self.assertEqual((taken[0].streams, taken[0].stopped), (["motion"], ["motion"]))
            self.assertIn("took this stream and has stopped since", taken[0].message)
            self.assertEqual(band.streams["motion"], 0)
            del seen[:]
            await band.configure(motion=50)  # configure() starts it again too
            self.assertTrue(await until(lambda: seen))
            self.assertEqual(band.taken_streams, [])
            await band.disconnect()
        run(go())

    def test_a_stream_that_is_silent_by_nature_is_found_when_the_band_says_it_is_off(self):
        async def go():
            band, sim, taken = await connect()
            await band.configure(events=True)
            await asyncio.sleep(0.1)
            self.assertEqual(band._stream_watch.asked, 0)
            sim.other_program_starts("events")
            sim.other_program_closes()
            self.assertTrue(await until(lambda: len(taken) == 1))
            self.assertEqual(taken[0].streams, ["events"])
            self.assertFalse(band.streams["events"])
            taps = []
            band.on("tap", taps.append)
            await band.take_back()
            sim.tap("tap")
            self.assertEqual(len(taps), 1)
            await band.disconnect()
        run(go())

    def test_firmware_1_0_motion_is_watched_too(self):
        async def go():
            band, sim, taken = await connect(firmware="1.0")
            await band.configure(motion=100)
            sim.other_program_starts("motion")
            self.assertTrue(await until(lambda: len(taken) == 1))
            self.assertEqual(taken[0].streams, ["motion"])
            await band.disconnect()
        run(go())

    def test_nothing_is_said_about_what_the_program_did_itself(self):
        async def go():
            band, sim, taken = await connect()
            await band.configure(motion=50, activity=True)
            await band.configure(motion=False)
            await asyncio.sleep(0.4)
            self.assertEqual((taken, band._stream_watch.asked), ([], 0))
            await band.disconnect()
            await asyncio.sleep(0.1)
            self.assertEqual(taken, [])
            self.assertIsNone(band._stream_watch._timer)

            band, sim, taken = await connect(via="usb")
            await band.configure(motion=50)
            await band.sleep()  # on the cable the link stays open and the streams are off
            await asyncio.sleep(0.4)
            self.assertEqual(taken, [])
            await band.disconnect()
        run(go())

    def test_on_the_cable_the_other_program_is_on_bluetooth_and_the_sentence_is_printed_once(self):
        async def go():
            sim = SimTransport(42, auto_tap=False, via="usb")
            band = await Band.connect(42, transport=sim)
            band._stream_watch.times.update(FAST)
            printed = io.StringIO()
            with contextlib.redirect_stderr(printed):
                await band.configure(motion=50)
                sim.other_program_starts("motion")
                self.assertTrue(await until(lambda: printed.getvalue()))
                await asyncio.sleep(0.3)
            self.assertEqual(printed.getvalue().count("Another program on Bluetooth took this stream"), 1)
            self.assertTrue(band.connected)
            await band.disconnect()
        run(go())

    def test_the_plain_band_has_the_event_and_take_back(self):
        band = PlainBand.connect(42, transport=SimTransport(42, auto_tap=False))
        try:
            band._band._stream_watch.times.update(FAST)
            taken, motion = [], []
            band.on("stream-taken", taken.append)
            band.on("motion", motion.append)
            band.configure(motion=50)
            band.transport.other_program_starts("motion")
            end = time.time() + 3
            while not taken and time.time() < end:
                band.wait(0.05)
            self.assertEqual([e.streams for e in taken], [["motion"]])
            self.assertEqual(band.taken_streams, ["motion"])
            del motion[:]
            self.assertEqual(band.take_back()["motion"], 50)
            band.wait(0.3)
            self.assertTrue(motion)
        finally:
            band.disconnect()


class ConnectedElsewhere(unittest.TestCase):
    def check(self, error):
        self.assertEqual(error.code, "not-found-nearby")
        text = str(error)
        self.assertTrue(text.startswith("Could not see Vexo-HACK-42."), text)
        self.assertIn("the dashboard tab, another program, a teammate's laptop", text)
        self.assertIn("one Bluetooth connection at a time", text)
        self.assertLess(text.index("something else is connected to it"), text.index("double-tap"))

    def test_the_pretend_band_cannot_be_found_and_the_sentence_names_that_first(self):
        async def go():
            sim = SimTransport(42, realtime=False)
            sim.connected_elsewhere = True
            with self.assertRaises(VexoError) as caught:
                await Band.connect(42, transport=sim)
            self.check(caught.exception)
            sim.connected_elsewhere = False  # the other one was closed
            band = await Band.connect(42, transport=sim)
            self.assertEqual(band.name, "Vexo-HACK-42")
            await band.disconnect()
        run(go())

    def test_real_bluetooth_says_the_same_when_the_scan_finds_nothing(self):
        async def nothing(*_args, **_kwargs):
            return None
        bleak = types.SimpleNamespace(BleakClient=object,
                                      BleakScanner=types.SimpleNamespace(find_device_by_filter=nothing))

        async def go():
            with mock.patch.dict("sys.modules", bleak=bleak):
                with self.assertRaises(VexoError) as caught:
                    await BleTransport(42, scan_seconds=0.01).open()
            self.check(caught.exception)
        run(go())


if __name__ == "__main__":
    unittest.main()
