// The codec against the shared vectors in contract/test-vectors.json.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as index from '../src/index.js';
import {
  NAME_PREFIX, bandId, bandNumberOf, parseBandNumber, SERVICE_UUID, RX_UUID, TX_UUID, OPCODES, STATUSES, STREAM_BITS, CONTRACT_REVISION, MAGIC, MAX_FRAME,
  EVENT_KINDS, LED_PATTERNS, CAPABILITIES, NOTICE_KINDS,
  bandName, cobsEncode, cobsDecode, frameForUsb, Deframer,
  encodeCommand, decodeFrame, decodeCommand, encodeResponse, encodeStream, encodeNotice,
} from '../src/codec.js';
import { hex, unhex, vectors, withBytes, likeVector } from './helpers.js';

test('constants match the vectors', () => {
  assert.equal(NAME_PREFIX, vectors.name_prefix);
  assert.equal(CONTRACT_REVISION, vectors.contract_revision);
  assert.deepEqual({ service: SERVICE_UUID, rx: RX_UUID, tx: TX_UUID }, vectors.uuids);
  assert.deepEqual({ ...OPCODES }, vectors.opcodes);
  assert.deepEqual([...STATUSES], vectors.statuses);
  assert.deepEqual({ ...STREAM_BITS }, vectors.stream_bits);
  assert.equal(MAGIC, 0x5856);
  assert.equal(MAX_FRAME, 244);
  assert.deepEqual([...LED_PATTERNS], ['off', 'solid', 'blink', 'breathe', 'flash', 'rainbow']);
  assert.deepEqual({ ...EVENT_KINDS }, { 1: 'tap', 2: 'double-tap', 3: 'free-fall', 4: 'tilt', 5: 'orientation', 6: 'motion-start', 7: 'motion-stop' });
  assert.equal(CAPABILITIES.join(' '), 'accel gyro events steps mic led battery temperature storage tx-power usb');
  assert.deepEqual(Object.values(NOTICE_KINDS), ['LOW_BATTERY', 'CRITICAL_BATTERY', 'STREAM_DROPPED', 'USB_POWER']);
});

test('names', () => {
  for (const v of vectors.names) {
    assert.equal(bandName(v.number), v.name);
    assert.ok(v.name.startsWith(NAME_PREFIX));
  }
  for (const bad of [-1, 1000, 1.5, NaN, '42', null, undefined]) assert.throws(() => bandName(bad), RangeError);
  assert.equal(NAME_PREFIX, 'Vexo-HACK-');
  // At least two digits; three from 100. Nothing depends on how many bands exist.
  assert.deepEqual([0, 1, 7, 42, 99, 100, 123, 999].map(bandId), ['00', '01', '07', '42', '99', '100', '123', '999']);
  assert.equal(bandName(100), 'Vexo-HACK-100');
  for (const n of [0, 1, 9, 10, 99, 100, 999]) assert.equal(bandNumberOf(bandName(n)), n);
  assert.equal(bandNumberOf('Vexo-HACK-007'), 7);
  for (const bad of ['Vexo-HACK-7', 'Vexo-HACK-1000', 'Vexo-HACK-', 'Vexo-042', 'vexo-hack-07', 'Vexo-HACK-07 ', 'xVexo-HACK-07', null, 7]) {
    assert.equal(bandNumberOf(bad), null, String(bad));
  }
  assert.deepEqual(['7', '07', '007', ' 42 ', '100', '0'].map(parseBandNumber), [7, 7, 7, 42, 100, 0]);
  for (const bad of ['', '1000', '0007', '4x', '-1', '4.2', null, 7]) assert.equal(parseBandNumber(bad), null, String(bad));
});

test('index.js exports', () => {
  const names = ['Band', 'VexoError', 'Transport', 'SimTransport', 'WebBluetoothTransport', 'WebSerialTransport',
    'bandName', 'bandId', 'NAME_PREFIX', 'SERVICE_UUID', 'CONTRACT_REVISION', 'EVENT_KINDS', 'LED_PATTERNS', 'codec',
    'BandAudio', 'canDecodeAudio', 'toInt16', 'oggPackets', 'SeqTracker', 'Resampler', 'AUDIO_RATE', 'AUDIO_FRAME_MS'];
  assert.deepEqual(Object.keys(index).sort(), names.sort());
  assert.equal(index.codec.encodeCommand, encodeCommand);
});

