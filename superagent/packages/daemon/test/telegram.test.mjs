// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import test from 'node:test';
import { TelegramApi, TelegramError, DOWNLOAD_BYTES, DOCUMENT_BYTES } from '../src/telegram/api.mjs';
import { TelegramState } from '../src/telegram/state.mjs';
import { loginTelegram } from '../src/telegram/auth.mjs';
import { normalizeUpdate, localMessageId } from '../src/telegram/events.mjs';
import { TelegramClient } from '../src/telegram/client.mjs';
import { downloadFile, filePayload } from '../src/telegram/media.mjs';
import { getConnectorManifest } from '../src/connectors/catalog.mjs';
import { ConnectorProcess } from '../src/connectors/process.mjs';
import { ProcessRegistry } from '../src/process-utils.mjs';
import { Store } from '../src/store.mjs';
import { Harness } from '../src/harness.mjs';
import { ActionService } from '../src/actions/service.mjs';
import { invokeOperation } from '../src/connectors/operations.mjs';
import { chatEvent, mailEvent } from './event-fixture.mjs';

const TOKEN = '123456:abcdefghijklmnopqrstuvwxyz_TEST';
const account = { botId: '123456', ownerId: '9007199254740000', chatId: '9007199254740000', token: TOKEN, offset: 0, activatedAt: 0 };
const logger = { info() {}, warn() {}, error() {}, debug() {}, child() { return this; } };
const response = (result) => Response.json({ ok: true, result });
const incoming = (id = 1, text = 'hello', extra = {}) => ({ update_id: id, message: {
  message_id: id, date: Math.floor(Date.now() / 1000), chat: { id: Number(account.chatId), type: 'private' },
  from: { id: Number(account.ownerId), is_bot: false }, text, ...extra,
} });
const pending = (signal) => new Promise((_, reject) => {
  if (signal.aborted) reject(signal.reason);
  else signal.addEventListener('abort', () => reject(signal.reason), { once: true });
});
function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-telegram-'));
  const state = new TelegramState(dir);
  t.after(() => { state.close(); fs.rmSync(dir, { recursive: true, force: true }); });
  return { dir, state };
}
function provider(fn, { webhook = '', botId = Number(account.botId) } = {}) {
  return async (url, init) => {
    assert.equal(init.redirect, 'error');
    assert.ok(url.startsWith(`https://api.telegram.org/bot${TOKEN}/`));
    const method = url.split('/').at(-1);
    if (method === 'getMe') return response({ id: botId, is_bot: true, username: 'vika_test_bot' });
    if (method === 'getWebhookInfo') return response({ url: webhook });
    return fn(method, init.body instanceof FormData ? init.body : JSON.parse(init.body), init);
  };
}
async function client(t, fn, config = {}) {
  const { dir, state } = fixture(t);
  state.saveAccount(account);
  const c = new TelegramClient({ dataDir: dir, retentionMs: 86400_000, ...config }, null, logger, { fetchImpl: provider(fn) });
  t.after(() => c.stop());
  await c.preflight();
  return c;
}

test('API contains tokens and provider descriptions even on transport, JSON and stream failures', async () => {
  for (const fetchImpl of [async () => { throw new Error(`leaked ${TOKEN}`); }, async () => new Response(TOKEN),
    async () => Response.json({ ok: false, error_code: 401, description: TOKEN }, { status: 401 }),
    async () => new Response(new ReadableStream({ start(c) { c.error(new Error(TOKEN)); } }))]) {
    const api = new TelegramApi(TOKEN, { fetchImpl });
    await assert.rejects(api.request('getMe'), (error) => !String(error.stack).includes(TOKEN) && !error.cause);
  }
  assert.throws(() => new TelegramApi('not/a/token'), /Invalid/);
  const api = new TelegramApi(TOKEN, { fetchImpl: async () => { throw new Error('must not run'); } });
  await assert.rejects(api.request('../getMe'), /Unsupported/);
  assert.equal(getConnectorManifest('telegram').actions['message.send'].idempotent, undefined);
});

