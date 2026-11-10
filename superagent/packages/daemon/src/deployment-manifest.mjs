// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { createRequire } from 'node:module';
import { InstanceLock } from './lock.mjs';

const require = createRequire(import.meta.url);
const { loadDeploymentManifest } = require('../../../deployment-config.cjs');

// Setup and live commands share one writer. Hold the lock only while reading,
// validating and replacing the file, never while waiting for user input.
function withManifestLock(filePath, edit) {
  const lock = new InstanceLock(`${filePath}.lock`);
  lock.acquire();
  try { return edit(); } finally { lock.release(); }
}

function stage(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true, mode: 0o700 });
  const temporary = `${filePath}.${randomUUID()}.tmp`;
  try {
    fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600, flag: 'wx' });
    loadDeploymentManifest({ VIKA_CONFIG: temporary, HOME: os.homedir() });
    return temporary;
  } catch (error) {
    fs.rmSync(temporary, { force: true });
    throw error;
  }
}

function save(filePath, value) {
  const temporary = stage(filePath, value);
  try {
    if (fs.existsSync(filePath)) fs.copyFileSync(filePath, `${filePath}.bak`);
    fs.renameSync(temporary, filePath);
  } finally {
    fs.rmSync(temporary, { force: true });
  }
}

function currentValue(filePath) {
  try { return JSON.parse(fs.readFileSync(filePath, 'utf8')); } catch (error) {
    if (error.code === 'ENOENT') return null;
    throw error;
  }
}

export function validateManifest(filePath, value) {
  fs.rmSync(stage(filePath, value), { force: true });
}

export function writeManifest(filePath, value, { expected } = {}) {
  return withManifestLock(filePath, () => {
    if (expected !== undefined && JSON.stringify(currentValue(filePath)) !== JSON.stringify(expected)) {
      throw Object.assign(new Error('Configuration changed elsewhere; run vika setup again.'), { code: 'manifest_conflict' });
    }
    save(filePath, value);
  });
}

// Patch a fresh, validated manifest under the same lock. Preserve all other
// instances and fields; a repeated command does not rewrite the file/backup.
export function updateManifest(filePath, edit) {
  return withManifestLock(filePath, () => {
    const deployment = loadDeploymentManifest({ VIKA_CONFIG: filePath, HOME: os.homedir() });
    const value = currentValue(filePath);
    const before = JSON.stringify(value);
    edit(value, deployment);
    if (JSON.stringify(value) !== before) save(filePath, value);
  });
}
