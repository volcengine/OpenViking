// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Layer: cli — the /model command boundary: which argument shapes reach the
// selection, and what the command answers when the catalogue and the shared
// grammar disagree about an id. Asserts the dispatcher's reply, not the
// transition's internals.

import assert from 'node:assert/strict';
import { describe, it } from './dsh-node-gate.mjs';
import { executeCommand } from '../src/commands.mjs';
import {
  DEFAULT_MODELS, defaultModelInfo, selectThroughSlashCommand, startAgent, newAgent, waitFor,
} from './dsh-model-selection-fixture.mjs';

describe('the /model command', () => {
  // The command layer validated the typed argument with a third, narrower
  // character set than the runtime's own: a model id containing `@` was refused
  // here with "无效的模型名" and never reached the transition at all, so the
  // daemon-side rule was unreachable from the only path a user has.
  it('passes a model id containing @ through to the selection', async (t) => {
    const models = [
      {
        provider: 'deepseek', name: 'DeepSeek', error: null, credential: null,
        models: [{ id: 'deepseek-chat@v2', name: 'DeepSeek Chat v2', inputModalities: ['text'] }],
      },
      ...DEFAULT_MODELS,
    ];
    const context = newAgent({
      childOptions: {
        models,
        modelInfo: (provider, model) => ({
          provider, id: model, name: model, inputModalities: ['text'],
          reasoning: { efforts: [{ id: 'low' }, { id: 'high' }], defaultEffort: 'high' },
        }),
      },
    });
    t.after(async () => { await context.agent.stop(); });
    const child = await startAgent(context);

    // Record every transition the command asks for, so a refusal at the command
    // layer is visible as "no transition was ever requested".
    const requested = [];
    const transition = context.runtime.transition.bind(context.runtime);
    context.runtime.transition = (request) => {
      requested.push(request);
      return transition(request);
    };

    const reply = await selectThroughSlashCommand(context, 'deepseek-chat@v2');
    // Before the fix this was {"ok":false,"text":"无效的模型名：deepseek-chat@v2"}
    // and `requested` stayed empty.
    assert.deepEqual(requested.map((request) => request.selector), ['deepseek-chat@v2'], reply.text);
    assert.equal(reply.ok, true, reply.text);
    assert.match(reply.text, /已切到 deepseek\/deepseek-chat@v2/);

    // And the daemon really asks DSH for it at the next boundary.
    context.agent.deliver('after the switch', 7);
    await waitFor(() => child.requests('superagent/session/model/select').length === 1,
      'the selection to reach DSH');
    assert.deepEqual(child.requests('superagent/session/model/select')[0].params, {
      sessionId: context.agent.threadId,
      provider: 'deepseek',
      model: 'deepseek-chat@v2',
      reasoningEffort: 'high',
    });
  });

  it('still refuses a model id the runtime rule rejects', async () => {
    // The command layer keeps refusing what the runtime rule refuses: widening
    // the accepted set must not turn into accepting anything at all. This one
    // never reaches a transition, so a stub runtime is enough.
    const requested = [];
    const runtime = {
      snapshot: async () => ({
        revision: 1, runtime: 'dsh', model: 'deepseek-official/deepseek-flash',
        effort: 'high', capabilities: {}, session: { id: 's', connected: true },
        models: [{ id: 'deepseek-official/deepseek-flash', resolvedModel: 'deepseek-flash' }],
      }),
      transition: async (request) => { requested.push(request); return { ok: true }; },
    };
    // A control character is what the shared rule refuses now; a space is a
    // legal id for a real dsh and must reach the transition.
    const reply = await executeCommand({ name: 'model', arg: 'group/smart\u0001split' }, runtime);
    assert.equal(reply.ok, false);
    assert.match(reply.text, /无效的模型名/);
    assert.deepEqual(requested, [], 'a rejected id must not reach the transition');
  });
});

describe('an id the catalogue reports but the grammar refuses', () => {
  // A DSH route may carry characters OpenViking SuperAgent's /model does not accept. The id stays
  // valid configuration -- nothing rewrites it -- and the command says which of
  // the two situations it is instead of calling a real route a typo.
  function runtimeWith(models) {
    return {
      snapshot: async () => ({
        revision: 1, runtime: 'dsh', model: 'deepseek-official/deepseek-flash',
        effort: 'high', capabilities: {}, session: { id: 's', connected: true }, models,
      }),
      transition: async (request) => ({ ok: true, applied: request.selector }),
    };
  }

  it('names the limitation instead of reporting an invalid name', async () => {
    const runtime = runtimeWith([{ id: 'group/a b', resolvedModel: 'a b' }]);
    const reply = await executeCommand({ name: 'model', arg: 'group/a b' }, runtime);
    assert.equal(reply.ok, false);
    assert.match(reply.text, /目录里/, 'says the id exists in the catalogue');
    assert.match(reply.text, /不会被改动/, 'promises the configuration is untouched');
    assert.doesNotMatch(reply.text, /无效的模型名/, 'not reported as a typo');
  });

  it('still calls an id the catalogue does not list invalid', async () => {
    const runtime = runtimeWith([{ id: 'group/a b', resolvedModel: 'a b' }]);
    const reply = await executeCommand({ name: 'model', arg: 'typo here' }, runtime);
    assert.equal(reply.ok, false);
    assert.match(reply.text, /无效的模型名/);
  });

  it('accepts a catalogue id that only needed "+"', async () => {
    const runtime = runtimeWith([{ id: 'deepseek/m+v1', resolvedModel: 'm+v1' }]);
    const reply = await executeCommand({ name: 'model', arg: 'deepseek/m+v1' }, runtime);
    assert.equal(reply.ok, true, reply.text);
  });
});
