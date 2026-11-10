// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import test from 'node:test';
import { WebSocketServer } from 'ws';
import { QQApi, QQError, MEDIA_BYTES, REPLY_WINDOW_MS } from '../src/qq/api.mjs';
import { QQState } from '../src/qq/state.mjs';
import { loginQQ } from '../src/qq/auth.mjs';
import { normalizeMessage, localMessageId } from '../src/qq/events.mjs';
import { QQClient } from '../src/qq/client.mjs';
import { createGateway } from '../src/qq/gateway.mjs';
import { downloadFile, uploadContent, uploadFile } from '../src/qq/media.mjs';
import { getConnectorManifest } from '../src/connectors/catalog.mjs';
import { ConnectorProcess } from '../src/connectors/process.mjs';
import { ProcessRegistry } from '../src/process-utils.mjs';
import { Store } from '../src/store.mjs';
import { Harness } from '../src/harness.mjs';
import { ActionService } from '../src/actions/service.mjs';
import { invokeOperation } from '../src/connectors/operations.mjs';
import { chatEvent, mailEvent } from './event-fixture.mjs';

const account = { appId: '123456', appSecret: 'secret_that_must_never_be_logged', ownerId: 'OWNER_OPENID', activatedAt: 0 };
const logger = { info() {}, warn() {}, error() {}, debug() {}, child() { return this; } };
const message = (id = 'msg-1', content = 'hello', extra = {}) => ({
  kind: 'c2c', rawEventType: 'C2C_MESSAGE_CREATE', senderId: account.ownerId, messageId: id,
  timestamp: new Date().toISOString(), content, msgIdx: `REFIDX_${id}==`, ...extra,
});
const waitAbort = (signal) => new Promise((resolve) => {
  if (signal.aborted) resolve();
  else signal.addEventListener('abort', resolve, { once: true });
});
function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-qq-'));
  const state = new QQState(dir);
  t.after(() => { state.close(); fs.rmSync(dir, { recursive: true, force: true }); });
  return { dir, state };
}
function provider(fn = () => assert.fail('unexpected HTTP request')) {
  return async (url, init) => {
    assert.equal(init.redirect, 'error');
    assert.equal(new URL(url).origin, 'https://api.bot.qq.com');
    const body = init.body ? JSON.parse(init.body) : undefined;
    if (url.endsWith('/app/getAppAccessToken')) {
      assert.equal(body.appId, account.appId); assert.equal(body.clientSecret, account.appSecret);
      return Response.json({ access_token: 'private-access-token', expires_in: '7200' });
    }
    assert.equal(init.headers.Authorization, 'QQBot private-access-token');
    if (url.endsWith('/gateway')) return Response.json({ url: 'wss://api.sgroup.qq.com/websocket/' });
    return fn(new URL(url).pathname, body, init);
  };
}
async function client(t, fn, options = {}) {
  const { dir, state } = fixture(t);
  state.saveAccount(account);
  const c = new QQClient({ dataDir: dir, retentionMs: 86400_000, sendTimeoutMs: 5000 }, null, logger, {
    fetchImpl: provider(fn), ...options,
  });
  t.after(() => c.stop());
  await c.preflight();
  return c;
}

test('API validates credentials, contains all provider data and treats HTTP-200 business errors as failures', async () => {
  assert.throws(() => new QQApi({ appId: '../id', appSecret: account.appSecret }), /Invalid/);
  for (const fetchImpl of [async () => { throw new Error(account.appSecret); },
    async () => new Response(account.appSecret),
    async () => Response.json({ code: 100007, message: account.appSecret }),
    async () => Response.json({ err_code: 100016, message: account.appSecret }),
    async () => new Response(new ReadableStream({ start(c) { c.error(new Error(account.appSecret)); } })),
  ]) {
    await assert.rejects(new QQApi(account, { fetchImpl }).accessToken(), (error) => !error.stack.includes(account.appSecret) && !error.cause);
  }
  const api = new QQApi(account, { fetchImpl: provider(() => Response.json({ code: 40034105, message: account.appSecret })) });
  await assert.rejects(api.request(`/v2/users/${account.ownerId}/messages`, {}), (e) => e instanceof QQError && /not permitted/.test(e.message));
  await assert.rejects(api.request('/../../messages', {}), /Unsupported/);
  for (const url of ['wss://evil.example/', 'ws://api.sgroup.qq.com/', 'wss://user:password@api.sgroup.qq.com/']) {
    api.request = async () => ({ url });
    await assert.rejects(api.gateway(), /Invalid QQ gateway/);
  }
  const manifest = getConnectorManifest('qq');
  assert.equal(manifest.actions['message.send'].idempotent, undefined);
  assert.deepEqual(manifest.queries, ['message.media']);
});

