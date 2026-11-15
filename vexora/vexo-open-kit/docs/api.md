# Library API

The JavaScript and Python libraries do the same things with the same names (JavaScript uses
`camelCase`, Python uses `snake_case`).

**Python on this page is the plain form**, the one most programs use
(`from vexo_open.sync import Band`): a call waits for its answer, there is no `await` and
no `async`, and the program stays alive with `band.wait(5)`. Every Python line on this
page, in a block or in a table, can be pasted into such a program as it is:

    band.set_led("#00ff00")
    led = band.get_led()

**The async form, said once.** Python also has the same API for `asyncio` programs
(`from vexo_open import Band`): the same calls with the same names, with `await` in front
of every call that talks to the band, like the JavaScript column:

    await band.set_led("#00ff00")
    led = await band.get_led()

There `Band.connect` is awaited too, a handler that calls the band is an `async def`, and
`await asyncio.sleep(5)` takes the place of `band.wait(5)`. That is the only difference.
Do not mix the two: an `await` line pasted into a plain program stops it with
`SyntaxError: 'await' outside function`. More: [Python without async](#python-without-async).

```js
import { Band } from './vexo-open-kit/js/src/index.js';

const band = await Band.connect(42);          // "Vexo-HACK-42"
await band.setLed('#00ff00');
console.log('LED is', await band.getLed());
await band.disconnect();
```

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "vexo-open-kit" / "python"))   # where the library is
from vexo_open.sync import Band

