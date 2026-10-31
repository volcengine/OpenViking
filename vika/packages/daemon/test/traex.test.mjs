// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { describe, it } from 'node:test';
import { TraexAgent } from '../src/traex.mjs';

const quietLogger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

const baseConfig = {
  traexBin: 'traecli',
  traexEffort: 'medium',
  workspace: '/tmp/vika-workspace',
  databasePath: '/tmp/vika-test.sqlite3',
  profile: 'test-profile',
  historyLimit: 30,
};

const flush = () => new Promise((resolve) => setImmediate(resolve));

class FakeChild extends EventEmitter {
  constructor() {
    super();
    this.pid = 987_654_321;
    this.exitCode = null;
    this.writes = [];
    this.stdin = {
      on() {},
      end() {},
      write: (line) => { this.writes.push(line); return true; },
    };
    this.stdout = new EventEmitter();
    this.stdout.setEncoding = () => {};
    this.stderr = new EventEmitter();
    this.stderr.setEncoding = () => {};
  }

  feed(message) { this.stdout.emit('data', `${JSON.stringify(message)}\n`); }
  sent() { return this.writes.map((line) => JSON.parse(line)); }
  ofMethod(method) { return this.sent().filter((m) => m.method === method); }
}

function makeStore() {
  let session = { sessionId: null, policyHash: null };
  const settings = {};
  return {
    getGlobalSession: () => session,
    setGlobalSession(sessionId, policyHash) { session = { sessionId, policyHash }; },
    publishOwnedSession({ sessionId, policyHash, runtime, epoch, syncedThroughSeq, sessionReady }) {
      session = { sessionId, policyHash, runtime, epoch, syncedThroughSeq, sessionReady };
      return true;
    },
    getCognitionState: () => ({ runtime: 'traex', epoch: 1, authorityToken: 'traex-token' }),
    advanceOwnedSessionAcceptance: () => true,
    cognitionJournalBeforeInbound: () => 0,
    cognitionJournalSeqForInbound: () => 0,
    recordActivity() {},
    history: () => [],
    getOVContext: () => ({ sessionId: 'ov-test', latest_archive_overview: '', messages: [] }),
    getSetting: (key) => settings[key] ?? null,
    setSetting: (key, value) => { settings[key] = value; },
    settings,
  };
}

function newAgent(store, options = {}, config = baseConfig) {
  const spawnCalls = [];
  const children = [];
  const spawn = (bin, args, spawnOptions) => {
    spawnCalls.push({ bin, args, options: spawnOptions });
    const child = new FakeChild();
    children.push(child);
    return child;
  };
  const agent = new TraexAgent(config, store, quietLogger, {
    spawn,
    handshakeTimeoutMs: 1_000,
    reconnectBaseMs: 5,
    reconnectCapMs: 20,
    ...options,
  });
  return { agent, children, spawnCalls };
}

async function handshake(agent, children, { threadId = 'th-traex-1' } = {}) {
  const started = agent.start();
  await flush();
  children[0].feed({ id: 1, result: {} });
  await flush();
  children[0].feed({ id: 2, result: { thread: { id: threadId } } });
  await started;
  return children[0];
}

