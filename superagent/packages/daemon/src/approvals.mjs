// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Approvals the owner gives by message, on an IM without cards: the draft goes
// to the owner as a numbered message and the owner answers 发送 N, 不发 N or
// 修改 N <text>. The number is the row id: AUTOINCREMENT never hands out one
// again, so an old message's number can never name a newer draft. A card
// approval is registered with its card instead (card-messages.mjs).

export function migrateApprovals(db) {
  db.exec(`CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_key TEXT NOT NULL UNIQUE,
    im TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    draft_json TEXT NOT NULL,
    edited INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'pending',
    superseded_by INTEGER,
    created_at_ms INTEGER NOT NULL,
    expires_at_ms INTEGER NOT NULL,
    settled_at_ms INTEGER
  )`);
}

function toApproval(row, now) {
  if (!row) return null;
  return {
    id: Number(row.id),
    im: row.im,
    ownerId: row.owner_id,
    draft: JSON.parse(row.draft_json),
    edited: Boolean(row.edited),
    // A pending draft past its time is expired, whether or not anyone looked.
    state: row.state === 'pending' && Number(row.expires_at_ms) <= now ? 'expired' : row.state,
    supersededBy: row.superseded_by === null ? null : Number(row.superseded_by),
  };
}

export function getApproval(db, id, now = Date.now()) {
  return toApproval(db.prepare('SELECT * FROM approvals WHERE id = ?').get(id), now);
}

// One row per proposal request: the same request again finds its first row.
export function createApproval(db, { requestKey, im, ownerId, draft, ttlMs, now = Date.now() }) {
  const existing = db.prepare('SELECT * FROM approvals WHERE request_key = ?').get(requestKey);
  if (existing) return { approval: toApproval(existing, now), created: false };
  const { lastInsertRowid } = db.prepare(`INSERT INTO approvals(request_key, im, owner_id, draft_json, created_at_ms, expires_at_ms)
    VALUES (?, ?, ?, ?, ?, ?)`).run(requestKey, im, ownerId, JSON.stringify(draft), now, now + ttlMs);
  return { approval: getApproval(db, lastInsertRowid, now), created: true };
}

function pending(db, sql, args, now) {
  return db.prepare(`${sql} WHERE id = ? AND state = 'pending' AND expires_at_ms > ?`).run(...args, now).changes > 0;
}

// First answer wins: only a pending, unexpired draft can start sending.
export function claimApproval(db, id, now = Date.now()) {
  return pending(db, "UPDATE approvals SET state = 'sending', settled_at_ms = ?", [now, id], now);
}

export function cancelApproval(db, id, now = Date.now()) {
  return pending(db, "UPDATE approvals SET state = 'cancelled', settled_at_ms = ?", [now, id], now);
}

export function editApproval(db, id, text, now = Date.now()) {
  const approval = getApproval(db, id, now);
  if (approval?.state !== 'pending') return null;
  const draft = { ...approval.draft, text };
  return pending(db, 'UPDATE approvals SET draft_json = ?, edited = 1', [JSON.stringify(draft), id], now)
    ? getApproval(db, id, now) : null;
}

export function finishApproval(db, id, state, now = Date.now()) {
  db.prepare('UPDATE approvals SET state = ?, settled_at_ms = ? WHERE id = ?').run(state, now, id);
}

// A newer draft for the same mail replaces the pending ones, so the owner can
// only send a version they were shown last.
export function supersedeApprovals(db, { connector, messageId, by, now = Date.now() }) {
  const rows = db.prepare("SELECT id, draft_json FROM approvals WHERE state = 'pending' AND id <> ?").all(by);
  for (const row of rows) {
    const draft = JSON.parse(row.draft_json);
    if (draft.connector !== connector || draft.message_id !== messageId) continue;
    db.prepare("UPDATE approvals SET state = 'superseded', superseded_by = ?, settled_at_ms = ? WHERE id = ? AND state = 'pending'")
      .run(by, now, row.id);
  }
}

export function pruneApprovals(db, cutoff) {
  return db.prepare('DELETE FROM approvals WHERE COALESCE(settled_at_ms, expires_at_ms) < ?').run(cutoff).changes;
}
