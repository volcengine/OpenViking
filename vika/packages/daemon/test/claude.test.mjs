// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { ovFixture } from './ov-fixture.mjs';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import { describe, it } from 'node:test';
import { ClaudeAgent, sanitizeClaudeEnv } from '../src/claude.mjs';

const quietLogger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

const baseConfig = {
  claudeBin: 'claude',
  claudeModel: 'sonnet',
  claudeEffort: 'medium',
  workspace: '/tmp/vika-workspace',
  projectDir: '/release',
  databasePath: '/tmp/vika-test.sqlite3',
  profile: 'test-profile',
  larkCliBin: '/fake/lark-cli',
  slackCliBin: '/fake-slack/slack',
  actionMcpPath: '/release/packages/daemon/src/actions/mcp.mjs',
  actionSocketPath: '/data/actions.sock',
  sendTimeoutMs: 5_000,
  maxReplyChars: 1_000,
  dryRun: true,
};

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

const helpFlags = [
  '--input-format', '--output-format', '--resume', '--session-id', '--mcp-config',
  '--dangerously-skip-permissions', '--disallowed-tools', '--effort',
  '--append-system-prompt', '--no-chrome', '--model', '--print', '--permission-prompts',
].join('\n');

function makeStore({ globalOverride = {} } = {}) {
  let global = {
    sessionId: null,
    policyHash: null,
    runtime: null,
    epoch: 0,
    syncedThroughSeq: 0,
    sessionReady: false,
    ...globalOverride,
  };
  const activities = [];
  const rows = [
    {
      seq: 1, epoch: 1, kind: 'inbound', status: 'COMMITTED', atMs: 1,
      inboundSeq: 1, chatId: 'oc_old12345678', threadId: null,
      messageId: 'om_old12345678', actor: 'ou_old12345678', text: 'earlier question',
    },
    {
      seq: 2, epoch: 1, kind: 'outbound', status: 'COMMITTED', atMs: 2,
      inboundSeq: null, chatId: 'oc_old12345678', threadId: null,
      messageId: 'om_answer123456', actor: 'me', text: 'earlier answer',
    },
    {
      seq: 3, epoch: 1, kind: 'inbound', status: 'COMMITTED', atMs: 3,
      inboundSeq: 7, chatId: 'oc_now12345678', threadId: null,
      messageId: 'om_now12345678', actor: 'ou_now12345678', text: 'current request',
    },
    {
      seq: 4, epoch: 1, kind: 'inbound', status: 'COMMITTED', atMs: 4,
      inboundSeq: 8, chatId: 'oc_now12345678', threadId: null,
      messageId: 'om_fix12345678', actor: 'ou_now12345678', text: 'live correction',
    },
  ];
  return {
    activities,
    getOVContext: () => ovFixture(rows),
    getCognitionState: () => ({ runtime: 'claude', epoch: 1, authorityToken: 'token-claude' }),
    getGlobalSession: () => global,
    setGlobalSession(sessionId, policyHash, fields) {
      global = { sessionId, policyHash, ...fields };
    },
    publishOwnedSession({ runtime, sessionId, policyHash, epoch, acceptedThroughSeq, sessionReady }) {
      global = {
        sessionId,
        policyHash,
        runtime,
        epoch,
        syncedThroughSeq: acceptedThroughSeq,
        sessionReady,
      };
      return true;
    },
    advanceOwnedSessionAcceptance({ sessionId, acceptedThroughSeq }) {
      if (global.sessionId !== sessionId) return false;
      global = {
        ...global,
        syncedThroughSeq: Math.max(global.syncedThroughSeq, acceptedThroughSeq),
      };
      return true;
    },
    markOwnedSessionUnresumable({ sessionId }) {
      if (global.sessionId !== sessionId) return false;
      global = { ...global, sessionReady: false };
      return true;
    },
    global: () => global,
    getSetting: () => null,
    setSetting() {},
    recordActivity(activity) { activities.push(activity); },
    cognitionJournal(_epoch, through, after = 0) {
      return rows.filter((row) => row.seq > after && row.seq <= through);
    },
    cognitionJournalBeforeInbound(inboundSeq) {
      const row = rows.find((item) => item.kind === 'inbound' && item.inboundSeq === inboundSeq);
      return row ? row.seq - 1 : rows.at(-1).seq;
    },
    cognitionJournalSeqForInbound(inboundSeq) {
      return rows.find((item) => item.kind === 'inbound' && item.inboundSeq === inboundSeq)?.seq ?? 0;
    },
  };
}

