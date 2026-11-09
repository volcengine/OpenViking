// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

export function readWorkspaceFile(relative, workspace, maxBytes = Infinity) {
  if (typeof relative !== 'string' || !relative || path.isAbsolute(relative) || relative.includes('\0')
    || relative.includes('\\') || relative.split('/').includes('..') || /^[a-z][a-z0-9+.-]*:/i.test(relative)) {
    throw new Error('file_path must be relative to the workspace without traversal or URLs');
  }
  const root = fs.realpathSync(workspace);
  const filename = fs.realpathSync(path.resolve(root, relative));
  if (!filename.startsWith(root + path.sep)) throw new Error('file_path escapes the workspace');
  const fd = fs.openSync(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.size < 1 || stat.size > maxBytes) {
      throw new Error(`file must be a nonempty regular file of at most ${maxBytes / 1024 / 1024} MiB`);
    }
    const bytes = Buffer.alloc(stat.size);
    const size = fs.readSync(fd, bytes, 0, stat.size, 0);
    if (size !== stat.size) throw new Error('file changed while it was being read');
    return { name: path.basename(filename), bytes };
  } finally { fs.closeSync(fd); }
}

// Disk-backed immutable snapshot for uploads. Copy/hash with bounded memory and
// yield between chunks so newer inbound events can still advance freshness.
export async function snapshotWorkspaceFile(relative, workspace, destinationDirectory) {
  if (typeof relative !== 'string' || !relative || path.isAbsolute(relative) || relative.includes('\0')
    || relative.includes('\\') || relative.split('/').includes('..') || /^[a-z][a-z0-9+.-]*:/i.test(relative)) {
    throw new Error('file_path must be relative to the workspace without traversal or URLs');
  }
  const root = await fs.promises.realpath(workspace);
  const filename = await fs.promises.realpath(path.resolve(root, relative));
  if (!filename.startsWith(root + path.sep)) throw new Error('file_path escapes the workspace');
  const source = await fs.promises.open(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  const name = path.basename(filename);
  const destination = path.join(destinationDirectory, name);
  let target;
  try {
    const before = await source.stat();
    if (!before.isFile() || before.size < 1 || !Number.isSafeInteger(before.size)) throw new Error('file must be a nonempty regular file');
    target = await fs.promises.open(destination, 'wx', 0o600);
    const digest = createHash('sha256'); let bytes = 0;
    for await (const chunk of source.createReadStream({ autoClose: false, highWaterMark: 1024 * 1024 })) {
      if (bytes + chunk.length > before.size) throw new Error('file changed while it was being copied');
      digest.update(chunk);
      let offset = 0;
      while (offset < chunk.length) {
        const result = await target.write(chunk, offset, chunk.length - offset, bytes + offset);
        if (!result.bytesWritten) throw new Error('File snapshot made no progress');
        offset += result.bytesWritten;
      }
      bytes += chunk.length;
    }
    const after = await source.stat();
    if (bytes !== before.size || before.size !== after.size || before.mtimeMs !== after.mtimeMs || before.ctimeMs !== after.ctimeMs) {
      throw new Error('file changed while it was being copied');
    }
    return { name, path: destination, digest: digest.digest('hex'), bytes };
  } finally { await source.close(); await target?.close(); }
}
