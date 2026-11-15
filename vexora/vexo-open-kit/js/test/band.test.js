// Band (the API in docs/api.md) against the simulator. The simulator runs with
// realtime: false, so nothing happens until a test calls sim.step(ms).
import test from 'node:test';
import assert from 'node:assert/strict';
import { Band, SimTransport, Transport, VexoError, codec } from '../src/index.js';
import { hex } from './helpers.js';

// These tests are about what a band with firmware 1.0 does, so the pretend band is one
// (firmware 1.1 has its own file, v11.test.js). The computer's clock is pinned at NOW, and
// the pretend band starts at band time 0, so an event with `t` has `time` NOW + t.
const NOW = 1794484800000;

async function connectSim(number = 42, simOptions = {}, bandOptions = {}) {
  const sim = new SimTransport(number, { realtime: false, firmware: '1.0', ...simOptions });
  const band = await Band.connect(number, { transport: sim, now: () => NOW, ...bandOptions });
  return { sim, band };
}

/** Collect every event of one type in a list. */
function collect(band, type) {
  const list = [];
  band.on(type, (e) => list.push(e));
  return list;
}

const fails = (promise, code) => assert.rejects(promise, (e) => {
  assert.ok(e instanceof VexoError, `expected a VexoError, got ${e}`);
  assert.equal(e.code, code);
  assert.ok(e.message.length > 10);
  return true;
});

const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) <= eps, `${a} is not ${b}`);

// ---------------------------------------------------------------- connecting

test('connect fills in identity and info', async () => {
  const { band } = await connectSim(42);
  assert.equal(band.number, 42);
  assert.equal(band.name, 'Vexo-HACK-42');
  assert.equal(band.connected, true);
  assert.equal(band.transportKind, 'sim');
  assert.deepEqual(band.info, {
    number: 42, protocol: '1.0', firmware: '1.0.0', features: [], hardware: 1,
    capabilities: ['accel', 'gyro', 'events', 'steps', 'mic', 'led', 'battery', 'temperature', 'storage', 'tx-power', 'usb'],
    maxFrame: 244, uptimeMs: 0,
  });
  assert.deepEqual(band.streams, { motion: 0, events: false, activity: false, audio: false, battery: false, temperature: false, link: false, micLevel: false });
  assert.equal(await band.getName(), 'Vexo-HACK-42');
  await band.disconnect();
  assert.equal(band.connected, false);
});

test('getInfo asks again', async () => {
  const { band, sim } = await connectSim(42);
  sim.step(1500);
  assert.equal((await band.getInfo()).uptimeMs, 1500);
  assert.equal(band.info.uptimeMs, 1500);
});

test("transport: 'sim' by name, and a number given as text", async () => {
  const band = await Band.connect('007', { transport: 'sim' });
  assert.equal(band.name, 'Vexo-HACK-07');
  assert.equal(band.transportKind, 'sim');
  await band.disconnect();
});

test('wrong-band: the message names both numbers and the link is closed', async () => {
  const sim = new SimTransport(17, { realtime: false });
  await assert.rejects(Band.connect(42, { transport: sim }), (e) => {
    assert.equal(e.code, 'wrong-band');
    assert.match(e.message, /Vexo-HACK-17/);
    assert.match(e.message, /Vexo-HACK-42/);
    return true;
  });
  await fails(sim.send(codec.encodeCommand(0, 'GET_INFO')), 'not-connected');
});

test('wrong-band: a band with no number yet (0) when a number was asked for', async () => {
  const sim = new SimTransport(0, { realtime: false });
  await fails(Band.connect(42, { transport: sim }), 'wrong-band');
});

test('null number accepts any band', async () => {
  const band = await Band.connect(null, { transport: new SimTransport(17, { realtime: false }) });
  assert.equal(band.number, 17);
  assert.equal(band.name, 'Vexo-HACK-17');
  const unset = await Band.connect(null, { transport: new SimTransport(0, { realtime: false }) });
  assert.equal(unset.name, 'Vexo-HACK-00');
  const any = await Band.connect(null, { transport: 'sim' });
  assert.equal(typeof any.number, 'number');
  await any.disconnect();
});

test('bad input to connect', async () => {
  await fails(Band.connect(1000, { transport: 'sim' }), 'bad-arg');
  await fails(Band.connect('abc', { transport: 'sim' }), 'bad-arg');
  await fails(Band.connect(42, { transport: 'carrier-pigeon' }), 'bad-transport');
  await fails(Band.connect(42), 'no-web-bluetooth');                    // Node has no Web Bluetooth
  await fails(Band.connect(42, { transport: 'serial' }), 'no-web-serial');
});

// ---------------------------------------------------------------- configure and the events

test('configure: motion in g and degrees/second, one event per sample', async () => {
  const { band, sim } = await connectSim();
  const motion = collect(band, 'motion');
  const streams = await band.configure({ motion: true });
  assert.equal(streams.motion, 25);                                     // true = current rate, 25 at first
  assert.equal(band.streams.motion, 25);
  sim.step(1000);
  assert.equal(motion.length, 20);                                      // 2 frames of 10 samples (25 per second)
  for (let i = 1; i < motion.length; i++) assert.equal(motion[i].t - motion[i - 1].t, 40);
  assert.deepEqual(Object.keys(motion[0]), ['t', 'ax', 'ay', 'az', 'gx', 'gy', 'gz', 'n', 'time']);
  near(motion[0].az, 1, 0.1);                                           // gravity on z, in g
  assert.ok(motion.some((m) => Math.abs(m.gx) > 1 && Math.abs(m.gx) <= 40));

  await band.configure({ motion: 100 });
  assert.equal(band.streams.motion, 100);
  assert.deepEqual(await band.getMotion(), { rate: 100, sensors: 'both', accelRange: 2, gyroRange: 2000 });   // only the rate changed
  motion.length = 0;
  sim.step(1000);
  assert.equal(motion.length, 100);

  await band.configure({ motion: false });
  assert.equal(band.streams.motion, 0);
  motion.length = 0;
  sim.step(1000);
  assert.equal(motion.length, 0);
});

test('motion unit conversion from a known frame', async () => {
  const { band, sim } = await connectSim();
  const motion = collect(band, 'motion');
  sim.onFrame(codec.encodeStream('motion', {
    t_ms: 1000, period_ms: 20,
    samples: [{ ax: 500, ay: -1000, az: 250, gx: 125, gy: -2000, gz: 0 }, { ax: -16000, ay: 16000, az: 1, gx: 20000, gy: -20000, gz: -1 }],
  }));
  assert.deepEqual(motion, [
    { t: 1000, ax: 0.5, ay: -1, az: 0.25, gx: 12.5, gy: -200, gz: 0, n: null, time: NOW + 1000 },
    { t: 1020, ax: -16, ay: 16, az: 0.001, gx: 2000, gy: -2000, gz: -0.1, n: null, time: NOW + 1020 },
  ]);
});

