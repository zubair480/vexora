// A pretend band. It answers every command in contract/contract.md the way a real band
// does (including the error replies) and makes up believable sensor data, so the library,
// the examples and the dashboard run with no hardware.
//
//   const sim = new SimTransport(42);
//   const band = await Band.connect(42, { transport: sim });
//   sim.onLed = (led) => drawTheLight(led);
//   sim.tap('double-tap');
//   sim.dropLink();                  // the link is lost (try it with { autoReconnect: true })
//
// It is a band with firmware 1.1 (fast motion, stream intervals, a clock). To get a band
// with the older firmware 1.0, which knows none of those: new SimTransport(42, { firmware: '1.0' }).
//
// Left alone it makes up its own data: a gentle swing, a slow walk, a voice that comes and
// goes, a tap every 4 seconds. To make things happen when you say so, direct it:
//
//   sim.tap('double-tap');                             // also free-fall, tilt, orientation, motion-start, motion-stop
//   sim.playMotion((s) => ({ gz: 200 }), { seconds: 0.5 });   // half a second of turning; s = seconds into the motion
//   sim.setLoudness(0.3);                              // a steady loudness, 0 (silence) to 1
//   sim.playLoudness([0.95]);                          // one loud reading: a clap
//   sim.setRssi(-85);                                  // signal strength in dBm
//   sim.setActivity('run');                            // or 'walk', 'still'
//   sim.setBattery(12);                                // percent
//   sim.dropLink(3);                                   // the link drops; the band cannot be found for 3 seconds
//   sim.after(2, () => sim.tap('free-fall'));          // in 2 seconds of band time
//   sim.endUsbSession();                               // the band ends the USB session, as firmware 1.1.3 does
//   sim.stopReading(7);                                // this computer takes no data for 7 seconds of band time
//
// Its counter (band time, the `t` of every reading) keeps counting while nothing is
// connected, like a real band's, and starts again at 0 when the band restarts. With
// { realtime: false } it only moves when you call sim.step(ms).
//
// It is built only on codec.js: commands arrive as bytes and answers leave as bytes.
import { Transport, VexoError, notFoundHelp } from '../transport.js';
import {
  MAGIC, MAX_FRAME, STREAM_BITS, EVENT_KINDS, bandName, Deframer, frameForUsb,
  decodeCommand, encodeResponse, encodeStream, encodeNotice,
} from '../codec.js';

const bit = (stream) => 1 << STREAM_BITS[stream];
const AUDIO = bit('audio');
const MIC = AUDIO | bit('mic_level');    // either one powers the microphone, which lights the mic indicator
const MOTION = bit('motion');
const MOTION2 = bit('motion2');
const KNOWN_STREAMS = { '1.0': 0x00ff, '1.1': 0x01ff };     // firmware 1.1 added bit 8, MOTION2
// Commands that came with firmware 1.1. A band with firmware 1.0 answers them UNKNOWN_OPCODE.
const V11_COMMANDS = ['TIME_SET', 'TIME_GET', 'STREAM_SET_INTERVAL', 'STREAM_GET_INTERVAL', 'MOTION2_SET', 'MOTION2_GET'];
const MOTION2_RATES = [10, 25, 50, 100, 208, 416, 833, 1666];
// The fastest MOTION2 rate each kind of link carries (contract 5.2).
const MOTION2_CAPS = {
  ble: { max_accel_hz: 833, max_gyro_hz: 833, max_both_hz: 416 },
  usb: { max_accel_hz: 1666, max_gyro_hz: 833, max_both_hz: 833 },
};
const MOTION2_FRAME_MS = 50;    // a MOTION2 frame holds the samples of this long ...
const MOTION2_MOST = { 1: 38, 2: 38, 3: 19 };   // ... but never more than fit in one frame
// From 208 a second up the motion chip runs on its own clock, which is never exactly right.
// The pretend chip is 0.8 % slow, so its measured period is a little longer than 1000000 / rate:
// code that trusts the nominal rate drifts, as on a real band. (The Python simulator has the same number.)
const CHIP_CLOCK = 1.008;
// Stream bit -> the intervals it allows, in ms (contract 5.7). Battery also allows 0 = only on change.
const INTERVALS = {
  [STREAM_BITS.activity]: { min: 100, max: 3600000, first: 1000 },
  [STREAM_BITS.battery]: { min: 1000, max: 3600000, first: 0, zero: true },
  [STREAM_BITS.temp]: { min: 100, max: 3600000, first: 5000 },
  [STREAM_BITS.link]: { min: 100, max: 3600000, first: 1000 },
  [STREAM_BITS.mic_level]: { min: 20, max: 10000, first: 100, step: 20 },
};
const CLOCK_MIN = 1577836800000;    // 2020-01-01, in ms since 1970
const CLOCK_MAX = 4102444800000;    // 2100-01-01
const ALL_CAPS = 0x07ff;
const STORE_KEYS = 1000;
const STORE_VALUE_MAX = 232;
const STORE_BYTES = 242164;     // free bytes when nothing is stored
const STORE_KEY_COST = 8;       // each key costs this much on top of its value (rounded up to 4 bytes)
const STORE_WRITES = 10;        // at most this many writes (put, delete) ...
const STORE_WRITES_MS = 1000;   // ... in any second
const LOW_BATTERY_PERCENT = 15;
const USB_CHUNK = 7;            // pretend the cable delivers a few bytes at a time
const USB_PAUSE_MS = 200;       // a computer that took nothing for this long: stream frames are given up (contract 2.2)
const USB_GIVE_UP_MS = 5200;    // ... and for this long: the band ends the USB session (firmware 1.1.3)
const USB_KEPT_MOST = 64;       // the most frames the pretend band keeps for a computer that does not read
const RESPONSE_FRAME = 0x02;    // the type byte of a response frame

// ---- directing the pretend band
const REST = { ax: 0, ay: 0, az: 1, gx: 0, gy: 0, gz: 0 };      // lying flat: 1 g straight up, no turning
// setActivity(state): state -> [code, steps a minute, motion in milli-g]
const ACTIVITIES = { still: [0, 0, 12], walk: [1, 110, 300], run: [2, 165, 600] };
const STORE_FOLDER = '.vexo-open';      // storeFile: true keeps the storage in this folder in the user's home folder

/** One entry of a playMotion / playLoudness queue. */
function curve(source, seconds) {
  if (seconds !== undefined && seconds !== null && !(typeof seconds === 'number' && seconds > 0)) {
    throw new RangeError(`seconds is how long to play it, a number above 0 (or left out), got ${seconds}.`);
  }
  const entry = { values: null, fn: null, seconds: seconds ?? null, at: 0, i: 0 };
  if (Array.isArray(source)) entry.values = [...source];
  else entry.fn = typeof source === 'function' ? source : () => source;       // one value: held steady
  return entry;
}

/**
 * Add a curve behind the ones that are waiting. One that would never end by itself
 * (a function with no `seconds`) makes way for it.
 */
function enqueue(queue, entry) {
  const last = queue.at(-1);
  if (last && last.fn && last.seconds === null) queue.pop();
  if (entry.fn || entry.values.length) queue.push(entry);
}

/**
 * The next value of the curve that is playing, or null when nothing is. Moves the curve on
 * by `step` seconds (the time between two readings).
 */
function take(queue, step) {
  while (queue.length) {
    const e = queue[0];
    let value;
    try {
      value = e.fn ? e.fn(e.at) : e.values[e.i];
    } catch (error) {
      console.error('Error in a function given to the pretend band:', error);
      queue.shift();
      continue;
    }
    e.at += step;
    e.i += 1;
    const over = e.seconds !== null && e.at >= e.seconds - 1e-9;
    if (over || (e.values && e.i >= e.values.length)) queue.shift();
    return value ?? null;
  }
  return null;
}

