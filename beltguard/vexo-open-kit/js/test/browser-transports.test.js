// Web Bluetooth and Web Serial only exist in browsers. These tests put small fakes of
// navigator.bluetooth and navigator.serial in place and check what the transports ask
// the browser to do.
import test from 'node:test';
import assert from 'node:assert/strict';
import { Band, WebBluetoothTransport, WebSerialTransport, SERVICE_UUID, codec } from '../src/index.js';
import { notTheBand } from '../src/transports/web-serial.js';
import { hex } from './helpers.js';

const INFO = { proto_major: 1, proto_minor: 0, fw_major: 1, fw_minor: 0, fw_patch: 0, hw: 1, band_no: 42, caps: 0x7ff, max_frame: 244, uptime_ms: 5 };

/** Answer a command frame the way a very small band would. */
function answer(frame) {
  const cmd = codec.decodeCommand(frame);
  return codec.encodeResponse(cmd.seq, cmd.opcode, 'OK', cmd.opcode === 'GET_INFO' ? INFO : {});
}

function useNavigator(t, navigator) {
  const before = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  Object.defineProperty(globalThis, 'navigator', { value: navigator, configurable: true, writable: true });
  t.after(() => (before ? Object.defineProperty(globalThis, 'navigator', before) : delete globalThis.navigator));
}

// ---------------------------------------------------------------- Web Bluetooth

function fakeBluetooth({ deviceName = 'Vexo-HACK-42' } = {}) {
  const log = [];             // everything the transport did, in order
  const listeners = {};
  let slowWrites = 0;
  const tx = {
    uuid: codec.TX_UUID,
    addEventListener: (type, fn) => { log.push(`tx.listen ${type}`); listeners.tx = fn; },
    startNotifications: async () => { await null; log.push('tx.startNotifications'); },
  };
  const notify = (bytes) => {
    // Hand the bytes over the way a browser does: a DataView that sits inside a bigger buffer.
    const big = new Uint8Array(bytes.length + 8);
    big.set(bytes, 4);
    listeners.tx({ target: { value: new DataView(big.buffer, 4, bytes.length) } });
  };
  const rx = {
    uuid: codec.RX_UUID,
    writeValueWithResponse: async (bytes) => {
      log.push(`rx.writeWithResponse ${hex(bytes)}`);
      slowWrites++;
      await new Promise((r) => setTimeout(r, 2));
      log.push(`rx.written ${hex(bytes)}`);
      slowWrites--;
      fake.mostWritesAtOnce = Math.max(fake.mostWritesAtOnce, slowWrites + 1);
      notify(answer(bytes));
    },
    writeValueWithoutResponse: async () => { throw new Error('the contract says: write WITH response'); },
    writeValue: async () => { throw new Error('the contract says: write WITH response'); },
  };
  const service = {
    getCharacteristic: async (uuid) => {
      log.push(`getCharacteristic ${uuid}`);
      return uuid === codec.RX_UUID ? rx : uuid === codec.TX_UUID ? tx : Promise.reject(new Error('no such characteristic'));
    },
  };
  const device = {
    name: deviceName,
    addEventListener: (type, fn) => { listeners[type] = fn; },
    gatt: {
      connect: async () => { log.push('gatt.connect'); if (fake.outOfRange) throw new Error('Connection attempt failed.'); return { getPrimaryService: async (uuid) => { log.push(`getPrimaryService ${uuid}`); return service; } }; },
      disconnect: () => { log.push('gatt.disconnect'); listeners.gattserverdisconnected?.(); },
    },
  };
  const fake = {
    log, device, notify, mostWritesAtOnce: 0, requests: [], outOfRange: false,
    drop: () => listeners.gattserverdisconnected(),
    navigator: { bluetooth: { requestDevice: async (options) => { fake.requests.push(options); log.push('requestDevice'); return device; } } },
  };
  return fake;
}

test('Web Bluetooth: the chooser filter is the exact name AND the service UUID', async (t) => {
  const fake = fakeBluetooth();
  useNavigator(t, fake.navigator);
  const band = await Band.connect(42);
  assert.deepEqual(fake.requests, [{
    filters: [{ name: 'Vexo-HACK-42', services: [SERVICE_UUID] }],
    optionalServices: [SERVICE_UUID],
  }]);
  assert.equal(band.transportKind, 'ble');
  assert.equal(band.name, 'Vexo-HACK-42');
  assert.equal(band.info.uptimeMs, 5);
  await band.disconnect();
});