test('configure: unknown key and bad rate change nothing', async () => {
  const { band } = await connectSim();
  await fails(band.configure({ motion: 50, heartRate: true }), 'bad-stream');
  await fails(band.configure({ events: true, motion: 30 }), 'bad-arg');
  await fails(band.configure({ temperature: 300 }), 'bad-arg');
  assert.equal((await band.command('STREAM_GET')).active, 0);
  assert.equal((await band.getMotion()).rate, 25);
});

test('configure: streams you do not mention keep their setting', async () => {
  const { band } = await connectSim();
  await band.configure({ motion: 50, events: true, battery: true });
  await band.configure({ activity: true, events: false });
  assert.deepEqual(band.streams, { motion: 50, events: false, activity: true, audio: false, battery: true, temperature: false, link: false, micLevel: false });
  assert.equal((await band.command('STREAM_GET')).active, 0b10101);
});

test('events: tap, double-tap, orientation with face, and the rest', async () => {
  const { band, sim } = await connectSim();
  const all = collect(band, 'event');
  const taps = collect(band, 'tap');
  const doubles = collect(band, 'double-tap');
  const turns = collect(band, 'orientation');
  assert.equal(sim.tap('tap'), false);                                  // events are off: nothing is sent
  await band.configure({ events: true });
  sim.step(500);
  assert.equal(sim.tap('tap'), true);
  sim.tap('double-tap');
  sim.tap('orientation', 5);
  for (const kind of ['free-fall', 'tilt', 'motion-start', 'motion-stop']) sim.tap(kind);
  assert.deepEqual(taps, [{ t: 500, kind: 'tap', arg: 0, time: NOW + 500 }]);
  assert.equal(doubles.length, 1);
  assert.deepEqual(turns, [{ t: 500, kind: 'orientation', arg: 5, face: '-z', time: NOW + 500 }]);
  assert.deepEqual(all.map((e) => e.kind), ['tap', 'double-tap', 'orientation', 'free-fall', 'tilt', 'motion-start', 'motion-stop']);
  sim.step(10000);
  assert.equal(all.length, 7);                                          // no scripted taps when realtime is off
});

test('setEvents / getEvents: kinds and the tap threshold in g', async () => {
  const { band, sim } = await connectSim();
  assert.deepEqual(await band.getEvents(), { kinds: ['tap', 'double-tap', 'free-fall', 'tilt', 'orientation', 'motion'], tapThreshold: 0.8 });
  assert.deepEqual(await band.setEvents({ kinds: ['tap', 'motion'], tapThreshold: 1.2 }), { kinds: ['tap', 'motion'], tapThreshold: 1.2 });
  assert.deepEqual(await band.command('EVENT_GET'), { event_mask: 0b100001, tap_threshold_mg: 1200 });
  assert.deepEqual(await band.setEvents({ tapThreshold: 0.2 }), { kinds: ['tap', 'motion'], tapThreshold: 0.2 });   // kinds kept
  assert.deepEqual(await band.setEvents({ kinds: ['double-tap'] }), { kinds: ['double-tap'], tapThreshold: 0.2 });  // threshold kept
  await band.configure({ events: true });
  const events = collect(band, 'event');
  sim.tap('tap');
  sim.tap('motion-stop');
  sim.tap('double-tap');
  assert.deepEqual(events.map((e) => e.kind), ['double-tap']);          // the mask is honoured
  await fails(band.setEvents({ kinds: ['shake'] }), 'bad-arg');
  await fails(band.setEvents({ tapThreshold: 0.1 }), 'bad-arg');
  await fails(band.setEvents({ kinds: 'tap' }), 'bad-arg');
});

test('activity, temperature, link, mic-level and battery events', async () => {
  const { band, sim } = await connectSim();
  const activity = collect(band, 'activity');
  const temperature = collect(band, 'temperature');
  const link = collect(band, 'link');
  const mic = collect(band, 'mic-level');
  const battery = collect(band, 'battery');
  await band.configure({ activity: true, temperature: 2, link: true, micLevel: true, battery: true });
  assert.deepEqual(band.streams, { motion: 0, events: false, activity: true, audio: false, battery: true, temperature: true, link: true, micLevel: true });
  assert.deepEqual(battery, [{ percent: 87, millivolts: 4083, state: 'battery', charging: false, time: NOW }]);   // once at start
  sim.step(4000);

  assert.equal(activity.length, 4);
  assert.deepEqual(Object.keys(activity[0]), ['t', 'steps', 'cadence', 'state', 'motion', 'time']);
  assert.equal(activity[0].state, 'walk');
  assert.ok(activity[3].steps > activity[0].steps);
  assert.ok(activity[0].motion > 0.2 && activity[0].motion < 0.4);     // g

  assert.deepEqual(temperature.map((e) => e.t), [2000, 4000]);          // every 2 seconds, as asked
  assert.ok(temperature[0].celsius > 30 && temperature[0].celsius < 32);
  near(temperature[0].celsius * 4, Math.round(temperature[0].celsius * 4));   // 0.25 degree steps

  assert.equal(link.length, 4);
  assert.deepEqual(Object.keys(link[0]), ['t', 'rssi', 'txPower', 'phy', 'time']);
  assert.ok(link[0].rssi < -40 && link[0].rssi > -70);
  assert.equal(link[0].txPower, -8);
  assert.equal(link[0].phy, '2M');

  assert.equal(mic.length, 40);
  assert.deepEqual(Object.keys(mic[0]), ['t', 'peakDb', 'rmsDb', 'level', 'time']);
  for (const m of mic) {
    assert.ok(m.level >= 0 && m.level <= 1);
    near(m.level, 1 - m.rmsDb / 96);
  }
  assert.ok(new Set(mic.map((m) => m.rmsDb)).size > 3, 'the level swells');

  sim.setBattery(50, 'charging');
  assert.deepEqual(battery[1], { percent: 50, millivolts: 3750, state: 'charging', charging: true, time: NOW });
  sim.setBattery(100, 'charged');
  assert.deepEqual(battery[2], { percent: 100, millivolts: 4200, state: 'charged', charging: false, time: NOW });
  sim.setBattery(60);
  sim.step(60000);
  assert.equal(battery.at(-1).percent, 59);                             // slow drain
});

