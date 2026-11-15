"""A band with firmware 1.1.3 ends the USB session of a computer that took no data for 5
seconds, without a notice: streams off, settings at their defaults, commands still
answered. These tests check that the library notices it (transports/session_watch.py),
says so with reason "session-ended", and that auto-reconnect sets everything again.
Everything runs against the pretend band and a fake port: not against a real band.
"""
import asyncio
import json
import os
import queue
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

from vexo_open import Band, SimTransport, Transport, codec
from vexo_open.sync import Band as PlainBand
from vexo_open.transports.ble import BleTransport
from vexo_open.transports.serial import SerialTransport
from vexo_open.transports.session_watch import SESSION_ENDED_TEXT, WATCH_TIMES, fastest_frame_seconds

FAST = dict(tick=0.02, silence=0.08, stall=3.0, settle=0.02, quiet=0.4)  # the watch, 25 times faster
BIT = codec.STREAM_BITS


def run(coro):
    return asyncio.run(coro)


async def connect_usb(times=FAST, sim=None, **options):
    """A pretend band on the cable that the library watches from the start."""
    sim = sim or SimTransport(42, via="usb", auto_tap=False)
    sim.session_watch = True
    band = await Band.connect(42, transport=sim, **options)
    band._reconnect_pause = 0.01
    band._watch.times.update(times)
    events = []
    for name in ("reconnecting", "reconnected", "disconnect"):
        band.on(name, lambda e, name=name: events.append((name, e)))
    return band, sim, events


async def until(check, seconds=5.0):
    end = time.time() + seconds
    while not check() and time.time() < end:
        await asyncio.sleep(0.005)
    return check()


def watch(sim):
    """Record the name of every command the pretend band receives from now on."""
    names, real = [], sim._handle

    def handle(frame):
        names.append(codec.decode_command(frame)["opcode"])
        real(frame)
    sim._handle = handle
    return names


class PretendBandTest(unittest.TestCase):
    def test_end_usb_session_stops_streams_resets_settings_and_says_nothing(self):
        async def main():
            sim = SimTransport(42, realtime=False, via="usb")
            band = await Band.connect(42, transport=sim)
            self.assertFalse(sim.session_watch)  # not watched until the call is used
            heard = []
            for name in ("motion", "notice", "disconnect"):
                band.on(name, lambda e, name=name: heard.append(name))
            await band.set_led("#ff0000")
            await band.set_motion(rate=100)
            await band.configure(motion=True, battery=True)
            sim.step(300)
            self.assertIn("motion", heard)
            del heard[:]
            hints = []
            real = sim.on_session_hint
            sim.on_session_hint = lambda: (hints.append(1), real())
            sim.end_usb_session()
            sim.step(2000)
            self.assertEqual(heard, [])  # no stream frame, no notice, no close
            self.assertEqual(hints, [])
            self.assertTrue(sim.session_watch)
            self.assertTrue(band.connected)
            # Commands are answered again at once; the first frame has an empty packet in front of it.
            self.assertEqual(dict(await band.command("IMU_GET")), dict(rate_hz=25, accel_range_g=2, gyro_range_dps=2000))
            self.assertEqual(hints, [1])
            self.assertEqual((await band.command("LED_GET")).brightness, 0)
            self.assertEqual(hints, [1])
            self.assertIn("battery-drain", sim._timers)  # the band itself goes on
            band._watch.stop()
            await band.disconnect()
        run(main())

    def test_stop_reading_follows_the_5_second_rule(self):
        async def start():
            sim = SimTransport(42, realtime=False, via="usb", auto_tap=False)
            band = await Band.connect(42, transport=sim)
            heard = []
            band.on("activity", lambda e: heard.append("activity"))
            band.on("notice", lambda e: heard.append(e.kind))
            return band, sim, heard

        async def main():
            # 7 seconds with a stream on: the session ends 5.2 seconds after the first frame nobody took.
            band, sim, heard = await start()
            await band.configure(activity=True)
            sim.stop_reading(7)
            sim.step(1000 + 5100)
            self.assertEqual(sim.active, 1 << BIT["activity"])  # not yet
            sim.step(200)
            self.assertEqual(sim.active, 0)  # the band ended the session
            sim.step(3000)
            self.assertEqual(heard, [])  # and said nothing
            band._watch.stop()
            await band.disconnect()
            # 1 second: a pause. Frames are lost, the band says stream-dropped, the session goes on.
            band, sim, heard = await start()
            await band.configure(activity=0.1)
            hints = []
            real = sim.on_session_hint
            sim.on_session_hint = lambda: (hints.append(1), real())
            sim.stop_reading(1)
            sim.step(1050)
            self.assertEqual(sim.active, 1 << BIT["activity"])
            self.assertEqual(hints, [1])  # one empty packet
            self.assertIn(heard.count("activity"), (2, 3))  # the 2 that waited in the first 200 ms and what came after, not all 10
            self.assertEqual([h for h in heard if h != "activity"], ["stream-dropped"])
            band._watch.stop()
            await band.disconnect()
            # No stream on: nothing waits for the computer, so the session is never ended.
            band, sim, heard = await start()
            await band.set_led("#00ff00")
            sim.stop_reading(60)
            sim.step(61000)
            self.assertEqual((await band.command("LED_GET")).g, 255)
            with self.assertRaises(ValueError):
                sim.stop_reading(0)
            await band.disconnect()
        run(main())

    def test_a_store_file_nested_too_deep_is_ignored_like_any_bad_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "store.json")
            with open(path, "w") as f:
                f.write("[" * 100000 + "]" * 100000)
            sim = SimTransport(42, realtime=False, store_file=path)  # must not raise
            self.assertEqual(sim.store, {})
            with open(path, "w") as f:
                json.dump({"7": "6869"}, f)
            self.assertEqual(SimTransport(42, realtime=False, store_file=path).store, {7: b"hi"})


