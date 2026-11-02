// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { eventMessage } from './connectors/protocol.mjs';

// Comments on Lark documents, as chat messages. A document is a conversation
// (`doc:<type>:<token>`, type `doc_comment`), each comment on it a thread there
// (the comment id), and each reply in a comment a message
// (`comment:<type>:<token>:<comment>:<reply>`). The comment event carries ids
// only: the Connector reads the comment, its thread and the document's title
// as the bot before the Core sees it.

export const DOC_COMMENT_EVENT = 'drive.notice.comment_add_v1';
export const DOC_COMMENT_CONVERSATION = 'doc_comment';
// Lark's comment API caps the text of one comment or reply at 10000 characters.
export const MAX_COMMENT_CHARS = 10_000;

// File types the comment API takes. Only these take a whole-document comment,
// the answer to a comment that takes no reply.
const FILE_TYPES = new Set(['doc', 'docx', 'sheet', 'file', 'slides', 'bitable', 'apps']);
export const WHOLE_COMMENT_TYPES = new Set(['doc', 'docx', 'file']);
const TOKEN = /^[A-Za-z0-9_-]{1,128}$/;
const OPEN_ID = /^ou_[A-Za-z0-9_-]{8,}$/;
const MESSAGE_ID = /^comment:([a-z]+):([A-Za-z0-9_-]{1,128}):([A-Za-z0-9_-]{1,128}):([A-Za-z0-9_-]{1,128})$/;

// How much of the thread a comment line shows: its first comment and the
// replies just before it, each bounded.
const EARLIER_REPLIES = 4;
const EARLIER_CHARS = 300;
const QUOTE_CHARS = 500;
const TITLE_CHARS = 200;

export function isDocCommentEvent(raw) {
  return (raw?.header?.event_type ?? raw?.event_type ?? raw?.type) === DOC_COMMENT_EVENT;
}

export function docChatId(fileType, fileToken) {
  return `doc:${fileType}:${fileToken}`;
}

export function commentMessageId({ fileType, fileToken, commentId, replyId }) {
  return `comment:${fileType}:${fileToken}:${commentId}:${replyId}`;
}

// The comment reply a message id names, or null when it names none.
export function parseCommentMessageId(id) {
  const match = MESSAGE_ID.exec(String(id ?? ''));
  if (!match || !FILE_TYPES.has(match[1])) return null;
  return { fileType: match[1], fileToken: match[2], commentId: match[3], replyId: match[4] };
}

export function isDocChatId(id) {
  return /^doc:[a-z]+:/.test(String(id ?? ''));
}

function text(value) {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '';
}

// The notice as the long connection delivers it: a v2 envelope with the ids
// in `event` and `event.notice_meta`, or the same fields flattened.
export function parseCommentNotice(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('event must be a JSON object');
  const event = raw.event && typeof raw.event === 'object' ? raw.event : raw;
  const meta = event.notice_meta ?? raw.notice_meta ?? {};
  const fileType = text(meta.file_type ?? event.file_type);
  const fileToken = text(meta.file_token ?? event.file_token);
  const commentId = text(event.comment_id ?? raw.comment_id);
  const replyId = text(event.reply_id ?? raw.reply_id);
  const from = meta.from_user_id ?? event.from_user_id ?? event.user_id;
  const fromId = typeof from === 'string' ? from : text(from?.open_id);
  if (!FILE_TYPES.has(fileType) || !TOKEN.test(fileToken) || !TOKEN.test(commentId)
    || (replyId && !TOKEN.test(replyId))) throw new Error('invalid document comment event');
  if (!OPEN_ID.test(fromId)) throw new Error('document comment event names no commenter');
  // Header times are milliseconds; a bare notice timestamp may be seconds.
  const time = Number.parseInt(text(raw.header?.create_time ?? meta.timestamp ?? event.timestamp ?? event.action_time), 10);
  return {
    eventId: text(raw.header?.event_id ?? raw.event_id) || null,
    createTimeMs: Number.isFinite(time) && time > 0 ? (time < 1e12 ? time * 1_000 : time) : Date.now(),
    tenantKey: text(raw.header?.tenant_key ?? event.tenant_key) || null,
    fileType, fileToken, commentId, replyId: replyId || null, fromId,
    mentioned: (event.is_mentioned ?? meta.is_mentioned ?? event.is_mention) === true,
  };
}

