// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Layer: coordinator — model selection state on AgentRuntime/DshAgent: which
// route a request resolves to, how "no effort override" is represented, and how
// a refused selection is versioned and rolled back. Drives a fake DSH bridge
// process through the daemon's own transition().

import assert from 'node:assert/strict';
import { after } from 'node:test';
import { describe, it } from './dsh-node-gate.mjs';
import { AgentRuntime } from '../src/agent-runtime.mjs';
import { adaptRuntime } from '../src/runtime-adapter.mjs';
import { DshAgent } from '../src/dsh.mjs';
import {
  DEFAULT_MODELS, FakeChild, baseConfig, defaultModelInfo, flush, makeStore,
  newAgent, quietLogger, selectThroughSlashCommand, startAgent, tempDir, waitFor,
} from './dsh-model-selection-fixture.mjs';

describe('dsh effort override representation', () => {
  it('never persists an effort override for a model that offers no effort', async (t) => {
    const models = [
      {
        provider: 'deepseek', name: 'DeepSeek', error: null, credential: null,
        models: [{ id: 'group/no-effort', name: 'No Effort', inputModalities: ['text'] }],
      },
      ...DEFAULT_MODELS,
    ];
    const context = newAgent({
      childOptions: {
        models,
        // A route whose model has no reasoning levels at all.
        modelInfo: (provider, model) => (provider === 'deepseek'
          ? { provider, id: model, name: model, inputModalities: ['text'] }
          : defaultModelInfo(provider, model)),
      },
    });
    t.after(async () => { await context.agent.stop(); });
    await startAgent(context);

    const result = await context.runtime.transition({ kind: 'select-model', selector: 'group/no-effort' });
    assert.equal(result.ok, true);
    assert.equal(result.model, 'deepseek/group/no-effort');
    assert.equal(result.effort, null, 'no effort override is null, never ""');

    // The store keeps the transports' own spelling for "no override" and never
    // a level: a stored level would be applied to a model that offers none, and
    // the string "null" is not a level either.
    assert.equal(context.store.settings.get('dsh_effort') ?? '', '',
      `store wrote an effort override: ${JSON.stringify([...context.store.settings])}`);
    assert.equal(context.store.settings.get('dsh_model'), 'deepseek/group/no-effort');

    // The real failure mode: the next daemon start constructs DshAgent with the
    // persisted settings.
    assert.doesNotThrow(() => new DshAgent(context.config, context.store, quietLogger, {
      spawn: () => new FakeChild({ models }),
      versionProbe: async () => '0.2.2-rc.1',
      environment: { PATH: '/usr/bin:/bin', DEEPSEEK_API_KEY: 'unit-test-key' },
    }), 'a restart must accept the settings the previous selection persisted');
  });

  it('keeps a saved "no override" as no override at start-up', async (t) => {
    const context = newAgent({ store: makeStore({ settings: { dsh_effort: '' } }) });
    // "" is the store's spelling for "no effort override" (a model with no
    // reasoning control). Reading it back as the configured default asked DSH
    // for a level the route refuses, which DSH answered by dropping the effort
    // and injecting a "fix your model configuration" notice on every start.
    assert.equal(context.agent.effort, null, 'a saved "" is no override, not the configured default');
    assert.doesNotThrow(() => new DshAgent(context.config, context.store, quietLogger, {
      spawn: () => new FakeChild(),
      versionProbe: async () => '0.2.2-rc.1',
      environment: { PATH: '/usr/bin:/bin', DEEPSEEK_API_KEY: 'unit-test-key' },
    }));
    t.after(async () => { await context.agent.stop(); });
    const child = await startAgent(context);
    const initialize = child.requests('initialize')[0];
    assert.equal('reasoningEffort' in initialize.params, false,
      'an absent override must not be sent as a level');
    assert.equal(context.agent.effort, null);
    assert.equal(context.agent.appliedEffort, null);
    assert.equal(context.agent._selectionPending(), false);
    assert.ok(!context.agent.turnNotice, 'no "fix your model configuration" notice');
    assert.equal(context.warnings.some((entry) => entry.event === 'dsh_model_downgraded'), false);
  });
});

