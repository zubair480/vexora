// Codec: the ONLY module that knows which bytes travel between a band and a computer.
// It follows contract/contract.md and is checked against contract/test-vectors.json.
//
// Everything is driven by small tables: one line per command, one line per stream.
// A line describes its fields as "name:type name:type ...". Field names are the
// contract's names. The types are:
//
//   u8 u16 u32 i8 i16 i32   whole numbers, little-endian
//   u64                      a whole number in 8 bytes (a time in milliseconds), little-endian
//   bytes                   all the remaining bytes (Uint8Array)
//   ascii                   all the remaining bytes as text
//   bytes8                  one length byte, then that many bytes (Uint8Array)
//   samples                 one count byte, then that many motion samples
//   samples2                one count byte, then that many fast-motion samples; which values
//                           a sample holds is said by the `sensors` field before it

export const CONTRACT_REVISION = '2026-11-12';

// Band names. This is the only place in the JavaScript code that knows the rule:
// NAME_PREFIX + the band number, written with at least NAME_MIN_DIGITS digits.
// Band 7 is "Vexo-HACK-07", band 42 is "Vexo-HACK-42", band 123 is "Vexo-HACK-123".
export const NAME_PREFIX = 'Vexo-HACK-';
const NAME_MIN_DIGITS = 2;
const NUMBER_MAX = 999;         // 0 = number not set yet

// Bluetooth identifiers. The computer writes to RX; the band notifies on TX.
export const SERVICE_UUID = '254f0001-df82-4a9c-a2b5-563a0f6b2021';
export const RX_UUID = '254f0002-df82-4a9c-a2b5-563a0f6b2021';
export const TX_UUID = '254f0003-df82-4a9c-a2b5-563a0f6b2021';

export const MAX_FRAME = 244;   // largest frame in bytes
export const MAGIC = 0x5856;    // safety number for SET_BAND_NUMBER and STORE_WIPE

const FRAME_COMMAND = 0x01;
const FRAME_RESPONSE = 0x02;
const FRAME_NOTICE = 0x03;
const FRAME_STREAM = 0x10;      // stream bit n uses frame type 0x10 + n

/** Status names; the position in the list is the status code. */
export const STATUSES = Object.freeze([
  'OK', 'UNKNOWN_OPCODE', 'BAD_LENGTH', 'BAD_ARG', 'BUSY',
  'NOT_AVAILABLE', 'DENIED', 'NOT_FOUND', 'NO_SPACE', 'IO_ERROR',
]);

/** Notice code -> name. */
export const NOTICE_KINDS = Object.freeze({ 1: 'LOW_BATTERY', 2: 'CRITICAL_BATTERY', 3: 'STREAM_DROPPED', 4: 'USB_POWER' });

/** Motion event code -> name. */
export const EVENT_KINDS = Object.freeze({
  1: 'tap', 2: 'double-tap', 3: 'free-fall', 4: 'tilt', 5: 'orientation', 6: 'motion-start', 7: 'motion-stop',
});

/** LED pattern names; the position in the list is the pattern code. */
export const LED_PATTERNS = Object.freeze(['off', 'solid', 'blink', 'breathe', 'flash', 'rainbow']);

/** Capability names; the position in the list is the bit in GET_INFO `caps`. */
export const CAPABILITIES = Object.freeze([
  'accel', 'gyro', 'events', 'steps', 'mic', 'led', 'battery', 'temperature', 'storage', 'tx-power', 'usb',
]);

// ------------------------------------------------------------------ the tables

const IMU = 'rate_hz:u8 accel_range_g:u8 gyro_range_dps:u16';
const EVENT = 'event_mask:u8 tap_threshold_mg:u16';
const LED = 'r:u8 g:u8 b:u8 brightness:u8 pattern:u8 period_ms:u16';
const BATTERY = 'percent:u8 state:u8 millivolts:u16';
const ACTIVE = 'active:u16';
const CLOCK = 'set:u8 unix_ms:u64 t_ms:u32';
const INTERVAL = 'stream:u8 interval_ms:u32 min_ms:u32 max_ms:u32';
const MOTION2 = 'rate_hz:u16 sensors:u8 max_accel_hz:u16 max_gyro_hz:u16 max_both_hz:u16';

