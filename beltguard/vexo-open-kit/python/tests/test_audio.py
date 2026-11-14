"""Tests for vexo_open.audio: band packets -> plain samples.

    cd python && python3 -m unittest discover -s tests

The decoder needs the optional `av` package; without it those tests are skipped and the
rest (loudness, level, .wav, the message that names the pip command) still run.
"""
import importlib.util
import math
import os
import struct
import tempfile
import unittest
import wave

from vexo_open import VexoError, audio
from vexo_open.sync import Band

HAS_AV = importlib.util.find_spec("av") is not None
HAS_NUMPY = importlib.util.find_spec("numpy") is not None
SILENCE = bytes([0xBB, 0x41, 37]) + bytes(37)       # the packet the pretend band sends


def tone(seconds, level_db=-20.0, hz=440):
    size = 32768 * 10 ** (level_db / 20) * math.sqrt(2)
    count = int(seconds * 16000)
    return struct.pack("<%dh" % count, *(int(size * math.sin(2 * math.pi * hz * n / 16000)) for n in range(count)))


def opus_packets(pcm):
    """Encode samples the way the band does: 16 kHz, mono, 20 ms packets."""
    import av
    encoder = av.CodecContext.create("libopus", "w")
    encoder.sample_rate, encoder.layout, encoder.format = 16000, "mono", "s16"
    encoder.bit_rate = 24000
    encoder.options = {"frame_duration": "20", "application": "voip"}
    packets = []
    for at in range(0, len(pcm) - 639, 640):
        frame = av.AudioFrame(format="s16", layout="mono", samples=320)
        frame.planes[0].update(pcm[at:at + 640])
        frame.sample_rate, frame.pts = 16000, at // 2
        packets += [bytes(p) for p in encoder.encode(frame)]
    return packets + [bytes(p) for p in encoder.encode(None)]


