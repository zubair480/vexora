# Speech to text for the band's microphone, behind one small interface.
#
#     import speech
#     ears = speech.create()                        # Deepgram; or the name of your own engine
#     ears.start(lambda text, final: print(text))   # called with partial and final text
#     band.on("audio", lambda a: ears.feed(a.opus, a.seq))
#     ...
#     print(ears.stop())                            # everything that was said
#
# Or all of that in one line:   text = speech.listen(band, 10)
#
# The kit turns speech into text with Deepgram, an online speech-to-text service. While it
# runs, everything the band's microphone hears is sent over the internet to Deepgram, a
# company. It prints one line that says so before any sound is sent: keep that line in your
# project, and tell the people near the band.
#
# It needs a key. Each team gets its own at the event. A key is a secret: put it in the
# environment variable DEEPGRAM_API_KEY, or in a file called ".env" in the folder you
# start your program in (one line: DEEPGRAM_API_KEY=...). Never in your code, in git, in a
# log, in a chat or in the band's storage. Without a key, start() stops with one sentence
# that says so; everything else about the band works with no key. docs/deepgram.md has the
# whole story and the measurements (tried with a real key in November 2026).
#
# Nothing to install: the band's Opus packets are sent as they are (tiny_websocket.py is
# the connection).
#
# To use another speech service, subclass Speech, write feed_pcm(), and either register it
# (speech.ENGINES["mine"] = MySpeech, then speech.create("mine")) or pass the class itself
# (speech.create(MySpeech)). docs/speech.md has a complete example.
import json
import os
import sys
import threading
import time
from urllib.parse import urlencode

import kit  # noqa: F401  (lets Python find the library)
from tiny_websocket import WebSocket, WebSocketError
from vexo_open.audio import FRAME_SAMPLES, SAMPLE_RATE, PcmDecoder
from vexo_open.sync import VexoError, short_errors

# A problem from this file that the program does not catch (no key, ...) ends it with one
# sentence and the line of the program, like every band problem. No traceback.
short_errors(helper=__file__)

FRAME_BYTES = 2 * FRAME_SAMPLES      # 20 ms of sound as 16 bit samples


class SpeechError(VexoError):
    """Something the person at the keyboard can fix; the message says how. It is a
    VexoError (code "speech"), so `except VexoError` catches it too, and uncaught it ends
    a program with one sentence: "Problem (speech): ..."."""

    def __init__(self, message: str) -> None:
        super().__init__("speech", message)


class Speech:
    """The interface every engine has: start, feed (band packets) or feed_pcm (plain
    samples), stop. Text arrives in on_text(text, final): final=False is a guess that may
    still change, final=True is settled. on_text is called from another thread."""

    name = "?"

    def start(self, on_text=None):
        """Get ready (the service is called). Raises SpeechError when something is missing."""
        self.on_text = on_text
        self.finals = []
        self._decoder = None
        return self

    def feed(self, packet, seq=None) -> None:
        """One Opus packet from the band (or an `audio` event). The default turns it into
        samples and calls feed_pcm(); an engine that understands Opus sends it as it is."""
        if self._decoder is None:
            print("Loading the sound decoder (a few seconds, the first time)...")
            self._decoder = PcmDecoder()
        self.feed_pcm(self._decoder.feed(packet, seq))

    def feed_pcm(self, pcm: bytes) -> None:
        """Plain sound: 16000 samples a second, mono, 16 bit, as bytes."""
        raise NotImplementedError

    def stop(self) -> str:
        """Finish what is still being worked on. Returns all the final text."""
        return self.text

    @property
    def text(self) -> str:
        return " ".join(self.finals)

    @property
    def label(self) -> str:
        """A few words for the person at the keyboard about who is listening."""
        return self.name

    def _say(self, text, final) -> None:
        text = (text or "").strip()
        if final and text:
            self.finals.append(text)
        if self.on_text and (text or not final):
            self.on_text(text, final)


# ---- Deepgram: the speech-to-text service the kit uses ---------------------------------

KEY_NAME = "DEEPGRAM_API_KEY"


