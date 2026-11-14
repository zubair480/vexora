// Tries Live transcript through Deepgram in a real Chrome (headless) with the pretend band.
// Not part of `node --test`: it needs Chrome. It starts its own small web server for the
// kit's files on a free port of this computer, and stops it at the end.
//
//   node dashboard/test/deepgram-check.mjs
//       against the pretend service in deepgram-mock.mjs. No key, no internet. The page's
//       code takes no address from outside, so this script's web server swaps Deepgram's
//       address for the pretend service in the two files that name it, on the way to the
//       browser. The files themselves are not changed.
//
//   DEEPGRAM_API_KEY=... node dashboard/test/deepgram-check.mjs --real voices/voices.json [--json out.json]
//       against the real service, with the kit's files exactly as they are. The key is read
//       from the environment variable DEEPGRAM_API_KEY, or from the file named in
//       DEEPGRAM_KEY_FILE; with neither, this mode is skipped. The key is typed into the
//       page's key field, and is never printed or written anywhere by this script.
//       voices.json lists Ogg Opus files of 20 ms packets and what is said in them:
//         [{ "file": "a.ogg", "text": "the words", "speechStart": 70, "speechEnd": 310 }, ...]
//       speechStart / speechEnd (optional): the packets where the speech starts and ends,
//       for the two delays. EVERY FILE IN THE LIST IS SENT TO DEEPGRAM: use only sound that
//       holds nobody's private speech.
//
// It checks that nothing leaves the computer before Live transcript is switched on, that
// the key is in no address, no storage and no text of the page, that the only other place
// contacted is Deepgram's one address with mip_opt_out=true, that the line "is being sent
// to Deepgram now" shows the whole time, and that a reload forgets the key.
// Set CHROME to the browser program if it is not in the usual macOS place.
import { spawn } from 'node:child_process';
import { appendFileSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { startMock, TEST_KEY, BAD_KEY, SENTENCE } from './deepgram-mock.mjs';
import { DEEPGRAM_ADDRESS } from '../speech/deepgram.js';
import { CONSENT_LINE, footerNote, ONLINE_PROBLEMS } from '../speech/pure.js';
import { wordErrors } from './accuracy-lib.mjs';

const args = process.argv.slice(2);
const option = (name) => {
  const at = args.indexOf(name);
  return at < 0 ? null : args.splice(at, 2)[1];
};
const voicesFile = option('--real');
const jsonOut = option('--json');
const usageFile = option('--usage');      // a file that gets one line per voice sent: "<seconds> <what>"
const real = voicesFile !== null;
const chrome = process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
if (!existsSync(chrome)) {
  console.error(`Chrome not found at ${chrome}. Set CHROME to the browser program.`);
  process.exit(2);
}
let KEY = TEST_KEY;
if (real) {
  KEY = (process.env.DEEPGRAM_API_KEY ?? '').trim();
  if (!KEY && process.env.DEEPGRAM_KEY_FILE && existsSync(process.env.DEEPGRAM_KEY_FILE)) KEY = readFileSync(process.env.DEEPGRAM_KEY_FILE, 'utf8').trim();
  if (!KEY) {
    console.log('Skipped: no key. Set DEEPGRAM_API_KEY, or DEEPGRAM_KEY_FILE to a file that holds the key.');
    process.exit(0);
  }
}
/** Text that may be printed or saved: every copy of the key is taken out first. */
const safe = (value) => JSON.parse(JSON.stringify(value ?? null).split(KEY).join('[key]'));
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const mock = real ? null : await startMock();
const THERE = 'wss://api.deepgram.com';
const swap = { on: !real, key: null };      // mock mode: hand out the two files with the pretend service's address

// ---- the kit's files, from this computer
const kit = fileURLToPath(new URL('../../', import.meta.url));
const TYPES = { html: 'text/html', js: 'text/javascript', mjs: 'text/javascript', css: 'text/css', png: 'image/png', json: 'application/json', opus: 'audio/ogg', md: 'text/plain' };
let voiceBytes = null;        // --real: what the pretend band says next
const web = createServer((request, response) => {
  let path = decodeURIComponent(new URL(request.url, 'http://kit').pathname);
  if (path.endsWith('/')) path += 'index.html';
  const file = join(kit, path);
  if (!file.startsWith(kit) || !existsSync(file)) {
    response.writeHead(404).end();
    return;
  }
  let body = readFileSync(file);
  if (swap.on && /\/speech\/(deepgram\.js|deepgram-frame\.html)$/.test(path)) body = body.toString('utf8').replaceAll(THERE, mock.ws);
  if (voiceBytes && path.endsWith('/speech/sample-speech.opus')) body = voiceBytes;
  response.writeHead(200, { 'Content-Type': TYPES[path.split('.').pop()] ?? 'application/octet-stream', 'Cache-Control': 'no-store' });
  response.end(body);
});
await new Promise((resolve) => web.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${web.address().port}`;

// ---- start Chrome and talk to it
const profile = mkdtempSync(join(tmpdir(), 'deepgram-check-'));
const browser = spawn(chrome, ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
  '--no-first-run', '--no-default-browser-check', '--autoplay-policy=no-user-gesture-required', 'about:blank'], { stdio: 'ignore' });
const portFile = join(profile, 'DevToolsActivePort');
for (let i = 0; i < 100 && !existsSync(portFile); i++) await sleep(100);
const [debugPort, debugPath] = readFileSync(portFile, 'utf8').trim().split('\n');
const socket = new WebSocket(`ws://127.0.0.1:${debugPort}${debugPath}`);
await new Promise((resolve, reject) => {
  socket.onopen = resolve;
  socket.onerror = reject;
});

let nextId = 1;
const waiting = new Map();
const problems = [];          // console errors and exceptions
const consoleLines = [];      // everything the page wrote to the console
const contacted = [];         // every address the browser asked for: pages, files, WebSockets
const sockets = [];           // one record per WebSocket: { url, protocol, status, sent, received, closed }
let sessionId;
const sendTo = (session, method, params = {}) => new Promise((resolve, reject) => {
  const id = nextId++;
  waiting.set(id, { resolve, reject });
  socket.send(JSON.stringify({ id, method, params, sessionId: session }));
});
const send = (method, params) => sendTo(sessionId, method, params);
const byRequest = new Map();
socket.onmessage = ({ data }) => {
  const msg = JSON.parse(data);
  if (msg.id) {
    const { resolve, reject } = waiting.get(msg.id);
    waiting.delete(msg.id);
    return msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
  }
  const p = msg.params;
  if (msg.method === 'Target.attachedToTarget') {
    for (const domain of ['Runtime', 'Network']) sendTo(p.sessionId, `${domain}.enable`).catch(() => {});
    sendTo(p.sessionId, 'Runtime.runIfWaitingForDebugger').catch(() => {});
  }
  if (msg.method === 'Runtime.consoleAPICalled') {
    const line = p.args.map((a) => a.value ?? a.description).join(' ');
    consoleLines.push(line);
    if (['error', 'warning', 'assert'].includes(p.type)) problems.push(`console.${p.type}: ${line}`);
  }
  if (msg.method === 'Runtime.exceptionThrown') problems.push(`exception: ${p.exceptionDetails.exception?.description ?? p.exceptionDetails.text}`);
  if (msg.method === 'Log.entryAdded') {
    consoleLines.push(`${p.entry.text} ${p.entry.url ?? ''}`);
    if (['error', 'warning'].includes(p.entry.level)) problems.push(`log ${p.entry.source}: ${p.entry.text} ${p.entry.url ?? ''}`);
  }
  if (msg.method === 'Network.requestWillBeSent') contacted.push(p.request.url);
  if (msg.method === 'Network.webSocketCreated') {
    contacted.push(p.url);
    const record = { url: p.url, protocol: null, otherHeaders: '', status: null, sent: 0, sentText: [], received: 0, closed: false };
    byRequest.set(p.requestId, record);
    sockets.push(record);
  }
  const record = byRequest.get(p?.requestId);
  if (record && msg.method === 'Network.webSocketWillSendHandshakeRequest') {
    const headers = Object.fromEntries(Object.entries(p.request.headers).map(([name, value]) => [name.toLowerCase(), value]));
    record.protocol = headers['sec-websocket-protocol'] ?? '';
    delete headers['sec-websocket-protocol'];
    record.otherHeaders = JSON.stringify(headers);
  }
  if (record && msg.method === 'Network.webSocketHandshakeResponseReceived') record.status = p.response.status;
  if (record && msg.method === 'Network.webSocketFrameSent') {
    if (p.response.opcode === 1) record.sentText.push(p.response.payloadData);
    else record.sent++;
  }
  if (record && msg.method === 'Network.webSocketFrameReceived') record.received++;
  if (record && msg.method === 'Network.webSocketClosed') record.closed = true;
  return undefined;
};

const { targetId } = await sendTo(undefined, 'Target.createTarget', { url: 'about:blank' });
({ sessionId } = await sendTo(undefined, 'Target.attachToTarget', { targetId, flatten: true }));
for (const domain of ['Runtime', 'Log', 'Page', 'Network']) await send(`${domain}.enable`);
await send('Target.setAutoAttach', { autoAttach: true, waitForDebuggerOnStart: true, flatten: true });
await send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.__violations = []; document.addEventListener("securitypolicyviolation", (e) => window.__violations.push(e.violatedDirective + " " + e.blockedURI));' });

async function run(expression) {
  const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text);
  return result.result.value;
}
async function until(what, expression, ms = 5000) {
  const end = Date.now() + ms;
  for (;;) {
    if (await run(`Boolean(${expression})`)) return;
    if (Date.now() > end) throw new Error(`Timed out waiting for ${what}`);
    await sleep(50);
  }
}
const el = (id) => `document.getElementById(${JSON.stringify(id)})`;
const text = (id) => run(`${el(id)}.textContent`);
const click = (id) => run(`${el(id)}.click()`);
const checked = (id) => run(`${el(id)}.checked`);
const hidden = (id) => run(`${el(id)}.hidden || ${el(id)}.closest("[hidden]") !== null`);
const stats = (what) => run(`import("./speech/speech.js").then((m) => (${what})(m.stats))`);
const frames = () => run('document.querySelectorAll("iframe").length');
const outside = () => contacted.filter((url) => !url.startsWith(`${origin}/`) && url !== 'about:blank');

async function open() {
  await send('Page.navigate', { url: `${origin}/dashboard/?band=42` });
  await until('the page to load', 'document.readyState === "complete" && !!document.getElementById("status")');
  await sleep(200);
  await click('btn-sim');
  await until('the pretend band to connect', `${el('status')}.textContent === "Pretend band"`);
  await until('the Live transcript switch to be enabled', `!${el('transcript-switch')}.disabled`, 3000);
}
/** Type into the key field the way a person does: real key presses, not a script that sets the value. */
async function typeKey(key, { enter = false } = {}) {
  await run(`${el('speech-key')}.focus()`);
  await send('Input.insertText', { text: key });
  if (enter) {
    for (const type of ['keyDown', 'keyUp']) await send('Input.dispatchKeyEvent', { type, key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
    await run(`${el('speech-key')}.blur()`);
  }
}
/** Where could a key be left behind? Everything a page can keep, and everything it shows. */
const traces = (key) => run(`(async () => {
  const key = ${JSON.stringify(key)};
  const found = [];
  if (location.href.includes(key)) found.push('the address of the page');
  if (document.documentElement.outerHTML.includes(key)) found.push('the text of the page');
  if (Array.from(document.querySelectorAll('input, textarea')).some((f) => f.value.includes(key))) found.push('a field');
  for (const [name, store] of [['localStorage', localStorage], ['sessionStorage', sessionStorage]]) {
    for (let i = 0; i < store.length; i++) if ((store.key(i) + store.getItem(store.key(i))).includes(key)) found.push(name);
  }
  if (document.cookie.includes(key)) found.push('a cookie');
  const stored = { local: Object.keys(localStorage), session: sessionStorage.length, cookie: document.cookie, databases: (await indexedDB.databases()).length, caches: (await caches.keys()).length };
  return { found, stored };
})()`);

let passed = 0;
const failures = [];
function check(what, ok, detail = '') {
  if (ok) passed++;
  else failures.push(`${what}${detail === '' ? '' : ` (got: ${JSON.stringify(safe(detail))})`}`);
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${what}`);
}
const report = [];

async function pretendService() {
  // ---- 1. before anything is switched on
  await open();
  check('before the switch: no request left this computer and no hidden page exists', outside().length === 0 && await frames() === 0 && mock.connections.length === 0, outside());
  check('before the switch: the foot of the page says nothing is sent', (await text('footer-note')).trim() === footerNote(false));
  check('the key field is a password field, not filled in or stored by the browser', await run(`${el('speech-key')}.type === "password" && ${el('speech-key')}.autocomplete === "off" && !${el('speech-key')}.closest("form")`));
  check('before the switch: no line says sound is being sent', await hidden('speech-consent'));

  // ---- 2. switching on without a key: a kind sentence, the microphone stays off, nothing is contacted
  await click('transcript-switch');
  await sleep(400);
  check('no key: Live transcript stays off and the page asks kindly for the key, naming docs/deepgram.md', !(await checked('transcript-switch'))
    && (await text('speech-key-status')).startsWith('Live transcript needs a Deepgram key first.') && /docs\/deepgram\.md/.test(await text('speech-key-status'))
    && /It needs your team's Deepgram key/.test(await text('transcript-status')) && await run(`document.activeElement === ${el('speech-key')}`), [await text('speech-key-status'), await text('transcript-status')]);
  if (process.env.SHOT) writeFileSync(process.env.SHOT, Buffer.from((await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })).data, 'base64'));
  check('no key: the microphone was not switched on, and nothing was contacted', !(await checked('audio-switch')) && outside().length === 0 && await frames() === 0);
  check('no key: no console error or exception', problems.length === 0, problems);
  await click('listen-switch');
  await until('Listen to play', `${el('listen-status')}.textContent.startsWith("Playing")`, 5000);
  check('no key: Listen, the loudness of the sound and the rest of the page work as before, and still nothing is contacted', await checked('audio-switch') && outside().length === 0);
  await click('listen-switch');
  await until('audio to stop', `${el('audio-stats')}.textContent === "Audio is off."`, 3000);

  // ---- 3. something that cannot be a key is refused and not kept
  await typeKey('two words', { enter: true });
  await sleep(200);
  check('"two words" is not taken as a key, and the field is emptied', /does not look like a key/.test(await text('speech-key-status')) && await run(`${el('speech-key')}.value === ""`), await text('speech-key-status'));

  // ---- 4. a key typed, then the switch
  await typeKey(TEST_KEY, { enter: true });
  await sleep(200);
  check('the key is taken out of the field at once, and the page says it is in memory only', await run(`${el('speech-key')}.value === ""`) && /in this page's memory/.test(await text('speech-key-status')), await text('speech-key-status'));
  check('a key typed but nothing switched on: still nothing contacted', outside().length === 0 && await frames() === 0 && mock.connections.length === 0);
  await click('transcript-switch');
  await until('the pretend service to be listening', `${el('transcript-status')}.dataset.state === "listening"`, 8000);
  check('switching on made one hidden page and one connection', await frames() === 1 && mock.connections.length === 1 && sockets.length === 1, [await frames(), mock.connections.length]);
  check('the line "is being sent to Deepgram now" is shown', !(await hidden('speech-consent')) && await text('speech-consent') === CONSENT_LINE, await text('speech-consent'));
  check('the foot of the page says sound is being sent', (await text('footer-note')).trim() === footerNote(true), await text('footer-note'));
  check('switching Live transcript on switched Stream audio on (blue light)', await checked('audio-switch'));
  await until('text from the pretend service', `${el('transcript-final')}.textContent.length + ${el('transcript-live')}.textContent.length > 5`, 8000);
  await sleep(1500);
  const got = mock.connections[0];
  const expected = new URL(DEEPGRAM_ADDRESS);
  check('the request went to /v1/listen with exactly the fixed settings, mip_opt_out=true among them', got.path === '/v1/listen' && got.url === `${expected.pathname}${expected.search}` && got.query.mip_opt_out === 'true', got.url);
  check('the key arrived in the protocol field only: not in the address, no cookie, no referrer', got.scheme === 'token' && got.key === TEST_KEY && !got.url.includes(TEST_KEY) && !got.cookie && !got.referer, [got.scheme, got.cookie, got.referer]);
  check('the browser\'s own record agrees: key in the protocol field, in no other header and in no address', sockets[0].protocol === `token, ${TEST_KEY}` && !sockets[0].otherHeaders.includes(TEST_KEY) && contacted.every((url) => !url.includes(TEST_KEY)), sockets[0].protocol);
  check('the band\'s packets arrive one per message, about 50 a second', got.binary.length > 40 && got.binary.every((b) => b.length > 3 && b.length < 400), got.binary.length);
  check('the live line showed first guesses', (await stats('(s) => s.texts')).some((t) => !t.final && t.text.startsWith('Hello from the')));
  const trace = await traces(TEST_KEY);
  check('while it runs the key is in no address, text, field, storage or cookie of the page', trace.found.length === 0, trace.found);
  check('nothing but the LED colour is stored', trace.stored.local.every((name) => /colou?r|led/i.test(name)) && trace.stored.session === 0 && trace.stored.cookie === '' && trace.stored.databases === 0 && trace.stored.caches === 0, trace.stored);
  check('the dashboard page itself still cannot contact the service', await run(`new Promise((resolve) => { const s = new WebSocket(${JSON.stringify(`${mock.ws}/v1/listen`)}); s.onopen = () => resolve(false); s.onerror = () => resolve(true); })`) && await run('window.__violations.length') > 0);

  // ---- 5. off: last words asked for, connection closed, hidden page gone, line gone
  await click('transcript-switch');
  await until('the hidden page to go', 'document.querySelectorAll("iframe").length === 0', 5000);
  await sleep(300);
  check('off: CloseStream was sent and the connection is closed', got.closed && got.text.at(-1) === '{"type":"CloseStream"}', got.text);
  check('off: the line and the foot of the page say nothing is sent any more', await hidden('speech-consent') && (await text('footer-note')).trim() === footerNote(false));
  const sentAtStop = got.binary.length;
  await sleep(800);
  check('off: no more sound arrives at the service', got.binary.length === sentAtStop && !(await checked('audio-switch')));

  // ---- 6. on again with the key still in memory; Forget key switches it off
  await click('transcript-switch');
  await until('listening again', `${el('transcript-status')}.dataset.state === "listening"`, 8000);
  check('on again: the key in memory is used, a second connection', mock.connections.length === 2 && mock.connections[1].key === TEST_KEY);
  await click('speech-key-forget');
  await until('the hidden page to go', 'document.querySelectorAll("iframe").length === 0', 5000);
  check('Forget key: Live transcript is off and the page says there is no key', !(await checked('transcript-switch')) && /^No key yet/.test(await text('speech-key-status')), await text('speech-key-status'));
  await click('transcript-switch');
  await sleep(400);
  check('after Forget key the switch does not connect', !(await checked('transcript-switch')) && mock.connections.length === 2);

  // ---- 7. a reload forgets the key
  await typeKey(TEST_KEY, { enter: true });
  await open();
  check('after a reload there is no key: the field is empty and the page says so', await run(`${el('speech-key')}.value === ""`) && /^No key yet/.test(await text('speech-key-status')), await text('speech-key-status'));
  await click('transcript-switch');
  await sleep(400);
  check('after a reload the switch does not connect', !(await checked('transcript-switch')) && mock.connections.length === 2 && await frames() === 0);

  // ---- 8. a wrong key, and a service that hangs up: fixed sentences, nothing the service said
  await typeKey(BAD_KEY);
  await click('transcript-switch');
  await until('the refusal', `${el('transcript-status')}.dataset.state === "error"`, 8000);
  check('a wrong key: one fixed sentence, none of the service\'s words, and not the key', await text('transcript-status') === `Live transcript stopped. ${ONLINE_PROBLEMS.refused}` && !(await run('document.body.textContent')).includes('SERVER-SECRET') && (await traces(BAD_KEY)).found.length === 0, await text('transcript-status'));
  check('a wrong key: the hidden page is gone and no line says sound is being sent', await frames() === 0 && await hidden('speech-consent'));
  check('the wrong key never appeared in an address or on the console', contacted.every((url) => !url.includes(BAD_KEY)) && consoleLines.every((line) => !line.includes(BAD_KEY) && !line.includes(TEST_KEY)));

  // ---- 9. the hidden page's own policy: with the real address in the policy it cannot reach the pretend service
  await click('transcript-switch');       // off
  swap.on = false;
  await open();
  const before = mock.connections.length;
  await typeKey(TEST_KEY);
  // (nothing is sent anywhere here: this only loads the hidden page as it really is, and asks it to try the pretend service)
  const blocked = await run(`new Promise((resolve) => {
    const frame = document.createElement('iframe');
    frame.hidden = true;
    frame.src = './speech/deepgram-frame.html';
    frame.onload = () => {
      const tried = [];
      const inside = frame.contentWindow;
      inside.document.addEventListener('securitypolicyviolation', (e) => tried.push(e.violatedDirective));
      for (const address of [${JSON.stringify(`${mock.ws}/v1/listen`)}, 'wss://api.deepgram.com/v1/other', 'wss://example.com/v1/listen']) {
        try { new inside.WebSocket(address); } catch (e) { tried.push('threw'); }
      }
      inside.fetch(${JSON.stringify(`${origin}/dashboard/style.css`)}).then(() => tried.push('fetched'), () => tried.push('fetch refused'));
      setTimeout(() => { frame.remove(); resolve(tried); }, 800);
    };
    document.body.append(frame);
  })`);
  check('the hidden page as it really is refuses every other address: another server, another path at Deepgram, even its own server', blocked.filter((t) => t === 'connect-src').length >= 3 && blocked.includes('fetch refused') && !blocked.includes('fetched') && mock.connections.length === before, blocked);
  check('no console errors or exceptions apart from the refused connections that were tried on purpose', problems.every((p) => /Content Security Policy|violates the following|WebSocket connection to|ERR_|Refused to connect/.test(p)), problems);
}

async function realService() {
  const folder = dirname(voicesFile);
  const voices = JSON.parse(readFileSync(voicesFile, 'utf8'));
  const rows = [];
  // Nothing is contacted before the switch, key typed or not.
  await open();
  await typeKey(KEY, { enter: true });
  await sleep(1500);
  check('key typed, switch off: no request left this computer', outside().length === 0 && sockets.length === 0 && await frames() === 0, outside());
  for (const voice of voices) {
    voiceBytes = readFileSync(join(folder, voice.file));
    const first = sockets.length;
    await open();                                   // a reload: the key is gone and is typed again
    check(`${voice.file}: after the reload the key is gone`, /^No key yet/.test(await text('speech-key-status')));
    await typeKey(KEY);
    await click('transcript-switch');
    await until('Deepgram to be listening', `${el('transcript-status')}.dataset.state === "listening" || ${el('transcript-status')}.dataset.state === "error"`, 15000);
    if (await run(`${el('transcript-status')}.dataset.state`) === 'error') throw new Error(`Deepgram did not connect: ${await text('transcript-status')}`);
    const total = voice.total ?? 0;
    let consentAlways = true;
    for (;;) {
      const seq = await stats('(s) => s.seq ?? 0');     // the pretend band counts its packets from 0: this is the place in the voice file
      consentAlways &&= !(await hidden('speech-consent')) && await text('speech-consent') === CONSENT_LINE;
      if (seq >= total - 5) break;
      await sleep(200);
      if (!(await checked('transcript-switch'))) throw new Error('Live transcript went off by itself');
    }
    const texts = (await stats('(s) => s.texts')).filter((t) => t.seq === null || t.seq <= total);
    const trace = await traces(KEY);
    await click('transcript-switch');
    await until('the hidden page to go', 'document.querySelectorAll("iframe").length === 0', 6000);
    voiceBytes = null;
    const heard = texts.filter((t) => t.final).map((t) => t.text).join(' ');
    const { errors, words } = wordErrors(voice.text, heard);
    const firstWord = texts.find((t) => t.text.trim() !== '');
    const lastFinal = texts.filter((t) => t.final).at(-1);
    const lags = texts.filter((t) => t.final && t.lag !== null).map((t) => t.lag);
    const row = {
      file: voice.file, voice: voice.voice, words, errors, heard,
      firstLiveWordS: firstWord && voice.speechStart !== undefined ? Number(((firstWord.seq - voice.speechStart) * 0.02).toFixed(2)) : null,
      finalAfterSpeechEndS: lastFinal && voice.speechEnd !== undefined ? Number(((lastFinal.seq - voice.speechEnd) * 0.02).toFixed(2)) : null,
      finalLagByServiceTimesS: lags.length ? Number((lags.reduce((a, b) => a + b, 0) / lags.length).toFixed(2)) : null,
      updates: texts.length, finals: texts.filter((t) => t.final).length, seconds: Number((total * 0.02).toFixed(1)),
    };
    rows.push(row);
    if (usageFile) appendFileSync(usageFile, `${row.seconds} browser ${voice.file}\n`);
    const mine = sockets.slice(first);
    check(`${voice.file}: one connection, to Deepgram's one address, answered with 101`, mine.length === 1 && mine[0].url === DEEPGRAM_ADDRESS && mine[0].status === 101, mine.map((s) => [s.url, s.status]));
    check(`${voice.file}: key in the protocol field only`, mine[0]?.protocol === `token, ${KEY}` && !mine[0].otherHeaders.includes(KEY) && !mine[0].url.includes(KEY));
    check(`${voice.file}: packets went up one per message and the line "is being sent" showed the whole time`, mine[0]?.sent > total * 0.8 && consentAlways, mine[0]?.sent);
    check(`${voice.file}: the key is in no address, text, field, storage or cookie of the page`, trace.found.length === 0 && trace.stored.session === 0 && trace.stored.cookie === '' && trace.stored.databases === 0, trace);
    check(`${voice.file}: CloseStream sent and the connection closed after switching off`, mine[0]?.sentText.at(-1) === '{"type":"CloseStream"}' && mine[0].closed, mine[0]?.sentText.slice(-1));
    console.log(`     ${errors} of ${words} words wrong; first live word ${row.firstLiveWordS} s after the speech starts; finished ${row.finalAfterSpeechEndS} s after it ends`);
  }
  // Everything the browser asked for during the whole run.
  const away = [...new Set(outside())];
  check('the whole run: the only address outside this computer is Deepgram\'s, with mip_opt_out=true', away.length === 1 && away[0] === DEEPGRAM_ADDRESS && new URL(away[0]).searchParams.get('mip_opt_out') === 'true', away);
  check('the whole run: the key is in no address the browser asked for', contacted.every((url) => !url.includes(KEY)));
  check('the whole run: the key was never written to the console', consoleLines.every((line) => !line.includes(KEY)));
  check('the whole run: no Content-Security-Policy violations, console errors or exceptions', await run('window.__violations.length') === 0 && problems.length === 0, problems);
  // With the switch off, a page that is used for a while contacts nothing.
  const mark = contacted.length;
  await open();
  await click('audio-switch');
  await click('listen-switch');
  await sleep(3000);
  check('switch off (Stream audio and Listen on): no request leaves this computer', contacted.slice(mark).every((url) => url.startsWith(`${origin}/`)) && await frames() === 0, contacted.slice(mark).filter((url) => !url.startsWith(`${origin}/`)));
  const errors = rows.reduce((sum, r) => sum + r.errors, 0);
  const words = rows.reduce((sum, r) => sum + r.words, 0);
  const middle = (list) => [...list].sort((a, b) => a - b)[list.length >> 1];
  const summary = {
    when: new Date().toISOString(), address: DEEPGRAM_ADDRESS, voices: rows.length, words, errors, wordErrorRate: Number((errors / Math.max(1, words)).toFixed(4)),
    typicalFirstLiveWordS: middle(rows.map((r) => r.firstLiveWordS).filter((v) => v !== null)), typicalFinalAfterSpeechEndS: middle(rows.map((r) => r.finalAfterSpeechEndS).filter((v) => v !== null)),
    secondsSent: Number(rows.reduce((sum, r) => sum + r.seconds, 0).toFixed(1)), addressesOutsideThisComputer: away, connections: sockets.map((s) => ({ url: s.url, status: s.status, binaryMessages: s.sent, textMessages: s.sentText, closed: s.closed })), rows,
  };
  report.push(`${errors} of ${words} words wrong (${(summary.wordErrorRate * 100).toFixed(1)}%); first live word typically ${summary.typicalFirstLiveWordS} s after the speech starts; `
    + `finished sentence typically ${summary.typicalFinalAfterSpeechEndS} s after the speech ends; ${summary.secondsSent} s of sound sent`);
  if (jsonOut) writeFileSync(jsonOut, `${JSON.stringify(safe(summary), null, 1)}\n`);
}

try {
  if (real) await realService();
  else await pretendService();
} catch (e) {
  failures.push(safe(e.message));
  console.log(`FAIL ${safe(e.message)}`);
  if (problems.length) console.log(safe(problems).join('\n'));
} finally {
  socket.close();
  browser.kill();
  await sleep(300);
  rmSync(profile, { recursive: true, force: true });
  await mock?.close();
  web.close();
}
console.log(`\n${report.join('\n')}`);
console.log(`\n${passed} checks passed, ${failures.length} failed`);
for (const failure of failures) console.log(`  failed: ${failure}`);
process.exit(failures.length ? 1 : 0);
