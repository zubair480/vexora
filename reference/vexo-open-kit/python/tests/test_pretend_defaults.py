"""What the pretend band does when nobody directs it has to be what the pages say, and the
same in Python and JavaScript (js/test/pretend-defaults.test.js is the twin of this file):
a gentle swing every 2 seconds, and a walk at 110 steps a minute.
Also here: band.on() refuses a name that is not an event."""
import asyncio
import math
import unittest

from vexo_open import Band, SimTransport, VexoError
from vexo_open.sync import Band as PlainBand

EVENTS = ["motion", "event", "tap", "double-tap", "free-fall", "tilt", "orientation", "motion-start", "motion-stop",
          "activity", "audio", "battery", "temperature", "link", "mic-level", "notice", "disconnect", "reconnecting",
          "reconnected", "stream-taken"]


def run(main):
    return asyncio.run(main())


async def connect(**options):
    sim = SimTransport(42, realtime=False, **options)
    return await Band.connect(42, transport=sim), sim


class SwingTest(unittest.TestCase):
    def stays_gentle(self, motion, label):
        """The documented bounds: ax, ay within 0.12 g, az 0.96 to 1.04, turning below 30 degrees/second."""
        self.assertGreaterEqual(len(motion), 200, label)
        for m in motion:
            self.assertTrue(abs(m.ax) <= 0.12 and abs(m.ay) <= 0.12, "%s: ax %s, ay %s" % (label, m.ax, m.ay))
            self.assertTrue(0.96 <= m.az <= 1.04, "%s: az %s" % (label, m.az))
            self.assertTrue(abs(m.gx) <= 30 and abs(m.gy) <= 30, "%s: gx %s, gy %s" % (label, m.gx, m.gy))
            self.assertTrue(abs(m.gz) <= 10, "%s: gz %s" % (label, m.gz))
        # ... and it does move: each value gets near its bound, in both directions
        for key, low, high in (("ax", -0.11, 0.11), ("ay", -0.11, 0.11), ("az", 0.965, 1.035),
                               ("gx", -28, 28), ("gy", -28, 28), ("gz", -9, 9)):
            values = [m[key] for m in motion]
            self.assertTrue(min(values) <= low and max(values) >= high,
                            "%s: %s only moved from %s to %s" % (label, key, min(values), max(values)))

    def test_the_made_up_swing_stays_inside_the_documented_bounds(self):
        async def main(firmware):
            band, sim = await connect(firmware=firmware)
            motion = []
            band.on("motion", motion.append)
            await band.configure(motion=50)
            sim.step(6000)
            self.stays_gentle(motion, "firmware " + firmware)
            first = motion[10]                           # it repeats every 2 seconds
            later = next(m for m in motion if abs(m.t - (first.t + 2000)) < 0.5)
            for key in ("ax", "ay", "az", "gx", "gy", "gz"):
                self.assertAlmostEqual(first[key], later[key], delta=0.002 + abs(first[key]) * 0.001, msg=key)
            await band.disconnect()
        for firmware in ("1.0", "1.1"):
            run(lambda: main(firmware))

    def test_the_same_bounds_at_a_fast_rate_and_after_another_sensor_range(self):
        async def main():
            band, sim = await connect()
            motion = []
            band.on("motion", motion.append)
            await band.set_motion(accel_range=16, gyro_range=250)
            await band.configure(motion=416)
            sim.step(4200)
            self.stays_gentle(motion, "416 a second")
            await band.disconnect()
        run(main)

    def test_sample_by_sample_the_same_formula_as_the_javascript_pretend_band(self):
        async def main():
            band, sim = await connect(firmware="1.0")
            motion = []
            band.on("motion", motion.append)
            await band.configure(motion=100)
            sim.step(2500)
            self.assertGreaterEqual(len(motion), 200)
            for m in motion:
                angle = 2 * math.pi * m.t / 2000
                for got, want, step in ((m.ax, 0.12 * math.sin(angle), 0.0011), (m.ay, 0.12 * math.cos(angle), 0.0011),
                                        (m.az, 1 + 0.04 * math.sin(3 * angle), 0.0011),
                                        (m.gx, 30 * math.cos(angle), 0.11), (m.gy, -30 * math.sin(angle), 0.11),
                                        (m.gz, 10 * math.sin(2 * angle), 0.11)):
                    self.assertAlmostEqual(got, want, delta=step, msg="t %s" % m.t)
            await band.disconnect()
        run(main)

    def test_left_alone_it_walks_at_110_steps_a_minute(self):
        async def main(firmware):
            band, sim = await connect(firmware=firmware)
            activity = []
            band.on("activity", activity.append)
            await band.configure(activity=True)
            sim.step(3500)
            self.assertEqual([(a.state, a.cadence, a.steps) for a in activity],
                             [("walk", 110, 1202), ("walk", 110, 1204), ("walk", 110, 1206)], firmware)
            await band.disconnect()
        for firmware in ("1.0", "1.1"):
            run(lambda: main(firmware))


