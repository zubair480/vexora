// Shared by the web pages in this folder. It looks after the parts every project needs
// and nobody wants to write twice: the band number box, the choice between Bluetooth, the
// USB cable and the pretend band, the Connect and Disconnect buttons, the message line,
// and what to do when the link drops.
//
// A project's own file then only says what to do with the band:
//
//   onConnect(async (band, pretend) => {
//     band.on('tap', ...);
//     await band.configure({ events: true });
//   });
//
// `pretend` is the kit's pretend band (a SimTransport) when that was chosen, otherwise
// null. Direct it with the kit's own calls: pretend.tap('tap'), pretend.playMotion(...),
// pretend.setLoudness(0.5), pretend.dropLink(2). The list is "The pretend band" in docs/api.md.
import { Band, SimTransport, codec } from './kit.js';

export const $ = (selector) => document.querySelector(selector);

// This file runs, so the page came from a web server: hide the "opened as a file" help.
$('#needs-server').hidden = true;

/** Show one line of text under the buttons. */
export function say(text) {
  $('#message').textContent = text;
}

/**
 * Run a band command from a handler, where nobody waits for the answer. While the library
 * is getting a dropped link back every command fails with `not-connected`; that is normal
 * and stays quiet here. Any other problem is shown on the message line.
 *   attempt(band.setLed('#00ff00'));
 */
export function attempt(promise) {
  return promise.catch((e) => {
    if (e.code !== 'not-connected') say(e.message);
  });
}

/**
 * The band number: typed in the box, or given in the address as ?band=7. null = none given.
 * "7", "07" and the full name "Vexo-HACK-07" all mean band 7, as on the kit's example
 * pages. Anything else is an error with one sentence that says what to type; onConnect
 * shows it on the message line. Band numbers are 1 to 999.
 */
export function bandNumber() {
  const text = $('#number').value.trim();
  if (text === '') return null;
  const prefix = codec.NAME_PREFIX;
  const name = text.toLowerCase().startsWith(prefix.toLowerCase()) ? prefix + text.slice(prefix.length) : text;
  const number = codec.parseBandNumber(text) ?? codec.bandNumberOf(name);
  if (number === null) throw new Error(`"${text}" is not a band number. Type just the number on your band or its bag, for example 42.`);
  if (number === 0) throw new Error('Band numbers are 1 to 999. 0 is what a band has before it gets a number: ask an organiser to number it.');
  return number;
}

// The address can fill the form in: ?band=7 and ?link=pretend (or usb). With the pretend
// band, &firmware=1.0 gives one with the older firmware, to see what your page does then.
const query = new URLSearchParams(location.search);
if (query.get('band')) $('#number').value = query.get('band');
if (query.get('link')) $('#link').value = query.get('link') === 'usb' ? 'serial' : query.get('link');
if (!$('#link').value) $('#link').value = 'ble';      // an unknown ?link= value: back to Bluetooth

function showPretendParts() {
  const on = $('#link').value === 'pretend';
  for (const part of document.querySelectorAll('.pretend-only')) part.hidden = !on;
}
$('#link').addEventListener('change', showPretendParts);
showPretendParts();

/** Draw the pretend band's light on the page (a real band shows it on your wrist). */
function showLight(led) {
  const dim = led.brightness / 255;
  const [r, g, b] = [led.r, led.g, led.b].map((part) => Math.round(part * dim));
  $('#light').style.backgroundColor = `rgb(${r}, ${g}, ${b})`;
  const pattern = ['off', 'on', 'blinking', 'breathing', 'flashing', 'rainbow'][led.pattern];
  $('#light-text').textContent = led.source === 'mic' ? 'blue: the microphone is on' : pattern;
}

const CANCELLED = 'Cancelled. Press Connect to try again.';
const STILL_CONNECTING_MS = 15000;
// A band that is connected to something else is not in the browser's list: that first, then the double-tap.
const STILL_CONNECTING_BLE = 'Still connecting... Your band is not in the list? Close whatever else is connected to it '
  + "(the dashboard tab, another program, a teammate's laptop), then double-tap the band. "
  + 'No list on the screen? Press Cancel and try again.';

