"""A band (or a file) that lies. Every number that comes from outside is checked here with
the worst value it can take: lengths, sample counts, counters that jump, bytes that never
end, files that are cut off. Nothing in the library may loop, grow or allocate without a
limit because of them, and a well-formed band must still work afterwards.

Also here, because the band's firmware relies on them: what the USB transport does with the
port (one 00 first, and it never stops reading), and firmware version numbers.
"""
import asyncio
import random
import struct
import sys
import threading
import time
import types
import unittest
from unittest import mock

from vexo_open import Band, SimTransport, VexoError, audio, codec
from vexo_open.transports import serial as serial_transport

MAX_FRAME = codec.MAX_FRAME


def run(coro):
    return asyncio.run(coro)


# ---- frames ----

class HostileFrames(unittest.TestCase):
    def test_a_frame_longer_than_the_limit_is_dropped(self):
        good = codec.encode_stream("battery", dict(percent=50, state=0, millivolts=3900))
        self.assertIsNotNone(codec.decode_frame(good))
        self.assertIsNone(codec.decode_frame(good + bytes(MAX_FRAME)))
        self.assertIsNone(codec.decode_frame(bytes([0x13]) + bytes(100000)))
        self.assertIsNone(codec.decode_frame(b""))

    def test_a_sample_count_larger_than_the_frame_is_dropped(self):
        motion = bytes([0x10]) + struct.pack("<IBB", 1, 10, 255)            # says 255 samples, has none
        self.assertIsNone(codec.decode_frame(motion))
        self.assertIsNone(codec.decode_frame(motion + bytes(12 * 19)))      # room for 19 only
        fast = bytes([0x18]) + struct.pack("<IIIBB", 1, 0, 1000, 1, 255)    # 255 samples of 6 bytes = 1530
        self.assertIsNone(codec.decode_frame(fast))
        self.assertIsNone(codec.decode_frame(fast + bytes(MAX_FRAME - len(fast))))
        for sensors in (0, 4, 255):                                         # the size of a sample is unknown
            self.assertIsNone(codec.decode_frame(bytes([0x18]) + struct.pack("<IIIBB", 1, 0, 1000, sensors, 1)
                                                 + bytes(12)))

    def test_the_most_samples_one_frame_can_hold(self):
        most = 0
        for count in range(256):
            f = codec.decode_frame(bytes([0x18]) + struct.pack("<IIIBB", 1, 0, 1000, 1, count) + bytes(6 * count))
            if f is not None:
                most = max(most, len(f["samples"]))
        self.assertEqual(most, (MAX_FRAME - 15) // 6)                       # 38: the frame limit decides

    def test_an_audio_length_larger_than_the_frame_is_dropped(self):
        self.assertIsNone(codec.decode_frame(bytes([0x13]) + struct.pack("<HB", 7, 255) + bytes(10)))
        self.assertIsNone(codec.decode_frame(bytes([0x13]) + struct.pack("<HB", 7, 255)))
        f = codec.decode_frame(bytes([0x13]) + struct.pack("<HB", 7, 240) + bytes(240))
        self.assertEqual(len(f["opus"]), 240)                               # the largest packet there is

    def test_random_bytes_never_raise_and_never_give_more_than_a_frame_holds(self):
        rng = random.Random(123)
        for _ in range(20000):
            kind = rng.choice([0x02, 0x03, 0x10, 0x13, 0x18, rng.randrange(256)])
            data = bytes([kind]) + bytes(rng.randrange(256) for _ in range(rng.randrange(0, 60)))
            f = codec.decode_frame(data)
            if f and "samples" in f:
                self.assertLessEqual(len(f["samples"]), 40)
        for wrong in (None, 7, "text", [1, 2]):
            self.assertIsNone(codec.decode_frame(wrong))


class HostileUsbBytes(unittest.TestCase):
    def test_bytes_with_no_00_do_not_pile_up(self):
        deframer = codec.Deframer(synced=True)
        for _ in range(2000):                                               # 1 MB and never a 00
            self.assertEqual(deframer.push(b"\x55" * 512), [])
            self.assertLessEqual(len(deframer._buffer), 2 * MAX_FRAME)
        frame = codec.encode_stream("battery", dict(percent=50, state=0, millivolts=3900))
        # the 00 ends the noise; the next whole frame is read again
        self.assertEqual(deframer.push(b"\x00" + codec.frame_for_usb(frame)), [frame])

    def test_one_byte_at_a_time_with_no_00(self):
        deframer = codec.Deframer(synced=True)
        for _ in range(5000):
            deframer.push(b"\x41")
        self.assertLessEqual(len(deframer._buffer), 2 * MAX_FRAME)

    def test_a_packet_that_decodes_to_more_than_a_frame_is_dropped(self):
        deframer = codec.Deframer(synced=True)
        big = codec.cobs_encode(bytes([0x13]) + bytes(range(1, 250)) * 4) + b"\x00"
        self.assertEqual(deframer.push(big), [])
        self.assertEqual(len(deframer._buffer), 0)

    def test_broken_cobs_is_dropped_without_a_word(self):
        deframer = codec.Deframer(synced=True)
        self.assertEqual(deframer.push(b"\xff\x01\x02\x00\x05\x01\x00\x00\x00"), [])
        self.assertIsNone(codec.cobs_decode(b"\x09\x01"))
        self.assertIsNone(codec.cobs_decode(b""))


# ---- sound ----

def stub_decoder(conceal=False):
    """A PcmDecoder without the 'av' package: decoding a packet gives 20 ms of silence."""
    decoder = audio.PcmDecoder.__new__(audio.PcmDecoder)
    decoder.conceal, decoder.lost, decoder.packets, decoder.samples = conceal, 0, 0, 0
    decoder._next_seq, decoder._toc = None, 0xB8

    def decode(packet):
        decoder.packets += 1
        decoder.samples += audio.FRAME_SAMPLES
        return bytes(2 * audio.FRAME_SAMPLES)
    decoder._decode = decode
    return decoder


class HostileSound(unittest.TestCase):
    def test_a_gap_of_up_to_one_second_is_filled(self):
        self.assertEqual(audio.MAX_FILL, 50)
        decoder = stub_decoder()
        decoder.feed(b"\xb8\x01", 0)
        out = decoder.feed(b"\xb8\x01", 51)                                 # 50 lost
        self.assertEqual((len(out), decoder.lost, decoder.packets), (51 * 640, 50, 52))

    def test_a_longer_hole_is_counted_but_not_filled(self):
        for conceal in (False, True):
            decoder = stub_decoder(conceal)
            decoder.feed(b"\xb8\x01", 0)
            out = decoder.feed(b"\xb8\x01", 52)                             # 51 lost
            self.assertEqual((len(out), decoder.lost, decoder.packets), (640, 51, 2))
            out = decoder.feed(b"\xb8\x01", 53)                             # and it simply goes on
            self.assertEqual((len(out), decoder.lost), (640, 51))

    def test_a_counter_that_jumps_on_every_packet_gives_one_packet_of_sound_each(self):
        decoder = stub_decoder()
        seq, total = 0, 0
        for _ in range(500):
            seq = (seq + 0x8000) & 0xFFFF
            total += len(decoder.feed(b"\xb8\x01", seq))
        self.assertEqual(total, 500 * 640)                                  # was: 500 fillers for each
        self.assertEqual(decoder.packets, 500)

    def test_an_ogg_file_with_one_giant_packet_is_refused_quickly(self):
        def page(body, lacing):
            return struct.pack("<4sBBqIIIB", b"OggS", 0, 0, 0, 1, 0, 0, len(lacing)) + lacing + body
        head = page(b"OpusHead" + bytes(11), bytes([19])) + page(b"OpusTags" + bytes(8), bytes([16]))
        good = head + page(b"\xb8\x01\x02", bytes([3]))
        self.assertEqual(audio.ogg_packets(good), [b"\xb8\x01\x02"])
        giant = head + page(bytes(255 * 255), b"\xff" * 255) * 320          # 20 MB, "one packet"
        started = time.monotonic()
        with self.assertRaises(VexoError) as caught:
            audio.ogg_packets(giant)
        self.assertEqual(caught.exception.code, "bad-arg")
        self.assertIn(str(audio.MAX_OGG_PACKET), str(caught.exception))
        self.assertLess(time.monotonic() - started, 5)
        for cut in range(len(head) + 1, len(good)):
            try:
                self.assertEqual(audio.ogg_packets(good[:cut]), [])
            except VexoError as e:
                self.assertEqual(e.code, "bad-arg")


# ---- a band that sends hostile frames to a connected program ----

class Rewriting(SimTransport):
    """A pretend band whose frames a test can change on their way to the library."""

    def __init__(self, *args, **kwargs):
        self.rewrite = lambda frame: frame
        super().__init__(*args, **kwargs)

    @property
    def on_frame(self):
        return lambda frame: self._to_library(self.rewrite(bytes(frame)))

    @on_frame.setter
    def on_frame(self, fn):
        self._to_library = fn


def with_patch(patch):
    """Rewrite GET_INFO answers so that the band reports firmware 1.1.<patch>."""
    def rewrite(frame):
        if frame[0] == 0x02 and frame[2] == codec.OPCODES["GET_INFO"] and frame[3] == 0:
            return frame[:8] + bytes([patch]) + frame[9:]
        return frame
    return rewrite


class HostileBand(unittest.TestCase):
    def test_hostile_frames_do_not_stop_a_connected_program(self):
        async def main():
            sim = Rewriting(42, realtime=False)
            band = await Band.connect(42, transport=sim)
            seen = []
            band.on("motion", seen.append)
            band.on("audio", seen.append)
            rng = random.Random(5)
            hostile = [
                bytes([0x10]) + struct.pack("<IBB", 1, 10, 255),
                bytes([0x18]) + struct.pack("<IIIBB", 1, 0xFFFFFFFF, 0xFFFFFFFF, 3, 255),
                bytes([0x13]) + struct.pack("<HB", 65535, 255),
                bytes([0x13]) + bytes(100000),
                bytes([0x02, 0, 0x01, 0]),                                  # a GET_INFO answer with no data
                bytes([0x02]), bytes([0x03]), bytes([0xEE]) * 244, b"",
            ] + [bytes(rng.randrange(256) for _ in range(rng.randrange(1, 245))) for _ in range(3000)]
            for frame in hostile:
                band._receive(frame)
            self.assertLessEqual(len(seen), 3000 * 40)
            # sample numbers and times at the very end of their range: arithmetic only, no loop
            band._receive(bytes([0x18]) + struct.pack("<IIIBB", 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 1, 2) + bytes(12))
            self.assertEqual([m.n for m in seen[-2:]], [0xFFFFFFFF, 0])
            self.assertEqual((await band.get_info()).number, 42)            # and it still answers
            await band.set_led("#00ff00")
            await band.disconnect()
        run(main())

    def test_a_band_number_that_cannot_exist(self):
        async def main():
            sim = Rewriting(42, realtime=False)
            sim.rewrite = lambda f: f[:10] + b"\xff\xff" + f[12:] if f[0] == 2 and f[2] == 1 else f
            with self.assertRaises(VexoError) as caught:
                await Band.connect(42, transport=sim)
            self.assertEqual(caught.exception.code, "wrong-band")
            band = await Band.connect(None, transport=sim)
            self.assertEqual(band.number, 65535)
            await band.disconnect()
        run(main())

    def test_firmware_1_1_x_is_firmware_1_1(self):
        async def main():
            for patch in (0, 1, 2, 3, 9, 255):
                sim = Rewriting(42, realtime=False)
                sim.rewrite = with_patch(patch)
                band = await Band.connect(42, transport=sim)
                self.assertEqual(band.info.firmware, "1.1.%d" % patch)
                self.assertEqual(band.info.protocol, "1.1")
                self.assertEqual(band.info.features, ["fast-motion", "intervals", "clock"])
                self.assertEqual(band.clock, "band")
                await band.configure(motion=416, sensors="accel", battery=60)       # 1.1 features work
                self.assertEqual((await band.get_interval("battery")).seconds, 60)
                await band.disconnect()
            sim = Rewriting(42, realtime=False, firmware="1.0")
            sim.rewrite = with_patch(7)                                              # 1.0.7: still 1.0
            band = await Band.connect(42, transport=sim)
            self.assertEqual((band.info.firmware, band.info.features), ("1.0.7", []))
            with self.assertRaises(VexoError) as caught:
                await band.configure(motion=416, sensors="accel")
            self.assertEqual(caught.exception.code, "not-available")
            await band.disconnect()
        run(main())


# ---- the USB cable: what the firmware relies on ----

def fake_serial(opened):
    """A pretend 'serial' package. Its port counts how often it is read."""
    class SerialException(Exception):
        pass

    class Serial:
        def __init__(self, port=None, baudrate=9600, timeout=None, rtscts=False, dsrdtr=False):
            self.log, self.reads, self.taken = [], 0, 0
            self.lock = threading.Lock()
            self.waiting = bytearray()
            self.band_side = codec.Deframer()       # like the band: drops everything up to the first 00
            self.answers = True
            self.is_open = False
            self.port = port

        def open(self):                             # like pyserial: Serial(None, ...) is opened later
            self.is_open = True
            opened.append(self)

        dtr = property(lambda self: None, lambda self, value: self.log.append(("dtr", value)))

        @property
        def in_waiting(self):
            return len(self.waiting)

        def reset_input_buffer(self):
            self.log.append(("reset_input_buffer",))

        def from_band(self, data):
            with self.lock:
                self.waiting += data

        def read(self, size=1):
            if not self.is_open:
                raise SerialException("closed")
            self.reads += 1
            with self.lock:
                chunk = bytes(self.waiting[:size])
                del self.waiting[:size]
            self.taken += len(chunk)
            if not chunk:
                time.sleep(0.002)
            return chunk

        def write(self, data):
            self.log.append(("write", bytes(data)))
            for frame in self.band_side.push(data):
                cmd = codec.decode_command(frame)
                info = dict(proto_major=1, proto_minor=0, fw_major=1, fw_minor=0, fw_patch=0, hw=1, band_no=42,
                            caps=0x07FF, max_frame=244, uptime_ms=5)
                if self.answers:
                    self.from_band(codec.frame_for_usb(codec.encode_response(
                        cmd["seq"], cmd["opcode"], "OK", info if cmd["opcode"] == "GET_INFO" else {})))

        def close(self):
            self.is_open = False

    serial = types.ModuleType("serial")
    serial.Serial, serial.SerialException = Serial, SerialException
    tools = types.ModuleType("serial.tools")
    tools.list_ports = types.ModuleType("serial.tools.list_ports")
    serial.tools = tools
    return {"serial": serial, "serial.tools": tools, "serial.tools.list_ports": tools.list_ports}


BATTERY = codec.frame_for_usb(codec.encode_stream("battery", dict(percent=50, state=0, millivolts=3900)))


class UsbSession(unittest.TestCase):
    def test_the_first_byte_of_every_session_is_00(self):
        async def main():
            opened = []
            with mock.patch.dict(sys.modules, fake_serial(opened)):
                band = await Band.connect(42, transport="serial", port="COM5")
                writes = [entry[1] for entry in opened[0].log if entry[0] == "write"]
                self.assertEqual(writes[0], b"\x00")                # before the first command
                self.assertEqual(codec.decode_command(codec.cobs_decode(writes[1][:-1]))["opcode"], "GET_INFO")
                self.assertEqual([w for w in writes[1:] if w == b"\x00"], [])       # and only once
                await band.transport.open()                         # a reconnect opens the port again
                again = [entry[1] for entry in opened[1].log if entry[0] == "write"]
                self.assertEqual(again[0], b"\x00")
                await band.disconnect()
        run(main())

    def test_it_reads_while_a_command_waits_for_its_answer(self):
        async def main():
            opened = []
            with mock.patch.dict(sys.modules, fake_serial(opened)):
                band = await Band.connect(42, transport="serial", port="COM5", timeout=0.4)
                port = opened[0]
                heard = []
                band.on("battery", heard.append)
                port.answers = False                                # the band goes quiet on commands ...
                waiting = asyncio.ensure_future(band.get_led())
                await asyncio.sleep(0.05)
                before = port.reads
                port.from_band(BATTERY * 20)                        # ... but keeps streaming
                await asyncio.sleep(0.15)
                self.assertFalse(waiting.done())
                self.assertGreater(port.reads, before + 5)          # still reading
                self.assertEqual(len(port.waiting), 0)              # everything was taken off the port
                self.assertEqual(len(heard), 20)                    # and delivered while the command waits
                with self.assertRaises(VexoError) as caught:
                    await waiting
                self.assertEqual(caught.exception.code, "timeout")
                port.answers = True
                await band.disconnect()
        run(main())

    def test_it_reads_while_the_program_is_busy(self):
        async def main():
            opened = []
            with mock.patch.dict(sys.modules, fake_serial(opened)):
                band = await Band.connect(42, transport="serial", port="COM5")
                port = opened[0]
                heard = []
                band.on("battery", heard.append)
                before = port.reads
                port.from_band(BATTERY * 300)
                time.sleep(0.3)                                     # a handler that blocks the whole program
                self.assertGreater(port.reads, before + 5)          # the reader went on meanwhile
                self.assertEqual(len(port.waiting), 0)              # the port was emptied
                self.assertEqual(heard, [])                         # nothing could be delivered yet
                await asyncio.sleep(0.05)
                self.assertEqual(len(heard), 300)                   # nothing was lost
                self.assertEqual(band.transport._waiting, 0)
                await band.disconnect()
        run(main())

    def test_frames_a_stuck_program_never_takes_do_not_pile_up_without_limit(self):
        async def main():
            opened = []
            with mock.patch.dict(sys.modules, fake_serial(opened)), \
                    mock.patch.object(serial_transport, "MAX_WAITING", 25):
                band = await Band.connect(42, transport="serial", port="COM5")
                port = opened[0]
                heard = []
                band.on("battery", heard.append)
                port.from_band(BATTERY * 400)
                time.sleep(0.3)                                     # the program is stuck
                self.assertEqual(len(port.waiting), 0)              # the port is still being emptied
                self.assertEqual(band.transport._waiting, 25)       # 25 frames wait, the rest were dropped
                await asyncio.sleep(0.05)
                self.assertEqual(len(heard), 25)
                self.assertEqual(band.transport._waiting, 0)
                port.from_band(BATTERY * 3)                         # afterwards everything is as before
                await asyncio.sleep(0.05)
                self.assertEqual(len(heard), 28)
                self.assertEqual((await band.get_info()).number, 42)
                await band.disconnect()
        run(main())

    def test_a_port_that_sends_bytes_without_end_is_read_and_forgotten(self):
        async def main():
            opened = []
            with mock.patch.dict(sys.modules, fake_serial(opened)):
                band = await Band.connect(42, transport="serial", port="COM5")
                port = opened[0]
                port.from_band(b"\x7f" * 300000)                    # never a 00
                await asyncio.sleep(0.3)
                self.assertEqual(len(port.waiting), 0)
                port.from_band(b"\x00")
                self.assertEqual((await band.get_info()).number, 42)
                await band.disconnect()
        run(main())


if __name__ == "__main__":
    unittest.main()
