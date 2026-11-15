// A second program on the band's other link takes a stream (contract 2.3: a stream goes to
// the transport that last started it), and a band that is already connected elsewhere.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Band, SimTransport } from '../src/index.js';

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** A connected pretend band whose stream watch runs 20 times faster than the real one. */
async function connect(options = {}) {
  const sim = new SimTransport(42, { autoTap: false, ...options });
  const band = await Band.connect(42, { transport: sim });
  band._streamWatch.times = { tick: 20, silence: 150, quiet: 300 };
  const taken = [];
  band.on('stream-taken', (e) => taken.push(e));
  return { sim, band, taken };
}

/** Wait until `check()` is true, at most `ms`. */
async function until(check, ms = 3000) {
  const end = Date.now() + ms;
  while (!check() && Date.now() < end) await wait(10);
  return check();
}

test('a second program takes "motion": stream-taken says so, the link stays up, takeBack() gets it back', async () => {
  const { sim, band, taken } = await connect();
  let motion = 0;
  let levels = 0;
  band.on('motion', () => { motion++; });
  band.on('mic-level', () => { levels++; });
  await band.configure({ motion: 50, micLevel: true });
  assert.ok(await until(() => motion > 0 && levels > 0));
  await wait(400);                                  // data flows: the watch asks nothing
  assert.equal(band._streamWatch.asked, 0);
  assert.equal(taken.length, 0);

  sim.otherProgramStarts('motion');                 // e.g. motion.py --usb beside a dashboard on Bluetooth
  assert.ok(await until(() => taken.length === 1));
  assert.deepEqual(taken[0].streams, ['motion']);
  assert.deepEqual(taken[0].stopped, []);
  assert.match(taken[0].message, /Another program on the cable \(USB\) took this stream/);
  assert.match(taken[0].message, /share its streams, its settings and its light/);
  assert.match(taken[0].message, /takeBack\(\)/);
  assert.equal(band.connected, true);
  assert.deepEqual(band.takenStreams, ['motion']);
  assert.equal(band.streams.motion, 50);            // the band still streams it, only not to us
  const before = levels;
  motion = 0;
  await wait(300);
  assert.equal(motion, 0);
  assert.ok(levels > before);                       // the other stream is still ours
  assert.equal(taken.length, 1);                    // said once, not every few seconds

  await band.takeBack();
  assert.deepEqual(band.takenStreams, []);
  assert.ok(await until(() => motion > 0));
  await wait(400);
  assert.equal(taken.length, 1);
  await band.disconnect();
});

test('the second program took the stream and ended: the band stopped it, and stream-taken says that', async () => {
  const { sim, band, taken } = await connect();
  let motion = 0;
  band.on('motion', () => { motion++; });
  await band.configure({ motion: 50 });
  assert.ok(await until(() => motion > 0));
  sim.otherProgramStarts('motion');
  sim.otherProgramCloses();
  assert.ok(await until(() => taken.length === 1));
  assert.deepEqual(taken[0].streams, ['motion']);
  assert.deepEqual(taken[0].stopped, ['motion']);
  assert.match(taken[0].message, /took this stream and has stopped since/);
  assert.equal(band.streams.motion, 0);
  motion = 0;
  await band.configure({ motion: 50 });             // configure() starts it again too
  assert.ok(await until(() => motion > 0));
  assert.deepEqual(band.takenStreams, []);
  await band.disconnect();
});

test('a stream that is silent by nature (taps): found when the band says it is off', async () => {
  const { sim, band, taken } = await connect();
  await band.configure({ events: true });
  await wait(100);
  assert.equal(band._streamWatch.asked, 0);
  sim.otherProgramStarts('events');
  sim.otherProgramCloses();
  assert.ok(await until(() => taken.length === 1));
  assert.deepEqual(taken[0].streams, ['events']);
  assert.equal(band.streams.events, false);
  let taps = 0;
  band.on('tap', () => { taps++; });
  await band.takeBack();
  assert.equal(sim.tap('tap'), true);
  assert.equal(taps, 1);
  await band.disconnect();
});

test('no stream-taken for what the program did itself: stop, disconnect, sleep', async () => {
  const { band, taken } = await connect();
  await band.configure({ motion: 50, activity: true });
  await band.configure({ motion: false });
  await wait(400);
  assert.equal(taken.length, 0);
  assert.equal(band._streamWatch.asked, 0);
  await band.disconnect();
  await wait(100);
  assert.equal(taken.length, 0);
  assert.equal(band._streamWatch._timer, null);

  const usb = await connect({ via: 'usb' });
  await usb.band.configure({ motion: 50 });
  await usb.band.sleep();                           // on the cable the link stays open and the streams are off
  await wait(400);
  assert.equal(usb.taken.length, 0);
  await usb.band.disconnect();
});

test('on the cable the other program is "on Bluetooth"; with nobody listening the sentence is printed once', async () => {
  const sim = new SimTransport(42, { autoTap: false, via: 'usb' });
  const band = await Band.connect(42, { transport: sim });
  band._streamWatch.times = { tick: 20, silence: 150, quiet: 300 };
  const printed = [];
  const original = console.error;
  console.error = (...parts) => printed.push(parts.join(' '));
  try {
    await band.configure({ motion: 50 });
    sim.otherProgramStarts('motion');
    assert.ok(await until(() => printed.length > 0));
    await wait(300);
  } finally {
    console.error = original;
  }
  assert.equal(printed.length, 1);
  assert.match(printed[0], /Another program on Bluetooth took this stream/);
  assert.equal(band.connected, true);
  await band.disconnect();
});

test('a band that is connected elsewhere cannot be found, and the sentence names that first', async () => {
  const sim = new SimTransport(42, { realtime: false });
  sim.connectedElsewhere = true;
  await assert.rejects(Band.connect(42, { transport: sim }), (e) => {
    assert.equal(e.code, 'not-found-nearby');
    assert.match(e.message, /Could not see Vexo-HACK-42\./);
    const elsewhere = e.message.indexOf('something else is connected to it');
    const doubleTap = e.message.indexOf('double-tap');
    assert.ok(elsewhere > 0 && doubleTap > elsewhere, e.message);
    assert.match(e.message, /the dashboard tab, another program, a teammate's laptop/);
    assert.match(e.message, /one Bluetooth connection at a time/);
    return true;
  });
  sim.connectedElsewhere = false;                   // the other one was closed
  const band = await Band.connect(42, { transport: sim });
  assert.equal(band.name, 'Vexo-HACK-42');
  await band.disconnect();
});
