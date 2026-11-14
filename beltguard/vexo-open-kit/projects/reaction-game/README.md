# Reaction game

**Easy. Web page (Chrome or Edge).**

Tap the band to start a round. Its light turns red: wait. After a random time it turns
green: tap as fast as you can. The page shows your time in milliseconds. Tap while it is
still red and the round is lost. The best time is saved on the band itself, so it is still
there tomorrow, on any laptop.

## Run it

In your project folder `my-project/`, the folder that holds the kit (on Windows type `python`;
port busy, or why `--bind`: see [the list](../README.md#run-one)). If this server runs
already, do not start a second one:

    python3 -m http.server 8000 --bind 127.0.0.1

- **Pretend band:** open <http://localhost:8000/vexo-open-kit/projects/reaction-game/?link=pretend>,
  press **Connect**, then use **Tap the pretend band**. Its light is drawn on the page.
  **Lose the link** shows what happens when the connection drops.
- **Real band:** open <http://localhost:8000/vexo-open-kit/projects/reaction-game/>, type your band
  number (the XX in Vexo-HACK-XX), double-tap the band, press **Connect** and pick it.
  For the USB cable choose **USB cable** in the list first.

## Read the code

- `game.js` (about 130 lines) is the whole game: four small functions, one per moment of
  a round (`ready`, `startRound`, `tooEarly`, `hit`), and `onTap`, which picks one. The
  numbers to tune are at its top (`WAIT_MS`, `SAME_TAP_MS`): guesses, not tried on a real band.
- `index.html` is the page. `../shared/page.js` connects and handles a dropped link. The
  Tap button calls the kit's pretend band: `pretend.tap('tap')`.

## Kit features it uses

Tap and double-tap events, the LED with patterns (`breathe`, `flash`, `rainbow`), storage
(`BEST_BOX` in `game.js`), the real time on every event (`tap.time`), auto-reconnect.

## Good to know

- The time is the tap's `time` minus the moment the band confirmed the green light. Over
  Bluetooth that is right to a few hundredths of a second, not to the millisecond.
- The pretend band on the page forgets its storage when you press Connect again or reload.
  A real band keeps it.
- Storage is shared: the next person who connects can read, change or erase the best
  time. So `readBest()` accepts a plain number only, and the page shows it as text.

## Try next

- Two players, two bands, two tabs: who taps first? (One tab connects to one band.)
- A rhythm game: blink the light at a steady beat and score how close each tap is.
- Best of five with an average, or a high-score list in five storage boxes.
- Use the band as a quiz buzzer: first tap after the question wins.
