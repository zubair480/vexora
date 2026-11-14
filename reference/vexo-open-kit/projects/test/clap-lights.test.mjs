// Clap lights: first the clap detector on its own (plain Node, no browser), then the page
// in a real (headless) Chrome with the pretend band.
//   node --test projects/test/clap-lights.test.mjs
// Without Chrome the page test is skipped (set CHROME to the browser program to point at one).
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { skip, serveKit, startChrome, sleep } from './chrome.mjs';
import { ClapDetector, RISE_DB, DOUBLE_MS, SILENCE_DB } from '../clap-lights/detector.js';

/** Feed readings to a new detector and return what it heard: [[t, 'clap'], ...]. `loudAt(t)` is dB above silence. */
function listen(loudAt, { ms = 4000, every = 20, detector = new ClapDetector(), from = 1000 } = {}) {
  const heard = [];
  for (let t = from; t < from + ms; t += every) {
    const kind = detector.feed({ t, peakDb: SILENCE_DB - loudAt(t - from) });
    if (kind) heard.push([t - from, kind]);
  }
  return heard;
}
const room = (level, claps = [], long = 20) => (t) => (claps.some((at) => t >= at && t < at + long) ? 90 : level + 2 * Math.sin(t / 37));

test('detector: a quiet room and a steady loud room have no claps', () => {
  assert.deepEqual(listen(room(12)), []);
  assert.deepEqual(listen(room(70)), []);
});

test('detector: one clap is one clap, also when it fills two readings', () => {
  assert.deepEqual(listen(room(12, [1000])), [[1000, 'clap']]);
  assert.deepEqual(listen(room(12, [1000], 40)), [[1000, 'clap']]);
  assert.deepEqual(listen(room(12, [1000], 200), { every: 100 }), [[1000, 'clap']], 'firmware 1.0: 10 readings a second');
});

test('detector: two claps close together are a double clap, far apart they are two claps', () => {
  assert.deepEqual(listen(room(12, [1000, 1300])), [[1000, 'clap'], [1300, 'double']]);
  assert.deepEqual(listen(room(12, [1000, 1300]), { every: 100 }), [[1000, 'clap'], [1300, 'double']]);
  assert.deepEqual(listen(room(12, [1000, 1000 + DOUBLE_MS + 200])), [[1000, 'clap'], [1000 + DOUBLE_MS + 200, 'clap']]);
  // three in a row: a pair, then a new first clap
  assert.deepEqual(listen(room(12, [1000, 1300, 1600])).map(([, kind]) => kind), ['clap', 'double', 'clap']);
});

test('detector: it compares with the background, not with a fixed loudness', () => {
  // The room gets loud and stays loud: one jump, then the background has caught up.
  const stepUp = (t) => (t < 1000 ? 12 : 60);
  assert.deepEqual(listen(stepUp, { ms: 8000 }), [[1000, 'clap']]);
  // A clap in a loud room still counts; a bump smaller than RISE_DB does not.
  assert.deepEqual(listen(room(60, [3000])), [[3000, 'clap']]);
  assert.deepEqual(listen((t) => (t === 3000 ? 12 + RISE_DB - 3 : 12)), []);
});

test('detector: the numbers measured on a real band give the same answers', () => {
  // A quiet room is peakDb 43 (33 to 48) and jumps at most 10 dB above the background.
  const QUIET = SILENCE_DB - 43;
  const quiet = (t) => QUIET + (t % 3000 === 1000 ? 10 : 2 * Math.sin(t / 37));
  assert.deepEqual(listen(quiet, { ms: 60000 }), []);
  // Loud bursts are peakDb 10 to 17: 26 to 32 dB above the background. All count.
  for (const peakDb of [10, 13, 17]) {
    assert.deepEqual(listen((t) => (t === 2000 ? SILENCE_DB - peakDb : QUIET)), [[2000, 'clap']], `peakDb ${peakDb}`);
  }
  // A steady tone that swells by 18 dB over half a second does not count.
  const swell = (t) => QUIET + 18 * Math.min(1, Math.max(0, (t - 2000) / 500));
  assert.deepEqual(listen(swell, { ms: 8000 }), []);
  assert.deepEqual(listen(swell, { ms: 8000, every: 100 }), [], 'firmware 1.0: 10 readings a second');
  assert.ok(RISE_DB > 18 && RISE_DB < 26, 'between the swell and the softest burst');
});

