// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const cli = fileURLToPath(new URL('../bin/vika.mjs', import.meta.url));

test('logs loads in a fresh process and forwards the selected instance to pm2', {
  skip: process.platform === 'win32' ? 'fake pm2 uses a POSIX shell script' : false,
}, (t) => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-logs-cli-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const config = path.join(home, 'config.json');
  fs.writeFileSync(config, JSON.stringify({
    version: 1, defaultInstance: 'alpha', env: {},
    agents: [{
      id: 'alpha', displayName: 'Alpha', profile: 'vika-alpha', port: 3738, model: 'codex',
      dataDir: path.join(home, 'data'), workspace: path.join(home, 'workspace'),
      ovConfig: path.join(home, 'ov.json'),
    }],
  }));
  const pm2 = path.join(home, 'pm2');
  fs.writeFileSync(pm2, '#!/bin/sh\nprintf "%s\\n" "$@"\n', { mode: 0o755 });
  // Start the real entry point with a cold module cache: importing control in
  // this test process would hide the concurrent ESM/CJS loading regression.
  const result = spawnSync(process.execPath, [cli, 'logs', 'alpha'], {
    env: { PATH: process.env.PATH, HOME: home, USERPROFILE: home, VIKA_CONFIG: config, PM2_BIN: pm2 },
    encoding: 'utf8', timeout: 10_000,
  });
  assert.ifError(result.error);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, '');
  assert.equal(result.stdout, 'logs\nvika-alpha\n--lines\n100\n');
});
