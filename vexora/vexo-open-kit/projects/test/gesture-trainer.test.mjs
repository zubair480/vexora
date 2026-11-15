// Gesture trainer: first the recogniser on its own (plain Node, no browser), then the page
// in a real (headless) Chrome with the pretend band.
//   node --test projects/test/gesture-trainer.test.mjs
// Without Chrome the page test is skipped (set CHROME to the browser program to point at one).
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { skip, serveKit, startChrome } from './chrome.mjs';
import { Segmenter, toShape, distance, match, POINTS, NOT_SURE, QUIET_MS, MAX_MS, MAX_SAMPLES } from '../gesture-trainer/recogniser.js';
import { MOTIONS } from '../gesture-trainer/motions.js';

const REST = { ax: 0, ay: 0, az: 1, gx: 0, gy: 0, gz: 0 };
/**
 * The samples a band would send: rest, the motion, rest. `rate` in samples/second,
 * `slow` stretches the motion in time, `shake` adds sensor noise (g; x100 for the gyro).
 */
function samples(shape, seconds, { rate = 50, slow = 1, shake = 0.004, from = 5000 } = {}) {
  const list = [];
  const noise = () => (Math.random() - 0.5) * shake;
  for (let i = 0; i < (seconds * slow + 1.2) * rate; i++) {
    const s = (i / rate - 0.4) / slow;                     // seconds into the motion
    const now = { ...REST, ...(s >= 0 && s < seconds ? shape(s) : {}) };
    const sample = { t: from + (i * 1000) / rate };
    for (const axis of Object.keys(REST)) sample[axis] = now[axis] + noise() * (axis[0] === 'g' ? 100 : 1);
    list.push(sample);
  }
  return list;
}
/** Run samples through a segmenter and return the gestures it cut out. */
function cut(list, segmenter = new Segmenter()) {
  return list.map((sample) => segmenter.feed(sample)).filter(Boolean);
}
const shapeOf = (name, options) => toShape(cut(samples(...MOTIONS[name], options))[0]);

test('segmenter: one motion is one gesture, cut where the hand moves', () => {
  for (const [name, [shape, seconds]] of Object.entries(MOTIONS)) {
    const found = cut(samples(shape, seconds));
    assert.equal(found.length, 1, name);
    const ms = found[0].at(-1).t - found[0][0].t;
    assert.ok(Math.abs(ms - seconds * 1000) <= 80, `${name}: ${ms} ms for a motion of ${seconds} s`);
  }
  assert.deepEqual(cut(samples(() => ({}), 1)), [], 'a hand at rest is no gesture');
});

test('segmenter: too short and too long are ignored, and it works again afterwards', () => {
  const segmenter = new Segmenter();
  const spin = () => ({ gz: 300 });
  assert.equal(cut(samples(spin, 0.1), segmenter).length, 0, 'a bump of 0.1 s');
  assert.equal(cut(samples(spin, 4, { from: 10000 }), segmenter).length, 0, 'four seconds of turning');
  assert.equal(cut(samples(spin, 0.6, { from: 20000 }), segmenter).length, 1);
});

test('segmenter: a band whose time stands still cannot make the list grow', () => {
  // A faulty band: moving samples, all with the same time (period 0). By the band's time
  // the gesture never gets long, so the count has to stop it.
  const segmenter = new Segmenter();
  for (let i = 0; i < 50000; i++) assert.equal(segmenter.feed({ ...REST, gz: 300, t: 5000 }), null);
  assert.ok(segmenter.samples.length <= MAX_SAMPLES, `${segmenter.samples.length} samples kept`);
  // it ends when the hand is quiet, is no gesture, and the next real gesture is found
  assert.equal(segmenter.feed({ ...REST, t: 5000 + QUIET_MS }), null);
  assert.equal(segmenter.samples.length, 0);
  assert.equal(cut(samples(...MOTIONS.flick, { from: 20000 }), segmenter).length, 1);
});

test('segmenter: a gesture that takes too long on the computer\'s clock is dropped, whatever the band\'s time says', () => {
  // The band's time crawls (1 ms a sample) while the computer's clock runs (100 ms a sample).
  const segmenter = new Segmenter();
  for (let i = 0; i < 200; i++) segmenter.feed({ ...REST, gz: 300, t: 5000 + i }, 1000000 + i * 100);
  assert.ok(segmenter.samples.length <= (2 * (MAX_MS + QUIET_MS)) / 100 + 2, `${segmenter.samples.length} samples kept`);
  assert.equal(segmenter.feed({ ...REST, t: 5200 + QUIET_MS }, 1030000), null, 'no gesture comes out of it');
  assert.equal(segmenter.samples.length, 0);
});

