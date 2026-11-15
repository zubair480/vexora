"""The byte format of the Vexo Open band: turns commands into bytes and bytes into objects.

This is the only file that knows the format in contract/contract.md. Everything is
little-endian. A frame is [type][body]; the tables below say what each body holds.
Field names are the contract's names, so you can read this file next to the contract.
"""
import struct

CONTRACT_REVISION = "2026-11-12"

# Band names. This is the only place in the Python code that knows the rule:
# NAME_PREFIX + the band number, written with at least NAME_MIN_DIGITS digits.
# Band 7 is "Vexo-HACK-07", band 42 is "Vexo-HACK-42", band 123 is "Vexo-HACK-123".
NAME_PREFIX = "Vexo-HACK-"
NAME_MIN_DIGITS = 2
NUMBER_MAX = 999  # 0 = number not set yet
SERVICE_UUID = "254f0001-df82-4a9c-a2b5-563a0f6b2021"
RX_UUID = "254f0002-df82-4a9c-a2b5-563a0f6b2021"  # we write commands here
TX_UUID = "254f0003-df82-4a9c-a2b5-563a0f6b2021"  # the band notifies here

MAGIC = 0x5856   # safety number that SET_BAND_NUMBER and STORE_WIPE must carry
MAX_FRAME = 244  # largest frame in bytes

TYPE_COMMAND, TYPE_RESPONSE, TYPE_NOTICE, TYPE_STREAM = 0x01, 0x02, 0x03, 0x10

STATUSES = ["OK", "UNKNOWN_OPCODE", "BAD_LENGTH", "BAD_ARG", "BUSY", "NOT_AVAILABLE",
            "DENIED", "NOT_FOUND", "NO_SPACE", "IO_ERROR"]  # index = status code
NOTICE_KINDS = {1: "LOW_BATTERY", 2: "CRITICAL_BATTERY", 3: "STREAM_DROPPED", 4: "USB_POWER"}
EVENT_KINDS = {1: "tap", 2: "double-tap", 3: "free-fall", 4: "tilt", 5: "orientation",
               6: "motion-start", 7: "motion-stop"}
LED_PATTERNS = ["off", "solid", "blink", "breathe", "flash", "rainbow"]  # index = code
CAPABILITIES = ["accel", "gyro", "events", "steps", "mic", "led", "battery", "temperature",
                "storage", "tx-power", "usb"]  # index = bit in GET_INFO caps

# Field layouts are written "name:code name:code ...". The codes are the ones Python's
# struct module uses: B = u8, b = i8, H = u16, h = i16, I = u32, i = i32, Q = u64.
# Two extra codes take all the bytes that are left: "*" = raw bytes, "a" = ASCII text.
_BATTERY = "percent:B state:B millivolts:H"
_IMU = "rate_hz:B accel_range_g:B gyro_range_dps:H"
_EVENT = "event_mask:B tap_threshold_mg:H"
_LED = "r:B g:B b:B brightness:B pattern:B period_ms:H"
_TIME = "set:B unix_ms:Q t_ms:I"
_INTERVAL = "stream:B interval_ms:I min_ms:I max_ms:I"
_MOTION2 = "rate_hz:H sensors:B max_accel_hz:H max_gyro_hz:H max_both_hz:H"

