"""Band against the simulator. The simulator's clock only moves when a test calls step()."""
import asyncio
import contextlib
import io
import unittest

from vexo_open import Band, Message, SimTransport, Transport, VexoError, codec


def run(coro):
    return asyncio.run(coro)


async def connect(number=42, **sim_options):
    # These tests describe a band with firmware 1.0 (the frames they count are the 1.0 ones).
    # Firmware 1.1, the pretend band's default, is tested in test_v11.py.
    sim_options.setdefault("firmware", "1.0")
    sim = SimTransport(number, realtime=False, **sim_options)
    return await Band.connect(number, transport=sim), sim


async def stepping(sim, coro, ms=100):
    """Run a library call that needs band time to pass (the pretend band's clock only
    moves in step()): step the clock until the call is done."""
    task = asyncio.ensure_future(coro)
    while not task.done():
        sim.step(ms)
        await asyncio.sleep(0.001)
    return await task


def plain(messages):
    """The events without their "time" field (real time, different on every run; it has
    its own tests in test_v11.py). Every data event must have one."""
    for m in messages:
        assert isinstance(m["time"], float), m
    return [{k: v for k, v in m.items() if k != "time"} for m in messages]


class Silent(Transport):
    """A link where nothing ever answers."""
    kind = "sim"

    def __init__(self):
        super().__init__()
        self.sent = []
        self.closed = False

    async def open(self):
        pass

    async def close(self):
        self.closed = True

    async def send(self, frame):
        self.sent.append(frame)


class BandCase(unittest.TestCase):
    def assertVexoError(self, code):
        return _ExpectCode(self, code)


class _ExpectCode:
    """async with self.assertVexoError("denied") as caught: ...  (caught.error is the VexoError)"""

    def __init__(self, case, code):
        self.case, self.code, self.error = case, code, None

    async def __aenter__(self):
        return self

    async def __aexit__(self, kind, error, tb):
        self.case.assertIsInstance(error, VexoError, "expected VexoError %r" % self.code)
        self.case.assertEqual(error.code, self.code, str(error))
        self.case.assertGreater(len(str(error)), 20)  # a real sentence, not just a code
        self.error = error
        return True


class ConnectTest(BandCase):
    def test_identity(self):
        async def main():
            band, sim = await connect(42)
            self.assertEqual((band.number, band.name, band.connected, band.transport_kind),
                             (42, "Vexo-HACK-42", True, "sim"))
            self.assertEqual(band.info, dict(
                number=42, protocol="1.0", firmware="1.0.0", hardware=1, max_frame=244, uptime_ms=0, features=[],
                capabilities=["accel", "gyro", "events", "steps", "mic", "led", "battery", "temperature",
                              "storage", "tx-power", "usb"]))
            self.assertIsInstance(band.info, Message)
            self.assertEqual(band.info.max_frame, 244)
            sim.step(1500)
            self.assertEqual((await band.get_info()).uptime_ms, 1500)
            self.assertEqual(await band.get_name(), "Vexo-HACK-42")
            self.assertFalse(any(band.streams.values()))
            self.assertEqual(sorted(band.streams), sorted(
                ["motion", "events", "activity", "audio", "battery", "temperature", "link", "mic_level"]))
            await band.disconnect()
            await band.disconnect()  # twice is fine
            self.assertFalse(band.connected)
        run(main())

    def test_wrong_band(self):
        async def main():
            sim = SimTransport(17, realtime=False)
            async with self.assertVexoError("wrong-band") as caught:
                await Band.connect(42, transport=sim)
            self.assertIn("Vexo-HACK-17", str(caught.error))
            self.assertIn("Vexo-HACK-42", str(caught.error))
            self.assertFalse(sim.is_open)  # the link was closed again
            unset = SimTransport(0, realtime=False)
            async with self.assertVexoError("wrong-band"):
                await Band.connect(42, transport=unset)
        run(main())

    def test_none_accepts_any_band(self):
        async def main():
            band = await Band.connect(None, transport=SimTransport(17, realtime=False))
            self.assertEqual((band.number, band.name), (17, "Vexo-HACK-17"))
            band = await Band.connect(None, transport="sim", realtime=False)
            self.assertEqual(band.name, "Vexo-HACK-42")
            band = await Band.connect("007", transport="sim", realtime=False)
            self.assertEqual(band.number, 7)
        run(main())

    def test_bad_input(self):
        async def main():
            async with self.assertVexoError("bad-transport"):
                await Band.connect(42, transport="carrier-pigeon")
            async with self.assertVexoError("bad-arg"):
                await Band.connect(1000, transport="sim")
            async with self.assertVexoError("bad-arg"):
                await Band.connect(42, transport="sim", via="wifi")
            async with self.assertVexoError("bad-arg"):
                await Band.connect(42, transport="sim", no_such_option=1)
        run(main())

    def test_async_context_manager(self):
        async def main():
            async with await Band.connect(42, transport="sim", realtime=False) as band:
                await band.set_led("#00ff00")
            self.assertFalse(band.connected)
        run(main())


