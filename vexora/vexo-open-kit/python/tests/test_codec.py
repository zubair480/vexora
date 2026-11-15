"""The codec against the shared vectors in contract/test-vectors.json."""
import json
import os
import random
import unittest

from vexo_open import codec

with open(os.path.join(os.path.dirname(__file__), "..", "..", "contract", "test-vectors.json")) as f:
    VECTORS = json.load(f)


def unhex(value: dict) -> dict:
    """The vectors write the bytes fields 'opus' and 'value' as hex; turn them into bytes."""
    out = dict(value)
    for key in ("opus", "value"):
        if isinstance(out.get(key), str):
            out[key] = bytes.fromhex(out[key])
    if isinstance(out.get("data"), dict):
        out["data"] = unhex(out["data"])
    return out


class ConstantsTest(unittest.TestCase):
    def test_revision(self):
        self.assertEqual(codec.CONTRACT_REVISION, VECTORS["contract_revision"])

    def test_names(self):
        self.assertEqual(codec.NAME_PREFIX, VECTORS["name_prefix"])
        for v in VECTORS["names"]:
            self.assertEqual(codec.band_name(v["number"]), v["name"])
            self.assertTrue(codec.band_name(v["number"]).startswith(codec.NAME_PREFIX))
        self.assertEqual(codec.band_name("007"), "Vexo-HACK-07")
        self.assertEqual(codec.NAME_PREFIX, "Vexo-HACK-")
        # At least two digits; three from 100. Nothing depends on how many bands exist.
        self.assertEqual([codec.band_id(n) for n in (0, 1, 7, 42, 99, 100, 123, 999)],
                         ["00", "01", "07", "42", "99", "100", "123", "999"])
        self.assertEqual(codec.band_name(100), "Vexo-HACK-100")
        for n in (0, 1, 9, 10, 99, 100, 999):
            self.assertEqual(codec.band_number_of(codec.band_name(n)), n)
        self.assertEqual(codec.band_number_of("Vexo-HACK-007"), 7)
        for bad in ("Vexo-HACK-7", "Vexo-HACK-1000", "Vexo-HACK-", "Vexo-042", "vexo-hack-07",
                    "Vexo-HACK-07 ", "xVexo-HACK-07", "Vexo-HACK-\u0663\u0663", None, 7):
            self.assertIsNone(codec.band_number_of(bad), bad)
        for bad in (1000, -1, "x", None, 4.5, True):
            with self.assertRaises(ValueError):
                codec.band_name(bad)

    def test_uuids(self):
        self.assertEqual(codec.SERVICE_UUID, VECTORS["uuids"]["service"])
        self.assertEqual(codec.RX_UUID, VECTORS["uuids"]["rx"])
        self.assertEqual(codec.TX_UUID, VECTORS["uuids"]["tx"])

    def test_opcodes(self):
        self.assertEqual(codec.OPCODES, VECTORS["opcodes"])

    def test_statuses(self):
        self.assertEqual(codec.STATUSES, VECTORS["statuses"])

    def test_stream_bits(self):
        self.assertEqual(codec.STREAM_BITS, VECTORS["stream_bits"])

    def test_other_tables(self):
        self.assertEqual(codec.LED_PATTERNS, ["off", "solid", "blink", "breathe", "flash", "rainbow"])
        self.assertEqual(codec.EVENT_KINDS[2], "double-tap")
        self.assertEqual(len(codec.CAPABILITIES), 11)
        self.assertEqual((codec.MAGIC, codec.MAX_FRAME), (0x5856, 244))


class CobsTest(unittest.TestCase):
    def test_both_directions(self):
        for v in VECTORS["cobs"]:
            decoded, encoded = bytes.fromhex(v["decoded"]), bytes.fromhex(v["encoded"])
            self.assertEqual(codec.cobs_encode(decoded), encoded)
            self.assertEqual(codec.cobs_decode(encoded), decoded)
            self.assertNotIn(0, codec.cobs_encode(decoded))
            self.assertEqual(codec.frame_for_usb(decoded), encoded + b"\x00")

    def test_invalid(self):
        for bad in (b"", b"\x05\x01\x02", b"\x02\x00", b"\x00"):
            self.assertIsNone(codec.cobs_decode(bad))

    def test_random_round_trip(self):
        rng = random.Random(1)
        for _ in range(300):
            data = bytes(rng.choice((0, 0, 1, 255, rng.randrange(256))) for _ in range(rng.randrange(600)))
            self.assertEqual(codec.cobs_decode(codec.cobs_encode(data)), data)

    def test_usb_stream_in_chunks(self):
        raw = bytes.fromhex(VECTORS["usb_stream"]["bytes"])
        for size in (1, 3, 7, len(raw)):
            deframer = codec.Deframer()
            frames = []
            for i in range(0, len(raw), size):
                frames += deframer.push(raw[i:i + size])
            self.assertEqual([f.hex() for f in frames], VECTORS["usb_stream"]["frames"], size)

    def test_synced_deframer_keeps_first_packet(self):
        self.assertEqual(codec.Deframer(synced=True).push(codec.frame_for_usb(b"\x02\x00\x04\x00")),
                         [b"\x02\x00\x04\x00"])
        self.assertEqual(codec.Deframer().push(codec.frame_for_usb(b"\x02\x00\x04\x00")), [])