# name: (opcode, arguments we send, data the band answers with)
COMMANDS = {
    "GET_INFO": (0x01, "", "proto_major:B proto_minor:B fw_major:B fw_minor:B fw_patch:B "
                           "hw:B band_no:H caps:H max_frame:H uptime_ms:I"),
    "GET_NAME": (0x02, "", "name:a"),
    "SET_BAND_NUMBER": (0x03, "number:H magic:H", "number:H"),
    "IDENTIFY": (0x04, "seconds:B", ""),
    "RESTART": (0x05, "", ""),
    "SLEEP": (0x06, "", ""),
    "TIME_SET": (0x07, "unix_ms:Q", _TIME),  # v1.1 (a v1.0 band answers UNKNOWN_OPCODE)
    "TIME_GET": (0x08, "", _TIME),           # v1.1
    "STREAM_START": (0x10, "mask:H", "active:H"),
    "STREAM_STOP": (0x11, "mask:H", "active:H"),
    "STREAM_GET": (0x12, "", "active:H"),
    "STREAM_SET_INTERVAL": (0x13, "stream:B interval_ms:I", _INTERVAL),  # v1.1
    "STREAM_GET_INTERVAL": (0x14, "stream:B", _INTERVAL),                # v1.1
    "IMU_SET": (0x20, _IMU, _IMU),
    "IMU_GET": (0x21, "", _IMU),
    "EVENT_SET": (0x22, _EVENT, _EVENT),
    "EVENT_GET": (0x23, "", _EVENT),
    "MOTION2_SET": (0x24, "rate_hz:H sensors:B", _MOTION2),  # v1.1
    "MOTION2_GET": (0x25, "", _MOTION2),                     # v1.1
    "MIC_SET_GAIN": (0x30, "gain_half_db:B", "gain_half_db:B"),
    "MIC_GET": (0x31, "", "gain_half_db:B sample_rate:H frame_ms:B channels:B bitrate:H"),
    "RADIO_SET_TX_POWER": (0x40, "dbm:b", "dbm:b"),
    "RADIO_GET": (0x41, "", "tx_dbm:b rssi_dbm:b phy:B interval:H mtu:H"),
    "LED_SET": (0x50, _LED, ""),
    "LED_GET": (0x51, "", _LED),
    "TEMP_SET_INTERVAL": (0x60, "seconds:B", "seconds:B"),
    "BATTERY_GET": (0x61, "", _BATTERY),
    "STORE_INFO": (0x70, "", "keys_max:H value_max:H free_bytes:I used_keys:H"),
    "STORE_PUT": (0x71, "key:H value:*", ""),
    "STORE_GET": (0x72, "key:H", "value:*"),
    "STORE_DEL": (0x73, "key:H", ""),
    "STORE_WIPE": (0x74, "magic:H", ""),
}
OPCODES = {name: row[0] for name, row in COMMANDS.items()}
# Response data that has no fixed size: the fewest and the most bytes the contract allows.
# GET_NAME is "Vexo-HACK-" and 2 or 3 digits; a stored value is 1..232 bytes.
_DATA_RANGE = {"GET_NAME": (12, 13), "STORE_GET": (1, 232)}
_OPCODE_NAMES = {number: name for name, number in OPCODES.items()}
# The commands contract v1.1 added. A band that reports proto_minor 0 does not know them.
V11_COMMANDS = ("TIME_SET", "TIME_GET", "STREAM_SET_INTERVAL", "STREAM_GET_INTERVAL",
                "MOTION2_SET", "MOTION2_GET")

# Stream name: (bit in the STREAM_START mask, body). The frame type is 0x10 + bit.
# MOTION, MOTION2 and AUDIO have a list or bytes after these fields; see _decode_stream below.
STREAMS = {
    "motion": (0, "t_ms:I period_ms:B count:B"),
    "event": (1, "t_ms:I event:B arg:B"),
    "activity": (2, "t_ms:I steps_total:I cadence_spm:B state:B motion_mg:H"),
    "audio": (3, "seq:H len:B"),
    "battery": (4, _BATTERY),
    "temp": (5, "t_ms:I temp_mc:i"),
    "link": (6, "t_ms:I rssi_dbm:b tx_dbm:b phy:B"),
    "mic_level": (7, "t_ms:I peak_db:B rms_db:B"),
    "motion2": (8, "t_ms:I sample_no:I period_us:I sensors:B count:B"),  # v1.1
}
STREAM_BITS = {name: row[0] for name, row in STREAMS.items()}
_STREAM_NAMES = {bit: name for name, bit in STREAM_BITS.items()}
_SAMPLE = struct.Struct("<6h")  # ax ay az gx gy gz
_SAMPLE_FIELDS = ("ax", "ay", "az", "gx", "gy", "gz")
# MOTION2 "sensors" value -> the fields of one sample (each an i16)
MOTION2_FIELDS = {1: ("ax", "ay", "az"), 2: ("gx", "gy", "gz"), 3: _SAMPLE_FIELDS}
MOTION2_RATES = (10, 25, 50, 100, 208, 416, 833, 1666)


