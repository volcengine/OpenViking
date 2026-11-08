// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { startMockEnv, textMessage } from './mock-env.mjs';

// The fake codex's 'inbox' behavior: a turn whose input says LONG stays open,
// an inbox notice makes it call message_check, and the line that follows gets
// one reply that ends the turn.
describe('inbox scheduler end to end', () => {
  it('holds an input that arrives mid-turn, notices it without its text, and delivers it on message_check', async () => {
    const env = await startMockEnv({ codex: { behavior: 'inbox' }, config: { scheduler: 'inbox' } });
    try {
      env.receive(textMessage({ messageId: 'om-long', text: 'LONG task for the owner', chatId: 'oc-a' }));
      await env.waitForCodexInput(/om-long/);
      env.receive(textMessage({ messageId: 'om-quick', text: 'a quick question', chatId: 'oc-b', senderId: 'ou-other' }));
      await env.waitForReply(/mock reply/, 15_000);

      const input = env.codexInput();
      const steers = input.filter((entry) => entry.method === 'turn/steer');
      assert.equal(input.filter((entry) => entry.method === 'turn/start').length, 1, 'no second turn');
      assert.match(steers[0].text, /^\[daemon inbox\] waiting=1 chats=1; new since the last notice:\n- chat=oc-b source=lark type=p2p waiting=1/);
      assert.doesNotMatch(steers[0].text, /quick question|om-quick/);
      assert.match(input.find((entry) => entry.method === 'tool-result').text, /^Delivered 1 waiting input\(s\), oldest first\. They follow as your next input/);
      assert.match(steers[1].text, /msg=om-quick .*a quick question/);
      assert.equal(env.activities('inbox.notified').length, 1);
      assert.deepEqual(env.activities('inbox.delivered').map((row) => row.metadata.reason), ['wake', 'check']);
      assert.equal(env.daemon.store.db.prepare('SELECT COUNT(*) AS n FROM inbox_pending').get().n, 0);
    } finally {
      await env.stop();
    }
  });

  it('legacy steers the same input into the running turn at once, and has no inbox', async () => {
    const env = await startMockEnv({ codex: { behavior: 'inbox' } });
    try {
      env.receive(textMessage({ messageId: 'om-long', text: 'LONG task for the owner', chatId: 'oc-a' }));
      await env.waitForCodexInput(/om-long/);
      env.receive(textMessage({ messageId: 'om-quick', text: 'a quick question', chatId: 'oc-b', senderId: 'ou-other' }));
      await env.waitForReply(/mock reply/, 15_000);
      const steers = env.codexInput().filter((entry) => entry.method === 'turn/steer');
      assert.equal(steers.length, 1);
      assert.match(steers[0].text, /msg=om-quick .*a quick question/);
      assert.equal(env.daemon.store.db.prepare("SELECT COUNT(*) AS n FROM sqlite_master WHERE name = 'inbox_pending'").get().n, 0);
      assert.ok(!env.daemon.actions.tools().some((tool) => tool.name === 'message_check'));
    } finally {
      await env.stop();
    }
  });
});
