// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import path from 'node:path';

// The environment keys that name a runtime executable. Each one reaches three
// consumers with three different working directories: `vika setup` probing
// (the shell's cwd), the daemon's control processes (the instance data
// directory), and the daemon spawning the runtime (the instance workspace). A
// relative path with a directory in it therefore names a different file for
// each of them, so only two forms are allowed: a bare command name found on
// PATH, or an absolute path.
export const RUNTIME_BIN_KEYS = Object.freeze([
  'CODEX_BIN',
  'TRAEX_BIN',
  'TRAECLI_BIN',
  'CLAUDE_BIN',
  'PI_BIN',
  'OPENCODE_BIN',
]);

const SEPARATOR = /[\\/]/;

// Recognized on every host, so a value written for Windows reads the same way
// wherever it is validated: a drive path (`C:\x`), and a UNC share (`\\srv\x`).
const WINDOWS_ABSOLUTE = /^(?:[A-Za-z]:[\\/]|[\\/]{2}[^\\/])/;

// `C:codex` is a drive-relative path on Windows: it looks like a drive, and is
// still resolved against that drive's working directory.
const DRIVE_RELATIVE = /^[A-Za-z]:(?![\\/])/;

function absoluteLike(name, paths) {
  // Windows accepts \tools and /tools as rooted, but neither fixes a drive.
  // Normalizing first makes both spellings have the same root on Windows;
  // POSIX /tools keeps its fully qualified slash root.
  if (paths.parse(paths.normalize(name)).root === '\\') return false;
  return paths.isAbsolute(name) || WINDOWS_ABSOLUTE.test(name);
}

/**
 * Whether a configured binary may be used as written.
 * @param {string} value - the raw environment value.
 * @param {typeof path} paths - path semantics (defaults to the current host).
 * @returns {'empty'|'name'|'absolute'|'relative'} how the value is read.
 */
export function runtimeBinKind(value, paths = path) {
  const name = typeof value === 'string' ? value.trim() : '';
  if (!name) return 'empty';
  if (absoluteLike(name, paths)) return 'absolute';
  if (SEPARATOR.test(name) || DRIVE_RELATIVE.test(name)) return 'relative';
  return 'name';
}

/**
 * The reason a configured binary is unusable, or null when it is fine.
 * @param {string} value - the raw environment value.
 * @param {string} key - the environment key, for the message.
 * @returns {string|null} the message to show.
 */
export function runtimeBinProblem(value, key) {
  if (runtimeBinKind(value) !== 'relative') return null;
  return `${key} must be an absolute path or a command name on PATH, not a relative path: `
    + 'the setup probe, the control process and the daemon run in different directories';
}

/**
 * Reject a relative runtime binary path, with the message every consumer uses.
 * @param {string} value - the raw environment value.
 * @param {string} key - the environment key, for the message.
 * @returns {string} the value, unchanged.
 */
export function assertRuntimeBinPath(value, key) {
  const problem = runtimeBinProblem(value, key);
  if (problem) throw new Error(problem);
  return value;
}
