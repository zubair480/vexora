# Examples

Short programs to copy from. They are starting points, not the assignment: build what
you want in your own project. Run the commands in `my-project/`, the folder that holds the
kit (the same place as everywhere in these pages; see
[Getting started](../docs/getting-started.md#get-the-kit-2-minutes)). On Windows, type
`python` wherever this page says `python3`.

> **42 is only our example. Wherever you see 42 in a command, an address or a name, type the
> number written on your band's bag.** Band 7 is `Vexo-HACK-07`, and its dashboard address
> ends in `?band=7`.

Every Python example takes your band number (`42` below is the example: 42 = your band number). Add `--usb` to use the USB cable,
or use `--sim` instead of the number to run with a pretend band and no hardware. Add
`--seconds 10` to stop after 10 seconds.

The two Node examples take the band number in the same place, and `--seconds 10` too. They
always run with a pretend band: Node has no Bluetooth and no USB for the library, so the
number only gives the pretend band its name (with no number it is band 42).

| Example | What it shows | Command to run |
|---|---|---|
| `python/led.py` | LED colours and blinking | `python3 vexo-open-kit/examples/python/led.py 42` |
| `python/motion.py` | Live motion readings as text | `python3 vexo-open-kit/examples/python/motion.py 42` |
| `python/motion_graph.py` | Live motion graph in a window (needs `python3 -m pip install matplotlib`) | `python3 vexo-open-kit/examples/python/motion_graph.py 42` |
| `python/fast_motion.py` | Fast motion: the fastest rate this link allows, the measured rate, and lost samples | `python3 vexo-open-kit/examples/python/fast_motion.py 42` |
| `python/taps.py` | Tap and double-tap change the LED | `python3 vexo-open-kit/examples/python/taps.py 42` |
| `python/storage.py` | Save a value on the band, read it back and remove it again | `python3 vexo-open-kit/examples/python/storage.py 42` |
| `python/reconnect.py` | Keep going when the link drops: the library connects again by itself | `python3 vexo-open-kit/examples/python/reconnect.py 42` |
| `python/audio_to_text.py` | Turn speech at the microphone into text while you talk, and save the sound as an `.ogg` file (the text comes from Deepgram, an online service: it needs your team's key, and the sound goes to that company, see below) | `python3 vexo-open-kit/examples/python/audio_to_text.py 42` |
| `python/ask_the_band.py` | Speak a question, an AI model answers: microphone to Deepgram to OpenAI (needs your team's two keys; `--sim --say "Why is the sky blue?"` tries it with typed words) | `python3 vexo-open-kit/examples/python/ask_the_band.py 42` |
| `python/voice_commands.py` | Voice commands by meaning: say what you want in your own words, the model picks one of a few actions (tool calling) and the band's light changes colour and pattern | `python3 vexo-open-kit/examples/python/voice_commands.py 42` |
| `python/meeting_notes.py` | A minute of talk becomes a summary and a list of action items (structured output) | `python3 vexo-open-kit/examples/python/meeting_notes.py 42` |
| `web/led.html` | LED from a web page, with a colour picker | `python3 -m http.server 8000 --bind 127.0.0.1` in `my-project/`, then open `http://localhost:8000/vexo-open-kit/examples/web/led.html` |
| `web/motion-graph.html` | Live motion graph on a canvas, with the newest readings as numbers | same server, `/vexo-open-kit/examples/web/motion-graph.html` |
| `web/taps.html` | Tap and double-tap change the LED | same server, `/vexo-open-kit/examples/web/taps.html` |
| `node/hello.mjs` | The JavaScript library in Node with the pretend band (Node has no Web Bluetooth, so no real band) | `node vexo-open-kit/examples/node/hello.mjs 42` |
| `node/fast-motion.mjs` | Fast motion with the pretend band: the fastest rate, counting lost samples, real time on every sample | `node vexo-open-kit/examples/node/fast-motion.mjs 42` |

Good to know:

- A real band over Bluetooth needs `python3 -m pip install bleak`. The USB cable needs
  `python3 -m pip install pyserial`. The pretend band needs neither. (Windows:
  `python -m pip install ...`.)
- `python3 -m pip install bleak` on a Mac with the Python that comes with macOS (3.9) can print a long
  warning about a "yanked version" of `pyobjc-core`. It is harmless: look for
  `Successfully installed` in the last line.
- **One light, shared.** An example on the USB cable (`--usb`) and a program on Bluetooth
  (the dashboard, a teammate's page) can be connected to one band at the same time, and
  they share its light, its streams and its settings. `led.py --usb` says "then off", and
  that is only true when nothing else is connected: with the dashboard open, the band
  keeps blinking white after `led.py` has ended, and the dashboard's own colour is gone
  until you pick it again. In the same way `motion.py --usb` takes the motion stream away
  from a dashboard on Bluetooth; the dashboard then says so and offers **Take it back**.
  See ["Two programs on one band"](../docs/api.md#two-programs-on-one-band).
- `fast_motion.py` on a real band prints something like "Asked for 833 a second, measured
  865.2 a second." That is not a fault: the sensor counts with its own clock, a few
  percent fast or slow, and the time of each reading is the truth (the example says so).
- `storage.py` removes its text again at the end, so nothing is left on the band for the
  next team. Storage is kept while the band is off: to see that, leave out the `delete`
  line. `band.storage.wipe()` erases everything on a band.
- With no band number and no `--sim`, a Python example stops and says so:
  "add your band number, or --sim for a pretend band", with the command to type.
- The web pages need the small web server from the table, started in `my-project/`. One
  server is enough for everything: the example pages, the dashboard
  (`http://localhost:8000/vexo-open-kit/dashboard/?band=42`) and your own pages.
  `--bind 127.0.0.1` keeps it on
  your own computer; without it, everyone on the same Wi-Fi can read the folder. If the
  command says `Address already in use`, another program has port 8000: use another number,
  for example `python3 -m http.server 8001 --bind 127.0.0.1` and `http://localhost:8001/...`.
  A page opened by double-click does not work, and says so after a second.
- Connect seems stuck on "Connecting..."? Press **Cancel** (the second button) and try
  again. With no pick in the browser's list, the library gives up after 2 minutes.
- The web pages have a **Pretend band** tick box. Tick it before you press Connect to try
  the page with no hardware. You can put the band number in the address: `led.html?band=42`.
- A band number is typed as `42` or `07`; the full name `Vexo-HACK-42` is understood too.
  Anything else stops with "is not a band number" and says what to type. Numbers are 1 to
  999; 0 is not a band you can ask for.
- `python/kit.py`, `web/kit.js` and `node/kit.mjs` are small helpers shared by the examples
  (they read the band number and the `--sim` / `--usb` / `--seconds` choice). `python/ogg_opus.py` writes the sound file.
- `python/speech.py` is speech-to-text behind one small interface (`start`, `feed`,
  `stop`). Its engine is Deepgram, an online speech-to-text service that needs your team's
  key; another speech service plugs in behind the same interface. `python/tiny_websocket.py`
  is the small connection helper it uses.
  `python/speech_accuracy.py` measures how many words an engine gets wrong on your own
  recordings: `python3 vexo-open-kit/examples/python/speech_accuracy.py my-recordings`. All of this is
  explained in [`docs/speech.md`](../docs/speech.md) and [`docs/deepgram.md`](../docs/deepgram.md).
- Text you read from a band was written by whoever connected before you. `storage.py`
  prints it through `kit.plain(...)`, which shows control characters as codes (`\x1b`)
  instead of sending them to the terminal. Do the same in your own programs. In a web
  page, show such text with `textContent`, never with `innerHTML`.
- Your own programs do not go in this folder. Make a folder `my-project` with the kit
  inside it and save them there; the first program for that layout is in
  [Getting started](../docs/getting-started.md#write-your-own-program). (If you do save a
  file here, nothing breaks: the kit's tests only look at the kit's own files.)
- `reconnect.py --sim --seconds 6`: the pretend band drops its link after 4 seconds, so you
  can watch the library connect again.
- While the microphone is on (recording, or only measuring loudness), the band's light is
  solid blue and the band refuses LED commands. That is on purpose: the light tells people nearby that the mic is on.
- `audio_to_text.py` saves the sound as `recording.ogg` in the folder you are in.
- `audio_to_text.py --sim` records silence, because a pretend band has no microphone (the
  program says so). To hear it work without a band, let the pretend band play a recording.
  The kit has one, 16 seconds of a person reading aloud (public domain):

      python3 vexo-open-kit/examples/python/audio_to_text.py --sim --seconds 16 --replay vexo-open-kit/dashboard/speech/sample-speech.opus

  (The pretend band starts the file again at its end; `audio_to_text.py` listens to it once
  and stops there, so the text does not begin a second time.)
  Any Ogg Opus file with 20 ms packets works, for example one this script saved earlier.
  In your own program the same thing is one option, `audio_packets=` (JavaScript:
  `audioPackets`): see ["A voice for the pretend band"](../docs/api.md#a-voice-for-the-pretend-band).
- Speech is turned into text by **Deepgram**, an online speech-to-text service. While
  `audio_to_text.py` listens, the sound of the band's microphone (everyone near the band)
  goes over the internet to that company, and the program says so before it sends any.
  Tell the people around you. Nothing has to be installed for it.
- It needs your team's Deepgram key, in the environment variable `DEEPGRAM_API_KEY`
  (`export DEEPGRAM_API_KEY=...`; Windows: `set DEEPGRAM_API_KEY=...`) or in an untracked
  file called `.env` in the folder you are in. Never put a key in your code, in git, in a
  log or in the band's storage. Without a key the program still saves the sound and says
  "Transcription needs a Deepgram key: see docs/deepgram.md"; `--text no` only saves the
  sound and sends nothing. [`docs/deepgram.md`](../docs/deepgram.md) has the whole story.
- Any other speech-to-text tool that accepts Ogg Opus files works with the saved file too.
- **Text to AI.** `ask_the_band.py`, `voice_commands.py` and `meeting_notes.py` send the
  text on to an OpenAI model. They need a second key, your team's OpenAI key, in
  `OPENAI_API_KEY` (set the same way, or in the same `.env` file; the kit's `env.example`
  is an empty form for both). The text goes over the internet to OpenAI, a company, and
  each program says so. With `--sim` the pretend band plays the kit's sample recording as
  its sound; with `--sim --say "your words"` nothing is listened to and only the OpenAI key
  is needed. Without that key they stop at once with one sentence that says what to set.
  `python/check_keys.py` checks both keys in half a minute:
  `python3 vexo-open-kit/examples/python/check_keys.py`. `python/ai.py` is the helper they
  share: plain HTTPS to OpenAI, nothing to install, the model's name in one constant.
  The steps, the errors and ten project ideas are in
  ["Audio to text to AI" in AGENTS.md](../AGENTS.md#audio-to-text-to-ai).
- For sound as samples, install the decoder: `python3 -m pip install av` (no version:
  tested with 15.1.0 on Python 3.9 and 19.0.2 on Python 3.12). The first packet of sound
  then takes a few seconds to arrive as samples: Python loads the decoder once. Text does
  not need the decoder.
- To use another speech service, plug it in through the interface in `speech.py`
  ([`docs/speech.md`](../docs/speech.md) shows how in about 15 lines).
- The microphone records everyone near the band, not only the wearer. The band's solid
  blue light means the microphone is on. Tell the people around you.

Tests for the examples (for the kit's maintainers; typed in the kit folder itself, not in
`my-project/`):

    python3 -m unittest discover -s examples/test
    node --test examples/test/*.mjs
