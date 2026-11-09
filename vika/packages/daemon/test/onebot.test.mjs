// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import test from 'node:test';
import { WebSocketServer } from 'ws';
import { OneBotError, OneBotSocket, ONEBOT_VERSION, validToken, validUrl } from '../src/onebot/api.mjs';
import { OneBotState } from '../src/onebot/state.mjs';
import { loginOneBot } from '../src/onebot/auth.mjs';
import {
  chatOf, cleanName, eventTime, groupChat, historyMessage, isSelf, messageId, namedUsers, normalizeMessage,
  parseChat, parseMessageId, privateChat,
} from '../src/onebot/events.mjs';
import { outboundSegments, plainText, TEXT_LIMIT } from '../src/onebot/format.mjs';
import { admitted, classify, EMPTY_POLICY, groupMode, parsePolicy, PolicyFile } from '../src/onebot/policy.mjs';
import { CONTEXT_AGE_MS, CONTEXT_LINES, MAX_HOLD_MS, ReleaseGate, TURN_GRACE_MS } from '../src/onebot/gate.mjs';
import { OneBotClient } from '../src/onebot/client.mjs';
import { getConnectorManifest } from '../src/connectors/catalog.mjs';
import { ConnectorProcess } from '../src/connectors/process.mjs';
import { invokeOperation } from '../src/connectors/operations.mjs';
import { ProcessRegistry } from '../src/process-utils.mjs';
import { normalizeEvent } from '../src/event.mjs';
import { formatInboundLine, mentionTarget } from '../src/runtime-input.mjs';
import { Store } from '../src/store.mjs';
import { Harness } from '../src/harness.mjs';
import { ActionService } from '../src/actions/service.mjs';

const URL_ = 'ws://127.0.0.1:3001/';
const TOKEN = 'napcat-Secret_123';
const SELF = '10001';
const OWNER = '20002';

function recorder() {
  const lines = [];
  const log = (level) => (event, fields = {}) => lines.push({ level, event, fields });
  return { lines, logger: { debug: log('debug'), info: log('info'), warn: log('warn'), error: log('error') } };
}

const ok = (data, echo) => ({ status: 'ok', retcode: 0, data, message: '', wording: '', echo });
const lifecycle = { time: 1, self_id: Number(SELF), post_type: 'meta_event', meta_event_type: 'lifecycle', sub_type: 'connect' };

// A OneBot forward-WebSocket server as the tests need it: the lifecycle
// greeting on open (as NapCat, LLOneBot and Lagrange send it), answers from
// `actions` by name, frames on demand.
function sockets({ actions = {}, open = () => 'greet' } = {}) {
  const opened = [];
  class Socket extends EventTarget {
    constructor(url, init) {
      super();
      this.url = url;
      this.init = init;
      this.sent = [];
      this.readyState = 0;
      opened.push(this);
      const how = open(opened.length);
      setImmediate(() => {
        if (how === 'refuse') {
          // Node 22: an error event and no close for a failed handshake.
          this.dispatchEvent(new Event('error'));
          return;
        }
        this.readyState = 1;
        this.dispatchEvent(new Event('open'));
        if (how === 'greet') this.frame(lifecycle);
      });
    }

    send(data) {
      const request = JSON.parse(data);
      this.sent.push(request);
      const handler = actions[request.action];
      if (handler) setImmediate(async () => this.frame({ ...await handler(request.params, this), echo: request.echo }));
    }

    close(code = 1005) {
      if (this.readyState === 3) return;
      this.readyState = 3;
      this.dispatchEvent(Object.assign(new Event('close'), { code }));
    }

    frame(value) { this.raw(JSON.stringify(value)); }
    raw(data) { if (this.readyState === 1) this.dispatchEvent(new MessageEvent('message', { data })); }
  }
  return { Socket, opened };
}

// Timers that move only when the test says so.
function clock(start = 1_000_000) {
  let current = start;
  const timers = new Set();
  return {
    now: () => current,
    setTimer: (fn, ms) => { const timer = { fn, at: current + ms }; timers.add(timer); return timer; },
    clearTimer: (timer) => { timers.delete(timer); },
    advance(ms) {
      const until = current + ms;
      for (;;) {
        const next = [...timers].sort((a, b) => a.at - b.at)[0];
        if (!next || next.at > until) break;
        timers.delete(next);
        current = next.at;
        next.fn();
      }
      current = until;
    },
  };
}

async function until(check, ms = 2000) {
  const deadline = Date.now() + ms;
  while (!check()) {
    if (Date.now() > deadline) throw new Error('condition not met in time');
    await delay(2);
  }
}

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-onebot-'));
  const state = new OneBotState(dir);
  t.after(() => { state.close(); fs.rmSync(dir, { recursive: true, force: true }); });
  return { dir, state };
}

function started(socket, onEvent = () => {}) {
  const controller = new AbortController();
  let ready = 0;
  const running = socket.run(onEvent, controller.signal, { onReady: () => { ready += 1; } });
  return { running, stop: () => controller.abort(), ready: () => ready };
}

test('addresses are ws/wss without credentials and tokens are printable ASCII or empty', () => {
  assert.equal(ONEBOT_VERSION, 'onebot-v11');
  for (const url of ['ws://127.0.0.1:3001', 'wss://bot.example.com/onebot', 'ws://[::1]:3001/']) assert.equal(validUrl(url), true, url);
  for (const url of ['http://127.0.0.1:3001', 'ws://user:pass@127.0.0.1:3001', 'ws://127.0.0.1:3001/?access_token=x',
    'ws://127.0.0.1:3001/#x', 'not a url', 42, `ws://h/${'a'.repeat(2048)}`]) assert.equal(validUrl(url), false, String(url).slice(0, 40));
  for (const token of ['', TOKEN, 'x'.repeat(512)]) assert.equal(validToken(token), true);
  for (const token of ['has space', 'x'.repeat(513), 'tab\t', '令牌', null, undefined]) assert.equal(validToken(token), false);
  assert.throws(() => new OneBotSocket({ url: 'ws://127.0.0.1/?access_token=x', token: '' }), /Invalid OneBot WebSocket address/);
  assert.throws(() => new OneBotSocket({ url: URL_, token: 'bad token' }), /Invalid OneBot access token/);
});

test('calls correlate by echo, answer out of order and carry the token only in the header', async () => {
  const ws = sockets();
  const { lines, logger } = recorder();
  const socket = new OneBotSocket({ url: URL_, token: TOKEN, WebSocketImpl: ws.Socket, logger });
  const events = [];
  const run = started(socket, (raw) => events.push(raw));
  assert.equal(socket.connected, false);
  await until(() => run.ready() === 1);
  assert.equal(socket.connected, true);
  assert.equal(ws.opened[0].url, URL_);
  assert.deepEqual(ws.opened[0].init, { headers: { Authorization: `Bearer ${TOKEN}` } });
  assert.deepEqual(events, [lifecycle]);

  const first = socket.call('get_login_info');
  const second = socket.call('get_group_list', { no_cache: false });
  const [a, b] = ws.opened[0].sent;
  assert.deepEqual([a.action, a.params, b.action, b.params], ['get_login_info', {}, 'get_group_list', { no_cache: false }]);
  assert.notEqual(a.echo, b.echo);
  ws.opened[0].frame({ ...ok([{ group_id: 1 }], b.echo) });
  ws.opened[0].frame({ ...ok({ user_id: 1 }, 'someone-else') });
  ws.opened[0].frame({ ...ok({ user_id: Number(SELF) }, a.echo) });
  assert.deepEqual(await second, [{ group_id: 1 }]);
  assert.deepEqual(await first, { user_id: Number(SELF) });

  // Failures carry the retcode and one safe line of the implementation's words.
  const failed = socket.call('get_image', { file: 'a.jpg' });
  ws.opened[0].frame({ status: 'failed', retcode: 1200, data: null, echo: ws.opened[0].sent[2].echo,
    message: `下载失败\nhttps://multimedia.nt.qq.com.cn/download?rkey=SECRET ${TOKEN}`, wording: '' });
  const error = await failed.catch((e) => e);
  assert.ok(error instanceof OneBotError);
  assert.equal(error.retcode, 1200);
  assert.equal(error.message, 'OneBot get_image failed (retcode 1200): 下载失败 [url] [token]');
  for (const [retcode, extra] of [[1404, { message: '不支持的API x' }], [404, {}]]) {
    const pending = socket.call('set_msg_emoji_like');
    ws.opened[0].frame({ status: 'failed', retcode, data: null, echo: ws.opened[0].sent.at(-1).echo, ...extra });
    const unsupported = await pending.catch((e) => e);
    assert.equal(unsupported.unsupported, true);
    assert.equal(unsupported.message, 'OneBot set_msg_emoji_like is not supported by this OneBot implementation');
  }
  // Lagrange answers without message or wording.
  const bare = socket.call('delete_msg');
  ws.opened[0].frame({ status: 'failed', retcode: -1, data: null, echo: ws.opened[0].sent.at(-1).echo });
  assert.equal((await bare.catch((e) => e)).message, 'OneBot delete_msg failed (retcode -1)');
  // Only retcode 0 is success.
  const odd = socket.call('get_status');
  ws.opened[0].frame({ status: 'ok', data: {}, echo: ws.opened[0].sent.at(-1).echo });
  assert.equal((await odd.catch((e) => e)).message, 'OneBot get_status failed (retcode missing)');
  await assert.rejects(socket.call('../etc'), /Unsupported OneBot action/);

  run.stop();
  await run.running;
  assert.equal(socket.connected, false);
  assert.ok(ws.opened[0].readyState === 3);
  assert.ok(!JSON.stringify(lines).includes(TOKEN));

  // NapCat runs without a token when none is set: no header at all.
  const open = sockets();
  const plain = started(new OneBotSocket({ url: URL_, token: '', WebSocketImpl: open.Socket, logger }));
  await until(() => plain.ready() === 1);
  assert.equal(open.opened[0].init, undefined);
  plain.stop();
  await plain.running;
});

test('a call says it was not sent without a connection and uncertain once written', async () => {
  const ws = sockets();
  const socket = new OneBotSocket({ url: URL_, token: TOKEN, WebSocketImpl: ws.Socket, callTimeoutMs: 30 });
  const before = await socket.call('send_msg', {}).catch((e) => e);
  assert.equal(before.message, 'OneBot is not connected; send_msg was not sent');

  const run = started(socket);
  await until(() => run.ready() === 1);
  const timedOut = await socket.call('send_msg', { message: 'hi' }).catch((e) => e);
  assert.equal(timedOut.message, 'OneBot send_msg got no answer in time; the outcome is uncertain');

  const cancel = new AbortController();
  const cancelled = socket.call('send_msg', {}, cancel.signal);
  cancel.abort();
  assert.equal((await cancelled.catch((e) => e)).message, 'OneBot send_msg was cancelled before it was answered; the outcome is uncertain');
  assert.equal((await socket.call('send_msg', {}, cancel.signal).catch((e) => e)).message, 'OneBot send_msg was cancelled before it was sent');

  // Closing but not yet closed: the browser API would silently discard the frame.
  ws.opened[0].readyState = 2;
  assert.equal((await socket.call('send_msg', {}).catch((e) => e)).message, 'OneBot is not connected; send_msg was not sent');
  ws.opened[0].readyState = 1;

  // The socket refuses the frame.
  const send = ws.opened[0].send;
  ws.opened[0].send = () => { throw new Error('closing'); };
  assert.equal((await socket.call('send_msg', {}).catch((e) => e)).message, 'OneBot send_msg could not be sent');
  ws.opened[0].send = send;

  const dropped = socket.call('send_msg', {});
  ws.opened[0].close(1006);
  const lost = await dropped.catch((e) => e);
  assert.equal(lost.message, 'OneBot connection closed before send_msg was answered; the outcome is uncertain');
  assert.equal(socket.connected, false);
  run.stop();
  await run.running;
});

test('a rejected token is fatal and never retried, in the NapCat and the LLOneBot shape', async () => {
  const rejected = { status: 'failed', retcode: 1403, data: null, message: 'token验证失败', wording: 'token验证失败', echo: null };
  for (const [name, reject] of [
    // NapCat: the 1403 frame, then a close without a code.
    ['napcat', (socket) => { socket.frame(rejected); socket.close(1005); }],
    // LLOneBot: the same frame without echo, then 1008.
    ['llonebot', (socket) => { socket.frame({ ...rejected, echo: undefined }); socket.close(1008); }],
    ['close 1008 only', (socket) => socket.close(1008)],
  ]) {
    const ws = sockets({ open: () => 'silent' });
    const { lines, logger } = recorder();
    let ready = 0;
    const running = new OneBotSocket({ url: URL_, token: TOKEN, WebSocketImpl: ws.Socket, logger, backoffMs: [1] })
      .run(() => {}, AbortSignal.timeout(2000), { onReady: () => { ready += 1; } });
    await until(() => ws.opened[0]?.readyState === 1);
    reject(ws.opened[0]);
    const error = await running.catch((e) => e);
    assert.ok(error instanceof OneBotError, name);
    assert.equal(error.auth, true, name);
    assert.match(error.message, /rejected the access token; run vika login onebot/);
    assert.ok(!error.message.includes(TOKEN));
    await delay(10);
    assert.equal(ws.opened.length, 1, name);
    assert.equal(ready, 0, name);
    assert.ok(!JSON.stringify(lines).includes(TOKEN));
  }
});

test('the global WebSocket sends a Bearer header that a NapCat-style server accepts or rejects', async (t) => {
  const server = new WebSocketServer({ port: 0, host: '127.0.0.1' });
  t.after(() => server.close());
  await new Promise((resolve) => server.once('listening', resolve));
  const seen = [];
  // websocket-server.ts authorize(): query token first, then the header.
  server.on('connection', (client, request) => {
    const query = new URL(request.url, 'http://localhost').searchParams.get('access_token');
    const header = request.headers.authorization?.split('Bearer ').pop() || '';
    seen.push({ url: request.url, header: request.headers.authorization ?? null });
    if ((query || header) !== TOKEN) {
      client.send(JSON.stringify({ status: 'failed', retcode: 1403, data: null, message: 'token验证失败', wording: 'token验证失败', echo: null }));
      client.close();
      return;
    }
    client.send(JSON.stringify(lifecycle));
    client.on('message', (data) => {
      const { action, echo } = JSON.parse(String(data));
      client.send(JSON.stringify(ok({ user_id: Number(SELF), nickname: action }, echo)));
    });
  });
  const url = `ws://127.0.0.1:${server.address().port}/`;
  const socket = new OneBotSocket({ url, token: TOKEN, logger: recorder().logger });
  const run = started(socket);
  await until(() => run.ready() === 1);
  assert.deepEqual(await socket.call('get_login_info'), { user_id: Number(SELF), nickname: 'get_login_info' });
  run.stop();
  await run.running;
  assert.deepEqual(seen, [{ url: '/', header: `Bearer ${TOKEN}` }]);

  const wrong = await new OneBotSocket({ url, token: 'wrong-token', backoffMs: [1] }).run(() => {}, AbortSignal.timeout(3000)).catch((e) => e);
  assert.equal(wrong.auth, true);
  assert.equal(seen.length, 2);
});

