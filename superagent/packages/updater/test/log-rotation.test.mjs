// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { it } from 'node:test';
import { pm2LogDir, rotateLogs } from '../src/log-rotation.mjs';

it('copies a log over the limit aside, truncates it in place and keeps two archives', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'log-rotation-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const log = path.join(dir, 'superagent-primary-out.log');
  const small = path.join(dir, 'superagent-web-out.log');
  fs.writeFileSync(small, 'ok\n');
  // An open append handle, as PM2 holds one, keeps writing to the same file.
  const fd = fs.openSync(log, 'a');
  t.after(() => fs.closeSync(fd));
  for (const round of ['one', 'two', 'three']) {
    fs.writeSync(fd, `${round}\n`.repeat(20));
    assert.deepEqual(rotateLogs(dir, { maxBytes: 50 }), ['superagent-primary-out.log']);
  }
  fs.writeSync(fd, 'after\n');
  assert.equal(fs.readFileSync(log, 'utf8'), 'after\n');
  assert.match(fs.readFileSync(`${log}.1`, 'utf8'), /^three\n/);
  assert.match(fs.readFileSync(`${log}.2`, 'utf8'), /^two\n/);
  assert.equal(fs.existsSync(`${log}.3`), false);
  assert.equal(fs.readFileSync(small, 'utf8'), 'ok\n');
});

it('uses the SuperAgent PM2 home unless PM2_HOME names another', () => {
  assert.equal(pm2LogDir({ HOME: '/home/u' }), path.join('/home/u', '.ov-superagent', 'pm2', 'logs'));
  assert.equal(pm2LogDir({ HOME: '/home/u', PM2_HOME: '/srv/pm2' }), path.join('/srv/pm2', 'logs'));
  assert.deepEqual(rotateLogs('/nonexistent-dir-for-test'), []);
});