test('pairs only a fresh private start challenge, stores numeric identities precisely and leaves later tasks pending', async (t) => {
  const { state } = fixture(t);
  let nonce;
  const offsets = [];
  const result = await loginTelegram({ state, token: TOKEN, signal: AbortSignal.timeout(2000),
    onChallenge: ({ url }) => { nonce = new URL(url).searchParams.get('start'); assert.ok(nonce.length >= 32); },
    fetchImpl: provider(async (method, body) => {
      assert.equal(method, 'getUpdates'); offsets.push(body.offset);
      if (offsets.length === 1) return response([
        incoming(1, `/start ${nonce}`, { chat: { id: -1, type: 'group' } }),
        incoming(2, '/start wrong'), incoming(3, `/start ${nonce}`, { date: 1 }),
      ]);
      return response([incoming(4, `/start ${nonce}`), incoming(5, 'task after pairing')]);
    }),
  });
  assert.deepEqual(offsets, [0, 4]);
  assert.equal(result.ownerId, account.ownerId);
  assert.equal(state.account().offset, 5);
  assert.equal(state.account().token, TOKEN);
  assert.equal(fs.statSync(state.databasePath).mode & 0o777, 0o600);
  assert.equal(fs.statSync(state.directory).mode & 0o777, 0o700);
  state.acquire(); state.release();
});

test('reauthorization stages owner tasks before acknowledging them and cancellation preserves old credentials', async (t) => {
  const { state } = fixture(t);
  state.saveAccount({ ...account, token: 'old-key' });
  const controller = new AbortController();
  let calls = 0, nonce;
  await assert.rejects(loginTelegram({ state, token: TOKEN, signal: controller.signal,
    onChallenge: ({ url }) => { nonce = new URL(url).searchParams.get('start'); },
    fetchImpl: provider(async (_method, body) => {
      if (calls++ === 0) return response([incoming(7, 'owner task'), incoming(8, 'stranger', { from: { id: 99, is_bot: false } })]);
      assert.equal(body.offset, 9);
      assert.equal(state.pending(account.botId).length, 1);
      controller.abort(new Error('cancelled'));
      return response([incoming(9, `/start ${nonce}`)]);
    }),
  }), /cancelled/);
  assert.equal(state.account().token, 'old-key');
  assert.equal(state.account().offset, 0);
  assert.equal(state.pending(account.botId)[0].event.envelope.content.text, 'owner task');
  state.acquire(); state.release();
});

test('successful reauthorization preserves queued tasks and switching owner clears private routes', async (t) => {
  const { state } = fixture(t);
  state.saveAccount(account);
  state.recordRoute('old', 1, account.chatId);
  let nonce, round = 0;
  const fetchImpl = provider(async () => response(round++ === 0 ? [incoming(5, 'queued')]
    : [incoming(6, `/start ${nonce}`), incoming(7, 'after')]));
  await loginTelegram({ state, token: TOKEN, fetchImpl, onChallenge: ({ url }) => { nonce = new URL(url).searchParams.get('start'); } });
  assert.equal(state.account().activatedAt, 0);
  assert.deepEqual(state.pending(account.botId).map((row) => row.updateId), [5, 7]);
  assert.equal(state.account().offset, 7);
  state.saveAccount({ ...account, ownerId: '99', chatId: '99' });
  assert.equal(state.route('old'), null);
  assert.deepEqual(state.pending(account.botId), []);
});

test('rejects existing webhook without changing it, and refuses concurrent local receivers', async (t) => {
  const { state, dir } = fixture(t);
  state.saveAccount(account);
  await assert.rejects(loginTelegram({ state, token: TOKEN, fetchImpl: provider(() => assert.fail('must not poll'), { webhook: 'https://existing.example' }) }), /active webhook/);
  assert.equal(state.account().token, TOKEN);
  state.acquire();
  const other = new TelegramState(dir);
  try { assert.throws(() => other.acquire(), /already in use/); other.release(); assert.throws(() => other.acquire(), /already in use/); }
  finally { other.close(); state.release(); }
  state.db.prepare('INSERT INTO receiver VALUES (1, ?, ?)').run(2147483647, 'stale');
  state.acquire(); state.release();
});

