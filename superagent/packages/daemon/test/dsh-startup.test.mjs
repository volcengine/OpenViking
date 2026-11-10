// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// DSH adapter suite — process start, version gate, handshake and shutdown.
// Every case drives a real DshAgent over a fake bridge process; the fake child
// and the store double live in ./dsh-agent-fixture.mjs.

import assert from 'node:assert/strict';
import path from 'node:path';
import fs from 'node:fs';
import { DshAgent } from '../src/dsh.mjs';
import { after } from 'node:test';
import { describe, it } from './dsh-node-gate.mjs';
import {
  baseConfig, makeStore, newAgent, quietLogger, startAgent, waitFor, acknowledge,
} from './dsh-agent-fixture.mjs';

describe('DshAgent startup and SDK transport', () => {
  it('probes the version and spawns SDK v1 with isolated env, generated patch and initialize', async () => {
    const context = newAgent();
    context.agent._environment = {
      PATH: '/usr/bin:/bin',
      DEEPSEEK_API_KEY: 'test-key',
      DSH_HOME: '/must-not-leak',
      DSH_PROFILE: 'bad-profile',
      DSH_PERMISSION_MODE: 'workspace-write',
      LARK_APP_SECRET: 'must-not-leak',
    };
    const child = await startAgent(context);
    const call = context.spawnCalls[0];
    assert.equal(call.bin, '/opt/dsh/bin/dsh');
    assert.deepEqual(call.args.slice(0, 2), ['--profile', 'sdk']);
    assert.equal(call.args[2], '--patch');
    assert.equal(call.options.cwd, context.config.workspace);
    assert.equal(call.options.detached, process.platform !== 'win32');
    assert.deepEqual(call.options.stdio, ['pipe', 'pipe', 'pipe']);
    assert.equal(call.options.env.DSH_HOME, path.join(context.config.dataDir, 'dsh', 'home'));
    assert.equal(call.options.env.DSH_AGENTS_HOME, path.join(context.config.dataDir, 'dsh', 'agents'));
    assert.equal(call.options.env.DSH_PERMISSION_MODE, 'danger-full-access');
    assert.equal(call.options.env.DSH_TELEMETRY_DISABLED, '1');
    assert.equal(call.options.env.DSH_PROFILE, undefined);
    assert.equal(call.options.env.LARK_APP_SECRET, undefined);
    assert.ok(call.options.env.PATH.startsWith(`${path.dirname(process.execPath)}:/fake-lark:/fake-slack:`));
    const patch = JSON.parse(fs.readFileSync(call.args[3], 'utf8'));
    assert.ok(patch.some((entry) => entry.id === 'sdk-jsonrpc-server' && entry.disabled));
    const inserted = patch.find((entry) => entry.insert)?.insert ?? [];
    const bridge = inserted.find((entry) => entry.id === 'superagent-dsh-bridge');
    const mcp = inserted.find((entry) => entry.id === 'superagent-personal-agent');
    assert.equal(bridge.config.maxTokensAsSuccess, true);
    assert.deepEqual(mcp.config.args, [
      context.config.actionMcpPath, '--socket', context.config.actionSocketPath,
      '--authority', 'dsh-token',
    ]);
    assert.equal(mcp.config.toolCallTimeoutMs, 10_000);
    const init = child.requests('initialize')[0];
    assert.deepEqual(init.params, {
      cwd: context.config.workspace,
      provider: 'deepseek-official',
      model: 'deepseek-flash',
      reasoningEffort: 'high',
      approvalRequests: true,
    });
    assert.equal(child.requests('superagent/hello').length, 1);
    // The model-settings document is probed once, after the bridge answered.
    assert.equal(child.requests('superagent/settings/describe').length, 1);
    assert.equal(child.requests('session/working-directory/get')[0].params.sessionId, context.agent.threadId);
    assert.equal(context.agent.connected, true);
    assert.equal(context.store.global().sessionReady, true);
    await context.agent.stop();
  });

  it('rejects unsupported versions before spawning DSH', async () => {
    const badVersion = newAgent({ options: { versionProbe: async () => '0.3.0' } });
    await assert.rejects(badVersion.agent.start(), /unsupported DSH version 0\.3\.0/);
    assert.equal(badVersion.children.length, 0);
  });

  it('fails startup when bridge hello is missing or has the wrong version', async () => {
    const context = newAgent({ childOptions: { hello: new Error('method not found') } });
    await assert.rejects(context.agent.start(), /method not found/);
    assert.equal(context.agent.connected, false);
    assert.equal(context.children[0].exitCode !== null, true);
  });

  it('resumes a matching stored session and preserves its journal cursor', async () => {
    const config = baseConfig();
    const probe = new DshAgent(config, makeStore(), quietLogger);
    const store = makeStore({ globalOverride: {
      sessionId: 'saved-session', policyHash: probe.policyHash, runtime: 'dsh', epoch: 1,
      syncedThroughSeq: 4, sessionReady: true,
    } });
    const context = newAgent({ config, store });
    const child = await startAgent(context);
    assert.equal(child.requests('session/resume')[0].params.sessionId, 'saved-session');
    // D4: resume restores the session's own selection, so this process re-asserts
    // the route it initialized with before the first turn.
    assert.deepEqual(child.requests('superagent/session/model/select')[0].params, {
      sessionId: 'saved-session',
      provider: 'deepseek-official',
      model: 'deepseek-flash',
      reasoningEffort: 'high',
    });
    assert.equal(child.requests('session/working-directory/get').length, 0);
    assert.equal(context.agent.threadId, 'saved-session');
    assert.equal(context.agent.syncedThroughSeq, 4);
    assert.equal(context.agent.needsHistoryPrefix, false);
    assert.deepEqual(store.prepared, []);
    await context.agent.stop();
  });

  it('falls back to a fresh session and prepares OV when resume fails', async () => {
    const config = baseConfig();
    const probe = new DshAgent(config, makeStore(), quietLogger);
    const store = makeStore({ globalOverride: {
      sessionId: 'missing-session', policyHash: probe.policyHash, runtime: 'dsh', epoch: 1,
      syncedThroughSeq: 4, sessionReady: true,
    } });
    const context = newAgent({ config, store, childOptions: { resumeError: 'not found' } });
    const child = await startAgent(context);
    assert.equal(child.requests('session/resume').length, 1);
    // A fresh session starts from what initialize set; there is nothing to
    // re-assert on it.
    assert.equal(child.requests('superagent/session/model/select').length, 0);
    const created = child.requests('session/working-directory/get')[0];
    assert.ok(created);
    assert.notEqual(created.params.sessionId, 'missing-session');
    assert.equal(context.agent.syncedThroughSeq, 0);
    assert.equal(context.agent.needsHistoryPrefix, true);
    assert.deepEqual(store.prepared, [1]);
    await context.agent.stop();
  });
});

describe('DshAgent shutdown', () => {
  it('sends shutdown and escalates to SIGTERM then SIGKILL for an uncooperative child', async () => {
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    child.autoHandshake = false;
    child.ignoreShutdown = true;
    child.ignoreSignals = true;
    const signals = [];
    const kill = child.kill.bind(child);
    child.kill = (signal) => { signals.push(signal); kill(signal); };
    const stopping = agent.stop();
    await waitFor(() => signals.includes('SIGKILL'));
    assert.deepEqual(signals, ['SIGTERM', 'SIGKILL']);
    assert.equal(child.requests('shutdown').length, 1);
    child.finish(137, 'SIGKILL');
    await stopping;
  });

  it('fails accepted and queued work when stopped mid-turn', async () => {
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    agent.deliver('accepted', 7);
    await acknowledge(child, child.requests('session/steer')[0]);
    agent.deliver('still running', 8);
    const stopping = agent.stop();
    await stopping;
    assert.equal(context.failures.length, 1);
    assert.deepEqual(context.failures[0].inboundSeqs, [7, 8]);
    assert.match(context.failures[0].error, /stopped during an active turn/);
  });
});
