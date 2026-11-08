// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

function pidIsAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code === 'EPERM';
  }
}

export class InstanceLock {
  constructor(lockPath) {
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
          throw new Error(`another vika instance is running (pid ${owner.pid})`);
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
