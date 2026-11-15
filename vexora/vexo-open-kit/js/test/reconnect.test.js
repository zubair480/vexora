// Auto-reconnect: Band.connect(42, { autoReconnect: true }). Everything runs against the
// pretend band; sim.reachable = false keeps it away, sim.dropLink() loses the link.
import test from 'node:test';
import assert from 'node:assert/strict';
import { Band, SimTransport, Transport, VexoError, codec } from '../src/index.js';

const ALL_OFF = { motion: 0, events: false, activity: false, audio: false, battery: false, temperature: false, link: false, micLevel: false };

async function connectSim(bandOptions = { autoReconnect: true }, simOptions = {}) {
  const sim = new SimTransport(42, { realtime: false, firmware: '1.0', ...simOptions });
  const band = await Band.connect(42, { transport: sim, ...bandOptions });
  band._reconnectPauseMs = 5;           // the real pause between attempts is 1 second
  const events = [];                    // every link event, in order
  for (const type of ['reconnecting', 'reconnected', 'disconnect']) band.on(type, (e) => events.push([type, e]));
  return { sim, band, events };
}

const once = (band, type) => new Promise((resolve) => {
  const stop = band.on(type, (e) => { stop(); resolve(e); });
});
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const fails = (promise, code, pattern = /./) => assert.rejects(promise, (e) => {
  assert.ok(e instanceof VexoError, `expected a VexoError, got ${e}`);
  assert.equal(e.code, code);
  assert.match(e.message, pattern);
  return true;
});

/** The commands the pretend band receives from now on, by contract name. */
function watchCommands(sim) {
  const seen = [];
  const command = sim._command.bind(sim);
  sim._command = (frame) => { seen.push(codec.decodeCommand(frame).opcode); command(frame); };
  return seen;
}

test('default: off, 120 seconds; a lost link ends with one disconnect { reason: "lost" } as before', async () => {
  const { band, sim, events } = await connectSim({});
  assert.equal(band.autoReconnect, false);
  assert.equal(band.reconnectSeconds, 120);
  assert.equal(band.reconnecting, false);
  assert.equal(band._reconnectPauseMs, 5);
  assert.equal((await Band.connect(42, { transport: new SimTransport(42, { realtime: false }) }))._reconnectPauseMs, 1000);
  await band.configure({ motion: 50 });
  sim.dropLink();
  await sleep(20);
  assert.deepEqual(events, [['disconnect', { reason: 'lost' }]]);
  assert.equal(band.connected, false);
  assert.equal(band.reconnecting, false);
  await fails(band.getLed(), 'not-connected', /Call Band\.connect/);
});

test('options: autoReconnect and reconnectSeconds are kept on the band; a bad reconnectSeconds is refused', async () => {
  const sim = new SimTransport(42, { realtime: false });
  const band = await Band.connect(42, { transport: sim, autoReconnect: true, reconnectSeconds: 30 });
  assert.equal(band.autoReconnect, true);
  assert.equal(band.reconnectSeconds, 30);
  await band.disconnect();
  for (const bad of [0, -1, '120', NaN]) await fails(Band.connect(42, { transport: sim, reconnectSeconds: bad }), 'bad-arg');
});

