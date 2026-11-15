// A band with firmware 1.1.3 ends the USB session of a computer that took no data for 5
// seconds, without a notice: streams off, settings at their defaults, commands still
// answered. These tests check that the library notices it (transports/session-watch.js),
// says so with reason 'session-ended', and that auto-reconnect sets everything again.
// Everything runs against the pretend band and a fake port: not against a real band.
import test from 'node:test';
import assert from 'node:assert/strict';
import { Band, SimTransport, Transport, WebBluetoothTransport, WebSerialTransport, codec } from '../src/index.js';
import { SESSION_ENDED_TEXT, WATCH_TIMES, fastestFrameMs } from '../src/transports/session-watch.js';
import { fakeSerialWithBand } from './fake-serial-port.js';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const once = (band, type) => new Promise((resolve) => {
  const stop = band.on(type, (e) => { stop(); resolve(e); });
});
const FAST = { tick: 20, silence: 80, stall: 3000, settle: 20, quiet: 400 };     // the watch, 25 times faster

/** A pretend band on the cable that the library watches from the start. */
async function connectUsb(bandOptions = {}, simOptions = {}, times = FAST) {
  const sim = new SimTransport(42, { via: 'usb', autoTap: false, ...simOptions });
  sim.sessionWatch = true;
  const band = await Band.connect(42, { transport: sim, ...bandOptions });
  band._reconnectPauseMs = 5;
  Object.assign(band._watch.times, times);
  const events = [];
  for (const type of ['reconnecting', 'reconnected', 'disconnect']) band.on(type, (e) => events.push([type, e]));
  return { sim, band, events };
}

/** The commands the pretend band receives from now on, by contract name. */
function watchCommands(sim) {
  const seen = [];
  const command = sim._command.bind(sim);
  sim._command = (frame) => { seen.push(codec.decodeCommand(frame)?.opcode); command(frame); };
  return seen;
}

function useGlobal(t, name, value) {
  const before = Object.getOwnPropertyDescriptor(globalThis, name);
  Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
  t.after(() => (before ? Object.defineProperty(globalThis, name, before) : delete globalThis[name]));
}

// ---------------------------------------------------------------- the pretend band

test('pretend band: endUsbSession() stops the streams, resets the settings, says nothing, and still answers', async () => {
  const sim = new SimTransport(42, { realtime: false, via: 'usb' });
  const band = await Band.connect(42, { transport: sim });
  assert.equal(sim.sessionWatch, false, 'not watched until the call is used');
  const heard = [];
  for (const type of ['motion', 'notice', 'disconnect']) band.on(type, () => heard.push(type));
  await band.setLed('#ff0000');
  await band.setMotion({ rate: 100 });
  await band.configure({ motion: true, battery: true });
  sim.step(300);
  assert.ok(heard.includes('motion'));
  heard.length = 0;
  const wire = [];
  sim.onWire = (direction, bytes) => { if (direction === 'to-computer') wire.push(...bytes); };
  sim.endUsbSession();
  sim.step(2000);
  assert.deepEqual(heard, [], 'no stream frame, no notice, no close');
  assert.deepEqual(wire, [], 'nothing on the cable at all');
  assert.equal(sim.sessionWatch, true);
  assert.equal(band.connected, true);
  // Commands are answered again at once, and the first frame has one extra 00 in front of it.
  assert.deepEqual(await band.command('IMU_GET'), { rate_hz: 25, accel_range_g: 2, gyro_range_dps: 2000 });
  assert.equal(wire[0], 0, 'an empty packet comes first');
  assert.notEqual(wire[1], 0);
  assert.equal((await band.command('LED_GET')).brightness, 0);
  band._watch.stop();
  await band.disconnect();
});