function shortId(id) {
  const match = /^([a-z]{1,4}_)(.+)$/.exec(id);
  return match ? match[1] + match[2].slice(0, 8) : String(id).slice(0, 11);
}

function oneLine(value, max) {
  const line = String(value ?? '').replace(/[\u0000-\u001f\u007f\u2028\u2029]/g, ' ').replace(/\s+/g, ' ').trim();
  const chars = [...line];
  return chars.length > max ? `${chars.slice(0, max).join('')}…` : line;
}

// A reply's elements as text: people as @name, document links as their URL.
// `mentions` are the people it @mentions, by open_id.
export function replyText(reply, nameOf = () => null) {
  const parts = [];
  const mentions = [];
  const elements = reply?.content?.elements;
  for (const element of Array.isArray(elements) ? elements.slice(0, 200) : []) {
    const type = element?.type;
    if (type === 'text_run' || type === 'text') {
      parts.push(text(element.text_run?.text ?? element.text));
    } else if (type === 'person' || type === 'mention_user') {
      const id = text(element.person?.user_id ?? element.mention_user ?? element.text);
      if (!id) continue;
      mentions.push(id);
      parts.push(`@${nameOf(id) ?? shortId(id)}`);
    } else if (type === 'docs_link' || type === 'link') {
      parts.push(text(element.docs_link?.url ?? element.link ?? element.text));
    }
  }
  const images = Array.isArray(reply?.extra?.image_list) ? reply.extra.image_list.length : 0;
  if (images > 0) parts.push(images === 1 ? ' [image]' : ` [${images} images]`);
  return { text: parts.join('').trim(), mentions: [...new Set(mentions)] };
}

// People a comment line names: the commenter, whom the reply mentions, and
// who wrote the earlier replies it shows. The bot is never looked up.
export function commentPeople(notice, context, selfId = null) {
  const { target, earlier } = threadParts(notice, context.replies ?? []);
  const ids = new Set([notice.fromId]);
  for (const reply of [target, ...earlier]) {
    if (!reply) continue;
    if (OPEN_ID.test(text(reply.user_id))) ids.add(reply.user_id);
    for (const id of replyText(reply).mentions) ids.add(id);
  }
  ids.delete(selfId);
  return [...ids].filter((id) => OPEN_ID.test(id));
}

// The reply the event is about and the ones shown before it: the comment's
// first reply (what the thread is about) and the last few before this one.
function threadParts(notice, replies) {
  const index = notice.replyId ? replies.findIndex((reply) => text(reply?.reply_id) === notice.replyId) : 0;
  const target = index >= 0 ? replies[index] ?? null : null;
  if (index <= 0) return { target, index, earlier: [], omitted: 0 };
  const from = Math.max(1, index - EARLIER_REPLIES);
  return { target, index, earlier: [replies[0], ...replies.slice(from, index)], omitted: from - 1 };
}