describe('dsh model selection state', () => {
  it('forgets a refusal when the pair is applied explicitly', async (t) => {
    const context = newAgent({
      selectError: { code: -32603, message: 'does not support reasoning effort "high"', data: { code: 'UNSUPPORTED_REASONING_EFFORT' } },
    });
    t.after(async () => { await context.agent.stop(); });
    const child = await startAgent(context);
    const { agent } = context;

    agent.setModel('deepseek-v4-pro');
    agent.deliver('refused once', 7);
    await waitFor(() => context.failures.length === 1, 'the refusal to be reported');
    assert.equal(child.requests('vika/session/model/select').length, 1);
    assert.deepEqual(agent.rejectedSelection, {
      model: 'deepseek-official/deepseek-v4-pro', effort: 'high',
    });
    assert.equal(agent.model, 'deepseek-official/deepseek-flash', 'the refusal rolls the desire back');

    // This is the path /model takes: RuntimeAdapter.applySettings, not setModel.
    child.selectError = null;
    adaptRuntime('dsh', agent).applySettings({
      model: 'deepseek-official/deepseek-v4-pro', effort: 'high',
    });
    assert.equal(agent.rejectedSelection, null, 'an explicit apply drops the refusal record');
    assert.equal(agent._selectionPending(), true);

    await agent._applyPendingSelection();
    assert.equal(child.requests('vika/session/model/select').length, 2);
    assert.equal(agent.appliedModel, 'deepseek-official/deepseek-v4-pro');
    assert.equal(agent._selectionPending(), false);
  });

  it('resubmits a refused pair when /model asks for it again', async (t) => {
    const context = newAgent({
      selectError: { code: -32603, message: 'does not support reasoning effort "high"', data: { code: 'UNSUPPORTED_REASONING_EFFORT' } },
    });
    t.after(async () => { await context.agent.stop(); });
    const child = await startAgent(context);
    const { agent } = context;

    agent.setModel('deepseek-v4-pro');
    agent.deliver('refused once', 7);
    await waitFor(() => context.failures.length === 1, 'the refusal to be reported');
    assert.equal(child.requests('vika/session/model/select').length, 1);

    // The user fixes the route and asks for the same pair explicitly.
    child.selectError = null;
    const reply = await selectThroughSlashCommand(context, 'deepseek-v4-pro');
    assert.equal(reply.ok, true, reply.text);
    assert.match(reply.text, /已切到 deepseek-official\/deepseek-v4-pro/);
    assert.equal(agent._selectionPending(), true,
      'the explicit re-selection must re-arm the pending switch');

    // "Switched" must mean the next boundary actually asks DSH again.
    agent.deliver('second request', 8);
    await waitFor(() => child.requests('vika/session/model/select').length === 2,
      'the explicit re-selection to reach DSH');
    await waitFor(() => agent.appliedModel === 'deepseek-official/deepseek-v4-pro', 'the applied route');
    assert.deepEqual(child.requests('vika/session/model/select')[1].params, {
      sessionId: agent.threadId,
      provider: 'deepseek-official',
      model: 'deepseek-v4-pro',
      reasoningEffort: 'high',
    });
  });
});

