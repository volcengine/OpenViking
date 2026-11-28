// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { eventMessage } from '../connectors/protocol.mjs';

// DingTalk ids are standard base64, and tool ids take no "/", so each one
// travels base64url-encoded. A message id carries its group, as Slack and
// OneBot ids do: a reply needs nothing but the id.
const encode = (value) => Buffer.from(value).toString('base64url');
const decode = (value) => Buffer.from(value, 'base64url').toString('utf8');

export const chatId = (conversationId) => `dtc_${encode(conversationId)}`;
export const messageId = (conversationId, msgId) => `dtm_${encode(conversationId)}.${encode(msgId)}`;

// The DingTalk openConversationId of a chat= or msg= id.
export function parseChatId(id) {
  const match = /^dtc_([\w-]+)$/.exec(String(id ?? ''));
  if (!match) throw new Error('DingTalk chat_id must be a dtc_ group id');
  return decode(match[1]);
}

export function parseMessageTarget(id) {
  const match = /^dtm_([\w-]+)\.([\w-]+)$/.exec(String(id ?? ''));
  if (!match) throw new Error('DingTalk message_id must be a dtm_ message id');
  return { group: decode(match[1]), message: decode(match[2]) };
}

// A robot callback as a Core event: group text that mentions the bot, or
// null for anything else.
export function normalizeMessage(callback, { botId }) {
  const message = JSON.parse(callback.data);
  if (String(message.conversationType) !== '2' || message.msgtype !== 'text' || message.isInAtList === false) return null;
  const text = String(message.text?.content ?? '').trim();
  if (!text) return null;
  const id = messageId(message.conversationId, message.msgId);
  return eventMessage('dingtalk', {
    id: `message:${id}`,
    kind: 'message.created',
    occurred_at: Number(message.createAt),
    subject: { type: 'message', id },
    actor: { type: 'user', id: message.senderStaffId || message.senderId,
      ...(message.senderNick ? { name: message.senderNick } : {}) },
    content: { text },
    conversation: { type: 'group', id: chatId(message.conversationId), tenant_id: message.chatbotCorpId || null },
    message: { id, type: 'text', mentions: [{ id: botId }] },
    reply_route: { action: 'message.reply', input: { message_id: id } },
  });
}

// A flattened DWS event for a group message this Connector handles, before the
// content type is looked at. Lets the caller tell an unreadable content type
// from an event it never processes.
export function isDwsGroupMessage(raw, group) {
  const time = raw?.event_time || raw?.timestamp;
  return ['user_im_message_receive_group', 'user_im_message_receive_group_all'].includes(raw?.type)
    && raw.conversation_id === group && Boolean(raw.message_id) && Boolean(raw.sender_open_dingtalk_id)
    && Number.isFinite(time) && time > 0;
}

// DWS emits a flattened personal event. The documented v1.0.69 output has no
// structured @ list, so a display name in content never creates a mention.
export function normalizeDwsMessage(raw, { group, tenantId }) {
  if (!isDwsGroupMessage(raw, group)) return null;
  const text = typeof raw.content === 'string' ? raw.content.trim() : '';
  if (!text) return null;
  const time = raw.event_time || raw.timestamp;
  const id = messageId(group, raw.message_id);
  const quote = raw.quoted_message;
  const quoted = quote?.conversation_id === group && quote.message_id ? quote : null;
  return eventMessage('dingtalk', {
    id: `message:${id}`,
    kind: 'message.created',
    occurred_at: time,
    subject: { type: 'message', id },
    actor: { type: 'user', id: raw.sender_open_dingtalk_id, ...(raw.sender ? { name: raw.sender } : {}) },
    content: { text },
    conversation: { type: 'group', id: chatId(group), tenant_id: tenantId },
    message: { id, type: 'text', mentions: [], ...(quoted ? { parent_id: messageId(group, quoted.message_id) } : {}) },
    ...(quoted?.sender_open_dingtalk_id ? { data: { quoted_sender_id: quoted.sender_open_dingtalk_id } } : {}),
    reply_route: { action: 'message.reply', input: { message_id: id } },
  });
}
