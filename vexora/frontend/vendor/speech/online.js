// Live transcript's link to Deepgram, as the dashboard page uses it. The code that talks to
// the company (deepgram.js) does not run in the dashboard page: the dashboard's security
// policy only allows the server the dashboard came from, and stays that way. It runs in a
// small hidden page of its own (an iframe) with its own policy: that one company, nothing
// else. The hidden page exists only between start() and stop().
//
//   link.start({ onText, onState, onError })
//       onText({ final, text, end })   final: false = the sentence being spoken; true = a finished sentence
//       onState('listening')           connected: sound may be sent now
//       onError(message)               it has stopped by itself; the message says why
//   link.packet(opus)                  one Opus packet from the band
//   link.restart()                     the band's stream started again
//   link.stop()
import { COMPANY, ONLINE_PROBLEMS, NO_KEY } from './pure.js';

const HERE = import.meta.url.slice(0, import.meta.url.lastIndexOf('/') + 1);    // this folder
const FRAME = 'deepgram-frame.html';      // the hidden page: a fixed file in this folder
const READY_MS = 10000;           // the hidden page must load within this time
const STOP_MS = 3000;             // after stop(), the hidden page is removed at the latest after this

/** @param {string} key the key the student typed. It is handed to the hidden page and to nothing else. */
export function onlineLink(key) {
  let frame = null;
  let port = null;
  let on = null;
  let timer = 0;
  let listener = null;

  function close(problem) {
    clearTimeout(timer);
    if (listener) window.removeEventListener('message', listener);
    listener = null;
    if (port) port.onmessage = null;
    port?.close();
    port = null;
    frame?.remove();              // this also closes its connection
    frame = null;
    const callbacks = on;
    on = null;
    if (problem) callbacks?.onError?.(problem);
  }
  // The hidden page reports a problem as one fixed word; the sentence shown comes from our own list.
  const sentence = (word) => (word === 'no-key' ? NO_KEY : ONLINE_PROBLEMS[word] ?? ONLINE_PROBLEMS.closed);

  return {
    start(callbacks) {
      if (frame) return;
      on = callbacks;
      const mine = document.createElement('iframe');
      frame = mine;
      mine.hidden = true;
      mine.title = `Link to ${COMPANY}`;
      listener = (event) => {
        if (event.source !== mine.contentWindow || event.origin !== window.location.origin) return;
        if (event.data?.type !== 'vexo-deepgram-ready' || port) return;
        clearTimeout(timer);
        const channel = new MessageChannel();
        port = channel.port1;
        port.onmessage = ({ data }) => {
          if (data.type === 'text') on?.onText?.({ final: data.final === true, text: String(data.text ?? ''), end: data.end });
          else if (data.type === 'state' && data.state === 'off') close();
          else if (data.type === 'state' && data.state === 'listening') on?.onState?.('listening');
          else if (data.type === 'error') close(sentence(data.problem));
        };
        // The key goes to our own hidden page only: same origin, that window, nothing else in the message.
        mine.contentWindow.postMessage({ type: 'vexo-deepgram-start', key }, window.location.origin, [channel.port2]);
      };
      window.addEventListener('message', listener);
      timer = setTimeout(() => close(ONLINE_PROBLEMS.frame), READY_MS);
      mine.src = HERE + FRAME;
      document.body.append(mine);
    },

    packet(opus) {
      port?.postMessage({ type: 'packet', opus });
    },

    restart() {
      port?.postMessage({ type: 'restart' });
    },

    /** Ask for the last words and take the hidden page away. No sound is handed in after this. */
    stop() {
      if (!frame) return;
      on = null;                  // whoever stopped it does not want to hear from it again
      if (!port) {
        close();
        return;
      }
      port.postMessage({ type: 'stop' });
      const closing = port;
      port = null;                // packet() and restart() now do nothing
      clearTimeout(timer);
      closing.onmessage = ({ data }) => {
        if (data.type === 'state' || data.type === 'error') {
          port = closing;
          close();
        }
      };
      timer = setTimeout(() => {
        port = closing;
        close();
      }, STOP_MS);
    },
  };
}