async function waitFor(predicate, timeoutMs = 500) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error('timed out waiting for condition');
}

function harness({ loggedIn = true, store = makeStore(), agentOptions = {}, help = helpFlags } = {}) {
  const calls = [];
  const persistent = [];
  const spawn = (_bin, args, options) => {
    const child = new FakeChild();
    calls.push({ args, child, options });
    queueMicrotask(() => {
      if (args.includes('--version')) {
        child.stdout.emit('data', '2.1.300 (Claude Code)\n');
        child.finish();
      } else if (args.includes('--help')) {
        child.stdout.emit('data', help);
        child.finish();
      } else if (args[0] === 'auth') {
        child.stdout.emit('data', JSON.stringify({ loggedIn }));
        child.finish();
      } else {
        persistent.push(child);
        child.emit('spawn');
      }
    });
    return child;
  };
  const failures = [];
  const agent = new ClaudeAgent(baseConfig, store, quietLogger, {
    spawn,
    startTimeoutMs: 100,
    startStabilityMs: 1,
    onTurnFailed: (failure) => failures.push(failure),
    ...agentOptions,
  });
  return { agent, calls, persistent, store, failures };
}

describe('ClaudeAgent persistent stream-json runtime', () => {
  it('classifies only model-specific startup exits for default-model recovery', async () => {
    for (const [detail, modelError] of [['Invalid model: claude-retired\n', true],
      ['Authentication failed: invalid API key\n', false], ['Unsupported model reasoning effort: minimal\n', false],
      ['Model claude-sonnet is unavailable due to a network error\n', false]]) {
      const { agent } = harness();
      agent._spawn = () => {
        const child = new FakeChild();
        queueMicrotask(() => {
          child.emit('spawn');
          child.stderr.emit('data', detail);
          child.finish(1);
        });
        return child;
      };
      await assert.rejects(agent._spawnPersistent(), (error) => {
        assert.equal(error.code === 'MODEL_UNAVAILABLE', modelError);
        assert.doesNotMatch(error.message, /invalid API key|claude-retired/);
        return true;
      });
      await agent.stop();
    }
  });

  it('omits the effort CLI override when the selected model has no effort control', async () => {
    const { agent, calls } = harness({ agentOptions: { effort: '' } });
    try {
      await agent.start();
      const args = calls.find((call) => call.args.includes('--print')).args;
      assert.equal(args.includes('--effort'), false);
      assert.equal(agent.appliedEffort, '');
    } finally { await agent.stop(); }
  });

  it('discovers versioned models and effort capabilities without sending a prompt', async () => {
    const { agent, persistent } = harness();
    await agent.start();
    try {
      const child = persistent[0];
      const pending = agent.listModels();
      const concurrent = agent.listModels();
      const request = JSON.parse(child.writes[0]);
      assert.equal(request.type, 'control_request');
      assert.equal(request.request.subtype, 'initialize');
      child.feed({ type: 'control_response', response: { subtype: 'success', request_id: request.request_id,
        response: { models: [
          { value: 'sonnet', resolvedModel: 'claude-sonnet-5-5', displayName: 'Sonnet 5.5', supportsEffort: true, supportedEffortLevels: ['low', 'high'] },
          { value: 'claude-haiku-4-5-20251001', displayName: 'Haiku 4.5', supportsEffort: false },
          { value: 'disabled', disabled: true },
        ] } } });
      const models = await pending;
      assert.deepEqual(await concurrent, models);
      assert.equal(models.length, 2);
      assert.equal(agent.resolvedModel, 'claude-sonnet-5-5');
      assert.deepEqual(models[0].efforts, ['low', 'high']);
      assert.deepEqual(models[1].efforts, []);
      assert.deepEqual(await agent.listModels(), models);
      assert.equal(child.writes.length, 1);
      assert.equal(agent.turnInProgress, false);
      assert.equal(agent.sessionInitialized, false);
    } finally { await agent.stop(); }
  });

  it('releases model discovery on disconnect and never invents a static catalog', async () => {
    const { agent } = harness({ agentOptions: { sessionPersistence: false } });
    await agent.start();
    const pending = agent.listModels();
    await agent.stop();
    assert.deepEqual(await pending, []);
    assert.equal(agent.pendingControl.size, 0);
  });

  it('defers model changes until the next input and resumes the same initialized session', async () => {
    const { agent, calls, persistent, store } = harness();
    await agent.start();
    const child = persistent[0];
    const id = agent.threadId;
    agent.deliver('active input', 7);
    child.feed({ type: 'system', subtype: 'init', session_id: id, model: 'claude-sonnet-5-5' });
    agent.setModel('opus');
    agent.deliver('correction', 8);
    assert.equal(child.exitCode, null);
    assert.equal(agent.appliedModel, 'sonnet');
    child.feed({ type: 'result', subtype: 'success' });
    const before = store.global();
    agent.deliver('next input');
    await waitFor(() => persistent.length === 2 && !agent.settingsUpdate);
    const args = calls.at(-1).args;
    assert.equal(args[args.indexOf('--model') + 1], 'opus');
    assert.equal(args[args.indexOf('--resume') + 1], id);
    assert.equal(agent.threadId, id);
    assert.deepEqual(store.global(), before);
    assert.equal(agent.appliedModel, 'opus');
    persistent[1].feed({ type: 'result', subtype: 'success' });
    await agent.stop();
  });

  it('applies effort after the active turn by resuming the same session and preserving queued input order', async () => {
    const { agent, calls, persistent, store, failures } = harness();
    await agent.start();
    const oldChild = persistent[0];
    const sessionId = agent.threadId;
    agent.deliver('current request', 7);
    oldChild.feed({ type: 'system', subtype: 'init', session_id: sessionId });
    agent.setEffort('high');
    agent.deliver('live correction', 8);
    assert.equal(persistent.length, 1);
    assert.equal(agent.appliedEffort, 'medium');
    assert.equal(oldChild.exitCode, null);
    oldChild.feed({ type: 'result', subtype: 'success' });
    const before = store.global();

    agent.deliver('next request');
    agent.deliver('next correction');
    await waitFor(() => persistent.length === 2 && !agent.settingsUpdate);
    const args = calls.at(-1).args;
    assert.equal(args[args.indexOf('--resume') + 1], sessionId);
    assert.equal(args[args.indexOf('--effort') + 1], 'high');
    assert.equal(agent.threadId, sessionId);
    assert.equal(agent.appliedEffort, 'high');
    assert.deepEqual(store.global(), before);
    assert.deepEqual(persistent[1].writes.map((line) => JSON.parse(line).message.content[0].text),
      ['next request', 'next correction']);
    assert.equal(failures.length, 0);
    persistent[1].feed({ type: 'result', subtype: 'success' });
    await agent.stop();
  });

  it('changes effort before the first prompt without resuming a nonexistent transcript', async () => {
    const { agent, calls, persistent } = harness();
    await agent.start();
    const sessionId = agent.threadId;
    agent.setEffort('high');
    agent.setEffort('xhigh');
    agent.deliver('first input', 7);
    await waitFor(() => persistent.length === 2 && !agent.settingsUpdate);
    const args = calls.at(-1).args;
    assert.equal(args.includes('--resume'), false);
    assert.equal(args[args.indexOf('--session-id') + 1], sessionId);
    assert.equal(args[args.indexOf('--effort') + 1], 'xhigh');
    assert.match(JSON.parse(persistent[1].writes[0]).message.content[0].text, /earlier question/);
    persistent[1].feed({ type: 'result', subtype: 'success' });
    await agent.stop();
  });

  it('reports queued input when effort reconfiguration fails without retiring the session', async () => {
    const { agent, failures, store } = harness({ agentOptions: { sessionPersistence: false } });
    await agent.start();
    const sessionId = agent.threadId;
    const epoch = agent.cognitionEpoch;
    agent._replaceProcess = async () => { throw new Error('effort process startup failed'); };
    agent.setEffort('high');
    agent.deliver('waiting input', 7, 'receipt-effort');
    await agent.settingsUpdate;
    assert.equal(agent.threadId, sessionId);
    assert.equal(agent.cognitionEpoch, epoch);
    assert.equal(agent.appliedEffort, 'medium');
    assert.equal(failures.length, 1);
    assert.deepEqual(failures[0].receiptIds, ['receipt-effort']);
    assert.match(failures[0].error, /effort process startup failed/);
    assert.equal(store.activities.some((event) => event.kind === 'agent.session_rotated'), false);
    await agent.stop();
  });

  it('stops an effort respawn before queued input can start', async () => {
    const { agent, persistent } = harness();
    await agent.start();
    agent.setEffort('high');
    agent.deliver('waiting input', 7, 'receipt-stop');
    await agent.stop();
    assert.equal(agent.child, null);
    assert.equal(persistent.length, 2);
    assert.ok(persistent.every((child) => child.exitCode !== null && child.writes.length === 0));
  });

  it('refuses a CLI without unattended permission handling before starting a session', async () => {
    const { agent, persistent } = harness({ help: helpFlags.replace('--permission-prompts', '') });
    await assert.rejects(agent.start(), /missing required flag --permission-prompts/);
    assert.equal(persistent.length, 0);
  });

  it('preflights, starts persistent stream-json, and prefixes the first real turn', async () => {
    const { agent, calls, persistent, store } = harness();
    await agent.start();

    assert.equal(calls.length, 4);
    const args = calls.at(-1).args;
    assert.equal(args[args.indexOf('--permission-prompts') + 1], 'none');
    for (const value of ['--print', '--input-format', 'stream-json', '--output-format', '--mcp-config']) {
      assert.ok(args.includes(value));
    }
    // User-scope and plugin MCP servers must load alongside personal_agent.
    assert.ok(!args.includes('--strict-mcp-config'));
    const mcpConfig = JSON.parse(fs.readFileSync(args[args.indexOf('--mcp-config') + 1], 'utf8'));
    assert.deepEqual(mcpConfig.mcpServers.personal_agent.args, [
      '/release/packages/daemon/src/actions/mcp.mjs', '--socket', '/data/actions.sock', '--authority', agent.authorityToken,
    ]);
    assert.equal(calls.at(-1).options.env.PERSONAL_AGENT_SOCKET, '/data/actions.sock');
    assert.equal(args[args.indexOf('--model') + 1], 'sonnet');
    assert.equal(args[args.indexOf('--effort') + 1], 'medium');
    assert.ok(args.includes('--session-id'));
    assert.ok(!args.includes('--resume'));
    assert.ok(calls.every((call) => call.options.env.LARKSUITE_CLI_PROFILE === 'test-profile'));
    assert.ok(calls.every((call) => call.options.env.PATH.startsWith('/fake:/fake-slack:')));

    agent.deliver('[chat=oc_now12345678 msg=om_now12345678] current request', 7);
    const first = JSON.parse(persistent[0].writes[0]);
    const body = first.message.content[0].text;
    assert.match(body, /daemon OpenViking context/);
    assert.match(body, /earlier question/);
    assert.match(body, /earlier answer/);
    assert.match(body, /history above, new messages below/);
    assert.match(body, /current request$/);
    assert.doesNotMatch(body, /catch-up complete/);

    const sessionId = args[args.indexOf('--session-id') + 1];
    persistent[0].feed({ type: 'system', subtype: 'init', session_id: sessionId });
    assert.equal(store.global().sessionReady, true);
    assert.equal(store.global().syncedThroughSeq, 0);
    persistent[0].feed({
      type: 'assistant',
      message: { content: [{ type: 'text', text: 'telemetry, not a Lark delivery' }] },
    });
    persistent[0].feed({ type: 'result', subtype: 'success', result: 'telemetry only' });
    assert.equal(agent.turnInProgress, false);
    assert.equal(store.global().syncedThroughSeq, 3);
    assert.ok(store.activities.some((activity) => activity.metadata?.delivery === 'telemetry_only'));
    await agent.stop();
  });

  it('writes a live correction into the active Claude turn and commits one cursor', async () => {
    const { agent, persistent, store } = harness();
    await agent.start();
    const child = persistent[0];
    agent.deliver('first request', 7);
    child.feed({ type: 'system', subtype: 'init', session_id: agent.sessionId });
    agent.deliver('live correction', 8);

    assert.equal(child.writes.length, 2);
    assert.equal(JSON.parse(child.writes[1]).message.content[0].text, 'live correction');
    assert.equal(agent.turnInProgress, true);
    child.feed({ type: 'result', subtype: 'success', result: 'corrected' });
    assert.equal(agent.turnInProgress, false);
    assert.equal(store.global().syncedThroughSeq, 4);
    await agent.stop();
  });

  it('demotes the stored session when stopped mid-turn, keeps it after a clean stop', async () => {
    const clean = harness();
    await clean.agent.start();
    clean.agent.deliver('quick request', 7);
    clean.persistent[0].feed({ type: 'system', subtype: 'init', session_id: clean.agent.sessionId });
    clean.persistent[0].feed({ type: 'result', subtype: 'success', result: 'done' });
    await clean.agent.stop();
    // Turn completed before shutdown: the transcript is intact and resumable.
    assert.equal(clean.store.global().sessionReady, true);

    const killed = harness();
    await killed.agent.start();
    killed.agent.deliver('long request', 7);
    killed.persistent[0].feed({ type: 'system', subtype: 'init', session_id: killed.agent.sessionId });
    await killed.agent.stop();
    // Killed with the turn still active: resuming would patch the interrupted
    // tool call and rewrite the whole cached prefix — never resume it.
    assert.equal(killed.store.global().sessionReady, false);
  });

  it('returns active receipt-only items and preserves the legacy success shape otherwise', async () => {
    const successes = [];
    const { agent, persistent } = harness({
      agentOptions: { onTurnSucceeded: (info) => successes.push(info) },
    });
    await agent.start();
    const child = persistent[0];

    agent.deliver('first audio segment', null, 'receipt-1');
    agent.deliverBatch([{ text: 'second audio segment', receiptId: 'receipt-2' }]);
    child.feed({ type: 'system', subtype: 'init', session_id: agent.sessionId });
    child.feed({ type: 'result', subtype: 'success', result: 'handled' });

    agent.deliver('normal inbound', 7);
    child.feed({ type: 'result', subtype: 'success', result: 'handled' });
    assert.deepEqual(successes, [
      { inboundSeqs: [], receiptIds: ['receipt-1', 'receipt-2'] },
      { inboundSeqs: [7] },
    ]);
    await agent.stop();
  });

  it('surfaces a receipt when prompt preparation fails before Claude owns the turn', async () => {
    const store = makeStore();
    store.cognitionJournalBeforeInbound = () => 1;
    store.getOVContext = () => undefined;
    const { agent, persistent, failures } = harness({ store });
    await agent.start();

    agent.deliver('will not be written', null, 'receipt-terminal');
    assert.equal(persistent[0].writes.length, 0);
    assert.equal(failures.length, 1);
    assert.deepEqual(failures[0].inboundSeqs, []);
    assert.deepEqual(failures[0].receiptIds, ['receipt-terminal']);
    assert.match(failures[0].error, /OpenViking context is not prepared/);
    await agent.stop();
  });

  it('reports an is_error result with subtype success as a turn failure', async () => {
    const { agent, persistent, failures } = harness();
    await agent.start();
    const child = persistent[0];
    agent.deliver('please answer', 7);
    child.feed({ type: 'system', subtype: 'init', session_id: agent.sessionId });
    child.feed({
      type: 'result',
      subtype: 'success',
      is_error: true,
      result: "You've hit your session limit · resets 5:30pm (UTC)",
    });
    assert.equal(agent.turnInProgress, false);
    assert.equal(failures.length, 1);
    assert.deepEqual(failures[0].inboundSeqs, [7]);
    assert.match(failures[0].error, /session limit/);
    await agent.stop();
  });

  it('does not replay accepted cognition after an error result', async () => {
    const store = makeStore();
    store.advanceOwnedSessionAcceptance = ({ acceptedThroughSeq }) => {
      const global = store.global();
      store.setGlobalSession(global.sessionId, global.policyHash, {
        ...global,
        syncedThroughSeq: Math.max(global.syncedThroughSeq, acceptedThroughSeq),
      });
      return true;
    };
    const { agent, persistent } = harness({ store });
    await agent.start();
    const child = persistent[0];

    agent.deliver('first', 7);
    const first = JSON.parse(child.writes[0]).message.content[0].text;
    assert.match(first, /earlier question/);
    child.feed({ type: 'system', subtype: 'init', session_id: agent.sessionId });
    child.feed({ type: 'result', subtype: 'error', result: 'failed' });

    agent.deliver('second', 8);
    const second = JSON.parse(child.writes[1]).message.content[0].text;
    assert.doesNotMatch(second, /earlier question/);
    assert.doesNotMatch(second, /current request/);
    assert.equal(second, 'second');
    await agent.stop();
  });

  it('still completes a turn when a tool result carried is_error but the run succeeded', async () => {
    const { agent, persistent, failures } = harness();
    await agent.start();
    const child = persistent[0];
    agent.deliver('please answer', 7);
    child.feed({ type: 'system', subtype: 'init', session_id: agent.sessionId });
    child.feed({ type: 'result', subtype: 'success', is_error: false, result: 'done' });
    assert.equal(agent.turnInProgress, false);
    assert.equal(failures.length, 0);
    await agent.stop();
  });

  it('interruptTurn kills the in-flight turn and keeps queued messages for the next session', async () => {
    const abandoned = [];
    const successes = [];
    const { agent, persistent, failures } = harness({
      agentOptions: {
        onTurnAbandoned: (info) => abandoned.push(info),
        onTurnSucceeded: (info) => successes.push(info),
      },
    });
    await agent.start();
    const child = persistent[0];
    agent.deliver('stuck request', 7);
    child.feed({ type: 'system', subtype: 'init', session_id: agent.sessionId });
    assert.equal(agent.turnInProgress, true);

    await agent.interruptTurn();
    assert.equal(agent.turnInProgress, false);
    assert.equal(failures.length, 0); // intentional abandonment is not a failure
    assert.deepEqual(abandoned, [{ inboundSeqs: [7] }]);

    agent.deliver('queued while switching', 8, 'receipt-queued'); // disconnected: queued, not lost
    const rotated = agent.rotateSession({ resume: false });
    await waitFor(() => persistent.length === 2);
    await rotated;
    await waitFor(() => persistent[1].writes.length > 0);
    const bodies = persistent[1].writes.map((line) => JSON.parse(line).message.content[0].text);
    assert.ok(bodies.some((text) => text.includes('queued while switching')));
    persistent[1].feed({ type: 'result', subtype: 'success', result: 'handled' });
    assert.deepEqual(successes, [{
      inboundSeqs: [8],
      receiptIds: ['receipt-queued'],
    }]);
    await agent.stop();
  });

  it('keeps a prepared process unpublished until session commit', async () => {
    const { agent, persistent, store } = harness();
    await agent.start();
    const previousSessionId = agent.sessionId;
    const previousGlobal = store.global();

    const prepared = await agent.prepareSessionRotation();
    assert.equal(persistent.length, 2);
    assert.notEqual(prepared.id, previousSessionId);
    assert.deepEqual(store.global(), previousGlobal);
    assert.equal(persistent[1].writes.length, 0);

    assert.equal(agent.commitSessionRotation(prepared, { persist: false }), prepared.id);
    assert.deepEqual(store.global(), previousGlobal);
    await agent.stop();
  });

  it('restores model and effort before draining queued work after a cancelled prepare', async () => {
    const { agent, calls, persistent } = harness({
      agentOptions: { reconnectBaseMs: 1, reconnectCapMs: 2 },
    });
    await agent.start();
    agent.model = 'opus';
    agent.effort = 'max';
    agent.queue.push([{ text: 'queued during transition', seq: 7 }]);

    await agent.prepareSessionRotation({
      rollback: { model: 'sonnet', effort: 'medium' },
    });
    assert.equal(persistent[1].writes.length, 0);
    await agent.cancelSessionRotation();

    assert.equal(agent.model, 'sonnet');
    assert.equal(agent.effort, 'medium');
    const persistentCalls = calls.filter(({ args }) => args.includes('--print'));
    const restoredArgs = persistentCalls.at(-1).args;
    assert.equal(restoredArgs[restoredArgs.indexOf('--model') + 1], 'sonnet');
    assert.equal(restoredArgs[restoredArgs.indexOf('--effort') + 1], 'medium');
    await waitFor(() => persistent[2].writes.length > 0);
    assert.match(persistent[2].writes[0], /queued during transition/);
    persistent[2].feed({ type: 'result', subtype: 'success', result: 'handled' });
    await agent.stop();
  });

  it('returns receipt-only active work through the interruption callback', async () => {
    const abandoned = [];
    const { agent, persistent } = harness({
      agentOptions: { onTurnAbandoned: (info) => abandoned.push(info) },
    });
    await agent.start();
    agent.deliver('stuck audio segment', null, 'receipt-abandoned');
    persistent[0].feed({ type: 'system', subtype: 'init', session_id: agent.sessionId });

    await agent.interruptTurn();
    assert.deepEqual(abandoned, [{
      inboundSeqs: [],
      receiptIds: ['receipt-abandoned'],
    }]);
    await agent.stop();
  });

  it('does not resume a preassigned session that never created a Claude transcript', async () => {
    const store = makeStore({
      globalOverride: {
        sessionId: '00000000-0000-4000-8000-000000000001',
        policyHash: 'stale-policy',
        runtime: 'claude',
        epoch: 1,
        syncedThroughSeq: 0,
        sessionReady: false,
      },
    });
    const { agent, calls } = harness({ store });
    store.global().policyHash = agent.policyHash;
    await agent.start();
    const args = calls.at(-1).args;
    assert.ok(args.includes('--session-id'));
    assert.ok(!args.includes('--resume'));
    assert.notEqual(agent.sessionId, '00000000-0000-4000-8000-000000000001');
    await agent.stop();
  });

  it('uses a fresh preassigned UUID after a pre-init process crash', async () => {
    const { agent, calls, persistent } = harness({
      agentOptions: { reconnectBaseMs: 1, reconnectCapMs: 2 },
    });
    await agent.start();
    const firstArgs = calls.at(-1).args;
    const firstSessionId = firstArgs[firstArgs.indexOf('--session-id') + 1];

    persistent[0].finish(1);
    await waitFor(() => persistent.length === 2);
    const reconnectArgs = calls.at(-1).args;
    const reconnectSessionId = reconnectArgs[reconnectArgs.indexOf('--session-id') + 1];
    assert.ok(reconnectArgs.includes('--session-id'));
    assert.ok(!reconnectArgs.includes('--resume'));
    assert.notEqual(reconnectSessionId, firstSessionId);
    await agent.stop();
  });

  it('uses a fresh session when an active result becomes ambiguous on disconnect', async () => {
    const { agent, calls, persistent } = harness({
      agentOptions: { reconnectBaseMs: 1, reconnectCapMs: 2 },
    });
    await agent.start();
    const first = persistent[0];
    const oldSessionId = agent.sessionId;
    agent.deliver('possibly accepted', 7);
    first.feed({ type: 'system', subtype: 'init', session_id: oldSessionId });
    first.finish(1);

    await waitFor(() => persistent.length === 2);
    const reconnectArgs = calls.at(-1).args;
    assert.ok(reconnectArgs.includes('--session-id'));
    assert.ok(!reconnectArgs.includes('--resume'));
    assert.notEqual(agent.sessionId, oldSessionId);
    await agent.stop();
  });

  it('fails closed when Claude auth is unavailable', async () => {
    const { agent, calls } = harness({ loggedIn: false });
    await assert.rejects(() => agent.start(), /not logged in/);
    assert.equal(calls.length, 3);
  });

  it('strips Lark secrets and the nested Claude marker from the child env', () => {
    const environment = sanitizeClaudeEnv({
      PATH: '/bin',
      ANTHROPIC_API_KEY: 'keep-for-claude',
      LARK_APP_SECRET: 'remove',
      FEISHU_TOKEN: 'remove',
      LARKSUITE_CLI_PROFILE: 'wrong-profile',
      CLAUDECODE: 'remove',
      ARK_API_KEY: 'remove',
      ARK_SESSION_ID: 'remove',
      SESSION_ID: 'remove',
    }, 'fixed-profile');
    assert.equal(environment.ANTHROPIC_API_KEY, 'keep-for-claude');
    assert.equal(environment.LARK_APP_SECRET, undefined);
    assert.equal(environment.FEISHU_TOKEN, undefined);
    assert.equal(environment.CLAUDECODE, undefined);
    assert.equal(environment.ARK_API_KEY, undefined);
    assert.equal(environment.ARK_SESSION_ID, undefined);
    assert.equal(environment.SESSION_ID, undefined);
    assert.equal(environment.LARKSUITE_CLI_PROFILE, 'fixed-profile');
  });
});