describe('handshake and a newer selection', () => {
  // The handshake's downgrade ladder adopts the pair DSH really started with as
  // the desired one (the user's stored choice stays persisted). A selection made
  // while that handshake is in flight is newer than the snapshot it started from
  // and must survive: it is the value the next turn boundary applies.
  it('keeps a selection made during the handshake instead of the downgrade', async (t) => {
    const store = makeStore({ settings: { dsh_model: 'deepseek/deepseek-chat', dsh_effort: 'high' } });
    const context = newAgent({
      store,
      childOptions: {
        holdInitialize: true,
        initializeError: (provider) => (provider === 'deepseek'
          ? Object.assign(new Error('no adapter registered for provider "deepseek"'), {
            code: -32603, data: { code: 'NO_ADAPTER' },
          })
          : null),
      },
    });
    t.after(async () => { await context.agent.stop(); });
    const { agent } = context;
    const starting = agent.start();
    await waitFor(() => context.children.length === 1, 'the child to be spawned');
    const child = context.children[0];
    // Answer every held initialize until the ladder settles, letting the test
    // act while the first one is still unanswered.
    let settled = false;
    starting.then(() => { settled = true; }, () => { settled = true; });
    await waitFor(() => child.held.length === 1, 'the first initialize');
    agent.setModel('deepseek-v4-pro');
    assert.equal(agent.model, 'deepseek-official/deepseek-v4-pro');
    for (let step = 0; step < 8 && !settled; step += 1) {
      child.releaseInitialize();
      await flush();
      if (settled) break;
      await waitFor(() => child.held.length > 0 || settled, 'the next initialize');
    }
    await starting;
    assert.equal(agent.connected, true);
    // The downgrade did happen: the process runs the fallback route.
    assert.equal(agent.appliedModel, 'deepseek-official/deepseek-flash');
    // ... and the newer choice is still desired, so the boundary applies it
    // instead of silently running the downgraded route forever.
    assert.equal(agent.model, 'deepseek-official/deepseek-v4-pro',
      'the handshake overwrote a selection made while it was in flight');
    assert.equal(agent._selectionPending(), true);

    agent.deliver('after the handshake', 7);
    await waitFor(() => child.requests('vika/session/model/select').length === 1,
      'the newer selection to be applied');
    assert.deepEqual(child.requests('vika/session/model/select')[0].params, {
      sessionId: agent.threadId,
      provider: 'deepseek-official',
      model: 'deepseek-v4-pro',
      reasoningEffort: 'high',
    });
  });

  it('adopts the downgraded pair when nothing newer arrived', async (t) => {
    const store = makeStore({ settings: { dsh_model: 'deepseek/deepseek-chat', dsh_effort: 'high' } });
    const context = newAgent({
      store,
      childOptions: {
        initializeError: (provider) => (provider === 'deepseek'
          ? Object.assign(new Error('no adapter registered for provider "deepseek"'), {
            code: -32603, data: { code: 'NO_ADAPTER' },
          })
          : null),
      },
    });
    t.after(async () => { await context.agent.stop(); });
    const child = await startAgent(context);
    const { agent } = context;
    // Nothing newer arrived: the downgraded route is what this process runs, so
    // it is the desired pair and there is nothing left to apply.
    assert.equal(agent.model, 'deepseek-official/deepseek-flash');
    assert.equal(agent.effort, 'high');
    assert.equal(agent.appliedModel, 'deepseek-official/deepseek-flash');
    assert.equal(agent._selectionPending(), false);
    // The user's stored choice is untouched, so the next start tries it again.
    assert.equal(context.store.settings.get('dsh_model'), 'deepseek/deepseek-chat');
    assert.equal(child.requests('vika/session/model/select').length, 0);
  });
});

