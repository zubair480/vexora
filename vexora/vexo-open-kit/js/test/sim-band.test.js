// What a real band does that the pretend band must copy: the differences between the USB
// cable and Bluetooth, the storage numbers and limits, and the "reachable" switch.
// Also the library's answers to them: storage calls that wait while the band is busy.
import test from 'node:test';
import assert from 'node:assert/strict';
import { Band, SimTransport, Transport, VexoError, codec } from '../src/index.js';

const INFO = { proto_major: 1, proto_minor: 0, fw_major: 1, fw_minor: 0, fw_patch: 0, hw: 1, band_no: 42, caps: 0x7ff, max_frame: 244, uptime_ms: 0 };

async function connectSim(simOptions = {}, bandOptions = {}) {
  const sim = new SimTransport(42, { realtime: false, firmware: '1.0', ...simOptions });
  const band = await Band.connect(42, { transport: sim, ...bandOptions });
  band.storage._busyPauseMs = 1;             // the tests move the band's clock themselves
  return { sim, band };
}

function collect(band, type) {
  const list = [];
  band.on(type, (e) => list.push(e));
  return list;
}

const fails = (promise, code, pattern = /./) => assert.rejects(promise, (e) => {
  assert.ok(e instanceof VexoError, `expected a VexoError, got ${e}`);
  assert.equal(e.code, code);
  assert.match(e.message, pattern);
  return true;
});

const tick = () => new Promise((resolve) => setTimeout(resolve, 1));

/** Let band time pass (stepMs at a time) until the call has finished. */
async function whileStepping(sim, promise, stepMs = 100) {
  let done = false;
  const settled = promise.then(() => { done = true; }, () => { done = true; });
  while (!done) {
    sim.step(stepMs);
    await tick();
  }
  await settled;
  return promise;
}

/** A band that answers every command with a status the test chooses. */
class Scripted extends Transport {
  kind = 'custom';
  sent = [];
  reply = () => 'OK';
  async open() {}
  async send(frame) {
    const cmd = codec.decodeCommand(frame);
    this.sent.push(cmd.opcode);
    if (cmd.opcode === 'GET_INFO') return this.onFrame(codec.encodeResponse(cmd.seq, 'GET_INFO', 'OK', INFO));
    const answer = this.reply(cmd);
    if (typeof answer === 'string') this.onFrame(codec.encodeResponse(cmd.seq, cmd.opcode, answer, {}));
    else this.onFrame(codec.encodeResponse(cmd.seq, cmd.opcode, 'OK', answer));
  }
}
const STORE = { keys_max: 1000, value_max: 232, free_bytes: 242164, used_keys: 0 };

// ---------------------------------------------------------------- A1, A2, A3: the cable is not Bluetooth

test('A1: RADIO_GET over USB has no radio numbers, also no mtu', async () => {
  const usb = await connectSim({ via: 'usb' });
  assert.deepEqual(await usb.band.command('RADIO_GET'), { tx_dbm: -8, rssi_dbm: 127, phy: 0, interval: 0, mtu: 0 });
  assert.deepEqual(await usb.band.getRadio(), { txPower: -8, rssi: null, phy: null, intervalMs: 0, mtu: 0 });
  const ble = await connectSim();
  assert.equal((await ble.band.command('RADIO_GET')).mtu, 247);         // Bluetooth is unchanged
});

test('A2 + C2: over USB a STREAM_START with `link` in it is refused whole, and the error says why', async () => {
  const { band, sim } = await connectSim({ via: 'usb' });
  const motion = collect(band, 'motion');
  await fails(band.command('STREAM_START', { mask: 0x40 }), 'not-available');
  await assert.rejects(band.configure({ motion: 50, link: true, battery: true }), (e) => {
    assert.equal(e.code, 'not-available');
    assert.match(e.message, /`link` stream \(signal strength\) only exists over Bluetooth/);
    assert.match(e.message, /USB cable, leave `link` out/);
    assert.match(e.message, /Nothing in this configure call was started/);
    return true;
  });
  assert.equal((await band.command('STREAM_GET')).active, 0);           // nothing in that mask started
  assert.deepEqual(band.streams, { motion: 0, events: false, activity: false, audio: false, battery: false, temperature: false, link: false, micLevel: false });
  sim.step(1000);
  assert.equal(motion.length, 0);
  await band.configure({ motion: 50, battery: true });                  // without `link` it works
  sim.step(1000);
  assert.equal(motion.length, 50);
  // Streams that already run are not touched by a refused start.
  await fails(band.configure({ link: true, events: true }), 'not-available');
  assert.equal((await band.command('STREAM_GET')).active, 0x11);
  assert.equal(band.streams.motion, 50);
});

