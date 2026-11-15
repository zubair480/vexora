// Clap detector. No page and no band in here: you feed it loudness readings and it tells
// you when it heard a clap. That makes it easy to test (see ../test/clap-lights.test.mjs)
// and easy to reuse in another project.
//
// The idea: a clap is not "loud", it is "suddenly much louder than a moment ago". So we
// keep a slow-moving background level, and a clap is a reading that jumps far above it.
// A steady loud room pulls the background up, so it stops counting after the first jump.

// The numbers to tune. RISE_DB is measured on one band on a desk:
//   a quiet room      peakDb about 43 (33 to 48); in one minute never more than 10 dB above the background
//   loud clap-like bursts   peakDb 10 to 17, that is 26 to 32 dB above the background: all counted
//   a steady tone that swells over half a second   18 dB above the background: not counted
// So 20 sits between the swell (18) and the bursts (26). For soft claps try 15 or 16; the
// price is that swelling sounds (somebody starts to talk, music fades in) count as claps too.
// The other numbers are still guesses that work on the pretend band. Not yet measured: real
// hands at the far end of a room, how fast two claps follow each other, a loud room.
export const RISE_DB = 20;          // a clap is at least this many dB above the background (measured, see above)
export const REARM_DB = 8;          // the sound must fall back to this close to the background before the next clap
export const BACKGROUND_MS = 1500;  // how slowly the background follows the room (bigger = slower)
export const REFRACTORY_MS = 120;   // after a clap, ignore everything for this long (the echo)
export const DOUBLE_MS = 800;       // two claps this close together are a double clap
export const SILENCE_DB = 96;       // the band reports dB below its loudest: 0 = loudest, 96 = silence
const GAP_MS = 2000;                // no reading for this long (link lost?): start again

export class ClapDetector {
  constructor() {
    this.reset();
  }

  /** Forget everything. Call it when the link drops: the room may be different afterwards. */
  reset() {
    this.background = null;         // dB above silence, or null before the first reading
    this.lastT = null;              // band time (ms) of the reading before this one
    this.lastClap = -Infinity;      // band time of the last clap
    this.first = null;              // band time of a clap that may still become a double clap
    this.armed = true;              // false from a clap until the sound has dropped again
  }

  /**
   * Feed one reading: `t` is the band's time in ms, `peakDb` the loudest moment since the
   * last reading. Returns 'clap', 'double' (the second clap of a pair) or null.
   */
  feed({ t, peakDb }) {
    const loud = SILENCE_DB - peakDb;                     // turn it round: 0 = silence, 96 = loudest
    const gap = this.lastT === null ? 0 : t - this.lastT;
    if (gap < 0 || gap > GAP_MS) this.reset();            // the band's time jumped: old numbers mean nothing
    this.lastT = t;
    if (this.background === null) this.background = loud;

    const rise = loud - this.background;
    // Move the background a little towards this reading. We use the time between the
    // readings, not their count, so 10 readings a second and 50 a second behave the same.
    this.background += rise * Math.min(1, Math.max(0, gap) / BACKGROUND_MS);

    let heard = null;
    if (this.armed && rise >= RISE_DB && t - this.lastClap >= REFRACTORY_MS) {
      this.armed = false;                                 // one clap can fill two readings: count it once
      this.lastClap = t;
      if (this.first !== null && t - this.first <= DOUBLE_MS) {
        heard = 'double';
        this.first = null;                                // a third clap starts a new pair
      } else {
        heard = 'clap';
        this.first = t;
      }
    } else if (rise < REARM_DB) {
      this.armed = true;
    }
    return heard;
  }
}