describe('model matching', () => {
  // A runtime whose catalogue is the whole answer: two providers expose the
  // same model name, which is where a first-match rule picks the wrong route.
  function fakeRuntime(entries) {
    const store = makeStore();
    const runtime = new AgentRuntime({ workspace: tempDir() }, store, quietLogger, {});
    runtime.runtime = 'dsh';
    const backend = {
      model: 'deepseek-official/deepseek-flash',
      effort: 'high',
      threadId: 'session',
      state: { connected: true, activeTurn: 0, queued: 0 },
      async start() {},
      async stop() { this.state.connected = false; },
      async listModels() { return entries; },
      sessionDescriptor: { sessionId: 'session', policyHash: 'policy', sessionReady: true },
    };
    runtime.backend = adaptRuntime('dsh', backend);
    return { runtime, backend };
  }

  it('refuses a bare name two routes both answer to instead of taking the first', async () => {
    const { runtime } = fakeRuntime([
      { id: 'alpha/model-x', resolvedModel: 'model-x', efforts: ['low', 'high'] },
      { id: 'beta/model-x', resolvedModel: 'model-x', efforts: ['low', 'high'] },
    ]);
    await assert.rejects(
      runtime.transition({ kind: 'select-model', selector: 'model-x' }),
      (error) => /ambiguous/i.test(error.message)
        && error.message.includes('alpha/model-x')
        && error.message.includes('beta/model-x'),
    );
  });

  it('prefers the canonical route over a same-named model on an operator route', async () => {
    const { runtime, backend } = fakeRuntime([
      // The operator route is listed first: a first-match rule picks it even
      // though the bare name canonicalizes onto the deepseek-official route.
      { id: 'deepseek/deepseek-flash', resolvedModel: 'deepseek-flash', efforts: ['low', 'high'] },
      { id: 'deepseek-official/deepseek-flash', resolvedModel: 'deepseek-flash', efforts: ['low', 'high'] },
    ]);
    const result = await runtime.transition({ kind: 'select-model', selector: 'deepseek-flash' });
    assert.equal(result.model, 'deepseek-official/deepseek-flash');
    assert.equal(backend.model, 'deepseek-official/deepseek-flash');
  });

  it('still resolves a unique bare name', async () => {
    const { runtime, backend } = fakeRuntime([
      { id: 'deepseek/deepseek-chat', resolvedModel: 'deepseek-chat', efforts: ['low', 'high'] },
      { id: 'deepseek-official/deepseek-flash', resolvedModel: 'deepseek-flash', efforts: ['low', 'high'] },
    ]);
    const result = await runtime.transition({ kind: 'select-model', selector: 'deepseek-chat' });
    assert.equal(result.model, 'deepseek/deepseek-chat');
    assert.equal(backend.model, 'deepseek/deepseek-chat');
  });

  it('still accepts a request when the catalogue established nothing', async () => {
    const { runtime } = fakeRuntime([]);
    // An unreachable catalogue is not evidence that the model is unknown, so
    // the runtime's canonical form is what gets applied.
    const result = await runtime.transition({ kind: 'select-model', selector: 'deepseek-flash' });
    assert.equal(result.model, 'deepseek-official/deepseek-flash');
  });

  it('keeps an explicit Claude version instead of the alias behind it', async () => {
    const store = makeStore();
    const runtime = new AgentRuntime({ workspace: tempDir() }, store, quietLogger, {});
    runtime.runtime = 'claude';
    const backend = {
      model: 'sonnet',
      effort: 'high',
      threadId: 'claude-session',
      state: { connected: true, activeTurn: 0, queued: 0 },
      async start() {},
      async stop() {},
      async listModels() {
        return [{
          id: 'sonnet', displayName: 'Sonnet 5.5', resolvedModel: 'claude-sonnet-5-5',
          efforts: ['low', 'high'], effortsKnown: true,
        }];
      },
      applySettings({ model, effort = this.effort }) { this.model = model; this.effort = effort; },
    };
    runtime.backend = adaptRuntime('claude', backend);

    // Typing the concrete version must pin that version: applying the alias
    // `sonnet` follows whatever Anthropic later calls current, which is a
    // different model, and the command reported "already sonnet".
    const pinned = await runtime.transition({ kind: 'select-model', selector: 'claude-sonnet-5-5' });
    assert.equal(pinned.model, 'claude-sonnet-5-5');
    assert.equal(pinned.unchanged, false, 'the concrete version is not "already sonnet"');
    assert.equal(backend.model, 'claude-sonnet-5-5');
    assert.equal(store.settings.get('claude_model'), 'claude-sonnet-5-5');

    // The alias itself still selects the alias.
    const alias = await runtime.transition({ kind: 'select-model', selector: 'sonnet' });
    assert.equal(alias.model, 'sonnet');
    assert.equal(alias.unchanged, false);
    assert.equal(backend.model, 'sonnet');
  });

  it('accepts a model id containing @', async () => {
    const { runtime, backend } = fakeRuntime([
      { id: 'deepseek/deepseek-chat@v2', resolvedModel: 'deepseek-chat@v2', efforts: ['low', 'high'] },
    ]);
    const result = await runtime.transition({ kind: 'select-model', selector: 'deepseek-chat@v2' });
    assert.equal(result.model, 'deepseek/deepseek-chat@v2');
    assert.equal(backend.model, 'deepseek/deepseek-chat@v2');
  });
});

// A refusal DSH really sends: the transport keeps -32603 and the string code
// rides in `data.code` (bridge v2.1).
const REFUSED_EFFORT = Object.freeze({
  code: -32603,
  message: 'provider "deepseek-official" model "deepseek-v4-pro" does not support reasoning effort "high"',
  data: { code: 'UNSUPPORTED_REASONING_EFFORT' },
});

