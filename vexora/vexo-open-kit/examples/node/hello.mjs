// The JavaScript library in Node, with a pretend band. Type it in my-project/:
//   node vexo-open-kit/examples/node/hello.mjs 42                 42 = your band number
//   node vexo-open-kit/examples/node/hello.mjs 42 --seconds 10    stop after 10 seconds (5 if you leave it out)
// Node has no Web Bluetooth, so it cannot reach a real band: the number only names the
// pretend band. For a real band use a web page (examples/web) or Python (examples/python).
import { Band } from '../../js/src/index.js';
import { args } from './kit.mjs';

const { number, seconds } = args({ seconds: 5 });
const band = await Band.connect(number, { transport: 'sim' });
console.log(`Connected to ${band.name}, a pretend band`);
band.on('motion', (m) => console.log(`x ${m.ax.toFixed(2)}  y ${m.ay.toFixed(2)}  z ${m.az.toFixed(2)}`));
band.on('tap', () => console.log('tap!'));
await band.setLed('#00a0ff');
console.log('LED is', await band.getLed());
await band.configure({ motion: 10, events: true });
setTimeout(() => band.disconnect(), seconds * 1000);
