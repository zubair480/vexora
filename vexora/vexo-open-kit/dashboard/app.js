// Vexo Open dashboard. A static page: everything runs in this browser tab and nothing is
// sent anywhere. The only thing it remembers is the last LED colour you picked.
import { Band, SimTransport, WebBluetoothTransport, WebSerialTransport, LED_PATTERNS, bandId } from '../js/src/index.js';
import {
  MIC_OWNS_LED, parseBandParam, isColour, bluetoothProblem, connectProblem, commandProblem, disconnectMessage,
  reconnectingMessage, reconnectedMessage, streamTakenMessage, startupStreams, setupMessage, eventLabel, batteryDetail, signalWords, ledView, History, RateMeter,
} from './logic.js';
import { temperatureText, TEMPERATURE_NOTE, loudnessBar, LOUDNESS_NOTE, PRETEND_MIC_NOTE } from './logic.js';
import { StepCounter, stepsText, activityDetail, STEPS_NOTE, STILL_CONNECTING_MS, stillConnectingMessage, CANCELLED_MESSAGE } from './logic.js';
import {
  DEFAULT_MOTION, pretendOptions, linkOf, sensorChoices, rateChoices, fallbackRate, motionHint, motionProblem,
  motionDroppedMessage, historySize, LossCounter, ArrivalRate, envelope,
} from './logic.js';
import { speechPanel } from './speech/panel.js';     // optional Listen and Live transcript

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);

// This file runs, so the page came from a web server: put the "opened as a file" help away.
$('needs-server').hidden = true;

function say(text, kind = 'info') {
  $('message').textContent = text;
  $('message').dataset.kind = kind;
}
function setStatus(state, text) {
  $('status').dataset.state = state;
  $('status').textContent = text;
}

// ---- which band? (?band=42)
const raw = params.get('band');
const chosen = raw === null ? null : parseBandParam(raw);   // { number, name } or null
if (chosen) {
  $('band-name').textContent = chosen.name;
  $('step-name').textContent = chosen.name;
  document.title = `${chosen.name} · Vexo Open`;
} else {
  $('pick').hidden = false;
  $('connect').hidden = true;
  if (raw !== null) $('pick-message').textContent = `"${raw}" is not a band number. Type the number written on your band's bag.`;
}
$('pick-form').addEventListener('submit', (e) => {
  e.preventDefault();
  const picked = parseBandParam($('pick-number').value);
  if (!picked) {
    $('pick-message').textContent = 'A band number is one to three digits, like 07 or 42.';
    return;
  }
  params.set('band', bandId(picked.number));
  location.search = params.toString();
});

// ---- what can this browser do?
const noBle = bluetoothProblem({
  reason: WebBluetoothTransport.unavailableReason(),
  secure: window.isSecureContext,
  iOS: /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1),
});
const noSerial = WebSerialTransport.unavailableReason();
if (noBle) {
  $('no-ble').hidden = false;
  $('no-ble').textContent = noBle;
}
if (noSerial) {
  $('serial-note').hidden = false;
  $('serial-note').textContent = 'USB cable connection needs Chrome or Edge on a laptop.';
}
// Sentences that never change. They live in logic.js so the tests can read them.
$('mic-note').textContent = LOUDNESS_NOTE;
$('temperature-note').textContent = TEMPERATURE_NOTE;
$('steps-note').textContent = STEPS_NOTE;
$('sim-mic-note').textContent = PRETEND_MIC_NOTE;

// ---- the state of the page; render() makes the buttons match it
let band = null;          // the connected Band, or null
let sim = null;           // the pretend band behind it, when there is one
let busy = false;         // a connection attempt is running
let attempt = 0;          // goes up with every connection attempt and every Cancel, so a late answer can be told apart
let cancellable = false;  // the attempt waits for the browser's list of bands or ports: show Cancel
let reconnecting = false; // the link dropped and the library is getting it back
let dropReason = 'lost';  // why it dropped: 'lost', or 'session-ended' (the band ended the USB session)
let linkWords = '';       // what the status says while connected
let audioOn = false;      // the Stream audio switch
let meterOn = false;      // the Loudness meter switch
let micBusy = false;      // waiting for the band to switch audio or the meter
let micProblem = '';
let ledProblem = '';
let limits = null;        // what band.motionLimits() said: the rates, and the fastest per sensor choice on this link
let motion = { ...DEFAULT_MOTION };   // the motion setting that is really on: { rate, sensors }
let motionBusy = false;   // waiting for the band to change the motion setting
let motionNote = '';
let link = 'ble';         // 'ble' or 'usb', for the words in the Motion panel
let takenStopped = false; // the streams another program took have been stopped by the band (that program ended)
let takingBack = false;   // waiting for the band after "Take it back"

