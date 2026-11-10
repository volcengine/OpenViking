// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import { promisify } from 'node:util';
import path from 'node:path';
import { createHash, randomBytes } from 'node:crypto';
import os from 'node:os';
import { spawn as nodeSpawn, execFile as nodeExecFile } from 'node:child_process';

// Object spreads can retain Windows' inherited `Path` beside an explicit
// `PATH` override. Node otherwise chooses one duplicate lexicographically.
// Use the explicit uppercase override consistently for lookup and the child.
export function normalizeEnvironment(env = process.env) {
  env ??= process.env;
  const result = { ...env };
  for (const name of ['PATH', 'PATHEXT', 'TEMP', 'TMP', 'USERPROFILE', 'SYSTEMROOT', 'COMSPEC', 'APPDATA', 'LOCALAPPDATA']) {
    const keys = Object.keys(env).filter(key => key.toUpperCase() === name);
    if (!keys.length) continue;
    const value = env[name] ?? env[keys[0]];
    for (const key of keys) delete result[key];
    result[name] = value;
  }
  return result;
}

export function findExecutable(command, env = process.env) {
  const paths = path.win32;
  env = normalizeEnvironment(env);
  const extensions = !paths.extname(command)
    ? [...String(env.PATHEXT || '.COM;.EXE;.BAT;.CMD').split(';').filter(Boolean), ''] : [''];
  const directories = paths.isAbsolute(command) || command.includes(paths.sep) || command.includes('/')
    ? [''] : String(env.PATH || '').split(';').filter(Boolean);
  for (const directory of directories) {
    for (const extension of extensions) {
      const candidate = paths.join(directory.replace(/^"|"$/g, ''), command + extension);
      try {
        fs.accessSync(candidate, fs.constants.F_OK);
        if (fs.statSync(candidate).isFile()) return candidate;
      } catch {}
    }
  }
  return null;
}

// Recognize the Node invocation in npm's generated shim, including extensionless
// JavaScript entrypoints. Do not interpret arbitrary batch scripts or select the
// earlier node.exe existence check as the application entrypoint.
function npmShimEntrypoint(shim) {
  const match = shim.match(/"(?:%_prog%|%dp0%[\\/]node\.exe|node(?:\.exe)?)"\s+"%dp0%[\\/]([^"\r\n]+)"\s+%\*/i);
  return match?.[1] ?? null;
}

// An OpenViking SuperAgent release ships bin\pm2.cmd, so `pm2` typed in a terminal uses that
// release's own pm2 and Node. It is our file, with a known layout: launch the
// entrypoint it wraps instead of reading it as an npm shim, and never pass the
// arguments to cmd.exe.
function releaseShimInvocation(resolved, args) {
  if (path.win32.basename(resolved).toLowerCase() !== 'pm2.cmd') return null;
  const release = path.dirname(path.dirname(resolved));
  const entrypoint = path.join(release, 'node_modules', 'pm2', 'bin', 'pm2');
  if (!fs.existsSync(entrypoint)) return null;
  const node = [path.join(release, 'runtime', 'node.exe'), path.join(release, 'node', 'bin', 'node.exe')]
    .find(candidate => fs.existsSync(candidate)) || process.execPath;
  return { command: node, args: [entrypoint, ...args] };
}