test('cobs, both directions', () => {
  for (const v of vectors.cobs) {
    assert.equal(hex(cobsEncode(unhex(v.decoded))), v.encoded);
    assert.equal(hex(cobsDecode(unhex(v.encoded))), v.decoded);
    assert.ok(!cobsEncode(unhex(v.decoded)).includes(0), 'encoded bytes never contain 00');
  }
  for (const bad of ['', '00', '0501', '050102', '0300aa', '020000']) assert.equal(cobsDecode(unhex(bad)), null, bad);
  assert.equal(hex(frameForUsb(unhex('010001'))), '0201020100');
});

test('usb_stream in chunks of 1, 3, 7 and all at once', () => {
  const bytes = unhex(vectors.usb_stream.bytes);
  for (const size of [1, 3, 7, bytes.length]) {
    const deframer = new Deframer();
    const frames = [];
    for (let i = 0; i < bytes.length; i += size) frames.push(...deframer.push(bytes.subarray(i, i + size)));
    assert.deepEqual(frames.map(hex), vectors.usb_stream.frames, `chunk size ${size}`);
  }
});

test('Deframer: skipFirst false keeps the first packet; oversize packets are dropped', () => {
  const first = frameForUsb(unhex('02000500'));
  assert.deepEqual(new Deframer().push(first), []);
  assert.deepEqual(new Deframer({ skipFirst: false }).push(first).map(hex), ['02000500']);
  const deframer = new Deframer({ skipFirst: false });
  const junk = new Uint8Array(1000).fill(0x11);
  assert.deepEqual(deframer.push(junk), []);
  assert.deepEqual(deframer.push(Uint8Array.of(0, ...first)).map(hex), ['02000500']);
});

test('commands: encode, and decodeCommand inverts it', () => {
  for (const v of vectors.commands) {
    const args = withBytes(v.args);
    const frame = encodeCommand(v.seq, v.name, args);
    assert.equal(hex(frame), v.hex, v.name);
    const back = decodeCommand(frame);
    assert.equal(back.seq, v.seq);
    assert.equal(back.opcode, v.name);
    const { magic, ...rest } = back.args;
    if ('magic' in back.args) assert.equal(magic, MAGIC);
    assert.deepEqual(rest, args, v.name);
  }
  assert.equal(vectors.commands.length >= Object.keys(OPCODES).length, true);
});

test('encodeCommand refuses bad input', () => {
  assert.throws(() => encodeCommand(0, 'NOPE'), RangeError);
  assert.throws(() => encodeCommand(0, 1), RangeError);
  assert.throws(() => encodeCommand(0, 'IDENTIFY'), RangeError);                          // missing
  assert.throws(() => encodeCommand(0, 'IDENTIFY', { seconds: 256 }), RangeError);        // out of range
  assert.throws(() => encodeCommand(0, 'IDENTIFY', { seconds: 1.5 }), RangeError);
  assert.throws(() => encodeCommand(0, 'IDENTIFY', { seconds: 5, extra: 1 }), RangeError); // unknown field
  assert.throws(() => encodeCommand(0, 'RADIO_SET_TX_POWER', { dbm: -129 }), RangeError);
  assert.throws(() => encodeCommand(256, 'GET_INFO'), RangeError);
  assert.throws(() => encodeCommand(0, 'STORE_PUT', { key: 1, value: 'text' }), RangeError);
  assert.throws(() => encodeCommand(0, 'STORE_PUT', { key: 1, value: new Uint8Array(240) }), RangeError); // over MAX_FRAME
});