test('drop -> reconnecting -> reconnected: streams flow again, LED and settings are back', async () => {
  const { band, sim, events } = await connectSim();
  const motion = [];
  band.on('motion', (m) => motion.push(m));
  await band.setLed('#ff8800', { brightness: 0.5, pattern: 'blink', period: 500 });
  await band.setMotion({ rate: 100, accelRange: 8, gyroRange: 500 });
  await band.setEvents({ kinds: ['tap', 'double-tap'], tapThreshold: 1.5 });
  await band.setMicGain(-6);
  await band.setTxPower(4);
  await band.configure({ motion: true, events: true, battery: true, temperature: 2, link: true });
  await band.identify(3);                                               // not sent again after a reconnect
  const streams = { ...band.streams };
  sim.step(100);
  assert.equal(motion.length, 10);

  const inFlight = band.getBattery();
  await null;
  const seen = watchCommands(sim);
  sim.dropLink();
  // D2: at once, in the same breath as the drop.
  assert.deepEqual(events, [['reconnecting', { reason: 'lost', seconds: 120 }]]);
  assert.equal(band.connected, false);
  assert.equal(band.reconnecting, true);
  assert.deepEqual(band.streams, ALL_OFF);
  await fails(inFlight, 'not-connected');

  const back = await once(band, 'reconnected');
  assert.deepEqual(Object.keys(back), ['attempts', 'seconds', 'failed']);
  assert.equal(back.attempts, 1);
  assert.ok(back.seconds >= 0 && back.seconds < 1);
  assert.equal(back.seconds, Math.round(back.seconds * 1000) / 1000);   // rounded to 0.001
  assert.deepEqual(back.failed, []);
  assert.equal(events.length, 2);                                       // no disconnect event at all
  assert.equal(band.connected, true);
  assert.equal(band.reconnecting, false);
  // D5: the order. LED before the streams.
  assert.deepEqual(seen, ['GET_INFO', 'IMU_SET', 'EVENT_SET', 'MIC_SET_GAIN', 'RADIO_SET_TX_POWER', 'TEMP_SET_INTERVAL', 'LED_SET', 'STREAM_START']);
  assert.deepEqual(band.streams, streams);
  assert.equal(band.streams.motion, 100);

  motion.length = 0;
  sim.step(1000);
  assert.equal(motion.length, 100);                                     // flowing again, at the rate that was set
  assert.deepEqual(await band.getLed(), { r: 255, g: 136, b: 0, brightness: 128 / 255, pattern: 'blink', period: 500 });
  assert.deepEqual(await band.getMotion(), { rate: 100, sensors: 'both', accelRange: 8, gyroRange: 500 });
  assert.deepEqual(await band.getEvents(), { kinds: ['tap', 'double-tap'], tapThreshold: 1.5 });
  assert.equal((await band.getMic()).gainDb, -6);
  assert.equal((await band.getRadio()).txPower, 4);
  assert.equal((await band.command('STREAM_GET')).active, 0x73);
  assert.equal(sim.led.source, 'user');                                 // no identify flash
  assert.equal(sim.tap('tap'), true);
});

test('a second drop is repaired too, with what was set in between; stopped streams stay stopped', async () => {
  const { band, sim, events } = await connectSim();
  await band.setLed('#ff0000');
  await band.configure({ motion: 50, audio: true });
  sim.dropLink();
  await once(band, 'reconnected');
  assert.equal(sim.led.source, 'mic');                                  // audio is back, so the mic light is on
  await band.configure({ audio: false, events: true });
  await band.setLed('#0000ff');
  sim.dropLink();
  await once(band, 'reconnected');
  assert.deepEqual(band.streams, { ...ALL_OFF, motion: 50, events: true });
  assert.equal((await band.getLed()).b, 255);
  assert.deepEqual(events.map(([type]) => type), ['reconnecting', 'reconnected', 'reconnecting', 'reconnected']);
});

test('nothing was set: only GET_INFO is sent, and the band is usable again', async () => {
  const { band, sim } = await connectSim();
  const seen = watchCommands(sim);
  sim.dropLink();
  assert.deepEqual((await once(band, 'reconnected')).failed, []);
  assert.deepEqual(seen, ['GET_INFO']);
  assert.deepEqual(band.streams, ALL_OFF);
  await band.setLed('#00ff00');
});

test('unreachable for a while: it keeps trying, one attempt per pause, and comes back', async () => {
  const { band, sim, events } = await connectSim();
  await band.configure({ battery: true });
  sim.reachable = false;
  sim.dropLink();
  await sleep(60);
  assert.equal(band.reconnecting, true);
  assert.equal(events.length, 1);                                       // "reconnecting" is emitted once
  sim.reachable = true;
  const back = await once(band, 'reconnected');
  assert.ok(back.attempts >= 3, `${back.attempts} attempts`);
  assert.ok(back.seconds >= 0.05);
  assert.equal(band.streams.battery, true);
});