band = Band.connect(42)
band.set_led("#00ff00")
print("LED is", band.get_led())
band.disconnect()
```

## Where your code goes

In your own folder, `my-project/`, with the kit inside it under exactly the name
`vexo-open-kit`. It is the same layout as in the [README](../README.md),
[Getting started](getting-started.md) and [AGENTS.md](../AGENTS.md):

    my-project/
      vexo-open-kit/       the kit; leave it as it is
      try.py               your programs and pages
      try.mjs

Type every command in `my-project/`. The library is not installed on your computer. It is
a folder inside the kit (`vexo-open-kit/python/vexo_open/` and `vexo-open-kit/js/src/`),
and your program has to say where that folder is.

**Python.** The first three lines of the program above do that. They build the path from
the place of the program file, so the program can be started from any folder. Without
them Python says `No module named 'vexo_open'`.

Every Python block on this page that starts with those three lines is a whole program. Save it
as `try.py` in `my-project/` and run it there (on Windows type `python`):

    python3 try.py

The other blocks are single lines to put into such a program. No band? Write
`Band.connect(42, transport="sim")`. A band problem that the program does not catch ends
it with one sentence, not a traceback: see [Errors](#errors).

**JavaScript.** There is nothing to install or build. Import the file `js/src/index.js`.
The path in the `import` line starts at the folder of the file that contains the line:

| Your file is in | Import line |
|---|---|
| your own folder with the kit inside it (`my-project/`) | `import { Band } from './vexo-open-kit/js/src/index.js';` |
| `examples/web/` or `examples/node/` | `import { Band } from '../../js/src/index.js';` |
| the kit folder | `import { Band } from './js/src/index.js';` |

The last two rows are for the kit's own files. In a web page, the page must come from a
web server and `Band.connect` must run inside a click handler; see "Shortest working
program: web page" in [AGENTS.md](../AGENTS.md). In Node only the pretend band works. To
try the JavaScript block above, write
`Band.connect(42, { transport: 'sim' })` in it, save it as `try.mjs` in `my-project/` and run:

    node try.mjs

Run in Node as it is printed, without that change, the block ends with one sentence and
exit code 1, because Node has no Bluetooth:
`Problem (no-web-bluetooth): Web Bluetooth only exists in browsers (Chrome or Edge), not in Node. In Node only the pretend band works: Band.connect(42, { transport: 'sim' }). ...`

## Connecting

| What | JavaScript | Python |
|---|---|---|
| Bluetooth (default) | `await Band.connect(42)` | `Band.connect(42)` |
| USB cable | `Band.connect(42, { transport: 'serial' })` | `Band.connect(42, transport="serial")`, optional `port="COM5"` |
| Pretend band | `Band.connect(42, { transport: 'sim' })` | `Band.connect(42, transport="sim")` |
| Whatever is on the cable | `Band.connect(null, { transport: 'serial' })` | `Band.connect(None, transport="serial")` |
| Use only what firmware 1.0 can do | `Band.connect(42, { protocol: '1.0' })` | `Band.connect(42, protocol="1.0")` |
| Leave the band's clock alone | `Band.connect(42, { setClock: false })` | `Band.connect(42, set_clock=False)` |
| Disconnect | `await band.disconnect()` | `band.disconnect()` |

The number is your band number, 1 to 999. 0 is not a band you can ask for.
`Band.connect(0)` fails at once with code `bad-arg`, over Bluetooth and with the pretend
band: "Band numbers are 1 to 999. 0 is what a band has before it gets a number: ask an
organiser to number it." Only the USB cable accepts 0, so that an organiser can reach a
band that has no number yet.

After connecting, the library asks the band who it is. If the band's number is not the one
you asked for, `connect` fails with `VexoError` code `wrong-band` (the message names both
numbers) and closes the link. Pass `null` / `None` as the number to accept any band (USB and
simulator; in a browser also Bluetooth, where the chooser then lists every Vexo Open band).

On the USB cable the band shows up as a port called `/dev/cu.usbmodem...` on a Mac, `COM`
and a number on Windows, `/dev/ttyACM...` on Linux. The Python library finds it by itself. In
a browser the person picks it from the browser's list, which shows every port on the
computer (a web page can only filter that list by USB ID numbers, and the band's are not
final): pick "Vexo Open band". A port that is not a USB device is refused with `port-failed`
before it is opened. On a
Mac, the first time a band is plugged in macOS may ask "Allow accessory to connect?"; until
you press Allow there is no port and connecting fails with `no-port`.

Properties: `band.number`, `band.name` (`"Vexo-HACK-42"`), `band.connected`, `band.transportKind`
/ `band.transport_kind` (`"ble"`, `"serial"`, `"sim"`), `band.streams` (what is on), `band.reconnecting`
(see [auto-reconnect](#when-the-link-drops-auto-reconnect)), `band.takenStreams` / `band.taken_streams`
(see [Two programs on one band](#two-programs-on-one-band)), and `band.info` (the table below).

**When the band cannot be found** (`not-found-nearby`, or it is missing from the browser's
list), the first thing to check is whether something else is connected to it: the
dashboard tab, another program, a teammate's laptop. The band takes one Bluetooth
connection at a time and cannot be found while it is connected, so a double-tap does not
help until that other connection is closed. The message says so: "Could not see
Vexo-HACK-42. If something else is connected to it (the dashboard tab, another program, a
teammate's laptop), close that first: the band takes one Bluetooth connection at a time
and cannot be found while it is connected. Then double-tap the band (its light blinks blue
for a minute), keep it close, and try again." The USB cable is a second way in while
Bluetooth is taken; the two programs then share the band
([Two programs on one band](#two-programs-on-one-band)).

`band.info`:

| `info` field (JS / Python) | Meaning |
|---|---|
| `number` | band number stored in the band |
| `protocol` | `"1.0"` or `"1.1"`: what the band's firmware speaks |
| `firmware` | `"1.1.0"` on every event band. Use `features` to decide what the band can do, never the firmware text |
| `features` | list of names: `fast-motion intervals clock` on an event band (firmware 1.1). Empty on the older firmware 1.0 |
| `hardware` | `1` = XIAO nRF52840 Sense |
| `capabilities` | list of names: `accel gyro events steps mic led battery temperature storage tx-power usb` |
| `maxFrame` / `max_frame` | largest frame in bytes |
| `uptimeMs` / `uptime_ms` | milliseconds since the band started |

`await band.getInfo()` / `band.get_info()` asks again; `await band.getName()` /
`band.get_name()` returns the name the band reports.

## Firmware 1.0 and 1.1

Event bands run firmware 1.1.0. The library asks the band when it connects
(`band.info.firmware`, `band.info.features`) and uses what the band has. You do not choose.

Firmware 1.1 has three things the older firmware 1.0 did not have. Each one has a name in
`band.info.features`:

| Feature | What it adds | Where |
|---|---|---|
| `fast-motion` | motion up to 1666 samples per second, one sensor alone, a number on every sample | [Fast motion](#fast-motion) |
| `intervals` | you choose how often activity, battery, temperature, link and mic level are sent | [How often: intervals](#how-often-intervals) |
| `clock` | the band knows the real time | [Time](#time) |

No event band has firmware 1.0, but the library still knows it. When you ask a 1.0 band for
something only 1.1 has, the call fails with `not-available`, the message says so, and
nothing is sent to the band.

- To write code that runs on both, look first:
  `band.info.features.includes('fast-motion')` / `"fast-motion" in band.info.features`.
- If a 1.1 feature misbehaves on a real band, connect with `{ protocol: '1.0' }` /
  `protocol="1.0"`. The library then treats the band as a 1.0 band: `info.features` is
  empty and nothing from 1.1 is used. `info.protocol` still shows what the band said.
- The pretend band has firmware 1.1. For a pretend band with firmware 1.0, make it yourself
  and pass it as the transport: `new SimTransport(42, { firmware: '1.0' })` /
  `SimTransport(42, firmware="1.0")` (`SimTransport` comes from `js/src/index.js`, and in
  Python from `vexo_open.transports.sim`).

## Turning data on: `configure`

Everything is off until you ask. Streams you do not mention keep their setting.

```js
await band.configure({ motion: 50, events: true, battery: true });
```
```python
band.configure(motion=50, events=True, battery=True)
```

| Key (JS / Python) | Value | Event you get | Fields of the event |
|---|---|---|---|
| `motion` | samples per second: `10`, `25`, `50`, `100`, and with firmware 1.1 also `208`, `416`, `833`, `1666` (see [Fast motion](#fast-motion)). `true` = keep the current rate, 25 at first; `0`/`false` = off; `"fastest"` = the most this link can carry | `motion`, once per sample | `t` (ms), `ax ay az` (g), `gx gy gz` (degrees/second), `n` (sample number; `null` / `None` on firmware 1.0) |
| `sensors` | `"accel"`, `"gyro"` or `"both"`: what a motion sample holds. It goes with `motion` and is not a stream of its own. One sensor alone needs firmware 1.1 | | the sensor you left out is `null` / `None` in every `motion` event |
| `events` | on/off | `event`, plus one named after the kind: `tap`, `double-tap`, `free-fall`, `tilt`, `orientation`, `motion-start`, `motion-stop` | `t`, `kind`, `arg`; `orientation` also has `face`: `+x -x +y -y +z -z` |
| `activity` | on/off, or seconds between readings, or `"fastest"` | `activity`, once a second | `t`, `steps` (the band's lifetime total: it goes on from one connection to the next and is kept across restarts. For steps since you connected, subtract the first `steps` you get), `cadence` (steps/minute), `state` (`still`, `walk`, `run`), `motion` (g) |
| `audio` | on/off | `audio`, 50 per second | `seq`, `opus` (one raw Opus packet: 16 kHz mono, 20 ms; to get samples see [Sound](#sound-from-opus-packets-to-samples)) |
| `battery` | on/off, or seconds between readings, or `"fastest"` | `battery`, once at the start and then when it changes (or every so many seconds, if you gave a number) | `percent`, `millivolts`, `state` (`battery`, `charging`, `charged`), `charging` (true/false) |
| `temperature` | on/off, or seconds between readings (default 5), or `"fastest"` | `temperature` | `t`, `celsius` (chip temperature, not skin) |
| `link` | on/off. Bluetooth only: over the USB cable `configure` fails with `not-available` and turns nothing on, so leave `link` out there. Or seconds between readings, or `"fastest"` | `link`, once a second | `t`, `rssi` (dBm, or `null` / `None` when the band has no reading), `txPower` / `tx_power` (dBm), `phy` (`1M`, `2M`) |
| `micLevel` / `mic_level` | on/off, or seconds between readings, or `"fastest"` | `mic-level`, 10 per second | `t`, `peakDb` / `peak_db`, `rmsDb` / `rms_db` (dB below the loudest measurable; 0 = loudest, 96 = silence), `level` (0..1, 1 = loudest), all measured over the time since the reading before: see [Loudness](#loudness-what-a-mic-level-event-measures). Turns the microphone on, so the light goes blue and the LED is locked, like `audio` |

Every event in this table also has `time`: the real time of the reading. See [Time](#time).

### Loudness: what a `mic-level` event measures

- **Over what time.** `peakDb` / `peak_db` and `rmsDb` / `rms_db` describe the sound since
  the reading before. On firmware 1.0 that is always the last 100 ms. On firmware 1.1 it is
  the last interval: 0.1 seconds unless you chose another one (0.02 to 10 seconds, see
  below). So at `micLevel: 0.02` each reading covers 20 ms, and a clap shows up as one or
  two loud readings.
- **The unit.** Both are whole decibels below the loudest sound the microphone can measure
  ("full scale"): 0 = as loud as it can measure, 96 = silence. A smaller number is louder.
- **What they follow.** They are measured on the band before the sound is squeezed into
  Opus, so they follow the mic gain (`setMicGain` / `set_mic_gain`): 10 dB less gain reads
  about 10 dB quieter.
- **What is not specified.** The contract says no more than this. By their names `rmsDb`
  is the average loudness over that time and `peakDb` its loudest instant, but how the band
  works them out (which samples, any filter or weighting) is not written down. Do not
  build on more than "peak is at least as loud as rms".
- **`level`** is worked out by the library from `rmsDb` alone: `level = 1 - rmsDb / 96`,
  kept between 0 and 1. `peakDb` plays no part in it.
- **In a real room `level` never gets near 0.** A real band at its default gain (+20 dB)
  reads `rmsDb` about 50 in a quiet room, which is `level` about 0.5, and about 20 to 30
  for speech near the band (`level` about 0.7 to 0.8). So a bar drawn straight from
  `level` is half full in silence. Pick your own scale from `rmsDb`, as the dashboard
  does: its bar is empty at 52 and full at 12 (`loudnessBar` in `dashboard/logic.js`).
  Measure your own room first; the numbers move with the gain and the distance.

### How often: intervals

For `activity`, `battery`, `temperature`, `link` and `micLevel` / `mic_level` you can give
a number instead of `true`: the seconds between two readings. Fractions are allowed. A
number also turns the stream on. `true` keeps the pace it has, and `0` or `false` turns
it off.

```js
await band.configure({ micLevel: 0.02, battery: 60 });      // loudness 50 times a second, battery once a minute
```
```python
band.configure(mic_level=0.02, battery=60)
```

| Stream | Seconds allowed (firmware 1.1) | Without a number |
|---|---|---|
| `activity` | 0.1 to 3600 | 1 |
| `battery` | 1 to 3600 | only when it changes |
| `temperature` | 0.1 to 3600 | 5 |
| `link` | 0.1 to 3600 | 1 |
| `micLevel` / `mic_level` | 0.02 to 10, in steps of 0.02 | 0.1 |

The limits come from the band. A number outside them fails with `bad-arg`, the message
names the range, and nothing in that `configure` call is changed or started. A `mic-level`
event measures the sound since the one before it, so a longer interval also means a
longer stretch of sound in each reading.

**The shortest interval, on any band.** Write `"fastest"` instead of a number and the
library asks the band for the shortest interval it allows and sets that (the first number
in the table). This is the way to write one program for both firmware versions: on a band
with firmware 1.0, `"fastest"` does not fail. Such a band has one fixed pace for each
stream (the last column), so the stream is simply turned on at that pace; only
`temperature` gets faster there (every second). A number on firmware 1.0 still fails with
`not-available`, as before.

| | JavaScript | Python |
|---|---|---|
| Turn on at the shortest interval | `await band.configure({ micLevel: 'fastest' })` | `band.configure(mic_level="fastest")` |
| The same without turning it on or off; the answer says what you got | `await band.setInterval('micLevel', 'fastest')` | `band.set_interval("mic_level", "fastest")` |

The answer of `setInterval(..., 'fastest')` / `set_interval(..., "fastest")` is the pace
in effect: `seconds` 0.02 on firmware 1.1, and 0.1 on firmware 1.0 (with `min` and `max`
the same number, because that band cannot change it; nothing is sent to it). Use it to
know how many readings a second to expect.

To change the pace without turning a stream on or off, and to read it back:

| What | JavaScript | Python |
|---|---|---|
| Set | `await band.setInterval('battery', 60)` | `band.set_interval("battery", 60)` |
| Read | `await band.getInterval('battery')` | `band.get_interval("battery")` |

Both return `stream`, `seconds`, `min` and `max` (the range the band allows, in seconds).
`setInterval('battery', 0)` / `set_interval("battery", 0)` goes back to "only when it
changes". Like every setting, intervals go back to their defaults when you disconnect.

Intervals need firmware 1.1. On a band with firmware 1.0 only `temperature` takes a
number, in whole seconds from 1 to 255; a number for one of the other four fails with
`not-available`, and so does `getInterval` / `get_interval`. `"fastest"` works on both.

Listening:

| What | JavaScript | Python |
|---|---|---|
| Listen | `band.on('motion', fn)` (returns a function that stops listening) | `band.on("motion", fn)` or `@band.on("motion")` above a function |
| Stop listening | `band.off('motion', fn)` | `band.off("motion", fn)` |

The event names are the ones in the tables on this page: `motion`, `event`, the seven
gestures, `activity`, `audio`, `battery`, `temperature`, `link`, `mic-level`, `notice`,
`disconnect`, `reconnecting`, `reconnected`. In `on` and in `off`, any other name fails at once with
`VexoError` code `bad-event`, and the message lists the events: a misspelt name
(`"tapp"`) or a stream name used as an event (`"mic_level"` or `"micLevel"` for the event
`"mic-level"`) would otherwise wait for ever for something that never comes.

Other events:

| Event | Fields |
|---|---|
| `disconnect` | `reason` (table below). With auto-reconnect also `reconnect: "gave-up"`, or `found` and `wanted` for `wrong-band`. With reason `session-ended` also `message` |
| `reconnecting`, `reconnected` | Only with [auto-reconnect](#when-the-link-drops-auto-reconnect) |
| `notice` | `kind` (`low-battery`, `critical-battery`, `stream-dropped`, `usb-power`), `arg`, `time` |

| `disconnect` reason | Meaning |
|---|---|
| `closed` | Your program called `disconnect()` |
| `lost` | The link dropped: out of range, band switched off, cable pulled, battery empty |
| `restart` | You called `restart()` |
| `sleep` | You called `sleep()` over Bluetooth |
| `wrong-band` | Auto-reconnect only: a band with another number answered, so the library stopped |
| `session-ended` | USB cable only: the library found the streams you switched on switched off on the band. The event also has `message`, a sentence to show. See [below](#over-the-usb-cable-when-the-band-ends-the-session) |

While `audio` or `micLevel` / `mic_level` is on, the microphone is on: the band shows a solid
blue light and refuses LED commands (see Errors). Turn both off to use the LED again; your
LED setting comes back by itself.
Motion readings travel in small packets, so several `motion` events arrive together. On
firmware 1.0 a packet holds 10 readings: at 10 per second they arrive once a second. On
firmware 1.1 a packet holds at most 50 ms of readings.

## Sound: from Opus packets to samples

The band sends its microphone as Opus packets (squeezed sound: 16 kHz, mono, 20 ms each,
50 a second). Most projects want plain samples (PCM) instead: for a speech service, a
graph, or their own maths. Both libraries have a ready decoder. Each one fills in packets
that were lost on the way, so the timing stays right. `docs/speech.md` goes from there to
text.

| | JavaScript (Chrome or Edge) | Python |
|---|---|---|
| Needs | Nothing to install: it uses the decoder built into the browser (WebCodecs), so the page must come from `localhost` or `https` | `python3 -m pip install av` (on Windows: `python -m pip install av`). No version on purpose: tested with 15.1.0 on Python 3.9 and 19.0.2 on Python 3.12 |
| Make it | `const sound = new BandAudio((samples, info) => { ... })` | `decoder = PcmDecoder()` (from `vexo_open.audio`) |
| Feed it | `band.on('audio', (a) => sound.push(a))` | `pcm = decoder.feed(a)` inside your `audio` handler |
| You get | `samples`: a `Float32Array`, 20 ms at 16000 samples a second, values -1 to 1. `toInt16(samples)` makes 16 bit numbers | `pcm`: `bytes`, 320 samples of 16 bits (little endian). `audio.to_float(pcm)` makes numbers from -1 to 1 |
| Lost packets so far | `info.lost`, `sound.lost` | `decoder.lost` |
| A hole in the sound | Up to 50 lost packets in a row (1 second) are filled in. A longer hole is a break: counted in `lost`, not filled | The same |
| Another sample rate | `new BandAudio(fn, { rate: 48000 })` (good for playing) | 16000 only |
| After the stream started again | `sound.restart()` (do it on the `reconnected` event) | make a new `PcmDecoder()` |
| When done | `sound.close()` | `decoder.flush()` |
| A whole recording at once | `oggPackets(bytes)` reads the packets of an `.ogg` file | `audio.ogg_packets(path)` reads the packets of an `.ogg` file (nothing to install), `audio.decode_packets(packets)`, `audio.write_wav(path, pcm)` |

```js
// BandAudio comes from the same file as Band:  import { Band, BandAudio } from '.../js/src/index.js'
const sound = new BandAudio((samples) => {
  let sum = 0;
  for (const v of samples) sum += v * v;
  console.log('loudness', Math.sqrt(sum / samples.length).toFixed(3));
});
band.on('audio', (a) => sound.push(a));
await band.configure({ audio: true });        // the light goes solid blue: the microphone is on
```

`BandAudio` throws a `VexoError` with code `not-available` in a browser that has no audio
decoder (Firefox, Safari, Node); `await canDecodeAudio()` tells you beforehand. In Python,
`vexo_open.audio` also has `level_db(pcm)` (loudness) and `normalise(pcm)` (a quiet
recording made louder), which need nothing installed.

To turn the samples into text see `docs/speech.md`. `examples/python/speech.py` gives one
small interface (`start`, `feed`, `stop`) over Deepgram, an online speech-to-text service
that needs your team's key ([`docs/deepgram.md`](deepgram.md)), and another speech service
fits behind the same three calls.

**The first `PcmDecoder()` of a Python program takes a few seconds**, up to about 7 on a
slow laptop: Python is loading the `av` package. Nothing is wrong. Make the decoder once,
before you turn the microphone on, and print a line first so that nobody thinks the
program hangs:

```python
from vexo_open.audio import PcmDecoder

