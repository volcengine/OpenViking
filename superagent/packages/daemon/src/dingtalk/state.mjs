// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

// Group messages taken from the Stream and not yet handed to the Core. A
// message is committed here before DingTalk is acknowledged and removed once
// written to the Core, so one caught between the two goes after a restart.
export class DingTalkState {
  constructor(dataDir, profile = null) {
    this.profile = profile;
    const directory = path.join(dataDir, 'dingtalk');
    fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
    fs.chmodSync(directory, 0o700);
    const file = path.join(directory, 'state.sqlite3');
    this.db = new DatabaseSync(file);
    fs.chmodSync(file, 0o600);
    this.db.exec(`
      PRAGMA busy_timeout = 5000;
      PRAGMA journal_mode = WAL;
      PRAGMA synchronous = FULL;
      CREATE TABLE IF NOT EXISTS pending (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, envelope TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS dws_sent (
        profile TEXT NOT NULL, message_id TEXT NOT NULL, sent_at INTEGER NOT NULL,
        PRIMARY KEY (profile, message_id)
      );
    `);
    const columns = this.db.prepare('PRAGMA table_info(pending)').all().map(({ name }) => name);
    if (!columns.includes('group_id')) this.db.exec('ALTER TABLE pending ADD COLUMN group_id TEXT');
    if (!columns.includes('kind')) this.db.exec("ALTER TABLE pending ADD COLUMN kind TEXT NOT NULL DEFAULT 'stream'");
    if (!columns.includes('created_at')) this.db.exec('ALTER TABLE pending ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0');
    if (!columns.includes('profile')) this.db.exec('ALTER TABLE pending ADD COLUMN profile TEXT');
    // Old DWS rows without a Profile cannot be attributed to the new receiver.
    this.db.prepare("DELETE FROM pending WHERE kind <> 'stream' AND (profile IS NULL OR profile IS NOT ?)").run(profile);
  }

  recordDwsSent(messageId, before) {
    this.db.prepare('DELETE FROM dws_sent WHERE sent_at < ?').run(before);
    this.db.prepare('INSERT OR REPLACE INTO dws_sent VALUES (?, ?, ?)').run(this.profile, messageId, Date.now());
  }

  isDwsSent(messageId, before) {
    return Boolean(this.db.prepare('SELECT 1 FROM dws_sent WHERE profile = ? AND message_id = ? AND sent_at >= ?')
      .get(this.profile, messageId, before));
  }

  // A redelivery of a message still waiting here is the same message.
  stage(event, group = null, kind = 'stream') {
    const { changes } = this.db.prepare(
      'INSERT OR IGNORE INTO pending (event_id, envelope, group_id, kind, created_at, profile) VALUES (?, ?, ?, ?, ?, ?)',
    ).run(event.id, JSON.stringify(event), group, kind, Date.now(), kind === 'stream' ? null : this.profile);
    return changes > 0;
  }

  pending(limit = 100) {
    return this.db.prepare("SELECT seq, envelope FROM pending WHERE kind = 'stream' ORDER BY seq LIMIT ?").all(limit);
  }

  pendingGroup(group, limit = 100) {
    return this.db.prepare("SELECT seq, envelope, kind FROM pending WHERE group_id = ? AND kind <> 'stream' ORDER BY seq LIMIT ?").all(group, limit);
  }

  pendingKinds() {
    return this.db.prepare("SELECT DISTINCT group_id, kind FROM pending WHERE kind <> 'stream'").all();
  }

  reconcileGroup(group, route) {
    if (route.channel !== 'dws') this.db.prepare("DELETE FROM pending WHERE group_id = ? AND kind <> 'stream'").run(group);
    else if (route.strategy === 'mention') this.db.prepare("UPDATE pending SET kind = 'context' WHERE group_id = ? AND kind = 'ambient'").run(group);
  }

  pruneContext(group, before, maxLines) {
    this.db.prepare(`
      DELETE FROM pending WHERE kind = 'context' AND (
        created_at < ? OR (group_id = ? AND seq NOT IN (
          SELECT seq FROM pending WHERE group_id = ? AND kind = 'context' ORDER BY seq DESC LIMIT ?
        ))
      )
    `).run(before, group, group, maxLines);
  }

  delivered(seq) { this.db.prepare('DELETE FROM pending WHERE seq = ?').run(seq); }

  close() { this.db.close(); }
}