test('A2 + C2: over Bluetooth `link` starts; another NOT_AVAILABLE keeps the usual sentence', async () => {
  const { band, sim } = await connectSim();
  const link = collect(band, 'link');
  await band.configure({ link: true });
  sim.step(2000);
  assert.equal(link.length, 2);
  await fails(band.command('STREAM_START', { mask: 0x0100 }), 'not-available', /not working on this band/);
  // A mask with `link` that the band refuses for another reason on a link we cannot name: the link sentence.
  await fails(band.command('STREAM_START', { mask: 0x0140 }), 'not-available', /only exists over Bluetooth/);
  // Web Bluetooth always has `link`, so there the sentence would be wrong.
  band.transportKind = 'ble';
  await fails(band.command('STREAM_START', { mask: 0x0140 }), 'not-available', /not working on this band/);
});

test('A3 + C1: sleep over USB stops the streams and the LED after 200 ms, and the session stays open', async () => {
  const { band, sim } = await connectSim({ via: 'usb' });
  const gone = collect(band, 'disconnect');
  const motion = collect(band, 'motion');
  await band.setLed('#ff0000');
  await band.configure({ motion: 100, events: true, battery: true });
  await band.sleep();
  assert.deepEqual(band.streams, { motion: 0, events: false, activity: false, audio: false, battery: false, temperature: false, link: false, micLevel: false });
  sim.step(199);
  assert.equal(sim.led.pattern, 1);                                     // not yet
  assert.equal(motion.length, 10);
  sim.step(1);
  assert.equal(sim.led.pattern, 0);                                     // user LED off
  assert.equal((await band.command('STREAM_GET')).active, 0);
  motion.length = 0;
  sim.step(5000);
  assert.equal(motion.length, 0);
  assert.equal(band.connected, true);                                   // no close, no "sleep" disconnect
  assert.deepEqual(gone, []);
  assert.equal((await band.getInfo()).number, 42);                      // it keeps answering
  await band.setLed('#00ff00');
  await band.configure({ motion: 100 });
  sim.step(1000);
  assert.equal(motion.length, 100);
  assert.equal(sim.led.g, 255);
});

test('A3: sleep over Bluetooth and restart on both links still close the link', async () => {
  for (const [via, method] of [['ble', 'sleep'], ['ble', 'restart'], ['usb', 'restart']]) {
    const { band, sim } = await connectSim({ via });
    const gone = collect(band, 'disconnect');
    await band[method]();
    sim.step(200);
    assert.deepEqual(gone, [{ reason: method }], `${via} ${method}`);
    assert.equal(band.connected, false);
  }
});

test('C1: restart or sleep explain a close for 3 seconds only; later it is reported as it is', async (t) => {
  for (const method of ['restart', 'sleep']) {
    const soon = await connectSim();
    const first = collect(soon.band, 'disconnect');
    await soon.band[method]();
    soon.band._closed('lost');                       // what a real Bluetooth link reports
    assert.deepEqual(first, [{ reason: method }]);
  }
  t.mock.timers.enable({ apis: ['Date'], now: 1_000_000 });
  for (const method of ['restart', 'sleep']) {
    const late = await connectSim({ via: 'usb' });   // over USB a sleeping band stays connected
    const second = collect(late.band, 'disconnect');
    await late.band[method]();
    t.mock.timers.tick(3001);
    late.band._closed('lost');                       // the cable is pulled much later
    assert.deepEqual(second, [{ reason: 'lost' }], method);
  }
});

// ---------------------------------------------------------------- C4: signal strength

test('C4: rssi -60 stays -60 and 127 (no reading) becomes null, in getRadio and in the link event', async () => {
  const transport = new Scripted();
  const band = await Band.connect(42, { transport });
  let rssi = -60;
  transport.reply = () => ({ tx_dbm: -8, rssi_dbm: rssi, phy: 2, interval: 24, mtu: 247 });
  assert.equal((await band.getRadio()).rssi, -60);
  rssi = 127;
  assert.equal((await band.getRadio()).rssi, null);
  rssi = -128;
  assert.equal((await band.getRadio()).rssi, -128);
  const link = collect(band, 'link');
  transport.onFrame(codec.encodeStream('link', { t_ms: 1, rssi_dbm: -60, tx_dbm: -8, phy: 2 }));
  transport.onFrame(codec.encodeStream('link', { t_ms: 2, rssi_dbm: 127, tx_dbm: -8, phy: 2 }));
  assert.deepEqual(link.map((l) => l.rssi), [-60, null]);
});

