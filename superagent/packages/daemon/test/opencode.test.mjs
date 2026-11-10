// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { describe, it } from 'node:test';
import { OpencodeAgent } from '../src/opencode.mjs';
import { RuntimeAdapter } from '../src/runtime-adapter.mjs';

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
  larkCliBin: '/fake/lark-cli',
  slackCliBin: '/fake-slack/slack',
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
    // Fake processes must never reach taskkill/process.kill with synthetic PIDs.
    // Real Windows tree termination is covered by windows-process.test.mjs.
    terminateProcessTree: (child, signal) => child.kill(signal),
    startTimeoutMs: 1_000,
    startStabilityMs: 1,
    controlTimeoutMs: 500,
    ...options,
  });
  return { agent, spawnCalls, children, store };
}

// Walks initialize -> session/new and lets the stability window pass.
async function driveStart(agent, child, {
  sessionId = 'ses_start1', model = 'opencode/big-pickle', load = false, loadResult = {}, loadFrames = [], loadError = null,
} = {}) {
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
    for (const update of loadFrames) {
      child.feed({ jsonrpc: '2.0', method: 'session/update', params: { sessionId: sessionLoad.params.sessionId, update } });
    }
    child.feed({ jsonrpc: '2.0', id: sessionLoad.id, ...(loadError ? { error: loadError } : { result: loadResult }) });
    await flush();
    if (!loadError) {
      // A resumed session reapplies the configured model before going ready.
      const setConfig = requests(child).find((message) => message.method === 'session/set_config_option');
      if (setConfig) {
        child.feed({ jsonrpc: '2.0', id: setConfig.id, result: {} });
        await flush();
      }
      return;
    }
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

function prompts(child) {
  return requests(child).filter((message) => message.method === 'session/prompt');
}

function storedSession(overrides = {}) {
  return makeStore({
    globalOverride: {
      sessionId: 'ses_saved',
      policyHash: new OpencodeAgent(baseConfig, makeStore(), quietLogger).policyHash,
      runtime: 'opencode',
      epoch: 1,
      sessionReady: true,
      syncedThroughSeq: 3,
      ...overrides,
    },
  });
}

function withHistory(store, { journalSeq = 5, inboundSeq = 6 } = {}) {
  store.getOVContext = () => ({ sessionId: 'ov', messages: [{ parts: [{ type: 'text', text: 'past' }] }] });
  store.cognitionJournalBeforeInbound = () => journalSeq;
  store.cognitionJournalSeqForInbound = () => inboundSeq;
  return store;
}

const replayedTurn = [
  { sessionUpdate: 'user_message_chunk', content: { type: 'text', text: 'earlier question' } },
  { sessionUpdate: 'tool_call', toolCallId: 'old-1', title: 'ls', status: 'completed', rawInput: { command: 'ls' } },
  { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'earlier answer' } },
];

