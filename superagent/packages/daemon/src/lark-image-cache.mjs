// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { preparePrivateDirectory } from './private-temp.mjs';
import { validImageKey } from './card-images.mjs';

// Owned by the Lark Connector, separately from Core's outbox database. Each
// success is committed synchronously before another upload or the card send.
export class LarkImageCache {
  constructor(dataDir) {
    const directory = preparePrivateDirectory(path.join(dataDir, 'lark'));
    this.path = path.join(directory, 'image-uploads.sqlite3');
    const fd = fs.openSync(this.path, fs.constants.O_CREAT | fs.constants.O_RDWR | fs.constants.O_NOFOLLOW, 0o600);
    fs.closeSync(fd);
    fs.chmodSync(this.path, 0o600);
    this.db = new DatabaseSync(this.path);
    this.db.exec(`
      PRAGMA busy_timeout = 5000;
      PRAGMA journal_mode = WAL;
      PRAGMA synchronous = FULL;
      CREATE TABLE IF NOT EXISTS image_uploads (
        instance TEXT NOT NULL, profile TEXT NOT NULL, digest TEXT NOT NULL,
        image_key TEXT NOT NULL, uploaded_at_ms INTEGER NOT NULL,
        PRIMARY KEY (instance, profile, digest)
      );
    `);
  }

  get(instance, profile, digest) {
    return this.db.prepare('SELECT image_key FROM image_uploads WHERE instance = ? AND profile = ? AND digest = ?')
      .get(instance, profile, digest)?.image_key ?? null;
  }

  put(instance, profile, digest, key) {
    if (!validImageKey(key)) throw new Error('Lark upload returned an invalid image_key');
    this.db.prepare(`INSERT INTO image_uploads VALUES (?, ?, ?, ?, ?)
      ON CONFLICT(instance, profile, digest) DO UPDATE SET image_key = excluded.image_key, uploaded_at_ms = excluded.uploaded_at_ms`)
      .run(instance, profile, digest, key, Date.now());
  }

  close() { this.db.close(); }
}
