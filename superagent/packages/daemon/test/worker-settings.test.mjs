// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { WorkerSettings, WORKER_RUNTIME_COOLDOWN_MS, classifyWorkerFailure } from '../src/worker-settings.mjs';
import { executeWorkersCommand } from '../src/worker-commands.mjs';
import { executeCommand, helpText, parseCommand } from '../src/commands.mjs';

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
  });

  it('takes the router\'s backup as fallback only where workers run, and can be set or turned off', () => {
    assert.equal(settingsWith({ config: { fallbackRuntime: 'dsh' } }).settings.fallback(), null);
    const { settings } = settingsWith({ config: { fallbackRuntime: 'codex' } });
    // The router's backup is the runtime the task is on: nowhere to go.
    assert.equal(settings.switchTarget('codex'), null);
    settings.setFallback('claude');
    assert.deepEqual([settings.fallback(), settings.fallbackSource(), settings.switchTarget('codex')], ['claude', 'set', 'claude']);
    settings.setFallback('off');
    assert.deepEqual([settings.fallback(), settings.fallbackSource()], [null, 'off']);
    settings.setFallback(null);
    assert.deepEqual([settings.fallback(), settings.fallbackSource()], ['codex', 'config']);
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
    assert.match(text, /^后台 worker（只影响之后新建和复开的任务；进行中的任务在原 Runtime 上做完/);
    assert.match(text, /\nRuntime：codex（跟随 router）\n模型：gpt-5\.6-sol（跟随 router）\n推理档位：medium（跟随 router）\n/);
    assert.match(text, /\n备用 Runtime：pi（同 router 的备用 Runtime）/);
    assert.match(text, /\n暂不可用：claude（额度用尽，约 10 分钟后再试/);
    assert.match(text, /\n进行中的任务：codex 2 · pi 1\n用法：/);
  });

  it('sets the runtime new and reopened tasks take, and says that tasks at work stay where they are', async () => {
    const { settings } = settingsWith();
    let reply = await run(settings, 'runtime pi');
    assert.equal(reply.ok, true);
    assert.match(reply.text, /^后台 worker 的 Runtime 已设为 pi（模型 CLI 默认）。\n之后新建和复开的任务在这里运行，复开的任务会带上原 worker 的交接信息。\n进行中的 2 个任务继续在原 Runtime 上做完。$/);
    assert.equal(settings.preferred(), 'pi');
    assert.match((await run(settings, '')).text, /\nRuntime：pi（单独设置）\n模型：CLI 默认\n推理档位：CLI 默认\n/);
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
    assert.match((await run(settings, 'effort turbo')).text, /^不支持的档位：turbo\n可选：none、minimal、low、medium、high、xhigh$/);
    assert.match((await run(settings, 'fallback claude')).text, /^worker 的备用 Runtime 已设为 claude。$/);
    assert.match((await run(settings, 'fallback off')).text, /^已关闭 worker 的备用 Runtime/);
    assert.match((await run(settings, 'bogus')).text, /^用法：/);
  });

  it('is a slash command of its own: /w matches it, help lists it, and the Core answers it', async () => {
    assert.deepEqual(parseCommand('/w runtime pi'), { name: 'workers', arg: 'runtime pi' });
    assert.match(helpText({}), /\/workers \[runtime \| model \| effort \| fallback …\] — 查看或设置后台 worker/);
    const runtime = { snapshot: async () => ({ runtime: 'pi', work: { working: false, connected: true } }) };
    assert.deepEqual(await executeCommand({ name: 'workers', arg: '' }, runtime, { workers: () => ({ ok: true, text: 'shown' }) }), { ok: true, text: 'shown' });
    assert.match((await executeCommand({ name: 'workers', arg: '' }, runtime)).text, /当前环境不支持 \/workers。/);
  });
});