test('reconnects with backoff after a disconnect or a failed handshake and logs no address', async () => {
  // 1 greets then drops, 2 and 3 fail like a refused handshake, 4 stays.
  const ws = sockets({ open: (n) => (n === 2 || n === 3 ? 'refuse' : 'greet') });
  const { lines, logger } = recorder();
  const socket = new OneBotSocket({ url: URL_, token: TOKEN, WebSocketImpl: ws.Socket, logger, backoffMs: [5, 10, 20] });
  const run = started(socket);
  await until(() => run.ready() === 1);
  ws.opened[0].close(1006);
  await until(() => run.ready() === 2);
  assert.equal(ws.opened.length, 4);
  assert.deepEqual(lines.filter((line) => line.level === 'warn').map(({ event, fields }) => [event, fields.failures, fields.retryInMs]),
    [['onebot_disconnected', 0, 5], ['onebot_connect_failed', 1, 5], ['onebot_connect_failed', 2, 10]]);
  assert.ok(!JSON.stringify(lines).includes('127.0.0.1') && !JSON.stringify(lines).includes(TOKEN));
  run.stop();
  await run.running;
  assert.ok(ws.opened.every((s) => s.readyState === 3 || s.readyState === 0));

  // Without reconnect the first failure is the answer.
  const refused = sockets({ open: () => 'refuse' });
  await assert.rejects(new OneBotSocket({ url: URL_, token: TOKEN, WebSocketImpl: refused.Socket })
    .run(() => {}, AbortSignal.timeout(2000), { reconnect: false }), /Could not connect to the OneBot WebSocket server/);
  const dropping = sockets();
  const once = new OneBotSocket({ url: URL_, token: TOKEN, WebSocketImpl: dropping.Socket });
  const ended = once.run(() => {}, AbortSignal.timeout(2000), { reconnect: false, onReady: () => dropping.opened[0].close(1006) });
  await assert.rejects(ended, /OneBot connection closed/);
  assert.equal(dropping.opened.length, 1);
});

test('a silent connection is closed after twice the heartbeat interval plus ten seconds, and a mute one after the greeting time', async () => {
  const time = clock();
  const ws = sockets();
  const { lines, logger } = recorder();
  const socket = new OneBotSocket({ url: URL_, token: TOKEN, WebSocketImpl: ws.Socket, logger, backoffMs: [1000],
    now: time.now, setTimer: time.setTimer, clearTimer: time.clearTimer });
  const run = started(socket);
  await until(() => run.ready() === 1);
  // Without a heartbeat announcement silence is normal.
  time.advance(10 * 60_000);
  assert.equal(ws.opened[0].readyState, 1);
  ws.opened[0].frame({ post_type: 'meta_event', meta_event_type: 'heartbeat', interval: 5000, status: { online: true, good: true } });
  time.advance(19_000);
  ws.opened[0].frame({ post_type: 'notice', notice_type: 'x' });
  time.advance(19_000);
  assert.equal(ws.opened[0].readyState, 1);
  time.advance(1_000);
  assert.equal(ws.opened[0].readyState, 3);
  assert.ok(lines.some((line) => line.event === 'onebot_heartbeat_lost'));
  await until(() => lines.some((line) => line.event === 'onebot_disconnected'));
  time.advance(1000);
  await until(() => run.ready() === 2);
  run.stop();
  await run.running;

  // Connected but never greeted, as on NapCat's /api path.
  const mute = sockets({ open: () => 'silent' });
  const quiet = new OneBotSocket({ url: URL_, token: TOKEN, WebSocketImpl: mute.Socket, logger,
    now: time.now, setTimer: time.setTimer, clearTimer: time.clearTimer });
  const ended = quiet.run(() => {}, new AbortController().signal, { reconnect: false });
  await until(() => mute.opened[0]?.readyState === 1);
  assert.equal(quiet.connected, false);
  await assert.rejects(quiet.call('get_status'), /not connected/);
  time.advance(15_000);
  await assert.rejects(ended, /Could not connect/);
  assert.equal(mute.opened[0].readyState, 3);
});

test('oversize, binary and non-JSON frames are dropped without logging their content', async () => {
  const ws = sockets();
  const { lines, logger } = recorder();
  const events = [];
  const run = started(new OneBotSocket({ url: URL_, token: TOKEN, WebSocketImpl: ws.Socket, logger }), (raw) => events.push(raw));
  await until(() => run.ready() === 1);
  const socket = ws.opened[0];
  socket.raw('not json SECRET');
  socket.raw('[1,2]');
  socket.raw('null');
  socket.raw(new ArrayBuffer(4));
  socket.raw(`{"post_type":"message","pad":"${'x'.repeat(48 * 1024 * 1024)}"}`);
  socket.frame({ post_type: 'message', message_type: 'private' });
  assert.deepEqual(events.slice(1), [{ post_type: 'message', message_type: 'private' }]);
  const rejected = lines.filter((line) => line.event === 'onebot_frame_rejected');
  assert.equal(rejected.length, 5);
  assert.ok(rejected.every((line) => Object.keys(line.fields).length === 0));
  // A handler that throws does not end the connection.
  run.stop();
  await run.running;

  const throwing = sockets();
  const failing = recorder();
  const thrower = started(new OneBotSocket({ url: URL_, token: TOKEN, WebSocketImpl: throwing.Socket, logger: failing.logger }),
    () => { throw new Error('disk full'); });
  await until(() => thrower.ready() === 1);
  assert.equal(throwing.opened[0].readyState, 1);
  assert.ok(failing.lines.some((line) => line.event === 'onebot_event_failed'));
  thrower.stop();
  await thrower.running;
});

test('state keeps its modes, the pending order and context bounds, and another account clears what belonged to the old one', (t) => {
  const { dir, state } = fixture(t);
  assert.equal(fs.statSync(state.directory).mode & 0o777, 0o700);
  assert.equal(fs.statSync(state.databasePath).mode & 0o777, 0o600);
  assert.equal(state.directory, path.join(dir, 'onebot'));
  assert.ok(fs.statSync(state.mediaDirectory).isDirectory());
  assert.equal(state.account(), null);
  assert.throws(() => new OneBotState('relative'), /must be absolute/);

  const account = { url: URL_, token: TOKEN, selfId: SELF, selfName: 'Vika', ownerId: OWNER, activatedAt: 1_700_000_000_000 };
  for (const bad of [{ url: 'http://x' }, { token: 'a b' }, { selfId: '0123' }, { ownerId: 'abc' }, { activatedAt: 1.5 }, { selfName: null }]) {
    assert.throws(() => state.saveAccount({ ...account, ...bad }), /Invalid OneBot account/);
  }
  state.saveAccount(account);
  assert.deepEqual(state.account(), account);
  state.saveAccount({ ...account, token: '' });
  assert.equal(state.account().token, '');

  state.recordRoute('ob:group:123456:-5', { chat: 'group:123456', mid: -5, files: [{ id: '0', kind: 'image', name: 'a.jpg' }] });
  state.recordRoute('ob:group:123456:-5', { chat: 'group:123456', mid: '-5' });
  assert.deepEqual(state.route('ob:group:123456:-5'), { chat: 'group:123456', mid: '-5', files: [{ id: '0', kind: 'image', name: 'a.jpg' }] });
  assert.equal(state.route('ob:group:123456:6'), null);
  state.rememberPerson('group:123456', 30003, 'Alice');
  state.rememberPerson('group:123456', '30003', 'Alice A');
  assert.deepEqual(state.people('group:123456'), [{ id: '30003', name: 'Alice A' }]);
  state.recordSent('group:123456', -5);
  assert.equal(state.isSent('group:123456', '-5'), true);
  assert.equal(state.isSent('group:123456', '5'), false);

  const at = 1_000;
  const seqs = ['context', 'ambient', 'context', 'direct'].map((cls, i) => state.stage({ chat: 'group:123456', cls, at: at + i, envelope: { n: i } }));
  state.stage({ chat: 'private:20002', cls: 'direct', at, envelope: { n: 'p' } });
  assert.throws(() => state.stage({ chat: 'group:1', cls: 'drop', at, envelope: {} }), /Invalid OneBot pending line/);
  assert.deepEqual(state.pendingChats(), [{ chat: 'group:123456', classes: new Set(['context', 'ambient', 'direct']) },
    { chat: 'private:20002', classes: new Set(['direct']) }]);
  assert.deepEqual(state.pending('group:123456').map((row) => [row.seq, row.cls, row.at, row.envelope.n]),
    seqs.map((seq, i) => [seq, ['context', 'ambient', 'context', 'direct'][i], at + i, i]));
  state.delivered(seqs[1]);
  assert.deepEqual(state.pending('group:123456').map((row) => row.envelope.n), [0, 2, 3]);

  // Only context lines are bounded: by age and by count, newest kept.
  for (let i = 0; i < 5; i += 1) state.stage({ chat: 'group:123456', cls: 'context', at: 2_000 + i, envelope: { n: `c${i}` } });
  state.pruneContext('group:123456', { maxLines: 3, before: 0 });
  assert.deepEqual(state.pending('group:123456').map((row) => row.envelope.n), [3, 'c2', 'c3', 'c4']);
  state.pruneContext('group:123456', { maxLines: 30, before: 2_004 });
  assert.deepEqual(state.pending('group:123456').map((row) => row.envelope.n), [3, 'c4']);

  // The same account again keeps everything; another account starts clean.
  state.saveAccount({ ...account, ownerId: '40004' });
  assert.ok(state.route('ob:group:123456:-5') && state.pending('group:123456').length === 2);
  state.saveAccount({ ...account, selfId: '10002' });
  assert.equal(state.route('ob:group:123456:-5'), null);
  assert.deepEqual(state.people('group:123456'), []);
  assert.equal(state.isSent('group:123456', '-5'), false);
  assert.deepEqual(state.pendingChats(), []);

  state.recordRoute('ob:private:20002:7', { chat: 'private:20002', mid: 7 });
  const old = path.join(state.mediaDirectory, 'old.jpg');
  fs.writeFileSync(old, 'x');
  fs.utimesSync(old, new Date(0), new Date(0));
  state.prune(Date.now() + 1000);
  assert.equal(state.route('ob:private:20002:7'), null);
  assert.equal(fs.existsSync(old), false);
});

function bindingServer({ self = { user_id: Number(SELF), nickname: 'Vika\u0000Bot' }, groups } = {}) {
  return sockets({ actions: {
    get_login_info: () => ok(self),
    get_group_list: () => (groups === undefined
      ? ok([{ group_id: 123456789, group_name: 'Friends' }, { group_id: 'bad' }, { group_id: 987654321, group_name: '' }])
      : groups),
  } });
}

const now = () => Math.floor(Date.now() / 1000);
const privateMessage = (fields = {}) => ({ post_type: 'message', message_type: 'private', sub_type: 'friend', self_id: Number(SELF),
  user_id: Number(OWNER), time: now(), message_id: -123, message: [{ type: 'text', data: { text: 'hi' } }], raw_message: 'hi',
  sender: { user_id: Number(OWNER), nickname: 'Owner' }, ...fields });

test('binds only a fresh private friend message with the exact code and keeps activation on the same account', async (t) => {
  const { state } = fixture(t);
  const ws = bindingServer();
  let challenge;
  const login = loginOneBot({ state, url: URL_, token: TOKEN, WebSocketImpl: ws.Socket, signal: AbortSignal.timeout(3000),
    onChallenge: (value) => { challenge = value; } });
  await until(() => challenge);
  assert.match(challenge.code, /^vika-[a-z2-9]{8}$/);
  assert.deepEqual({ ...challenge, code: undefined }, { code: undefined, selfId: SELF, selfName: 'Vika Bot' });
  const text = (value) => [{ type: 'text', data: { text: value } }];
  const socket = ws.opened[0];
  socket.frame(privateMessage({ message_type: 'group', sub_type: 'normal', group_id: 123456789, user_id: 30003, message: text(challenge.code) }));
  socket.frame(privateMessage({ user_id: 30003, message: text('vika-wrong') }));
  socket.frame(privateMessage({ message: text(challenge.code), time: now() - 3600 }));
  socket.frame(privateMessage({ message: text(challenge.code), sub_type: 'group' }));
  // The account's own message carrying the code, as NapCat and as Lagrange report it.
  socket.frame(privateMessage({ post_type: 'message_sent', user_id: Number(SELF), target_id: Number(OWNER), message: text(challenge.code) }));
  socket.frame(privateMessage({ user_id: Number(SELF), message: text(challenge.code) }));
  await delay(20);
  assert.equal(state.account(), null);
  // NapCat's millisecond fallback for `time`, and a code split across text segments.
  const sentAt = Date.now();
  socket.frame(privateMessage({ time: sentAt, message: [{ type: 'text', data: { text: ` ${challenge.code.slice(0, 5)}` } },
    { type: 'text', data: { text: `${challenge.code.slice(5)} ` } }] }));
  const bound = await login;
  assert.deepEqual(bound, { selfId: SELF, selfName: 'Vika Bot', ownerId: OWNER,
    groups: [{ id: '123456789', name: 'Friends' }, { id: '987654321', name: '' }] });
  assert.deepEqual(state.account(), { url: URL_, token: TOKEN, selfId: SELF, selfName: 'Vika Bot', ownerId: OWNER, activatedAt: sentAt });
  assert.equal(socket.readyState, 3);

  // The same account bound again, in CQ-string format and without groups:
  // activation stays, and the group list is best effort.
  state.recordRoute('ob:group:123456789:1', { chat: 'group:123456789', mid: 1 });
  const again = bindingServer({ groups: { status: 'failed', retcode: 1200, data: null } });
  let code;
  const relogin = loginOneBot({ state, url: URL_, token: '', WebSocketImpl: again.Socket, signal: AbortSignal.timeout(3000),
    onChallenge: (value) => { code = value.code; } });
  await until(() => code);
  again.opened[0].frame(privateMessage({ user_id: 40004, message: code }));
  assert.deepEqual((await relogin).groups, []);
  assert.equal(state.account().activatedAt, sentAt);
  assert.equal(state.account().ownerId, '40004');
  assert.ok(state.route('ob:group:123456789:1'));
});