class ConfigureTest(BandCase):
    def test_motion_units_and_rate(self):
        async def main():
            band, sim = await connect()
            samples = []
            band.on("motion", samples.append)
            streams = await band.configure(motion=True)
            self.assertEqual(streams["motion"], 25)  # True keeps the rate; 25 at first
            sim.step(1000)
            self.assertEqual(len(samples), 20)  # two frames of 10
            self.assertEqual([s.t for s in samples[:3]], [0, 40, 80])
            s = samples[5]
            self.assertEqual(sorted(s), ["ax", "ay", "az", "gx", "gy", "gz", "n", "t", "time"])
            self.assertTrue(0.9 < s.az < 1.1 and abs(s.ax) < 0.2 and abs(s.gx) <= 30)

            await band.configure(motion=100)
            self.assertEqual(band.streams["motion"], 100)
            self.assertEqual(await band.get_motion(), dict(rate=100, sensors="both", accel_range=2, gyro_range=2000))
            del samples[:]
            sim.step(1000)
            self.assertEqual(len(samples), 100)
            self.assertEqual(samples[1].t - samples[0].t, 10)

            await band.configure(motion=False)
            self.assertEqual(band.streams["motion"], 0)
            del samples[:]
            sim.step(1000)
            self.assertEqual(samples, [])
            await band.configure(motion=True)
            self.assertEqual(band.streams["motion"], 100)  # the rate was kept
        run(main())

    def test_unit_conversion_of_a_known_frame(self):
        async def main():
            band, sim = await connect()
            got = {}
            for name in ("motion", "activity", "temperature", "link", "mic-level", "battery", "audio",
                         "orientation", "notice"):
                band.on(name, lambda m, name=name: got.setdefault(name, []).append(m))
            await band.configure(events=True)
            S = codec.encode_stream
            frames = [
                S("motion", dict(t_ms=1000, period_ms=20, samples=[
                    dict(ax=500, ay=-1000, az=250, gx=125, gy=-2000, gz=0)] * 2)),
                S("activity", dict(t_ms=5, steps_total=123456, cadence_spm=112, state=2, motion_mg=340)),
                S("temp", dict(t_ms=6, temp_mc=-4750)),
                S("link", dict(t_ms=7, rssi_dbm=127, tx_dbm=-8, phy=0)),
                S("link", dict(t_ms=8, rssi_dbm=-63, tx_dbm=4, phy=1)),
                S("mic_level", dict(t_ms=9, peak_db=12, rms_db=24)),
                S("mic_level", dict(t_ms=9, peak_db=200, rms_db=200)),
                S("battery", dict(percent=100, state=2, millivolts=4190)),
                S("battery", dict(percent=50, state=1, millivolts=3900)),
                S("audio", dict(seq=65535, opus=b"\xbb\x01")),
                S("event", dict(t_ms=10, event=5, arg=4)),
                S("event", dict(t_ms=10, event=200, arg=0)),       # unknown kind: ignored
                codec.encode_notice("STREAM_DROPPED", 3),
                codec.encode_notice(200, 1),                       # unknown notice: ignored
                bytes([0x7F, 1, 2]), b"", b"\x02", b"\x10\x00",    # unknown or broken: ignored
                codec.encode_response(99, "LED_GET", "OK", dict(sim.user_led)),  # nobody asked: ignored
            ]
            for frame in frames:
                sim.on_frame(frame)
            self.assertIsInstance(got["motion"][0], Message)
            got = {name: plain(messages) for name, messages in got.items()}
            self.assertEqual(got["motion"], [
                dict(t=1000, ax=0.5, ay=-1.0, az=0.25, gx=12.5, gy=-200.0, gz=0.0, n=None),
                dict(t=1020, ax=0.5, ay=-1.0, az=0.25, gx=12.5, gy=-200.0, gz=0.0, n=None)])
            self.assertEqual(got["activity"], [dict(t=5, steps=123456, cadence=112, state="run", motion=0.34)])
            self.assertEqual(got["temperature"], [dict(t=6, celsius=-4.75)])
            self.assertEqual(got["link"], [dict(t=7, rssi=None, tx_power=-8, phy=None),
                                           dict(t=8, rssi=-63, tx_power=4, phy="1M")])
            self.assertEqual(got["mic-level"], [dict(t=9, peak_db=12, rms_db=24, level=0.75),
                                                dict(t=9, peak_db=200, rms_db=200, level=0.0)])
            self.assertEqual(got["battery"], [
                dict(percent=100, millivolts=4190, state="charged", charging=False),
                dict(percent=50, millivolts=3900, state="charging", charging=True)])
            self.assertEqual(got["audio"], [dict(seq=65535, opus=b"\xbb\x01")])
            self.assertEqual(got["orientation"], [dict(t=10, kind="orientation", arg=4, face="+z")])
            self.assertEqual(got["notice"], [dict(kind="stream-dropped", arg=3)])
            self.assertEqual(await band.get_name(), "Vexo-HACK-42")  # still healthy after the junk
        run(main())

    def test_events(self):
        async def main():
            band, sim = await connect()
            every, taps, doubles = [], [], []
            band.on("event", every.append)
            band.on("tap", taps.append)

            @band.on("double-tap")
            def on_double(e):
                doubles.append(e)

            self.assertFalse(sim.tap("tap"))  # the stream is still off
            await band.configure(events=True)
            self.assertTrue(band.streams["events"])
            sim.step(250)
            for kind in ("tap", "double-tap", "free-fall", "tilt", "motion-start", "motion-stop"):
                self.assertTrue(sim.tap(kind))
            sim.tap("orientation", 1)
            self.assertEqual([e.kind for e in every], ["tap", "double-tap", "free-fall", "tilt",
                                                       "motion-start", "motion-stop", "orientation"])
            self.assertEqual(plain(taps), [dict(t=250, kind="tap", arg=0)])
            self.assertEqual(len(doubles), 1)
            self.assertEqual(every[-1].face, "-x")

            band.off("tap", taps.append)   # a different bound method object: must still work
            band.off("double-tap", on_double)
            with self.assertRaises(VexoError) as caught:   # a name that is not an event: as in on()
                band.off("nothing", on_double)
            self.assertEqual(caught.exception.code, "bad-event")
            sim.tap("double-tap")
            self.assertEqual(len(doubles), 1)

            # Only taps from now on.
            self.assertEqual(await band.set_events(kinds=["tap"]), dict(kinds=["tap"], tap_threshold=0.8))
            self.assertFalse(sim.tap("double-tap"))
            self.assertTrue(sim.tap("tap"))
        run(main())

    def test_other_streams(self):
        async def main():
            band, sim = await connect()
            got = {}
            for name in ("activity", "battery", "temperature", "link", "mic-level"):
                band.on(name, lambda m, name=name: got.setdefault(name, []).append(m))
            streams = await band.configure(activity=True, battery=True, temperature=2, link=True, mic_level=True)
            self.assertEqual(streams, dict(motion=0, events=False, activity=True, audio=False, battery=True,
                                           temperature=True, link=True, mic_level=True))
            self.assertEqual(len(got["battery"]), 1)  # once at start
            self.assertEqual(plain(got["battery"])[0], await band.get_battery())
            sim.step(10000)
            self.assertEqual(len(got["activity"]), 10)
            self.assertGreater(got["activity"][-1].steps, got["activity"][0].steps)
            self.assertEqual(got["activity"][0].state, "walk")
            self.assertEqual(len(got["temperature"]), 5)  # every 2 s
            self.assertTrue(25 < got["temperature"][0].celsius < 40)
            self.assertEqual(len(got["link"]), 10)
            self.assertEqual((got["link"][0].phy, got["link"][0].tx_power), ("2M", -8))
            self.assertTrue(-70 < got["link"][0].rssi < -40)
            self.assertEqual(len(got["mic-level"]), 100)
            self.assertTrue(all(0 <= m.level <= 1 for m in got["mic-level"]))

            # The mic level follows the gain: 20 dB less gain reads 20 dB quieter.
            loud = max(m.level for m in got["mic-level"])
            await band.set_mic_gain(0)
            del got["mic-level"][:]
            sim.step(10000)
            self.assertLess(max(m.level for m in got["mic-level"]), loud)

            await band.configure(temperature=False, link=False, activity=False, mic_level=False)
            self.assertEqual([k for k, v in band.streams.items() if v], ["battery"])
            sim.set_battery(9)
            self.assertEqual((got["battery"][-1].percent, got["battery"][-1].state), (9, "battery"))
        run(main())

    def test_low_battery_notice(self):
        async def main():
            band, sim = await connect()
            notices = []
            band.on("notice", notices.append)
            sim.set_battery(50)
            self.assertEqual(notices, [])
            sim.set_battery(14)
            self.assertEqual(plain(notices), [dict(kind="low-battery", arg=14)])
            self.assertEqual((await band.get_battery()).percent, 14)
            sim.set_battery(80, 1)
            self.assertEqual(await band.get_battery(),
                             dict(percent=80, millivolts=4020, state="charging", charging=True))
        run(main())

    def test_bad_configure(self):
        async def main():
            band, sim = await connect()
            async with self.assertVexoError("bad-stream"):
                await band.configure(heartbeat=True)
            for rate in (30, 1, 1000, 12.5, "50"):
                async with self.assertVexoError("bad-arg"):
                    await band.configure(motion=rate)
            for seconds in (256, -1, 2.5):
                async with self.assertVexoError("bad-arg"):
                    await band.configure(temperature=seconds)
            self.assertFalse(any(band.streams.values()))
            # A stream bit this band does not have: nothing starts.
            async with self.assertVexoError("not-available"):
                await band.command("STREAM_START", mask=0x0101)
            self.assertEqual((await band.command("STREAM_GET")).active, 0)
        run(main())

    def test_raw_stream_commands_keep_streams_in_step(self):
        async def main():
            band, sim = await connect()
            await band.command("STREAM_START", mask=0b10100)
            self.assertEqual([k for k, v in band.streams.items() if v], ["activity", "battery"])
        run(main())


