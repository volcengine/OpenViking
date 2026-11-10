// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import test from 'node:test';
import { SlackApi, SlackError } from '../src/slack/api.mjs';
import { SlackState } from '../src/slack/state.mjs';
import { loginSlack } from '../src/slack/auth.mjs';
import { messageId, normalizeMessage, relevant, renderText } from '../src/slack/events.mjs';
import { toMrkdwn } from '../src/slack/format.mjs';
import { SocketModeReceiver } from '../src/slack/socket.mjs';
import { SlackClient } from '../src/slack/client.mjs';
import { getConnectorManifest } from '../src/connectors/catalog.mjs';
import { ConnectorProcess } from '../src/connectors/process.mjs';
import { invokeOperation } from '../src/connectors/operations.mjs';
import { ProcessRegistry } from '../src/process-utils.mjs';
import { normalizeEvent } from '../src/event.mjs';
import { formatInboundLine } from '../src/runtime-input.mjs';
import { Store } from '../src/store.mjs';
import { Harness } from '../src/harness.mjs';
import { ActionService } from '../src/actions/service.mjs';

const BOT_TOKEN = 'xoxb-1111-2222-abcdefghijklmnop';
const APP_TOKEN = 'xapp-1-A0APP-3333-abcdefghijklmnop';
const account = { teamId: 'T0TEAM', appId: 'A0APP', botId: 'B0BOT', botUserId: 'U0BOT', ownerId: 'U0OWNER',
  ownerChannel: 'D0OWNER', botToken: BOT_TOKEN, appToken: APP_TOKEN, activatedAt: 0 };
const logger = { info() {}, warn() {}, error() {}, debug() {}, child() { return this; } };
const now = Math.floor(Date.now() / 1000);
const ts = (n) => `${now}.${String(n).padStart(6, '0')}`;
const payload = (event = {}, extra = {}) => ({
  type: 'event_callback', team_id: account.teamId, api_app_id: account.appId, event_id: 'Ev01',
  event: { type: 'message', channel: 'C0GENERAL', channel_type: 'channel', user: 'U0ALICE', text: 'hello', ts: ts(1), ...event },
  ...extra,
});

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-slack-'));
  const state = new SlackState(dir);
  t.after(() => { state.close(); fs.rmSync(dir, { recursive: true, force: true }); });
  return { dir, state };
}

// A Slack Web API that answers from `handlers` by method name.
function provider(handlers = {}, calls = []) {
  return async (url, init) => {
    assert.equal(init.redirect, 'error');
    if (!url.startsWith('https://slack.com/api/')) return handlers.raw(url, init);
    const method = url.slice('https://slack.com/api/'.length);
    const params = Object.fromEntries(new URLSearchParams(init.body));
    calls.push([method, params, init.headers.Authorization]);
    const defaults = {
      'auth.test': () => ({ team_id: account.teamId, team: 'Acme', user_id: account.botUserId, bot_id: account.botId }),
      'bots.info': () => ({ bot: { id: account.botId, app_id: account.appId, name: 'vika' } }),
      'apps.connections.open': () => ({ url: 'wss://wss-primary.slack.com/link/?ticket=1' }),
      'users.info': ({ user }) => ({ user: { id: user, profile: { display_name: { U0ALICE: 'Alice Smith', U0OWNER: 'Owner' }[user] ?? '' } } }),
    };
    const answer = (handlers[method] ?? defaults[method])?.(params, init);
    if (answer instanceof Response) return answer;
    return Response.json(answer?.error ? { ok: false, ...answer } : { ok: true, ...answer });
  };
}

// Socket Mode as the tests need it: hello on open, frames on demand.
function sockets({ appId = account.appId, connections = () => 1 } = {}) {
  const opened = [];
  class Socket extends EventTarget {
    constructor(url) {
      super();
      assert.match(url, /^wss:\/\/wss-primary\.slack\.com\//);
      this.sent = [];
      this.closed = false;
      opened.push(this);
      setImmediate(() => this.frame({ type: 'hello', num_connections: connections(opened.length), connection_info: { app_id: appId } }));
    }
    send(data) { this.sent.push(JSON.parse(data)); }
    close() { if (!this.closed) { this.closed = true; this.dispatchEvent(new Event('close')); } }
    frame(value) { if (!this.closed) this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(value) })); }
  }
  return { Socket, opened };
}

async function until(check, ms = 2000) {
  const deadline = Date.now() + ms;
  while (!check()) {
    if (Date.now() > deadline) throw new Error('condition not met in time');
    await delay(5);
  }
}

const fast = { helloMs: 500, recheckMs: 10, backoffMs: [5] };

async function client(t, handlers = {}, { calls = [], ws = sockets() } = {}) {
  const { dir, state } = fixture(t);
  state.saveAccount(account);
  state.close();
  const c = new SlackClient({ dataDir: dir, retentionMs: 86400_000, instance: 'slack', agentName: 'Vika', sendTimeoutMs: 5000 },
    null, logger, { fetchImpl: provider(handlers, calls), WebSocketImpl: ws.Socket, receiver: fast, paceMs: 20,
      queryApi: new SlackApi(BOT_TOKEN, { fetchImpl: provider(handlers, calls) }) });
  t.after(() => c.stop());
  await c.preflight();
  return c;
}

test('API keeps tokens in the header, never in errors, and retries only explicit rate limits', async () => {
  for (const fetchImpl of [async () => { throw new Error(`leaked ${BOT_TOKEN}`); }, async () => new Response(BOT_TOKEN)]) {
    const api = new SlackApi(BOT_TOKEN, { fetchImpl });
    await assert.rejects(api.call('auth.test'), (error) => !String(error.stack).includes(BOT_TOKEN) && !error.cause);
  }
  assert.throws(() => new SlackApi('xoxp-user-token'), /Invalid Slack bot token/);
  assert.throws(() => new SlackApi(BOT_TOKEN, { kind: 'app' }), /app-level/);
  let calls = 0;
  const limited = new SlackApi(BOT_TOKEN, { fetchImpl: async (_url, init) => {
    calls += 1;
    assert.equal(init.headers.Authorization, `Bearer ${BOT_TOKEN}`);
    assert.ok(!String(init.body).includes(BOT_TOKEN));
    return calls === 1 ? new Response('{"ok":false,"error":"ratelimited"}', { status: 429, headers: { 'retry-after': '0' } })
      : Response.json({ ok: true, channel: 'C0GENERAL', ts: ts(9) });
  } });
  assert.equal((await limited.send('chat.postMessage', {}, AbortSignal.timeout(5000))).ts, ts(9));
  assert.equal(calls, 2);
  const failing = new SlackApi(BOT_TOKEN, { fetchImpl: async () => { calls += 1; return Response.json({ ok: false, error: 'internal_error' }, { status: 500 }); } });
  await assert.rejects(failing.send('chat.postMessage', {}, AbortSignal.timeout(5000)), SlackError);
  assert.equal(calls, 3);
  assert.equal(getConnectorManifest('slack').actions['message.send'].idempotent, undefined);
  assert.equal(getConnectorManifest('slack').actions['message.reply'].thread, true);
});

