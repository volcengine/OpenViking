// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, it } from 'node:test';
import { ActionService } from '../src/actions/service.mjs';
import { normalizeEvent } from '../src/event.mjs';
import { Harness } from '../src/harness.mjs';
import { LarkClient } from '../src/lark.mjs';
import {
  commentContent, commentPlainText, isDocCommentEvent, normalizeDocComment, parseCommentMessageId, parseCommentNotice,
} from '../src/lark-doc-comments.mjs';
import { formatInboundLine } from '../src/runtime-input.mjs';
import { Store } from '../src/store.mjs';
import { larkConnectors, larkSuccess } from './action-fixture.mjs';

const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };
const SELF = 'ou_bot000000001';
const ALICE = 'ou_alice0000001';
const BOB = 'ou_bob000000001';
const TOKEN = 'TLLKdcpDro9ijQxA33ycNMabcef';
const MSG = `comment:docx:${TOKEN}:7618859513273112345:7618859513273119999`;

// The comment event as the long connection delivers it (Lark's own example).
function notice({ replyId = '7618859513273119999', from = ALICE, mentioned = true, ...rest } = {}) {
  return {
    schema: '2.0',
    header: { event_id: '5e3702a84e847582be8db7fb73283c02', event_type: 'drive.notice.comment_add_v1',
      create_time: String(Date.now()), token: 'rvaY', app_id: 'cli_9f5343c580712544', tenant_key: '2ca1d211f64f6438' },
    event: {
      notice_meta: { file_type: 'docx', file_token: TOKEN, from_user_id: { open_id: from, union_id: 'on_x', user_id: 'e33' },
        to_user_id: { open_id: SELF }, notice_type: replyId ? 'add_reply' : 'add_comment' },
      comment_id: '7618859513273112345', ...(replyId ? { reply_id: replyId } : {}), is_mentioned: mentioned, ...rest,
    },
  };
}

const textRun = (text) => ({ type: 'text_run', text_run: { text } });
const person = (id) => ({ type: 'person', person: { user_id: id } });
const reply = (id, user, elements) => ({ reply_id: id, user_id: user, create_time: 1610281603, content: { elements } });

function thread() {
  return [
    reply('7618859513273110001', BOB, [textRun('这个数对吗？')]),
    reply('7618859513273110002', SELF, [textRun('对的，来自 Q3 报表。')]),
    reply('7618859513273119999', ALICE, [person(SELF), textRun(' 帮我把这条加到 '), person(BOB), textRun(' 的任务')]),
  ];
}

