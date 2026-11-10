// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Provider-independent input. Only Connectors decode provider payloads;
// persistence and runtime input never depend on provider_event.
const NAME = /^[a-z][a-z0-9-]{0,63}$/;
const KIND = /^[a-z][a-z0-9_.-]{0,127}$/;

function object(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${label} must be an object`);
  return value;
}

function identifier(value, label, max = 1024) {
  if (typeof value !== 'string' || !value || value.length > max
    || /[\u0000-\u001f\u007f]/.test(value)) throw new Error(`${label} is invalid`);
  return value;
}

function optionalId(value, label) {
  return value == null ? null : identifier(value, label);
}

// A display name as the Connector saw it: untrusted, one line, bounded.
function displayName(value) {
  if (value == null) return null;
  if (typeof value !== 'string') throw new Error('actor name must be text');
  return value.replace(/[\u0000-\u001f\u007f\u2028\u2029]/g, ' ').trim().slice(0, 100) || null;
}

export function conversationKeyFor(instance, conversation) {
  return JSON.stringify([instance, 'conversation', conversation.tenant_id ?? null,
    conversation.id, conversation.thread_id ?? null]);
}

export function eventIdentity(event) {
  return JSON.stringify([event.connectorInstance, event.eventId]);
}

export function normalizeEvent(input) {
  const value = object(input, 'connector event');
  const source = identifier(value.connector, 'connector', 64);
  const instance = identifier(value.instance, 'connector instance', 64);
  if (!NAME.test(source) || !NAME.test(instance)) throw new Error('invalid connector identity');
  const eventId = identifier(value.id, 'event id');
  const kind = identifier(value.kind, 'event kind', 128);
  if (!KIND.test(kind)) throw new Error('invalid event kind');
  if (!Number.isSafeInteger(value.occurred_at) || value.occurred_at < 0
    || value.occurred_at > 8_640_000_000_000_000) throw new Error('invalid event time');
  const subjectInput = object(value.subject, 'event subject');
  const subject = { type: identifier(subjectInput.type, 'subject type', 64),
    id: identifier(subjectInput.id, 'subject id') };
  if (!KIND.test(subject.type)) throw new Error('invalid subject type');
  const actorName = value.actor == null ? null : displayName(object(value.actor, 'actor').name);
  const actor = value.actor == null ? null : {
    id: identifier(value.actor.id, 'actor id'),
    type: identifier(value.actor.type, 'actor type', 64),
    ...(actorName ? { name: actorName } : {}),
  };
  const body = object(value.content ?? {}, 'event content');
  if (body.title != null && typeof body.title !== 'string') throw new Error('event title must be text');
  if (body.text != null && typeof body.text !== 'string') throw new Error('event content must be text');
  const attachments = body.attachments ?? [];
  if (!Array.isArray(attachments) || attachments.length > 100) throw new Error('invalid event attachments');
  for (const attachment of attachments) {
    object(attachment, 'attachment');
    identifier(attachment.id, 'attachment id');
    identifier(attachment.type, 'attachment type', 64);
  }
  const conversation = value.conversation == null ? null : {
    id: identifier(object(value.conversation, 'conversation').id, 'conversation id'),
    type: identifier(value.conversation.type, 'conversation type', 64),
    tenant_id: optionalId(value.conversation.tenant_id, 'tenant id'),
    thread_id: optionalId(value.conversation.thread_id, 'thread id'),
    // People from outside the owner's organization are in it.
    ...(value.conversation.external === true ? { external: true } : {}),
  };
  const mentions = value.message?.mentions ?? [];
  if (!Array.isArray(mentions) || mentions.length > 100) throw new Error('invalid mentions');
  const normalizedMentions = mentions.map((mention) => {
    object(mention, 'mention');
    if (mention.name != null && typeof mention.name !== 'string') throw new Error('mention name must be text');
    return { id: optionalId(mention.id, 'mention id'), key: optionalId(mention.key, 'mention key'),
      name: mention.name ?? null };
  });
  const message = value.message == null ? null : {
    id: identifier(object(value.message, 'message').id, 'message id'),
    type: identifier(value.message.type, 'message type', 64),
    parent_id: optionalId(value.message.parent_id, 'parent message id'),
    root_id: optionalId(value.message.root_id, 'root message id'),
    thread_root_id: optionalId(value.message.thread_root_id, 'thread root id'),
    mentions: normalizedMentions,
  };
  const route = value.reply_route == null ? null : {
    action: identifier(object(value.reply_route, 'reply route').action, 'reply action', 128),
    input: object(value.reply_route.input, 'reply input'),
  };
  if (route && !KIND.test(route.action)) throw new Error('invalid reply action');
  const data = object(value.data ?? {}, 'event data');
  const conversationKey = conversation ? conversationKeyFor(instance, conversation) : null;
  const scopeKey = conversationKey ?? JSON.stringify([instance, subject.type, subject.id]);
  const envelope = {
    protocol_version: value.protocol_version, type: 'EVENT', connector: source, instance,
    id: eventId, kind, occurred_at: value.occurred_at, subject, actor,
    content: { title: body.title ?? '', text: body.text ?? '', attachments },
    conversation, message, reply_route: route, data,
  };
  return {
    envelope, eventId, source, connectorInstance: instance, kind, subject, actor,
    title: body.title ?? '', content: body.text ?? '', attachments, data,
    conversation, message, replyRoute: route, conversationKey, scopeKey,
    createTimeMs: value.occurred_at,
    chatId: conversation?.id ?? null, chatType: conversation?.type ?? null,
    tenantKey: conversation?.tenant_id ?? null, threadId: conversation?.thread_id ?? null,
    messageId: message?.id ?? null, messageType: message?.type ?? null,
    rootId: message?.root_id ?? null, parentId: message?.parent_id ?? null,
    threadRootId: message?.thread_root_id ?? null, mentions: message?.mentions ?? [],
    senderId: actor?.id ?? null, senderType: actor?.type ?? null, senderName: actorName,
    external: conversation?.external === true,
  };
}

export function deterministicFilter(event, config, now = Date.now()) {
  if (event.senderId && config.ignoredSenderIds?.has(event.senderId)) return 'ignored_sender';
  if (event.data.supported === false) return 'unsupported_message_type';
  if (!event.content.trim() && !event.title.trim() && event.attachments.length === 0
    && event.kind === 'message.created') return 'empty_content';
  if (event.createTimeMs < now - config.maxEventAgeMs) return 'stale_event';
  return null;
}
