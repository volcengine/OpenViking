// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// A router may take a quick look, then must give longer work to a task.
// Count actual local work tools, not orchestration wrappers (functions.exec),
// Core MCP tools, or message delivery. Those must remain usable at the cap.
export const ROUTER_WORK_TOOL_BUDGET = 6;
export const ROUTER_SHELL_LIMIT_S = 30;
const WORK_TOOLS = new Set([
  'bash', 'powershell', 'shell', 'shell_command', 'exec_command',
  'read', 'read_file', 'edit', 'edit_file', 'write', 'write_file', 'apply_patch',
  'multiedit', 'notebookedit', 'grep', 'grep_files', 'find', 'glob', 'ls', 'list_dir',
  'websearch', 'webfetch', 'view_image',
]);

export function isRouterWorkTool(name) {
  return typeof name === 'string' && WORK_TOOLS.has(name.toLowerCase());
}

export class RouterToolBudget {
  constructor(limit = ROUTER_WORK_TOOL_BUDGET) {
    this.limit = limit;
    this.reset();
  }

  reset() { this.used = 0; }

  check(name) {
    if (!(this.limit > 0) || !isRouterWorkTool(name)) return null;
    if (this.used >= this.limit) {
      return `You have used your own tools ${this.limit} times in this turn: what still needs work is a task. Hand it to a worker with task_create, tell the requester once, and go on with your other inputs.`;
    }
    this.used += 1;
    return null;
  }
}
