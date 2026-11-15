// Gesture recogniser by template matching. No page and no band in here: you feed it
// motion samples, it cuts out the gestures and compares them with the examples you gave
// it. That makes it easy to test (see ../test/gesture-trainer.test.mjs).
//
// Three steps:
//   1. Segmenter   cuts the stream of samples into gestures: from "the hand starts to
//                  move" until "the hand has been quiet for a moment".
//   2. toShape     squeezes a gesture into a fixed list of numbers, so a slow and a fast
//                  version of the same gesture look alike.
//   3. match       finds the recorded example with the most similar shape.

// The numbers to tune. They are guesses that work on the pretend band's motions: nobody
// has tried them on a real wrist yet. What is measured (one band lying on a desk)
// is how quiet a band at rest is: it turns at about 2.2 to 2.4 degrees/second
// and its acceleration changes by at most 0.005 g from one sample to the next. Both start
// numbers are far above that, so a band at rest never starts a gesture. Still to measure:
// a still hand (it trembles more than a desk) and real gestures.
export const START_GYRO = 40;     // degrees/second: turning faster than this counts as moving (at rest: about 2.3)
export const START_ACCEL = 0.08;  // g: so does a change in acceleration this big from one sample to the next (at rest: 0.005 at most)
export const QUIET_MS = 300;      // a gesture ends when the hand was quiet for this long
export const MIN_MS = 200;        // shorter than this is a bump, not a gesture
export const MAX_MS = 3000;       // longer than this is just moving around
export const MAX_SAMPLES = 2000;  // the most samples one gesture may have (50 a second for 3 seconds are 150)
export const POINTS = 24;         // every gesture becomes this many points in time
export const GYRO_SCALE = 250;    // degrees/second that count as much as...
export const ACCEL_SCALE = 1;     // ...this many g. Without it the big gyro numbers would decide everything.
export const NOT_SURE = 0.5;      // nearest example further away than this: "not sure" (the page shows the distance)
const AXES = ['ax', 'ay', 'az', 'gx', 'gy', 'gz'];

/** Is the hand moving at this sample? `before` is the sample before it (or null). */
function moving(sample, before) {
  if (Math.hypot(sample.gx, sample.gy, sample.gz) > START_GYRO) return true;
  return before !== null
    && Math.hypot(sample.ax - before.ax, sample.ay - before.ay, sample.az - before.az) > START_ACCEL;
}

export class Segmenter {
  constructor() {
    this.reset();
  }

  /** Throw away a gesture in progress. Call it when the link drops. */
  reset() {
    this.samples = [];            // the gesture so far; empty while the hand is quiet
    this.before = null;           // the sample before this one
    this.lastMove = 0;            // band time (ms) of the last sample that was moving
    this.began = 0;               // the computer's time (ms) when this gesture began
    this.tooLong = false;         // true once it has too many samples or takes too long: no gesture any more
  }

  /**
   * Feed one motion sample ({ t, ax, ay, az, gx, gy, gz }). Returns the samples of a
   * finished gesture, or null. `t` is the band's time in ms: we measure with it, never by
   * counting samples, because samples can arrive in packets or get lost. `now` is the
   * computer's time in ms: the band's time alone cannot be trusted to stop the list from
   * growing (a faulty band can send the same `t` again and again).
   */
  feed(sample, now = Date.now()) {
    if (!Number.isFinite(sample.t)) return null;                       // no time, no place in a gesture
    // A jump in time (samples lost, link was down): what we have is not one gesture.
    if (this.before && (sample.t < this.before.t || sample.t - this.before.t > QUIET_MS)) this.reset();
    const isMoving = moving(sample, this.before);
    this.before = sample;
    if (isMoving) this.lastMove = sample.t;
    if (this.samples.length === 0 && !isMoving) return null;           // still quiet
    // Keep the sample, unless the hand has been moving for too long already: that is no
    // gesture any more (see below), and the list must not grow without end. "Too long" is
    // measured three ways: by the band's time, by the count, and by the computer's clock.
    if (this.samples.length === 0) {
      this.began = now;
      this.tooLong = false;
      this.samples.push(sample);
    } else if (this.samples.length >= MAX_SAMPLES || now - this.began > 2 * (MAX_MS + QUIET_MS)) {
      this.tooLong = true;
    } else if (sample.t - this.samples[0].t <= MAX_MS + QUIET_MS) {
      this.samples.push(sample);
    }
    if (sample.t - this.lastMove < QUIET_MS) return null;              // not over yet

    // Quiet for long enough: the gesture is everything up to the last moving sample.
    const gesture = this.samples.filter((one) => one.t <= this.lastMove);
    const tooLong = this.tooLong;
    this.samples = [];
    this.tooLong = false;
    if (tooLong || gesture.length === 0) return null;
    const ms = this.lastMove - gesture[0].t;
    return ms >= MIN_MS && ms <= MAX_MS ? gesture : null;
  }
}

/**
 * Turn a gesture into POINTS x 6 numbers. The points are spread evenly over the time of
 * the gesture, and each value is read off between the two samples next to that time.
 */
export function toShape(gesture) {
  const start = gesture[0].t;
  const length = gesture[gesture.length - 1].t - start;
  const shape = [];
  let i = 0;
  for (let p = 0; p < POINTS; p++) {
    const t = start + (length * p) / (POINTS - 1);
    while (i < gesture.length - 2 && gesture[i + 1].t < t) i++;
    const a = gesture[i], b = gesture[i + 1] ?? a;
    const part = b.t > a.t ? Math.min(1, Math.max(0, (t - a.t) / (b.t - a.t))) : 0;
    for (const axis of AXES) {
      const value = a[axis] + (b[axis] - a[axis]) * part;
      shape.push(value / (axis[0] === 'g' ? GYRO_SCALE : ACCEL_SCALE));
    }
  }
  return shape;
}

/** How different two shapes are: the mean distance between their points. 0 = the same. */
export function distance(one, other) {
  let sum = 0;
  for (let p = 0; p < one.length; p += AXES.length) {
    let square = 0;
    for (let k = p; k < p + AXES.length; k++) square += (one[k] - other[k]) ** 2;
    sum += Math.sqrt(square);
  }
  return sum / (one.length / AXES.length);
}

/**
 * Find the nearest example. `examples` is a list of { label, shape }. Returns
 * { label, distance }; label is null when nothing is near enough (or there are no examples).
 */
export function match(shape, examples, limit = NOT_SURE) {
  let best = { label: null, distance: Infinity };
  for (const example of examples) {
    const d = distance(shape, example.shape);
    if (d < best.distance) best = { label: example.label, distance: d };
  }
  return best.distance <= limit ? best : { label: null, distance: best.distance };
}
