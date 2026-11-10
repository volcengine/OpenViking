// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// DSH adapter suite — reconnect, rotation and the model-settings document watch.
// Every case drives a real DshAgent over a fake bridge process; the fake child
// and the store double live in ./dsh-agent-fixture.mjs.

import assert from 'node:assert/strict';
import path from 'node:path';
import fs from 'node:fs';
import { DshAgent } from '../src/dsh.mjs';
import { dshProfilePatchHash } from '../src/dsh-runtime.mjs';
import { RuntimeAdapter } from '../src/runtime-adapter.mjs';
import { after } from 'node:test';
import { describe, it } from './dsh-node-gate.mjs';
import {
  FakeChild, baseConfig, flush, makeStore, newAgent, startAgent, waitFor, writeProfilePatch, acknowledge, event, settle, defaultModelInfo,
} from './dsh-agent-fixture.mjs';

describe('DshAgent reconnect, settings and rotation', () => {
  it('fails acknowledged work, requeues unacknowledged input, and reconnects to a fresh session', async () => {
    const context = newAgent();
    const { agent } = context;
    const first = await startAgent(context);
    const oldSession = agent.threadId;
    agent.deliver('accepted', 7, 'accepted-receipt');
    await acknowledge(first, first.requests('session/steer')[0], 'accepted-message');
    agent.deliver('ambiguous', 8, 'ambiguous-receipt');
    first.finish(1, null);
    assert.equal(context.failures.length, 1);
    assert.deepEqual(context.failures[0], { inboundSeqs: [7], error: 'dsh connection lost', receiptIds: ['accepted-receipt'] });
    assert.equal(agent.queue.flat().length, 1);
    assert.equal(agent.forceFreshSession, true);
    await waitFor(() => context.children.length === 2 && agent.connected);
    const second = context.children[1];
    assert.notEqual(agent.threadId, oldSession);
    assert.equal(second.requests('session/resume').length, 0);
    await waitFor(() => second.requests('session/steer').length === 1);
    const replay = second.requests('session/steer')[0];
    assert.match(replay.params.contentBlocks[0].text, /ambiguous/);
    assert.match(replay.params.contentBlocks[0].text, /earlier question/);
    await acknowledge(second, replay, 'replayed');
    await settle(second, agent);
    assert.deepEqual(context.successes.at(-1).receiptIds, ['ambiguous-receipt']);
    await agent.stop();
  });

  it('fails queued input after three failed reconnect attempts and continues backing off', async () => {
    const context = newAgent();
    const { agent } = context;
    const first = await startAgent(context);
    // Every replacement fails the bridge probe; only the first one had a valid
    // handshake, so this measures exactly three reconnect failures.
    agent._spawn = (bin, args, options) => {
      const child = new FakeChild({ hello: new Error('reconnect unavailable') });
      context.children.push(child);
      context.spawnCalls.push({ bin, args, options, child });
      return child;
    };
    first.finish(1, null);
    agent.deliver('cannot reconnect', 7);
    await waitFor(() => context.children.length >= 4 && context.failures.length === 1, 2_000);
    assert.match(context.failures[0].error, /reconnect unavailable/);
    assert.equal(agent.queue.length, 0);
    await agent.stop();
  });

  it('applies a model and effort switch with one select at the next turn boundary, without restarting', async () => {
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    const sessionId = agent.threadId;
    agent.setModel('deepseek-v4-pro');
    agent.setEffort('max');
    agent.deliver('after settings', 7);
    await waitFor(() => child.requests('vika/session/model/select').length === 1);
    // A switch is a session selection, not a process restart.
    assert.equal(context.children.length, 1);
    assert.equal(child.exitCode, null);
    assert.deepEqual(child.requests('vika/session/model/select')[0].params, {
      sessionId, provider: 'deepseek-official', model: 'deepseek-v4-pro', reasoningEffort: 'max',
    });
    await waitFor(() => child.requests('session/steer').length === 1);
    // The selection lands before the input, never in the middle of a turn.
    const methods = child.sent().map((frame) => frame.method);
    assert.ok(methods.indexOf('vika/session/model/select') < methods.indexOf('session/steer'));
    assert.equal(agent.appliedModel, 'deepseek-official/deepseek-v4-pro');
    assert.equal(agent.appliedEffort, 'max');
    assert.equal(agent.resolvedModel, 'deepseek-official/deepseek-v4-pro');
    assert.equal(agent._selectionPending(), false);
    assert.equal(context.store.settings.get('dsh_model'), 'deepseek-official/deepseek-v4-pro');
    await acknowledge(child, child.requests('session/steer')[0]);
    await settle(child, agent);
    await agent.stop();
  });

  it('lists only the routes the bridge reports as usable, with metadata from model info', async () => {
    const context = newAgent({ childOptions: {
      // The catalogue's modality survives when the metadata endpoint omits it.
      modelInfo: (provider, model) => ({
        ...defaultModelInfo(provider, model),
        inputModalities: model === 'deepseek-v4-pro' ? ['text', 'image'] : ['text'],
      }),
    } });
    const { agent } = context;
    const child = await startAgent(context);
    const models = await agent.listModels();
    assert.deepEqual(models.map((entry) => entry.id), [
      'deepseek-official/deepseek-flash', 'deepseek-official/deepseek-v4-pro',
    ]);
    // A route without models (an account that is not logged in, a route without
    // a credential) is never offered: /model lists what can actually run.
    assert.ok(!models.some((entry) => entry.id.startsWith('deepseek-account/')));
    assert.equal(child.requests('vika/llm/models').length, 1);
    assert.equal(child.requests('vika/llm/model/info').length, 2);
    const flash = models.find((entry) => entry.id === 'deepseek-official/deepseek-flash');
    assert.deepEqual(flash, {
      id: 'deepseek-official/deepseek-flash',
      displayName: 'DeepSeek Flash',
      resolvedModel: 'deepseek-flash',
      input: ['text'],
      isDefault: true,
      efforts: ['low', 'high'],
      defaultEffort: 'high',
      reasoning: true,
      effortsKnown: true,
    });
    const pro = models.find((entry) => entry.id === 'deepseek-official/deepseek-v4-pro');
    assert.deepEqual(pro.input, ['text', 'image']);
    assert.ok(!pro.isDefault);
    await agent.stop();
  });

  it('serves the last catalogue when the bridge cannot answer instead of emptying the menu', async () => {
    const context = newAgent();
    const { agent } = context;
    await startAgent(context);
    const first = await agent.listModels();
    assert.equal(first.length, 2);
    const request = agent._request.bind(agent);
    agent._request = (method, params, timeoutMs, extra) => method === 'vika/llm/models'
      ? Promise.reject(new Error('catalogue unavailable'))
      : request(method, params, timeoutMs, extra);
    // A failed refresh keeps the entries the menu already showed.
    assert.deepEqual(await agent.listModels(), first);
    assert.ok(context.warnings.some((entry) => entry.event === 'dsh_model_catalog_failed'));
    await agent.stop();
  });

  it('serves the cached catalogue while the process is not running', async () => {
    const context = newAgent();
    const { agent } = context;
    await startAgent(context);
    const first = await agent.listModels();
    await agent.stop();
    assert.equal(agent.connected, false);
    assert.deepEqual(await agent.listModels(), first);
  });

  it('drops the cache when DSH answers that no route is left', async () => {
    // B1: an empty answer is an answer. Caching the previous list instead would
    // make the menu advertise models that were deleted while the process was
    // still connected, and keep doing it after the process went away.
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    const first = await agent.listModels();
    assert.equal(first.length, 2);
    // Every route is gone from DSH's own document, but the process is unchanged,
    // so no restart happens and the very next query sees the empty catalogue.
    child.models = [];
    assert.deepEqual(await agent.listModels(), []);
    assert.deepEqual(agent.modelCatalog, [], 'the empty answer replaces the cache');
    await agent.stop();
    assert.equal(agent.connected, false);
    assert.deepEqual(await agent.listModels(), [], 'a disconnected read no longer serves deleted models');
  });

  it('fails the turn and keeps the process when DSH refuses the selection', async () => {
    const context = newAgent({ childOptions: {
      // Bridge v2.1 keeps DSH's string code on the frame in `data.code`.
      selectError: {
        code: -32603,
        message: 'provider "deepseek" model "deepseek-chat" does not support reasoning effort "max"',
        data: { code: 'UNSUPPORTED_REASONING_EFFORT' },
      },
    } });
    const { agent } = context;
    const child = await startAgent(context);
    agent.setModel('deepseek/deepseek-chat');
    agent.setEffort('max');
    agent.deliver('rejected', 7);
    await waitFor(() => context.failures.length === 1, 2_000);
    assert.match(context.failures[0].error, /dsh model selection failed \(UNSUPPORTED_REASONING_EFFORT\)/);
    // Not restarted and not steered: the input is reported, never silently run
    // on a route the user did not pick.
    assert.equal(context.children.length, 1);
    assert.equal(child.requests('session/steer').length, 0);
    assert.equal(agent.model, 'deepseek-official/deepseek-flash');
    assert.equal(agent.effort, 'high');
    assert.equal(agent._selectionPending(), false);
    // The persisted choice is untouched, so the next start tries it again.
    assert.equal(context.store.settings.get('dsh_model'), 'deepseek/deepseek-chat');
    assert.equal(agent.queue.length, 0);
    assert.ok(context.warnings.some((entry) => entry.event === 'dsh_model_select_failed'));
    await agent.stop();
  });

  it('stops retrying a refused selection and reports the input queued behind it', async () => {
    const context = newAgent({ childOptions: {
      selectError: {
        code: -32603,
        message: 'does not support reasoning effort "high"',
        data: { code: 'UNSUPPORTED_REASONING_EFFORT' },
      },
    } });
    const { agent } = context;
    const child = await startAgent(context);
    // /new: a fresh session inherits the initialize route, so the next boundary
    // re-asserts the selection the session carried.
    agent.commitSessionRotation(await agent.prepareSessionRotation());
    assert.equal(agent._selectionPending(), true);
    // The daemon's availability callback re-claims the group burst on every
    // signal: this is the path that used to resend a refused select forever.
    let claims = 0;
    agent.onAvailable = () => {
      claims += 1;
      if (claims <= 3) agent.tryStartBatch([{ text: 'claimed after the refusal', seq: 8 }]);
    };
    agent.deliver('queued behind the refused selection', 7);
    await waitFor(() => context.failures.length === 1, 2_000);
    // The input is reported, not run on a route the caller did not choose.
    assert.deepEqual(context.failures[0].inboundSeqs, [7]);
    assert.match(context.failures[0].error, /dsh model selection failed \(UNSUPPORTED_REASONING_EFFORT\)/);
    // One attempt: the refused pair is recorded, so neither the failure's own
    // availability signal nor the claim that follows resubmits it.
    assert.equal(child.requests('vika/session/model/select').length, 1);
    assert.equal(agent._selectionPending(), false);
    assert.equal(agent.selectionStale, false);
    assert.deepEqual(agent.rejectedSelection, {
      model: 'deepseek-official/deepseek-flash', effort: 'high',
    });
    // The claim runs on the route the process actually has.
    await waitFor(() => child.requests('session/steer').length === 1, 2_000);
    assert.match(
      child.requests('session/steer')[0].params.contentBlocks[0].text,
      /claimed after the refusal/,
    );
    await acknowledge(child, child.requests('session/steer')[0]);
    await settle(child, agent);
    assert.equal(child.requests('vika/session/model/select').length, 1);
    assert.equal(
      context.warnings.filter((entry) => entry.event === 'dsh_model_select_failed').length, 1,
    );
    await agent.stop();
  });

  it('tries a different model after the previous choice was refused', async () => {
    const context = newAgent({ childOptions: {
      selectError: {
        code: -32603,
        message: 'no adapter registered for provider "deepseek"',
        data: { code: 'NO_ADAPTER' },
      },
    } });
    const { agent } = context;
    const child = await startAgent(context);
    agent.setModel('deepseek/deepseek-chat');
    agent.deliver('first attempt', 7);
    await waitFor(() => context.failures.length === 1, 2_000);
    assert.equal(child.requests('vika/session/model/select').length, 1);
    // The refused pair is remembered and the desired value is back on the route
    // the process really has, so nothing keeps asking for it.
    assert.deepEqual(agent.rejectedSelection, {
      model: 'deepseek/deepseek-chat', effort: 'high',
    });
    assert.equal(agent.model, 'deepseek-official/deepseek-flash');
    assert.equal(agent._selectionPending(), false);
    // A different choice is a new request, and DSH accepts this one.
    child.selectError = null;
    agent.setModel('deepseek-v4-pro');
    assert.equal(agent.rejectedSelection, null);
    agent.deliver('second attempt', 8);
    await waitFor(() => child.requests('vika/session/model/select').length === 2
      && agent.appliedModel === 'deepseek-official/deepseek-v4-pro', 2_000);
    assert.deepEqual(child.requests('vika/session/model/select')[1].params, {
      sessionId: agent.threadId,
      provider: 'deepseek-official', model: 'deepseek-v4-pro', reasoningEffort: 'high',
    });
    assert.equal(agent.appliedModel, 'deepseek-official/deepseek-v4-pro');
    assert.equal(agent._selectionPending(), false);
    await waitFor(() => child.requests('session/steer').length === 1, 2_000);
    await acknowledge(child, child.requests('session/steer')[0], 'second-attempt');
    await settle(child, agent);
    await agent.stop();
  });

  it('records the initialize pair when a resumed session cannot be re-asserted', async () => {
    const store = makeStore();
    const config = baseConfig();
    const first = newAgent({ store, config });
    await startAgent(first);
    await first.agent.stop();
    const second = newAgent({ store, config, childOptions: {
      selectError: {
        code: -32603,
        message: 'does not support reasoning effort "high"',
        data: { code: 'UNSUPPORTED_REASONING_EFFORT' },
      },
    } });
    const child = await startAgent(second);
    // resume restored the session's own selection and DSH refused to re-assert
    // the initialize pair. That pair is what the process still runs, so it is
    // recorded as applied instead of null -- null used to leave the pending test
    // true forever, with no applied pair to roll back to.
    assert.equal(child.requests('session/resume').length, 1);
    assert.equal(child.requests('vika/session/model/select').length, 1);
    assert.ok(second.warnings.some((entry) => entry.event === 'dsh_resume_model_select_failed'));
    assert.equal(second.agent.appliedModel, 'deepseek-official/deepseek-flash');
    assert.equal(second.agent.appliedEffort, 'high');
    assert.equal(second.agent.selectionStale, true);
    second.agent.deliver('boundary retry', 7);
    await waitFor(() => second.failures.length === 1, 2_000);
    // Exactly one more attempt: the boundary asks once, records the refusal, and
    // reports the queued input instead of retrying it on every availability
    // signal.
    assert.equal(child.requests('vika/session/model/select').length, 2);
    assert.equal(second.agent.selectionStale, false);
    assert.equal(second.agent._selectionPending(), false);
    assert.deepEqual(second.failures[0].inboundSeqs, [7]);
    second.agent.deliver('after the refusal', 8);
    await waitFor(() => child.requests('session/steer').length === 1, 2_000);
    assert.equal(child.requests('vika/session/model/select').length, 2);
    await acknowledge(child, child.requests('session/steer')[0], 'after-refusal');
    await settle(child, second.agent);
    await second.agent.stop();
  });

  it('keeps the applied pair bound to the session a rotation created', async () => {
    const context = newAgent();
    const { agent, store } = context;
    const child = await startAgent(context);
    // A model switch is applied on the session that is current at the boundary.
    agent.setModel('deepseek-v4-pro');
    agent.deliver('switch to v4-pro', 7);
    await waitFor(() => agent.appliedModel === 'deepseek-official/deepseek-v4-pro', 2_000);
    await acknowledge(child, child.requests('session/steer')[0]);
    await settle(child, agent);
    assert.equal(agent.appliedModel, 'deepseek-official/deepseek-v4-pro');

    // /new: a fresh session has no recorded selection of its own, so it runs the
    // route this process initialized with again. Keeping the retired session's
    // pair as "applied" is what made a refused re-selection look applied while
    // the next request really ran the initialize route.
    agent.commitSessionRotation(await agent.prepareSessionRotation());
    assert.equal(agent.appliedModel, 'deepseek-official/deepseek-flash',
      'a new session inherits the initialize route, not the retired selection');
    assert.equal(agent.appliedEffort, 'high');
    assert.equal(agent._selectionPending(), true);

    // The re-assertion on the new session is refused.
    child.selectError = {
      code: -32603,
      message: 'does not support reasoning effort "high"',
      data: { code: 'UNSUPPORTED_REASONING_EFFORT' },
    };
    agent.deliver('after /new', 8);
    await waitFor(() => context.failures.length === 1, 2_000);
    // The books now say what the session really runs: the refusal rolled the
    // desire back to the pair the new session inherited, never to a pair from a
    // session that no longer exists.
    assert.equal(agent.model, 'deepseek-official/deepseek-flash');
    assert.equal(agent.appliedModel, 'deepseek-official/deepseek-flash');
    assert.equal(agent._selectionPending(), false);
    // The persisted intent is untouched, so the next start tries it again.
    assert.equal(store.settings.get('dsh_model'), 'deepseek-official/deepseek-v4-pro');

    // The next input runs the route the books name, with no extra select.
    agent.deliver('next turn', 9);
    await waitFor(() => child.requests('session/steer').length === 2, 2_000);
    assert.equal(child.requests('vika/session/model/select').length, 2);
    await acknowledge(child, child.requests('session/steer')[1], 'after-rotation');
    await settle(child, agent);
    await agent.stop();
  });

  it('prepares, commits, preserves context, cancels, and defers disconnected rotation', async () => {
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    const old = agent.threadId;
    const preparing = agent.prepareSessionRotation();
    await flush();
    const prepared = await preparing;
    assert.equal(child.requests('session/working-directory/get').at(-1).params.sessionId, prepared.id);
    assert.equal(agent.threadId, old);
    agent.commitSessionRotation(prepared, { persist: true, preserveContext: true });
    assert.equal(agent.threadId, prepared.id);
    assert.equal(agent.needsHistoryPrefix, true);
    agent.deliver('new session input', 7);
    // A new session inherits the initialize route, so the selection is
    // re-asserted before its first input.
    await waitFor(() => child.requests('session/steer').length === 1);
    assert.deepEqual(child.requests('vika/session/model/select').at(-1).params, {
      sessionId: prepared.id, provider: 'deepseek-official', model: 'deepseek-flash',
      reasoningEffort: 'high',
    });
    const steer = child.requests('session/steer')[0];
    assert.equal(steer.params.sessionId, prepared.id);
    assert.match(steer.params.contentBlocks[0].text, /earlier question/);
    await acknowledge(child, steer);
    await settle(child, agent);

    const cancelling = agent.prepareSessionRotation();
    const cancelled = await cancelling;
    agent.cancelSessionRotation();
    assert.notEqual(cancelled.id, prepared.id);
    assert.equal(agent.threadId, prepared.id);
    assert.equal(await agent.rotateSession({ resume: true }), prepared.id);
    await agent.stop();

    const offline = newAgent();
    const deferred = await offline.agent.prepareSessionRotation();
    assert.equal(deferred.sessionReady, false);
    assert.equal(deferred.deferred, true);
    assert.equal(offline.agent.commitSessionRotation(deferred, { persist: false }), null);
    assert.equal(offline.agent.forceFreshSession, true);
  });

  it('uses RuntimeAdapter cancellation without changing the process or session', async () => {
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    const adapter = new RuntimeAdapter('dsh', agent);
    const processBefore = agent.child;
    const sessionBefore = agent.threadId;
    agent.deliver('loop', 7);
    await acknowledge(child, child.requests('session/steer')[0]);
    assert.equal(adapter.cancelTurn(), true);
    assert.deepEqual(child.requests('session/cancel')[0].params, { sessionId: sessionBefore, keepInbox: false });
    await settle(child, agent, { kind: 'aborted', reason: { kind: 'user' } });
    assert.equal(agent.child, processBefore);
    assert.equal(agent.threadId, sessionBefore);
    assert.equal(context.successes.length, 1);
    await agent.stop();
  });
});

