// USB cable from a web page (Chrome and Edge on laptops). No double-tap needed.
//
// Three things here follow from how the band treats a USB session (contract 2.2):
// - A band with firmware 1.1.3 ends the session of a computer that took no data for about
//   5 seconds while streams run, without a notice. The port stays open, commands are still
//   answered, but the streams are off. The library notices that (session-watch.js; this
//   file only reports an empty packet as a sign) and ends the link with reason
//   'session-ended'. With autoReconnect it is opened and set up again, with no click.
// - The read buffer is kept small on purpose (READ_BUFFER_BYTES below), so that a stuck
//   page really is seen as stuck by the band and the microphone goes off.
// - When the tab is closed or reloaded the port is closed first (pagehide), so the band
//   sees a proper close.
// The session end and the page close were tested with the pretend band and fake ports only,
// not yet with a real band on firmware 1.1.3.
import { Transport, VexoError } from '../transport.js';
import { Deframer, frameForUsb } from '../codec.js';

// Which ports the chooser lists. A page can only filter on a USB vendor / product ID (not
// on the name "Vexo Open band"), and the band's IDs are not final (contract 2.2). A filter
// on IDs that change later would hide the band completely, so every serial port is shown
// and the port is checked after it was picked instead (see notTheBand below).
const PORT_FILTERS = [];

/**
 * Why the port that was picked cannot be a band, or null if it could be one.
 * The band is always a USB device. A port with no USB vendor ID is something built into
 * the computer (on a Mac: "Bluetooth-Incoming-Port", "debug-console"), so it is refused
 * before it is opened. A USB port with other IDs is let through: the IDs are not final,
 * and a port that is not a band simply never answers.
 * @param {{usbVendorId?: number, usbProductId?: number}} info what port.getInfo() returned
 */
export function notTheBand(info) {
  if (typeof info?.usbVendorId === 'number') return null;
  return 'That port is not a band: it is not a USB device. Connect again and pick "Vexo Open band". '
    + 'Its port is called cu.usbmodem... on a Mac, COM and a number on Windows, ttyACM... on Linux. '
    + 'Not "Bluetooth-Incoming-Port" and not "debug-console". If the band is not in the list, try another cable (some only charge).';
}

// The band ignores the baud rate, but the browser insists on one.
const ANY_BAUD_RATE = 115200;
// How many bytes the browser keeps for the page while the page is busy (the browser's own
// default is 255). Two rules pull in opposite directions:
// - Big enough for a normal hiccup. While a handler works or the browser tidies its memory
//   (say 250 ms) the page takes nothing. If the buffer is full for 200 ms the band pauses
//   and stream frames are lost (notice 'stream-dropped').
// - Small enough that a page that is really stuck is seen by the band. While the buffer
//   fills, the band still sees a reader. Only when it is full do the band's 5 seconds
//   start, and until they are over the microphone stays on for a page nobody looks at.
// The numbers, from the data rates in the contract (sections 4 and 5.2):
//
//   streams on                                  bytes/s   64 KiB    16 KiB   8 KiB    4 KiB
//   audio alone                                   3,200   20 s      5.1 s    2.6 s    1.3 s
//   audio + motion at 100 a second                4,470   15 s      3.7 s    1.8 s    0.9 s
//   fastest motion alone (833 both, 1666 accel)  10,650    6.2 s    1.5 s    0.77 s   0.38 s
//   audio + fastest motion + motion at 100       15,120    4.3 s    1.1 s    0.54 s   0.27 s
//
// (How long the buffer takes to fill. The framing adds about 2 percent, and the computer's
// own USB buffer adds a little time on top: a few kilobytes on Linux and Windows, more on a Mac.)
// With 64 KiB a stuck page kept the microphone on for about 25 seconds (20 + 5). 4 KiB
// leaves only 0.27 s at the highest rate, too close to a 250 ms hiccup. 8 KiB rides out
// half a second at the highest rate and 2.6 s with audio alone, and a stuck page loses its
// session after about 8 seconds with audio on (2.6 + 5.2), sooner with more data.
const READ_BUFFER_BYTES = 8192;

const NO_PORT_PICKED = 'No USB port picked. Plug the band in with a data cable (not a charge-only one) and try again. '
  + 'The band is listed as "Vexo Open band"; on a Mac its port is called /dev/cu.usbmodem... '
  + 'On a Mac, the first time a band is plugged in macOS may ask "Allow accessory to connect?"; until you press Allow '
  + '(or allow it under System Settings > Privacy & Security) no port appears.';

export class WebSerialTransport extends Transport {
  kind = 'serial';
  /** The band can end a USB session by itself: Band watches for it (session-watch.js). */
  sessionWatch = true;
  /** Assigned by Band. Called when an empty packet arrives in the middle of a session. */
  onSessionHint = () => {};

  /** null if Web Serial can be used here, otherwise a sentence explaining why not. */
  static unavailableReason() {
    const nav = globalThis.navigator;
    if (!nav?.serial) {
      if (typeof window === 'undefined') return "Web Serial only exists in browsers (Chrome or Edge), not in Node. In Node only the pretend band works: Band.connect(42, { transport: 'sim' }). "
        + 'For a real band use a web page or the Python library.';
      if (window.isSecureContext === false) return 'USB only works on https:// pages (or localhost).';
      return 'This browser has no Web Serial. Use Chrome or Edge on a laptop.';
    }
    return null;
  }

