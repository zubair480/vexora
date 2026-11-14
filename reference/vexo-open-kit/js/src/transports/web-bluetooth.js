// Bluetooth from a web page (Chrome and Edge on laptops and Android; not iPhone/iPad,
// not Firefox, not Safari).
import { Transport, VexoError, ELSEWHERE_HELP } from '../transport.js';
import { SERVICE_UUID, RX_UUID, TX_UUID, MAX_FRAME, bandName } from '../codec.js';

/**
 * The result of `promise`, or an error after `seconds`, whichever comes first. Without this
 * a browser that never answers (no list of bands shows up, or a band that went out of
 * reach while connecting) would leave the page on "Connecting..." for ever.
 */
function within(promise, seconds, makeError) {
  let timer;
  const late = new Promise((_done, fail) => { timer = setTimeout(() => fail(makeError()), seconds * 1000); });
  return Promise.race([promise, late]).finally(() => clearTimeout(timer));
}

export class WebBluetoothTransport extends Transport {
  kind = 'ble';
  /** Seconds to wait for a band to be picked in the browser's list. A band can only be found for 60 seconds after a double-tap. */
  chooserSeconds = 120;
  /** Seconds to wait for the picked band to answer. */
  connectSeconds = 20;
  /**
   * true = the chooser lists every Vexo Open band nearby, not only the one with this band's name.
   * For a computer that remembers a band under an old name (a band that got a new number): there the
   * browser never shows it under the new name. The band's number is still checked after connecting.
   */
  listAll = false;

  /** @param {number|null} number band number; null = let the chooser list every Vexo Open band */
  constructor(number = null) {
    super();
    this.bandName = number === null ? null : bandName(number);
    this._writes = Promise.resolve();   // writes wait here so they go out one at a time
    this._closing = false;
    this._rx = null;                    // the characteristic we write to, while connected
  }

  /** null if Web Bluetooth can be used here, otherwise a sentence explaining why not. */
  static unavailableReason() {
    const nav = globalThis.navigator;
    if (!nav?.bluetooth) {
      if (typeof window === 'undefined') return "Web Bluetooth only exists in browsers (Chrome or Edge), not in Node. In Node only the pretend band works: Band.connect(42, { transport: 'sim' }). "
        + 'For a real band use a web page or the Python library.';
      if (window.isSecureContext === false) return 'Bluetooth only works on https:// pages (or localhost).';
      return 'This browser has no Web Bluetooth. Use Chrome or Edge on a laptop or an Android phone. iPhone and iPad browsers cannot connect.';
    }
    return null;
  }

  async open() {
    const why = WebBluetoothTransport.unavailableReason();
    if (why) throw new VexoError('no-web-bluetooth', why);
    const who = this.bandName ?? 'the band';
    let device;
    try {
      // Must be called from a click. The chooser only lists Vexo Open bands (the service
      // UUID), and only the one with this name when a number was given. The band sends the
      // service in its first packet and the name in its scan response; the browser puts the
      // two together before it compares them with this filter.
      const filter = this.bandName && !this.listAll ? { name: this.bandName, services: [SERVICE_UUID] } : { services: [SERVICE_UUID] };
      device = await within(
        navigator.bluetooth.requestDevice({ filters: [filter], optionalServices: [SERVICE_UUID] }),
        this.chooserSeconds,
        () => new VexoError('cancelled', `No band was picked within ${this.chooserSeconds} seconds. If the browser's list of bands is still open, close it. `
          + `Was ${who} not in the list? ${ELSEWHERE_HELP} `
          + `Then double-tap ${who} to wake it (it blinks blue) and try again. If no list showed up at all, check that Bluetooth is switched on and reload the page.`),
      );
    } catch (e) {
      if (e.name === 'NotFoundError') throw new VexoError('cancelled', `No band picked. Was ${who} not in the list? ${ELSEWHERE_HELP} `
        + `Then double-tap ${who} to wake it (it blinks blue), and try again.`);
      throw e;
    }
    this.device = device;
    // One listener for the whole life of the device, however often we connect to it.
    device.addEventListener('gattserverdisconnected', () => {
      if (device.gatt?.connected) return;        // an old drop reported late, after we are back
      const wasUp = this._rx !== null;
      this._rx = null;
      if (wasUp) this.onClose(this._closing ? 'closed' : 'lost');
    });
    await this._connect();
  }

  /**
   * Connect again to the band that was picked before. The browser already has permission
   * for it, so this needs no click and shows no chooser. (Used by auto-reconnect.)
   */
  async reopen() {
    if (!this.device) return this.open();
    await this._connect();
  }

  async _connect() {
    const device = this.device;
    this._closing = false;
    this._writes = Promise.resolve();
    let gaveUp = false;
    const steps = async () => {
      const server = await device.gatt.connect();
      const service = await server.getPrimaryService(SERVICE_UUID);
      const rx = await service.getCharacteristic(RX_UUID);      // we write commands here
      const tx = await service.getCharacteristic(TX_UUID);      // the band answers here
      if (gaveUp) return device.gatt?.disconnect();             // answered too late: let the band go again
      // Each notification is handed to Band at once: nothing is kept here, so nothing can pile up.
      tx.addEventListener('characteristicvaluechanged', (ev) => {
        const v = ev.target.value;
        this.onFrame(new Uint8Array(v.buffer.slice(v.byteOffset, v.byteOffset + v.byteLength)));
      });
      // Listen before the first command, or its answer could be missed.
      await tx.startNotifications();
      if (!gaveUp) this._rx = rx;
    };
    try {
      await within(steps(), this.connectSeconds, () => {
        gaveUp = true;
        return new Error(`The band did not answer within ${this.connectSeconds} seconds.`);
      });
    } catch (e) {
      this._rx = null;
      device.gatt?.disconnect();
      throw new VexoError('not-found-nearby', `Could not connect to ${device.name ?? this.bandName ?? 'the band'}: ${e.message} `
        + 'Double-tap the band to wake it, keep it close, and try again.');
    }
  }

  async close() {
    this._closing = true;
    this.device?.gatt?.disconnect();
  }

  send(frame) {
    const write = this._writes.then(() => {
      if (!this._rx) throw new VexoError('not-connected', 'Not connected to a band.');
      if (frame.length > MAX_FRAME) throw new VexoError('bad-arg', `A frame can be at most ${MAX_FRAME} bytes.`);
      return this._rx.writeValueWithResponse(frame);
    });
    this._writes = write.catch(() => {});
    return write;
  }
}
