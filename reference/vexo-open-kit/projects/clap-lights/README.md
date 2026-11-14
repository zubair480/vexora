# Clap lights

**Medium. Web page (Chrome or Edge).**

A lamp on the screen that you switch with your hands. Clap once: the lamp goes on or off.
Clap twice quickly: it changes colour. A ring around the lamp and a row of bars follow how
loud the room is. The lamp is on the screen and not on the band, because the band's own
light is solid blue and locked while its microphone is on. **The microphone is on the whole
time: tell the people near you.** The page reads loudness numbers only; no sound is recorded.

## Run it

In your project folder `my-project/`, the folder that holds the kit (on Windows type `python`;
port busy, or why `--bind`: see [the list](../README.md#run-one)). If this server runs
already, do not start a second one:

    python3 -m http.server 8000 --bind 127.0.0.1

- **Pretend band:** open <http://localhost:8000/vexo-open-kit/projects/clap-lights/?link=pretend>,
  press **Connect**, then use **Clap** and **Clap twice**. **Quiet**, **Talking** and
  **Loud room** change the noise around the pretend band. **Lose the link** shows what
  happens when the connection drops.
- **Real band:** open <http://localhost:8000/vexo-open-kit/projects/clap-lights/>, type your band
  number (the XX in Vexo-HACK-XX), double-tap the band, press **Connect** and pick it.
  For the USB cable choose **USB cable** in the list first. Then clap near the band.

## Read the code

- `detector.js` (about 60 lines) decides what a clap is. It knows nothing about the page
  or the band. The numbers to tune (`RISE_DB`, `DOUBLE_MS`, ...) are at its top. `RISE_DB`
  (20) is measured on one band on a desk; the others are still guesses.
- `lights.js` (about 130 lines) is the page: `onLevel` handles one loudness reading, `draw`
  paints the lamp, the ring and the bars.
- `index.html` is the page. `../shared/page.js` connects and handles a dropped link. The
  Clap buttons call the kit's pretend band: `pretend.playLoudness([0.95])`.

## Kit features it uses

The loudness meter (`micLevel`, event `mic-level` with `level` and `peakDb`), the shortest
stream interval (`'fastest'`: 50 readings a second on firmware 1.1, 10 on 1.0, one line
for both), the band's time (`t`) on every reading, auto-reconnect.

## Good to know

- A clap is "suddenly much louder than the background", not "loud". So a door that slams
  or somebody who starts to shout counts as a clap too, and music that stays loud does not.
- A real band reads about 50 % in a quiet room, not 0: "Quiet" on the pretend band is
  that level. With 10 readings a second (firmware 1.0) two very fast claps can run into one.
- Measured: a quiet room jumps at most 10 dB above the background, loud claps 26 to 32 dB,
  a tone that swells in half a second 18 dB. Soft claps missed? Set `RISE_DB` to 15 or 16;
  swelling sounds then count too. Real hands across a room and a loud room: not yet tried.
- The band's light cannot show any of this: with the microphone on, `setLed` fails with `denied`.

## Try next

- Clap a rhythm to unlock something: long-short-short opens the "safe".
- A noise traffic light for the classroom: green, yellow, red by the background level.
- Three claps start a party mode where the colour changes with every loud beat.
- Count how many claps your team manages in ten seconds, with a high score.
