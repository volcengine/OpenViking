// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { AgentRuntime } from '../src/agent-runtime.mjs';

const quietLogger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

class SimulationStore {
  constructor() {
    this.cognition = { runtime: 'codex', epoch: 1, authorityToken: 'token-codex' };
    this.session = { sessionId: null, policyHash: null, runtime: null, epoch: 0, syncedThroughSeq: 0 };
    this.commits = [];
    this.settings = new Map();
    this.journal = [{
      seq: 1, epoch: 1, kind: 'inbound', status: 'COMMITTED', atMs: 1,
      inboundSeq: 1, chatId: 'oc_sim12345678', threadId: null,
      messageId: 'om_sim12345678', actor: 'ou_sim12345678', text: 'prior message',
    }];
  }

  getOVContext() {
    return { sessionId: 'ov-simulation', messages: [{ parts: [{ type: 'text', text: 'prior message' }], source_message_ids: ['lark:1:1'] }] };
  }
  getCognitionState() { return { ...this.cognition }; }
  initializeCognition(runtime, authorityToken) {
    this.cognition = { runtime, epoch: this.cognition.epoch, authorityToken };
    return this.getCognitionState();
  }
  getGlobalSession() { return { ...this.session }; }
  getSetting(key) { return this.settings.get(key) ?? null; }
  setSetting(key, value) { this.settings.set(key, value); }
  cognitionJournalHead() { return this.journal.at(-1)?.seq ?? 0; }
  cognitionJournal(_epoch, through, after = 0) {
    return this.journal.filter((row) => row.seq > after && row.seq <= through);
  }
  commitRuntimeSwitch(fields) {
    if (this.cognition.runtime !== fields.expectedRuntime
      || this.cognition.authorityToken !== fields.expectedAuthorityToken
      || this.cognition.epoch !== fields.epoch) {
      throw new Error('cognition authority changed during runtime switch');
    }
    this.cognition = {
      runtime: fields.runtime,
      epoch: fields.epoch,
      authorityToken: fields.authorityToken,
    };
    this.session = {
      sessionId: fields.sessionId,
      policyHash: fields.policyHash,
      runtime: fields.runtime,
      epoch: fields.epoch,
      syncedThroughSeq: fields.syncedThroughSeq,
      sessionReady: fields.sessionReady,
    };
    this.settings.set(`${fields.runtime}_model`, fields.model);
    this.commits.push({ ...fields });
  }
  appendCognitionJournal(entry) {
    this.journal.push({
      seq: this.journal.length + 1,
      epoch: this.cognition.epoch,
      status: 'COMMITTED',
      atMs: Date.now(),
      ...entry,
    });
  }
  bumpCognitionEpoch(runtime, authorityToken) {
    this.cognition = { runtime, authorityToken, epoch: this.cognition.epoch + 1 };
    this.session = { sessionId: null, policyHash: null, runtime: null, epoch: 0, syncedThroughSeq: 0 };
    return this.getCognitionState();
  }
}

class ScriptedBackend {
  constructor(runtime, options, events, { failStart = false } = {}) {
    this.runtime = runtime;
    this.options = options;
    this.events = events;
    this.failStart = failStart;
    this.model = options.model ?? '';
    this.effort = 'medium';
    this.threadId = `${runtime}-session-${events.length}`;
    this.state = { connected: false, activeTurn: 0, queued: 0 };
    this.turnInProgress = false;
    this.deliveries = [];
  }
  get sessionDescriptor() {
    return {
      runtime: this.runtime,
      sessionId: this.threadId,
      policyHash: `${this.runtime}-policy`,
      epoch: this.options.cognitionEpoch,
      syncedThroughSeq: 0,
      sessionReady: this.runtime === 'codex',
    };
  }
  async start() {
    this.events.push(`${this.runtime}:start`);
    if (this.failStart) throw new Error(`${this.runtime} unavailable`);
    this.state.connected = true;
  }
  async whenIdle() { this.events.push(`${this.runtime}:idle`); }
  activateSession() { this.events.push(`${this.runtime}:activate`); }
  async stop() { this.state.connected = false; this.events.push(`${this.runtime}:stop`); }
  deliverBatch(items) { this.deliveries.push(items); return true; }
  tryStartBatch(items) { this.deliveries.push(items); return true; }
  async setModel(model) { this.model = model; }
  setEffort(effort) { this.effort = effort; }
  async listModels() {
    return this.runtime === 'codex'
      ? [{ id: 'gpt-5.6-sol', efforts: ['medium'] }]
      : [{ id: 'sonnet', efforts: ['medium'] }];
  }
  async rotateSession() { this.threadId = `${this.runtime}-new`; return this.threadId; }
}

