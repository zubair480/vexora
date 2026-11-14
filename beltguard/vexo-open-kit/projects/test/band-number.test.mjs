// The band number box of the project pages (shared/page.js) takes the same forms as the
// kit's example pages: 42, 07 and the full name Vexo-HACK-42. Anything else is one
// sentence on the page, and there is no band 0. First bandNumber() on its own (plain
// Node, with a stand-in for the page), then the box on a real page in headless Chrome.
//   node --test projects/test/band-number.test.mjs
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { skip, serveKit, startChrome } from './chrome.mjs';

const NOT_A_NUMBER = (text) => `"${text}" is not a band number. Type just the number on your band or its bag, for example 42.`;
const ZERO = 'Band numbers are 1 to 999. 0 is what a band has before it gets a number: ask an organiser to number it.';
const GOOD = [['42', 42], ['07', 7], ['7', 7], ['007', 7], [' 42 ', 42], ['Vexo-HACK-42', 42], ['Vexo-HACK-07', 7],
  ['vexo-hack-42', 42], ['Vexo-HACK-123', 123], ['999', 999], ['1', 1]];
const BAD = ['abc', '1000', '4.2', '-5', 'Vexo-HACK-', 'Vexo-HACK-7', 'Vexo-HACK-1000', 'Vexo-42', '4 2', '٤٢'];
const ZEROS = ['0', '00', '000', 'Vexo-HACK-00', 'Vexo-HACK-000'];

test('bandNumber(): the number, a zero in front, the full name; a sentence for the rest; no band 0', async () => {
  // A stand-in for the page: just enough for shared/page.js to load in Node.
  const parts = new Map();
  const part = (selector) => {
    if (!parts.has(selector)) parts.set(selector, { value: '', hidden: false, textContent: '', style: {}, addEventListener() {} });
    return parts.get(selector);
  };
  const had = { document: globalThis.document, location: globalThis.location };
  globalThis.document = { querySelector: part, querySelectorAll: () => [] };
  globalThis.location = { search: '' };
  try {
    const { bandNumber } = await import('../shared/page.js');
    const typed = (text) => { part('#number').value = text; return bandNumber(); };
    assert.equal(typed(''), null, 'nothing typed');
    assert.equal(typed('   '), null);
    for (const [text, number] of GOOD) assert.equal(typed(text), number, text);
    for (const text of BAD) assert.throws(() => typed(text), { message: NOT_A_NUMBER(text) }, text);
    for (const text of ZEROS) assert.throws(() => typed(text), { message: ZERO }, text);
  } finally {
    globalThis.document = had.document;
    globalThis.location = had.location;
  }
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

test('the box on the page: every good form connects to that band, every other one says what to type', { skip, timeout: 90000 }, async () => {
  const page = await chrome.openPage(`${server.origin}/projects/reaction-game/?link=pretend`);
  const type = (text) => page.run(`document.querySelector('#number').value = ${JSON.stringify(text)}`);
  assert.ok(await page.run('document.querySelector("#number").maxLength') >= 'Vexo-HACK-123'.length, 'the full name fits in the box');
  for (const [text, name] of [['42', 'Vexo-HACK-42'], ['07', 'Vexo-HACK-07'], ['Vexo-HACK-42', 'Vexo-HACK-42'], ['vexo-hack-07', 'Vexo-HACK-07']]) {
    await type(text);
    await page.click('#connect');
    await page.untilText('#message', /Connected to Vexo-HACK-/);
    assert.equal(await page.text('#message'), `Connected to ${name}.`, text);
    await page.click('#disconnect');
    await page.untilText('#message', /^Disconnected\.$/);
  }
  for (const text of ['abc', '1000', 'Vexo-HACK-7']) {
    await type(text);
    await page.click('#connect');
    assert.equal(await page.text('#message'), NOT_A_NUMBER(text));
    assert.equal(await page.run('document.querySelector("#connect").disabled'), false, 'Connect can be pressed again');
  }
  for (const text of ['0', 'Vexo-HACK-00']) {
    await type(text);
    await page.click('#connect');
    assert.equal(await page.text('#message'), ZERO);
  }
  // a real band (Bluetooth) is asked for its number the same way, before the browser's list opens
  await page.run('document.querySelector("#link").value = "ble"');
  await type('abc');
  await page.click('#connect');
  assert.equal(await page.text('#message'), NOT_A_NUMBER('abc'));
  await type('');
  await page.click('#connect');
  assert.match(await page.text('#message'), /Type your band number first.*\(1 to 999\)/);
  assert.deepEqual(page.problems, []);
  await page.close();
});

test('the number in the address takes the same forms', { skip, timeout: 60000 }, async () => {
  const page = await chrome.openPage(`${server.origin}/projects/clap-lights/?link=pretend&band=Vexo-HACK-42`);
  await page.connectPretend();
  assert.match(await page.text('#message'), /^Connected to Vexo-HACK-42\./);
  assert.deepEqual(page.problems, []);
  await page.close();
});
