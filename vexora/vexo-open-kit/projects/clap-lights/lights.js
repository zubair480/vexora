// Clap lights: a lamp on the screen that you switch with claps, and a picture of the sound.
// Kit features: the loudness meter (micLevel), the shortest stream interval ('fastest'),
// the band's time on every reading, auto-reconnect.
// The microphone is on while this page is connected (the band's light is solid blue). The
// page only gets loudness numbers: no sound is recorded and none leaves the band.
import { $, say, onConnect } from '../shared/page.js';
import { ClapDetector, SILENCE_DB } from './detector.js';

const THEMES = [['Sunshine', '#ffc93c'], ['Ocean', '#3cc8ff'], ['Lime', '#8cff5a'], ['Candy', '#ff6fb0']];
const BARS = 90;                 // how many readings the picture remembers: never more, the oldest goes
const DRAW_MS = 15;              // paint at most this often (the computer's clock), however fast readings arrive

const detector = new ClapDetector();
const canvas = $('#picture');
const pen = canvas.getContext('2d');

let lampOn = false;
let theme = 0;                   // which of THEMES
let claps = 0;
let ring = 0;                    // size of the ring, 0..1. It jumps up with the sound and sinks slowly.
let bars = [];                   // the last readings: { level: 0..1, clap: true or false }

function draw() {
  const [, colour] = THEMES[theme];
  const { width, height } = canvas;
  pen.clearRect(0, 0, width, height);

  // The lamp, on the left. shadowBlur makes it glow when it is on.
  const x = 130, y = height / 2;
  pen.lineWidth = 4;
  pen.strokeStyle = colour;
  pen.globalAlpha = 0.25 + 0.75 * ring;
  pen.beginPath();
  pen.arc(x, y, 62 + 50 * ring, 0, 2 * Math.PI);         // the ring: bigger when louder
  pen.stroke();
  pen.globalAlpha = 1;
  pen.shadowColor = colour;
  pen.shadowBlur = lampOn ? 40 : 0;
  pen.fillStyle = lampOn ? colour : '#2a313c';
  pen.beginPath();
  pen.arc(x, y, 50, 0, 2 * Math.PI);
  pen.fill();
  pen.shadowBlur = 0;

  // The bars, on the right: one per reading, newest on the right.
  const left = 270, step = (width - left - 10) / BARS, floor = height - 12, top = height - 30;
  bars.forEach((bar, i) => {
    pen.fillStyle = bar.clap ? '#ffffff' : colour;
    pen.globalAlpha = bar.clap ? 1 : 0.35 + 0.65 * (i / BARS);        // old bars fade
    const tall = Math.max(2, bar.level * top);
    pen.fillRect(left + (i + BARS - bars.length) * step, floor - tall, step - 1, tall);
  });
  pen.globalAlpha = 1;

  // The background level of the detector: a clap has to jump well above this line.
  if (detector.background !== null) {
    const lineY = floor - (detector.background / SILENCE_DB) * top;
    pen.strokeStyle = '#9aa5b5';
    pen.lineWidth = 1;
    pen.setLineDash([3, 5]);
    pen.beginPath();
    pen.moveTo(left, lineY);
    pen.lineTo(width - 10, lineY);
    pen.stroke();
    pen.setLineDash([]);
  }
}

function showLamp() {
  $('#status').textContent = lampOn ? 'Lamp on' : 'Lamp off';
  $('#claps').textContent = claps;
  $('#theme').textContent = THEMES[theme][0];
}

function onLevel(reading) {
  // reading.level is 0..1 (average loudness). reading.peakDb is the loudest moment since
  // the last reading: a clap is very short, so the peak catches it best.
  const heard = detector.feed(reading);
  if (heard) {
    claps += 1;
    lampOn = !lampOn;            // every clap flips the lamp at once: no waiting to see if a second one comes
    if (heard === 'double') theme = (theme + 1) % THEMES.length;
    // A double clap has flipped the lamp twice, so it is back where it was, in a new colour.
    showLamp();
  }
  const peak = 1 - reading.peakDb / SILENCE_DB;
  ring = Math.max(peak, ring * 0.9);
  bars.push({ level: peak, clap: Boolean(heard) });
  if (bars.length > BARS) bars.shift();
  $('#loud').textContent = `${Math.round(reading.level * 100)} %`;
  // A band sends 50 readings a second at most. If one sends many more, paint only every
  // DRAW_MS: the page stays usable, and the newest reading is still painted in the end.
  if (Date.now() - drawnAt >= DRAW_MS) {
    paint();
  } else if (drawLater === null) {
    drawLater = setTimeout(paint, DRAW_MS);
  }
}

let drawnAt = 0;                 // the computer's time (ms) of the last painting
let drawLater = null;            // the timer for a painting that waits

function paint() {
  clearTimeout(drawLater);
  drawLater = null;
  drawnAt = Date.now();
  draw();
}

const PRETEND_BUTTONS = ['#pretend-clap', '#pretend-clap2', '#pretend-quiet', '#pretend-talk', '#pretend-loud'];

onConnect(async (band, pretend) => {
  band.on('mic-level', onLevel);

  // When the link drops, the readings stop. Forget the background level: after a gap it
  // is out of date. The library gets the link back and turns the meter on again by itself.
  band.on('reconnecting', () => { detector.reset(); $('#status').textContent = 'Link lost...'; });
  band.on('reconnected', showLamp);
  band.on('disconnect', () => {
    detector.reset();
    bars = [];
    ring = 0;
    $('#status').textContent = 'Not connected';
    $('#loud').textContent = '-';
    for (const button of PRETEND_BUTTONS) $(button).disabled = true;
    draw();
  });

  if (pretend) {
    for (const button of PRETEND_BUTTONS) $(button).disabled = false;
    // The kit's pretend band: a level is what the page reads as `level`. A real band reads
    // about 0.5 in a quiet room, so that is where "Quiet" is. One reading at 0.95 is a clap.
    const clap = () => pretend.playLoudness([0.95]);
    pretend.setLoudness(0.5);
    $('#pretend-clap').onclick = clap;
    $('#pretend-clap2').onclick = () => { clap(); pretend.after(0.3, clap); };
    $('#pretend-quiet').onclick = () => pretend.setLoudness(0.5);
    $('#pretend-talk').onclick = () => pretend.setLoudness(0.6);
    $('#pretend-loud').onclick = () => pretend.setLoudness(0.7);
  }

  // A clap lasts a few hundredths of a second, so ask for the shortest interval the band
  // allows: 'fastest' is 50 readings a second on firmware 1.1 and 10 a second on firmware
  // 1.0, with no branch here. The answer says what we got. The detector works with both,
  // because it uses the time of each reading.
  const pace = await band.setInterval('micLevel', 'fastest');
  await band.configure({ micLevel: true });            // the mic is on now: the band's light turns solid blue
  say(`Connected to ${band.name}. Listening, ${Math.round(1 / pace.seconds)} readings a second. The microphone is on: tell the people near you.`);
  showLamp();
});

draw();
