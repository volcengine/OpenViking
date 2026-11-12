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

const reaction = (overrides = {}) => ({
  header: { event_id: 'evt-reaction', event_type: 'im.message.reaction.created_v1', create_time: String(Date.now()) },
  event: { message_id: target, reaction_type: { emoji_type: 'THUMBSUP' }, operator_type: 'user',
    user_id: { open_id: userId }, action_time: String(Date.now()), ...overrides },
});

it('places a reaction in the thread of the message it is on', (t) => {
  const { resolve } = fixture(t);
  const event = resolve(reaction());
  assert.equal(deterministicFilter(event, config), null);
  assert.equal(event.chatId, chatId);
  assert.equal(event.threadId, 'omt_thread1234567');
  assert.equal(event.parentId, target);
  assert.match(event.messageId, /^re_/);
  assert.equal(event.senderId, userId);
  assert.deepEqual(event.replyRoute, { action: 'message.reply', input: { message_id: target } });
  const line = formatInboundLine(event);
  assert.match(line, /^\[chat=oc_reacttest123456 thread=omt_thread1234567 source=lark msg=re_\w+ event=evt-reaction target=om_target12345678 /);
  assert.match(line, /\] \[reaction THUMBSUP\]$/);
});

it('names a removed reaction', (t) => {
  const { resolve } = fixture(t);
  const raw = reaction();
  raw.header.event_type = 'im.message.reaction.deleted_v1';
  assert.equal(resolve(raw).content, '[removed reaction THUMBSUP]');
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

it('drops the bot\'s own reactions and events about unknown messages', (t) => {
  const { resolve } = fixture(t);
  assert.equal(deterministicFilter(resolve(reaction({ operator_type: 'app', user_id: undefined, app_id: 'cli_x' })), config),
    'unsupported_message_type');
  assert.equal(deterministicFilter(resolve(reaction({ message_id: 'om_unknown1234567' })), config),
    'unsupported_message_type');
});

it('places a reaction on the bot\'s own message and drops the recall of one', (t) => {
  const { store, resolve } = fixture(t);
  store.db.prepare(`INSERT INTO cognition_journal (epoch, kind, at_ms, chat_id, thread_id, message_id, actor, text, detail_json)
    VALUES (0, 'outbound', ?, ?, NULL, 'om_mine1234567890', 'me', 'hi', '{"connectorInstance":"lark"}')`).run(Date.now(), chatId);
  const event = resolve(reaction({ message_id: 'om_mine1234567890' }));
  assert.equal(deterministicFilter(event, config), null);
  assert.equal(event.chatId, chatId);
  assert.equal(event.threadId, null);
  assert.equal(deterministicFilter(resolve({ header: { event_id: 'evt-recall-mine', event_type: 'im.message.recalled_v1' },
    event: { message_id: 'om_mine1234567890', chat_id: chatId, recall_type: 'message_owner' } }), config), 'unsupported_message_type');
});

it('a reaction never makes a reply stale; a recall does', (t) => {
  const { store, resolve } = fixture(t);
  const target = store.db.prepare("SELECT seq, scope_key FROM connector_events WHERE event_id = 'evt-target'").get();
  store.ingest(resolve(reaction()));
  assert.equal(store.newerAcceptedSeq(target.scope_key, target.seq), null);
  store.ingest(resolve({ header: { event_id: 'evt-recall-2', event_type: 'im.message.recalled_v1' },
    event: { message_id: 'om_target12345678', chat_id: 'oc_reacttest123456', recall_time: String(Date.now()), recall_type: 'message_owner' } }));
  assert.notEqual(store.newerAcceptedSeq(target.scope_key, target.seq), null);
});