test('Web Bluetooth: with no number the filter is the service UUID alone', async (t) => {
  const fake = fakeBluetooth();
  useNavigator(t, fake.navigator);
  const band = await Band.connect(null);
  assert.deepEqual(fake.requests, [{ filters: [{ services: [SERVICE_UUID] }], optionalServices: [SERVICE_UUID] }]);
  assert.equal(band.number, 42);
});

test('Web Bluetooth: subscribes to TX before the first command, and writes with response', async (t) => {
  const fake = fakeBluetooth();
  useNavigator(t, fake.navigator);
  const band = await Band.connect(42);
  const getInfo = hex(codec.encodeCommand(0, 'GET_INFO'));
  assert.deepEqual(fake.log, [
    'requestDevice',
    'gatt.connect',
    `getPrimaryService ${SERVICE_UUID}`,
    `getCharacteristic ${codec.RX_UUID}`,
    `getCharacteristic ${codec.TX_UUID}`,
    'tx.listen characteristicvaluechanged',
    'tx.startNotifications',
    `rx.writeWithResponse ${getInfo}`,
    `rx.written ${getInfo}`,
  ]);
  await band.identify();
  assert.equal(fake.log.at(-1), `rx.written ${hex(codec.encodeCommand(1, 'IDENTIFY', { seconds: 5 }))}`);
});

test('Web Bluetooth: writes go out one at a time, even when send() is called in a burst', async (t) => {
  const fake = fakeBluetooth();
  useNavigator(t, fake.navigator);
  const transport = new WebBluetoothTransport(42);
  await transport.open();
  const frames = [1, 2, 3, 4].map((seq) => codec.encodeCommand(seq, 'LED_SET', { r: seq, g: 0, b: 0, brightness: 255, pattern: 1, period_ms: 1000 }));
  await Promise.all(frames.map((frame) => transport.send(frame)));
  assert.equal(fake.mostWritesAtOnce, 1);
  assert.deepEqual(fake.log.filter((l) => l.startsWith('rx.writeWithResponse')).map((l) => l.split(' ')[1]), frames.map(hex));
  await assert.rejects(transport.send(new Uint8Array(245)), { code: 'bad-arg' });
  await transport.send(frames[0]);                                      // a refused frame does not block the next one
});

test('Web Bluetooth: frames, a lost link, and closing', async (t) => {
  const fake = fakeBluetooth();
  useNavigator(t, fake.navigator);
  const band = await Band.connect(42, { now: () => 1000000 });    // the computer's clock, pinned
  const taps = [];
  const gone = [];
  band.on('tap', (e) => taps.push(e));
  band.on('disconnect', (e) => gone.push(e));
  fake.notify(codec.encodeStream('event', { t_ms: 77, event: 1, arg: 0 }));
  // the fake band said uptime 5 ms when it answered GET_INFO, so band time 77 is 72 ms later
  assert.deepEqual(taps, [{ t: 77, kind: 'tap', arg: 0, time: 1000072 }]);
  fake.drop();
  assert.deepEqual(gone, [{ reason: 'lost' }]);
  await assert.rejects(band.getLed(), { code: 'not-connected' });

  const second = await Band.connect(42);
  const closed = [];
  second.on('disconnect', (e) => closed.push(e));
  await second.disconnect();
  assert.deepEqual(closed, [{ reason: 'closed' }]);
  assert.equal(fake.log.at(-1), 'gatt.disconnect');
});

test('Web Bluetooth: wrong band on the link, cancelled chooser, connect failure', async (t) => {
  const fake = fakeBluetooth();
  useNavigator(t, fake.navigator);
  await assert.rejects(Band.connect(17), { code: 'wrong-band' });      // the fake band says it is 42
  assert.equal(fake.log.at(-1), 'gatt.disconnect');

  fake.navigator.bluetooth.requestDevice = async () => { throw Object.assign(new Error('User cancelled'), { name: 'NotFoundError' }); };
  await assert.rejects(Band.connect(42), (e) => e.code === 'cancelled' && /Vexo-HACK-42/.test(e.message));

  fake.navigator.bluetooth.requestDevice = async () => fake.device;
  fake.device.gatt.connect = async () => { throw new Error('Connection attempt failed.'); };
  await assert.rejects(Band.connect(42), (e) => e.code === 'not-found-nearby' && /Double-tap/.test(e.message));
});

