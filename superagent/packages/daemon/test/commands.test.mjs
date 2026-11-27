// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { chatEvent } from './event-fixture.mjs';
import { describe, it } from 'node:test';
import {
  EFFORT_LEVELS,
  commandCandidate,
  executeCommand,
  matchPrefix,
  parseCommand,
} from '../src/commands.mjs';
import { RUNTIME_IDS } from '../src/runtime-catalog.mjs';

function fakeCodex(overrides = {}) {
  const calls = [];
  return {
    calls,
    runtime: 'codex',
    threadId: 'th-live',
    model: '',
    effort: 'medium',
    async listModels() { return [{ id: this.model || 'gpt-default', isDefault: true, efforts: EFFORT_LEVELS }]; },
    state: { connected: true, activeTurn: 0, queued: 0 },
    turnInProgress: false,
    async interruptTurn() { calls.push(['interrupt']); },
    async rotateSession(options) { calls.push(['rotate', options]); return 'th-new'; },
    async setModel(model) { calls.push(['setModel', model]); return 'th-new'; },
    async setEffort(effort) { calls.push(['setEffort', effort]); return 'th-new'; },
    async snapshot({ catalog = 'cached' } = {}) {
      const models = catalog === 'refresh' ? await this.listModels?.() ?? [] : [];
      return {
        runtime: this.runtime,
        session: { id: this.threadId, connected: this.state.connected },
        model: this.model,
        resolvedModel: this.resolvedModel,
        effort: this.effort,
        queued: this.state.queued,
        work: this.workStatus ?? {
          working: this.turnInProgress,
          startedAtMs: null,
          connected: this.state.connected,
          queued: this.state.queued,
          switching: false,
        },
        capabilities: {
          effortControl: this.runtime === 'volc'
            ? 'remote-managed'
            : 'selectable',
        },
        models,
      };
    },
    async transition(request) {
      if (request.kind === 'new-session') {
        await this.interruptTurn();
        return { ok: true, threadId: await this.rotateSession({ resume: false }) };
      }
      if (request.kind === 'select-runtime') {
        calls.push(['runtime', request.runtime]);
        const unchanged = this.runtime === request.runtime;
        this.runtime = request.runtime;
        return { ok: true, runtime: this.runtime, unchanged };
      }
      if (request.kind === 'select-model') {
        const result = await this.setModel(request.selector);
        return result && typeof result === 'object'
          ? { ok: true, ...result }
          : { ok: true, runtimeChanged: false, model: request.selector, threadId: result };
      }
      if (request.kind === 'select-effort') {
        const unchanged = this.effort === request.effort;
        if (!unchanged) await this.setEffort(request.effort);
        this.effort = request.effort;
        return {
          ok: true,
          effort: request.effort,
          unchanged,
        };
      }
      throw new Error(`unexpected transition ${request.kind}`);
    },
    ...overrides,
  };
}

describe('parseCommand', () => {
  it('returns null for anything that does not start with a slash', () => {
    assert.equal(parseCommand('hello'), null);
    assert.equal(parseCommand('说说 /new 是什么'), null);
    assert.equal(parseCommand(''), null);
    assert.equal(parseCommand(null), null);
  });

  it('matches exact names and unambiguous prefixes, case-insensitively', () => {
    assert.deepEqual(parseCommand('/new'), { name: 'new', arg: '' });
    assert.deepEqual(parseCommand('  /h  '), { name: 'help', arg: '' });
    assert.deepEqual(parseCommand('/M gpt-5.5'), { name: 'model', arg: 'gpt-5.5' });
    assert.deepEqual(parseCommand('/eff high'), { name: 'effort', arg: 'high' });
    assert.deepEqual(parseCommand('/run claude'), { name: 'runtime', arg: 'claude' });
    assert.deepEqual(parseCommand(' /primary STATUS '), { name: 'primary', arg: 'STATUS' });
    assert.deepEqual(parseCommand('/p'), { name: 'primary', arg: '' });
  });

  it('keeps everything after the command token as one arg', () => {
    assert.deepEqual(parseCommand('/model  a  b '), { name: 'model', arg: 'a  b' });
  });

  it('reports unknown and bare-slash tokens without a name', () => {
    const unknown = parseCommand('/foo bar');
    assert.equal(unknown.name, null);
    assert.equal(unknown.token, 'foo');
    assert.deepEqual(unknown.matches, []);
    assert.equal(parseCommand('/').name, null);
  });
});

