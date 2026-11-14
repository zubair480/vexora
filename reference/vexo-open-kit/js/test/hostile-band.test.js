// A band (or a file) that lies. Every number that comes from outside is checked here with
// the worst value it can take: lengths, sample counts, counters that jump, bytes that never
// end, files that are cut off. Nothing in the library may loop, grow or allocate without a
// limit because of them, and a well-formed band must still work afterwards.
//
// Also here, because the band's firmware relies on them: what the USB transport does with
// the port (one 00 first, and it never stops reading), and firmware version numbers.
import test from 'node:test';
import assert from 'node:assert/strict';
import { Band, SimTransport, WebSerialTransport, codec } from '../src/index.js';
import { SeqTracker, MAX_FILL, oggPackets } from '../src/audio.js';

const { MAX_FRAME } = codec;
const u32 = (n) => [n & 0xff, (n >>> 8) & 0xff, (n >>> 16) & 0xff, (n >>> 24) & 0xff];
const bytes = (...parts) => Uint8Array.from(parts.flat());
const zeros = (n) => new Array(n).fill(0);
const BATTERY = codec.encodeStream('battery', { percent: 50, state: 0, millivolts: 3900 });
const pause = (ms) => new Promise((r) => setTimeout(r, ms));

// A small random number source with a fixed start, so a failure can be repeated.
function randomSource(seed) {
  let s = seed;
  return (below) => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return Math.floor((s / 2 ** 32) * below);
  };
}

// ---------------------------------------------------------------- frames

test('hostile frames: longer than the limit, or a count larger than the frame, is dropped', () => {
  assert.ok(codec.decodeFrame(BATTERY));
  assert.equal(codec.decodeFrame(bytes([...BATTERY], zeros(MAX_FRAME))), null);
  assert.equal(codec.decodeFrame(new Uint8Array(100000).fill(0x13)), null);
  assert.equal(codec.decodeFrame(new Uint8Array(0)), null);
  const motion = bytes(0x10, u32(1), 10, 255);                        // says 255 samples, has none
  assert.equal(codec.decodeFrame(motion), null);
  assert.equal(codec.decodeFrame(bytes([...motion], zeros(12 * 19))), null);
  const fast = bytes(0x18, u32(1), u32(0), u32(1000), 1, 255);        // 255 samples of 6 bytes = 1530
  assert.equal(codec.decodeFrame(fast), null);
  assert.equal(codec.decodeFrame(bytes([...fast], zeros(MAX_FRAME - fast.length))), null);
  for (const sensors of [0, 4, 255]) {                                // the size of a sample is unknown
    assert.equal(codec.decodeFrame(bytes(0x18, u32(1), u32(0), u32(1000), sensors, 1, zeros(12))), null);
  }
  assert.equal(codec.decodeFrame(bytes(0x13, 7, 0, 255, zeros(10))), null);     // audio: 255 bytes, has 10
  assert.equal(codec.decodeFrame(bytes(0x13, 7, 0, 240, zeros(240))).opus.length, 240);
});

test('hostile frames: the frame limit decides how many samples one frame holds', () => {
  let most = 0;
  for (let count = 0; count < 256; count++) {
    const f = codec.decodeFrame(bytes(0x18, u32(1), u32(0), u32(1000), 1, count, zeros(6 * count)));
    if (f) most = Math.max(most, f.samples.length);
  }
  assert.equal(most, Math.floor((MAX_FRAME - 15) / 6));               // 38
});

test('hostile frames: random bytes never throw', () => {
  const random = randomSource(123);
  for (let i = 0; i < 20000; i++) {
    const kind = [0x02, 0x03, 0x10, 0x13, 0x18, random(256)][random(6)];
    const frame = Uint8Array.from({ length: 1 + random(60) }, (_, at) => (at ? random(256) : kind));
    const f = codec.decodeFrame(frame);
    if (f?.samples) assert.ok(f.samples.length <= 40);
  }
  for (const wrong of [null, 7, 'text', [1, 2]]) assert.equal(codec.decodeFrame(wrong), null);
});

