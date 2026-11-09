// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// DSH adapter suite — initialize downgrade, catalogue availability and credentials.
// Every case drives a real DshAgent over a fake bridge process; the fake child
// and the store double live in ./dsh-agent-fixture.mjs.

import assert from 'node:assert/strict';
import path from 'node:path';
import fs from 'node:fs';
import { DshAgent } from '../src/dsh.mjs';
import { describe, it } from './dsh-node-gate.mjs';
import {
  FakeChild, flush, newAgent, startAgent, waitFor, event, settle, profilePatchFile,
} from './dsh-agent-fixture.mjs';

describe('DshAgent initialize downgrades', () => {
  it('drops the reasoning effort once when initialize refuses it', async () => {
    const context = newAgent();
    const child = new FakeChild();
    const originalAnswer = child._answer.bind(child);
    child._answer = (frame) => {
      if (frame.method !== 'initialize' || frame.params.reasoningEffort === undefined) {
        originalAnswer(frame);
        return;
      }
      queueMicrotask(() => child.feed({ id: frame.id, error: {
        code: -32603,
        message: 'provider "deepseek-official" model "deepseek-flash" does not support reasoning effort "max"',
      } }));
    };
    context.agent._spawn = () => child;
    context.agent.setEffort('max', { persist: false });
    await context.agent.start();
    const attempts = child.requests('initialize');
    assert.equal(attempts.length, 2);
    assert.equal(attempts[0].params.reasoningEffort, 'max');
    // The retry omits the effort instead of substituting another one.
    assert.equal('reasoningEffort' in attempts[1].params, false);
    // Applied state records the request: no effort was sent, so none is pinned.
    assert.equal(context.agent.appliedEffort, null);
    assert.equal(context.agent.appliedModel, 'deepseek-official/deepseek-flash');
    assert.equal(context.agent._selectionPending(), false);
    assert.ok(context.warnings.some((entry) => entry.event === 'dsh_model_downgraded'));
    await context.agent.stop();
  });

  it('falls back to the built-in default when initialize does not know the route', async () => {
    const context = newAgent();
    const child = new FakeChild();
    const originalAnswer = child._answer.bind(child);
    child._answer = (frame) => {
      if (frame.method !== 'initialize' || frame.params.model !== 'deepseek-v4-max') {
        originalAnswer(frame);
        return;
      }
      queueMicrotask(() => child.feed({ id: frame.id, error: {
        code: -32603, message: 'unknown model: deepseek-official/deepseek-v4-max',
      } }));
    };
    context.agent._spawn = () => child;
    context.agent.model = 'deepseek-official/deepseek-v4-max';
    await context.agent.start();
    const attempts = child.requests('initialize');
    assert.equal(attempts.length, 2);
    assert.equal(attempts[0].params.model, 'deepseek-v4-max');
    assert.deepEqual(attempts[1].params, {
      cwd: context.config.workspace,
      provider: 'deepseek-official',
      model: 'deepseek-flash',
      reasoningEffort: 'high',
      approvalRequests: true,
    });
    // The process runs the fallback; the persisted choice stays the user's.
    assert.equal(context.agent.model, 'deepseek-official/deepseek-flash');
    assert.equal(context.agent.appliedModel, 'deepseek-official/deepseek-flash');
    assert.equal(context.store.settings.has('dsh_model'), false);
    assert.ok(context.warnings.some((entry) => entry.event === 'dsh_model_downgraded'));
    await context.agent.stop();
  });

  it('classifies a bridge error by its data.code instead of its message', async () => {
    const context = newAgent();
    const child = new FakeChild();
    const originalAnswer = child._answer.bind(child);
    child._answer = (frame) => {
      if (frame.method !== 'initialize' || frame.params.reasoningEffort === undefined) {
        originalAnswer(frame);
        return;
      }
      // No recognizable text: only data.code says what went wrong.
      queueMicrotask(() => child.feed({ id: frame.id, error: {
        code: -32603, message: 'refused', data: { code: 'UNSUPPORTED_REASONING_EFFORT' },
      } }));
    };
    context.agent._spawn = () => child;
    await context.agent.start();
    assert.equal(child.requests('initialize').length, 2);
    assert.equal('reasoningEffort' in child.requests('initialize')[1].params, false);
    await context.agent.stop();
  });

  it('starts and warns when the model settings document did not activate', async () => {
    const context = newAgent({ childOptions: { settingsDescriptor: null } });
    const { agent } = context;
    await startAgent(context);
    // A broken `llm-pi-ai` document is not a startup failure: DSH still serves
    // the built-in routes, so the daemon reports it and keeps running.
    assert.equal(agent.connected, true);
    const warning = context.warnings.find((entry) => entry.event === 'dsh_model_settings_invalid');
    assert.ok(warning, JSON.stringify(context.warnings));
    assert.equal(warning.fields.path, profilePatchFile(context.config));
    await agent.stop();
  });
});