describe('OpencodeAgent ACP backend', () => {
  it('spawns opencode acp with a daemon-owned config, MCP adapter and policy file', async () => {
    const { agent, spawnCalls, children } = newAgent();
    await startSession(agent, children);
    const call = spawnCalls[0];
    assert.equal(call.bin, 'opencode');
    assert.deepEqual(call.args, ['acp', '--cwd', '/tmp/vika-workspace']);
    const env = call.options.env;
    assert.equal(env.LARKSUITE_CLI_PROFILE, 'test-profile');
    assert.deepEqual(env.PATH.split(path.delimiter).slice(0, 2), ['/fake', '/fake-slack']);
    assert.equal(env.PERSONAL_AGENT_SOCKET, '/tmp/vika-opencode-data/actions.sock');
    assert.equal(env.PERSONAL_AGENT_AUTHORITY, 'oc-token');
    const configPath = env.OPENCODE_CONFIG;
    assert.ok(configPath.startsWith(path.join(os.tmpdir(), 'vika-opencode-')));
    // The global config folder is a Vika-owned copy without OpenViking.
    assert.equal(env.XDG_CONFIG_HOME, path.join(path.dirname(configPath), 'config-home'));
    assert.ok(fs.existsSync(path.join(env.XDG_CONFIG_HOME, 'opencode', 'opencode.json')));
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    assert.equal(config.mcp.personal_agent.type, 'local');
    assert.deepEqual(config.mcp.personal_agent.command, [
      process.execPath,
      '/release/packages/daemon/src/actions/mcp.mjs',
      '--socket', '/tmp/vika-opencode-data/actions.sock',
      '--authority', 'oc-token',
    ]);
    assert.equal(config.mcp.personal_agent.args, undefined);
    assert.equal(config.mcp.personal_agent.enabled, true);
    assert.equal(config.mcp.personal_agent.timeout, 10_000);
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

    // The turn end drains the queue synchronously, so the next prompt starts
    // at once: wait for it rather than for an idle gap that never shows.
    child.feed({ jsonrpc: '2.0', id: prompts[0].id, result: { stopReason: 'end_turn' } });
    await waitFor(() => requests(child).filter((message) => message.method === 'session/prompt').length === 2);
    assert.equal(agent.turnActive, true);
    prompts = requests(child).filter((message) => message.method === 'session/prompt');
    assert.equal(prompts.length, 2);
    assert.deepEqual(prompts[1].params.prompt, [{ type: 'text', text: 'second task' }]);
    child.feed({ jsonrpc: '2.0', id: prompts[1].id, result: { stopReason: 'end_turn' } });
    await waitFor(() => !agent.turnActive);
    const completed = store.activities.filter((activity) => activity.kind === 'agent.turn_completed');
    assert.deepEqual(completed.map((activity) => activity.status), ['completed', 'completed']);
    await agent.stop();
  });

  it('cancels a looping prompt in place and drains the queue into the same session behind the notice', async () => {
    const failed = [];
    const { agent, children } = newAgent(makeStore(), { onTurnFailed: (info) => failed.push(info) });
    const child = await startSession(agent, children);
    const adapter = new RuntimeAdapter('opencode', agent);
    assert.equal(adapter.cancelTurn(), false, 'nothing to cancel while idle');
    agent.deliver('loop forever', 7);
    await waitFor(() => agent.turnActive);
    agent.deliver('waiting meanwhile', 8);
    await waitFor(() => agent.queue.length === 1);

    assert.equal(adapter.cancelTurn(), true);
    assert.deepEqual(requests(child).at(-1), { jsonrpc: '2.0', method: 'session/cancel', params: { sessionId: 'ses_start1' } });
    assert.equal(agent.turnActive, true, 'live until OpenCode ends the prompt');
    adapter.setTurnNotice('[daemon notice] stopped');
    const [prompt] = requests(child).filter((message) => message.method === 'session/prompt');
    child.feed({ jsonrpc: '2.0', id: prompt.id, result: { stopReason: 'cancelled' } });
    await waitFor(() => requests(child).filter((message) => message.method === 'session/prompt').length === 2);

    const next = requests(child).filter((message) => message.method === 'session/prompt')[1];
    assert.equal(next.params.sessionId, 'ses_start1');
    assert.deepEqual(next.params.prompt, [{ type: 'text', text: '[daemon notice] stopped\nwaiting meanwhile' }]);
    assert.equal(children.length, 1, 'same process');
    // AgentRuntime turns this report of the stopped turn into a normal end.
    assert.deepEqual(failed.map((info) => [info.inboundSeqs, info.error]), [[[7], 'opencode turn ended: cancelled']]);
    assert.equal(agent.turnNotice, null, 'shown once');
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
          content: { type: 'text', text: 'telemetry ' },
        },
      },
    });
    child.feed({
      method: 'session/update',
      params: {
        sessionId: 'ses_start1',
        update: {
          sessionUpdate: 'agent_message_chunk',
          content: { type: 'text', text: 'note' },
        },
      },
    });
    child.feed({ jsonrpc: '2.0', id: prompt.id, result: { stopReason: 'end_turn' } });
    await waitFor(() => !agent.turnActive);
    const permissionAnswer = parsedWrites(child).find((message) => message.id === 900);
    assert.deepEqual(permissionAnswer.result, { outcome: { outcome: 'selected', optionId: 'opt-reject' } });
    const kinds = store.activities.map((activity) => activity.kind);
    assert.ok(kinds.includes('agent.interaction_declined'));
    assert.equal(kinds.filter((kind) => kind === 'agent.tool_called').length, 1);
    assert.ok(kinds.includes('agent.tool_completed'));
    // Streamed chunks land as one row per message, not one per token.
    const notes = store.activities.filter((activity) => activity.kind === 'agent.note');
    assert.deepEqual(notes.map((note) => note.summary), ['telemetry note']);
    await agent.stop();
  });

  it('records a tool call once, with the arguments OpenCode sends after announcing it', async () => {
    const { agent, children, store } = newAgent();
    const child = await startSession(agent, children);
    agent.deliver('reply', 7);
    await waitFor(() => agent.turnActive);
    const update = (fields) => child.feed({
      method: 'session/update',
      params: { sessionId: 'ses_start1', update: { toolCallId: 'personal_agent_send_message_0', ...fields } },
    });
    // The frame sequence opencode 1.19 emits for an MCP tool call.
    update({ sessionUpdate: 'tool_call', status: 'pending', title: 'personal_agent_send_message', kind: 'other', rawInput: {} });
    update({
      sessionUpdate: 'tool_call_update', status: 'in_progress', title: 'personal_agent_send_message', kind: 'other',
      rawInput: { chat_id: 'oc-1', markdown: 'hi' },
    });
    update({ sessionUpdate: 'tool_call_update', status: 'completed', content: [], rawOutput: {} });
    const prompt = requests(child).find((message) => message.method === 'session/prompt');
    child.feed({ jsonrpc: '2.0', id: prompt.id, result: { stopReason: 'end_turn' } });
    await waitFor(() => !agent.turnActive);
    const called = store.activities.filter((activity) => activity.kind === 'agent.tool_called');
    assert.equal(called.length, 1);
    assert.equal(called[0].metadata.toolName, 'personal_agent_send_message');
    assert.match(JSON.stringify(called[0].metadata.input), /oc-1/);
    const completed = store.activities.filter((activity) => activity.kind === 'agent.tool_completed');
    assert.deepEqual(completed.map((activity) => activity.status), ['COMPLETED']);
    await agent.stop();
  });

  it('fails a turn OpenCode stops for any reason other than end_turn', async () => {
    const { agent, children, store } = newAgent();
    const child = await startSession(agent, children);
    agent.deliver('long answer', 7);
    await waitFor(() => agent.turnActive);
    const prompt = requests(child).find((message) => message.method === 'session/prompt');
    child.feed({ jsonrpc: '2.0', id: prompt.id, result: { stopReason: 'max_tokens' } });
    await waitFor(() => !agent.turnActive);
    const completed = store.activities.filter((activity) => activity.kind === 'agent.turn_completed');
    assert.deepEqual(completed.map((activity) => [activity.status, activity.summary]), [
      ['failed', 'opencode turn ended: max_tokens'],
    ]);
    await agent.stop();
  });

  it('keeps a long-running prompt open until OpenCode ends the turn', async () => {
    const { agent, children, store } = newAgent();
    const child = await startSession(agent, children);
    agent.deliver('slow task', 7);
    await waitFor(() => agent.turnActive);
    // A turn has no deadline; real turns take seconds to minutes.
    await new Promise((resolve) => setTimeout(resolve, 30));
    assert.equal(agent.turnActive, true);
    assert.equal(store.activities.some((activity) => activity.kind === 'agent.turn_completed'), false);
    const prompt = requests(child).find((message) => message.method === 'session/prompt');
    child.feed({ jsonrpc: '2.0', id: prompt.id, result: { stopReason: 'end_turn' } });
    await waitFor(() => !agent.turnActive);
    const completed = store.activities.filter((activity) => activity.kind === 'agent.turn_completed');
    assert.deepEqual(completed.map((activity) => activity.status), ['completed']);
    await agent.stop();
  });

  it('applies a changed model before the next turn without replacing the session', async () => {
    const { agent, children, store } = newAgent();
    const child = await startSession(agent, children, { model: 'opencode/big-pickle' });
    const sessionId = agent.threadId;
    // The initial startup already applied the configured model; selecting it
    // again sends nothing.
    agent.setModel('anthropic/claude-sonnet-5');
    agent.deliver('same model', 6);
    await waitFor(() => requests(child).some((message) => message.method === 'session/prompt'));
    assert.equal(requests(child).filter((message) => message.method === 'session/set_config_option').length, 1);
    const first = requests(child).find((message) => message.method === 'session/prompt');
    child.feed({ jsonrpc: '2.0', id: first.id, result: { stopReason: 'end_turn' } });
    await waitFor(() => !agent.turnActive);
    const startupUpdates = requests(child).filter((message) => message.method === 'session/set_config_option');
    agent.setModel('opencode/big-pickle');
    assert.equal(store.settings.opencode_model, 'opencode/big-pickle');
    agent.deliver('next turn', 7);
    await waitFor(() => requests(child).filter((message) => message.method === 'session/set_config_option').length
      === startupUpdates.length + 1);
    const setConfig = requests(child).filter((message) => message.method === 'session/set_config_option').at(-1);
    assert.deepEqual(setConfig.params, {
      sessionId: 'ses_start1', configId: 'model', value: 'opencode/big-pickle',
    });
    child.feed({ jsonrpc: '2.0', id: setConfig.id, result: {} });
    await waitFor(() => requests(child).filter((message) => message.method === 'session/prompt').length === 2);
    assert.equal(agent.appliedModel, 'opencode/big-pickle');
    assert.equal(agent.resolvedModel, 'opencode/big-pickle');
    assert.equal(agent.threadId, sessionId);
    assert.equal(children.length, 1);
    await agent.stop();
  });

  it('applies a /model choice published through the runtime adapter before the next prompt', async () => {
    const { agent, children } = newAgent();
    const child = await startSession(agent, children, { model: 'anthropic/claude-sonnet-5' });
    const adapter = new RuntimeAdapter('opencode', agent);
    const before = requests(child).filter((message) => message.method === 'session/set_config_option').length;
    // AgentRuntime's /model path: persist, then publish the plain field.
    adapter.applySettings({ model: 'opencode/big-pickle' });
    agent.deliver('next turn', 7);
    await waitFor(() => requests(child).filter((message) => message.method === 'session/set_config_option').length
      === before + 1);
    const setConfig = requests(child).filter((message) => message.method === 'session/set_config_option').at(-1);
    assert.deepEqual(setConfig.params, { sessionId: 'ses_start1', configId: 'model', value: 'opencode/big-pickle' });
    // The prompt waits for the session to acknowledge the model.
    assert.equal(requests(child).some((message) => message.method === 'session/prompt'), false);
    child.feed({ jsonrpc: '2.0', id: setConfig.id, result: {} });
    await waitFor(() => requests(child).some((message) => message.method === 'session/prompt'));
    assert.equal(agent.appliedModel, 'opencode/big-pickle');
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
    await startSession(agent, children, {
      load: true,
      sessionId: 'ses_saved',
      loadResult: {
        configOptions: [{ id: 'model', type: 'select', currentValue: 'anthropic/claude-sonnet-5', options: [
          { value: 'anthropic/claude-sonnet-5', name: 'Claude Sonnet 5' },
          { value: 'opencode/big-pickle', name: 'Big Pickle' },
        ] }],
      },
    });
    const load = requests(children[0]).find((message) => message.method === 'session/load');
    assert.equal(load.params.sessionId, 'ses_saved');
    assert.equal(agent.threadId, 'ses_saved');
    // A resumed session still offers its models to /model.
    assert.deepEqual((await agent.listModels()).map((entry) => entry.id), [
      'anthropic/claude-sonnet-5', 'opencode/big-pickle',
    ]);
    await agent.stop();
  });

  it('leaves the session to the next process when one dies during session/load', async (t) => {
    const { agent, children } = newAgent(makeStore(), { reconnectBaseMs: 1, reconnectCapMs: 1 });
    t.after(() => agent.stop());
    await startSession(agent, children);
    children[0].finish(1);
    await waitFor(() => children[1] && requests(children[1]).some((message) => message.method === 'initialize'));
    children[1].feed({ jsonrpc: '2.0', id: requests(children[1])[0].id, result: { agentInfo: { name: 'OpenCode' } } });
    await waitFor(() => requests(children[1]).some((message) => message.method === 'session/load'));
    children[1].finish(1);
    assert.equal([...agent.pendingRpc.values()].some((pending) => pending.child === children[1]), false,
      'its load is rejected with the process, not left to time out');
    await waitFor(() => children[2]);
    await driveStart(agent, children[2], { load: true });
    await waitFor(() => agent.connected);
    assert.equal(agent.sessionId, 'ses_start1');
    assert.equal(agent.sessionInitialized, true);
    agent.deliver('after', 9);
    await waitFor(() => prompts(children[2]).length === 1);
    assert.equal(prompts(children[2])[0].params.sessionId, 'ses_start1');
  });

  it('keeps the catch-up and the watermark when OpenCode refuses a prompt', async () => {
    const store = withHistory(makeStore());
    const failed = [];
    const { agent, children } = newAgent(store, { onTurnFailed: (info) => failed.push(info) });
    const child = await startSession(agent, children);
    agent.deliver('one', 6);
    await waitFor(() => prompts(child).length === 1);
    assert.match(prompts(child)[0].params.prompt[0].text, /daemon OpenViking context/);
    // Arrives on its own after session/new: not evidence the turn ran.
    child.feed({ method: 'session/update', params: { sessionId: 'ses_start1', update: { sessionUpdate: 'available_commands_update', availableCommands: [] } } });
    child.feed({ jsonrpc: '2.0', id: prompts(child)[0].id, error: { code: -32602, message: 'Invalid params: session not found: ses_start1', data: { sessionId: 'ses_start1' } } });
    await waitFor(() => failed.length === 1);
    assert.equal(agent.needsHistoryPrefix, true);
    assert.equal(store.global().syncedThroughSeq, 0, 'a refused prompt was not accepted');
    agent.deliver('two', 7);
    await waitFor(() => prompts(child).length === 2);
    assert.match(prompts(child)[1].params.prompt[0].text, /daemon OpenViking context[\s\S]*two$/);
    child.feed({ jsonrpc: '2.0', id: prompts(child)[1].id, result: { stopReason: 'end_turn' } });
    await waitFor(() => !agent.turnActive);
    assert.equal(store.global().syncedThroughSeq, 6, 'an accepted prompt still advances it');
    await agent.stop();
  });

  it('publishes the fresh session a mid-turn crash reconnects to', async (t) => {
    const store = makeStore();
    store.cognitionJournalSeqForInbound = () => 6;
    const { agent, children } = newAgent(store, { reconnectBaseMs: 1, reconnectCapMs: 1 });
    t.after(() => agent.stop());
    await startSession(agent, children);
    agent.deliver('work', 6);
    await waitFor(() => prompts(children[0]).length === 1);
    children[0].finish(1);
    await waitFor(() => children[1]);
    await driveStart(agent, children[1], { sessionId: 'ses_second' });
    await waitFor(() => agent.connected);
    assert.equal(agent.sessionId, 'ses_second');
    assert.equal(store.global().sessionId, 'ses_second');
    assert.equal(store.global().sessionReady, true);
    agent.deliver('again', 7);
    await waitFor(() => prompts(children[1]).length === 1);
    children[1].feed({ jsonrpc: '2.0', id: prompts(children[1])[0].id, result: { stopReason: 'end_turn' } });
    await waitFor(() => !agent.turnActive);
    assert.equal(store.global().syncedThroughSeq, 6, 'the new session owns the watermark');
  });

  it('does not publish a prepared session when its process crashes before the commit', async (t) => {
    const store = makeStore();
    const { agent, children } = newAgent(store, { reconnectBaseMs: 1, reconnectCapMs: 1 });
    t.after(() => agent.stop());
    await startSession(agent, children);
    const preparing = agent.prepareSessionRotation();
    await waitFor(() => children[1]);
    await driveStart(agent, children[1], { sessionId: 'ses_prepared' });
    await preparing;
    children[1].finish(1);
    await waitFor(() => children[2]);
    await driveStart(agent, children[2], { load: true });
    await waitFor(() => agent.connected);
    assert.equal(agent.sessionId, 'ses_prepared');
    assert.equal(store.global().sessionId, 'ses_start1');
  });

  it('reopens the previous session when a rotation is cancelled', async (t) => {
    const store = makeStore();
    const { agent, children } = newAgent(store);
    t.after(() => agent.stop());
    await startSession(agent, children);
    const preparing = agent.prepareSessionRotation();
    await waitFor(() => children[1]);
    await driveStart(agent, children[1], { sessionId: 'ses_prepared' });
    await preparing;
    const cancelling = agent.cancelSessionRotation();
    await waitFor(() => children[2]);
    await driveStart(agent, children[2], { load: true });
    await cancelling;
    const load = requests(children[2]).find((message) => message.method === 'session/load');
    assert.equal(load.params.sessionId, 'ses_start1');
    assert.equal(requests(children[2]).some((message) => message.method === 'session/new'), false);
    assert.equal(agent.sessionId, 'ses_start1');
    assert.equal(store.global().sessionId, 'ses_start1');
  });

  it('publishes the replacement when a cancelled rotation cannot reopen the previous session', async (t) => {
    const store = storedSession();
    const { agent, children } = newAgent(store);
    t.after(() => agent.stop());
    await startSession(agent, children, { load: true });
    const preparing = agent.prepareSessionRotation();
    await waitFor(() => children[1]);
    await driveStart(agent, children[1], { sessionId: 'ses_prepared' });
    await preparing;
    const cancelling = agent.cancelSessionRotation();
    await waitFor(() => children[2]);
    await driveStart(agent, children[2], {
      load: true, loadError: { code: -32000, message: 'session not found' }, sessionId: 'ses_fallback',
    });
    await cancelling;
    assert.equal(agent.sessionId, 'ses_fallback');
    assert.equal(store.global().sessionId, 'ses_fallback');
  });

  it('gives each process its own activity keys for the same resumed session', async () => {
    const store = storedSession();
    const runOnce = async () => {
      const { agent, children } = newAgent(store);
      const child = await startSession(agent, children, { load: true });
      agent.deliver('turn', 8);
      await waitFor(() => prompts(child).length === 1);
      assert.equal(prompts(child)[0].params.sessionId, 'ses_saved');
      child.feed({ jsonrpc: '2.0', id: prompts(child)[0].id, result: { stopReason: 'end_turn' } });
      await waitFor(() => !agent.turnActive);
      await agent.stop();
    };
    await runOnce();
    await runOnce();
    const keys = store.activities.map((activity) => activity.eventKey);
    assert.equal(keys.length, 4);
    assert.equal(new Set(keys).size, 4);
    assert.ok(keys.every((key) => key.startsWith('opencode:ses_saved:')));
  });

  it('keeps replayed history out of notes and activity', async () => {
    const store = storedSession();
    const { agent, children } = newAgent(store);
    const child = await startSession(agent, children, { load: true, loadFrames: replayedTurn });
    assert.equal(agent.threadId, 'ses_saved');
    assert.equal(agent.noteText, '');
    assert.equal(agent.toolStarts.size, 0);
    assert.equal(store.activities.length, 0);

    agent.deliver('continue', 8);
    await waitFor(() => prompts(child).length === 1);
    child.feed({
      method: 'session/update',
      params: { sessionId: 'ses_saved', update: { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'fresh note' } } },
    });
    child.feed({ jsonrpc: '2.0', id: prompts(child)[0].id, result: { stopReason: 'end_turn' } });
    await waitFor(() => !agent.turnActive);
    assert.deepEqual(store.activities.filter((activity) => activity.kind === 'agent.note').map((note) => note.summary), ['fresh note']);
    await agent.stop();
  });

  it('ends a tool on status failed, names it by update.name and falls back to rawOutput', async () => {
    const { agent, children, store } = newAgent();
    const child = await startSession(agent, children);
    agent.deliver('work', 7);
    await waitFor(() => prompts(child).length === 1);
    const update = (fields) => child.feed({ method: 'session/update', params: { sessionId: 'ses_start1', update: fields } });
    update({ sessionUpdate: 'tool_call', toolCallId: 't1', name: 'bash', title: 'echo ok', status: 'in_progress', rawInput: { command: 'echo ok' } });
    update({ sessionUpdate: 'tool_call_update', toolCallId: 't1', status: 'completed', content: [], rawOutput: { output: 'from rawOutput' } });
    update({ sessionUpdate: 'tool_call', toolCallId: 't2', title: 'personal_agent_send_message', status: 'in_progress', rawInput: { chat_id: 'x' } });
    update({ sessionUpdate: 'tool_call_update', toolCallId: 't2', status: 'failed', content: [], rawOutput: { error: 'denied' } });
    assert.equal(agent.toolStarts.size, 0, 'failed ends the call without waiting for the turn');
    update({ sessionUpdate: 'tool_call', toolCallId: 't3', title: 'read', status: 'in_progress', rawInput: { path: 'a' } });
    update({ sessionUpdate: 'tool_call_update', toolCallId: 't3', status: 'completed', content: [{ type: 'text', text: 'from content' }], rawOutput: { output: 'unused' } });
    child.feed({ jsonrpc: '2.0', id: prompts(child)[0].id, result: { stopReason: 'end_turn' } });
    await waitFor(() => !agent.turnActive);
    const called = store.activities.filter((activity) => activity.kind === 'agent.tool_called');
    assert.deepEqual(called.map((activity) => [activity.metadata.toolName, activity.summary]), [
      ['bash', 'echo ok'],
      ['personal_agent_send_message', 'personal_agent_send_message'],
      ['read', 'read'],
    ]);
    const completed = store.activities.filter((activity) => activity.kind === 'agent.tool_completed');
    assert.deepEqual(completed.map((activity) => [activity.status, activity.metadata.runtime]), [
      ['COMPLETED', 'opencode'], ['FAILED', 'opencode'], ['COMPLETED', 'opencode'],
    ]);
    assert.match(JSON.stringify(completed[0].metadata.output), /from rawOutput/);
    assert.match(JSON.stringify(completed[1].metadata.error), /denied/);
    assert.match(JSON.stringify(completed[2].metadata.output), /from content/);
    assert.doesNotMatch(JSON.stringify(completed[2].metadata.output), /unused/);
    await agent.stop();
  });

  describe('journal catch-up across failures and restarts', () => {
    const catchup = /daemon OpenViking context/;
    const update = (child, fields) => child.feed({ method: 'session/update', params: { sessionId: 'ses_start1', update: fields } });
    const firstPromptAfterRestart = async (store, expectedSession) => {
      const restart = newAgent(store);
      const child = await startSession(restart.agent, restart.children, { load: true });
      assert.equal(requests(child).find((message) => message.method === 'session/load').params.sessionId, expectedSession);
      restart.agent.deliver('next', 9);
      await waitFor(() => prompts(child).length === 1);
      const text = prompts(child)[0].params.prompt[0].text;
      await restart.agent.stop();
      return text;
    };

    it('owes the catch-up after a restart when a mid-turn crash reconnected to a session that never got a prompt', async () => {
      const store = withHistory(makeStore());
      const { agent, children } = newAgent(store, { reconnectBaseMs: 1, reconnectCapMs: 1 });
      await startSession(agent, children);
      agent.deliver('work', 6);
      await waitFor(() => prompts(children[0]).length === 1);
      children[0].finish(1);
      await waitFor(() => children[1]);
      await driveStart(agent, children[1], { sessionId: 'ses_second' });
      await waitFor(() => agent.connected);
      assert.equal(store.global().sessionId, 'ses_second');
      await agent.stop();
      assert.match(await firstPromptAfterRestart(store, 'ses_second'), catchup);
    });

    it('owes the catch-up after a restart when an idle reconnect fell back to a new session', async () => {
      const store = withHistory(makeStore());
      const { agent, children } = newAgent(store, { reconnectBaseMs: 1, reconnectCapMs: 1 });
      await startSession(agent, children);
      agent.deliver('work', 6);
      await waitFor(() => prompts(children[0]).length === 1);
      children[0].feed({ jsonrpc: '2.0', id: prompts(children[0])[0].id, result: { stopReason: 'end_turn' } });
      await waitFor(() => !agent.turnActive);
      children[0].finish(1);
      await waitFor(() => children[1]);
      await driveStart(agent, children[1], {
        load: true, loadError: { code: -32603, message: 'Internal error: OpenCode service failure', data: { service: 'session' } }, sessionId: 'ses_second',
      });
      await waitFor(() => agent.connected);
      assert.equal(store.global().sessionId, 'ses_second');
      await agent.stop();
      assert.match(await firstPromptAfterRestart(store, 'ses_second'), catchup);
    });

    it('does not repeat the catch-up after a restart into a transcript that has content', async () => {
      const store = withHistory(storedSession());
      const { agent, children } = newAgent(store);
      const child = await startSession(agent, children, { load: true, loadFrames: replayedTurn });
      agent.deliver('next', 9);
      await waitFor(() => prompts(child).length === 1);
      assert.doesNotMatch(prompts(child)[0].params.prompt[0].text, catchup);
      await agent.stop();
    });

    it('keeps the catch-up owed by a session that never got a prompt when its rotation is cancelled', async () => {
      const store = withHistory(makeStore());
      const { agent, children } = newAgent(store);
      await startSession(agent, children);
      assert.equal(agent.needsHistoryPrefix, true);
      const preparing = agent.prepareSessionRotation();
      await waitFor(() => children[1]);
      await driveStart(agent, children[1], { sessionId: 'ses_prepared' });
      await preparing;
      const cancelling = agent.cancelSessionRotation();
      await waitFor(() => children[2]);
      await driveStart(agent, children[2], { load: true });
      await cancelling;
      assert.equal(agent.sessionId, 'ses_start1');
      agent.deliver('first', 6);
      await waitFor(() => prompts(children[2]).length === 1);
      assert.match(prompts(children[2])[0].params.prompt[0].text, catchup);
      await agent.stop();
    });

    it('keeps a refused prompt\'s catch-up across a reconnect into a transcript that has content', async () => {
      const store = withHistory(makeStore());
      const failed = [];
      const { agent, children } = newAgent(store, { onTurnFailed: (info) => failed.push(info), reconnectBaseMs: 1, reconnectCapMs: 1 });
      await startSession(agent, children);
      agent.deliver('one', 6);
      await waitFor(() => prompts(children[0]).length === 1);
      children[0].feed({ jsonrpc: '2.0', id: prompts(children[0])[0].id, error: { code: -32602, message: 'Invalid params: session not found' } });
      await waitFor(() => failed.length === 1);
      children[0].finish(1);
      await waitFor(() => children[1]);
      await driveStart(agent, children[1], { load: true, loadFrames: replayedTurn });
      await waitFor(() => agent.connected);
      agent.deliver('two', 7);
      await waitFor(() => prompts(children[1]).length === 1);
      assert.match(prompts(children[1])[0].params.prompt[0].text, catchup);
      await agent.stop();
    });

    it('treats a prompt error after the turn produced updates as accepted, whatever its code', async () => {
      const store = withHistory(makeStore());
      const failed = [];
      const { agent, children } = newAgent(store, { onTurnFailed: (info) => failed.push(info) });
      const child = await startSession(agent, children);
      agent.deliver('one', 6);
      await waitFor(() => prompts(child).length === 1);
      update(child, { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'working' } });
      update(child, { sessionUpdate: 'tool_call', toolCallId: 't1', title: 'read', status: 'in_progress', rawInput: { path: 'a' } });
      child.feed({ jsonrpc: '2.0', id: prompts(child)[0].id, error: { code: -32000, message: 'stream ended' } });
      await waitFor(() => failed.length === 1);
      assert.equal(agent.needsHistoryPrefix, false);
      assert.equal(store.global().syncedThroughSeq, 6);
      agent.deliver('two', 7);
      await waitFor(() => prompts(child).length === 2);
      assert.equal(prompts(child)[1].params.sessionId, 'ses_start1');
      assert.doesNotMatch(prompts(child)[1].params.prompt[0].text, catchup);
      await agent.stop();
    });

    it('does not repeat the catch-up after a provider failure, which OpenCode answers without any update', async () => {
      const store = withHistory(makeStore());
      const failed = [];
      const { agent, children } = newAgent(store, { onTurnFailed: (info) => failed.push(info) });
      const child = await startSession(agent, children);
      agent.deliver('one', 6);
      await waitFor(() => prompts(child).length === 1);
      assert.match(prompts(child)[0].params.prompt[0].text, catchup);
      child.feed({ jsonrpc: '2.0', id: prompts(child)[0].id, error: { code: -32603, message: 'Internal error: stub http 400', data: { service: 'session', errorName: 'APIError' } } });
      await waitFor(() => failed.length === 1);
      assert.equal(agent.needsHistoryPrefix, false);
      assert.equal(store.global().syncedThroughSeq, 6);
      agent.deliver('two', 7);
      await waitFor(() => prompts(child).length === 2);
      assert.doesNotMatch(prompts(child)[1].params.prompt[0].text, catchup);
      await agent.stop();
    });
  });

  it('records a pending bash call with its command, not the cwd-only input it is announced with', async () => {
    const { agent, children, store } = newAgent();
    const child = await startSession(agent, children);
    agent.deliver('work', 7);
    await waitFor(() => prompts(child).length === 1);
    const update = (fields) => child.feed({ method: 'session/update', params: { sessionId: 'ses_start1', update: fields } });
    update({ sessionUpdate: 'tool_call', toolCallId: 't1', title: 'bash', kind: 'execute', status: 'pending', rawInput: { cwd: '/work' } });
    assert.equal(store.activities.some((activity) => activity.kind === 'agent.tool_called'), false);
    update({ sessionUpdate: 'tool_call_update', toolCallId: 't1', status: 'pending', title: 'bash', rawInput: { cwd: '/work' } });
    assert.equal(store.activities.some((activity) => activity.kind === 'agent.tool_called'), false);
    update({ sessionUpdate: 'tool_call_update', toolCallId: 't1', status: 'in_progress', title: 'echo hi', rawInput: { cwd: '/work', command: 'echo hi' } });
    update({ sessionUpdate: 'tool_call_update', toolCallId: 't1', status: 'completed', title: 'echo hi', rawInput: { cwd: '/work', command: 'echo hi' }, content: [] });
    child.feed({ jsonrpc: '2.0', id: prompts(child)[0].id, result: { stopReason: 'end_turn' } });
    await waitFor(() => !agent.turnActive);
    const called = store.activities.filter((activity) => activity.kind === 'agent.tool_called');
    assert.equal(called.length, 1);
    assert.equal(called[0].summary, 'echo hi');
    assert.match(JSON.stringify(called[0].metadata.input), /echo hi/);
    await agent.stop();
  });

  it('fails a cancelled turn that OpenCode still answers with end_turn, and only that turn', async () => {
    const succeeded = [];
    const failed = [];
    const { agent, children } = newAgent(makeStore(), {
      onTurnSucceeded: (info) => succeeded.push(info),
      onTurnFailed: (info) => failed.push(info),
    });
    const child = await startSession(agent, children);
    agent.deliver('retrying', 6);
    await waitFor(() => prompts(child).length === 1);
    assert.equal(agent.cancelTurn(), true);
    child.feed({ jsonrpc: '2.0', id: prompts(child)[0].id, result: { stopReason: 'end_turn', usage: { totalTokens: 0 } } });
    await waitFor(() => !agent.turnActive);
    assert.equal(succeeded.length, 0);
    assert.deepEqual(failed.map((info) => info.inboundSeqs), [[6]]);
    assert.match(failed[0].error, /turn ended: cancelled/);
    agent.deliver('later', 7);
    await waitFor(() => prompts(child).length === 2);
    child.feed({ jsonrpc: '2.0', id: prompts(child)[1].id, result: { stopReason: 'end_turn' } });
    await waitFor(() => !agent.turnActive);
    assert.deepEqual(succeeded.map((info) => info.inboundSeqs), [[7]]);
    await agent.stop();
  });
});