describe('commandCandidate', () => {
  it('passes p2p text through unchanged', () => {
    assert.equal(commandCandidate(chatEvent({ chatType: 'p2p', content: ' /new ', mentions: [] })), '/new');
    assert.equal(commandCandidate(chatEvent({ chatType: 'p2p', content: '普通消息', mentions: [] })), '普通消息');
  });

  it('never accepts slash commands in group chats, mentioned or not', () => {
    assert.equal(commandCandidate(chatEvent({ chatType: 'group', content: '/help', mentions: [] })), null);
    assert.equal(commandCandidate(chatEvent({ chatType: 'group', content: '/help' })), null);
    assert.equal(commandCandidate(chatEvent({ chatType: 'group', content: '/primary' })), null);
    const mentions = [{ key: '@_user_1', name: 'Alpha', id: 'ou_bot12345678' }];
    assert.equal(
      commandCandidate(chatEvent({ chatType: 'group', content: '@_user_1 /new', mentions })),
      null,
    );
    assert.equal(
      commandCandidate(chatEvent({ chatType: 'group', content: '/model gpt-5.5 @_user_1', mentions })),
      null,
    );
  });
});

describe('commandCandidate on a card', () => {
  const choice = (value, { option, chatType = 'p2p' } = {}) => ({
    ...chatEvent({ kind: 'message.action', messageType: 'card_action', chatType, messageId: 'ca_choice', parentId: 'om_card123456789' }),
    data: { interaction: { value, ...(option === undefined ? {} : { option }) } },
  });

  it('opens the task picked in the /tasks dropdown', () => {
    assert.equal(commandCandidate(choice({ superagent_command: '/tasks' }, { option: 't12' })), '/tasks t12');
    assert.equal(commandCandidate(choice({ superagent_command: '/tasks' }, { option: 'stop t12' })), null);
    assert.equal(commandCandidate(choice({ superagent_command: '/tasks' })), null);
  });

  it('runs the /tasks a refresh button was drawn by, and nothing else', () => {
    assert.equal(commandCandidate(choice({ superagent_command: '/tasks', arg: '', refresh: true })), '/tasks');
    assert.equal(commandCandidate(choice({ superagent_command: '/tasks', arg: 't3', refresh: true })), '/tasks t3');
    // The button names its argument; an option on it is ignored.
    assert.equal(commandCandidate(choice({ superagent_command: '/tasks', arg: 't3', refresh: true }, { option: 't4' })), '/tasks t3');
    for (const arg of ['stop t3', 't3 x', undefined, null, 3]) {
      assert.equal(commandCandidate(choice({ superagent_command: '/tasks', arg, refresh: true })), null, String(arg));
    }
    assert.equal(commandCandidate(choice({ superagent_command: '/new', arg: '', refresh: true })), null);
    assert.equal(commandCandidate(choice({ superagent_command: '/tasks', arg: '', refresh: true }, { chatType: 'group' })), null);
  });
});

describe('matchPrefix', () => {
  it('lets an exact match win over longer candidates and reports ambiguity', () => {
    assert.deepEqual(matchPrefix('low', ['low', 'lower']), ['low']);
    assert.deepEqual(matchPrefix('m', EFFORT_LEVELS), ['minimal', 'medium']);
    assert.deepEqual(matchPrefix('', EFFORT_LEVELS), []);
  });
});

