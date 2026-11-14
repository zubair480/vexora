"""Every response is checked against the length the contract gives for its command.

Why: over USB the start of a response can arrive cut off, then 00, then the whole response
again with the same seq (contract 2.2, firmware 1.1.3), and the cut piece can decode as a
frame. A response of the wrong length is never used: the library keeps waiting for the
right one until the usual timeout. The same tests as js/test/response-length.test.js.
"""
import asyncio
import unittest

from vexo_open import Band, VexoError, codec
from vexo_open.transport import Transport

OPCODES = codec.OPCODES


def run(coro):
    return asyncio.run(coro)


def response(seq, opcode, status, data=b""):
    return bytes([0x02, seq % 256, opcode, status]) + bytes(data)


# Data bytes of an OK response, counted from the opcode table of contract/contract.md.
SIZES = {
    "GET_INFO": 16, "GET_NAME": (12, 13), "SET_BAND_NUMBER": 2, "IDENTIFY": 0, "RESTART": 0, "SLEEP": 0,
    "TIME_SET": 13, "TIME_GET": 13, "STREAM_START": 2, "STREAM_STOP": 2, "STREAM_GET": 2,
    "STREAM_SET_INTERVAL": 13, "STREAM_GET_INTERVAL": 13, "IMU_SET": 4, "IMU_GET": 4, "EVENT_SET": 3,
    "EVENT_GET": 3, "MOTION2_SET": 9, "MOTION2_GET": 9, "MIC_SET_GAIN": 1, "MIC_GET": 7,
    "RADIO_SET_TX_POWER": 1, "RADIO_GET": 7, "LED_SET": 0, "LED_GET": 7, "TEMP_SET_INTERVAL": 1,
    "BATTERY_GET": 4, "STORE_INFO": 10, "STORE_PUT": 0, "STORE_GET": (1, 232), "STORE_DEL": 0, "STORE_WIPE": 0,
}
INFO = dict(proto_major=1, proto_minor=1, fw_major=1, fw_minor=1, fw_patch=3, hw=1, band_no=42, caps=0x7FF,
            max_frame=244, uptime_ms=5000)
LED = dict(r=0, g=255, b=0, brightness=128, pattern=1, period_ms=1000)
CLOCK = dict(set=1, unix_ms=1794470400000, t_ms=5000)


class Codec(unittest.TestCase):
    def test_every_command_has_its_length_and_only_that_length_decodes(self):
        self.assertEqual(sorted(SIZES), sorted(OPCODES))                 # a length for every command
        for name, size in SIZES.items():
            fewest, most = size if isinstance(size, tuple) else (size, size)
            expected = str(fewest) if fewest == most else "%d to %d" % (fewest, most)
            for n in (fewest, most):
                frame = response(7, OPCODES[name], 0, bytes(n))
                self.assertEqual(codec.decode_frame(frame)["opcode"], name, (name, n))
                self.assertIsNone(codec.wrong_length_response(frame))
            for n in (fewest - 1, most + 1, 0, 1, 240):
                if n < 0 or fewest <= n <= most:
                    continue
                frame = response(7, OPCODES[name], 0, bytes(n))
                self.assertIsNone(codec.decode_frame(frame), (name, n))
                self.assertEqual(codec.wrong_length_response(frame),
                                 {"seq": 7, "opcode": name, "got": n, "expected": expected})

    def test_a_status_other_than_ok_passes_with_no_data_for_every_command(self):
        for name in SIZES:
            for code, status in enumerate(codec.STATUSES):
                if not code:
                    continue
                frame = response(9, OPCODES[name], code)
                self.assertEqual(codec.decode_frame(frame),
                                 {"frame": "response", "seq": 9, "opcode": name, "status": status, "data": {}})
                self.assertIsNone(codec.wrong_length_response(frame))
            self.assertEqual(codec.decode_frame(response(9, OPCODES[name], 77))["status"], 77)   # not known here

    def test_a_command_the_library_has_no_line_for_passes_through_untouched(self):
        for opcode in (0x09, 0x0A, 0x7F, 0xFF):
            for n in (0, 1, 12, 100, 240):
                for status in (0, 1):
                    frame = response(3, opcode, status, bytes(n))
                    self.assertEqual(codec.decode_frame(frame), {
                        "frame": "response", "seq": 3, "opcode": opcode, "status": codec.STATUSES[status], "data": {}})
                    self.assertIsNone(codec.wrong_length_response(frame))

    def test_stream_frames_and_notices_are_not_touched_by_the_check(self):
        battery = codec.encode_stream("battery", dict(percent=50, state=0, millivolts=3900))
        self.assertIsNone(codec.wrong_length_response(battery))
        self.assertIsNone(codec.wrong_length_response(codec.encode_notice("USB_POWER", 1)))
        for wrong in (None, 7, "text", b"", bytes(3), bytes(500)):
            self.assertIsNone(codec.wrong_length_response(wrong))

    def test_known_limit_a_cut_stored_value_that_keeps_a_byte_has_an_allowed_length(self):
        # The length check cannot catch this one (any 1..232 bytes are a value). Only the
        # band can: it must not let a cut piece end in 00.
        whole = codec.encode_response(1, "STORE_GET", "OK", {"value": bytes([5, 6, 0, 7])})
        cut = cut_then_whole(whole, 7)[0]
        self.assertEqual(codec.decode_frame(cut)["data"]["value"], bytes([5, 6]))