class NoticeTest(unittest.TestCase):
    def test_noticed_within_3_seconds_with_the_real_times_and_says_why(self):
        async def main():
            band, sim, events = await connect_usb(times=WATCH_TIMES)
            self.assertEqual(band._watch.times, dict(tick=1.0, silence=2.0, stall=3.0, settle=0.5, quiet=30.0))
            await band.configure(mic_level=True)
            await asyncio.sleep(0.3)
            seen = watch(sim)
            at = time.time()
            sim.end_usb_session()
            self.assertTrue(await until(lambda: events))
            took = time.time() - at
            self.assertTrue(1.9 <= took <= 3.3, "noticed after %.2f s" % took)
            name, gone = events[0]
            self.assertEqual((name, gone.reason), ("disconnect", "session-ended"))
            self.assertTrue(gone.message.startswith(SESSION_ENDED_TEXT))
            self.assertRegex(gone.message, r"took no data for 5 seconds.*Streams were stopped\. Connect again")
            self.assertEqual(seen, ["STREAM_GET"])  # one question, nothing else
            self.assertFalse(band.connected)
            self.assertFalse(band.reconnecting)
            self.assertFalse(band.streams["mic_level"])
            self.assertFalse(sim.is_open)  # the port was closed properly
            self.assertIsNone(band._watch._timer)
            self.assertEqual(len(events), 1)
        run(main())

    def test_auto_reconnect_sets_everything_again_and_data_flows(self):
        async def main():
            band, sim, events = await connect_usb(auto_reconnect=True)
            await band.set_led("#00ff00", brightness=0.5)
            await band.set_motion(accel_range=8)
            await band.configure(motion=100, mic_level=0.04, events=True)
            motion = []
            band.on("motion", motion.append)
            await asyncio.sleep(0.15)
            self.assertTrue(motion)
            sim.end_usb_session()
            self.assertTrue(await until(lambda: len(events) == 2))
            self.assertEqual([name for name, _ in events], ["reconnecting", "reconnected"])
            why, back = events[0][1], events[1][1]
            self.assertEqual(why.reason, "session-ended")
            self.assertRegex(why.message, r"took no data for 5 seconds.*connecting again")
            self.assertEqual(back.failed, [])
            self.assertEqual(back.attempts, 1)
            self.assertTrue(band.connected)
            self.assertEqual(band.streams["motion"], 100)
            self.assertTrue(band.streams["mic_level"] and band.streams["events"])
            self.assertEqual(sim.imu["accel_range_g"], 8)
            self.assertEqual(sim.intervals[BIT["mic_level"]], 40)
            self.assertEqual(sim.user_led["g"], 255)
            del motion[:]
            await asyncio.sleep(0.2)
            self.assertTrue(motion)  # motion readings arrive again
            self.assertEqual(len(events), 2)  # and the link stays
            self.assertIsNotNone(band._watch._timer)  # the new session is watched too
            await band.disconnect()
        run(main())

    def test_the_programs_own_stream_get_is_enough(self):
        async def main():
            band, sim, events = await connect_usb(times=WATCH_TIMES, sim=SimTransport(42, via="usb", realtime=False))
            await band.configure(activity=True)
            sim.end_usb_session()
            self.assertEqual(dict(await band.command("STREAM_GET")), dict(active=0))
            self.assertTrue(await until(lambda: events))
            self.assertEqual(events[0][1].reason, "session-ended")
        run(main())

    def test_the_blocking_wrapper_gets_the_same_events(self):
        sim = SimTransport(42, via="usb", auto_tap=False)
        sim.session_watch = True
        band = PlainBand.connect(42, transport=sim, auto_reconnect=True)
        try:
            band._band._watch.times.update(FAST)
            events, readings = [], []
            for name in ("reconnecting", "reconnected", "disconnect"):
                band.on(name, lambda e, name=name: events.append((name, e)))
            band.on("activity", readings.append)
            band.configure(activity=0.1)
            band.transport.end_usb_session()
            end = time.time() + 5
            while len(events) < 2 and time.time() < end:
                band.wait(0.05)
            self.assertEqual([name for name, _ in events], ["reconnecting", "reconnected"])
            self.assertEqual(events[0][1].reason, "session-ended")
            self.assertIn("took no data for 5 seconds", events[0][1].message)
            del readings[:]
            band.wait(0.4)
            self.assertTrue(readings)  # data flows again
        finally:
            band.disconnect()
        # Without auto-reconnect: one disconnect, and wait() returns.
        sim = SimTransport(42, via="usb", auto_tap=False)
        sim.session_watch = True
        band = PlainBand.connect(42, transport=sim)
        try:
            band._band._watch.times.update(FAST)
            reasons = []
            band.on("disconnect", lambda e: reasons.append(e.reason))
            band.configure(activity=0.1)
            band.transport.end_usb_session()
            band.wait(5)
            band.wait(0.1)
            self.assertEqual(reasons, ["session-ended"])
            self.assertFalse(band.connected)
        finally:
            band.disconnect()


