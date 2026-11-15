// The Bluetooth contract guide (docs/bluetooth-contract.md and .html) against the contract's
// own test vectors.   node --test examples/test/*.mjs
//
// The visual page decodes frames with code written inside the page. That code is taken out
// of the page here and run on every vector in contract/test-vectors.json. The example
// frames printed in the Markdown page must be real vectors and decode to the values the
// page states. Nothing here can reach Bluetooth or a USB port.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const KIT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const read = (page) => readFileSync(join(KIT, page), 'utf8');
const html = read('docs/bluetooth-contract.html');
const guide = read('docs/bluetooth-contract.md');
const vectors = JSON.parse(read('contract/test-vectors.json'));

// the page's decoder: the script with id "codec" has no page code in it
const codec = html.match(/<script id="codec">\n([\s\S]*?)<\/script>/)[1];
const scope = {};
new Function('globalThis', codec)(scope);
const { decodeFrame, COMMANDS, STREAMS, STATUSES, STREAM_BITS } = scope.VexoContract;
const real = new Set(['commands', 'responses', 'notices', 'streams'].flatMap((kind) => vectors[kind].map((v) => v.hex)));

test('the page decoder: every command in the test vectors', () => {
  assert.equal(vectors.commands.length, 37);
  for (const v of vectors.commands) {
    const got = decodeFrame(v.hex);
    assert.ok(got, v.hex);
    assert.deepEqual({ name: got.value.opcode, seq: got.value.seq, args: got.value.args }, { name: v.name, seq: v.seq, args: v.args }, v.hex);
  }
});

test('the page decoder: every response, notice and data frame in the test vectors', () => {
  let count = 0;
  for (const kind of ['responses', 'notices', 'streams']) {
    for (const v of vectors[kind]) { assert.deepEqual(decodeFrame(v.hex)?.value, v.value, `${kind} ${v.hex}`); count++; }
  }
  assert.equal(count, 51 + 7 + 26);
});

test('the page decoder: every invalid frame is refused, and nonsense does not throw', () => {
  for (const v of vectors.invalid) assert.equal(decodeFrame(v.hex), null, v.note);
  for (const text of ['xyz', '0', '   ', 'ff'.repeat(245)]) assert.equal(decodeFrame(text), null, text);
  assert.deepEqual(decodeFrame('01 07 50 00 FF 00 80 01 E8 03').value, decodeFrame('01075000ff008001e803').value);
  assert.deepEqual(decodeFrame('0x12,0x60,0xea,0x00,0x00,0x40,0xe2,0x01,0x00,0x70,0x01,0x54,0x01').value.steps_total, 123456);
});

test('the page decoder: every field has its place, and the fields cover the whole frame', () => {
  for (const hex of real) {
    const got = decodeFrame(hex);
    let at = 0;
    for (const f of got.fields) { assert.equal(f.at, at, `${hex}: ${f.name}`); at += f.size; }
    if (got.value.frame !== 'unknown') assert.equal(at, hex.length / 2, hex);
  }
});

test('the tables in the page are the contract: opcodes, statuses and stream bits', () => {
  assert.deepEqual(Object.fromEntries(Object.entries(COMMANDS).map(([op, c]) => [c.name, Number(op)])), vectors.opcodes);
  assert.deepEqual(STATUSES.map((s) => s[0]), vectors.statuses);
  assert.deepEqual(Object.fromEntries(STREAM_BITS.map((name, bit) => [name, bit])), vectors.stream_bits);
  for (const [type, stream] of Object.entries(STREAMS)) assert.equal(Number(type) - 0x10, vectors.stream_bits[stream.frame], stream.name);
  for (const id of Object.values(vectors.uuids)) { assert.ok(html.includes(id), id); assert.ok(guide.includes(id), id); }
});

