# Guide for AI coding agents

You are helping a student team build a project with a Vexo Open band: a wristband with a
motion sensor, a microphone and a colour LED. This kit holds the libraries that talk to it.
Read this file in full before you start. It is enough to do the whole job; open the other
files only when a task below points to them.

## Rule 1: ask for the band number first

Before you write any code, ask the team:

> What is your band number? It is written on the bag your band came in (number 42 means the band is called Vexo-HACK-42).

- Wait for the answer before you build anything. If you cannot ask (you run without a
  person, or as a helper of another agent), stop and hand the question back as your
  answer. Do not build "for now" with a number of your own and ask at the end.
- Use that number everywhere: in `Band.connect(...)`, in dashboard links, in commands.
- Never guess it, and never build or run with a placeholder number. Never copy the `42`
  or `7` from the examples in this kit into the team's code. This goes for the pretend
  band too: the number in the program is the team's number from the first line on.
- Never connect to another number. Other teams are in the same room with their own bands.
  `Band.connect(42)` looks only for the band called `Vexo-HACK-42`, and `?band=42` in the
  dashboard address makes the browser's list show only that band.
- When you write Bluetooth code yourself, without the kit's libraries, filter the scan by the
  exact band name built from the team's band number (`Vexo-HACK-07` for 7), together with the
  service UUID in `contract/contract.md`. Never connect to the first Vexo band the scan finds.
  Working examples for a web page and for Python are in "Connecting with your own Bluetooth
  code" in `docs/bluetooth-contract.md`.
- Keep the number in one place in the team's code (one constant or one command-line argument).
- Never write 42 into a team's program. 42 is the example number on these pages: ask for the
  team's band number and use that.

### Only the team's own band: hard rules

Once the team has told you their band number, these hold for everything you write and run.

1. You MUST ask for the team's band number before you write or run anything that connects.
   NEVER guess it, NEVER use 42, NEVER reuse a number from an example, a log or another
   conversation.
2. You MUST write the number down once in the team's project (one constant such as
   `BAND = 17`, or one config value) and use that one value in every connect, every scan
   and every dashboard address.
3. You MUST NEVER connect to, scan for, or send a command to any other band: not the first
   Vexo band found, not a band with a stronger signal, not a neighbour's band to test
   whether it works, not a list of bands. NEVER write code that scans without a filter on
   the exact name `Vexo-HACK-NN` for that number. NEVER write code that loops over band
   numbers. NEVER use the "whatever is on the cable" form
   (`Band.connect(None, transport="serial")`, in JavaScript
   `Band.connect(null, { transport: 'serial' })`) in a team's program without checking that
   `band.number` equals the team's number.
4. After every connect you MUST check that the band's reported number is the team's
   (`band.number`, `band.name`). `Band.connect(17)` does this by itself: it fails with code
   `wrong-band` and closes the link. In code that does not use the library, or that
   connected with `None` / `null`, check it yourself; if it is not the team's band,
   disconnect at once, change nothing on that band, and tell the user.
5. If the team's band cannot be found, you MUST NOT try another number. Tell the user to
   double-tap their band (it can then be found for 60 seconds) and to check the number on
   the bag. Use the pretend band (`--sim`) meanwhile.
6. If the user asks to connect to a different band, first confirm that it is their own
   team's band (a replacement), then change the one number. You MUST refuse to connect to
   another team's band, and say why: other bands belong to other teams, and that band's
   microphone and light would be taken over.
7. You MUST NEVER renumber a band. Only the organisers change a band's number
   (`SET_BAND_NUMBER`, `set_band_number`, `setBandNumber`).

Band 7 is called `Vexo-HACK-07`, band 42 is `Vexo-HACK-42`, band 123 is `Vexo-HACK-123`.
The library takes the plain number (`7`) and builds the name itself. Band numbers are 1 to
999; 0 is not a band you can ask for.

## Audio to text to AI

> **42 is only our example. Wherever you see 42 in a command, an address or a name, type the
> number written on your band's bag.** Band 7 is `Vexo-HACK-07`, and its dashboard address
> ends in `?band=7`.

For the team and for the agent alike: how the band's microphone becomes text, and how an
AI model makes sense of that text. Every command is typed in `my-project/`, the folder
that holds the kit. On Windows, type `python` wherever this file says `python3`.

    band microphone --> your laptop --> Deepgram --> text --> OpenAI --> an answer, a choice, notes
       (sound)          (Python)      (speech to text)       (AI model)          |
                                                                                 v
                                                      your code: the light, the screen, anything

Two online services do the work, and each needs a key. **Each team gets its own Deepgram
key and its own OpenAI key from the organisers.** They work during the event and are
switched off afterwards. A key is a password that is billed to someone: treat it like one.

### 1. Set your Deepgram key (sound to text)

In the terminal you start your programs from, with your key in place of the dots:

    export DEEPGRAM_API_KEY=...             (macOS, Linux)
    $env:DEEPGRAM_API_KEY = "..."           (Windows PowerShell)
    set DEEPGRAM_API_KEY=...                (Windows cmd, no quotes)

This lasts until you close that terminal. To set it once for every terminal, use the
`.env` file of step 3 instead.

