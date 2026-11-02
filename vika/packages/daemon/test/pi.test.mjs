// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { ovFixture } from './ov-fixture.mjs';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { EventEmitter } from 'node:events';
import { describe, it } from 'node:test';
import { managedPiBin, PI_VERSION, PI_MCP_EXTENSION, PI_MCP_STATUS } from '../src/pi-runtime.mjs';
import { assembleConversationPolicy } from '../src/conversation-policy.mjs';
import {
  PI_EFFORTS,
  PiAgent,
  sanitizePiEnv,
} from '../src/pi.mjs';

const quietLogger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

const baseConfig = {
  piBin: 'pi',
  piModel: 'openai/gpt-4o',
  piEffort: 'medium',
  workspace: '/tmp/vika-workspace',
  projectDir: '/release',
  dataDir: '/tmp/vika-pi-data',
  databasePath: '/tmp/vika-test.sqlite3',
  profile: 'test-profile',
  connectors: ['lark'],
  larkCliBin: '/fake/lark-cli',
  slackCliBin: '/fake-slack/slack',
  actionSocketPath: '/tmp/vika-pi-data/actions.sock',
  actionMcpPath: '/release/packages/daemon/src/actions/mcp.mjs',
  sendTimeoutMs: 5_000,
  maxReplyChars: 1_000,
  dryRun: true,
};