// Settings every new connection starts with (contract section 5).
const defaults = () => ({
  streams: 0,
  imu: { rate_hz: 25, accel_range_g: 2, gyro_range_dps: 2000 },
  events: { event_mask: 0x3f, tap_threshold_mg: 800 },
  gain: 80,
  tx: -8,
  led: { r: 0, g: 0, b: 0, brightness: 0, pattern: 0, period_ms: 1000 },
  intervals: Object.fromEntries(Object.entries(INTERVALS).map(([stream, limit]) => [stream, limit.first])),
  motion2: { rate_hz: 25, sensors: 3 },
  identifyUntil: 0,
});

/** The fastest MOTION2 rate this link carries for a `sensors` choice (1 accel, 2 gyro, 3 both). */
const motion2Cap = (sim, sensors) => Object.values(MOTION2_CAPS[sim.via])[sensors - 1];

/**
 * 1666 Hz never runs together with the gyroscope (contract 5.2). MOTION runs the gyroscope
 * unless its range is 0, so MOTION with the gyroscope on and MOTION2 at 1666 Hz never stream
 * at once. True if they would, with `streams` on and these settings.
 */
const gyroBeside1666 = (streams, motion2, imu) => Boolean(streams & MOTION) && Boolean(streams & MOTION2)
  && motion2.rate_hz === MOTION2_RATES.at(-1) && imu.gyro_range_dps !== 0;

/** Time between two MOTION2 samples in microseconds, as the band reports it. */
const motion2PeriodUs = (rate) => (rate <= 100 ? 1000000 / rate : Math.round(1000000 / rate * CHIP_CLOCK));

/** How many samples one MOTION2 frame holds. */
const motion2Count = (rate, sensors) =>
  Math.max(1, Math.min(MOTION2_MOST[sensors], Math.floor(rate * MOTION2_FRAME_MS / 1000)));

/**
 * What the pretend motion sensor reads at band time `ms`: the motion you play, or the
 * made-up swing. `step` is the time to the next reading, in seconds.
 */
function motionSample(sim, ms, step) {
  const { accel_range_g, gyro_range_dps } = sim._s.imu;
  const gyro = gyro_range_dps ? 1 : 0;      // gyro off = zeros
  if (sim._directed) {
    const told = take(sim._motions, step);
    const now = told !== null && typeof told === 'object' ? { ...REST, ...told } : REST;
    return {
      ax: clip(now.ax * 1000, accel_range_g * 1000),
      ay: clip(now.ay * 1000, accel_range_g * 1000),
      az: clip(now.az * 1000, accel_range_g * 1000),
      gx: clip(gyro * now.gx * 10, gyro_range_dps * 10),
      gy: clip(gyro * now.gy * 10, gyro_range_dps * 10),
      gz: clip(gyro * now.gz * 10, gyro_range_dps * 10),
    };
  }
  const angle = 2 * Math.PI * ms / 2000;    // one gentle swing every 2 s, the same as the Python pretend band
  return {
    ax: clip(120 * Math.sin(angle), accel_range_g * 1000),
    ay: clip(120 * Math.cos(angle), accel_range_g * 1000),
    az: clip(1000 + 40 * Math.sin(3 * angle), accel_range_g * 1000),
    gx: clip(gyro * 300 * Math.cos(angle), gyro_range_dps * 10),
    gy: clip(gyro * -300 * Math.sin(angle), gyro_range_dps * 10),
    gz: clip(gyro * 100 * Math.sin(2 * angle), gyro_range_dps * 10),
  };
}

const clip = (value, limit) => Math.max(-limit, Math.min(limit, Math.round(value)));

/** What a value of `length` bytes takes from the storage. */
const storeCost = (length) => STORE_KEY_COST + Math.ceil(length / 4) * 4;

/**
 * The checks every storage command makes after its own arguments were fine: BUSY while a
 * wipe runs, and for writes also BUSY beyond 10 a second. Returns null when it may go ahead.
 * Only a write that is then answered OK counts towards the 10.
 */
function storeBusy(sim, isWrite = false) {
  if (sim._t < sim._wipeUntil) return 'BUSY';
  if (!isWrite) return null;
  sim._writes = sim._writes.filter((at) => sim._t - at < STORE_WRITES_MS);
  return sim._writes.length >= STORE_WRITES ? 'BUSY' : null;
}