test('token refresh uses expires_in, and queued moderation responses never claim delivery success', async () => {
  let tokens = 0;
  const api = new QQApi(account, { fetchImpl: async (url) => {
    if (url.endsWith('/app/getAppAccessToken')) { tokens++; return Response.json({ access_token: `token${tokens}`, expires_in: '7200' }); }
    return Response.json({ id: 'pending' }, { status: 202 });
  } });
  assert.equal(await api.accessToken(), 'token1');
  assert.equal(await api.accessToken(), 'token1');
  api.expiresAt = 0;
  assert.equal(await api.accessToken(), 'token2');
  await assert.rejects(api.request(`/v2/users/${account.ownerId}/messages`, {}), /HTTP 202/);
  const aborted = AbortSignal.abort();
  await assert.rejects(api.request(`/v2/users/${account.ownerId}/messages`, {}, aborted), /cancelled/);
});

test('binding accepts only fresh C2C proof; strangers/groups/wrong challenges cannot replace an account', async (t) => {
  const { state } = fixture(t);
  state.saveAccount({ ...account, appSecret: 'previous_private_secret' });
  let challenge;
  const result = await loginQQ({ state, ...account, fetchImpl: provider(),
    onChallenge: (value) => { challenge = value.challenge; },
    gatewayFactory: ({ signal, onReady, onMessage }) => ({ async start() {
      onReady(); await delay(0);
      assert.match(challenge, /^vika-bind [a-f0-9]{36}$/);
      for (const bad of [message('wrong', 'vika-bind wrong'), message('group', challenge, { kind: 'group' }),
        message('old', challenge, { timestamp: new Date(0).toISOString() }), message('bot', challenge, { senderIsBot: true })]) onMessage(bad);
      assert.equal(state.account().appSecret, 'previous_private_secret');
      onMessage(message('proof', challenge));
      await waitAbort(signal);
    } }),
  });
  assert.equal(result.ownerId, account.ownerId);
  assert.equal(state.account().appSecret, account.appSecret);
  assert.equal(state.account().activatedAt, 0);
  assert.equal(fs.statSync(state.databasePath).mode & 0o777, 0o600);
  assert.equal(fs.statSync(state.directory).mode & 0o777, 0o700);
  state.acquire(); state.release();
});

test('cancelled reauthorization preserves credentials and owner messages; switching owner clears old private routes', async (t) => {
  const { state, dir } = fixture(t);
  state.saveAccount(account);
  const event = normalizeMessage(message(), account, state);
  state.recordRoute(event);
  const other = new QQState(dir);
  t.after(() => other.close());
  const controller = new AbortController();
  await assert.rejects(loginQQ({ state, ...account, fetchImpl: provider(), signal: controller.signal,
    gatewayFactory: ({ onReady, onMessage, signal }) => ({ async start() {
      assert.throws(() => other.acquire(), /already in use/);
      onReady(); onMessage(message('pending', 'owner task')); onMessage(message('stranger', 'private', { senderId: 'STRANGER' }));
      controller.abort(new Error('cancelled'));
      await waitAbort(signal);
    } }),
  }), /cancelled/);
  assert.equal(state.pending().length, 1);
  assert.equal(state.account().ownerId, account.ownerId);
  state.saveAccount({ ...account, ownerId: 'NEW_OWNER' });
  assert.equal(state.pending().length, 0);
  assert.equal(state.route(event.id), null);
  state.acquire(); state.release();
});