test('mic level follows the gain, and conversions clamp', async () => {
  const { band, sim } = await connectSim();
  const mic = collect(band, 'mic-level');
  await band.configure({ micLevel: true });
  sim.step(2000);
  const loud = mic.reduce((sum, m) => sum + m.level, 0) / mic.length;
  await band.setMicGain(-20);
  mic.length = 0;
  sim.step(2000);
  const quiet = mic.reduce((sum, m) => sum + m.level, 0) / mic.length;
  assert.ok(quiet < loud - 0.1, `${quiet} should be well below ${loud}`);

  mic.length = 0;
  sim.onFrame(codec.encodeStream('mic_level', { t_ms: 1, peak_db: 0, rms_db: 0 }));
  sim.onFrame(codec.encodeStream('mic_level', { t_ms: 2, peak_db: 96, rms_db: 96 }));
  sim.onFrame(codec.encodeStream('mic_level', { t_ms: 3, peak_db: 200, rms_db: 200 }));
  assert.deepEqual(mic.map((m) => m.level), [1, 0, 0]);
  assert.deepEqual(mic[2], { t: 3, peakDb: 200, rmsDb: 200, level: 0, time: NOW + 3 });
});

test('link event: rssi 127 and phy 0 become null; unknown things are ignored', async () => {
  const { band, sim } = await connectSim();
  const link = collect(band, 'link');
  const events = collect(band, 'event');
  const notices = collect(band, 'notice');
  sim.onFrame(codec.encodeStream('link', { t_ms: 5, rssi_dbm: 127, tx_dbm: 4, phy: 0 }));
  sim.onFrame(codec.encodeStream('link', { t_ms: 6, rssi_dbm: -60, tx_dbm: 4, phy: 1 }));
  assert.deepEqual(link, [
    { t: 5, rssi: null, txPower: 4, phy: null, time: NOW + 5 }, { t: 6, rssi: -60, txPower: 4, phy: '1M', time: NOW + 6 },
  ]);
  sim.onFrame(codec.encodeStream('event', { t_ms: 9, event: 200, arg: 3 }));    // unknown event code
  sim.onFrame(codec.encodeNotice(200, 1));                                      // unknown notice
  sim.onFrame(Uint8Array.of(0x7f, 1, 2));                                       // unknown frame type
  sim.onFrame(Uint8Array.of(0x11, 1));                                          // broken frame
  sim.onFrame(codec.encodeResponse(99, 'LED_GET', 'DENIED'));                   // a response nobody asked for
  assert.equal(events.length, 0);
  assert.equal(notices.length, 0);
  assert.equal((await band.getLed()).pattern, 'off');                           // still working
});

test('notices', async () => {
  const { band, sim } = await connectSim();
  const notices = collect(band, 'notice');
  sim.setBattery(14);
  sim.setBattery(13);                                                   // still low: no second notice
  sim.onFrame(codec.encodeNotice('CRITICAL_BATTERY', 4));
  sim.onFrame(codec.encodeNotice('STREAM_DROPPED', 3));
  sim.onFrame(codec.encodeNotice('USB_POWER', 1));
  assert.deepEqual(notices, [
    { kind: 'low-battery', arg: 14, time: NOW },
    { kind: 'critical-battery', arg: 4, time: NOW },
    { kind: 'stream-dropped', arg: 3, time: NOW },
    { kind: 'usb-power', arg: 1, time: NOW },
  ]);
});

test('on returns a function that stops listening; off works; a broken handler does not stop the others', async (t) => {
  const { band, sim } = await connectSim();
  await band.configure({ events: true });
  const errors = t.mock.method(console, 'error', () => {});
  const seen = [];
  const stop = band.on('tap', () => seen.push('a'));
  const b = () => seen.push('b');
  band.on('tap', () => { throw new Error('student bug'); });
  band.on('tap', b);
  sim.tap();
  assert.deepEqual(seen, ['a', 'b']);
  assert.equal(errors.mock.callCount(), 1);
  stop();
  band.off('tap', b);
  sim.tap();
  assert.deepEqual(seen, ['a', 'b']);
  assert.deepEqual(await band.getEvents(), { kinds: ['tap', 'double-tap', 'free-fall', 'tilt', 'orientation', 'motion'], tapThreshold: 0.8 });
});

// ---------------------------------------------------------------- audio and the mic light

test('audio: valid silent Opus packets, 50 per second, seq restarts at 0', async () => {
  const { band, sim } = await connectSim();
  const audio = collect(band, 'audio');
  await band.configure({ audio: true });
  assert.equal(band.streams.audio, true);
  sim.step(1000);
  assert.equal(audio.length, 50);
  assert.deepEqual(audio.map((a) => a.seq), [...Array(50).keys()]);
  for (const { opus } of audio) {
    assert.ok(opus instanceof Uint8Array);
    assert.equal(opus[0], 0xbb);
    assert.equal(opus[1], 0x41);
    assert.ok(opus[2] >= 20 && opus[2] <= 80);
    assert.equal(opus.length, 3 + opus[2]);
    assert.ok(opus.subarray(3).every((b) => b === 0));
  }
  assert.ok(new Set(audio.map((a) => a.opus.length)).size > 5, 'the size swells');
  assert.equal((await band.getRadio()).intervalMs, 15);                 // faster link while audio streams

  await band.configure({ audio: false });
  audio.length = 0;
  sim.step(200);
  assert.equal(audio.length, 0);
  await band.configure({ audio: true });
  sim.step(100);
  assert.deepEqual(audio.map((a) => a.seq), [0, 1, 2, 3, 4]);           // starts again at 0
});

test('audio: audioPackets are played in a loop', async () => {
  const packets = [Uint8Array.of(1, 2, 3), Uint8Array.of(4), Uint8Array.of(5, 6)];
  const { band, sim } = await connectSim(42, { audioPackets: packets });
  const audio = collect(band, 'audio');
  await band.configure({ audio: true });
  sim.step(100);
  assert.deepEqual(audio.map((a) => hex(a.opus)), ['010203', '04', '0506', '010203', '04']);
});