class WithoutAnythingInstalled(unittest.TestCase):
    def test_level(self):
        self.assertAlmostEqual(audio.level_db(tone(1, -20)), -20, delta=0.1)
        self.assertAlmostEqual(audio.level_db(tone(1, -50)), -50, delta=0.3)
        self.assertEqual(audio.level_db(bytes(640)), -100.0)
        self.assertEqual(audio.level_db(b""), -100.0)

    def test_normalise_lifts_a_quiet_voice_and_keeps_the_length(self):
        quiet = tone(0.5, -70) + tone(1, -45) + tone(0.5, -70)
        loud = audio.normalise(quiet)
        self.assertEqual(len(loud), len(quiet))
        self.assertAlmostEqual(audio.level_db(loud[16000:48000]), -20, delta=1.0)
        self.assertAlmostEqual(audio.level_db(loud[:16000]), -45, delta=1.5)    # the same gain everywhere

    def test_normalise_never_clips_and_has_a_limit(self):
        spike = tone(1, -40) + struct.pack("<h", 20000) * 10
        self.assertLessEqual(max(struct.unpack("<%dh" % (len(spike) // 2), audio.normalise(spike))), 32767 * 0.99 + 1)
        self.assertAlmostEqual(audio.level_db(audio.normalise(tone(1, -70), max_gain_db=30)), -40, delta=1.0)
        self.assertEqual(audio.normalise(bytes(640)), bytes(640))
        self.assertEqual(audio.normalise(b""), b"")

    def test_write_wav(self):
        pcm = tone(0.25)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "a.wav")
            audio.write_wav(path, pcm)
            with wave.open(path, "rb") as f:
                self.assertEqual((f.getframerate(), f.getnchannels(), f.getsampwidth()), (16000, 1, 2))
                self.assertEqual(f.readframes(f.getnframes()), pcm)

    @unittest.skipIf(HAS_AV, "av is installed")
    def test_the_decoder_says_what_to_install(self):
        with self.assertRaises(VexoError) as caught:
            audio.PcmDecoder()
        self.assertEqual(caught.exception.code, "not-available")
        self.assertIn("python3 -m pip install av (on Windows: python -m pip install av)", str(caught.exception))

    @unittest.skipIf(HAS_NUMPY, "numpy is installed")
    def test_to_float_says_what_to_install(self):
        with self.assertRaises(VexoError) as caught:
            audio.to_float(bytes(4))
        self.assertIn("python3 -m pip install numpy (on Windows: python -m pip install numpy)", str(caught.exception))

    def test_ogg_packets_reads_the_voice_file_in_the_kit_and_a_pretend_band_plays_it(self):
        # the recording the docs name: a person reading aloud, 20 ms packets
        kit = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(kit, "dashboard", "speech", "sample-speech.opus")
        packets = audio.ogg_packets(path)
        self.assertEqual(len(packets), 796)                          # 15.9 seconds
        self.assertEqual({p[0] >> 3 for p in packets}, {23})         # every packet is 20 ms, mono
        with open(path, "rb") as f:
            self.assertEqual(audio.ogg_packets(f.read()), packets)   # the bytes of the file work too
        heard = []
        with Band.connect(42, transport="sim", audio_packets=packets) as band:
            band.on("audio", heard.append)
            band.configure(audio=True)
            band.wait(0.3)
        self.assertGreaterEqual(len(heard), 8)
        self.assertEqual([a.opus for a in heard], packets[:len(heard)])

    def test_ogg_packets_says_when_a_file_is_not_ogg_opus(self):
        for data in (b"", b"RIFF....WAVE", b"OggS" + bytes(40)):
            with self.assertRaises(VexoError) as caught:
                audio.ogg_packets(data)
            self.assertEqual(caught.exception.code, "bad-arg")
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "notes.ogg")
            with open(path, "wb") as f:
                f.write(b"hello")
            with self.assertRaises(VexoError) as caught:
                audio.ogg_packets(path)
            self.assertIn("notes.ogg is not an Ogg Opus file", str(caught.exception))
        with self.assertRaises(OSError):
            audio.ogg_packets(os.path.join(folder, "missing.ogg"))


@unittest.skipUnless(HAS_AV, "needs: pip install av")
class Decoder(unittest.TestCase):
    def test_every_packet_is_20_ms(self):
        decoder = audio.PcmDecoder()
        sizes = [len(decoder.feed(SILENCE, seq)) for seq in range(50)]
        total = sum(sizes) + len(decoder.flush())
        self.assertEqual(total, 50 * 640)                 # one second of 16 bit samples, exactly
        self.assertEqual(sizes[1:], [640] * 49)           # the very first packet may be a few samples short
        self.assertEqual((decoder.lost, decoder.packets, decoder.seconds), (0, 50, 1.0))

    def test_a_tone_comes_back_as_the_same_tone(self):
        sent = tone(1.0, -20, hz=440)
        pcm = audio.decode_packets(opus_packets(sent))
        self.assertAlmostEqual(len(pcm) / 32000, 1.0, delta=0.05)
        self.assertAlmostEqual(audio.level_db(pcm[6400:-6400]), -20, delta=1.5)
        samples = struct.unpack("<%dh" % (len(pcm) // 2), pcm)[3200:-3200]
        crossings = sum(1 for a, b in zip(samples, samples[1:]) if a < 0 <= b)
        self.assertAlmostEqual(crossings / (len(samples) / 16000), 440, delta=5)

    def test_lost_packets_are_filled_so_the_timing_stays_right(self):
        packets = opus_packets(tone(1.0))[:50]
        decoder = audio.PcmDecoder()
        out = b""
        for seq, packet in enumerate(packets):
            if seq not in (20, 21, 22):
                out += decoder.feed(packet, seq)
        out += decoder.flush()
        self.assertEqual(len(out), 50 * 640)
        self.assertEqual((decoder.lost, decoder.packets), (3, 50))
        gap = out[20 * 640 + 320:21 * 640 + 320]          # the decoder carries the tone on, it is not a hole
        self.assertGreater(audio.level_db(gap), -40)

    def test_without_concealment_a_gap_is_silence(self):
        packets = opus_packets(tone(0.5))
        decoder = audio.PcmDecoder(conceal=False)
        out = decoder.feed(packets[0], 0) + decoder.feed(packets[1], 5)
        self.assertEqual(decoder.lost, 4)
        self.assertEqual(len(out) + len(decoder.flush()), 6 * 640)

    def test_the_counter_wraps_and_late_packets_are_ignored(self):
        decoder = audio.PcmDecoder()
        decoder.feed(SILENCE, 65534)
        decoder.feed(SILENCE, 65535)
        self.assertEqual(len(decoder.feed(SILENCE, 0)), 640)
        self.assertEqual(decoder.lost, 0)
        self.assertEqual(decoder.feed(SILENCE, 65530), b"")          # an old one
        self.assertEqual(len(decoder.feed(SILENCE, 2)), 2 * 640)     # 1 was lost
        self.assertEqual(decoder.lost, 1)

    def test_a_damaged_packet_is_20_ms_of_silence_not_a_crash(self):
        decoder = audio.PcmDecoder()
        decoder.feed(SILENCE, 0)
        self.assertEqual(len(decoder.feed(b"\xff\xff\xff\xff\xff", 1)), 640)
        self.assertEqual(decoder.feed(b"", 2), b"")

    def test_decode_packets_takes_bytes_pairs_and_events(self):
        self.assertEqual(len(audio.decode_packets([SILENCE] * 10)), 10 * 640)
        self.assertEqual(len(audio.decode_packets([(0, SILENCE), (3, SILENCE)])), 4 * 640)

    def test_with_the_pretend_band(self):
        band = Band.connect(42, transport="sim")
        decoder = audio.PcmDecoder()
        chunks = []
        band.on("audio", lambda a: chunks.append(decoder.feed(a)))
        band.configure(audio=True)
        band.wait(0.5)
        band.configure(audio=False)
        band.disconnect()
        self.assertGreaterEqual(len(chunks), 15)
        self.assertEqual(decoder.lost, 0)
        self.assertLess(audio.level_db(b"".join(chunks)), -80)       # the pretend band sends silence

    @unittest.skipUnless(HAS_NUMPY, "needs: pip install numpy")
    def test_to_float(self):
        values = audio.to_float(struct.pack("<3h", 0, 16384, -32768))
        self.assertEqual(values.tolist(), [0.0, 0.5, -1.0])
        self.assertEqual(str(values.dtype), "float32")


if __name__ == "__main__":
    unittest.main()