describe('DshAgent model availability', () => {
  it('surfaces an initialize model rejection as a structured MODEL_UNAVAILABLE', async () => {
    const context = newAgent();
    const child = new FakeChild();
    child._answer = (frame) => {
      if (frame.method === 'initialize') {
        queueMicrotask(() => child.feed({
          id: frame.id,
          error: { code: -32603, message: 'unknown model: deepseek-official/nope', data: { status: 404 } },
        }));
      }
    };
    context.agent._spawn = () => child;
    context.agent.model = 'deepseek-official/nope';
    await assert.rejects(context.agent.start(), (error) => {
      assert.equal(error.code, 'MODEL_UNAVAILABLE');
      assert.equal(error.rpcCode, -32603);
      assert.equal(error.status, 404);
      return true;
    });
    assert.equal(context.agent.connected, false);
  });

  it('leaves an unrelated initialize failure unstructured', async () => {
    const context = newAgent();
    const child = new FakeChild();
    child._answer = (frame) => {
      if (frame.method === 'initialize') {
        queueMicrotask(() => child.feed({
          id: frame.id, error: { code: -32603, message: 'workspace is not a directory' },
        }));
      }
    };
    context.agent._spawn = () => child;
    await assert.rejects(context.agent.start(), (error) => {
      assert.notEqual(error.code, 'MODEL_UNAVAILABLE');
      assert.equal(error.rpcCode, undefined);
      return true;
    });
  });
});

describe('DshAgent credential pre-check', () => {
  it('starts a deepseek-official route whose key is only in the DSH credentials file', async () => {
    const context = newAgent();
    const { agent, config } = context;
    // No DEEPSEEK_API_KEY in the child environment at all.
    agent._environment = { PATH: '/usr/bin:/bin' };
    // The credential is DSH's own: the file `vika dsh model key set` writes.
    const home = path.join(config.dataDir, 'dsh', 'home');
    fs.mkdirSync(home, { recursive: true, mode: 0o700 });
    fs.writeFileSync(path.join(home, '.credentials.yaml'), 'version: 1\nrefs:\n  DEEPSEEK_API_KEY: sk-from-credentials\n');
    agent.setModel('deepseek-official/deepseek-flash');
    await agent.start();
    await flush();
    // The process was spawned and the handshake completed.
    assert.equal(context.children.length, 1);
    assert.equal(agent.connected, true);
    assert.equal(agent.appliedModel, 'deepseek-official/deepseek-flash');
    // Nothing was invented into the child environment: DSH reads its own file.
    assert.equal(context.spawnCalls[0].options.env.DEEPSEEK_API_KEY, undefined);
    await agent.stop();
  });

  it('starts even with no credential anywhere and lets DSH report the missing key', async () => {
    const context = newAgent();
    const { agent } = context;
    agent._environment = { PATH: '/usr/bin:/bin' };
    agent.setModel('deepseek-official/deepseek-flash');
    await agent.start();
    await flush();
    assert.equal(context.children.length, 1);
    assert.equal(agent.connected, true);
    // The first turn reaches DSH, which answers MISSING_CREDENTIAL itself.
    agent.deliver('needs a key', 7, 'r-nokey');
    const child = context.children[0];
    await waitFor(() => child.requests('session/steer').length === 1);
    const steer = child.requests('session/steer')[0];
    child.respond(steer, { messageId: 'm-nokey' });
    event(child, agent.threadId, 'user/message', { id: 'm-nokey', content: [] });
    await flush();
    await settle(child, agent, {
      kind: 'error', error: { message: 'MISSING_CREDENTIAL: no api key for deepseek-official', code: 'MISSING_CREDENTIAL' },
    });
    assert.equal(context.failures.length, 1);
    assert.match(context.failures[0].error, /MISSING_CREDENTIAL/);
    await agent.stop();
  });
});
