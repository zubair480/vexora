// Tests for the dashboard's page-free logic. Run: node --test dashboard/test/*.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { Band, SimTransport, VexoError, bandName, NAME_PREFIX, LED_PATTERNS } from '../../js/src/index.js';
import {
  MIC_OWNS_LED, parseBandParam, isColour, bluetoothProblem, connectProblem, commandProblem, disconnectMessage,
  reconnectingMessage, reconnectedMessage, streamTakenMessage, startupStreams, setupMessage, eventLabel, batteryDetail, signalWords, ledView, History, RateMeter,
} from '../logic.js';
import { temperatureText, TEMPERATURE_NOTE, loudnessBar, LOUDNESS_NOTE, BAR_EMPTY_DB, BAR_FULL_DB, PRETEND_MIC_NOTE } from '../logic.js';
import { StepCounter, stepsText, activityDetail, STEPS_NOTE, STILL_CONNECTING_MS, stillConnectingMessage, CANCELLED_MESSAGE } from '../logic.js';
import { notTheBand } from '../../js/src/transports/web-serial.js';

const sim = (number, options) => new SimTransport(number, { realtime: false, ...options });

test('parseBandParam accepts one to three digits and nothing else', () => {
  assert.deepEqual(parseBandParam('042'), { number: 42, name: bandName(42) });
  assert.deepEqual(parseBandParam(' 7 '), { number: 7, name: bandName(7) });
  for (const same of ['7', '07', '007']) assert.deepEqual(parseBandParam(same), { number: 7, name: 'Vexo-HACK-07' });
  assert.deepEqual(parseBandParam('120'), { number: 120, name: 'Vexo-HACK-120' });
  assert.equal(parseBandParam('999').number, 999);
  for (const bad of ['', 'abc', '1000', '-1', '4.2', '0x1', '42<b>', null, undefined, 42]) {
    assert.equal(parseBandParam(bad), null, `should refuse ${bad}`);
  }
});

test('isColour only accepts # and six hex digits', () => {
  assert.ok(isColour('#00a0ff'));
  assert.ok(isColour('#FFFFFF'));
  for (const bad of ['00a0ff', '#fff', '#00a0fg', 'red', null, '#00a0ff; x']) assert.ok(!isColour(bad));
});

test('bluetoothProblem picks the right explanation', () => {
  assert.equal(bluetoothProblem({ reason: null, secure: true, iOS: false }), null);
  assert.match(bluetoothProblem({ reason: 'x', secure: true, iOS: true }), /iPhones and iPads cannot connect/);
  assert.match(bluetoothProblem({ reason: 'x', secure: false, iOS: false }), /not opened in a secure way/);
  assert.match(bluetoothProblem({ reason: 'x', secure: true, iOS: false }), /Chrome or Edge/);
});

test('connectProblem: a real wrong-band error shows both numbers', async () => {
  const wanted = bandName(42);
  const error = await Band.connect(42, { transport: sim(17) }).then(() => null, (e) => e);
  assert.equal(error.code, 'wrong-band');
  const text = connectProblem(error, { kind: 'ble', wanted });
  assert.ok(text.includes(bandName(17)), text);
  assert.ok(text.includes(wanted), text);
  assert.ok(text.includes('band 17.'), text);
});

test('connectProblem: a band with no number yet', async () => {
  const error = await Band.connect(42, { transport: sim(0) }).then(() => null, (e) => e);
  const text = connectProblem(error, { kind: 'serial', wanted: bandName(42) });
  assert.match(text, /no number yet/);
  assert.ok(text.includes(bandName(42)));
});

