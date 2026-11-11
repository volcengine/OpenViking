// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

// The Email Connector's own state: how far it has read the inbox, and the
// headers a reply needs for each mail it reported. Private to the daemon user.
// One database per mailbox instance: each mailbox runs in its own process.
export class EmailState {
  constructor(dataDir, instance = 'email') {
    if (!path.isAbsolute(dataDir)) throw new Error('Email data directory must be absolute');
    if (!/^email(-[a-z0-9-]+)?$/.test(instance)) throw new Error('Email instance name is invalid');
    const directory = path.join(dataDir, 'email', instance);
    fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
    try { fs.chmodSync(directory, 0o700); } catch {}
    const databasePath = path.join(directory, 'state.sqlite3');
    this.db = new DatabaseSync(databasePath);
    try { fs.chmodSync(databasePath, 0o600); } catch {}
    // Waits out another process holding the database (the CLI next to a running daemon).
    this.db.exec('PRAGMA busy_timeout = 5000');
    this.db.exec('PRAGMA journal_mode = WAL');
    this.db.exec('PRAGMA synchronous = FULL');
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS cursors (
        mailbox TEXT PRIMARY KEY,
        uid_validity INTEGER NOT NULL,
        last_uid INTEGER NOT NULL,
        updated_at_ms INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS mails (
        message_id TEXT PRIMARY KEY,
        reply_json TEXT NOT NULL,
        received_at_ms INTEGER NOT NULL
      );
      CREATE INDEX IF NOT EXISTS mails_received ON mails(received_at_ms);
    `);
  }

  cursor(mailbox) {
    const row = this.db.prepare('SELECT uid_validity, last_uid FROM cursors WHERE mailbox = ?').get(mailbox);
    return row ? { uidValidity: Number(row.uid_validity), lastUid: Number(row.last_uid) } : null;
  }

  saveCursor(mailbox, { uidValidity, lastUid }, now = Date.now()) {
    this.db.prepare(`INSERT INTO cursors(mailbox, uid_validity, last_uid, updated_at_ms) VALUES (?, ?, ?, ?)
      ON CONFLICT(mailbox) DO UPDATE SET uid_validity = excluded.uid_validity, last_uid = excluded.last_uid,
        updated_at_ms = excluded.updated_at_ms`).run(mailbox, uidValidity, lastUid, now);
  }

  remember(messageId, reply, now = Date.now()) {
    this.db.prepare(`INSERT INTO mails(message_id, reply_json, received_at_ms) VALUES (?, ?, ?)
      ON CONFLICT(message_id) DO UPDATE SET reply_json = excluded.reply_json`).run(messageId, JSON.stringify(reply), now);
  }

  mail(messageId) {
    const row = this.db.prepare('SELECT reply_json FROM mails WHERE message_id = ?').get(messageId);
    return row ? JSON.parse(row.reply_json) : null;
  }

  prune(before) {
    this.db.prepare('DELETE FROM mails WHERE received_at_ms < ?').run(before);
  }

  close() {
    this.db.close();
  }
}