// ---------------------------------------------------------------- A4: storage numbers

test('A4: free bytes: 242164 when empty; a key costs 8 bytes plus its value rounded up to 4', async () => {
  const { band, sim } = await connectSim();
  const free = async () => (await band.storage.info()).freeBytes;
  assert.deepEqual(await band.storage.info(), { keysMax: 1000, valueMax: 232, freeBytes: 242164, usedKeys: 0 });
  await band.storage.put(1, 'a');                                       // 1 byte -> 8 + 4
  assert.equal(await free(), 242164 - 12);
  await band.storage.put(2, 'abcd');                                    // 4 bytes -> 8 + 4
  assert.equal(await free(), 242164 - 24);
  await band.storage.put(3, 'abcde');                                   // 5 bytes -> 8 + 8
  assert.equal(await free(), 242164 - 40);
  await band.storage.put(3, new Uint8Array(232));                       // replaced: 8 + 232 instead of 16
  assert.equal(await free(), 242164 - 24 - 240);
  await band.storage.delete(3);
  assert.equal(await free(), 242164 - 24);
  assert.equal(sim._store.size, 2);
});

test('A4: 1000 keys of 232 bytes fit; NO_SPACE when the new cost does not fit', async () => {
  const { band, sim } = await connectSim();
  // Fill the store behind the band's back (1000 real puts would need 100 seconds of band time).
  for (let key = 0; key < 1000; key++) sim._store.set(key, new Uint8Array(232));
  assert.deepEqual(await band.storage.info(), { keysMax: 1000, valueMax: 232, freeBytes: 242164 - 240000, usedKeys: 1000 });
  await band.storage.put(5, new Uint8Array(232).fill(1));               // replacing a full-size value fits
  // Now a store with only 4 bytes left (the pretend band does not mind the odd key numbers).
  sim._store.clear();
  for (let key = 0; key < 1009; key++) sim._store.set(key + 2000, new Uint8Array(232));   // 1009 x 240 = 242160
  assert.equal((await band.storage.info()).freeBytes, 4);
  await fails(band.storage.put(1, 'x'), 'no-space');                    // needs 12
  assert.equal(await band.storage.get(1), null);
  sim._store.delete(2000);
  sim._store.set(1, new Uint8Array(232));                               // key 1 holds 240 again: free is 4
  await band.storage.put(1, new Uint8Array(232).fill(2));               // same cost after removing the old value
  await band.storage.put(1, 'small');
  assert.equal((await band.storage.info()).freeBytes, 4 + 240 - 16);
});

// ---------------------------------------------------------------- A5: 10 writes a second

test('A5: the 11th write within a second of band time gets BUSY and changes nothing', async () => {
  const { band, sim } = await connectSim();
  const put = (key) => band.command('STORE_PUT', { key, value: Uint8Array.of(key) });
  for (let key = 0; key < 10; key++) await put(key);
  await fails(put(10), 'busy');
  assert.equal(sim._store.has(10), false);
  await fails(band.command('STORE_DEL', { key: 0 }), 'busy');           // a delete is a write too
  assert.equal(sim._store.has(0), true);
  assert.deepEqual((await band.command('STORE_GET', { key: 0 })).value, Uint8Array.of(0));   // reading is not limited
  assert.equal((await band.command('STORE_INFO')).used_keys, 10);
  sim.step(999);
  await fails(put(10), 'busy');                                         // refused writes did not count, but the 10 still do
  sim.step(1);
  for (let key = 10; key < 20; key++) await put(key);                   // a second later: 10 more
  await fails(put(20), 'busy');
  assert.equal(sim._store.size, 20);
});

