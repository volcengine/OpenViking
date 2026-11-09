// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { validToken, validUrl } from './api.mjs';

const QQ_ID = /^[1-9][0-9]{0,15}$/;
const CLASSES = new Set(['direct', 'ambient', 'context']);

// Address, token, owner, reply routes, names and the lines waiting for
// release of one instance's OneBot account, in a database only the daemon
// user can read. There is no receiver lease: OneBot hands every event to
// every connection instead of splitting them.
export class OneBotState {
  constructor(dataDir) {
    if (!path.isAbsolute(dataDir)) throw new Error('OneBot data directory must be absolute');
    this.directory = path.join(dataDir, 'onebot');
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
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1), url TEXT NOT NULL, token TEXT NOT NULL,
        self_id TEXT NOT NULL, self_name TEXT NOT NULL, owner_id TEXT NOT NULL, activated_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS routes (
        id TEXT PRIMARY KEY, chat TEXT NOT NULL, mid TEXT NOT NULL, files_json TEXT, saved_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS people (
        chat TEXT NOT NULL, user_id TEXT NOT NULL, name TEXT NOT NULL, saved_at INTEGER NOT NULL,
        PRIMARY KEY (chat, user_id)
      );
      CREATE TABLE IF NOT EXISTS sent (
        chat TEXT NOT NULL, mid TEXT NOT NULL, saved_at INTEGER NOT NULL, PRIMARY KEY (chat, mid)
      );
      CREATE TABLE IF NOT EXISTS pending (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, chat TEXT NOT NULL, class TEXT NOT NULL, at INTEGER NOT NULL,
        envelope_json TEXT NOT NULL
      );
    `);
  }

  transaction(fn) {
    this.db.exec('BEGIN IMMEDIATE');
    try { const result = fn(); this.db.exec('COMMIT'); return result; }
    catch (error) { this.db.exec('ROLLBACK'); throw error; }
  }

  account() {
    const row = this.db.prepare('SELECT * FROM account WHERE singleton = 1').get();
    return row ? { url: row.url, token: row.token, selfId: row.self_id, selfName: row.self_name, ownerId: row.owner_id,
      activatedAt: Number(row.activated_at) } : null;
  }

  saveAccount({ url, token, selfId, selfName, ownerId, activatedAt }) {
    if (!validUrl(url) || !validToken(token) || !QQ_ID.test(selfId ?? '') || !QQ_ID.test(ownerId ?? '')
      || typeof selfName !== 'string' || selfName.length > 256 || !Number.isSafeInteger(activatedAt)) {
      throw new Error('Invalid OneBot account');
    }
    this.transaction(() => {
      // Another QQ account: its routes, names, sends and waiting lines mean nothing here.
      if (this.account()?.selfId !== selfId) {
        this.db.exec('DELETE FROM routes; DELETE FROM people; DELETE FROM sent; DELETE FROM pending;');
      }
      this.db.prepare('INSERT OR REPLACE INTO account VALUES (1, ?, ?, ?, ?, ?, ?)')
        .run(url, token, selfId, selfName, ownerId, activatedAt);
    });
  }

  // A route seen again keeps the files it was first recorded with unless the
  // new sighting knows them: a send knows none, an event or history entry does.
  recordRoute(id, { chat, mid, files = null }) {
    this.db.prepare(`INSERT INTO routes VALUES (?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET saved_at = excluded.saved_at,
        files_json = COALESCE(excluded.files_json, routes.files_json)`)
      .run(id, chat, String(mid), files?.length ? JSON.stringify(files) : null, Date.now());
  }

  route(id) {
    const row = this.db.prepare('SELECT * FROM routes WHERE id = ?').get(id);
    return row ? { chat: row.chat, mid: row.mid, files: row.files_json ? JSON.parse(row.files_json) : [] } : null;
  }

  // People this connector has named to the model in a chat, so a mention by
  // that name resolves to the same person.
  rememberPerson(chat, userId, name) {
    this.db.prepare(`INSERT INTO people VALUES (?, ?, ?, ?)
      ON CONFLICT(chat, user_id) DO UPDATE SET name = excluded.name, saved_at = excluded.saved_at`)
      .run(chat, String(userId), name, Date.now());
  }

  people(chat) {
    return this.db.prepare('SELECT user_id, name FROM people WHERE chat = ?').all(chat)
      .map((row) => ({ id: row.user_id, name: row.name }));
  }

  // Messages this connector sent: the account's own messages also come back
  // as events, and only these are the agent's echo.
  recordSent(chat, mid) {
    this.db.prepare('INSERT OR REPLACE INTO sent VALUES (?, ?, ?)').run(chat, String(mid), Date.now());
  }

  isSent(chat, mid) {
    return Boolean(this.db.prepare('SELECT 1 FROM sent WHERE chat = ? AND mid = ?').get(chat, String(mid)));
  }

  // Classified lines wait here until their chat is released, so a restart
  // loses none of them.
  stage({ chat, cls, at, envelope }) {
    if (!CLASSES.has(cls) || !Number.isSafeInteger(at)) throw new Error('Invalid OneBot pending line');
    return Number(this.db.prepare('INSERT INTO pending (chat, class, at, envelope_json) VALUES (?, ?, ?, ?)')
      .run(chat, cls, at, JSON.stringify(envelope)).lastInsertRowid);
  }

  pendingChats() {
    const chats = new Map();
    for (const row of this.db.prepare('SELECT chat, class FROM pending ORDER BY seq').all()) {
      if (!chats.has(row.chat)) chats.set(row.chat, new Set());
      chats.get(row.chat).add(row.class);
    }
    return [...chats].map(([chat, classes]) => ({ chat, classes }));
  }

  pending(chat) {
    return this.db.prepare('SELECT * FROM pending WHERE chat = ? ORDER BY seq').all(chat)
      .map((row) => ({ seq: Number(row.seq), cls: row.class, at: Number(row.at), envelope: JSON.parse(row.envelope_json) }));
  }

  delivered(seq) { this.db.prepare('DELETE FROM pending WHERE seq = ?').run(seq); }

  // Context lines of a chat older than `before` are dropped unseen, then all
  // but the newest `maxLines` of the rest, so an old line does not take the
  // place of a fresh one.
  pruneContext(chat, { maxLines, before }) {
    this.db.prepare(`DELETE FROM pending WHERE chat = ? AND class = 'context' AND at < ?`).run(chat, before);
    this.db.prepare(`DELETE FROM pending WHERE chat = ? AND class = 'context' AND seq NOT IN (
      SELECT seq FROM pending WHERE chat = ? AND class = 'context' ORDER BY seq DESC LIMIT ?)`)
      .run(chat, chat, maxLines);
  }

  prune(before) {
    this.db.prepare('DELETE FROM routes WHERE saved_at < ?').run(before);
    this.db.prepare('DELETE FROM people WHERE saved_at < ?').run(before);
    this.db.prepare('DELETE FROM sent WHERE saved_at < ?').run(before);
    for (const entry of fs.readdirSync(this.mediaDirectory, { withFileTypes: true })) {
      const filename = path.join(this.mediaDirectory, entry.name);
      if (entry.isFile() && fs.statSync(filename).mtimeMs < before) fs.rmSync(filename);
    }
  }

  close() { if (this.db) { this.db.close(); this.db = null; } }
}
