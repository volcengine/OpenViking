// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, it } from 'node:test';
import { EventEmitter } from 'node:events';
import { AgentRuntime } from '../src/agent-runtime.mjs';
import { OpencodeAgent } from '../src/opencode.mjs';
import { AgyAgent } from '../src/agy.mjs';
import { resolveRuntimeModelTarget as parseRuntimeModelTarget } from '../src/runtime-catalog.mjs';
import { adaptRuntime } from '../src/runtime-adapter.mjs';
import { ActionService } from '../src/actions/service.mjs';
import { fakeConnectors } from './action-fixture.mjs';
import { chatEvent } from './event-fixture.mjs';
import { Store } from '../src/store.mjs';
import { OVSessionContext } from '../src/ov-session-context.mjs';
import { cognitionHistoryPrefix } from '../src/cognition.mjs';
import { runClaudeRuntimeSimulation } from '../simulator/claude-runtime-simulator.mjs';

const quietLogger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

function backend(runtime, options = {}) {
  return {
    runtime,
    model: options.model ?? options.initialModel ?? '',
    effort: 'medium',
    threadId: `${runtime}-session`,
    state: { connected: false, activeTurn: 0, queued: 0 },
    turnInProgress: false,
    get sessionDescriptor() {
      return {
        runtime,
        sessionId: this.threadId,
        policyHash: `${runtime}-policy`,
        epoch: options.cognitionEpoch,
        syncedThroughSeq: 0,
        sessionReady: runtime === 'codex',
      };
    },
    async start() { this.state.connected = true; },
    async stop() { this.state.connected = false; },
    async whenIdle() {},
    activateSession() {},
    async listModels() { return [{ id: this.model || 'gpt-5.6-sol', isDefault: true, efforts: ['medium', 'high'] }]; },
    async prepareSessionRotation() {
      this.preparedSession = { id: `${runtime}-fresh`, sessionReady: runtime !== 'claude' };
      return this.preparedSession;
    },
    commitSessionRotation(prepared) {
      this.threadId = prepared.id;
      this.preparedSession = null;
      return this.threadId;
    },
    cancelSessionRotation() { this.preparedSession = null; },
  };
}

// An `opencode acp` child that answers the startup handshake by itself, with
// the session's own default model and no daemon-side model configured.
function autoAnsweringOpencode(spawns, { defaultModel = 'volcengine-ark/ep-default' } = {}) {
  return () => {
    const child = new EventEmitter();
    child.pid = 987_654_400 + spawns.length;
    child.exitCode = null;
    child.stdout = new EventEmitter();
    child.stdout.setEncoding = () => {};
    child.stderr = new EventEmitter();
    child.stderr.setEncoding = () => {};
    child.stdin = new EventEmitter();
    child.stdin.writable = true;
    child.stdin.end = () => {};
    child.kill = (signal) => {
      if (child.exitCode !== null) return;
      child.exitCode = 0;
      child.emit('exit', 0, signal);
    };
    child.requests = [];
    const send = (frame) => setImmediate(() => child.stdout.emit('data', `${JSON.stringify({ jsonrpc: '2.0', ...frame })}\n`));
    const answer = (id, result) => send({ id, result });
    const models = [defaultModel, 'volcengine-ark/ep-other'];
    child.stdin.write = (line) => {
      const message = JSON.parse(line);
      if (message.method) child.requests.push(message);
      if (message.method === 'initialize') {
        answer(message.id, { protocolVersion: 1, agentInfo: { name: 'OpenCode', version: '1.19.5' } });
      } else if (message.method === 'session/new') {
        answer(message.id, {
          sessionId: 'ses_switch',
          configOptions: [{ id: 'model', type: 'select', currentValue: defaultModel, options: [
            { value: defaultModel, name: 'Default' },
            { value: 'volcengine-ark/ep-other', name: 'Other' },
          ] }],
        });
      } else if (message.method === 'session/set_config_option') {
        if (models.includes(message.params.value)) answer(message.id, {});
        // What opencode 1.19.5 answers for a model it does not know.
        else send({ id: message.id, error: { code: -32602, message: `Invalid params: model not found: ${message.params.value}` } });
      }
      return true;
    };
    spawns.push(child);
    return child;
  };
}

// An `agy acp` child that answers its own handshake, with agy's model calls.
function autoAnsweringAgy(spawns, { defaultModel = 'gemini-3.8-flash-low' } = {}) {
  return () => {
    const child = new EventEmitter();
    child.pid = 987_654_500 + spawns.length;
    child.exitCode = null;
    child.stdout = new EventEmitter();
    child.stdout.setEncoding = () => {};
    child.stderr = new EventEmitter();
    child.stderr.setEncoding = () => {};
    child.stdin = new EventEmitter();
    child.stdin.writable = true;
    child.stdin.end = () => {};
    child.kill = (signal) => {
      if (child.exitCode !== null) return;
      child.exitCode = 0;
      child.emit('exit', 0, signal);
    };
    child.requests = [];
    const send = (frame) => setImmediate(() => child.stdout.emit('data', `${JSON.stringify({ jsonrpc: '2.0', ...frame })}\n`));
    const answer = (id, result) => send({ id, result });
    const models = [defaultModel, 'gemini-3.8-flash-high'];
    child.stdin.write = (line) => {
      const message = JSON.parse(line);
      if (message.method) child.requests.push(message);
      if (message.method === 'initialize') {
        answer(message.id, {
          protocolVersion: 1,
          agentInfo: { name: 'Antigravity ACP Server', version: '1.5.3' },
          agentCapabilities: { _meta: { antigravity: { systemPrompt: { append: true } } } },
        });
      } else if (message.method === 'session/new') {
        answer(message.id, {
          sessionId: 'agy_switch',
          models: { availableModels: models.map((modelId) => ({ modelId, name: modelId })), currentModelId: defaultModel },
        });
      } else if (message.method === 'session/set_model') {
        if (models.includes(message.params.modelId)) answer(message.id, {});
        else send({ id: message.id, error: { code: -32602, message: `Unknown model ID: ${message.params.modelId}` } });
      }
      return true;
    };
    spawns.push(child);
    return child;
  };
}

function agySwitchRuntime(directory, store, spawns) {
  const config = {
    agyBin: 'agy', agyModel: '', workspace: directory, dataDir: directory,
    profile: 'test-profile', connectors: ['lark'], actionSocketPath: path.join(directory, 'actions.sock'),
    actionMcpPath: '/release/packages/daemon/src/actions/mcp.mjs', sendTimeoutMs: 5_000, maxReplyChars: 1_000,
  };
  return new AgentRuntime(config, store, quietLogger, {
    codexFactory: (options) => backend('codex', options),
    agyFactory: (options) => new AgyAgent(config, store, quietLogger, {
      ...options, spawn: autoAnsweringAgy(spawns), startStabilityMs: 1,
    }),
  });
}

