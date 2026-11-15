// The time `t` of fast motion samples (firmware 1.1): never backwards, never the same twice,
// and as close to what the band reported as that allows. The frames here are built like a
// real band's: the stamp in whole milliseconds, a sensor a few percent fast, the first
// quarter second with the nominal period.
import test from 'node:test';
import assert from 'node:assert/strict';
import { Band, SimTransport, codec } from '../src/index.js';

const NOW = 1794484800000;              // the computer's clock, pinned
const SPAN = 2 ** 32;

async function connect(simOptions = {}, bandOptions = {}) {
  const sim = new SimTransport(42, { realtime: false, ...simOptions });
  const band = await Band.connect(42, { transport: sim, now: () => NOW, ...bandOptions });
  const motion = [];
  band.on('motion', (e) => motion.push(e));
  return { sim, band, motion };
}

/**
 * Frames as a band sends them. `fast` = how much faster than nominal its sensor runs,
 * `jitter` = how far (ms) a stamp can be from the true time of the frame's first sample,
 * before it is rounded to a whole millisecond. The same numbers as in the Python test.
 */
function bandFrames({ rate, count, frames, fast = 1.04, jitter = 1, start = 5000, seed = 7 }) {
  const truePeriod = 1000 / rate / fast;                    // ms
  let x = seed;
  const random = () => (x = x * 48271 % 2147483647) / 2147483647;
  return Array.from({ length: frames }, (_, k) => {
    const first = k * count * truePeriod;                   // ms since the stream started
    return {
      t_ms: Math.round(start + first + (random() * 2 - 1) * jitter) % SPAN,
      sample_no: k * count,
      period_us: first < 250 ? Math.round(1e6 / rate) : Math.round(truePeriod * 1000),   // nominal, then measured
      sensors: 1,
      samples: Array.from({ length: count }, (_, i) => ({ ax: k, ay: i, az: 1000 })),
    };
  });
}

const send = (sim, frames) => frames.forEach((f) => sim.onFrame(codec.encodeStream('motion2', f)));

/** The band's own time for sample i of a frame (contract: t_ms + i x period_us / 1000). */
const reported = (f, i) => f.t_ms + i * f.period_us / 1000;

/** How many frames start at or before the reported time of the last sample of the frame before. */
const backSteps = (frames) => frames.slice(1).filter((f, k) => f.t_ms <= reported(frames[k], frames[k].samples.length - 1)).length;

/** b - a on the band's counter, which starts again at 0 after 2^32 ms. */
const after = (a, b) => (((b - a) % SPAN) + SPAN + SPAN / 2) % SPAN - SPAN / 2;

function assertRising(motion, what = '') {
  for (let i = 1; i < motion.length; i++) {
    assert.ok(after(motion[i - 1].t, motion[i].t) > 0, `${what} t at sample ${i}: ${motion[i - 1].t} then ${motion[i].t}`);
    assert.ok(motion[i].time > motion[i - 1].time, `${what} time at sample ${i}`);
  }
}

/** Every frame against what the band reported for it. `motion` holds the events of exactly these frames. */
function assertHonest(frames, motion, what = '') {
  let at = 0;
  let last = null;
  for (const f of frames) {
    const events = motion.slice(at, at + f.samples.length);
    at += f.samples.length;
    const end = reported(f, f.samples.length - 1);
    if (last === null || f.t_ms > last) {
      // the usual frame: the band's own times, untouched
      events.forEach((e, i) => assert.equal(e.t, reported(f, i), `${what} frame ${f.sample_no} sample ${i}`));
    } else {
      // a frame stamped at or before the last time: never before its own reported time, and it ends where the band says
      events.forEach((e, i) => {
        assert.ok(e.t >= reported(f, i) - 1e-6, `${what} frame ${f.sample_no} sample ${i} is before the band's time`);
        assert.ok(e.t <= end + 1e-6, `${what} frame ${f.sample_no} sample ${i} is after the frame's last reported time`);
      });
      assert.ok(Math.abs(events.at(-1).t - end) < 1e-6, `${what} frame ${f.sample_no} ends at ${events.at(-1).t}, the band says ${end}`);
    }
    last = events.at(-1).t;
  }
  assert.equal(at, motion.length);
}

const SETTINGS = [
  { rate: 416, count: 20, frames: 60, jitter: 2 },          // Bluetooth, accelerometer alone
  { rate: 833, count: 38, frames: 60 },
  { rate: 1666, count: 38, frames: 120 },                   // USB cable
  { rate: 416, count: 19, frames: 60, fast: 0.97 },         // a slow sensor, both sensors' frame size
  { rate: 208, count: 10, frames: 60, jitter: 3 },
];

test('fast motion: the reported times would step backwards, t and time never do', async () => {
  for (const setting of SETTINGS) {
    const what = `${setting.rate} Hz`;
    const frames = bandFrames(setting);
    const { sim, band, motion } = await connect();
    send(sim, frames);
    if (setting.fast === undefined) assert.ok(backSteps(frames) > 0, `${what}: these frames should hold a backwards step`);
    assert.equal(motion.length, setting.frames * setting.count);
    assertRising(motion, what);
    assertHonest(frames, motion, what);
    assert.deepEqual(motion.map((e) => e.n), motion.map((_, i) => i));          // no sample lost, none twice
    // `time` is `t` on the real clock: the same distance apart, to the microsecond
    for (const e of motion) assert.ok(Math.abs(e.time - band._timeOf(e.t)) === 0);
    const offset = motion[0].time - motion[0].t;
    for (const e of motion) assert.ok(Math.abs(e.time - e.t - offset) < 1e-3, what);
  }
});

