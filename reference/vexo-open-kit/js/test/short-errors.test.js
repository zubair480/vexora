// In Node a VexoError that nobody catches ends the program with one sentence, as the Python
// library does. Seen with the first JavaScript block of docs/api.md, run unchanged in Node:
// it ended in a ten-line stack trace.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const library = new URL('../src/index.js', import.meta.url).href;

function runProgram(body, env = {}) {
  const folder = mkdtempSync(join(tmpdir(), 'short-errors-'));
  try {
    const file = join(folder, 'try.mjs');
    writeFileSync(file, `import { Band } from ${JSON.stringify(library)};\n${body}\n`);
    const done = spawnSync(process.execPath, [file], { encoding: 'utf8', timeout: 30000, env: { ...process.env, ...env } });
    return { code: done.status, out: done.stdout, err: done.stderr };
  } finally {
    rmSync(folder, { recursive: true, force: true });
  }
}

const FIRST_BLOCK = `const band = await Band.connect(42);          // "Vexo-HACK-42"
await band.setLed('#00ff00');
console.log('LED is', await band.getLed());
await band.disconnect();`;

test('the first block of docs/api.md, unchanged in Node: one sentence that says what to write, exit code 1', () => {
  const { code, out, err } = runProgram(FIRST_BLOCK);
  assert.equal(code, 1);
  assert.equal(out, '');
  const lines = err.trim().split('\n');
  assert.equal(lines.length, 1, err);
  assert.match(lines[0], /^Problem \(no-web-bluetooth\): Web Bluetooth only exists in browsers \(Chrome or Edge\), not in Node\. /);
  assert.match(lines[0], /Band\.connect\(42, \{ transport: 'sim' \}\)/);
});

test('with the pretend band the same block runs; a band problem later on is one sentence too', () => {
  const works = runProgram(FIRST_BLOCK.replace('Band.connect(42)', "Band.connect(42, { transport: 'sim' })"));
  assert.equal(works.code, 0, works.err);
  assert.match(works.out, /^LED is \{/);
  const refused = runProgram("const band = await Band.connect(42, { transport: 'sim' });\nawait band.configure({ micLevel: true });\nawait band.setLed('#ff0000');");
  assert.equal(refused.code, 1);
  assert.match(refused.err.trim(), /^Problem \(denied\): LED_SET: The microphone is on.*\.$/);
  assert.equal(refused.err.trim().split('\n').length, 1, refused.err);
});

test('VEXO_TRACEBACK=1 shows the stack trace, and a bug in the program always keeps its own', () => {
  const traced = runProgram(FIRST_BLOCK, { VEXO_TRACEBACK: '1' });
  assert.equal(traced.code, 1);
  assert.match(traced.err, /^VexoError: Web Bluetooth only exists/);
  assert.match(traced.err, /\n\s+at .*try\.mjs:\d+/);
  const bug = runProgram("const band = await Band.connect(42, { transport: 'sim' });\nawait band.disconnect();\nnoSuchFunction();");
  assert.equal(bug.code, 1);
  assert.match(bug.err, /ReferenceError: noSuchFunction is not defined/);
  assert.match(bug.err, /\n\s+at .*try\.mjs:\d+/);
});

test('a program that handles uncaught errors itself is left alone', () => {
  const own = runProgram("process.on('uncaughtException', (e) => { console.log('mine:', e.code); process.exit(3); });\nawait Band.connect(42);");
  assert.equal(own.code, 3);
  assert.equal(own.out.trim(), 'mine: no-web-bluetooth');
  assert.equal(own.err, '');
});
