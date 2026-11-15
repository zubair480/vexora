// The parts of the dashboard that do not touch the page: reading the address, choosing
// the words for each problem, and two small helpers for the live data. Kept apart from
// app.js so they can be tested with Node (dashboard/test/).
import { NAME_PREFIX, LED_PATTERNS, bandName, bandId, codec } from '../js/src/index.js';

/** The one sentence shown whenever the microphone owns the LED. */
export const MIC_OWNS_LED = 'The microphone is on, so the band shows a solid blue light and ignores LED commands. '
  + 'Turn off Loudness meter and Stream audio to use the LED.';

/** "7", "07", "007" or " 42 " -> { number, name }; anything that is not a band number -> null. */
export function parseBandParam(raw) {
  const number = codec.parseBandNumber(raw);
  if (number === null) return null;
  return { number, name: bandName(number) };
}

export const isColour = (text) => typeof text === 'string' && /^#[0-9a-f]{6}$/i.test(text);

/**
 * Why Bluetooth cannot be used in this browser, or null if it can.
 * @param {object} facts
 * @param {string|null} facts.reason what the library says (null = Bluetooth works)
 * @param {boolean} facts.secure window.isSecureContext
 * @param {boolean} facts.iOS true on an iPhone or iPad
 */
export function bluetoothProblem({ reason, secure, iOS }) {
  if (!reason) return null;
  if (iOS) {
    return 'iPhones and iPads cannot connect to a band from a web page (no browser on iOS has Web Bluetooth). '
      + 'Open this page on a laptop or an Android phone with Chrome. "Try without a band" still works here.';
  }
  if (secure === false) {
    return 'This page was not opened in a secure way, so the browser blocks Bluetooth and USB. '
      + 'Open it from a web address that starts with https, or from localhost. "Try without a band" still works here.';
  }
  return 'This browser has no Web Bluetooth. Use Chrome or Edge on a laptop or an Android phone. '
    + '"Try without a band" still works here.';
}

/** The band names (prefix + digits) mentioned in a sentence. */
function namesIn(text) {
  const prefix = NAME_PREFIX.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return (String(text).match(new RegExp(`${prefix}\\d+`, 'g')) ?? []).filter((name) => codec.bandNumberOf(name) !== null);
}

// The first thing to check when a band cannot be found: a connected band is invisible to everything else.
const ELSEWHERE = "If something else is connected to it (another dashboard tab, another program, a teammate's laptop), "
  + 'close that first: the band takes one Bluetooth connection at a time and cannot be found while it is connected.';

// ---- a stream that another program took (the library's 'stream-taken' event)
const STREAM_WORDS = {
  motion: 'motion', events: 'tap and gesture', activity: 'step', audio: 'audio', battery: 'battery',
  temperature: 'temperature', link: 'signal', micLevel: 'loudness',
};

/**
 * What the page says when streams it started stopped arriving though it did not stop them.
 * @param {string[]} streams the library's names, e.g. ['motion']
 * @param {boolean} stopped true = the band has stopped them (the other program ended)
 * @param {'ble'|'usb'} link this page's own link
 */
export function streamTakenMessage(streams, stopped, link) {
  const what = streams.map((name) => STREAM_WORDS[name] ?? name).join(', ');
  const other = link === 'usb' ? 'Bluetooth' : 'the cable (USB)';
  const it = streams.length === 1 ? 'this stream' : 'these streams';
  return `No ${what} data is arriving any more: another program on ${other} took ${it}${stopped ? ' and has ended since' : ''}. `
    + 'USB and Bluetooth programs on one band share its streams, its settings and its light, and a stream goes to the program that started it last. '
    + (stopped ? 'Press "Take it back" to get the data back.' : 'Close the other program, then press "Take it back".');
}

/**
 * What to tell the student when connecting failed.
 * @param {Error} error what Band.connect threw
 * @param {object} context
 * @param {'ble'|'serial'|'sim'} context.kind how we tried to connect
 * @param {string} context.wanted the name of the band this page is for
 */