describe('document comment events', () => {
  it('reads the ids of a comment notice, enveloped or flattened, and refuses one without them', () => {
    assert.equal(isDocCommentEvent(notice()), true);
    assert.equal(isDocCommentEvent({ header: { event_type: 'im.message.receive_v1' } }), false);
    const parsed = parseCommentNotice(notice());
    assert.deepEqual({ ...parsed, createTimeMs: typeof parsed.createTimeMs }, {
      eventId: '5e3702a84e847582be8db7fb73283c02', createTimeMs: 'number', tenantKey: '2ca1d211f64f6438',
      fileType: 'docx', fileToken: TOKEN, commentId: '7618859513273112345', replyId: '7618859513273119999',
      fromId: ALICE, mentioned: true,
    });
    const flat = parseCommentNotice({ type: 'drive.notice.comment_add_v1', file_type: 'sheet', file_token: 'shtTok',
      comment_id: '1', user_id: { open_id: ALICE }, timestamp: '1700000000' });
    assert.deepEqual([flat.fileType, flat.fileToken, flat.replyId, flat.createTimeMs], ['sheet', 'shtTok', null, 1_700_000_000_000]);
    assert.throws(() => parseCommentNotice(notice({ comment_id: '../x' })), /invalid document comment event/);
    assert.throws(() => parseCommentNotice(notice({ from: 'someone' })), /names no commenter/);
  });

  it('becomes a chat message: the document a chat, the comment its thread, with the thread shown before the reply', () => {
    const names = new Map([[ALICE, 'Alice'], [BOB, 'Bob']]);
    const envelope = normalizeDocComment(parseCommentNotice(notice()), {
      comment: { quote: '本季度  目标\n是 100', is_whole: false }, replies: thread(),
      title: 'Q4 计划', url: 'https://example.feishu.cn/docx/TLLK', names,
    }, { selfId: SELF, selfName: 'Vika' });
    const event = normalizeEvent(envelope);
    assert.deepEqual([event.kind, event.chatId, event.chatType, event.threadId, event.messageId], [
      'message.created', `doc:docx:${TOKEN}`, 'doc_comment', '7618859513273112345', MSG,
    ]);
    assert.deepEqual(event.actor, { id: ALICE, type: 'user', name: 'Alice' });
    assert.equal(event.title, '[document "Q4 计划" https://example.feishu.cn/docx/TLLK]');
    assert.equal(event.content, [
      '[on the text: "本季度 目标 是 100"]',
      '[earlier in this comment: Bob: 这个数对吗？ | you: 对的，来自 Q3 报表。]',
      '@Vika 帮我把这条加到 @Bob 的任务',
    ].join('\n'));
    assert.deepEqual(event.mentions.map(({ id, name }) => [id, name]), [[SELF, 'Vika'], [BOB, 'Bob']]);
    // Right after the bot's own reply: shown as a reply to it.
    assert.equal(event.parentId, `comment:docx:${TOKEN}:7618859513273112345:7618859513273110002`);
    assert.deepEqual(event.replyRoute, { action: 'message.reply', input: { message_id: MSG } });
    assert.match(formatInboundLine(event, null, { mention: 'mixed', replyTo: { kind: 'me', text: '对的，来自 Q3 报表。' } }),
      new RegExp(`^\\[chat=doc:docx:${TOKEN} thread=76188595132 source=lark msg=${MSG} event=5e3702a84e847582be8db7fb73283c02 `
        + 'time=\\S+ from=Alice reply_to=me quote=对的，来自_Q3_报表。 mention=mixed\\] \\[document "Q4 计划" '));
  });

  it('still wakes the bot when the comment cannot be read, and keeps a long thread to its first comment and last replies', () => {
    const unreadable = normalizeEvent(normalizeDocComment(parseCommentNotice(notice()), { error: true }, { selfId: SELF }));
    assert.equal(unreadable.title, `[document docx ${TOKEN}]`);
    assert.match(unreadable.content, /could not be read: the document is probably not shared with the bot/);
    assert.deepEqual(unreadable.mentions.map(({ id }) => id), [SELF], 'the event says the bot was mentioned');
    assert.equal(unreadable.parentId, null);
    assert.equal(unreadable.senderName, null);

    const long = Array.from({ length: 9 }, (_, i) => reply(String(100 + i), BOB, [textRun(`reply ${i}`)]));
    const event = normalizeEvent(normalizeDocComment(parseCommentNotice(notice({ replyId: '108', mentioned: false })),
      { replies: long }, { selfId: SELF }));
    assert.match(event.content, /^\[earlier in this comment: ou_bob00000: reply 0 \| \(3 more\) \| ou_bob00000: reply 4 \| .*reply 7\]\nreply 8$/);
    assert.deepEqual(event.mentions, []);
    // A new comment (no reply id) is its first reply.
    const fresh = normalizeEvent(normalizeDocComment(parseCommentNotice(notice({ replyId: null })),
      { replies: [reply('555', ALICE, [textRun('看下这里')])] }, { selfId: SELF }));
    assert.equal(fresh.messageId, `comment:docx:${TOKEN}:7618859513273112345:555`);
    assert.equal(fresh.content, '看下这里');
  });

  it('names a comment reply by its message id, and writes plain text for a comment', () => {
    assert.deepEqual(parseCommentMessageId(MSG), { fileType: 'docx', fileToken: TOKEN,
      commentId: '7618859513273112345', replyId: '7618859513273119999' });
    assert.equal(parseCommentMessageId('om_abc'), null);
    assert.equal(parseCommentMessageId('comment:exe:a:b:c'), null);
    assert.equal(commentPlainText('# 标题\n**加粗** 和 `code`，见 [文档](https://x.feishu.cn/docx/a)\n```js\nx()\n```'),
      '标题\n加粗 和 code，见 文档 (https://x.feishu.cn/docx/a)\nx()');
    assert.deepEqual(commentContent('好的', [ALICE]), [{ type: 'mention_user', mention_user: ALICE }, { type: 'text', text: ' 好的' }]);
    assert.throws(() => commentContent('```\n```'), /needs text/);
  });

  it('shows the commenter by the name the Connector found, never fetching a member roster for a document', async (t) => {
    const store = new Store(':memory:', logger);
    const delivered = [];
    const harness = new Harness({
      config: { maxEventBytes: 100_000, maxEventAgeMs: 60_000, ignoredSenderIds: new Set() }, store, logger, recordActivity() {},
      runtime: { snapshot: () => ({ session: { connected: true }, work: { turnInProgress: false } }),
        submit: (items) => { delivered.push(...items); return { accepted: true }; } },
      roster: { warm: () => assert.fail('a document has no member roster') },
      isOwner: (event) => event.senderId === ALICE,
      mentionTarget: () => 'me',
    });
    t.after(() => { harness.stop(); store.close(); });
    harness.handleConnectorEvent(normalizeDocComment(parseCommentNotice(notice()),
      { replies: thread().slice(2), names: new Map([[ALICE, 'Alice Wang']]) }, { selfId: SELF }));
    await harness.ingressBarrier;
    assert.equal(delivered.length, 1);
    assert.match(delivered[0].text, /from=Alice_Wang owner=1 mention=me\]/);
    assert.equal(delivered[0].chatType, 'doc_comment');
  });
});

