// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import { promisify } from 'node:util';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { spawn as nodeSpawn, execFile as nodeExecFile } from 'node:child_process';

// Object spreads can retain Windows' inherited `Path` beside an explicit
// `PATH` override. Node otherwise chooses one duplicate lexicographically.
// Use the explicit uppercase override consistently for lookup and the child.
export function normalizeEnvironment(env = process.env, platform = process.platform) {
  if (platform !== 'win32') return env;
  const result = { ...env };
  for (const name of ['PATH', 'PATHEXT']) {
    const keys = Object.keys(env).filter(key => key.toUpperCase() === name);
    if (!keys.length) continue;
    const value = env[name] ?? env[keys[0]];
    for (const key of keys) delete result[key];
    result[name] = value;
  }
  return result;
}

export function findExecutable(command, env = process.env, platform = process.platform) {
  const windows = platform === 'win32';
  const paths = windows ? path.win32 : path.posix;
  env = normalizeEnvironment(env, platform);
  const extensions = windows && !paths.extname(command)
    ? [...String(env.PATHEXT || '.COM;.EXE;.BAT;.CMD').split(';').filter(Boolean), ''] : [''];
  const directories = paths.isAbsolute(command) || command.includes(paths.sep)
    ? [''] : String(env.PATH || '').split(windows ? ';' : ':').filter(Boolean);
  for (const directory of directories) {
    for (const extension of extensions) {
      const candidate = paths.join(directory.replace(/^"|"$/g, ''), command + extension);
      try {
        fs.accessSync(candidate, windows ? fs.constants.F_OK : fs.constants.X_OK);
        if (fs.statSync(candidate).isFile()) return candidate;
      } catch {}
    }
  }
  return null;
}

// Recognize the Node invocation in npm's generated shim, including extensionless
// JavaScript entrypoints. Do not interpret arbitrary batch scripts or select the
// earlier node.exe existence check as the application entrypoint.
export function npmShimEntrypoint(shim) {
  const match = shim.match(/"(?:%_prog%|%dp0%[\\/]node\.exe|node(?:\.exe)?)"\s+"%dp0%[\\/]([^"\r\n]+)"\s+%\*/i);
  return match?.[1] ?? null;
}

// Run npm's Node entrypoint directly. Never interpolate model/user arguments
// into cmd.exe: its expansion rules differ from CreateProcess argument quoting.
export function executableInvocation(command, args, { platform = process.platform, env = process.env } = {}) {
  if (platform !== 'win32') return { command, args };
  const resolved = findExecutable(command, env, platform) || command;
  if (/\.(?:cmd|bat)$/i.test(resolved)) {
    const shim = fs.readFileSync(resolved, 'utf8');
    const relativeEntrypoint = npmShimEntrypoint(shim);
    if (!relativeEntrypoint) throw new Error(`Unsupported Windows command shim: ${resolved}. Configure the native .exe or Node entrypoint.`);
    const entrypoint = path.resolve(path.dirname(resolved), relativeEntrypoint.replaceAll('\\', path.sep));
    if (!fs.statSync(entrypoint).isFile()) throw new Error(`Missing npm entrypoint: ${entrypoint}`);
    return { command: process.execPath, args: [entrypoint, ...args] };
  }
  if (/\.[cm]?js$/i.test(resolved) || /[\\/]node_modules[\\/]pm2[\\/]bin[\\/]pm2$/i.test(resolved)) {
    return { command: process.execPath, args: [resolved, ...args] };
  }
  return { command: resolved, args };
}

export function spawn(command, args = [], options = {}) {
  if (process.platform !== 'win32') return nodeSpawn(...arguments);
  if (!Array.isArray(args)) { options = args ?? {}; args = []; }
  const invocation = executableInvocation(command, args, { env: options.env });
  return nodeSpawn(invocation.command, invocation.args, { windowsHide: true, ...options, env: normalizeEnvironment(options?.env) });
}

export function execFile(command, args, options, callback) {
  if (process.platform !== 'win32') return nodeExecFile(...arguments);
  if (typeof args === 'function') { callback = args; args = []; options = {}; }
  else if (!Array.isArray(args)) { callback = typeof options === 'function' ? options : callback; options = args ?? options ?? {}; args = []; }
  if (typeof options === 'function') { callback = options; options = {}; }
  const invocation = executableInvocation(command, args, { env: options?.env });
  return nodeExecFile(invocation.command, invocation.args, { windowsHide: true, ...options, env: normalizeEnvironment(options?.env) }, callback);
}

export function localSocketPath(directory, name = 'actions', platform = process.platform) {
  if (platform !== 'win32') return path.posix.join(directory, `${name}.sock`);
  const identity = path.win32.resolve(directory).toLowerCase();
  const digest = createHash('sha256').update(identity).digest('hex').slice(0, 32);
  return `\\\\.\\pipe\\vika-${name}-${digest}`;
}

export function isNamedPipe(value) {
  return typeof value === 'string' && value.startsWith('\\\\.\\pipe\\');
}

execFile[promisify.custom] = process.platform !== 'win32' ? promisify(nodeExecFile) : (command, args, options = {}) => {
  if (!Array.isArray(args)) { options = args ?? options; args = []; }
  const invocation = executableInvocation(command, args, { env: options.env });
  return promisify(nodeExecFile)(invocation.command, invocation.args, { windowsHide: true, ...options, env: normalizeEnvironment(options?.env) });
};
