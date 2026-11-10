// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, describe, it } from 'node:test';
import { Store } from '../src/store.mjs';
import { cognitionHistoryPrefix } from '../src/cognition.mjs';
import { chatEvent } from './event-fixture.mjs';

const quietLogger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

function event(id, content = `message ${id}`, overrides = {}) {
  return chatEvent({
    type: 'im.message.receive_v1',
    eventId: `evt-${id}`,
    messageId: `om-${id}`,
    conversationKey: overrides.conversationKey || 'default:tenant:oc-chat:main',
    chatId: 'oc-chat',
    chatType: 'group',
    messageType: 'text',
    senderId: 'ou-user',
    senderType: 'user',
    rootId: null,
    parentId: null,
    threadId: null,
    threadRootId: null,
    content,
    mentions: [],
    createTimeMs: Date.now(),
    raw: { id },
    ...overrides,
  });
}

describe('Store', () => {
  let directory;
  let store;

  beforeEach(() => {
    directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-store-'));
    store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
  });

  afterEach(() => {
    try { store.close(); } catch {}
    fs.rmSync(directory, { recursive: true, force: true });
  });

  it('records an accepted message DONE and deduplicates replay durably', () => {
    assert.equal(store.ingest(event('1')).inserted, true);
    assert.equal(store.ingest(event('1')).inserted, false);
    assert.equal(store.stats().inbound.DONE, 1);

    store.close();
    store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    assert.equal(store.ingest(event('1')).inserted, false);
  });

  it('resolves a quoted message author from inbound history or the journal', () => {
    store.ingest(event('parent', 'original', { senderId: 'ou-author' }));
    assert.deepEqual(store.resolveMessageAuthor('om-parent'), {
      kind: 'user', chatId: 'oc-chat', senderId: 'ou-author',
    });

    store.appendCognitionJournal({
      kind: 'delivery', messageId: 'om-mine', actor: 'me', chatId: 'oc-chat', text: 'sent by bot',
    });
    assert.deepEqual(store.resolveMessageAuthor('om-mine'), { kind: 'me', text: 'sent by bot' });

    assert.equal(store.resolveMessageAuthor('om-never-seen'), null);
    assert.equal(store.resolveMessageAuthor(null), null);
  });

  it('looks an inbound row up by seq for failure notices', () => {
    const { inbound } = store.ingest(event('7', 'needs a reply'));
    const found = store.inboundBySeq(inbound.seq);
    assert.equal(found.messageId, 'om-7');
    assert.equal(found.content, 'needs a reply');
    assert.equal(store.inboundBySeq(9999), null);
    assert.equal(store.inboundBySeq(null), null);
  });

  it('persists group chat names and applies retry timing without scheduling p2p lookups', () => {
    const now = 1_700_000_000_000;
    const group = event('named-source', 'group message', {
      chatId: 'oc-named-source',
      conversationKey: 'default:tenant:oc-named-source:main',
    });
    const direct = event('direct-source', 'direct message', {
      chatId: 'oc-direct-source',
      chatType: 'p2p',
      conversationKey: 'default:tenant:oc-direct-source:main',
    });
    store.ingest(group, null, now);
    store.ingest(direct, null, now + 1);

    assert.deepEqual(
      store.dueChatNameLookups(now + 2).map((source) => source.chatId),
      [group.chatId],
    );
    assert.equal(store.deferChatSourceName(group.chatId, 'RETRY', now + 5_000, now + 2), true);
    assert.equal(store.chatNameLookupCandidate(group.chatId, now + 4_999), null);
    assert.equal(store.chatNameLookupCandidate(group.chatId, now + 5_000).failureCount, 1);
    assert.equal(
      store.saveChatSourceName(group.chatId, 'Backend 日常群', now + 86_400_000, now + 5_001),
      true,
    );

    const databasePath = path.join(directory, 'state.sqlite3');
    store.close();
    store = new Store(databasePath, quietLogger);
    const persisted = store.db.prepare(`
      SELECT name, lookup_status, failure_count FROM chat_sources WHERE chat_id = ?
    `).get(group.chatId);
    assert.deepEqual({ ...persisted }, {
      name: 'Backend 日常群',
      lookup_status: 'RESOLVED',
      failure_count: 0,
    });
  });

  it('does not persist raw event envelopes and blanks ignored content', () => {
    const accepted = event('raw', 'visible', { raw: { token: 'secret-sentinel' } });
    store.ingest(accepted);
    const acceptedRow = store.db.prepare('SELECT event_json, content, status FROM connector_events WHERE message_id = ?')
      .get(accepted.messageId);
    assert.ok(!acceptedRow.event_json.includes('secret-sentinel'));
    assert.equal(acceptedRow.content, 'visible');
    assert.equal(acceptedRow.status, 'DONE');

    const ignored = event('ignored', 'private ignored body', { raw: { app_secret: 'secret-sentinel' } });
    store.ingest(ignored, 'unsupported_message_type');
    const ignoredRow = store.db.prepare(`
      SELECT event_json, content, mentions_json, status FROM connector_events WHERE message_id = ?
    `).get(ignored.messageId);
    const { event_json, ...fields } = ignoredRow;
    assert.ok(!event_json.includes('private ignored body'));
    assert.deepEqual(fields, {
      content: '',
      mentions_json: '[]',
      status: 'RULE_IGNORED',
    });
  });

  it('records message.received and message.ignored activities', () => {
    store.ingest(event('1', 'hello there'));
    store.ingest(event('2', 'private'), 'unsupported_message_type');
    const kinds = store.listActivities({ limit: 100 }).activities.map((a) => a.kind).sort();
    assert.deepEqual(kinds, ['message.ignored', 'message.received']);
    const serialized = JSON.stringify(store.listActivities({ limit: 100 }).activities);
    assert.equal(serialized.includes('private'), false);
  });

  it('stores one global session shared across chats and clears it durably', () => {
    assert.deepEqual(store.getGlobalSession(), {
      sessionId: null,
      policyHash: null,
      runtime: null,
      epoch: 0,
      syncedThroughSeq: 0,
      sessionReady: false,
    });
    store.setGlobalSession('thread-abc', 'a'.repeat(64));
    assert.deepEqual(store.getGlobalSession(), {
      sessionId: 'thread-abc',
      policyHash: 'a'.repeat(64),
      runtime: 'codex',
      epoch: 1,
      syncedThroughSeq: 0,
      sessionReady: true,
    });

    const databasePath = path.join(directory, 'state.sqlite3');
    store.close();
    store = new Store(databasePath, quietLogger);
    assert.equal(store.getGlobalSession().sessionId, 'thread-abc');
    store.clearGlobalSession();
    assert.equal(store.getGlobalSession().sessionId, null);
  });

  it('rolls back the complete global session tuple when a write fails', () => {
    store.setGlobalSession('stable-session', 'stable-policy', {
      runtime: 'claude',
      epoch: 3,
      syncedThroughSeq: 9,
      sessionReady: true,
    });
    const before = store.getGlobalSession();
    const statement = store.setMetaStatement;
    let writes = 0;
    store.setMetaStatement = {
      run(...args) {
        writes += 1;
        if (writes === 3) throw new Error('injected session tuple failure');
        return statement.run(...args);
      },
    };

    assert.throws(() => store.setGlobalSession('phantom-session', 'phantom-policy', {
      runtime: 'claude',
      epoch: 4,
      syncedThroughSeq: 0,
      sessionReady: false,
    }), /injected session tuple failure/);
    store.setMetaStatement = statement;
    assert.deepEqual(store.getGlobalSession(), before);
  });

  it('marks the owned session unresumable only under the current authority', () => {
    const cognition = store.initializeCognition('claude', 'claude-authority');
    store.setGlobalSession('sess-live1234', 'policy-hash', {
      runtime: 'claude',
      epoch: cognition.epoch,
      syncedThroughSeq: 4,
      sessionReady: true,
    });

    assert.equal(store.markOwnedSessionUnresumable({
      runtime: 'claude',
      authorityToken: 'stale-authority',
      sessionId: 'sess-live1234',
      epoch: cognition.epoch,
    }), false);
    assert.equal(store.getGlobalSession().sessionReady, true);

    assert.equal(store.markOwnedSessionUnresumable({
      runtime: 'claude',
      authorityToken: 'claude-authority',
      sessionId: 'sess-live1234',
      epoch: cognition.epoch,
    }), true);
    const session = store.getGlobalSession();
    assert.equal(session.sessionReady, false);
    assert.equal(session.sessionId, 'sess-live1234');
    assert.equal(session.syncedThroughSeq, 4);
  });

  it('lets a delta catch-up skip the runtime own committed outbound rows', () => {
    const { epoch } = store.initializeCognition('claude', 'token-own');
    store.appendCognitionJournal({ kind: 'inbound', actor: 'ou_user123456', chatId: 'oc-chat', messageId: 'om-q1', text: 'question' });
    store.appendCognitionJournal({ kind: 'outbound', chatId: 'oc-chat', actor: 'me', text: 'own committed answer', runtimeToken: 'token-own' });
    store.appendCognitionJournal({ kind: 'outbound', chatId: 'oc-chat', actor: 'me', status: 'UNCERTAIN', text: 'own uncertain send', runtimeToken: 'token-own' });
    store.appendCognitionJournal({ kind: 'outbound', chatId: 'oc-chat', actor: 'me', text: 'other runtime send', runtimeToken: 'token-other' });
    store.appendCognitionJournal({ kind: 'control', actor: 'daemon', text: 'effort changed' });

    const all = store.cognitionJournal(epoch);
    assert.equal(all.length, 5);
    const filtered = store.cognitionJournal(epoch, Number.MAX_SAFE_INTEGER, 0, {
      excludeCommittedOutboundFrom: 'token-own',
    });
    assert.deepEqual(filtered.map((row) => row.text), [
      'question',
      'own uncertain send',
      'other runtime send',
      'effort changed',
    ]);

    // A delta that only contained the runtime's own committed sends produces no
    // prefix text at all, while still advancing the cursor.
    const head = store.cognitionJournalHead(epoch);
    const emptyDelta = cognitionHistoryPrefix(store, epoch, 2, 1);
    assert.equal(emptyDelta.text, '');
    assert.equal(emptyDelta.throughSeq, 2);
    // An unprepared context fails closed, never replaying the local ledger.
    store.getOVContext = () => undefined;
    assert.throws(() => cognitionHistoryPrefix(store, epoch, head), /OpenViking context is not prepared/);
    // Even with a complete local ledger, an OV handoff is never reconstructed from it.
  });

  it('atomically advances cognition and publishes a prepared managed session', () => {
    const before = store.initializeCognition('volc', 'volc-authority');
    store.setGlobalSession('sesn-old123456', 'old-policy', {
      runtime: 'volc',
      epoch: before.epoch,
      syncedThroughSeq: 7,
    });
    const cognition = store.commitCognitionSessionRotation({
      runtime: 'volc',
      authorityToken: 'volc-authority',
      nextAuthorityToken: 'volc-authority-next',
      sessionId: 'sesn-new123456',
      policyHash: 'new-policy',
      expectedEpoch: before.epoch,
      settings: { volc_model: 'ark' },
    });
    assert.equal(cognition.epoch, before.epoch + 1);
    assert.equal(cognition.authorityToken, 'volc-authority-next');
    assert.deepEqual(store.getGlobalSession(), {
      sessionId: 'sesn-new123456',
      policyHash: 'new-policy',
      runtime: 'volc',
      epoch: cognition.epoch,
      syncedThroughSeq: 0,
      sessionReady: true,
    });
    assert.equal(store.getSetting('volc_model'), 'ark');
    assert.throws(() => store.commitCognitionSessionRotation({
      runtime: 'volc',
      authorityToken: 'stale-authority',
      sessionId: 'sesn-phantom123',
      policyHash: 'phantom-policy',
      expectedEpoch: cognition.epoch,
    }), /authority changed/);
    assert.equal(store.getGlobalSession().sessionId, 'sesn-new123456');
  });

  it('publishes a runtime session handle only for the session it belongs to', () => {
    const before = store.initializeCognition('durable', 'durable-authority');
    store.setGlobalSession('sesn-first', 'first-policy', {
      runtime: 'durable',
      epoch: before.epoch,
      nativeSessionId: 41,
    });
    assert.equal(store.getNativeSessionId(), 41);

    store.commitCognitionSessionRotation({
      runtime: 'durable',
      authorityToken: 'durable-authority',
      sessionId: 'sesn-second',
      policyHash: 'second-policy',
      expectedEpoch: before.epoch,
      nativeSessionId: 42,
    });
    assert.equal(store.getGlobalSession().sessionId, 'sesn-second');
    assert.equal(store.getNativeSessionId(), 42);

    // A handle cannot outlive the session it was stored with: a payload naming
    // another session is ignored rather than handed to the current one.
    store.setGlobalSession('sesn-third', 'third-policy', { runtime: 'durable' });
    assert.equal(store.getNativeSessionId(), null);
    store.setGlobalSession('sesn-second', 'second-policy', { runtime: 'durable' });
    assert.equal(store.getNativeSessionId(), null);

    // A rejected rotation publishes neither half of the pair.
    assert.throws(() => store.commitCognitionSessionRotation({
      runtime: 'durable',
      authorityToken: 'stale-authority',
      sessionId: 'sesn-phantom',
      policyHash: 'phantom-policy',
      expectedEpoch: store.getCognitionState().epoch,
      nativeSessionId: 43,
    }), /authority changed/);
    assert.equal(store.getNativeSessionId(), null);

    store.clearGlobalSession();
    assert.equal(store.getNativeSessionId(), null);
  });

  it('advances accepted session context only for the current cognition owner', () => {
    const cognition = store.initializeCognition('codex', 'codex-authority');
    assert.equal(store.publishOwnedSession({
      runtime: 'codex',
      authorityToken: cognition.authorityToken,
      sessionId: 'thread-codex-active',
      policyHash: 'codex-policy',
      epoch: cognition.epoch,
      acceptedThroughSeq: 2,
    }), true);
    assert.equal(store.advanceOwnedSessionAcceptance({
      runtime: 'codex',
      authorityToken: cognition.authorityToken,
      sessionId: 'thread-codex-active',
      epoch: cognition.epoch,
      acceptedThroughSeq: 7,
    }), true);
    assert.equal(store.getGlobalSession().syncedThroughSeq, 7);

    store.commitRuntimeSwitch({
      runtime: 'claude',
      authorityToken: 'claude-authority',
      sessionId: 'session-claude-active',
      policyHash: 'claude-policy',
      epoch: cognition.epoch,
      expectedRuntime: cognition.runtime,
      expectedAuthorityToken: cognition.authorityToken,
    });
    assert.equal(store.advanceOwnedSessionAcceptance({
      runtime: 'codex',
      authorityToken: cognition.authorityToken,
      sessionId: 'thread-codex-active',
      epoch: cognition.epoch,
      acceptedThroughSeq: 11,
    }), false);
    assert.equal(store.publishOwnedSession({
      runtime: 'codex',
      authorityToken: cognition.authorityToken,
      sessionId: 'thread-codex-late',
      policyHash: 'codex-policy',
      epoch: cognition.epoch,
      acceptedThroughSeq: 11,
    }), false);
    assert.deepEqual(store.getGlobalSession(), {
      sessionId: 'session-claude-active',
      policyHash: 'claude-policy',
      runtime: 'claude',
      epoch: cognition.epoch,
      syncedThroughSeq: 0,
      sessionReady: true,
    });
  });

  it('rejects a runtime switch staged under a superseded authority token', () => {
    const cognition = store.initializeCognition('codex', 'codex-old-authority');
    store.setGlobalSession('thread-codex-active', 'codex-policy', {
      runtime: 'codex',
      epoch: cognition.epoch,
      syncedThroughSeq: 7,
    });
    store.initializeCognition('codex', 'codex-new-authority');

    assert.throws(() => store.commitRuntimeSwitch({
      runtime: 'claude',
      authorityToken: 'claude-authority',
      sessionId: 'session-claude-candidate',
      policyHash: 'claude-policy',
      epoch: cognition.epoch,
      expectedRuntime: 'codex',
      expectedAuthorityToken: 'codex-old-authority',
    }), /authority changed during runtime switch/);
    assert.deepEqual(store.getGlobalSession(), {
      sessionId: 'thread-codex-active',
      policyHash: 'codex-policy',
      runtime: 'codex',
      epoch: cognition.epoch,
      syncedThroughSeq: 7,
      sessionReady: true,
    });
  });

  it('persists the accepted managed turn fence for crash recovery', () => {
    const cognition = store.initializeCognition('volc', 'volc-authority');
    store.setGlobalSession('sesn-active1234', 'volc-policy', {
      runtime: 'volc',
      epoch: cognition.epoch,
    });
    store.beginManagedTurn({
      runtime: 'volc',
      authorityToken: cognition.authorityToken,
      sessionId: 'sesn-active1234',
      epoch: cognition.epoch,
      inboundSeqs: [7, 7, 8],
      conversationKey: 'default:tenant:chat:main',
      deliveredThroughSeq: 11,
      atMs: 1234,
    });
    assert.deepEqual(store.getManagedTurn('volc'), {
      runtime: 'volc',
      authorityToken: cognition.authorityToken,
      sessionId: 'sesn-active1234',
      epoch: cognition.epoch,
      status: 'POSTING',
      inboundSeqs: [7, 8],
      conversationKey: 'default:tenant:chat:main',
      deliveredThroughSeq: 11,
      expectedUserEventId: null,
      atMs: 1234,
    });
    store.acceptManagedTurn('volc', 'sesn-active1234', 'sevt-user1234');
    assert.equal(store.getManagedTurn('volc').status, 'ACCEPTED');
    assert.equal(store.getGlobalSession().syncedThroughSeq, 11);
    assert.equal(store.clearManagedTurn('volc', {
      sessionId: 'sesn-active1234',
      expectedUserEventId: 'wrong',
    }), false);
    assert.equal(store.clearManagedTurn('volc', {
      sessionId: 'sesn-active1234',
      expectedUserEventId: 'sevt-user1234',
    }), true);
    assert.equal(store.getManagedTurn('volc'), null);
  });


  it('rejects a managed-turn fence after its authority token is superseded', () => {
    const cognition = store.initializeCognition('volc', 'volc-old-authority');
    store.setGlobalSession('sesn-active1234', 'volc-policy', {
      runtime: 'volc',
      epoch: cognition.epoch,
    });
    store.beginManagedTurn({
      runtime: 'volc',
      authorityToken: cognition.authorityToken,
      sessionId: 'sesn-active1234',
      epoch: cognition.epoch,
      inboundSeqs: [7],
      conversationKey: 'default:tenant:chat:main',
      deliveredThroughSeq: 11,
    });
    store.initializeCognition('volc', 'volc-new-authority');

    assert.throws(
      () => store.acceptManagedTurn('volc', 'sesn-active1234', 'sevt-user1234'),
      /no longer owns/,
    );
    assert.equal(store.getManagedTurn('volc').status, 'POSTING');
    assert.equal(store.getGlobalSession().syncedThroughSeq, 0);
  });

  it('atomically rolls back managed acceptance and its cursor', () => {
    const cognition = store.initializeCognition('volc', 'volc-authority');
    store.setGlobalSession('sesn-active1234', 'volc-policy', {
      runtime: 'volc',
      epoch: cognition.epoch,
    });
    store.beginManagedTurn({
      runtime: 'volc',
      authorityToken: cognition.authorityToken,
      sessionId: 'sesn-active1234',
      epoch: cognition.epoch,
      inboundSeqs: [7],
      conversationKey: 'default:tenant:chat:main',
      deliveredThroughSeq: 11,
    });
    const statement = store.setMetaStatement;
    let writes = 0;
    store.setMetaStatement = {
      run(...args) {
        writes += 1;
        if (writes === 2) throw new Error('injected acceptance cursor failure');
        return statement.run(...args);
      },
    };

    assert.throws(
      () => store.acceptManagedTurn('volc', 'sesn-active1234', 'sevt-user1234'),
      /injected acceptance cursor failure/,
    );
    store.setMetaStatement = statement;
    assert.equal(store.getManagedTurn('volc').status, 'POSTING');
    assert.equal(store.getGlobalSession().syncedThroughSeq, 0);
  });

  it('quarantines ambiguous sessions and atomically replaces cognition before reuse', () => {
    const before = store.initializeCognition('volc', 'volc-authority');
    store.setGlobalSession('sesn-ambiguous12', 'volc-policy', {
      runtime: 'volc',
      epoch: before.epoch,
      syncedThroughSeq: 9,
    });
    assert.equal(store.poisonManagedSession(
      'volc',
      'sesn-ambiguous12',
      before.epoch,
      1234,
    ), true);
    assert.deepEqual(store.getManagedSessionPoison('volc'), {
      runtime: 'volc',
      sessionId: 'sesn-ambiguous12',
      epoch: before.epoch,
      atMs: 1234,
    });
    const after = store.replacePoisonedManagedSession({
      runtime: 'volc',
      authorityToken: 'volc-authority',
      nextAuthorityToken: 'replacement-authority',
      expectedSessionId: 'sesn-ambiguous12',
      sessionId: 'sesn-clean123456',
      policyHash: 'volc-policy',
      expectedEpoch: before.epoch,
    });
    assert.deepEqual(after, {
      runtime: 'volc',
      epoch: before.epoch + 1,
      authorityToken: 'replacement-authority',
    });
    assert.equal(store.getManagedSessionPoison('volc'), null);
    assert.equal(store.getGlobalSession().sessionId, 'sesn-clean123456');
    assert.equal(store.getGlobalSession().syncedThroughSeq, 0);
  });

  it('prunes old inbound but keeps the append-only activity ledger', () => {
    store.ingest(event('old', 'old body'), null, 1_000);
    const before = store.listActivities({ limit: 100 }).activities;
    const removed = store.prune(1_000, 10_000);
    assert.ok(removed.inbound > 0);
    assert.equal(store.stats().activities, before.length);
    assert.deepEqual(
      store.listActivities({ limit: 100 }).activities.map((item) => item.id),
      before.map((item) => item.id),
    );
  });
});