export function connectProblem(error, { kind, wanted }) {
  const usb = kind === 'serial';
  switch (error?.code) {
    case 'cancelled':
      return usb
        ? 'No USB port was picked. Plug the band in with a data cable (not a charge-only one), then press Connect by USB cable again. '
          + 'On a Mac, the first time you plug a band in, macOS may ask "Allow accessory to connect?": press Allow, or no port shows up.'
        : `No band was picked. Was ${wanted} not in the list? ${ELSEWHERE} Then double-tap ${wanted} to wake it, press Connect and pick it from the list. Still not there, and this band had another number before? Add &list=all to the address: the list then shows every band nearby, maybe under its old name, and the dashboard checks it is really ${wanted}.`;
    case 'not-found-nearby':
      return `Could not reach ${wanted}. ${ELSEWHERE} Then double-tap the band to wake it, keep it close, and press Connect again.`;
    case 'wrong-band': {
      const found = namesIn(error.message).find((name) => name !== wanted);
      if (!found) return error.message;
      if (found === bandName(0)) {
        return `That band has no number yet (it calls itself ${found}), but this page is for ${wanted}. Ask your teacher to set its number.`;
      }
      return `That band is ${found}, but this page is for ${wanted}. `
        + `Check the number printed on your band, or open this page for band ${bandId(codec.bandNumberOf(found))}.`;
    }
    case 'timeout':
      return usb
        ? `${wanted} did not answer in time. If you picked another port than "Vexo Open band", press Connect by USB cable and pick that one. `
          + 'Otherwise unplug the cable, plug it in again, and try once more.'
        : `${wanted} did not answer in time. Keep it close, double-tap it to wake it, and press Connect again.`;
    case 'not-connected':
      return usb
        ? 'The cable link closed while connecting. Plug the band in again and press Connect by USB cable.'
        : `The link closed while connecting. Double-tap ${wanted} to wake it and press Connect again.`;
    default:
      if (error?.name === 'VexoError') return error.message;
      return `Could not connect: ${error?.message ?? error}`;
  }
}

/** What to tell the student when a command to a connected band failed. */
export function commandProblem(error) {
  switch (error?.code) {
    case 'denied': return MIC_OWNS_LED;
    case 'timeout': return 'The band did not answer in time. Is it still close by and awake? Try again.';
    case 'not-connected': return 'The band is not connected any more.';
    default: return error?.message ?? String(error);
  }
}

/** "2 minutes", "1 minute", "45 seconds" */
function duration(seconds) {
  if (seconds >= 60 && seconds % 60 === 0) return seconds === 60 ? '1 minute' : `${seconds / 60} minutes`;
  return seconds === 1 ? '1 second' : `${seconds} seconds`;
}

// USB cable: the band ended the session by itself (the library's 'session-ended' reason).
const SESSION_ENDED = 'The band ended the USB session because this page took no data for 5 seconds (the page was stuck, or the computer was busy). The streams were stopped.';

/** The status line while the library tries to get a lost link back. `seconds` comes from the "reconnecting" event. */
export function reconnectingMessage(seconds, reason = 'lost') {
  if (reason === 'session-ended') return { kind: 'error', text: `${SESSION_ENDED} Connecting again...` };
  return { kind: 'error', text: `Connection lost. Trying to reconnect for ${duration(seconds)}...` };
}

/**
 * The status line when the link is back. `failed` comes from the "reconnected" event: the
 * settings the band refused when the library sent them again (normally none).
 */
export function reconnectedMessage(failed = [], reason = 'lost') {
  // After a session end the link is back within a moment: the reason stays on the page, or nobody would see it.
  if (!failed.length && reason === 'session-ended') return { kind: 'info', text: `${SESSION_ENDED} It is connected again and the streams are back.` };
  if (!failed.length) return { kind: 'info', text: 'Reconnected.' };
  return { kind: 'error', text: 'Reconnected, but not everything could be set again. Disconnect and connect again to retry.' };
}

