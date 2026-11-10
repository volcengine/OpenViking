// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { it } from 'node:test';
import { Store } from '../src/store.mjs';
import { ActionService } from '../src/actions/service.mjs';
import { MAX_FILE_BYTES } from '../src/actions/content.mjs';
import { Harness } from '../src/harness.mjs';
import { normalizeEvent as normalizeConnectorEvent, deterministicFilter } from '../src/event.mjs';
import { normalizeLarkMessage, normalizeLarkEvent } from '../src/connectors/lark-events.mjs';
import { resolveCardAction, settledChoiceCard } from '../src/card-messages.mjs';
import { fakeConnectors, larkConnectors, larkSuccess as success } from './action-fixture.mjs';
import { chatEvent } from './event-fixture.mjs';

const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };
const profile = 'test-profile';
const chatId = 'oc_cardtest123456';
const userId = 'ou_answerer123456';
const decisionId = 'om_question123456';
const decisionEvent = 'evt-question';
const remoteId = 'om_delivered123456';
const authority = 'runtime-test-token';
const question = { prompt: '选择实现方案', options: [{ key: 'small', label: '小改动' }, { key: 'full', label: '完整实现' }] };
const config = { profile, maxEventBytes: 100_000, maxEventAgeMs: 600_000, ignoredSenderIds: new Set() };
const normalizeEvent = (raw) => normalizeLarkMessage(raw);
const normalizeCardAction = (db, raw, selectedProfile) => resolveCardAction(db,
  normalizeConnectorEvent(normalizeLarkEvent(raw)), selectedProfile);

function fixture(t, { thread = false, run } = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-card-test-'));
  const workspace = path.join(directory, 'workspace');
  const tempDir = path.join(directory, 'tmp');
  fs.mkdirSync(workspace);
  const databasePath = path.join(directory, 'state.sqlite3');
  const store = new Store(databasePath, logger);
  store.initializeCognition('codex', authority);
  store.ingest(normalizeEvent({ type: 'im.message.receive_v1', event_id: decisionEvent,
    message_id: decisionId, chat_id: chatId, chat_type: 'group', message_type: 'text',
    sender_id: userId, sender_type: 'user', content: '做个选择',
    thread_id: thread ? 'omt_thread123456' : undefined,
    root_id: thread ? 'om_rootcard123456' : undefined,
  }, profile));
  const connectors = larkConnectors({ profile, tempDir, run: (bin, args, options) => (run
    ? run(bin, args, options)
    : success({ message_id: remoteId, chat_id: chatId,
      ...(thread ? { thread_id: 'omt_thread123456', root_id: 'om_rootcard123456' } : {}) })) });
  const service = new ActionService({
    config: { profile, workspace, tempDir, maxReplyChars: 30_000 }, store, connectors, logger,
  });
  const call = (tool, input) => service.call(tool, input, { authority });
  t.after(() => { store.close(); fs.rmSync(directory, { recursive: true, force: true }); });
  return { directory, workspace, tempDir, databasePath, store, service, call, calls: connectors.calls };
}

function callback(overrides = {}) {
  return { type: 'card.action.trigger', event_id: 'evt-click-1', message_id: remoteId,
    chat_id: chatId, operator_id: userId, timestamp: String(Date.now()),
    token: 'must-never-enter-model-or-db', action_tag: 'button',
    action_value: JSON.stringify({ vika_choice: 'full' }), ...overrides };
}

it('delivers received cards even when Lark omits or blanks their content', () => {
  for (const content of [undefined, null, '', '   ', '{}']) {
    const event = normalizeEvent({ type: 'im.message.receive_v1', event_id: 'evt-empty-card',
      message_id: remoteId, chat_id: chatId, chat_type: 'group', message_type: 'interactive',
      sender_id: userId, sender_type: 'user', content }, profile);
    assert.equal(event.content, '[card]');
    assert.equal(deterministicFilter(event, config), null);
  }
});

it('sends a document snapshot from the workspace into the original thread and cleans it up', async (t) => {
  let staged;
  const f = fixture(t, { thread: true, run: (_bin, args, options) => {
    const relative = args[args.indexOf('--file') + 1];
    staged = path.join(options.cwd, relative);
    assert.equal(fs.readFileSync(staged, 'utf8'), 'document bytes');
    assert.ok(args.includes('--reply-in-thread'));
    assert.ok(!args.includes('--markdown'));
    return success({ message_id: remoteId });
  } });
  fs.writeFileSync(path.join(f.workspace, '报告.pdf'), 'document bytes');
  const input = { message_id: decisionId, decision_id: decisionEvent, reply_in_thread: true, file_path: '报告.pdf' };
  const result = await f.call('reply_message', input);
  assert.equal(result.data.message_id, remoteId);
  assert.equal(fs.existsSync(staged), false);
  assert.deepEqual(fs.readdirSync(f.workspace), ['报告.pdf']);
  const again = await f.call('reply_message', input);
  assert.equal(again.data.duplicate, true);
  assert.match(again.text, /Already delivered/);
  assert.equal(f.calls.length, 1);
});

