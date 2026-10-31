// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { normalizeEvent } from '../event.mjs';
import { eventMessage } from './protocol.mjs';
import { createHash } from 'node:crypto';
import { isMeetingEvent, normalizeMeetingEvent } from '../meeting/events.mjs';

export function normalizeLarkEvent(input, instance = 'lark', { docComment = null } = {}) {
  if (isDocCommentEvent(input)) return normalizeDocCommentEvent(input, instance, docComment);
  if (isMeetingEvent(input)) {
    const { raw, ...meeting } = normalizeMeetingEvent(input);
    const kind = {
      'vc.bot.meeting_invited_v1': 'meeting.invited',
      'vc.bot.meeting_activity_v1': 'meeting.activity',
      'vc.bot.meeting_ended_v1': 'meeting.ended',
    }[meeting.eventType];
    return eventMessage('lark', {
      instance, id: meeting.eventId, kind, occurred_at: meeting.createTimeMs,
      subject: { type: 'meeting', id: meeting.meetingId || meeting.meetingNumber || meeting.callId || meeting.eventId },
      content: {}, data: { meeting },
    });
  }
  if ((input?.header?.event_type ?? input?.type ?? input?.event_type) === 'card.action.trigger') {
    const raw = input.event ?? input;
    const id = input.header?.event_id ?? input.event_id;
    const cardId = raw.context?.open_message_id ?? raw.open_message_id ?? raw.message_id;
    const operator = raw.operator?.open_id ?? raw.operator_id;
    if (!/^om_[A-Za-z0-9_-]{8,}$/.test(cardId ?? '')
      || !/^ou_[A-Za-z0-9_-]{8,}$/.test(operator ?? '')) throw new Error('invalid card callback identity');
    const action = raw.action ?? {};
    const interaction = {
      message_id: cardId,
      conversation_id: raw.context?.open_chat_id ?? raw.chat_id ?? null,
      tag: action.tag ?? raw.action_tag,
      value: parseJson(action.value ?? raw.action_value),
      name: action.name ?? raw.action_name,
      form: parseJson(action.form_value ?? raw.form_value),
      input: action.input_value ?? raw.input_value,
      option: action.option ?? raw.option,
      options: action.options ?? raw.options,
      checked: action.checked ?? raw.checked,
    };
    if (JSON.stringify(interaction).length > 12000) throw new Error('card action exceeds the input limit');
    return eventMessage('lark', {
      instance, id, kind: 'message.action',
      occurred_at: Number(input.header?.create_time ?? raw.timestamp ?? Date.now()),
      subject: { type: 'message', id: cardId }, actor: { type: 'user', id: operator },
      message: { id: `ca_${createHash('sha256').update(JSON.stringify([instance, id])).digest('hex').slice(0, 40)}`, type: 'card_action' },
      content: { text: '[card action]' }, data: { interaction },
    });
  }
  return normalizeLarkMessage(input, instance).envelope;
}

// Lark's provider schema is decoded only at the Connector boundary. Media is
// delivered as a resource reference, never as bytes in the prompt.
const AGENT_VISIBLE_MESSAGE_TYPES = new Set([
  'text', 'post', 'image', 'file', 'audio', 'media', 'video', 'sticker',
  'merge_forward', 'video_chat', 'interactive', 'card_action',
]);
const IMAGE_KEY = /^img_[A-Za-z0-9_-]{8,}$/;

function parseJson(value) {
  if (typeof value !== 'string') return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function collectText(value, output, mediaKeys = new Set(), depth = 0) {
  if (depth > 8 || value === null || value === undefined) return;
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (trimmed) output.push(trimmed);
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) collectText(item, output, mediaKeys, depth + 1);
    return;
  }
  if (typeof value !== 'object') return;

  // Rich-post image/media nodes carry resources inside otherwise generic
  // content. Preserve only the resource pointer that the matching MCP tool
  // can retrieve; keep the generic sensitive-key filter below for everything
  // else.
  if ((value.tag === 'img' || value.tag === 'image')
    && typeof value.image_key === 'string' && IMAGE_KEY.test(value.image_key)) {
    const key = `image:${value.image_key}`;
    if (!mediaKeys.has(key)) output.push(`[image image_key=${value.image_key}]`);
    mediaKeys.add(key);
    return;
  }
  if ((value.tag === 'media' || value.tag === 'file')
    && typeof value.file_key === 'string' && value.file_key) {
    const key = `file:${value.file_key}`;
    if (!mediaKeys.has(key)) output.push(`[${value.tag} file_key=${value.file_key}]`);
    mediaKeys.add(key);
    return;
  }

  const priorityKeys = ['text', 'title', 'content', 'name', 'file_name'];
  const visited = new Set();
  for (const key of priorityKeys) {
    if (Object.hasOwn(value, key)) {
      collectText(value[key], output, mediaKeys, depth + 1);
      visited.add(key);
    }
  }
  for (const [key, child] of Object.entries(value)) {
    if (visited.has(key) || ['tag', 'style', 'language'].includes(key) || /(?:_key|_id|url|token|secret)$/i.test(key)) continue;
    collectText(child, output, mediaKeys, depth + 1);
  }
}

