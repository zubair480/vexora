// Shared by the pages in this folder. It looks after the boring parts: the band number
// box, the "pretend band" tick box, the Connect and Disconnect buttons, the message line.
import { SimTransport, codec } from '../../js/src/index.js';

const $ = (selector) => document.querySelector(selector);

// This file runs, so the page came from a web server: hide the "opened as a file" help.
$('#needs-server').hidden = true;

/** Show one line of text under the buttons. */
export function say(text) {
  $('#message').textContent = text;
}

/**
 * The number typed in the box (or given in the address as ?band=42). null = any band.
 * "7", "07" and the full name "Vexo-HACK-07" all mean band 7. Anything else is an error
 * with the same words as the dashboard uses; onConnect shows it on the page.
 */
export function bandNumber() {
  const text = $('#number').value.trim() || (new URLSearchParams(location.search).get('band') ?? '').trim();
  if (text === '') return null;
  const number = codec.parseBandNumber(text) ?? codec.bandNumberOf(text);
  if (number === null) throw new Error(`"${text}" is not a band number. Type the number written on your band's bag.`);
  return number;
}

/** How to reach the band: 'ble' (Bluetooth), or a pretend band when the box is ticked. */
export function transport() {
  if (!$('#pretend').checked) return 'ble';
  const sim = new SimTransport(bandNumber() ?? 42);
  sim.onLed = showLight;                 // draw the pretend band's light on the page
  $('#light-row').hidden = false;
  return sim;
}

function showLight(led) {
  const dim = led.brightness / 255;
  const [r, g, b] = [led.r, led.g, led.b].map((part) => Math.round(part * dim));
  $('#light').style.backgroundColor = `rgb(${r}, ${g}, ${b})`;
  $('#light-text').textContent = ['off', 'on', 'blinking', 'breathing', 'flashing', 'rainbow'][led.pattern];
}

/**
 * Run `start` when Connect is pressed. Browsers only allow Bluetooth to start from a
 * click, which is why there is a button. `start` must return the band.
 */
export function onConnect(start) {
  let band = null;
  let attempt = 0;                       // goes up with every Connect and every Cancel
  const idle = (text) => {
    say(text);
    $('#connect').disabled = false;
    $('#disconnect').disabled = true;
    $('#disconnect').textContent = 'Disconnect';
  };
  $('#connect').addEventListener('click', async () => {
    const mine = ++attempt;
    $('#connect').disabled = true;
    $('#disconnect').disabled = false;   // while connecting, the second button is Cancel
    $('#disconnect').textContent = 'Cancel';
    say('Connecting...');
    const slow = setTimeout(() => {
      if (mine === attempt) say(STILL_CONNECTING);
    }, STILL_CONNECTING_MS);
    try {
      const found = await start();
      if (mine !== attempt) {            // Cancel was pressed while we waited: let this band go again
        await found.disconnect();
        if (!band && !$('#connect').disabled) say(CANCELLED);
        return;
      }
      band = found;
      $('#disconnect').textContent = 'Disconnect';
      // Another program on the band's other link took a stream of this page: say so (the sentence says what to do).
      band.on('stream-taken', (e) => say(e.message));
      band.on('disconnect', () => {
        band = null;
        idle('Disconnected.');
      });
    } catch (e) {
      if (mine === attempt) idle(e.message);   // the library's messages say what to do next
    } finally {
      clearTimeout(slow);
    }
  });
  $('#disconnect').addEventListener('click', () => {
    if (band) return band.disconnect();
    attempt++;                           // Cancel: stop waiting for this try
    return idle(CANCELLED);
  });
}

const CANCELLED = 'Cancelled. Press Connect to try again.';
const STILL_CONNECTING_MS = 15000;
const STILL_CONNECTING = 'Still connecting... Your band is not in the list? Close whatever else is connected to it '
  + "(the dashboard tab, another program, a teammate's laptop), then double-tap the band. "
  + 'No list of bands on the screen? Press Cancel and try again.';