// The comment event as a standard chat message. `context` is what the
// Connector read: `{ comment, replies, title, url, names }`, with `error` set
// when the comment could not be read; the bot still wakes, told why.
export function normalizeDocComment(notice, context = {}, { instance = 'lark', selfId = null, selfName = null } = {}) {
  const names = context.names ?? new Map();
  const nameOf = (id) => (id && id === selfId ? selfName ?? null : names.get(id) ?? null);
  const replies = Array.isArray(context.replies) ? context.replies : [];
  const { target, index, earlier, omitted } = threadParts(notice, replies);
  const replyId = notice.replyId ?? (text(target?.reply_id) || null);
  if (!replyId) throw new Error('document comment event names no reply');
  const ids = { fileType: notice.fileType, fileToken: notice.fileToken, commentId: notice.commentId };
  const chatId = docChatId(notice.fileType, notice.fileToken);
  const messageId = commentMessageId({ ...ids, replyId });

  const document = context.title
    ? `[document "${oneLine(context.title, TITLE_CHARS)}"${context.url ? ` ${oneLine(context.url, 300)}` : ''}]`
    : `[document ${notice.fileType} ${notice.fileToken}]`;
  const lines = [];
  const quote = oneLine(context.comment?.quote, QUOTE_CHARS);
  if (quote) lines.push(`[on the text: "${quote}"]`);
  if (earlier.length > 0) {
    const shown = earlier.map((reply) => {
      const author = text(reply?.user_id);
      const who = author && author === selfId ? 'you' : (names.get(author) ?? (author ? shortId(author) : 'unknown'));
      return `${oneLine(who, 100)}: ${oneLine(replyText(reply, nameOf).text, EARLIER_CHARS)}`;
    });
    if (omitted > 0) shown.splice(1, 0, `(${omitted} more)`);
    lines.push(`[earlier in this comment: ${shown.join(' | ')}]`);
  }
  let body;
  let mentions = [];
  if (target) {
    const read = replyText(target, nameOf);
    body = read.text || '[empty comment]';
    mentions = read.mentions;
  } else {
    body = context.error
      ? '[the comment could not be read: the document is probably not shared with the bot]'
      : '[the comment was not found: it may have been deleted]';
  }
  // The event's own flag stands in for a mention the text did not show.
  if (notice.mentioned && selfId && !mentions.includes(selfId)) mentions.push(selfId);
  lines.push(body);

  // A reply right after one of the bot's own continues that exchange: it is
  // shown as a reply to that message (reply_to=me).
  const previous = index > 0 ? replies[index - 1] : null;
  const parentId = previous && selfId && text(previous.user_id) === selfId && text(previous.reply_id)
    ? commentMessageId({ ...ids, replyId: text(previous.reply_id) }) : null;
  return eventMessage('lark', {
    instance,
    id: notice.eventId || `comment:${notice.commentId}:${replyId}`,
    kind: 'message.created',
    occurred_at: notice.createTimeMs,
    subject: { type: 'conversation', id: chatId },
    actor: { id: notice.fromId, type: 'user', name: names.get(notice.fromId) ?? null },
    content: { title: document, text: lines.join('\n') },
    conversation: { id: chatId, type: DOC_COMMENT_CONVERSATION, tenant_id: notice.tenantKey ?? '-', thread_id: notice.commentId },
    message: {
      id: messageId, type: DOC_COMMENT_CONVERSATION, parent_id: parentId,
      mentions: mentions.slice(0, 100).map((id) => ({ id, name: nameOf(id), key: null })),
    },
    reply_route: { action: 'message.reply', input: { message_id: messageId } },
    data: { supported: true },
  });
}

// Markdown as the plain text a comment shows: links as "text (url)", no
// emphasis markers, headings or code fences.
export function commentPlainText(markdown) {
  return String(markdown ?? '')
    .split('\n').filter((line) => !/^\s*```/.test(line)).join('\n')
    .replace(/!?\[([^\]\n]*)\]\(([^)\s]+)\)/g, (_, label, url) => (label && label !== url ? `${label} (${url})` : url))
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/(\*\*|__)(?=\S)([\s\S]*?\S)\1/g, '$2')
    .replace(/`([^`\n]+)`/g, '$1')
    .trim();
}

// The --content elements of a comment or reply: plain text, optionally
// after @mentions of the given people.
export function commentContent(textValue, mentionIds = []) {
  const body = commentPlainText(textValue);
  if (!body) throw new Error('a document comment needs text');
  return [
    ...mentionIds.map((id) => ({ type: 'mention_user', mention_user: id })),
    { type: 'text', text: mentionIds.length > 0 ? ` ${body}` : body },
  ];
}
