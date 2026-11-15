// A faulty or pretend band must not crash the program with its answers: a clock far in
// the future, a `sensors` value that does not exist, a sound counter that jumps with every
// packet. The same tests as python/tests/test_faulty_band.py.
import test from 'node:test';
import assert from 'node:assert/strict';
import { Band, Transport, VexoError, SeqTracker, codec } from '../src/index.js';
import { MAX_FILL } from '../src/audio.js';

const INFO = { proto_major: 1, proto_minor: 1, fw_major: 1, fw_minor: 1, fw_patch: 3, hw: 1, band_no: 42, caps: 0x7ff, max_frame: 244, uptime_ms: 5000 };
const GOOD_CLOCK = { set: 1, unix_ms: 1794470400000, t_ms: 5000 };
const MOTION2 = { rate_hz: 25, sensors: 3, max_accel_hz: 833, max_gyro_hz: 833, max_both_hz: 416 };
const NOW = 1794484800000;
// Outside 2020 .. 2100. The first one is the largest whole number JavaScript keeps exact; the
// raw bytes below are the largest a band can send.
const BAD_TIMES = [Number.MAX_SAFE_INTEGER, 4102444800000, 1577836799999, 1, 0];

/** A band that answers every command with the data the test gives for it (or raw bytes). */
class Faulty extends Transport {
  kind = 'ble';

  constructor(answers = {}) {
    super();
    this.answers = { GET_INFO: INFO, TIME_GET: GOOD_CLOCK, TIME_SET: GOOD_CLOCK, ...answers };
  }

  async open() {}

  async send(frame) {
    const cmd = codec.decodeCommand(frame);
    const answer = this.answers[cmd.opcode];
    if (answer instanceof Uint8Array) this.onFrame(Uint8Array.from([0x02, cmd.seq, codec.OPCODES[cmd.opcode], 0, ...answer]));
    else if (answer) this.onFrame(codec.encodeResponse(cmd.seq, cmd.opcode, 'OK', answer));
  }
}

test('a clock outside 2020 to 2100 is treated as not set: no Invalid Date', async () => {
  const allOnes = Uint8Array.from([1, 255, 255, 255, 255, 255, 255, 255, 255, 0x58, 0x1b, 0, 0]);     // set, 2^64 - 1, t_ms 7000
  const cases = [...BAD_TIMES.map((unix_ms) => ({ set: 1, unix_ms, t_ms: 7000 })), allOnes];
  for (const bad of cases) {
    const band = await Band.connect(42, { transport: new Faulty({ TIME_GET: bad, TIME_SET: bad }), now: () => NOW });
    assert.equal(band.clock, 'computer');
    for (const now of [await band.time(), await band.setTime()]) {
      assert.deepEqual(now, { time: NOW, set: false, t: 7000, source: 'computer' });
      assert.equal(new Date(now.time).toISOString(), '2026-11-12T12:00:00.000Z');       // threw "Invalid time value"
    }
    const events = [];
    band.on('temperature', (e) => events.push(e));
    band.transport.onFrame(codec.encodeStream('temp', { t_ms: 7100, temp_mc: 31000 }));
    assert.equal(new Date(events[0].time).toISOString(), '2026-11-12T12:00:00.100Z');
  }
});

test('a clock inside the range is used as before', async () => {
  for (const unix_ms of [1577836800000, 1794470400000, 4102444799999]) {
    const good = { set: 1, unix_ms, t_ms: 7000 };
    const band = await Band.connect(42, { transport: new Faulty({ TIME_GET: good, TIME_SET: good }), now: () => NOW });
    assert.equal(band.clock, 'band');
    assert.deepEqual(await band.time(), { time: unix_ms, set: true, t: 7000, source: 'band' });
  }
});

test('an answer with a sensors value that does not exist is ignored, like one of the wrong length', async () => {
  for (const sensors of [0, 4, 7, 255]) {
    for (const command of ['MOTION2_GET', 'MOTION2_SET']) {
      const band = await Band.connect(42, {
        timeoutMs: 30,
        transport: new Faulty({
          MOTION2_GET: { ...MOTION2, sensors: command === 'MOTION2_GET' ? sensors : 3 },
          MOTION2_SET: { ...MOTION2, sensors },
          STREAM_START: { active: 256 },
        }),
      });
      for (const call of [() => band.motionLimits(), () => band.configure({ motion: 50, sensors: 'accel' }), () => band.getMotion()]) {
        try {
          const got = await call();
          if (got && 'sensors' in got) assert.ok(['accel', 'gyro', 'both'].includes(got.sensors), 'no answer built from a sensor that does not exist');
        } catch (e) {
          assert.ok(e instanceof VexoError, `${command} sensors ${sensors}: ${e}`);
          assert.equal(e.code, 'timeout');
        }
      }
      assert.equal(band.connected, true);
    }
  }
});

test('sound: a counter that jumps by the most that is filled cannot multiply the sound', () => {
  for (const jump of [MAX_FILL + 1, MAX_FILL, 26, 3]) {              // a gap of 50, 49, 25, 2 before every packet
    const seq = new SeqTracker();
    let packets = 0;
    for (let i = 0; i < 1000; i++) {
      const verdict = seq.accept((i * jump) & 0xffff);
      assert.equal(verdict.take, true);
      packets += verdict.fill + 1;
    }
    assert.ok(packets <= 2 * 1000 + MAX_FILL, `jump ${jump}: ${packets} packets of sound for 1000 that arrived`);   // was 51000
    assert.equal(seq.lost, 999 * (jump - 1));                         // every lost packet is still counted
  }
});

test('sound: a link that loses half of its packets is still filled completely; one gap of a second too', () => {
  const half = new SeqTracker();
  let packets = 0;
  for (let n = 0; n < 2000; n += 2) packets += half.accept(n).fill + 1;
  assert.equal(packets, 1999);
  const seq = new SeqTracker();
  seq.accept(0);
  assert.deepEqual(seq.accept(51), { take: true, fill: 50, jump: false });
  assert.deepEqual(seq.accept(102), { take: true, fill: 1, jump: true });         // 50 lost again at once: the budget is used up
  let n = 102;
  for (let i = 0; i < 60; i++) seq.accept(++n);                                    // 60 packets in order: the budget is back
  assert.deepEqual(seq.accept(n + 51), { take: true, fill: 50, jump: false });
});
