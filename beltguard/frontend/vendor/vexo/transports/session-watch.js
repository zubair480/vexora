// The USB session watch: notices when the band ended the USB session by itself.
//
// A band with firmware 1.1.3 ends the USB session of a computer that took no data at all
// for about 5 seconds while streams were running (a page or program that was stuck). It
// sends no notice. Afterwards the port is still open and commands are answered, but every
// stream is off and the settings are back at their defaults. Without this watch a page
// would go on showing "Connected" with no data.
//
// Only a transport with `sessionWatch = true` is watched (the USB cable). Over Bluetooth
// nothing here runs: no timer and no command.
//
// How it works, cheapest first:
//   1. Signs (they cost nothing, no command is sent):
//      - streams that send on a timer are on, and no stream frame came for 2 seconds
//        (or 3 frame times of the fastest such stream, if that is longer). After a session
//        end every stream is silent, so the fastest one shows it first;
//      - this program itself was stuck for 3 seconds or more (the timer below came late);
//      - an empty packet came in the middle of a session (the transport says so with
//        onSessionHint). A band that paused for 200 ms sends one too, so this is only a
//        reason to look at the stream frames for a moment;
//      - only streams that are silent by nature are on (taps, battery on change) and
//        nothing at all came for 30 seconds.
//   2. The check: one STREAM_GET. It is only sent after a sign, so never while data flows.
//      If the band says a stream is off that this program switched on, the session was
//      ended. If it says everything is still on, nothing happens, and the next check
//      waits twice as long (at most 30 seconds) until a stream frame comes again.
//   3. The end: the port is closed and the link ends with reason 'session-ended'. With
//      autoReconnect the library opens the port again and sets everything again, exactly
//      as after a pulled cable. Without it the program gets 'disconnect'.
//
// Tested with the pretend band and fake ports only, not yet with a real band.
import { STREAM_BITS } from '../codec.js';

export const SESSION_ENDED = 'session-ended';
export const SESSION_ENDED_TEXT = 'The band ended the USB session because this computer took no data for 5 seconds '
  + '(a page or program that was stuck). Streams were stopped.';
/** The sentence for the person: what happened and what happens next. */
export const sessionEndedText = (reconnecting) => `${SESSION_ENDED_TEXT} ${reconnecting
  ? 'The library is connecting again and sets your streams and settings again.'
  : 'Connect again, and keep handlers short.'}`;

/** The times of the watch, in ms. Tests make them shorter on one band: band._watch.times. */
export const WATCH_TIMES = Object.freeze({
  tick: 1000,       // how often the watch looks (a timer, nothing is sent)
  silence: 2000,    // streams on a timer are on and no stream frame came for this long: ask
  stall: 3000,      // the timer came this late: this program was stuck, ask once frames had time to come
  settle: 500,      // after a sign, frames that come this soon may be old ones: they do not count
  quiet: 30000,     // only naturally silent streams are on and nothing came for this long: ask. Also the longest wait
});

const FIRST_STREAM = 0x10;      // frame types 0x10..0x1f are stream frames (contract section 4)
const bit = (name) => 1 << STREAM_BITS[name];

/**
 * The time between two frames of the fastest stream that sends on a timer, in ms, or null
 * when no such stream is on (taps and "battery on change" are silent by nature).
 */
export function fastestFrameMs(band, mask) {
  const memory = band._memory;
  const set = memory.intervals ?? {};
  const each = [];
  if (mask & bit('motion')) each.push(10000 / (band._imu?.rate_hz ?? 25));              // 10 samples in a frame
  if (mask & bit('motion2')) each.push(Math.max(50, 1000 / (band._m2?.rate_hz ?? 25)));  // 50 ms in a frame, 1 sample or more
  if (mask & bit('audio')) each.push(20);
  if (mask & bit('mic_level')) each.push(set[STREAM_BITS.mic_level] ?? 100);
  if (mask & bit('link')) each.push(set[STREAM_BITS.link] ?? 1000);                      // Bluetooth only
  if (mask & bit('activity')) each.push(set[STREAM_BITS.activity] ?? 1000);
  if (mask & bit('temp')) each.push(set[STREAM_BITS.temp] ?? (memory.TEMP_SET_INTERVAL ? memory.TEMP_SET_INTERVAL.seconds * 1000 : 5000));
  if ((mask & bit('battery')) && set[STREAM_BITS.battery] > 0) each.push(set[STREAM_BITS.battery]);
  return each.length ? Math.min(...each) : null;
}

