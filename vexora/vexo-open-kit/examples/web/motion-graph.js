// Motion graph: draws x, y and z of the band's motion sensor on a canvas.
import { Band } from '../../js/src/index.js';
import { bandNumber, transport, onConnect, say } from './kit.js';

const canvas = document.querySelector('#graph');
const pen = canvas.getContext('2d');
const COLOURS = { ax: '#d95f02', ay: '#1b9e77', az: '#7570b3' };
const KEEP = 300;                       // readings on screen: 6 seconds at 50 a second
let readings = [];

onConnect(async () => {
  const band = await Band.connect(bandNumber(), { transport: transport() });
  band.on('motion', (m) => { readings = [...readings, m].slice(-KEEP); });
  await band.configure({ motion: 50 });
  say(`Connected to ${band.name}. Move it.`);
  requestAnimationFrame(draw);
  return band;
});

function draw() {
  pen.clearRect(0, 0, canvas.width, canvas.height);
  const newest = readings.at(-1);
  for (const axis of Object.keys(COLOURS)) {
    if (newest) document.querySelector(`#${axis}`).textContent = newest[axis].toFixed(2);   // the numbers above the graph
    pen.beginPath();
    pen.strokeStyle = COLOURS[axis];
    // 0 g is the middle of the canvas, +2 g the top edge, -2 g the bottom edge.
    readings.forEach((m, i) => pen.lineTo(i * canvas.width / KEEP, canvas.height * (0.5 - m[axis] / 4)));
    pen.stroke();
  }
  requestAnimationFrame(draw);          // draw again on the next screen refresh
}
