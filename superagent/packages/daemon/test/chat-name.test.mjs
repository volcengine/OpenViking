// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { ChatNameTracker } from '../src/chat-names.mjs';

const quietLogger = {
  debug() {},
  info() {},
  warn() {},
  error() {},
  child() { return this; },
};

function trackerHarness(source, getChatName) {
  const saved = [];
  const deferred = [];
  // Like the real store: once a lookup persisted a result, the chat stops
  // being a due candidate until its next refresh window.
  const settled = () => saved.length > 0 || deferred.length > 0;
  const tracker = new ChatNameTracker({
    lark: { profile: 'test-profile', getChatName },
    store: {
      chatNameLookupCandidate(chatId) {
        return !settled() && chatId === source.chatId ? source : null;
      },
      dueChatNameLookups() {
        return settled() ? [] : [source];
      },
      saveChatSourceName(...args) {
        saved.push(args);
        return true;
      },
      deferChatSourceName(...args) {
        deferred.push(args);
        return true;
      },
    },
    logger: quietLogger,
  });
  return { tracker, saved, deferred };
}

describe('non-blocking group name enrichment', () => {
  it('deduplicates an in-flight lookup and persists a successful result', async () => {
    const source = {
      chatId: 'oc-private-source',
      sourceRef: 'opaque-source',
      chatType: 'group',
      failureCount: 0,
    };
    let finish;
    let calls = 0;
    const { tracker, saved, deferred } = trackerHarness(source, () => {
      calls += 1;
      return new Promise((resolve) => { finish = resolve; });
    });

    assert.equal(tracker.schedule(source.chatId), true);
    assert.equal(tracker.schedule(source.chatId), false);
    assert.equal(calls, 1);
    const lookup = tracker.lookups.get(source.chatId);
    finish('研发同步群');
    await lookup;

    assert.equal(saved.length, 1);
    assert.equal(saved[0][0], source.chatId);
    assert.equal(saved[0][1], '研发同步群');
    assert.ok(saved[0][2] > saved[0][3]);
    assert.equal(deferred.length, 0);
    assert.equal(tracker.lookups.size, 0);
  });

  it('turns permission or network failure into bounded retry state', async () => {
    const source = {
      chatId: 'oc-retry-source',
      sourceRef: 'opaque-retry',
      chatType: 'group',
      failureCount: 2,
    };
    const { tracker, saved, deferred } = trackerHarness(source, async () => {
      throw new Error('permission denied');
    });
    const before = Date.now();
    assert.equal(tracker.schedule(source.chatId), true);
    await tracker.lookups.get(source.chatId);

    assert.equal(saved.length, 0);
    assert.equal(deferred.length, 1);
    assert.equal(deferred[0][0], source.chatId);
    assert.equal(deferred[0][1], 'RETRY');
    assert.ok(deferred[0][2] >= before + (20 * 60 * 1_000));
    assert.ok(deferred[0][2] <= before + (20 * 60 * 1_000) + 1_000);
  });

  it('scans due sources so migrated chats are retried without a new message', async () => {
    const source = {
      chatId: 'oc-migrated-source',
      sourceRef: 'opaque-migrated',
      chatType: 'group',
      failureCount: 0,
    };
    const { tracker, saved } = trackerHarness(source, async () => '历史群聊');
    tracker.refresh();
    await tracker.lookups.get(source.chatId);
    assert.equal(saved[0][1], '历史群聊');
  });
});