test('pretend band: stopReading() follows the 5 second rule on the band\'s own clock', async () => {
  const start = async () => {
    const sim = new SimTransport(42, { realtime: false, via: 'usb', autoTap: false });
    const band = await Band.connect(42, { transport: sim });
    const heard = [];
    for (const type of ['activity', 'notice']) band.on(type, (e) => heard.push(type === 'notice' ? e.kind : type));
    return { sim, band, heard };
  };
  // 7 seconds with a stream on: the session ends 5.2 seconds after the first frame nobody took.
  let { sim, band, heard } = await start();
  await band.configure({ activity: true });
  sim.stopReading(7);
  sim.step(1000 + 5100);
  assert.equal(sim._s.streams, 1 << codec.STREAM_BITS.activity, 'not yet');
  sim.step(200);
  assert.equal(sim._s.streams, 0, 'the band ended the session');
  sim.step(3000);
  assert.deepEqual(heard, [], 'and said nothing');
  band._watch.stop();
  await band.disconnect();
  // 1 second: a pause. Frames are lost, the band says stream-dropped, the session goes on.
  ({ sim, band, heard } = await start());
  await band.configure({ activity: 0.1 });
  let hints = 0;
  const hint = sim.onSessionHint;
  sim.onSessionHint = () => { hints++; hint(); };
  sim.stopReading(1);
  sim.step(1150);
  assert.equal(sim._s.streams, 1 << codec.STREAM_BITS.activity);
  assert.equal(hints, 1, 'one empty packet');
  const readings = heard.filter((h) => h === 'activity').length;
  assert.ok(readings >= 3 && readings <= 4, `${readings} readings: the 2 that waited in the first 200 ms and what came after, not all 11`);
  assert.deepEqual(heard.filter((h) => h !== 'activity'), ['stream-dropped']);
  band._watch.stop();
  await band.disconnect();
  // No stream on: nothing waits for the computer, so the session is never ended.
  ({ sim, band, heard } = await start());
  await band.setLed('#00ff00');
  sim.stopReading(60);
  sim.step(61000);
  assert.equal((await band.command('LED_GET')).g, 255);
  await band.disconnect();
});

// ---------------------------------------------------------------- noticing it

test('a session end is noticed within 3 seconds, with the library\'s real times, and says why', async () => {
  const { sim, band, events } = await connectUsb({}, {}, WATCH_TIMES);
  assert.deepEqual({ ...band._watch.times }, { tick: 1000, silence: 2000, stall: 3000, settle: 500, quiet: 30000 });
  await band.configure({ micLevel: true });
  await sleep(300);
  const seen = watchCommands(sim);
  const at = Date.now();
  sim.endUsbSession();
  const gone = await once(band, 'disconnect');
  const took = Date.now() - at;
  assert.ok(took >= 1900 && took <= 3300, `noticed after ${took} ms`);
  assert.equal(gone.reason, 'session-ended');
  assert.ok(gone.message.startsWith(SESSION_ENDED_TEXT));
  assert.match(gone.message, /took no data for 5 seconds.*Streams were stopped\. Connect again/);
  assert.deepEqual(seen, ['STREAM_GET'], 'one question, nothing else');
  assert.equal(band.connected, false);
  assert.equal(band.reconnecting, false);
  assert.equal(band.streams.micLevel, false);
  assert.equal(sim._open, false, 'the port was closed properly');
  assert.equal(band._watch._timer, null);
  assert.deepEqual(events, [['disconnect', gone]]);
  await assert.rejects(band.getLed(), (e) => e.code === 'not-connected');
});