test('segmenter: a sample without a usable time is skipped and nothing fails', () => {
  const segmenter = new Segmenter();
  for (const t of [NaN, Infinity, -Infinity, undefined, null, 'soon']) assert.equal(segmenter.feed({ ...REST, gz: 300, t }), null);
  assert.equal(segmenter.samples.length, 0);
  assert.equal(cut(samples(...MOTIONS.flick), segmenter).length, 1);
});

test('flood: three seconds of samples as fast as the computer can make them', () => {
  // Same time, time 0 apart, time going backwards, a time far in the future: in turns.
  const segmenter = new Segmenter();
  const end = Date.now() + 3000;
  let fed = 0, most = 0;
  while (Date.now() < end) {
    for (let i = 0; i < 1000; i++, fed++) {
      const t = fed % 5000 === 0 ? 9e15 : fed % 777 === 0 ? 4000 : 5000 + Math.floor(fed / 100000);
      segmenter.feed({ ...REST, gz: fed % 2 ? 300 : -300, ax: (fed % 7) / 3, t });
    }
    most = Math.max(most, segmenter.samples.length);
  }
  assert.ok(fed > 100000, `only ${fed} samples in three seconds`);
  assert.ok(most <= MAX_SAMPLES, `${most} samples kept at one moment`);
  assert.equal(cut(samples(...MOTIONS.flick, { from: 20000 }), new Segmenter()).length, 1);
});

test('segmenter: two motions with a rest are two gestures; a hole in the samples never glues two halves together', () => {
  const [shape, seconds] = MOTIONS.flick;
  const twice = [...samples(shape, seconds), ...samples(shape, seconds, { from: 7000 })];
  assert.equal(cut(twice).length, 2);
  // the link was down in the middle of a circle: the two halves are not a gesture
  const holed = samples(...MOTIONS.circle).filter((sample) => sample.t < 5700 || sample.t > 5700 + QUIET_MS + 100);
  // (what is left after the hole may count as a short gesture of its own; it will not match a circle)
  assert.ok(cut(holed).every((gesture) => gesture.at(-1).t - gesture[0].t < 400), 'nothing as long as a circle comes out');
  const segmenter = new Segmenter();
  samples(...MOTIONS.circle).slice(0, 40).forEach((sample) => segmenter.feed(sample));
  segmenter.reset();
  assert.equal(segmenter.samples.length, 0);
});

test('shape: always POINTS x 6 numbers; speed, sample rate and packets do not matter much', () => {
  for (const name of Object.keys(MOTIONS)) {
    const usual = shapeOf(name);
    assert.equal(usual.length, POINTS * 6);
    assert.ok(distance(usual, shapeOf(name)) < 0.1, `${name}: the same motion twice`);
    assert.ok(distance(usual, shapeOf(name, { slow: 1.25 })) < NOT_SURE, `${name}: done slowly`);
    assert.ok(distance(usual, shapeOf(name, { rate: 100 })) < NOT_SURE, `${name}: 100 samples a second`);
  }
});

test('match: the nearest example wins, a motion nobody recorded is "not sure"', () => {
  const examples = ['circle', 'shake', 'flick'].flatMap((name, label) => [1, 1.1, 0.9].map((slow) => ({ label, shape: shapeOf(name, { slow }) })));
  assert.equal(match(shapeOf('circle'), examples).label, 0);
  assert.equal(match(shapeOf('shake', { slow: 1.05 }), examples).label, 1);
  assert.equal(match(shapeOf('flick'), examples).label, 2);
  const punch = match(shapeOf('punch'), examples);
  assert.equal(punch.label, null);
  assert.ok(punch.distance > NOT_SURE, 'the distance is still reported');
  assert.deepEqual(match(shapeOf('circle'), []), { label: null, distance: Infinity }, 'no examples yet');
});

let server, chrome;
before(async () => {
  if (skip) return;
  server = await serveKit();
  chrome = await startChrome();
});
after(async () => {
  await chrome?.close();
  server?.close();
});

