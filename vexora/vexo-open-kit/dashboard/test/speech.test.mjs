// Tests for the arithmetic behind Listen and Live transcript (dashboard/speech/pure.js).
// No browser, no network. The words and the link of Live transcript are in deepgram.test.mjs. Run: node --test dashboard/test/*.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, statSync } from 'node:fs';
import {
  SAMPLE_FILE, SAMPLE_SECONDS, SAMPLE_SENTENCE, oggPackets, SeqTracker, MAX_FILL, lostPacket, ArrivalClock, LagMeter, playAt, PLAY,
  NO_DECODER, EXAMPLE_NOTE, listenStatus,
} from '../speech/pure.js';
import { Resampler } from '../../js/src/audio.js';
import { SimTransport, Band } from '../../js/src/index.js';

const speechDir = new URL('../speech/', import.meta.url);
const sample = () => new Uint8Array(readFileSync(new URL(SAMPLE_FILE, speechDir)));

// ---- Ogg Opus

/** A tiny Ogg file: each page is [packets], with the sizes written the Ogg way. */
function ogg(pages) {
  const bytes = [];
  for (const packets of pages) {
    const sizes = [];
    const body = [];
    for (const packet of packets) {
      let left = packet.length;
      while (left >= 255) {
        sizes.push(255);
        left -= 255;
      }
      if (!packet.open) sizes.push(left);
      body.push(...packet);
    }
    bytes.push(0x4f, 0x67, 0x67, 0x53, ...new Array(22).fill(0), sizes.length, ...sizes, ...body);
  }
  return Uint8Array.from(bytes);
}
const HEAD = [...Buffer.from('OpusHead'), 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0];
const TAGS = [...Buffer.from('OpusTags'), 0, 0, 0, 0, 0, 0, 0, 0];

test('oggPackets: gives the sound packets and leaves out the two headers', () => {
  const packets = oggPackets(ogg([[HEAD], [TAGS], [[0xb8, 1, 2], [0xb8, 3]], [[0xb8, 4, 5, 6]]]));
  assert.deepEqual(packets.map((p) => [...p]), [[0xb8, 1, 2], [0xb8, 3], [0xb8, 4, 5, 6]]);
});

test('oggPackets: a packet of 255 bytes or more, and one that runs over two pages', () => {
  const big = Array.from({ length: 300 }, (_, i) => i & 255);
  const exact = new Array(255).fill(7);
  const first = Object.assign(new Array(255).fill(9), { open: true });      // 255 bytes, carries on in the next page
  const packets = oggPackets(ogg([[HEAD], [TAGS], [big, exact, first], [[1, 2, 3]]]));
  assert.deepEqual(packets.map((p) => p.length), [300, 255, 258]);
  assert.deepEqual([...packets[0]], big);
  assert.deepEqual([...packets[2].slice(-3)], [1, 2, 3]);
});

test('oggPackets: says so when the file is not Ogg Opus or is cut off', () => {
  assert.throws(() => oggPackets(Uint8Array.from([1, 2, 3, 4])), /not an Ogg Opus file/);
  assert.throws(() => oggPackets(ogg([[[1, 2, 3]], [[4]], [[5]]])), /not an Ogg Opus file/);
  const whole = ogg([[HEAD], [TAGS], [[0xb8, 1, 2, 3, 4, 5]]]);
  assert.throws(() => oggPackets(whole.slice(0, whole.length - 2)), /cut off/);
});

test('the sample voice is an Ogg Opus file of 20 ms packets like the band sends', () => {
  const packets = oggPackets(sample());
  assert.equal(packets.length, 796);                              // 15.92 seconds
  for (const packet of packets) {
    assert.equal(packet[0], 0xb8, 'CELT, wideband, 20 ms, mono, one frame');
    assert.ok(packet.length <= 80, 'about 24 kbit/s');
  }
  assert.ok(statSync(new URL(SAMPLE_FILE, speechDir)).size < 60000);
  assert.match(SAMPLE_SENTENCE, /^In a spell of dry weather, .* the crow could not reach the water\.$/);
  assert.ok(readFileSync(new URL('README.md', speechDir), 'utf8').replace(/\s*\n>?\s*/g, ' ').includes(SAMPLE_SENTENCE), 'README.md must give the sentence');
});