test('Web Bluetooth: a chooser that never answers and a band that never answers end with a message, not for ever', async (t) => {
  const fake = fakeBluetooth();
  useNavigator(t, fake.navigator);
  const never = new Promise(() => {});

  // no list of bands ever shows up (or nobody picks): 'cancelled' after chooserSeconds
  fake.navigator.bluetooth.requestDevice = () => never;
  let transport = new WebBluetoothTransport(42);
  assert.equal(transport.chooserSeconds, 120);       // a band can be found for 60 seconds after a double-tap
  transport.chooserSeconds = 0.05;
  let started = Date.now();
  await assert.rejects(Band.connect(42, { transport }),
    (e) => e.code === 'cancelled' && /No band was picked within 0\.05 seconds/.test(e.message) && /Vexo-HACK-42/.test(e.message));
  assert.ok(Date.now() - started < 2000);

  // a band was picked but never answers: 'not-found-nearby' after connectSeconds, and the band is let go
  fake.navigator.bluetooth.requestDevice = async () => fake.device;
  fake.device.gatt.connect = () => never;
  transport = new WebBluetoothTransport(42);
  assert.equal(transport.connectSeconds, 20);
  transport.connectSeconds = 0.05;
  started = Date.now();
  await assert.rejects(Band.connect(42, { transport }),
    (e) => e.code === 'not-found-nearby' && /did not answer within 0\.05 seconds/.test(e.message) && /Double-tap/.test(e.message));
  assert.ok(Date.now() - started < 2000);
  assert.equal(fake.log.at(-1), 'gatt.disconnect');

  // an answer that comes after the limit does not bring a half-open link to life
  let answer;
  fake.device.gatt.connect = () => new Promise((done) => { answer = done; });
  transport = new WebBluetoothTransport(42);
  transport.connectSeconds = 0.05;
  await assert.rejects(Band.connect(42, { transport }), { code: 'not-found-nearby' });
  answer({ getPrimaryService: async () => ({ getCharacteristic: async () => ({ addEventListener() {}, startNotifications: async () => {} }) }) });
  await new Promise((done) => setTimeout(done, 20));
  await assert.rejects(transport.send(new Uint8Array([1])), { code: 'not-connected' });
  assert.equal(fake.log.at(-1), 'gatt.disconnect');
});

test('Web Bluetooth: reopen() connects to the already chosen device again, with no chooser, and listens again', async (t) => {
  const fake = fakeBluetooth();
  useNavigator(t, fake.navigator);
  const transport = new WebBluetoothTransport(42);
  const frames = [];
  const closes = [];
  transport.onFrame = (bytes) => frames.push(codec.decodeFrame(bytes));
  transport.onClose = (reason) => closes.push(reason);
  await transport.open();
  fake.drop();
  assert.deepEqual(closes, ['lost']);
  await assert.rejects(transport.send(codec.encodeCommand(0, 'GET_INFO')), { code: 'not-connected' });

  fake.log.length = 0;
  await transport.reopen();
  assert.deepEqual(fake.log, [                                          // no requestDevice: no click needed
    'gatt.connect',
    `getPrimaryService ${SERVICE_UUID}`,
    `getCharacteristic ${codec.RX_UUID}`,
    `getCharacteristic ${codec.TX_UUID}`,
    'tx.listen characteristicvaluechanged',
    'tx.startNotifications',
  ]);
  assert.equal(fake.requests.length, 1);
  await transport.send(codec.encodeCommand(7, 'GET_INFO'));
  assert.equal(frames.at(-1).seq, 7);                                   // the answer arrives over the new subscription
  fake.drop();
  assert.deepEqual(closes, ['lost', 'lost']);                           // one close per drop, not one per listener

  // Out of range: reopen() fails with not-found-nearby and reports no extra close.
  fake.outOfRange = true;
  await assert.rejects(transport.reopen(), (e) => e.code === 'not-found-nearby' && /Vexo-HACK-42/.test(e.message));
  assert.deepEqual(closes, ['lost', 'lost']);
  fake.outOfRange = false;
  await transport.reopen();
  await transport.close();
  assert.deepEqual(closes, ['lost', 'lost', 'closed']);

  // Never opened: reopen() is the same as open().
  const fresh = new WebBluetoothTransport(42);
  await fresh.reopen();
  assert.equal(fake.requests.length, 2);
});

