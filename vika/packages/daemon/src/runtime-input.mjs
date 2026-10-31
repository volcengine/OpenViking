// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { lookupMemberName, sanitizeMemberName } from './roster.mjs';

// Runtime-neutral authenticated envelope shared by every adapter. Untrusted
// content stays on one physical line so it cannot forge metadata.

// The submit-item contract shared by every runtime backend: a text line with an
// optional inbound seq and delivery receipt id.
export function eventDeliveryItem(event, seq = event.seq, overrides = {}) {
  return {
    seq, text: formatInboundLine(event),
    conversationKey: event.conversationKey,
    scopeKey: event.scopeKey,
    scope: { kind: event.source, key: event.scopeKey, conversationKey: event.conversationKey },
    chatType: event.kind.startsWith('message.') ? event.chatType : null,
    ...(event.replyRoute ? { target: {
      connector: event.connectorInstance, ...event.replyRoute,
      chatId: event.chatId, threadId: event.threadId, messageId: event.messageId,
    } } : {}),
    ...overrides,
  };
}

// Percent-encodes what would break a tag (whitespace, brackets) and `%` itself,
// so readMetadataTag gives back the exact id a tool argument copied from a tag.
export function metadataTag(value) {
  return String(value).replace(/[\s\[\]%]/g, (character) => encodeURIComponent(character));
}

export function readMetadataTag(value) {
  return decodeURIComponent(value);
}

function eventContent(event) {
  return [event.title, event.content,
    ...(event.attachments ?? []).map((item) => `[attachment type=${metadataTag(item.type)} id=${metadataTag(item.id)}]`),
  ].filter(Boolean).join('\n');
}

export function formatEventLine(event) {
  const fields = [
    `source=${metadataTag(event.source)}`,
    ...(event.connectorInstance !== event.source ? [`instance=${metadataTag(event.connectorInstance)}`] : []),
    `kind=${metadataTag(event.kind)}`,
    // A non-chat event that can be answered (a mail) carries its reply target.
    ...(event.messageId ? [`msg=${metadataTag(event.messageId)}`] : []),
    `event=${metadataTag(event.eventId)}`,
    `subject=${metadataTag(event.subject.type)}:${metadataTag(event.subject.id)}`,
    `time=${isoMinute(event.createTimeMs)}`,
    ...(event.actor ? [`actor=${metadataTag(event.actor.type)}:${metadataTag(event.actor.id)}`] : []),
  ];
  return `[${fields.join(' ')}] ${oneLine(eventContent(event))}`;
}

export function deliveryItem({ text, seq = null, receiptId }) {
  return receiptId === null || receiptId === undefined
    ? { text, seq }
    : { text, seq, receiptId };
}

export function receiptIds(items) {
  return [...new Set(items
    .map((item) => item.receiptId)
    .filter((receiptId) => receiptId !== null && receiptId !== undefined))];
}

export function withReceiptIds(fields, ids) {
  return ids.length > 0 ? { ...fields, receiptIds: ids } : fields;
}
export function shortenId(id) {
  if (!id) return 'unknown';
  const match = /^([a-z]{1,4}_)(.+)$/.exec(id);
  if (match) return match[1] + match[2].slice(0, 8);
  return String(id).slice(0, 11);
}

export function isoMinute(ms) {
  return `${new Date(ms).toISOString().slice(0, 16)}Z`;
}

// Senders that are another bot or app: integrations, alerts, other agents.
export const BOT_SENDERS = new Set(['bot', 'app']);

// Lark names come from the chat roster, or from the event where there is no
// roster (a document comment); other connectors name the sender in the event
// itself.
function fromLabel(chatId, senderId, resolveName = null, source = 'lark', senderName = null) {
  if (source !== 'lark') return sanitizeMemberName(senderName) ?? shortenId(senderId);
  if (resolveName) {
    try {
      const resolved = resolveName(chatId, senderId);
      if (resolved) return resolved;
    } catch { /* fall through to the shared resolver / stub */ }
  }
  return lookupMemberName(chatId, senderId) ?? sanitizeMemberName(senderName) ?? shortenId(senderId);
}

// How a quoted message of the agent's own begins, as one tag value: no
// whitespace, brackets or `=`, so it can neither end the tag nor pass for
// another field.
const QUOTE_CHARS = 60;

function quoteTag(text) {
  const words = String(text ?? '').replace(/[\u0000-\u001f\u007f\u2028\u2029[\]=]/g, ' ').trim().split(/\s+/).join('_');
  const chars = [...words];
  return chars.length > QUOTE_CHARS ? `${chars.slice(0, QUOTE_CHARS).join('')}…` : words;
}

