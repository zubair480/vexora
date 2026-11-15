# A very small WebSocket client, so that the online speech service in speech.py works with
# nothing installed. A WebSocket is a normal internet connection that starts like a web
# request and then carries messages both ways (RFC 6455).
#
#     ws = WebSocket("wss://example.com/listen?x=1", headers={"Authorization": "Token ..."})
#     ws.send(b"bytes")          # a binary message
#     ws.send('{"a": 1}')        # a text message
#     message = ws.recv()        # str, bytes, or None when the other side has closed
#     ws.close()
#
# send() may be called from one thread while another thread waits in recv().
import base64
import hashlib
import os
import socket
import ssl
import struct
import threading
from urllib.parse import urlsplit

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"   # fixed by the WebSocket standard
MOST_HEAD = 8192                                # the answer to the opening request is never longer
MOST_MESSAGE = 1 << 20                          # one message from the service: at most 1 MB


class WebSocketError(Exception):
    """The connection could not be opened. .status is the web status code (401 = the key
    was refused), or 0 when there was no answer at all."""

    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


def accept_key(key: str) -> str:
    """The answer a server must give to the random key in the opening request."""
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def frame(opcode: int, data: bytes, mask: bool) -> bytes:
    """One WebSocket frame. opcode: 1 text, 2 binary, 8 close, 9 ping, 10 pong.
    A client must mask what it sends (mask=True); a server must not."""
    head = bytes([0x80 | opcode])
    size = len(data)
    bit = 0x80 if mask else 0
    if size < 126:
        head += bytes([bit | size])
    elif size < 65536:
        head += bytes([bit | 126]) + struct.pack(">H", size)
    else:
        head += bytes([bit | 127]) + struct.pack(">Q", size)
    if not mask:
        return head + data
    key = os.urandom(4)
    return head + key + bytes(b ^ key[i % 4] for i, b in enumerate(data))


def read_frame(read):
    """Read one frame with read(n) -> exactly n bytes. Returns (final, opcode, data)."""
    first, second = read(2)
    size = second & 0x7F
    if size == 126:
        size = struct.unpack(">H", read(2))[0]
    elif size == 127:
        size = struct.unpack(">Q", read(8))[0]
    if size > MOST_MESSAGE:
        raise ValueError("The message is too long.")
    key = read(4) if second & 0x80 else None
    data = read(size) if size else b""
    if key:
        data = bytes(b ^ key[i % 4] for i, b in enumerate(data))
    return bool(first & 0x80), first & 0x0F, data


def read_exactly(sock, count: int) -> bytes:
    data = b""
    while len(data) < count:
        more = sock.recv(count - len(data))
        if not more:
            raise ConnectionError("The connection was closed.")
        data += more
    return data


class WebSocket:
    def __init__(self, url, headers=None, timeout=10):
        parts = urlsplit(url)
        secure = parts.scheme == "wss"
        host, port = parts.hostname, parts.port or (443 if secure else 80)
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            if secure:
                sock = self._context().wrap_socket(sock, server_hostname=host)
        except ssl.SSLCertVerificationError as error:
            raise WebSocketError("This Python cannot check the certificate of %s (%s). Fix it once "
                                 "with:  python3 -m pip install certifi" % (host, error.verify_message)) from None
        except OSError as error:
            raise WebSocketError("Could not reach %s: %s. Is this computer online?" % (host, error)) from None
        self.sock = sock
        self._lock = threading.Lock()
        self.closed = False
        key = base64.b64encode(os.urandom(16)).decode()
        path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
        lines = ["GET %s HTTP/1.1" % path, "Host: %s" % parts.netloc, "Upgrade: websocket",
                 "Connection: Upgrade", "Sec-WebSocket-Key: " + key, "Sec-WebSocket-Version: 13"]
        lines += ["%s: %s" % item for item in (headers or {}).items()]
        if any("\r" in line or "\n" in line for line in lines):
            sock.close()
            raise WebSocketError("The address or a header has a line break in it.")
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        answer = b""
        while b"\r\n\r\n" not in answer and len(answer) < MOST_HEAD:
            try:
                more = sock.recv(1)
            except OSError:
                more = b""
            if not more:
                break
            answer += more
        text = answer.decode("latin-1")
        first = text.split("\r\n")[0].split(" ")
        status = int(first[1]) if text.startswith("HTTP/") and len(first) > 1 and first[1].isdigit() else 0
        if status != 101 or accept_key(key).lower() not in text.lower():
            # Only the number is kept. What the service wrote next to it is not read and not
            # repeated: it is text from a stranger, and it has no business in your terminal or log.
            sock.close()
            raise WebSocketError("The service refused the connection (status %d)." % status, status)
        sock.settimeout(None)

    @staticmethod
    def _context():
        context = ssl.create_default_context()
        try:                                      # some Pythons on a Mac ship without certificates
            import certifi
            context.load_verify_locations(certifi.where())
        except ImportError:
            pass
        return context

    def send(self, message, opcode=None) -> None:
        """Send one message: str = text, bytes = binary."""
        if isinstance(message, str):
            message, opcode = message.encode(), opcode or 1
        with self._lock:
            if not self.closed:
                self.sock.sendall(frame(opcode or 2, bytes(message), mask=True))

    def recv(self, timeout=None):
        """Wait for the next message. Returns str (text), bytes (binary), or None when the
        other side closed the connection. Raises socket.timeout after `timeout` seconds."""
        parts, kind = [], 0
        self.sock.settimeout(timeout)
        while True:
            try:
                final, opcode, data = read_frame(lambda n: read_exactly(self.sock, n))
            except socket.timeout:
                raise
            except (OSError, ValueError):
                self.close()
                return None
            if opcode == 8:                       # close
                self.close()
                return None
            if opcode == 9:                       # ping: answer with pong
                self.send(data, opcode=10)
                continue
            if opcode == 10:
                continue
            kind = opcode or kind
            parts.append(data)
            if sum(len(p) for p in parts) > MOST_MESSAGE:
                self.close()
                return None
            if final:
                whole = b"".join(parts)
                return whole.decode("utf-8", "replace") if kind == 1 else whole

    def close(self) -> None:
        with self._lock:
            if self.closed:
                return
            self.closed = True
            try:
                self.sock.sendall(frame(8, struct.pack(">H", 1000), mask=True))
            except OSError:
                pass
        try:
            self.sock.shutdown(socket.SHUT_RDWR)  # wakes a thread that waits in recv()
        except OSError:
            pass
        self.sock.close()