// One function per command. It gets the simulator and the decoded arguments and returns
// the response data, or a status name when the band would refuse.
const COMMANDS = {
  GET_INFO: (sim) => ({
    proto_major: 1, proto_minor: sim._minor(), fw_major: 1, fw_minor: sim._minor(), fw_patch: 0, hw: 1,
    band_no: sim.number, caps: ALL_CAPS, max_frame: MAX_FRAME, uptime_ms: sim._now(),
  }),
  GET_NAME: (sim) => ({ name: bandName(sim.number) }),
  SET_BAND_NUMBER(sim, { number, magic }) {
    if (sim.via !== 'usb') return 'DENIED';
    if (magic !== MAGIC || number < 1 || number > 999) return 'BAD_ARG';
    sim.number = number;
    return { number };
  },
  IDENTIFY(sim, { seconds }) {
    if (sim._s.streams & MIC) return 'DENIED';
    if (seconds < 1 || seconds > 30) return 'BAD_ARG';
    sim._s.identifyUntil = sim._t + seconds * 1000;
    sim._due.set('identify', sim._s.identifyUntil);
    return {};
  },
  RESTART: (sim) => sim._closeSoon('restart'),
  // Over Bluetooth the link closes. On the cable the band dozes off but keeps answering.
  SLEEP(sim) {
    if (sim.via !== 'usb') return sim._closeSoon('sleep');
    sim._due.set('doze', sim._t + 200);
    return {};
  },
  // The clock: real time in ms since 1970. The band keeps it until it restarts.
  TIME_SET(sim, { unix_ms }) {
    if (unix_ms < CLOCK_MIN || unix_ms >= CLOCK_MAX) return 'BAD_ARG';
    sim._clockZero = unix_ms - Math.round(sim._t);
    return COMMANDS.TIME_GET(sim);
  },
  TIME_GET(sim) {
    const set = sim._clockZero !== null;
    return { set: set ? 1 : 0, unix_ms: set ? sim._clockZero + Math.round(sim._t) : 0, t_ms: sim._now() };
  },

  STREAM_START(sim, { mask }) {
    if (mask & ~KNOWN_STREAMS[sim.firmware]) return 'NOT_AVAILABLE';
    if (sim.via === 'usb' && (mask & bit('link'))) return 'NOT_AVAILABLE';     // LINK is Bluetooth only; nothing starts
    // MOTION2 set faster than this link carries: nothing starts.
    if ((mask & MOTION2) && sim._s.motion2.rate_hz > motion2Cap(sim, sim._s.motion2.sensors)) return 'DENIED';
    // 1666 Hz and the gyroscope never together: whichever of MOTION and MOTION2 comes second is refused.
    if (gyroBeside1666(sim._s.streams | mask, sim._s.motion2, sim._s.imu)) return 'DENIED';
    sim._startStreams(mask & ~sim._s.streams);
    sim._elsewhere &= ~mask;                // a stream goes to the link that started it last: this one again
    return { active: sim._s.streams };
  },
  STREAM_STOP(sim, { mask }) {
    sim._stopStreams(mask & sim._s.streams);
    return { active: sim._s.streams };
  },
  STREAM_GET: (sim) => ({ active: sim._s.streams }),
  STREAM_SET_INTERVAL(sim, { stream, interval_ms }) {
    const limit = INTERVALS[stream];
    if (!limit) return 'BAD_ARG';           // motion, events and audio have no interval
    const inRange = interval_ms >= limit.min && interval_ms <= limit.max && interval_ms % (limit.step ?? 1) === 0;
    if (!inRange && !(limit.zero && interval_ms === 0)) return 'BAD_ARG';
    sim._s.intervals[stream] = interval_ms;
    if (sim._s.streams & (1 << stream)) {   // the new interval counts from now
      const job = STREAM_JOBS.find((name) => STREAM_BITS[name] === stream);
      if (interval_ms > 0) sim._due.set(job, sim._t + interval_ms);
      else sim._due.delete(job);
    }
    return COMMANDS.STREAM_GET_INTERVAL(sim, { stream });
  },
  STREAM_GET_INTERVAL(sim, { stream }) {
    const limit = INTERVALS[stream];
    if (!limit) return 'BAD_ARG';
    return { stream, interval_ms: sim._s.intervals[stream], min_ms: limit.min, max_ms: limit.max };
  },

  IMU_SET(sim, args) {
    const ok = [10, 25, 50, 100].includes(args.rate_hz) && [2, 4, 8, 16].includes(args.accel_range_g)
      && [0, 250, 500, 1000, 2000].includes(args.gyro_range_dps);
    if (!ok) return 'BAD_ARG';
    // Switching the gyroscope on while MOTION streams beside MOTION2 at 1666 Hz: nothing changes.
    if (gyroBeside1666(sim._s.streams, sim._s.motion2, args)) return 'DENIED';
    sim._s.imu = args;
    if (sim._due.has('motion')) sim._due.set('motion', sim._t + 10000 / args.rate_hz);   // new rate from now on
    return args;
  },
  IMU_GET: (sim) => sim._s.imu,
  EVENT_SET(sim, args) {
    if (args.event_mask > 0x3f || args.tap_threshold_mg < 200 || args.tap_threshold_mg > 8000) return 'BAD_ARG';
    sim._s.events = args;
    return args;
  },
  EVENT_GET: (sim) => sim._s.events,
  MOTION2_SET(sim, { rate_hz, sensors }) {
    if (!MOTION2_RATES.includes(rate_hz) || sensors < 1 || sensors > 3) return 'BAD_ARG';
    // The cap only counts while MOTION2 streams; otherwise STREAM_START checks it.
    const streaming = Boolean(sim._s.streams & MOTION2);
    if (streaming && rate_hz > motion2Cap(sim, sensors)) return 'DENIED';
    // ... and so does "1666 Hz never with the gyroscope", which MOTION runs.
    if (gyroBeside1666(sim._s.streams, { rate_hz, sensors }, sim._s.imu)) return 'DENIED';
    const changed = rate_hz !== sim._s.motion2.rate_hz || sensors !== sim._s.motion2.sensors;
    sim._s.motion2 = { rate_hz, sensors };
    if (streaming && changed) sim._due.set('motion2', sim._t + sim._firstIn('motion2'));    // sample numbers start again
    return COMMANDS.MOTION2_GET(sim);
  },
  MOTION2_GET: (sim) => ({ ...sim._s.motion2, ...MOTION2_CAPS[sim.via] }),

  MIC_SET_GAIN(sim, { gain_half_db }) {
    if (gain_half_db > 80) return 'BAD_ARG';
    sim._s.gain = gain_half_db;
    return { gain_half_db };
  },
  MIC_GET: (sim) => ({ gain_half_db: sim._s.gain, sample_rate: 16000, frame_ms: 20, channels: 1, bitrate: 24000 }),

  RADIO_SET_TX_POWER(sim, { dbm }) {
    if (![-20, -16, -12, -8, -4, 0, 4].includes(dbm)) return 'BAD_ARG';
    sim._s.tx = dbm;
    return { dbm };
  },
  RADIO_GET(sim) {
    if (sim.via === 'usb') return { tx_dbm: sim._s.tx, rssi_dbm: 127, phy: 0, interval: 0, mtu: 0 };
    return { tx_dbm: sim._s.tx, rssi_dbm: sim._rssi(), phy: 2, interval: sim._s.streams & AUDIO ? 12 : 24, mtu: 247 };
  },

  LED_SET(sim, args) {
    if (sim._s.streams & MIC) return 'DENIED';
    const usesPeriod = args.pattern >= 2;
    if (args.pattern > 5 || (usesPeriod && (args.period_ms < 100 || args.period_ms > 10000))) return 'BAD_ARG';
    sim._s.led = args;
    return {};
  },
  LED_GET: (sim) => sim._s.led,

  TEMP_SET_INTERVAL(sim, { seconds }) {
    if (seconds < 1) return 'BAD_ARG';
    sim._s.intervals[STREAM_BITS.temp] = seconds * 1000;     // the same setting as STREAM_SET_INTERVAL of temp
    return { seconds };
  },
  BATTERY_GET: (sim) => sim._battery(),

  // Storage. Each command checks its arguments first, then whether the storage is busy.
  STORE_INFO(sim) {
    if (storeBusy(sim)) return 'BUSY';
    let used = 0;
    for (const value of sim._store.values()) used += storeCost(value.length);
    return { keys_max: STORE_KEYS, value_max: STORE_VALUE_MAX, free_bytes: STORE_BYTES - used, used_keys: sim._store.size };
  },
  STORE_PUT(sim, { key, value }) {
    if (value.length < 1 || value.length > STORE_VALUE_MAX) return 'BAD_LENGTH';
    if (key >= STORE_KEYS) return 'BAD_ARG';
    if (storeBusy(sim, true)) return 'BUSY';
    const old = sim._store.has(key) ? storeCost(sim._store.get(key).length) : 0;
    const free = COMMANDS.STORE_INFO(sim).free_bytes + old;
    if (storeCost(value.length) > free) return 'NO_SPACE';
    sim._store.set(key, value);
    sim._writes.push(sim._t);
    return {};
  },
  STORE_GET(sim, { key }) {
    if (key >= STORE_KEYS) return 'BAD_ARG';
    if (storeBusy(sim)) return 'BUSY';
    return sim._store.has(key) ? { value: sim._store.get(key) } : 'NOT_FOUND';
  },
  STORE_DEL(sim, { key }) {
    if (key >= STORE_KEYS) return 'BAD_ARG';
    if (storeBusy(sim, true)) return 'BUSY';
    if (!sim._store.delete(key)) return 'NOT_FOUND';
    sim._writes.push(sim._t);
    return {};
  },
  // Answers OK when the erase starts. Until it is over (wipeMs of band time) every storage
  // command gets BUSY. Nobody can look meanwhile, so the keys are simply dropped now.
  STORE_WIPE(sim, { magic }) {
    if (magic !== MAGIC) return 'BAD_ARG';
    if (storeBusy(sim)) return 'BUSY';
    sim._store.clear();
    sim._wipeUntil = sim._t + sim.wipeMs;
    return {};
  },
};