test('hostile USB bytes: bytes with no 00 do not pile up, and a 00 gets things going again', () => {
  const deframer = new codec.Deframer({ skipFirst: false });
  const noise = new Uint8Array(512).fill(0x55);
  for (let i = 0; i < 2000; i++) {                                    // 1 MB and never a 00
    assert.deepEqual(deframer.push(noise), []);
    assert.ok(deframer._packet.length <= MAX_FRAME + 3);
  }
  assert.deepEqual(deframer.push(bytes(0, [...codec.frameForUsb(BATTERY)])), [BATTERY]);
  // a packet that decodes to more than a frame, and broken COBS, are dropped without a word
  const big = codec.cobsEncode(Uint8Array.from({ length: 245 }, (_, i) => (i % 250) + 1));
  assert.deepEqual(deframer.push(bytes([...big], 0)), []);
  assert.deepEqual(deframer.push(bytes(0xff, 1, 2, 0, 5, 1, 0, 0, 0)), []);
  assert.deepEqual(deframer.push(codec.frameForUsb(BATTERY)), [BATTERY]);
});

// ---------------------------------------------------------------- sound

test('hostile sound: a packet counter that jumps is never filled with more than one second', () => {
  assert.equal(MAX_FILL, 50);
  for (const jump of [1, 2, 50, 51, 52, 500, 0x7fff, 0x8000, 0x8001, 0xffff]) {
    const seq = new SeqTracker();
    seq.accept(0);
    const verdict = seq.accept(jump);
    assert.ok(verdict.fill <= MAX_FILL, `jump ${jump}`);
    assert.equal(verdict.fill, jump - 1 <= MAX_FILL ? jump - 1 : 0, `jump ${jump}`);
  }
  const seq = new SeqTracker();
  let at = 0;
  let filled = 0;
  for (let i = 0; i < 500; i++) {                                     // every packet half the counter ahead
    at = (at + 0x7fff) & 0xffff;
    filled += seq.accept(at).fill;
  }
  assert.equal(filled, 0);
  assert.equal(seq.lost, 499 * 0x7ffe + 0);                           // the first packet sets the start; every later hole is counted
});

function oggPage(body, lacing) {
  return bytes([0x4f, 0x67, 0x67, 0x53], zeros(22), lacing.length, [...lacing], [...body]);
}
const text = (s) => [...s].map((c) => c.charCodeAt(0));
const OGG_HEAD = bytes(
  [...oggPage(bytes(text('OpusHead'), zeros(11)), [19])],
  [...oggPage(bytes(text('OpusTags'), zeros(8)), [16])],
);

test('hostile sound file: cut off, or one giant packet, is an error and quick', () => {
  const good = bytes([...OGG_HEAD], [...oggPage([0xb8, 1, 2], [3])]);
  assert.deepEqual(oggPackets(good), [Uint8Array.of(0xb8, 1, 2)]);
  for (let cut = 0; cut < good.length; cut++) {
    try {
      assert.deepEqual(oggPackets(good.subarray(0, cut)), []);
    } catch (e) {
      assert.match(e.message, /Ogg/);
    }
  }
  // 20 MB that claims to be a single packet: refused at 61440 bytes
  const page = oggPage(new Uint8Array(255 * 255), new Uint8Array(255).fill(255));
  const giant = new Uint8Array(OGG_HEAD.length + 320 * page.length);
  giant.set(OGG_HEAD);
  for (let i = 0; i < 320; i++) giant.set(page, OGG_HEAD.length + i * page.length);
  const started = Date.now();
  assert.throws(() => oggPackets(giant), /61440/);
  assert.ok(Date.now() - started < 5000);
  // the second header packet (OpusTags) may be bigger than a sound packet: it can hold a picture
  const tags = bytes(text('OpusTags'), zeros(65025 - 8));
  const withPicture = bytes(
    [...oggPage(bytes(text('OpusHead'), zeros(11)), [19])],
    [...oggPage(tags, new Uint8Array(255).fill(255))], [...oggPage(zeros(34975), [...new Array(137).fill(255), 40])],
    [...oggPage([0xb8, 9], [2])],
  );
  assert.deepEqual(oggPackets(withPicture), [Uint8Array.of(0xb8, 9)]);
});

// ---------------------------------------------------------------- a band that sends hostile frames

/** A pretend band whose frames a test can change on their way to the library. */
function rewriting(options = {}) {
  const sim = new SimTransport(42, { realtime: false, ...options });
  let toLibrary = () => {};
  sim.rewrite = (frame) => frame;
  Object.defineProperty(sim, 'onFrame', {
    get: () => (frame) => toLibrary(sim.rewrite(Uint8Array.from(frame))),
    set: (fn) => { toLibrary = fn; },
  });
  return sim;
}

/** Rewrite GET_INFO answers so that the band reports firmware x.y.<patch>. */
const withPatch = (patch) => (frame) => {
  if (frame[0] === 0x02 && frame[2] === codec.OPCODES.GET_INFO && frame[3] === 0) frame[8] = patch;
  return frame;
};

