"""The Bluetooth and USB transports, with pretend 'bleak' and 'serial' packages.

The real packages are optional and may not be installed, so these tests put small fakes
into sys.modules and check what the transports ask of them.
"""
import asyncio
import queue
import sys
import types
import unittest
from unittest import mock

from vexo_open import Band, VexoError, codec
from vexo_open.transports import serial as serial_transport
from vexo_open.transports.ble import BleTransport
from vexo_open.transports.serial import SerialTransport

INFO = dict(proto_major=1, proto_minor=0, fw_major=1, fw_minor=0, fw_patch=0, hw=1, band_no=42,
            caps=0x07FF, max_frame=244, uptime_ms=5)


def answer(frame: bytes) -> bytes:
    """What a tiny pretend band replies to one command frame."""
    cmd = codec.decode_command(frame)
    data = INFO if cmd["opcode"] == "GET_INFO" else dict(name="Vexo-HACK-42") if cmd["opcode"] == "GET_NAME" else {}
    return codec.encode_response(cmd["seq"], cmd["opcode"], "OK", data)


def run(coro):
    return asyncio.run(coro)


def _make_ble(number):
    from vexo_open.transports.ble import BleTransport
    return BleTransport(number)


# ---- pretend bleak ----

class Advert:
    def __init__(self, local_name, service_uuids, maker=None):
        self.local_name, self.service_uuids = local_name, service_uuids
        self.manufacturer_data = {} if maker is None else {0xFFFF: maker}


class Device:
    """What bleak passes next to the advertisement; name is what the computer remembers."""
    def __init__(self, label, name=None):
        self.label, self.name = label, name

    def __eq__(self, other):
        return other == self.label


def fake_bleak(adverts, log):
    class BleakScanner:
        @classmethod
        async def find_device_by_filter(cls, filterfunc, timeout=10.0, **kwargs):
            log.append(("scan", timeout, kwargs))
            for device, advert in adverts:
                if filterfunc(device, advert):
                    return device
            return None

    class BleakClient:
        last = None

        def __init__(self, device, disconnected_callback=None, **kwargs):
            self.device, self.disconnected_callback, self.notify = device, disconnected_callback, None
            BleakClient.last = self

        async def connect(self):
            log.append(("connect", self.device))

        async def start_notify(self, uuid, callback):
            log.append(("start_notify", uuid))
            self.notify = callback

        async def write_gatt_char(self, uuid, data, response=False):
            log.append(("write", uuid, bytes(data), response))
            self.notify(uuid, bytearray(answer(bytes(data))))

        async def disconnect(self):
            log.append(("disconnect",))

    module = types.ModuleType("bleak")
    module.BleakScanner, module.BleakClient = BleakScanner, BleakClient
    return module