test('Web Bluetooth: auto-reconnect comes back through reopen() and sets the LED again', async (t) => {
  const fake = fakeBluetooth();
  useNavigator(t, fake.navigator);
  const band = await Band.connect(42, { autoReconnect: true });
  band._reconnectPauseMs = 5;
  await band.setLed('#ff0000');
  const events = [];
  for (const type of ['reconnecting', 'reconnected', 'disconnect']) band.on(type, (e) => events.push(type));
  fake.outOfRange = true;
  fake.log.length = 0;
  fake.drop();
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(band.reconnecting, true);
  fake.outOfRange = false;
  await new Promise((resolve) => band.on('reconnected', resolve));
  assert.deepEqual(events, ['reconnecting', 'reconnected']);
  assert.ok(!fake.log.includes('requestDevice'));
  const sent = fake.log.filter((l) => l.startsWith('rx.written')).map((l) => codec.decodeCommand(Buffer.from(l.split(' ')[1], 'hex')).opcode);
  assert.deepEqual(sent, ['GET_INFO', 'LED_SET']);
  await band.disconnect();
  assert.deepEqual(events, ['reconnecting', 'reconnected', 'disconnect']);
});

test('unavailableReason: a sentence when the browser API is missing, null when it is there', (t) => {
  assert.match(WebBluetoothTransport.unavailableReason(), /browser/);
  assert.match(WebSerialTransport.unavailableReason(), /browser/);
  useNavigator(t, { bluetooth: {}, serial: {} });
  assert.equal(WebBluetoothTransport.unavailableReason(), null);
  assert.equal(WebSerialTransport.unavailableReason(), null);
});

// ---------------------------------------------------------------- Web Serial

function fakeSerial() {
  const log = [];
  const written = [];         // every chunk of bytes the transport put on the wire
  const fake = {
    log, written, requests: [], ports: [],
    fromBand: (bytes) => fake.current.fromBand(bytes),
    pullCable: () => fake.current.pullCable(),
    /** A port as the browser hands it out. A replugged band is a new port with the same USB ids. */
    plugIn(ids = { usbVendorId: 0x1234, usbProductId: 0x0001 }) {
      let deliver;            // hands bytes to the transport's reader
      let finish;
      let queue = [];
      const reader = {
        read: () => new Promise((resolve, reject) => {
          if (queue.length) return resolve({ value: queue.shift(), done: false });
          deliver = (value) => { deliver = null; resolve({ value, done: false }); };
          finish = (error) => (error ? reject(error) : resolve({ value: undefined, done: true }));
        }),
        cancel: async () => { log.push('reader.cancel'); finish?.(); },
        releaseLock: () => {},
      };
      const fromBand = (bytes) => (deliver ? deliver(bytes) : queue.push(bytes));
      const bandSide = new codec.Deframer();
      const writer = {
        write: async (bytes) => {
          written.push(Uint8Array.from(bytes));
          for (const frame of bandSide.push(bytes)) {
            // Answer in two pieces, to show that the transport glues the bytes back together.
            const reply = codec.frameForUsb(answer(frame));
            fromBand(reply.subarray(0, 3));
            setTimeout(() => fromBand(reply.subarray(3)), 1);
          }
        },
        releaseLock: () => {},
      };
      const port = {
        open: async (options) => { log.push(`open ${JSON.stringify(options)}`); fake.current = port; queue = []; },
        setSignals: async (signals) => { log.push(`setSignals ${JSON.stringify(signals)}`); },
        close: async () => { log.push('close'); },
        getInfo: () => ids,
        readable: { getReader: () => reader },
        writable: { getWriter: () => writer },
        fromBand,
        pullCable: () => { fake.ports = fake.ports.filter((p) => p !== port); finish(new Error('The device has been lost.')); },
      };
      fake.ports.push(port);
      return port;
    },
    navigator: {
      serial: {
        requestPort: async (options) => { fake.requests.push(options); log.push('requestPort'); return fake.ports[0]; },
        getPorts: async () => { log.push('getPorts'); return [...fake.ports]; },
      },
    },
  };
  fake.current = fake.plugIn();
  return fake;
}