test('A5: a sliding second, and the order of the checks: arguments, then BUSY, then NOT_FOUND / NO_SPACE', async () => {
  const { band, sim } = await connectSim();
  const put = (key) => band.command('STORE_PUT', { key, value: Uint8Array.of(1) });
  for (let key = 0; key < 5; key++) await put(key);
  sim.step(600);
  for (let key = 5; key < 10; key++) await put(key);
  await fails(put(10), 'busy');
  sim.step(400);                                                        // the first five are a second old
  for (let key = 10; key < 15; key++) await put(key);
  await fails(put(15), 'busy');
  // While busy: wrong arguments are still reported as such ...
  await fails(band.command('STORE_PUT', { key: 1, value: new Uint8Array(233) }), 'bad-length');
  await fails(band.command('STORE_PUT', { key: 1000, value: Uint8Array.of(1) }), 'bad-arg');
  await fails(band.command('STORE_DEL', { key: 1000 }), 'bad-arg');
  // ... and BUSY comes before "nothing stored there".
  await fails(band.command('STORE_DEL', { key: 700 }), 'busy');
  sim.step(1000);
  await fails(band.command('STORE_DEL', { key: 700 }), 'not-found');
  // Only writes answered OK count: after nine puts and a refused delete there is room for one more.
  for (let key = 20; key < 29; key++) await put(key);
  await put(29);
  await fails(put(30), 'busy');
});

test('A5: only writes answered OK count: 11 deletes of an empty key are all NOT_FOUND, then a put is still OK', async () => {
  const { band, sim } = await connectSim();
  for (let i = 0; i < 11; i++) await fails(band.command('STORE_DEL', { key: 5 }), 'not-found');
  assert.deepEqual(await band.command('STORE_PUT', { key: 5, value: Uint8Array.of(1) }), {});
  // A put answered NO_SPACE does not count either.
  for (let key = 0; key < 1009; key++) sim._store.set(key + 2000, new Uint8Array(232));
  sim._store.delete(5);                                                 // 4 bytes free
  for (let i = 0; i < 11; i++) await fails(band.command('STORE_PUT', { key: 6, value: Uint8Array.of(1) }), 'no-space');
  sim._store.delete(2000);                                              // room again
  assert.deepEqual(await band.command('STORE_PUT', { key: 6, value: Uint8Array.of(1) }), {});
});

test('A6: while a wipe runs, wrong arguments still answer BAD_ARG / BAD_LENGTH; wipeMs 0 finishes at once', async () => {
  const { band } = await connectSim();
  await band.command('STORE_WIPE');
  await fails(band.command('STORE_WIPE', { magic: 1 }), 'bad-arg');
  await fails(band.command('STORE_GET', { key: 1000 }), 'bad-arg');
  await fails(band.command('STORE_DEL', { key: 1000 }), 'bad-arg');
  await fails(band.command('STORE_PUT', { key: 1000, value: Uint8Array.of(1) }), 'bad-arg');
  await fails(band.command('STORE_PUT', { key: 1, value: new Uint8Array(233) }), 'bad-length');
  await fails(band.command('STORE_GET', { key: 1 }), 'busy');
  const instant = await connectSim({ wipeMs: 0 });
  await instant.band.command('STORE_PUT', { key: 1, value: Uint8Array.of(1) });
  await instant.band.command('STORE_WIPE');
  assert.equal((await instant.band.command('STORE_INFO')).used_keys, 0);   // no BUSY at all
});

// ---------------------------------------------------------------- A6: the wipe takes time

test('A6: STORE_WIPE answers OK at once, then every storage command is BUSY for wipeMs of band time', async () => {
  const { band, sim } = await connectSim();
  await band.storage.put(1, 'kept until the wipe');
  await fails(band.command('STORE_WIPE', { magic: 1 }), 'bad-arg');
  assert.equal(await band.storage.getText(1), 'kept until the wipe');
  assert.deepEqual(await band.command('STORE_WIPE'), {});
  const all = () => [
    band.command('STORE_INFO'), band.command('STORE_PUT', { key: 1, value: Uint8Array.of(1) }),
    band.command('STORE_GET', { key: 1 }), band.command('STORE_DEL', { key: 1 }), band.command('STORE_WIPE'),
  ];
  for (const call of all()) await fails(call, 'busy');
  sim.step(999);
  for (const call of all()) await fails(call, 'busy');
  assert.equal((await band.getLed()).pattern, 'off');                   // everything else still answers
  sim.step(1);
  assert.deepEqual(await band.command('STORE_INFO'), STORE);            // over: the store is empty
  await fails(band.command('STORE_GET', { key: 1 }), 'not-found');
});

