"""USB cable using the 'pyserial' package. pip install pyserial

On the cable every frame is COBS-encoded and followed by one 00 byte (see codec.py).

A band with firmware 1.1.3 ends the USB session of a computer that took no data for about 5
seconds while streams run, without a notice: the port stays open and commands are still
answered, but the streams are off and the settings are back at their defaults. The library
notices that (session_watch.py; this file only reports an empty packet as a sign) and ends
the link with reason "session-ended". With auto_reconnect the port is then opened again and
everything is set again.

In Python that is rare, because of how this file reads: a thread of its own takes the bytes
from the port all the time, also while the program is busy (see MAX_WAITING below). So the
band does not see a busy Python program as stuck. It only sees one that is stopped as a
whole: stopped in a debugger, suspended (Ctrl+Z), or held by code that never lets other
threads run.

The session end was tested with the pretend band and a fake port only, not yet with a real
band on firmware 1.1.3.
"""
import asyncio
import threading
from typing import Optional

from ..codec import Deframer, frame_for_usb
from ..transport import Transport, VexoError

# The band's USB vendor and product ID are not fixed yet. When they are, put them here as
# (vendor_id, product_id) and bands are found by ID. Until then we look at the USB text.
USB_ID = None
USB_TEXT = "Vexo Open"  # appears in the band's USB manufacturer and product strings
# The reader below never stops taking bytes from the port, also while the program is busy.
# Frames the program has not taken yet wait in memory, at most this many (about 1 MB);
# beyond that new frames are dropped, as on a link that is too slow.
# The consequence, said plainly: the band cannot tell that a Python program is stuck. A
# program whose handler hangs for a minute keeps its USB session, its streams and the
# microphone (blue light on) for that minute, because this reader goes on taking the data
# and throws away what does not fit. The band's 5 second rule only catches a program that
# is stopped as a whole. This is kept on purpose: reading all the time is what the contract
# asks of a client (2.2, "never stop reading"), it is what lets a slow handler lose no
# answer, and a Python program that hangs can be ended by its person with Ctrl+C, which
# closes the port. A web page is different (one thread), see web-serial.js.
MAX_WAITING = 5000


