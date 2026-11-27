// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { WorkerSettings, WORKER_RUNTIME_COOLDOWN_MS, classifyWorkerFailure } from '../src/worker-settings.mjs';
import { executeWorkersCommand } from '../src/worker-commands.mjs';

function memoryStore() {
  const map = new Map();
  return { map, getSetting: (key) => map.get(`setting:${key}`) ?? null, setSetting: (key, value) => map.set(`setting:${key}`, String(value)) };
}

function settingsWith({ router = { runtime: 'codex', model: 'gpt-5.6-sol', effort: 'medium' }, config = {}, clock = { now: 1_000_000 } } = {}) {
  const store = memoryStore();
  const state = { router };
  const settings = new WorkerSettings({ store, config, router: () => state.router, now: () => clock.now });
  return { settings, store, state, clock };
}

describe('classifyWorkerFailure', () => {
  it('tells a used-up quota from a passing failure and from one another runtime would share', () => {
    for (const message of [
      "You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro) or try again at 3:05 PM.",
      'Claude AI usage limit reached|1763600400',
      "5-hour limit reached ∙ resets 3pm",
      "You've hit your weekly limit · resets Nov 21",
      'Error: 429 You exceeded your current quota, please check your plan and billing details. (insufficient_quota)',
      'Your credit balance is too low to access the Anthropic API.',
      'AccountOverdueError: The request failed because your account has an overdue balance.',
      'exit 1: 402 Insufficient Balance',
      '模型调用失败：账户余额不足',
      '本月额度已用尽',
    ]) {
      assert.deepEqual(classifyWorkerFailure(message), { exhausted: true, recoverable: true }, message);
    }
    for (const message of ['429 Too Many Requests', 'upstream overloaded, retry later', 'exit 1: ECONNRESET', '503 Service Unavailable']) {
      assert.deepEqual(classifyWorkerFailure(message), { exhausted: false, recoverable: true }, message);
    }
    for (const message of ['stopped', '', 'pi turn aborted', '401 Unauthorized: invalid api key', 'exit 1: SyntaxError: unexpected token']) {
      assert.deepEqual(classifyWorkerFailure(message), { exhausted: false, recoverable: false }, message);
    }
  });
});