// The repeating jobs. Each one sends its frame and returns how many ms until it runs
// again (0 = do not repeat). They run on the simulator's own clock, see step().
const JOBS = {
  motion(sim) {
    const period = 1000 / sim._s.imu.rate_hz;
    const first = sim._t - 9 * period;      // a frame leaves when its 10th sample is taken
    const samples = [];
    for (let i = 0; i < 10; i++) samples.push(motionSample(sim, first + i * period, period / 1000));
    sim._stream('motion', { t_ms: Math.round(first) >>> 0, period_ms: period, samples });
    return 10 * period;
  },
  // Fast motion: every sample has a number, and a frame leaves when its last sample is taken.
  motion2(sim) {
    const { rate_hz, sensors } = sim._s.motion2;
    const period_us = motion2PeriodUs(rate_hz);
    const count = motion2Count(rate_hz, sensors);
    const run = sim._motion2;               // { from: band time it started, taken: samples so far }
    const at = (number) => run.from + (number + 1) * period_us / 1000;
    const samples = [];
    for (let i = 0; i < count; i++) {
      const { ax, ay, az, gx, gy, gz } = motionSample(sim, at(run.taken + i), period_us / 1e6);
      samples.push(sensors === 1 ? { ax, ay, az } : sensors === 2 ? { gx, gy, gz } : { ax, ay, az, gx, gy, gz });
    }
    sim._stream('motion2', { t_ms: Math.round(at(run.taken)) >>> 0, sample_no: run.taken >>> 0, period_us, sensors, samples });
    run.taken += count;
    return count * period_us / 1000;
  },
  battery(sim) {                            // only when a battery interval is set
    sim._stream('battery', sim._battery());
    return sim._s.intervals[STREAM_BITS.battery];
  },
  activity(sim) {
    if (sim._walk) {                        // setActivity: steps by the band's clock
      sim._countSteps();
      const { state, cadence, motion } = sim._walk;
      sim._stream('activity', { t_ms: sim._now(), steps_total: sim._steps >>> 0, cadence_spm: cadence, state, motion_mg: motion });
      return sim._s.intervals[STREAM_BITS.activity];
    }
    sim._steps += 2;                        // a steady walk
    sim._stream('activity', { t_ms: sim._now(), steps_total: sim._steps, cadence_spm: 110, state: 1, motion_mg: 300 + Math.round(40 * Math.sin(sim._t / 3000)) });
    return sim._s.intervals[STREAM_BITS.activity];
  },
  audio(sim) {
    let opus;
    if (sim._packets.length) {
      opus = sim._packets.shift();          // given to playLoudness: each one is sent once
    } else if (sim.audioPackets) {
      opus = sim.audioPackets[sim._audioSeq % sim.audioPackets.length];
    } else {
      // A real, silent Opus packet whose size swells like speech: one empty 20 ms frame
      // (TOC 0xBB = CELT wideband mono, "code 3"), then `padding` bytes of padding.
      const padding = 50 + Math.round(30 * Math.sin(sim._t / 400));
      opus = new Uint8Array(3 + padding);
      opus.set([0xbb, 0x41, padding]);
    }
    sim._stream('audio', { seq: sim._audioSeq & 0xffff, opus });
    sim._audioSeq++;
    return 20;
  },
  temp(sim) {
    const celsius = 31 + 0.5 * Math.sin(sim._t / 9000);
    sim._stream('temp', { t_ms: sim._now(), temp_mc: Math.round(celsius * 4) * 250 });     // 0.25 degree steps
    return sim._s.intervals[STREAM_BITS.temp];
  },
  link(sim) {
    sim._stream('link', { t_ms: sim._now(), rssi_dbm: sim._rssi(), tx_dbm: sim._s.tx, phy: 2 });
    return sim._s.intervals[STREAM_BITS.link];
  },
  mic_level(sim) {
    // Loudness swells like someone talking, and a lower gain makes everything quieter.
    const swell = 0.5 + 0.5 * Math.sin(sim._t / 700);
    let rms = Math.min(96, Math.round(50 - 30 * swell + (80 - sim._s.gain) / 2));
    let peak = Math.max(0, rms - 8);
    let level = take(sim._loudness, sim._s.intervals[STREAM_BITS.mic_level] / 1000);
    if (typeof level !== 'number') level = sim._loud;
    if (level !== null) {                   // directed: `level` is what a program reads at the default gain (+20 dB)
      rms = Math.round(Math.max(0, Math.min(96, 96 * (1 - Math.max(0, Math.min(1, level))) + (80 - sim._s.gain) / 2)));
      peak = Math.max(0, rms - 6);
    }
    sim._stream('mic_level', { t_ms: sim._now(), peak_db: peak, rms_db: rms });
    return sim._s.intervals[STREAM_BITS.mic_level];
  },
  // Not streams, but they run on the same clock:
  identify: () => 0,                        // identify time is over; step() redraws the LED
  drain(sim) {
    if (sim._batteryState === 0 && sim._percent > 1) sim.setBattery(sim._percent - 1);
    return 60000;
  },
  script(sim) {                             // realtime only: somebody taps now and then
    sim.tap(sim._scriptCount++ % 2 ? 'double-tap' : 'tap');
    return 4000;
  },
  shutdown(sim) {
    sim._shutdown(sim._closeReason);
    return 0;
  },
  listen(sim) {                             // stopReading(seconds) is over
    sim._readAgain();
    return 0;
  },
  giveUp(sim) {                             // nothing was taken for 5.2 seconds: the session ends
    if (!sim._deaf) return 0;
    sim._deaf.ended = true;
    sim.endUsbSession();
    return 0;
  },
  doze(sim) {                               // SLEEP on the cable: streams stop, light off, session stays
    sim._stopStreams(sim._s.streams);
    sim._s.led = defaults().led;
    sim._s.identifyUntil = 0;
    sim._due.delete('identify');
    return 0;
  },
};

// configure() name -> stream bit, for otherProgramStarts()
const CONFIGURE_BITS = {
  motion: STREAM_BITS.motion, events: STREAM_BITS.event, activity: STREAM_BITS.activity, audio: STREAM_BITS.audio,
  battery: STREAM_BITS.battery, temperature: STREAM_BITS.temp, link: STREAM_BITS.link, micLevel: STREAM_BITS.mic_level,
};

const STREAM_JOBS = ['motion', 'activity', 'audio', 'battery', 'temp', 'link', 'mic_level', 'motion2'];

export class SimTransport extends Transport {
  kind = 'sim';

