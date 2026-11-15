// Firmware 1.1: fast motion, stream intervals and the clock, all against the pretend band
// (no real band runs firmware 1.1 yet). Also: a band with firmware 1.0 never gets a 1.1 command.
import test from 'node:test';
import assert from 'node:assert/strict';
import { Band, SimTransport, VexoError, codec } from '../src/index.js';

const NOW = 1794484800000;              // the computer's clock, pinned
const ALL_RATES = [10, 25, 50, 100, 208, 416, 833, 1666];
const V11_COMMANDS = ['TIME_SET', 'TIME_GET', 'STREAM_SET_INTERVAL', 'STREAM_GET_INTERVAL', 'MOTION2_SET', 'MOTION2_GET'];
const BIT8 = 0x100;

/** Every command the pretend band receives from now on: [{ name, ...args }]. */
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
  const sim = new SimTransport(42, { realtime: false, ...simOptions });
  const log = watch(sim);
  const band = await Band.connect(42, { transport: sim, now: () => NOW, ...bandOptions });
  band._reconnectPauseMs = 5;
  return { sim, band, log };
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
const once = (band, type) => new Promise((resolve) => {
  const off = band.on(type, (e) => { off(); resolve(e); });
});
const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) <= eps, `${a} is not ${b}`);

/** The pretend band alone, spoken to in frames: ask(name, args) returns { status, data }, frames collects the rest. */
async function rawSim(options = {}) {
  const sim = new SimTransport(42, { realtime: false, ...options });
  const frames = [];
  let answer = null;
  sim.onFrame = (bytes) => {
    const msg = codec.decodeFrame(bytes);
    if (msg.frame === 'response') answer = msg;
    else frames.push(msg);
  };
  await sim.open();
  let seq = 0;
  const ask = (name, args = {}) => {
    sim._command(codec.encodeCommand(seq++ & 0xff, name, args));
    return { status: answer.status, data: answer.data };
  };
  return { sim, frames, ask };
}

// ---------------------------------------------------------------- version handling

test('a 1.1 band: info, features, and the clock is set on connect', async () => {
  const { band, log } = await connect();
  assert.equal(band.info.protocol, '1.1');
  assert.equal(band.info.firmware, '1.1.0');
  assert.deepEqual(band.info.features, ['fast-motion', 'intervals', 'clock']);
  assert.deepEqual(log, [{ name: 'GET_INFO' }, { name: 'TIME_SET', unix_ms: NOW }]);
  assert.equal(band.clock, 'band');
  assert.deepEqual(Object.keys(band.streams), ['motion', 'events', 'activity', 'audio', 'battery', 'temperature', 'link', 'micLevel']);
});

test('a 1.0 band: no features, the clock comes from the computer, nothing else is sent', async () => {
  const { band, log } = await connect({ firmware: '1.0' });
  assert.equal(band.info.protocol, '1.0');
  assert.equal(band.info.firmware, '1.0.0');
  assert.deepEqual(band.info.features, []);
  assert.deepEqual(names(log), ['GET_INFO']);
  assert.equal(band.clock, 'computer');
});

test('a later protocol 1.x is used like 1.1; another major version is not', async () => {
  for (const [major, minor, features] of [[1, 2, 3], [1, 7, 3], [2, 1, 0], [0, 9, 0]]) {
    const sim = new SimTransport(42, { realtime: false });
    const encode = codec.encodeResponse;
    const command = sim._command.bind(sim);
    sim._command = (frame) => {           // rewrite the GET_INFO answer on its way out
      const send = sim.onFrame;
      sim.onFrame = (bytes) => {
        const msg = codec.decodeFrame(bytes);
        if (msg.frame === 'response' && msg.opcode === 'GET_INFO') bytes = encode(msg.seq, 'GET_INFO', 'OK', { ...msg.data, proto_major: major, proto_minor: minor });
        send(bytes);
      };
      command(frame);
      sim.onFrame = send;
    };
    const band = await Band.connect(42, { transport: sim });
    assert.equal(band.info.protocol, `${major}.${minor}`);
    assert.equal(band.info.features.length, features, `${major}.${minor}`);
  }
});

test("protocol: '1.0' treats a 1.1 band as a 1.0 band: no 1.1 command and no bit 8 leaves", async () => {
  const { band, sim, log } = await connect({}, { protocol: '1.0' });
  assert.equal(band.info.protocol, '1.1');                              // what the band said
  assert.deepEqual(band.info.features, []);
  assert.equal(band.clock, 'computer');
  const motion = collect(band, 'motion');
  await band.configure({ motion: 'fastest', temperature: 2, battery: true });
  assert.equal(band.streams.motion, 100);
  sim.step(1000);
  assert.equal(motion.length, 100);
  assert.equal(motion[0].n, null);                                      // the old stream
  assert.deepEqual(await band.motionLimits(), { rates: [10, 25, 50, 100], accel: null, gyro: null, both: 100, transport: 'sim' });
  assert.deepEqual(await band.time(), { time: NOW, set: false, t: 1000, source: 'computer' });
  await fails(band.configure({ motion: 416 }), 'not-available', /^You connected with protocol '1\.0', which can do 10, 25, 50 or 100 samples per second with both sensors\. 416 per second needs firmware 1\.1\.$/);
  await fails(band.setInterval('battery', 60), 'not-available', /protocol '1\.0'/);
  await fails(band.setTime(), 'not-available');
  assert.deepEqual(names(log), ['GET_INFO', 'IMU_GET', 'IMU_SET', 'TEMP_SET_INTERVAL', 'STREAM_START', 'GET_INFO']);
  assert.equal(log.find((c) => c.name === 'STREAM_START').mask, 0b110001);
  for (const bad of ['1.1', '2.0', 1, true]) await fails(Band.connect(42, { transport: 'sim', protocol: bad }), 'bad-arg', /protocol can only be '1\.0'/);
});

test('a 1.0 band never gets a 1.1 command or bit 8: connect, every stream, time, limits, reconnect', async () => {
  const { band, sim, log } = await connect({ firmware: '1.0' }, { autoReconnect: true });
  await band.configure({
    motion: true, events: true, activity: true, audio: true, battery: true, temperature: true, link: true, micLevel: true,
  });
  await band.configure({ motion: 'fastest', sensors: 'both', temperature: 3 });
  await band.configure({ motion: 50 });
  await band.setMotion({ rate: 25, sensors: 'both', gyroRange: 500 });
  await band.getMotion();
  await band.setInterval('temperature', 4);
  await band.time();
  await band.motionLimits();
  // everything the library refuses by itself on a 1.0 band: nothing may be sent for these
  await fails(band.configure({ motion: 416 }), 'not-available');
  await fails(band.configure({ sensors: 'accel' }), 'not-available');
  await fails(band.configure({ activity: 0.5 }), 'not-available');
  await fails(band.configure({ battery: 60 }), 'not-available');
  await fails(band.configure({ link: 2 }), 'not-available');
  await fails(band.configure({ micLevel: 0.02 }), 'not-available');
  await fails(band.setMotion({ rate: 833 }), 'not-available');
  await fails(band.setMotion({ sensors: 'gyro' }), 'not-available');
  await fails(band.setInterval('battery', 60), 'not-available');
  await fails(band.getInterval('temperature'), 'not-available');
  await fails(band.setTime(), 'not-available');
  sim.step(500);
  sim.dropLink();
  const back = await once(band, 'reconnected');
  assert.deepEqual(back.failed, []);
  await band.time();
  await band.configure({ motion: false, micLevel: false, audio: false });
  await band.disconnect();

  for (const name of ['GET_INFO', 'IMU_GET', 'IMU_SET', 'TEMP_SET_INTERVAL', 'STREAM_START', 'STREAM_STOP']) assert.ok(names(log).includes(name), name);
  assert.equal(names(log).filter((name) => name === 'GET_INFO').length, 4);     // connect, time(), reconnect, time()
  for (const sent of log) {
    assert.ok(!V11_COMMANDS.includes(sent.name), `${sent.name} was sent to a 1.0 band`);
    if (sent.name === 'STREAM_START' || sent.name === 'STREAM_STOP') assert.equal(sent.mask & ~0xff, 0, `mask ${sent.mask}`);
  }
  // and it is this test's log that would have caught it: the same calls on a 1.1 band do send them
  const other = await connect();
  await other.band.configure({ motion: true });
  assert.ok(names(other.log).includes('TIME_SET') && names(other.log).includes('MOTION2_GET'));
  assert.equal(other.log.at(-1).mask, BIT8);
});