// A real OpencodeAgent behind AgentRuntime, starting from a fake Codex.
function opencodeSwitchRuntime(directory, store, spawns) {
  const config = {
    opencodeBin: 'opencode', opencodeModel: '', workspace: directory, dataDir: directory,
    profile: 'test-profile', connectors: ['lark'], actionSocketPath: path.join(directory, 'actions.sock'),
    actionMcpPath: '/release/packages/daemon/src/actions/mcp.mjs', sendTimeoutMs: 5_000, maxReplyChars: 1_000,
  };
  return new AgentRuntime(config, store, quietLogger, {
    codexFactory: (options) => backend('codex', options),
    opencodeFactory: (options) => new OpencodeAgent(config, store, quietLogger, {
      ...options, spawn: autoAnsweringOpencode(spawns), startStabilityMs: 1,
    }),
  });
}

describe('global Codex/Claude hard switch', () => {
  it('keeps catalogs scoped and validates max/ultra using the active default model', async () => {
    const runtime = new AgentRuntime({}, {}, quietLogger);
    const implementation = backend('codex');
    implementation.resolvedModel = 'gpt-active';
    implementation.listModels = async () => [
      { id: 'gpt-active', efforts: ['medium', 'max', 'ultra'] },
      { id: 'gpt-other', efforts: ['minimal'] },
    ];
    implementation.setEffort = (effort) => { implementation.effort = effort; };
    runtime.backend = adaptRuntime('codex', implementation);
    const snapshot = await runtime.snapshot({ catalog: 'refresh' });
    assert.deepEqual(snapshot.models.map((entry) => entry.id), ['gpt-active', 'gpt-other']);
    for (const effort of ['max', 'ultra']) {
      assert.equal((await runtime.transition({ kind: 'select-effort', effort })).ok, true);
    }
    for (const effort of ['managed', 'minimal']) {
      assert.equal((await runtime.transition({ kind: 'select-effort', effort })).ok, false);
    }
    assert.equal(runtime.effort, 'ultra');
  });

  it('rejects cross-runtime model intents and makes selecting the current runtime a no-op', async () => {
    const runtime = new AgentRuntime({}, {}, quietLogger);
    const implementation = backend('codex');
    implementation.interruptTurn = () => assert.fail('must not interrupt');
    runtime.backend = adaptRuntime('codex', implementation);
    for (const selector of ['claude:sonnet', 'pi', 'sonnet']) {
      await assert.rejects(runtime.transition({ kind: 'select-model', selector }), /runtime|unavailable/);
    }
    const before = runtime.backend;
    const result = await runtime.transition({ kind: 'select-runtime', runtime: 'codex' });
    assert.equal(result.unchanged, true);
    assert.equal(runtime.backend, before);
    assert.equal(runtime.threadId, 'codex-session');
  });

  it('retires stale sessions, fences authority, and rolls back target-start failure', async () => {
    const result = await runClaudeRuntimeSimulation();
    assert.equal(result.ok, true);
    assert.deepEqual(result.switches, ['claude', 'codex']);
    assert.equal(result.oldAuthorityRevoked, true);
    assert.equal(result.staleCodexReused, false);
    assert.equal(result.failurePreservedOldRuntime, true);
  });

  it('infers direct model providers while retaining hidden qualified aliases', () => {
    assert.deepEqual(parseRuntimeModelTarget('sonnet'), {
      runtime: 'claude', model: 'sonnet', canonical: 'sonnet',
    });
    assert.deepEqual(parseRuntimeModelTarget('gpt-5.6-sol'), {
      runtime: 'codex', model: 'gpt-5.6-sol', canonical: 'gpt-5.6-sol',
    });
    assert.deepEqual(parseRuntimeModelTarget('claude'), {
      runtime: 'codex', model: 'claude', canonical: 'claude',
    });
    assert.deepEqual(parseRuntimeModelTarget('codex'), {
      runtime: 'codex', model: 'codex', canonical: 'codex',
    });
    assert.deepEqual(parseRuntimeModelTarget('claude:sonnet'), {
      runtime: 'claude', model: 'sonnet', canonical: 'sonnet',
    });
    assert.deepEqual(parseRuntimeModelTarget('codex:gpt-5.6-sol'), {
      runtime: 'codex', model: 'gpt-5.6-sol', canonical: 'gpt-5.6-sol',
    });
    assert.deepEqual(parseRuntimeModelTarget('traex'), {
      runtime: 'traex', model: '', canonical: 'traex',
    });
    assert.deepEqual(parseRuntimeModelTarget('trae:gpt-5.6-sol'), {
      runtime: 'traex', model: 'gpt-5.6-sol', canonical: 'gpt-5.6-sol',
    });
    assert.deepEqual(parseRuntimeModelTarget('ark'), {
      runtime: 'volc', model: 'ark', canonical: 'ark',
    });
    assert.deepEqual(parseRuntimeModelTarget('volcengine'), {
      runtime: 'volc', model: 'ark', canonical: 'ark',
    });
    assert.deepEqual(parseRuntimeModelTarget('pi'), {
      runtime: 'pi', model: '', canonical: 'pi',
    });
    assert.deepEqual(parseRuntimeModelTarget('pi:openai/gpt-4o'), {
      runtime: 'pi', model: 'openai/gpt-4o', canonical: 'openai/gpt-4o',
    });
  });

  it('stays on Pi for qualified and unprefixed live-list model selectors', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-runtime-pi-select-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    store.initializeCognition('pi', 'pi-authority');
    let instance;
    const selected = [];
    const runtime = new AgentRuntime({}, store, quietLogger, {
      codexFactory: (options) => {
        selected.push('codex');
        return backend('codex', options);
      },
      piFactory: (options) => {
        selected.push('pi');
        instance = backend('pi', options);
        instance.model = options.model || 'google/gemini-pro';
        instance.listModels = async () => [{ id: 'google/gemini-pro', efforts: ['medium'] }, { id: 'openai/gpt-4o', efforts: ['medium'] }];
        instance.calls = [];
        instance.interruptTurn = async function interruptTurn() {
          this.calls.push('interrupt');
        };
        instance.setModel = async function setModel(model) {
          this.calls.push(`setModel:${model}`);
          this.model = model;
        };
        instance.prepareSessionRotation = async function prepareSessionRotation() {
          this.calls.push('prepare');
          return { id: 'pi-fresh', sessionReady: true };
        };
        instance.commitSessionRotation = function commitSessionRotation(prepared, options) {
          this.calls.push(`commit:${options.persist}`);
          this.threadId = prepared.id;
          return this.threadId;
        };
        return instance;
      },
    });

    try {
      await runtime.start();
      assert.equal(runtime.runtime, 'pi');

      const qualified = await runtime.transition({
        kind: 'select-model', selector: 'pi:openai/gpt-4o',
      });
      assert.equal(qualified.runtimeChanged, false);
      assert.equal(qualified.runtime, 'pi');
      assert.equal(qualified.model, 'openai/gpt-4o');
      assert.equal(runtime.runtime, 'pi');
      assert.equal(runtime.model, 'openai/gpt-4o');
      assert.equal(store.getCognitionState().runtime, 'pi');

      const live = await runtime.transition({
        kind: 'select-model', selector: 'openai/gpt-4o',
      });
      assert.equal(live.runtimeChanged, false);
      assert.equal(live.runtime, 'pi');
      assert.equal(runtime.runtime, 'pi');
      assert.equal(selected.filter((id) => id === 'codex').length, 0);
      assert.equal(instance.model, 'openai/gpt-4o');
      assert.equal(store.getSetting('pi_model'), 'openai/gpt-4o');
    } finally {
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('starts Pi rather than Codex on a fresh database configured with model pi', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-runtime-fresh-pi-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    const selected = [];
    const runtime = new AgentRuntime({
      initialModel: 'pi',
    }, store, quietLogger, {
      codexFactory: () => {
        selected.push('codex');
        return backend('codex');
      },
      piFactory: (options) => {
        selected.push('pi');
        return backend('pi', options);
      },
    });
    try {
      assert.equal(store.hasCognitionRuntime(), false);
      await runtime.start();
      assert.deepEqual(selected, ['pi']);
      assert.equal(runtime.runtime, 'pi');
      assert.equal(store.getCognitionState().runtime, 'pi');
    } finally {
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('starts TraeX rather than Codex on a fresh database configured with model traex', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-runtime-fresh-traex-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    const selected = [];
    const runtime = new AgentRuntime({
      initialModel: 'traex',
    }, store, quietLogger, {
      codexFactory: () => {
        selected.push('codex');
        return backend('codex');
      },
      traexFactory: (options) => {
        selected.push('traex');
        return backend('traex', options);
      },
    });
    try {
      assert.equal(store.hasCognitionRuntime(), false);
      await runtime.start();
      assert.deepEqual(selected, ['traex']);
      assert.equal(runtime.runtime, 'traex');
      assert.equal(store.getCognitionState().runtime, 'traex');
    } finally {
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('passes a qualified TraeX initial model to the fresh backend', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-runtime-fresh-traex-model-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    let initialOptions;
    const runtime = new AgentRuntime({
      initialModel: 'trae:gpt-5.6-sol',
    }, store, quietLogger, {
      codexFactory: () => backend('codex'),
      traexFactory: (options) => {
        initialOptions = options;
        return backend('traex', options);
      },
    });
    try {
      assert.equal(store.hasCognitionRuntime(), false);
      await runtime.start();
      assert.equal(runtime.runtime, 'traex');
      assert.equal(runtime.model, 'gpt-5.6-sol');
      assert.equal(initialOptions.initialModel, 'gpt-5.6-sol');
      assert.equal(store.getCognitionState().runtime, 'traex');
    } finally {
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('starts Ark rather than Codex on a fresh database configured with model ark', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-runtime-fresh-ark-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    const selected = [];
    const runtime = new AgentRuntime({
      connectors: ['lark'],
      initialModel: 'ark',
    }, store, quietLogger, {
      codexFactory: () => {
        selected.push('codex');
        return backend('codex');
      },
      volcFactory: (options) => {
        selected.push('volc');
        return backend('volc', options);
      },
    });
    try {
      assert.equal(store.hasCognitionRuntime(), false);
      await runtime.start();
      assert.deepEqual(selected, ['volc']);
      assert.equal(runtime.runtime, 'volc');
      assert.equal(store.getCognitionState().runtime, 'volc');
      assert.equal(store.hasCognitionRuntime(), true);
    } finally {
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('reconciles a setup runtime change from config into an existing database', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-runtime-config-reconcile-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    store.initializeCognition('codex', 'token-before-setup');
    const selected = [];
    const persisted = [];
    const runtime = new AgentRuntime({
      initialModel: 'pi',
    }, store, quietLogger, {
      persistRuntimePreference: (target) => persisted.push(target),
      codexFactory: () => {
        selected.push('codex');
        return backend('codex');
      },
      piFactory: (options) => {
        selected.push('pi');
        return backend('pi', options);
      },
    });
    try {
      await runtime.start();
      assert.deepEqual(selected, ['pi']);
      assert.deepEqual(persisted, [], 'config already contains the desired runtime');
      assert.equal(runtime.runtime, 'pi');
      assert.equal(store.getCognitionState().runtime, 'pi');
      assert.notEqual(store.getCognitionState().authorityToken, 'token-before-setup');
      assert.equal(store.getGlobalSession().runtime, 'pi');
    } finally {
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('persists a live runtime choice before committing its SQLite authority', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-runtime-persist-choice-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    store.initializeCognition('codex', 'old-token');
    const order = [];
    const runtime = new AgentRuntime({}, store, quietLogger, {
      persistRuntimePreference: async (target) => {
        assert.equal(target, 'pi');
        assert.equal(store.getCognitionState().runtime, 'codex');
        assert.equal(runtime.runtime, 'codex');
        order.push('config');
      },
      codexFactory: (options) => backend('codex', options),
      piFactory: (options) => backend('pi', options),
    });
    try {
      await runtime.start();
      const result = await runtime.transition({ kind: 'select-runtime', runtime: 'pi' });
      order.push('sqlite');
      assert.equal(result.runtimeChanged, true);
      assert.deepEqual(order, ['config', 'sqlite']);
      assert.equal(runtime.runtime, 'pi');
      assert.equal(store.getCognitionState().runtime, 'pi');
    } finally {
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('keeps the old runtime authoritative when persisting a live choice fails', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-runtime-persist-failure-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    store.initializeCognition('codex', 'old-token');
    let staged;
    const runtime = new AgentRuntime({}, store, quietLogger, {
      persistRuntimePreference: () => { throw new Error('manifest write failed'); },
      codexFactory: (options) => backend('codex', options),
      piFactory: (options) => {
        staged = backend('pi', options);
        return staged;
      },
    });
    try {
      await runtime.start();
      const before = store.getCognitionState();
      await assert.rejects(
        runtime.transition({ kind: 'select-runtime', runtime: 'pi' }),
        /manifest write failed/,
      );
      assert.equal(runtime.runtime, 'codex');
      assert.deepEqual(store.getCognitionState(), before);
      assert.equal(staged.state.connected, false);
    } finally {
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('reports the root model idle while only an external tool or subagent is running', () => {
    const runtime = new AgentRuntime({}, {}, quietLogger);
    runtime.backend = backend('codex');
    runtime.backend.turnInProgress = true;
    runtime.backend.modelWorking = false;
    runtime.backend.modelStartedAtMs = null;
    assert.equal(runtime.workStatus.working, false);

    runtime.backend.modelWorking = true;
    runtime.backend.modelStartedAtMs = 5678;
    runtime.backend.turnStartedAtMs = 1234;
    assert.deepEqual(runtime.workStatus, {
      working: true,
      startedAtMs: 1234,
      connected: false,
      queued: 0,
      switching: false,
    });
  });

  it('forwards opaque delivery receipts without requiring an inbound sequence', () => {
    const runtime = new AgentRuntime({}, {}, quietLogger);
    const calls = [];
    runtime.backend = adaptRuntime('codex', {
      deliverBatch(items) { calls.push(['deliverBatch', items]); return true; },
    });

    assert.equal(runtime.submit([{ text: 'normal inbound', seq: 7 }]).accepted, true);
    assert.equal(runtime.submit([{
      text: 'meeting audio', seq: null, receiptId: 'receipt-1',
    }]).accepted, true);
    assert.equal(runtime.submit([
      { text: 'next segment', receiptId: 'receipt-2' },
    ]).accepted, true);
    assert.deepEqual(calls, [
      ['deliverBatch', [{ text: 'normal inbound', seq: 7 }]],
      ['deliverBatch', [{ text: 'meeting audio', seq: null, receiptId: 'receipt-1' }]],
      ['deliverBatch', [{ text: 'next segment', receiptId: 'receipt-2' }]],
    ]);
  });

  it('follows the last document comment of a batch with the rules for answering it, and nothing else', () => {
    const runtime = new AgentRuntime({ connectors: ['lark'] }, {}, quietLogger);
    const calls = [];
    runtime.backend = adaptRuntime('codex', {
      deliverBatch(items) { calls.push(items); return true; },
    });
    const chat = { text: '[chat=oc_a] hi', seq: 1, chatType: 'group' };
    runtime.submit([chat]);
    runtime.submit([
      { text: '[chat=doc:docx:A] first', seq: 2, chatType: 'doc_comment' },
      chat,
      { text: '[chat=doc:docx:A] second', seq: 3, chatType: 'doc_comment', receiptId: 'r-3' },
    ]);
    assert.deepEqual(calls[0], [chat], 'a chat batch is passed through untouched');
    const [first, again, second] = calls[1];
    assert.equal(first.text, '[chat=doc:docx:A] first');
    assert.equal(again, chat);
    assert.equal(second.receiptId, 'r-3');
    assert.match(second.text, /^\[chat=doc:docx:A\] second\n\[daemon notice\] How to answer the document comment above\. /);
    assert.equal(second.text.split('\n').length, 2);
  });

  it('publishes an immutable normalized snapshot', async () => {
    const runtime = new AgentRuntime({}, {}, quietLogger);
    const implementation = backend('claude');
    implementation.model = 'sonnet';
    implementation.effort = 'high';
    implementation.state = { connected: true, activeTurn: 1, queued: 2 };
    implementation.turnInProgress = true;
    implementation.modelWorking = true;
    implementation.turnStartedAtMs = 1234;
    runtime.runtime = 'claude';
    runtime.backend = adaptRuntime('claude', implementation);

    const snapshot = await runtime.snapshot();
    assert.equal(snapshot.runtime, 'claude');
    assert.equal(snapshot.phase, 'working');
    assert.equal(snapshot.session.id, 'claude-session');
    assert.equal(snapshot.capabilities.activeInput, 'steer');
    assert.equal(snapshot.queued, 2);
    assert.equal(snapshot.work.turnInProgress, true);
    assert.equal(Object.isFrozen(snapshot), true);
    assert.equal(Object.isFrozen(snapshot.session), true);
    assert.equal(Object.isFrozen(snapshot.work), true);
    await assert.rejects(async () => { snapshot.session.id = 'mutated'; }, /read only|Cannot assign/);
  });

  it('rejects targetless or mixed managed-agent scope before ownership transfers', async () => {
    const runtime = new AgentRuntime({}, {}, quietLogger);
    const calls = [];
    runtime.runtime = 'volc';
    runtime.backend = adaptRuntime('volc', {
      deliverBatch(items) { calls.push(items); return true; },
    });

    assert.deepEqual(runtime.submit([{ text: 'targetless', receiptId: 'meeting-1' }]), {
      accepted: false,
      reason: 'invalid-scope',
    });
    assert.equal(runtime.submit([
      { text: 'a', conversationKey: 'chat-a' },
      { text: 'b', conversationKey: 'chat-b' },
    ]).accepted, false);
    assert.equal(runtime.submit([
      { text: 'a', scope: { conversationKey: 'chat-a' } },
      { text: 'b', scope: { conversationKey: 'chat-a' } },
    ]).accepted, true);
    assert.equal(calls.length, 1);

    assert.equal(runtime.submit([
      { text: 'issue opened', scope: { kind: 'github', key: '["github","issue","owner/repo#123"]' } },
      { text: 'issue labeled', scopeKey: '["github","issue","owner/repo#123"]' },
    ]).accepted, true);
    assert.equal(calls.length, 2);

    const effort = await runtime.transition({ kind: 'select-effort', effort: 'high' });
    assert.deepEqual(effort, {
      ok: false,
      kind: 'select-effort',
      reason: 'remote-managed',
    });
  });

  it('live-validates a direct Codex name before switching from Claude', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-direct-model-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    store.initializeCognition('claude', 'old-token');
    const stagedCodex = [];
    const runtime = new AgentRuntime({}, store, quietLogger, {
      claudeFactory: (options) => {
        const instance = backend('claude', options);
        instance.model = 'sonnet';
        return instance;
      },
      codexFactory: (options) => {
        const instance = backend('codex', options);
        instance.listModels = async () => [{ id: 'gpt-5.6-sol', efforts: ['medium'] }];
        stagedCodex.push(instance);
        return instance;
      },
    });

    try {
      await runtime.start();
      await assert.rejects(() => runtime.transition({
        kind: 'select-runtime', runtime: 'codex', model: 'gpt-missing',
      }), /unavailable/);
      assert.equal(runtime.runtime, 'claude');
      assert.equal(store.getCognitionState().runtime, 'claude');
      assert.equal(stagedCodex[0].state.connected, false);

      const result = await runtime.transition({ kind: 'select-runtime', runtime: 'codex', model: 'gpt-5.6-sol' });
      assert.equal(result.runtimeChanged, true);
      assert.equal(result.model, 'gpt-5.6-sol');
      assert.equal(runtime.runtime, 'codex');
      assert.equal(runtime.model, 'gpt-5.6-sol');
      assert.equal(store.getCognitionState().runtime, 'codex');
    } finally {
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('switches runtimes immediately by interrupting a stuck in-flight turn', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-runtime-stuck-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    let previous;
    const runtime = new AgentRuntime({}, store, quietLogger, {
      codexFactory: (options) => {
        previous = backend('codex', options);
        previous.calls = [];
        previous.turnInProgress = true;
        // A stuck turn: whenIdle would hang forever if the switch awaited it.
        previous.whenIdle = function whenIdle() {
          return this.turnInProgress ? new Promise(() => {}) : Promise.resolve();
        };
        previous.interruptTurn = async function interruptTurn() {
          this.calls.push('interrupt');
          this.turnInProgress = false;
        };
        return previous;
      },
      claudeFactory: (options) => {
        const instance = backend('claude', options);
        instance.model = options.model;
        return instance;
      },
    });

    try {
      await runtime.start();
      store.setSetting('claude_model', 'sonnet');
      const result = await runtime.transition({ kind: 'select-runtime', runtime: 'claude' });
      assert.equal(runtime.model, 'sonnet');
      assert.equal(result.runtimeChanged, true);
      assert.equal(runtime.runtime, 'claude');
      assert.deepEqual(previous.calls, ['interrupt']);
    } finally {
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('switches to OpenCode with no configured model, keeping the session default', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-runtime-opencode-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    const spawns = [];
    const runtime = opencodeSwitchRuntime(directory, store, spawns);

    try {
      await runtime.start();
      // No opencode_model setting and no VIKA_OPENCODE_MODEL, as on an
      // instance that never ran OpenCode: the target model is the default ''.
      const result = await runtime.transition({ kind: 'select-runtime', runtime: 'opencode' });
      assert.equal(result.runtimeChanged, true);
      assert.equal(runtime.runtime, 'opencode');
      // The canonical default stands for "OpenCode's own model"; the session
      // reports which one that is.
      assert.equal(runtime.model, 'opencode');
      assert.equal(runtime.backend.model, '');
      assert.equal(runtime.backend.resolvedModel, 'volcengine-ark/ep-default');
      // The session default needs no set_config_option round trip.
      assert.equal(spawns.at(-1).requests.some((message) => message.method === 'session/set_config_option'), false);
    } finally {
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('falls back to the OpenCode default when the saved model is gone', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-runtime-opencode-gone-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    const spawns = [];
    const runtime = opencodeSwitchRuntime(directory, store, spawns);

    try {
      await runtime.start();
      store.setSetting('opencode_model', 'volcengine-ark/ep-removed');
      const result = await runtime.transition({ kind: 'select-runtime', runtime: 'opencode' });
      assert.equal(result.runtimeChanged, true);
      assert.equal(runtime.runtime, 'opencode');
      assert.equal(runtime.backend.model, '');
      assert.equal(runtime.backend.resolvedModel, 'volcengine-ark/ep-default');
      assert.equal(spawns.length, 2);
    } finally {
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('switches to Antigravity with no configured model, keeping the session default', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-runtime-agy-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    const spawns = [];
    const runtime = agySwitchRuntime(directory, store, spawns);

    try {
      await runtime.start();
      const result = await runtime.transition({ kind: 'select-runtime', runtime: 'agy' });
      assert.equal(result.runtimeChanged, true);
      assert.equal(runtime.runtime, 'agy');
      assert.equal(runtime.model, 'agy');
      assert.equal(runtime.backend.model, '');
      assert.equal(runtime.backend.resolvedModel, 'gemini-3.8-flash-low');
      assert.equal(spawns.at(-1).requests.some((message) => message.method === 'session/set_model'), false);
    } finally {
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('falls back to the Antigravity default when the saved model is gone', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-runtime-agy-gone-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    const spawns = [];
    const runtime = agySwitchRuntime(directory, store, spawns);

    try {
      await runtime.start();
      store.setSetting('agy_model', 'gemini-removed');
      const result = await runtime.transition({ kind: 'select-runtime', runtime: 'agy' });
      assert.equal(result.runtimeChanged, true);
      assert.equal(runtime.runtime, 'agy');
      assert.equal(runtime.backend.model, '');
      assert.equal(runtime.backend.resolvedModel, 'gemini-3.8-flash-low');
      assert.equal(spawns.length, 2);
    } finally {
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('hands archived OV history without an overview to the target runtime', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-runtime-ov-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    // Latest OV with Working Memory off: archived messages leave /context and
    // no overview replaces them.
    const ov = new OVSessionContext(store, {
      namespace: 'test-namespace',
      messages: [],
      async create(id) { return { session_id: id, uri: `viking://session/${id}` }; },
      async append(id, message) { this.messages.push({ ...message, parts: [{ type: 'text', text: message.content }] }); },
      async context() {
        return { messages: this.messages.slice(-1), latest_archive_overview: '', stats: { totalArchives: 3, failedArchives: 0 } };
      },
    }, quietLogger);
    const runtime = new AgentRuntime({}, store, quietLogger, {
      codexFactory: (options) => backend('codex', options),
      claudeFactory: (options) => {
        const instance = backend('claude', options);
        instance.model = options.model;
        // A fresh native session prepares OV history while it starts.
        instance.start = async function start() {
          await store.prepareOVContext(options.cognitionEpoch);
          this.state.connected = true;
        };
        return instance;
      },
    });

    try {
      await runtime.start();
      store.appendCognitionJournal({ kind: 'inbound', actor: 'user', chatId: 'chat', messageId: 'm1', text: 'archived request' });
      store.appendCognitionJournal({ kind: 'inbound', actor: 'user', chatId: 'chat', messageId: 'm2', text: 'recent request' });
      store.setSetting('claude_model', 'sonnet');
      const result = await runtime.transition({ kind: 'select-runtime', runtime: 'claude' });
      assert.equal(result.runtimeChanged, true);
      assert.equal(runtime.runtime, 'claude');
      const prefix = cognitionHistoryPrefix(store, store.getCognitionState().epoch, result.syncedThroughSeq).text;
      assert.match(prefix, /recent request/);
      assert.doesNotMatch(prefix, /archived request/);
    } finally {
      ov.stop();
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('preserves the active turn, session, epoch and authority on a same-runtime model change', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-runtime-samemodel-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    let instance;
    const authorities = [];
    const runtime = new AgentRuntime({}, store, quietLogger, {
      onAuthorityChanged: (authority) => authorities.push(authority),
      codexFactory: (options) => {
        instance = backend('codex', options);
        instance.calls = [];
        instance.turnInProgress = true;
        instance.whenIdle = function whenIdle() {
          return this.turnInProgress ? new Promise(() => {}) : Promise.resolve();
        };
        instance.interruptTurn = async function interruptTurn() {
          this.calls.push('interrupt');
          this.turnInProgress = false;
        };
        instance.setModel = async function setModel(model) {
          this.calls.push(`setModel:${model}`);
          store.setSetting('codex_model', model);
          this.model = model;
        };
        instance.prepareSessionRotation = async function prepareSessionRotation() {
          this.calls.push('prepare');
          return { id: 'codex-fresh', sessionReady: true };
        };
        instance.commitSessionRotation = function commitSessionRotation(prepared, options) {
          assert.equal(options.preserveContext, true);
          this.calls.push(`commit:${options.persist}`);
          this.threadId = prepared.id;
          return this.threadId;
        };
        return instance;
      },
    });

    try {
      await runtime.start();
      const before = store.getCognitionState();
      let preparedEpoch;
      store.prepareOVContext = async (epoch) => { preparedEpoch = epoch; };
      const result = await runtime.transition({ kind: 'select-model', selector: 'gpt-5.6-sol' });
      assert.deepEqual(instance.calls, []);
      assert.equal(instance.model, 'gpt-5.6-sol');
      assert.equal(instance.turnInProgress, true);
      assert.equal(preparedEpoch, undefined);
      assert.equal(result.runtimeChanged, false);
      assert.equal(result.threadId, 'codex-session');
      // Both native and logical sessions are preserved.
      assert.equal(store.getCognitionState().epoch, before.epoch);
      assert.equal(store.getSetting('codex_model'), 'gpt-5.6-sol');
      // Codex keeps one MCP child across native thread rotation, so its process
      // authority remains stable until that child is replaced.
      assert.equal(store.getCognitionState().authorityToken, before.authorityToken);
      assert.equal(authorities.length, 1);
      assert.equal(authorities.at(-1).authorityToken, store.getCognitionState().authorityToken);
    } finally {
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('rotates a prepared Ark session without waiting on queued next-turn input', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-runtime-ark-new-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    store.initializeCognition('volc', 'old-token');
    let instance;
    const runtime = new AgentRuntime({ connectors: ['lark'] }, store, quietLogger, {
      volcFactory: (options) => {
        instance = backend('volc', options);
        instance.threadId = 'sesn-old123456';
        instance.policyHash = 'volc-policy';
        instance.state.queued = 1;
        instance.whenIdle = () => new Promise(() => {});
        instance.prepareSessionRotation = async () => ({ id: 'sesn-new123456' });
        instance.commitSessionRotation = (prepared, commitOptions) => {
          instance.threadId = prepared.id;
          instance.commitOptions = commitOptions;
          return prepared.id;
        };
        return instance;
      },
    });

    try {
      await runtime.start();
      const before = store.getCognitionState();
      store.setGlobalSession(instance.threadId, instance.policyHash, {
        runtime: 'volc',
        epoch: before.epoch,
      });
      const { threadId: sessionId } = await runtime.transition({ kind: 'new-session' });
      assert.equal(sessionId, 'sesn-new123456');
      assert.deepEqual(instance.commitOptions, { persist: false });
      assert.equal(store.getCognitionState().epoch, before.epoch + 1);
      assert.notEqual(store.getCognitionState().authorityToken, before.authorityToken);
      assert.equal(instance.authorityToken, store.getCognitionState().authorityToken);
      assert.equal(store.getGlobalSession().sessionId, 'sesn-new123456');
    } finally {
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('keeps cognition and the active session unchanged when the prepared-session CAS fails', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-runtime-cas-fail-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    let instance;
    let cancelled = 0;
    let committed = 0;
    const runtime = new AgentRuntime({}, store, quietLogger, {
      codexFactory: (options) => {
        instance = backend('codex', options);
        instance.prepareSessionRotation = async () => ({ id: 'codex-candidate', sessionReady: true });
        instance.commitSessionRotation = () => { committed += 1; return 'codex-candidate'; };
        instance.cancelSessionRotation = () => { cancelled += 1; };
        return instance;
      },
    });

    try {
      await runtime.start();
      const before = store.getCognitionState();
      store.setGlobalSession('codex-session', 'codex-policy', {
        runtime: 'codex',
        epoch: before.epoch,
        syncedThroughSeq: 7,
      });
      const globalBefore = store.getGlobalSession();
      store.commitCognitionSessionRotation = () => { throw new Error('sqlite CAS failed'); };

      await assert.rejects(
        () => runtime.transition({ kind: 'new-session' }),
        /sqlite CAS failed/,
      );
      assert.deepEqual(store.getCognitionState(), before);
      assert.deepEqual(store.getGlobalSession(), globalBefore);
      assert.equal(cancelled, 1);
      assert.equal(committed, 0);
    } finally {
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('rejects a stale effort intent before interrupting the current runtime', async () => {
    const runtime = new AgentRuntime({}, {}, quietLogger);
    const calls = [];
    const implementation = backend('codex');
    implementation.interruptTurn = async () => { calls.push('interrupt'); };
    implementation.setEffort = (effort) => { calls.push(`effort:${effort}`); };
    runtime.runtime = 'codex';
    runtime.backend = adaptRuntime('codex', implementation);
    runtime.revision = 4;

    const result = await runtime.transition({
      kind: 'select-effort',
      effort: 'high',
      expectedRuntime: 'claude',
      expectedRevision: 3,
    });
    assert.equal(result.ok, false);
    assert.equal(result.reason, 'stale-snapshot');
    assert.deepEqual(calls, []);
  });

  for (const id of ['codex', 'traex', 'claude', 'pi']) it(`${id} effort preserves an active turn, session, epoch and authority`, async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-effort-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    try {
      store.initializeCognition(id, 'token');
      const cognition = store.getCognitionState();
      store.setGlobalSession(`${id}-session`, 'policy', { runtime: id, epoch: cognition.epoch, syncedThroughSeq: 7 });
      const session = store.getGlobalSession();
      const runtime = new AgentRuntime({}, store, quietLogger);
      const implementation = backend(id);
      implementation.turnInProgress = true;
      implementation.interruptTurn = () => assert.fail('effort must not interrupt');
      let updates = 0;
      implementation.setEffort = (effort) => {
        updates += 1;
        store.setSetting(`${id}_effort`, effort);
        implementation.effort = effort;
      };
      runtime.runtime = id;
      runtime.backend = adaptRuntime(id, implementation);

      const result = await runtime.transition({ kind: 'select-effort', effort: 'high' });
      assert.deepEqual(result, { ok: true, kind: 'select-effort', runtime: id, effort: 'high', unchanged: false });
      assert.equal(implementation.turnInProgress, true);
      assert.equal(runtime.threadId, session.sessionId);
      assert.deepEqual(store.getCognitionState(), cognition);
      assert.deepEqual(store.getGlobalSession(), session);
      assert.equal(store.getSetting(`${id}_effort`), 'high');
      const repeated = await runtime.transition({ kind: 'select-effort', effort: 'high' });
      assert.equal(repeated.unchanged, true);
      assert.equal(updates, 1);
    } finally {
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('propagates an effort persistence failure without rotating or interrupting', async () => {
    const runtime = new AgentRuntime({}, {}, quietLogger);
    runtime.backend = adaptRuntime('codex', {
      effort: 'medium', threadId: 'same-session',
      listModels: async () => [{ id: 'gpt-default', isDefault: true, efforts: ['medium', 'high'] }],
      async setEffort() { throw new Error('settings write failed'); },
      interruptTurn() { assert.fail('effort must not interrupt'); },
    });
    await assert.rejects(runtime.transition({ kind: 'select-effort', effort: 'high' }), /settings write failed/);
    assert.equal(runtime.effort, 'medium');
    assert.equal(runtime.threadId, 'same-session');
    assert.equal(runtime.pendingTransitions, 0);
  });

  it('releases an Ark rotation fence when remote session preparation fails', async () => {
    const runtime = new AgentRuntime({}, {
      getCognitionState: () => ({ runtime: 'volc', epoch: 1, authorityToken: 'token' }),
    }, quietLogger);
    let cancelled = 0;
    runtime.runtime = 'volc';
    runtime.backend = adaptRuntime('volc', {
      async prepareSessionRotation() { throw new Error('remote create failed'); },
      cancelSessionRotation() { cancelled += 1; },
    });
    await assert.rejects(() => runtime.transition({ kind: 'new-session' }), /remote create failed/);
    assert.equal(cancelled, 1);
  });

  it('rotates process authority on startup so a crash survivor loses read and write access', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-runtime-restart-'));
    const databasePath = path.join(directory, 'state.sqlite3');
    const store = new Store(databasePath, quietLogger);
    store.initializeCognition('codex', 'token-from-crashed-daemon');
    const connectors = fakeConnectors();
    const actions = new ActionService({ config: {}, store, connectors, logger: quietLogger });
    const stale = { authority: 'token-from-crashed-daemon' };
    const runtime = new AgentRuntime({}, store, quietLogger, {
      codexFactory: (options) => backend('codex', options),
    });

    try {
      await runtime.start();
      assert.notEqual(store.getCognitionState().authorityToken, 'token-from-crashed-daemon');
      await assert.rejects(() => actions.call('list_chats', {}, stale), /no longer owns/);
      await assert.rejects(() => actions.call('send_message', {
        chat_id: 'oc_restart123456',
        markdown: 'must stay fenced',
        decision_id: 'message:om_restart123456',
      }, stale), /no longer owns/);
      assert.equal(connectors.calls.length, 0);
    } finally {
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  for (const connector of ['weixin', 'telegram', 'qq', 'onebot']) it(`serves ${connector} through local agent tools and rejects a switch to Volc`, async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), `lark-runtime-${connector}-`));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    const runtime = new AgentRuntime({ connectors: [connector] }, store, quietLogger, {
      codexFactory: (options) => backend('codex', options),
    });
    try {
      await runtime.start();
      assert.equal(runtime.runtime, 'codex');
      await assert.rejects(() => runtime.transition({ kind: 'select-runtime', runtime: 'volc' }), /Volc/);
      assert.equal(runtime.runtime, 'codex');
    } finally {
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('cancels and retires a staged backend when shutdown races a hard switch', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-runtime-stop-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    let releaseStart;
    let markStarted;
    const startGate = new Promise((resolve) => { releaseStart = resolve; });
    const stagedStarted = new Promise((resolve) => { markStarted = resolve; });
    let staged;
    const runtime = new AgentRuntime({}, store, quietLogger, {
      codexFactory: (options) => backend('codex', options),
      claudeFactory: (options) => {
        staged = backend('claude', options);
        staged.stopCount = 0;
        staged.start = async function start() {
          markStarted();
          await startGate;
          this.state.connected = true;
        };
        staged.stop = async function stop() {
          this.stopCount += 1;
          this.state.connected = false;
        };
        return staged;
      },
    });

    try {
      await runtime.start();
      const switchResult = runtime.transition({ kind: 'select-runtime', runtime: 'claude', model: 'sonnet' });
      assert.deepEqual(runtime.submit([{ text: 'must remain caller-owned', seq: 9 }]), {
        accepted: false,
        reason: 'switching',
      });
      await stagedStarted;
      const stopped = runtime.stop();
      releaseStart();
      await stopped;
      await assert.rejects(() => switchResult, /stopped during runtime switch/);
      assert.equal(store.getCognitionState().runtime, 'codex');
      assert.equal(runtime.runtime, 'codex');
      assert.ok(staged.stopCount >= 1);
    } finally {
      releaseStart();
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('waits for and retires a same-runtime provider operation during stop', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-runtime-stop-transition-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    let releaseModel;
    let markModelStarted;
    const modelStarted = new Promise((resolve) => { markModelStarted = resolve; });
    const modelGate = new Promise((resolve) => { releaseModel = resolve; });
    let instance;
    const runtime = new AgentRuntime({}, store, quietLogger, {
      codexFactory: (options) => {
        instance = backend('codex', options);
        instance.stopCount = 0;
        instance.interruptTurn = async () => {};
        instance.listModels = async function listModels() {
          markModelStarted();
          await modelGate;
          return [{ id: 'gpt-5.6-sol', efforts: ['medium'] }];
        };
        instance.stop = async function stop() {
          this.stopCount += 1;
          this.state.connected = false;
        };
        return instance;
      },
    });

    try {
      await runtime.start();
      const epoch = store.getCognitionState().epoch;
      const changing = runtime.transition({ kind: 'select-model', selector: 'gpt-5.6-sol' });
      await modelStarted;
      const stopped = runtime.stop();
      releaseModel();
      await stopped;
      await assert.rejects(() => changing, /stopped during model transition/);
      assert.equal(store.getCognitionState().epoch, epoch);
      assert.ok(instance.stopCount >= 2);
    } finally {
      releaseModel();
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });
});

describe('loop breaker', () => {
  // A Codex stand-in with a live turn. `ended(kind, info)` plays the
  // transport reporting that turn's end through its backend callbacks.
  function loopRuntime(t, { cancels = true, decided = true, reportsAbandon = true, ...options } = {}) {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-runtime-loop-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    const seen = { succeeded: [], failed: [], abandoned: [], stopped: [], activities: [], commits: [] };
    let instance;
    let callbacks;
    const runtime = new AgentRuntime({}, store, quietLogger, {
      onTurnSucceeded: (info) => seen.succeeded.push(info),
      onTurnFailed: (info) => seen.failed.push(info),
      onTurnAbandoned: (info) => seen.abandoned.push(info),
      onTurnStopped: (info) => seen.stopped.push(info),
      recordActivity: (kind, fields) => seen.activities.push({ kind, ...fields }),
      codexFactory: (backendOptions) => {
        callbacks = backendOptions;
        instance = backend('codex', backendOptions);
        instance.turnInProgress = true;
        instance.cancelCalls = 0;
        instance.interrupts = 0;
        instance.cancelTurn = () => { instance.cancelCalls += 1; return cancels; };
        instance.interruptTurn = async () => {
          instance.interrupts += 1;
          if (!instance.turnInProgress) return;
          instance.turnInProgress = false;
          // A turn with no seq or receipt (a reminder line) reports nothing.
          if (reportsAbandon) callbacks.onTurnAbandoned({ inboundSeqs: [7] });
        };
        let fresh = 0;
        instance.prepareSessionRotation = async () => ({ id: `codex-fresh-${fresh += 1}`, sessionReady: true });
        instance.commitSessionRotation = (prepared, commitOptions) => {
          seen.commits.push(commitOptions);
          instance.threadId = prepared.id;
          return prepared.id;
        };
        return instance;
      },
      ...options,
    });
    t.after(async () => {
      await runtime.stop();
      store.close();
      fs.rmSync(directory, { recursive: true, force: true });
    });
    const ended = (kind, info) => {
      instance.turnInProgress = false;
      callbacks[{ failed: 'onTurnFailed', succeeded: 'onTurnSucceeded', abandoned: 'onTurnAbandoned' }[kind]](info);
    };
    const looped = store.ingest(chatEvent({ messageId: 'om-looped', eventId: 'evt-looped', chatType: 'group' })).inbound;
    // The repeated call was a stay_silent or send on a decided input.
    if (decided) store.recordDecision({ row: store.acceptedEvents('evt-looped')[0], outcome: 'silent' });
    return { runtime, store, seen, ended, looped, backend: () => instance };
  }

  it('cancels the looping turn in place and ends it as a normal turn, with a notice for the next prompt', async (t) => {
    const { runtime, seen, ended, looped, backend: current } = loopRuntime(t);
    await runtime.start();
    const result = runtime.breakLoop({ tool: 'stay_silent', decisionSeq: looped.seq, count: 3 });
    assert.deepEqual(result, { action: 'cancel' });
    assert.equal(current().cancelCalls, 1);
    assert.deepEqual(runtime.breakLoop({ tool: 'stay_silent', decisionSeq: looped.seq, count: 4 }),
      { action: 'skipped', reason: 'stop-in-flight' });
    // OpenCode, Pi, Durable and Claude report a cancelled prompt as failed.
    ended('failed', { inboundSeqs: [looped.seq, 8], error: 'opencode turn ended: cancelled' });
    assert.deepEqual(seen.failed, []);
    assert.deepEqual(seen.succeeded, []);
    assert.deepEqual(seen.stopped, [{ inboundSeqs: [looped.seq, 8], error: 'opencode turn ended: cancelled' }]);
    assert.match(current().turnNotice, /^\[daemon notice\] The daemon stopped your previous turn: it called stay_silent for event=evt-looped 3 times after that call had nothing left to change\. That input already has its decision; take no further action on it\. Go on with the inputs below\.$/);
    assert.doesNotMatch(current().turnNotice, /new session/);
    assert.deepEqual(seen.activities.map((row) => [row.kind, row.status, row.inboundSeq, row.metadata.action, row.metadata.endedAs]),
      [['agent.loop_stopped', 'CANCELLED', looped.seq, 'cancel', 'failed']]);
    assert.equal(current().interrupts, 0);
    assert.deepEqual(seen.commits, [], 'same session');
    // Later turn ends are reported as they are again.
    ended('failed', { inboundSeqs: [9], error: 'boom' });
    assert.deepEqual(seen.failed, [{ inboundSeqs: [9], error: 'boom' }]);
  });

  it('moves to a new session that keeps the history when the cancel does not end the turn in time', async (t) => {
    const { runtime, store, seen, looped, backend: current } = loopRuntime(t, { loopCancelGraceMs: 20 });
    await runtime.start();
    const epoch = store.getCognitionState().epoch;
    assert.deepEqual(runtime.breakLoop({ tool: 'stay_silent', decisionSeq: looped.seq, count: 3 }), { action: 'cancel' });
    await new Promise((resolve) => setTimeout(resolve, 60));
    await runtime.transitionBarrier;
    assert.equal(current().interrupts, 1);
    assert.deepEqual(seen.stopped, [{ inboundSeqs: [7] }]);
    assert.deepEqual(seen.abandoned, []);
    assert.deepEqual(seen.commits, [{ persist: false, preserveContext: true }]);
    assert.equal(runtime.threadId, 'codex-fresh-1');
    assert.equal(store.getCognitionState().epoch, epoch);
    assert.match(current().turnNotice, /You are now in a new session; what came before is in the history above\./);
    assert.deepEqual(seen.activities.map((row) => [row.status, row.metadata.cancelTimedOut]), [['NEW_SESSION', true]]);
  });

  it('moves to a new session at once when the transport cannot cancel in place', async (t) => {
    // A repeated timer leaves no decision: the notice does not call the input done.
    const { runtime, seen, looped, backend: current } = loopRuntime(t, { cancels: false, decided: false });
    await runtime.start();
    assert.deepEqual(runtime.breakLoop({ tool: 'create_timer', decisionSeq: looped.seq, count: 3 }), { action: 'new-session' });
    await runtime.transitionBarrier;
    assert.equal(current().cancelCalls, 1);
    assert.equal(current().interrupts, 1);
    assert.deepEqual(seen.commits, [{ persist: false, preserveContext: true }]);
    assert.deepEqual(seen.stopped, [{ inboundSeqs: [7] }]);
    assert.match(current().turnNotice, /it called create_timer for event=evt-looped 3 times after that call had nothing left to change\. Do not repeat that call\. You are now in a new session/);
  });

  it('moves a session stopped again within the window to a new one, and cancels in place again after that', async (t) => {
    let now = 1_000_000;
    const { runtime, seen, ended, looped, backend: current } = loopRuntime(t, { clock: () => now });
    await runtime.start();
    const stop = () => runtime.breakLoop({ tool: 'stay_silent', decisionSeq: looped.seq, count: 3 });
    assert.equal(stop().action, 'cancel');
    ended('succeeded', { inboundSeqs: [looped.seq] });
    // Ten minutes later the same session is cancelled in place once more.
    now += 10 * 60_000;
    current().turnInProgress = true;
    assert.equal(stop().action, 'cancel');
    ended('succeeded', { inboundSeqs: [looped.seq] });
    // Within the window: a new session.
    now += 60_000;
    current().turnInProgress = true;
    assert.equal(stop().action, 'new-session');
    await runtime.transitionBarrier;
    assert.equal(runtime.threadId, 'codex-fresh-1');
    // That new session has no stop of its own yet.
    now += 1_000;
    current().turnInProgress = true;
    assert.equal(stop().action, 'cancel');
    assert.equal(current().cancelCalls, 3);
    assert.deepEqual(seen.activities.map((row) => row.metadata.action), ['cancel', 'cancel', 'new-session']);
  });

  it('reports the end of a stop that the transport ended without telling, so the repeat counts reset', async (t) => {
    // A reminder-only turn (lines without seq) interrupted on the way to a new
    // session, and a cancelled turn that ends without any callback.
    const replaced = loopRuntime(t, { cancels: false, reportsAbandon: false });
    await replaced.runtime.start();
    assert.equal(replaced.runtime.breakLoop({ tool: 'stay_silent', decisionSeq: replaced.looped.seq, count: 3 }).action, 'new-session');
    await replaced.runtime.transitionBarrier;
    assert.equal(replaced.backend().interrupts, 1);
    assert.deepEqual(replaced.seen.stopped, [{ inboundSeqs: [] }]);
    assert.equal(replaced.runtime.loopBreak, null);

    const quiet = loopRuntime(t, { loopCancelGraceMs: 20 });
    await quiet.runtime.start();
    assert.equal(quiet.runtime.breakLoop({ tool: 'stay_silent', decisionSeq: quiet.looped.seq, count: 3 }).action, 'cancel');
    quiet.backend().turnInProgress = false;
    await new Promise((resolve) => setTimeout(resolve, 60));
    assert.deepEqual(quiet.seen.stopped, [{ inboundSeqs: [] }]);
    assert.deepEqual(quiet.seen.commits, [], 'nothing left to replace');
  });

  it('forgets earlier stops once the session is replaced, whatever id the transport reuses', async (t) => {
    const { runtime, seen, ended, looped, backend: current } = loopRuntime(t);
    await runtime.start();
    const stop = () => runtime.breakLoop({ tool: 'stay_silent', decisionSeq: looped.seq, count: 3 });
    assert.equal(stop().action, 'cancel');
    ended('succeeded', { inboundSeqs: [looped.seq] });
    // /new hands back a session under the very same id.
    current().prepareSessionRotation = async () => ({ id: 'codex-session', sessionReady: true });
    await runtime.transition({ kind: 'new-session' });
    assert.equal(runtime.threadId, 'codex-session');
    current().turnInProgress = true;
    assert.equal(stop().action, 'cancel');
    ended('succeeded', { inboundSeqs: [looped.seq] });
    assert.deepEqual(seen.activities.map((row) => row.metadata.action), ['cancel', 'cancel']);
  });

  it('skips with no live turn, and leaves a stop that /new already ended alone', async (t) => {
    const { runtime, seen, looped, backend: current } = loopRuntime(t, { loopCancelGraceMs: 20 });
    await runtime.start();
    current().turnInProgress = false;
    assert.deepEqual(runtime.breakLoop({ tool: 'stay_silent', decisionSeq: looped.seq, count: 3 }),
      { action: 'skipped', reason: 'no-turn' });
    current().turnInProgress = true;
    assert.equal(runtime.breakLoop({ tool: 'stay_silent', decisionSeq: looped.seq, count: 3 }).action, 'cancel');
    await runtime.transition({ kind: 'new-session' });
    // The /new interrupt ended the stopped turn; its grace timer finds nothing to do.
    await new Promise((resolve) => setTimeout(resolve, 60));
    await runtime.transitionBarrier;
    assert.equal(current().interrupts, 1);
    assert.deepEqual(seen.stopped, [{ inboundSeqs: [7] }]);
    assert.deepEqual(seen.commits.length, 1);
    assert.equal(runtime.loopBreak, null);
  });
});