test('auto-reconnect: the link comes back by itself, settings and streams are set again, data flows', async () => {
  const { sim, band, events } = await connectUsb({ autoReconnect: true }, { firmware: '1.1' });
  await band.setLed('#00ff00', { brightness: 0.5 });
  await band.setMotion({ accelRange: 8 });
  await band.configure({ motion: 100, micLevel: 0.04, events: true });
  let motion = 0;
  band.on('motion', () => motion++);
  await sleep(150);
  assert.ok(motion > 0);
  sim.endUsbSession();
  const why = await once(band, 'reconnecting');
  assert.equal(why.reason, 'session-ended');
  assert.match(why.message, /took no data for 5 seconds.*connecting again/);
  assert.equal(band.reconnecting, true);
  const back = await once(band, 'reconnected');
  assert.deepEqual(back.failed, []);
  assert.equal(back.attempts, 1);
  assert.deepEqual(events.map(([type]) => type), ['reconnecting', 'reconnected']);
  assert.equal(band.connected, true);
  assert.deepEqual(band.streams, { motion: 100, events: true, activity: false, audio: false, battery: false, temperature: false, link: false, micLevel: true });
  assert.equal(sim._s.imu.accel_range_g, 8);
  assert.equal(sim._s.intervals[codec.STREAM_BITS.mic_level], 40);
  assert.equal(sim._s.led.g, 255);
  motion = 0;
  await sleep(200);
  assert.ok(motion > 0, 'motion readings arrive again');
  assert.equal(events.length, 2, 'and the link stays');
  assert.ok(band._watch._timer !== null, 'the new session is watched too');
  await band.disconnect();
});

test('the program asks STREAM_GET itself: the answer is enough to notice the end', async () => {
  const { sim, band } = await connectUsb({}, { realtime: false }, WATCH_TIMES);
  await band.configure({ activity: true });
  sim.endUsbSession();
  const gone = once(band, 'disconnect');
  assert.deepEqual(await band.command('STREAM_GET'), { active: 0 });
  assert.equal((await gone).reason, 'session-ended');
});

// ---------------------------------------------------------------- no false alarms

test('streams that are silent by nature (taps, battery on change) are not taken for a session end', async () => {
  const { sim, band, events } = await connectUsb();
  await band.configure({ events: true, battery: true });
  assert.equal(fastestFrameMs(band, band._memory.mask), null);
  const seen = watchCommands(sim);
  await sleep(300);                                  // far longer than the silence time (80 ms)
  assert.deepEqual(seen, [], 'nothing was asked');
  await sleep(250);                                  // after the long quiet time (400 ms) the band is asked once ...
  assert.deepEqual(seen, ['STREAM_GET']);
  assert.deepEqual(events, [], '... and all is well');
  assert.equal(band.connected, true);
  sim.tap('tap');                                    // and a real session end is still found by that slow check
  sim.endUsbSession();
  assert.equal((await once(band, 'disconnect')).reason, 'session-ended');
});

test('a stream that is on but really silent is asked about less and less often, and never ends the link', async () => {
  const { sim, band, events } = await connectUsb();
  await band.configure({ activity: 0.1 });
  sim._due.delete('activity');                       // the band stays in its session but sends nothing
  const seen = watchCommands(sim);
  await sleep(1500);
  assert.ok(seen.length >= 2 && seen.length <= 5, `asked ${seen.length} times in 1.5 s (waits of 0.3, 0.4, 0.4 ... s, not every 0.3 s or less)`);
  assert.ok(seen.every((name) => name === 'STREAM_GET'));
  assert.deepEqual(events, []);
  await band.disconnect();
});

test('while data flows nothing is asked: no command in 1.5 seconds of streaming', async () => {
  const { sim, band, events } = await connectUsb({}, {}, { tick: 100, silence: 400, stall: 3000, settle: 100, quiet: 30000 });
  await band.configure({ audio: true, motion: 50 });
  const seen = watchCommands(sim);
  await sleep(1500);
  assert.deepEqual(seen, []);
  assert.equal(band._watch.asked, 0);
  assert.deepEqual(events, []);
  await band.disconnect();
});