test('the pretend band plays packets given to it after connecting, in a loop, counting from 0', async () => {
  const sim = new SimTransport(42, { realtime: false });
  const band = await Band.connect(42, { transport: sim });
  const packets = oggPackets(sample()).slice(0, 5);
  sim.audioPackets = packets;                                     // what the dashboard does when a switch goes on
  const got = [];
  band.on('audio', (frame) => got.push(frame));
  const configured = band.configure({ audio: true });
  sim.step(10);
  await configured;
  sim.step(20 * 12);
  assert.ok(got.length >= 11, `got ${got.length} packets`);
  got.slice(0, 11).forEach((frame, i) => {
    assert.equal(frame.seq, i);
    assert.deepEqual([...frame.opus], [...packets[i % 5]]);
  });
  await band.disconnect();
});

// ---- the packet counter

test('SeqTracker: packets in order need nothing filled in', () => {
  const seq = new SeqTracker();
  for (let i = 0; i < 10; i++) assert.deepEqual(seq.accept(i), { take: true, fill: 0, jump: false });
  assert.equal(seq.lost, 0);
});

test('SeqTracker: a gap is filled, so the timing does not drift', () => {
  const seq = new SeqTracker();
  seq.accept(0);
  assert.deepEqual(seq.accept(4), { take: true, fill: 3, jump: false });
  assert.deepEqual(seq.accept(5), { take: true, fill: 0, jump: false });
  assert.equal(seq.lost, 3);
  // Over a whole stream: packets taken + packets filled = packets sent.
  const tracker = new SeqTracker();
  let frames = 0;
  for (let i = 0; i < 1000; i++) {
    if (i % 7 === 3 || (i > 500 && i < 520)) continue;           // lost on the way
    const verdict = tracker.accept(i);
    frames += verdict.fill + 1;
  }
  assert.equal(frames, 1000);
});

test('SeqTracker: the counter wraps round after 65535', () => {
  const seq = new SeqTracker();
  seq.accept(65534);
  assert.deepEqual(seq.accept(65535), { take: true, fill: 0, jump: false });
  assert.deepEqual(seq.accept(0), { take: true, fill: 0, jump: false });
  assert.deepEqual(seq.accept(3), { take: true, fill: 2, jump: false });
  const other = new SeqTracker();
  other.accept(65530);
  assert.deepEqual(other.accept(2), { take: true, fill: 7, jump: false });
});

test('SeqTracker: a hole too long to fill is a break, not a second of made-up sound', () => {
  const seq = new SeqTracker();
  seq.accept(10);
  assert.deepEqual(seq.accept(10 + MAX_FILL + 1), { take: true, fill: MAX_FILL, jump: false });
  assert.deepEqual(seq.accept(10 + MAX_FILL + 1 + MAX_FILL + 2), { take: true, fill: 0, jump: true });
  assert.equal(seq.lost, MAX_FILL + MAX_FILL + 1);
});

test('SeqTracker: an old or repeated packet is dropped', () => {
  const seq = new SeqTracker();
  seq.accept(100);
  seq.accept(101);
  assert.deepEqual(seq.accept(101), { take: false, fill: 0, jump: false });
  assert.deepEqual(seq.accept(99), { take: false, fill: 0, jump: false });
  assert.deepEqual(seq.accept(102), { take: true, fill: 0, jump: false });
});