describe('DshAgent model settings source', () => {
  it('restarts once between turns when the profile patch content changes', async () => {
    const context = newAgent();
    const { agent } = context;
    const first = await startAgent(context);
    const sessionId = agent.threadId;
    writeProfilePatch(context.config, [{ id: 'llm-pi-ai', config: { providers: { deepseek: {} } } }]);
    agent.deliver('after the settings were written', 7);
    await waitFor(() => context.children.length === 2 && agent.connected);
    assert.notEqual(first.exitCode, null);
    const second = context.children[1];
    assert.equal(second.requests('session/resume')[0].params.sessionId, sessionId);
    // The restart reapplies the selection on the resumed session, then steers.
    assert.equal(second.requests('vika/session/model/select').length, 1);
    await waitFor(() => second.requests('session/steer').length === 1);
    await acknowledge(second, second.requests('session/steer')[0]);
    await settle(second, agent);
    await agent.stop();
  });

  it('counts a settings write that lands during the handshake as a change', async () => {
    const context = newAgent();
    const { agent } = context;
    await startAgent(context);
    const started = [{ id: 'llm-pi-ai', config: { providers: { deepseek: {} } } }];
    const written = [{ id: 'llm-pi-ai', config: { providers: { deepseek: {}, kimi: {} } } }];
    writeProfilePatch(context.config, started);
    const startedHash = dshProfilePatchHash({ dataDir: context.config.dataDir });
    // DSH reads this document in its own start-up. Holding the bridge hello lets
    // the test write a second change after the child that booted from `started`
    // has read it -- the window a coincident `ov-superagent dsh model` command lands in.
    let releaseBoot = null;
    const spawn = agent._spawn;
    agent._spawn = (bin, args, options) => {
      const child = spawn(bin, args, options);
      const answer = child._answer.bind(child);
      child._answer = (frame) => {
        if (frame.method !== 'vika/hello' || releaseBoot) {
          answer(frame);
          return;
        }
        writeProfilePatch(context.config, written);
        releaseBoot = () => answer(frame);
      };
      return child;
    };
    const restarting = agent.restartWhenIdle('model-settings-changed');
    await waitFor(() => typeof releaseBoot === 'function', 2_000);
    releaseBoot();
    assert.equal(await restarting, true);
    assert.equal(context.children.length, 2);
    // The baseline is the document this child was started from, not a re-read
    // after the handshake: the write that landed in that window is unread.
    assert.equal(agent.profilePatchHash, startedHash);
    assert.equal(agent._profilePatchChanged(), true);
    // And it converges instead of being swallowed: the next boundary restarts
    // once more, this time from the newer document.
    agent.deliver('after the second write', 7);
    await waitFor(() => context.children.length === 3 && agent.connected
      && !agent.restarting && !agent.settingsUpdate, 2_000);
    assert.equal(agent._profilePatchChanged(), false);
    const third = context.children[2];
    await waitFor(() => third.requests('session/steer').length === 1);
    await acknowledge(third, third.requests('session/steer')[0]);
    await settle(third, agent);
    await agent.stop();
  });

  it('does not restart when the profile patch is rewritten with the same content', async () => {
    const context = newAgent();
    const value = [{ id: 'llm-pi-ai', config: { providers: { deepseek: {} } } }];
    writeProfilePatch(context.config, value);
    const { agent } = context;
    const child = await startAgent(context);
    assert.ok(agent.profilePatchHash);
    // Same bytes, new mtime: a content hash, not a timestamp, decides.
    writeProfilePatch(context.config, value);
    agent.deliver('same settings', 7);
    await waitFor(() => child.requests('session/steer').length === 1);
    assert.equal(context.children.length, 1);
    assert.equal(child.requests('vika/session/model/select').length, 0);
    await acknowledge(child, child.requests('session/steer')[0]);
    await settle(child, agent);
    await agent.stop();
  });

  it('refreshes a changed profile patch before answering the model menu', async () => {
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    writeProfilePatch(context.config, [{ id: 'llm-pi-ai', config: { providers: { deepseek: {} } } }]);
    const models = await agent.listModels();
    // The menu is served by the process that actually reads the new document.
    assert.equal(context.children.length, 2);
    assert.equal(child.requests('vika/llm/models').length, 0);
    assert.deepEqual(models.map((entry) => entry.id), [
      'deepseek-official/deepseek-flash', 'deepseek-official/deepseek-v4-pro',
    ]);
    await agent.stop();
  });

  it('applies a changed profile patch at the next turn boundary, never mid-turn', async () => {
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    agent.deliver('still running', 7);
    await acknowledge(child, child.requests('session/steer')[0], 'running-first');
    assert.ok(agent.turn);
    writeProfilePatch(context.config, [{ id: 'llm-pi-ai', config: { providers: { deepseek: {} } } }]);
    // Mid-turn input is still steered into the live turn: a settings document
    // never interrupts work already running.
    agent.deliver('same turn', 8);
    await waitFor(() => child.requests('session/steer').length === 2);
    assert.equal(context.children.length, 1);
    await acknowledge(child, child.requests('session/steer')[1], 'running-second');
    await settle(child, agent);
    // The turn ended; the change is still pending because no turn is starting.
    assert.equal(context.children.length, 1);
    agent.deliver('next turn', 9);
    await waitFor(() => context.children.length === 2 && agent.connected, 2_000);
    assert.notEqual(child.exitCode, null);
    const second = context.children[1];
    await waitFor(() => second.requests('session/steer').length === 1);
    assert.match(second.requests('session/steer')[0].params.contentBlocks[0].text, /next turn/);
    await acknowledge(second, second.requests('session/steer')[0]);
    await settle(second, agent);
    await agent.stop();
  });

  it('keeps the queued batch when a settings restart fails and lets reconnect retry it', async () => {
    // B2: the restart is the answer to a settings change, and the batch behind it
    // is what the user just said. One transient handshake failure must not fail
    // it: the reconnect loop owns the retry budget (three consecutive failures)
    // and this restart is only its first attempt.
    const context = newAgent({ childOptions: (index) => (index === 1
      ? { hello: new Error('restart handshake unavailable') }
      : {}) });
    const { agent } = context;
    await startAgent(context);
    writeProfilePatch(context.config, [{ id: 'llm-pi-ai', config: { providers: { deepseek: {} } } }]);
    agent.deliver('survives the restart', 7);
    // The first replacement fails its handshake; the second one is the process
    // the batch is finally delivered to.
    await waitFor(() => context.children.length === 3 && agent.connected, 2_000);
    assert.equal(context.failures.length, 0, 'the queued batch is not failed by one failed restart');
    assert.ok(context.warnings.some((entry) => entry.event === 'dsh_restart_for_settings_failed'));
    const third = context.children[2];
    await waitFor(() => third.requests('session/steer').length === 1, 2_000);
    assert.match(third.requests('session/steer')[0].params.contentBlocks[0].text, /survives the restart/);
    await acknowledge(third, third.requests('session/steer')[0]);
    await settle(third, agent);
    assert.deepEqual(context.successes, [{ inboundSeqs: [7] }]);
    await agent.stop();
  });

  it('fails the queued batch only when the reconnect budget after a restart is spent', async () => {
    // The other half of B2: the batch is not failed by the restart, but the
    // reconnect loop still owns the terminal decision once its own limit is hit.
    const context = newAgent({
      options: { reconnectMaxAttempts: 2 },
      childOptions: (index) => (index === 0 ? {} : { hello: new Error('restart handshake unavailable') }),
    });
    const { agent } = context;
    await startAgent(context);
    writeProfilePatch(context.config, [{ id: 'llm-pi-ai', config: { providers: { deepseek: {} } } }]);
    agent.deliver('cannot be delivered', 7);
    await waitFor(() => context.failures.length === 1, 3_000);
    assert.match(context.failures[0].error, /reconnect attempts exhausted/);
    assert.equal(agent.autoReconnect, false);
    assert.equal(agent.queue.length, 0);
    await agent.stop();
  });

  it('leaves a credential change to DSH instead of restarting the process', async () => {
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    const credentials = path.join(context.config.dataDir, 'dsh', 'home', '.credentials.yaml');
    fs.mkdirSync(path.dirname(credentials), { recursive: true });
    fs.writeFileSync(credentials, 'DEEPSEEK_API_KEY: secret\n');
    agent.deliver('after a key was added', 7);
    await waitFor(() => child.requests('session/steer').length === 1);
    // DSH reloads credentials on its own: the process and session stay put.
    assert.equal(context.children.length, 1);
    await acknowledge(child, child.requests('session/steer')[0]);
    await settle(child, agent);
    await agent.stop();
  });

  it('a stop during the version probe leaves no child behind and publishes no session', async () => {
    // The probe is a separate `node --import tsx` launch, so it is seconds wide on
    // a loaded box. A stop landing in that window used to find `this.child` still
    // null, terminate nothing, and let the handshake spawn a child afterwards --
    // one that no generation owned, holding DSH_HOME while the next start ran.
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const context = newAgent({ options: { versionProbe: async () => { await gate; return '0.2.2-rc.1'; } } });
    const { agent } = context;
    const started = agent.start();
    await agent.stop();
    release();
    await assert.rejects(started, /stopped during startup/);
    assert.equal(agent.child, null, 'no child was adopted by the stopped generation');
    assert.equal(agent.connected, false);
    assert.equal(agent.sessionReady, false, 'no session was published');
    assert.equal(context.children.length, 0, 'nothing was spawned after the stop');
  });
});

describe('DshAgent start bound', () => {
  it('comes from config, and an explicit option still wins', () => {
  // A start that exceeds the bound fails the daemon, so the bound is adjustable
  // through config; an injected option is what the tests use and must win.
  const logger = { warn() {}, info() {}, error() {}, child() { return logger; } };
  const make = (config, options) => new DshAgent(
    { dataDir: '/tmp/dsh-start-bound', workspace: '/tmp', ...config }, {}, logger, options,
  ).startTimeoutMs;
  assert.equal(make({}, {}), 30_000);
  assert.equal(make({ dshStartTimeoutMs: 120_000 }, {}), 120_000);
  assert.equal(make({ dshStartTimeoutMs: 120_000 }, { startTimeoutMs: 5_000 }), 5_000);
  assert.equal(make({ dshStartTimeoutMs: 0 }, {}), 30_000, 'a nonsensical value falls back');
  assert.equal(make({ dshStartTimeoutMs: 'nope' }, {}), 30_000);
});
})