class AudioAndLedTest(BandCase):
    def test_mic_light_owns_the_led(self):
        async def main():
            shown = []
            band, sim = await connect(on_led=shown.append)
            await band.set_led("#ff8800", brightness=0.5, pattern="blink", period=500)
            mine = dict(r=255, g=136, b=0, brightness=128, pattern=2, period_ms=500, source="user")
            self.assertEqual(shown[-1], mine)

            await band.configure(audio=True)
            self.assertEqual(sim.led, dict(r=0, g=0, b=255, brightness=255, pattern=1, period_ms=1000,
                                           source="mic"))
            self.assertEqual(shown[-1]["source"], "mic")
            for refused in (band.set_led("#00ff00"), band.led_off(), band.identify()):
                async with self.assertVexoError("denied") as caught:
                    await refused
                for word in ("microphone", "blue", "audio=False"):
                    self.assertIn(word, str(caught.error))
            # get_led still gives the stored setting, and it comes back when audio stops.
            self.assertEqual((await band.get_led()).pattern, "blink")
            self.assertEqual(sim.led["source"], "mic")
            await band.configure(audio=False)
            self.assertEqual(sim.led, mine)
            self.assertEqual(shown[-1], mine)
            await band.set_led("#00ff00")
            self.assertEqual(sim.led["g"], 255)

            # The loudness meter alone powers the microphone too: blue light, LED refused.
            await band.configure(mic_level=True)
            self.assertEqual(sim.led["source"], "mic")
            self.assertEqual(shown[-1]["source"], "mic")
            for refused in (band.set_led("#0000ff"), band.led_off(), band.identify()):
                async with self.assertVexoError("denied") as caught:
                    await refused
                self.assertIn("mic_level=False", str(caught.error))
            # With both running, the light stays until both have stopped.
            await band.configure(audio=True)
            await band.configure(mic_level=False)
            self.assertEqual(sim.led["source"], "mic")
            await band.configure(audio=False)
            self.assertEqual(sim.led["source"], "user")
            self.assertEqual(sim.led["g"], 255)
            await band.set_led("#0000ff")
            self.assertEqual(sim.led["b"], 255)
        run(main())

    def test_audio_stream(self):
        async def main():
            band, sim = await connect()
            packets = []
            band.on("audio", packets.append)
            await band.configure(audio=True)
            sim.step(1000)
            self.assertEqual([p.seq for p in packets], list(range(50)))
            for p in packets:  # a valid silent Opus packet of speech-like size
                n = p.opus[2]
                self.assertEqual(p.opus[:2], b"\xbb\x41")
                self.assertTrue(20 <= n <= 80 and p.opus[3:] == bytes(n))
            self.assertEqual((await band.get_radio()).interval_ms, 15.0)  # faster link while audio is on

            await band.configure(audio=False)
            del packets[:]
            sim.step(500)
            self.assertEqual(packets, [])
            await band.configure(audio=True)
            sim.step(100)
            self.assertEqual([p.seq for p in packets], [0, 1, 2, 3, 4])  # seq restarts at 0
        run(main())

    def test_audio_packets_option(self):
        async def main():
            band, sim = await connect(audio_packets=[b"one", b"two", b"three"])
            packets = []
            band.on("audio", packets.append)
            await band.configure(audio=True)
            sim.step(100)
            self.assertEqual([p.opus for p in packets], [b"one", b"two", b"three", b"one", b"two"])
        run(main())

    def test_led(self):
        async def main():
            band, sim = await connect()
            self.assertEqual(await band.get_led(), dict(r=0, g=0, b=0, brightness=0.0, pattern="off", period=1000))
            self.assertIsNone(await band.set_led("#FF8800"))
            self.assertEqual(await band.get_led(), dict(r=255, g=136, b=0, brightness=1.0, pattern="solid",
                                                        period=1000))
            await band.set_led(1, 2, 3, brightness=0.5, pattern="rainbow", period=2500)
            self.assertEqual(sim.user_led, dict(r=1, g=2, b=3, brightness=128, pattern=5, period_ms=2500))
            self.assertAlmostEqual((await band.get_led()).brightness, 128 / 255)
            await band.set_led("00ff00")
            self.assertEqual(sim.user_led["g"], 255)
            await band.led_off()
            self.assertEqual(sim.led, dict(r=0, g=0, b=0, brightness=0, pattern=0, period_ms=1000, source="user"))

            for colour in ("green", "#12345", "#gggggg", (300, 0, 0), (1, 2, None), (1.5, 2, 3)):
                args = colour if isinstance(colour, tuple) else (colour,)
                async with self.assertVexoError("bad-colour"):
                    await band.set_led(*args)
            for options in (dict(brightness=2), dict(brightness=-0.1), dict(pattern="disco"),
                            dict(period=50), dict(period=20000)):
                async with self.assertVexoError("bad-arg"):
                    await band.set_led("#ffffff", **options)
        run(main())

    def test_identify(self):
        async def main():
            band, sim = await connect()
            await band.set_led("#ff0000")
            await band.identify()
            self.assertEqual(sim.led, dict(r=255, g=255, b=255, brightness=255, pattern=2, period_ms=250,
                                           source="identify"))
            sim.step(4999)
            self.assertEqual(sim.led["source"], "identify")
            sim.step(2)
            self.assertEqual((sim.led["source"], sim.led["r"], sim.led["g"]), ("user", 255, 0))
            await band.identify(10)
            sim.step(9000)
            self.assertEqual(sim.led["source"], "identify")
            for seconds in (0, 31, 2.5):
                async with self.assertVexoError("bad-arg"):
                    await band.identify(seconds)
        run(main())


