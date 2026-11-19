// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import personalAgentMcp, { WORKER_CWD } from '../src/pi-mcp-extension.mjs';

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
    assert.equal(load({ PERSONAL_AGENT_SHELL_LIMIT_S: undefined, PERSONAL_AGENT_SHELL_DEFAULT_S: undefined }).has('tool_call'), false);
  });
});

describe('Pi shell timeout', () => {
  const env = { PERSONAL_AGENT_SHELL_LIMIT_S: undefined, PERSONAL_AGENT_SHELL_DEFAULT_S: '300', PERSONAL_AGENT_SHELL_MAX_S: '3600' };

  it('gives a command without a timeout the default, keeps one the model chose, and caps it at the most', () => {
    const toolCall = load(env).get('tool_call');
    const timeout = (input) => {
      const event = { toolName: 'bash', input: { command: 'npm test', ...input } };
      assert.equal(toolCall(event), undefined, 'never refused');
      return event.input.timeout;
    };
    assert.equal(timeout({}), 300);
    assert.equal(timeout({ timeout: 0 }), 300);
    assert.equal(timeout({ timeout: 1_200 }), 1_200, 'a long build keeps the timeout it asked for');
    assert.equal(timeout({ timeout: 5 }), 5);
    assert.equal(timeout({ timeout: 86_400 }), 3_600);
    assert.equal(toolCall({ toolName: 'bash', input: { command: 'sleep 60 && gh run view 1' } }), undefined, 'a worker may wait');
    const read = { toolName: 'read', input: { path: 'x' } };
    assert.equal(toolCall(read), undefined);
    assert.deepEqual(read.input, { path: 'x' });
  });

  it('has no most unless one is given', () => {
    const event = { toolName: 'bash', input: { command: 'make', timeout: 86_400 } };
    load({ ...env, PERSONAL_AGENT_SHELL_MAX_S: undefined }).get('tool_call')(event);
    assert.equal(event.input.timeout, 86_400);
  });

  it('leaves a workers router to its own, shorter cap', () => {
    const handlers = load({ ...env, PERSONAL_AGENT_SHELL_LIMIT_S: '30' });
    const event = { toolName: 'bash', input: { command: 'npm test', timeout: 600 } };
    handlers.get('tool_call')(event);
    assert.equal(event.input.timeout, 30);
  });
});

describe('Pi worker task inputs', () => {
  // A daemon action socket that answers task_check with what the test queued.
  async function actionSocket(t, answers) {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pi-task-'));
    const socketPath = path.join(dir, 'actions.sock');
    const calls = [];
    const server = http.createServer((request, response) => {
      let body = '';
      request.on('data', (chunk) => { body += chunk; });
      request.on('end', () => {
        calls.push({ ...JSON.parse(body), authority: request.headers['x-agent-authority'] });
        const text = answers.shift();
        response.setHeader('content-type', 'application/json');
        response.end(JSON.stringify({ ok: true, result: text ? { text, data: { count: 1 } } : { text: 'No new inputs for your task.', data: { count: 0 } } }));
      });
    });
    await new Promise((resolve) => server.listen(socketPath, resolve));
    t.after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });
    return { socketPath, calls };
  }

  it('brings what is routed to the task into a running worker, after a step and before its run settles', async (t) => {
    const { socketPath, calls } = await actionSocket(t, [null, 'New inputs for your task, oldest first:\n[chat=oc_a msg=om_2 event=e2] 只看上周']);
    const handlers = load({ PERSONAL_AGENT_TASK_INPUTS: '1' });
    const saved = { ...process.env };
    Object.assign(process.env, { PERSONAL_AGENT_SOCKET: socketPath, PERSONAL_AGENT_AUTHORITY: 'worker-token' });
    t.after(() => { for (const key of ['PERSONAL_AGENT_SOCKET', 'PERSONAL_AGENT_AUTHORITY']) if (!(key in saved)) delete process.env[key]; });
    assert.equal(await handlers.get('turn_end')({}), undefined, 'nothing new: the run goes on as it would');
    const result = await handlers.get('agent_before_settle')({});
    assert.equal(result.continue, true);
    assert.equal(result.entries.length, 1);
    assert.deepEqual({ ...result.entries[0], content: undefined }, { type: 'custom_message', customType: 'task_input', display: true, content: undefined });
    assert.match(result.entries[0].content, /^\[task input\] New inputs for your task, oldest first:\n\[chat=oc_a msg=om_2 event=e2\] 只看上周\nHandle what changes or answers your task; hand back with task_handback what does not belong to it\.$/);
    assert.deepEqual(calls.map((call) => [call.tool, call.authority]), [['task_check', 'worker-token'], ['task_check', 'worker-token']]);
  });

  it('names one working directory in every worker\'s system prompt, so a forked session keeps its prefix', () => {
    const start = load({ PERSONAL_AGENT_TASK_INPUTS: '1' }).get('before_agent_start');
    const event = { systemPromptOptions: { cwd: '/srv/workspace/tasks/t2', appendSystemPrompt: 'policy' } };
    start(event);
    assert.deepEqual(event.systemPromptOptions, { cwd: WORKER_CWD, appendSystemPrompt: 'policy' });
    assert.match(WORKER_CWD, /the task directory your brief names \("Your directory: …"\); your shell and file tools start there/);
    assert.equal(load({ PERSONAL_AGENT_TASK_INPUTS: undefined }).has('before_agent_start'), false, 'a router keeps its own');
  });

  it('is off for the router and when the daemon cannot be reached', async () => {
    assert.equal(load({ PERSONAL_AGENT_TASK_INPUTS: undefined }).has('agent_before_settle'), false);
    const handlers = load({ PERSONAL_AGENT_TASK_INPUTS: '1' });
    const saved = process.env.PERSONAL_AGENT_SOCKET;
    process.env.PERSONAL_AGENT_SOCKET = path.join(os.tmpdir(), 'no-such-daemon.sock');
    try { assert.equal(await handlers.get('turn_end')({}), undefined); } finally {
      if (saved === undefined) delete process.env.PERSONAL_AGENT_SOCKET; else process.env.PERSONAL_AGENT_SOCKET = saved;
    }
  });
});