class BleTest(unittest.TestCase):
    def connect(self, adverts, number=42):
        log = []
        bleak = fake_bleak(adverts, log)
        with mock.patch.dict(sys.modules, {"bleak": bleak}):
            return run(self._session(number, log, bleak))

    async def _session(self, number, log, bleak):
        band = await Band.connect(number, transport="ble", scan_seconds=1.5)
        await band.set_led("#00ff00")
        return band, log, bleak.BleakClient.last

    def test_scan_filter_needs_name_and_service(self):
        uuid = codec.SERVICE_UUID
        adverts = [
            ("same name, no service", Advert("Vexo-HACK-42", [])),
            ("same name, other service", Advert("Vexo-HACK-42", ["0000180a-0000-1000-8000-00805f9b34fb"])),
            ("service, other band", Advert("Vexo-HACK-43", [uuid])),
            ("service, no name", Advert(None, [uuid])),
            ("the real one", Advert("Vexo-HACK-42", [uuid.upper()])),
        ]
        band, log, client = self.connect(adverts)
        self.assertEqual(client.device, "the real one")
        self.assertEqual(log[0], ("scan", 1.5, {"service_uuids": [uuid]}))
        self.assertEqual((band.name, band.transport_kind), ("Vexo-HACK-42", "ble"))

        log = []
        with mock.patch.dict(sys.modules, {"bleak": fake_bleak(adverts[:4], log)}):
            with self.assertRaises(VexoError) as caught:
                run(Band.connect(42, transport="ble"))
        self.assertEqual(caught.exception.code, "not-found-nearby")
        self.assertIn("double-tap", str(caught.exception))
        self.assertIn("something else is connected to it", str(caught.exception))
        self.assertIn("Vexo-HACK-42", str(caught.exception))
        self.assertEqual([entry[0] for entry in log], ["scan"])  # never connected to a look-alike

    def test_scan_when_the_name_arrives_late_or_never(self):
        """The name travels in the scan response; the first packet has the service and the
        band number as maker data. Either is enough, but only together with the service."""
        uuid = codec.SERVICE_UUID
        mine, other = bytes([0x4F, 1, 42, 0]), bytes([0x4F, 1, 43, 0])
        band300 = bytes([0x4F, 1]) + (300).to_bytes(2, "little")
        refused = [
            ("first packet of another band", Advert(None, [uuid], other)),
            ("our number, no service", Advert(None, [], mine)),
            ("our number, other maker tag", Advert(None, [uuid], bytes([0x4E, 1, 42, 0]))),
            ("maker data too short", Advert(None, [uuid], bytes([0x4F, 1, 42]))),
            ("band 298, low byte 42", Advert(None, [uuid], bytes([0x4F, 1, 42, 1]))),
            ("name says another band, number says ours", Advert("Vexo-HACK-43", [uuid], mine)),
            (Device("remembered name, no service", "Vexo-HACK-42"), Advert(None, [])),
            ("prefix only", Advert("Vexo-HACK-4", [uuid])),
            ("three digit look-alike", Advert("Vexo-HACK-420", [uuid])),
        ]
        for accepted in [
            ("first packet only", Advert(None, [uuid], mine)),
            ("both packets merged", Advert("Vexo-HACK-42", [uuid], mine)),
            ("name only, in the scan response", Advert("Vexo-HACK-42", [uuid])),
            (Device("remembered name with the service", "Vexo-HACK-42"), Advert(None, [uuid])),
        ]:
            band, log, client = self.connect(refused + [accepted])
            self.assertEqual(client.device, accepted[0])
        log = []
        with mock.patch.dict(sys.modules, {"bleak": fake_bleak(refused, log)}):
            with self.assertRaises(VexoError) as caught:
                run(Band.connect(42, transport="ble"))
        self.assertEqual(caught.exception.code, "not-found-nearby")
        # Band numbers of 100 and more use three digits and a two-byte number.
        t = _make_ble(300)
        self.assertEqual((t.name, t.number), ("Vexo-HACK-300", 300))
        self.assertTrue(t._is_our_band(Device("d"), Advert(None, [uuid], band300)))
        self.assertTrue(t._is_our_band(Device("d"), Advert("Vexo-HACK-300", [uuid])))
        self.assertFalse(t._is_our_band(Device("d"), Advert("Vexo-HACK-30", [uuid])))

    def test_subscribe_before_first_command_and_write_with_response(self):
        band, log, client = self.connect([("dev", Advert("Vexo-HACK-42", [codec.SERVICE_UUID]))])
        self.assertEqual([entry[0] for entry in log], ["scan", "connect", "start_notify", "write", "write"])
        self.assertEqual(log[2], ("start_notify", codec.TX_UUID))
        writes = [entry for entry in log if entry[0] == "write"]
        for _, uuid, data, response in writes:
            self.assertEqual(uuid, codec.RX_UUID)
            self.assertIs(response, True)
        self.assertEqual(codec.decode_command(writes[0][2])["opcode"], "GET_INFO")  # plain frame, no COBS
        self.assertEqual(writes[1][2].hex(), "01015000ff00ff01e803")

    def test_lost_link_and_disconnect(self):
        async def main():
            band = await Band.connect(42, transport="ble")
            reasons = []
            band.on("disconnect", lambda e: reasons.append(e.reason))
            client = sys.modules["bleak"].BleakClient.last
            client.disconnected_callback(client)
            self.assertEqual(reasons, ["lost"])
            band2 = await Band.connect(42, transport="ble")
            await band2.disconnect()
            await band2.disconnect()
            with self.assertRaises(VexoError) as caught:
                await band2.transport.send(b"\x01\x00\x01")
            self.assertEqual(caught.exception.code, "not-connected")
        log = []
        with mock.patch.dict(sys.modules, {"bleak": fake_bleak([("dev", Advert("Vexo-HACK-42", [codec.SERVICE_UUID]))], log)}):
            run(main())
        self.assertEqual(log[-1], ("disconnect",))

    def test_reconnect_opens_again_and_leaves_nothing_behind(self):
        async def main():
            band = await Band.connect(42, transport="ble", auto_reconnect=True, reconnect_seconds=2)
            band._reconnect_pause = 0.01
            events = []
            for name in ("reconnecting", "reconnected", "disconnect"):
                band.on(name, lambda e, name=name: events.append(name))
            await band.set_led("#00ff00")
            first = sys.modules["bleak"].BleakClient.last
            del log[:]
            first.disconnected_callback(first)  # the link drops
            self.assertIsNone(band.transport._client)  # the dead connection is let go of
            for _ in range(200):
                if len(events) == 2:
                    break
                await asyncio.sleep(0.005)
            self.assertEqual(events, ["reconnecting", "reconnected"])
            second = sys.modules["bleak"].BleakClient.last
            self.assertIsNot(second, first)
            self.assertIs(band.transport._client, second)
            # scanned and subscribed again, then asked who it is and set the light again
            self.assertEqual([entry[0] for entry in log], ["scan", "connect", "start_notify", "write", "write"])
            self.assertEqual([codec.decode_command(entry[2])["opcode"] for entry in log[3:]], ["GET_INFO", "LED_SET"])
            first.disconnected_callback(first)  # a late report from the old connection changes nothing
            self.assertEqual((events, band.connected), (["reconnecting", "reconnected"], True))

            # open() on a transport that is still open closes the old connection first.
            del log[:]
            await band.transport.open()
            self.assertEqual([entry[0] for entry in log], ["disconnect", "scan", "connect", "start_notify"])
            self.assertEqual(events, ["reconnecting", "reconnected"])  # closing it ourselves is not "lost"
            await band.disconnect()
            self.assertEqual(events, ["reconnecting", "reconnected", "disconnect"])

            # A connection that fails half way is closed again.
            async def broken(self, uuid, callback):
                raise OSError("no such service")
            sys.modules["bleak"].BleakClient.start_notify = broken
            del log[:]
            with self.assertRaises(VexoError) as caught:
                await Band.connect(42, transport="ble")
            self.assertEqual(caught.exception.code, "not-found-nearby")
            self.assertEqual([entry[0] for entry in log], ["scan", "connect", "disconnect"])
        log = []
        with mock.patch.dict(sys.modules, {"bleak": fake_bleak([("dev", Advert("Vexo-HACK-42", [codec.SERVICE_UUID]))], log)}):
            run(main())

    def test_wrong_band_closes_the_link(self):
        log = []
        with mock.patch.dict(sys.modules, {"bleak": fake_bleak([("dev", Advert("Vexo-HACK-07", [codec.SERVICE_UUID]))], log)}):
            with self.assertRaises(VexoError) as caught:
                run(Band.connect(7, transport="ble"))  # it advertises Vexo-HACK-07 but says it is 42
        self.assertEqual(caught.exception.code, "wrong-band")
        self.assertEqual(log[-1], ("disconnect",))

    def test_number_none_is_refused(self):
        with self.assertRaises(VexoError) as caught:
            run(Band.connect(None, transport="ble"))
        self.assertEqual(caught.exception.code, "bad-arg")
        self.assertIn("USB", str(caught.exception))
        with self.assertRaises(VexoError):
            BleTransport(None)

    def test_missing_package(self):
        with mock.patch.dict(sys.modules, {"bleak": None}):
            with self.assertRaises(VexoError) as caught:
                run(Band.connect(42, transport="ble"))
        self.assertEqual(caught.exception.code, "no-bleak")
        self.assertIn("Run: python3 -m pip install bleak (on Windows: python -m pip install bleak)",
                      str(caught.exception))


