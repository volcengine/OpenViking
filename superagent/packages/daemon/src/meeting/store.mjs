// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { meetingEventFingerprint } from './events.mjs';

function number(value) {
  return typeof value === 'bigint' ? Number(value) : value;
}

function parsePayload(value) {
  try {
    return JSON.parse(value || '{}');
  } catch {
    return {};
  }
}

function session(row) {
  if (!row) return null;
  return {
    sessionKey: row.session_key,
    meetingId: row.meeting_id,
    meetingNumber: row.meeting_no,
    callId: row.call_id,
    inviteEventId: row.invite_event_id,
    status: row.status,
    pageToken: row.page_token,
    lastSequence: row.last_sequence === null ? null : number(row.last_sequence),
    lastEventAtMs: row.last_event_at_ms === null ? null : number(row.last_event_at_ms),
    joinedAtMs: row.joined_at_ms === null ? null : number(row.joined_at_ms),
    endedAtMs: row.ended_at_ms === null ? null : number(row.ended_at_ms),
    lastError: row.last_error,
    updatedAtMs: number(row.updated_at_ms),
  };
}

function event(row) {
  if (!row) return null;
  return {
    id: number(row.id),
    eventKey: row.event_key,
    meetingId: row.meeting_id,
    eventType: row.event_type,
    activityType: row.activity_type,
    occurredAtMs: number(row.occurred_at_ms),
    receivedAtMs: number(row.received_at_ms),
    payload: parsePayload(row.payload_json),
    inboxBatchId: row.inbox_batch_id === null ? null : number(row.inbox_batch_id),
    inboxReadAtMs: row.inbox_read_at_ms === null ? null : number(row.inbox_read_at_ms),
  };
}

