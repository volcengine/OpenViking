// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Coordinator-layer fixture shared by the model-selection suites: a fake DSH
// bridge process, a store double, an AgentRuntime factory, and the /model
// command entry. No product code imports this module.

import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { after } from 'node:test';
import { AgentRuntime } from '../src/agent-runtime.mjs';
import { adaptRuntime } from '../src/runtime-adapter.mjs';
import { DshAgent } from '../src/dsh.mjs';
import { executeCommand } from '../src/commands.mjs';


const quietLogger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };
const flush = () => new Promise((resolve) => setImmediate(resolve));

async function waitFor(predicate, label, timeoutMs = 2_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
  throw new Error(`timed out waiting for ${label}`);
}

const createdTempDirs = new Set();

function tempDir() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-model-selection-'));
  createdTempDirs.add(directory);
  return directory;
}

after(() => {
  for (const directory of createdTempDirs) {
    try { fs.rmSync(directory, { recursive: true, force: true }); } catch {}
  }
  createdTempDirs.clear();
});

function baseConfig(overrides = {}) {
  const dataDir = overrides.dataDir ?? tempDir();
  return {
    dshBin: '/opt/dsh/bin/dsh',
    dshEffort: 'high',
    workspace: tempDir(),
    projectDir: '/release',
    dataDir,
    databasePath: path.join(dataDir, 'test.sqlite3'),
    profile: 'test-profile',
    connectors: ['lark'],
    larkCliBin: '/fake-lark/lark-cli',
    slackCliBin: '/fake-slack/slack',
    actionSocketPath: path.join(dataDir, 'actions.sock'),
    actionMcpPath: '/release/packages/daemon/src/actions/mcp.mjs',
    sendTimeoutMs: 5_000,
    maxReplyChars: 1_000,
    dryRun: true,
    ...overrides,
  };
}

// A store with the shape DshAgent and AgentRuntime use, including the real
// store's `String(value)` coercion: a null written into the settings table is
// the string "null", which is exactly what the effort contract must not do.
function makeStore({ settings = {}, runtime = 'dsh' } = {}) {
  let global = {
    sessionId: null, policyHash: null, runtime: null, epoch: 0,
    syncedThroughSeq: 0, sessionReady: false,
  };
  const values = new Map(Object.entries(settings));
  return {
    settings: values,
    getCognitionState: () => ({ runtime, epoch: 1, authorityToken: 'dsh-token' }),
    getGlobalSession: () => global,
    setGlobalSession(sessionId, policyHash, fields) {
      global = { sessionId, policyHash, ...fields };
    },
    publishOwnedSession({ sessionId, policyHash, runtime: owner, epoch, acceptedThroughSeq, sessionReady }) {
      global = { sessionId, policyHash, runtime: owner, epoch, syncedThroughSeq: acceptedThroughSeq, sessionReady };
      return true;
    },
    markOwnedSessionUnresumable() { return true; },
    advanceOwnedSessionAcceptance() { return true; },
    getSetting: (key) => values.get(key) ?? null,
    setSetting(key, value) { values.set(key, String(value)); },
    setRuntimeSettings(owner, { model, effort }) {
      values.set(`${owner}_model`, String(model));
      if (effort !== undefined) values.set(`${owner}_effort`, String(effort));
    },
    cognitionJournalHead: () => 0,
    cognitionJournal: () => [],
    cognitionJournalBeforeInbound: () => 0,
    cognitionJournalSeqForInbound: () => 0,
    getOVContext: () => null,
    commitRuntimeSwitch({ runtime: owner, sessionId, policyHash, epoch, syncedThroughSeq }) {
      global = { sessionId, policyHash, runtime: owner, epoch, syncedThroughSeq, sessionReady: true };
      return true;
    },
    appendCognitionJournal() {},
    recordActivity() {},
    async prepareOVContext() {},
  };
}

const DESCRIPTOR = Object.freeze({ ns: 'llm-pi-ai', revision: 1, value: { providers: {} } });

