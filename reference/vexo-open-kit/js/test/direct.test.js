// Directing the pretend band (playMotion, setLoudness, setRssi, ...), 'fastest' for the
// interval streams, where the pretend band keeps its storage, and what `t` and `time` do
// across a reconnect and a restart. All against the pretend band.
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, readFileSync, writeFileSync, existsSync, readdirSync, statSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Band, SimTransport, VexoError, codec } from '../src/index.js';

const NOW = 1794484800000;              // the computer's clock, pinned; a test moves it with clock.now
const V11_ONLY = ['TIME_SET', 'TIME_GET', 'STREAM_SET_INTERVAL', 'STREAM_GET_INTERVAL', 'MOTION2_SET', 'MOTION2_GET'];

function watch(sim) {
  const seen = [];
  const command = sim._command.bind(sim);
  sim._command = (frame) => {
    const c = codec.decodeCommand(frame);
    seen.push({ name: c.opcode, ...c.args });
    command(frame);
  };
  return seen;
}
const names = (log) => log.map((c) => c.name);

async function connect(simOptions = {}, bandOptions = {}) {
  const sim = simOptions instanceof SimTransport ? simOptions : new SimTransport(42, { realtime: false, ...simOptions });
  const clock = { now: NOW };
  const band = await Band.connect(42, { transport: sim, now: () => clock.now, ...bandOptions });
  band._reconnectPauseMs = 5;
  return { sim, band, clock, log: watch(sim) };
}

function collect(band, type) {
  const list = [];
  band.on(type, (e) => list.push(e));
  return list;
}

const fails = (promise, code, pattern = /./) => assert.rejects(promise, (e) => {
  assert.ok(e instanceof VexoError, `expected a VexoError, got ${e}`);
  assert.equal(e.code, code, e.message);
  assert.match(e.message, pattern);
  return true;
});

async function until(check, ms = 2000) {
  const end = Date.now() + ms;
  while (!check() && Date.now() < end) await new Promise((resolve) => setTimeout(resolve, 2));
  return check();
}

// ---------------------------------------------------------------- 'fastest' intervals

test("fastest: firmware 1.1 takes the shortest interval the band allows", async () => {
  const { band, sim, log } = await connect();
  const streams = await band.configure({ micLevel: 'fastest', activity: 'fastest', battery: 'fastest', temperature: 'fastest', link: 'fastest' });
  for (const key of ['micLevel', 'activity', 'battery', 'temperature', 'link']) assert.equal(streams[key], true, key);
  const sets = log.filter((c) => c.name === 'STREAM_SET_INTERVAL').map((c) => [c.stream, c.interval_ms]).sort((a, b) => a[0] - b[0]);
  assert.deepEqual(sets, [[2, 100], [4, 1000], [5, 100], [6, 100], [7, 20]]);
  assert.deepEqual(await band.getInterval('micLevel'), { stream: 'micLevel', seconds: 0.02, min: 0.02, max: 10 });
  const levels = collect(band, 'mic-level');
  sim.step(1000);
  assert.equal(levels.length, 50);
});

test("fastest: setInterval says what you got and leaves the stream alone", async () => {
  const { band } = await connect();
  assert.deepEqual(await band.setInterval('micLevel', 'fastest'), { stream: 'micLevel', seconds: 0.02, min: 0.02, max: 10 });
  assert.deepEqual(await band.setInterval('battery', 'fastest'), { stream: 'battery', seconds: 1, min: 1, max: 3600 });
  assert.equal(band.streams.micLevel || band.streams.battery, false);
});

