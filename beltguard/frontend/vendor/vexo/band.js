// Band: the friendly API in docs/api.md. It turns method calls into contract commands
// (through codec.js), sends them one at a time over a transport, and turns the frames
// that come back into events with everyday units (g, degrees/second, Celsius, 0..1).
import {
  bandName, encodeCommand, decodeFrame, wrongLengthResponse,
  CAPABILITIES, EVENT_KINDS, LED_PATTERNS, NOTICE_KINDS, STREAM_BITS,
} from './codec.js';
import { VexoError, shortErrors } from './transport.js';
import { SimTransport } from './transports/sim.js';
import { WebBluetoothTransport } from './transports/web-bluetooth.js';
import { WebSerialTransport } from './transports/web-serial.js';
import { SessionWatch, SESSION_ENDED, sessionEndedText } from './transports/session-watch.js';
import { StreamWatch, STREAM_TAKEN } from './stream-watch.js';

const TRANSPORTS = {
  ble: (number) => new WebBluetoothTransport(number),
  serial: () => new WebSerialTransport(),
  sim: (number) => new SimTransport(number ?? undefined),
};

const DEFAULT_TIMEOUT_MS = 2000;
// The most async handlers that may be running at once: the number the Python library uses
// for waiting events (MAX_WAITING). See _emit.
const MAX_BUSY = 5000;
const KEPT_EVENTS = ['disconnect', 'reconnecting', 'reconnected', STREAM_TAKEN];
const DROP_HELP = `Your async handlers are slower than the band's data: ${MAX_BUSY} are still running. From now on new `
  + 'events are dropped while that many run. Keep handlers short. band.droppedEvents counts the dropped events.';
const DEFAULT_RECONNECT_SECONDS = 120;      // a dropped band can be found again for 2 minutes
const EXPECTED_CLOSE_MS = 3000;             // after restart() or sleep(), a close this soon is no surprise

// Auto-reconnect sends the settings you made again, in this order. The LED comes before
// the streams, because the microphone streams lock the LED.
// The fast motion setting and the stream intervals (firmware 1.1) follow, then the streams.
const RESTORE_ORDER = ['IMU_SET', 'EVENT_SET', 'MIC_SET_GAIN', 'RADIO_SET_TX_POWER', 'TEMP_SET_INTERVAL', 'LED_SET', 'MOTION2_SET'];

// configure() key -> bit in the STREAM_START / STREAM_STOP mask
const STREAM_KEYS = {
  motion: STREAM_BITS.motion, events: STREAM_BITS.event, activity: STREAM_BITS.activity,
  audio: STREAM_BITS.audio, battery: STREAM_BITS.battery, temperature: STREAM_BITS.temp,
  link: STREAM_BITS.link, micLevel: STREAM_BITS.mic_level,
};

// Every name band.on() knows: the data events, one for each gesture, and the link's own.
const EVENT_NAMES = ['motion', 'event', ...Object.values(EVENT_KINDS), 'activity', 'audio', 'battery', 'temperature',
  'link', 'mic-level', 'notice', 'disconnect', 'reconnecting', 'reconnected', STREAM_TAKEN];

/** Throws 'bad-event' for a name on() and off() do not know (a typo would otherwise wait for ever). */
function checkEvent(type) {
  if (typeof type === 'string' && EVENT_NAMES.includes(type)) return;
  const kebab = typeof type === 'string' ? type.trim().replace(/([a-z])([A-Z])/g, '$1-$2').toLowerCase().replaceAll('_', '-') : '';
  const near = { events: 'event', miclevel: 'mic-level', doubletap: 'double-tap' }[kebab] ?? kebab;
  const hint = EVENT_NAMES.includes(near) ? ` Did you mean "${near}"?` : '';
  throw new VexoError('bad-event', `Unknown event "${String(type)}".${hint} Events: ${EVENT_NAMES.join(', ')}.`);
}

const MOTION_RATES = [10, 25, 50, 100];                         // firmware 1.0
const FAST_RATES = [10, 25, 50, 100, 208, 416, 833, 1666];      // firmware 1.1 (fast motion)
const SENSORS = ['accel', 'gyro', 'both'];                      // position + 1 = the contract's `sensors` number
const USB_CAPS = { accel: 1666, gyro: 833, both: 833 };          // the most the USB cable carries (for the advice in messages)
const BIT_MOTION = 1 << STREAM_BITS.motion;
const BIT_MOTION2 = 1 << STREAM_BITS.motion2;
const BIT_AUDIO = 1 << STREAM_BITS.audio;
// The streams that are sent on a timer: configure() takes seconds for them, and so does setInterval().
const INTERVAL_KEYS = ['activity', 'battery', 'temperature', 'link', 'micLevel'];
const MIC_LEVEL_STEP_MS = 20;
const FASTEST = 'fastest';              // instead of a number: the highest motion rate, or the shortest interval, this band can do
// Firmware 1.0 sends these streams at one fixed pace (contract 4.3), in seconds; 0 = only when it changes.
const PACE_1_0 = { activity: 1, battery: 0, link: 1, micLevel: 0.1 };
const AUDIO_FRAME_MS = 20;
// What a band with firmware 1.1 adds (band.info.features).
const FEATURES_1_1 = ['fast-motion', 'intervals', 'clock'];
// Fast motion times that would go backwards (see fastMotionTimes).
const FAST_T_RESTART_MS = 1000;         // a stamp this far behind: the band's counter started again
const FAST_T_LEAST_STEP = 1 / 8;        // the least step between two samples, in sample periods ...
const FAST_T_LEAST_MS = 0.001;          // ... and in milliseconds
const CLOCK_MIN = 1577836800000;        // the band takes times from 2020 ...
const CLOCK_MAX = 4102444800000;        // ... up to 2100, in ms since 1970
const T_SPAN = 2 ** 32;                 // the band's millisecond counter starts again at 0 after this many
const ACCEL_RANGES = [2, 4, 8, 16];
const GYRO_RANGES = [0, 250, 500, 1000, 2000];
const TX_POWERS = [-20, -16, -12, -8, -4, 0, 4];
const EVENT_MASK_KINDS = ['tap', 'double-tap', 'free-fall', 'tilt', 'orientation', 'motion'];   // position = mask bit
const FACES = ['+x', '-x', '+y', '-y', '+z', '-z'];
const ACTIVITY_STATES = ['still', 'walk', 'run'];
const BATTERY_STATES = ['battery', 'charging', 'charged'];
const PHYS = { 1: '1M', 2: '2M' };
const SILENCE_DB = 96;

// What to tell a beginner for each status the band can answer with.
const STATUS_HELP = {
  UNKNOWN_OPCODE: 'The band does not know this command. Its firmware may be older than this library.',
  BAD_LENGTH: 'The band did not understand the command (wrong number of bytes).',
  BAD_ARG: 'The band refused a value that is outside what it allows. Nothing was changed.',
  BUSY: 'The band is still busy with its storage. Wait a moment and try again.',
  NOT_AVAILABLE: 'That part is not working on this band. Look at band.info.capabilities to see what is.',
  DENIED: 'The band refused this for a safety reason.',
  NOT_FOUND: 'Nothing is stored under that key.',
  NO_SPACE: 'The storage on the band is full. Delete something, or wipe it.',
  IO_ERROR: 'A sensor or the storage on the band reported an error. Try again, or restart the band.',
};
const MIC_OWNS_LED = 'The microphone is on, so the band shows a solid blue light: that is the "mic is on" sign '
  + 'and it cannot be changed. Turn the microphone off first with configure({ audio: false, micLevel: false }), then try again.';
// Contract 5.2: 1666 Hz never together with the gyroscope. Only reached with raw commands:
// configure() on firmware 1.1 uses the fast motion stream alone.
const NO_GYRO_AT_1666 = 'Motion at 1666 samples per second never runs together with the gyroscope, and the '
  + 'MOTION stream of firmware 1.0 uses the gyroscope. Stop one of the two motion streams, use a lower rate, '
  + 'or switch the gyroscope off (IMU_SET with gyro_range_dps 0). Nothing was changed.';
const DENIED_HELP = {
  IMU_SET: NO_GYRO_AT_1666,
  MOTION2_SET: 'That is more motion samples per second than this link can carry. '
    + 'await band.motionLimits() shows the most it can do.',
  LED_SET: MIC_OWNS_LED,
  IDENTIFY: MIC_OWNS_LED,
  SET_BAND_NUMBER: 'The band number can only be changed over the USB cable, not over Bluetooth. '
    + "Plug the band in and connect with { transport: 'serial' }.",
};

const LINK_NEEDS_BLUETOOTH = 'The `link` stream (signal strength) only exists over Bluetooth. On the USB cable, '
  + 'leave `link` out of configure(). Nothing in this configure call was started.';