test('a failed login keeps the old account', async (t) => {
  const { state } = fixture(t);
  const account = { url: URL_, token: TOKEN, selfId: SELF, selfName: 'Vika', ownerId: OWNER, activatedAt: 1 };
  state.saveAccount(account);

  const rejecting = sockets({ open: () => 'silent' });
  const refused = loginOneBot({ state, url: URL_, token: 'wrong', WebSocketImpl: rejecting.Socket, signal: AbortSignal.timeout(3000) });
  await until(() => rejecting.opened[0]?.readyState === 1);
  rejecting.opened[0].frame({ status: 'failed', retcode: 1403, data: null, message: 'token验证失败', wording: 'token验证失败', echo: null });
  rejecting.opened[0].close();
  assert.equal((await refused.catch((e) => e)).auth, true);

  // Lagrange refuses a wrong token in the handshake: indistinguishable from
  // an unreachable server, so the message names both.
  await assert.rejects(loginOneBot({ state, url: URL_, token: TOKEN, WebSocketImpl: sockets({ open: () => 'refuse' }).Socket,
    signal: AbortSignal.timeout(3000) }), /Could not connect to the OneBot WebSocket server; check its address, that it is running and the access token/);

  await assert.rejects(loginOneBot({ state, url: URL_, token: TOKEN, WebSocketImpl: bindingServer({ self: { user_id: 'x' } }).Socket,
    signal: AbortSignal.timeout(3000) }), /invalid login account/);

  // Nobody sends the code in time. A fake socket holds no handle, and
  // AbortSignal.timeout does not keep Node 22's event loop alive.
  const waiting = bindingServer();
  const expiry = new AbortController();
  setTimeout(() => expiry.abort(new DOMException('The operation timed out', 'TimeoutError')), 200);
  const timeout = await loginOneBot({ state, url: URL_, token: TOKEN, WebSocketImpl: waiting.Socket, signal: expiry.signal })
    .catch((e) => e);
  assert.equal(timeout.name, 'TimeoutError');
  assert.equal(waiting.opened[0].readyState, 3);

  // The connection drops while waiting for the code.
  const dropping = bindingServer();
  let shown = false;
  const dropped = loginOneBot({ state, url: URL_, token: TOKEN, WebSocketImpl: dropping.Socket, signal: AbortSignal.timeout(3000),
    onChallenge: () => { shown = true; } });
  await until(() => shown);
  dropping.opened[0].close(1006);
  await assert.rejects(dropped, /OneBot connection closed/);
  assert.equal(dropping.opened.length, 1);
  assert.deepEqual(state.account(), account);
});

const account = { selfId: SELF, selfName: 'Vika', ownerId: OWNER, activatedAt: 0 };
const T = 1_767_000_000;
const TOOL_ID = /^[A-Za-z0-9_.:@=+-]{1,256}$/;
const text = (value) => ({ type: 'text', data: { text: value } });
const at = (qq, extra = {}) => ({ type: 'at', data: { qq, ...extra } });
const group = (message, extra = {}) => ({
  post_type: 'message', message_type: 'group', sub_type: 'normal', self_id: Number(SELF), time: T,
  message_id: -1234567, group_id: 123456789, user_id: 30003,
  sender: { user_id: 30003, nickname: 'alice_nick', card: 'Alice' }, message, message_format: 'array', ...extra,
});
const direct = (message, extra = {}) => ({
  post_type: 'message', message_type: 'private', sub_type: 'friend', self_id: Number(SELF), time: T,
  message_id: 77, user_id: Number(OWNER), target_id: Number(OWNER),
  sender: { user_id: Number(OWNER), nickname: 'Owner' }, message, message_format: 'array', ...extra,
});
const normalize = (raw, names = new Map()) => normalizeMessage(raw, { account, instance: 'onebot', names });

test('onebot ids are stable, keep the signed message id and fit the tool id pattern', () => {
  assert.equal(groupChat('123456789'), 'group:123456789');
  assert.equal(privateChat(OWNER), `private:${OWNER}`);
  assert.deepEqual(parseChat('group:123456789'), { kind: 'group', id: '123456789' });
  assert.deepEqual(parseChat(`private:${OWNER}`), { kind: 'private', id: OWNER });
  for (const bad of ['group:0123', 'guild:1', 'group:', 'group:99999999999999999', null]) assert.equal(parseChat(bad), null);
  const id = messageId('group:123456789', '-1234567');
  assert.equal(id, 'ob:group:123456789:-1234567');
  assert.match(id, TOOL_ID);
  assert.deepEqual(parseMessageId(id), { chat: 'group:123456789', mid: '-1234567' });
  for (const bad of ['ob:group:123456789:-0', 'ob:group:123456789:007', 'ob:guild:1:2', 'sl:T1:C1:1.2', 'ob:group:1:x']) {
    assert.equal(parseMessageId(bad), null, bad);
  }
  assert.equal(cleanName(' A\nB\u0000 '), 'A B');
  assert.equal(cleanName(''), null);
});

test('onebot time is seconds or, from NapCat fallback, milliseconds', () => {
  assert.equal(eventTime({ time: T }), T * 1000);
  assert.equal(eventTime({ time: T * 1000 + 123 }), T * 1000 + 123);
  assert.equal(eventTime({ time: String(T) }), T * 1000);
  for (const bad of [0, -1, 1.5, 'x', null]) assert.equal(eventTime({ time: bad }), null);
  const seconds = normalize(group([text('hi')]));
  const millis = normalize(group([text('hi')], { time: T * 1000 + 999 }));
  assert.equal(seconds.envelope.occurred_at, T * 1000);
  assert.equal(millis.envelope.occurred_at, T * 1000 + 999);
  // The same message under either clock is the same event.
  assert.equal(seconds.envelope.id, `${SELF}:group:123456789:-1234567:${T}`);
  assert.equal(millis.envelope.id, seconds.envelope.id);
});

test('onebot group message renders mentions, quotes, media and markers into a canonical event', () => {
  const card = JSON.stringify({ app: 'com.tencent.miniapp', prompt: '[QQ小程序]哔哩哔哩',
    meta: { detail_1: { title: '哔哩哔哩', desc: 'A video', qqdocurl: 'https://b23.tv/abc' } } });
  const raw = group([
    { type: 'reply', data: { id: '-55' } },
    at(SELF), text(' look '), at('40004'), at('50005', { name: 'Carol' }), text(' and '), at('all'),
    { type: 'image', data: { file: 'ABC.png', sub_type: 0, url: 'https://multimedia.nt.qq.com.cn/download?appid=1&rkey=SECRET', file_size: '2048' } },
    { type: 'image', data: { file: 'sticker.gif', sub_type: 1, summary: '[动画表情]', url: 'https://x/download?rkey=SECRET2' } },
    { type: 'image', data: { file: 'face.gif', summary: '[狗头]', emoji_id: 'abc', url: 'https://gxh.vip.qq.com/raw300.gif' } },
    { type: 'mface', data: { summary: '[开心]', emoji_id: 'x', url: 'https://gxh.vip.qq.com/raw.gif' } },
    { type: 'file', data: { file: 'report.pdf', file_id: '/abc-uuid', file_size: 4096 } },
    { type: 'video', data: { file: 'opaque-code', url: 'https://video.qq.com/v?rkey=SECRET3' } },
    { type: 'forward', data: { id: '7349123456789' } },
    { type: 'json', data: { data: card } },
    { type: 'face', data: { id: '14' } }, { type: 'record', data: { file: 'a.amr' } },
    { type: 'dice', data: { result: 3 } }, { type: 'Weird Type', data: {} },
  ]);
  const event = normalize(raw, new Map([['40004', 'Bob']]));
  assert.equal(event.chat, 'group:123456789');
  assert.equal(event.mid, '-1234567');
  assert.equal(event.userId, '30003');
  assert.equal(event.id, 'ob:group:123456789:-1234567');
  assert.equal(event.self, false);
  assert.equal(event.atSelf, true);
  assert.equal(event.replyTo, 'ob:group:123456789:-55');
  assert.equal(event.text, '@Vika look @Bob@Carol and @全体成员[动画表情][狗头][开心][forwarded chat]'
    + '[card] [QQ小程序]哔哩哔哩 哔哩哔哩 https://b23.tv/abc[face][voice message][dice][unsupported]');
  assert.equal(event.bareText, event.text.slice('@Vika '.length));
  assert.deepEqual(event.files, [
    { id: '0', kind: 'image', name: 'ABC.png', file: 'ABC.png', fileId: null,
      url: 'https://multimedia.nt.qq.com.cn/download?appid=1&rkey=SECRET', size: 2048 },
    { id: '1', kind: 'file', name: 'report.pdf', file: 'report.pdf', fileId: '/abc-uuid', url: null, size: 4096 },
    { id: '2', kind: 'file', name: 'video.mp4', file: 'opaque-code', fileId: null, url: 'https://video.qq.com/v?rkey=SECRET3', size: null },
    { id: '3', kind: 'forward', name: 'forwarded-chat.txt', file: '7349123456789', fileId: null, url: null, size: null },
  ]);
  assert.deepEqual(event.people, [['30003', 'Alice'], ['40004', 'Bob'], ['50005', 'Carol']]);
  const envelope = event.envelope;
  assert.equal(envelope.connector, 'onebot');
  assert.deepEqual(envelope.subject, { type: 'conversation', id: 'group:123456789' });
  assert.deepEqual(envelope.actor, { id: '30003', type: 'user', name: 'Alice' });
  assert.deepEqual(envelope.conversation, { id: 'group:123456789', type: 'group', tenant_id: SELF, thread_id: null });
  assert.equal(envelope.message.type, 'file');
  assert.equal(envelope.message.parent_id, 'ob:group:123456789:-55');
  assert.deepEqual(envelope.message.mentions.map(({ id, name }) => [id, name]),
    [[SELF, 'Vika'], ['40004', 'Bob'], ['50005', 'Carol']]);
  assert.deepEqual(envelope.content.attachments, [
    { id: '0', type: 'image', name: 'ABC.png' }, { id: '1', type: 'file', name: 'report.pdf' },
    { id: '2', type: 'file', name: 'video.mp4' }, { id: '3', type: 'file', name: 'forwarded-chat.txt' },
  ]);
  assert.deepEqual(envelope.reply_route, { action: 'message.reply', input: { message_id: event.id } });
  // Media URLs carry download keys; they stay in the route and never reach the event.
  assert.doesNotMatch(JSON.stringify(envelope), /rkey|SECRET|multimedia|gxh\.vip/);
  assert.equal(normalizeEvent(envelope).envelope.id, envelope.id);
  assert.deepEqual([...namedUsers(raw)], [SELF, '40004', '50005']);
});

test('onebot sticker, image-only and Lagrange-shaped media', () => {
  const sticker = normalize(group([{ type: 'image', data: { file: 'a.gif', subType: 1, summary: '' } }]));
  assert.equal(sticker.text, '[sticker]');
  assert.deepEqual(sticker.files, []);
  assert.equal(sticker.envelope.message.type, 'text');
  // Lagrange: `file` is the URL, the name is `filename`, every picture's summary is "[图片]".
  const picture = normalize(group([{ type: 'image', data: { file: 'https://a/b?rkey=K', filename: 'p.jpg', url: 'https://a/b?rkey=K', summary: '[图片]', subType: 0 } }]));
  assert.equal(picture.text, '');
  assert.deepEqual(picture.files, [{ id: '0', kind: 'image', name: 'p.jpg', file: null, fileId: null, url: 'https://a/b?rkey=K', size: null }]);
  assert.equal(picture.envelope.message.type, 'image');
  const file = normalize(group([{ type: 'file', data: { file_name: 'x.zip', file_id: 'fid', url: 'file:///tmp/x.zip' } }]));
  assert.deepEqual(file.files, [{ id: '0', kind: 'file', name: 'x.zip', file: null, fileId: 'fid', url: null, size: null }]);
  // A file name is the sender's choice: one that looks like an address is never a download address.
  for (const type of ['file', 'video', 'image']) {
    const named = normalize(group([{ type, data: { file: 'http://169.254.169.254/latest/meta-data/', file_id: 'uuid-1' } }]));
    assert.equal(named.files[0].url, null, type);
  }
  const cardBad = normalize(group([{ type: 'json', data: { data: '{not json' } }]));
  assert.equal(cardBad.text, '[card]');
  const cardKey = normalize(group([{ type: 'json', data: { data: { prompt: 'p', meta: { news: { jumpUrl: 'https://x/y?rkey=1' } } } } }]));
  assert.equal(cardKey.text, '[card] p');
});

test('onebot detects the account own messages under each dialect', () => {
  // NapCat / LLOneBot: message_sent, the private peer in target_id.
  const sent = direct([text('done')], { post_type: 'message_sent', message_sent_type: 'self', user_id: Number(SELF),
    target_id: Number(OWNER), sender: { user_id: Number(SELF), nickname: 'VikaQQ' } });
  assert.equal(isSelf(sent, SELF), true);
  assert.equal(chatOf(sent, SELF), `private:${OWNER}`);
  const event = normalize(sent);
  assert.equal(event.self, true);
  assert.equal(event.chat, `private:${OWNER}`);
  assert.deepEqual(event.envelope.actor, { id: SELF, type: 'bot', name: 'Vika' });
  assert.equal(event.envelope.conversation.type, 'p2p');
  assert.deepEqual(event.people, []);
  // Lagrange: an ordinary message whose author is the account.
  const lagrange = group([text('gs status')], { user_id: Number(SELF), sender: { user_id: Number(SELF), nickname: 'VikaQQ' } });
  assert.equal(isSelf(lagrange, SELF), true);
  assert.equal(normalize(lagrange).envelope.actor.type, 'bot');
  const incoming = direct([text('hello')]);
  assert.equal(isSelf(incoming, SELF), false);
  assert.equal(chatOf(incoming, SELF), `private:${OWNER}`);
  assert.equal(chatOf({ message_type: 'guild', user_id: 1 }, SELF), null);
});

test('onebot drops what is not a fresh message of this account and rejects broken identity', () => {
  assert.equal(normalize({ post_type: 'notice', notice_type: 'group_recall' }), null);
  assert.equal(normalize(direct([text('hi')], { sub_type: 'group' })), null, 'temporary session');
  assert.equal(normalize(group([text('hi')], { sub_type: 'anonymous' })), null);
  assert.equal(normalize(group([text('hi')], { self_id: 99999 })), null, 'another account');
  assert.equal(normalizeMessage(group([text('hi')]), { account: { ...account, activatedAt: (T + 1) * 1000 } }), null);
  assert.equal(normalize(group([{ type: 'reply', data: { id: '5' } }])), null, 'nothing to show');
  assert.equal(normalize(group([text('   ')])), null);
  assert.throws(() => normalize(group([text('hi')], { message_id: 1.5 })), /Invalid OneBot message identity/);
  assert.throws(() => normalize(group([text('hi')], { group_id: 'abc' })), /Invalid OneBot message identity/);
  assert.throws(() => normalize(group([text('hi')], { time: 0 })), /Invalid OneBot message identity/);
});