In the dashboard (`http://localhost:8000/vexo-open-kit/dashboard/?band=XX`, see "The
dashboard for the team's band" below): paste the key into the field **Deepgram key for
Live transcript** in the Microphone panel and press Enter, then switch on **Live
transcript**. The key stays in that page's memory only. **Forget key** removes it; so does
reloading or closing the tab.

### 2. Set your OpenAI key (text to AI)

The same way, in the same terminal:

    export OPENAI_API_KEY=...               (macOS, Linux)
    $env:OPENAI_API_KEY = "..."             (Windows PowerShell)
    set OPENAI_API_KEY=...                  (Windows cmd, no quotes)

The OpenAI key is for Python programs on your own laptop. It never goes into a web page,
and the dashboard has no field for it: everyone who opens a page can read all of it.

### 3. Or keep both keys in a `.env` file

One file instead of typing the lines above in every new terminal. Copy the kit's empty
form into `my-project/` and write each key after its `=`, with no spaces and no quotes:

    cp vexo-open-kit/env.example .env       (Windows: copy vexo-open-kit\env.example .env)

The programs read `.env` from the folder you start them in. An environment variable wins
over the file. If `my-project/` is a git repository, put a line `.env` into its
`.gitignore` **before** you make the file, and check that `git status` does not list `.env`.

### 4. Check both keys (30 seconds, no band needed)

    python3 vexo-open-kit/examples/python/check_keys.py

A pretend band plays 6 seconds of a recording that is in the kit, Deepgram turns it into
text, and the AI model is asked one short question. You should see about this:

    Note: while this runs, everything the band's microphone hears is sent over the internet to Deepgram (deepgram.com). Tell the people near the band.
    Deepgram key: works. It heard: "In a spell of dry weather, when the birds could find very little to drink,"
    Note: the text is sent over the internet to OpenAI (openai.com), model gpt-6-luna.
    OpenAI key: works. The model gpt-6-luna said: "All good"

If a key does not work, its line says `NOT working` and why:

| What you see | What it means | What to do |
|---|---|---|
| `Transcription needs a Deepgram key` or `This needs an OpenAI key` | No key was found | Do step 1, 2 or 3 in this terminal. A new terminal has forgotten `export` and `set` lines |
| `status 401` | The service does not know the key | Copy it again: the whole key, no spaces. PowerShell needs the quotes, cmd must not have them. A key that was switched off also answers 401: ask an organiser |
| Deepgram `status 402`, or OpenAI `status 429, insufficient_quota` | The key's balance is used up | Tell an organiser |
| `status 429` | Too many requests or connections at once with this key | Close the other programs and browser tabs that use it, wait a few seconds, try again |
| OpenAI `status 404` or `status 400` | The model name is wrong | Remove the `OPENAI_MODEL` variable if you set one |
| `Could not reach api.deepgram.com` or `Could not reach api.openai.com` | No internet, or the network blocks the connection | Check the Wi-Fi; try a phone's hotspot; tell an organiser if it is the event's network |
| `cannot check the certificate` | This Python has no certificates (some Macs) | `python3 -m pip install certifi`, then run it again |

Only the status number is shown, never what the service wrote, and never the key.

### 5. Run the three examples

Each is one command and about 15 lines of code. `42` stands for your band number. While
one listens, the band's light is solid blue: the microphone is on, and what it hears goes
to Deepgram, then as text to OpenAI. **Tell the people near the band first.**

| Example | What it does | With your band | With no band |
|---|---|---|---|
| Ask the band | You speak a question, the answer is printed | `python3 vexo-open-kit/examples/python/ask_the_band.py 42` | `python3 vexo-open-kit/examples/python/ask_the_band.py --sim` |
| Voice commands by meaning | You say what you want in your own words ("something calm"), the model picks one of a few actions, and the band's light changes colour and pattern | `python3 vexo-open-kit/examples/python/voice_commands.py 42` | `python3 vexo-open-kit/examples/python/voice_commands.py --sim` |
| Meeting notes | A minute of talk becomes a summary and a list of action items | `python3 vexo-open-kit/examples/python/meeting_notes.py 42` | `python3 vexo-open-kit/examples/python/meeting_notes.py --sim` |

- `--sim` is a pretend band. It has no microphone, so it plays the kit's sample recording
  (a person reading the start of a fable, 16 seconds) as its sound. The examples then
  answer, act on or summarise the fable.
- `--say` takes typed words in place of the microphone, so you can try your own sentences
  with no band, no noise and no Deepgram key:

      python3 vexo-open-kit/examples/python/voice_commands.py --sim --say "make it calm and blue"

- `--seconds 20` listens longer. `--usb` uses the USB cable.
- The model's name is in one place, `MODEL` in `examples/python/ai.py` (`gpt-6-luna`, a
  small and fast one). To try another without changing code, set the environment variable
  `OPENAI_MODEL`.
- The kit talks to OpenAI with plain HTTPS from Python's standard library, in `ai.py`:
  nothing to install, and you can read every field that is sent. The official package
  (`python3 -m pip install openai`) does the same and more; it reads the same
  `OPENAI_API_KEY`, so you can switch at any time.

### 6. Build your own

A whole program: tap the band, speak for five seconds, read the answer. Save it as
`talk.py` in `my-project/` and run `python3 talk.py`:

```python
import sys
import threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "vexo-open-kit" / "examples" / "python"))
import ai                                       # text -> OpenAI    (vexo-open-kit/examples/python/ai.py)
import speech                                   # sound -> Deepgram (speech.py; it finds the library too)
from vexo_open.sync import Band

BAND = 7                                        # the team's band number: ask, never guess

tapped = threading.Event()
band = Band.connect(BAND)                       # pretend band: Band.connect(BAND, transport="sim")
band.on("tap", lambda e: tapped.set())          # in a handler only note what happened ...
band.configure(events=True)
print("Tap the band, then speak.")
while not tapped.is_set():
    band.wait(0.2)
words = speech.listen(band, 5)                  # ... and listen here, in the main program
band.disconnect()
print("You said:", words)
print("Answer:", ai.ask(words))
```

The four calls that do the work:

| Call | What you get |
|---|---|
| `speech.listen(band, seconds)` | The microphone for that long, as text (through Deepgram) |
| `ai.ask(text, instructions)` | The model's answer, as text |
| `ai.choose(text, tools)` | One action out of your list, with its details: `("set_light", {"colour": "red", "pattern": "blink"})`. Make each action with `ai.tool(name, description, colour=["red", "green"])` |
| `ai.extract(text, schema)` | A dictionary with the fields of your JSON schema filled in |

Three things to know:

- **Light or microphone, not both.** While the microphone is on, the band refuses LED
  commands (rule 4). Listen first, then set the light, as `voice_commands.py` does.
- **Call `speech.listen` in the main program, never inside a handler.** A handler runs
  alone; the sound waits behind it and nothing is heard.
- **A model can be wrong.** Let it choose among actions you wrote (`ai.choose`), never let
  it write code or commands that your program then runs.

### Ideas: motion + voice + AI

1. **Tap to ask.** Double-tap, ask a question, read the answer on a big screen.
2. **Mood light.** Say how you feel; the model picks a colour and a pattern for the light.
3. **Gesture coach.** Do an exercise; the model turns the motion numbers of each try into one short tip.
4. **Hands-free cooking timer.** "Ten minutes for the pasta": the model pulls out the time, the light blinks when it is over.
5. **Meeting helper.** Tilt the band to start and stop; notes and action items appear when it stops.
6. **Step storyteller.** Every 500 steps the model writes the next line of a story about your walk.
7. **Language partner.** Say a sentence in the language you are learning; the model corrects it, green light for right.
8. **Charades judge.** Act out a word with your arm and describe it; the model guesses from the motion summary and your hints.
9. **Fall check.** After a `free-fall` the band listens: "I am fine" cancels the alarm, silence or "help" raises it.
10. **Presentation coach.** Counts filler words in your talk and how much your hand moved, then gives two tips.

### Keep the keys safe

- Keys live in environment variables or in an untracked `.env` file. Nowhere else.
- Never in your code, in a commit, in a web page, in a screenshot, in the band's storage,
  and never pasted into a chat: not with a person, and not with an AI coding agent.