test('the 200 ms pause (an empty packet without a session end) does not end the link', async () => {
  const { sim, band, events } = await connectUsb();
  await band.configure({ micLevel: 0.02 });
  const notices = [];
  band.on('notice', (n) => notices.push(n.kind));
  const seen = watchCommands(sim);
  let hints = 0;
  const hint = sim.onSessionHint;
  sim.onSessionHint = () => { hints++; hint(); };
  sim.usbPause();
  assert.equal(hints, 1);
  await sleep(300);
  assert.deepEqual(notices, ['stream-dropped']);
  assert.deepEqual(seen, [], 'frames came after the empty packet: nothing was asked');
  // With only naturally silent streams on, the empty packet is worth one question. The answer is "still on".
  await band.configure({ micLevel: false, events: true });
  seen.length = 0;
  sim.usbPause();
  await sleep(150);
  assert.deepEqual(seen, ['STREAM_GET']);
  assert.deepEqual(events, []);
  assert.equal(band.connected, true);
  await band.disconnect();
});

test('a program that was stuck itself asks once it runs again, and not while frames still come', async () => {
  const { sim, band, events } = await connectUsb();
  await band.configure({ events: true });
  const seen = watchCommands(sim);
  band._watch._tickAt -= 5000;                       // as if the timer came 5 seconds late
  await sleep(150);
  assert.deepEqual(seen, ['STREAM_GET']);
  assert.deepEqual(events, []);
  await band.configure({ micLevel: 0.02 });
  seen.length = 0;
  band._watch._tickAt -= 5000;
  await sleep(150);
  assert.deepEqual(seen, [], 'the stream still sends: no question');
  band._watch._tickAt -= 5000;                       // stuck long enough for the band to give up
  sim.endUsbSession();
  assert.equal((await once(band, 'disconnect')).reason, 'session-ended');
});

// ---------------------------------------------------------------- Bluetooth is left alone

test('a Bluetooth-kind link gets no timer and no extra command, whatever the streams do', async () => {
  class BleLike extends Transport {
    kind = 'ble';
    constructor(sim) { super(); this.sim = sim; sim.onFrame = (f) => this.onFrame(f); sim.onClose = (r) => this.onClose(r); }
    open() { return this.sim.open(); }
    close() { return this.sim.close(); }
    send(frame) { return this.sim.send(frame); }
  }
  const sim = new SimTransport(42, { autoTap: false });
  const band = await Band.connect(42, { transport: new BleLike(sim) });
  Object.assign(band._watch.times, FAST);
  const events = [];
  band.on('disconnect', (e) => events.push(e));
  await band.configure({ micLevel: 0.02, activity: 0.1 });
  const seen = watchCommands(sim);
  assert.equal(band._watch._timer, null, 'no timer');
  await sleep(200);
  sim.endUsbSession();                               // the streams go silent (over Bluetooth this never happens this way)
  band._watch.hint();                                // even a sign from somewhere changes nothing
  await sleep(600);
  assert.equal(band._watch._timer, null);
  assert.equal(band._watch.asked, 0);
  assert.deepEqual(seen, [], 'not one command was sent');
  assert.deepEqual(events, []);
  assert.deepEqual(await band.command('STREAM_GET'), { active: 0 });     // the program's own question is only answered
  await sleep(50);
  assert.equal(band.connected, true);
  await band.disconnect();
  // The real transports: only the cable is watched.
  assert.equal(new WebSerialTransport().sessionWatch, true);
  assert.equal(new WebBluetoothTransport(42).sessionWatch, undefined);
  assert.equal('onSessionHint' in new WebBluetoothTransport(42), false);
});

test('the pretend band behaves as before when the new calls are not used: no timer, nothing asked', async () => {
  const sim = new SimTransport(42, { autoTap: false });
  const band = await Band.connect(42, { transport: sim });
  Object.assign(band._watch.times, FAST);
  await band.configure({ events: true, activity: true });
  const seen = watchCommands(sim);
  await sleep(600);
  assert.equal(band._watch._timer, null);
  assert.deepEqual(seen, []);
  await band.disconnect();
});