test('onebot bounds text and reads a CQ-code string without leaking its media URLs', () => {
  const long = normalize(group([text('x'.repeat(20_000))]));
  assert.equal(long.text.length, 16_000);
  const cq = normalize(group(`[CQ:reply,id=9][CQ:at,qq=${SELF}] gs &#91;1&#93;[CQ:image,file=a.png,url=https://h/d?rkey=SECRET&amp;x=1]`));
  assert.equal(cq.text, '@Vika gs [1]');
  assert.equal(cq.bareText, 'gs [1]');
  assert.equal(cq.atSelf, true);
  assert.equal(cq.replyTo, 'ob:group:123456789:9');
  assert.equal(cq.files[0].url, 'https://h/d?rkey=SECRET&x=1');
  assert.doesNotMatch(JSON.stringify(cq.envelope), /rkey/);
});

test('onebot bare text skips the quote and @self that address the account', () => {
  const event = normalize(group([{ type: 'reply', data: { id: '3' } }, at(SELF), text(' '), at(SELF), text('  gs 签到')]));
  assert.equal(event.bareText, 'gs 签到');
  assert.equal(normalize(group([at('40004'), text(' gs')])).bareText, '@40004 gs');
  assert.equal(normalize(group([text('hey '), at(SELF)])).bareText, 'hey @Vika');
  // Spoken text leaves out every @, so a name inside someone's @ is not something typed.
  const named = normalize(group([at('40004'), text(' hi '), at(SELF), text(' vika'), at('all')]), new Map([['40004', 'Vikander']]));
  assert.equal(named.text, '@Vikander hi @Vika vika@全体成员');
  assert.equal(named.spokenText, 'hi  vika');
});

test('onebot keeps at most 100 mentions, its own among them, and still shows every @', () => {
  const ids = Array.from({ length: 149 }, (_, i) => String(30_000 + i));
  const event = normalize(group([...ids.map((id) => at(id)), at(SELF)]));
  assert.equal(event.text, `${ids.map((id) => `@${id}`).join('')}@Vika`);
  const { mentions } = event.envelope.message;
  assert.equal(mentions.length, 100);
  assert.deepEqual(mentions.slice(0, 2).map(({ id, name }) => [id, name]), [[SELF, 'Vika'], ['30000', null]]);
  assert.equal(mentionTarget(normalizeEvent(event.envelope).mentions, SELF), 'mixed');
  // Up to the limit the order is the message's.
  const few = normalize(group([at('40004'), at(SELF)])).envelope.message.mentions;
  assert.deepEqual(few.map((item) => item.id), ['40004', SELF]);
});

test('onebot history keeps own and old messages and validates the chat', () => {
  const own = group([text('earlier answer')], { post_type: 'message_sent', user_id: Number(SELF), sender: { user_id: Number(SELF), nickname: 'VikaQQ' } });
  const old = { ...account, activatedAt: Date.now() };
  const { message, route } = historyMessage(own, { account: old, chat: 'group:123456789' });
  assert.deepEqual(message, {
    message_id: 'ob:group:123456789:-1234567', chat_id: 'group:123456789',
    conversation: { id: 'group:123456789', tenant_id: SELF, thread_id: null }, create_time: String(T * 1000), msg_type: 'text',
    sender: { id: SELF, name: 'Vika', type: 'bot' }, content: 'earlier answer', attachments: [],
  });
  assert.deepEqual(route, { chat: 'group:123456789', mid: '-1234567', files: [] });
  const withFile = historyMessage(group([{ type: 'file', data: { file: 'a.txt', file_id: 'f' } }]), { account, chat: 'group:123456789' });
  assert.equal(withFile.message.content, '[attachment type=file id=0 name="a.txt"]');
  assert.equal(withFile.message.sender.name, 'Alice');
  // Private history carries no target_id: either party of the chat is valid.
  const mine = direct([text('ok')], { user_id: Number(SELF), target_id: undefined });
  assert.equal(historyMessage(mine, { account, chat: `private:${OWNER}` }).message.sender.type, 'bot');
  assert.throws(() => historyMessage(group([text('x')]), { account, chat: 'group:1' }), /history message identity/);
  assert.throws(() => historyMessage(direct([text('x')], { user_id: 333 }), { account, chat: `private:${OWNER}` }), /history message identity/);
  assert.equal(historyMessage(group([]), { account, chat: 'group:123456789' }), null);
});

test('onebot plain text drops Markdown clutter and keeps code, lists and links', () => {
  assert.equal(plainText('# Title #\n## Learn C#\n**bold** and __a phrase__ but __init__ stays'), 'Title\nLearn C#\nbold and a phrase but __init__ stays');
  assert.equal(plainText('see [docs](https://x.y/a?b=1), [https://a.b](https://a.b) and ![cat](https://c.d/p.png)'),
    'see docs (https://x.y/a?b=1), https://a.b and cat (https://c.d/p.png)');
  assert.equal(plainText('run `npm **test**` now'), 'run npm **test** now');
  assert.equal(plainText('- one\n- two\n1. three'), '- one\n- two\n1. three');
  assert.equal(plainText('```js\nconst a = **b**;\n# not a heading\n```\nafter'), 'const a = **b**;\n# not a heading\nafter');
  assert.equal(plainText('  [text](javascript:alert(1))  '), '[text](javascript:alert(1))');
});

test('onebot outbound segments place resolved mentions and never @everyone', () => {
  assert.deepEqual(outboundSegments('hello'), [text('hello')]);
  assert.deepEqual(outboundSegments('hi @Alice and @Al, ok', [{ id: '1', name: 'Al' }, { id: '2', name: 'Alice' }, { id: '3', name: 'Bob' }]), [
    at('3'), text(' hi '), at('2'), text(' and '), at('1'), text(', ok'),
  ]);
  assert.deepEqual(outboundSegments('@Alice_Smith hi @Alice Smith', [{ id: '2', name: 'Alice_Smith' }]), [
    at('2'), text(' hi @Alice Smith'),
  ]);
  assert.deepEqual(outboundSegments('ping @Ann Lee', [{ id: '4', name: 'Ann_Lee' }]), [text('ping '), at('4')]);
  // The label the model saw in from= (sanitizeMemberName) is replaced in place too.
  assert.deepEqual(outboundSegments('thanks @Alice_Smith!', [{ id: '2', name: 'Alice Smith' }]), [text('thanks '), at('2'), text('!')]);
  assert.deepEqual(outboundSegments('问 @技术_张三 吧', [{ id: '5', name: '[技术]张三' }]), [text('问 '), at('5'), text(' 吧')]);
  assert.deepEqual(outboundSegments('[CQ:at,qq=all] @全体成员'), [text('[CQ:at,qq=all] @全体成员')]);
  assert.throws(() => outboundSegments('x', [{ id: 'all', name: '全体成员' }]), /QQ number/);
  assert.throws(() => outboundSegments('   '), /1–4000 characters/);
  assert.doesNotThrow(() => outboundSegments('字'.repeat(TEXT_LIMIT)));
  assert.throws(() => outboundSegments('字'.repeat(TEXT_LIMIT + 1)), /send it as a file/);
});

test('onebot policy validates the owner file strictly', () => {
  const policy = parsePolicy(JSON.stringify({ groups: { 123456789: 'ambient', 987654321: 'mention' },
    commandPrefixes: ['gs', '/rss', 'gs'], names: ['Vika'], settleMs: 1500 }));
  assert.deepEqual([...policy.groups], [['123456789', 'ambient'], ['987654321', 'mention']]);
  assert.deepEqual(policy.commandPrefixes, ['gs', '/rss']);
  assert.deepEqual(policy.names, ['Vika']);
  assert.equal(policy.settleMs, 1500);
  assert.ok(Object.isFrozen(policy) && Object.isFrozen(policy.commandPrefixes));
  assert.equal(EMPTY_POLICY.groups.size, 0);
  assert.equal(EMPTY_POLICY.settleMs, 3000);
  assert.equal(EMPTY_POLICY.private, 'owner');
  assert.equal(parsePolicy('{"private": "friends"}').private, 'friends');
  const invalid = [
    ['{"private": "all"}', /private must be "owner" or "friends"/],
    ['{"private": null}', /private must be "owner" or "friends"/],
    ['{"group": {}}', /key "group" is unknown/],
    ['{"groups": {"1": "all"}}', /"ambient" or "mention"/],
    ['{"groups": {"01": "ambient"}}', /group number/],
    ['{"groups": []}', /groups must be an object/],
    ['{"groups": null}', /groups must be an object/],
    ['{"groups": {"1": "ambient", "1": "mention"}}', /duplicate JSON key: 1/],
    ['[]', /must be a JSON object/],
    ['{"commandPrefixes": "gs"}', /at most 50/],
    [JSON.stringify({ commandPrefixes: Array.from({ length: 51 }, (_, i) => `p${i}`) }), /at most 50/],
    ['{"commandPrefixes": [""]}', /1–32 characters/],
    ['{"commandPrefixes": [" gs"]}', /not starting with a space/],
    [JSON.stringify({ names: ['x'.repeat(33)] }), /1–32 characters/],
    [JSON.stringify({ names: Array.from({ length: 11 }, (_, i) => `n${i}`) }), /at most 10/],
    ['{"settleMs": 60001}', /0 to 60000/],
    ['{"settleMs": 1.5}', /0 to 60000/],
    ['{"settleMs": null}', /0 to 60000/],
  ];
  for (const [input, error] of invalid) assert.throws(() => parsePolicy(input), error, input);
  // A syntax error does not echo the file back.
  assert.throws(() => parsePolicy('{"groups": {"secret-token-here'), (error) => /not valid JSON/.test(error.message)
    && !error.message.includes('secret'));
});

test('onebot policy file reloads on change and keeps the last good policy', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-onebot-policy-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const warnings = [];
  const file = new PolicyFile(dataDir, { warn: (event, fields) => warnings.push([event, fields]) });
  assert.equal(file.path, path.join(dataDir, 'onebot', 'config.json'));
  assert.equal(file.current(), EMPTY_POLICY, 'missing file is the empty policy');
  fs.mkdirSync(path.dirname(file.path));
  let stamp = 1_700_000_000;
  const write = (content) => {
    fs.writeFileSync(file.path, content);
    stamp += 10;
    fs.utimesSync(file.path, stamp, stamp);
  };
  write('{"groups": {"1": "ambient"}}');
  const first = file.current();
  assert.equal(groupMode(first, 'group:1'), 'ambient');
  assert.equal(file.current(), first, 'unchanged file is not parsed again');
  write('{"groups": {"1": "mention"}}');
  assert.equal(groupMode(file.current(), 'group:1'), 'mention');
  const good = file.current();
  write('{"groups": {"1": "mentoin"}}');
  assert.equal(file.current(), good);
  assert.equal(file.current(), good);
  assert.equal(warnings.length, 1, 'one warning per changed file');
  assert.equal(warnings[0][0], 'onebot_policy_invalid');
  write('{"groups": {"2": "ambient"}}');
  assert.equal(groupMode(file.current(), 'group:2'), 'ambient');
  // A fresh reader (a restart, preflight) with an invalid file is an error that names the file.
  write('{"groups": {"1": "x"}, "token": "hunter2"}');
  const fresh = new PolicyFile(dataDir);
  assert.throws(() => fresh.current(), (error) => error.message.includes(file.path) && !error.message.includes('hunter2'));
  write('{"groups": {"3": "mention"}}');
  assert.equal(groupMode(fresh.current(), 'group:3'), 'mention');
  fs.rmSync(file.path);
  assert.equal(fresh.current(), EMPTY_POLICY);
  assert.throws(() => new PolicyFile('relative'), /absolute/);
});

test('onebot admission covers the owner chat, or every friend with private friends, and listed groups only', () => {
  const friends = parsePolicy('{"groups": {"123456789": "ambient"}, "private": "friends"}');
  assert.equal(admitted(friends, account, 'private:30003'), true);
  assert.equal(admitted(friends, account, `private:${OWNER}`), true);
  assert.equal(admitted(friends, account, 'group:111'), false);
  const policy = parsePolicy('{"groups": {"123456789": "ambient", "987654321": "mention"}}');
  assert.equal(admitted(policy, account, `private:${OWNER}`), true);
  assert.equal(admitted(policy, account, 'private:30003'), false);
  assert.equal(admitted(policy, account, 'group:123456789'), true);
  assert.equal(admitted(policy, account, 'group:987654321'), true);
  assert.equal(admitted(policy, account, 'group:111'), false);
  assert.equal(admitted(policy, null, `private:${OWNER}`), false);
  assert.equal(admitted(policy, account, 'garbage'), false);
  assert.equal(groupMode(policy, 'group:111'), null);
  assert.equal(groupMode(policy, `private:${OWNER}`), null);
});

test('onebot classification follows every row of the table', () => {
  const policy = parsePolicy('{"groups": {"1": "ambient", "2": "mention"}, "commandPrefixes": ["gs", "/rss"], "names": ["Vika"]}');
  // `spoken` is the text without @mentions; unless a row says otherwise there are none.
  const line = (fields) => classify({ kind: 'group', chat: 'group:1', self: false, own: false, fromOwner: false,
    text: 'hello', atSelf: false, replyToOwn: false, ...fields, spoken: fields.spoken ?? fields.text ?? 'hello' }, policy);
  const owner = (fields) => line({ kind: 'private', chat: `private:${OWNER}`, fromOwner: true, ...fields });
  // self and own: the echo of what the agent sent.
  assert.equal(line({ self: true, own: true }), 'drop');
  assert.equal(owner({ self: true, own: true, fromOwner: false }), 'drop');
  // self, not own: another client on the account. Context in a group, never shown in a private chat,
  // where it would cost a turn of its own and a leading / would run as a daemon command.
  assert.equal(line({ self: true, text: 'gs ok', atSelf: true }), 'context');
  assert.equal(line({ self: true, text: '/new' }), 'context');
  assert.equal(line({ chat: 'group:2', self: true }), 'context');
  assert.equal(owner({ self: true, fromOwner: false }), 'drop');
  assert.equal(owner({ self: true, fromOwner: false, text: '/new' }), 'drop');
  assert.equal(owner({ self: true, fromOwner: false, text: '  /model x' }), 'drop');
  // private from the owner, with and without a command prefix.
  assert.equal(owner({ text: 'gs bind 123456' }), 'drop');
  assert.equal(owner({ text: '  /rss add x' }), 'drop');
  assert.equal(owner({ text: 'hello' }), 'direct');
  assert.equal(owner({ text: 'GS is case-sensitive' }), 'direct');
  assert.equal(owner({ fromOwner: false }), 'drop');
  // A leading / is a daemon command in the Core: the owner's goes through.
  assert.equal(owner({ text: '/new' }), 'direct');
  // private from a friend under "private": "friends": like the owner's, but never a / command.
  const friends = parsePolicy('{"private": "friends", "commandPrefixes": ["gs"]}');
  const friend = (fields) => classify({ kind: 'private', chat: 'private:30003', self: false, own: false, fromOwner: false,
    text: 'hello', atSelf: false, replyToOwn: false, ...fields }, friends);
  assert.equal(friend({}), 'direct');
  assert.equal(friend({ self: true, text: '/new' }), 'drop');
  assert.equal(friend({ self: true, text: 'see you' }), 'drop');
  assert.equal(friend({ text: 'gs bind' }), 'drop');
  assert.equal(friend({ text: '/new' }), 'drop');
  assert.equal(friend({ text: ' /model x' }), 'drop');
  assert.equal(friend({ text: 'a/b' }), 'direct');
  assert.equal(friend({ fromOwner: true, text: '/new' }), 'direct');
  // group with a command prefix, even when it also addresses the agent.
  assert.equal(line({ text: 'gs 签到', atSelf: true }), 'context');
  assert.equal(line({ chat: 'group:2', text: '/rss list' }), 'context');
  // group addressed to the agent.
  assert.equal(line({ atSelf: true }), 'direct');
  assert.equal(line({ replyToOwn: true }), 'direct');
  assert.equal(line({ text: 'what does vika think?' }), 'direct');
  assert.equal(line({ chat: 'group:2', atSelf: true }), 'direct');
  // A name inside someone else's @ does not address the agent; the same name typed does.
  assert.equal(line({ chat: 'group:2', text: '@Vikander hi', spoken: 'hi' }), 'context');
  assert.equal(line({ chat: 'group:2', text: '@Vikander ask vika', spoken: 'ask vika' }), 'direct');
  // unaddressed group lines by mode.
  assert.equal(line({}), 'ambient');
  assert.equal(line({ chat: 'group:2' }), 'context');
  assert.equal(line({ chat: 'group:3' }), 'drop', 'not a connected group');
  // A full-text line still classifies: only the typed start counts as a command.
  assert.equal(line({ text: 'I said gs yesterday' }), 'ambient');
});

