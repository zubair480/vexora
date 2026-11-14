"""Contract v1.1: fast motion (MOTION2), seconds between readings, and the clock.

All against the pretend band, because no real band has firmware 1.1 yet. The last
class checks the other side: nothing of v1.1 is ever sent to a band with firmware 1.0.
"""
import unittest

from vexo_open import Band, SimTransport, codec
from vexo_open import band as band_module

from test_band import BandCase, plain, run
from test_reconnect import until

NOW = 1794484800.123  # the computer's clock in these tests: pinned, so times can be compared exactly
V11_OPCODES = {"TIME_SET", "TIME_GET", "STREAM_SET_INTERVAL", "STREAM_GET_INTERVAL", "MOTION2_SET", "MOTION2_GET"}
FAST_BIT = 0x100


class Clock:
    """Stands in for the computer's clock. clock.now can be moved by a test."""

    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


class V11Case(BandCase):
    def setUp(self):
        self.clock = Clock()
        self._real_clock = band_module.computer_time
        band_module.computer_time = self.clock

    def tearDown(self):
        band_module.computer_time = self._real_clock


def log(sim):
    """Record every command the pretend band receives from now on, decoded."""
    seen, real = [], sim._handle

    def handle(frame):
        seen.append(codec.decode_command(frame))
        real(frame)
    sim._handle = handle
    return seen


def names(seen):
    return [c["opcode"] for c in seen]


def frames_of(band, kind="motion2"):
    """Collect the decoded stream frames of one kind as they arrive (before Band splits them)."""
    got, real = [], band.transport.on_frame

    def on_frame(data):
        f = codec.decode_frame(data)
        if f and f["frame"] == kind:
            got.append(f)
        real(data)
    band.transport.on_frame = on_frame
    return got


async def connect(number=42, sim=None, **options):
    sim_options = {k: options.pop(k) for k in ("via", "firmware") if k in options}
    sim = sim or SimTransport(number, realtime=False, **sim_options)
    return await Band.connect(number, transport=sim, **options), sim


class VersionTest(V11Case):
    def test_info_and_features(self):
        async def main():
            band, sim = await connect()
            self.assertEqual((band.info.protocol, band.info.firmware), ("1.1", "1.1.0"))
            self.assertEqual(band.info.features, ["fast-motion", "intervals", "clock"])
            self.assertEqual(sorted(band.streams), sorted(
                ["motion", "events", "activity", "audio", "battery", "temperature", "link", "mic_level"]))
            old, _ = await connect(firmware="1.0")
            self.assertEqual((old.info.protocol, old.info.firmware, old.info.features), ("1.0", "1.0.0", []))
        run(main())

    def test_a_later_minor_version_counts_as_1_1(self):
        async def main():
            band, sim = await connect()
            real = sim._do_get_info
            sim._do_get_info = lambda a: ("OK", dict(real(a)[1], proto_minor=2, fw_minor=2))
            await band.get_info()
            self.assertEqual((band.info.protocol, band.info.features), ("1.2", ["fast-motion", "intervals", "clock"]))
            sim._do_get_info = lambda a: ("OK", dict(real(a)[1], proto_major=2))
            await band.get_info()
            self.assertEqual((band.info.protocol, band.info.features), ("2.1", []))
        run(main())

    def test_protocol_1_0_escape_hatch(self):
        async def main():
            sim = SimTransport(42, realtime=False)
            seen = log(sim)
            band = await Band.connect(42, transport=sim, protocol="1.0")
            self.assertEqual((band.info.protocol, band.info.features, band.clock), ("1.1", [], "computer"))
            got = []
            band.on("motion", got.append)
            await band.configure(motion=50, temperature=3, battery=True)
            sim.step(1000)
            self.assertEqual(len(got), 50)
            self.assertIsNone(got[0].n)  # the old motion stream
            self.assertEqual(sim.active, 0b110001)
            self.assertEqual(await band.motion_limits(),
                             dict(rates=[10, 25, 50, 100], accel=None, gyro=None, both=100, transport="sim"))
            self.assertEqual((await band.time()).source, "computer")
            async with self.assertVexoError("not-available") as caught:
                await band.configure(motion=416)
            self.assertIn('protocol="1.0"', str(caught.error))
            for call in (lambda: band.set_time(), lambda: band.set_interval("battery", 60),
                         lambda: band.get_interval("battery"), lambda: band.configure(sensors="accel"),
                         lambda: band.configure(battery=60)):
                async with self.assertVexoError("not-available"):
                    await call()
            self.assertFalse(V11_OPCODES & set(names(seen)))
            self.assertEqual(names(seen), ["GET_INFO", "IMU_GET", "IMU_SET", "TEMP_SET_INTERVAL", "STREAM_START",
                                           "GET_INFO"])
            # The student's own raw commands are passed through.
            self.assertEqual((await band.command("TIME_GET")).set, 0)
            for bad in ("1.1", "2.0", 1.0, True):
                async with self.assertVexoError("bad-arg"):
                    await Band.connect(42, transport=SimTransport(42, realtime=False), protocol=bad)
            async with self.assertVexoError("bad-arg"):
                await Band.connect(42, transport=SimTransport(42, realtime=False), set_clock="no")
        run(main())