// [opcode, name, arguments, response data]
// TIME_*, STREAM_*_INTERVAL and MOTION2_* came with contract v1.1: a band with firmware
// 1.0 answers them UNKNOWN_OPCODE.
const COMMAND_TABLE = [
  [0x01, 'GET_INFO', '', 'proto_major:u8 proto_minor:u8 fw_major:u8 fw_minor:u8 fw_patch:u8 hw:u8 band_no:u16 caps:u16 max_frame:u16 uptime_ms:u32'],
  [0x02, 'GET_NAME', '', 'name:ascii'],
  [0x03, 'SET_BAND_NUMBER', 'number:u16 magic:u16', 'number:u16'],
  [0x04, 'IDENTIFY', 'seconds:u8', ''],
  [0x05, 'RESTART', '', ''],
  [0x06, 'SLEEP', '', ''],
  [0x07, 'TIME_SET', 'unix_ms:u64', CLOCK],
  [0x08, 'TIME_GET', '', CLOCK],
  [0x10, 'STREAM_START', 'mask:u16', ACTIVE],
  [0x11, 'STREAM_STOP', 'mask:u16', ACTIVE],
  [0x12, 'STREAM_GET', '', ACTIVE],
  [0x13, 'STREAM_SET_INTERVAL', 'stream:u8 interval_ms:u32', INTERVAL],
  [0x14, 'STREAM_GET_INTERVAL', 'stream:u8', INTERVAL],
  [0x20, 'IMU_SET', IMU, IMU],
  [0x21, 'IMU_GET', '', IMU],
  [0x22, 'EVENT_SET', EVENT, EVENT],
  [0x23, 'EVENT_GET', '', EVENT],
  [0x24, 'MOTION2_SET', 'rate_hz:u16 sensors:u8', MOTION2],
  [0x25, 'MOTION2_GET', '', MOTION2],
  [0x30, 'MIC_SET_GAIN', 'gain_half_db:u8', 'gain_half_db:u8'],
  [0x31, 'MIC_GET', '', 'gain_half_db:u8 sample_rate:u16 frame_ms:u8 channels:u8 bitrate:u16'],
  [0x40, 'RADIO_SET_TX_POWER', 'dbm:i8', 'dbm:i8'],
  [0x41, 'RADIO_GET', '', 'tx_dbm:i8 rssi_dbm:i8 phy:u8 interval:u16 mtu:u16'],
  [0x50, 'LED_SET', LED, ''],
  [0x51, 'LED_GET', '', LED],
  [0x60, 'TEMP_SET_INTERVAL', 'seconds:u8', 'seconds:u8'],
  [0x61, 'BATTERY_GET', '', BATTERY],
  [0x70, 'STORE_INFO', '', 'keys_max:u16 value_max:u16 free_bytes:u32 used_keys:u16'],
  [0x71, 'STORE_PUT', 'key:u16 value:bytes', ''],
  [0x72, 'STORE_GET', 'key:u16', 'value:bytes'],
  [0x73, 'STORE_DEL', 'key:u16', ''],
  [0x74, 'STORE_WIPE', 'magic:u16', ''],
];

