"""Checks the Ogg Opus writer (examples/python/ogg_opus.py) byte by byte. No packages needed.

The pages are taken apart here with separate code, and the checksum is worked out a second
way (bit by bit, straight from the Ogg description) so the writer is not checking itself.
"""
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))
from ogg_opus import PRE_SKIP, OggOpusWriter, ogg_crc, packet_samples, read_packets  # noqa: E402

VOICE = bytes([0xB8]) + bytes(range(1, 60))   # looks like one 20 ms packet from the band


def slow_crc(data: bytes) -> int:
    """CRC-32 with polynomial 0x04C11DB7, start value 0, no bit reversal, no final XOR."""
    crc = 0
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = (crc << 1) ^ 0x04C11DB7 if crc & 0x80000000 else crc << 1
        crc &= 0xFFFFFFFF
    return crc


def pages(data: bytes) -> list:
    """Split a file into pages: dicts with flags, granule, serial, number, packets, crc_ok."""
    found, at = [], 0
    while at < len(data):
        magic, version, flags, granule, serial, number, crc, count = struct.unpack_from("<4sBBqIIIB", data, at)
        assert magic == b"OggS" and version == 0, "bad page start at byte %d" % at
        sizes = data[at + 27:at + 27 + count]
        end = at + 27 + count + sum(sizes)
        assert end <= len(data), "page runs past the end of the file"
        whole = data[at:end]
        body, packets, part = at + 27 + count, [], b""
        for size in sizes:
            part += data[body:body + size]
            body += size
            if size < 255:
                packets.append(part)
                part = b""
        assert part == b"", "a packet continues on the next page (the writer never does that)"
        found.append(dict(flags=flags, granule=granule, serial=serial, number=number, packets=packets,
                          sizes=list(sizes), crc_ok=slow_crc(whole[:22] + bytes(4) + whole[26:]) == crc))
        at = end
    return found


def record(steps, **options):
    """Write (seq, packet) pairs to a file; returns (writer, file bytes, packets read back)."""
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "t.ogg")
        ogg = OggOpusWriter(path, **options)
        for seq, packet in steps:
            ogg.write(seq, packet)
        ogg.close()
        ogg.close()   # closing twice is harmless
        with open(path, "rb") as f:
            return ogg, f.read(), read_packets(path)


class Checksum(unittest.TestCase):
    def test_known_values(self):
        self.assertEqual(ogg_crc(b""), 0)
        self.assertEqual(ogg_crc(b"\x01"), 0x04C11DB7)
        self.assertEqual(ogg_crc(b"123456789"), 0x89A1897F)   # published check value of "CRC-32/MPEG-2 with start 0"

    def test_fast_and_slow_agree(self):
        data = bytes((i * 37 + 11) % 256 for i in range(5000))
        for length in (0, 1, 2, 27, 255, 256, 5000):
            self.assertEqual(ogg_crc(data[:length]), slow_crc(data[:length]))


class PacketLength(unittest.TestCase):
    def test_the_first_byte_gives_the_duration(self):
        self.assertEqual(packet_samples(b"\xb8" + bytes(40)), 960)          # the band: 20 ms
        self.assertEqual(packet_samples(bytes([0xBB, 0x41, 3, 0, 0, 0])), 960)  # the pretend band's silence
        self.assertEqual(packet_samples(b"\xb0\x00"), 480)                  # 10 ms
        self.assertEqual(packet_samples(b"\x80\x00"), 120)                  # 2.5 ms
        self.assertEqual(packet_samples(b"\x18\x00"), 2880)                 # 60 ms
        self.assertEqual(packet_samples(b"\x60\x00"), 480)                  # hybrid 10 ms
        self.assertEqual(packet_samples(b"\xb9\x00\x00"), 1920)             # two 20 ms frames
        self.assertEqual(packet_samples(bytes([0xBB, 3, 0])), 2880)         # three 20 ms frames
        self.assertEqual(packet_samples(b"\xb8"), 960)                      # the 1-byte gap filler