test('binding failure closes its lease and redacts SDK errors without changing credentials', async (t) => {
  const { state } = fixture(t);
  state.saveAccount(account);
  await assert.rejects(loginQQ({ state, ...account, fetchImpl: provider(),
    gatewayFactory: () => ({ start() { throw new Error(account.appSecret); } }),
  }), (error) => /QQ gateway/.test(error.message) && !error.stack.includes(account.appSecret));
  assert.deepEqual(state.account(), account);
  state.acquire(); state.release();
});

test('canonical events keep stable IDs, quoted parents and attachments but omit auth tokens and foreign chats', (t) => {
  const { state } = fixture(t);
  const first = normalizeMessage(message(), account, state);
  state.recordRoute(first);
  const second = normalizeMessage(message('msg-2', 'quoted', { refMsgIdx: 'REFIDX_msg-1==',
    attachments: [{ url: 'https://multimedia.nt.qq.com/file?secret=SIGNATURE', content_type: 'image/png', filename: 'photo.png', size: 3 }],
    raw: { auth_token: 'SUPERSECRET' }, messageScene: { ext: ['auth_token=SUPERSECRET'] },
    msgElements: [{ content: 'untrusted quote must not become the actual request' }],
  }), account, state);
  assert.equal(second.envelope.message.parent_id, first.id);
  assert.equal(second.envelope.message.type, 'image');
  assert.equal(second.envelope.conversation.type, 'p2p');
  assert.doesNotMatch(JSON.stringify(second.envelope), /SIGNATURE|SUPERSECRET|untrusted quote/);
  assert.equal(normalizeMessage(message(), account, state).envelope.id, first.envelope.id);
  assert.notEqual(normalizeMessage(message('msg-1', 'different', { msgIdx: 'REFIDX_distinct' }), account, state).id, first.id);
  for (const extra of [{ kind: 'group' }, { senderId: 'STRANGER' }, { timestamp: 'invalid' }, { senderIsBot: true }, { content: 'vika-bind old-secret' }]) {
    assert.equal(normalizeMessage(message('x', 'hello', extra), account, state), null);
  }
  assert.equal(normalizeMessage(message('voice', '/new', {
    attachments: [{ url: 'https://multimedia.nt.qq.com/audio', content_type: 'audio/silk' }],
  }), account, state), null);
});

test('passive sequences persist across duplicate events, ambiguous sends and restarts; active sends do not borrow reply quota', async (t) => {
  const bodies = [];
  const c = await client(t, async (_path, body) => {
    bodies.push(body);
    if (bodies.length === 1) throw new Error(account.appSecret);
    return Response.json({ id: `sent-${bodies.length}`, ext_info: { ref_idx: `REFIDX_sent-${bodies.length}` } });
  });
  const event = normalizeMessage(message(), account, c.state);
  c.state.recordRoute(event);
  const input = { message_id: event.id, content: { type: 'markdown', text: '**plain text**' } };
  await assert.rejects(c.replyMessage(input), /QQ request failed/);
  assert.equal(bodies.length, 1, 'an uncertain send is never retried');
  c.state.recordRoute(event);
  const reopened = new QQState(c.config.dataDir);
  try { assert.equal(reopened.reserveReply(event.nativeId), 2); } finally { reopened.close(); }
  c.nextSendAt = 0;
  await c.replyMessage(input);
  assert.equal(bodies.at(-1).msg_seq, 3);
  assert.equal(bodies.at(-1).msg_id, event.nativeId);
  assert.equal(bodies.at(-1).message_reference.message_id, event.refIdx);
  c.nextSendAt = 0; await c.replyMessage(input);
  c.nextSendAt = 0; await assert.rejects(c.replyMessage(input), /count limit/);
  assert.equal(bodies.length, 3);
  await c.sendMessage({ chat_id: account.ownerId, content: input.content });
  assert.equal(bodies.at(-1).msg_id, undefined);
  assert.equal(bodies.at(-1).message_reference, undefined);
  await assert.rejects(c.sendMessage({ chat_id: 'STRANGER', content: input.content }), /verified owner/);
  const expired = normalizeMessage(message('expired', 'hi', { timestamp: new Date(Date.now() - REPLY_WINDOW_MS - 1).toISOString() }), account, c.state);
  c.state.recordRoute(expired); c.nextSendAt = 0;
  await assert.rejects(c.replyMessage({ ...input, message_id: expired.id }), /expired/);
  assert.equal(bodies.length, 4, 'expired replies never fall back to proactive sends');
});