- Use only your own team's keys.
- **If a key leaks** (it got into git, a chat, a photo of a screen): tell an organiser at
  once. The key is switched off and you get a new one. Deleting the line afterwards does
  not help: assume it was copied.

The full rules are in "Safety: keys, text from a band, the microphone" at the end of this
file, and for Deepgram in `docs/deepgram.md`.

### For coding agents

Build on the kit's helpers; do not write a new speech or AI client. Read these files,
all in `vexo-open-kit/examples/python/`, they are short:

- `speech.py`: sound to text. `speech.listen(band, seconds)` returns the text of that
  many seconds of microphone. For text while people speak:
  `ears = speech.create().start(lambda text, final: ...)`, `band.on("audio", ears.feed)`,
  `band.configure(audio=True)`, and at the end `ears.stop()`.
- `ai.py`: text to AI. `ai.ask`, `ai.choose` with `ai.tool`, `ai.extract`; the model name
  is `ai.MODEL`. `ai.request(body)` sends any other body to OpenAI's Responses API.
- `ask_the_band.py`, `voice_commands.py`, `meeting_notes.py`: the three patterns, each a
  whole program. Copy the closest one into `my-project/` and change it. A copy needs the
  path line of `talk.py` (step 6, its fourth line) above its imports, or it does not find the helpers.
- Motion comes from the library, not from these helpers: `band.configure(motion=50)` and
  `band.on("motion", fn)` give `ax ay az` (g) and `gx gy gz` (degrees/second);
  `band.configure(events=True)` gives `tap`, `double-tap`, `free-fall`, `tilt` and the
  others in "What the band can do" below. To give motion to the model, send a few numbers
  you worked out (peak, average, count), not the raw stream.
- Both helpers read the keys themselves. Never ask for a key, never write one into a
  file, never print one. With no key, build and test with `--sim --say "..."` (needs only
  the OpenAI key) or with the motion parts alone, and tell the team which step above sets
  the key. The addresses of the two services are fixed in the helpers: leave them fixed.
- A page in a browser gets text from Deepgram with the team's key typed into a password
  field (`docs/deepgram.md`). It gets no OpenAI key, ever: AI calls are made from Python.

## Map of this kit

| Path | What it is | Read it when |
|---|---|---|
| `docs/api.md` | Every library call, event, field and error, JavaScript and Python side by side | You need a call that is not in this file |
| `docs/getting-started.md` | Step-by-step setup for a person, and a table of problems and fixes | Something does not connect |
| `docs/speech.md` | Microphone to text: the decoder, the speech interface, where keys go | The team wants the sound as samples, or speech-to-text |
| `docs/deepgram.md` | Deepgram, the online service that turns the band's sound into text: the key, the rules, the code, the errors | The team wants speech-to-text. Read it before you touch a key |
| `examples/python/`, `examples/web/`, `examples/node/` | Short working programs: LED, motion, motion graph, taps, storage, auto-reconnect, microphone to text, and three that send the text to an AI model (see "Audio to text to AI" above) | You want code to copy from. List: `examples/README.md` |
| `projects/` | Six small complete projects (reaction game, leash, clap lights, step diary, fall alarm, gesture trainer) that run on the pretend band and direct it. Not yet tried on a real band as whole projects (see below the table) | Look here FIRST, before you write from scratch, when the team wants a game, an alarm or a tracker, or anything else close to one of the six. Then start from it: copy the project's folder and `projects/shared` into `my-project/` ("Make one your own" in `projects/README.md`) |
| `dashboard/` | Web page that shows one band's live data and controls its LED | The team wants to see the band working, or to check the band without your code |
| `docs/bluetooth-contract.md` | The Bluetooth contract explained for people: how to connect with your own Bluetooth code (filtered on the band's name), every frame with a real example, how to get each kind of data. `docs/bluetooth-contract.html` is the same as a visual page with a frame decoder | The team wants to talk to the band without the kit's libraries, or to understand a frame |
| `contract/contract.md` | The byte format of every command and data frame | Only for writing a client in another language (for USB, follow the checklist in its section 2.2), or to understand a raw error |
| `js/src/`, `python/vexo_open/` | The libraries | Import them. Do not edit them |
| `scripts/`, `.githooks/`, `*/test/`, `python/tests/` | Checks and tests for the kit | Run them (see the end). Do not edit them |

Do not edit `js/`, `python/`, `contract/`, `dashboard/` or `scripts/`. They are the
kit. If something in them looks wrong, tell the team instead of changing it.

What is tested on a real band, and what is not: the kit's features (the libraries, the
dashboard, the examples) are tested on a real band. The six projects are not, as
whole projects. They run on the pretend band. Of their thresholds (the tunable numbers:
how loud is a clap, which signal strength is "far", how still is "still") a few were
measured on one real band on a desk (the clap jump, the motion sensor at
rest, the signal next to the laptop); the others are guesses. Each project's README says
which. They will need tuning. Tell the team so when you start from a project.

## Where the team's code goes

**Rule: the team's project is their own.** This kit is a reference, not a template and not the team's project. Write the team's code
in the team's own project folder and repository (`my-project/`), never inside the kit
folder. Do not treat the kit's examples and projects as the limit of what can be built:
the team may build anything, in any language, with the band's motion, taps, light and
microphone. The team's `.env` with its keys never goes into their repository.

Put the project in the team's own folder, with the kit inside it, unchanged:

    my-project/              the team's folder or repository; all new code goes here
      vexo-open-kit/         this kit (cloned or unzipped)
      app.py
      index.html

This is the only layout. Every sample in this file and in the other pages of the kit is
written for it and fails in another one (Python: `No module named 'vexo_open'`; web page:
the button does nothing). Three things to get right:

- The kit is a folder inside the project folder, not next to it.
- The kit folder is called exactly `vexo-open-kit`. A ZIP download often unpacks as
  `vexo-open-kit-main`: rename it.
- The team's files (`app.py`, `index.html`) are in `my-project/` itself, not in the kit.

The order is always the same: first the folder `my-project/`, then the kit goes inside
it. No kit on the computer yet: make `my-project/`, then clone or unpack the kit inside it.
The kit is there already (for example you were started inside the kit itself) and the
team has no folder yet: go one folder up, create `my-project/` there, move the whole kit
folder into it, and work in `my-project/` from then on:

    cd ..
    mkdir my-project
    mv vexo-open-kit my-project/
    cd my-project

