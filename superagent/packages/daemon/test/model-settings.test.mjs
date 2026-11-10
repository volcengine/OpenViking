// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { it } from 'node:test';
import { AgentRuntime } from '../src/agent-runtime.mjs';
import { adaptRuntime } from '../src/runtime-adapter.mjs';
import { CodexAgent } from '../src/codex.mjs';
import { TraexAgent } from '../src/traex.mjs';
import { Store } from '../src/store.mjs';
import { executeCommand } from '../src/commands.mjs';

const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };
const config = { workspace: '/tmp/vika-test' };

function fixture(t, { runtimeId = 'codex', claudeFactory } = {}) {
  const store = new Store(':memory:', logger);
  store.initializeCognition(runtimeId, 'old-authority');
  const runtime = new AgentRuntime(config, store, logger, { claudeFactory });
  runtime.runtime = runtimeId;
  const old = backend('codex', { model: 'model-a', effort: 'minimal' });
  runtime.backend = adaptRuntime(runtimeId, old);
  t.after(async () => { await runtime.stop(); store.close(); });
  return { runtime, store, old };
}

function backend(runtime, { model = '', effort = 'minimal' } = {}) {
  return { model, effort, threadId: `${runtime}-session`,
    state: { connected: true, activeTurn: 0, queued: 0 },
    async start() {}, async stop() { this.state.connected = false; },
    async listModels() { return [{ id: 'default', isDefault: true, efforts: ['medium', 'high'] }]; },
    sessionDescriptor: { sessionId: `${runtime}-session`, policyHash: 'policy', sessionReady: true } };
}

it('recovers a retired Claude model with compatible default settings and explains the adjustment', async (t) => {
  const stages = [];
  const { runtime, store, old } = fixture(t, { claudeFactory: (options) => {
    const agent = backend('claude', options);
    if (!options.model) agent.listModels = async () => []; // fallback still retains the known default effort
    stages.push(agent);
    return agent;
  } });
  store.setSetting('claude_model', 'claude-retired');
  store.setSetting('claude_effort', 'minimal');
  const epoch = store.getCognitionState().epoch;
  const result = await executeCommand({ name: 'runtime', arg: 'claude' }, runtime);
  assert.equal(result.ok, true);
  assert.match(result.text, /claude-retired.*恢复默认模型/);
  assert.match(result.text, /minimal.*medium/);
  assert.deepEqual(stages.map((agent) => agent.model), ['claude-retired', '']);
  assert.equal(stages[0].state.connected, false);
  assert.equal(old.state.connected, false);
  assert.equal(runtime.runtime, 'claude');
  assert.equal(store.getSetting('claude_model'), '');
  assert.equal(store.getSetting('claude_effort'), 'medium');
  assert.equal(store.getCognitionState().epoch, epoch);
});

for (const failure of ['empty', 'throws']) it(`allows runtime recovery when model discovery ${failure}`, async (t) => {
  const stages = [];
  const { runtime, store } = fixture(t, { claudeFactory: (options) => {
    const agent = backend('claude', options);
    agent.listModels = async () => {
      if (failure === 'throws') throw new Error('discovery timed out');
      return [];
    };
    stages.push(agent);
    return agent;
  } });
  store.setSetting('claude_model', 'claude-saved');
  store.setSetting('claude_effort', 'minimal');
  const result = await runtime.transition({ kind: 'select-runtime', runtime: 'claude' });
  assert.equal(result.ok, true);
  assert.equal(result.modelFallback, null);
  assert.equal(stages.length, 1);
  assert.equal(runtime.runtime, 'claude');
  assert.equal(store.getSetting('claude_model'), 'claude-saved');
  assert.equal(store.getSetting('claude_effort'), 'minimal');
});

it('retries a model-specific startup failure once with the runtime default', async (t) => {
  const stages = [];
  const { runtime, store } = fixture(t, { claudeFactory: (options) => {
    const agent = backend('claude', options);
    agent.start = async () => {
      if (options.model) throw Object.assign(new Error('CLI startup failed'), { code: 'MODEL_UNAVAILABLE' });
    };
    stages.push(agent);
    return agent;
  } });
  store.setSetting('claude_model', 'claude-retired');
  const result = await runtime.transition({ kind: 'select-runtime', runtime: 'claude' });
  assert.equal(result.ok, true);
  assert.equal(stages.length, 2);
  assert.equal(stages[0].state.connected, false);
  assert.equal(store.getSetting('claude_model'), '');
});