print("Loading the sound decoder...")
decoder = PcmDecoder()                        # a few seconds, once
band.on("audio", lambda a: print(len(decoder.feed(a)), "bytes of samples"))
band.configure(audio=True)                    # the light goes solid blue: the microphone is on
```

Small print for Python: the very first `feed()` returns 304 samples instead of 320 and
`flush()` returns the last 16; `to_float` needs numpy; `PcmDecoder(conceal=False)` fills a
lost packet with silence instead of smoothing over it; without `av` installed the decoder
raises `VexoError` with code `not-available` and names the install command. That command
has no version on any page of the kit (one version of `av` does not install on every
Python).

### A voice for the pretend band

The pretend band has no microphone. Its `audio` packets are real Opus, but they decode to
silence, so a program that listens hears nothing. To give it a voice, hand it the Opus
packets of a sound file: it plays them as its `audio` stream, 50 a second, and starts
again at the end. The kit has such a file, `dashboard/speech/sample-speech.opus`: 16
seconds of a person reading aloud, from a public-domain recording. Any other Ogg Opus file
with 20 ms packets works too, for example one that `examples/python/audio_to_text.py`
saved from a real band.

Python (a whole program; needs nothing installed):

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "vexo-open-kit" / "python"))   # where the library is
from vexo_open import audio
from vexo_open.sync import Band

voice = Path(__file__).resolve().parent / "vexo-open-kit" / "dashboard" / "speech" / "sample-speech.opus"
band = Band.connect(42, transport="sim", audio_packets=audio.ogg_packets(voice))
packets = []
band.on("audio", packets.append)              # each one has .opus (one Opus packet) and .seq
band.configure(audio=True)
band.wait(2)
band.disconnect()
print(len(packets), "packets of a voice; the first one is", len(packets[0].opus), "bytes")
```

From there the packets go where a real band's would: into `PcmDecoder` (above), or into
`ears.feed(a.opus, a.seq)` for text ([`docs/speech.md`](speech.md)).

JavaScript (Node; save it as `voice.mjs` in `my-project/` and run `node voice.mjs`):

```js
import { readFileSync } from 'node:fs';
import { Band, SimTransport, oggPackets } from './vexo-open-kit/js/src/index.js';

const voice = oggPackets(readFileSync('./vexo-open-kit/dashboard/speech/sample-speech.opus'));
const band = await Band.connect(42, { transport: new SimTransport(42, { audioPackets: voice }) });
let count = 0;
band.on('audio', (a) => { count += 1; });     // a.opus is one Opus packet, a.seq its number
await band.configure({ audio: true });
await new Promise((done) => setTimeout(done, 2000));
await band.disconnect();
console.log(count, 'packets of a voice');
```

In a web page there is no `readFileSync`. Fetch the file from your own server instead:
`oggPackets(new Uint8Array(await (await fetch('./vexo-open-kit/dashboard/speech/sample-speech.opus')).arrayBuffer()))`.
Then `band.on('audio', (a) => sound.push(a))` with a `BandAudio` gives you the samples.

