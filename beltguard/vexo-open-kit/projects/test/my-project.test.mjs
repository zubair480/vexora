// A web project copied out of the kit into the student's own folder runs there unchanged:
//   my-project/vexo-open-kit/   the kit
//   my-project/shared/          copied from vexo-open-kit/projects/shared
//   my-project/reaction-game/   copied from vexo-open-kit/projects/reaction-game
//   node --test projects/test/*.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { cpSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { skip, KIT, serveKit, startChrome } from './chrome.mjs';

test('reaction-game copied next to the kit connects to the pretend band as it is', { skip, timeout: 60000 }, async () => {
  const mine = join(mkdtempSync(join(tmpdir(), 'vexo-my-project-')), 'my-project');
  mkdirSync(join(mine, 'vexo-open-kit', 'js'), { recursive: true });
  cpSync(join(KIT, 'js', 'src'), join(mine, 'vexo-open-kit', 'js', 'src'), { recursive: true });
  for (const name of ['shared', 'reaction-game']) cpSync(join(KIT, 'projects', name), join(mine, name), { recursive: true });
  const server = await serveKit(mine);
  const chrome = await startChrome();
  try {
    const page = await chrome.openPage(`${server.origin}/reaction-game/?link=pretend&band=7`);
    assert.equal(await page.run('document.querySelector("#needs-server").hidden'), true);
    await page.connectPretend();
    await page.untilText('#status', /Tap the band to start/);
    await page.click('#pretend-tap');
    await page.untilText('#status', /Wait for green/);
    assert.deepEqual(page.problems, []);
    await page.close();
  } finally {
    await chrome.close();
    server.close();
    rmSync(join(mine, '..'), { recursive: true, force: true });
  }
});