test('pause stops events while allowing drain replies; stop cancels queued sends before any POST', async (t) => {
  let handler, sent = 0;
  const c = await client(t, () => { sent++; return Response.json({ id: 'sent' }); }, {
    gatewayFactory: ({ onMessage, signal }) => ({ start() { handler = onMessage; return waitAbort(signal); } }),
  });
  const lines = [];
  c.startSubscription((line) => lines.push(JSON.parse(line)), () => assert.fail('unexpected exit'));
  await delay(0);
  handler(message());
  await c.stopSubscription();
  handler(message('late'));
  assert.equal(lines.length, 1);
  await c.replyMessage({ message_id: lines[0].message.id, content: { type: 'markdown', text: 'drained' } });
  assert.equal(sent, 1);
  c.nextSendAt = Date.now() + 10_000;
  const queued = assert.rejects(c.sendMessage({ chat_id: account.ownerId, content: { type: 'markdown', text: 'cancel' } }));
  await c.stop(); await queued;
  assert.equal(sent, 1);
});

test('uploads validate file snapshots and use base64 with srv_send_msg=false for small files', async (t) => {
  const { dir } = fixture(t);
  const file = path.join(dir, 'test.png'); fs.writeFileSync(file, 'png');
  const content = uploadContent({ path: file });
  const calls = [];
  const api = new QQApi(account, { fetchImpl: provider((url, body) => { calls.push({ url, body }); return Response.json({ file_info: 'file-info' }); }) });
  assert.equal(await uploadFile(api, account.ownerId, content, AbortSignal.timeout(1000)), 'file-info');
  assert.equal(calls[0].body.srv_send_msg, false);
  assert.equal(calls[0].body.file_type, 1);
  assert.equal(Buffer.from(calls[0].body.file_data, 'base64').toString(), 'png');
  fs.truncateSync(file, MEDIA_BYTES + 1);
  assert.throws(() => uploadContent({ path: file }), /20 MiB/);
  const symlink = path.join(dir, 'link'); fs.symlinkSync(file, symlink);
  assert.throws(() => uploadContent({ path: symlink }));
  assert.throws(() => uploadContent({ path: dir }), /regular file/);
});

test('large uploads validate signed COS parts and acknowledge them before completing', async () => {
  const bytes = Buffer.alloc(5_000_001, 7);
  const requests = [], parts = [];
  const api = {
    async request(url, body) {
      requests.push({ url, body });
      if (url.endsWith('/upload_prepare')) return { upload_id: 'upload-1', block_size: 3_000_000,
        parts: [1, 2].map((index) => ({ index, presigned_url: `https://bucket-123.cos.ap-guangzhou.myqcloud.com/part${index}?signature=PRIVATE` })) };
      return { file_info: 'completed' };
    },
    async fetchImpl(url, init) { assert.equal(init.redirect, 'error'); assert.equal(init.headers, undefined); parts.push(init.body); return new Response('ok'); },
  };
  assert.equal(await uploadFile(api, account.ownerId, { bytes, name: 'file.bin', fileType: 4 }, AbortSignal.timeout(1000)), 'completed');
  assert.deepEqual(parts.map((part) => part.length), [3_000_000, 2_000_001]);
  assert.deepEqual(Buffer.concat(parts), bytes);
  assert.equal(requests.at(-1).body.srv_send_msg, false);
  assert.equal(requests.at(-1).body.upload_id, 'upload-1');
  assert.deepEqual(requests.filter(({ url }) => url.endsWith('/upload_part_finish')).map(({ body }) => body.part_index), [1, 2]);
  let puts = 0;
  api.fetchImpl = async () => { puts++; return new Response(''); };
  api.request = async () => ({ upload_id: 'bad', block_size: 3_000_000,
    parts: [{ index: 1, presigned_url: 'https://127.0.0.1/private' }, { index: 2, presigned_url: 'https://evil.example/' }] });
  await assert.rejects(uploadFile(api, account.ownerId, { bytes, name: 'x', fileType: 4 }, AbortSignal.timeout(1000)), /host/);
  assert.equal(puts, 0);
});

