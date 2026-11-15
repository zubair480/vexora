// Rules every project in this folder keeps. No band and no browser needed.
//   node --test projects/test/*.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const PROJECTS = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (...parts) => readFileSync(join(PROJECTS, ...parts), 'utf8');
const folders = readdirSync(PROJECTS).filter((name) => statSync(join(PROJECTS, name)).isDirectory()).sort();
const projects = folders.filter((name) => !['shared', 'test'].includes(name));
const files = (folder) => readdirSync(join(PROJECTS, folder)).filter((name) => /\.(html|js|css|py|md)$/.test(name));

test('there are six projects, and the index lists each one with a difficulty', () => {
  assert.equal(projects.length, 6, projects.join(', '));
  const index = read('README.md');
  for (const name of projects) {
    const row = index.split('\n').find((line) => line.includes(`](${name}/)`));
    assert.ok(row, `${name} is in projects/README.md`);
    assert.match(row, /\b(Easy|Medium|Harder)\b/, `${name} has a difficulty hint`);
  }
});

for (const name of projects) {
  test(`${name}: a one-screen README with the same sections as the others`, () => {
    const text = read(name, 'README.md');
    assert.ok(text.split('\n').length <= 60, `${text.split('\n').length} lines: keep it to one screen`);
    for (const heading of ['## Run it', '## Read the code', '## Kit features it uses', '## Good to know', '## Try next']) {
      assert.ok(text.includes(heading), `${name}/README.md has "${heading}"`);
    }
    assert.match(text, /Pretend band:/);
    assert.match(text, /Real band:/);
    assert.match(text, /^\*\*(Easy|Medium|Harder)\. (Web page \(Chrome or Edge\)|Python)\.\*\*$/m);
  });

  test(`${name}: has a test, and never writes a band number into the code`, () => {
    const tests = readdirSync(join(PROJECTS, 'test')).join(' ');
    assert.ok(tests.includes(`${name}.test.mjs`) || tests.includes(`test_${name.replace('-', '_')}.py`), `a test for ${name}`);
    for (const file of files(name).filter((f) => /\.(js|py)$/.test(f))) {
      assert.doesNotMatch(read(name, file), /Band\.connect\(\s*\d/, `${name}/${file} connects to a fixed band number`);
      assert.doesNotMatch(read(name, file), /\.wipe\(/, `${name}/${file} must not erase the whole storage`);
    }
  });
}

for (const name of projects.filter((p) => existsSync(join(PROJECTS, p, 'index.html')))) {
  test(`${name}: safe static page`, () => {
    const html = read(name, 'index.html');
    assert.ok(html.includes('<meta http-equiv="Content-Security-Policy" content="default-src \'self\'">'), 'CSP header');
    const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)];
    assert.equal(scripts.length, 1, 'one script');
    assert.match(scripts[0][1].trim(), /^type="module" src="[a-z-]+\.js"$/);
    assert.equal(scripts[0][2].trim(), '', 'no inline script');
    assert.doesNotMatch(html, /<style\b/i);
    assert.doesNotMatch(html, /\sstyle\s*=/i);
    assert.doesNotMatch(html, /\son[a-z]+\s*=/i);
    assert.doesNotMatch(html, /javascript:/i);
    // what shared/page.js looks for is on the page
    for (const id of ['number', 'link', 'connect', 'disconnect', 'message', 'needs-server', 'light', 'light-text', 'pretend-drop']) {
      assert.ok(html.includes(`id="${id}"`), `#${id}`);
    }
    assert.ok(html.includes('class="card row pretend-only" hidden') || /class="[^"]*pretend-only[^"]*" hidden/.test(html), 'pretend controls start hidden');
  });
}

test('the projects use only the kit\'s public pretend-band calls, and nothing private', () => {
  for (const folder of folders.filter((name) => name !== 'test')) {
    for (const file of files(folder).filter((f) => /\.(js|py)$/.test(f))) {
      const text = read(folder, file);
      // a method of the library or of the pretend band that starts with _ is not public
      assert.doesNotMatch(text, /\b(?:band|sim|pretend|transport|super\(\)|super)\._[a-z]/i, `${folder}/${file} reaches into a private part`);
      assert.doesNotMatch(text, /extends SimTransport|\(SimTransport\)/, `${folder}/${file} must not subclass the pretend band`);
      assert.doesNotMatch(text, /gettempdir|\/tmp\/|innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|\bexec\(|new Function|os\.system|subprocess|fetch\(|XMLHttpRequest|WebSocket|urllib|requests\.|socket\b/,
        `${folder}/${file}: no temp-dir files, no HTML from text, no code from text, no network`);
    }
  }
});

test('every page on how to run says it the way the rest of the kit does', () => {
  for (const name of ['', ...projects]) {
    const text = read(name, 'README.md');
    for (const line of text.split('\n').filter((l) => l.includes('http.server'))) {
      assert.match(line, /-m http\.server \d+ --bind 127\.0\.0\.1/, `${name}/README.md: ${line.trim()}`);
    }
    assert.match(text, /[Oo]n\s+Windows,?\s+type\s+`python`/, `${name}/README.md says what to type on Windows`);
    assert.doesNotMatch(text, /pretend\.(js|py)|top folder/, `${name}/README.md`);
    if (name) assert.match(text, /[Nn]umbers to tune[\s\S]{0,200}(guess|tried on a real)/, `${name}/README.md says where its thresholds are and that they are guesses`);
  }
  assert.match(read('clap-lights', 'README.md'), /microphone is on[^.]*tell the people near you/i);
  assert.match(read('clap-lights', 'index.html'), /Tell the people near you/);
});

test('nothing in projects/ loads from another site, and every local file it points to exists', () => {
  for (const folder of folders) {
    for (const file of files(folder).filter((f) => /\.(html|js|css)$/.test(f))) {
      const text = read(folder, file);
      assert.doesNotMatch(text, /https?:\/\/(?!localhost[:\/])|@import/i, `${folder}/${file}`);
      if (file.endsWith('.css')) assert.doesNotMatch(text, /url\(/i, `${folder}/${file}`);
      for (const m of text.matchAll(/(?:src|href)="([^"]*)"/g)) {
        assert.ok(existsSync(join(PROJECTS, folder, m[1])), `${folder}/${file}: ${m[1]} exists`);
      }
      for (const m of text.matchAll(/from '([^']*)'/g)) {
        if (m[1].startsWith('.')) assert.ok(existsSync(join(PROJECTS, folder, m[1])), `${folder}/${file}: import ${m[1]} exists`);
      }
    }
  }
});
