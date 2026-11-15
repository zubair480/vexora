// Listen and Live transcript, the half that runs in the page. An optional example of what
// can be done with the band's sound:
//
//   band.on('audio')  ->  Opus packet
//       Listen:           the browser's own decoder (WebCodecs) -> Web Audio -> the computer's speakers
//       Live transcript:  the packet as it is -> online.js -> Deepgram (a company on the internet) -> text
//
// Listen never leaves this computer. Live transcript needs the team's Deepgram key and
// sends the sound to that company from a hidden page of its own (online.js).
//
// Nothing here is created until a switch is turned on: no decoder, no audio output, no
// connection. Turning both off stops and frees all of it.
import {
  SOUND_RATE, FRAME_MS, MAX_FILL, SAMPLE_FILE, SeqTracker, lostPacket, ArrivalClock, LagMeter, playAt, PLAY, oggPackets, looksLikeKey, NO_KEY,
} from './pure.js';
import { loadFile } from './load.js';
import { onlineLink } from './online.js';

const DECODER_SETUP = { codec: 'opus', sampleRate: SOUND_RATE, numberOfChannels: 1 };
const PACKET_SAMPLES = (SOUND_RATE * FRAME_MS) / 1000;     // 320: the sound in one packet
const QUIET_AFTER_MS = 1000;      // no packet for this long = "waiting for sound"
const now = () => performance.now();

/** Numbers for the check scripts in dashboard/test/. Nothing else reads them. */
export const stats = { seq: null, decoded: 0, energy: 0, played: 0, late: 0, skippedPlay: 0, readyMs: null, texts: [], decoderRate: null };

/** Can this browser decode the band's Opus sound by itself? (Listen needs it; Live transcript does not.) */
export async function canDecode() {
  try {
    if (typeof AudioDecoder !== 'function' || typeof EncodedAudioChunk !== 'function') return false;
    return (await AudioDecoder.isConfigSupported(DECODER_SETUP)).supported === true;
  } catch {
    return false;
  }
}

/** The pretend band's voice: the Opus packets of the sample file in this folder. */
export async function samplePackets() {
  return oggPackets(await loadFile(SAMPLE_FILE));
}

/** Plays decoded sound through the speakers, keeping a little ready so bursts do not stutter. */
class Player {
  constructor() {
    const Context = window.AudioContext;
    try {
      this.context = new Context({ sampleRate: 48000, latencyHint: 'interactive' });    // the decoder's own rate
    } catch {
      this.context = new Context();
    }
    this.meter = this.context.createAnalyser();
    this.meter.fftSize = 2048;
    this.meter.connect(this.context.destination);
    this.until = 0;                 // when the sound already queued runs out (audio clock, seconds)
  }

  get blocked() {
    return this.context.state !== 'running';
  }

  resume() {
    return this.context.resume().catch(() => {});
  }

  play(samples, rate) {
    const context = this.context;
    if (this.blocked) return;       // the browser is holding the sound back; old sound is not saved up
    const { at, late } = playAt(context.currentTime, this.until);
    if (late && this.until > 0) stats.late++;
    if (at === null) {
      stats.skippedPlay++;
      return;
    }
    const buffer = context.createBuffer(1, samples.length, rate);
    buffer.copyToChannel(samples, 0);
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.meter);
    source.start(at);
    this.until = at + samples.length / rate;
    stats.played += samples.length;
  }

  /** Seconds between a packet arriving and its sound leaving the speakers. */
  get delay() {
    const waiting = Math.max(0, this.until - this.context.currentTime);
    return (waiting || PLAY.lead) + (this.context.outputLatency || this.context.baseLatency || 0);
  }

  /** How loud the sound going to the speakers is right now (0 = silence). */
  level() {
    const wave = new Float32Array(this.meter.fftSize);
    this.meter.getFloatTimeDomainData(wave);
    let sum = 0;
    for (const v of wave) sum += v * v;
    return Math.sqrt(sum / wave.length);
  }

  close() {
    this.context.close().catch(() => {});
  }
}

/**
 * Listen and Live transcript for one page. `onChange` is called whenever something that
 * the page shows has changed; read the fields and getters below to draw it.
 */