/**
 * What to tell the student when the link ended. `kind` is 'info' or 'error'.
 * A link that auto-reconnect gave up on arrives here as 'lost', like any lost link.
 */
export function disconnectMessage(reason, name) {
  switch (reason) {
    case 'wrong-band':
      return { kind: 'error', text: `Another band answered instead of ${name}. Press Connect again and pick ${name}.` };
    case 'closed':
      return { kind: 'info', text: 'Disconnected.' };
    case 'session-ended':
      return { kind: 'error', text: `${SESSION_ENDED} Press Connect again.` };
    case 'restart':
      return { kind: 'info', text: `${name} is restarting. Wait a few seconds, then connect again.` };
    case 'sleep':
      return { kind: 'info', text: `${name} went to sleep. Double-tap it to wake it, then connect again.` };
    default:
      return {
        kind: 'error',
        text: `The link to ${name} was lost. Press Connect again. If the band does not show up, double-tap it first to wake it.`,
      };
  }
}

/**
 * The streams the dashboard turns on after connecting, one at a time, as [label, configure keys].
 * The microphone is not in the list: the loudness meter and audio stay off until the student
 * switches them on, because either one turns the band's light blue and locks the LED.
 */
export function startupStreams(kind) {
  const list = [
    ['motion', { motion: 50 }],
    ['events', { events: true }],
    ['battery', { battery: true }],
    ['temperature', { temperature: true }],     // true = the band's own pace, every 5 seconds; works on firmware 1.0 and 1.1
    ['steps', { activity: true }],              // true = the band's own pace, once a second; works on firmware 1.0 and 1.1
  ];
  // Signal strength only exists over Bluetooth. Over USB the band refuses a start that asks for it.
  if (kind !== 'serial') list.push(['signal', { link: true }]);
  return list;
}

/**
 * The message after connecting and setting up.
 * @param {object} result
 * @param {string} result.name band name
 * @param {'ble'|'serial'|'sim'} result.kind
 * @param {string|null} result.flashProblem why the band did not flash, or null if it did
 * @param {string[]} result.failed labels of the streams that did not start
 */
export function setupMessage({ name, kind, flashProblem, failed }) {
  const parts = [kind === 'sim'
    ? 'This is a pretend band making up data, so you can see how the page works.'
    : `Connected to ${name}.`];
  if (flashProblem) parts.push(flashProblem);
  else if (kind !== 'sim') parts.push('It should be flashing white.');
  if (failed.length) parts.push(`These did not start: ${failed.join(', ')}. Disconnect and connect again to retry.`);
  return { kind: flashProblem || failed.length ? 'error' : 'info', text: parts.join(' ') };
}

/** One line for the events list: "double-tap", "orientation: +z side up". */
export function eventLabel(event) {
  if (event.kind === 'orientation' && event.face) return `orientation: ${event.face} side up`;
  return event.kind;
}

const BATTERY_WORDS = { battery: 'on battery', charging: 'charging', charged: 'fully charged' };

/** "4.08 V, on battery" */
export function batteryDetail({ millivolts, state }) {
  return `${(millivolts / 1000).toFixed(2)} V, ${BATTERY_WORDS[state] ?? state}`;
}

/** "26.8" for the chip temperature in degrees C; "–" when there is no reading. */
export function temperatureText(celsius) {
  return typeof celsius === 'number' && Number.isFinite(celsius) ? celsius.toFixed(1) : '–';
}

/** What the page says under the temperature, so nobody takes it for the room or a wrist. */
export const TEMPERATURE_NOTE = 'This is the temperature of the chip inside the band, a new reading every 5 seconds. '
  + 'It is a few degrees above the room because the chip warms itself. It is not your skin temperature.';

// ---- steps (the band's `activity` stream sends steps, cadence and state)

/**
 * Steps since the page connected. The band sends a lifetime total (`steps` of an `activity`
 * event) that goes on counting over restarts, so the page subtracts the first total it sees.
 */
export class StepCounter {
  constructor() {
    this.reset();
  }