test('Web Serial: opens the port, raises DTR, and puts COBS frames ending in 00 on the wire', async (t) => {
  const fake = fakeSerial();
  useNavigator(t, fake.navigator);
  const band = await Band.connect(42, { transport: 'serial' });
  assert.equal(band.transportKind, 'serial');
  assert.equal(band.name, 'Vexo-HACK-42');
  assert.deepEqual(fake.requests, [{ filters: [] }]);                   // no USB vendor filter: the band's ids are not final
  assert.equal(fake.log[0], 'requestPort');
  assert.match(fake.log[1], /^open \{"baudRate":\d+,"bufferSize":\d+\}$/);
  assert.equal(fake.log[2], 'setSignals {"dataTerminalReady":true}');

  await band.storage.put(1, Uint8Array.of(0, 0, 7, 0));
  await band.setLed('#000000');
  assert.equal(hex(fake.written[0]), '00');                             // the opening 00
  assert.equal(hex(fake.written[1]), '0201020100');                     // GET_INFO seq 0: 01 00 01 as COBS, then 00
  const commands = [];
  for (const bytes of fake.written.slice(1)) {
    assert.equal(bytes.at(-1), 0, 'ends in 00');
    assert.equal(bytes.indexOf(0), bytes.length - 1, 'no 00 inside');
    commands.push(codec.decodeCommand(codec.cobsDecode(bytes.subarray(0, -1))).opcode);
  }
  assert.deepEqual(commands, ['GET_INFO', 'STORE_PUT', 'LED_SET']);

  const gone = [];
  band.on('disconnect', (e) => gone.push(e));
  await band.disconnect();
  assert.deepEqual(gone, [{ reason: 'closed' }]);
  assert.deepEqual(fake.log.slice(-3), ['reader.cancel', 'setSignals {"dataTerminalReady":false}', 'close']);
});

test('Web Serial: stream frames arrive; a pulled cable is a lost link', async (t) => {
  const fake = fakeSerial();
  useNavigator(t, fake.navigator);
  const band = await Band.connect(null, { transport: 'serial' });       // whatever is on the cable
  assert.equal(band.number, 42);
  const battery = [];
  const gone = [];
  band.on('battery', (e) => battery.push(e));
  band.on('disconnect', (e) => gone.push(e));
  const two = Uint8Array.of(
    ...codec.frameForUsb(codec.encodeStream('battery', { percent: 80, state: 0, millivolts: 4000 })),
    0x05, 0x01, 0x02, 0x00,                                             // not valid COBS: dropped
    ...codec.frameForUsb(codec.encodeStream('battery', { percent: 79, state: 0, millivolts: 3990 })),
  );
  fake.fromBand(two);
  await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(battery.map((b) => b.percent), [80, 79]);
  fake.pullCable();
  await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(gone, [{ reason: 'lost' }]);
  await assert.rejects(band.getLed(), { code: 'not-connected' });
});

test('Web Serial: cancelled chooser and a port that will not open', async (t) => {
  const fake = fakeSerial();
  useNavigator(t, fake.navigator);
  const requestPort = fake.navigator.serial.requestPort;
  fake.navigator.serial.requestPort = async () => { throw Object.assign(new Error('No port selected'), { name: 'NotFoundError' }); };
  await assert.rejects(Band.connect(42, { transport: 'serial' }), (e) => {
    assert.equal(e.code, 'cancelled');
    assert.match(e.message, /data cable/);
    // C3: the Mac permission question, and what the port is called there.
    assert.match(e.message, /On a Mac, the first time a band is plugged in macOS may ask "Allow accessory to connect\?"/);
    assert.match(e.message, /until you press Allow \(or allow it under System Settings > Privacy & Security\) no port appears/);
    assert.match(e.message, /\/dev\/cu\.usbmodem/);
    assert.match(e.message, /"Vexo Open band"/);
    return true;
  });
  fake.navigator.serial.requestPort = async (options) => {
    const port = await requestPort(options);
    port.open = async () => { throw new Error('Failed to open serial port.'); };
    return port;
  };
  await assert.rejects(Band.connect(42, { transport: 'serial' }), { code: 'port-failed' });
});

