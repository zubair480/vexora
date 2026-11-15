# Vexo Open kit

A Vexo Open band is a wrist band with a motion sensor, a microphone and a colour light. This
kit shows you how to read and control it from a laptop over Bluetooth. No app, no account.

**This repo is a reference, not your project.** It holds instructions, the Bluetooth
contract, two small libraries, a dashboard and examples to copy. Make your own folder and
your own repository, in any language, and build whatever you want. Copy any file you like
(MIT licence). You never need to change this repo or push to it.

> **42 is only our example. Wherever you see 42 in a command, an address or a name, type the
> number written on your band's bag.** Band 7 is `Vexo-HACK-07`, and its dashboard address
> ends in `?band=7`.

## Set up (2 minutes)

You need Python 3.9 or newer and Chrome or Edge. Make a folder for your project and put the
kit inside it:

    mkdir my-project
    cd my-project
    git clone https://github.com/VexoAI-Org/vexo-open-kit.git

(Or download the ZIP, unpack it inside `my-project`, and rename the folder to exactly
`vexo-open-kit`.) You now have:

    my-project/            your own programs go here; type every command here
      vexo-open-kit/       this kit; leave it as it is

On Windows, type `python` wherever these pages say `python3`.

## Connect your band (2 minutes)

1. In `my-project/`, start the small web server and leave it running:

       python3 -m http.server 8000 --bind 127.0.0.1

   (`--bind 127.0.0.1` keeps it on your own computer. Without it,
   everyone on the same Wi-Fi can read the folder.
   `Address already in use`? Type `8001` here and in the address.)
2. Open <http://localhost:8000/vexo-open-kit/dashboard/?band=42> in Chrome or Edge, with
   your own number in place of 42.
3. **Double-tap the band.** Its light blinks blue: it can be found for 60 seconds.
4. Press **Connect** and pick your band. The list shows only your band, for example
   `Vexo-HACK-42`.
5. The band flashes white. You see live motion, taps and battery, and you can change the
   light.

No band yet? Press **Try without a band**.

Three rules:

- **Connect only to your own band.** `?band=42` and `Band.connect(42)` look only for
  `Vexo-HACK-42`. If your band flashes white and it was not you, someone else has
  connected to it: restart it and tell a helper.
  To restart a band by hand, press the small RESET button on the board once (once only:
  two quick presses start the board's loader instead; press once more to get the band
  back). A band with no battery also restarts when you unplug the USB cable and plug it
  in again. After a restart the light blinks blue once a second for 60 seconds, or until
  you connect.
- **One program at a time.** Close the dashboard tab before you run a Python program, and
  the other way round.
- **Double-tap before every connect.** No blue blink, no connection.

## Run an example (2 minutes)

In a second terminal, in `my-project/` (42 = your band number):

    python3 -m pip install bleak
    python3 vexo-open-kit/examples/python/led.py 42

The light goes red, green, blue, then blinks white. Without a band:
`python3 vexo-open-kit/examples/python/led.py --sim`. All examples, with one command each,
are in [examples/README.md](examples/README.md).

## Sound, text and AI

The microphone gives you three things:

| You want | Run or use |
|---|---|
| The sound itself, as a file | `python3 vexo-open-kit/examples/python/audio_to_text.py 42 --text no` (42 is the example band number: use yours). Nothing is sent anywhere. |
| Text of what was said | Paste your team's Deepgram key in the dashboard and switch on **Live transcript**, or run `audio_to_text.py 42` |
| An AI model that understands it | `python3 vexo-open-kit/examples/python/ask_the_band.py 42`: ask a question out loud, the answer is printed |

Text needs your team's Deepgram key, the AI model needs your team's OpenAI key. Setting the
keys, checking them and three AI examples are in
["Audio to text to AI" in AGENTS.md](AGENTS.md#audio-to-text-to-ai). More on Deepgram:
[docs/deepgram.md](docs/deepgram.md). Plain sound for any other service:
[docs/speech.md](docs/speech.md). While the microphone is on, the band shows a solid blue
light, and with a key the sound goes to Deepgram: tell the people near you.

## Build your own

Your code goes in `my-project/`, never inside the kit folder. Make it your own repository:

    git init
    printf 'vexo-open-kit/\n.env\n' >> .gitignore

That keeps the kit and `.env` (the file with your team's keys) out of your repository:
**`.env` never goes into a commit.** Then:

- Copy an example or one of the [six small projects](projects/README.md) and change it.
  [Getting started](docs/getting-started.md#write-your-own-program) shows the first program.
- Using an AI coding agent? Point it at [AGENTS.md](AGENTS.md) first.
- Every library call, in Python and JavaScript: [docs/api.md](docs/api.md).
- Writing the Bluetooth part yourself, in any language?
  [The Bluetooth contract, explained](docs/bluetooth-contract.md) shows how to connect and
  how every packet is built. It is also a page with drawings:
  `http://localhost:8000/vexo-open-kit/docs/bluetooth-contract.html`.

## What is in each folder

| Folder | What it is for |
|---|---|
| `dashboard/` | A ready web page to see and check your band. You do not edit it. |
| `examples/` | [Short programs, one idea each](examples/README.md), to copy from. |
| `projects/` | [Six small complete projects](projects/README.md) to start from. |
| `python/`, `js/` | The two libraries your programs import. You do not edit them. |
| `docs/` | [Getting started](docs/getting-started.md), [library API](docs/api.md), [speech and raw sound](docs/speech.md), [Deepgram](docs/deepgram.md), [the Bluetooth contract, explained](docs/bluetooth-contract.md). |
| `contract/` | [`contract.md`](contract/contract.md): the full contract, byte by byte, with test frames. |
| `AGENTS.md` | [The guide for AI coding agents](AGENTS.md), and the key setup for people. |
| `env.example` | An empty form for your team's two keys. Copy it to `my-project/.env`. |

For the kit's maintainers: [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

MIT. See [LICENSE](LICENSE).