(Windows: `move` instead of `mv`. If Windows says the folder is in use, close the programs
that have it open, or copy the folder instead.) Then check: `my-project/vexo-open-kit/AGENTS.md`
exists. A file a student saves inside the kit by mistake does not break the kit's tests,
but keep the kit clean anyway.

## Shortest working program: Python

`my-project/app.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "vexo-open-kit" / "python"))   # the library: a folder in the kit
from vexo_open.sync import Band

BAND = 7                                  # the team's band number: ask, never guess

band = Band.connect(BAND)                 # Bluetooth. Pretend band: Band.connect(BAND, transport="sim")
band.set_led("#00ff00")                   # USB cable: Band.connect(BAND, transport="serial")
band.on("tap", lambda e: print("tap"))
band.configure(events=True)               # nothing arrives until you turn it on
band.wait(5)                              # keep running for 5 seconds
band.disconnect()
```

Run it in `my-project/` (Python 3.9 or newer; on Windows type `python` wherever this file
says `python3`):

    python3 app.py

The first three lines tell Python where the library is: the folder `vexo-open-kit/python`
next to the program file. The path is built from the place of the file (`__file__`), so
the program can be started from any folder. Without those lines, or with the kit somewhere
else, the import fails with `No module named 'vexo_open'`. With the pretend band this
program prints `tap` once (the pretend band taps itself after 4 seconds). The library is
not installed with `pip`: it is used straight from the kit folder, so there is nothing to
install and nothing goes stale when the kit is replaced by a newer one.

A real band over Bluetooth needs `python3 -m pip install bleak`; the USB cable needs
`python3 -m pip install pyserial`; the pretend band needs neither. Two things need more,
with a real band and with the pretend one alike: turning the microphone's sound into
samples needs `python3 -m pip install av`, and turning it into text needs the team's
Deepgram key and the internet (nothing to install; see `docs/deepgram.md`). `av` is
written without a version on purpose (tested with 15.1.0 on Python 3.9 and 19.0.2 on
Python 3.12). `vexo_open.sync` is the
plain version. `from vexo_open import Band` is the same API with `async` / `await`.

When a band call fails and the program does not catch it, the program ends with one
sentence instead of a traceback, for example
`Problem (denied): The band refused LED_SET because the microphone is on. ...` and under
it `(line 9 of app.py)`. The
exit code is 1 and the band is disconnected first. Only band problems (`VexoError`) are
shortened; a bug in the program (`NameError`, `ZeroDivisionError`, ...) keeps its full
traceback. To see the traceback of a band problem too, start the program with the
environment variable `VEXO_TRACEBACK=1`.

A handler (the function given to `band.on`) may call the band and use the answer, for
example `led = band.get_led()` inside a tap handler (rule 17).

## Shortest working program: web page

`my-project/index.html`:

```html
<button id="go">Connect</button>
<p id="out">Not connected.</p>
<script>  // a double-clicked file cannot load the library: say so on the page instead of doing nothing
  if (location.protocol === 'file:') document.getElementById('out').textContent = 'This page was opened by double-click, so it cannot load the library. In this folder run:  python3 -m http.server 8000 --bind 127.0.0.1  Then open http://localhost:8000/' + location.pathname.split('/').pop() + ' (8000, or the port you started it with)';
</script>
<script type="module">
  import { Band } from './vexo-open-kit/js/src/index.js';

  const BAND = 7;                                   // the team's band number: ask, never guess
  const say = (text) => { document.getElementById('out').textContent = text; };

  document.getElementById('go').onclick = async () => {
    try {
      say('Connecting... pick your band in the list.');
      const band = await Band.connect(BAND);        // pretend band: Band.connect(BAND, { transport: 'sim' })
      await band.setLed('#00ff00');                 // USB cable: Band.connect(BAND, { transport: 'serial' })
      say(`Connected to ${band.name}. Tap it.`);
      band.on('tap', () => say('tap'));
      await band.configure({ events: true });       // nothing arrives until you turn it on
    } catch (e) {
      say(e.message);                               // the library's messages say what to do next
    }
  };
</script>
```

Serve it from `my-project/` and open it in Chrome or Edge:

    python3 -m http.server 8000 --bind 127.0.0.1

then <http://localhost:8000/index.html>. Press Connect: the page says `Connecting...`, then
`Connected to Vexo-HACK-07. Tap it.`, and `tap` when the band is tapped (the pretend band
taps itself after 4 seconds). A problem is shown on the page in the library's own words.
Start one server only, in `my-project/`: it also serves the kit's dashboard and example
pages, under `http://localhost:8000/vexo-open-kit/...`. Rules for web pages:

- Always type `--bind 127.0.0.1`. It keeps the server on this computer. Without it, everyone
  on the same Wi-Fi (the whole event) can read every file in the folder.
- If the command ends with `OSError: [Errno 48] Address already in use` (the number differs
  per system), another program has port 8000. Use another port, in the command and in the
  address: `python3 -m http.server 8001 --bind 127.0.0.1` and `http://localhost:8001/index.html`.
  Then change the number in every address in this file and in the kit's other pages too,
  links included: the links always point to 8000.
- The address is `http://localhost:8000/...`. `http://127.0.0.1:8000/...` works too.
- Bluetooth and USB only start from a click, so connect inside a button handler.
- The page must come from `http://localhost` or an `https://` address, never a double-clicked
  file. A double-clicked page (`file://`) cannot load the library; the small first script
  in the sample says so on the page, with the command and the address to use instead.
- If nobody picks a band in the browser's list, `Band.connect` fails after 2 minutes with
  code `cancelled`. Catch the error and show its `message` on the page, as the sample does.
  Never leave a result only in the console: the team does not look there.
- The library has no build step and no packages. Import the file as shown. The path starts
  at the folder of the page, so it changes when the page is somewhere else.
- Node has no Web Bluetooth. In Node the JavaScript library only works with `transport: 'sim'`.
  Without it a Node program ends with one sentence, `Problem (no-web-bluetooth): ...`, and
  exit code 1, like a Python program does for a band problem nobody catches.

## No band at hand: the pretend band

The pretend band (simulator) speaks the real protocol, including the refusals. Use it to
develop and to test, then switch to the real band by removing the transport option.

- Python: `Band.connect(BAND, transport="sim")`
- JavaScript: `Band.connect(BAND, { transport: 'sim' })`
- Kit examples: `python3 vexo-open-kit/examples/python/led.py --sim`, `node vexo-open-kit/examples/node/hello.mjs` (typed in `my-project/`)
- Example web pages: tick **Pretend band**. Dashboard: press **Try without a band**.
- The pretend band has firmware 1.1, like every event band (rule 13). To test
  against a pretend band with the older firmware 1.0, make it yourself and pass it as the transport.
  Python: `from vexo_open.transports.sim import SimTransport`, then
  `Band.connect(BAND, transport=SimTransport(BAND, firmware="1.0"))`. JavaScript:
  `Band.connect(BAND, { transport: new SimTransport(BAND, { firmware: '1.0' }) })`, with
  `SimTransport` imported from the same file as `Band`.

