// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { normalizeEvent } from '../event.mjs';
import { eventMessage } from './protocol.mjs';
import { createHash } from 'node:crypto';
import { isMeetingEvent, normalizeMeetingEvent } from '../meeting/events.mjs';

export function normalizeLarkEvent(input, instance = 'lark') {
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