test('the 1.0 messages name the firmware and what it can do', async () => {
  const { band } = await connect({ firmware: '1.0' });
  await fails(band.configure({ motion: 416 }), 'not-available',
    /^This band's firmware \(1\.0\.0\) can do 10, 25, 50 or 100 samples per second with both sensors\. 416 per second needs firmware 1\.1\.$/);
  await fails(band.configure({ sensors: 'accel' }), 'not-available', /with both sensors\. One sensor alone needs firmware 1\.1\.$/);
  await fails(band.configure({ motion: 30 }), 'bad-arg', /must be one of 10, 25, 50, 100; got 30/);
  await fails(band.configure({ activity: 0.5 }), 'not-available', /needs band firmware 1\.1; this band has 1\.0\.0\. Use true for the standard rate\./);
  await fails(band.setInterval('link', 5), 'not-available', /needs band firmware 1\.1; this band has 1\.0\.0/);
  await fails(band.getInterval('temperature'), 'not-available', /firmware 1\.1/);
  await fails(band.setTime(), 'not-available', /Setting the clock needs band firmware 1\.1; this band has 1\.0\.0/);
  // temperature keeps what it could always do: whole seconds from 1 to 255
  await fails(band.configure({ temperature: 0.5 }), 'bad-arg', /whole number from 1 to 255/);
  await fails(band.setInterval('temperature', 256), 'bad-arg', /from 1 to 255/);
  assert.deepEqual(await band.setInterval('temperature', 7), { stream: 'temperature', seconds: 7, min: 1, max: 255 });
  assert.equal(Boolean(band.streams.temperature), false);               // setInterval does not turn it on
  assert.deepEqual(await band.setMotion({ rate: 50 }), { rate: 50, sensors: 'both', accelRange: 2, gyroRange: 2000 });
  // 0 and false still mean off, as before
  await band.configure({ activity: true });
  assert.equal((await band.configure({ activity: 0 })).activity, false);
});

// ---------------------------------------------------------------- fast motion

test('a MOTION2 frame becomes one motion event per sample, with t, n, time and null for what was not sent', async () => {
  const { band, sim } = await connect();
  const anchor = await band.time();                                     // band time 0 is NOW
  assert.deepEqual(anchor, { time: NOW, set: true, t: 0, source: 'band' });
  const motion = collect(band, 'motion');
  sim.onFrame(codec.encodeStream('motion2', {
    t_ms: 1000, sample_no: 832, period_us: 2404, sensors: 1,
    samples: [{ ax: 10, ay: -20, az: 1000 }, { ax: 11, ay: -21, az: 1001 }, { ax: -16000, ay: 16000, az: 1 }],
  }));
  assert.deepEqual(Object.keys(motion[0]), ['t', 'ax', 'ay', 'az', 'gx', 'gy', 'gz', 'n', 'time']);
  assert.deepEqual(motion[0], { t: 1000, ax: 0.01, ay: -0.02, az: 1, gx: null, gy: null, gz: null, n: 832, time: NOW + 1000 });
  assert.deepEqual(motion.map((m) => m.n), [832, 833, 834]);
  near(motion[1].t, 1002.404);                                          // sub-millisecond spacing
  near(motion[2].t, 1004.808);
  near(motion[2].time, NOW + 1004.808);
  assert.deepEqual([motion[2].ax, motion[2].ay, motion[2].az], [-16, 16, 0.001]);

  motion.length = 0;
  sim.onFrame(codec.encodeStream('motion2', { t_ms: 2000, sample_no: 0, period_us: 1201, sensors: 2, samples: [{ gx: 125, gy: -2000, gz: -1 }] }));
  sim.onFrame(codec.encodeStream('motion2', {
    t_ms: 3000, sample_no: 4294967295, period_us: 40000, sensors: 3,
    samples: [{ ax: 500, ay: -1000, az: 250, gx: 125, gy: -2000, gz: 0 }, { ax: 0, ay: 0, az: 0, gx: 0, gy: 0, gz: 0 }],
  }));
  assert.deepEqual(motion[0], { t: 2000, ax: null, ay: null, az: null, gx: 12.5, gy: -200, gz: -0.1, n: 0, time: NOW + 2000 });
  assert.deepEqual(motion[1], { t: 3000, ax: 0.5, ay: -1, az: 0.25, gx: 12.5, gy: -200, gz: 0, n: 4294967295, time: NOW + 3000 });
  assert.equal(motion[2].n, 0);                                         // the sample number starts again after 2^32
  assert.equal(motion[2].t, 3040);
  // the old stream still makes the same event, without a number
  sim.onFrame(codec.encodeStream('motion', { t_ms: 4000, period_ms: 20, samples: [{ ax: 500, ay: 0, az: 0, gx: 10, gy: 0, gz: 0 }] }));
  assert.deepEqual(motion[3], { t: 4000, ax: 0.5, ay: 0, az: 0, gx: 1, gy: 0, gz: 0, n: null, time: NOW + 4000 });
});

test('configure({ motion: 416, sensors: "accel" }): MOTION2 is used, every sample arrives, the real spacing shows', async () => {
  const { band, sim, log } = await connect();
  const motion = collect(band, 'motion');
  const streams = await band.configure({ motion: 416, sensors: 'accel' });
  assert.equal(streams.motion, 416);
  assert.deepEqual(log.slice(2), [
    { name: 'MOTION2_GET' }, { name: 'MOTION2_SET', rate_hz: 416, sensors: 1 }, { name: 'STREAM_START', mask: BIT8 },
  ]);
  sim.step(1000);
  assert.ok(motion.length > 395 && motion.length < 416, `${motion.length} samples in a second`);   // the chip runs a little slow
  motion.forEach((m, i) => assert.equal(m.n, i));
  assert.equal(motion[0].gx, null);
  near(motion[0].az, 1, 0.1);
  const spacing = (motion.at(-1).t - motion[0].t) / (motion.length - 1);
  assert.ok(Math.abs(spacing - 1000 / 416) > 0.01, 'not the nominal spacing');
  near(spacing, 2.423, 0.001);                                          // period_us of the pretend band at 416
  near(motion[5].time - motion[4].time, spacing, 1e-3);
  assert.deepEqual(await band.getMotion(), { rate: 416, sensors: 'accel', accelRange: 2, gyroRange: 2000 });

  // sensors alone changes the sensors and leaves on/off as it is; the numbers start again
  motion.length = 0;
  await band.configure({ sensors: 'gyro' });
  assert.equal(band.streams.motion, 416);
  sim.step(100);
  assert.equal(motion[0].n, 0);
  assert.equal(motion[0].ax, null);
  assert.notEqual(motion[0].gx, null);

  // true keeps the rate; false turns it off
  await band.configure({ motion: false });
  assert.equal(band.streams.motion, 0);
  assert.equal(log.at(-1).name, 'STREAM_STOP');
  assert.equal(log.at(-1).mask, BIT8);
  motion.length = 0;
  sim.step(200);
  assert.equal(motion.length, 0);
  await band.configure({ sensors: 'both' });                            // off stays off
  assert.equal(band.streams.motion, 0);
  await band.configure({ motion: true });
  assert.equal(band.streams.motion, 416);
  await band.configure({ motion: 0 });
  assert.equal(band.streams.motion, 0);
});

test('motion: true on a fresh 1.1 band is 25 a second with both sensors, on MOTION2', async () => {
  const { band, sim, log } = await connect();
  const motion = collect(band, 'motion');
  assert.equal((await band.configure({ motion: true })).motion, 25);
  assert.deepEqual(names(log.slice(2)), ['MOTION2_GET', 'STREAM_START']);     // no MOTION2_SET: nothing to change
  sim.step(1000);
  assert.equal(motion.length, 25);
  for (let i = 1; i < motion.length; i++) assert.equal(motion[i].t - motion[i - 1].t, 40);
  assert.ok(motion.every((m, i) => m.n === i && m.gx !== null && m.ax !== null));
  // the old stream stays reachable by its contract name, and makes motion events too
  motion.length = 0;
  await band.configure({ motion: false });
  await band.command('STREAM_START', { mask: 1 });
  assert.equal(band.streams.motion, 25);
  sim.step(400);
  assert.equal(motion.length, 10);
  assert.equal(motion[0].n, null);
  await band.configure({ motion: false });                              // turns off whichever motion stream is on
  assert.equal((await band.command('STREAM_GET')).active, 0);
});

test('"fastest" and motionLimits follow the link and the sensors', async () => {
  const ble = await connect();
  assert.deepEqual(await ble.band.motionLimits(), { rates: ALL_RATES, accel: 833, gyro: 833, both: 416, transport: 'sim' });
  assert.equal((await ble.band.configure({ motion: 'fastest' })).motion, 416);
  assert.equal((await ble.band.configure({ motion: 'fastest', sensors: 'accel' })).motion, 833);
  assert.equal((await ble.band.configure({ motion: 'fastest', sensors: 'gyro' })).motion, 833);
  assert.deepEqual(names(ble.log).filter((n) => n === 'MOTION2_GET'), ['MOTION2_GET']);       // asked once per link

  const usb = await connect({ via: 'usb' });
  assert.deepEqual(await usb.band.motionLimits(), { rates: ALL_RATES, accel: 1666, gyro: 833, both: 833, transport: 'sim' });
  assert.equal((await usb.band.configure({ motion: 'fastest' })).motion, 833);
  assert.equal((await usb.band.configure({ motion: 'fastest', sensors: 'accel' })).motion, 1666);
  const motion = collect(usb.band, 'motion');
  usb.sim.step(1000);
  assert.ok(motion.length > 1600 && motion.length < 1666, `${motion.length}`);
  motion.forEach((m, i) => assert.equal(m.n, i));                       // nothing lost through the cable framing

  const old = await connect({ firmware: '1.0' });
  assert.equal((await old.band.configure({ motion: 'fastest' })).motion, 100);
  assert.deepEqual(await old.band.motionLimits(), { rates: [10, 25, 50, 100], accel: null, gyro: null, both: 100, transport: 'sim' });
});

test('over the cap: denied before anything is sent, and the message names the link, the cap and the way out', async () => {
  const { band, log } = await connect();
  await band.motionLimits();
  const sent = log.length;
  await fails(band.configure({ motion: 833, sensors: 'both', events: true }), 'denied',
    /^833 samples per second with both sensors is too much for Bluetooth here: the most is 416 with both, 833 with accel alone, 833 with gyro alone\. Use sensors: 'accel', or sensors: 'gyro', or the USB cable \(up to 833 with both, 1666 with accel alone\), or a lower rate\. Nothing was changed\.$/);
  await fails(band.configure({ motion: 1666, sensors: 'accel' }), 'denied',
    /^1666 samples per second with accel alone is too much for Bluetooth here: the most is 416 with both, 833 with accel alone, 833 with gyro alone\. Use the USB cable \(up to 833 with both, 1666 with accel alone\), or a lower rate\. Nothing was changed\.$/);
  await fails(band.setMotion({ rate: 833 }), 'denied', /too much for Bluetooth/);
  assert.equal(log.length, sent);                                       // nothing was sent
  assert.deepEqual(band.streams, { motion: 0, events: false, activity: false, audio: false, battery: false, temperature: false, link: false, micLevel: false });
  assert.deepEqual(await band.getMotion(), { rate: 25, sensors: 'both', accelRange: 2, gyroRange: 2000 });

  // 833 with one sensor is fine; asking for both sensors while it runs is not
  await band.configure({ motion: 833, sensors: 'accel' });
  await fails(band.configure({ sensors: 'both' }), 'denied', /833 samples per second with both sensors/);
  assert.equal(band.streams.motion, 833);

  const usb = await connect({ via: 'usb' });
  await fails(usb.band.configure({ motion: 1666 }), 'denied',
    /^1666 samples per second with both sensors is too much for the USB cable: the most is 833 with both, 1666 with accel alone, 833 with gyro alone\. Use sensors: 'accel', or a lower rate\. Nothing was changed\.$/);
  await fails(usb.band.configure({ motion: 1666, sensors: 'gyro' }), 'denied', /with gyro alone is too much for the USB cable: .*Use sensors: 'accel', or a lower rate\./);
});

test('the band answering DENIED gives the same message (MOTION2_SET while streaming, STREAM_START over the cap)', async () => {
  const { band } = await connect();
  await band.configure({ motion: 208 });
  // by contract name, so the library's own check is not in the way
  await fails(band.command('MOTION2_SET', { rate_hz: 833, sensors: 3 }), 'denied',
    /^833 samples per second with both sensors is too much for Bluetooth here: the most is 416 with both, 833 with accel alone, 833 with gyro alone\. Use sensors: 'accel', or sensors: 'gyro', or the USB cable/);
  assert.equal(band.streams.motion, 208);                               // nothing changed
  await band.configure({ motion: false });
  await band.command('MOTION2_SET', { rate_hz: 1666, sensors: 1 });     // not streaming: the band takes any allowed value
  await fails(band.command('STREAM_START', { mask: BIT8 | 2 }), 'denied', /^1666 samples per second with accel alone is too much for Bluetooth/);
  assert.deepEqual(band.streams, { motion: 0, events: false, activity: false, audio: false, battery: false, temperature: false, link: false, micLevel: false });
  await fails(band.configure({ motion: true }), 'denied', /1666 samples per second/);     // true = keep the rate, and that rate is too much
  // without knowing the caps (nothing asked yet on this link) the message still says what to do
  const fresh = await connect();
  await fresh.band.command('MOTION2_SET', { rate_hz: 1666, sensors: 1 });
  fresh.band._m2 = null;
  await fails(fresh.band.command('STREAM_START', { mask: BIT8 }), 'denied', /more motion samples per second than this link can carry.*motionLimits/);
});

test('a rate that is not in the list, or a wrong sensors word: bad-arg that lists the choices, nothing sent', async () => {
  const { band, log } = await connect();
  const sent = log.length;
  await fails(band.configure({ motion: 200 }), 'bad-arg', /must be one of 10, 25, 50, 100, 208, 416, 833, 1666; got 200/);
  await fails(band.configure({ motion: 3330 }), 'bad-arg', /10, 25, 50, 100, 208, 416, 833, 1666/);
  await fails(band.setMotion({ rate: 400 }), 'bad-arg', /208, 416/);
  await fails(band.configure({ sensors: 'all' }), 'bad-arg', /sensors must be one of accel, gyro, both; got all/);
  await fails(band.setMotion({ sensors: 3 }), 'bad-arg', /accel, gyro, both/);
  await fails(band.configure({ motion: 'fast' }), 'bad-arg', /true or false or a number or "fastest"/);
  assert.equal(log.length, sent);
});

test('setMotion on 1.1: rate and sensors go to MOTION2_SET, ranges to IMU_SET', async () => {
  const { band, sim, log } = await connect();
  assert.deepEqual(await band.getMotion(), { rate: 25, sensors: 'both', accelRange: 2, gyroRange: 2000 });
  log.length = 0;
  assert.deepEqual(await band.setMotion({ rate: 208, sensors: 'gyro' }), { rate: 208, sensors: 'gyro', accelRange: 2, gyroRange: 2000 });
  assert.deepEqual(log, [{ name: 'MOTION2_SET', rate_hz: 208, sensors: 2 }]);   // settings were cached by getMotion
  log.length = 0;
  assert.deepEqual(await band.setMotion({ accelRange: 8, gyroRange: 0 }), { rate: 208, sensors: 'gyro', accelRange: 8, gyroRange: 0 });
  assert.deepEqual(log, [{ name: 'IMU_SET', rate_hz: 25, accel_range_g: 8, gyro_range_dps: 0 }]);
  assert.deepEqual(await band.setMotion({ rate: 100, accelRange: 16 }), { rate: 100, sensors: 'gyro', accelRange: 16, gyroRange: 0 });
  assert.deepEqual(await band.setMotion(), { rate: 100, sensors: 'gyro', accelRange: 16, gyroRange: 0 });   // nothing given: nothing changes
  // the ranges apply to the fast stream: gyro range 0 = gyro values 0
  const motion = collect(band, 'motion');
  await band.configure({ motion: true, sensors: 'both' });
  sim.step(200);
  assert.ok(motion.length > 0 && motion.every((m) => m.gx === 0 && m.gy === 0 && m.gz === 0));
  await fails(band.setMotion({ accelRange: 3 }), 'bad-arg');
});

// ---------------------------------------------------------------- stream intervals

test('configure takes seconds for activity, battery, temperature, link and micLevel', async () => {
  const { band, sim, log } = await connect();
  const got = Object.fromEntries(['activity', 'battery', 'temperature', 'link', 'mic-level'].map((type) => [type, collect(band, type)]));
  const streams = await band.configure({ activity: 0.1, battery: 2, temperature: 0.5, link: 0.25, micLevel: 0.02 });
  assert.deepEqual(streams, { motion: 0, events: false, activity: true, audio: false, battery: true, temperature: true, link: true, micLevel: true });
  const sets = log.filter((c) => c.name === 'STREAM_SET_INTERVAL');
  assert.deepEqual(sets.map((c) => [c.stream, c.interval_ms]), [[2, 100], [4, 2000], [5, 500], [6, 250], [7, 20]]);
  assert.equal(log.at(-1).name, 'STREAM_START');                        // the intervals go first, then one start
  assert.equal(log.at(-1).mask, 0b11110100);
  sim.step(4000);
  assert.equal(got.activity.length, 40);
  assert.equal(got.battery.length, 3);                                  // once at start, then every 2 seconds
  assert.equal(got.temperature.length, 8);
  assert.equal(got.link.length, 16);
  assert.equal(got['mic-level'].length, 200);
  assert.equal(got['mic-level'][1].t - got['mic-level'][0].t, 20);

  // true keeps the interval; false and 0 turn the stream off
  await band.configure({ activity: false, link: 0 });
  await band.configure({ activity: true });
  got.activity.length = 0;
  sim.step(1000);
  assert.equal(got.activity.length, 10);
  assert.equal(band.streams.link, false);
  assert.deepEqual(await band.getInterval('activity'), { stream: 'activity', seconds: 0.1, min: 0.1, max: 3600 });
});

test('an interval the band does not allow: bad-arg that names the range, and nothing is changed or started', async () => {
  const { band, log } = await connect();
  await fails(band.configure({ motion: 50, activity: 0.05 }), 'bad-arg', /^"activity" can come every 0\.1 to 3600 seconds, got 0\.05\. Nothing was changed\.$/);
  await fails(band.configure({ battery: 0.5, events: true }), 'bad-arg', /^"battery" can come every 1 to 3600 seconds, got 0\.5\. Nothing was changed\.$/);
  await fails(band.configure({ temperature: 4000 }), 'bad-arg', /^"temperature" can come every 0\.1 to 3600 seconds, got 4000\. Nothing was changed\.$/);
  await fails(band.configure({ link: -1 }), 'bad-arg', /^"link" can come every 0\.1 to 3600 seconds, got -1\. Nothing was changed\.$/);
  await fails(band.configure({ micLevel: 0.03 }), 'bad-arg', /^"micLevel" can come every 0\.02 to 10 seconds, in steps of 0\.02, got 0\.03\. Nothing was changed\.$/);
  await fails(band.configure({ micLevel: 11 }), 'bad-arg', /0\.02 to 10 seconds, in steps of 0\.02/);
  await fails(band.configure({ activity: NaN }), 'bad-arg');
  await fails(band.configure({ activity: '1' }), 'bad-stream');           // not true, false or a number: the code from before intervals
  await fails(band.configure({ events: 2 }), 'bad-stream');             // events has no interval
  await fails(band.configure({ audio: 0.02 }), 'bad-stream');
  // one good and one bad interval in the same call: the good one is not set either
  await fails(band.configure({ activity: 0.5, link: 0.01 }), 'bad-arg');
  assert.ok(!names(log).includes('STREAM_SET_INTERVAL') && !names(log).includes('STREAM_START') && !names(log).includes('MOTION2_SET'));
  assert.equal((await band.getInterval('activity')).seconds, 1);
  assert.deepEqual(band.streams, { motion: 0, events: false, activity: false, audio: false, battery: false, temperature: false, link: false, micLevel: false });
});

test('setInterval and getInterval: seconds in and out, limits from the band, the stream is not turned on or off', async () => {
  const { band, sim, log } = await connect();
  assert.deepEqual(await band.getInterval('battery'), { stream: 'battery', seconds: 0, min: 1, max: 3600 });
  assert.deepEqual(await band.getInterval('micLevel'), { stream: 'micLevel', seconds: 0.1, min: 0.02, max: 10 });
  assert.deepEqual(await band.getInterval('mic_level'), { stream: 'micLevel', seconds: 0.1, min: 0.02, max: 10 });
  assert.deepEqual(await band.getInterval('temperature'), { stream: 'temperature', seconds: 5, min: 0.1, max: 3600 });
  assert.deepEqual(await band.setInterval('battery', 60), { stream: 'battery', seconds: 60, min: 1, max: 3600 });
  assert.deepEqual(log.at(-1), { name: 'STREAM_SET_INTERVAL', stream: 4, interval_ms: 60000 });
  assert.equal(band.streams.battery, false);                            // still off
  assert.deepEqual(await band.setInterval('battery', 0), { stream: 'battery', seconds: 0, min: 1, max: 3600 });   // only on change again
  assert.deepEqual(await band.setInterval('micLevel', 0.06), { stream: 'micLevel', seconds: 0.06, min: 0.02, max: 10 });
  assert.deepEqual(await band.setInterval('link', 0.2504), { stream: 'link', seconds: 0.25, min: 0.1, max: 3600 });   // whole milliseconds

  await fails(band.setInterval('battery', 0.5), 'bad-arg', /^"battery" can come every 1 to 3600 seconds \(or 0 = only when it changes\), got 0\.5\. Nothing was changed\.$/);
  await fails(band.setInterval('activity', 0), 'bad-arg', /^"activity" can come every 0\.1 to 3600 seconds, got 0\. Nothing was changed\.$/);
  await fails(band.setInterval('micLevel', 0.05), 'bad-arg', /in steps of 0\.02, got 0\.05/);
  await fails(band.setInterval('activity', '1'), 'bad-arg');
  await fails(band.setInterval('activity'), 'bad-arg');
  for (const none of ['motion', 'events', 'audio', 'heartRate', 'toString', undefined]) {
    const code = ['motion', 'events', 'audio'].includes(none) ? 'bad-arg' : 'bad-stream';     // a stream without an interval / no stream at all
    await fails(band.setInterval(none, 1), code, /has no interval\. The streams with one: activity, battery, temperature, link, micLevel\./);
    await fails(band.getInterval(none), code);
  }

  // a running stream changes pace from the moment it is set, and stays on
  const activity = collect(band, 'activity');
  await band.configure({ activity: true });
  sim.step(1000);
  assert.equal(activity.length, 1);
  await band.setInterval('activity', 0.2);
  sim.step(1000);
  assert.equal(activity.length, 6);
  assert.equal(band.streams.activity, true);
});

test('the band answering BAD_ARG to an interval: the library asks for the limits and says them', async () => {
  const { band, log } = await connect();
  await band.getInterval('battery');
  band._limits[4] = { min_ms: 0, max_ms: 1e9 };                         // pretend the library knew wider limits than the band has
  log.length = 0;
  await fails(band.setInterval('battery', 0.5), 'bad-arg', /^"battery" can come every 1 to 3600 seconds \(or 0 = only when it changes\), got 0\.5\. Nothing was changed\.$/);
  assert.deepEqual(names(log), ['STREAM_SET_INTERVAL', 'STREAM_GET_INTERVAL']);
  band._limits[7] = { min_ms: 0, max_ms: 1e9 };
  await fails(band.configure({ micLevel: 20 }), 'bad-arg', /^"micLevel" can come every 0\.02 to 10 seconds, in steps of 0\.02, got 20\. Nothing was changed\.$/);
  assert.equal(band.streams.micLevel, false);
});

// ---------------------------------------------------------------- the clock

test('event times come from the anchor, across the wrap of the band counter, before and after it', async () => {
  const sim = new SimTransport(42, { realtime: false });
  sim._t = 2 ** 32 - 1000;                                              // the band has been on for almost 49.7 days
  const band = await Band.connect(42, { transport: sim, now: () => NOW });
  assert.deepEqual(await band.time(), { time: NOW, set: true, t: 4294966296, source: 'band' });
  const activity = collect(band, 'activity');
  const motion = collect(band, 'motion');
  const stamp = (t_ms) => sim.onFrame(codec.encodeStream('activity', { t_ms, steps_total: 1, cadence_spm: 0, state: 0, motion_mg: 0 }));
  stamp(4294966296);            // the anchor itself
  stamp(4294967295);            // 999 ms later, the last value before the wrap
  stamp(0);                     // 1000 ms later: the counter started again
  stamp(5000);                  // 6 s later
  stamp(4294966296 - 250);      // a frame from 250 ms before the anchor
  stamp(4294966296 - 2 ** 31 + 1);      // as far back as the rule reaches (24.8 days)
  stamp((4294966296 + 2 ** 31 - 1) % 2 ** 32);     // and as far ahead
  assert.deepEqual(activity.map((e) => e.time - NOW), [0, 999, 1000, 6000, -250, -(2 ** 31) + 1, 2 ** 31 - 1]);
  assert.deepEqual(activity.map((e) => e.t), [4294966296, 4294967295, 0, 5000, 4294966046, 2147482649, 2147482647]);

  // fast motion across the wrap: the samples keep their sub-millisecond spacing
  sim.onFrame(codec.encodeStream('motion2', {
    t_ms: 4294967295, sample_no: 7, period_us: 1201, sensors: 2, samples: [{ gx: 0, gy: 0, gz: 0 }, { gx: 0, gy: 0, gz: 0 }, { gx: 0, gy: 0, gz: 0 }],
  }));
  near(motion[0].time, NOW + 999);
  near(motion[1].time, NOW + 999 + 1.201);
  near(motion[2].time, NOW + 999 + 2.402);
  // and the pretend band itself runs through the wrap
  const temperature = collect(band, 'temperature');
  await band.configure({ temperature: 0.4 });
  sim.step(1500);
  assert.deepEqual(temperature.map((e) => e.time - NOW), [400, 800, 1200]);
  assert.deepEqual(temperature.map((e) => e.t), [4294966696, 4294967096, 200]);
});

test('band.time() and setTime(): the shape, a given time, the range, and the anchor follows', async () => {
  const { band, sim, log } = await connect();
  sim.step(2500);
  assert.deepEqual(await band.time(), { time: NOW + 2500, set: true, t: 2500, source: 'band' });
  assert.equal(log.at(-1).name, 'TIME_GET');
  const taps = collect(band, 'tap');
  await band.configure({ events: true });
  sim.tap();
  assert.equal(taps[0].time, NOW + 2500);

  // a time of your own, far above 2^32 ms
  const moon = Date.UTC(2069, 6, 20, 20, 17);
  assert.deepEqual(await band.setTime(moon), { time: moon, set: true, t: 2500, source: 'band' });
  assert.deepEqual(log.at(-1), { name: 'TIME_SET', unix_ms: moon });
  sim.step(100);
  sim.tap();
  assert.equal(taps[1].time, moon + 100);                               // the anchor was refreshed
  assert.equal(taps[1].t, 2600);
  assert.equal(new Date(taps[1].time).toISOString(), '2069-07-20T20:17:00.100Z');
  // no argument = the computer's time now; fractions are rounded
  assert.equal((await band.setTime()).time, NOW);
  assert.equal((await band.setTime(NOW + 0.6)).time, NOW + 1);

  const sent = log.length;
  for (const bad of [NOW / 1000, NOW * 1000, 0, -1, NaN, '2026', null, Date.UTC(2019, 11, 31), Date.UTC(2100, 0, 1)]) {
    await fails(band.setTime(bad), 'bad-arg', /from the year 2020 to 2100, in milliseconds since 1970 \(like Date\.now\(\)\)/);
  }
  assert.equal(log.length, sent);                                       // the library said no before sending
  assert.equal((await band.time()).time, NOW + 1);

  // someone else sets the band's clock: time() notices and the events follow
  sim._command(codec.encodeCommand(200, 'TIME_SET', { unix_ms: moon }));
  sim.tap();
  assert.equal(taps[2].time, NOW + 1);                                  // not known yet
  assert.equal((await band.time()).time, moon);
  sim.tap();
  assert.equal(taps[3].time, moon);
});

test('time() on a 1.0 band: the computer estimate, by GET_INFO', async () => {
  const { band, sim, log } = await connect({ firmware: '1.0' });
  sim.step(700);
  band.now = () => NOW + 705;                                           // the computer's clock moved on, 5 ms more than the band's
  assert.deepEqual(await band.time(), { time: NOW + 705, set: false, t: 700, source: 'computer' });
  assert.deepEqual(names(log), ['GET_INFO', 'GET_INFO']);
  assert.equal(band.clock, 'computer');
  const taps = collect(band, 'tap');
  await band.configure({ events: true });
  sim.step(300);
  sim.tap();
  assert.deepEqual(taps, [{ t: 1000, kind: 'tap', arg: 0, time: NOW + 1005 }]);     // anchor: uptime 700 was NOW + 705
});

test('setClock: false reads the band clock: unset = computer time, set by someone before = band time', async () => {
  const sim = new SimTransport(42, { realtime: false });
  const log = watch(sim);
  const first = await Band.connect(42, { transport: sim, now: () => NOW, setClock: false });
  assert.deepEqual(log, [{ name: 'GET_INFO' }, { name: 'TIME_GET' }]);
  assert.equal(first.clock, 'computer');
  sim.step(1000);
  first.now = () => NOW + 1000;
  assert.deepEqual(await first.time(), { time: NOW + 1000, set: false, t: 1000, source: 'computer' });
  const taps = collect(first, 'tap');
  await first.configure({ events: true });
  sim.tap();
  assert.equal(taps[0].time, NOW + 1000);
  // setTime() still works when asked for, and from then on the band's clock is used
  first.now = () => NOW + 5000;                                         // this computer's clock is 4 s ahead of what it was
  assert.deepEqual(await first.setTime(), { time: NOW + 5000, set: true, t: 1000, source: 'band' });
  assert.equal(first.clock, 'band');
  await first.disconnect();
  assert.equal(first.clock, 'computer');

  // the next client does not set it, and finds the clock the first one left (the link was closed for no band time)
  const second = await Band.connect(42, { transport: sim, now: () => 1, setClock: false });
  assert.equal(second.clock, 'band');
  assert.deepEqual(await second.time(), { time: NOW + 5000, set: true, t: 1000, source: 'band' });
  assert.ok(!names(log).slice(-3).includes('TIME_SET'));
});

test('a band that refuses the clock still connects, with computer time', async () => {
  const sim = new SimTransport(42, { realtime: false });
  const log = watch(sim);
  const band = await Band.connect(42, { transport: sim, now: () => 12345 });     // a computer whose clock says 1970
  assert.deepEqual(names(log), ['GET_INFO', 'TIME_SET', 'TIME_GET']);  // refused (BAD_ARG), so the library only reads the clock
  assert.equal(band.clock, 'computer');
  assert.deepEqual(await band.time(), { time: 12345, set: false, t: 0, source: 'computer' });
  // after a reconnect the refused command is named in `failed`
  band.autoReconnect = true;
  band._reconnectPauseMs = 5;
  sim.dropLink();
  assert.deepEqual((await once(band, 'reconnected')).failed, ['TIME_SET']);
  await band.disconnect();
  await fails(Band.connect(42, { transport: 'sim', now: 5 }), 'bad-arg');
});

test('battery and notice events carry the time they arrived; audio counts 20 ms per frame from its first one', async () => {
  const { band, sim } = await connect();
  let clock = NOW + 50;
  band.now = () => clock;
  const battery = collect(band, 'battery');
  const notices = collect(band, 'notice');
  const audio = collect(band, 'audio');
  await band.configure({ battery: true });
  assert.deepEqual(battery, [{ percent: 87, millivolts: 4083, state: 'battery', charging: false, time: NOW + 50 }]);
  clock = NOW + 900;
  sim.setBattery(14);
  assert.equal(battery[1].time, NOW + 900);
  assert.deepEqual(notices, [{ kind: 'low-battery', arg: 14, time: NOW + 900 }]);
  assert.deepEqual(Object.keys(await band.getBattery()), ['percent', 'millivolts', 'state', 'charging']);

  clock = NOW + 1000;
  await band.configure({ audio: true });
  clock = NOW + 1140;                                                   // the first frame arrives
  sim.step(20);
  clock = NOW + 9999;                                                   // later frames do not look at the computer's clock
  sim.step(80);
  assert.deepEqual(audio.map((a) => a.seq), [0, 1, 2, 3, 4]);
  assert.deepEqual(audio.map((a) => a.time - NOW), [1140, 1160, 1180, 1200, 1220]);
  assert.deepEqual(Object.keys(audio[0]), ['seq', 'opus', 'time']);

  // a gap in seq is a gap in time, and seq starts again at 0 after 65535
  const frame = (seq) => sim.onFrame(codec.encodeStream('audio', { seq, opus: Uint8Array.of(1) }));
  frame(10);
  frame(65535);
  frame(0);
  frame(3);
  assert.deepEqual(audio.slice(5).map((a) => a.time - NOW - 1140), [200, 65535 * 20, 65536 * 20, 65539 * 20]);

  // stop and start: a new run, timed from its own first frame
  await band.configure({ audio: false });
  await band.configure({ audio: true });
  audio.length = 0;
  clock = NOW + 20000;
  sim.step(40);
  assert.deepEqual(audio.map((a) => [a.seq, a.time - NOW]), [[0, 20000], [1, 20020]]);
});

test('every data event has a time', async () => {
  const { band, sim } = await connect();
  const seen = {};
  for (const type of ['motion', 'event', 'tap', 'activity', 'audio', 'battery', 'temperature', 'link', 'mic-level', 'notice']) {
    band.on(type, (e) => { seen[type] = e; });
  }
  await band.configure({ motion: 50, events: true, activity: true, audio: true, battery: true, temperature: 1, link: true, micLevel: true });
  sim.step(1000);
  sim.tap();
  sim.setBattery(10);
  assert.equal(Object.keys(seen).length, 10);
  for (const [type, e] of Object.entries(seen)) {
    assert.equal(typeof e.time, 'number', type);
    assert.ok(Math.abs(e.time - NOW) <= 1000, `${type}: ${e.time}`);
    if ('t' in e) near(e.time, NOW + e.t);
  }
});

// ---------------------------------------------------------------- auto-reconnect

test('auto-reconnect on 1.1: the clock first, then MOTION2_SET, the intervals, and the streams with bit 8', async () => {
  const { band, sim, log } = await connect({}, { autoReconnect: true });
  const motion = collect(band, 'motion');
  await band.setLed('#00ff00');
  await band.setMotion({ gyroRange: 500 });
  await band.configure({ motion: 208, sensors: 'accel', activity: 0.5, battery: 30, events: true });
  await band.setInterval('micLevel', 0.04);
  await band.setInterval('temperature', 2);
  sim.step(1000);
  assert.ok(motion.length >= 200);
  const streams = { ...band.streams };

  log.length = 0;
  motion.length = 0;
  band.now = () => NOW + 60000;                                         // a minute later on the computer
  sim.dropLink();
  assert.equal(band.clock, 'computer');
  const back = await once(band, 'reconnected');
  assert.deepEqual(back.failed, []);
  assert.deepEqual(log, [
    { name: 'GET_INFO' },
    { name: 'TIME_SET', unix_ms: NOW + 60000 },
    { name: 'IMU_SET', rate_hz: 25, accel_range_g: 2, gyro_range_dps: 500 },
    { name: 'LED_SET', r: 0, g: 255, b: 0, brightness: 255, pattern: 1, period_ms: 1000 },
    { name: 'MOTION2_SET', rate_hz: 208, sensors: 1 },
    { name: 'STREAM_SET_INTERVAL', stream: 2, interval_ms: 500 },
    { name: 'STREAM_SET_INTERVAL', stream: 4, interval_ms: 30000 },
    { name: 'STREAM_SET_INTERVAL', stream: 5, interval_ms: 2000 },
    { name: 'STREAM_SET_INTERVAL', stream: 7, interval_ms: 40 },
    { name: 'STREAM_START', mask: BIT8 | 0b10110 },
  ]);
  assert.deepEqual(band.streams, streams);
  assert.equal(band.streams.motion, 208);
  assert.equal(band.clock, 'band');
  sim.step(500);
  assert.ok(motion.length >= 100);
  assert.equal(motion[0].n, 0);                                         // a new run of the stream
  assert.equal(motion[0].gx, null);
  near(motion[0].time, NOW + 60000 + (motion[0].t - 1000), 1e-6);       // band time went on from 1000; the clock is the new one
  assert.deepEqual(await band.getMotion(), { rate: 208, sensors: 'accel', accelRange: 2, gyroRange: 500 });
  assert.equal((await band.getInterval('micLevel')).seconds, 0.04);
  await band.disconnect();
});

test('auto-reconnect with setClock: false reads the clock again instead of setting it', async () => {
  const { band, sim, log } = await connect({}, { autoReconnect: true, setClock: false });
  await band.setTime(NOW - 5000);                                       // on purpose, by hand
  log.length = 0;
  sim.dropLink();
  await once(band, 'reconnected');
  assert.deepEqual(names(log), ['GET_INFO', 'TIME_GET']);
  assert.equal(band.clock, 'band');
  assert.equal((await band.time()).time, NOW - 5000);                   // still the band's own clock
  await band.disconnect();
});

test('auto-reconnect: the temperature interval is restored by the command that set it last', async () => {
  const { band, sim, log } = await connect({}, { autoReconnect: true });
  await band.setInterval('temperature', 0.5);
  await band.command('TEMP_SET_INTERVAL', { seconds: 9 });
  log.length = 0;
  sim.dropLink();
  await once(band, 'reconnected');
  assert.deepEqual(log.slice(2), [{ name: 'TEMP_SET_INTERVAL', seconds: 9 }]);
  await band.setInterval('temperature', 0.5);
  log.length = 0;
  sim.dropLink();
  await once(band, 'reconnected');
  assert.deepEqual(log.slice(2), [{ name: 'STREAM_SET_INTERVAL', stream: 5, interval_ms: 500 }]);
  await band.disconnect();
});

test('auto-reconnect: a band that comes back with firmware 1.0 gets no 1.1 command; what could not be restored is in `failed`', async () => {
  const { band, sim, log } = await connect({}, { autoReconnect: true });
  await band.configure({ motion: 416, sensors: 'accel', activity: 0.5, events: true });
  log.length = 0;
  sim.firmware = '1.0';                                                 // somebody swapped the firmware meanwhile
  sim.dropLink();
  const back = await once(band, 'reconnected');
  assert.deepEqual(back.failed, ['MOTION2_SET', 'STREAM_SET_INTERVAL', 'STREAM_START']);
  assert.deepEqual(log, [{ name: 'GET_INFO' }, { name: 'STREAM_START', mask: 0b110 }]);     // the streams a 1.0 band has
  assert.equal(band.info.protocol, '1.0');
  assert.deepEqual(band.info.features, []);
  assert.equal(band.clock, 'computer');
  assert.deepEqual(band.streams, { motion: 0, events: true, activity: true, audio: false, battery: false, temperature: false, link: false, micLevel: false });
  await fails(band.configure({ motion: 416 }), 'not-available');
  assert.equal((await band.configure({ motion: 100 })).motion, 100);    // and it works as a 1.0 band from here

  // the other way round: it comes back with 1.1, and the old settings are restored on the old stream
  log.length = 0;
  sim.firmware = '1.1';
  sim.dropLink();
  assert.deepEqual((await once(band, 'reconnected')).failed, []);
  assert.deepEqual(names(log), ['GET_INFO', 'TIME_SET', 'IMU_SET', 'STREAM_START']);
  assert.equal(log.at(-1).mask, 0b111);
  assert.equal(band.streams.motion, 100);
  assert.deepEqual(band.info.features, ['fast-motion', 'intervals', 'clock']);
  await band.disconnect();
});

// ---------------------------------------------------------------- the pretend band itself

test('simulator: MOTION2 caps by link, DENIED over the cap, BAD_ARG outside the sets', async () => {
  const ble = await rawSim();
  assert.deepEqual(ble.ask('MOTION2_GET').data, { rate_hz: 25, sensors: 3, max_accel_hz: 833, max_gyro_hz: 833, max_both_hz: 416 });
  const usb = await rawSim({ via: 'usb' });
  usb.sim.onWire = () => {};
  const usbBand = await Band.connect(42, { transport: new SimTransport(42, { realtime: false, via: 'usb' }) });
  assert.deepEqual(await usbBand.command('MOTION2_GET'), { rate_hz: 25, sensors: 3, max_accel_hz: 1666, max_gyro_hz: 833, max_both_hz: 833 });

  for (const rate of [0, 1, 20, 200, 104, 1667, 3330, 6660]) assert.equal(ble.ask('MOTION2_SET', { rate_hz: rate, sensors: 3 }).status, 'BAD_ARG', `${rate}`);
  for (const sensors of [0, 4, 255]) assert.equal(ble.ask('MOTION2_SET', { rate_hz: 25, sensors }).status, 'BAD_ARG');
  assert.deepEqual(ble.ask('MOTION2_GET').data.rate_hz, 25);            // nothing changed

  // not streaming: every allowed value is accepted, whatever the link
  for (const rate of ALL_RATES) for (const sensors of [1, 2, 3]) assert.equal(ble.ask('MOTION2_SET', { rate_hz: rate, sensors }).status, 'OK');
  // the cap is checked when the stream starts
  const start = (rate_hz, sensors, mask = BIT8) => {
    ble.ask('STREAM_STOP', { mask: 0xffff });
    ble.ask('MOTION2_SET', { rate_hz, sensors });
    return ble.ask('STREAM_START', { mask }).status;
  };
  assert.deepEqual([start(416, 3), start(833, 3), start(833, 1), start(833, 2), start(1666, 1), start(1666, 2)],
    ['OK', 'DENIED', 'OK', 'OK', 'DENIED', 'DENIED']);
  assert.equal(start(833, 3, BIT8 | 2), 'DENIED');
  assert.equal(ble.ask('STREAM_GET').data.active, 0);                   // nothing in that mask started
  // while it streams, a setting over the cap is refused and nothing changes
  assert.equal(start(416, 3), 'OK');
  assert.equal(ble.ask('MOTION2_SET', { rate_hz: 833, sensors: 3 }).status, 'DENIED');
  assert.equal(ble.ask('MOTION2_SET', { rate_hz: 1666, sensors: 1 }).status, 'DENIED');
  assert.equal(ble.ask('MOTION2_GET').data.rate_hz, 416);
  assert.equal(ble.ask('MOTION2_SET', { rate_hz: 833, sensors: 1 }).status, 'OK');

  for (const [rate, sensors, status] of [[833, 3, 'OK'], [833, 2, 'OK'], [1666, 1, 'OK'], [1666, 2, 'DENIED'], [1666, 3, 'DENIED']]) {
    await usbBand.command('STREAM_STOP', { mask: BIT8 });
    await usbBand.command('MOTION2_SET', { rate_hz: rate, sensors });
    const result = await usbBand.command('STREAM_START', { mask: BIT8 }).then(() => 'OK', (e) => e.code.toUpperCase());
    assert.equal(result, status, `${rate} ${sensors} over USB`);
  }
  // bits above 8 do not exist
  assert.equal(ble.ask('STREAM_START', { mask: 0x200 }).status, 'NOT_AVAILABLE');
  assert.equal(ble.ask('STREAM_START', { mask: 0x8001 }).status, 'NOT_AVAILABLE');
});

test('simulator: MOTION2 frame sizes, period_us, and sample numbers', async () => {
  const { sim, frames, ask } = await rawSim({ via: 'ble' });
  // rate -> samples per frame with one sensor / with both (contract 5.2)
  const sizes = { 10: [1, 1], 25: [1, 1], 50: [2, 2], 100: [5, 5], 208: [10, 10], 416: [20, 19], 833: [38, 19], 1666: [38, 19] };
  for (const rate of ALL_RATES) {
    for (const sensors of [1, 2, 3]) {
      ask('STREAM_STOP', { mask: BIT8 });
      ask('MOTION2_SET', { rate_hz: rate, sensors });
      sim._s.motion2 = { rate_hz: rate, sensors };
      sim._startStreams(BIT8);                                          // as STREAM_START does, without the cap of this link
      frames.length = 0;
      sim.step(2000);
      const count = sizes[rate][sensors === 3 ? 1 : 0];
      const what = `${rate} a second, sensors ${sensors}`;
      assert.ok(frames.length >= 2, what);
      let next = 0;
      for (const f of frames) {
        assert.equal(f.frame, 'motion2');
        assert.equal(f.sensors, sensors);
        assert.equal(f.samples.length, count, what);
        assert.equal(f.sample_no, next, what);                          // counts every sample, from 0
        next += count;
        assert.deepEqual(Object.keys(f.samples[0]), sensors === 1 ? ['ax', 'ay', 'az'] : sensors === 2 ? ['gx', 'gy', 'gz'] : ['ax', 'ay', 'az', 'gx', 'gy', 'gz']);
        assert.ok(codec.encodeStream('motion2', f).length <= 244);
        if (rate <= 100) assert.equal(f.period_us, 1000000 / rate, what);
        else assert.ok(f.period_us !== Math.round(1000000 / rate) && Math.abs(f.period_us * rate / 1000000 - 1) < 0.03, `${what}: ${f.period_us}`);
      }
      // t_ms is where the first sample of the frame is, by the band's own measure of the spacing
      const [a, b] = frames;
      assert.ok(Math.abs((b.t_ms - a.t_ms) - count * a.period_us / 1000) <= 1, what);
      // and the samples really come at that spacing: about rate * 2 of them in 2 seconds, by period_us
      assert.ok(Math.abs(next - 2000000 / a.period_us) <= count, `${what}: ${next} samples`);
    }
  }
  ask('STREAM_STOP', { mask: BIT8 });

  // numbers start at 0 on every start, and on a MOTION2_SET that changes the stream (not on one that changes nothing)
  ask('MOTION2_SET', { rate_hz: 100, sensors: 3 });
  ask('STREAM_START', { mask: BIT8 });
  frames.length = 0;
  sim.step(200);
  assert.deepEqual(frames.map((f) => f.sample_no), [0, 5, 10, 15]);
  ask('MOTION2_SET', { rate_hz: 100, sensors: 3 });                     // the same again
  sim.step(100);
  assert.deepEqual(frames.map((f) => f.sample_no).slice(4), [20, 25]);
  ask('MOTION2_SET', { rate_hz: 100, sensors: 1 });
  frames.length = 0;
  sim.step(100);
  assert.deepEqual(frames.map((f) => [f.sample_no, f.sensors]), [[0, 1], [5, 1]]);
  ask('MOTION2_SET', { rate_hz: 50, sensors: 1 });
  frames.length = 0;
  sim.step(100);
  assert.deepEqual(frames.map((f) => [f.sample_no, f.period_us]), [[0, 20000], [2, 20000]]);
  ask('STREAM_STOP', { mask: BIT8 });
  ask('STREAM_START', { mask: BIT8 });
  frames.length = 0;
  sim.step(40);
  assert.equal(frames[0].sample_no, 0);
});

test('simulator: MOTION and MOTION2 run together; gyro range 0 gives gyro 0 on both', async () => {
  const { sim, frames, ask } = await rawSim();
  ask('MOTION2_SET', { rate_hz: 208, sensors: 3 });
  assert.equal(ask('STREAM_START', { mask: BIT8 | 1 }).data.active, BIT8 | 1);
  sim.step(1000);
  const old = frames.filter((f) => f.frame === 'motion');
  const fast = frames.filter((f) => f.frame === 'motion2');
  assert.equal(old.length, 2);                                          // 25 a second, 10 per frame
  assert.ok(fast.length >= 20);
  assert.ok(fast.some((f) => f.samples.some((s) => s.gx !== 0)));
  near(fast[0].samples[0].az, 1000, 100);                               // the same pretend movement
  ask('IMU_SET', { rate_hz: 25, accel_range_g: 2, gyro_range_dps: 0 });
  frames.length = 0;
  sim.step(1000);
  for (const f of frames) for (const s of f.samples) assert.deepEqual([s.gx, s.gy, s.gz], [0, 0, 0]);
  assert.equal(ask('STREAM_STOP', { mask: 1 }).data.active, BIT8);
  frames.length = 0;
  sim.step(200);
  assert.ok(frames.length > 0 && frames.every((f) => f.frame === 'motion2'));
});

test('simulator: stream intervals and their limits', async () => {
  const { sim, frames, ask } = await rawSim();
  const get = (stream) => ask('STREAM_GET_INTERVAL', { stream });
  assert.deepEqual(get(2).data, { stream: 2, interval_ms: 1000, min_ms: 100, max_ms: 3600000 });
  assert.deepEqual(get(4).data, { stream: 4, interval_ms: 0, min_ms: 1000, max_ms: 3600000 });
  assert.deepEqual(get(5).data, { stream: 5, interval_ms: 5000, min_ms: 100, max_ms: 3600000 });
  assert.deepEqual(get(6).data, { stream: 6, interval_ms: 1000, min_ms: 100, max_ms: 3600000 });
  assert.deepEqual(get(7).data, { stream: 7, interval_ms: 100, min_ms: 20, max_ms: 10000 });
  for (const stream of [0, 1, 3, 8, 9, 255]) {
    assert.equal(get(stream).status, 'BAD_ARG');
    assert.equal(ask('STREAM_SET_INTERVAL', { stream, interval_ms: 1000 }).status, 'BAD_ARG');
  }
  const set = (stream, interval_ms) => ask('STREAM_SET_INTERVAL', { stream, interval_ms }).status;
  assert.deepEqual([set(2, 99), set(2, 100), set(2, 3600000), set(2, 3600001), set(2, 0)], ['BAD_ARG', 'OK', 'OK', 'BAD_ARG', 'BAD_ARG']);
  assert.deepEqual([set(4, 999), set(4, 1000), set(4, 0), set(4, 3600001)], ['BAD_ARG', 'OK', 'OK', 'BAD_ARG']);
  assert.deepEqual([set(5, 99), set(5, 100), set(6, 99), set(6, 100)], ['BAD_ARG', 'OK', 'BAD_ARG', 'OK']);
  assert.deepEqual([set(7, 0), set(7, 10), set(7, 20), set(7, 30), set(7, 40), set(7, 10000), set(7, 10020)],
    ['BAD_ARG', 'BAD_ARG', 'OK', 'BAD_ARG', 'OK', 'OK', 'BAD_ARG']);
  assert.deepEqual(ask('STREAM_SET_INTERVAL', { stream: 2, interval_ms: 250 }).data, { stream: 2, interval_ms: 250, min_ms: 100, max_ms: 3600000 });
  assert.equal(set(2, 5), 'BAD_ARG');
  assert.equal(get(2).data.interval_ms, 250);                           // a refused value changes nothing

  // TEMP_SET_INTERVAL and stream 5 are one setting
  assert.deepEqual(ask('TEMP_SET_INTERVAL', { seconds: 7 }).data, { seconds: 7 });
  assert.equal(get(5).data.interval_ms, 7000);
  set(5, 200);
  set(6, 500);
  set(7, 20);
  ask('STREAM_START', { mask: 0b11100100 });
  frames.length = 0;
  sim.step(1000);
  const count = (name) => frames.filter((f) => f.frame === name).length;
  assert.deepEqual([count('activity'), count('temp'), count('link'), count('mic_level')], [4, 5, 2, 50]);
  // set while it runs: the new interval counts from that moment
  sim.step(30);
  set(5, 1000);
  frames.length = 0;
  sim.step(1000);
  assert.deepEqual(frames.filter((f) => f.frame === 'temp').map((f) => f.t_ms), [2030]);
  // the settings go back to their defaults with the link
  sim.dropLink();
  await sim.open();
  assert.deepEqual([get(2).data.interval_ms, get(5).data.interval_ms, get(7).data.interval_ms], [1000, 5000, 100]);
});

test('simulator: battery on change (interval 0) or on a timer', async () => {
  const { sim, frames, ask } = await rawSim();
  ask('STREAM_START', { mask: 0x10 });
  assert.equal(frames.length, 1);                                       // once at start
  sim.step(5000);
  assert.equal(frames.length, 1);
  sim.setBattery(80);
  assert.equal(frames.length, 2);                                       // on change
  ask('STREAM_SET_INTERVAL', { stream: 4, interval_ms: 2000 });
  frames.length = 0;
  sim.step(6000);
  assert.equal(frames.length, 3);                                       // every 2 seconds, changed or not
  sim.setBattery(70);
  assert.equal(frames.length, 3);                                       // the timer sends it, not the change
  sim.step(2000);
  assert.equal(frames.at(-1).percent, 70);
  ask('STREAM_SET_INTERVAL', { stream: 4, interval_ms: 0 });
  frames.length = 0;
  sim.step(10000);
  assert.equal(frames.length, 0);
  sim.setBattery(60);
  assert.equal(frames.length, 1);
  // periodic from the start: one at start, then on the timer
  ask('STREAM_STOP', { mask: 0x10 });
  ask('STREAM_SET_INTERVAL', { stream: 4, interval_ms: 1000 });
  frames.length = 0;
  ask('STREAM_START', { mask: 0x10 });
  sim.step(3000);
  assert.equal(frames.length, 4);
});

test('simulator: the clock: range, kept across a dropped link and across clients, lost on restart', async () => {
  const { sim, ask } = await rawSim();
  sim.step(1234);
  assert.deepEqual(ask('TIME_GET').data, { set: 0, unix_ms: 0, t_ms: 1234 });
  for (const bad of [0, 1794484800, 1577836799999, 4102444800000, NOW * 1000]) {
    assert.equal(ask('TIME_SET', { unix_ms: bad }).status, 'BAD_ARG', `${bad}`);
  }
  assert.equal(ask('TIME_GET').data.set, 0);                            // nothing changed
  assert.deepEqual(ask('TIME_SET', { unix_ms: 1577836800000 }).data, { set: 1, unix_ms: 1577836800000, t_ms: 1234 });
  assert.equal(ask('TIME_SET', { unix_ms: 4102444799999 }).status, 'OK');
  assert.deepEqual(ask('TIME_SET', { unix_ms: NOW }).data, { set: 1, unix_ms: NOW, t_ms: 1234 });
  sim.step(766);
  assert.deepEqual(ask('TIME_GET').data, { set: 1, unix_ms: NOW + 766, t_ms: 2000 });

  sim.dropLink();
  await sim.open();
  assert.deepEqual(ask('TIME_GET').data, { set: 1, unix_ms: NOW + 766, t_ms: 2000 });     // another client finds it set
  sim.step(1000);
  assert.equal(ask('TIME_GET').data.unix_ms, NOW + 1766);
  await sim.close();
  await sim.open();
  assert.equal(ask('TIME_GET').data.set, 1);

  ask('RESTART');
  sim.step(300);                                                        // the band restarts 200 ms after its answer
  await sim.open();
  // The clock is gone, and the band's counter began again at 0 when it restarted (at 3200), 100 ms ago.
  assert.deepEqual(ask('TIME_GET').data, { set: 0, unix_ms: 0, t_ms: 100 });
  assert.equal(ask('GET_INFO').data.uptime_ms, 100);
});

test("simulator: firmware '1.0' is a band from before all this", async () => {
  const { sim, frames, ask } = await rawSim({ firmware: '1.0' });
  const info = ask('GET_INFO').data;
  assert.deepEqual([info.proto_major, info.proto_minor, info.fw_major, info.fw_minor, info.fw_patch], [1, 0, 1, 0, 0]);
  const args = {
    TIME_SET: { unix_ms: NOW }, STREAM_SET_INTERVAL: { stream: 2, interval_ms: 500 }, STREAM_GET_INTERVAL: { stream: 2 },
    MOTION2_SET: { rate_hz: 25, sensors: 3 },
  };
  for (const name of V11_COMMANDS) assert.deepEqual(ask(name, args[name]), { status: 'UNKNOWN_OPCODE', data: {} }, name);
  // also with the wrong number of bytes: a band that does not know the command cannot check its length
  sim._command(Uint8Array.of(1, 9, codec.OPCODES.TIME_SET, 1, 2));
  assert.equal(ask('STREAM_START', { mask: BIT8 }).status, 'NOT_AVAILABLE');
  assert.equal(ask('STREAM_START', { mask: BIT8 | 0b100 }).status, 'NOT_AVAILABLE');
  assert.equal(ask('STREAM_GET').data.active, 0);                       // nothing started
  assert.equal(ask('STREAM_START', { mask: 0b100 }).data.active, 0b100);
  sim.step(3000);
  assert.equal(frames.filter((f) => f.frame === 'activity').length, 3);     // the fixed rate of 1.0
  assert.equal(ask('STREAM_STOP', { mask: BIT8 | 0b100 }).data.active, 0);  // unknown bits in a stop are ignored, as before

  const now = await rawSim();
  const fresh = now.ask('GET_INFO').data;
  assert.deepEqual([fresh.proto_major, fresh.proto_minor, fresh.fw_major, fresh.fw_minor, fresh.fw_patch], [1, 1, 1, 1, 0]);
  assert.throws(() => new SimTransport(42, { firmware: '2.0' }), RangeError);
  assert.equal(new SimTransport(42).firmware, '1.1');
});

// ---------------------------------------------------------------- 1666 Hz never with the gyroscope (contract 5.2)

test('simulator: 1666 Hz never together with the gyroscope, whichever motion stream comes second', async () => {
  const { band, sim } = await connect({ via: 'usb' });
  const status = (promise) => promise.then(() => 'OK', (e) => e.code.toUpperCase());
  const active = async () => (await band.command('STREAM_GET')).active;
  const GYRO_ON = { rate_hz: 25, accel_range_g: 2, gyro_range_dps: 2000 };
  const GYRO_OFF = { ...GYRO_ON, gyro_range_dps: 0 };

  // MOTION2 runs at 1666 (accelerometer alone, the only way): MOTION, which runs the gyroscope, may not start
  await band.command('MOTION2_SET', { rate_hz: 1666, sensors: 1 });
  await band.command('STREAM_START', { mask: BIT8 });
  await fails(band.command('STREAM_START', { mask: 1 }), 'denied', /1666 samples per second never runs together with the gyroscope/);
  assert.equal(await status(band.command('STREAM_START', { mask: 0b101 })), 'DENIED');
  assert.equal(await active(), BIT8);                                   // nothing in that mask started
  assert.equal(await status(band.command('STREAM_START', { mask: 0b100 })), 'OK');      // other streams are free
  await band.command('STREAM_STOP', { mask: 0b100 });
  // ... unless the gyroscope is off (range 0): then MOTION starts, and the gyroscope cannot be switched on
  assert.deepEqual(await band.command('IMU_SET', GYRO_OFF), GYRO_OFF);
  assert.equal((await band.command('STREAM_START', { mask: 1 })).active, BIT8 | 1);
  await fails(band.command('IMU_SET', GYRO_ON), 'denied', /never runs together with the gyroscope/);
  assert.deepEqual(await band.command('IMU_GET'), GYRO_OFF);            // nothing changed
  assert.deepEqual(await band.command('IMU_SET', { ...GYRO_OFF, rate_hz: 50, accel_range_g: 8 }), { ...GYRO_OFF, rate_hz: 50, accel_range_g: 8 });
  // a slower MOTION2 frees the gyroscope again
  await band.command('MOTION2_SET', { rate_hz: 833, sensors: 1 });
  assert.deepEqual(await band.command('IMU_SET', GYRO_ON), GYRO_ON);
  // ... and now MOTION runs the gyroscope, so MOTION2 cannot go back to 1666 while it streams
  await fails(band.command('MOTION2_SET', { rate_hz: 1666, sensors: 1 }), 'denied', /never runs together with the gyroscope/);
  assert.deepEqual([sim._s.motion2.rate_hz, await active()], [833, BIT8 | 1]);          // nothing changed

  // the other order: MOTION streams with the gyroscope on, MOTION2 is off
  await band.command('STREAM_STOP', { mask: BIT8 });
  assert.equal(await status(band.command('MOTION2_SET', { rate_hz: 1666, sensors: 1 })), 'OK');     // not streaming: any setting is taken
  await fails(band.command('STREAM_START', { mask: BIT8 }), 'denied', /never runs together with the gyroscope/);
  assert.equal(await active(), 1);
  // both in one start
  await band.command('STREAM_STOP', { mask: 1 });
  assert.equal(await status(band.command('STREAM_START', { mask: BIT8 | 1 })), 'DENIED');
  assert.equal(await status(band.command('STREAM_START', { mask: BIT8 | 0b11 })), 'DENIED');
  assert.equal(await active(), 0);
  // with the gyroscope off both run, in either order and in one start
  await band.command('IMU_SET', GYRO_OFF);
  assert.equal((await band.command('STREAM_START', { mask: BIT8 | 1 })).active, BIT8 | 1);
  await band.command('STREAM_STOP', { mask: BIT8 | 1 });
  await band.command('STREAM_START', { mask: 1 });
  assert.equal((await band.command('STREAM_START', { mask: BIT8 })).active, BIT8 | 1);
  // 1666 with the gyroscope in MOTION2 itself stays what it was: over the cap
  await fails(band.command('MOTION2_SET', { rate_hz: 1666, sensors: 3 }), 'denied', /too much for the USB cable/);
  // below 1666 nothing of this applies
  await band.command('STREAM_STOP', { mask: BIT8 | 1 });
  await band.command('IMU_SET', GYRO_ON);
  await band.command('MOTION2_SET', { rate_hz: 833, sensors: 3 });
  assert.equal((await band.command('STREAM_START', { mask: BIT8 | 1 })).active, BIT8 | 1);

  // Bluetooth never carries 1666, so there the cap answers first
  const ble = await connect();
  await ble.band.command('STREAM_START', { mask: 1 });
  await ble.band.command('MOTION2_SET', { rate_hz: 1666, sensors: 1 });
  await fails(ble.band.command('STREAM_START', { mask: BIT8 }), 'denied', /too much for Bluetooth/);
});

test('the library never runs the two motion streams side by side, so configure meets no 1666 refusal', async () => {
  const { band, sim, log } = await connect({ via: 'usb' });
  assert.equal((await band.configure({ motion: 'fastest', sensors: 'accel' })).motion, 1666);
  assert.equal(sim._s.streams, BIT8);
  await band.setMotion({ gyroRange: 500 });                             // the gyroscope's range is free: MOTION is not running
  assert.equal((await band.configure({ motion: 833, sensors: 'both' })).motion, 833);
  assert.equal((await band.configure({ motion: 1666, sensors: 'accel' })).motion, 1666);
  assert.ok(log.every((c) => c.name !== 'STREAM_START' || !(c.mask & 1)));
});
