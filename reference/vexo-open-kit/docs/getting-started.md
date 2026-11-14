# Getting started

From nothing to a blinking LED in about ten minutes.

This kit is only the instructions and the reference. Your project is your own: your own
folder, your own repository, any language, and whatever you want to build. The examples
here are for copying from, not the limit of what you can make.

> **42 is only our example. Wherever you see 42 in a command, an address or a name, type the
> number written on your band's bag.** Band 7 is `Vexo-HACK-07`, and its dashboard address
> ends in `?band=7`.

| Step | Time |
|---|---|
| [Get the kit](#get-the-kit-2-minutes) | 2 minutes |
| [1. See your band working in the dashboard](#1-see-your-band-working-2-minutes) | 2 minutes |
| [2. Your first Python program](#2-your-first-python-program-5-minutes) | 5 minutes |
| [3. Your first web page](#3-your-first-web-page-1-minute) | 1 minute |

No band yet? Every step also works with a pretend band. Look for "No band?" in each step.

## What you need

- Your band and the bag it came in. Your band number is written on the bag. Number 42
  means the band is called **Vexo-HACK-42**. Band numbers are 1 to
  999; 0 is not a band you can ask for.
- A laptop (Windows, macOS, Linux or a Chromebook) with **Chrome** or **Edge**.
- Bluetooth switched on.

An iPhone or iPad cannot connect to the band from a web page. Firefox and Safari cannot
connect either. Everything on this page is done on a laptop. (Chrome on an Android phone
has Bluetooth, but a phone cannot open `localhost` on your laptop: a page for a phone has
to be published on an `https://` address.)

## Get the kit (2 minutes)

1. **Install Python** (3.9 or newer) from <https://www.python.org/downloads/>.
   On Windows, tick the box in the installer that adds Python to PATH.
   macOS and Linux often have Python already: type `python3 --version` in a terminal to check.
2. **Get the kit, and give it a home.** First the folder, then the kit inside it. Make a
   new folder called `my-project`. Download
   this kit as a ZIP file from the page where you found it, and unpack it inside
   `my-project`. If you know git, clone it there instead. If the kit is on your computer
   already, move the whole kit folder into `my-project`. The kit folder must be called
   exactly `vexo-open-kit`. A ZIP often unpacks as `vexo-open-kit-main`: rename it. You
   now have:

       my-project/            your own programs and pages go here
         vexo-open-kit/       the kit; leave it as it is

3. **Open a terminal in `my-project`**: the folder that holds the kit, not the kit folder
   itself. (Windows: open the folder, type `cmd` in the address bar, press Enter. macOS and
   Linux: open a terminal, type `cd ` and a space, drag the folder in, press Enter.)

Every command on this page is typed there, in `my-project/`. On Windows, type `python` (or
`py`) wherever this page says `python3`.

## 1. See your band working (2 minutes)

The dashboard is a web page in the kit that shows what your band does. A web page can only
use Bluetooth when it comes from a web server, not when you double-click the file. Python
has a small web server built in.

1. In the terminal, in `my-project/`, run:

       python3 -m http.server 8000 --bind 127.0.0.1

   Always type `--bind 127.0.0.1`. It keeps the server on your own computer. Without it,
   everyone on the same Wi-Fi can read every file in the folder.
   If you see `OSError: ... Address already in use`, another program has port 8000. Try
   another port: type `8001` instead of `8000`, here and in every address below.
   This goes for every address on these pages, the links too. A link always points to
   8000: with another port do not click it, type the address with your own number.
2. **Leave that terminal as it is.** The server runs until you press Ctrl+C, and it is the
   one web server for everything on this page. For the next commands open a second
   terminal, in `my-project/` again.
3. Open <http://localhost:8000/vexo-open-kit/dashboard/?band=42> in Chrome or Edge on the
   same laptop, with your own number in place of `42`.
4. **Double-tap** the band. Its light blinks blue. It can now be found for 60 seconds.
5. Press **Connect**. The browser shows a list with one band in it: yours. The `?band=42`
   in the address makes the list show only `Vexo-HACK-42`, so you cannot pick a
   neighbour's band by mistake. (Without `?band=` in the address the dashboard first asks
   you to type your number.) Pick your band.
6. The band flashes white. The dashboard now shows live motion, taps, battery and the
   chip's temperature, and you can change the LED colour.

No band? Press **Try without a band** on the dashboard.

If the band is not in the list, see [When something does not work](#when-something-does-not-work).

## 2. Your first Python program (5 minutes)

1. **Use the second terminal**, the one in `my-project/` that is not running the web server.
2. **Install the Bluetooth package:**

       python3 -m pip install bleak

   On a Mac with the Python that comes with macOS (3.9), pip may print a long yellow
   warning about a "yanked version" of `pyobjc-core`. It looks like an error and is not
   one: the install worked if the last line starts with `Successfully installed`.
3. **Close the dashboard tab** if it is still connected. The band talks to one program at
   a time.
4. **Double-tap the band**, then run the LED example with your band number:

       python3 vexo-open-kit/examples/python/led.py 42

   The LED turns red, green, blue, then blinks white.

No band? Run `python3 vexo-open-kit/examples/python/led.py --sim`. Nothing needs to be installed for that.

Now open `vexo-open-kit/examples/python/led.py` in an editor. It is eleven lines. Change a
colour and run it again. The other examples are listed in
[examples/README.md](../examples/README.md), and every command is in
[the library API](api.md).

### Write your own program

This is the whole idea. Your own programs do not go into the kit. They go into
`my-project`, the folder that holds the kit:

    my-project/
      vexo-open-kit/       the kit; leave it as it is
      first.py             your program

Save these lines as `first.py` in `my-project/`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "vexo-open-kit" / "python"))   # where the library is
from vexo_open.sync import Band

band = Band.connect(42)          # your band number
print("Connected to", band.name)
band.set_led("#00ff00")          # green
band.wait(3)                     # keep it on for 3 seconds
band.disconnect()
```

Double-tap the band. In the same terminal (it is in `my-project/`), start the program:

    python3 first.py

It prints `Connected to Vexo-HACK-42`, the LED is green for 3 seconds, and the program ends.

No band? Change the connect line to `band = Band.connect(42, transport="sim")`. That is the
pretend band.

Why the first three lines? The library is not installed on your computer. It is a folder
inside the kit (`vexo-open-kit/python/vexo_open/`). Those lines tell Python where it is:
in the folder `vexo-open-kit`, next to your program. They work from whatever folder you
start the program in. Without them, or when the kit folder has another name or is in
another place, Python says `No module named 'vexo_open'`.

Do not save your own files inside the kit. (If you do, nothing breaks: the kit's own tests
ignore files that are not part of the kit. But you can then not replace the kit with a
newer one without losing them.)

### Reacting to the band: handlers

A handler is a function the library calls when something happens, for example a tap:

```python
def on_tap(event):
    led = band.get_led()         # asking the band inside a handler works
    print("tap! the light is", led.pattern)
    band.set_led("#ff00aa")

band.on("tap", on_tap)
band.configure(events=True)      # nothing arrives until you turn it on
band.wait(10)                    # keep the program alive so the handler can run
```

Put these lines in `first.py` in place of the line `band.wait(3)`. Tap the band and it
prints `tap! the light is solid` and turns pink. (The pretend band taps itself after 4
seconds.) Rules for handlers:

- Handlers run one at a time, in the order things happened, while your main program sits
  in `band.wait(...)`.
- A handler may call the band and use the answer (`band.get_led()`, `band.set_led(...)`,
  `band.storage.put(...)`, `band.wait(2)`).
- While one handler runs, the next events wait. Keep handlers short.
- An error in a handler is printed, and the program goes on.

## 3. Your first web page (1 minute)

The web server from step 1 is still running in `my-project/`. (If you stopped it, start it
again there: `python3 -m http.server 8000 --bind 127.0.0.1`.) It serves everything in
`my-project/`: the kit's pages now, your own pages later. Do not start a second one.

1. Open <http://localhost:8000/vexo-open-kit/examples/web/led.html> in Chrome or Edge on
   the same laptop.
2. Type your band number, double-tap the band, press **Connect** and pick your band.
   The LED blinks green. Pick another colour on the page.

No band? Tick **Pretend band** before you press Connect.

The page is `vexo-open-kit/examples/web/led.html` and its program is `led.js` next to it.
Have a look at them; they are short.

Stuck on "Connecting..."? Press **Cancel** and press Connect again. If the browser shows no
list of bands at all, check that Bluetooth is switched on.

Your own page goes in `my-project/`, like your Python program. Save it there as
`index.html`, and the same server shows it at <http://localhost:8000/index.html>. The
shortest page that works is in [AGENTS.md](../AGENTS.md#shortest-working-program-web-page).

`localhost` only works on the laptop that runs the server. To use your own page from a
phone, it has to be published on an `https://` address.

## Using the USB cable instead

The cable works when Bluetooth does not. No double-tap is needed.

1. Plug the band into the laptop with a USB data cable. (Some cables only charge.)
   On a Mac, the first time you plug a band in, macOS may ask **Allow accessory to
   connect?** Press **Allow**. Until you do, the laptop does not see the band.
2. Install the cable package:

       python3 -m pip install pyserial

3. Add `--usb` to any example:

       python3 vexo-open-kit/examples/python/led.py 42 --usb

In your own program, write `Band.connect(42, transport="serial")`.
On the dashboard, press **Connect by USB cable** (Chrome or Edge on a laptop). The browser
then lists every port on the computer, not only bands. Pick **Vexo Open band**: its port is
called `cu.usbmodem...` on a Mac, `COM` and a number on Windows (the name there can be
"USB Serial Device"), `ttyACM...` on Linux. Not "Bluetooth-Incoming-Port" and not
"debug-console": the page refuses those and tells you to pick again.

Over the cable the band has no signal strength to report. Everything else works the same.

## When something does not work

| What you see | What to do |
|---|---|
| The band is not in the list, or Python says "Could not see Vexo-HACK-42" | First check whether something else is connected to it: the dashboard tab, another program, a teammate's laptop. The band takes one Bluetooth connection at a time and cannot be found while it is connected, so a double-tap does not help until you close that. Then double-tap the band. It can be found for 60 seconds after a double-tap (the light blinks blue during that time). Keep it close to the laptop. |
| The band was in the list a moment ago and now it is gone | Someone else is connected to it, or you still are in another tab or program. Close the other tab or stop the other program, then double-tap. |
| The page still says "Connected" but the numbers stand still ("Arriving: 0"), or you see "Another program on the cable took this stream" (`stream-taken`) | A second program is using the same band on its other link: one on Bluetooth, one on the USB cable. They share the band, and a stream goes to the program that started it last. Close the other program, then press **Take it back** on the dashboard; in your own program call `band.take_back()` (JavaScript `band.takeBack()`). Details: [api.md](api.md#two-programs-on-one-band). |
| My light changed by itself, or a program said "then off" and the light stayed on | Another program is connected to the same band (one on Bluetooth, one on the USB cable). There is one light and they share it: the last one to set it wins, and the light only goes off by itself when the last program disconnects. Set your colour again, or close the other program. |
| `python3 -m pip install bleak` prints a long warning about a "yanked version" of `pyobjc-core` | Harmless. It is a note about one of the packages bleak uses on a Mac, seen with the Python 3.9 that comes with macOS. If the last line starts with `Successfully installed`, go on. |
| The fast-motion example says "Asked for 833 a second, measured 865.2 a second" | Not a fault. The motion sensor counts with its own clock, which runs a few percent fast or slow. The time of each reading (`time` and `t`) is measured by the band and is right. See [Fast motion](api.md#fast-motion). |
| The list shows a band that is not yours | Check the name in the list against the number on your band's bag. Pick only your own number. |
| "This is Vexo-HACK-17, not Vexo-HACK-42" | The band has a different number than the one you typed. Use the number printed on the band. |
| The browser says it has no Bluetooth | Use Chrome or Edge on a laptop or Android. iPhone, iPad, Firefox and Safari cannot connect. Check that Bluetooth is switched on. |
| macOS asks whether the browser or the terminal may use Bluetooth | Say yes. If you said no earlier, switch it on under System Settings, Privacy & Security, Bluetooth, then restart the browser or terminal. |
| Windows finds nothing | Switch Bluetooth on in Windows settings. Do not pair the band in Windows settings; just connect from the browser or from Python. |
| Android finds nothing | Switch on Bluetooth. Allow Chrome the "Nearby devices" permission when it asks. On older Android versions Chrome needs the Location permission instead, and Location must be switched on. |
| Linux finds nothing | Bluetooth on Linux needs BlueZ, the standard Bluetooth service. Check that it is installed and running, and that Bluetooth is switched on. In Chrome on Linux you may also have to switch on Web Bluetooth under `chrome://flags`. |
| The LED does nothing, or you get "denied" | The microphone is on. While audio or the loudness meter (`mic_level`) is on, the light is solid blue and the band refuses LED commands. Turn both off first (`configure(audio=False, mic_level=False)`), then set the LED. |
| `pip` is not found | Use `python3 -m pip install ...` (Windows: `python -m pip install ...`). |
| `pip` says "externally-managed-environment" | Make a private Python folder first: `python3 -m venv .venv`, then `source .venv/bin/activate` (Windows: `.venv\Scripts\activate`), then install again. |
| `No module named 'vexo_open'` | Python cannot find the library. Your program must be in `my-project/`, the kit folder inside it must be called exactly `vexo-open-kit`, and the three lines at the top of the program must be there. See [Write your own program](#write-your-own-program). |
| `can't open file '...vexo-open-kit/vexo-open-kit/...'` (the kit's name twice), or the web server answers "File not found" for an address with `/vexo-open-kit/` in it | Your terminal is inside the kit folder, one step too deep. Every command on these pages is typed in `my-project/`, the folder that holds the kit. Type `cd ..` and run the command again. To see where you are, type `pwd` (Windows: `cd`): the last part must be `my-project`. A web server that was started inside the kit: stop it with Ctrl+C, type `cd ..`, start it again. |
| "No band found on USB" | Try another cable (some only charge) and another USB port. If more than one band is plugged in, unplug the others. On a Mac, the first time a band is plugged in macOS may ask "Allow accessory to connect?". Press Allow, or switch it on under System Settings, Privacy & Security, "Allow accessories to connect". Until then the band has no port. The port is called `/dev/cu.usbmodem...` on a Mac, `COM` and a number on Windows, `/dev/ttyACM...` on Linux. |
| Over the USB cable the data stops, or you see "The band ended the USB session ..." (`session-ended`) | The library found the streams you switched on switched off on the band, for example because a second program used the band. The dashboard and a program with auto-reconnect connect again by themselves and set everything again. Without auto-reconnect, connect again and repeat every `configure`. Details: [api.md](api.md#over-the-usb-cable-when-the-band-ends-the-session). |
| "not-available" when you turn on `link` | The `link` data (signal strength) only exists over Bluetooth. On the USB cable, leave `link` out of `configure`. Nothing in that `configure` call was turned on, so call it again without `link`. |
| "not-available" and "needs firmware 1.1" | Event bands run firmware 1.1.0, so you should not see this on a real band: you see it when you connected with `protocol="1.0"` or made a pretend band with firmware 1.0. Motion faster than 100 samples per second, one sensor alone, a number of seconds for a stream, and setting the band's clock all need firmware 1.1. Use what 1.0 can do: `motion` 10, 25, 50 or 100, and `True` instead of a number. `band.info.firmware` shows which one your band has. See [Firmware 1.0 and 1.1](api.md#firmware-10-and-11). |
| "denied" and "too much for Bluetooth" | You asked for more motion samples per second than the link can carry. The message says the most it can do and the way out: one sensor alone, or the USB cable. Or ask for `motion="fastest"`. See [Fast motion](api.md#fast-motion). |
| Storage says "busy" | The band accepts 10 writes a second. The library waits and tries again by itself for up to 3 seconds. If you still see this, write less often. |
| The web page (or the dashboard) does nothing when opened by double-click | Open it through the web server. In `my-project/`: `python3 -m http.server 8000 --bind 127.0.0.1`, then `http://localhost:8000/...` (8000, or the port you started it with). After a second the page says so itself. |
| `OSError: ... Address already in use` when you start the web server | Another program has that port: perhaps your own server from step 1, still running in another terminal. Then use that one; one server is enough. If not, use another port: `python3 -m http.server 8001 --bind 127.0.0.1`, then `http://localhost:8001/...`. |
| The page stays on "Connecting..." | Press **Cancel**, then Connect again. If the browser shows no list of bands, switch Bluetooth on and reload the page. Without a pick the page gives up by itself after 2 minutes. |
| A band call inside a handler seems to do nothing | Look at the terminal: an error in a handler is printed there and the program goes on. See [Reacting to the band: handlers](#reacting-to-the-band-handlers). |
| The connection drops | Move closer. After a dropped connection the band can be found again for 2 minutes without a double-tap; after that, double-tap again. The dashboard reconnects by itself during those 2 minutes. Your own program does the same when you connect with `auto_reconnect=True` (see [the library API](api.md#when-the-link-drops-auto-reconnect)). |
| You have no band at all | Use the pretend band: `--sim` in Python, **Pretend band** on the example pages, **Try without a band** on the dashboard. |

## Good to know

- **Everything resets when you disconnect.** The LED goes off, all data streams stop and
  all settings go back to their defaults. Your program sets what it needs each time it
  connects.
- **A lost connection can come back by itself.** Connect with
  `Band.connect(42, auto_reconnect=True)` and the library tries again for 2 minutes, then
  sets your LED, settings and data streams again. It is off unless you ask for it.
- **Storage is shared.** What you store on the band stays there, and the next person who
  connects can read, change or erase it. Never store a secret.
- **Storage is slow on purpose.** The band accepts 10 writes a second. The library waits
  when you go faster, so 30 writes take a few seconds.
- **The signal reading can be empty.** `rssi` is `None` (`null` in JavaScript) when the
  band has no reading, and always over the cable. Check for that before you do sums with it.
- **Blue light means the mic is on.** While the band sends sound or measures loudness
  (`audio` or `mic_level`), its light is solid blue and cannot be changed. This is so people
  nearby can see it. It goes off when you turn both off or disconnect.
- **The motion sensor measures up to 2 g.** A harder push, shake or clap reads exactly 2.0
  until you widen the range: `band.set_motion(accel_range=8)` (2, 4, 8 or 16). See
  [Controls](api.md#controls).
- **Every reading has a real time.** Each event has `time` (in Python: seconds since 1970,
  like `time.time()`). See [Time](api.md#time).
- **Event bands run firmware 1.1.0.** So does the pretend band: fast motion, the clock
  and intervals work on both ([details](api.md#firmware-10-and-11)).
- **The temperature is the chip's, not your skin's.** It reads a few degrees above room
  temperature because the chip warms itself.
- **You only connect to your own band.** `Band.connect(42)` looks only for the band called
  `Vexo-HACK-42` and ignores every other band in the room.
- **Connect only to your own band.** If your band flashes white or its light changes and
  it was not you, someone else has connected to it: restart it and tell a helper.
- **To restart a band by hand,** press the small RESET button on the board once. Once
  only: two quick presses start the board's loader instead (a drive called XIAO-SENSE
  appears); copy nothing onto it and press RESET once more to get the band back. A band
  with no battery also restarts when you unplug the USB cable and plug it in again.
  After a restart the light blinks blue once a second for 60 seconds, or until you
  connect: the band is ready.
- **One Bluetooth connection at a time.** If your program is connected, the dashboard
  cannot be, and the other way round. A band that is connected cannot be found by anyone
  else, so "could not see the band" often means: something else is still connected.
- **A program on the USB cable and one on Bluetooth share the band.** One of each can be
  connected at once. They share its streams (a stream goes to the program that started it
  last), its settings and its light. See
  [Two programs on one band](api.md#two-programs-on-one-band).
- **You can see every byte.** [The Bluetooth contract, explained](bluetooth-contract.md)
  shows what travels between the band and your laptop, and how to connect with your own
  Bluetooth code instead of the kit's libraries.
- **Nothing is secret on the radio.** The band has no pairing and no password. Anyone
  nearby with the kit can connect to a band that has just been double-tapped.

## Speech and AI

Your team gets two keys from the organisers: one for Deepgram, which turns the band's
sound into text, and one for OpenAI, whose models make sense of that text. Setting the
keys, checking them with one command, and three examples (ask the band a question, voice
commands by meaning, meeting notes) are in one place:
["Audio to text to AI" in AGENTS.md](../AGENTS.md#audio-to-text-to-ai). Follow the steps
there; they are written for people as well as for coding agents.

## Stay safe

- **Keys stay out of your code.** Speech-to-text uses Deepgram, and your team gets a key
  for it. Never type the key into a program or into the code of a web page. Never commit
  it, never print or log it, never store it on the band. Put it in the environment
  variable `DEEPGRAM_API_KEY`, or type it into the dashboard's key field. The same goes
  for your OpenAI key (`OPENAI_API_KEY`), which is for Python only: never a web page. If a key got
  into a commit, a chat or onto a shared screen, tell an organiser: it is switched off and
  you get a new one. [Using Deepgram with your band](deepgram.md) has the full rules.
- **With speech-to-text on, the microphone's sound leaves your laptop** and goes to
  Deepgram, a company. Tell the people near the band first.
- **Text from a band is not yours.** Whatever you read from the band (storage values,
  names) was put there by the last person who connected. Treat it as plain text. Never run
  it as code or as a command. In a web page, show it with `textContent`, never with
  `innerHTML`.
- **The microphone records the people around you.** The band's solid blue light means the
  microphone is on. Tell people before you record, and stop if someone asks you to. Do not
  cover the light.

Next: [the examples](../examples/README.md) and [the library API](api.md).
