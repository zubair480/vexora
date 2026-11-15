// Tries Listen in a real Chrome (headless) with the pretend band, whose voice is the sample
// in dashboard/speech/. Not part of `node --test`: it needs Chrome and a web server, and
// takes about half a minute. (Live transcript has its own check: deepgram-check.mjs.)
//
// For the kit's maintainers: typed in the kit folder itself (the check expects the kit at the
// root of the server; a student serves my-project/ instead).
//   python3 -m http.server 8137 --bind 127.0.0.1          (in the kit folder, in another terminal)
//   node dashboard/test/listen-check.mjs 8137
//
// It checks that nothing is loaded before the switch is on, that Listen plays real sound,
// that it carries on after a lost link, that switching off frees everything, that every
// request stays on the local server and that the page's security policy is never broken.
// Set CHROME to the browser program if it is not in the usual macOS place.
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const port = Number(process.argv[2]);
const chrome = process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
if (!port) {
  console.error('Usage: node dashboard/test/listen-check.mjs <port of the web server>');
  process.exit(2);
}
if (!existsSync(chrome)) {
  console.error(`Chrome not found at ${chrome}. Set CHROME to the browser program.`);
  process.exit(2);
}
const origin = `http://127.0.0.1:${port}`;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---- start Chrome and talk to it
const profile = mkdtempSync(join(tmpdir(), 'listen-check-'));
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
const problems = [];        // console errors, exceptions, requests to other places, failed loads
const requests = new Map(); // request id -> { url, bytes, cached, worker }
let workers = 0;            // worker threads Chrome has started for the page
let workerSession = null;   // the newest one
let sessionId;
const sendTo = (session, method, params = {}) => new Promise((resolve, reject) => {
  const id = nextId++;
  waiting.set(id, { resolve, reject });
  socket.send(JSON.stringify({ id, method, params, sessionId: session }));
});
const send = (method, params) => sendTo(sessionId, method, params);

socket.onmessage = ({ data }) => {
  const msg = JSON.parse(data);
  if (msg.id) {
    const { resolve, reject } = waiting.get(msg.id);
    waiting.delete(msg.id);
    return msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
  }
  const p = msg.params;
  const inWorker = msg.sessionId !== sessionId;
  if (msg.method === 'Target.attachedToTarget') {
    // The speech model runs in a worker. Watch its requests and its console too.
    if (p.targetInfo.type === 'worker') {
      workers++;
      workerSession = p.sessionId;
    }
    for (const domain of ['Runtime', 'Network']) sendTo(p.sessionId, `${domain}.enable`).catch(() => {});
    sendTo(p.sessionId, 'Runtime.runIfWaitingForDebugger').catch(() => {});
  }
  if (msg.method === 'Target.detachedFromTarget') workers = Math.max(0, workers - 1);
  if (msg.method === 'Runtime.consoleAPICalled' && ['error', 'warning', 'assert'].includes(p.type)) {
    problems.push(`console.${p.type}: ${p.args.map((a) => a.value ?? a.description).join(' ')}`);
  }
  if (msg.method === 'Runtime.exceptionThrown') problems.push(`exception: ${p.exceptionDetails.exception?.description ?? p.exceptionDetails.text}`);
  if (msg.method === 'Log.entryAdded' && ['error', 'warning'].includes(p.entry.level)) problems.push(`log ${p.entry.source}: ${p.entry.text} ${p.entry.url ?? ''}`);
  if (msg.method === 'Network.requestWillBeSent') {
    requests.set(p.requestId, { url: p.request.url, bytes: 0, cached: false, worker: inWorker, status: 0 });
    if (!p.request.url.startsWith(`${origin}/`)) problems.push(`request to another place: ${p.request.url}`);
  }
  if (msg.method === 'Network.responseReceived') {
    const r = requests.get(p.requestId);
    if (r) {
      r.cached = !!p.response.fromDiskCache || p.response.status === 304;
      r.status = p.response.status;
    }
    if (p.response.status >= 400) problems.push(`HTTP ${p.response.status}: ${p.response.url}`);
  }
  if (msg.method === 'Network.loadingFinished' && requests.has(p.requestId)) requests.get(p.requestId).bytes = p.encodedDataLength;
  if (msg.method === 'Network.loadingFailed') problems.push(`load failed: ${p.errorText} ${p.blockedReason ?? ''} ${requests.get(p.requestId)?.url ?? ''}`);
};

