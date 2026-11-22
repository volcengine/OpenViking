// SPDX-License-Identifier: AGPL-3.0

import { eventMessage } from './protocol.mjs';

// DingTalk ids can contain "/" (standard base64). Vika's tool ids accept
// base64url, so preserve the full provider id in a reversible form.
function routeId(prefix, value) {
  if (typeof value !== 'string' || !value || /[\x00-\x20\x7f]/.test(value)) {
    throw new Error('Invalid DingTalk identifier');
  }
  const encoded = prefix + Buffer.from(value).toString('base64url');
  if (encoded.length > 256) throw new Error('DingTalk identifier exceeds the Vika tool limit');
  return encoded;
}

export function decodeDingTalkChatId(chatId) {
  if (typeof chatId !== 'string' || !/^dtc_[A-Za-z0-9_-]+$/.test(chatId)) {
    throw new Error('DingTalk requires a valid group chat_id');
  }
  const decoded = Buffer.from(chatId.slice(4), 'base64url').toString('utf8');
  if (routeId('dtc_', decoded) !== chatId) throw new Error('Invalid DingTalk group chat_id');
  return decoded;
}

export function normalizeDingTalkEvent(callback, { botId }) {
  const message = typeof callback.data === 'string' ? JSON.parse(callback.data) : callback.data;
  if (!message || typeof message !== 'object' || Array.isArray(message)) {
    throw new Error('Invalid DingTalk callback body');
  }
  if (String(message.conversationType) !== '2' || message.msgtype !== 'text') return null;

  const mentioned = message.isInAtList === true || (
    message.isInAtList == null && typeof message.chatbotUserId === 'string'
    && message.chatbotUserId.length > 0 && Array.isArray(message.atUsers)
    && message.atUsers.some((user) => user?.dingtalkId === message.chatbotUserId)
  );
  if (!mentioned) return null;
  if (typeof botId !== 'string' || !botId) throw new Error('DingTalk bot identity is required');

  const text = typeof message.text?.content === 'string' ? message.text.content.trim() : '';
  if (!text) return null;
  const timestamp = message.createAt;
  if (typeof timestamp !== 'number' && !(typeof timestamp === 'string' && /^\d+$/.test(timestamp))) {
    throw new Error('DingTalk message timestamp is invalid');
  }

  const chatId = routeId('dtc_', message.conversationId);
  const messageId = routeId('dtm_', message.msgId);
  return eventMessage('dingtalk', {
    id: `message:${messageId}`,
    kind: 'message.created',
    occurred_at: Number(timestamp),
    subject: { type: 'message', id: messageId },
    actor: { type: 'user', id: message.senderStaffId || message.senderId },
    content: { text },
    conversation: {
      type: 'group', id: chatId, tenant_id: message.chatbotCorpId || null,
    },
    message: { id: messageId, type: 'text', mentions: [{ id: botId }] },
    reply_route: { action: 'message.reply', input: { message_id: messageId, chat_id: chatId } },
  });
}
