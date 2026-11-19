// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Claude Code, Codex and TraeX use the same PreToolUse protocol. The daemon
// owns the counter: parallel hook processes share one budget, and steering
// more input into a turn never replenishes it. Workers do not install this hook.
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { actionEndpoint, requestAction } from './actions/client.mjs';
import { isRouterWorkTool } from './router-tool-budget.mjs';

export const ROUTER_TOOL_HOOK = fileURLToPath(import.meta.url);
export function routerToolHookCommand() {
  return `${JSON.stringify(process.execPath)} ${JSON.stringify(ROUTER_TOOL_HOOK)}`;
}

export function routerToolHooks() {
  return { PreToolUse: [{ hooks: [{ type: 'command', command: routerToolHookCommand(), timeout: 10 }] }] };
}

export function routerToolHookArgs() {
  return ['-c', `hooks.PreToolUse=[{hooks=[{type="command",command=${JSON.stringify(routerToolHookCommand())},timeout=10}]}]`];
}

export async function routerToolHookOutput(event, check = async (input) => {
  const response = await requestAction(actionEndpoint('hook'), 'POST', '/v1/router-tool-check', input,
    { signal: AbortSignal.timeout(5_000) });
  if (!response.ok) throw new Error(response.error || 'router budget unavailable');
  return response.result;
}) {
  if (event?.hook_event_name !== 'PreToolUse' || !isRouterWorkTool(event.tool_name)) return null;
  let reason;
  try { reason = (await check({ tool: event.tool_name }))?.reason; } catch {
    // An unavailable guard must not silently permit unbounded local work.
    reason = 'The router tool budget could not be checked. Use task_create for this work; do not retry your own work tools in this turn.';
  }
  return reason ? { hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: 'deny', permissionDecisionReason: reason } } : null;
}

if (process.argv[1] && path.resolve(process.argv[1]) === ROUTER_TOOL_HOOK) {
  let input = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (chunk) => { input += chunk; });
  process.stdin.on('end', async () => {
    let event;
    try { event = JSON.parse(input); } catch { process.stderr.write('Invalid router tool hook input'); process.exitCode = 2; return; }
    const output = await routerToolHookOutput(event);
    if (output) process.stdout.write(JSON.stringify(output));
  });
}
