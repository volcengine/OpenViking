// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { describe, it } from 'node:test';
import { CopilotAgent } from '../src/copilot.mjs';
import { COPILOT_NOT_LOGGED_IN } from '../src/copilot-runtime.mjs';
import { RuntimeAdapter } from '../src/runtime-adapter.mjs';

const quietLogger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

class FakeChild extends EventEmitter {
  constructor() {
    super();
    this.pid = 777_001;
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
    getCognitionState: () => ({ runtime: 'copilot', epoch: 1, authorityToken: 'cop-token' }),
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
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-copilot-test-'));
  const home = path.join(dir, 'user-home');
  const workspace = path.join(dir, 'workspace');
  const dataDir = path.join(dir, 'data');
  fs.mkdirSync(path.join(home, '.copilot'), { recursive: true });
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
    copilotBin: 'copilot',
    copilotModel: '',
    copilotEffort: 'medium',
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

  const agent = new CopilotAgent(cfg, store, quietLogger, {
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

describe('CopilotAgent ACP backend', () => {
  it('isolates COPILOT_HOME, injects copilot-instructions.md & --additional-mcp-config, and applies dynamic effort', async (t) => {
    const { agent, spawns, dataDir, home } = makeFixture(t, {
      config: { copilotModel: 'gpt-5', copilotEffort: 'high' },
    });
    fs.writeFileSync(path.join(home, '.copilot', 'mcp-config.json'), JSON.stringify({
      mcpServers: { openviking: { command: 'ov' }, 'user-mcp': { command: 'user' } },
    }));

    const startPromise = agent.start();
    const { child, args, options } = await waitForSpawn(spawns);
    assert.ok(args.includes('--acp') && args.includes('--stdio'));
    assert.ok(args.includes('--disable-builtin-mcps') && args.includes('--allow-all') && args.includes('--no-ask-user'));
    const mcpFlagIdx = args.indexOf('--additional-mcp-config');
    assert.ok(mcpFlagIdx >= 0 && args[mcpFlagIdx + 1].startsWith('@'));
    const mcpFile = args[mcpFlagIdx + 1].slice(1);
    const mcpConfig = JSON.parse(fs.readFileSync(mcpFile, 'utf8'));
    assert.ok(mcpConfig.mcpServers.personal_agent);

    assert.equal(options.env.COPILOT_HOME, path.join(dataDir, 'copilot', 'home'));
    assert.equal(options.env.COPILOT_DISABLE_LOGIN_SHELL_ENV, '1');
    assert.ok(fs.existsSync(path.join(options.env.COPILOT_HOME, 'copilot-instructions.md')));
    const sanitizedMcp = JSON.parse(fs.readFileSync(path.join(options.env.COPILOT_HOME, 'mcp-config.json'), 'utf8'));
    assert.deepEqual(Object.keys(sanitizedMcp.mcpServers), ['user-mcp']);

    const init = await waitForWrite(child, (m) => m.method === 'initialize');
    child.feed({
      jsonrpc: '2.0', id: init.id,
      result: { protocolVersion: 1, agentCapabilities: { loadSession: true }, agentInfo: { name: 'Copilot', version: '1.0.114' } },
    });

    const created = await waitForWrite(child, (m) => m.method === 'session/new');
    assert.deepEqual(created.params.mcpServers, []);
    child.feed({
      jsonrpc: '2.0', id: created.id,
      result: {
        sessionId: 'cop-sess-1',
        configOptions: [
          {
            id: 'model', category: 'model', currentValue: 'claude-sonnet-4.6',
            options: [{ value: 'claude-sonnet-4.6', name: 'Sonnet 4.6' }, { value: 'gpt-5', name: 'GPT-5' }],
          },
          {
            id: 'reasoning_effort', category: 'thought_level', currentValue: 'medium',
            options: [{ value: 'low' }, { value: 'medium' }, { value: 'high' }],
          },
        ],
      },
    });

    const setModel = await waitForWrite(child, (m) => m.method === 'session/set_model');
    assert.equal(setModel.params.modelId, 'gpt-5');
    child.feed({
      jsonrpc: '2.0', id: setModel.id,
      result: {
        configOptions: [
          { id: 'model', category: 'model', currentValue: 'gpt-5', options: [{ value: 'gpt-5' }] },
          { id: 'reasoning_effort', category: 'thought_level', currentValue: 'medium', options: [{ value: 'low' }, { value: 'high' }] },
        ],
      },
    });

    const setEffort = await waitForWrite(child, (m) => m.method === 'session/set_config_option');
    assert.deepEqual(setEffort.params, { sessionId: 'cop-sess-1', configId: 'reasoning_effort', value: 'high' });
    child.feed({ jsonrpc: '2.0', id: setEffort.id, result: {} });

    await startPromise;
    assert.equal(agent.resolvedModel, 'gpt-5');
    assert.equal(agent.effort, 'high');
    assert.deepEqual((await agent.listModels()).find((m) => m.id === 'gpt-5').efforts, ['low', 'high']);

    await agent.stop();
  });

  it('skips effort RPC cleanly for non-reasoning models on startup and rejects invalid setEffort synchronously', async (t) => {
    const { agent, spawns } = makeFixture(t, {
      config: { copilotModel: 'claude-haiku-4.5', copilotEffort: 'medium' },
    });
    const startPromise = agent.start();
    const { child } = await waitForSpawn(spawns);
    const init = await waitForWrite(child, (m) => m.method === 'initialize');
    child.feed({ jsonrpc: '2.0', id: init.id, result: { agentInfo: { name: 'Copilot' } } });
    const created = await waitForWrite(child, (m) => m.method === 'session/new');
    child.feed({
      jsonrpc: '2.0', id: created.id,
      result: {
        sessionId: 'cop-haiku',
        configOptions: [
          { id: 'model', currentValue: 'claude-haiku-4.5', options: [{ value: 'claude-haiku-4.5' }] },
        ],
      },
    });
    const setModel = await waitForWrite(child, (m) => m.method === 'session/set_model');
    child.feed({ jsonrpc: '2.0', id: setModel.id, result: {} });
    await startPromise;

    assert.equal(parsedWrites(child).some((m) => m.method === 'session/set_config_option'), false);
    assert.throws(() => agent.setEffort('high'), /does not support/);
    await agent.stop();
  });

  it('learns non-reasoning effort limits gracefully when set_model returns empty configOptions', async (t) => {
    const { agent, spawns } = makeFixture(t, {
      config: { copilotModel: 'claude-haiku-4.5', copilotEffort: 'medium' },
    });
    const startPromise = agent.start();
    const { child } = await waitForSpawn(spawns);
    const init = await waitForWrite(child, (m) => m.method === 'initialize');
    child.feed({ jsonrpc: '2.0', id: init.id, result: { agentInfo: { name: 'Copilot' } } });
    const created = await waitForWrite(child, (m) => m.method === 'session/new');
    child.feed({
      jsonrpc: '2.0', id: created.id,
      result: {
        sessionId: 'cop-switch',
        configOptions: [
          { id: 'model', currentValue: 'claude-sonnet-4.6', options: [{ value: 'claude-sonnet-4.6' }, { value: 'claude-haiku-4.5' }] },
          { id: 'reasoning_effort', currentValue: 'medium', options: [{ value: 'medium' }, { value: 'high' }] },
        ],
      },
    });
    const setModel = await waitForWrite(child, (m) => m.method === 'session/set_model');
    child.feed({ jsonrpc: '2.0', id: setModel.id, result: {} });
    const setEffort = await waitForWrite(child, (m) => m.method === 'session/set_config_option');
    child.feed({
      jsonrpc: '2.0', id: setEffort.id,
      error: { code: -32602, message: 'This model does not support reasoning_effort configuration.' },
    });
    await startPromise;
    const entry = (await agent.listModels()).find((m) => m.id === 'claude-haiku-4.5');
    assert.equal(entry.effortsKnown, true);
    assert.deepEqual(entry.efforts, []);
    assert.throws(() => agent.setEffort('high'), /does not support/);
    await agent.stop();
  });

  it('treats unauthenticated session/new or session/load as fatal COPILOT_NOT_LOGGED_IN', async (t) => {
    const store = makeStore();
    const first = makeFixture(t, { store });
    const p = first.agent.start();
    const { child } = await waitForSpawn(first.spawns);
    const init = await waitForWrite(child, (m) => m.method === 'initialize');
    child.feed({ jsonrpc: '2.0', id: init.id, result: { agentInfo: { name: 'Copilot' } } });
    const created = await waitForWrite(child, (m) => m.method === 'session/new');
    child.feed({
      jsonrpc: '2.0', id: created.id,
      error: { code: -32000, message: 'Authentication required' },
    });
    await assert.rejects(() => p, { code: COPILOT_NOT_LOGGED_IN });
  });

  it('executes queued turns, streams tool/note activities, cancels via session/cancel, and supports RuntimeAdapter', async (t) => {
    const successes = [];
    const failures = [];
    const { agent, store, spawns } = makeFixture(t, {
      options: {
        onTurnSucceeded: (info) => successes.push(info),
        onTurnFailed: (info) => failures.push(info),
      },
    });
    const adapter = new RuntimeAdapter('copilot', agent);
    const startPromise = adapter.start();
    const { child } = await waitForSpawn(spawns);
    const init = await waitForWrite(child, (m) => m.method === 'initialize');
    child.feed({ jsonrpc: '2.0', id: init.id, result: { agentInfo: { name: 'Copilot' } } });
    const created = await waitForWrite(child, (m) => m.method === 'session/new');
    child.feed({
      jsonrpc: '2.0', id: created.id,
      result: {
        sessionId: 'cop-turn-1',
        configOptions: [
          { id: 'model', currentValue: 'claude-sonnet-4.6', options: [{ value: 'claude-sonnet-4.6' }] },
        ],
      },
    });
    await startPromise;

    const r1 = adapter.submit([{ text: 'first', seq: 1, receiptId: 'r-1' }], { admission: 'claim-if-idle' });
    assert.deepEqual(r1, { accepted: true, disposition: 'started' });
    const p1 = await waitForWrite(child, (m) => m.method === 'session/prompt');

    // Queued while turn 1 runs.
    const r2 = adapter.submit([{ text: 'second', seq: 2, receiptId: 'r-2' }]);
    assert.deepEqual(r2, { accepted: true, disposition: 'enqueued' });

    child.feed({
      jsonrpc: '2.0', method: 'session/update',
      params: { sessionId: 'cop-turn-1', update: { sessionUpdate: 'tool_call', toolCallId: 'tc-1', status: 'in_progress', name: 'send_message', rawInput: { chat_id: 'oc_1' } } },
    });
    child.feed({
      jsonrpc: '2.0', method: 'session/update',
      params: { sessionId: 'cop-turn-1', update: { sessionUpdate: 'tool_call_update', toolCallId: 'tc-1', status: 'completed', rawOutput: { ok: true } } },
    });
    child.feed({
      jsonrpc: '2.0', method: 'session/update',
      params: { sessionId: 'cop-turn-1', update: { sessionUpdate: 'agent_message_chunk', content: { text: 'done' } } },
    });
    child.feed({ jsonrpc: '2.0', id: p1.id, result: { stopReason: 'end_turn' } });

    const p2 = await waitForWrite(child, (m) => m.method === 'session/prompt' && m.id !== p1.id);
    assert.match(p2.params.prompt[0].text, /second/);
    assert.equal(adapter.cancelTurn(), true);
    const cancel = await waitForWrite(child, (m) => m.method === 'session/cancel');
    assert.equal(cancel.params.sessionId, 'cop-turn-1');
    child.feed({ jsonrpc: '2.0', id: p2.id, result: { stopReason: 'cancelled' } });

    await adapter.whenIdle();
    assert.equal(successes.length, 1);
    assert.deepEqual(successes[0].receiptIds, ['r-1']);
    assert.equal(failures.length, 1);
    assert.match(failures[0].error, /cancelled/);
    assert.ok(store.activities.some((row) => row.kind === 'agent.tool_called' && row.metadata.toolName === 'send_message'));
    await adapter.stop();
  });
});