class NoFalseAlarmTest(unittest.TestCase):
    def test_streams_that_are_silent_by_nature(self):
        async def main():
            band, sim, events = await connect_usb()
            await band.configure(events=True, battery=True)
            self.assertIsNone(fastest_frame_seconds(band, band._restore_mask))
            seen = watch(sim)
            await asyncio.sleep(0.3)  # far longer than the silence time (0.08 s)
            self.assertEqual(seen, [])  # nothing was asked
            await asyncio.sleep(0.25)  # after the long quiet time (0.4 s) the band is asked once ...
            self.assertEqual(seen, ["STREAM_GET"])
            self.assertEqual(events, [])  # ... and all is well
            self.assertTrue(band.connected)
            sim.end_usb_session()  # a real session end is still found by that slow check
            self.assertTrue(await until(lambda: events))
            self.assertEqual(events[0][1].reason, "session-ended")
        run(main())

    def test_a_stream_that_is_on_but_silent_is_asked_about_less_and_less_often(self):
        async def main():
            band, sim, events = await connect_usb()
            await band.configure(activity=0.1)
            del sim._timers["activity"]  # the band stays in its session but sends nothing
            seen = watch(sim)
            await asyncio.sleep(1.5)
            self.assertTrue(2 <= len(seen) <= 5, seen)  # waits of 0.3, 0.4, 0.4 ... s
            self.assertEqual(set(seen), {"STREAM_GET"})
            self.assertEqual(events, [])
            await band.disconnect()
        run(main())

    def test_nothing_is_asked_while_data_flows(self):
        async def main():
            band, sim, events = await connect_usb(times=dict(tick=0.1, silence=0.4, stall=3.0, settle=0.1, quiet=30.0))
            await band.configure(audio=True, motion=50)
            seen = watch(sim)
            await asyncio.sleep(1.5)
            self.assertEqual(seen, [])
            self.assertEqual(band._watch.asked, 0)
            self.assertEqual(events, [])
            await band.disconnect()
        run(main())

    def test_the_200_ms_pause_does_not_end_the_link(self):
        async def main():
            band, sim, events = await connect_usb()
            await band.configure(mic_level=0.02)
            notices = []
            band.on("notice", lambda n: notices.append(n.kind))
            seen = watch(sim)
            sim.usb_pause()
            await asyncio.sleep(0.3)
            self.assertEqual(notices, ["stream-dropped"])
            self.assertEqual(seen, [])  # frames came after the empty packet: nothing was asked
            # With only naturally silent streams on, the empty packet is worth one question. The answer is "still on".
            await band.configure(mic_level=False, events=True)
            del seen[:]
            sim.usb_pause()
            await asyncio.sleep(0.15)
            self.assertEqual(seen, ["STREAM_GET"])
            self.assertEqual(events, [])
            self.assertTrue(band.connected)
            await band.disconnect()
        run(main())

    def test_a_program_that_was_stopped_itself_asks_once_it_runs_again(self):
        async def main():
            band, sim, events = await connect_usb()
            await band.configure(events=True)
            seen = watch(sim)
            band._watch._tick_at -= 5  # as if the timer came 5 seconds late
            await asyncio.sleep(0.15)
            self.assertEqual(seen, ["STREAM_GET"])
            self.assertEqual(events, [])
            await band.configure(mic_level=0.02)
            del seen[:]
            band._watch._tick_at -= 5
            await asyncio.sleep(0.15)
            self.assertEqual(seen, [])  # the stream still sends: no question
            band._watch._tick_at -= 5  # stopped long enough for the band to give up
            sim.end_usb_session()
            self.assertTrue(await until(lambda: events))
            self.assertEqual(events[0][1].reason, "session-ended")
        run(main())

    def test_no_stream_on_nothing_is_watched_and_sleep_is_no_session_end(self):
        async def main():
            band, sim, events = await connect_usb()
            await band.set_led("#0000ff")
            self.assertIsNone(band._watch._timer)
            await band.configure(activity=0.1)
            self.assertIsNotNone(band._watch._timer)
            await band.configure(activity=False)
            self.assertIsNone(band._watch._timer)
            await band.configure(activity=0.1)
            seen = watch(sim)
            await band.sleep()  # over USB the streams stop and the link stays
            await asyncio.sleep(0.6)
            self.assertEqual(seen, ["SLEEP"])
            self.assertEqual(events, [])
            self.assertIsNone(band._watch._timer)
            await band.disconnect()
        run(main())