# ---- pretend pyserial ----

class Port:
    def __init__(self, device, product=None, manufacturer=None, vid=None, pid=None):
        self.device, self.product, self.manufacturer, self.vid, self.pid = device, product, manufacturer, vid, pid


def fake_serial(ports, opened, noise=b""):
    class SerialException(Exception):
        pass

    class Serial:
        # Like pyserial: Serial(None, ...) is not open yet; open() raises DTR (unless it
        # was set to False first) as part of opening; setting dtr on an open port writes
        # the control lines again.
        def __init__(self, port=None, baudrate=9600, timeout=None, rtscts=False, dsrdtr=False):
            self.port, self.timeout, self.rtscts, self.dsrdtr = port, timeout, rtscts, dsrdtr
            self.log = []       # what the transport did, in order
            self.written = []
            self.incoming = queue.Queue()
            self.band_side = codec.Deframer()  # like the band: skips up to the first 00
            self.is_open = False
            self._dtr = True
            if port is not None:
                self.open()

        def open(self):
            if self.port == "BROKEN":
                raise SerialException("no such port")
            self.is_open = True
            self.log.append(("open", {"dtr": self._dtr, "rtscts": self.rtscts, "dsrdtr": self.dsrdtr}))
            opened.append(self)
            if noise:
                self.incoming.put(noise)  # stale bytes from before we opened

        @property
        def dtr(self):
            return self._dtr

        @dtr.setter
        def dtr(self, value):
            self._dtr = value
            if self.is_open:
                self.log.append(("dtr", value))  # a control-line write the band sees

        @property
        def in_waiting(self):
            return 0

        def reset_input_buffer(self):
            self.log.append(("reset_input_buffer",))
            while not self.incoming.empty():
                self.incoming.get()

        def read(self, size=1):
            if not self.is_open:
                raise SerialException("closed")
            try:
                return self.incoming.get(timeout=0.02)
            except queue.Empty:
                return b""

        def write(self, data):
            self.log.append(("write", bytes(data)))
            self.written.append(bytes(data))
            for frame in self.band_side.push(data):
                reply = codec.frame_for_usb(answer(frame))
                self.incoming.put(reply[:3])   # in two pieces, as a real port may deliver it
                self.incoming.put(reply[3:])

        def close(self):
            self.log.append(("close",))
            self.is_open = False

    serial = types.ModuleType("serial")
    serial.Serial, serial.SerialException = Serial, SerialException
    tools = types.ModuleType("serial.tools")
    list_ports = types.ModuleType("serial.tools.list_ports")
    list_ports.comports = lambda: list(ports)
    serial.tools, tools.list_ports = tools, list_ports
    return {"serial": serial, "serial.tools": tools, "serial.tools.list_ports": list_ports}


