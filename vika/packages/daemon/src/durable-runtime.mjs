// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { agentCliCommands } from './agent-clis.mjs';
import { sanitizeAgentChildEnv } from './process-utils.mjs';

// The release's dependency is also the supported version of the durable host.
// Updating it is a reviewed package/lockfile change, never an implicit @latest.
const manifest = JSON.parse(fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
export const DURABLE_VERSION = manifest.dependencies['@earendil-works/pi-durable'];
// v2: the daemon owns the session → durable-conversation mapping and hands the
// handle to the host in `hello`; the host keeps no local session index.
export const DURABLE_PROTOCOL = 2;
export const DURABLE_HOST = fileURLToPath(new URL('./durable-host.mjs', import.meta.url));

export function durableHostPath(override = '') {
  return override || DURABLE_HOST;
}

// The durable host is a Node script supervised by the daemon. Run it with the
// daemon's own Node so pm2's PATH cannot pick another interpreter.
export function durableCommand(hostPath) {
  return { command: process.execPath, args: [hostPath] };
}

export function assertDurableVersion(reported) {
  if (String(reported).trim() !== DURABLE_VERSION) {
    throw new Error(`pi-durable ${DURABLE_VERSION} is required by this release; run npm ci (host reported ${String(reported).trim() || 'nothing'})`);
  }
}

export function sanitizeDurableEnv(environment, profile = '', actions = null, config = {}) {
  // Model providers (OpenAI, Anthropic, DeepSeek, …) read their key from this
  // child's environment, exactly as pi-ai documents, or from Pi's auth.json.
  // As for Pi, ARK_API_KEY survives the sanitizer: a Volcengine Ark provider in
  // ~/.pi/agent/models.json may name it as its key.
  const safeEnv = sanitizeAgentChildEnv(environment, { profile, actions, cliBins: agentCliCommands(config).map(({ bin }) => bin) });
  if (typeof environment.ARK_API_KEY === 'string' && environment.ARK_API_KEY) {
    safeEnv.ARK_API_KEY = environment.ARK_API_KEY;
  }
  return safeEnv;
}
