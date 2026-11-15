// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export const LOG_MAX_BYTES = 10 * 1024 * 1024;
export const LOG_ARCHIVES = 2;
export const LOG_CHECK_MS = 60 * 60 * 1000;

export function pm2LogDir(env = process.env) {
  return path.join(env.PM2_HOME || path.join(env.HOME || os.homedir(), '.ov-superagent', 'pm2'), 'logs');
}

// PM2 keeps its log files open in append mode and has no size limit of its
// own, so a file over the limit is copied aside and truncated in place:
// name.log.1 is the newest archive, older ones shift up and the last drops.
export function rotateLogs(dir, { maxBytes = LOG_MAX_BYTES, archives = LOG_ARCHIVES } = {}) {
  const rotated = [];
  let names;
  try { names = fs.readdirSync(dir); } catch { return rotated; }
  for (const name of names) {
    if (!name.endsWith('.log')) continue;
    const file = path.join(dir, name);
    try {
      if (fs.statSync(file).size <= maxBytes) continue;
      for (let index = archives; index > 1; index -= 1) {
        const from = `${file}.${index - 1}`;
        if (fs.existsSync(from)) fs.renameSync(from, `${file}.${index}`);
      }
      fs.copyFileSync(file, `${file}.1`);
      fs.truncateSync(file, 0);
      rotated.push(name);
    } catch { /* a file that vanished or is unreadable waits for the next check */ }
  }
  return rotated;
}

export function startLogRotation({ dir = pm2LogDir(), env = process.env, intervalMs = LOG_CHECK_MS } = {}) {
  const maxBytes = Number.parseInt(env.OV_SUPERAGENT_LOG_MAX_BYTES ?? '', 10) || LOG_MAX_BYTES;
  const run = () => rotateLogs(dir, { maxBytes });
  run();
  const timer = setInterval(run, intervalMs);
  timer.unref?.();
  return () => clearInterval(timer);
}