const { targetId } = await sendTo(undefined, 'Target.createTarget', { url: 'about:blank' });
({ sessionId } = await sendTo(undefined, 'Target.attachToTarget', { targetId, flatten: true }));
for (const domain of ['Runtime', 'Log', 'Page', 'Network']) await send(`${domain}.enable`);
await send('Target.setAutoAttach', { autoAttach: true, waitForDebuggerOnStart: true, flatten: true });
const WATCH = 'window.__violations = []; document.addEventListener("securitypolicyviolation", (e) => window.__violations.push(e.violatedDirective + " " + e.blockedURI));';
let watchScript = (await send('Page.addScriptToEvaluateOnNewDocument', { source: WATCH })).identifier;

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
async function open(query) {
  await send('Page.navigate', { url: `${origin}/dashboard/${query}` });
  await until('the page to load', 'document.readyState === "complete" && !!document.getElementById("status")');
  await sleep(200);
}
const el = (id) => `document.getElementById(${JSON.stringify(id)})`;
const text = (id) => run(`${el(id)}.textContent`);
const click = (id) => run(`${el(id)}.click()`);
const checked = (id) => run(`${el(id)}.checked`);
const stats = (what) => run(`import("./speech/speech.js").then((m) => (${what})(m.stats))`);
/** Wait until this many samples have been queued for the speakers since the page opened. */
async function untilPlayed(samples, ms = 8000) {
  for (const end = Date.now() + ms; await stats('(s) => s.played') <= samples;) {
    if (Date.now() > end) throw new Error('Timed out waiting for sound to be queued for playing');
    await sleep(50);
  }
}
const speechRequests = () => [...requests.values()].filter((r) => /\/speech\/(sample-|deepgram|online)/.test(r.url) && !r.url.endsWith('/speech/online.js'));
const LED_CONTROLS = 'Array.from(document.querySelectorAll("#led-colour, #led-brightness, #led-pattern, #led-off, #identify, #swatches button"))';
let passed = 0;
const failures = [];
const report = [];
function check(what, ok, detail = '') {
  if (ok) passed++;
  else failures.push(`${what}${detail === '' ? '' : ` (got: ${JSON.stringify(detail)})`}`);
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${what}`);
}

try {
  // ---- 1. nothing is loaded or started before a switch is on
  await open('?band=42');
  check('before connecting: Listen and Live transcript are off and disabled', await run(`${el('listen-switch')}.disabled && ${el('transcript-switch')}.disabled && !${el('listen-switch')}.checked && !${el('transcript-switch')}.checked`));
  check('the page says it is an example, and that Live transcript needs a key and sends the sound to a company', /example/.test(await text('speech-note')) && /needs your team's key, and the sound goes to that company/.test(await text('speech-note')), await text('speech-note'));
  await click('btn-sim');
  await until('the pretend band to connect', `${el('status')}.textContent === "Pretend band"`);
  await until('the switches to be enabled', `!${el('listen-switch')}.disabled && !${el('transcript-switch')}.disabled`, 3000);
  await sleep(500);
  check('connected: both switches are enabled and still off', !(await checked('listen-switch')) && !(await checked('transcript-switch')));
  check('neither the sample voice nor the link to the speech service was requested yet', speechRequests().length === 0, speechRequests().map((r) => r.url));
  check('no worker and no hidden page', workers === 0 && await run('document.querySelectorAll("iframe").length') === 0, workers);
  check('Stream audio is still off', !(await checked('audio-switch')) && await text('audio-stats') === 'Audio is off.');

  // ---- 2. Listen: decoded sound is not silence, and it reaches the audio output
  await click('listen-switch');
  await untilPlayed(24000);
  check('switching Listen on switched Stream audio on', await checked('audio-switch'));
  check('the pretend LED turned blue and the LED controls are locked', await run(`${el('sim-led-dot')}.dataset.source === "mic" && ${LED_CONTROLS}.every((c) => c.disabled)`));
  const before = await stats('(s) => ({ decoded: s.decoded, energy: s.energy })');
  check('the decoder gave sound that is not silence', before.decoded > 24000 && Math.sqrt(before.energy / before.decoded) > 0.01, before);
  report.push(`decoder output: ${await stats('(s) => s.decoderRate')} samples per second, loudness (RMS) ${Math.sqrt(before.energy / before.decoded).toFixed(3)} of full scale`);
  let loudest = 0;
  for (let i = 0; i < 40; i++) {
    loudest = Math.max(loudest, await stats('(s) => (s.playLevel ? s.playLevel() : 0)'));
    await sleep(100);
  }
  check('Listen sends sound that is not silence to the audio output', loudest > 0.01, loudest);
  check('the Listen status says it is playing', /^Playing the band's microphone on this computer, about \d+(\.\d)? s behind\./.test(await text('listen-status')), await text('listen-status'));
  const play = await stats('(s) => ({ played: s.played, late: s.late, skipped: s.skippedPlay })');
  report.push(`Listen over 4 s: ${play.played} samples queued, ${play.late} short silences (queue ran dry), ${play.skipped} pieces skipped, loudest level at the output ${loudest.toFixed(3)}`);
  check('Listen alone: Live transcript is off and nothing was sent anywhere', !(await checked('transcript-switch')) && await run(`${el('speech-consent')}.hidden`) && await run('document.querySelectorAll("iframe").length') === 0);

  // ---- 3. the link drops and comes back: the stream starts again at packet 0, and the sound carries on
  await click('sim-drop');
  await until('the reconnecting status', `${el('status')}.textContent === "Reconnecting…"`, 3000);
  await until('the link to come back', `${el('status')}.textContent === "Pretend band"`, 10000);
  check('after the reconnect Listen and Stream audio are still on', await checked('listen-switch') && await checked('audio-switch'));
  const playedBefore = await stats('(s) => s.played');
  await untilPlayed(playedBefore + 16000);
  check('after the reconnect the sound carries on', true);

  // ---- 4. off: everything stops and the page is as it was
  await click('listen-switch');
  await until('Stream audio to go off again', `${el('audio-stats')}.textContent === "Audio is off."`, 3000);
  check('off: Stream audio, which the switch had turned on, is off again', !(await checked('audio-switch')));
  check('off: the LED controls work again', await run(`${LED_CONTROLS}.every((c) => !c.disabled)`) && await text('led-note') === '');
  check('off: the status line says so', await text('listen-status') === 'Listen is off.', await text('listen-status'));
  const playedAtStop = await stats('(s) => s.decoded');
  await sleep(600);
  check('off: nothing is decoded any more', await stats('(s) => s.decoded') === playedAtStop);

  // Stream audio switched off by hand takes Listen with it.
  await click('listen-switch');
  await until('Listen to play again', `${el('listen-status')}.textContent.startsWith("Playing")`, 5000);
  await click('audio-switch');
  await until('audio to stop', `${el('audio-stats')}.textContent === "Audio is off."`, 3000);
  check('switching Stream audio off switches Listen off too', !(await checked('listen-switch')) && await text('listen-status') === 'Listen is off.');
  await click('btn-disconnect');
  await until('the disconnect', `${el('status')}.textContent === "Not connected"`, 3000);
  check('Disconnect: both switches are off and disabled', !(await checked('listen-switch')) && await run(`${el('listen-switch')}.disabled && ${el('transcript-switch')}.disabled`));
  check('no Content-Security-Policy violations', (await run('window.__violations.length')) === 0, await run('window.__violations'));

  // ---- 5. a browser that cannot decode Opus: a clear message, and the rest still works
  await send('Page.removeScriptToEvaluateOnNewDocument', { identifier: watchScript });
  watchScript = (await send('Page.addScriptToEvaluateOnNewDocument', { source: `${WATCH} delete window.AudioDecoder;` })).identifier;
  await open('?band=42');
  await until('the message', `!${el('speech-problem')}.hidden`, 3000);
  check('no decoder: a clear message', (await text('speech-problem')).startsWith('This browser cannot decode the band\'s sound') && (await text('speech-problem')).endsWith('Everything else on this page works.'), await text('speech-problem'));
  await click('btn-sim');
  await until('the pretend band to connect', `${el('status')}.textContent === "Pretend band"`);
  await until('motion numbers', `/^-?\\d+\\.\\d\\d$/.test(${el('az')}.textContent)`);
  check('no decoder: Listen stays disabled; Stream audio and Live transcript (which needs no decoder) do not', await run(`${el('listen-switch')}.disabled && !${el('transcript-switch')}.disabled && !${el('audio-switch')}.disabled`));
  await click('audio-switch');
  await until('audio numbers', `/^\\d+ frames\\/s/.test(${el('audio-stats')}.textContent)`, 4000);
  check('no decoder: the audio stream is still counted', true);

  const all = [...requests.values()];
  check('every request went to the local server', all.length > 0 && all.every((r) => r.url.startsWith(`${origin}/`)), all.filter((r) => !r.url.startsWith(`${origin}/`)).map((r) => r.url));
  check('no console errors, warnings, exceptions or failed loads', problems.length === 0, problems);
} catch (e) {
  failures.push(e.message);
  console.log(`FAIL ${e.message}`);
  if (problems.length) console.log(problems.join('\n'));
} finally {
  socket.close();
  browser.kill();
  await sleep(300);
  rmSync(profile, { recursive: true, force: true });
}
console.log(`\n${report.join('\n')}`);
console.log(`\n${passed} checks passed, ${failures.length} failed`);
for (const failure of failures) console.log(`  failed: ${failure}`);
process.exit(failures.length ? 1 : 0);
