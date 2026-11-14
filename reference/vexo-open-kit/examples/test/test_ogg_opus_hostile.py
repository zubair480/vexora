"""The Ogg Opus writer and reader (examples/python/ogg_opus.py) against a band, or a file,
that lies: a packet counter that jumps, and files that are cut off or hold giant packets.
Nothing here may write, loop or allocate without a limit. No packages needed.
"""
import io
import os
import struct
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))
from ogg_opus import MAX_FILL, MAX_PACKET, OggOpusWriter, read_packets  # noqa: E402

VOICE = bytes([0xB8]) + bytes(range(1, 60))   # looks like one 20 ms packet from the band


def page(packets, flags=0, lacing=None):
    """One Ogg page (the checksum is left 0: the reader does not look at it)."""
    sizes = lacing if lacing is not None else b"".join(
        b"\xff" * (len(p) // 255) + bytes([len(p) % 255]) for p in packets)
    return struct.pack("<4sBBqIIIB", b"OggS", 0, flags, 0, 1, 0, 0, len(sizes)) + sizes + b"".join(packets)


HEAD = page([b"OpusHead" + bytes(11)], flags=2) + page([b"OpusTags" + bytes(8)])


def read(data):
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "t.ogg")
        with open(path, "wb") as f:
            f.write(data)
        return read_packets(path)


class JumpingCounter(unittest.TestCase):
    def test_the_longest_gap_that_is_filled_is_one_second(self):
        self.assertEqual(MAX_FILL, 50)
        out = io.BytesIO()
        ogg = OggOpusWriter(out)
        ogg.write(0, VOICE)
        ogg.write(MAX_FILL + 1, VOICE)            # 50 missing: filled
        self.assertEqual((ogg.frames, ogg.lost), (52, 50))
        ogg.write(MAX_FILL + 2 + MAX_FILL + 1, VOICE)   # 51 missing: a break, counted but not filled
        self.assertEqual((ogg.frames, ogg.lost), (53, 101))
        self.assertEqual(ogg.next_seq, 2 * MAX_FILL + 4)
        ogg.write(2 * MAX_FILL + 4, VOICE)        # and the recording simply goes on
        self.assertEqual((ogg.frames, ogg.lost), (54, 101))

    def test_a_counter_that_jumps_on_every_packet_cannot_blow_the_file_up(self):
        # The worst a band can do: every packet 32768 ahead of the one before. Before the
        # limit each of these wrote 32768 filler packets (about 40 kB a packet, 4 MB a second).
        written = []

        class Counting:
            def write(self, data):
                written.append(len(data))

        ogg = OggOpusWriter(Counting())
        seq = 0
        for _ in range(200):                      # 4 seconds of packets
            seq = (seq + 0x8000) & 0xFFFF
            ogg.write(seq, VOICE)
        ogg.close()
        self.assertEqual(ogg.frames, 200)                         # only what really arrived
        self.assertLess(sum(written), 200 * (len(VOICE) + 2) + 1000)
        self.assertEqual(ogg.lost, 0x8000 + 199 * 0x7FFF)         # every hole is still counted

    def test_every_possible_jump_writes_at_most_fifty_fillers(self):
        for jump in (1, 2, 50, 51, 52, 500, 0x7FFF, 0x8000, 0x8001, 0xFFFF):
            ogg = OggOpusWriter(io.BytesIO())
            ogg.write(0, VOICE)
            ogg.write(jump, VOICE)
            self.assertLessEqual(ogg.frames, 2 + MAX_FILL, jump)
            expected = jump - 1 if jump <= 0x8001 else 0          # further back than that = an old packet
            self.assertEqual(ogg.lost, expected, jump)


class HostileFile(unittest.TestCase):
    def test_a_good_file_still_reads(self):
        self.assertEqual(read(HEAD + page([VOICE, VOICE]) + page([VOICE], flags=4)), [VOICE] * 3)

    def test_a_file_cut_off_anywhere_is_an_error_not_a_crash(self):
        whole = HEAD + page([VOICE, VOICE])
        errors = 0
        for cut in range(len(whole)):
            try:
                self.assertEqual(read(whole[:cut]), [], cut)      # cut inside the word "OggS": the page is not seen
            except ValueError:
                errors += 1
        self.assertGreater(errors, len(whole) - 10)

    def test_sizes_that_point_past_the_end(self):
        with self.assertRaises(ValueError):
            read(HEAD + page([b"\xb8"], lacing=b"\xff" * 255))    # says 65025 bytes, has 1

    def test_one_giant_packet_is_refused_quickly(self):
        # 20 MB that claims to be a single packet: refused at 61440 bytes, not glued together.
        body = bytes(255 * 255)
        giant = (page([body], lacing=b"\xff" * 255)) * 320
        started = time.monotonic()
        with self.assertRaises(ValueError) as caught:
            read(HEAD + giant)
        self.assertIn(str(MAX_PACKET), str(caught.exception))
        self.assertLess(time.monotonic() - started, 5)

    def test_big_header_packets_are_allowed(self):
        # The second packet (OpusTags) may hold a picture: bigger than a sound packet may be.
        tags = b"OpusTags" + bytes(100000)
        data = page([b"OpusHead" + bytes(11)], flags=2) + b"".join(
            page([tags[at:at + 255 * 255]], lacing=b"\xff" * 255) for at in range(0, 65025, 65025))
        rest = tags[65025:]
        data += page([rest]) + page([VOICE])
        self.assertEqual(read(data), [VOICE])

    def test_not_an_ogg_file(self):
        for data in (b"", b"RIFF....WAVE", b"OggS", b"OggS" + bytes(22)):
            with self.assertRaises(ValueError):
                read(data)


if __name__ == "__main__":
    unittest.main()
