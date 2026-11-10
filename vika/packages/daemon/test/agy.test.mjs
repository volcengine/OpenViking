// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { after, describe, it } from 'node:test';
import { AgyAgent } from '../src/agy.mjs';
import { classifyModelError } from '../src/runtime-catalog.mjs';
import { RuntimeAdapter } from '../src/runtime-adapter.mjs';

// The launcher reads the owner's ~/.gemini; never the real one in a test.
const fakeHome = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-agy-test-home-'));
process.env.HOME = fakeHome;
after(() => fs.rmSync(fakeHome, { recursive: true, force: true }));

const quietLogger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

const baseConfig = {
  agyBin: 'agy',
  agyModel: '',
  workspace: '/tmp/vika-agy-workspace',
  projectDir: '/release',
  dataDir: '/tmp/vika-agy-data',
  databasePath: '/tmp/vika-test.sqlite3',
  profile: 'test-profile',
  connectors: ['lark'],
  larkCliBin: '/fake/lark-cli',
  slackCliBin: '/fake-slack/slack',
  actionSocketPath: '/tmp/vika-agy-data/actions.sock',
  actionMcpPath: '/release/packages/daemon/src/actions/mcp.mjs',
  sendTimeoutMs: 5_000,
  maxReplyChars: 1_000,
  dryRun: true,
};