// Run npm's Node entrypoint directly. Never interpolate model/user arguments
// into cmd.exe: its expansion rules differ from CreateProcess argument quoting.
export function executableInvocation(command, args, { env = process.env } = {}) {
  const resolved = findExecutable(command, env) || command;
  if (/\.(?:cmd|bat)$/i.test(resolved)) {
    const release = releaseShimInvocation(resolved, args);
    if (release) return release;
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
  const extension = path.win32.extname(resolved).toLowerCase();
  if (extension && !['.exe', '.com'].includes(extension)) {
    throw new Error(`Unsupported Windows executable entrypoint: ${resolved}. Invoke scripts through an explicit interpreter (for example pwsh.exe -File script.ps1).`);
  }
  return { command: resolved, args };
}

export function spawn(command, args = [], options = {}) {
  if (!Array.isArray(args)) { options = args ?? {}; args = []; }
  const invocation = executableInvocation(command, args, { env: options?.env });
  return nodeSpawn(invocation.command, invocation.args, { windowsHide: true, ...options, env: normalizeEnvironment(options?.env) });
}

export function execFile(command, args, options, callback) {
  if (typeof args === 'function') { callback = args; args = []; options = {}; }
  else if (!Array.isArray(args)) { callback = typeof options === 'function' ? options : callback; options = args ?? options ?? {}; args = []; }
  if (typeof options === 'function') { callback = options; options = {}; }
  const invocation = executableInvocation(command, args, { env: options?.env });
  return nodeExecFile(invocation.command, invocation.args, { windowsHide: true, ...options, env: normalizeEnvironment(options?.env) }, callback);
}

// Random for each daemon process: only the returned endpoint is passed to clients.
// This reduces guessing/squatting; Node does not expose pipe-owner verification.
const pipeNonce = randomBytes(24).toString('hex');
export function localSocketPath(directory, name = 'actions') {
  const identity = path.win32.resolve(directory).toLowerCase() + '\0' + os.homedir();
  const digest = createHash('sha256').update(identity).digest('hex').slice(0, 24);
  return `\\\\.\\pipe\\vika-${name}-${digest}-${pipeNonce}`;
}

export function isNamedPipe(value) {
  return typeof value === 'string' && value.startsWith('\\\\.\\pipe\\');
}

execFile[promisify.custom] = (command, args, options = {}) => {
  if (!Array.isArray(args)) { options = args ?? options; args = []; }
  const invocation = executableInvocation(command, args, { env: options?.env });
  return promisify(nodeExecFile)(invocation.command, invocation.args, { windowsHide: true, ...options, env: normalizeEnvironment(options?.env) });
};

export const spawnDetached = false;
export const processOptions = Object.freeze({ windowsHide: true });
export function homeDir(env = process.env) { return env.HOME || env.USERPROFILE; }
export function binName(name) { return name + '.exe'; }
export function expandHome(value, home) { return value === '~' ? home : /^~[\\/]/.test(value) ? path.join(home, value.slice(2)) : value; }
export function linkConfig(from, to) {
  if (fs.statSync(from).isDirectory()) fs.symlinkSync(path.resolve(from), to, 'junction');
  else fs.copyFileSync(from, to, fs.constants.COPYFILE_EXCL);
}
export function onShutdown(handler) {
  process.on('message', message => {
    if (message === 'shutdown') void handler('shutdown').then(() => {
      if (process.connected) process.disconnect();
    });
  });
}
export function terminateProcessTree(child, signal = 'SIGTERM') {
  if (!child?.pid || child.exitCode != null || child.signalCode != null) return;
  const killer = spawn('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
  killer.on('error', () => { try { child.kill(signal); } catch {} });
  killer.unref();
}
export function assertPrivateDirectory(directory) {
  const info = fs.lstatSync(directory);
  if (!info.isDirectory() || info.isSymbolicLink()) throw new Error(`Temporary directory must be a directory: ${directory}`);
  // No POSIX mode-bit assertion here. The user must provision a private directory.
}
export function preparePrivateDirectory(directory) {
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  assertPrivateDirectory(directory);
  return directory;
}
export function urlOpenCommand() { return 'explorer.exe'; }

export const maxCommandLineLength = 32767;
export const supportsPosixSignals = false;
export const supportsFileModes = false;
export const supportsShellScripts = false;
export const pathDelimiter = ';';
// No service manager to register with: `ovsa autostart` writes the user's own
// Startup folder instead, which needs no elevation.
export const loginService = 'startupFolder';
export const systemEnvironmentKeys = Object.freeze(['USERPROFILE', 'SYSTEMROOT', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT', 'HOMEDRIVE', 'HOMEPATH', 'USERNAME']);
export const hostPlatformId = `win32-${process.arch}`;
export const environmentCaseSensitive = false;

// Native command tool used by the managed Durable runtime.
export const codingShell = 'powershell';