def band_id(number) -> str:
    """band_id(7) -> "07", band_id(123) -> "123": the number as printed on the band and its bag.
    The number must be 0..999 (0 = number not set yet)."""
    try:
        n = int(number)
    except (TypeError, ValueError):
        n = -1
    if isinstance(number, (bool, float)) or not 0 <= n <= NUMBER_MAX:
        raise ValueError("A band number is a whole number from 0 to %d, got %r." % (NUMBER_MAX, number))
    return str(n).zfill(NAME_MIN_DIGITS)


def band_name(number) -> str:
    """band_name(42) -> "Vexo-HACK-42", the name the band advertises."""
    return NAME_PREFIX + band_id(number)


def band_number_of(name):
    """band_number_of("Vexo-HACK-07") -> 7. Anything that is not a band name -> None."""
    if not isinstance(name, str) or not name.startswith(NAME_PREFIX):
        return None
    digits = name[len(NAME_PREFIX):]
    if not (NAME_MIN_DIGITS <= len(digits) <= 3 and digits.isascii() and digits.isdigit()):
        return None
    return int(digits)


# ---- packing and unpacking one field layout ----

def _split(layout):
    """"key:H value:*" -> (["key"], "<H", "value", "*"): fixed names, struct format, rest."""
    fields = [f.split(":") for f in layout.split()]
    rest = fields.pop() if fields and fields[-1][1] in "*a" else (None, None)
    return [f[0] for f in fields], "<" + "".join(f[1] for f in fields), rest[0], rest[1]


def _pack(layout, values, what):
    names, fmt, rest_name, rest_kind = _split(layout)
    known = set(names) | ({rest_name} if rest_name else set())
    extra = sorted(set(values) - known)
    missing = sorted(known - set(values))
    if extra or missing:
        raise ValueError("%s needs exactly these fields: %s." % (what, ", ".join(sorted(known)) or "none"))
    try:
        out = struct.pack(fmt, *[values[n] for n in names])
    except (struct.error, TypeError) as e:
        raise ValueError("%s: a value does not fit its field (%s)." % (what, e))
    if rest_name:
        rest = values[rest_name]
        out += rest.encode("ascii") if rest_kind == "a" else bytes(rest)
    return out


def _unpack(layout, data, exact=False):
    """Return a dict, or None when there are too few bytes (or, with exact, too many)."""
    names, fmt, rest_name, rest_kind = _split(layout)
    size = struct.calcsize(fmt)
    if len(data) < size or (exact and not rest_name and len(data) != size):
        return None
    out = dict(zip(names, struct.unpack_from(fmt, data)))
    if rest_name:
        rest = bytes(data[size:])
        out[rest_name] = rest.decode("ascii", "replace") if rest_kind == "a" else rest
    return out


# ---- COBS: how frames are separated on the USB cable ----
# COBS rewrites a frame so that it contains no 00 byte; a 00 then marks the end of it.

def cobs_encode(data: bytes) -> bytes:
    """Encode without the trailing 00."""
    out = bytearray()
    block = bytearray()  # bytes since the last zero
    owed = True          # does the current block still need writing at the end?
    for byte in bytes(data):
        if byte == 0:
            out.append(len(block) + 1)  # code byte = distance to the next zero
            out += block
            block = bytearray()
            owed = True
        else:
            block.append(byte)
            owed = True
            if len(block) == 254:  # longest block: code 0xFF means "no zero follows"
                out += b"\xff" + block
                block = bytearray()
                owed = False
    if owed:
        out.append(len(block) + 1)
        out += block
    return bytes(out)


def cobs_decode(data: bytes):
    """Decode one packet (without its trailing 00). Returns None if it is not valid COBS."""
    data = bytes(data)
    if not data or 0 in data:
        return None
    out = bytearray()
    i = 0
    while i < len(data):
        code = data[i]
        end = i + code
        if end > len(data):
            return None  # the code byte points past the end
        out += data[i + 1:end]
        i = end
        if code < 0xFF and i < len(data):
            out.append(0)
    return bytes(out)


def frame_for_usb(frame: bytes) -> bytes:
    """What to write to the serial port for one frame."""
    return cobs_encode(frame) + b"\x00"


