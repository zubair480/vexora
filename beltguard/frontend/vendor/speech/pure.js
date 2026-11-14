// The parts of Listen and Live transcript that are only arithmetic or words: following the
// packet counter, measuring how far behind the text is, when to play the next piece of
// sound, and the words for the status lines. Nothing here touches the page or the network,
// so Node can test all of it (dashboard/test/speech.test.mjs, deepgram.test.mjs).

export const SOUND_RATE = 16000;          // samples per second of the band's microphone
export const FRAME_MS = 20;               // one Opus packet from the band

/**
 * Live transcript has one engine: Deepgram, a speech-to-text company on the internet. It
 * needs the team's own key and sends the band's sound to that company. Nothing of it is
 * loaded or contacted until Live transcript is switched on with a key (online.js, deepgram.js).
 */
export const COMPANY = 'Deepgram';
/** A key as the page accepts it: letters, digits and . _ ~ - only, no spaces, at most 200. */
export const looksLikeKey = (text) => /^[\w.~-]{1,200}$/.test(String(text ?? ''));

/** The pretend band's voice: a person reading SAMPLE_SENTENCE aloud, a public-domain recording (see README.md here). */
export const SAMPLE_FILE = 'sample-speech.opus';
export const SAMPLE_SECONDS = 16;         // how long the sample is; the pretend band plays it again and again
export const SAMPLE_SENTENCE = 'In a spell of dry weather, when the birds could find very little to drink, a thirsty crow found '
  + 'a pitcher with a little water in it. But the pitcher was high and had a narrow neck, and no matter how he tried, '
  + 'the crow could not reach the water.';

// ---- reading an Ogg Opus file, following the packet counter
// These live in the kit's library (js/src/audio.js), where a project can use them too.
export { oggPackets, SeqTracker, MAX_FILL, lostPacket } from '../../js/src/audio.js';

// ---- when did this sound arrive? (to measure how far behind the text is)

/** Remembers when each piece of sound arrived, so "sample number" can be turned back into a time. */
export class ArrivalClock {
  constructor(size = 4096) {
    this._size = size;
    this.reset();
  }

  reset() {
    this._ends = [];
    this._times = [];
    this.position = 0;
  }

  /** `samples` more samples arrived at time `now` (ms). */
  push(samples, now) {
    this.position += samples;
    this._ends.push(this.position);
    this._times.push(now);
    if (this._ends.length > this._size) {
      this._ends.splice(0, this._size / 2);
      this._times.splice(0, this._size / 2);
    }
  }

  /** When did the sample with this number arrive? null if it is too old or has not arrived. */
  timeOf(sample) {
    const ends = this._ends;
    if (!ends.length || sample > this.position) return null;
    let low = 0;
    let high = ends.length - 1;
    while (low < high) {
      const mid = (low + high) >> 1;
      if (ends[mid] >= sample) high = mid;
      else low = mid + 1;
    }
    return this._times[low];
  }
}

/** Keeps the last few lags to show a calm number, and remembers the worst one. */
export class LagMeter {
  constructor() {
    this.reset();
  }

  reset() {
    this._last = [];
    this.worst = 0;
    this.count = 0;
  }

  add(seconds) {
    if (!(seconds >= 0)) return;
    this._last.push(seconds);
    if (this._last.length > 5) this._last.shift();
    this.worst = Math.max(this.worst, seconds);
    this.count++;
  }

  /** The middle one of the last few lags, in seconds, or null before the first text. */
  get typical() {
    if (!this._last.length) return null;
    const sorted = [...this._last].sort((a, b) => a - b);
    return sorted[sorted.length >> 1];
  }
}

// ---- playing: when to start the next piece of sound

export const PLAY = { lead: 0.2, most: 0.8 };     // seconds of sound kept ready; more than `most` is skipped

/**
 * Sound arrives in bursts, so playing keeps a little ready (a jitter buffer).
 * @param {number} now the audio clock, seconds
 * @param {number} queuedUntil when the sound already queued runs out, seconds
 * @returns {{ at: number|null, late: boolean }} at = when to start this piece (null = skip
 *   it, too much is waiting); late = the queue had run dry, so there was a short silence
 */
export function playAt(now, queuedUntil, { lead, most } = PLAY) {
  if (queuedUntil <= now) return { at: now + lead, late: true };
  if (queuedUntil - now > most) return { at: null, late: false };
  return { at: queuedUntil, late: false };
}

// ---- words for the status lines

export const NO_DECODER = 'This browser cannot decode the band\'s sound (it has no WebCodecs audio decoder for Opus), '
  + 'so Listen is not available here. Use a current Chrome or Edge, and open the page '
  + 'from localhost or an https address. Everything else on this page works.';

export const EXAMPLE_NOTE = 'Listen and Live transcript are a starting example of what you can do with the band\'s sound, '
  + 'not a finished product. Both are off until you switch them on. Listen plays the sound on this computer and sends it nowhere. '
  + `Live transcript uses ${COMPANY}, a speech service on the internet: it needs your team's key, and the sound goes to that company. `
  + 'Without a key everything else on this page works as before. docs/deepgram.md in the kit explains the key and the rules for it; '
  + 'docs/speech.md shows how to use the sound in your own program.';