class ControlsTest(BandCase):
    def test_motion_settings(self):
        async def main():
            band, sim = await connect()
            self.assertEqual(await band.get_motion(), dict(rate=25, sensors="both", accel_range=2, gyro_range=2000))
            self.assertEqual(await band.set_motion(rate=100, accel_range=8, gyro_range=500),
                             dict(rate=100, sensors="both", accel_range=8, gyro_range=500))
            self.assertEqual(await band.set_motion(gyro_range=0),
                             dict(rate=100, sensors="both", accel_range=8, gyro_range=0))
            self.assertEqual((await band.get_motion()).accel_range, 8)  # what was left out is kept
            samples = []
            band.on("motion", samples.append)
            await band.configure(motion=True)
            sim.step(200)
            self.assertEqual(len(samples), 20)
            self.assertTrue(all(s.gx == s.gy == s.gz == 0 for s in samples))  # gyro off
            for bad in (dict(rate=60), dict(accel_range=3), dict(gyro_range=100), dict(rate=999)):
                async with self.assertVexoError("bad-arg"):
                    await band.set_motion(**bad)
            self.assertEqual(await band.get_motion(), dict(rate=100, sensors="both", accel_range=8, gyro_range=0))
        run(main())

    def test_event_settings(self):
        async def main():
            band, sim = await connect()
            self.assertEqual(await band.get_events(), dict(
                kinds=["tap", "double-tap", "free-fall", "tilt", "orientation", "motion"], tap_threshold=0.8))
            self.assertEqual(await band.set_events(kinds=["tap", "double-tap"], tap_threshold=1.2),
                             dict(kinds=["tap", "double-tap"], tap_threshold=1.2))
            self.assertEqual(sim.events, dict(event_mask=3, tap_threshold_mg=1200))
            self.assertEqual((await band.set_events(tap_threshold=0.2)).kinds, ["tap", "double-tap"])
            self.assertEqual((await band.set_events(kinds=["motion"])).tap_threshold, 0.2)
            for bad in (dict(kinds=["jump"]), dict(tap_threshold=0.1), dict(tap_threshold=9)):
                async with self.assertVexoError("bad-arg"):
                    await band.set_events(**bad)
            async with self.assertVexoError("bad-arg"):  # the band itself says no
                await band.command("EVENT_SET", event_mask=3, tap_threshold_mg=100)
        run(main())

    def test_mic(self):
        async def main():
            band, sim = await connect()
            self.assertEqual(await band.get_mic(), dict(gain_db=20.0, sample_rate=16000, frame_ms=20,
                                                        channels=1, bitrate=24000))
            self.assertEqual(await band.set_mic_gain(10), 10.0)
            self.assertEqual(sim.gain_half_db, 60)
            self.assertEqual(await band.set_mic_gain(-20), -20.0)
            self.assertEqual(await band.set_mic_gain(-2.5), -2.5)
            self.assertEqual((await band.get_mic()).gain_db, -2.5)
            for bad in (21, -20.5, "loud"):
                async with self.assertVexoError("bad-arg"):
                    await band.set_mic_gain(bad)
            async with self.assertVexoError("bad-arg"):
                await band.command("MIC_SET_GAIN", gain_half_db=81)
        run(main())

    def test_radio(self):
        async def main():
            band, sim = await connect()
            radio = await band.get_radio()
            self.assertEqual((radio.tx_power, radio.phy, radio.interval_ms, radio.mtu), (-8, "2M", 30.0, 247))
            self.assertTrue(-70 < radio.rssi < -40)
            self.assertEqual(await band.set_tx_power(-12), -12)
            self.assertEqual((await band.get_radio()).tx_power, -12)
            self.assertEqual(await band.set_tx_power(4), 4)
            for bad in (8, -13, 200, 1.5):
                async with self.assertVexoError("bad-arg"):
                    await band.set_tx_power(bad)
            self.assertEqual((await band.get_radio()).tx_power, 4)
        run(main())

    def test_band_number(self):
        async def main():
            band, sim = await connect(42)  # the band believes it is on Bluetooth
            async with self.assertVexoError("denied") as caught:
                await band.set_band_number(7)
            self.assertIn("USB", str(caught.error))
            self.assertEqual((band.number, sim.number), (42, 42))

            band, sim = await connect(42, via="usb")
            self.assertEqual(await band.set_band_number(7), 7)
            self.assertEqual((band.number, band.name, band.info.number), (7, "Vexo-HACK-07", 7))
            self.assertEqual(await band.get_name(), "Vexo-HACK-07")
            for bad in (0, 1000, "7", 2.0):
                async with self.assertVexoError("bad-arg"):
                    await band.set_band_number(bad)
            # The number survives a disconnect.
            await band.disconnect()
            band = await Band.connect(7, transport=sim)
            self.assertEqual(band.name, "Vexo-HACK-07")
        run(main())

    def test_command_by_contract_name(self):
        async def main():
            band, sim = await connect()
            self.assertEqual(await band.command("IMU_SET", rate_hz=50, accel_range_g=2, gyro_range_dps=2000),
                             dict(rate_hz=50, accel_range_g=2, gyro_range_dps=2000))
            led = await band.command("LED_GET")
            self.assertIsInstance(led, Message)
            self.assertEqual(led.period_ms, 1000)
            self.assertEqual(await band.command("LED_SET", r=1, g=2, b=3, brightness=4, pattern=1, period_ms=5), {})
            self.assertEqual((await band.command("TEMP_SET_INTERVAL", seconds=9)).seconds, 9)
            for name, args in (("NO_SUCH", {}), ("LED_GET", dict(x=1)), ("IDENTIFY", {}),
                               ("IDENTIFY", dict(seconds=999))):
                async with self.assertVexoError("bad-arg"):  # refused before sending
                    await band.command(name, **args)
            for name, args in (("LED_SET", dict(r=0, g=0, b=0, brightness=0, pattern=6, period_ms=1000)),
                               ("LED_SET", dict(r=0, g=0, b=0, brightness=0, pattern=2, period_ms=99)),
                               ("TEMP_SET_INTERVAL", dict(seconds=0)),
                               ("IMU_SET", dict(rate_hz=11, accel_range_g=2, gyro_range_dps=0)),
                               ("STORE_GET", dict(key=1000)), ("STORE_DEL", dict(key=1000)),
                               ("STORE_WIPE", dict(magic=1)), ("IDENTIFY", dict(seconds=31))):
                async with self.assertVexoError("bad-arg"):  # refused by the band
                    await band.command(name, **args)
            async with self.assertVexoError("bad-length"):
                await band.command("STORE_PUT", key=1, value=b"")
            async with self.assertVexoError("bad-length"):
                await band.command("STORE_PUT", key=1, value=bytes(233))
        run(main())

    def test_sim_answers_unknown_and_malformed_commands(self):
        async def main():
            band, sim = await connect()
            answers = []
            sim.on_frame = answers.append
            await sim.send(bytes([1, 5, 0x99]))           # no such opcode
            await sim.send(bytes([1, 6, 0x04]))           # IDENTIFY without its argument
            await sim.send(bytes([1, 7, 0x01, 0xAA]))     # GET_INFO with an argument too many
            await sim.send(bytes([2, 0, 1, 0]))           # not a command: dropped, no reply
            self.assertEqual([codec.decode_frame(a)["status"] for a in answers],
                             ["UNKNOWN_OPCODE", "BAD_LENGTH", "BAD_LENGTH"])
            self.assertEqual(codec.decode_frame(answers[0])["opcode"], 0x99)
        run(main())

    def test_every_opcode_answers(self):
        async def main():
            band, sim = await connect(via="usb", firmware="1.1")  # 1.1 knows every opcode in the table
            args = dict(
                TIME_SET=dict(unix_ms=1794484800123), STREAM_SET_INTERVAL=dict(stream=5, interval_ms=1000),
                STREAM_GET_INTERVAL=dict(stream=5), MOTION2_SET=dict(rate_hz=25, sensors=3),
                SET_BAND_NUMBER=dict(number=42), IDENTIFY=dict(seconds=1), STREAM_START=dict(mask=1),
                STREAM_STOP=dict(mask=1), IMU_SET=dict(rate_hz=25, accel_range_g=2, gyro_range_dps=0),
                EVENT_SET=dict(event_mask=1, tap_threshold_mg=800), MIC_SET_GAIN=dict(gain_half_db=1),
                RADIO_SET_TX_POWER=dict(dbm=0), LED_SET=dict(r=0, g=0, b=0, brightness=0, pattern=0, period_ms=0),
                TEMP_SET_INTERVAL=dict(seconds=1), STORE_PUT=dict(key=1, value=b"x"), STORE_GET=dict(key=1),
                STORE_DEL=dict(key=1))
            for name in codec.COMMANDS:
                if name not in ("RESTART", "SLEEP"):
                    self.assertIsInstance(await band.command(name, **args.get(name, {})), Message, name)
        run(main())