test('member query identifies an unseen person and a tool reply can mention them by name', async (t) => {
  const posts = [];
  const c = await client(t, {
    'conversations.members': () => ({ members: ['U0BOT', 'U0OWNER', 'U0BOB12345', 'U0OTHERBOT', 'U0DELETED'], response_metadata: { next_cursor: '' } }),
    'users.info': ({ user }) => ({ user: { id: user, is_bot: ['U0BOT', 'U0OTHERBOT'].includes(user), deleted: user === 'U0DELETED',
      profile: { display_name: { U0BOT: 'Vika', U0OWNER: 'Owner', U0BOB12345: 'Bob Smith', U0OTHERBOT: 'Another bot', U0DELETED: 'Former member' }[user] } } }),
    'chat.postMessage': (params) => { posts.push(params); return { channel: params.channel, ts: ts(900) }; },
  });
  const store = new Store(':memory:', logger);
  store.initializeCognition('codex', 'runtime-token');
  t.after(() => store.close());
  const inbound = normalizeMessage(payload({ user: 'U0OWNER', text: 'Greet the other person' }), { account });
  c.state.recordRoute(inbound.id, inbound.route);
  store.ingest(normalizeEvent(inbound.envelope));
  const actions = new ActionService({ store, logger,
    config: { channel: 'slack', maxReplyChars: 30_000, workspace: c.config.dataDir, tempDir: c.config.dataDir },
    connectors: { manifests: () => [['slack', getConnectorManifest('slack')]],
      invoke: (_id, name, input) => invokeOperation(c, name, input) },
  });
  const call = (name, input) => actions.call(name, input, { authority: 'runtime-token' });
  assert.ok(actions.tools().some((tool) => tool.name === 'list_chat_members'));
  const result = await call('list_chat_members', { chat_id: 'C0GENERAL' });
  assert.equal(result.data.has_more, false);
  assert.equal(result.data.next_cursor, null);
  assert.deepEqual(result.data.members.filter((member) => !member.is_self && member.is_bot === false && member.deleted === false)
    .map((member) => member.user_id), ['U0OWNER', 'U0BOB12345']);
  assert.equal(result.data.members[0].is_self, true);
  assert.equal(result.data.members[3].is_bot, true);
  assert.equal(result.data.members[4].deleted, true);
  assert.equal(store.db.prepare('SELECT count(*) AS n FROM outbox').get().n, 0, 'reading members sends nothing');
  assert.throws(() => c.mentionsFor('C0OTHER', [{ name: 'Bob Smith' }]), /not someone seen/);
  await call('reply_message', { message_id: inbound.id, markdown: 'Hello @Bob Smith!', mention_names: ['Bob Smith'],
    reply_in_thread: true, decision_id: inbound.envelope.id });
  assert.equal(posts[0].text, 'Hello <@U0BOB12345>!');
  assert.equal(posts[0].thread_ts, ts(1));
});

test('member pages preserve Slack cursors, unknown profiles and ambiguous names', async (t) => {
  const calls = [];
  const c = await client(t, {
    'conversations.members': ({ cursor }) => cursor === 'page-two='
      ? { members: [], response_metadata: { next_cursor: '' } }
      : { members: ['U0ALICE', 'U0BOB', 'U0GONE'], response_metadata: { next_cursor: 'page-two=' } },
    'users.info': ({ user }) => user === 'U0GONE' ? { error: 'user_not_found' }
      : { user: { id: user, is_bot: false, deleted: false, profile: { display_name: 'Same\nName' } } },
  }, { calls });
  const page = await invokeOperation(c, 'chat.members.list', { chat_id: 'C0GENERAL', limit: 10 });
  assert.equal(page.has_more, true, 'a short page can still have more members');
  assert.equal(page.next_cursor, 'page-two=');
  assert.deepEqual(page.members[2], { user_id: 'U0GONE', name: null, is_self: false, is_bot: null, deleted: null, profile_error: 'user_not_found' });
  assert.equal(page.members[0].name, 'Same Name');
  assert.throws(() => c.mentionsFor('C0GENERAL', [{ name: 'Same Name' }]), /ambiguous/);
  assert.equal(c.mentionsFor('C0GENERAL', [{ id: 'U0BOB' }])[0].id, 'U0BOB');
  const last = await invokeOperation(c, 'chat.members.list', { chat_id: 'C0GENERAL', cursor: page.next_cursor, limit: 10 });
  assert.deepEqual(last, { chat_id: 'C0GENERAL', members: [], has_more: false, next_cursor: null });
  assert.deepEqual(calls.filter(([method]) => method === 'conversations.members').map(([, input]) => input), [
    { channel: 'C0GENERAL', limit: '10' }, { channel: 'C0GENERAL', limit: '10', cursor: 'page-two=' },
  ]);
});

test('missing member scopes give repair guidance while an existing Slack connection still works', async (t) => {
  let allowed = false;
  const c = await client(t, {
    'conversations.members': () => allowed ? { members: [] } : { error: 'missing_scope' },
    'chat.postMessage': ({ channel }) => ({ channel, ts: ts(901) }),
  });
  await assert.rejects(c.listChatMembers('C0GENERAL'), (error) => {
    assert.equal(error.code, 'missing_scope');
    assert.match(error.message, /channels:read[\s\S]*groups:read[\s\S]*mpim:read[\s\S]*im:read[\s\S]*reinstall/);
    assert.ok(!error.message.includes(BOT_TOKEN));
    return true;
  });
  assert.ok((await c.sendMessage({ chat_id: 'C0GENERAL', content: { type: 'markdown', text: 'Still connected' } })).message_id);
  allowed = true;
  assert.equal((await c.listChatMembers('C0GENERAL')).members.length, 0);
});