test('normalizes stable events, quotes and attachments; excludes strangers, groups and handshake secrets', () => {
  const original = normalizeUpdate(incoming(7), account);
  assert.equal(original.envelope.id, '123456:7');
  assert.equal(original.id, `tg:123456:${account.chatId}:7`);
  assert.notEqual(localMessageId('123456', '1', 7), localMessageId('123456', '2', 7));
  const photo = normalizeUpdate(incoming(8, undefined, { caption: '/new', photo: [{ file_id: 'small', file_size: 3 }, { file_id: 'large', file_size: 4 }], reply_to_message: incoming(7).message }), account);
  assert.equal(photo.envelope.message.type, 'image');
  assert.equal(photo.envelope.message.parent_id, original.id);
  assert.equal(photo.media.id, 'large');
  for (const update of [incoming(9, '/start secret'), incoming(10, 'x', { from: { id: 1, is_bot: false } }),
    incoming(11, 'x', { chat: { id: 1, type: 'group' } }), incoming(12, undefined, { text: undefined, voice: { file_id: 'voice' } })]) {
    assert.equal(normalizeUpdate(update, account), null);
  }
  assert.equal(normalizeUpdate(incoming(13), { ...account, activatedAt: Date.now() + 60_000 }), null);
});

test('resumes pairing backlog, checkpoints all update kinds, and pause keeps sends available', async (t) => {
  const seen = [], offsets = [];
  let awaiting;
  const polled = new Promise((resolve) => { awaiting = resolve; });
  const c = await client(t, async (method, body, init) => {
    if (method !== 'getUpdates') return response({ message_id: 20, chat: { id: Number(account.chatId) } });
    offsets.push(body.offset);
    if (offsets.length === 1) return response([incoming(1), { update_id: 2, my_chat_member: {} }]);
    awaiting(); return pending(init.signal);
  });
  c.state.stage(account.botId, 0, normalizeUpdate(incoming(0, 'staged', { message_id: 10 }), account));
  c.startSubscription((line) => seen.push(JSON.parse(line)), () => assert.fail('unexpected exit'));
  await polled;
  await c.stopSubscription();
  assert.deepEqual(offsets, [0, 3]);
  assert.equal(c.state.account().offset, 3);
  assert.deepEqual(c.state.pending(account.botId), []);
  assert.deepEqual(seen.map((event) => event.content.text), ['staged', 'hello']);
  await c.replyMessage({ message_id: seen[1].message.id, content: { type: 'markdown', text: 'draining reply' } });
  const resumed = c.startSubscription(() => {}, () => assert.fail('unexpected exit'));
  await delay(5); await c.stopSubscription(); await resumed;
  assert.equal(offsets.at(-1), 3);
});

test('fatal polling conflicts isolate this connector', async (t) => {
  const c = await client(t, async () => Response.json({ ok: false, error_code: 409 }, { status: 409 }));
  let exited;
  await c.startSubscription(() => assert.fail(), (exit) => { exited = exit; });
  assert.match(exited.stderr, /polling conflict/);
});

test('a failed cursor write never confirms the batch on the next poll', async (t) => {
  const offsets = [], seen = [];
  let resolve;
  const retried = new Promise((done) => { resolve = done; });
  const c = await client(t, async (_method, body, init) => {
    offsets.push(body.offset);
    if (offsets.length <= 2) return response([incoming(1)]);
    resolve(); return pending(init.signal);
  });
  const save = c.state.saveCursor.bind(c.state);
  let writes = 0;
  c.state.saveCursor = (offset) => { if (writes++ === 0) throw new Error('disk error'); save(offset); };
  c.startSubscription((line) => seen.push(JSON.parse(line).id), () => assert.fail('unexpected exit'));
  await retried;
  await c.stopSubscription();
  assert.deepEqual(offsets, [0, 0, 2]);
  assert.deepEqual(seen, ['123456:1', '123456:1']);
  assert.equal(c.state.account().offset, 2);
});