test('hostile band: bad frames do not stop a connected program', async () => {
  const sim = rewriting();
  const band = await Band.connect(42, { transport: sim });
  const seen = [];
  band.on('motion', (m) => seen.push(m));
  band.on('audio', (a) => seen.push(a));
  const random = randomSource(5);
  const hostile = [
    bytes(0x10, u32(1), 10, 255),
    bytes(0x18, u32(1), u32(0xffffffff), u32(0xffffffff), 3, 255),
    bytes(0x13, 255, 255, 255),
    new Uint8Array(100000).fill(0x13),
    bytes(0x02, 0, 0x01, 0),                                          // a GET_INFO answer with no data
    bytes(0x02), bytes(0x03), new Uint8Array(244).fill(0xee), new Uint8Array(0),
  ];
  for (let i = 0; i < 3000; i++) hostile.push(Uint8Array.from({ length: 1 + random(244) }, () => random(256)));
  for (const frame of hostile) band._receive(frame);
  assert.ok(seen.length <= 3000 * 40);
  // sample numbers and times at the very end of their range: arithmetic only, no loop
  band._receive(bytes(0x18, u32(0xffffffff), u32(0xffffffff), u32(0xffffffff), 1, 2, zeros(12)));
  assert.deepEqual(seen.slice(-2).map((m) => m.n), [0xffffffff, 0]);
  assert.equal((await band.getInfo()).number, 42);                    // and it still answers
  await band.setLed('#00ff00');
  await band.disconnect();
});

test('hostile band: a band number that cannot exist', async () => {
  const sim = rewriting();
  sim.rewrite = (f) => {
    if (f[0] === 2 && f[2] === 1) f[10] = f[11] = 0xff;
    return f;
  };
  await assert.rejects(Band.connect(42, { transport: sim }), { code: 'wrong-band' });
  const band = await Band.connect(null, { transport: sim });
  assert.equal(band.number, 65535);
  await band.disconnect();
});

test('firmware 1.1.x is firmware 1.1: the patch number changes no feature', async () => {
  for (const patch of [0, 1, 2, 3, 9, 255]) {
    const sim = rewriting();
    sim.rewrite = withPatch(patch);
    const band = await Band.connect(42, { transport: sim });
    assert.equal(band.info.firmware, `1.1.${patch}`);
    assert.equal(band.info.protocol, '1.1');
    assert.deepEqual(band.info.features, ['fast-motion', 'intervals', 'clock']);
    assert.equal(band.clock, 'band');
    await band.configure({ motion: 416, sensors: 'accel', battery: 60 });      // 1.1 features work
    assert.equal((await band.getInterval('battery')).seconds, 60);
    await band.disconnect();
  }
  const sim = rewriting({ firmware: '1.0' });
  sim.rewrite = withPatch(7);                                          // 1.0.7: still 1.0
  const band = await Band.connect(42, { transport: sim });
  assert.equal(band.info.firmware, '1.0.7');
  assert.deepEqual(band.info.features, []);
  await assert.rejects(band.configure({ motion: 416, sensors: 'accel' }), { code: 'not-available' });
  await band.disconnect();
});

// ---------------------------------------------------------------- the USB cable: what the firmware relies on

const INFO = { proto_major: 1, proto_minor: 0, fw_major: 1, fw_minor: 0, fw_patch: 0, hw: 1, band_no: 42, caps: 0x7ff, max_frame: 244, uptime_ms: 5 };

/** A pretend navigator.serial with one port. It counts how often the port is read. */
function fakeSerial(t) {
  const fake = { log: [], written: [], reads: 0, readWaiting: false, answers: true, queue: [] };
  let deliver = null;
  let finish = null;
  const reader = {
    read: () => new Promise((resolve) => {
      fake.reads++;
      if (fake.queue.length) return resolve({ value: fake.queue.shift(), done: false });
      fake.readWaiting = true;
      deliver = (value) => { deliver = null; fake.readWaiting = false; resolve({ value, done: false }); };
      finish = () => resolve({ value: undefined, done: true });
    }),
    cancel: async () => finish?.(),
    releaseLock: () => {},
  };
  fake.fromBand = (data) => (deliver ? deliver(data) : fake.queue.push(data));
  const bandSide = new codec.Deframer();           // like the band: drops everything up to the first 00
  const writer = {
    write: async (data) => {
      fake.written.push(Uint8Array.from(data));
      for (const frame of bandSide.push(data)) {
        const cmd = codec.decodeCommand(frame);
        if (fake.answers) fake.fromBand(codec.frameForUsb(codec.encodeResponse(cmd.seq, cmd.opcode, 'OK', cmd.opcode === 'GET_INFO' ? INFO : {})));
      }
    },
    releaseLock: () => {},
  };
  const port = {
    open: async (options) => { fake.log.push(['open', options]); },
    setSignals: async (signals) => { fake.log.push(['setSignals', signals]); },
    close: async () => { fake.log.push(['close']); },
    getInfo: () => ({ usbVendorId: 0x1234, usbProductId: 1 }),
    readable: { getReader: () => reader },
    writable: { getWriter: () => writer },
  };
  const before = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  Object.defineProperty(globalThis, 'navigator', {
    value: { serial: { requestPort: async () => port, getPorts: async () => [port] } }, configurable: true, writable: true,
  });
  t.after(() => (before ? Object.defineProperty(globalThis, 'navigator', before) : delete globalThis.navigator));
  return fake;
}

