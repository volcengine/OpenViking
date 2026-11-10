// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Layer: coordinator + DSH handshake -- what the daemon does when the route it
// was configured to run serves nothing.
//
// The failure this file pins down: `ov-superagent setup` selected DeepSeek Harness and
// wrote the bare runtime alias `"model": "dsh"`, which resolves to dsh's own
// default `deepseek-official/deepseek-flash`. That route is registered in DSH but
// serves ZERO models without DEEPSEEK_API_KEY -- the owner's credential lives on
// an operator gateway route instead -- so the process could not start and the
// daemon exited into a pm2 restart loop.
//
// Both startup paths reach that state and both are covered:
//   - a fresh instance (what setup just created) starts straight from the
//     manifest and never goes through a runtime switch;
//   - an existing instance switches runtimes through the staged `_hardSwitch`.
// The case is owned by the handshake that has the live child and its catalogue;
// the coordinator only decides whether a substitution is allowed at all.
//
// Every case drives the real DshAgent over a fake bridge process (one per spawned
// child) and the real AgentRuntime, so the wiring under test is the shipped one.

import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { after } from 'node:test';
import { describe, it } from './dsh-node-gate.mjs';
import { AgentRuntime } from '../src/agent-runtime.mjs';
import { DshAgent } from '../src/dsh.mjs';

const quietLogger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

// Every directory this suite creates is tracked so the run leaves nothing behind.
const createdDirs = new Set();

function tempDir() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-dsh-fallback-'));
  fs.chmodSync(directory, 0o700);
  createdDirs.add(directory);
  return directory;
}

after(() => {
  for (const directory of createdDirs) {
    try { fs.rmSync(directory, { recursive: true, force: true }); } catch {}
  }
  createdDirs.clear();
});

