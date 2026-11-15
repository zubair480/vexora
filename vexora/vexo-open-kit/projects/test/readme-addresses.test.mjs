// Opens every address the project READMEs give, the way a student does: the kit is inside
// my-project/ as my-project/vexo-open-kit/, one web server serves my-project/, and the
// pages are under /vexo-open-kit/projects/... The test builds that folder (a link to the
// kit, in a temporary place), opens each address in headless Chrome and connects to the
// pretend band. An address of a server started in the kit folder gives 404 here.
// (projects/test/test_readme_commands.py fetches the same addresses from the web server command the pages print.)
//   node --test projects/test/readme-addresses.test.mjs
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { skip, KIT, serveKit, startChrome } from './chrome.mjs';

const PROJECTS = join(KIT, 'projects');
const names = readdirSync(PROJECTS).filter((name) => existsSync(join(PROJECTS, name, 'README.md'))).sort();
const pages = names.filter((name) => existsSync(join(PROJECTS, name, 'index.html')));
const ADDRESS = /http:\/\/localhost:8000(\/[^\s<>`"')]*)/g;
/** Every address a text gives, without the full stop or comma of the sentence around it. */
const addresses = (text) => [...text.matchAll(ADDRESS)].map((found) => found[1].replace(/[.,]+$/, '')).filter((path) => path !== '/...');
/** The "opened as a file" help of a page, as text. */
const needsServer = (name) => readFileSync(join(PROJECTS, name, 'index.html'), 'utf8')
  .match(/<p id="needs-server">([\s\S]*?)<\/p>/)[1].replace(/<[^>]+>/g, '').replace(/\s+/g, ' ');

/** Where each address is given: [[place, path], ...]. */
function given() {
  const list = [];
  for (const place of ['README.md', ...names.map((name) => `${name}/README.md`)]) {
    for (const path of addresses(readFileSync(join(PROJECTS, place), 'utf8'))) list.push([place, path]);
  }
  for (const name of pages) for (const path of addresses(needsServer(name))) list.push([`${name}/index.html`, path]);
  return list;
}

let mine, server, chrome;
before(async () => {
  // my-project/ with the kit inside it, and next to the kit the copies "Make one your own" describes
  mine = join(mkdtempSync(join(tmpdir(), 'vexo-readme-')), 'my-project');
  mkdirSync(mine);
  try {
    symlinkSync(KIT, join(mine, 'vexo-open-kit'), 'dir');
  } catch {                                  // no right to make links (Windows): copy what the pages use
    for (const part of ['js/src', 'projects']) cpSync(join(KIT, part), join(mine, 'vexo-open-kit', part), { recursive: true });
  }
  for (const name of ['shared', ...pages]) cpSync(join(PROJECTS, name), join(mine, name), { recursive: true });
  server = await serveKit(mine);
  if (!skip) chrome = await startChrome();
});
after(async () => {
  await chrome?.close();
  server?.close();
  if (mine) rmSync(join(mine, '..'), { recursive: true, force: true });
});

test('every address in the READMEs and in the "opened as a file" help exists with the server in my-project/', async () => {
  const list = given();
  for (const [place, path] of list) {
    const answer = await fetch(server.origin + path);
    assert.equal(answer.status, 200, `${place}: http://localhost:8000${path}`);
    assert.match(await answer.text(), /<p id="needs-server">/, `${place}: ${path} is a project page`);
  }
  for (const name of pages) {                // each page inside the kit (with and without the pretend band), and as a copy
    const paths = list.map(([, path]) => path);
    for (const path of [`/vexo-open-kit/projects/${name}/?link=pretend`, `/vexo-open-kit/projects/${name}/`, `/${name}/`]) {
      assert.ok(paths.includes(path), `${path} is given somewhere`);
    }
    assert.match(needsServer(name), new RegExp(`then open http://localhost:8000/vexo-open-kit/projects/${name}/ \\(8000, or the port you started it with\\)`));
    assert.match(needsServer(name), /in your project folder \(my-project, the folder that holds vexo-open-kit\), run python3 -m http\.server 8000 --bind 127\.0\.0\.1 /);
  }
  // the address of a server started in the kit folder (what the pages said before) is a 404
  assert.equal((await fetch(`${server.origin}/projects/reaction-game/`)).status, 404);
});

test('every one of those pages starts and connects to the pretend band', { skip, timeout: 120000 }, async () => {
  const seen = new Set();
  for (const [place, path] of given()) {
    if (seen.has(path)) continue;
    seen.add(path);
    const page = await chrome.openPage(server.origin + path);
    assert.equal(await page.run('document.querySelector("#needs-server").hidden'), true, `${place}: ${path}`);
    if (!path.includes('link=pretend')) await page.run('document.querySelector("#link").value = "pretend"');
    await page.connectPretend();
    assert.deepEqual(page.problems, [], `${place}: ${path}`);
    await page.close();
  }
  assert.ok(seen.size >= 3 * pages.length, `${seen.size} addresses`);
});
