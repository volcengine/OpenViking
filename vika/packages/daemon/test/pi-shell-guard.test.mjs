// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import personalAgentMcp from '../src/pi-mcp-extension.mjs';

// The extension's handlers, as Pi would register them.
function load(env) {
  const saved = { ...process.env };
  Object.assign(process.env, { PERSONAL_AGENT_MCP_CONFIG: JSON.stringify({ command: 'node', args: [] }) }, env);
  for (const [key, value] of Object.entries(env)) if (value === undefined) delete process.env[key];
  const handlers = new Map();
  try {
    personalAgentMcp({ registerMcpServer() {}, on(name, handler) { handlers.set(name, handler); } });
  } finally {
    for (const key of Object.keys(process.env)) if (!(key in saved)) delete process.env[key];
    Object.assign(process.env, saved);
  }
  return handlers;
}

describe('Pi router shell guard', () => {
  it('caps a router\'s shell commands and refuses sleep under the workers scheduler', () => {
    const toolCall = load({ PERSONAL_AGENT_SHELL_LIMIT_S: '30' }).get('tool_call');
    const call = (toolName, input) => {
      const event = { toolName, input: { ...input } };
      return { result: toolCall(event), input: event.input };
    };
    assert.deepEqual(call('bash', { command: 'npm test', timeout: 600 }).input.timeout, 30);
    assert.deepEqual(call('bash', { command: 'ls' }).input.timeout, 30);
    assert.deepEqual(call('bash', { command: 'ls', timeout: 5 }).input.timeout, 5);
    for (const command of ['sleep 60', 'make && sleep 5', 'echo hi; sleep 1', '(sleep 3)']) {
      assert.match(call('bash', { command }).result?.reason ?? '', /may not sleep or wait; hand work that waits to a task/, command);
    }
    assert.equal(call('bash', { command: 'grep -r sleeper .' }).result, undefined);
    assert.equal(call('read', { path: 'x' }).result, undefined);
  });

  it('gives a router run a budget of its own work tools, renewed at each run', () => {
    const handlers = load({ PERSONAL_AGENT_SHELL_LIMIT_S: '30', PERSONAL_AGENT_WORK_TOOL_BUDGET: '2' });
    const call = (toolName) => handlers.get('tool_call')({ toolName, input: { command: 'ls' } });
    assert.equal(call('bash'), undefined);
    assert.equal(call('read'), undefined);
    assert.equal(call('mcp__personal_agent__list_chat_messages'), undefined, 'Core tools are not counted');
    assert.match(call('grep')?.reason ?? '', /used your own tools 2 times in this turn: what still needs work is a task\. Hand it to a worker with task_create/);
    handlers.get('agent_start')();
    assert.equal(call('bash'), undefined);
  });

  it('leaves the shell alone otherwise', () => {
    assert.equal(load({ PERSONAL_AGENT_SHELL_LIMIT_S: undefined }).has('tool_call'), false);
  });
});