for (const failure of ['authentication', 'default-start', 'commit']) it(`rolls back a runtime fallback on ${failure} failure`, async (t) => {
  const stages = [];
  const { runtime, store } = fixture(t, { claudeFactory: (options) => {
    const agent = backend('claude', options);
    agent.start = async () => {
      if (failure === 'authentication') throw new Error('Authentication failed');
      if (failure === 'default-start' && !options.model) throw new Error('Unknown model: default');
    };
    stages.push(agent);
    return agent;
  } });
  store.setSetting('claude_model', 'claude-retired');
  store.setSetting('claude_effort', 'minimal');
  const before = store.getCognitionState();
  if (failure === 'commit') {
    const set = store.setSetting.bind(store);
    store.setSetting = (key, value) => {
      if (key === 'claude_effort') throw new Error('settings write failed');
      return set(key, value);
    };
  }
  await assert.rejects(runtime.transition({ kind: 'select-runtime', runtime: 'claude' }), /failed|Unknown model/);
  assert.equal(stages.length, failure === 'authentication' ? 1 : 2);
  assert.ok(stages.every((agent) => !agent.state.connected));
  assert.equal(runtime.runtime, 'codex');
  assert.deepEqual(store.getCognitionState(), before);
  assert.equal(store.getSetting('claude_model'), 'claude-retired');
  assert.equal(store.getSetting('claude_effort'), 'minimal');
});

for (const [runtimeId, Agent] of [['codex', CodexAgent], ['traex', TraexAgent]]) {
  it(`${runtimeId} applies compatible model/effort pairs on the next native turn without rotating`, async (t) => {
    const { runtime, store } = fixture(t, { runtimeId });
    const agent = new Agent(config, store, logger, { model: 'model-a', effort: 'minimal' });
    agent.threadId = 'existing-session';
    const models = [
      { id: 'model-a', efforts: ['minimal', 'medium'] },
      { id: 'default-high', efforts: ['medium', 'high'], defaultEffort: 'high' },
      { id: 'keep-high', efforts: ['medium', 'high'], defaultEffort: 'medium' },
      { id: 'medium-fallback', efforts: ['low', 'medium'], defaultEffort: 'invalid' },
      { id: 'first-fallback', efforts: ['low'] },
      { id: 'no-effort', efforts: [] },
    ];
    agent.listModels = async () => models;
    runtime.backend = adaptRuntime(runtimeId, agent);
    const requests = [];
    agent._request = async (method, params) => { requests.push({ method, params }); return {}; };
    const cognition = store.getCognitionState();
    for (const [model, effort] of [['default-high', 'high'], ['keep-high', 'high'],
      ['medium-fallback', 'medium'], ['first-fallback', 'low'], ['no-effort', '']]) {
      const result = await executeCommand({ name: 'model', arg: model }, runtime);
      assert.equal(result.ok, true);
      assert.equal(runtime.effort, effort);
      assert.equal(store.getSetting(`${runtimeId}_model`), model);
      assert.equal(store.getSetting(`${runtimeId}_effort`), effort);
      assert.deepEqual(store.getCognitionState(), cognition);
      agent._startTurn([{ text: 'next message' }]);
      const request = requests.at(-1);
      assert.equal(request.method, 'turn/start');
      assert.equal(request.params.threadId, 'existing-session');
      assert.equal(request.params.model, model);
      assert.equal(request.params.effort, effort || null);
      if (model === 'keep-high') assert.doesNotMatch(result.text, /推理档位已从/);
      if (model === 'default-high') assert.match(result.text, /minimal.*high/);
      if (model === 'no-effort') assert.match(result.text, /取消额外/);
    }
    agent.startingTurn = false;
  });
}

it('does not change settings when target effort capabilities are unknown', async (t) => {
  const { runtime, old, store } = fixture(t);
  store.setRuntimeSettings('codex', { model: 'model-a', effort: 'minimal' });
  old.listModels = async () => ['model-a', 'model-b'].map((id) => ({ id, effortsKnown: false, efforts: [] }));
  const result = await executeCommand({ name: 'model', arg: 'model-b' }, runtime);
  assert.equal(result.ok, false);
  assert.match(result.text, /均未修改.*重试/);
  assert.equal(runtime.model, 'model-a');
  assert.equal(runtime.effort, 'minimal');
  assert.equal(store.getSetting('codex_model'), 'model-a');
  const unchanged = await runtime.transition({ kind: 'select-model', selector: 'model-a' });
  assert.equal(unchanged.unchanged, true);
});

it('rolls back the entire model/effort pair on a persistence failure', async (t) => {
  const { runtime, old, store } = fixture(t);
  store.setRuntimeSettings('codex', { model: 'model-a', effort: 'minimal' });
  old.listModels = async () => [{ id: 'model-b', efforts: ['high'] }];
  const set = store.setSetting.bind(store);
  store.setSetting = (key, value) => {
    if (key === 'codex_effort') throw new Error('settings write failed');
    return set(key, value);
  };
  await assert.rejects(runtime.transition({ kind: 'select-model', selector: 'model-b' }), /write failed/);
  assert.equal(runtime.model, 'model-a');
  assert.equal(runtime.effort, 'minimal');
  assert.equal(store.getSetting('codex_model'), 'model-a');
  assert.equal(store.getSetting('codex_effort'), 'minimal');
});