// [name, body]; the position in the list is the stream bit.
const STREAM_TABLE = [
  ['motion', 't_ms:u32 period_ms:u8 samples:samples'],
  ['event', 't_ms:u32 event:u8 arg:u8'],
  ['activity', 't_ms:u32 steps_total:u32 cadence_spm:u8 state:u8 motion_mg:u16'],
  ['audio', 'seq:u16 opus:bytes8'],
  ['battery', BATTERY],
  ['temp', 't_ms:u32 temp_mc:i32'],
  ['link', 't_ms:u32 rssi_dbm:i8 tx_dbm:i8 phy:u8'],
  ['mic_level', 't_ms:u32 peak_db:u8 rms_db:u8'],
  ['motion2', 't_ms:u32 sample_no:u32 period_us:u32 sensors:u8 samples:samples2'],    // v1.1
];

const NOTICE_FIELDS = fields('code:u8 arg:u8');
const SAMPLE_FIELDS = fields('ax:i16 ay:i16 az:i16 gx:i16 gy:i16 gz:i16');
const SAMPLE_SIZE = 12;
// A fast-motion sample holds only the sensors that were asked for. The key is `sensors`.
const SAMPLE2_FIELDS = {
  1: fields('ax:i16 ay:i16 az:i16'),
  2: fields('gx:i16 gy:i16 gz:i16'),
  3: SAMPLE_FIELDS,
};
const U32_SPAN = 0x100000000;

/** Command name -> opcode number. */
export const OPCODES = Object.freeze(Object.fromEntries(COMMAND_TABLE.map(([code, name]) => [name, code])));

/** Stream name -> bit number in the STREAM_START / STREAM_STOP mask. */
export const STREAM_BITS = Object.freeze(Object.fromEntries(STREAM_TABLE.map(([name], bit) => [name, bit])));

// How each whole-number type is stored.
const INTS = {
  u8: { size: 1, min: 0, max: 0xff, read: 'getUint8' },
  u16: { size: 2, min: 0, max: 0xffff, read: 'getUint16' },
  u32: { size: 4, min: 0, max: 0xffffffff, read: 'getUint32' },
  i8: { size: 1, min: -0x80, max: 0x7f, read: 'getInt8' },
  i16: { size: 2, min: -0x8000, max: 0x7fff, read: 'getInt16' },
  i32: { size: 4, min: -0x80000000, max: 0x7fffffff, read: 'getInt32' },
};

// Response data that has no fixed size: the fewest and the most bytes the contract allows.
// GET_NAME is "Vexo-HACK-" and 2 or 3 digits; a stored value is 1..232 bytes.
const DATA_RANGE = { GET_NAME: [12, 13], STORE_GET: [1, 232] };

const COMMANDS = new Map();     // looked up by name and by opcode number
for (const [code, name, args, data] of COMMAND_TABLE) {
  const entry = { code, name, args: fields(args), data: fields(data) };
  // How many data bytes an OK response may have: [fewest, most]. Fixed layouts: one size.
  const fixed = entry.data.reduce((sum, [, type]) => sum + (type === 'u64' ? 8 : INTS[type]?.size ?? 0), 0);
  entry.size = DATA_RANGE[name] ?? [fixed, fixed];
  COMMANDS.set(name, entry);
  COMMANDS.set(code, entry);
}
const STREAMS = STREAM_TABLE.map(([name, body]) => ({ name, body: fields(body) }));

// ------------------------------------------------------------------ packing fields

/** "a:u8 b:u16" -> [['a', 'u8'], ['b', 'u16']] */
function fields(text) {
  return text ? text.split(' ').map((part) => part.split(':')) : [];
}

/** A plain Uint8Array copy of part of `bytes`. */
function copy(bytes, start, end) {
  return new Uint8Array(bytes.subarray(start, end));
}

function toBytes(value, what) {
  if (value instanceof Uint8Array) return value;
  if (value instanceof ArrayBuffer || Array.isArray(value)) return new Uint8Array(value);
  throw new RangeError(`${what} must be bytes (a Uint8Array)`);
}

