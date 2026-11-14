"""off() refuses a name that is not an event, like on(). And band number 0 ("number not
set") cannot be asked for, except over the USB cable, where an organiser numbers a band.
The same tests as js/test/off-and-band-zero.test.js."""
import asyncio
import os
import subprocess
import sys
import unittest

from vexo_open import Band, VexoError, codec
from vexo_open.sync import Band as SyncBand
from vexo_open.transport import Transport
from vexo_open.transports.sim import SimTransport

ZERO = "Band numbers are 1 to 999. 0 is what a band has before it gets a number: ask an organiser to number it."
KIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BATTERY = codec.encode_stream("battery", dict(percent=50, state=0, millivolts=3900))


def run(coro):
    return asyncio.run(coro)


class Off(unittest.TestCase):
    def test_off_with_a_name_that_is_not_an_event_raises_bad_event_in_the_words_of_on(self):
        async def main():
            band = await Band.connect(42, transport=SimTransport(42, realtime=False))
            plain = SyncBand.connect(42, transport=SimTransport(42, realtime=False))
            try:
                for name in ("tapp", "mic_level", "doubletap", "", None, 7):
                    with self.assertRaises(VexoError) as on:
                        band.on(name, print)
                    self.assertEqual(on.exception.code, "bad-event")
                    for which in (band, plain):
                        with self.assertRaises(VexoError) as off:
                            which.off(name, print)
                        self.assertEqual((off.exception.code, str(off.exception)), ("bad-event", str(on.exception)))
                with self.assertRaisesRegex(VexoError, 'Did you mean "mic-level"\\?'):
                    plain.off("mic_level", print)
            finally:
                plain.disconnect()
                await band.disconnect()
        run(main())

    def test_off_with_a_real_event_still_works_and_a_handler_never_added_is_no_error(self):
        async def main():
            sim = SimTransport(42, realtime=False)
            band = await Band.connect(42, transport=sim)
            seen = []
            band.on("battery", seen.append)
            band.off("battery", print)             # never added: nothing happens
            band.off("tap", seen.append)           # added for another event: nothing happens
            sim.on_frame(BATTERY)
            self.assertEqual(len(seen), 1)
            band.off("battery", seen.append)
            sim.on_frame(BATTERY)
            self.assertEqual(len(seen), 1)
            await band.disconnect()
        run(main())

    def test_plain_off_with_a_real_event_still_works(self):
        with SyncBand.connect(42, transport=SimTransport(42, realtime=False)) as band:
            seen = []
            band.on("battery", seen.append)
            band.off("battery", print)
            band.off("tap", seen.append)
            band.transport.on_frame(BATTERY)
            band.wait(0.05)
            self.assertEqual(len(seen), 1)
            band.off("battery", seen.append)
            band.transport.on_frame(BATTERY)
            band.wait(0.05)
            self.assertEqual(len(seen), 1)


class Watched(Transport):
    """A transport that only notes whether anybody tried to open it."""

    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.opened = 0

    async def open(self):
        self.opened += 1

    async def send(self, frame):
        cmd = codec.decode_command(frame)
        data = {"GET_INFO": dict(proto_major=1, proto_minor=0, fw_major=1, fw_minor=0, fw_patch=0, hw=1, band_no=0,
                                 caps=0x7FF, max_frame=244, uptime_ms=5)}.get(cmd["opcode"])
        self.on_frame(codec.encode_response(cmd["seq"], cmd["opcode"], "OK", data))


class BandZero(unittest.TestCase):
    def refused(self, caught):
        self.assertEqual((caught.exception.code, str(caught.exception)), ("bad-arg", ZERO))

    def test_connect_0_is_refused_over_bluetooth_and_with_the_pretend_band_before_any_connecting(self):
        async def main():
            for number in (0, "0", "00", "000"):
                for options in ({}, {"transport": "ble"}, {"transport": "sim"}):
                    with self.assertRaises(VexoError) as caught:
                        await Band.connect(number, **options)
                    self.refused(caught)
            for kind in ("ble", "sim", "custom"):
                transport = Watched(kind)
                with self.assertRaises(VexoError) as caught:
                    await Band.connect(0, transport=transport)
                self.refused(caught)
                self.assertEqual(transport.opened, 0, kind)
            with self.assertRaises(VexoError) as caught:      # a pretend band made with 0 stays possible for tests
                await Band.connect(0, transport=SimTransport(0, realtime=False))
            self.refused(caught)
        run(main())
        with self.assertRaises(VexoError) as caught:
            SyncBand.connect(0, transport="sim")
        self.refused(caught)

    def test_connect_0_over_the_usb_cable_still_reaches_a_band_that_has_no_number_yet(self):
        async def main():
            transport = Watched("serial")
            band = await Band.connect(0, transport=transport)
            self.assertEqual((transport.opened, band.number, band.name), (1, 0, "Vexo-HACK-00"))
            await band.disconnect()
            # The usual way stays: no number at all, then set_band_number.
            band = await Band.connect(None, transport=SimTransport(0, realtime=False, via="usb"))
            self.assertEqual(band.number, 0)
            self.assertEqual(await band.set_band_number(12), 12)
            self.assertEqual(band.name, "Vexo-HACK-12")
            await band.disconnect()
        run(main())

    def test_other_numbers_are_as_before(self):
        async def main():
            for number in (1, 999):
                band = await Band.connect(number, transport=SimTransport(number, realtime=False))
                self.assertEqual(band.number, number)
                await band.disconnect()
            for number in (1000, -1, 4.2, True):
                with self.assertRaises(VexoError) as caught:
                    await Band.connect(number, transport="sim")
                self.assertEqual(caught.exception.code, "bad-arg")
                self.assertNotEqual(str(caught.exception), ZERO)
        run(main())

    def test_the_examples_command_line_refuses_0_with_the_same_sentence(self):
        # examples/python/kit.py reads "0" and "Vexo-HACK-00" as the number 0; Band.connect refuses it.
        led = os.path.join(KIT, "examples", "python", "led.py")
        for words in (["0", "--sim"], ["0"], ["00", "--sim"], ["Vexo-HACK-00", "--sim"]):
            done = subprocess.run([sys.executable, led] + words, timeout=60, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, universal_newlines=True)
            self.assertEqual(done.returncode, 1, (words, done.stderr))
            self.assertIn("Problem (bad-arg): " + ZERO, done.stderr, words)
            self.assertNotIn("Traceback", done.stderr, words)


if __name__ == "__main__":
    unittest.main()
