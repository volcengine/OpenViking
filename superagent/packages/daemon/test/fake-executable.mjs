// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';

// Test fixtures need a command the product can start, not just a file it can
// read. POSIX takes a shebang script directly. Windows cannot start a script
// through CreateProcess, so the fixture names the Node entrypoint instead —
// the executable form the platform launcher accepts next to a native `.exe`
// and an npm shim (see `executableInvocation` in platform/win32.mjs).

/**
 * Write a Node script into `directory` under `name` and return the path to
 * spawn. On Windows that is `<name>.mjs` (or `.cjs`); on POSIX it is the
 * shebang script itself.
 *
 * @param {string} directory
 * @param {string} name
 * @param {{ file?: string, body?: string, commonjs?: boolean }} source
 *   `file` copies an existing script (the fake DSH), `body` writes one inline.
 *   `commonjs` keeps the body a `.cjs` so its `require` calls work.
 */
export function installNodeCommand(directory, name, { file, body, commonjs = false } = {}) {
  const content = file ? fs.readFileSync(file) : Buffer.from(body ?? '');
  const target = path.join(directory, process.platform === 'win32' ? `${name}.${commonjs ? 'cjs' : 'mjs'}` : name);
  fs.writeFileSync(target, content, process.platform === 'win32' ? {} : { mode: 0o755 });
  return target;
}
