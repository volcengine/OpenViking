// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const root = fileURLToPath(new URL('..', import.meta.url));
function walk(directory) {
  return fs.readdirSync(path.join(root, directory), { withFileTypes: true }).flatMap((entry) => {
    const name = path.join(directory, entry.name);
    return entry.isDirectory() ? walk(name) : [name];
  });
}
const files = [
  ...walk('scripts'),
  ...['daemon', 'web', 'updater', 'cli'].flatMap((pkg) =>
    ['src', 'scripts', 'bin', 'public'].flatMap((dir) => {
      const name = `packages/${pkg}/${dir}`;
      return fs.existsSync(path.join(root, name)) ? walk(name) : [];
    })),
];
function check(command, args) {
  const result = spawnSync(command, args, { cwd: root, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}
for (const file of files.filter((name) => /\.(mjs|cjs|js)$/.test(name))) check(process.execPath, ['--check', file]);
if (process.platform === 'win32') {
  console.log('SKIP POSIX shell syntax checks on Windows (release shell scripts are unsupported).');
} else {
  for (const file of [...files.filter((name) => name.endsWith('.sh')), 'bin/pm2']) check('sh', ['-n', file]);
}
