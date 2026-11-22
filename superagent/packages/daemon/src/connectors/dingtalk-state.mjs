// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

const REPLAY_WINDOW_MS = 24 * 60 * 60 * 1000;

export class DingTalkState {
  constructor(dataDir, account) {
    const directory = path.join(dataDir, 'dingtalk');
    fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
    fs.chmodSync(directory, 0o700);
    const filename = path.join(directory, 'receive.sqlite3');
    this.account = account;
    this.db = new DatabaseSync(filename);
    try {
      fs.chmodSync(filename, 0o600);
      this.db.exec(`
        PRAGMA busy_timeout = 2500;
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = FULL;
        CREATE TABLE IF NOT EXISTS received (
          seq INTEGER PRIMARY KEY AUTOINCREMENT,
          account TEXT NOT NULL,
          event_id TEXT NOT NULL,
          envelope TEXT NOT NULL,
          forwarded_at INTEGER,
          UNIQUE(account, event_id)
        );
        CREATE INDEX IF NOT EXISTS received_account_seq ON received(account, seq);
        CREATE INDEX IF NOT EXISTS received_expiry ON received(forwarded_at) WHERE forwarded_at IS NOT NULL;
      `);
      this.insert = this.db.prepare('INSERT OR IGNORE INTO received (account, event_id, envelope) VALUES (?, ?, ?)');
      this.readNext = this.db.prepare('SELECT seq, envelope FROM received WHERE account = ? AND seq > ? ORDER BY seq LIMIT 1');
      this.markForwarded = this.db.prepare('UPDATE received SET forwarded_at = ? WHERE seq = ? AND forwarded_at IS NULL');
      this.expire = this.db.prepare('DELETE FROM received WHERE forwarded_at < ?');
      this.prune();
    } catch (error) {
      this.db.close();
      throw error;
    }
  }

  stage(event) { this.insert.run(this.account, event.id, JSON.stringify(event)); }
  next(after) { return this.readNext.get(this.account, after); }
  forwarded(seq) { this.markForwarded.run(Date.now(), seq); }
  close() { this.db.close(); }

  // A stdout write is not a Core receipt. Keep forwarded rows for replay;
  // retain rows that have never been forwarded until they can be handed off.
  prune() { this.expire.run(Date.now() - REPLAY_WINDOW_MS); }
}