Two things to know. The loudness readings (`mic-level`) do not follow the file: the
pretend band cannot hear, and makes its loudness up (see
[The pretend band](#the-pretend-band-making-things-happen)). And a real band needs none of
this: remove `transport` and `audio_packets` / `audioPackets`, and the same program
listens to the room.

## Fast motion

Needs firmware 1.1 (`fast-motion` in `band.info.features`), which
[every event band has](#firmware-10-and-11).

```js
band.on('motion', (m) => console.log(m.n, m.t, m.ax, m.ay, m.az));
await band.configure({ motion: 416, sensors: 'accel' });
```
```python
band.on("motion", lambda m: print(m.n, m.t, m.ax, m.ay, m.az))
band.configure(motion=416, sensors="accel")
```

- **Rates.** 10, 25, 50, 100, 208, 416, 833 or 1666 samples per second. Any other number
  fails with `bad-arg`.
- **Sensors.** `"accel"` (acceleration only), `"gyro"` (turning speed only) or `"both"`
  (what you get if you never say). With one sensor a sample is half the size, so the link
  can carry twice as many. `sensors` on its own, without `motion`, changes the sensors and
  leaves the stream on or off as it was.
- **Limits.** How fast you can go depends on the link and on the sensors:

  | `sensors` | Bluetooth | USB cable |
  |---|---|---|
  | `"both"` | 416 | 833 |
  | `"accel"` | 833 | 1666 |
  | `"gyro"` | 833 | 833 |

  `motion: 'fastest'` / `motion="fastest"` picks the highest rate in this table for your
  link and your sensors. `await band.motionLimits()` / `band.motion_limits()` gives
  the same numbers for the link you are on: `rates` (every rate this band knows), `accel`,
  `gyro`, `both` (the most for each choice) and `transport`.

```js
console.log(await band.motionLimits());     // { rates: [10, 25, ...], accel: 833, gyro: 833, both: 416, transport: 'ble' }
await band.configure({ motion: 'fastest', sensors: 'accel' });
console.log(band.streams.motion);           // 833 over Bluetooth
```
```python
print(band.motion_limits())
band.configure(motion="fastest", sensors="accel")
print(band.streams["motion"])               # 833 over Bluetooth
```

- **Too fast.** Asking for more than the table allows fails with `denied` before anything
  is sent, and nothing is changed or started. The message names the way out, for example:
  "833 samples per second with both sensors is too much for Bluetooth here: the most is 416
  with both, 833 with accel alone, 833 with gyro alone. Use sensors: 'accel', or sensors:
  'gyro', or the USB cable (up to 833 with both, 1666 with accel alone), or a lower rate.
  Nothing was changed." (Python writes `sensors="accel"` in the same sentence.)
- **What a `motion` event holds.**

  | Field | Meaning |
  |---|---|
  | `t` | the band's own clock in milliseconds, with a fraction: at 833 per second two samples are 1.2 ms apart. It goes up with every sample: never the same value twice and never backwards, also after lost samples and after the stream starts again. (The band stamps each packet in whole milliseconds; where two packets would overlap by a fraction of a millisecond, the library spreads the later one's samples over the time that is left.) Only a new connection can make it smaller, or the counter starting again at 0 after 49.7 days |
  | `ax ay az` | acceleration in g; `null` / `None` with `sensors` `"gyro"` |
  | `gx gy gz` | turning speed in degrees/second; `null` / `None` with `sensors` `"accel"` |
  | `n` | the number of the sample: 0, 1, 2, ... It starts at 0 when the stream starts and when you change the rate or the sensors. A jump (41, 42, 57) means that many samples were lost on the way (here 14) |
  | `time` | the real time of the sample, see [Time](#time) |

- **Use `t`, not the rate, to place samples in time.** From 208 per second up, the motion
  chip runs on its own clock, which can be a few percent off: "416 per second" may really
  be 413. On one real band "833" measured 865 a second, about 4 percent fast. That is
  normal and not a fault: `fast_motion.py` prints "Asked for 833 a second, measured 865.2
  a second." for it. The band measures the real spacing, and `t` and `time` already include
  it: the time of each sample is the truth, the rate is only a name.
- **With the microphone.** Over Bluetooth, `audio` and fast motion share the same link.
  With `audio` on, stay at 208 with both sensors or 416 with one, and listen for the
  `notice` event with kind `stream-dropped`: it means the link could not keep up.
- **`setMotion` / `set_motion`** takes `rate` and `sensors` too and does not turn the
  stream on. `band.streams.motion` shows the rate as before.
- **On a band with firmware 1.0** the rates are 10, 25, 50 and 100 with both sensors.
  Anything else fails with `not-available` and a message like "This band's firmware (1.0.0)
  can do 10, 25, 50 or 100 samples per second with both sensors. 416 per second needs
  firmware 1.1." There `"fastest"` means 100, `n` is `null` / `None`, and
  `motionLimits` / `motion_limits` gives `rates` 10 to 100, `both` 100, and `accel` and
  `gyro` `null` / `None`.

Programs to try it with: [`examples/python/fast_motion.py`](../examples/python/fast_motion.py)
(a real band or the pretend one) and
[`examples/node/fast-motion.mjs`](../examples/node/fast-motion.mjs) (the pretend band).

## Time

Every event with data has two times:

- `t` is the band's own counter: milliseconds since the band started. Good for the time
  between two readings. (`audio`, `battery` and `notice` events have no `t`.)
- `time` is the real time of the reading, the kind a clock on the wall shows. In
  JavaScript it is milliseconds since 1970, like `Date.now()`: `new Date(e.time)`. In
  Python it is seconds since 1970 as a float, like `time.time()`:
  `datetime.fromtimestamp(e.time)`.

```js
band.on('tap', (e) => console.log('tap at', new Date(e.time).toLocaleTimeString()));
await band.configure({ events: true });
console.log(await band.time());             // { time: 1794484800123, set: true, t: 52340, source: 'band' }
console.log(band.clock);                    // 'band'
```
```python
band.on("tap", lambda e: print("tap at", e.time))
band.configure(events=True)
print(band.time())
print(band.clock)                           # "band"
```

Where `time` comes from depends on the firmware. `band.clock` tells you which:

| `band.clock` | Band | How `time` is worked out |
|---|---|---|
| `"band"` | firmware 1.1 | The band has a clock. The library sets it to your computer's time when it connects, and again after every auto-reconnect. The band then says which real time goes with its counter `t`, and the library turns the `t` of every reading into `time` |
| `"computer"` | firmware 1.0 | **There is no clock on the band.** `time` is worked out from your computer's clock and the band's uptime: the library notes your computer's time at the moment the band reports how long it has been on, and counts from there |

Either way `time` is only as right as your computer's clock, give or take the time one
message takes to cross the link (a few hundredths of a second over Bluetooth).

`audio`, `battery` and `notice` events are not stamped by the band on any firmware. For
`battery` and `notice`, `time` is the moment the event arrived on your computer. For
`audio`, it is the arrival of the first packet after the stream started, plus 20 ms for
each packet after it (lost packets are counted too).

| What | JavaScript | Python |
|---|---|---|
| Ask the band what time it has | `await band.time()` | `band.time()` |
| Set the band's clock to your computer's time now | `await band.setTime()` | `band.set_time()` |
| Set it to a time you give | `await band.setTime(Date.UTC(2026, 10, 12))` (ms) | `band.set_time(1794441600.0)` (seconds) |
| Where `time` comes from | `band.clock` | `band.clock` |
| Connect without setting the band's clock | `Band.connect(42, { setClock: false })` | `Band.connect(42, set_clock=False)` |

`time()` returns four things:

| Field | Meaning |
|---|---|
| `time` | the time now, in the unit above (ms in JavaScript, seconds in Python) |
| `set` | true if the band's own clock is set |
| `t` | the band's millisecond counter at that same instant |
| `source` | `"band"`, or `"computer"` when the band has no clock or it is not set |

- `setTime` / `set_time` returns the same four things. It needs firmware 1.1: on a 1.0
  band it fails with `not-available`. The band takes times from the year 2020 to 2100.
- On a 1.0 band `time()` still works: it gives your computer's time, with `set` false and
  `source` `"computer"`.
- The band keeps its clock while it has power, also while nothing is connected. It loses
  it when it restarts or the battery runs empty. That is why the library sets it on every
  connect.
- The clock is not protected: any program that connects can set it. With
  `setClock: false` / `set_clock=False` the library does not set it. It reads the band's
  clock instead and uses it if someone has set it; if not, `band.clock` is `"computer"`.

### `t` and `time` when the link comes back

`t` belongs to the band: it counts the milliseconds since the band was switched on or
restarted, whether anything is connected or not. `time` belongs to the link: on every
connect and every auto-reconnect the library pairs the band's counter with real time
again (firmware 1.1: it sets the band's clock from your computer and reads the pair back;
firmware 1.0: it notes your computer's time next to the band's uptime).

| What happened | `t` | `time` |
|---|---|---|
| The link dropped and auto-reconnect got it back (the band stayed on) | Goes on. It does not start again: the first reading after the gap has a `t` that is larger by the length of the gap | Goes on as real time. It is paired again at the reconnect, so it follows your computer's clock as it is then: if that clock was corrected meanwhile, `time` steps by the same amount while `t` does not |
| You called `restart()` and connected again | Starts again near 0, so it is smaller than before the restart. The library does not reconnect by itself after `restart()` | Right again from the new connect on. A restart makes a firmware 1.1 band forget its clock; the library sets it when you connect (with `setClock: false` / `set_clock=False` it stays unset and `band.clock` is `"computer"`) |
| The band restarted by itself (reset button, empty battery) while auto-reconnect was on | Starts again near 0 after `reconnected` | Right again from `reconnected` on, as above |
| You disconnected and connected again later (the band stayed on) | Goes on, as in the first row | Paired again at the new connect |

So: to measure the time between two readings use `t`, but only between readings of the
same run of the band; a `t` that is smaller than the one before means the band restarted
(or, after 49.7 days, that the counter wrapped). To put readings on a calendar, or to
compare readings from before and after a restart, use `time`. `n`, the sample number of
fast motion, starts at 0 on every new link. On both firmware versions the result is the
same; only where the pairing comes from differs (`band.clock`).

## Controls

| What | JavaScript | Python |
|---|---|---|
| LED colour | `setLed('#ff8800')` or `setLed(255, 136, 0)` | `set_led("#ff8800")` or `set_led(255, 136, 0)` |
| LED options | `setLed('#ff8800', { brightness: 0.5, pattern: 'blink', period: 500 })` | `set_led("#ff8800", brightness=0.5, pattern="blink", period=500)` |
| LED off | `ledOff()` | `led_off()` |
| Read the LED setting | `getLed()` -> `{ r, g, b, brightness, pattern, period }` | `get_led()` |
| Flash white to find it | `identify()` or `identify(10)` (seconds, 1..30, default 5) | `identify()` / `identify(10)` |
| Motion sensor | `setMotion({ rate: 100, sensors: 'both', accelRange: 8, gyroRange: 500 })` | `set_motion(rate=100, sensors="both", accel_range=8, gyro_range=500)` |
| Read motion settings | `getMotion()` -> `{ rate, sensors, accelRange, gyroRange }` | `get_motion()` -> `rate, sensors, accel_range, gyro_range` |
| Fastest motion on this link | `motionLimits()` -> `{ rates, accel, gyro, both, transport }` | `motion_limits()` -> `rates, accel, gyro, both, transport` |
| How often a stream sends | `setInterval('battery', 60)`, `getInterval('battery')` -> `{ stream, seconds, min, max }` | `set_interval("battery", 60)`, `get_interval("battery")` -> `stream, seconds, min, max` |
| The band's clock | `time()`, `setTime()` -> `{ time, set, t, source }` | `time()`, `set_time()` -> `time, set, t, source` |
| Which events, tap sensitivity | `setEvents({ kinds: ['tap', 'double-tap'], tapThreshold: 1.2 })` | `set_events(kinds=["tap", "double-tap"], tap_threshold=1.2)` |
| Read event settings | `getEvents()` -> `{ kinds, tapThreshold }` | `get_events()` -> `kinds, tap_threshold` |
| Mic gain | `setMicGain(10)` (dB, -20..+20 in half steps, default +20) | `set_mic_gain(10)` |
| Read mic settings | `getMic()` -> `{ gainDb, sampleRate, frameMs, channels, bitrate }` | `get_mic()` -> `gain_db, sample_rate, frame_ms, channels, bitrate` |
| Radio power | `setTxPower(-12)` (dBm: -20 -16 -12 -8 -4 0 4) | `set_tx_power(-12)` |
| Read radio state | `getRadio()` -> `{ txPower, rssi, phy, intervalMs, mtu }` | `get_radio()` -> `tx_power, rssi, phy, interval_ms, mtu` |
| Battery once | `getBattery()` -> same fields as the `battery` event, without `time` | `get_battery()` |
| Restart | `restart()` | `restart()` |
| Sleep | `sleep()` | `sleep()` |
| Set the band number (USB only) | `setBandNumber(42)` | `set_band_number(42)` |

A band that has no number yet calls itself `Vexo-HACK-00`. Reach it over the USB cable with `null` / `None` (or 0) as the number, then set its number; 0 is refused on every other link.
| Any command by contract name | `command('LED_GET')`, `command('IMU_SET', { rate_hz: 50, accel_range_g: 2, gyro_range_dps: 2000 })` | `command("LED_GET")`, `command("IMU_SET", rate_hz=50, accel_range_g=2, gyro_range_dps=2000)` |

Details:

- **LED.** There is one light, and every program connected to the band shares it: one on
  Bluetooth and one on the USB cable set the same LED. The last `setLed` wins, and the
  light goes off by itself only when the last program disconnects. So `led.py --usb`, run
  while the dashboard is connected, changes the dashboard's light, and when it ends the
  band keeps showing the pattern it set last (blinking white), not "off" and not the
  dashboard's colour. If your light changed by itself, another program is connected
  ([Two programs on one band](#two-programs-on-one-band)).
  `setLed`, `ledOff` and `identify` fail with `denied` while the microphone is on
  (`audio` or `micLevel` / `mic_level`). `brightness` is 0..1 (default 1). `pattern` is `solid` (default), `blink`,
  `breathe`, `flash`, `rainbow` or `off`. `period` is milliseconds per cycle, 100..10000
  (default 1000). `getLed` returns `brightness` as 0..1.
- **Motion.** In `setMotion`, anything you leave out keeps its value. `accelRange` is 2, 4, 8
  or 16 (g); `gyroRange` is 0 (gyro off), 250, 500, 1000 or 2000 (degrees/second).
  **After every connect the ranges are 2 g and 2000 degrees/second.** A reading beyond the
  range is cut off at the range, so with the default a hard shake, a punch or a clap reads
  exactly 2.0 and its real peak is lost. Widen the range before you measure such a thing:
  `band.setMotion({ accelRange: 8 })` / `band.set_motion(accel_range=8)`. Like every
  setting, the range is back at its default after a disconnect.
  `configure({ motion: 50 })` changes only the rate. The two ranges apply at every rate.
  `sensors` is `accel`, `gyro` or `both`; on firmware 1.0 it is always `both`.
  See [Fast motion](#fast-motion).
- **Any command.** The names are the ones in [`contract/contract.md`](../contract/contract.md),
  with the contract's field names and raw units. Firmware 1.1 added `MOTION2_SET`,
  `MOTION2_GET` (fast motion: `rate_hz`, `sensors` 1 accel, 2 gyro, 3 both),
  `STREAM_SET_INTERVAL`, `STREAM_GET_INTERVAL` (`stream` is the bit number, `interval_ms`),
  `TIME_SET` and `TIME_GET` (`unix_ms`), and stream bit 8 for fast motion:
  `command('STREAM_START', { mask: 256 })` / `command("STREAM_START", mask=256)`. On
  firmware 1.1 `configure` always uses that stream for `motion`; the older one is still
  there as `mask: 1` and makes the same `motion` events. `command` sends what you write,
  to any band: a band with firmware 1.0 answers a 1.1 command with `unknown-opcode`.
- **Events.** `kinds` may contain `tap`, `double-tap`, `free-fall`, `tilt`, `orientation`,
  `motion` (start and stop together). `tapThreshold` is in g, 0.2..8 (default 0.8; lower =
  more sensitive).
- **Radio.** `rssi` is `null` / `None` when the band has no reading. That can happen over
  Bluetooth too, so check before you use it. Over USB with no Bluetooth link, `getRadio`
  gives `rssi` and `phy` `null` / `None`, `intervalMs` 0 and `mtu` 0.
- **Sleep.** The band turns all streams and the LED off. Over Bluetooth the link then
  closes (`disconnect` with reason `sleep`) and the band cannot be found until someone
  double-taps it. Over the USB cable the link stays open and the band keeps answering:
  there is no `disconnect` event, and `band.streams` shows everything off.
- All settings go back to their defaults when you disconnect. Only storage and the band
  number are kept.

## Storage

A small key-value store on the band: keys are numbers 0..999, values are 1..232 bytes.
**Anything you store can be read, changed or wiped by the next person who connects.**

| What | JavaScript | Python |
|---|---|---|
| Save | `band.storage.put(7, 'hello')` or `put(7, bytes)` | `band.storage.put(7, "hello")` or `put(7, b"...")` |
| Load bytes | `band.storage.get(7)` -> `Uint8Array`, or `null` if empty | `band.storage.get(7)` -> `bytes` or `None` |
| Load text | `band.storage.getText(7)` | `band.storage.get_text(7)` |
| Delete | `band.storage.delete(7)` | `band.storage.delete(7)` |
| How full | `band.storage.info()` -> `{ keysMax, valueMax, freeBytes, usedKeys }` | `band.storage.info()` -> `keys_max, value_max, free_bytes, used_keys` |
| Erase everything | `band.storage.wipe()` | `band.storage.wipe()` |

Text is stored as UTF-8.

Over the USB cable a value can, in rare cases, arrive cut short and still look complete; if a value must be right, read it twice and compare.

- **Speed.** The band accepts 10 writes (`put`, `delete`) a second and answers "busy" to
  more. The library then waits and sends again by itself, for up to 3 seconds. So a loop of
  30 `put` calls works; it just takes a few seconds. After 3 seconds of "busy" the call
  fails with `busy`.
- **Wipe.** Erasing takes the band a while. `wipe()` returns only when the erase has
  finished, so a `put` right after it works. It gives up after 30 seconds with `timeout`;
  change that with `wipe({ timeoutSeconds: 60 })` / `wipe(timeout=60)`. `not-available`
  means the band could not erase its storage: call `wipe()` again.
- **Size.** An empty store has 242,164 bytes free (`info()` gives the real number). Each
  key costs 8 bytes plus its value rounded up to a multiple of 4. 1000 keys of 232 bytes
  fit. When it is full, `put` fails with `no-space`.

## When the link drops: auto-reconnect

By default a dropped link stays closed: you get `disconnect` with reason `lost` and must
connect again yourself. Ask for auto-reconnect when you connect, and the library does it:

```js
const band = await Band.connect(42, { autoReconnect: true });   // optional: reconnectSeconds: 120
```
```python
band = Band.connect(42, auto_reconnect=True)                     # optional: reconnect_seconds=120
```

What happens when the link is lost:

1. You get `reconnecting` with `reason` (`lost`, or `session-ended` over the USB cable) and `seconds` (how long it will try, 120
   unless you changed it). `band.reconnecting` is true, `band.connected` is false and
   `band.streams` shows everything off. No `disconnect` event.
2. The library tries about once a second. The band can be found again for 2 minutes after
   a dropped link, with no double-tap, which is why the default is 120 seconds.
3. When the band answers, the library sets again what you had set on this link: the LED,
   the motion and event settings (the fast motion rate and sensors too), mic gain, radio
   power, every stream interval, and every stream that was on. On firmware 1.1 it also
   sets the band's clock again (with `setClock: false` / `set_clock=False` it reads it
   again). Sample numbers (`n`) start again at 0. Your handlers stay. Then you get `reconnected` with `attempts`
   (how many tries), `seconds` (how long it took) and `failed` (contract names of settings
   the band refused, normally an empty list). `identify` is not repeated.
4. If the band does not come back in time you get `disconnect` with reason `lost` and
   `reconnect: "gave-up"`.

While it is reconnecting, every command fails at once with `not-connected`: wait for
`reconnected`. `disconnect()` stops the attempts (`disconnect` with reason `closed`).
In Python, `band.wait()` keeps waiting through a reconnect.

It never reconnects:

- after your program called `disconnect()`, `restart()` or `sleep()`,
- after a `critical-battery` notice (the band stays off until it is charged),
- when a band with another number answers: you get `disconnect` with reason `wrong-band`
  and the two numbers in `found` and `wanted`.

In a browser no new click is needed: the page reuses the band or the port you chose. The
dashboard connects this way. A program to try it with:
[`examples/python/reconnect.py`](../examples/python/reconnect.py).

### Over the USB cable: when the band ends the session

Over the USB cable the library watches the streams you switched on. If they go silent, it
asks the band once which streams are on. While data arrives it asks nothing. If the band
says your streams are off, the library closes the port and ends the link with reason
`session-ended`; the event has a `message` to show.

- With auto-reconnect you get `reconnecting` (reason `session-ended`), the port is opened
  again with no click, your LED, settings and streams are set again, and you get
  `reconnected`: the same steps as after a pulled cable. Without auto-reconnect you get
  `disconnect` with reason `session-ended`: connect again and repeat every `configure`.
- An event band (firmware 1.1.0) does not end a USB session by itself, so this is rare.
  You can see it when something else switched your streams off, for example a second
  program on the band's other link ([Two programs on one band](#two-programs-on-one-band)).

```js
band.on('reconnecting', (e) => { if (e.reason === 'session-ended') show(e.message); });
band.on('disconnect', (e) => { if (e.reason === 'session-ended') show(e.message); });
```
```python
band.on("disconnect", lambda e: print(e.message) if e.reason == "session-ended" else None)
```

**A handler must be short.** A web page has one thread. While a handler works, the page
takes no data from the band, and when the buffers are full stream frames are lost: you
get a `notice` with kind `stream-dropped`. Collect in the handler and do slow work in
small pieces. The Python library reads the cable on a thread of its own, also while your
program is busy, and drops what your program does not take (it keeps at most 5000
frames). Keep handlers short in Python too: events wait in line behind a slow handler.

Bluetooth is not watched this way: there the library sends nothing extra and starts no
timer. To try it in your own program, see `endUsbSession()` / `end_usb_session()` in the
table of the next section.

## Two programs on one band

The band takes one Bluetooth connection at a time, and one program on the USB cable. So
two programs can be on one band at once, one on each link: the dashboard on Bluetooth and
`motion.py --usb`, or your page and a teammate's script on the cable. They do not get a
band each. **USB and Bluetooth programs on one band share it: its streams, its settings
and its light.**

| Shared | What that means |
|---|---|
| Streams | A stream goes to the program that started it last. When the second program starts `motion`, the first one gets no more motion, with no notice: its link stays up and its commands are still answered. When the second program ends, the band stops the streams that program had; the first program does not get them back by itself |
| Settings | One set for both: the motion rate and ranges, the intervals, the mic gain, the radio power, which gestures are on. What one program sets, the other one gets |
| The light | One LED. The last `setLed` / `set_led` wins, whichever program sent it, and `ledOff` / `led_off` in one program turns off the other one's light. The light goes off by itself only when the last program disconnects: a program that ends while another one is still connected leaves the light as it is. The blue microphone light is on while either program has the microphone on, and then neither can set the LED |
| Storage, the clock, the band number | One of each, as always |

**The library notices a stream that was taken.** When a stream your program started has
sent nothing for 3 seconds (or 3 of its own intervals, if that is longer) while the link
is up, the library asks the band once which streams are on. If yours is off, or on but
not coming to you any more, you get the event `stream-taken`:

| Field | Meaning |
|---|---|
| `streams` | the names, as in `configure`: `["motion"]` |
| `stopped` | those of them that the band has stopped (the other program has ended). The others still run, for the other program |
| `message` | one sentence for the person, for example: "No data arrives any more for the "motion" stream, though this program did not stop it and the link is up. Another program on the cable (USB) took this stream: USB and Bluetooth programs on one band share its streams, its settings and its light, and a stream goes to the program that started it last. Close the other program first; then, to get the data back, call band.takeBack()." |

```js
band.on('stream-taken', (e) => console.log(e.message));
```
```python
band.on("stream-taken", lambda e: print(e.message))
```

With no handler for it, the library prints the sentence once by itself. Nothing is taken
back by itself: two programs would then take the stream from each other for ever. Close
the other program, then:

| What | JavaScript | Python |
|---|---|---|
| Start the taken streams again, for this program | `await band.takeBack()` | `band.take_back()` |
| Which streams are taken and not back yet | `band.takenStreams` | `band.taken_streams` |

`configure` with the stream (`configure({ motion: 50 })`) takes it back too. Settings are
shared, so if the other program changed a rate or an interval, set it again. The
dashboard shows the sentence with a **Take it back** button.

What the library cannot see: a stream that is silent by nature (`events`, and `battery`
without an interval). While one of those is on, the library asks the band every 30
seconds, and only learns that it is gone once the band has stopped it. On the USB cable, a
stream the band has stopped is reported as `session-ended`
([above](#over-the-usb-cable-when-the-band-ends-the-session)), and auto-reconnect sets it
up again. To try it in your own
program, see `otherProgramStarts` in [the pretend band](#the-pretend-band-making-things-happen).

## The pretend band: making things happen

Left alone, the pretend band makes up its own, calm data:

| Stream | What the pretend band sends by itself |
|---|---|
| `motion` | a gentle swing that repeats every 2 seconds: `ax` and `ay` within 0.12 g, `az` 0.96 to 1.04, turning below 30 degrees/second. Never a shake or a fall |
| `events` | a `tap` 4 seconds after connecting, a `double-tap` at 8 seconds, and so on in turns every 4 seconds. No other gesture |
| `audio` | silence: real Opus packets that decode to silence ([give it a voice](#a-voice-for-the-pretend-band)) |
| `mic-level` | a made-up voice that swells and fades every few seconds: `rmsDb` / `rms_db` from about 50 (quiet) down to 10 or 20 (loud) |
| `activity` | walking, about 110 steps a minute; `steps` starts at 1200 and goes up by 2 with every reading |
| `battery`, `temperature`, `link` | 87 percent, on battery, 1 percent less each minute; about 31 degrees; about -55 dBm |

For a test or a demo you want things to
happen when you say so. Make the pretend band yourself, pass it as the transport, and
direct it with the calls below. Everything still travels through the real protocol, so
your program cannot tell the difference, and a real band needs none of it.

```js
const sim = new SimTransport(42, { autoTap: false });       // SimTransport comes from the same file as Band
const band = await Band.connect(42, { transport: sim });
band.on('tap', () => console.log('tap'));
band.on('mic-level', (e) => console.log('loudness', e.rmsDb));
await band.configure({ events: true, micLevel: true });
sim.setLoudness(0.5);                                       // a quiet room
sim.tap('tap');                                             // prints: tap
sim.after(0.15, () => sim.playLoudness([0.95]));            // a clap, 0.15 seconds from now
```
The same in Python, as a whole program (save it as `try.py` in `my-project/`, run
`python3 try.py`):

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "vexo-open-kit" / "python"))   # where the library is
from vexo_open.sync import Band
from vexo_open.transports.sim import SimTransport

band = Band.connect(42, transport=SimTransport(42, auto_tap=False))
sim = band.transport                         # the pretend band, to direct it: always through band.transport
band.on("tap", lambda e: print("tap"))
band.on("mic-level", lambda e: print("loudness", e.rms_db))
band.configure(events=True, mic_level=True)
sim.set_loudness(0.5)                        # a quiet room
sim.tap("tap")                               # prints: tap
sim.after(0.15, lambda: sim.play_loudness([0.95]))   # a clap, 0.15 seconds from now
band.wait(0.5)
band.disconnect()
```

**One rule for which object to call.** In a plain Python program (`vexo_open.sync`),
direct the pretend band through `band.transport`, always: in the main program, in a
handler, and in a function you give to `after`. The program above takes it once
(`sim = band.transport`) and uses that name, so the `sim.` calls in the table below work
as printed; `band.transport.tap("tap")` written out in full is the same thing. Do not
call the `SimTransport(...)` object you made directly: it lives on the band's own
thread, and `band.transport` is the door that runs each call there and waits for it.
With `async` Python and in JavaScript there is one thread only, so there `sim` is the
`SimTransport` object itself.

| What | JavaScript | Python |
|---|---|---|
| A tap or another gesture: `tap`, `double-tap`, `free-fall`, `tilt`, `orientation` (with a face 0..5), `motion-start`, `motion-stop`. Returns false if the events stream is off | `sim.tap('double-tap')`, `sim.tap('orientation', 4)` | `sim.tap("double-tap")`, `sim.tap("orientation", 4)` |
| No tap every 4 seconds | `new SimTransport(42, { autoTap: false })` | `SimTransport(42, auto_tap=False)` |
| Play a motion: a function of the seconds since it began | `sim.playMotion((s) => ({ gz: 200 }), { seconds: 0.5 })` | `sim.play_motion(lambda s: dict(gz=200), seconds=0.5)` |
| Play a motion: a list, one entry for each sample | `sim.playMotion([{ ax: 0.1 }, { ax: 0.4 }])` | `sim.play_motion([dict(ax=0.1), dict(ax=0.4)])` |
| Is a motion still playing or waiting | `sim.moving` | `sim.moving` |
| Back to the made-up swing | `sim.stopMotion()` | `sim.stop_motion()` |
| A steady loudness, 0 (silence) to 1 | `sim.setLoudness(0.5)` | `sim.set_loudness(0.5)` |
| A loudness curve: a function, a list (one entry for each reading) or one level; a clap is `[0.95]` | `sim.playLoudness((s) => 0.5 + s, { seconds: 0.3 })` | `sim.play_loudness(lambda s: 0.5 + s, seconds=0.3)` |
| ... with Opus packets for the `audio` stream | `sim.playLoudness(0.75, { seconds: 2, packets })` | `sim.play_loudness(0.75, seconds=2, packets=packets)` |
| Opus packets to play in a loop on the `audio` stream | `new SimTransport(42, { audioPackets })`, `sim.audioPackets = ...` | `SimTransport(42, audio_packets=...)`, `sim.audio_packets = ...` |
| Signal strength in dBm | `sim.setRssi(-85)` | `sim.set_rssi(-85)` |
| Battery: percent and `battery`, `charging` or `charged` | `sim.setBattery(12)`, `sim.setBattery(80, 'charging')` | `sim.set_battery(12)`, `sim.set_battery(80, "charging")` |
| What the wearer does: `still`, `walk`, `run` | `sim.setActivity('run')`, `sim.setActivity('walk', { cadence: 90, steps: 5000 })` | `sim.set_activity("run")`, `sim.set_activity("walk", cadence=90, steps=5000)` |
| The link drops; the band can be found again at once | `sim.dropLink()` | `sim.drop_link()` |
| The link drops and the band stays away for 3 seconds | `sim.dropLink(3)` | `sim.drop_link(3)` |
| Away until you say so | `sim.reachable = false`, later `true` | `sim.reachable = False`, later `True` |
| Something else is connected to the band, so it cannot be found: connecting fails with `not-found-nearby` and the sentence a real band gives. Set it before you connect | `sim.connectedElsewhere = true`, later `false` | `sim.connected_elsewhere = True`, later `False` |
| A second program on the band's other link starts streams (names as in `configure`): this program gets no more of them, and after a few seconds `stream-taken` ([Two programs on one band](#two-programs-on-one-band)) | `sim.otherProgramStarts('motion')` | `sim.other_program_starts("motion")` |
| That second program ends: the band stops the streams it had taken | `sim.otherProgramCloses()` | `sim.other_program_closes()` |
| The band restarts by itself (reset button) | `sim.powerCycle()` | `sim.power_cycle()` |
| The band's USB session ends: streams stop, settings go back to their defaults, no notice, commands are still answered. From this call on the library watches the pretend band like a band on the cable, and you get `session-ended` ([above](#over-the-usb-cable-when-the-band-ends-the-session)) | `sim.endUsbSession()` | `sim.end_usb_session()` |
| Watch the pretend band like a band on the cable from the start (otherwise it is only watched after the call above) | `sim.sessionWatch = true` | `sim.session_watch = True` (set it before you connect) |
| Do something later, on the band's clock | `sim.after(2, () => sim.tap('free-fall'))` | `sim.after(2, lambda: sim.tap("free-fall"))` |
| The light the band shows | `sim.led`, `sim.onLed = (led) => ...` | `sim.led`, `SimTransport(42, on_led=fn)` |
| A band with firmware 1.0, or one on the USB cable | `{ firmware: '1.0' }`, `{ via: 'usb' }` | `firmware="1.0"`, `via="usb"` |
| A test that runs the clock itself | `{ realtime: false }`, then `sim.step(ms)` | `realtime=False`, then `sim.step(ms)` |

Details:

- **Motion.** The function or list gives any of `ax ay az` (in g) and `gx gy gz` (in
  degrees/second); what it leaves out is at rest (`az` 1, the others 0). Values beyond the
  sensor's range are cut off, as on a real band: at 2 g and 2000 degrees/second unless you
  widened the range with `setMotion` / `set_motion` (see [Controls](#controls)). `seconds` is how long it plays. Without
  it a list plays once to its end, and a function or a single reading plays until you
  call `playMotion` / `play_motion` again or stop it. Motions queue up: a second call
  plays after the first has ended. Between and after them the band lies flat and still. A
  motion only moves on while the motion stream is on.
- **Loudness.** A level is what your program reads as `level` at the default mic gain:
  `rmsDb = 96 x (1 - level)`, and `peakDb` 6 less. Curves queue up like motions and play
  on top of the steady level. `setLoudness(null)` / `set_loudness(None)` brings the
  made-up voice back. The pretend band cannot hear the Opus packets you give it: the
  loudness is the curve you give, not the loudness of that sound. Each packet is sent
  once, 50 a second, while `audio` is on; afterwards the band goes back to its looped
  packets or to silence.
- **Signal strength.** `setRssi(null)` / `set_rssi(None)` brings the made-up value (about
  -55) back. `getRadio` / `get_radio` reports the same number.
- **Activity.** Left alone the pretend band walks at 2 steps a reading. After
  `setActivity` / `set_activity` it counts steps by its own clock (cadence in steps a
  minute: 0, 110 or 165 unless you give one), also while nothing is connected, like a
  real band.
- **Later.** `after` counts seconds on the band's clock and runs your function while the
  link is open; if the link is down at that moment, it runs right after the next connect.
- **The band's counter.** It keeps counting while nothing is connected and starts again at
  0 after `restart()` and `powerCycle()` / `power_cycle()`, like a real band's
  (see [Time](#t-and-time-when-the-link-comes-back)).
- **An error in a function you gave** (a motion, a curve, `after`) is printed and that
  function is dropped; the pretend band goes on.

### Where the pretend band keeps its storage

In memory. `band.storage` of a pretend band lives in the `SimTransport` object: it
survives a dropped link and a new `Band.connect` with the same object, and it is gone
when the program ends or the page is reloaded. Nothing is written to your disk.

To keep it from one run of a program to the next, like a real band does, name a file:

| | JavaScript (Node only) | Python |
|---|---|---|
| A file you choose (its folder must exist) | `new SimTransport(42, { storeFile: 'band-42.json' })` | `SimTransport(42, store_file="band-42.json")` |
| A file of your own in your home folder | `new SimTransport(42, { storeFile: true })` | `SimTransport(42, store_file=True)` |

With `true` / `True` the file is `.vexo-open/pretend-band-42.json` in your home folder
(one file for each band number). The folder is made so that only you can open it, and
the file is readable by you only. It is never put in the shared temporary folder of the
computer, where another user could read it or put a file in its place. The file is
written again after every change, in one step, and both languages read each other's
files. A file that does not hold a store is ignored. A web page has no files: there
`storeFile` fails with `not-available`. As on a real band, do not store anything private.

## Errors

Problems raise `VexoError` with a short `code` and a `message` written for the person at the
keyboard.

| `code` | When |
|---|---|
| `denied` | The band refused for a safety reason. The usual one: LED commands (`setLed`, `ledOff`, `identify`) while `audio` or `micLevel` / `mic_level` is on, because the blue mic light owns the LED. Also `setBandNumber` over Bluetooth, and a motion rate that is too much for this link (see [Fast motion](#fast-motion); the message names the most it can carry and the way out) |
| `bad-arg` | A value the band (or the library, before sending) does not allow: band number 0 or above 999 in `connect`, a motion rate that is not in the list, an interval outside its range (the message names the range), a time outside 2020 to 2100 |
| `bad-length`, `unknown-opcode` | Should not happen with this library; the band did not understand the command |
| `busy` | Storage answered "busy" for 3 seconds (more than 10 writes a second, or still erasing). Try again later |
| `not-available` | That part is not working on this band (see `info.capabilities`). Or the band has firmware 1.0 and you asked for something only 1.1 has: a motion rate above 100, one sensor alone, a stream interval, `setTime` / `set_time` (see [Firmware 1.0 and 1.1](#firmware-10-and-11)). Also: `link` asked for over the USB cable (it is Bluetooth only); `wipe()` when the band could not erase; `audio` and `micLevel` / `mic_level` if the LED is broken, because the mic may not run without its blue light |
| `not-found`, `no-space`, `io-error` | Storage or sensor errors (`storage.get` of an empty key returns `null` / `None` instead of raising) |
| `timeout` | No reply within 2 seconds. Also `wipe()` still erasing after its limit |
| `not-connected` | The link is closed, or the library is reconnecting (wait for `reconnected`) |
| `wrong-band` | The band on the link is not the number you asked for. The error also has `found` and `wanted` (the two numbers) |
| `cancelled`, `not-found-nearby`, `no-web-bluetooth`, `no-web-serial`, `no-bleak`, `no-pyserial`, `no-port`, `port-failed`, `bad-transport`, `bad-stream`, `bad-event`, `bad-colour` | Could not connect or bad input; the message says what to do. `bad-stream`: a name `configure` does not know. `bad-event`: a name `on` or `off` does not know. Both messages list the names that exist |

**A problem nobody catches, in a plain Python program.** With `vexo_open.sync`, a
`VexoError` that your program does not catch ends the program with one sentence and the
line of your program it came from, not a traceback:

    Problem (denied): The band refused LED_SET because the microphone is on. ...
      (line 9 of try.py)

The band is disconnected first, and the exit code is 1. Only `VexoError` is shortened like
this. Any other error is a bug in the program and keeps its full traceback (the band is
disconnected then too). To get the traceback of a `VexoError` as well, start the program
with the environment variable `VEXO_TRACEBACK=1`. To handle a problem yourself, catch it:
`except VexoError as e:` and look at `e.code`. In a web page nothing is shown by itself:
wrap `Band.connect` and the calls after it in `try { ... } catch (e) { ... }` and put
`e.message` on the page. In Node the JavaScript library does the same as Python: a
`VexoError` nobody catches ends the program with the one sentence `Problem (code): ...`
and exit code 1 (`VEXO_TRACEBACK=1` shows the stack trace; a program that handles
uncaught errors itself is left alone).

## Python without async

`vexo_open.sync.Band` has every method above as a plain function call:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "vexo-open-kit" / "python"))   # where the library is
from vexo_open.sync import Band

band = Band.connect(42)
band.on("tap", lambda e: print("tap!"))
band.configure(events=True)
band.wait(5)             # keep the program alive for 5 seconds (or band.wait() until Ctrl+C)
band.disconnect()
```

The first three lines are explained in [Where your code goes](#where-your-code-goes).
Every Python line on this page is written this way, so the Python column of any table
can be copied as it is. `band.wait(5)` keeps the program alive so that handlers can run;
with the async form that would be `await asyncio.sleep(5)`, and every band call there
gets `await` in front.

`with Band.connect(42) as band:` disconnects for you. So does the end of the program: the
band is disconnected properly when the program ends, also after an error.

### Handlers: the rules

A handler is the function you give to `band.on(...)`. With `vexo_open.sync`:

- Handlers run on a background thread, one at a time, in the order the events arrived.
  Your main program is usually in `band.wait(...)` meanwhile.
- A handler may call the band and use the answer, like the main program:
  `led = band.get_led()`, `band.set_led("#ff00aa")`, `band.storage.get_text(7)`,
  `band.wait(2)`. A call that fails raises `VexoError` inside the handler.
- While one handler runs, the next events wait in line and arrive late. So keep handlers
  short. With fast motion (hundreds of samples a second) only store
  the sample in the handler and do the slow work in the main program.
- The line holds at most 5000 events. When it is full, the oldest data event in it is
  dropped to make room for the new one. `disconnect`, `reconnecting` and `reconnected` are
  never dropped, and a `notice` only when nothing else is left to drop.
  `band.dropped_events` counts the dropped events, and the first time one line on the
  terminal says so. If the number is not 0, the handler is too slow for the data.
- An error in a handler is printed (`Error in your "tap" handler:` and the traceback), and
  the program goes on. A band problem (`VexoError`) is printed as one sentence instead, as
  in the main program: `Problem (denied): ...` and under it
  `(line 9 of app.py, in your "tap" handler)`. With `VEXO_TRACEBACK=1` a band problem has
  its traceback too. If a handler seems to do nothing, look at the terminal.
- `band.disconnect()` in a handler ends `band.wait()` in the main program.
- `band.wait(...)` in the main program returns only after the handlers of everything that
  has arrived so far have run (a slow handler gets 2 seconds). `band.disconnect()` does
  the same. With more than 1000 events waiting they do not wait: `band.wait(5)` then
  returns after 5 seconds, and `disconnect()` drops the data events that still wait.
- A handler and the main program run at the same time. Sharing a list or a counter between
  them is fine for simple things (`readings.append(m)`).

With the async `Band` (`from vexo_open import Band`), handlers run on the event loop. A
plain handler must not wait there: write `async def on_tap(e): led = await band.get_led()`.
In JavaScript a handler is called on the page's own thread; make it `async` to `await` a
band call: `band.on('tap', async () => { const led = await band.getLed(); })`.
Plain handlers there run at once, so nothing waits. Of `async` handlers at most 5000 may
be running at the same time: while that many run, new data events are not given to
`async` handlers. `band.droppedEvents` / `band.dropped_events` counts them.

## How it is built

    your code -> Band -> codec (objects <-> bytes) -> transport (frames <-> band)

- `Band` (`js/src/band.js`, `python/vexo_open/band.py`): the API above. Sends one command at
  a time and waits for its reply.
- Codec (`js/src/codec.js`, `python/vexo_open/codec.py`): the only place that knows the byte
  format in [`contract/contract.md`](../contract/contract.md). Both are tested against the
  same file, [`contract/test-vectors.json`](../contract/test-vectors.json).
  Writing your own USB (serial) client instead of using these libraries? Follow the
  checklist "Writing your own USB client" in section 2.2 of the contract.
- Transports: Web Bluetooth, Web Serial, simulator (JavaScript); bleak, pyserial, simulator
  (Python). A transport is any object with `open`, `close`, `send(frame)` that calls
  `onFrame` / `on_frame`, so you can write your own.
- The simulator is a pretend band that speaks the real protocol, including the error
  replies, so everything here runs with no hardware. You can direct it: see
  [The pretend band](#the-pretend-band-making-things-happen).