class FakeChild extends EventEmitter {
  constructor() {
    super();
    this.pid = 987_654_322;
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
  '--mode', '--append-system-prompt', '--thinking', '--session-id', '--session-dir',
  '--model', '--extension', '--approve', 'rpc',
].join('\n');

function parsedWrites(child) {
  return child.writes.map((line) => JSON.parse(line));
}

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
    getCognitionState: () => ({ runtime: 'pi', epoch: 1, authorityToken: 'token-pi' }),
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

// Pi answers its first command only after every session_start handler returns.
const answerSteering = (child, { id }) => child.feed({ type: 'response', id, command: 'set_steering_mode', success: true });

function harness({ config = baseConfig, store = makeStore(), agentOptions = {}, version = PI_VERSION, mcpStatus = 'ready',
  onSteering = answerSteering } = {}) {
  const calls = [];
  const persistent = [];
  const spawn = (bin, args, options) => {
    const child = new FakeChild();
    calls.push({ bin, args, child, options });
    queueMicrotask(() => {
      if (args.includes('--version')) {
        child.stdout.emit('data', `${version}\n`);
        child.finish();
      } else if (args.includes('--help')) {
        child.stdout.emit('data', helpFlags);
        child.finish();
      } else {
        persistent.push(child);
        const write = child.stdin.write;
        child.stdin.write = (value) => {
          const payload = JSON.parse(value);
          if (payload.type === 'set_steering_mode') queueMicrotask(() => onSteering(child, payload));
          return write(value);
        };
        child.emit('spawn');
        if (mcpStatus) child.feed({ type: 'extension_ui_request', method: 'notify', message: `${PI_MCP_STATUS}:${mcpStatus}` });
      }
    });
    return child;
  };
  const failures = [];
  const agent = new PiAgent(config, store, quietLogger, {
    spawn,
    startTimeoutMs: 100,
    onTurnFailed: (failure) => failures.push(failure),
    ...agentOptions,
  });
  return { agent, calls, persistent, store, failures };
}

describe('PiAgent persistent rpc runtime', () => {
  it('changes models between turns and waits for model and effort acknowledgement in the same session', async () => {
    const { agent, persistent, failures } = harness();
    await agent.start();
    const child = persistent[0];
    const id = agent.threadId;
    agent.deliver('active input', 7);
    agent.setModel('anthropic/claude-sonnet');
    assert.equal(parsedWrites(child).some((request) => request.type === 'set_model'), false);
    child.feed({ type: 'agent_settled' });
    await waitFor(() => !agent.turnActive);
    agent.deliver('next input');
    const model = parsedWrites(child).at(-1);
    assert.equal(model.type, 'set_model');
    assert.equal(model.provider, 'anthropic');
    assert.equal(model.modelId, 'claude-sonnet');
    child.feed({ type: 'response', id: model.id, command: 'set_model', success: true });
    await waitFor(() => parsedWrites(child).at(-1).type === 'set_thinking_level');
    const effort = parsedWrites(child).at(-1);
    assert.equal(parsedWrites(child).filter((request) => request.type === 'prompt').length, 1);
    child.feed({ type: 'response', id: effort.id, command: effort.type, success: true });
    await agent.settingsUpdate;
    assert.equal(parsedWrites(child).at(-1).type, 'prompt');
    assert.equal(agent.threadId, id);
    assert.equal(persistent.length, 1);
    assert.equal(agent.appliedModel, 'anthropic/claude-sonnet');
    assert.deepEqual(failures, []);
    child.feed({ type: 'agent_settled' });
    await waitFor(() => !agent.turnActive);
    await agent.stop();
  });

  it('resolves the default Pi model from state before listing its capabilities', async () => {
    const { agent, persistent } = harness({ agentOptions: { model: '' } });
    await agent.start();
    const child = persistent[0];
    const pending = agent.listModels();
    const state = parsedWrites(child).at(-1);
    assert.equal(state.type, 'get_state');
    child.feed({ type: 'response', id: state.id, success: true, data: { model: { provider: 'openai', id: 'gpt-test' } } });
    await waitFor(() => parsedWrites(child).at(-1).type === 'get_available_models');
    const catalog = parsedWrites(child).at(-1);
    child.feed({ type: 'response', id: catalog.id, success: true, data: { models: [{ provider: 'openai', id: 'gpt-test', reasoning: true }] } });
    assert.equal((await pending)[0].id, agent.resolvedModel);
    assert.equal(agent.resolvedModel, 'openai/gpt-test');
    await agent.stop();
  });

  it('applies effort between turns through RPC and waits for acknowledgement before the next prompt', async () => {
    const { agent, persistent, store, failures } = harness();
    await agent.start();
    const child = persistent[0];
    const sessionId = agent.threadId;
    agent.deliver('current request', 7);
    agent.setEffort('high');
    agent.deliver('live correction', 8);
    assert.equal(parsedWrites(child).some((command) => command.type === 'set_thinking_level'), false);
    child.feed({ type: 'agent_settled' });
    const before = store.global();
    agent.deliver('next request');
    agent.deliver('next correction');
    const update = parsedWrites(child).at(-1);
    assert.equal(update.type, 'set_thinking_level');
    assert.equal(update.level, 'high');
    assert.equal(parsedWrites(child).filter((command) => command.type === 'prompt').length, 1);
    child.feed({ type: 'response', id: update.id, command: update.type, success: true });
    await agent.settingsUpdate;
    const next = parsedWrites(child).at(-1);
    assert.equal(next.type, 'prompt');
    assert.equal(next.message, 'next request\nnext correction');
    assert.equal(agent.appliedEffort, 'high');
    assert.equal(agent.threadId, sessionId);
    assert.deepEqual(store.global(), before);
    assert.equal(persistent.length, 1);
    assert.equal(parsedWrites(child).some((command) => command.type === 'abort'), false);
    assert.equal(failures.length, 0);
    child.feed({ type: 'agent_settled' });
    await agent.stop();
  });

  it('reports a rejected effort update and can retry in the same session', async () => {
    const { agent, persistent, failures } = harness();
    await agent.start();
    const child = persistent[0];
    const sessionId = agent.threadId;
    agent.setEffort('high');
    agent.deliver('waiting input', 7, 'receipt-effort');
    const update = parsedWrites(child).at(-1);
    child.feed({ type: 'response', id: update.id, command: update.type, success: false, error: 'effort rejected' });
    await agent.settingsUpdate;
    assert.equal(failures.length, 1);
    assert.deepEqual(failures[0].receiptIds, ['receipt-effort']);
    assert.match(failures[0].error, /effort rejected/);
    assert.equal(parsedWrites(child).some((command) => command.type === 'prompt'), false);
    assert.equal(agent.appliedEffort, 'medium');
    agent.deliver('retry input', 7);
    const retry = parsedWrites(child).at(-1);
    child.feed({ type: 'response', id: retry.id, command: retry.type, success: true });
    await agent.settingsUpdate;
    assert.equal(parsedWrites(child).at(-1).type, 'prompt');
    assert.equal(agent.threadId, sessionId);
    assert.equal(persistent.length, 1);
    child.feed({ type: 'agent_settled' });
    await agent.stop();
  });

  it('coalesces effort changes during an RPC before releasing queued input', async () => {
    const { agent, persistent } = harness();
    await agent.start();
    const child = persistent[0];
    agent.setEffort('high');
    agent.deliver('waiting input', 7);
    const first = parsedWrites(child).at(-1);
    agent.setEffort('xhigh');
    child.feed({ type: 'response', id: first.id, command: first.type, success: true });
    await waitFor(() => parsedWrites(child).at(-1).level === 'xhigh');
    assert.equal(parsedWrites(child).some((command) => command.type === 'prompt'), false);
    const second = parsedWrites(child).at(-1);
    child.feed({ type: 'response', id: second.id, command: second.type, success: true });
    await agent.settingsUpdate;
    assert.equal(agent.appliedEffort, 'xhigh');
    assert.equal(parsedWrites(child).filter((command) => command.type === 'prompt').length, 1);
    child.feed({ type: 'agent_settled' });
    await agent.stop();
  });

  it('cancels a pending effort RPC on stop without delivering or losing its queued receipt', async () => {
    const { agent, persistent, failures } = harness();
    await agent.start();
    const child = persistent[0];
    agent.setEffort('high');
    agent.deliver('waiting input', 7, 'receipt-stop');
    await agent.stop();
    assert.equal(agent.child, null);
    assert.equal(agent.settingsUpdate, null);
    assert.equal(parsedWrites(child).some((command) => command.type === 'prompt'), false);
    assert.equal(failures.length, 1);
    assert.deepEqual(failures[0].receiptIds, ['receipt-stop']);
  });

  it('preflights, starts pi --mode rpc, and prefixes the first real turn', async () => {
    const { agent, calls, persistent, store } = harness();
    await agent.start();

    const args = calls.at(-1).args;
    assert.equal(args[args.indexOf('--mode') + 1], 'rpc');
    assert.ok(args.includes('--mode'));
    assert.ok(args.includes('--append-system-prompt'));
    assert.equal(args[args.indexOf('--thinking') + 1], 'medium');
    assert.equal(args[args.indexOf('--model') + 1], 'openai/gpt-4o');
    assert.ok(args.includes('--session-id'));
    const env = calls.at(-1).options.env;
    assert.equal(env.LARKSUITE_CLI_PROFILE, 'test-profile');
    assert.ok(calls.every((call) => call.options.env.PATH.startsWith('/fake:/fake-slack:')));
    assert.equal(env.PERSONAL_AGENT_SOCKET, '/tmp/vika-pi-data/actions.sock');
    assert.equal(env.PERSONAL_AGENT_AUTHORITY, agent.authorityToken);
    assert.equal(env.LARK_APP_SECRET, undefined);
    assert.equal(env.PI_LARK_TOOLS_CONFIG, undefined);
    const promptPath = args[args.indexOf('--append-system-prompt') + 1];
    const runtimePrompt = fs.readFileSync(promptPath, 'utf8');
    assert.equal(runtimePrompt, assembleConversationPolicy(baseConfig));
    assert.doesNotMatch(runtimePrompt, /Agent tools from bash|actions\/cli\.mjs/);
    assert.doesNotMatch(runtimePrompt, /lark-cli im \+messages-reply/);
    assert.equal(args[args.indexOf('--extension') + 1], PI_MCP_EXTENSION);
    assert.deepEqual(JSON.parse(env.PERSONAL_AGENT_MCP_CONFIG), {
      command: process.execPath,
      args: [baseConfig.actionMcpPath, '--socket', baseConfig.actionSocketPath, '--authority', agent.authorityToken],
      timeout: 10,
    });

    agent.deliver('[chat=oc_now12345678 msg=om_now12345678] current request', 7);
    const prompt = parsedWrites(persistent[0]).find((message) => message.type === 'prompt');
    assert.ok(prompt);
    assert.match(prompt.message, /daemon OpenViking context/);
    assert.match(prompt.message, /earlier question/);
    assert.match(prompt.message, /earlier answer/);
    assert.match(prompt.message, /history above, new messages below/);
    assert.match(prompt.message, /current request$/);
    assert.equal(store.global().runtime, 'pi');
    await agent.stop();
  });

  it('runs the managed Pi with the daemon Node, and an explicit PI_BIN as given', async () => {
    const managed = harness({ config: { ...baseConfig, piBin: managedPiBin() } });
    await managed.agent.start();
    assert.equal(managed.calls.length, 3);
    for (const { bin, args } of managed.calls) {
      assert.equal(bin, process.execPath);
      assert.equal(args[0], managedPiBin());
    }
    assert.deepEqual(managed.calls.at(-1).args.slice(1, 3), ['--mode', 'rpc']);
    await managed.agent.stop();

    const explicit = harness();
    await explicit.agent.start();
    for (const { bin, args } of explicit.calls) {
      assert.equal(bin, 'pi');
      assert.notEqual(args[0], managedPiBin());
    }
    await explicit.agent.stop();
  });

  it('rejects unsupported Pi versions before starting a session', async () => {
    const { agent, persistent } = harness({ version: '0.87.1' });
    await assert.rejects(agent.start(), /Pi .* is required.*npm ci/);
    assert.equal(persistent.length, 0);
    assert.equal(agent.connected, false);
  });

  it('does not report ready when the Pi process starts without MCP tools', async () => {
    const { agent, persistent, store } = harness({ mcpStatus: null, agentOptions: { startTimeoutMs: 25 } });
    await assert.rejects(agent.start(), /Pi startup timed out/);
    assert.equal(agent.connected, false);
    assert.equal(store.global().sessionReady, false);
    assert.notEqual(persistent[0].exitCode, null);
  });

  it('fails the start when an extension opens a dialog before Pi reads commands', async () => {
    const { agent, persistent, store } = harness({
      onSteering: (child) => child.feed({ type: 'extension_ui_request', id: 'startup-dialog', method: 'confirm', title: 'Trust?' }),
    });
    await assert.rejects(agent.start(), /interactive dialog during startup/);
    assert.equal(agent.connected, false);
    assert.equal(store.global().sessionReady, false);
    assert.notEqual(persistent[0].exitCode, null);
    assert.ok(!parsedWrites(persistent[0]).some(({ type }) => type === 'extension_ui_response'),
      'Pi cannot read a dialog answer before session_start returns');
  });

  it('is not ready until Pi answers its first command', async () => {
    const { agent, store } = harness({ onSteering: () => {}, agentOptions: { startTimeoutMs: 25 } });
    await assert.rejects(agent.start(), /Pi startup timed out/);
    assert.equal(agent.connected, false);
    assert.equal(store.global().sessionReady, false);
  });

  it('rejects failed MCP initialization without publishing the session', async () => {
    const { agent, store } = harness({ mcpStatus: 'failed' });
    await assert.rejects(agent.start(), /could not load the Core MCP tools/);
    assert.equal(store.global().sessionReady, false);
  });

  it('writes steer for a second deliver during an active turn, not a second unadorned prompt', async () => {
    const { agent, persistent } = harness();
    await agent.start();
    const child = persistent[0];
    agent.deliver('first request', 7);
    agent.deliver('live correction', 8);

    const commands = parsedWrites(child).filter((message) => message.type === 'prompt' || message.type === 'steer');
    assert.equal(commands.length, 2);
    assert.equal(commands[0].type, 'prompt');
    assert.match(commands[0].message, /first request/);
    assert.equal(commands[1].type, 'steer');
    assert.equal(commands[1].message, 'live correction');
    assert.ok(!commands.some((message) => message.type === 'prompt' && message !== commands[0]));
    assert.equal(agent.turnInProgress, true);
    child.feed({ type: 'agent_settled' });
    assert.equal(agent.turnInProgress, false);
    await agent.stop();
  });

  it('demotes the stored session when stopped mid-turn, keeps it after a clean stop', async () => {
    const clean = harness();
    await clean.agent.start();
    clean.agent.deliver('quick request', 7);
    clean.persistent[0].feed({ type: 'agent_settled' });
    await clean.agent.stop();
    assert.equal(clean.store.global().sessionReady, true);

    const killed = harness();
    await killed.agent.start();
    killed.agent.deliver('long request', 7);
    await killed.agent.stop();
    assert.equal(killed.store.global().sessionReady, false);
  });

  it('keeps queued work across disconnect and writes it after respawn', async () => {
    const { agent, persistent, failures } = harness({
      agentOptions: { reconnectBaseMs: 1, reconnectCapMs: 2 },
    });
    await agent.start();
    persistent[0].finish(1);
    agent.deliver('queued after disconnect', 8);
    await waitFor(() => persistent.length === 2);
    await waitFor(() => parsedWrites(persistent[1]).some((message) => (
      message.type === 'prompt' && String(message.message).includes('queued after disconnect')
    )));
    assert.equal(failures.length, 0);
    await agent.stop();
  });

  it('interrupt emits turn-abandoned receipts and does not raise onTurnFailed', async () => {
    const abandoned = [];
    const { agent, persistent, failures } = harness({
      agentOptions: { onTurnAbandoned: (info) => abandoned.push(info) },
    });
    await agent.start();
    const child = persistent[0];
    agent.deliver('stuck request', 7);
    assert.equal(agent.turnInProgress, true);

    await agent.interruptTurn();
    assert.equal(agent.turnInProgress, false);
    assert.equal(failures.length, 0);
    assert.deepEqual(abandoned, [{ inboundSeqs: [7] }]);
    assert.ok(parsedWrites(child).some((message) => message.type === 'abort'));
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
    assert.equal(
      parsedWrites(persistent[1]).some((message) => message.type === 'prompt' || message.type === 'steer'),
      false,
    );

    assert.equal(agent.commitSessionRotation(prepared, { persist: false }), prepared.id);
    assert.deepEqual(store.global(), previousGlobal);
    await agent.stop();
  });

  it('restores model and effort before draining queued work after a cancelled prepare', async () => {
    const { agent, calls, persistent } = harness({
      agentOptions: { reconnectBaseMs: 1, reconnectCapMs: 2 },
    });
    await agent.start();
    agent.model = 'google/gemini-pro';
    agent.effort = 'max';
    agent.queue.push([{ text: 'queued during transition', seq: 7 }]);

    await agent.prepareSessionRotation({
      rollback: { model: 'openai/gpt-4o', effort: 'medium' },
    });
    assert.equal(
      parsedWrites(persistent[1]).some((message) => message.type === 'prompt'),
      false,
    );
    await agent.cancelSessionRotation();

    assert.equal(agent.model, 'openai/gpt-4o');
    assert.equal(agent.effort, 'medium');
    const persistentCalls = calls.filter(({ args }) => args.includes('--mode'));
    const restoredArgs = persistentCalls.at(-1).args;
    assert.equal(restoredArgs[restoredArgs.indexOf('--model') + 1], 'openai/gpt-4o');
    assert.equal(restoredArgs[restoredArgs.indexOf('--thinking') + 1], 'medium');
    await waitFor(() => parsedWrites(persistent[2]).some((message) => (
      message.type === 'prompt' && String(message.message).includes('queued during transition')
    )));
    persistent[2].feed({ type: 'agent_settled' });
    await agent.stop();
  });

  it('maps get_available_models onto provider/id catalog entries', async () => {
    const { agent, persistent } = harness();
    await agent.start();
    const pending = agent.listModels();
    await waitFor(() => parsedWrites(persistent[0]).some((message) => message.type === 'get_available_models'));
    const request = parsedWrites(persistent[0]).find((message) => message.type === 'get_available_models');
    persistent[0].feed({
      type: 'response',
      id: request.id,
      command: 'get_available_models',
      success: true,
      data: {
        models: [
          { provider: 'openai', id: 'gpt-4o', reasoning: true },
          { provider: 'google', id: 'gemini-flash', reasoning: false },
        ],
      },
    });
    assert.deepEqual(await pending, [
      { id: 'openai/gpt-4o', efforts: [...PI_EFFORTS] },
      { id: 'google/gemini-flash', efforts: ['off'] },
    ]);
    await agent.stop();
  });

  it('strips Lark secrets but preserves the configured Pi model credential', () => {
    const environment = sanitizePiEnv({
      PATH: '/bin',
      OPENAI_API_KEY: 'keep-for-pi',
      LARK_APP_SECRET: 'remove',
      FEISHU_TOKEN: 'remove',
      LARKSUITE_CLI_PROFILE: 'wrong-profile',
      ARK_API_KEY: 'keep-for-pi',
      SESSION_ID: 'remove',
    }, 'fixed-profile');
    assert.equal(environment.OPENAI_API_KEY, 'keep-for-pi');
    assert.equal(environment.LARK_APP_SECRET, undefined);
    assert.equal(environment.FEISHU_TOKEN, undefined);
    assert.equal(environment.ARK_API_KEY, 'keep-for-pi');
    assert.equal(environment.SESSION_ID, undefined);
    assert.equal(environment.LARKSUITE_CLI_PROFILE, 'fixed-profile');
  });
});
