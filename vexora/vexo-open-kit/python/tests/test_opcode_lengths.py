"""Every command of the contract has a length rule for its response, in the place where
responses are matched (Band), so the rule holds over USB, Bluetooth and the pretend band.
The list of commands and their lengths is read from contract/contract.md itself: a new
command in the contract with no length rule in the library fails here.
The same tests as js/test/opcode-lengths.test.js."""
import asyncio
import json
import os
import re
import time
import unittest

from vexo_open import Band, VexoError, codec
from vexo_open.transport import Transport

CONTRACT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "contract")
with open(os.path.join(CONTRACT, "contract.md"), encoding="utf-8") as f:
    TEXT = f.read()
with open(os.path.join(CONTRACT, "test-vectors.json"), encoding="utf-8") as f:
    VECTORS = json.load(f)
TYPE_SIZES = {"u8": 1, "i8": 1, "u16": 2, "i16": 2, "u32": 4, "i32": 4, "u64": 8}


def run(coro):
    return asyncio.run(coro)


def type_bytes(text):
    return sum(TYPE_SIZES[t] for t in re.findall(r"\b[ui](?:8|16|32|64)\b", text))


def cells(line):
    return [cell.strip() for cell in line.split("|")[1:-1]]


def contract_table():
    """The opcode table of the contract: name -> (opcode, fewest, most data bytes of an OK response)."""
    table = TEXT[TEXT.index("### Opcode table"):TEXT.index("## 4. Streams")]
    battery = cells(next(line for line in TEXT.split("\n") if re.match(r"^\| 4 \| `0x14` \| BATTERY \|", line)))[3]
    name12 = re.search(r"`GET_NAME` has (\d+)\s+or (\d+) bytes", TEXT)
    store = re.search(r"`STORE_GET` (\d+) to (\d+)", TEXT)
    out = {}
    for line in table.split("\n"):
        if not re.match(r"^\| `0x[0-9a-fA-F]{2}` \|", line):
            continue
        code, name, _, data = cells(line)[:4]
        if not re.match(r"^[A-Z][A-Z0-9_]+$", name):
            continue  # a row that is not a command of the contract
        said = re.search(r"\((\d+) bytes\)", data) or re.match(r"the same (\d+) bytes", data)
        counted = type_bytes(data)
        if data == "none":
            size = (0, 0)
        elif said:
            assert not counted or counted == int(said.group(1)), "%s: the fields add up to the bytes the contract says" % name
            size = (int(said.group(1)),) * 2
        elif counted:
            size = (counted, counted)
        elif data.startswith("BATTERY body"):
            size = (type_bytes(battery),) * 2
        elif data.startswith("ASCII name"):
            size = (int(name12.group(1)), int(name12.group(2)))
        elif data == "value bytes":
            size = (int(store.group(1)), int(store.group(2)))
        else:
            raise AssertionError('%s: this test does not know how long "%s" is. Add a length rule for it.' % (name, data))
        out[name] = (int(code.strip("`"), 16),) + size
    return out


TABLE = contract_table()
INFO = dict(proto_major=1, proto_minor=1, fw_major=1, fw_minor=1, fw_patch=3, hw=1, band_no=42, caps=0x7FF,
            max_frame=244, uptime_ms=5000)
CLOCK = dict(set=1, unix_ms=1794470400000, t_ms=5000)
EVENTS = ("motion", "event", "tap", "double-tap", "free-fall", "tilt", "orientation", "motion-start", "motion-stop",
          "activity", "audio", "battery", "temperature", "link", "mic-level", "notice")


def response(seq, opcode, status, data=b""):
    return bytes([0x02, seq % 256, opcode, status]) + bytes(data)


def with_bytes(fields):
    """The vectors write the byte fields `opus` and `value` as hex; code uses bytes."""
    return {k: bytes.fromhex(v) if k in ("opus", "value") and isinstance(v, str) else v for k, v in fields.items()}


class Double(Transport):
    """A band that answers as the test says. kind "ble": each frame arrives as one notification.
    kind "serial": the frames travel as USB bytes (COBS, 00) and come back through the Deframer."""

    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.reply = None
        self.deframer = codec.Deframer(synced=True)

    async def open(self):
        pass

    def deliver(self, frames):
        for frame in frames:
            if self.kind != "serial":
                self.on_frame(frame)
            else:
                for got in self.deframer.push(codec.frame_for_usb(frame)):
                    self.on_frame(got)

    async def send(self, frame):
        cmd = codec.decode_command(frame)
        if self.reply:
            return self.deliver(self.reply(cmd))
        data = {"GET_INFO": INFO, "TIME_GET": CLOCK, "TIME_SET": CLOCK}[cmd["opcode"]]
        self.deliver([codec.encode_response(cmd["seq"], cmd["opcode"], "OK", data)])


