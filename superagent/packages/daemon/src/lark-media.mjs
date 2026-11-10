// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { assertPrivateDirectory, preparePrivateDirectory } from './private-temp.mjs';

export function prepareLarkMediaDirectory(tempDir) {
  // Never chmod a shared directory such as /tmp. Existing paths must already
  // be private; create both the configured root and media child with 0700.
  const directory = path.join(tempDir, 'media');
  for (const entry of [tempDir, directory]) preparePrivateDirectory(entry);
  return directory;
}

export function pruneLarkMedia(tempDir, retentionMs, now = Date.now()) {
  if (!tempDir || !Number.isFinite(retentionMs) || retentionMs <= 0) return 0;
  const directory = path.join(tempDir, 'media');
  if (!fs.existsSync(directory)) return 0;
  assertPrivateDirectory(tempDir);
  assertPrivateDirectory(directory);
  let removed = 0;
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    // Only our downloaded resources, never other temporary files or links.
    if (!entry.isFile() || !/^lark-[a-f0-9]{24}(?:\.[A-Za-z0-9._-]+)?$/.test(entry.name)) continue;
    const file = path.join(directory, entry.name);
    try {
      const info = fs.lstatSync(file);
      if (info.isFile() && info.mtimeMs < now - retentionMs) {
        fs.unlinkSync(file);
        removed += 1;
      }
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
  }
  return removed;
}