test('while the microphone is on (audio or the loudness meter): setLed, ledOff and identify are denied; the LED comes back afterwards', async () => {
  const { band, sim } = await connectSim();
  const lights = [];
  sim.onLed = (led) => lights.push(led);
  await band.setLed('#00ff00', { brightness: 0.5 });
  assert.deepEqual(lights.at(-1), { r: 0, g: 255, b: 0, brightness: 128, pattern: 1, period_ms: 1000, source: 'user' });

  await band.configure({ audio: true, micLevel: true });
  assert.deepEqual(lights.at(-1), { r: 0, g: 0, b: 255, brightness: 255, pattern: 1, period_ms: 1000, source: 'mic' });
  for (const attempt of [band.setLed('#ff0000'), band.ledOff(), band.identify()]) {
    await assert.rejects(attempt, (e) => {
      assert.equal(e.code, 'denied');
      assert.match(e.message, /microphone/);
      assert.match(e.message, /blue/);
      assert.match(e.message, /audio: false, micLevel: false/);
      return true;
    });
  }
  // The stored setting is still readable, and unchanged.
  assert.deepEqual(await band.getLed(), { r: 0, g: 255, b: 0, brightness: 128 / 255, pattern: 'solid', period: 1000 });
  assert.equal(sim.led.source, 'mic');

  // The loudness meter alone keeps the microphone on: still blue, still denied.
  await band.configure({ audio: false });
  assert.equal(band.streams.micLevel, true);
  assert.equal(sim.led.source, 'mic');
  await assert.rejects(band.setLed('#ff0000'), (e) => e.code === 'denied');
  await assert.rejects(band.identify(), (e) => e.code === 'denied');

  await band.configure({ micLevel: false });
  assert.deepEqual(sim.led, { r: 0, g: 255, b: 0, brightness: 128, pattern: 1, period_ms: 1000, source: 'user' });
  assert.deepEqual(lights.at(-1), sim.led);
  await band.setLed('#ff0000');                                         // and LED commands work again
  assert.equal(sim.led.r, 255);

  // The meter on its own, with audio never started, lights it as well.
  await band.configure({ micLevel: true });
  assert.equal(lights.at(-1).source, 'mic');
  await assert.rejects(band.ledOff(), (e) => e.code === 'denied');
  await band.configure({ micLevel: false });
  assert.equal(sim.led.source, 'user');
});

// ---------------------------------------------------------------- LED

test('setLed: colours, options, getLed, ledOff', async () => {
  const { band, sim } = await connectSim();
  await band.setLed('#ff8800');
  assert.deepEqual(await band.getLed(), { r: 255, g: 136, b: 0, brightness: 1, pattern: 'solid', period: 1000 });
  await band.setLed(1, 2, 3);
  assert.deepEqual(await band.command('LED_GET'), { r: 1, g: 2, b: 3, brightness: 255, pattern: 1, period_ms: 1000 });
  await band.setLed('#ff8800', { brightness: 0.5, pattern: 'blink', period: 500 });
  assert.deepEqual(await band.command('LED_GET'), { r: 255, g: 136, b: 0, brightness: 128, pattern: 2, period_ms: 500 });
  await band.setLed(0, 0, 255, { pattern: 'breathe', brightness: 0.2, period: 10000 });
  const led = await band.getLed();
  assert.equal(led.pattern, 'breathe');
  near(led.brightness, 51 / 255);
  for (const pattern of ['flash', 'rainbow', 'off']) {
    await band.setLed('00ff00', { pattern });
    assert.equal((await band.getLed()).pattern, pattern);
  }
  await band.ledOff();
  assert.deepEqual(await band.getLed(), { r: 0, g: 0, b: 0, brightness: 0, pattern: 'off', period: 1000 });
  assert.equal(sim.led.pattern, 0);

  await fails(band.setLed('green'), 'bad-colour');
  await fails(band.setLed('#ff88'), 'bad-colour');
  await fails(band.setLed(256, 0, 0), 'bad-colour');
  await fails(band.setLed(1, 2), 'bad-colour');
  await fails(band.setLed('#ffffff', { brightness: 2 }), 'bad-arg');
  await fails(band.setLed('#ffffff', { pattern: 'pulse' }), 'bad-arg');
  await fails(band.setLed('#ffffff', { period: 50 }), 'bad-arg');
});

test('identify: white blink for N seconds, then the previous light returns', async () => {
  const { band, sim } = await connectSim();
  await band.setLed('#0000ff');
  await band.identify();
  assert.equal(sim.led.source, 'identify');
  assert.deepEqual([sim.led.r, sim.led.g, sim.led.b, sim.led.pattern, sim.led.period_ms], [255, 255, 255, 2, 250]);
  sim.step(4900);
  assert.equal(sim.led.source, 'identify');
  const lights = [];
  sim.onLed = (led) => lights.push(led);
  sim.step(200);
  assert.equal(lights.length, 1);
  assert.deepEqual(lights[0], { r: 0, g: 0, b: 255, brightness: 255, pattern: 1, period_ms: 1000, source: 'user' });
  await band.identify(10);
  sim.step(9900);
  assert.equal(sim.led.source, 'identify');
  await fails(band.identify(0), 'bad-arg');
  await fails(band.identify(31), 'bad-arg');
});

test('a low battery dims the LED', async () => {
  const { band, sim } = await connectSim();
  await band.setLed('#ffffff');
  sim.setBattery(10);
  assert.equal(sim.led.brightness, 64);
  sim.setBattery(10, 'charging');
  assert.equal(sim.led.brightness, 255);
});

// ---------------------------------------------------------------- the other controls

test('setMotion / getMotion: what you leave out stays', async () => {
  const { band, sim } = await connectSim();
  assert.deepEqual(await band.getMotion(), { rate: 25, sensors: 'both', accelRange: 2, gyroRange: 2000 });
  assert.deepEqual(await band.setMotion({ rate: 100, sensors: 'both', accelRange: 8, gyroRange: 500 }), { rate: 100, sensors: 'both', accelRange: 8, gyroRange: 500 });
  assert.deepEqual(await band.setMotion({ gyroRange: 0 }), { rate: 100, sensors: 'both', accelRange: 8, gyroRange: 0 });
  assert.deepEqual(await band.setMotion({ accelRange: 16 }), { rate: 100, sensors: 'both', accelRange: 16, gyroRange: 0 });
  assert.deepEqual(await band.getMotion(), { rate: 100, sensors: 'both', accelRange: 16, gyroRange: 0 });
  const motion = collect(band, 'motion');
  await band.configure({ motion: true });
  assert.equal(band.streams.motion, 100);
  sim.step(100);
  assert.equal(motion.length, 10);
  assert.ok(motion.every((m) => m.gx === 0 && m.gy === 0 && m.gz === 0), 'gyro off = zeros');
  await band.setMotion({ rate: 50 });
  assert.equal(band.streams.motion, 50);                                // streams follows a rate change
  await fails(band.setMotion({ rate: 30 }), 'bad-arg');
  await fails(band.setMotion({ accelRange: 3 }), 'bad-arg');
  await fails(band.setMotion({ gyroRange: 100 }), 'bad-arg');
});

test('mic gain in dB', async () => {
  const { band } = await connectSim();
  assert.deepEqual(await band.getMic(), { gainDb: 20, sampleRate: 16000, frameMs: 20, channels: 1, bitrate: 24000 });
  assert.equal(await band.setMicGain(10), 10);
  assert.equal((await band.command('MIC_GET')).gain_half_db, 60);
  assert.equal(await band.setMicGain(-20), -20);
  assert.equal(await band.setMicGain(0.5), 0.5);
  assert.equal((await band.getMic()).gainDb, 0.5);
  await fails(band.setMicGain(21), 'bad-arg');
  await fails(band.setMicGain(-20.5), 'bad-arg');
});

