// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// The release's dependency is also the supported version for an explicit PI_BIN.
// Updating Pi is a reviewed package/lockfile change, never an implicit @latest.
const manifest = JSON.parse(fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
export const PI_VERSION = manifest.dependencies['@earendil-works/pi-coding-agent'];
export const PI_MCP_EXTENSION = fileURLToPath(new URL('./pi-mcp-extension.mjs', import.meta.url));
export const PI_MCP_STATUS = 'personal-agent-mcp';

// Pi's bash has no default timeout: a command that never ends (a search over
// a whole home directory, a server started in the foreground) holds the turn,
// and a worker's task, until someone stops it. A Pi session that does the work
// itself (a worker, or a router of the legacy or inbox scheduler) gives a
// command without a timeout PI_SHELL_DEFAULT_S, and lets none ask for more than
// PI_SHELL_MAX_S (pi-mcp-extension.mjs). Builds and test runs can take longer
// than the default; the policy tells the model to give them their own.
export const PI_SHELL_DEFAULT_S = 300;
export const PI_SHELL_MAX_S = 3_600;

export function piShellTimeoutEnv() {
  return { PERSONAL_AGENT_SHELL_DEFAULT_S: String(PI_SHELL_DEFAULT_S), PERSONAL_AGENT_SHELL_MAX_S: String(PI_SHELL_MAX_S) };
}

export function managedPiBin() {
  const entry = fileURLToPath(import.meta.resolve('@earendil-works/pi-coding-agent'));
  return path.join(path.dirname(entry), 'bundle', 'cli.js');
}

// The managed Pi is a Node script. Run it with the daemon's own Node: its
// `#!/usr/bin/env node` line would take whichever node is first on PATH, and
// pm2 puts /usr/bin ahead of the user's Node. An explicit PI_BIN runs as given.
export function piCommand(piBin, args) {
  return piBin === managedPiBin()
    ? { command: process.execPath, args: [piBin, ...args] }
    : { command: piBin, args };
}

// Pi enables the bash tool by default; on Windows that resolves to the System32
// WSL launcher and fails when WSL is not installed. The powershell tool is Pi's
// Windows equivalent (it prefers pwsh 7), so every session this daemon starts
// selects it and drops bash. The +/- form keeps the other default tools.
export function piToolArgs(platform = process.platform) {
  return platform === 'win32' ? ['--tools', '+powershell,-bash'] : [];
}

export function assertPiVersion(output) {
  if (String(output).trim() !== PI_VERSION) {
    throw new Error(`Pi ${PI_VERSION} is required by this release; run npm ci and remove PI_BIN to use the managed version, or point PI_BIN at Pi ${PI_VERSION}`);
  }
}
