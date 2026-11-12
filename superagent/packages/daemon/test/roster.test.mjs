// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  ChatRoster,
  collectRosterMembers,
  lookupMemberName,
  registerNameResolver,
  sanitizeMemberName,
} from '../src/roster.mjs';

const quietLogger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

describe('sanitizeMemberName', () => {
  it('keeps names as single whitespace-free tokens', () => {
    assert.equal(sanitizeMemberName('张 三'), '张_三');
    assert.equal(sanitizeMemberName('  Alice  Example '), 'Alice_Example');
  });

  it('neutralizes bracket-metadata forgery attempts', () => {
    // `[`, `]`, `=`, control chars all collapse: the result cannot open or close
    // a daemon-authenticated bracket or introduce a key=value token.
    assert.equal(sanitizeMemberName('x] [chat=oc_evil'), 'x_chat_oc_evil');
    assert.equal(sanitizeMemberName('a\nb\u0000c'), 'a_b_c');
    assert.ok(!/[[\]=\s]/.test(sanitizeMemberName('e] [from=me] =')));
  });

  it('caps length and returns null for empty input', () => {
    assert.equal(sanitizeMemberName('x'.repeat(100)).length, 40);
    assert.equal(sanitizeMemberName('   '), null);
    assert.equal(sanitizeMemberName(null), null);
  });
});

describe('collectRosterMembers', () => {
  it('reads users and bots buckets and drops invalid entries', () => {
    const members = collectRosterMembers({
      users: [
        { name: 'Alice', open_id: 'ou_alice12345678' },
        { name: '', open_id: 'ou_unnamed123456' },
        { name: 'NoId' },
      ],
      bots: [{ name: 'Helper Bot', open_id: 'ou_bot123456789' }],
    });
    assert.deepEqual(members, [
      { id: 'ou_alice12345678', name: 'Alice' },
      { id: 'ou_bot123456789', name: 'Helper_Bot' },
    ]);
  });
});

function makeRoster({ members, ttlMs = 600_000, warmTimeoutMs = 50 } = {}) {
  const state = { fetches: 0, fail: false, nowMs: 1_000_000, members };
  const roster = new ChatRoster({
    fetchMembers: async () => {
      state.fetches += 1;
      if (state.fail) throw new Error('lark-cli exploded');
      return { users: state.members, bots: [] };
    },
    ttlMs,
    warmTimeoutMs,
    logger: quietLogger,
    now: () => state.nowMs,
  });
  return { roster, state };
}

describe('ChatRoster', () => {
  it('caches per chat within the TTL and refreshes after expiry', async () => {
    const { roster, state } = makeRoster({
      members: [{ name: 'Alice', open_id: 'ou_alice12345678' }],
    });
    await roster.warm('oc_chat123456');
    await roster.warm('oc_chat123456');
    assert.equal(state.fetches, 1);
    assert.equal(roster.nameForId('oc_chat123456', 'ou_alice12345678'), 'Alice');
    state.nowMs += 601_000; // past the TTL
    await roster.warm('oc_chat123456');
    assert.equal(state.fetches, 2);
  });

  it('never fails a caller when the fetch errors and keeps the stale cache', async () => {
    const { roster, state } = makeRoster({
      members: [{ name: 'Alice', open_id: 'ou_alice12345678' }],
    });
    await roster.warm('oc_chat123456');
    state.fail = true;
    state.nowMs += 601_000;
    assert.equal(await roster.warm('oc_chat123456'), false);
    // Stale names remain usable; unknown ids still miss (stub fallback upstream).
    assert.equal(roster.nameForId('oc_chat123456', 'ou_alice12345678'), 'Alice');
    assert.equal(roster.nameForId('oc_chat123456', 'ou_other1234567'), null);
  });

  it('bounds warm() by its timeout instead of blocking ingest', async () => {
    const roster = new ChatRoster({
      fetchMembers: () => new Promise(() => {}), // never resolves
      warmTimeoutMs: 20,
      logger: quietLogger,
    });
    const startedAt = Date.now();
    assert.equal(await roster.warm('oc_chat123456'), false);
    assert.ok(Date.now() - startedAt < 1_000);
  });

  it('resolves names case-insensitively with _ and spaces equivalent', async () => {
    const { roster } = makeRoster({
      members: [
        { name: '张 三', open_id: 'ou_zhang1234567' },
        { name: 'Alice', open_id: 'ou_alice12345678' },
      ],
    });
    assert.deepEqual(
      await roster.resolveNames('oc_chat123456', ['张_三', 'alice']),
      [{ name: '张_三', id: 'ou_zhang1234567' }, { name: 'alice', id: 'ou_alice12345678' }],
    );
  });

  it('forces one refresh on a miss before rejecting an unknown name', async () => {
    const { roster, state } = makeRoster({
      members: [{ name: 'Alice', open_id: 'ou_alice12345678' }],
    });
    await roster.warm('oc_chat123456');
    state.members = [
      { name: 'Alice', open_id: 'ou_alice12345678' },
      { name: 'Newcomer', open_id: 'ou_new123456789' },
    ];
    // The cached roster misses; a forced refresh picks up the new member.
    assert.deepEqual(
      await roster.resolveNames('oc_chat123456', ['Newcomer']),
      [{ name: 'Newcomer', id: 'ou_new123456789' }],
    );
    assert.equal(state.fetches, 2);
    await assert.rejects(
      () => roster.resolveNames('oc_chat123456', ['Ghost']),
      /unknown member name.*Ghost/,
    );
  });

  it('rejects ambiguous names listing the candidates', async () => {
    const { roster } = makeRoster({
      members: [
        { name: 'Alex', open_id: 'ou_alexone123456' },
        { name: 'alex', open_id: 'ou_alextwo123456' },
      ],
    });
    await assert.rejects(
      () => roster.resolveNames('oc_chat123456', ['Alex']),
      (error) => {
        assert.match(error.message, /ambiguous/);
        assert.match(error.message, /ou_alexone123456/);
        assert.match(error.message, /ou_alextwo123456/);
        return true;
      },
    );
  });

  it('rejects with a roster-unavailable error when the fetch never succeeds', async () => {
    const { roster, state } = makeRoster({ members: [] });
    state.fail = true;
    await assert.rejects(
      () => roster.resolveNames('oc_chat123456', ['Alice']),
      /roster is unavailable/,
    );
  });
});

describe('process-wide name resolver', () => {
  it('serves registered lookups and fails closed on resolver errors', () => {
    try {
      registerNameResolver((chatId, userId) => (userId === 'ou_alice12345678' ? 'Alice' : null));
      assert.equal(lookupMemberName('oc_chat123456', 'ou_alice12345678'), 'Alice');
      assert.equal(lookupMemberName('oc_chat123456', 'ou_other1234567'), null);
      registerNameResolver(() => { throw new Error('boom'); });
      assert.equal(lookupMemberName('oc_chat123456', 'ou_alice12345678'), null);
    } finally {
      registerNameResolver(null);
    }
    assert.equal(lookupMemberName('oc_chat123456', 'ou_alice12345678'), null);
  });
});