function baseConfig(overrides = {}) {
  const dataDir = overrides.dataDir ?? tempDir();
  return {
    dshBin: '/opt/dsh/bin/dsh',
    dshEffort: 'high',
    initialModel: '',
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

// The store double: the cognition authority, the global session and the settings
// table, which is everything AgentRuntime and DshAgent read or write here. A
// missing `dsh_model` is `null`, exactly like the real store.
function makeStore({ runtime = null, authorityToken = 'token-before', settings = {} } = {}) {
  let cognition = { runtime, epoch: 1, authorityToken: runtime ? authorityToken : null };
  let global = {
    sessionId: null, policyHash: null, runtime: null, epoch: 0,
    syncedThroughSeq: 0, sessionReady: false,
  };
  const values = new Map(Object.entries(settings));
  const journal = [];
  // What the agent recorded as activity: the substitution at startup is asserted
  // from here, because a daemon log is not something the operator sees.
  const activities = [];
  return {
    settings: values,
    activities,
    recordActivity(activity) { activities.push(activity); },
    getCognitionState: () => ({ ...cognition }),
    hasCognitionRuntime: () => Boolean(cognition.runtime),
    initializeCognition(owner, token) {
      cognition = { runtime: owner, epoch: cognition.epoch, authorityToken: token };
      return { ...cognition };
    },
    getGlobalSession: () => global,
    getSetting: (key) => values.get(key) ?? null,
    setSetting(key, value) { values.set(key, String(value)); },
    setRuntimeSettings(owner, { model, effort }) {
      values.set(`${owner}_model`, String(model));
      if (effort !== undefined) values.set(`${owner}_effort`, String(effort));
    },
    publishOwnedSession({ sessionId, policyHash, runtime: owner, epoch, acceptedThroughSeq, sessionReady }) {
      global = { sessionId, policyHash, runtime: owner, epoch, syncedThroughSeq: acceptedThroughSeq, sessionReady };
      return true;
    },
    advanceOwnedSessionAcceptance() { return true; },
    markOwnedSessionUnresumable() { return true; },
    cognitionJournalHead: () => 0,
    cognitionJournal: () => [],
    cognitionJournalBeforeInbound: () => 0,
    cognitionJournalSeqForInbound: () => 0,
    getOVContext: () => null,
    commitRuntimeSwitch({
      runtime: owner, authorityToken: token, sessionId, policyHash, epoch,
      syncedThroughSeq, model, effort, expectedRuntime,
    }) {
      // The real store CASes the authority it is replacing; a switch that lost
      // the race must not be recorded as committed.
      if (expectedRuntime !== undefined && expectedRuntime !== cognition.runtime) {
        throw new Error('cognition authority changed during runtime switch');
      }
      cognition = { runtime: owner, epoch, authorityToken: token };
      global = { sessionId, policyHash, runtime: owner, epoch, syncedThroughSeq, sessionReady: true };
      values.set(`${owner}_model`, String(model));
      if (effort !== undefined) values.set(`${owner}_effort`, String(effort));
      journal.push({ kind: 'commit', owner, model });
      return true;
    },
    appendCognitionJournal(entry) { journal.push(entry); },
    async prepareOVContext() {},
  };
}

const DAEMON_METHODS = Object.freeze([
  'session/steer', 'session/cancel',
  'vika/llm/models', 'vika/llm/model/info', 'vika/session/model/select',
]);

// The catalogue of the machine that crash-looped: the built-in route exists but
// serves ZERO models (its credential lives on the operator gateway route), and
// only the operator route lists anything.
const UNUSABLE_DEFAULT = Object.freeze([
  { provider: 'deepseek-official', name: 'DeepSeek', models: [], error: null, credential: null },
  {
    provider: 'deepseek', name: 'DeepSeek', error: null, credential: null,
    models: [{ id: 'deepseek-chat', name: 'DeepSeek Chat', inputModalities: ['text'] }],
  },
]);

// The healthy catalogue: the runtime's own default is servable.
const SERVED_DEFAULT = Object.freeze([
  {
    provider: 'deepseek-official', name: 'DeepSeek', error: null, credential: null,
    models: [
      { id: 'deepseek-flash', name: 'DeepSeek Flash', inputModalities: ['text'] },
      { id: 'deepseek-v4-pro', name: 'DeepSeek V4 Pro', inputModalities: ['text'] },
    ],
  },
]);

// What a real dsh answers for a route whose credential is missing: NOTHING. Its
// adapter registers the provider unconditionally, so `initialize` succeeds and the
// route simply lists no models (measured on 0.2.2-rc.1 -- see the file header).
// Kept as a named constant because a fake that REJECTS here would encode a
// premise real DSH does not have.
const ACCEPT_EVERYTHING = null;

// What a real dsh answers for a route it has no adapter for: `NO_ADAPTER` at
// initialize (this one really is a refusal).
const REFUSE_DEEPSEEK = (provider) => (provider === 'deepseek'
  ? Object.assign(new Error('no adapter registered for provider "deepseek"'), {
    code: -32603, data: { code: 'NO_ADAPTER' },
  })
  : null);

// One fake DSH child per spawned process, speaking the bridge v2 surface the
// handshake and the model paths use.
class FakeChild extends EventEmitter {
  constructor({ catalogue = SERVED_DEFAULT, refuse = null } = {}) {
    super();
    this.pid = 760_000 + (FakeChild.nextPid += 1);
    this.exitCode = null;
    this.signalCode = null;
    this.writes = [];
    this.catalogue = catalogue;
    this.refuse = refuse;
    this.stdin = new EventEmitter();
    this.stdin.writable = true;
    this.stdin.write = (value) => {
      const frame = JSON.parse(value);
      this.writes.push(frame);
      queueMicrotask(() => this._reply(frame));
      return true;
    };
    this.stdin.end = () => {};
    this.stdout = new EventEmitter();
    this.stdout.setEncoding = () => {};
    this.stderr = new EventEmitter();
    this.stderr.setEncoding = () => {};
  }

  static nextPid = 1;

  requests(method) { return this.writes.filter((frame) => frame.method === method); }

  _reply(frame) {
    if (this.exitCode !== null || !frame.method) return;
    const method = frame.method;
    if (method === 'initialize') {
      const error = this.refuse?.(frame.params.provider, frame.params.model);
      if (error) this._error(frame, error);
      // Real DSH answers initialize from the adapter and does NOT check whether
      // the route can serve anything; the catalogue is the only place that says
      // so. This fake must keep that split, or a test can prove a fix against a
      // premise the real binary does not have.
      else this._result(frame, {
        serverInfo: { name: 'deepseek-harness-sdk-runtime', version: '0.0.1' },
      });
      return;
    }
    if (method === 'vika/hello') {
      this._result(frame, { bridge: 'vika-dsh-bridge', version: 2, methods: [...DAEMON_METHODS] });
      return;
    }
    if (method === 'vika/llm/models') { this._result(frame, { providers: this.catalogue }); return; }
    if (method === 'vika/llm/model/info') {
      this._result(frame, {
        provider: frame.params.provider,
        id: frame.params.model,
        name: frame.params.model,
        inputModalities: ['text'],
        reasoning: {
          efforts: [{ id: 'low', name: 'Low' }, { id: 'high', name: 'High' }],
          defaultEffort: 'high',
        },
      });
      return;
    }
    if (method === 'vika/settings/describe') {
      this._result(frame, {
        ns: 'llm-pi-ai', descriptor: { ns: 'llm-pi-ai', revision: 1, value: { providers: {} } },
      });
      return;
    }
    if (method === 'session/working-directory/get') { this._result(frame, { cwd: '/tmp/workspace' }); return; }
    if (method === 'session/resume') { this._result(frame, {}); return; }
    if (method === 'vika/session/model/select') {
      this._result(frame, {
        provider: frame.params.provider,
        model: frame.params.model,
        reasoningEffort: frame.params.reasoningEffort ?? null,
      });
      return;
    }
    if (method === 'session/steer') { this._result(frame, { messageId: `message-${frame.id}` }); return; }
    if (method === 'session/wait') { this._result(frame, {}); return; }
    if (method === 'shutdown') { this._result(frame, {}); this.finish(0, null); return; }
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

  finish(code = 0, signal = null) {
    if (this.exitCode !== null) return;
    this.exitCode = code;
    this.signalCode = signal;
    this.emit('exit', code, signal);
  }

  kill() { this.finish(143, 'SIGTERM'); }
}

// A plain coordinator-side backend, for "the runtime being left behind".
function codexBackend() {
  return {
    model: 'gpt-5.6-sol',
    effort: 'medium',
    threadId: 'codex-session',
    resolvedModel: '',
    state: { connected: false, activeTurn: 0, queued: 0 },
    turnInProgress: false,
    get sessionDescriptor() {
      return { sessionId: 'codex-session', policyHash: 'codex-policy', sessionReady: true };
    },
    async start() { this.state.connected = true; },
    async stop() { this.state.connected = false; },
    async listModels() { return [{ id: 'gpt-5.6-sol', efforts: ['medium', 'high'] }]; },
    async interruptTurn() { this.interrupted = (this.interrupted ?? 0) + 1; },
    activateSession() {},
    applySettings({ model, effort }) { this.model = model; this.effort = effort; },
  };
}

// A backend good enough for the staged-switch path, where the case under test is
// the coordinator's own ladder rather than a real handshake.
function stagedBackend({ model = '', effort = 'high', catalogue = [], accepted = [] } = {}) {
  return {
    model,
    effort,
    threadId: 'staged-session',
    resolvedModel: '',
    state: { connected: false, activeTurn: 0, queued: 0 },
    turnInProgress: false,
    get sessionDescriptor() {
      return { sessionId: `staged-${model}`, policyHash: 'staged-policy', sessionReady: true };
    },
    async start() {
      if (!accepted.includes(this.model)) {
        throw Object.assign(new Error(`model is unavailable: ${this.model}`), { code: 'MODEL_UNAVAILABLE' });
      }
      this.state.connected = true;
    },
    async stop() { this.state.connected = false; },
    async listModels() { return catalogue; },
    activateSession() {},
    applySettings({ model: next, effort: nextEffort }) { this.model = next; this.effort = nextEffort; },
  };
}

// The runtime + the fake DSH processes it spawned + every warn it logged.
function makeRuntime({ store, initialModel = '', catalogue = SERVED_DEFAULT, refuse = null, dshFactory = null } = {}) {
  const config = baseConfig({ initialModel });
  const children = [];
  const warnings = [];
  const logger = { ...quietLogger, warn: (event, fields) => warnings.push({ event, fields }) };
  const runtime = new AgentRuntime(config, store, logger, {
    codexFactory: () => codexBackend(),
    dshFactory: dshFactory ?? ((options) => new DshAgent(config, store, logger, {
      ...options,
      spawn: () => {
        const child = new FakeChild({ catalogue, refuse });
        children.push(child);
        return child;
      },
      versionProbe: async () => '0.2.2-rc.1',
      environment: { PATH: '/usr/bin:/bin', DEEPSEEK_API_KEY: 'unit-test-key' },
      startTimeoutMs: 2_000,
      controlTimeoutMs: 1_000,
      shutdownTimeoutMs: 20,
      processExitTimeoutMs: 10,
      reconnectBaseMs: 5,
      reconnectCapMs: 15,
    })),
  });
  return { runtime, config, children, warnings, store };
}

const pairs = (child) => child.requests('initialize').map((request) => ({
  provider: request.params.provider, model: request.params.model,
}));

describe('a fresh instance whose configured route serves nothing', () => {
  // THE REAL SHAPE (measured on dsh 0.2.2-rc.1): initialize on the built-in
  // default is ACCEPTED, and the route then serves zero models. A trigger that
  // waits for a refusal never fires here, which is what the first version of this
  // fix got wrong.
  it('starts on the first servable catalogue route when initialize is accepted but serves nothing', async () => {
    const store = makeStore();
    assert.equal(store.hasCognitionRuntime(), false, 'the state `ov-superagent setup` leaves behind');
    const context = makeRuntime({
      store, initialModel: 'dsh', catalogue: UNUSABLE_DEFAULT, refuse: ACCEPT_EVERYTHING,
    });

    await context.runtime.start();

    assert.equal(context.runtime.runtime, 'dsh');
    assert.equal(context.runtime.model, 'deepseek/deepseek-chat',
      'the process must run a route the catalogue actually serves');
    // In the same process: the handshake asks the catalogue and re-initializes.
    assert.equal(context.children.length, 1);
    assert.deepEqual(pairs(context.children[0]), [
      { provider: 'deepseek-official', model: 'deepseek-flash' },
      { provider: 'deepseek', model: 'deepseek-chat' },
    ], 'the configured route is initialized first, then the catalogue route');
    // Both halves of the operator signal: what was unservable, and what replaced it.
    assert.ok(context.warnings.some((entry) => entry.event === 'dsh_model_unservable'),
      'the unservable route must be logged');
    assert.ok(context.warnings.some((entry) => entry.event === 'dsh_model_adopted'),
      'the adoption must be logged');
    // Logging alone is not enough: an answer from the adopted route looks normal,
    // so the substitution has to be visible where the other runtimes report theirs.
    const fallback = context.store.activities.filter((item) => item.kind === 'agent.fallback');
    assert.equal(fallback.length, 1, 'exactly one substitution is recorded');
    assert.equal(fallback[0].status, 'SWITCHED');
    assert.match(fallback[0].summary, /deepseek\/deepseek-chat/);
    assert.equal(fallback[0].metadata.requested, 'deepseek-official/deepseek-flash');
    assert.equal(fallback[0].metadata.model, 'deepseek/deepseek-chat');
    assert.equal(fallback[0].metadata.trigger, 'dsh-startup');
    // A first start with no stored choice saves the route it really runs, so the
    // next start does not walk into the dead one again.
    assert.equal(store.getSetting('dsh_model'), 'deepseek/deepseek-chat');
    await context.runtime.stop();
  });

  it('starts cleanly on the next run once the adopted route is persisted', async () => {
    const store = makeStore();
    const first = makeRuntime({
      store, initialModel: 'dsh', catalogue: UNUSABLE_DEFAULT, refuse: ACCEPT_EVERYTHING,
    });
    await first.runtime.start();
    assert.equal(first.runtime.model, 'deepseek/deepseek-chat');
    await first.runtime.stop();

    const second = makeRuntime({
      store, initialModel: 'dsh', catalogue: UNUSABLE_DEFAULT, refuse: ACCEPT_EVERYTHING,
    });
    await second.runtime.start();
    const child = second.children[0];

    assert.deepEqual(pairs(child), [{ provider: 'deepseek', model: 'deepseek-chat' }],
      'the persisted route must be the first, and only, one tried');
    assert.equal(second.warnings.some((entry) => entry.event === 'dsh_model_adopted'), false,
      'nothing had to be substituted the second time');
    assert.equal(second.runtime.model, 'deepseek/deepseek-chat');
    await second.runtime.stop();
  });

  // The refusal shape is a different real one (an operator route whose profile was
  // removed, so dsh answers NO_ADAPTER at initialize). Both shapes must reach the
  // catalogue: this one refuses the configured route, then the built-in default is
  // accepted but unservable, and the catalogue route is what finally serves.
  it('also adopts from the catalogue when initialize is refused', async () => {
    const store = makeStore({ settings: { dsh_model: 'deepseek/retired' } });
    // Only the retired route has no adapter; the catalogue's deepseek model and the
    // built-in default both initialize (the default just serves nothing).
    const refuse = (provider, model) => (model === 'retired'
      ? Object.assign(new Error('no adapter registered for provider "deepseek"'), {
        code: -32603, data: { code: 'NO_ADAPTER' },
      })
      : null);
    const context = makeRuntime({
      store, initialModel: 'dsh', catalogue: UNUSABLE_DEFAULT, refuse,
    });

    await context.runtime.start();

    assert.equal(context.runtime.model, 'deepseek/deepseek-chat',
      'a refused route must not end the start when the catalogue has a servable one');
    assert.deepEqual(pairs(context.children[0]), [
      { provider: 'deepseek', model: 'retired' },
      { provider: 'deepseek-official', model: 'deepseek-flash' },
      { provider: 'deepseek', model: 'deepseek-chat' },
    ], 'refused route, then the built-in default, then the catalogue route');
    // A stored choice is the user's: it is not rewritten by the substitution.
    assert.equal(store.getSetting('dsh_model'), 'deepseek/retired');
    await context.runtime.stop();
  });

  it('leaves a runtime whose default is servable on it', async () => {
    const store = makeStore();
    const context = makeRuntime({
      store, initialModel: 'dsh', catalogue: SERVED_DEFAULT, refuse: ACCEPT_EVERYTHING,
    });

    await context.runtime.start();

    assert.equal(context.runtime.model, 'deepseek-official/deepseek-flash');
    assert.deepEqual(pairs(context.children[0]), [
      { provider: 'deepseek-official', model: 'deepseek-flash' },
    ]);
    assert.equal(store.getSetting('dsh_model'), null, 'nothing was substituted, so nothing is written');
    assert.equal(context.warnings.some((entry) => entry.event === 'dsh_model_adopted'), false);
    assert.equal(context.warnings.some((entry) => entry.event === 'dsh_model_unservable'), false);
    await context.runtime.stop();
  });

  it('keeps a stored route that serves nothing instead of overwriting it', async () => {
    // The design's constraint: a substitution is saved only for a first start
    // that has no setting. A stored choice belongs to the user, so this start
    // still runs a servable route -- but leaves the setting for them to fix.
    const store = makeStore({ settings: { dsh_model: 'deepseek/retired' } });
    const context = makeRuntime({
      store, initialModel: 'dsh', catalogue: UNUSABLE_DEFAULT, refuse: ACCEPT_EVERYTHING,
    });

    await context.runtime.start();

    assert.equal(context.runtime.model, 'deepseek/deepseek-chat', 'the daemon must still come up');
    assert.equal(store.getSetting('dsh_model'), 'deepseek/retired', 'the user\'s stored choice stays');
    await context.runtime.stop();
  });

  it('does not read a failed catalogue probe as "nothing is servable"', async () => {
    // `_catalogueRoutes` returns [] both when the bridge cannot answer and when it
    // answered that no route serves anything. Only the second may move a start:
    // treating an unanswered probe as a verdict would silently reroute a healthy
    // instance on any transient RPC failure.
    const store = makeStore();
    const context = makeRuntime({
      store, initialModel: 'dsh', catalogue: [], refuse: ACCEPT_EVERYTHING,
    });

    await context.runtime.start();

    assert.equal(context.runtime.model, 'deepseek-official/deepseek-flash',
      'an empty catalogue answer offers nothing to adopt, so the started route stays');
    assert.equal(context.warnings.some((entry) => entry.event === 'dsh_model_adopted'), false);
    await context.runtime.stop();
  });
});

describe('a switch onto a runtime whose configured route serves nothing', () => {
  it('falls back to a catalogue route and commits instead of failing the switch', async () => {
    const store = makeStore({ runtime: 'codex' });
    const context = makeRuntime({ store, catalogue: UNUSABLE_DEFAULT, refuse: ACCEPT_EVERYTHING });
    await context.runtime.start();
    assert.equal(context.runtime.runtime, 'codex');

    // `/runtime dsh` names no model: the runtime's own default is the target.
    const result = await context.runtime.transition({ kind: 'select-runtime', runtime: 'dsh' });

    assert.equal(result.ok, true, 'the previous turn was already interrupted, so this must commit');
    assert.equal(result.runtime, 'dsh');
    assert.deepEqual(result.modelFallback, {
      from: 'deepseek-official/deepseek-flash', to: 'deepseek/deepseek-chat',
    });
    assert.equal(context.runtime.runtime, 'dsh');
    assert.equal(context.runtime.model, 'deepseek/deepseek-chat');
    assert.equal(store.getCognitionState().runtime, 'dsh');
    // The applied pair is persisted by the commit, the same way a normal switch is.
    assert.equal(store.getSetting('dsh_model'), 'deepseek/deepseek-chat');
    assert.ok(context.warnings.some((entry) => entry.event === 'runtime_model_fallback'),
      'the switch must log the substitution');
    await context.runtime.stop();
  });

  it('prefers the runtime default when the catalogue lists it', async () => {
    // A persisted operator route that no longer has an adapter: dsh's own
    // initialize ladder runs the built-in default, and the catalogue proves it.
    const store = makeStore({
      runtime: 'codex', settings: { dsh_model: 'deepseek/retired' },
    });
    const catalogue = [...SERVED_DEFAULT, ...UNUSABLE_DEFAULT.slice(1)];
    const context = makeRuntime({ store, catalogue, refuse: REFUSE_DEEPSEEK });
    await context.runtime.start();

    const result = await context.runtime.transition({ kind: 'select-runtime', runtime: 'dsh' });

    assert.equal(result.ok, true);
    assert.deepEqual(result.modelFallback, {
      from: 'deepseek/retired', to: 'deepseek-official/deepseek-flash',
    });
    // The route the process already proved it starts on is adopted in place: no
    // second process, and nothing the catalogue has not vouched for.
    assert.equal(context.children.length, 1);
    assert.equal(context.runtime.model, 'deepseek-official/deepseek-flash');
    assert.equal(store.getSetting('dsh_model'), 'deepseek-official/deepseek-flash');
    await context.runtime.stop();
  });

  it('restages on the first catalogue route when the started route is not listed', async () => {
    // The one case in-place adoption cannot cover: the target started on the
    // requested model but its catalogue does not list that model, so committing
    // it would run something nothing vouches for. The ladder restarts once on a
    // route the catalogue does list.
    const store = makeStore({ runtime: 'codex' });
    const catalogue = [{
      id: 'deepseek/deepseek-chat', resolvedModel: 'deepseek-chat',
      efforts: ['low', 'high'], effortsKnown: true,
    }];
    const stages = [];
    const context = makeRuntime({
      store,
      dshFactory: (options) => {
        const agent = stagedBackend({
          model: options.model,
          effort: options.effort ?? 'high',
          catalogue,
          accepted: ['deepseek-official/deepseek-flash', 'deepseek/deepseek-chat'],
        });
        stages.push(agent);
        return agent;
      },
    });
    await context.runtime.start();

    const result = await context.runtime.transition({ kind: 'select-runtime', runtime: 'dsh' });

    assert.equal(result.ok, true);
    assert.deepEqual(stages.map((stage) => stage.model), [
      'deepseek-official/deepseek-flash', 'deepseek/deepseek-chat',
    ]);
    assert.equal(stages[0].state.connected, false, 'the unlisted route must be retired');
    assert.deepEqual(result.modelFallback, {
      from: 'deepseek-official/deepseek-flash', to: 'deepseek/deepseek-chat',
    });
    assert.equal(context.runtime.model, 'deepseek/deepseek-chat');
    await context.runtime.stop();
  });
});

describe('an explicitly requested model is never replaced', () => {
  it('refuses it and names the target runtime in the error', async () => {
    const store = makeStore({ runtime: 'codex' });
    const catalogue = UNUSABLE_DEFAULT;
    const context = makeRuntime({ store, catalogue, refuse: ACCEPT_EVERYTHING });
    await context.runtime.start();
    assert.equal(context.runtime.runtime, 'codex');

    // A named model is the caller's choice: dsh's ladder may drop to its own
    // default in-process, but the coordinator must not commit that silently.
    await assert.rejects(
      context.runtime.transition({
        kind: 'select-runtime', runtime: 'dsh', model: 'deepseek/retired',
      }),
      (error) => {
        assert.equal(error.code, 'MODEL_UNAVAILABLE');
        assert.equal(error.model, 'deepseek/retired');
        // The log on the real machine said "durable model is unavailable" while
        // the runtime that could not start was dsh: name the target, not the one
        // being left.
        assert.match(error.message, /^dsh model is unavailable: deepseek\/retired$/);
        return true;
      },
    );

    assert.equal(context.runtime.runtime, 'codex', 'the failed switch must not change the runtime');
    assert.equal(store.getCognitionState().runtime, 'codex');
    assert.equal(store.getSetting('dsh_model'), null, 'a refused switch persists nothing');
    await context.runtime.stop();
  });

  it('does not let the handshake substitute a model the caller asked for', async () => {
    const store = makeStore({ runtime: 'codex' });
    const context = makeRuntime({ store, catalogue: SERVED_DEFAULT, refuse: REFUSE_DEEPSEEK });
    await context.runtime.start();

    // deepseek/retired has no adapter, so dsh's ladder runs the built-in default.
    // That downgrade is allowed inside the process; it is not what commits.
    await assert.rejects(
      context.runtime.transition({
        kind: 'select-runtime', runtime: 'dsh', model: 'deepseek/retired',
      }),
      (error) => {
        assert.equal(error.code, 'MODEL_UNAVAILABLE');
        assert.match(error.message, /^dsh model is unavailable: deepseek\/retired$/);
        return true;
      },
    );

    assert.equal(context.runtime.runtime, 'codex');
    assert.equal(store.getSetting('dsh_model'), null);
    await context.runtime.stop();
  });
});
