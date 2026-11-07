// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../../', import.meta.url));
function walk(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const file = path.join(directory, entry.name);
    return entry.isDirectory() ? walk(file) : /\.(?:mjs|cjs)$/.test(file) ? [file] : [];
  });
}
function surface() {
  const files = [
    ...fs.readdirSync(root).filter(name => name.endsWith('.cjs')).map(name => path.join(root, name)),
    ...walk(path.join(root, 'scripts')),
    ...fs.readdirSync(path.join(root, 'packages')).flatMap(name => {
      const src = path.join(root, 'packages', name, 'src');
      return fs.existsSync(src) ? walk(src) : [];
    }),
  ];
  return Object.fromEntries(files.sort().flatMap(file => {
    const lines = fs.readFileSync(file, 'utf8').split('\n')
      .filter(line => /win32|process\.platform|node:child_process/.test(line)).map(line => line.trim());
    return lines.length ? [[path.relative(root, file).split(path.sep).join('/'), lines]] : [];
  }));
}

test('platform branches and direct process imports require explicit isolation review', () => {
  const allowed = JSON.parse(fs.readFileSync(new URL('./fixtures/platform-allowlist.json', import.meta.url), 'utf8'));
  assert.deepEqual(surface(), allowed,
    'Review platform/process boundary changes and explicitly update fixtures/platform-allowlist.json. Do not regenerate it blindly.');
});