test('text sends stay owner-only, use quote routes, preserve literal Markdown, and never retry uncertain results', async (t) => {
  const calls = [];
  const c = await client(t, async (method, body) => {
    calls.push([method, body]);
    return Response.json({ ok: false, error_code: 500, description: TOKEN }, { status: 500 });
  });
  await assert.rejects(c.sendMessage({ chat_id: '99', content: { type: 'markdown', text: 'x' } }), /verified owner/);
  await assert.rejects(c.sendMessage({ chat_id: account.chatId, content: { type: 'markdown', text: 'x'.repeat(4097) } }), /4096/);
  assert.equal(calls.length, 0);
  c.state.recordRoute('quote', 42, account.chatId);
  await assert.rejects(c.replyMessage({ message_id: 'quote', content: { type: 'markdown', text: '**bold**_literal' } }), TelegramError);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][1].parse_mode, undefined);
  assert.equal(calls[0][1].reply_parameters.message_id, 42);
});

test('queued sends expire before dispatch and 429 waits are cancellable', async (t) => {
  let calls = 0;
  const c = await client(t, async (_method, _body, init) => { calls += 1; return pending(init.signal); }, { sendTimeoutMs: 560 });
  const timer = setTimeout(() => {}, 1000);
  t.after(() => clearTimeout(timer));
  const input = { chat_id: account.chatId, content: { type: 'markdown', text: 'hello' } };
  const results = await Promise.allSettled([c.sendMessage(input), c.sendMessage(input)]);
  assert.deepEqual(results.map((r) => r.status), ['rejected', 'rejected']);
  assert.equal(calls, 1);
  const api = new TelegramApi(TOKEN, { fetchImpl: async () => { calls += 1; return Response.json({ ok: false, error_code: 429, parameters: { retry_after: 5 } }); } });
  await assert.rejects(api.send('sendMessage', {}, AbortSignal.timeout(30)));
  assert.equal(calls, 2);
});

test('explicit 429 sends retry after waiting and stop after two retries', async () => {
  let calls = 0, limited = false;
  const times = [];
  const api = new TelegramApi(TOKEN, { fetchImpl: async () => {
    times.push(Date.now());
    return calls++ === 0 || limited
      ? Response.json({ ok: false, error_code: 429, parameters: { retry_after: 1 } }, { status: 429 })
      : response({ message_id: 5 });
  } });
  assert.equal((await api.send('sendMessage', {}, AbortSignal.timeout(5000))).message_id, 5);
  assert.equal(calls, 2);
  assert.ok(times[1] - times[0] >= 950);
  limited = true;
  await assert.rejects(api.send('sendMessage', {}, AbortSignal.timeout(5000)), TelegramError);
  assert.equal(calls, 5);
});

test('uploads files through multipart and checks size/symlinks before network; images above photo limit become documents', async (t) => {
  const { dir } = fixture(t);
  const name = path.join(dir, 'picture.png');
  fs.writeFileSync(name, 'png');
  const image = filePayload({ path: name }, account.chatId, 3);
  assert.equal(image.method, 'sendPhoto');
  assert.equal(image.body.get('chat_id'), account.chatId);
  assert.equal(JSON.parse(image.body.get('reply_parameters')).message_id, 3);
  fs.truncateSync(name, 10_000_001);
  assert.equal(filePayload({ path: name }, account.chatId).method, 'sendDocument');
  fs.truncateSync(name, DOCUMENT_BYTES + 1);
  assert.throws(() => filePayload({ path: name }, account.chatId), /50 MB/);
  const link = path.join(dir, 'link'); fs.symlinkSync(name, link);
  assert.throws(() => filePayload({ path: link }, account.chatId));
});

