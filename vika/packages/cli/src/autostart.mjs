// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import * as defaultPlatform from '../../daemon/src/platform/index.mjs';
import { capabilitiesFor } from '../../daemon/src/capabilities.mjs';

import { execFile as execFileCallback } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import { t } from './i18n.mjs';

// `vika autostart enable|disable`: start Vika at login/boot without sudo.
// Linux: a systemd user unit; macOS: a LaunchAgent; Windows: a script in the
// user's own Startup folder. All three run `vika start`, the same path as by
// hand, with the PATH of the shell that enabled it so the agent CLIs (codex,
// claude) are found under a minimal service environment.

const execFile = promisify(execFileCallback);
const LABEL = 'com.vika.agent';
const STARTUP_ENTRY = 'vika-autostart.vbs';

export function systemdUnit({ homeDir, pathValue }) {
  return `[Unit]
Description=Vika (local agent instances)
After=network-online.target

[Service]
Type=forking
PIDFile=${path.join(homeDir, '.vika', 'pm2', 'pm2.pid')}
Environment=PATH=${pathValue}
ExecStart=${path.join(homeDir, '.vika', 'bin', 'vika')} start
ExecStop=${path.join(homeDir, '.vika', 'bin', 'vika')} stop
Restart=on-failure

[Install]
WantedBy=default.target
`;
}