test('unix_ms is 8 bytes and stays a plain number, also above 2^32', () => {
  const cases = [
    [0, '0000000000000000'],
    [1, '0100000000000000'],
    [2 ** 32 - 1, 'ffffffff00000000'],
    [2 ** 32, '0000000001000000'],
    [1794484800123, '7b0aa1cfa1010000'],              // a date in 2026, about 418 times 2^32
    [4102444799999, 'ffd7c32cbb030000'],              // the last millisecond the band takes (2099)
    [Number.MAX_SAFE_INTEGER, 'ffffffffffff1f00'],
  ];
  for (const [unix_ms, bytes] of cases) {
    const frame = encodeCommand(1, 'TIME_SET', { unix_ms });
    assert.equal(hex(frame), `010107${bytes}`, `${unix_ms}`);
    const back = decodeCommand(frame).args.unix_ms;
    assert.equal(typeof back, 'number');                              // never a BigInt
    assert.equal(back, unix_ms);
    const answer = decodeFrame(encodeResponse(1, 'TIME_GET', 'OK', { set: 1, unix_ms, t_ms: 4294967295 }));
    assert.deepEqual(answer.data, { set: 1, unix_ms, t_ms: 4294967295 });
  }
  for (const bad of [-1, 1.5, NaN, Infinity, 2 ** 53, 2 ** 64, '5', 5n, null, undefined]) {
    assert.throws(() => encodeCommand(1, 'TIME_SET', { unix_ms: bad }), RangeError, String(bad));
  }
});

test('motion2: the samples hold what `sensors` says, and nothing else encodes', () => {
  const head = { t_ms: 1, sample_no: 2, period_us: 3 };
  const accel = { ax: 1, ay: 2, az: 3 };
  const gyro = { gx: 4, gy: 5, gz: 6 };
  assert.equal(encodeStream('motion2', { ...head, sensors: 1, samples: [accel] }).length, 15 + 6);
  assert.equal(encodeStream('motion2', { ...head, sensors: 2, samples: [gyro, gyro] }).length, 15 + 12);
  assert.equal(encodeStream('motion2', { ...head, sensors: 3, samples: [{ ...accel, ...gyro }] }).length, 15 + 12);
  // the biggest frames the contract allows fit exactly
  assert.equal(encodeStream('motion2', { ...head, sensors: 1, samples: Array(38).fill(accel) }).length, 243);
  assert.equal(encodeStream('motion2', { ...head, sensors: 3, samples: Array(19).fill({ ...accel, ...gyro }) }).length, 243);
  assert.throws(() => encodeStream('motion2', { ...head, sensors: 3, samples: Array(20).fill({ ...accel, ...gyro }) }), RangeError);
  for (const sensors of [0, 4, undefined]) assert.throws(() => encodeStream('motion2', { ...head, sensors, samples: [] }), RangeError);
  assert.throws(() => encodeStream('motion2', { ...head, sensors: 1, samples: [{ ...accel, ...gyro }] }), RangeError);   // gyro values in an accel-only frame
  assert.throws(() => encodeStream('motion2', { ...head, sensors: 3, samples: [accel] }), RangeError);
  assert.throws(() => encodeStream('motion2', { ...head, sensors: 2, samples: [accel] }), RangeError);
  // decoding: sensors 4 and up is as unknown as 0
  const frame = encodeStream('motion2', { ...head, sensors: 1, samples: [accel] });
  for (const sensors of [0, 4, 255]) {
    const copy = Uint8Array.from(frame);
    copy[13] = sensors;
    assert.equal(decodeFrame(copy), null);
  }
  assert.deepEqual(decodeFrame(frame).samples, [accel]);
});

test('decodeCommand: bad length, unknown opcode, not a command', () => {
  assert.deepEqual(decodeCommand(unhex('010704')), { seq: 7, opcode: 'IDENTIFY', bad_length: true });
  assert.deepEqual(decodeCommand(unhex('0107040505')), { seq: 7, opcode: 'IDENTIFY', bad_length: true });
  assert.deepEqual(decodeCommand(unhex('01070100')), { seq: 7, opcode: 'GET_INFO', bad_length: true });
  assert.deepEqual(decodeCommand(unhex('010709aabb')), { seq: 7, opcode: 9, args: {} });
  assert.equal(decodeCommand(unhex('0105')), null);
  assert.equal(decodeCommand(unhex('02075000')), null);
  assert.equal(decodeCommand(new Uint8Array(0)), null);
});

