// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { actionEndpoint, listTools } from './actions/client.mjs';
import { PI_MCP_STATUS } from './pi-runtime.mjs';

// Loaded explicitly only by the daemon's Pi process. Nothing is written to
// ~/.pi or the workspace, and no tools are reimplemented in this extension.
// Pi's own tools that do work, as against the Core tools a router routes with.
const WORK_TOOLS = new Set(['bash', 'read', 'edit', 'write', 'grep', 'find', 'ls']);

export default function personalAgentMcp(pi) {
  const config = JSON.parse(process.env.PERSONAL_AGENT_MCP_CONFIG);
  pi.registerMcpServer('personal_agent', { ...config, exposure: 'direct' });

  let stopped = false;
  pi.on('session_shutdown', () => { stopped = true; });

  // A router (workers scheduler) hands longer work to tasks: its own shell
  // commands are capped and may not sleep, and each run gets a few calls of
  // its own work tools; past them, the work belongs to a task. A capped
  // command alone does not stop many short ones in a row.
  const shellLimit = Number(process.env.PERSONAL_AGENT_SHELL_LIMIT_S);
  const workBudget = Number(process.env.PERSONAL_AGENT_WORK_TOOL_BUDGET);
  let workUsed = 0;
  if (shellLimit > 0) {
    pi.on('agent_start', () => { workUsed = 0; });
    pi.on('tool_call', (event) => {
      if (workBudget > 0 && WORK_TOOLS.has(event.toolName)) {
        workUsed += 1;
        if (workUsed > workBudget) {
          return { block: true, reason: `You have used your own tools ${workBudget} times in this turn: what still needs work is a task. Hand it to a worker with task_create, tell the requester once, and go on with your other inputs.` };
        }
      }
      if (event.toolName !== 'bash' || !event.input) return undefined;
      if (/(^|[\s;&|(`])sleep\s/.test(String(event.input.command ?? ''))) {
        return { block: true, reason: 'Your own shell may not sleep or wait; hand work that waits to a task (task_create).' };
      }
      const timeout = Number(event.input.timeout);
      event.input.timeout = timeout > 0 ? Math.min(timeout, shellLimit) : shellLimit;
      return undefined;
    });
  }
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
