// Follows the JavaScript in the docs the way a student does.   node --test examples/test/*.mjs
//
// The printed code is copied out of the markdown files and put where the pages say: in
// my-project/, the student's own folder, with the kit inside it as vexo-open-kit/. That
// goes for docs/api.md and for AGENTS.md alike. The one change made is the one the pages
// describe for "no band": Band.connect(42) becomes Band.connect(42, { transport: 'sim' }).
// Nothing here can reach Bluetooth or a USB port. my-project/ is a copy in a temporary place.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, existsSync, mkdtempSync, mkdirSync, cpSync, rmSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { tmpdir } from 'node:os';

const KIT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const read = (page) => readFileSync(join(KIT, page), 'utf8');

// my-project/vexo-open-kit/js: the layout AGENTS.md draws
const TEMP = mkdtempSync(join(tmpdir(), 'vexo-docs-'));
const PROJECT = join(TEMP, 'my-project');
const KIT_COPY = join(PROJECT, 'vexo-open-kit');
mkdirSync(KIT_COPY, { recursive: true });
cpSync(join(KIT, 'js', 'src'), join(KIT_COPY, 'js', 'src'), { recursive: true });
const VOICE = 'dashboard/speech/sample-speech.opus';      // the recording the pages name as the pretend band's voice
mkdirSync(join(KIT_COPY, 'dashboard', 'speech'), { recursive: true });
cpSync(join(KIT, VOICE), join(KIT_COPY, VOICE));
const LIBRARY = './vexo-open-kit/js/src/index.js';        // the import path from a file in my-project/
test.after(() => rmSync(TEMP, { recursive: true, force: true }));

/** The text under one heading, up to the next heading of the same or a higher level. */
function section(text, heading) {
  const start = text.indexOf(`\n## ${heading}`);
  assert.notEqual(start, -1, `heading "${heading}" is gone`);
  const end = text.indexOf('\n## ', start + 1);
  return text.slice(start, end === -1 ? undefined : end);
}

const blocks = (text, language) =>
  [...text.matchAll(new RegExp('^```' + language + '\\n([\\s\\S]*?)^```$', 'gm'))].map((m) => m[1]);

