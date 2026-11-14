"""Auto-reconnect: Band.connect(42, auto_reconnect=True). All against the pretend band."""
import asyncio
import time
import unittest

from vexo_open import Band, Message, SimTransport, VexoError, codec

from test_band import BandCase, plain, run

RESTORE = ["GET_INFO", "IMU_SET", "EVENT_SET", "MIC_SET_GAIN", "RADIO_SET_TX_POWER", "TEMP_SET_INTERVAL",
           "LED_SET", "STREAM_START"]


async def connect(seconds=5, sim=None, **options):
    """A band with auto-reconnect on, quick retries, and a list of its link events."""
    sim = sim or SimTransport(42, realtime=False, firmware="1.0")  # firmware 1.1: test_v11.py
    band = await Band.connect(42, transport=sim, auto_reconnect=True, reconnect_seconds=seconds, **options)
    band._reconnect_pause = 0.01
    events = []
    for name in ("reconnecting", "reconnected", "disconnect"):
        band.on(name, lambda e, name=name: events.append((name, e)))
    return band, sim, events


async def until(check, seconds=2.0):
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


def count_opens(sim):
    opens, real = [], sim.open

    async def open_():
        opens.append(time.time())
        await real()
    sim.open = open_
    return opens


async def set_everything(band):
    await band.set_motion(rate=100, accel_range=16, gyro_range=250)
    await band.set_events(kinds=["tap"], tap_threshold=2)
    await band.set_mic_gain(0)
    await band.set_tx_power(4)
    await band.set_led("#ff0000", pattern="blink")
    await band.set_led("#00ff00", brightness=0.5)  # the last light counts
    await band.identify(5)                           # not set again after a reconnect
    await band.configure(motion=True, events=True, temperature=60, battery=True, activity=True)
    await band.configure(activity=False)             # stopped again: must stay off
    await band.configure(mic_level=True)             # the mic locks the light from here on


