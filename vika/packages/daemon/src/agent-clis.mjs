// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { bundledLarkCli } from './lark-cli-bin.mjs';
import { bundledSlackCli } from './slack/cli.mjs';

// One catalog for every local runtime's PATH and command discovery prompt.
// Add future bundled CLIs here after wiring their installation and config.
export function agentCliCommands(config = {}) {
  return [
    { name: 'lark-cli', bin: config.larkCliBin || bundledLarkCli(), versionArgs: ['--version'] },
    { name: 'slack', bin: config.slackCliBin || bundledSlackCli(), versionArgs: ['version', '--skip-update', '--no-color'] },
  ];
}

// Quote a single executable, not a shell command with arguments.
export function cliShellCommand(bin) {
  return /^[a-zA-Z0-9_@%+=:,./-]+$/.test(bin) ? bin : `'${bin.replaceAll("'", "'\\''")}'`;
}

export function cliCommandCode(bin, args = '') {
  const command = cliShellCommand(bin) + args;
  const fence = '`'.repeat(Math.max(0, ...(command.match(/`+/g) || []).map(run => run.length)) + 1);
  return `${fence}${command}${fence}`;
}