test('part acknowledgement retries only the explicit transient code, and cancellation prevents the final message', async (t) => {
  const { dir } = fixture(t);
  const file = path.join(dir, 'large.bin'); fs.writeFileSync(file, Buffer.alloc(5_000_000, 1));
  let finishCalls = 0, messageCalls = 0, completeCalls = 0;
  const api = {
    fetchImpl: async () => new Response('ok'),
    async request(url) {
      if (url.endsWith('/upload_prepare')) return { upload_id: 'upload', block_size: 5_000_000,
        parts: [{ index: 1, presigned_url: 'https://bucket-123.cos.ap-guangzhou.myqcloud.com/file' }] };
      if (url.endsWith('/upload_part_finish') && ++finishCalls === 1) throw new QQError(400, 40093001);
      if (url.endsWith('/files')) completeCalls++;
      if (url.endsWith('/messages')) messageCalls++;
      return { file_info: 'done', id: 'sent' };
    },
  };
  await uploadFile(api, account.ownerId, uploadContent({ path: file }), AbortSignal.timeout(5000));
  assert.equal(finishCalls, 2); assert.equal(completeCalls, 1); assert.equal(messageCalls, 0);
  const c = await client(t, () => assert.fail('unexpected API request'));
  c.api = api;
  const previous = api.request;
  api.request = async (url, ...args) => {
    const result = await previous(url, ...args);
    if (url.endsWith('/files')) c.actions.abort();
    return result;
  };
  await assert.rejects(c.sendMessage({ chat_id: account.ownerId, content: { type: 'file', path: file } }));
  assert.equal(messageCalls, 0);
});

test('downloads restrict hosts, omit authorization, bound streams and store private files without path traversal', async (t) => {
  const { state } = fixture(t);
  const api = { fetchImpl: async (url, init) => {
    assert.equal(url, 'https://multimedia.nt.qq.com/file?sig=secret');
    assert.equal(init.headers, undefined); assert.equal(init.redirect, 'error'); return new Response('data');
  } };
  const media = { url: 'http://multimedia.nt.qq.com/file?sig=secret', name: '../../a.txt', kind: 'file', size: 4 };
  const got = await downloadFile(api, media, state.mediaDirectory, AbortSignal.timeout(1000));
  assert.equal(got.name, 'a.txt'); assert.equal(fs.readFileSync(got.path, 'utf8'), 'data');
  assert.equal(fs.statSync(got.path).mode & 0o777, 0o600);
  for (const url of ['https://localhost/file', 'https://evil.qq.com/file', 'https://multimedia.nt.qq.com.evil.example/file',
    'https://u:p@multimedia.nt.qq.com/file', 'https://multimedia.nt.qq.com:444/file', 'file:///tmp/private']) {
    await assert.rejects(downloadFile(api, { ...media, url }, state.mediaDirectory, AbortSignal.timeout(1000)), /host/);
  }
  let cancelled = false;
  api.fetchImpl = async () => new Response(new ReadableStream({
    pull(c) { c.enqueue(new Uint8Array(5_000_000)); }, cancel() { cancelled = true; },
  }));
  await assert.rejects(downloadFile(api, { ...media, size: undefined }, state.mediaDirectory, AbortSignal.timeout(1000)), /size limit/);
  assert.equal(cancelled, true);
  api.fetchImpl = async () => { throw new Error(media.url); };
  await assert.rejects(downloadFile(api, media, state.mediaDirectory, AbortSignal.timeout(1000)), (e) => !e.stack.includes('sig=secret'));
});

