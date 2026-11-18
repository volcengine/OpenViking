// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { PLATFORMS } from '../../updater/src/artifacts.mjs';
import { binName, findExecutable, hostPlatformId } from '../src/platform/index.mjs';
import { bundledRipgrep, RIPGREP_VERSION } from '../src/ripgrep-bin.mjs';
import { extractRipgrepBinary, installRipgrepBinary, RIPGREP_ASSETS } from '../../../scripts/release/ripgrep.mjs';

function scratch(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ripgrep-test-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return directory;
}

test('ripgrep is pinned for every release platform and the Windows preview, at the release bin/', () => {
  assert.deepEqual(Object.keys(RIPGREP_ASSETS).sort(), Object.keys(PLATFORMS).sort());
  for (const [, sha256] of Object.values(RIPGREP_ASSETS)) assert.match(sha256, /^[0-9a-f]{64}$/);
  const root = fileURLToPath(new URL('../../../', import.meta.url));
  assert.equal(bundledRipgrep({ host: 'linux-x64' }), path.join(root, 'bin', binName('rg')));
  assert.equal(bundledRipgrep(), hostPlatformId.startsWith('termux-')
    ? findExecutable('rg') || 'rg'
    : path.join(root, 'bin', binName('rg')));
});

test('ripgrep installer refuses unverified archives before replacing the binary', async (t) => {
  const root = scratch(t);
  await assert.rejects(installRipgrepBinary({ root, platformId: 'unsupported' }), /Unsupported ripgrep platform/);
  const urls = [];
  for (const platformId of ['linux-x64', 'win32-x64']) {
    await assert.rejects(installRipgrepBinary({ root, platformId, fetchImpl: async (value) => {
      urls.push(value);
      return new Response('tampered download');
    } }), /sha.?256|checksum/i);
  }
  assert.deepEqual(urls, [
    `https://github.com/BurntSushi/ripgrep/releases/download/${RIPGREP_VERSION}/ripgrep-${RIPGREP_VERSION}-x86_64-unknown-linux-musl.tar.gz`,
    `https://github.com/BurntSushi/ripgrep/releases/download/${RIPGREP_VERSION}/ripgrep-${RIPGREP_VERSION}-x86_64-pc-windows-msvc.zip`,
  ]);
  assert.deepEqual(fs.readdirSync(root), []);
});

test('ripgrep extraction takes only the binary and its MIT license from the release layout', (t) => {
  const root = scratch(t);
  const target = 'x86_64-unknown-linux-musl';
  const stem = `ripgrep-${RIPGREP_VERSION}-${target}`;
  fs.mkdirSync(path.join(root, 'source', stem), { recursive: true });
  for (const [name, text] of [['rg', 'executable fixture'], ['LICENSE-MIT', 'MIT fixture'], ['UNLICENSE', 'unlicense'], ['README.md', 'readme']]) {
    fs.writeFileSync(path.join(root, 'source', stem, name), text, { mode: 0o755 });
  }
  const archive = path.join(root, 'rg.tar.gz');
  execFileSync('tar', ['-czf', archive, '-C', path.join(root, 'source'), stem]);
  const output = path.join(root, 'output');
  fs.mkdirSync(output);
  const { bin, license } = extractRipgrepBinary(archive, output, { target });
  assert.equal(fs.readFileSync(bin, 'utf8'), 'executable fixture');
  assert.equal(fs.readFileSync(license, 'utf8'), 'MIT fixture');
  assert.deepEqual(fs.readdirSync(path.join(output, stem)).sort(), ['LICENSE-MIT', 'rg']);
  assert.throws(() => extractRipgrepBinary(archive, output, { target: 'aarch64-apple-darwin' }), /has no ripgrep-.*-aarch64-apple-darwin\/rg with its license/);
});
