// Every response is checked against the length the contract gives for its command.
// Why: over USB the start of a response can arrive cut off, then 00, then the whole
// response again with the same seq (contract 2.2, firmware 1.1.3), and the cut piece can
// decode as a frame. A response of the wrong length is never used: the library keeps
// waiting for the right one until the usual timeout.
import test from 'node:test';
import assert from 'node:assert/strict';
import { Band, Transport, codec } from '../src/index.js';

const { OPCODES } = codec;
const zeros = (n) => new Array(n).fill(0);
const response = (seq, opcode, status, data = []) => Uint8Array.from([0x02, seq, opcode, status, ...data]);

// Data bytes of an OK response, counted from the opcode table of contract/contract.md.
const SIZES = {
  GET_INFO: 16, GET_NAME: [12, 13], SET_BAND_NUMBER: 2, IDENTIFY: 0, RESTART: 0, SLEEP: 0, TIME_SET: 13, TIME_GET: 13,
  STREAM_START: 2, STREAM_STOP: 2, STREAM_GET: 2, STREAM_SET_INTERVAL: 13, STREAM_GET_INTERVAL: 13,
  IMU_SET: 4, IMU_GET: 4, EVENT_SET: 3, EVENT_GET: 3, MOTION2_SET: 9, MOTION2_GET: 9,
  MIC_SET_GAIN: 1, MIC_GET: 7, RADIO_SET_TX_POWER: 1, RADIO_GET: 7, LED_SET: 0, LED_GET: 7,
  TEMP_SET_INTERVAL: 1, BATTERY_GET: 4, STORE_INFO: 10, STORE_PUT: 0, STORE_GET: [1, 232], STORE_DEL: 0, STORE_WIPE: 0,
};
const INFO = { proto_major: 1, proto_minor: 1, fw_major: 1, fw_minor: 1, fw_patch: 3, hw: 1, band_no: 42, caps: 0x7ff, max_frame: 244, uptime_ms: 5000 };
const LED = { r: 0, g: 255, b: 0, brightness: 128, pattern: 1, period_ms: 1000 };

// ---------------------------------------------------------------- the codec

test('response length: every command has its length, and only that length decodes', () => {
  assert.deepEqual(Object.keys(SIZES).sort(), Object.keys(OPCODES).sort(), 'a length for every command the library knows');
  for (const [name, size] of Object.entries(SIZES)) {
    const [fewest, most] = Array.isArray(size) ? size : [size, size];
    const expected = fewest === most ? String(fewest) : `${fewest} to ${most}`;
    for (const n of [fewest, most]) {
      const frame = response(7, OPCODES[name], 0, zeros(n));
      assert.equal(codec.decodeFrame(frame)?.opcode, name, `${name} with ${n} data bytes`);
      assert.equal(codec.wrongLengthResponse(frame), null);
    }
    for (const n of [fewest - 1, most + 1, 0, 1, 240].filter((x) => x >= 0 && (x < fewest || x > most))) {
      const frame = response(7, OPCODES[name], 0, zeros(n));
      assert.equal(codec.decodeFrame(frame), null, `${name} with ${n} data bytes`);
      assert.deepEqual(codec.wrongLengthResponse(frame), { seq: 7, opcode: name, got: n, expected });
    }
  }
});

test('response length: a status other than OK passes with no data, for every command', () => {
  for (const name of Object.keys(SIZES)) {
    codec.STATUSES.forEach((status, code) => {
      if (!code) return;
      const frame = response(9, OPCODES[name], code);
      assert.deepEqual(codec.decodeFrame(frame), { frame: 'response', seq: 9, opcode: name, status, data: {} });
      assert.equal(codec.wrongLengthResponse(frame), null);
    });
    assert.equal(codec.decodeFrame(response(9, OPCODES[name], 77)).status, 77);         // a status this library does not know
  }
});

