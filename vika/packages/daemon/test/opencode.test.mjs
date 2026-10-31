// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { describe, it } from 'node:test';
import { OpencodeAgent } from '../src/opencode.mjs';

const quietLogger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

const baseConfig = {
  opencodeBin: 'opencode',
  opencodeModel: 'anthropic/claude-sonnet-5',
  workspace: '/tmp/vika-workspace',
  projectDir: '/release',
  dataDir: '/tmp/vika-opencode-data',
  databasePath: '/tmp/vika-test.sqlite3',
  profile: 'test-profile',
  connectors: ['lark'],
  actionSocketPath: '/tmp/vika-opencode-data/actions.sock',
  actionMcpPath: '/release/packages/daemon/src/actions/mcp.mjs',
  sendTimeoutMs: 5_000,
  maxReplyChars: 1_000,
  dryRun: true,
};

class FakeChild extends EventEmitter {
  constructor() {
    super();
    this.pid = 987_654_323;
    this.exitCode = null;
    this.writes = [];
    this.stdin = new EventEmitter();
    this.stdin.writable = true;
    this.stdin.write = (value) => { this.writes.push(value); return true; };
    this.stdin.end = () => {};
    this.stdout = new EventEmitter();
    this.stdout.setEncoding = () => {};
    this.stderr = new EventEmitter();
    this.stderr.setEncoding = () => {};
  }

  feed(value) { this.stdout.emit('data', `${JSON.stringify(value)}\n`); }
  finish(code = 0, signal = null) {
    if (this.exitCode !== null) return;
    this.exitCode = code;
    this.emit('exit', code, signal);
  }
  kill(signal = 'SIGTERM') { this.finish(signal === 'SIGKILL' ? 137 : 0, signal); }
}

function parsedWrites(child) {
  return child.writes.map((line) => JSON.parse(line));
}

function makeStore({ globalOverride } = {}) {
  let global = {
    sessionId: null,
    policyHash: null,
    runtime: null,
    epoch: 0,
    syncedThroughSeq: 0,
    sessionReady: false,
    ...globalOverride,
  };
  const settings = {};
  const activities = [];
  return {
    settings,
    activities,
    getCognitionState: () => ({ runtime: 'opencode', epoch: 1, authorityToken: 'oc-token' }),
    getGlobalSession: () => global,
    setGlobalSession(sessionId, policyHash, fields) {
      global = { sessionId, policyHash, ...fields };
    },
    publishOwnedSession({ sessionId, policyHash, runtime, epoch, acceptedThroughSeq, sessionReady }) {
      global = { sessionId, policyHash, runtime, epoch, syncedThroughSeq: acceptedThroughSeq, sessionReady };
      return true;
    },
    advanceOwnedSessionAcceptance({ sessionId, acceptedThroughSeq }) {
      if (global.sessionId !== sessionId) return false;
      global = { ...global, syncedThroughSeq: Math.max(global.syncedThroughSeq, acceptedThroughSeq) };
      return true;
    },
    markOwnedSessionUnresumable({ sessionId }) {
      if (global.sessionId !== sessionId) return false;
      global = { ...global, sessionReady: false };
      return true;
    },
    clearGlobalSession() { global = { sessionId: null, policyHash: null }; },
    getSetting: (key) => settings[key] ?? null,
    setSetting(key, value) { settings[key] = value; },
    recordActivity(activity) { activities.push(activity); },
    prepareOVContext() {},
    cognitionJournalBeforeInbound: () => 0,
    cognitionJournalSeqForInbound: () => 0,
    global: () => global,
  };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

async function waitFor(predicate, timeoutMs = 500) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error('timed out waiting for condition');
}

function newAgent(store = makeStore(), options = {}, config = baseConfig) {
  const spawnCalls = [];
  const children = [];
  const spawn = (bin, args, spawnOptions) => {
    const child = new FakeChild();
    spawnCalls.push({ bin, args, options: spawnOptions, child });
    children.push(child);
    return child;
  };
  // The real child emits an error asynchronously, not during the spawn call.
  const asyncSpawn = (...args) => {
    const child = spawn(...args);
    queueMicrotask(() => child.emit('spawn'));
    return child;
  };
  const agent = new OpencodeAgent(config, store, quietLogger, {
    spawn,
    startTimeoutMs: 1_000,
    startStabilityMs: 1,
    controlTimeoutMs: 500,
    ...options,
  });
  return { agent, spawnCalls, children, store };
}