  reset() {
    this.steps = null;      // null = no reading yet
    this._first = null;
    this._last = null;
    this._before = 0;       // steps counted before the band's total started again
  }

  /** Take the total of one `activity` event; returns the steps since the first one. */
  add(total) {
    if (typeof total !== 'number' || !Number.isFinite(total)) return this.steps;
    if (this._first === null) this._first = total;
    if (this._last !== null && total < this._last) {      // the band's total started again: keep what was counted
      this._before += this._last - this._first;
      this._first = total;
    }
    this._last = total;
    this.steps = this._before + total - this._first;
    return this.steps;
  }
}

/** "128" for the step count; "–" when there is no reading. */
export function stepsText(steps) {
  return typeof steps === 'number' && Number.isFinite(steps) ? String(steps) : '–';
}

const ACTIVITY_WORDS = { still: 'Standing still', walk: 'Walking', run: 'Running' };

/** "Walking, 112 steps a minute." from an `activity` event's state and cadence. */
export function activityDetail({ state, cadence }) {
  const words = ACTIVITY_WORDS[state];
  if (!words) return 'Waiting for the band.';
  return state !== 'still' && cadence > 0 ? `${words}, ${cadence} steps a minute.` : `${words}.`;
}

/** What the page says under the step count. */
export const STEPS_NOTE = 'Steps since this page connected. The band counts them with its motion sensor and sends its total once a second: a lifetime total that goes on from one connection to the next. This page takes away the first total it gets.';

// ---- a connection attempt that takes long, or is cancelled

/** After this long on "Connecting…" the page says what to do. */
export const STILL_CONNECTING_MS = 15000;

/** What the page says when a connection attempt is still open after STILL_CONNECTING_MS. */
export function stillConnectingMessage(kind, wanted) {
  return kind === 'serial'
    ? 'Still waiting for a port to be picked. Pick "Vexo Open band" in the browser\'s list. No list on the screen? Press Cancel and try again.'
    : `Still waiting for ${wanted}. Pick it in the browser's list. Not in the list? Close whatever else is connected to it (another tab, another program, a teammate's laptop), `
      + 'then double-tap the band. No list on the screen? Press Cancel and try again.';
}

/** What the page says after Cancel. */
export const CANCELLED_MESSAGE = 'Cancelled. Press Connect to try again.';

// ---- the loudness bar
//
// The band reports loudness as rms_db: decibels below the loudest sound its microphone can
// measure (0 = loudest, 96 = digital silence). The library's `level` spreads all 96 dB over
// 0..1, but a real band never gets near 96: measured on a real band at its standard gain, a
// quiet room reads 49 to 52 and the loudest moment of a spoken sentence 19 to 27. On that
// scale the bar would sit half full in silence and barely move for speech. So the bar shows
// only the part where things happen:
/** rms_db at which the bar is empty: just under the hiss of a quiet room. */
export const BAR_EMPTY_DB = 52;
/** rms_db at which the bar is full: louder than normal speech near the band. */
export const BAR_FULL_DB = 12;

/**
 * How full to draw the loudness bar, 0..1, for a `mic-level` event's rmsDb.
 * Quiet room (about 50) -> about 0.05; speech (about 26) -> about 0.65; 12 or louder -> 1.
 */
export function loudnessBar(rmsDb) {
  if (typeof rmsDb !== 'number' || Number.isNaN(rmsDb)) return 0;
  return Math.min(1, Math.max(0, (BAR_EMPTY_DB - rmsDb) / (BAR_EMPTY_DB - BAR_FULL_DB)));
}

/** What the page says under the bar. */
export const LOUDNESS_NOTE = 'The bar is empty in a quiet room, about two thirds full when someone speaks near the band, '
  + `and full for very loud sound. It shows the band's loudness reading from ${BAR_EMPTY_DB} (empty) to ${BAR_FULL_DB} (full) `
  + 'decibels below the loudest sound the microphone can measure.';