test('official SDK handles Identify, C2C dispatch and Resume over a real local WebSocket', { timeout: 10_000 }, async (t) => {
  const server = new WebSocketServer({ port: 0, host: '127.0.0.1' });
  await new Promise((resolve) => server.once('listening', resolve));
  const controller = new AbortController();
  t.after(() => { controller.abort(); for (const socket of server.clients) socket.terminate(); server.close(); });
  const resumed = Promise.withResolvers();
  const received = [];
  let connects = 0;
  server.on('connection', (socket) => {
    const n = ++connects;
    socket.send(JSON.stringify({ op: 10, d: { heartbeat_interval: 1000 } }));
    socket.on('message', (bytes) => {
      const payload = JSON.parse(bytes);
      if (payload.op === 1) { socket.send(JSON.stringify({ op: 11 })); return; }
      assert.equal(payload.d.token, 'QQBot test-token');
      if (n === 1) {
        assert.equal(payload.op, 2); assert.equal(payload.d.intents, 1 << 25);
        socket.send(JSON.stringify({ op: 0, s: 1, t: 'READY', d: { session_id: 'session-1' } }));
        socket.send(JSON.stringify({ op: 0, s: 2, t: 'C2C_MESSAGE_CREATE', d: { id: 'native', author: { user_openid: account.ownerId },
          content: 'hello', timestamp: new Date().toISOString(), message_scene: { ext: ['auth_token=DO_NOT_LOG'] } } }));
        socket.send(JSON.stringify({ op: 7 }));
      } else {
        assert.equal(payload.op, 6); assert.equal(payload.d.session_id, 'session-1'); assert.equal(payload.d.seq, 2);
        socket.send(JSON.stringify({ op: 0, t: 'RESUMED', s: 3, d: {} })); resumed.resolve();
      }
    });
  });
  const gateway = createGateway({ signal: controller.signal, logger,
    api: { appId: account.appId, accessToken: async () => 'test-token', gateway: async () => `ws://127.0.0.1:${server.address().port}`, clearToken() {} },
    onMessage: (msg) => received.push(msg),
  });
  const run = gateway.start();
  await resumed.promise;
  assert.equal(received.length, 1); assert.equal(received[0].senderId, account.ownerId);
  controller.abort(); await run;
});

test('real QQ worker supports spec/check/start/pause/reply/stop and releases the lease', { timeout: 10_000 }, async (t) => {
  const { dir, state } = fixture(t); state.saveAccount(account);
  const event = normalizeMessage(message(), account, state);
  state.stage(event);
  const workerFile = path.join(dir, 'worker.mjs');
  fs.writeFileSync(workerFile, `
    const { QQClient } = await import(${JSON.stringify(new URL('../src/qq/client.mjs', import.meta.url).href)});
    const preflight = QQClient.prototype.preflight;
    QQClient.prototype.preflight = function () {
      this.gatewayFactory = ({ signal }) => ({ start: () => new Promise(resolve => {
        if (signal.aborted) resolve(); else signal.addEventListener('abort', resolve, { once: true });
      }) });
      return preflight.call(this);
    };
    globalThis.fetch = async (url, init) => Response.json(url.endsWith('/app/getAppAccessToken')
      ? { access_token: 'token', expires_in: '7200' } : url.endsWith('/gateway')
      ? { url: 'wss://api.sgroup.qq.com/' } : { id: 'sent-message' });
    await import(${JSON.stringify(new URL('../src/connectors/worker.mjs', import.meta.url).href)});
  `);
  const registry = new ProcessRegistry(logger);
  const worker = new ConnectorProcess('qq', { dataDir: dir, projectDir: process.cwd(), connectorWorkerPath: workerFile,
    retentionMs: 86400_000, maxEventBytes: 100_000, sendTimeoutMs: 5000 }, registry, logger);
  t.after(async () => { await worker.stop(); await registry.stopAll(1000); });
  assert.equal((await worker.preflight()).manifest.name, 'qq');
  assert.equal(worker.owner.id, account.ownerId);
  const receive = Promise.withResolvers();
  await worker.startSubscription(receive.resolve, () => assert.fail('unexpected worker exit'));
  const inbound = await receive.promise;
  await worker.request('pause', {});
  const sent = await worker.invoke('message.reply', { message_id: inbound.message.id, content: { type: 'markdown', text: 'reply' } });
  assert.equal(sent.message_id, localMessageId(account.appId, 'sent-message'));
  await worker.stop(); state.acquire(); state.release();
});