class SerialTransport(Transport):
    kind = "serial"
    session_watch = True  # the band can end a USB session by itself: Band watches for it (session_watch.py)

    def __init__(self, port: Optional[str] = None) -> None:
        """port: e.g. "COM5", or "/dev/cu.usbmodem101" on a Mac. None = find the band automatically."""
        super().__init__()
        self.port = port
        self.on_session_hint = lambda: None  # assigned by Band: an empty packet came in the middle of a session
        self._serial = None
        self._thread: Optional[threading.Thread] = None
        self._closing = False
        self._waiting = 0  # frames read from the cable and not yet given to the program
        self._hint_waiting = False  # an empty-packet sign is on its way to the program
        self._waiting_lock = threading.Lock()

    @staticmethod
    def find_ports() -> list:
        """Names of the serial ports that look like a Vexo Open band."""
        from serial.tools import list_ports
        found = []
        for p in list_ports.comports():
            text = "%s %s" % (getattr(p, "product", None) or "", getattr(p, "manufacturer", None) or "")
            by_id = USB_ID is not None and (getattr(p, "vid", None), getattr(p, "pid", None)) == USB_ID
            if by_id or USB_TEXT in text:
                found.append(p.device)
        return found

    async def open(self) -> None:
        try:
            import serial
        except ImportError:
            raise VexoError("no-pyserial", "USB needs the 'pyserial' package. Run: python3 -m pip install pyserial "
                            "(on Windows: python -m pip install pyserial)")
        if self._serial is not None or self._thread is not None:
            await self.close()  # opening again (a reconnect): tidy up the old port and reader first
        port = self.port
        if port is None:
            found = self.find_ports()
            if len(found) != 1:
                seen = "No band found on USB" if not found else "Found %d bands on USB (%s)" % (
                    len(found), ", ".join(found))
                text = seen + (". Plug the band in, or give the port name yourself: "
                               "Band.connect(42, transport=\"serial\", port=\"COM5\"). "
                               "On a Mac the port is called /dev/cu.usbmodem...")
                if not found:
                    text += (" Also on a Mac: the first time a band is plugged in, macOS may ask "
                             "\"Allow accessory to connect?\". Until you press Allow (or allow it under "
                             "System Settings > Privacy & Security) no port appears.")
                raise VexoError("no-port", text)
            port = found[0]
        try:
            # DTR on tells the band a program is listening. It is chosen before the port is
            # opened, so pyserial raises it as part of the open and no control line is
            # written afterwards: the band takes a later write for a new program opening
            # the port and ends the session (contract 2.2). No RTS/CTS flow control.
            self._serial = serial.Serial(None, 115200, timeout=0.2, rtscts=False, dsrdtr=False)
            self._serial.port = port
            self._serial.dtr = True
            self._serial.open()
            self._serial.reset_input_buffer()  # forget bytes from before we opened
            self._serial.write(b"\x00")  # ends any half-typed packet on the band's side
        except (serial.SerialException, OSError) as e:
            self._serial = None
            raise VexoError("port-failed", "Could not open %s: %s" % (port, e))
        self._closing = False
        self._waiting = 0
        loop = asyncio.get_event_loop()
        self._thread = threading.Thread(target=self._read_loop, args=(self._serial, loop), daemon=True)
        self._thread.start()

    def _read_loop(self, port, loop: asyncio.AbstractEventLoop) -> None:
        # The band is silent until we send a command, and we just emptied the input
        # buffer, so the first byte we read starts a packet.
        # This loop runs on its own thread and reads all the time the port is open: while
        # a command waits for its answer and while the program's handlers run. (The band
        # ends the session of a computer that stops taking bytes.)
        deframer = Deframer(synced=True)
        # An empty packet (a 00 right after a 00) once frames have arrived: the band paused,
        # or it started a new session. Band is told, and looks whether the streams still send.
        after_zero = mid_session = False
        try:
            while not self._closing:
                chunk = port.read(port.in_waiting or 1)
                if chunk and mid_session and ((after_zero and chunk[:1] == b"\x00") or b"\x00\x00" in chunk):
                    if not self._hint_waiting:  # at most one waits for the program, however many 00 come
                        self._hint_waiting = True
                        loop.call_soon_threadsafe(self._hint)
                if chunk:
                    after_zero = chunk[-1:] == b"\x00"
                for frame in deframer.push(chunk):
                    mid_session = True
                    with self._waiting_lock:
                        if self._waiting >= MAX_WAITING:
                            continue  # the program is not keeping up: drop, do not grow without limit
                        self._waiting += 1
                    loop.call_soon_threadsafe(self._deliver, frame)
        except Exception:
            pass  # cable pulled
        if not self._closing:
            try:
                loop.call_soon_threadsafe(self.on_close, "lost")
            except RuntimeError:
                pass  # event loop already finished

    def _hint(self) -> None:
        self._hint_waiting = False
        self.on_session_hint()

    def _deliver(self, frame: bytes) -> None:
        with self._waiting_lock:
            self._waiting -= 1
        self.on_frame(frame)

    async def close(self) -> None:
        self._closing = True
        port, self._serial = self._serial, None
        if self._thread is not None:
            await asyncio.get_event_loop().run_in_executor(None, self._thread.join)
            self._thread = None
        if port is not None:
            try:
                port.dtr = False  # DTR off = session closed
                port.close()
            except Exception:
                pass  # the cable may already be gone

    async def send(self, frame: bytes) -> None:
        port = self._serial
        if port is None:
            raise VexoError("not-connected", "Not connected to a band.")
        try:
            await asyncio.get_event_loop().run_in_executor(None, port.write, frame_for_usb(frame))
        except Exception as e:
            raise VexoError("not-connected", "The USB cable stopped working: %s" % e)