it('rejects traversal, URLs, escaped symlinks, directories, oversized files, and mixed bodies before sending', async (t) => {
  const f = fixture(t);
  fs.writeFileSync(path.join(f.directory, 'outside.pdf'), 'private');
  fs.symlinkSync(path.join(f.directory, 'outside.pdf'), path.join(f.workspace, 'link.pdf'));
  fs.mkdirSync(path.join(f.workspace, 'folder'));
  const fd = fs.openSync(path.join(f.workspace, 'large.pdf'), 'w');
  fs.ftruncateSync(fd, MAX_FILE_BYTES + 1); fs.closeSync(fd);
  for (const file_path of ['../outside.pdf', '/tmp/outside.pdf', 'https://example.com/a', 'link.pdf', 'folder', 'large.pdf']) {
    await assert.rejects(() => f.call('send_message', { chat_id: chatId, decision_id: decisionEvent, file_path }));
  }
  await assert.rejects(() => f.call('send_message', { chat_id: chatId, decision_id: decisionEvent,
    markdown: 'hi', file_key: 'file_existing123456' }), /exactly one of/);
  assert.equal(f.calls.length, 0);
});

it('supports existing file keys, dry runs, and cleanup after an uncertain upload/send', async (t) => {
  const f = fixture(t, { run: () => { throw new Error('network lost'); } });
  f.service.config.dryRun = true;
  assert.equal((await f.call('send_message', { chat_id: chatId, decision_id: decisionEvent,
    file_key: 'file_existing123456' })).data.dry_run, true);
  assert.equal(f.calls.length, 0);
  f.service.config.dryRun = false;
  fs.writeFileSync(path.join(f.workspace, 'report.docx'), 'report');
  await assert.rejects(() => f.call('send_message', { chat_id: chatId, decision_id: decisionEvent,
    file_path: 'report.docx' }), /network lost/);
  assert.deepEqual(fs.readdirSync(f.workspace), ['report.docx']);
  assert.deepEqual(fs.readdirSync(path.join(f.tempDir, 'outbox')), []);
  assert.equal(f.store.db.prepare("SELECT status FROM cognition_journal WHERE kind='outbound'").get().status, 'UNCERTAIN');
  assert.deepEqual(f.store.stats().outbox, { UNCERTAIN: 1 });
});