// The Lark client with lark-cli scripted by subcommand.
function client(answers = {}) {
  const calls = [];
  const lark = new LarkClient({ profile: 'bot-profile', larkCliBin: '/fake/lark-cli', projectDir: '/project', sendTimeoutMs: 5000 }, {
    async run(_command, args) {
      calls.push(args.slice(2));
      const command = args.find((arg) => arg.startsWith('+') || arg === 'metas');
      const answer = answers[command];
      if (answer instanceof Error) return { code: 1, timedOut: false, stdout: '', stderr: answer.message };
      return larkSuccess(typeof answer === 'function' ? answer(args) : answer ?? {});
    },
  }, logger);
  lark.selfId = SELF;
  lark.selfName = 'Vika';
  return { lark, calls };
}

const card = (fields = {}) => ({ items: [{ comment_id: '7618859513273112345', is_whole: false, is_solved: false,
  quote: '目标', reply_list: { replies: thread() }, ...fields }] });

describe('Lark client on document comments', () => {
  it('reads the comment, the document title and the names as the bot before the Core sees it', async () => {
    const { lark, calls } = client({
      '+batch-query-comments': card(),
      metas: { metas: [{ doc_token: TOKEN, title: 'Q4 计划', url: 'https://example.feishu.cn/docx/TLLK' }] },
      '+get-user': (args) => ({ user: { name: { [ALICE]: 'Alice', [BOB]: 'Bob' }[args[args.indexOf('--user-id') + 1]] } }),
    });
    const event = normalizeEvent(await lark.docCommentEvent(notice()));
    assert.equal(event.senderName, 'Alice');
    assert.match(event.content, /^\[on the text: "目标"\]\n\[earlier in this comment: Bob: 这个数对吗？ \| you: .*\]\n@Vika 帮我把这条加到 @Bob 的任务$/);
    assert.deepEqual(calls.slice(0, 2), [
      ['drive', '+batch-query-comments', '--as', 'bot', '--token', TOKEN, '--type', 'docx',
        '--comment-ids', '7618859513273112345', '--format', 'json'],
      ['drive', 'metas', 'batch_query', '--as', 'bot', '--data',
        JSON.stringify({ request_docs: [{ doc_token: TOKEN, doc_type: 'docx' }], with_url: true }), '--format', 'json'],
    ]);
    assert.deepEqual(calls.slice(2).map((args) => args[args.indexOf('--user-id') + 1]).sort(), [ALICE, BOB].sort());
    // Names are kept: a second comment looks nobody up again.
    await lark.docCommentEvent(notice());
    assert.equal(calls.filter((args) => args.includes('+get-user')).length, 2);
  });

  it('drops a comment of its own, reads replies past the first page, and wakes the bot on a failed read', async () => {
    const own = client();
    assert.equal(await own.lark.docCommentEvent(notice({ from: SELF })), null);
    assert.equal(own.calls.length, 0);

    const paged = client({
      '+batch-query-comments': card({ has_more: true, reply_list: { replies: thread().slice(0, 1) } }),
      '+list-replies': (args) => (args.includes('--page-token')
        ? { items: thread().slice(2), has_more: false } : { items: thread().slice(0, 2), has_more: true, page_token: 'p2' }),
    });
    const event = normalizeEvent(await paged.lark.docCommentEvent(notice()));
    assert.match(event.content, /@Vika 帮我把这条加到/);
    assert.equal(paged.calls.filter((args) => args.includes('+list-replies')).length, 2);

    const denied = client({ '+batch-query-comments': new Error('permission denied'), metas: new Error('permission denied') });
    const unread = normalizeEvent(await denied.lark.docCommentEvent(notice()));
    assert.match(unread.content, /could not be read/);
    assert.equal(unread.messageId, MSG);
  });

  it('replies in the comment thread, or opens a whole-document comment where the comment takes no reply', async () => {
    const { lark, calls } = client({ '+batch-query-comments': card(), '+add-reply': { reply_id: '7618859513273120000' } });
    const delivered = await lark.replyMessage({ message_id: MSG, content: { type: 'markdown', text: '**好的**，已记下' }, idempotency_key: 'k' });
    assert.deepEqual(delivered, { message_id: `comment:docx:${TOKEN}:7618859513273112345:7618859513273120000`,
      chat_id: `doc:docx:${TOKEN}`, thread_id: '7618859513273112345' });
    assert.deepEqual(calls[1], ['drive', '+add-reply', '--as', 'bot', '--token', TOKEN, '--type', 'docx',
      '--comment-id', '7618859513273112345', '--content', JSON.stringify([{ type: 'text', text: '好的，已记下' }]), '--format', 'json']);

    const solved = client({ '+batch-query-comments': card({ is_solved: true }),
      '+add-comment': { comment_id: '9000', reply_list: { replies: [{ reply_id: '9001' }] } } });
    const whole = await solved.lark.replyMessage({ message_id: MSG, content: { type: 'markdown', text: '这条已解决' } });
    assert.equal(whole.message_id, `comment:docx:${TOKEN}:9000:9001`);
    assert.deepEqual(solved.calls[1], ['drive', '+add-comment', '--as', 'bot', '--doc', TOKEN, '--type', 'docx', '--full-comment',
      '--content', JSON.stringify([{ type: 'mention_user', mention_user: ALICE }, { type: 'text', text: ' 这条已解决' }]), '--format', 'json']);

    const sheet = client({ '+batch-query-comments': card({ is_whole: true }) });
    await assert.rejects(() => sheet.lark.replyMessage({ message_id: MSG.replace(':docx:', ':sheet:'), content: { type: 'markdown', text: 'x' } }),
      /takes no new whole-document comment/);
    await assert.rejects(() => lark.replyMessage({ message_id: MSG, content: { type: 'card', card: {} } }), /text only/);
    assert.throws(() => lark.sendMessage({ chat_id: `doc:docx:${TOKEN}`, content: { type: 'markdown', text: 'x' } }), /no new message/);
  });

  it('reacts on a comment reply (OnIt as Typing), removes it by its emoji, and deletes its own reply', async () => {
    const { lark, calls } = client();
    assert.deepEqual(await lark.reactMessage({ message_id: MSG, emoji_type: 'OnIt' }), { reaction_id: 'Typing' });
    await lark.deleteReaction({ message_id: MSG, reaction_id: 'Typing' });
    await lark.retractMessage({ message_id: MSG });
    const target = ['--token', TOKEN, '--type', 'docx'];
    assert.deepEqual(calls, [
      ['drive', '+react-reply', '--as', 'bot', ...target, '--reply-id', '7618859513273119999', '--emoji', 'Typing', '--action', 'add', '--format', 'json'],
      ['drive', '+react-reply', '--as', 'bot', ...target, '--reply-id', '7618859513273119999', '--emoji', 'Typing', '--action', 'delete', '--format', 'json'],
      ['drive', '+delete-reply', '--as', 'bot', ...target, '--comment-id', '7618859513273112345', '--reply-id', '7618859513273119999',
        '--yes', '--format', 'json'],
    ]);
  });
});

