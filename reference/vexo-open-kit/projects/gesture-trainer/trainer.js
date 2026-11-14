// Gesture trainer: record three examples of a gesture, then the page recognises it.
// Kit features: motion samples (acceleration and turning speed), the band's time on every
// sample, the LED, auto-reconnect. The clever part is in recogniser.js.
import { $, say, attempt, onConnect } from '../shared/page.js';
import { Segmenter, toShape, match, NOT_SURE, GYRO_SCALE } from './recogniser.js';
import { MOTIONS } from './motions.js';

const GESTURES = 3;              // rows in the table on the page
const EXAMPLES = 3;              // how many times you show each gesture
const LINES = { ax: '#ff5c5c', ay: '#5cd65c', az: '#5c9dff', gx: '#ffa13d', gy: '#c77dff', gz: '#3dd6d6' };

const segmenter = new Segmenter();
// The examples live here, in the page's memory: one list of shapes per gesture. They are
// gone when the page is reloaded (see "Try next" in the README for keeping them).
const examples = Array.from({ length: GESTURES }, () => []);

let band = null;
let recording = -1;              // the gesture being recorded (0, 1, 2), or -1 = recognising
let resting = null;              // timer that waits for the pretend hand to finish a motion

/** The big line and the small line under it. A recognised gesture is written in its colour. */
function show(text, detail = '', colour = '') {
  $('#status').textContent = text;
  $('#status').style.color = colour;
  $('#detail').textContent = detail;
}
const nameOf = (g) => $(`#name-${g}`).value.trim() || `Gesture ${g + 1}`;
const trained = () => examples.some((list) => list.length > 0);

function showTable() {
  for (let g = 0; g < GESTURES; g++) {
    $(`#count-${g}`).textContent = `${examples[g].length} of ${EXAMPLES}`;
    $(`#record-${g}`).textContent = recording === g ? 'Stop' : 'Record';
    $(`#record-${g}`).disabled = !band || (recording !== -1 && recording !== g);
  }
}

function waitForGesture() {
  if (recording !== -1) show(`Show me "${nameOf(recording)}"`, `Example ${examples[recording].length + 1} of ${EXAMPLES}. Move, then hold still for a moment.`);
  else if (trained()) show('Make a gesture', 'Move, then hold still for a moment.');
  else show('Press Record', 'Record at least one gesture first. Two or three are more fun.');
}

function record(g) {
  if (recording === g) {         // the button says Stop now
    recording = -1;
    attempt(band.ledOff());
  } else {
    recording = g;
    examples[g] = [];            // recording again replaces the old examples
    attempt(band.setLed('#ffffff', { pattern: 'breathe', period: 1500 }));    // white and breathing: "I am learning"
  }
  showTable();
  waitForGesture();
}

/** Draw the samples of one gesture: six lines over time, so you see what the recogniser sees. */
function draw(gesture) {
  const canvas = $('#picture');
  const pen = canvas.getContext('2d');
  const { width, height } = canvas;
  pen.clearRect(0, 0, width, height);
  if (!gesture) return;
  const start = gesture[0].t, length = gesture[gesture.length - 1].t - start;
  pen.lineWidth = 2;
  for (const [axis, colour] of Object.entries(LINES)) {
    const scale = axis[0] === 'g' ? GYRO_SCALE * 2 : 2;      // +-2 g and +-500 degrees/second fill the height
    pen.strokeStyle = colour;
    pen.beginPath();
    for (const sample of gesture) {
      // sample.t places the point in time. Counting samples would bend the picture when some are lost.
      pen.lineTo(10 + ((sample.t - start) / length) * (width - 20), height / 2 - (sample[axis] / scale) * (height / 2 - 8));
    }
    pen.stroke();
  }
}

function onGesture(gesture) {
  draw(gesture);
  const shape = toShape(gesture);
  const seconds = ((gesture[gesture.length - 1].t - gesture[0].t) / 1000).toFixed(1);
  if (recording !== -1) {
    examples[recording].push(shape);
    if (examples[recording].length === EXAMPLES) {
      show(`Learned "${nameOf(recording)}"`, 'Record the next gesture, or make one and see if it is recognised.');
      recording = -1;
      attempt(band.ledOff());
    } else {
      waitForGesture();
    }
    showTable();
    return;
  }
  if (!trained()) return;
  // Every example of every gesture is a candidate. The label is the row in the table.
  const candidates = examples.flatMap((list, g) => list.map((one) => ({ label: g, shape: one })));
  const found = match(shape, candidates);
  const detail = `${seconds} seconds. Distance to the nearest example: ${found.distance.toFixed(2)} (not sure above ${NOT_SURE}).`;
  if (found.label === null) {
    show('Not sure', detail);
    attempt(band.ledOff());
  } else {
    const colour = $(`#colour-${found.label}`).value;         // '#rrggbb' from the colour picker
    show(nameOf(found.label), detail, colour);
    attempt(band.setLed(colour));                             // the mic is off in this project, so the LED is ours
  }
}

onConnect(async (theBand, pretend) => {
  band = theBand;
  band.on('motion', (sample) => {
    const gesture = segmenter.feed(sample);
    if (gesture) onGesture(gesture);
  });

  // When the link drops, half a gesture is worth nothing: throw it away and stop recording.
  // The examples recorded so far stay. The library gets the link back by itself.
  const stop = (text) => { segmenter.reset(); recording = -1; showTable(); show(text); };
  band.on('reconnecting', () => stop('Link lost...'));
  band.on('reconnected', () => { attempt(band.ledOff()); waitForGesture(); });
  band.on('disconnect', () => {
    band = null;
    stop('Not connected');
    clearInterval(resting);
    for (const name of Object.keys(MOTIONS)) $(`#pretend-${name}`).disabled = true;
  });

  for (let g = 0; g < GESTURES; g++) $(`#record-${g}`).onclick = () => record(g);
  if (pretend) {
    // The buttons are off while the pretend hand moves, and for half a second of rest
    // after it. Without the rest two presses would run together into one long gesture.
    const buttons = (on) => { for (const name of Object.keys(MOTIONS)) $(`#pretend-${name}`).disabled = !on; };
    pretend.playMotion([{}]);    // one sample at rest: from now on the pretend hand lies still between its motions
    for (const [name, [shape, seconds]] of Object.entries(MOTIONS)) {
      $(`#pretend-${name}`).onclick = () => {
        pretend.playMotion(shape, { seconds });
        pretend.playMotion({}, { seconds: 0.5 });        // {} = at rest
        buttons(false);
        clearInterval(resting);
        resting = setInterval(() => { if (!pretend.moving) { clearInterval(resting); buttons(Boolean(band)); } }, 50);
      };
    }
    buttons(true);
  }

  await band.configure({ motion: 50 });             // 50 samples a second: every band can do that
  say(`Connected to ${band.name}.`);
  showTable();
  waitForGesture();
});