  async open() {
    const missing = WebSerialTransport.unavailableReason();
    if (missing) throw new VexoError('no-web-serial', missing);
    let port;
    try {
      // Must be called from a click.
      port = await navigator.serial.requestPort({ filters: PORT_FILTERS });
    } catch (e) {
      if (e.name === 'NotFoundError') throw new VexoError('cancelled', NO_PORT_PICKED);
      throw e;
    }
    // A browser without getInfo() cannot tell, so the port is tried.
    const why = port.getInfo ? notTheBand(port.getInfo()) : null;
    if (why) throw new VexoError('port-failed', why);
    await this._start(port);
  }

  /**
   * Open the port that was picked before, again. The browser already has permission for
   * it, so this needs no click and shows no chooser. (Used by auto-reconnect.)
   */
  async reopen() {
    if (!this._picked) return this.open();
    await this._release();                // the old, dead port, if it is still held
    const { port: old, ids } = this._picked;
    const ports = await navigator.serial.getPorts();      // every port this page may use
    const same = (port) => {
      const now = port.getInfo?.() ?? {};
      return now.usbVendorId === ids.usbVendorId && now.usbProductId === ids.usbProductId;
    };
    const port = ports.find((p) => p === old) ?? ports.find(same);
    if (!port) throw new VexoError('not-found-nearby', 'The band is not on the USB cable right now. Plug it in again.');
    await this._start(port);
  }

  async _start(port) {
    try {
      await port.open({ baudRate: ANY_BAUD_RATE, bufferSize: READ_BUFFER_BYTES });
      // Raising DTR tells the band that somebody is listening.
      await port.setSignals({ dataTerminalReady: true });
    } catch (e) {
      throw new VexoError('port-failed', `Could not open the USB port: ${e.message} Is another tab or program using it?`);
    }
    this.port = port;
    this._picked = { port, ids: port.getInfo?.() ?? {} };     // so reopen() can find it again
    this._closing = false;
    this._writer = this._writerLock = port.writable.getWriter();
    this._reader = port.readable.getReader();
    this._loop = this._readLoop();
    this._listenForPageHide();
    // The band drops bytes up to the first 00 after the port opens (contract 2.2), so
    // send one 00 now; otherwise it would throw our first command away.
    await this._writer.write(Uint8Array.of(0));
  }

  async _readLoop() {
    // The band only starts sending once DTR is up, so we are at the start of a frame and
    // must not skip the first one: it will be the answer to our first command.
    // This loop takes bytes all the time the port is open: nothing else in the library
    // waits in it (an answer that is awaited, or an async handler, does not hold it up).
    // Nothing piles up here: each frame goes to Band at once, the browser keeps at most
    // READ_BUFFER_BYTES for us, and the Deframer drops a packet that is too long to be a frame.
    const deframer = new Deframer({ skipFirst: false });
    // An empty packet (a 00 right after a 00) once frames have arrived: the band paused, or
    // it started a new session. Band is told, and looks whether the streams still send.
    let afterZero = false;
    let midSession = false;
    try {
      for (;;) {
        const { value, done } = await this._reader.read();
        if (done) break;
        let empty = false;
        for (const byte of value) {
          if (byte === 0 && afterZero && midSession) empty = true;
          afterZero = byte === 0;
        }
        for (const frame of deframer.push(value)) {
          midSession = true;
          this.onFrame(frame);
        }
        if (empty) this.onSessionHint();
      }
    } catch {
      // the cable was pulled
    }
    this._writer = null;
    this.onClose(this._closing ? 'closed' : 'lost');
  }

  async close() {
    if (!this.port) return;
    this._closing = true;
    await this._reader?.cancel().catch(() => {});
    await this._loop;
    await this._release();
  }

  /**
   * Close the port when the tab is closed, reloaded or leaves for another page. Without
   * this the page would simply vanish and the band would not see a proper close.
   * 'pagehide' is used and not 'beforeunload': a page can still cancel a beforeunload
   * ("Leave site?"), and then the port would be closed under a page that stays.
   */
  _listenForPageHide() {
    const page = globalThis.window;
    if (!page?.addEventListener || this._onPageHide) return;     // Node, or already listening
    this._onPageHide = () => {
      const port = this.port;
      if (!port) return;
      this._closing = true;
      // First, at once: lower DTR. The page may be gone before the steps of close() have all run.
      Promise.resolve().then(() => port.setSignals({ dataTerminalReady: false })).catch(() => {});
      this.close().catch(() => {});
    };
    page.addEventListener('pagehide', this._onPageHide);
  }

  /** Give the port back to the browser. */
  async _release() {
    const port = this.port;
    if (!port) return;
    this.port = null;
    if (this._onPageHide) {
      globalThis.window?.removeEventListener?.('pagehide', this._onPageHide);
      this._onPageHide = null;
    }
    try {
      this._reader?.releaseLock();
      this._writerLock?.releaseLock();
    } catch {
      // already released
    }
    await port.setSignals({ dataTerminalReady: false }).catch(() => {});   // DTR low = session closed
    await port.close().catch(() => {});
  }

  async send(frame) {
    if (!this._writer) throw new VexoError('not-connected', 'Not connected to a band.');
    await this._writer.write(frameForUsb(frame));   // COBS, then 00
  }
}