test("fastest: firmware 1.0 falls back to the band's fixed pace; a number is still refused", async () => {
  for (const [simOptions, bandOptions] of [[{ firmware: '1.0' }, {}], [{}, { protocol: '1.0' }]]) {
    const { band, sim, log } = await connect(simOptions, bandOptions);
    await band.configure({ micLevel: 'fastest', activity: 'fastest', battery: 'fastest', link: 'fastest' });
    assert.deepEqual(names(log), ['STREAM_START']);      // plain on, nothing else
    assert.ok(band.streams.micLevel && band.streams.link);
    const levels = collect(band, 'mic-level');
    sim.step(1000);
    assert.equal(levels.length, 10);                      // the pace of firmware 1.0
    log.length = 0;
    assert.deepEqual(await band.setInterval('micLevel', 'fastest'), { stream: 'micLevel', seconds: 0.1, min: 0.1, max: 0.1 });
    assert.deepEqual(await band.setInterval('activity', 'fastest'), { stream: 'activity', seconds: 1, min: 1, max: 1 });
    assert.deepEqual(await band.setInterval('battery', 'fastest'), { stream: 'battery', seconds: 0, min: 0, max: 0 });
    assert.deepEqual(log, []);                            // nothing was sent
    // temperature is the one interval firmware 1.0 has: its shortest is 1 second
    await band.configure({ temperature: 'fastest' });
    assert.deepEqual(log, [{ name: 'TEMP_SET_INTERVAL', seconds: 1 }, { name: 'STREAM_START', mask: 1 << 5 }]);
    assert.deepEqual(await band.setInterval('temperature', 'fastest'), { stream: 'temperature', seconds: 1, min: 1, max: 255 });
    assert.ok(!names(log).some((name) => V11_ONLY.includes(name)));
    log.length = 0;
    await fails(band.configure({ micLevel: 0.02 }), 'not-available', /firmware 1\.1/);
    await fails(band.setInterval('micLevel', 0.02), 'not-available', /firmware 1\.1/);
    await fails(band.getInterval('micLevel'), 'not-available', /firmware 1\.1/);
    assert.deepEqual(log, []);
    await band.disconnect();
    await fails(band.setInterval('micLevel', 'fastest'), 'not-connected');
  }
});

test("fastest: only that word, and only for streams with an interval", async () => {
  for (const firmware of ['1.0', '1.1']) {
    const { band, log } = await connect({ firmware });
    for (const bad of [{ micLevel: 'fast' }, { events: 'fastest' }, { audio: 'fastest' }, { battery: 'slowest' }]) {
      await fails(band.configure(bad), 'bad-stream');
    }
    await fails(band.setInterval('motion', 'fastest'), 'bad-arg');
    assert.deepEqual(log, []);
  }
});

test("fastest: a reconnect sets the interval again", async () => {
  const { band, sim, log } = await connect({}, { autoReconnect: true });
  await band.configure({ micLevel: 'fastest' });
  log.length = 0;
  sim.dropLink();
  assert.ok(await until(() => band.connected));
  assert.ok(log.some((c) => c.name === 'STREAM_SET_INTERVAL' && c.stream === 7 && c.interval_ms === 20));
  assert.equal(band.streams.micLevel, true);
});

// ---------------------------------------------------------------- t and time across links

test('time: a dropped link and auto-reconnect: t goes on, time stays real time', async () => {
  for (const [firmware, source] of [['1.1', 'band'], ['1.0', 'computer']]) {
    const sim = new SimTransport(42, { realtime: false, firmware });
    sim.step(5000);                       // the band has been on for 5 seconds
    const { band, clock } = await connect(sim, { autoReconnect: true });
    const taps = collect(band, 'tap');
    await band.configure({ events: true });
    sim.step(1000);
    sim.tap('tap');
    assert.deepEqual([taps[0].t, taps[0].time, band.clock], [6000, NOW + 1000, source], firmware);

    sim.dropLink();
    sim.step(3000);                       // out of range for 3 seconds: the band keeps counting ...
    clock.now = NOW + 4000;               // ... and so does the computer
    assert.ok(await until(() => band.connected), firmware);
    assert.equal(band.clock, source);
    sim.step(500);
    sim.tap('tap');
    // t goes on from where it was: 6000 + 3000 + 500. It did not start again.
    assert.deepEqual([taps[1].t, taps[1].time], [9500, NOW + 4500], firmware);
    await band.disconnect();
  }
});

test("time: after a reconnect it follows the computer's clock as it is then", async () => {
  for (const firmware of ['1.1', '1.0']) {
    const { band, sim, clock } = await connect({ firmware }, { autoReconnect: true });
    const taps = collect(band, 'tap');
    await band.configure({ events: true });
    sim.step(1000);
    sim.tap('tap');
    sim.dropLink();
    sim.step(3000);
    clock.now = NOW + 4000 + 3600000;     // meanwhile the computer's clock was put forward an hour
    assert.ok(await until(() => band.connected));
    sim.step(500);
    sim.tap('tap');
    assert.equal(taps[1].t - taps[0].t, 3500, firmware);                  // t: the true time between them
    assert.equal(taps[1].time - taps[0].time, 3603500, firmware);         // time: steps with the clock
    await band.disconnect();
  }
});