class Scripted(Transport):
    """A band that answers by itself while the library connects, and afterwards as the test says."""
    kind = "serial"

    def __init__(self):
        super().__init__()
        self.reply = None           # (command) -> frames to hand to the library

    async def open(self):
        pass

    async def send(self, frame):
        cmd = codec.decode_command(frame)
        if self.reply:
            frames = self.reply(cmd)
        else:
            data = {"GET_INFO": INFO, "TIME_GET": CLOCK, "TIME_SET": CLOCK}[cmd["opcode"]]
            frames = [codec.encode_response(cmd["seq"], cmd["opcode"], "OK", data)]
        for f in frames:
            self.on_frame(f)


def cut_then_whole(frame, cut):
    """What a client really receives when the band sends the first `cut` bytes of a response,
    then 00, then the whole response: the frames the USB framing lets through."""
    wire = codec.frame_for_usb(frame)
    return codec.Deframer(synced=True).push(wire[:cut] + b"\x00" + wire)


def every_cut(frame):
    """Every way to cut a response short: (the cut pieces that look like frames, the whole one)."""
    pieces = []
    for cut in range(1, len(frame) + 1):
        pieces += cut_then_whole(frame, cut)[:-1]
    return pieces, frame


CASES = [
    ("GET_INFO", {}, INFO),
    ("GET_INFO", {}, dict(INFO, proto_minor=0, fw_minor=0, fw_patch=0, band_no=7, uptime_ms=0)),   # 1.0: more 00 bytes
    ("LED_GET", {}, dict(LED, r=0, g=0, b=0)),
    ("TIME_GET", {}, dict(set=1, unix_ms=1794470400000, t_ms=256)),
    ("STORE_INFO", {}, dict(keys_max=1000, value_max=232, free_bytes=242164, used_keys=0)),
    ("RADIO_GET", {}, dict(tx_dbm=-8, rssi_dbm=127, phy=0, interval=0, mtu=0)),
    ("MOTION2_GET", {}, dict(rate_hz=25, sensors=3, max_accel_hz=1666, max_gyro_hz=833, max_both_hz=833)),
    ("STREAM_GET", {}, dict(active=0)),
    ("GET_NAME", {}, dict(name="Vexo-HACK-42")),
]