  /**
   * @param {number} [number] the pretend band's number
   * @param {object} [options]
   * @param {boolean} [options.realtime] true (default) = runs by itself; false = no timers, call step(ms) yourself (tests)
   * @param {'ble'|'usb'} [options.via] which kind of link to imitate (default 'ble')
   * @param {Uint8Array[]} [options.audioPackets] Opus packets to play in a loop instead of silence
   * @param {number} [options.wipeMs] how long erasing the storage takes, in ms of band time (default 1000)
   * @param {'1.0'|'1.1'} [options.firmware] which firmware to imitate (default '1.1'). '1.0' is a band
   *   without fast motion, stream intervals and a clock: it answers those commands UNKNOWN_OPCODE
   * @param {boolean} [options.autoTap] false = it does not tap itself every 4 seconds (only your own tap() calls count)
   * @param {string|boolean|null} [options.storeFile] where the pretend band's storage is kept between two runs
   *   of a program. Left out: nowhere, it lives in memory and is gone when the program (or the page) ends.
   *   A file name: that file (its folder must exist). true: the file pretend-band-<number>.json in the
   *   folder .vexo-open in your home folder. The file is only readable by you. Node only: a web page has
   *   no files, and open() then fails with code 'not-available'. Like a real band's storage it is not a
   *   place for anything private.
   */
  constructor(number = 42, {
    realtime = true, via = 'ble', audioPackets = null, wipeMs = 1000, firmware = '1.1', autoTap = true, storeFile = null,
  } = {}) {
    super();
    bandName(number);                       // throws if the number is not 0..999
    if (via !== 'ble' && via !== 'usb') throw new RangeError(`via must be 'ble' or 'usb', got "${via}"`);
    if (!KNOWN_STREAMS[firmware]) throw new RangeError(`firmware must be '1.0' or '1.1', got "${firmware}"`);
    if (typeof autoTap !== 'boolean') throw new RangeError(`autoTap must be true or false, got "${autoTap}"`);
    if (storeFile !== null && storeFile !== false && storeFile !== true && (typeof storeFile !== 'string' || !storeFile)) {
      throw new RangeError('storeFile must be a file name, true (a file in your home folder) or left out');
    }
    this.number = number;
    this.realtime = realtime;
    this.autoTap = autoTap;
    this.via = via;
    /** '1.1' or '1.0'. Change it while the link is down to pretend the band came back with other firmware. */
    this.firmware = firmware;
    this._clockZero = null;                 // real time (ms since 1970) at band time 0; null = clock not set
    this.audioPackets = audioPackets && audioPackets.length ? audioPackets : null;
    this.wipeMs = wipeMs;
    /**
     * false = the band cannot be found (out of range, asleep): open() fails with code
     * 'not-found-nearby' until you set it back to true. dropLink() leaves it true.
     */
    this.reachable = true;
    /**
     * true = something else is connected to the band over Bluetooth (the dashboard, a
     * teammate's program), so it cannot be found: open() fails with code 'not-found-nearby'
     * and the same sentence a real band gives, until you set it back to false.
     */
    this.connectedElsewhere = false;
    /** Called with the light the band shows whenever it changes: { r, g, b, brightness, pattern, period_ms, source }. */
    this.onLed = () => {};
    /** Called with every chunk of bytes on the pretend USB cable: ('to-band' | 'to-computer', Uint8Array). */
    this.onWire = () => {};
    /**
     * true = the library watches this pretend band the way it watches a band on the USB cable
     * (for a session the band ended). false until endUsbSession() or stopReading() is used.
     */
    this.sessionWatch = false;
    /** Assigned by Band. Called when an empty packet arrives in the middle of a session. */
    this.onSessionHint = () => {};
    // Kept for the whole life of this object, like a real band keeps them:
    this._store = new Map();
    this._steps = 1200;
    this._percent = 87;
    this._batteryState = 0;                 // 0 on battery, 1 charging, 2 charged
    this._t = 0;                            // the band's clock in ms
    this._writes = [];                      // band times of the last accepted storage writes
    this._wipeUntil = 0;                    // storage is erasing until this band time (a wipe outlives the link)
    // What you directed (see "directing the pretend band" above). It all outlives a link.
    this._motions = [];                     // playMotion: the motions waiting to be played, one after the other
    this._directed = false;                 // playMotion was used: between two motions the band lies still
    this._loud = null;                      // setLoudness: the steady loudness, null = the made-up voice
    this._loudness = [];                    // playLoudness: the curves waiting to be played
    this._packets = [];                     // ... and their Opus packets, each sent once
    this._rssiSet = null;                   // setRssi
    this._walk = null;                      // setActivity: { state, cadence, motion, since, part }
    this._plans = [];                       // after(): { at, action }, soonest first
    this._awayUntil = null;                 // dropLink(seconds): band time from which it can be found again
    this._downSince = null;                 // realtime: the computer's clock when the link closed
    this._rebased = 0;                      // band time taken away by a restart during step()
    this._storeFile = storeFile || null;    // storeFile: a file name, or true
    this._files = null;                     // Node's file functions, once the storage file was read
    this._saved = '{}';                     // the storage as it is in the file
    // Reset on every connection:
    this._open = false;
    this._s = defaults();
    this._elsewhere = 0;                    // otherProgramStarts(): the streams that go to the band's other link
    this._due = new Map();                  // job name -> time it runs next
    this._later = [];                       // things to send right after the current response
    this._timer = null;
    this._lastLed = JSON.stringify(this.led);
  }

  // ---------------------------------------------------------------- transport interface

  async open() {
    if (this._open) return;
    if (this._storeFile && !this._files) await this._loadStore();
    this._catchUp();
    if (this.connectedElsewhere) throw new VexoError('not-found-nearby', notFoundHelp(bandName(this.number)));
    if (!this.reachable) {
      throw new VexoError('not-found-nearby', `The pretend band ${bandName(this.number)} cannot be found right now (its "reachable" switch is off).`);
    }
    if (this._awayUntil !== null && this._t < this._awayUntil) {
      throw new VexoError('not-found-nearby', `The pretend band ${bandName(this.number)} is out of range for another `
        + `${((this._awayUntil - this._t) / 1000).toFixed(1)} seconds (dropLink was called with seconds).`);
    }
    this._awayUntil = null;
    this._downSince = null;
    this._open = true;
    this._s = defaults();
    this._elsewhere = 0;                    // otherProgramStarts(): the streams that go to the band's other link
    this._due.clear();
    this._deaf = null;                      // stopReading(): { from, ended, kept, dropped }
    this._newSession = false;               // endUsbSession(): the next frame has one 00 in front of it
    this._due.set('drain', this._t + 60000);
    if (this.via === 'usb') {
      // Both ends cut the byte flow back into frames. The band drops bytes up to the first
      // 00 (contract 2.2), so the computer starts by sending one.
      this._bandSide = new Deframer();
      this._computerSide = new Deframer({ skipFirst: false });
      this._wire('to-band', Uint8Array.of(0));
    }
    if (this.realtime) {
      this._scriptCount = 0;
      if (this.autoTap) this._due.set('script', this._t + 4000);
      let last = Date.now();
      this._timer = setInterval(() => {
        const now = Date.now();
        this.step(Math.min(now - last, 250));
        last = now;
      }, 20);
    }
  }

  async close() {
    this._shutdown('closed');
  }

  async send(frame) {
    if (!this._open) throw new VexoError('not-connected', 'The simulated band is not connected.');
    if (this.via === 'usb') this._wire('to-band', frameForUsb(frame));
    else this._command(frame);
  }

  // ---------------------------------------------------------------- for pages and tests

  /**
   * Move the band's clock forward by `ms` and send everything it would have sent. The
   * counter also moves while the link is closed (nothing is sent then).
   */
  step(ms) {
    if (!this._open) {
      this._t += ms;
      return;
    }
    const end = this._t + ms;
    this._rebased = 0;
    for (;;) {
      let next = null;
      for (const [name, at] of this._due) {
        if (at <= end && (next === null || at < this._due.get(next))) next = name;
      }
      const plan = this._plans[0];
      if (plan && plan.at <= end && (next === null || plan.at < this._due.get(next))) {
        this._t = Math.max(this._t, plan.at);
        this._plans.shift();
        try {
          plan.action();
        } catch (e) {
          console.error("Error in a function given to the pretend band's after():", e);
        }
      } else {
        if (next === null) break;
        const at = this._due.get(next);
        this._t = Math.max(this._t, at);
        this._due.delete(next);
        const again = JOBS[next](this);
        if (this._open && again > 0 && !this._due.has(next)) this._due.set(next, at + again);
      }
      if (!this._open) {
        // The link closed meanwhile. The rest of the time still passes; after a restart on the new counter.
        this._t = Math.max(this._t, end - this._rebased);
        return;
      }
      this._ledChanged();
    }
    this._t = end;
  }

