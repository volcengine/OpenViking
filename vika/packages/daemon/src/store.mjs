// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { opaqueRef } from './logger.mjs';
import {
  activityDefinition,
  cleanActivityMetadata,
  cleanActivityText,
  reasonLabel,
} from './activity.mjs';
import { MeetingStore, migrateMeetingStore } from './meeting/store.mjs';
import { migrateCardMessages, acceptCardAnswer } from './card-messages.mjs';
import { migrateApprovals, pruneApprovals } from './approvals.mjs';
import { normalizeEvent, eventIdentity } from './event.mjs';

function number(value) {
  return typeof value === 'bigint' ? Number(value) : value;
}

function rowToInbound(row) {
  if (!row) return null;
  return {
    ...normalizeEvent(JSON.parse(row.event_json)),
    seq: number(row.seq),
    messageId: row.message_id,
    eventId: row.event_id,
    conversationKey: row.conv_key,
    scopeKey: row.scope_key,
    chatId: row.chat_id,
    chatType: row.chat_type,
    messageType: row.message_type,
    source: row.source || 'lark',
    senderId: row.sender_id,
    senderType: row.sender_type,
    rootId: row.root_id,
    parentId: row.parent_id,
    threadId: row.thread_id,
    threadRootId: row.thread_root_id,
    content: row.content,
    mentions: JSON.parse(row.mentions_json || '[]'),
    createTimeMs: number(row.create_time_ms),
    receivedAtMs: number(row.received_at_ms),
    status: row.status,
    filterReason: row.filter_reason,
    turnId: row.turn_id === null ? null : number(row.turn_id),
  };
}

function rowToChatSource(row) {
  if (!row) return null;
  return {
    chatId: row.chat_id,
    sourceRef: row.source_ref,
    chatType: row.chat_type,
    name: row.name,
    lookupStatus: row.lookup_status,
    failureCount: number(row.failure_count),
    lastLookupAtMs: row.last_lookup_at_ms === null ? null : number(row.last_lookup_at_ms),
    nextLookupAtMs: number(row.next_lookup_at_ms),
    createdAtMs: number(row.created_at_ms),
    updatedAtMs: number(row.updated_at_ms),
  };
}

function rowToCognitionJournal(row) {
  if (!row) return null;
  return {
    seq: number(row.seq),
    epoch: number(row.epoch),
    kind: row.kind,
    status: row.status,
    atMs: number(row.at_ms),
    inboundSeq: row.inbound_seq === null ? null : number(row.inbound_seq),
    chatId: row.chat_id,
    threadId: row.thread_id,
    messageId: row.message_id,
    actor: row.actor,
    text: row.text,
    metadata: parseJsonObject(row.detail_json),
  };
}

function parseJsonObject(value) {
  try {
    const parsed = JSON.parse(value || '{}');
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function maskPublicIdentifiers(value) {
  return cleanActivityText(value, 4000)
    .replace(/\b(?:om|oc|ou|on|omt|cli)_[A-Za-z0-9_-]{6,}\b/g, '[LARK_ID]');
}

function chatIdFromConversationKey(value) {
  if (!value) return null;
  try {
    const parts = JSON.parse(value);
    if (Array.isArray(parts) && parts[1] === 'conversation') return parts[3];
  } catch {}
  const match = String(value).match(/:([^:]+):(?:main|thread:[^:]+)$/);
  return match?.[1] || null;
}

function sourceRefFromConversationKey(value) {
  return opaqueRef(chatIdFromConversationKey(value)) || opaqueRef(value);
}

const PUBLIC_METADATA_KEYS = new Set([
  'action', 'attempt', 'batchSize', 'bytes', 'chatType',
  'code', 'codexVersion', 'deliveryMode', 'dryRun', 'exitCode',
  'durationMs', 'expired', 'filterReason', 'inboundSeqs', 'interruptedTurns', 'itemType', 'larkVersion',
  'connector', 'source', 'eventKind',
  'messageType', 'outboxes', 'reason', 'reasonCode', 'removed',
  'requestedDeliveryMode', 'signal', 'toolLabel', 'toolName', 'toolServer', 'toolStatus',
  'tool', 'via', 'runtime', 'decisionSeq', 'newerSeq',
  // Full-context fields: what the model saw, what tools received/returned, cost.
  'cachedInputTokens', 'contextWindow', 'costUsd', 'cwd', 'delivery', 'effort', 'error',
  'historyChars', 'input', 'inputTokens', 'latencyMs', 'model', 'output', 'outputTokens', 'reasoningTokens',
  'status', 'terminal', 'thinking', 'toolId',
]);
const PUBLIC_NESTED_METADATA_KEYS = new Set(['conversations', 'inbound', 'outbox']);

function publicMetadataValue(value) {
  if (typeof value === 'string') return maskPublicIdentifiers(value);
  if (Array.isArray(value)) return value.slice(0, 50).map(publicMetadataValue);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).slice(0, 50)
        .filter(([key]) => PUBLIC_NESTED_METADATA_KEYS.has(key))
        .map(([key, child]) => [key, publicMetadataValue(child)]),
    );
  }
  return value;
}

function publicMetadata(value) {
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => PUBLIC_METADATA_KEYS.has(key))
      .map(([key, child]) => [key, publicMetadataValue(child)]),
  );
}

function rowToPublicActivity(row) {
  const sourceRef = row.source_ref || (row.conv_key ? sourceRefFromConversationKey(row.conv_key) : 'system');
  const sourceType = row.source_type || (row.conv_key ? null : 'system');
  return {
    id: number(row.id),
    kind: row.kind,
    category: row.category,
    severity: row.severity,
    priority: number(row.priority),
    title: maskPublicIdentifiers(row.title),
    summary: maskPublicIdentifiers(row.summary),
    conversationRef: opaqueRef(row.conv_key),
    sourceRef,
    sourceType,
    sourceName: row.source_name ? maskPublicIdentifiers(row.source_name) : null,
    turnId: row.turn_id === null ? null : number(row.turn_id),
    inboundSeq: row.inbound_seq === null ? null : number(row.inbound_seq),
    outboxId: row.outbox_id === null ? null : number(row.outbox_id),
    status: row.status,
    metadata: publicMetadata(parseJsonObject(row.detail_json)),
    createdAtMs: number(row.occurred_at_ms),
  };
}

export class Store {
  constructor(databasePath, logger) {
    fs.mkdirSync(path.dirname(databasePath), { recursive: true, mode: 0o700 });
    this.db = new DatabaseSync(databasePath);
    try { fs.chmodSync(databasePath, 0o600); } catch {}
    this.logger = logger;
    this.db.exec('PRAGMA journal_mode = WAL');
    this.db.exec('PRAGMA synchronous = FULL');
    this.db.exec('PRAGMA foreign_keys = ON');
    this.db.exec('PRAGMA busy_timeout = 5000');
    this.migrate();
    this.prepare();
    this.meetings = new MeetingStore(this.db, (fn) => this.transaction(fn));
  }

  migrate() {
    migrateCardMessages(this.db);
    migrateApprovals(this.db);
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
      );

      CREATE TABLE IF NOT EXISTS conversations (
        conv_key TEXT PRIMARY KEY,
        session_id TEXT,
        policy_hash TEXT,
        pending_draft_json TEXT,
        last_ingress_seq INTEGER NOT NULL DEFAULT 0,
        created_at_ms INTEGER NOT NULL,
        updated_at_ms INTEGER NOT NULL
      );

