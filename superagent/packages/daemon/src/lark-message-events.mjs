// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { normalizeEvent } from './event.mjs';

export const LARK_MESSAGE_EVENT_KINDS = new Set(['message.recalled', 'message.reaction.created', 'message.reaction.deleted']);

// Places a Lark recall or reaction in the conversation of the message it is
// about, from the daemon's own history: a message someone sent here, or one
// the bot sent. Reactions name no chat, and a recall's thread is only known
// here. One about an unknown message, the bot's own reaction, or the recall of
// the bot's own message is not shown to the Agent.
export function resolveLarkMessageEvent(db, event) {
  const target = event.data.target_message_id;
  const instance = event.connectorInstance;
  const ignore = () => ({ ...event, data: { ...event.data, supported: false } });
  if (event.actor?.type === 'app') return ignore();
  const inbound = db.prepare(`SELECT chat_id, chat_type, tenant_key, thread_id, thread_root_id, sender_id, sender_type
    FROM connector_events WHERE connector_instance = ? AND message_id = ? AND kind = 'message.created'
    ORDER BY seq DESC LIMIT 1`).get(instance, target);
  let place = inbound;
  if (!place) {
    const own = db.prepare(`SELECT chat_id, thread_id FROM cognition_journal
      WHERE COALESCE(json_extract(detail_json, '$.connectorInstance'), json_extract(detail_json, '$.source'), 'lark') = ?
        AND message_id = ? AND actor = 'me' AND chat_id IS NOT NULL ORDER BY seq DESC LIMIT 1`).get(instance, target);
    if (!own || event.kind === 'message.recalled') return ignore();
    const chat = db.prepare(`SELECT chat_type, tenant_key FROM connector_events
      WHERE connector_instance = ? AND chat_id = ? AND kind = 'message.created' ORDER BY seq DESC LIMIT 1`).get(instance, own.chat_id);
    if (!chat) return ignore();
    place = { ...chat, chat_id: own.chat_id, thread_id: own.thread_id, thread_root_id: null };
  }
  if (event.data.chat_id && event.data.chat_id !== place.chat_id) return ignore();
  // A recall names no one; its author is who recalled it unless an admin did.
  const actor = event.actor ?? (event.data.recall_type === 'message_owner' && place.sender_id
    ? { id: place.sender_id, type: place.sender_type || 'user' } : null);
  return normalizeEvent({
    ...event.envelope,
    actor,
    conversation: { id: place.chat_id, type: place.chat_type, tenant_id: place.tenant_key ?? null,
      thread_id: place.thread_id ?? null },
    message: { ...event.envelope.message, parent_id: target, root_id: place.thread_root_id ?? null,
      thread_root_id: place.thread_root_id ?? null },
    reply_route: event.kind === 'message.recalled' ? null : { action: 'message.reply', input: { message_id: target } },
  });
}