test('response length: a command the library has no line for passes through untouched', () => {
  for (const opcode of [0x09, 0x0a, 0x7f, 0xff]) {
    for (const n of [0, 1, 12, 100, 240]) {
      for (const status of [0, 1]) {
        const frame = response(3, opcode, status, zeros(n));
        assert.deepEqual(codec.decodeFrame(frame), { frame: 'response', seq: 3, opcode, status: codec.STATUSES[status], data: {} });
        assert.equal(codec.wrongLengthResponse(frame), null);
      }
    }
  }
});

test('response length: stream frames and notices are not touched by the check', () => {
  assert.equal(codec.wrongLengthResponse(codec.encodeStream('battery', { percent: 50, state: 0, millivolts: 3900 })), null);
  assert.equal(codec.wrongLengthResponse(codec.encodeNotice('USB_POWER', 1)), null);
  for (const wrong of [null, 7, 'text', new Uint8Array(0), new Uint8Array(3), new Uint8Array(500)]) {
    assert.equal(codec.wrongLengthResponse(wrong), null);
  }
});

// ---------------------------------------------------------------- the band

/** A band that answers by itself while the library connects, and afterwards as the test says. */
class Scripted extends Transport {
  kind = 'serial';
  reply = null;                 // (command) -> frames to hand to the library
  async open() {}
  async send(frame) {
    const cmd = codec.decodeCommand(frame);
    const clock = { set: 1, unix_ms: 1794470400000, t_ms: 5000 };
    const connecting = { GET_INFO: INFO, TIME_GET: clock, TIME_SET: clock }[cmd.opcode];
    const frames = this.reply ? this.reply(cmd) : [codec.encodeResponse(cmd.seq, cmd.opcode, 'OK', connecting)];
    for (const f of frames) this.onFrame(f);
  }
}

/**
 * What a client really receives when the band sends the first `cut` bytes of a response,
 * then 00, then the whole response: the frames the USB framing lets through.
 */
function cutThenWhole(frame, cut) {
  const wire = codec.frameForUsb(frame);
  return new codec.Deframer({ skipFirst: false }).push(Uint8Array.from([...wire.subarray(0, cut), 0, ...wire]));
}

/** Every way to cut a response short, as frames: [the cut pieces that look like frames, the whole one]. */
function everyCut(frame) {
  const pieces = [];
  for (let cut = 1; cut < frame.length + 1; cut++) pieces.push(...cutThenWhole(frame, cut).slice(0, -1));
  return [pieces, frame];
}

const CASES = [
  ['GET_INFO', {}, INFO],
  ['GET_INFO', {}, { ...INFO, proto_minor: 0, fw_minor: 0, fw_patch: 0, band_no: 7, uptime_ms: 0 }],     // firmware 1.0: more 00 bytes
  ['LED_GET', {}, { ...LED, r: 0, g: 0, b: 0 }],
  ['TIME_GET', {}, { set: 1, unix_ms: 1794470400000, t_ms: 256 }],
  ['STORE_INFO', {}, { keys_max: 1000, value_max: 232, free_bytes: 242164, used_keys: 0 }],
  ['RADIO_GET', {}, { tx_dbm: -8, rssi_dbm: 127, phy: 0, interval: 0, mtu: 0 }],
  ['MOTION2_GET', {}, { rate_hz: 25, sensors: 3, max_accel_hz: 1666, max_gyro_hz: 833, max_both_hz: 833 }],
  ['STREAM_GET', {}, { active: 0 }],
  ['GET_NAME', {}, { name: 'Vexo-HACK-42' }],
];

test('cut, then whole: the cut piece is never the answer, the whole response is', async () => {
  for (const [name, args, data] of CASES) {
    const transport = new Scripted();
    const band = await Band.connect(42, { transport });
    let looksValid = 0;
    transport.reply = (cmd) => {
      const [pieces, whole] = everyCut(codec.encodeResponse(cmd.seq, name, 'OK', data));
      looksValid += pieces.length;
      return [...pieces, whole];
    };
    assert.deepEqual(await band.command(name, args), data, name);
    if (name !== 'GET_NAME') assert.ok(looksValid > 0, `${name}: at least one cut piece gets through the USB framing as a frame`);
  }
});