/**
 * When Connect is pressed: connect to the band, then run `setup(band, pretend)`.
 * Browsers only allow Bluetooth and USB to start from a click, which is why there is a button.
 */
export function onConnect(setup) {
  let band = null;
  let tries = 0;                         // goes up with every Connect and every Cancel
  const idle = (text) => {
    band = null;
    say(text);
    $('#connect').disabled = false;
    $('#disconnect').disabled = true;
    $('#disconnect').textContent = 'Disconnect';
    if ($('#pretend-drop')) $('#pretend-drop').disabled = true;
    $('#number').disabled = false;
    $('#link').disabled = false;
  };

  $('#connect').addEventListener('click', async () => {
    const link = $('#link').value;                       // 'ble', 'serial' or 'pretend'
    let number;
    try {
      number = bandNumber();
    } catch (e) {
      say(e.message);                                    // not a band number: the sentence says what to type
      return;
    }
    if (number === null && link !== 'pretend') {
      say('Type your band number first: the XX in Vexo-HACK-XX on your band or its bag (1 to 999).');
      return;
    }
    const mine = ++tries;
    $('#connect').disabled = true;
    $('#number').disabled = true;
    $('#link').disabled = true;
    $('#disconnect').disabled = false;                   // while connecting, the second button is Cancel
    $('#disconnect').textContent = 'Cancel';
    say(link === 'pretend' ? 'Connecting...' : 'Connecting... pick your band in the list.');
    const slow = setTimeout(() => {
      if (mine === tries && !band) say(link === 'ble' ? STILL_CONNECTING_BLE : 'Still connecting... No list on the screen? Press Cancel and try again.');
    }, STILL_CONNECTING_MS);
    let found = null;
    try {
      // The pretend band: autoTap false = it does nothing by itself, the buttons in the
      // dashed box decide what happens. A pretend band can have any number from 1 to 999.
      const pretend = link === 'pretend' ? new SimTransport(number ?? 42, { autoTap: false, firmware: query.get('firmware') ?? '1.1' }) : null;
      if (pretend) pretend.onLed = showLight;
      // autoReconnect: when the link drops, the library gets it back by itself and sets
      // the LED, the settings and the data streams again.
      found = await Band.connect(pretend ? pretend.number : number, { transport: pretend ?? link, autoReconnect: true });
      if (mine !== tries) {                              // Cancel was pressed while we waited: let this band go again
        await found.disconnect();
        return;
      }
      band = found;
      $('#disconnect').textContent = 'Disconnect';
      band.on('reconnecting', (e) => say(`Link lost. Trying to get it back for ${e.seconds} seconds...`));
      band.on('reconnected', (e) => say(`Connected again after ${e.seconds.toFixed(1)} seconds.`));
      // Another program on the band's other link (USB cable or Bluetooth) took a stream of this page.
      band.on('stream-taken', (e) => say(e.message));
      band.on('disconnect', (e) => {
        idle(e.reason === 'closed' ? 'Disconnected.' : `Disconnected (${e.reason}). Press Connect to start again.`);
      });
      say(`Connected to ${band.name}.`);
      // "Lose the link" on the pretend band: it stays out of reach for 2 seconds, about
      // as long as a real one takes. Watch the library get it back by itself.
      if (pretend && $('#pretend-drop')) {
        $('#pretend-drop').disabled = false;
        $('#pretend-drop').onclick = () => pretend.dropLink(2);
      }
      await setup(band, pretend);
    } catch (e) {
      if (mine !== tries) return;                        // cancelled meanwhile: the message is already there
      if (found?.connected) await found.disconnect().catch(() => {});
      idle(e.message);                                   // the library's messages are one sentence that says what to do next
    } finally {
      clearTimeout(slow);
    }
  });
  $('#disconnect').addEventListener('click', () => {
    if (band) return band.disconnect();
    tries++;                                             // Cancel: stop waiting for this try
    return idle(CANCELLED);
  });
}