describe('WorkerSettings', () => {
  it('follows the router until set, and takes the router\'s model only on the router\'s runtime', () => {
    const { settings, state } = settingsWith();
    assert.equal(settings.configuredRuntime(), null);
    assert.equal(settings.preferred(), 'codex');
    assert.deepEqual(settings.modelFor('codex'), { model: 'gpt-5.6-sol', effort: 'medium', modelSource: 'router', effortSource: 'router' });
    assert.deepEqual(settings.modelFor('pi'), { model: null, effort: null, modelSource: 'default', effortSource: 'default' });
    settings.setRuntime('pi');
    settings.setModel('pi', 'volcengine-ark/ep-1');
    settings.setEffort('pi', 'high');
    assert.equal(settings.preferred(), 'pi');
    assert.deepEqual(settings.modelFor('pi'), { model: 'volcengine-ark/ep-1', effort: 'high', modelSource: 'set', effortSource: 'set' });
    // The router moving elsewhere leaves workers where they were set.
    state.router = { runtime: 'dsh', model: 'deepseek/x', effort: 'high' };
    assert.equal(settings.preferred(), 'pi');
    settings.setRuntime(null);
    assert.equal(settings.preferred(), 'dsh');
    // A runtimes' bare alias as model is no model (the router's "codex").
    state.router = { runtime: 'codex', model: 'codex', effort: '' };
    assert.equal(settings.modelFor('codex').model, null);
    assert.throws(() => settings.setRuntime('dsh'), /workers do not run on dsh/);
  });

  it('keeps the router\'s effort out of a worker set to another model on the same runtime', () => {
    const { settings } = settingsWith({ router: { runtime: 'codex', model: 'gpt-6-astra', effort: 'high' } });
    // An explicit model means an explicit choice: the router's effort was
    // chosen for another model, so it is not inherited.
    settings.setModel('codex', 'gpt-5.6-sol');
    assert.deepEqual(settings.modelFor('codex'), { model: 'gpt-5.6-sol', effort: null, modelSource: 'set', effortSource: 'default' });
    settings.setEffort('codex', 'medium');
    assert.equal(settings.modelFor('codex').effort, 'medium');
    // Back on the router's model, an unset effort follows the router again.
    settings.setModel('codex', 'gpt-6-astra');
    settings.setEffort('codex', '');
    assert.deepEqual(settings.modelFor('codex'), { model: 'gpt-6-astra', effort: 'high', modelSource: 'set', effortSource: 'router' });
  });

  it('offers the effort levels of the worker\'s model, and the runtime union only when the model reports none', () => {
    const { settings } = settingsWith();
    const models = [
      { id: 'gpt-6-astra', efforts: ['low', 'high', 'max'], defaultEffort: 'high' },
      { id: 'gpt-6-luna', efforts: ['low', 'medium'], isDefault: true },
      { id: 'gpt-5.6-sol', efforts: [], effortsKnown: false },
    ];
    assert.deepEqual(settings.efforts('codex', 'gpt-6-astra', models), ['low', 'high', 'max']);
    assert.deepEqual(settings.efforts('codex', 'gpt-6-luna', models), ['low', 'medium']);
    assert.deepEqual(settings.efforts('codex', 'gpt-5.6-sol', models), ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'], 'an unreported catalogue keeps the union');
    assert.deepEqual(settings.efforts('codex', 'unknown', models), ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'], 'an unknown model keeps the union');
    assert.deepEqual(settings.efforts('codex', null, models), ['low', 'medium'], 'no model takes the default entry');
    assert.deepEqual(settings.efforts('codex'), ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'], 'without a catalogue it is the union');
  });

  it('sends new work and moving tasks past a runtime whose quota ran out, and back once the pause is over', () => {
    const { settings, clock } = settingsWith({ config: { fallbackRuntime: 'pi' } });
    assert.equal(settings.fallback(), 'pi');
    assert.equal(settings.fallbackSource(), 'config');
    assert.equal(settings.forNewWork(), 'codex');
    assert.equal(settings.switchTarget('codex'), 'pi');
    settings.markUnavailable('codex', 'usage limit');
    assert.deepEqual(settings.unavailable('codex'), { until: clock.now + WORKER_RUNTIME_COOLDOWN_MS, reason: 'usage limit' });
    assert.equal(settings.forNewWork(), 'pi');
    // A task on pi that must move has nowhere to go: codex is out.
    assert.equal(settings.switchTarget('pi'), null);
    clock.now += WORKER_RUNTIME_COOLDOWN_MS + 1;
    assert.equal(settings.unavailable('codex'), null);
    assert.equal(settings.forNewWork(), 'codex');
    // The preferred runtime comes first: workers moved to claude by hand.
    settings.setRuntime('claude');
    assert.equal(settings.switchTarget('codex'), 'claude');
    assert.equal(settings.switchTarget('claude'), 'pi');
    // Choosing a runtime by hand clears its pause.
    settings.markUnavailable('claude', 'quota');
    settings.setRuntime('claude');
    assert.equal(settings.unavailable('claude'), null);
    // The fallback: the router's backup where workers run, set, or off.
    assert.equal(settingsWith({ config: { fallbackRuntime: 'dsh' } }).settings.fallback(), null);
    settings.setFallback('traex');
    assert.deepEqual([settings.fallback(), settings.fallbackSource(), settings.switchTarget('claude')], ['traex', 'set', 'traex']);
    settings.setFallback('off');
    assert.deepEqual([settings.fallback(), settings.fallbackSource(), settings.switchTarget('claude')], [null, 'off', null]);
  });
});

describe('/workers', () => {
  const OWNER_DM = { chatType: 'p2p', senderType: 'user', senderId: 'ou_owner' };
  const connectors = { isOwner: (event) => event.senderId === 'ou_owner' };
  const tasks = { overview: () => ({ live: [{ runtime: 'codex' }, { runtime: 'codex' }, { runtime: 'pi' }] }) };
  const router = { runtime: 'codex', snapshot: async () => ({ runtime: 'codex', models: [{ id: 'gpt-5.6-sol' }, { id: 'gpt-6-astra' }] }) };

  function run(settings, arg) {
    return executeWorkersCommand({ name: 'workers', arg }, { event: OWNER_DM, connectors, settings, tasks, router, now: 1_000_000 });
  }

  it('is for the owner in a direct chat, on an instance with workers', async () => {
    const { settings } = settingsWith();
    for (const event of [{ ...OWNER_DM, chatType: 'group' }, { ...OWNER_DM, senderId: 'ou_alice' }]) {
      assert.match((await executeWorkersCommand({ name: 'workers', arg: '' }, { event, connectors, settings })).text, /^仅限已绑定的 owner/);
    }
    assert.match((await executeWorkersCommand({ name: 'workers', arg: '' }, { event: OWNER_DM, connectors, settings: null })).text, /没有启用 workers 调度/);
  });

  it('shows what workers run on, apart from the router, and the tasks at work on each runtime', async () => {
    const { settings } = settingsWith({ config: { fallbackRuntime: 'pi' } });
    settings.markUnavailable('claude', 'quota', 10 * 60_000);
    const { ok, text } = await run(settings, '');
    assert.equal(ok, true);
    assert.match(text, /\nRuntime：codex（跟随 router）\n模型：gpt-5\.6-sol（跟随 router）\n推理档位：medium（跟随 router）\n/);
    assert.match(text, /\n备用 Runtime：pi（同 router 的备用 Runtime）/);
    assert.match(text, /\n暂不可用：claude（额度用尽，约 10 分钟后再试/);
    assert.match(text, /\n进行中的任务：codex 2 · pi 1\n用法：/);
  });

  it('sets the runtime new tasks take, and says that tasks at work stay where they are', async () => {
    const { settings } = settingsWith();
    const reply = await run(settings, 'runtime pi');
    assert.equal(reply.ok, true);
    assert.match(reply.text, /进行中的 2 个任务继续在原 Runtime 上做完。/);
    assert.equal(settings.preferred(), 'pi');
    // By alias and prefix; a runtime workers cannot run on is refused.
    assert.equal((await run(settings, 'run trae')).ok, true);
    assert.equal(settings.preferred(), 'traex');
    assert.match((await run(settings, 'runtime dsh')).text, /worker 不能运行在 dsh 上/);
    assert.match((await run(settings, 'runtime router')).text, /^worker 改为跟随 router，当前是 codex。/);
    assert.equal(settings.configuredRuntime(), null);
  });

  it('sets the model and effort per runtime, checked against what that runtime is known to serve', async () => {
    const { settings } = settingsWith();
    // On the router's runtime, its live catalogue: a prefix picks one.
    assert.match((await run(settings, 'model gpt-6')).text, /^worker 在 codex 上的模型已设为 gpt-6-astra。/);
    assert.match((await run(settings, 'model gpt-9')).text, /^未找到模型：gpt-9\n可选：gpt-5\.6-sol、gpt-6-astra$/);
    // Another runtime, named in the selector; no catalogue there, taken as typed.
    assert.match((await run(settings, 'model pi:volcengine-ark/ep-2')).text, /^worker 在 pi 上的模型已设为 volcengine-ark\/ep-2。/);
    assert.equal(settings.own('pi').model, 'volcengine-ark/ep-2');
    assert.match((await run(settings, 'model default')).text, /^worker 在 codex 上的模型已恢复默认：gpt-5\.6-sol（跟随 router）/);
    assert.match((await run(settings, 'effort x')).text, /^worker 在 codex 上的推理档位已设为 xhigh。/);
    assert.match((await run(settings, 'effort max')).text, /^worker 在 codex 上的推理档位已设为 max。/);
    assert.match((await run(settings, 'effort ultra')).text, /^worker 在 codex 上的推理档位已设为 ultra。/);
    assert.match((await run(settings, 'effort turbo')).text, /^不支持的档位：turbo\n可选：none、minimal、low、medium、high、xhigh、max、ultra$/);
    assert.match((await run(settings, 'bogus')).text, /^用法：/);
  });

  it('checks a worker effort against the model it will run, not the runtime\'s whole list', async () => {
    const { settings } = settingsWith({ router: { runtime: 'codex', model: 'gpt-6-astra', effort: 'high' } });
    const catalogue = { runtime: 'codex', snapshot: async () => ({ runtime: 'codex', models: [
      { id: 'gpt-6-astra', efforts: ['low', 'high', 'max'], defaultEffort: 'high', isDefault: true },
      { id: 'gpt-5.6-sol', efforts: ['low', 'medium'], defaultEffort: 'medium' },
    ] }) };
    const send = (arg) => executeWorkersCommand({ name: 'workers', arg }, { event: OWNER_DM, connectors, settings, tasks, router: catalogue, now: 1_000_000 });
    assert.match((await send('effort')).text, /\n模型：gpt-6-astra（跟随 router）\n推理档位：high（跟随 router）\n可选：low、high、max\n/);
    assert.match((await send('effort ultra')).text, /^不支持的档位：ultra\n可选：low、high、max$/);
    assert.equal((await send('effort max')).ok, true);
    // Another model on the same runtime lists its own levels, and the max set
    // for the first one is dropped because this model does not offer it.
    assert.match((await send('model gpt-5.6')).text, /没有 max 这一档，已恢复默认档位/);
    assert.equal(settings.own('codex').effort, '');
    assert.match((await send('effort')).text, /\n模型：gpt-5.6-sol（单独设置）\n推理档位：CLI 默认\n可选：low、medium\n/);
    assert.match((await send('effort high')).text, /^不支持的档位：high\n可选：low、medium$/);
  });

  it('sets how many worker turns run at once, plus how many more only follow-ups may take', async () => {
    const { settings, store } = settingsWith();
    let pumped = 0;
    const instance = { slots: 3, reservedSlots: 1 };
    const taskApi = { overview: () => ({ live: [] }), limits: () => settings.limits(instance), pump: () => { pumped += 1; } };
    const send = (arg) => executeWorkersCommand({ name: 'workers', arg }, { event: OWNER_DM, connectors, settings, tasks: taskApi, router, now: 1_000_000 });
    assert.match((await send('slots')).text, /^并发槽位：3，另有 1 个只给跟进任务（最多同时 4 轮）\n用法：/);
    assert.match((await send('')).text, /\n并发槽位：3，另有 1 个只给跟进任务（最多同时 4 轮）\n/, 'the overview shows them too');

    const set = await send('slots 6 2');
    assert.equal(set.ok, true);
    assert.match(set.text, /^已设置。并发槽位：6，另有 2 个只给跟进任务（最多同时 8 轮）/);
    assert.equal(pumped, 1, 'a raised limit starts queued work now');
    assert.deepEqual(settings.limits(instance), { slots: 6, reservedSlots: 2 });

    assert.equal((await send('slots 2')).ok, true, 'a reserved count above the new slots is fine: they come on top');
    assert.deepEqual(settings.limits(instance), { slots: 2, reservedSlots: 2 }, 'without a reserved count, the one set stays');
    assert.match((await send('slots 4 0')).text, /并发槽位：4（不另留跟进专用槽位）/);

    for (const bad of ['slots 0', 'slots 65', 'slots 6x', 'slots 6 65', 'slots 6 default', 'slots 6 2 9']) {
      const reply = await send(bad);
      assert.equal(reply.ok, false, bad);
      assert.match(reply.text, /用法：\/workers slots/, bad);
    }
    assert.deepEqual(settings.limits(instance), { slots: 4, reservedSlots: 0 }, 'a refused value changes nothing');

    assert.match((await send('slots default')).text, /^已恢复实例配置。并发槽位：3，另有 1 个/);
    assert.deepEqual(settings.limits(instance), { slots: 3, reservedSlots: 1 });

    assert.equal((await send('slots 5')).ok, true);
    assert.equal(store.getSetting('worker_reserved_slots'), '', 'a reserved count never typed is not written');
    instance.reservedSlots = 3;
    assert.deepEqual(settings.limits(instance), { slots: 5, reservedSlots: 3 }, 'so it still follows the instance');
  });
});