class CommandTest(unittest.TestCase):
    def test_encode(self):
        for v in VECTORS["commands"]:
            args = unhex(v["args"])
            frame = codec.encode_command(v["seq"], v["name"], args)
            self.assertEqual(frame.hex(), v["hex"], v["name"])

    def test_decode_command_inverts_encode(self):
        for v in VECTORS["commands"]:
            cmd = codec.decode_command(bytes.fromhex(v["hex"]))
            self.assertEqual((cmd["seq"], cmd["opcode"]), (v["seq"], v["name"]))
            self.assertEqual(codec.encode_command(cmd["seq"], cmd["opcode"], cmd["args"]).hex(), v["hex"])
            self.assertEqual(cmd["args"].pop("magic", codec.MAGIC), codec.MAGIC)
            self.assertEqual(cmd["args"], unhex(v["args"]))

    def test_decode_command_problems(self):
        self.assertEqual(codec.decode_command(bytes.fromhex("01070405ff")),
                         {"seq": 7, "opcode": "IDENTIFY", "bad_length": True})
        self.assertEqual(codec.decode_command(bytes.fromhex("010704")),
                         {"seq": 7, "opcode": "IDENTIFY", "bad_length": True})
        self.assertEqual(codec.decode_command(bytes.fromhex("010999")), {"seq": 9, "opcode": 0x99, "args": {}})
        self.assertIsNone(codec.decode_command(bytes.fromhex("0105")))
        self.assertIsNone(codec.decode_command(bytes.fromhex("02000100")))

    def test_encode_rejects_bad_input(self):
        bad = [("NOPE", {}), ("IDENTIFY", {}), ("IDENTIFY", {"seconds": 5, "extra": 1}),
               ("IDENTIFY", {"seconds": 256}), ("IDENTIFY", {"seconds": -1}), ("IDENTIFY", {"seconds": "5"}),
               ("RADIO_SET_TX_POWER", {"dbm": 200}), ("STREAM_START", {"mask": 70000}),
               ("STORE_PUT", {"key": 1, "value": bytes(240)}), ("GET_INFO", {"x": 1})]
        for name, args in bad:
            with self.assertRaises(ValueError, msg=(name, args)):
                codec.encode_command(0, name, args)


class DecodeTest(unittest.TestCase):
    def test_responses_notices_streams(self):
        for section in ("responses", "notices", "streams"):
            for v in VECTORS[section]:
                self.assertEqual(codec.decode_frame(bytes.fromhex(v["hex"])), unhex(v["value"]), v["hex"])

    def test_invalid(self):
        self.assertGreater(len(VECTORS["invalid"]), 5)
        for v in VECTORS["invalid"]:
            self.assertIsNone(codec.decode_frame(bytes.fromhex(v["hex"])), v["note"])

    def test_band_side_helpers_reproduce_the_hex(self):
        for v in VECTORS["responses"]:
            x = unhex(v["value"])
            self.assertEqual(codec.encode_response(x["seq"], x["opcode"], x["status"], x["data"]).hex(), v["hex"])
        for v in VECTORS["notices"]:
            self.assertEqual(codec.encode_notice(v["value"]["code"], v["value"]["arg"]).hex(), v["hex"])
        for v in VECTORS["streams"]:
            x = unhex(v["value"])
            if x["frame"] != "unknown":
                self.assertEqual(codec.encode_stream(x["frame"], x).hex(), v["hex"])

    def test_fuzz_never_raises(self):
        rng = random.Random(2026)
        known = [bytes.fromhex(v["hex"]) for s in ("responses", "notices", "streams") for v in VECTORS[s]]
        for i in range(20000):
            if i % 2:
                data = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 260)))
            else:  # a real frame, damaged: cut short, or with one byte changed
                data = bytearray(rng.choice(known))
                if rng.random() < 0.5:
                    data = data[:rng.randrange(len(data) + 1)]
                elif data:
                    data[rng.randrange(len(data))] = rng.randrange(256)
            result = codec.decode_frame(bytes(data))
            self.assertTrue(result is None or isinstance(result, dict))
            self.assertTrue(codec.decode_command(bytes(data)) is None or True)
        for odd in (None, "text", 5, bytearray(b"\x14\x57\x00\x93\x0f"), memoryview(b"\x03\x01\x0e")):
            codec.decode_frame(odd)
        self.assertEqual(codec.decode_frame(bytearray(b"\x14\x57\x00\x93\x0f"))["percent"], 87)


if __name__ == "__main__":
    unittest.main()