class FastMotionTest(V11Case):
    def test_motion_uses_the_fast_stream_and_decodes_per_sample(self):
        async def main():
            band, sim = await connect()
            seen = log(sim)
            got = []
            band.on("motion", got.append)
            streams = await band.configure(motion=True)
            self.assertEqual(names(seen), ["MOTION2_GET", "STREAM_START"])  # the limits are asked once
            self.assertEqual(seen[-1]["args"], dict(mask=FAST_BIT))
            self.assertEqual((streams["motion"], sim.active), (25, FAST_BIT))  # 25 at first
            sim.step(1000)
            self.assertEqual(len(got), 25)
            self.assertEqual([m.n for m in got[:3]], [0, 1, 2])
            self.assertEqual([m.t for m in got[:3]], [0.0, 40.0, 80.0])
            self.assertEqual(list(got[0]), ["t", "ax", "ay", "az", "gx", "gy", "gz", "n", "time"])
            self.assertTrue(0.9 < got[5].az < 1.1 and got[5].gx is not None)
            self.assertEqual([round(m.time - NOW, 6) for m in got[:2]], [0, 0.04])

            del seen[:], got[:]
            await band.configure(motion=416, sensors="accel")
            self.assertEqual(names(seen), ["MOTION2_SET"])  # already streaming: no new start
            self.assertEqual(seen[0]["args"], dict(rate_hz=416, sensors=1))
            self.assertEqual(band.streams["motion"], 416)
            sim.step(1000)
            self.assertTrue(400 <= len(got) <= 416, len(got))  # whole frames of 20
            self.assertEqual([m.n for m in got], list(range(len(got))))  # counts from 0 again, none lost
            self.assertTrue(all(m.gx is None and m.gy is None and m.gz is None for m in got))
            self.assertTrue(all(isinstance(m.ax, float) for m in got))
            period = got[1].t - got[0].t
            self.assertTrue(2.40 < period < 2.45 and period != int(period), period)  # sub-millisecond
            self.assertAlmostEqual(got[7].time - got[6].time, period / 1000, places=6)

            del got[:]
            await band.configure(sensors="gyro")  # sensors alone: the rate and on/off stay
            self.assertEqual((band.streams["motion"], sim.motion2), (416, dict(rate_hz=416, sensors=2)))
            sim.step(100)
            self.assertTrue(got and all(m.ax is None and m.az is None and m.gx is not None for m in got))

            await band.configure(motion=False)
            self.assertEqual((band.streams["motion"], sim.active), (0, 0))
            await band.configure(sensors="both")  # while off: only the setting changes
            self.assertEqual((band.streams["motion"], sim.active, sim.motion2["sensors"]), (0, 0, 3))
            await band.configure(motion=True)
            self.assertEqual(band.streams["motion"], 416)  # the rate was kept
        run(main())

    def test_a_known_frame(self):
        async def main():
            band, sim = await connect()
            got = []
            band.on("motion", got.append)
            sim.on_frame(bytes.fromhex("1840e20100400300006409000001030a00ecffe8030b00ebffe90380c1803e0100"))
            self.assertEqual(plain(got), [
                dict(t=123456.0, ax=0.01, ay=-0.02, az=1.0, gx=None, gy=None, gz=None, n=832),
                dict(t=123458.404, ax=0.011, ay=-0.021, az=1.001, gx=None, gy=None, gz=None, n=833),
                dict(t=123460.808, ax=-16.0, ay=16.0, az=0.001, gx=None, gy=None, gz=None, n=834)])
            self.assertEqual([round(m.time - NOW, 6) for m in got], [123.456, 123.458404, 123.460808])
            del got[:]
            sim.on_frame(codec.encode_stream("motion2", dict(
                t_ms=10, sample_no=0xFFFFFFFF, period_us=1201, sensors=2,
                samples=[dict(gx=125, gy=-2000, gz=0), dict(gx=1, gy=2, gz=3)])))
            self.assertEqual(plain(got), [
                dict(t=10.0, ax=None, ay=None, az=None, gx=12.5, gy=-200.0, gz=0.0, n=0xFFFFFFFF),
                dict(t=11.201, ax=None, ay=None, az=None, gx=0.1, gy=0.2, gz=0.3, n=0)])  # n wraps like the band's
            # A jump in n is the number of samples lost.
            sim.on_frame(codec.encode_stream("motion2", dict(
                t_ms=50, sample_no=40, period_us=1201, sensors=1, samples=[dict(ax=1, ay=2, az=3)])))
            self.assertEqual(got[-1].n - got[-2].n - 1, 39)
        run(main())

    def test_limits_and_fastest(self):
        async def main():
            band, sim = await connect()
            seen = log(sim)
            self.assertEqual(await band.motion_limits(), dict(
                rates=[10, 25, 50, 100, 208, 416, 833, 1666], accel=833, gyro=833, both=416, transport="sim"))
            await band.motion_limits()
            self.assertEqual(names(seen), ["MOTION2_GET"])  # asked once per link
            self.assertEqual((await band.configure(motion="fastest"))["motion"], 416)  # both sensors at first
            self.assertEqual((await band.configure(motion="fastest", sensors="accel"))["motion"], 833)
            self.assertEqual((await band.set_motion(rate="fastest", sensors="both")).rate, 416)

            cable, usb = await connect(via="usb")
            self.assertEqual(await cable.motion_limits(), dict(
                rates=[10, 25, 50, 100, 208, 416, 833, 1666], accel=1666, gyro=833, both=833, transport="sim"))
            self.assertEqual((await cable.configure(motion="fastest"))["motion"], 833)
            self.assertEqual((await cable.configure(motion="fastest", sensors="accel"))["motion"], 1666)
            self.assertEqual((await cable.configure(motion="fastest", sensors="gyro"))["motion"], 833)
            self.assertEqual(usb.motion2, dict(rate_hz=833, sensors=2))

            old, _ = await connect(firmware="1.0")
            self.assertEqual((await old.configure(motion="fastest"))["motion"], 100)
            self.assertEqual((await old.set_motion(rate="fastest")).rate, 100)
        run(main())

    def test_over_the_cap_is_denied_with_the_way_out(self):
        async def main():
            band, sim = await connect()
            await band.configure(motion=100, events=True)
            seen = log(sim)
            before = (dict(sim.motion2), sim.active)
            async with self.assertVexoError("denied") as caught:
                await band.configure(motion=833, sensors="both", activity=True)
            self.assertEqual(str(caught.error), (
                "833 samples per second with both sensors is too much for Bluetooth here: the most is 416 with "
                "both, 833 with accel alone, 833 with gyro alone. Use sensors=\"accel\", or sensors=\"gyro\", or "
                "the USB cable (up to 833 with both, 1666 with accel alone), or a lower rate. Nothing was changed."))
            self.assertEqual(((dict(sim.motion2), sim.active), seen), (before, []))  # checked before sending
            async with self.assertVexoError("denied") as caught:
                await band.configure(motion=1666, sensors="accel")
            self.assertIn("Use the USB cable (up to 833 with both, 1666 with accel alone), or a lower rate",
                          str(caught.error))
            await band.configure(motion=833, sensors="accel")
            async with self.assertVexoError("denied"):
                await band.configure(sensors="both")  # 833 is kept, and that is too much with both
            async with self.assertVexoError("denied"):
                await band.set_motion(sensors="both")
            self.assertEqual(sim.motion2, dict(rate_hz=833, sensors=1))

            cable, usb = await connect(via="usb")
            async with self.assertVexoError("denied") as caught:
                await cable.configure(motion=1666, sensors="both")
            self.assertEqual(str(caught.error), (
                "1666 samples per second with both sensors is too much for the USB cable: the most is 833 with "
                "both, 1666 with accel alone, 833 with gyro alone. Use sensors=\"accel\", or a lower rate. "
                "Nothing was changed."))
        run(main())

    def test_the_band_saying_denied_gets_the_same_message(self):
        async def main():
            # The band is the judge. If its limits were lower than it said, its DENIED is explained too.
            for refused in ("MOTION2_SET", "STREAM_START"):
                band, sim = await connect()
                await band.configure(activity=True)
                setattr(sim, "_do_" + refused.lower(), lambda a: "DENIED")
                async with self.assertVexoError("denied") as caught:
                    await band.configure(motion=416, sensors="accel", events=True)
                self.assertIn("416 samples per second with accel alone is too much for Bluetooth here",
                              str(caught.error))
                self.assertEqual((caught.error.status, sim.active), ("DENIED", 0b100))  # nothing started
                self.assertEqual(band.streams["motion"], 0)
            # A raw command gets a plain sentence.
            band, sim = await connect()
            await band.command("MOTION2_SET", rate_hz=833, sensors=1)
            await band.command("STREAM_START", mask=FAST_BIT)
            async with self.assertVexoError("denied") as caught:
                await band.command("MOTION2_SET", rate_hz=833, sensors=3)
            self.assertIn("motion_limits()", str(caught.error))
        run(main())

    def test_a_rate_that_does_not_exist(self):
        async def main():
            band, sim = await connect()
            seen = log(sim)
            for rate in (30, 1, 200, 3330, 12.5, "50", "fast"):
                async with self.assertVexoError("bad-arg") as caught:
                    await band.configure(motion=rate)
                self.assertIn("10, 25, 50, 100, 208, 416, 833 or 1666", str(caught.error))
                async with self.assertVexoError("bad-arg"):
                    await band.set_motion(rate=rate)
            for sensors in ("accelerometer", 1, True):
                async with self.assertVexoError("bad-arg") as caught:
                    await band.configure(motion=50, sensors=sensors)
                self.assertIn('"accel", "gyro" or "both"', str(caught.error))
            async with self.assertVexoError("bad-arg"):
                await band.set_motion(sensors="all")
            self.assertEqual((await band.configure(motion=50, sensors=None))["motion"], 50)  # None = left out
            self.assertEqual(names(seen), ["MOTION2_GET", "MOTION2_SET", "STREAM_START"])
        run(main())

    def test_set_and_get_motion(self):
        async def main():
            band, sim = await connect()
            self.assertEqual(await band.get_motion(), dict(rate=25, sensors="both", accel_range=2, gyro_range=2000))
            seen = log(sim)
            self.assertEqual(await band.set_motion(rate=208, sensors="gyro", accel_range=8, gyro_range=500),
                             dict(rate=208, sensors="gyro", accel_range=8, gyro_range=500))
            self.assertEqual(names(seen), ["MOTION2_SET", "IMU_SET"])  # the ranges are shared by both streams
            self.assertEqual(seen[1]["args"], dict(rate_hz=25, accel_range_g=8, gyro_range_dps=500))
            self.assertEqual((sim.motion2, sim.imu["rate_hz"]), (dict(rate_hz=208, sensors=2), 25))
            self.assertEqual(await band.set_motion(gyro_range=0),
                             dict(rate=208, sensors="gyro", accel_range=8, gyro_range=0))
            got = []
            band.on("motion", got.append)
            await band.configure(motion=True, sensors="both")
            sim.step(200)
            self.assertTrue(got and all(m.gx == m.gy == m.gz == 0 for m in got))  # gyro range 0: zeros
            for bad in (dict(accel_range=3), dict(gyro_range=100)):
                async with self.assertVexoError("bad-arg"):
                    await band.set_motion(**bad)
        run(main())

    def test_the_old_stream_stays_reachable_and_both_can_run(self):
        async def main():
            band, sim = await connect()
            got = []
            band.on("motion", got.append)
            await band.command("STREAM_START", mask=1)
            self.assertEqual(band.streams["motion"], 25)
            sim.step(400)
            self.assertEqual((len(got), got[0].n), (10, None))
            await band.configure(motion=50)  # the fast stream next to it
            self.assertEqual((sim.active, band.streams["motion"]), (FAST_BIT | 1, 50))
            del got[:]
            sim.step(400)
            self.assertEqual(len([m for m in got if m.n is None]), 10)
            self.assertEqual(len([m for m in got if m.n is not None]), 20)
            await band.configure(motion=False)  # off means off: both are stopped
            self.assertEqual((sim.active, band.streams["motion"]), (0, 0))
        run(main())

    def test_firmware_1_0_says_what_needs_1_1(self):
        async def main():
            band, sim = await connect(firmware="1.0")
            seen = log(sim)
            async with self.assertVexoError("not-available") as caught:
                await band.configure(motion=416)
            self.assertEqual(str(caught.error), "This band's firmware (1.0.0) can do 10, 25, 50 or 100 samples per "
                             "second with both sensors. 416 per second needs firmware 1.1.")
            async with self.assertVexoError("not-available") as caught:
                await band.configure(motion=50, sensors="accel")
            self.assertIn("One sensor alone needs firmware 1.1.", str(caught.error))
            async with self.assertVexoError("not-available") as caught:
                await band.set_motion(rate=1666, sensors="gyro")
            self.assertIn("1666 per second, or one sensor alone, needs firmware 1.1.", str(caught.error))
            async with self.assertVexoError("bad-arg"):
                await band.configure(motion=30)
            self.assertEqual(seen, [])
            self.assertEqual((await band.configure(motion=50, sensors="both"))["motion"], 50)  # "both" is what 1.0 does
            self.assertEqual((await band.set_motion(sensors="both")).sensors, "both")
        run(main())


