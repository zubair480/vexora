// Sound from the band, ready to use. The band sends its microphone as Opus packets (a
// compressed form: 16000 samples per second, one channel, 20 ms each). Most projects want
// plain samples instead (PCM), for a speech service, a graph or their own maths.
//
//   import { Band, BandAudio } from './vexo-open-kit/js/src/index.js';
//
//   const sound = new BandAudio((samples) => {
//     // samples: a Float32Array at 16000 samples per second, values from -1 to 1
//   });
//   band.on('audio', (packet) => sound.push(packet));
//   await band.configure({ audio: true });
//   ...
//   sound.close();
//
// BandAudio uses the decoder built into the browser (WebCodecs: a current Chrome or Edge,
// page from localhost or https). It fills in packets lost on the way, so the timing stays
// right. The smaller pieces below (SeqTracker, Resampler, oggPackets) work anywhere,
// Node included.
import { VexoError } from './transport.js';

export const AUDIO_RATE = 16000;          // samples per second of the band's microphone
export const AUDIO_FRAME_MS = 20;         // one Opus packet
const MODEL_RATE = AUDIO_RATE;
const MAX_OGG_PACKET = 61440;             // the largest sound packet an Ogg Opus file may hold (RFC 7845)

// ---- Ogg Opus file -> Opus packets

/**
 * The sound packets of an Ogg Opus file (the two header packets are left out).
 * An Ogg file is a row of pages: a 27-byte header, a list of sizes, then the packets.
 * @param {Uint8Array} bytes the whole file
 * @returns {Uint8Array[]}
 */
export function oggPackets(bytes) {
  const packets = [];
  let parts = [];
  let length = 0;                           // bytes of the packet being put together
  let at = 0;
  const isPage = () => bytes[at] === 0x4f && bytes[at + 1] === 0x67 && bytes[at + 2] === 0x67 && bytes[at + 3] === 0x53;   // "OggS"
  while (at + 27 <= bytes.length && isPage()) {
    const count = bytes[at + 26];
    const sizes = bytes.subarray(at + 27, at + 27 + count);
    at += 27 + count;
    for (const size of sizes) {
      if (at + size > bytes.length) throw new Error('This Ogg file is cut off in the middle.');
      parts.push(bytes.subarray(at, at + size));
      at += size;
      length += size;
      if (packets.length >= 2 && length > MAX_OGG_PACKET) {      // the two header packets may be bigger
        throw new Error(`This Ogg file holds a packet of more than ${MAX_OGG_PACKET} bytes: that is not Opus sound.`);
      }
      if (size < 255) {                     // a size below 255 ends the packet
        packets.push(join(parts));
        parts = [];
        length = 0;
      }
    }
  }
  const head = packets[0];
  if (packets.length < 2 || !head || String.fromCharCode(...head.subarray(0, 8)) !== 'OpusHead') {
    throw new Error('This is not an Ogg Opus file.');
  }
  return packets.slice(2);
}

function join(parts) {
  if (parts.length === 1) return parts[0].slice();
  const whole = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) {
    whole.set(p, at);
    at += p.length;
  }
  return whole;
}

// ---- the packet counter (seq)

export const MAX_FILL = 50;       // lost packets filled in at most (1 second); a longer hole is a break
// Filling has a budget too, so that a faulty band whose counter jumps with every packet
// cannot make the computer produce 50 times the sound it sent: the budget starts at
// MAX_FILL, every packet that arrives adds 1 (up to MAX_FILL), every filled packet takes 1.
// So never more is filled in than arrived, plus one second. A link that loses up to half
// of its packets is still filled completely.
const STALE_LIMIT = 5;            // this many "old" packets in a row means the stream started again

/**
 * Follows the band's packet counter. It starts at 0, goes up by one per packet and wraps
 * round after 65535. A jump forward means packets were lost on the way.
 */
export class SeqTracker {
  constructor() {
    this.lost = 0;                // packets that never arrived, in total
    this.reset();
  }

  /** The stream starts again (audio switched on, or the band reconnected): the next packet may have any number. */
  reset() {
    this._next = null;
    this._stale = 0;
    this._budget = MAX_FILL;      // lost packets that may still be filled in (see MAX_FILL)
  }

