"""Directing the pretend band (play_motion, set_loudness, set_rssi, ...), "fastest" for the
interval streams, where the pretend band keeps its storage, and what `t` and `time` do
across a reconnect and a restart. All against the pretend band."""
import json
import os
import stat
import tempfile
import time
import unittest
from unittest import mock

from vexo_open import Band, SimTransport, VexoError, codec
from vexo_open.sync import Band as PlainBand
from vexo_open.transports import sim as sim_module

from test_band import run
from test_reconnect import until
from test_v11 import NOW, V11Case, log, names

V11_ONLY = {"TIME_SET", "TIME_GET", "STREAM_SET_INTERVAL", "STREAM_GET_INTERVAL", "MOTION2_SET", "MOTION2_GET"}


async def connect(firmware="1.1", **options):
    sim_options = {k: options.pop(k) for k in ("via", "store_file", "audio_packets") if k in options}
    sim = SimTransport(42, realtime=False, firmware=firmware, **sim_options)
    band = await Band.connect(42, transport=sim, **options)
    band._reconnect_pause = 0.005
    return band, sim


def collect(band, event):
    got = []
    band.on(event, got.append)
    return got


class FastestIntervalTest(V11Case):
    def test_firmware_1_1_takes_the_shortest_interval_the_band_allows(self):
        async def main():
            band, sim = await connect()
            seen = log(sim)
            streams = await band.configure(mic_level="fastest", activity="fastest", battery="fastest",
                                           temperature="fastest", link="fastest")
            self.assertTrue(all(streams[key] for key in ("mic_level", "activity", "battery", "temperature", "link")))
            self.assertEqual(sim.intervals, {2: 100, 4: 1000, 5: 100, 6: 100, 7: 20})
            sets = [(c["args"]["stream"], c["args"]["interval_ms"]) for c in seen
                    if c["opcode"] == "STREAM_SET_INTERVAL"]
            self.assertEqual(sorted(sets), [(2, 100), (4, 1000), (5, 100), (6, 100), (7, 20)])
            self.assertEqual(await band.get_interval("mic_level"), dict(stream="mic_level", seconds=0.02, min=0.02, max=10))
            levels = collect(band, "mic-level")
            sim.step(1000)
            self.assertEqual(len(levels), 50)
        run(main())

    def test_set_interval_fastest_says_what_you_got_and_leaves_the_stream_alone(self):
        async def main():
            band, sim = await connect()
            self.assertEqual(await band.set_interval("mic_level", "fastest"),
                             dict(stream="mic_level", seconds=0.02, min=0.02, max=10))
            self.assertEqual(await band.set_interval("battery", "fastest"),
                             dict(stream="battery", seconds=1, min=1, max=3600))
            self.assertFalse(band.streams["mic_level"] or band.streams["battery"])
        run(main())

    def test_firmware_1_0_falls_back_to_the_fixed_pace(self):
        async def main():
            for options in (dict(firmware="1.0"), dict(protocol="1.0")):
                band, sim = await connect(**options)
                seen = log(sim)
                await band.configure(mic_level="fastest", activity="fastest", battery="fastest", link="fastest")
                self.assertEqual(names(seen), ["STREAM_START"], options)  # plain on, nothing else
                self.assertTrue(band.streams["mic_level"] and band.streams["link"])
                levels = collect(band, "mic-level")
                sim.step(1000)
                self.assertEqual(len(levels), 10)  # the pace of firmware 1.0
                del seen[:]
                self.assertEqual(await band.set_interval("mic_level", "fastest"),
                                 dict(stream="mic_level", seconds=0.1, min=0.1, max=0.1))
                self.assertEqual(await band.set_interval("activity", "fastest"),
                                 dict(stream="activity", seconds=1, min=1, max=1))
                self.assertEqual(await band.set_interval("battery", "fastest"),
                                 dict(stream="battery", seconds=0, min=0, max=0))  # only when it changes
                self.assertEqual(seen, [])  # nothing was sent
                # temperature is the one interval firmware 1.0 has: its shortest is 1 second
                await band.configure(temperature="fastest")
                self.assertEqual(names(seen), ["TEMP_SET_INTERVAL", "STREAM_START"])
                self.assertEqual(sim.temp_seconds, 1)
                self.assertEqual(await band.set_interval("temperature", "fastest"),
                                 dict(stream="temperature", seconds=1, min=1, max=255))
                self.assertFalse(V11_ONLY & set(names(seen)))
                # a number is still refused, clearly, and nothing is sent (rules 13 and 16)
                del seen[:]
                for call in (lambda: band.configure(mic_level=0.02), lambda: band.set_interval("mic_level", 0.02),
                             lambda: band.get_interval("mic_level")):
                    async with self.assertVexoError("not-available") as caught:
                        await call()
                    self.assertIn("firmware 1.1", str(caught.error))
                self.assertEqual(seen, [])
                await band.disconnect()
                async with self.assertVexoError("not-connected"):
                    await band.set_interval("mic_level", "fastest")
        run(main())

    def test_only_the_word_fastest_and_only_for_streams_with_an_interval(self):
        async def main():
            for firmware in ("1.0", "1.1"):
                band, sim = await connect(firmware)
                seen = log(sim)
                for bad in (dict(mic_level="fast"), dict(events="fastest"), dict(audio="fastest"),
                            dict(battery="slowest")):
                    with self.assertRaises(VexoError, msg=bad) as caught:
                        await band.configure(**bad)
                    self.assertEqual(caught.exception.code, "bad-stream", bad)
                async with self.assertVexoError("bad-arg"):
                    await band.set_interval("motion", "fastest")
                self.assertEqual(seen, [])
        run(main())

    def test_a_reconnect_sets_the_fastest_interval_again(self):
        async def main():
            band, sim = await connect(auto_reconnect=True)
            await band.configure(mic_level="fastest")
            sim.drop_link()
            self.assertEqual(sim.intervals[7], 100)  # the band forgot it
            self.assertTrue(await until(lambda: band.connected))
            self.assertEqual((sim.intervals[7], band.streams["mic_level"]), (20, True))
        run(main())