test('cut, then whole: a stored value that is cut down to nothing is not taken for an empty value', async () => {
  const transport = new Scripted();
  const band = await Band.connect(42, { transport });
  const value = Uint8Array.from([0, 1, 2, 3, ...zeros(60), 9]);          // starts with 00: the cut can fall right after the header
  let pieces = [];
  transport.reply = (cmd) => {
    const whole = codec.encodeResponse(cmd.seq, 'STORE_GET', 'OK', { value });
    pieces = cutThenWhole(whole, 5).slice(0, -1);                          // the band sent 5 bytes, then 00
    return [...pieces, whole];
  };
  assert.deepEqual(await band.storage.get(5), value);
  assert.equal(pieces.length, 1);
  assert.deepEqual([...pieces[0].subarray(2)], [OPCODES.STORE_GET, 0], 'the cut piece is a whole-looking frame: OK, no value');
});

test('known limit: a cut stored value that keeps one byte or more has an allowed length', () => {
  // The length check cannot catch this one (any 1..232 bytes are a value). Only the band
  // can: it must not let a cut piece end in 00.
  const whole = codec.encodeResponse(1, 'STORE_GET', 'OK', { value: Uint8Array.from([5, 6, 0, 7]) });
  const [cut] = cutThenWhole(whole, 7);
  assert.deepEqual([...codec.decodeFrame(cut).data.value], [5, 6]);
});

test('wrong length and no right answer: timeout that says so, and the next command works', async () => {
  const transport = new Scripted();
  const band = await Band.connect(42, { transport, timeoutMs: 30 });
  for (const [name, size, n] of [['LED_GET', '7', 8], ['LED_GET', '7', 6], ['LED_SET', '0', 1], ['GET_INFO', '16', 15],
    ['GET_INFO', '16', 17], ['STORE_GET', '1 to 232', 0], ['STORE_GET', '1 to 232', 233], ['GET_NAME', '12 to 13', 8]]) {
    transport.reply = (cmd) => [response(cmd.seq, OPCODES[name], 0, zeros(n))];
    const args = name === 'LED_SET' ? LED : name === 'STORE_GET' ? { key: 1 } : {};
    await assert.rejects(band.command(name, args), (e) => {
      assert.equal(e.code, 'timeout', name);
      assert.match(e.message, new RegExp(`answered ${name} with ${n} bytes of data where the contract has ${size},`));
      return true;
    });
  }
  transport.reply = () => [];                                              // really silent: the usual sentence
  await assert.rejects(band.command('LED_GET'), (e) => e.code === 'timeout' && /did not answer LED_GET/.test(e.message));
  transport.reply = (cmd) => [codec.encodeResponse(cmd.seq, 'LED_GET', 'OK', LED)];
  assert.deepEqual(await band.command('LED_GET'), LED);
  assert.equal(band.connected, true);
});

test('a wrong length for another seq or another command does not change the message', async () => {
  const transport = new Scripted();
  const band = await Band.connect(42, { transport, timeoutMs: 30 });
  transport.reply = (cmd) => [response(cmd.seq + 1, OPCODES.LED_GET, 0, zeros(3)), response(cmd.seq, OPCODES.IMU_GET, 0, zeros(3))];
  await assert.rejects(band.command('LED_GET'), (e) => e.code === 'timeout' && /did not answer LED_GET/.test(e.message));
});

test('a status other than OK still fails at once, and a frame for an unknown command is left alone', async () => {
  const transport = new Scripted();
  const band = await Band.connect(42, { transport });
  transport.reply = (cmd) => [response(cmd.seq, 0x09, 0, zeros(12)), codec.encodeResponse(cmd.seq, cmd.opcode, 'DENIED')];
  await assert.rejects(band.command('LED_SET', LED), { code: 'denied' });
  transport.reply = (cmd) => [codec.encodeResponse(cmd.seq, cmd.opcode, 'NOT_FOUND')];
  assert.equal(await band.storage.get(5), null);
  transport.reply = (cmd) => [response(cmd.seq, OPCODES.LED_GET, 77)];
  await assert.rejects(band.command('LED_GET'), { code: 'unknown-status' });
});