const CONNECT_BUTTONS = ['btn-ble', 'btn-serial', 'btn-sim'];
const ledControls = [$('led-colour'), $('led-brightness'), $('led-pattern'), $('led-off'), $('identify')];

function render() {
  const linked = band !== null;               // Disconnect also stops a reconnect that is still trying
  const live = linked && !reconnecting;       // the band answers: data and controls are on
  $('panels').dataset.live = String(live);
  $('btn-ble').disabled = busy || !!noBle;
  $('btn-serial').disabled = busy || !!noSerial;
  $('btn-sim').disabled = busy;
  for (const id of CONNECT_BUTTONS) $(id).hidden = linked;
  $('connect-steps').hidden = linked;               // "1. Double-tap your band ..." is for before connecting
  $('btn-cancel').hidden = !(busy && cancellable);
  $('btn-disconnect').hidden = !linked;
  $('sim-drop').hidden = !(live && sim);
  $('sim-tools').hidden = !(live && sim);
  $('sim-led').hidden = !(linked && sim);
  $('sim-mic-note').hidden = !(linked && sim);      // the pretend band's sound is a recording, not this room
  $('usb-help').hidden = linked || !!noSerial;      // which entry to pick in the browser's list of ports
  // Streams this page started that go to another program now (the library's 'stream-taken').
  const taken = live ? band.takenStreams : [];
  $('taken').hidden = taken.length === 0;
  $('taken-text').textContent = taken.length ? streamTakenMessage(taken, takenStopped, link) : '';
  $('take-back').disabled = takingBack;

  $('audio-switch').disabled = !live || micBusy;
  $('meter-switch').disabled = !live || micBusy;
  if (!micBusy) {
    $('audio-switch').checked = audioOn;
    $('meter-switch').checked = meterOn;
  }
  // While the microphone is on (audio or the loudness meter), the blue mic light owns the
  // LED, so the LED controls are off.
  const micOn = audioOn || meterOn;
  const ledLocked = !live || micOn || micBusy;
  for (const el of ledControls) el.disabled = ledLocked;
  $('led-note').textContent = micOn ? MIC_OWNS_LED : ledProblem;
  speechUi.setUsable(live && !micBusy);
  renderMotion(live);
}