### What the pretend band does by itself

Left alone it sends calm, made-up data. Know this before you decide that a program "works":

- **Motion:** a gentle swing that repeats every 2 seconds. `ax` and `ay` stay within 0.12 g,
  `az` between 0.96 and 1.04, turning below 30 degrees/second. Never a shake, a fall or a flip.
- **Gestures:** a `tap` 4 seconds after connecting, a `double-tap` at 8 seconds, and so on
  in turns, every 4 seconds. No other gesture, ever.
- **Microphone:** the `audio` stream is silence (real Opus packets that decode to silence),
  so speech-to-text hears nothing. `mic-level` is a made-up voice that swells and fades
  every few seconds (`rms_db` from about 50 down to 10 or 20); it has nothing to do with
  the `audio` packets.
- **Steps:** walking at about 110 steps a minute; `steps` starts at 1200 and goes up by 2
  with every reading.
- **Battery** 87 percent, on battery, 1 percent less each minute. **Chip temperature**
  about 31. **Signal** about -55 dBm. It behaves like a band on Bluetooth.
- **Storage:** empty, in memory: gone when the program ends, unless the program names a
  file (`store_file=` / `storeFile:`).

### Directing it: make things happen when you say so

For a tap at a known moment, a motion, a clap, a weak signal or a dropped link, make the
pretend band yourself and tell it what to do. In `app.py`, in place of the connect line:

```python
from vexo_open.transports.sim import SimTransport                         # JavaScript: imported next to Band
band = Band.connect(BAND, transport=SimTransport(BAND, auto_tap=False))   # no taps of its own
band.on("free-fall", lambda e: print("it fell"))
band.configure(events=True, mic_level=True)
band.transport.tap("free-fall")                                           # prints: it fell
band.transport.set_loudness(0.9)                                          # a loud room, until you say otherwise
```

The calls: `tap(kind)`, `play_motion(...)`, `stop_motion()`, `set_loudness(level)`,
`play_loudness(...)`, `set_rssi(dbm)`, `set_battery(percent)`, `set_activity("run")`,
`drop_link(seconds)`, `power_cycle()`, `other_program_starts("motion")`,
`other_program_closes()` (rule 7), `after(seconds, fn)`. JavaScript: the same in
camelCase (`playMotion`, `setLoudness`, `setRssi`, `dropLink`, ...), called on the
`SimTransport` object itself. In a plain Python program (`vexo_open.sync`) always call
them as `band.transport.NAME(...)`, as above: in the main program, in a handler and in a
function given to `after` alike. That runs the call on the band's own thread; the
`SimTransport` object you made is not safe to call from the program's thread. The full table is "The pretend band: making things
happen" in `docs/api.md`. Never reach into its methods that start with `_`.

A voice for the pretend band: give it the packets of a sound file. The kit has one,
`dashboard/speech/sample-speech.opus` (16 seconds of a person reading aloud, public domain).
Python: `Band.connect(BAND, transport="sim", audio_packets=audio.ogg_packets(FILE))` with
`from vexo_open import audio`. JavaScript: `new SimTransport(BAND, { audioPackets: oggPackets(bytes) })`.
Both in full: "A voice for the pretend band" in `docs/api.md`.

### A gesture of your own, from raw motion

The band knows seven gestures (the table below). Anything else (a shake, a punch, a slow
turn of the wrist) the team builds from `motion` readings: keep the last half second,
work out one number, compare it with a limit. The pretend band's own swing never sets such
a detector off, so play the gesture with `play_motion` to test it. A shake detector, in
`app.py` in place of everything below the line `BAND = 7`:

```python
import math
from collections import deque
from vexo_open.transports.sim import SimTransport

recent = deque(maxlen=25)                     # the last half second, at 50 readings a second
quiet_until = 0

def on_motion(m):
    global quiet_until
    recent.append(math.sqrt(m.ax ** 2 + m.ay ** 2 + m.az ** 2))   # |a| in g: 1.0 while the band is still
    if m.t > quiet_until and max(recent) - min(recent) > 1.5:     # |a| jumped up and down: a shake
        print("shake")
        quiet_until = m.t + 1000              # one shake, one print: ignore the next second

band = Band.connect(BAND, transport=SimTransport(BAND))
band.set_motion(accel_range=8)                # the sensor measures up to 2 g unless you say so; a shake goes beyond
band.on("motion", on_motion)
band.configure(motion=50)
band.wait(1)                                  # the made-up swing: nothing is printed
band.transport.play_motion(lambda s: dict(az=1 + 1.5 * math.sin(40 * s)), seconds=0.5)   # shake it, by code
band.wait(1.5)                                # prints: shake
band.disconnect()
```

With the real band, remove `transport=...` and the `play_motion` line, and shake it. Pick
the limit (1.5 here) and the window from real readings: print `max(recent) - min(recent)`
for a while, with and without the gesture. For fast gestures use a higher rate (rule 14).

**The sensor's range.** After every connect the accelerometer measures up to 2 g in each
direction and the gyro up to 2000 degrees/second. A reading beyond the range is cut off
at the range: with the default, a hard shake, a punch or a clap of the hands reads
exactly 2.0 and the real peak is lost. For such gestures widen the range first, as the
sample does: `band.set_motion(accel_range=8)` (2, 4, 8 or 16 g; JavaScript
`band.setMotion({ accelRange: 8 })`); the gyro takes `gyro_range=500` (250, 500, 1000 or
2000). `band.get_motion()` reads both back. The pretend band cuts off in the same way, so
a played motion like the one above (`az` up to 2.5 g) only tests the detector honestly
with the range set. Like every setting, the range is back at 2 g after a disconnect.

## The dashboard for the team's band

