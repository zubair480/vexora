// Opens the dashboard in a real Chrome (headless) and clicks through it with the pretend
// band. Not part of `node --test`, because it needs Chrome and a web server:
//
// For the kit's maintainers. Both commands are typed in the kit folder itself (this check
// expects the kit at the root of the server; a student serves my-project/ instead):
//
//   python3 -m http.server 8137 --bind 127.0.0.1     (in the kit folder, in another terminal)
//   node dashboard/test/browser-check.mjs 8137 [folder-for-screenshots]
//
// Set CHROME to the browser program if it is not in the usual macOS place.
// Uses only Node (22 or newer) and Chrome's own remote-control protocol; nothing to install.
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { bandName } from '../../js/src/index.js';

const port = Number(process.argv[2]);
const shots = process.argv[3] ?? null;
const chrome = process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
if (!port) {
  console.error('Usage: node dashboard/test/browser-check.mjs <port of the web server> [folder-for-screenshots]');
  process.exit(2);
}
if (!existsSync(chrome)) {
  console.error(`Chrome not found at ${chrome}. Set CHROME to the browser program.`);
  process.exit(2);
}
const origin = `http://127.0.0.1:${port}`;
const page = (query) => `${origin}/dashboard/${query}`;
const MIC_SENTENCE = 'The microphone is on, so the band shows a solid blue light and ignores LED commands. Turn off Loudness meter and Stream audio to use the LED.';
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---- start Chrome and talk to it
const profile = mkdtempSync(join(tmpdir(), 'dashboard-check-'));
const browser = spawn(chrome, ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
  '--no-first-run', '--no-default-browser-check', 'about:blank'], { stdio: 'ignore' });
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
const problems = [];        // console errors, exceptions, policy violations, requests to other places
const requests = [];
socket.onmessage = ({ data }) => {
  const msg = JSON.parse(data);
  if (msg.id) {
    const { resolve, reject } = waiting.get(msg.id);
    waiting.delete(msg.id);
    return msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
  }
  const p = msg.params;
  if (msg.method === 'Runtime.consoleAPICalled' && ['error', 'warning', 'assert'].includes(p.type)) {
    problems.push(`console.${p.type}: ${p.args.map((a) => a.value ?? a.description).join(' ')}`);
  }
  if (msg.method === 'Runtime.exceptionThrown') problems.push(`exception: ${p.exceptionDetails.exception?.description ?? p.exceptionDetails.text}`);
  if (msg.method === 'Log.entryAdded' && ['error', 'warning'].includes(p.entry.level)) problems.push(`log ${p.entry.source}: ${p.entry.text} ${p.entry.url ?? ''}`);
  if (msg.method === 'Network.requestWillBeSent') {
    requests.push(p.request.url);
    if (!p.request.url.startsWith(`${origin}/`)) problems.push(`request to another place: ${p.request.url}`);
  }
  if (msg.method === 'Network.loadingFailed') problems.push(`load failed: ${p.errorText} ${p.blockedReason ?? ''}`);
  if (msg.method === 'Network.responseReceived' && p.response.status >= 400) problems.push(`HTTP ${p.response.status}: ${p.response.url}`);
};
let sessionId;
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = nextId++;
  waiting.set(id, { resolve, reject });
  socket.send(JSON.stringify({ id, method, params, sessionId }));
});

const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
({ sessionId } = await send('Target.attachToTarget', { targetId, flatten: true }));
for (const domain of ['Runtime', 'Log', 'Page', 'Network']) await send(`${domain}.enable`);
// Runs before the page's own code: remember every Content-Security-Policy violation.
await send('Page.addScriptToEvaluateOnNewDocument', {
  source: 'window.__violations = []; document.addEventListener("securitypolicyviolation", (e) => window.__violations.push(e.violatedDirective + " " + e.blockedURI));',
});

async function run(expression) {
  const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text);
  return result.result.value;
}
async function open(query, { width = 1100, height = 900, mobile = false } = {}) {
  await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: mobile ? 2 : 1, mobile });
  await send('Page.navigate', { url: page(query) });
  await until('the page to load', 'document.readyState === "complete" && !!document.getElementById("status")');
  await sleep(150);
}
async function until(what, expression, ms = 5000) {
  const end = Date.now() + ms;
  for (;;) {
    if (await run(`Boolean(${expression})`)) return;
    if (Date.now() > end) throw new Error(`Timed out waiting for ${what}`);
    await sleep(50);
  }
}
const text = (id) => run(`document.getElementById(${JSON.stringify(id)}).textContent`);
const click = (id) => run(`document.getElementById(${JSON.stringify(id)}).click()`);
const hidden = (id) => run(`document.getElementById(${JSON.stringify(id)}).hidden`);
const LED_CONTROLS = 'Array.from(document.querySelectorAll("#led-colour, #led-brightness, #led-pattern, #led-off, #identify, #swatches button"))';
async function screenshot(name) {
  if (!shots) return;
  const { data } = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
  writeFileSync(join(shots, name), Buffer.from(data, 'base64'));
}