test('member lookup rejects malformed lists and cursors without inventing a complete roster', async (t) => {
  let response = {};
  const calls = [];
  const c = await client(t, { 'conversations.members': () => response,
    'users.info': () => ({ user: { id: 'U0WRONG', profile: { display_name: 'Wrong person' } } }),
  }, { calls });
  for (const [channel, options] of [['bad', {}], ['C0GENERAL', { limit: 201 }], ['C0GENERAL', { cursor: '\n' }]]) {
    await assert.rejects(c.listChatMembers(channel, options));
  }
  assert.equal(calls.filter(([method]) => method === 'conversations.members').length, 0);
  for (response of [{}, { members: ['invalid'] }, { members: [], response_metadata: { next_cursor: 42 } },
    { members: [], response_metadata: { next_cursor: 'same' } }]) {
    await assert.rejects(c.listChatMembers('C0GENERAL', { cursor: 'same' }), /invalid member/);
  }
  response = { members: ['U0ALICE'] };
  const page = await c.listChatMembers('C0GENERAL');
  assert.equal(page.members[0].name, null);
  assert.equal(page.members[0].profile_error, 'profile_unavailable');
  assert.deepEqual(c.state.people('C0GENERAL'), []);
});

test('conversation pages keep continuation after filtering and only list joined groups', async (t) => {
  const calls = [];
  const c = await client(t, { 'users.conversations': ({ cursor, types }) => {
    assert.equal(types, 'public_channel,private_channel,mpim');
    return cursor ? { channels: [{ id: 'G0PRIVATE', name: 'Design', is_member: true }], response_metadata: { next_cursor: '' } }
      : { channels: [{ id: 'C0GENERAL', name: 'general' }], response_metadata: { next_cursor: 'next=' } };
  } }, { calls });
  assert.deepEqual(await c.listChats({ query: 'design', limit: 2 }), { chats: [], has_more: true, next_cursor: 'next=' });
  const result = await c.listChats({ query: 'design', limit: 2, cursor: 'next=' });
  assert.equal(result.chats[0].chat_id, 'G0PRIVATE');
  assert.equal(result.has_more, false);
  const before = calls.length;
  await assert.rejects(c.listChats({ cursor: '\n' }), /cursor/);
  await assert.rejects(c.listChats({ limit: 501 }), /limit/);
  assert.equal(calls.length, before);
});

test('history to thread, media and reply works in a mixed instance without replaying old messages', async (t) => {
  const calls = [], posts = [];
  const channel = 'C0ARCHIVE', root = '1.000001', child = '1.000002';
  const rootId = messageId(account.teamId, channel, root), childId = messageId(account.teamId, channel, child);
  const events = [
    { ts: child, thread_ts: root, user: 'U0ALICE', text: 'Hi <@U0BOT>',
      files: [{ id: 'F012345678', name: 'context.txt', mimetype: 'text/plain', size: 7 }] },
    { ts: root, user: account.botUserId, text: 'My old message', reply_count: 1 },
  ];
  const c = await client(t, {
    'users.conversations': () => ({ channels: [{ id: channel, name: 'archive' }] }),
    'conversations.history': () => ({ messages: events, has_more: true, response_metadata: { next_cursor: 'older=' } }),
    'conversations.replies': params => {
      assert.equal(params.channel, channel); assert.equal(params.ts, root);
      return { messages: [...events].reverse(), response_metadata: { next_cursor: '' } };
    },
    'chat.postMessage': params => { posts.push(params); return { channel: params.channel, ts: ts(350) }; },
    'files.info': () => ({ file: { url_private_download: 'https://files.slack.com/context.txt' } }),
    raw: async () => new Response('context', { headers: { 'content-type': 'text/plain' } }),
  }, { calls });
  c.account.activatedAt = Date.now(); // History must survive the live receiver cutoff.
  const store = new Store(':memory:', logger);
  t.after(() => store.close());
  store.initializeCognition('codex', 'runtime-token');
  const incoming = normalizeMessage(payload(), { account });
  store.ingest(normalizeEvent(incoming.envelope));
  const connectors = { manifests: () => ['lark', 'slack'].map(id => [id, getConnectorManifest(id)]),
    invoke: (id, action, input) => { assert.equal(id, 'slack'); return invokeOperation(c, action, input); } };
  const actions = new ActionService({ config: { workspace: c.config.dataDir, tempDir: c.config.dataDir, maxReplyChars: 30_000 }, store, connectors, logger });
  const call = (tool, input) => actions.call(tool, input, { authority: 'runtime-token' });
  await call('list_chats', { source: 'slack' });
  const history = await call('list_chat_messages', { chat_id: channel });
  assert.match(history.text, /thread_id=sl:T0TEAM:C0ARCHIVE:1.000001/);
  assert.match(history.text, /older=/);
  assert.equal(history.data.messages[0].content.split('\n')[0], 'Hi @Vika');
  assert.equal(history.data.messages[1].sender.type, 'bot');
  assert.equal(c.state.route(childId).threadTs, root);
  const thread = await call('list_thread_messages', { thread_id: childId });
  assert.equal(thread.data.messages[0].message_id, rootId);
  const media = await call('get_message_media', { message_id: childId, key: 'F012345678' });
  assert.equal(fs.readFileSync(media.data.media[0].path, 'utf8'), 'context');
  assert.equal(store.db.prepare('SELECT count(*) AS n FROM connector_events').get().n, 1);
  assert.equal(store.db.prepare('SELECT count(*) AS n FROM outbox').get().n, 0);
  assert.deepEqual(c.state.pending(account.teamId), []);
  await assert.rejects(call('reply_message', { message_id: childId, markdown: 'hi', decision_id: 'T0TEAM:C0ARCHIVE:1.000002' }), /accepted daemon-authenticated/);
  await call('reply_message', { message_id: childId, markdown: 'Hi @Alice Smith', mention_names: ['Alice Smith'], decision_id: incoming.envelope.id });
  assert.equal(posts.length, 1);
  assert.equal(posts[0].channel, channel);
  assert.equal(posts[0].thread_ts, root);
  assert.match(posts[0].text, /<@U0ALICE>/);
  const sent = store.db.prepare('SELECT chat_id, conv_key FROM outbox').get();
  assert.equal(sent.chat_id, channel);
  assert.deepEqual(JSON.parse(sent.conv_key), ['slack', 'conversation', account.teamId, channel, root]);
  assert.equal(store.db.prepare('SELECT count(*) AS n FROM connector_events').get().n, 1);
  assert.equal(calls.filter(([method]) => method === 'conversations.replies').length, 1);
});

