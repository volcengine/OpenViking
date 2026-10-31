// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, it } from 'node:test';
import { ActionService } from '../src/actions/service.mjs';
import {
  docCommentMessageId, normalizeLarkEvent, parseDocCommentMessageId,
} from '../src/connectors/lark-events.mjs';
import { normalizeEvent } from '../src/event.mjs';
import { LarkClient, SUBSCRIBED_EVENT_TYPES } from '../src/lark.mjs';
import { formatInboundLine } from '../src/runtime-input.mjs';
import { Store } from '../src/store.mjs';
import { fakeConnectors } from './action-fixture.mjs';
import { subjectEvent } from './event-fixture.mjs';

const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };
const TOKEN = 'runtime-token';
const DOC = 'doxcnAbCdEfGh123456789';

function notice(fields = {}) {
  return {
    schema: '2.0',
    header: { event_id: fields.eventId ?? 'evt_doc_comment_1', event_type: 'drive.notice.comment_add_v1',
      create_time: String(fields.createTime ?? Date.now()), tenant_key: 't1', app_id: 'cli_x' },
    event: {
      notice_meta: { file_type: 'docx', file_token: fields.token ?? DOC, notice_type: fields.noticeType ?? 'add_comment',
        from_user_id: { open_id: fields.from ?? 'ou_author12345678' }, to_user_id: { open_id: 'ou_bot123456789' } },
      comment_id: fields.commentId ?? '7300000000000000001', reply_id: '7300000000000000002',
      is_mentioned: fields.mentioned ?? true,
    },
  };
}

const comment = {
  comment_id: '7300000000000000001', quote: 'the selected\nparagraph', is_whole: false, is_solved: false,
  reply_list: { replies: [{ reply_id: '7300000000000000002', content: { elements: [
    { type: 'person', person: { user_id: 'ou_bot123456789' } },
    { type: 'text_run', text_run: { text: ' please tighten this' } },
  ] } }] },
};

