// Fast motion with a pretend band. Type it in my-project/:
//   node vexo-open-kit/examples/node/fast-motion.mjs 42                 42 = your band number
//   node vexo-open-kit/examples/node/fast-motion.mjs 42 --seconds 10    stop after 10 seconds (3 if you leave it out)
// Fast motion needs band firmware 1.1, which the pretend band has. Node cannot reach a real
// band; the same calls work in a web page (examples/web) on a real band with firmware 1.1.
import { Band } from '../../js/src/index.js';
import { args } from './kit.mjs';

const { number, seconds } = args({ seconds: 3 });
const band = await Band.connect(number, { transport: 'sim' });
console.log(`Connected to ${band.name}, a pretend band with firmware ${band.info.firmware}`);
const most = await band.motionLimits();
console.log(`The most this link carries: ${most.both} a second with both sensors, ${most.accel} with accel alone`);

let count = 0;          // samples since the last line was printed
let lost = 0;
let last = null;        // the newest sample
band.on('motion', (m) => {
  count++;
  if (last && m.n > last.n + 1) lost += m.n - last.n - 1;      // a jump in n = that many samples lost
  last = m;
});
await band.configure({ motion: 'fastest', sensors: 'accel' }); // the accelerometer alone goes fastest
// The sensor's own clock is a few percent off, so a few more or fewer than this arrive each
// second. That is normal, not a fault: the `time` of each sample is the truth.
console.log(`Motion is on: ${band.streams.motion} samples per second`);

const timer = setInterval(() => {
  if (!last) return console.log('No samples yet.');
  const when = new Date(last.time).toISOString();              // `time` is real time, like Date.now()
  console.log(`${count} samples, ${lost} lost. Newest: z ${last.az.toFixed(2)} g at ${when}`);
  count = 0;
}, 1000);
setTimeout(() => {
  clearInterval(timer);
  band.disconnect();
}, seconds * 1000 + 200);