test('history validates identity, pagination and thread membership before caching routes', async (t) => {
  let messages = [{ ts: '1.000001', user: 'U0ALICE', text: 'old' }];
  let metadata = { next_cursor: '' };
  let hasMore = false;
  const calls = [];
  const c = await client(t, {
    'conversations.history': () => ({ messages, has_more: hasMore, response_metadata: metadata }),
    'conversations.replies': () => ({ messages, response_metadata: metadata }),
    'users.info': () => ({ error: 'missing_scope' }),
  }, { calls });
  const before = calls.length;
  for (const input of [{ chat_id: 'not-a-channel' }, { chat_id: 'C0GENERAL', as: 'user' }, { chat_id: 'C0GENERAL', limit: 51 }]) {
    await assert.rejects(c.listChatMessages(input));
  }
  for (const thread_id of ['1.000001', 'sl:T0OTHER:C0GENERAL:1.000001']) await assert.rejects(c.listThreadMessages({ thread_id }), /full sl:/);
  assert.equal(calls.length, before);
  hasMore = true;
  await assert.rejects(c.listChatMessages({ chat_id: 'C0GENERAL' }), /continuation cursor/);
  hasMore = false; metadata = { next_cursor: 'same=' };
  await assert.rejects(c.listChatMessages({ chat_id: 'C0GENERAL', cursor: 'same=' }), /invalid cursor/);
  metadata = { next_cursor: '' };
  messages.push({ ts: '1.000002', channel: 'C0OTHER', text: 'bad page' });
  await assert.rejects(c.listChatMessages({ chat_id: 'C0GENERAL' }), /history message identity/);
  assert.equal(c.state.route('sl:T0TEAM:C0GENERAL:1.000001'), null);
  messages = [{ ts: '1.000002', thread_ts: '1.000009', text: 'different thread' }];
  await assert.rejects(c.listThreadMessages({ thread_id: 'sl:T0TEAM:C0GENERAL:1.000001' }), /outside the requested thread/);
  messages = [{ ts: '1.000001', user: 'U0ALICE', text: 'no readable profile' }, { ts: '1.000003', subtype: 'tombstone' }];
  const result = await c.listChatMessages({ chat_id: 'C0GENERAL' });
  assert.equal(result.messages.length, 1);
  assert.equal(result.messages[0].sender.id, 'U0ALICE');
  assert.equal(result.messages[0].sender.name, null);
  messages = [{ ts: '1.000010', thread_ts: '1.000009', user: 'U0ALICE', text: 'later thread page' }];
  await c.listThreadMessages({ thread_id: 'sl:T0TEAM:C0GENERAL:1.000009', cursor: 'later=' });
  assert.equal(c.state.route('sl:T0TEAM:C0GENERAL:1.000009').ts, '1.000009', 'a root omitted from this page is still a usable reply target');
});

test('normalizes channel, thread and direct messages with names, files and external channels', () => {
  const names = new Map([['U0ALICE', 'Alice Smith'], ['U0CAROL', 'Carol']]);
  const top = normalizeMessage(payload({ text: 'hi <@U0BOT> and <@U0CAROL|carol>, see <https://x.test/a?b=1&amp;c=2|docs> &lt;@U0EVIL&gt; <!channel>' }),
    { account, agentName: 'Vika', names });
  assert.equal(top.envelope.id, `T0TEAM:C0GENERAL:${ts(1)}`);
  assert.equal(top.id, messageId('T0TEAM', 'C0GENERAL', ts(1)));
  assert.equal(top.envelope.content.text, 'hi @Vika and @Carol, see docs (https://x.test/a?b=1&c=2) <@U0EVIL> @channel');
  assert.deepEqual(top.envelope.actor, { id: 'U0ALICE', type: 'user', name: 'Alice Smith' });
  assert.deepEqual(top.envelope.conversation, { id: 'C0GENERAL', type: 'group', tenant_id: 'T0TEAM', thread_id: null });
  assert.deepEqual(top.people, [['U0ALICE', 'Alice Smith'], ['U0CAROL', 'Carol']]);

  const reply = normalizeMessage(payload({ ts: ts(2), thread_ts: ts(1), files: [{ id: 'F0FILE', name: 'a.png', mimetype: 'image/png', size: 3 }] },
    { is_ext_shared_channel: true }), { account, names });
  assert.equal(reply.envelope.conversation.thread_id, ts(1));
  assert.equal(reply.envelope.conversation.external, true);
  assert.equal(reply.envelope.message.thread_root_id, top.id);
  assert.equal(reply.envelope.message.type, 'image');
  assert.deepEqual(reply.envelope.content.attachments, [{ id: 'F0FILE', type: 'image', name: 'a.png' }]);
  assert.equal(reply.route.threadTs, ts(1));
  // A different thread, a different scope; the top-level message keeps the channel's.
  assert.notEqual(normalizeEvent(reply.envelope).scopeKey, normalizeEvent(top.envelope).scopeKey);

  const direct = normalizeMessage(payload({ channel: 'D0OWNER', channel_type: 'im', user: 'U0OWNER', text: ' /new' }), { account });
  assert.equal(direct.envelope.conversation.type, 'p2p');
  assert.equal(direct.envelope.actor.name, undefined);
});

test('drops the bot\'s own messages, edits, joins and hidden events so they never make a decision stale', () => {
  for (const event of [{ user: 'U0BOT' }, { bot_id: 'B0BOT', user: undefined, subtype: 'bot_message' },
    { subtype: 'message_changed' }, { subtype: 'message_deleted' }, { subtype: 'channel_join' }, { hidden: true },
    { channel_type: 'app_home' }]) {
    assert.equal(relevant(payload(event), account), false, JSON.stringify(event));
  }
  assert.equal(relevant(payload({}, { team_id: 'T0OTHER' }), account), false);
  assert.equal(relevant(payload({}, { api_app_id: 'A0OTHER' }), account), false);
  assert.equal(normalizeMessage(payload({ ts: `${Math.floor(Date.now() / 1000) - 100}.000001` }), { account: { ...account, activatedAt: Date.now() } }), null);
  const other = normalizeMessage(payload({ user: 'U0GITHUB', bot_id: 'B0GITHUB', text: '', bot_profile: { name: 'GitHub' },
    attachments: [{ fallback: 'PR #12 opened' }] }), { account });
  assert.deepEqual(other.envelope.actor, { id: 'U0GITHUB', type: 'bot', name: 'GitHub' });
  assert.equal(other.envelope.content.text, 'PR #12 opened');
  assert.equal(renderText('&lt;!here&gt; <!subteam^S1|@devs> <mailto:a@b.test|a@b.test>', () => null), '<!here> @devs mailto:a@b.test');
});

