// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// A Claude Code / Codex hook for a worker (workers scheduler): what is routed
// to its task while it works joins the run, as pi-mcp-extension.mjs does for
// Pi. Both CLIs take the same hook format. After a tool call (PostToolUse) new
// task inputs are added as context; when the run is about to end (Stop) they
// keep it going instead. The hook reads them with task_check under the
// worker's authority (PERSONAL_AGENT_SOCKET / PERSONAL_AGENT_AUTHORITY), so a
// line is claimed once, by whichever path sees it first.

import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { actionEndpoint, callTool } from './actions/client.mjs';

export const TASK_INPUT_HOOK = fileURLToPath(import.meta.url);
export const TASK_INPUT_EVENTS = Object.freeze(['PostToolUse', 'Stop']);

// The hook's answer to one event, or null to leave the run as it is.
export async function taskInputHookOutput(event, check = () => callTool(actionEndpoint('mcp'), 'task_check', {})) {
  const name = event?.hook_event_name;
  if (!TASK_INPUT_EVENTS.includes(name)) return null;
  let result;
  try { result = await check(); } catch { return null; }
  if (!result?.data?.count) return null;
  const text = `[task input] ${result.text}\nHandle what changes or answers your task; hand back with task_handback what does not belong to it.`;
  return name === 'Stop'
    ? { decision: 'block', reason: text }
    : { hookSpecificOutput: { hookEventName: 'PostToolUse', additionalContext: text } };
}

// The command line a CLI runs for the hook: this daemon's node and this file.
export function taskInputHookCommand() {
  return `${JSON.stringify(process.execPath)} ${JSON.stringify(TASK_INPUT_HOOK)}`;
}

if (process.argv[1] && path.resolve(process.argv[1]) === TASK_INPUT_HOOK) {
  let input = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (chunk) => { input += chunk; });
  process.stdin.on('end', async () => {
    let event = null;
    try { event = JSON.parse(input); } catch { /* not ours */ }
    const output = await taskInputHookOutput(event);
    if (output) process.stdout.write(JSON.stringify(output));
  });
}
