// Runs inside the small hidden page deepgram-frame.html. That page's security policy lets
// it contact Deepgram's one address and nothing else; the dashboard around it cannot
// contact any company at all. The dashboard hands the band's packets in through a message
// port, and the text comes back the same way. online.js is the other end.
import { deepgramLink } from './deepgram.js';

let started = false;

window.addEventListener('message', (event) => {
  // Only the dashboard that made this page may talk to it, and only once.
  if (event.origin !== window.location.origin || event.source !== window.parent) return;
  if (event.data?.type !== 'vexo-deepgram-start' || started || !event.ports[0]) return;
  started = true;
  const port = event.ports[0];
  // The key is the only thing taken from the message. Where the sound goes and what is
  // asked for are fixed in deepgram.js and again in this page's policy.
  const link = deepgramLink({ key: String(event.data.key ?? '') });
  port.onmessage = ({ data }) => {
    if (data?.type === 'packet') link.packet(data.opus);
    else if (data?.type === 'restart') link.restart();
    else if (data?.type === 'stop') link.stop();
  };
  link.start({
    onText: (text) => port.postMessage({ type: 'text', final: text.final, text: text.text, end: text.end }),
    onState: (state) => port.postMessage({ type: 'state', state }),
    onError: (problem) => port.postMessage({ type: 'error', problem }),
  });
});

window.parent.postMessage({ type: 'vexo-deepgram-ready' }, window.location.origin);