test('unreachable for the whole window: gives up with disconnect { reason: "lost", reconnect: "gave-up" }', async () => {
  const { band, sim, events } = await connectSim({ autoReconnect: true, reconnectSeconds: 0.08 });
  await band.setLed('#ff0000');
  let opens = 0;
  const open = sim.open.bind(sim);
  sim.open = () => { opens++; return open(); };
  sim.reachable = false;
  const started = Date.now();
  sim.dropLink();
  assert.deepEqual(events, [['reconnecting', { reason: 'lost', seconds: 0.08 }]]);
  const gone = await once(band, 'disconnect');
  const took = Date.now() - started;
  assert.deepEqual(gone, { reason: 'lost', reconnect: 'gave-up' });
  assert.ok(took >= 70 && took < 400, `gave up after about 80 ms (${took})`);
  assert.equal(band.reconnecting, false);
  assert.equal(band.connected, false);
  assert.ok(opens >= 3);
  // Never an attempt after the deadline, not even when the band shows up again.
  const before = opens;
  sim.reachable = true;
  await sleep(30);
  assert.equal(opens, before);
  assert.equal(events.length, 2);
  await fails(band.getLed(), 'not-connected', /Call Band\.connect/);
  // The memory is gone: a fresh connection does not get the old LED from anywhere.
  assert.deepEqual(band._memory, {});
});

test('an attempt that is still opening at the deadline is given up, and its link is closed when it arrives', async () => {
  const { band, sim, events } = await connectSim({ autoReconnect: true, reconnectSeconds: 0.05 });
  const open = sim.open.bind(sim);
  sim.open = async () => { await sleep(120); return open(); };          // a very slow radio
  sim.dropLink();
  assert.deepEqual(await once(band, 'disconnect'), { reason: 'lost', reconnect: 'gave-up' });
  assert.equal(events.length, 2);
  await sleep(120);
  assert.equal(sim._open, false);                                       // not left connected behind our back
  assert.equal(band.connected, false);
});

test('no reconnect after disconnect()', async () => {
  const { band, sim, events } = await connectSim();
  await band.configure({ motion: 50 });
  await band.disconnect();
  await sleep(20);
  assert.deepEqual(events, [['disconnect', { reason: 'closed' }]]);
  assert.equal(band.reconnecting, false);
  assert.equal(sim._open, false);
  assert.deepEqual(band._memory, {});
});

test('no reconnect after sleep() or restart(); they forget the settings; sleep shows the streams as off', async () => {
  for (const method of ['sleep', 'restart']) {
    const { band, sim, events } = await connectSim();
    await band.setLed('#ff0000');
    await band.configure({ motion: 50 });
    await band[method]();
    assert.deepEqual(band._memory, {});
    if (method === 'sleep') assert.deepEqual(band.streams, ALL_OFF);
    sim.step(200);
    await sleep(20);
    assert.deepEqual(events, [['disconnect', { reason: method }]]);
    assert.equal(band.reconnecting, false);
    assert.equal(sim._open, false);
  }
});

test('restart or sleep accepted, but the link only says "lost": still no reconnect within 3 seconds', async () => {
  for (const method of ['sleep', 'restart']) {
    const { band, events } = await connectSim();
    await band[method]();
    band._closed('lost');
    assert.deepEqual(events, [['disconnect', { reason: method }]]);
    assert.equal(band.reconnecting, false);
  }
});

test('sleep over USB keeps the link; a cable pulled later than 3 seconds is a real loss and is repaired', async (t) => {
  const { band, sim, events } = await connectSim({ autoReconnect: true }, { via: 'usb' });
  await band.setLed('#ff0000');
  await band.sleep();
  sim.step(200);
  assert.equal(band.connected, true);
  t.mock.timers.enable({ apis: ['Date'], now: Date.now() + 3001 });
  const seen = watchCommands(sim);
  sim.dropLink();
  assert.deepEqual(events, [['reconnecting', { reason: 'lost', seconds: 120 }]]);
  await once(band, 'reconnected');
  assert.deepEqual(seen, ['GET_INFO']);                                 // sleep() forgot the LED
});

test('a critical-battery notice on this link: the close is reported as lost at once, no retry', async () => {
  const { band, sim, events } = await connectSim();
  await band.configure({ battery: true });
  sim.onFrame(codec.encodeNotice('CRITICAL_BATTERY', 2));
  sim.dropLink();
  await sleep(20);
  assert.deepEqual(events, [['disconnect', { reason: 'lost' }]]);
  assert.equal(band.reconnecting, false);
  assert.equal(sim._open, false);
});

