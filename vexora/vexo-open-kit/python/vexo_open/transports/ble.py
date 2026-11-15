"""Bluetooth using the 'bleak' package (Windows, macOS, Linux). pip install bleak"""
from ..codec import RX_UUID, SERVICE_UUID, TX_UUID, band_name, band_number_of
from ..transport import Transport, VexoError, not_found_help


MAKER_ID = 0xFFFF  # maker data in the first packet: [0x4F][contract major][band number u16]
MAKER_TAG = 0x4F


class BleTransport(Transport):
    kind = "ble"

    def __init__(self, number, scan_seconds: float = 10.0) -> None:
        super().__init__()
        if number is None:
            raise VexoError("bad-arg", "Bluetooth needs the band's number, e.g. Band.connect(42). "
                            "Connecting to \"any band\" (None) only works with the USB cable or the simulator.")
        self.name = band_name(number)
        self.number = band_number_of(self.name)
        self.scan_seconds = scan_seconds
        self._client = None

    def _is_our_band(self, device, advert) -> bool:
        """True only for a device that advertises the Vexo Open service and is our band.

        A band sends two packets. The first holds the service and the band number (as
        maker data); the second, the scan response, holds the name. Most computers hand
        us both together. If only the first has arrived, the number in it decides."""
        services = [str(u).lower() for u in (getattr(advert, "service_uuids", None) or [])]
        if SERVICE_UUID not in services:
            return False
        name = getattr(advert, "local_name", None)
        if name:
            return name == self.name
        maker = (getattr(advert, "manufacturer_data", None) or {}).get(MAKER_ID)
        if maker is not None:
            maker = bytes(maker)
            return len(maker) >= 4 and maker[0] == MAKER_TAG and int.from_bytes(maker[2:4], "little") == self.number
        return getattr(device, "name", None) == self.name  # the computer's remembered name, last resort

    async def open(self) -> None:
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError:
            raise VexoError("no-bleak", "Bluetooth needs the 'bleak' package. Run: python3 -m pip install bleak "
                            "(on Windows: python -m pip install bleak)")
        await self.close()  # opening again (a reconnect): let go of the old connection first
        device = await BleakScanner.find_device_by_filter(
            self._is_our_band, timeout=self.scan_seconds, service_uuids=[SERVICE_UUID])
        if device is None:
            raise VexoError("not-found-nearby", not_found_help(self.name))
        client = BleakClient(device, disconnected_callback=lambda _client: self._lost(client))
        try:
            await client.connect()
            # Listen first, so the answer to the very first command cannot be missed.
            # Each notification is handed to Band at once, on the event loop: nothing is kept
            # here, so nothing can pile up in this file.
            await client.start_notify(TX_UUID, lambda _sender, data: self.on_frame(bytes(data)))
        except BaseException as e:
            try:
                await client.disconnect()  # leave no half-open connection behind
            except Exception:
                pass
            if not isinstance(e, Exception):
                raise  # the program is stopping (or the reconnect was cancelled)
            raise VexoError("not-found-nearby", "Found %s but could not connect (%s). Double-tap the "
                            "band and try again." % (self.name, e))
        self._client = client

    def _lost(self, client) -> None:
        if client is self._client:  # not an old connection, and not one we closed ourselves
            self._client = None
            self.on_close("lost")

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.disconnect()

    async def send(self, frame: bytes) -> None:
        if self._client is None:
            raise VexoError("not-connected", "Not connected to a band.")
        await self._client.write_gatt_char(RX_UUID, frame, response=True)