class IntervalTest(V11Case):
    def test_configure_with_seconds(self):
        async def main():
            band, sim = await connect()
            seen = log(sim)
            got = {}
            for name in ("activity", "battery", "temperature", "link", "mic-level"):
                band.on(name, lambda m, name=name: got.setdefault(name, []).append(m))
            streams = await band.configure(activity=0.1, battery=60, temperature=0.5, link=2, mic_level=0.02)
            self.assertEqual([(c["opcode"], c["args"]) for c in seen], [
                ("STREAM_SET_INTERVAL", dict(stream=2, interval_ms=100)),
                ("STREAM_SET_INTERVAL", dict(stream=4, interval_ms=60000)),
                ("STREAM_SET_INTERVAL", dict(stream=5, interval_ms=500)),
                ("STREAM_SET_INTERVAL", dict(stream=6, interval_ms=2000)),
                ("STREAM_SET_INTERVAL", dict(stream=7, interval_ms=20)),
                ("STREAM_START", dict(mask=0b11110100))])
            self.assertEqual({k for k, v in streams.items() if v},
                             {"activity", "battery", "temperature", "link", "mic_level"})  # a number turns it on
            sim.step(120000)
            self.assertEqual({k: len(v) for k, v in got.items()},
                             {"activity": 1200, "battery": 3, "temperature": 240, "link": 60, "mic-level": 6000})
            # True and False do not touch the interval; 0 is off.
            del seen[:]
            await band.configure(activity=0, battery=False, temperature=True)
            self.assertEqual(names(seen), ["STREAM_STOP"])
            self.assertEqual((band.streams["activity"], band.streams["battery"], sim.intervals[4]),
                             (False, False, 60000))
        run(main())

    def test_a_value_the_band_does_not_allow(self):
        async def main():
            band, sim = await connect()
            cases = [(dict(battery=0.5), '"battery" can come every 1 to 3600 seconds, got 0.5. Nothing was changed.'),
                     (dict(mic_level=0.03), '"mic_level" can come every 0.02 to 10 seconds, in steps of 0.02, '
                                            'got 0.03. Nothing was changed.'),
                     (dict(activity=0.05), '"activity" can come every 0.1 to 3600 seconds, got 0.05. '
                                           'Nothing was changed.'),
                     (dict(temperature=4000), '"temperature" can come every 0.1 to 3600 seconds, got 4000. '
                                              'Nothing was changed.'),
                     (dict(link=1e9), '"link" can come every 0.1 to 3600 seconds')]
            for args, text in cases:
                async with self.assertVexoError("bad-arg") as caught:
                    await band.configure(events=True, **args)
                self.assertIn(text, str(caught.error))
                self.assertEqual(caught.error.status, "BAD_ARG")
            self.assertEqual((sim.active, sim.intervals), (0, {2: 1000, 4: 0, 5: 5000, 6: 1000, 7: 100}))
            for bad in (-1, "5", float("nan"), [1]):
                async with self.assertVexoError("bad-stream"):
                    await band.configure(battery=bad)
                async with self.assertVexoError("bad-arg"):
                    await band.configure(temperature=bad)
            for key in ("events", "audio"):
                async with self.assertVexoError("bad-stream"):
                    await band.configure(**{key: 5})
        run(main())

    def test_set_interval_and_get_interval(self):
        async def main():
            band, sim = await connect()
            self.assertEqual(await band.get_interval("battery"),
                             dict(stream="battery", seconds=0.0, min=1.0, max=3600.0))
            self.assertEqual(await band.get_interval("mic_level"),
                             dict(stream="mic_level", seconds=0.1, min=0.02, max=10.0))
            self.assertEqual(await band.set_interval("battery", 60),
                             dict(stream="battery", seconds=60.0, min=1.0, max=3600.0))
            self.assertEqual(await band.set_interval("temperature", 0.25),
                             dict(stream="temperature", seconds=0.25, min=0.1, max=3600.0))
            self.assertFalse(any(band.streams.values()))  # it does not turn anything on
            self.assertEqual((await band.set_interval("battery", 0)).seconds, 0)  # only when it changes
            async with self.assertVexoError("bad-arg") as caught:
                await band.set_interval("battery", 0.5)
            self.assertEqual(str(caught.error), '"battery" can come every 1 to 3600 seconds (or 0 = only when it '
                             'changes), got 0.5. Nothing was changed.')
            async with self.assertVexoError("bad-arg") as caught:
                await band.set_interval("mic_level", 0.03)
            self.assertIn("every 0.02 to 10 seconds, in steps of 0.02, got 0.03", str(caught.error))
            async with self.assertVexoError("bad-arg"):
                await band.set_interval("link", 0)
            for stream in ("motion", "events", "audio"):
                async with self.assertVexoError("bad-arg") as caught:
                    await band.set_interval(stream, 1)
                self.assertIn("has no interval", str(caught.error))
                async with self.assertVexoError("bad-arg"):
                    await band.get_interval(stream)
            for call in (band.set_interval("heartbeat", 1), band.get_interval("mic-level")):
                async with self.assertVexoError("bad-stream"):
                    await call
            for bad in (-1, "5", None, True):
                async with self.assertVexoError("bad-arg"):
                    await band.set_interval("link", bad)
        run(main())

    def test_firmware_1_0(self):
        async def main():
            band, sim = await connect(firmware="1.0")
            seen = log(sim)
            for key in ("activity", "battery", "link", "mic_level"):
                async with self.assertVexoError("not-available") as caught:
                    await band.configure(**{key: 2})
                self.assertIn("needs band firmware 1.1; this band has 1.0.0. Use True for the standard rate.",
                              str(caught.error))
                async with self.assertVexoError("not-available"):
                    await band.set_interval(key, 2)
            for key in ("activity", "battery", "temperature", "link", "mic_level"):
                async with self.assertVexoError("not-available"):
                    await band.get_interval(key)
            self.assertEqual(seen, [])
            # Temperature keeps what it could always do: whole seconds, 1..255.
            self.assertEqual(await band.set_interval("temperature", 3),
                             dict(stream="temperature", seconds=3.0, min=1.0, max=255.0))
            self.assertEqual((seen[0]["opcode"], seen[0]["args"]), ("TEMP_SET_INTERVAL", dict(seconds=3)))
            for bad in (0.5, 0, 256, "3"):
                async with self.assertVexoError("bad-arg"):
                    await band.set_interval("temperature", bad)
        run(main())


