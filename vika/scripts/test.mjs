// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { terminateProcessTree } from '../packages/daemon/src/platform/index.mjs';

// Keep the entire suite visible. A stuck fixture is a failure, never a green
// --test-force-exit run. Terminate only this runner's process tree on timeout.
const timeoutMs = Number(process.env.VIKA_TEST_TIMEOUT_MS || 600_000);
if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) throw new Error('VIKA_TEST_TIMEOUT_MS must be a positive integer');
const child = spawn(process.execPath, ['--test', '--test-timeout=120000',
  ...process.argv.slice(2),
], { cwd: fileURLToPath(new URL('..', import.meta.url)), stdio: 'inherit', detached: process.platform !== 'win32' });
let timedOut = false;
const timer = setTimeout(() => {
  timedOut = true;
  console.error(`Test suite exceeded ${timeoutMs} ms; terminating its child processes. Result: FAIL.`);
  terminateProcessTree(child, 'SIGKILL');
}, timeoutMs);
child.on('error', (error) => { clearTimeout(timer); console.error(error.message); process.exitCode = 1; });
child.on('exit', (code) => { clearTimeout(timer); process.exitCode = timedOut ? 1 : (code ?? 1); });
