// Checks the web pages and the Node example.   node --test examples/test/*.mjs
//
// 1. Reads every page as text: the safety header is there, nothing is written inline,
//    nothing is loaded from another site.
// 2. Runs each page's script against a tiny stand-in for the browser page, with the
//    "pretend band" box ticked, and looks at what the script did.
// 3. Runs examples/node/hello.mjs and examples/node/fast-motion.mjs, with a band number,
//    without one, and with wrong arguments.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, existsSync, mkdtempSync, symlinkSync, rmSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { tmpdir } from 'node:os';

const EXAMPLES = join(dirname(fileURLToPath(import.meta.url)), '..');
const WEB = join(EXAMPLES, 'web');
const pagesOnDisk = readdirSync(WEB).filter((name) => name.endsWith('.html')).sort();
const read = (name) => readFileSync(join(WEB, name), 'utf8');
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

test('the three pages exist', () => {
  assert.deepEqual(pagesOnDisk, ['led.html', 'motion-graph.html', 'taps.html']);
});

for (const name of pagesOnDisk) {
  test(`${name}: safe static page`, () => {
    const html = read(name);
    assert.ok(html.includes('<meta http-equiv="Content-Security-Policy" content="default-src \'self\'">'), 'CSP header');
    // every script comes from its own file next to the page
    const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)];
    assert.equal(scripts.length, 1);
    const own = name.replace('.html', '.js');
    assert.equal(scripts[0][1].trim(), `type="module" src="${own}"`);
    assert.equal(scripts[0][2].trim(), '', 'no inline script');
    assert.ok(existsSync(join(WEB, own)));
    assert.ok(read(own).includes("from '../../js/src/index.js'"), 'uses the library from this kit');
    // no inline style, no inline event handlers, no javascript: links
    assert.doesNotMatch(html, /<style\b/i);
    assert.doesNotMatch(html, /\sstyle\s*=/i);
    assert.doesNotMatch(html, /\son[a-z]+\s*=/i);
    assert.doesNotMatch(html, /javascript:/i);
    // the style sheet and the icon are local files
    const links = [...html.matchAll(/<link\b[^>]*>/gi)].map((m) => m[0]);
    assert.deepEqual(links, ['<link rel="icon" href="../../dashboard/icon.png">', '<link rel="stylesheet" href="style.css">']);
    // everything the shared helper looks for is on the page
    for (const id of ['number', 'pretend', 'connect', 'disconnect', 'message', 'light-row', 'light', 'light-text']) {
      assert.ok(html.includes(`id="${id}"`), `#${id}`);
    }
    assert.ok(/<button id="connect">Connect<\/button>/.test(html), 'a Connect button');
  });

  // A page opened by double-click (file://) cannot load its program, so no script can say
  // what is wrong. The help is plain text in the page; the style sheet shows it after a
  // second, and kit.js hides it the moment the program does start.
  test(`${name}: opened as a file, the page itself says to use the local server`, () => {
    const help = read(name).match(/<p id="needs-server">([\s\S]*?)<\/p>/)?.[1].replace(/\s+/g, ' ');
    assert.ok(help, 'the help paragraph');
    assert.ok(help.includes('opened as a file (by double-click)'));
    assert.ok(help.includes('<code>python3 -m http.server 8000 --bind 127.0.0.1</code>'), 'the server command, this computer only');
    assert.ok(help.includes('in your project folder (the folder that holds <code>vexo-open-kit</code>)'), 'where to start the server: my-project/');
    assert.doesNotMatch(help, /in the kit folder/);
    assert.ok(help.includes(`<code>http://localhost:8000/vexo-open-kit/examples/web/${name}</code>`), 'its own address, under my-project/');
    assert.ok(help.includes(`${name}</code> (8000, or the port you started it with) in Chrome or Edge.`), 'port 8000 may be taken: the address follows the port');
    const css = read('style.css');
    assert.match(css, /#needs-server \{ visibility: hidden; animation: show-help 0s 1s forwards;/);
    assert.match(css, /#needs-server\[hidden\] \{ display: none; \}/);
    assert.match(css, /@keyframes show-help \{ to \{ visibility: visible; \} \}/);
    assert.match(read('kit.js'), /^\$\('#needs-server'\)\.hidden = true;$/m);
  });
}

test('nothing in examples/web points to another site', () => {
  for (const name of readdirSync(WEB)) {
    const text = read(name).replaceAll('http://localhost:8000/vexo-open-kit/examples/web/', '');   // the page's own address, as help text
    assert.doesNotMatch(text, /https?:|\/\/[a-z0-9.-]+\.[a-z]{2,}|@import|url\(/i, name);
    for (const m of text.matchAll(/(?:src|href)="([^"]*)"/g)) {
      assert.ok(existsSync(join(WEB, m[1])), `${name}: ${m[1]} is a file in this folder`);
    }
    for (const m of text.matchAll(/from '([^']*)'/g)) {
      assert.ok(existsSync(join(WEB, m[1])), `${name}: import ${m[1]} exists`);
    }
  }
});

