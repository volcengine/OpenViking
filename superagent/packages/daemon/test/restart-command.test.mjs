// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { RESTART_USAGE, executeRestartCommand, scheduleInstanceRestart } from '../src/restart-command.mjs';

const OWNER_DM = { chatType: 'p2p', senderType: 'user', senderId: 'ou_owner' };
const connectors = { isOwner: (event) => event.senderId === 'ou_owner' };

describe('/restart', () => {
  it('is for the owner in a direct chat only', () => {
    let scheduled = 0;
    const schedule = () => { scheduled += 1; };
    const refused = /^仅限已绑定的 owner 在与机器人的私聊中使用 \/restart。$/;
    for (const event of [
      { ...OWNER_DM, chatType: 'group' },
      { ...OWNER_DM, senderId: 'ou_alice' },
      { ...OWNER_DM, senderType: 'bot' },
    ]) {
      const outcome = executeRestartCommand({ name: 'restart', arg: '' }, { event, connectors, schedule });
      assert.match(outcome.text, refused);
      assert.equal(outcome.ok, false);
    }
    assert.equal(scheduled, 0, 'a refusal never restarts anything');
  });

  it('schedules the restart and says so before the instance goes quiet', () => {
    let scheduled = 0;
    const outcome = executeRestartCommand({ name: 'restart', arg: '' }, {
      event: OWNER_DM, connectors, schedule: () => { scheduled += 1; },
    });
    assert.equal(outcome.ok, true);
    assert.equal(scheduled, 1);
    assert.match(outcome.text, /这条回复送达后开始/);
  });

  it('takes no argument, and says why nothing happened when it cannot restart', () => {
    assert.equal(executeRestartCommand({ name: 'restart', arg: 'now' }, { event: OWNER_DM, connectors, schedule: () => {} }).text, RESTART_USAGE);
    assert.match(executeRestartCommand({ name: 'restart', arg: '' }, { event: OWNER_DM, connectors }).text, /不能重启这个实例/);
    const failing = executeRestartCommand({ name: 'restart', arg: '' }, {
      event: OWNER_DM, connectors, schedule: () => { throw new Error('no such instance'); },
    });
    assert.equal(failing.ok, false);
    assert.match(failing.text, /没能安排重启：no such instance/);
  });
});

describe('scheduleInstanceRestart', () => {
  function harness() {
    const spawns = [];
    const timers = [];
    const options = {
      instanceId: 'alpha',
      env: { HOME: '/home/owner' },
      node: '/opt/node',
      cli: '/opt/cli/ov-superagent.mjs',
      spawnImpl: (command, args, spawnOptions) => {
        spawns.push({ command, args, options: spawnOptions, unref: false });
        return { unref() { spawns.at(-1).unref = true; } };
      },
      timer: (callback, delay) => { timers.push({ callback, delay }); return { unref() {} }; },
    };
    return { spawns, timers, options };
  }

  it('starts the CLI detached, after the reply has had time to go out', () => {
    const { spawns, timers, options } = harness();
    scheduleInstanceRestart(options);
    assert.deepEqual(spawns, [], 'nothing runs before the delay');
    assert.equal(timers.length, 1);
    assert.equal(timers[0].delay, 3_000);
    timers[0].callback();
    assert.deepEqual(spawns, [{
      command: '/opt/node',
      args: ['/opt/cli/ov-superagent.mjs', 'restart', 'alpha'],
      options: { detached: true, stdio: 'ignore', env: { HOME: '/home/owner' } },
      unref: true,
    }]);
  });

  it('needs the instance id, and swallows a spawn failure instead of crashing the turn', () => {
    assert.throws(() => scheduleInstanceRestart({ instanceId: '' }), /needs the instance id/);
    const timers = [];
    scheduleInstanceRestart({
      instanceId: 'alpha',
      timer: (callback) => { timers.push(callback); return { unref() {} }; },
      spawnImpl: () => { throw new Error('spawn failed'); },
    });
    assert.doesNotThrow(() => timers[0]());
  });
});