/** Shown next to the key field, always: what switching Live transcript on means. */
export const SENDING_WARNING = `Live transcript does not stay on this computer. While it is on, everything the band's `
  + `microphone hears, from everyone near the band, is sent over the internet to ${COMPANY}, a speech-to-text company. `
  + 'Tell the people around you before you switch it on. The band\'s solid blue light means the microphone is on. '
  + `Use your own team's ${COMPANY} key. A key typed here is seen by this page only: it is kept in the memory of this open page, `
  + 'not stored and not shown. Reload or close the page, or press "Forget key", and it is gone. '
  + 'Never put a key in code, a commit, a chat or a screenshot.';

/** Shown the whole time sound is going to the company. */
export const CONSENT_LINE = `The band's microphone is being sent to ${COMPANY} now. Switch Live transcript off to stop.`;

/** The line at the foot of the page. `sending`: sound is going to the company right now. */
export const footerNote = (sending) => (sending
  ? `Live transcript is sending the band's microphone to ${COMPANY}. Everything else on this page runs in your browser.`
  : 'Everything on this page runs in your browser. Nothing is sent anywhere.');

/** The line under the key field. `asked`: Live transcript was switched on without a key. */
export const keyStatus = ({ hasKey, refused, asked }) => {
  if (refused) return 'That does not look like a key: a key is letters and digits, with no spaces. Nothing was kept.';
  if (hasKey) return `A ${COMPANY} key is in this page's memory. It is not stored: reload or close the page, or press "Forget key", and it is gone.`;
  if (asked) return `Live transcript needs a ${COMPANY} key first. Paste your team's key into the field above; docs/deepgram.md in the kit says where it comes from. `
    + 'Everything else on this page works without one.';
  return `No key yet. Paste your team's ${COMPANY} key into the field above. It is not shown and not stored.`;
};

export const NO_KEY = `Live transcript needs a ${COMPANY} key. Paste your team's key into the key field first (docs/deepgram.md in the kit).`;
/** Why the link to the company ended by itself. Fixed sentences: nothing the service says is repeated on the page. */
export const ONLINE_PROBLEMS = {
  refused: `${COMPANY} refused the connection. The usual reasons: the key is wrong or has expired, the key has no credit left, `
    + 'too many connections are open at once, or this network blocks this kind of connection (try another network or a phone hotspot).',
  closed: `The connection to ${COMPANY} closed. Check the internet connection, then switch Live transcript on again.`,
  reported: `${COMPANY} reported a problem and stopped. Switch Live transcript on again.`,
  frame: `The part of the page that talks to ${COMPANY} could not be loaded.`,
};

const seconds = (s) => (s < 10 ? s.toFixed(1) : String(Math.round(s)));

/**
 * The status line under the Live transcript switch.
 * @param {object} s
 * @param {'off'|'connecting'|'listening'|'error'} s.state
 * @param {boolean} [s.hasKey] a key is in the page's memory
 * @param {number|null} [s.lag] how far the text is behind the speech, seconds
 * @param {boolean} [s.pretend] the sound is the pretend band's recording, not a room
 * @param {boolean} [s.sound] sound is arriving from the band
 * @param {string} [s.problem]
 */
export function transcriptStatus(s) {
  switch (s.state) {
    case 'connecting':
      return `Connecting to ${COMPANY}...`;
    case 'listening': {
      if (!s.sound) return `Connected to ${COMPANY}. Waiting for sound from the band...`;
      const lag = s.lag === null || s.lag === undefined ? ''
        : ` The text is ${s.lag < 0.05 ? 'less than 0.1' : `about ${seconds(s.lag)}`} s behind the speech.`;
      if (s.pretend) return `Listening to the pretend band's recording through ${COMPANY}; it repeats every ${SAMPLE_SECONDS} seconds.${lag}`;
      return `Listening through ${COMPANY}.${lag}`;
    }
    case 'error':
      return `Live transcript stopped. ${s.problem ?? ''}`.trim();
    default:
      if (!s.hasKey) return `Live transcript is off. It needs your team's ${COMPANY} key: paste it into the key field below (docs/deepgram.md in the kit).`;
      return `Live transcript is off. Switching it on sends the band's sound to ${COMPANY}.`;
  }
}

/**
 * The status line under the Listen switch.
 * @param {object} s
 * @param {boolean} s.on
 * @param {boolean} [s.sound] sound is arriving from the band
 * @param {boolean} [s.blocked] the browser has not let the page play sound yet
 * @param {number} [s.delay] seconds between the band hearing a sound and the speakers playing it
 * @param {number} [s.lost] packets lost on the way so far
 */
export function listenStatus(s) {
  if (!s.on) return 'Listen is off.';
  if (s.blocked) return 'The browser is holding the sound back. Click anywhere on this page to let it play.';
  if (!s.sound) return 'Waiting for sound from the band...';
  const lost = s.lost ? ` ${s.lost} lost ${s.lost === 1 ? 'packet was' : 'packets were'} smoothed over.` : '';
  return `Playing the band's microphone on this computer, about ${seconds(s.delay ?? 0)} s behind.${lost} `
    + 'Keep the band away from the speakers, or use headphones.';
}