// Walks initialize -> session/new and lets the stability window pass.
async function driveStart(agent, child, { sessionId = 'ses_start1', model = 'opencode/big-pickle', load = false } = {}) {
  await waitFor(() => requests(child).some((message) => message.method === 'initialize'));
  const init = requests(child).find((message) => message.method === 'initialize');
  child.feed({
    jsonrpc: '2.0', id: init.id, result: {
      protocolVersion: 1,
      agentInfo: { name: 'OpenCode', version: '1.19.5' },
    },
  });
  await flush();
  if (load) {
    const sessionLoad = requests(child).find((message) => message.method === 'session/load');
    assert.ok(sessionLoad);
    child.feed({ jsonrpc: '2.0', id: sessionLoad.id, result: {} });
    await flush();
    // A resumed session reapplies the configured model before going ready.
    const setConfig = requests(child).find((message) => message.method === 'session/set_config_option');
    if (setConfig) {
      child.feed({ jsonrpc: '2.0', id: setConfig.id, result: {} });
      await flush();
    }
    return;
  }
  const sessionNew = requests(child).find((message) => message.method === 'session/new');
  child.feed({
    jsonrpc: '2.0', id: sessionNew.id, result: {
      sessionId,
      configOptions: [
        { id: 'model', type: 'select', currentValue: model, options: [
          { value: 'anthropic/claude-sonnet-5', name: 'Claude Sonnet 5' },
          { value: 'opencode/big-pickle', name: 'Big Pickle' },
        ] },
      ],
    },
  });
  await flush();
  // session/set_config_option must be settled before the agent goes ready, or
  // the first deliver races the settings update into a spurious second prompt.
  const setConfig = requests(child).find((message) => message.method === 'session/set_config_option');
  if (setConfig) {
    child.feed({ jsonrpc: '2.0', id: setConfig.id, result: {} });
    await waitFor(() => !agent.settingsUpdate);
  }
}

async function startSession(agent, children, options = {}) {
  const started = agent.start();
  await waitFor(() => children[0]);
  await driveStart(agent, children[0], options);
  await started.catch(() => {});
  await waitFor(() => !agent.settingsUpdate);
  await flush();
  return children[0];
}

function requests(child) {
  return child ? parsedWrites(child).filter((message) => message.method) : [];
}