// ---- connect / disconnect
async function connect(kind) {
  if (band || busy || !chosen) return;
  const mine = ++attempt;
  busy = true;
  cancellable = kind !== 'sim';
  // The browser's list may never show up, or never be answered. Say so after a while; Cancel is the way out.
  const slow = setTimeout(() => {
    if (mine === attempt && busy) say(stillConnectingMessage(kind, chosen.name));
  }, STILL_CONNECTING_MS);
  setStatus('busy', 'Connecting…');
  say(kind === 'ble' ? `Pick ${chosen.name} in the list, then press Pair.` : kind === 'serial' ? 'Pick "Vexo Open band" in the list, then press Connect.' : '');
  render();
  let connected = null;
  let pretend = null;
  try {
    let transport = kind;
    if (kind === 'sim') {
      pretend = new SimTransport(chosen.number, pretendOptions(params));    // ?firmware=1.0 and ?link=usb
      pretend.onLed = showSimLed;
      transport = pretend;
    }
    // ?list=all: the chooser lists every band nearby (for a computer that remembers this band under an
    // old name). The library still checks the number after connecting and refuses another band.
    if (kind === 'ble' && params.get('list') === 'all') {
      transport = new WebBluetoothTransport(chosen.number);
      transport.listAll = true;
    }
    // For Bluetooth the library filters the chooser to this band's exact name and service.
    // autoReconnect: when the link drops, the library finds the band again by itself.
    connected = await Band.connect(chosen.number, { transport, autoReconnect: true });
  } catch (e) {
    if (mine === attempt) say(connectProblem(e, { kind, wanted: chosen.name }), 'error');
  } finally {
    clearTimeout(slow);
    if (mine === attempt) {
      busy = false;
      if (!connected) {
        setStatus('idle', 'Not connected');
        render();
      }
    }
  }
  if (mine !== attempt) {                     // Cancel was pressed meanwhile: let a band that answered late go again
    connected?.disconnect().catch(() => {});
    return;
  }
  if (!connected) return;

  band = connected;
  sim = pretend;
  linkWords = kind === 'sim' ? 'Pretend band' : kind === 'serial' ? 'Connected by USB' : 'Connected';
  setStatus('connected', linkWords);
  say(kind === 'sim' ? 'Starting the pretend band…' : `Connected to ${band.name}. Setting it up…`);
  link = linkOf(kind, pretend?.via);
  // A pretend band on a pretend USB cable gets the streams of a cable link (no signal strength).
  const streamKind = link === 'usb' ? 'serial' : kind;
  wire(band, streamKind);
  const hadFocus = CONNECT_BUTTONS.includes(document.activeElement?.id);
  render();
  if (hadFocus) $('btn-disconnect').focus();
  await setUp(band, streamKind);
}

// Flash the band, then turn the streams on one by one. One stream failing does not stop the others.
async function setUp(b, kind) {
  let flashProblem = null;
  try {
    await b.identify();
  } catch (e) {
    flashProblem = e.code === 'denied' ? MIC_OWNS_LED : `The band did not flash: ${commandProblem(e)}`;
  }
  const failed = [];
  for (const [label, streams] of startupStreams(kind)) {
    if (band !== b || reconnecting) return;     // the link ended meanwhile; that message is already showing
    try {
      await b.configure(streams);
    } catch {
      failed.push(label);
    }
  }
  if (band !== b || reconnecting) return;
  if (kind === 'serial') $('signal-words').textContent = 'only shown over Bluetooth';
  await syncMotion(b);                        // fill the rate and sensor pickers with what this band can do
  if (band !== b || reconnecting) return;
  const result = setupMessage({ name: b.name, kind: sim ? 'sim' : kind, flashProblem, failed });
  say(result.text, result.kind);
}

// The link dropped and the library is trying to get it back: say so, and switch the controls off meanwhile.
function onReconnecting(b, seconds, reason) {
  if (band !== b) return;
  reconnecting = true;
  dropReason = reason;
  ledNext = null;
  ledProblem = '';
  micProblem = '';
  motionNote = '';
  micLevel = 0;
  barDirty = true;
  setStatus('busy', 'Reconnecting…');
  const hadFocus = document.activeElement?.id === 'sim-drop';
  render();
  if (hadFocus) $('btn-disconnect').focus();
  const message = reconnectingMessage(seconds, reason);
  say(message.text, message.kind);
  requestFrame();
}

// The link is back. The library has set the LED, the settings and the streams again.
async function onReconnected(b, kind, failed) {
  if (band !== b) return;
  reconnecting = false;
  micBusy = false;
  audioOn = b.streams.audio;        // what the band says
  meterOn = b.streams.micLevel;
  speechUi.audioChanged(audioOn);   // the band's packet counter starts again after a reconnect
  audioMeter.reset(performance.now());
  $('audio-stats').textContent = audioOn ? 'Audio is on. Counting…' : 'Audio is off.';
  setStatus('connected', linkWords);
  render();
  const message = reconnectedMessage(failed, dropReason);
  say(message.text, message.kind);
  // Streams the page had not started yet when the link dropped are started now.
  for (const [, streams] of startupStreams(kind)) {
    if (band !== b || reconnecting) return;
    if (streams.motion && b.streams.motion) continue;     // the library put motion back as it was: keep that rate
    await b.configure(streams).catch(() => {});
  }
  await syncMotion(b, true);                  // the pickers show what is really on now
}