      CREATE TABLE IF NOT EXISTS connector_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        connector_instance TEXT NOT NULL,
        event_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        subject_type TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        scope_key TEXT NOT NULL,
        event_json TEXT NOT NULL,
        message_id TEXT,
        conv_key TEXT,
        chat_id TEXT,
        chat_type TEXT,
        message_type TEXT,
        tenant_key TEXT,
        source TEXT NOT NULL,
        sender_id TEXT,
        sender_type TEXT,
        root_id TEXT,
        parent_id TEXT,
        thread_id TEXT,
        thread_root_id TEXT,
        content TEXT NOT NULL,
        mentions_json TEXT NOT NULL,
        create_time_ms INTEGER NOT NULL,
        received_at_ms INTEGER NOT NULL,
        status TEXT NOT NULL,
        filter_reason TEXT,
        turn_id INTEGER,
        last_error TEXT,
        FOREIGN KEY (conv_key) REFERENCES conversations(conv_key),
        UNIQUE(connector_instance, event_id)
      );

      CREATE INDEX IF NOT EXISTS connector_events_conv_status_seq
        ON connector_events(conv_key, status, seq);
      CREATE INDEX IF NOT EXISTS connector_events_scope_status_seq
        ON connector_events(scope_key, status, seq);
      CREATE INDEX IF NOT EXISTS connector_events_event
        ON connector_events(event_id);
      CREATE INDEX IF NOT EXISTS connector_events_received_at
        ON connector_events(received_at_ms);
      CREATE INDEX IF NOT EXISTS connector_events_chat
        ON connector_events(chat_id, seq);
      CREATE INDEX IF NOT EXISTS connector_events_message
        ON connector_events(connector_instance, message_id);
      CREATE INDEX IF NOT EXISTS connector_events_subject
        ON connector_events(connector_instance, subject_type, subject_id, seq);
      CREATE INDEX IF NOT EXISTS connector_events_message_seq
        ON connector_events(message_id, seq);

      -- Every external side effect, from any runtime or the daemon itself, is
      -- reserved here before its Connector is invoked. The journal row with
      -- dedupe_key outbox:<id> is written and settled in the same transaction.
      CREATE TABLE IF NOT EXISTS outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        connector TEXT NOT NULL,
        action TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '',
        caller TEXT NOT NULL,
        status TEXT NOT NULL,
        attempt_count INTEGER NOT NULL DEFAULT 1,
        chat_id TEXT,
        conv_key TEXT,
        inbound_seq INTEGER,
        remote_message_id TEXT,
        result_json TEXT,
        last_error TEXT,
        created_at_ms INTEGER NOT NULL,
        updated_at_ms INTEGER NOT NULL,
        sent_at_ms INTEGER,
        UNIQUE(connector, idempotency_key)
      );

      CREATE INDEX IF NOT EXISTS outbox_status_updated
        ON outbox(status, updated_at_ms);
      CREATE INDEX IF NOT EXISTS outbox_remote_message
        ON outbox(remote_message_id);

      -- One model decision per accepted inbound event: a final delivery
      -- ('delivered') or an explicit stay_silent ('silent'). Not a side effect:
      -- no outbox or journal row, never synced to OpenViking. A decision on the
      -- newest input of a scope covers the earlier ones there.
      CREATE TABLE IF NOT EXISTS decisions (
        connector_instance TEXT NOT NULL,
        event_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        scope_key TEXT NOT NULL,
        outcome TEXT NOT NULL,
        decided_at_ms INTEGER NOT NULL,
        PRIMARY KEY (connector_instance, event_id)
      );

      CREATE INDEX IF NOT EXISTS decisions_scope_seq
        ON decisions(scope_key, seq);

      CREATE TABLE IF NOT EXISTS activities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_key TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL,
        category TEXT NOT NULL,
        severity TEXT NOT NULL,
        priority INTEGER NOT NULL,
        title TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '',
        conv_key TEXT,
        source_ref TEXT,
        source_type TEXT,
        source_name TEXT,
        turn_id INTEGER,
        inbound_seq INTEGER,
        outbox_id INTEGER,
        status TEXT,
        detail_json TEXT NOT NULL DEFAULT '{}',
        occurred_at_ms INTEGER NOT NULL,
        recorded_at_ms INTEGER NOT NULL
      );

      CREATE INDEX IF NOT EXISTS activities_time
        ON activities(occurred_at_ms DESC, id DESC);
      CREATE INDEX IF NOT EXISTS activities_priority
        ON activities(priority DESC, id DESC);
      CREATE INDEX IF NOT EXISTS activities_kind
        ON activities(kind, id DESC);
      CREATE INDEX IF NOT EXISTS activities_conversation
        ON activities(conv_key, id DESC);
      CREATE INDEX IF NOT EXISTS activities_turn
        ON activities(turn_id, id);
      CREATE INDEX IF NOT EXISTS activities_inbound
        ON activities(inbound_seq, id);

      CREATE TABLE IF NOT EXISTS chat_sources (
        chat_id TEXT PRIMARY KEY,
        source_ref TEXT NOT NULL,
        chat_type TEXT NOT NULL,
        name TEXT,
        lookup_status TEXT NOT NULL DEFAULT 'PENDING',
        failure_count INTEGER NOT NULL DEFAULT 0,
        last_lookup_at_ms INTEGER,
        next_lookup_at_ms INTEGER NOT NULL DEFAULT 0,
        created_at_ms INTEGER NOT NULL,
        updated_at_ms INTEGER NOT NULL
      );

      CREATE INDEX IF NOT EXISTS chat_sources_lookup
        ON chat_sources(chat_type, next_lookup_at_ms);

      -- Canonical, daemon-owned observable cognition shared by every chat and
      -- every model runtime. Runtime-native transcripts stay opaque; a hard
      -- runtime switch starts one fresh target session and catches it up from
      -- this ordered journal instead of reviving a stale per-runtime session.
      CREATE TABLE IF NOT EXISTS cognition_journal (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        epoch INTEGER NOT NULL,
        kind TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'COMMITTED',
        at_ms INTEGER NOT NULL,
        inbound_seq INTEGER,
        chat_id TEXT,
        thread_id TEXT,
        message_id TEXT,
        actor TEXT,
        text TEXT NOT NULL DEFAULT '',
        runtime_token TEXT,
        dedupe_key TEXT UNIQUE,
        detail_json TEXT NOT NULL DEFAULT '{}'
      );

      CREATE INDEX IF NOT EXISTS cognition_journal_epoch_seq
        ON cognition_journal(epoch, seq);
      CREATE INDEX IF NOT EXISTS cognition_journal_runtime_token
        ON cognition_journal(runtime_token, seq);

      -- A prior sidecar may have died after Lark accepted an action but before
      -- its local commit. Preserve that ambiguity for catch-up; never silently
      -- treat it as failed and invite a duplicate send.
      UPDATE cognition_journal SET status = 'UNCERTAIN' WHERE status = 'PENDING';
      UPDATE outbox SET status = 'UNCERTAIN' WHERE status = 'PENDING';
    `);
    migrateMeetingStore(this.db);
    this.db.exec(`
      CREATE INDEX IF NOT EXISTS activities_source
        ON activities(source_ref, id DESC)
    `);
  }

  prepare() {
    this.insertConversation = this.db.prepare(`
      INSERT INTO conversations(conv_key, created_at_ms, updated_at_ms)
      VALUES (?, ?, ?)
      ON CONFLICT(conv_key) DO NOTHING
    `);
    this.insertInbound = this.db.prepare(`
      INSERT OR IGNORE INTO connector_events(
        connector_instance, kind, subject_type, subject_id, scope_key, event_json, tenant_key,
        message_id, event_id, conv_key, chat_id, chat_type, message_type,
        source, sender_id, sender_type, root_id, parent_id, thread_id, thread_root_id,
        content, mentions_json, create_time_ms, received_at_ms,
        status, filter_reason
      ) VALUES (${Array(26).fill('?').join(', ')})
    `);
    this.getInboundByMessage = this.db.prepare('SELECT * FROM connector_events WHERE connector_instance = ? AND message_id = ? ORDER BY seq DESC LIMIT 1');
    this.getInboundByEvent = this.db.prepare('SELECT * FROM connector_events WHERE connector_instance = ? AND event_id = ?');
    this.getJournalActorByMessage = this.db.prepare(
      "SELECT actor, text FROM cognition_journal WHERE COALESCE(json_extract(detail_json, '$.connectorInstance'), json_extract(detail_json, '$.source'), 'lark') = ? AND message_id = ? ORDER BY seq DESC LIMIT 1",
    );
    this.getMetaStatement = this.db.prepare('SELECT value FROM meta WHERE key = ?');
    this.setMetaStatement = this.db.prepare(`
      INSERT INTO meta(key, value) VALUES (?, ?)
      ON CONFLICT(key) DO UPDATE SET value = excluded.value
    `);
    this.deleteGlobalSessionStatement = this.db.prepare(
      `DELETE FROM meta WHERE key IN (
        'global_session_id', 'global_policy_hash', 'global_session_runtime',
        'global_session_epoch', 'global_session_synced_through_seq',
        'global_session_ready'
      )`,
    );
    this.insertJournalStatement = this.db.prepare(`
      INSERT OR IGNORE INTO cognition_journal(
        epoch, kind, status, at_ms, inbound_seq, chat_id, thread_id,
        message_id, actor, text, runtime_token, dedupe_key, detail_json
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);
    this.insertActivityStatement = this.db.prepare(`
      INSERT OR IGNORE INTO activities(
        event_key, kind, category, severity, priority, title, summary,
        conv_key, source_ref, source_type, source_name, turn_id, inbound_seq, outbox_id,
        status, detail_json, occurred_at_ms, recorded_at_ms
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);
    this.getActivitySourceByInboundStatement = this.db.prepare(`
      SELECT connector_events.chat_id, connector_events.chat_type, connector_events.source,
             connector_events.connector_instance, connector_events.subject_type, connector_events.subject_id,
             chat_sources.name AS source_name
      FROM connector_events
      LEFT JOIN chat_sources ON chat_sources.chat_id = connector_events.chat_id AND connector_events.source = 'lark'
      WHERE connector_events.seq = ?
    `);
    this.getActivitySourceByOutboxStatement = this.db.prepare(`
      SELECT outbox.chat_id, connector_events.chat_type, chat_sources.name AS source_name
      FROM outbox
      LEFT JOIN connector_events ON connector_events.seq = outbox.inbound_seq
      LEFT JOIN chat_sources ON chat_sources.chat_id = outbox.chat_id
      WHERE outbox.id = ?
    `);
    this.getChatSourceStatement = this.db.prepare(`
      SELECT * FROM chat_sources WHERE chat_id = ?
    `);
    this.upsertChatSourceStatement = this.db.prepare(`
      INSERT INTO chat_sources(
        chat_id, source_ref, chat_type, lookup_status, failure_count,
        next_lookup_at_ms, created_at_ms, updated_at_ms
      ) VALUES (?, ?, ?, 'PENDING', 0, 0, ?, ?)
      ON CONFLICT(chat_id) DO UPDATE SET
        source_ref = excluded.source_ref,
        chat_type = excluded.chat_type,
        updated_at_ms = MAX(chat_sources.updated_at_ms, excluded.updated_at_ms)
    `);
    this.resolveChatSourceStatement = this.db.prepare(`
      UPDATE chat_sources
      SET name = ?, lookup_status = 'RESOLVED', failure_count = 0,
          last_lookup_at_ms = ?, next_lookup_at_ms = ?, updated_at_ms = ?
      WHERE chat_id = ?
    `);
    this.deferChatSourceStatement = this.db.prepare(`
      UPDATE chat_sources
      SET lookup_status = ?, failure_count = failure_count + 1,
          last_lookup_at_ms = ?, next_lookup_at_ms = ?, updated_at_ms = ?
      WHERE chat_id = ?
    `);
    this.updateActivitiesSourceNameStatement = this.db.prepare(`
      UPDATE activities SET source_name = ? WHERE source_ref = ?
    `);
  }

  resolveActivitySource(activity) {
    let row = null;
    if (activity.inboundSeq !== undefined && activity.inboundSeq !== null) {
      row = this.getActivitySourceByInboundStatement.get(activity.inboundSeq);
    }
    if (row && (row.source !== 'lark' || !row.chat_id)) {
      return {
        sourceRef: opaqueRef(JSON.stringify([row.connector_instance, row.subject_type, row.subject_id])),
        sourceType: ['p2p', 'group'].includes(row.chat_type) ? row.chat_type : 'event',
        sourceName: null,
      };
    }
    if (!row?.chat_id && activity.outboxId !== undefined && activity.outboxId !== null) {
      row = this.getActivitySourceByOutboxStatement.get(activity.outboxId);
    }
    if (!row?.chat_id) {
      const chatId = chatIdFromConversationKey(activity.conversationKey);
      const source = chatId ? this.getChatSourceStatement.get(chatId) : null;
      return {
        sourceRef: sourceRefFromConversationKey(activity.conversationKey) || null,
        sourceType: ['p2p', 'group'].includes(
          activity.sourceType || activity.chatType || activity.metadata?.chatType,
        )
          ? (activity.sourceType || activity.chatType || activity.metadata?.chatType)
          : null,
        sourceName: source?.name ?? null,
      };
    }
    return {
      sourceRef: opaqueRef(row.chat_id),
      sourceType: ['p2p', 'group'].includes(row.chat_type) ? row.chat_type : null,
      sourceName: row.source_name ?? null,
    };
  }

  recordActivity(activity, recordedAtMs = Date.now()) {
    if (!activity?.eventKey || !activity?.kind) {
      throw new Error('activity requires eventKey and kind');
    }
    const definition = activityDefinition(activity.kind);
    const metadata = cleanActivityMetadata(activity.metadata);
    const source = this.resolveActivitySource(activity);
    const result = this.insertActivityStatement.run(
      cleanActivityText(activity.eventKey, 500),
      activity.kind,
      activity.category ?? definition.category,
      activity.severity ?? definition.severity,
      activity.priority ?? definition.priority,
      cleanActivityText(activity.title ?? definition.title, 200),
      cleanActivityText(activity.summary, 4000),
      activity.conversationKey ?? null,
      source.sourceRef,
      source.sourceType,
      source.sourceName,
      activity.turnId ?? null,
      activity.inboundSeq ?? null,
      activity.outboxId ?? null,
      activity.status ?? null,
      JSON.stringify(metadata),
      activity.occurredAtMs ?? recordedAtMs,
      recordedAtMs,
    );
    return number(result.changes) > 0;
  }

  rememberChatSource(chatId, chatType, now = Date.now()) {
    if (!chatId || !['p2p', 'group'].includes(chatType)) return false;
    const result = this.upsertChatSourceStatement.run(
      chatId,
      opaqueRef(chatId),
      chatType,
      now,
      now,
    );
    return number(result.changes) > 0;
  }

  chatNameLookupCandidate(chatId, now = Date.now()) {
    const row = this.getChatSourceStatement.get(chatId);
    if (!row || row.chat_type !== 'group' || number(row.next_lookup_at_ms) > now) return null;
    return rowToChatSource(row);
  }

  dueChatNameLookups(now = Date.now(), limit = 20) {
    const safeLimit = Math.max(1, Math.min(100, Number(limit) || 20));
    return this.db.prepare(`
      SELECT * FROM chat_sources
      WHERE chat_type = 'group' AND next_lookup_at_ms <= ?
      ORDER BY next_lookup_at_ms, updated_at_ms
      LIMIT ?
    `).all(now, safeLimit).map(rowToChatSource);
  }

  saveChatSourceName(chatId, name, nextLookupAtMs, now = Date.now()) {
    const safeName = cleanActivityText(name, 200);
    if (!safeName) return false;
    return this.transaction(() => {
      const source = this.getChatSourceStatement.get(chatId);
      if (!source || source.chat_type !== 'group') return false;
      const updated = this.resolveChatSourceStatement.run(
        safeName,
        now,
        nextLookupAtMs,
        now,
        chatId,
      );
      if (number(updated.changes) === 0) return false;
      this.updateActivitiesSourceNameStatement.run(safeName, source.source_ref);
      return true;
    });
  }

  deferChatSourceName(chatId, status, nextLookupAtMs, now = Date.now()) {
    const safeStatus = status === 'EMPTY' ? 'EMPTY' : 'RETRY';
    return number(this.deferChatSourceStatement.run(
      safeStatus,
      now,
      nextLookupAtMs,
      now,
      chatId,
    ).changes) > 0;
  }

  closeRunningSubscriberActivities(status = 'STOPPED') {
    const safeStatus = status === 'FAILED' ? 'FAILED' : 'STOPPED';
    return number(this.db.prepare(`
      UPDATE activities SET status = ?
      WHERE kind = 'system.subscriber_started' AND status = 'RUNNING'
    `).run(safeStatus).changes);
  }

  // Cognition epochs are the durable "sessions" the Activity page can pick:
  // /new advances the epoch; model/effort changes preserve it. Native ids stay
  // out of the public payload; the UI only gets epoch + time window + counts.
  listActivitySessions() {
    const cognition = this.getCognitionState();
    const journalRows = this.db.prepare(`
      SELECT epoch AS epoch,
             MIN(at_ms) AS start_ms,
             MAX(at_ms) AS last_ms,
             COUNT(*) AS journal_count
      FROM cognition_journal
      GROUP BY epoch
      ORDER BY epoch ASC
    `).all();
    const byEpoch = new Map();
    for (const row of journalRows) {
      byEpoch.set(number(row.epoch), {
        startMs: number(row.start_ms),
        lastMs: number(row.last_ms),
        journalCount: number(row.journal_count),
      });
    }
    if (!byEpoch.has(cognition.epoch)) {
      const previous = [...byEpoch.keys()].sort((left, right) => left - right).at(-1);
      const previousLast = previous != null ? byEpoch.get(previous).lastMs : null;
      const latestActivity = number(
        this.db.prepare('SELECT MAX(occurred_at_ms) AS t FROM activities').get()?.t,
      );
      const startMs = previousLast != null
        ? previousLast + 1
        : (Number.isFinite(latestActivity) ? latestActivity : Date.now());
      byEpoch.set(cognition.epoch, {
        startMs,
        lastMs: startMs,
        journalCount: 0,
      });
    }

    const firstActivity = number(
      this.db.prepare('SELECT MIN(occurred_at_ms) AS t FROM activities').get()?.t,
    );
    const epochs = [...byEpoch.keys()].sort((left, right) => left - right);
    if (epochs.length > 0 && Number.isFinite(firstActivity)) {
      const first = byEpoch.get(epochs[0]);
      if (firstActivity < first.startMs) first.startMs = firstActivity;
    }

    const countOpen = this.db.prepare(
      'SELECT COUNT(*) AS count FROM activities WHERE occurred_at_ms >= ?',
    );
    const countClosed = this.db.prepare(
      'SELECT COUNT(*) AS count FROM activities WHERE occurred_at_ms >= ? AND occurred_at_ms < ?',
    );
    const sessions = [];
    for (let index = 0; index < epochs.length; index += 1) {
      const epoch = epochs[index];
      const current = epoch === cognition.epoch;
      const startMs = byEpoch.get(epoch).startMs;
      const next = epochs[index + 1];
      const endMs = next != null ? byEpoch.get(next).startMs : null;
      const activityCount = endMs == null
        ? number(countOpen.get(startMs).count)
        : number(countClosed.get(startMs, endMs).count);
      sessions.push({
        epoch,
        current,
        startMs,
        endMs,
        activityCount,
        runtime: current ? cognition.runtime : null,
      });
    }
    sessions.reverse();
    return sessions;
  }

  activitySessionWindow(epoch) {
    if (!Number.isSafeInteger(epoch) || epoch < 1) return null;
    return this.listActivitySessions().find((session) => session.epoch === epoch) ?? null;
  }

  listActivities({ limit = 100, beforeId = null, afterId = null, epoch = null } = {}) {
    const safeLimit = Math.max(1, Math.min(100, Number(limit) || 100));
    if (afterId != null) {
      // Live tail: oldest-first after the cursor so a poller never skips rows.
      const rows = this.db.prepare(`
        SELECT * FROM activities WHERE id > ? ORDER BY id ASC LIMIT ?
      `).all(afterId, safeLimit + 1);
      const visible = rows.slice(0, safeLimit).reverse();
      return { activities: visible.map(rowToPublicActivity), nextCursor: null, hasNewer: rows.length > safeLimit };
    }
    let startMs = null;
    let endMs = null;
    if (epoch != null) {
      const session = this.activitySessionWindow(epoch);
      if (!session) {
        return { activities: [], nextCursor: null };
      }
      startMs = session.startMs;
      endMs = session.endMs;
    }
    const rows = this.db.prepare(`
      SELECT * FROM activities
      WHERE (? IS NULL OR id < ?)
        AND (? IS NULL OR occurred_at_ms >= ?)
        AND (? IS NULL OR occurred_at_ms < ?)
      ORDER BY id DESC
      LIMIT ?
    `).all(
      beforeId,
      beforeId,
      startMs,
      startMs,
      endMs,
      endMs,
      safeLimit + 1,
    );
    const hasMore = rows.length > safeLimit;
    const visible = hasMore ? rows.slice(0, safeLimit) : rows;
    return {
      activities: visible.map(rowToPublicActivity),
      nextCursor: hasMore ? number(visible.at(-1).id) : null,
    };
  }

  activityOverview(now = Date.now()) {
    const start = new Date(now);
    start.setHours(0, 0, 0, 0);
    const startMs = start.getTime();
    const scalar = (sql, ...params) => number(this.db.prepare(sql).get(...params).count);
    return {
      attention: scalar(`SELECT COUNT(*) AS count FROM outbox WHERE status = 'UNCERTAIN'`),
      receivedToday: scalar(`SELECT COUNT(*) AS count FROM connector_events WHERE received_at_ms >= ?`, startMs),
      sentToday: scalar(`SELECT COUNT(*) AS count FROM outbox WHERE status = 'SENT' AND sent_at_ms >= ?`, startMs),
    };
  }

  transaction(fn) {
    this.db.exec('BEGIN IMMEDIATE');
    try {
      const result = fn();
      this.db.exec('COMMIT');
      return result;
    } catch (error) {
      try { this.db.exec('ROLLBACK'); } catch {}
      throw error;
    }
  }

  // One event ledger for all sources. Chat columns are nullable indexes into the
  // canonical envelope, not a separate message ledger. No provider raw is stored.
  ingest(event, filterReason = null, now = Date.now(), { journal = true } = {}) {
    return this.transaction(() => {
      const existing = this.getInboundByEvent.get(event.connectorInstance, event.eventId);
      if (existing) return { inserted: false, inbound: rowToInbound(existing), filterReason: existing.filter_reason };
      if (!filterReason && event.kind === 'message.action' && !acceptCardAnswer(this.db, event, now)) {
        filterReason = 'card_already_answered_or_expired';
      }
      if (event.conversationKey) this.insertConversation.run(event.conversationKey, now, now);
      if (event.source === 'lark') this.rememberChatSource(event.chatId, event.chatType, now);
      const status = filterReason ? 'RULE_IGNORED' : 'DONE';
      const envelope = filterReason ? { ...event.envelope,
        content: { title: '', text: '', attachments: [] }, data: {},
        message: event.envelope.message ? { ...event.envelope.message, mentions: [] } : null,
      } : event.envelope;
      const result = this.insertInbound.run(
        event.connectorInstance, event.kind, event.subject.type, event.subject.id,
        event.scopeKey, JSON.stringify(envelope), event.tenantKey,
        event.messageId,
        event.eventId,
        event.conversationKey,
        event.chatId,
        event.chatType,
        event.messageType,
        event.source || 'lark',
        event.senderId,
        event.senderType,
        event.rootId,
        event.parentId,
        event.threadId,
        event.threadRootId,
        filterReason ? '' : event.content,
        JSON.stringify(filterReason ? [] : event.mentions),
        event.createTimeMs,
        now,
        status,
        filterReason,
      );
      const inserted = number(result.changes) > 0;
      const row = this.getInboundByEvent.get(event.connectorInstance, event.eventId);
      if (inserted) {
        const ignored = Boolean(filterReason);
        if (!ignored && journal) {
          this.appendCognitionJournal({
            kind: 'inbound',
            atMs: event.createTimeMs || now,
            inboundSeq: number(row.seq),
            chatId: event.chatId,
            threadId: event.threadId,
            messageId: event.messageId,
            actor: event.senderId || 'unknown',
            text: event.content,
            dedupeKey: `event:${eventIdentity(event)}`,
            metadata: { source: event.source, connectorInstance: event.connectorInstance, event: envelope },
          });
        }
        this.recordActivity({
          eventKey: `inbound:${row.seq}:${ignored ? 'ignored' : 'received'}`,
          kind: ignored ? 'message.ignored' : event.chatId ? 'message.received' : 'connector.event_received',
          conversationKey: event.conversationKey,
          inboundSeq: number(row.seq),
          status,
          summary: ignored ? `未进入 Agent：${reasonLabel(filterReason)}` : [event.title, event.content].filter(Boolean).join('\n'),
          occurredAtMs: now,
          metadata: {
            chatType: event.chatType,
            messageType: event.messageType,
            source: event.source || 'lark',
            eventKind: event.kind,
            filterReason,
            ...(Number.isFinite(event.createTimeMs) ? { latencyMs: Math.max(0, now - event.createTimeMs) } : {}),
          },
        }, now);
      }
      return { inserted, inbound: rowToInbound(row), filterReason: row?.filter_reason ?? filterReason };
    });
  }

  upsertMeetingInvite({
    sessionKey,
    meetingId = null,
    meetingNumber,
    callId = null,
    eventId,
    atMs = Date.now(),
  }) {
    return this.meetings.upsertMeetingInvite({
      sessionKey, meetingId, meetingNumber, callId, eventId, atMs,
    });
  }

  markMeetingJoining(sessionKey, atMs = Date.now()) {
    return this.meetings.markMeetingJoining(sessionKey, atMs);
  }

  markMeetingJoinFailed(sessionKey, error, atMs = Date.now()) {
    return this.meetings.markMeetingJoinFailed(sessionKey, error, atMs);
  }

  markMeetingJoined(sessionKey, { meetingId, meetingNumber = null, atMs = Date.now() }) {
    return this.meetings.markMeetingJoined(sessionKey, { meetingId, meetingNumber, atMs });
  }

  ensureMeetingSession(meetingId, { meetingNumber = null, atMs = Date.now() } = {}) {
    return this.meetings.ensureMeetingSession(meetingId, { meetingNumber, atMs });
  }

  meetingSessionById(meetingId) {
    return this.meetings.meetingSessionById(meetingId);
  }

  activeMeetingSessions() {
    return this.meetings.activeMeetingSessions();
  }

  ingestMeetingEvent({
    eventKey,
    meetingId,
    eventType,
    activityType = null,
    occurredAtMs = Date.now(),
    payload = {},
    receivedAtMs = Date.now(),
  }) {
    return this.meetings.ingestMeetingEvent({
      eventKey, meetingId, eventType, activityType, occurredAtMs, payload, receivedAtMs,
    });
  }

  updateMeetingProgress(meetingId, {
    pageToken = null,
    sequence = null,
    eventAtMs = null,
    atMs = Date.now(),
  } = {}) {
    return this.meetings.updateMeetingProgress(meetingId, {
      pageToken, sequence, eventAtMs, atMs,
    });
  }

  meetingHasSequenceGap(meetingId, sequence) {
    return this.meetings.meetingHasSequenceGap(meetingId, sequence);
  }

  pendingMeetingEvents(meetingId, limit = 50) {
    return this.meetings.pendingMeetingEvents(meetingId, limit);
  }

  pendingMeetingIds() {
    return this.meetings.pendingMeetingIds();
  }

  commitMeetingInboxBatch(meetingId, rows, atMs = Date.now()) {
    return this.meetings.commitMeetingInboxBatch(meetingId, rows, atMs);
  }

  unreadMeetingInboxCount(meetingId = null) {
    return this.meetings.unreadMeetingInboxCount(meetingId);
  }

  readMeetingInbox({ meetingId = null, limit = 100, atMs = Date.now() } = {}) {
    return this.meetings.readMeetingInbox({ meetingId, limit, atMs });
  }

  markMeetingInboxBatchesNotified(batchIds, atMs = Date.now()) {
    return this.meetings.markMeetingInboxBatchesNotified(batchIds, atMs);
  }

  finishMeeting(meetingId, atMs = Date.now()) {
    return this.meetings.finishMeeting(meetingId, atMs);
  }

  getCognitionState() {
    const epoch = Number.parseInt(this.getMetaStatement.get('global_cognition_epoch')?.value ?? '1', 10);
    return {
      runtime: this.getMetaStatement.get('active_runtime')?.value ?? 'codex',
      epoch: Number.isSafeInteger(epoch) && epoch > 0 ? epoch : 1,
      authorityToken: this.getMetaStatement.get('active_runtime_token')?.value ?? null,
    };
  }

  hasCognitionRuntime() {
    return Boolean(this.getMetaStatement.get('active_runtime')?.value);
  }

  initializeCognition(runtime, authorityToken) {
    return this.transaction(() => {
      const current = this.getCognitionState();
      this.setMetaStatement.run('active_runtime', runtime);
      this.setMetaStatement.run('active_runtime_token', authorityToken);
      this.setMetaStatement.run('global_cognition_epoch', String(current.epoch));
      return { runtime, epoch: current.epoch, authorityToken };
    });
  }

  bumpCognitionEpoch(runtime, authorityToken) {
    return this.transaction(() => {
      const epoch = this.getCognitionState().epoch + 1;
      this.deleteGlobalSessionStatement.run();
      this.setMetaStatement.run('active_runtime', runtime);
      this.setMetaStatement.run('active_runtime_token', authorityToken);
      this.setMetaStatement.run('global_cognition_epoch', String(epoch));
      return { runtime, epoch, authorityToken };
    });
  }

  // A managed runtime must create its remote session before retiring the old
  // one locally. Once prepared, advance the epoch and publish the replacement
  // session in one transaction so a crash cannot leave an empty global session.
  commitCognitionSessionRotation({
    runtime,
    authorityToken,
    nextAuthorityToken = authorityToken,
    sessionId,
    policyHash,
    expectedEpoch,
    syncedThroughSeq = 0,
    sessionReady = true,
    settings = {},
    preserveContext = false,
  }) {
    return this.transaction(() => {
      const current = this.getCognitionState();
      if (current.runtime !== runtime
        || current.authorityToken !== authorityToken
        || current.epoch !== expectedEpoch) {
        throw new Error('cognition authority changed during session rotation');
      }
      const epoch = current.epoch + (preserveContext ? 0 : 1);
      this.setMetaStatement.run('active_runtime', runtime);
      this.setMetaStatement.run('active_runtime_token', nextAuthorityToken);
      this.setMetaStatement.run('global_cognition_epoch', String(epoch));
      this.setMetaStatement.run('global_session_id', sessionId);
      this.setMetaStatement.run('global_policy_hash', policyHash);
      this.setMetaStatement.run('global_session_runtime', runtime);
      this.setMetaStatement.run('global_session_epoch', String(epoch));
      this.setMetaStatement.run('global_session_synced_through_seq', String(syncedThroughSeq));
      this.setMetaStatement.run('global_session_ready', sessionReady ? '1' : '0');
      for (const [key, value] of Object.entries(settings)) {
        this.setMetaStatement.run(`setting:${key}`, String(value));
      }
      this.clearManagedMeta(runtime);
      return { runtime, epoch, authorityToken: nextAuthorityToken };
    });
  }

  // Two-phase runtime commit. The target has already started under a
  // non-authoritative token. sessionReady distinguishes a real native
  // transcript from Claude's preassigned UUID before its first stdin turn.
  commitRuntimeSwitch({
    runtime,
    authorityToken,
    sessionId,
    policyHash,
    epoch,
    syncedThroughSeq = 0,
    sessionReady = true,
    model = '',
    effort,
    expectedRuntime,
    expectedAuthorityToken,
  }) {
    return this.transaction(() => {
      const current = this.getCognitionState();
      if (current.runtime !== expectedRuntime
        || current.authorityToken !== expectedAuthorityToken
        || current.epoch !== epoch) {
        throw new Error('cognition authority changed during runtime switch');
      }
      this.setMetaStatement.run('active_runtime', runtime);
      this.setMetaStatement.run('active_runtime_token', authorityToken);
      this.setMetaStatement.run('global_session_id', sessionId);
      this.setMetaStatement.run('global_policy_hash', policyHash);
      this.setMetaStatement.run('global_session_runtime', runtime);
      this.setMetaStatement.run('global_session_epoch', String(epoch));
      this.setMetaStatement.run('global_session_synced_through_seq', String(syncedThroughSeq));
      this.setMetaStatement.run('global_session_ready', sessionReady ? '1' : '0');
      this.setMetaStatement.run(`setting:${runtime}_model`, String(model));
      if (effort !== undefined) this.setSetting(`${runtime}_effort`, effort);
      return { runtime, epoch, authorityToken };
    });
  }

  appendCognitionJournal({
    kind,
    status = 'COMMITTED',
    atMs = Date.now(),
    inboundSeq = null,
    chatId = null,
    threadId = null,
    messageId = null,
    actor = null,
    text = '',
    runtimeToken = null,
    dedupeKey = null,
    metadata = {},
    epoch = this.getCognitionState().epoch,
  }) {
    const result = this.insertJournalStatement.run(
      epoch,
      kind,
      status,
      atMs,
      inboundSeq,
      chatId,
      threadId,
      messageId,
      actor,
      text,
      runtimeToken,
      dedupeKey,
      JSON.stringify(metadata ?? {}),
    );
    if (number(result.changes) === 0 && dedupeKey) {
      return number(this.db.prepare('SELECT seq FROM cognition_journal WHERE dedupe_key = ?').get(dedupeKey)?.seq);
    }
    return number(this.db.prepare('SELECT last_insert_rowid() AS seq').get().seq);
  }

  cognitionJournal(
    epoch = this.getCognitionState().epoch,
    throughSeq = Number.MAX_SAFE_INTEGER,
    afterSeq = 0,
    { excludeCommittedOutboundFrom = null } = {},
  ) {
    // A same-session delta catch-up may skip COMMITTED outbound rows the active
    // runtime authored itself: its native transcript already contains that
    // delivery as a successful tool call, so echoing the text back is pure
    // duplicate input. PENDING/UNCERTAIN outbound rows are always included.
    if (excludeCommittedOutboundFrom) {
      return this.db.prepare(`
        SELECT * FROM cognition_journal
        WHERE epoch = ? AND seq > ? AND seq <= ?
          AND status IN ('PENDING', 'COMMITTED', 'UNCERTAIN')
          AND NOT (kind = 'outbound' AND status = 'COMMITTED' AND runtime_token = ?)
        ORDER BY seq
      `).all(epoch, afterSeq, throughSeq, excludeCommittedOutboundFrom)
        .map(rowToCognitionJournal);
    }
    return this.db.prepare(`
      SELECT * FROM cognition_journal
      WHERE epoch = ? AND seq > ? AND seq <= ?
        AND status IN ('PENDING', 'COMMITTED', 'UNCERTAIN')
      ORDER BY seq
    `).all(epoch, afterSeq, throughSeq).map(rowToCognitionJournal);
  }

  cognitionJournalHead(epoch = this.getCognitionState().epoch) {
    const row = this.db.prepare(`
      SELECT MAX(seq) AS seq FROM cognition_journal
      WHERE epoch = ? AND status IN ('PENDING', 'COMMITTED', 'UNCERTAIN')
    `).get(epoch);
    return number(row?.seq) || 0;
  }

  cognitionJournalBeforeInbound(inboundSeq, epoch = this.getCognitionState().epoch) {
    if (inboundSeq === null || inboundSeq === undefined) return this.cognitionJournalHead(epoch);
    const row = this.db.prepare(`
      SELECT seq FROM cognition_journal
      WHERE epoch = ? AND inbound_seq = ? AND kind = 'inbound'
    `).get(epoch, inboundSeq);
    return row ? Math.max(0, number(row.seq) - 1) : this.cognitionJournalHead(epoch);
  }

  cognitionJournalSeqForInbound(inboundSeq, epoch = this.getCognitionState().epoch) {
    if (inboundSeq === null || inboundSeq === undefined) return 0;
    const row = this.db.prepare(`
      SELECT seq FROM cognition_journal
      WHERE epoch = ? AND inbound_seq = ? AND kind = 'inbound'
    `).get(epoch, inboundSeq);
    return number(row?.seq) || 0;
  }

  // One active runtime session is shared across every chat. No per-runtime or
  // per-conversation session cache exists: a hard switch replaces these fields.
  getGlobalSession() {
    const epoch = Number.parseInt(this.getMetaStatement.get('global_session_epoch')?.value ?? '0', 10);
    const synced = Number.parseInt(
      this.getMetaStatement.get('global_session_synced_through_seq')?.value ?? '0',
      10,
    );
    return {
      sessionId: this.getMetaStatement.get('global_session_id')?.value ?? null,
      policyHash: this.getMetaStatement.get('global_policy_hash')?.value ?? null,
      runtime: this.getMetaStatement.get('global_session_runtime')?.value ?? null,
      epoch: Number.isSafeInteger(epoch) ? epoch : 0,
      syncedThroughSeq: Number.isSafeInteger(synced) ? synced : 0,
      sessionReady: Boolean(this.getMetaStatement.get('global_session_id')?.value)
        && this.getMetaStatement.get('global_session_ready')?.value !== '0',
    };
  }

  setGlobalSession(sessionId, policyHash, {
    runtime = this.getCognitionState().runtime,
    epoch = this.getCognitionState().epoch,
    syncedThroughSeq = this.getGlobalSession().syncedThroughSeq,
    sessionReady = true,
  } = {}) {
    return this.transaction(() => {
      this.setMetaStatement.run('global_session_id', sessionId);
      this.setMetaStatement.run('global_policy_hash', policyHash);
      this.setMetaStatement.run('global_session_runtime', runtime);
      this.setMetaStatement.run('global_session_epoch', String(epoch));
      this.setMetaStatement.run('global_session_synced_through_seq', String(syncedThroughSeq));
      this.setMetaStatement.run('global_session_ready', sessionReady ? '1' : '0');
    });
  }

  // Runtime adapters may finish work after a model/session switch has already
  // transferred authority. Publish only while the caller still owns the exact
  // cognition generation, so a late child cannot restore its retired session.
  publishOwnedSession({
    runtime,
    authorityToken,
    sessionId,
    policyHash,
    epoch,
    acceptedThroughSeq = 0,
    sessionReady = true,
  }) {
    return this.transaction(() => {
      const cognition = this.getCognitionState();
      if (cognition.runtime !== runtime
        || cognition.authorityToken !== authorityToken
        || cognition.epoch !== epoch) return false;
      if (!sessionId || !Number.isSafeInteger(acceptedThroughSeq) || acceptedThroughSeq < 0) {
        throw new TypeError('owned session publication is invalid');
      }
      const current = this.getGlobalSession();
      const sameSession = current.runtime === runtime
        && current.epoch === epoch
        && current.sessionId === sessionId;
      const cursor = sameSession
        ? Math.max(current.syncedThroughSeq, acceptedThroughSeq)
        : acceptedThroughSeq;
      const ready = sameSession ? current.sessionReady || sessionReady : sessionReady;
      this.setMetaStatement.run('global_session_id', sessionId);
      this.setMetaStatement.run('global_policy_hash', policyHash);
      this.setMetaStatement.run('global_session_runtime', runtime);
      this.setMetaStatement.run('global_session_epoch', String(epoch));
      this.setMetaStatement.run('global_session_synced_through_seq', String(cursor));
      this.setMetaStatement.run('global_session_ready', ready ? '1' : '0');
      return true;
    });
  }

  // A runtime stopped while a turn was still in flight cannot prove whether the
  // native transcript accepted that input. Resuming such a transcript risks
  // duplicated input, and (for Claude) resume rewrites the dangling tool call,
  // which invalidates the entire cached prompt prefix at cache-write price.
  // Demote the stored session so the next start opens a fresh one behind a
  // journal catch-up instead.
  markOwnedSessionUnresumable({ runtime, authorityToken, sessionId, epoch }) {
    return this.transaction(() => {
      const cognition = this.getCognitionState();
      const session = this.getGlobalSession();
      if (cognition.runtime !== runtime
        || cognition.authorityToken !== authorityToken
        || cognition.epoch !== epoch
        || session.runtime !== runtime
        || session.epoch !== epoch
        || session.sessionId !== sessionId) return false;
      this.setMetaStatement.run('global_session_ready', '0');
      return true;
    });
  }

  // `synced_through_seq` is the native-session acceptance watermark: once the
  // exact runtime/session owner has durably accepted journal input it advances
  // monotonically, independently of turn success or local Lark delivery.
  advanceOwnedSessionAcceptance({
    runtime,
    authorityToken,
    sessionId,
    epoch,
    acceptedThroughSeq,
  }) {
    return this.transaction(() => {
      if (!Number.isSafeInteger(acceptedThroughSeq) || acceptedThroughSeq < 0) {
        throw new TypeError('accepted session cursor is invalid');
      }
      const cognition = this.getCognitionState();
      const session = this.getGlobalSession();
      if (cognition.runtime !== runtime
        || cognition.authorityToken !== authorityToken
        || cognition.epoch !== epoch
        || session.runtime !== runtime
        || session.epoch !== epoch
        || session.sessionId !== sessionId) return false;
      if (acceptedThroughSeq > session.syncedThroughSeq) {
        this.setMetaStatement.run(
          'global_session_synced_through_seq',
          String(acceptedThroughSeq),
        );
      }
      return true;
    });
  }

  getManagedTurn(runtime) {
    const raw = this.getMetaStatement.get(`managed_turn:${runtime}`)?.value;
    if (!raw) return null;
    const turn = parseJsonObject(raw);
    return turn.runtime === runtime && turn.sessionId ? turn : null;
  }

  beginManagedTurn({
    runtime,
    authorityToken,
    sessionId,
    epoch,
    inboundSeqs,
    conversationKey,
    deliveredThroughSeq = 0,
    atMs = Date.now(),
  }) {
    return this.transaction(() => {
      const cognition = this.getCognitionState();
      const session = this.getGlobalSession();
      if (cognition.runtime !== runtime
        || cognition.authorityToken !== authorityToken
        || cognition.epoch !== epoch
        || session.runtime !== runtime || session.epoch !== epoch
        || session.sessionId !== sessionId) {
        throw new Error('managed turn no longer owns the active session');
      }
      const turn = {
        runtime,
        authorityToken,
        sessionId,
        epoch,
        status: 'POSTING',
        inboundSeqs: [...new Set((inboundSeqs ?? []).filter(Number.isSafeInteger))],
        conversationKey,
        deliveredThroughSeq,
        expectedUserEventId: null,
        atMs,
      };
      this.setMetaStatement.run(`managed_turn:${runtime}`, JSON.stringify(turn));
      return turn;
    });
  }

  acceptManagedTurn(runtime, sessionId, expectedUserEventId) {
    return this.transaction(() => {
      const turn = this.getManagedTurn(runtime);
      if (!turn || turn.sessionId !== sessionId || turn.status !== 'POSTING') {
        throw new Error('managed turn acceptance does not match the pending POST');
      }
      const cognition = this.getCognitionState();
      const session = this.getGlobalSession();
      if (cognition.runtime !== runtime
        || cognition.authorityToken !== turn.authorityToken
        || cognition.epoch !== turn.epoch
        || session.runtime !== runtime
        || session.epoch !== turn.epoch
        || session.sessionId !== sessionId) {
        throw new Error('managed turn acceptance no longer owns the active session');
      }
      const accepted = { ...turn, status: 'ACCEPTED', expectedUserEventId };
      this.setMetaStatement.run(`managed_turn:${runtime}`, JSON.stringify(accepted));
      const acceptedThroughSeq = Number.isSafeInteger(turn.deliveredThroughSeq)
        ? turn.deliveredThroughSeq
        : 0;
      if (acceptedThroughSeq > session.syncedThroughSeq) {
        this.setMetaStatement.run(
          'global_session_synced_through_seq',
          String(acceptedThroughSeq),
        );
      }
      return accepted;
    });
  }

  clearManagedTurn(runtime, { sessionId = null, expectedUserEventId = null } = {}) {
    return this.transaction(() => {
      const turn = this.getManagedTurn(runtime);
      if (!turn) return false;
      if (sessionId && turn.sessionId !== sessionId) return false;
      if (expectedUserEventId && turn.expectedUserEventId !== expectedUserEventId) return false;
      this.db.prepare('DELETE FROM meta WHERE key = ?').run(`managed_turn:${runtime}`);
      return true;
    });
  }

  getManagedSessionPolicy(runtime, sessionId) {
    const value = parseJsonObject(
      this.getMetaStatement.get(`managed_session_policy:${runtime}`)?.value,
    );
    return value.sessionId === sessionId && typeof value.policyHash === 'string'
      ? value.policyHash
      : null;
  }

  setManagedSessionPolicy(runtime, sessionId, policyHash) {
    this.setMetaStatement.run(
      `managed_session_policy:${runtime}`,
      JSON.stringify({ sessionId, policyHash }),
    );
  }

  getManagedSessionPoison(runtime) {
    const value = parseJsonObject(
      this.getMetaStatement.get(`managed_session_poison:${runtime}`)?.value,
    );
    return value.runtime === runtime && value.sessionId ? value : null;
  }

  poisonManagedSession(runtime, sessionId, epoch, atMs = Date.now()) {
    return this.transaction(() => {
      const cognition = this.getCognitionState();
      const session = this.getGlobalSession();
      if (cognition.runtime !== runtime || cognition.epoch !== epoch
        || session.runtime !== runtime || session.epoch !== epoch
        || session.sessionId !== sessionId) return false;
      const poison = { runtime, sessionId, epoch, atMs };
      this.setMetaStatement.run(`managed_session_poison:${runtime}`, JSON.stringify(poison));
      return true;
    });
  }

  replacePoisonedManagedSession({
    runtime,
    authorityToken,
    nextAuthorityToken,
    expectedSessionId,
    sessionId,
    policyHash,
    expectedEpoch,
  }) {
    return this.transaction(() => {
      const cognition = this.getCognitionState();
      const current = this.getGlobalSession();
      const poison = this.getManagedSessionPoison(runtime);
      if (cognition.runtime !== runtime
        || cognition.authorityToken !== authorityToken
        || cognition.epoch !== expectedEpoch
        || current.runtime !== runtime
        || current.epoch !== expectedEpoch
        || current.sessionId !== expectedSessionId
        || poison?.sessionId !== expectedSessionId
        || poison?.epoch !== expectedEpoch) {
        throw new Error('managed session quarantine no longer owns cognition authority');
      }
      const epoch = expectedEpoch + 1;
      this.setMetaStatement.run('active_runtime_token', nextAuthorityToken);
      this.setMetaStatement.run('global_cognition_epoch', String(epoch));
      this.setMetaStatement.run('global_session_id', sessionId);
      this.setMetaStatement.run('global_policy_hash', policyHash);
      this.setMetaStatement.run('global_session_runtime', runtime);
      this.setMetaStatement.run('global_session_epoch', String(epoch));
      this.setMetaStatement.run('global_session_synced_through_seq', '0');
      this.setMetaStatement.run('global_session_ready', '1');
      this.clearManagedMeta(runtime);
      return { runtime, epoch, authorityToken: nextAuthorityToken };
    });
  }

  // A fresh session or authority hand-off invalidates the managed runtime's
  // persisted turn fence and poison marker. Single owner of that key namespace.
  clearManagedMeta(runtime) {
    this.db.prepare('DELETE FROM meta WHERE key IN (?, ?)').run(
      `managed_turn:${runtime}`,
      `managed_session_poison:${runtime}`,
    );
  }

  // Who authored a message id this daemon has seen: an inbound sender, or the
  // bot itself (journaled deliveries record actor 'me'). Null when unseen —
  // e.g. quoted messages older than local history.
  resolveMessageAuthor(messageId, instance = 'lark') {
    if (!messageId) return null;
    const inbound = this.getInboundByMessage.get(instance, messageId);
    if (inbound) return { kind: 'user', chatId: inbound.chat_id, senderId: inbound.sender_id };
    const journal = this.getJournalActorByMessage.get(instance, messageId);
    if (journal?.actor === 'me') return { kind: 'me', text: journal.text };
    return null;
  }

  latestAcceptedInboundSeq(chatId, instance = 'lark') {
    if (!chatId) return 0;
    const row = this.db.prepare(`
      SELECT COALESCE(MAX(seq), 0) AS seq
      FROM connector_events WHERE connector_instance = ? AND chat_id = ? AND status = 'DONE'
    `).get(instance, chatId);
    return number(row?.seq);
  }

  inboundBySeq(seq) {
    if (seq === null || seq === undefined) return null;
    return rowToInbound(this.db.prepare('SELECT * FROM connector_events WHERE seq = ?').get(seq));
  }

  clearGlobalSession() {
    this.deleteGlobalSessionStatement.run();
  }

  // Runtime overrides (e.g. /model, /effort) in the same meta KV table.
  getSetting(key) {
    return this.getMetaStatement.get(`setting:${key}`)?.value ?? null;
  }

  setSetting(key, value) {
    this.setMetaStatement.run(`setting:${key}`, String(value));
  }

  setRuntimeSettings(runtime, { model, effort }) {
    return this.transaction(() => {
      this.setSetting(`${runtime}_model`, model);
      if (effort !== undefined) this.setSetting(`${runtime}_effort`, effort);
    });
  }

  // Reserve one external action together with its outbound journal row and
  // activity. An existing key reports its state instead; an UNCERTAIN one is
  // reclaimed only when the provider honours the idempotency key.
  reserveOutbox({
    connector, action, idempotencyKey, payload, summary = '', caller, retryUncertain = false,
    chatId = null, threadId = null, convKey = null, inboundSeq = null, runtimeToken = null, metadata = {},
  }, now = Date.now()) {
    return this.transaction(() => {
      const existing = this.db.prepare('SELECT * FROM outbox WHERE connector = ? AND idempotency_key = ?')
        .get(connector, idempotencyKey);
      let id;
      let attempt = 1;
      if (existing) {
        id = number(existing.id);
        if (existing.status !== 'UNCERTAIN' || !retryUncertain) {
          return { id, claimed: false, status: existing.status,
            result: existing.result_json ? JSON.parse(existing.result_json) : null };
        }
        attempt = number(existing.attempt_count) + 1;
        this.db.prepare(`UPDATE outbox SET status = 'PENDING', attempt_count = ?, last_error = NULL, updated_at_ms = ?
          WHERE id = ?`).run(attempt, now, id);
        this.db.prepare("UPDATE cognition_journal SET status = 'PENDING' WHERE dedupe_key = ?").run(`outbox:${id}`);
      } else {
        id = number(this.db.prepare(`
          INSERT INTO outbox(connector, action, idempotency_key, payload_json, summary, caller, status,
            chat_id, conv_key, inbound_seq, created_at_ms, updated_at_ms)
          VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?)
        `).run(connector, action, idempotencyKey, JSON.stringify(payload), summary, caller,
          chatId, convKey, inboundSeq, now, now).lastInsertRowid);
        this.appendCognitionJournal({
          kind: 'outbound', status: 'PENDING', atMs: now, chatId, threadId, actor: 'me', text: summary,
          runtimeToken, dedupeKey: `outbox:${id}`,
          metadata: { action, source: connector, connectorInstance: connector, ...metadata },
        });
      }
      this.recordActivity({
        eventKey: `outbox:${id}:${attempt}:queued`, kind: 'delivery.queued', conversationKey: convKey,
        inboundSeq, outboxId: id, status: 'PENDING', summary, occurredAtMs: now,
        metadata: { connector, action, attempt },
      }, now);
      return { id, claimed: true, status: 'PENDING', attempt, result: null };
    });
  }

  settleOutbox(id, status, { result = null, error = null } = {}, now = Date.now()) {
    return this.transaction(() => {
      const row = this.db.prepare('SELECT * FROM outbox WHERE id = ?').get(id);
      const remoteMessageId = typeof result?.message_id === 'string' ? result.message_id : null;
      this.db.prepare(`
        UPDATE outbox SET status = ?, result_json = ?, remote_message_id = ?, last_error = ?, updated_at_ms = ?,
          sent_at_ms = CASE WHEN ? = 'SENT' THEN ? ELSE sent_at_ms END
        WHERE id = ?
      `).run(status, result === null ? null : JSON.stringify(result), remoteMessageId,
        error === null ? null : String(error).slice(0, 4_000), now, status, now, id);
      this.db.prepare(`UPDATE cognition_journal SET status = ?, message_id = COALESCE(?, message_id)
        WHERE dedupe_key = ?`).run(status === 'SENT' ? 'COMMITTED' : status, remoteMessageId, `outbox:${id}`);
      this.recordActivity({
        eventKey: `outbox:${id}:${number(row.attempt_count)}:${status.toLowerCase()}`,
        kind: status === 'SENT' ? 'delivery.sent' : 'delivery.uncertain',
        conversationKey: row.conv_key, inboundSeq: row.inbound_seq === null ? null : number(row.inbound_seq),
        outboxId: id, status, summary: status === 'SENT' ? row.summary : String(error ?? ''), occurredAtMs: now,
        metadata: { connector: row.connector, action: row.action, attempt: number(row.attempt_count) },
      }, now);
    });
  }

  // A cleared temporary reaction no longer belongs to the observable history.
  cancelOutboxJournal(id) {
    this.db.prepare("UPDATE cognition_journal SET status = 'CANCELLED' WHERE dedupe_key = ?").run(`outbox:${id}`);
  }

  // The newest accepted-or-ignored inbound row for a message id on one connector.
  // Only the message's place (chat, thread) is read from it, which every event
  // about that message shares.
  eventByMessageId(messageId, connector) {
    return this.db.prepare(`SELECT * FROM connector_events WHERE connector_instance = ? AND message_id = ?
      ORDER BY seq DESC LIMIT 1`).get(connector, messageId) ?? null;
  }

  // Accepted inbound events with this event id: at most one per instance.
  acceptedEvents(eventId, instance = null) {
    return this.db.prepare("SELECT * FROM connector_events WHERE event_id = ? AND status = 'DONE' ORDER BY seq")
      .all(eventId).filter((row) => instance === null || row.connector_instance === instance);
  }

  sentTimerOccurrenceDelivery(occurrenceId) {
    return this.db.prepare(`
      SELECT o.* FROM outbox o
      JOIN connector_events e ON e.seq = o.inbound_seq
      LEFT JOIN cognition_journal j ON j.dedupe_key = 'outbox:' || o.id
      WHERE o.status = 'SENT'
        AND o.action IN ('message.send', 'message.reply')
        AND COALESCE(json_extract(j.detail_json, '$.interim'), 0) != 1
        AND e.connector_instance = 'timer'
        AND e.kind = 'reminder.due'
        AND json_extract(e.event_json, '$.data.timer.occurrence_id') = ?
      ORDER BY o.sent_at_ms DESC, o.id DESC
      LIMIT 1
    `).get(occurrenceId) ?? null;
  }

  // Accepted inbound events that carried this message on one instance.
  acceptedMessageEvents(messageId, instance) {
    return this.db.prepare(`SELECT * FROM connector_events
      WHERE connector_instance = ? AND message_id = ? AND status = 'DONE'`).all(instance, messageId);
  }

  // The newest accepted inbound received after seq in that scope, if any. Core
  // receive order, never provider timestamps.
  newerAcceptedSeq(scopeKey, seq) {
    const newer = this.db.prepare(`
      SELECT MAX(seq) AS seq FROM connector_events WHERE scope_key = ? AND status = 'DONE' AND seq > ?
    `).get(scopeKey, seq);
    return newer?.seq == null ? null : number(newer.seq);
  }

  // The last message a person wrote in this chat/thread, up to `seq`; 0 if none.
  lastPersonSeq(scopeKey, seq = Number.MAX_SAFE_INTEGER) {
    return number(this.db.prepare(`
      SELECT COALESCE(MAX(seq), 0) AS seq FROM connector_events
      WHERE scope_key = ? AND status = 'DONE' AND seq <= ? AND (sender_type IS NULL OR sender_type NOT IN ('bot', 'app'))
    `).get(scopeKey, seq).seq);
  }

  // How many bot messages in this chat/thread, other than `seq`, the agent has
  // answered there with text since a person last wrote in it.
  botMessagesAnswered(scopeKey, seq) {
    const person = this.lastPersonSeq(scopeKey);
    const answered = number(this.db.prepare(`
      SELECT COUNT(DISTINCT e.seq) AS count FROM outbox o JOIN connector_events e ON e.seq = o.inbound_seq
      WHERE e.scope_key = ? AND e.seq > ? AND e.seq <> ? AND e.sender_type IN ('bot', 'app')
        AND o.conv_key = e.conv_key AND o.caller = 'agent' AND o.action IN ('message.send', 'message.reply')
        AND o.status IN ('PENDING', 'SENT', 'UNCERTAIN')
    `).get(scopeKey, person, seq).count);
    return { person, answered };
  }

  // Bot messages in this chat/thread since a person last wrote there, up to and
  // including `seq`.
  botsSincePerson(scopeKey, seq) {
    return number(this.db.prepare(`
      SELECT COUNT(*) AS count FROM connector_events
      WHERE scope_key = ? AND status = 'DONE' AND seq > ? AND seq <= ? AND sender_type IN ('bot', 'app')
    `).get(scopeKey, this.lastPersonSeq(scopeKey, seq), seq).count);
  }

  // Records the model's decision on one accepted event (a connector_events
  // row). A delivery always wins and replaces an earlier silence; silence never
  // replaces anything. `created` is false when the row was already there with
  // this outcome (or silence met a delivery).
  recordDecision({ row, outcome }, now = Date.now()) {
    if (!['silent', 'delivered'].includes(outcome)) throw new Error(`unknown decision outcome: ${outcome}`);
    const previous = this.decisionFor(row.connector_instance, row.event_id);
    const result = this.db.prepare(`
      INSERT INTO decisions(connector_instance, event_id, seq, scope_key, outcome, decided_at_ms)
      VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(connector_instance, event_id) DO UPDATE SET
        outcome = excluded.outcome, decided_at_ms = excluded.decided_at_ms
      WHERE excluded.outcome = 'delivered' AND decisions.outcome = 'silent'
    `).run(row.connector_instance, row.event_id, number(row.seq), row.scope_key, outcome, now);
    return {
      created: number(result.changes) > 0,
      previous: previous?.outcome ?? null,
      row: this.decisionFor(row.connector_instance, row.event_id),
    };
  }

  decisionFor(instance, eventId) {
    const row = this.db.prepare('SELECT * FROM decisions WHERE connector_instance = ? AND event_id = ?').get(instance, eventId);
    return row ? { ...row, seq: number(row.seq), decided_at_ms: number(row.decided_at_ms) } : null;
  }

  // The outcome of a decision in the scope that covers seq ('delivered' over
  // 'silent'), or null: one on that input or on a newer one there, as
  // attribution lets the newest input answer earlier ones.
  coveringDecision(scopeKey, seq) {
    return this.db.prepare(`SELECT outcome FROM decisions WHERE scope_key = ? AND seq >= ?
      ORDER BY outcome = 'delivered' DESC LIMIT 1`).get(scopeKey, seq)?.outcome ?? null;
  }

  // Which connector a message or chat id belongs to, as far as this daemon has
  // seen it: an authenticated inbound or one of its own deliveries.
  connectorForMessage(messageId) {
    return this.db.prepare('SELECT connector_instance AS connector FROM connector_events WHERE message_id = ? ORDER BY seq DESC LIMIT 1').get(messageId)?.connector
      ?? this.db.prepare('SELECT connector FROM outbox WHERE remote_message_id = ? ORDER BY id DESC LIMIT 1').get(messageId)?.connector
      ?? null;
  }

  connectorForChat(chatId) {
    return this.db.prepare('SELECT connector_instance AS connector FROM connector_events WHERE chat_id = ? ORDER BY seq DESC LIMIT 1').get(chatId)?.connector
      ?? this.db.prepare('SELECT connector FROM outbox WHERE chat_id = ? ORDER BY id DESC LIMIT 1').get(chatId)?.connector
      ?? null;
  }

  // Tenant and chat type of a chat as its connector last reported them. For a
  // chat it never reported, the connector's newest tenant: a Lark app sees one
  // tenant on every event. Unary + keeps that lookup on newest-first seq order,
  // which stops at the first row instead of sorting the connector's history.
  chatContext(connector, chatId) {
    return this.db.prepare(`SELECT tenant_key, chat_type FROM connector_events
      WHERE chat_id = ? AND connector_instance = ? ORDER BY seq DESC LIMIT 1`).get(chatId, connector)
      ?? { tenant_key: this.db.prepare(`SELECT tenant_key FROM connector_events
        WHERE +connector_instance = ? AND tenant_key IS NOT NULL ORDER BY seq DESC LIMIT 1`).get(connector)?.tenant_key ?? null };
  }

  // Scope of a message this bot delivered on a connector (for replies/reactions to it).
  deliveredMessage(messageId, connector) {
    return this.db.prepare(`SELECT connector, chat_id, conv_key FROM outbox
      WHERE remote_message_id = ? AND connector = ? ORDER BY id DESC LIMIT 1`).get(messageId, connector) ?? null;
  }

  prune(retentionMs, now = Date.now()) {
    const cutoff = now - retentionMs;
    const removed = this.transaction(() => {
      const inbound = this.db.prepare(`
        DELETE FROM connector_events
        WHERE received_at_ms < ?
      `).run(cutoff);
      const meeting = this.meetings.prune(cutoff);
      const outbox = this.db.prepare(`
        DELETE FROM outbox WHERE updated_at_ms < ? AND status IN ('SENT', 'UNCERTAIN')
      `).run(cutoff);
      const approvals = pruneApprovals(this.db, cutoff);
      const decisions = this.db.prepare('DELETE FROM decisions WHERE decided_at_ms < ?').run(cutoff);
      const conversations = this.db.prepare(`
        DELETE FROM conversations
        WHERE NOT EXISTS (SELECT 1 FROM connector_events WHERE connector_events.conv_key = conversations.conv_key)
      `).run();
      return {
        inbound: number(inbound.changes),
        meetingEvents: meeting.events,
        meetingSessions: meeting.sessions,
        outbox: number(outbox.changes),
        approvals: number(approvals),
        decisions: number(decisions.changes),
        conversations: number(conversations.changes),
      };
    });
    try { this.db.exec('PRAGMA wal_checkpoint(PASSIVE)'); } catch {}
    return removed;
  }

  stats() {
    const inbound = Object.fromEntries(this.db.prepare(`
      SELECT status, COUNT(*) AS count FROM connector_events GROUP BY status
    `).all().map((row) => [row.status, number(row.count)]));
    const outbox = Object.fromEntries(this.db.prepare(`
      SELECT status, COUNT(*) AS count FROM outbox GROUP BY status
    `).all().map((row) => [row.status, number(row.count)]));
    const activities = number(this.db.prepare(`
      SELECT COUNT(*) AS count FROM activities
    `).get().count);
    const meeting = this.meetings.stats();
    return { inbound, outbox, activities, ...meeting };
  }

  close() {
    try { this.db.exec('PRAGMA wal_checkpoint(TRUNCATE)'); } catch {}
    this.db.close();
  }
}
