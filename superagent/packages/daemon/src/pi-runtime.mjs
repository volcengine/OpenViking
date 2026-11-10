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

export function assertPiVersion(output) {
  if (String(output).trim() !== PI_VERSION) {
    throw new Error(`Pi ${PI_VERSION} is required by this release; run npm ci and remove PI_BIN to use the managed version, or point PI_BIN at Pi ${PI_VERSION}`);
  }
}
