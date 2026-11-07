// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

function pidIsAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code === 'EPERM';
  }
}

// A PID alone can belong to a different process after a crash. Unknown identity
// must fail closed; inability to query a process is not proof the lock is stale.
export function processIdentity(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return null;
  try {
    if (process.platform === 'linux') {
      const stat = fs.readFileSync(`/proc/${pid}/stat`, 'utf8');
      return `linux:${stat.slice(stat.lastIndexOf(')') + 2).split(' ')[19]}`;
    }
    if (process.platform === 'win32') {
      const executable = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
      const result = execFileSync(executable, ['-NoProfile', '-NonInteractive', '-Command',
        `(Get-Process -Id ${pid} -ErrorAction Stop).StartTime.ToUniversalTime().Ticks.ToString()`],
        { encoding: 'utf8', windowsHide: true, timeout: 5000, stdio: ['ignore', 'pipe', 'ignore'] }).trim();
      return /^\d+$/.test(result) ? `windows:${result}` : null;
    }
  } catch {}
  return null;
}

export class InstanceLock {
  constructor(lockPath, { identity = processIdentity } = {}) {
    this.identity = identity;
    this.lockPath = lockPath;
    this.held = false;
  }

  acquire() {
    fs.mkdirSync(path.dirname(this.lockPath), { recursive: true, mode: 0o700 });
    for (let attempt = 0; attempt < 4; attempt += 1) {
      try {
        const fd = fs.openSync(this.lockPath, 'wx', 0o600);
        fs.writeFileSync(fd, JSON.stringify({
          pid: process.pid,
          processIdentity: this.identity(process.pid),
          hostname: os.hostname(),
          startedAt: new Date().toISOString(),
        }));
        fs.closeSync(fd);
        this.held = true;
        return;
      } catch (error) {
        if (error.code !== 'EEXIST') throw error;
        let before;
        try {
          before = fs.lstatSync(this.lockPath);
          if (!before.isFile()) throw new Error('daemon lock path is not a regular file');
        } catch (statError) {
          if (statError.code === 'ENOENT') continue;
          throw statError;
        }
        let owner = null;
        try { owner = JSON.parse(fs.readFileSync(this.lockPath, 'utf8')); } catch {}
        if (owner?.hostname === os.hostname() && pidIsAlive(Number(owner.pid))) {
          const actualIdentity = this.identity(Number(owner.pid));
          if (!owner.processIdentity || !actualIdentity || owner.processIdentity === actualIdentity) {
            throw new Error(`another vika instance is running (pid ${owner.pid})`);
          }
        }
        let after;
        try { after = fs.lstatSync(this.lockPath); } catch (statError) {
          if (statError.code === 'ENOENT') continue;
          throw statError;
        }
        if (before.dev !== after.dev || before.ino !== after.ino) continue;
        try { fs.unlinkSync(this.lockPath); } catch (unlinkError) {
          if (unlinkError.code !== 'ENOENT') throw unlinkError;
        }
      }
    }
    throw new Error('failed to acquire daemon lock');
  }

  release() {
    if (!this.held) return;
    try {
      const owner = JSON.parse(fs.readFileSync(this.lockPath, 'utf8'));
      if (Number(owner.pid) === process.pid) fs.unlinkSync(this.lockPath);
    } catch {}
    this.held = false;
  }
}