describe('OpencodeAgent ACP backend', () => {
  it('spawns opencode acp with a daemon-owned config, MCP adapter and policy file', async () => {
    const { agent, spawnCalls, children } = newAgent();
    await startSession(agent, children);
    const call = spawnCalls[0];
    assert.equal(call.bin, 'opencode');
    assert.deepEqual(call.args, ['acp', '--cwd', '/tmp/vika-workspace']);
    const env = call.options.env;
    assert.equal(env.LARKSUITE_CLI_PROFILE, 'test-profile');
    assert.equal(env.PERSONAL_AGENT_SOCKET, '/tmp/vika-opencode-data/actions.sock');
    assert.equal(env.PERSONAL_AGENT_AUTHORITY, 'oc-token');
    const configPath = env.OPENCODE_CONFIG;
    assert.ok(configPath.startsWith(path.join(os.tmpdir(), 'vika-opencode-')));
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    assert.equal(config.mcp.personal_agent.type, 'local');
    assert.equal(config.mcp.personal_agent.command, process.execPath);
    assert.deepEqual(config.mcp.personal_agent.args, [
      '/release/packages/daemon/src/actions/mcp.mjs',
      '--socket', '/tmp/vika-opencode-data/actions.sock',
      '--authority', 'oc-token',
    ]);
    const policy = fs.readFileSync(config.instructions[0], 'utf8');
    assert.match(policy, /Operating policy/);
    await agent.stop();
  });

  it('rejects an ACP handshake that is not OpenCode', async () => {
    const { agent, children } = newAgent(undefined, undefined, undefined);
    const started = agent.start();
    await flush();
    const init = requests(children[0]).find((message) => message.method === 'initialize');
    children[0].feed({ jsonrpc: '2.0', id: init.id, result: { agentInfo: { name: 'SomeoneElse' } } });
    await assert.rejects(started, /unrecognised ACP agent/);
  });

  it('starts one prompt when idle and queues mid-turn messages for the next prompt', async () => {
    const { agent, children, store } = newAgent();
    const child = await startSession(agent, children);
    assert.equal(agent.runtime, 'opencode');
    assert.equal(store.getGlobalSession().runtime, 'opencode');

    agent.deliver('first task', 7);
    await waitFor(() => agent.turnActive);
    let prompts = requests(child).filter((message) => message.method === 'session/prompt');
    assert.equal(prompts.length, 1);
    assert.equal(prompts[0].params.sessionId, 'ses_start1');
    assert.deepEqual(prompts[0].params.prompt, [{ type: 'text', text: 'first task' }]);
    assert.equal(agent.turnActive, true);

    agent.deliver('second task', 8);
    // Mid-turn inputs must land in the runtime queue and never become a wire
    // message; wait for that state instead of racing the synchronous write.
    await waitFor(() => agent.queue.length === 1 && agent.turnActive);
    const promptsMidTurn = requests(child).filter((message) => message.method === 'session/prompt');
    assert.equal(promptsMidTurn.length, 1);
    assert.equal(agent.state.queued, 1);

    child.feed({ jsonrpc: '2.0', id: prompts[0].id, result: { stopReason: 'end_turn' } });
    await waitFor(() => !agent.turnActive);
    await flush();
    prompts = requests(child).filter((message) => message.method === 'session/prompt');
    assert.equal(prompts.length, 2);
    assert.deepEqual(prompts[1].params.prompt, [{ type: 'text', text: 'second task' }]);
    child.feed({ jsonrpc: '2.0', id: prompts[1].id, result: { stopReason: 'end_turn' } });
    await waitFor(() => !agent.turnActive);
    await agent.stop();
  });

  it('records agent note, tool activity and denies permission requests', async () => {
    const { agent, children, store } = newAgent();
    const child = await startSession(agent, children);
    agent.deliver('do work', 7);
    await flush();
    const prompt = requests(child).find((message) => message.method === 'session/prompt');
    child.feed({
      jsonrpc: '2.0', method: 'session/request_permission', id: 900,
      params: {
        sessionId: 'ses_start1',
        options: [
          { optionId: 'opt-allow', kind: 'allow_once' },
          { optionId: 'opt-reject', kind: 'reject_once' },
        ],
      },
    });
    child.feed({
      method: 'session/update',
      params: {
        sessionId: 'ses_start1',
        update: {
          sessionUpdate: 'tool_call',
          toolCallId: 'tc-1',
          name: 'shell',
          title: 'shell',
          rawInput: { command: 'ls' },
        },
      },
    });
    child.feed({
      method: 'session/update',
      params: {
        sessionId: 'ses_start1',
        update: {
          sessionUpdate: 'tool_call_update',
          toolCallId: 'tc-1',
          status: 'completed',
          content: [{ type: 'text', text: 'done' }],
        },
      },
    });
    child.feed({
      method: 'session/update',
      params: {
        sessionId: 'ses_start1',
        update: {
          sessionUpdate: 'agent_message_chunk',
          content: { type: 'text', text: 'telemetry note' },
        },
      },
    });
    child.feed({ jsonrpc: '2.0', id: prompt.id, result: { stopReason: 'end_turn' } });
    await waitFor(() => !agent.turnActive);
    const permissionAnswer = parsedWrites(child).find((message) => message.id === 900);
    assert.deepEqual(permissionAnswer.result, { outcome: { outcome: 'selected', optionId: 'opt-reject' } });
    const kinds = store.activities.map((activity) => activity.kind);
    assert.ok(kinds.includes('agent.interaction_declined'));
    assert.ok(kinds.includes('agent.tool_called'));
    assert.ok(kinds.includes('agent.tool_completed'));
    assert.ok(kinds.includes('agent.note'));
    await agent.stop();
  });

  it('applies a changed model before the next turn without replacing the session', async () => {
    const { agent, children } = newAgent();
    const child = await startSession(agent, children, { model: 'opencode/big-pickle' });
    const sessionId = agent.threadId;
    // The initial startup already applied the configured model.
    const startupUpdates = requests(child).filter((message) => message.method === 'session/set_config_option');
    agent.setModel('anthropic/claude-sonnet-5');
    await waitFor(() => requests(child).filter((message) => message.method === 'session/set_config_option').length
      === startupUpdates.length + 1);
    const setConfig = requests(child).filter((message) => message.method === 'session/set_config_option').at(-1);
    assert.equal(setConfig.params.option.id, 'model');
    assert.equal(setConfig.params.option.value, 'anthropic/claude-sonnet-5');
    child.feed({ jsonrpc: '2.0', id: setConfig.id, result: {} });
    await waitFor(() => agent.appliedModel === 'anthropic/claude-sonnet-5');
    agent.deliver('next turn', 7);
    await waitFor(() => requests(child).some((message) => message.method === 'session/prompt'));
    assert.equal(agent.threadId, sessionId);
    assert.equal(children.length, 1);
    await agent.stop();
  });

  it('surfaces session/new model options for /model discovery', async () => {
    const { agent, children } = newAgent(makeStore(), {}, { ...baseConfig, opencodeModel: '' });
    await startSession(agent, children, { model: 'opencode/big-pickle' });
    const models = await agent.listModels();
    assert.deepEqual(models.map((entry) => entry.id), [
      'anthropic/claude-sonnet-5', 'opencode/big-pickle',
    ]);
    assert.equal(models.find((entry) => entry.id === 'opencode/big-pickle').isDefault, true);
    await agent.stop();
  });

  it('cancels and abandons the active turn, and prepares a fresh session on rotation', async () => {
    const { agent, children } = newAgent();
    const child = await startSession(agent, children);
    let abandoned = null;
    agent.onTurnAbandoned = (event) => { abandoned = event; };
    agent.deliver('long task', 7, 'receipt-7');
    await waitFor(() => agent.turnActive);
    await agent.interruptTurn();
    const cancel = requests(child).find((message) => message.method === 'session/cancel');
    assert.ok(cancel, 'session/cancel is notified for the live turn');
    assert.equal(cancel.params.sessionId, 'ses_start1');
    assert.deepEqual(abandoned.inboundSeqs, [7]);
    assert.deepEqual(abandoned.receiptIds, ['receipt-7']);

    // prepareSessionRotation replaces the idle process immediately; answer the
    // new child's handshake while it starts.
    const rotation = agent.prepareSessionRotation();
    await waitFor(() => children[1]);
    await driveStart(agent, children[1], { sessionId: 'ses_prepared', model: 'anthropic/claude-sonnet-5' });
    const prepared = await rotation;
    await waitFor(() => agent.threadId === 'ses_prepared');
    assert.equal(children.length, 2);
    assert.notEqual(prepared.id, 'ses_start1');
    assert.equal(agent.threadId, prepared.id);
    assert.equal(child.exitCode !== null, true, 'old process is terminated during prepare');
    agent.commitSessionRotation(prepared);
    assert.equal(agent.threadId, prepared.id);
    await agent.stop();
  });

  it('loads a stored session through session/load on reconnect', async () => {
    const store = makeStore({
      globalOverride: {
        sessionId: 'ses_saved',
        policyHash: new OpencodeAgent(baseConfig, makeStore(), quietLogger).policyHash,
        runtime: 'opencode',
        epoch: 1,
        sessionReady: true,
        syncedThroughSeq: 3,
      },
    });
    const { agent, children } = newAgent(store);
    await startSession(agent, children, { load: true, sessionId: 'ses_saved' });
    const load = requests(children[0]).find((message) => message.method === 'session/load');
    assert.equal(load.params.sessionId, 'ses_saved');
    assert.equal(agent.threadId, 'ses_saved');
    await agent.stop();
  });
});