export class Speech {
  // The Deepgram key. It lives here, in the memory of the open page, and nowhere else:
  // not in storage, not in the page's address, not in any text on the page.
  #key = '';

  constructor(onChange = () => {}) {
    this.onChange = onChange;
    this.listening = false;         // the Listen switch
    this.transcribing = false;      // the Live transcript switch
    this.state = 'off';             // Live transcript: 'off' | 'connecting' | 'listening' | 'error'
    this.problem = '';
    this.lines = [];                // finished sentences
    this.live = '';                 // the sentence being spoken now
    this.lag = new LagMeter();
    this._seq = new SeqTracker();
    this._decoder = null;
    this._decoderFailures = 0;
    this._player = null;
    this._link = null;              // the link to Deepgram, while Live transcript is on
    this._sent = null;              // when each packet was handed to it (to measure how far behind the text is)
    this._lastPacket = null;
    this._lastArrival = -Infinity;
    this._frameIndex = 0;
  }

  get active() {
    return this.listening || this.transcribing;
  }

  /** Is the band's sound going to Deepgram right now (or about to, while it connects)? */
  get sending() {
    return !!this._link;
  }

  /** Is a key in the page's memory? */
  get hasKey() {
    return this.#key !== '';
  }

  /** Take the key the student typed. Returns false, and keeps nothing, if it cannot be a key. */
  setKey(text) {
    const key = String(text ?? '').trim();
    if (!looksLikeKey(key)) return false;
    this.#key = key;
    this.onChange();
    return true;
  }

  /** Forget the key. If it is in use, Live transcript goes off first. */
  forgetKey() {
    if (this.transcribing) this.setTranscript(false);
    this.#key = '';
    this.onChange();
  }

  /** Packets lost on the way since the page opened. */
  get lost() {
    return this._seq.lost;
  }

  /** Is sound arriving from the band right now? */
  get sound() {
    return now() - this._lastArrival < QUIET_AFTER_MS;
  }

  get playBlocked() {
    return !!this._player && this._player.blocked;
  }

  get playDelay() {
    return this._player ? this._player.delay : 0;
  }

  /** How loud the sound going to the speakers is (for the check script). */
  get playLevel() {
    return this._player ? this._player.level() : 0;
  }

  // ---- the two switches

  setListen(on) {
    if (on === this.listening) return;
    this.listening = on;
    if (on) {
      this._player = new Player();
      this._player.resume();
      stats.playLevel = () => this.playLevel;
    } else {
      this._player.close();
      this._player = null;
      stats.playLevel = null;
    }
    this._tidy();
  }

  /** Live transcript on or off. On needs a key (setKey); without one it stops with a sentence that says so. */
  setTranscript(on) {
    if (on === this.transcribing) return;
    this.transcribing = on;
    if (on) {
      this.problem = '';
      this.lines = [];
      this.live = '';
      this.lag.reset();
      stats.readyMs = null;
      this._switchedOnAt = now();
      this._startLink();
    } else {
      this._stopLink();
      this.state = 'off';
      this.live = '';
    }
    this._tidy();
  }

  /** Both off, and everything freed. */
  stop() {
    this.setListen(false);
    this.setTranscript(false);
  }

  /** The user clicked somewhere: a browser that was holding the sound back will now play it. */
  nudge() {
    if (this.playBlocked) this._player.resume().then(() => this.onChange());
  }