export function chatTag(chatId, threadId) {
  const chat = chatId || 'unknown';
  return threadId ? `${chat} thread=${shortenId(threadId)}` : chat;
}

export function oneLine(text) {
  return String(text ?? '')
    .replace(/\s*[\r\n\u2028\u2029]+\s*/g, ' ⏎ ')
    .replace(/[\u0000-\u001f\u007f]/g, ' ');
}

// Whom a message's @mentions call, by id against the bot's own id on that
// Connector: 'me', 'other' or 'mixed' (the bot and someone else). null when
// nothing is mentioned or the bot's id is unknown, never a guess from names.
export function mentionTarget(mentions, selfId) {
  if (!Array.isArray(mentions) || mentions.length === 0) return null;
  if (typeof selfId !== 'string' || !selfId) return null;
  const me = mentions.some((mention) => mention?.id === selfId);
  const other = mentions.some((mention) => mention?.id !== selfId);
  if (me && other) return 'mixed';
  return me ? 'me' : 'other';
}

const MENTION_TARGETS = new Set(['me', 'other', 'mixed']);

function renderInboundContent(message) {
  let content = eventContent(message);
  for (const mention of message.mentions ?? []) {
    if (typeof mention?.key !== 'string' || !mention.key) continue;
    const label = oneLine(mention.name).trim() || shortenId(mention.id);
    content = content.split(mention.key).join(`@${label}`);
  }
  return oneLine(content);
}

export function formatInboundLine(message, resolveName = null, authenticated = {}) {
  if (message.kind && (!message.kind.startsWith('message.') || !message.chatId || !message.messageId)) {
    return formatEventLine(message);
  }
  const idleForMs = Number(authenticated?.idleForMs);
  const idleTag = Number.isFinite(idleForMs) && idleForMs >= 0
    ? ` idle=${Math.max(1, Math.floor(idleForMs / 1_000))}s`
    : '';
  // A one-time reminder: the last turn ended without a decision on this line.
  const undecidedTag = authenticated?.undecided ? ' undecided=1' : '';
  // The sender is the owner this connector reported at check.
  const ownerTag = authenticated?.owner ? ' owner=1' : '';
  const replyTo = authenticated?.replyTo;
  const quote = replyTo?.kind === 'me' ? quoteTag(replyTo.text) : '';
  const replyTag = replyTo
    ? ` reply_to=${replyTo.kind === 'me'
      ? `me${quote ? ` quote=${quote}` : ''}`
      : replyTo.kind === 'user'
        ? fromLabel(replyTo.chatId ?? message.chatId, replyTo.senderId, resolveName, message.source)
        : shortenId(replyTo.id)}`
    : '';
  const bots = authenticated?.botsSinceHuman;
  const botsTag = Number.isSafeInteger(bots) && bots > 0 ? ` bots_since_human=${bots}` : '';
  const mentionTag = MENTION_TARGETS.has(authenticated?.mention) ? ` mention=${authenticated.mention}` : '';
  const line = `[chat=${chatTag(metadataTag(message.chatId), message.threadId)}${message.external ? ' external=true' : ''} source=${message.source || 'lark'}`
    + (message.connectorInstance && message.connectorInstance !== message.source ? ` instance=${metadataTag(message.connectorInstance)}` : '')
    + ` msg=${metadataTag(message.messageId)} event=${metadataTag(message.eventId)}`
    + (message.messageType === 'card_action' ? ` card=${message.parentId}` : '')
    + ` time=${isoMinute(message.createTimeMs)} from=${fromLabel(message.chatId, message.senderId, resolveName, message.source, message.senderName)}${ownerTag}`
    + `${BOT_SENDERS.has(message.senderType) ? ' sender=bot' : ''}${botsTag}${replyTag}${mentionTag}${idleTag}${undecidedTag}] ${renderInboundContent(message)}`;
  return line;
}

// A quote-reply's target, resolved from local history: 'me' when the sender
// quoted one of the bot's own messages (a strong addressing signal). Plain
// thread membership (parent == thread root) is not a quote and stays untagged.
export function resolveReplyTarget(store, message) {
  const parentId = message.parentId;
  if (!parentId || parentId === message.messageId) return null;
  if (message.threadRootId && parentId === message.threadRootId) return null;
  const author = store.resolveMessageAuthor?.(parentId, message.connectorInstance ?? message.source ?? 'lark');
  return author ?? { kind: 'unknown', id: parentId };
}

