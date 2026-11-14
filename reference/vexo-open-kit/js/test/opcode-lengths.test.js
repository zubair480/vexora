// Every command of the contract has a length rule for its response, in the place where
// responses are matched (Band), so the rule holds over USB, Bluetooth and the pretend band.
// The list of commands and their lengths is read from contract/contract.md itself: a new
// command in the contract with no length rule in the library fails here.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { Band, Transport, codec } from '../src/index.js';
import { vectors, unhex, withBytes, likeVector } from './helpers.js';

const contract = readFileSync(new URL('../../contract/contract.md', import.meta.url), 'utf8');
const TYPE_SIZES = { u8: 1, i8: 1, u16: 2, i16: 2, u32: 4, i32: 4, u64: 8 };
const typeBytes = (text) => (text.match(/\b[ui](8|16|32|64)\b/g) ?? []).reduce((sum, type) => sum + TYPE_SIZES[type], 0);
const cells = (line) => line.split('|').slice(1, -1).map((cell) => cell.trim());

/** The opcode table of the contract: name -> { code, size: [fewest, most] data bytes of an OK response }. */
function contractTable() {
  const table = contract.slice(contract.indexOf('### Opcode table'), contract.indexOf('## 4. Streams'));
  const battery = cells(contract.split('\n').find((line) => /^\| 4 \| `0x14` \| BATTERY \|/.test(line)))[3];
  const name12 = contract.match(/`GET_NAME` has (\d+)\s+or (\d+) bytes/);
  const store = contract.match(/`STORE_GET` (\d+) to (\d+)/);
  const out = {};
  for (const line of table.split('\n').filter((row) => /^\| `0x[0-9a-f]{2}` \|/i.test(row))) {
    const [code, name, , data] = cells(line);
    if (!/^[A-Z][A-Z0-9_]+$/.test(name)) continue;            // a row that is not a command of the contract
    const said = data.match(/\((\d+) bytes\)/) ?? data.match(/^the same (\d+) bytes/);
    const counted = typeBytes(data);
    let size;
    if (data === 'none') size = [0, 0];
    else if (said) {
      if (counted) assert.equal(counted, Number(said[1]), `${name}: the fields add up to the bytes the contract says`);
      size = [Number(said[1]), Number(said[1])];
    } else if (counted) size = [counted, counted];
    else if (/^BATTERY body/.test(data)) size = [typeBytes(battery), typeBytes(battery)];
    else if (/^ASCII name/.test(data)) size = [Number(name12[1]), Number(name12[2])];
    else if (data === 'value bytes') size = [Number(store[1]), Number(store[2])];
    else assert.fail(`${name}: this test does not know how long "${data}" is. Add a length rule for it.`);
    out[name] = { code: Number(code.replaceAll('`', '')), size };
  }
  return out;
}

const TABLE = contractTable();
const response = (seq, opcode, status, data = []) => Uint8Array.from([0x02, seq, opcode, status, ...data]);
const zeros = (n) => new Array(n).fill(0);

test('the contract table, the codec table and the test vectors list the same commands', () => {
  const fromContract = Object.fromEntries(Object.entries(TABLE).map(([name, row]) => [name, row.code]));
  assert.ok(Object.keys(TABLE).length >= 32);
  assert.deepEqual(fromContract, { ...codec.OPCODES });
  assert.deepEqual(fromContract, vectors.opcodes);
  assert.deepEqual(TABLE.GET_INFO.size, [16, 16]);
  assert.deepEqual(TABLE.GET_NAME.size, [12, 13]);
  assert.deepEqual(TABLE.STORE_GET.size, [1, 232]);
  assert.deepEqual(TABLE.BATTERY_GET.size, [4, 4]);
  assert.deepEqual(TABLE.LED_SET.size, [0, 0]);
});

test('codec: for every command of the contract, only the contract length decodes (every length 0..240)', () => {
  for (const [name, { code, size: [fewest, most] }] of Object.entries(TABLE)) {
    const expected = fewest === most ? String(fewest) : `${fewest} to ${most}`;
    for (let n = 0; n <= 240; n++) {
      const frame = response(5, code, 0, zeros(n));
      const right = n >= fewest && n <= most;
      assert.equal(codec.decodeFrame(frame)?.opcode ?? null, right ? name : null, `${name} OK with ${n} data bytes`);
      assert.deepEqual(codec.wrongLengthResponse(frame), right ? null : { seq: 5, opcode: name, got: n, expected });
    }
    // Any other status carries no data.
    for (let status = 1; status <= 12; status++) {
      assert.equal(codec.decodeFrame(response(5, code, status)).opcode, name);
      for (const n of [1, 2, fewest, most, 240].filter((x) => x > 0)) {
        const frame = response(5, code, status, zeros(n));
        assert.equal(codec.decodeFrame(frame), null, `${name} status ${status} with ${n} data bytes`);
        assert.deepEqual(codec.wrongLengthResponse(frame), { seq: 5, opcode: name, got: n, expected: '0' });
      }
    }
    for (let cut = 0; cut < 4; cut++) assert.equal(codec.decodeFrame(response(5, code, 0).subarray(0, cut)), null);   // header cut
  }
});

const INFO = { proto_major: 1, proto_minor: 1, fw_major: 1, fw_minor: 1, fw_patch: 3, hw: 1, band_no: 42, caps: 0x7ff, max_frame: 244, uptime_ms: 5000 };
const CLOCK = { set: 1, unix_ms: 1794470400000, t_ms: 5000 };