function onDisconnected(b, reason) {
  if (band !== b) return;
  const name = b.name;
  band = null;
  sim = null;
  reconnecting = false;
  audioOn = false;
  meterOn = false;
  micBusy = false;
  micProblem = '';
  ledProblem = '';
  ledNext = null;
  limits = null;
  motion = { ...DEFAULT_MOTION };
  motionBusy = false;
  motionNote = '';
  clearInterval(motionTimer);
  $('motion-measured').textContent = '–';
  $('motion-lost').textContent = '–';
  clearInterval(statsTimer);
  $('temperature').textContent = temperatureText(null);     // an old reading would look like a live one
  stepCounter.reset();
  $('steps').textContent = stepsText(null);
  $('steps-detail').textContent = 'Waiting for the band.';
  speechUi.disconnected();
  $('audio-stats').textContent = 'Audio is off.';
  micLevel = 0;
  barDirty = true;
  setStatus('idle', 'Not connected');
  const hadFocus = ['btn-disconnect', 'sim-drop'].includes(document.activeElement?.id);
  render();
  const message = disconnectMessage(reason, name);
  say(message.text, message.kind);
  if (hadFocus) $('btn-sim').focus();
  requestFrame();
}

for (const [id, kind] of [['btn-ble', 'ble'], ['btn-serial', 'serial'], ['btn-sim', 'sim']]) {
  $(id).addEventListener('click', () => connect(kind));
}
// Cancel: stop waiting for a connection attempt (for example when the browser's list of bands never shows up).
$('btn-cancel').addEventListener('click', () => {
  if (!busy) return;
  attempt++;
  busy = false;
  setStatus('idle', 'Not connected');
  render();
  say(CANCELLED_MESSAGE);
  $('btn-ble').disabled ? $('btn-sim').focus() : $('btn-ble').focus();
});
$('btn-disconnect').addEventListener('click', () => {
  band?.disconnect().catch((e) => say(commandProblem(e), 'error'));
});
// "Lose the link": the pretend band goes out of reach for 3 seconds, then can be found again.
const SIM_AWAY_MS = 3000;
$('sim-drop').addEventListener('click', () => {
  const pretend = sim;
  if (!pretend) return;
  pretend.reachable = false;
  pretend.dropLink();
  setTimeout(() => { pretend.reachable = true; }, SIM_AWAY_MS);
});
// "Take it back": start the streams again that another program took. The stream then goes to this page.
$('take-back').addEventListener('click', async () => {
  const b = band;
  if (!b || takingBack) return;
  takingBack = true;
  render();
  try {
    await b.takeBack();
    if (band === b) say('The data is back on this page. If the other program is still running, it has lost it now.');
  } catch (e) {
    if (band === b) say(`Could not take it back. ${commandProblem(e)}`, 'error');
  } finally {
    takingBack = false;
    if (band === b) {
      audioOn = b.streams.audio;
      meterOn = b.streams.micLevel;
      render();
      await syncMotion(b, true).catch(() => {});     // settings are shared too: show the rate that is really on
    }
  }
});
// The pretend band: a second program on its other link starts motion, and ends again.
$('sim-take').addEventListener('click', () => sim?.otherProgramStarts('motion'));
$('sim-leave').addEventListener('click', () => sim?.otherProgramCloses());
$('sim-tap').addEventListener('click', () => {
  if (sim && !sim.tap('tap')) say('The pretend band did not send the tap: its events are not turned on.', 'error');
});

// ---- live data
const FIELDS = ['ax', 'ay', 'az', 'gx', 'gy', 'gz'];
let history = new History(historySize(DEFAULT_MOTION.rate), FIELDS);   // the graph: about 5 s of samples at the rate that is on
const losses = new LossCounter();       // lost motion samples, from gaps in their numbers
const arriving = new ArrivalRate();     // motion samples per second that really arrive
let motionTimer = 0;
const stepCounter = new StepCounter();   // steps since the page connected
const audioMeter = new RateMeter();
let statsTimer = 0;
let micLevel = 0;         // 0..1, how full the loudness bar is (see loudnessBar in logic.js)
let graphDirty = true;    // something to redraw on the next frame
let barDirty = false;

