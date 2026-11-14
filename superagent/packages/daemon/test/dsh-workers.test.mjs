// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { test } from './dsh-node-gate.mjs';
import { createWorkerHandlers, DSH_WORKER_PRESET } from '../src/dsh-bridge/workers.mjs';

function fixture() {
  process.env.PERSONAL_AGENT_SOCKET = '/tmp/test-dsh-workers.sock';
  const listeners = new Map();
  const agents = new Map();
  const created = [];
  const toolsCalled = [];
  const authorities = [];
  const jobs = [];
  const ctx = {
    agents,
    on(name, fn) { listeners.set(name, fn); },
    get(name) {
      if (name === 'jobs') return {
        list: () => jobs,
        kill(id, owner) { jobs.find(job => job.id === id).killedBy = owner; },
      };
      if (name === 'sessionPersistence') return { async stat() { return null; }, async flush() {} };
      if (name === 'llm') return { async resolveCallConfig(value) { return value; } };
      return undefined;
    },
  };
  agents.create = async (options) => {
    created.push(options);
    const registered = new Map();
    const agent = {
      id: options.sessionId, status: 'idle', inbox: { nextTurn: [], nextStep: [] },
      session: {
        id: options.sessionId, events: [],
        snapshotEvents() { return this.events; },
      },
      model: { selections: [], select(value) { this.selections.push(value); } },
      ctx: {
        systemPrompt: { section() { return () => {}; } },
        tools: {
          get() { return null; },
          restrict() { return () => {}; },
          register(definition) { registered.set(definition.name, definition); return () => {}; },
        },
      },
      registered,
      followup(message) {
        this.inbox.nextTurn.push(message);
        this.session.events.push({ type: 'user/message', data: message });
        this.inbox.nextTurn = [];
        this.status = 'running';
      },
      cancel() { this.status = 'idle'; listeners.get('agent/status')?.({ agent: this, status: 'idle' }); },
    };
    await options.setup(null, agent);
    agents.set(options.sessionId, agent);
    return { agent };
  };
  const handlers = createWorkerHandlers({
    ctx, agents,
    createUserMessage({ content, source }) {
      return { id: `message-${source.requestId}`, role: 'user', content, source };
    },
    listWorkerTools(endpoint) {
      authorities.push(endpoint.authority);
      return [
        { name: 'task_check', description: 'check', inputSchema: { type: 'object' } },
        { name: 'task_report', description: 'report', inputSchema: { type: 'object' } },
      ];
    },
    async callWorkerTool(endpoint, name, args) {
      toolsCalled.push({ authority: endpoint.authority, name, args });
      return { text: 'ok' };
    },
  });
  return { ctx, agents, created, handlers, listeners, toolsCalled, authorities, jobs };
}

function params(f, turn = 1) {
  return {
    taskId: 't1', sessionId: `worker-${f}`, cwd: `/work/${f}`, authority: `token-${f}`,
    policy: 'worker policy', turn, provider: 'deepseek', model: 'deepseek-chat', effort: 'high',
  };
}

test('worker sessions use the worker preset and finish with text and usage', async () => {
  const f = fixture();
  const ensured = await f.handlers['superagent/worker/ensure'](params('t1'));
  assert.equal(ensured.preset, DSH_WORKER_PRESET);
  assert.equal(f.created[0].meta.agentPreset, DSH_WORKER_PRESET);
  assert.deepEqual(f.created[0].meta.cwd, '/work/t1');
  assert.deepEqual(f.agents.get('worker-t1').model.selections[0], {
    provider: 'deepseek', model: 'deepseek-chat', reasoningEffort: 'high',
  });
  const submitted = await f.handlers['superagent/worker/submit']({
    sessionId: 'worker-t1', requestId: 't1:1', prompt: 'do work',
  });
  assert.equal(submitted.reused, false);
  const waiting = f.handlers['superagent/worker/wait']({ sessionId: 'worker-t1' });
  const agent = f.agents.get('worker-t1');
  f.listeners.get('session/event')(agent.session, {
    type: 'assistant/message',
    data: { message: { content: [{ type: 'text', text: 'finished' }] }, usage: { inputTokens: 8, cacheReadTokens: 2, outputTokens: 3 } },
  });
  f.listeners.get('session/event')(agent.session, { type: 'turn/end', data: { reason: { kind: 'end_turn' } } });
  agent.status = 'idle';
  f.listeners.get('agent/status')({ agent, status: 'idle' });
  const result = await waiting;
  assert.equal(result.text, 'finished');
  assert.equal(result.nativeSession, 'worker-t1');
  assert.equal(result.error, null);
  assert.deepEqual(result.usage, { inputTokens: 10, cachedInputTokens: 2, outputTokens: 3, reasoningTokens: 0 });
});

test('a repeated task/turn submission is idempotent across history and inbox', async () => {
  const f = fixture();
  await f.handlers['superagent/worker/ensure'](params('t1'));
  const input = { sessionId: 'worker-t1', requestId: 't1:1', prompt: 'once' };
  await f.handlers['superagent/worker/submit'](input);
  const again = await f.handlers['superagent/worker/submit'](input);
  assert.equal(again.reused, true);
  assert.equal(f.agents.get('worker-t1').session.events.filter(event => event.type === 'user/message').length, 1);
});

test('worker Core tools use only the current turn authority and expire at settlement', async () => {
  const f = fixture();
  await f.handlers['superagent/worker/ensure'](params('t1'));
  const agent = f.agents.get('worker-t1');
  const output = await agent.registered.get('task_report').execute({ state: 'done' }, { signal: AbortSignal.abort() });
  assert.equal(output.text, 'ok');
  assert.deepEqual(f.toolsCalled[0], { authority: 'token-t1', name: 'task_report', args: { state: 'done' } });
  await f.handlers['superagent/worker/cancel']({ sessionId: 'worker-t1' });
  await assert.rejects(
    agent.registered.get('task_report').execute({}, { signal: new AbortController().signal }),
    /turn has ended/,
  );
});

test('two workers have independent turns and cancellation stays session-local', async () => {
  const f = fixture();
  await f.handlers['superagent/worker/ensure'](params('t1'));
  await f.handlers['superagent/worker/ensure']({ ...params('t2'), taskId: 't2' });
  await f.handlers['superagent/worker/submit']({ sessionId: 'worker-t1', requestId: 't1:1', prompt: 'one' });
  await f.handlers['superagent/worker/submit']({ sessionId: 'worker-t2', requestId: 't2:1', prompt: 'two' });
  const second = f.handlers['superagent/worker/wait']({ sessionId: 'worker-t2' });
  await f.handlers['superagent/worker/cancel']({ sessionId: 'worker-t1' });
  const two = f.agents.get('worker-t2');
  assert.equal(two.status, 'running');
  f.listeners.get('session/event')(two.session, { type: 'turn/end', data: { reason: { kind: 'end_turn' } } });
  two.status = 'idle';
  f.listeners.get('agent/status')({ agent: two, status: 'idle' });
  assert.equal((await second).error, null);
});
