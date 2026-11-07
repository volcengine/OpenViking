import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { InstanceLock, processIdentity } from '../src/lock.mjs';
test('lock reclaims reused PID only with evidence of a changed process identity', t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-lock-identity-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'daemon.lock');
  const write = identity => fs.writeFileSync(file, JSON.stringify({ pid: process.pid, hostname: os.hostname(), processIdentity: identity }));
  write('old-process');
  const lock = new InstanceLock(file, { identity: () => 'new-process' });
  lock.acquire();
  assert.equal(JSON.parse(fs.readFileSync(file)).processIdentity, 'new-process');
  assert.throws(() => new InstanceLock(file, { identity: () => 'new-process' }).acquire(), /another vika instance/);
  assert.throws(() => new InstanceLock(file, { identity: () => null }).acquire(), /another vika instance/);
  lock.release();
  write(undefined);
  assert.throws(() => lock.acquire(), /another vika instance/, 'legacy live locks are not removed without identity evidence');
});

test('native process identity is stable for the current process', { skip: !['linux', 'win32'].includes(process.platform) }, () => {
  const identity = processIdentity(process.pid);
  assert.ok(identity);
  assert.equal(processIdentity(process.pid), identity);
  assert.equal(processIdentity(-1), null);
});