function wire(b, kind) {
  history.clear();
  graphDirty = true;
  b.on('motion', (m) => {
    history.push(m);          // kept cheap: up to 1666 of these a second; the graph is drawn once per screen refresh
    losses.add(m.n);
    arriving.add(m.t);
    graphDirty = true;
    requestFrame();
  });
  b.on('event', addEvent);
  b.on('mic-level', (m) => {
    micLevel = loudnessBar(m.rmsDb);    // not m.level: that spreads 96 dB over the bar, which leaves it half full in silence
    barDirty = true;
    requestFrame();
  });
  b.on('audio', (frame) => {
    audioMeter.add(frame.opus.length);
    speechUi.frame(frame);
  });
  b.on('battery', (p) => {
    $('battery').textContent = p.percent;
    $('battery-detail').textContent = batteryDetail(p);
  });
  b.on('temperature', (t) => {
    $('temperature').textContent = temperatureText(t.celsius);
  });
  b.on('activity', (a) => {
    $('steps').textContent = stepsText(stepCounter.add(a.steps));
    $('steps-detail').textContent = activityDetail(a);
  });
  b.on('link', (l) => {
    $('rssi').textContent = l.rssi === null ? '–' : l.rssi;
    $('signal-words').textContent = signalWords(l.rssi);
  });
  b.on('notice', (n) => {
    if (n.kind === 'low-battery' || n.kind === 'critical-battery') say('The band\'s battery is low. Charge it soon.', 'error');
  });
  b.on('stream-taken', (taken) => {
    if (band !== b) return;
    takenStopped = taken.stopped.length > 0;
    render();
  });
  b.on('reconnecting', (r) => onReconnecting(b, r.seconds, r.reason));
  b.on('reconnected', (r) => onReconnected(b, kind, r.failed));
  b.on('disconnect', (d) => onDisconnected(b, d.reason));
  b.on('notice', (n) => {
    const dropped = n.kind === 'stream-dropped' ? motionDroppedMessage(n.arg) : null;
    if (!dropped) return;
    motionNote = dropped;
    render();
  });
  losses.reset();
  arriving.reset(performance.now());
  motionTimer = setInterval(showMotionStats, 1000);
  statsTimer = setInterval(showAudioStats, 1000);
}

function addEvent(event) {
  const list = $('events');
  list.querySelector('.empty')?.remove();
  const li = document.createElement('li');
  const label = document.createElement('span');
  label.textContent = eventLabel(event);
  const time = document.createElement('time');
  time.textContent = new Date().toLocaleTimeString();
  li.append(label, time);
  list.prepend(li);
  while (list.children.length > 30) list.lastChild.remove();
}

// ---- microphone: the Loudness meter and Stream audio switches
async function micSwitch(key, word, want) {
  const b = band;
  if (!b) return render();
  micBusy = true;
  micProblem = '';
  render();
  try {
    await b.configure({ [key]: want });
  } catch (err) {
    micProblem = `Could not turn ${word} ${want ? 'on' : 'off'}. ${commandProblem(err)}`;
  } finally {
    if (band === b) {
      micBusy = false;
      audioOn = b.streams.audio;      // what the band says, not what we hoped
      meterOn = b.streams.micLevel;
      if (key === 'audio') {
        audioMeter.reset(performance.now());
        speechUi.audioChanged(audioOn);
      }
      if (!meterOn) {
        micLevel = 0;
        barDirty = true;
        requestFrame();
      }
      $('audio-stats').textContent = micProblem || (audioOn ? 'Audio is on. Counting…' : 'Audio is off.');
      // When the microphone goes off, the band puts its own LED setting back. Nothing to send.
      render();
    }
  }
}
$('audio-switch').addEventListener('change', (e) => micSwitch('audio', 'audio', e.target.checked));
$('meter-switch').addEventListener('change', (e) => micSwitch('micLevel', 'the loudness meter', e.target.checked));
// Listen and Live transcript (speech/) need the audio stream; they flip it through the same micSwitch.
const speechUi = speechPanel({
  audioIsOn: () => audioOn,
  setAudio: (on) => micSwitch('audio', 'audio', on),
  pretendBand: () => sim,
});