  /**
   * @param {number} seq the counter of the packet that just arrived
   * @returns {{ take: boolean, fill: number, jump: boolean }} take = use this packet;
   *   fill = lost packets to fill in before it; jump = too many were lost (or the stream
   *   started again) to fill in all of them, so the sound before and after do not belong together.
   */
  accept(seq) {
    if (this._next === null) {
      this._next = (seq + 1) & 0xffff;
      return { take: true, fill: 0, jump: false };
    }
    const gap = (seq - this._next) & 0xffff;
    if (gap >= 0x8000) {                    // an old packet, or one we already had
      if (++this._stale < STALE_LIMIT) return { take: false, fill: 0, jump: false };
      this._stale = 0;                      // not old after all: the stream started again
      this._next = (seq + 1) & 0xffff;
      return { take: true, fill: 0, jump: true };
    }
    this._stale = 0;
    this._next = (seq + 1) & 0xffff;
    this.lost += gap;
    const fill = gap > MAX_FILL ? 0 : Math.min(gap, this._budget);
    this._budget = Math.min(MAX_FILL, this._budget - fill + 1);
    return { take: true, fill, jump: fill < gap };
  }
}

/**
 * The packet to give the decoder in place of a lost one: only the first byte of a normal
 * packet (same kind and length of sound), with no sound data. The decoder then smooths
 * over the hole by itself, and the timing stays right.
 */
export const lostPacket = (lastPacket) => Uint8Array.of(((lastPacket && lastPacket[0]) ?? 0xb8) & 0xfc);

// ---- decoder output (usually 48000 samples per second) -> 16000 for the speech model

/** Low-pass filter for taking every `factor`-th sample: a windowed sinc. */
function lowpass(factor, taps) {
  const h = new Float32Array(taps);
  const mid = (taps - 1) / 2;
  const cutoff = 0.9 / (2 * factor);       // a little below half the new sample rate
  let sum = 0;
  for (let i = 0; i < taps; i++) {
    const x = i - mid;
    const sinc = x === 0 ? 2 * cutoff : Math.sin(2 * Math.PI * cutoff * x) / (Math.PI * x);
    h[i] = sinc * (0.54 - 0.46 * Math.cos((2 * Math.PI * i) / (taps - 1)));   // Hamming window
    sum += h[i];
  }
  for (let i = 0; i < taps; i++) h[i] /= sum;
  return h;
}

/** Turns sound at `fromRate` into sound at `toRate`, piece by piece, with no click between pieces. */
export class Resampler {
  constructor(fromRate, toRate = MODEL_RATE) {
    this.fromRate = fromRate;
    this.toRate = toRate;
    this._factor = fromRate / toRate;
    this._whole = Number.isInteger(this._factor) && this._factor > 1;
    this._taps = this._whole ? lowpass(this._factor, 16 * this._factor + 1) : null;
    this.reset();
  }

  reset() {
    this._tail = new Float32Array(0);      // input samples not used up yet
    this._at = 0;                          // position of the next output sample in _tail (may be a fraction)
  }

  /** @param {Float32Array} input @returns {Float32Array} */
  push(input) {
    if (this._factor === 1) return input;
    const all = new Float32Array(this._tail.length + input.length);
    all.set(this._tail);
    all.set(input, this._tail.length);
    const out = [];
    let at = this._at;
    if (this._whole) {
      const taps = this._taps;
      for (; at + taps.length <= all.length; at += this._factor) {
        let sum = 0;
        for (let k = 0; k < taps.length; k++) sum += all[at + k] * taps[k];
        out.push(sum);
      }
    } else {                               // an unusual rate: straight lines between samples
      for (; at + 1 < all.length; at += this._factor) {
        const i = Math.floor(at);
        out.push(all[i] + (all[i + 1] - all[i]) * (at - i));
      }
    }
    const used = Math.min(Math.floor(at), all.length);
    this._tail = all.slice(used);
    this._at = at - used;
    return Float32Array.from(out);
  }
}

// ---- Opus packets -> samples, in a browser

const DECODER_SETUP = { codec: 'opus', sampleRate: AUDIO_RATE, numberOfChannels: 1 };