  /** realtime: a band goes on counting while nothing is connected. Add the time the link has been closed. */
  _catchUp() {
    if (this._downSince === null) return;
    const now = Date.now();
    this._t += Math.max(0, now - this._downSince);
    this._downSince = now;
  }

  /** The light the band shows right now (the mic indicator and identify win over your setting). */
  get led() {
    const off = { r: 0, g: 0, b: 0, brightness: 0, pattern: 0, period_ms: 1000, source: 'user' };
    if (!this._open) return off;
    if (this._s.streams & MIC) return { r: 0, g: 0, b: 255, brightness: 255, pattern: 1, period_ms: 1000, source: 'mic' };
    if (this._t < this._s.identifyUntil) return { r: 255, g: 255, b: 255, brightness: 255, pattern: 2, period_ms: 250, source: 'identify' };
    const cap = this._isLow() ? 64 : 255;   // a low battery dims the LED
    return { ...this._s.led, brightness: Math.min(this._s.led.brightness, cap), source: 'user' };
  }

  /**
   * Pretend something happened to the band: tap('tap'), tap('double-tap'), tap('orientation', 4), ...
   * Returns true if an EVENT frame was sent (the stream is on and this kind is enabled).
   */
  tap(kind = 'tap', arg = 0) {
    const code = typeof kind === 'number' ? kind : Number(Object.keys(EVENT_KINDS).find((c) => EVENT_KINDS[c] === kind));
    if (!EVENT_KINDS[code]) throw new RangeError(`Unknown event "${kind}". Events: ${Object.values(EVENT_KINDS).join(', ')}.`);
    const maskBit = Math.min(code, 6) - 1;  // motion-start and motion-stop share bit 5
    if (!this._open || !(this._s.streams & bit('event')) || !(this._s.events.event_mask & (1 << maskBit))) return false;
    this._stream('event', { t_ms: this._now(), event: code, arg });
    return true;
  }

  /** Set the pretend battery. state: 'battery' (default), 'charging' or 'charged'. */
  setBattery(percent, state = 'battery') {
    const code = typeof state === 'number' ? state : ['battery', 'charging', 'charged'].indexOf(state);
    if (!(percent >= 0 && percent <= 100) || code < 0 || code > 2) throw new RangeError('setBattery(percent 0..100, "battery" | "charging" | "charged")');
    const wasLow = this._isLow();
    this._percent = Math.round(percent);
    this._batteryState = code;
    if (!this._open) return;
    // With a battery interval set, frames leave on the timer, not on a change.
    const onChange = this._s.intervals[STREAM_BITS.battery] === 0;
    if ((this._s.streams & bit('battery')) && onChange) this._stream('battery', this._battery());
    if (this._isLow() && !wasLow) this._send(encodeNotice('LOW_BATTERY', this._percent));
    this._ledChanged();
  }

  /**
   * Pretend the link was lost (the band went out of range, or the cable was pulled).
   * The band can be found again at once, like a real one. dropLink(3): it cannot be found
   * for the next 3 seconds and comes back by itself. For "away until I say so" set
   * `reachable = false` first and back to true later.
   */
  dropLink(seconds = null) {
    if (seconds !== null) {
      if (typeof seconds !== 'number' || !(seconds >= 0)) throw new RangeError(`dropLink seconds is how long the band stays away, 0 or more, got ${seconds}.`);
      this._awayUntil = this._t + seconds * 1000;
    }
    this._shutdown('lost');
  }

  /**
   * Pretend a second program, on the band's other link (the USB cable when this one is
   * Bluetooth), started these streams: otherProgramStarts('motion', 'micLevel'). The names
   * are the ones configure() takes. A stream goes to the program that started it last, so
   * this program gets no more of them, with no notice; its link stays up, its commands are
   * answered and STREAM_GET still says the streams are on. The library notices the silence
   * after a few seconds ('stream-taken'); band.takeBack() starts them for this program again.
   */
  otherProgramStarts(...names) {
    let mask = 0;
    for (const name of names.flat()) {
      const key = name === 'mic_level' ? 'micLevel' : name;
      if (!Object.hasOwn(CONFIGURE_BITS, key)) throw new RangeError(`Unknown stream "${name}". Streams: ${Object.keys(CONFIGURE_BITS).join(', ')}.`);
      // motion: the motion stream that is on; otherwise the one this firmware's library would start
      if (key === 'motion') mask |= (this._s.streams & (bit('motion') | MOTION2)) || (this.firmware === '1.0' ? bit('motion') : MOTION2);
      else mask |= 1 << CONFIGURE_BITS[key];
    }
    if (!this._open) return;
    this._startStreams(mask & ~this._s.streams);
    this._elsewhere |= mask;
    this._ledChanged();
  }

  /**
   * Pretend that second program ended: the band stops the streams it had taken (a link
   * that closes takes its streams with it). STREAM_GET now says they are off.
   */
  otherProgramCloses() {
    if (!this._open) return;
    this._stopStreams(this._elsewhere);
    this._ledChanged();
  }

  /**
   * Pretend the band ended the USB session by itself, as firmware 1.1.3 does when the
   * computer took no data for 5 seconds: every stream stops, every setting goes back to
   * its default (as with no Bluetooth client connected), and the band says nothing. The link stays open and
   * commands are still answered. The next frame has one extra 00 in front of it (an empty
   * packet). From this call on the library watches this pretend band like a band on the
   * USB cable: it notices the silence and ends the link with reason 'session-ended'.
   */
  endUsbSession() {
    this.sessionWatch = true;
    if (!this._open) return;
    this._stopStreams(this._s.streams);
    this._s = defaults();
    this._due.delete('identify');
    this._later = [];
    if (this._deaf) this._deaf.kept = [];   // an answer that was still waiting is thrown away
    this._newSession = true;
    this._ledChanged();
  }

  /**
   * Pretend the band paused because the computer took nothing for 200 ms (firmware 1.1.2
   * and later): one empty packet, then a 'stream-dropped' notice for each stream that is
   * on. The session goes on: streams and settings stay. (The library must NOT take this
   * for a session end.)
   */
  usbPause() {
    this.sessionWatch = true;
    if (!this._open) return;
    this._emptyPacket();
    for (const number of Object.values(STREAM_BITS)) {
      if (this._s.streams & (1 << number)) this._send(encodeNotice('STREAM_DROPPED', number));
    }
  }

  /**
   * Pretend this computer takes no data for `seconds` of band time (a page or program that
   * is stuck), on a computer with no buffer of its own. The band behaves as firmware 1.1.3:
   * after 200 ms it gives up stream frames (answers are kept), and once it has had data
   * waiting for 5.2 seconds it ends the session, as endUsbSession() does. With no stream on
   * nothing waits, and the session is never ended. When the time is over the computer reads again.
   */
  stopReading(seconds) {
    if (typeof seconds !== 'number' || !(seconds > 0)) throw new RangeError(`stopReading seconds is how long nothing is taken, a number above 0, got ${seconds}.`);
    this.sessionWatch = true;
    if (!this._open) return;
    this._deaf = this._deaf ?? { from: null, ended: false, kept: [], dropped: false };
    this._due.set('listen', this._t + seconds * 1000);
  }

  /** stopReading() is over: what the band kept arrives, after an empty packet if it paused. */
  _readAgain() {
    const deaf = this._deaf;
    this._deaf = null;
    this._due.delete('giveUp');
    if (!deaf || deaf.from === null || deaf.ended) return;
    if (deaf.dropped) this._emptyPacket();
    for (const frame of deaf.kept) this._send(frame);
    if (!deaf.dropped) return;
    for (const number of Object.values(STREAM_BITS)) {
      if (this._s.streams & (1 << number)) this._send(encodeNotice('STREAM_DROPPED', number));
    }
  }

