// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { it } from 'node:test';
import { Store } from '../src/store.mjs';
import { normalizeEvent, deterministicFilter } from '../src/event.mjs';
import { normalizeLarkMessage, normalizeLarkEvent } from '../src/connectors/lark-events.mjs';
import { resolveLarkMessageEvent } from '../src/lark-message-events.mjs';
import { formatInboundLine } from '../src/runtime-input.mjs';

const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };
const config = { maxEventAgeMs: 600_000, ignoredSenderIds: new Set() };
const chatId = 'oc_reacttest123456';
const userId = 'ou_reactor1234567';
const appId = 'cli_receiver123456';
const peerAppId = 'cli_peer123456789';
const actors = [['user', userId], ['app', peerAppId]];
const target = 'om_target12345678';

function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-message-events-'));
  const store = new Store(path.join(directory, 'state.sqlite3'), logger);
  t.after(() => { store.close(); fs.rmSync(directory, { recursive: true, force: true }); });
  store.ingest(normalizeLarkMessage({ type: 'im.message.receive_v1', event_id: 'evt-target', message_id: target,
    chat_id: chatId, chat_type: 'group', message_type: 'text', sender_id: userId, sender_type: 'user',
    content: 'hello', thread_id: 'omt_thread1234567', root_id: 'om_root123456789' }));
  const resolve = (raw) => resolveLarkMessageEvent(store.db, normalizeEvent(normalizeLarkEvent(raw)));
  return { store, resolve };
}

const mine = 'om_mine1234567890';
// The bot's own reply in the target's thread, as the journal records it.
function botSent(store, { messageId = mine, threadId = 'omt_thread1234567' } = {}) {
  store.db.prepare(`INSERT INTO cognition_journal (epoch, kind, at_ms, chat_id, thread_id, message_id, actor, text, detail_json)
    VALUES (0, 'outbound', ?, ?, ?, ?, 'me', 'hi', '{"connectorInstance":"lark"}')`).run(Date.now(), chatId, threadId, messageId);
}

const reaction = (overrides = {}, operatorType = 'user') => ({
  header: { event_id: 'evt-reaction', event_type: 'im.message.reaction.created_v1', app_id: appId, create_time: String(Date.now()) },
  event: { message_id: target, reaction_type: { emoji_type: 'THUMBSUP' }, operator_type: operatorType,
    ...(operatorType === 'app' ? { app_id: peerAppId } : { user_id: { open_id: userId } }),
    action_time: String(Date.now()), ...overrides },
});

for (const [senderType, senderId] of actors) it(`places reactions from ${senderType} actors on the bot's message in that message's thread`, (t) => {
  const { store, resolve } = fixture(t);
  botSent(store);
  const event = resolve(reaction({ message_id: mine }, senderType));
  assert.equal(deterministicFilter(event, config), null);
  assert.equal(event.chatId, chatId);
  assert.equal(event.threadId, 'omt_thread1234567');
  assert.equal(event.parentId, mine);
  assert.match(event.messageId, /^re_/);
  assert.equal(event.senderId, senderId);
  assert.equal(event.senderType, senderType);
  assert.deepEqual(event.replyRoute, { action: 'message.reply', input: { message_id: mine } });
  const line = formatInboundLine(event);
  assert.match(line, /^\[chat=oc_reacttest123456 thread=omt_thread1234567 source=lark msg=re_\w+ event=evt-reaction target=om_mine1234567890 /);
  assert.match(line, /\] \[reaction THUMBSUP\]$/);
});

for (const [senderType] of actors) it(`accepts a removed ${senderType} reaction`, (t) => {
  const { store, resolve } = fixture(t);
  botSent(store);
  const raw = reaction({ message_id: mine }, senderType);
  raw.header.event_type = 'im.message.reaction.deleted_v1';
  const event = resolve(raw);
  assert.equal(deterministicFilter(event, config), null);
  assert.equal(event.content, '[removed reaction THUMBSUP]');
});

