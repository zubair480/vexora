# Fall alarm

**Medium. Python.**

The band notices a fall. If the wearer stays still afterwards, the light flashes red and an
alarm counts down in the terminal. A double tap on the band says "I am fine" and cancels
it. With no double tap, `send_alert()` runs: here it prints a line, in your project it
could send a message.

## Run it

In your project folder `my-project/`, the folder that holds the kit (on Windows type `python`
wherever this page says `python3`):

- **Pretend band:** `python3 vexo-open-kit/projects/fall-alarm/fall_alarm.py --sim`
  The pretend band falls three times: once cancelled by a double tap, once the wearer gets
  up, once nobody reacts. It takes about 40 seconds and then stops; add `--check 1 --countdown 3` for a
  faster run.
- **Real band:** `python3 vexo-open-kit/projects/fall-alarm/fall_alarm.py 42` with your
  own number in place of 42 (needs `python3 -m pip install bleak`; double-tap the band first). Add
  `--usb` for the cable. Drop the band onto a pillow from half a metre, then leave it.

Options: `--check 5` seconds of stillness before the alarm, `--countdown 10` seconds to
cancel, `--seconds 60` to stop by itself.

## Read the code

`fall_alarm.py` (about 130 lines). The handlers only put what happened into an inbox. The
loop at the bottom is a small state machine: `watching` -> `checking` -> `alarm` ->
`alerted`. The numbers to tune are at the top (`CHECK_SECONDS`, `COUNTDOWN_SECONDS`,
`LANDING_SECONDS`): guesses, not tried on a real fall. The `if args.pretend:` block is the
story the pretend band plays, with the kit's `sim.tap("free-fall")` and `sim.set_activity("still")`.

## Kit features it uses

`free-fall`, `motion-start` and `double-tap` events, activity (`still` / `walk` / `run`),
LED patterns, the real time on every event, auto-reconnect (the alarm keeps counting while
the link is down).

## Good to know

- This is a hackathon demo, not a medical device. Nobody has tried it on a real band or
  a real fall. Do not rely on it for anyone's safety.
- "Moving" means a `motion-start` event or an activity reading that is not `still`.

## Try next

- Make `send_alert()` real: an email, a chat message, a sound on the laptop.
- Save the time of every fall in the band's storage and show the list on the next start.
- Use the microphone's loudness meter: a shout during the countdown also cancels.
- Read the motion data around the fall and measure how hard the landing was.