describe('decision ledger', () => {
  it('adds the table to an existing database, lets a delivery override silence, and prunes old decisions', (t) => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-decisions-'));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    const file = path.join(directory, 'state.sqlite3');
    // A database from before the ledger existed.
    const old = new Store(file, quietLogger);
    old.ingest(event('kept'));
    old.db.exec('DROP TABLE decisions');
    old.close();

    const store = new Store(file, quietLogger);
    t.after(() => store.close());
    const row = store.db.prepare("SELECT * FROM connector_events WHERE event_id = 'evt-kept'").get();
    assert.equal(store.recordDecision({ row, outcome: 'silent' }, 1_000).created, true);
    assert.equal(store.recordDecision({ row, outcome: 'silent' }, 1_000).created, false);
    assert.equal(store.coveringDecision(row.scope_key, row.seq), 'silent');
    assert.equal(store.coveringDecision(row.scope_key, row.seq + 1), null);
    const upgraded = store.recordDecision({ row, outcome: 'delivered' }, 2_000);
    assert.deepEqual([upgraded.created, upgraded.previous, upgraded.row.outcome], [true, 'silent', 'delivered']);
    assert.equal(store.recordDecision({ row, outcome: 'silent' }, 3_000).created, false);
    assert.equal(store.coveringDecision(row.scope_key, row.seq), 'delivered');

    assert.equal(store.prune(1_000, 2_500).decisions, 0);
    assert.equal(store.prune(1_000, 3_500).decisions, 1);
    assert.equal(store.decisionFor('lark', 'evt-kept'), null);
  });
});
