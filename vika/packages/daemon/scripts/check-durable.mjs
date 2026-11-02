// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import readline from 'node:readline';
import { DURABLE_HOST, DURABLE_VERSION } from '../src/durable-runtime.mjs';

// Start the durable host the way the daemon does, without a model provider:
// the fake provider path only exercises module loading, the Harness, SQLite
// storage, and the protocol handshake this release ships.
const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-durable-check-'));
const workspace = path.join(dataDir, 'workspace');
fs.mkdirSync(workspace);
const child = spawn(process.execPath, [DURABLE_HOST], {
  cwd: workspace,
  env: { ...process.env, DURABLE_FAUX: '1', DURABLE_FAUX_RESPONSES: '[{"text":"ok"}]' },
  stdio: ['pipe', 'pipe', 'pipe'],
});
let settled = false;
const fail = (error) => {
  if (settled) return;
  settled = true;
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  try { child.kill('SIGKILL'); } catch {}
  fs.rmSync(dataDir, { recursive: true, force: true });
  process.exit(1);
};
const timer = setTimeout(() => fail(new Error(`durable host did not answer within 20s (${DURABLE_VERSION})`)), 20_000);
timer.unref?.();
child.stderr.setEncoding('utf8');
child.stderr.on('data', (chunk) => process.stderr.write(String(chunk)));
child.once('error', fail);
child.once('exit', (code, signal) => {
  if (settled) return;
  fail(new Error(`durable host exited early (code ${code}, signal ${signal})`));
});
readline.createInterface({ input: child.stdout }).on('line', (line) => {
  let message;
  try { message = JSON.parse(line); } catch { return; }
  if (message.type === 'ready') {
    if (message.version !== DURABLE_VERSION) {
      fail(new Error(`durable host is ${message.version}, release needs ${DURABLE_VERSION}`));
      return;
    }
    if (!settled) {
      settled = true;
      clearTimeout(timer);
      try { child.stdin.write(`${JSON.stringify({ type: 'stop' })}\n`); } catch {}
      setTimeout(() => {
        fs.rmSync(dataDir, { recursive: true, force: true });
        process.stdout.write(`Managed durable host ${DURABLE_VERSION} is ready.\n`);
        process.exit(0);
      }, 200);
    }
    return;
  }
  if (message.type === 'fatal') fail(new Error(message.error || 'durable host failed'));
});
child.stdin.write(`${JSON.stringify({
  id: 'hello', type: 'hello', dataDir, workspace, sessionKey: 'check', model: 'faux/faux-model', effort: 'medium', policy: '',
})}\n`);