class BluetoothIsLeftAloneTest(unittest.TestCase):
    def test_a_bluetooth_kind_link_gets_no_timer_and_no_extra_command(self):
        class BleLike(Transport):
            kind = "ble"

            def __init__(self, sim):
                super().__init__()
                self.sim = sim
                sim.on_frame = lambda f: self.on_frame(f)
                sim.on_close = lambda r: self.on_close(r)

            async def open(self):
                await self.sim.open()

            async def close(self):
                await self.sim.close()

            async def send(self, frame):
                await self.sim.send(frame)

        async def main():
            sim = SimTransport(42, auto_tap=False)
            band = await Band.connect(42, transport=BleLike(sim))
            band._watch.times.update(FAST)
            events = []
            band.on("disconnect", events.append)
            await band.configure(mic_level=0.02, activity=0.1)
            seen = watch(sim)
            self.assertIsNone(band._watch._timer)  # no timer
            await asyncio.sleep(0.2)
            sim.end_usb_session()  # the streams go silent (over Bluetooth this never happens this way)
            band._watch.hint()  # even a sign from somewhere changes nothing
            await asyncio.sleep(0.6)
            self.assertIsNone(band._watch._timer)
            self.assertEqual(band._watch.asked, 0)
            self.assertEqual(seen, [])  # not one command was sent
            self.assertEqual(events, [])
            self.assertEqual(dict(await band.command("STREAM_GET")), dict(active=0))  # the program's own question
            await asyncio.sleep(0.05)
            self.assertTrue(band.connected)
            await band.disconnect()
        run(main())
        # The real transports: only the cable is watched.
        self.assertTrue(SerialTransport().session_watch)
        self.assertFalse(getattr(BleTransport(42), "session_watch", False))
        self.assertFalse(hasattr(BleTransport(42), "on_session_hint"))

    def test_the_pretend_band_is_as_before_when_the_new_calls_are_not_used(self):
        async def main():
            sim = SimTransport(42, auto_tap=False)
            band = await Band.connect(42, transport=sim)
            band._watch.times.update(FAST)
            await band.configure(events=True, activity=True)
            seen = watch(sim)
            await asyncio.sleep(0.6)
            self.assertIsNone(band._watch._timer)
            self.assertEqual(seen, [])
            await band.disconnect()
        run(main())


# ---- the serial transport itself, with a pretend pyserial that has the pretend band behind it ----

