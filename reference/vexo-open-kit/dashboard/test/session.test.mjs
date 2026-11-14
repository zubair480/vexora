// The dashboard and a USB session that the band ended (firmware 1.1.3 does that to a page
// that took no data for 5 seconds). The page must not go on showing "Connected" with dead
// data: it says why, and the library connects again and puts the streams back.
//
// The page test runs in headless Chrome. navigator.serial is replaced by a fake port with
// the pretend band behind it (js/test/fake-serial-port.js): no real port is opened.
// It is skipped when Chrome is not found. Not tested against a real band.
import test from 'node:test';
import assert from 'node:assert/strict';
import { disconnectMessage, reconnectingMessage, reconnectedMessage } from '../logic.js';
import { serveKit, startChrome, skip, sleep } from '../../projects/test/chrome.mjs';

test('the words for a session end: why it happened, and what happens next', () => {
  const going = reconnectingMessage(120, 'session-ended');
  assert.equal(going.kind, 'error');
  assert.match(going.text, /^The band ended the USB session because this page took no data for 5 seconds .*Connecting again\.\.\.$/);
  const back = reconnectedMessage([], 'session-ended');
  assert.match(back.text, /ended the USB session.*connected again and the streams are back/);
  assert.match(reconnectedMessage(['LED_SET'], 'session-ended').text, /not everything could be set again/);
  assert.match(disconnectMessage('session-ended', 'Vexo-HACK-42').text, /ended the USB session.*Press Connect again\.$/);
  // A lost link reads as before.
  assert.deepEqual(reconnectingMessage(120), { kind: 'error', text: 'Connection lost. Trying to reconnect for 2 minutes...' });
  assert.deepEqual(reconnectedMessage([]), { kind: 'info', text: 'Reconnected.' });
});

test('dashboard over a fake USB port: a session end is shown and repaired; leaving the page closes the port', { skip, timeout: 60000 }, async (t) => {
  const server = await serveKit();
  const chrome = await startChrome();
  t.after(async () => { await chrome.close(); server.close(); });
  const page = await chrome.openPage(`${server.origin}/dashboard/?band=42`);
  // Put the fake port in place of the browser's own before anything connects.
  await page.run(`(async () => {
    const { fakeSerialWithBand } = await import('/js/test/fake-serial-port.js');
    const fake = fakeSerialWithBand(42, { autoTap: false });
    Object.defineProperty(navigator, 'serial', { value: fake.navigator.serial, configurable: true });
    for (const step of ['setSignals', 'close']) {      // what the port saw, kept where it outlives the page
      const real = fake.port[step];
      fake.port[step] = async (...args) => {
        localStorage.setItem('port-log', (localStorage.getItem('port-log') ?? '') + step + JSON.stringify(args) + ' ');
        return real(...args);
      };
    }
    localStorage.removeItem('port-log');
    window.fake = fake;
    window.statuses = [];
    new MutationObserver(() => window.statuses.push(document.querySelector('#status').textContent))
      .observe(document.querySelector('#status'), { childList: true, characterData: true, subtree: true });
  })()`);
  await page.click('#btn-serial');
  await page.untilText('#status', /^Connected by USB$/);
  await page.until('motion readings on the page', '/\\d/.test(document.querySelector("#motion-measured").textContent)', 15000);
  assert.match(await page.run('fake.log.find((line) => line.startsWith("open"))'), /"bufferSize":8192/);
  const streams = await page.run('fake.sim._s.streams');
  assert.ok(streams !== 0);

  // The band ends the session: streams off, no notice. The port stays open.
  await page.run('window.statuses.length = 0; fake.sim.endUsbSession()');
  const at = Date.now();
  await page.untilText('#message', /The band ended the USB session because this page took no data for 5 seconds/, 6000);
  assert.ok(Date.now() - at < 4000, `the page said so after ${Date.now() - at} ms`);
  await page.untilText('#message', /connected again and the streams are back/, 6000);
  await page.untilText('#status', /^Connected by USB$/);
  const statuses = await page.run('window.statuses');
  assert.ok(statuses.includes('Reconnecting…'), `the status left "Connected" meanwhile: ${JSON.stringify(statuses)}`);
  assert.equal(await page.run('fake.requests'), 1, 'no second chooser');
  await page.until('the streams to be on again', `fake.sim._s.streams === ${streams}`);
  // Data really flows again: the pretend band's motion frames reach the page.
  await page.run('window.frames = 0; fake.sim.onWire = ((real) => (d, b) => { if (d === "to-computer") window.frames++; real(d, b); })(fake.sim.onWire)');
  await sleep(500);
  assert.ok(await page.run('window.frames') > 5);
  assert.deepEqual(page.problems, []);

  // Reload the page (the same as closing the tab or leaving): the port is closed first (DTR low, close), so the band sees a proper close.
  await page.run('localStorage.removeItem("port-log")');
  await page.run('setTimeout(() => location.reload(), 0)');
  await sleep(500);
  await page.until('the page to be loaded again', 'document.readyState === "complete" && typeof window.fake === "undefined"');
  const log = await page.run('localStorage.getItem("port-log")');
  assert.match(String(log), /setSignals\[\{"dataTerminalReady":false\}\]/, 'DTR was lowered');
  assert.match(String(log), /close\[\]/, 'the port was closed');
});
