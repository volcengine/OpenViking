// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { startMockEnv, textMessage } from './mock-env.mjs';

const count = (text, pattern) => (text.match(pattern) ?? []).length;

// A real daemon, a fake Codex app-server that works on a HOLD request until it
// is interrupted, and the same fake serving TraeX. The owner switches runtimes
// in the middle of that work and sends nothing else.
describe('/runtime in the middle of a turn', () => {
  for (const scheduler of ['legacy', 'inbox', 'workers']) {
    it(`goes on with the interrupted request on the new runtime, once, with no new message (${scheduler})`, async () => {
      const env = await startMockEnv({ codex: { behavior: 'switch' }, config: { scheduler } });
      try {
        env.receive(textMessage({ messageId: 'om-hold', text: 'HOLD: summarize the release notes' }));
        await env.waitFor(() => env.codexInput().some((entry) => entry.method === 'turn/start' && /msg=om-hold/.test(entry.text)));
        const codexPid = env.codexInput().find((entry) => entry.method === 'turn/start').pid;

        env.receive(textMessage({ messageId: 'om-switch', text: '/runtime traex' }));
        await env.waitForReply(/已切换 Runtime：traex/);
        await env.waitForReply(/mock reply/);

        const replies = env.replies().map((args) => args.join(' '));
        assert.equal(replies.filter((line) => /mock reply/.test(line)).length, 1, 'answered once');
        assert.ok(replies.some((line) => /om-hold/.test(line) && /mock reply/.test(line)), 'on the interrupted message');
        assert.ok(replies.some((line) => /被打断的 1 条消息由 traex 接着处理，不用重发/.test(line)));
        assert.equal(replies.filter((line) => /处理异常/.test(line)).length, 0, 'no failure notice');

        const turns = env.codexInput().filter((entry) => entry.method === 'turn/start');
        assert.equal(turns.length, 2);
        const [first, second] = turns;
        assert.equal(first.pid, codexPid);
        assert.notEqual(second.pid, codexPid, 'the second turn runs on the TraeX process');
        assert.ok(env.codexInput().some((entry) => entry.method === 'turn/interrupt' && entry.pid === codexPid));
        // The handed-on turn: notice, then the request, once (the history in
        // front of it ends before it).
        assert.match(second.text, /\[daemon notice\] \/runtime switched from codex to you while codex was working\./);
        assert.match(second.text, /It sent nothing after the first of these inputs\./);
        assert.equal(count(second.text, /msg=om-hold/g), 1);
        assert.ok(second.text.indexOf('[daemon notice]') < second.text.indexOf('msg=om-hold'));
        assert.doesNotMatch(second.text, /om-switch/, 'the command itself is not model input');

        const activity = env.activities('agent.switch_resumed');
        assert.equal(activity.length, 1);
        assert.deepEqual([activity[0].metadata.from, activity[0].metadata.to], ['codex', 'traex']);
      } finally {
        await env.stop();
      }
    });
  }
});

describe('/runtime with more input around the interrupted turn', () => {
  it('hands on a line steered into the interrupted turn with it, and answers the newest once (legacy)', async () => {
    const env = await startMockEnv({ codex: { behavior: 'switch' }, config: { scheduler: 'legacy' } });
    try {
      env.receive(textMessage({ messageId: 'om-hold', text: 'HOLD: summarize the release notes' }));
      await env.waitFor(() => env.codexInput().some((entry) => entry.method === 'turn/start'));
      env.receive(textMessage({ messageId: 'om-more', text: 'and list the breaking changes' }));
      await env.waitFor(() => env.codexInput().some((entry) => entry.method === 'turn/steer' && /msg=om-more/.test(entry.text)));
      env.receive(textMessage({ messageId: 'om-switch', text: '/runtime traex' }));
      await env.waitForReply(/被打断的 2 条消息由 traex 接着处理/);
      await env.waitForReply(/mock reply/);
      const second = env.codexInput().filter((entry) => entry.method === 'turn/start')[1];
      assert.equal(count(second.text, /msg=om-hold/g), 1);
      assert.equal(count(second.text, /msg=om-more/g), 1);
      assert.ok(second.text.indexOf('msg=om-hold') < second.text.indexOf('msg=om-more'), 'in the order they came');
      const replies = env.replies().map((args) => args.join(' ')).filter((line) => /mock reply/.test(line));
      assert.equal(replies.length, 1);
      assert.match(replies[0], /om-more/);
    } finally {
      await env.stop();
    }
  });

  it('leaves a line still waiting in the inbox to the inbox, after the handed-on one (inbox)', async () => {
    const env = await startMockEnv({ codex: { behavior: 'switch' }, config: { scheduler: 'inbox' } });
    try {
      env.receive(textMessage({ messageId: 'om-hold', text: 'HOLD: summarize the release notes' }));
      await env.waitFor(() => env.codexInput().some((entry) => entry.method === 'turn/start'));
      env.receive(textMessage({ messageId: 'om-later', chatId: 'oc-other', text: 'unrelated question' }));
      await env.waitFor(() => env.activities('message.received').length >= 2
        || env.daemon.inbox?.size > 0);
      env.receive(textMessage({ messageId: 'om-switch', text: '/runtime traex' }));
      await env.waitForReply(/被打断的 1 条消息由 traex 接着处理/);
      await env.waitFor(() => env.replies().filter((args) => args.join(' ').includes('mock reply')).length >= 2);
      const turns = env.codexInput().filter((entry) => entry.method === 'turn/start');
      assert.match(turns[1].text, /\[daemon notice\] \/runtime switched/);
      assert.match(turns[1].text, /msg=om-hold/);
      assert.doesNotMatch(turns[1].text, /om-later/, 'the inbox keeps what the model never had');
      assert.equal(turns.filter((entry) => /msg=om-later/.test(entry.text)).length, 1);
      const replies = env.replies().map((args) => args.join(' ')).filter((line) => /mock reply/.test(line));
      assert.equal(replies.length, 2);
      assert.equal(replies.filter((line) => /om-hold/.test(line)).length, 1);
      assert.equal(replies.filter((line) => /om-later/.test(line)).length, 1);
    } finally {
      await env.stop();
    }
  });
});
