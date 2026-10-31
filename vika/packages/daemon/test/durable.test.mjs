// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { describe, it } from 'node:test';
import { DURABLE_VERSION, DURABLE_HOST, sanitizeDurableEnv } from '../src/durable-runtime.mjs';
import { availableChatModels, loadDurableModels, piDefaultModel } from '../src/durable-models.mjs';
import { DurableAgent } from '../src/durable.mjs';

const quietLogger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

function tempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'vika-durable-test-'));
}

function baseConfig(overrides = {}) {
  const dataDir = overrides.dataDir ?? tempDir();
  return {
    durableHostPath: DURABLE_HOST,
    durableModel: '',
    durableEffort: 'medium',
    workspace: overrides.workspace ?? tempDir(),
    dataDir,
    databasePath: path.join(dataDir, 'test.sqlite3'),
    profile: 'test-profile',
    connectors: ['lark'],
    actionSocketPath: path.join(dataDir, 'actions.sock'),
    sendTimeoutMs: 5_000,
    maxReplyChars: 1_000,
    dryRun: true,
    ...overrides,
  };
}

function makeStore({ runtime = 'durable', session = null } = {}) {
  let global = {
    sessionId: null,
    policyHash: null,
    runtime: null,
    epoch: 0,
    syncedThroughSeq: 0,
    sessionReady: false,
    ...(session ?? {}),
  };
  const activities = [];
  const settings = new Map();
  const rows = [
    { seq: 1, epoch: 1, kind: 'inbound', inboundSeq: 1, text: 'earlier question' },
    { seq: 2, epoch: 1, kind: 'outbound', inboundSeq: null, text: 'earlier answer' },
    { seq: 3, epoch: 1, kind: 'inbound', inboundSeq: 7, text: 'current request' },
  ];
  return {
    activities,
    getCognitionState: () => ({ runtime, epoch: 1, authorityToken: 'token-durable' }),
    getGlobalSession: () => global,
    setGlobalSession(sessionId, policyHash, fields) {
      global = { sessionId, policyHash, ...fields };
    },
    publishOwnedSession({ runtime: owner, sessionId, policyHash, epoch, acceptedThroughSeq, sessionReady, nativeSessionId = null }) {
      global = { sessionId, policyHash, runtime: owner, epoch, syncedThroughSeq: acceptedThroughSeq, sessionReady, nativeSessionId };
      return true;
    },
    advanceOwnedSessionAcceptance({ sessionId, acceptedThroughSeq }) {
      if (global.sessionId !== sessionId) return false;
      global = { ...global, syncedThroughSeq: Math.max(global.syncedThroughSeq, acceptedThroughSeq) };
      return true;
    },
    global: () => global,
    getNativeSessionId: () => (global.sessionId ? global.nativeSessionId ?? null : null),
    getSetting: (key) => settings.get(key) ?? null,
    setSetting: (key, value) => settings.set(key, value),
    recordActivity(activity) { activities.push(activity); },
    getOVContext: () => ({ sessionId: 'ov-session', messages: [] }),
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
    prepareOVContext: async () => {},
  };
}

async function waitFor(predicate, timeoutMs = 5_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error('timed out waiting for condition');
}