// A clock and timer table the gate runs on, advanced by hand.
function fakeTime(start = 1_000_000) {
  let now = start;
  let next = 0;
  const timers = new Map();
  const cleared = [];
  return {
    timers, cleared,
    now: () => now,
    setTimer: (fn, ms) => { timers.set(++next, { at: now + ms, fn }); return next; },
    clearTimer: (id) => { cleared.push(id); timers.delete(id); },
    advance(ms) {
      const end = now + ms;
      for (;;) {
        const [id, timer] = [...timers].sort((a, b) => a[1].at - b[1].at)[0] ?? [];
        if (!timer || timer.at > end) break;
        timers.delete(id);
        now = timer.at;
        timer.fn();
      }
      now = end;
    },
  };
}

function gate(time, { settle = 3000, onRelease = () => {} } = {}) {
  const released = [];
  const lines = new Map();
  const value = { settle };
  const instance = new ReleaseGate({
    settleMs: () => value.settle, now: time.now, setTimer: time.setTimer, clearTimer: time.clearTimer,
    release: (chat) => {
      released.push([chat, time.now(), lines.get(chat) ?? []]);
      lines.delete(chat);
      onRelease(chat);
    },
  });
  // Staging as the client does it: the line goes to state, then to the gate.
  const stage = (chat, cls, label = cls) => {
    lines.set(chat, [...(lines.get(chat) ?? []), label]);
    instance.staged(chat, cls, time.now());
  };
  return { gate: instance, released, stage, value };
}

test('onebot gate releases a direct line at once and waits for quiet on ambient lines', () => {
  const time = fakeTime();
  const { released, stage } = gate(time);
  const t0 = time.now();
  stage('group:1', 'direct', 'd1');
  assert.deepEqual(released, [['group:1', t0, ['d1']]]);
  assert.equal(time.timers.size, 0);
  const other = gate(time);
  other.stage('group:2', 'ambient', 'a1');
  time.advance(2000);
  other.stage('group:2', 'ambient', 'a2');
  time.advance(2999);
  assert.deepEqual(other.released, []);
  time.advance(1);
  assert.deepEqual(other.released, [['group:2', t0 + 5000, ['a1', 'a2']]]);
});

test('onebot gate: context never releases alone, extends the wait and rides the next release in order', () => {
  const time = fakeTime();
  const { released, stage } = gate(time);
  const t0 = time.now();
  stage('group:1', 'context', 'c1');
  time.advance(10 * 60_000);
  assert.deepEqual(released, []);
  assert.equal(time.timers.size, 0, 'no timer for context alone');
  const t1 = time.now();
  stage('group:1', 'ambient', 'a1');
  time.advance(2500);
  stage('group:1', 'context', 'c2');
  time.advance(2999);
  assert.deepEqual(released, [], 'a context line arriving while ambient waits is activity');
  time.advance(1);
  assert.deepEqual(released, [['group:1', t1 + 5500, ['c1', 'a1', 'c2']]]);
  stage('group:1', 'context', 'c3');
  stage('group:1', 'direct', 'd1');
  assert.deepEqual(released.at(-1), ['group:1', t1 + 5500, ['c3', 'd1']]);
  assert.ok(t0 < t1);
});

test('onebot gate caps the wait at MAX_HOLD from the first waiting ambient line', () => {
  const time = fakeTime();
  const { released, stage } = gate(time);
  const t0 = time.now();
  stage('group:1', 'ambient');
  for (let elapsed = 2000; elapsed < MAX_HOLD_MS + 10_000; elapsed += 2000) {
    time.advance(2000);
    if (released.length) break;
    stage('group:1', elapsed % 4000 ? 'context' : 'ambient');
  }
  assert.equal(released.length, 1);
  assert.equal(released[0][1], t0 + MAX_HOLD_MS);
  // The next ambient line waits out the grace of that release, not a cap
  // left over from the lines already delivered.
  stage('group:1', 'ambient');
  time.advance(released[0][1] + TURN_GRACE_MS - 1 - time.now());
  assert.equal(released.length, 1);
  time.advance(1);
  assert.equal(released.length, 2);
  assert.equal(released[1][1], t0 + MAX_HOLD_MS + TURN_GRACE_MS);
  // Under endless chatter the cap counts from the first line waiting since then.
  const t1 = time.now() + 1000;
  for (let elapsed = 0; elapsed < MAX_HOLD_MS + 10_000 && released.length === 2; elapsed += 1000) {
    time.advance(1000);
    if (released.length === 2) stage('group:1', 'ambient');
  }
  assert.equal(released[2][1], t1 + MAX_HOLD_MS);
});

test('onebot gate holds ambient lines through the grace after a release until the agent sends', () => {
  const time = fakeTime();
  const { gate: g, released, stage } = gate(time);
  const t0 = time.now();
  stage('group:1', 'direct');
  time.advance(1000);
  stage('group:1', 'ambient');
  time.advance(TURN_GRACE_MS - 1001);
  assert.equal(released.length, 1, 'still inside the grace');
  time.advance(1);
  assert.equal(released.length, 2);
  assert.equal(released[1][1], t0 + TURN_GRACE_MS);

  // The agent answers: the grace ends early, but the chat must then be quiet
  // for settleMs after that send.
  const t1 = time.now();
  time.advance(1000);
  stage('group:1', 'ambient');
  time.advance(4000);
  g.sent('group:1');
  const sentAt = time.now();
  time.advance(2999);
  assert.equal(released.length, 2);
  time.advance(1);
  assert.deepEqual(released.map(([, when]) => when), [t0, t0 + TURN_GRACE_MS, sentAt + 3000]);
  assert.ok(sentAt + 3000 < t1 + TURN_GRACE_MS);

  // Only the first send after a release ends its grace; a later release opens a new one.
  time.advance(500);
  stage('group:1', 'ambient');
  const t2 = time.now();
  time.advance(5000);
  assert.equal(released.length, 3, 'a new grace started with the last release');
  time.advance(TURN_GRACE_MS);
  assert.equal(released.length, 4);
  assert.equal(released[3][1], t2 - 500 + TURN_GRACE_MS);
});

test('onebot gate: a send written before the latest release does not end its grace', () => {
  const time = fakeTime();
  const { gate: g, released, stage } = gate(time);
  stage('group:1', 'direct');
  const answering = time.now();
  time.advance(1000);
  stage('group:1', 'direct');
  const second = time.now();
  stage('group:1', 'ambient');
  // The answer to the first batch comes back after the second release.
  time.advance(500);
  g.sent('group:1', answering);
  time.advance(3000);
  assert.equal(released.length, 2, 'the second release is still unanswered');
  // A send written since then answers it.
  g.sent('group:1', time.now());
  time.advance(2999);
  assert.equal(released.length, 2);
  time.advance(1);
  assert.equal(released.length, 3);
  assert.ok(released[2][1] < second + TURN_GRACE_MS);
});

test('onebot gate runs on a monotonic clock, so a wall clock stepped back holds nothing', async () => {
  const released = [];
  const wall = Date.now;
  let back = 0;
  Date.now = () => wall() - back;
  const g = new ReleaseGate({ settleMs: () => 20, release: (chat) => released.push(chat) });
  try {
    g.staged('group:1', 'ambient');
    back = 3_600_000;
    await delay(100);
    assert.deepEqual(released, ['group:1']);
  } finally {
    Date.now = wall;
    g.stop();
  }
});

test('onebot gate: sends while nothing waits release nothing and still count as activity', () => {
  const time = fakeTime();
  const { gate: g, released, stage } = gate(time);
  g.sent('group:1');
  assert.equal(time.timers.size, 0);
  time.advance(1000);
  stage('group:1', 'ambient');
  time.advance(2999);
  assert.equal(released.length, 0);
  time.advance(1);
  assert.equal(released.length, 1);
});

test('onebot gate keeps one timer for the nearest due chat and reads settleMs live', () => {
  const time = fakeTime();
  const { released, stage, value } = gate(time);
  const t0 = time.now();
  value.settle = 10_000;
  stage('group:1', 'ambient');
  assert.equal(time.timers.size, 1);
  value.settle = 2000;
  stage('group:2', 'ambient');
  assert.equal(time.timers.size, 1);
  assert.equal([...time.timers.values()][0].at, t0 + 2000);
  time.advance(2000);
  // group:1 was staged under 10 s, but the policy now says 2 s.
  assert.deepEqual(released.map(([chat, when]) => [chat, when]), [['group:1', t0 + 2000], ['group:2', t0 + 2000]]);
  assert.equal(time.timers.size, 0);
});

test('onebot gate resumes waiting chats after a restart and stop cancels everything', () => {
  const time = fakeTime();
  const { gate: g, released, stage } = gate(time);
  g.resume('group:1', new Set(['context']));
  assert.deepEqual(released, []);
  g.resume('group:2', new Set(['context', 'ambient']));
  g.resume(`private:${OWNER}`, new Set(['direct']));
  assert.deepEqual(released.map(([chat]) => chat), ['group:2', `private:${OWNER}`]);
  stage('group:1', 'ambient');
  assert.equal(time.timers.size, 1);
  g.stop();
  assert.equal(time.timers.size, 0);
  assert.equal(time.cleared.length, 1);
  time.advance(MAX_HOLD_MS * 2);
  assert.equal(released.length, 2);
});

test('onebot gate does not re-enter a release that stages more lines or stops the gate', () => {
  const time = fakeTime();
  let depth = 0;
  let maxDepth = 0;
  let restaged = false;
  const harness = gate(time, {
    onRelease: (chat) => {
      depth++;
      maxDepth = Math.max(maxDepth, depth);
      if (chat === 'group:1' && !restaged) {
        restaged = true;
        harness.stage('group:1', 'direct', 'late');
        harness.stage('group:2', 'ambient', 'other');
      }
      depth--;
    },
  });
  harness.stage('group:1', 'direct', 'first');
  assert.equal(maxDepth, 1);
  assert.deepEqual(harness.released.map(([chat, , lines]) => [chat, lines]), [['group:1', ['first']], ['group:1', ['late']]]);
  assert.equal(time.timers.size, 1, 'the other chat waits on the timer');
  time.advance(3000);
  assert.deepEqual(harness.released.at(-1).slice(0, 1), ['group:2']);

  const stopping = gate(time, { onRelease: () => stopping.gate.stop() });
  stopping.gate.resume('group:1', new Set(['ambient']));
  stopping.gate.resume('group:2', new Set(['ambient']));
  assert.equal(stopping.released.length, 2, 'resume releases each chat as it is resumed');
  stopping.stage('group:3', 'ambient');
  stopping.stage('group:4', 'direct');
  assert.equal(stopping.released.length, 3);
  time.advance(MAX_HOLD_MS);
  assert.equal(stopping.released.length, 3, 'stop inside a release cancels the waiting chat');
});

test('onebot gate exports its bounds', () => {
  assert.equal(MAX_HOLD_MS, 45_000);
  assert.equal(TURN_GRACE_MS, 20_000);
  assert.equal(CONTEXT_LINES, 30);
  assert.equal(CONTEXT_AGE_MS, 6 * 60 * 60 * 1000);
});

const quietLogger = { info() {}, warn() {}, error() {}, debug() {}, child() { return this; } };
const GROUP = '123456789';
const QUIET_GROUP = '987654321';
const POLICY = { groups: { [GROUP]: 'ambient', [QUIET_GROUP]: 'mention' }, commandPrefixes: ['gs'], names: ['Vika'] };
const fresh = () => Math.floor(Date.now() / 1000);
const PNG = Buffer.from('89504e470d0a1a0a0000000d49484452', 'hex');

// A client on fake sockets. Actions answer from `actions` (the sockets()
// helper adds the echo); an action without a handler is never answered.
async function client(t, { actions = {}, policy = POLICY, time = null, fetchImpl, sendTimeoutMs = 5000, paceMs = 0 } = {}) {
  const { dir, state } = fixture(t);
  state.saveAccount({ url: URL_, token: TOKEN, selfId: SELF, selfName: 'Vika', ownerId: OWNER, activatedAt: 0 });
  state.close();
  fs.writeFileSync(path.join(dir, 'onebot', 'config.json'), JSON.stringify(policy));
  let mid = 500;
  const ws = sockets({ actions: {
    get_login_info: () => ok({ user_id: Number(SELF), nickname: 'Vika' }),
    send_group_msg: () => ok({ message_id: ++mid }),
    send_private_msg: () => ok({ message_id: ++mid }),
    ...actions,
  } });
  const c = new OneBotClient({ dataDir: dir, retentionMs: 86400_000, instance: 'onebot', sendTimeoutMs }, null, quietLogger,
    { WebSocketImpl: ws.Socket, fetchImpl, paceMs, ...(time ? { now: time.now, setTimer: time.setTimer, clearTimer: time.clearTimer } : {}) });
  t.after(() => c.stop());
  const checked = await c.preflight();
  const lines = [];
  const exits = [];
  const start = () => c.startSubscription((line) => lines.push(JSON.parse(line)), (exit) => exits.push(exit));
  // Delivers a frame and waits until the client has staged it.
  const push = async (raw) => { ws.opened.at(-1).frame(raw); await c.inbound; };
  const sent = (action) => ws.opened.flatMap((socket) => socket.sent).filter((request) => request.action === action);
  return { c, ws, dir, checked, lines, exits, start, push, sent };
}