/** Append the fields of `values` to the byte list `out`. Throws RangeError on a bad value. */
function pack(out, layout, values, what) {
  for (const key of Object.keys(values)) {
    if (!layout.some(([name]) => name === key)) {
      const known = layout.map(([name]) => name).join(', ') || 'none';
      throw new RangeError(`${what} has no field "${key}" (fields: ${known})`);
    }
  }
  for (const [name, type] of layout) {
    const value = values[name];
    const label = `${what} field "${name}"`;
    if (value === undefined || value === null) throw new RangeError(`${label} is missing`);
    if (type === 'bytes') {
      out.push(...toBytes(value, label));
    } else if (type === 'ascii') {
      for (const ch of String(value)) out.push(ch.charCodeAt(0) & 0x7f);
    } else if (type === 'bytes8') {
      const bytes = toBytes(value, label);
      if (bytes.length > 255) throw new RangeError(`${label} is longer than 255 bytes`);
      out.push(bytes.length, ...bytes);
    } else if (type === 'samples') {
      if (!Array.isArray(value) || value.length > 255) throw new RangeError(`${label} must be a list of at most 255 samples`);
      out.push(value.length);
      for (const sample of value) pack(out, SAMPLE_FIELDS, sample, 'motion sample');
    } else if (type === 'samples2') {
      const layout2 = SAMPLE2_FIELDS[values.sensors];
      if (!layout2) throw new RangeError(`${what} field "sensors" must be 1, 2 or 3, got ${values.sensors}`);
      if (!Array.isArray(value) || value.length > 255) throw new RangeError(`${label} must be a list of at most 255 samples`);
      out.push(value.length);
      for (const sample of value) pack(out, layout2, sample, 'motion sample');
    } else if (type === 'u64') {
      // Kept as a plain number: times in milliseconds are far below 2^53, where numbers stay exact.
      if (!Number.isSafeInteger(value) || value < 0) {
        throw new RangeError(`${label} must be a whole number from 0 to ${Number.MAX_SAFE_INTEGER}, got ${value}`);
      }
      const high = Math.floor(value / U32_SPAN);
      const low = value - high * U32_SPAN;
      for (let i = 0; i < 4; i++) out.push((low >>> (8 * i)) & 0xff);
      for (let i = 0; i < 4; i++) out.push((high >>> (8 * i)) & 0xff);
    } else {
      const int = INTS[type];
      if (!Number.isInteger(value) || value < int.min || value > int.max) {
        throw new RangeError(`${label} must be a whole number from ${int.min} to ${int.max}, got ${value}`);
      }
      for (let i = 0; i < int.size; i++) out.push((value >>> (8 * i)) & 0xff);
    }
  }
  return out;
}

/** Read fields starting at `start`. Returns [values, end], or null if bytes are missing. */
function unpack(layout, bytes, start) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const values = {};
  let at = start;
  for (const [name, type] of layout) {
    if (type === 'bytes') {
      values[name] = copy(bytes, at);
      at = bytes.length;
    } else if (type === 'ascii') {
      values[name] = String.fromCharCode(...bytes.subarray(at));
      at = bytes.length;
    } else if (type === 'bytes8') {
      const end = at + 1 + bytes[at];
      if (at >= bytes.length || end > bytes.length) return null;
      values[name] = copy(bytes, at + 1, end);
      at = end;
    } else if (type === 'samples') {
      const count = bytes[at];
      if (at >= bytes.length || at + 1 + count * SAMPLE_SIZE > bytes.length) return null;
      at += 1;
      values[name] = [];
      for (let i = 0; i < count; i++, at += SAMPLE_SIZE) values[name].push(unpack(SAMPLE_FIELDS, bytes, at)[0]);
    } else if (type === 'samples2') {
      const layout2 = SAMPLE2_FIELDS[values.sensors];
      if (!layout2 || at >= bytes.length) return null;      // unknown sensors: the sample size is unknown
      const count = bytes[at];
      const size = layout2.length * 2;
      if (at + 1 + count * size > bytes.length) return null;
      at += 1;
      values[name] = [];
      for (let i = 0; i < count; i++, at += size) values[name].push(unpack(layout2, bytes, at)[0]);
    } else if (type === 'u64') {
      if (at + 8 > bytes.length) return null;
      values[name] = view.getUint32(at, true) + view.getUint32(at + 4, true) * U32_SPAN;
      at += 8;
    } else {
      const int = INTS[type];
      if (at + int.size > bytes.length) return null;
      values[name] = view[int.read](at, true);
      at += int.size;
    }
  }
  return [values, at];
}

