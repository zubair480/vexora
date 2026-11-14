# Gesture trainer

**Harder. Web page (Chrome or Edge).**

Teach the band your own gestures. Give a gesture a name and a colour, press **Record** and
do it three times. Do the same for one or two more. From then on the page names every
gesture you make, and the band lights up in that gesture's colour. A motion it has never
seen gets "Not sure". The last motion it looked at is drawn as six lines, so you see what
the recogniser sees.

## Run it

In your project folder `my-project/`, the folder that holds the kit (on Windows type `python`; port busy, or
why `--bind`: see [the list](../README.md#run-one)). If this server runs already, do not start a second one:

    python3 -m http.server 8000 --bind 127.0.0.1

- **Pretend band:** open <http://localhost:8000/vexo-open-kit/projects/gesture-trainer/?link=pretend>,
  press **Connect**. Press **Record** next to "Circle", then the **Circle** button in the
  dashed box three times. Do the same with "Shake". Now press **Circle**, **Shake** and
  **Punch** and see what the page says. **Lose the link** shows what happens when the
  connection drops.
- **Real band:** open <http://localhost:8000/vexo-open-kit/projects/gesture-trainer/>, type your band
  number (the XX in Vexo-HACK-XX), double-tap the band, press **Connect** and pick it.
  For the USB cable choose **USB cable** in the list first. Hold still, make the gesture,
  hold still again: the stillness is how the page knows where a gesture starts and ends.

## Read the code

- `recogniser.js` (about 110 lines) is the clever part and knows nothing about the page or
  the band: `Segmenter` cuts gestures out of the motion, `toShape` turns one into 24
  points, `match` finds the nearest recorded example. The numbers to tune are at its top (`MAX_SAMPLES` = 2000
  is the most samples one gesture keeps): guesses that fit the pretend hand, not tried on a real wrist. Measured
  on one band on a desk: at rest it reads 2.2 to 2.4 degrees/second and steps of at most 0.005 g,
  far below `START_GYRO` (40) and `START_ACCEL` (0.08). A still hand and real gestures: not yet measured.
- `trainer.js` (about 150 lines) is the page: `record`, `onGesture` and `draw`.
- `motions.js` holds the pretend hand's motions, played with the kit's `pretend.playMotion(...)`. `../shared/page.js` connects.

## Kit features it uses

Motion samples at 50 a second (acceleration and turning speed, six numbers), the band's time (`t`) on every
sample, the LED (`setLed`, `ledOff`, the `breathe` pattern while it records), auto-reconnect.

## Good to know

- The examples live in the page's memory. Reload the page and they are gone. A dropped
  link or Disconnect keeps them.
- The page shows the distance to the nearest example. Too many "Not sure"? Raise
  `NOT_SURE` in `recogniser.js`. Wrong guesses? Lower it, or record clearer gestures.
- How you hold your arm is part of the gesture (gravity shows in the acceleration), so
  record and use a gesture in the same pose.

## Try next

- Keep the examples on the band: a shape is 144 numbers, and a storage box holds 232
  bytes, so round each number to one byte and use one box per example.
- A spell-casting game: the page asks for a gesture and you have two seconds to cast it.
- Control a slide show or a music player: flick for next, circle for play and pause.
- Show the second-nearest gesture too, and say "not sure" when the two are almost as close.
