// off() refuses a name that is not an event, like on(). And band number 0 ("number not
// set") cannot be asked for, except over the USB cable, where an organiser numbers a band.
import test from 'node:test';
import assert from 'node:assert/strict';
import { Band, SimTransport, Transport, codec } from '../src/index.js';
import { parseBandParam } from '../../dashboard/logic.js';

const ZERO = 'Band numbers are 1 to 999. 0 is what a band has before it gets a number: ask an organiser to number it.';

test('off() with a name that is not an event throws bad-event, in the words of on()', async () => {
  const band = await Band.connect(42, { transport: new SimTransport(42, { realtime: false }) });
  const handler = () => {};
  for (const name of ['tapp', 'mic_level', 'doubleTap', '', null, undefined, 7]) {
    let fromOn = null;
    try { band.on(name, handler); } catch (e) { fromOn = e; }
    assert.equal(fromOn?.code, 'bad-event', String(name));
    assert.throws(() => band.off(name, handler), (e) => e.code === 'bad-event' && e.message === fromOn.message, String(name));
  }
  assert.throws(() => band.off('mic_level', handler), /Did you mean "mic-level"\?/);
});

test('off() with a real event still works, and a handler that was never added is no error', async () => {
  const sim = new SimTransport(42, { realtime: false });
  const band = await Band.connect(42, { transport: sim });
  const seen = [];
  const handler = (e) => seen.push(e);
  const stop = band.on('battery', handler);
  band.off('battery', () => {});               // never added: nothing happens
  band.off('tap', handler);                    // added for another event: nothing happens
  sim.onFrame(codec.encodeStream('battery', { percent: 50, state: 0, millivolts: 3900 }));
  assert.equal(seen.length, 1);
  band.off('battery', handler);
  stop();                                      // the function on() returned: safe after off()
  sim.onFrame(codec.encodeStream('battery', { percent: 50, state: 0, millivolts: 3900 }));
  assert.equal(seen.length, 1);
});

/** A transport that only notes whether anybody tried to open it. */
class Watched extends Transport {
  constructor(kind) {
    super();
    this.kind = kind;
    this.opened = 0;
  }

  async open() { this.opened++; }

  async send(frame) {
    const cmd = codec.decodeCommand(frame);
    const data = { GET_INFO: { proto_major: 1, proto_minor: 0, fw_major: 1, fw_minor: 0, fw_patch: 0, hw: 1, band_no: 0, caps: 0x7ff, max_frame: 244, uptime_ms: 5 } }[cmd.opcode];
    this.onFrame(codec.encodeResponse(cmd.seq, cmd.opcode, 'OK', data));
  }
}

test('Band.connect(0) is refused over Bluetooth and with the pretend band, before any connecting', async () => {
  const refused = (e) => {
    assert.equal(e.name, 'VexoError');
    assert.equal(e.code, 'bad-arg');
    assert.equal(e.message, ZERO);
    return true;
  };
  for (const number of [0, '0', '00', '000']) {
    await assert.rejects(Band.connect(number), refused, 'Bluetooth is the default');
    await assert.rejects(Band.connect(number, { transport: 'ble' }), refused);
    await assert.rejects(Band.connect(number, { transport: 'sim' }), refused);
  }
  for (const kind of ['ble', 'sim', 'custom']) {
    const transport = new Watched(kind);
    await assert.rejects(Band.connect(0, { transport }), refused, kind);
    assert.equal(transport.opened, 0, `${kind}: nothing was opened`);
  }
  const sim = new SimTransport(0, { realtime: false });       // a pretend band made with 0 stays possible for tests
  await assert.rejects(Band.connect(0, { transport: sim }), refused);
});

test('Band.connect(0) over the USB cable still reaches a band that has no number yet', async () => {
  const transport = new Watched('serial');
  const band = await Band.connect(0, { transport });
  assert.equal(transport.opened, 1);
  assert.equal(band.number, 0);
  assert.equal(band.name, 'Vexo-HACK-00');
  await band.disconnect();
  // The usual way stays: no number at all, then setBandNumber.
  const any = await Band.connect(null, { transport: new SimTransport(0, { realtime: false, via: 'usb' }) });
  assert.equal(any.number, 0);
  assert.equal(await any.setBandNumber(12), 12);
  assert.equal(any.name, 'Vexo-HACK-12');
});

test('every student entry point that reads a number ends at the same refusal for 0', async () => {
  // The readers themselves take "0" as the number 0 (it is the contract's "not set");
  // Band.connect is the one place that refuses it, so the sentence is the same everywhere.
  for (const text of ['0', '00', '000']) assert.equal(codec.parseBandNumber(text), 0);
  assert.equal(codec.bandNumberOf('Vexo-HACK-00'), 0);
  // The dashboard: ?band=0, then Connect or "Try without a band".
  const chosen = parseBandParam('0');
  assert.deepEqual(chosen, { number: 0, name: 'Vexo-HACK-00' });
  await assert.rejects(Band.connect(chosen.number, { transport: new SimTransport(chosen.number), autoReconnect: true }), { code: 'bad-arg', message: ZERO });
  await assert.rejects(Band.connect(chosen.number, { autoReconnect: true }), { code: 'bad-arg', message: ZERO });
  // The example pages (examples/web/kit.js): the number box, with and without "Pretend band".
  const typed = codec.parseBandNumber('0') ?? codec.bandNumberOf('0');
  await assert.rejects(Band.connect(typed, { transport: 'ble' }), { message: ZERO });
  await assert.rejects(Band.connect(typed, { transport: new SimTransport(typed ?? 42) }), { message: ZERO });
});

test('other numbers are as before: 1 and 999 connect, 1000 and -1 are bad-arg with the usual sentence', async () => {
  for (const number of [1, 999]) {
    const band = await Band.connect(number, { transport: new SimTransport(number, { realtime: false }) });
    assert.equal(band.number, number);
  }
  for (const number of [1000, -1, 4.2]) {
    await assert.rejects(Band.connect(number, { transport: 'sim' }), (e) => e.code === 'bad-arg' && /Use the number printed on the band/.test(e.message));
  }
});
