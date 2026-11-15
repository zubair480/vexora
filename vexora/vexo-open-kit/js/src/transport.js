// A transport moves whole frames between the computer and one band. It knows nothing
// about what the frames mean (that is codec.js). To add a new way to reach a band, write
// an object with this shape and pass it to Band.connect(number, { transport }).
//
//   kind                      'ble', 'serial' or 'sim' (shown as band.transportKind)
//   async open()              connect; throw VexoError if it cannot
//   async close()             disconnect
//   async reopen()            optional: connect to the same band again without asking the
//                             person (auto-reconnect uses it; without it, open() is called again)
//   async send(Uint8Array)    send one frame to the band
//   onFrame = (Uint8Array)    assigned by Band; call it once per frame received
//   onClose = (reason)        assigned by Band; call it once when the link ends
//   sessionWatch              optional: true = the band can end the session by itself while
//                             the link stays open (the USB cable). Band then watches for
//                             streams that went silent and ends the link with reason
//                             'session-ended' (transports/session-watch.js). Leave it out
//                             for any other link: nothing is watched and nothing extra is sent
//   onSessionHint = ()        optional, assigned by Band when the property exists; call it
//                             when an empty packet arrives in the middle of a session

export class VexoError extends Error {
  /**
   * @param {string} code short machine-readable reason, e.g. 'denied' (list in docs/api.md)
   * @param {string} message a sentence for the person at the keyboard
   */
  constructor(code, message) {
    super(message);
    this.name = 'VexoError';
    this.code = code;
  }
}

let shortErrorsOn = false;

/**
 * Node only: a VexoError that nobody catches ends the program with one sentence instead
 * of a stack trace, as the Python library does:
 *
 *   Problem (no-web-bluetooth): Web Bluetooth only exists in browsers. ...
 *
 * Band.connect switches this on by itself. Only VexoError is shown this way: its message is
 * written for the person at the keyboard. Every other error (a bug in the program) keeps
 * its stack trace. The program still ends with exit code 1. For the stack trace of a
 * VexoError too, set the environment variable VEXO_TRACEBACK=1. In a browser, and when
 * the program (or a test runner) handles uncaught errors itself, nothing is changed.
 */
export function shortErrors() {
  const proc = globalThis.process;
  if (shortErrorsOn || !proc?.versions?.node || typeof proc.on !== 'function') return;
  shortErrorsOn = true;
  const handler = (error) => {
    if (proc.listenerCount('uncaughtException') > 1) return;     // somebody else handles uncaught errors
    if (error?.name === 'VexoError' && !proc.env?.VEXO_TRACEBACK) console.error(`Problem (${error.code}): ${error.message}`);
    else console.error(error?.stack ?? error);                   // as Node itself would: the whole trace
    proc.exit(1);
  };
  proc.on('uncaughtException', handler);
}

/**
 * What to tell someone whose band cannot be found over Bluetooth, most likely reason first.
 * A band that is connected to anything is invisible to everything else, so a double-tap
 * alone cannot help a second program.
 * @param {string} name the band's name, e.g. "Vexo-HACK-42"
 */
export function notFoundHelp(name) {
  return `Could not see ${name}. ${ELSEWHERE_HELP} Then double-tap the band (its light blinks blue for a minute), keep it close, and try again.`;
}
/** The first thing to check when a band cannot be found. */
export const ELSEWHERE_HELP = "If something else is connected to it (the dashboard tab, another program, a teammate's laptop), "
  + 'close that first: the band takes one Bluetooth connection at a time and cannot be found while it is connected.';

export class Transport {
  kind = 'custom';
  onFrame = () => {};
  onClose = () => {};
  async open() { throw new Error('not implemented'); }
  async close() {}
  async send(_frame) { throw new Error('not implemented'); }
}
