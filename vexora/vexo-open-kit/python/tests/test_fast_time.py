"""The time t of fast motion samples (firmware 1.1): never backwards, never the same twice,
and as close to what the band reported as that allows. The frames here are built like a
real band's: the stamp in whole milliseconds, a sensor a few percent fast, the first
quarter second with the nominal period. The same cases as js/test/fast-time.test.js.
"""
import math

from vexo_open import Band, SimTransport, codec

from test_band import run
from test_reconnect import until
from test_v11 import V11Case

SPAN = 2 ** 32


async def connect(**options):
    sim_options = {k: options.pop(k) for k in ("via",) if k in options}
    sim = SimTransport(42, realtime=False, **sim_options)
    band = await Band.connect(42, transport=sim, **options)
    motion = []
    band.on("motion", motion.append)
    return sim, band, motion


def band_frames(rate, count, frames, fast=1.04, jitter=1, start=5000, seed=7):
    """Frames as a band sends them. fast = how much faster than nominal its sensor runs,
    jitter = how far (ms) a stamp can be from the true time of the frame's first sample,
    before it is rounded to a whole millisecond."""
    true_period = 1000 / rate / fast  # ms
    x, out = seed, []
    for k in range(frames):
        x = x * 48271 % 2147483647
        first = k * count * true_period  # ms since the stream started
        out.append(dict(
            t_ms=math.floor(start + first + (x / 2147483647 * 2 - 1) * jitter + 0.5) % SPAN,
            sample_no=k * count,
            period_us=round(1e6 / rate) if first < 250 else round(true_period * 1000),  # nominal, then measured
            sensors=1,
            samples=[dict(ax=k, ay=i, az=1000) for i in range(count)]))
    return out


def send(sim, frames):
    for f in frames:
        sim.on_frame(codec.encode_stream("motion2", f))


def reported(f, i):
    """The band's own time for sample i of a frame (contract: t_ms + i x period_us / 1000)."""
    return f["t_ms"] + i * f["period_us"] / 1000


def back_steps(frames):
    """How many frames start at or before the reported time of the last sample of the frame before."""
    return sum(b["t_ms"] <= reported(a, len(a["samples"]) - 1) for a, b in zip(frames, frames[1:]))


def after(a, b):
    """b - a on the band's counter, which starts again at 0 after 2**32 ms."""
    return (b - a + SPAN / 2) % SPAN - SPAN / 2


SETTINGS = [
    dict(rate=416, count=20, frames=60, jitter=2),   # Bluetooth, accelerometer alone
    dict(rate=833, count=38, frames=60),
    dict(rate=1666, count=38, frames=120),           # USB cable
    dict(rate=416, count=19, frames=60, fast=0.97),  # a slow sensor, both sensors' frame size
    dict(rate=208, count=10, frames=60, jitter=3),
]