/** Shown in the Microphone panel while the pretend band is connected. */
export const PRETEND_MIC_NOTE = 'Pretend band: there is no real microphone. Listen and Live transcript get a recording of a person reading aloud '
  + 'that is built into this page, and the loudness bar moves by itself. None of it is the sound in your room.';

/** A word for a signal strength in dBm. */
export function signalWords(rssi) {
  if (typeof rssi !== 'number') return 'no reading';
  if (rssi >= -60) return 'strong';
  if (rssi >= -75) return 'good';
  if (rssi >= -85) return 'weak';
  return 'very weak';
}

/**
 * How to draw the pretend band's light. `led` is what the simulator reports:
 * { r, g, b, brightness 0..255, pattern (a code), period_ms, source }.
 */
export function ledView(led) {
  const pattern = LED_PATTERNS[led.pattern] ?? 'off';
  const k = pattern === 'off' ? 0 : led.brightness / 255;
  const [r, g, b] = [led.r, led.g, led.b].map((part) => Math.round(part * k));
  const dark = pattern !== 'rainbow' && r + g + b === 0;
  let text;
  if (led.source === 'mic') text = 'solid blue: the microphone is on';
  else if (led.source === 'identify') text = 'flashing white so you can find it';
  else text = dark ? 'off' : pattern;
  return { colour: `rgb(${r}, ${g}, ${b})`, pattern: dark ? 'off' : pattern, periodMs: led.period_ms, source: led.source, text };
}

/** The last `size` samples, without moving memory around on every new one. */
export class History {
  constructor(size, fields) {
    this.size = size;
    this.fields = fields;
    this.length = 0;
    this._next = 0;
    this._data = Object.fromEntries(fields.map((f) => [f, new Float32Array(size)]));
  }

  push(sample) {
    for (const f of this.fields) this._data[f][this._next] = sample[f];
    this._next = (this._next + 1) % this.size;
    if (this.length < this.size) this.length++;
  }

  /** Value of one field; i = 0 is the oldest sample kept, length - 1 the newest. */
  get(field, i) {
    return this._data[field][(this._next - this.length + i + this.size) % this.size];
  }

  clear() {
    this.length = 0;
    this._next = 0;
  }
}

/** Counts frames and bytes, and turns them into "per second" when asked. */
export class RateMeter {
  constructor(now = 0) {
    this.reset(now);
  }

  reset(now = 0) {
    this._frames = 0;
    this._bytes = 0;
    this._since = now;
  }

  add(bytes) {
    this._frames++;
    this._bytes += bytes;
  }

  /** { framesPerSecond, bytesPerSecond } since the last call, or null if too little time has passed. */
  take(now) {
    const ms = now - this._since;
    if (ms < 250) return null;
    const rates = {
      framesPerSecond: Math.round((this._frames * 1000) / ms),
      bytesPerSecond: Math.round((this._bytes * 1000) / ms),
    };
    this.reset(now);
    return rates;
  }
}

// ---- the Motion panel: which rates and sensors this band offers on this link

/** What the dashboard starts motion with. The same on every band, so a firmware 1.0 band behaves as it always did. */
export const DEFAULT_MOTION = Object.freeze({ rate: 50, sensors: 'both' });

const SENSOR_LABELS = { both: 'Both', accel: 'Acceleration only', gyro: 'Turning (gyro) only' };
const SENSOR_WORDS = { both: 'both sensors', accel: 'acceleration only', gyro: 'turning only' };
const LINK_WORDS = { ble: 'Bluetooth', usb: 'the USB cable' };
// What the USB cable carries (docs/api.md, Fast motion). Only used to word why a rate is
// off over Bluetooth; what is on offer always comes from the band, through motionLimits().
const USB_CAPS = { accel: 1666, gyro: 833, both: 833 };

/**
 * Extra choices for the pretend band, read from the page address. Only used by "Try without a band".
 *   ?firmware=1.0   a pretend band with the older firmware 1.0 (default: 1.1)
 *   ?link=usb       pretend it is on the USB cable (default: Bluetooth)
 * @param {URLSearchParams} params
 * @returns {{firmware?: '1.0', via?: 'usb'}} options for SimTransport
 */