/** Can this browser decode the band's sound by itself? (false in Node, Firefox and old browsers) */
export async function canDecodeAudio() {
  try {
    if (typeof AudioDecoder !== 'function' || typeof EncodedAudioChunk !== 'function') return false;
    return (await AudioDecoder.isConfigSupported(DECODER_SETUP)).supported === true;
  } catch {
    return false;
  }
}

/**
 * Turns the band's `audio` events into plain samples.
 * Give every `audio` event to push(); `onSamples` is called with each piece of sound, in order.
 */
export class BandAudio {
  /**
   * @param {(samples: Float32Array, info: { rate: number, lost: number }) => void} onSamples
   *   called about 50 times a second with 20 ms of sound; `info.lost` counts the packets
   *   lost on the way so far (their place is filled with smoothed-over sound)
   * @param {object} [options]
   * @param {number} [options.rate] samples per second you want (default 16000; 48000 is good for playing)
   * @param {(error: Error) => void} [options.onError] called if the browser cannot decode
   */
  constructor(onSamples, { rate = AUDIO_RATE, onError = () => {} } = {}) {
    if (typeof AudioDecoder !== 'function' || typeof EncodedAudioChunk !== 'function') {
      throw new VexoError('not-available', 'This browser cannot decode the band\'s sound (no WebCodecs audio decoder). '
        + 'Use a current Chrome or Edge, and open the page from localhost or an https address.');
    }
    this.rate = rate;
    this._onSamples = onSamples;
    this._onError = onError;
    this._seq = new SeqTracker();
    this._resampler = null;
    this._lastPacket = null;
    this._index = 0;
    this._failures = 0;
    this._closed = false;
    this._open();
  }

  /** Packets lost on the way so far. */
  get lost() {
    return this._seq.lost;
  }

  _open() {
    const decoder = new AudioDecoder({
      output: (sound) => this._decoded(sound),
      error: (error) => {
        // A packet the decoder could not read closes it. Start a fresh one; give up if it keeps happening.
        if (this._decoder !== decoder || this._closed) return;
        this._decoder = null;
        if (++this._failures > 20) this._onError(new VexoError('not-available', `The browser could not decode the band's sound: ${error.message}`));
        else this._open();
      },
    });
    decoder.configure(DECODER_SETUP);
    this._decoder = decoder;
  }

  /** One `audio` event from the band: { seq, opus }. */
  push({ seq, opus }) {
    if (this._closed || !this._decoder || !opus || !opus.length) return;
    const verdict = this._seq.accept(seq);
    if (!verdict.take) return;
    if (verdict.jump) this._resampler?.reset();
    for (let i = 0; i < verdict.fill; i++) this._decode(lostPacket(this._lastPacket));
    this._lastPacket = opus;
    this._decode(opus);
  }

  /** The band's audio stream started again (switched on again, or a reconnect): its packet counter starts at 0. */
  restart() {
    this._seq.reset();
    this._lastPacket = null;
    this._resampler?.reset();
  }

  _decode(packet) {
    try {
      const chunk = { type: 'key', timestamp: this._index * AUDIO_FRAME_MS * 1000, duration: AUDIO_FRAME_MS * 1000, data: packet };
      this._decoder.decode(new EncodedAudioChunk(chunk));
      this._index++;
    } catch {
      // the decoder was closing; its error handler starts a new one
    }
  }

  _decoded(sound) {
    const samples = new Float32Array(sound.numberOfFrames);
    sound.copyTo(samples, { planeIndex: 0, format: 'f32-planar' });
    const from = sound.sampleRate;          // browsers decode Opus at 48000, whatever went in
    sound.close();
    this._failures = 0;
    if (this._closed) return;
    if (!this._resampler || this._resampler.fromRate !== from) this._resampler = new Resampler(from, this.rate);
    const out = this._resampler.push(samples);
    if (out.length) this._onSamples(out, { rate: this.rate, lost: this._seq.lost });
  }

  /** Stop and free the decoder. */
  close() {
    this._closed = true;
    try {
      this._decoder?.close();
    } catch {
      // it was closed already
    }
    this._decoder = null;
  }
}

/** Samples from -1 to 1 -> 16 bit numbers (what most speech services call "linear16" or "pcm_s16le"). */
export function toInt16(samples) {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) out[i] = Math.max(-32768, Math.min(32767, Math.round(samples[i] * 32767)));
  return out;
}
