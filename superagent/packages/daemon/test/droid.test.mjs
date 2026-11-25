// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { describe, it } from 'node:test';
import { DroidAgent } from '../src/droid.mjs';
import { DROID_NOT_LOGGED_IN, parseDroidModels } from '../src/droid-runtime.mjs';
import { RuntimeAdapter } from '../src/runtime-adapter.mjs';

const quietLogger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

class FakeChild extends EventEmitter {
  constructor() {
    super();
    this.pid = 778_001;
    this.exitCode = null;
    this.writes = [];
    this.stdin = new EventEmitter();
    this.stdin.writable = true;
    this.stdin.write = (line) => { this.writes.push(line); return true; };
    this.stdin.end = () => {};
    this.stdout = new EventEmitter();
    this.stdout.setEncoding = () => {};
    this.stderr = new EventEmitter();
    this.stderr.setEncoding = () => {};
  }

  feed(frame) { this.stdout.emit('data', `${JSON.stringify(frame)}\n`); }
  finish(code = 0, signal = null) {
    if (this.exitCode !== null) return;
    this.exitCode = code;
    this.emit('exit', code, signal);
  }
  kill(signal = 'SIGTERM') { this.finish(signal === 'SIGKILL' ? 137 : 0, signal); }
}

const parsedWrites = (child) => child.writes.map((line) => JSON.parse(line));

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
    getCognitionState: () => ({ runtime: 'droid', epoch: 1, authorityToken: 'dr-token' }),
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
    setSetting: (key, value) => { settings[key] = value; },
    recordActivity: (row) => activities.push(row),
    prepareOVContext() {},
    getOVContext: () => ({ sessionId: 'ov-1', messages: [] }),
    cognitionJournalBeforeInbound: () => 0,
    cognitionJournalSeqForInbound: (seq) => seq,
  };
}

function makeFixture(t, { config = {}, store = makeStore(), options = {} } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-droid-test-'));
  const home = path.join(dir, 'user-home');
  const workspace = path.join(dir, 'workspace');
  const dataDir = path.join(dir, 'data');
  fs.mkdirSync(path.join(home, '.factory'), { recursive: true });
  fs.writeFileSync(path.join(home, '.factory', 'auth.v2.loginkeychain'), 'keychain-data');
  fs.mkdirSync(workspace, { recursive: true });
  fs.mkdirSync(dataDir, { recursive: true });
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));

  const spawns = [];
  const spawn = (command, args, spawnOptions) => {
    const child = new FakeChild();
    spawns.push({ command, args, options: spawnOptions, child });
    return child;
  };
  const cfg = {
    droidBin: 'droid',
    droidModel: '',
    droidEffort: 'medium',
    workspace,
    projectDir: dir,
    dataDir,
    profile: 'test-profile',
    connectors: ['lark'],
    larkCliBin: '/fake/lark-cli',
    slackCliBin: '/fake/slack',
    actionSocketPath: path.join(dataDir, 'actions.sock'),
    actionMcpPath: path.join(dir, 'actions-mcp.mjs'),
    sendTimeoutMs: 5_000,
    maxReplyChars: 1_000,
    dryRun: true,
    ...config,
  };
  const prevHome = process.env.HOME;
  process.env.HOME = home;
  t.after(() => { process.env.HOME = prevHome; });

  const agent = new DroidAgent(cfg, store, quietLogger, {
    spawn,
    terminateProcessTree: (child, signal) => child.kill(signal),
    startStabilityMs: 10,
    controlTimeoutMs: 200,
    ...options,
  });
  return { agent, store, spawns, dir, home, workspace, dataDir };
}

async function waitForSpawn(spawns, index = 0) {
  while (spawns.length <= index) await new Promise((resolve) => setImmediate(resolve));
  return spawns[index];
}

function waitForWrite(child, predicate) {
  return new Promise((resolve) => {
    const check = () => {
      const match = parsedWrites(child).find(predicate);
      if (match) {
        child.stdin.write = origWrite;
        resolve(match);
        return true;
      }
      return false;
    };
    const origWrite = child.stdin.write;
    if (check()) return;
    child.stdin.write = (line) => {
      const ok = origWrite(line);
      check();
      return ok;
    };
  });
}

const SAMPLE_MODELS = [
  { id: 'gpt-6-sol', modelId: 'gpt-6-sol', displayName: 'GPT-6 Sol', supportedReasoningEfforts: ['none', 'low', 'medium', 'high'], defaultReasoningEffort: 'medium', deprecated: false },
  { id: 'auto', modelId: 'auto', displayName: 'Auto Model', supportedReasoningEfforts: ['none'], defaultReasoningEffort: 'none', deprecated: false },
  { id: 'old-model', modelId: 'old-model', displayName: 'Old', supportedReasoningEfforts: ['low'], deprecated: true },
];