  /**
   * For a fake USB port in a test: bytes the computer wrote to the cable. Only with
   * { via: 'usb' }. What the band sends back comes out of onWire('to-computer', bytes).
   */
  writeBytes(bytes) {
    if (!this._open || this.via !== 'usb') throw new VexoError('not-connected', 'writeBytes needs an open pretend band made with { via: "usb" }.');
    this._wire('to-band', bytes);
  }

  /** One empty packet: a 00 on the pretend cable, and the sign the transport gives for it. */
  _emptyPacket() {
    if (this.via === 'usb') this._wire('to-computer', Uint8Array.of(0));
    try {
      this.onSessionHint();
    } catch (e) {
      console.error('Error in onSessionHint:', e);
    }
  }

  /**
   * Pretend the band restarted by itself (its reset button, an empty battery that was
   * charged again): the link is lost, the band's counter starts again at 0 and its clock is
   * forgotten. Storage and the step count stay. The band can be found again at once.
   * (band.restart() asks a band to do this; then the reason is 'restart'.)
   */
  powerCycle() {
    this._catchUp();
    this._clockZero = null;
    this._restartCounter();
    this._shutdown('lost');
  }

  /**
   * Play a motion instead of the made-up swing. `source` is one of:
   * - a function of s, the seconds since this motion began. It returns an object with any of
   *   ax ay az (in g) and gx gy gz (in degrees/second); what it leaves out is at rest (az 1,
   *   the others 0). Example: (s) => ({ gz: 200 })
   * - a list of such objects: one for each sample the band takes, at whatever rate is on
   * - one such object: held steady
   *
   * `seconds`: how long it plays (left out: a list plays once to its end, a function or a
   * single object until you call playMotion again or stopMotion()). Motions queue up: a
   * second call plays after the first has ended. Between and after them the band lies flat
   * and still. A motion only moves on while the motion stream is on.
   */
  playMotion(source, { seconds = null } = {}) {
    enqueue(this._motions, curve(source, seconds));
    this._directed = true;
  }

  /** Forget every motion from playMotion and go back to the made-up swing. */
  stopMotion() {
    this._motions = [];
    this._directed = false;
  }

  /** True while a motion from playMotion is playing or waiting. */
  get moving() {
    return this._motions.length > 0;
  }

  /**
   * A steady loudness for the mic-level stream: the `level` a program reads at the band's
   * default gain, 0 (silence) to 1 (as loud as it can measure). In a quiet room a real band
   * reads about 0.5, with speech nearby about 0.7 to 0.8. null: back to the made-up voice.
   */
  setLoudness(level) {
    if (level !== null && !(typeof level === 'number' && level >= 0 && level <= 1)) {
      throw new RangeError(`setLoudness takes a level from 0 (silence) to 1 (loudest), or null, got ${level}.`);
    }
    this._loud = level;
  }

  /**
   * Play a loudness curve on the mic-level stream, on top of setLoudness. `source`: a
   * function of s (seconds since it began) that returns a level 0..1, a list of levels (one
   * for each reading; playLoudness([0.95]) is one clap), or one level. `seconds` and the
   * queue work as in playMotion. Afterwards the loudness is the steady one again.
   *
   * `packets`: Opus packets (Uint8Array, 20 ms each) for the audio stream. Each is sent
   * once, in order, then the band goes back to audioPackets or silence. The pretend band
   * cannot hear them: give a curve that fits the sound yourself.
   */
  playLoudness(source, { seconds = null, packets = null } = {}) {
    enqueue(this._loudness, curve(source, seconds));
    for (const packet of packets ?? []) this._packets.push(Uint8Array.from(packet));
  }

  /**
   * The signal strength the band reports (link events and getRadio), in dBm: about -20 next
   * to the computer, -90 far away. null: back to the made-up value around -55.
   */
  setRssi(dbm) {
    if (dbm !== null && !(typeof dbm === 'number' && dbm >= -127 && dbm <= 20)) {
      throw new RangeError(`setRssi takes dBm from -127 to 20 (for example -60), or null, got ${dbm}.`);
    }
    this._rssiSet = dbm === null ? null : Math.round(dbm);
  }

  /**
   * What the wearer does: 'still', 'walk' or 'run'. `cadence`: steps a minute (left out: 0,
   * 110, 165). The band then counts steps by its own clock, also while nothing is
   * connected, like a real one. `steps`: set the lifetime step count to this number.
   */
  setActivity(state = 'walk', { cadence = null, steps = null } = {}) {
    if (!Object.hasOwn(ACTIVITIES, state)) throw new RangeError(`setActivity takes 'still', 'walk' or 'run', got "${state}".`);
    const [code, usual, motion] = ACTIVITIES[state];
    const pace = cadence ?? usual;
    if (typeof pace !== 'number' || !(pace >= 0 && pace <= 255)) throw new RangeError(`setActivity cadence is steps a minute, 0 to 255, got ${cadence}.`);
    this._countSteps();                     // what was walked so far, at the old pace
    if (steps !== null) this._steps = Math.trunc(steps);
    this._walk = { state: code, cadence: Math.round(pace), motion, since: this._t, part: 0 };
  }

  /**
   * Call action() when `seconds` more have passed on the band's clock, for example
   * sim.after(2, () => sim.tap('free-fall')). It runs while the link is open; if the link
   * is down at that moment it runs right after the next connect.
   */
  after(seconds, action) {
    if (typeof seconds !== 'number' || !(seconds >= 0) || typeof action !== 'function') {
      throw new RangeError('after(seconds, action) takes a number of seconds, 0 or more, and a function.');
    }
    this._plans.push({ at: this._t + seconds * 1000, action });
    this._plans.sort((a, b) => a.at - b.at);
  }

  // ---------------------------------------------------------------- inside the pretend band

  _now() {
    return Math.round(this._t) >>> 0;
  }

  /** The second number of the protocol and firmware versions: 1.0 or 1.1. */
  _minor() {
    return this.firmware === '1.0' ? 0 : 1;
  }

  _rssi() {
    if (this._rssiSet !== null) return this._rssiSet;
    return -55 + Math.round(3 * Math.sin(this._t / 2300));
  }

  _isLow() {
    return this._batteryState === 0 && this._percent < LOW_BATTERY_PERCENT;
  }

  /** The band's counter begins at 0 again; everything that was timed on it moves along. */
  _restartCounter() {
    const was = this._t;
    this._rebased += was;
    this._t = 0;
    this._writes = this._writes.map((at) => at - was);
    this._wipeUntil -= was;
    if (this._awayUntil !== null) this._awayUntil -= was;
    for (const plan of this._plans) plan.at -= was;
    if (this._walk) this._walk.since -= was;
  }

  /** Bring the step count up to date: the band counts by its clock, connected or not. */
  _countSteps() {
    const walk = this._walk;
    if (!walk) return;
    const steps = walk.part + (this._t - walk.since) * walk.cadence / 60000;
    this._steps += Math.floor(steps);
    walk.part = steps - Math.floor(steps);
    walk.since = this._t;
  }