class TimeAcrossLinksTest(V11Case):
    """What `t` (the band's counter) and `time` (real time) do when the link comes back."""

    def test_dropped_link_and_auto_reconnect(self):
        async def main():
            for firmware, clock in (("1.1", "band"), ("1.0", "computer")):
                self.clock.now = NOW
                sim = SimTransport(42, realtime=False, firmware=firmware)
                sim.step(5000)  # the band has been on for 5 seconds
                band = await Band.connect(42, transport=sim, auto_reconnect=True)
                band._reconnect_pause = 0.005
                taps = collect(band, "tap")
                await band.configure(events=True)
                sim.step(1000)
                sim.tap("tap")
                self.assertEqual((taps[0].t, band.clock), (6000, clock), firmware)
                self.assertAlmostEqual(taps[0].time, NOW + 1, places=6)

                sim.drop_link()
                sim.step(3000)        # out of range for 3 seconds: the band keeps counting ...
                self.clock.now = NOW + 4  # ... and so does the computer
                self.assertTrue(await until(lambda: band.connected), firmware)
                self.assertEqual(band.clock, clock)
                sim.step(500)
                sim.tap("tap")
                # t goes on from where it was: 6000 + 3000 + 500. It did not start again.
                self.assertEqual(taps[1].t, 9500, firmware)
                self.assertAlmostEqual(taps[1].time, NOW + 4.5, places=6)
                self.assertAlmostEqual(taps[1].time - taps[0].time, (taps[1].t - taps[0].t) / 1000, places=6)
                await band.disconnect()
        run(main())

    def test_time_follows_the_computer_clock_as_it_is_at_the_reconnect(self):
        async def main():
            for firmware in ("1.1", "1.0"):
                self.clock.now = NOW
                band, sim = await connect(firmware, auto_reconnect=True)
                taps = collect(band, "tap")
                await band.configure(events=True)
                sim.step(1000)
                sim.tap("tap")
                sim.drop_link()
                sim.step(3000)
                self.clock.now = NOW + 4 + 3600  # meanwhile the computer's clock was put forward an hour
                self.assertTrue(await until(lambda: band.connected))
                sim.step(500)
                sim.tap("tap")
                self.assertEqual(taps[1].t - taps[0].t, 3500, firmware)             # t: the true time between them
                self.assertAlmostEqual(taps[1].time - taps[0].time, 3603.5, places=6)  # time: steps with the clock
                await band.disconnect()
        run(main())

    def test_restart_starts_t_again_and_time_stays_right_after_connecting_again(self):
        async def main():
            for firmware, clock in (("1.1", "band"), ("1.0", "computer")):
                self.clock.now = NOW
                sim = SimTransport(42, realtime=False, firmware=firmware)
                sim.step(5000)
                band = await Band.connect(42, transport=sim, auto_reconnect=True)
                taps, ends = collect(band, "tap"), collect(band, "disconnect")
                await band.configure(events=True)
                sim.step(1000)
                sim.tap("tap")
                self.assertEqual(taps[0].t, 6000)
                await band.restart()
                sim.step(300)  # the band restarts 200 ms after its answer; 100 ms later ...
                self.assertEqual((ends, band.reconnecting), ([dict(reason="restart")], False))  # no auto-reconnect
                self.assertEqual((sim.now_ms, sim.clock_offset), (100, None))  # ... its counter shows 100, its clock is gone

                self.clock.now = NOW + 2
                again = await Band.connect(42, transport=sim)
                self.assertEqual((again.clock, again.info.uptime_ms), (clock, 100), firmware)
                taps = collect(again, "tap")
                await again.configure(events=True)
                sim.step(400)
                sim.tap("tap")
                self.assertEqual(taps[0].t, 500, firmware)  # t began again at 0: smaller than before the restart
                self.assertAlmostEqual(taps[0].time, NOW + 2.4, places=6)  # time is right all the same
                await again.disconnect()
        run(main())

    def test_after_a_restart_the_band_clock_is_unset_until_someone_sets_it(self):
        async def main():
            band, sim = await connect()
            await band.restart()
            sim.step(300)
            again = await Band.connect(42, transport=sim, set_clock=False)
            self.assertEqual(again.clock, "computer")
            self.assertEqual(await again.time(), dict(time=NOW, set=False, t=100, source="computer"))
            taps = collect(again, "tap")
            await again.configure(events=True)
            sim.step(1000)
            sim.tap("tap")
            self.assertAlmostEqual(taps[0].time, NOW + 1, places=6)  # worked out from the computer's clock
        run(main())

    def test_band_that_restarted_by_itself_and_auto_reconnect(self):
        async def main():
            for firmware, clock in (("1.1", "band"), ("1.0", "computer")):
                self.clock.now = NOW
                sim = SimTransport(42, realtime=False, firmware=firmware)
                sim.step(5000)
                band = await Band.connect(42, transport=sim, auto_reconnect=True)
                band._reconnect_pause = 0.005
                taps = collect(band, "tap")
                await band.configure(events=True)
                sim.tap("tap")
                sim.power_cycle()  # reset button: the link is lost, the counter starts at 0, the clock is gone
                self.clock.now = NOW + 1
                self.assertTrue(await until(lambda: band.connected), firmware)
                self.assertEqual(band.clock, clock)
                sim.step(250)
                sim.tap("tap")
                self.assertEqual([e.t for e in taps], [5000, 250], firmware)  # t went back
                self.assertAlmostEqual(taps[1].time, NOW + 1.25, places=6)     # time did not
                self.assertGreater(taps[1].time, taps[0].time)
                await band.disconnect()
        run(main())

    def test_fast_motion_t_follows_the_band_after_each_new_link(self):
        async def main():
            band, sim = await connect(auto_reconnect=True)
            sim.step(5000)
            motion = collect(band, "motion")
            await band.configure(motion=208)
            sim.step(100)
            before = motion[-1]
            sim.drop_link()
            sim.step(2000)
            self.assertTrue(await until(lambda: band.connected))
            sim.step(100)
            self.assertEqual(motion[-1].n < before.n or motion[-1].n < 40, True)  # n began again at 0
            self.assertGreater(motion[-1].t, before.t + 2000)
            del motion[:]
            sim.power_cycle()
            self.assertTrue(await until(lambda: band.connected))
            sim.step(100)
            self.assertLess(motion[-1].t, 200)  # the band's counter started again, and t follows it
        run(main())


