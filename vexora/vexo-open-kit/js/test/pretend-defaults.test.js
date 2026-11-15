// What the pretend band does when nobody directs it has to be what the pages say, and the
// same in JavaScript and Python (python/tests/test_pretend_defaults.py is the twin of this
// file): a gentle swing every 2 seconds, and a walk at 110 steps a minute.
// Also here: band.on() refuses a name that is not an event.
import test from 'node:test';
import assert from 'node:assert/strict';
import { Band, SimTransport, VexoError } from '../src/index.js';

async function connect(options = {}) {
  const sim = new SimTransport(42, { realtime: false, ...options });
  return { sim, band: await Band.connect(42, { transport: sim }) };
}

/** The documented bounds: ax, ay within 0.12 g, az 0.96 to 1.04, turning below 30 degrees/second. */
function staysGentle(motion, label) {
  assert.ok(motion.length >= 200, `${label}: ${motion.length} samples`);
  for (const m of motion) {
    assert.ok(Math.abs(m.ax) <= 0.12 && Math.abs(m.ay) <= 0.12, `${label}: ax ${m.ax}, ay ${m.ay}`);
    assert.ok(m.az >= 0.96 && m.az <= 1.04, `${label}: az ${m.az}`);
    assert.ok(Math.abs(m.gx) <= 30 && Math.abs(m.gy) <= 30, `${label}: gx ${m.gx}, gy ${m.gy}`);
    assert.ok(Math.abs(m.gz) <= 10, `${label}: gz ${m.gz}`);
  }
  // ... and it does move: each value gets near its bound, in both directions
  const span = (key) => [Math.min(...motion.map((m) => m[key])), Math.max(...motion.map((m) => m[key]))];
  for (const [key, low, high] of [['ax', -0.11, 0.11], ['ay', -0.11, 0.11], ['az', 0.965, 1.035], ['gx', -28, 28], ['gy', -28, 28], ['gz', -9, 9]]) {
    const [least, most] = span(key);
    assert.ok(least <= low && most >= high, `${label}: ${key} only moved from ${least} to ${most}`);
  }
}

for (const firmware of ['1.0', '1.1']) {
  test(`firmware ${firmware}: the made-up swing stays inside the documented bounds`, async () => {
    const { sim, band } = await connect({ firmware });
    const motion = [];
    band.on('motion', (m) => motion.push(m));
    await band.configure({ motion: 50 });
    sim.step(6000);
    staysGentle(motion, `firmware ${firmware}`);
    // it repeats every 2 seconds
    const at = (t) => motion.find((m) => Math.abs(m.t - t) < 0.5);
    const first = motion[10];
    const later = at(first.t + 2000);
    assert.ok(later, 'a sample 2 seconds later');
    for (const key of ['ax', 'ay', 'az', 'gx', 'gy', 'gz']) assert.ok(Math.abs(first[key] - later[key]) <= 0.002 + Math.abs(first[key]) * 0.001, key);
    await band.disconnect();
  });
}

test('the made-up swing has the same bounds at a fast rate and after another sensor range', async () => {
  const { sim, band } = await connect();
  const motion = [];
  band.on('motion', (m) => motion.push(m));
  await band.setMotion({ accelRange: 16, gyroRange: 250 });
  await band.configure({ motion: 416 });
  sim.step(4200);
  staysGentle(motion, '416 a second');
  await band.disconnect();
});

test('the swing is the documented one: sample by sample the same formula as the Python pretend band', async () => {
  const { sim, band } = await connect({ firmware: '1.0' });
  const motion = [];
  band.on('motion', (m) => motion.push(m));
  await band.configure({ motion: 100 });
  sim.step(2500);
  assert.ok(motion.length >= 200);
  for (const m of motion) {
    const angle = 2 * Math.PI * m.t / 2000;
    const near = (got, want, step) => assert.ok(Math.abs(got - want) <= step, `t ${m.t}: got ${got}, formula ${want}`);
    near(m.ax, 0.12 * Math.sin(angle), 0.0011);
    near(m.ay, 0.12 * Math.cos(angle), 0.0011);
    near(m.az, 1 + 0.04 * Math.sin(3 * angle), 0.0011);
    near(m.gx, 30 * Math.cos(angle), 0.11);
    near(m.gy, -30 * Math.sin(angle), 0.11);
    near(m.gz, 10 * Math.sin(2 * angle), 0.11);
  }
  await band.disconnect();
});