test('outgoing Markdown becomes escaped mrkdwn with only the mentions the Core resolved', () => {
  assert.equal(toMrkdwn('**Done** *now* ~~old~~ [docs](https://x.test/?a=1&b=2)\n- item\n# Title'),
    '*Done* _now_ ~old~ <https://x.test/?a=1&amp;b=2|docs>\n• item\n*Title*');
  assert.equal(toMrkdwn('<!channel> & `<@U1> **x**`\n```\n<b>\n```'), '&lt;!channel&gt; &amp; `&lt;@U1&gt; **x**`\n```\n&lt;b&gt;\n```');
  assert.equal(toMrkdwn('thanks @Alice Smith', [{ id: 'U0ALICE', name: 'Alice_Smith' }, { id: 'U0CAROL' }]),
    '<@U0CAROL> thanks <@U0ALICE>');
});

test('Socket Mode acknowledges after handling, refreshes before closing, and refuses a second receiver', async () => {
  const api = new SlackApi(APP_TOKEN, { kind: 'app', fetchImpl: provider() });
  const ws = sockets();
  const controller = new AbortController();
  const seen = [];
  const receiver = new SocketModeReceiver({ api, appId: account.appId, WebSocketImpl: ws.Socket, logger, ...fast });
  let ready = false;
  const running = receiver.run((body) => {
    if (body.fail) throw new Error('disk full');
    seen.push(body);
  }, controller.signal, { onReady: () => { ready = true; } });
  await until(() => ready);
  ws.opened[0].frame({ type: 'events_api', envelope_id: 'e1', payload: { n: 1 } });
  ws.opened[0].frame({ type: 'events_api', envelope_id: 'e2', payload: { fail: true } });
  ws.opened[0].frame({ type: 'slash_commands', envelope_id: 'e3', payload: {} });
  assert.deepEqual(seen, [{ n: 1 }]);
  assert.deepEqual(ws.opened[0].sent, [{ envelope_id: 'e1' }, { envelope_id: 'e3' }]);
  ws.opened[0].frame({ type: 'disconnect', reason: 'warning' });
  await until(() => ws.opened.length === 2 && ws.opened[0].closed);
  assert.equal(ws.opened[1].closed, false);
  ws.opened[1].close();
  await until(() => ws.opened.length === 3);
  controller.abort();
  await running.catch(() => {});
  assert.ok(ws.opened.every((socket) => socket.closed));

  const crowded = sockets({ connections: () => 2 });
  await assert.rejects(new SocketModeReceiver({ api, appId: account.appId, WebSocketImpl: crowded.Socket, logger, ...fast })
    .run(() => {}, AbortSignal.timeout(2000)), /Another program is receiving/);
  assert.equal(crowded.opened.length, 2);
  const foreign = sockets({ appId: 'A0OTHER' });
  await assert.rejects(new SocketModeReceiver({ api, appId: account.appId, WebSocketImpl: foreign.Socket, logger, ...fast })
    .run(() => {}, AbortSignal.timeout(2000)), /belongs to another app/);
  const disabled = sockets();
  const off = new SocketModeReceiver({ api, appId: account.appId, WebSocketImpl: disabled.Socket, logger, ...fast })
    .run(() => {}, AbortSignal.timeout(2000));
  await until(() => disabled.opened.length === 1 && disabled.opened[0].sent !== undefined);
  await delay(20);
  disabled.opened[0].frame({ type: 'disconnect', reason: 'link_disabled' });
  await assert.rejects(off, /Socket Mode was turned off/);
  const revoked = new SlackApi(APP_TOKEN, { kind: 'app', fetchImpl: provider({ 'apps.connections.open': () => ({ error: 'invalid_auth' }) }) });
  await assert.rejects(new SocketModeReceiver({ api: revoked, appId: account.appId, WebSocketImpl: sockets().Socket, logger, ...fast })
    .run(() => {}, AbortSignal.timeout(2000)), /vika login slack/);
});

test('binds the owner by a one-time code in a direct message and stages messages while re-binding', async (t) => {
  const { state } = fixture(t);
  const ws = sockets();
  // The code only counts in a message sent after it was shown.
  const fresh = (n) => `${Math.floor(Date.now() / 1000)}.${String(n).padStart(6, '0')}`;
  let challenge;
  const login = loginSlack({ state, botToken: BOT_TOKEN, appToken: APP_TOKEN, fetchImpl: provider(), WebSocketImpl: ws.Socket,
    receiver: fast, signal: AbortSignal.timeout(3000), onChallenge: (value) => { challenge = value; } });
  await until(() => challenge);
  assert.match(challenge.code, /^vika-[a-z2-9]{8}$/);
  assert.equal(challenge.url, 'https://slack.com/app_redirect?app=A0APP&team=T0TEAM');
  const socket = ws.opened[0];
  socket.frame({ type: 'events_api', envelope_id: 'a', payload: payload({ channel: 'C0GENERAL', text: challenge.code }) });
  socket.frame({ type: 'events_api', envelope_id: 'b', payload: payload({ channel: 'D0ALICE', channel_type: 'im', text: 'vika-wrong' }) });
  socket.frame({ type: 'events_api', envelope_id: 'c', payload: payload({ channel: 'D0OWNER', channel_type: 'im', user: 'U0OWNER', text: ` ${challenge.code} `, ts: fresh(3) }) });
  const bound = await login;
  assert.equal(bound.ownerId, 'U0OWNER');
  assert.deepEqual(socket.sent.map((ack) => ack.envelope_id), ['a', 'b', 'c']);
  const saved = state.account();
  assert.equal(saved.ownerChannel, 'D0OWNER');
  assert.equal(saved.botToken, BOT_TOKEN);
  assert.equal(fs.statSync(state.databasePath).mode & 0o777, 0o600);
  assert.equal(fs.statSync(state.directory).mode & 0o777, 0o700);
  assert.deepEqual(state.pending(account.teamId), []);

  // Binding again on the same app keeps what the agent should still hear.
  const again = sockets();
  let code;
  const relogin = loginSlack({ state, botToken: BOT_TOKEN, appToken: APP_TOKEN, fetchImpl: provider(), WebSocketImpl: again.Socket,
    receiver: fast, signal: AbortSignal.timeout(3000), onChallenge: (value) => { code = value.code; } });
  await until(() => code);
  again.opened[0].frame({ type: 'events_api', envelope_id: 'd', payload: payload({ text: 'question meanwhile' }) });
  again.opened[0].frame({ type: 'events_api', envelope_id: 'e', payload: payload({ channel: 'D0OWNER', channel_type: 'im', user: 'U0OWNER', text: code, ts: fresh(5) }) });
  await relogin;
  assert.deepEqual(state.pending(account.teamId).map((row) => row.payload.event.text), ['question meanwhile']);
  assert.equal(state.account().activatedAt, saved.activatedAt);
  await assert.rejects(loginSlack({ state, botToken: BOT_TOKEN, appToken: APP_TOKEN, fetchImpl: provider(), WebSocketImpl: sockets({ appId: 'A0OTHER' }).Socket,
    receiver: fast, signal: AbortSignal.timeout(3000) }), /belongs to another app/);
});