let passed = 0;
const failures = [];
function check(what, ok, detail = '') {
  if (ok) passed++;
  else failures.push(`${what}${detail === '' ? '' : ` (got: ${JSON.stringify(detail)})`}`);
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${what}`);
}

try {
  // ---- 1. laptop size, ?band=42, pretend band
  const name = bandName(42);
  await open('?band=42');
  check('heading shows the band name', await text('band-name') === name, await text('band-name'));
  check('status starts as "Not connected"', await text('status') === 'Not connected');
  check('LED controls are disabled before connecting', await run(`${LED_CONTROLS}.length === 12 && ${LED_CONTROLS}.every((el) => el.disabled)`));
  check('audio and loudness switches are disabled before connecting', await run('document.getElementById("audio-switch").disabled && document.getElementById("meter-switch").disabled'));
  check('pattern list comes from the library', await run('Array.from(document.getElementById("led-pattern").options).map((o) => o.value).join()') === 'solid,blink,breathe,flash,rainbow');

  // The "opened as a file" help is plain text in the page; served from a web server the page's program puts it away at once.
  await sleep(1200);      // longer than the second after which the style sheet would show it
  check('served: the "opened as a file" help is not shown', await hidden('needs-server') && await run('document.getElementById("needs-server").getBoundingClientRect().height') === 0);
  check('before connecting: the three connect steps are shown', !(await hidden('connect-steps')) && (await text('connect-steps')).includes('Double-tap your band'), await text('connect-steps'));
  check('before connecting: USB help says which port to pick, no temperature, no pretend-microphone note', !(await hidden('usb-help')) && (await text('usb-help')).includes('Vexo Open band') && await text('temperature') === '–' && await hidden('sim-mic-note'));
  check('the loudness bar and the temperature are explained on the page', (await text('mic-note')).startsWith('The bar is empty in a quiet room') && (await text('temperature-note')).includes('chip inside the band'), [await text('mic-note'), await text('temperature-note')]);

  // Steps: empty before connecting, explained, and no Cancel button while nothing is connecting.
  check('before connecting: no step count, the Steps panel says what it shows, no Cancel button', await text('steps') === '–' && await text('steps-detail') === 'Waiting for the band.'
    && (await text('steps-note')).startsWith('Steps since this page connected.') && await hidden('btn-cancel'), [await text('steps'), await text('steps-note')]);
  // A real Bluetooth attempt: in this Chrome nobody answers the list of bands. Cancel must end it.
  if (!(await run('document.getElementById("btn-ble").disabled'))) {
    await send('Runtime.evaluate', { expression: 'document.getElementById("btn-ble").click()', userGesture: true });   // Bluetooth only starts from a real click
    await sleep(300);
    if (await text('status') === 'Connecting…') {
      check('Connecting…: a Cancel button is there', !(await hidden('btn-cancel')));
      await click('btn-cancel');
      check('Cancel ends the attempt: "Not connected", the buttons work again', await text('status') === 'Not connected' && await text('message') === 'Cancelled. Press Connect to try again.'
        && await hidden('btn-cancel') && !(await run('document.getElementById("btn-sim").disabled')), [await text('status'), await text('message')]);
    } else {
      console.log(`note the browser answered the Bluetooth request at once ("${await text('message')}"), so Cancel was not tried`);
    }
  }

  await click('btn-sim');
  await until('the pretend band to connect', 'document.getElementById("status").textContent === "Pretend band"');
  check('status shows the pretend band', await text('status') === 'Pretend band');
  check('the pretend band connects without a Cancel button', await hidden('btn-cancel'));
  check('connected: the connect steps ("Double-tap your band ...") are put away', await hidden('connect-steps') && await run('document.getElementById("connect-steps").getBoundingClientRect().height') === 0);
  check('pretend band: the Microphone panel says the sound is a recording of a person reading aloud', !(await hidden('sim-mic-note')) && (await text('sim-mic-note')).includes('recording of a person reading aloud') && (await text('sim-mic-note')).includes('None of it is the sound in your room'), await text('sim-mic-note'));
  check('connected: the USB help is put away', await hidden('usb-help'));
  await until('the identify flash', 'document.getElementById("sim-led-dot").dataset.source === "identify"', 2000);
  check('pretend LED shows the identify flash after connecting', await text('sim-led-text') === 'flashing white so you can find it', await text('sim-led-text'));
  await until('motion numbers', '/^-?\\d+\\.\\d\\d$/.test(document.getElementById("az").textContent)');
  const first = await text('ax');
  await sleep(600);
  const second = await text('ax');
  check('motion values update', first !== second, [first, second]);
  check('graph has something drawn', await run(`(() => { const c = document.getElementById("graph"); const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++; return n; })()`) > 500);
  check('battery shows a number', /^\d+$/.test(await text('battery')), await text('battery'));
  check('battery detail shows volts and state', /^\d\.\d\d V, on battery$/.test(await text('battery-detail')), await text('battery-detail'));
  await until('a signal reading', '/^-\\d+$/.test(document.getElementById("rssi").textContent)', 3000);
  check('signal shows rssi and a word', await text('signal-words') === 'strong', await text('signal-words'));
  check('setup message explains the pretend band', (await text('message')).startsWith('This is a pretend band'), await text('message'));
  check('audio and the loudness meter are off by default', await text('audio-stats') === 'Audio is off.' && !(await run('document.getElementById("audio-switch").checked || document.getElementById("meter-switch").checked')));
  check('the pretend LED is not blue after connecting', await run('document.getElementById("sim-led-dot").dataset.source') !== 'mic');
  check('LED controls are enabled once connected', await run(`${LED_CONTROLS}.every((el) => !el.disabled)`));

  // A second program on the band's other link takes motion: the page says so and offers to take it back.
  check('no "Take it back" while the data flows', await hidden('taken'));
  await click('sim-take');
  await until('the page to notice that motion went elsewhere', '!document.getElementById("taken").hidden', 8000);
  check('stream taken: the page says another program on the cable took it, status stays connected',
    (await text('taken-text')).startsWith('No motion data is arriving any more: another program on the cable (USB) took this stream.')
    && (await text('taken-text')).endsWith('Close the other program, then press "Take it back".') && await text('status') === 'Pretend band', await text('taken-text'));
  await click('take-back');
  await until('the motion to come back', 'document.getElementById("taken").hidden', 3000);
  const backA = await text('ax');
  await sleep(600);
  check('Take it back: the message is gone and motion values update again', await hidden('taken') && backA !== await text('ax'), [backA, await text('ax')]);
  await click('sim-take');
  await click('sim-leave');
  await until('the page to notice that motion was stopped', '!document.getElementById("taken").hidden', 8000);
  check('the other program ended: the page says so', (await text('taken-text')).includes('took this stream and has ended since') && (await text('taken-text')).endsWith('Press "Take it back" to get the data back.'), await text('taken-text'));
  await click('take-back');
  await until('the motion to come back again', 'document.getElementById("taken").hidden', 3000);

  await click('sim-tap');
  await until('the tap to show up', 'document.querySelector("#events li span")?.textContent === "tap"', 2000);
  check('Tap button adds a tap to the events list', true);

  // ---- 2a. Loudness meter: it turns the microphone on too, so blue mic light and LED controls locked
  await click('meter-switch');
  await until('the loudness meter to start', 'document.getElementById("sim-led-dot").dataset.source === "mic"', 2000);
  await until('the loudness bar to move', '!["", "scaleX(0)", "scaleX(0.000)"].includes(document.getElementById("mic-bar").style.transform)', 2000);
  check('loudness bar is driven by mic-level', true);
  // The pretend voice swells once in about 4.4 s: the bar must go from nearly empty to well filled, not hover around the middle.
  const bars = [];
  for (let i = 0; i < 46; i++) {
    bars.push(Number(/scaleX\(([\d.]+)\)/.exec(await run('document.getElementById("mic-bar").style.transform'))?.[1]));
    await sleep(100);
  }
  check('loudness bar uses its whole length (near empty when quiet, well filled when loud)', Math.min(...bars) <= 0.15 && Math.max(...bars) >= 0.7, [Math.min(...bars), Math.max(...bars)]);
  await until('a chip temperature reading', '/^\\d+\\.\\d$/.test(document.getElementById("temperature").textContent)', 6000);
  check('chip temperature shows a number with one decimal', /^3[01]\.\d$/.test(await text('temperature')), await text('temperature'));
  // Steps: the pretend band walks, 2 steps a second, counted from 0 since connecting.
  const stepsBefore = Number(await text('steps'));
  await until('the step count to go up', `Number(document.getElementById("steps").textContent) >= ${stepsBefore} + 2`, 3000);
  check('Steps: a count since connecting that goes up, with the pace in words', stepsBefore >= 4 && stepsBefore < 100 && await text('steps-detail') === 'Walking, 110 steps a minute.', [stepsBefore, await text('steps-detail')]);
  check('Loudness meter disables every LED control', await run(`${LED_CONTROLS}.every((el) => el.disabled)`));
  check('the page explains the meter lock in one sentence', await text('led-note') === MIC_SENTENCE, await text('led-note'));
  await click('audio-switch');
  await until('audio next to the meter', '/^\\d+ frames\\/s/.test(document.getElementById("audio-stats").textContent)', 3000);
  await click('audio-switch');
  await until('audio to stop again', 'document.getElementById("audio-stats").textContent === "Audio is off."', 2000);
  check('LED stays locked while only the meter runs', await run(`${LED_CONTROLS}.every((el) => el.disabled)`) && await run('document.getElementById("sim-led-dot").dataset.source') === 'mic');
  await click('meter-switch');
  await until('the meter to stop', 'document.getElementById("sim-led-dot").dataset.source !== "mic"', 2000);
  check('LED controls come back when the meter stops', await run(`${LED_CONTROLS}.every((el) => !el.disabled)`) && await text('led-note') === '');
  await until('the loudness bar to empty', '["scaleX(0)", "scaleX(0.000)"].includes(document.getElementById("mic-bar").style.transform)', 2000);
  check('the loudness bar goes back to empty', true);

  // ---- 2. Stream audio: blue mic light, LED controls locked
  await click('audio-switch');
  await until('audio to start', 'document.getElementById("sim-led-dot").dataset.source === "mic"', 2000);
  check('pretend LED turns blue while audio streams', await run('document.getElementById("sim-led-dot").style.backgroundColor') === 'rgb(0, 0, 255)');
  check('pretend LED text names the microphone', await text('sim-led-text') === 'solid blue: the microphone is on');
  check('Stream audio disables every LED control', await run(`${LED_CONTROLS}.every((el) => el.disabled)`));
  check('the page explains why in one sentence', await text('led-note') === MIC_SENTENCE, await text('led-note'));
  await until('audio numbers', '/^\\d+ frames\\/s, \\d+ bytes\\/s$/.test(document.getElementById("audio-stats").textContent)', 3000);
  await sleep(1100);
  const stats = await text('audio-stats');
  const fps = Number(stats.split(' ')[0]);
  check('audio shows about 50 frames/s and bytes/s', fps >= 40 && fps <= 60, stats);
  await screenshot('laptop-audio-on.png');

  await click('audio-switch');
  await until('audio to stop', 'document.getElementById("sim-led-dot").dataset.source !== "mic"', 2000);
  check('LED controls come back when audio stops', await run(`${LED_CONTROLS}.every((el) => !el.disabled)`));
  check('the sentence goes away', await text('led-note') === '');
  check('audio text says off again', await text('audio-stats') === 'Audio is off.');

  // ---- 3. LED controls reach the pretend band (after the 5 s identify flash is over)
  await until('the identify flash to end', 'document.getElementById("sim-led-dot").dataset.source === "user"', 7000);
  await run('document.querySelector("#swatches button").click()');
  await until('the LED to turn red', 'document.getElementById("sim-led-dot").style.backgroundColor === "rgb(255, 0, 0)"', 2000);
  check('a swatch sets the pretend LED', await text('sim-led-text') === 'solid');
  await run('(() => { const s = document.getElementById("led-pattern"); s.value = "blink"; s.dispatchEvent(new Event("change")); })()');
  await until('the blink pattern', 'document.getElementById("sim-led-dot").dataset.pattern === "blink"', 2000);
  await run('(() => { const s = document.getElementById("led-brightness"); s.value = "50"; s.dispatchEvent(new Event("input")); })()');
  await until('half brightness', 'document.getElementById("sim-led-dot").style.backgroundColor === "rgb(128, 0, 0)"', 2000);
  check('pattern and brightness reach the pretend LED', true);
  await click('identify');
  await until('Flash my band', 'document.getElementById("sim-led-dot").dataset.source === "identify"', 2000);
  check('"Flash my band" flashes the pretend LED', true);
  await click('led-off');
  check('the last colour is the only thing stored', await run('JSON.stringify(Object.entries(localStorage))') === '[["vexo-open-led-colour","#ff0000"]]', await run('JSON.stringify(Object.entries(localStorage))'));
  check('no cookies', await run('document.cookie') === '');
  await screenshot('laptop-connected.png');

  // ---- 4. lost link: the page reconnects by itself; then disconnect while it is trying; then a plain disconnect
  await run('document.querySelector("#swatches button").click()');
  await until('the LED to turn red again', 'document.getElementById("sim-led-dot").style.backgroundColor === "rgb(128, 0, 0)"', 7000);
  await click('sim-drop');
  await until('the reconnecting status', 'document.getElementById("status").textContent === "Reconnecting…"', 2000);
  const lost = await text('message');
  check('lost link: the page says it is trying to reconnect', lost === 'Connection lost. Trying to reconnect for 2 minutes...', lost);
  check('lost link: Disconnect stays, the connect buttons and the connect steps stay hidden', !(await hidden('btn-disconnect')) && await hidden('btn-sim') && await hidden('sim-drop') && await hidden('connect-steps'));
  check('lost link: LED controls and audio switch are disabled meanwhile', await run(`${LED_CONTROLS}.every((el) => el.disabled) && document.getElementById("audio-switch").disabled`));
  await until('the link to come back', 'document.getElementById("status").textContent === "Pretend band"', 8000);
  check('reconnected: the page says so', await text('message') === 'Reconnected.', await text('message'));
  check('reconnected: LED controls are enabled again', await run(`${LED_CONTROLS}.every((el) => !el.disabled)`));
  await until('the LED colour to be back', 'document.getElementById("sim-led-dot").style.backgroundColor === "rgb(128, 0, 0)"', 2000);
  check('reconnected: the pretend LED shows the colour from before the drop', true);
  const beforeDrop = await text('ax');
  await sleep(600);
  check('reconnected: motion values update again', beforeDrop !== await text('ax'));
  await until('a signal reading after the reconnect', '/^-\\d+$/.test(document.getElementById("rssi").textContent)', 3000);

  await click('sim-drop');
  await until('the reconnecting status again', 'document.getElementById("status").textContent === "Reconnecting…"', 2000);
  await click('btn-disconnect');
  await until('the cancelled reconnect', 'document.getElementById("status").textContent === "Not connected"', 2000);
  check('Disconnect while reconnecting stops it', await text('message') === 'Disconnected.' && !(await hidden('btn-sim')) && await hidden('btn-disconnect'), await text('message'));
  await sleep(3500);
  check('and it stays disconnected when the pretend band is back', await text('status') === 'Not connected');
  await click('btn-sim');
  await until('a new connection', 'document.getElementById("status").textContent === "Pretend band"');
  check('connecting again works', true);
  await click('btn-disconnect');
  await until('the disconnect', 'document.getElementById("status").textContent === "Not connected"', 2000);
  check('Disconnect says "Disconnected."', await text('message') === 'Disconnected.', await text('message'));
  check('disconnected: the connect steps are back', !(await hidden('connect-steps')));
  check('disconnected: no stale step count', await text('steps') === '–' && await text('steps-detail') === 'Waiting for the band.', await text('steps'));
  check('disconnected: no stale temperature, no pretend-microphone note, USB help is back', await text('temperature') === '–' && await hidden('sim-mic-note') && !(await hidden('usb-help')), await text('temperature'));
  check('no Content-Security-Policy violations (laptop run)', (await run('window.__violations.length')) === 0, await run('window.__violations'));

  // ---- 4b. Motion panel: rate and sensor pickers, measured rate, lost samples (pretend band, firmware 1.1, pretend Bluetooth)
  const pick = (id, value) => run(`(() => { const s = document.getElementById(${JSON.stringify(id)}); s.value = ${JSON.stringify(String(value))}; s.dispatchEvent(new Event("change")); })()`);
  const options = (id, which) => run(`Array.from(document.getElementById(${JSON.stringify(id)}).options).filter((o) => ${which}).map((o) => o.textContent).join("|")`);
  const motionIs = (rate, sensors) => `!document.getElementById("motion-rate").disabled && document.getElementById("motion-rate").value === "${rate}" && document.getElementById("motion-sensors").value === "${sensors}"`;
  const measured = async () => Number(await text('motion-measured'));
  await open('?band=42&demo=1');
  check('motion: the pickers are disabled until the band has said what it can do', await run('document.getElementById("motion-rate").disabled && document.getElementById("motion-sensors").disabled') || await run(motionIs(50, 'both')));
  await until('the motion pickers', motionIs(50, 'both'));
  check('motion: starts at 50 per second with both sensors, as before', true);
  check('motion: rates this link carries with both sensors are enabled', await options('motion-rate', '!o.disabled') === '10|25|50|100|208|416', await options('motion-rate', '!o.disabled'));
  check('motion: 833 and 1666 are shown, disabled, with the reason', await options('motion-rate', 'o.disabled') === '833: only with one sensor on Bluetooth|1666: USB cable, acceleration only', await options('motion-rate', 'o.disabled'));
  check('motion: three sensor choices, all enabled on firmware 1.1', await options('motion-sensors', '!o.disabled') === 'Both|Acceleration only|Turning (gyro) only', await options('motion-sensors', '!o.disabled'));
  check('motion: the hint names the link and its limits', (await text('motion-hint')).startsWith('Over Bluetooth the most is 416 samples per second with both sensors, 833 with one sensor.'), await text('motion-hint'));
  check('motion: both pickers have a label', await run('["motion-rate", "motion-sensors"].every((id) => document.querySelector(`label[for="${id}"]`)?.textContent.trim().length > 0)'));
  await until('a measured rate at 50', '/^\\d+$/.test(document.getElementById("motion-measured").textContent)', 4000);
  check('motion: about 50 per second measured at the start, none lost', Math.abs(await measured() - 50) <= 3 && await text('motion-lost') === '0', [await text('motion-measured'), await text('motion-lost')]);

  await pick('motion-sensors', 'accel');
  await until('acceleration only', motionIs(50, 'accel'), 3000);
  check('motion: changing the sensors keeps the rate when it is allowed', await text('motion-note') === '', await text('motion-note'));
  check('motion: with acceleration only, 833 is enabled', await options('motion-rate', 'o.disabled') === '1666: USB cable, acceleration only', await options('motion-rate', 'o.disabled'));
  check('motion: the gyro toggle is off and disabled with acceleration only', await run('document.getElementById("gyro-toggle").disabled && !document.getElementById("gyro-toggle").checked'));
  await pick('motion-rate', 416);
  await until('416 per second', motionIs(416, 'accel'), 3000);
  await sleep(3200);
  const at416 = await measured();
  check('motion: 416 accel only measures near 416 per second', at416 >= 400 && at416 <= 420, at416);
  check('motion: 416 accel only loses no samples', await text('motion-lost') === '0', await text('motion-lost'));
  const beforeFast = await text('ax');
  await sleep(400);
  check('motion: the numbers and the graph keep moving at 416', beforeFast !== await text('ax') && await run(`(() => { const c = document.getElementById("graph"); const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++; return n; })()`) > 500);
  await screenshot('laptop-motion-416.png');

  // the link drops at 416 accel only: after the reconnect the pickers show what is really on
  await click('sim-drop');
  await until('the reconnecting status (motion)', 'document.getElementById("status").textContent === "Reconnecting…"', 2000);
  check('motion: the pickers are disabled while reconnecting', await run('document.getElementById("motion-rate").disabled && document.getElementById("motion-sensors").disabled'));
  await until('the link to come back (motion)', 'document.getElementById("status").textContent === "Pretend band"', 8000);
  await until('the pickers after the reconnect', motionIs(416, 'accel'), 3000);
  await sleep(2600);
  const back = await measured();
  check('motion: after a reconnect the pickers show 416 accel only and that is what arrives', back >= 400 && back <= 420 && await text('motion-lost') === '0', [back, await text('motion-lost')]);

  await pick('motion-rate', 833);
  await until('833 per second', motionIs(833, 'accel'), 3000);
  await pick('motion-sensors', 'both');
  await until('both sensors at the fastest allowed', motionIs(416, 'both'), 3000);
  check('motion: going back to both drops 833 to 416 and says so', await text('motion-note') === '833 samples per second is too fast with both sensors on Bluetooth. Now 416, the fastest allowed.', await text('motion-note'));
  check('motion: 833 with both sensors is disabled over the pretend Bluetooth link', await run('Array.from(document.getElementById("motion-rate").options).find((o) => o.value === "833").disabled'));
  check('motion: the gyro toggle is enabled again with both sensors', !(await run('document.getElementById("gyro-toggle").disabled')));
  // a refusal from the library is shown in its own words (asking past the disabled option, as a stale page could)
  await run('(() => { const s = document.getElementById("motion-rate"); const o = Array.from(s.options).find((x) => x.value === "833"); o.disabled = false; s.value = "833"; s.dispatchEvent(new Event("change")); })()');
  await until('the refusal', 'document.getElementById("motion-note").textContent.startsWith("833 samples per second with both sensors is too much for Bluetooth here")', 3000);
  check('motion: a refused rate shows the library\'s message and the picker goes back to what is on', await run(motionIs(416, 'both')), await text('motion-note'));
  await pick('motion-sensors', 'gyro');
  await until('turning only', motionIs(416, 'gyro'), 3000);
  check('motion: with turning only the graph shows turning and the toggle follows', await run('document.getElementById("gyro-toggle").disabled && document.getElementById("gyro-toggle").checked') && await text('motion-unit') === 'degrees/second', await text('motion-unit'));
  check('no Content-Security-Policy violations (motion run)', (await run('window.__violations.length')) === 0, await run('window.__violations'));

  // ---- 4c. a pretend band with firmware 1.0 (?firmware=1.0): four rates, both sensors only
  await open('?band=42&demo=1&firmware=1.0');
  await until('the motion pickers on firmware 1.0', motionIs(50, 'both'));
  check('firmware 1.0: exactly four rates, all enabled', await options('motion-rate', 'true') === '10|25|50|100' && await options('motion-rate', 'o.disabled') === '', await options('motion-rate', 'true'));
  check('firmware 1.0: only Both can be picked', await options('motion-sensors', '!o.disabled') === 'Both' && await options('motion-sensors', 'o.disabled') === 'Acceleration only|Turning (gyro) only', await options('motion-sensors', '!o.disabled'));
  check('firmware 1.0: one line says fast rates need firmware 1.1', await text('motion-hint') === 'This band has firmware 1.0: 10, 25, 50, 100 samples per second with both sensors. Faster rates and one sensor alone need band firmware 1.1.', await text('motion-hint'));
  await until('a measured rate on firmware 1.0', '/^\\d+$/.test(document.getElementById("motion-measured").textContent)', 4000);
  check('firmware 1.0: about 50 per second measured; lost samples are not reported', Math.abs(await measured() - 50) <= 3 && await text('motion-lost') === 'not reported by firmware 1.0', [await text('motion-measured'), await text('motion-lost')]);
  await pick('motion-rate', 100);
  await until('100 per second on firmware 1.0', motionIs(100, 'both'), 3000);
  await sleep(2600);
  check('firmware 1.0: 100 per second can be picked and arrives', Math.abs(await measured() - 100) <= 5, await measured());
  check('firmware 1.0: the gyro toggle still works', !(await run('document.getElementById("gyro-toggle").disabled')));
  check('firmware 1.0: set up without a problem', (await text('message')).startsWith('This is a pretend band') && await run('document.getElementById("message").dataset.kind') === 'info', await text('message'));
  await until('a chip temperature reading on firmware 1.0', '/^\\d+\\.\\d$/.test(document.getElementById("temperature").textContent)', 7000);
  check('firmware 1.0: the chip temperature is shown too', true);
  check('firmware 1.0: steps are counted too', Number(await text('steps')) >= 2 && await text('steps-detail') === 'Walking, 110 steps a minute.', await text('steps'));

  // ---- 4d. the fastest the pretend band can go: 1666 accel only on a pretend USB cable (?link=usb). Does the page keep up?
  await send('Performance.enable');
  const cpu = async () => Object.fromEntries((await send('Performance.getMetrics')).metrics.map((m) => [m.name, m.value]));
  await open('?band=42&demo=1&link=usb');
  await until('the motion pickers on the pretend cable', motionIs(50, 'both'));
  check('usb: set up without a problem (no signal stream asked for)', (await text('message')).startsWith('This is a pretend band') && await run('document.getElementById("message").dataset.kind') === 'info', await text('message'));
  check('usb: 1666 is shown disabled with both sensors, with the reason', await options('motion-rate', 'o.disabled') === '1666: acceleration only on the USB cable', await options('motion-rate', 'o.disabled'));
  await pick('motion-sensors', 'accel');
  await until('acceleration only on the cable', motionIs(50, 'accel'), 3000);
  await pick('motion-rate', 1666);
  await until('1666 per second', motionIs(1666, 'accel'), 3000);
  await sleep(1500);
  // count the screen refreshes the page gets for 5 seconds while 1666 samples a second come in
  const before = await cpu();
  await run('new Promise((done) => { const gaps = []; let last = 0; const t0 = performance.now(); const tick = (now) => { if (last) gaps.push(now - last); last = now; if (now - t0 < 5000) requestAnimationFrame(tick); else { window.__frames = gaps; done(); } }; requestAnimationFrame(tick); })');
  const after = await cpu();
  const gaps = await run('window.__frames');
  const frameRate = gaps.length / 5;
  const sorted = [...gaps].sort((a, b) => a - b);
  const worst = sorted.at(-1);
  const p95 = sorted[Math.floor(sorted.length * 0.95)];
  const slow = gaps.filter((g) => g > 34).length;
  const busy = (after.TaskDuration - before.TaskDuration) / (after.Timestamp - before.Timestamp);
  const script = (after.ScriptDuration - before.ScriptDuration) / (after.Timestamp - before.Timestamp);
  const fastest = await measured();
  const lostFast = await text('motion-lost');
  console.log(`     1666 accel only for 5 s: ${frameRate.toFixed(1)} frames/s, frame time median ${sorted[sorted.length >> 1].toFixed(1)} ms, 95% ${p95.toFixed(1)} ms, worst ${worst.toFixed(1)} ms, ${slow} frames over 34 ms; `
    + `main thread busy ${(busy * 100).toFixed(1)}% (script ${(script * 100).toFixed(1)}%); measured ${fastest} samples/s, lost ${lostFast}`);
  check('1666: measured rate is near 1666 per second (the pretend chip runs 0.8% slow)', fastest >= 1620 && fastest <= 1670, fastest);
  check('1666: no samples lost', lostFast === '0', lostFast);
  check('1666: the page still draws at least 30 frames a second', frameRate >= 30, frameRate);
  check('1666: the main thread is busy less than half the time', busy < 0.5, busy);
  check('1666: the graph is drawn', await run(`(() => { const c = document.getElementById("graph"); const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++; return n; })()`) > 500);
  await screenshot('laptop-motion-1666.png');
  check('no Content-Security-Policy violations (fast motion run)', (await run('window.__violations.length')) === 0, await run('window.__violations'));

  // ---- 5. no band in the address
  await open('');
  check('no band: the "Which band?" form shows and Connect is hidden', !(await hidden('pick')) && await hidden('connect'));
  await run('(() => { document.getElementById("pick-number").value = "42"; document.getElementById("pick-form").requestSubmit(); })()');
  await until('the form to open the band page', 'location.search === "?band=42" && !!document.getElementById("band-name")');
  await until('the page to load', 'document.readyState === "complete"');
  check('the form opens ?band=42', await text('band-name') === name);
  await open('?band=12ab');
  check('a bad band number is explained', (await text('pick-message')).includes('is not a band number') && !(await hidden('pick')), await text('pick-message'));

  // ---- 6. phone size (360 px wide), dark colours
  await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'dark' }] });
  await open('?band=42&demo=1', { width: 360, height: 740, mobile: true });
  await until('the pretend band on the phone', 'document.getElementById("status").textContent === "Pretend band"');
  await until('motion numbers', '/^-?\\d+\\.\\d\\d$/.test(document.getElementById("az").textContent)');
  check('phone: no sideways scrolling at 360 px', (await run('document.documentElement.scrollWidth')) <= 360, await run('document.documentElement.scrollWidth'));
  await click('gyro-toggle');
  await until('gyro numbers', '/^-?\\d+$/.test(document.getElementById("ax").textContent)', 2000);
  check('phone: the gyro toggle switches the graph to degrees/second', await text('motion-unit') === 'degrees/second');
  await click('audio-switch');
  await until('audio on the phone', 'document.getElementById("led-note").textContent !== ""', 2000);
  check('phone: still no sideways scrolling with the mic sentence showing', (await run('document.documentElement.scrollWidth')) <= 360);
  await sleep(300);
  await screenshot('phone-dark.png');
  check('no Content-Security-Policy violations (phone run)', (await run('window.__violations.length')) === 0);
  await send('Emulation.setEmulatedMedia', { features: [] });

  // ---- 7. an iPhone: no Web Bluetooth
  await send('Page.addScriptToEvaluateOnNewDocument', {
    source: 'Object.defineProperty(Navigator.prototype, "bluetooth", { get: () => undefined }); Object.defineProperty(Navigator.prototype, "serial", { get: () => undefined });',
  });
  await send('Emulation.setUserAgentOverride', {
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1',
  });
  await open('?band=42', { width: 390, height: 800, mobile: true });
  const notice = await text('no-ble');
  check('iPhone: a clear notice', !(await hidden('no-ble')) && notice.startsWith('iPhones and iPads cannot connect'), notice);
  check('iPhone: Connect buttons are disabled, the pretend band is not', await run('document.getElementById("btn-ble").disabled && document.getElementById("btn-serial").disabled && !document.getElementById("btn-sim").disabled'));
  await screenshot('iphone-notice.png');

  // ---- 8. opened by double-click (file://): the program cannot load, the page itself must say what to do
  {
    const before = [problems.length, requests.length];      // what goes wrong here is the point of this part
    await send('Emulation.setUserAgentOverride', { userAgent: '' });
    await send('Emulation.setDeviceMetricsOverride', { width: 1100, height: 900, deviceScaleFactor: 1, mobile: false });
    await send('Page.navigate', { url: new URL('../index.html', import.meta.url).href });
    await until('the file to load', 'location.protocol === "file:" && document.readyState === "complete" && !!document.getElementById("needs-server")');
    check('file://: the page\'s program did not start (that is why the help is needed)', await text('status') === 'Not connected' && await text('mic-note') === '' && await run('document.getElementById("led-pattern").options.length') === 0);
    await until('the help to show up', 'document.getElementById("needs-server").getBoundingClientRect().height > 20', 3000);
    const help = await run('(() => { const el = document.getElementById("needs-server"); const box = el.getBoundingClientRect(); return { text: el.textContent, top: box.top, height: box.height, width: box.width, shown: getComputedStyle(el).visibility === "visible" && getComputedStyle(el).display !== "none" && !el.hidden }; })()');
    check('file://: after a second the page says it was opened as a file and gives the server command and the address',
      help.shown && help.height > 20 && help.width > 300 && help.top < 300 && help.text.includes('opened as a file (by double-click)')
      && help.text.includes('python3 -m http.server 8000 --bind 127.0.0.1') && help.text.includes('localhost:8000/vexo-open-kit/dashboard/'), help);
    check('file://: the help is laid out by the style sheet (the page did load its own CSS)', await run('getComputedStyle(document.getElementById("needs-server")).borderRadius') === '10px');
    await screenshot('opened-as-a-file.png');
    await sleep(200);
    console.log(`     (file://: ${problems.length - before[0]} expected console/load problems ignored)`);
    problems.length = before[0];
    requests.length = before[1];
  }

  check('every request went to the local server', requests.length > 0 && requests.every((url) => url.startsWith(`${origin}/`)), requests.filter((url) => !url.startsWith(`${origin}/`)));
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
console.log(`\n${passed} checks passed, ${failures.length} failed`);
for (const failure of failures) console.log(`  failed: ${failure}`);
process.exit(failures.length ? 1 : 0);