export class SessionWatch {
  /** @param {object} band the Band this watch belongs to */
  constructor(band) {
    this.band = band;
    this.times = { ...WATCH_TIMES };
    /** How many times the watch asked the band (STREAM_GET). For tests. */
    this.asked = 0;
    /** 'session-ended' while the watch is closing the link for that reason (Band._closed reads it). */
    this.ending = null;
    this._timer = null;
    this._asking = false;
    this._reset();
    // A transport that sees the bytes can tell us about an empty packet.
    if (band.transport && 'onSessionHint' in band.transport) band.transport.onSessionHint = () => this.hint();
  }

  _reset() {
    const now = Date.now();
    this._tickAt = now;       // when the timer last ran
    this._since = now;        // silence is counted from here ...
    this._heardAt = 0;        // ... or from the last stream frame
    this._hintAt = null;      // a sign that still has to be looked at
    this._wait = 1;           // 1, 2, 4, ...: each check that found nothing doubles the wait
  }

  /** The streams this program switched on (what auto-reconnect would start again). */
  _believed() {
    return this.band._memory.mask ?? 0;
  }

  _watching() {
    const band = this.band;
    return Boolean(band._linkUp && band.transport?.sessionWatch && this._believed());
  }

  /** Called by Band after every answer: start the timer when streams are on, stop it when none is. */
  update() {
    if (!this._watching()) return this.stop();
    if (this._timer !== null) return;
    this._reset();
    this._timer = setInterval(() => this._tick(), this.times.tick);
    this._timer.unref?.();      // Node: this timer alone does not keep a program running
  }

  stop() {
    if (this._timer === null) return;
    clearInterval(this._timer);
    this._timer = null;
  }

  /** Called by Band for every frame: a stream frame is a sign of life. */
  heard(type) {
    if (this._timer === null || type < FIRST_STREAM || type >= FIRST_STREAM + 16) return;
    this._heardAt = Date.now();
    this._wait = 1;
  }

  /** A sign that the session may have ended (an empty packet in the middle of a session). */
  hint() {
    this.update();              // the pretend band becomes a watched one when endUsbSession() is called
    if (this._timer !== null && this._hintAt === null) this._hintAt = Date.now();
  }

  _tick() {
    const { band, times } = this;
    const now = Date.now();
    const late = now - this._tickAt - times.tick;
    this._tickAt = now;
    if (!this._watching()) return this.stop();
    if (band._reconnectRun || this._asking) return;
    const mask = this._believed();
    const fastest = fastestFrameMs(band, mask);
    if (late >= times.stall) {
      if (this._hintAt === null) this._hintAt = now;      // this program was stuck: look once frames had time to come
    } else if (late >= times.tick) {
      this._since = now;                                  // a short hiccup: that time is not silence of the band
    }
    if (this._hintAt !== null) {
      const first = this._hintAt + times.settle;          // frames before this may be old ones from a buffer
      if (now < first + Math.min(Math.max(times.settle, 1.5 * (fastest ?? 0)), times.quiet)) return;
      const alive = this._heardAt >= first;
      this._hintAt = null;
      if (alive) return;                                  // data flows: no command
      return this._ask();
    }
    const limit = Math.min(times.quiet, (fastest === null ? times.quiet : Math.max(times.silence, 3 * fastest)) * this._wait);
    if (now - Math.max(this._heardAt, this._since) >= limit) return this._ask();
    return undefined;
  }

  /** The check: one STREAM_GET. Band._remember hands the answer to ended(). */
  async _ask() {
    this._asking = true;
    this.asked++;
    try {
      await this.band._enqueue('STREAM_GET');
    } catch {
      // no answer: the commands of the program show that soon enough
    }
    this._asking = false;
    this._since = Date.now();
    this._wait = Math.min(this._wait * 2, 64);
  }

  /**
   * Called by Band with the answer to every STREAM_GET, before it is used. true = streams
   * this program switched on are off: the band ended the session. The link is then ended
   * with reason 'session-ended' and what auto-reconnect has to set again is kept.
   */
  ended(active) {
    const band = this.band;
    if (!band.transport?.sessionWatch || !band._linkUp || band._reconnectRun) return false;
    if (!(this._believed() & ~active)) return false;
    queueMicrotask(() => this.end());       // after the answer went to whoever asked
    return true;
  }

  /** End the link the normal way, with reason 'session-ended'. */
  async end() {
    const band = this.band;
    if (!band._linkUp || band._closing || band._reconnectRun || this.ending) return;
    this.ending = SESSION_ENDED;
    this.stop();
    try {
      // Close the port properly: the band sees a close, and the next open starts a clean session.
      await Promise.resolve().then(() => band.transport.close()).catch(() => {});
      band._closed(SESSION_ENDED);          // if the transport did not say so itself
    } finally {
      this.ending = null;
    }
  }
}