class FakeChild extends EventEmitter {
  constructor() {
    super();
    this.pid = 987_654_321;
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

// A host answers hello with ready, then the id acknowledgement. Later commands
// are acknowledged by the test itself.
function fakeHarness({ config = baseConfig(), store = makeStore(), version = DURABLE_VERSION, ready = {}, agentOptions = {} } = {}) {
  const children = [];
  const spawn = (_command, _args) => {
    const child = new FakeChild();
    children.push(child);
    const write = child.stdin.write;
    child.stdin.write = (value) => {
      const payload = JSON.parse(value);
      if (payload.type === 'hello') {
        queueMicrotask(() => {
          child.feed({
            type: 'ready', protocol: 1, version, conversationId: 2,
            model: payload.model, effort: payload.effort, ...ready,
          });
          child.feed({ id: payload.id, ok: true });
        });
      }
      return write(value);
    };
    return child;
  };
  const failures = [];
  const successes = [];
  const agent = new DurableAgent(config, store, quietLogger, {
    spawn,
    startTimeoutMs: 200,
    reconnectBaseMs: 20,
    rpcTimeoutMs: 200,
    onTurnFailed: (failure) => failures.push(failure),
    onTurnSucceeded: (success) => successes.push(success),
    ...agentOptions,
  });
  return { agent, children, store, failures, successes };
}

function lastWrite(child) {
  return JSON.parse(child.writes.at(-1));
}

describe('DurableAgent host protocol', () => {
  it('starts the host, submits a batch with a stable request id and completes the turn', async () => {
    const { agent, children, store, failures } = fakeHarness();
    await agent.start();
    const child = children[0];
    const hello = JSON.parse(child.writes[0]);
    assert.equal(hello.type, 'hello');
    assert.equal(hello.sessionKey, agent.threadId);
    // A fresh session asks the host for a new conversation and then publishes
    // the one it got back; the host keeps no index of its own.
    assert.equal(hello.nativeSessionId, null);
    assert.equal(store.global().nativeSessionId, 2);
    assert.match(hello.policy, /Vika|personal|agent/i);
    assert.equal(agent.connected, true);
    assert.equal(agent.state.connected, true);

    agent.deliver('current request', 7);
    const submit = lastWrite(child);
    assert.equal(submit.type, 'submit');
    assert.match(submit.text, /current request/);
    assert.match(submit.requestId, new RegExp(`^durable:${agent.threadId}:7$`));
    assert.equal(agent.turnActive, true);
    assert.equal(agent.turnInProgress, true);

    child.feed({ type: 'turn_start', turn: 1 });
    child.feed({ type: 'note', text: 'working on it' });
    child.feed({ type: 'tool_start', toolId: 'call-1', toolName: 'send_message', input: { text: 'hi' } });
    child.feed({ type: 'usage', inputTokens: 12, cachedInputTokens: 2, outputTokens: 5, costUsd: 0.01 });
    child.feed({ type: 'tool_end', toolId: 'call-1', toolName: 'send_message', isError: false, output: 'sent' });
    child.feed({ type: 'settled', status: 'done', turn: 1 });

    await waitFor(() => !agent.turnActive);
    assert.deepEqual(failures, []);
    const kinds = store.activities.map((activity) => activity.kind);
    assert.ok(kinds.includes('agent.note'));
    assert.ok(kinds.includes('agent.tool_called'));
    assert.ok(kinds.includes('agent.tool_completed'));
    const completed = store.activities.find((activity) => activity.kind === 'agent.turn_completed');
    assert.equal(completed.status, 'completed');
    assert.equal(completed.metadata.inputTokens, 12);
    assert.equal(completed.metadata.cachedInputTokens, 2);
    assert.equal(completed.metadata.costUsd, 0.01);
    await agent.stop();
  });

  it('sends later input while a turn runs and fails the turn with the host error', async () => {
    const { agent, children, store, failures } = fakeHarness();
    await agent.start();
    const child = children[0];
    agent.deliver('first request', 7);
    agent.deliver('correction', 8);
    const writes = child.writes.map((line) => JSON.parse(line)).filter((message) => message.type === 'submit');
    assert.equal(writes.length, 2);
    assert.equal(writes[1].requestId, `durable:${agent.threadId}:8`);
    assert.ok(store.activities.some((activity) => activity.kind === 'agent.steered'));

    child.feed({ type: 'settled', status: 'failed', error: 'model exploded', turn: 1 });
    await waitFor(() => !agent.turnActive);
    assert.equal(failures.length, 1);
    assert.match(failures[0].error, /model exploded/);
    assert.equal(agent.connected, true);
    await agent.stop();
  });

  it('keeps the session and active turn across a host crash, then resumes in the replacement', async () => {
    const { agent, children, store } = fakeHarness();
    await agent.start();
    const first = children[0];
    const sessionId = agent.threadId;
    agent.deliver('long request', 7);
    first.finish(1, null);

    await waitFor(() => children.length === 2, 3_000);
    await waitFor(() => agent.connected);
    const hello = JSON.parse(children[1].writes[0]);
    assert.equal(hello.sessionKey, sessionId);
    assert.equal(agent.threadId, sessionId);
    assert.equal(agent.turnActive, true);
    assert.equal(store.global().sessionReady, true);

    children[1].feed({ type: 'settled', status: 'done', turn: 1 });
    await waitFor(() => !agent.turnActive);
    assert.equal(store.activities.at(-1).kind, 'agent.turn_completed');
    await agent.stop();
  });

  it('rotates through a prepared session and restores it when cancelled', async () => {
    const { agent, children, store } = fakeHarness();
    await agent.start();
    const child = children[0];
    const before = agent.threadId;

    const pending = agent.prepareSessionRotation({});
    let request = lastWrite(child);
    assert.equal(request.type, 'rotate');
    child.feed({ id: request.id, ok: true, conversationId: 3 });
    const prepared = await pending;
    assert.match(prepared.id, /^[0-9a-f-]{36}$/);
    assert.equal(agent.threadId, before);
    agent.commitSessionRotation(prepared, { persist: true });
    assert.equal(agent.threadId, prepared.id);
    assert.equal(store.global().sessionId, prepared.id);

    const next = agent.threadId;
    const second = agent.prepareSessionRotation({});
    request = lastWrite(child);
    assert.equal(request.type, 'rotate');
    child.feed({ id: request.id, ok: true, conversationId: 4 });
    const preparedAgain = await second;
    const cancel = agent.cancelSessionRotation();
    request = lastWrite(child);
    assert.equal(request.type, 'open');
    assert.equal(request.sessionKey, next);
    child.feed({ id: request.id, ok: true, conversationId: 2 });
    await cancel;
    assert.equal(agent.threadId, next);
    assert.equal(preparedAgain.id !== next, true);
    await agent.stop();
  });

  it('lists models from the host and applies model and effort between turns', async () => {
    const { agent, children } = fakeHarness();
    await agent.start();
    const child = children[0];
    const pending = agent.listModels();
    const request = lastWrite(child);
    assert.equal(request.type, 'models');
    child.feed({ id: request.id, ok: true, models: [{ id: 'openai/gpt-4o', efforts: ['low', 'medium', 'high'] }] });
    assert.deepEqual(await pending, [{ id: 'openai/gpt-4o', efforts: ['low', 'medium', 'high'] }]);
    agent.listModelsCache = null;

    agent.setModel('openai/gpt-4o');
    agent.setEffort('high');
    agent.deliver('next request', 7);
    const configure = lastWrite(child);
    assert.equal(configure.type, 'configure');
    assert.equal(configure.model, 'openai/gpt-4o');
    assert.equal(configure.effort, 'high');
    child.feed({ id: configure.id, ok: true });
    await agent.settingsUpdate;
    const submit = lastWrite(child);
    assert.equal(submit.type, 'submit');
    assert.equal(agent.appliedModel, 'openai/gpt-4o');
    assert.equal(agent.appliedEffort, 'high');
    await agent.stop();
  });

  it('refuses a host that reports the wrong pi-durable version', async () => {
    const { agent } = fakeHarness({ version: '9.9.9' });
    await assert.rejects(agent.start(), /pi-durable 1\.4\.0 is required/);
  });

  it('queues input while the host is disconnected and drains after reconnect', async () => {
    const { agent, children } = fakeHarness();
    await agent.start();
    children[0].finish(1, null);
    await waitFor(() => !agent.connected, 1_000);
    agent.deliver('queued request', 7);
    await waitFor(() => children.length === 2, 3_000);
    await waitFor(() => agent.connected);
    await waitFor(() => children[1].writes.some((line) => JSON.parse(line).type === 'submit'));
    const submit = children[1].writes.map((line) => JSON.parse(line)).find((message) => message.type === 'submit');
    assert.match(submit.text, /queued request/);
    await agent.stop();
  });

  it('hands the published conversation handle back to a resumed session', async () => {
    const config = baseConfig();
    const store = makeStore();
    const first = fakeHarness({ config, store });
    await first.agent.start();
    const sessionId = store.global().sessionId;
    assert.equal(store.global().nativeSessionId, 2);
    await first.agent.stop();

    const second = fakeHarness({ config, store });
    await second.agent.start();
    const hello = JSON.parse(second.children[0].writes[0]);
    assert.equal(hello.sessionKey, sessionId);
    assert.equal(hello.nativeSessionId, 2);
    await second.agent.stop();
  });

  it('adopts the conversation the host opened when the stored handle is stale', async () => {
    const config = baseConfig();
    const store = makeStore();
    const first = fakeHarness({ config, store });
    await first.agent.start();
    const resumed = store.global();
    await first.agent.stop();

    // The durable store was replaced, so the recorded conversation is gone.
    store.global().nativeSessionId = 99;
    const second = fakeHarness({ config, store });
    await second.agent.start();
    const hello = JSON.parse(second.children[0].writes[0]);
    assert.equal(hello.nativeSessionId, 99, 'the daemon still asks for the stored handle');
    assert.equal(second.store.global().nativeSessionId, 2, 'the host reports the replacement it opened');
    await second.agent.stop();
    assert.equal(resumed.sessionId, second.store.global().sessionId);
  });

  it('publishes a rotated conversation handle only when the session CAS commits', async () => {
    const { agent, children, store } = fakeHarness();
    await agent.start();
    const child = children[0];
    assert.equal(agent.nativeSessionId, 2);

    const pending = agent.prepareSessionRotation();
    const rotate = child.writes.map((line) => JSON.parse(line)).find((message) => message.type === 'rotate');
    child.feed({ id: rotate.id, ok: true, conversationId: 5 });
    const prepared = await pending;

    assert.equal(prepared.nativeSessionId, 5);
    // Prepared is not published: the session still owns conversation 2 until
    // the daemon's transaction hands authority over.
    assert.equal(agent.nativeSessionId, 2);
    assert.equal(store.global().nativeSessionId, 2);
    assert.equal(agent.sessionDescriptor.nativeSessionId, 2);

    agent.commitSessionRotation(prepared, { persist: false });
    assert.equal(agent.nativeSessionId, 5);
    assert.equal(agent.sessionDescriptor.nativeSessionId, 5);
    assert.equal(store.global().nativeSessionId, 2, 'persist:false leaves the CAS as the only writer');
    await agent.stop();
  });

  it('restores the previous conversation handle when a rotation is cancelled', async () => {
    const { agent, children } = fakeHarness();
    await agent.start();
    const child = children[0];

    const pending = agent.prepareSessionRotation();
    const rotate = child.writes.map((line) => JSON.parse(line)).find((message) => message.type === 'rotate');
    child.feed({ id: rotate.id, ok: true, conversationId: 5 });
    await pending;

    const cancelled = agent.cancelSessionRotation();
    const reopen = child.writes.map((line) => JSON.parse(line)).find((message) => message.type === 'open');
    child.feed({ id: reopen.id, ok: true, conversationId: 2 });
    await cancelled;

    assert.equal(reopen.nativeSessionId, 2);
    assert.equal(agent.nativeSessionId, 2);
    await agent.stop();
  });
});

describe('DurableAgent against the real host', () => {
  it('runs a faux model turn end to end and records the note', async () => {
    const dataDir = tempDir();
    const config = baseConfig({ dataDir });
    const store = makeStore();
    const successes = [];
    const agent = new DurableAgent(config, store, quietLogger, {
      onTurnSucceeded: (success) => successes.push(success),
    });
    process.env.DURABLE_FAUX = '1';
    process.env.DURABLE_FAUX_RESPONSES = JSON.stringify([{ text: 'durable answer' }]);
    try {
      await agent.start();
      assert.equal(agent.connected, true);
      assert.equal(agent.threadId, store.global().sessionId);
      assert.equal(agent.resolvedModel, 'faux/faux-model');
      await agent.listModels();
      agent.deliver('hello durable', 7);
      await waitFor(() => !agent.turnActive, 15_000);
      assert.equal(successes.length, 1);
      assert.ok(store.activities.some((activity) => activity.kind === 'agent.note'
        && activity.summary === 'durable answer'));
      // The conversation handle lives with the session row, not in the data
      // directory: a restored instance can resume without a second file.
      assert.ok(Number.isSafeInteger(store.global().nativeSessionId));
      assert.equal(fs.existsSync(path.join(dataDir, 'durable', 'sessions.json')), false);
    } finally {
      await agent.stop();
      delete process.env.DURABLE_FAUX;
      delete process.env.DURABLE_FAUX_RESPONSES;
      fs.rmSync(dataDir, { recursive: true, force: true });
    }
  });

  it('executes a Core tool through actions.sock and feeds the result back', async () => {
    const dataDir = tempDir();
    const socketPath = path.join(dataDir, 'actions.sock');
    const calls = [];
    const server = http.createServer((request, response) => {
      let body = '';
      request.on('data', (chunk) => { body += chunk; });
      request.on('end', () => {
        if (request.method === 'GET' && request.url === '/v1/tools') {
          response.setHeader('content-type', 'application/json');
          response.end(JSON.stringify({ tools: [{
            name: 'send_message',
            description: 'Send a text message',
            inputSchema: {
              type: 'object',
              properties: { text: { type: 'string' } },
              required: ['text'],
              additionalProperties: false,
            },
          }] }));
          return;
        }
        if (request.method === 'POST' && request.url === '/v1/call') {
          calls.push(JSON.parse(body));
          response.setHeader('content-type', 'application/json');
          response.end(JSON.stringify({ ok: true, result: { text: 'sent', data: null, media: [] } }));
          return;
        }
        response.statusCode = 404;
        response.end();
      });
    });
    await new Promise((resolve) => server.listen(socketPath, resolve));
    const config = baseConfig({ dataDir });
    config.actionSocketPath = socketPath;
    const store = makeStore();
    const successes = [];
    const agent = new DurableAgent(config, store, quietLogger, {
      onTurnSucceeded: (success) => successes.push(success),
    });
    process.env.DURABLE_FAUX = '1';
    process.env.DURABLE_FAUX_RESPONSES = JSON.stringify([
      { toolCall: { name: 'send_message', arguments: { text: 'hello from durable' } } },
      { text: 'done' },
    ]);
    try {
      await agent.start();
      agent.deliver('send a message', 7);
      await waitFor(() => successes.length === 1, 15_000);
      assert.equal(calls.length, 1);
      assert.deepEqual(calls[0], { tool: 'send_message', input: { text: 'hello from durable' } });
      const kinds = store.activities.map((activity) => activity.kind);
      assert.ok(kinds.includes('agent.tool_called'));
      assert.ok(kinds.includes('agent.tool_completed'));
      const completed = store.activities.find((activity) => activity.kind === 'agent.tool_completed');
      assert.equal(completed.status, 'COMPLETED');
      assert.ok(store.activities.some((activity) => activity.kind === 'agent.note' && activity.summary === 'done'));
    } finally {
      await agent.stop();
      await new Promise((resolve) => server.close(resolve));
      delete process.env.DURABLE_FAUX;
      delete process.env.DURABLE_FAUX_RESPONSES;
      fs.rmSync(dataDir, { recursive: true, force: true });
    }
  });

  it('runs commands and edits workspace files through the coding tools', async () => {
    const dataDir = tempDir();
    const config = baseConfig({ dataDir });
    const workspace = config.workspace;
    const store = makeStore();
    const successes = [];
    const agent = new DurableAgent(config, store, quietLogger, {
      onTurnSucceeded: (success) => successes.push(success),
    });
    process.env.DURABLE_FAUX = '1';
    process.env.DURABLE_FAUX_RESPONSES = JSON.stringify([
      { toolCall: { name: 'bash', arguments: { command: 'printf hello > note.txt && cat note.txt' } } },
      { toolCall: { name: 'read', arguments: { path: 'note.txt' } } },
      { toolCall: { name: 'write', arguments: { path: 'draft.txt', content: 'first' } } },
      { toolCall: { name: 'edit', arguments: { path: 'draft.txt', edits: [{ oldText: 'first', newText: 'second' }] } } },
      { text: 'done' },
    ]);
    try {
      await agent.start();
      agent.deliver('do local work', 7);
      await waitFor(() => successes.length === 1, 20_000);
      // The coding tools share the run's NodeExecutionEnv, so relative paths
      // land in the configured workspace, not the host's own directory.
      assert.equal(fs.readFileSync(path.join(workspace, 'note.txt'), 'utf8'), 'hello');
      assert.equal(fs.readFileSync(path.join(workspace, 'draft.txt'), 'utf8'), 'second');
      const called = store.activities
        .filter((activity) => activity.kind === 'agent.tool_called')
        .map((activity) => activity.summary);
      assert.deepEqual(called, ['bash', 'read', 'write', 'edit']);
      const completed = store.activities.filter((activity) => activity.kind === 'agent.tool_completed');
      assert.equal(completed.length, 4);
      // A missing tool would settle as a completed error result, which is how
      // this surfaced: the turn succeeded while every local call failed.
      assert.deepEqual(completed.map((activity) => activity.status), ['COMPLETED', 'COMPLETED', 'COMPLETED', 'COMPLETED']);
    } finally {
      await agent.stop();
      delete process.env.DURABLE_FAUX;
      delete process.env.DURABLE_FAUX_RESPONSES;
      fs.rmSync(dataDir, { recursive: true, force: true });
    }
  });

  it('resumes a turn the host was killed in the middle of', async () => {
    const dataDir = tempDir();
    const config = baseConfig({ dataDir });
    const store = makeStore();
    const failures = [];
    const successes = [];
    const agent = new DurableAgent(config, store, quietLogger, {
      reconnectBaseMs: 50,
      onTurnFailed: (failure) => failures.push(failure),
      onTurnSucceeded: (success) => successes.push(success),
    });
    process.env.DURABLE_FAUX = '1';
    process.env.DURABLE_FAUX_DELAY_MS = '1500';
    process.env.DURABLE_FAUX_RESPONSES = JSON.stringify([{ text: 'resumed answer' }]);
    try {
      await agent.start();
      const sessionId = agent.threadId;
      agent.deliver('finish this', 7);
      await waitFor(() => agent.child && agent.turnActive, 5_000);
      await new Promise((resolve) => setTimeout(resolve, 300));
      agent.child.kill('SIGKILL');
      await waitFor(() => !agent.connected, 2_000);
      assert.equal(agent.threadId, sessionId);
      assert.equal(agent.turnActive, true);
      await waitFor(() => successes.length === 1, 20_000);
      assert.deepEqual(failures, []);
      assert.ok(store.activities.some((activity) => activity.kind === 'agent.note'
        && activity.summary === 'resumed answer'));
      assert.equal(store.global().sessionId, sessionId);
    } finally {
      await agent.stop();
      delete process.env.DURABLE_FAUX;
      delete process.env.DURABLE_FAUX_DELAY_MS;
      delete process.env.DURABLE_FAUX_RESPONSES;
      fs.rmSync(dataDir, { recursive: true, force: true });
    }
  });
});

describe('durable model configuration', () => {
  it('reads the providers, logins and default model Pi uses', async () => {
    const agentDir = tempDir();
    fs.writeFileSync(path.join(agentDir, 'models.json'), JSON.stringify({
      providers: {
        'volcengine-ark': {
          baseUrl: 'https://ark.example.invalid/api/v3',
          api: 'openai-completions',
          models: [{ id: 'ep-test', reasoning: true }],
        },
      },
    }));
    fs.writeFileSync(path.join(agentDir, 'auth.json'), JSON.stringify({
      'volcengine-ark': { type: 'api_key', key: 'ark-test-key' },
    }));
    fs.writeFileSync(path.join(agentDir, 'settings.json'), JSON.stringify({
      defaultProvider: 'volcengine-ark',
      defaultModel: 'ep-test',
    }));
    const saved = process.env.PI_CODING_AGENT_DIR;
    process.env.PI_CODING_AGENT_DIR = agentDir;
    try {
      assert.equal(piDefaultModel(), 'volcengine-ark/ep-test');
      const { models } = await loadDurableModels();
      const chat = await availableChatModels(models);
      // A model configured in Pi is a model the durable runtime can run, and
      // Pi's default is what an unset VIKA_DURABLE_MODEL resolves to.
      assert.equal(chat[0].provider, 'volcengine-ark');
      assert.equal(chat[0].id, 'ep-test');
    } finally {
      if (saved === undefined) delete process.env.PI_CODING_AGENT_DIR;
      else process.env.PI_CODING_AGENT_DIR = saved;
      fs.rmSync(agentDir, { recursive: true, force: true });
    }
  });

  it('keeps ARK_API_KEY for a models.json provider, like Pi does', () => {
    // sanitizeAgentChildEnv strips VOLCENGINE_/ARK_ keys; a Pi provider may
    // name ARK_API_KEY as its key, so the durable child keeps it too.
    const safe = sanitizeDurableEnv({
      PATH: '/usr/bin',
      HOME: '/home/u',
      ARK_API_KEY: 'ark-secret',
      VOLCENGINE_API_KEY: 'volc-secret',
      LARK_APP_SECRET: 'lark-secret',
    }, 'profile-a');
    assert.equal(safe.ARK_API_KEY, 'ark-secret');
    assert.equal(safe.VOLCENGINE_API_KEY, undefined);
    assert.equal(safe.LARK_APP_SECRET, undefined);
    assert.equal(safe.LARKSUITE_CLI_PROFILE, 'profile-a');
  });

  it('puts the configured lark-cli and Slack CLI first on PATH, like the other runtimes', () => {
    const safe = sanitizeDurableEnv({ PATH: '/usr/bin:/bin' }, 'profile-a', null, {
      larkCliBin: '/custom-lark/lark-cli',
      slackCliBin: '/custom-slack/slack',
    });
    assert.equal(safe.PATH, '/custom-lark:/custom-slack:/usr/bin:/bin');
  });
});