describe('TraexAgent app-server backend', () => {
  it('explains a startup protocol mismatch with bounded, redacted stderr', async () => {
    const { agent, children } = newAgent(makeStore());
    const started = agent.start();
    const rejected = assert.rejects(started, (error) => {
      assert.match(error.message, /requires a TraeX with app-server support/);
      assert.match(error.message, /unknown subcommand.*app-server/);
      assert.doesNotMatch(error.message, /super-secret/);
      assert.ok(error.message.length < 2500);
      return true;
    });
    await flush();
    children[0].stderr.emit('data', 'unknown subcommand app-server; api_key=sup');
    children[0].stderr.emit('data', `er-secret\n${'x'.repeat(10000)}`);
    children[0].exitCode = 1;
    children[0].emit('exit', 1, null);
    await rejected;
  });

  it('starts traecli app-server over stdio without Codex-only service-tier flags', async () => {
    const store = makeStore();
    const { agent, children, spawnCalls } = newAgent(store, {}, {
      ...baseConfig,
      projectDir: '/release',
      larkCliBin: '/fake/lark-cli',
      slackCliBin: '/fake-slack/slack',
      actionMcpPath: '/release/packages/daemon/src/actions/mcp.mjs',
      actionSocketPath: '/tmp/traex-actions.sock',
      sendTimeoutMs: 5_000,
      maxReplyChars: 1_000,
      dryRun: true,
    });

    await handshake(agent, children);

    assert.equal(spawnCalls[0].bin, 'traecli');
    assert.deepEqual(spawnCalls[0].args.slice(0, 3), ['app-server', '--listen', 'stdio://']);
    const rendered = spawnCalls[0].args.filter((arg) => !arg.startsWith('developer_instructions=')).join('\n');
    assert.match(rendered, /mcp_servers\.personal_agent\.command/);
    const toolArgs = spawnCalls[0].args.find((arg) => arg.startsWith('mcp_servers.personal_agent.args='));
    assert.deepEqual(JSON.parse(toolArgs.slice(toolArgs.indexOf('=') + 1)), [
      '/release/packages/daemon/src/actions/mcp.mjs',
      '--socket', '/tmp/traex-actions.sock', '--authority', 'traex-token',
    ]);
    assert.doesNotMatch(rendered, /model_reasoning_effort/);
    assert.doesNotMatch(rendered, /service_tier="fast"/);
    assert.doesNotMatch(rendered, /features\.fast_mode=true/);
    assert.doesNotMatch(rendered, /features\.plugins=false/);
    assert.doesNotMatch(rendered, /features\.tool_suggest=false/);
    assert.doesNotMatch(rendered, /features\.apps=false/);
    assert.doesNotMatch(rendered, /--effort/);
    assert.equal(spawnCalls[0].options.env.LARKSUITE_CLI_PROFILE, 'test-profile');
    assert.ok(spawnCalls[0].options.env.PATH.startsWith('/fake:/fake-slack:'));
    assert.equal(spawnCalls[0].options.env.PERSONAL_AGENT_SOCKET, '/tmp/traex-actions.sock');
    assert.equal(spawnCalls[0].options.env.PERSONAL_AGENT_AUTHORITY, 'traex-token');
  });

  it('applies TraeX effort on the next turn in the same thread', async () => {
    const store = makeStore();
    const { agent, children } = newAgent(store);
    agent.model = 'gpt-5.6-sol';
    const child = await handshake(agent, children);

    const threadStart = child.ofMethod('thread/start').at(-1);
    assert.deepEqual(threadStart.params.config, { model_reasoning_effort: 'medium' });

    agent.deliver('hello');
    const turn = child.ofMethod('turn/start').at(-1);
    assert.equal(turn.params.model, 'gpt-5.6-sol');
    assert.equal(turn.params.effort, 'medium');
    assert.equal(Object.hasOwn(turn.params, 'config'), false);

    agent.setModel('gpt-5.6-terra');
    assert.equal(store.settings.traex_model, 'gpt-5.6-terra');
    agent.setEffort('xhigh');
    assert.equal(store.settings.traex_effort, 'xhigh');
    child.feed({ method: 'turn/started', params: { turn: { id: 't1' } } });
    child.feed({ method: 'turn/completed', params: { turn: { status: 'completed' } } });
    agent.deliver('next turn');
    const next = child.ofMethod('turn/start').at(-1);
    assert.equal(next.params.effort, 'xhigh');
    assert.equal(next.params.threadId, turn.params.threadId);
    assert.equal(child.ofMethod('thread/start').length, 1);
    assert.equal(child.ofMethod('turn/interrupt').length, 0);
  });

  it('lets persisted TraeX model setting override a fresh-start initial model', async () => {
    const store = makeStore();
    store.settings.traex_model = 'gpt-saved';
    const { agent, children } = newAgent(store, { initialModel: 'gpt-env' });
    const child = await handshake(agent, children);

    agent.deliver('hello');
    const turn = child.ofMethod('turn/start').at(-1);
    assert.equal(turn.params.model, 'gpt-saved');
    assert.equal(turn.params.effort, 'medium');
  });

  it('exposes app-server reasoning efforts as TraeX effort controls', async () => {
    const store = makeStore();
    const { agent, children } = newAgent(store);
    const child = await handshake(agent, children);

    const listing = agent.listModels();
    await flush();
    const request = child.ofMethod('model/list').at(-1);
    child.feed({
      id: request.id,
      result: {
        data: [
          {
            id: 'gpt-5.5',
            hidden: false,
            supportedReasoningEfforts: [{ reasoningEffort: 'high' }],
          },
        ],
      },
    });

    assert.deepEqual(await listing, [{ id: 'gpt-5.5', effortsKnown: true, efforts: ['high'] }]);
  });

  it('publishes and records TraeX session ownership separately from Codex', async () => {
    const activities = [];
    const store = makeStore();
    store.recordActivity = (activity) => activities.push(activity);
    const { agent, children } = newAgent(store);
    const child = await handshake(agent, children);

    assert.equal(agent.runtime, 'traex');
    assert.equal(store.getGlobalSession().runtime, 'traex');

    agent.deliver('do work', 7);
    child.feed({ method: 'turn/started', params: { turn: { id: 'turn-1' } } });
    child.feed({ method: 'turn/completed', params: { turn: { id: 'turn-1', status: 'completed' } } });

    assert.match(activities.at(-1).eventKey, /^traex:th-traex-1:turn-1:agent\.turn_completed:/);
  });
});