function finish(out, what) {
  if (out.length > MAX_FRAME) throw new RangeError(`${what} is ${out.length} bytes; a frame can be at most ${MAX_FRAME}`);
  return Uint8Array.from(out);
}

function byte(value, what) {
  if (!Number.isInteger(value) || value < 0 || value > 255) throw new RangeError(`${what} must be 0..255, got ${value}`);
  return value;
}

/** A name from a list/table -> its number. Numbers pass through. */
function codeOf(table, value, what) {
  if (typeof value === 'number') return byte(value, what);
  const found = Object.keys(table).find((code) => table[code] === value);
  if (found === undefined) throw new RangeError(`Unknown ${what} "${value}"`);
  return Number(found);
}

// ------------------------------------------------------------------ names

/** 7 -> "07", 42 -> "42", 123 -> "123": the number as it is printed on the band and its bag. */
export function bandId(number) {
  if (!Number.isInteger(number) || number < 0 || number > NUMBER_MAX) {
    throw new RangeError(`A band number is a whole number from 0 to ${NUMBER_MAX}, got "${number}"`);
  }
  return String(number).padStart(NAME_MIN_DIGITS, '0');
}

/** 42 -> "Vexo-HACK-42", the name the band advertises. */
export function bandName(number) {
  return NAME_PREFIX + bandId(number);
}

/** "7", "07" or "007" -> 7. Anything that is not one to three digits -> null. */
export function parseBandNumber(text) {
  if (typeof text !== 'string' || !/^\d{1,3}$/.test(text.trim())) return null;
  return Number(text.trim());
}

/** "Vexo-HACK-07" -> 7. Anything that is not a band name -> null. */
export function bandNumberOf(name) {
  if (typeof name !== 'string' || !name.startsWith(NAME_PREFIX)) return null;
  const digits = name.slice(NAME_PREFIX.length);
  return new RegExp(`^\\d{${NAME_MIN_DIGITS},3}$`).test(digits) ? Number(digits) : null;
}

// ------------------------------------------------------------------ computer side

/**
 * Build a command frame.
 *   encodeCommand(7, 'LED_SET', { r: 0, g: 255, b: 0, brightness: 128, pattern: 1, period_ms: 1000 })
 * Throws RangeError for an unknown command name or a missing / unknown / out-of-range field.
 */
export function encodeCommand(seq, name, args = {}) {
  const command = typeof name === 'string' ? COMMANDS.get(name) : undefined;
  if (!command) throw new RangeError(`Unknown command "${name}". The names are in contract/contract.md, e.g. LED_SET.`);
  // The two dangerous commands carry a fixed safety number; fill it in.
  if (command.args.some(([field]) => field === 'magic') && args.magic === undefined) args = { ...args, magic: MAGIC };
  const out = [FRAME_COMMAND, byte(seq, 'seq'), command.code];
  return finish(pack(out, command.args, args, name), name);
}

/**
 * Turn one frame from the band into an object:
 *   { frame: 'response', seq, opcode, status, data }
 *   { frame: 'notice', code, arg }
 *   { frame: 'motion' | 'event' | 'activity' | 'audio' | 'battery' | 'temp' | 'link' | 'mic_level' | 'motion2', ...fields }
 *   { frame: 'unknown', type }
 * Returns null for bytes that are not a valid frame. Never throws.
 * An OK response whose data does not have the length the contract gives for its command
 * (too short or too long) is not a valid frame: over USB the start of a response can arrive
 * cut off and still look like a frame (contract 2.2). A response with another status carries
 * no data: one that does is not a valid frame either. wrongLengthResponse() tells which.
 * A response to a command this library has no table line for is passed on as it is; no
 * command of the library can be waiting for it.
 */
