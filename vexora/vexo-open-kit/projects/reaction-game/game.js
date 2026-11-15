// Reaction game: tap to start, wait while the light is red, tap when it turns green.
// Kit features: tap events, the LED and its patterns, storage (the best time lives on
// the band), the real time on every event, auto-reconnect.
import { $, say, attempt, onConnect } from '../shared/page.js';

// The numbers to tune. They are guesses that work on the pretend band: nobody has played
// this on a real band yet.
const BEST_BOX = 310;            // the storage box (0..999) that holds the best time. Anyone can read or change it.
const WAIT_MS = [1500, 4000];    // the light turns green after a random time between these two
const SAME_TAP_MS = 400;         // two tap events closer together than this are one hit
const SLOWEST_MS = 60000;        // a best time above this is not a reaction time: somebody wrote it by hand

let band = null;
let state = 'off';               // 'off' -> 'ready' -> 'wait' -> 'go' -> 'result' -> 'ready' ...  ('paused' while the link is down)
let timer = null;                // the timeout that is running, if any
let greenAt = 0;                 // real time (ms) at which the light turned green
let best = null;                 // best time in ms, or null

const show = (text) => { $('#status').textContent = text; };

function ready(text = 'Tap the band to start') {
  clearTimeout(timer);
  state = 'ready';
  show(text);
  attempt(band.setLed('#0060ff', { pattern: 'breathe', period: 2000 }));
}

function startRound() {
  state = 'wait';
  show('Wait for green...');
  attempt(band.setLed('#ff0000'));
  const delay = WAIT_MS[0] + Math.random() * (WAIT_MS[1] - WAIT_MS[0]);
  timer = setTimeout(async () => {
    await attempt(band.setLed('#00ff00'));          // the answer comes back once the band has done it
    if (state !== 'wait') return;                   // the round was stopped while we waited for the band
    greenAt = Date.now();
    state = 'go';
    show('TAP!');
  }, delay);
}

function tooEarly() {
  clearTimeout(timer);
  state = 'result';
  show('Too early!');
  attempt(band.setLed('#ff00aa', { pattern: 'flash', period: 300 }));
  timer = setTimeout(ready, 1500);
}

function hit(tap) {
  if (tap.time < greenAt) { tooEarly(); return; }      // felt before green, delivered after it
  // tap.time is when the band felt the tap, in ms since 1970, like Date.now().
  const ms = Math.max(1, Math.round(tap.time - greenAt));
  state = 'result';
  const item = document.createElement('li');
  item.textContent = `${ms} ms`;
  $('#rounds').prepend(item);
  while ($('#rounds').children.length > 5) $('#rounds').lastChild.remove();
  if (best === null || ms < best) {
    best = ms;
    $('#best').textContent = `${best} ms`;
    show(`${ms} ms - new best!`);
    attempt(band.setLed('#ffffff', { pattern: 'rainbow', period: 600 }));
    attempt(band.storage.put(BEST_BOX, String(best)));     // stays on the band when it is switched off
  } else {
    show(`${ms} ms`);
    attempt(band.setLed('#ffffff', { pattern: 'flash', period: 400 }));
  }
  timer = setTimeout(ready, 2000);
}

let lastTap = -Infinity;         // real time (ms) of the tap before this one

function onTap(tap) {
  // A real double tap can arrive as a tap and then a double-tap. Count it once. We compare
  // tap.time (real time), not tap.t: the band's own counter starts again when it restarts.
  if (!Number.isFinite(tap.time)) return;           // a tap without a usable time cannot be measured
  if (Math.abs(tap.time - lastTap) < SAME_TAP_MS) return;
  lastTap = tap.time;
  if (state === 'ready') startRound();
  else if (state === 'wait') tooEarly();
  else if (state === 'go') hit(tap);
  // in 'result' (showing a result) and 'paused' (link down) a tap does nothing
}

/**
 * The best time saved on the band, or null. Whoever connected to this band before you can
 * have written anything into the box, so only a plain whole number of milliseconds counts.
 */
async function readBest() {
  let text = null;
  try {
    text = await band.storage.getText(BEST_BOX);    // null when the box is empty
  } catch (e) {
    if (e.code) throw e;                            // a problem with the band: show it. Anything else: not text
  }
  const ms = /^[0-9]{1,5}$/.test(text ?? '') ? Number(text) : 0;
  return ms >= 1 && ms <= SLOWEST_MS ? ms : null;
}

onConnect(async (theBand, pretend) => {
  band = theBand;
  lastTap = -Infinity;
  // A double tap is still somebody hitting the band, so both count.
  band.on('tap', onTap);
  band.on('double-tap', onTap);

  // When the link drops in the middle of a round, stop the round. The library gets the
  // link back by itself (page.js connects with autoReconnect) and we start fresh.
  band.on('reconnecting', () => { clearTimeout(timer); state = 'paused'; show('Link lost...'); });
  band.on('reconnected', () => ready());
  band.on('disconnect', () => {
    clearTimeout(timer);
    state = 'off';
    show('Not connected');
    $('#forget').disabled = true;
    $('#pretend-tap').disabled = true;
  });

  best = await readBest();
  $('#best').textContent = best ? `${best} ms` : '-';       // textContent: text from a band never goes into the page as HTML
  $('#forget').disabled = false;
  $('#forget').onclick = async () => {
    // Deleting an empty box is an error (not-found), so look first.
    if (await band.storage.get(BEST_BOX).catch(() => null)) await attempt(band.storage.delete(BEST_BOX));
    best = null;
    $('#best').textContent = '-';
  };
  if (pretend) {
    $('#pretend-tap').disabled = false;
    $('#pretend-tap').onclick = () => pretend.tap('tap');
  }

  await band.configure({ events: true });           // nothing arrives until you turn it on
  say(`Connected to ${band.name}.`);
  ready();
});