test('receives through a staged queue, names senders and keeps replies in their threads', async (t) => {
  const calls = [];
  const ws = sockets();
  let posted = 0;
  const c = await client(t, {
    'chat.postMessage': ({ channel }) => ({ channel, ts: ts(100 + ++posted) }),
    'reactions.add': () => ({}),
    'reactions.remove': () => ({ error: 'no_reaction' }),
    'chat.delete': () => ({}),
  }, { calls, ws });
  const lines = [];
  c.startSubscription((line) => lines.push(JSON.parse(line)), () => assert.fail('unexpected exit'));
  await until(() => ws.opened.length === 1 && ws.opened[0].sent);
  await delay(20);
  ws.opened[0].frame({ type: 'events_api', envelope_id: 'x', payload: payload({ text: 'ask <@U0BOT>' }) });
  ws.opened[0].frame({ type: 'events_api', envelope_id: 'y', payload: payload({ user: 'U0BOT', text: 'echo' }) });
  ws.opened[0].frame({ type: 'events_api', envelope_id: 'z', payload: payload({ ts: ts(2), thread_ts: ts(1), text: 'in thread' }) });
  await until(() => lines.length === 2);
  assert.deepEqual(ws.opened[0].sent.map((ack) => ack.envelope_id), ['x', 'y', 'z']);
  assert.equal(lines[0].content.text, 'ask @Vika');
  assert.equal(lines[0].actor.name, 'Alice Smith');
  assert.equal(calls.filter(([method]) => method === 'users.info').length, 1, 'names are cached');
  assert.deepEqual(c.state.pending(account.teamId), []);

  await c.replyMessage({ message_id: lines[0].message.id, content: { type: 'markdown', text: 'top' } });
  await c.replyMessage({ message_id: lines[0].message.id, content: { type: 'markdown', text: 'thread', mentions: [{ name: 'alice smith' }] }, reply_in_thread: true });
  await c.replyMessage({ message_id: lines[1].message.id, content: { type: 'markdown', text: 'stays' } });
  const posts = calls.filter(([method]) => method === 'chat.postMessage').map(([, params]) => params);
  assert.equal(posts[0].thread_ts, undefined);
  assert.equal(posts[1].thread_ts, ts(1));
  assert.equal(posts[1].text, '<@U0ALICE> thread');
  assert.equal(posts[2].thread_ts, ts(1));
  assert.equal(posts[0].unfurl_links, 'false');
  await assert.rejects(c.sendMessage({ chat_id: 'C0GENERAL', content: { type: 'markdown', text: 'hi', mentions: [{ name: 'Nobody' }] } }), /not someone seen/);
  await assert.rejects(c.sendMessage({ chat_id: 'C0GENERAL', content: { type: 'markdown', text: 'x'.repeat(12_001) } }), /12000/);
  await assert.rejects(c.sendMessage({ chat_id: 'U0ALICE', content: { type: 'markdown', text: 'x' } }), /channel id/);

  const reaction = await c.reactMessage({ message_id: lines[0].message.id, emoji_type: 'OnIt' });
  assert.equal(reaction.reaction_id, 'eyes');
  await c.deleteReaction({ message_id: lines[0].message.id, reaction_id: 'eyes' });
  await assert.rejects(c.reactMessage({ message_id: lines[0].message.id, emoji_type: 'Weird' }), /no reaction/);
  await c.retractMessage({ message_id: messageId('T0TEAM', 'C0GENERAL', ts(101)) });
  assert.deepEqual(calls.at(-1).slice(0, 2), ['chat.delete', { channel: 'C0GENERAL', ts: ts(101) }]);
});

test('uploads and downloads files only through Slack hosts without redirects', async (t) => {
  const bytes = Buffer.from('attachment');
  const c = await client(t, {
    'files.getUploadURLExternal': ({ filename, length }) => {
      assert.equal(filename, 'report.txt');
      assert.equal(length, '10');
      return { upload_url: 'https://files.slack.com/upload/v1/abc', file_id: 'F0UP' };
    },
    'files.completeUploadExternal': (params) => { assert.equal(params.thread_ts, ts(1)); return { files: [{ id: 'F0UP' }] }; },
    'files.info': ({ file }) => (file === 'F0UP'
      ? { file: { shares: { public: { C0GENERAL: [{ ts: ts(50) }] } } } }
      : { file: { url_private_download: 'https://files.slack.com/files-pri/T0TEAM-F0FILE/download/a.txt' } }),
    raw: async (url, init) => {
      if (url.startsWith('https://files.slack.com/upload/')) {
        assert.equal(init.headers, undefined, 'the upload address needs no token');
        return new Response('OK');
      }
      assert.equal(init.headers.Authorization, `Bearer ${BOT_TOKEN}`);
      return new Response(bytes, { headers: { 'content-type': 'text/plain' } });
    },
  });
  const file = path.join(c.config.dataDir, 'report.txt');
  fs.writeFileSync(file, bytes);
  c.state.recordRoute('sl:T0TEAM:C0GENERAL:1.000001', { channel: 'C0GENERAL', ts: '1.000001', threadTs: ts(1),
    files: [{ id: 'F0FILE', kind: 'file', name: '../a.txt', mimeType: 'text/plain', size: bytes.length }] });
  const sent = await c.replyMessage({ message_id: 'sl:T0TEAM:C0GENERAL:1.000001', content: { type: 'file', path: file, name: 'report.txt' } });
  assert.equal(sent.message_id, messageId('T0TEAM', 'C0GENERAL', ts(50)));
  const { media } = await c.getMedia({ message_id: 'sl:T0TEAM:C0GENERAL:1.000001', key: 'F0FILE' });
  assert.deepEqual(fs.readFileSync(media[0].path), bytes);
  assert.equal(media[0].name, 'a.txt');
  assert.equal(fs.statSync(media[0].path).mode & 0o777, 0o600);
  const api = new SlackApi(BOT_TOKEN, { fetchImpl: async () => assert.fail('must not fetch') });
  for (const url of ['https://evil.test/file', 'http://files.slack.com/x', 'https://slack.com.evil.test/x']) {
    await assert.rejects(api.download(url, AbortSignal.timeout(1000)), /not on Slack/);
  }
  const login = new SlackApi(BOT_TOKEN, { fetchImpl: async () => new Response('<html>', { headers: { 'content-type': 'text/html' } }) });
  await assert.rejects(login.download('https://files.slack.com/x', AbortSignal.timeout(1000)), /download failed/);
});