class StorageTest(BandCase):
    def test_storage(self):
        async def main():
            band, sim = await connect()
            store = band.storage
            self.assertEqual(await store.info(), dict(keys_max=1000, value_max=232, free_bytes=242164, used_keys=0))
            self.assertIsNone(await store.get(7))       # empty key: None, not an error
            self.assertIsNone(await store.get_text(7))
            self.assertIsNone(await store.put(7, "héllo ✓"))
            self.assertEqual(await store.get_text(7), "héllo ✓")
            self.assertEqual(await store.get(7), "héllo ✓".encode("utf-8"))
            await store.put(0, b"\x00\x01\xff")
            await store.put(999, bytes(232))
            self.assertEqual(await store.get(0), b"\x00\x01\xff")
            await store.put(0, bytearray(b"new"))       # a put replaces the old value
            self.assertEqual(await store.get(0), b"new")
            info = await store.info()
            # each key costs 8 bytes plus its value rounded up to a multiple of 4
            self.assertEqual((info.used_keys, info.free_bytes), (3, 242164 - (8 + 12) - (8 + 4) - (8 + 232)))

            await store.delete(7)
            self.assertIsNone(await store.get(7))
            async with self.assertVexoError("not-found"):
                await store.delete(7)
            async with self.assertVexoError("bad-arg"):
                await store.put(1000, "x")              # the band refuses the key
            async with self.assertVexoError("bad-arg"):
                await store.get(1000)
            for value in ("", b"", bytes(233), "é" * 117):
                async with self.assertVexoError("bad-arg"):
                    await store.put(1, value)

            # Storage survives a disconnect; a wipe empties it.
            await band.disconnect()
            band = await Band.connect(42, transport=sim)
            self.assertEqual(await band.storage.get(0), b"new")
            band.storage._busy_pause = 0.001
            await stepping(sim, band.storage.wipe())    # erasing takes a second of band time
            self.assertEqual((await band.storage.info()).used_keys, 0)
            self.assertIsNone(await band.storage.get(0))
            band2, sim2 = await connect()               # a new pretend band starts empty
            self.assertIsNone(await band2.storage.get(0))
        run(main())

    def test_no_space(self):
        async def main():
            band, sim = await connect()
            # 1000 keys of the biggest value fit (1000 x 240 bytes), with a little left over.
            sim.store = {key: bytes(232) for key in range(999)}
            await band.storage.put(999, bytes(232))
            self.assertEqual((await band.storage.info()).free_bytes, 242164 - 240000)
            await band.storage.put(999, bytes(232))  # replacing a value needs no extra room
            # Make one value bigger than the band allows, to fill the rest quickly: now
            # the same put does not fit any more.
            sim.store[0] = bytes(2400)
            async with self.assertVexoError("no-space") as caught:
                await band.storage.put(999, bytes(232))
            self.assertIn("full", str(caught.error))
        run(main())


