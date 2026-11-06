import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { autostartCommand, autostartEnabled } from '../src/autostart.mjs';

test('Windows login startup is enabled and disabled without systemctl', async () => {
  const homeDir = fs.mkdtempSync(path.join(os.tmpdir(), '维京 用户 login '));
  const options = { homeDir, env: {}, platform: 'win32', output: { write() {} }, execFileImpl: async () => { throw new Error('must not execute service managers'); } };
  try {
    assert.equal(autostartEnabled(options), false);
    assert.equal(await autostartCommand(['enable'], options), 0);
    assert.equal(autostartEnabled(options), true);
    const file = path.join(homeDir, 'AppData', 'Roaming', 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup', 'Vika.vbs');
    const bytes = fs.readFileSync(file);
    assert.deepEqual([...bytes.subarray(0, 2)], [0xff, 0xfe]);
    const content = bytes.toString('utf16le');
    assert.ok(content.includes(homeDir));
    assert.ok(content.includes('维京 用户'));
    assert.match(content, /vika-launch\.mjs/);
    assert.match(content, /start".*, 0, False/);
    assert.equal(await autostartCommand(['disable'], options), 0);
    assert.equal(autostartEnabled(options), false);
  } finally { fs.rmSync(homeDir, { recursive: true, force: true }); }
});

test('Windows login startup honors redirected roaming AppData', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-redirected-'));
  const homeDir = path.join(directory, 'home');
  const roaming = path.join(directory, 'roaming elsewhere');
  const options = { homeDir, env: { APPDATA: roaming }, platform: 'win32', output: { write() {} } };
  try {
    await autostartCommand(['enable'], options);
    assert.equal(autostartEnabled(options), true);
    assert.ok(fs.existsSync(path.join(roaming, 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup', 'Vika.vbs')));
    assert.equal(fs.existsSync(path.join(homeDir, 'AppData')), false);
    await autostartCommand(['disable'], options);
    assert.equal(autostartEnabled(options), false);
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});