test('no stream on: nothing is watched, also on the cable; sleep() is not taken for a session end', async () => {
  const { sim, band, events } = await connectUsb();
  await band.setLed('#0000ff');
  assert.equal(band._watch._timer, null);
  await band.configure({ activity: 0.1 });
  assert.ok(band._watch._timer !== null);
  await band.configure({ activity: false });
  assert.equal(band._watch._timer, null);
  await band.configure({ activity: 0.1 });
  const seen = watchCommands(sim);
  await band.sleep();                                // over USB the streams stop and the link stays
  await sleep(600);
  assert.deepEqual(seen, ['SLEEP']);
  assert.deepEqual(events, []);
  assert.equal(band._watch._timer, null);
  await band.disconnect();
});

// ---------------------------------------------------------------- web-serial.js against a fake port

test('Web Serial: the port is opened with the 8 KiB read buffer', async (t) => {
  const fake = fakeSerialWithBand(42, { realtime: false });
  useGlobal(t, 'navigator', fake.navigator);
  const band = await Band.connect(42, { transport: 'serial' });
  assert.equal(fake.log[1], 'open {"baudRate":115200,"bufferSize":8192}');
  assert.equal(band.transport.sessionWatch, true);
  await band.disconnect();
});

test('Web Serial: a session end is noticed, the port is closed and opened again with no chooser, data flows again', async (t) => {
  const fake = fakeSerialWithBand(42, { autoTap: false });
  useGlobal(t, 'navigator', fake.navigator);
  const band = await Band.connect(42, { transport: 'serial', autoReconnect: true });
  band._reconnectPauseMs = 5;
  Object.assign(band._watch.times, FAST);
  await band.setLed('#ff8800');
  await band.configure({ motion: 100, activity: 0.1 });
  let readings = 0;
  band.on('motion', () => readings++);
  await sleep(150);
  assert.ok(readings > 0);
  fake.log.length = 0;
  const events = [];
  for (const type of ['reconnecting', 'reconnected', 'disconnect']) band.on(type, (e) => events.push([type, e.reason]));
  fake.sim.endUsbSession();
  const back = await once(band, 'reconnected');
  assert.deepEqual(back.failed, []);
  assert.deepEqual(events, [['reconnecting', 'session-ended'], ['reconnected', undefined]]);
  assert.deepEqual(fake.log, [
    'reader.cancel', 'setSignals {"dataTerminalReady":false}', 'close',
    'getPorts', 'open {"baudRate":115200,"bufferSize":8192}', 'setSignals {"dataTerminalReady":true}',
  ]);
  assert.equal(fake.requests, 1, 'the chooser was shown once, at the start');
  assert.equal(fake.sim._s.led.r, 255);
  assert.equal(fake.sim._s.motion2.rate_hz, 100);
  readings = 0;
  await sleep(150);
  assert.ok(readings > 0, 'motion readings arrive again');
  await band.disconnect();
});

test('Web Serial: without auto-reconnect the program gets disconnect with reason session-ended', async (t) => {
  const fake = fakeSerialWithBand(42, { autoTap: false });
  useGlobal(t, 'navigator', fake.navigator);
  const band = await Band.connect(42, { transport: 'serial' });
  Object.assign(band._watch.times, FAST);
  await band.configure({ audio: true });
  await sleep(100);
  fake.sim.endUsbSession();
  const gone = await once(band, 'disconnect');
  assert.equal(gone.reason, 'session-ended');
  assert.match(gone.message, /Connect again/);
  await sleep(10);
  assert.equal(fake.log.at(-1), 'close');
  assert.equal(band.connected, false);
});

