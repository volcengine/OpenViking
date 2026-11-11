// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../../', import.meta.url));
function files(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const file = path.join(directory, entry.name);
    return entry.isDirectory() ? files(file) : [file];
  });
}
test('platform detection is confined to the single platform adapter', () => {
  const sources = fs.readdirSync(path.join(root, 'packages')).flatMap(name => {
    const directory = path.join(root, 'packages', name, 'src');
    return fs.existsSync(directory) ? files(directory) : [];
  });
  sources.push(...fs.readdirSync(root).filter(name => name.endsWith('.cjs')).map(name => path.join(root, name)));
  const violations = sources.filter(file => !file.includes(`${path.sep}platform${path.sep}`))
    .filter(file => /process\s*\.\s*platform|\bwin32\b/.test(fs.readFileSync(file, 'utf8')));
  assert.deepEqual(violations.map(file => path.relative(root, file)), []);
});
