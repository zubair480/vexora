# Band leash

**Easy. Python.**

Never leave your bag behind. Put the band on your bag and keep the program running on your
laptop (or wear the band and leave the laptop). It watches the signal strength: green
light when near, blinking yellow when getting far, flashing red when far. When the link is
lost the terminal shouts LEFT BEHIND, and when the band is found again it says how long it
was away.

## Run it

In your project folder `my-project/`, the folder that holds the kit (on Windows type `python`
wherever this page says `python3`):

- **Pretend band:** `python3 vexo-open-kit/projects/band-leash/leash.py --sim`
  The pretend band is carried away step by step, goes out of range for 4 seconds and
  comes back. It takes about 20 seconds.
- **Real band:** `python3 vexo-open-kit/projects/band-leash/leash.py 42` with your own
  number in place of 42 (needs `python3 -m pip install bleak`; double-tap the band first). Then walk away
  with the band. Bluetooth only: with `--usb` it explains why and stops.

Options: `--near -60` and `--far -80` are the borders of the zones in dBm, `--seconds 60`
to stop by itself.

## Read the code

`leash.py` (about 130 lines). The numbers to tune are at the top. `NEAR_DBM`, `FAR_DBM`,
`SMOOTH_SECONDS` and `SLACK_DBM` are guesses: nobody has walked away with a real band yet.
`METER_FULL_DBM` (-20) is measured on one band on a desk. `READINGS_MOST` (100) is
the most readings it keeps, so a faulty band cannot fill the computer. `zone_for()` picks
the zone and keeps it from flickering, `meter()` draws the line of text. The handlers only
put what happened into an inbox; the loop at the bottom smooths the signal and reacts. The
`if args.pretend:` block is the story, told with the kit's `sim.set_rssi(-75)` and `sim.drop_link(4)`.

## Kit features it uses

`link` (signal strength), LED patterns, the shortest stream interval (`link="fastest"`:
ten readings a second on firmware 1.1, one on 1.0, the same line for both), the real time
on every reading, auto-reconnect (`reconnecting` is "left behind", `reconnected` the welcome back).

## Good to know

- Signal strength is a rough hint, not a ruler. A body, a wall or a bag between band and
  laptop changes it a lot. Next to the laptop a real band reads about -18 to -19 dBm. Where
  -60 and -80 are in a room is not measured yet: walk away, watch the meter, set `--near` and `--far`.
- A zone changes only when the signal is 3 dBm past the border (`SLACK_DBM`), so it does
  not flicker. A lost link also happens when the band is off or its battery is empty.
- The library looks for the band for 2 minutes. After that the program stops; double-tap
  the band and start again.
- When the link drops the band's light goes off: the band resets whenever a link closes.

## Try next

- Play a sound on the laptop when the band is left behind, and another when it is back.
- Guard the laptop instead: wear the band and lock the screen when you walk away.
- Draw the signal over time with a text graph, and find where your room's borders are.
- Count how often and how long the band was away, and save it in the band's storage.
