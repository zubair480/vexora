# Speech to text

How the sound from the band's microphone becomes text: the decoder, the speech interface,
and the service the kit uses for the text, Deepgram.

> **42 is only our example. Wherever you see 42 in a command, an address or a name, type the
> number written on your band's bag.** Band 7 is `Vexo-HACK-07`, and its dashboard address
> ends in `?band=7`.

Two things first:

- **The microphone records everyone near the band**, not only the person wearing it. The
  band's **solid blue light** means the microphone is on. Tell the people around you.
- The text comes from [Deepgram](deepgram.md), an online speech-to-text service. While it
  is on, **the sound leaves your computer and goes to that company**: say so to the people
  around you as well. It needs your team's key. Without a key everything else works
  (motion, the LED, saving the sound, the samples); only the text is missing.

## From the band to text

    band  --Opus packets-->  decoder  --samples (PCM)-->  speech engine  -->  text

1. `band.configure(audio=True)` makes the band send its microphone as **Opus packets**:
   50 a second, each holding 20 ms of sound (16 kHz, mono). Every packet has a counter,
   `seq`, so you can tell when one was lost on the way.
2. Opus is squeezed sound. Most tools want plain samples instead (**PCM**: 16000 numbers a
   second). The decoder helper below does that.
3. A speech engine turns the sound into text. `examples/python/speech.py` has one,
   Deepgram, which takes the Opus packets as they are (so for text alone step 2 is not
   needed), and an interface for your own.

Try it (10 seconds, text appears while you talk). Nothing to install. Set your team's key
first ([`deepgram.md`](deepgram.md) says where it comes from and where it must never go),
then type the command in `my-project/`, the folder that holds the kit:

    export DEEPGRAM_API_KEY=...
    python3 vexo-open-kit/examples/python/audio_to_text.py 42

On Windows type `set DEEPGRAM_API_KEY=...` and `python` instead of `python3`. The sound is
saved as `recording.ogg` in the folder you are in. With no key the program still saves the
sound and says in one sentence that the text needs a key; `--text no` only saves the sound
and sends nothing anywhere.

No band? `--sim` instead of the number runs with a pretend band. It has no microphone and
sends silence, so let it play a recording. The kit has one, 16 seconds of a person reading
aloud (public domain):

    python3 vexo-open-kit/examples/python/audio_to_text.py --sim --seconds 16 --replay vexo-open-kit/dashboard/speech/sample-speech.opus