test('A6: wipeMs is a constructor option, and a wipe goes on when the link closes', async () => {
  const quick = await connectSim({ wipeMs: 50 });
  await quick.band.command('STORE_WIPE');
  quick.sim.step(49);
  await fails(quick.band.command('STORE_INFO'), 'busy');
  quick.sim.step(1);
  assert.equal((await quick.band.command('STORE_INFO')).used_keys, 0);

  const { band, sim } = await connectSim();
  assert.equal(sim.wipeMs, 1000);
  await band.storage.put(1, 'x');
  await band.command('STORE_WIPE');
  sim.step(400);
  sim.dropLink();
  const again = await Band.connect(42, { transport: sim });
  await fails(again.command('STORE_INFO'), 'busy');                     // still erasing (band time only runs while connected)
  sim.step(600);
  assert.equal((await again.command('STORE_INFO')).used_keys, 0);
});

// ---------------------------------------------------------------- A7: reachable

test('A7: while reachable is false, open() fails with not-found-nearby; dropLink leaves the band reachable', async () => {
  const sim = new SimTransport(42, { realtime: false });
  assert.equal(sim.reachable, true);
  sim.reachable = false;
  await fails(Band.connect(42, { transport: sim }), 'not-found-nearby');
  sim.reachable = true;
  const band = await Band.connect(42, { transport: sim });
  await band.storage.put(1, 'kept');
  await band.setLed('#ff0000');
  sim.step(1234);
  sim.dropLink();
  assert.equal(sim.reachable, true);
  const again = await Band.connect(42, { transport: sim });
  assert.equal(await again.storage.getText(1), 'kept');                 // storage, number and clock survive
  assert.equal(again.info.uptimeMs, 1234);
  assert.equal((await again.getLed()).pattern, 'off');                  // settings reset
});

// ---------------------------------------------------------------- B1: storage calls wait for a busy band

test('B1: a loop of 30 puts just takes about 3 seconds of band time', async () => {
  const { band, sim } = await connectSim();
  const loop = (async () => {
    for (let key = 0; key < 30; key++) await band.storage.put(key, `value ${key}`);
  })();
  await whileStepping(sim, loop, 50);
  assert.equal(sim._store.size, 30);
  assert.ok(sim._t >= 2000 && sim._t <= 3500, `took ${sim._t} ms of band time`);
  assert.equal(await band.storage.getText(29), 'value 29');
});

test('B1: put, get, getText, delete and info all send again after BUSY', async () => {
  const transport = new Scripted();
  const band = await Band.connect(42, { transport });
  band.storage._busyPauseMs = 2;
  let busy = 0;
  transport.reply = (cmd) => {
    if (busy-- > 0) return 'BUSY';
    if (cmd.opcode === 'STORE_GET') return { value: Uint8Array.of(104, 105) };
    return cmd.opcode === 'STORE_INFO' ? STORE : {};
  };
  const calls = {
    STORE_PUT: () => band.storage.put(1, 'hi'),
    STORE_GET: () => band.storage.get(1),
    STORE_DEL: () => band.storage.delete(1),
    STORE_INFO: () => band.storage.info(),
  };
  for (const [opcode, call] of Object.entries(calls)) {
    transport.sent.length = 0;
    busy = 3;
    await call();
    assert.deepEqual(transport.sent, [opcode, opcode, opcode, opcode]);
  }
  busy = 2;
  assert.equal(await band.storage.getText(1), 'hi');
  // Other refusals are not retried.
  transport.sent.length = 0;
  transport.reply = () => 'NO_SPACE';
  await fails(band.storage.put(1, 'hi'), 'no-space');
  assert.deepEqual(transport.sent, ['STORE_PUT']);
});

test('B1: defaults are 100 ms and 3 s; after the limit the call fails with busy and says to try later', async () => {
  const transport = new Scripted();
  const band = await Band.connect(42, { transport });
  assert.equal(band.storage._busyPauseMs, 100);
  assert.equal(band.storage._busySeconds, 3);
  band.storage._busyPauseMs = 5;
  band.storage._busySeconds = 0.04;
  transport.reply = () => 'BUSY';
  for (const call of [() => band.storage.put(1, 'x'), () => band.storage.get(1), () => band.storage.getText(1),
    () => band.storage.delete(1), () => band.storage.info(), () => band.storage.wipe()]) {
    transport.sent.length = 0;
    const started = Date.now();
    await fails(call(), 'busy', /storage is still busy.*10 writes a second.*[Tt]ry again later/);
    const took = Date.now() - started;
    assert.ok(took >= 25 && took < 400, `gave up after about 40 ms (${took})`);
    assert.ok(transport.sent.length >= 3 && transport.sent.length <= 9, `sent ${transport.sent.length} times`);
  }
});

