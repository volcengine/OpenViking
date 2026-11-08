// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { installLarkCliBinary } from '../../../scripts/release/lark-cli.mjs';
import { PLATFORMS, platform } from '../src/artifacts.mjs';

test('Windows source dependency uses the checksum-verified zip/exe path, never a release build', async t => {
  assert.equal(platform('win32-x64').sourceOnly, true);
  assert.deepEqual(Object.keys(PLATFORMS).filter(id => !PLATFORMS[id].sourceOnly), ['linux-x64', 'linux-arm64', 'darwin-x64', 'darwin-arm64']);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-artifact-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const archive = 'lark-cli-1.0.94-windows-amd64.zip';
  const body = Buffer.from('test archive payload');
  const hash = createHash('sha256').update(body).digest('hex');
  fs.writeFileSync(path.join(root, 'package.json'), JSON.stringify({ version: '1.0.94' }));
  fs.writeFileSync(path.join(root, 'checksums.txt'), `${hash}  ${archive}\n`);
  const requests = [];
  let extracted = 0;
  const options = {
    packageDir: root, platformId: 'win32-x64', mirror: 'https://fixture.invalid',
    fetchImpl: async url => { requests.push(url); return new Response(body); },
    execFileSyncImpl(command, args) {
      extracted++;
      assert.equal(command, 'tar');
      assert.equal(args[0], '-xf');
      assert.equal(path.basename(args[1]), archive);
      assert.equal(args[2], '-C');
      assert.equal(args[4], 'lark-cli.exe');
      assert.deepEqual(fs.readFileSync(args[1]), body);
      fs.writeFileSync(path.join(args[3], 'lark-cli.exe'), 'binary fixture');
    },
  };
  const result = await installLarkCliBinary(options);
  assert.equal(path.basename(result.bin), 'lark-cli.exe');
  assert.equal(fs.readFileSync(result.bin, 'utf8'), 'binary fixture');
  assert.equal(requests.length, 1);
  assert.ok(requests[0].endsWith(archive));
  assert.equal(extracted, 1);
  await assert.rejects(installLarkCliBinary({ ...options, fetchImpl: async () => new Response('corrupt') }), /SHA-256 mismatch/);
  assert.equal(extracted, 1, 'invalid archive must not be extracted');
  const build = spawnSync(process.execPath, [fileURLToPath(new URL('../../../scripts/build-release.mjs', import.meta.url)), '--platform', 'win32-x64'], { encoding: 'utf8' });
  assert.notEqual(build.status, 0);
  assert.match(build.stderr, /Release builds are not supported/);
});