/** The documented "no band" change, and proof that no other kind of connect is left. */
function pretend(code) {
  const changed = code
    .replace(/Band\.connect\((\w+)\)/g, "Band.connect($1, { transport: 'sim' })")
    .replace(/Band\.connect\((\w+), \{ (?!transport)/g, "Band.connect($1, { transport: 'sim', ");
  for (const line of changed.split('\n')) {
    const call = line.split('//')[0].match(/Band\.connect\([^)]*\)/);
    if (call) assert.match(call[0], /transport: 'sim'/, `this connect would look for a real band: ${call[0]}`);
  }
  return changed;
}

const run = (file, cwd) => new Promise((done, fail) => {
  execFile(process.execPath, [file], { cwd, timeout: 30000 },
    (error, stdout, stderr) => (error ? fail(new Error(`${error.message}\n${stderr}`)) : done(stdout)));
});

test('the pretend-band change is the only way a printed program gets to run', () => {
  assert.equal(pretend('await Band.connect(BAND);'), "await Band.connect(BAND, { transport: 'sim' });");
  assert.equal(pretend('Band.connect(42, { autoReconnect: true })'), "Band.connect(42, { transport: 'sim', autoReconnect: true })");
  assert.throws(() => pretend("Band.connect(42, { transport: 'ble' })"));
});

test('docs/api.md: every import line in the table finds the library from the folder it names', () => {
  const where = section(read('docs/api.md'), 'Where your code goes');
  const rows = [...where.matchAll(/^\| (.+?) \| `import \{ Band \} from '([^']+)';` \|$/gm)];
  assert.equal(rows.length, 3);
  const folders = {
    'the kit folder': [KIT],
    '`examples/web/` or `examples/node/`': [join(KIT, 'examples', 'web'), join(KIT, 'examples', 'node')],
    'your own folder with the kit inside it (`my-project/`)': [PROJECT],
  };
  for (const [, place, path] of rows) {
    assert.ok(folders[place], `a new row: ${place}`);
    for (const folder of folders[place]) {
      assert.ok(existsSync(join(folder, path)), `${path} from ${folder}`);
    }
  }
});

test('docs/api.md: the first JavaScript program runs in Node as the page says, in my-project/', async () => {
  const page = read('docs/api.md');
  const where = section(page, 'Where your code goes');
  const named = where.match(/write\s+`Band\.connect\(42, \{ transport: 'sim' \}\)` in it, save it as `([\w.]+)` in `my-project\/` and run:\s+node ([\w.]+)\n/);
  assert.ok(named, 'the page no longer says where to save the program and how to start it');
  assert.equal(named[1], named[2]);
  const whole = blocks(page, 'js').filter((code) => code.startsWith('import '));
  assert.equal(whole.length, 2, 'two whole JavaScript programs on the page: the first one and the voice for the pretend band');
  for (const code of whole) assert.ok(code.includes(`from '${LIBRARY}';`), 'a whole program imports the library from my-project/');
  assert.doesNotMatch(whole[0], /transport/);                      // as printed it is for a real band
  writeFileSync(join(PROJECT, named[1]), pretend(whole[0]));       // "in my-project/"
  const out = await run(named[1], PROJECT);
  assert.match(out, /^LED is \{ r: 0, g: 255, b: 0/);
});

test('docs/api.md: the JavaScript voice for the pretend band runs as printed and plays the file the page names', async () => {
  const part = read('docs/api.md').split('\n### A voice for the pretend band\n')[1].split('\n## ')[0];
  assert.ok(part.includes(`\`${VOICE}\``));
  const [program] = blocks(part, 'js');
  assert.match(part, /save it as `voice\.mjs` in `my-project\/` and run `node voice\.mjs`/);
  assert.ok(program.includes(`oggPackets(readFileSync('./vexo-open-kit/${VOICE}'))`));
  assert.ok(program.includes('new SimTransport(42, { audioPackets: voice })'));
  writeFileSync(join(PROJECT, 'voice.mjs'), program);
  const out = await run('voice.mjs', PROJECT);
  const count = Number(out.match(/^(\d+) packets of a voice\n$/)?.[1]);
  assert.ok(count >= 90 && count <= 105, out);                    // 2 seconds, 50 a second
  // and they are the file's packets, in order, not silence
  const { Band, SimTransport, oggPackets } = await import('../../js/src/index.js');
  const voice = oggPackets(readFileSync(join(KIT, VOICE)));
  assert.equal(voice.length, 796);                                // 15.9 seconds: "16 seconds of a person reading aloud"
  const sim = new SimTransport(42, { audioPackets: voice, realtime: false });
  const band = await Band.connect(42, { transport: sim });
  const heard = [];
  band.on('audio', (a) => heard.push(a.opus));
  await band.configure({ audio: true });
  sim.step(200);
  assert.ok(heard.length >= 9);
  heard.forEach((opus, i) => assert.deepEqual([...opus], [...voice[i]]));
  // the line for a web page (fetch instead of readFileSync) names the same file
  assert.ok(part.includes(`fetch('./vexo-open-kit/${VOICE}')`));
});

// The short JavaScript blocks of the firmware 1.1 parts are lines "to put into a program".
// Here each one is put into the smallest program around it (connect to the pretend band,
// the lines, a moment for events, disconnect) and run. A block that connects by itself
// gets no connect line added.
test('docs/api.md: the JavaScript lines about intervals, fast motion and time run as printed', async () => {
  const page = read('docs/api.md');
  const parts = ['Turning data on: `configure`', 'Fast motion', 'Time'];
  const lines = parts.flatMap((heading) => blocks(section(page, heading), 'js'));
  assert.equal(lines.length, 5, 'a block was added or removed: look at it, then change this number');
  const outs = [];
  for (const [i, code] of lines.entries()) {
    const connect = code.includes('Band.connect(') ? '' : 'const band = await Band.connect(42);\n';
    const program = `import { Band } from '${LIBRARY}';\n${connect}${code}`
      + 'await new Promise((done) => setTimeout(done, 300));\nawait band.disconnect();\n';
    writeFileSync(join(PROJECT, `lines${i}.mjs`), pretend(program));
    outs.push(await run(`lines${i}.mjs`, PROJECT));
  }
  const [, , fast, limits, time] = outs;
  // fast motion: n counts 0, 1, 2 ..., t has a fraction, accel only
  const samples = fast.trim().split('\n').map((line) => line.split(' ').map(Number));
  assert.ok(samples.length > 50, `${samples.length} samples`);
  samples.forEach(([n], i) => assert.equal(n, i));
  assert.ok(samples.some(([, t]) => !Number.isInteger(t)));
  // the limits as the comment in the block shows them (the pretend band imitates Bluetooth)
  assert.match(limits, /accel: 833,\s+gyro: 833,\s+both: 416,\s+transport: 'sim'/);
  assert.match(limits, /\n833\n$/);
  assert.match(page, /console\.log\(band\.streams\.motion\);\s+\/\/ 833 over Bluetooth/);
  // the clock: set from this computer on connect, so `time` is now
  const said = time.match(/\{ time: (\d+), set: true, t: \d+, source: 'band' \}\nband\n$/);
  assert.ok(said, time);
  assert.ok(Math.abs(Number(said[1]) - Date.now()) < 60000);
});

test('docs/api.md: the JavaScript lines about the pretend band run as printed, and every call in its table exists', async () => {
  const part = section(read('docs/api.md'), 'The pretend band: making things happen');
  const lines = blocks(part, 'js');
  assert.equal(lines.length, 1);
  const program = `import { Band, SimTransport } from '${LIBRARY}';\n${lines[0]}`
    + 'await new Promise((done) => setTimeout(done, 500));\nawait band.disconnect();\n';
  writeFileSync(join(PROJECT, 'pretend-lines.mjs'), program);
  const out = (await run('pretend-lines.mjs', PROJECT)).split('\n');
  assert.equal(out[0], 'tap');
  assert.ok(out.includes('loudness 48'), out.join(' '));      // the steady level 0.5
  assert.ok(out.includes('loudness 5'), out.join(' '));       // the clap
  assert.equal(out.filter((line) => line === 'tap').length, 1);   // autoTap: false: no tap of its own
  const { SimTransport } = await import('../../js/src/index.js');
  const sim = new SimTransport(42, { realtime: false });
  for (const name of ['tap', 'playMotion', 'stopMotion', 'setLoudness', 'playLoudness', 'setRssi', 'setBattery', 'setActivity', 'dropLink', 'powerCycle', 'after', 'step']) {
    assert.equal(typeof sim[name], 'function', name);
    assert.ok(part.includes(`sim.${name}(`), `docs/api.md does not show sim.${name}(`);
  }
});

test('docs: the firmware 1.1 names on the pages are the ones the library takes', async () => {
  const { Band, SimTransport } = await import('../../js/src/index.js');
  const api = read('docs/api.md');
  const agents = read('AGENTS.md');
  for (const name of ['motionLimits', 'setInterval', 'getInterval', 'setTime', 'time', 'setMotion', 'getMotion']) {
    assert.equal(typeof Band.prototype[name], 'function', name);
    assert.ok(api.includes(`${name}(`), `docs/api.md does not mention ${name}`);
  }
  for (const text of ["{ protocol: '1.0' }", '{ setClock: false }', "new SimTransport(42, { firmware: '1.0' })", 'band.clock', 'info.features']) {
    assert.ok(api.includes(text), text);
  }
  for (const text of ['motion="fastest"', 'sensors="accel"', 'motion_limits()', 'set_interval(', 'band.clock', 'protocol="1.0"']) {
    assert.ok(agents.includes(text), `AGENTS.md: ${text}`);
  }
  // the caps table on the page is what the pretend band answers, link by link
  const rows = Object.fromEntries([...section(api, 'Fast motion').matchAll(/^ *\| `"(both|accel|gyro)"` \| (\d+) \| (\d+) \|$/gm)]
    .map(([, sensors, ble, usb]) => [sensors, { ble: Number(ble), usb: Number(usb) }]));
  assert.deepEqual(Object.keys(rows), ['both', 'accel', 'gyro']);
  for (const via of ['ble', 'usb']) {
    const band = await Band.connect(42, { transport: new SimTransport(42, { via, realtime: false }) });
    const limits = await band.motionLimits();
    for (const sensors of ['both', 'accel', 'gyro']) assert.equal(limits[sensors], rows[sensors][via], `${sensors} over ${via}`);
  }
  // the interval table too
  const band = await Band.connect(42, { transport: new SimTransport(42, { realtime: false }) });
  const table = [...api.matchAll(/^\| `(activity|battery|temperature|link|micLevel)`(?: \/ `mic_level`)? \| ([\d.]+) to ([\d.]+)[^|]*\| ([^|]+) \|$/gm)];
  assert.equal(table.length, 5);
  for (const [, stream, min, max, usual] of table) {
    const now = await band.getInterval(stream);
    assert.deepEqual([now.min, now.max], [Number(min), Number(max)], stream);
    assert.equal(now.seconds, stream === 'battery' ? 0 : Number(usual), stream);
  }
  // and the two messages the page quotes are the ones the library gives
  const quoted = (text) => text.replace(/\s+/g, ' ');
  const tooFast = await band.configure({ motion: 833, sensors: 'both' }).catch((e) => e.message);
  assert.ok(quoted(api).includes(`"${tooFast}"`), tooFast);
  const old = await Band.connect(42, { transport: new SimTransport(42, { firmware: '1.0', realtime: false }) });
  const needs11 = await old.configure({ motion: 416 }).catch((e) => e.message);
  assert.ok(quoted(api).includes(`"${needs11}"`), needs11);
});

test('AGENTS.md: the web page finds the library, and what happens is written into the page', async () => {
  const text = section(read('AGENTS.md'), 'Shortest working program: web page');
  assert.match(text, /`my-project\/index\.html`:\n\n```html/);
  assert.match(text, /Serve it from `my-project\/`/);
  const [html] = blocks(text, 'html');
  assert.ok(html.split('\n').length <= 26, 'the shortest page stays short');
  const script = html.match(/<script type="module">\n([\s\S]*?)<\/script>/)[1];
  const path = script.match(/import \{ Band \} from '([^']+)';/)[1];
  assert.equal(path, LIBRARY);
  assert.ok(existsSync(join(PROJECT, path)), `${path} from my-project/`);
  assert.match(html, /<button id="go">/);
  assert.match(html, /<p id="out">Not connected\.<\/p>/);          // the place on the page where everything is said
  assert.doesNotMatch(script, /console\./, 'nothing is left in the console only');
  // Opened by double-click (file://) the module script cannot load the library and nothing
  // would happen. A plain script before it says so on the page. Run that script both ways.
  const scripts = [...html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)];
  assert.deepEqual(scripts.map((m) => m[1]), ['', ' type="module"'], 'the plain script comes first: it must run when the module cannot');
  assert.ok(html.indexOf('id="out"') < html.indexOf('<script>'), 'the place for the text exists when the plain script runs');
  const opened = (protocol, pathname) => {
    const out = { textContent: 'Not connected.' };
    new Function('location', 'document', scripts[0][2])({ protocol, pathname }, { getElementById: (id) => (id === 'out' ? out : null) });
    return out.textContent;
  };
  // the page gives the whole way out by itself (the command and the address), for the file it really is
  assert.equal(opened('file:', '/folder/my-project/index.html'),
    'This page was opened by double-click, so it cannot load the library. In this folder run:  python3 -m http.server 8000 --bind 127.0.0.1  Then open http://localhost:8000/index.html (8000, or the port you started it with)');
  assert.match(opened('file:', '/C:/work/my-project/game.html'), /Then open http:\/\/localhost:8000\/game\.html \(8000, or the port you started it with\)$/);
  assert.doesNotMatch(html, /see below/);
  assert.equal(opened('http:', '/index.html'), 'Not connected.');
  assert.match(text, / {4}python3 -m http\.server 8000 --bind 127\.0\.0\.1\n\nthen <http:\/\/localhost:8000\/index\.html>/);
  assert.match(text, /Address already in use/);
  assert.match(text, /`python3 -m http\.server 8001 --bind 127\.0\.0\.1` and `http:\/\/localhost:8001\/index\.html`/);
  assert.match(script, /pretend band: Band\.connect\(BAND, \{ transport: 'sim' \}\)/);
  // The page's own script, saved next to where index.html would be, with a stand-in for
  // the button and the paragraph. Pressing the button must connect, set the LED, say so
  // on the page, and show the tap on the page.
  const press = (file) => `
    const button = {};
    const out = { textContent: 'Not connected.' };
    const shown = [];
    globalThis.document = { getElementById: (id) => ({ go: button, out })[id] ?? null };
    await import('./${file}');
    await button.onclick();
    shown.push(out.textContent);
    await new Promise((done) => setTimeout(done, 4300));   // the pretend band taps itself after 4 seconds
    shown.push(out.textContent);
    process.stdout.write(JSON.stringify(shown));
    process.exit(0);
  `;
  writeFileSync(join(PROJECT, 'page.mjs'), pretend(script));
  writeFileSync(join(PROJECT, 'press.mjs'), press('page.mjs'));
  assert.deepEqual(JSON.parse(await run('press.mjs', PROJECT)), ['Connected to Vexo-HACK-07. Tap it.', 'tap']);
  assert.match(text, /the page says `Connecting\.\.\.`, then\s+`Connected to Vexo-HACK-07\. Tap it\.`, and `tap` when the band is tapped/);
  assert.match(script, /try \{\n\s+say\('Connecting\.\.\. pick your band in the list\.'\);\n\s+const band = await Band\.connect\(BAND\);/, 'the page answers the click at once');
  // A problem is shown on the page too. As printed the page asks for Bluetooth, and Node
  // has none: the library's own sentence must land in the paragraph, not in the console.
  writeFileSync(join(PROJECT, 'page-real.mjs'), script);
  writeFileSync(join(PROJECT, 'press-real.mjs'), press('page-real.mjs').replace('4300', '10'));
  const [problem] = JSON.parse(await run('press-real.mjs', PROJECT));
  assert.match(problem, /Web Bluetooth/);
  assert.notEqual(problem, 'Not connected.');
});

test('AGENTS.md: the JavaScript names in rule 3 are the ones the library takes', async () => {
  assert.match(read('AGENTS.md'), /\{ autoReconnect: true \}/);
  const { Band } = await import('../../js/src/index.js');
  const band = await Band.connect(42, { transport: 'sim', autoReconnect: true, reconnectSeconds: 120 });
  assert.equal(band.reconnecting, false);
  await band.disconnect();
});