test('detector: a jump in the band time or reset() starts again', () => {
  const detector = new ClapDetector();
  listen(room(12), { detector });
  assert.ok(Math.abs(detector.background - 12) < 3);
  detector.feed({ t: 60000, peakDb: SILENCE_DB - 70 });           // a minute later, in a loud room
  assert.equal(detector.background, 70, 'the old background is forgotten, and this is not a clap');
  detector.reset();
  assert.equal(detector.background, null);
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

test('clap-lights: claps switch the lamp, a double clap changes the colour, the link comes back', { skip, timeout: 60000 }, async () => {
  const page = await chrome.openPage(`${server.origin}/projects/clap-lights/?link=pretend&band=7`);
  const claps = async () => Number(await page.text('#claps'));
  // The colour in the middle of the lamp. Read from a copy, so the page's own canvas stays untouched.
  const lampPixel = () => page.run(`(() => {
    const copy = document.createElement('canvas');
    copy.getContext('2d').drawImage(document.querySelector('#picture'), 0, 0);
    return Array.from(copy.getContext('2d').getImageData(130, 130, 1, 1).data).join();
  })()`);
  assert.equal(await page.run('document.querySelector(".pretend-only").hidden'), false, 'the pretend controls show');
  assert.equal(await page.run('document.querySelector("#pretend-clap").disabled'), true, 'no clapping before Connect');
  await page.connectPretend();
  assert.match(await page.text('#message'), /Vexo-HACK-07\. Listening, 50 readings a second/, 'the pretend band has firmware 1.1');
  assert.equal(await page.text('#light-text'), 'blue: the microphone is on', 'the mic owns the band light');
  await page.untilText('#status', /^Lamp off$/);
  await page.untilText('#loud', /^\d+ %$/);
  await sleep(300);                                        // a few readings, so there is a background level

  await page.click('#pretend-clap');
  await page.untilText('#status', /^Lamp on$/);
  assert.equal(await claps(), 1);
  assert.equal(await lampPixel(), '255,201,60,255', 'the lamp is drawn in the first colour');
  await sleep(1000);                                       // longer than a double clap

  await page.click('#pretend-clap2');
  await page.untilText('#claps', /^3$/);
  assert.equal(await page.text('#theme'), 'Ocean', 'two quick claps change the colour');
  assert.equal(await page.text('#status'), 'Lamp on', 'and leave the lamp as it was');
  assert.equal(await lampPixel(), '60,200,255,255');
  await sleep(1000);

  await page.click('#pretend-clap');
  await page.untilText('#status', /^Lamp off$/);
  assert.equal(await claps(), 4);
  assert.equal(await page.text('#theme'), 'Ocean');

  // A louder room. The jump itself may count as a clap (it is a sudden rise). After that
  // the background has caught up: nothing more is counted, and a real clap still is.
  await page.click('#pretend-loud');
  await sleep(2500);
  const before = await claps();
  assert.ok(before === 4 || before === 5, `at most one clap for the jump, got ${before - 4}`);
  await sleep(1000);
  assert.equal(await claps(), before, 'steady noise is not clapping');
  await page.click('#pretend-clap');
  await page.untilText('#claps', new RegExp(`^${before + 1}$`));

  // Lose the link: the library gets it back and turns the loudness meter on again. (The
  // pretend band is back at once, so "Link lost..." shows too briefly to wait for.)
  await page.click('#pretend-quiet');
  await page.click('#pretend-drop');
  await page.untilText('#message', /Connected again after/, 10000);
  await page.untilText('#status', /^Lamp o(n|ff)$/);
  assert.equal(await page.text('#light-text'), 'blue: the microphone is on');
  await sleep(2500);                                       // the room went quiet meanwhile: let the background settle
  const after = await claps();
  await page.click('#pretend-clap');
  await page.untilText('#claps', new RegExp(`^${after + 1}$`));

  await page.click('#disconnect');
  await page.untilText('#message', /^Disconnected\.$/);
  assert.equal(await page.text('#status'), 'Not connected');
  assert.equal(await page.run('document.querySelector("#pretend-clap").disabled'), true);
  assert.deepEqual(page.problems, []);
  await page.close();
});
