"""Turn the band's microphone packets into ordinary sound samples (PCM).

The band sends Opus packets: 50 a second, each holding 20 ms of sound. Most sound tools
want plain samples instead: 16000 numbers a second, one channel, each a 16 bit integer.

    from vexo_open.audio import PcmDecoder

    decoder = PcmDecoder()
    band.on("audio", lambda a: use(decoder.feed(a)))    # bytes: 320 samples for each packet
    band.configure(audio=True)

A whole recording at once:  pcm = decode_packets(packets)

Decoding Opus needs one extra package (it brings its own Opus library; works the same on
macOS, Windows and Linux):

    python3 -m pip install av          (on Windows: python -m pip install av)

The first PcmDecoder() of a program takes a few seconds (up to about 7 on a slow laptop):
that is Python loading the `av` package. Make it before you turn the microphone on.

Everything else in this file (loudness, making quiet sound louder, saving a .wav file,
reading the packets of an .ogg file) needs nothing installed. With numpy installed,
to_float() gives the samples as an array.
"""
import array
import math
import struct
import sys

from .transport import VexoError

SAMPLE_RATE = 16000        # samples a second
FRAME_SAMPLES = 320        # samples in one band packet (20 ms)
MAX_FILL = 50              # lost packets filled in at most (1 second); a longer hole is a break
# Filling has a budget too, so that a faulty band whose counter jumps with every packet
# cannot make the computer produce 50 times the sound it sent: the budget starts at
# MAX_FILL, every packet that arrives adds 1 (up to MAX_FILL), every filled packet takes 1.
# So never more is filled in than arrived, plus one second. A link that loses up to half
# of its packets is still filled completely.
MAX_OGG_PACKET = 61440     # the largest sound packet an Ogg Opus file may hold (RFC 7845)
INSTALL = "python3 -m pip install av (on Windows: python -m pip install av)"

__all__ = ["PcmDecoder", "decode_packets", "to_float", "level_db", "normalise", "write_wav", "ogg_packets",
           "SAMPLE_RATE", "FRAME_SAMPLES"]


def _load_av():
    try:
        import av
    except ImportError:
        raise VexoError("not-available",
                        "Decoding the band's sound needs the 'av' package. Install it once with:  "
                        + INSTALL) from None
    return av


class PcmDecoder:
    """Opus packets from the band in, 16 kHz mono 16 bit samples out (as bytes, little endian).

    feed() takes an `audio` event, or a packet and its `seq` counter. When packets were lost
    on the way (a jump in `seq`), the gap is filled with sound the decoder makes up from what
    came before, so one packet is always 20 ms and the timing stays right. `lost` counts them.
    A hole of more than MAX_FILL packets (1 second) is a break, not a gap: it is counted in
    `lost` but not filled (the same rule as SeqTracker in the JavaScript library).
    """

    _budget = MAX_FILL  # lost packets that may still be filled in (see MAX_FILL)

    def __init__(self, conceal: bool = True) -> None:
        av = _load_av()
        self._av = av
        # "libopus" is the reference decoder and smooths over lost packets best.
        name = "libopus" if "libopus" in av.codecs_available else "opus"
        self._codec = av.CodecContext.create(name, "r")
        # The same 19 bytes that start an .ogg file: version 1, 1 channel.
        self._codec.extradata = b"OpusHead" + struct.pack("<BBHIhB", 1, 1, 0, SAMPLE_RATE, 0, 0)
        self._resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        self.conceal = conceal
        self.lost = 0             # packets that never arrived
        self.packets = 0          # packets decoded, the filled-in ones included
        self.samples = 0          # samples given out so far
        self._next_seq = None
        self._toc = 0xB8          # first byte of a 20 ms packet, used to fill gaps

    def feed(self, packet, seq=None) -> bytes:
        """One packet in, its samples out. `packet` is an `audio` event or the Opus bytes."""
        if hasattr(packet, "opus"):
            packet, seq = packet.opus, getattr(packet, "seq", seq)
        packet = bytes(packet)
        if not packet:
            return b""
        out = b""
        if seq is not None:
            gap = 0 if self._next_seq is None else (seq - self._next_seq) & 0xFFFF
            if gap > 0x8000:
                return b""                        # an old packet that arrived late: ignore it
            fill = min(gap, self._budget) if gap <= MAX_FILL else 0    # a longer hole is not filled
            for _ in range(fill):
                out += self._fill()
            self._budget = min(MAX_FILL, self._budget - fill + 1)
            self.lost += gap
            self._next_seq = (seq + 1) & 0xFFFF   # the counter wraps round at 65535
        self._toc = packet[0]
        return out + self._decode(packet)

    def _fill(self) -> bytes:
        """20 ms for a packet that never came."""
        if self.conceal:
            # A packet with only its first byte means "nothing here": the decoder then
            # continues the sound it had, fading out.
            return self._decode(bytes([self._toc & 0xFC]))
        self.packets += 1
        self.samples += FRAME_SAMPLES
        return bytes(2 * FRAME_SAMPLES)

    def _decode(self, packet: bytes) -> bytes:
        self.packets += 1
        try:
            frames = self._codec.decode(self._av.Packet(packet))
            pcm = b"".join(self._bytes(r) for f in frames for r in self._resampler.resample(f))
        except Exception:                         # a damaged packet: 20 ms of silence instead
            pcm = bytes(2 * FRAME_SAMPLES)
        self.samples += len(pcm) // 2
        return pcm

    @staticmethod
    def _bytes(frame) -> bytes:
        return bytes(frame.planes[0])[:2 * frame.samples]

    def flush(self) -> bytes:
        """The last few samples still inside the decoder. Call it once, after the last packet."""
        try:
            pcm = b"".join(self._bytes(r) for r in self._resampler.resample(None))
        except Exception:
            pcm = b""
        self.samples += len(pcm) // 2
        return pcm

    @property
    def seconds(self) -> float:
        return self.samples / SAMPLE_RATE