class LinkTest(BandCase):
    def test_timeout(self):
        async def main():
            silent = Silent()
            async with self.assertVexoError("timeout"):
                await Band.connect(42, transport=silent, timeout=0.05)
            self.assertTrue(silent.closed)
            self.assertEqual(codec.decode_command(silent.sent[0])["opcode"], "GET_INFO")

            band, sim = await connect()
            band.timeout = 0.05
            sim._handle = lambda frame: None  # the band stops answering
            async with self.assertVexoError("timeout"):
                await band.get_led()
            del sim._handle
            self.assertEqual((await band.get_led()).pattern, "off")  # and recovers
        run(main())

    def test_late_answer_is_ignored(self):
        async def main():
            band, sim = await connect()
            band.timeout = 0.05
            held = []
            sim._handle = held.append
            async with self.assertVexoError("timeout"):
                await band.get_name()
            del sim._handle
            self.assertEqual((await band.get_mic()).channels, 1)
            sim._handle(held[0])  # the old answer arrives now: nobody is waiting, nothing breaks
            self.assertEqual(await band.get_name(), "Vexo-HACK-42")
        run(main())

    def test_late_answer_is_not_taken_for_the_answer_to_a_later_command(self):
        # Firmware 1.1.3 keeps a response through a pause, so it can arrive after its
        # command timed out, while the same command is waiting again with the next seq.
        async def main():
            band, sim = await connect()
            band.timeout = 0.05
            held = []
            sim._handle = held.append
            async with self.assertVexoError("timeout"):
                await band.command("LED_GET")
            old_seq = codec.decode_command(held[0])["seq"]
            band.timeout = 2
            second = asyncio.ensure_future(band.command("LED_GET"))
            await asyncio.sleep(0.01)
            new_seq = codec.decode_command(held[1])["seq"]
            self.assertEqual(new_seq, (old_seq + 1) % 256)
            led = dict(r=9, g=9, b=9, brightness=9, pattern=1, period_ms=100)
            sim.on_frame(codec.encode_response(old_seq, "LED_GET", "OK", led))      # the late one
            await asyncio.sleep(0.01)
            self.assertFalse(second.done())
            sim.on_frame(codec.encode_response(new_seq, "LED_GET", "OK", dict(led, r=1)))
            self.assertEqual((await second).r, 1)
            del sim._handle
        run(main())

    def test_one_command_at_a_time_and_seq_wraps(self):
        async def main():
            band, sim = await connect()
            seqs = []
            real = sim._handle

            def spy(frame):
                seqs.append(frame[1])
                real(frame)
            sim._handle = spy
            results = await asyncio.gather(*[band.get_name() for _ in range(300)])
            self.assertEqual(set(results), {"Vexo-HACK-42"})
            self.assertEqual(seqs, [(1 + i) % 256 for i in range(300)])
        run(main())

    def test_disconnect_event_and_command_in_flight(self):
        async def main():
            band, sim = await connect()
            reasons = []
            band.on("disconnect", lambda e: reasons.append(e.reason))
            await band.configure(motion=50, audio=True)
            sim._handle = lambda frame: None  # the next command gets no answer...
            waiting = asyncio.ensure_future(band.get_led())
            await asyncio.sleep(0.01)
            sim.drop_link()                   # ...and then the link drops
            async with self.assertVexoError("not-connected"):
                await waiting
            self.assertEqual(reasons, ["lost"])
            self.assertFalse(band.connected)
            self.assertFalse(any(band.streams.values()))
            async with self.assertVexoError("not-connected"):
                await band.set_led("#ff0000")
            await band.disconnect()
            self.assertEqual(reasons, ["lost"])  # only once
        run(main())

    def test_disconnect_reasons(self):
        async def main():
            for action, reason in (("disconnect", "closed"), ("restart", "restart"), ("sleep", "sleep")):
                band, sim = await connect()
                reasons = []
                band.on("disconnect", lambda e: reasons.append(e.reason))
                self.assertIsNone(await getattr(band, action)())
                if action != "disconnect":
                    sim.step(199)
                    self.assertTrue(band.connected)  # the band goes 200 ms after its answer
                    sim.step(1)
                self.assertEqual(reasons, [reason])
                self.assertFalse(band.connected or sim.is_open)
        run(main())

    def test_settings_reset_on_reconnect(self):
        async def main():
            shown = []
            band, sim = await connect(on_led=shown.append)
            await band.set_led("#ff0000", pattern="blink")
            await band.set_motion(rate=100, accel_range=16, gyro_range=250)
            await band.set_events(kinds=["tap"], tap_threshold=2)
            await band.set_mic_gain(0)
            await band.set_tx_power(4)
            await band.configure(motion=True, audio=True, temperature=60, battery=True)
            await band.storage.put(3, "kept")
            await band.disconnect()
            self.assertEqual(shown[-1]["pattern"], 0)  # light off when the link closes

            band = await Band.connect(42, transport=sim)
            self.assertFalse(any(band.streams.values()))
            self.assertEqual((await band.command("STREAM_GET")).active, 0)
            self.assertEqual((await band.get_led()).pattern, "off")
            self.assertEqual(await band.get_motion(), dict(rate=25, sensors="both", accel_range=2, gyro_range=2000))
            self.assertEqual((await band.get_events()).tap_threshold, 0.8)
            self.assertEqual(len((await band.get_events()).kinds), 6)
            self.assertEqual((await band.get_mic()).gain_db, 20.0)
            self.assertEqual((await band.get_radio()).tx_power, -8)
            self.assertEqual(await band.storage.get_text(3), "kept")
            got = []
            band.on("motion", got.append)
            band.on("temperature", got.append)
            sim.step(5000)
            self.assertEqual(got, [])  # nothing streams until asked again
            await band.configure(temperature=True)
            sim.step(5000)
            self.assertEqual(len(got), 1)  # back to one reading every 5 s
        run(main())

    def test_handlers(self):
        async def main():
            band, sim = await connect()
            seen = []

            async def slow(e):
                await asyncio.sleep(0)
                seen.append(("async", e.kind))

            def broken(e):
                raise RuntimeError("student bug")

            async def broken_later(e):
                raise RuntimeError("async student bug")

            band.on("tap", broken)
            band.on("tap", broken_later)
            band.on("tap", slow)
            band.on("tap", lambda e: seen.append(("plain", e.kind)))
            await band.configure(events=True)
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                sim.tap("tap")
                await asyncio.sleep(0.01)
            self.assertEqual(sorted(seen), [("async", "tap"), ("plain", "tap")])
            self.assertIn("student bug", errors.getvalue())
            self.assertIn("async student bug", errors.getvalue())
            self.assertEqual(await band.get_name(), "Vexo-HACK-42")  # the band still works
        run(main())


