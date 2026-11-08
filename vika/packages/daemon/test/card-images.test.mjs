// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { ActionService } from '../src/actions/service.mjs';
import { resolveContent } from '../src/actions/content.mjs';
import { MAX_CARD_BYTES, MAX_CARD_IMAGE_BYTES, replaceCardImages } from '../src/card-images.mjs';
import { LarkClient } from '../src/lark.mjs';
import { LarkImageCache } from '../src/lark-image-cache.mjs';
import { Store } from '../src/store.mjs';
import { invokeOperation } from '../src/connectors/operations.mjs';
import { fakeConnectors } from './action-fixture.mjs';
import { chatEvent } from './event-fixture.mjs';

const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aol8AAAAASUVORK5CYII=', 'base64');
const GIF = Buffer.from('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7', 'base64');
const TOKEN = 'card-test-authority';
const CHAT = 'oc_cardtest12345';
const MESSAGE = 'om_cardtest12345';
const PROFILE = 'explicit-daemon-profile';
const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };
const image = (alias) => ({ tag: 'img', img_key: `{{image:${alias}}}`, alt: { tag: 'plain_text', content: alias } });
const card = (...aliases) => ({ elements: aliases.map(image) });
const ok = (data) => ({ code: 0, stdout: JSON.stringify({ ok: true, data }), stderr: '' });

