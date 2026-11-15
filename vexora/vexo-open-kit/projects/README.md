# Example projects

Six small, complete projects to start from. Each one shows a different part of the band,
runs with the pretend band out of the box, and ends with ideas for making it your own.
They are starting points to copy, not the assignment: your own project can be anything.

> **42 is only our example. Wherever you see 42 in a command, an address or a name, type the
> number written on your band's bag.** Band 7 is `Vexo-HACK-07`, and its dashboard address
> ends in `?band=7`.

| Project | What it does | Runs in | Difficulty |
|---|---|---|---|
| [reaction-game](reaction-game/) | Red light, green light: tap the band as fast as you can. The best time is saved on the band | Web page | Easy |
| [band-leash](band-leash/) | Warns when the band (on your bag, your bike, your little brother) gets too far away or is left behind | Python | Easy |
| [clap-lights](clap-lights/) | The band's loudness meter drives a light show on the screen; clap to switch a lamp | Web page | Medium |
| [step-diary](step-diary/) | A step and activity diary that is kept on the band and survives a lost link and a restart | Python | Medium |
| [fall-alarm](fall-alarm/) | Notices a fall, counts down, and raises an alert unless a double tap says "I am fine" | Python | Medium |
| [gesture-trainer](gesture-trainer/) | Show it a motion three times and it recognises it afterwards, then does something | Web page | Harder |

**What is tested, and what is not.** The kit's features (the libraries, the dashboard,
the examples) are tested on a real band. These six projects are not, as whole projects:
they run on the pretend band, and their tests pass there. The numbers that decide how a
project behaves (how big a jump in loudness is a clap, which signal strength is "far", how
still is "still") started as guesses. A few were measured on one band on a desk:
the jump in loudness of a clap (clap-lights), the motion sensor at rest
(gesture-trainer) and the signal next to the laptop (band-leash). The others are still
guesses, for example which signal is "near" and "far", which needs a walk test. You will
need to tune them. Each project keeps them in one place, named in its README under "Read
the code", and the README says for each number whether it is measured or a guess.

## Run one

Type the commands in a terminal in your project folder `my-project/`, the folder that
holds the kit, as everywhere in the kit:

    my-project/
      vexo-open-kit/       the kit, with these projects in vexo-open-kit/projects/

On Windows, type `python` wherever these pages say `python3`. Nothing needs to be
installed for the pretend band.

- **Python projects:** `python3 vexo-open-kit/projects/fall-alarm/fall_alarm.py --sim`
  for the pretend band, `python3 vexo-open-kit/projects/fall-alarm/fall_alarm.py 42` for
  band 42 (your own number; Bluetooth needs `python3 -m pip install bleak`), add `--usb`
  for the cable (needs `python3 -m pip install pyserial`).
- **Web projects:** run `python3 -m http.server 8000 --bind 127.0.0.1` in `my-project/`
  (it is the same server as in the getting-started page: if it runs already, do not
  start a second one), then open
  <http://localhost:8000/vexo-open-kit/projects/reaction-game/?link=pretend> in Chrome or
  Edge. For a real band leave `?link=pretend` out and type your band number on the page.

Always type `--bind 127.0.0.1`. It keeps the server on your own computer; without it,
everyone on the same Wi-Fi can read every file in the folder. If the command says
`Address already in use`, another program has port 8000: use another number, for example
`python3 -m http.server 8001 --bind 127.0.0.1` and `http://localhost:8001/...`. A page
opened by double-click does not work, and says so.

Each folder has a README of one screen: what it does, how to run it, how to read the
code, and what to try next.

## Make one your own

Your own code does not go into the kit. Copy the project's folder and the folder
`shared` out of `vexo-open-kit/projects/` into `my-project/`, next to the kit:

    my-project/
      vexo-open-kit/       the kit; leave it as it is
      shared/              copied from vexo-open-kit/projects/shared
      fall-alarm/          copied from vexo-open-kit/projects/fall-alarm

Nothing in the files has to be changed: `shared/start.py` and `shared/kit.js` find the
library in `vexo-open-kit`, next to them. You stay in `my-project/`; only the paths get
shorter:

    python3 fall-alarm/fall_alarm.py --sim

The web server is the same one, started in `my-project/`. A copied page (here the folder
`reaction-game`) is at <http://localhost:8000/reaction-game/?link=pretend>.

## What is shared

| File | What it is |
|---|---|
| `shared/start.py` | For the Python projects: finds the kit's library, reads the band number, `--sim` and `--usb` from the command line, shows a problem as one sentence, `story()` to make the pretend band do something at a known moment, and `Inbox()`: what the handlers report waits there for the main loop, at most 1000 reports (the oldest go), so a faulty band that sends without a pause cannot fill the computer or stop `--seconds` and Ctrl+C |
| `shared/kit.js` | For the web pages: the one place that says where the kit's library is |
| `shared/page.js`, `shared/style.css` | For the web pages: the band number box, Connect, Cancel and Disconnect, the message line, a dropped link |

The band number is never written in a project. It comes from the command line (Python)
or from the box on the page (web), so your team's number is in one place. Both take `42`,
`07` or the full name `Vexo-HACK-42`. Band numbers are 1 to 999; a band that says 0 has
no number yet: ask an organiser.

## Three things every project does

1. **It turns on what it needs.** Nothing arrives from the band until `configure(...)`.
2. **It survives a dropped link.** Every project connects with auto-reconnect and says
   what it does while the link is away. Try **Lose the link** on a web page.
3. **It directs the pretend band.** A game or an alarm needs things to happen at a known
   moment, so each project tells the kit's pretend band what to do: `tap`, `play_motion`,
   `set_loudness`, `set_rssi`, `set_activity`, `drop_link`. Look for `if args.pretend:`
   (Python) or the buttons in the dashed box (web). All the calls are under "The pretend
   band" in [`docs/api.md`](../docs/api.md#the-pretend-band-making-things-happen). A real
   band needs none of it.

## Safety

- **Storage on a band is not private, and what is in it is not yours.** Whoever connected
  before can have written anything. The reaction game and the step diary accept only the
  plain numbers they wrote themselves and skip everything else; they never show it as
  HTML and never use it as a file name or as code. Do the same in your project.
- **The microphone hears everyone near the band.** Clap lights turns it on (the band's
  light is solid blue) and reads loudness numbers only: no sound is recorded or sent
  anywhere. Tell the people around you, and stop when someone asks.
- **What the projects write to your computer:** nothing, with a real band. With `--sim`,
  the Python pretend band keeps its storage in `.vexo-open/pretend-band-NUMBER.json` in
  your home folder, a file only you can read and at most a few hundred kB. The web pages
  keep everything in the page's memory and talk to no other computer.

## Tests

Every project has a test that runs it against the pretend band (the web pages in a
headless Chrome). These two commands are typed in the kit folder itself
(`my-project/vexo-open-kit`), not in `my-project/`:

    python3 -m unittest discover -s projects/test
    node --test projects/test/*.test.mjs

They need Python 3.9 or newer and Node 18 or newer. The page tests also need Node 22 and
Chrome or Edge, and are skipped without them (set `CHROME` to the browser program if it
is somewhere unusual).