describe('a refused selection is versioned by the request it answers', () => {
  // P1-4 ②: B/high is in flight when the user narrows the same model to B/low.
  // Rolling the desire back to the applied route on the older request's failure
  // overwrote the newer intent: the next turn ran A/low instead of B/low.
  it('leaves a newer intent untouched when an older select is refused', async (t) => {
    const context = newAgent({ childOptions: { holdSelect: true } });
    t.after(async () => { await context.agent.stop(); });
    const child = await startAgent(context);
    const { agent } = context;

    agent.setModel('deepseek-official/deepseek-v4-pro');
    agent.setEffort('high');
    agent.deliver('waiting for B/high', 7);
    await waitFor(() => child.heldSelects.length === 1, 'the select to be in flight');
    assert.deepEqual(child.heldSelects[0].params, {
      sessionId: agent.threadId,
      provider: 'deepseek-official', model: 'deepseek-v4-pro', reasoningEffort: 'high',
    });

    // The newer intent arrives while that request is still unanswered.
    adaptRuntime('dsh', agent).applySettings({
      model: 'deepseek-official/deepseek-v4-pro', effort: 'low',
    });
    assert.equal(agent.effort, 'low', 'the newer intent is the desired pair');

    // The older request is refused.
    child.releaseSelect({ error: REFUSED_EFFORT });
    await waitFor(() => child.heldSelects.length === 1, 'the newer pair to be requested');
    // The refusal belongs to the request it answered: the newer pair survives
    // and is the one asked for next.
    assert.equal(agent.model, 'deepseek-official/deepseek-v4-pro',
      'the older refusal rolled the model back over the newer intent');
    assert.equal(agent.effort, 'low', 'the older refusal overwrote the newer effort');
    assert.deepEqual(child.heldSelects[0].params, {
      sessionId: agent.threadId,
      provider: 'deepseek-official', model: 'deepseek-v4-pro', reasoningEffort: 'low',
    });

    // The input that was waiting for the older selection is not failed: it runs
    // once the newer pair is applied.
    assert.equal(context.failures.length, 0, JSON.stringify(context.failures));
    child.releaseSelect();
    await waitFor(() => child.requests('session/steer').length === 1, 'the waiting input to run');
    assert.equal(agent.appliedModel, 'deepseek-official/deepseek-v4-pro');
    assert.equal(agent.appliedEffort, 'low');
  });

  // P1-4 ③: when the select outcome is unknown (the request timed out), the
  // applied pair is not established. The daemon must refuse to run input on a
  // guess instead of rolling the books back to a value nothing confirmed.
  it('does not claim either pair when the select outcome is unknown', async (t) => {
    const context = newAgent({ options: { controlTimeoutMs: 60 } });
    t.after(async () => { await context.agent.stop(); });
    const child = await startAgent(context);
    const { agent } = context;
    const originalRequest = agent._request.bind(agent);
    let timeouts = 0;
    agent._request = (method, params, timeoutMs, extra) => {
      if (method === 'vika/session/model/select') {
        timeouts += 1;
        // Written, never answered: DSH may or may not have recorded it.
        return Promise.reject(Object.assign(new Error('dsh rpc timed out: vika/session/model/select'), {
          code: 'DSH_RPC_TIMEOUT',
        }));
      }
      return originalRequest(method, params, timeoutMs, extra);
    };

    // Snapshot the bookkeeping at the moment the input is refused: the recovery
    // handshake that follows legitimately records what the fresh process runs.
    const refusedWith = [];
    agent.onTurnFailed = (info) => {
      refusedWith.push({
        error: info.error, applied: agent.appliedModel, desired: agent.model,
        unresolved: agent.selectionUnresolved,
      });
    };
    agent.setModel('deepseek-official/deepseek-v4-pro');
    agent.deliver('before an unknown selection', 7);
    await waitFor(() => refusedWith.length === 1, 'the unknown outcome to be reported');
    assert.equal(timeouts, 1);
    assert.match(refusedWith[0].error, /could not be verified/i);
    // Neither pair is claimed: B was never confirmed, and A is not proven either.
    assert.notEqual(refusedWith[0].applied, 'deepseek-official/deepseek-v4-pro',
      'the unconfirmed pair must not be recorded as applied');
    assert.equal(refusedWith[0].unresolved, true);
    assert.equal(refusedWith[0].desired, 'deepseek-official/deepseek-v4-pro', 'the desire is not discarded');
    assert.equal(child.requests('session/steer').length, 0, 'input must not run on a guessed route');

    // A fresh handshake establishes the truth again instead of running on the
    // guess: the process is re-initialized and the applied pair is recorded.
    await waitFor(() => context.children.length === 2, 'the recovery handshake');
    await waitFor(() => agent.selectionUnresolved === false, 'the state to be resolved');
  });

  it('refuses delivery while the applied pair is unknown', async (t) => {
    const context = newAgent();
    t.after(async () => { await context.agent.stop(); });
    const child = await startAgent(context);
    const { agent } = context;
    // The state the unknown-outcome path leaves behind.
    agent.selectionUnresolved = true;
    agent.deliver('input on an unverified route', 7);
    await waitFor(() => context.failures.length === 1, 'the refusal to be reported');
    assert.match(context.failures[0].error, /could not be verified/i);
    assert.deepEqual(context.failures[0].inboundSeqs, [7]);
    assert.equal(child.requests('session/steer').length, 0);
  });

  it('a rotation names the route again, so input runs after /new', async (t) => {
    const context = newAgent();
    t.after(async () => { await context.agent.stop(); });
    const child = await startAgent(context);
    const { agent } = context;
    agent.selectionUnresolved = true;
    // A new session inherits the initialize route: that pair is known without
    // asking DSH, so the unknown state ends with the rotation instead of
    // blocking every later input.
    const previous = agent.threadId;
    agent.commitSessionRotation(await agent.prepareSessionRotation());
    const rotated = agent.threadId;
    assert.notEqual(rotated, previous);
    assert.equal(agent.selectionUnresolved, false);
    agent.deliver('after the rotation', 7);
    await waitFor(() => child.requests('session/steer').length === 1, 'the input to run');
    // The input ran on the new session, and the selection it was waiting for
    // was asserted on that same session first.
    const selectedAt = child.writes.findIndex((frame) => frame.method === 'vika/session/model/select');
    const steeredAt = child.writes.findIndex((frame) => frame.method === 'session/steer');
    assert.ok(selectedAt >= 0 && selectedAt < steeredAt, `${selectedAt} < ${steeredAt}`);
    assert.equal(child.requests('vika/session/model/select')[0].params.sessionId, rotated);
    assert.equal(child.requests('session/steer')[0].params.sessionId, rotated);
  });
});