// ---- a stand-in for the browser page: just enough for the example scripts

function fakeElement() {
  const listeners = {};
  const drawn = [];
  const pen = new Proxy({}, {
    get: (_t, key) => (...args) => { drawn.push([key, ...args]); },
    set: () => true,
  });
  return {
    value: '', checked: false, hidden: true, disabled: false, textContent: '', style: {},
    width: 600, height: 240, drawn,
    addEventListener(type, fn) { (listeners[type] ??= []).push(fn); },
    getContext: () => pen,
    /** Fire an event and wait for whatever the handlers started. */
    fire(type, event = {}) { return Promise.all((listeners[type] ?? []).map((fn) => fn({ target: this, ...event }))); },
  };
}

let loads = 0;
async function openPage(script, { pretend = true, number = '' } = {}) {
  const elements = new Map();
  const $ = (selector) => {
    if (!elements.has(selector)) elements.set(selector, fakeElement());
    return elements.get(selector);
  };
  globalThis.document = { querySelector: $ };
  globalThis.location = { search: '' };
  globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 16).unref();
  $('#needs-server').hidden = false;                  // as in the page
  await import(`../web/${script}?load=${loads++}`);   // a fresh copy of the page script each time
  // kit.js is loaded once for all tests here (in a browser: once per page), so look the first time only
  if (loads === 1) assert.equal($('#needs-server').hidden, true, 'the program started, so the "opened as a file" help is hidden');
  $('#pretend').checked = pretend;
  $('#number').value = number;
  $('#disconnect').disabled = true;
  return $;
}

test('led.js: connects, blinks green, follows the colour picker', async () => {
  const $ = await openPage('led.js', { number: '7' });
  await $('#connect').fire('click');
  assert.match($('#message').textContent, /Connected to Vexo-HACK-07/);
  assert.equal($('#light').style.backgroundColor, 'rgb(0, 255, 0)');
  assert.equal($('#light-text').textContent, 'blinking');
  assert.equal($('#light-row').hidden, false);
  assert.equal($('#connect').disabled, true);
  assert.equal($('#disconnect').disabled, false);
  $('#colour').value = '#ff8800';
  await $('#colour').fire('change');
  assert.equal($('#light').style.backgroundColor, 'rgb(255, 136, 0)');
  assert.equal($('#light-text').textContent, 'on');
  await $('#off').fire('click');
  assert.equal($('#light').style.backgroundColor, 'rgb(0, 0, 0)');
  await $('#disconnect').fire('click');
  assert.equal($('#message').textContent, 'Disconnected.');
  assert.equal($('#connect').disabled, false);
});

test('taps.js: a tap turns the light green and is listed', async () => {
  const $ = await openPage('taps.js');
  await $('#connect').fire('click');
  assert.match($('#message').textContent, /Connected to Vexo-HACK-42/);
  await sleep(4300);                                  // the pretend band taps itself after 4 seconds
  assert.equal($('#log').textContent, 'tap\n');
  assert.equal($('#light').style.backgroundColor, 'rgb(0, 255, 0)');
  await $('#disconnect').fire('click');
});

