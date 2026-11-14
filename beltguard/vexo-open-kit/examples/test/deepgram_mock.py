"""A pretend Deepgram for the tests: a tiny WebSocket server on this computer only.

It speaks the part of Deepgram's live service the kit uses, as tried against the real
service in November 2026: the opening request with the key in the Authorization header
(Python) or in the protocol field (a browser, Node), sound as binary messages, the text
messages KeepAlive and CloseStream, and answers of the types Results and Metadata.
Nothing here reaches the internet, and no real key is ever used with it.
"""
import json
import os
import socket
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python"))
from tiny_websocket import accept_key, frame, read_exactly, read_frame  # noqa: E402

SERVER_WORDS = "WORDS-FROM-THE-SERVER"      # must never show up in what the kit prints


def results(text, final, pause=False):
    return json.dumps({"type": "Results", "is_final": final, "speech_final": pause,
                       "channel": {"alternatives": [{"transcript": text}]}})


class MockDeepgram:
    """with MockDeepgram() as server: ... server.url, server.requests, server.sound, server.texts

    refuse=401     answer the opening request with that status and SERVER_WORDS, as text
    drop_after=N   hang up without a word after N sound messages
    error=True     send an Error message with SERVER_WORDS after the first sound message
    Every 25 sound messages (half a second of band sound) it sends a guess, every 50 a
    finished sentence; CloseStream gets the rest, a Metadata message and a goodbye."""

    def __init__(self, refuse=None, drop_after=None, error=False):
        self.refuse, self.drop_after, self.error = refuse, drop_after, error
        self.requests, self.sound, self.texts = [], [], []
        self.closed = threading.Event()
        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(5)
        self.port = self._server.getsockname()[1]
        self.url = "ws://127.0.0.1:%d/v1/listen" % self.port
        threading.Thread(target=self._accept, daemon=True).start()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self._server.close()

    def _accept(self):
        while True:
            try:
                sock, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(sock,), daemon=True).start()

    def _serve(self, sock):
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                more = sock.recv(1)
                if not more:
                    return
                head += more
            lines = head.decode("latin-1").split("\r\n")
            headers = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:]) if k}
            self.requests.append({"path": lines[0].split(" ")[1], "headers": headers})
            if self.refuse:
                body = json.dumps({"err_msg": SERVER_WORDS}).encode()
                sock.sendall(("HTTP/1.1 %d Refused\r\ndg-error: %s\r\nContent-Length: %d\r\n\r\n"
                              % (self.refuse, SERVER_WORDS, len(body))).encode() + body)
                return
            answer = ["HTTP/1.1 101 Switching Protocols", "Upgrade: websocket", "Connection: Upgrade",
                      "Sec-WebSocket-Accept: " + accept_key(headers["sec-websocket-key"])]
            if "sec-websocket-protocol" in headers:          # a browser or Node: the key is the second entry
                answer.append("Sec-WebSocket-Protocol: token")
            sock.sendall(("\r\n".join(answer) + "\r\n\r\n").encode())
            count = said = 0
            while True:
                _final, opcode, data = read_frame(lambda n: read_exactly(sock, n))
                if opcode == 8:
                    return
                if opcode == 2:
                    self.sound.append(data)
                    count += 1
                    if self.error and count == 1:
                        sock.sendall(frame(1, json.dumps({"type": "Error", "description": SERVER_WORDS}).encode(), False))
                    if self.drop_after and count >= self.drop_after:
                        return
                    if count % 50 == 0:
                        said += 1
                        sock.sendall(frame(1, results("Sentence %d." % said, True, True).encode(), False))
                    elif count % 25 == 0:
                        sock.sendall(frame(1, results("Sent", False).encode(), False))
                elif opcode == 1:
                    self.texts.append(json.loads(data.decode()))
                    if self.texts[-1].get("type") == "CloseStream":
                        if count % 50:
                            sock.sendall(frame(1, results("The end.", True, True).encode(), False))
                        sock.sendall(frame(1, json.dumps({"type": "Metadata", "duration": count * 0.02}).encode(), False))
                        sock.sendall(frame(8, b"\x03\xe8", False))
                        return
        except (OSError, ValueError, ConnectionError, KeyError):
            pass
        finally:
            sock.close()
            self.closed.set()


def launcher(url, examples):
    """Python code that runs a program (sys.argv[1], with its arguments) with speech.py
    pointed at a pretend Deepgram. The address is fixed in speech.py and is not an option,
    so a test has to change it in the running program itself."""
    return ("import runpy, sys; sys.path.insert(0, %r); import speech; speech.DeepgramSpeech.URL = %r; "
            "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')" % (examples, url))