test('left alone the pretend band walks at 110 steps a minute, 2 steps a reading', async () => {
  for (const firmware of ['1.0', '1.1']) {
    const { sim, band } = await connect({ firmware });
    const activity = [];
    band.on('activity', (a) => activity.push(a));
    await band.configure({ activity: true });
    sim.step(3500);
    assert.deepEqual(activity.map((a) => [a.state, a.cadence, a.steps]), [['walk', 110, 1202], ['walk', 110, 1204], ['walk', 110, 1206]], firmware);
    await band.disconnect();
  }
});

// ---- band.on() with a name that is not an event

const EVENTS = ['motion', 'event', 'tap', 'double-tap', 'free-fall', 'tilt', 'orientation', 'motion-start', 'motion-stop',
  'activity', 'audio', 'battery', 'temperature', 'link', 'mic-level', 'notice', 'disconnect', 'reconnecting', 'reconnected', 'stream-taken'];

const badEvent = (call, name) => assert.throws(call, (e) => {
  assert.ok(e instanceof VexoError, `expected a VexoError, got ${e}`);
  assert.equal(e.code, 'bad-event');
  assert.ok(e.message.startsWith(`Unknown event "${name}".`), e.message);
  assert.ok(e.message.endsWith(`Events: ${EVENTS.join(', ')}.`), e.message);
  return true;
});

test('on() takes every event the library sends', async () => {
  const { band } = await connect();
  for (const name of EVENTS) {
    const stop = band.on(name, () => {});
    assert.equal(typeof stop, 'function', name);
    stop();
  }
  await band.disconnect();
});

test('on() with a name that is not an event throws bad-event and lists the events', async () => {
  const { sim, band } = await connect();
  const seen = [];
  for (const name of ['tapp', 'taps', 'Tap', 'shake', '', 'configure']) badEvent(() => band.on(name, () => seen.push(name)), name);
  badEvent(() => band.on(undefined, () => {}), 'undefined');
  badEvent(() => band.on(() => {}), '() => {}');
  // a name from another place of the API gets a pointer to the event that was meant
  for (const [name, meant] of [['micLevel', 'mic-level'], ['mic_level', 'mic-level'], ['double_tap', 'double-tap'], ['doubleTap', 'double-tap'], ['events', 'event'], ['freeFall', 'free-fall']]) {
    assert.throws(() => band.on(name, () => {}), (e) => e.code === 'bad-event' && e.message.includes(`Did you mean "${meant}"?`), name);
  }
  assert.throws(() => band.on('tapp', () => {}), (e) => !e.message.includes('Did you mean'));
  // nothing was registered, and the band still works
  await band.configure({ events: true });
  sim.tap('tap');
  assert.deepEqual(seen, []);
  await band.disconnect();
});

test('off() refuses a wrong name too, and a handler that was never added is no error', async () => {
  const { sim, band } = await connect({ autoTap: false });
  const taps = [];
  const onTap = () => taps.push('tap');
  band.on('tap', onTap);
  assert.throws(() => band.off('tapp', onTap), { code: 'bad-event' });     // as on(); the real handler stays
  await band.configure({ events: true });
  sim.tap('tap');
  assert.equal(taps.length, 1);
  band.off('tap', onTap);
  band.off('tap', onTap);                // twice, and for a handler that was never added: fine
  band.off('motion', onTap);
  sim.tap('tap');
  assert.equal(taps.length, 1);
  await band.disconnect();
});