test('the Markdown page: every example frame is a real vector and decodes to the values printed next to it', () => {
  const examples = [...guide.matchAll(/^Example\. .*?: `([0-9a-f]+)` decodes to: (.*)\.$/gm)];
  assert.ok(examples.length >= 60, `only ${examples.length} examples found`);
  for (const [, hex, stated] of examples) {
    assert.ok(real.has(hex), `${hex} is not in contract/test-vectors.json`);
    const fields = Object.fromEntries(decodeFrame(hex).fields.map((f) => [f.name, f.value]));
    const pairs = [...stated.matchAll(/`([\w.[\]]+)` (-?\d+|"[^"]*")/g)];
    assert.ok(pairs.length >= 2, hex);
    for (const [, name, value] of pairs) assert.deepEqual(JSON.parse(value), fields[name], `${hex}: ${name}`);
    assert.equal(pairs.length, Object.keys(fields).length - 1, `${hex}: every field but the type byte is printed`);
  }
  // every kind of data and every command has an example
  const shown = new Set(examples.map(([, hex]) => hex.slice(0, 2) === '01' || hex.slice(0, 2) === '02' ? hex.slice(0, 2) + hex.slice(4, 6) : hex.slice(0, 2)));
  for (const type of Object.keys(STREAMS)) assert.ok(shown.has(Number(type).toString(16)), `no example of stream type ${type}`);
  for (const hexInTable of guide.matchAll(/\| `(0[23][0-9a-f]{6,})` \|/g)) assert.ok(real.has(hexInTable[1]), hexInTable[1]);
});

test('the visual page shows the same example frames, and loads nothing from anywhere', () => {
  const inPage = [...html.matchAll(/data-hex="([0-9a-f]+)"/g)].map((m) => m[1]);
  const inGuide = [...guide.matchAll(/^Example\. .*?: `([0-9a-f]+)` decodes to: /gm)].map((m) => m[1]);
  assert.deepEqual(inPage, inGuide);
  for (const hex of inPage) assert.ok(real.has(hex), hex);
  assert.doesNotMatch(html, /<(script|link|img|iframe|audio|video|source)\b[^>]*\s(src|href)=/i);
  assert.doesNotMatch(html, /@import|url\(|fetch\(|XMLHttpRequest|WebSocket|sendBeacon/);
  assert.equal([...html.matchAll(/<script/g)].length, 2);
  // every heading of the Markdown page is in the visual page, under the same anchor
  const slug = (t) => t.trim().toLowerCase().replace(/[^\w\- ]/g, '').replace(/ /g, '-');
  for (const [, title] of guide.matchAll(/^#{2,3} (.*)$/gm)) assert.ok(html.includes(` id="${slug(title)}"`), title);
});

test('connecting with your own code: both examples filter on the exact band name and the service', async () => {
  const part = guide.split('\n## Connecting with your own Bluetooth code\n')[1].split('\n## ')[0];
  assert.match(part, /\*\*Always filter for your own band's name\. Never connect to the first Vexo band you see\.\*\*/);
  const js = part.match(/```js\n([\s\S]*?)```/)[1];
  const py = part.match(/```python\n([\s\S]*?)```/)[1];
  assert.match(py, /adv\.local_name == NAME/);
  assert.match(py, /service_uuids=\[SERVICE\]/);
  assert.match(py, /NAME = "Vexo-HACK-%02d" % BAND/);
  // the web page example, run against a pretend browser: what it asks for, and what it writes
  const asked = []; const written = []; const heard = [];
  const tx = { addEventListener: (name, fn) => { tx.fn = fn; }, startNotifications: async () => { tx.listening = true; } };
  const rx = { writeValueWithResponse: async (bytes) => { assert.ok(tx.listening, 'it listens before it asks'); written.push(Array.from(bytes)); } };
  const service = { getCharacteristic: async (id) => (id === vectors.uuids.rx ? rx : id === vectors.uuids.tx ? tx : assert.fail(id)) };
  const device = { addEventListener() {}, gatt: { connect: async () => ({ getPrimaryService: async (id) => { assert.equal(id, vectors.uuids.service); return service; } }) } };
  const navigator = { bluetooth: { requestDevice: async (options) => { asked.push(options); return device; } } };
  const connect = new Function('navigator', 'console', `${js}\nreturn connect;`)(navigator, { log: (...a) => heard.push(a.join(' ')) });
  await connect();
  assert.deepEqual(asked, [{ filters: [{ name: 'Vexo-HACK-42', services: [vectors.uuids.service] }], optionalServices: [vectors.uuids.service] }]);
  assert.deepEqual(written, [[1, 0, 1]]);                       // GET_INFO, the first command vector
  assert.equal(vectors.commands[0].hex, '010001');
  const answer = Uint8Array.from(Buffer.from(vectors.responses[0].hex, 'hex'));
  tx.fn({ target: { value: new DataView(answer.buffer) } });
  assert.match(heard[0], /^frame 02 00 01 00 01 00/);
  for (const [number, name] of [[7, 'Vexo-HACK-07'], [42, 'Vexo-HACK-42'], [999, 'Vexo-HACK-999']]) {
    assert.equal('Vexo-HACK-' + String(number).padStart(2, '0'), name);
    assert.ok(vectors.names.some((n) => n.number === number && n.name === name));
  }
});