test('client preflight reports the account, owner and self, and refuses a changed account, no login or a bad policy', async (t) => {
  const { c, checked, sent } = await client(t);
  assert.deepEqual(checked, { version: ONEBOT_VERSION, self: { id: SELF }, owner: { id: OWNER, target: { chat_id: `private:${OWNER}` } } });
  assert.equal(c.subscriptionRunning, false);
  assert.equal(sent('get_login_info').length, 1);

  await assert.rejects(client(t, { actions: { get_login_info: () => ok({ user_id: 99999, nickname: 'Other' }) } }),
    /OneBot account changed; authorize again/);
  await assert.rejects(client(t, { policy: { groups: { 1: 'loud' }, token: 'hunter2' } }),
    (error) => error.message.endsWith(path.join('onebot', 'config.json')) && !error.message.includes('hunter2'));
  const { dir, state } = fixture(t);
  state.close();
  const empty = new OneBotClient({ dataDir: dir, retentionMs: 1 }, null, quietLogger, { WebSocketImpl: sockets().Socket });
  t.after(() => empty.stop());
  await assert.rejects(empty.preflight(), /OneBot is not connected; run vika login onebot <instance> first/);

  // A rejected token while running fails the worker; before start it fails start.
  const rejected = await client(t);
  rejected.start();
  rejected.ws.opened[0].frame({ status: 'failed', retcode: 1403, data: null, message: 'token验证失败', echo: null });
  await until(() => rejected.exits.length === 1);
  assert.match(rejected.exits[0].stderr, /rejected the access token/);
  assert.equal(rejected.c.subscriptionRunning, false);
});

test('client drops its own echo even when message_sent precedes the send answer, and shows another client as sender=bot context', async (t) => {
  const time = clock();
  let echo = null;
  const { c, dir, lines, start, push, sent } = await client(t, { time, actions: {
    // The echo goes out well before the answer, as NapCat may do.
    send_group_msg: async (_params, socket) => {
      socket.frame(echo);
      await delay(20);
      return ok({ message_id: -9001 });
    },
  } });
  start();
  echo = group([text('on it')], { post_type: 'message_sent', message_id: -9001, user_id: Number(SELF), time: fresh(),
    sender: { user_id: Number(SELF), nickname: 'Vika' } });
  const result = await c.sendMessage({ chat_id: `group:${GROUP}`, content: { type: 'markdown', text: '**on it**' } });
  assert.deepEqual(result, { message_id: `ob:group:${GROUP}:-9001`, chat_id: `group:${GROUP}` });
  assert.deepEqual(sent('send_group_msg')[0].params, { group_id: Number(GROUP), message: [text('on it')] });
  await c.inbound;
  assert.deepEqual(c.state.pendingChats(), [], 'the echo is not staged');

  // Another program on the account, then a person: one batch once the chat is quiet.
  await push(group([text('gs 签到成功')], { post_type: 'message_sent', message_id: 31, user_id: Number(SELF), time: fresh(),
    sender: { user_id: Number(SELF), nickname: 'Vika' } }));
  await push(group([text('nice')], { message_id: 32, time: fresh() }));
  assert.equal(lines.length, 0);
  time.advance(2999);
  assert.equal(lines.length, 0);
  time.advance(1);
  assert.deepEqual(lines.map((line) => [line.actor.type, line.content.text]), [['bot', 'gs 签到成功'], ['user', 'nice']]);
  assert.deepEqual(lines[0].actor, { id: SELF, type: 'bot', name: 'Vika' });
  assert.match(formatInboundLine(normalizeEvent(lines[0])), / from=Vika sender=bot\] gs 签到成功$/);
  assert.deepEqual(c.state.pendingChats(), []);

  // After a release the chat gets time to be answered; the agent's send ends it early.
  await push(group([text('more')], { message_id: 30, time: fresh() }));
  time.advance(3000);
  assert.equal(lines.length, 2);
  await c.sendMessage({ chat_id: `group:${GROUP}`, content: { type: 'markdown', text: 'ok' } });
  time.advance(2999);
  assert.equal(lines.length, 2);
  time.advance(1);
  assert.equal(lines.at(-1).content.text, 'more');

  // An @, a quote of an own message or a name releases at once; the owner's private line too.
  await push(group([{ type: 'reply', data: { id: '-9001' } }, text('why?')], { message_id: 33, time: fresh() }));
  assert.equal(lines.at(-1).content.text, 'why?');
  await push(direct([text('hello')], { message_id: 34, time: fresh() }));
  assert.equal(lines.at(-1).conversation.id, `private:${OWNER}`);
  // A line in a mention group waits for the next release there.
  await push(group([text('chatter')], { group_id: Number(QUIET_GROUP), message_id: 35, time: fresh() }));
  time.advance(60_000);
  assert.equal(lines.length, 5);
  await push(group([text('Vika, ping')], { group_id: Number(QUIET_GROUP), message_id: 36, time: fresh() }));
  assert.deepEqual(lines.slice(5).map((line) => line.content.text), ['chatter', 'Vika, ping']);
  // Lines to another program on the account stay out of the owner's chat.
  await push(direct([text('gs bind 123456')], { message_id: 37, time: fresh() }));
  assert.equal(lines.length, 7);

  // Paused, nothing is emitted, lines are staged and released on the next
  // start; a group the policy no longer lists loses its waiting lines.
  await c.stopSubscription();
  await push(group([text('later')], { message_id: 38, time: fresh() }));
  await push(group([text('Vika?')], { group_id: Number(QUIET_GROUP), message_id: 39, time: fresh() }));
  time.advance(60_000);
  assert.equal(lines.length, 7);
  const policyPath = path.join(dir, 'onebot', 'config.json');
  fs.writeFileSync(policyPath, JSON.stringify({ groups: { [GROUP]: 'ambient' } }));
  fs.utimesSync(policyPath, 1_700_000_000, 1_700_000_000);
  start();
  assert.deepEqual(lines.slice(7).map((line) => line.content.text), ['later']);
  assert.deepEqual(c.state.pendingChats(), []);
});

test('client: a self line waits only for the send on the socket, and a chat\'s pace holds back only that chat', async (t) => {
  let mid = 700;
  const written = [];
  const { c, ws, lines, start, sent } = await client(t, { paceMs: 400, sendTimeoutMs: 30_000, actions: {
    // NapCat reports the account's own message as it answers the send.
    send_group_msg: (_params, socket) => {
      const id = ++mid;
      written.push(Date.now());
      socket.frame(group([text('part')], { post_type: 'message_sent', message_id: id, user_id: Number(SELF), time: fresh(),
        sender: { user_id: Number(SELF), nickname: 'Vika' } }));
      return ok({ message_id: id });
    },
  } });
  start();
  const begun = Date.now();
  const parts = [1, 2, 3, 4].map((n) => c.sendMessage({ chat_id: `group:${GROUP}`, content: { type: 'markdown', text: `part ${n}` } }));
  await until(() => sent('send_group_msg').length === 1);
  ws.opened.at(-1).frame(direct([text('stop, wrong chat')], { message_id: 90, time: fresh() }));
  await until(() => lines.length === 1);
  assert.ok(Date.now() - begun < 600, 'the owner\'s line waited only for the first send, not for the paced ones');
  // The group's next sends wait out its pace; the owner's chat had no send.
  const other = Date.now();
  await c.sendMessage({ chat_id: `private:${OWNER}`, content: { type: 'markdown', text: 'ok' } });
  assert.ok(Date.now() - other < 600, 'a send to another chat does not wait out the group\'s pace');
  assert.ok(sent('send_group_msg').length < 4);
  await Promise.all(parts);
  assert.ok(written.slice(1).every((at, i) => at - written[i] >= 395), 'the group\'s sends are paced');
  // Every echo was recognised as the agent's own.
  await c.inbound;
  assert.deepEqual(c.state.pendingChats(), []);
  assert.deepEqual(lines.map((line) => line.content.text), ['stop, wrong chat']);
});

test('client: pause and stop return at once while a send waits for its answer', async (t) => {
  const { c, ws, start, sent } = await client(t, { sendTimeoutMs: 5000, actions: { send_group_msg: undefined } });
  start();
  const sending = c.sendMessage({ chat_id: `group:${GROUP}`, content: { type: 'markdown', text: 'big' } }).catch((e) => e);
  await until(() => sent('send_group_msg').length === 1);
  // A self line now waits on that send to know whether it is the agent's echo.
  ws.opened.at(-1).frame(group([text('gs 签到成功')], { post_type: 'message_sent', message_id: 31, user_id: Number(SELF), time: fresh(),
    sender: { user_id: Number(SELF), nickname: 'Vika' } }));
  const begun = Date.now();
  await c.stopSubscription();
  await c.stop();
  assert.ok(Date.now() - begun < 1500, `stop took ${Date.now() - begun} ms`);
  assert.equal((await sending).message, 'OneBot send_group_msg was cancelled before it was answered; the outcome is uncertain');
});

test('client: a send ends only the grace of a release it answers', async (t) => {
  const time = clock();
  let answer;
  const { c, lines, start, push, sent } = await client(t, { time, actions: {
    send_group_msg: () => new Promise((resolve) => { answer = () => resolve(ok({ message_id: 601 })); }),
  } });
  start();
  const chat = `group:${GROUP}`;
  await push(group([at(SELF), text(' first')], { message_id: 1, time: fresh() }));
  assert.equal(lines.length, 1);
  const reply = c.sendMessage({ chat_id: chat, content: { type: 'markdown', text: 'about the first' } });
  await until(() => sent('send_group_msg').length === 1);
  time.advance(1000);
  // A second release while the answer to the first is on its way.
  await push(group([at(SELF), text(' second')], { message_id: 2, time: fresh() }));
  await push(group([text('chatter')], { message_id: 3, time: fresh() }));
  assert.equal(lines.length, 2);
  answer();
  await reply;
  time.advance(3000);
  assert.equal(lines.length, 2, 'the second release keeps its grace');
  time.advance(TURN_GRACE_MS);
  assert.deepEqual(lines.map((line) => line.content.text), ['@Vika first', '@Vika second', 'chatter']);
});

test('client: a released batch carries the newest 30 context lines, none older than 6 h, then the direct line', async (t) => {
  const { c, lines, start, push } = await client(t);
  start();
  const chat = `group:${QUIET_GROUP}`;
  for (let i = 0; i < 35; i += 1) await push(group([text(`c${i}`)], { group_id: Number(QUIET_GROUP), message_id: 100 + i, time: fresh() }));
  assert.equal(lines.length, 0);
  // Staged before a restart, at a wall-clock time more than 6 h ago, and newer than the rest by order.
  const stale = normalize(group([text('stale')], { group_id: Number(QUIET_GROUP), message_id: 200, time: fresh() })).envelope;
  c.state.stage({ chat, cls: 'context', at: Date.now() - CONTEXT_AGE_MS - 1000, envelope: stale });
  await push(group([at(SELF), text(' now')], { group_id: Number(QUIET_GROUP), message_id: 300, time: fresh() }));
  assert.deepEqual(lines.map((line) => line.content.text),
    [...Array.from({ length: CONTEXT_LINES }, (_, i) => `c${35 - CONTEXT_LINES + i}`), '@Vika now']);
  assert.deepEqual(c.state.pendingChats(), []);
});

test('client: a name inside someone else\'s @ does not address the agent', async (t) => {
  const { lines, start, push } = await client(t, { actions: {
    get_group_member_info: ({ user_id: id }) => ok({ user_id: id, card: 'Vikander', nickname: 'v' }),
  } });
  start();
  const quiet = { group_id: Number(QUIET_GROUP), time: fresh() };
  await push(group([at('60006'), text(' hi')], { message_id: 1, ...quiet }));
  assert.equal(lines.length, 0);
  await push(group([text('vika, look')], { message_id: 2, ...quiet }));
  assert.deepEqual(lines.map((line) => line.content.text), ['@Vikander hi', 'vika, look']);
});

test('client looks up a few names per line and keeps a bounded memory of failed lookups', async (t) => {
  const { c, start, push, sent } = await client(t, { actions: {
    get_group_member_info: () => ({ status: 'failed', retcode: 1200, data: null, message: '用户不存在' }),
  } });
  start();
  const chat = `group:${GROUP}`;
  const ids = Array.from({ length: 8 }, (_, i) => String(60_001 + i));
  await push(group([text('hi '), ...ids.map((id) => at(id))], { message_id: 1, time: fresh() }));
  assert.equal(sent('get_group_member_info').length, 5);
  assert.equal(c.state.pending(chat)[0].envelope.content.text, `hi ${ids.map((id) => `@${id}`).join('')}`);
  // Expired entries are dropped when one is added, and the oldest once 1000 are held.
  c.failedNames.clear();
  c.failedNames.set('expired', Date.now() - 1);
  for (let i = 0; i < 999; i += 1) c.failedNames.set(`${chat}:${i}`, Date.now() + 60_000);
  await push(group([at('70001')], { message_id: 2, time: fresh() }));
  assert.equal(c.failedNames.size, 1000);
  assert.ok(!c.failedNames.has('expired') && c.failedNames.has(`${chat}:70001`));
  await push(group([at('70002')], { message_id: 3, time: fresh() }));
  assert.equal(c.failedNames.size, 1000);
  assert.ok(!c.failedNames.has(`${chat}:0`) && c.failedNames.has(`${chat}:70002`));
});