describe('Lark doc comment notices', () => {
  it('subscribes to the comment notice event', () => {
    assert.ok(SUBSCRIBED_EVENT_TYPES.includes('drive.notice.comment_add_v1'));
  });

  it('normalizes a mention into a per-document input the agent can reply to', () => {
    const event = normalizeEvent(normalizeLarkEvent(notice(), 'lark', { docComment: comment }));
    assert.equal(event.kind, 'doc.comment');
    assert.deepEqual(event.subject, { type: 'document', id: `docx:${DOC}` });
    assert.equal(event.messageId, `doccomment:docx:${DOC}:7300000000000000001`);
    assert.deepEqual(event.replyRoute, { action: 'message.reply', input: { message_id: event.messageId } });
    assert.equal(event.chatId, null);
    const line = formatInboundLine(event);
    assert.match(line, /^\[source=lark kind=doc\.comment msg=doccomment:docx:\S+ event=evt_doc_comment_1 subject=document:docx:\S+ time=\S+ actor=user:ou_author12345678\] /);
    assert.match(line, /reason=mention mentioned=1 comment=7300000000000000001 reply=7300000000000000002\] quote: the selected paragraph/);
    assert.match(line, /@ou_bot123456789 please tighten this$/);
  });

  it('keeps unmentioned notices with a reason, drops the bot\'s own comments, keeps unread ones, rejects bad ids', () => {
    const reason = (fields) => formatInboundLine(normalizeEvent(normalizeLarkEvent(notice(fields)))).match(/reason=(\S+) mentioned=(\d)/).slice(1);
    assert.deepEqual(reason({ mentioned: false }), ['owned_doc', '0']);
    assert.deepEqual(reason({ mentioned: false, noticeType: 'add_reply' }), ['thread_participant', '0']);
    assert.deepEqual(reason({ mentioned: true, noticeType: 'add_reply' }), ['mention', '1']);
    assert.deepEqual(reason({ mentioned: false, noticeType: 'something_new' }), ['notice', '0']);
    assert.equal(normalizeLarkEvent(notice({ from: 'ou_bot123456789' })), null);
    const unread = normalizeEvent(normalizeLarkEvent(notice()));
    assert.match(unread.content, /drive \+batch-query-comments/);
    assert.throws(() => normalizeLarkEvent(notice({ token: 'bad token' })), /invalid doc comment/);
  });

  it('round-trips the reply route and refuses anything else', () => {
    const ref = { file_type: 'sheet', file_token: DOC, comment_id: '123456' };
    assert.deepEqual(parseDocCommentMessageId(docCommentMessageId(ref)), ref);
    for (const value of ['om_abc12345678', `doccomment:wiki:${DOC}:123456`, `doccomment:docx:${DOC}`]) {
      assert.equal(parseDocCommentMessageId(value), null);
    }
  });

  it('reads and replies through lark-cli drive as the bot', async () => {
    const calls = [];
    const lark = new LarkClient({ profile: 'bot-profile', larkCliBin: '/fake/lark-cli', projectDir: '/p', sendTimeoutMs: 5000 }, {
      async run(command, args) {
        calls.push(args);
        const data = args.includes('+batch-query-comments') ? { items: [comment] } : { reply_id: '7300000000000000009' };
        return { code: 0, timedOut: false, stdout: JSON.stringify({ ok: true, data }), stderr: '' };
      },
    }, logger);
    const ref = { file_type: 'docx', file_token: DOC, comment_id: '7300000000000000001' };
    assert.equal((await lark.getDocComment(ref)).quote, comment.quote);
    assert.deepEqual(calls[0], ['--profile', 'bot-profile', 'drive', '+batch-query-comments', '--as', 'bot',
      '--token', DOC, '--type', 'docx', '--comment-ids', '7300000000000000001', '--format', 'json']);
    const result = await lark.replyMessage({ message_id: docCommentMessageId(ref), idempotency_key: 'k',
      content: { type: 'markdown', text: 'Done.', mentions: [{ id: 'ou_author12345678' }] } });
    assert.deepEqual(result, { message_id: '7300000000000000001:7300000000000000009', doc_comment: true });
    assert.deepEqual(calls[1], ['--profile', 'bot-profile', 'drive', '+add-reply', '--as', 'bot', '--token', DOC,
      '--type', 'docx', '--comment-id', '7300000000000000001', '--content',
      JSON.stringify([{ type: 'mention_user', text: 'ou_author12345678' }, { type: 'text', text: ' Done.' }]), '--format', 'json']);
    await assert.rejects(lark.replyMessage({ message_id: docCommentMessageId(ref), content: { type: 'card', card: {} } }),
      /markdown text only/);
  });

  function setup(t) {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'doc-comment-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), logger);
    store.initializeCognition('codex', TOKEN);
    const connectors = fakeConnectors({ ids: ['lark'], owners: { lark: { id: 'ou_owner12345678', target: { user_id: 'ou_owner12345678' } } } });
    const service = new ActionService({
      config: { workspace: directory, tempDir: path.join(directory, 'tmp'), maxReplyChars: 1_000, channel: 'lark' },
      store, connectors, logger,
    });
    t.after(() => { store.close(); fs.rmSync(directory, { recursive: true, force: true }); });
    return { store, connectors, call: (tool, input) => service.call(tool, input, { authority: TOKEN }) };
  }

  it('lets a doc comment be answered in a comment thread of its own document only', async (t) => {
    const { store, connectors, call } = setup(t);
    store.ingest(normalizeEvent(normalizeLarkEvent(notice(), 'lark', { docComment: comment })));
    const own = `doccomment:docx:${DOC}:7300000000000000001`;
    await call('reply_message', { message_id: own, markdown: 'Tightened.', decision_id: 'evt_doc_comment_1' });
    assert.deepEqual(connectors.calls.map(({ action, input }) => [action, input.message_id]), [['message.reply', own]]);
    // Another document's comment, or a chat message, stays owner-only.
    for (const message_id of [`doccomment:docx:doxcnOtherDoc12345678:7300000000000000001`, 'om_group12345678']) {
      await assert.rejects(call('reply_message', { message_id, markdown: 'leak', decision_id: 'evt_doc_comment_1' }),
        (error) => error.code === 'owner_only');
    }
    // Other non-chat events still cannot reply to a doc comment.
    store.ingest(subjectEvent({ eventId: 'reminder-1' }));
    await assert.rejects(call('reply_message', { message_id: own, markdown: 'x', decision_id: 'reminder-1' }),
      (error) => error.code === 'owner_only');
    assert.equal(connectors.calls.length, 1);
  });
});
