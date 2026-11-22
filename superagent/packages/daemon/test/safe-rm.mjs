// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Removing a test's temp directory is not the behavior under test, and Windows
// can refuse it while SQLite's WAL/shm files or a child's pipe are still open.
// Retry briefly, then leave the directory to the OS temp sweep instead of
// failing the case.

import fs from 'node:fs';

const RETRYABLE = new Set(['EPERM', 'EBUSY', 'ENOTEMPTY']);

export function removeTempDir(target) {
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      fs.rmSync(target, { recursive: true, force: true, maxRetries: 2, retryDelay: 50 });
      return;
    } catch (error) {
      if (process.platform !== 'win32' || !RETRYABLE.has(error.code)) throw error;
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 100);
    }
  }
}