export function migrateMeetingStore(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS meeting_sessions (
      session_key TEXT PRIMARY KEY,
      meeting_id TEXT UNIQUE,
      meeting_no TEXT,
      call_id TEXT,
      invite_event_id TEXT,
      status TEXT NOT NULL,
      page_token TEXT,
      last_sequence INTEGER,
      last_event_at_ms INTEGER,
      joined_at_ms INTEGER,
      ended_at_ms INTEGER,
      last_error TEXT,
      created_at_ms INTEGER NOT NULL,
      updated_at_ms INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS meeting_sessions_status
      ON meeting_sessions(status, updated_at_ms);

    CREATE TABLE IF NOT EXISTS meeting_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      event_key TEXT NOT NULL UNIQUE,
      revision_of TEXT,
      semantic_fingerprint TEXT,
      meeting_id TEXT,
      event_type TEXT NOT NULL,
      activity_type TEXT,
      occurred_at_ms INTEGER NOT NULL,
      received_at_ms INTEGER NOT NULL,
      payload_json TEXT NOT NULL,
      inbox_batch_id INTEGER,
      inbox_read_at_ms INTEGER
    );
    CREATE INDEX IF NOT EXISTS meeting_events_received
      ON meeting_events(received_at_ms);

    CREATE TABLE IF NOT EXISTS meeting_inbox_batches (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      meeting_id TEXT NOT NULL,
      first_event_id INTEGER NOT NULL,
      last_event_id INTEGER NOT NULL,
      event_count INTEGER NOT NULL,
      created_at_ms INTEGER NOT NULL,
      notified_at_ms INTEGER,
      UNIQUE(meeting_id, first_event_id, last_event_id)
    );
  `);
  db.exec(`
    CREATE INDEX IF NOT EXISTS meeting_events_pending_v2
      ON meeting_events(meeting_id, inbox_batch_id, id);
    CREATE INDEX IF NOT EXISTS meeting_events_inbox
      ON meeting_events(inbox_read_at_ms, inbox_batch_id, id);
    CREATE INDEX IF NOT EXISTS meeting_events_revision
      ON meeting_events(revision_of, id);
    CREATE INDEX IF NOT EXISTS meeting_events_semantic
      ON meeting_events(event_key, revision_of, semantic_fingerprint);
  `);
}

export function readMeetingInboxDatabase(
  db,
  { meetingId = null, limit = 100, atMs = Date.now() } = {},
) {
  const safeLimit = Math.max(1, Math.min(500, Number(limit) || 100));
  db.exec('BEGIN IMMEDIATE');
  try {
    const rows = meetingId
      ? db.prepare(`
          SELECT * FROM meeting_events
          WHERE inbox_batch_id IS NOT NULL
            AND inbox_read_at_ms IS NULL
            AND meeting_id = ?
          ORDER BY id LIMIT ?
        `).all(meetingId, safeLimit)
      : db.prepare(`
          SELECT * FROM meeting_events
          WHERE inbox_batch_id IS NOT NULL AND inbox_read_at_ms IS NULL
          ORDER BY id LIMIT ?
        `).all(safeLimit);
    const acknowledge = db.prepare(`
      UPDATE meeting_events SET inbox_read_at_ms = ?
      WHERE id = ? AND inbox_read_at_ms IS NULL
    `);
    for (const row of rows) acknowledge.run(atMs, row.id);

    const remainingRow = meetingId
      ? db.prepare(`
          SELECT COUNT(*) AS count FROM meeting_events
          WHERE inbox_batch_id IS NOT NULL
            AND inbox_read_at_ms IS NULL
            AND meeting_id = ?
        `).get(meetingId)
      : db.prepare(`
          SELECT COUNT(*) AS count FROM meeting_events
          WHERE inbox_batch_id IS NOT NULL AND inbox_read_at_ms IS NULL
        `).get();
    db.exec('COMMIT');
    return {
      items: rows.map((row) => event({ ...row, inbox_read_at_ms: atMs })),
      remaining: number(remainingRow.count) || 0,
      acknowledgedAtMs: atMs,
    };
  } catch (error) {
    try {
      db.exec('ROLLBACK');
    } catch {}
    throw error;
  }
}

export class MeetingStore {
  constructor(db, transaction) {
    this.db = db;
    this.transaction = transaction;
  }

  upsertMeetingInvite({
    sessionKey,
    meetingId = null,
    meetingNumber,
    callId = null,
    eventId,
    atMs = Date.now(),
  }) {
    this.db.prepare(`
      INSERT INTO meeting_sessions(
        session_key, meeting_id, meeting_no, call_id, invite_event_id,
        status, created_at_ms, updated_at_ms
      ) VALUES (?, ?, ?, ?, ?, 'INVITED', ?, ?)
      ON CONFLICT(session_key) DO UPDATE SET
        meeting_id = COALESCE(excluded.meeting_id, meeting_sessions.meeting_id),
        meeting_no = COALESCE(excluded.meeting_no, meeting_sessions.meeting_no),
        call_id = COALESCE(excluded.call_id, meeting_sessions.call_id),
        invite_event_id = COALESCE(
          excluded.invite_event_id,
          meeting_sessions.invite_event_id
        ),
        updated_at_ms = MAX(meeting_sessions.updated_at_ms, excluded.updated_at_ms)
    `).run(sessionKey, meetingId, meetingNumber, callId, eventId, atMs, atMs);
    return session(this.db.prepare(`
      SELECT * FROM meeting_sessions WHERE session_key = ?
    `).get(sessionKey));
  }

  markMeetingJoining(key, atMs = Date.now()) {
    return number(this.db.prepare(`
      UPDATE meeting_sessions
      SET status = 'JOINING', last_error = NULL, updated_at_ms = ?
      WHERE session_key = ? AND status IN ('INVITED', 'FAILED')
    `).run(atMs, key).changes) > 0;
  }

  markMeetingJoinFailed(key, error, atMs = Date.now()) {
    return number(this.db.prepare(`
      UPDATE meeting_sessions
      SET status = 'FAILED', last_error = ?, updated_at_ms = ?
      WHERE session_key = ?
    `).run(String(error).slice(0, 2000), atMs, key).changes) > 0;
  }

  markMeetingJoined(key, { meetingId, meetingNumber = null, atMs = Date.now() }) {
    return this.transaction(() => {
      const placeholder = this.db.prepare(`
        SELECT * FROM meeting_sessions
        WHERE meeting_id = ? AND session_key <> ?
      `).get(meetingId, key);
      if (placeholder) {
        this.db.prepare('DELETE FROM meeting_sessions WHERE session_key = ?')
          .run(placeholder.session_key);
      }
      this.db.prepare(`
        UPDATE meeting_sessions SET
          meeting_id = ?,
          meeting_no = COALESCE(?, meeting_no, ?),
          status = CASE WHEN ? = 'ENDED' THEN 'ENDED' ELSE 'JOINED' END,
          page_token = COALESCE(page_token, ?),
          last_sequence = CASE
            WHEN last_sequence IS NULL THEN ?
            WHEN ? IS NULL THEN last_sequence
            ELSE MAX(last_sequence, ?)
          END,
          last_event_at_ms = CASE
            WHEN last_event_at_ms IS NULL THEN ?
            WHEN ? IS NULL THEN last_event_at_ms
            ELSE MAX(last_event_at_ms, ?)
          END,
          joined_at_ms = COALESCE(joined_at_ms, ?),
          ended_at_ms = COALESCE(ended_at_ms, ?),
          last_error = NULL,
          updated_at_ms = ?
        WHERE session_key = ?
      `).run(
        meetingId,
        meetingNumber,
        placeholder?.meeting_no ?? null,
        placeholder?.status ?? null,
        placeholder?.page_token ?? null,
        placeholder?.last_sequence ?? null,
        placeholder?.last_sequence ?? null,
        placeholder?.last_sequence ?? null,
        placeholder?.last_event_at_ms ?? null,
        placeholder?.last_event_at_ms ?? null,
        placeholder?.last_event_at_ms ?? null,
        atMs,
        placeholder?.ended_at_ms ?? null,
        atMs,
        key,
      );
      return this.meetingSessionById(meetingId);
    });
  }

  ensureMeetingSession(meetingId, { meetingNumber = null, atMs = Date.now() } = {}) {
    this.db.prepare(`
      INSERT OR IGNORE INTO meeting_sessions(
        session_key, meeting_id, meeting_no, status, created_at_ms, updated_at_ms
      ) VALUES (?, ?, ?, 'ACTIVE', ?, ?)
    `).run(`meeting:${meetingId}`, meetingId, meetingNumber, atMs, atMs);
    this.db.prepare(`
      UPDATE meeting_sessions SET
        meeting_no = COALESCE(?, meeting_no),
        status = CASE
          WHEN status IN ('ENDED', 'JOINING') THEN status
          ELSE 'ACTIVE'
        END,
        updated_at_ms = MAX(updated_at_ms, ?)
      WHERE meeting_id = ?
    `).run(meetingNumber, atMs, meetingId);
    return this.meetingSessionById(meetingId);
  }

  meetingSessionById(id) {
    return session(this.db.prepare(`
      SELECT * FROM meeting_sessions WHERE meeting_id = ?
    `).get(id));
  }

  activeMeetingSessions() {
    return this.db.prepare(`
      SELECT * FROM meeting_sessions
      WHERE meeting_id IS NOT NULL AND status IN ('JOINED', 'ACTIVE')
      ORDER BY updated_at_ms
    `).all().map(session);
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
    const payloadJson = JSON.stringify(payload ?? {});
    const fingerprint = meetingEventFingerprint(payload, activityType);
    const known = this.db.prepare(`
      SELECT id FROM meeting_events
      WHERE (event_key = ? OR revision_of = ?) AND semantic_fingerprint = ?
      LIMIT 1
    `).get(eventKey, eventKey, fingerprint);
    if (known) return false;

    const base = this.db.prepare(`
      SELECT * FROM meeting_events WHERE event_key = ?
    `).get(eventKey);
    const update = (row) => {
      const changed = number(this.db.prepare(`
        UPDATE meeting_events SET
          event_type = ?,
          activity_type = ?,
          occurred_at_ms = ?,
          received_at_ms = ?,
          payload_json = ?,
          semantic_fingerprint = ?
        WHERE id = ? AND inbox_read_at_ms IS NULL
      `).run(
        eventType,
        activityType,
        occurredAtMs,
        receivedAtMs,
        payloadJson,
        fingerprint,
        row.id,
      ).changes) > 0;
      return changed && row.inbox_batch_id === null;
    };
    if (base && base.inbox_read_at_ms === null) return update(base);

    if (base) {
      const pendingRevision = this.db.prepare(`
        SELECT * FROM meeting_events
        WHERE revision_of = ? AND inbox_read_at_ms IS NULL
        ORDER BY id DESC LIMIT 1
      `).get(eventKey);
      if (pendingRevision) return update(pendingRevision);
      const revisionKey = `${eventKey}:revision:${fingerprint}`;
      return number(this.db.prepare(`
        INSERT OR IGNORE INTO meeting_events(
          event_key, revision_of, semantic_fingerprint, meeting_id,
          event_type, activity_type, occurred_at_ms, received_at_ms,
          payload_json, inbox_batch_id, inbox_read_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
      `).run(
        revisionKey,
        eventKey,
        fingerprint,
        meetingId,
        eventType,
        activityType,
        occurredAtMs,
        receivedAtMs,
        payloadJson,
      ).changes) > 0;
    }

    return number(this.db.prepare(`
      INSERT OR IGNORE INTO meeting_events(
        event_key, revision_of, semantic_fingerprint, meeting_id,
        event_type, activity_type, occurred_at_ms, received_at_ms,
        payload_json, inbox_batch_id, inbox_read_at_ms
      ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
    `).run(
      eventKey,
      fingerprint,
      meetingId,
      eventType,
      activityType,
      occurredAtMs,
      receivedAtMs,
      payloadJson,
    ).changes) > 0;
  }

  updateMeetingProgress(
    id,
    { pageToken = null, sequence = null, eventAtMs = null, atMs = Date.now() } = {},
  ) {
    return number(this.db.prepare(`
      UPDATE meeting_sessions SET
        page_token = COALESCE(?, page_token),
        last_sequence = CASE
          WHEN ? IS NULL THEN last_sequence
          WHEN last_sequence IS NULL OR ? > last_sequence THEN ?
          ELSE last_sequence
        END,
        last_event_at_ms = CASE
          WHEN ? IS NULL THEN last_event_at_ms
          WHEN last_event_at_ms IS NULL OR ? > last_event_at_ms THEN ?
          ELSE last_event_at_ms
        END,
        updated_at_ms = MAX(updated_at_ms, ?)
      WHERE meeting_id = ?
    `).run(
      pageToken,
      sequence,
      sequence,
      sequence,
      eventAtMs,
      eventAtMs,
      eventAtMs,
      atMs,
      id,
    ).changes) > 0;
  }

  meetingHasSequenceGap(id, sequence) {
    const last = this.meetingSessionById(id)?.lastSequence;
    return Number.isSafeInteger(sequence)
      && Number.isSafeInteger(last)
      && sequence > last + 1;
  }

  pendingMeetingEvents(id, limit = 50) {
    const safeLimit = Math.max(1, Math.min(500, Number(limit) || 50));
    return this.db.prepare(`
      SELECT * FROM meeting_events
      WHERE meeting_id = ?
        AND inbox_batch_id IS NULL
        AND inbox_read_at_ms IS NULL
      ORDER BY id LIMIT ?
    `).all(id, safeLimit).map(event);
  }

  pendingMeetingIds() {
    return this.db.prepare(`
      SELECT DISTINCT meeting_id FROM meeting_events
      WHERE meeting_id IS NOT NULL
        AND inbox_batch_id IS NULL
        AND inbox_read_at_ms IS NULL
      ORDER BY meeting_id
    `).all().map((row) => row.meeting_id);
  }

  commitMeetingInboxBatch(meetingId, rows, atMs = Date.now()) {
    if (!rows?.length) return null;
    return this.transaction(() => {
      const eligible = rows.filter((row) => this.db.prepare(`
        SELECT 1 FROM meeting_events
        WHERE id = ? AND inbox_batch_id IS NULL AND inbox_read_at_ms IS NULL
      `).get(row.id));
      if (!eligible.length) return null;
      const first = eligible[0];
      const last = eligible.at(-1);
      const inserted = this.db.prepare(`
        INSERT OR IGNORE INTO meeting_inbox_batches(
          meeting_id, first_event_id, last_event_id, event_count, created_at_ms
        ) VALUES (?, ?, ?, ?, ?)
      `).run(meetingId, first.id, last.id, eligible.length, atMs);
      if (!number(inserted.changes)) return null;
      const batchId = number(this.db.prepare('SELECT last_insert_rowid() AS id').get().id);
      const update = this.db.prepare(`
        UPDATE meeting_events SET inbox_batch_id = ?
        WHERE id = ? AND inbox_batch_id IS NULL AND inbox_read_at_ms IS NULL
      `);
      for (const row of eligible) update.run(batchId, row.id);
      return {
        batchId,
        meetingId,
        eventCount: eligible.length,
        pendingCount: this.unreadMeetingInboxCount(),
      };
    });
  }

  unreadMeetingInboxCount(id = null) {
    const row = id
      ? this.db.prepare(`
          SELECT COUNT(*) AS count FROM meeting_events
          WHERE inbox_batch_id IS NOT NULL
            AND inbox_read_at_ms IS NULL
            AND meeting_id = ?
        `).get(id)
      : this.db.prepare(`
          SELECT COUNT(*) AS count FROM meeting_events
          WHERE inbox_batch_id IS NOT NULL AND inbox_read_at_ms IS NULL
        `).get();
    return number(row.count) || 0;
  }

  readMeetingInbox(options = {}) {
    return readMeetingInboxDatabase(this.db, options);
  }

  markMeetingInboxBatchesNotified(ids, atMs = Date.now()) {
    if (!ids?.length) return 0;
    const update = this.db.prepare(`
      UPDATE meeting_inbox_batches
      SET notified_at_ms = COALESCE(notified_at_ms, ?)
      WHERE id = ?
    `);
    return this.transaction(() => ids.reduce(
      (count, id) => count + number(update.run(atMs, id).changes),
      0,
    ));
  }

  finishMeeting(id, atMs = Date.now()) {
    return number(this.db.prepare(`
      UPDATE meeting_sessions
      SET status = 'ENDED',
          ended_at_ms = COALESCE(ended_at_ms, ?),
          updated_at_ms = ?
      WHERE meeting_id = ?
    `).run(atMs, atMs, id).changes) > 0;
  }

  prune(cutoff) {
    const events = number(this.db.prepare(`
      DELETE FROM meeting_events
      WHERE received_at_ms < ? AND inbox_read_at_ms IS NOT NULL
    `).run(cutoff).changes);
    this.db.prepare(`
      DELETE FROM meeting_inbox_batches
      WHERE created_at_ms < ?
        AND NOT EXISTS (
          SELECT 1 FROM meeting_events
          WHERE meeting_events.inbox_batch_id = meeting_inbox_batches.id
        )
    `).run(cutoff);
    const sessions = number(this.db.prepare(`
      DELETE FROM meeting_sessions
      WHERE status IN ('ENDED', 'FAILED') AND updated_at_ms < ?
        AND (meeting_id IS NULL OR NOT EXISTS (
          SELECT 1 FROM meeting_events
          WHERE meeting_events.meeting_id = meeting_sessions.meeting_id
        ))
    `).run(cutoff).changes);
    return { events, sessions };
  }

  stats() {
    const meetings = Object.fromEntries(this.db.prepare(`
      SELECT status, COUNT(*) AS count
      FROM meeting_sessions
      GROUP BY status
    `).all().map((row) => [row.status, number(row.count)]));
    return {
      meetings,
      meetingEvents: number(
        this.db.prepare('SELECT COUNT(*) AS count FROM meeting_events').get().count,
      ),
      meetingInbox: this.unreadMeetingInboxCount(),
    };
  }
}