class BandSide(unittest.TestCase):
    async def connect(self, **options):
        transport = Scripted()
        return await Band.connect(42, transport=transport, **options), transport

    def test_cut_then_whole_the_cut_piece_is_never_the_answer(self):
        async def main():
            for name, args, data in CASES:
                band, transport = await self.connect()
                seen = []

                def reply(cmd, name=name, data=data, seen=seen):
                    pieces, whole = every_cut(codec.encode_response(cmd["seq"], name, "OK", data))
                    seen.extend(pieces)
                    return pieces + [whole]
                transport.reply = reply
                self.assertEqual(dict(await band.command(name, **args)), data, name)
                if name != "GET_NAME":
                    self.assertTrue(seen, "%s: at least one cut piece gets through the USB framing" % name)
        run(main())

    def test_a_stored_value_cut_down_to_nothing_is_not_taken_for_an_empty_value(self):
        async def main():
            band, transport = await self.connect()
            value = bytes([0, 1, 2, 3]) + bytes(60) + b"\x09"       # starts with 00: the cut can fall right after the header
            pieces = []

            def reply(cmd):
                whole = codec.encode_response(cmd["seq"], "STORE_GET", "OK", {"value": value})
                pieces.extend(cut_then_whole(whole, 5)[:-1])        # the band sent 5 bytes, then 00
                return pieces + [whole]
            transport.reply = reply
            self.assertEqual(await band.storage.get(5), value)
            self.assertEqual(len(pieces), 1)
            self.assertEqual(pieces[0][2:], bytes([OPCODES["STORE_GET"], 0]))   # whole-looking: OK, no value
        run(main())

    def test_wrong_length_and_no_right_answer_is_a_timeout_that_says_so(self):
        async def main():
            band, transport = await self.connect(timeout=0.03)
            for name, size, n in (("LED_GET", "7", 8), ("LED_GET", "7", 6), ("LED_SET", "0", 1), ("GET_INFO", "16", 15),
                                  ("GET_INFO", "16", 17), ("STORE_GET", "1 to 232", 0), ("STORE_GET", "1 to 232", 233),
                                  ("GET_NAME", "12 to 13", 8)):
                transport.reply = lambda cmd, name=name, n=n: [response(cmd["seq"], OPCODES[name], 0, bytes(n))]
                args = LED if name == "LED_SET" else {"key": 1} if name == "STORE_GET" else {}
                with self.assertRaises(VexoError) as caught:
                    await band.command(name, **args)
                self.assertEqual(caught.exception.code, "timeout", name)
                self.assertIn("answered %s with %d bytes of data where the contract has %s," % (name, n, size),
                              caught.exception.message)
            transport.reply = lambda cmd: []                        # really silent: the usual sentence
            with self.assertRaises(VexoError) as caught:
                await band.command("LED_GET")
            self.assertEqual(caught.exception.code, "timeout")
            self.assertIn("did not answer LED_GET", caught.exception.message)
            transport.reply = lambda cmd: [codec.encode_response(cmd["seq"], "LED_GET", "OK", LED)]
            self.assertEqual(dict(await band.command("LED_GET")), LED)
            self.assertTrue(band.connected)
        run(main())

    def test_a_wrong_length_for_another_seq_or_command_does_not_change_the_message(self):
        async def main():
            band, transport = await self.connect(timeout=0.03)
            transport.reply = lambda cmd: [response(cmd["seq"] + 1, OPCODES["LED_GET"], 0, bytes(3)),
                                           response(cmd["seq"], OPCODES["IMU_GET"], 0, bytes(3))]
            with self.assertRaises(VexoError) as caught:
                await band.command("LED_GET")
            self.assertEqual(caught.exception.code, "timeout")
            self.assertIn("did not answer LED_GET", caught.exception.message)
        run(main())

    def test_a_status_other_than_ok_still_fails_at_once_and_an_unknown_command_is_left_alone(self):
        async def main():
            band, transport = await self.connect()
            transport.reply = lambda cmd: [response(cmd["seq"], 0x09, 0, bytes(12)),
                                           codec.encode_response(cmd["seq"], cmd["opcode"], "DENIED")]
            with self.assertRaises(VexoError) as caught:
                await band.command("LED_SET", **LED)
            self.assertEqual(caught.exception.code, "denied")
            transport.reply = lambda cmd: [codec.encode_response(cmd["seq"], cmd["opcode"], "NOT_FOUND")]
            self.assertIsNone(await band.storage.get(5))
            transport.reply = lambda cmd: [response(cmd["seq"], OPCODES["LED_GET"], 77)]
            with self.assertRaises(VexoError) as caught:
                await band.command("LED_GET")
            self.assertEqual(caught.exception.code, "unknown-status")
        run(main())


if __name__ == "__main__":
    unittest.main()
