// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { actionEndpoint, listTools } from './actions/client.mjs';
import { PI_MCP_STATUS } from './pi-runtime.mjs';

// Loaded explicitly only by the daemon's Pi process. Nothing is written to
// ~/.pi or the workspace, and no tools are reimplemented in this extension.
export default function personalAgentMcp(pi) {
  const config = JSON.parse(process.env.PERSONAL_AGENT_MCP_CONFIG);
  pi.registerMcpServer('personal_agent', { ...config, exposure: 'direct' });

  let stopped = false;
  pi.on('session_shutdown', () => { stopped = true; });
  pi.on('session_start', (_event, ctx) => {
    stopped = false;
    // Other session_start handlers (including built-in MCP) must run before
    // waiting for their connections. A UI notification is RPC telemetry, not
    // a model message or a user turn.
    void ready(ctx).then(() => {
      if (!stopped) ctx.ui.notify(`${PI_MCP_STATUS}:ready`, 'info');
    }).catch(() => {
      if (!stopped) ctx.ui.notify(`${PI_MCP_STATUS}:failed`, 'error');
    });
  });

  async function ready(ctx) {
    // Pi gives file-configured servers priority over extension registrations.
    // Fail rather than silently connecting this bot to a different server.
    const agentDir = process.env.PI_CODING_AGENT_DIR || path.join(os.homedir(), '.pi', 'agent');
    for (const file of [path.join(agentDir, 'mcp.json'), path.join(ctx.cwd, '.pi', 'mcp.json')]) {
      let contents;
      try { contents = fs.readFileSync(file, 'utf8'); } catch (error) {
        if (error.code === 'ENOENT') continue;
        throw error;
      }
      if (Object.keys(JSON.parse(contents).mcpServers ?? {}).some((name) => name.replaceAll('-', '_') === 'personal_agent')) {
        throw new Error('personal_agent is reserved for the daemon session');
      }
    }
    const definitions = await listTools(actionEndpoint('mcp'));
    const names = definitions.map(({ name }) => `mcp__personal_agent__${name}`);
    if (!names.length) throw new Error('Core offered no tools');
    const deadline = Date.now() + 10_000;
    while (!stopped && Date.now() < deadline) {
      const active = new Set(pi.getActiveTools());
      const direct = new Set(pi.getAllTools().filter((tool) => tool.exposure === 'direct').map((tool) => tool.name));
      if (names.every((name) => active.has(name) && direct.has(name))) {
        // OpenViking SuperAgent owns OpenViking: the extension of Pi's own registers bare
        // openviking_* tools, which must not be here (ov-isolation.mjs).
        const foreign = pi.getAllTools().map((tool) => tool.name).filter((name) => /^openviking_/.test(name));
        if (foreign.length) throw new Error(`Pi loaded OpenViking's own extension (${foreign.join(', ')}); OpenViking SuperAgent runs Pi without it`);
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    throw new Error('Pi did not expose the Core MCP tools directly');
  }
}