// The catalogue every fake process answers with by default: two usable
// deepseek-official models plus an empty route.
const DEFAULT_MODELS = Object.freeze([
  {
    provider: 'deepseek-official', name: 'DeepSeek', error: null, credential: null,
    models: [
      { id: 'deepseek-flash', name: 'DeepSeek Flash', inputModalities: ['text'] },
      { id: 'deepseek-v4-pro', name: 'DeepSeek V4 Pro', inputModalities: ['text'] },
    ],
  },
  { provider: 'deepseek-account', name: 'DeepSeek Account', models: [], error: null, credential: null },
]);

function defaultModelInfo(provider, model) {
  return {
    provider,
    id: model,
    name: model,
    inputModalities: ['text'],
    reasoning: { efforts: [{ id: 'low', name: 'Low' }, { id: 'high', name: 'High' }], defaultEffort: 'high' },
  };
}

// A fake DSH process: the same LF-framed JSON-RPC surface the real one speaks
// for the calls a model switch makes, with the two refusals this suite needs
// (an initialize the ladder has to downgrade, and a select DSH rejects).
class FakeChild extends EventEmitter {
  constructor(options = {}) {
    super();
    this.pid = 980_000 + (FakeChild.nextPid += 1);
    this.exitCode = null;
    this.signalCode = null;
    this.writes = [];
    this.models = options.models ?? DEFAULT_MODELS;
    this.modelInfo = options.modelInfo ?? null;
    this.selectError = options.selectError ?? null;
    // (provider, model, effort) -> an Error to answer initialize with.
    this.initializeError = options.initializeError ?? null;
    // Keep the initialize answer until the test releases it, so a selection can
    // be made while the handshake is still in flight.
    this.holdInitialize = options.holdInitialize === true;
    this.held = [];
    // Keep every select answer until the test releases it, so a newer intent or
    // a failure can be produced while one selection is still in flight.
    this.holdSelect = options.holdSelect === true;
    this.heldSelects = [];
    this.ignoreShutdown = false;
    this.stdin = new EventEmitter();
    this.stdin.writable = true;
    this.stdin.write = (value) => {
      const frame = JSON.parse(value);
      this.writes.push(frame);
      this._answer(frame);
      return true;
    };
    this.stdin.end = () => {};
    this.stdout = new EventEmitter();
    this.stdout.setEncoding = () => {};
    this.stderr = new EventEmitter();
    this.stderr.setEncoding = () => {};
  }

  static nextPid = 1;

  _answer(frame) {
    if (!frame.method) return;
    if (frame.method === 'initialize' && this.holdInitialize) {
      this.held.push(frame);
      return;
    }
    if (frame.method === 'vika/session/model/select' && this.holdSelect) {
      this.heldSelects.push(frame);
      return;
    }
    queueMicrotask(() => this._reply(frame));
  }

  releaseInitialize() {
    const held = this.held.splice(0);
    for (const frame of held) this._reply(frame);
    return held.length;
  }

  // Answer the selects that were held. `error` refuses them instead.
  releaseSelect({ error = null } = {}) {
    const held = this.heldSelects.splice(0);
    for (const frame of held) {
      if (error) this._error(frame, error);
      else this._reply(frame);
    }
    return held.length;
  }