export function humanizeContent(messageType, rawContent) {
  if (messageType === 'interactive' && (rawContent == null
    || (typeof rawContent === 'string' && !rawContent.trim()))) return '[card]';
  if (typeof rawContent === 'string' && !rawContent.trim().startsWith('{') && !rawContent.trim().startsWith('[')) {
    return rawContent.trim();
  }
  const parsed = parseJson(rawContent);
  if (typeof parsed === 'string') return parsed.trim();
  // image_key/file_key are resource pointers scoped to this message and requiring bot auth
  // to resolve, so surfacing them here (unlike other *_key/*_url fields) is not a content
  // leak — see packages/daemon/agent/lark-message-formats.md for how to fetch each type.
  if (messageType === 'image' && typeof parsed?.image_key === 'string' && IMAGE_KEY.test(parsed.image_key)) {
    return `[image image_key=${parsed.image_key}]`;
  }
  if (['file', 'audio', 'media', 'video', 'sticker'].includes(messageType)
    && typeof parsed?.file_key === 'string' && parsed.file_key) {
    const name = typeof parsed.file_name === 'string' && parsed.file_name ? ` file_name=${parsed.file_name}` : '';
    return `[${messageType} file_key=${parsed.file_key}${name}]`;
  }
  const parts = [];
  collectText(parsed, parts);
  const unique = [...new Set(parts)];
  if (unique.length > 0) return unique.join('\n').trim();
  // Lark emits call invitations and call lifecycle notices as video_chat IM
  // messages. They often carry no human-readable body, but still need to wake
  // the Agent so it can inspect the active meeting and decide whether to join.
  if (messageType === 'video_chat') return '[Video call]';
  if (messageType === 'merge_forward') return '[Forwarded messages]';
  // A card's receive event can arrive without any extractable body even though
  // the message API does return its text, so fall back to a placeholder that
  // wakes the Agent instead of letting empty_content drop the card. Reading the
  // body is a list_chat_messages call away — see lark-message-formats.md.
  if (messageType === 'interactive') return '[card]';
  if (['image', 'file', 'audio', 'media', 'video', 'sticker'].includes(messageType)) {
    return `[${messageType}]`;
  }
  return '';
}

function normalizeMentions(value) {
  if (!Array.isArray(value)) return [];
  return value.map((mention) => {
    if (!mention || typeof mention !== 'object') return null;
    return {
      key: mention.key ?? null,
      name: mention.name ?? null,
      id: mention.id?.open_id ?? mention.id?.user_id ?? mention.open_id ?? mention.user_id ?? null,
    };
  }).filter(Boolean);
}

function valueOrNull(value) {
  return value === undefined || value === null || value === '' ? null : String(value);
}