test('a low-battery notice does not stop the reconnect', async () => {
  const { band, sim, events } = await connectSim();
  sim.setBattery(10);
  sim.dropLink();
  await once(band, 'reconnected');
  assert.deepEqual(events.map(([type]) => type), ['reconnecting', 'reconnected']);
});

test('wrong band answers: the link is closed, no more retries, disconnect { reason: "wrong-band", found, wanted }', async () => {
  const { band, sim, events } = await connectSim();
  await band.setLed('#ff0000');
  let opens = 0;
  const open = sim.open.bind(sim);
  sim.open = () => { opens++; return open(); };
  const seen = watchCommands(sim);
  sim.reachable = false;
  sim.dropLink();
  sim.number = 17;                       // another band took its place
  sim.reachable = true;
  assert.deepEqual(await once(band, 'disconnect'), { reason: 'wrong-band', found: 17, wanted: 42 });
  assert.deepEqual(events.map(([type]) => type), ['reconnecting', 'disconnect']);
  assert.deepEqual(seen, ['GET_INFO']);                                 // nothing was set on the stranger
  assert.equal(sim._open, false);
  assert.equal(band.connected, false);
  assert.equal(band.reconnecting, false);
  assert.equal(band.number, 42);                                        // still describes the band you connected to
  assert.equal(band.name, 'Vexo-HACK-42');
  const before = opens;
  await sleep(30);
  assert.equal(opens, before);
});

test('connected with null (any band): the number it had is the one that must come back', async () => {
  const sim = new SimTransport(9, { realtime: false });
  const band = await Band.connect(null, { transport: sim, autoReconnect: true });
  band._reconnectPauseMs = 5;
  sim.dropLink();
  sim.number = 10;
  assert.deepEqual(await once(band, 'disconnect'), { reason: 'wrong-band', found: 10, wanted: 9 });
});

test('a command during the reconnect fails at once with not-connected and says to wait for "reconnected"', async () => {
  const { band, sim } = await connectSim();
  const queued = [band.getLed(), band.getBattery()];
  sim.reachable = false;
  sim.dropLink();
  for (const call of [...queued, band.getInfo(), band.setLed('#ff0000'), band.configure({ motion: 50 }), band.storage.put(1, 'x'),
    band.command('LED_GET')]) {
    const started = Date.now();
    await fails(call, 'not-connected', /dropped and the library is reconnecting.*wait for the "reconnected" event/i);
    assert.ok(Date.now() - started < 50);
  }
  sim.reachable = true;
  await once(band, 'reconnected');
  assert.deepEqual(await band.getLed(), { r: 0, g: 0, b: 0, brightness: 0, pattern: 'off', period: 1000 });   // the refused setLed never went out
  assert.equal(await band.storage.get(1), null);
});

test('disconnect() while reconnecting cancels it: disconnect { reason: "closed" }, and no attempt afterwards', async () => {
  const { band, sim, events } = await connectSim();
  await band.setLed('#ff0000');
  let opens = 0;
  const open = sim.open.bind(sim);
  sim.open = () => { opens++; return open(); };
  sim.reachable = false;
  sim.dropLink();
  await sleep(20);
  await band.disconnect();
  assert.deepEqual(events, [['reconnecting', { reason: 'lost', seconds: 120 }], ['disconnect', { reason: 'closed' }]]);
  assert.equal(band.reconnecting, false);
  assert.equal(band.connected, false);
  const before = opens;
  sim.reachable = true;
  await sleep(30);
  assert.equal(opens, before);
  assert.equal(sim._open, false);
  await band.disconnect();                                              // again: nothing
  assert.equal(events.length, 2);
  await fails(band.getLed(), 'not-connected', /Call Band\.connect/);
});

