// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Links a test needs to build a path the product must refuse or leave alone.
// Windows only creates file symlinks with developer mode or elevation, and a
// directory symlink needs `junction` there. Each helper says whether the link
// exists, so a case keeps the rest of its assertions when it does not.

import fs from 'node:fs';

/** Link two directories: a junction on Windows (no privilege needed), a directory symlink elsewhere. */
export function directoryLink(target, link) {
  fs.symlinkSync(target, link, process.platform === 'win32' ? 'junction' : 'dir');
}

/** Link a file, or return false where the host will not create one. */
export function fileLink(target, link) {
  try {
    fs.symlinkSync(target, link, 'file');
    return true;
  } catch (error) {
    if (process.platform === 'win32' && ['EPERM', 'EACCES', 'UNKNOWN'].includes(error.code)) return false;
    throw error;
  }
}