class Deframer:
    """Feed it serial bytes in chunks of any size; it gives back whole frames.

    A fresh Deframer throws away everything up to the first 00, because the bytes might
    start in the middle of a packet. Pass synced=True when you know they do not.
    """

    def __init__(self, synced: bool = False) -> None:
        self._synced = synced
        self._buffer = bytearray()

    def push(self, chunk: bytes) -> list:
        frames = []
        self._buffer += chunk
        while True:
            end = self._buffer.find(0)
            if end < 0:
                break  # keep the unfinished tail for next time
            packet = bytes(self._buffer[:end])
            del self._buffer[:end + 1]
            if not self._synced:
                self._synced = True
                continue
            frame = cobs_decode(packet) if packet else None
            if frame and len(frame) <= MAX_FRAME:
                frames.append(frame)  # anything else is dropped silently
        if len(self._buffer) > 2 * MAX_FRAME:  # noise with no 00 in it: do not grow forever
            del self._buffer[:]
            self._synced = False
        return frames


# ---- client side: commands out, frames in ----

def encode_command(seq: int, name: str, args: dict = None) -> bytes:
    """encode_command(7, "IDENTIFY", {"seconds": 5}) -> the frame to send. ValueError if wrong."""
    if name not in COMMANDS:
        raise ValueError("Unknown command %r. Commands: %s." % (name, ", ".join(COMMANDS)))
    opcode, layout, _ = COMMANDS[name]
    args = dict(args or {})
    if "magic:H" in layout:
        args.setdefault("magic", MAGIC)
    frame = bytes([TYPE_COMMAND, seq & 0xFF, opcode]) + _pack(layout, args, name)
    if len(frame) > MAX_FRAME:
        raise ValueError("%s: the frame would be %d bytes; the limit is %d." % (name, len(frame), MAX_FRAME))
    return frame


def decode_frame(data):
    """Decode one frame from the band into a dict; None if it is broken. Never raises.
    An OK response whose data is not the length the contract gives for its command is
    broken too, and so is a response with another status that carries data;
    wrong_length_response() tells which.

    {"frame": "response", "seq", "opcode", "status", "data"}, {"frame": "notice", "code", "arg"},
    {"frame": "motion" | "event" | ..., fields}, or {"frame": "unknown", "type"}.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        return None
    data = bytes(data)
    if not data or data[0] == TYPE_COMMAND or len(data) > MAX_FRAME:
        return None
    kind, body = data[0], data[1:]
    if kind == TYPE_RESPONSE:
        return _decode_response(body)
    if kind == TYPE_NOTICE:
        if len(body) < 2:
            return None
        return {"frame": "notice", "code": NOTICE_KINDS.get(body[0], body[0]), "arg": body[1]}
    if (kind - TYPE_STREAM) in _STREAM_NAMES:
        return _decode_stream(_STREAM_NAMES[kind - TYPE_STREAM], body)
    return {"frame": "unknown", "type": kind}


def _data_range(name):
    """How many data bytes an OK response to this command may have: (fewest, most)."""
    if name in _DATA_RANGE:
        return _DATA_RANGE[name]
    size = struct.calcsize(_split(COMMANDS[name][2])[1])
    return size, size


def wrong_length_response(data):
    """For a frame decode_frame() turned down: was it a response to a known command with
    the wrong number of data bytes? Then {"seq", "opcode": name, "got": data bytes that came,
    "expected": a text such as "16" or "1 to 232"}; otherwise None.
    Status OK: the length the contract gives for the command. Any other status: no data."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        return None
    data = bytes(data)
    if not 4 <= len(data) <= MAX_FRAME or data[0] != TYPE_RESPONSE:
        return None
    name = _OPCODE_NAMES.get(data[2])
    if name is None:
        return None
    got = len(data) - 4
    fewest, most = _data_range(name) if data[3] == 0 else (0, 0)
    if fewest <= got <= most:
        return None
    return {"seq": data[1], "opcode": name, "got": got,
            "expected": str(fewest) if fewest == most else "%d to %d" % (fewest, most)}