In `my-project/` run `python3 -m http.server 8000 --bind 127.0.0.1` (the same single
server as for the team's own page), then open
`http://localhost:8000/vexo-open-kit/dashboard/?band=XX` in Chrome or Edge, with the
team's number in place of XX (`7`, `07` and `007` all mean band 7). This local address is
the one to give the team: the dashboard is opened from the team's own computer.
The dashboard opened by double-click does nothing, and says so on the page after a second.
Close the dashboard before running the team's program: the band takes one Bluetooth
connection at a time, and a program on the USB cable would share the band with it (rule 7).

The Motion panel has a rate picker and a sensor picker (both, acceleration only, turning
only). It offers what this band can do on this link; a rate that is too fast for the link
is shown greyed out with the reason. Next to it the page shows the rate that really
arrives and how many samples were lost. A band with firmware 1.0 offers 10, 25, 50 and 100
with both sensors. To see that view with the pretend band add `?firmware=1.0` to the
address; `?link=usb` makes the pretend band behave like one on the USB cable.

The page also shows the steps since it connected (with walking or running pace; the
band itself sends a lifetime total, see below the table in the next section), the
battery, the signal strength and the chip's temperature (the chip
inside the band, a few degrees above the room; not skin temperature). The loudness bar in
the Microphone panel is scaled for a real room: empty when it is quiet, about two thirds
full for speech near the band. It draws `rms_db` from 52 (empty) to 12 (full), not the
library's `level`, which is about 0.5 in a quiet room. **Connect by USB cable** opens the
browser's list of every port on the computer: pick "Vexo Open band" (`cu.usbmodem...` on a
Mac, `COM` and a number on Windows, `ttyACM...` on Linux).

The Microphone panel has two optional switches, both off at the start: **Listen** plays
the band's microphone through the computer's speakers, and **Live transcript** shows
text as people speak. The transcript comes from Deepgram, an online service: the team
types its own Deepgram key into the key field on the page (it stays in that page's memory
until the tab is reloaded or closed, and is stored nowhere), and while the switch is on
the band's sound goes over the internet to that company; the page shows a line that says
so the whole time. Without a key the switch says that the transcript needs one, and
nothing is sent; Listen and everything else on the page work with no key. It works best
with a clear voice within arm's length of the band. Both need a current Chrome or Edge. With
**Try without a band** the pretend band speaks with a recorded human voice, so both can be tried
with no hardware; that voice is a recording built into the page, not the sound in the room,
and the page says so. `examples/python/audio_to_text.py` does the same from Python: it
prints text while it listens. A project is free to use any other speech-to-text.

## What the band can do

| Part | How to use it (Python names; JavaScript uses camelCase) |
|---|---|
| Motion: acceleration and turning speed | `configure(motion=50)` then `on("motion", fn)`: `ax ay az` in g, `gx gy gz` in degrees/second. Rates 10, 25, 50, 100 on every band |
| Fast motion (firmware 1.1 only, rules 13 and 14) | `configure(motion=416, sensors="accel")`: rates up to 1666, `sensors` `"accel"`, `"gyro"` or `"both"`. `motion="fastest"` = the most the link carries; `motion_limits()` tells the numbers. Each `motion` event has `n` (sample number) |
| Gestures | `configure(events=True)` then `on("tap")`, `on("double-tap")`, `on("free-fall")`, `on("tilt")`, `on("orientation")`, `on("motion-start")`, `on("motion-stop")` |
| Steps | `configure(activity=True)` then `on("activity")`: `steps`, `cadence`, `state` |
| Microphone | `configure(audio=True)` then `on("audio")`: raw Opus packets. `configure(mic_level=True)` then `on("mic-level")`: loudness only, no decoding. Both lock the LED (rule 4) |
| LED | `set_led("#ff8800", brightness=0.5, pattern="blink", period=500)`, `led_off()`, `identify()` |
| Battery, chip temperature | `configure(battery=True)`, `configure(temperature=True)` |
| How often a stream sends (firmware 1.1 only, rule 16) | Seconds instead of `True`: `configure(battery=60, mic_level=0.02, activity=0.5)`. Or `set_interval("battery", 60)`, `get_interval("battery")` |
| Real time (rule 15) | Every data event has `time`. `band.time()`, `band.set_time()`, `band.clock` |
| Signal strength | `configure(link=True)` then `on("link")`: `rssi`. Bluetooth only (rule 12). `rssi` can be `None` / `null` (no reading): handle it |
| Storage | `band.storage.put(7, "hello")`, `get_text(7)`, `delete(7)`, `wipe()`: keys 0..999, values up to 232 bytes (rule 6) |

Not on this band: heart rate, skin temperature, vibration, a screen, a speaker.

`steps` in an `activity` event is the band's lifetime total: it goes on from one connection
to the next and is kept across restarts. It is not "steps since the program connected".
For that, remember the first `steps` the program sees and subtract it (the dashboard does).

## Rules that surprise people

1. **Everything is off until configured.** No data arrives before `configure(...)`.
2. **The band resets when the link closes.** LED off, all streams off, all settings back to
   defaults. Only storage and the band number are kept (and, on firmware 1.1, the clock
   while the band has power). Every connect must set up again.