class DirectTest(V11Case):
    def test_play_motion_function_then_rest(self):
        async def main():
            for firmware in ("1.1", "1.0"):
                band, sim = await connect(firmware)
                motion = collect(band, "motion")
                await band.configure(motion=50)
                asked = []
                sim.play_motion(lambda s: asked.append(round(s, 6)) or dict(gz=200, ax=0.5), seconds=0.2)
                self.assertTrue(sim.moving)
                sim.step(1000)
                self.assertFalse(sim.moving)
                self.assertEqual(asked, [0, 0.02, 0.04, 0.06, 0.08, 0.1, 0.12, 0.14, 0.16, 0.18], firmware)
                values = [(m.ax, m.ay, m.az, m.gx, m.gy, m.gz) for m in motion]
                self.assertEqual(values[:10], [(0.5, 0, 1, 0, 0, 200)] * 10, firmware)
                self.assertEqual(values[10:], [(0, 0, 1, 0, 0, 0)] * (len(values) - 10))  # lying flat and still
                self.assertGreaterEqual(len(values), 40)
        run(main())

    def test_play_motion_list_queue_and_stop(self):
        async def main():
            band, sim = await connect()
            motion = collect(band, "motion")
            await band.configure(motion=50, sensors="accel")
            sim.play_motion([dict(ax=0.1), dict(ax=0.2), dict(ax=99)])  # one entry for each sample; 99 g is clipped
            sim.play_motion(dict(ay=-1, az=0), seconds=0.04)            # a single reading, held; plays after the list
            sim.play_motion(lambda s: dict(az=-1))                       # until something else is asked for
            sim.step(200)
            self.assertEqual([(m.ax, m.ay, m.az) for m in motion][:7],
                             [(0.1, 0, 1), (0.2, 0, 1), (2, 0, 1), (0, -1, 0), (0, -1, 0), (0, 0, -1), (0, 0, -1)])
            self.assertTrue(all(m.gx is None for m in motion))  # the sensor that was not asked for
            self.assertTrue(sim.moving)
            sim.play_motion([dict(ax=1)])  # the endless one makes way
            del motion[:]
            sim.step(100)
            self.assertEqual([m.ax for m in motion][:2], [1, 0])
            self.assertFalse(sim.moving)
            sim.stop_motion()  # back to the made-up swing
            del motion[:]
            sim.step(500)
            self.assertTrue(any(m.ax != 0 for m in motion))
            for bad in (0, -1, "1", True):
                with self.assertRaises(ValueError):
                    sim.play_motion(lambda s: {}, seconds=bad)
        run(main())

    def test_a_motion_function_that_fails_is_dropped(self):
        async def main():
            band, sim = await connect()
            motion = collect(band, "motion")
            await band.configure(motion=50)
            sim.play_motion(lambda s: 1 / 0)
            with mock.patch("sys.stderr"):
                sim.step(100)
            self.assertEqual((motion[0].az, sim.moving), (1, False))
        run(main())

    def test_loudness_steady_curve_and_clap(self):
        async def main():
            band, sim = await connect()
            levels = collect(band, "mic-level")
            await band.configure(mic_level=True)
            sim.set_loudness(0.5)
            sim.step(300)
            self.assertEqual([(e.rms_db, e.peak_db, e.level) for e in levels], [(48, 42, 0.5)] * 3)
            sim.play_loudness([0.95])  # a clap: one loud reading
            sim.play_loudness(lambda s: 0.25 + s, seconds=0.2)
            del levels[:]
            sim.step(500)
            self.assertEqual([e.rms_db for e in levels], [5, 72, 62, 48, 48])
            await band.set_mic_gain(0)  # 20 dB less gain: everything reads 20 dB quieter
            del levels[:]
            sim.step(100)
            self.assertEqual(levels[0].rms_db, 68)
            sim.set_loudness(None)
            for bad in (-0.1, 1.5, "loud", True):
                with self.assertRaises(ValueError):
                    sim.set_loudness(bad)
        run(main())

    def test_play_loudness_with_audio_packets(self):
        async def main():
            band, sim = await connect(audio_packets=[b"\xaa"])
            audio = collect(band, "audio")
            await band.configure(audio=True, mic_level=0.02)
            levels = collect(band, "mic-level")
            sim.play_loudness(0.75, seconds=0.06, packets=[b"\x01", b"\x02", b"\x03"])
            sim.step(100)
            self.assertEqual([a.opus for a in audio], [b"\x01", b"\x02", b"\x03", b"\xaa", b"\xaa"])
            self.assertEqual([e.rms_db for e in levels][:3], [24, 24, 24])
            self.assertNotEqual(levels[3].rms_db, 24)
        run(main())

    def test_rssi(self):
        async def main():
            band, sim = await connect()
            link = collect(band, "link")
            await band.configure(link=True)
            sim.set_rssi(-85)
            sim.step(1000)
            self.assertEqual((link[0].rssi, (await band.get_radio()).rssi), (-85, -85))
            sim.set_rssi(None)
            sim.step(1000)
            self.assertTrue(-60 <= link[1].rssi <= -50)
            for bad in (-200, 30, "far", True):
                with self.assertRaises(ValueError):
                    sim.set_rssi(bad)
        run(main())

    def test_battery_by_name(self):
        async def main():
            band, sim = await connect()
            battery = collect(band, "battery")
            await band.configure(battery=True)
            sim.set_battery(50, "charging")
            sim.set_battery(100, "charged")
            sim.set_battery(40)
            sim.set_battery(30, 1)  # the contract's number works too
            self.assertEqual([(b.percent, b.state) for b in battery[1:]],
                             [(50, "charging"), (100, "charged"), (40, "battery"), (30, "charging")])
            with self.assertRaises(ValueError):
                sim.set_battery(50, "full")
        run(main())

    def test_activity_counts_steps_by_the_band_clock_also_while_the_link_is_down(self):
        async def main():
            band, sim = await connect(auto_reconnect=True)
            activity = collect(band, "activity")
            await band.configure(activity=True)
            sim.set_activity("run", steps=5000)
            sim.step(2000)
            self.assertEqual([(a.state, a.cadence, a.steps) for a in activity], [("run", 165, 5002), ("run", 165, 5005)])
            sim.drop_link()
            sim.step(60000 - 2000)  # a minute of running in all, most of it out of range
            self.assertTrue(await until(lambda: band.connected))
            sim.step(1000)
            self.assertEqual(activity[-1].steps, 5000 + 165 + 2)
            sim.set_activity("still")
            sim.step(3000)
            self.assertEqual((activity[-1].state, activity[-1].cadence, activity[-1].steps), ("still", 0, 5167))
            sim.set_activity("walk", cadence=60)
            sim.step(3000)
            self.assertEqual((activity[-1].state, activity[-1].cadence, activity[-1].steps), ("walk", 60, 5170))
            with self.assertRaises(ValueError):
                sim.set_activity("swim")
        run(main())

    def test_drop_link_for_some_seconds(self):
        async def main():
            band, sim = await connect(auto_reconnect=True)
            sim.drop_link(2)
            self.assertTrue(sim.reachable)
            with self.assertRaises(VexoError) as caught:
                await sim.open()
            self.assertEqual(caught.exception.code, "not-found-nearby")
            sim.step(1900)
            await until(lambda: False, 0.05)
            self.assertTrue(band.reconnecting)  # still away
            sim.step(100)
            self.assertTrue(await until(lambda: band.connected))  # back by itself
            with self.assertRaises(ValueError):
                sim.drop_link(-1)
        run(main())

    def test_after_runs_on_the_band_clock(self):
        async def main():
            band, sim = await connect()
            falls = collect(band, "free-fall")
            await band.configure(events=True)
            order = []
            sim.after(1.5, lambda: sim.tap("free-fall"))
            sim.after(0.5, lambda: order.append(sim.now_ms))
            sim.after(0.7, lambda: 1 / 0)  # an error is printed, the band goes on
            sim.step(400)
            self.assertEqual(order, [])
            with mock.patch("sys.stderr"):
                sim.step(1000)
            self.assertEqual((order, falls), ([500], []))
            sim.step(200)
            self.assertEqual([e.t for e in falls], [1500])
            # while the link is down it waits for the next connect
            sim.after(1, lambda: order.append("late"))
            await band.disconnect()
            sim.step(5000)
            self.assertEqual(order, [500])
            again = await Band.connect(42, transport=sim)
            sim.step(1)
            self.assertEqual(order, [500, "late"])
            await again.disconnect()
        run(main())

    def test_auto_tap_can_be_switched_off(self):
        async def main():
            for auto_tap, scripted in ((True, True), (False, False)):
                sim = SimTransport(42, auto_tap=auto_tap)
                band = await Band.connect(42, transport=sim)
                self.assertEqual("script" in sim._timers, scripted)
                await band.disconnect()
            with self.assertRaises(VexoError):
                SimTransport(42, auto_tap="no")
        run(main())

    def test_realtime_band_keeps_counting_while_the_link_is_down(self):
        async def main():
            sim = SimTransport(42, auto_tap=False)
            band = await Band.connect(42, transport=sim, auto_reconnect=True)
            band._reconnect_pause = 0.005
            sim.drop_link(0.2)
            dropped_at, started = sim.now_ms, time.monotonic()
            self.assertTrue(await until(lambda: band.connected, 3))
            waited = (time.monotonic() - started) * 1000
            self.assertGreaterEqual(waited, 190)  # it stayed away for the 0.2 seconds
            self.assertGreaterEqual(sim.now_ms - dropped_at, 190)  # and its counter went on meanwhile
            await band.disconnect()
        run(main())

    def test_plain_program_directs_it_through_band_transport(self):
        band = PlainBand.connect(42, transport=SimTransport(42, auto_tap=False))
        try:
            got = []
            band.on("link", got.append)
            band.on("tap", got.append)
            band.transport.set_rssi(-80)
            band.configure(link=0.1, events=True)
            band.transport.after(0.05, lambda: band.transport.tap("tap"))
            band.wait(0.5)
            self.assertTrue(got and all(e.rssi == -80 for e in got if "rssi" in e), got)
            self.assertEqual(len([e for e in got if e.get("kind") == "tap"]), 1)
        finally:
            band.disconnect()


class StoreFileTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = os.path.join(self.folder.name, "band.json")

    def test_in_memory_unless_a_file_is_named(self):
        async def main():
            before = set(os.listdir(tempfile.gettempdir()))
            sim = SimTransport(42, realtime=False)
            band = await Band.connect(42, transport=sim, auto_reconnect=True)
            band._reconnect_pause = 0.005
            await band.storage.put(7, "hello")
            sim.drop_link()
            self.assertTrue(await until(lambda: band.connected))
            self.assertEqual(await band.storage.get_text(7), "hello")  # survives a reconnect in the program
            await band.disconnect()
            self.assertIsNone(sim._store_path)
            left = [name for name in set(os.listdir(tempfile.gettempdir())) - before if "pretend" in name]
            self.assertEqual(left, [])  # nothing was written to the temp folder
        run(main())

    def test_a_named_file_keeps_the_storage_for_the_next_run(self):
        async def main():
            band, sim = await connect(store_file=self.path)
            self.assertFalse(os.path.exists(self.path))  # nothing to keep yet
            await band.storage.put(7, "hello")
            await band.storage.put(999, bytes([0, 255]))
            with open(self.path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"7": "68656c6c6f", "999": "00ff"})
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)  # readable by you only
            await band.disconnect()

            band, sim = await connect(store_file=self.path)  # the next run of the program
            self.assertEqual((await band.storage.get_text(7), await band.storage.get(999)), ("hello", bytes([0, 255])))
            await band.storage.delete(7)
            sim.wipe_ms = 500
            await band.storage.put(1, "x")
            task = __import__("asyncio").ensure_future(band.storage.wipe())
            await until(lambda: sim._wipe_end is not None)
            sim.step(600)
            await task
            with open(self.path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {})
            await band.disconnect()
            self.assertEqual(os.listdir(self.folder.name), ["band.json"])  # no half-written files left
        run(main())

    def test_a_file_that_is_not_a_store_is_ignored(self):
        for text in ("not json", "[1, 2]", '{"7": "zz"}', '{"1000": "00"}', '{"7": ""}', '{"7": "%s"}' % ("00" * 233),
                     '{"-1": "00"}', '{"7": 5}'):
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(text)
            self.assertEqual(SimTransport(42, store_file=self.path).store, {}, text)

    def test_true_means_a_folder_of_your_own_in_your_home_folder(self):
        home = os.path.join(self.folder.name, "home")
        os.mkdir(home)
        with mock.patch.dict(os.environ, dict(HOME=home, USERPROFILE=home)):
            expected = os.path.join(home, ".vexo-open", "pretend-band-42.json")
            self.assertEqual(sim_module.store_path(42), expected)

            async def main():
                band, sim = await connect(store_file=True)
                await band.storage.put(1, "mine")
                await band.disconnect()
            run(main())
            self.assertTrue(os.path.isfile(expected))
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(expected)).st_mode), 0o700)
            self.assertEqual(SimTransport(42, store_file=True).store, {1: b"mine"})
            self.assertEqual(SimTransport(7, store_file=True).store, {})  # each band number has its own file

    def test_the_folder_of_a_named_file_must_exist(self):
        with self.assertRaises(VexoError) as caught:
            SimTransport(42, store_file=os.path.join(self.folder.name, "missing", "band.json"))
        self.assertEqual(caught.exception.code, "bad-arg")


class CodecStillAgrees(unittest.TestCase):
    def test_directed_values_fit_the_frames(self):
        sim = SimTransport(42, realtime=False)
        frames = []
        sim.on_frame = lambda data: frames.append(codec.decode_frame(data))
        run(sim.open())
        sim.set_loudness(1)
        sim.set_rssi(-127)
        sim.play_motion(dict(ax=-99, gx=99999))
        sim._handle(codec.encode_command(0, "STREAM_START", dict(mask=0b11000001)))
        sim.step(1000)
        kinds = {f["frame"] for f in frames}
        self.assertTrue({"motion", "link", "mic_level"} <= kinds)
        sample = [f for f in frames if f["frame"] == "motion"][0]["samples"][0]
        self.assertEqual((sample["ax"], sample["gx"]), (-2000, 20000))  # clipped to the ranges


if __name__ == "__main__":
    unittest.main()