function showAudioStats() {
  if (!audioOn || micBusy) return;
  const rates = audioMeter.take(performance.now());
  if (rates) $('audio-stats').textContent = `${rates.framesPerSecond} frames/s, ${rates.bytesPerSecond} bytes/s`;
}

// ---- LED
const SWATCHES = ['#ff0000', '#ff8800', '#ffee00', '#00d040', '#00a0ff', '#8040ff', '#ffffff'];
const COLOUR_KEY = 'vexo-open-led-colour';
for (const c of SWATCHES) {
  const btn = document.createElement('button');
  btn.style.background = c;
  btn.disabled = true;
  btn.setAttribute('aria-label', `Set LED to ${c}`);
  btn.addEventListener('click', () => {
    $('led-colour').value = c;
    sendLed();
  });
  $('swatches').append(btn);
  ledControls.push(btn);
}
for (const pattern of LED_PATTERNS) {
  if (pattern === 'off') continue;      // the Off button does that
  const option = document.createElement('option');
  option.value = pattern;
  option.textContent = pattern;
  $('led-pattern').append(option);
}
try {
  const saved = localStorage.getItem(COLOUR_KEY);
  if (isColour(saved)) $('led-colour').value = saved;
} catch {
  // storage is blocked: start with the default colour
}

let ledBusy = false;
let ledNext = null;       // the newest LED action that has not been sent yet

// Dragging the picker or slider fires many events; only the newest setting is sent.
async function led(action) {
  const b = band;
  if (!b) return;
  ledNext = action;
  if (ledBusy) return;
  ledBusy = true;
  try {
    while (ledNext !== null && band === b) {
      const next = ledNext;
      ledNext = null;
      await next(b);
      ledProblem = '';
    }
  } catch (e) {
    // 'denied' means the microphone owns the LED.
    ledNext = null;
    if (band === b && !reconnecting) ledProblem = commandProblem(e);
  } finally {
    ledBusy = false;
    render();
  }
}

function sendLed() {
  const colour = $('led-colour').value;
  const options = { brightness: Number($('led-brightness').value) / 100, pattern: $('led-pattern').value };
  try {
    localStorage.setItem(COLOUR_KEY, colour);
  } catch {
    // storage is blocked: the colour is just not remembered
  }
  led((b) => b.setLed(colour, options));
}
$('led-colour').addEventListener('input', sendLed);
$('led-brightness').addEventListener('input', sendLed);
$('led-pattern').addEventListener('change', sendLed);
$('led-off').addEventListener('click', () => led((b) => b.ledOff()));
$('identify').addEventListener('click', () => led((b) => b.identify()));

// The pretend band tells us which light it really shows (the blue mic light and the
// identify flash win over the colour you set).
function showSimLed(state) {
  const view = ledView(state);
  const dot = $('sim-led-dot');
  dot.style.backgroundColor = view.colour;
  dot.style.animationDuration = `${view.periodMs}ms`;
  dot.dataset.pattern = view.pattern;
  dot.dataset.source = view.source;
  $('sim-led-text').textContent = view.text;
}

// ---- motion: how many samples per second, and which sensors
const motionPickers = ['motion-rate', 'motion-sensors'];

// Put the choices in a list. A choice that is not possible stays in the list, switched off.
function fillSelect(select, choices, value) {
  const key = JSON.stringify(choices);
  if (select.dataset.choices !== key) {
    select.dataset.choices = key;
    select.replaceChildren(...choices.map((choice) => {
      const option = document.createElement('option');
      option.value = choice.value;
      option.textContent = choice.label;
      option.disabled = !choice.enabled;
      return option;
    }));
  }
  select.value = String(value);
}

function renderMotion(live) {
  const usable = live && limits !== null && !motionBusy;
  for (const id of motionPickers) $(id).disabled = !usable;
  $('motion-note').textContent = motionNote;
  if (!limits) {
    $('gyro-toggle').disabled = false;
    if (!band) $('motion-hint').textContent = 'Connect a band to choose how fast it sends motion.';
    return;
  }
  const rates = rateChoices(limits, motion.sensors, link).map((c) => ({ value: c.rate, label: c.label, enabled: c.enabled }));
  fillSelect($('motion-rate'), rates, motion.rate);
  fillSelect($('motion-sensors'), sensorChoices(limits), motion.sensors);
  $('motion-hint').textContent = motionHint(limits, link, sim !== null);
  // With one sensor alone the graph shows that sensor, and the gyro switch follows.
  $('gyro-toggle').disabled = motion.sensors !== 'both';
  if (motion.sensors !== 'both') showView(motion.sensors);
}

