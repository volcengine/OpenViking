// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
import { validToken } from './api.mjs';

const NAME_TTL_MS = 24 * 60 * 60 * 1000;

// Credentials, owner, reply routes, names and the receive queue of one
// instance's Slack app, in a database only the daemon user can read.
export class SlackState {
  constructor(dataDir) {
    if (!path.isAbsolute(dataDir)) throw new Error('Slack data directory must be absolute');
    this.directory = path.join(dataDir, 'slack');
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
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1), team_id TEXT NOT NULL, app_id TEXT NOT NULL,
        bot_id TEXT NOT NULL, bot_user_id TEXT NOT NULL, owner_id TEXT NOT NULL, owner_channel TEXT NOT NULL,
        bot_token TEXT NOT NULL, app_token TEXT NOT NULL, activated_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS routes (
        id TEXT PRIMARY KEY, channel TEXT NOT NULL, ts TEXT NOT NULL, thread_ts TEXT,
        files_json TEXT, saved_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS people (
        channel TEXT NOT NULL, user_id TEXT NOT NULL, name TEXT NOT NULL, saved_at INTEGER NOT NULL,
        PRIMARY KEY (channel, user_id)
      );
      CREATE TABLE IF NOT EXISTS names (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, saved_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS pending (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, team_id TEXT NOT NULL, payload_json TEXT NOT NULL
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
    return row ? { teamId: row.team_id, appId: row.app_id, botId: row.bot_id, botUserId: row.bot_user_id,
      ownerId: row.owner_id, ownerChannel: row.owner_channel, botToken: row.bot_token, appToken: row.app_token,
      activatedAt: Number(row.activated_at) } : null;
  }

  // One receiver per app on this machine: Slack splits events among every
  // open connection, so a second local receiver would silently take half.
  acquire() {
    const lease = randomUUID();
    this.transaction(() => {
      const old = this.db.prepare('SELECT * FROM receiver WHERE singleton = 1').get();
      if (old) {
        let alive = true;
        try { process.kill(Number(old.pid), 0); } catch (error) { alive = error.code !== 'ESRCH'; }
        if (alive) throw new Error('Slack is already in use; stop this instance before logging in or starting another receiver');
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
    const { teamId, appId, botId, botUserId, ownerId, ownerChannel, botToken, appToken, activatedAt } = account;
    if (!/^T[A-Z0-9]{2,30}$/.test(teamId ?? '') || !/^A[A-Z0-9]{2,30}$/.test(appId ?? '')
      || !/^B[A-Z0-9]{2,30}$/.test(botId ?? '') || !/^[UW][A-Z0-9]{2,30}$/.test(botUserId ?? '')
      || !/^[UW][A-Z0-9]{2,30}$/.test(ownerId ?? '') || !/^D[A-Z0-9]{2,30}$/.test(ownerChannel ?? '')
      || !validToken(botToken, 'bot') || !validToken(appToken, 'app') || !Number.isSafeInteger(activatedAt)) {
      throw new Error('Invalid Slack account');
    }
    this.transaction(() => {
      const old = this.account();
      // Another workspace or app: its routes, names and queued events mean nothing here.
      if (!old || old.teamId !== teamId || old.appId !== appId) {
        this.db.exec('DELETE FROM routes; DELETE FROM people; DELETE FROM names; DELETE FROM pending;');
      }
      this.db.prepare('INSERT OR REPLACE INTO account VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)')
        .run(teamId, appId, botId, botUserId, ownerId, ownerChannel, botToken, appToken, activatedAt);
    });
  }

  recordRoute(id, { channel, ts, threadTs = null, files = null }, { refresh = false } = {}) {
    this.db.prepare(`INSERT INTO routes VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET saved_at = excluded.saved_at
      ${refresh ? ', thread_ts = excluded.thread_ts, files_json = excluded.files_json' : ''}`)
      .run(id, channel, ts, threadTs, files?.length ? JSON.stringify(files) : null, Date.now());
  }

  route(id) {
    const row = this.db.prepare('SELECT * FROM routes WHERE id = ?').get(id);
    return row ? { channel: row.channel, ts: row.ts, threadTs: row.thread_ts,
      files: row.files_json ? JSON.parse(row.files_json) : [] } : null;
  }

  // People this connector has named to the model in a channel, so a mention
  // by that name resolves to the same person.
  rememberPerson(channel, userId, name) {
    this.db.prepare(`INSERT INTO people VALUES (?, ?, ?, ?)
      ON CONFLICT(channel, user_id) DO UPDATE SET name = excluded.name, saved_at = excluded.saved_at`)
      .run(channel, userId, name, Date.now());
  }

  people(channel) {
    return this.db.prepare('SELECT user_id, name FROM people WHERE channel = ?').all(channel)
      .map((row) => ({ id: row.user_id, name: row.name }));
  }

  cachedName(id, now = Date.now()) {
    const row = this.db.prepare('SELECT name, saved_at FROM names WHERE id = ?').get(id);
    return row && now - Number(row.saved_at) < NAME_TTL_MS ? row.name : null;
  }

  saveName(id, name) {
    this.db.prepare('INSERT OR REPLACE INTO names VALUES (?, ?, ?)').run(id, name, Date.now());
  }

  // Received envelopes wait here between Slack's acknowledgement and the
  // Core, so a slow name lookup never delays the acknowledgement and a crash
  // in between loses nothing.
  stage(teamId, payload) {
    this.db.prepare('INSERT INTO pending (team_id, payload_json) VALUES (?, ?)').run(teamId, JSON.stringify(payload));
  }

  pending(teamId, limit = 100) {
    return this.db.prepare('SELECT * FROM pending WHERE team_id = ? ORDER BY seq LIMIT ?').all(teamId, limit)
      .map((row) => ({ seq: Number(row.seq), payload: JSON.parse(row.payload_json) }));
  }

  delivered(seq) { this.db.prepare('DELETE FROM pending WHERE seq = ?').run(seq); }

  prune(before) {
    this.db.prepare('DELETE FROM routes WHERE saved_at < ?').run(before);
    this.db.prepare('DELETE FROM people WHERE saved_at < ?').run(before);
    this.db.prepare('DELETE FROM names WHERE saved_at < ?').run(before);
    for (const entry of fs.readdirSync(this.mediaDirectory, { withFileTypes: true })) {
      const filename = path.join(this.mediaDirectory, entry.name);
      if (entry.isFile() && fs.statSync(filename).mtimeMs < before) fs.rmSync(filename);
    }
  }

  close() { if (this.db) { this.release(); this.db.close(); this.db = null; } }
}
