import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { once } from 'node:events';
import { terminateProcessTree } from '../src/process-utils.mjs';
// Imported directly, not through process-utils: the case below is about the win32
// implementation's timing and runs on every host.
import * as windows from '../src/platform/win32.mjs';

const alive = (pid) => { try { process.kill(pid, 0); return true; } catch { return false; } };

// A pid no host hands out: Windows pids are 32-bit and Linux caps pid_max far
// below this one.
const noSuchPid = 2 ** 31 - 1;

test('Windows termination signals the handle in the same tick when the pid is already gone', () => {
  // taskkill cannot act on a pid that is gone; learning that from its exit code
  // delayed the handle fallback by one process spawn, so an escalation window
  // shorter than that spawn sent SIGKILL without the SIGTERM before it (the
  // dsh-startup escalation case, seen once in a full-suite run).
  const signals = [];
  const child = { pid: noSuchPid, exitCode: null, signalCode: null, kill: (signal) => signals.push(signal) };
  windows.terminateProcessTree(child);
  windows.terminateProcessTree(child, 'SIGKILL');
  assert.deepEqual(signals, ['SIGTERM', 'SIGKILL']);
});

test('Windows cancellation terminates a runtime and its grandchild', { skip: process.platform !== 'win32', timeout: 15000 }, async () => {
  const code = `const {spawn}=require('node:child_process'); const child=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'}); console.log(child.pid); setInterval(()=>{},1000);`;
  const child = spawn(process.execPath, ['-e', code], { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
  let grandchild;
  try {
    const [data] = await once(child.stdout, 'data');
    grandchild = Number(String(data).trim());
    assert.ok(Number.isSafeInteger(grandchild) && grandchild > 0);
    terminateProcessTree(child);
    for (let i = 0; i < 100 && (alive(child.pid) || alive(grandchild)); i++) {
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.equal(alive(child.pid), false);
    assert.equal(alive(grandchild), false);
  } finally {
    for (const pid of [child.pid, grandchild].filter(Boolean)) {
      if (alive(pid)) { try { execFileSync('taskkill.exe', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore' }); } catch {} }
    }
  }
});

test('Windows termination never targets a ChildProcess already known to have exited', { skip: process.platform !== 'win32' }, () => {
  // Use this test process PID: a regression would terminate the test runner.
  // A real exited child may have had its PID reused by an unrelated process.
  for (const state of [{ exitCode: 0, signalCode: null }, { exitCode: null, signalCode: 'SIGTERM' }]) {
    terminateProcessTree({ pid: process.pid, ...state, kill() { assert.fail('must not kill exited child'); } });
  }
});