describe('DroidAgent stream-jsonrpc backend', () => {
  it('filters deprecated/disabled models and keeps per-model reasoning efforts', () => {
    const models = parseDroidModels(SAMPLE_MODELS, 'gpt-6-sol');
    assert.deepEqual(models.map((m) => m.id), ['gpt-6-sol', 'auto']);
    assert.equal(models[0].isDefault, true);
    assert.deepEqual(models[0].efforts, ['none', 'low', 'medium', 'high']);
    assert.deepEqual(models[1].efforts, ['none']);
  });

  it('keeps HOME intact, sets FACTORY_HOME_OVERRIDE, initializes session with policy & MCP, and handles turns + interrupt', async (t) => {
    const successes = [];
    const failures = [];
    const { agent, store, spawns, dataDir, home } = makeFixture(t, {
      config: { droidModel: 'gpt-6-sol', droidEffort: 'high' },
      options: {
        onTurnSucceeded: (info) => successes.push(info),
        onTurnFailed: (info) => failures.push(info),
      },
    });
    const adapter = new RuntimeAdapter('droid', agent);
    const startPromise = adapter.start();

    const { args, options, child } = await waitForSpawn(spawns);
    assert.ok(args.includes('exec') && args.includes('stream-jsonrpc'));
    assert.equal(options.env.HOME, home);
    assert.equal(options.env.FACTORY_HOME_OVERRIDE, path.join(dataDir, 'droid', 'home'));
    assert.ok(fs.existsSync(path.join(options.env.FACTORY_HOME_OVERRIDE, '.factory', 'auth.v2.loginkeychain')));

    const init = await waitForWrite(child, (m) => m.method === 'droid.initialize_session');
    assert.equal(init.type, 'request');
    assert.equal(init.factoryApiVersion, '1.0.0');
    assert.equal(init.params.disableBuiltinSkills, true);
    assert.equal(init.params.autoRejectPermissionRequests, true);
    assert.equal(init.params.systemPromptOverride, agent.systemPrompt);
    assert.equal(init.params.mcpServers[0].name, 'personal_agent');

    child.feed({
      jsonrpc: '2.0', type: 'response', factoryProtocolVersion: '1.264.0', id: init.id,
      result: {
        sessionId: 'dr-sess-1',
        settings: { modelId: 'gpt-6-sol', reasoningEffort: 'high' },
        availableModels: SAMPLE_MODELS,
      },
    });
    await startPromise;

    assert.equal(agent.resolvedModel, 'gpt-6-sol');
    const listReqPromise = adapter.listModels();
    const listReq = await waitForWrite(child, (m) => m.method === 'droid.list_models');
    child.feed({ jsonrpc: '2.0', type: 'response', id: listReq.id, result: { models: SAMPLE_MODELS } });
    const catalog = await listReqPromise;
    assert.deepEqual(catalog.map((m) => m.id), ['gpt-6-sol', 'auto']);

    // Validate effort guard on current model and model/effort update via adapter.
    assert.throws(() => adapter.selectEffort('off'), /Unsupported effort off/);
    adapter.applySettings({ model: 'auto', effort: 'none' });

    const submit1 = adapter.submit([{ text: 'hello', seq: 1, receiptId: 'dr-r1' }], { admission: 'claim-if-idle' });
    assert.deepEqual(submit1, { accepted: true, disposition: 'started' });

    const upd = await waitForWrite(child, (m) => m.method === 'droid.update_session_settings');
    assert.deepEqual(upd.params, { modelId: 'auto', reasoningEffort: 'none' });
    child.feed({ jsonrpc: '2.0', type: 'response', id: upd.id, result: {} });

    const msg1 = await waitForWrite(child, (m) => m.method === 'droid.add_user_message');
    assert.equal(msg1.params.queuePlacement, 'end_of_turn');
    child.feed({ jsonrpc: '2.0', type: 'response', id: msg1.id, result: {} });

    // Queue next message while turn 1 is live.
    const submit2 = adapter.submit([{ text: 'follow up', seq: 2, receiptId: 'dr-r2' }]);
    assert.deepEqual(submit2, { accepted: true, disposition: 'enqueued' });

    child.feed({
      jsonrpc: '2.0', type: 'notification', method: 'droid.session_notification',
      params: { sessionId: 'dr-sess-1', notification: { type: 'tool_call', toolUse: { type: 'tool_use', id: 't-1', name: 'send_message', input: {} } } },
    });
    child.feed({
      jsonrpc: '2.0', type: 'notification', method: 'droid.session_notification',
      params: { sessionId: 'dr-sess-1', notification: { type: 'tool_call', toolUse: { type: 'tool_use', id: 't-1', name: 'send_message', input: { text: 'hi' } } } },
    });
    child.feed({
      jsonrpc: '2.0', type: 'notification', method: 'droid.session_notification',
      params: { sessionId: 'dr-sess-1', notification: { type: 'tool_execution_phase_changed', toolUseId: 't-1', toolName: 'send_message', phase: 'executing' } },
    });
    child.feed({
      jsonrpc: '2.0', type: 'notification', method: 'droid.session_notification',
      params: { sessionId: 'dr-sess-1', notification: { type: 'tool_result', toolUseId: 't-1', content: 'ok', isError: false } },
    });
    child.feed({
      jsonrpc: '2.0', type: 'notification', method: 'droid.session_notification',
      params: { sessionId: 'dr-sess-1', notification: { type: 'assistant_text_delta', textDelta: 'ok' } },
    });
    child.feed({
      jsonrpc: '2.0', type: 'notification', method: 'droid.session_notification',
      params: { sessionId: 'dr-sess-1', notification: { type: 'agent_turn_completed' } },
    });

    const msg2 = await waitForWrite(child, (m) => m.method === 'droid.add_user_message' && m.id !== msg1.id);
    child.feed({ jsonrpc: '2.0', type: 'response', id: msg2.id, result: {} });
    assert.equal(adapter.cancelTurn(), true);
    const intr = await waitForWrite(child, (m) => m.method === 'droid.interrupt_session');
    child.feed({ jsonrpc: '2.0', type: 'response', id: intr.id, result: {} });
    child.feed({
      jsonrpc: '2.0', type: 'notification', method: 'droid.session_notification',
      params: { sessionId: 'dr-sess-1', notification: { type: 'droid_working_state_changed', newState: 'idle' } },
    });

    await adapter.whenIdle();
    assert.equal(successes.length, 1);
    assert.deepEqual(successes[0].receiptIds, ['dr-r1']);
    assert.equal(failures.length, 1);
    assert.match(failures[0].error, /cancelled/);
    assert.ok(store.activities.some((row) => row.kind === 'agent.tool_completed' && row.metadata.toolName === 'send_message'));
    await adapter.stop();
  });

  it('resumes an existing session with droid.load_session and restores catch-up on refused turn or auth failure', async (t) => {
    const store = makeStore();
    const { agent: first, spawns: s1, workspace } = makeFixture(t, { store });
    const policyHash = first.policyHash;
    store.setGlobalSession('dr-saved', policyHash, {
      runtime: 'droid', epoch: 1, syncedThroughSeq: 3, sessionReady: true,
    });

    const p = first.start();
    const { child: c1 } = await waitForSpawn(s1);
    const loadReq = await waitForWrite(c1, (m) => m.method === 'droid.load_session');
    assert.equal(loadReq.params.sessionId, 'dr-saved');
    assert.equal(loadReq.params.disableBuiltinSkills, true);
    c1.feed({
      jsonrpc: '2.0', type: 'response', id: loadReq.id,
      result: {
        session: { messages: [{ id: '1' }, { id: '2' }] },
        settings: { modelId: 'gpt-6-sol', reasoningEffort: 'medium' },
        availableModels: SAMPLE_MODELS,
      },
    });
    await p;
    assert.equal(first.needsHistoryPrefix, false);
    await first.stop();

    const failTurn = makeFixture(t, { store: makeStore(), config: { workspace } });
    const p2 = failTurn.agent.start();
    const { child: c2 } = await waitForSpawn(failTurn.spawns);
    const init2 = await waitForWrite(c2, (m) => m.method === 'droid.initialize_session');
    c2.feed({
      jsonrpc: '2.0', type: 'response', id: init2.id,
      result: {
        sessionId: 'dr-2',
        settings: { modelId: 'gpt-6-sol', reasoningEffort: 'medium' },
        availableModels: SAMPLE_MODELS,
      },
    });
    await p2;
    failTurn.agent.deliver('trigger', 1, 'r-fail');
    const msg = await waitForWrite(c2, (m) => m.method === 'droid.add_user_message');
    c2.feed({
      jsonrpc: '2.0', type: 'response', id: msg.id,
      error: { code: -32000, message: 'Authentication failed' },
    });
    await failTurn.agent.whenIdle();
    assert.equal(failTurn.agent.needsHistoryPrefix, true);
    await failTurn.agent.stop();

    const unauth = makeFixture(t);
    const p3 = unauth.agent.start();
    const { child: c3 } = await waitForSpawn(unauth.spawns);
    const init3 = await waitForWrite(c3, (m) => m.method === 'droid.initialize_session');
    c3.feed({
      jsonrpc: '2.0', type: 'response', id: init3.id,
      error: { code: -32000, message: 'Authentication failed' },
    });
    await assert.rejects(() => p3, { code: DROID_NOT_LOGGED_IN });
  });
});