test('client neither stages nor answers chats that are not connected, and with private friends lets friends in without slash commands', async (t) => {
  const { c, lines, start, push, sent } = await client(t);
  start();
  await push(group([text('@everyone hi '), at('60006')], { group_id: 111, message_id: 1, time: fresh() }));
  await push(direct([text('hello')], { user_id: 30003, target_id: undefined, message_id: 2, time: fresh(),
    sender: { user_id: 30003, nickname: 'Stranger' } }));
  assert.equal(lines.length, 0);
  assert.deepEqual(c.state.pendingChats(), []);
  assert.equal(c.state.route('ob:group:111:1'), null);
  assert.deepEqual(sent('get_group_member_info'), [], 'nothing is looked up for a chat that is not connected');
  const markdown = { type: 'markdown', text: 'hi' };
  for (const attempt of [
    () => c.sendMessage({ chat_id: 'group:111', content: markdown }),
    () => c.sendMessage({ chat_id: 'private:30003', content: markdown }),
    () => c.replyMessage({ message_id: 'ob:group:111:1', content: markdown }),
    () => c.reactMessage({ message_id: 'ob:group:111:1', emoji_type: 'OK' }),
    () => c.retractMessage({ message_id: 'ob:group:111:1' }),
    () => c.getMedia({ message_id: 'ob:group:111:1' }),
    () => c.listChatMessages({ chat_id: 'private:30003' }),
  ]) await assert.rejects(attempt(), /OneBot is not connected to (group:111|private:30003)/);
  await assert.rejects(c.sendMessage({ chat_id: '123456789', content: markdown }), /group:<id> or private:<id>/);
  assert.equal(sent('send_group_msg').length + sent('send_private_msg').length, 0);

  const friends = await client(t, { policy: { ...POLICY, private: 'friends' } });
  friends.start();
  const friend = (message, extra) => direct(message, { user_id: 30003, target_id: undefined, time: fresh(),
    sender: { user_id: 30003, nickname: 'Alice' }, ...extra });
  // Core runs a leading / as a daemon command for whoever sent it: only the owner's get through.
  await friends.push(friend([text('/new')], { message_id: 3 }));
  await friends.push(friend([text('gs bind')], { message_id: 4 }));
  await friends.push(direct([text('/new')], { message_id: 5, time: fresh() }));
  await friends.push(friend([text('hi there')], { message_id: 6 }));
  assert.deepEqual(friends.lines.map((line) => [line.conversation.id, line.content.text]),
    [[`private:${OWNER}`, '/new'], ['private:30003', 'hi there']]);
  // What another client sends from the account in a private chat, a / command or not, is never shown.
  const own = (message, extra) => direct(message, { post_type: 'message_sent', user_id: Number(SELF), target_id: 30003, time: fresh(),
    sender: { user_id: Number(SELF), nickname: 'Vika' }, ...extra });
  await friends.push(own([text('/new')], { message_id: 7 }));
  await friends.push(own([text('see you')], { message_id: 8 }));
  await friends.push(own([text('/model x')], { message_id: 9, target_id: Number(OWNER) }));
  await friends.push(own([text('plugin reply')], { message_id: 12, target_id: Number(OWNER) }));
  assert.deepEqual(friends.c.state.pendingChats(), [], 'not even staged');
  await friends.push(friend([text('bye')], { message_id: 10 }));
  await friends.push(direct([text('hello')], { message_id: 11, time: fresh() }));
  assert.deepEqual(friends.lines.slice(2).map((line) => [line.actor.type, line.content.text]), [['user', 'bye'], ['user', 'hello']]);
  assert.deepEqual(friends.c.state.pendingChats(), []);
  await friends.c.sendMessage({ chat_id: 'private:30003', content: markdown });
  assert.equal(friends.sent('send_private_msg')[0].params.user_id, 30003);
  await assert.rejects(friends.c.sendMessage({ chat_id: 'private:40004', content: markdown }), /not connected to private:40004/);
  // Back to "owner": the friend's chat is closed again.
  const policyPath = path.join(friends.dir, 'onebot', 'config.json');
  fs.writeFileSync(policyPath, JSON.stringify({ ...POLICY, private: 'owner' }));
  fs.utimesSync(policyPath, 1_700_000_000, 1_700_000_000);
  await assert.rejects(friends.c.sendMessage({ chat_id: 'private:30003', content: markdown }), /not connected to private:30003/);
  assert.equal(friends.sent('send_private_msg').length, 1);
});

test('client mentions only people seen in the chat, quotes the target of a reply and sends files as base64', async (t) => {
  const { c, dir, start, push, sent } = await client(t, { actions: {
    get_group_member_info: ({ user_id: id }) => (id === 60006 ? ok({ user_id: id, card: '', nickname: 'Dave' })
      : { status: 'failed', retcode: 1200, data: null, message: '用户不存在' }),
  } });
  start();
  await push(group([text('hi')], { message_id: 1, time: fresh(), user_id: 30003, sender: { user_id: 30003, card: 'Alice Smith' } }));
  await push(group([text('yo')], { message_id: 2, time: fresh(), user_id: 40004, sender: { user_id: 40004, nickname: 'Bob' } }));
  await push(group([text('yo')], { message_id: 3, time: fresh(), user_id: 50005, sender: { user_id: 50005, card: 'bob' } }));
  const chat = `group:${GROUP}`;
  // An @ of someone not seen yet: their card, looked up once; a failed lookup is not repeated soon.
  const erin = { time: fresh(), user_id: 80008, sender: { user_id: 80008, nickname: 'Erin' } };
  await push(group([text('ask '), at('60006'), at('70007'), at('30003')], { message_id: 4, ...erin }));
  await push(group([at('60006'), at('70007')], { message_id: 5, ...erin }));
  assert.deepEqual(sent('get_group_member_info').map((request) => request.params),
    [{ group_id: Number(GROUP), user_id: 60006 }, { group_id: Number(GROUP), user_id: 70007 }]);
  assert.deepEqual(c.state.pending(chat).slice(-2).map((row) => row.envelope.content.text), ['ask @Dave@70007@Alice Smith', '@Dave@70007']);
  await c.replyMessage({ message_id: `ob:${chat}:1`, content: { type: 'markdown', text: 'thanks @Alice Smith', mentions: [{ name: 'alice_smith' }] } });
  assert.deepEqual(sent('send_group_msg')[0].params.message,
    [{ type: 'reply', data: { id: '1' } }, text('thanks '), at('30003')]);
  await assert.rejects(c.sendMessage({ chat_id: chat, content: { type: 'markdown', text: 'hi @Bob', mentions: [{ name: 'Bob' }] } }),
    /mention "Bob" is ambiguous in this chat/);
  await assert.rejects(c.sendMessage({ chat_id: chat, content: { type: 'markdown', text: 'hi', mentions: [{ name: 'Carol' }] } }),
    /mention "Carol" is not someone seen in this chat/);
  // The model names people by their from= label (sanitizeMemberName): brackets
  // and spaces become _, and a long name is cut at 40 characters.
  await push(group([text('在')], { message_id: 6, time: fresh(), user_id: 90009, sender: { user_id: 90009, card: '[技术]张三' } }));
  await push(group([text('o7')], { message_id: 7, time: fresh(), user_id: 91001,
    sender: { user_id: 91001, card: 'Captain Picard Of The Starship Enterprise NCC' } }));
  for (const [label, id] of [['技术_张三', 90009], ['Alice_Smith', 30003], ['Captain_Picard_Of_The_Starship_Enterpris', 91001]]) {
    await c.sendMessage({ chat_id: chat, content: { type: 'markdown', text: `ask @${label} now`, mentions: [{ name: label }] } });
    assert.deepEqual(sent('send_group_msg').at(-1).params.message, [text('ask '), at(String(id)), text(' now')], label);
  }
  await push(group([text('我也在')], { message_id: 8, time: fresh(), user_id: 90010, sender: { user_id: 90010, card: '技术 张三' } }));
  await assert.rejects(c.sendMessage({ chat_id: chat, content: { type: 'markdown', text: 'hi @技术_张三', mentions: [{ name: '技术_张三' }] } }),
    /mention "技术_张三" is ambiguous in this chat/);
  // QQ shows no @ in a private chat: the name stays text.
  await c.sendMessage({ chat_id: `private:${OWNER}`, content: { type: 'markdown', text: 'ask @Alice Smith', mentions: [{ name: 'Alice Smith' }] } });
  assert.deepEqual(sent('send_private_msg')[0].params, { user_id: Number(OWNER), message: [text('ask @Alice Smith')] });
  await assert.rejects(c.sendMessage({ chat_id: chat, content: { type: 'markdown', text: 'x'.repeat(4001) } }), /send it as a file/);

  const picture = path.join(dir, 'chart.bin');
  fs.writeFileSync(picture, PNG);
  const report = path.join(dir, 'report.txt');
  fs.writeFileSync(report, 'numbers');
  await c.replyMessage({ message_id: `ob:${chat}:1`, content: { type: 'file', path: picture } });
  await c.replyMessage({ message_id: `ob:${chat}:1`, content: { type: 'file', path: report, name: 'Q3 report.txt' } });
  const [image, file] = sent('send_group_msg').slice(-2).map((request) => request.params.message);
  assert.deepEqual(image, [{ type: 'reply', data: { id: '1' } }, { type: 'image', data: { file: `base64://${PNG.toString('base64')}` } }]);
  assert.deepEqual(file, [{ type: 'file', data: { file: `base64://${Buffer.from('numbers').toString('base64')}`, name: 'Q3 report.txt' } }]);
  const link = path.join(dir, 'link.txt');
  fs.symlinkSync(report, link);
  await assert.rejects(c.sendMessage({ chat_id: chat, content: { type: 'file', path: link } }), /ELOOP|symbolic/);
  fs.writeFileSync(path.join(dir, 'empty.txt'), '');
  await assert.rejects(c.sendMessage({ chat_id: chat, content: { type: 'file', path: path.join(dir, 'empty.txt') } }), /nonempty regular file/);
  assert.equal(sent('send_group_msg').length, 6);
});

test('client sends are uncertain once written, plainly not sent otherwise, and retract and react only what they may', async (t) => {
  let reactions = 'ok';
  const { c, ws, start, push, sent } = await client(t, { sendTimeoutMs: 700, actions: {
    send_group_msg: undefined,
    send_private_msg: () => ok({ message_id: 'not-a-number' }),
    delete_msg: () => ok(null),
    set_msg_emoji_like: () => (reactions === 'ok' ? ok(null) : { status: 'failed', retcode: 1404, data: null }),
  } });
  start();
  const chat = `group:${GROUP}`;
  const silent = await c.sendMessage({ chat_id: chat, content: { type: 'markdown', text: 'hi' } }).catch((e) => e);
  assert.equal(silent.message, 'OneBot send_group_msg was cancelled before it was answered; the outcome is uncertain');
  await assert.rejects(c.sendMessage({ chat_id: `private:${OWNER}`, content: { type: 'markdown', text: 'hi' } }),
    /no message id; delivery is uncertain/);
  ws.opened[0].close(1006);
  const offline = await c.sendMessage({ chat_id: chat, content: { type: 'markdown', text: 'hi' } }).catch((e) => e);
  assert.equal(offline.message, 'OneBot is not connected; send_group_msg was not sent');
  assert.equal(sent('send_group_msg').length, 1);
  await until(() => ws.opened.length === 2 && c.socket.connected, 3000);

  c.state.recordSent(chat, '-77');
  await push(group([text('hi')], { message_id: 8, time: fresh() }));
  await assert.rejects(c.retractMessage({ message_id: `ob:${chat}:8` }), /retracts only messages this agent sent/);
  assert.deepEqual(await c.retractMessage({ message_id: `ob:${chat}:-77` }), { message_id: `ob:${chat}:-77`, retracted: true });
  assert.deepEqual(sent('delete_msg')[0].params, { message_id: -77 });

  assert.deepEqual(await c.reactMessage({ message_id: `ob:${chat}:8`, emoji_type: 'THUMBSUP' }), { message_id: `ob:${chat}:8`, reaction_id: '76' });
  assert.deepEqual(sent('set_msg_emoji_like')[0].params, { message_id: 8, emoji_id: '76', set: true });
  await c.deleteReaction({ message_id: `ob:${chat}:8`, reaction_id: '128064' });
  assert.equal(sent('set_msg_emoji_like')[1].params.set, false);
  await assert.rejects(c.reactMessage({ message_id: `ob:${chat}:8`, emoji_type: 'THINKING' }), /no reaction for THINKING; use one of OK, THUMBSUP/);
  await assert.rejects(c.reactMessage({ message_id: `ob:private:${OWNER}:5`, emoji_type: 'OK' }), /group messages only/);
  reactions = 'unsupported';
  await assert.rejects(c.reactMessage({ message_id: `ob:${chat}:8`, emoji_type: 'OK' }), /Reactions are not available on this OneBot implementation/);
});

test('client media prefers the implementation copy, falls back to the event address, stays bounded and writes forwarded chats as text', async (t) => {
  const fetched = [];
  let body = 'from-the-event';
  const fetchImpl = async (url, init) => {
    fetched.push([url, init.redirect]);
    return new Response(body);
  };
  const forward = {
    // NapCat: messages with sender and message.
    messages: [
      { user_id: 30003, sender: { user_id: 30003, nickname: 'alice', card: 'Alice' }, message: [text('see this')] },
      { user_id: 40004, sender: { user_id: 40004, nickname: 'Bob' }, message: [{ type: 'image', data: { file: 'x.png' } }, text('line one\nline two')] },
    ],
  };
  const { c, start, push, sent } = await client(t, { fetchImpl, actions: {
    get_image: (params) => (params.file === 'copy.png' ? ok({ file: '/srv/napcat/copy.png', url: '/srv/napcat/copy.png', base64: PNG.toString('base64') })
      : { status: 'failed', retcode: 1404, data: null }),
    get_file: () => ok({ file: '/srv/napcat/r.pdf', url: 'https://napcat.local/fresh.pdf' }),
    get_forward_msg: (params) => (params.id === 'lagrange-res' ? ok({ message: [{ type: 'node', data: { user_id: '1', nickname: 'Carol', content: [text('hey')] } }] })
      : ok(forward)),
  } });
  start();
  const chat = `group:${GROUP}`;
  await push(group([
    { type: 'image', data: { file: 'copy.png', url: 'https://multimedia.nt.qq.com.cn/download?rkey=K1' } },
    { type: 'image', data: { file: 'gone.png', url: 'https://multimedia.nt.qq.com.cn/download?rkey=K2' } },
    { type: 'file', data: { file: 'r.pdf', file_id: '/uuid' } },
    { type: 'forward', data: { id: '7349' } },
  ], { message_id: 1, time: fresh() }));
  const { media } = await c.getMedia({ message_id: `ob:${chat}:1` });
  assert.deepEqual(media.map((item) => [item.kind, item.name, item.mime_type]), [
    ['image', 'copy.png', 'image/png'], ['image', 'gone.png', 'application/octet-stream'],
    ['file', 'r.pdf', 'application/octet-stream'], ['file', 'forwarded-chat.txt', 'text/plain; charset=utf-8']]);
  assert.deepEqual(fs.readFileSync(media[0].path), PNG);
  assert.equal(fs.readFileSync(media[1].path, 'utf8'), 'from-the-event');
  assert.equal(fs.readFileSync(media[3].path, 'utf8'), 'Alice: see this\nBob: line one\n  line two\n');
  assert.ok(media.every((item) => (fs.statSync(item.path).mode & 0o777) === 0o600 && item.path.startsWith(c.state.mediaDirectory)));
  // The implementation's local paths are never read; its address wins over the event's.
  assert.deepEqual(fetched, [['https://multimedia.nt.qq.com.cn/download?rkey=K2', 'error'], ['https://napcat.local/fresh.pdf', 'error']]);
  assert.deepEqual(sent('get_file')[0].params, { file_id: '/uuid' });
  assert.deepEqual(sent('get_forward_msg')[0].params, { id: '7349' });

  await push(group([{ type: 'forward', data: { id: 'lagrange-res' } }], { message_id: 2, time: fresh() }));
  const lagrange = await c.getMedia({ message_id: `ob:${chat}:2` });
  assert.equal(fs.readFileSync(lagrange.media[0].path, 'utf8'), 'Carol: hey\n');

  await push(group([text('no files')], { message_id: 3, time: fresh() }));
  await assert.rejects(c.getMedia({ message_id: `ob:${chat}:3` }), /No OneBot attachment/);
  await push(group([{ type: 'file', data: { file: 'big.iso', file_id: '/big', file_size: 21 * 1024 * 1024 } }], { message_id: 4, time: fresh() }));
  await assert.rejects(c.getMedia({ message_id: `ob:${chat}:4` }), /up to 20 MiB/);
  body = new Uint8Array(20 * 1024 * 1024 + 1);
  await push(group([{ type: 'image', data: { file: 'huge.png', url: 'https://multimedia.nt.qq.com.cn/download?rkey=SECRET' } }], { message_id: 5, time: fresh() }));
  const huge = await c.getMedia({ message_id: `ob:${chat}:5` }).catch((e) => e);
  assert.equal(huge.message, 'OneBot media download failed or exceeded the size limit');
});