export function decodeFrame(bytes) {
  if (!(bytes instanceof Uint8Array) || bytes.length < 1 || bytes.length > MAX_FRAME) return null;
  const type = bytes[0];
  if (type === FRAME_COMMAND) return null;      // bands never send commands

  if (type === FRAME_RESPONSE) {
    if (bytes.length < 4) return null;
    const command = COMMANDS.get(bytes[2]);
    const status = STATUSES[bytes[3]] ?? bytes[3];
    let data = {};
    if (command && wrongLengthResponse(bytes)) return null;
    if (status === 'OK' && command) {
      const got = unpack(command.data, bytes, 4);
      if (!got) return null;
      data = got[0];
    }
    return { frame: 'response', seq: bytes[1], opcode: command ? command.name : bytes[2], status, data };
  }

  if (type === FRAME_NOTICE) {
    const got = unpack(NOTICE_FIELDS, bytes, 1);
    if (!got) return null;
    return { frame: 'notice', code: NOTICE_KINDS[got[0].code] ?? got[0].code, arg: got[0].arg };
  }

  const stream = STREAMS[type - FRAME_STREAM];
  if (stream) {
    const got = unpack(stream.body, bytes, 1);
    return got ? { frame: stream.name, ...got[0] } : null;
  }
  return { frame: 'unknown', type };
}

/**
 * For a frame decodeFrame() turned down: was it a response to a known command with the
 * wrong number of data bytes? Then { seq, opcode, got, expected } (opcode is the name, got
 * the data bytes that came, expected a text such as "16" or "1 to 232"); otherwise null.
 * Status OK: the length the contract gives for the command. Any other status: no data.
 */
export function wrongLengthResponse(bytes) {
  if (!(bytes instanceof Uint8Array) || bytes.length < 4 || bytes.length > MAX_FRAME) return null;
  if (bytes[0] !== FRAME_RESPONSE) return null;
  const command = COMMANDS.get(bytes[2]);
  if (!command) return null;
  const got = bytes.length - 4;
  const [fewest, most] = bytes[3] === 0 ? command.size : [0, 0];
  if (got >= fewest && got <= most) return null;
  return { seq: bytes[1], opcode: command.name, got, expected: fewest === most ? String(fewest) : `${fewest} to ${most}` };
}

// ------------------------------------------------------------------ band side (used by the simulator)

/**
 * Read a command frame the way a band does. Returns
 *   { seq, opcode, args }              opcode is the name, or the number if unknown
 *   { seq, opcode, bad_length: true }  the argument bytes do not fit the table
 * or null if the bytes are not a command frame at all.
 */
export function decodeCommand(bytes) {
  if (!(bytes instanceof Uint8Array) || bytes.length < 3 || bytes.length > MAX_FRAME || bytes[0] !== FRAME_COMMAND) return null;
  const seq = bytes[1];
  const command = COMMANDS.get(bytes[2]);
  if (!command) return { seq, opcode: bytes[2], args: {} };
  const got = unpack(command.args, bytes, 3);
  if (!got || got[1] !== bytes.length) return { seq, opcode: command.name, bad_length: true };
  return { seq, opcode: command.name, args: got[0] };
}

/** Build a response frame. `opcode` and `status` may be names or numbers. Data is only sent with OK. */
export function encodeResponse(seq, opcode, status, data = {}) {
  const command = COMMANDS.get(opcode);
  const out = [FRAME_RESPONSE, byte(seq, 'seq'), command ? command.code : byte(opcode, 'opcode'), codeOf(STATUSES, status, 'status')];
  if (out[3] === 0 && command) pack(out, command.data, data, `${command.name} response`);
  return finish(out, 'response');
}

