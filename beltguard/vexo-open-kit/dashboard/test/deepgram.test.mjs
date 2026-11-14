// Tests of Live transcript's one engine, Deepgram (dashboard/speech/deepgram.js, online.js,
// deepgram-frame.*, and the words in pure.js). Nothing here contacts Deepgram and no real
// key is used: the code is run first with a pretend WebSocket object, then over a real
// socket against a pretend service on this computer (deepgram-mock.mjs).
//
//   node --test dashboard/test/deepgram.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  COMPANY, looksLikeKey, SENDING_WARNING, CONSENT_LINE, footerNote, keyStatus, NO_KEY, ONLINE_PROBLEMS, transcriptStatus, EXAMPLE_NOTE,
} from '../speech/pure.js';
import { deepgramLink, DEEPGRAM_ADDRESS, withoutKey, joinText } from '../speech/deepgram.js';
import { startMock, TEST_KEY, BAD_KEY, SENTENCE } from './deepgram-mock.mjs';

const dir = new URL('../', import.meta.url);
const read = (name) => readFileSync(new URL(name, dir), 'utf8');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(condition, ms = 3000) {
  for (const end = Date.now() + ms; Date.now() < end;) {
    if (condition()) return;
    await sleep(10);
  }
  assert.fail('waited too long');
}

/** A WebSocket that goes nowhere: it keeps what was sent, and the test plays the service. */
function pretendSocket() {
  const made = [];
  class Socket {
    constructor(url, protocols) {
      this.url = url;
      this.protocols = protocols;
      this.sent = [];
      this.closed = false;
      made.push(this);
    }

    send(data) {
      this.sent.push(data);
    }

    close() {
      this.closed = true;
    }

    // what the test does as the service
    open() {
      this.onopen?.({});
    }

    say(message) {
      this.onmessage?.({ data: typeof message === 'string' ? message : JSON.stringify(message) });
    }

    drop(code = 1006, reason = '') {
      this.onclose?.({ code, reason });
    }
  }
  return { Socket, made };
}
// Every link a test starts is stopped at the end, so that no timer keeps the tests from ending.
const links = [];
test.after(() => {
  for (const link of links) link.stop();
});

/** Start a link on a pretend socket and collect everything it reports. */
function started(options = {}) {
  const { Socket, made } = pretendSocket();
  const seen = { texts: [], states: [], errors: [] };
  const link = deepgramLink({ key: TEST_KEY, WebSocket: Socket, closeWaitMs: 10, ...options });
  links.push(link);
  link.start({ onText: (t) => seen.texts.push(t), onState: (s) => seen.states.push(s), onError: (e) => seen.errors.push(e) });
  return { link, seen, made, socket: made[0] };
}
const results = (transcript, isFinal, speechFinal, more = {}) => ({
  type: 'Results', start: 1, duration: 0.5, is_final: isFinal, speech_final: speechFinal,
  channel: { alternatives: [{ transcript, words: transcript ? [{ word: 'x', start: 1.1, end: 1.4 }] : [] }] }, ...more,
});
const packet = (n) => Uint8Array.of(0x48, n, n, n);

// ---- the one engine, and the words on the page

test('Live transcript has one engine and no picker: no model, nothing to choose, no other company', () => {
  assert.equal(COMPANY, 'Deepgram');
  const html = read('index.html');
  assert.doesNotMatch(html, /speech-model|transcript-progress|<progress\b/);
  for (const name of ['index.html', 'speech/pure.js', 'speech/online.js', 'speech/deepgram.js', 'speech/deepgram-frame.js', 'speech/deepgram-frame.html', 'speech/panel.js', 'speech/speech.js', 'speech/load.js']) {
    assert.doesNotMatch(read(name), /moonshine|onnx|elevenlabs|openai|assemblyai|googleapis/i, name);
  }
});