test('SeqTracker: after reset() the stream may start at 0 again (audio switched on again, band reconnected)', () => {
  const seq = new SeqTracker();
  for (let i = 0; i < 500; i++) seq.accept(i);
  seq.reset();
  assert.deepEqual(seq.accept(0), { take: true, fill: 0, jump: false });
  assert.deepEqual(seq.accept(1), { take: true, fill: 0, jump: false });
  assert.equal(seq.lost, 0);
});

test('SeqTracker: a restart nobody announced is noticed after a few packets', () => {
  const seq = new SeqTracker();
  for (let i = 0; i < 500; i++) seq.accept(i);
  const verdicts = [0, 1, 2, 3, 4, 5, 6].map((i) => seq.accept(i));
  assert.deepEqual(verdicts.slice(0, 4).map((v) => v.take), [false, false, false, false]);
  assert.deepEqual(verdicts[4], { take: true, fill: 0, jump: true });
  assert.deepEqual(verdicts[5], { take: true, fill: 0, jump: false });
  assert.equal(seq.lost, 0);
});

test('lostPacket: one byte that says "same kind of packet, no sound data"', () => {
  assert.deepEqual([...lostPacket(Uint8Array.of(0xb8, 1, 2, 3))], [0xb8]);
  assert.deepEqual([...lostPacket(Uint8Array.of(0xbb, 0x41, 9))], [0xb8]);    // the pretend band's silent packet
  assert.deepEqual([...lostPacket(null)], [0xb8]);
});

// ---- resampling

const tone = (hz, rate, n, from = 0) => Float32Array.from({ length: n }, (_, i) => Math.sin((2 * Math.PI * hz * (from + i)) / rate));
const rms = (x, from = 0) => Math.sqrt(x.slice(from).reduce((s, v) => s + v * v, 0) / (x.length - from));

test('Resampler: 48000 -> 16000 keeps a speech tone and gives a third of the samples', () => {
  const r = new Resampler(48000);
  let out = [];
  for (let i = 0; i < 50; i++) out = out.concat([...r.push(tone(1000, 48000, 960, i * 960))]);
  assert.ok(Math.abs(out.length - 16000) <= 20, `got ${out.length} samples for one second`);
  assert.ok(Math.abs(rms(Float32Array.from(out), 200) - Math.SQRT1_2) < 0.02);
});

test('Resampler: the pieces join without a click (same result as one long piece)', () => {
  const whole = tone(440, 48000, 9600);
  const a = new Resampler(48000).push(whole);
  const r = new Resampler(48000);
  const joined = [];
  let at = 0;
  for (const size of [960, 1, 959, 2000, 37, 5643]) {
    joined.push(...r.push(whole.subarray(at, at + size)));
    at += size;
  }
  assert.equal(at, whole.length);
  assert.equal(joined.length, a.length);
  for (let i = 0; i < a.length; i++) assert.ok(Math.abs(a[i] - joined[i]) < 1e-6, `sample ${i}`);
});

test('Resampler: sound above what 16000 can hold is removed, not folded into the speech', () => {
  const out = new Resampler(48000).push(tone(12000, 48000, 48000));
  assert.ok(rms(out, 200) < 0.02, `left over: ${rms(out, 200)}`);
});

test('Resampler: 16000 passes straight through; an odd rate still comes out at the right length', () => {
  const same = tone(300, 16000, 320);
  assert.equal(new Resampler(16000).push(same), same);
  const out = new Resampler(44100).push(tone(300, 44100, 44100));
  assert.ok(Math.abs(out.length - 16000) <= 2);
  assert.ok(Math.abs(rms(out) - Math.SQRT1_2) < 0.02);
});

// ---- how far behind the text is

test('ArrivalClock: tells when a sample arrived', () => {
  const clock = new ArrivalClock();
  assert.equal(clock.timeOf(1), null);
  clock.push(320, 1000);
  clock.push(320, 1020);
  clock.push(320, 1500);            // a burst arrived late
  assert.equal(clock.timeOf(1), 1000);
  assert.equal(clock.timeOf(320), 1000);
  assert.equal(clock.timeOf(321), 1020);
  assert.equal(clock.timeOf(960), 1500);
  assert.equal(clock.timeOf(961), null, 'not arrived yet');
  clock.reset();
  assert.equal(clock.position, 0);
  assert.equal(clock.timeOf(1), null);
});