test('Harness/outbox dedup QQ events and replies; primary QQ routes owner notices and numbered mail approvals', async (t) => {
  const sent = [];
  const c = await client(t, (_path, body) => { sent.push(body); return Response.json({ id: `sent-${sent.length}` }); });
  const store = new Store(':memory:', logger); store.initializeCognition('codex', 'runtime-token');
  const mails = [];
  const connectors = {
    manifests: () => ['qq', 'email'].map((id) => [id, getConnectorManifest(id)]),
    owner: (id) => id === 'qq' ? { id: account.ownerId, target: { chat_id: account.ownerId } } : null,
    invoke: async (id, action, input) => {
      if (id === 'email') { mails.push(input); return { sent: true }; }
      c.nextSendAt = 0; return invokeOperation(c, action, input);
    },
  };
  const config = { channel: 'qq', maxReplyChars: 4000, maxEventBytes: 100_000,
    maxEventAgeMs: 60_000, ignoredSenderIds: new Set(), workspace: c.config.dataDir, tempDir: c.config.dataDir };
  const actions = new ActionService({ config, store, connectors, logger });
  const delivered = [], commands = [];
  const harness = new Harness({ config, store, logger, recordActivity() {},
    runtime: { snapshot: () => ({ session: { connected: true }, work: { turnInProgress: false } }),
      submit: (items) => { delivered.push(...items); return { accepted: true }; } }, onSlashCommand: (cmd) => commands.push(cmd),
  });
  t.after(() => { harness.stop(); store.close(); });
  const event = normalizeMessage(message(), account, c.state); c.state.recordRoute(event);
  harness.handleConnectorEvent(event.envelope); harness.handleConnectorEvent(event.envelope);
  harness.handleConnectorEvent(normalizeMessage(message('cmd', '/help'), account, c.state).envelope);
  const attachment = normalizeMessage(message('image', '/new', {
    attachments: [{ url: 'https://multimedia.nt.qq.com/x', content_type: 'image/png', size: 3 }],
  }), account, c.state);
  harness.handleConnectorEvent(attachment.envelope);
  await harness.ingressBarrier;
  assert.equal(delivered.length, 2); assert.equal(commands.length, 1);
  const call = (name, input) => actions.call(name, input, { authority: 'runtime-token' });
  const reply = { message_id: event.id, markdown: '回答', decision_id: attachment.id };
  await call('reply_message', reply); await call('reply_message', reply);
  assert.equal(sent.length, 1); assert.equal(store.db.prepare('SELECT status FROM outbox').get().status, 'SENT');
  store.ingest(mailEvent({ uid: 7 }));
  await call('notify_owner', { markdown: '新邮件', decision_id: 'imap:1:7' });
  assert.equal(sent.at(-1).msg_id, undefined);
  await call('reply_message', { message_id: 'mail:1:7', markdown: '周四可以', decision_id: 'imap:1:7' });
  assert.match(sent.at(-1).content, /发送 1/); assert.equal(mails.length, 0);
  const proof = normalizeMessage(message('approve', '发送 1'), account, c.state); c.state.recordRoute(proof);
  const approve = chatEvent({ source: 'qq', chatId: account.ownerId, senderId: account.ownerId,
    messageId: proof.id, chatType: 'p2p', content: '发送 1' });
  store.ingest(approve);
  await actions.answerApproval(approve, actions.matchApprovalReply('发送 1', approve));
  assert.equal(mails.length, 1); assert.equal(mails[0].content.text, '周四可以');
});