test('gesture-trainer: record two gestures, recognise them, reject a third, the link comes back', { skip, timeout: 90000 }, async () => {
  const page = await chrome.openPage(`${server.origin}/projects/gesture-trainer/?link=pretend&band=7`);
  const disabled = (selector) => page.run(`document.querySelector(${JSON.stringify(selector)}).disabled`);
  const lightColour = () => page.run('document.querySelector("#light").style.backgroundColor');
  /** Make the pretend hand do a motion. The buttons are off while it is still moving. */
  const move = async (name) => {
    await page.until(`the pretend hand to be free for ${name}`, `!document.querySelector("#pretend-${name}").disabled`);
    await page.click(`#pretend-${name}`);
  };
  assert.equal(await page.run('document.querySelector(".pretend-only").hidden'), false, 'the pretend controls show');
  assert.equal(await disabled('#record-0'), true, 'no recording before Connect');
  await page.connectPretend();
  assert.match(await page.text('#message'), /Vexo-HACK-07/, 'the band number comes from the address');
  await page.untilText('#status', /^Press Record$/);

  // Record "Circle": three examples, then recording stops by itself.
  await page.click('#record-0');
  await page.untilText('#status', /^Show me "Circle"$/);
  await page.untilText('#light-text', /^breathing$/);
  assert.equal(await disabled('#record-1'), true, 'one recording at a time');
  for (const count of [1, 2, 3]) {
    await move('circle');
    await page.untilText('#count-0', new RegExp(`^${count} of 3$`));
  }
  await page.untilText('#status', /^Learned "Circle"$/);
  await page.untilText('#light-text', /^off$/);

  // Record the second gesture under a name of our own.
  await page.run('document.querySelector("#name-1").value = "Wobble"');
  await page.click('#record-1');
  await page.untilText('#status', /^Show me "Wobble"$/);
  for (const count of [1, 2, 3]) {
    await move('shake');
    await page.untilText('#count-1', new RegExp(`^${count} of 3$`));
  }
  await page.untilText('#status', /^Learned "Wobble"$/);

  // Recognise: the name is shown and the band lights up in the colour of that gesture.
  await move('circle');
  await page.untilText('#status', /^Circle$/);
  await page.until('the red light', 'document.querySelector("#light").style.backgroundColor === "rgb(255, 59, 48)"');
  assert.match(await page.text('#detail'), /^1\.0 seconds\. Distance to the nearest example: 0\.0\d /);
  await move('shake');
  await page.untilText('#status', /^Wobble$/);
  await page.until('the green light', 'document.querySelector("#light").style.backgroundColor === "rgb(52, 199, 89)"');
  // Motions nobody recorded.
  await move('flick');
  await page.untilText('#status', /^Not sure$/);
  await page.untilText('#light-text', /^off$/);
  await move('circle');
  await page.untilText('#status', /^Circle$/);
  await move('punch');
  await page.untilText('#status', /^Not sure$/);
  assert.equal(await page.text('#count-0'), '3 of 3', 'recognising does not add examples');
  const drawn = await page.run(`(() => {
    const copy = document.createElement('canvas');
    copy.width = 720; copy.height = 220;
    copy.getContext('2d').drawImage(document.querySelector('#picture'), 0, 0);
    return copy.getContext('2d').getImageData(0, 0, 720, 220).data.some((part) => part > 0);
  })()`);
  assert.equal(drawn, true, 'the last gesture is drawn');

  // Lose the link in the middle of a recording: the recording stops, the examples stay.
  await page.click('#record-2');
  await move('flick');
  await page.untilText('#count-2', /^1 of 3$/);
  await page.click('#pretend-drop');
  await page.untilText('#message', /Connected again after/, 10000);
  await page.untilText('#status', /^Make a gesture$/);
  assert.equal(await page.text('#record-2'), 'Record', 'not recording any more');
  assert.equal(await disabled('#record-0'), false);
  await page.untilText('#light-text', /^off$/);
  await move('shake');
  await page.untilText('#status', /^Wobble$/);
  assert.equal(await lightColour(), 'rgb(52, 199, 89)');
  await move('flick');
  await page.untilText('#status', /^Flick$/);            // one example is enough to be found
  assert.equal(await page.text('#count-2'), '1 of 3');

  await page.click('#disconnect');
  await page.untilText('#message', /^Disconnected\.$/);
  assert.equal(await page.text('#status'), 'Not connected');
  assert.equal(await disabled('#record-0'), true);
  assert.equal(await disabled('#pretend-circle'), true);
  assert.deepEqual(page.problems, []);
  await page.close();
});
