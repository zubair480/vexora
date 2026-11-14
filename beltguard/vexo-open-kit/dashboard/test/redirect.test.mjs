// The kit's top page (index.html + redirect.js) sends a visitor on to the dashboard. An
// address can carry ?band=42, and a student may open it by double-click.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const kit = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const script = readFileSync(join(kit, 'redirect.js'), 'utf8');

/** Where redirect.js sends a visitor whose address ends in `search`. */
function sentTo(search) {
  const went = [];
  new Function('location', script)({ search, replace: (to) => went.push(to) });
  assert.equal(went.length, 1);
  return went[0];
}

test('redirect.js goes to the dashboard page by its file name and keeps the band number', () => {
  assert.equal(sentTo('?band=42'), 'dashboard/index.html?band=42');
  assert.equal(sentTo(''), 'dashboard/index.html');
  assert.equal(sentTo('?band=07&firmware=1.0'), 'dashboard/index.html?band=07&firmware=1.0');
});

test('opened by double-click it lands on a page, not on a list of files', () => {
  // file:// has no "index.html for a folder": a target that ends in / shows the folder's files
  for (const search of ['', '?band=42']) {
    const target = sentTo(search).split('?')[0];
    assert.doesNotMatch(target, /\/$/);
    assert.ok(existsSync(join(kit, target)), target);
  }
  // and that page has the "opened as a file" notice
  assert.match(readFileSync(join(kit, 'dashboard', 'index.html'), 'utf8'), /id="needs-server"/);
});

test('index.html loads redirect.js and its fallback link names the file too', () => {
  const html = readFileSync(join(kit, 'index.html'), 'utf8');
  assert.match(html, /<script src="redirect\.js"><\/script>/);
  const links = [...html.matchAll(/href="([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(links, ['dashboard/index.html']);
});
