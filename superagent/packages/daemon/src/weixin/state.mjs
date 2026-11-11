// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

function normalizedBaseUrl(value) {
  let parsed;
  try { parsed = new URL(value); } catch {
    throw new Error('Weixin API base URL is invalid');
  }
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error('Weixin API base URL must be an HTTPS URL without credentials or query data');
  }
  return parsed.toString().replace(/\/+$/, '');
}

export function weixinStatePath(dataDir) {
  return path.join(dataDir, 'weixin', 'state.sqlite3');
}

export class WeixinState {
  constructor(dataDir) {
    if (!path.isAbsolute(dataDir)) throw new Error('Weixin data directory must be absolute');
    const directory = path.join(dataDir, 'weixin');
    fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
    try { fs.chmodSync(directory, 0o700); } catch {}
    this.databasePath = weixinStatePath(dataDir);
    this.mediaDirectory = path.join(directory, 'media');
    this.db = new DatabaseSync(this.databasePath);
    try { fs.chmodSync(this.databasePath, 0o600); } catch {}
    // Waits out another process holding the database (the CLI next to a running daemon).
    this.db.exec('PRAGMA busy_timeout = 5000');
    this.db.exec('PRAGMA journal_mode = WAL');
    this.db.exec('PRAGMA synchronous = FULL');
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS account (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
        account_id TEXT NOT NULL,
        token TEXT NOT NULL,
        base_url TEXT NOT NULL,
        user_id TEXT,
        saved_at_ms INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS transport_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at_ms INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS context_tokens (
        peer_id TEXT PRIMARY KEY,
        token TEXT NOT NULL,
        updated_at_ms INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS inbound_routes (
        local_message_id TEXT PRIMARY KEY,
        provider_message_id TEXT NOT NULL,
        peer_id TEXT NOT NULL,
        received_at_ms INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS inbound_media (
        local_message_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        kind TEXT NOT NULL,
        local_path TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        file_name TEXT NOT NULL,
        byte_size INTEGER NOT NULL,
        created_at_ms INTEGER NOT NULL,
        PRIMARY KEY(local_message_id, ordinal)
      );
    `);
  }

  transaction(action) {
    this.db.exec('BEGIN IMMEDIATE');
    try {
      const result = action();
      this.db.exec('COMMIT');
      return result;
    } catch (error) {
      try { this.db.exec('ROLLBACK'); } catch {}
      throw error;
    }
  }

  account() {
    const row = this.db.prepare('SELECT * FROM account WHERE singleton = 1').get();
    if (!row) return null;
    return {
      accountId: row.account_id,
      token: row.token,
      baseUrl: row.base_url,
      userId: row.user_id || null,
      savedAtMs: Number(row.saved_at_ms),
    };
  }

  saveAccount({ accountId, token, baseUrl, userId = null }, now = Date.now()) {
    if (!accountId || !token || !baseUrl) throw new Error('Incomplete Weixin account credentials');
    const apiBaseUrl = normalizedBaseUrl(baseUrl);
    let discardedMedia = [];
    this.transaction(() => {
      const previous = this.db.prepare(
        'SELECT account_id, user_id FROM account WHERE singleton = 1',
      ).get();
      if (previous && (previous.account_id !== accountId || previous.user_id !== userId)) {
        discardedMedia = this.db.prepare('SELECT local_path FROM inbound_media').all()
          .map((row) => row.local_path);
        this.db.exec('DELETE FROM transport_state; DELETE FROM context_tokens; DELETE FROM inbound_routes; DELETE FROM inbound_media');
      }
      this.db.prepare(`
        INSERT INTO account(singleton, account_id, token, base_url, user_id, saved_at_ms)
        VALUES (1, ?, ?, ?, ?, ?)
        ON CONFLICT(singleton) DO UPDATE SET
          account_id = excluded.account_id,
          token = excluded.token,
          base_url = excluded.base_url,
          user_id = excluded.user_id,
          saved_at_ms = excluded.saved_at_ms
      `).run(accountId, token, apiBaseUrl, userId, now);
    });
    for (const filename of discardedMedia) {
      try { fs.rmSync(filename, { force: true }); } catch {}
    }
    return this.account();
  }

  cursor() {
    return this.db.prepare("SELECT value FROM transport_state WHERE key = 'get_updates_buf'").get()?.value ?? '';
  }

  saveCursor(value, now = Date.now()) {
    if (typeof value !== 'string' || !value) return false;
    this.db.prepare(`
      INSERT INTO transport_state(key, value, updated_at_ms) VALUES ('get_updates_buf', ?, ?)
      ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at_ms = excluded.updated_at_ms
    `).run(value, now);
    return true;
  }

  recordInbound({ localMessageId, providerMessageId, peerId, contextToken = null }, now = Date.now()) {
    return this.transaction(() => {
      if (contextToken) {
        this.db.prepare(`
          INSERT INTO context_tokens(peer_id, token, updated_at_ms) VALUES (?, ?, ?)
          ON CONFLICT(peer_id) DO UPDATE SET token = excluded.token, updated_at_ms = excluded.updated_at_ms
        `).run(peerId, contextToken, now);
      }
      this.db.prepare(`
        INSERT INTO inbound_routes(local_message_id, provider_message_id, peer_id, received_at_ms)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(local_message_id) DO UPDATE SET
          provider_message_id = excluded.provider_message_id,
          peer_id = excluded.peer_id,
          received_at_ms = excluded.received_at_ms
      `).run(localMessageId, providerMessageId, peerId, now);
      return true;
    });
  }

  routeForMessage(localMessageId) {
    const row = this.db.prepare(`
      SELECT r.peer_id, r.provider_message_id, c.token AS context_token
      FROM inbound_routes r
      LEFT JOIN context_tokens c ON c.peer_id = r.peer_id
      WHERE r.local_message_id = ?
    `).get(localMessageId);
    return row ? {
      peerId: row.peer_id,
      providerMessageId: row.provider_message_id,
      contextToken: row.context_token || null,
    } : null;
  }

  replaceInboundMedia(localMessageId, media, now = Date.now()) {
    if (typeof localMessageId !== 'string' || !localMessageId) throw new Error('Weixin message id is required');
    if (!Array.isArray(media) || media.length > 4) throw new Error('Weixin media list is invalid');
    const root = path.resolve(this.mediaDirectory);
    for (const item of media) {
      const localPath = path.resolve(item?.localPath ?? '');
      if (!Number.isInteger(item?.ordinal) || item.ordinal < 0
        || !['image', 'file'].includes(item?.kind)
        || !localPath.startsWith(`${root}${path.sep}`)
        || typeof item?.mimeType !== 'string' || !item.mimeType
        || typeof item?.fileName !== 'string' || !item.fileName
        || !Number.isSafeInteger(item?.byteSize) || item.byteSize < 1) {
        throw new Error('Weixin media record is invalid');
      }
    }
    const previous = this.db.prepare(
      'SELECT local_path FROM inbound_media WHERE local_message_id = ?',
    ).all(localMessageId).map((row) => row.local_path);
    this.transaction(() => {
      this.db.prepare('DELETE FROM inbound_media WHERE local_message_id = ?').run(localMessageId);
      const insert = this.db.prepare(`
        INSERT INTO inbound_media(
          local_message_id, ordinal, kind, local_path, mime_type, file_name, byte_size, created_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
      `);
      for (const item of media) {
        insert.run(
          localMessageId,
          item.ordinal,
          item.kind,
          path.resolve(item.localPath),
          item.mimeType,
          item.fileName,
          item.byteSize,
          now,
        );
      }
    });
    const retained = new Set(media.map((item) => path.resolve(item.localPath)));
    for (const filename of previous) {
      if (!retained.has(filename)) {
        try { fs.rmSync(filename, { force: true }); } catch {}
      }
    }
    return media.length;
  }

  mediaForMessage(localMessageId) {
    const root = path.resolve(this.mediaDirectory);
    return this.db.prepare(`
      SELECT ordinal, kind, local_path, mime_type, file_name, byte_size
      FROM inbound_media
      WHERE local_message_id = ?
      ORDER BY ordinal
    `).all(localMessageId).flatMap((row) => {
      const localPath = path.resolve(row.local_path);
      if (!localPath.startsWith(`${root}${path.sep}`)) return [];
      let stat;
      try { stat = fs.lstatSync(localPath); } catch { return []; }
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size !== Number(row.byte_size)) return [];
      return [{
        ordinal: Number(row.ordinal),
        kind: row.kind,
        path: localPath,
        mimeType: row.mime_type,
        fileName: row.file_name,
        byteSize: Number(row.byte_size),
      }];
    });
  }

  pruneMedia(beforeMs) {
    if (!Number.isFinite(beforeMs)) return 0;
    const rows = this.db.prepare(
      'SELECT local_message_id, ordinal, local_path FROM inbound_media WHERE created_at_ms < ?',
    ).all(beforeMs);
    if (rows.length === 0) return 0;
    this.transaction(() => {
      this.db.prepare('DELETE FROM inbound_media WHERE created_at_ms < ?').run(beforeMs);
    });
    for (const row of rows) {
      try { fs.rmSync(row.local_path, { force: true }); } catch {}
    }
    return rows.length;
  }

  contextToken(peerId) {
    return this.db.prepare('SELECT token FROM context_tokens WHERE peer_id = ?').get(peerId)?.token ?? null;
  }

  close() {
    try { this.db.close(); } catch {}
  }
}