test('ArrivalClock: keeps working for hours without growing', () => {
  const clock = new ArrivalClock(64);
  for (let i = 0; i < 10000; i++) clock.push(320, i * 20);
  assert.ok(clock._ends.length <= 64);
  assert.equal(clock.timeOf(10000 * 320), 9999 * 20);
  assert.equal(clock.timeOf(9990 * 320), 9989 * 20);
});

test('LagMeter: a calm typical value, and the worst', () => {
  const lag = new LagMeter();
  assert.equal(lag.typical, null);
  for (const s of [0.5, 0.6, 9, 0.4, 0.7]) lag.add(s);
  assert.equal(lag.typical, 0.6);
  assert.equal(lag.worst, 9);
  lag.add(NaN);
  lag.add(-1);
  assert.equal(lag.count, 5);
  lag.reset();
  assert.equal(lag.typical, null);
  assert.equal(lag.worst, 0);
});

// ---- playing

test('playAt: keeps a little sound ready, plays pieces back to back, skips when too much waits', () => {
  assert.deepEqual(playAt(10, 0), { at: 10 + PLAY.lead, late: true });               // first piece
  assert.deepEqual(playAt(10, 10.15), { at: 10.15, late: false });                   // back to back
  assert.deepEqual(playAt(10, 9.99), { at: 10 + PLAY.lead, late: true });            // ran dry: start again with a lead
  assert.deepEqual(playAt(10, 10 + PLAY.most + 0.01), { at: null, late: false });    // a burst after a stall: skip to catch up
  // A steady stream never drifts: 500 pieces of 20 ms end exactly 10 s after the first start.
  let until = 0;
  let first = null;
  for (let i = 0; i < 500; i++) {
    const { at } = playAt(5 + i * 0.02, until);
    first ??= at;
    until = at + 0.02;
  }
  assert.ok(Math.abs(until - first - 10) < 1e-9);
});

// ---- words

test('listenStatus and the fixed notes', () => {
  assert.equal(listenStatus({ on: false }), 'Listen is off.');
  assert.equal(listenStatus({ on: true, sound: false }), 'Waiting for sound from the band...');
  assert.match(listenStatus({ on: true, blocked: true }), /^The browser is holding the sound back\. Click anywhere/);
  assert.match(listenStatus({ on: true, sound: true, delay: 0.23, lost: 0 }), /^Playing the band's microphone on this computer, about 0\.2 s behind\. Keep the band away/);
  assert.match(listenStatus({ on: true, sound: true, delay: 0.2, lost: 1 }), /1 lost packet was smoothed over\./);
  assert.match(listenStatus({ on: true, sound: true, delay: 0.2, lost: 12 }), /12 lost packets were smoothed over\./);
  assert.match(NO_DECODER, /Everything else on this page works\.$/);
  assert.match(EXAMPLE_NOTE, /example/);
  assert.match(EXAMPLE_NOTE, /Listen plays the sound on this computer and sends it nowhere\./);
  assert.match(EXAMPLE_NOTE, /Without a key everything else on this page works as before\./);
  assert.match(NO_DECODER, /so Listen is not available here/);
  assert.doesNotMatch(NO_DECODER + EXAMPLE_NOTE, /Moonshine|model|download|MB\b/i);
  assert.ok(Math.abs(oggPackets(sample()).length * 0.02 - SAMPLE_SECONDS) < 0.5, 'the sample is as long as the status line says');
  assert.ok(SAMPLE_SENTENCE.startsWith('In a spell of dry weather'));
  for (const words of [NO_DECODER, EXAMPLE_NOTE]) assert.doesNotMatch(words, /—/);
});
