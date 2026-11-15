// Checks on the dashboard's files as text: the page must stay static, self-contained
// and safe to host anywhere. Run: node --test dashboard/test/*.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';

const dir = new URL('../', import.meta.url);
const read = (name) => readFileSync(new URL(name, dir), 'utf8');
// Every text file the page is made of (the test folder is not part of the page).
const pageFiles = readdirSync(dir).filter((name) => /\.(html|js|css)$/.test(name));
const html = read('index.html');
// The code for the optional Listen and Live transcript. Held to the same rules as the
// files above, with two exceptions that are spelled out and tested further down.
const speechFiles = readdirSync(new URL('speech/', dir)).filter((name) => name.endsWith('.js')).map((name) => `speech/${name}`);
function filesUnder(folder, prefix = '') {
  return readdirSync(folder, { withFileTypes: true }).flatMap((entry) => (entry.isDirectory()
    ? filesUnder(new URL(`${entry.name}/`, folder), `${prefix}${entry.name}/`)
    : [`${prefix}${entry.name}`]));
}

test('the page is made of the expected files', () => {
  for (const name of ['index.html', 'app.js', 'style.css']) assert.ok(pageFiles.includes(name), name);
});

test('index.html has the strict Content-Security-Policy', () => {
  assert.ok(html.includes(`<meta http-equiv="Content-Security-Policy" content="default-src 'self'">`));
  assert.ok(html.indexOf('Content-Security-Policy') < html.indexOf('<link'), 'the policy must come before anything is loaded');
});

test('index.html has nothing inline: no style attributes, style elements, script bodies or on* handlers', () => {
  assert.doesNotMatch(html, /\sstyle\s*=/i);
  assert.doesNotMatch(html, /<style\b/i);
  assert.doesNotMatch(html, /\son[a-z]+\s*=/i);
  assert.doesNotMatch(html, /javascript:/i);
  const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)];
  assert.equal(scripts.length, 2);       // the page's program, and before it the few lines for a page opened as a file
  for (const [, attributes, body] of scripts) {
    assert.match(attributes, /\bsrc="(app|file-help)\.js"/);
    assert.equal(body.trim(), '');
  }
});

test('index.html only loads files that sit next to it', () => {
  const targets = [...html.matchAll(/\b(?:src|href|action)\s*=\s*"([^"]*)"/gi)].map((m) => m[1]);
  assert.deepEqual(targets.sort(), ['app.js', 'file-help.js', 'icon.png', 'style.css']);
});

test('every input has a label', () => {
  const controls = [...html.matchAll(/<(?:input|select)\b[^>]*>/gi)].map((m) => m[0]);
  assert.ok(controls.length >= 6);
  for (const tag of controls) {
    const id = /\bid="([^"]+)"/.exec(tag)?.[1];
    assert.ok(id, `no id on ${tag}`);
    assert.ok(html.includes(`for="${id}"`) || /aria-label="/.test(tag), `no label for ${id}`);
  }
  assert.match(html, /id="status"[^>]*aria-live="polite"/);
});

