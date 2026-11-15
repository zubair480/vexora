// A fake navigator.serial for tests: one USB port with the pretend band behind it. The
// transport under test (web-serial.js) talks to it the way it talks to a browser's port;
// the bytes go to a SimTransport made with { via: 'usb' }, which answers like a band.
// It also runs in a web page (dashboard/test/session-check.mjs loads it there).
//
//   const fake = fakeSerialWithBand(42);
//   globalThis.navigator = fake.navigator;
//   const band = await Band.connect(42, { transport: 'serial' });
//   fake.sim.endUsbSession();           // direct the band behind the port
//   fake.log                            // what the transport asked of the port, in order
import { SimTransport } from '../src/transports/sim.js';

export function fakeSerialWithBand(number = 42, simOptions = {}) {
  const sim = new SimTransport(number, { via: 'usb', ...simOptions });
  const log = [];
  let waiting = null;         // the read() that waits for bytes: { resolve, reject }
  let queue = [];
  let open = false;
  const toComputer = (bytes) => {
    if (!open) return;
    const copy = Uint8Array.from(bytes);
    if (waiting) {
      const { resolve } = waiting;
      waiting = null;
      resolve({ value: copy, done: false });
    } else {
      queue.push(copy);
    }
  };
  sim.onWire = (direction, bytes) => { if (direction === 'to-computer') toComputer(bytes); };
  const reader = {
    read: () => new Promise((resolve, reject) => {
      if (queue.length) return resolve({ value: queue.shift(), done: false });
      if (!open) return resolve({ value: undefined, done: true });
      waiting = { resolve, reject };
      return undefined;
    }),
    cancel: async () => {
      log.push('reader.cancel');
      const was = waiting;
      waiting = null;
      queue = [];
      open = false;
      was?.resolve({ value: undefined, done: true });
    },
    releaseLock: () => {},
  };
  const writer = {
    write: async (bytes) => {
      if (!sim._open) throw new Error('The port is closed.');
      sim.writeBytes(Uint8Array.from(bytes));
    },
    releaseLock: () => {},
  };
  const port = {
    open: async (options) => {
      log.push(`open ${JSON.stringify(options)}`);
      queue = [];
      open = true;
      await sim.open();
    },
    setSignals: async (signals) => { log.push(`setSignals ${JSON.stringify(signals)}`); },
    close: async () => {
      log.push('close');
      open = false;
      await sim.close();
    },
    getInfo: () => ({ usbVendorId: 0x2fe3, usbProductId: 0x0004 }),
    readable: { getReader: () => reader },
    writable: { getWriter: () => writer },
  };
  const fake = {
    sim, log, port, requests: 0,
    navigator: {
      serial: {
        requestPort: async () => { fake.requests++; log.push('requestPort'); return port; },
        getPorts: async () => { log.push('getPorts'); return [port]; },
      },
    },
  };
  return fake;
}