  _reply(frame) {
    if (this.exitCode !== null) return;
    const method = frame.method;
    if (method === 'initialize') {
      const error = this.initializeError?.(frame.params.provider, frame.params.model, frame.params.reasoningEffort);
      if (error) this._error(frame, error);
      else this._result(frame, { serverInfo: { name: 'deepseek-harness-sdk-runtime', version: '0.2.2-rc.1' } });
      return;
    }
    if (method === 'vika/hello') {
      this._result(frame, {
        bridge: 'vika-dsh-bridge',
        version: 2,
        methods: [
          'session/steer', 'session/cancel',
          'vika/llm/models', 'vika/llm/model/info', 'vika/session/model/select',
        ],
      });
      return;
    }
    if (method === 'vika/settings/describe') {
      this._result(frame, { ns: 'llm-pi-ai', descriptor: DESCRIPTOR });
      return;
    }
    if (method === 'session/resume') { this._result(frame, {}); return; }
    if (method === 'session/working-directory/get') { this._result(frame, { cwd: '/tmp/workspace' }); return; }
    if (method === 'vika/llm/models') { this._result(frame, { providers: this.models }); return; }
    if (method === 'vika/llm/model/info') {
      const info = (this.modelInfo ?? defaultModelInfo)(frame.params.provider, frame.params.model);
      if (info instanceof Error) this._error(frame, info);
      else this._result(frame, info);
      return;
    }
    if (method === 'vika/session/model/select') {
      if (this.selectError) this._error(frame, this.selectError);
      else {
        this._result(frame, {
          provider: frame.params.provider,
          model: frame.params.model,
          reasoningEffort: frame.params.reasoningEffort ?? null,
        });
      }
      return;
    }
    if (method === 'session/steer') {
      this._result(frame, { messageId: `message-${frame.id}` });
      return;
    }
    if (method === 'session/wait') { this._result(frame, {}); return; }
    if (method === 'shutdown') {
      this._result(frame, {});
      if (!this.ignoreShutdown) this.finish(0, null);
      return;
    }
    this._error(frame, Object.assign(new Error(`method not found: ${method}`), { code: -32601 }));
  }

  _result(frame, result) {
    this.stdout.emit('data', `${JSON.stringify({ jsonrpc: '2.0', id: frame.id, result })}\n`);
  }

  _error(frame, error) {
    const code = Number.isSafeInteger(error.code) ? error.code : -32603;
    this.stdout.emit('data', `${JSON.stringify({
      jsonrpc: '2.0',
      id: frame.id,
      error: { code, message: error.message, ...(error.data === undefined ? {} : { data: error.data }) },
    })}\n`);
  }

  requests(method) { return this.writes.filter((frame) => frame.method === method); }

  finish(code = 0, signal = null) {
    if (this.exitCode !== null) return;
    this.exitCode = code;
    this.signalCode = signal;
    this.emit('exit', code, signal);
  }

  kill() { this.finish(143, 'SIGTERM'); }
}

function newAgent({
  store = makeStore(), config = baseConfig(), childOptions = {}, selectError = null, options = {},
} = {}) {
  const children = [];
  const failures = [];
  const warnings = [];
  const spawn = () => {
    const child = new FakeChild({ ...childOptions, selectError });
    children.push(child);
    return child;
  };
  const logger = { ...quietLogger, warn: (event, fields) => warnings.push({ event, fields }) };
  const agent = new DshAgent(config, store, logger, {
    spawn,
    versionProbe: async () => '0.2.2-rc.1',
    environment: { PATH: '/usr/bin:/bin', DEEPSEEK_API_KEY: 'unit-test-key' },
    startTimeoutMs: 2_000,
    controlTimeoutMs: 1_000,
    shutdownTimeoutMs: 20,
    processExitTimeoutMs: 10,
    reconnectBaseMs: 5,
    reconnectCapMs: 15,
    onTurnFailed: (info) => failures.push(info),
    ...options,
  });
  return { agent, children, store, config, failures, warnings };
}

async function startAgent(context) {
  context.runtime = new AgentRuntime(context.config, context.store, quietLogger, {});
  context.runtime.runtime = 'dsh';
  context.runtime.backend = adaptRuntime('dsh', context.agent);
  await context.agent.start();
  await flush();
  return context.children[0];
}

// The /model command path, from the daemon's own dispatcher down.
function selectThroughSlashCommand(context, arg) {
  return executeCommand({ name: 'model', arg }, context.runtime);
}

export {
  quietLogger, flush, waitFor, tempDir, baseConfig, makeStore, DESCRIPTOR,
  DEFAULT_MODELS, defaultModelInfo, FakeChild, newAgent, startAgent,
  selectThroughSlashCommand,
};