const lostText = () => (!limits ? '–' : typeof limits.accel === 'number' ? String(losses.lost) : 'not reported by firmware 1.0');

// Start the graph, the measured rate and the lost count again: they belong to one setting.
function restartMotionReadout() {
  if (history.size === historySize(motion.rate)) history.clear();
  else history = new History(historySize(motion.rate), FIELDS);
  losses.reset();
  arriving.reset(performance.now());
  $('motion-measured').textContent = '–';
  graphDirty = true;
  requestFrame();
}

// Ask the band what it can do and what is on, so the pickers never show a wish.
async function syncMotion(b, restart = false) {
  try {
    const now = await b.motionLimits();
    // Firmware 1.0 has one choice of sensors and is asked nothing extra.
    const set = typeof now.accel === 'number' ? await b.getMotion() : { rate: b.streams.motion || DEFAULT_MOTION.rate, sensors: 'both' };
    if (band !== b) return;
    const changed = set.rate !== motion.rate || set.sensors !== motion.sensors;
    limits = now;
    motion = { rate: set.rate, sensors: set.sensors };
    if (changed || restart) restartMotionReadout();
    $('motion-lost').textContent = lostText();
  } catch (e) {
    if (band === b && !reconnecting) motionNote = motionProblem(e);
  }
  if (band === b) render();
}

async function changeMotion(rate, sensors, note = '') {
  const b = band;
  if (!b || !limits || motionBusy) return render();
  const focused = document.activeElement?.id;
  motionBusy = true;
  motionNote = note;
  render();
  let problem = '';
  try {
    await b.configure(typeof limits.accel === 'number' ? { motion: rate, sensors } : { motion: rate });
  } catch (e) {
    problem = motionProblem(e);       // "too fast for this link" and "needs firmware 1.1" come from the library, as they are
  }
  if (band !== b) return;
  await syncMotion(b, true);
  if (band !== b) return;
  motionBusy = false;
  if (problem && !reconnecting) motionNote = problem;
  render();
  if (motionPickers.includes(focused) && !$(focused).disabled) $(focused).focus();
}
$('motion-rate').addEventListener('change', (e) => changeMotion(Number(e.target.value), motion.sensors));
$('motion-sensors').addEventListener('change', (e) => {
  if (!limits) return;
  // Keep the rate if these sensors allow it, else the fastest that they do, and say so.
  const next = fallbackRate(limits, e.target.value, motion.rate, link);
  changeMotion(next.rate, e.target.value, next.text);
});

function showMotionStats() {
  if (!band || reconnecting || motionBusy) return;
  const rate = arriving.take(performance.now());
  if (rate !== null) $('motion-measured').textContent = String(Math.round(rate));
  $('motion-lost').textContent = lostText();
}

// ---- drawing: at most one frame per screen refresh, only when something changed, and
// not at all while the tab is hidden.
const canvas = $('graph');
const ctx = canvas.getContext('2d');
const VIEWS = {
  accel: { keys: ['ax', 'ay', 'az'], range: 2, ticks: [-1, 0, 1], unit: 'g', digits: 2, label: 'Live acceleration graph, x y and z' },
  gyro: { keys: ['gx', 'gy', 'gz'], range: 300, ticks: [-200, 0, 200], unit: 'degrees/second', digits: 0, label: 'Live turning speed graph, x y and z' },
};
let view = VIEWS.accel;
let size = { w: 0, h: 0 };
let colours = null;       // read from the style sheet once, and again when the colour scheme changes
let frameWanted = false;
let lastNumbers = 0;
let columnLo = new Float32Array(0);     // smallest and largest value per pixel column, for fast motion
let columnHi = new Float32Array(0);

function requestFrame() {
  if (frameWanted || document.hidden) return;
  frameWanted = true;
  requestAnimationFrame(frame);
}