class ClockTest(V11Case):
    def test_connect_sets_the_clock(self):
        async def main():
            sim = SimTransport(42, realtime=False)
            sim.now_ms = 5000.0
            seen = log(sim)
            band = await Band.connect(42, transport=sim)
            self.assertEqual([(c["opcode"], c["args"]) for c in seen],
                             [("GET_INFO", {}), ("TIME_SET", dict(unix_ms=1794484800123))])
            self.assertEqual((band.clock, band._anchor, sim.clock_offset),
                             ("band", (1794484800123, 5000), 1794484795123))
            got = []
            for name in ("tap", "activity", "temperature", "link", "mic-level", "motion"):
                band.on(name, got.append)
            await band.configure(events=True)
            self.clock.now += 99  # the computer's clock no longer matters: the band's clock counts
            sim.step(250)
            sim.tap("tap")
            self.assertEqual((got[0].t, got[0].time), (5250, NOW + 0.25))
            await band.configure(activity=True, temperature=1, link=True, mic_level=True, motion=10)
            del got[:]
            sim.step(1000)
            self.assertEqual(len(got), 23)  # 1 activity, 1 temperature, 1 link, 10 mic level, 10 motion
            for m in got:
                self.assertAlmostEqual(m.time, NOW + (m.t - 5000) / 1000, places=6)
        run(main())

    def test_the_wrap_of_the_band_counter(self):
        async def main():
            sim = SimTransport(42, realtime=False)
            sim.now_ms = 2.0 ** 32 - 1000  # one second before the band's counter starts again at 0
            band = await Band.connect(42, transport=sim)
            self.assertEqual(band._anchor[1], 2 ** 32 - 1000)
            got = []
            band.on("tap", got.append)
            await band.configure(events=True)
            sim.tap("tap")
            sim.step(900)
            sim.tap("tap")
            sim.step(200)  # past the wrap
            sim.tap("tap")
            sim.step(3600000)
            sim.tap("tap")
            self.assertEqual([e.t for e in got], [2 ** 32 - 1000, 2 ** 32 - 100, 100, 3600100])
            self.assertEqual([round(e.time - NOW, 3) for e in got], [0.0, 0.9, 1.1, 3601.1])
            # A frame stamped before the anchor (it waited in a queue) lands in the past.
            del got[:]
            sim.on_frame(codec.encode_stream("event", dict(t_ms=2 ** 32 - 1500, event=1, arg=0)))
            self.assertEqual(got[0].time, NOW - 0.5)
            # The same rule, straight: before and after an anchor on either side of the wrap.
            for anchor_t, t, seconds in ((100, 2 ** 32 - 400, -0.5), (100, 99, -0.001), (100, 100, 0), (100, 600, 0.5),
                                         (2 ** 32 - 1, 0, 0.001), (0, 2 ** 32 - 1, -0.001),
                                         (7, 7 + 2 ** 31 - 1, 2147483.647), (7, 7 + 2 ** 31, -2147483.648)):
                band._anchor = (1000000000000, anchor_t)
                self.assertAlmostEqual(band._real(t), 1000000000 + seconds, places=6, msg=(anchor_t, t))
            self.assertEqual(band._real(600, 0.25), 1000000000 - 2147483.648 + 2147484.24125)
        run(main())

    def test_fast_motion_time_has_the_part_below_a_millisecond(self):
        async def main():
            band, sim = await connect()
            got = []
            band.on("motion", got.append)
            await band.configure(motion=833, sensors="accel")
            sim.step(500)
            step_us = round(1e6 * (got[1].time - got[0].time))
            self.assertEqual(step_us, 1210)  # the measured period, not 1000000 / 833
            for m in got:
                self.assertAlmostEqual(m.time, NOW + m.t / 1000, places=6)
            self.assertEqual(len({m.time for m in got}), len(got))  # every sample has its own time
        run(main())

    def test_time_and_set_time(self):
        async def main():
            band, sim = await connect()
            sim.step(2000)
            self.assertEqual(await band.time(), dict(time=NOW + 2, set=True, t=2000, source="band"))
            seen = log(sim)
            self.clock.now = NOW + 50
            self.assertEqual(await band.set_time(), dict(time=NOW + 50, set=True, t=2000, source="band"))
            self.assertEqual((seen[0]["opcode"], seen[0]["args"]), ("TIME_SET", dict(unix_ms=1794484850123)))
            self.assertEqual(band._anchor, (1794484850123, 2000))
            self.assertEqual(await band.set_time(1700000000.5),
                             dict(time=1700000000.5, set=True, t=2000, source="band"))
            got = []
            band.on("tap", got.append)
            await band.configure(events=True)
            sim.step(1000)
            sim.tap("tap")
            self.assertEqual(got[0].time, 1700000001.5)
            for bad in (5, 1577836799, 4102444800, 1794484800123):  # 1970, just too early, 2100, milliseconds
                async with self.assertVexoError("bad-arg") as caught:
                    await band.set_time(bad)
                self.assertIn("seconds since 1970, like time.time()", str(caught.error))
            for bad in (-1, "now", True, float("inf")):
                async with self.assertVexoError("bad-arg"):
                    await band.set_time(bad)
            self.assertEqual((await band.time()).time, 1700000001.5)  # nothing was changed
            # Someone else set the band's clock meanwhile: time() notices and the events follow.
            sim.clock_offset += 10000
            self.assertEqual((await band.time()).time, 1700000011.5)
            sim.tap("tap")
            self.assertEqual(got[-1].time, 1700000011.5)
        run(main())

    def test_set_clock_false(self):
        async def main():
            sim = SimTransport(42, realtime=False)
            sim.now_ms = 700.0
            seen = log(sim)
            band = await Band.connect(42, transport=sim, set_clock=False)
            self.assertEqual(names(seen), ["GET_INFO", "TIME_GET"])
            self.assertEqual((band.clock, sim.clock_offset), ("computer", None))  # the band's clock is not set
            self.assertEqual(await band.time(), dict(time=NOW, set=False, t=700, source="computer"))
            got = []
            band.on("tap", got.append)
            await band.configure(events=True)
            sim.step(300)
            sim.tap("tap")
            self.assertAlmostEqual(got[0].time, NOW + 0.3, places=6)  # our clock at the answer + band time since
            await band.disconnect()

            setter = await Band.connect(42, transport=sim)  # another program sets the clock...
            await setter.disconnect()
            self.clock.now = NOW + 500  # ...and this computer's clock is off
            del seen[:]
            band = await Band.connect(42, transport=sim, set_clock=False)
            self.assertEqual((names(seen), band.clock), (["GET_INFO", "TIME_GET"], "band"))
            self.assertEqual((await band.time()).source, "band")
            band.on("tap", got.append)
            await band.configure(events=True)
            sim.tap("tap")
            self.assertEqual(got[-1].time, NOW)  # the band's clock, not this computer's
        run(main())

    def test_firmware_1_0_estimates_from_the_computer(self):
        async def main():
            sim = SimTransport(42, realtime=False, firmware="1.0")
            sim.now_ms = 60000.0
            band = await Band.connect(42, transport=sim)
            self.assertEqual((band.clock, band._anchor), ("computer", (NOW * 1000, 60000)))
            got = []
            band.on("motion", got.append)
            band.on("tap", got.append)
            await band.configure(motion=100, events=True)
            sim.step(100)
            sim.tap("tap")
            self.assertEqual([round(m.time - NOW, 6) for m in got], [i / 100 for i in range(11)])
            self.clock.now = NOW + 7
            sim.step(1000)
            self.assertEqual(await band.time(), dict(time=NOW + 7, set=False, t=61100, source="computer"))
            async with self.assertVexoError("not-available") as caught:
                await band.set_time()
            self.assertIn("needs band firmware 1.1; this band has 1.0.0", str(caught.error))
        run(main())

    def test_a_refused_time_set_does_not_stop_connect(self):
        async def main():
            self.clock.now = 12.5  # a computer that thinks it is 1970
            band, sim = await connect()
            self.assertEqual((band.clock, sim.clock_offset, band.connected), ("computer", None, True))
            self.assertEqual(await band.time(), dict(time=12.5, set=False, t=0, source="computer"))
        run(main())

    def test_events_without_band_time(self):
        async def main():
            band, sim = await connect()
            got = {}
            for name in ("battery", "notice", "audio"):
                band.on(name, lambda m, name=name: got.setdefault(name, []).append(m))
            await band.configure(battery=True)
            self.clock.now = NOW + 3
            sim.set_battery(14)
            self.assertEqual([m.time for m in got["battery"]], [NOW, NOW + 3])  # when they arrived
            self.assertEqual([(m.kind, m.time) for m in got["notice"]], [("low-battery", NOW + 3)])
            self.assertEqual(sorted(got["battery"][0]), ["charging", "millivolts", "percent", "state", "time"])

            # Audio: the first frame after a start gets its arrival time, then 20 ms per step of seq.
            await band.configure(audio=True)
            self.clock.now = NOW + 10
            for i, seq in enumerate((65534, 65535, 0, 2)):  # seq wraps; one frame (1) was lost
                self.clock.now += 0.5  # frames arrive in bursts: the arrival time of later ones is not used
                sim.on_frame(codec.encode_stream("audio", dict(seq=seq, opus=b"\xbb")))
            self.assertEqual([round(m.time - NOW, 6) for m in got["audio"]], [10.5, 10.52, 10.54, 10.58])
            del got["audio"][:]
            await band.configure(audio=False)
            await band.configure(audio=True)  # a new start counts from its own first frame
            self.clock.now = NOW + 100
            sim.step(60)
            self.assertEqual([(m.seq, round(m.time - NOW, 6)) for m in got["audio"]],
                             [(0, 100), (1, 100.02), (2, 100.04)])
            # More than 65536 frames (22 minutes): the time keeps counting up.
            band._audio_clock = [NOW, 65535, 65535]
            sim.on_frame(codec.encode_stream("audio", dict(seq=0, opus=b"\xbb")))
            self.assertAlmostEqual(got["audio"][-1].time, NOW + 65536 * 0.02, places=6)
        run(main())


