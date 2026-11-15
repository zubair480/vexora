// Opens the reaction game in a real (headless) Chrome with the pretend band and plays it.
//   node --test projects/test/*.test.mjs
// Without Chrome these tests are skipped (set CHROME to the browser program to point at one).
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { skip, serveKit, startChrome, sleep } from './chrome.mjs';

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
const open = (project, query = '?link=pretend&band=7') => chrome.openPage(`${server.origin}/projects/${project}/${query}`);

test('reaction-game: a whole round, a too-early tap, the best time is kept on the band', { skip, timeout: 60000 }, async () => {
  const page = await open('reaction-game');
  assert.equal(await page.run('document.querySelector(".pretend-only").hidden'), false, 'the pretend controls show');
  await page.connectPretend();
  assert.match(await page.text('#message'), /Vexo-HACK-07/, 'the band number comes from the address');
  await page.untilText('#status', /Tap the band to start/);
  assert.equal(await page.text('#light-text'), 'breathing');

  await page.click('#pretend-tap');                       // start a round
  await page.untilText('#status', /Wait for green/);
  await page.until('the red light', 'document.querySelector("#light").style.backgroundColor === "rgb(255, 0, 0)"');
  await page.untilText('#status', /TAP!/, 6000);
  assert.equal(await page.run('document.querySelector("#light").style.backgroundColor'), 'rgb(0, 255, 0)');
  await sleep(120);
  await page.click('#pretend-tap');
  await page.untilText('#status', /^\d+ ms - new best!$/);
  const ms = Number((await page.text('#rounds li')).replace(' ms', ''));
  assert.ok(ms >= 100 && ms < 1500, `a believable reaction time, got ${ms}`);
  assert.equal(await page.text('#best'), `${ms} ms`);

  await page.untilText('#status', /Tap the band to start/, 5000);
  await page.click('#pretend-tap');                       // next round: tap while it is still red
  await page.untilText('#status', /Wait for green/);
  await sleep(450);
  await page.click('#pretend-tap');
  await page.untilText('#status', /Too early!/);

  // Lose the link: the library gets it back, the best time is still there, the game goes on.
  await page.untilText('#status', /Tap the band to start/, 5000);
  await page.click('#pretend-drop');
  await page.untilText('#message', /Connected again after/, 10000);
  await page.untilText('#status', /Tap the band to start/);
  await page.click('#pretend-tap');
  await page.untilText('#status', /TAP!/, 6000);
  await sleep(400);
  await page.click('#pretend-tap');
  await page.untilText('#status', /^\d+ ms$/);
  assert.equal(await page.text('#best'), `${ms} ms`, 'a slower round does not replace the best time');
  await page.untilText('#status', /Tap the band to start/, 5000);
  await page.click('#forget');
  await page.untilText('#best', /^-$/);
  await page.click('#disconnect');
  await page.untilText('#message', /^Disconnected\.$/);
  assert.equal(await page.text('#status'), 'Not connected');
  assert.deepEqual(page.problems, []);
  await page.close();
});

test('a real band needs its number: without one the page asks for it', { skip, timeout: 30000 }, async () => {
  const page = await open('reaction-game', '');
  await page.click('#connect');
  assert.match(await page.text('#message'), /Type your band number first/);
  assert.deepEqual(page.problems, []);
  await page.close();
});