describe('runtime switches that are allowed to downgrade', () => {
  function stagedBackend({ runtime, model, list = [] }) {
    return {
      runtime,
      model,
      effort: 'high',
      threadId: `${runtime}-session`,
      state: { connected: false, activeTurn: 0, queued: 0 },
      turnInProgress: false,
      resolvedModel: '',
      get sessionDescriptor() {
        return { sessionId: this.threadId, policyHash: `${runtime}-policy`, sessionReady: true };
      },
      async start() { this.state.connected = true; },
      async stop() { this.state.connected = false; },
      async interruptTurn() { this.interrupted = (this.interrupted ?? 0) + 1; },
      activateSession() {},
      async listModels() { return list; },
      applySettings({ model, effort }) { this.model = model; this.effort = effort; },
    };
  }

  it('falls back instead of rejecting a target that started on another route', async () => {
    const store = makeStore({ settings: { dsh_model: 'deepseek/deepseek-chat' } });
    const children = [];
    const warnings = [];
    const dshFactory = (backendOptions) => new DshAgent(baseConfig(), store,
      { ...quietLogger, warn: (event, fields) => warnings.push({ event, fields }) }, {
        ...backendOptions,
        spawn: () => {
          const child = new FakeChild({ initializeError: (provider) => (provider === 'deepseek'
            ? Object.assign(new Error('no adapter registered for provider "deepseek"'), {
              code: -32603, data: { code: 'NO_ADAPTER' },
            })
            : null) });
          children.push(child);
          return child;
        },
        versionProbe: async () => '0.2.2-rc.1',
        environment: { PATH: '/usr/bin:/bin', DEEPSEEK_API_KEY: 'unit-test-key' },
        startTimeoutMs: 2_000, controlTimeoutMs: 1_000,
      });
    const previous = stagedBackend({ runtime: 'codex', model: 'gpt-5.6-sol' });
    const runtime = new AgentRuntime(baseConfig(), store, quietLogger, {
      codexFactory: () => previous,
      dshFactory,
    });
    runtime.runtime = 'codex';
    runtime.backend = adaptRuntime('codex', previous);

    const result = await runtime.transition({ kind: 'select-runtime', runtime: 'dsh' });
    // The previous turn was already interrupted, so the allowed downgrade has to
    // commit instead of failing the whole switch.
    assert.equal(previous.interrupted, 1);
    assert.equal(result.ok, true);
    assert.equal(runtime.runtime, 'dsh');
    assert.deepEqual(result.modelFallback, {
      from: 'deepseek/deepseek-chat', to: 'deepseek-official/deepseek-flash',
    });
    assert.equal(runtime.model, 'deepseek-official/deepseek-flash');
  });
});