class ReconnectTest(BandCase):
    def test_drop_reconnect_restore(self):
        async def main():
            band, sim, events = await connect(seconds=5)
            self.assertFalse(band.reconnecting)
            await set_everything(band)
            streams = dict(band.streams)
            await band.storage.put(1, "kept")
            sim.step(300)
            sim._handle = lambda frame: None       # the next command gets no answer...
            in_flight = asyncio.ensure_future(band.get_led())
            await asyncio.sleep(0.01)
            sim.reachable = False                  # ...the band goes out of range...
            del sim._handle
            names = watch(sim)
            sim.drop_link()                        # ...and the link drops

            # At once: not connected, streams off, "reconnecting" instead of "disconnect".
            self.assertEqual(events, [("reconnecting", dict(reason="lost", seconds=5))])
            self.assertIsInstance(events[0][1], Message)
            self.assertEqual((band.connected, band.reconnecting), (False, True))
            self.assertFalse(any(band.streams.values()))
            async with self.assertVexoError("not-connected"):
                await in_flight
            # Calls meanwhile fail at once and say why.
            for call in (band.get_led, lambda: band.set_led("#0000ff"), lambda: band.storage.get(1),
                         lambda: band.command("GET_INFO"), lambda: band.configure(battery=True)):
                async with self.assertVexoError("not-connected") as caught:
                    await call()
                self.assertIn("reconnecting", str(caught.error))
                self.assertIn("\"reconnected\" event", str(caught.error))
            await asyncio.sleep(0.05)              # a few tries find nothing
            self.assertEqual((len(events), band.reconnecting, names), (1, True, []))

            sim.reachable = True                   # back in range
            self.assertTrue(await until(lambda: len(events) == 2))
            name, e = events[1]
            self.assertEqual(name, "reconnected")
            self.assertEqual(sorted(e), ["attempts", "failed", "seconds"])
            self.assertGreater(e.attempts, 2)
            self.assertEqual(e.failed, [])
            self.assertTrue(0.05 <= e.seconds < 2, e.seconds)
            self.assertEqual((band.connected, band.reconnecting), (True, False))

            # Everything was set again, in the right order (the light before the streams).
            self.assertEqual(names, RESTORE)
            self.assertEqual(sim.imu, dict(rate_hz=100, accel_range_g=16, gyro_range_dps=250))
            self.assertEqual(sim.events, dict(event_mask=1, tap_threshold_mg=2000))
            self.assertEqual((sim.gain_half_db, sim.tx_dbm, sim.temp_seconds), (40, 4, 60))
            self.assertEqual((sim.user_led["g"], sim.user_led["brightness"], sim.user_led["pattern"]),
                             (255, 128, 1))
            self.assertEqual(sim.led["source"], "mic")  # and the mic light is on top again
            self.assertFalse(sim.identifying)
            self.assertEqual(band.streams, streams)
            self.assertEqual(band.streams["motion"], 100)
            self.assertEqual((await band.command("STREAM_GET")).active, sim.active)
            self.assertEqual(band.info.uptime_ms, 300)   # band.info is fresh
            self.assertEqual(await band.storage.get_text(1), "kept")

            # ...and the data flows again.
            got = {}
            for stream in ("motion", "temperature", "mic-level", "tap", "activity"):
                band.on(stream, lambda m, stream=stream: got.setdefault(stream, []).append(m))
            sim.step(60000)
            sim.tap("tap")
            self.assertEqual((len(got["motion"]), len(got["temperature"]), len(got["mic-level"]), len(got["tap"])),
                             (6000, 1, 600, 1))
            self.assertNotIn("activity", got)

            # A second drop works the same way.
            sim.drop_link()
            self.assertTrue(await until(lambda: len(events) == 4))
            self.assertEqual([name for name, _ in events], ["reconnecting", "reconnected"] * 2)
            self.assertEqual(events[3][1].attempts, 1)
            self.assertEqual(band.streams, streams)
            await band.disconnect()
            self.assertEqual(events[4:], [("disconnect", dict(reason="closed"))])
        run(main())

    def test_gives_up_after_the_window(self):
        async def main():
            band, sim, events = await connect(seconds=0.15)
            await band.set_led("#ff0000")
            await band.configure(battery=True)
            opens = count_opens(sim)
            sim.reachable = False
            dropped = time.time()
            sim.drop_link()
            self.assertEqual(events, [("reconnecting", dict(reason="lost", seconds=0.15))])
            self.assertTrue(await until(lambda: len(events) == 2))
            self.assertEqual(events[1], ("disconnect", dict(reason="lost", reconnect="gave-up")))
            self.assertTrue(0.14 <= time.time() - dropped < 0.6)
            self.assertEqual((band.connected, band.reconnecting), (False, False))
            self.assertEqual((band._restore, band._restore_mask), ({}, 0))
            self.assertGreater(len(opens), 3)
            self.assertLess(max(opens), dropped + 0.15)  # no try after the deadline
            sim.reachable = True
            tries = len(opens)
            await asyncio.sleep(0.05)
            self.assertEqual((len(opens), len(events), sim.is_open), (tries, 2, False))  # it really stopped
            async with self.assertVexoError("not-connected") as caught:
                await band.get_led()
            self.assertNotIn("reconnecting", str(caught.error))
            await band.disconnect()
            self.assertEqual(len(events), 2)
        run(main())

    def test_no_reconnect_after_disconnect_sleep_restart(self):
        async def main():
            for action, reason in (("disconnect", "closed"), ("sleep", "sleep"), ("restart", "restart")):
                for real_link in (False, True):
                    band, sim, events = await connect()
                    opens = count_opens(sim)
                    await band.set_led("#ff0000")
                    await getattr(band, action)()
                    if real_link:
                        band._closed("lost")  # a real Bluetooth link just reports "lost"
                    else:
                        sim.step(200)         # the pretend band names the reason itself
                    await asyncio.sleep(0.03)
                    self.assertEqual(events, [("disconnect", dict(reason=reason))], action)
                    self.assertEqual((band.reconnecting, band.connected, opens), (False, False, []))
        run(main())

    def test_no_reconnect_after_critical_battery(self):
        async def main():
            band, sim, events = await connect()
            notices = []
            band.on("notice", notices.append)
            opens = count_opens(sim)
            sim._emit(codec.encode_notice("CRITICAL_BATTERY", 2))  # the band is about to switch off
            sim.drop_link()
            await asyncio.sleep(0.03)
            self.assertEqual(plain(notices), [dict(kind="critical-battery", arg=2)])
            self.assertEqual(events, [("disconnect", dict(reason="lost"))])  # at once, no retry
            self.assertEqual((band.reconnecting, opens), (False, []))

            # A low-battery notice is no reason to give up.
            band, sim, events = await connect()
            sim.set_battery(10)
            sim.drop_link()
            self.assertTrue(await until(lambda: len(events) == 2))
            self.assertEqual(events[1][0], "reconnected")
        run(main())

    def test_wrong_band_stops_it(self):
        async def main():
            band, sim, events = await connect()
            await band.set_led("#ff0000")
            opens = count_opens(sim)
            names = watch(sim)
            sim.number = 17  # another band answers under the same address
            sim.drop_link()
            self.assertTrue(await until(lambda: len(events) == 2))
            self.assertEqual(events[1], ("disconnect", dict(reason="wrong-band", found=17, wanted=42)))
            self.assertEqual(names, ["GET_INFO"])  # nothing was set on the wrong band
            self.assertEqual((band.connected, band.reconnecting, sim.is_open), (False, False, False))
            self.assertEqual((band.number, band.name), (42, "Vexo-HACK-42"))
            await asyncio.sleep(0.05)
            self.assertEqual((len(opens), len(events)), (1, 2))  # no more tries
        run(main())

    def test_disconnect_while_reconnecting(self):
        async def main():
            band, sim, events = await connect()
            opens = count_opens(sim)
            sim.reachable = False
            sim.drop_link()
            await asyncio.sleep(0.03)
            self.assertTrue(band.reconnecting)
            await band.disconnect()
            self.assertEqual(events[1:], [("disconnect", dict(reason="closed"))])
            self.assertEqual((band.connected, band.reconnecting), (False, False))
            sim.reachable = True
            tries = len(opens)
            await asyncio.sleep(0.05)
            self.assertEqual((len(opens), len(events), sim.is_open), (tries, 2, False))
            await band.disconnect()  # twice is fine
            self.assertEqual(len(events), 2)
            async with self.assertVexoError("not-connected"):
                await band.get_led()

            # ...also while a try is waiting for the band's answer.
            band, sim, events = await connect()
            sim.drop_link()
            sim._handle = lambda frame: None  # the band is found but says nothing
            await asyncio.sleep(0.03)
            self.assertTrue(band.reconnecting and sim.is_open)
            await band.disconnect()
            self.assertEqual(events[1:], [("disconnect", dict(reason="closed"))])
            self.assertFalse(sim.is_open)
        run(main())

    def test_off_by_default(self):
        async def main():
            sim = SimTransport(42, realtime=False)
            band = await Band.connect(42, transport=sim)
            self.assertEqual((band.auto_reconnect, band.reconnect_seconds, band.reconnecting), (False, 120, False))
            events = []
            for name in ("reconnecting", "reconnected", "disconnect"):
                band.on(name, lambda e, name=name: events.append((name, e)))
            await band.set_led("#ff0000")
            opens = count_opens(sim)
            sim.drop_link()
            await asyncio.sleep(0.03)
            self.assertEqual(events, [("disconnect", dict(reason="lost"))])  # exactly as before
            self.assertEqual((band.connected, band.reconnecting, opens), (False, False, []))

            band, sim, events = await connect(seconds=120)
            self.assertEqual((band.auto_reconnect, band.reconnect_seconds), (True, 120))
            self.assertEqual(Band(sim)._reconnect_pause, 1.0)
            await band.disconnect()
        run(main())

    def test_bad_options(self):
        async def main():
            for options in (dict(auto_reconnect=1), dict(auto_reconnect="yes"), dict(reconnect_seconds=0),
                            dict(reconnect_seconds=-5), dict(reconnect_seconds="120"),
                            dict(reconnect_seconds=True)):
                async with self.assertVexoError("bad-arg"):
                    await Band.connect(42, transport="sim", **options)
        run(main())

    def test_a_setting_the_band_refuses_is_skipped_and_reported(self):
        async def main():
            band, sim, events = await connect()
            await set_everything(band)
            sim._do_radio_set_tx_power = lambda a: "NOT_AVAILABLE"
            sim._do_led_set = lambda a: "IO_ERROR"
            names = watch(sim)
            sim.drop_link()
            self.assertTrue(await until(lambda: len(events) == 2))
            self.assertEqual(events[1][0], "reconnected")
            self.assertEqual(events[1][1].failed, ["RADIO_SET_TX_POWER", "LED_SET"])
            self.assertEqual(names, RESTORE)  # the rest was still sent
            self.assertEqual((sim.gain_half_db, sim.temp_seconds, band.streams["motion"]), (40, 60, 100))
            self.assertTrue(band.connected)

            # Streams the band refuses: reported the same way, nothing is on.
            sim._do_stream_start = lambda a: "NOT_AVAILABLE"
            sim.drop_link()
            self.assertTrue(await until(lambda: len(events) == 4))
            self.assertEqual(events[3][1].failed, ["RADIO_SET_TX_POWER", "LED_SET", "STREAM_START"])
            self.assertFalse(any(band.streams.values()))
        run(main())

    def test_drop_during_restore_tries_again(self):
        async def main():
            band, sim, events = await connect()
            await set_everything(band)
            real, drops = sim._handle, []

            def handle(frame):
                if codec.decode_command(frame)["opcode"] == "LED_SET" and not drops:
                    drops.append(1)
                    sim.drop_link()  # gone again, in the middle of setting things
                else:
                    real(frame)
            sim.drop_link()
            sim._handle = handle
            self.assertTrue(await until(lambda: len(events) == 2))
            self.assertEqual([name for name, _ in events], ["reconnecting", "reconnected"])  # one of each
            self.assertEqual((events[1][1].attempts, events[1][1].failed, drops), (2, [], [1]))
            self.assertEqual((sim.user_led["g"], band.streams["motion"], band.connected), (255, 100, True))
        run(main())

    def test_a_band_that_does_not_answer_counts_as_a_failed_try(self):
        async def main():
            band, sim, events = await connect(seconds=5, timeout=0.03)
            real = sim._handle
            silent = [True]
            sim.drop_link()
            sim._handle = lambda frame: None if silent[0] else real(frame)
            await asyncio.sleep(0.1)
            self.assertEqual((len(events), band.reconnecting), (1, True))
            silent[0] = False
            self.assertTrue(await until(lambda: len(events) == 2))
            self.assertGreater(events[1][1].attempts, 1)
            await band.disconnect()

            # One try never runs past the deadline, even with a long answer timeout.
            band, sim, events = await connect(seconds=0.1, timeout=5)
            started = time.time()
            sim.drop_link()
            sim._handle = lambda frame: None
            self.assertTrue(await until(lambda: len(events) == 2))
            self.assertEqual(events[1], ("disconnect", dict(reason="lost", reconnect="gave-up")))
            self.assertLess(time.time() - started, 0.5)
            self.assertFalse(sim.is_open)
        run(main())

    def test_reopen_is_used_when_the_transport_has_one(self):
        class Reopening(SimTransport):
            reopened = 0

            async def reopen(self):
                self.reopened += 1
                await SimTransport.open(self)

        async def main():
            sim = Reopening(42, realtime=False)
            band, sim, events = await connect(sim=sim)
            opens = count_opens(sim)
            sim.drop_link()
            self.assertTrue(await until(lambda: len(events) == 2))
            self.assertEqual((sim.reopened, events[1][0]), (1, "reconnected"))
            self.assertEqual(opens, [])  # only through reopen(), not open()
        run(main())

    def test_after_sleep_on_the_cable_nothing_old_is_set_again(self):
        async def main():
            sim = SimTransport(42, realtime=False, via="usb", firmware="1.0")
            band, sim, events = await connect(sim=sim)
            await band.set_led("#ff0000")
            await band.configure(motion=50)
            await band.sleep()
            sim.step(200)
            band._expect_close = None  # (as if more than 3 seconds had passed)
            await band.set_mic_gain(0)  # set after the sleep: this one is remembered
            names = watch(sim)
            sim.drop_link()
            self.assertTrue(await until(lambda: len(events) == 2))
            self.assertEqual(events[1][0], "reconnected")
            self.assertEqual(names, ["GET_INFO", "MIC_SET_GAIN"])
            self.assertEqual((sim.user_led["r"], sim.active), (0, 0))
        run(main())

    def test_realtime_sim(self):
        async def main():
            band, sim, events = await connect(sim=SimTransport(42))
            await band.configure(motion=100)
            got = []
            band.on("motion", got.append)
            sim.drop_link()
            self.assertTrue(await until(lambda: len(events) == 2))
            got.clear()
            await asyncio.sleep(0.3)
            self.assertGreater(len(got), 10)
            await band.disconnect()
        run(main())


    def test_disconnect_wins_over_an_attempt_that_failed_in_the_same_moment(self):
        """disconnect() cancels the reconnect loop. If the attempt in flight has just
        failed when the cancel arrives, asyncio.wait_for (Python 3.9 to 3.11) hands back
        that failure instead of the cancel, and the loop used to carry on until
        reconnect_seconds were over, with disconnect() waiting for it all that time."""
        async def go():
            band, sim, events = await connect(seconds=60)
            asked = []

            async def open_fails_and_someone_disconnects():
                if not asked:
                    asked.append(asyncio.ensure_future(band.disconnect()))
                raise VexoError("not-found-nearby", "The pretend band is away.")
            sim.open = open_fails_and_someone_disconnects
            sim.drop_link()
            self.assertTrue(await until(lambda: asked))
            started = time.time()
            await asyncio.wait_for(asyncio.shield(asked[0]), 3)  # used to take the whole 60 seconds
            self.assertLess(time.time() - started, 1)
            self.assertEqual(events, [("reconnecting", dict(reason="lost", seconds=60)),
                                      ("disconnect", dict(reason="closed"))])
            self.assertFalse(band.reconnecting or band.connected)
            await asyncio.sleep(0.05)  # and nothing follows
            self.assertEqual(len(events), 2)
        run(go())

    def test_disconnect_wins_over_an_attempt_that_succeeded_in_the_same_moment(self):
        async def go():
            band, sim, events = await connect(seconds=60)
            asked, real_open = [], sim.open

            async def open_works_and_someone_disconnects():
                await real_open()
                asked.append(True)
            sim.open = open_works_and_someone_disconnects
            real_exchange = band._exchange

            async def exchange(name, args, restoring=False):
                data = await real_exchange(name, args, restoring)
                if name == "STREAM_START" and restoring and len(asked) == 1:  # the last step of a reconnect
                    asked.append(asyncio.ensure_future(band.disconnect()))
                return data
            band._exchange = exchange
            await band.configure(events=True)
            sim.drop_link()
            self.assertTrue(await until(lambda: len(asked) == 2))
            await asyncio.wait_for(asyncio.shield(asked[1]), 3)
            await asyncio.sleep(0.05)
            self.assertEqual(events, [("reconnecting", dict(reason="lost", seconds=60)),
                                      ("disconnect", dict(reason="closed"))])
            self.assertFalse(band.reconnecting or band.connected or sim.is_open)
        run(go())


if __name__ == "__main__":
    unittest.main()
