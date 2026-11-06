// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import { promisify } from 'node:util';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { spawn as nodeSpawn, execFile as nodeExecFile } from 'node:child_process';

export function findExecutable(command, env = process.env, platform = process.platform) {
  const windows = platform === 'win32';
  const paths = windows ? path.win32 : path;
  const key = Object.keys(env).find(key => key.toUpperCase() === 'PATH');
  const extensions = windows && !paths.extname(command)
    ? [...String(env.PATHEXT || '.COM;.EXE;.BAT;.CMD').split(';').filter(Boolean), ''] : [''];
  const directories = paths.isAbsolute(command) || command.includes(paths.sep)
    ? [''] : String(env[key] || '').split(windows ? ';' : path.delimiter).filter(Boolean);
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

// Run npm's Node entrypoint directly. Never interpolate model/user arguments
// into cmd.exe: its expansion rules differ from CreateProcess argument quoting.
export function executableInvocation(command, args, { platform = process.platform, env = process.env } = {}) {
  if (platform !== 'win32') return { command, args };
  const resolved = findExecutable(command, env, platform) || command;
  if (/\.(?:cmd|bat)$/i.test(resolved)) {
    const shim = fs.readFileSync(resolved, 'utf8');
    const match = shim.match(/"%dp0%[\\/]([^"\r\n]+\.(?:[cm]?js))"/i);
    if (!match) throw new Error(`Unsupported Windows command shim: ${resolved}. Configure the native .exe or Node entrypoint.`);
    const entrypoint = path.resolve(path.dirname(resolved), match[1].replaceAll('\\', path.sep));
    if (!fs.statSync(entrypoint).isFile()) throw new Error(`Missing npm entrypoint: ${entrypoint}`);
    return { command: process.execPath, args: [entrypoint, ...args] };
  }
  if (/\.[cm]?js$/i.test(resolved) || /[\\/]node_modules[\\/]pm2[\\/]bin[\\/]pm2$/i.test(resolved)) {
    return { command: process.execPath, args: [resolved, ...args] };
  }
  return { command: resolved, args };
}

export function spawn(command, args = [], options = {}) {
  const invocation = executableInvocation(command, args, { env: options.env });
  return nodeSpawn(invocation.command, invocation.args, { windowsHide: true, ...options });
}

export function execFile(command, args, options, callback) {
  const invocation = executableInvocation(command, args, { env: options?.env });
  return nodeExecFile(invocation.command, invocation.args, { windowsHide: true, ...options }, callback);
}

export function localSocketPath(directory, name = 'actions', platform = process.platform) {
  if (platform !== 'win32') return path.join(directory, `${name}.sock`);
  const identity = path.win32.resolve(directory).toLowerCase();
  const digest = createHash('sha256').update(identity).digest('hex').slice(0, 32);
  return `\\\\.\\pipe\\vika-${name}-${digest}`;
}

export function isNamedPipe(value) {
  return typeof value === 'string' && value.startsWith('\\\\.\\pipe\\');
}

execFile[promisify.custom] = (command, args, options = {}) => {
  const invocation = executableInvocation(command, args, { env: options.env });
  return promisify(nodeExecFile)(invocation.command, invocation.args, { windowsHide: true, ...options });
};