/** Build a stream frame: encodeStream('battery', { percent: 87, state: 0, millivolts: 3987 }). */
export function encodeStream(name, value) {
  const bit = STREAM_BITS[name];
  if (bit === undefined) throw new RangeError(`Unknown stream "${name}"`);
  const { frame, ...body } = value;     // allow passing a decoded frame straight back in
  return finish(pack([FRAME_STREAM + bit], STREAMS[bit].body, body, name), name);
}

/** Build a notice frame. `code` may be a name like 'LOW_BATTERY' or a number. */
export function encodeNotice(code, arg) {
  return Uint8Array.of(FRAME_NOTICE, codeOf(NOTICE_KINDS, code, 'notice'), byte(arg, 'arg'));
}

// ------------------------------------------------------------------ USB framing (COBS)
//
// On the USB cable there are no packets, only a flow of bytes. So each frame is rewritten
// to contain no 00 byte (that is COBS) and a 00 is sent after it to mark the end.

/** COBS-encode a frame. The result has no 00 in it; the closing 00 is not added here. */
export function cobsEncode(bytes) {
  const out = [0];
  let codeAt = 0;     // where the length of the current run goes
  let code = 1;
  for (let i = 0; i < bytes.length; i++) {
    if (bytes[i] !== 0) {
      out.push(bytes[i]);
      code++;
    }
    if (bytes[i] === 0 || code === 0xff) {
      out[codeAt] = code;
      if (code === 0xff && i === bytes.length - 1) return Uint8Array.from(out);
      codeAt = out.length;
      out.push(0);
      code = 1;
    }
  }
  out[codeAt] = code;
  return Uint8Array.from(out);
}

/** Undo cobsEncode. Returns null if the bytes are not valid COBS. */
export function cobsDecode(bytes) {
  if (bytes.length === 0) return null;
  const out = [];
  let i = 0;
  while (i < bytes.length) {
    const code = bytes[i];
    if (code === 0 || i + code > bytes.length) return null;
    for (let j = i + 1; j < i + code; j++) {
      if (bytes[j] === 0) return null;
      out.push(bytes[j]);
    }
    i += code;
    if (code < 0xff && i < bytes.length) out.push(0);
  }
  return Uint8Array.from(out);
}

/** A frame as it travels on the USB cable: COBS, then one 00. */
export function frameForUsb(frame) {
  const encoded = cobsEncode(frame);
  const out = new Uint8Array(encoded.length + 1);
  out.set(encoded);
  return out;
}

const MAX_USB_PACKET = MAX_FRAME + 2;

/**
 * Cuts a flow of USB bytes back into frames. Feed it chunks of any size:
 *   for (const frame of deframer.push(chunk)) ...
 * Rules (contract 2.2): bytes before the first 00 are dropped, because we may have joined
 * in the middle of a frame; empty packets are skipped; packets that are not valid COBS
 * are dropped without a word; an unfinished packet waits for the next chunk.
 * Pass { skipFirst: false } when you know the flow starts at the start of a frame.
 */
export class Deframer {
  constructor({ skipFirst = true } = {}) {
    this._inSync = !skipFirst;
    this._packet = [];
  }

  push(chunk) {
    const frames = [];
    for (const b of chunk) {
      if (b !== 0) {
        if (this._inSync) this._packet.push(b);
        // Far too long to be a frame: throw it away up to the next 00.
        if (this._packet.length > MAX_USB_PACKET) {
          this._packet = [];
          this._inSync = false;
        }
        continue;
      }
      if (this._inSync && this._packet.length > 0) {
        const frame = cobsDecode(this._packet);
        if (frame && frame.length >= 1 && frame.length <= MAX_FRAME) frames.push(frame);
      }
      this._packet = [];
      this._inSync = true;
    }
    return frames;
  }
}