it('sends a choice card and feeds a click to the agent with durable routing and a replyable card id', async (t) => {
  const f = fixture(t, { thread: true });
  const result = await f.call('reply_message', { message_id: decisionId, decision_id: decisionEvent,
    reply_in_thread: true, choices: question });
  assert.equal(result.data.message_id, remoteId);
  assert.equal(result.data.delivery_warning, undefined);
  const sent = f.calls[0].args;
  assert.equal(sent[sent.indexOf('--msg-type') + 1], 'interactive');
  const card = JSON.parse(sent[sent.indexOf('--content') + 1]);
  assert.equal(card.elements[1].actions[1].value.vika_choice, 'full');
  // Reopen the database to prove this is not a process-local callback map.
  const reopened = new Store(f.databasePath, logger);
  t.after(() => reopened.close());
  const input = [];
  const updates = [];
  const harness = new Harness({ config, store: reopened, logger, recordActivity() {},
    runtime: { snapshot: () => ({ session: { connected: true }, work: { turnInProgress: true } }),
      submit: (items) => input.push(...items) },
    onSlashCommand: () => assert.fail('card clicks must not become slash commands'),
    prepareEvent: (event) => resolveCardAction(reopened.db, event, profile),
    afterEvent: (event) => updates.push([event.parentId, settledChoiceCard(event)]),
  });
  harness.handleConnectorEvent(normalizeLarkEvent(callback()));
  await harness.ingressBarrier;
  assert.equal(input.length, 1);
  assert.match(input[0].text, /msg=ca_/);
  assert.match(input[0].text, new RegExp(`card=${remoteId}`));
  assert.match(input[0].text, /reply_to=me/);
  assert.match(input[0].text, /"key":"full"/);
  assert.equal(input[0].target.threadId, 'omt_thread123456');
  assert.equal(updates.length, 1);
  assert.ok(!JSON.stringify(input).includes('must-never'));
  assert.ok(!JSON.stringify(reopened.db.prepare('SELECT * FROM connector_events').all()).includes('must-never'));
  harness.handleConnectorEvent(normalizeLarkEvent(callback()));
  harness.handleConnectorEvent(normalizeLarkEvent(callback({ event_id: 'evt-click-2' })));
  await harness.ingressBarrier;
  assert.equal(input.length, 1);
  const inbound = reopened.inboundBySeq(input[0].seq);
  assert.equal(f.service.fence({ decision_id: inbound.eventId }, { trace: {} }).message_id, inbound.messageId);
  await f.call('reply_message', { message_id: remoteId, decision_id: inbound.eventId,
    reply_in_thread: true, markdown: '继续执行完整实现' });
  const replyArgs = f.calls.at(-1).args;
  assert.equal(replyArgs[replyArgs.indexOf('--message-id') + 1], remoteId);
  assert.equal((await f.service.replyToEvent(inbound, 'runtime failed', 'failure-notice')).ok, true);
  const noticeArgs = f.calls.at(-1).args;
  assert.equal(noticeArgs[noticeArgs.indexOf('--message-id') + 1], remoteId);
  const cancel = normalizeLarkMessage({ type: 'im.message.receive_v1', event_id: 'evt-cancel',
    message_id: 'om_cancelled123456', chat_id: chatId, chat_type: 'group', message_type: 'text',
    sender_id: userId, sender_type: 'user', content: '取消执行',
    thread_id: 'omt_thread123456', root_id: 'om_rootcard123456',
  });
  assert.equal(cancel.conversationKey, inbound.conversationKey);
  reopened.ingest(cancel);
  assert.throws(() => f.service.fence({ decision_id: inbound.eventId }, { trace: {} }), /newer authenticated input/);
  harness.stop();
});

it('rejects unknown cards, wrong app/chat/user, invalid options, expired questions and ignored senders', async (t) => {
  const f = fixture(t);
  await f.call('send_message', { chat_id: chatId, decision_id: decisionEvent, choices: question });
  for (const changes of [
    { message_id: 'om_unknown123456' }, { chat_id: 'oc_otherchat1234' }, { operator_id: 'ou_otheruser1234' },
    { action_value: '{"vika_choice":"forged"}' },
  ]) assert.throws(() => normalizeCardAction(f.store.db, callback(changes), profile));
  assert.throws(() => normalizeCardAction(f.store.db, callback(), 'other-profile'));
  const event = normalizeCardAction(f.store.db, callback(), profile);
  const ignored = deterministicFilter(event, { ...config, ignoredSenderIds: new Set([userId]) });
  f.store.ingest(event, ignored);
  assert.equal(f.store.db.prepare('SELECT answer_event_id FROM lark_cards').get().answer_event_id, null);
  f.store.db.prepare('UPDATE lark_cards SET expires_at_ms = 0').run();
  const expired = normalizeCardAction(f.store.db, callback({ event_id: 'evt-expired' }), profile);
  assert.equal(f.store.ingest(expired).filterReason, 'card_already_answered_or_expired');
});

it('resolves a newly created thread from the delivered card before accepting callbacks', async (t) => {
  const f = fixture(t, { run: (_bin, args) => args.includes('+messages-mget')
    ? success({ items: [{ message_id: remoteId, chat_id: chatId,
      thread_id: 'omt_newthread12345', root_id: decisionId }] })
    : success({ message_id: remoteId }) });
  const result = await f.call('reply_message', { message_id: decisionId, decision_id: decisionEvent,
    reply_in_thread: true, choices: question });
  assert.equal(result.data.delivery_warning, undefined);
  assert.equal(f.calls.length, 2);
  assert.ok(f.calls[1].args.includes('--no-reactions'));
  const event = normalizeCardAction(f.store.db, callback(), profile);
  assert.equal(event.threadId, 'omt_newthread12345');
  assert.equal(event.threadRootId, decisionId);
  assert.equal(JSON.parse(event.conversationKey).at(-1), 'omt_newthread12345');
});