// ---------------------------------------------------------------- B2, B3: wipe waits for the erase

test('B2: wipe() returns when the erase is over, so a put straight after it works', async () => {
  const { band, sim } = await connectSim();
  await band.storage.put(1, 'old');
  const seen = [];
  const forward = sim.onFrame;
  sim.onFrame = (bytes) => { seen.push(codec.decodeFrame(bytes)); forward(bytes); };
  let finished = false;
  const wiping = band.storage.wipe().then(() => { finished = true; });
  for (let i = 0; i < 20; i++) await tick();
  assert.equal(finished, false);                                        // still erasing: wipe() has not returned
  await whileStepping(sim, wiping, 100);
  assert.ok(sim._t >= 1000);
  await band.storage.put(2, 'new');                                     // no BUSY
  assert.equal(await band.storage.get(1), null);
  assert.equal(await band.storage.getText(2), 'new');
  const responses = seen.filter((f) => f.frame === 'response').map((f) => `${f.opcode} ${f.status}`);
  assert.equal(responses[0], 'STORE_WIPE OK');
  assert.ok(responses.includes('STORE_INFO BUSY'));                     // it asked while the band erased
  assert.equal(responses.filter((r) => r === 'STORE_WIPE OK').length, 1);
});

test('B2: wipe() while another wipe is still running waits for it (BUSY on STORE_WIPE is retried)', async () => {
  const { band, sim } = await connectSim();
  await band.command('STORE_WIPE');                                     // somebody else started a wipe
  const wiping = band.storage.wipe();                                   // ours waits, then wipes again
  await whileStepping(sim, wiping, 100);
  assert.ok(sim._t >= 2000, `two erases, one after the other (${sim._t} ms)`);
  assert.equal((await band.storage.info()).usedKeys, 0);
});

test('B2: wipe({ timeoutSeconds }) gives up with a timeout that says the storage is still erasing', async () => {
  const transport = new Scripted();
  const band = await Band.connect(42, { transport });
  band.storage._busyPauseMs = 5;
  transport.reply = (cmd) => (cmd.opcode === 'STORE_WIPE' ? {} : 'BUSY');
  const started = Date.now();
  await fails(band.storage.wipe({ timeoutSeconds: 0.05 }), 'timeout', /storage is still erasing/);
  const took = Date.now() - started;
  assert.ok(took >= 30 && took < 500, `about 50 ms (${took})`);
  assert.equal(transport.sent.filter((name) => name === 'STORE_WIPE').length, 1);
  await fails(band.storage.wipe({ timeoutSeconds: 0 }), 'bad-arg');
  await fails(band.storage.wipe({ timeoutSeconds: '30' }), 'bad-arg');
});

test('B2: NOT_AVAILABLE after the wipe means the erase failed: call wipe() again', async () => {
  const transport = new Scripted();
  const band = await Band.connect(42, { transport });
  band.storage._busyPauseMs = 2;
  let polls = 0;
  transport.reply = (cmd) => (cmd.opcode === 'STORE_WIPE' ? {} : polls++ < 2 ? 'BUSY' : 'NOT_AVAILABLE');
  await fails(band.storage.wipe(), 'not-available', /could not erase its storage.*wipe\(\) again/);
});

test('B2: the link dropping while wipe() waits gives the usual not-connected error', async () => {
  const { band, sim } = await connectSim();
  const wiping = band.storage.wipe();
  for (let i = 0; i < 10; i++) await tick();
  sim.dropLink();
  await fails(wiping, 'not-connected', /is not connected/);
});

test('B2 + B3: with the pretend band running by itself, wipe() then put() works and takes about wipeMs', async () => {
  const sim = new SimTransport(42, { wipeMs: 150 });
  const band = await Band.connect(42, { transport: sim });
  band.storage._busyPauseMs = 20;
  await band.storage.put(1, 'old');
  const started = Date.now();
  await band.storage.wipe();
  const took = Date.now() - started;
  await band.storage.put(2, 'new');
  assert.ok(took >= 100 && took < 1500, `took ${took} ms`);
  assert.equal(await band.storage.get(1), null);
  for (let key = 10; key < 22; key++) await band.storage.put(key, 'more than ten in a row');   // B1 in real time
  assert.equal((await band.storage.info()).usedKeys, 13);
  await band.disconnect();
});