3. **A dropped link comes back only if you ask for it.** Connect with
   `Band.connect(BAND, auto_reconnect=True)` (JavaScript: `{ autoReconnect: true }`). When
   the link is lost the library then tries again for 2 minutes (`reconnect_seconds=120` /
   `reconnectSeconds: 120`), and afterwards sets the LED, the settings and the streams
   again by itself (on firmware 1.1 also the fast motion setting, the intervals and the
   band's clock). In a browser no new click is needed. Events: `reconnecting`
   (`reason`, `seconds`), then `reconnected` (`attempts`, `seconds`, `failed` = settings the
   band refused) or, when it gives up, `disconnect` (`reason` `lost`, `reconnect`
   `gave-up`). `band.reconnecting` is true in between, and every command fails with
   `not-connected`: catch that, or wait for `reconnected`. It never reconnects after the
   program called `disconnect()`, `restart()` or `sleep()`, after a `critical-battery`
   notice, or when another band answers (`disconnect` with reason `wrong-band`). Without
   the option nothing comes back: listen for `disconnect` (reason `lost`), call
   `Band.connect` again and repeat every `configure` and `set_led`.
4. **No LED while the microphone is on.** With `audio` or `mic_level` on (either one), the
   light is solid blue and `set_led`, `led_off` and `identify` fail with code `denied`.
   Turn both off first with `configure(audio=False, mic_level=False)`; the team's LED
   setting then comes back by itself. A project cannot show loudness on the band's own LED.
5. **Audio is raw Opus.** Each `audio` event holds one Opus packet (16 kHz, mono, 20 ms),
   not sound samples. Use the kit's decoder to get plain samples (PCM). JavaScript in
   Chrome or Edge: `new BandAudio((samples) => ...)` and `band.on('audio', (a) => sound.push(a))`
   (nothing to install). Python: `PcmDecoder().feed(a)` from `vexo_open.audio` (needs
   `python3 -m pip install av`). Make the `PcmDecoder()` once, before `configure(audio=True)`:
   the first one takes a few seconds (Python loads `av`), so print "loading the sound
   decoder..." before it. Details: "Sound" in `docs/api.md`. For text, copy
   `examples/python/audio_to_text.py`: it uses `examples/python/speech.py`, one small
   interface (`start`, `feed`, `stop`) over Deepgram, an online speech-to-text service
   (it takes the Opus packets as they are: nothing to install, nothing to decode). It
   needs the team's Deepgram key, and the sound leaves the computer: follow safety rule 1
   below and `docs/deepgram.md` exactly. A team can put another speech service behind the
   same interface (`docs/speech.md`).
6. **Storage is not private, and not fast.** The next person who connects can read, change
   or erase it. Never store a secret, a name or a password. The band accepts 10 writes a
   second; the library waits and retries for up to 3 seconds, then fails with `busy`. So
   do not write on every motion sample. `wipe()` returns when the erase has finished, which
   can take seconds. An empty store holds about 240 kB (1000 keys of 232 bytes).
7. **One Bluetooth connection at a time, and USB and Bluetooth share the band.** While one
   program, tab or dashboard is connected over Bluetooth, nothing else can find the band.
   So when connecting fails with "Could not see Vexo-HACK-XX" (`not-found-nearby`), or the
   band is missing from the browser's list, first ask what else is connected (the
   dashboard tab, another program, a teammate's laptop) and have it closed; a double-tap
   alone cannot help. One program on the USB cable can be connected beside one on
   Bluetooth. Those two share the band: its settings, its one light (the last `set_led`
   wins; the light goes off by itself only when the last program disconnects) and its
   streams. A stream goes to the program that started it last, so starting `motion` over
   USB silently takes it away from a dashboard on Bluetooth, and when the USB program ends
   the band stops that stream. The library notices after about 3 seconds and gives the
   event `stream-taken` (`streams`, `stopped`, `message`; printed once if nobody listens).
   Nothing comes back by itself: close the other program, then call `band.take_back()`
   (JavaScript `band.takeBack()`; the dashboard has a **Take it back** button). Never run
   the team's program on one link while the dashboard is open on the other and expect
   both to get the data. "Two programs on one band" in `docs/api.md`.
8. **Double-tap opens the connect window.** The band can only be found for 60 seconds after
   a double-tap (it blinks blue), after power-on, or for 2 minutes after a dropped link.
   If connecting fails, first make sure nothing else is connected to the band (rule 7),
   then tell the team to double-tap the band and try again.
9. **iPhone and iPad browsers cannot connect.** No browser on iOS has Web Bluetooth. Firefox
   and Safari on laptops do not either. Use Chrome or Edge on a laptop or Android, or Python.
10. **The USB cable is the fallback.** When Bluetooth will not work, plug the band in with a
    data cable and use `transport="serial"` (Python, needs `pyserial`) or
    `{ transport: 'serial' }` (Chrome or Edge on a laptop). No double-tap is needed.
    The first time a band is plugged into a Mac, macOS may ask "Allow accessory to
    connect?". Until the team presses Allow (or allows it under System Settings, Privacy &
    Security, "Allow accessories to connect") there is no port and connecting fails with
    "No band found on USB". The port is `/dev/cu.usbmodem...` on a Mac, `COM` and a number
    on Windows, `/dev/ttyACM...` on Linux.
    Keep handlers short over the cable: a web page that computes for seconds without a
    pause takes no bytes from the band meanwhile, and stream frames are lost (`notice`
    with kind `stream-dropped`).
11. **Wrong band.** If the band that answers has another number, `connect` fails with code
    `wrong-band`. Do not work around it; ask the team to check the number on their band.
12. **The cable is not the same as Bluetooth in two places.** `link` (signal strength) is
    Bluetooth only: over USB `configure(link=True)` fails with `not-available` and turns
    on nothing from that call, and `get_radio()` gives `rssi` and `phy` `None`, `interval_ms`
    0 and `mtu` 0. `sleep()` over Bluetooth closes the link (`disconnect`, reason `sleep`);
    over USB the streams and the LED go off but the link stays open and keeps answering.
13. **Event bands run firmware 1.1.0.** `band.info.firmware` is `1.1.0` and
    `band.info.features` lists `fast-motion`, `intervals` and `clock`. The library still
    knows the older firmware 1.0, which no event band has: there `features` is empty, and
    a call that needs 1.1 fails with `not-available`, the message says which firmware is
    needed, and nothing is sent. To make the library use only what 1.0 can do, connect
    with `Band.connect(BAND, protocol="1.0")` (JavaScript `{ protocol: '1.0' }`).
14. **Fast motion has a limit for each link.** Rates are 10, 25, 50, 100, 208, 416, 833,
    1666 samples per second; another number is `bad-arg`. The most a link carries:
    Bluetooth 416 with both sensors, 833 with one; USB cable 833 with both or with the
    gyro alone, 1666 with the accelerometer alone. More than that fails with `denied`
    before anything starts, and the message names the way out. Do not hard-code the
    table: use `configure(motion="fastest", sensors="accel")`, or ask
    `band.motion_limits()` (JavaScript `motionLimits()`), which gives `rates`, `accel`,
    `gyro`, `both`, `transport`. In a `motion` event the sensor that was left out is `None` /
    `null`, so with `sensors="accel"` do not do arithmetic on `gx`. `n` counts the samples
    from 0; a jump means samples were lost. Place samples in time with `t` or `time`, never
    by counting at the nominal rate (the real rate is a few percent off). Both always go up from
    one sample to the next on one connection, also after lost samples. At 833 a second
    a handler runs 833 times a second: collect in it, do the slow work elsewhere. With
    `audio` on over Bluetooth stay at 208 (both) or 416 (one sensor).
15. **`time` is real time, and its unit depends on the language.** Every data event has
    `time` next to `t`. Python: seconds since 1970 as a float, like `time.time()`
    (`datetime.fromtimestamp(e.time)`). JavaScript: milliseconds since 1970, like
    `Date.now()` (`new Date(e.time)`). `t` is the band's own counter in milliseconds since
    it started; use it for the time between two readings. On firmware 1.1 the band has a
    clock, and the library sets it from the computer on every connect and reconnect
    (`band.clock` is `"band"`). On firmware 1.0 there is no clock on the band: `time` is
    worked out from the computer's clock and the band's uptime (`band.clock` is
    `"computer"`). For `audio`, `battery` and `notice` events `time` is based on when they
    arrived. `band.time()` returns `time`, `set`, `t`, `source`; `band.set_time()` sets the
    band's clock again (1.1 only). Anyone who connects can set the clock: like storage, it
    is not protected. Across a dropped link that auto-reconnect gets back, `t` goes on and
    `time` is paired with the computer's clock again; after a restart of the band `t`
    starts again near 0 while `time` stays right, so compare readings from before and
    after a restart with `time`, never with `t` ("Time" in `docs/api.md`).
