// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { getConfig } from '../src/config.mjs';
import { LarkClient } from '../src/lark.mjs';
import { prepareLarkMediaDirectory, pruneLarkMedia } from '../src/lark-media.mjs';
import { PersonalAgentDaemon } from '../src/daemon.mjs';

const logger = { debug() {}, info() {}, warn() {}, error() {} };

function temporaryRoot(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-lark-media-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

test('downloads and reads images outside /root, retaining them until maintenance expires them', async (t) => {
  const root = temporaryRoot(t);
  const tempDir = path.join(root, 'private');
  const previous = process.env.VIKA_TEMP_DIR;
  process.env.VIKA_TEMP_DIR = tempDir;
  t.after(() => {
    if (previous === undefined) delete process.env.VIKA_TEMP_DIR;
    else process.env.VIKA_TEMP_DIR = previous;
  });
  const config = { ...getConfig(), dataDir: '/root/.vika/data', retentionMs: 86_400_000 };
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aol8AAAAASUVORK5CYII=', 'base64');
  const lark = new LarkClient(config, {
    async run(command, args, { cwd }) {
      assert.equal(cwd, path.join(tempDir, 'media'));
      assert.equal(fs.statSync(tempDir).mode & 0o777, 0o700);
      assert.equal(fs.statSync(cwd).mode & 0o777, 0o700);
      const output = args[args.indexOf('--output') + 1];
      assert.equal(path.isAbsolute(output), false);
      const saved = `${output}.png`;
      fs.writeFileSync(path.join(cwd, saved), png);
      return { code: 0, stdout: JSON.stringify({ ok: true, data: { saved_path: saved } }), stderr: '' };
    },
  }, logger);
  const { media: [item] } = await lark.getMedia({ message_id: 'om_image12345678', key: 'img_abcdefgh1234' });
  assert.deepEqual(fs.readFileSync(item.path), png);
  assert.equal(item.mime_type, 'image/png');
  assert.equal(pruneLarkMedia(tempDir, config.retentionMs), 0);
  const old = new Date(Date.now() - config.retentionMs - 60_000);
  fs.utimesSync(item.path, old, old);
  // Exercise the daemon's scheduled cleanup, not just the helper.
  const removed = {};
  PersonalAgentDaemon.prototype.runMaintenance.call({
    config, store: { prune: () => removed }, logger, recordSystemActivity() {},
  });
  assert.equal(removed.larkMedia, 1);
  assert.equal(fs.existsSync(item.path), false);
});

test('rejects shared or symlinked download directories without changing their permissions', (t) => {
  const root = temporaryRoot(t);
  const shared = path.join(root, 'shared');
  fs.mkdirSync(shared);
  fs.chmodSync(shared, 0o755);
  assert.throws(() => prepareLarkMediaDirectory(shared), /mode 0700/);
  assert.equal(fs.statSync(shared).mode & 0o777, 0o755);
  const link = path.join(root, 'link');
  fs.symlinkSync(shared, link);
  assert.throws(() => prepareLarkMediaDirectory(link), /owned by the daemon user/);
  fs.symlinkSync(shared, path.join(root, 'media'));
  assert.throws(() => prepareLarkMediaDirectory(root), /owned by the daemon user/);
});

test('prunes only expired Lark files, leaving current downloads, other files and symlinks alone', (t) => {
  const root = temporaryRoot(t);
  const directory = prepareLarkMediaDirectory(root);
  const name = `lark-${'a'.repeat(24)}`;
  const now = Date.now();
  const old = new Date(now - 120_000);
  const write = (filename, expired = true) => {
    const file = path.join(directory, filename);
    fs.writeFileSync(file, 'image');
    if (expired) fs.utimesSync(file, old, old);
    return file;
  };
  const expired = write(`${name}.png`);
  const current = write(name, false);
  const unrelated = write('keep.txt');
  const linked = path.join(directory, `${name}.jpg`);
  fs.symlinkSync(unrelated, linked);
  assert.equal(pruneLarkMedia(root, 60_000, now), 1);
  assert.equal(fs.existsSync(expired), false);
  for (const file of [current, unrelated, linked]) assert.equal(fs.existsSync(file), true);
  assert.equal(pruneLarkMedia(path.join(root, 'missing'), 60_000, now), 0);
});
