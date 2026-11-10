// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
import { integer } from './api.mjs';

export class TelegramState {
  constructor(dataDir) {
    if (!path.isAbsolute(dataDir)) throw new Error('Telegram data directory must be absolute');
    this.directory = path.join(dataDir, 'telegram');
    this.mediaDirectory = path.join(this.directory, 'media');
    fs.mkdirSync(this.mediaDirectory, { recursive: true, mode: 0o700 });
    fs.chmodSync(this.directory, 0o700);
    this.databasePath = path.join(this.directory, 'state.sqlite3');
    this.db = new DatabaseSync(this.databasePath);
    fs.chmodSync(this.databasePath, 0o600);
    this.db.exec(`
      PRAGMA busy_timeout = 5000;
      PRAGMA journal_mode = WAL;
      PRAGMA synchronous = FULL;
      CREATE TABLE IF NOT EXISTS account (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1), bot_id TEXT NOT NULL,
        owner_id TEXT NOT NULL, chat_id TEXT NOT NULL, token TEXT NOT NULL,
        offset INTEGER NOT NULL, activated_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS routes (
        id TEXT PRIMARY KEY, native_id INTEGER NOT NULL, chat_id TEXT NOT NULL,
        media_json TEXT, saved_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS pending (
        bot_id TEXT NOT NULL, update_id INTEGER NOT NULL, event_json TEXT NOT NULL,
        PRIMARY KEY (bot_id, update_id)
      );
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
    return row ? { botId: row.bot_id, ownerId: row.owner_id, chatId: row.chat_id, token: row.token,
      offset: Number(row.offset), activatedAt: Number(row.activated_at) } : null;
  }

  acquire() {
    const lease = randomUUID();
    this.transaction(() => {
      const old = this.db.prepare('SELECT * FROM receiver WHERE singleton = 1').get();
      if (old) {
        let alive = true;
        try { process.kill(Number(old.pid), 0); } catch (error) { alive = error.code !== 'ESRCH'; }
        if (alive) throw new Error('Telegram is already in use; stop this instance before logging in or starting another receiver');
      }
      this.db.prepare('INSERT OR REPLACE INTO receiver VALUES (1, ?, ?)').run(process.pid, lease);
    });
    this.lease = lease;
  }

  release() {
    if (this.lease) this.db.prepare('DELETE FROM receiver WHERE lease = ?').run(this.lease);
    this.lease = null;
  }

  saveAccount({ botId, ownerId, chatId, token, offset, activatedAt }) {
    if (![botId, ownerId, chatId].every((id) => typeof id === 'string' && /^[1-9]\d*$/.test(id)
      && Number.isSafeInteger(Number(id))) || !token) throw new Error('Invalid Telegram account');
    integer(offset, 'offset'); integer(activatedAt, 'activation time');
    this.transaction(() => {
      const old = this.account();
      if (!old || old.botId !== botId || old.ownerId !== ownerId || old.chatId !== chatId) {
        this.db.exec('DELETE FROM routes; DELETE FROM pending;');
      }
      this.db.prepare('INSERT OR REPLACE INTO account VALUES (1, ?, ?, ?, ?, ?, ?)')
        .run(botId, ownerId, chatId, token, offset, activatedAt);
    });
  }

  saveCursor(offset) {
    this.db.prepare('UPDATE account SET offset = ? WHERE singleton = 1').run(integer(offset, 'offset'));
  }

  recordRoute(id, nativeId, chatId, media = null) {
    this.db.prepare(`INSERT INTO routes VALUES (?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET saved_at = excluded.saved_at`)
      .run(id, nativeId, chatId, media ? JSON.stringify(media) : null, Date.now());
  }

  route(id) {
    const row = this.db.prepare('SELECT * FROM routes WHERE id = ?').get(id);
    return row ? { nativeId: Number(row.native_id), chatId: row.chat_id,
      media: row.media_json ? JSON.parse(row.media_json) : null } : null;
  }

  // Reauthorization polls also acknowledge updates. Stage the existing
  // owner's canonical events before advancing that poll, even if login is
  // later cancelled. This is local to pairing, not a Core ACK protocol.
  stage(botId, updateId, event) {
    this.db.prepare('INSERT OR IGNORE INTO pending VALUES (?, ?, ?)').run(botId, updateId, JSON.stringify(event));
  }

  pending(botId) {
    return this.db.prepare('SELECT * FROM pending WHERE bot_id = ? ORDER BY update_id').all(botId)
      .map((row) => ({ updateId: Number(row.update_id), event: JSON.parse(row.event_json) }));
  }

  delivered(botId, updateId) { this.db.prepare('DELETE FROM pending WHERE bot_id = ? AND update_id = ?').run(botId, updateId); }

  prune(before) {
    this.db.prepare('DELETE FROM routes WHERE saved_at < ?').run(before);
    for (const entry of fs.readdirSync(this.mediaDirectory, { withFileTypes: true })) {
      const filename = path.join(this.mediaDirectory, entry.name);
      if (entry.isFile() && fs.statSync(filename).mtimeMs < before) fs.rmSync(filename);
    }
  }

  close() { if (this.db) { this.release(); this.db.close(); this.db = null; } }
}