class FakeChild extends EventEmitter {
  constructor() {
    super();
    this.pid = 987_654_423;
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

const requests = (child) => child.writes.map((line) => JSON.parse(line)).filter((message) => message.method);
const prompts = (child) => requests(child).filter((message) => message.method === 'session/prompt');

function makeStore({ globalOverride } = {}) {
  let global = {
    sessionId: null, policyHash: null, runtime: null, epoch: 0, syncedThroughSeq: 0, sessionReady: false, ...globalOverride,
  };
  const settings = {};
  const activities = [];
  return {
    settings,
    activities,
    getCognitionState: () => ({ runtime: 'agy', epoch: 1, authorityToken: 'agy-token' }),
    getGlobalSession: () => global,
    setGlobalSession(sessionId, policyHash, fields) { global = { sessionId, policyHash, ...fields }; },
    publishOwnedSession({ sessionId, policyHash, runtime, epoch, acceptedThroughSeq, sessionReady }) {
      global = { sessionId, policyHash, runtime, epoch, syncedThroughSeq: acceptedThroughSeq, sessionReady };
      return true;
    },
    advanceOwnedSessionAcceptance({ sessionId, acceptedThroughSeq }) {
      if (global.sessionId !== sessionId) return false;
      global = { ...global, syncedThroughSeq: Math.max(global.syncedThroughSeq, acceptedThroughSeq) };
      return true;
    },
    markOwnedSessionUnresumable() { return true; },
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
  const agent = new AgyAgent(config, store, quietLogger, {
    spawn,
    terminateProcessTree: (child, signal) => child.kill(signal),
    startTimeoutMs: 1_000,
    startStabilityMs: 1,
    controlTimeoutMs: 500,
    ...options,
  });
  return { agent, spawnCalls, children, store };
}

const INITIALIZE_RESULT = {
  protocolVersion: 1,
  agentInfo: { name: 'Antigravity ACP Server', version: '1.5.3' },
  agentCapabilities: { loadSession: true, _meta: { antigravity: { systemPrompt: { append: true } } } },
};

const MODELS = {
  availableModels: [
    { modelId: 'gemini-3.8-flash-low', name: 'Gemini 3.8 Flash (Low)' },
    { modelId: 'gemini-3.8-flash-high', name: 'Gemini 3.8 Flash (High)' },
  ],
  currentModelId: 'gemini-3.8-flash-low',
};

function answerSetModel(child) {
  const setModel = requests(child).find((message) => message.method === 'session/set_model');
  if (setModel) child.feed({ jsonrpc: '2.0', id: setModel.id, result: {} });
}

async function driveStart(agent, child, {
  sessionId = 'agy-ses-1', load = false, loadFrames = [], newResult = { sessionId, models: MODELS },
} = {}) {
  await waitFor(() => requests(child).some((message) => message.method === 'initialize'));
  const init = requests(child).find((message) => message.method === 'initialize');
  child.feed({ jsonrpc: '2.0', id: init.id, result: INITIALIZE_RESULT });
  await flush();
  if (load) {
    await waitFor(() => requests(child).some((message) => message.method === 'session/load'));
    const sessionLoad = requests(child).find((message) => message.method === 'session/load');
    for (const update of loadFrames) {
      child.feed({ jsonrpc: '2.0', method: 'session/update', params: { sessionId: sessionLoad.params.sessionId, update } });
    }
    child.feed({ jsonrpc: '2.0', id: sessionLoad.id, result: { sessionId: sessionLoad.params.sessionId, models: MODELS } });
    await flush();
  }
  if (requests(child).some((message) => message.method === 'session/new') || !load) {
    await waitFor(() => requests(child).some((message) => message.method === 'session/new'));
    const sessionNew = requests(child).find((message) => message.method === 'session/new');
    child.feed({ jsonrpc: '2.0', id: sessionNew.id, result: newResult });
    await flush();
  }
  answerSetModel(child);
  await flush();
}

async function startSession(agent, children, options = {}) {
  const started = agent.start();
  await waitFor(() => children[0]);
  await driveStart(agent, children[0], options);
  await started;
  return children[0];
}

function storedSession(overrides = {}) {
  return makeStore({
    globalOverride: {
      sessionId: 'agy-saved',
      policyHash: new AgyAgent(baseConfig, makeStore(), quietLogger).policyHash,
      runtime: 'agy',
      epoch: 1,
      sessionReady: true,
      syncedThroughSeq: 3,
      ...overrides,
    },
  });
}

const replayedTurn = [
  { sessionUpdate: 'user_message_chunk', content: { type: 'text', text: 'earlier question' } },
  { sessionUpdate: 'tool_call', toolCallId: 'old-1', name: 'run_command', title: 'ls', status: 'completed', rawInput: { CommandLine: 'ls' } },
  { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'earlier answer' } },
];

function withHistory(store, { journalSeq = 5, inboundSeq = 6 } = {}) {
  let prepared = 0;
  store.getOVContext = () => ({ sessionId: 'ov', messages: [{ parts: [{ type: 'text', text: 'past' }] }] });
  store.prepareOVContext = () => { prepared += 1; };
  store.cognitionJournalBeforeInbound = () => journalSeq;
  store.cognitionJournalSeqForInbound = () => inboundSeq;
  store.preparedCount = () => prepared;
  return store;
}

describe('AgyAgent ACP backend', () => {
  it('launches agy acp unattended against a Vika-owned gemini dir and no OpenCode config', async () => {
    const { agent, spawnCalls, children } = newAgent();
    await startSession(agent, children);
    const call = spawnCalls[0];
    assert.equal(call.bin, 'agy');
    assert.equal(call.args.length, 3);
    assert.deepEqual(call.args.slice(0, 2), ['acp', '--dangerously-skip-permissions']);
    const geminiDir = call.args[2].slice('--gemini-dir='.length);
    assert.ok(call.args[2].startsWith('--gemini-dir='));
    assert.ok(geminiDir.startsWith(path.join(os.tmpdir(), 'vika-agy-')));
    assert.equal(path.basename(geminiDir), 'config-home');
    assert.equal(fs.statSync(path.join(geminiDir, 'config')).isDirectory(), true);
    assert.equal(call.options.cwd, '/tmp/vika-agy-workspace');
    const env = call.options.env;
    assert.equal(env.OPENCODE_CONFIG, undefined);
    assert.equal(env.HOME, fakeHome);
    assert.equal(env.PERSONAL_AGENT_AUTHORITY, 'agy-token');
    assert.equal(env.LARKSUITE_CLI_PROFILE, 'test-profile');
    await agent.stop();
    assert.equal(fs.existsSync(geminiDir), false, 'the config home goes with the process');
  });

  it('warns about the workspace and starts agy without the owner\'s OpenViking', async (t) => {
    const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-agy-test-ws-'));
    t.after(() => {
      fs.rmSync(workspace, { recursive: true, force: true });
      fs.rmSync(path.join(fakeHome, '.gemini'), { recursive: true, force: true });
    });
    const plugin = path.join(workspace, '.agents', 'plugins', 'openviking-memory');
    fs.mkdirSync(plugin, { recursive: true });
    fs.writeFileSync(path.join(plugin, 'plugin.json'), '{"name":"openviking-memory"}');
    fs.mkdirSync(path.join(fakeHome, '.gemini', 'config'), { recursive: true });
    fs.writeFileSync(path.join(fakeHome, '.gemini', 'config', 'mcp_config.json'), JSON.stringify({
      mcpServers: { openviking: { command: 'ov' }, keepme: { command: 'tool' } },
    }));
    const warnings = [];
    const logger = { ...quietLogger, warn: (event, fields) => warnings.push({ event, fields }), child() { return this; } };
    const spawnCalls = [];
    const children = [];
    const agent = new AgyAgent({ ...baseConfig, workspace }, makeStore(), logger, {
      spawn: (bin, args) => { const child = new FakeChild(); spawnCalls.push(args); children.push(child); return child; },
      terminateProcessTree: (child, signal) => child.kill(signal),
      startTimeoutMs: 1_000, startStabilityMs: 1, controlTimeoutMs: 500,
    });
    await startSession(agent, children);
    const warning = warnings.find(({ event }) => event === 'agy_workspace_openviking_pieces');
    assert.deepEqual(warning.fields.pieces, [plugin]);
    const geminiDir = spawnCalls[0][2].slice('--gemini-dir='.length);
    const mcp = JSON.parse(fs.readFileSync(path.join(geminiDir, 'config', 'mcp_config.json'), 'utf8'));
    assert.deepEqual(Object.keys(mcp.mcpServers), ['keepme']);
    assert.equal(fs.statSync(path.join(geminiDir, 'config', '.migrated')).size, 0);
    await agent.stop();
  });

  it('honors agyBin', async () => {
    const { agent, spawnCalls, children } = newAgent(makeStore(), {}, { ...baseConfig, agyBin: '/opt/agy' });
    await startSession(agent, children);
    assert.equal(spawnCalls[0].bin, '/opt/agy');
    await agent.stop();
  });

  it('passes the MCP server and the appended system prompt to session/new', async () => {
    const { agent, children } = newAgent();
    const child = await startSession(agent, children);
    const sessionNew = requests(child).find((message) => message.method === 'session/new');
    assert.equal(sessionNew.params.cwd, '/tmp/vika-agy-workspace');
    assert.deepEqual(sessionNew.params.mcpServers, [{
      name: 'personal_agent',
      command: process.execPath,
      args: ['/release/packages/daemon/src/actions/mcp.mjs', '--socket', '/tmp/vika-agy-data/actions.sock', '--authority', 'agy-token'],
      env: [],
    }]);
    assert.match(sessionNew.params._meta.antigravity.systemPrompt.append, /Operating policy/);
    await agent.stop();
  });

  it('rejects an agent that is not Antigravity or cannot append a system prompt', async () => {
    for (const [result, pattern] of [
      [{ agentInfo: { name: 'SomeoneElse' } }, /unrecognised ACP agent/],
      [{ agentInfo: { name: 'Antigravity ACP Server' }, agentCapabilities: {} }, /cannot append a system prompt/],
    ]) {
      const { agent, children } = newAgent();
      const started = agent.start();
      await waitFor(() => children[0] && requests(children[0]).some((message) => message.method === 'initialize'));
      const init = requests(children[0]).find((message) => message.method === 'initialize');
      children[0].feed({ jsonrpc: '2.0', id: init.id, result });
      await assert.rejects(started, pattern);
    }
  });

  it('turns Authentication required into a login message without losing the stored session', async () => {
    const store = storedSession();
    const { agent, children } = newAgent(store);
    const started = agent.start();
    await waitFor(() => children[0] && requests(children[0]).some((message) => message.method === 'initialize'));
    const child = children[0];
    child.feed({ jsonrpc: '2.0', id: requests(child)[0].id, result: INITIALIZE_RESULT });
    await waitFor(() => requests(child).some((message) => message.method === 'session/load'));
    const load = requests(child).find((message) => message.method === 'session/load');
    child.feed({ jsonrpc: '2.0', id: load.id, error: { code: -32000, message: 'Authentication required' } });
    await assert.rejects(started, /not logged in: run agy/);
    assert.equal(agent.sessionId, 'agy-saved');
    assert.equal(agent.sessionInitialized, true);
    assert.equal(requests(child).some((message) => message.method === 'session/new'), false);
    assert.equal(store.global().sessionId, 'agy-saved', 'the session survives until someone signs in');
  });

  it('reports a not-logged-in session/new and a not-logged-in turn in the same words', async () => {
    const newSession = newAgent();
    const started = newSession.agent.start();
    await waitFor(() => newSession.children[0] && requests(newSession.children[0]).some((message) => message.method === 'initialize'));
    newSession.children[0].feed({ jsonrpc: '2.0', id: requests(newSession.children[0])[0].id, result: INITIALIZE_RESULT });
    await waitFor(() => requests(newSession.children[0]).some((message) => message.method === 'session/new'));
    const sessionNew = requests(newSession.children[0]).find((message) => message.method === 'session/new');
    newSession.children[0].feed({ jsonrpc: '2.0', id: sessionNew.id, error: { code: -32000, message: 'Authentication required' } });
    await assert.rejects(started, /not logged in: run agy/);

    const failed = [];
    const { agent, children } = newAgent(makeStore(), { onTurnFailed: (info) => failed.push(info) });
    const child = await startSession(agent, children);
    agent.deliver('hello', 7);
    await waitFor(() => prompts(child).length === 1);
    child.feed({ jsonrpc: '2.0', id: prompts(child)[0].id, error: { code: -32000, message: 'Authentication required' } });
    await waitFor(() => failed.length === 1);
    assert.match(failed[0].error, /not logged in: run agy/);
    assert.equal(agent.needsHistoryPrefix, true, 'a not-logged-in prompt was refused, so the catch-up is still owed');
    await agent.stop();
  });

  it('offers the models Antigravity reports and starts on the session default', async () => {
    const { agent, children } = newAgent();
    const child = await startSession(agent, children);
    const models = await agent.listModels();
    assert.deepEqual(models.map((entry) => entry.id), ['gemini-3.8-flash-low', 'gemini-3.8-flash-high']);
    assert.equal(models.find((entry) => entry.id === 'gemini-3.8-flash-low').displayName, 'Gemini 3.8 Flash (Low)');
    assert.equal(models.find((entry) => entry.id === 'gemini-3.8-flash-low').isDefault, true);
    assert.deepEqual(models[0].efforts, []);
    assert.equal(models[0].effortsKnown, false);
    assert.equal(agent.resolvedModel, 'gemini-3.8-flash-low');
    assert.equal(requests(child).some((message) => message.method === 'session/set_model'), false);
    await agent.stop();
  });

  it('applies a configured model through session/set_model before the first prompt', async () => {
    const { agent, children, store } = newAgent(makeStore(), {}, { ...baseConfig, agyModel: 'gemini-3.8-flash-high' });
    const child = await startSession(agent, children);
    const setModel = requests(child).find((message) => message.method === 'session/set_model');
    assert.deepEqual(setModel.params, { sessionId: 'agy-ses-1', modelId: 'gemini-3.8-flash-high' });
    assert.equal(requests(child).some((message) => message.method === 'session/set_config_option'), false);
    assert.equal(agent.resolvedModel, 'gemini-3.8-flash-high');
    agent.setModel('gemini-3.8-flash-low');
    assert.equal(store.settings.agy_model, 'gemini-3.8-flash-low');
    await agent.stop();
  });

  it('applies a /model choice before the next prompt, never during one', async () => {
    const { agent, children } = newAgent();
    const child = await startSession(agent, children);
    const adapter = new RuntimeAdapter('agy', agent);
    adapter.applySettings({ model: 'gemini-3.8-flash-high' });
    agent.deliver('next turn', 7);
    await waitFor(() => requests(child).some((message) => message.method === 'session/set_model'));
    assert.equal(prompts(child).length, 0, 'the prompt waits for the model');
    answerSetModel(child);
    await waitFor(() => prompts(child).length === 1);
    assert.equal(agent.appliedModel, 'gemini-3.8-flash-high');
    await agent.stop();
  });

  it('fails queued work and classifies an unknown model the way the runtime expects', async () => {
    const failed = [];
    const { agent, children } = newAgent(makeStore(), { onTurnFailed: (info) => failed.push(info) });
    const child = await startSession(agent, children);
    agent.setModel('gemini-nope');
    agent.deliver('turn', 7);
    await waitFor(() => requests(child).some((message) => message.method === 'session/set_model'));
    const setModel = requests(child).find((message) => message.method === 'session/set_model');
    child.feed({ jsonrpc: '2.0', id: setModel.id, error: { code: -32602, message: 'Unknown model ID: gemini-nope' } });
    await waitFor(() => failed.length === 1);
    assert.equal(prompts(child).length, 0);
    assert.equal(classifyModelError(new Error(failed[0].error)).kind, 'unknown-model');
    await agent.stop();
  });

  it('records the tool name from update.name and treats failed as a failure', async () => {
    const { agent, children, store } = newAgent();
    const child = await startSession(agent, children);
    agent.deliver('work', 7);
    await waitFor(() => prompts(child).length === 1);
    const update = (fields) => child.feed({ method: 'session/update', params: { sessionId: 'agy-ses-1', update: fields } });
    update({ sessionUpdate: 'tool_call', toolCallId: 't1', name: 'run_command', title: 'echo ok', status: 'in_progress', rawInput: { CommandLine: 'echo ok' } });
    update({ sessionUpdate: 'tool_call_update', toolCallId: 't1', status: 'completed', rawOutput: { output: 'ok' } });
    update({ sessionUpdate: 'tool_call', toolCallId: 't2', name: 'mcp_personal_agent_send_message', title: 'Send', status: 'in_progress', rawInput: { chat_id: 'x' } });
    update({ sessionUpdate: 'tool_call_update', toolCallId: 't2', status: 'failed', rawOutput: { error: 'denied' } });
    child.feed({ jsonrpc: '2.0', id: prompts(child)[0].id, result: { stopReason: 'end_turn' } });
    await waitFor(() => !agent.turnActive);
    const called = store.activities.filter((activity) => activity.kind === 'agent.tool_called');
    assert.deepEqual(called.map((activity) => [activity.metadata.toolName, activity.summary]), [
      ['run_command', 'echo ok'],
      ['mcp_personal_agent_send_message', 'Send'],
    ]);
    const completed = store.activities.filter((activity) => activity.kind === 'agent.tool_completed');
    assert.deepEqual(completed.map((activity) => [activity.status, activity.metadata.runtime]), [
      ['COMPLETED', 'agy'], ['FAILED', 'agy'],
    ]);
    assert.match(JSON.stringify(completed[1].metadata.error), /denied/);
    assert.match(JSON.stringify(completed[0].metadata.output), /ok/);
    assert.equal(store.activities.find((activity) => activity.kind === 'agent.turn_started').metadata.runtime, 'agy');
    assert.ok(store.activities.every((activity) => activity.eventKey.startsWith('agy:')));
    await agent.stop();
  });

  it('keeps replayed history out of notes and activity, and carries the MCP server and prompt into session/load', async () => {
    const store = storedSession();
    const { agent, children } = newAgent(store);
    const child = await startSession(agent, children, { load: true, loadFrames: replayedTurn });
    const load = requests(child).find((message) => message.method === 'session/load');
    assert.equal(load.params.sessionId, 'agy-saved');
    assert.equal(load.params.mcpServers[0].name, 'personal_agent');
    assert.match(load.params._meta.antigravity.systemPrompt.append, /Operating policy/);
    assert.equal(requests(child).some((message) => message.method === 'session/new'), false);
    assert.equal(agent.threadId, 'agy-saved');
    assert.equal(agent.noteText, '');
    assert.equal(agent.toolStarts.size, 0);
    assert.equal(store.activities.length, 0);
    assert.deepEqual((await agent.listModels()).map((entry) => entry.id), ['gemini-3.8-flash-low', 'gemini-3.8-flash-high']);

    agent.deliver('continue', 8);
    await waitFor(() => prompts(child).length === 1);
    child.feed({ jsonrpc: '2.0', method: 'session/update', params: { sessionId: 'agy-saved', update: { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'fresh note' } } } });
    child.feed({ jsonrpc: '2.0', id: prompts(child)[0].id, result: { stopReason: 'end_turn' } });
    await waitFor(() => !agent.turnActive);
    assert.deepEqual(store.activities.filter((activity) => activity.kind === 'agent.note').map((note) => note.summary), ['fresh note']);
    await agent.stop();
  });

  it('re-applies the configured model after session/load', async () => {
    const store = storedSession();
    const { agent, children } = newAgent(store, {}, { ...baseConfig, agyModel: 'gemini-3.8-flash-high' });
    const child = await startSession(agent, children, { load: true, loadFrames: replayedTurn });
    const setModel = requests(child).find((message) => message.method === 'session/set_model');
    assert.deepEqual(setModel.params, { sessionId: 'agy-saved', modelId: 'gemini-3.8-flash-high' });
    await agent.stop();
  });

  it('treats a session/load that replays nothing as a lost transcript and opens a fresh session', async () => {
    const warnings = [];
    const store = storedSession();
    const logger = { ...quietLogger, warn: (event) => warnings.push(event), child() { return this; } };
    const spawns = [];
    const children = [];
    const agent = new AgyAgent(baseConfig, store, logger, {
      spawn: (bin, args, options) => { const child = new FakeChild(); spawns.push(options); children.push(child); return child; },
      terminateProcessTree: (child, signal) => child.kill(signal),
      startTimeoutMs: 1_000, startStabilityMs: 1, controlTimeoutMs: 500,
    });
    const child = await startSession(agent, children, { load: true, sessionId: 'agy-fresh' });
    assert.ok(requests(child).some((message) => message.method === 'session/new'));
    assert.equal(agent.threadId, 'agy-fresh');
    assert.equal(agent.needsHistoryPrefix, true, 'the journal catches the fresh session up');
    assert.ok(warnings.includes('agy_session_load_failed_fresh_start'));
    assert.equal(store.global().sessionId, 'agy-fresh');
    await agent.stop();
  });

  it('keeps a session with no acknowledged history and an empty replay, and still sends its catch-up', async () => {
    const store = withHistory(storedSession({ syncedThroughSeq: 0 }));
    const { agent, children } = newAgent(store);
    const child = await startSession(agent, children, { load: true });
    assert.equal(requests(child).some((message) => message.method === 'session/new'), false);
    assert.equal(agent.threadId, 'agy-saved');
    assert.equal(agent.needsHistoryPrefix, true, 'nothing has reached this transcript yet');
    assert.equal(store.preparedCount(), 1, 'the catch-up needs OpenViking context');
    agent.deliver('hi', 6);
    await waitFor(() => prompts(child).length === 1);
    assert.match(prompts(child)[0].params.prompt[0].text, /daemon OpenViking context[\s\S]*past[\s\S]*hi$/);
    await agent.stop();
  });

  it('keeps the catch-up and the watermark when the agent refuses a prompt', async () => {
    const store = withHistory(makeStore());
    const failed = [];
    const { agent, children } = newAgent(store, { onTurnFailed: (info) => failed.push(info) });
    const child = await startSession(agent, children);
    agent.deliver('one', 6);
    await waitFor(() => prompts(child).length === 1);
    assert.match(prompts(child)[0].params.prompt[0].text, /daemon OpenViking context/);
    child.feed({ jsonrpc: '2.0', id: prompts(child)[0].id, error: { code: -32000, message: 'Resource exhausted' } });
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

  it('ignores a process that died during session/load once a later one has resumed', async () => {
    const store = makeStore();
    const { agent, children } = newAgent(store, { reconnectBaseMs: 1, reconnectCapMs: 1, controlTimeoutMs: 80 });
    await startSession(agent, children);
    children[0].finish(1);
    await waitFor(() => children[1] && requests(children[1]).some((message) => message.method === 'initialize'));
    children[1].feed({ jsonrpc: '2.0', id: requests(children[1])[0].id, result: INITIALIZE_RESULT });
    await waitFor(() => requests(children[1]).some((message) => message.method === 'session/load'));
    children[1].finish(1);
    await waitFor(() => children[2]);
    await driveStart(agent, children[2], { load: true, loadFrames: replayedTurn });
    await waitFor(() => agent.connected);
    await new Promise((resolve) => setTimeout(resolve, 120)); // past the dead load's timer
    assert.equal(agent.sessionId, 'agy-ses-1');
    assert.equal(agent.sessionInitialized, true);
    agent.deliver('after', 9);
    await waitFor(() => prompts(children[2]).length === 1);
    assert.equal(prompts(children[2])[0].params.sessionId, 'agy-ses-1');
    await agent.stop();
  });

  it('publishes the fresh session a mid-turn crash reconnects to', async () => {
    const store = makeStore();
    store.cognitionJournalSeqForInbound = () => 6;
    const { agent, children } = newAgent(store, { reconnectBaseMs: 1, reconnectCapMs: 1 });
    await startSession(agent, children);
    agent.deliver('work', 6);
    await waitFor(() => prompts(children[0]).length === 1);
    children[0].finish(1);
    await waitFor(() => children[1]);
    await driveStart(agent, children[1], { sessionId: 'agy-ses-2' });
    await waitFor(() => agent.connected);
    assert.equal(agent.sessionId, 'agy-ses-2');
    assert.equal(store.global().sessionId, 'agy-ses-2');
    assert.equal(store.global().sessionReady, true);
    agent.deliver('again', 7);
    await waitFor(() => prompts(children[1]).length === 1);
    children[1].feed({ jsonrpc: '2.0', id: prompts(children[1])[0].id, result: { stopReason: 'end_turn' } });
    await waitFor(() => !agent.turnActive);
    assert.equal(store.global().syncedThroughSeq, 6, 'the new session owns the watermark');
    await agent.stop();
  });

  it('does not publish a prepared session when its process crashes before the commit', async () => {
    const store = makeStore();
    const { agent, children } = newAgent(store, { reconnectBaseMs: 1, reconnectCapMs: 1 });
    await startSession(agent, children);
    const preparing = agent.prepareSessionRotation();
    await waitFor(() => children[1]);
    await driveStart(agent, children[1], { sessionId: 'agy-ses-2' });
    await preparing;
    children[1].finish(1);
    await waitFor(() => children[2]);
    await driveStart(agent, children[2], { load: true, loadFrames: replayedTurn });
    await waitFor(() => agent.connected);
    assert.equal(agent.sessionId, 'agy-ses-2');
    assert.equal(store.global().sessionId, 'agy-ses-1');
    await agent.stop();
  });

  it('reopens the previous session when a rotation is cancelled', async () => {
    const store = makeStore();
    const { agent, children } = newAgent(store);
    await startSession(agent, children);
    const preparing = agent.prepareSessionRotation();
    await waitFor(() => children[1]);
    await driveStart(agent, children[1], { sessionId: 'agy-ses-2' });
    await preparing;
    const cancelling = agent.cancelSessionRotation();
    await waitFor(() => children[2]);
    await driveStart(agent, children[2], { load: true, loadFrames: replayedTurn });
    await cancelling;
    assert.deepEqual(requests(children[2]).map((message) => message.method), ['initialize', 'session/load']);
    assert.equal(requests(children[2])[1].params.sessionId, 'agy-ses-1');
    assert.equal(agent.sessionId, 'agy-ses-1');
    assert.equal(store.global().sessionId, 'agy-ses-1');
    await agent.stop();
  });

  it('publishes the replacement when a cancelled rotation cannot reopen the previous session', async () => {
    const store = storedSession();
    const { agent, children } = newAgent(store);
    await startSession(agent, children, { load: true, loadFrames: replayedTurn });
    const preparing = agent.prepareSessionRotation();
    await waitFor(() => children[1]);
    await driveStart(agent, children[1], { sessionId: 'agy-ses-2' });
    await preparing;
    const cancelling = agent.cancelSessionRotation();
    await waitFor(() => children[2]);
    await driveStart(agent, children[2], { load: true, sessionId: 'agy-ses-3' });
    await cancelling;
    assert.equal(agent.sessionId, 'agy-ses-3');
    assert.equal(store.global().sessionId, 'agy-ses-3');
    await agent.stop();
  });

  it('gives each process its own activity keys for the same resumed session', async () => {
    const store = storedSession();
    const runOnce = async () => {
      const { agent, children } = newAgent(store);
      const child = await startSession(agent, children, { load: true, loadFrames: replayedTurn });
      agent.deliver('turn', 8);
      await waitFor(() => prompts(child).length === 1);
      child.feed({ jsonrpc: '2.0', id: prompts(child)[0].id, result: { stopReason: 'end_turn' } });
      await waitFor(() => !agent.turnActive);
      await agent.stop();
    };
    await runOnce();
    await runOnce();
    const keys = store.activities.map((activity) => activity.eventKey);
    assert.equal(keys.length, 4);
    assert.equal(new Set(keys).size, 4);
  });

  it('queues mid-turn messages and never has two prompts live', async () => {
    const { agent, children, store } = newAgent();
    const child = await startSession(agent, children);
    assert.equal(agent.runtime, 'agy');
    assert.equal(store.getGlobalSession().runtime, 'agy');
    agent.deliver('first', 7);
    await waitFor(() => prompts(child).length === 1);
    agent.deliver('second', 8);
    assert.equal(agent.tryStartBatch([{ text: 'third', seq: 9 }]), false);
    await flush();
    assert.equal(prompts(child).length, 1);
    assert.equal(agent.state.queued, 1);
    child.feed({ jsonrpc: '2.0', id: prompts(child)[0].id, result: { stopReason: 'end_turn' } });
    await waitFor(() => prompts(child).length === 2);
    assert.deepEqual(prompts(child)[1].params.prompt, [{ type: 'text', text: 'second' }]);
    child.feed({ jsonrpc: '2.0', id: prompts(child)[1].id, result: { stopReason: 'end_turn' } });
    await waitFor(() => !agent.turnActive);
    await agent.stop();
  });

  it('holds new prompts after an interrupt until the abandoned prompt has settled', async () => {
    const { agent, children } = newAgent();
    const child = await startSession(agent, children);
    agent.deliver('abandoned', 7);
    await waitFor(() => prompts(child).length === 1);
    await agent.interruptTurn();
    assert.equal(agent.turnActive, false);
    assert.equal(agent.tryStartBatch([{ text: 'racer', seq: 8 }]), false);
    agent.deliver('later', 9);
    await flush();
    assert.equal(prompts(child).length, 1, 'the first prompt is still live on the wire');
    assert.equal(agent.state.queued, 1);
    child.feed({ jsonrpc: '2.0', id: prompts(child)[0].id, result: { stopReason: 'cancelled' } });
    await waitFor(() => prompts(child).length === 2);
    assert.deepEqual(prompts(child)[1].params.prompt, [{ type: 'text', text: 'later' }]);
    await agent.stop();
  });

  it('reports itself available once an abandoned prompt settles with nothing queued', async () => {
    let available = 0;
    const { agent, children } = newAgent(makeStore(), { onAvailable: () => { available += 1; } });
    const child = await startSession(agent, children);
    agent.deliver('abandoned', 7);
    await waitFor(() => prompts(child).length === 1);
    await agent.interruptTurn();
    available = 0;
    assert.equal(agent.tryStartBatch([{ text: 'too early', seq: 8 }]), false);
    child.feed({ jsonrpc: '2.0', id: prompts(child)[0].id, result: { stopReason: 'cancelled' } });
    await waitFor(() => available === 1);
    assert.equal(agent.tryStartBatch([{ text: 'now', seq: 9 }]), true);
    await waitFor(() => prompts(child).length === 2);
    await agent.stop();
  });

  it('cancels a live turn in place and reports the stop as the runtime expects', async () => {
    const failed = [];
    const { agent, children } = newAgent(makeStore(), { onTurnFailed: (info) => failed.push(info) });
    const child = await startSession(agent, children);
    agent.deliver('loop', 7);
    await waitFor(() => prompts(child).length === 1);
    assert.equal(agent.cancelTurn(), true);
    assert.deepEqual(requests(child).at(-1), { jsonrpc: '2.0', method: 'session/cancel', params: { sessionId: 'agy-ses-1' } });
    assert.equal(agent.turnActive, true);
    child.feed({ jsonrpc: '2.0', id: prompts(child)[0].id, result: { stopReason: 'cancelled' } });
    await waitFor(() => !agent.turnActive);
    assert.deepEqual(failed.map((info) => info.error), ['agy turn ended: cancelled']);
    assert.equal(children.length, 1, 'same process');
    await agent.stop();
  });

  it('fails a turn that agy reports as terminated under a normal end_turn', async () => {
    const failed = [];
    const succeeded = [];
    const { agent, children } = newAgent(makeStore(), {
      onTurnFailed: (info) => failed.push(info),
      onTurnSucceeded: (info) => succeeded.push(info),
    });
    const child = await startSession(agent, children);
    const finish = async (text, index) => {
      agent.deliver(`line ${index}`, index);
      await waitFor(() => prompts(child).length === index);
      child.feed({ jsonrpc: '2.0', method: 'session/update', params: { sessionId: 'agy-ses-1', update: { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text } } } });
      child.feed({ jsonrpc: '2.0', id: prompts(child)[index - 1].id, result: { stopReason: 'end_turn' } });
      await waitFor(() => !agent.turnActive);
    };
    await finish('\n⚠ Agent execution terminated due to error.\n', 1);
    assert.equal(failed.length, 1);
    assert.match(failed[0].error, /terminated due to error/);
    assert.equal(succeeded.length, 0);

    await finish('A normal answer quoting ⚠ Agent execution terminated due to error. in the middle.', 2);
    assert.equal(failed.length, 1);
    assert.equal(succeeded.length, 1);
    await agent.stop();
  });

  it('declines a permission request that arrives anyway', async () => {
    const { agent, children, store } = newAgent();
    const child = await startSession(agent, children);
    agent.deliver('work', 7);
    await waitFor(() => prompts(child).length === 1);
    child.feed({
      jsonrpc: '2.0', method: 'session/request_permission', id: 901,
      params: { sessionId: 'agy-ses-1', options: [{ optionId: 'allow', kind: 'allow_once' }, { optionId: 'deny', kind: 'reject_once' }] },
    });
    await flush();
    const answer = child.writes.map((line) => JSON.parse(line)).find((message) => message.id === 901);
    assert.deepEqual(answer.result, { outcome: { outcome: 'selected', optionId: 'deny' } });
    assert.ok(store.activities.some((activity) => activity.kind === 'agent.interaction_declined'));
    await agent.stop();
  });

  it('has no effort control and a policy hash of its own', () => {
    const { agent } = newAgent();
    assert.throws(() => agent.setEffort('high'), /agy does not support daemon-side effort control/);
    assert.equal(agent.capabilities.effortControl, 'unsupported');
    assert.equal(agent.capabilities.activeInput, 'queue-next-turn');
    assert.equal(agent.policyVersion, 'agy-acp-v1');
  });
});