def find_key(name: str = KEY_NAME) -> str:
    """The key from the environment variable, or from a ".env" file in the folder the
    program was started in. Never write a key into code."""
    found = os.environ.get(name, "").strip()
    for folder in ([] if found else [os.getcwd()]):
        try:
            with open(os.path.join(folder, ".env"), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("export "):
                        line = line[7:]
                    key, _, value = line.partition("=")
                    if key.strip() == name and value.strip():
                        found = value.strip().strip("'\"")
        except (OSError, ValueError):
            pass
        if found:
            break
    if not found:
        raise SpeechError("Transcription needs a Deepgram key: see docs/deepgram.md. Put your team's key in the environment variable %s "
                          "(macOS, Linux: export %s=...   Windows PowerShell: $env:%s = \"...\"   Windows cmd: set %s=...), "
                          "or in a file called .env in the folder you start the program in, with the line %s=your-key. "
                          "Never put it in your code or in git." % (name, name, name, name, name))
    if not found.isascii() or not found.isprintable() or " " in found:
        raise SpeechError("The Deepgram key in %s has a space or a strange character in it. "
                          "Copy it again, the whole key and nothing else." % name)
    return found


# What to do about each answer Deepgram gives to a refused connection. Only the number
# comes from Deepgram; the sentences are ours.
REFUSED = {
    400: "Deepgram did not understand the settings. Check the options you gave to speech.create().",
    401: "Deepgram did not accept the key. Check %s: the whole key, nothing before or after it." % KEY_NAME,
    402: "The key has no credit left. Tell an organiser.",
    403: "The key is not allowed to use this model or setting.",
    429: "Too many connections with this key at the same time. Stop the other programs that use it, "
         "wait a few seconds and start again.",
}


class DeepgramSpeech(Speech):
    """Sends the band's sound to Deepgram as it arrives; text comes back while people speak.

    send="opus"  (default) the band's Opus packets exactly as they arrive, one per message.
                 Nothing to decode, nothing to install, about 3 kB a second.
    send="pcm"   decoded samples (needs: python3 -m pip install av), 32 kB a second. Use it
                 to feed plain samples with feed_pcm(). The text is as good either way.

    Other options: model, language, endpointing (how long a pause ends a sentence, in ms),
    and any other setting of Deepgram's live service by its name, for example
    keyterm="Vexo". Where the sound goes is fixed in this file and is not an option."""

    name = "deepgram"
    company = "Deepgram (deepgram.com)"
    URL = "wss://api.deepgram.com/v1/listen"        # the only place the key and the sound go
    KEEP_ALIVE = 4.0                                 # Deepgram hangs up after 10 s with no message
    FIXED = ("mip_opt_out", "encoding", "sample_rate", "channels", "interim_results", "callback", "callback_method")

    def __init__(self, model="nova-3", language="en", send="opus", endpointing=100, **more):
        if send not in ("opus", "pcm"):
            raise SpeechError('send must be "opus" or "pcm".')
        for name in more:
            if name in self.FIXED or name in ("key", "url"):
                raise SpeechError("'%s' cannot be set here. The key comes from %s; where the sound goes and "
                                  "how it is sent are fixed in speech.py." % (name, KEY_NAME))
        self.model, self.language, self.send = model, language, send
        self.query = {
            "model": model, "language": language,
            "encoding": "opus" if send == "opus" else "linear16", "sample_rate": SAMPLE_RATE, "channels": 1,
            "interim_results": "true",              # guesses while someone is still speaking
            "smart_format": "true",                 # punctuation, and numbers as digits
            "endpointing": endpointing,
            "mip_opt_out": "true",                  # "do not keep this sound to train your models"
        }
        self.query.update(more)

    @property
    def label(self) -> str:
        return "%s, online (model %s)" % (self.company, self.model)

    def start(self, on_text=None):
        super().start(on_text)
        key = find_key()
        # Say who gets the sound, before any of it is sent. Keep this line in your project.
        print("Note: while this runs, everything the band's microphone hears is sent over the "
              "internet to %s. Tell the people near the band." % self.company)
        try:
            # The key travels in a header of the opening request, inside the encrypted
            # connection. It is not part of the address.
            self._ws = WebSocket(self.URL + "?" + urlencode(self.query), headers={"Authorization": "Token " + key})
        except WebSocketError as error:
            if error.status:
                raise SpeechError("deepgram: %s (status %d)" % (
                    REFUSED.get(error.status, "Deepgram refused the connection."), error.status)) from None
            raise SpeechError("deepgram: %s If the internet works otherwise, this network may block "
                              "this kind of connection (a WebSocket): try another network or a phone's hotspot."
                              % error) from None
        self.error = None
        self._parts = []                             # finished pieces of the sentence being spoken
        self._stopping = False
        self._sent_at = time.time()
        self._done = threading.Event()
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._keep_alive, daemon=True).start()
        return self

    # ---- sound out

    def feed(self, packet, seq=None) -> None:
        if hasattr(packet, "opus"):
            packet, seq = packet.opus, packet.seq
        if self.send == "pcm":
            Speech.feed(self, packet, seq)           # decodes, fills lost packets, calls feed_pcm()
        elif packet:
            self._send(bytes(packet))

    def feed_pcm(self, pcm: bytes) -> None:
        if self.send != "pcm":
            raise SpeechError('To feed plain samples to Deepgram, create it with send="pcm".')
        if pcm:
            self._send(pcm)

    def _send(self, sound: bytes) -> None:
        self._sent_at = time.time()
        self._ws.send(sound)

    def _keep_alive(self) -> None:
        # While the band sends no sound (the microphone is off), tell Deepgram we are still here.
        while not self._done.wait(1.0):
            if time.time() - self._sent_at >= self.KEEP_ALIVE and not self._stopping:
                self._sent_at = time.time()
                self._ws.send(json.dumps({"type": "KeepAlive"}))

    # ---- text back

    def _read(self) -> None:
        while True:
            message = self._ws.recv()
            if message is None:
                break
            if isinstance(message, str):
                try:
                    self._message(json.loads(message))
                except (ValueError, TypeError, AttributeError, IndexError, KeyError):
                    pass                             # not what the documentation describes: skip it
        self._sentence_ends()
        if not self._stopping and not self.error:
            self.error = "the connection closed"
        if self.error and not self._stopping:
            print("Speech problem: Deepgram stopped listening (%s). The text so far is kept." % self.error,
                  file=sys.stderr)
        self._done.set()

    def _message(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "Results":
            best = (message.get("channel", {}).get("alternatives") or [{}])[0]
            piece = str(best.get("transcript") or "").strip()
            if not message.get("is_final"):
                self._say(" ".join(self._parts + [piece]), False)     # a guess; it may still change
                return
            if piece:
                self._parts.append(piece)
            if message.get("speech_final"):          # Deepgram heard a pause: the sentence is complete
                self._sentence_ends()
            elif piece:
                self._say(" ".join(self._parts), False)
        elif kind == "Error":
            self.error = "it reported an error"     # its own words are not repeated here

    def _sentence_ends(self) -> None:
        parts, self._parts = self._parts, []
        if parts:
            self._say(" ".join(parts), True)

    def stop(self, wait=5.0) -> str:
        """Ask for the last words, wait for them (at most `wait` seconds) and hang up."""
        if self.send == "pcm" and self._decoder:
            self.feed_pcm(self._decoder.flush())
        self._stopping = True
        self._ws.send(json.dumps({"type": "CloseStream"}))    # "send what you still have, then close"
        self._done.wait(wait)
        self._ws.close()
        self._done.wait(1.0)
        if self.error and not self.finals:
            raise SpeechError("deepgram: %s." % self.error)
        return self.text


ENGINES = {"deepgram": DeepgramSpeech}       # name -> class; add your own engine here


def listen(band, seconds, on_text=None, **options) -> str:
    """The shortest way from the band to text: switch the microphone on for `seconds`,
    send the sound to the speech engine, switch it off, and return everything that was said.
    on_text(text, final) is called while people speak. Options are those of create().
    Call it in the main program, not inside a handler given to band.on(): a handler runs
    alone, the sound waits behind it, and nothing would be heard."""
    ears = create(**options).start(on_text)
    feed = ears.feed
    band.on("audio", feed)
    try:
        band.configure(audio=True)               # the light goes solid blue: the microphone is on
        band.wait(seconds)
        band.configure(audio=False)
    finally:
        band.off("audio", feed)
    return ears.stop()


def create(name="deepgram", **options) -> Speech:
    """speech.create(), speech.create("deepgram", language="de"), or your own engine: by the
    name you gave it in ENGINES (speech.create("mine")), or the class itself (speech.create(MySpeech))."""
    if isinstance(name, type) and issubclass(name, Speech):
        return name(**options)
    if name not in ENGINES:
        raise SpeechError("Unknown speech engine '%s'. The engines here are: %s. To use your own, write a "
                          "class based on speech.Speech and register it with speech.ENGINES[\"my-name\"] = MyClass, "
                          "or pass the class itself: speech.create(MyClass). See docs/speech.md."
                          % (name, ", ".join(ENGINES)))
    return ENGINES[name](**options)