export function normalizeLarkMessage(input, instance = 'lark') {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    throw new Error('event must be a JSON object');
  }

  const isRaw = input.header && input.event;
  const header = isRaw ? input.header : {};
  const event = isRaw ? input.event : input;
  const message = isRaw ? event.message ?? {} : input;
  const sender = isRaw ? event.sender ?? {} : {};

  const type = valueOrNull(header.event_type ?? input.type ?? input.event_type);
  const messageId = valueOrNull(message.message_id ?? input.message_id ?? input.id);
  const eventId = valueOrNull(header.event_id ?? input.event_id);
  const chatId = valueOrNull(message.chat_id ?? input.chat_id);
  const chatType = valueOrNull(message.chat_type ?? input.chat_type) ?? 'unknown';
  const messageType = valueOrNull(message.message_type ?? input.message_type) ?? 'unknown';
  const senderType = valueOrNull(sender.sender_type ?? input.sender_type);
  // Another bot's message (with the include_bot permission) may carry no
  // user id; it still needs an actor, so the Core knows a bot wrote it.
  const senderId = valueOrNull(
    sender.sender_id?.open_id ?? sender.sender_id?.user_id ?? sender.sender_id?.app_id ?? input.sender_id,
  ) ?? (['app', 'bot'].includes(senderType) ? 'bot' : null);
  const rootId = valueOrNull(message.root_id ?? input.root_id);
  const parentId = valueOrNull(message.parent_id ?? input.parent_id);
  const threadId = valueOrNull(message.thread_id ?? input.thread_id);
  const threadRootId = threadId ? (rootId || (parentId && parentId !== messageId ? parentId : messageId)) : null;
  const tenantKey = valueOrNull(header.tenant_key ?? sender.tenant_key ?? input.tenant_key) ?? '-';
  const createTimeRaw = message.create_time ?? header.create_time ?? input.create_time ?? input.timestamp;
  const createTimeMs = Number.parseInt(String(createTimeRaw ?? Date.now()), 10);
  const rawContent = message.content ?? input.content ?? '';
  const content = humanizeContent(messageType, rawContent);
  const mentions = normalizeMentions(message.mentions ?? input.mentions);

  if (!type) throw new Error('event type is missing');
  if (!messageId) throw new Error('message_id is missing');
  if (!chatId) throw new Error('chat_id is missing');

  if (type !== 'im.message.receive_v1') throw new Error(`unsupported Lark message event: ${type}`);
  return normalizeEvent(eventMessage('lark', {
    instance,
    id: eventId || `message:${messageId}`,
    kind: 'message.created',
    occurred_at: Number.isFinite(createTimeMs) ? createTimeMs : Date.now(),
    subject: { type: 'conversation', id: chatId },
    actor: senderId ? { id: senderId, type: senderType || 'user' } : null,
    content: { text: content },
    conversation: { id: chatId, type: chatType, tenant_id: tenantKey, thread_id: threadId },
    message: { id: messageId, type: messageType, root_id: rootId, parent_id: parentId,
      thread_root_id: threadRootId, mentions },
    reply_route: { action: 'message.reply', input: { message_id: messageId } },
    data: { supported: AGENT_VISIBLE_MESSAGE_TYPES.has(messageType) },
  }));
}

// Drive comment notices: `drive.notice.comment_add_v1` fires whenever Lark
// would notify the app as a person: a new comment on a doc it owns, a reply in
// a thread it has commented in, or a comment/reply that @-mentions it. The
// notice carries ids only; the Connector reads the comment itself. Every notice
// becomes an input with a `reason`, and the agent decides whether to answer
// (as with unmentioned group messages); only the bot's own comments are dropped.
export const DOC_COMMENT_EVENT = 'drive.notice.comment_add_v1';
const DOC_FILE_TYPES = new Set(['doc', 'docx', 'sheet', 'bitable', 'slides', 'file']);
const DOC_TOKEN = /^[A-Za-z0-9_-]{8,64}$/;
const COMMENT_ID = /^[0-9A-Za-z_-]{4,64}$/;
const DOC_COMMENT_MESSAGE = /^doccomment:([a-z]+):([A-Za-z0-9_-]{8,64}):([0-9A-Za-z_-]{4,64})$/;

export function isDocCommentEvent(input) {
  return (input?.header?.event_type ?? input?.type ?? input?.event_type) === DOC_COMMENT_EVENT;
}

// Why Lark notified the bot: mention | owned_doc (a new comment on a doc it
// owns) | thread_participant (a reply in a thread it is in) | notice (an
// unknown notice_type; is_mentioned still says whether it was @-mentioned).
export function docCommentReason({ notice_type: type, mentioned }) {
  if (mentioned) return 'mention';
  if (type === 'add_comment') return 'owned_doc';
  if (type === 'add_reply') return 'thread_participant';
  return 'notice';
}