const FORBIDDEN = [
  [/https?:\/\//i, 'a web address (the page must not load or contact anything)'],
  [/\b(?:data|blob):/i, 'a data: or blob: URL'],
  [/\beval\s*\(/, 'eval('],
  [/new\s+Function\b/, 'new Function'],
  [/\b(?:innerHTML|outerHTML|insertAdjacentHTML)\b/, 'HTML built from text'],
  [/document\.write/, 'document.write'],
  [/\bfetch\s*\(/, 'fetch('],
  [/XMLHttpRequest/, 'XMLHttpRequest'],
  [/WebSocket/, 'WebSocket'],
  [/EventSource/, 'EventSource'],
  [/sendBeacon/, 'sendBeacon'],
  [/\bimport\s*\(/, 'dynamic import'],
  [/@import|url\s*\(/i, 'a style sheet loading something'],
  [/setAttribute\(\s*['"]style/, "setAttribute('style')"],
  [/cssText/, 'cssText'],
  [/sessionStorage|indexedDB|document\.cookie/, 'storage other than the LED colour'],
  [/PROTOCOL_IS_PLACEHOLDER|contract-pending|Preview build/i, 'the old preview notes'],
];

for (const name of pageFiles) {
  test(`${name} contains nothing forbidden`, () => {
    const text = read(name);
    for (const [pattern, what] of FORBIDDEN) assert.doesNotMatch(text, pattern, `${name} contains ${what}`);
  });
}

test('scripts only import the library and each other', () => {
  for (const name of pageFiles.filter((n) => n.endsWith('.js'))) {
    const sources = [...read(name).matchAll(/\bfrom\s+'([^']+)'/g)].map((m) => m[1]);
    // app.js may also import the panel of the optional Listen and Live transcript.
    const allowed = name === 'app.js' ? ['../js/src/index.js', './logic.js', './speech/panel.js'] : ['../js/src/index.js', './logic.js'];
    for (const source of sources) assert.ok(allowed.includes(source), `${name} imports ${source}`);
  }
});

test('the page has a chip temperature reading, a note under the loudness bar, and help for the USB port list', () => {
  const app = read('app.js');
  // Chip temperature: a place for the number, its unit, and a heading that says whose temperature it is.
  assert.match(html, /<h2 class="sub">Chip temperature<\/h2>\s*<div class="readout"><span id="temperature">–<\/span> <small>°C<\/small><\/div>\s*<p class="hint" id="temperature-note"><\/p>/);
  assert.match(app, /b\.on\('temperature',/);
  assert.match(app, /\$\('temperature-note'\)\.textContent = TEMPERATURE_NOTE/);
  // Loudness: the bar is drawn from loudnessBar(), not from the library's 0..96 dB level, and the page says what it shows.
  assert.match(app, /micLevel = loudnessBar\(m\.rmsDb\)/);
  assert.doesNotMatch(app, /micLevel = m\.level/);
  assert.ok(html.indexOf('id="mic-bar"') < html.indexOf('id="mic-note"') && html.indexOf('id="mic-note"') < html.indexOf('id="audio-switch"'));
  assert.match(app, /\$\('mic-note'\)\.textContent = LOUDNESS_NOTE/);
  // USB: next to the buttons, which entry to pick on each system.
  const help = /<p id="usb-help" class="hint">([\s\S]*?)<\/p>/.exec(html)?.[1] ?? '';
  for (const word of ['Vexo Open band', 'cu.usbmodem', 'COM', 'ttyACM', 'Bluetooth-Incoming-Port', 'debug-console']) assert.ok(help.includes(word), word);
  assert.ok(html.indexOf('id="btn-serial"') < html.indexOf('id="usb-help"') && html.indexOf('id="usb-help"') < html.indexOf('id="panels"'));
  assert.match(app, /\$\('usb-help'\)\.hidden = linked \|\| !!noSerial/);
});

// A page opened by double-click (file://) cannot load app.js, so no script can say what is
// wrong, and the policy above forbids a script written into the page. The help is plain
// text in the page: the style sheet shows it after a second, app.js hides it when it runs.
test('opened as a file, the page itself says to start the local server, without any inline script', () => {
  const app = read('app.js');
  const css = read('style.css');
  const help = /<p id="needs-server" class="notice">([\s\S]*?)<\/p>/.exec(html)?.[1] ?? '';
  assert.ok(help.includes('opened as a file (by double-click)'), help);
  assert.ok(help.includes('<code>python3 -m http.server 8000 --bind 127.0.0.1</code>'), 'the server command, this computer only');
  assert.ok(help.includes('in your project folder (the folder that holds <b>vexo-open-kit</b>)'), 'where to start it');
  assert.ok(help.includes('<code id="needs-server-address">localhost:8000/vexo-open-kit/dashboard/</code>'), 'the address under my-project/');
  assert.ok(help.includes('dashboard/</code> (8000, or the port you started it with)'), 'port 8000 may be taken: the address follows the port');
  assert.ok(html.indexOf('id="needs-server"') < html.indexOf('id="connect"'), 'above the Connect panel');
  assert.match(css, /#needs-server \{ height: 0; margin: 0; padding: 0; overflow: hidden; animation: show-help 0s 1s forwards; \}/);
  assert.match(css, /@keyframes show-help \{ to \{ height: auto; [^}]*\} \}/);
  assert.match(css, /\[hidden\] \{ display: none !important; \}/);
  assert.match(app, /^\$\('needs-server'\)\.hidden = true;$/m);
  // it is the first thing app.js does, before anything that could fail
  assert.ok(app.indexOf("$('needs-server').hidden = true;") < app.indexOf('function say('));
});

// The help prints the address to type. A page opened as a file with ?band=42 (from the
// kit's top page, or typed) must print the address with ?band=42, or the student lands on
// "No band chosen". A module cannot load from a file, so a plain script does it.
test('opened as a file with ?band=42, the printed address keeps the band number and every other parameter', () => {
  const script = read('file-help.js');
  const printed = (protocol, search) => {
    const address = { textContent: /<code id="needs-server-address">([^<]*)<\/code>/.exec(html)[1] };
    const page = { getElementById: (id) => (id === 'needs-server-address' ? address : null) };
    globalThis.Function('location', 'document', script)({ protocol, search }, page);
    return address.textContent;
  };
  assert.equal(printed('file:', '?band=42'), 'localhost:8000/vexo-open-kit/dashboard/?band=42');
  assert.equal(printed('file:', '?band=07&firmware=1.0&link=usb'), 'localhost:8000/vexo-open-kit/dashboard/?band=07&firmware=1.0&link=usb');
  assert.equal(printed('file:', ''), 'localhost:8000/vexo-open-kit/dashboard/');
  // served from a web server the help is hidden and the script leaves it alone
  assert.equal(printed('http:', '?band=42'), 'localhost:8000/vexo-open-kit/dashboard/');
  // a plain script (a module does not load from a file), placed after the help and before the page's program
  assert.match(html, /<script src="file-help\.js"><\/script>\n<script type="module" src="app\.js"><\/script>/);
  assert.ok(html.indexOf('id="needs-server-address"') < html.indexOf('src="file-help.js"'));
  assert.doesNotMatch(script, /\b(import|export)\b/);
  // only as text: whatever is in the address can never become HTML
  assert.match(script, /\.textContent \+= location\.search;/);
});

test('the connect steps ("Double-tap your band ...") are put away while a band is connected', () => {
  const app = read('app.js');
  assert.match(html, /<ol id="connect-steps" class="steps">\s*<li><b>Double-tap<\/b> your band to wake it up\.<\/li>/);
  assert.match(app, /\$\('connect-steps'\)\.hidden = linked;/);
});

test('the page has a Steps panel fed by the activity stream, and it is emptied on disconnect', () => {
  const app = read('app.js');
  assert.match(html, /<h2>Steps<\/h2>\s*<div class="big"><span id="steps">–<\/span><small>steps<\/small><\/div>\s*<p class="hint" id="steps-detail">Waiting for the band\.<\/p>\s*<p class="hint" id="steps-note"><\/p>/);
  assert.ok(html.indexOf('id="panels"') < html.indexOf('id="steps"'), 'inside the live panels');
  assert.match(app, /b\.on\('activity', \(a\) => \{\s*\$\('steps'\)\.textContent = stepsText\(stepCounter\.add\(a\.steps\)\);\s*\$\('steps-detail'\)\.textContent = activityDetail\(a\);/);
  assert.match(app, /\$\('steps-note'\)\.textContent = STEPS_NOTE/);
  assert.match(app, /stepCounter\.reset\(\);\s*\$\('steps'\)\.textContent = stepsText\(null\);/);      // an old count would look like a live one
});

test('a connection attempt can be cancelled: a Cancel button, shown only while the browser\'s list is awaited', () => {
  const app = read('app.js');
  assert.match(html, /<button id="btn-cancel" hidden>Cancel<\/button>/);
  assert.match(app, /\$\('btn-cancel'\)\.hidden = !\(busy && cancellable\)/);
  assert.match(app, /cancellable = kind !== 'sim'/);
  assert.match(app, /say\(CANCELLED_MESSAGE\)/);
  assert.match(app, /say\(stillConnectingMessage\(kind, chosen\.name\)\)/);
  assert.match(app, /if \(mine !== attempt\) \{[^}]*\s*connected\?\.disconnect\(\)/);      // a band that answers after Cancel is let go
});

test('the Microphone panel has a note for the pretend band, hidden until the pretend band is connected', () => {
  const app = read('app.js');
  const panel = /<h2>Microphone<\/h2>([\s\S]*?)<\/section>/.exec(html)[1];
  assert.match(panel, /^\s*<p id="sim-mic-note" class="notice" hidden><\/p>/);      // first thing in the panel, above every switch
  assert.match(app, /\$\('sim-mic-note'\)\.textContent = PRETEND_MIC_NOTE/);
  assert.match(app, /\$\('sim-mic-note'\)\.hidden = !\(linked && sim\)/);
});

// ---- the optional Listen and Live transcript (dashboard/speech/)

// The same list as for the page, minus what one file needs to do its one job:
//   speech/load.js      is the single place that downloads (the pretend band's voice, same server only)
//   speech/deepgram.js  is the single place that opens a connection to another server: Live
//                       transcript's link to Deepgram. It runs only in the hidden page
//                       speech/deepgram-frame.html (deepgram.test.mjs holds the tests of both)
const SPEECH_EXCEPTIONS = {
  'speech/load.js': ['fetch('],
  'speech/deepgram.js': ['WebSocket'],
};
for (const name of speechFiles) {
  test(`${name} contains nothing forbidden`, () => {
    const text = read(name);
    const excused = SPEECH_EXCEPTIONS[name] ?? [];
    for (const [pattern, what] of FORBIDDEN) {
      if (excused.includes(what)) continue;
      assert.doesNotMatch(text, pattern, `${name} contains ${what}`);
    }
    assert.doesNotMatch(text, /Vexo-/, `${name} spells out the name prefix`);
    assert.doesNotMatch(text, /localStorage/, `${name} stores something`);
  });
}

test('the exceptions are exactly these two files, and each uses its exception in one narrow way', () => {
  assert.deepEqual(Object.keys(SPEECH_EXCEPTIONS).filter((name) => !speechFiles.includes(name)), []);
  // load.js: one download call; only the pretend band's voice; this folder only; the
  // browser is told to refuse another server and any redirect.
  const load = read('speech/load.js');
  assert.equal([...load.matchAll(/\bfetch\s*\(/g)].length, 1);
  assert.ok(load.includes("fetch(HERE + name, { mode: 'same-origin', credentials: 'same-origin', redirect: 'error' })"));
  assert.ok(load.indexOf('if (!ALLOWED.has(name)) throw') > 0 && load.indexOf('if (!ALLOWED.has(name)) throw') < load.indexOf('fetch('));
  assert.ok(load.includes('const ALLOWED = new Set([SAMPLE_FILE]);'));
  assert.ok(load.includes("const HERE = import.meta.url.slice(0, import.meta.url.lastIndexOf('/') + 1);"));
  // deepgram.js: one connection, to the fixed address, with the key in the protocol field.
  const deepgram = read('speech/deepgram.js');
  assert.deepEqual([...deepgram.matchAll(/new\s+(\w+)\s*\(([^)]*)\)/g)].map((m) => `${m[1]}(${m[2]})`), ["Socket(DEEPGRAM_ADDRESS, ['token', key])"]);
  assert.equal(deepgram.split('globalThis.WebSocket').length - 1, 1);
});

test('speech scripts only import each other and the library\'s sound helpers', () => {
  const own = ['./pure.js', './load.js', './speech.js', './online.js', './deepgram.js'];
  for (const name of speechFiles) {
    const sources = [...read(name).matchAll(/\bfrom\s+'([^']+)'/g)].map((m) => m[1]);
    // pure.js takes the sound helpers (lost packets, Ogg reading) from the kit's library
    const allowed = name === 'speech/pure.js' ? [...own, '../../js/src/audio.js'] : own;
    for (const source of sources) assert.ok(allowed.includes(source), `${name} imports ${source}`);
  }
});

test('no speech model runs in the page: no worker, no vendored runtime, nothing downloaded but the pretend band\'s voice', () => {
  const all = filesUnder(new URL('speech/', dir));
  assert.deepEqual(all.sort(), ['README.md', 'deepgram-frame.html', 'deepgram-frame.js', 'deepgram.js', 'load.js', 'online.js', 'panel.js', 'pure.js', 'sample-speech.opus', 'speech.js']);
  for (const name of [...pageFiles, ...speechFiles]) {
    assert.doesNotMatch(read(name), /new\s+(?:Shared)?Worker\b|importScripts|WebAssembly|\.wasm|\.onnx|vendor\//, `${name} starts a worker or loads a model`);
  }
  // Nothing of Listen runs when the page loads: the decoder and the audio output are only made inside methods.
  const speech = read('speech/speech.js');
  for (const made of ['new AudioDecoder(', 'new Context(']) {
    const line = speech.split('\n').find((l) => l.includes(made));
    assert.match(line, /^\s{4,}/, `${made} must sit inside a method`);
  }
});

// The one exception: Live transcript's link to Deepgram. Its address stands once in the
// code that connects and once in the security policy of the hidden page that code runs in.
const ONLINE_ADDRESS = 'wss://api.deepgram.com/v1/listen';
const NAMES_THE_ADDRESS = ['speech/deepgram.js', 'speech/deepgram-frame.html'];
test('nothing in dashboard/, outside the tests, holds a web address, except the two files of the link to Deepgram', () => {
  const files = filesUnder(dir).filter((name) => !name.startsWith('test/') && /\.(html|js|mjs|css|md|txt|json|svg)$/.test(name));
  assert.ok(files.includes('speech/README.md') && files.includes('speech/speech.js') && files.includes('index.html'));
  for (const name of NAMES_THE_ADDRESS) assert.ok(files.includes(name), name);
  for (const name of files) {
    const found = read(name).match(/(?:https?|wss?|ftp):\/\/[^\s"'`?]*/gi) ?? [];
    assert.deepEqual(found, NAMES_THE_ADDRESS.includes(name) ? [ONLINE_ADDRESS] : [], `${name} holds a web address`);
  }
});

test('the files in speech/ have names and sizes the repository accepts', () => {
  const all = filesUnder(new URL('speech/', dir));
  for (const name of all) {
    assert.doesNotMatch(name, /(\.pem|\.key|\.der|\.p12|\.pfx|\.env|\.uf2|\.hex|\.bin|\.elf|\.ogg|\.wav)$|(^|\/)(build|dist|models|node_modules)\//i, `${name} would be refused or ignored by git`);
    assert.ok(statSync(new URL(`speech/${name}`, dir)).size < 1e6, `${name} is over 1 MB`);
  }
});

test('the only thing stored is the LED colour, and storage failures are caught', () => {
  const app = read('app.js');
  const uses = [...app.matchAll(/localStorage\.(\w+)\(([^,)]*)/g)].map((m) => `${m[1]}:${m[2]}`);
  assert.deepEqual(uses.sort(), ['getItem:COLOUR_KEY', 'setItem:COLOUR_KEY']);
  for (const m of app.matchAll(/localStorage\./g)) {
    const before = app.slice(0, m.index);
    assert.ok(before.lastIndexOf('try {') > before.lastIndexOf('}'), 'localStorage must sit directly inside a try block');
  }
  assert.doesNotMatch(read('logic.js'), /localStorage/);
});

test('the band name is never built by hand', () => {
  for (const name of pageFiles) assert.doesNotMatch(read(name), /Vexo-/, `${name} spells out the name prefix`);
});

test('the page connects with auto-reconnect on, for every kind of link, and shows both reconnect events', () => {
  const app = read('app.js');
  const connects = [...app.matchAll(/Band\.connect\(([^;]*)\);/g)].map((m) => m[1]);
  assert.equal(connects.length, 1);                                    // one place connects, whatever the transport
  assert.match(connects[0], /autoReconnect: true/);
  assert.match(app, /\.on\('reconnecting'/);
  assert.match(app, /\.on\('reconnected'/);
  assert.match(app, /reconnectingMessage\(/);
  assert.match(app, /reconnectedMessage\(/);
});

test('the page only asks for the `link` stream through startupStreams, which leaves it out over USB', () => {
  const app = read('app.js');
  assert.doesNotMatch(app, /link:\s*true/);
  assert.match(app, /startupStreams\(kind\)/);
  assert.match(read('logic.js'), /if \(kind !== 'serial'\) list\.push\(\['signal', \{ link: true \}\]\)/);
});