def decode_packets(packets, conceal: bool = True) -> bytes:
    """A whole recording -> samples. `packets` is a list of `audio` events, of Opus packets
    (bytes), or of (seq, packet) pairs."""
    decoder = PcmDecoder(conceal)
    out = []
    for item in packets:
        if isinstance(item, tuple):
            out.append(decoder.feed(item[1], item[0]))
        else:
            out.append(decoder.feed(item))
    out.append(decoder.flush())
    return b"".join(out)


def _samples(pcm):
    a = array.array("h")
    a.frombytes(bytes(pcm)[:len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        a.byteswap()
    return a


def to_float(pcm):
    """Samples as a numpy array of numbers from -1.0 to 1.0 (what most speech models want)."""
    try:
        import numpy
    except ImportError:
        raise VexoError("not-available", "to_float() needs numpy. Install it once with:  "
                                         "python3 -m pip install numpy (on Windows: python -m pip install numpy)") from None
    return numpy.frombuffer(bytes(pcm)[:len(pcm) // 2 * 2], dtype="<i2").astype(numpy.float32) / 32768.0


def level_db(pcm) -> float:
    """How loud the samples are: dB below full scale (0 = as loud as possible, -100 = silence).
    For a band at its default gain a quiet room is about -50 and speech nearby about -30."""
    a = _samples(pcm)
    if not a:
        return -100.0
    mean = sum(x * x for x in a) / len(a)
    return max(-100.0, 10 * math.log10(mean / (32768.0 * 32768.0))) if mean else -100.0


def normalise(pcm, target_db: float = -20.0, max_gain_db: float = 40.0) -> bytes:
    """Make a recording louder (or quieter) so that its loud parts sit near `target_db`.
    A voice far from the band is very quiet; speech models do better after this. The gain is
    the same for the whole recording, it never goes above `max_gain_db`, and nothing clips."""
    a = _samples(pcm)
    if not a:
        return b""
    # Loudness of each 20 ms; the 95th percentile is "the loud parts" (the speech, not the pauses).
    powers = sorted(sum(x * x for x in a[i:i + FRAME_SAMPLES]) / FRAME_SAMPLES
                    for i in range(0, max(1, len(a) - FRAME_SAMPLES + 1), FRAME_SAMPLES))
    loud = math.sqrt(powers[min(len(powers) - 1, int(0.95 * len(powers)))]) / 32768.0
    peak = max(max(a), -min(a)) / 32768.0
    if loud <= 0 or peak <= 0:
        return bytes(pcm)
    gain = min(10 ** (target_db / 20) / loud, 10 ** (max_gain_db / 20), 0.99 / peak)
    out = array.array("h", (int(x * gain) for x in a))
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()


def write_wav(path, pcm) -> None:
    """Save samples as a normal .wav file (16 kHz, mono, 16 bit)."""
    import wave
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(bytes(pcm))


def ogg_packets(source) -> list:
    """The Opus packets of an Ogg Opus file (.ogg or .opus), without its two header packets.
    `source` is the file's path, or the bytes of the whole file. The list is what a pretend
    band takes as its voice: Band.connect(42, transport="sim", audio_packets=ogg_packets(path)).
    Needs nothing installed. (JavaScript: oggPackets(bytes).)"""
    if isinstance(source, (bytes, bytearray, memoryview)):
        data, name = bytes(source), "This"
    else:
        with open(source, "rb") as f:
            data = f.read()
        name = str(source)
    # An Ogg file is a row of pages: a 27 byte header, a list of sizes, then the packets.
    packets, part, at = [], bytearray(), 0
    while data[at:at + 4] == b"OggS" and at + 27 <= len(data):
        count = data[at + 26]
        sizes = data[at + 27:at + 27 + count]
        at += 27 + count
        for size in sizes:
            if at + size > len(data):
                raise VexoError("bad-arg", "%s is an Ogg file that is cut off in the middle." % name)
            part += data[at:at + size]
            at += size
            if len(packets) >= 2 and len(part) > MAX_OGG_PACKET:    # the two header packets may be bigger
                raise VexoError("bad-arg", "%s holds a packet of more than %d bytes: that is not Opus sound."
                                % (name, MAX_OGG_PACKET))
            if size < 255:                        # a size below 255 ends the packet
                packets.append(bytes(part))
                part = bytearray()
    if len(packets) < 2 or not packets[0].startswith(b"OpusHead"):
        raise VexoError("bad-arg", "%s is not an Ogg Opus file (a .ogg or .opus file with Opus sound in it)." % name)
    return packets[2:]