  // No model and no worker: the packets go to Deepgram through online.js, and text comes back.
  _startLink() {
    if (!this.hasKey) {
      this.state = 'error';
      this.problem = NO_KEY;
      return;
    }
    this.state = 'connecting';
    const link = onlineLink(this.#key);
    this._link = link;
    this._sent = new ArrivalClock();
    const mine = () => this._link === link;     // a link that was stopped has nothing more to say
    link.start({
      onState: () => {
        if (!mine()) return;
        this.state = 'listening';
        stats.readyMs = Math.round(now() - this._switchedOnAt);
        this.onChange();
      },
      onText: (message) => {
        if (!mine()) return;
        this._text(message);
        this.onChange();
      },
      onError: (problem) => {
        if (mine()) this._fail(problem);
      },
    });
  }

  _stopLink() {
    this._link?.stop();
    this._link = null;
    this._sent = null;
  }

  _fail(problem) {
    this._stopLink();
    this.state = 'error';
    this.problem = problem;
    this.live = '';
    this.onChange();
  }

  // Make the decoder when Listen goes on; close it when Listen goes off. Live transcript
  // needs none: Deepgram takes the packets as they are.
  _tidy() {
    if (this.listening && !this._decoder) this._openDecoder();
    if (!this.listening && this._decoder) this._closeDecoder();
    this.onChange();
  }

  _openDecoder() {
    const decoder = new AudioDecoder({
      output: (sound) => this._decoded(sound),
      error: () => {
        // A packet the decoder could not read closes it. Start a fresh one; give up if it keeps happening.
        if (this._decoder !== decoder) return;
        this._decoder = null;
        if (++this._decoderFailures > 20) {
          this.listenProblem = 'The browser could not decode the band\'s sound.';
          this.setListen(false);
        } else if (this.listening) {
          this._openDecoder();
        }
      },
    });
    decoder.configure(DECODER_SETUP);
    this._decoder = decoder;
    this._frameIndex = 0;
  }

  _closeDecoder() {
    const decoder = this._decoder;
    this._decoder = null;
    try {
      decoder.close();
    } catch {
      // it was closed already
    }
  }

  // ---- sound in

  /**
   * The band's audio stream started again (Stream audio switched on, or the band came
   * back after a lost link): its packet counter starts at 0 again, and what was being
   * said before is over.
   */
  streamRestarted() {
    this._seq.reset();
    this._lastPacket = null;
    this.live = '';
    this._link?.restart();
  }

  /** One `audio` event from the band: { seq, opus }. */
  frame({ seq, opus }) {
    if (!this.active || (!this._decoder && !this._link) || !opus || !opus.length) {
      if (opus && opus.length) this._seq.accept(seq);       // keep following the counter while off
      return;
    }
    const verdict = this._seq.accept(seq);
    if (!verdict.take) return;
    this._lastArrival = now();
    stats.seq = seq;
    // Too much is missing to smooth over: what comes next does not belong to what came before.
    if (verdict.jump) this._link?.restart();
    if (this._link && this.state === 'listening') {
      // Deepgram gets the packet as the band sent it. A lost packet is simply not there.
      this._link.packet(opus);
      this._sent.push(PACKET_SAMPLES, now());
    }
    if (!this._decoder) return;
    for (let i = 0; i < verdict.fill && i < MAX_FILL; i++) this._decode(lostPacket(this._lastPacket));
    this._lastPacket = opus;
    this._decode(opus);
  }

  _decode(packet) {
    try {
      const chunk = { type: 'key', timestamp: this._frameIndex * FRAME_MS * 1000, duration: FRAME_MS * 1000 };
      chunk.data = packet;
      this._decoder.decode(new EncodedAudioChunk(chunk));
      this._frameIndex++;
    } catch {
      // the decoder was closing; the error handler above starts a new one
    }
  }

  // Decoded sound comes out here, one piece per packet, in order.
  _decoded(sound) {
    const samples = new Float32Array(sound.numberOfFrames);
    sound.copyTo(samples, { planeIndex: 0, format: 'f32-planar' });
    const rate = sound.sampleRate;
    sound.close();
    this._decoderFailures = 0;
    stats.decoded += samples.length;
    stats.decoderRate = rate;
    for (const v of samples) stats.energy += v * v;
    if (this._player) this._player.play(samples, rate);
  }

  // ---- text out

  // Text from Deepgram. `end` says how many seconds of the sound sent so far it covers,
  // so the wait is counted from the moment that sound was handed over.
  _text({ final, text, end }) {
    const heardAt = Number.isFinite(end) ? this._sent?.timeOf(Math.round(end * SOUND_RATE)) ?? null : null;
    const lag = heardAt === null ? null : (now() - heardAt) / 1000;
    if (lag !== null) this.lag.add(lag);
    if (final) {
      if (text) this.lines.push(text);
      while (this.lines.length > 200) this.lines.shift();
      this.live = '';
    } else {
      this.live = text;
    }
    stats.texts.push({ final, text, lag, seq: stats.seq });
    if (stats.texts.length > 500) stats.texts.shift();
  }
}
