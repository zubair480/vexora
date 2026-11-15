// Helpers for the page tests: a small web server for the kit folder and a headless Chrome
// driven through Chrome's own remote-control protocol, the same way as
// dashboard/test/browser-check.mjs. Nothing to install: Node 22 or newer and Chrome.
// Set CHROME to the browser program if it is not in one of the usual places.
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { existsSync, mkdtempSync, readFileSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, extname, join, normalize, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

export const KIT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const ANSWER_MS = 30000;      // the longest one command to Chrome may take
export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const PLACES = [
  process.env.CHROME,
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
  '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
];
/** The browser program, or null when there is none (the page tests are then skipped). */
export const chromePath = PLACES.find((place) => place && existsSync(place)) ?? null;
/** Why the page tests cannot run here, or false when they can. Use it as `{ skip }`. */
export const skip = !chromePath ? 'Chrome was not found. Set CHROME to the browser program.'
  : typeof WebSocket === 'undefined' ? 'The page tests need Node 22 or newer.' : false;

const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.json': 'application/json' };

/** Is `path` the folder `folder` itself or something inside it? A folder next to it whose name begins the same is not. */
export function isInside(folder, path) {
  const root = normalize(folder).replace(/[\\/]+$/, '');
  const asked = normalize(path);
  return asked === root || asked.startsWith(root + sep);
}

/** Serve a folder (the kit folder unless you give another) on a free port, for this computer only. Returns { origin, close }. */
export async function serveKit(folder = KIT) {
  const server = createServer((request, response) => {
    let path = normalize(join(folder, decodeURIComponent(new URL(request.url, 'http://x').pathname)));
    if (!isInside(folder, path)) { response.writeHead(403).end(); return; }
    if (existsSync(path) && statSync(path).isDirectory()) path = join(path, 'index.html');
    if (!existsSync(path)) { response.writeHead(404).end('not found'); return; }
    response.writeHead(200, { 'content-type': TYPES[extname(path)] ?? 'application/octet-stream' });
    response.end(readFileSync(path));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { origin: `http://127.0.0.1:${server.address().port}`, close: () => server.close() };
}

/** Start headless Chrome. Returns { openPage, close }. */
export async function startChrome() {
  const profile = mkdtempSync(join(tmpdir(), 'vexo-projects-'));
  const browser = spawn(chromePath, ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', 'about:blank'], { stdio: 'ignore' });
  const portFile = join(profile, 'DevToolsActivePort');
  // Chrome writes the file in two steps (port, then path): wait until both lines are there.
  const written = () => existsSync(portFile) && /^\d+\n\/\S+/.test(readFileSync(portFile, 'utf8'));
  for (let i = 0; i < 150 && !written(); i++) await sleep(100);
  const giveUp = (why) => { browser.kill('SIGKILL'); rmSync(profile, { recursive: true, force: true, maxRetries: 5 }); throw new Error(why); };
  if (!written()) giveUp('Chrome did not start within 15 seconds.');
  const [debugPort, debugPath] = readFileSync(portFile, 'utf8').trim().split('\n');
  const socket = new WebSocket(`ws://127.0.0.1:${debugPort}${debugPath}`);
  await new Promise((resolve, reject) => {
    socket.onopen = resolve;
    socket.onerror = reject;
    setTimeout(() => reject(new Error('Chrome did not open its control connection within 15 seconds.')), ANSWER_MS / 2).unref();
  }).catch((e) => giveUp(e.message));

  let nextId = 1;
  const waiting = new Map();
  const listeners = new Map();                 // sessionId -> function(method, params)
  socket.onmessage = ({ data }) => {
    const msg = JSON.parse(data);
    if (msg.id) {
      if (!waiting.has(msg.id)) return;          // answered too late: already given up on
      const { resolve, reject } = waiting.get(msg.id);
      waiting.delete(msg.id);
      return msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
    }
    listeners.get(msg.sessionId)?.(msg.method, msg.params);
  };
  const send = (method, params = {}, sessionId = undefined) => new Promise((resolve, reject) => {
    const id = nextId++;
    // No answer is a failure, not a wait without end: a browser that hangs must not hang the tests.
    const late = setTimeout(() => { waiting.delete(id); reject(new Error(`Chrome did not answer ${method} within ${ANSWER_MS / 1000} seconds.`)); }, ANSWER_MS);
    late.unref();
    waiting.set(id, { resolve: (v) => { clearTimeout(late); resolve(v); }, reject: (e) => { clearTimeout(late); reject(e); } });
    socket.send(JSON.stringify({ id, method, params, sessionId }));
  });

  /** Open a page in a new tab and wait until it has loaded. */
  async function openPage(url) {
    const origin = new URL(url).origin;
    const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
    const problems = [];     // console errors, exceptions, policy violations, requests to other places
    listeners.set(sessionId, (method, p) => {
      if (method === 'Runtime.consoleAPICalled' && ['error', 'warning', 'assert'].includes(p.type)) {
        problems.push(`console.${p.type}: ${p.args.map((a) => a.value ?? a.description).join(' ')}`);
      }
      if (method === 'Runtime.exceptionThrown') problems.push(`exception: ${p.exceptionDetails.exception?.description ?? p.exceptionDetails.text}`);
      if (method === 'Log.entryAdded' && ['error', 'warning'].includes(p.entry.level)) problems.push(`log: ${p.entry.text} ${p.entry.url ?? ''}`);
      if (method === 'Network.requestWillBeSent' && !p.request.url.startsWith(`${origin}/`)) problems.push(`request to another place: ${p.request.url}`);
      if (method === 'Network.responseReceived' && p.response.status >= 400) problems.push(`HTTP ${p.response.status}: ${p.response.url}`);
    });
    const page = (method, params) => send(method, params, sessionId);
    for (const domain of ['Runtime', 'Log', 'Page', 'Network']) await page(`${domain}.enable`);

    /** Run JavaScript in the page and return its value. */
    async function run(expression) {
      const result = await page('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text);
      return result.result.value;
    }
    /** Wait until a JavaScript expression in the page is true. */
    async function until(what, expression, ms = 8000) {
      const end = Date.now() + ms;
      for (;;) {
        if (await run(`Boolean(${expression})`)) return;
        if (Date.now() > end) throw new Error(`Timed out waiting for ${what}. Message line: ${await run('document.querySelector("#message")?.textContent')}`);
        await sleep(40);
      }
    }
    const el = (selector) => `document.querySelector(${JSON.stringify(selector)})`;
    const api = {
      run, until, problems,
      text: (selector) => run(`${el(selector)}.textContent`),
      click: (selector) => run(`${el(selector)}.click()`),
      /** Wait until the text of an element matches a regular expression. */
      untilText: (selector, pattern, ms) => until(`${selector} to show ${pattern}`, `${pattern}.test(${el(selector)}.textContent)`, ms),
      /** Choose the pretend band and press Connect. */
      async connectPretend() {
        await until('the Connect button', `${el('#connect')} && !${el('#connect')}.disabled`);
        await api.click('#connect');
        await api.untilText('#message', /Connected to Vexo-HACK-/);
      },
      close: () => send('Target.closeTarget', { targetId }),
    };
    await page('Page.navigate', { url });
    await until('the page to load', 'document.readyState === "complete"');
    // The page's program loads the library after that; it hides the "opened as a file" help when it runs.
    await until('the page\'s program to start', '!document.querySelector("#needs-server") || document.querySelector("#needs-server").hidden');
    await sleep(100);
    return api;
  }

  return {
    openPage,
    async close() {
      try { socket.close(); } catch { /* already closed */ }
      browser.kill('SIGKILL');
      await sleep(200);
      rmSync(profile, { recursive: true, force: true, maxRetries: 5 });
    },
  };
}