test('motion-graph.js: draws three lines from live readings', async () => {
  const $ = await openPage('motion-graph.js');
  await $('#connect').fire('click');
  await sleep(700);
  const drawn = $('#graph').drawn;
  const count = (what) => drawn.filter(([name]) => name === what).length;
  assert.ok(count('clearRect') >= 5, 'redraws');
  assert.equal(count('stroke'), count('beginPath'));
  assert.equal(count('stroke'), 3 * count('clearRect'));
  const points = drawn.filter(([name]) => name === 'lineTo');
  assert.ok(points.length >= 60, `${points.length} points`);
  for (const [, x, y] of points) {
    assert.ok(x >= 0 && x <= 600 && y >= 0 && y <= 240, `point ${x},${y} is on the canvas`);
  }
  // the newest reading as numbers too, so that a still band (three flat lines) does not look dead
  assert.match($('#ax').textContent, /^-?0\.\d\d$/);
  assert.match($('#ay').textContent, /^-?0\.\d\d$/);
  assert.match($('#az').textContent, /^[01]\.\d\d$/);
  const page = read('motion-graph.html');
  for (const id of ['ax', 'ay', 'az']) assert.ok(page.includes(`<b id="${id}">–</b>`), `#${id} is on the page`);
  assert.match(page, /three flat lines mean "not moving", not "not working"/);
  await $('#disconnect').fire('click');
  assert.equal($('#message').textContent, 'Disconnected.');
});

test('without the pretend band, a place with no Web Bluetooth gets a clear message', async () => {
  const $ = await openPage('led.js', { pretend: false, number: '42' });
  await $('#connect').fire('click');
  assert.match($('#message').textContent, /Web Bluetooth/);
  assert.equal($('#connect').disabled, false);        // the button works again for a second try
});

// A fake Web Bluetooth whose list of bands never answers: what a student sees when the
// browser shows no chooser at all. requestDevice() returns a promise that stays open.
function bluetoothThatNeverAnswers(t) {
  const before = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  const before2 = Object.getOwnPropertyDescriptor(globalThis, 'window');
  let asked = 0;
  const answers = [];
  const bluetooth = { requestDevice: () => { asked++; return new Promise((done, fail) => answers.push({ done, fail })); } };
  Object.defineProperty(globalThis, 'navigator', { value: { bluetooth }, configurable: true, writable: true });
  Object.defineProperty(globalThis, 'window', { value: { isSecureContext: true }, configurable: true, writable: true });
  t.after(() => {
    if (before) Object.defineProperty(globalThis, 'navigator', before); else delete globalThis.navigator;
    if (before2) Object.defineProperty(globalThis, 'window', before2); else delete globalThis.window;
  });
  return { asked: () => asked, answers };
}

test('Connecting that never ends has a way out: the second button is Cancel', async (t) => {
  const bluetooth = bluetoothThatNeverAnswers(t);
  const $ = await openPage('led.js', { pretend: false, number: '42' });
  const pressed = $('#connect').fire('click');        // never finishes by itself
  await sleep(20);
  assert.equal(bluetooth.asked(), 1);
  assert.equal($('#message').textContent, 'Connecting...');
  assert.equal($('#connect').disabled, true);
  assert.equal($('#disconnect').disabled, false);
  assert.equal($('#disconnect').textContent, 'Cancel');
  await $('#disconnect').fire('click');
  assert.equal($('#message').textContent, 'Cancelled. Press Connect to try again.');
  assert.equal($('#connect').disabled, false);
  assert.equal($('#disconnect').disabled, true);
  assert.equal($('#disconnect').textContent, 'Disconnect');
  // the old try ending late (the student closes a list that did show up) changes nothing
  bluetooth.answers[0].fail(Object.assign(new Error('User cancelled'), { name: 'NotFoundError' }));
  await pressed;
  assert.equal($('#message').textContent, 'Cancelled. Press Connect to try again.');
  // and Connect works again, here with the pretend band
  $('#pretend').checked = true;
  await $('#connect').fire('click');
  assert.match($('#message').textContent, /Connected to Vexo-HACK-42/);
  assert.equal($('#disconnect').textContent, 'Disconnect');
  await $('#disconnect').fire('click');
  assert.equal($('#message').textContent, 'Disconnected.');
});

