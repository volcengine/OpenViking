// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { eventMessage } from '../connectors/protocol.mjs';
import { integer } from './api.mjs';

export function localMessageId(botId, chatId, messageId) { return `tg:${botId}:${chatId}:${messageId}`; }

export function privateSender(message) {
  return message?.chat?.type === 'private' && !message.sender_chat && message.from?.is_bot === false
    && Number.isSafeInteger(message.from.id) && message.from.id > 0
    && Number.isSafeInteger(message.chat.id) && message.chat.id > 0;
}

export function normalizeUpdate(update, account, instance = 'telegram') {
  integer(update?.update_id, 'update id');
  const message = update.message;
  if (!privateSender(message) || String(message.from.id) !== account.ownerId
    || String(message.chat.id) !== account.chatId) return null;
  const nativeId = integer(message.message_id, 'message id', 1);
  const at = integer(message.date, 'message timestamp') * 1000;
  if (!Number.isSafeInteger(at) || at < account.activatedAt) return null;
  // /start belongs to pairing, not the model (including old one-time secrets).
  if (typeof message.text === 'string' && /^\/start(?:@[A-Za-z0-9_]+)?(?:\s|$)/.test(message.text)) return null;
  let text = message.text;
  let media = null;
  let item;
  if (Array.isArray(message.photo) && message.photo.length) {
    item = message.photo.at(-1);
    media = { kind: 'image', mimeType: 'image/jpeg', name: 'photo.jpg' };
  } else if (message.document) {
    item = message.document;
    media = { kind: 'file', mimeType: item.mime_type, name: item.file_name };
  }
  if (media) {
    if (typeof item?.file_id !== 'string' || !/^[A-Za-z0-9_-]{1,512}$/.test(item.file_id)) throw new Error('Invalid Telegram media id');
    if (item.file_size !== undefined) integer(item.file_size, 'file size', 1);
    media = { ...media, id: item.file_id, size: item.file_size,
      name: typeof media.name === 'string' ? media.name.slice(0, 255) : null };
    text = [typeof message.caption === 'string' ? message.caption : '', `[${media.kind} attachment]`].filter(Boolean).join('\n');
  }
  if (typeof text !== 'string' || !text.trim()) return null;
  const id = localMessageId(account.botId, account.chatId, nativeId);
  const parent = message.reply_to_message;
  const parentId = parent?.chat?.id === message.chat.id && Number.isSafeInteger(parent.message_id) && parent.message_id > 0
    ? localMessageId(account.botId, account.chatId, parent.message_id) : null;
  return { id, nativeId, chatId: account.chatId, media, envelope: eventMessage('telegram', {
    instance, id: `${account.botId}:${update.update_id}`, kind: 'message.created', occurred_at: at,
    subject: { type: 'conversation', id: account.chatId }, actor: { id: account.ownerId, type: 'user' },
    conversation: { id: account.chatId, type: 'p2p', tenant_id: account.botId },
    message: { id, type: media?.kind ?? 'text', parent_id: parentId },
    content: { text, attachments: media ? [{ id: media.id, type: media.kind, name: media.name }] : [] },
    reply_route: { action: 'message.reply', input: { message_id: id } },
  }) };
}