test('radio: tx power and state over Bluetooth', async () => {
  const { band } = await connectSim();
  const radio = await band.getRadio();
  assert.deepEqual(Object.keys(radio), ['txPower', 'rssi', 'phy', 'intervalMs', 'mtu']);
  assert.equal(radio.txPower, -8);
  assert.ok(radio.rssi <= -50 && radio.rssi >= -60);
  assert.equal(radio.phy, '2M');
  assert.equal(radio.intervalMs, 30);                                   // 24 x 1.25 ms
  assert.equal(radio.mtu, 247);
  for (const dbm of [-20, -16, -12, -8, -4, 0, 4]) {
    assert.equal(await band.setTxPower(dbm), dbm);
    assert.equal((await band.getRadio()).txPower, dbm);
  }
  await fails(band.setTxPower(8), 'bad-arg');
  await fails(band.setTxPower(-10), 'bad-arg');
});

test('battery once', async () => {
  const { band, sim } = await connectSim();
  assert.deepEqual(await band.getBattery(), { percent: 87, millivolts: 4083, state: 'battery', charging: false });
  sim.setBattery(100, 'charged');
  assert.deepEqual(await band.getBattery(), { percent: 100, millivolts: 4200, state: 'charged', charging: false });
});

test('restart and sleep: OK, then the link closes 200 ms later', async () => {
  for (const [method, reason] of [['restart', 'restart'], ['sleep', 'sleep']]) {
    const { band, sim } = await connectSim();
    const gone = collect(band, 'disconnect');
    await band[method]();
    sim.step(199);
    assert.equal(band.connected, true);
    sim.step(1);
    assert.equal(band.connected, false);
    assert.deepEqual(gone, [{ reason }]);
  }
});

test('setBandNumber is denied over Bluetooth and works over USB', async () => {
  const ble = await connectSim(42);
  await assert.rejects(ble.band.setBandNumber(7), (e) => {
    assert.equal(e.code, 'denied');
    assert.match(e.message, /USB/);
    return true;
  });
  assert.equal(ble.band.number, 42);
  assert.equal(await ble.band.getName(), 'Vexo-HACK-42');

  const usb = await connectSim(42, { via: 'usb' });
  assert.equal(await usb.band.setBandNumber(7), 7);
  assert.equal(usb.band.number, 7);
  assert.equal(usb.band.name, 'Vexo-HACK-07');
  assert.equal(usb.band.info.number, 7);
  assert.equal(await usb.band.getName(), 'Vexo-HACK-07');
  await fails(usb.band.setBandNumber(0), 'bad-arg');
  await fails(usb.band.setBandNumber(1000), 'bad-arg');
  await fails(usb.band.command('SET_BAND_NUMBER', { number: 9, magic: 1 }), 'bad-arg');   // wrong safety number
  // The number is kept by the band: a new connection sees it.
  await usb.band.disconnect();
  const again = await Band.connect(7, { transport: usb.sim });
  assert.equal(again.name, 'Vexo-HACK-07');
});

// ---------------------------------------------------------------- command() and the band's own checks

test('command(): contract names in, contract fields out', async () => {
  const { band } = await connectSim();
  assert.deepEqual(await band.command('IMU_SET', { rate_hz: 50, accel_range_g: 2, gyro_range_dps: 2000 }), { rate_hz: 50, accel_range_g: 2, gyro_range_dps: 2000 });
  assert.deepEqual(await band.command('LED_GET'), { r: 0, g: 0, b: 0, brightness: 0, pattern: 0, period_ms: 1000 });
  assert.deepEqual(await band.command('TEMP_SET_INTERVAL', { seconds: 9 }), { seconds: 9 });
  assert.deepEqual(await band.command('STREAM_START', { mask: 0b11 }), { active: 0b11 });
  assert.deepEqual(band.streams.motion, 50);                            // band.streams follows raw commands too
  assert.equal(band.streams.events, true);
  await fails(band.command('NOT_A_COMMAND'), 'bad-arg');
  await fails(band.command('IDENTIFY', { seconds: 999 }), 'bad-arg');   // does not fit in the field
  await fails(band.command('IDENTIFY', {}), 'bad-arg');
});

test('BAD_ARG from the band, and nothing changes', async () => {
  const { band } = await connectSim();
  const bad = [
    ['IMU_SET', { rate_hz: 30, accel_range_g: 2, gyro_range_dps: 2000 }],
    ['IMU_SET', { rate_hz: 25, accel_range_g: 3, gyro_range_dps: 2000 }],
    ['IMU_SET', { rate_hz: 25, accel_range_g: 2, gyro_range_dps: 100 }],
    ['EVENT_SET', { event_mask: 0x40, tap_threshold_mg: 800 }],
    ['EVENT_SET', { event_mask: 1, tap_threshold_mg: 199 }],
    ['EVENT_SET', { event_mask: 1, tap_threshold_mg: 8001 }],
    ['MIC_SET_GAIN', { gain_half_db: 81 }],
    ['RADIO_SET_TX_POWER', { dbm: 8 }],
    ['LED_SET', { r: 1, g: 1, b: 1, brightness: 1, pattern: 6, period_ms: 1000 }],
    ['LED_SET', { r: 1, g: 1, b: 1, brightness: 1, pattern: 2, period_ms: 99 }],
    ['LED_SET', { r: 1, g: 1, b: 1, brightness: 1, pattern: 5, period_ms: 10001 }],
    ['TEMP_SET_INTERVAL', { seconds: 0 }],
    ['IDENTIFY', { seconds: 0 }],
    ['IDENTIFY', { seconds: 31 }],
    ['STORE_PUT', { key: 1000, value: Uint8Array.of(1) }],
    ['STORE_GET', { key: 1000 }],
    ['STORE_DEL', { key: 1000 }],
    ['STORE_WIPE', { magic: 0 }],
  ];
  for (const [name, args] of bad) await fails(band.command(name, args), 'bad-arg');
  assert.deepEqual(await band.getMotion(), { rate: 25, sensors: 'both', accelRange: 2, gyroRange: 2000 });
  assert.deepEqual(await band.getEvents(), { kinds: ['tap', 'double-tap', 'free-fall', 'tilt', 'orientation', 'motion'], tapThreshold: 0.8 });
  assert.equal((await band.getMic()).gainDb, 20);
  assert.equal((await band.getLed()).pattern, 'off');
  // The period is ignored for off and solid, so an odd period is fine there.
  await band.command('LED_SET', { r: 1, g: 1, b: 1, brightness: 1, pattern: 1, period_ms: 5 });
});