class ReconnectV11Test(V11Case):
    async def start(self, **options):
        sim = SimTransport(42, realtime=False)
        band = await Band.connect(42, transport=sim, auto_reconnect=True, reconnect_seconds=5, **options)
        band._reconnect_pause = 0.01
        events = []
        for name in ("reconnecting", "reconnected", "disconnect"):
            band.on(name, lambda e, name=name: events.append((name, e)))
        return band, sim, events

    def test_everything_of_1_1_comes_back(self):
        async def main():
            band, sim, events = await self.start()
            await band.set_led("#00ff00")
            await band.set_motion(gyro_range=500)
            await band.configure(motion=416, sensors="accel", battery=60, mic_level=0.02, temperature=30, events=True)
            await band.configure(temperature=0.5)  # the last value counts
            await band.set_interval("link", 5)      # set, though the stream is off
            sim.step(3000)
            seen = log(sim)
            self.clock.now = NOW + 20
            sim.drop_link()
            self.assertEqual((sim.motion2, sim.active, sim.intervals[4]), (dict(rate_hz=25, sensors=3), 0, 0))
            self.assertTrue(await until(lambda: len(events) == 2))
            self.assertEqual((events[1][0], events[1][1].failed), ("reconnected", []))
            self.assertEqual([(c["opcode"], c["args"]) for c in seen], [
                ("GET_INFO", {}),
                ("TIME_SET", dict(unix_ms=1794484820123)),
                ("IMU_SET", dict(rate_hz=25, accel_range_g=2, gyro_range_dps=500)),
                ("LED_SET", dict(r=0, g=255, b=0, brightness=255, pattern=1, period_ms=1000)),
                ("MOTION2_SET", dict(rate_hz=416, sensors=1)),
                ("STREAM_SET_INTERVAL", dict(stream=4, interval_ms=60000)),
                ("STREAM_SET_INTERVAL", dict(stream=5, interval_ms=500)),
                ("STREAM_SET_INTERVAL", dict(stream=6, interval_ms=5000)),
                ("STREAM_SET_INTERVAL", dict(stream=7, interval_ms=20)),
                ("STREAM_START", dict(mask=FAST_BIT | 0b10110010))])
            self.assertEqual((sim.motion2, sim.active), (dict(rate_hz=416, sensors=1), FAST_BIT | 0b10110010))
            self.assertEqual(sim.intervals, {2: 1000, 4: 60000, 5: 500, 6: 5000, 7: 20})
            self.assertEqual((band.streams["motion"], band.streams["mic_level"], band.clock), (416, True, "band"))
            self.assertEqual(band._anchor, (1794484820123, 3000))
            got = []
            band.on("motion", got.append)
            sim.step(100)
            self.assertEqual((got[0].n, got[0].gx), (0, None))
            self.assertAlmostEqual(got[0].time, NOW + 20, places=6)
            await band.disconnect()
        run(main())

    def test_set_clock_false_asks_again_and_the_band_kept_its_clock(self):
        async def main():
            band, sim, events = await self.start(set_clock=False)
            await band.command("TIME_SET", unix_ms=1700000000000)  # the student's own business
            self.assertEqual(band.clock, "band")
            sim.step(4000)
            seen = log(sim)
            sim.drop_link()
            self.assertTrue(await until(lambda: len(events) == 2))
            self.assertEqual(names(seen), ["GET_INFO", "TIME_GET"])
            self.assertEqual((band.clock, band._anchor), ("band", (1700000004000, 4000)))  # kept across the drop
            await band.disconnect()
        run(main())

    def test_the_band_comes_back_with_firmware_1_0(self):
        async def main():
            band, sim, events = await self.start()
            await band.configure(motion=208, battery=60, events=True)
            seen = log(sim)
            sim.firmware = "1.0"
            sim.drop_link()
            self.assertTrue(await until(lambda: len(events) == 2))
            self.assertEqual(events[1][1].failed, ["MOTION2_SET", "STREAM_SET_INTERVAL", "STREAM_START"])
            self.assertEqual([(c["opcode"], c["args"]) for c in seen],
                             [("GET_INFO", {}), ("STREAM_START", dict(mask=0b10010))])  # what 1.0 can do
            self.assertEqual((band.info.features, band.clock, band.streams["motion"], band.streams["battery"]),
                             ([], "computer", 0, True))
            await band.disconnect()
        run(main())

    def test_a_refused_clock_is_reported(self):
        async def main():
            band, sim, events = await self.start()
            sim._do_time_set = lambda a: "BAD_ARG"
            sim.drop_link()
            self.assertTrue(await until(lambda: len(events) == 2))
            self.assertEqual((events[1][1].failed, band.clock), (["TIME_SET"], "computer"))
            await band.disconnect()
        run(main())