class UsbSimTest(BandCase):
    def test_bytes_really_pass_through_cobs(self):
        async def main():
            sim = SimTransport(42, realtime=False, via="usb", firmware="1.0")
            wire = []  # every chunk of bytes that crossed the pretend cable
            real = codec.Deframer.push

            def spy(deframer, chunk):
                wire.append(bytes(chunk))
                return real(deframer, chunk)
            codec.Deframer.push = spy
            try:
                band = await Band.connect(42, transport=sim)
                got = []
                band.on("motion", got.append)
                await band.set_led("#000100")  # zeros in the frame: COBS must hide them
                await band.configure(motion=100)
                sim.step(100)
            finally:
                codec.Deframer.push = real
            self.assertEqual(len(got), 10)
            self.assertGreater(len(wire), 8)
            for chunk in wire:
                self.assertEqual(chunk[-1], 0)
                self.assertNotIn(0, chunk[:-1])
            frames = [codec.cobs_decode(chunk[:-1]) for chunk in wire]
            self.assertEqual(codec.decode_command(frames[0])["opcode"], "GET_INFO")
            self.assertEqual(codec.decode_frame(frames[1])["data"]["band_no"], 42)
            self.assertEqual(codec.decode_frame(frames[-1])["frame"], "motion")
            self.assertEqual(band.transport_kind, "sim")
        run(main())

    def test_usb_behaviour(self):
        async def main():
            band, sim = await connect(via="usb")
            radio = await band.get_radio()
            self.assertEqual((radio.rssi, radio.phy, radio.interval_ms, radio.mtu), (None, None, 0, 0))
            # The link stream (signal strength) is Bluetooth only: asking for it on the cable
            # is refused, and nothing else in the same call starts either.
            got = []
            band.on("link", got.append)
            band.on("battery", got.append)
            async with self.assertVexoError("not-available") as caught:
                await band.configure(battery=True, link=True)
            for words in ("link", "only exists over Bluetooth", "USB cable", "leave link out",
                          "Nothing in that configure call was started"):
                self.assertIn(words, str(caught.error))
            sim.step(5000)
            self.assertEqual(got, [])
            self.assertFalse(any(band.streams.values()))
            self.assertEqual((await band.command("STREAM_GET")).active, 0)
            self.assertTrue((await band.configure(battery=True))["battery"])  # without link it works
            self.assertEqual(len(got), 1)
        run(main())


