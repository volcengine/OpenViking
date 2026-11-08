// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import fs from 'node:fs';
import path from 'node:path';
export { spawn, execFile } from 'node:child_process';
export const spawnDetached = true;
export const processOptions = Object.freeze({});
export function normalizeEnvironment(env = process.env) { return env; }
export function executableInvocation(command, args) { return { command, args }; }
export function isNamedPipe() { return false; }
export function localSocketPath(directory, name = 'actions') { return path.join(directory, `${name}.sock`); }
export function homeDir(env = process.env) { return env.HOME; }
export function binName(name) { return name; }
export function expandHome(value, home) { return value === '~' ? home : value.replace(/^~(?=\/)/, home); }
export function linkConfig(from, to) { try { fs.symlinkSync(from, to); } catch { /* raced or unsupported: skip */ } }
export function onShutdown() {}
export function findExecutable(command, env = process.env) {
  for (const directory of String(env.PATH || '').split(path.delimiter).filter(Boolean)) {
    const candidate = path.join(directory, command);
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      if (fs.statSync(candidate).isFile()) return candidate;
    } catch {}
  }
  return null;
}

export function terminateProcessTree(child, signal = 'SIGTERM') {
  if (!child?.pid) return;
  {
    try {
      // The group may outlive its leader, so always signal by the original pgid
      // even after the leader exited — otherwise grandchildren leak.
      process.kill(-child.pid, signal);
      return;
    } catch {}
  }
  if (child.exitCode !== null) return;
  try { child.kill(signal); } catch {}
}

export function assertPrivateDirectory(directory) {
  const info = fs.lstatSync(directory);
  if (!info.isDirectory() || info.isSymbolicLink()
    || (typeof process.getuid === 'function' && info.uid !== process.getuid())) {
    throw new Error(`Temporary directory must be a directory owned by the daemon user: ${directory}`);
  }
  if ((info.mode & 0o077) !== 0) {
    throw new Error(`Temporary directory must have mode 0700; use a private VIKA_TEMP_DIR: ${directory}`);
  }
}

export function preparePrivateDirectory(directory) {
  // Do not chmod shared paths such as /tmp, or follow a supplied symlink.
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  assertPrivateDirectory(directory);
  return directory;
}
export function urlOpenCommand(env = process.env) {
  if (process.platform === 'darwin') return 'open';
  if (process.platform === 'linux' && (env.DISPLAY || env.WAYLAND_DISPLAY)) return 'xdg-open';
  return null;
}

// No adapter-defined argv bound; native execve limits still apply.
export const maxCommandLineLength = null;
export const supportsPosixSignals = true;
export const supportsFileModes = true;
export const supportsShellScripts = true;
export const pathDelimiter = ':';
export const loginService = process.platform === 'darwin' ? 'launchd' : 'systemd';
export const systemEnvironmentKeys = Object.freeze([]);
export const hostPlatformId = `${process.platform}-${process.arch}`;
export const environmentCaseSensitive = true;

// Native command tool used by the managed Durable runtime.
export const codingShell = 'bash';