test('the words: a warning that is always there, one line the whole time sound goes out, and a footer that is true in both states', () => {
  for (const part of ['does not stay on this computer', 'everyone near the band', 'sent over the internet to Deepgram, a speech-to-text company',
    'Tell the people around you before you switch it on', 'solid blue light means the microphone is on', 'Use your own team\'s Deepgram key',
    'A key typed here is seen by this page only', 'not stored and not shown', 'Reload or close the page', '"Forget key"',
    'Never put a key in code, a commit, a chat or a screenshot']) assert.ok(SENDING_WARNING.includes(part), part);
  assert.equal(CONSENT_LINE, 'The band\'s microphone is being sent to Deepgram now. Switch Live transcript off to stop.');
  assert.equal(footerNote(false), 'Everything on this page runs in your browser. Nothing is sent anywhere.');
  assert.equal(footerNote(true), 'Live transcript is sending the band\'s microphone to Deepgram. Everything else on this page runs in your browser.');
  const html = read('index.html');
  assert.ok(html.includes(`<footer id="footer-note">\n  ${footerNote(false)}\n</footer>`), 'the page starts with the same words');
  assert.match(html, /It is not sent anywhere either, unless you switch on Live transcript below: that sends it to Deepgram, a speech-to-text company\./);
  assert.match(html, /<p class="hint note" id="speech-warning"><\/p>/);
  assert.match(read('speech/panel.js'), /\$\('speech-warning'\)\.textContent = SENDING_WARNING;/);
  assert.match(EXAMPLE_NOTE, /Live transcript uses Deepgram, a speech service on the internet: it needs your team's key, and the sound goes to that company\./);
  assert.match(EXAMPLE_NOTE, /docs\/deepgram\.md in the kit explains the key/);
  for (const words of [SENDING_WARNING, CONSENT_LINE, footerNote(true), NO_KEY, ...Object.values(ONLINE_PROBLEMS)]) assert.doesNotMatch(words, /—/);
});

test('the status line: without a key it says kindly what is missing; with one, every stage says what is going on', () => {
  assert.equal(transcriptStatus({ state: 'off' }), 'Live transcript is off. It needs your team\'s Deepgram key: paste it into the key field below (docs/deepgram.md in the kit).');
  assert.equal(transcriptStatus({ state: 'off', hasKey: true }), 'Live transcript is off. Switching it on sends the band\'s sound to Deepgram.');
  assert.equal(transcriptStatus({ state: 'connecting' }), 'Connecting to Deepgram...');
  assert.equal(transcriptStatus({ state: 'listening', sound: false }), 'Connected to Deepgram. Waiting for sound from the band...');
  assert.equal(transcriptStatus({ state: 'listening', sound: true, lag: null }), 'Listening through Deepgram.');
  assert.equal(transcriptStatus({ state: 'listening', sound: true, lag: 0.42 }), 'Listening through Deepgram. The text is about 0.4 s behind the speech.');
  assert.equal(transcriptStatus({ state: 'listening', sound: true, lag: 0.01 }), 'Listening through Deepgram. The text is less than 0.1 s behind the speech.');
  assert.equal(transcriptStatus({ state: 'listening', sound: true, lag: 0.42, pretend: true }), 'Listening to the pretend band\'s recording through Deepgram; it repeats every 16 seconds. The text is about 0.4 s behind the speech.');
  assert.equal(transcriptStatus({ state: 'error', problem: ONLINE_PROBLEMS.refused }), `Live transcript stopped. ${ONLINE_PROBLEMS.refused}`);
  assert.equal(transcriptStatus({ state: 'error', problem: NO_KEY }), 'Live transcript stopped. Live transcript needs a Deepgram key. Paste your team\'s key into the key field first (docs/deepgram.md in the kit).');
  assert.equal(keyStatus({ hasKey: false }), 'No key yet. Paste your team\'s Deepgram key into the field above. It is not shown and not stored.');
  assert.equal(keyStatus({ hasKey: false, asked: true }), 'Live transcript needs a Deepgram key first. Paste your team\'s key into the field above; docs/deepgram.md in the kit says where it comes from. '
    + 'Everything else on this page works without one.');
  assert.match(keyStatus({ hasKey: true }), /^A Deepgram key is in this page's memory\. It is not stored: reload or close the page, or press "Forget key", and it is gone\.$/);
  assert.match(keyStatus({ refused: true }), /does not look like a key.*Nothing was kept\.$/);
});

test('switching on without a key never crashes and never switches the microphone on: the page asks for the key instead', () => {
  const panel = read('speech/panel.js');
  // the switch is usable whenever a band is connected, key or no key, decoder or no decoder
  assert.match(panel, /\$\('transcript-switch'\)\.disabled = !usable \|\| busy;/);
  const flip = panel.slice(panel.indexOf('async function flip('), panel.indexOf('$(\'listen-switch\').addEventListener'));
  assert.ok(flip.indexOf('if (!speech.hasKey) {') > 0 && flip.indexOf('if (!speech.hasKey) {') < flip.indexOf('setAudio(true)'), 'the key is checked before the microphone goes on');
  assert.match(flip, /keyAsked = !keyRefused;\n\s*\$\('transcript-switch'\)\.checked = false;\n\s*draw\(\);\n\s*\$\('speech-key'\)\.focus\(\);\n\s*return;/);
  // and if something else switches it on without a key, speech.js stops with the same kind sentence instead of connecting
  const speech = read('speech/speech.js');
  assert.match(speech, /if \(!this\.hasKey\) \{\n\s*this\.state = 'error';\n\s*this\.problem = NO_KEY;\n\s*return;\n\s*\}/);
  assert.ok(speech.indexOf('if (!this.hasKey) {') < speech.indexOf('onlineLink(this.#key)'));
});

// ---- the key

test('the key field is a password field that the browser does not fill in, complete or store', () => {
  const html = read('index.html');
  const field = /<input\b[^>]*id="speech-key"[^>]*>/.exec(html)[0];
  assert.match(field, /\btype="password"/);
  assert.match(field, /\bautocomplete="off"/);
  assert.match(field, /\bspellcheck="false"/);
  assert.doesNotMatch(field, /\b(name|value)=/);                 // nothing a form or the browser could pick up
  assert.doesNotMatch(html, /<form\b[^>]*>(?:(?!<\/form>)[\s\S])*speech-key/);     // not inside a form: nothing offers to save it
  assert.match(html, /<label for="speech-key">Deepgram key for Live transcript<\/label>/);
  assert.match(html, /<button id="speech-key-forget" class="quiet" disabled>Forget key<\/button>/);
  assert.match(html, /<p id="speech-consent" class="notice sending" role="status" aria-live="polite" hidden><\/p>/);
});

test('the key lives only in the memory of the open page: no storage, no address, no text on the page, no console', () => {
  const ours = ['app.js', 'logic.js', 'speech/panel.js', 'speech/speech.js', 'speech/online.js', 'speech/deepgram.js', 'speech/deepgram-frame.js', 'speech/pure.js'];
  for (const name of ours) {
    const text = read(name);
    if (name !== 'app.js') assert.doesNotMatch(text, /localStorage/, `${name} stores something`);     // app.js stores the LED colour only (static.test.mjs)
    if (name.startsWith('speech/')) assert.doesNotMatch(text, /sessionStorage|indexedDB|document\.cookie|caches\.|history\.(push|replace)State|location\.(hash|search|href)\s*=/, `${name} could keep or show a key`);
    if (name.startsWith('speech/')) assert.doesNotMatch(text, /console\./, `${name} writes to the console`);
  }
  const speech = read('speech/speech.js');
  assert.match(speech, /^ {2}#key = '';$/m);                                       // a private field: no other script can read it
  assert.deepEqual([...speech.matchAll(/this\.#key\b[^\n]*/g)].map((m) => m[0]), [
    "this.#key !== '';", 'this.#key = key;', "this.#key = '';", 'this.#key);',
  ]);
  assert.match(speech, /const link = onlineLink\(this\.#key\);/);       // the one place it is handed on
  const panel = read('speech/panel.js');
  // What is typed is taken out of the field at once, and never copied anywhere but setKey().
  assert.match(panel, /const typed = field\.value\.trim\(\);\n\s*field\.value = '';/);
  assert.deepEqual(panel.split('\n').filter((line) => /\btyped\b/.test(line.replace(/\/\/.*$/, ''))).map((line) => line.trim()), [
    'const typed = field.value.trim();', "if (typed === '') return;", 'keyRefused = !speech.setKey(typed);',
  ]);
  assert.deepEqual([...panel.matchAll(/speech-key'\)\.value/g)].length, 1);          // only to empty it
  assert.match(panel, /\$\('speech-key'\)\.value = '';\n\s*keyRefused = false;\n\s*keyAsked = false;\n\s*const wasOn = speech\.transcribing;\n\s*speech\.forgetKey\(\);/);
  // The only message that carries it goes to our own hidden page, same origin, with nothing else in it.
  const online = read('speech/online.js');
  const uses = online.split('\n').map((line) => line.replace(/\/\/.*$/, '').trim()).filter((line) => !line.startsWith('*') && !line.startsWith('/**') && /(?<![-'\w.])key(?![\w'])/.test(line));
  assert.deepEqual(uses, ['export function onlineLink(key) {', "mine.contentWindow.postMessage({ type: 'vexo-deepgram-start', key }, window.location.origin, [channel.port2]);"]);
  assert.match(online, /mine\.contentWindow\.postMessage\(\{ type: 'vexo-deepgram-start', key \}, window\.location\.origin, \[channel\.port2\]\);/);
});

test('what can be a key: letters, digits and . _ ~ - only', () => {
  for (const good of [TEST_KEY, 'a', 'Ab0._~-', 'f'.repeat(200)]) assert.ok(looksLikeKey(good), good);
  for (const bad of ['', ' ', 'two words', 'a,b', 'a\nb', 'a"b', 'f'.repeat(201), 'k?x=1', 'k/../x', 'k#x', undefined, null]) assert.equal(looksLikeKey(bad), false, String(bad));
});

// ---- the policies

test('the dashboard keeps its own policy; the hidden page may contact Deepgram\'s one address and nothing else', () => {
  assert.ok(read('index.html').includes(`<meta http-equiv="Content-Security-Policy" content="default-src 'self'">`));
  const frame = read('speech/deepgram-frame.html');
  const policies = [...frame.matchAll(/<meta http-equiv="Content-Security-Policy" content="([^"]*)">/g)].map((m) => m[1]);
  assert.deepEqual(policies, ["default-src 'none'; script-src 'self'; connect-src wss://api.deepgram.com/v1/listen"]);
  assert.ok(frame.indexOf('Content-Security-Policy') < frame.indexOf('<script'), 'the policy must come before the script');
  assert.match(frame, /<meta name="referrer" content="no-referrer">/);
  assert.deepEqual([...frame.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)].map((m) => [m[1].trim(), m[2]]), [['type="module" src="deepgram-frame.js"', '']]);
  assert.doesNotMatch(frame, /<(link|img|iframe|style|form|object|embed)\b/i);
  // The address in the code is the address in the policy, with the question mark and the settings after it.
  assert.ok(DEEPGRAM_ADDRESS.startsWith('wss://api.deepgram.com/v1/listen?'));
});

test('the hidden page is made only when Live transcript goes on with a key, from a fixed file, and removed again', () => {
  const online = read('speech/online.js');
  assert.deepEqual([...online.matchAll(/createElement\('([^']+)'\)/g)].map((m) => m[1]), ['iframe']);
  assert.match(online, /const FRAME = 'deepgram-frame\.html';/);
  assert.match(online, /mine\.src = HERE \+ FRAME;/);
  assert.match(online, /frame\?\.remove\(\);/);
  const made = online.split('\n').find((line) => line.includes("createElement('iframe')"));
  assert.match(made, /^\s{6,}/, 'inside start(), not when the file loads');
  // speech.js starts a link in one place: _startLink(), reached only from setTranscript(true).
  const speech = read('speech/speech.js');
  assert.equal(speech.split('onlineLink(').length - 1, 1);
  assert.equal(speech.split('this._startLink()').length - 1, 1);
  // No sound is handed over before the service has answered, or after the link is stopped.
  assert.match(speech, /if \(this\._link && this\.state === 'listening'\) \{/);
  assert.match(speech, /_stopLink\(\) \{\n\s*this\._link\?\.stop\(\);\n\s*this\._link = null;/);
  for (const name of ['app.js', 'speech/panel.js', 'speech/pure.js', 'speech/load.js']) assert.doesNotMatch(read(name), /from '\.\/(online|deepgram)[^']*'|deepgram-frame|iframe/, name);
});

// ---- what the hidden page accepts

test('the hidden page takes only the key from the dashboard: no address, no settings', async () => {
  const frame = read('speech/deepgram-frame.js');
  // one read of the start message, and it is the key
  assert.deepEqual([...frame.matchAll(/event\.data\??\.(\w+)/g)].map((m) => m[1]).sort(), ['key', 'type']);
  assert.match(frame, /deepgramLink\(\{ key: String\(event\.data\.key \?\? ''\) \}\)/);
  assert.match(frame, /if \(event\.origin !== window\.location\.origin \|\| event\.source !== window\.parent\) return;/);
  assert.match(frame, /if \(event\.data\?\.type !== 'vexo-deepgram-start' \|\| started \|\| !event\.ports\[0\]\) return;/);
  // from the port: packets, restart, stop. Nothing else is read.
  assert.deepEqual([...new Set([...frame.matchAll(/data\??\.(\w+)/g)].map((m) => m[1]))].sort(), ['key', 'opus', 'type']);
  for (const word of ['url', 'tokenUrl', 'params', 'model', 'language', 'audio', 'address', 'query', 'host']) {
    assert.doesNotMatch(frame.replace(/^\s*\/\/.*$/gm, ''), new RegExp(`\\b${word}\\b`), `the hidden page reads "${word}"`);
  }

  // Run the hidden page's script with a pretend window, and hand it everything a hostile page might try.
  const { Socket, made } = pretendSocket();
  const listeners = [];
  const posted = [];
  const parent = { postMessage: (data, origin) => posted.push({ data, origin }) };
  const page = { location: { origin: 'http://kit.test' }, parent, addEventListener: (type, fn) => listeners.push(fn) };
  const source = frame.replace(/^import .*$/m, '').replaceAll('window.', 'page.');
  new Function('page', 'deepgramLink', source)(page, (options) => {           // eslint-disable-line no-new-func
    assert.deepEqual(Object.keys(options), ['key']);
    const link = deepgramLink({ ...options, WebSocket: Socket, closeWaitMs: 10 });
    links.push(link);
    return link;
  });
  assert.deepEqual(posted, [{ data: { type: 'vexo-deepgram-ready' }, origin: 'http://kit.test' }]);
  const port = () => ({ postMessage() {}, onmessage: null });
  const hostile = {
    type: 'vexo-deepgram-start', key: TEST_KEY, url: 'wss://evil.test/x', tokenUrl: 'https://evil.test/t', address: 'wss://evil.test',
    params: { callback: 'https://evil.test/c', mip_opt_out: 'false' }, model: 'other', language: 'xx', audio: 'linear16', query: 'callback=https://evil.test',
  };
  // from another origin, or from a window that is not the dashboard: ignored
  listeners[0]({ origin: 'http://evil.test', source: parent, data: hostile, ports: [port()] });
  listeners[0]({ origin: 'http://kit.test', source: {}, data: hostile, ports: [port()] });
  listeners[0]({ origin: 'http://kit.test', source: parent, data: { ...hostile, type: 'other' }, ports: [port()] });
  listeners[0]({ origin: 'http://kit.test', source: parent, data: hostile, ports: [] });
  assert.equal(made.length, 0);
  // from the dashboard: the fixed address, whatever else the message holds
  const mine = port();
  listeners[0]({ origin: 'http://kit.test', source: parent, data: hostile, ports: [mine] });
  assert.equal(made.length, 1);
  assert.equal(made[0].url, DEEPGRAM_ADDRESS);
  assert.deepEqual(made[0].protocols, ['token', TEST_KEY]);
  // only once
  listeners[0]({ origin: 'http://kit.test', source: parent, data: { ...hostile, key: 'k-two' }, ports: [port()] });
  assert.equal(made.length, 1);
  // a port message cannot change anything either
  made[0].open();
  mine.onmessage({ data: { type: 'packet', opus: packet(1), url: 'wss://evil.test' } });
  mine.onmessage({ data: { type: 'configure', url: 'wss://evil.test' } });
  mine.onmessage({ data: null });
  assert.equal(made.length, 1);
  assert.deepEqual(made[0].sent, [packet(1)]);
});

// ---- the adapter (fixed address, nothing the service says is repeated, no extra settings)

test('the address is fixed: the band\'s Opus as it is, English, first guesses, and "do not train on this sound"', () => {
  const address = new URL(DEEPGRAM_ADDRESS);
  assert.equal(`${address.protocol}//${address.host}${address.pathname}`, 'wss://api.deepgram.com/v1/listen');
  assert.deepEqual(Object.fromEntries(address.searchParams), {
    model: 'nova-3', language: 'en', encoding: 'opus', sample_rate: '16000', channels: '1',
    interim_results: 'true', smart_format: 'true', endpointing: '100', mip_opt_out: 'true',
  });
  assert.equal(address.username + address.password + address.hash, '');
  // The only options are the key and three things for these tests. None of them is an address or a setting.
  const code = read('speech/deepgram.js');
  assert.match(code, /export function deepgramLink\(\{ key, WebSocket: Socket = globalThis\.WebSocket, keepAliveMs = KEEP_ALIVE_MS, closeWaitMs = CLOSE_WAIT_MS \} = \{\}\) \{/);
  assert.match(code, /^const ADDRESS = 'wss:\/\/api\.deepgram\.com\/v1\/listen';$/m);
  assert.match(code, /^export const DEEPGRAM_ADDRESS = `\$\{ADDRESS\}\?\$\{QUERY\}`;$/m);
  assert.doesNotMatch(code, /\b(url|params|tokenUrl|bearer|URLSearchParams)\b/);
});

test('the key goes in the protocol field, never in the address; packets go up one by one; then CloseStream', () => {
  const { link, seen, socket, made } = started({ key: TEST_KEY, url: 'wss://evil.test/x', params: { callback: 'https://evil.test' }, model: 'other' });
  assert.equal(made.length, 1);
  assert.equal(socket.url, DEEPGRAM_ADDRESS);                     // options that do not exist change nothing
  assert.deepEqual(socket.protocols, ['token', TEST_KEY]);
  assert.ok(!socket.url.includes(TEST_KEY));
  link.packet(packet(1));
  assert.deepEqual(socket.sent, [], 'nothing is sent before the service has answered');
  socket.open();
  assert.deepEqual(seen.states, ['listening']);
  link.packet(packet(1));
  link.packet(packet(2));
  link.packet(new Uint8Array(0));                                 // an empty packet is not sent
  assert.deepEqual(socket.sent, [packet(1), packet(2)]);
  link.stop();
  assert.equal(socket.sent.at(-1), '{"type":"CloseStream"}');
  link.packet(packet(3));
  assert.equal(socket.sent.length, 3, 'no sound after stop()');
  socket.say(results('Bye.', true, true));
  socket.say({ type: 'Metadata', duration: 1 });
  assert.deepEqual(seen.texts.map((t) => [t.final, t.text]), [[true, 'Bye.']]);
  assert.deepEqual(seen.states, ['listening', 'off']);
  assert.ok(socket.closed);
  assert.deepEqual(seen.errors, []);
});

test('first guesses become the live line, final pieces are joined, a pause ends the sentence', () => {
  const { seen, socket } = started();
  socket.open();
  socket.say(results('', false, false));
  socket.say(results('the quick', false, false));
  socket.say(results('The quick brown', true, false));
  socket.say(results('fox', false, false));
  socket.say(results('fox jumps.', true, true));
  socket.say(results('', true, true));
  socket.say('not json');
  socket.say({ type: 'SpeechStarted' });
  socket.say(null);
  assert.deepEqual(seen.texts, [
    { final: false, text: 'the quick', end: 1.5 },
    { final: false, text: 'The quick brown', end: 1.5 },
    { final: false, text: 'The quick brown fox', end: 1.5 },
    { final: true, text: 'The quick brown fox jumps.', end: 1.4 },         // where the last word ended
  ]);
  // a sentence that never pauses is cut, so the live line does not grow without end
  const long = 'word '.repeat(70).trim();
  socket.say(results(long, true, false));
  assert.deepEqual(seen.texts.at(-1), { final: true, text: long, end: 1.4 });
  // very long text from the service is cut, and times that are not numbers are left out
  socket.say({ type: 'Results', is_final: false, channel: { alternatives: [{ transcript: 'x'.repeat(5000) }] } });
  assert.equal(seen.texts.at(-1).text.length, 2000);
  assert.equal(seen.texts.at(-1).end, null);
  assert.equal(joinText([' a ', '', 'b']), 'a b');
});

test('KeepAlive goes out as text while no sound is sent; Finalize when the band\'s stream starts again', async () => {
  const { link, socket } = started({ keepAliveMs: 30 });
  socket.open();
  await sleep(120);
  assert.ok(socket.sent.length >= 1 && socket.sent.every((m) => m === '{"type":"KeepAlive"}'), JSON.stringify(socket.sent));
  link.restart();
  assert.equal(socket.sent.at(-1), '{"type":"Finalize"}');
  link.stop();
  const count = socket.sent.length;
  await sleep(80);
  assert.equal(socket.sent.length, count, 'nothing more after stop()');
});

test('stop() without an answer from the service still ends, with what was heard', async () => {
  const { link, seen, socket } = started({ closeWaitMs: 30 });
  socket.open();
  socket.say(results('Half a', true, false));
  link.stop();
  await sleep(80);
  assert.deepEqual(seen.texts.at(-1), { final: true, text: 'Half a', end: 1.4 });
  assert.deepEqual(seen.states, ['listening', 'off']);
  assert.ok(socket.closed);
});

test('whatever the service says when it refuses, fails or closes, the page gets one fixed word and never the key', () => {
  const SAID = 'SERVER-WORDS';
  // refused before it opened
  let run = started();
  run.socket.onerror({ message: `${SAID} ${TEST_KEY}` });
  assert.deepEqual(run.seen.errors, ['refused']);
  run = started();
  run.socket.drop(1006, `${SAID} ${TEST_KEY}`);
  assert.deepEqual(run.seen.errors, ['refused']);
  // an Error message
  run = started();
  run.socket.open();
  run.socket.say({ type: 'Error', description: `${SAID} ${TEST_KEY}`, message: SAID, variant: SAID });
  assert.deepEqual(run.seen.errors, ['reported']);
  assert.ok(run.socket.closed);
  // closed with a reason
  run = started();
  run.socket.open();
  run.socket.drop(1011, `${SAID} ${TEST_KEY}`);
  assert.deepEqual(run.seen.errors, ['closed']);
  // the browser refuses to make the socket, and its message repeats the key
  class Throws {
    constructor(url, protocols) {
      throw new SyntaxError(`The subprotocol '${protocols[1]}' is invalid.`);
    }
  }
  const seen = [];
  deepgramLink({ key: TEST_KEY, WebSocket: Throws }).start({ onError: (e) => seen.push(e) });
  assert.deepEqual(seen, ['refused']);
  // no key, no WebSocket in this browser
  deepgramLink({ key: '', WebSocket: Throws }).start({ onError: (e) => seen.push(e) });
  deepgramLink({ key: TEST_KEY, WebSocket: null }).start({ onError: (e) => seen.push(e) });
  assert.deepEqual(seen, ['refused', 'no-key', 'no-socket']);
  // text that comes back is the transcript; even there a copy of the key is taken out
  run = started();
  run.socket.open();
  run.socket.say(results(`my key is ${TEST_KEY}`, true, true));
  assert.equal(run.seen.texts[0].text, 'my key is [key]');
  assert.equal(withoutKey(`a ${TEST_KEY} b ${TEST_KEY}`, TEST_KEY), 'a [key] b [key]');
  // the sentences shown for those words are our own, and the code has no way to pass the service's text on
  const online = read('speech/online.js');
  assert.match(online, /const sentence = \(word\) => \(word === 'no-key' \? NO_KEY : ONLINE_PROBLEMS\[word\] \?\? ONLINE_PROBLEMS\.closed\);/);
  assert.match(online, /else if \(data\.type === 'error'\) close\(sentence\(data\.problem\)\);/);
  const code = read('speech/deepgram.js');
  assert.doesNotMatch(code, /\.(reason|code|description|variant|err_msg|message)\b(?!\.(type|channel|start|duration|is_final|speech_final))/);
  for (const sentence of Object.values(ONLINE_PROBLEMS)) assert.ok(sentence.length > 30 && !/\$\{/.test(sentence));
});

// ---- over a real socket, against the pretend service

/** Node's own WebSocket, sent to the pretend service instead. Only this test file can do that: the code has no option for it. */
const toMock = (mock) => class extends WebSocket {
  constructor(url, protocols) {
    super(url.replace('wss://api.deepgram.com', mock.ws), protocols);
  }
};
const realSocket = typeof WebSocket === 'function';

test('over a real socket: key in the protocol field only, the settings in the address, packets up, text back, a clean end', { skip: !realSocket }, async () => {
  const mock = await startMock();
  try {
    const seen = { texts: [], states: [], errors: [] };
    const link = deepgramLink({ key: TEST_KEY, WebSocket: toMock(mock) });
    link.start({ onText: (t) => seen.texts.push(t), onState: (s) => seen.states.push(s), onError: (e) => seen.errors.push(e) });
    await until(() => seen.states.includes('listening'));
    for (let i = 0; i < 10; i++) link.packet(packet(i));
    await until(() => seen.texts.length >= 3);
    link.stop();
    await until(() => seen.states.includes('off'));
    const [got] = mock.connections;
    assert.equal(mock.connections.length, 1);
    assert.equal(got.path, '/v1/listen');
    assert.equal(got.url, DEEPGRAM_ADDRESS.replace('wss://api.deepgram.com', ''));
    assert.equal(got.query.mip_opt_out, 'true');
    assert.equal(got.query.encoding, 'opus');
    assert.deepEqual([got.scheme, got.key], ['token', TEST_KEY]);
    assert.ok(!got.url.includes(TEST_KEY), 'the key is not in the address');
    assert.equal(got.binary.length, 10);
    assert.deepEqual([...got.binary[3]], [...packet(3)]);            // one packet, one message, unchanged
    assert.deepEqual(got.text, ['{"type":"CloseStream"}']);
    assert.deepEqual(seen.texts.filter((t) => t.final).map((t) => t.text), [SENTENCE]);
    assert.ok(seen.texts.some((t) => !t.final && t.text === 'Hello from the pretend'));
    assert.deepEqual(seen.errors, []);
  } finally {
    await mock.close();
  }
});

test('over a real socket: a wrong key and a service that fails are reported with fixed words', { skip: !realSocket }, async () => {
  const mock = await startMock({ closeReason: 'SERVER-WORDS' });
  try {
    const errors = [];
    deepgramLink({ key: BAD_KEY, WebSocket: toMock(mock) }).start({ onError: (e) => errors.push(e) });
    await until(() => errors.length === 1);
    assert.deepEqual(errors, ['refused']);
    const link = deepgramLink({ key: TEST_KEY, WebSocket: toMock(mock) });
    const states = [];
    link.start({ onError: (e) => errors.push(e), onState: (s) => states.push(s) });
    await until(() => states.includes('listening'));
    for (let i = 0; i < 20; i++) link.packet(packet(i));
    await until(() => errors.length === 2);
    assert.deepEqual(errors, ['refused', 'closed']);
  } finally {
    await mock.close();
  }
});

test('the test keys are short made-up words, and no file of Live transcript holds anything shaped like a real key', () => {
  for (const key of [TEST_KEY, BAD_KEY]) assert.ok(key.length < 8, key);
  for (const name of ['speech/deepgram.js', 'speech/deepgram-frame.js', 'speech/deepgram-frame.html', 'speech/online.js', 'speech/pure.js', 'speech/panel.js',
    'test/deepgram.test.mjs', 'test/deepgram-mock.mjs', 'test/deepgram-check.mjs']) {
    assert.doesNotMatch(read(name), /\b[0-9a-f]{32,}\b|\beyJ[\w-]{10,}/, name);
  }
});