test('disconnect() inside the "reconnecting" handler, and while an attempt is opening, leave nothing connected', async () => {
  const first = await connectSim();
  first.band.on('reconnecting', () => first.band.disconnect());
  first.sim.dropLink();
  await sleep(20);
  assert.deepEqual(first.events.map(([type, e]) => `${type} ${e.reason}`), ['reconnecting lost', 'disconnect closed']);
  assert.equal(first.sim._open, false);

  const second = await connectSim();
  const open = second.sim.open.bind(second.sim);
  second.sim.open = async () => { await sleep(30); return open(); };
  second.sim.dropLink();
  await sleep(5);
  await second.band.disconnect();
  await sleep(60);
  assert.equal(second.sim._open, false);
  assert.deepEqual(second.events.map(([type]) => type), ['reconnecting', 'disconnect']);
});

test('a setting the band refuses during the restore is skipped and named in `failed`', async () => {
  const { band, sim } = await connectSim();
  await band.setLed('#ff0000');
  await band.setMicGain(-6);
  await band.configure({ motion: 50, events: true });
  band._memory.MIC_SET_GAIN = { gain_half_db: 200 };                    // something this band will answer BAD_ARG to
  sim.dropLink();
  const back = await once(band, 'reconnected');
  assert.deepEqual(back.failed, ['MIC_SET_GAIN']);
  assert.equal((await band.getLed()).r, 255);                           // the rest was restored
  assert.deepEqual(band.streams, { ...ALL_OFF, motion: 50, events: true });
});

test('the link drops again in the middle of the restore: back to the retry loop, same window, one "reconnected"', async () => {
  const { band, sim, events } = await connectSim();
  await band.setLed('#ff0000');
  await band.configure({ motion: 50 });
  let drops = 2;
  const command = sim._command.bind(sim);
  sim._command = (frame) => {
    if (codec.decodeCommand(frame).opcode === 'LED_SET' && drops-- > 0) return sim.dropLink();
    command(frame);
  };
  sim.dropLink();
  const back = await once(band, 'reconnected');
  assert.equal(back.attempts, 3);
  assert.deepEqual(back.failed, []);
  assert.deepEqual(events.map(([type]) => type), ['reconnecting', 'reconnected']);
  assert.equal(band.streams.motion, 50);
  assert.equal((await band.getLed()).r, 255);
});

test('a band that opens but does not answer GET_INFO is hung up on and tried again', async () => {
  const INFO = { proto_major: 1, proto_minor: 0, fw_major: 1, fw_minor: 0, fw_patch: 0, hw: 1, band_no: 42, caps: 0x7ff, max_frame: 244, uptime_ms: 0 };
  class Moody extends Transport {
    kind = 'custom';
    log = [];
    silent = 0;                          // how many GET_INFOs to ignore
    async open() { this.log.push('open'); }
    async reopen() { this.log.push('reopen'); }
    async close() { this.log.push('close'); }
    async send(frame) {
      const cmd = codec.decodeCommand(frame);
      if (cmd.opcode === 'GET_INFO' && this.silent-- > 0) return;
      this.onFrame(codec.encodeResponse(cmd.seq, cmd.opcode, 'OK', cmd.opcode === 'GET_INFO' ? INFO : {}));
    }
  }
  const transport = new Moody();
  const band = await Band.connect(42, { transport, autoReconnect: true, timeoutMs: 20 });
  band._reconnectPauseMs = 5;
  await band.setLed('#ff0000');
  transport.silent = 1;
  transport.onClose('lost');
  const back = await once(band, 'reconnected');
  assert.equal(back.attempts, 2);
  // D3: reopen() is used when the transport has one; a half-open link is closed before the next try.
  assert.deepEqual(transport.log, ['open', 'reopen', 'close', 'reopen']);
  await band.disconnect();
  assert.equal(transport.log.at(-1), 'close');
});

test('the pretend band running by itself: drop it, and the data is back without doing anything', async () => {
  const sim = new SimTransport(42);
  const band = await Band.connect(42, { transport: sim, autoReconnect: true });
  band._reconnectPauseMs = 10;
  const motion = [];
  band.on('motion', (m) => motion.push(m));
  await band.configure({ motion: 100 });
  sim.reachable = false;
  sim.dropLink();
  setTimeout(() => { sim.reachable = true; }, 40);
  await once(band, 'reconnected');
  motion.length = 0;
  await sleep(300);
  assert.ok(motion.length >= 10, `got ${motion.length} samples after the reconnect`);
  await band.disconnect();
});