function xml(value) {
  return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export function launchAgent({ homeDir, pathValue }) {
  const log = path.join(homeDir, '.vika', 'logs', 'autostart.log');
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${xml(path.join(homeDir, '.vika', 'bin', 'vika'))}</string>
    <string>start</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>${xml(pathValue)}</string></dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>${xml(log)}</string>
  <key>StandardErrorPath</key><string>${xml(log)}</string>
</dict>
</plist>
`;
}

function targets(homeDir, platform, env = process.env) {
  if (platform.loginService === 'launchd') {
    return { file: path.join(homeDir, 'Library', 'LaunchAgents', `${LABEL}.plist`) };
  }
  if (platform.loginService === 'startupFolder') {
    // The shell runs this folder at logon. Windows has no per-user service
    // manager to ask instead, and a scheduled task would need more privilege
    // than the feature is worth.
    const roaming = env.APPDATA || path.join(env.USERPROFILE || homeDir, 'AppData', 'Roaming');
    return { file: path.join(roaming, 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup', STARTUP_ENTRY) };
  }
  return { file: path.join(homeDir, '.config', 'systemd', 'user', 'vika.service') };
}

// A .vbs starts the launcher with no console window; a .cmd in the Startup
// folder would flash one at every logon. UTF-16LE with a BOM because WSH reads
// anything else as ANSI and a profile path need not be ASCII.
export function startupEntry(homeDir, pathValue = null) {
  const vb = (value) => String(value).replaceAll('"', '""');
  const launcher = path.join(homeDir, '.vika', 'bin', 'vika.cmd');
  const logs = path.join(homeDir, '.vika', 'logs');
  const log = path.join(logs, 'autostart.log');
  return [
    "' Vika: start it when this user logs in. Written by `vika autostart enable`.",
    "' Delete this file, or run `vika autostart disable`, to stop it.",
    'Option Explicit',
    'Dim shell, q, command',
    'Set shell = CreateObject("WScript.Shell")',
    'q = Chr(34)',
    `shell.Environment("PROCESS")("HOME") = "${vb(homeDir)}"`,
    ...(pathValue == null ? [] : [`shell.Environment("PROCESS")("PATH") = "${vb(pathValue)}"`]),
    // mkdir first: the redirect would fail, silently, if the directory were gone.
    `command = "cmd /d /c (if not exist " & q & "${vb(logs)}" & q & " mkdir " & q & "${vb(logs)}" & q & ")"`,
    `command = command & " & " & q & "${vb(launcher)}" & q & " start >> " & q & "${vb(log)}" & q & " 2>&1"`,
    'shell.Run command, 0, False',
  ].join('\r\n') + '\r\n';
}

export function autostartEnabled({ env = process.env, homeDir = defaultPlatform.homeDir(env) || os.homedir(), platform = defaultPlatform } = {}) {
  return capabilitiesFor(platform).autostart && fs.existsSync(targets(homeDir, platform, env).file);
}

export async function autostartCommand([action, ...extra], {
  env = process.env,
  homeDir = defaultPlatform.homeDir(env) || os.homedir(),
  platform = defaultPlatform,
  execFileImpl = execFile,
  output = process.stdout,
  uid = process.getuid?.(),
} = {}) {
  if (!['enable', 'disable'].includes(action) || extra.length) {
    output.write('usage: vika autostart <enable|disable>\n');
    return 2;
  }
  if (!capabilitiesFor(platform).autostart) {
    output.write(t('这台系统没有可用的开机自启方式\n', 'Autostart is not available on this system\n'));
    return 1;
  }
  const { file } = targets(homeDir, platform, env);
  if (platform.loginService === 'startupFolder') {
    return action === 'disable'
      ? disableStartupEntry({ file, output })
      : enableStartupEntry({ file, homeDir, env, output });
  }
  if (action === 'disable') {
    if (platform.loginService === 'launchd') await execFileImpl('launchctl', ['bootout', `gui/${uid}`, file]).catch(() => {});
    else await execFileImpl('systemctl', ['--user', 'disable', '--now', 'vika.service']).catch(() => {});
    fs.rmSync(file, { force: true });
    if (platform.loginService !== 'launchd') await execFileImpl('systemctl', ['--user', 'daemon-reload']).catch(() => {});
    output.write(t('开机自启已关闭\n', 'Autostart is off\n'));
    return 0;
  }
  const pathValue = env.PATH || '/usr/bin:/bin';
  fs.mkdirSync(path.dirname(file), { recursive: true });
  if (platform.loginService === 'launchd') {
    fs.mkdirSync(path.join(homeDir, '.vika', 'logs'), { recursive: true });
    fs.writeFileSync(file, launchAgent({ homeDir, pathValue }));
    await execFileImpl('launchctl', ['bootout', `gui/${uid}`, file]).catch(() => {});
    await execFileImpl('launchctl', ['bootstrap', `gui/${uid}`, file]);
    output.write(t(`开机自启已开启（${file}）\n`, `Autostart is on (${file})\n`));
    return 0;
  }
  fs.writeFileSync(file, systemdUnit({ homeDir, pathValue }));
  await execFileImpl('systemctl', ['--user', 'daemon-reload']);
  await execFileImpl('systemctl', ['--user', 'enable', 'vika.service']);
  output.write(t(`开机自启已开启（${file}）\n`, `Autostart is on (${file})\n`));
  output.write(t('服务器上还要执行一次 loginctl enable-linger，否则退出登录后 Vika 会停。\n',
    'On a server, also run loginctl enable-linger once, or Vika stops when you log out.\n'));
  return 0;
}


// Windows: the Startup folder needs no elevation, and the user can see, disable
// or delete the entry themselves (Task Manager lists it on its Startup tab).
function enableStartupEntry({ file, homeDir, env, output }) {
  const launcher = path.join(homeDir, '.vika', 'bin', 'vika.cmd');
  if (!fs.existsSync(launcher)) {
    output.write(t(`没找到 ${launcher}：先运行安装脚本，再开启开机自启\n`,
      `${launcher} is missing: run the installer first, then enable autostart\n`));
    return 1;
  }
  fs.mkdirSync(path.join(homeDir, '.vika', 'logs'), { recursive: true });
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const text = startupEntry(homeDir, env.PATH ?? env.Path ?? null);
  fs.writeFileSync(file, Buffer.concat([Buffer.from([0xff, 0xfe]), Buffer.from(text, 'utf16le')]));
  output.write(t(`开机自启已开启（${file}）\n`, `Autostart is on (${file})\n`));
  output.write(t('登录这个用户后自动启动，不是系统服务，也不需要管理员；输出在 ~/.vika/logs/autostart.log\n',
    'It starts when this user logs in, as no service and with no administrator; output goes to ~/.vika/logs/autostart.log\n'));
  return 0;
}

function disableStartupEntry({ file, output }) {
  fs.rmSync(file, { force: true });
  output.write(t('开机自启已关闭\n', 'Autostart is off\n'));
  return 0;
}