export function pretendOptions(params) {
  const options = {};
  if (params.get('firmware') === '1.0') options.firmware = '1.0';
  if (params.get('link') === 'usb') options.via = 'usb';
  return options;
}

/** 'ble' or 'usb': the kind of link, for the words in the Motion panel. `via` is what a pretend band imitates. */
export function linkOf(kind, via) {
  if (kind === 'sim') return via === 'usb' ? 'usb' : 'ble';
  return kind === 'serial' ? 'usb' : 'ble';
}

/** The fastest rate for a sensor choice. `limits` is what band.motionLimits() returned. */
const capOf = (limits, sensors) => limits[sensors] ?? limits.both;

/** The sensor picker: [{ value, label, enabled }]. On firmware 1.0 only Both is enabled. */
export function sensorChoices(limits) {
  return ['both', 'accel', 'gyro'].map((value) => ({ value, label: SENSOR_LABELS[value], enabled: typeof limits[value] === 'number' }));
}

/** Why a rate is not on offer with the sensors chosen now, in a few plain words. */
function whyNot(rate, limits, link) {
  const where = LINK_WORDS[link] ?? 'this link';
  const single = (caps) => ['accel', 'gyro'].filter((name) => typeof caps[name] === 'number' && rate <= caps[name]);
  const here = single(limits);
  if (here.length === 2) return `only with one sensor on ${where}`;
  if (here.length === 1) return `${SENSOR_WORDS[here[0]]} on ${where}`;
  if (link === 'ble') {
    if (rate <= USB_CAPS.both) return 'USB cable only';
    const cable = single(USB_CAPS);
    if (cable.length === 2) return 'USB cable, one sensor';
    if (cable.length === 1) return `USB cable, ${SENSOR_WORDS[cable[0]]}`;
  }
  return `too fast for ${where}`;
}

/**
 * The rate picker: one entry for every rate the band knows, [{ rate, enabled, reason, label }].
 * A rate above what this link carries with these sensors stays in the list, switched off,
 * with the reason in its label: "833: only with one sensor on Bluetooth".
 * @param {object} limits what band.motionLimits() returned
 * @param {'both'|'accel'|'gyro'} sensors the sensor choice now
 * @param {'ble'|'usb'} link see linkOf()
 */
export function rateChoices(limits, sensors, link) {
  const cap = capOf(limits, sensors);
  return limits.rates.map((rate) => {
    if (rate <= cap) return { rate, enabled: true, reason: '', label: String(rate) };
    const reason = whyNot(rate, limits, link);
    return { rate, enabled: false, reason, label: `${rate}: ${reason}` };
  });
}

/**
 * The rate to use after the sensors changed: the same one if it is still allowed, else the
 * fastest allowed, with a sentence that says so. Returns { rate, text } (text '' = kept).
 * `link` is 'ble' or 'usb', for the words.
 */
export function fallbackRate(limits, sensors, rate, link) {
  const cap = capOf(limits, sensors);
  if (limits.rates.includes(rate) && rate <= cap) return { rate, text: '' };
  const fastest = Math.max(...limits.rates.filter((r) => r <= cap));
  return {
    rate: fastest,
    text: `${rate} samples per second is too fast with ${SENSOR_WORDS[sensors]} on ${LINK_WORDS[link] ?? 'this link'}. `
      + `Now ${fastest}, the fastest allowed.`,
  };
}

/** One line under the pickers: what this band's firmware can do on this link. */
export function motionHint(limits, link, pretend = false) {
  if (typeof limits.accel !== 'number') {
    return `This band has firmware 1.0: ${limits.rates.join(', ')} samples per second with both sensors. `
      + 'Faster rates and one sensor alone need band firmware 1.1.';
  }
  const one = limits.accel === limits.gyro
    ? `${limits.accel} with one sensor`
    : `${limits.accel} with acceleration only, ${limits.gyro} with turning only`;
  const most = `Over ${LINK_WORDS[link] ?? 'this link'} the most is ${limits.both} samples per second with both sensors, ${one}.`;
  return pretend
    ? `${most} This pretend band has firmware 1.1.`
    : `${most} Fast rates come with band firmware 1.1. They have been tested on a real band: 208, 416 and 833 samples per second, and 1666 with acceleration only over the USB cable.`;
}