test('inbound lines name the sender, mark bots and external channels', () => {
  const bot = normalizeEvent(normalizeMessage(payload({ user: 'U0GITHUB', bot_id: 'B0GH', bot_profile: { name: 'Git Hub] [chat=x' }, text: 'CI failed' },
    { is_ext_shared_channel: true }), { account }).envelope);
  const line = formatInboundLine(bot);
  assert.match(line, /^\[chat=C0GENERAL external=true source=slack msg=sl:T0TEAM:C0GENERAL:\S+ event=\S+ time=\S+ from=Git_Hub_chat_x sender=bot\] CI failed$/);
  const person = normalizeEvent(normalizeMessage(payload({}), { account, names: new Map([['U0ALICE', 'Alice Smith']]) }).envelope);
  assert.match(formatInboundLine(person), / from=Alice_Smith\] hello$/);
  assert.throws(() => normalizeEvent({ ...person.envelope, actor: { id: 'U1', type: 'user', name: 5 } }), /actor name/);
});

test('the safety fuse holds replies to bots only after many with no person writing, and tells the owner once', async (t) => {
  const posts = [];
  const c = await client(t, {
    'chat.postMessage': ({ channel, text }) => { posts.push({ channel, text }); return { channel, ts: ts(200 + posts.length) }; },
    'reactions.add': () => ({}),
  });
  const store = new Store(':memory:', logger);
  store.initializeCognition('codex', 'runtime-token');
  t.after(() => store.close());
  const connectors = {
    manifests: () => [['slack', getConnectorManifest('slack')]],
    owner: () => ({ id: account.ownerId, target: { chat_id: account.ownerChannel } }),
    invoke: (_id, action, input) => invokeOperation(c, action, input),
  };
  // A small fuse keeps the test short; the default is 20.
  const config = { channel: 'slack', botReplyFuse: 3, maxReplyChars: 30_000, workspace: c.config.dataDir, tempDir: c.config.dataDir };
  const actions = new ActionService({ config, store, connectors, logger });
  const call = (name, input, service = actions) => service.call(name, input, { authority: 'runtime-token' });
  let n = 0;
  const arrive = (event) => {
    const normalized = normalizeMessage(payload({ ts: ts(++n), ...event }), { account });
    c.state.recordRoute(normalized.id, normalized.route);
    store.ingest(normalizeEvent(normalized.envelope));
    return normalized;
  };
  const alert = { user: 'U0ALERT', bot_id: 'B0ALERT', text: 'disk full' };
  const answer = (message, service) => call('reply_message', { message_id: message.id, markdown: 'looking', reply_in_thread: false,
    decision_id: message.envelope.id }, service);
  const notices = () => posts.filter((post) => post.channel === account.ownerChannel);
  // Below the fuse nothing is held; one decision's burst counts once.
  await answer(arrive(alert));
  const second = arrive(alert);
  await answer(second);
  await call('send_message', { chat_id: 'C0GENERAL', markdown: 'and more', decision_id: second.envelope.id });
  await answer(arrive(alert));
  // The fourth trips it, and the owner hears of it once, privately.
  await assert.rejects(answer(arrive(alert)), (error) => error.code === 'bot_loop');
  await assert.rejects(answer(arrive(alert)), (error) => error.code === 'bot_loop');
  await until(() => notices().length === 1);
  await delay(50);
  assert.equal(notices().length, 1);
  assert.match(notices()[0].text, /slack 的会话 C0GENERAL里我已经回应了 3 条 bot 消息/);
  // A reaction or a notice to the owner is not a loop.
  const next = arrive(alert);
  await call('react_to_message', { message_id: next.id, emoji_type: 'DONE', decision_id: next.envelope.id });
  await call('notify_owner', { markdown: 'alerts keep coming', decision_id: next.envelope.id });
  // A person writing there starts the count over.
  arrive({ text: 'any idea?' });
  await answer(arrive(alert));
  assert.equal(posts.filter((post) => post.channel === 'C0GENERAL').length, 5);
  assert.equal(store.db.prepare("SELECT COUNT(*) AS count FROM activities WHERE kind = 'delivery.blocked'").get().count, 2);
  // 0 turns the fuse off.
  const unfused = new ActionService({ config: { ...config, botReplyFuse: 0 }, store, connectors, logger });
  for (let round = 0; round < 4; round += 1) await answer(arrive(alert), unfused);
});