test('time: restart() starts t again at 0; time is right after connecting again', async () => {
  for (const [firmware, source] of [['1.1', 'band'], ['1.0', 'computer']]) {
    const sim = new SimTransport(42, { realtime: false, firmware });
    sim.step(5000);
    const { band } = await connect(sim, { autoReconnect: true });
    let taps = collect(band, 'tap');
    const ends = collect(band, 'disconnect');
    await band.configure({ events: true });
    sim.step(1000);
    sim.tap('tap');
    assert.equal(taps[0].t, 6000);
    await band.restart();
    sim.step(300);                        // the band restarts 200 ms after its answer; 100 ms later ...
    assert.deepEqual([ends, band.reconnecting], [[{ reason: 'restart' }], false]);    // no auto-reconnect

    const next = await connect(sim);
    next.clock.now = NOW + 2000;
    const again = await Band.connect(42, { transport: (await next.band.disconnect(), sim), now: () => NOW + 2000 });
    assert.deepEqual([again.clock, again.info.uptimeMs], [source, 100], firmware);     // ... its counter shows 100
    taps = collect(again, 'tap');
    await again.configure({ events: true });
    sim.step(400);
    sim.tap('tap');
    assert.equal(taps[0].t, 500, firmware);                   // t began again at 0: smaller than before the restart
    assert.equal(taps[0].time, NOW + 2400, firmware);         // time is right all the same
    await again.disconnect();
  }
});

test("time: after a restart the band's clock is unset until someone sets it", async () => {
  const { band, sim } = await connect();
  await band.restart();
  sim.step(300);
  const again = await Band.connect(42, { transport: sim, now: () => NOW, setClock: false });
  assert.equal(again.clock, 'computer');
  assert.deepEqual(await again.time(), { time: NOW, set: false, t: 100, source: 'computer' });
  const taps = collect(again, 'tap');
  await again.configure({ events: true });
  sim.step(1000);
  sim.tap('tap');
  assert.equal(taps[0].time, NOW + 1000);                     // worked out from the computer's clock
});

test('time: a band that restarted by itself, with auto-reconnect: t goes back, time does not', async () => {
  for (const [firmware, source] of [['1.1', 'band'], ['1.0', 'computer']]) {
    const sim = new SimTransport(42, { realtime: false, firmware });
    sim.step(5000);
    const { band, clock } = await connect(sim, { autoReconnect: true });
    const taps = collect(band, 'tap');
    await band.configure({ events: true });
    sim.tap('tap');
    sim.powerCycle();                     // reset button: the link is lost, the counter starts at 0, the clock is gone
    clock.now = NOW + 1000;
    assert.ok(await until(() => band.connected), firmware);
    assert.equal(band.clock, source);
    sim.step(250);
    sim.tap('tap');
    assert.deepEqual(taps.map((e) => e.t), [5000, 250], firmware);
    assert.equal(taps[1].time, NOW + 1250, firmware);
    await band.disconnect();
  }
});

test('time: fast motion t follows the band after each new link', async () => {
  const { band, sim } = await connect({}, { autoReconnect: true });
  sim.step(5000);
  const motion = collect(band, 'motion');
  await band.configure({ motion: 208 });
  sim.step(100);
  const before = motion.at(-1);
  sim.dropLink();
  sim.step(2000);
  assert.ok(await until(() => band.connected));
  sim.step(100);
  assert.ok(motion.at(-1).n < 40);                            // n began again at 0
  assert.ok(motion.at(-1).t > before.t + 2000);
  motion.length = 0;
  sim.powerCycle();
  assert.ok(await until(() => band.connected));
  sim.step(100);
  assert.ok(motion.at(-1).t < 200);                           // the band's counter started again, and t follows it
});

// ---------------------------------------------------------------- directing the pretend band

test('playMotion: a function of time, then the band lies still', async () => {
  for (const firmware of ['1.1', '1.0']) {
    const { band, sim } = await connect({ firmware });
    const motion = collect(band, 'motion');
    await band.configure({ motion: 50 });
    const asked = [];
    sim.playMotion((s) => { asked.push(Math.round(s * 1e6) / 1e6); return { gz: 200, ax: 0.5 }; }, { seconds: 0.2 });
    assert.equal(sim.moving, true);
    sim.step(1000);
    assert.equal(sim.moving, false);
    assert.deepEqual(asked, [0, 0.02, 0.04, 0.06, 0.08, 0.1, 0.12, 0.14, 0.16, 0.18], firmware);
    const values = motion.map((m) => [m.ax, m.ay, m.az, m.gx, m.gy, m.gz].map((v) => v + 0));
    assert.deepEqual(values.slice(0, 10), Array(10).fill([0.5, 0, 1, 0, 0, 200]), firmware);
    assert.deepEqual(values.slice(10), Array(values.length - 10).fill([0, 0, 1, 0, 0, 0]));    // lying flat and still
    assert.ok(values.length >= 40);
  }
});