test('the other statuses: not-available, bad-length, unknown-opcode, not-found', async () => {
  const { band, sim } = await connectSim();
  await fails(band.command('STREAM_START', { mask: 0x0101 }), 'not-available');
  assert.equal((await band.command('STREAM_GET')).active, 0);           // nothing started
  await fails(band.command('STORE_PUT', { key: 1, value: new Uint8Array(0) }), 'bad-length');
  await fails(band.command('STORE_PUT', { key: 1, value: new Uint8Array(233) }), 'bad-length');
  await fails(band.command('STORE_GET', { key: 5 }), 'not-found');
  await fails(band.storage.delete(5), 'not-found');

  // Straight to the pretend band, as bytes: an opcode nobody knows, and wrong lengths.
  const replies = [];
  sim.onFrame = (bytes) => replies.push(codec.decodeFrame(bytes));
  await sim.send(Uint8Array.of(0x01, 0x30, 0x09));
  await sim.send(Uint8Array.of(0x01, 0x31, 0x04));                      // IDENTIFY without seconds
  await sim.send(Uint8Array.of(0x01, 0x32, 0x01, 0x00));                // GET_INFO with an extra byte
  await sim.send(Uint8Array.of(0x02, 0x33, 0x01, 0x00));                // not a command: silence
  assert.deepEqual(replies, [
    { frame: 'response', seq: 0x30, opcode: 9, status: 'UNKNOWN_OPCODE', data: {} },
    { frame: 'response', seq: 0x31, opcode: 'IDENTIFY', status: 'BAD_LENGTH', data: {} },
    { frame: 'response', seq: 0x32, opcode: 'GET_INFO', status: 'BAD_LENGTH', data: {} },
  ]);
});

test('every status has a readable error', async () => {
  class Refuser extends Transport {
    kind = 'sim';
    status = 'OK';
    async open() {}
    async send(frame) {
      const cmd = codec.decodeCommand(frame);
      const info = { proto_major: 1, proto_minor: 0, fw_major: 1, fw_minor: 0, fw_patch: 0, hw: 1, band_no: 5, caps: 3, max_frame: 244, uptime_ms: 1 };
      this.onFrame(codec.encodeResponse(cmd.seq, cmd.opcode, this.status, cmd.opcode === 'GET_INFO' ? info : {}));
    }
  }
  const transport = new Refuser();
  const band = await Band.connect(5, { transport });
  assert.deepEqual(band.info.capabilities, ['accel', 'gyro']);
  const codes = { UNKNOWN_OPCODE: 'unknown-opcode', BAD_LENGTH: 'bad-length', BAD_ARG: 'bad-arg', BUSY: 'busy', NOT_AVAILABLE: 'not-available',
    DENIED: 'denied', NOT_FOUND: 'not-found', NO_SPACE: 'no-space', IO_ERROR: 'io-error', 99: 'unknown-status' };
  for (const [status, code] of Object.entries(codes)) {
    transport.status = Number(status) || status;
    await fails(band.command('STORE_DEL', { key: 1 }), code);
  }
});

// ---------------------------------------------------------------- storage

test('storage: put, get, text, delete, info, wipe', async () => {
  const { band, sim } = await connectSim(42, { wipeMs: 0 });            // an erase that takes no time (the real thing: storage.test.js)
  assert.deepEqual(await band.storage.info(), { keysMax: 1000, valueMax: 232, freeBytes: 242164, usedKeys: 0 });
  assert.equal(await band.storage.get(7), null);                        // empty key: null, not an error
  assert.equal(await band.storage.getText(7), null);

  await band.storage.put(7, 'hello');
  assert.deepEqual(await band.storage.get(7), Uint8Array.from([104, 101, 108, 108, 111]));
  assert.equal(await band.storage.getText(7), 'hello');
  await band.storage.put(8, 'Grüße, 世界');                             // text is stored as UTF-8
  assert.equal(await band.storage.getText(8), 'Grüße, 世界');
  await band.storage.put(0, Uint8Array.of(0, 255, 0));
  assert.deepEqual(await band.storage.get(0), Uint8Array.of(0, 255, 0));
  await band.storage.put(999, new Uint8Array(232).fill(7));
  assert.equal((await band.storage.get(999)).length, 232);
  await band.storage.put(7, 'replaced');                                // a put replaces the old value
  assert.equal(await band.storage.getText(7), 'replaced');
  assert.deepEqual(await band.storage.info(), { keysMax: 1000, valueMax: 232, freeBytes: 242164 - (8 + 8) - (8 + 16) - (8 + 4) - (8 + 232), usedKeys: 4 });   // 8 per key + the value rounded up to 4

  await band.storage.delete(0);
  assert.equal(await band.storage.get(0), null);
  assert.equal((await band.storage.info()).usedKeys, 3);

  await fails(band.storage.put(1000, 'x'), 'bad-arg');
  await fails(band.storage.put(-1, 'x'), 'bad-arg');
  await fails(band.storage.put(1.5, 'x'), 'bad-arg');
  await fails(band.storage.put(1, ''), 'bad-arg');
  await fails(band.storage.put(1, 'x'.repeat(233)), 'bad-arg');
  await fails(band.storage.put(1, 42), 'bad-arg');
  await fails(band.storage.get(1000), 'bad-arg');
  await fails(band.storage.delete('7'), 'bad-arg');

  // Storage outlives the connection ...
  await band.disconnect();
  const again = await Band.connect(42, { transport: sim });
  assert.equal(await again.storage.getText(7), 'replaced');
  // ... until somebody wipes it.
  await again.storage.wipe();
  assert.equal(await again.storage.get(7), null);
  assert.equal((await again.storage.info()).usedKeys, 0);
  // A new simulator starts empty.
  const other = await connectSim(42);
  assert.equal((await other.band.storage.info()).usedKeys, 0);
});

// ---------------------------------------------------------------- timing, queueing, closing

/** A transport that answers GET_INFO and then goes quiet. */
class QuietTransport extends Transport {
  kind = 'sim';
  sent = [];
  async open() {}
  async send(frame) {
    const cmd = codec.decodeCommand(frame);
    this.sent.push(cmd);
    if (cmd.opcode !== 'GET_INFO') return;
    this.onFrame(codec.encodeResponse(cmd.seq, 'GET_INFO', 'OK', {
      proto_major: 1, proto_minor: 0, fw_major: 1, fw_minor: 0, fw_patch: 0, hw: 1, band_no: 42, caps: 0x7ff, max_frame: 244, uptime_ms: 0,
    }));
  }
}