test('Web Serial: an empty packet in the middle of a session is reported, the one at the start is not', async (t) => {
  const fake = fakeSerialWithBand(42, { realtime: false });
  useGlobal(t, 'navigator', fake.navigator);
  const transport = new WebSerialTransport();
  let hints = 0;
  const band = await Band.connect(42, { transport });
  transport.onSessionHint = () => hints++;
  await band.getLed();
  assert.equal(hints, 0);
  fake.sim.onWire('to-computer', Uint8Array.of(0));          // one 00 right after the 00 that ended the last frame
  await sleep(5);
  assert.equal(hints, 1);
  await band.getLed();                                       // frames after it are read as before
  // A new session on the same transport: its first frame may have a 00 in front of it. That is no sign.
  hints = 0;
  await transport.reopen();
  fake.sim.onWire('to-computer', Uint8Array.of(0, 0));
  await band.getLed();
  assert.equal(hints, 0);
  await band.disconnect();
});

test('Web Serial: closing or reloading the tab (pagehide) closes the port; the listener goes away on close', async (t) => {
  const fake = fakeSerialWithBand(42, { realtime: false });
  useGlobal(t, 'navigator', fake.navigator);
  const listeners = new Map();
  useGlobal(t, 'window', {
    isSecureContext: true,
    addEventListener: (type, fn) => listeners.set(type, fn),
    removeEventListener: (type, fn) => { if (listeners.get(type) === fn) listeners.delete(type); },
  });
  const band = await Band.connect(42, { transport: 'serial', autoReconnect: true });
  const events = [];
  for (const type of ['reconnecting', 'disconnect']) band.on(type, (e) => events.push([type, e.reason]));
  assert.deepEqual([...listeners.keys()], ['pagehide'], 'pagehide only: a beforeunload can still be cancelled');
  fake.log.length = 0;
  listeners.get('pagehide')();                               // the tab is closed
  assert.deepEqual(fake.log.slice(0, 1), ['reader.cancel'], 'the close starts before the handler returns');
  await sleep(10);
  assert.deepEqual(fake.log.filter((line) => line !== 'reader.cancel'), [
    'setSignals {"dataTerminalReady":false}', 'setSignals {"dataTerminalReady":false}', 'close',
  ]);
  assert.equal(fake.sim._open, false, 'the band saw a close');
  assert.equal(listeners.size, 0, 'the listener was removed');
  assert.deepEqual(events, [['disconnect', 'closed']], 'a close, not a lost link: nothing tries to reconnect');
  // Connect and disconnect the normal way: the listener is added and removed each time.
  const again = await Band.connect(42, { transport: 'serial' });
  assert.equal(listeners.size, 1);
  await again.disconnect();
  assert.equal(listeners.size, 0);
});

test('Web Serial: no window (Node) and a window without events: open and close still work', async (t) => {
  const fake = fakeSerialWithBand(42, { realtime: false });
  useGlobal(t, 'navigator', fake.navigator);
  assert.equal(typeof globalThis.window, 'undefined');
  const band = await Band.connect(42, { transport: 'serial' });
  await band.disconnect();
  useGlobal(t, 'window', { isSecureContext: true });
  const second = await Band.connect(42, { transport: 'serial' });
  await second.disconnect();
  assert.equal(fake.log.filter((line) => line === 'close').length, 2);
});

// ---------------------------------------------------------------- the pretend band's storage file

test('pretend band: a storage file nested too deep, or too large, is ignored like any bad file', async () => {
  const { mkdtempSync, writeFileSync, rmSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const folder = mkdtempSync(join(tmpdir(), 'vexo-store-'));
  try {
    const file = join(folder, 'store.json');
    for (const bad of ['['.repeat(100000) + ']'.repeat(100000), `{"1":"${'00'.repeat(600000)}"}`, '{"1":{"a":1}}']) {
      writeFileSync(file, bad);
      const sim = new SimTransport(42, { realtime: false, storeFile: file });
      const band = await Band.connect(42, { transport: sim });        // must not throw
      assert.equal((await band.storage.info()).usedKeys, 0, 'the band starts empty');
      await band.disconnect();
    }
  } finally {
    rmSync(folder, { recursive: true, force: true });
  }
});