test('kit.js says so when connecting takes long', () => {
  const kit = read('kit.js');
  assert.match(kit, /Still connecting\.\.\. Your band is not in the list\? Close whatever else is connected to it /);
  assert.match(kit, /No list of bands on the screen\? Press Cancel and try again\./);
  assert.match(kit, /band\.on\('stream-taken', \(e\) => say\(e\.message\)\)/);
  assert.match(kit, /const STILL_CONNECTING_MS = 15000;/);
});

test('a wrong band number gives a message, not a crash', async () => {
  // the same words as the dashboard uses for a bad ?band=, never `got "NaN"`
  for (const typed of ['abc', '4x', '1234', '-5']) {
    const $ = await openPage('led.js', { number: typed });
    await $('#connect').fire('click');
    assert.equal($('#message').textContent, `"${typed}" is not a band number. Type the number written on your band's bag.`);
    assert.equal($('#connect').disabled, false);
  }
  assert.match(readFileSync(join(EXAMPLES, '..', 'dashboard', 'app.js'), 'utf8'), /`"\$\{raw\}" is not a band number\. Type the number written on your band's bag\.`/);
});

test('the band number may be typed as 7, 07 or the full name', async () => {
  for (const [typed, name] of [['7', 'Vexo-HACK-07'], ['07', 'Vexo-HACK-07'], [' 42 ', 'Vexo-HACK-42'], ['Vexo-HACK-42', 'Vexo-HACK-42']]) {
    const $ = await openPage('led.js', { number: typed });
    await $('#connect').fire('click');
    assert.equal($('#message').textContent, `Connected to ${name}. It blinks green. Now pick a colour.`, typed);
    await $('#disconnect').fire('click');
  }
});

/** Run one Node example the way a student does: in my-project/, the kit inside it as vexo-open-kit/. */
const PROJECT = mkdtempSync(join(tmpdir(), 'vexo-node-examples-'));
symlinkSync(join(EXAMPLES, '..'), join(PROJECT, 'vexo-open-kit'), 'junction');
test.after(() => rmSync(PROJECT, { recursive: true, force: true }));
const node = (name, ...words) => new Promise((done) => {
  execFile(process.execPath, [`vexo-open-kit/examples/node/${name}`, ...words], { cwd: PROJECT, timeout: 30000 },
    (error, stdout, stderr) => done({ code: error ? error.code : 0, stdout, stderr }));
});