test('timeout when the band never answers; the next command still goes out', async () => {
  const transport = new QuietTransport();
  const band = await Band.connect(42, { transport, timeoutMs: 20 });
  const started = Date.now();
  await assert.rejects(band.getLed(), (e) => {
    assert.equal(e.code, 'timeout');
    assert.match(e.message, /Vexo-HACK-42/);
    return true;
  });
  assert.ok(Date.now() - started < 1000);
  assert.equal(band.connected, true);
  // A late answer to the old command is ignored; a fresh GET_INFO works.
  transport.onFrame(codec.encodeResponse(1, 'LED_GET', 'OK', { r: 9, g: 9, b: 9, brightness: 9, pattern: 1, period_ms: 100 }));
  assert.equal((await band.getInfo()).number, 42);
  assert.deepEqual(transport.sent.map((c) => c.seq), [0, 1, 2]);        // seq counts up
});

test('a late answer is not taken for the answer to a later command', async () => {
  // Firmware 1.1.3 keeps a response through a pause, so it can arrive after its command
  // timed out, while the same command is waiting again with the next seq.
  const transport = new QuietTransport();
  const band = await Band.connect(42, { transport, timeoutMs: 20 });
  await assert.rejects(band.command('LED_GET'), { code: 'timeout' });
  band.timeoutMs = 2000;
  let settled = false;
  const second = band.command('LED_GET').finally(() => { settled = true; });
  await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(transport.sent.map((c) => [c.opcode, c.seq]), [['GET_INFO', 0], ['LED_GET', 1], ['LED_GET', 2]]);
  const led = { r: 9, g: 9, b: 9, brightness: 9, pattern: 1, period_ms: 100 };
  transport.onFrame(codec.encodeResponse(1, 'LED_GET', 'OK', led));              // the late one
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(settled, false);
  transport.onFrame(codec.encodeResponse(2, 'LED_GET', 'OK', { ...led, r: 1 }));
  assert.equal((await second).r, 1);
});

test('timeout during connect closes the link; STORE_WIPE gets the same time as every command', async () => {
  class Dead extends Transport {
    kind = 'sim';
    closed = 0;
    async open() {}
    async send() {}
    async close() { this.closed++; }
  }
  const dead = new Dead();
  await fails(Band.connect(42, { transport: dead, timeoutMs: 20 }), 'timeout');
  assert.equal(dead.closed, 1);

  const band = await Band.connect(42, { transport: new QuietTransport(), timeoutMs: 40 });
  const started = Date.now();
  await fails(band.storage.wipe(), 'timeout');
  const took = Date.now() - started;
  assert.ok(took >= 35 && took < 110, `waited about 40 ms, not 3 times as long (${took} ms)`);
  assert.equal(band.timeoutMs, 40);
  assert.equal((await Band.connect(42, { transport: new QuietTransport() })).timeoutMs, 2000);   // the default is 2 s
});

test('a response must match seq AND opcode', async () => {
  const transport = new QuietTransport();
  const band = await Band.connect(42, { transport, timeoutMs: 40 });
  const led = { r: 1, g: 2, b: 3, brightness: 255, pattern: 1, period_ms: 1000 };
  const asked = band.command('LED_GET');
  await new Promise((r) => setTimeout(r, 5));
  transport.onFrame(codec.encodeResponse(0, 'LED_GET', 'OK', led));                 // wrong seq
  transport.onFrame(codec.encodeResponse(1, 'BATTERY_GET', 'OK', { percent: 1, state: 0, millivolts: 1 }));   // wrong opcode
  transport.onFrame(codec.encodeResponse(1, 'LED_GET', 'OK', led));                 // the right one
  assert.deepEqual(await asked, led);
});

test('one command at a time, in order', async () => {
  class Slow extends QuietTransport {
    inFlight = 0;
    most = 0;
    async send(frame) {
      const cmd = codec.decodeCommand(frame);
      if (cmd.opcode === 'GET_INFO') return super.send(frame);
      this.sent.push(cmd);
      this.most = Math.max(this.most, ++this.inFlight);
      setTimeout(() => {
        this.inFlight--;
        this.onFrame(codec.encodeResponse(cmd.seq, cmd.opcode, 'OK', { seconds: cmd.args.seconds }));
      }, 3);
    }
  }
  const transport = new Slow();
  const band = await Band.connect(42, { transport });
  const answers = await Promise.all([1, 2, 3, 4, 5].map((seconds) => band.command('TEMP_SET_INTERVAL', { seconds })));
  assert.deepEqual(answers.map((a) => a.seconds), [1, 2, 3, 4, 5]);
  assert.equal(transport.most, 1);
});

test('seq wraps after 255', async () => {
  const { band, sim } = await connectSim();
  const seqs = [];
  const send = sim.send.bind(sim);
  sim.send = (frame) => { seqs.push(frame[1]); return send(frame); };
  for (let i = 0; i < 300; i++) await band.command('STREAM_GET');
  assert.equal(seqs[0], 1);
  assert.equal(seqs[254], 255);
  assert.equal(seqs[255], 0);
  assert.equal(seqs[299], 44);
});

test('a dropped link: one disconnect event, the command in flight is rejected, streams reset', async () => {
  const transport = new QuietTransport();
  const band = await Band.connect(42, { transport });
  band.streams.events = true;
  const gone = collect(band, 'disconnect');
  const inFlight = band.getLed();
  const queued = band.getBattery();
  await new Promise((r) => setTimeout(r, 5));
  transport.onClose('lost');
  transport.onClose('lost');
  await fails(inFlight, 'not-connected');
  await fails(queued, 'not-connected');
  await fails(band.setLed('#ff0000'), 'not-connected');
  assert.deepEqual(gone, [{ reason: 'lost' }]);
  assert.equal(band.connected, false);
  assert.equal(band.streams.events, false);
  await band.disconnect();                                              // already closed: nothing happens
  assert.equal(gone.length, 1);
});

test('dropLink on the simulator, and disconnect() says "closed"', async () => {
  const lost = await connectSim();
  const gone = collect(lost.band, 'disconnect');
  await lost.band.configure({ motion: 50, audio: true });
  lost.sim.dropLink();
  assert.deepEqual(gone, [{ reason: 'lost' }]);
  assert.equal(lost.band.streams.motion, 0);
  assert.equal(lost.band.streams.audio, false);
  assert.equal(lost.sim.led.pattern, 0);

  const closed = await connectSim();
  const events = collect(closed.band, 'disconnect');
  await closed.band.disconnect();
  await closed.band.disconnect();
  assert.deepEqual(events, [{ reason: 'closed' }]);
});