test('fast motion: the old way of working out t really went backwards on these frames', () => {
  const frames = bandFrames(SETTINGS[0]);
  const naive = frames.flatMap((f) => f.samples.map((_, i) => reported(f, i)));
  assert.ok(naive.some((t, i) => i > 0 && t <= naive[i - 1]));
});

test('fast motion: a lost frame leaves the gap the band reported, and t goes on rising', async () => {
  for (const setting of SETTINGS.slice(0, 3)) {
    const all = bandFrames(setting);
    const lost = [7, 8, 30];                                // frames that never arrived
    const frames = all.filter((_, k) => !lost.includes(k));
    const { sim, motion } = await connect();
    send(sim, frames);
    assertRising(motion);
    assertHonest(frames, motion);
    const count = setting.count;
    const before = motion[7 * count - 1];
    const next = motion[7 * count];
    assert.equal(next.n - before.n, 2 * count + 1);         // two frames of samples are missing ...
    assert.equal(next.t, all[9].t_ms);                      // ... and the next sample is where the band stamped it
    assert.ok(next.t - before.t > 2 * count * all[9].period_us / 1000 - 3);     // the gap in t is the time that was lost
  }
});

test('fast motion: a stream that starts again (sample numbers from 0) keeps t rising', async () => {
  const { sim, motion } = await connect();
  const first = bandFrames({ rate: 1666, count: 38, frames: 20 });
  // started again right away: the first stamp is a millisecond before the last time already given
  const end = reported(first.at(-1), 37);
  const second = bandFrames({ rate: 1666, count: 38, frames: 20, start: Math.floor(end) - 1, seed: 11 });
  // and once more after a pause
  const third = bandFrames({ rate: 416, count: 20, frames: 20, start: 9000, seed: 13 });
  send(sim, [...first, ...second, ...third]);
  assertRising(motion);
  assertHonest([...first, ...second, ...third], motion);
  assert.equal(motion[20 * 38].n, 0);
  assert.equal(motion[40 * 38].n, 0);
  assert.equal(motion[40 * 38].t, third[0].t_ms);           // after a pause: the band's stamp as it is
});

test('fast motion: t rises across the place where the band counter starts again at 0', async () => {
  const { sim, band, motion } = await connect();
  const frames = bandFrames({ rate: 833, count: 38, frames: 40, start: SPAN - 900 });
  assert.ok(frames[0].t_ms > SPAN - 2000 && frames.at(-1).t_ms < 2000);
  send(sim, frames);
  assertRising(motion);                                     // t on the counter, time on the real clock
  for (const e of motion) assert.ok(e.t >= 0 && e.t < SPAN);
  assert.ok(motion.at(-1).time - motion[0].time > 1600 && motion.at(-1).time - motion[0].time < 1800);
  assert.equal(motion.at(-1).time, band._timeOf(motion.at(-1).t));
});

test('fast motion: frames of one sample with the same stamp', async () => {
  const { sim, motion } = await connect();
  const frames = bandFrames({ rate: 1666, count: 1, frames: 400, jitter: 0 });
  assert.ok(frames.some((f, k) => k > 0 && f.t_ms === frames[k - 1].t_ms));
  send(sim, frames);
  assertRising(motion);
  // nothing else to go by: such a sample follows the one before by 1/8 period, and the next stamp is used again
  motion.forEach((e, k) => assert.ok(e.t - frames[k].t_ms >= 0 && e.t - frames[k].t_ms < frames[k].period_us / 1000, `sample ${k}`));
});

test('fast motion: a stamp far behind is the band counter starting again, and is followed', async () => {
  const { sim, motion } = await connect();
  send(sim, bandFrames({ rate: 416, count: 20, frames: 5, start: 60000 }));
  const again = bandFrames({ rate: 416, count: 20, frames: 5, start: 2000 });
  send(sim, again);
  assert.equal(motion[100].t, again[0].t_ms);
  assertRising(motion.slice(100));
});

test('fast motion: a new link starts from the stamps of the band that answers', async () => {
  const { sim, band, motion } = await connect({}, { autoReconnect: true });
  band._reconnectPauseMs = 5;
  send(sim, bandFrames({ rate: 416, count: 20, frames: 5, start: 5000 }));
  const back = new Promise((resolve) => band.on('reconnected', resolve));
  sim.dropLink();
  await back;
  const frames = bandFrames({ rate: 416, count: 20, frames: 5, start: 5200, jitter: 0 });   // inside the old run's times
  send(sim, frames);
  assert.equal(motion[100].t, frames[0].t_ms);
  assertRising(motion.slice(100));
});

test('fast motion from the pretend band: t and time rise at every rate, on both links', async () => {
  for (const via of ['ble', 'usb']) {
    const { sim, band, motion } = await connect({ via });
    const { rates, accel } = await band.motionLimits();
    for (const rate of rates.filter((r) => r <= accel)) {
      await band.configure({ motion: rate, sensors: 'accel' });
      motion.length = 0;
      sim.step(3000);
      assert.ok(motion.length > rate * 2.5, `${via} ${rate}`);
      assertRising(motion, `${via} ${rate}`);
      assert.deepEqual(motion.map((e) => e.n), motion.map((_, i) => i));
      await band.configure({ motion: 0 });
    }
  }
});
