// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import { mkdtemp, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { diagnoseAppFailure, readAppLogTail } from '../src/app-failure.mjs';

function line(fields) {
  return `${JSON.stringify({ time: '2026-11-25T05:35:00.000Z', ...fields })}\n`;
}

async function logWith(t, text) {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'superagent-failure-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'superagent-web-error.log');
  await writeFile(file, text);
  return file;
}

test('an occupied Web port is named with the port from the log', () => {
  const tail = line({ level: 'error', message: 'web_start_failed', error: 'listen EADDRINUSE: address already in use 127.0.0.1:3737' });
  const diagnosis = diagnoseAppFailure({ state: { status: 'waiting restart', exit_code: 1 }, tail });
  assert.deepEqual(diagnosis, { kind: 'port_in_use', port: 3737, detail: 'listen EADDRINUSE: address already in use 127.0.0.1:3737' });
});

test('a port conflict without a port in the message falls back to the app\'s configured port', () => {
  const tail = line({ level: 'error', message: 'daemon_start_failed', error: 'listen EADDRINUSE' });
  assert.equal(diagnoseAppFailure({ state: { OV_SUPERAGENT_PORT: '3740' }, tail }).port, 3740);
  assert.equal(diagnoseAppFailure({ tail }).port, null);
});

test('IPv6 and wildcard listen addresses still name the port', () => {
  const ipv6 = line({ level: 'error', message: 'web_start_failed', error: 'listen EADDRINUSE: address already in use [::1]:3737' });
  const wildcard = line({ level: 'error', message: 'web_start_failed', error: 'listen EADDRINUSE: address already in use :::3737' });
  assert.equal(diagnoseAppFailure({ tail: ipv6 }).port, 3737);
  assert.equal(diagnoseAppFailure({ tail: wildcard }).port, 3737);
});

test('a rejected setting keeps the setting in the detail', () => {
  const tail = line({ level: 'error', message: 'invalid_configuration', error: 'OV_SUPERAGENT_WEB_HOST must be a loopback address' });
  assert.deepEqual(diagnoseAppFailure({ tail }), {
    kind: 'invalid_configuration',
    detail: 'OV_SUPERAGENT_WEB_HOST must be a loopback address',
  });
});

test('a crash before the JSON logger started is read from the Node stack', () => {
  const tail = [
    'node:internal/modules/esm/resolve:275',
    "    throw new ERR_MODULE_NOT_FOUND(path, base, 'import');",
    'Error [ERR_MODULE_NOT_FOUND]: Cannot find module \'/home/me/.ov-superagent/releases/x/packages/web/src/server.mjs\'',
  ].join('\n');
  assert.equal(diagnoseAppFailure({ tail }).kind, 'incomplete_install');
  assert.match(diagnoseAppFailure({ tail }).detail, /Cannot find module/);
});

test('permission errors and uncaught exceptions are told apart', () => {
  assert.equal(diagnoseAppFailure({ tail: line({ level: 'error', message: 'web_start_failed', error: "EACCES: permission denied, open '/x/public'" }) }).kind, 'permission');
  assert.equal(diagnoseAppFailure({ tail: line({ level: 'error', message: 'uncaught_exception', error: 'boom' }) }).kind, 'crashed');
});

test('only the newest failure line speaks: an old crash is not blamed on the current one', () => {
  const tail = [
    line({ level: 'error', message: 'web_start_failed', error: 'listen EADDRINUSE: address already in use 127.0.0.1:3737' }),
    line({ level: 'info', message: 'web_starting' }),
    line({ level: 'error', message: 'invalid_configuration', error: 'OV_SUPERAGENT_WEB_LOG_LEVEL must be one of: debug, info, warn, error' }),
  ].join('');
  assert.equal(diagnoseAppFailure({ tail }).kind, 'invalid_configuration');
});

test('an empty or unrelated log yields no diagnosis', () => {
  assert.equal(diagnoseAppFailure({ tail: '' }), null);
  assert.equal(diagnoseAppFailure({ tail: line({ level: 'info', message: 'web_starting' }) }), null);
});

test('the log tail is the end of the file, and a missing log is empty', async (t) => {
  const file = await logWith(t, `${'x'.repeat(64 * 1024)}tail-marker`);
  const tail = await readAppLogTail(file, { maxBytes: 32 });
  assert.equal(tail.length, 32);
  assert.match(tail, /tail-marker$/);
  assert.equal(await readAppLogTail(path.join(path.dirname(file), 'nope.log')), '');
  assert.equal(await readAppLogTail(''), '');
});