test('settings go back to defaults when you reconnect to the same simulator', async () => {
  const { band, sim } = await connectSim();
  const lights = [];
  sim.onLed = (led) => lights.push(led);
  await band.setLed('#ff0000', { pattern: 'blink' });
  await band.setMotion({ rate: 100, sensors: 'both', accelRange: 16, gyroRange: 0 });
  await band.setEvents({ kinds: ['tap'], tapThreshold: 2 });
  await band.setMicGain(0);
  await band.setTxPower(4);
  await band.configure({ motion: true, audio: true, temperature: 1, battery: true });
  await band.storage.put(1, 'kept');
  await band.disconnect();
  assert.equal(lights.at(-1).pattern, 0);                               // LED off on disconnect

  const again = await Band.connect(42, { transport: sim });
  const audio = collect(again, 'audio');
  const motion = collect(again, 'motion');
  sim.step(1000);
  assert.equal(motion.length + audio.length, 0);                        // every stream is off
  assert.equal((await again.command('STREAM_GET')).active, 0);
  assert.deepEqual(again.streams, { motion: 0, events: false, activity: false, audio: false, battery: false, temperature: false, link: false, micLevel: false });
  assert.deepEqual(await again.getLed(), { r: 0, g: 0, b: 0, brightness: 0, pattern: 'off', period: 1000 });
  assert.deepEqual(await again.getMotion(), { rate: 25, sensors: 'both', accelRange: 2, gyroRange: 2000 });
  assert.deepEqual(await again.getEvents(), { kinds: ['tap', 'double-tap', 'free-fall', 'tilt', 'orientation', 'motion'], tapThreshold: 0.8 });
  assert.equal((await again.getMic()).gainDb, 20);
  assert.equal((await again.getRadio()).txPower, -8);
  assert.deepEqual(await again.command('TEMP_SET_INTERVAL', { seconds: 5 }), { seconds: 5 });
  assert.equal(await again.storage.getText(1), 'kept');                 // storage is kept
  await again.configure({ audio: true });
  sim.step(60);
  assert.deepEqual(audio.map((a) => a.seq), [0, 1, 2]);                 // audio seq starts at 0 again
});

// ---------------------------------------------------------------- the simulator over "USB"

test("sim via 'usb': every byte really travels as COBS", async () => {
  const wire = [];
  const sim = new SimTransport(42, { realtime: false, via: 'usb' });
  sim.onWire = (direction, bytes) => wire.push([direction, Uint8Array.from(bytes)]);
  const band = await Band.connect(42, { transport: sim });
  assert.equal(band.transportKind, 'sim');
  const motion = collect(band, 'motion');
  const link = collect(band, 'link');
  await band.setLed('#00ff00');
  await band.storage.put(3, Uint8Array.of(0, 0, 0, 1, 0));              // zeros are what COBS is for
  assert.deepEqual(await band.storage.get(3), Uint8Array.of(0, 0, 0, 1, 0));
  await band.configure({ motion: 100 });
  sim.step(2000);
  assert.equal(motion.length, 200);
  assert.equal(link.length, 0);                                         // no signal readings on a cable

  assert.deepEqual(await band.getRadio(), { txPower: -8, rssi: null, phy: null, intervalMs: 0, mtu: 0 });

  assert.equal(hex(wire[0][1]), '00');                                  // the opening 00
  assert.equal(hex(wire[1][1]), '0201020100');                          // GET_INFO, seq 0, as COBS + 00
  const directions = new Set();
  for (const [direction, bytes] of wire.slice(1)) {
    directions.add(direction);
    assert.equal(bytes.at(-1), 0, 'ends in 00');
    assert.equal(bytes.indexOf(0), bytes.length - 1, 'no other 00');
    const frame = codec.cobsDecode(bytes.subarray(0, -1));
    assert.ok(frame && frame.length >= 1 && frame.length <= 244);
    if (direction === 'to-band') assert.ok(codec.decodeCommand(frame));
    else assert.ok(codec.decodeFrame(frame));
  }
  assert.deepEqual([...directions].sort(), ['to-band', 'to-computer']);
});

test('realtime simulator runs by itself and stops on disconnect', async () => {
  const band = await Band.connect(42, { transport: 'sim' });
  const motion = collect(band, 'motion');
  await band.configure({ motion: 100 });
  await new Promise((r) => setTimeout(r, 400));
  assert.ok(motion.length >= 10, `got ${motion.length} samples`);
  await band.disconnect();
  const count = motion.length;
  await new Promise((r) => setTimeout(r, 100));
  assert.equal(motion.length, count);
});

test('simulator: clipping to the accel range, constructor checks', async () => {
  const { band, sim } = await connectSim();
  const frames = [];
  const forward = sim.onFrame;
  sim.onFrame = (bytes) => { frames.push(codec.decodeFrame(bytes)); forward(bytes); };
  await band.configure({ motion: 100 });
  sim.step(5000);
  const samples = frames.filter((f) => f.frame === 'motion').flatMap((f) => f.samples);
  assert.equal(samples.length, 500);
  assert.ok(samples.every((s) => Math.abs(s.az) <= 2000 && Math.abs(s.ax) <= 2000));
  assert.ok(frames.filter((f) => f.frame === 'motion').every((f) => f.samples.length === 10 && f.period_ms === 10));
  assert.throws(() => new SimTransport(1000), RangeError);
  assert.throws(() => new SimTransport(1, { via: 'wifi' }), RangeError);
  assert.throws(() => sim.tap('shake'), RangeError);
  assert.throws(() => sim.setBattery(101), RangeError);
});

test('restart and sleep name the reason even when the transport only says the link dropped', async () => {
  for (const method of ['restart', 'sleep']) {
    const { band } = await connectSim();
    const reasons = [];
    band.on('disconnect', (d) => reasons.push(d.reason));
    await band[method]();
    band._closed('lost');            // what a real Bluetooth link reports
    assert.deepEqual(reasons, [method]);
  }
});

test('wrong-band error carries both numbers', async () => {
  const sim = new SimTransport(17, { realtime: false });
  await assert.rejects(Band.connect(42, { transport: sim }), (e) => e.code === 'wrong-band' && e.found === 17 && e.wanted === 42);
});

// ---------------------------------------------------------------- from the independent review

test('configure: wrong value types and inherited names are refused, nothing changes', async () => {
  const { band } = await connectSim();
  for (const bad of [{ events: 'false' }, { events: 1 }, { battery: null }, { toString: true }, { constructor: true }]) {
    await fails(band.configure(bad), 'bad-stream');
  }
  await fails(band.configure({ motion: '50' }), 'bad-arg');
  assert.equal(band.streams.events, false);
  assert.equal(Boolean(band.streams.motion), false);
  await band.disconnect();
});

test('configure: two calls at once both take effect, and a failed call does not block the next', async () => {
  const { band } = await connectSim();
  await Promise.all([band.configure({ motion: 50 }), band.configure({ events: true })]);
  assert.equal(band.streams.motion, 50);
  assert.equal(band.streams.events, true);
  await assert.rejects(band.configure({ motion: -1 }));
  await band.configure({ battery: true });
  assert.equal(band.streams.battery, true);
  await band.disconnect();
});