class FastTimeTest(V11Case):
    def assertRising(self, motion, what=""):
        for i in range(1, len(motion)):
            a, b = motion[i - 1], motion[i]
            self.assertGreater(after(a.t, b.t), 0, "%s t at sample %d: %r then %r" % (what, i, a.t, b.t))
            self.assertGreater(b.time, a.time, "%s time at sample %d" % (what, i))

    def assertHonest(self, frames, motion, what=""):
        """Every frame against what the band reported for it. motion holds the events of exactly these frames."""
        at, last = 0, None
        for f in frames:
            count = len(f["samples"])
            events = motion[at:at + count]
            at += count
            end = reported(f, count - 1)
            where = "%s frame %d" % (what, f["sample_no"])
            if last is None or f["t_ms"] > last:  # the usual frame: the band's own times, untouched
                self.assertEqual([e.t for e in events], [reported(f, i) for i in range(count)], where)
            else:
                # a frame stamped at or before the last time: never before its own reported time,
                # and it ends where the band says
                for i, e in enumerate(events):
                    self.assertGreaterEqual(e.t, reported(f, i) - 1e-6, where)
                    self.assertLessEqual(e.t, end + 1e-6, where)
                self.assertAlmostEqual(events[-1].t, end, delta=1e-6, msg=where)
            last = events[-1].t
        self.assertEqual(at, len(motion))

    def test_the_reported_times_would_step_backwards_t_and_time_never_do(self):
        async def main():
            for setting in SETTINGS:
                what = "%d Hz" % setting["rate"]
                frames = band_frames(**setting)
                sim, band, motion = await connect()
                send(sim, frames)
                if "fast" not in setting:
                    self.assertGreater(back_steps(frames), 0, what + ": these frames should hold a backwards step")
                self.assertEqual(len(motion), setting["frames"] * setting["count"])
                self.assertRising(motion, what)
                self.assertHonest(frames, motion, what)
                self.assertEqual([e.n for e in motion], list(range(len(motion))))  # no sample lost, none twice
                # time is t on the real clock: the same distance apart, to the microsecond
                offset = motion[0].time - motion[0].t / 1000
                for e in motion:
                    self.assertEqual(e.time, band._real(e.t))
                    self.assertAlmostEqual(e.time - e.t / 1000, offset, delta=1e-6, msg=what)
        run(main())

    def test_the_old_way_of_working_out_t_really_went_backwards_on_these_frames(self):
        frames = band_frames(**SETTINGS[0])
        naive = [reported(f, i) for f in frames for i in range(len(f["samples"]))]
        self.assertTrue(any(b <= a for a, b in zip(naive, naive[1:])))

    def test_a_lost_frame_leaves_the_gap_the_band_reported(self):
        async def main():
            for setting in SETTINGS[:3]:
                everything = band_frames(**setting)
                lost = (7, 8, 30)  # frames that never arrived
                frames = [f for k, f in enumerate(everything) if k not in lost]
                sim, band, motion = await connect()
                send(sim, frames)
                self.assertRising(motion)
                self.assertHonest(frames, motion)
                count = setting["count"]
                before, nxt = motion[7 * count - 1], motion[7 * count]
                self.assertEqual(nxt.n - before.n, 2 * count + 1)  # two frames of samples are missing ...
                self.assertEqual(nxt.t, everything[9]["t_ms"])  # ... and the next sample is where the band stamped it
                # the gap in t is the time that was lost
                self.assertGreater(nxt.t - before.t, 2 * count * everything[9]["period_us"] / 1000 - 3)
        run(main())

    def test_a_stream_that_starts_again_keeps_t_rising(self):
        async def main():
            sim, band, motion = await connect()
            first = band_frames(1666, 38, 20)
            # started again right away: the first stamp is a millisecond before the last time already given
            end = reported(first[-1], 37)
            second = band_frames(1666, 38, 20, start=math.floor(end) - 1, seed=11)
            third = band_frames(416, 20, 20, start=9000, seed=13)  # and once more after a pause
            send(sim, first + second + third)
            self.assertRising(motion)
            self.assertHonest(first + second + third, motion)
            self.assertEqual((motion[20 * 38].n, motion[40 * 38].n), (0, 0))
            self.assertEqual(motion[40 * 38].t, third[0]["t_ms"])  # after a pause: the band's stamp as it is
        run(main())

    def test_t_rises_where_the_band_counter_starts_again_at_0(self):
        async def main():
            sim, band, motion = await connect()
            frames = band_frames(833, 38, 40, start=SPAN - 900)
            self.assertTrue(frames[0]["t_ms"] > SPAN - 2000 and frames[-1]["t_ms"] < 2000)
            send(sim, frames)
            self.assertRising(motion)  # t on the counter, time on the real clock
            self.assertTrue(all(0 <= e.t < SPAN for e in motion))
            self.assertTrue(1.6 < motion[-1].time - motion[0].time < 1.8)
        run(main())

    def test_frames_of_one_sample_with_the_same_stamp(self):
        async def main():
            sim, band, motion = await connect()
            frames = band_frames(1666, 1, 400, jitter=0)
            self.assertTrue(any(a["t_ms"] == b["t_ms"] for a, b in zip(frames, frames[1:])))
            send(sim, frames)
            self.assertRising(motion)
            # nothing else to go by: such a sample follows the one before by 1/8 period,
            # and the next stamp is used again
            for e, f in zip(motion, frames):
                self.assertTrue(0 <= e.t - f["t_ms"] < f["period_us"] / 1000)
        run(main())

    def test_a_stamp_far_behind_is_the_band_counter_starting_again(self):
        async def main():
            sim, band, motion = await connect()
            send(sim, band_frames(416, 20, 5, start=60000))
            again = band_frames(416, 20, 5, start=2000)
            send(sim, again)
            self.assertEqual(motion[100].t, again[0]["t_ms"])
            self.assertRising(motion[100:])
        run(main())

    def test_a_new_link_starts_from_the_stamps_of_the_band_that_answers(self):
        async def main():
            sim, band, motion = await connect(auto_reconnect=True, reconnect_seconds=5)
            band._reconnect_pause = 0.01
            back = []
            band.on("reconnected", back.append)
            send(sim, band_frames(416, 20, 5, start=5000))
            sim.drop_link()
            self.assertTrue(await until(lambda: back))
            frames = band_frames(416, 20, 5, start=5200, jitter=0)  # inside the old run's times
            send(sim, frames)
            self.assertEqual(motion[100].t, frames[0]["t_ms"])
            self.assertRising(motion[100:])
        run(main())

    def test_the_pretend_band_t_and_time_rise_at_every_rate_on_both_links(self):
        async def main():
            for via in ("ble", "usb"):
                sim, band, motion = await connect(via=via)
                limits = await band.motion_limits()
                for rate in [r for r in limits.rates if r <= limits.accel]:
                    await band.configure(motion=rate, sensors="accel")
                    del motion[:]
                    sim.step(3000)
                    self.assertGreater(len(motion), rate * 2.5, (via, rate))
                    self.assertRising(motion, "%s %d" % (via, rate))
                    self.assertEqual([e.n for e in motion], list(range(len(motion))))
                    await band.configure(motion=0)
        run(main())