describe('agent tools on document comments', () => {
  function setup(t, { fail = () => false } = {}) {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'doc-comment-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), logger);
    store.initializeCognition('codex', 'token');
    const connectors = larkConnectors({ tempDir: directory, run: (_bin, args) => {
      if (fail(args)) return { code: 1, timedOut: true, stdout: '', stderr: 'timed out' };
      if (args.includes('+batch-query-comments')) return larkSuccess(card());
      if (args.includes('+add-reply')) return larkSuccess({ reply_id: '7618859513273120000' });
      return larkSuccess({ message_id: 'om_sent123456789' });
    } });
    const service = new ActionService({ config: { workspace: directory, tempDir: path.join(directory, 'tmp'), maxReplyChars: 30_000 },
      store, connectors, logger });
    t.after(() => { store.close(); fs.rmSync(directory, { recursive: true, force: true }); });
    store.ingest(normalizeEvent(normalizeDocComment(parseCommentNotice(notice()), { replies: thread() }, { selfId: SELF })));
    const call = (tool, input) => service.call(tool, input, { authority: 'token' });
    return { store, connectors, call };
  }
  const decision = '5e3702a84e847582be8db7fb73283c02';

  it('answers in plain text only, posts nothing new to a document, and keeps a working Typing until the answer', async (t) => {
    const { store, connectors, call } = setup(t);
    await assert.rejects(() => call('reply_message', { message_id: MSG, card: { elements: [] }, decision_id: decision }),
      /cannot send card messages here/);
    await assert.rejects(() => call('reply_message', { message_id: MSG, markdown: 'hi', mention_names: ['Bob'], decision_id: decision }),
      /mentions are not supported/);
    await assert.rejects(() => call('reply_message', { message_id: MSG, markdown: 'x'.repeat(10_001), decision_id: decision }),
      /10000/);
    await assert.rejects(() => call('send_message', { chat_id: `doc:docx:${TOKEN}`, markdown: 'hi', decision_id: decision }),
      /takes no new message: answer one of its messages with reply_message/);
    assert.equal(connectors.calls.length, 0, 'refused before any lark-cli call');

    await call('react_to_message', { message_id: MSG, emoji_type: 'OnIt', temporary: true, decision_id: decision });
    const sent = await call('reply_message', { message_id: MSG, markdown: '已记下', reply_in_thread: true, decision_id: decision });
    assert.match(sent.text, /Delivered reply/);
    assert.deepEqual(connectors.calls.map(({ args }) => args[3]), ['+react-reply', '+batch-query-comments', '+add-reply', '+react-reply']);
    assert.deepEqual(connectors.calls.at(-1).args.slice(-5), ['Typing', '--action', 'delete', '--format', 'json']);
    const row = store.db.prepare("SELECT chat_id, remote_message_id FROM outbox WHERE action = 'message.reply'").get();
    assert.deepEqual({ ...row }, { chat_id: `doc:docx:${TOKEN}`,
      remote_message_id: `comment:docx:${TOKEN}:7618859513273112345:7618859513273120000` });
  });

  it('never retries an uncertain comment reply: the comment API has no idempotency key', async (t) => {
    let failing = true;
    const { store, call } = setup(t, { fail: (args) => failing && args.includes('+add-reply') });
    const input = { message_id: MSG, markdown: '只发一次', decision_id: decision };
    await assert.rejects(() => call('reply_message', input), /timed out/);
    failing = false;
    await assert.rejects(() => call('reply_message', input), /outcome is uncertain/);
    assert.deepEqual(store.stats().outbox, { UNCERTAIN: 1 });
  });
});
