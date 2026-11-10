// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Fixture shared by the dsh-*.test.mjs suites: a fake DSH child
// that speaks the bridge wire protocol, a store double, and the agent factory.
// No product code imports this module.

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { after } from 'node:test';
import { DshAgent } from '../src/dsh.mjs';

const quietLogger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };
const flush = () => new Promise((resolve) => setImmediate(resolve));

async function waitFor(predicate, timeoutMs = 1_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
  throw new Error('timed out waiting for condition');
}

// Every directory this suite creates is tracked so the run leaves nothing
// behind: a suite that leaks a few directories per case filled /tmp with
// thousands of them. `after` removes them once, whatever the outcome.
const createdTempDirs = new Set();

function tempDir() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-dsh-agent-test-'));
  createdTempDirs.add(directory);
  return directory;
}

after(() => {
  for (const directory of createdTempDirs) {
    try {
      fs.rmSync(directory, { recursive: true, force: true });
    } catch {}
  }
  createdTempDirs.clear();
});

function baseConfig(overrides = {}) {
  const dataDir = overrides.dataDir ?? tempDir();
  return {
    dshBin: '/opt/dsh/bin/dsh',
    dshEffort: 'high',
    workspace: overrides.workspace ?? tempDir(),
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

function makeStore({ globalOverride = {}, runtime = 'dsh', chats = null } = {}) {
  let global = {
    sessionId: null,
    policyHash: null,
    runtime: null,
    epoch: 0,
    syncedThroughSeq: 0,
    sessionReady: false,
    ...globalOverride,
  };
  const settings = new Map();
  const activities = [];
  const prepared = [];
  const rows = [
    { seq: 1, epoch: 1, kind: 'inbound', inboundSeq: 1, text: 'earlier question' },
    { seq: 2, epoch: 1, kind: 'outbound', inboundSeq: null, text: 'earlier answer' },
    { seq: 3, epoch: 1, kind: 'inbound', inboundSeq: 7, text: 'current request' },
  ];
  return {
    activities,
    settings,
    prepared,
    getCognitionState: () => ({ runtime, epoch: 1, authorityToken: 'dsh-token' }),
    getGlobalSession: () => global,
    global: () => global,
    setGlobalSession(sessionId, policyHash, fields) {
      global = { sessionId, policyHash, ...fields };
    },
    publishOwnedSession({ sessionId, policyHash, runtime: owner, epoch, acceptedThroughSeq, sessionReady }) {
      global = { sessionId, policyHash, runtime: owner, epoch, syncedThroughSeq: acceptedThroughSeq, sessionReady };
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
    // Present only when a case cares where its input came from: `scopeKey` is
    // what a failure notice is addressed and muted by.
    ...(chats === null ? {} : {
      inboundBySeq: (seq) => (chats[seq]
        ? { seq, scopeKey: chats[seq], conversationKey: chats[seq] }
        : null),
    }),
    getSetting: (key) => settings.get(key) ?? null,
    setSetting(key, value) { settings.set(key, value); },
    recordActivity(activity) { activities.push(activity); },
    getOVContext: () => ({ sessionId: 'ov-session', latest_archive_overview: '', messages: [
      { source_message_ids: ['lark:1:1'], parts: [{ type: 'text', text: 'earlier question' }] },
      { source_message_ids: ['lark:1:2'], parts: [{ type: 'text', text: 'earlier answer' }] },
    ] }),
    cognitionJournal(epoch, through, after = 0) {
      return rows.filter((row) => row.seq > after && row.seq <= through);
    },
    cognitionJournalBeforeInbound(inboundSeq) {
      const row = rows.find((item) => item.kind === 'inbound' && item.inboundSeq === inboundSeq);
      return row ? row.seq - 1 : rows.at(-1).seq;
    },
    cognitionJournalSeqForInbound(inboundSeq) {
      return rows.find((item) => item.kind === 'inbound' && item.inboundSeq === inboundSeq)?.seq ?? 0;
    },
    async prepareOVContext(epoch) { prepared.push(epoch); },
  };
}

// The DSH model catalogue every fake process answers by default: two usable
// deepseek-official models plus one route without models (an account that is
// not logged in), which /model must never list.
const DEFAULT_MODELS = Object.freeze([
  {
    provider: 'deepseek-official', name: 'DeepSeek', error: null, credential: null,
    models: [
      { id: 'deepseek-flash', name: 'DeepSeek Flash', inputModalities: ['text'] },
      { id: 'deepseek-v4-pro', name: 'DeepSeek V4 Pro', inputModalities: ['text', 'image'] },
    ],
  },
  { provider: 'deepseek-account', name: 'DeepSeek Account', models: [], error: null, credential: null },
]);

const DEFAULT_SETTINGS_DESCRIPTOR = Object.freeze({
  ns: 'llm-pi-ai', revision: 1, value: { providers: {} },
});

function defaultModelInfo(provider, model) {
  return {
    provider, id: model, name: model, inputModalities: ['text'],
    reasoning: { efforts: [{ id: 'low', name: 'Low' }, { id: 'high', name: 'High' }], defaultEffort: 'high' },
  };
}

class FakeChild extends EventEmitter {
  constructor({
    autoHandshake = true, resumeError = null, hello = null, synchronousAck = false,
    silentMethods = [], steerResult = null, writeBlocked = false,
    models = DEFAULT_MODELS, modelInfo = null, selectError = null,
    settingsDescriptor = DEFAULT_SETTINGS_DESCRIPTOR,
  } = {}) {
    super();
    this.pid = 987_650_000 + FakeChild.nextPid++;
    this.exitCode = null;
    this.signalCode = null;
    this.writes = [];
    this.autoHandshake = autoHandshake;
    this.resumeError = resumeError;
    this.models = models;
    this.modelInfo = modelInfo;
    this.selectError = selectError;
    // null models an `llm-pi-ai` document DSH could not activate.
    this.settingsDescriptor = settingsDescriptor;
    this.hello = hello ?? {
      bridge: 'vika-dsh-bridge',
      version: 2,
      methods: [
        'session/steer', 'session/cancel',
        'vika/llm/models', 'vika/llm/model/info', 'vika/session/model/select',
      ],
    };
    this.synchronousAck = synchronousAck;
    // Methods this child never answers, to model an SDK that goes quiet.
    this.silentMethods = new Set(silentMethods);
    // A fixed steer result (e.g. {} for a malformed acknowledgement).
    this.steerResult = steerResult;
    this.writeBlocked = writeBlocked;
    this.ignoreShutdown = false;
    this.ignoreSignals = false;
    this.respondedIds = new Set();
    this.stdin = new EventEmitter();
    this.stdin.writable = true;
    this.stdin.write = (value) => {
      const frame = JSON.parse(value);
      this.writes.push(frame);
      this._answer(frame);
      // Backpressure: the first frame is buffered but the stream says "wait for
      // drain", exactly like a full OS pipe.
      if (this.writeBlocked) {
        this.writeBlocked = false;
        return false;
      }
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
    if (!frame.method || this.silentMethods.has(frame.method)) return;
    const answer = () => {
      if (this.exitCode !== null) return;
      if (frame.method === 'initialize') this.feed({ id: frame.id, result: { serverInfo: { name: 'deepseek-harness-sdk-runtime', version: '0.2.2-rc.1' } } });
      else if (frame.method === 'vika/hello') {
        if (this.hello instanceof Error) this.feed({ id: frame.id, error: { code: -32601, message: this.hello.message } });
        else this.feed({ id: frame.id, result: this.hello });
      } else if (frame.method === 'session/resume') {
        if (this.resumeError) this.feed({ id: frame.id, error: { code: -32603, message: this.resumeError } });
        else this.feed({ id: frame.id, result: {} });
      } else if (frame.method === 'session/working-directory/get') {
        this.feed({ id: frame.id, result: { cwd: '/tmp/vika-workspace' } });
      } else if (frame.method === 'vika/settings/describe') {
        this.feed({ id: frame.id, result: { ns: 'llm-pi-ai', descriptor: this.settingsDescriptor } });
      } else if (frame.method === 'vika/llm/models') {
        this.feed({ id: frame.id, result: { providers: this.models } });
      } else if (frame.method === 'vika/llm/model/info') {
        this.feed({ id: frame.id, result: (this.modelInfo ?? defaultModelInfo)(frame.params.provider, frame.params.model) });
      } else if (frame.method === 'vika/session/model/select') {
        if (this.selectError) this.feed({ id: frame.id, error: this.selectError });
        else {
          this.feed({ id: frame.id, result: {
            provider: frame.params.provider,
            model: frame.params.model,
            reasoningEffort: frame.params.reasoningEffort ?? null,
          } });
        }
      } else if (frame.method === 'session/steer' && (this.synchronousAck || this.steerResult !== null)) {
        this.feed({ id: frame.id, result: this.steerResult ?? { messageId: `sync-${frame.id}` } });
      } else if (frame.method === 'shutdown' && !this.ignoreShutdown) {
        this.feed({ id: frame.id, result: {} });
        this.finish(0, null);
      }
    };
    if (this.synchronousAck || this.steerResult !== null) answer();
    else queueMicrotask(answer);
  }

  drain() { this.stdin.emit('drain'); }

  feed(value) {
    if (value?.id !== undefined && value?.method === undefined) this.respondedIds.add(value.id);
    this.stdout.emit('data', `${JSON.stringify(value)}\n`);
  }
  respond(request, result = {}) {
    const value = { jsonrpc: '2.0', id: request.id, result };
    this.respondedIds.add(value.id);
    this.feed(value);
  }
  reject(request, code, message) { this.feed({ jsonrpc: '2.0', id: request.id, error: { code, message } }); }
  sent() { return [...this.writes]; }
  requests(method) { return this.writes.filter((frame) => frame.method === method); }
  finish(code = 0, signal = null) {
    if (this.exitCode !== null) return;
    this.exitCode = code;
    this.signalCode = signal;
    this.emit('exit', code, signal);
  }
  kill(signal = 'SIGTERM') {
    if (this.ignoreSignals) return;
    this.finish(signal === 'SIGKILL' ? 137 : 143, signal);
  }
}

function newAgent({ store = makeStore(), config = baseConfig(), options = {}, childOptions = {} } = {}) {
  const children = [];
  const spawnCalls = [];
  const spawn = (bin, args, spawnOptions) => {
    const child = new FakeChild(
      typeof childOptions === 'function' ? childOptions(children.length) : childOptions,
    );
    children.push(child);
    spawnCalls.push({ bin, args, options: spawnOptions, child });
    return child;
  };
  const failures = [];
  const successes = [];
  const abandoned = [];
  const warnings = [];
  const logger = {
    ...quietLogger,
    warn: (event, fields) => warnings.push({ event, fields }),
  };
  const agent = new DshAgent(config, store, logger, {
    spawn,
    versionProbe: async () => '0.2.2-rc.1',
    environment: { PATH: '/usr/bin:/bin', DEEPSEEK_API_KEY: 'unit-test-key' },
    startTimeoutMs: 500,
    controlTimeoutMs: 300,
    shutdownTimeoutMs: 20,
    processExitTimeoutMs: 10,
    reconnectBaseMs: 5,
    reconnectCapMs: 15,
    onTurnFailed: (info) => failures.push(info),
    onTurnSucceeded: (info) => successes.push(info),
    onTurnAbandoned: (info) => abandoned.push(info),
    ...options,
  });
  return { agent, children, spawnCalls, store, config, failures, successes, abandoned, warnings };
}

async function startAgent(context) {
  await context.agent.start();
  await flush();
  return context.children[0];
}

async function acknowledge(child, steer, messageId = `message-${steer.id}`, observe = true) {
  child.respond(steer, { messageId });
  await flush();
  if (observe) {
    child.feed({
      jsonrpc: '2.0', method: 'session.event',
      params: {
        sessionId: steer.params.sessionId,
        event: { type: 'user/message', seq: 1, time: 1, data: { id: messageId, role: 'user', content: [], source: { kind: 'user' } } },
      },
    });
  }
}

function event(child, sessionId, type, data) {
  child.feed({ jsonrpc: '2.0', method: 'session.event', params: { sessionId, event: { type, seq: 1, time: 1, data } } });
}

async function settle(child, agent, reason = { kind: 'completed' }) {
  event(child, agent.threadId, 'turn/end', { turn: 1, reason });
  for (let round = 0; round < 8 && agent.turn; round += 1) {
    const pendingWaits = child.requests('session/wait').filter((request) => agent.pendingRpc.has(request.id));
    for (const request of pendingWaits) child.respond(request, {});
    await flush();
  }
  await waitFor(() => !agent.turn).catch((error) => {
    throw new Error(`${error.message}: turn=${JSON.stringify(agent.turn)} pending=${JSON.stringify([...agent.pendingRpc.keys()])} responses=${JSON.stringify([...child.respondedIds])} writes=${JSON.stringify(child.sent())}`);
  });
}

// The profile patch under <DSH_HOME>/profiles/sdk/ is the only model
// configuration source; the daemon watches its content and restarts only when it
// really changed.
function profilePatchFile(config) {
  return path.join(config.dataDir, 'dsh', 'home', 'profiles', 'sdk', 'cordis.patch.yml');
}

function writeProfilePatch(config, value) {
  const file = profilePatchFile(config);
  // The daemon owns <dataDir>/dsh and asserts it stays private (0700).
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  fs.writeFileSync(file, JSON.stringify(value, null, 2));
  return file;
}


export {
  quietLogger, flush, waitFor, tempDir, baseConfig, makeStore,
  DEFAULT_MODELS, DEFAULT_SETTINGS_DESCRIPTOR, defaultModelInfo, FakeChild,
  newAgent, startAgent, acknowledge, event, settle,
  profilePatchFile, writeProfilePatch,
};
