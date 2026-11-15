// The small web server of the page tests (chrome.mjs) serves one folder and nothing else:
// not the folder above it, and not a folder next to it whose name begins with the same letters.
//   node --test projects/test/test-server.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { request } from 'node:http';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, sep } from 'node:path';
import { isInside, serveKit } from './chrome.mjs';

test('isInside: the folder and what is in it, not its neighbours', () => {
  const kit = join(sep, 'home', 'ada', 'kit');
  assert.equal(isInside(kit, kit), true);
  assert.equal(isInside(kit, join(kit, 'projects', 'index.html')), true);
  assert.equal(isInside(kit + sep, join(kit, 'a.js')), true, 'a folder given with a separator at its end');
  assert.equal(isInside(kit, join(sep, 'home', 'ada', 'kit-private', 'notes.txt')), false, 'a neighbour whose name begins the same');
  assert.equal(isInside(kit, join(sep, 'home', 'ada', 'kitchen')), false);
  assert.equal(isInside(kit, join(sep, 'home', 'ada')), false);
  assert.equal(isInside(kit, join(kit, '..', 'kit-private', 'notes.txt')), false);
});

/** Ask for a path exactly as written (fetch would tidy the dots away first). */
const status = (origin, path) => new Promise((resolve, reject) => {
  const { hostname, port } = new URL(origin);
  request({ hostname, port, path, method: 'GET' }, (answer) => { answer.resume(); resolve(answer.statusCode); }).on('error', reject).end();
});

test('the test server refuses a folder next to the one it serves', async () => {
  const top = mkdtempSync(join(tmpdir(), 'vexo-server-'));
  mkdirSync(join(top, 'kit'));
  mkdirSync(join(top, 'kit-private'));
  writeFileSync(join(top, 'kit', 'page.html'), 'ours');
  writeFileSync(join(top, 'kit-private', 'notes.txt'), 'not ours');
  writeFileSync(join(top, 'above.txt'), 'not ours');
  const server = await serveKit(join(top, 'kit'));
  try {
    assert.equal(await status(server.origin, '/page.html'), 200);
    assert.equal(await status(server.origin, '/..%2Fkit-private%2Fnotes.txt'), 403);
    assert.equal(await status(server.origin, '/..%2Fabove.txt'), 403);
    assert.equal(await status(server.origin, '/..%2Fkit%2Fpage.html'), 200, 'out and in again is still inside');
    assert.equal(await status(server.origin, '/nothing.html'), 404);
  } finally {
    server.close();
    rmSync(top, { recursive: true, force: true });
  }
});