  /** storeFile: read the file (once). Anything that is not a store this band could hold is ignored. */
  async _loadStore() {
    let fs;
    let path;
    let os;
    let crypto;
    try {
      [fs, path, os, crypto] = await Promise.all(['node:fs', 'node:path', 'node:os', 'node:crypto'].map((name) => import(name)));
    } catch {
      throw new VexoError('not-available', 'The pretend band can only keep its storage in a file (storeFile) in Node. '
        + 'In a web page leave storeFile out: the storage then lives in memory.');
    }
    const file = this._storeFile === true ? path.join(os.homedir(), STORE_FOLDER, `pretend-band-${this.number}.json`) : path.resolve(this._storeFile);
    const folder = path.dirname(file);
    if (this._storeFile === true) fs.mkdirSync(folder, { recursive: true, mode: 0o700 });     // only you can look into it
    else if (!fs.existsSync(folder)) throw new VexoError('bad-arg', `SimTransport storeFile: the folder ${folder} does not exist.`);
    this._files = { fs, path, crypto, file, folder };
    try {
      if (fs.statSync(file).size > 4 * STORE_BYTES) return;
      const store = new Map();
      let used = 0;
      for (const [name, hex] of Object.entries(JSON.parse(fs.readFileSync(file, 'utf8')))) {
        const key = Number(name);
        if (!Number.isInteger(key) || key < 0 || key >= STORE_KEYS || typeof hex !== 'string' || !/^([0-9a-f]{2})+$/i.test(hex)) return;
        const value = Uint8Array.from(hex.match(/../g), (pair) => parseInt(pair, 16));
        if (value.length > STORE_VALUE_MAX) return;
        used += storeCost(value.length);
        store.set(key, value);
      }
      if (used > STORE_BYTES) return;
      this._store = store;
      this._saved = this._storeText();
    } catch {
      // no file yet, or not a storage file: start empty; it is written at the next change
    }
  }

  _storeText() {
    const keys = [...this._store.keys()].sort((a, b) => a - b);
    return JSON.stringify(Object.fromEntries(keys.map((key) => [key, Array.from(this._store.get(key), (b) => b.toString(16).padStart(2, '0')).join('')])));
  }

  /**
   * storeFile: write the storage to its file when it has changed. The new file is written
   * beside the old one under a name nobody can guess, readable by you only, and then put in
   * its place in one step, so a reader never sees half a file.
   */
  _saveStore() {
    if (!this._files) return;
    const text = this._storeText();
    if (text === this._saved) return;
    const { fs, path, crypto, file, folder } = this._files;
    const part = path.join(folder, `.pretend-band-${crypto.randomBytes(8).toString('hex')}.part`);
    try {
      fs.writeFileSync(part, text, { mode: 0o600, flag: 'wx' });
      fs.renameSync(part, file);
    } catch (e) {
      fs.rmSync(part, { force: true });
      throw e;
    }
    this._saved = text;
  }

  _battery() {
    return { percent: this._percent, state: this._batteryState, millivolts: 3300 + this._percent * 9 };
  }

  /** Handle one command frame and answer it. */
  _command(frame) {
    const cmd = decodeCommand(frame);
    if (!cmd) return;                       // not a command: a real band stays silent too
    let result;
    if (this.firmware === '1.0' && V11_COMMANDS.includes(cmd.opcode)) result = 'UNKNOWN_OPCODE';
    else if (cmd.bad_length) result = 'BAD_LENGTH';
    else if (typeof cmd.opcode !== 'string') result = 'UNKNOWN_OPCODE';
    else result = COMMANDS[cmd.opcode](this, cmd.args);
    this._saveStore();                      // before the answer: what the band said it stored is on the disk
    if (typeof result === 'string') this._send(encodeResponse(cmd.seq, cmd.opcode, result));
    else this._send(encodeResponse(cmd.seq, cmd.opcode, 'OK', result));
    for (const job of this._later.splice(0)) job();
    this._ledChanged();
  }

  _startStreams(mask) {
    this._s.streams |= mask;
    for (const name of STREAM_JOBS) {
      if (!(mask & bit(name))) continue;
      if (name === 'audio') this._audioSeq = 0;                           // seq restarts on every start
      const firstIn = this._firstIn(name);
      if (firstIn > 0) this._due.set(name, this._t + firstIn);        // 0: battery with no interval has no timer
    }
    if (mask & bit('battery')) this._later.push(() => this._stream('battery', this._battery()));   // once at start
  }

  /** How long after its start (or a change of its setting) a stream's first frame leaves, in ms. */
  _firstIn(name) {
    if (name === 'motion') return 10000 / this._s.imu.rate_hz;
    if (name === 'motion2') {
      const { rate_hz, sensors } = this._s.motion2;
      this._motion2 = { from: this._t, taken: 0 };                      // sample numbers start at 0
      return motion2Count(rate_hz, sensors) * motion2PeriodUs(rate_hz) / 1000;
    }
    return this._s.intervals[STREAM_BITS[name]] ?? JOBS_FIRST[name];
  }

  _stopStreams(mask) {
    this._s.streams &= ~mask;
    this._elsewhere &= ~mask;
    for (const name of STREAM_JOBS) if (mask & bit(name)) this._due.delete(name);
  }

  _closeSoon(reason) {
    this._closeReason = reason;
    this._due.set('shutdown', this._t + 200);
    return {};
  }

  _shutdown(reason) {
    if (!this._open) return;
    this._open = false;
    clearInterval(this._timer);
    this._downSince = this.realtime ? Date.now() : null;
    this._s = defaults();                   // settings go back to defaults; number, storage and clock stay
    this._elsewhere = 0;
    if (reason === 'restart') {
      // The band starts again: its clock is lost, and its counter (t_ms, uptime_ms) begins at 0.
      // Storage, the band number and the step count stay.
      this._clockZero = null;
      this._restartCounter();
    }
    this._due.clear();
    this._later = [];
    this._ledChanged();
    this.onClose(reason);
  }

  _ledChanged() {
    const led = this.led;
    const text = JSON.stringify(led);
    if (text === this._lastLed) return;
    this._lastLed = text;
    try {
      this.onLed(led);
    } catch (e) {
      console.error('Error in your onLed function:', e);
    }
  }

  _stream(name, value) {
    if (this._elsewhere & bit(name)) return;        // this stream goes to the program on the other link
    this._send(encodeStream(name, value));
  }

  /** Send one frame to the computer. */
  _send(frame) {
    const deaf = this._deaf;
    if (deaf) {                             // stopReading(): the computer takes nothing
      if (deaf.ended) return;
      if (deaf.from === null) {
        deaf.from = this._t;                // from now on the band has data waiting
        this._due.set('giveUp', this._t + USB_GIVE_UP_MS);
      }
      const keep = this._t - deaf.from < USB_PAUSE_MS || frame[0] === RESPONSE_FRAME;
      if (keep && deaf.kept.length < USB_KEPT_MOST) deaf.kept.push(frame);
      else deaf.dropped = true;
      return;
    }
    if (this._newSession) {                 // the first frame of a session has one 00 in front of it
      this._newSession = false;
      this._emptyPacket();
    }
    if (this.via === 'usb') this._wire('to-computer', frameForUsb(frame));
    else this.onFrame(frame);
  }

  /** The pretend USB cable: bytes go in at one end and come out, a few at a time, at the other. */
  _wire(direction, bytes) {
    this.onWire(direction, bytes);
    for (let i = 0; i < bytes.length; i += USB_CHUNK) {
      const chunk = bytes.subarray(i, i + USB_CHUNK);
      if (direction === 'to-band') for (const frame of this._bandSide.push(chunk)) this._command(frame);
      else for (const frame of this._computerSide.push(chunk)) this.onFrame(frame);
    }
  }
}

// How long after STREAM_START the first frame of each fixed-rate stream leaves (ms).
const JOBS_FIRST = { activity: 1000, audio: 20, link: 1000, mic_level: 100 };