test('connectProblem: the other connection errors', () => {
  const wanted = bandName(42);
  const of = (code, kind = 'ble') => connectProblem(new VexoError(code, 'library text'), { kind, wanted });
  // A band that is connected elsewhere is not in the list: that comes first, the double-tap second.
  assert.match(of('cancelled'), /^No band was picked\. Was Vexo-HACK-42 not in the list\? If something else is connected to it \(another dashboard tab, another program, a teammate's laptop\), close that first.*Then double-tap/);
  assert.match(of('cancelled', 'serial'), /No USB port was picked/);
  assert.match(of('cancelled', 'serial'), /On a Mac.*"Allow accessory to connect\?".*press Allow/);
  assert.match(of('not-found-nearby'), /^Could not reach Vexo-HACK-42\. If something else is connected to it .*one Bluetooth connection at a time.*Then double-tap the band/);
  assert.match(of('timeout'), /did not answer in time.*double-tap/);
  assert.match(of('not-connected'), /closed while connecting/);
  assert.equal(of('no-web-bluetooth'), 'library text');
  assert.equal(of('wrong-band'), 'library text');     // no names to pull out: fall back to the library's sentence
  assert.equal(connectProblem(new Error('boom'), { kind: 'ble', wanted }), 'Could not connect: boom');
});

test('connectProblem: a real timeout from a band that never answers', async () => {
  const silent = { kind: 'custom', async open() {}, async close() {}, async send() {} };
  const error = await Band.connect(42, { transport: silent, timeoutMs: 20 }).then(() => null, (e) => e);
  assert.equal(error.code, 'timeout');
  assert.match(connectProblem(error, { kind: 'ble', wanted: bandName(42) }), /did not answer in time/);
});

test('commandProblem: LED commands while audio or the loudness meter runs give the mic sentence', async () => {
  const band = await Band.connect(42, { transport: sim(42) });
  for (const mic of ['audio', 'micLevel']) {
    await band.configure({ [mic]: true });
    for (const action of [() => band.setLed('#ff0000'), () => band.ledOff(), () => band.identify()]) {
      const error = await action().then(() => null, (e) => e);
      assert.equal(error.code, 'denied', mic);
      assert.equal(commandProblem(error), MIC_OWNS_LED);
    }
    await band.configure({ [mic]: false });
  }
  await band.setLed('#ff0000');                        // works again, nothing else needed
  await band.disconnect();
  assert.equal(MIC_OWNS_LED, 'The microphone is on, so the band shows a solid blue light and ignores LED commands. '
    + 'Turn off Loudness meter and Stream audio to use the LED.');
});

test('commandProblem: timeout, not-connected, anything else', () => {
  assert.match(commandProblem(new VexoError('timeout', 'x')), /did not answer in time/);
  assert.match(commandProblem(new VexoError('not-connected', 'x')), /not connected/);
  assert.equal(commandProblem(new VexoError('bad-arg', 'library text')), 'library text');
});

test('disconnectMessage covers every reason', () => {
  const name = bandName(42);
  assert.deepEqual(disconnectMessage('closed', name), { kind: 'info', text: 'Disconnected.' });
  assert.match(disconnectMessage('restart', name).text, /restarting/);
  assert.match(disconnectMessage('sleep', name).text, /Double-tap it to wake it/);
  const lost = disconnectMessage('lost', name);
  assert.equal(lost.kind, 'error');
  assert.match(lost.text, /was lost\. Press Connect again\..*double-tap/);
  assert.equal(disconnectMessage('something-new', name).kind, 'error');
  const wrong = disconnectMessage('wrong-band', name);
  assert.equal(wrong.kind, 'error');
  assert.ok(wrong.text.startsWith(`Another band answered instead of ${name}.`));
});

test('reconnect status lines: the plain sentences while trying and when back', () => {
  assert.deepEqual(reconnectingMessage(120), { kind: 'error', text: 'Connection lost. Trying to reconnect for 2 minutes...' });
  assert.equal(reconnectingMessage(60).text, 'Connection lost. Trying to reconnect for 1 minute...');
  assert.equal(reconnectingMessage(45).text, 'Connection lost. Trying to reconnect for 45 seconds...');
  assert.equal(reconnectingMessage(90).text, 'Connection lost. Trying to reconnect for 90 seconds...');
  assert.deepEqual(reconnectedMessage([]), { kind: 'info', text: 'Reconnected.' });
  assert.deepEqual(reconnectedMessage(), { kind: 'info', text: 'Reconnected.' });
  const partly = reconnectedMessage(['LED_SET']);
  assert.equal(partly.kind, 'error');
  assert.match(partly.text, /^Reconnected, but not everything could be set again\./);
});

test('reconnect as the page sees it: lost -> "Trying to reconnect" -> "Reconnected." with the streams back', async () => {
  const transport = sim(42);
  const band = await Band.connect(42, { transport, autoReconnect: true });      // how app.js connects
  band._reconnectPauseMs = 5;
  const lines = [];
  band.on('reconnecting', (r) => lines.push(reconnectingMessage(r.seconds).text));
  band.on('reconnected', (r) => lines.push(reconnectedMessage(r.failed).text));
  band.on('disconnect', (d) => lines.push(disconnectMessage(d.reason, band.name).text));
  for (const [, streams] of startupStreams('sim')) await band.configure(streams);
  await band.setLed('#00a0ff');
  const streams = { ...band.streams };
  transport.reachable = false;            // what the "Lose the link" button does
  transport.dropLink();
  assert.deepEqual(lines, ['Connection lost. Trying to reconnect for 2 minutes...']);
  assert.equal(commandProblem(await band.setLed('#ff0000').then(() => null, (e) => e)), 'The band is not connected any more.');
  transport.reachable = true;
  await new Promise((resolve) => band.on('reconnected', resolve));
  assert.deepEqual(lines, ['Connection lost. Trying to reconnect for 2 minutes...', 'Reconnected.']);
  assert.deepEqual(band.streams, streams);
  assert.equal((await band.getLed()).b, 255);
  for (const [, again] of startupStreams('sim')) await band.configure(again);   // the page asks once more: harmless
  assert.deepEqual(band.streams, streams);
  await band.disconnect();
  assert.equal(lines.at(-1), 'Disconnected.');
});

test('reconnect gives up: the page gets the usual lost-link message', async () => {
  const transport = sim(42);
  const band = await Band.connect(42, { transport, autoReconnect: true, reconnectSeconds: 0.05 });
  band._reconnectPauseMs = 5;
  transport.reachable = false;
  transport.dropLink();
  const gone = await new Promise((resolve) => band.on('disconnect', resolve));
  assert.equal(gone.reconnect, 'gave-up');
  const message = disconnectMessage(gone.reason, band.name);
  assert.equal(message.kind, 'error');
  assert.match(message.text, /was lost\. Press Connect again\./);
});

test('over USB the page never asks for the signal stream, and the band accepts everything it does ask for', async () => {
  const transport = sim(42, { via: 'usb' });
  const band = await Band.connect(42, { transport, autoReconnect: true });
  const link = [];
  band.on('link', (l) => link.push(l));
  const commands = [];
  const command = transport._command.bind(transport);
  transport._command = (frame) => { commands.push(frame); command(frame); };
  for (const [label, streams] of startupStreams('serial')) {
    assert.notEqual(label, 'signal');
    assert.ok(!('link' in streams));
    await band.configure(streams);                                      // none is refused
  }
  assert.deepEqual(band.streams, {
    motion: 50, events: true, activity: true, audio: false, battery: true, temperature: true, link: false, micLevel: false,
  });
  transport.step(3000);
  assert.equal(link.length, 0);                                         // no signal reading over USB
  assert.ok(commands.length > 0);
  // What would happen if it did ask: the band refuses, and the library explains.
  const error = await band.configure({ link: true }).then(() => null, (e) => e);
  assert.equal(error.code, 'not-available');
  assert.match(commandProblem(error), /only exists over Bluetooth/);
  await band.disconnect();
});

test('the simulator really reports closed, lost, restart and sleep', async () => {
  const reasons = [];
  for (const end of ['disconnect', 'drop', 'restart', 'sleep']) {
    const transport = sim(42);
    const band = await Band.connect(42, { transport });
    band.on('disconnect', (d) => reasons.push(d.reason));
    if (end === 'disconnect') await band.disconnect();
    else if (end === 'drop') transport.dropLink();
    else {
      await band[end]();
      transport.step(500);
    }
  }
  assert.deepEqual(reasons, ['closed', 'lost', 'restart', 'sleep']);
});

test('startupStreams: the microphone (audio and the loudness meter) stays off, no signal stream over USB, and the band accepts each one', async () => {
  const keys = (kind) => startupStreams(kind).flatMap(([, streams]) => Object.keys(streams));
  assert.deepEqual(keys('ble'), ['motion', 'events', 'battery', 'temperature', 'activity', 'link']);
  assert.deepEqual(keys('sim'), ['motion', 'events', 'battery', 'temperature', 'activity', 'link']);
  assert.deepEqual(keys('serial'), ['motion', 'events', 'battery', 'temperature', 'activity']);
  const band = await Band.connect(42, { transport: sim(42) });
  for (const [, streams] of startupStreams('ble')) await band.configure(streams);
  assert.deepEqual(band.streams, {
    motion: 50, events: true, activity: true, audio: false, battery: true, temperature: true, link: true, micLevel: false,
  });
  await band.setLed('#00ff00');                        // so the LED works right after connecting
  await band.disconnect();
});

test('setupMessage says which streams did not start', () => {
  const name = bandName(42);
  assert.deepEqual(setupMessage({ name, kind: 'ble', flashProblem: null, failed: [] }),
    { kind: 'info', text: `Connected to ${name}. It should be flashing white.` });
  const bad = setupMessage({ name, kind: 'ble', flashProblem: MIC_OWNS_LED, failed: ['motion', 'battery'] });
  assert.equal(bad.kind, 'error');
  assert.ok(bad.text.includes(MIC_OWNS_LED));
  assert.match(bad.text, /These did not start: motion, battery\./);
  assert.match(setupMessage({ name, kind: 'sim', flashProblem: null, failed: [] }).text, /pretend band/);
});

test('eventLabel, batteryDetail, signalWords', () => {
  assert.equal(eventLabel({ kind: 'double-tap', arg: 0 }), 'double-tap');
  assert.equal(eventLabel({ kind: 'orientation', arg: 4, face: '+z' }), 'orientation: +z side up');
  assert.equal(eventLabel({ kind: 'orientation', arg: 9, face: null }), 'orientation');
  assert.equal(batteryDetail({ millivolts: 4083, state: 'battery' }), '4.08 V, on battery');
  assert.equal(batteryDetail({ millivolts: 4200, state: 'charged' }), '4.20 V, fully charged');
  assert.equal(signalWords(-55), 'strong');
  assert.equal(signalWords(-70), 'good');
  assert.equal(signalWords(-80), 'weak');
  assert.equal(signalWords(-95), 'very weak');
  assert.equal(signalWords(null), 'no reading');
});

test('ledView follows the light the simulator reports: user colour, identify flash, blue mic light', async () => {
  const transport = sim(42);
  const seen = [];
  transport.onLed = (state) => seen.push(ledView(state));
  const band = await Band.connect(42, { transport });
  await band.identify(1);
  assert.deepEqual(seen.at(-1), { colour: 'rgb(255, 255, 255)', pattern: 'blink', periodMs: 250, source: 'identify', text: 'flashing white so you can find it' });
  transport.step(1100);
  assert.equal(seen.at(-1).text, 'off');
  await band.setLed('#ff8800', { brightness: 0.5, pattern: 'breathe', period: 2000 });
  assert.deepEqual(seen.at(-1), { colour: 'rgb(128, 68, 0)', pattern: 'breathe', periodMs: 2000, source: 'user', text: 'breathe' });
  await band.configure({ audio: true });
  assert.deepEqual(seen.at(-1), { colour: 'rgb(0, 0, 255)', pattern: 'solid', periodMs: 1000, source: 'mic', text: 'solid blue: the microphone is on' });
  await band.configure({ audio: false });
  assert.equal(seen.at(-1).colour, 'rgb(128, 68, 0)');   // the band puts the stored LED back by itself
  await band.setLed('#123456', { pattern: 'rainbow' });
  assert.equal(seen.at(-1).pattern, 'rainbow');
  await band.ledOff();
  assert.equal(seen.at(-1).pattern, 'off');
  await band.disconnect();
  assert.ok(LED_PATTERNS.includes('rainbow') && NAME_PREFIX.length > 0);
});

test('History keeps only the newest samples, oldest first', () => {
  const h = new History(3, ['a', 'b']);
  assert.equal(h.length, 0);
  for (let i = 1; i <= 5; i++) h.push({ a: i, b: -i });
  assert.equal(h.length, 3);
  assert.deepEqual([0, 1, 2].map((i) => h.get('a', i)), [3, 4, 5]);
  assert.equal(h.get('b', 2), -5);
  h.clear();
  assert.equal(h.length, 0);
  h.push({ a: 9, b: 9 });
  assert.equal(h.get('a', 0), 9);
});

test('RateMeter turns counts into per-second numbers', () => {
  const m = new RateMeter(1000);
  for (let i = 0; i < 50; i++) m.add(53);
  assert.equal(m.take(1100), null);                    // too soon to say
  assert.deepEqual(m.take(2000), { framesPerSecond: 50, bytesPerSecond: 2650 });
  assert.deepEqual(m.take(3000), { framesPerSecond: 0, bytesPerSecond: 0 });
});

// ---- the Motion panel
import {
  DEFAULT_MOTION, pretendOptions, linkOf, sensorChoices, rateChoices, fallbackRate, motionHint, motionProblem,
  motionDroppedMessage, historySize, LossCounter, ArrivalRate, envelope,
} from '../logic.js';

const BLE_LIMITS = { rates: [10, 25, 50, 100, 208, 416, 833, 1666], accel: 833, gyro: 833, both: 416, transport: 'ble' };
const USB_LIMITS = { rates: [10, 25, 50, 100, 208, 416, 833, 1666], accel: 1666, gyro: 833, both: 833, transport: 'serial' };
const OLD_LIMITS = { rates: [10, 25, 50, 100], accel: null, gyro: null, both: 100, transport: 'ble' };
const labels = (choices) => choices.map((c) => (c.enabled ? c.label : `(${c.label})`));

test('the dashboard still starts motion at 50 per second with both sensors', () => {
  assert.deepEqual({ ...DEFAULT_MOTION }, { rate: 50, sensors: 'both' });
  assert.deepEqual(startupStreams('ble')[0], ['motion', { motion: DEFAULT_MOTION.rate }]);
});

test('pretendOptions: ?firmware=1.0 and ?link=usb, and nothing else, change the pretend band', () => {
  const read = (query) => pretendOptions(new URLSearchParams(query));
  assert.deepEqual(read('band=42'), {});
  assert.deepEqual(read('band=42&firmware=1.0'), { firmware: '1.0' });
  assert.deepEqual(read('firmware=1.1&link=ble'), {});
  assert.deepEqual(read('firmware=2&link=wifi'), {});
  assert.deepEqual(read('firmware=1.0&link=usb'), { firmware: '1.0', via: 'usb' });
  assert.equal(new SimTransport(42, { realtime: false, ...read('firmware=1.0') }).firmware, '1.0');
  assert.equal(new SimTransport(42, { realtime: false, ...read('link=usb') }).via, 'usb');
  assert.equal(new SimTransport(42, { realtime: false, ...read('') }).firmware, '1.1');
});

test('linkOf: the link for the words, also for a pretend band', () => {
  assert.equal(linkOf('ble'), 'ble');
  assert.equal(linkOf('serial'), 'usb');
  assert.equal(linkOf('sim', 'ble'), 'ble');
  assert.equal(linkOf('sim', 'usb'), 'usb');
  assert.equal(linkOf('sim', undefined), 'ble');
});

test('rateChoices over Bluetooth: every rate is listed, the ones that are too fast are off and say why', () => {
  assert.deepEqual(labels(rateChoices(BLE_LIMITS, 'both', 'ble')),
    ['10', '25', '50', '100', '208', '416', '(833: only with one sensor on Bluetooth)', '(1666: USB cable, acceleration only)']);
  assert.deepEqual(labels(rateChoices(BLE_LIMITS, 'accel', 'ble')),
    ['10', '25', '50', '100', '208', '416', '833', '(1666: USB cable, acceleration only)']);
  assert.deepEqual(labels(rateChoices(BLE_LIMITS, 'gyro', 'ble')).slice(-2), ['833', '(1666: USB cable, acceleration only)']);
  const off = rateChoices(BLE_LIMITS, 'both', 'ble')[6];
  assert.deepEqual(off, { rate: 833, enabled: false, reason: 'only with one sensor on Bluetooth', label: '833: only with one sensor on Bluetooth' });
});

test('rateChoices over the USB cable', () => {
  assert.deepEqual(labels(rateChoices(USB_LIMITS, 'both', 'usb')).slice(-2), ['833', '(1666: acceleration only on the USB cable)']);
  assert.deepEqual(labels(rateChoices(USB_LIMITS, 'gyro', 'usb')).slice(-2), ['833', '(1666: acceleration only on the USB cable)']);
  assert.ok(rateChoices(USB_LIMITS, 'accel', 'usb').every((c) => c.enabled));
});

test('rateChoices follows the limits the band gives, not a fixed table', () => {
  const slow = { rates: [10, 25, 50, 100, 208, 416, 833, 1666], accel: 416, gyro: 416, both: 208, transport: 'ble' };
  assert.deepEqual(labels(rateChoices(slow, 'accel', 'ble')).slice(-3), ['416', '(833: USB cable only)', '(1666: USB cable, acceleration only)']);
  assert.deepEqual(labels(rateChoices(slow, 'both', 'ble'))[5], '(416: only with one sensor on Bluetooth)');
  assert.deepEqual(labels(rateChoices({ ...USB_LIMITS, accel: 833 }, 'both', 'usb')).at(-1), '(1666: too fast for the USB cable)');
});

test('firmware 1.0: four rates, all on, and only Both can be picked', () => {
  assert.deepEqual(labels(rateChoices(OLD_LIMITS, 'both', 'ble')), ['10', '25', '50', '100']);
  assert.deepEqual(sensorChoices(OLD_LIMITS), [
    { value: 'both', label: 'Both', enabled: true },
    { value: 'accel', label: 'Acceleration only', enabled: false },
    { value: 'gyro', label: 'Turning (gyro) only', enabled: false },
  ]);
  assert.ok(sensorChoices(BLE_LIMITS).every((c) => c.enabled));
  assert.equal(motionHint(OLD_LIMITS, 'ble'),
    'This band has firmware 1.0: 10, 25, 50, 100 samples per second with both sensors. Faster rates and one sensor alone need band firmware 1.1.');
  assert.equal(motionHint(OLD_LIMITS, 'ble', true), motionHint(OLD_LIMITS, 'ble'));
});

test('motionHint on firmware 1.1 names the link and its limits, and says honestly what it was tested with', () => {
  assert.equal(motionHint(BLE_LIMITS, 'ble', true),
    'Over Bluetooth the most is 416 samples per second with both sensors, 833 with one sensor. This pretend band has firmware 1.1.');
  assert.equal(motionHint(USB_LIMITS, 'usb', false),
    'Over the USB cable the most is 833 samples per second with both sensors, 1666 with acceleration only, 833 with turning only. '
    + 'Fast rates come with band firmware 1.1. They have been tested on a real band: 208, 416 and 833 samples per second, and 1666 with acceleration only over the USB cable.');
});

test('fallbackRate keeps the rate when the new sensors allow it, else takes the fastest allowed and says so', () => {
  assert.deepEqual(fallbackRate(BLE_LIMITS, 'both', 416, 'ble'), { rate: 416, text: '' });
  assert.deepEqual(fallbackRate(BLE_LIMITS, 'gyro', 833, 'ble'), { rate: 833, text: '' });
  assert.deepEqual(fallbackRate(BLE_LIMITS, 'both', 833, 'ble'),
    { rate: 416, text: '833 samples per second is too fast with both sensors on Bluetooth. Now 416, the fastest allowed.' });
  assert.deepEqual(fallbackRate(USB_LIMITS, 'gyro', 1666, 'usb'),
    { rate: 833, text: '1666 samples per second is too fast with turning only on the USB cable. Now 833, the fastest allowed.' });
  assert.deepEqual(fallbackRate(OLD_LIMITS, 'both', 50, 'ble'), { rate: 50, text: '' });
  assert.equal(fallbackRate(OLD_LIMITS, 'both', 416, 'ble').rate, 100);
});

test('the pickers and the library agree: every enabled choice is accepted by the pretend band, every disabled one refused', async () => {
  for (const options of [{ via: 'ble' }, { via: 'usb' }, { firmware: '1.0' }]) {
    const transport = new SimTransport(42, { realtime: false, ...options });
    const band = await Band.connect(42, { transport });
    const limits = await band.motionLimits();
    const link = linkOf('sim', transport.via);
    for (const { value: sensors, enabled: sensorsOn } of sensorChoices(limits)) {
      for (const choice of rateChoices(limits, sensors, link)) {
        const asked = band.configure({ motion: choice.rate, sensors });
        if (sensorsOn && choice.enabled) {
          await asked;
          assert.equal(band.streams.motion, choice.rate);
        } else {
          await assert.rejects(asked, (e) => ['denied', 'not-available'].includes(e.code), `${JSON.stringify(options)} ${sensors} ${choice.rate}`);
        }
      }
    }
    await band.disconnect();
  }
});

test('motionProblem shows the library\'s own words for "too fast" and "needs firmware 1.1"', async () => {
  const band = await Band.connect(42, { transport: sim(42) });
  const tooFast = await band.configure({ motion: 833, sensors: 'both' }).catch((e) => e);
  assert.equal(tooFast.code, 'denied');
  assert.equal(motionProblem(tooFast), tooFast.message);
  assert.match(motionProblem(tooFast), /^833 samples per second with both sensors is too much for Bluetooth here/);
  await band.disconnect();
  const old = await Band.connect(42, { transport: new SimTransport(42, { realtime: false, firmware: '1.0' }) });
  const needs = await old.configure({ motion: 416 }).catch((e) => e);
  assert.equal(needs.code, 'not-available');
  assert.equal(motionProblem(needs), needs.message);
  assert.match(motionProblem(needs), /needs firmware 1\.1/);
  await old.disconnect();
  assert.equal(motionProblem(new VexoError('timeout', 'x')), commandProblem(new VexoError('timeout', 'x')));
});

test('motionDroppedMessage: only for the two motion streams', () => {
  assert.match(motionDroppedMessage(8), /drop motion samples.*lower rate or one sensor/);
  assert.equal(motionDroppedMessage(0), motionDroppedMessage(8));
  assert.equal(motionDroppedMessage(2), null);
  assert.equal(motionDroppedMessage(undefined), null);
});

test('historySize: about 5 seconds at any rate', () => {
  assert.equal(historySize(50), 250);          // what the graph always had
  assert.equal(historySize(1666), 8330);
  assert.equal(historySize(10), 50);
  assert.equal(historySize(0), 50);
});

test('LossCounter counts the gaps in n', () => {
  const c = new LossCounter();
  assert.deepEqual([c.lost, c.reported], [0, false]);
  for (const n of [0, 1, 2, 3]) c.add(n);
  assert.deepEqual([c.lost, c.reported], [0, true]);
  for (const n of [41, 42, 57]) c.add(n);       // 4..40 lost, then 43..56
  assert.equal(c.lost, 37 + 14);
  c.add(0);                                     // the numbers started again (new setting): not a loss
  c.add(1);
  assert.equal(c.lost, 51);
  c.reset();
  assert.deepEqual([c.lost, c.reported], [0, false]);
  c.add(5);                                     // the first number after a reset only marks the start
  assert.equal(c.lost, 0);
  c.add(2 ** 32 - 1);                           // a step back, not 4 billion lost
  c.add(1);                                     // over the wrap: 0 is missing
  assert.equal(c.lost, 1);
});

test('LossCounter: firmware 1.0 does not number its samples', () => {
  const c = new LossCounter();
  c.add(null);
  c.add(undefined);
  assert.deepEqual([c.lost, c.reported], [0, false]);
});

test('LossCounter with the pretend band: no loss at 416 accel only; a dropped frame shows as its samples', async () => {
  const transport = sim(42);
  const band = await Band.connect(42, { transport });
  const counter = new LossCounter();
  let skip = false;
  let skipped = 0;
  band.on('motion', (m) => {
    if (skip) skipped++;
    else counter.add(m.n);
  });
  await band.configure({ motion: 416, sensors: 'accel' });
  transport.step(1000);
  assert.ok(counter.reported);
  assert.equal(counter.lost, 0);
  skip = true;
  transport.step(100);
  skip = false;
  transport.step(500);
  assert.ok(skipped > 0);
  assert.equal(counter.lost, skipped);
  await band.disconnect();
});

test('ArrivalRate: samples per second from the band\'s own time stamps', () => {
  const meter = new ArrivalRate(0);
  assert.equal(meter.take(1000), null);                 // nothing yet, and not quiet for long
  assert.equal(meter.take(2500), 0);                    // quiet for 2.5 s
  for (let i = 0; i <= 416; i++) meter.add(5000 + i * 2.4);
  assert.ok(Math.abs(meter.take(3000) - 1000 / 2.4) < 0.01);
  for (let i = 417; i <= 832; i++) meter.add(5000 + i * 2.4);     // the next second joins on without a gap
  assert.ok(Math.abs(meter.take(4000) - 1000 / 2.4) < 0.01);
  assert.equal(meter.take(5000), null);                 // keeps the last reading for a moment
  assert.equal(meter.take(6500), 0);
  // half the samples lost on the way: half the rate
  meter.reset(0);
  for (let i = 0; i <= 400; i += 2) meter.add(i * 2.4);
  assert.ok(Math.abs(meter.take(1000) - 500 / 2.4) < 0.01);
  // the band's clock starting again at 0 does not upset it
  meter.reset(0);
  meter.add(2 ** 32 - 10);
  meter.add(10);
  assert.equal(meter.take(1000), 50);
  // one sample alone says nothing
  meter.reset(0);
  meter.add(1);
  assert.equal(meter.take(100), null);
});

test('ArrivalRate with the pretend band: 416 per second arrives as a little under 416 (the chip clock), 50 as 50', async () => {
  const transport = sim(42);
  const band = await Band.connect(42, { transport });
  const meter = new ArrivalRate(0);
  band.on('motion', (m) => meter.add(m.t));
  await band.configure({ motion: 416, sensors: 'accel' });
  transport.step(2000);
  const fast = meter.take(2000);
  assert.ok(fast > 405 && fast < 416, String(fast));
  await band.configure({ motion: 50, sensors: 'both' });
  meter.reset(0);
  transport.step(2000);
  assert.ok(Math.abs(meter.take(2000) - 50) < 0.5);
  await band.disconnect();
});

test('envelope: smallest and largest value per column, filling from the left', () => {
  const history = new History(12, ['v']);
  const lo = new Float32Array(4);
  const hi = new Float32Array(4);
  assert.equal(envelope(history, 'v', 4, lo, hi), 0);
  for (const v of [1, 5, 3, -2, 0, 7, 4]) history.push({ v });
  assert.equal(envelope(history, 'v', 4, lo, hi), 3);            // 7 of 12 samples: two full columns and a started one
  assert.deepEqual([...lo.slice(0, 3)], [1, -2, 4]);
  assert.deepEqual([...hi.slice(0, 3)], [5, 7, 4]);
  for (let v = 10; v < 22; v++) history.push({ v });             // the window is full and has rolled over
  assert.equal(envelope(history, 'v', 4, lo, hi), 4);
  assert.deepEqual([...lo], [10, 13, 16, 19]);
  assert.deepEqual([...hi], [12, 15, 18, 21]);
});

test('envelope keeps a short spike that plain "every n-th sample" would miss: 5 s at 1666 per second on 900 columns', () => {
  const history = new History(historySize(1666), ['v']);
  for (let i = 0; i < history.size; i++) history.push({ v: i === 4321 ? 9 : 0 });
  const columns = 900;
  const lo = new Float32Array(columns);
  const hi = new Float32Array(columns);
  assert.equal(envelope(history, 'v', columns, lo, hi), columns);
  assert.equal(Math.max(...hi), 9);
  assert.equal(hi.filter((v) => v === 9).length, 1);
  assert.equal(Math.min(...lo), 0);
});

// ---- chip temperature, the loudness bar, the USB port list, the pretend microphone

test('temperature: the page turns the stream on with plain true, and a reading arrives on firmware 1.0 and 1.1', async () => {
  const asked = startupStreams('ble').find(([label]) => label === 'temperature');
  assert.deepEqual(asked, ['temperature', { temperature: true }]);      // no interval: firmware 1.0 refuses fractions, and 5 s is fine
  for (const firmware of ['1.0', '1.1']) {
    for (const via of ['ble', 'usb']) {
      const transport = sim(42, { firmware, via });
      const band = await Band.connect(42, { transport });
      const shown = [];
      band.on('temperature', (t) => shown.push(temperatureText(t.celsius)));
      for (const [, streams] of startupStreams(via === 'usb' ? 'serial' : 'ble')) await band.configure(streams);
      assert.equal(band.streams.temperature, true, `${firmware} ${via}`);
      transport.step(11000);
      assert.equal(shown.length, 2, `${firmware} ${via}: one reading every 5 seconds`);
      for (const text of shown) assert.match(text, /^3[01]\.\d$/, `${firmware} ${via}`);
      await band.disconnect();
    }
  }
});

// ---- steps

test('steps: the page turns the activity stream on with plain true, and steps arrive on firmware 1.0 and 1.1, over Bluetooth and the cable', async () => {
  const asked = startupStreams('ble').find(([label]) => label === 'steps');
  assert.deepEqual(asked, ['steps', { activity: true }]);               // no interval: firmware 1.0 has none, and once a second is fine
  assert.ok(startupStreams('serial').some(([label]) => label === 'steps'));
  for (const firmware of ['1.0', '1.1']) {
    for (const via of ['ble', 'usb']) {
      const transport = sim(42, { firmware, via });
      const band = await Band.connect(42, { transport });
      const counter = new StepCounter();
      const shown = [];
      band.on('activity', (a) => shown.push([stepsText(counter.add(a.steps)), activityDetail(a)]));
      for (const [, streams] of startupStreams(via === 'usb' ? 'serial' : 'ble')) await band.configure(streams);
      assert.equal(band.streams.activity, true, `${firmware} ${via}`);
      transport.step(3500);
      assert.deepEqual(shown, [                                         // the pretend band walks: 2 steps a second
        ['0', 'Walking, 110 steps a minute.'], ['2', 'Walking, 110 steps a minute.'], ['4', 'Walking, 110 steps a minute.'],
      ], `${firmware} ${via}`);
      await band.disconnect();
    }
  }
});

test('StepCounter: steps since the first reading, also when the band\'s total starts again', () => {
  const counter = new StepCounter();
  assert.equal(counter.steps, null);
  assert.equal(stepsText(counter.steps), '–');
  assert.equal(counter.add(1200), 0);                 // the band's lifetime total: the first one is where we start
  assert.equal(counter.add(1203), 3);
  assert.equal(counter.add(1203), 3);
  assert.equal(counter.add(undefined), 3);            // not a reading: keep what is shown
  assert.equal(counter.add(NaN), 3);
  assert.equal(counter.add(5), 3);                    // the total started again: nothing counted is lost
  assert.equal(counter.add(9), 7);
  assert.equal(counter.steps, 7);
  counter.reset();                                    // what the page does on disconnect
  assert.equal(counter.steps, null);
  assert.equal(counter.add(40), 0);
});

test('stepsText, activityDetail and the notes under the step count', () => {
  assert.equal(stepsText(0), '0');
  assert.equal(stepsText(128), '128');
  for (const none of [null, undefined, NaN, Infinity, '12']) assert.equal(stepsText(none), '–');
  assert.equal(activityDetail({ state: 'still', cadence: 0 }), 'Standing still.');
  assert.equal(activityDetail({ state: 'still', cadence: 40 }), 'Standing still.');
  assert.equal(activityDetail({ state: 'walk', cadence: 112 }), 'Walking, 112 steps a minute.');
  assert.equal(activityDetail({ state: 'walk', cadence: 0 }), 'Walking.');
  assert.equal(activityDetail({ state: 'run', cadence: 165 }), 'Running, 165 steps a minute.');
  assert.equal(activityDetail({ state: 'unknown', cadence: 3 }), 'Waiting for the band.');
  assert.match(STEPS_NOTE, /^Steps since this page connected\./);
  assert.match(STEPS_NOTE, /a lifetime total that goes on from one connection to the next\. This page takes away the first total it gets\.$/);
});

// ---- a connection attempt that never ends

test('a slow connection attempt gets a sentence that names the way out, and Cancel has its own', () => {
  assert.equal(STILL_CONNECTING_MS, 15000);
  assert.equal(stillConnectingMessage('ble', 'Vexo-HACK-42'),
    "Still waiting for Vexo-HACK-42. Pick it in the browser's list. Not in the list? Close whatever else is connected to it "
    + "(another tab, another program, a teammate's laptop), then double-tap the band. No list on the screen? Press Cancel and try again.");
  assert.match(stillConnectingMessage('serial', 'Vexo-HACK-42'), /Pick "Vexo Open band" in the browser's list\. No list on the screen\? Press Cancel and try again\.$/);
  assert.equal(CANCELLED_MESSAGE, 'Cancelled. Press Connect to try again.');
});

test('temperatureText: one decimal, and a dash when there is no reading', () => {
  assert.equal(temperatureText(26.75), '26.8');
  assert.equal(temperatureText(26.5), '26.5');
  assert.equal(temperatureText(-3), '-3.0');
  assert.equal(temperatureText(0), '0.0');
  for (const none of [null, undefined, NaN, Infinity, '26']) assert.equal(temperatureText(none), '–');
  assert.match(TEMPERATURE_NOTE, /chip inside the band/);
  assert.match(TEMPERATURE_NOTE, /not your skin temperature/);
  assert.match(TEMPERATURE_NOTE, /above the room/);
});

test('loudnessBar: a quiet room is nearly empty, speech is clearly visible, loud is full', () => {
  assert.equal(BAR_EMPTY_DB, 52);
  assert.equal(BAR_FULL_DB, 12);
  // rms_db measured on a real band at its standard gain (+20 dB): a quiet room read 49 to 52.
  for (const quiet of [49, 50, 51, 52]) assert.ok(loudnessBar(quiet) <= 0.08, `quiet ${quiet} -> ${loudnessBar(quiet)}`);
  // The loudest moment of a spoken sentence read 19 to 27, a steady tone from a speaker 28.
  for (const speech of [19, 26, 27, 28]) {
    const bar = loudnessBar(speech);
    assert.ok(bar >= 0.6 && bar < 0.9, `speech ${speech} -> ${bar}`);
  }
  assert.equal(loudnessBar(32), 0.5);
  // Both ends are flat: quieter than a quiet room is empty, very loud is full.
  for (const silent of [53, 66, 96, 200]) assert.equal(loudnessBar(silent), 0);
  for (const loud of [12, 5, 0, -1]) assert.equal(loudnessBar(loud), 1);
  // Louder never draws less.
  for (let db = 96; db > 0; db--) assert.ok(loudnessBar(db - 1) >= loudnessBar(db), `at ${db}`);
  for (const none of [undefined, null, NaN, '30']) assert.equal(loudnessBar(none), 0);
  // The sentence on the page names the same two numbers.
  assert.ok(LOUDNESS_NOTE.includes(`from ${BAR_EMPTY_DB} (empty) to ${BAR_FULL_DB} (full)`));
  assert.match(LOUDNESS_NOTE, /empty in a quiet room/);
});

test('loudnessBar: where the library\'s level sat half full, the bar is near empty; the pretend band uses most of it', async () => {
  const transport = sim(42);
  const band = await Band.connect(42, { transport });
  const seen = [];
  band.on('mic-level', (m) => seen.push(m));
  await band.configure({ micLevel: true });
  transport.step(5000);                                // more than one swell of the pretend voice
  await band.disconnect();
  const bars = seen.map((m) => loudnessBar(m.rmsDb));
  assert.ok(Math.min(...bars) <= 0.1, `lowest ${Math.min(...bars)}`);
  assert.ok(Math.max(...bars) >= 0.7, `highest ${Math.max(...bars)}`);
  // The library's public value is unchanged: 50 dB below full scale is still about half.
  const quiet = seen.find((m) => m.rmsDb === 50);
  assert.ok(quiet, 'the pretend band passes through 50');
  assert.ok(Math.abs(quiet.level - (1 - 50 / 96)) < 1e-9);
  assert.equal(loudnessBar(50), 0.05);
});

test('connectProblem over USB: a port that is not a band, and a port that never answers', () => {
  const wanted = bandName(42);
  // The library refuses a port that is not a USB device; its sentence is shown as it is.
  const refused = new VexoError('port-failed', notTheBand({}));
  const text = connectProblem(refused, { kind: 'serial', wanted });
  assert.match(text, /^That port is not a band/);
  assert.match(text, /pick "Vexo Open band"/);
  // Another USB device was picked: it never answers, and the message says what to pick.
  const silent = connectProblem(new VexoError('timeout', 'x'), { kind: 'serial', wanted });
  assert.match(silent, /did not answer in time\. If you picked another port than "Vexo Open band"/);
  assert.match(silent, /unplug the cable, plug it in again/);
  assert.doesNotMatch(connectProblem(new VexoError('timeout', 'x'), { kind: 'ble', wanted }), /port/);
});

test('the pretend microphone note says it is a recording of a person reading aloud, not the room', () => {
  assert.match(PRETEND_MIC_NOTE, /^Pretend band: there is no real microphone\./);
  assert.match(PRETEND_MIC_NOTE, /recording of a person reading aloud that is built into this page/);
  assert.match(PRETEND_MIC_NOTE, /None of it is the sound in your room\./);
});

test('streamTakenMessage: what happened, why, and the way out', () => {
  const running = streamTakenMessage(['motion'], false, 'ble');
  assert.match(running, /^No motion data is arriving any more: another program on the cable \(USB\) took this stream\./);
  assert.match(running, /share its streams, its settings and its light/);
  assert.match(running, /Close the other program, then press "Take it back"\.$/);
  const ended = streamTakenMessage(['motion', 'micLevel'], true, 'usb');
  assert.match(ended, /^No motion, loudness data is arriving any more: another program on Bluetooth took these streams and has ended since\./);
  assert.match(ended, /Press "Take it back" to get the data back\.$/);
});

test('the page shows stream-taken with a "Take it back" button, and takes back with the library', async () => {
  const { readFileSync } = await import('node:fs');
  const app = readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  assert.match(html, /<div id="taken" class="taken" hidden>\s*<p id="taken-text"[^>]*role="status"[^>]*><\/p>\s*<button id="take-back">Take it back<\/button>/);
  assert.match(app, /b\.on\('stream-taken'/);
  assert.match(app, /await b\.takeBack\(\)/);
  assert.match(app, /streamTakenMessage\(taken, takenStopped, link\)/);
});
