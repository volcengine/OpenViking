// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { createHash } from 'node:crypto';
import { eventMessage } from '../connectors/protocol.mjs';
import { identifier, openid } from './api.mjs';

export function localMessageId(appId, nativeId, msgIdx = '') {
  return `qq:${appId}:${createHash('sha256').update(`${nativeId}\0${msgIdx}`).digest('hex')}`;
}

export function privateSender(message) {
  if (message?.kind !== 'c2c' || message.rawEventType !== 'C2C_MESSAGE_CREATE' || message.senderIsBot === true) return null;
  try { return openid(message.senderId); } catch { return null; }
}

export function normalizeMessage(message, account, state, instance = 'qq') {
  if (privateSender(message) !== account.ownerId) return null;
  const at = Date.parse(message.timestamp);
  if (!Number.isFinite(at) || at < account.activatedAt || at > Date.now() + 30_000) return null;
  let text = typeof message.content === 'string' ? message.content.slice(0, 16_000) : '';
  // Pairing secrets never enter the runtime or conversation archive.
  if (/^vika-bind(?:\s|$)/.test(text.trim())) return null;
  const nativeId = identifier(message.messageId, 'message id');
  const refIdx = message.msgIdx ? identifier(message.msgIdx, 'reference index') : null;
  const id = localMessageId(account.appId, nativeId, refIdx ?? '');
  const media = [];
  if (Array.isArray(message.attachments)) {
    for (const item of message.attachments.slice(0, 10)) {
      if (typeof item?.url !== 'string' || item.url.length > 8192) continue;
      const mimeType = typeof item.content_type === 'string' ? item.content_type.slice(0, 128) : 'application/octet-stream';
      // Voice/video are intentionally outside the first connector version.
      if (/^(audio|video)\//.test(mimeType)) continue;
      if (item.size !== undefined && (!Number.isSafeInteger(item.size) || item.size < 1)) continue;
      media.push({ id: `${id}:${media.length}`, kind: mimeType.startsWith('image/') ? 'image' : 'file',
        url: item.url, mimeType, size: item.size,
        name: typeof item.filename === 'string' ? item.filename.slice(0, 255) : 'attachment' });
    }
  }
  // Unsupported attachment captions must not become text slash commands.
  if (message.attachments?.length && !media.length) return null;
  if (!text.trim() && !media.length) return null;
  text = [text, ...media.map((item) => `[${item.kind} attachment]`)].filter(Boolean).join('\n');
  const parentId = message.refMsgIdx ? state?.byReference(message.refMsgIdx)?.id ?? null : null;
  // raw/messageScene/msgElements may contain signed URLs and auth_token.
  // Only these explicit fields are allowed into the canonical event.
  return { id, nativeId, chatId: account.ownerId, refIdx, at, incoming: true, media,
    envelope: eventMessage('qq', {
      instance, id, kind: 'message.created', occurred_at: at,
      subject: { type: 'conversation', id: account.ownerId }, actor: { id: account.ownerId, type: 'user' },
      conversation: { id: account.ownerId, type: 'p2p', tenant_id: account.appId },
      message: { id, type: media[0]?.kind ?? 'text', parent_id: parentId },
      content: { text, attachments: media.map(({ id: key, kind, name }) => ({ id: key, type: kind, name })) },
      reply_route: { action: 'message.reply', input: { message_id: id } },
    }),
  };
}