class Tables(unittest.TestCase):
    def test_the_contract_table_the_codec_table_and_the_test_vectors_list_the_same_commands(self):
        from_contract = {name: row[0] for name, row in TABLE.items()}
        self.assertGreaterEqual(len(TABLE), 32)
        self.assertEqual(from_contract, codec.OPCODES)
        self.assertEqual(from_contract, VECTORS["opcodes"])
        self.assertEqual([TABLE[n][1:] for n in ("GET_INFO", "GET_NAME", "STORE_GET", "BATTERY_GET", "LED_SET")],
                         [(16, 16), (12, 13), (1, 232), (4, 4), (0, 0)])

    def test_codec_for_every_command_only_the_contract_length_decodes(self):
        for name, (code, fewest, most) in TABLE.items():
            expected = str(fewest) if fewest == most else "%d to %d" % (fewest, most)
            for n in range(241):
                frame = response(5, code, 0, bytes(n))
                right = fewest <= n <= most
                decoded = codec.decode_frame(frame)
                self.assertEqual(decoded and decoded["opcode"], name if right else None, (name, n))
                self.assertEqual(codec.wrong_length_response(frame),
                                 None if right else {"seq": 5, "opcode": name, "got": n, "expected": expected})
            for status in range(1, 13):                        # any other status carries no data
                self.assertEqual(codec.decode_frame(response(5, code, status))["opcode"], name)
                for n in {1, 2, fewest, most, 240} - {0}:
                    frame = response(5, code, status, bytes(n))
                    self.assertIsNone(codec.decode_frame(frame), (name, status, n))
                    self.assertEqual(codec.wrong_length_response(frame),
                                     {"seq": 5, "opcode": name, "got": n, "expected": "0"})
            for cut in range(4):                               # header cut
                self.assertIsNone(codec.decode_frame(response(5, code, 0)[:cut]))

    def test_codec_a_stream_frame_or_notice_that_is_cut_short_never_decodes(self):
        for vector in VECTORS["streams"] + VECTORS["notices"]:
            if vector["value"]["frame"] == "unknown":
                continue                                       # a frame type this library does not know: no layout
            whole = bytes.fromhex(vector["hex"])
            self.assertIsNotNone(codec.decode_frame(whole))
            for cut in range(len(whole)):
                self.assertIsNone(codec.decode_frame(whole[:cut]), (vector["hex"], cut))


class OnTheBand(unittest.TestCase):
    def every_command(self, kind):
        async def main():
            for name, (code, fewest, most) in TABLE.items():
                command = next((c for c in VECTORS["commands"] if c["name"] == name), None)
                answer = next((r for r in VECTORS["responses"]
                               if r["value"]["opcode"] == name and r["value"]["status"] == "OK"), None)
                self.assertTrue(command and answer, "%s: the test vectors have a command and an OK response" % name)
                transport = Double(kind)
                band = await Band.connect(42, transport=transport, timeout=1.0)
                sent = []

                def reply(cmd):
                    self.assertEqual(cmd["opcode"], name)
                    whole = bytearray(bytes.fromhex(answer["hex"]))
                    whole[1] = cmd["seq"]
                    whole, data = bytes(whole), bytes(whole[4:])
                    wrong = [whole[:cut] for cut in range(1, 4)]                                     # header cut
                    wrong += [response(cmd["seq"], code, 0, data[:n]) for n in range(len(data)) if n < fewest]   # too short
                    wrong.append(response(cmd["seq"], code, 0, data + bytes(most + 1 - len(data))))  # too long
                    wrong.append(response(cmd["seq"], code, 6, b"\x01"))                             # not OK, with data
                    wrong.append(response(cmd["seq"], 0x7F, 0, data))                                # a command nobody sent
                    sent.append(len(wrong))
                    return wrong + [whole]
                transport.reply = reply
                got = await band.command(name, **with_bytes(command["args"]))
                self.assertEqual(dict(got), with_bytes(answer["value"]["data"]), name)
                self.assertGreaterEqual(sent[0], 6)

                # Only wrong lengths: not used and not failed at once. The wait ends at the usual timeout.
                band.timeout = 0.025
                for n in [most + 1] + ([fewest - 1] if fewest else []):
                    transport.reply = lambda cmd, n=n: [response(cmd["seq"], code, 0, bytes(n))]
                    started = time.monotonic()
                    with self.assertRaises(VexoError) as caught:
                        await band.command(name, **with_bytes(command["args"]))
                    self.assertEqual(caught.exception.code, "timeout", name)
                    self.assertIn("answered %s with %d bytes of data" % (name, n), str(caught.exception))
                    self.assertGreaterEqual(time.monotonic() - started, 0.02, name)
                self.assertTrue(band.connected)
        run(main())

    def test_usb_wrong_length_responses_are_ignored_and_the_whole_one_after_them_is_used(self):
        self.every_command("serial")

    def test_bluetooth_wrong_length_responses_are_ignored_and_the_whole_one_after_them_is_used(self):
        self.every_command("ble")

    def cut_streams(self, kind):
        async def main():
            transport = Double(kind)
            band = await Band.connect(42, transport=transport)
            events = []
            for event in EVENTS:
                band.on(event, events.append)
            seen = set()
            for vector in VECTORS["streams"] + VECTORS["notices"]:
                whole = bytes.fromhex(vector["hex"])
                transport.deliver([whole[:cut] for cut in range(1, len(whole))])
                self.assertEqual(events, [], "%s: a cut frame gave an event" % vector["hex"])
                transport.deliver([whole])
                if events:
                    seen.add(vector["value"]["frame"])
                del events[:]
            self.assertEqual(seen, set(codec.STREAM_BITS) | {"notice"})
            self.assertTrue(band.connected)
        run(main())

    def test_usb_a_cut_stream_frame_or_notice_gives_no_event_and_no_error(self):
        self.cut_streams("serial")

    def test_bluetooth_a_cut_stream_frame_or_notice_gives_no_event_and_no_error(self):
        self.cut_streams("ble")


if __name__ == "__main__":
    unittest.main()
