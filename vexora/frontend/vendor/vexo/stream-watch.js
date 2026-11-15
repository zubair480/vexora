// The stream watch: notices "my stream stopped arriving though I did not stop it".
//
// A band has two links, Bluetooth and the USB cable, and one program can be on each. They
// share the band: its settings, its light and its streams. A stream goes to the program
// that started it last (contract section 2.3). So when a second program on the other link
// starts "motion", the first program gets no motion any more, with no notice, while its
// link stays up and its commands are still answered. When that second program ends, the
// band stops the stream altogether. Without this watch a page would go on showing
// "Connected" with no data.
//
// How it works:
//   1. The sign (it costs nothing, no command is sent): a stream this program started and
//      did not stop has sent nothing for 3 seconds (or 3 of its frame times, if that is
//      longer) while the link is up. Streams that are silent by nature (taps, "battery on
//      change") give no such sign; while one of those is on, the watch asks every 30 seconds.
//   2. The check: one STREAM_GET. It is only sent after a sign, so never while data flows.
//      - The band says the stream is off: something else stopped it (a program on the
//        other link took it and then ended, or stopped it).
//      - The band says it is on, and still nothing arrived: it now goes to the other link.
//   3. The news: the event 'stream-taken' { streams, stopped, message }. Nothing is taken
//      back by itself (two programs would then fight over the stream for ever):
//      band.takeBack() starts the streams again for this program.
//
// On the USB cable a stream that the band reports as off is handled by the session watch
// (transports/session-watch.js): there it means the band ended the session.
//
// Tested with the pretend band only, not yet with two programs on a real band.
import { fastestFrameMs } from './transports/session-watch.js';

export const STREAM_TAKEN = 'stream-taken';

/** The times of the watch, in ms. Tests make them shorter on one band: band._streamWatch.times. */
export const STREAM_WATCH_TIMES = Object.freeze({
  tick: 1000,       // how often the watch looks (a timer, nothing is sent)
  silence: 3000,    // a stream on a timer sent nothing for this long: ask
  quiet: 30000,     // a stream that is silent by nature is on: ask this often
});

const FIRST_STREAM = 0x10;      // frame type 0x10 + n carries stream bit n (contract section 4)

export class StreamWatch {
  /** @param {object} band the Band this watch belongs to */
  constructor(band) {
    this.band = band;
    this.times = { ...STREAM_WATCH_TIMES };
    /** How many times the watch asked the band (STREAM_GET). For tests. */
    this.asked = 0;
    /** Mask of the streams this program started and has not stopped. */
    this.mine = 0;
    /** Of those: the ones reported with 'stream-taken' and not started again since. */
    this.taken = 0;
    this._heard = new Map();    // stream bit -> when its last frame came (or when it was started)
    this._timer = null;
    this._asking = false;
    this._tickAt = 0;
    this._since = 0;            // silence is not counted from before this (the program itself was stuck)
    this._askedAt = 0;
  }

  /** Band: STREAM_START was accepted. `mask` is what was asked for, `active` what the band says is on. */
  started(mask, active) {
    const now = Date.now();
    this.mine |= mask & active;
    this.taken &= ~mask;
    for (let bit = 0; bit < 16; bit++) if (mask & (1 << bit)) this._heard.set(bit, now);
  }

  /** Band: STREAM_STOP was accepted for `mask`. */
  stopped(mask) {
    this.mine &= ~mask;
    this.taken &= ~mask;
  }

  /** Band: the link is gone, or the band was put to sleep: nothing is on any more. */
  reset() {
    this.mine = 0;
    this.taken = 0;
    this.stop();
  }

  _watched() {
    return this.band._linkUp ? this.mine & ~this.taken : 0;
  }

  /** Called by Band after every answer: the timer runs only while a stream of this program is on. */
  update() {
    if (!this._watched()) return this.stop();
    if (this._timer !== null) return;
    this._tickAt = this._since = this._askedAt = Date.now();
    this._timer = setInterval(() => this._tick(), this.times.tick);
    this._timer.unref?.();      // Node: this timer alone does not keep a program running
  }

  stop() {
    if (this._timer === null) return;
    clearInterval(this._timer);
    this._timer = null;
  }

  /** Called by Band for every frame. */
  heard(type) {
    if (type >= FIRST_STREAM && type < FIRST_STREAM + 16) this._heard.set(type - FIRST_STREAM, Date.now());
  }

  /** The watched streams that send on a timer and have been silent for too long. */
  _silent(now) {
    let mask = 0;
    const watched = this._watched();
    for (let bit = 0; bit < 16; bit++) {
      if (!(watched & (1 << bit))) continue;
      const frame = fastestFrameMs(this.band, 1 << bit);
      if (frame === null) continue;                       // silent by nature
      const from = Math.max(this._heard.get(bit) ?? 0, this._since);
      if (now - from >= Math.max(this.times.silence, 3 * frame)) mask |= 1 << bit;
    }
    return mask;
  }

  _tick() {
    const { band, times } = this;
    const now = Date.now();
    const late = now - this._tickAt - times.tick;
    this._tickAt = now;
    if (!this._watched()) return this.stop();
    if (band._reconnectRun || band._critical || this._asking) return undefined;
    if (late >= times.tick) {
      this._since = now;        // this program itself was stuck: that time is not silence of the band
      return undefined;
    }
    if (this._silent(now)) return this._ask();
    // Streams that are silent by nature show nothing: ask now and then. (On the USB cable the session watch does.)
    const natural = this._watched() & ~this._timed();
    if (natural && !band.transport?.sessionWatch && now - this._askedAt >= times.quiet) return this._ask();
    return undefined;
  }

  _timed() {
    let mask = 0;
    for (let bit = 0; bit < 16; bit++) if (fastestFrameMs(this.band, 1 << bit) !== null) mask |= 1 << bit;
    return mask;
  }

  /** The check: one STREAM_GET. Band._remember hands the answer to answer(). */
  async _ask() {
    this._asking = true;
    this.asked++;
    this._askedAt = Date.now();
    try {
      await this.band._enqueue('STREAM_GET');
    } catch {
      // no answer: the link itself is in trouble, and the program hears about that
    }
    this._asking = false;
  }

  /**
   * Called by Band with the answer to every STREAM_GET. A stream of this program that the
   * band says is off, or that is on and still silent, was taken: tell the program once.
   */
  answer(active) {
    const band = this.band;
    if (!band._linkUp || band._reconnectRun || band._critical) return;
    const watched = this._watched();
    const off = watched & ~active;
    const lost = off | (this._silent(Date.now()) & active);
    if (!lost) return;
    this.taken |= lost;
    queueMicrotask(() => band._streamsTaken(lost, off));    // after the answer went to whoever asked
  }
}