it('places a recall and credits its author', (t) => {
  const { resolve } = fixture(t);
  const event = resolve({ header: { event_id: 'evt-recall', event_type: 'im.message.recalled_v1' },
    event: { message_id: target, chat_id: chatId, recall_time: String(Date.now()), recall_type: 'message_owner' } });
  assert.equal(deterministicFilter(event, config), null);
  assert.equal(event.threadId, 'omt_thread1234567');
  assert.equal(event.senderId, userId);
  assert.equal(event.replyRoute, null);
  assert.match(event.messageId, /^rc_/);
  assert.equal(event.content, '[recalled a message]');
});

it('drops the bot\'s own reactions, reactions on someone else\'s message, and events about unknown messages', (t) => {
  const { store, resolve } = fixture(t);
  botSent(store);
  assert.equal(deterministicFilter(resolve(reaction()), config), 'unsupported_message_type',
    'a reaction on a person\'s message is theirs to read, not a request to the Agent');
  assert.equal(deterministicFilter(resolve(reaction({ message_id: mine, app_id: appId }, 'app')), config),
    'unsupported_message_type');
  assert.equal(deterministicFilter(resolve(reaction({ message_id: 'om_unknown1234567' })), config),
    'unsupported_message_type');
  for (const missing of ['operator', 'receiver']) {
    const raw = reaction({ message_id: mine }, 'app');
    if (missing === 'operator') delete raw.event.app_id;
    else delete raw.header.app_id;
    assert.equal(deterministicFilter(resolve(raw), config), 'unsupported_message_type',
      'an app without both identities cannot be distinguished from the bot itself');
  }
});

it('places a reaction on the bot\'s own message and drops the recall of one', (t) => {
  const { store, resolve } = fixture(t);
  botSent(store, { threadId: null });
  const event = resolve(reaction({ message_id: mine }));
  assert.equal(deterministicFilter(event, config), null);
  assert.equal(event.chatId, chatId);
  assert.equal(event.threadId, null);
  assert.equal(deterministicFilter(resolve({ header: { event_id: 'evt-recall-mine', event_type: 'im.message.recalled_v1' },
    event: { message_id: 'om_mine1234567890', chat_id: chatId, recall_type: 'message_owner' } }), config), 'unsupported_message_type');
});

for (const [senderType] of actors) it(`takes reactions from ${senderType} actors only on the bot's newest message in that thread`, (t) => {
  const { store, resolve } = fixture(t);
  botSent(store);
  botSent(store, { messageId: 'om_mineLater12345' });
  botSent(store, { messageId: 'om_mineElsewhere1', threadId: 'omt_other12345678' });
  assert.equal(deterministicFilter(resolve(reaction({ message_id: mine }, senderType)), config), 'unsupported_message_type',
    'an older message of the bot\'s is not where people answer it');
  assert.equal(deterministicFilter(resolve(reaction({ message_id: 'om_mineLater12345' }, senderType)), config), null);
  assert.equal(deterministicFilter(resolve(reaction({ message_id: 'om_mineElsewhere1' }, senderType)), config), null,
    'each thread has its own newest message');
});

for (const [senderType] of actors) it(`reactions from ${senderType} actors never make a reply stale; a recall does`, (t) => {
  const { store, resolve } = fixture(t);
  const target = store.db.prepare("SELECT seq, scope_key FROM connector_events WHERE event_id = 'evt-target'").get();
  botSent(store);
  const event = resolve(reaction({ message_id: mine }, senderType));
  assert.equal(deterministicFilter(event, config), null);
  const placed = store.ingest(event);
  assert.equal(placed.inbound.scopeKey, target.scope_key, 'the reaction lands in the same thread');
  assert.equal(placed.filterReason, null);
  assert.equal(store.newerAcceptedSeq(target.scope_key, target.seq), null);
  store.ingest(resolve({ header: { event_id: 'evt-recall-2', event_type: 'im.message.recalled_v1' },
    event: { message_id: 'om_target12345678', chat_id: 'oc_reacttest123456', recall_time: String(Date.now()), recall_type: 'message_owner' } }));
  assert.notEqual(store.newerAcceptedSeq(target.scope_key, target.seq), null);
});