BAND_PORT = Port("/dev/band", product="Vexo Open band", manufacturer="Vexo Open")
OTHER_PORT = Port("/dev/mouse", product="Optical Mouse", manufacturer="Somebody")


class SerialTest(unittest.TestCase):
    def test_wire_bytes_dtr_and_order(self):
        opened = []

        async def main():
            band = await Band.connect(42, transport="serial")
            self.assertEqual((band.name, band.transport_kind), ("Vexo-HACK-42", "serial"))
            await band.set_led("#000100")
            self.assertEqual(await band.get_name(), "Vexo-HACK-42")
            await band.disconnect()
            await band.disconnect()
        with mock.patch.dict(sys.modules, fake_serial([OTHER_PORT, BAND_PORT], opened, noise=b"\x07junk")):
            run(main())
        port = opened[0]
        self.assertEqual(port.port, "/dev/band")  # found by its USB text
        # DTR goes on as part of the open, with no RTS/CTS flow control, and old input is
        # dropped before anything is sent; DTR off at the end.
        self.assertEqual(port.log[:3], [("open", {"dtr": True, "rtscts": False, "dsrdtr": False}),
                                        ("reset_input_buffer",), ("write", b"\x00")])
        self.assertEqual(port.log[-2:], [("dtr", False), ("close",)])
        # No control line is written between the open and the close: from firmware 1.1.3
        # the band ends the session on any such write (contract 2.2).
        self.assertEqual([entry for entry in port.log if entry[0] == "dtr"], [("dtr", False)])
        # Everything on the wire is a COBS packet: no 00 inside, one 00 at the end.
        self.assertEqual(len(port.written), 4)
        for chunk in port.written:
            self.assertEqual(chunk[-1], 0)
            self.assertNotIn(0, chunk[:-1])
        sent = [codec.decode_command(codec.cobs_decode(chunk[:-1])) for chunk in port.written[1:]]
        self.assertEqual([c["opcode"] for c in sent], ["GET_INFO", "LED_SET", "GET_NAME"])
        self.assertEqual(sent[1]["args"]["g"], 1)

    def test_port_given_and_number_none(self):
        opened = []

        async def main():
            band = await Band.connect(None, transport="serial", port="COM5")
            self.assertEqual(band.number, 42)
            await band.disconnect()
        with mock.patch.dict(sys.modules, fake_serial([], opened)):
            run(main())
        self.assertEqual(opened[0].port, "COM5")

    def test_auto_detect(self):
        mouse_only, two_bands = [OTHER_PORT], [BAND_PORT, Port("/dev/band2", manufacturer="Vexo Open")]
        with mock.patch.dict(sys.modules, fake_serial(two_bands + [OTHER_PORT], [])):
            self.assertEqual(SerialTransport.find_ports(), ["/dev/band", "/dev/band2"])
        for ports, words in ((mouse_only, "No band found"), (two_bands, "/dev/band2")):
            with mock.patch.dict(sys.modules, fake_serial(ports, [])):
                with self.assertRaises(VexoError) as caught:
                    run(Band.connect(42, transport="serial"))
            self.assertEqual(caught.exception.code, "no-port")
            self.assertIn(words, str(caught.exception))
            self.assertIn("port=", str(caught.exception))

    def test_usb_id_is_one_constant(self):
        self.assertIsNone(serial_transport.USB_ID)  # not fixed yet
        by_id = Port("/dev/by-id", vid=0x1234, pid=0x5678)
        with mock.patch.dict(sys.modules, fake_serial([by_id, OTHER_PORT], [])):
            self.assertEqual(SerialTransport.find_ports(), [])
            with mock.patch.object(serial_transport, "USB_ID", (0x1234, 0x5678)):
                self.assertEqual(SerialTransport.find_ports(), ["/dev/by-id"])

    def test_errors(self):
        with mock.patch.dict(sys.modules, fake_serial([], [])):
            with self.assertRaises(VexoError) as caught:
                run(Band.connect(42, transport="serial", port="BROKEN"))
        self.assertEqual(caught.exception.code, "port-failed")
        with mock.patch.dict(sys.modules, {"serial": None}):
            with self.assertRaises(VexoError) as caught:
                run(Band.connect(42, transport="serial"))
        self.assertEqual(caught.exception.code, "no-pyserial")
        self.assertIn("Run: python3 -m pip install pyserial (on Windows: python -m pip install pyserial)",
                      str(caught.exception))

    def test_no_band_on_usb_explains_the_mac_question(self):
        with mock.patch.dict(sys.modules, fake_serial([OTHER_PORT], [])):
            with self.assertRaises(VexoError) as caught:
                run(Band.connect(42, transport="serial"))
        self.assertEqual(caught.exception.code, "no-port")
        text = str(caught.exception)
        for words in ("No band found on USB", "On a Mac", "\"Allow accessory to connect?\"", "press Allow",
                      "System Settings > Privacy & Security", "no port appears", "/dev/cu.usbmodem",
                      "port=\"COM5\""):
            self.assertIn(words, text)
        # Two bands plugged in: the port names, and no talk about a missing port.
        two = [BAND_PORT, Port("/dev/band2", product="Vexo Open band", manufacturer="Vexo Open")]
        with mock.patch.dict(sys.modules, fake_serial(two, [])):
            with self.assertRaises(VexoError) as caught:
                run(Band.connect(42, transport="serial"))
        self.assertEqual(caught.exception.code, "no-port")
        self.assertIn("Found 2 bands on USB (/dev/band, /dev/band2)", str(caught.exception))
        self.assertNotIn("Allow accessory", str(caught.exception))

    def test_reconnect_opens_the_port_again(self):
        opened = []

        async def main():
            band = await Band.connect(42, transport="serial", auto_reconnect=True, reconnect_seconds=2)
            band._reconnect_pause = 0.01
            events = []
            for name in ("reconnecting", "reconnected", "disconnect"):
                band.on(name, lambda e, name=name: events.append(name))
            await band.set_led("#00ff00")
            old_thread = band.transport._thread
            opened[0].is_open = False  # reads now fail, like a pulled cable
            for _ in range(400):
                if len(events) == 2:
                    break
                await asyncio.sleep(0.005)
            self.assertEqual(events, ["reconnecting", "reconnected"])
            self.assertEqual(len(opened), 2)  # a new port object...
            self.assertIn(("close",), opened[0].log)  # ...after the old one was closed
            self.assertFalse(old_thread.is_alive())  # ...and its reader thread ended
            self.assertIs(band.transport._serial, opened[1])
            self.assertTrue(band.transport._thread.is_alive() and band.transport._thread is not old_thread)
            sent = [codec.decode_command(f)["opcode"] for f in codec.Deframer().push(b"".join(opened[1].written))]
            self.assertEqual(sent, ["GET_INFO", "LED_SET"])
            self.assertEqual(await band.get_name(), "Vexo-HACK-42")
            await band.disconnect()
            self.assertEqual(events, ["reconnecting", "reconnected", "disconnect"])
        with mock.patch.dict(sys.modules, fake_serial([BAND_PORT], opened)):
            run(main())

    def test_cable_pulled(self):
        opened = []

        async def main():
            band = await Band.connect(42, transport="serial")
            reasons = []
            band.on("disconnect", lambda e: reasons.append(e.reason))
            opened[0].is_open = False  # reads now fail, like a pulled cable
            for _ in range(100):
                if reasons:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(reasons, ["lost"])
            self.assertFalse(band.connected)
            await band.disconnect()
        with mock.patch.dict(sys.modules, fake_serial([BAND_PORT], opened)):
            run(main())


if __name__ == "__main__":
    unittest.main()