// The ids a notice names; throws when it is malformed.
export function docCommentNotice(input) {
  const event = input?.event ?? input ?? {};
  const meta = event.notice_meta ?? {};
  const notice = {
    file_type: String(meta.file_type ?? ''), file_token: String(meta.file_token ?? ''),
    comment_id: String(event.comment_id ?? ''), reply_id: valueOrNull(event.reply_id),
    notice_type: valueOrNull(meta.notice_type), from: valueOrNull(meta.from_user_id?.open_id),
    to: valueOrNull(meta.to_user_id?.open_id), mentioned: event.is_mentioned === true,
  };
  notice.reason = docCommentReason(notice);
  if (!DOC_FILE_TYPES.has(notice.file_type) || !DOC_TOKEN.test(notice.file_token)
    || !COMMENT_ID.test(notice.comment_id)) throw new Error('invalid doc comment notice');
  if (notice.reply_id !== null && !COMMENT_ID.test(notice.reply_id)) throw new Error('invalid doc comment reply id');
  return notice;
}

// A notice for a comment the bot wrote itself (its own reply in a thread).
export function isOwnDocComment(notice) {
  return Boolean(notice.from && notice.to && notice.from === notice.to);
}

// The msg= a doc comment carries: reply_message on it adds a reply to that
// comment thread. It encodes the whole route, so the Connector needs no state.
export function docCommentMessageId({ file_type: type, file_token: token, comment_id: commentId }) {
  return `doccomment:${type}:${token}:${commentId}`;
}

export function parseDocCommentMessageId(value) {
  const match = DOC_COMMENT_MESSAGE.exec(String(value ?? ''));
  if (!match || !DOC_FILE_TYPES.has(match[1])) return null;
  return { file_type: match[1], file_token: match[2], comment_id: match[3] };
}

// Plain text of comment reply elements (text_run / docs_link / person).
export function commentElementsText(elements) {
  if (!Array.isArray(elements)) return '';
  return elements.map((element) => {
    if (element?.type === 'text_run') return element.text_run?.text ?? '';
    if (element?.type === 'docs_link') return element.docs_link?.url ?? '';
    if (element?.type === 'person') return `@${element.person?.user_id ?? 'user'}`;
    return '';
  }).join('').trim();
}

// `comment` is the fetched comment ({ quote, replies: [{ reply_id, content:
// { elements } }] }) or null when the read failed; the input still wakes the
// agent with the ids so it can read the thread itself.
export function normalizeDocCommentEvent(input, instance = 'lark', comment = null) {
  const notice = docCommentNotice(input);
  if (isOwnDocComment(notice)) return null;
  const header = input.header ?? {};
  const replies = Array.isArray(comment?.reply_list?.replies) ? comment.reply_list.replies
    : Array.isArray(comment?.replies) ? comment.replies : [];
  const reply = replies.find((item) => notice.reply_id && item?.reply_id === notice.reply_id) ?? replies.at(-1) ?? null;
  const text = commentElementsText(reply?.content?.elements) || '[doc comment; read it with lark-cli drive +batch-query-comments]';
  const quote = typeof comment?.quote === 'string' ? oneLineLabel(comment.quote, 500) : '';
  const id = valueOrNull(header.event_id) ?? `doccomment:${notice.file_token}:${notice.comment_id}:${notice.reply_id ?? '-'}`;
  const createTimeMs = Number.parseInt(String(header.create_time ?? Date.now()), 10);
  return eventMessage('lark', {
    instance, id, kind: 'doc.comment',
    occurred_at: Number.isFinite(createTimeMs) ? createTimeMs : Date.now(),
    // One subject per document: every comment on a doc shares its scope, so a
    // burst on one doc is fenced and ordered together.
    subject: { type: 'document', id: `${notice.file_type}:${notice.file_token}` },
    actor: notice.from ? { type: 'user', id: notice.from } : null,
    content: {
      title: `[doc ${notice.file_type}:${notice.file_token} reason=${notice.reason} mentioned=${notice.mentioned ? 1 : 0} comment=${notice.comment_id}${notice.reply_id ? ` reply=${notice.reply_id}` : ''}${comment?.is_whole ? ' whole=1' : ''}${comment?.is_solved ? ' solved=1' : ''}]${quote ? ` quote: ${quote}` : ''}`,
      text: oneLineLabel(text, 4_000),
    },
    message: { id: docCommentMessageId(notice), type: 'doc_comment' },
    reply_route: { action: 'message.reply', input: { message_id: docCommentMessageId(notice) } },
    data: { doc_comment: { ...notice, quote: quote || null } },
  });
}

function oneLineLabel(value, max) {
  return String(value ?? '').replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, max);
}
