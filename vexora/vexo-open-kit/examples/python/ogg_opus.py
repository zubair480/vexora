# Saves the band's sound as a normal ".ogg" file (Ogg Opus) that any player can open.
#
# The band sends small Opus packets, 50 a second, each holding 20 ms of sound. An Ogg file
# is a row of "pages". Each page has a 27-byte header, a list of packet sizes, the packets,
# and a checksum. The first two pages describe the sound (OpusHead, OpusTags).
#
#     ogg = OggOpusWriter("sound.ogg")
#     band.on("audio", lambda a: ogg.write(a.seq, a.opus))
#     ...
#     ogg.close()
#
# Pure Python, nothing to install. The format is described in RFC 3533 (Ogg) and RFC 7845
# (Opus inside Ogg).
import struct

PRE_SKIP = 312           # samples a player throws away at the start (the usual Opus value)
PACKETS_PER_PAGE = 50    # one page per second of sound
MAX_FILL = 50            # lost packets filled in at most (1 second); a longer hole is not filled
MAX_PACKET = 61440       # the largest sound packet an Ogg Opus file may hold (RFC 7845)


def _crc_table():
    table = []
    for n in range(256):
        r = n << 24
        for _ in range(8):
            r = ((r << 1) ^ 0x04C11DB7 if r & 0x80000000 else r << 1) & 0xFFFFFFFF
        table.append(r)
    return table


CRC_TABLE = _crc_table()


def ogg_crc(data: bytes) -> int:
    """The checksum Ogg uses for each page."""
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ CRC_TABLE[(crc >> 24) ^ byte]
    return crc


def packet_samples(packet: bytes) -> int:
    """How much sound one Opus packet holds, counted in samples at 48000 per second.
    The first byte of the packet says so (20 ms = 960)."""
    config, code = packet[0] >> 3, packet[0] & 3
    if config < 12:
        one = (480, 960, 1920, 2880)[config & 3]
    elif config < 16:
        one = (480, 960)[config & 1]
    else:
        one = 120 << (config & 3)
    frames = (1, 2, 2, packet[1] & 63 if len(packet) > 1 else 0)[code]
    return one * frames


class OggOpusWriter:
    """Writes Opus packets into an Ogg Opus file as they arrive."""

    def __init__(self, path, sample_rate: int = 16000, serial: int = 0x56584F31,
                 packets_per_page: int = PACKETS_PER_PAGE) -> None:
        # `path` is a file name, or anything with a write() method (to send the pages
        # somewhere else as they are made; a short page then means a short delay).
        self.file = path if hasattr(path, "write") else open(path, "wb")
        self.packets_per_page = packets_per_page
        self.serial = serial      # any number; it names this stream inside the file
        self.page_number = 0
        self.granule = 0          # samples written so far (48000 per second)
        self.page = []            # packets waiting for the next page
        self.next_seq = 0         # the band counts packets from 0
        self.frames = 0           # packets written, including the filled-in ones
        self.lost = 0             # packets that never arrived
        self.last_toc = 0xB8      # first byte of a 20 ms packet, used to fill gaps
        head = b"OpusHead" + struct.pack("<BBHIhB", 1, 1, PRE_SKIP, sample_rate, 0, 0)
        tags = b"OpusTags" + struct.pack("<I", 13) + b"vexo-open-kit" + struct.pack("<I", 0)
        self._write_page([head], 0, flags=2)   # 2 = first page of the stream
        self._write_page([tags], 0)            # the two header pages hold no sound

    def write(self, seq: int, packet: bytes) -> None:
        """Add the packet with counter `seq`. If packets are missing before it, the gap is
        filled with empty packets so the timing stays right (a player smooths them over).
        A hole of more than MAX_FILL packets (1 second) is a break in the recording: it is
        counted in `lost` but not filled, so a counter that jumps cannot blow the file up."""
        gap = (seq - self.next_seq) & 0xFFFF      # the counter wraps round at 65535
        if gap > 0x8000 or not packet:
            return                                # an old or empty packet: ignore it
        self.lost += gap
        for _ in range(gap if gap <= MAX_FILL else 0):
            self._add(bytes([self.last_toc & 0xFC]))   # "nothing here": 1 byte, no sound data
        self.last_toc = packet[0]
        self._add(bytes(packet))
        self.next_seq = (seq + 1) & 0xFFFF

    def _add(self, packet: bytes) -> None:
        sizes = sum(len(p) // 255 + 1 for p in self.page) + len(packet) // 255 + 1
        if len(self.page) >= self.packets_per_page or sizes > 255:
            self._write_page(self.page, self.granule)
            self.page = []
        self.page.append(packet)
        self.granule += packet_samples(packet)
        self.frames += 1

    def _write_page(self, packets, granule: int, flags: int = 0) -> None:
        """granule = samples of sound in the file up to the end of this page."""
        # A packet's size is written as bytes that add up to it: 255, 255, ..., rest.
        sizes = b"".join(b"\xff" * (len(p) // 255) + bytes([len(p) % 255]) for p in packets)
        header = struct.pack("<4sBBqIIIB", b"OggS", 0, flags, granule, self.serial,
                             self.page_number, 0, len(sizes))
        page = header + sizes + b"".join(packets)
        page = page[:22] + struct.pack("<I", ogg_crc(page)) + page[26:]
        self.file.write(page)
        self.page_number += 1

    def close(self) -> None:
        """Write the last page (marked "end of stream") and close the file."""
        if self.page is None:
            return
        self._write_page(self.page, self.granule, flags=4)   # 4 = last page
        self.page = None
        if hasattr(self.file, "close"):
            self.file.close()

    @property
    def seconds(self) -> float:
        return self.granule / 48000


def read_packets(path) -> list:
    """The sound packets of an Ogg Opus file, without the two header packets."""
    with open(path, "rb") as f:
        data = f.read()
    packets, part, at = [], bytearray(), 0
    while data[at:at + 4] == b"OggS":
        if at + 27 > len(data) or at + 27 + data[at + 26] > len(data):
            raise ValueError("%s is an Ogg file that is cut off in the middle." % path)
        count = data[at + 26]
        sizes = data[at + 27:at + 27 + count]
        at += 27 + count
        for size in sizes:
            if at + size > len(data):
                raise ValueError("%s is an Ogg file that is cut off in the middle." % path)
            part += data[at:at + size]
            at += size
            if len(packets) >= 2 and len(part) > MAX_PACKET:    # the two header packets may be bigger
                raise ValueError("%s holds a packet of more than %d bytes: that is not Opus sound."
                                 % (path, MAX_PACKET))
            if size < 255:                        # a size below 255 ends the packet
                packets.append(bytes(part))
                part = bytearray()
    if len(packets) < 2 or not packets[0].startswith(b"OpusHead"):
        raise ValueError("%s is not an Ogg Opus file." % path)
    return packets[2:]