test('client media never takes an address from a file name, nor fetches one on this machine or a private network', async (t) => {
  const fetched = [];
  const fetchImpl = async (url) => { fetched.push(url); return new Response('SECRET'); };
  const { c, start, push } = await client(t, { fetchImpl, actions: {
    // NapCat without enableLocalFile2Url: a local path and no bytes.
    get_file: () => ok({ file: '/napcat/cache/x', url: '/napcat/cache/x', file_size: '12', file_name: 'x' }),
    get_image: () => ok({ file: '/napcat/cache/y', url: '/napcat/cache/y' }),
  } });
  start();
  const chat = `group:${GROUP}`;
  await push(group([{ type: 'file', data: { file: 'http://169.254.169.254/latest/meta-data/iam/security-credentials/role',
    file_id: 'uuid-1' } }], { message_id: 1, time: fresh() }));
  await assert.rejects(c.getMedia({ message_id: `ob:${chat}:1` }), /did not provide this attachment/);
  const local = ['http://127.0.0.1:3000/a', 'http://localhost/a', 'http://LOCALHOST./a', 'http://x.localhost/a', 'http://2130706433/a',
    'http://0.0.0.0/a', 'http://10.1.2.3/a', 'http://100.96.0.96/a', 'http://169.254.169.254/a', 'http://172.16.0.1/a',
    'https://192.168.1.5/a', 'http://[::]/a', 'http://[::1]/a', 'http://[fd00::1]/a', 'http://[fe80::1]/a', 'http://[::ffff:10.0.0.1]/a'];
  for (const [index, url] of local.entries()) {
    await push(group([{ type: 'image', data: { file: `p${index}.png`, url } }], { message_id: 10 + index, time: fresh() }));
    await assert.rejects(c.getMedia({ message_id: `ob:${chat}:${10 + index}` }), /OneBot media download failed/, url);
  }
  assert.deepEqual(fetched, []);
  // Other hosts, public address literals among them, are fetched.
  for (const [index, url] of ['https://multimedia.nt.qq.com.cn/download?rkey=K', 'http://203.0.113.7/a', 'http://[2001:db8::1]/a'].entries()) {
    await push(group([{ type: 'image', data: { file: `q${index}.png`, url } }], { message_id: 50 + index, time: fresh() }));
    await c.getMedia({ message_id: `ob:${chat}:${50 + index}` });
  }
  assert.deepEqual(fetched, ['https://multimedia.nt.qq.com.cn/download?rkey=K', 'http://203.0.113.7/a', 'http://[2001:db8::1]/a']);
});

test('client history is the newest page, newest first, with routes; chat lists show connected groups only', async (t) => {
  const history = [
    group([text('older')], { message_id: 10, time: T, user_id: 30003, sender: { user_id: 30003, card: 'Alice' } }),
    group([text('my answer')], { message_id: 11, time: T + 5, post_type: 'message_sent', user_id: Number(SELF),
      sender: { user_id: Number(SELF), nickname: 'Vika' } }),
    group([{ type: 'image', data: { file: 'p.png', url: 'https://multimedia.nt.qq.com.cn/download?rkey=K' } }], { message_id: 12, time: T + 9 }),
  ];
  const { c, sent } = await client(t, { actions: {
    get_group_msg_history: () => ok({ messages: history }),
    get_friend_msg_history: () => ok({ messages: [direct([text('x')], { group_id: undefined, user_id: 333 })] }),
    get_group_list: () => ok([{ group_id: Number(GROUP), group_name: 'Friends' }, { group_id: 111, group_name: 'Other' },
      { group_id: Number(QUIET_GROUP), group_name: 'Quiet Room' }, { group_id: 'bad' }]),
  } });
  const chat = `group:${GROUP}`;
  const page = await c.listChatMessages({ chat_id: chat, limit: 2 });
  assert.deepEqual(sent('get_group_msg_history')[0].params, { group_id: Number(GROUP), count: 2 });
  assert.deepEqual(page.messages.map((message) => [message.message_id, message.sender.type, message.content]), [
    [`ob:${chat}:12`, 'user', '[attachment type=image id=0 name="p.png"]'], [`ob:${chat}:11`, 'bot', 'my answer']]);
  assert.equal(page.has_more, false);
  assert.equal(page.next_cursor, null);
  assert.deepEqual(page.messages[0].conversation, { id: chat, tenant_id: SELF, thread_id: null });
  assert.equal(c.state.route(`ob:${chat}:12`).files[0].url, 'https://multimedia.nt.qq.com.cn/download?rkey=K');
  assert.doesNotMatch(JSON.stringify(page), /rkey/);
  // QQ times are whole seconds: two messages of one second keep the page's order, newest first.
  history.push(group([text('same second, later')], { message_id: 13, time: T + 9 }));
  assert.deepEqual((await c.listChatMessages({ chat_id: chat, limit: 3 })).messages.map((message) => message.message_id),
    [`ob:${chat}:13`, `ob:${chat}:12`, `ob:${chat}:11`]);
  await assert.rejects(c.listChatMessages({ chat_id: chat, cursor: 'x' }), /no cursor/);
  await assert.rejects(c.listChatMessages({ chat_id: chat, limit: 51 }), /between 1 and 50/);
  await assert.rejects(c.listChatMessages({ chat_id: chat, as: 'user' }), /this account only/);
  await assert.rejects(c.listChatMessages({ chat_id: `private:${OWNER}` }), /history message identity/);
  assert.equal(c.state.route(`ob:private:${OWNER}:77`), null);

  assert.deepEqual(await c.listChats(), { chats: [
    { chat_id: chat, name: 'Friends', chat_status: 'normal' }, { chat_id: `group:${QUIET_GROUP}`, name: 'Quiet Room', chat_status: 'normal' },
  ], has_more: false, next_cursor: null });
  assert.deepEqual((await c.listChats({ query: 'quiet' })).chats.map((item) => item.chat_id), [`group:${QUIET_GROUP}`]);
  assert.equal((await c.listChats({ limit: 1 })).has_more, true);

  // Through the Core's tools: results take the shapes it renders.
  const store = new Store(':memory:', quietLogger);
  t.after(() => store.close());
  store.initializeCognition('codex', 'runtime-token');
  const connectors = { manifests: () => [['onebot', getConnectorManifest('onebot')]],
    invoke: (_id, action, input) => invokeOperation(c, action, input) };
  const actions = new ActionService({ config: { workspace: c.config.dataDir, tempDir: c.config.dataDir, maxReplyChars: 30_000 },
    store, connectors, logger: quietLogger });
  const call = (tool, input) => actions.call(tool, input, { authority: 'runtime-token' });
  assert.match((await call('list_chats', {})).text, /Friends \| chat_id=group:123456789 \| normal/);
  assert.match((await call('list_chat_messages', { chat_id: chat })).text, /ob:group:123456789:11 \| Vika \| text \| my answer/);
});

test('Harness delivers OneBot lines and the outbox keeps one reply per decision', async (t) => {
  const { c, sent } = await client(t);
  const store = new Store(':memory:', quietLogger);
  store.initializeCognition('codex', 'runtime-token');
  const connectors = { manifests: () => [['onebot', getConnectorManifest('onebot')]], owner: () => null,
    invoke: (_id, action, input) => invokeOperation(c, action, input) };
  const config = { channel: 'onebot', maxReplyChars: 30_000, maxEventBytes: 100_000, maxEventAgeMs: 60_000,
    ignoredSenderIds: new Set(), workspace: c.config.dataDir, tempDir: c.config.dataDir };
  const actions = new ActionService({ config, store, connectors, logger: quietLogger });
  const delivered = [], commands = [];
  const harness = new Harness({ config, store, logger: quietLogger, recordActivity() {},
    runtime: { snapshot: () => ({ session: { connected: true }, work: { turnInProgress: false } }),
      submit: (items) => { delivered.push(...items); return { accepted: true }; } },
    onSlashCommand: (command) => commands.push(command),
  });
  t.after(() => { harness.stop(); store.close(); });
  const message = normalizeMessage(direct([text('hi')], { time: fresh(), message_id: 41 }), { account, instance: 'onebot' });
  harness.handleConnectorEvent(message.envelope);
  harness.handleConnectorEvent(message.envelope);
  harness.handleConnectorEvent(normalizeMessage(direct([text('/help')], { time: fresh(), message_id: 42 }), { account, instance: 'onebot' }).envelope);
  await harness.ingressBarrier;
  assert.equal(delivered.length, 1);
  assert.match(delivered[0].text, /^\[chat=private:20002 source=onebot msg=ob:private:20002:41 .* from=Owner\] hi$/);
  assert.equal(commands.length, 1, 'the owner reaches the daemon commands');
  // The Core refuses text QQ cannot take before it reserves the outbox, so the decision stays open.
  await assert.rejects(actions.call('reply_message', { message_id: message.id, markdown: 'x'.repeat(4001), decision_id: message.envelope.id },
    { authority: 'runtime-token' }), /exceeds the 4000-character limit/);
  const reply = { message_id: message.id, markdown: '你好', decision_id: message.envelope.id };
  await actions.call('reply_message', reply, { authority: 'runtime-token' });
  await actions.call('reply_message', reply, { authority: 'runtime-token' });
  assert.equal(sent('send_private_msg').length, 1);
  assert.deepEqual(sent('send_private_msg')[0].params.message, [{ type: 'reply', data: { id: '41' } }, text('你好')]);
});

test('real OneBot worker supports spec/check/start, batches a chat, drops its own echo, replies after pause and stops', { timeout: 15_000 }, async (t) => {
  const { dir, state } = fixture(t);
  const server = new WebSocketServer({ port: 0, host: '127.0.0.1' });
  t.after(() => server.close());
  await new Promise((resolve) => server.once('listening', resolve));
  const url = `ws://127.0.0.1:${server.address().port}/`;
  state.saveAccount({ url, token: TOKEN, selfId: SELF, selfName: 'Vika', ownerId: OWNER, activatedAt: 0 });
  // A short settle keeps the test fast.
  fs.writeFileSync(path.join(dir, 'onebot', 'config.json'), JSON.stringify({ ...POLICY, settleMs: 50 }));
  let peer = null;
  const requests = [];
  const event = (raw) => peer.send(JSON.stringify(raw));
  server.on('connection', (client, request) => {
    assert.equal(request.headers.authorization, `Bearer ${TOKEN}`);
    peer = client;
    client.send(JSON.stringify(lifecycle));
    client.on('message', (data) => {
      const { action, params, echo } = JSON.parse(String(data));
      requests.push({ action, params });
      if (action === 'get_login_info') client.send(JSON.stringify(ok({ user_id: Number(SELF), nickname: 'Vika' }, echo)));
      if (action === 'send_group_msg') {
        // The account's own echo first, then the answer.
        event(group([text('reply')], { post_type: 'message_sent', message_id: 9001, user_id: Number(SELF), time: fresh(),
          sender: { user_id: Number(SELF), nickname: 'Vika' } }));
        client.send(JSON.stringify(ok({ message_id: 9001 }, echo)));
      }
      if (action === 'send_private_msg') client.send(JSON.stringify(ok({ message_id: 9002 }, echo)));
    });
  });
  const registry = new ProcessRegistry(quietLogger);
  const worker = new ConnectorProcess('onebot', { dataDir: dir, projectDir: process.cwd(), retentionMs: 86400_000,
    maxEventBytes: 100_000, sendTimeoutMs: 5000 }, registry, quietLogger);
  t.after(async () => { await worker.stop(); await registry.stopAll(1000); });
  assert.equal((await worker.preflight()).manifest.name, 'onebot');
  assert.deepEqual(worker.owner, { id: OWNER, target: { chat_id: `private:${OWNER}` } });
  assert.deepEqual(worker.self, { id: SELF });
  const received = [];
  await worker.startSubscription((message) => received.push(message), () => assert.fail('unexpected worker exit'));

  // Another program's line rides along with the next release of its chat.
  event(group([text('gs 签到成功')], { post_type: 'message_sent', message_id: 31, user_id: Number(SELF), time: fresh(),
    sender: { user_id: Number(SELF), nickname: 'Vika' } }));
  event(group([text('nice weather')], { message_id: 32, time: fresh() }));
  await until(() => received.length === 2, 3000);
  assert.deepEqual(received.map((message) => [message.actor.type, message.content.text]), [['bot', 'gs 签到成功'], ['user', 'nice weather']]);

  const sent = await worker.invoke('message.reply', { message_id: received[1].message.id, content: { type: 'markdown', text: 'reply' } });
  assert.equal(sent.message_id, `ob:group:${GROUP}:9001`);
  assert.deepEqual(requests.find((item) => item.action === 'send_group_msg').params,
    { group_id: Number(GROUP), message: [{ type: 'reply', data: { id: '32' } }, text('reply')] });
  // The echo is not an event: the next one is the owner's.
  event(direct([text('thanks')], { message_id: 33, time: fresh() }));
  await until(() => received.length === 3, 3000);
  assert.equal(received[2].content.text, 'thanks');

  await worker.request('pause', {});
  event(direct([text('after pause')], { message_id: 34, time: fresh() }));
  const reply = await worker.invoke('message.send', { chat_id: `private:${OWNER}`, content: { type: 'markdown', text: 'bye' } });
  assert.equal(reply.message_id, `ob:private:${OWNER}:9002`);
  await delay(100);
  assert.equal(received.length, 3);
  await worker.stop();
  const after = new OneBotState(dir);
  assert.deepEqual(after.pending(`private:${OWNER}`).map((row) => row.envelope.content.text), ['after pause']);
  // Had the echo of 'reply' not been dropped, it would wait here as context.
  assert.deepEqual(after.pending(`group:${GROUP}`), []);
  after.close();
});