function frame(now) {
  frameWanted = false;
  if (document.hidden) return;
  if (barDirty) {
    barDirty = false;
    $('mic-bar').style.transform = `scaleX(${micLevel.toFixed(3)})`;
  }
  if (graphDirty) {
    graphDirty = false;
    drawGraph();
    if (now - lastNumbers > 150) {      // numbers that change 50 times a second cannot be read
      lastNumbers = now;
      showNumbers();
    }
  }
}

function readColours() {
  const style = getComputedStyle(document.documentElement);
  const get = (name) => style.getPropertyValue(name).trim();
  colours = { line: get('--line'), muted: get('--muted'), traces: [get('--x'), get('--y'), get('--z')] };
}

function drawGraph() {
  const { w, h } = size;
  if (!w || !h) return;
  if (!colours) readColours();
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const { range } = view;
  const yOf = (v) => h / 2 - (Math.max(-range, Math.min(range, v)) / range) * (h / 2 - 6);
  ctx.lineWidth = 1;
  ctx.strokeStyle = colours.line;
  ctx.fillStyle = colours.muted;
  ctx.font = '11px system-ui, sans-serif';
  const unit = view === VIEWS.accel ? 'g' : '°/s';
  for (const tick of view.ticks) {
    ctx.beginPath();
    ctx.moveTo(0, yOf(tick));
    ctx.lineTo(w, yOf(tick));
    ctx.stroke();
    ctx.fillText(`${tick > 0 ? '+' : ''}${tick} ${unit}`, 4, yOf(tick) - 3);
  }
  ctx.lineWidth = 2;
  ctx.lineJoin = 'round';
  // Few samples: one point each. Many (fast motion): one up-and-down stroke per pixel column,
  // from the smallest to the largest value in it, so a frame never draws more than 2 x width points.
  const perPoint = history.size <= 2 * w;
  const step = w / (history.size - 1);
  if (!perPoint && columnLo.length !== w) {
    columnLo = new Float32Array(w);
    columnHi = new Float32Array(w);
  }
  view.keys.forEach((key, k) => {
    ctx.strokeStyle = colours.traces[k];
    ctx.beginPath();
    if (perPoint) {
      for (let i = 0; i < history.length; i++) {
        if (i === 0) ctx.moveTo(0, yOf(history.get(key, 0)));
        else ctx.lineTo(i * step, yOf(history.get(key, i)));
      }
    } else {
      const filled = envelope(history, key, w, columnLo, columnHi);
      for (let c = 0; c < filled; c++) {
        if (c === 0) ctx.moveTo(0.5, yOf(columnHi[0]));
        else ctx.lineTo(c + 0.5, yOf(columnHi[c]));
        ctx.lineTo(c + 0.5, yOf(columnLo[c]));
      }
    }
    ctx.stroke();
  });
}

function showNumbers() {
  if (!history.length) return;
  const last = history.length - 1;
  view.keys.forEach((key, k) => {
    $(['ax', 'ay', 'az'][k]).textContent = history.get(key, last).toFixed(view.digits);
  });
}

// Which sensor the graph shows: 'accel' or 'gyro'.
function showView(name) {
  $('gyro-toggle').checked = name === 'gyro';
  if (view === VIEWS[name]) return;
  view = VIEWS[name];
  $('motion-unit').textContent = view.unit;
  canvas.setAttribute('aria-label', view.label);
  graphDirty = true;
  showNumbers();
  requestFrame();
}
$('gyro-toggle').addEventListener('change', (e) => showView(e.target.checked ? 'gyro' : 'accel'));

// The browser tells us the canvas size when it changes, so drawing never has to measure the page.
new ResizeObserver((entries) => {
  const box = entries[0].contentRect;
  size = { w: Math.round(box.width), h: Math.round(box.height) };
  graphDirty = true;
  requestFrame();
}).observe(canvas);
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  colours = null;
  graphDirty = true;
  requestFrame();
});
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    graphDirty = true;
    barDirty = true;
    requestFrame();
  }
});

render();

// For checking the page without clicking: ?band=42&demo=1 starts the pretend band.
if (chosen && params.get('demo') === '1') connect('sim');