if __name__ == "__main__":
    unittest.main()


class ExpectedCloseTest(BandCase):
    def test_restart_and_sleep_name_the_reason(self):
        async def go(method):
            band = await Band.connect(42, transport=SimTransport(42, realtime=False))
            reasons = []
            band.on("disconnect", lambda d: reasons.append(d.reason))
            await getattr(band, method)()
            band._closed("lost")  # what a real Bluetooth link reports
            return reasons
        for method in ("restart", "sleep"):
            self.assertEqual(run(go(method)), [method])

    def test_wrong_band_carries_both_numbers(self):
        async def go():
            try:
                await Band.connect(42, transport=SimTransport(17, realtime=False))
            except VexoError as e:
                return e.code, e.found, e.wanted
        self.assertEqual(run(go()), ("wrong-band", 17, 42))


class ReviewRegressions(unittest.TestCase):
    """From the independent review."""

    def test_configure_refuses_wrong_types(self):
        async def go():
            band, _sim = await connect()
            for bad in ({"events": "false"}, {"events": 1}, {"battery": None}):
                with self.assertRaises(VexoError) as caught:
                    await band.configure(**bad)
                self.assertEqual(caught.exception.code, "bad-stream")
            self.assertFalse(any(band.streams.values()))
            await band.disconnect()
        run(go())

    def test_configure_twice_at_once_and_after_a_failure(self):
        async def go():
            band, _sim = await connect()
            await asyncio.gather(band.configure(motion=50), band.configure(events=True))
            self.assertEqual(band.streams["motion"], 50)
            self.assertTrue(band.streams["events"])
            with self.assertRaises(VexoError):
                await band.configure(motion=-1)
            await band.configure(battery=True)
            self.assertTrue(band.streams["battery"])
            await band.disconnect()
        run(go())