function statusError(command, status, args = {}, transportKind = null) {
  const code = typeof status === 'string' ? status.toLowerCase().replaceAll('_', '-') : 'unknown-status';
  let help = (status === 'DENIED' && DENIED_HELP[command]) || STATUS_HELP[status] || `The band answered with status ${status}.`;
  if (status === 'DENIED' && command === 'STREAM_START' && (args.mask & BIT_MOTION2)) help = DENIED_HELP.MOTION2_SET;
  else if (status === 'DENIED' && command === 'STREAM_START' && (args.mask & BIT_MOTION)) help = NO_GYRO_AT_1666;
  // Asking for `link` on the USB cable makes the band refuse the whole STREAM_START.
  // (Over Web Bluetooth `link` always exists, so there the usual sentence is the right one.)
  const askedForLink = command === 'STREAM_START' && (args.mask & (1 << STREAM_BITS.link));
  if (status === 'NOT_AVAILABLE' && askedForLink && transportKind !== 'ble') help = LINK_NEEDS_BLUETOOTH;
  return new VexoError(code, `${command}: ${help}`);
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const LATE = Symbol('late');

/** The promise's value, or LATE if it has not settled after `ms`. */
function orLate(promise, ms) {
  let timer;
  const late = new Promise((resolve) => { timer = setTimeout(() => resolve(LATE), Math.max(0, ms)); });
  return Promise.race([promise, late]).finally(() => clearTimeout(timer));
}

const badArg = (message) => new VexoError('bad-arg', message);
/**
 * True when a TIME_SET / TIME_GET answer holds a time that can be used. A band that says
 * "set" with a time outside what the contract allows (a faulty band) is treated like one
 * whose clock is not set: times then come from this computer's clock.
 */
const clockSet = (d) => Boolean(d.set) && d.unix_ms >= CLOCK_MIN && d.unix_ms < CLOCK_MAX;
const BAND_ZERO_HELP = 'Band numbers are 1 to 999. 0 is what a band has before it gets a number: ask an organiser to number it.';

/** The timeout message when an answer did come, but with the wrong number of bytes. */
const wrongLengthText = (name, wrong, seconds) => `The band answered ${name} with ${wrong.got} bytes of data where `
  + `the contract has ${wrong.expected}, so that answer was not used, and no answer of the right length came within `
  + `${seconds} seconds. Try again. If it happens every time, this kit and the band's firmware do not match: get the newest kit.`;

function oneOf(value, allowed, what) {
  if (!allowed.includes(value)) throw badArg(`${what} must be one of ${allowed.join(', ')}; got ${value}.`);
  return value;
}

function between(value, min, max, what) {
  if (typeof value !== 'number' || !(value >= min && value <= max)) throw badArg(`${what} must be from ${min} to ${max}; got ${value}.`);
  return value;
}

const sensorWords = (name) => (name === 'both' ? 'both sensors' : `${name} alone`);

/** The fastest rate for each sensor choice, from a MOTION2_SET / MOTION2_GET answer. */
const capsOf = (m2) => ({ accel: m2.max_accel_hz, gyro: m2.max_gyro_hz, both: m2.max_both_hz });

/**
 * The error for a motion rate that is too much for this link. It names the link, the
 * most it can do, and the way out.
 * @param {number} rate samples per second that were asked for
 * @param {string} sensors 'accel', 'gyro' or 'both'
 * @param {{accel: number, gyro: number, both: number}} caps the most this link can do
 * @param {string} link 'ble', 'serial' or anything else
 */
function tooFast(rate, sensors, caps, link) {
  const where = link === 'ble' ? 'Bluetooth here' : link === 'serial' ? 'the USB cable' : 'this link';
  const ways = SENSORS.filter((name) => name !== 'both' && name !== sensors && rate <= caps[name]).map((name) => `sensors: '${name}'`);
  if (link === 'ble' && rate <= USB_CAPS[sensors]) {
    ways.push(`the USB cable (up to ${USB_CAPS.both} with both, ${USB_CAPS.accel} with accel alone)`);
  }
  ways.push('a lower rate');
  return new VexoError('denied', `${rate} samples per second with ${sensorWords(sensors)} is too much for ${where}: `
    + `the most is ${caps.both} with both, ${caps.accel} with accel alone, ${caps.gyro} with gyro alone. `
    + `Use ${ways.join(', or ')}. Nothing was changed.`);
}

/**
 * The time `t` (band milliseconds, 0 .. 2^32, with a fraction) of every sample in one fast
 * motion frame. `last` is the `t` of the sample before this frame on the same link, or null.
 *
 * The band's own times are used as they are (contract: sample i at t_ms + i x period_us / 1000)
 * whenever the frame starts after `last`. That is every frame after a lost one and after a
 * restart of the stream, so a gap in `t` is the gap the band reported.
 *
 * The stamp is in whole milliseconds and the period is an estimate, so now and then a frame
 * is stamped at or before the time the frame before it ended on. Used as they are, the times
 * would step backwards. Such a frame is spread evenly from `last` up to the band's own time
 * for its last sample: it ends where the band says, and no time is repeated or goes back.
 * If even that last time is not after `last`, the frame follows on in steps of 1/8 period.
 * A stamp a second or more behind is the band's counter itself starting again: it is followed.
 */
function fastMotionTimes(last, m) {
  const period = m.period_us / 1000;
  const count = m.samples.length;
  let first = m.t_ms;
  let step = period;
  if (last !== null) {
    let ahead = (m.t_ms - last) % T_SPAN;                   // the counter starts again at 0 after 2^32 ms
    if (ahead < 0) ahead += T_SPAN;
    if (ahead >= T_SPAN / 2) ahead -= T_SPAN;
    if (ahead <= 0 && ahead > -FAST_T_RESTART_MS) {
      step = Math.max((ahead + (count - 1) * period) / count, period * FAST_T_LEAST_STEP, FAST_T_LEAST_MS);
      first = last + step;
    }
  }
  return m.samples.map((_, i) => (first + i * step) % T_SPAN);
}

function whole(value, min, max, what) {
  if (!Number.isInteger(value)) throw badArg(`${what} must be a whole number from ${min} to ${max}; got ${value}.`);
  return between(value, min, max, what);
}

// ---- contract fields -> API fields (the unit conversions in docs/api.md)

const fromInfo = (d, features) => ({
  number: d.band_no,
  protocol: `${d.proto_major}.${d.proto_minor}`,
  firmware: `${d.fw_major}.${d.fw_minor}.${d.fw_patch}`,
  features,
  hardware: d.hw,
  capabilities: CAPABILITIES.filter((_, bit) => d.caps & (1 << bit)),
  maxFrame: d.max_frame,
  uptimeMs: d.uptime_ms,
});
const fromLed = (d) => ({
  r: d.r, g: d.g, b: d.b, brightness: d.brightness / 255, pattern: LED_PATTERNS[d.pattern] ?? d.pattern, period: d.period_ms,
});
/** Motion settings. `m2` is the fast-motion setting of a firmware 1.1 band; without it the rate is IMU_SET's. */
const fromImu = (d, m2 = null) => ({
  rate: m2 ? m2.rate_hz : d.rate_hz, sensors: m2 ? SENSORS[m2.sensors - 1] : 'both',
  accelRange: d.accel_range_g, gyroRange: d.gyro_range_dps,
});
const fromInterval = (key, d) => ({ stream: key, seconds: d.interval_ms / 1000, min: d.min_ms / 1000, max: d.max_ms / 1000 });
const fromEvents = (d) => ({
  kinds: EVENT_MASK_KINDS.filter((_, bit) => d.event_mask & (1 << bit)),
  tapThreshold: d.tap_threshold_mg / 1000,
});
const gainDb = (halfDb) => (halfDb - 40) / 2;
const fromBattery = (d) => ({
  percent: d.percent, millivolts: d.millivolts, state: BATTERY_STATES[d.state] ?? 'unknown', charging: d.state === 1,
});

/**
 * One Vexo Open band.
 *
 *   const band = await Band.connect(42);
 *   band.on('motion', (m) => console.log(m.ax, m.ay, m.az));
 *   await band.configure({ motion: 50 });
 *   await band.setLed('#ff0000');
 */
export class Band {
  /**
   * Connect to a band and check it is the one you asked for.
   * @param {number|null} number band number, e.g. 42 for "Vexo-HACK-42"; null = accept any band
   * @param {object} [options]
   * @param {'ble'|'serial'|'sim'|object} [options.transport] how to reach it (default 'ble'), or your own transport object
   * @param {number} [options.timeoutMs] how long to wait for each reply (default 2000)
   * @param {boolean} [options.autoReconnect] true = when the link is lost, find the band again and put your
   *   LED, settings and streams back (default false). Listen for 'reconnecting' and 'reconnected'.
   * @param {number} [options.reconnectSeconds] how long to keep trying before giving up (default 120)
   * @param {'1.0'} [options.protocol] '1.0' = use only what a band with firmware 1.0 can do, even if this
   *   band has 1.1 (the way out if a 1.1 feature misbehaves). Left out = use what the band reports
   * @param {boolean} [options.setClock] false = do not set the band's clock to this computer's time (default true)
   * @param {function} [options.now] the computer's time in ms since 1970 (default Date.now); tests pin it
   */
  static async connect(number = null, {
    transport = 'ble', timeoutMs = DEFAULT_TIMEOUT_MS, autoReconnect = false, reconnectSeconds = DEFAULT_RECONNECT_SECONDS,
    protocol = null, setClock = true, now = null,
  } = {}) {
    shortErrors();        // Node: a VexoError nobody catches ends the program with one sentence, not a stack trace
    if (typeof number === 'string' && /^\d+$/.test(number.trim())) number = Number(number);
    // 0 is "number not set". Only the USB cable may reach such a band (so that it can be numbered).
    if (number === 0 && (typeof transport === 'string' ? transport : transport?.kind) !== 'serial') throw badArg(BAND_ZERO_HELP);
    if (number !== null) {
      try {
        bandName(number);     // check the number before any chooser opens
      } catch (e) {
        throw badArg(`${e.message}. Use the number printed on the band, for example Band.connect(42).`);
      }
    }
    if (typeof reconnectSeconds !== 'number' || !(reconnectSeconds > 0)) {
      throw badArg(`reconnectSeconds must be a number of seconds above 0; got ${reconnectSeconds}.`);
    }
    if (protocol !== null && protocol !== undefined && protocol !== '1.0') {
      throw badArg(`protocol can only be '1.0' (use only what band firmware 1.0 can do); got ${JSON.stringify(protocol)}. `
        + 'Leave it out to use everything the band has.');
    }
    if (now !== null && typeof now !== 'function') throw badArg('now must be a function that returns milliseconds since 1970, like Date.now.');
    if (typeof transport === 'string') {
      if (!TRANSPORTS[transport]) throw new VexoError('bad-transport', `Unknown transport "${transport}". Use 'ble', 'serial' or 'sim'.`);
      transport = TRANSPORTS[transport](number);
    }
    const band = new Band(transport, timeoutMs);
    transport.onFrame = (bytes) => band._receive(bytes);
    transport.onClose = (reason) => band._closed(reason);
    band.reconnectSeconds = reconnectSeconds;
    band._oldOnly = protocol === '1.0';
    band._setClock = Boolean(setClock);
    if (now) band.now = now;
    await transport.open();
    band._linkUp = true;
    band.connected = true;
    try {
      await band.getInfo();     // ask the band who it is
      if (number !== null && band.number !== number) {
        const found = band.number === 0
          ? `This band has no number yet (it calls itself ${band.name})`
          : `This is ${band.name}`;
        const error = new VexoError('wrong-band', `${found}, not ${bandName(number)}. Check the number printed on the band, `
          + 'or pass null as the number to accept any band.');
        error.found = band.number;     // the number this band really has (0 = not set)
        error.wanted = number;
        throw error;
      }
      await band._syncClock();
    } catch (e) {
      await band.disconnect().catch(() => {});
      throw e;
    }
    band.autoReconnect = Boolean(autoReconnect);     // only once the first connection stands
    return band;
  }

  constructor(transport, timeoutMs = DEFAULT_TIMEOUT_MS) {
    this.transport = transport;
    this.transportKind = transport.kind ?? 'custom';
    this.timeoutMs = timeoutMs;
    this.number = null;
    this.name = null;
    this.info = null;
    this.connected = false;
    /** true = a lost link is repaired by the library (set with Band.connect(..., { autoReconnect: true })). */
    this.autoReconnect = false;
    /** How long auto-reconnect keeps trying, in seconds. */
    this.reconnectSeconds = DEFAULT_RECONNECT_SECONDS;
    /** true while the library is trying to get a lost link back. Commands fail with 'not-connected' meanwhile. */
    this.reconnecting = false;
    this.streams = allOff();
    /**
     * Where the `time` of events comes from: 'band' = the band's own clock (firmware 1.1, clock set),
     * 'computer' = worked out from this computer's clock and the band's uptime.
     */
    this.clock = 'computer';
    /** The computer's time in ms since 1970. The one place the library reads the computer's clock for `time`. */
    this.now = () => Date.now();
    this.storage = new BandStorage(this);
    this._handlers = new Map();
    /** Events not given to async handlers because too many of them were still running (see MAX_BUSY). */
    this.droppedEvents = 0;
    this._busy = 0;                     // async handlers that have started and not finished
    this._queue = Promise.resolve();    // commands wait here so only one is in flight
    this._pending = null;               // the command waiting for its response
    this._seq = 0;
    this._imu = null;                   // last known IMU settings (contract field names)
    this._closing = false;
    this._linkUp = false;               // the transport is open (also during the repair work of a reconnect)
    this._link = 0;                     // counts links: a command only goes out on the link it was asked on
    this._expectClose = null;           // { reason, until } after restart() or sleep()
    this._critical = false;             // the band said its battery is empty: it will not come back
    this._memory = {};                  // what you set on this link, for auto-reconnect: { LED_SET: args, ..., mask }
    this._reconnectRun = null;          // the reconnect that is running now, if any
    this._reconnectPauseMs = 1000;      // wait between two reconnect attempts
    this._wake = null;                  // ends that wait early
    this._oldOnly = false;              // connect option protocol: '1.0'
    this._setClock = true;              // connect option setClock
    this._fast = false;                 // the band has firmware 1.1 and we may use it
    this._active = 0;                   // the stream mask the band last reported
    this._m2 = null;                    // last known fast-motion settings and caps (contract field names)
    this._limits = {};                  // stream bit -> { min_ms, max_ms } of its interval, as the band said (the bit is one byte: 256 entries at most)
    this._anchor = null;                // { unix, t }: one instant in real time (ms since 1970) and in band time
    this._fastT = null;                 // `t` of the last fast motion sample on this link (fastMotionTimes)
    this._audio = null;                 // { time, seq, steps }: where the audio stream is, for the time of its frames
    this._watch = new SessionWatch(this);   // USB only: notices a session the band ended (transports/session-watch.js)
    this._streamWatch = new StreamWatch(this);   // notices a stream that another program took (stream-watch.js)
  }

  // ---------------------------------------------------------------- events

  /**
   * Listen for an event. Returns a function that stops listening.
   * Besides the data events there are:
   *   'disconnect'    { reason }: 'closed' (you called disconnect), 'lost', 'restart', 'sleep', or
   *                   'wrong-band' (with found, wanted). After auto-reconnect gave up:
   *                   { reason: 'lost', reconnect: 'gave-up' }. USB cable only: 'session-ended' (with message):
   *                   the band ended the USB session because this computer took no data for 5 seconds.
   *   'reconnecting'  { reason: 'lost', seconds }: the link dropped and auto-reconnect has started.
   *                   reason 'session-ended' (with message): the band ended the USB session.
   *   'reconnected'   { attempts, seconds, failed }: the link is back and your LED, settings and streams
   *                   were sent again. `failed` lists the commands the band refused (normally empty).
   *   'stream-taken'  { streams, stopped, message }: streams this program started have stopped arriving
   *                   though it did not stop them: a program on the band's other link (USB cable or
   *                   Bluetooth) took them. Nothing comes back by itself: see takeBack().
   */
  on(type, handler) {
    checkEvent(type);                        // a name that is not an event: 'bad-event', with the list
    if (!this._handlers.has(type)) this._handlers.set(type, new Set());
    this._handlers.get(type).add(handler);
    return () => this.off(type, handler);
  }

  /**
   * Stop calling `handler` for `type`. A name that is not an event: 'bad-event', as in on().
   * (A handler that was never added: nothing happens.)
   */
  off(type, handler) {
    checkEvent(type);
    this._handlers.get(type)?.delete(handler);
  }

  // ---------------------------------------------------------------- identity

  /** Ask the band who it is. Also refreshes band.info, band.number and band.name. */
  async getInfo() {
    await this.command('GET_INFO');
    return this.info;
  }

  /** The name the band reports, e.g. "Vexo-HACK-42". */
  async getName() {
    return (await this.command('GET_NAME')).name;
  }

  /** Give the band a new number, 1..999. Only works over the USB cable. */
  async setBandNumber(number) {
    whole(number, 1, 999, 'The band number');
    const { number: applied } = await this.command('SET_BAND_NUMBER', { number });
    this.number = applied;
    this.name = bandName(applied);
    if (this.info) this.info.number = applied;
    return applied;
  }

  // ---------------------------------------------------------------- streams

  /**
   * Turn data streams on or off. Everything is off until you ask; keys you leave out
   * keep their setting.
   *   await band.configure({ motion: 50, events: true, battery: true });
   *   await band.configure({ motion: 416, sensors: 'accel' });      // fast motion, firmware 1.1
   * Keys:
   *   motion       samples per second; true = current rate; false = off; 'fastest' = the most this
   *                link carries. Firmware 1.0: 10, 25, 50, 100. Firmware 1.1 also 208, 416, 833, 1666,
   *                up to what the link carries (see motionLimits())
   *   sensors      'accel', 'gyro' or 'both': what a motion sample holds (one sensor alone needs firmware 1.1)
   *   events, audio                                   true or false
   *   activity, battery, temperature, link, micLevel  true or false, or seconds between readings
   *                (firmware 1.1; on firmware 1.0 only temperature takes seconds, whole ones from 1 to 255),
   *                or 'fastest' = the shortest interval this band can do. On firmware 1.0 that is the
   *                band's one fixed pace, so 'fastest' then simply turns the stream on (temperature: every second)
   * link (signal strength) is Bluetooth only: on the USB cable leave it out, or the whole
   * call fails with 'not-available' and nothing in it starts.
   */
  async configure(wanted = {}) {
    // Check everything first, so a typo does not leave the band half configured.
    for (const key of Object.keys(wanted)) {
      const value = wanted[key];
      if (key === 'sensors') {
        oneOf(value, SENSORS, 'sensors');
        continue;
      }
      if (!Object.hasOwn(STREAM_KEYS, key)) throw new VexoError('bad-stream', `Unknown stream "${key}". Streams: ${Object.keys(STREAM_KEYS).join(', ')}.`);
      const numberAllowed = key === 'motion' || INTERVAL_KEYS.includes(key);
      const fine = typeof value === 'boolean' || (numberAllowed && typeof value === 'number') || (numberAllowed && value === FASTEST);
      if (!fine) {
        // (the codes are the ones from before intervals existed: only motion and temperature took numbers)
        throw new VexoError(key === 'motion' || key === 'temperature' ? 'bad-arg' : 'bad-stream',
          `Stream "${key}" must be true or false${numberAllowed ? ' or a number or "fastest"' : ''}, got ${JSON.stringify(value)}.`);
      }
    }
    const fast = this._fast;
    const wantedRate = typeof wanted.motion === 'number' && wanted.motion !== 0 ? wanted.motion : null;
    let seconds = INTERVAL_KEYS.filter((key) => wanted[key] === FASTEST || (typeof wanted[key] === 'number' && wanted[key] !== 0))
      .map((key) => [key, wanted[key]]);
    if (fast) {
      if (wantedRate !== null) oneOf(wantedRate, FAST_RATES, 'The motion rate (samples per second)');
    } else {
      if (wanted.sensors !== undefined && wanted.sensors !== 'both') throw this._motionNeeds11(wantedRate, wanted.sensors);
      if (wantedRate !== null) this._oldRate(wantedRate, wanted.sensors);
      // 'fastest' on firmware 1.0: temperature can come every second; the others have one
      // fixed pace, so the stream is simply turned on.
      seconds = seconds.filter(([key, value]) => value !== FASTEST || key === 'temperature')
        .map(([key, value]) => [key, value === FASTEST ? 1 : value]);
      for (const [key, value] of seconds) {
        if (key === 'temperature') whole(value, 1, 255, 'The temperature interval (seconds)');
        else throw this._needs11(`A number of seconds for "${key}"`, ' Use true for the standard rate.');
      }
    }

    // Firmware 1.1: the motion key always uses the fast motion stream. Work out what to set.
    let motion2 = null;
    if (fast && (wanted.motion || wanted.sensors !== undefined)) {
      const now = await this._motion2();
      const sensors = wanted.sensors ?? SENSORS[now.sensors - 1];
      const rate = wanted.motion === 'fastest' ? capsOf(now)[sensors] : (wantedRate ?? now.rate_hz);
      this._checkCap(rate, sensors, now);
      const next = { rate_hz: rate, sensors: SENSORS.indexOf(sensors) + 1 };
      if (next.rate_hz !== now.rate_hz || next.sensors !== now.sensors) motion2 = next;
    }
    const intervals = [];
    if (fast) for (const [key, value] of seconds) intervals.push([key, await this._intervalMs(key, value), value]);

    // Everything is fine: now change the band.
    for (const [key, ms, value] of intervals) await this._sendInterval(key, ms, value);
    if (motion2) await this.command('MOTION2_SET', motion2);
    if (!fast) {
      const rate = wanted.motion === 'fastest' ? MOTION_RATES.at(-1) : wantedRate;
      if (rate !== null) {
        const imu = await this._imuSettings();
        if (imu.rate_hz !== rate) await this.command('IMU_SET', { ...imu, rate_hz: rate });
      } else if (wanted.motion === true) {
        await this._imuSettings();    // so band.streams.motion shows the real rate
      }
      for (const [, value] of seconds) await this.command('TEMP_SET_INTERVAL', { seconds: value });
    }

    let start = 0;
    let stop = 0;
    const motionBit = fast ? BIT_MOTION2 : BIT_MOTION;
    const taken = this._streamWatch.taken;      // on for the band, but going to another program: start again
    for (const [key, value] of Object.entries(wanted)) {
      if (key === 'sensors') continue;
      if (key === 'motion') {
        if (value && (!(this._active & motionBit) || (taken & motionBit))) start |= motionBit;
        if (!value) stop |= this._active & (BIT_MOTION | BIT_MOTION2);
        continue;
      }
      const isOn = Boolean(this.streams[key]);
      if (value && (!isOn || (taken & (1 << STREAM_KEYS[key])))) start |= 1 << STREAM_KEYS[key];
      if (!value && isOn) stop |= 1 << STREAM_KEYS[key];
    }
    if (start) await this.command('STREAM_START', { mask: start });
    if (stop) await this.command('STREAM_STOP', { mask: stop });
    return this.streams;
  }

  /**
   * Start again the streams that another program took (see the 'stream-taken' event). A
   * band's streams are shared by everything connected to it, on the USB cable and on
   * Bluetooth, and a stream goes to the program that started it last: after this call
   * that is this program, and the other program gets no more of it. Close the other
   * program first, or the two keep taking the stream from each other. Settings are shared
   * too: if the other program changed a rate or an interval, set it again with configure().
   * Returns band.streams. With nothing taken, nothing is sent.
   */
  async takeBack() {
    const mask = this._streamWatch.taken;
    if (mask) await this.command('STREAM_START', { mask });
    return this.streams;
  }

  /** The names of the streams that were taken and not started again since, e.g. ['motion']. */
  get takenStreams() {
    return streamNames(this._streamWatch.taken);
  }

  /** The stream watch found streams of this program that go elsewhere now (`off`: the band stopped them). */
  _streamsTaken(lost, off) {
    if (!this._linkUp || this._reconnectRun) return;
    const streams = streamNames(lost);
    const message = streamTakenText(streams, Boolean(off), this._linkKind());
    if (!this._handlers.get(STREAM_TAKEN)?.size) console.error(message);     // nobody listens: say it once, here
    this._emit(STREAM_TAKEN, { streams, stopped: streamNames(off), message });
  }

  /**
   * How often a stream sends, without turning it on or off:
   *   await band.setInterval('battery', 60)      // a battery reading every minute
   *   await band.setInterval('battery', 0)       // back to "only when it changes"
   * Streams: activity, battery, temperature, link, micLevel. Needs firmware 1.1 (on 1.0 only
   * temperature works, in whole seconds). Returns { stream, seconds, min, max }.
   *
   *   await band.setInterval('micLevel', 'fastest')
   * asks for the shortest interval this band can do, on any firmware, and the answer says
   * what you got. A band with firmware 1.0 has one fixed pace for every stream but
   * temperature: nothing is sent, and the answer holds that pace (min and max are the same
   * number: it cannot be changed).
   */
  async setInterval(stream, seconds) {
    const key = intervalKey(stream);
    if (!this._fast) {
      if (seconds === FASTEST && key !== 'temperature') {
        if (this.reconnecting || !this.connected) throw this._notConnected();
        return { stream: key, seconds: PACE_1_0[key], min: PACE_1_0[key], max: PACE_1_0[key] };
      }
      if (seconds === FASTEST) seconds = 1;
      if (key !== 'temperature') throw this._needs11(`setInterval('${key}')`, " Use configure() with true for the standard rate.");
      whole(seconds, 1, 255, 'The temperature interval (seconds)');
      const applied = await this.command('TEMP_SET_INTERVAL', { seconds });
      return { stream: key, seconds: applied.seconds, min: 1, max: 255 };
    }
    const ms = await this._intervalMs(key, seconds, true);
    return fromInterval(key, await this._sendInterval(key, ms, seconds, true));
  }

  /** How often a stream sends now: { stream, seconds, min, max }. Needs firmware 1.1. */
  async getInterval(stream) {
    const key = intervalKey(stream);
    if (!this._fast) throw this._needs11(`getInterval('${key}')`);
    return fromInterval(key, await this.command('STREAM_GET_INTERVAL', { stream: STREAM_KEYS[key] }));
  }

  /** Seconds -> whole milliseconds, checked against the limits the band gave for this stream. */
  async _intervalMs(key, seconds, zeroAllowed = false) {
    const bit = STREAM_KEYS[key];
    const limit = this._limits[bit] ?? await this.command('STREAM_GET_INTERVAL', { stream: bit });
    if (seconds === FASTEST) return limit.min_ms;       // the shortest interval the band says it allows
    const ms = typeof seconds === 'number' ? Math.round(seconds * 1000) : NaN;
    const inRange = ms >= limit.min_ms && ms <= limit.max_ms && (key !== 'micLevel' || ms % MIC_LEVEL_STEP_MS === 0);
    if (!inRange && !(zeroAllowed && key === 'battery' && ms === 0)) throw intervalError(key, seconds, limit, zeroAllowed);
    return ms;
  }

  /** Send an interval. If the band still says no, ask it for its limits and say them. */
  async _sendInterval(key, ms, seconds, zeroAllowed = false) {
    const stream = STREAM_KEYS[key];
    try {
      return await this.command('STREAM_SET_INTERVAL', { stream, interval_ms: ms });
    } catch (e) {
      if (e.code !== 'bad-arg') throw e;
      throw intervalError(key, seconds, await this.command('STREAM_GET_INTERVAL', { stream }), zeroAllowed);
    }
  }

  /** The error for something only firmware 1.1 has. */
  _needs11(what, tip = '') {
    if (this._oldOnly) {
      return new VexoError('not-available', `${what} needs band firmware 1.1, and you connected with protocol '1.0', `
        + `which keeps to what firmware 1.0 can do.${tip}`);
    }
    return new VexoError('not-available', `${what} needs band firmware 1.1; this band has ${this.info?.firmware ?? 'an older one'}.${tip}`);
  }

  _motionNeeds11(rate, sensors) {
    const start = this._oldOnly
      ? "You connected with protocol '1.0', which"
      : `This band's firmware (${this.info?.firmware ?? '1.0'})`;
    const fastRate = FAST_RATES.includes(rate) && !MOTION_RATES.includes(rate);
    const one = sensors !== undefined && sensors !== 'both';
    const what = fastRate ? `${rate} per second${one ? ', or one sensor alone,' : ''}` : 'One sensor alone';
    return new VexoError('not-available', `${start} can do 10, 25, 50 or 100 samples per second with both sensors. `
      + `${what} needs firmware 1.1.`);
  }

  /** Check a motion rate for a band with firmware 1.0. */
  _oldRate(rate, sensors) {
    if (FAST_RATES.includes(rate) && !MOTION_RATES.includes(rate)) throw this._motionNeeds11(rate, sensors);
    return oneOf(rate, MOTION_RATES, 'The motion rate (samples per second)');
  }

  /** Refuse, before anything is sent, a fast-motion rate this link cannot carry. */
  _checkCap(rate, sensors, m2) {
    const caps = capsOf(m2);
    if (rate > caps[sensors]) throw tooFast(rate, sensors, caps, this._linkKind());
  }

  /** 'ble' or 'serial'; the pretend band says which one it imitates. */
  _linkKind() {
    if (this.transportKind !== 'sim') return this.transportKind;
    return this.transport.via === 'usb' ? 'serial' : 'ble';
  }

  // ---------------------------------------------------------------- LED

  /**
   * Set the LED: setLed('#ff8800'), setLed(255, 136, 0), and with options
   * setLed('#ff8800', { brightness: 0.5, pattern: 'blink', period: 500 }).
   */
  async setLed(r, g, b, options) {
    if (typeof r === 'string') {
      const m = /^#?([0-9a-f]{6})$/i.exec(r.trim());
      if (!m) throw new VexoError('bad-colour', `A colour looks like "#ff8800" (# and six hex digits); got "${r}".`);
      options = g;
      const v = parseInt(m[1], 16);
      [r, g, b] = [v >> 16, (v >> 8) & 255, v & 255];
    }
    for (const part of [r, g, b]) {
      if (!Number.isInteger(part) || part < 0 || part > 255) {
        throw new VexoError('bad-colour', `Red, green and blue must each be a whole number from 0 to 255; got ${r}, ${g}, ${b}.`);
      }
    }
    const { brightness = 1, pattern = 'solid', period = 1000 } = options ?? {};
    between(brightness, 0, 1, 'LED brightness');
    oneOf(pattern, LED_PATTERNS, 'The LED pattern');
    whole(period, 100, 10000, 'The LED period (milliseconds)');
    await this.command('LED_SET', {
      r, g, b, brightness: Math.round(brightness * 255), pattern: LED_PATTERNS.indexOf(pattern), period_ms: period,
    });
  }

  async ledOff() {
    await this.command('LED_SET', { r: 0, g: 0, b: 0, brightness: 0, pattern: 0, period_ms: 1000 });
  }

  /** The LED setting you made: { r, g, b, brightness (0..1), pattern, period }. */
  async getLed() {
    return fromLed(await this.command('LED_GET'));
  }

  /** Blink white for a few seconds so you can see which band you are connected to. */
  async identify(seconds = 5) {
    whole(seconds, 1, 30, 'The identify time (seconds)');
    await this.command('IDENTIFY', { seconds });
  }

  // ---------------------------------------------------------------- motion sensor

  /**
   * setMotion({ rate: 100, sensors: 'both', accelRange: 8, gyroRange: 500 }); what you leave out stays.
   * Returns { rate, sensors, accelRange, gyroRange } as the band applied them.
   */
  async setMotion({ rate, sensors, accelRange, gyroRange } = {}) {
    if (sensors !== undefined) oneOf(sensors, SENSORS, 'sensors');
    if (accelRange !== undefined) oneOf(accelRange, ACCEL_RANGES, 'accelRange (g)');
    if (gyroRange !== undefined) oneOf(gyroRange, GYRO_RANGES, 'gyroRange (degrees/second)');
    if (!this._fast) {
      if (sensors !== undefined && sensors !== 'both') throw this._motionNeeds11(rate, sensors);
      if (rate !== undefined) this._oldRate(rate, sensors);
      const now = await this._imuSettings();
      return fromImu(await this.command('IMU_SET', {
        rate_hz: rate ?? now.rate_hz,
        accel_range_g: accelRange ?? now.accel_range_g,
        gyro_range_dps: gyroRange ?? now.gyro_range_dps,
      }));
    }
    // Firmware 1.1: rate and sensors belong to the fast motion stream, the ranges to both streams.
    if (rate !== undefined) oneOf(rate, FAST_RATES, 'The motion rate (samples per second)');
    let m2 = await this._motion2();
    let next = null;
    if (rate !== undefined || sensors !== undefined) {
      const name = sensors ?? SENSORS[m2.sensors - 1];
      this._checkCap(rate ?? m2.rate_hz, name, m2);
      next = { rate_hz: rate ?? m2.rate_hz, sensors: SENSORS.indexOf(name) + 1 };
    }
    let imu = await this._imuSettings();
    if (accelRange !== undefined || gyroRange !== undefined) {
      imu = await this.command('IMU_SET', {
        rate_hz: imu.rate_hz,
        accel_range_g: accelRange ?? imu.accel_range_g,
        gyro_range_dps: gyroRange ?? imu.gyro_range_dps,
      });
    }
    if (next) m2 = await this.command('MOTION2_SET', next);
    return fromImu(imu, m2);
  }

  /** { rate, sensors, accelRange, gyroRange } */
  async getMotion() {
    const imu = await this.command('IMU_GET');
    return fromImu(imu, this._fast ? await this.command('MOTION2_GET') : null);
  }

  /**
   * The fastest motion rate this link carries for each sensor choice:
   *   { rates: [10, 25, ...], accel: 833, gyro: 833, both: 416, transport: 'ble' }
   * `rates` are the rates this band knows. On firmware 1.0: accel and gyro are null (one
   * sensor alone is not possible) and both is 100.
   */
  async motionLimits() {
    if (!this._fast) return { rates: [...MOTION_RATES], accel: null, gyro: null, both: MOTION_RATES.at(-1), transport: this.transportKind };
    return { rates: [...FAST_RATES], ...capsOf(await this._motion2()), transport: this.transportKind };
  }

  /** setEvents({ kinds: ['tap', 'double-tap'], tapThreshold: 1.2 }); what you leave out stays. */
  async setEvents({ kinds, tapThreshold } = {}) {
    let mask;
    if (kinds !== undefined) {
      if (!Array.isArray(kinds)) throw badArg(`kinds must be a list such as ['tap', 'double-tap']; got ${kinds}.`);
      mask = 0;
      for (const kind of kinds) mask |= 1 << EVENT_MASK_KINDS.indexOf(oneOf(kind, EVENT_MASK_KINDS, 'An event kind'));
    }
    if (tapThreshold !== undefined) between(tapThreshold, 0.2, 8, 'tapThreshold (g)');
    const now = mask === undefined || tapThreshold === undefined ? await this.command('EVENT_GET') : {};
    return fromEvents(await this.command('EVENT_SET', {
      event_mask: mask ?? now.event_mask,
      tap_threshold_mg: tapThreshold === undefined ? now.tap_threshold_mg : Math.round(tapThreshold * 1000),
    }));
  }

  async getEvents() {
    return fromEvents(await this.command('EVENT_GET'));
  }

  // ---------------------------------------------------------------- microphone, radio, battery

  /** Microphone gain in dB, -20..+20 in half steps. Returns the gain the band applied. */
  async setMicGain(db) {
    between(db, -20, 20, 'Mic gain (dB)');
    const { gain_half_db } = await this.command('MIC_SET_GAIN', { gain_half_db: Math.round(db * 2) + 40 });
    return gainDb(gain_half_db);
  }

  async getMic() {
    const d = await this.command('MIC_GET');
    return { gainDb: gainDb(d.gain_half_db), sampleRate: d.sample_rate, frameMs: d.frame_ms, channels: d.channels, bitrate: d.bitrate };
  }

  /** Radio power in dBm: -20, -16, -12, -8, -4, 0 or 4. */
  async setTxPower(dbm) {
    oneOf(dbm, TX_POWERS, 'Radio power (dBm)');
    return (await this.command('RADIO_SET_TX_POWER', { dbm })).dbm;
  }

  /**
   * { txPower, rssi, phy, intervalMs, mtu }. rssi is in dBm (for example -60), or null when there is
   * no reading. Over the USB cable there is no Bluetooth link: rssi and phy are null, intervalMs and mtu 0.
   */
  async getRadio() {
    const d = await this.command('RADIO_GET');
    return {
      txPower: d.tx_dbm, rssi: d.rssi_dbm === 127 ? null : d.rssi_dbm, phy: PHYS[d.phy] ?? null,
      intervalMs: d.interval * 1.25, mtu: d.mtu,
    };
  }

  async getBattery() {
    return fromBattery(await this.command('BATTERY_GET'));
  }

  // ---------------------------------------------------------------- time

  /**
   * What time the band thinks it is: { time, set, t, source }.
   *   time    ms since 1970, like Date.now(): new Date(time)
   *   set     true if the band's own clock is set
   *   t       the band's millisecond counter at that same instant (the `t` of events)
   *   source  'band' (its own clock) or 'computer' (worked out from this computer's clock)
   * A band with firmware 1.0 has no clock: set is false and source is 'computer'.
   */
  async time() {
    if (!this._fast) {
      const info = await this.command('GET_INFO');
      return { time: this._anchor.unix, set: false, t: info.uptime_ms, source: 'computer' };
    }
    return this._fromClock(await this.command('TIME_GET'));
  }

  /**
   * Set the band's clock: to this computer's time, or to `time` (ms since 1970).
   * Returns the same as time(). Needs firmware 1.1.
   */
  async setTime(time = this.now()) {
    if (!this._fast) throw this._needs11('Setting the clock');
    const unix_ms = typeof time === 'number' ? Math.round(time) : NaN;
    const wrong = () => badArg('The band takes a time from the year 2020 to 2100, in milliseconds since 1970 '
      + `(like Date.now()); got ${time}.`);
    if (!(unix_ms >= CLOCK_MIN && unix_ms < CLOCK_MAX)) throw wrong();
    try {
      return this._fromClock(await this.command('TIME_SET', { unix_ms }));
    } catch (e) {
      throw e.code === 'bad-arg' ? wrong() : e;
    }
  }

  _fromClock(d) {
    if (clockSet(d)) return { time: d.unix_ms, set: true, t: d.t_ms, source: 'band' };
    return { time: this._anchor.unix, set: false, t: d.t_ms, source: 'computer' };
  }

  /**
   * Right after connecting: on firmware 1.1 set the band's clock (or only read it, with
   * setClock: false). A band that refuses keeps working; times then come from the computer.
   * Returns the command the band refused, or null.
   */
  async _syncClock() {
    if (!this._fast) return null;
    let refused = null;
    const gone = (e) => e.code === 'not-connected' || e.code === 'timeout';
    if (this._setClock) {
      try {
        await this._enqueue('TIME_SET', { unix_ms: Math.round(this.now()) });
        return null;
      } catch (e) {
        if (gone(e)) throw e;
        refused = 'TIME_SET';
      }
    }
    try {
      await this._enqueue('TIME_GET');
    } catch (e) {
      if (gone(e)) throw e;
      refused ??= 'TIME_GET';
    }
    return refused;
  }

  /** Band time `t` (ms, may have a fraction) -> real time in ms since 1970 (contract 5.8). */
  _timeOf(t) {
    const anchor = this._anchor;
    if (!anchor) return this.now();
    let d = (t - anchor.t) % T_SPAN;        // the counter starts again at 0 after 2^32 ms ...
    if (d < 0) d += T_SPAN;
    if (d >= T_SPAN / 2) d -= T_SPAN;       // ... and a reading may be from before the anchor
    return anchor.unix + d;
  }

  // ---------------------------------------------------------------- device

  /** Restart the band. The link closes a moment later (auto-reconnect does not bring it back). */
  async restart() {
    await this.command('RESTART');
    // The link drop that follows within a few seconds is expected.
    this._expectClose = { reason: 'restart', until: Date.now() + EXPECTED_CLOSE_MS };
    this._forget();
  }

  /**
   * Put the band to sleep: its streams stop and its LED goes off. Over Bluetooth the link
   * closes a moment later (wake the band with a double-tap, then connect again). On the USB
   * cable the link stays open and the band keeps answering.
   */
  async sleep() {
    await this.command('SLEEP');
    this._expectClose = { reason: 'sleep', until: Date.now() + EXPECTED_CLOSE_MS };
    this._streamWatch.reset();
    this.streams = allOff();
    this._active = 0;
    this._forget();
  }

  /** Close the link. Also stops a reconnect that is still trying. */
  async disconnect() {
    if (this.reconnecting) {
      this._reconnectRun = null;
      this.reconnecting = false;
      this._wake?.();
      this._forget();
      await this._hangUp();
      this._emit('disconnect', { reason: 'closed' });
      return;
    }
    if (!this.connected) return;
    this._closing = true;
    try {
      await this.transport.close();
    } finally {
      this._closed('closed');
    }
  }

  // ---------------------------------------------------------------- any command

  /**
   * Send any command by its contract name and get the response data back, with the
   * contract's field names and raw units:
   *   await band.command('LED_GET')
   *   await band.command('IMU_SET', { rate_hz: 50, accel_range_g: 2, gyro_range_dps: 2000 })
   */
  command(name, args = {}) {
    if (this.reconnecting) return Promise.reject(this._notConnected());
    return this._enqueue(name, args);
  }

  _enqueue(name, args = {}) {
    const link = this._link;
    const job = this._queue.then(() => this._exchange(name, args, link));
    this._queue = job.catch(() => {});
    return job;
  }

  _exchange(name, args, link) {
    if (!this._linkUp || link !== this._link) throw this._notConnected();
    const seq = this._seq;
    this._seq = (seq + 1) & 0xff;
    let frame;
    try {
      frame = encodeCommand(seq, name, args);
    } catch (e) {
      throw badArg(e.message);
    }
    return new Promise((resolve, reject) => {
      const waitMs = this.timeoutMs;
      const timer = setTimeout(() => pending.reject(new VexoError('timeout', pending.wrong
        ? wrongLengthText(name, pending.wrong, waitMs / 1000)
        : `${this.name ?? 'The band'} did not answer ${name} within ${waitMs / 1000} seconds. Is it still close by and awake?`)), waitMs);
      const done = (settle, value) => {
        if (this._pending !== pending) return;
        clearTimeout(timer);
        this._pending = null;
        settle(value);
      };
      const pending = { seq, name, args, resolve: (v) => done(resolve, v), reject: (e) => done(reject, e) };
      this._pending = pending;
      Promise.resolve()
        .then(() => this.transport.send(frame))
        .catch((e) => pending.reject(e instanceof VexoError ? e : new VexoError('not-connected', `Could not send ${name}: ${e.message}`)));
    });
  }

  _notConnected() {
    if (this.reconnecting) {
      return new VexoError('not-connected', `The link to ${this.name ?? 'the band'} dropped and the library is reconnecting. `
        + 'Wait for the "reconnected" event, then try again.');
    }
    return new VexoError('not-connected', `${this.name ?? 'The band'} is not connected. Call Band.connect(...) again.`);
  }

  async _imuSettings() {
    return this._imu ?? this.command('IMU_GET');
  }

  /** The fast-motion settings and what this link can carry (asked once per link, then kept up to date). */
  async _motion2() {
    return this._m2 ?? this.command('MOTION2_GET');
  }

  // ---------------------------------------------------------------- receiving

  _receive(bytes) {
    this._watch.heard(bytes[0]);                // USB session watch: a stream frame is a sign of life
    this._streamWatch.heard(bytes[0]);          // stream watch: this stream still arrives
    const msg = decodeFrame(bytes);
    if (!msg) {                                 // not a valid frame: ignore
      // An answer of the wrong length is never used. Over USB the start of an answer can
      // arrive cut off, and the whole answer follows with the same seq: keep waiting for it.
      const wrong = wrongLengthResponse(bytes);
      const pending = this._pending;
      if (wrong && pending && wrong.seq === pending.seq && wrong.opcode === pending.name) pending.wrong = wrong;
      return;
    }
    if (msg.frame === 'response') return this._response(msg);
    if (msg.frame === 'notice' && msg.code === 'CRITICAL_BATTERY') this._critical = true;
    const convert = INCOMING[msg.frame];        // unknown frame types are ignored
    if (convert) convert(msg, (type, event) => this._emit(type, event), this);
  }

  _response(msg) {
    const pending = this._pending;
    // Not the reply we are waiting for (late or unasked): ignore.
    if (!pending || msg.seq !== pending.seq || msg.opcode !== pending.name) return;
    // sensors is not 1, 2 or 3: not a valid answer, ignored like one of the wrong length.
    if ((msg.opcode === 'MOTION2_SET' || msg.opcode === 'MOTION2_GET') && msg.status === 'OK' && !SENSORS[msg.data.sensors - 1]) return;
    if (msg.status !== 'OK') return pending.reject(this._statusError(pending, msg.status));
    this._remember(pending.name, msg.data, pending.args);
    pending.resolve(msg.data);
  }

  /** The error for a status the band answered with. */
  _statusError(pending, status) {
    const { name, args } = pending;
    // Fast motion over the cap: say what this link can do and the way out.
    if (status === 'DENIED' && this._m2) {
      const caps = capsOf(this._m2);
      const asked = name === 'MOTION2_SET' && SENSORS[args.sensors - 1] ? args
        : name === 'STREAM_START' && (args.mask & BIT_MOTION2) ? this._m2 : null;
      if (asked) {
        const sensors = SENSORS[asked.sensors - 1];
        // Within what the link carries and still refused at 1666: the gyroscope is running for MOTION.
        if (asked.rate_hz === FAST_RATES.at(-1) && asked.rate_hz <= caps[sensors]) return new VexoError('denied', `${name}: ${NO_GYRO_AT_1666}`);
        return tooFast(asked.rate_hz, sensors, caps, this._linkKind());
      }
    }
    return statusError(name, status, args, this.transportKind);
  }

  /**
   * Keep band.info, band.streams, band.clock, the cached settings and the reconnect memory
   * in step with the band.
   */
  _remember(name, data, args) {
    if (RESTORE_ORDER.includes(name)) this._memory[name] = args;
    if (name === 'GET_INFO') {
      // A later protocol 1.x only adds things, so it is used like 1.1.
      this._fast = !this._oldOnly && data.proto_major === 1 && data.proto_minor >= 1;
      this.info = fromInfo(data, this._fast ? [...FEATURES_1_1] : []);
      this.number = data.band_no;
      this.name = bandName(Math.min(data.band_no, 999));
      // No clock on the band (or not known yet): this computer's clock now, next to the band's uptime.
      if (this.clock !== 'band' || !this._fast) this._useComputerClock(data.uptime_ms);
    }
    if ((name === 'TIME_SET' || name === 'TIME_GET') && this._fast) {
      if (clockSet(data)) {
        this._anchor = { unix: data.unix_ms, t: data.t_ms };
        this.clock = 'band';
      } else {
        this._useComputerClock(data.t_ms);
      }
    }
    if (name === 'IMU_SET' || name === 'IMU_GET') this._imu = data;
    if (name === 'MOTION2_SET' || name === 'MOTION2_GET') this._m2 = data;
    if (name === 'STREAM_SET_INTERVAL' || name === 'STREAM_GET_INTERVAL') {
      this._limits[data.stream] = { min_ms: data.min_ms, max_ms: data.max_ms };
    }
    // What auto-reconnect sends again. The temperature interval has two commands: the last one counts.
    if (name === 'STREAM_SET_INTERVAL') {
      this._memory.intervals = { ...this._memory.intervals, [data.stream]: data.interval_ms };
      if (data.stream === STREAM_BITS.temp) delete this._memory.TEMP_SET_INTERVAL;
    }
    if (name === 'TEMP_SET_INTERVAL' && this._memory.intervals) delete this._memory.intervals[STREAM_BITS.temp];
    // USB session watch: streams you switched on are off, so the band ended the session. Keep what to set again.
    if (name === 'STREAM_GET' && this._watch.ended(data.active)) return;
    if (name === 'STREAM_START') this._streamWatch.started(args.mask, data.active);
    if (name === 'STREAM_STOP') this._streamWatch.stopped(args.mask);
    if (name === 'STREAM_GET') this._streamWatch.answer(data.active);
    if (name === 'STREAM_START' || name === 'STREAM_STOP' || name === 'STREAM_GET') {
      this._active = data.active;
      for (const key of Object.keys(STREAM_KEYS)) this.streams[key] = Boolean(data.active & (1 << STREAM_KEYS[key]));
      this._memory.mask = data.active;
      if (!(data.active & BIT_AUDIO)) this._audio = null;     // the next audio frame is the first of a new run
    }
    this.streams.motion = this._motionRate();
    this._watch.update();                       // USB session watch: runs only while streams are on
    this._streamWatch.update();                 // stream watch: runs only while streams of this program are on
  }

  /** band.streams.motion: the rate of the motion stream that is on (the fast one if both are), 0 = off. */
  _motionRate() {
    if (this._active & BIT_MOTION2) return this._m2?.rate_hz ?? 25;
    if (this._active & BIT_MOTION) return this._imu?.rate_hz ?? 25;
    return 0;
  }

  /** Event times are worked out from this computer's clock: now is band time `t`. */
  _useComputerClock(t) {
    this._anchor = { unix: this.now(), t };
    this.clock = 'computer';
  }

  /** The link is gone: nothing known about the band's settings holds any more. */
  _linkDown() {
    this._linkUp = false;
    this._watch.stop();
    this._streamWatch.reset();
    this._link++;
    this._imu = null;
    this._m2 = null;
    this._limits = {};
    this._active = 0;
    this._audio = null;
    this._fastT = null;
    this.clock = 'computer';
  }

  /** Forget what auto-reconnect would send again. */
  _forget() {
    this._memory = {};
  }

  /** The transport says the link ended. */
  _closed(reason) {
    if (!this._linkUp) return;
    this._linkDown();
    const expected = this._expectClose && Date.now() <= this._expectClose.until ? this._expectClose.reason : null;
    this._expectClose = null;
    if (this._reconnectRun) {           // dropped again in the middle of reconnecting: that loop tries again
      this._pending?.reject(this._notConnected());
      return;
    }
    this.connected = false;
    this.streams = allOff();
    const why = this._closing ? 'closed' : (this._watch.ending ?? expected ?? reason ?? 'lost');
    // Only a link that was lost is worth repairing, and a band with an empty battery stays away.
    const retry = (why === 'lost' || why === SESSION_ENDED) && this.autoReconnect && !this._critical;
    // The band ended the USB session: the event also carries the sentence for the person.
    const detail = why === SESSION_ENDED ? { reason: why, message: sessionEndedText(retry) } : { reason: why };
    const run = retry ? {} : null;
    this._reconnectRun = run;
    this.reconnecting = retry;
    this._pending?.reject(this._notConnected());
    if (!retry) {
      this._forget();
      this._emit('disconnect', detail);
      return;
    }
    this._emit('reconnecting', { ...detail, seconds: this.reconnectSeconds });
    this._reconnectLoop(run);
  }

  /** Close the transport from our side, quietly (used while reconnecting). */
  async _hangUp() {
    this._linkDown();
    this._pending?.reject(this._notConnected());
    await Promise.resolve().then(() => this.transport.close()).catch(() => {});
  }

  /** Auto-reconnect: open the link again, check it is the same band, send the settings again. */
  async _reconnectLoop(run) {
    const started = Date.now();
    const deadline = started + this.reconnectSeconds * 1000;
    const before = { number: this.number, name: this.name, info: this.info };
    const transport = this.transport;
    const live = () => this._reconnectRun === run;      // false once disconnect() cancelled us
    let attempts = 0;
    await null;                                         // let the transport finish closing first
    while (live() && Date.now() < deadline) {
      attempts++;
      try {
        // reopen() is the transport's way to come back without a new click; not every transport has one.
        const opening = Promise.resolve().then(() => (transport.reopen ? transport.reopen() : transport.open()));
        if (await orLate(opening, deadline - Date.now()) === LATE) {
          opening.then(() => transport.close()).catch(() => {});      // too late: do not keep a link nobody uses
          break;
        }
        if (!live()) {
          await Promise.resolve().then(() => transport.close()).catch(() => {});
          return;
        }
        this._linkUp = true;
        this._critical = false;
        await this._enqueue('GET_INFO');
        if (!live()) return;
        if (before.number !== null && this.number !== before.number) {
          const found = this.number;
          Object.assign(this, before);                  // band.number stays the band you connected to
          await this._hangUp();
          this._endReconnect(run, { reason: 'wrong-band', found, wanted: before.number });
          return;
        }
        const clock = await this._syncClock();          // first, so the events that follow get the right time
        if (!live()) return;
        const failed = [...(clock ? [clock] : []), ...await this._restore()];
        if (!live()) return;
        if (!this._linkUp) throw this._notConnected();
        this._reconnectRun = null;
        this.reconnecting = false;
        this.connected = true;
        this._emit('reconnected', { attempts, seconds: Math.round(Date.now() - started) / 1000, failed });
        return;
      } catch {
        if (!live()) return;
        if (this._linkUp) await this._hangUp();          // half open (the band did not answer): start clean
      }
      const pause = Math.min(this._reconnectPauseMs, deadline - Date.now());
      if (pause > 0 && live()) {
        await new Promise((resolve) => {
          const timer = setTimeout(resolve, pause);
          this._wake = () => { clearTimeout(timer); resolve(); };     // disconnect() ends the wait early
        });
        this._wake = null;
      }
    }
    this._endReconnect(run, { reason: 'lost', reconnect: 'gave-up' });
  }

  _endReconnect(run, detail) {
    if (this._reconnectRun !== run) return;
    this._reconnectRun = null;
    this.reconnecting = false;
    this._forget();
    this._emit('disconnect', detail);
  }

  /** Send the remembered settings again on a fresh link. Returns the commands the band refused. */
  async _restore() {
    const memory = this._memory;
    const jobs = RESTORE_ORDER.filter((name) => memory[name]).map((name) => [name, memory[name]]);
    for (const [stream, interval_ms] of Object.entries(memory.intervals ?? {})) {
      jobs.push(['STREAM_SET_INTERVAL', { stream: Number(stream), interval_ms }]);
    }
    if (memory.mask) jobs.push(['STREAM_START', { mask: memory.mask }]);
    const failed = [];
    for (const [name, args] of jobs) {
      // The band may have come back with firmware 1.0: it never gets a 1.1 command. What it
      // cannot take is reported as failed and forgotten, like a setting the band refused.
      if (!this._fast && (name === 'MOTION2_SET' || name === 'STREAM_SET_INTERVAL')) {
        failed.push(name);
        delete memory.MOTION2_SET;
        delete memory.intervals;
        continue;
      }
      if (!this._fast && name === 'STREAM_START' && (args.mask & BIT_MOTION2)) {
        failed.push('STREAM_START');
        memory.mask = args.mask &= ~BIT_MOTION2;
        if (!args.mask) continue;
      }
      try {
        await this._enqueue(name, args);
      } catch (e) {
        if (e.code === 'not-connected' || e.code === 'timeout') throw e;     // the link is gone again
        failed.push(name);                                                  // the band said no: skip it
      }
    }
    return failed;
  }

  _emit(type, event) {
    // Handlers are called at once, inside the call that brought the frame: the library keeps
    // no line of waiting events, so none can grow. Only `async` handlers stay alive after
    // the call. At most MAX_BUSY of them may be running: beyond that a new data event is not
    // given to async handlers (band.droppedEvents counts; link events are always given).
    for (const handler of [...(this._handlers.get(type) ?? [])]) {
      try {
        if (this._busy >= MAX_BUSY && !KEPT_EVENTS.includes(type) && handler?.constructor?.name === 'AsyncFunction') {
          if (this.droppedEvents++ === 0) console.error(DROP_HELP);     // one line, the first time only
          continue;
        }
        const result = handler(event);
        if (typeof result?.then === 'function') {
          this._busy++;
          result.then(() => { this._busy--; }, (e) => {
            this._busy--;
            console.error(`Error in your "${type}" handler:`, e);
          });
        }
      } catch (e) {
        console.error(`Error in your "${type}" handler:`, e);
      }
    }
  }
}

/** 'micLevel' (or 'mic_level') -> 'micLevel'; a stream without an interval is refused. */
function intervalKey(stream) {
  const key = stream === 'mic_level' ? 'micLevel' : stream;
  if (!INTERVAL_KEYS.includes(key)) {
    // motion, events and audio are streams, only not ones with an interval
    const code = typeof key === 'string' && Object.hasOwn(STREAM_KEYS, key) ? 'bad-arg' : 'bad-stream';
    throw new VexoError(code, `Stream "${stream}" has no interval. The streams with one: ${INTERVAL_KEYS.join(', ')}.`);
  }
  return key;
}

/** The error for an interval outside what the band allows; `limit` is the band's own answer. */
function intervalError(key, seconds, limit, zeroAllowed) {
  const steps = key === 'micLevel' ? `, in steps of ${MIC_LEVEL_STEP_MS / 1000}` : '';
  const zero = key === 'battery' && zeroAllowed ? ' (or 0 = only when it changes)' : '';
  return badArg(`"${key}" can come every ${limit.min_ms / 1000} to ${limit.max_ms / 1000} seconds${steps}${zero}, got ${seconds}. Nothing was changed.`);
}

/** Stream mask -> the configure() names, e.g. ['motion', 'micLevel']. Both motion streams are 'motion'. */
function streamNames(mask) {
  const names = Object.keys(STREAM_KEYS).filter((key) => mask & (1 << STREAM_KEYS[key]));
  if ((mask & BIT_MOTION2) && !names.includes('motion')) names.unshift('motion');
  return names;
}

/** The sentence of the 'stream-taken' event. `link` is this program's own link: 'ble', 'serial' or another. */
function streamTakenText(streams, stopped, link) {
  const other = link === 'ble' ? 'the cable (USB)' : link === 'serial' ? 'Bluetooth' : "the band's other link";
  const what = streams.length === 1 ? `the "${streams[0]}" stream` : `the streams ${streams.map((name) => `"${name}"`).join(', ')}`;
  const it = streams.length === 1 ? 'this stream' : 'these streams';
  return `No data arrives any more for ${what}, though this program did not stop it and the link is up. `
    + `Another program on ${other} took ${it}${stopped ? ' and has stopped since' : ''}: USB and Bluetooth programs on one band share its streams, `
    + 'its settings and its light, and a stream goes to the program that started it last. '
    + `${stopped ? 'To' : 'Close the other program first; then, to'} get the data back, call band.takeBack().`;
}

function allOff() {
  return { motion: 0, events: false, activity: false, audio: false, battery: false, temperature: false, link: false, micLevel: false };
}

// Decoded frame -> the events in docs/api.md. One entry per frame kind.
const INCOMING = {
  motion(m, emit, band) {
    m.samples.forEach((s, i) => {
      const t = m.t_ms + i * m.period_ms;
      emit('motion', {
        t,
        ax: s.ax / 1000, ay: s.ay / 1000, az: s.az / 1000,  // milli-g -> g
        gx: s.gx / 10, gy: s.gy / 10, gz: s.gz / 10,        // tenths -> degrees/second
        n: null,                                            // this stream does not number its samples
        time: band._timeOf(t),
      });
    });
  },
  // Fast motion (firmware 1.1): the same event. A sensor that was not asked for is null.
  motion2(m, emit, band) {
    const g = (milliG) => (milliG === undefined ? null : milliG / 1000);
    const dps = (tenths) => (tenths === undefined ? null : tenths / 10);
    const times = fastMotionTimes(band._fastT, m);
    m.samples.forEach((s, i) => {
      const t = times[i];                                   // band milliseconds, with a fraction
      emit('motion', {
        t,
        ax: g(s.ax), ay: g(s.ay), az: g(s.az),
        gx: dps(s.gx), gy: dps(s.gy), gz: dps(s.gz),
        n: (m.sample_no + i) % T_SPAN,                      // a jump in n = that many samples lost
        time: band._timeOf(t),
      });
    });
    if (times.length) band._fastT = times[times.length - 1];
  },
  event(m, emit, band) {
    const kind = EVENT_KINDS[m.event];
    if (!kind) return;                                      // an event this library does not know
    const event = { t: m.t_ms, kind, arg: m.arg };
    if (kind === 'orientation') event.face = FACES[m.arg] ?? null;
    event.time = band._timeOf(m.t_ms);
    emit('event', event);
    emit(kind, event);
  },
  activity(m, emit, band) {
    emit('activity', {
      t: m.t_ms, steps: m.steps_total, cadence: m.cadence_spm,
      state: ACTIVITY_STATES[m.state] ?? 'unknown', motion: m.motion_mg / 1000, time: band._timeOf(m.t_ms),
    });
  },
  // Audio frames carry no time stamp: the first one after a start is "now", each later one 20 ms on.
  audio(m, emit, band) {
    const run = band._audio ?? (band._audio = { time: band.now(), seq: m.seq, steps: 0 });
    run.steps += (m.seq - run.seq) & 0xffff;                // seq starts again at 0 after 65535
    run.seq = m.seq;
    emit('audio', { seq: m.seq, opus: m.opus, time: run.time + run.steps * AUDIO_FRAME_MS });
  },
  battery(m, emit, band) {
    emit('battery', { ...fromBattery(m), time: band.now() });   // no time stamp: the time it arrived
  },
  temp(m, emit, band) {
    emit('temperature', { t: m.t_ms, celsius: m.temp_mc / 1000, time: band._timeOf(m.t_ms) });
  },
  link(m, emit, band) {
    emit('link', {
      t: m.t_ms, rssi: m.rssi_dbm === 127 ? null : m.rssi_dbm, txPower: m.tx_dbm, phy: PHYS[m.phy] ?? null,
      time: band._timeOf(m.t_ms),
    });
  },
  mic_level(m, emit, band) {
    const level = Math.min(1, Math.max(0, 1 - m.rms_db / SILENCE_DB));
    emit('mic-level', { t: m.t_ms, peakDb: m.peak_db, rmsDb: m.rms_db, level, time: band._timeOf(m.t_ms) });
  },
  notice(m, emit, band) {
    if (typeof m.code !== 'string') return;                 // a notice this library does not know
    emit('notice', { kind: m.code.toLowerCase().replaceAll('_', '-'), arg: m.arg, time: band.now() });
  },
};

/**
 * band.storage: a small key-value store on the band. Anyone who connects can read it.
 * The band takes at most 10 writes (put, delete) a second and answers "busy" beyond that
 * and while it erases. Every call here waits and asks again by itself, so a loop of 30 puts
 * simply takes about 3 seconds.
 */
class BandStorage {
  constructor(band) {
    this._band = band;
    this._busyPauseMs = 100;      // how long to wait before asking a busy band again
    this._busySeconds = 3;        // how long to keep asking a busy band before giving up
  }

  /** Send one storage command; while the band answers BUSY, wait and send it again. */
  async _send(name, args) {
    const started = Date.now();
    for (;;) {
      try {
        return await this._band.command(name, args);
      } catch (e) {
        if (e.code !== 'busy') throw e;
        if (Date.now() - started + this._busyPauseMs > this._busySeconds * 1000) {
          throw new VexoError('busy', `${name}: The band's storage is still busy after ${this._busySeconds} seconds `
            + '(it takes at most 10 writes a second, and nothing while it erases). Try again later.');
        }
        await wait(this._busyPauseMs);
      }
    }
  }

  /** Save text or bytes (1..232 bytes) under a key 0..999. */
  async put(key, value) {
    checkKey(key);
    let bytes;
    if (typeof value === 'string') bytes = new TextEncoder().encode(value);
    else if (value instanceof Uint8Array) bytes = value;
    else if (value instanceof ArrayBuffer || Array.isArray(value)) bytes = new Uint8Array(value);
    else throw badArg('A stored value must be text or bytes (a Uint8Array).');
    if (bytes.length < 1 || bytes.length > 232) {
      throw badArg(`A stored value must be 1 to 232 bytes; this one is ${bytes.length}. (Letters with accents and emoji take more than one byte.)`);
    }
    await this._send('STORE_PUT', { key, value: bytes });
  }

  /** The bytes under a key, or null if nothing is stored there. */
  async get(key) {
    checkKey(key);
    try {
      return (await this._send('STORE_GET', { key })).value;
    } catch (e) {
      if (e.code === 'not-found') return null;
      throw e;
    }
  }

  /** The text under a key, or null if nothing is stored there. */
  async getText(key) {
    const bytes = await this.get(key);
    return bytes === null ? null : new TextDecoder().decode(bytes);
  }

  /** Remove a key. Fails with code 'not-found' if nothing is stored there. */
  async delete(key) {
    checkKey(key);
    await this._send('STORE_DEL', { key });
  }

  /** { keysMax, valueMax, freeBytes, usedKeys } */
  async info() {
    const d = await this._send('STORE_INFO');
    return { keysMax: d.keys_max, valueMax: d.value_max, freeBytes: d.free_bytes, usedKeys: d.used_keys };
  }

  /**
   * Erase every key, and wait until the band has finished (about a second), so you can
   * put() again straight away.
   * @param {object} [options]
   * @param {number} [options.timeoutSeconds] how long to wait for the erase to finish (default 30)
   */
  async wipe({ timeoutSeconds = 30 } = {}) {
    if (typeof timeoutSeconds !== 'number' || !(timeoutSeconds > 0)) {
      throw badArg(`timeoutSeconds must be a number of seconds above 0; got ${timeoutSeconds}.`);
    }
    await this._send('STORE_WIPE');
    // The band answers at once and erases afterwards. It is done when STORE_INFO stops answering BUSY.
    const deadline = Date.now() + timeoutSeconds * 1000;
    for (;;) {
      try {
        await this._band.command('STORE_INFO');
        return;
      } catch (e) {
        if (e.code === 'not-available') {
          throw new VexoError('not-available', 'The band could not erase its storage. Call band.storage.wipe() again.');
        }
        if (e.code !== 'busy') throw e;
      }
      if (Date.now() + this._busyPauseMs > deadline) {
        throw new VexoError('timeout', `The band's storage is still erasing after ${timeoutSeconds} seconds. `
          + 'Wait a little longer, then look at band.storage.info().');
      }
      await wait(this._busyPauseMs);
    }
  }
}

function checkKey(key) {
  whole(key, 0, 999, 'A storage key');
}
