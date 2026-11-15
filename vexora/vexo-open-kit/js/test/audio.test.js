// Tests for BandAudio (js/src/audio.js): the band's Opus packets -> plain samples.
// Node has no audio decoder, so a pretend one stands in for the browser's: it answers each
// packet with 20 ms of sound at 48000 samples per second, as Chrome does. The arithmetic
// underneath (SeqTracker, Resampler, oggPackets) is tested in dashboard/test/speech.test.mjs.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BandAudio, canDecodeAudio, toInt16, VexoError, AUDIO_RATE, AUDIO_FRAME_MS } from '../src/index.js';

/** Puts a pretend decoder in place for one test. `decoded` lists every packet it was given. */
function pretendDecoder(t, { rate = 48000, breakOn = null } = {}) {
  const decoded = [];
  class AudioDecoder {
    static isConfigSupported = async (setup) => ({ supported: setup.codec === 'opus' });
    constructor(handlers) {
      this.handlers = handlers;
    }
    configure(setup) {
      this.setup = setup;
    }
    decode(chunk) {
      decoded.push(chunk);
      if (breakOn && breakOn(chunk)) return this.handlers.error(new Error('bad packet'));
      const count = (rate * AUDIO_FRAME_MS) / 1000;
      // the "sound" is a steady value that tells which packet it came from
      const value = chunk.data.length === 1 ? 0 : chunk.data[1] / 100;
      return this.handlers.output({
        numberOfFrames: count, sampleRate: rate, close() {},
        copyTo: (into) => into.fill(value),
      });
    }
    close() {
      this.closed = true;
    }
  }
  class EncodedAudioChunk {
    constructor(init) {
      Object.assign(this, init);
    }
  }
  Object.assign(globalThis, { AudioDecoder, EncodedAudioChunk });
  t.after(() => {
    delete globalThis.AudioDecoder;
    delete globalThis.EncodedAudioChunk;
  });
  return decoded;
}
const packet = (seq, value = 50) => ({ seq, opus: Uint8Array.of(0xb8, value, 1, 2) });

test('without a decoder (Node, Firefox): canDecodeAudio is false and BandAudio says what to do', async () => {
  assert.equal(await canDecodeAudio(), false);
  assert.throws(() => new BandAudio(() => {}), (e) => e instanceof VexoError && e.code === 'not-available' && /Chrome or Edge/.test(e.message));
});

test('every packet comes out as 20 ms of samples at 16000 a second, in order', async (t) => {
  const decoded = pretendDecoder(t);
  assert.equal(await canDecodeAudio(), true);
  const pieces = [];
  const sound = new BandAudio((samples, info) => pieces.push({ samples, info }));
  for (let seq = 0; seq < 50; seq++) sound.push(packet(seq));
  assert.equal(decoded.length, 50);
  assert.deepEqual(decoded.slice(0, 3).map((c) => [c.type, c.timestamp, c.duration]), [['key', 0, 20000], ['key', 20000, 20000], ['key', 40000, 20000]]);
  const total = pieces.reduce((sum, p) => sum + p.samples.length, 0);
  assert.ok(Math.abs(total - AUDIO_RATE) <= 16, `one second of sound, got ${total} samples`);    // the filter holds a few back
  assert.ok(pieces.every((p) => p.samples instanceof Float32Array && p.info.rate === 16000 && p.info.lost === 0));
  assert.ok(Math.abs(pieces.at(-1).samples.at(-1) - 0.5) < 1e-3, 'the level is kept');
  sound.close();
  sound.push(packet(50));
  assert.equal(decoded.length, 50, 'nothing is decoded after close()');
});

test('another rate can be asked for; 48000 passes the decoder\'s sound straight through', (t) => {
  pretendDecoder(t);
  let total = 0;
  const sound = new BandAudio((samples, info) => {
    total += samples.length;
    assert.equal(info.rate, 48000);
  }, { rate: 48000 });
  for (let seq = 0; seq < 10; seq++) sound.push(packet(seq));
  assert.equal(total, 9600);
  assert.equal(sound.rate, 48000);
});

test('packets lost on the way are filled in, so the timing stays right, and counted', (t) => {
  const decoded = pretendDecoder(t);
  let total = 0;
  let lost = 0;
  const sound = new BandAudio((samples, info) => {
    total += samples.length;
    lost = info.lost;
  }, { rate: 48000 });
  sound.push(packet(10));
  sound.push(packet(11));
  sound.push(packet(15));               // 12, 13 and 14 never arrived
  assert.equal(decoded.length, 6);
  assert.deepEqual(decoded.slice(2, 5).map((c) => c.data.length), [1, 1, 1], 'stand-in packets with no sound data');
  assert.equal(total, 6 * 960);
  assert.equal(lost, 3);
  assert.equal(sound.lost, 3);
  sound.push(packet(11));               // an old packet again: ignored
  assert.equal(decoded.length, 6);
});

test('restart(): the band\'s counter starts at 0 again, and that is not counted as loss', (t) => {
  const decoded = pretendDecoder(t);
  const sound = new BandAudio(() => {});
  for (let seq = 500; seq < 505; seq++) sound.push(packet(seq));
  sound.restart();
  for (let seq = 0; seq < 5; seq++) sound.push(packet(seq));
  assert.equal(decoded.length, 10);
  assert.equal(sound.lost, 0);
});

test('a packet the decoder cannot read does not stop the sound; a decoder that never works is reported once', (t) => {
  let bad = (chunk) => chunk.data[1] === 99;
  const decoded = pretendDecoder(t, { breakOn: (chunk) => bad(chunk) });
  const errors = [];
  let pieces = 0;
  const sound = new BandAudio(() => pieces++, { rate: 48000, onError: (e) => errors.push(e) });
  sound.push(packet(0));
  sound.push(packet(1, 99));
  sound.push(packet(2));
  assert.equal(pieces, 2);
  assert.deepEqual(errors, []);
  bad = () => true;
  for (let seq = 3; seq < 40; seq++) sound.push(packet(seq));
  assert.equal(errors.length, 1);
  assert.equal(errors[0].code, 'not-available');
  assert.ok(decoded.length >= 20);
});

test('toInt16: samples from -1 to 1 become 16 bit numbers, and too-loud ones are cut off', () => {
  assert.deepEqual([...toInt16(Float32Array.of(0, 0.25, -0.25, 1, -1, 2, -2))], [0, 8192, -8192, 32767, -32767, 32767, -32768]);
});