def _decode_response(body):
    if len(body) < 3:
        return None
    seq, opcode, status = body[0], body[1], body[2]
    name = _OPCODE_NAMES.get(opcode)
    values = {}
    if name and status != 0 and len(body) > 3:
        return None  # a status other than OK carries no data (contract 2.2)
    if name and status == 0:
        # The data must have the length the contract gives for this command, no less and no
        # more: over USB the start of a response can arrive cut off and still look like a
        # frame (contract 2.2). Unknown opcodes are passed on as they are: no command of the
        # library can be waiting for one.
        fewest, most = _data_range(name)
        if not fewest <= len(body) - 3 <= most:
            return None
        values = _unpack(COMMANDS[name][2], body[3:])
        if values is None:
            return None
    return {"frame": "response", "seq": seq, "opcode": name or opcode,
            "status": STATUSES[status] if status < len(STATUSES) else status, "data": values}


def _decode_stream(name, body):
    out = _unpack(STREAMS[name][1], body)
    if out is None:
        return None
    if name == "motion":
        count = out.pop("count")
        if len(body) < 6 + count * _SAMPLE.size:
            return None
        out["samples"] = [dict(zip(_SAMPLE_FIELDS, _SAMPLE.unpack_from(body, 6 + i * _SAMPLE.size)))
                          for i in range(count)]
    if name == "motion2":
        count, fields = out.pop("count"), MOTION2_FIELDS.get(out["sensors"])
        if fields is None:
            return None  # sensors is not 1, 2 or 3: the size of a sample is unknown
        one = struct.Struct("<%dh" % len(fields))
        if len(body) < 14 + count * one.size:
            return None
        out["samples"] = [dict(zip(fields, one.unpack_from(body, 14 + i * one.size))) for i in range(count)]
    if name == "audio":
        length = out.pop("len")
        if len(body) < 3 + length:
            return None
        out["opus"] = body[3:3 + length]
    return dict(frame=name, **out)


# ---- band side: used by the simulator (and by anyone building a pretend band) ----

def decode_command(data):
    """Decode a command frame as the band sees it.

    {"seq", "opcode": name or number, "args": {...}}; "bad_length": True replaces "args"
    when the argument bytes do not match the table. None if this is not a command frame.
    """
    data = bytes(data)
    if len(data) < 3 or data[0] != TYPE_COMMAND:
        return None
    name = _OPCODE_NAMES.get(data[2])
    if name is None:
        return {"seq": data[1], "opcode": data[2], "args": {}}
    args = _unpack(COMMANDS[name][1], data[3:], exact=True)
    if args is None:
        return {"seq": data[1], "opcode": name, "bad_length": True}
    return {"seq": data[1], "opcode": name, "args": args}


def encode_response(seq: int, opcode, status="OK", data: dict = None) -> bytes:
    """opcode and status may be names or numbers. Data is only sent with status OK."""
    name = opcode if opcode in COMMANDS else _OPCODE_NAMES.get(opcode)
    number = OPCODES[name] if name else int(opcode)
    code = STATUSES.index(status) if status in STATUSES else int(status)
    frame = bytes([TYPE_RESPONSE, seq & 0xFF, number, code])
    if name and code == 0:
        frame += _pack(COMMANDS[name][2], data or {}, name + " response")
    return frame


def encode_stream(name: str, value: dict) -> bytes:
    """encode_stream("battery", {"percent": 87, "state": 0, "millivolts": 3987})"""
    bit, layout = STREAMS[name]
    value = {k: v for k, v in value.items() if k != "frame"}
    tail = b""
    if name == "motion":
        samples = value.pop("samples")
        value["count"] = len(samples)
        tail = b"".join(_SAMPLE.pack(*[s[f] for f in _SAMPLE_FIELDS]) for s in samples)
    if name == "motion2":
        samples, fields = value.pop("samples"), MOTION2_FIELDS[value["sensors"]]
        value["count"] = len(samples)
        tail = b"".join(struct.pack("<%dh" % len(fields), *[s[f] for f in fields]) for s in samples)
    if name == "audio":
        tail = bytes(value.pop("opus"))
        value["len"] = len(tail)
    return bytes([TYPE_STREAM + bit]) + _pack(layout, value, name) + tail


def encode_notice(code, arg: int) -> bytes:
    """code may be a name ("LOW_BATTERY") or a number."""
    for number, name in NOTICE_KINDS.items():
        if code == name:
            code = number
    return bytes([TYPE_NOTICE, int(code), arg & 0xFF])
