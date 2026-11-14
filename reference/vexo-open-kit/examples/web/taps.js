// Taps: one tap turns the light green, a double tap turns it pink.
import { Band } from '../../js/src/index.js';
import { bandNumber, transport, onConnect, say } from './kit.js';

const log = document.querySelector('#log');
onConnect(async () => {
  const band = await Band.connect(bandNumber(), { transport: transport() });
  band.on('tap', () => band.setLed('#00ff00'));
  band.on('double-tap', () => band.setLed('#ff00aa'));
  band.on('event', (e) => { log.textContent = `${e.kind}\n${log.textContent}`.split('\n').slice(0, 20).join('\n'); });   // the newest 20
  await band.configure({ events: true });
  say(`Connected to ${band.name}. Tap it.`);
  return band;
});
