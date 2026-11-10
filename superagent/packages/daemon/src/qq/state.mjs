// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
import { credentials, openid, REPLY_WINDOW_MS } from './api.mjs';

export class QQState {
  constructor(dataDir) {
    if (!path.isAbsolute(dataDir)) throw new Error('QQ data directory must be absolute');
    this.directory = path.join(dataDir, 'qq');
    this.mediaDirectory = path.join(this.directory, 'media');
    fs.mkdirSync(this.mediaDirectory, { recursive: true, mode: 0o700 });
    fs.chmodSync(this.directory, 0o700);
    fs.chmodSync(this.mediaDirectory, 0o700);
    this.databasePath = path.join(this.directory, 'state.sqlite3');
    this.db = new DatabaseSync(this.databasePath);
    fs.chmodSync(this.databasePath, 0o600);
    this.db.exec(`
      PRAGMA busy_timeout = 5000;
      PRAGMA journal_mode = WAL;
      PRAGMA synchronous = FULL;
      CREATE TABLE IF NOT EXISTS account (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1), app_id TEXT NOT NULL,
        app_secret TEXT NOT NULL, owner_id TEXT NOT NULL, activated_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS routes (
        id TEXT PRIMARY KEY, native_id TEXT NOT NULL, chat_id TEXT NOT NULL,
        ref_idx TEXT, occurred_at INTEGER NOT NULL, incoming INTEGER NOT NULL,
        media_json TEXT NOT NULL, saved_at INTEGER NOT NULL
      );
      CREATE INDEX IF NOT EXISTS routes_ref ON routes(ref_idx);
      CREATE TABLE IF NOT EXISTS replies (
        native_id TEXT PRIMARY KEY, used INTEGER NOT NULL, expires_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS pending (id TEXT PRIMARY KEY, event_json TEXT NOT NULL, saved_at INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS receiver (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1), pid INTEGER NOT NULL, lease TEXT NOT NULL
      );
    `);
    this.lease = null;
  }

  transaction(fn) {
    this.db.exec('BEGIN IMMEDIATE');
    try { const result = fn(); this.db.exec('COMMIT'); return result; }
    catch (error) { this.db.exec('ROLLBACK'); throw error; }
  }

  account() {
    const row = this.db.prepare('SELECT * FROM account WHERE singleton = 1').get();
    return row ? { appId: row.app_id, appSecret: row.app_secret, ownerId: row.owner_id, activatedAt: row.activated_at } : null;
  }

  acquire() {
    const lease = randomUUID();
    this.transaction(() => {
      const old = this.db.prepare('SELECT * FROM receiver WHERE singleton = 1').get();
      if (old) {
        let alive = true;
        try { process.kill(Number(old.pid), 0); } catch (error) { alive = error.code !== 'ESRCH'; }
        if (alive) throw new Error('QQ is already in use; stop this instance before logging in or starting another receiver');
      }
      this.db.prepare('INSERT OR REPLACE INTO receiver VALUES (1, ?, ?)').run(process.pid, lease);
    });
    this.lease = lease;
  }

  release() {
    if (this.lease) this.db.prepare('DELETE FROM receiver WHERE lease = ?').run(this.lease);
    this.lease = null;
  }

  saveAccount(account) {
    credentials(account.appId, account.appSecret); openid(account.ownerId);
    if (!Number.isSafeInteger(account.activatedAt) || account.activatedAt < 0) throw new Error('Invalid QQ activation time');
    this.transaction(() => {
      const old = this.account();
      if (!old || old.appId !== account.appId || old.ownerId !== account.ownerId) {
        this.db.exec('DELETE FROM routes; DELETE FROM replies; DELETE FROM pending;');
      }
      this.db.prepare('INSERT OR REPLACE INTO account VALUES (1, ?, ?, ?, ?)')
        .run(account.appId, account.appSecret, account.ownerId, account.activatedAt);
    });
  }

  recordRoute({ id, nativeId, chatId, refIdx = null, at, incoming = false, media = [] }) {
    this.transaction(() => {
      this.db.prepare('INSERT OR IGNORE INTO routes VALUES (?, ?, ?, ?, ?, ?, ?, ?)')
        .run(id, nativeId, chatId, refIdx, at, incoming ? 1 : 0, JSON.stringify(media), Date.now());
      if (incoming) this.db.prepare('INSERT OR IGNORE INTO replies VALUES (?, 0, ?)').run(nativeId, at + REPLY_WINDOW_MS);
    });
  }

  route(id) {
    const row = this.db.prepare('SELECT * FROM routes WHERE id = ?').get(id);
    return row ? { id: row.id, nativeId: row.native_id, chatId: row.chat_id, refIdx: row.ref_idx,
      at: row.occurred_at, incoming: Boolean(row.incoming), media: JSON.parse(row.media_json) } : null;
  }

  byReference(refIdx) {
    const row = this.db.prepare('SELECT id FROM routes WHERE ref_idx = ? ORDER BY saved_at DESC LIMIT 1').get(refIdx);
    return row ? this.route(row.id) : null;
  }

  checkReply(nativeId, now = Date.now()) {
    const row = this.db.prepare('SELECT * FROM replies WHERE native_id = ?').get(nativeId);
    if (!row || row.expires_at <= now) throw new Error('QQ reply expired; wait for a new owner message');
    if (row.used >= 4) throw new Error('QQ reply count limit reached; wait for a new owner message');
    return row.used;
  }

  reserveReply(nativeId, now = Date.now()) {
    return this.transaction(() => {
      // Reserve before POST, including attempts with an uncertain outcome.
      // Duplicate delivery or a worker restart must never reset this counter.
      const seq = this.checkReply(nativeId, now) + 1;
      this.db.prepare('UPDATE replies SET used = ? WHERE native_id = ?').run(seq, nativeId);
      return seq;
    });
  }

  stage(event) {
    this.db.prepare('INSERT OR IGNORE INTO pending VALUES (?, ?, ?)').run(event.id, JSON.stringify(event), Date.now());
  }
  pending() { return this.db.prepare('SELECT event_json FROM pending ORDER BY saved_at, rowid').all().map((row) => JSON.parse(row.event_json)); }
  delivered(id) { this.db.prepare('DELETE FROM pending WHERE id = ?').run(id); }

  prune(before) {
    this.db.prepare('DELETE FROM routes WHERE saved_at < ?').run(before);
    this.db.prepare('DELETE FROM replies WHERE expires_at < ?').run(before);
    for (const entry of fs.readdirSync(this.mediaDirectory, { withFileTypes: true })) {
      const filename = path.join(this.mediaDirectory, entry.name);
      if (entry.isFile() && fs.statSync(filename).mtimeMs < before) fs.rmSync(filename);
    }
  }

  close() { if (this.db) { this.release(); this.db.close(); this.db = null; } }
}
