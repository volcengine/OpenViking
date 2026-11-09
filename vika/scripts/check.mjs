// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { spawnSync } from 'node:child_process';
import { globSync } from 'node:fs';

function check(command, flag, patterns) {
  for (const pattern of patterns) {
    const files = globSync(pattern);
    if (!files.length) throw new Error(`No files matched ${pattern}`);
    for (const file of files) {
      const { status, error } = spawnSync(command, [flag, file], { stdio: 'inherit' });
      if (error) throw error;
      if (status !== 0) process.exit(status ?? 1);
    }
  }
}

if (process.platform === 'win32') {
  console.log('Skipping POSIX shell syntax checks on Windows.');
} else {
  check('sh', '-n', ['scripts/*.sh', 'bin/pm2']);
}

check(process.execPath, '--check', [
  'scripts/*.mjs', 'scripts/release/*.mjs',
  'packages/daemon/src/*.mjs',
  'packages/daemon/src/dsh/*.mjs', 'packages/daemon/src/dsh-bridge/*.mjs',
  'packages/daemon/src/platform/*.mjs',
  'packages/daemon/src/actions/*.mjs', 'packages/daemon/src/connectors/*.mjs',
  'packages/daemon/src/extensions/*.mjs', 'packages/daemon/src/meeting/*.mjs',
  'packages/daemon/src/qq/*.mjs', 'packages/daemon/src/telegram/*.mjs',
  'packages/daemon/src/slack/*.mjs', 'packages/daemon/src/onebot/*.mjs', 'packages/daemon/src/weixin/*.mjs',
  'packages/daemon/src/email/*.mjs', 'packages/daemon/scripts/*.mjs',
  'packages/web/src/*.mjs', 'packages/web/scripts/*.mjs', 'packages/web/public/*.js',
  'packages/updater/src/*.mjs', 'packages/cli/src/*.mjs', 'packages/cli/bin/*.mjs',
]);
