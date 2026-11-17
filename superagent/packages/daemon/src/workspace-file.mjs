// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';

export function resolveWorkspacePath(relative, workspace) {
  if (typeof relative !== 'string' || !relative || path.isAbsolute(relative) || relative.includes('\0')
    || relative.includes('\\') || relative.split('/').includes('..') || /^[a-z][a-z0-9+.-]*:/i.test(relative)) {
    throw new Error('file_path must be relative to the workspace without traversal or URLs');
  }
  const root = fs.realpathSync(workspace);
  const requested = path.resolve(root, relative);
  const filename = fs.realpathSync(requested);
  if (!filename.startsWith(root + path.sep)) throw new Error('file_path escapes the workspace');
  return { root, requested, filename };
}

export function readWorkspaceFile(relative, workspace, maxBytes = 20 * 1024 * 1024) {
  const { filename } = resolveWorkspacePath(relative, workspace);
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
