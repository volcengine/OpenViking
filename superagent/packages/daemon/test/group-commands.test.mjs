// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { executeWorkersCommand } from '../src/worker-commands.mjs';
import { executeCommand, parseCommand } from '../src/commands.mjs';

const GROUP = { chatType: 'group', chatId: 'oc_group', senderType: 'user', senderId: 'ou_alice' };
const connectors = { isOwner: (event) => event.senderId === 'ou_owner' };

// Only what a public view is allowed to read: the task states, never settings.
const tasksWith = (live) => ({ overview: () => ({ recent: 0, live }) });

describe('/workers in a group', () => {
  it('counts what the workers are doing, without runtimes, models or slots', async () => {
    const tasks = tasksWith([{ status: 'running' }, { status: 'running' }, { status: 'queued' }, { status: 'waiting' }]);
    const outcome = await executeWorkersCommand({ name: 'workers', arg: '' }, { event: GROUP, connectors, settings: null, tasks, publicView: true });
    assert.equal(outcome.ok, true);
    assert.equal(outcome.text, '后台 worker：2 个在忙，1 个排队，1 个等回复\n进行中的任务：4 个');
    assert.doesNotMatch(outcome.text, /Runtime|模型|槽位|pi|codex/);
  });

  it('does not change a setting from a group', async () => {
    const tasks = tasksWith([]);
    for (const arg of ['runtime pi', 'model gpt-5.5', 'effort high', 'fallback codex', 'slots 6']) {
      const refused = await executeWorkersCommand({ name: 'workers', arg }, { event: GROUP, connectors, settings: null, tasks, publicView: true });
      assert.equal(refused.ok, false, arg);
      assert.match(refused.text, /^worker 的 Runtime、模型和槽位设置只能在私聊里改/);
    }
  });

  it('says so when the instance has no worker scheduler', async () => {
    const outcome = await executeWorkersCommand({ name: 'workers', arg: '' }, { event: GROUP, connectors, settings: null, tasks: null, publicView: true });
    assert.equal(outcome.text, '这个实例没有启用后台 worker。');
  });

  it('still lets only the owner run /workers in a direct chat', async () => {
    const dm = { chatType: 'p2p', senderType: 'user', senderId: 'ou_alice' };
    const outcome = await executeWorkersCommand({ name: 'workers', arg: '' }, { event: dm, connectors, settings: null, tasks: null });
    assert.match(outcome.text, /^仅限已绑定的 owner 在与机器人的私聊中使用 \/workers。$/);
  });
});

describe('a group /workers from the command layer', () => {
  it('reaches the public view and never the settings card', async () => {
    const tasks = tasksWith([{ status: 'running' }]);
    const runtime = { snapshot: async () => ({ runtime: 'pi', model: 'gpt-5.5', work: { working: false, connected: true } }) };
    const outcome = await executeCommand(parseCommand('/workers'), runtime, {
      publicView: true,
      workers: (parsed) => executeWorkersCommand(parsed, { event: GROUP, connectors, settings: null, tasks, publicView: true }),
    });
    assert.equal(outcome.text, '后台 worker：1 个在忙\n进行中的任务：1 个');
    assert.equal(outcome.card, undefined);
  });
});