test('responses: decode, and encodeResponse rebuilds the hex', () => {
  for (const v of vectors.responses) {
    assert.deepEqual(likeVector(decodeFrame(unhex(v.hex))), v.value, v.hex);
    const { seq, opcode, status, data } = v.value;
    assert.equal(hex(encodeResponse(seq, opcode, status, withBytes(data))), v.hex, v.hex);
  }
});

test('notices: decode, and encodeNotice rebuilds the hex', () => {
  for (const v of vectors.notices) {
    assert.deepEqual(decodeFrame(unhex(v.hex)), v.value);
    assert.equal(hex(encodeNotice(v.value.code, v.value.arg)), v.hex);
  }
});

test('streams: decode, and encodeStream rebuilds the hex', () => {
  for (const v of vectors.streams) {
    const decoded = decodeFrame(unhex(v.hex));
    assert.deepEqual(likeVector(decoded), v.value, v.hex);
    if (v.value.frame === 'unknown') continue;
    assert.equal(hex(encodeStream(v.value.frame, withBytes(v.value))), v.hex, v.hex);
    if (decoded.frame === 'audio') assert.ok(decoded.opus instanceof Uint8Array);
  }
  assert.throws(() => encodeStream('nope', {}), RangeError);
});

test('invalid frames decode to null without throwing', () => {
  for (const v of vectors.invalid) assert.equal(decodeFrame(unhex(v.hex)), null, v.note);
  assert.equal(decodeFrame(new Uint8Array(245).fill(0x7f)), null);
  for (const notBytes of [null, undefined, 'abc', 42, {}, [2, 0, 1, 0]]) assert.equal(decodeFrame(notBytes), null);
});

test('decodeFrame works on a Buffer and on a view into a bigger buffer', () => {
  const v = vectors.responses[0];
  assert.deepEqual(decodeFrame(Buffer.from(v.hex, 'hex')), v.value);
  const big = new Uint8Array(64);
  big.set(unhex(v.hex), 10);
  assert.deepEqual(decodeFrame(big.subarray(10, 10 + v.hex.length / 2)), v.value);
});

test('fuzz: random bytes never make decodeFrame, decodeCommand or the Deframer throw', () => {
  let state = 12345;      // a tiny fixed random generator, so a failure can be repeated
  const random = () => (state = (state * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
  const types = [1, 2, 3, 0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18];
  const deframer = new Deframer();
  for (let i = 0; i < 20000; i++) {
    const bytes = new Uint8Array(Math.floor(random() * 40));
    for (let j = 0; j < bytes.length; j++) bytes[j] = Math.floor(random() * 256);
    if (bytes.length && i % 2) bytes[0] = types[i % types.length];   // aim half of them at real frame types
    const decoded = decodeFrame(bytes);
    assert.ok(decoded === null || typeof decoded.frame === 'string');
    decodeCommand(bytes);
    cobsDecode(bytes);
    for (const frame of deframer.push(bytes)) decodeFrame(frame);
  }
});

test('fuzz: every cut-short or stretched vector still decodes without throwing', () => {
  for (const v of [...vectors.responses, ...vectors.notices, ...vectors.streams]) {
    const bytes = unhex(v.hex);
    for (let n = 0; n <= bytes.length; n++) decodeFrame(bytes.subarray(0, n));
    decodeFrame(Uint8Array.of(...bytes, 1, 2, 3));
  }
});

test('cobs round-trips random frames through the Deframer', () => {
  let state = 99;
  const random = () => (state = (state * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
  const deframer = new Deframer({ skipFirst: false });
  for (let i = 0; i < 500; i++) {
    const frame = new Uint8Array(1 + Math.floor(random() * MAX_FRAME));
    for (let j = 0; j < frame.length; j++) frame[j] = random() < 0.3 ? 0 : Math.floor(random() * 256);
    assert.deepEqual(deframer.push(frameForUsb(frame)).map(hex), [hex(frame)]);
  }
});
