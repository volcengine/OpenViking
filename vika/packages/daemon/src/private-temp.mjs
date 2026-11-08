// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';

export function assertPrivateDirectory(directory) {
  const info = fs.lstatSync(directory);
  if (!info.isDirectory() || info.isSymbolicLink()
    || (typeof process.getuid === 'function' && info.uid !== process.getuid())) {
    throw new Error(`Temporary directory must be a directory owned by the daemon user: ${directory}`);
  }
  if (process.platform !== 'win32' && (info.mode & 0o077) !== 0) {
    throw new Error(`Temporary directory must have mode 0700; use a private VIKA_TEMP_DIR: ${directory}`);
  }
}

export function preparePrivateDirectory(directory) {
  // Do not chmod shared paths such as /tmp, or follow a supplied symlink.
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  assertPrivateDirectory(directory);
  return directory;
}
