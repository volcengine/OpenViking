// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { execFile as execFileCallback } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import { t } from './i18n.mjs';

// `vika autostart enable|disable`: start Vika at login/boot without sudo.
// Linux: a systemd user unit; macOS: a LaunchAgent. Both run `vika start`,
// the same path as by hand, with the PATH of the shell that enabled it so the
// agent CLIs (codex, claude) are found under a minimal service environment.

const execFile = promisify(execFileCallback);
const LABEL = 'com.vika.agent';

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

function targets(homeDir, platform) {
  return platform === 'darwin'
    ? { file: path.join(homeDir, 'Library', 'LaunchAgents', `${LABEL}.plist`) }
    : { file: path.join(homeDir, '.config', 'systemd', 'user', 'vika.service') };
}

export function autostartEnabled({ homeDir = os.homedir(), platform = process.platform } = {}) {
  return fs.existsSync(targets(homeDir, platform).file);
}

export async function autostartCommand([action, ...extra], {
  env = process.env,
  homeDir = os.homedir(),
  platform = process.platform,
  execFileImpl = execFile,
  output = process.stdout,
  uid = process.getuid?.(),
} = {}) {
  if (!['enable', 'disable'].includes(action) || extra.length) {
    output.write('usage: vika autostart <enable|disable>\n');
    return 2;
  }
  const { file } = targets(homeDir, platform);
  if (action === 'disable') {
    if (platform === 'darwin') await execFileImpl('launchctl', ['bootout', `gui/${uid}`, file]).catch(() => {});
    else await execFileImpl('systemctl', ['--user', 'disable', '--now', 'vika.service']).catch(() => {});
    fs.rmSync(file, { force: true });
    if (platform !== 'darwin') await execFileImpl('systemctl', ['--user', 'daemon-reload']).catch(() => {});
    output.write(t('开机自启已关闭\n', 'Autostart is off\n'));
    return 0;
  }
  const pathValue = env.PATH || '/usr/bin:/bin';
  fs.mkdirSync(path.dirname(file), { recursive: true });
  if (platform === 'darwin') {
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