export async function runClaudeRuntimeSimulation() {
  const store = new SimulationStore();
  const events = [];
  const instances = [];
  let failNextClaude = false;
  const factory = (runtime) => (options) => {
    const backend = new ScriptedBackend(runtime, options, events, {
      failStart: runtime === 'claude' && failNextClaude,
    });
    failNextClaude = false;
    instances.push(backend);
    return backend;
  };
  const runtime = new AgentRuntime({}, store, quietLogger, {
    codexFactory: factory('codex'),
    claudeFactory: factory('claude'),
  });

  await runtime.start();
  const codexGenerationToken = store.cognition.authorityToken;
  const originalCodex = runtime.backend;
  const switched = await runtime.transition({ kind: 'select-runtime', runtime: 'claude', model: 'sonnet' });
  const claude = runtime.backend;
  const claudeImplementation = instances.at(-1);
  assert.equal(switched.runtimeChanged, true);
  assert.equal(runtime.runtime, 'claude');
  assert.equal(claudeImplementation.options.allowResume, false);
  assert.equal(claudeImplementation.options.syncedThroughSeq, 0);
  assert.equal(store.session.syncedThroughSeq, 0);
  assert.equal(store.session.sessionReady, false);
  assert.equal(originalCodex.state.connected, false);
  assert.notEqual(claudeImplementation.options.authorityToken, codexGenerationToken);

  const committedToken = store.cognition.authorityToken;
  failNextClaude = true;
  // Same-runtime changes do not construct a second runtime; use a fresh
  // manager to exercise target-start failure before authority commit.
  const failingStore = new SimulationStore();
  const failing = new AgentRuntime({}, failingStore, quietLogger, {
    codexFactory: factory('codex'),
    claudeFactory: (options) => new ScriptedBackend('claude', options, events, { failStart: true }),
  });
  await failing.start();
  const failingGenerationToken = failingStore.cognition.authorityToken;
  const stillCodex = failing.backend;
  await assert.rejects(() => failing.transition({
    kind: 'select-runtime', runtime: 'claude', model: 'sonnet',
  }), /unavailable/);
  assert.equal(failing.runtime, 'codex');
  assert.equal(failing.backend, stillCodex);
  assert.equal(failingStore.cognition.authorityToken, failingGenerationToken);

  await runtime.transition({ kind: 'select-runtime', runtime: 'codex', model: 'gpt-5.6-sol' });
  assert.equal(runtime.runtime, 'codex');
  assert.notEqual(runtime.backend, originalCodex);
  assert.equal(store.getGlobalSession().runtime, 'codex');

  return {
    ok: true,
    switches: store.commits.map((entry) => entry.runtime),
    oldAuthorityRevoked: committedToken !== codexGenerationToken,
    staleCodexReused: runtime.backend === originalCodex,
    failurePreservedOldRuntime: failing.backend === stillCodex,
    events,
    instances: instances.length,
  };
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : '';
if (invokedPath && invokedPath === fileURLToPath(import.meta.url)) {
  const result = await runClaudeRuntimeSimulation();
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}
