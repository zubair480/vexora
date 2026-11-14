// Live transcript through Deepgram, a speech-to-text company on the internet. One
// WebSocket: the band's Opus packets go up as they are, text comes back while people speak.
//
// This file runs only inside the small hidden page deepgram-frame.html, whose own security
// policy allows this one address and nothing else. Everything the band's microphone hears
// is sent to Deepgram while it is on.
//
// Where the sound goes and what is asked for are fixed here. Nothing from outside (a
// message, the page's address, storage) can change them.
const ADDRESS = 'wss://api.deepgram.com/v1/listen';
const QUERY = 'model=nova-3&language=en&encoding=opus&sample_rate=16000&channels=1'
  + '&interim_results=true&smart_format=true&endpointing=100&mip_opt_out=true';
// model=nova-3 language=en      Deepgram's general model, English
// encoding=opus sample_rate=16000 channels=1
//                               the band's packets exactly as they arrive: nothing is decoded here
// interim_results=true          first guesses while someone is still speaking
// smart_format=true             punctuation, capital letters, "42" for numbers
// endpointing=100               a pause of 0.1 s ends a sentence (measured on band recordings: 300 made the
//                               finished sentence come seconds late, the default cuts in mid-phrase)
// mip_opt_out=true              Deepgram may not use this sound to improve its models
export const DEEPGRAM_ADDRESS = `${ADDRESS}?${QUERY}`;

const KEEP_ALIVE_MS = 4000;       // Deepgram closes after 10 s without sound; it asks for a KeepAlive every 3 to 5 s
const CLOSE_WAIT_MS = 1500;       // how long stop() waits for the last words
const LONG_LINE = 300;            // letters; a sentence that never pauses is cut here
const MOST_TEXT = 2000;           // letters taken from one message of the service

/** Takes every copy of the key out of a message before it is shown. */
export const withoutKey = (text, key) => (key ? String(text).split(key).join('[key]') : String(text));
/** Joins the pieces of a sentence that arrive one by one. */
export const joinText = (parts) => parts.map((p) => p.trim()).filter(Boolean).join(' ');

/**
 * @param {object} options
 * @param {string} options.key a Deepgram API key
 * @param {Function} [options.WebSocket] @param {number} [options.keepAliveMs] @param {number} [options.closeWaitMs]
 *   only for the tests: a pretend socket and shorter waits. There is no option for the address.
 */
export function deepgramLink({ key, WebSocket: Socket = globalThis.WebSocket, keepAliveMs = KEEP_ALIVE_MS, closeWaitMs = CLOSE_WAIT_MS } = {}) {
  let socket = null;
  let open = false;
  let stopping = false;
  let on = null;                  // the callbacks given to start()
  let done = [];                  // finished pieces of the sentence being spoken
  let spokenUntil = null;         // where the last word of those pieces ended, in seconds of sound sent
  let lastSent = 0;
  let keepAlive = 0;
  let closeTimer = 0;

  function send(data) {
    if (!open) return;
    try {
      socket.send(data);
      if (typeof data !== 'string') lastSent = Date.now();
    } catch {
      // the socket is closing; its close handler reports that
    }
  }

  // `problem` is one of the fixed words 'refused' | 'closed' | 'reported' | 'no-key' | 'no-socket', never text from the service.
  function end(problem) {
    clearInterval(keepAlive);
    clearTimeout(closeTimer);
    const callbacks = on;
    const old = socket;
    socket = null;
    on = null;
    open = false;
    if (old) {
      old.onopen = old.onmessage = old.onerror = old.onclose = null;
      try {
        old.close();
      } catch {
        // closed already
      }
    }
    if (!callbacks) return;
    if (problem) callbacks.onError?.(problem);
    else callbacks.onState?.('off');
  }

  const number = (value) => (Number.isFinite(value) ? value : null);
  const say = (final, text, until) => on?.onText?.({ final, text: withoutKey(text, key).slice(0, MOST_TEXT), end: until });

  function result(message) {
    const best = message.channel?.alternatives?.[0];
    const piece = typeof best?.transcript === 'string' ? best.transcript : '';
    const pieceEnd = number(message.start + message.duration);
    if (!message.is_final) {
      // A first guess for the newest piece; it may still change.
      const live = joinText([...done, piece]);
      if (live) say(false, live, pieceEnd);
      return;
    }
    if (piece) {
      done.push(piece);
      spokenUntil = number(best.words?.at?.(-1)?.end) ?? pieceEnd;
    }
    // speech_final = Deepgram heard a pause: the sentence is complete.
    if (message.speech_final || joinText(done).length > LONG_LINE) finish();
    else if (done.length) say(false, joinText(done), pieceEnd);
  }

  function finish() {
    const line = joinText(done);
    done = [];
    if (line) say(true, line, spokenUntil);
    spokenUntil = null;
  }

  function received(data) {
    if (typeof data !== 'string') return;
    let message;
    try {
      message = JSON.parse(data);
    } catch {
      return;
    }
    if (message?.type === 'Results') result(message);
    else if (message?.type === 'Error') end('reported');
    else if (message?.type === 'Metadata' && stopping) {      // the last message after CloseStream
      finish();
      end();
    }
  }

  return {
    /**
     * @param {object} callbacks
     * @param {(t: { final: boolean, text: string, end: number|null }) => void} callbacks.onText
     *   end: how many seconds of the sound sent so far the text covers
     * @param {(state: 'listening'|'off') => void} [callbacks.onState]
     * @param {(problem: string) => void} [callbacks.onError] it has stopped by itself; one fixed word says why
     */
    start(callbacks) {
      if (socket) return;
      on = callbacks;
      stopping = false;
      done = [];
      if (!key) return end('no-key');
      if (typeof Socket !== 'function') return end('no-socket');
      try {
        // A browser cannot add a header to a WebSocket, so Deepgram reads the key from the
        // "protocol" field of the request instead. It is never part of the address.
        socket = new Socket(DEEPGRAM_ADDRESS, ['token', key]);
      } catch {
        return end('refused');    // the browser's own message could repeat the key, so it is not used
      }
      socket.binaryType = 'arraybuffer';
      socket.onopen = () => {
        open = true;
        lastSent = Date.now();
        // While no sound arrives from the band, tell Deepgram we are still here.
        keepAlive = setInterval(() => {
          if (Date.now() - lastSent >= keepAliveMs) send(JSON.stringify({ type: 'KeepAlive' }));
        }, Math.min(1000, keepAliveMs));
        on?.onState?.('listening');
      };
      socket.onmessage = (event) => received(event.data);
      // The browser does not tell a page why a connection was refused.
      socket.onerror = () => {
        if (!open && !stopping) end('refused');     // once open, the close that follows is reported
      };
      socket.onclose = () => {
        if (stopping) {
          finish();
          return end();
        }
        return end(open ? 'closed' : 'refused');
      };
      return undefined;
    },

    /** One Opus packet from the band, sent as it is: one packet, one message. */
    packet(opus) {
      if (stopping || !opus?.length) return;
      send(opus);
    },

    /** The band's stream started again: what was being said is over. */
    restart() {
      if (!open || stopping) return;
      send(JSON.stringify({ type: 'Finalize' }));
    },

    /** Ask for the last words, and close. */
    stop() {
      if (!socket || stopping) return;
      if (!open) {
        end();
        return;
      }
      stopping = true;
      clearInterval(keepAlive);
      send(JSON.stringify({ type: 'CloseStream' }));
      closeTimer = setTimeout(() => {
        finish();
        end();
      }, closeWaitMs);
    },
  };
}
