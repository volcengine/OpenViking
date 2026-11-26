// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { eventMessage } from '../connectors/protocol.mjs';

// DingTalk ids are standard base64, and tool ids take no "/", so each one
// travels base64url-encoded. A message id carries its group, as Slack and
// OneBot ids do: a reply needs nothing but the id.
const encode = (value) => Buffer.from(value).toString('base64url');
const decode = (value) => Buffer.from(value, 'base64url').toString('utf8');

export const chatId = (conversationId) => `dtc_${encode(conversationId)}`;
const messageId = (conversationId, msgId) => `dtm_${encode(conversationId)}.${encode(msgId)}`;

// The DingTalk openConversationId of a chat= or msg= id.
export function parseChatId(id) {
  const match = /^dtc_([\w-]+)$/.exec(String(id ?? ''));
  if (!match) throw new Error('DingTalk chat_id must be a dtc_ group id');
  return decode(match[1]);
}

export function parseMessageId(id) {
  const match = /^dtm_([\w-]+)\.[\w-]+$/.exec(String(id ?? ''));
  if (!match) throw new Error('DingTalk message_id must be a dtm_ message id');
  return decode(match[1]);
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
