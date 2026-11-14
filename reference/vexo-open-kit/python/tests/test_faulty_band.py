"""A faulty or pretend band must not crash the program with its answers: a clock far in
the future, a `sensors` value that does not exist, a sound counter that jumps with every
packet. The same tests as js/test/faulty-band.test.js."""
import asyncio
import datetime
import unittest

from vexo_open import Band, VexoError, audio, codec
from vexo_open.transport import Transport

INFO = dict(proto_major=1, proto_minor=1, fw_major=1, fw_minor=1, fw_patch=3, hw=1, band_no=42, caps=0x7FF,
            max_frame=244, uptime_ms=5000)
GOOD_CLOCK = dict(set=1, unix_ms=1794470400000, t_ms=5000)
MOTION2 = dict(rate_hz=25, sensors=3, max_accel_hz=833, max_gyro_hz=833, max_both_hz=416)
BAD_TIMES = (2 ** 64 - 1, 2 ** 53, 4102444800000, 1577836799999, 1, 0)   # outside 2020 .. 2100


def run(coro):
    return asyncio.run(coro)


class Faulty(Transport):
    """A band that answers every command with the data the test gives for it."""
    kind = "ble"

    def __init__(self, **answers):
        super().__init__()
        self.answers = dict({"GET_INFO": INFO, "TIME_GET": GOOD_CLOCK, "TIME_SET": GOOD_CLOCK}, **answers)

    async def open(self):
        pass

    async def send(self, frame):
        cmd = codec.decode_command(frame)
        if cmd["opcode"] in self.answers:
            self.on_frame(codec.encode_response(cmd["seq"], cmd["opcode"], "OK", self.answers[cmd["opcode"]]))


class Clock(unittest.TestCase):
    def test_a_clock_outside_2020_to_2100_is_treated_as_not_set(self):
        async def main():
            for unix_ms in BAD_TIMES:
                bad = dict(set=1, unix_ms=unix_ms, t_ms=7000)
                band = await Band.connect(42, transport=Faulty(TIME_GET=bad, TIME_SET=bad))
                self.assertEqual(band.clock, "computer", unix_ms)
                for now in (await band.time(), await band.set_time()):
                    self.assertEqual((now.source, now.set, now.t), ("computer", False, 7000), unix_ms)
                    year = datetime.datetime.fromtimestamp(now.time).year     # raised "year is out of range"
                    self.assertTrue(2020 <= year < 2100, unix_ms)
                events = []
                band.on("temperature", events.append)
                band.transport.on_frame(codec.encode_stream("temp", dict(t_ms=7100, temp_mc=31000)))
                self.assertTrue(2020 <= datetime.datetime.fromtimestamp(events[0].time).year < 2100, unix_ms)
        run(main())

    def test_a_clock_inside_the_range_is_used_as_before(self):
        async def main():
            for unix_ms in (1577836800000, 1794470400000, 4102444799999):
                good = dict(set=1, unix_ms=unix_ms, t_ms=7000)
                band = await Band.connect(42, transport=Faulty(TIME_GET=good, TIME_SET=good))
                self.assertEqual(band.clock, "band")
                now = await band.time()
                self.assertEqual((now.source, now.set, now.time), ("band", True, unix_ms / 1000))
        run(main())


class Sensors(unittest.TestCase):
    def test_an_answer_with_a_sensors_value_that_does_not_exist_is_ignored(self):
        async def main():
            for sensors in (0, 4, 7, 255):
                for command in ("MOTION2_GET", "MOTION2_SET"):
                    band = await Band.connect(42, timeout=0.05, transport=Faulty(
                        MOTION2_GET=dict(MOTION2, sensors=sensors if command == "MOTION2_GET" else 3),
                        MOTION2_SET=dict(MOTION2, sensors=sensors), STREAM_START=dict(active=256)))
                    for call in (band.motion_limits, lambda: band.configure(motion=50, sensors="accel"), band.get_motion):
                        try:
                            await call()                       # was: KeyError, a crash in the program
                        except VexoError as e:
                            self.assertEqual(e.code, "timeout", (sensors, command))
                    self.assertTrue(band.connected)
        run(main())

    def test_the_three_real_values_are_used(self):
        async def main():
            for sensors, name in ((1, "accel"), (2, "gyro"), (3, "both")):
                band = await Band.connect(42, transport=Faulty(MOTION2_GET=dict(MOTION2, sensors=sensors),
                                                               IMU_GET=dict(rate_hz=25, accel_range_g=2, gyro_range_dps=2000)))
                self.assertEqual((await band.get_motion()).sensors, name)
        run(main())


def stub_decoder():
    """A PcmDecoder without the 'av' package: decoding a packet gives 20 ms of silence."""
    decoder = audio.PcmDecoder.__new__(audio.PcmDecoder)
    decoder.conceal, decoder.lost, decoder.packets, decoder.samples = False, 0, 0, 0
    decoder._next_seq, decoder._toc = None, 0xB8

    def decode(packet):
        decoder.packets += 1
        decoder.samples += audio.FRAME_SAMPLES
        return bytes(2 * audio.FRAME_SAMPLES)
    decoder._decode = decode
    return decoder


class SoundGaps(unittest.TestCase):
    def test_a_counter_that_jumps_by_the_most_that_is_filled_cannot_multiply_the_sound(self):
        for jump in (audio.MAX_FILL + 1, audio.MAX_FILL, 26, 3):     # a gap of 50, 49, 25, 2 before every packet
            decoder = stub_decoder()
            for i in range(1000):
                decoder.feed(b"\xb8\x01", (i * jump) & 0xFFFF)
            # was: 51 packets of sound for each packet that arrived
            self.assertLessEqual(decoder.packets, 2 * 1000 + audio.MAX_FILL, jump)
            self.assertEqual(decoder.lost, 999 * (jump - 1))          # every lost packet is still counted

    def test_a_link_that_loses_half_of_its_packets_is_still_filled_completely(self):
        decoder = stub_decoder()
        for seq in range(0, 2000, 2):
            decoder.feed(b"\xb8\x01", seq)
        self.assertEqual((decoder.packets, decoder.lost), (1999, 999))

    def test_one_gap_of_a_second_is_filled_and_the_budget_comes_back(self):
        decoder = stub_decoder()
        decoder.feed(b"\xb8\x01", 0)
        self.assertEqual(len(decoder.feed(b"\xb8\x01", 51)), 51 * 640)       # 50 lost: all filled
        self.assertEqual(len(decoder.feed(b"\xb8\x01", 102)), 2 * 640)       # 50 lost again at once: 1 filled
        seq = 102
        for _ in range(60):                                                  # 60 packets in order: the budget is back
            seq += 1
            decoder.feed(b"\xb8\x01", seq)
        self.assertEqual(len(decoder.feed(b"\xb8\x01", seq + 51)), 51 * 640)


if __name__ == "__main__":
    unittest.main()