class SimV11Test(V11Case):
    """The pretend band itself, through raw commands: it must answer like the contract says."""

    def test_caps_by_link_and_refusals(self):
        async def main():
            band, sim = await connect()
            self.assertEqual(await band.command("MOTION2_GET"), dict(
                rate_hz=25, sensors=3, max_accel_hz=833, max_gyro_hz=833, max_both_hz=416))
            cable, usb = await connect(via="usb")
            self.assertEqual(await cable.command("MOTION2_GET"), dict(
                rate_hz=25, sensors=3, max_accel_hz=1666, max_gyro_hz=833, max_both_hz=833))
            for bad in (dict(rate_hz=30, sensors=3), dict(rate_hz=0, sensors=1), dict(rate_hz=3330, sensors=1),
                        dict(rate_hz=100, sensors=0), dict(rate_hz=100, sensors=4)):
                async with self.assertVexoError("bad-arg"):
                    await band.command("MOTION2_SET", **bad)
            # Not streaming: any allowed value is accepted, also one this link cannot stream.
            self.assertEqual((await band.command("MOTION2_SET", rate_hz=1666, sensors=3)).rate_hz, 1666)
            for mask in (FAST_BIT, FAST_BIT | 0b10):
                async with self.assertVexoError("denied"):
                    await band.command("STREAM_START", mask=mask)
                self.assertEqual((sim.active, sim._timers.get("motion2")), (0, None))  # nothing started
            await band.command("MOTION2_SET", rate_hz=833, sensors=1)
            self.assertEqual((await band.command("STREAM_START", mask=FAST_BIT)).active, FAST_BIT)
            for over in (dict(rate_hz=833, sensors=3), dict(rate_hz=1666, sensors=1)):
                async with self.assertVexoError("denied"):  # streaming: over the cap is refused
                    await band.command("MOTION2_SET", **over)
            self.assertEqual(sim.motion2, dict(rate_hz=833, sensors=1))
            await band.command("MOTION2_SET", rate_hz=833, sensors=2)  # at the cap is fine
            # Bits above 8 do not exist.
            for mask in (0x200, 0x8001, 0x300):
                async with self.assertVexoError("not-available"):
                    await band.command("STREAM_START", mask=mask)
            self.assertEqual(sim.active, FAST_BIT)
            # The cable allows what Bluetooth does not.
            await cable.command("MOTION2_SET", rate_hz=1666, sensors=1)
            await cable.command("STREAM_START", mask=FAST_BIT)
            async with self.assertVexoError("denied"):
                await cable.command("MOTION2_SET", rate_hz=1666, sensors=2)
            # Settings go back to the defaults when the link closes.
            await band.disconnect()
            band = await Band.connect(42, transport=sim)
            self.assertEqual((await band.command("MOTION2_GET")).rate_hz, 25)
        run(main())

    def test_frames(self):
        async def main():
            band, sim = await connect(via="usb")
            frames = frames_of(band)
            expect = {  # rate: (samples per frame with one sensor, with both)
                10: (1, 1), 25: (1, 1), 50: (2, 2), 100: (5, 5), 208: (10, 10), 416: (20, 19), 833: (38, 19),
                1666: (38, None)}
            for rate, counts in expect.items():
                for sensors, count in ((1, counts[0]), (2, counts[0]), (3, counts[1])):
                    if count is None or (rate == 1666 and sensors == 2):
                        continue
                    await band.command("STREAM_STOP", mask=FAST_BIT)
                    await band.command("MOTION2_SET", rate_hz=rate, sensors=sensors)
                    await band.command("STREAM_START", mask=FAST_BIT)
                    del frames[:]
                    sim.step(1000)
                    what = (rate, sensors)
                    self.assertTrue(all(len(f["samples"]) == count for f in frames), what)
                    self.assertEqual(frames[0]["sample_no"], 0, what)  # from 0 at every start
                    self.assertEqual([f["sample_no"] for f in frames], [i * count for i in range(len(frames))], what)
                    self.assertEqual(sorted(frames[0]["samples"][0]),
                                     {1: ["ax", "ay", "az"], 2: ["gx", "gy", "gz"],
                                      3: ["ax", "ay", "az", "gx", "gy", "gz"]}[sensors], what)
                    size = 15 + count * (12 if sensors == 3 else 6)
                    self.assertLessEqual(size, codec.MAX_FRAME, what)
                    period = frames[0]["period_us"]
                    if rate <= 100:
                        self.assertEqual(period, 1000000 // rate, what)  # exact on the millisecond grid
                    else:  # measured: near the nominal value, but not it
                        self.assertNotEqual(period, round(1000000 / rate), what)
                        self.assertLess(abs(period * rate / 1000000 - 1), 0.03, what)
                    total = sum(len(f["samples"]) for f in frames)
                    self.assertLess(abs(total - 1000000 / period), count + 1, what)
                    for a, b in zip(frames, frames[1:]):  # t_ms follows the measured period
                        self.assertLessEqual(abs(b["t_ms"] - a["t_ms"] - count * period / 1000), 1, what)
        run(main())

    def test_assuming_the_nominal_rate_drifts(self):
        async def main():
            band, sim = await connect()
            got = []
            band.on("motion", got.append)
            await band.configure(motion=416, sensors="accel")
            sim.step(10000)
            last = got[-1]
            wrong = got[0].t + last.n * 1000 / 416  # what a program would get from "416 a second"
            self.assertGreater(abs(wrong - last.t), 50)  # tens of milliseconds off after 10 seconds
            self.assertLess(abs(last.t - 10000), 60)     # t itself is right
        run(main())

    def test_sample_no_restarts_only_when_the_stream_changes(self):
        async def main():
            band, sim = await connect()
            frames = frames_of(band)
            await band.command("STREAM_START", mask=FAST_BIT)
            sim.step(200)
            self.assertEqual([f["sample_no"] for f in frames], [0, 1, 2, 3, 4])
            await band.command("MOTION2_SET", rate_hz=25, sensors=3)  # the same setting: keeps counting
            sim.step(80)
            self.assertEqual(frames[-1]["sample_no"], 6)
            await band.command("MOTION2_SET", rate_hz=50, sensors=3)
            sim.step(200)
            self.assertEqual([f["sample_no"] for f in frames[7:]], [0, 2, 4, 6, 8])
            await band.command("STREAM_START", mask=FAST_BIT)  # already running: nothing restarts
            sim.step(40)
            self.assertEqual(frames[-1]["sample_no"], 10)
            await band.command("STREAM_STOP", mask=FAST_BIT)
            await band.command("STREAM_START", mask=FAST_BIT)
            sim.step(40)
            self.assertEqual(frames[-1]["sample_no"], 0)
        run(main())

    def test_interval_limits(self):
        async def main():
            band, sim = await connect()
            limits = {2: (100, 3600000, 1000), 4: (1000, 3600000, 0), 5: (100, 3600000, 5000),
                      6: (100, 3600000, 1000), 7: (20, 10000, 100)}
            for stream, (low, high, default) in limits.items():
                self.assertEqual(await band.command("STREAM_GET_INTERVAL", stream=stream),
                                 dict(stream=stream, interval_ms=default, min_ms=low, max_ms=high))
                for ms in (low, high):
                    self.assertEqual(await band.command("STREAM_SET_INTERVAL", stream=stream, interval_ms=ms),
                                     dict(stream=stream, interval_ms=ms, min_ms=low, max_ms=high))
                for ms in (low - 1, high + 1, 0xFFFFFFFF) + ((0,) if stream != 4 else ()):
                    async with self.assertVexoError("bad-arg"):
                        await band.command("STREAM_SET_INTERVAL", stream=stream, interval_ms=ms)
                self.assertEqual(sim.intervals[stream], high)  # a refused value changes nothing
            for ms in (30, 50, 110):  # the mic level counts in steps of 20 ms
                async with self.assertVexoError("bad-arg"):
                    await band.command("STREAM_SET_INTERVAL", stream=7, interval_ms=ms)
            self.assertEqual((await band.command("STREAM_SET_INTERVAL", stream=4, interval_ms=0)).interval_ms, 0)
            for stream in (0, 1, 3, 8, 9, 255):
                async with self.assertVexoError("bad-arg"):
                    await band.command("STREAM_SET_INTERVAL", stream=stream, interval_ms=1000)
                async with self.assertVexoError("bad-arg"):
                    await band.command("STREAM_GET_INTERVAL", stream=stream)
            # Back to the defaults when the link closes.
            await band.disconnect()
            band = await Band.connect(42, transport=sim)
            self.assertEqual((await band.command("STREAM_GET_INTERVAL", stream=7)).interval_ms, 100)
        run(main())

    def test_intervals_change_the_streams(self):
        async def main():
            band, sim = await connect()
            got = {}
            for name in ("battery", "temperature", "mic-level"):
                band.on(name, lambda m, name=name: got.setdefault(name, []).append(m))
            await band.configure(battery=True, temperature=True, mic_level=True)
            sim.step(10000)
            self.assertEqual({k: len(v) for k, v in got.items()}, {"battery": 1, "temperature": 2, "mic-level": 100})
            # Battery with an interval: one frame every interval, changed or not; no extra one on a change.
            await band.command("STREAM_SET_INTERVAL", stream=4, interval_ms=2000)
            del got["battery"][:]
            sim.step(3000)
            sim.set_battery(50)
            self.assertEqual(len(got["battery"]), 1)
            sim.step(7000)
            self.assertEqual([m.percent for m in got["battery"]], [87, 50, 50, 50, 50])
            await band.command("STREAM_SET_INTERVAL", stream=4, interval_ms=0)  # back to "when it changes"
            del got["battery"][:]
            sim.step(10000)
            sim.set_battery(49)
            self.assertEqual([m.percent for m in got["battery"]], [49])
            # The new interval counts from the moment it is set, also while the stream runs.
            await band.command("STREAM_SET_INTERVAL", stream=7, interval_ms=1000)
            del got["mic-level"][:]
            sim.step(3500)
            self.assertEqual(len(got["mic-level"]), 3)
            # TEMP_SET_INTERVAL and stream 5 are one setting.
            await band.command("TEMP_SET_INTERVAL", seconds=2)
            self.assertEqual((await band.command("STREAM_GET_INTERVAL", stream=5)).interval_ms, 2000)
            await band.command("STREAM_SET_INTERVAL", stream=5, interval_ms=250)
            self.assertEqual(sim.temp_seconds, 0.25)
            del got["temperature"][:]
            sim.step(1000)
            self.assertEqual(len(got["temperature"]), 4)
            # A stream started after its interval was set uses it from the start.
            await band.configure(battery=False)
            await band.command("STREAM_SET_INTERVAL", stream=4, interval_ms=1000)
            del got["battery"][:]
            await band.configure(battery=True)
            sim.step(2000)
            self.assertEqual(len(got["battery"]), 3)  # one at start, then one a second
        run(main())

    def test_clock(self):
        async def main():
            sim = SimTransport(42, realtime=False)
            band = await Band.connect(42, transport=sim, set_clock=False)
            self.assertEqual(await band.command("TIME_GET"), dict(set=0, unix_ms=0, t_ms=0))
            for bad in (0, 1794484800, 1577836799999, 4102444800000, 1794484800123000, 2 ** 64 - 1):
                async with self.assertVexoError("bad-arg"):  # 1970, seconds, just too early, 2100, microseconds
                    await band.command("TIME_SET", unix_ms=bad)
            self.assertEqual((await band.command("TIME_GET")).set, 0)
            sim.step(1500)
            self.assertEqual(await band.command("TIME_SET", unix_ms=1577836800000),
                             dict(set=1, unix_ms=1577836800000, t_ms=1500))
            self.assertEqual((await band.command("TIME_SET", unix_ms=4102444799999)).unix_ms, 4102444799999)
            await band.command("TIME_SET", unix_ms=1794484800123)
            sim.step(2500)
            self.assertEqual(await band.command("TIME_GET"), dict(set=1, unix_ms=1794484802623, t_ms=4000))
            self.assertEqual((await band.get_info()).uptime_ms, 4000)  # the same counter
            # Kept across a dropped link and for the next client; time goes on meanwhile.
            sim.drop_link()
            sim.now_ms += 6000
            other = await Band.connect(42, transport=sim, set_clock=False)
            self.assertEqual(await other.command("TIME_GET"), dict(set=1, unix_ms=1794484808623, t_ms=10000))
            self.assertEqual(other.clock, "band")
            # Lost when the band restarts.
            await other.restart()
            sim.step(200)
            self.assertFalse(sim.is_open)
            again = await Band.connect(42, transport=sim, set_clock=False)
            self.assertEqual(((await again.command("TIME_GET")).set, again.clock), (0, "computer"))
        run(main())

    def test_firmware_1_0_mode(self):
        async def main():
            band, sim = await connect(firmware="1.0")
            info = await band.command("GET_INFO")
            self.assertEqual((info.proto_major, info.proto_minor, info.fw_major, info.fw_minor, info.fw_patch),
                             (1, 0, 1, 0, 0))
            args = dict(TIME_SET=dict(unix_ms=1794484800123), STREAM_SET_INTERVAL=dict(stream=5, interval_ms=1000),
                        STREAM_GET_INTERVAL=dict(stream=5), MOTION2_SET=dict(rate_hz=25, sensors=3))
            for name in sorted(V11_OPCODES):
                async with self.assertVexoError("unknown-opcode"):
                    await band.command(name, **args.get(name, {}))
            self.assertEqual(sim.clock_offset, None)
            for mask in (FAST_BIT, FAST_BIT | 1):
                async with self.assertVexoError("not-available"):
                    await band.command("STREAM_START", mask=mask)
            self.assertEqual((sim.active, sim._timers.get("motion"), sim._timers.get("motion2")), (0, None, None))
            self.assertEqual((await band.command("STREAM_STOP", mask=FAST_BIT)).active, 0)  # unknown bits are ignored
            for bad in ("2.0", "1", 1.0, None):
                with self.assertRaises(Exception):
                    SimTransport(42, firmware=bad)
        run(main())


class NothingNewForAnOldBand(V11Case):
    """The friendly calls never send a v1.1 opcode or stream bit 8 to a band with firmware
    1.0, and what they do send is byte for byte what the library sent before v1.1."""

    def test_frame_log(self):
        async def main():
            sim = SimTransport(42, realtime=False, firmware="1.0")
            sent, real_send = [], sim.send

            async def send(frame):
                sent.append(bytes(frame))
                await real_send(frame)
            sim.send = send
            band = await Band.connect(42, transport=sim, auto_reconnect=True, reconnect_seconds=5)
            band._reconnect_pause = 0.01
            events = []
            band.on("reconnected", events.append)
            got = []
            band.on("motion", got.append)

            await band.get_info()
            await band.configure(motion=50, events=True, activity=True, audio=True, battery=True, temperature=True,
                                 link=True, mic_level=True)
            await band.configure(motion="fastest", temperature=7)
            await band.configure(motion=True, sensors="both")
            await band.time()
            await band.motion_limits()
            await band.set_motion(rate=25, accel_range=4)
            await band.get_motion()
            await band.set_interval("temperature", 9)
            sim.step(500)
            self.assertTrue(got and all(m.n is None for m in got))
            refused = [lambda: band.configure(motion=416), lambda: band.configure(sensors="accel"),
                       lambda: band.configure(battery=60), lambda: band.set_interval("battery", 60),
                       lambda: band.get_interval("temperature"), lambda: band.set_time(),
                       lambda: band.set_motion(rate=1666), lambda: band.set_motion(sensors="gyro")]
            for call in refused:
                async with self.assertVexoError("not-available"):
                    await call()
            sim.drop_link()
            self.assertTrue(await until(lambda: len(events) == 1))
            self.assertEqual(events[0].failed, [])
            await band.configure(motion=False, audio=False, mic_level=False)
            await band.set_led("#00ff00", brightness=0.5)
            await band.disconnect()

            # What today's library (before v1.1) sends for the same calls, in order.
            expect = [
                ("GET_INFO", {}),                                           # connect
                ("GET_INFO", {}),                                           # get_info
                ("IMU_GET", {}),                                            # configure(motion=50, ...)
                ("IMU_SET", dict(rate_hz=50, accel_range_g=2, gyro_range_dps=2000)),
                ("STREAM_START", dict(mask=0xFF)),
                ("IMU_SET", dict(rate_hz=100, accel_range_g=2, gyro_range_dps=2000)),  # "fastest" on 1.0
                ("TEMP_SET_INTERVAL", dict(seconds=7)),
                ("GET_INFO", {}),                                           # time()
                ("IMU_SET", dict(rate_hz=25, accel_range_g=4, gyro_range_dps=2000)),   # set_motion
                ("IMU_GET", {}),                                            # get_motion
                ("TEMP_SET_INTERVAL", dict(seconds=9)),                     # set_interval("temperature", 9)
                ("GET_INFO", {}),                                           # the reconnect
                ("IMU_SET", dict(rate_hz=25, accel_range_g=4, gyro_range_dps=2000)),
                ("TEMP_SET_INTERVAL", dict(seconds=9)),
                ("STREAM_START", dict(mask=0xFF)),
                ("STREAM_STOP", dict(mask=0b10001001)),
                ("LED_SET", dict(r=0, g=255, b=0, brightness=128, pattern=1, period_ms=1000)),
            ]
            self.assertEqual([f.hex() for f in sent],
                             [codec.encode_command(seq, name, args).hex() for seq, (name, args) in enumerate(expect)])
            # Spelled out for the first ones, so this does not lean on the codec alone.
            self.assertEqual([f.hex() for f in sent[:5]],
                             ["010001", "010101", "010221", "0103203202d007", "010410ff00"])
            for frame in sent:
                self.assertNotIn(frame[2], (0x07, 0x08, 0x13, 0x14, 0x24, 0x25), frame.hex())
                if frame[2] == 0x10:
                    self.assertEqual(frame[4] & 0x01, 0, frame.hex())  # no STREAM_START with bit 8
        run(main())

    def test_every_friendly_call_on_firmware_1_0(self):
        async def main():
            for options in (dict(firmware="1.0"), dict(protocol="1.0")):  # an old band, and the escape hatch
                band, sim = await connect(**options)
                seen = log(sim)
                await band.configure(motion=10, events=True, activity=True, audio=True, battery=True,
                                     temperature=5, link=True, mic_level=True)
                await band.configure(**{key: False for key in band.streams})
                await band.set_events(kinds=["tap"])
                await band.set_mic_gain(3)
                await band.set_tx_power(0)
                await band.get_radio()
                await band.get_battery()
                await band.get_name()
                await band.time()
                await band.motion_limits()
                await band.storage.info()
                await band.led_off()
                self.assertFalse(V11_OPCODES & set(names(seen)), options)
                self.assertFalse([c for c in seen if c["opcode"] == "STREAM_START" and c["args"]["mask"] & FAST_BIT])
                self.assertTrue(len(seen) > 12)
        run(main())


class Gyro1666Test(V11Case):
    """Contract 5.2: 1666 Hz never together with the gyroscope."""

    def test_whichever_motion_stream_comes_second_is_refused(self):
        async def main():
            band, sim = await connect(via="usb")
            gyro_on = dict(rate_hz=25, accel_range_g=2, gyro_range_dps=2000)
            gyro_off = dict(gyro_on, gyro_range_dps=0)
            words = "1666 samples per second never runs together with the gyroscope"

            # MOTION2 runs at 1666 (accelerometer alone, the only way): MOTION, which runs
            # the gyroscope, may not start
            await band.command("MOTION2_SET", rate_hz=1666, sensors=1)
            await band.command("STREAM_START", mask=FAST_BIT)
            async with self.assertVexoError("denied") as caught:
                await band.command("STREAM_START", mask=1)
            self.assertIn(words, str(caught.error))
            async with self.assertVexoError("denied"):
                await band.command("STREAM_START", mask=0b101)
            self.assertEqual(sim.active, FAST_BIT)  # nothing in that mask started
            self.assertEqual((await band.command("STREAM_START", mask=0b100)).active, FAST_BIT | 0b100)
            await band.command("STREAM_STOP", mask=0b100)  # (other streams are free)
            # ... unless the gyroscope is off (range 0): then MOTION starts, and the gyroscope
            # cannot be switched on
            self.assertEqual(await band.command("IMU_SET", **gyro_off), gyro_off)
            self.assertEqual((await band.command("STREAM_START", mask=1)).active, FAST_BIT | 1)
            async with self.assertVexoError("denied") as caught:
                await band.command("IMU_SET", **gyro_on)
            self.assertIn(words, str(caught.error))
            self.assertEqual(sim.imu, gyro_off)  # nothing changed
            other = dict(gyro_off, rate_hz=50, accel_range_g=8)
            self.assertEqual(await band.command("IMU_SET", **other), other)
            # a slower MOTION2 frees the gyroscope again
            await band.command("MOTION2_SET", rate_hz=833, sensors=1)
            self.assertEqual(await band.command("IMU_SET", **gyro_on), gyro_on)
            # ... and now MOTION runs the gyroscope, so MOTION2 cannot go back to 1666 while it streams
            async with self.assertVexoError("denied"):
                await band.command("MOTION2_SET", rate_hz=1666, sensors=1)
            self.assertEqual((sim.motion2, sim.active), (dict(rate_hz=833, sensors=1), FAST_BIT | 1))

            # the other order: MOTION streams with the gyroscope on, MOTION2 is off
            await band.command("STREAM_STOP", mask=FAST_BIT)
            await band.command("MOTION2_SET", rate_hz=1666, sensors=1)  # not streaming: any setting is taken
            async with self.assertVexoError("denied"):
                await band.command("STREAM_START", mask=FAST_BIT)
            self.assertEqual(sim.active, 1)
            # both in one start
            await band.command("STREAM_STOP", mask=1)
            for mask in (FAST_BIT | 1, FAST_BIT | 0b11):
                async with self.assertVexoError("denied"):
                    await band.command("STREAM_START", mask=mask)
            self.assertEqual(sim.active, 0)
            # with the gyroscope off both run, in either order and in one start
            await band.command("IMU_SET", **gyro_off)
            self.assertEqual((await band.command("STREAM_START", mask=FAST_BIT | 1)).active, FAST_BIT | 1)
            await band.command("STREAM_STOP", mask=FAST_BIT | 1)
            await band.command("STREAM_START", mask=1)
            self.assertEqual((await band.command("STREAM_START", mask=FAST_BIT)).active, FAST_BIT | 1)
            # 1666 with the gyroscope in MOTION2 itself stays what it was: over the cap
            async with self.assertVexoError("denied"):
                await band.command("MOTION2_SET", rate_hz=1666, sensors=3)
            # below 1666 nothing of this applies
            await band.command("STREAM_STOP", mask=FAST_BIT | 1)
            await band.command("IMU_SET", **gyro_on)
            await band.command("MOTION2_SET", rate_hz=833, sensors=3)
            self.assertEqual((await band.command("STREAM_START", mask=FAST_BIT | 1)).active, FAST_BIT | 1)

            # Bluetooth never carries 1666, so there the cap answers first
            radio, ble = await connect()
            await radio.command("STREAM_START", mask=1)
            await radio.command("MOTION2_SET", rate_hz=1666, sensors=1)
            async with self.assertVexoError("denied"):
                await radio.command("STREAM_START", mask=FAST_BIT)
            self.assertEqual(ble.active, 1)
        run(main())

    def test_the_library_never_runs_the_two_motion_streams_side_by_side(self):
        async def main():
            band, sim = await connect(via="usb")
            seen = log(sim)
            await band.configure(motion="fastest", sensors="accel")
            self.assertEqual(band.streams["motion"], 1666)
            self.assertEqual(sim.active, FAST_BIT)
            await band.set_motion(gyro_range=500)  # the gyroscope's range is free: MOTION is not running
            await band.configure(motion=833, sensors="both")
            self.assertEqual(band.streams["motion"], 833)
            await band.configure(motion=1666, sensors="accel")
            self.assertEqual(band.streams["motion"], 1666)
            self.assertFalse([c for c in seen if c["opcode"] == "STREAM_START" and c["args"]["mask"] & 1])

            # a refusal for this reason, if the band gives one, is explained as what it is
            sim._do_stream_start = lambda a: "DENIED"
            await band.configure(motion=0)
            async with self.assertVexoError("denied") as caught:
                await band.configure(motion=True)
            self.assertIn("never runs together with the gyroscope", str(caught.error))
        run(main())


if __name__ == "__main__":
    unittest.main()