/**
 * A band that answers as the test says. kind 'ble': each frame arrives as one notification.
 * kind 'serial': the frames travel as USB bytes (COBS, 00) and come back through the Deframer.
 */
class Double extends Transport {
  constructor(kind) {
    super();
    this.kind = kind;
    this.reply = null;
    this.deframer = new codec.Deframer({ skipFirst: false });
  }

  async open() {}

  deliver(frames) {
    for (const frame of frames) {
      if (this.kind !== 'serial') this.onFrame(frame);
      else for (const got of this.deframer.push(codec.frameForUsb(frame))) this.onFrame(got);
    }
  }

  async send(frame) {
    const cmd = codec.decodeCommand(frame);
    const connecting = { GET_INFO: INFO, TIME_GET: CLOCK, TIME_SET: CLOCK }[cmd.opcode];
    this.deliver(this.reply ? this.reply(cmd) : [codec.encodeResponse(cmd.seq, cmd.opcode, 'OK', connecting)]);
  }
}

for (const kind of ['serial', 'ble']) {
  test(`${kind}: for every command, wrong-length responses are ignored and the whole one after them is used`, async () => {
    for (const [name, { code, size: [fewest, most] }] of Object.entries(TABLE)) {
      const command = vectors.commands.find((c) => c.name === name);
      const answer = vectors.responses.find((r) => r.value.opcode === name && r.value.status === 'OK');
      assert.ok(command && answer, `${name}: the test vectors have a command and an OK response`);
      const transport = new Double(kind);
      const band = await Band.connect(42, { transport, timeoutMs: 1000 });
      let wrongSent = 0;
      transport.reply = (cmd) => {
        assert.equal(cmd.opcode, name);
        const whole = unhex(answer.hex);
        whole[1] = cmd.seq;
        const data = [...whole.subarray(4)];
        const wrong = [];
        for (let cut = 1; cut < 4; cut++) wrong.push(whole.slice(0, cut));                                 // header cut
        for (let n = 0; n < data.length; n++) if (n < fewest) wrong.push(response(cmd.seq, code, 0, data.slice(0, n)));   // too short
        wrong.push(response(cmd.seq, code, 0, [...data, ...zeros(most + 1 - data.length)]));              // too long
        wrong.push(response(cmd.seq, code, 6, [1]));                                                       // not OK, with data
        wrong.push(response(cmd.seq, 0x7f, 0, data));                                                      // a command nobody sent
        wrongSent = wrong.length;
        return [...wrong, whole];
      };
      const got = await band.command(name, withBytes(command.args));
      assert.deepEqual(likeVector({ frame: 'response', data: got }).data, answer.value.data, name);
      assert.ok(wrongSent >= 6, name);

      // Only wrong lengths: not used and not failed at once. The wait ends at the usual timeout.
      band.timeoutMs = 25;
      for (const wrong of [zeros(most + 1), ...(fewest > 0 ? [zeros(fewest - 1)] : [])]) {
        transport.reply = (cmd) => [response(cmd.seq, code, 0, wrong)];
        const started = Date.now();
        await assert.rejects(band.command(name, withBytes(command.args)), (e) => {
          assert.equal(e.code, 'timeout', name);
          assert.match(e.message, new RegExp(`answered ${name} with ${wrong.length} bytes of data`));
          return true;
        });
        assert.ok(Date.now() - started >= 20, `${name}: waited until the timeout`);
      }
      assert.equal(band.connected, true);
    }
  });
}

// ---------------------------------------------------------------- streams and notices

const EVENTS = ['motion', 'event', 'tap', 'double-tap', 'free-fall', 'tilt', 'orientation', 'motion-start', 'motion-stop',
  'activity', 'audio', 'battery', 'temperature', 'link', 'mic-level', 'notice'];

test('codec: a stream frame or notice that is cut short never decodes', () => {
  for (const { hex, value } of [...vectors.streams, ...vectors.notices]) {
    if (value.frame === 'unknown') continue;                  // a frame type this library does not know: it has no layout
    const whole = unhex(hex);
    assert.ok(codec.decodeFrame(whole), hex);
    for (let cut = 0; cut < whole.length; cut++) assert.equal(codec.decodeFrame(whole.subarray(0, cut)), null, `${hex} cut to ${cut} bytes`);
  }
});

for (const kind of ['serial', 'ble']) {
  test(`${kind}: a cut stream frame or notice gives no event and no error; the whole one gives its event`, async () => {
    const transport = new Double(kind);
    const band = await Band.connect(42, { transport });
    let events = 0;
    for (const type of EVENTS) band.on(type, () => { events++; });
    const seen = new Set();
    for (const { hex, value } of [...vectors.streams, ...vectors.notices]) {
      const whole = unhex(hex);
      for (let cut = 1; cut < whole.length; cut++) transport.deliver([whole.slice(0, cut)]);
      assert.equal(events, 0, `${hex}: a cut ${value.frame} frame gave an event`);
      transport.deliver([whole]);
      if (events > 0) seen.add(value.frame);
      events = 0;
    }
    assert.deepEqual([...seen].sort(), [...Object.keys(codec.STREAM_BITS), 'notice'].sort());
    assert.equal(band.connected, true);
  });
}