test('USB: the first byte of every session is 00, and the port is opened with room to read ahead', async (t) => {
  const fake = fakeSerial(t);
  const transport = new WebSerialTransport();
  const band = await Band.connect(42, { transport });
  assert.deepEqual([...fake.written[0]], [0]);                         // before the first command
  assert.equal(codec.decodeCommand(codec.cobsDecode(fake.written[1].subarray(0, -1))).opcode, 'GET_INFO');
  assert.equal(fake.written.slice(1).filter((w) => w.length === 1 && w[0] === 0).length, 0);    // and only once
  // Room for a hiccup, but small enough that the band sees a stuck page (the reasoning is in web-serial.js).
  assert.equal(fake.log[0][1].bufferSize, 8192, 'the browser keeps taking bytes while the page is busy for a moment');
  assert.deepEqual(fake.log[1], ['setSignals', { dataTerminalReady: true }]);
  assert.equal(fake.log.filter(([what]) => what === 'setSignals').length, 1, 'DTR is written once per session');
  assert.ok(!('flowControl' in fake.log[0][1]) || fake.log[0][1].flowControl === 'none', 'no RTS/CTS flow control');
  const sent = fake.written.length;
  await transport.reopen();                                            // a reconnect opens the port again
  assert.deepEqual([...fake.written[sent]], [0]);
  await band.setLed('#000100');
  await band.disconnect();
  // Per session: DTR up right after the open and before the first byte, DTR down at the
  // close, and no control line in between or ever RTS (contract 2.2, firmware 1.1.3).
  const signals = fake.log.filter(([what]) => what === 'setSignals').map(([, s]) => s);
  assert.deepEqual(signals, [
    { dataTerminalReady: true }, { dataTerminalReady: false }, { dataTerminalReady: true }, { dataTerminalReady: false },
  ]);
});

test('USB: it reads while a command waits for its answer, and while a handler is still working', async (t) => {
  const fake = fakeSerial(t);
  const band = await Band.connect(42, { transport: 'serial' });
  band.timeoutMs = 300;
  const heard = [];
  let release;
  band.on('battery', async (b) => {
    heard.push(b);
    await new Promise((r) => { release = r; });                        // a handler that never comes back by itself
  });
  fake.answers = false;                                                // the band goes quiet on commands ...
  const waiting = band.getLed();
  waiting.catch(() => {});
  await pause(20);
  assert.equal(fake.readWaiting, true, 'a read is open while the command waits');
  const before = fake.reads;
  for (let i = 0; i < 20; i++) {                                       // ... but keeps streaming
    fake.fromBand(codec.frameForUsb(BATTERY));
    await pause(1);
    assert.equal(fake.readWaiting, true, 'the next read is open at once');
    assert.equal(fake.queue.length, 0, 'nothing is left on the port');
  }
  assert.equal(fake.reads, before + 20);
  assert.equal(heard.length, 20);                                      // delivered although the first handler never finished
  await assert.rejects(waiting, { code: 'timeout' });
  assert.equal(fake.readWaiting, true, 'and it still reads after the timeout');
  release();
  fake.answers = true;
  assert.equal((await band.getInfo()).number, 42);
  await band.disconnect();
});

test('USB: bytes without end are read and forgotten', async (t) => {
  const fake = fakeSerial(t);
  const band = await Band.connect(42, { transport: 'serial' });
  for (let i = 0; i < 300; i++) fake.fromBand(new Uint8Array(1000).fill(0x7f));    // never a 00
  await pause(20);
  assert.equal(fake.queue.length, 0);
  fake.fromBand(Uint8Array.of(0));
  assert.equal((await band.getInfo()).number, 42);
  await band.disconnect();
});