(The pretend band starts the file again at its end; `audio_to_text.py` listens to it once
and stops there, so the text does not begin a second time.) In your own program the pretend band gets its voice with one option, `audio_packets=`
(JavaScript: `audioPackets`); the next section and
["A voice for the pretend band"](api.md#a-voice-for-the-pretend-band) show how.

## The decoder helper (Python)

`vexo_open.audio` is part of the library. It needs one extra package, which brings its own
Opus decoder and installs the same way on macOS, Windows and Linux:

    python3 -m pip install av

(On Windows: `python -m pip install av`.) No version here, on purpose: pip then takes the
newest `av` that fits your Python. It was tested with 15.1.0 on Python 3.9 and with 19.0.2
on Python 3.12, and no single version installs on both. Without `av` the library still
imports; the helper then stops with one sentence that names this command. The text does
not need it: Deepgram takes the band's packets as they are.

The first `PcmDecoder()` of a program takes a few seconds (up to about 7 on a slow
laptop) while Python loads `av`. Make it once, before the microphone goes on, and print a
line first.

Live, packet by packet (lines for a program in `my-project/` that has connected already):

```python
from vexo_open.audio import PcmDecoder

print("Loading the sound decoder...")
decoder = PcmDecoder()           # a few seconds, the first time

def on_audio(a):
    pcm = decoder.feed(a)        # bytes: 320 samples (20 ms), 16 bit, little endian
    ...                          # give them to anything that wants plain sound

band.on("audio", on_audio)
band.configure(audio=True)
```

A whole recording at once, and a few small tools:

```python
from vexo_open import audio

pcm = audio.decode_packets(packets)       # a list of audio events, or of Opus packets
audio.write_wav("sound.wav", pcm)         # a normal .wav file
print(audio.level_db(pcm))                # loudness: about -50 quiet room, -30 speech nearby
louder = audio.normalise(pcm)             # lift a quiet recording, without clipping
samples = audio.to_float(pcm)             # numpy array, -1.0 to 1.0 (needs numpy)
```

- **Lost packets.** When `seq` jumps, `feed()` fills the gap: the decoder carries the sound
  on for the missing 20 ms, so the timing stays right. `decoder.lost` counts them.
  `PcmDecoder(conceal=False)` fills with silence instead.
- **Loudness.** `level_db` is in dB below full scale. At the band's default gain a quiet
  room is about -50 and a voice near the band about -30; a voice across the table is much
  lower. So never compare against a fixed number such as "-40 is speech": compare with
  the room's own background.
- No decoder at all is needed to **save** the sound: `examples/python/ogg_opus.py` wraps
  the packets in a normal `.ogg` file in pure Python.
- Nor to **read** such a file: `audio.ogg_packets(path)` gives the Opus packets of an
  `.ogg` or `.opus` file.

### No band: give the pretend band a voice

A pretend band sends silence. Give it the packets of a recording and it plays them as its
microphone, in a loop. In your own program in `my-project/`, change the connect line:

```python
from pathlib import Path
from vexo_open import audio

voice = Path(__file__).resolve().parent / "vexo-open-kit" / "dashboard" / "speech" / "sample-speech.opus"
band = Band.connect(42, transport="sim", audio_packets=audio.ogg_packets(voice))
```

`sample-speech.opus` is in the kit: 16 seconds of a person reading aloud, from a
public-domain recording (`dashboard/speech/README.md` says where it is from). A file that
`audio_to_text.py` saved from a real band works the same way. The whole program, and the
JavaScript form (`new SimTransport(42, { audioPackets: oggPackets(bytes) })`), are under
["A voice for the pretend band"](api.md#a-voice-for-the-pretend-band).

## In the browser

The browser has its own decoder, `BandAudio`, in `js/src/audio.js`. It uses the decoder
built into Chrome and Edge, so there is nothing to install:

```js
import { Band, BandAudio, toInt16, canDecodeAudio } from './vexo-open-kit/js/src/index.js';

const sound = new BandAudio((samples, info) => {
  // samples: one packet of plain sound; toInt16(samples) gives 16 bit numbers
}, { rate: 16000 });
band.on('audio', (a) => sound.push(a));
await band.configure({ audio: true });
```

`sound.restart()` starts the decoder afresh, `sound.close()` ends it, and
`await canDecodeAudio()` tells whether this browser can do it at all. The details are in the
section "Sound: from Opus packets to samples" in [`docs/api.md`](api.md).

The dashboard's **Live transcript** switch is a working example of the next step: it sends
the band's packets to Deepgram with the key you type into the page, and shows the text.
To try it, run this in `my-project/` (if your web server is not running there already) and
open `http://localhost:8000/vexo-open-kit/dashboard/?band=XX` with your band number:

    python3 -m http.server 8000 --bind 127.0.0.1

The steps are in [`deepgram.md`](deepgram.md#the-dashboard); how the page keeps the key and
what it sends is in [`dashboard/speech/README.md`](../dashboard/speech/README.md).

## One interface, one ready engine

`examples/python/speech.py` puts every speech engine behind the same four calls
(`create`, `start`, `feed`, `stop`). A whole program; save it as `listen.py` in
`my-project/` and run `python3 listen.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "vexo-open-kit" / "examples" / "python"))
import speech                                   # vexo-open-kit/examples/python/speech.py; it finds the library too
from vexo_open.sync import Band

ears = speech.create()                          # Deepgram; or the name of your own engine
ears.start(lambda text, final: print(text))     # partial guesses (final=False) and settled text
band = Band.connect(42)
band.on("audio", lambda a: ears.feed(a.opus, a.seq))
band.configure(audio=True)                      # the light goes solid blue: the microphone is on
band.wait(10)
band.configure(audio=False)
band.disconnect()
print(ears.stop())                              # all the final text
```

`feed()` takes the band's packets; `feed_pcm()` takes plain samples, if you have those.
The first three lines tell Python where `speech.py` is: in the kit's `examples/python/`
folder. Like the three lines that find the library, they are built from the place of your
program file, so they work from whatever folder you start it in. `import speech` also
finds the library (`vexo_open`) for you, which is why the usual three lines are not
needed here. No band?
Use the connect line from ["No band: give the pretend band a voice"](#no-band-give-the-pretend-band-a-voice).

When something is missing (no key, a key Deepgram does not accept, no internet) the
program ends with one sentence that says what to do and names the line of your program,
the same way as a band problem, not with a traceback:

    Problem (speech): Transcription needs a Deepgram key: see docs/deepgram.md. Put your team's key in ...
      (line 8 of listen.py)

`speech.SpeechError` is a `VexoError` with the code `speech`, so `except VexoError as e:`
catches it and `e.message` is the sentence. Because `ears.start(...)` comes before the
band is connected, a missing key stops the program before the microphone goes on.

The engine in the kit is `"deepgram"`: [Deepgram](deepgram.md), an online service. Text
arrives while someone is still speaking, and the finished sentence about half a second
after they stop. It needs your team's key and the internet, and the sound goes to that
company. [`deepgram.md`](deepgram.md) has the measurements, the rules for the key, the
settings and what to do when it does not work. It is the only engine in the kit: there is
no speech model that runs on your own computer here. A team that wants one, or another
service, plugs it in through the interface;
["Plug in your own service"](#plug-in-your-own-service) shows how. Keep the engine's name
in one place (`speech.create(NAME)`), so that changing your mind is a one-word change.

## Measure it on your own recordings

    python3 vexo-open-kit/examples/python/speech_accuracy.py my-recordings --show

`my-recordings` is a folder with sound files (`.ogg` files saved by `audio_to_text.py`, or
16 kHz mono `.wav` files) and a file `voices.json` that says what was really said:

    [ {"file": "one.ogg", "text": "Turn on the light."},
      {"file": "two.ogg", "text": "We need 42 chairs.", "group": "far away"} ]

It plays every recording into the engine at the band's pace and prints the words it got
wrong. The recordings are **sent to Deepgram**, and the time counts against your team's
key, so use a few short ones, of people who agreed. `--engine mine` measures an engine
you added to `speech.py` the same way. The counting rule: capital letters and punctuation
do not count, and "42" equals "forty-two".

## Plug in your own service

An engine is a class with `start`, `feed_pcm` and `stop`. This one is complete; replace
the line in `stop()` with a call to your service:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "vexo-open-kit" / "examples" / "python"))
import speech

class MySpeech(speech.Speech):
    name = "mine"

    def start(self, on_text=None):
        super().start(on_text)               # remembers on_text, empties the text
        self.sound = b""
        return self

    def feed_pcm(self, pcm):                 # 16 kHz, mono, 16 bit samples, as bytes
        self.sound += pcm

    def stop(self):
        answer = "%.1f seconds of sound" % (len(self.sound) / 32000)   # your service here
        self._say(answer, final=True)        # hands the text to on_text
        return self.text

speech.ENGINES["mine"] = MySpeech            # now speech.create("mine") works

ears = speech.create("mine").start(lambda text, final: print("heard:", text))
ears.feed_pcm(bytes(48000))                  # try it with no band: 1.5 seconds of silence
print(ears.stop())
```

Save it as `mine.py` in `my-project/` and run `python3 mine.py`. It prints:

    heard: 1.5 seconds of sound
    1.5 seconds of sound

The first line comes from `on_text`, the second is what `stop()` returns. The last three
lines of the program only try the engine out, with no band. In your own program feed it
the band's packets instead, as `listen.py` above does, with `speech.create("mine")`.

You do not write `feed()`: the base class decodes the band's packets and calls your
`feed_pcm()`. A service that understands Opus can take the packets as they are: write
`feed(packet, seq)` instead. For text while people speak, call
`self._say(text, final=False)` whenever the service sends a guess. Instead of registering
a name you can also pass the class itself: `speech.create(MySpeech)`. Read the service's
key as the next section says. The samples are yours: with `feed_pcm` they can go to any
service, or to a speech model you install yourself.

## Keys

A key is a password that is billed to someone. Each team gets its own Deepgram key at the
event; [`deepgram.md`](deepgram.md#the-key) has the full rules. In short:

- Put it in an **environment variable**: `export DEEPGRAM_API_KEY=...` on macOS and Linux,
  `set DEEPGRAM_API_KEY=...` on Windows (PowerShell: `$env:DEEPGRAM_API_KEY = "..."`), in
  the terminal you start the program from.
  `speech.py` reads it from there; your own code reads it with
  `os.environ["DEEPGRAM_API_KEY"]`.
- Or in a file called **`.env`** in the folder you start the program in, one line:
  `DEEPGRAM_API_KEY=...`. The kit's `.gitignore` keeps `.env` out of git; if your project
  has its own repository, add `.env` to its `.gitignore` before you create the file.
- **Never** put a key in your code, in a commit, in a log or a printed message, in a web
  page that others can open, in a screenshot or a chat, or in the band's storage (anyone
  who connects can read it).
- If a key did end up in git or in a chat, it is burned: tell an organiser, who switches
  it off and gives you a new one. Deleting the line later does not help.

## Licences

Nothing in this part of the kit is copied from elsewhere. The one optional package is
downloaded by `pip` and keeps its own licence.

| What | Licence |
|---|---|
| [PyAV](https://github.com/PyAV-Org/PyAV) (`av`), the decoder package | BSD 3-Clause; its wheels include FFmpeg (LGPL) and libopus (BSD) |

Deepgram is a service, not a part of the kit: its own terms apply to what you send it.