function fixture(t, options = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-card-'));
  const config = { dataDir: path.join(root, 'data'), workspace: path.join(root, 'workspace'),
    tempDir: path.join(root, 'temp'), profile: PROFILE, channel: 'lark', instance: 'lark',
    maxReplyChars: 1000, sendTimeoutMs: 1000, larkCliBin: '/fake/lark-cli' };
  fs.mkdirSync(config.dataDir, { mode: 0o700 });
  fs.mkdirSync(config.workspace, { mode: 0o700 });
  fs.writeFileSync(path.join(config.workspace, 'a.png'), PNG);
  fs.writeFileSync(path.join(config.workspace, 'copy.png'), PNG);
  fs.writeFileSync(path.join(config.workspace, 'b.gif'), GIF);
  const f = { root, config, options, calls: [], uploads: [], cards: [], uploadAttempts: 0, sendAttempts: 0 };
  const open = () => {
    f.store = new Store(path.join(config.dataDir, 'vika.sqlite3'), logger);
    f.store.initializeCognition('codex', TOKEN);
    f.lark = new LarkClient(config, { async run(_bin, args, cliOptions) {
      f.calls.push({ args, options: cliOptions });
      assert.deepEqual(args.slice(0, 3), ['--profile', PROFILE, 'im']);
      if (args[3] === 'images') {
        assert.equal(f.store.db.prepare("SELECT count(*) AS n FROM outbox WHERE status = 'PENDING'").get().n > 0, true);
        f.uploadAttempts++;
        assert.deepEqual(args.slice(3, 10), ['images', 'create', '--as', 'bot', '--data', '{"image_type":"message"}', '--file']);
        const file = args[10];
        assert.match(file, /^image=\.\/[a-f0-9]{64}\.(png|gif)$/);
        const filename = path.join(cliOptions.cwd, file.slice('image=./'.length));
        assert.equal(fs.statSync(filename).mode & 0o777, 0o600);
        assert.equal(fs.statSync(cliOptions.cwd).mode & 0o777, 0o700);
        options.onUpload?.();
        if (options.uploadGate) await options.uploadGate;
        if (options.failUpload === f.uploadAttempts) return { code: 1, stdout: '', stderr: 'upload failed' };
        const key = options.invalidKey ?? `img_v3_uploaded_${f.uploadAttempts}`;
        f.uploads.push(key);
        if (options.failPersist) f.lark.imageCache.put = () => { throw new Error('disk full'); };
        return ok({ image_key: key });
      }
      if (args[3] === '+messages-mget') return ok({ message_id: 'om_delivered12345', chat_id: CHAT, thread_id: 'omt_cardtest1234' });
      assert.ok(['+messages-send', '+messages-reply'].includes(args[3]));
      const sent = JSON.parse(args[args.indexOf('--content') + 1]);
      assert.doesNotMatch(JSON.stringify(sent), /\{\{image:/);
      // Every uploaded success is durable before the interactive send.
      for (const key of f.uploads) {
        assert.ok(f.lark.imageCache.db.prepare('SELECT image_key FROM image_uploads WHERE image_key = ?').get(key));
      }
      f.cards.push(sent);
      f.sendAttempts++;
      if (options.failSend === f.sendAttempts) return { code: 1, stdout: '', stderr: 'ambiguous send failure' };
      return ok({ message_id: 'om_delivered12345', chat_id: CHAT });
    } }, logger);
    f.connectors = fakeConnectors({ owners: { lark: { id: 'ou_owner12345678', target: { user_id: 'ou_owner12345678' } } },
      run: (_connector, action, input) => invokeOperation(f.lark, action, input) });
    f.service = new ActionService({ config, store: f.store, connectors: f.connectors, logger });
  };
  open();
  f.store.ingest(chatEvent({ messageId: MESSAGE, chatId: CHAT, chatType: 'group' }));
  f.send = (content = { card: card('cover'), card_images: { cover: 'a.png' } }, tool = 'send_message', authority = TOKEN) =>
    f.service.call(tool, { ...(tool === 'send_message' ? { chat_id: CHAT } : tool === 'reply_message' ? { message_id: MESSAGE } : {}),
      decision_id: `message:${MESSAGE}`, ...content }, { authority });
  f.restart = async () => { await f.lark.stop(); f.store.close(); open(); };
  t.after(async () => { await f.lark.stop(); f.store.close(); fs.rmSync(root, { recursive: true, force: true }); });
  return f;
}

for (const tool of ['send_message', 'reply_message', 'notify_owner']) {
  test(`${tool} uploads a local card image as bot, sends real keys, and registers callbacks`, async (t) => {
    const f = fixture(t);
    const content = { card: tool === 'reply_message' ? { schema: '2.0', body: card('cover') } : card('cover'),
      card_images: { cover: 'a.png' }, ...(tool === 'reply_message' ? { reply_in_thread: true } : {}) };
    await f.send(content, tool);
    assert.equal(f.uploadAttempts, 1);
    assert.equal(f.sendAttempts, 1);
    assert.match(JSON.stringify(f.cards[0]), /img_v3_uploaded_1/);
    const record = f.store.db.prepare('SELECT * FROM lark_cards').get();
    assert.equal(record.profile, PROFILE);
    if (tool === 'reply_message') assert.equal(JSON.parse(record.scope_json).conversation.thread_id, 'omt_cardtest1234');
    assert.deepEqual(fs.readdirSync(path.join(f.config.tempDir, 'outbox')), []);
    assert.ok(f.service.tools().every((item) => !/upload/.test(item.name)));
  });
}

test('multiple aliases and repeated img_key nodes deduplicate by content, not by filename', async (t) => {
  const f = fixture(t);
  await f.send({ card: card('a', 'b', 'same', 'a'), card_images: { same: 'copy.png', b: 'b.gif', a: 'a.png' } });
  assert.equal(f.uploadAttempts, 2);
  const keys = f.cards[0].elements.map((item) => item.img_key);
  assert.equal(keys[0], keys[2]);
  assert.equal(keys[0], keys[3]);
  assert.notEqual(keys[0], keys[1]);
});

test('concurrent distinct outbox actions share an in-flight image upload', async (t) => {
  const started = Promise.withResolvers();
  const gate = Promise.withResolvers();
  const f = fixture(t, { onUpload: started.resolve, uploadGate: gate.promise });
  const content = { card: card('a'), card_images: { a: 'a.png' } };
  const first = f.send({ ...content, idempotency_key: 'first' });
  const second = f.send({ ...content, idempotency_key: 'second' });
  try {
    await started.promise;
    assert.equal(f.uploadAttempts, 1);
  } finally { gate.resolve(); }
  await Promise.all([first, second]);
  assert.equal(f.uploadAttempts, 1);
  assert.equal(f.sendAttempts, 2);
});

test('identical concurrent outbox attempts reserve once before uploading', async (t) => {
  const started = Promise.withResolvers();
  const gate = Promise.withResolvers();
  const f = fixture(t, { onUpload: started.resolve, uploadGate: gate.promise });
  const first = f.send();
  try {
    await started.promise;
    await assert.rejects(f.send(), /still in progress/);
  } finally { gate.resolve(); }
  await first;
  assert.equal(f.uploadAttempts, 1);
  assert.equal(f.sendAttempts, 1);
});

const invalidTemplates = [
  ['missing alias', { card: card('missing'), card_images: { cover: 'a.png' } }],
  ['no resource mapping', { card: card('missing') }],
  ['unused alias', { card: card('a'), card_images: { a: 'a.png', unused: 'b.gif' } }],
  ['invalid alias', { card: card('a.b'), card_images: { 'a.b': 'a.png' } }],
  ['alias ending in newline', { card: card('a'), card_images: { 'a\n': 'a.png' } }],
  ['empty map', { card: card(), card_images: {} }],
  ['array mapping', { card: card('a'), card_images: [['a', 'a.png'], ['a', 'b.gif']] }],
  ['embedded placeholder', { card: { elements: [{ tag: 'img', img_key: 'prefix{{image:a}}' }] }, card_images: { a: 'a.png' } }],
  ['placeholder ending in newline', { card: { elements: [{ tag: 'img', img_key: '{{image:a}}\n' }] }, card_images: { a: 'a.png' } }],
  ['text placeholder', { card: { elements: [{ tag: 'markdown', content: '{{image:a}}' }] }, card_images: { a: 'a.png' } }],
  ['malformed structure', { card: { schema: '2.0', elements: [image('a')] }, card_images: { a: 'a.png' } }],
  ['oversize template', { card: { ...card('a'), extra: 'x'.repeat(MAX_CARD_BYTES) }, card_images: { a: 'a.png' } }],
  ['oversize after replacement', { card: { ...card('a'), extra: 'x'.repeat(MAX_CARD_BYTES - 200) }, card_images: { a: 'a.png' } }],
];
for (const [name, content] of invalidTemplates) test(`rejects ${name} before uploading or reserving`, async (t) => {
  const f = fixture(t);
  await assert.rejects(f.send(content));
  assert.equal(f.calls.length, 0);
  assert.equal(f.store.db.prepare('SELECT count(*) AS n FROM outbox').get().n, 0);
});

for (const [name, pathname, prepare] of [
  ['URL', 'https://example.com/a.png'], ['absolute path', '/tmp/a.png'], ['traversal', '../a.png'],
  ['symlink escape', 'link.png', (f) => { fs.writeFileSync(path.join(f.root, 'outside.png'), PNG); fs.symlinkSync(path.join(f.root, 'outside.png'), path.join(f.config.workspace, 'link.png')); }],
  ['directory', 'folder', (f) => fs.mkdirSync(path.join(f.config.workspace, 'folder'))],
  ['empty image', 'bad.png', (f) => fs.writeFileSync(path.join(f.config.workspace, 'bad.png'), '')],
  ['renamed non-image', 'bad.png', (f) => fs.writeFileSync(path.join(f.config.workspace, 'bad.png'), '%PDF-1.7 not an image')],
  ['unsupported SVG', 'bad.svg', (f) => fs.writeFileSync(path.join(f.config.workspace, 'bad.svg'), '<svg width="1" height="1"></svg>')],
  ['oversize image', 'bad.png', (f) => { const file = path.join(f.config.workspace, 'bad.png'); fs.writeFileSync(file, PNG); fs.truncateSync(file, MAX_CARD_IMAGE_BYTES + 1); }],
  ['too large dimensions', 'bad.png', (f) => { const data = Buffer.from(PNG); data.writeUInt32BE(12001, 16); fs.writeFileSync(path.join(f.config.workspace, 'bad.png'), data); }],
]) test(`rejects ${name}, including when another image is valid`, async (t) => {
  const f = fixture(t);
  prepare?.(f);
  await assert.rejects(f.send({ card: card('a', 'z'), card_images: { a: 'a.png', z: pathname } }));
  assert.equal(f.calls.length, 0);
  assert.deepEqual(fs.readdirSync(path.join(f.config.tempDir, 'outbox')), []);
});

for (const body of [{ markdown: 'hello' }, { file_path: 'a.png' }, { file_key: 'file_12345678' },
  { choices: { prompt: 'choose', options: [{ key: 'a', label: 'A' }, { key: 'b', label: 'B' }] } }]) {
  test(`card_images rejects ${Object.keys(body)[0]} as its body`, async (t) => {
    const f = fixture(t);
    await assert.rejects(f.send({ ...body, card_images: { a: 'a.png' } }), /only accompany card/);
    assert.equal(f.calls.length, 0);
  });
}

test('non-Lark connectors reject card_images without invoking a connector', async (t) => {
  for (const id of ['weixin', 'telegram', 'slack']) {
    const f = fixture(t);
    f.service.connectors = fakeConnectors({ ids: [id] });
    f.store.ingest(chatEvent({ source: id, messageId: 'other_12345678', chatId: 'other_chat', chatType: 'p2p' }));
    await assert.rejects(f.service.call('reply_message', { source: id, message_id: 'other_12345678',
      decision_id: 'message:other_12345678', card: card('a'), card_images: { a: 'a.png' } }, { authority: TOKEN }), /only by Lark/);
    assert.equal(f.service.connectors.calls.length, 0);
  }
});

test('second upload failure sends no card; retry after restart reuses the first durable key', async (t) => {
  const f = fixture(t, { failUpload: 2 });
  const content = { card: card('a', 'b'), card_images: { a: 'a.png', b: 'b.gif' } };
  await assert.rejects(f.send(content), /upload failed/);
  assert.equal(f.cards.length, 0);
  assert.equal(f.lark.imageCache.db.prepare('SELECT count(*) AS n FROM image_uploads').get().n, 1);
  assert.equal(f.store.db.prepare('SELECT status FROM outbox').get().status, 'UNCERTAIN');
  // Emulate an interrupted attempt that had not settled its outbox row.
  f.store.db.prepare("UPDATE outbox SET status = 'PENDING'").run();
  await f.restart();
  await f.send(content);
  assert.equal(f.uploadAttempts, 3, 'only the failed second image uploads again');
  assert.equal(f.sendAttempts, 1);
  assert.equal(f.store.db.prepare('SELECT attempt_count FROM outbox').get().attempt_count, 2);
});

test('an uncertain card send retries with identical keys and UUID after restart, then SENT deduplicates', async (t) => {
  const f = fixture(t, { failSend: 1 });
  await assert.rejects(f.send(), /ambiguous send failure/);
  await f.restart();
  await f.send();
  assert.equal(f.uploadAttempts, 1);
  assert.equal(f.sendAttempts, 2);
  assert.deepEqual(f.cards[0], f.cards[1]);
  const sends = f.calls.filter(({ args }) => args[3] === '+messages-send');
  assert.equal(sends[0].args[sends[0].args.indexOf('--idempotency-key') + 1], sends[1].args[sends[1].args.indexOf('--idempotency-key') + 1]);
  assert.equal((await f.send()).data.duplicate, true);
  assert.equal(f.sendAttempts, 2);
});

test('persistence or provider-key validation failure never sends a partial card', async (t) => {
  for (const options of [{ failPersist: true }, { invalidKey: 'bad-key' }, { invalidKey: 'img_bad_key\n' }, { invalidKey: `img_${'x'.repeat(300)}` }]) {
    const f = fixture(t, options);
    await assert.rejects(f.send());
    assert.equal(f.cards.length, 0);
    assert.equal(f.store.db.prepare('SELECT status FROM outbox').get().status, 'UNCERTAIN');
  }
});

test('authority and freshness are rechecked after content preparation, before any upload', async (t) => {
  for (const change of ['authority', 'freshness', 'dry-run']) {
    const f = fixture(t);
    if (change === 'dry-run') f.config.dryRun = true;
    const result = f.send();
    if (change === 'authority') f.store.initializeCognition('claude', 'retired');
    if (change === 'freshness') f.store.ingest(chatEvent({ messageId: 'om_newer12345678', chatId: CHAT, chatType: 'group' }));
    if (change === 'dry-run') await result;
    else await assert.rejects(result);
    assert.equal(f.calls.length, 0);
    assert.equal(f.store.db.prepare('SELECT count(*) AS n FROM outbox').get().n, 0);
  }
});

test('canonical identity includes template, alias and bytes but excludes source and snapshot paths', (t) => {
  const f = fixture(t);
  const first = resolveContent({ card: card('a', 'b'), card_images: { b: 'b.gif', a: 'a.png' } }, f.config);
  const second = resolveContent({ card: card('a', 'b'), card_images: { a: 'copy.png', b: 'b.gif' } }, f.config);
  try {
    assert.deepEqual(first.canonical, second.canonical);
    assert.notEqual(first.payload.card_images[0].path, second.payload.card_images[0].path);
    assert.ok(!JSON.stringify(first.canonical).includes(f.root));
    const changed = resolveContent({ card: card('a', 'b'), card_images: { a: 'b.gif', b: 'a.png' } }, f.config);
    try { assert.notDeepEqual(first.canonical, changed.canonical); } finally { changed.cleanup(); }
  } finally { first.cleanup(); second.cleanup(); }
});

test('cache survives reopening and isolates connector instance and profile', (t) => {
  const f = fixture(t);
  let cache = new LarkImageCache(f.config.dataDir);
  cache.put('lark', 'profile-a', 'sha', 'img_test_a');
  cache.close();
  cache = new LarkImageCache(f.config.dataDir);
  try {
    assert.equal(cache.get('lark', 'profile-a', 'sha'), 'img_test_a');
    assert.equal(cache.get('lark', 'profile-b', 'sha'), null);
    assert.equal(cache.get('other-lark', 'profile-a', 'sha'), null);
  } finally { cache.close(); }
});

test('replacement only touches exact img_key nodes and leaves caller templates intact', () => {
  const template = { ...card('a'), text: 'literal cover', value: { alias: 'a' } };
  const replaced = replaceCardImages(template, new Map([['a', 'img_real_key']]));
  assert.equal(replaced.elements[0].img_key, 'img_real_key');
  assert.equal(template.elements[0].img_key, '{{image:a}}');
  assert.equal(replaced.value.alias, 'a');
});