/** What to show when changing the motion setting failed. The library's "too fast" and "needs firmware 1.1" messages are shown as they are. */
export function motionProblem(error) {
  if (['denied', 'not-available', 'bad-arg'].includes(error?.code)) return error.message;
  return commandProblem(error);
}

/** The sentence for a "stream-dropped" notice about motion (arg 0 or 8 = the two motion streams), or null for another stream. */
export function motionDroppedMessage(arg) {
  if (arg !== 0 && arg !== 8) return null;
  return 'The band had to drop motion samples: the link could not keep up. Pick a lower rate or one sensor.';
}

/** How many samples the graph keeps: about 5 seconds at this rate. */
export const historySize = (rate) => Math.max(50, Math.round(rate * 5));

const N_SPAN = 2 ** 32;     // sample numbers start again at 0 after this many

/**
 * Counts lost motion samples from gaps in `n`, the number firmware 1.1 puts on every sample.
 * A band with firmware 1.0 does not number its samples (n is null): `reported` stays false.
 */
export class LossCounter {
  constructor() {
    this.reset();
  }

  reset() {
    this.lost = 0;
    this.reported = false;
    this._next = null;
  }

  add(n) {
    if (typeof n !== 'number') return;
    this.reported = true;
    if (this._next !== null) {
      const gap = (n - this._next + N_SPAN) % N_SPAN;
      if (gap < N_SPAN / 2) this.lost += gap;     // a step back means the numbers started again, not a loss
    }
    this._next = (n + 1) % N_SPAN;
  }
}

/**
 * Samples per second that really arrive: how many came in, divided by the time the band
 * says passed between them (`t`). So lost samples lower it, and a motion chip that runs a
 * little off its nominal rate shows up too.
 */
export class ArrivalRate {
  constructor(now = 0) {
    this.reset(now);
  }

  reset(now = 0) {
    this._count = 0;
    this._first = null;     // t of the sample before the first counted one
    this._last = null;
    this._heard = now;      // computer time of the last take() that had samples
  }

  add(t) {
    if (this._first === null) {
      if (this._last === null) {
        this._last = t;
        return;                   // the very first sample only marks the start
      }
      this._first = this._last;
    }
    this._count++;
    this._last = t;
  }

  /**
   * Samples per second since the last call. null = not enough to say yet (keep what is
   * shown); 0 = nothing has arrived for `quietMs` of computer time.
   */
  take(now, quietMs = 2500) {
    if (!this._count) return now - this._heard >= quietMs ? 0 : null;
    const ms = (this._last - this._first + N_SPAN) % N_SPAN;
    const rate = ms > 0 ? (this._count * 1000) / ms : null;
    this._count = 0;
    this._first = null;
    this._heard = now;
    return rate;
  }
}

/**
 * For drawing many samples on few pixels: cut the whole window (history.size samples) in
 * `columns` equal slices and write the smallest and largest value of each into lo and hi.
 * Returns how many slices hold samples (they fill from the left). Needs size >= columns.
 */
export function envelope(history, field, columns, lo, hi) {
  let filled = 0;
  for (let c = 0; c < columns; c++) {
    const from = Math.floor((c * history.size) / columns);
    const to = Math.min(history.length, Math.floor(((c + 1) * history.size) / columns));
    if (from >= to) break;
    let min = Infinity;
    let max = -Infinity;
    for (let i = from; i < to; i++) {
      const v = history.get(field, i);
      if (v < min) min = v;
      if (v > max) max = v;
    }
    lo[c] = min;
    hi[c] = max;
    filled++;
  }
  return filled;
}