test('playMotion: a list, a queue, an endless one that makes way, and stopMotion', async () => {
  const { band, sim } = await connect();
  const motion = collect(band, 'motion');
  await band.configure({ motion: 50, sensors: 'accel' });
  sim.playMotion([{ ax: 0.1 }, { ax: 0.2 }, { ax: 99 }]);       // one entry for each sample; 99 g is clipped
  sim.playMotion({ ay: -1, az: 0 }, { seconds: 0.04 });         // a single reading, held; plays after the list
  sim.playMotion(() => ({ az: -1 }));                           // until something else is asked for
  sim.step(200);
  assert.deepEqual(motion.slice(0, 7).map((m) => [m.ax, m.ay, m.az].map((v) => v + 0)),
    [[0.1, 0, 1], [0.2, 0, 1], [2, 0, 1], [0, -1, 0], [0, -1, 0], [0, 0, -1], [0, 0, -1]]);
  assert.ok(motion.every((m) => m.gx === null));                // the sensor that was not asked for
  assert.equal(sim.moving, true);
  sim.playMotion([{ ax: 1 }]);                                  // the endless one makes way
  motion.length = 0;
  sim.step(100);
  assert.deepEqual(motion.slice(0, 2).map((m) => m.ax), [1, 0]);
  assert.equal(sim.moving, false);
  sim.stopMotion();                                             // back to the made-up swing
  motion.length = 0;
  sim.step(500);
  assert.ok(motion.some((m) => m.ax !== 0));
  for (const bad of [0, -1, '1', true]) assert.throws(() => sim.playMotion(() => ({}), { seconds: bad }), RangeError);
});

test('playMotion: a function that fails is dropped', async (t) => {
  const { band, sim } = await connect();
  const motion = collect(band, 'motion');
  await band.configure({ motion: 50 });
  t.mock.method(console, 'error', () => {});
  sim.playMotion(() => { throw new Error('oops'); });
  sim.step(100);
  assert.deepEqual([motion[0].az, sim.moving], [1, false]);
});

test('loudness: steady, a curve, a clap, and it follows the gain', async () => {
  const { band, sim } = await connect();
  const levels = collect(band, 'mic-level');
  await band.configure({ micLevel: true });
  sim.setLoudness(0.5);
  sim.step(300);
  assert.deepEqual(levels.map((e) => [e.rmsDb, e.peakDb, e.level]), Array(3).fill([48, 42, 0.5]));
  sim.playLoudness([0.95]);                                     // a clap: one loud reading
  sim.playLoudness((s) => 0.25 + s, { seconds: 0.2 });
  levels.length = 0;
  sim.step(500);
  assert.deepEqual(levels.map((e) => e.rmsDb), [5, 72, 62, 48, 48]);
  await band.setMicGain(0);                                     // 20 dB less gain: everything reads 20 dB quieter
  levels.length = 0;
  sim.step(100);
  assert.equal(levels[0].rmsDb, 68);
  sim.setLoudness(null);
  for (const bad of [-0.1, 1.5, 'loud', true]) assert.throws(() => sim.setLoudness(bad), RangeError);
});

test('playLoudness with audio packets: each is sent once, then the usual sound', async () => {
  const { band, sim } = await connect({ audioPackets: [Uint8Array.of(0xaa)] });
  const audio = collect(band, 'audio');
  await band.configure({ audio: true, micLevel: 0.02 });
  const levels = collect(band, 'mic-level');
  sim.playLoudness(0.75, { seconds: 0.06, packets: [Uint8Array.of(1), Uint8Array.of(2), Uint8Array.of(3)] });
  sim.step(100);
  assert.deepEqual(audio.map((a) => a.opus[0]), [1, 2, 3, 0xaa, 0xaa]);
  assert.deepEqual(levels.slice(0, 3).map((e) => e.rmsDb), [24, 24, 24]);
  assert.notEqual(levels[3].rmsDb, 24);
});