test('downloads bounded media from the fixed host without redirects, sanitizes names, and rejects unsafe paths', async (t) => {
  const { state } = fixture(t);
  const bytes = Buffer.from('attachment');
  let filePath = 'documents/file_1.txt', downloads = 0;
  const api = new TelegramApi(TOKEN, { fetchImpl: async (url, init) => {
    assert.equal(init.redirect, 'error');
    if (url.endsWith('/getFile')) return response({ file_id: 'file_1', file_path: filePath, file_size: bytes.length });
    assert.ok(url.startsWith(`https://api.telegram.org/file/bot${TOKEN}/`));
    downloads += 1; return new Response(bytes);
  } });
  const media = { id: 'file_1', size: bytes.length, kind: 'file', name: '../../private.txt' };
  const got = await downloadFile(api, media, state.mediaDirectory, AbortSignal.timeout(1000));
  assert.deepEqual(fs.readFileSync(got.path), bytes);
  assert.equal(got.name, 'private.txt');
  assert.equal(fs.statSync(got.path).mode & 0o777, 0o600);
  for (filePath of ['https://evil.example/file', '../file', '/file', 'a/%2e%2e/file', 'file?token=evil']) {
    await assert.rejects(downloadFile(api, media, state.mediaDirectory, AbortSignal.timeout(1000)), /Invalid Telegram file path/);
  }
  await assert.rejects(downloadFile(api, { ...media, size: DOWNLOAD_BYTES + 1 }, state.mediaDirectory, AbortSignal.timeout(1000)), /20 MB/);
  assert.equal(downloads, 1);
});

test('media streams without a declared size are bounded and transport errors never expose tokens', async () => {
  let cancelled = false;
  const api = new TelegramApi(TOKEN, { fetchImpl: async () => new Response(new ReadableStream({
    pull(controller) { controller.enqueue(new Uint8Array(5_000_000)); },
    cancel() { cancelled = true; },
  })) });
  await assert.rejects(api.download('documents/file', AbortSignal.timeout(1000)), /size limit/);
  assert.equal(cancelled, true);
  api.fetchImpl = async () => { throw new Error(`https://api.telegram.org/file/bot${TOKEN}/documents/file`); };
  await assert.rejects(api.download('documents/file', AbortSignal.timeout(1000)), (error) => !error.stack.includes(TOKEN) && !error.cause);
});

test('real connector worker supports check/start/pause/reply/stop and releases its lease', async (t) => {
  const { dir, state } = fixture(t);
  state.saveAccount(account);
  const workerFile = path.join(dir, 'worker.mjs');
  fs.writeFileSync(workerFile, `
    let polled = false;
    globalThis.fetch = async (url, init) => {
      const method = url.split('/').at(-1);
      const result = method === 'getMe' ? { id: 123456, is_bot: true, username: 'vika_test_bot' }
        : method === 'getWebhookInfo' ? { url: '' } : method === 'getUpdates' ? null
        : { message_id: 50, chat: { id: ${account.chatId} } };
      if (method !== 'getUpdates') return Response.json({ ok: true, result });
      if (!polled) { polled = true; return Response.json({ ok: true, result: ${JSON.stringify([incoming(1)])} }); }
      return new Promise((_, reject) => init.signal.addEventListener('abort', () => reject(init.signal.reason), { once: true }));
    };
    await import(${JSON.stringify(new URL('../src/connectors/worker.mjs', import.meta.url).href)});
  `);
  const registry = new ProcessRegistry(logger);
  const worker = new ConnectorProcess('telegram', { dataDir: dir, projectDir: process.cwd(), connectorWorkerPath: workerFile,
    retentionMs: 86400_000, maxEventBytes: 100_000, sendTimeoutMs: 5000 }, registry, logger);
  t.after(async () => { await worker.stop(); await registry.stopAll(1000); });
  assert.equal((await worker.preflight()).manifest.name, 'telegram');
  assert.equal(worker.owner.id, account.ownerId);
  let receive;
  const event = new Promise((resolve) => { receive = resolve; });
  await worker.startSubscription(receive, () => assert.fail('unexpected worker exit'));
  const message = await event;
  await worker.request('pause', {});
  const sent = await worker.invoke('message.reply', { message_id: message.message.id, content: { type: 'markdown', text: 'reply' } });
  assert.equal(sent.message_id, localMessageId(account.botId, account.chatId, 50));
  await worker.stop();
  state.acquire(); state.release();
});

