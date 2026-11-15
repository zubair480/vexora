# Using Deepgram with your band

The kit turns speech into text with [Deepgram](https://deepgram.com), an online
speech-to-text service. This page is everything a team needs: what it does, the rule about
other people's voices, where the key goes, and the code.

> **42 is only our example. Wherever you see 42 in a command, an address or a name, type the
> number written on your band's bag.** Band 7 is `Vexo-HACK-07`, and its dashboard address
> ends in `?band=7`.

## What it is

Your program (or the dashboard) sends the sound of the band's microphone to Deepgram over
the internet, and Deepgram sends the words back while people are still speaking. There is
nothing to install and nothing to download.

**While it is on, the microphone's sound leaves your laptop.** The band's microphone hears
everyone near it, not only the person wearing it, and all of that goes to Deepgram, a
company. So:

- The band's **solid blue light** means the microphone is on. **Tell the people near the
  band before you switch it on**, and switch it off when you are done.
- The kit asks Deepgram not to keep the sound for training its models (`mip_opt_out=true`
  on every connection). Keep that setting in your own code.
- Without a key nothing is sent, and everything else about the band works as before:
  motion, taps, the LED, saving the sound, the pretend band. Only the text is missing, and
  the program says so in one sentence.

What we measured in November 2026, on recordings from a real band (the band's own Opus
packets over USB, default microphone gain; the speaker was a computer voice from a
laptop's speakers, the same 8 passages by 7 voices, 230 words, that this kit has used for
its speech measurements):

| | Deepgram (`nova-3`), through `speech.py`, live |
|---|---|
| Words wrong, 5 passages by natural-sounding voices (124 words) | 7 (5.6 %) |
| Words wrong, all 8 passages (230 words) | 64 (27.8 %) |
| First words on the screen after someone starts to speak | 0.5 to 1 second |
| Finished sentence after the speaker stops | about 0.4 seconds (the longest: 1.6) |
| The kit's sample voice (a person reading aloud, 41 words, not through a band) | 0 wrong |
| Sending the band's Opus packets as they are, or decoding them first | no difference (64 and 68 wrong) |

How to read it: Deepgram is fast, and good with natural voices. Almost all of its mistakes
(56 of 64) came from three old, robot-like computer voices, where it also dropped whole
sentences. A person's voice is closer to the natural ones, but **no human voice through a
band has been measured yet**: measure your own with
`examples/python/speech_accuracy.py` (see [`speech.md`](speech.md)). It needs the internet
the whole time; with no connection there is no text.

## The key

A key is a password that is billed to someone. **Each team gets its own key at the event**,
from the organisers. It works during the event and is switched off afterwards.

Where the key goes:

- An **environment variable**, in the terminal you start your program from:
  `export DEEPGRAM_API_KEY=...` on macOS and Linux, `set DEEPGRAM_API_KEY=...` on Windows
  (in PowerShell, which does not know `set`: `$env:DEEPGRAM_API_KEY = "..."`).
- Or a file called **`.env`** in the folder you start your program in, with the one line
  `DEEPGRAM_API_KEY=...`. The kit's `.gitignore` keeps `.env` out of git. If your project
  has its own repository, add `.env` to its `.gitignore` **before** you create the file.
- In the dashboard: **typed into the key field**. It stays in that page's memory until you
  reload or close the tab. It is not stored, and only that page can see it.

Where the key never goes: **in your code, in a commit, in a web page you share or host, in
a screenshot or a screen share, in a chat or a forum, in a log or a printed message, in
the band's storage** (anyone who connects to the band can read that). Use your own team's
key, never another team's.

**If a key leaks** (it got into git, a chat, a photo of your screen): tell an organiser
straight away. The key is switched off and you get a new one. Deleting the line or the
message afterwards does not help: assume it was copied.

The same steps in short, together with the OpenAI key and a command that checks both keys,
are in ["Audio to text to AI" in AGENTS.md](../AGENTS.md#audio-to-text-to-ai).

## Python

Ten seconds of the band's microphone as text. A whole program; save it as `listen.py` in
`my-project/`, set the key as above, and run `python3 listen.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "vexo-open-kit" / "examples" / "python"))
import speech                                   # vexo-open-kit/examples/python/speech.py; it finds the library too
from vexo_open.sync import Band

ears = speech.create()                          # Deepgram; the key comes from DEEPGRAM_API_KEY
ears.start(lambda text, final: print(text))     # guesses (final=False) and settled sentences
band = Band.connect(42)
band.on("audio", lambda a: ears.feed(a.opus, a.seq))
band.configure(audio=True)                      # the light goes solid blue: the microphone is on
band.wait(10)
band.configure(audio=False)
band.disconnect()
print(ears.stop())                              # all the final text
```

Or the ready example, which also saves the sound as `recording.ogg`:

    python3 vexo-open-kit/examples/python/audio_to_text.py 42

Shorter still, when you only want the text at the end: `text = speech.listen(band, 10)`
does the five lines from `ears.start` to `ears.stop` in one (call it in the main program,
not inside a handler). To send the text on to an AI model, see
["Audio to text to AI"](../AGENTS.md#audio-to-text-to-ai).

It prints one line that says where the sound goes before any is sent. Keep that line in
your project. Options: `speech.create("deepgram", language="de")` for another language,
`keyterm="Vexo"` for a word it should know, `endpointing=300` to wait for a longer pause
before a sentence counts as finished. No band? Use the connect line from
["No band: give the pretend band a voice"](speech.md#no-band-give-the-pretend-band-a-voice).

## The dashboard

1. Open the dashboard and connect your band (or "Try without a band").
2. In the Microphone panel, paste your team's key into the field **Deepgram key for Live
   transcript** and press Enter. The field empties; the key is now in the page's memory.
3. Tell the people near the band, then switch on **Live transcript**. The band's light goes
   solid blue, and the page shows a line for as long as sound is going to Deepgram.
4. Switch **Live transcript** off to stop. **Forget key** removes the key from the page;
   so does reloading or closing the tab.

With no key typed, the switch tells you that the transcript needs a key and nothing is
sent. Use the dashboard on your own computer; do not type a key into a copy of the page
that someone else hosts.

## From your own code

The address is always the same. These are the settings for the band's sound:

    wss://api.deepgram.com/v1/listen?model=nova-3&language=en&encoding=opus&sample_rate=16000&channels=1&interim_results=true&smart_format=true&endpointing=100&mip_opt_out=true

- `encoding=opus&sample_rate=16000&channels=1`: the band's packets exactly as they arrive,
  **one packet in one binary message**. No decoding, no file header. (Decoded samples work
  too: `encoding=linear16` with the same rate.)
- `interim_results=true`: guesses while someone speaks. Each answer has `is_final`
  (this piece will not change any more) and `speech_final` (the speaker paused).
- `endpointing=100`: a pause of a tenth of a second ends a sentence. With the band's
  microphone a larger number made the finished sentence come seconds later.
- `mip_opt_out=true`: Deepgram does not keep the sound for training. Keep it.
- When no sound is sent for a while, send the text message `{"type": "KeepAlive"}` every
  few seconds, or Deepgram hangs up after 10. To finish, send `{"type": "CloseStream"}`:
  the last words arrive and Deepgram closes the connection.

Python, with the small WebSocket helper that is in the kit (a whole program; save it as
`forward.py` in `my-project/` and run `python3 forward.py`):

```python
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "vexo-open-kit" / "python"))               # the library
sys.path.insert(0, str(Path(__file__).resolve().parent / "vexo-open-kit" / "examples" / "python"))  # the helper
from tiny_websocket import WebSocket
from vexo_open.sync import Band

URL = ("wss://api.deepgram.com/v1/listen?model=nova-3&language=en&encoding=opus&sample_rate=16000"
       "&channels=1&interim_results=true&smart_format=true&endpointing=100&mip_opt_out=true")
print("Note: the band's microphone is sent to Deepgram while this runs.")
deepgram = WebSocket(URL, headers={"Authorization": "Token " + os.environ["DEEPGRAM_API_KEY"]})
band = Band.connect(42)
band.on("audio", lambda a: deepgram.send(a.opus))          # one Opus packet = one binary message
band.configure(audio=True)
band.wait(10)
band.configure(audio=False)
band.disconnect()
deepgram.send(json.dumps({"type": "CloseStream"}))         # "send me the rest, then hang up"
while True:
    message = deepgram.recv()
    if message is None:                                    # Deepgram closed: that was all
        break
    answer = json.loads(message)
    if answer.get("type") == "Results" and answer["is_final"]:
        print(answer["channel"]["alternatives"][0]["transcript"])
```

JavaScript (Node 22 or newer; save it as `forward.mjs` in `my-project/` and run
`node forward.mjs`). Node cannot reach a real band, so this one plays the kit's sample
voice through a pretend band; in a web page the same lines work with a real one:

```js
import { readFileSync } from 'node:fs';
import { Band, SimTransport, oggPackets } from './vexo-open-kit/js/src/index.js';

const URL = 'wss://api.deepgram.com/v1/listen?model=nova-3&language=en&encoding=opus&sample_rate=16000'
  + '&channels=1&interim_results=true&smart_format=true&endpointing=100&mip_opt_out=true';
const key = process.env.DEEPGRAM_API_KEY;       // in a web page: from a password field, never from the code
console.log("Note: the band's microphone is sent to Deepgram while this runs.");
// The key rides in the "protocol" field of the connection, not in the address.
const deepgram = new WebSocket(URL, ['token', key]);
deepgram.onmessage = (event) => {
  const answer = JSON.parse(event.data);
  if (answer.type === 'Results' && answer.is_final) console.log(answer.channel.alternatives[0].transcript);
};
deepgram.onclose = () => process.exit(0);       // Deepgram hangs up when it has sent everything
await new Promise((open, refused) => { deepgram.onopen = open; deepgram.onerror = () => refused(new Error('Deepgram refused the connection.')); });

const voice = oggPackets(readFileSync('./vexo-open-kit/dashboard/speech/sample-speech.opus'));
const band = await Band.connect(42, { transport: new SimTransport(42, { audioPackets: voice }) });
band.on('audio', (a) => deepgram.send(a.opus)); // one Opus packet = one binary message
await band.configure({ audio: true });
await new Promise((done) => setTimeout(done, 10000));
await band.disconnect();
deepgram.send(JSON.stringify({ type: 'CloseStream' }));
```

A page of your own that talks to Deepgram is yours to keep safe: take the key from a
`<input type="password">`, keep it in a variable, and never put it in the address of the
page, in `localStorage` or in the code.

## When it does not work

| What you see | What it means | What to do |
|---|---|---|
| "Transcription needs a Deepgram key" | No key was found | Set `DEEPGRAM_API_KEY` in this terminal, or make the `.env` file, or type the key into the dashboard |
| Status 401 | Deepgram does not know the key | Copy it again: the whole key, no spaces, no quotes. A key that was switched off also answers 401: ask an organiser |
| Status 402 | The key has no credit left | Tell an organiser |
| Status 403 | The key may not use that model or setting | Go back to the settings on this page |
| Status 429 | Too many connections at once with this key | Stop the other programs and browser tabs that use it, wait a few seconds, start again |
| Status 400 | A setting in the address is wrong | Compare the address with the one on this page |
| "Could not reach api.deepgram.com", or it connects and closes at once | No internet, or the network blocks WebSockets | Check the Wi-Fi; try another network or a phone's hotspot; tell an organiser if the event's network is the problem |
| It connects, but no text | No sound is arriving, or nobody is near the band | Is the band's light solid blue? Is the voice within arm's length? `audio_to_text.py` saves `recording.ogg`: listen to it |
| The dashboard says Deepgram refused the connection | A browser is not told why | The three usual reasons: a wrong key, no credit, no internet |

The kit's programs show only the status number, never the text Deepgram sent with it, and
never the key.