class EventNameTest(unittest.TestCase):
    def bad_event(self, call, name):
        with self.assertRaises(VexoError) as caught:
            call()
        self.assertEqual(caught.exception.code, "bad-event")
        text = str(caught.exception)
        self.assertTrue(text.startswith('There is no event called "%s".' % name), text)
        self.assertTrue(text.endswith("Events: %s." % ", ".join(EVENTS)), text)
        return text

    def test_on_takes_every_event_the_library_sends(self):
        async def main():
            band, _sim = await connect()
            for name in EVENTS:
                handler = band.on(name, lambda e: None)
                band.off(name, handler)
            await band.disconnect()
        run(main)

    def test_a_name_that_is_not_an_event_raises_bad_event_and_lists_the_events(self):
        async def main():
            band, sim = await connect()
            seen = []
            for name in ("tapp", "taps", "Tap", "shake", "", "configure"):
                self.bad_event(lambda: band.on(name, seen.append), name)
                self.bad_event(lambda: band.on(name), name)      # the decorator form: at once, not when it is used
            self.bad_event(lambda: band.on(None, seen.append), "None")
            for name, meant in (("mic_level", "mic-level"), ("micLevel", "mic-level"), ("double_tap", "double-tap"),
                                ("events", "event"), ("free_fall", "free-fall")):
                self.assertIn('Did you mean "%s"?' % meant, self.bad_event(lambda: band.on(name, seen.append), name))
            self.assertNotIn("Did you mean", self.bad_event(lambda: band.on("tapp", seen.append), "tapp"))
            await band.configure(events=True)                    # nothing was registered, and the band still works
            sim.tap("tap")
            self.assertEqual(seen, [])
            await band.disconnect()
        run(main)

    def test_off_refuses_a_wrong_name_too_and_still_stops_a_real_handler(self):
        async def main():
            band, sim = await connect(auto_tap=False)
            taps = []
            on_tap = taps.append
            band.on("tap", on_tap)
            self.bad_event(lambda: band.off("tapp", on_tap), "tapp")   # as on(); the real handler stays
            await band.configure(events=True)
            sim.tap("tap")
            self.assertEqual(len(taps), 1)
            band.off("tap", on_tap)
            band.off("tap", on_tap)                              # twice: fine
            band.off("motion", print)                            # a handler that was never added: fine
            sim.tap("tap")
            self.assertEqual(len(taps), 1)
            await band.disconnect()
        run(main)

    def test_the_plain_band_refuses_them_too_in_both_forms(self):
        band = PlainBand.connect(42, transport=SimTransport(42, auto_tap=False))
        try:
            taps = []
            self.bad_event(lambda: band.on("tapp", taps.append), "tapp")
            self.bad_event(lambda: band.on("tapp"), "tapp")          # @band.on("tapp") fails on that line
            self.bad_event(lambda: band.off("tapp", taps.append), "tapp")   # off() refuses it too
            self.assertIn('Did you mean "mic-level"?', self.bad_event(lambda: band.on("mic_level", taps.append), "mic_level"))

            @band.on("tap")
            def on_tap(event):
                taps.append(event.kind)
            band.configure(events=True)
            band.transport.tap("tap")
            band.wait(0.2)
            self.assertEqual(taps, ["tap"])
            band.off("tap", on_tap)
            band.transport.tap("tap")
            band.wait(0.2)
            self.assertEqual(taps, ["tap"])
        finally:
            band.disconnect()


if __name__ == "__main__":
    unittest.main()
