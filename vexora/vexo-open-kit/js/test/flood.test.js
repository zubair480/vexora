// A band that sends as fast as it can while the handlers are slow must not use up the
// computer. In JavaScript a handler is called at once, inside the call that brought the
// frame, so the library keeps no line of waiting events. Only async handlers stay alive
// after the call: at most 5000 of them may be running, more events are dropped and counted.
import test from 'node:test';
import assert from 'node:assert/strict';
import { Band, SimTransport, codec } from '../src/index.js';

const CAP = 5000;
const BATTERY = codec.encodeStream('battery', { percent: 50, state: 0, millivolts: 3900 });
const pause = () => new Promise((resolve) => { setImmediate(resolve); });

test('flood with slow async handlers: capped, counted, said once; commands and link events still work; ends on time', async (t) => {
  const said = [];
  t.mock.method(console, 'error', (...words) => said.push(words.join(' ')));
  const sim = new SimTransport(42, { realtime: false, autoTap: false });
  const band = await Band.connect(42, { transport: sim });
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const counts = { slow: 0, plain: 0 };
  const ends = [];
  band.on('battery', async () => { counts.slow++; await gate; });
  band.on('battery', () => { counts.plain++; });               // a plain handler runs at once: never dropped
  band.on('disconnect', async (e) => { ends.push(e.reason); });

  const started = Date.now();
  let sent = 0;
  let most = 0;
  let answered = 0;
  while (Date.now() - started < 1500) {                         // as fast as it can, for 1.5 seconds
    for (let i = 0; i < 100; i++) sim.onFrame(BATTERY);
    sent += 100;
    most = Math.max(most, band._busy);
    if (sent % 20000 === 0) {                                   // a command in the middle of the flood
      await band.setLed('#00ff00');
      assert.equal((await band.getLed()).g, 255);
      answered++;
    }
    await pause();
  }
  const took = Date.now() - started;
  assert.ok(took < 2000, `the flood loop ended on time (${took} ms)`);
  assert.ok(sent > 20000, `the flood was a flood (${sent} frames)`);
  assert.ok(answered >= 1);
  assert.ok(most <= CAP, `async handlers running at once: ${most}`);
  assert.deepEqual([counts.slow, counts.plain], [CAP, sent]);
  assert.equal(band.droppedEvents, sent - CAP);                 // counted, and the program can read it
  assert.equal(said.length, 1, 'one line, not one for each frame');
  assert.match(said[0], /band\.droppedEvents/);

  await band.disconnect();
  assert.deepEqual(ends, ['closed']);                           // given although the cap is reached
  release();
  await pause();
  await pause();
  assert.equal(band._busy, 0);
});

test('after the slow handlers have finished, events are given again', async (t) => {
  t.mock.method(console, 'error', () => {});
  const sim = new SimTransport(42, { realtime: false, autoTap: false });
  const band = await Band.connect(42, { transport: sim });
  let release;
  let gate = new Promise((resolve) => { release = resolve; });
  let calls = 0;
  band.on('battery', async () => { calls++; await gate; });
  for (let i = 0; i < CAP + 300; i++) sim.onFrame(BATTERY);
  assert.deepEqual([calls, band.droppedEvents], [CAP, 300]);
  release();
  await pause();
  await pause();
  gate = Promise.resolve();
  sim.onFrame(BATTERY);
  assert.deepEqual([calls, band.droppedEvents], [CAP + 1, 300]);
});

test('an async handler that fails is reported like any handler, not left as an unhandled rejection', async (t) => {
  const said = [];
  t.mock.method(console, 'error', (...words) => said.push(words.map(String).join(' ')));
  const sim = new SimTransport(42, { realtime: false, autoTap: false });
  const band = await Band.connect(42, { transport: sim });
  band.on('battery', async () => { throw new Error('mine'); });
  sim.onFrame(BATTERY);
  await pause();
  assert.equal(said.length, 1);
  assert.match(said[0], /Error in your "battery" handler:.*mine/);
  assert.equal(band._busy, 0);
});
