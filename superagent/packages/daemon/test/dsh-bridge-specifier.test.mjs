// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { importableModuleUrl } from '../src/dsh-bridge/specifier.mjs';

test('a resolved filesystem path becomes the file URL import() accepts', () => {
  // The shape of a drive-letter path is what broke the bridge on Windows:
  // `import('C:\\...')` reads `c:` as a URL scheme. Kept literal rather than
  // platform-dependent so the assertion also runs on POSIX CI.
  const resolved = 'C:\\Program Files\\dsh\\node_modules\\@deepseek-ai\\dsh-llm\\lib\\index.js';
  assert.equal(importableModuleUrl(resolved), pathToFileURL(resolved).href);
  assert.match(importableModuleUrl(resolved), /^file:\/\/\//);
});

test('a POSIX path still becomes the same file URL', () => {
  const resolved = '/opt/dsh/node_modules/@deepseek-ai/dsh-llm/lib/index.js';
  assert.equal(importableModuleUrl(resolved), pathToFileURL(resolved).href);
  assert.match(importableModuleUrl(resolved), /^file:\/\/\//);
});

test('the returned specifier imports the module it names', async (t) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-dsh-specifier-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const file = path.join(directory, 'module.mjs');
  fs.writeFileSync(file, 'export default 42;\n');
  assert.equal((await import(importableModuleUrl(file))).default, 42);
});
