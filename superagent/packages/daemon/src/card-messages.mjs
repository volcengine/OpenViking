// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { choiceCard } from './actions/content.mjs';
import { normalizeEvent } from './event.mjs';

export function migrateCardMessages(db) {
  db.exec(`CREATE TABLE IF NOT EXISTS lark_cards (
    message_id TEXT PRIMARY KEY,
    profile TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    question_json TEXT,
    expires_at_ms INTEGER,
    answer_event_id TEXT,
    answer_json TEXT
  )`);
}

function findField(value, field, depth = 0) {
  if (!value || typeof value !== 'object' || depth > 8) return null;
  if (typeof value[field] === 'string' && value[field]) return value[field];
  for (const child of Object.values(value)) {
    const found = findField(child, field, depth + 1);
    if (found) return found;
  }
  return null;
}

function cardThreadId(data) {
  return findField(data, 'thread_id');
}

// The scope describes where the card landed, never the decision that asked for
// it: a Weixin message may send a card to a Lark group. `instance` is the
// delivering Connector. `inbound` is an accepted inbound in the destination chat
// on that Connector (the replied-to message, or the decision when it sits
// there); only it may vouch for the thread. `chat` supplies tenant and type.
export function registerCard(db, { messageId, profile, question = null, instance, chatId, inbound = null, chat = null,
  replyInThread, replyTarget, data, now = Date.now() }) {
  if (!/^om_[A-Za-z0-9_-]{8,}$/.test(messageId ?? '')) throw new Error('card delivery returned no valid message id');
  const destination = findField(data, 'chat_id') || chatId;
  if (!/^oc_[A-Za-z0-9_-]{8,}$/.test(destination ?? '')) throw new Error('card destination could not be verified');
  const threadId = replyInThread ? (cardThreadId(data) || inbound?.thread_id) : null;
  if (replyInThread && !threadId) throw new Error('delivered card thread could not be verified');
  const scope = {
    instance,
    conversation: { id: destination, type: chat?.chat_type ?? 'group',
      tenant_id: chat?.tenant_key ?? null, thread_id: threadId },
    threadRootId: replyInThread ? (findField(data, 'root_id') || inbound?.thread_root_id || replyTarget) : null,
  };
  db.prepare(`INSERT INTO lark_cards(message_id, profile, scope_json, question_json, expires_at_ms)
    VALUES (?, ?, ?, ?, ?) ON CONFLICT(message_id) DO NOTHING`).run(
    messageId, profile, JSON.stringify(scope), question ? JSON.stringify(question) : null,
    question ? now + question.timeout_seconds * 1000 : null,
  );
}

// Resolve a canonical interaction against the trusted delivery ledger. Provider
// decoding (including dropping callback tokens) happens in the Lark Connector.
export function resolveCardAction(db, event, profile) {
  const eventId = event.eventId;
  const interaction = event.data.interaction ?? {};
  const messageId = interaction.message_id;
  const chatId = interaction.conversation_id;
  const operator = event.actor?.id;
  if (typeof eventId !== 'string' || !eventId || eventId.length > 256
    || !/^om_[A-Za-z0-9_-]{8,}$/.test(messageId ?? '')
    || !/^ou_[A-Za-z0-9_-]{8,}$/.test(operator ?? '')) throw new Error('invalid card callback identity');
  const record = db.prepare('SELECT * FROM lark_cards WHERE message_id = ? AND profile = ?').get(messageId, profile);
  if (!record) throw new Error('card was not sent through this daemon');
  const scope = JSON.parse(record.scope_json);
  if (scope.instance !== event.connectorInstance) throw new Error('card callback connector does not match its delivery');
  if (chatId && chatId !== scope.conversation.id) throw new Error('card callback chat does not match its delivery');
  const value = interaction.value;
  let question = record.question_json ? JSON.parse(record.question_json) : null;
  let answer = null;
  let content;
  if (question) {
    if (question.answerer_id !== operator) throw new Error('card answerer is not authorized');
    const key = value?.vika_choice;
    answer = question.options.find((option) => option.key === key);
    if (!answer) throw new Error('invalid card choice');
    content = `[card answer] ${JSON.stringify({ question: question.prompt, key: answer.key, label: answer.label })}`;
  } else {
    const { message_id, conversation_id, ...details } = interaction;
    const serialized = JSON.stringify(details);
    if (serialized.length > 12000) throw new Error('card action exceeds the input limit');
    content = `[card action] ${serialized}`;
  }
  return {
    ...normalizeEvent({ ...event.envelope,
      conversation: scope.conversation,
      message: { ...event.envelope.message, parent_id: messageId,
        root_id: scope.threadRootId, thread_root_id: scope.threadRootId },
      content: { text: content },
      reply_route: { action: 'message.reply', input: { message_id: messageId } },
    }),
    question, answer,
  };
}

// Called inside the same transaction as inbound insertion. Different clicks on
// a question settle once; replay of the same event remains safe after a crash.
export function acceptCardAnswer(db, event, now) {
  if (!event.question) return true;
  const result = db.prepare(`UPDATE lark_cards SET answer_event_id = ?, answer_json = ?
    WHERE message_id = ? AND expires_at_ms > ?
      AND (answer_event_id IS NULL OR answer_event_id = ?)`).run(
    event.eventId, JSON.stringify(event.answer), event.parentId, now, event.eventId,
  );
  return Number(result.changes) > 0;
}

// An approval card is settled by ActionService.settleApproval instead.
export function settledChoiceCard(event) {
  return event.question && event.answer && event.question.kind !== 'approval' ? choiceCard(event.question, event.answer) : null;
}