16. **Intervals are in seconds, and need firmware 1.1.** `activity`, `battery`,
    `temperature`, `link` and `mic_level` take a number of seconds instead of `True`
    (fractions allowed; the number also turns the stream on). Allowed: 0.1 to 3600 for
    `activity`, `temperature` and `link`; 1 to 3600 for `battery`; 0.02 to 10 in steps of
    0.02 for `mic_level`. Outside that: `bad-arg`, the message names the range, nothing in
    the call is changed. `set_interval("battery", 60)` changes the pace without turning the
    stream on or off; `set_interval("battery", 0)` goes back to "only when it changes". On
    firmware 1.0 only `temperature` takes a number (whole seconds, 1 to 255). To run on
    both without a branch, write `"fastest"` instead of a number:
    `configure(mic_level="fastest")` is the shortest interval the band allows (0.02 on
    1.1), and on 1.0 it simply turns the stream on at its fixed pace (0.1) instead of
    failing; `set_interval("mic_level", "fastest")` returns the pace you got in `seconds`.
17. **Python handlers run one at a time on a background thread.** With `vexo_open.sync`, a
    handler may call the band and use the answer: `band.get_led()`, `band.set_led(...)`,
    `band.storage.get_text(7)` and `band.wait(2)` all work inside a handler. While one
    handler runs, the next events wait in line, so keep handlers short; with fast motion,
    only collect in the handler. At most 5000 events wait in line: when a handler is so
    slow that more pile up, the oldest stream events (motion, sound, levels) are dropped,
    one line on the screen says so and `band.dropped_events` counts them; answers to
    commands and link news are never dropped. An error in a handler is printed and the
    program goes on; a band problem is printed as one sentence, as in the main program.
    With the async `Band`, write the handler as `async def` and `await` the calls; in
    JavaScript, make it an `async` function.

## Safety: keys, text from a band, the microphone

1. **No keys in code.** The team's Deepgram key (or any key, token or password) never
   goes into a program file, a web page, a commit, a log or a `print`, and never into band
   storage (rule 6: anyone can read it). Read it when the program starts, from an
   environment variable or from a file that is listed in `.gitignore`. For Deepgram, do
   exactly this:
   - The key's name is `DEEPGRAM_API_KEY`. `examples/python/speech.py` reads it from the
     environment, or from a file called `.env` in the folder the program is started in
     (one line, `DEEPGRAM_API_KEY=...`). Your own code reads `os.environ["DEEPGRAM_API_KEY"]`.
   - **Never ask the team to paste the key into the chat with you, and never write it into
     a file yourself**, not even `.env`. Tell the team to type `export DEEPGRAM_API_KEY=...`
     (Windows: `set DEEPGRAM_API_KEY=...`) in their own terminal, or to create `.env`
     themselves. The exact lines, also for PowerShell, are in "Audio to text to AI".
     If the key does show up in the conversation, treat it as leaked (below).
   - Before a `.env` file exists in a project that is a git repository, make sure `.env`
     is in that project's `.gitignore`, and check with `git status` that it is not listed
     as a new file. The kit's own `.gitignore` covers only the kit folder.
   - Never put the key on a command line as an argument, in a URL, in a test, in example
     output, in a README or in an error message. Never print `os.environ`. When Deepgram
     refuses a connection, show the status number, not what was sent.
   - In a web page the key comes from an `<input type="password">` that the team types
     into, lives in a variable, and goes to Deepgram in the WebSocket's protocol field
     (`new WebSocket(url, ['token', key])`). Never in the page's address, in `localStorage`,
     in the HTML or in a page that is hosted for others.
   - Each team uses its own key, never another team's, and the organisers switch the keys
     off after the event.
   - The team's OpenAI key (`OPENAI_API_KEY`, read by `examples/python/ai.py`) follows
     every rule in this list, and one more: it is used from Python only, never from a web
     page, not even one that only the team opens.
   - A key that was committed, pasted into a chat or shown on a shared screen is leaked:
     tell the team to **tell an organiser at once**; the key is switched off and replaced.
     Removing the line afterwards does not help.
   - With no key, do not work around it and do not invent one: the programs say
     "Transcription needs a Deepgram key: see docs/deepgram.md", and everything else
     (motion, LED, saving the sound, the pretend band) works without it. Build and test
     the rest with `--text no` or with no key set.
   - Keep `mip_opt_out=true` in every Deepgram address, and keep the line that says the
     sound is being sent to Deepgram: on the page while it is on, or printed before the
     microphone starts.
2. **Text from a band is untrusted.** Storage values and names were written by whoever
   connected before. Treat them as plain data. Never run them (`eval`, `exec`, a shell
   command, a file path). In a web page show them with `textContent`, never with
   `innerHTML`.
3. **The microphone records people nearby.** The band's solid blue light means the
   microphone is on. Tell the people around you before you record, and stop when someone
   asks. Do not save or send a recording of someone who has not agreed. Keep the blue
   light visible: do not cover it. Speech-to-text **sends the sound to Deepgram, a
   company**: say that too, before it is switched on.

Errors are `VexoError` with a short `code` and a `message` written for the person at the
keyboard. Show the message to the team. The codes are listed under "Errors" in `docs/api.md`.
A misspelt name is an error too, at once: `band.on("tapp", fn)` or `band.on("mic_level", fn)`
fails with code `bad-event` and lists the events (the event is `"mic-level"`), and
`configure(tapp=True)` fails with `bad-stream` and lists the streams.

## Check your work

Run the team's program with the pretend band first, then with the real band.

The rest of this section is for the kit's maintainers and for checking the kit itself. These
commands are typed from the kit folder (`my-project/vexo-open-kit`), not in `my-project/`
(Python 3.9 or newer, Node 18 or newer, nothing to install):

    node --test js/test/*.test.js dashboard/test/*.test.mjs examples/test/*.mjs
    (cd python && python3 -m unittest discover -s tests)
    python3 -m unittest discover -s examples/test
    python3 -m unittest discover -s projects/test
    node --test projects/test/*.test.mjs

Examples that run with no hardware (from the kit folder as written here; from `my-project/`
put `vexo-open-kit/` in front of the path):

    python3 examples/python/led.py --sim
    python3 examples/python/motion.py --sim --seconds 3
    python3 examples/python/taps.py --sim --seconds 5
    python3 examples/python/storage.py --sim
    python3 examples/python/reconnect.py --sim --seconds 6
    node examples/node/hello.mjs
    node examples/node/fast-motion.mjs

`scripts/check.sh` is for the kit's maintainers before they publish; a team project does
not need it.