test('setRssi', async () => {
  const { band, sim } = await connect();
  const link = collect(band, 'link');
  await band.configure({ link: true });
  sim.setRssi(-85);
  sim.step(1000);
  assert.deepEqual([link[0].rssi, (await band.getRadio()).rssi], [-85, -85]);
  sim.setRssi(null);
  sim.step(1000);
  assert.ok(link[1].rssi >= -60 && link[1].rssi <= -50);
  for (const bad of [-200, 30, 'far', true]) assert.throws(() => sim.setRssi(bad), RangeError);
});

test('setActivity counts steps by the band clock, also while the link is down', async () => {
  const { band, sim } = await connect({}, { autoReconnect: true });
  const activity = collect(band, 'activity');
  await band.configure({ activity: true });
  sim.setActivity('run', { steps: 5000 });
  sim.step(2000);
  assert.deepEqual(activity.map((a) => [a.state, a.cadence, a.steps]), [['run', 165, 5002], ['run', 165, 5005]]);
  sim.dropLink();
  sim.step(60000 - 2000);                 // a minute of running in all, most of it out of range
  assert.ok(await until(() => band.connected));
  sim.step(1000);
  assert.equal(activity.at(-1).steps, 5000 + 165 + 2);
  sim.setActivity('still');
  sim.step(3000);
  assert.deepEqual([activity.at(-1).state, activity.at(-1).cadence, activity.at(-1).steps], ['still', 0, 5167]);
  sim.setActivity('walk', { cadence: 60 });
  sim.step(3000);
  assert.deepEqual([activity.at(-1).state, activity.at(-1).cadence, activity.at(-1).steps], ['walk', 60, 5170]);
  assert.throws(() => sim.setActivity('swim'), RangeError);
});

test('dropLink(seconds): away for that long, then back by itself', async () => {
  const { band, sim } = await connect({}, { autoReconnect: true });
  sim.dropLink(2);
  assert.equal(sim.reachable, true);
  await fails(sim.open(), 'not-found-nearby');
  sim.step(1900);
  await new Promise((resolve) => setTimeout(resolve, 40));
  assert.equal(band.reconnecting, true);  // still away
  sim.step(100);
  assert.ok(await until(() => band.connected));                 // back by itself
  assert.throws(() => sim.dropLink(-1), RangeError);
});

test('after(): runs on the band clock, and waits for a link', async (t) => {
  const { band, sim } = await connect();
  const falls = collect(band, 'free-fall');
  await band.configure({ events: true });
  const order = [];
  sim.after(1.5, () => sim.tap('free-fall'));
  sim.after(0.5, () => order.push(sim._t));
  sim.after(0.7, () => { throw new Error('oops'); });           // an error is printed, the band goes on
  sim.step(400);
  assert.deepEqual(order, []);
  t.mock.method(console, 'error', () => {});
  sim.step(1000);
  assert.deepEqual([order, falls], [[500], []]);
  sim.step(200);
  assert.deepEqual(falls.map((e) => e.t), [1500]);
  sim.after(1, () => order.push('late'));
  await band.disconnect();
  sim.step(5000);
  assert.deepEqual(order, [500]);
  const again = await Band.connect(42, { transport: sim });
  sim.step(1);
  assert.deepEqual(order, [500, 'late']);
  await again.disconnect();
});

test('autoTap: false switches the scripted taps off', async () => {
  for (const autoTap of [true, false]) {
    const sim = new SimTransport(42, { autoTap });
    const band = await Band.connect(42, { transport: sim });
    assert.equal(sim._due.has('script'), autoTap);
    await band.disconnect();
  }
  assert.throws(() => new SimTransport(42, { autoTap: 'no' }), RangeError);
});

test('a realtime pretend band keeps counting while the link is down', async () => {
  const sim = new SimTransport(42, { autoTap: false });
  const band = await Band.connect(42, { transport: sim, autoReconnect: true });
  band._reconnectPauseMs = 5;
  const droppedAt = sim._t;
  const started = Date.now();
  sim.dropLink(0.2);
  assert.ok(await until(() => band.connected, 3000));
  assert.ok(Date.now() - started >= 190);                       // it stayed away for the 0.2 seconds
  assert.ok(sim._t - droppedAt >= 190);                         // and its counter went on meanwhile
  await band.disconnect();
});