describe('executeCommand', () => {
  it('dispatches primary commands without reading or changing the runtime', async () => {
    const command = parseCommand('/primary status');
    const outcome = { ok: true, text: '当前主 IM：飞书（lark）' };
    assert.deepEqual(await executeCommand(command, null, { primary: (parsed) => {
      assert.deepEqual(parsed, command);
      return outcome;
    } }), outcome);
    assert.equal((await executeCommand(command, null)).ok, false);
    const help = await executeCommand(parseCommand('/help'), fakeCodex());
    assert.match(help.text, /\/primary status/);
  });

  it('uses the resolved default model for effort instead of the catalog union', async () => {
    const agent = fakeCodex({ model: '', resolvedModel: 'gpt-active', listModels: async () => [
      { id: 'gpt-active', efforts: ['medium', 'high', 'max', 'ultra'] },
      { id: 'gpt-other', isDefault: true, efforts: ['minimal'] },
    ] });
    const listing = await executeCommand({ name: 'effort', arg: '' }, agent);
    assert.match(listing.text, /可选：medium、high、max、ultra/);
    assert.doesNotMatch(listing.text, /minimal|managed/);
    assert.equal((await executeCommand({ name: 'effort', arg: 'ultra' }, agent)).ok, true);
    assert.deepEqual(agent.calls, [['setEffort', 'ultra']]);
  });

  it('does not borrow effort levels when current model capabilities are unknown or empty', async () => {
    for (const models of [[], [{ id: 'gpt-current', efforts: [] }, { id: 'other', efforts: ['high'] }]]) {
      const agent = fakeCodex({ model: 'gpt-current', listModels: async () => models });
      const listing = await executeCommand({ name: 'effort', arg: '' }, agent);
      assert.doesNotMatch(listing.text, /可选：/);
      assert.equal((await executeCommand({ name: 'effort', arg: 'high' }, agent)).ok, false);
      assert.deepEqual(agent.calls, []);
    }
  });

  it('help reports commands and current session state', async () => {
    const codex = fakeCodex({ threadId: 'th-42', model: 'gpt-x', effort: 'high' });
    const outcome = await executeCommand({ name: 'help', arg: '' }, codex);
    assert.equal(outcome.ok, true);
    assert.match(outcome.text, /\/new/);
    assert.match(outcome.text, /th-42/);
    assert.match(outcome.text, /gpt-x/);
    assert.match(outcome.text, /high/);
    assert.match(outcome.text, /\/runtime/);
    assert.doesNotMatch(outcome.text, /claude:|codex:/);
    assert.doesNotMatch(outcome.text, /restart/);
    assert.equal(codex.calls.length, 0);
  });

  it('help reports TraeX effort controls', async () => {
    const agent = fakeCodex({ runtime: 'traex', threadId: 'th-42', model: 'gpt-5.5', effort: 'medium' });
    const outcome = await executeCommand({ name: 'help', arg: '' }, agent);
    assert.equal(outcome.ok, true);
    assert.match(outcome.text, /\/effort/);
    assert.match(outcome.text, /当前模型的推理档位/);
    assert.match(outcome.text, /推理档位：medium/);
  });

  it('new rotates the session without resume', async () => {
    const codex = fakeCodex();
    const outcome = await executeCommand({ name: 'new', arg: '' }, codex);
    assert.equal(outcome.ok, true);
    assert.match(outcome.text, /th-new/);
    assert.deepEqual(codex.calls, [['interrupt'], ['rotate', { resume: false }]]);
  });

  it('treats the removed /restart command as unknown', () => {
    assert.deepEqual(parseCommand('/restart'), {
      name: null,
      arg: '',
      token: 'restart',
      matches: [],
    });
  });

  it('new while disconnected still succeeds with a deferred message', async () => {
    const codex = fakeCodex({ rotateSession: async () => null });
    const outcome = await executeCommand({ name: 'new', arg: '' }, codex);
    assert.equal(outcome.ok, true);
    assert.match(outcome.text, /重连/);
  });

  it('status reports active duration without consulting subagents', async () => {
    const codex = fakeCodex({
      runtime: 'codex',
      model: 'gpt-5.6-sol',
      workStatus: {
        working: true,
        startedAtMs: Date.now() - 125_000,
        connected: true,
        queued: 2,
        switching: false,
      },
    });
    const outcome = await executeCommand({ name: 'status', arg: '' }, codex);
    assert.equal(outcome.ok, true);
    assert.match(outcome.text, /模型工作中，已持续 2 分/);
    assert.match(outcome.text, /排队消息：2/);
    assert.equal(codex.calls.length, 0);
  });

  it('model without an arg reports the current model and the available options', async () => {
    const codex = fakeCodex({
      model: '',
      listModels: async () => [
        { id: 'gpt-5.5', efforts: ['low', 'medium'] },
        { id: 'gpt-5.5-codex', efforts: [] },
      ],
    });
    const outcome = await executeCommand({ name: 'model', arg: '' }, codex);
    assert.equal(outcome.ok, true);
    assert.match(outcome.text, /（默认）/);
    assert.match(outcome.text, /可选：gpt-5\.5、gpt-5\.5-codex/);
    assert.equal(codex.calls.length, 0);
  });

  it('names a runtime with no model chosen as its default, never as the runtime id', async () => {
    // Pi reports its canonical default ("pi"), which is the manifest's runtime
    // selector. Nobody chose it and /model cannot set it; what the session runs
    // on is the fact worth naming.
    const pi = fakeCodex({
      runtime: 'pi',
      model: 'pi',
      resolvedModel: 'ollama/deepseek-v4.1-flash:1017',
      listModels: async () => [{ id: 'ollama/deepseek-v4.1-flash:1017', efforts: ['high'] }],
    });

    const help = await executeCommand({ name: 'help', arg: '' }, pi);
    assert.match(help.text, /模型：默认（ollama\/deepseek-v4\.1-flash:1017）/);
    assert.doesNotMatch(help.text, /模型：pi/);

    const status = await executeCommand({ name: 'status', arg: '' }, pi);
    assert.match(status.text, /模型：默认（ollama\/deepseek-v4\.1-flash:1017）/);

    const model = await executeCommand({ name: 'model', arg: '' }, pi);
    assert.match(model.text, /当前模型：默认（ollama\/deepseek-v4\.1-flash:1017）/);
    // Already named by the default, so it is not repeated as 实际模型.
    assert.doesNotMatch(model.text, /实际模型/);
    assert.doesNotMatch(model.text, /当前模型：pi/);
  });

  it('falls back to 默认 when a runtime default selector has no session model to name', async () => {
    const pi = fakeCodex({ runtime: 'pi', model: 'pi', listModels: async () => [] });
    const help = await executeCommand({ name: 'help', arg: '' }, pi);
    assert.match(help.text, /模型：默认\n/);
    const model = await executeCommand({ name: 'model', arg: '' }, pi);
    assert.match(model.text, /当前模型：默认\n/);
  });

  it('model resolves prefixes against the live list and rejects unknown or ambiguous names', async () => {
    const codex = fakeCodex({
      listModels: async () => [
        { id: 'gpt-5.5', efforts: [] },
        { id: 'gpt-5.5-codex', efforts: [] },
      ],
    });

    const prefixed = await executeCommand({ name: 'model', arg: 'gpt-5.5-c' }, codex);
    assert.equal(prefixed.ok, true);
    assert.deepEqual(codex.calls, [['setModel', 'gpt-5.5-codex']]);

    const exact = await executeCommand({ name: 'model', arg: 'gpt-5.5' }, codex);
    assert.equal(exact.ok, true);
    assert.deepEqual(codex.calls.at(-1), ['setModel', 'gpt-5.5']);

    const ambiguous = await executeCommand({ name: 'model', arg: 'gpt' }, codex);
    assert.equal(ambiguous.ok, false);
    assert.match(ambiguous.text, /歧义/);

    const unknown = await executeCommand({ name: 'model', arg: 'claude-x' }, codex);
    assert.equal(unknown.ok, false);
    assert.match(unknown.text, /未找到模型/);
    assert.equal(codex.calls.length, 2);
  });

  it('model accepts a raw name when no list is available and rejects garbage', async () => {
    const codex = fakeCodex({ listModels: async () => [] });
    const outcome = await executeCommand({ name: 'model', arg: 'gpt-5.5-codex' }, codex);
    assert.equal(outcome.ok, true);
    assert.deepEqual(codex.calls, [['setModel', 'gpt-5.5-codex']]);

    const bad = await executeCommand({ name: 'model', arg: 'rm -rf /' }, codex);
    assert.equal(bad.ok, false);
    assert.equal(codex.calls.length, 1);
  });

  it('rejects cross-runtime model selectors without switching', async () => {
    const agent = fakeCodex({ runtime: 'claude', model: 'sonnet',
      listModels: async () => [{ id: 'sonnet', efforts: ['medium'] }] });
    for (const arg of ['gpt-5.6-sol', 'codex:gpt-5.6-sol', 'pi', 'ark', 'traex']) {
      const result = await executeCommand({ name: 'model', arg }, agent);
      assert.equal(result.ok, false);
      assert.match(result.text, /\/runtime/);
    }
    assert.deepEqual(agent.calls, []);
  });

  it('accepts a same-runtime qualifier and preserves the session', async () => {
    const agent = fakeCodex({ runtime: 'claude', model: 'sonnet',
      listModels: async () => [{ id: 'sonnet' }, { id: 'opus' }] });
    const result = await executeCommand({ name: 'model', arg: 'claude:op' }, agent);
    assert.equal(result.ok, true);
    assert.deepEqual(agent.calls, [['setModel', 'opus']]);
    assert.match(result.text, /下一轮生效，上下文保留/);
    assert.doesNotMatch(result.text, /新会话/);
    assert.equal((await executeCommand({ name: 'model', arg: 'claude:' }, agent)).ok, false);
  });

  it('shows Claude versions and accepts resolved model ids', async () => {
    const agent = fakeCodex({ runtime: 'claude', model: 'sonnet',
      listModels: async () => [{ id: 'sonnet', displayName: 'Sonnet 5.5', resolvedModel: 'claude-sonnet-5-5' }] });
    const listing = await executeCommand({ name: 'model', arg: '' }, agent);
    assert.match(listing.text, /Sonnet 5.5/);
    assert.match(listing.text, /实际模型：claude-sonnet-5-5/);
    const result = await executeCommand({ name: 'model', arg: 'claude-sonnet-5-5' }, agent);
    assert.equal(result.ok, true);
    assert.deepEqual(agent.calls, [['setModel', 'claude-sonnet-5-5']]);
  });

  it('lists runtimes without consulting the model backend', async () => {
    const agent = fakeCodex({ listModels() { assert.fail('runtime listing is local'); } });
    const result = await executeCommand({ name: 'runtime', arg: '' }, agent);
    assert.match(result.text, /当前 Runtime：codex/);
    assert.match(result.text, /claude/);
    assert.match(result.text, /ark/);
    assert.deepEqual(agent.calls, []);
  });

  it('lists the runtimes this machine can start, not the whole catalogue', async () => {
    const agent = fakeCodex();
    const availability = Object.fromEntries(RUNTIME_IDS.map((id) => [id, { available: ['pi', 'durable', 'dsh'].includes(id), reason: 'command' }]));
    const result = await executeCommand({ name: 'runtime', arg: '' }, agent, { availability });

    assert.match(result.text, /^当前 Runtime：codex\n可选：pi、durable、dsh\n/);
    assert.doesNotMatch(result.text, /traex|claude|ark|opencode/);
  });

  it('selects runtime aliases and treats the current runtime as a no-op', async () => {
    const agent = fakeCodex();
    const switched = await executeCommand({ name: 'runtime', arg: 'ark' }, agent);
    assert.equal(switched.ok, true);
    assert.deepEqual(agent.calls, [['runtime', 'volc']]);
    const repeated = await executeCommand({ name: 'runtime', arg: 'volcengine' }, agent);
    assert.match(repeated.text, /当前已是 Runtime：volc（会话保留）/);
    assert.equal((await executeCommand({ name: 'runtime', arg: 'unknown' }, agent)).ok, false);
    assert.equal(agent.calls.length, 2);
  });

  it('rejects a stale model transition instead of reporting success', async () => {
    const agent = fakeCodex({ transition: async () => ({ ok: false, reason: 'stale-snapshot' }) });
    const result = await executeCommand({ name: 'model', arg: 'gpt-default' }, agent);
    assert.equal(result.ok, false);
    assert.match(result.text, /重试/);
  });

  it('keeps provider-qualified Pi model ids inside Pi', async () => {
    const agent = fakeCodex({ runtime: 'pi', model: 'google/gemini-pro',
      listModels: async () => [{ id: 'openai/gpt-4o' }] });
    for (const arg of ['pi:openai/gpt-4o', 'openai/gpt-4o']) {
      const result = await executeCommand({ name: 'model', arg }, agent);
      assert.equal(result.ok, true);
      assert.match(result.text, /上下文保留/);
      assert.deepEqual(agent.calls.at(-1), ['setModel', 'openai/gpt-4o']);
    }
  });

  it('explains that Ark effort is managed remotely', async () => {
    const agent = fakeCodex({ runtime: 'volc', model: 'ark', effort: 'managed' });
    const { calls } = agent;
    const result = await executeCommand({ name: 'effort', arg: 'high' }, agent);
    assert.equal(result.ok, false);
    assert.match(result.text, /远端 Agent/);
    assert.equal(calls.some(([name]) => name === 'setEffort'), false);
  });

  it('switches TraeX effort while preserving the session', async () => {
    const agent = fakeCodex({
      runtime: 'traex',
      model: 'gpt-5.5',
      effort: 'medium',
      listModels: async () => [{ id: 'gpt-5.5', efforts: ['low', 'medium', 'high'] }],
    });
    const result = await executeCommand({ name: 'effort', arg: 'high' }, agent);
    assert.equal(result.ok, true);
    assert.equal(result.text, '已切换推理档位：high（下一轮生效，上下文保留）');
    assert.deepEqual(agent.calls, [['setEffort', 'high']]);
    const repeated = await executeCommand({ name: 'effort', arg: 'high' }, agent);
    assert.equal(repeated.text, '当前已是推理档位：high（上下文保留）');
    assert.equal(agent.calls.length, 1);
  });

  it('effort prefix-matches a level and rejects ambiguous or unknown input', async () => {
    const codex = fakeCodex();
    const high = await executeCommand({ name: 'effort', arg: 'h' }, codex);
    assert.equal(high.ok, true);
    assert.match(high.text, /下一轮生效，上下文保留/);
    assert.deepEqual(codex.calls, [
      ['setEffort', 'high'],
    ]);

    const ambiguous = await executeCommand({ name: 'effort', arg: 'm' }, codex);
    assert.equal(ambiguous.ok, false);
    assert.match(ambiguous.text, /minimal、medium/);

    const unknown = await executeCommand({ name: 'effort', arg: 'turbo' }, codex);
    assert.equal(unknown.ok, false);
    assert.equal(codex.calls.length, 1);
  });

  it('saves effort while disconnected without announcing a cleared session', async () => {
    const agent = fakeCodex({ state: { connected: false, activeTurn: 0, queued: 0 } });
    const result = await executeCommand({ name: 'effort', arg: 'high' }, agent);
    assert.equal(result.text, '已切换推理档位：high（下一轮生效，上下文保留）');
    assert.deepEqual(agent.calls, [['setEffort', 'high']]);
  });

  it('does not report success when the runtime rejects an effort', async () => {
    const agent = fakeCodex({ transition: async () => ({ ok: false, reason: 'unsupported-effort' }) });
    const result = await executeCommand({ name: 'effort', arg: 'high' }, agent);
    assert.equal(result.ok, false);
    assert.equal(result.text, '不支持的档位：high');
  });

  it('effort validates against the current model and rejects unsupported levels', async () => {
    const codex = fakeCodex({
      model: 'gpt-5.6-sol',
      listModels: async () => [
        { id: 'gpt-5.5', efforts: ['minimal', 'low', 'medium', 'high', 'xhigh'] },
        { id: 'gpt-5.6-sol', efforts: ['none', 'low', 'medium', 'high', 'xhigh'] },
      ],
    });

    const unsupported = await executeCommand({ name: 'effort', arg: 'minimal' }, codex);
    assert.equal(unsupported.ok, false);
    assert.match(unsupported.text, /不支持的档位：minimal/);
    assert.match(unsupported.text, /none、low、medium、high、xhigh/);
    assert.equal(codex.calls.length, 0);

    const none = await executeCommand({ name: 'effort', arg: 'n' }, codex);
    assert.equal(none.ok, true);
    assert.deepEqual(codex.calls, [['setEffort', 'none']]);

    const listing = await executeCommand({ name: 'effort', arg: '' }, codex);
    assert.match(listing.text, /可选：none、low、medium、high、xhigh/);
  });

  it('unknown commands answer with help instead of reaching codex', async () => {
    const codex = fakeCodex();
    const outcome = await executeCommand({ name: null, arg: '', token: 'foo', matches: [] }, codex);
    assert.equal(outcome.ok, false);
    assert.match(outcome.text, /未知命令：\/foo/);
    assert.match(outcome.text, /可用命令/);
    assert.equal(codex.calls.length, 0);
  });
});