test('node/hello.mjs runs by itself and ends', async () => {
  const { code, stdout: out, stderr } = await node('hello.mjs', '--seconds', '4.3');
  assert.deepEqual([code, stderr], [0, '']);
  assert.match(out, /^Connected to Vexo-HACK-42, a pretend band$/m);      // no number: band 42, and it says what it talks to
  assert.match(out, /^LED is \{ r: 0, g: 160, b: 255/m);
  assert.ok(out.match(/^x -?\d\.\d\d {2}y -?\d\.\d\d {2}z -?\d\.\d\d$/gm).length >= 30);
  assert.match(out, /^tap!$/m);
});

test('node examples: the first argument is the band number, in the same forms as the Python examples take', async () => {
  for (const [typed, name] of [['42', 'Vexo-HACK-42'], ['07', 'Vexo-HACK-07'], ['7', 'Vexo-HACK-07'], ['Vexo-HACK-42', 'Vexo-HACK-42'],
    ['vexo-hack-123', 'Vexo-HACK-123']]) {
    const hello = await node('hello.mjs', typed, '--seconds', '0.3');
    assert.deepEqual([hello.code, hello.stderr], [0, ''], typed);
    assert.ok(hello.stdout.startsWith(`Connected to ${name}, a pretend band\n`), `${typed}: ${hello.stdout}`);
    assert.ok(hello.stdout.split('\n').length < 12, `${typed} is the band number, not a number of seconds`);
  }
  const fast = await node('fast-motion.mjs', '07', '--seconds=0');
  assert.deepEqual([fast.code, fast.stderr], [0, '']);
  assert.match(fast.stdout, /^Connected to Vexo-HACK-07, a pretend band with firmware 1\.1\.0$/m);
  // --sim is what the Python examples take for "no band": here it changes nothing, and it is not an error
  const sim = await node('hello.mjs', '--sim', '--seconds', '0');
  assert.deepEqual([sim.code, sim.stderr], [0, '']);
  assert.match(sim.stdout, /^Connected to Vexo-HACK-42, a pretend band$/m);
});

test('node examples: a wrong argument ends with one sentence and the command to type, as it was typed', async () => {
  const how = (name) => `Type:   node vexo-open-kit/examples/node/${name} 42   or   node vexo-open-kit/examples/node/${name} 42 --seconds 10\n`;
  const wrong = [
    [['abc'], '"abc" is not a band number. Type just the number on your band or its bag, for example 42.'],
    [['4x'], '"4x" is not a band number. Type just the number on your band or its bag, for example 42.'],
    [['1234'], '"1234" is not a band number. Type just the number on your band or its bag, for example 42.'],
    [['Vexo-HACK-'], '"Vexo-HACK-" is not a band number. Type just the number on your band or its bag, for example 42.'],
    [['4.3'], '"4.3" is not a band number. Type just the number on your band or its bag, for example 42.'],   // the old "seconds" argument
    [['0'], 'Band numbers are 1 to 999. 0 is what a band has before it gets a number: ask an organiser to number it.'],
    [['Vexo-HACK-00'], 'Band numbers are 1 to 999. 0 is what a band has before it gets a number: ask an organiser to number it.'],
    [['42', '--seconds', 'abc'], '--seconds needs a number of seconds from 0 to 3600, for example --seconds 10, not "abc".'],
    [['42', '--seconds', '-1'], '--seconds needs a number of seconds from 0 to 3600, for example --seconds 10, not "-1".'],
    [['42', '--seconds'], '--seconds needs a number of seconds from 0 to 3600, for example --seconds 10.'],
    [['42', '10'], '"10" is one argument too many. Type the band number first, then --seconds for the time, for example 42 --seconds 10.'],
    [['42', '--fast'], '"--fast" is not an option of this example. The only one is --seconds, for example --seconds 10.'],
    [['42', '--usb'], 'Node cannot use the USB cable (or Bluetooth), so this example only runs with the pretend band. '
      + 'For a real band use the Python examples (examples/python) or the web pages (examples/web).'],
  ];
  for (const name of ['hello.mjs', 'fast-motion.mjs']) {
    for (const [words, sentence] of wrong) {
      const done = await node(name, ...words);
      assert.deepEqual([done.code, done.stdout, done.stderr], [2, '', `${sentence}\n${how(name)}`], `${name} ${words.join(' ')}`);
    }
  }
});

test('node examples: the header of each file gives the command for my-project/ and says the band is a pretend one', () => {
  for (const name of ['hello.mjs', 'fast-motion.mjs']) {
    const text = readFileSync(join(EXAMPLES, 'node', name), 'utf8');
    assert.match(text, new RegExp(`^// .*pretend band\\. Type it in my-project/:\\n//   node vexo-open-kit/examples/node/${name} 42 `));
    assert.match(text, new RegExp(`^//   node vexo-open-kit/examples/node/${name} 42 --seconds 10 `, 'm'));
    assert.match(text, /cannot reach a real\s+(\/\/ )?band/);
    assert.match(text, /Band\.connect\(number, \{ transport: 'sim' \}\)/);
    assert.doesNotMatch(text, /process\.argv/);
  }
});

test('node/fast-motion.mjs: the fastest accel-only rate, every sample counted, real time on the samples', async () => {
  const before = Date.now();
  const { code, stdout: out, stderr } = await node('fast-motion.mjs', '42', '--seconds', '2');
  assert.deepEqual([code, stderr], [0, '']);
  assert.match(out, /^Connected to Vexo-HACK-42, a pretend band with firmware 1\.1\.0$/m);
  assert.match(out, /^The most this link carries: 416 a second with both sensors, 833 with accel alone$/m);
  assert.match(out, /^Motion is on: 833 samples per second$/m);
  const lines = [...out.matchAll(/^(\d+) samples, (\d+) lost\. Newest: z -?\d\.\d\d g at (\S+)$/gm)];
  assert.equal(lines.length, 2);
  for (const [, samples, lost, when] of lines) {
    assert.ok(Number(samples) > 500 && Number(samples) < 1200, `${samples} samples in a second`);
    assert.equal(lost, '0');
    assert.ok(Math.abs(Date.parse(when) - before) < 30000, `${when} is now`);
  }
});