// ---------------------------------------------------------------- where the storage is kept

function tempFolder(t) {
  const folder = mkdtempSync(join(tmpdir(), 'vexo-sim-test-'));
  t.after(() => rmSync(folder, { recursive: true, force: true }));
  return folder;
}

test('storage: in memory unless a file is named; it survives a reconnect in the program', async () => {
  const { band, sim } = await connect({}, { autoReconnect: true });
  await band.storage.put(7, 'hello');
  sim.dropLink();
  assert.ok(await until(() => band.connected));
  assert.equal(await band.storage.getText(7), 'hello');
  assert.equal(sim._files, null);         // no file functions were even loaded
  await band.disconnect();
});

test('storage: storeFile keeps it for the next run, in a file only you can read', async (t) => {
  const folder = tempFolder(t);
  const file = join(folder, 'band.json');
  let { band } = await connect({ storeFile: file });
  assert.equal(existsSync(file), false);  // nothing to keep yet
  await band.storage.put(7, 'hello');
  await band.storage.put(999, Uint8Array.of(0, 255));
  assert.deepEqual(JSON.parse(readFileSync(file, 'utf8')), { 7: '68656c6c6f', 999: '00ff' });
  if (process.platform !== 'win32') assert.equal(statSync(file).mode & 0o777, 0o600);
  await band.disconnect();

  ({ band } = await connect({ storeFile: file }));              // the next run of the program
  assert.equal(await band.storage.getText(7), 'hello');
  assert.deepEqual([...await band.storage.get(999)], [0, 255]);
  await band.storage.delete(7);
  assert.deepEqual(JSON.parse(readFileSync(file, 'utf8')), { 999: '00ff' });
  const wiped = band.storage.wipe();
  await until(() => JSON.stringify(JSON.parse(readFileSync(file, 'utf8'))) === '{}');
  band.transport.step(1500);
  await wiped;
  assert.deepEqual(JSON.parse(readFileSync(file, 'utf8')), {});
  await band.disconnect();
  assert.deepEqual(readdirSync(folder), ['band.json']);         // no half-written files left
});

test('storage: a file that is not a store is ignored', async (t) => {
  const file = join(tempFolder(t), 'band.json');
  for (const text of ['not json', '[1, 2]', '{"7": "zz"}', '{"1000": "00"}', '{"7": ""}', `{"7": "${'00'.repeat(233)}"}`, '{"-1": "00"}', '{"7": 5}']) {
    writeFileSync(file, text);
    const { band } = await connect({ storeFile: file });
    assert.equal((await band.storage.info()).usedKeys, 0, text);
    await band.disconnect();
  }
});

test('storage: storeFile true is a folder of your own in your home folder', async (t) => {
  const home = join(tempFolder(t), 'home');
  mkdirSync(home);
  const before = { HOME: process.env.HOME, USERPROFILE: process.env.USERPROFILE };
  t.after(() => { for (const [k, v] of Object.entries(before)) { if (v === undefined) delete process.env[k]; else process.env[k] = v; } });
  process.env.HOME = home;
  process.env.USERPROFILE = home;
  const expected = join(home, '.vexo-open', 'pretend-band-42.json');
  let { band } = await connect({ storeFile: true });
  await band.storage.put(1, 'mine');
  await band.disconnect();
  assert.ok(existsSync(expected));
  if (process.platform !== 'win32') assert.equal(statSync(join(home, '.vexo-open')).mode & 0o777, 0o700);
  ({ band } = await connect({ storeFile: true }));
  assert.equal(await band.storage.getText(1), 'mine');
  await band.disconnect();
});

test('storage: the folder of a named file must exist; other values are refused', async (t) => {
  const sim = new SimTransport(42, { realtime: false, storeFile: join(tempFolder(t), 'missing', 'band.json') });
  await fails(sim.open(), 'bad-arg', /does not exist/);
  for (const bad of [5, '', {}]) assert.throws(() => new SimTransport(42, { storeFile: bad }), RangeError);
});

test('the Python and the JavaScript pretend band read the same storage file', async (t) => {
  const file = join(tempFolder(t), 'band.json');
  writeFileSync(file, '{"7": "68656c6c6f", "999": "00ff"}');     // as python/vexo_open/transports/sim.py writes it
  const { band } = await connect({ storeFile: file });
  assert.equal(await band.storage.getText(7), 'hello');
  await band.disconnect();
});