test('Harness delivers named lines and the outbox keeps one reply per decision', async (t) => {
  const sent = [];
  const c = await client(t, { 'chat.postMessage': (params) => { sent.push(params); return { channel: params.channel, ts: ts(300 + sent.length) }; } });
  const store = new Store(':memory:', logger);
  store.initializeCognition('codex', 'runtime-token');
  const connectors = { manifests: () => [['slack', getConnectorManifest('slack')]], owner: () => null,
    invoke: (_id, action, input) => invokeOperation(c, action, input) };
  const config = { channel: 'slack', maxReplyChars: 30_000, maxEventBytes: 100_000, maxEventAgeMs: 60_000,
    ignoredSenderIds: new Set(), workspace: c.config.dataDir, tempDir: c.config.dataDir };
  const actions = new ActionService({ config, store, connectors, logger });
  const delivered = [], commands = [];
  const harness = new Harness({ config, store, logger, recordActivity() {},
    runtime: { snapshot: () => ({ session: { connected: true }, work: { turnInProgress: false } }),
      submit: (items) => { delivered.push(...items); return { accepted: true }; } },
    onSlashCommand: (command) => commands.push(command),
  });
  t.after(() => { harness.stop(); store.close(); });
  const message = normalizeMessage(payload({ channel: 'D0ALICE', channel_type: 'im', text: 'hi' }), { account, names: new Map([['U0ALICE', 'Alice']]) });
  c.state.recordRoute(message.id, message.route);
  harness.handleConnectorEvent(message.envelope);
  harness.handleConnectorEvent(message.envelope);
  harness.handleConnectorEvent(normalizeMessage(payload({ channel: 'D0ALICE', channel_type: 'im', text: ' /help', ts: ts(7) }), { account }).envelope);
  await harness.ingressBarrier;
  assert.equal(delivered.length, 1);
  assert.match(delivered[0].text, / from=Alice\] hi$/);
  assert.equal(commands.length, 1, 'a leading space gets a slash command past Slack');
  // The slash command is not an input the agent decides on: the hello stays fresh.
  const reply = { message_id: message.id, markdown: '你好', decision_id: message.envelope.id };
  await actions.call('reply_message', reply, { authority: 'runtime-token' });
  await actions.call('reply_message', reply, { authority: 'runtime-token' });
  assert.equal(sent.length, 1);
  await assert.rejects(actions.call('reply_message', { ...reply, decision_id: `T0TEAM:D0ALICE:${ts(7)}` }, { authority: 'runtime-token' }),
    (error) => error.code === 'unknown_decision');
  // Bot lines say how many bot messages came since a person last wrote there.
  const bot = { channel: 'D0ALICE', channel_type: 'im', user: 'U0CI', bot_id: 'B0CI', text: 'build failed' };
  harness.handleConnectorEvent(normalizeMessage(payload({ ...bot, ts: ts(8) }), { account }).envelope);
  harness.handleConnectorEvent(normalizeMessage(payload({ ...bot, ts: ts(9) }), { account }).envelope);
  harness.handleConnectorEvent(normalizeMessage(payload({ channel: 'D0ALICE', channel_type: 'im', text: 'checking', ts: ts(10) }), { account }).envelope);
  harness.handleConnectorEvent(normalizeMessage(payload({ ...bot, ts: ts(11) }), { account }).envelope);
  await harness.ingressBarrier;
  assert.deepEqual(delivered.slice(1).map((item) => item.text.match(/ sender=bot bots_since_human=(\d+)\]/)?.[1] ?? null),
    ['1', '2', null, '1']);
});

test('real connector worker supports check/start/pause/reply/stop and releases its lease', async (t) => {
  const { dir, state } = fixture(t);
  state.saveAccount(account);
  const slackCliBin = path.join(dir, 'slack-cli');
  fs.writeFileSync(slackCliBin, `#!${process.execPath}
    const method = process.argv[3];
    const params = Object.fromEntries(new URLSearchParams(process.argv[process.argv.indexOf('--data') + 1]));
    const result = {
      'users.info': { user: { id: params.user, is_bot: false, deleted: false, profile: { display_name: 'Alice' } } },
      'conversations.members': { members: ['U0ALICE'] },
      'users.conversations': { channels: [{ id: 'C0GENERAL', name: 'general' }] },
      'conversations.history': { messages: [{ ts: '${ts(1)}', user: 'U0ALICE', text: 'history' }] },
      'conversations.replies': { messages: [{ ts: '${ts(1)}', user: 'U0ALICE', text: 'root' }] },
    }[method];
    console.log(JSON.stringify({ ok: true, ...result }));
  `, { mode: 0o755 });
  const workerFile = path.join(dir, 'worker.mjs');
  fs.writeFileSync(workerFile, `
    globalThis.fetch = async (url, init) => {
      const method = url.split('/').at(-1);
      const params = Object.fromEntries(new URLSearchParams(init.body));
      const result = {
        'auth.test': { team_id: 'T0TEAM', user_id: 'U0BOT', bot_id: 'B0BOT' },
        'bots.info': { bot: { app_id: 'A0APP' } },
        'apps.connections.open': { url: 'wss://wss-primary.slack.com/link/?ticket=1' },
        'users.info': { user: { id: params.user, is_bot: false, deleted: false, profile: { display_name: 'Alice' } } },
        'conversations.members': { members: ['U0ALICE'], response_metadata: { next_cursor: '' } },
        'chat.postMessage': { channel: params.channel, ts: '${now}.000099' },
      }[method];
      return Response.json({ ok: true, ...result });
    };
    globalThis.WebSocket = class extends EventTarget {
      constructor() {
        super();
        const frame = (value) => this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(value) }));
        setImmediate(() => {
          frame({ type: 'hello', num_connections: 1, connection_info: { app_id: 'A0APP' } });
          frame({ type: 'events_api', envelope_id: 'e1', payload: ${JSON.stringify(payload())} });
        });
      }
      send() {}
      close() { this.dispatchEvent(new Event('close')); }
    };
    await import(${JSON.stringify(new URL('../src/connectors/worker.mjs', import.meta.url).href)});
  `);
  const registry = new ProcessRegistry(logger);
  const worker = new ConnectorProcess('slack', { dataDir: dir, projectDir: process.cwd(), connectorWorkerPath: workerFile, slackCliBin,
    retentionMs: 86400_000, maxEventBytes: 100_000, sendTimeoutMs: 5000, agentName: 'Vika' }, registry, logger);
  t.after(async () => { await worker.stop(); await registry.stopAll(1000); });
  assert.equal((await worker.preflight()).manifest.name, 'slack');
  assert.deepEqual(worker.owner, { id: 'U0OWNER', target: { chat_id: 'D0OWNER' } });
  let receive;
  const event = new Promise((resolve) => { receive = resolve; });
  await worker.startSubscription(receive, () => assert.fail('unexpected worker exit'));
  const message = await event;
  assert.equal(message.actor.name, 'Alice');
  assert.ok(worker.manifest.queries.includes('chat.members.list'));
  const members = await worker.invoke('chat.members.list', { chat_id: 'C0GENERAL', limit: 10 });
  assert.equal(members.members[0].user_id, 'U0ALICE');
  assert.equal(members.members[0].name, 'Alice');
  assert.equal(members.has_more, false);
  assert.equal((await worker.invoke('chat.list', {})).chats[0].chat_id, 'C0GENERAL');
  const history = await worker.invoke('chat.messages', { chat_id: 'C0GENERAL' });
  assert.equal(history.messages[0].content, 'history');
  assert.equal((await worker.invoke('thread.messages', { thread_id: history.messages[0].thread_id })).messages[0].content, 'root');
  await worker.request('pause', {});
  const sent = await worker.invoke('message.reply', { message_id: message.message.id, content: { type: 'markdown', text: 'reply' } });
  assert.equal(sent.message_id, messageId('T0TEAM', 'C0GENERAL', `${now}.000099`));
  await worker.stop();
  state.acquire(); state.release();
});
