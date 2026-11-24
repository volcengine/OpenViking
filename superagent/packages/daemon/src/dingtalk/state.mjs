// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

// Group messages taken from the Stream and not yet handed to the Core. A
// message is committed here before DingTalk is acknowledged and removed once
// written to the Core, so one caught between the two goes after a restart.
export class DingTalkState {
  constructor(dataDir) {
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
    `);
  }

  // A redelivery of a message still waiting here is the same message.
  stage(event) {
    this.db.prepare('INSERT OR IGNORE INTO pending (event_id, envelope) VALUES (?, ?)').run(event.id, JSON.stringify(event));
  }

  pending(limit = 100) {
    return this.db.prepare('SELECT seq, envelope FROM pending ORDER BY seq LIMIT ?').all(limit);
  }

  delivered(seq) { this.db.prepare('DELETE FROM pending WHERE seq = ?').run(seq); }

  close() { this.db.close(); }
}
