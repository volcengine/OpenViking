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
  it('goes on with the interrupted request on the new runtime, once, with no new message', async () => {
    const env = await startMockEnv({ codex: { behavior: 'switch' } });
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
      // The handed-on turn: notice, then the request, once (the history in
      // front of it ends before it).
      assert.match(second.text, /\[daemon notice\] \/runtime switched from codex to you while codex was working\./);
      assert.equal(count(second.text, /msg=om-hold/g), 1);
      assert.ok(second.text.indexOf('[daemon notice]') < second.text.indexOf('msg=om-hold'));
      assert.doesNotMatch(second.text, /om-switch/, 'the command itself is not model input');
    } finally {
      await env.stop();
    }
  });
});