test('Web Serial: reopen() finds the port again through getPorts() (same USB ids), with no chooser', async (t) => {
  const fake = fakeSerial();
  useNavigator(t, fake.navigator);
  const transport = new WebSerialTransport();
  const frames = [];
  const closes = [];
  transport.onFrame = (bytes) => frames.push(codec.decodeFrame(bytes));
  transport.onClose = (reason) => closes.push(reason);
  await transport.open();
  fake.pullCable();
  await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(closes, ['lost']);
  await assert.rejects(transport.send(codec.encodeCommand(0, 'GET_INFO')), { code: 'not-connected' });

  // Not plugged in yet: nothing to open.
  fake.log.length = 0;
  await assert.rejects(transport.reopen(), (e) => e.code === 'not-found-nearby' && /Plug it in again/.test(e.message));
  assert.deepEqual(fake.log, ['setSignals {"dataTerminalReady":false}', 'close', 'getPorts']);   // the dead port was given back first

  // Something else with other ids is not our band.
  fake.plugIn({ usbVendorId: 0x9999, usbProductId: 0x0001 });
  await assert.rejects(transport.reopen(), { code: 'not-found-nearby' });

  // The band is back: a new port object with the same ids.
  const back = fake.plugIn();
  fake.log.length = 0;
  fake.written.length = 0;
  await transport.reopen();
  assert.equal(transport.port, back);
  assert.equal(fake.log[0], 'getPorts');
  assert.match(fake.log[1], /^open \{"baudRate":\d+,"bufferSize":\d+\}$/);
  assert.equal(fake.log[2], 'setSignals {"dataTerminalReady":true}');
  assert.ok(!fake.log.includes('requestPort'));
  assert.equal(fake.requests.length, 1);
  assert.equal(hex(fake.written[0]), '00');                             // the opening 00 again
  await transport.send(codec.encodeCommand(9, 'GET_INFO'));
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(frames.at(-1).seq, 9);
  await transport.close();
  assert.deepEqual(closes, ['lost', 'closed']);

  // Never opened: reopen() is the same as open().
  const fresh = new WebSerialTransport();
  await fresh.reopen();
  assert.equal(fake.requests.length, 2);
  await fresh.close();
});

test('Web Serial: auto-reconnect comes back when the cable is plugged in again', async (t) => {
  const fake = fakeSerial();
  useNavigator(t, fake.navigator);
  const band = await Band.connect(42, { transport: 'serial', autoReconnect: true });
  band._reconnectPauseMs = 5;
  await band.setLed('#00ff00');
  const events = [];
  for (const type of ['reconnecting', 'reconnected', 'disconnect']) band.on(type, () => events.push(type));
  fake.pullCable();
  await new Promise((r) => setTimeout(r, 30));
  assert.deepEqual(events, ['reconnecting']);
  fake.written.length = 0;
  fake.plugIn();
  const back = await new Promise((resolve) => band.on('reconnected', resolve));
  assert.deepEqual(back.failed, []);
  assert.ok(back.attempts >= 2);
  const sent = fake.written.slice(1).map((bytes) => codec.decodeCommand(codec.cobsDecode(bytes.subarray(0, -1))).opcode);
  assert.deepEqual(sent, ['GET_INFO', 'LED_SET']);
  assert.equal(fake.requests.length, 1);                                // the chooser was shown once, at the start
  await band.disconnect();
  assert.deepEqual(events, ['reconnecting', 'reconnected', 'disconnect']);
});

test('Web Serial: a picked port that is not a USB device is refused before it is opened', async (t) => {
  const fake = fakeSerial();
  useNavigator(t, fake.navigator);
  // What a Mac lists next to the band: built-in ports, which have no USB ids.
  fake.ports.length = 0;
  fake.plugIn({});
  const error = await Band.connect(42, { transport: 'serial' }).then(() => null, (e) => e);
  assert.equal(error.code, 'port-failed');
  assert.match(error.message, /^That port is not a band: it is not a USB device\. Connect again and pick "Vexo Open band"\./);
  assert.match(error.message, /cu\.usbmodem.*COM.*ttyACM/);
  assert.deepEqual(fake.log, ['requestPort']);                          // never opened, so nothing to give back

  // The same transport can be used again, and the band is then accepted.
  fake.ports.length = 0;
  fake.plugIn({ usbVendorId: 0x2fe3, usbProductId: 0x0004 });
  const band = await Band.connect(42, { transport: 'serial' });
  assert.equal(band.name, 'Vexo-HACK-42');
  await band.disconnect();
});

test('Web Serial: any USB port is let through, because the band\'s USB ids are not final', async (t) => {
  assert.equal(notTheBand({ usbVendorId: 0x2fe3, usbProductId: 0x0004 }), null);     // the first build
  assert.equal(notTheBand({ usbVendorId: 0x1234, usbProductId: 0x0001 }), null);     // whatever a later build says
  assert.equal(notTheBand({ usbVendorId: 0 }), null);
  for (const builtIn of [{}, undefined, null, { bluetoothServiceClassId: '00001101-0000-1000-8000-00805f9b34fb' }]) {
    assert.match(notTheBand(builtIn), /not a USB device/);
  }
  // A browser whose ports have no getInfo() cannot tell: the port is tried.
  const fake = fakeSerial();
  useNavigator(t, fake.navigator);
  delete fake.ports[0].getInfo;
  const band = await Band.connect(42, { transport: 'serial' });
  assert.equal(band.transportKind, 'serial');
  await band.disconnect();
});