test('Harness and outbox deduplicate Telegram events/replies and route notices and approved mail to the owner', async (t) => {
  const sent = [];
  const c = await client(t, async (_method, body) => {
    sent.push(body); return response({ message_id: 100 + sent.length, chat: { id: Number(account.chatId) } });
  });
  const store = new Store(':memory:', logger);
  store.initializeCognition('codex', 'runtime-token');
  const mails = [];
  const connectors = {
    manifests: () => ['telegram', 'email'].map((id) => [id, getConnectorManifest(id)]),
    owner: (id) => id === 'telegram' ? { id: account.ownerId, target: { chat_id: account.chatId } } : null,
    invoke: async (id, action, input) => {
      if (id === 'email') { mails.push(input); return { sent: true }; }
      return invokeOperation(c, action, input);
    },
  };
  const config = { channel: 'telegram', maxReplyChars: 4096, maxEventBytes: 100_000,
    maxEventAgeMs: 60_000, ignoredSenderIds: new Set(), workspace: c.config.dataDir, tempDir: c.config.dataDir };
  const actions = new ActionService({ config, store, connectors, logger });
  const delivered = [], commands = [];
  const harness = new Harness({ config, store, logger, recordActivity() {},
    runtime: { snapshot: () => ({ session: { connected: true }, work: { turnInProgress: false } }),
      submit: (items) => { delivered.push(...items); return { accepted: true }; } },
    onSlashCommand: (command) => commands.push(command),
  });
  t.after(() => { harness.stop(); store.close(); });
  const event = normalizeUpdate(incoming(1), account);
  c.state.recordRoute(event.id, 1, account.chatId);
  harness.handleConnectorEvent(event.envelope);
  harness.handleConnectorEvent(event.envelope);
  harness.handleConnectorEvent(normalizeUpdate(incoming(2, '/help'), account).envelope);
  harness.handleConnectorEvent(normalizeUpdate(incoming(3, undefined, { text: undefined, caption: '/new', photo: [{ file_id: 'img', file_size: 3 }] }), account).envelope);
  await harness.ingressBarrier;
  assert.equal(delivered.length, 2);
  assert.equal(commands.length, 1, 'media captions cannot execute slash commands');
  const call = (name, input) => actions.call(name, input, { authority: 'runtime-token' });
  const reply = { message_id: event.id, markdown: '回答', decision_id: '123456:3' };
  await call('reply_message', reply); await call('reply_message', reply);
  assert.equal(sent.length, 1);
  assert.equal(store.db.prepare('SELECT status FROM outbox').get().status, 'SENT');
  store.ingest(mailEvent({ uid: 7 }));
  await call('notify_owner', { markdown: '新邮件', decision_id: 'imap:1:7' });
  assert.equal(sent.at(-1).chat_id, account.chatId);
  await call('reply_message', { message_id: 'mail:1:7', markdown: '周四可以', decision_id: 'imap:1:7' });
  assert.match(sent.at(-1).text, /发送 1/);
  assert.equal(mails.length, 0);
  const approve = chatEvent({ source: 'telegram', chatId: account.chatId, senderId: account.ownerId,
    messageId: localMessageId(account.botId, account.chatId, 50), chatType: 'p2p', content: '发送 1' });
  store.ingest(approve);
  c.state.recordRoute(approve.messageId, 50, account.chatId);
  await actions.answerApproval(approve, actions.matchApprovalReply('发送 1', approve));
  assert.equal(mails.length, 1);
  assert.equal(mails[0].content.text, '周四可以');
});
