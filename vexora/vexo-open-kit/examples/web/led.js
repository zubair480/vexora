// LED: blink green after connecting, then show whatever colour you pick.
import { Band } from '../../js/src/index.js';
import { bandNumber, transport, onConnect, say } from './kit.js';

let band;
onConnect(async () => {
  band = await Band.connect(bandNumber(), { transport: transport() });
  await band.setLed('#00ff00', { pattern: 'blink', period: 500 });   // blink twice a second
  say(`Connected to ${band.name}. It blinks green. Now pick a colour.`);
  return band;
});
document.querySelector('#colour').addEventListener('change', (e) => band?.setLed(e.target.value));
document.querySelector('#off').addEventListener('click', () => band?.ledOff());