def fake_serial_with_band(opened):
    """A pretend 'serial' package. Each Serial object is one open of the same cable; the
    bytes go to one SimTransport, which answers like a band. Returns (modules, sim)."""
    sim = SimTransport(42, realtime=False, auto_tap=False)
    lock = threading.Lock()  # the transport writes on one thread and the test steps the band on another

    class SerialException(Exception):
        pass

    class Serial:
        def __init__(self, port=None, baudrate=9600, timeout=None, rtscts=False, dsrdtr=False):
            self.port, self.log, self.is_open, self._dtr = port, [], False, True
            self.incoming = queue.Queue()
            self.band_side = codec.Deframer()

        def open(self):
            self.is_open = True
            self.log.append("open")
            opened.append(self)
            with lock:
                sim._shutdown(None)  # a new open is a new session
                sim.is_open = True
                sim._deaf, sim._new_session = None, False
                sim._every("battery-drain", 60000, sim._drain_battery)
                sim.on_frame = lambda frame: self.incoming.put(codec.frame_for_usb(frame))
                sim.on_session_hint = lambda: self.incoming.put(b"\x00")  # the empty packet, as bytes

        dtr = property(lambda self: self._dtr, lambda self, value: (setattr(self, "_dtr", value), self.log.append(("dtr", value)))[0])
        in_waiting = 0

        def reset_input_buffer(self):
            pass

        def read(self, size=1):
            if not self.is_open:
                raise SerialException("closed")
            try:
                return self.incoming.get(timeout=0.01)
            except queue.Empty:
                return b""

        def write(self, data):
            for frame in self.band_side.push(data):
                with lock:
                    sim._handle(frame)

        def close(self):
            self.log.append("close")
            self.is_open = False
            with lock:
                sim._shutdown(None)

    def step(ms):
        with lock:
            sim.step(ms)

    serial = types.ModuleType("serial")
    serial.Serial, serial.SerialException = Serial, SerialException
    tools = types.ModuleType("serial.tools")
    list_ports = types.ModuleType("serial.tools.list_ports")
    port = types.SimpleNamespace(device="/dev/band", product="Vexo Open band", manufacturer="Vexo Open", vid=None, pid=None)
    list_ports.comports = lambda: [port]
    serial.tools, tools.list_ports = tools, list_ports
    return {"serial": serial, "serial.tools": tools, "serial.tools.list_ports": list_ports}, sim, step


class SerialTransportTest(unittest.TestCase):
    def test_session_end_over_the_serial_transport_reconnects_and_data_flows(self):
        opened = []
        modules, sim, step = fake_serial_with_band(opened)

        async def run_band(seconds):
            end = time.time() + seconds
            while time.time() < end:
                step(20)
                await asyncio.sleep(0.02)

        async def main():
            band = await Band.connect(42, transport="serial", auto_reconnect=True)
            self.assertTrue(band.transport.session_watch)
            band._reconnect_pause = 0.01
            band._watch.times.update(FAST)
            events, readings = [], []
            for name in ("reconnecting", "reconnected", "disconnect"):
                band.on(name, lambda e, name=name: events.append((name, e.get("reason"))))
            band.on("activity", readings.append)
            await band.set_led("#ff8800")
            await band.configure(activity=0.1)
            await run_band(0.4)
            self.assertTrue(readings)
            self.assertEqual(events, [])
            sim.end_usb_session()
            end = time.time() + 5
            while len(events) < 2 and time.time() < end:
                await run_band(0.05)
            self.assertEqual(events, [("reconnecting", "session-ended"), ("reconnected", None)])
            self.assertEqual(len(opened), 2)  # the port was closed and opened again
            self.assertEqual(opened[0].log, [("dtr", True), "open", ("dtr", False), "close"])  # after the open DTR is only written to close
            self.assertEqual(sim.user_led["r"], 255)
            del readings[:]
            await run_band(0.4)
            self.assertTrue(readings)  # data flows again
            self.assertEqual(len(events), 2)
            await band.disconnect()
        with mock.patch.dict(sys.modules, modules):
            run(main())

    def test_an_empty_packet_mid_session_is_reported_and_the_pause_ends_nothing(self):
        opened = []
        modules, sim, step = fake_serial_with_band(opened)

        async def main():
            band = await Band.connect(42, transport="serial")
            band._watch.times.update(FAST)
            hints = []
            real = band.transport.on_session_hint
            band.transport.on_session_hint = lambda: (hints.append(1), real())
            reasons = []
            band.on("disconnect", lambda e: reasons.append(e.reason))
            await band.configure(mic_level=0.02)
            sim.usb_pause()
            for _ in range(15):
                step(20)
                await asyncio.sleep(0.02)
            self.assertEqual(hints, [1])
            self.assertEqual(band._watch.asked, 0)  # frames went on: nothing was asked
            self.assertEqual(reasons, [])
            self.assertTrue(band.connected)
            await band.disconnect()
        with mock.patch.dict(sys.modules, modules):
            run(main())


if __name__ == "__main__":
    unittest.main()