it('reports a sent card with failed callback registration without inviting a duplicate send', async (t) => {
  const f = fixture(t, { run: (_bin, args) => {
    if (args.includes('+messages-mget')) throw new Error('lookup failed');
    return success({ message_id: remoteId });
  } });
  const args = { message_id: decisionId, decision_id: decisionEvent,
    reply_in_thread: true, choices: question };
  const result = await f.call('reply_message', args);
  assert.equal(result.data.message_id, remoteId);
  assert.match(result.data.delivery_warning, /Do not resend/);
  assert.match(result.text, /Do not resend/);
  assert.equal(f.store.db.prepare("SELECT status FROM cognition_journal WHERE kind='outbound'").get().status, 'COMMITTED');
  assert.throws(() => normalizeCardAction(f.store.db, callback(), profile), /not sent through/);
  assert.equal((await f.call('reply_message', args)).data.duplicate, true);
  assert.equal(f.calls.length, 2);
});

it('accepts raw SDK form callbacks without leaking tokens and keeps separate actions on one card', async (t) => {
  const f = fixture(t);
  await f.call('send_message', { chat_id: chatId, decision_id: decisionEvent,
    card: { schema: '2.0', body: { elements: [{ tag: 'markdown', content: 'Form' }] } } });
  for (const id of ['raw-one', 'raw-two']) {
    const event = normalizeCardAction(f.store.db, {
      header: { event_type: 'card.action.trigger', event_id: id, create_time: String(Date.now()) },
      event: { operator: { open_id: userId }, context: { open_message_id: remoteId, open_chat_id: chatId },
        token: 'private-token', action: { tag: 'button', value: { action: 'example' }, form_value: { input: 'hello' } } },
    }, profile);
    assert.match(event.content, /hello/);
    assert.ok(!JSON.stringify(event).includes('private-token'));
    assert.equal(f.store.ingest(event).inserted, true);
  }
});

it('registers a cross-channel card where it landed, not where the decision came from', async (t) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-card-test-'));
  const workspace = path.join(directory, 'workspace');
  fs.mkdirSync(workspace);
  const store = new Store(path.join(directory, 'state.sqlite3'), logger);
  t.after(() => { store.close(); fs.rmSync(directory, { recursive: true, force: true }); });
  store.initializeCognition('pi', authority);
  const groupMessage = normalizeEvent({ type: 'im.message.receive_v1', event_id: 'evt-group',
    message_id: 'om_groupmsg123456', chat_id: chatId, chat_type: 'group', message_type: 'text',
    sender_id: userId, sender_type: 'user', content: '群里的消息', tenant_key: 'tenant_lark' }, profile);
  store.ingest(groupMessage);
  store.ingest(chatEvent({ source: 'weixin', messageId: 'wx-decision-0001', chatId: 'wxuser@im.wechat',
    tenantKey: 'wxbot@im.bot', senderId: 'wxuser@im.wechat' }));
  const delivered = ['om_crosscard12345', 'om_unseencard1234'];
  const connectors = fakeConnectors({ ids: ['lark', 'weixin'],
    run: (_connector, _action, input) => ({ message_id: delivered.shift(), chat_id: input.chat_id }) });
  const service = new ActionService({ config: { profile, workspace, tempDir: path.join(directory, 'tmp'),
    maxReplyChars: 30_000 }, store, connectors, logger });
  const send = (chat) => service.call('send_message', { source: 'lark', chat_id: chat,
    decision_id: 'message:wx-decision-0001', card: { elements: [{ tag: 'markdown', content: 'vote' }] } }, { authority });

  assert.equal((await send(chatId)).data.delivery_warning, undefined);
  const click = normalizeCardAction(store.db, callback({ message_id: 'om_crosscard12345' }), profile);
  assert.equal(click.connectorInstance, 'lark');
  assert.equal(click.conversationKey, groupMessage.conversationKey, 'clicks share the group conversation');

  // A chat the bot never heard from still gets the Lark tenant, never Weixin's.
  await send('oc_unseenchat1234');
  const scope = store.db.prepare('SELECT scope_json FROM lark_cards WHERE message_id = ?').get('om_unseencard1234');
  assert.deepEqual(JSON.parse(scope.scope_json), { instance: 'lark', threadRootId: null,
    conversation: { id: 'oc_unseenchat1234', type: 'group', tenant_id: 'tenant_lark', thread_id: null } });
});

it('validates card structure, size, mentions and choice keys without invoking the CLI', async (t) => {
  const f = fixture(t);
  for (const body of [
    { card: { type: 'template' } },
    { card: { elements: [{ tag: 'markdown', content: '<at id=all></at>' }] } },
    { card: { elements: [{ tag: 'markdown', content: 'x'.repeat(40000) }] } },
    { choices: { ...question, options: [question.options[0], question.options[0]] } },
    { choices: { ...question, timeout_seconds: 1 } },
  ]) await assert.rejects(() => f.call('send_message', { chat_id: chatId, decision_id: decisionEvent, ...body }));
  assert.equal(f.calls.length, 0);
});