class Writer(unittest.TestCase):
    def test_file_layout(self):
        sent = [(i, VOICE[:20 + i % 30]) for i in range(120)]
        ogg, data, back = record(sent)
        ps = pages(data)
        self.assertTrue(all(p["crc_ok"] for p in ps))
        self.assertEqual([p["number"] for p in ps], list(range(len(ps))))
        self.assertEqual(len({p["serial"] for p in ps}), 1)
        # first page: only the description, flagged "start"
        self.assertEqual([p["flags"] for p in ps], [2, 0, 0, 0, 4])
        head = ps[0]["packets"]
        self.assertEqual(len(head), 1)
        self.assertEqual(head[0][:8], b"OpusHead")
        version, channels, pre_skip, rate, gain, mapping = struct.unpack("<BBHIhB", head[0][8:])
        self.assertEqual((version, channels, pre_skip, rate, gain, mapping), (1, 1, PRE_SKIP, 16000, 0, 0))
        self.assertEqual(len(head[0]), 19)
        tags = ps[1]["packets"]
        self.assertEqual(len(tags), 1)
        self.assertEqual(tags[0][:8], b"OpusTags")
        vendor_len, = struct.unpack_from("<I", tags[0], 8)
        self.assertEqual(struct.unpack_from("<I", tags[0], 12 + vendor_len), (0,))   # no comments
        self.assertEqual(len(tags[0]), 16 + vendor_len)
        self.assertEqual((ps[0]["granule"], ps[1]["granule"]), (0, 0))
        # sound pages: 50 packets (one second) each, positions count 48000 a second
        self.assertEqual([len(p["packets"]) for p in ps[2:]], [50, 50, 20])
        self.assertEqual([p["granule"] for p in ps[2:]], [48000, 96000, 115200])
        self.assertEqual([p for page in ps[2:] for p in page["packets"]], [p for _, p in sent])
        self.assertEqual(back, [p for _, p in sent])
        self.assertEqual((ogg.frames, ogg.lost), (120, 0))
        self.assertAlmostEqual(ogg.seconds, 2.4)

    def test_gaps_are_filled_so_timing_stays_right(self):
        ogg, data, back = record([(0, VOICE), (1, VOICE), (4, VOICE), (5, VOICE)])
        self.assertEqual(back, [VOICE, VOICE, b"\xb8", b"\xb8", VOICE, VOICE])
        self.assertEqual((ogg.frames, ogg.lost), (6, 2))
        self.assertEqual(pages(data)[-1]["granule"], 6 * 960)

    def test_filler_copies_the_kind_of_the_packet_before_it(self):
        silence = bytes([0xBB, 0x41, 2, 0, 0])            # what the pretend band sends
        _, _, back = record([(0, silence), (2, silence)])
        self.assertEqual(back[1], b"\xb8")                # same 20 ms kind, one empty frame

    def test_packets_lost_at_the_very_start(self):
        ogg, data, back = record([(3, VOICE)])
        self.assertEqual(back, [b"\xb8"] * 3 + [VOICE])
        self.assertEqual(ogg.lost, 3)
        self.assertEqual(pages(data)[-1]["granule"], 4 * 960)

    def test_counter_wraps_at_65535(self):
        ogg = OggOpusWriter(os.devnull)
        ogg.next_seq = 65534
        for seq in (65534, 65535, 0, 2):
            ogg.write(seq, VOICE)
        self.assertEqual((ogg.frames, ogg.lost, ogg.next_seq), (5, 1, 3))
        ogg.close()

    def test_old_and_empty_packets_are_ignored(self):
        ogg, _, back = record([(0, VOICE), (1, VOICE), (0, b"\xb8late"), (1, b"\xb8late"), (2, b""), (2, VOICE)])
        self.assertEqual(back, [VOICE] * 3)
        self.assertEqual(ogg.lost, 0)

    def test_long_gap_spans_pages(self):
        # 50 packets (one second) is the longest gap that is filled
        ogg, data, back = record([(0, VOICE), (51, VOICE), (102, VOICE), (153, VOICE)])
        ps = pages(data)
        self.assertTrue(all(p["crc_ok"] for p in ps))
        self.assertEqual(len(back), 154)
        self.assertEqual(ogg.lost, 150)
        self.assertEqual(ps[-1]["granule"], 154 * 960)
        self.assertEqual([p["granule"] for p in ps[2:5]], [48000, 96000, 144000])
        self.assertEqual(ps[-1]["flags"], 4)

    def test_big_packets_use_several_size_bytes(self):
        big = [b"\xb8" + bytes([n % 251]) * (n - 1) for n in (254, 255, 256, 510, 600)]
        _, data, back = record(list(enumerate(big)))
        ps = pages(data)
        self.assertTrue(all(p["crc_ok"] for p in ps))
        self.assertEqual(back, big)
        self.assertEqual(ps[2]["sizes"], [254, 255, 0, 255, 1, 255, 255, 0, 255, 255, 90])

    def test_a_page_never_has_more_than_255_size_bytes(self):
        big = b"\xb8" + bytes(1500)                       # 6 size bytes each
        _, data, back = record([(i, big) for i in range(100)])
        ps = pages(data)
        self.assertTrue(all(p["crc_ok"] and len(p["sizes"]) <= 255 for p in ps))
        self.assertEqual(len(back), 100)
        self.assertEqual([len(p["packets"]) for p in ps[2:]], [42, 42, 16])
        self.assertEqual([p["granule"] for p in ps[2:]], [42 * 960, 84 * 960, 100 * 960])

    def test_exactly_one_full_page_still_ends_properly(self):
        _, data, _ = record([(i, VOICE) for i in range(50)])
        ps = pages(data)
        self.assertEqual([p["flags"] for p in ps], [2, 0, 4])
        self.assertEqual(len(ps[2]["packets"]), 50)

    def test_nothing_recorded_is_still_a_valid_file(self):
        ogg, data, back = record([])
        ps = pages(data)
        self.assertEqual([p["flags"] for p in ps], [2, 0, 4])
        self.assertTrue(all(p["crc_ok"] for p in ps))
        self.assertEqual((back, ogg.seconds), ([], 0))

    def test_one_damaged_byte_is_noticed(self):
        _, data, _ = record([(0, VOICE)])
        broken = bytearray(data)
        broken[-1] ^= 1
        self.assertFalse(pages(bytes(broken))[-1]["crc_ok"])

    def test_reader_refuses_other_files(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "x.ogg")
            with open(path, "wb") as f:
                f.write(b"not an ogg file")
            with self.assertRaises(ValueError):
                read_packets(path)


if __name__ == "__main__":
    unittest.main()
