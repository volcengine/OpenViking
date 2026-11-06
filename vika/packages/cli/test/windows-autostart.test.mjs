import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { autostartCommand, autostartEnabled } from '../src/autostart.mjs';

test('Windows login startup is enabled and disabled without systemctl', async () => {
  const homeDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika login '));
  const options = { homeDir, platform: 'win32', output: { write() {} }, execFileImpl: async () => { throw new Error('must not execute service managers'); } };
  try {
    assert.equal(autostartEnabled(options), false);
    assert.equal(await autostartCommand(['enable'], options), 0);
    assert.equal(autostartEnabled(options), true);
    const file = path.join(homeDir, 'AppData', 'Roaming', 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup', 'Vika.vbs');
    const content = fs.readFileSync(file, 'utf8');
    assert.match(content, /vika-launch\.mjs/);
    assert.match(content, /start".*, 0, False/);
    assert.equal(await autostartCommand(['disable'], options), 0);
    assert.equal(autostartEnabled(options), false);
  } finally { fs.rmSync(homeDir, { recursive: true, force: true }); }
});
