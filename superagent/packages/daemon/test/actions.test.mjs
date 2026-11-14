// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, it } from 'node:test';
import { fileURLToPath } from 'node:url';
import { EDITABLE_CHARS, approvalCard, approvalNote, approvedText } from '../src/actions/approval.mjs';
import { ActionService } from '../src/actions/service.mjs';
import { startActionApi, stopActionApi } from '../src/actions/api.mjs';
import { createMcpHandler } from '../src/actions/mcp.mjs';
import { TOOLS, publicTool } from '../src/actions/tools.mjs';
import { resolveCardAction } from '../src/card-messages.mjs';
import { formatCognitionJournalLine } from '../src/cognition.mjs';
import { eventMessage } from '../src/connectors/protocol.mjs';
import { normalizeEvent } from '../src/event.mjs';
import { Harness } from '../src/harness.mjs';
import { formatInboundLine } from '../src/runtime-input.mjs';
import { personDirectory, personId } from '../src/ov-person.mjs';
import { localSocketPath, supportsFileModes } from '../src/platform/index.mjs';
import { Store } from '../src/store.mjs';
import { LarkClient } from '../src/lark.mjs';
import { chatEvent, mailEvent, subjectEvent } from './event-fixture.mjs';
import { fakeConnectors, larkConnectors } from './action-fixture.mjs';

const TOKEN = 'runtime-token';
const OWNER = { id: 'ou_owner12345678', target: { user_id: 'ou_owner12345678' } };
const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };
const mcpPath = fileURLToPath(new URL('../src/actions/mcp.mjs', import.meta.url));

function setup(t, { ids = ['lark'], run = null, roster = null, liveness = null, meetingEnabled = false, ovClient = null,
  ovTools = null, openVikingConfigPath = undefined,
  owners = { lark: OWNER }, channel = 'lark', profile = undefined, explicitSilence = false, stagedReplies = false,
  larkMessageAccess = 'owner', onNoProgress = null, connectors: given = null } = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'actions-'));
  const workspace = path.join(directory, 'workspace');
  fs.mkdirSync(workspace);
  const store = new Store(path.join(directory, 'state.sqlite3'), logger);
  store.initializeCognition('codex', TOKEN);
  const connectors = given ?? fakeConnectors({ ids, run, owners });
  const logs = [];
  const record = (level) => (message, fields = {}) => logs.push({ level, message, ...fields });
  const service = new ActionService({
    config: { workspace, tempDir: path.join(directory, 'tmp'), maxReplyChars: 1_000, channel, profile, explicitSilence, stagedReplies,
      larkMessageAccess, ...(openVikingConfigPath !== undefined ? { openVikingConfigPath } : {}) },
    store, connectors, roster, liveness, meetingEnabled, ovClient, ovTools, onNoProgress,
    logger: { debug() {}, info: record('info'), warn: record('warn'), error: record('error'), child() { return this; } },
  });
  t.after(() => { store.close(); fs.rmSync(directory, { recursive: true, force: true }); });
  const call = (tool, input, authority = TOKEN) => service.call(tool, input, { authority });
  const inbound = (fields) => store.ingest(chatEvent({ chatType: 'group', ...fields })).inbound;
  return { directory, workspace, store, connectors, service, call, inbound, logs };
}

describe('agent tools', () => {
  it('offers only the tools some configured connector supports', (t) => {
    const names = (service) => service.tools().map((tool) => tool.name);
    const lark = setup(t, { meetingEnabled: true });
    assert.ok(names(lark.service).includes('react_to_message'));
    assert.ok(names(lark.service).includes('list_chats'));
    assert.ok(names(lark.service).includes('read_meeting_inbox'));
    assert.ok(!names(lark.service).includes('list_chat_members'));
    assert.ok(names(setup(t, { ids: ['slack'] }).service).includes('list_chat_members'));
    const weixin = setup(t, { ids: ['weixin'] });
    assert.deepEqual(names(weixin.service), [
      'send_message', 'notify_owner', 'reply_message', 'get_message_media', 'read_openviking_session_context',
    ]);
    assert.ok(names(lark.service).includes('read_openviking_session_context'));
    // stay_silent is opt-in per instance.
    assert.equal(names(lark.service).includes('stay_silent'), false);
    assert.deepEqual(names(setup(t, { ids: ['weixin'], explicitSilence: true }).service), [
      'send_message', 'notify_owner', 'reply_message', 'stay_silent', 'get_message_media', 'read_openviking_session_context',
    ]);
  });

  it('keeps a bot-only Lark instance inside the chats the bot has joined: no owner identity, and search as the bot', async (t) => {
    const empty = async () => ({ messages: [], has_more: false });
    const { service, call, connectors } = setup(t, { larkMessageAccess: 'bot', run: empty });
    const tools = new Map(service.tools().map((tool) => [tool.name, tool]));
    for (const name of ['list_chat_messages', 'list_thread_messages']) {
      assert.deepEqual(tools.get(name).inputSchema.properties.as.enum, ['bot'], name);
    }
    assert.match(tools.get('search_messages').description, /^Search messages in the chats the bot has joined \(bot identity\)/);
    await assert.rejects(call('list_chat_messages', { source: 'lark', chat_id: 'oc_test12345678', as: 'user' }),
      { code: 'invalid_input', message: /as must be one of bot/ });
    await call('list_chat_messages', { source: 'lark', chat_id: 'oc_test12345678' });
    await call('search_messages', { query: 'release' });
    assert.deepEqual(connectors.calls, [
      { connector: 'lark', action: 'chat.messages', input: { chat_id: 'oc_test12345678' } },
      { connector: 'lark', action: 'message.search', input: { query: 'release', as: 'bot' } },
    ]);

    // By default the owner's visibility is there, as before.
    const owner = setup(t, { run: empty });
    const offered = new Map(owner.service.tools().map((tool) => [tool.name, tool]));
    assert.deepEqual(offered.get('list_chat_messages').inputSchema.properties.as.enum, ['bot', 'user']);
    assert.match(offered.get('search_messages').description, /^Search messages across all chats visible to the user \(user identity\)/);
    await owner.call('search_messages', { query: 'release' });
    assert.deepEqual(owner.connectors.calls, [{ connector: 'lark', action: 'message.search', input: { query: 'release' } }]);

    // The Lark Connector searches with the identity it is given, the owner's by default.
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'actions-lark-'));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    const lark = larkConnectors({ tempDir: directory, run: async () => ({ code: 0, stdout: JSON.stringify({ ok: true, data: { messages: [] } }), stderr: '' }) });
    const real = setup(t, { larkMessageAccess: 'bot', connectors: lark });
    await real.call('search_messages', { query: 'release' });
    const args = lark.calls.at(-1).args;
    assert.deepEqual(args.slice(args.indexOf('+messages-search'), args.indexOf('+messages-search') + 3), ['+messages-search', '--as', 'bot']);
  });

  it('validates input against the tool schema before any connector call', async (t) => {
    const { call, connectors, inbound } = setup(t);
    inbound({ messageId: 'om_a' });
    await assert.rejects(() => call('send_message', { chat_id: 'oc_chat12345678', text: 'legacy', decision_id: 'message:om_a' }), /unknown field: text/);
    await assert.rejects(() => call('send_message', { chat_id: 'oc_chat12345678', markdown: 'x' }), /decision_id is required/);
    await assert.rejects(() => call('react_to_message', { message_id: 'om_a', emoji_type: '1bad', decision_id: 'message:om_a' }), /emoji_type has an invalid format/);
    await assert.rejects(() => call('send_message', { chat_id: 'oc_chat12345678', decision_id: 'message:om_a' }), /message body is required/);
    await assert.rejects(() => call('no_such_tool', {}), /unknown tool/);
    assert.equal(connectors.calls.length, 0);
  });

  it('fences a retired runtime out of reads and writes', async (t) => {
    const { store, call, connectors, inbound } = setup(t);
    inbound({ messageId: 'om_decision123456' });
    store.initializeCognition('claude', 'new-owner');
    await assert.rejects(() => call('list_chats', {}), /no longer owns/);
    await assert.rejects(() => call('send_message', {
      chat_id: 'oc-chat', markdown: 'late', decision_id: 'message:om_decision123456',
    }), /no longer owns/);
    assert.equal(connectors.calls.length, 0);
  });

  it('keeps empty Slack pages pageable, rejects unsupported Lark list cursors and fences query results after runtime retirement', async (t) => {
    let finish;
    const { call, service, store, connectors } = setup(t, { ids: ['lark', 'slack'], run: async (_id, _action, input) => {
      if (input.query === 'pending') return new Promise(resolve => { finish = resolve; });
      return { chats: [], has_more: true, next_cursor: 'next=' };
    } });
    assert.match((await call('list_chats', { source: 'slack', query: 'design' })).text, /cursor="next="/);
    await assert.rejects(call('list_chats', { source: 'lark', cursor: 'next=' }), /only for Slack queries and Lark chat\/thread/);
    assert.equal(connectors.calls.length, 1);
    const pending = call('list_chats', { source: 'slack', query: 'pending' });
    store.initializeCognition('claude', 'new-owner');
    finish({ chats: [{ chat_id: 'C0OLD', name: 'old' }] });
    await assert.rejects(pending, /no longer owns/);
    assert.equal(service.queryTargets.size, 0);
  });

  it('pages Lark chat and thread history through the tool and CLI without changing identity', async (t) => {
    for (const [tool, target, command, order] of [
      ['list_chat_messages', { chat_id: 'oc_test12345678' }, '+chat-messages-list', 'desc'],
      ['list_thread_messages', { thread_id: 'omt_test12345678' }, '+threads-messages-list', 'asc'],
    ]) {
      for (const as of ['bot', 'user']) {
        const pages = [
          { messages: [{ message_id: 'om_first12345678' }], has_more: true, page_token: 'opaque+/token=' },
          { messages: [], has_more: true, page_token: 'empty-page-next=' },
          { items: [{ message_id: 'om_last123456789' }], has_more: false, page_token: 'stale-token' },
        ];
        const connectors = larkConnectors({ tempDir: os.tmpdir(), run: async () => ({
          code: 0, stdout: JSON.stringify({ ok: true, data: pages.shift() }), stderr: '',
        }) });
        const { call, service } = setup(t, { connectors, larkMessageAccess: as === 'bot' ? 'bot' : 'owner' });
        const input = { source: 'lark', ...target, limit: 2, as };
        const schema = service.tools().find(item => item.name === tool).inputSchema;
        assert.match(schema.properties.cursor.description, /Lark or Slack/);
        const first = await call(tool, input);
        assert.deepEqual(first.data.messages.map(item => item.message_id), ['om_first12345678']);
        assert.equal(first.data.next_cursor, 'opaque+/token=');
        assert.match(first.text, /cursor="opaque\+\/token="/);
        const middle = await call(tool, { ...input, cursor: first.data.next_cursor });
        assert.equal(middle.data.messages.length, 0);
        assert.equal(middle.data.has_more, true);
        assert.match(middle.text, /cursor="empty-page-next="/);
        const last = await call(tool, { ...input, cursor: middle.data.next_cursor });
        assert.deepEqual(last.data.messages.map(item => item.message_id), ['om_last123456789']);
        assert.equal(last.data.has_more, false);
        assert.equal(last.data.next_cursor, '');
        assert.doesNotMatch(last.text, /More results|cursor=/);
        assert.equal(connectors.calls.length, 3);
        for (const { args } of connectors.calls) {
          assert.ok(args.includes(command));
          assert.equal(args[args.indexOf('--as') + 1], as);
          assert.equal(args[args.indexOf('--page-size') + 1], '2');
          assert.ok(args.includes(order));
          assert.ok(args.includes(Object.values(target)[0]));
          assert.ok(!args.includes('--page-all'));
        }
        assert.ok(!connectors.calls[0].args.includes('--page-token'));
        assert.deepEqual(connectors.calls[1].args.slice(-2), ['--page-token', first.data.next_cursor]);
        assert.deepEqual(connectors.calls[2].args.slice(-2), ['--page-token', middle.data.next_cursor]);
        if (as === 'bot') {
          await assert.rejects(call(tool, { ...input, as: 'user', cursor: first.data.next_cursor }), /as must be one of bot/);
          assert.equal(connectors.calls.length, 3);
        }
      }
    }
  });

  it('separates response attribution from the latest freshness fence', async (t) => {
    const { store, call, connectors, inbound, logs } = setup(t);
    const older = inbound({ messageId: 'om_olderDecision1', chatId: 'oc_steer12345678' });
    const olderTwo = inbound({ messageId: 'om_otherOlder1234', chatId: 'oc_steer12345678' });
    const latest = inbound({ messageId: 'om_latestDecision', chatId: 'oc_steer12345678' });
    inbound({ messageId: 'om_otherScope1234', chatId: 'oc_other12345678' });

    await call('send_message', { chat_id: 'oc_steer12345678', markdown: 'fast answer B', decision_id: 'message:om_latestDecision' });
    await call('send_message', { chat_id: 'oc_steer12345678', markdown: 'finished answer A',
      decision_id: 'message:om_latestDecision', response_to_id: 'message:om_olderDecision1' });
    await call('reply_message', { message_id: 'om_otherOlder1234', markdown: 'answer A2', decision_id: 'message:om_latestDecision' });
    assert.deepEqual(
      store.db.prepare('SELECT inbound_seq FROM outbox ORDER BY id').all().map((row) => row.inbound_seq),
      [latest.seq, older.seq, olderTwo.seq],
    );

    await assert.rejects(() => call('send_message', { chat_id: 'oc_steer12345678', markdown: 'cross scope',
      decision_id: 'message:om_latestDecision', response_to_id: 'message:om_otherScope1234' }), /same chat\/thread or subject, no newer/);
    await assert.rejects(() => call('send_message', { chat_id: 'oc_steer12345678', markdown: 'future',
      decision_id: 'message:om_olderDecision1', response_to_id: 'message:om_latestDecision' }), /newer authenticated input/);
    await assert.rejects(() => call('send_message', { chat_id: 'oc_steer12345678', markdown: 'stale',
      decision_id: 'message:om_olderDecision1' }), /newer authenticated input/);
    await assert.rejects(() => call('send_message', { chat_id: 'oc_steer12345678', markdown: 'unknown',
      decision_id: 'message:om_neverReceived1' }), /not the event= of an accepted daemon-authenticated input/);
    assert.equal(connectors.calls.length, 3, 'invalid attribution and stale fences stop before the connector');

    // Each call leaves one agent_tool line; refusals carry the seqs that explain them.
    const lines = logs.filter((line) => line.message === 'agent_tool');
    assert.deepEqual(lines.slice(0, 3).map((line) => [line.outcome, line.decisionSeq, line.responseSeq]),
      [['ok', latest.seq, latest.seq], ['ok', latest.seq, older.seq], ['ok', latest.seq, olderTwo.seq]]);
    assert.ok(lines.slice(0, 3).every((line) => line.outboxId && line.attempt === 1 && line.connector === 'lark'));
    assert.deepEqual(lines.slice(3).map((line) => [line.outcome, line.code, line.decisionSeq ?? null, line.newerSeq ?? null]), [
      ['blocked', 'attribution_rejected', latest.seq, null],
      ['blocked', 'stale_decision', older.seq, latest.seq],
      ['blocked', 'stale_decision', older.seq, latest.seq],
      ['blocked', 'unknown_decision', null, null],
    ]);
    assert.doesNotMatch(JSON.stringify(logs), /oc_steer|om_latest|fast answer/);
    const blockedRows = store.db.prepare("SELECT inbound_seq, detail_json FROM activities WHERE kind = 'delivery.blocked' ORDER BY id").all();
    assert.equal(blockedRows.length, 4);
    assert.deepEqual(JSON.parse(blockedRows[1].detail_json), {
      tool: 'send_message', code: 'stale_decision', connector: 'lark', via: null, runtime: 'codex',
      decisionSeq: older.seq, newerSeq: latest.seq,
    });
  });

  it('reserves every delivery in the outbox and journal; a repeat returns the earlier result', async (t) => {
    const { store, call, connectors, inbound } = setup(t);
    inbound({ messageId: 'om_decision123456', chatId: 'oc_chat12345678' });
    const input = { chat_id: 'oc_chat12345678', markdown: 'hello', decision_id: 'message:om_decision123456' };
    const first = await call('send_message', input);
    assert.match(first.text, /^Delivered message to chat_id=oc_chat12345678\. message_id=om_delivered123456/);
    const [{ connector, action, input: sent }] = connectors.calls;
    assert.deepEqual([connector, action], ['lark', 'message.send']);
    assert.deepEqual(sent.content, { type: 'markdown', text: 'hello' });
    assert.match(sent.idempotency_key, /^ld_[a-f0-9]{40}$/);

    const row = store.db.prepare('SELECT * FROM outbox').get();
    assert.deepEqual([row.status, row.caller, row.remote_message_id], ['SENT', 'agent', 'om_delivered123456']);
    const journal = store.db.prepare("SELECT * FROM cognition_journal WHERE kind = 'outbound'").get();
    assert.deepEqual([journal.status, journal.message_id, journal.chat_id, journal.runtime_token, journal.text],
      ['COMMITTED', 'om_delivered123456', 'oc_chat12345678', TOKEN, 'hello']);
    assert.deepEqual(store.db.prepare("SELECT kind FROM activities WHERE outbox_id = ? ORDER BY id").all(row.id)
      .map((item) => item.kind), ['delivery.queued', 'delivery.sent']);

    const repeat = await call('send_message', input);
    assert.equal(repeat.data.duplicate, true);
    assert.match(repeat.text, /Already delivered/);
    assert.equal(connectors.calls.length, 1);

    await call('send_message', { ...input, idempotency_key: 'intentional-second' });
    assert.equal(connectors.calls.length, 2);
    assert.notEqual(connectors.calls[1].input.idempotency_key, sent.idempotency_key);
  });

  it('retries an uncertain action only where the provider honours the idempotency key', async (t) => {
    let fail = true;
    const run = (_connector, action) => {
      if (fail) throw new Error('timed out');
      return action === 'message.react' ? { reaction_id: 'r1' } : { message_id: 'om_delivered123456' };
    };
    const { store, call, connectors, inbound } = setup(t, { ids: ['lark', 'weixin'], run });
    inbound({ messageId: 'om_decision123456', chatId: 'oc_chat12345678' });
    inbound({ source: 'weixin', messageId: 'wx_peer_1', chatId: 'peer@im.wechat', chatType: 'p2p' });
    const send = { chat_id: 'oc_chat12345678', markdown: 'once', decision_id: 'message:om_decision123456' };
    const react = { message_id: 'om_decision123456', emoji_type: 'OK', decision_id: 'message:om_decision123456' };
    const weixin = { message_id: 'wx_peer_1', markdown: 'hi', decision_id: 'message:wx_peer_1' };
    for (const [tool, input] of [['send_message', send], ['react_to_message', react], ['reply_message', weixin]]) {
      await assert.rejects(() => call(tool, input), /timed out/);
    }
    assert.deepEqual(store.stats().outbox, { UNCERTAIN: 3 });
    assert.equal(store.cognitionJournal().filter((item) => item.status === 'UNCERTAIN').length, 3);

    fail = false;
    const retried = await call('send_message', send);
    assert.equal(retried.data.message_id, 'om_delivered123456');
    assert.equal(connectors.calls[3].input.idempotency_key, connectors.calls[0].input.idempotency_key);
    assert.equal(store.db.prepare("SELECT attempt_count FROM outbox WHERE action = 'message.send'").get().attempt_count, 2);
    await assert.rejects(() => call('react_to_message', react), /outcome is uncertain/);
    await assert.rejects(() => call('reply_message', weixin), /outcome is uncertain/);
    assert.equal(connectors.calls.length, 4);
  });

  it('routes member queries without delivery and validates pagination and runtime authority', async (t) => {
    const { call, service, store, connectors, inbound } = setup(t, { ids: ['lark', 'slack'], run: (_connector, action, input) => {
      assert.equal(action, 'chat.members.list');
      return { chat_id: input.chat_id, members: [
        { user_id: 'U0BOB12345', name: 'Bob', is_bot: false, is_self: false, deleted: false },
        { user_id: 'U0UNKNOWN', name: null, is_bot: null, is_self: false, deleted: null, profile_error: 'user_not_found' },
      ], has_more: true, next_cursor: 'page-two=' };
    } });
    inbound({ source: 'slack', messageId: 'sl:T0TEAM:C0GENERAL:1.000001', chatId: 'C0GENERAL' });
    inbound({ messageId: 'om_lark12345678', chatId: 'oc_chat12345678' });
    const definition = service.tools().find((tool) => tool.name === 'list_chat_members');
    assert.equal(definition.annotations.readOnlyHint, true);
    const result = await call('list_chat_members', { chat_id: 'C0GENERAL', limit: 2, cursor: 'page-one=' });
    assert.deepEqual(connectors.calls, [{ connector: 'slack', action: 'chat.members.list',
      input: { chat_id: 'C0GENERAL', limit: 2, cursor: 'page-one=' } }]);
    assert.match(result.text, /user_id=U0BOB12345.*name="Bob".*is_bot=false/);
    assert.match(result.text, /More members exist[\s\S]*page-two=/);
    assert.match(result.text, /Some profiles could not be read/);
    assert.equal(result.data.members[1].is_bot, null);
    assert.equal(store.db.prepare('SELECT count(*) AS n FROM outbox').get().n, 0);
    for (const input of [{}, { chat_id: 'C0GENERAL', limit: 201 }, { chat_id: 'C0GENERAL', cursor: '\n' }]) {
      await assert.rejects(call('list_chat_members', input), (error) => error.code === 'invalid_input');
    }
    await assert.rejects(call('list_chat_members', { chat_id: 'C0GENERAL' }, 'retired-runtime'), /no longer owns/);
    await assert.rejects(call('list_chat_members', { chat_id: 'oc_chat12345678' }), /lark does not support/);
    assert.equal(connectors.calls.length, 1);
  });

  it('routes by a known target id, an explicit source, or the only capable connector', async (t) => {
    const { call, connectors, inbound } = setup(t, { ids: ['lark', 'weixin'],
      run: (_connector, action) => (action === 'chat.list' ? { chats: [], has_more: false } : { message_id: 'om_delivered123456' }) });
    inbound({ messageId: 'om_decision123456', chatId: 'oc_chat12345678' });
    inbound({ source: 'weixin', messageId: 'wx_peer_1', chatId: 'peer@im.wechat', chatType: 'p2p' });

    await call('reply_message', { message_id: 'wx_peer_1', markdown: 'hi', decision_id: 'message:wx_peer_1' });
    await call('reply_message', { message_id: 'om_decision123456', markdown: 'hi', decision_id: 'message:om_decision123456' });
    await call('list_chats', {});
    assert.deepEqual(connectors.calls.map((item) => [item.connector, item.action]), [
      ['weixin', 'message.reply'], ['lark', 'message.reply'], ['lark', 'chat.list'],
    ]);

    await assert.rejects(() => call('react_to_message', { message_id: 'wx_peer_1', emoji_type: 'OK', decision_id: 'message:wx_peer_1' }),
      /weixin does not support message.react/);
    await assert.rejects(() => call('send_message', { chat_id: 'oc_neverSeen1234', markdown: 'x', decision_id: 'message:om_decision123456' }),
      /pass source \(one of: lark, weixin\)/);
    await call('send_message', { source: 'lark', chat_id: 'oc_neverSeen1234', markdown: 'x', decision_id: 'message:om_decision123456' });
    assert.equal(connectors.calls.at(-1).connector, 'lark');
  });

  it('checks message content against the connector manifest', async (t) => {
    const { call, connectors, inbound } = setup(t, { ids: ['weixin'] });
    inbound({ source: 'weixin', messageId: 'wx_peer_1', chatId: 'peer@im.wechat', chatType: 'p2p' });
    const base = { message_id: 'wx_peer_1', decision_id: 'message:wx_peer_1' };
    await assert.rejects(() => call('reply_message', { ...base, card: { elements: [] } }), /weixin cannot send card messages/);
    await assert.rejects(() => call('reply_message', { ...base, markdown: 'x', mention_user_ids: ['ou_someone12345'] }),
      /mentions are not supported/);
    await assert.rejects(() => call('reply_message', { ...base, markdown: 'x', reply_in_thread: true }), /does not support thread replies/);
    assert.equal(connectors.calls.length, 0);
  });

  it('freezes a workspace file for the upload and removes the snapshot afterwards', async (t) => {
    let seen;
    const { call, workspace, directory, inbound } = setup(t, { run: (_connector, _action, input) => {
      seen = { ...input.content, bytes: fs.readFileSync(input.content.path, 'utf8') };
      return { message_id: 'om_delivered123456' };
    } });
    inbound({ messageId: 'om_decision123456', chatId: 'oc_chat12345678' });
    fs.writeFileSync(path.join(workspace, 'chart.png'), 'png bytes');
    await call('send_message', { chat_id: 'oc_chat12345678', file_path: 'chart.png', decision_id: 'message:om_decision123456' });
    assert.equal(seen.type, 'file');
    assert.equal(seen.name, 'chart.png');
    assert.equal(seen.bytes, 'png bytes');
    assert.ok(seen.path.startsWith(path.join(directory, 'tmp', 'outbox')));
    assert.equal(fs.existsSync(seen.path), false);
  });

  it('refuses a video container Lark cannot send before any outbox entry, and still takes the mp4', async (t) => {
    const { call, workspace, connectors, inbound, store } = setup(t);
    inbound({ messageId: 'om_decision123456', chatId: 'oc_chat12345678' });
    for (const name of ['clip.webm', 'clip.mp4']) fs.writeFileSync(path.join(workspace, name), 'video');
    const send = (file) => call('send_message', { chat_id: 'oc_chat12345678', file_path: file, decision_id: 'message:om_decision123456' });
    await assert.rejects(send('clip.webm'), /Lark cannot send clip\.webm.*\.mp4.*No action was taken/);
    assert.equal(connectors.calls.length, 0);
    assert.equal(store.db.prepare('SELECT count(*) AS n FROM outbox').get().n, 0);
    await send('clip.mp4');
    assert.deepEqual(connectors.calls.map(({ input }) => input.content.name), ['clip.mp4']);
  });

  for (const tool of ['send_message', 'reply_message', 'notify_owner']) {
    for (const [name, flag] of [['chart 图.png', '--image'], ['report.pdf', '--file']]) {
      for (const fails of [false, true]) {
        it(`${tool} uploads ${name} from configured private temp storage and cleans up on ${fails ? 'failure' : 'success'}`, async (t) => {
          let lark;
          const { call, workspace, service, inbound } = setup(t, {
            run: (_connector, action, input) => action === 'message.reply' ? lark.replyMessage(input) : lark.sendMessage(input),
          });
          const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-upload-'));
          t.after(() => fs.rmSync(tempDir, { recursive: true, force: true }));
          service.config.dataDir = '/root/.ov-superagent/data/primary';
          service.config.tempDir = tempDir;
          let snapshot;
          lark = new LarkClient({ ...service.config, profile: 'test', larkCliBin: '/fake/lark-cli', sendTimeoutMs: 5000 }, {
            async run(_bin, args, { cwd }) {
              assert.equal(args[3], tool === 'reply_message' ? '+messages-reply' : '+messages-send');
              assert.equal(args[args.indexOf(flag) + 1], `./${name}`);
              assert.equal(path.dirname(cwd), path.join(tempDir, 'outbox'));
              snapshot = path.join(cwd, name);
              if (supportsFileModes) {
                for (const dir of [tempDir, path.dirname(cwd), cwd]) {
                  assert.equal(fs.statSync(dir).mode & 0o777, 0o700);
                }
                assert.equal(fs.statSync(snapshot).mode & 0o777, 0o600);
              }
              assert.equal(fs.readFileSync(snapshot, 'utf8'), 'original bytes');
              return fails
                ? { code: 1, stdout: '', stderr: 'simulated upload failure' }
                : { code: 0, stdout: JSON.stringify({ ok: true, data: { message_id: 'om_delivered123456' } }), stderr: '' };
            },
          }, logger);
          inbound({ messageId: 'om_decision123456', chatId: 'oc_chat12345678' });
          const source = path.join(workspace, name);
          fs.writeFileSync(source, 'original bytes');
          const target = tool === 'send_message' ? { chat_id: 'oc_chat12345678' }
            : tool === 'reply_message' ? { message_id: 'om_decision123456' } : {};
          const send = () => call(tool, { ...target, file_path: name, decision_id: 'message:om_decision123456' });
          if (fails) await assert.rejects(send, /simulated upload failure/);
          else await send();
          assert.ok(snapshot);
          assert.equal(fs.existsSync(snapshot), false);
          assert.deepEqual(fs.readdirSync(path.join(tempDir, 'outbox')), []);
          assert.equal(fs.readFileSync(source, 'utf8'), 'original bytes');
        });
      }
    }
  }

  it('rejects a shared temp root or symlinked outbox before uploading', async (t) => {
    const { call, workspace, directory, service, connectors, inbound } = setup(t);
    inbound({ messageId: 'om_decision123456', chatId: 'oc_chat12345678' });
    fs.writeFileSync(path.join(workspace, 'chart.png'), 'image');
    const tempDir = service.config.tempDir;
    fs.mkdirSync(tempDir);
    const send = () => call('send_message', { chat_id: 'oc_chat12345678', file_path: 'chart.png', decision_id: 'message:om_decision123456' });
    if (supportsFileModes) {
      fs.chmodSync(tempDir, 0o755);
      await assert.rejects(send, /mode 0700/);
      assert.equal(fs.statSync(tempDir).mode & 0o777, 0o755);
      fs.chmodSync(tempDir, 0o700);
    }
    fs.symlinkSync(directory, path.join(tempDir, 'outbox'), 'junction');
    await assert.rejects(send, /Temporary directory must be a directory/);
    assert.equal(connectors.calls.length, 0);
  });

  it('resolves mention names through the roster of the target chat', async (t) => {
    const lookups = [];
    const roster = { async resolveNames(chatId, names) {
      lookups.push(chatId);
      return names.map((name) => ({ name, id: 'ou_alice12345678' }));
    } };
    const { call, connectors, inbound } = setup(t, { roster });
    inbound({ messageId: 'om_target12345678', chatId: 'oc_decision123456' });
    inbound({ messageId: 'om_decision123456', chatId: 'oc_decision123456' });
    await call('reply_message', { message_id: 'om_target12345678', markdown: '@Alice please check',
      mention_names: ['Alice'], mention_user_ids: ['ou_bob1234567890'], decision_id: 'message:om_decision123456' });
    assert.deepEqual(lookups, ['oc_decision123456']);
    assert.deepEqual(connectors.calls[0].input.content.mentions, [
      { id: 'ou_bob1234567890' }, { name: 'Alice', id: 'ou_alice12345678' },
    ]);
  });

  it('re-checks freshness and authority after an awaited mention lookup', async (t) => {
    let finishLookup = null;
    const roster = { resolveNames: (_chatId, names) => new Promise((resolve) => {
      finishLookup = () => resolve(names.map((name) => ({ name, id: 'ou_alice12345678' })));
    }) };
    const { store, call, connectors, inbound } = setup(t, { roster });
    inbound({ messageId: 'om_decision123456', chatId: 'oc_chat12345678' });

    const stale = call('send_message', { chat_id: 'oc_chat12345678', markdown: '@Alice hi',
      mention_names: ['Alice'], decision_id: 'message:om_decision123456' });
    inbound({ messageId: 'om_newer123456789', chatId: 'oc_chat12345678' });
    finishLookup();
    await assert.rejects(stale, (error) => error.code === 'stale_decision');

    const revoked = call('reply_message', { message_id: 'om_newer123456789', markdown: '@Alice hi',
      mention_names: ['Alice'], decision_id: 'message:om_newer123456789' });
    store.initializeCognition('claude', 'new-owner');
    finishLookup();
    await assert.rejects(revoked, (error) => error.code === 'authority_revoked');

    assert.equal(connectors.calls.length, 0);
    assert.equal(store.db.prepare('SELECT COUNT(*) AS n FROM outbox').get().n, 0);
  });

  it('leaves reply_message undecided when local freshness blocks it', async (t) => {
    const { store, call, inbound } = setup(t);
    const older = inbound({ messageId: 'om_olderFresh1234', chatId: 'oc_chat12345678', createTimeMs: 1_000 });
    const latest = inbound({ messageId: 'om_latestFresh1234', chatId: 'oc_chat12345678', createTimeMs: 2_000 });
    const reply = (row) => call('reply_message', {
      message_id: row.messageId, markdown: 'reconsidered answer', decision_id: row.eventId,
    });

    await assert.rejects(reply(older), (error) => error.code === 'stale_decision');
    assert.equal(store.decisionFor(older.connectorInstance, older.eventId), null,
      'a stale reply is not a delivered decision');
    await reply(latest);
    assert.equal(store.decisionFor(latest.connectorInstance, latest.eventId).outcome, 'delivered');

  });
  it('blocks a delivery to a Connector that is down without reserving it, so the retry goes through', async (t) => {
    const { store, call, connectors } = setup(t, { ids: ['lark', 'weixin'] });
    let weixinUp = false;
    connectors.available = (id) => id !== 'weixin' || weixinUp;
    store.ingest(chatEvent({ source: 'weixin', messageId: 'wx-decision-0001', chatId: 'wx-peer' }));
    const reply = () => call('reply_message', { message_id: 'wx-decision-0001', markdown: '收到',
      decision_id: 'message:wx-decision-0001' });

    await assert.rejects(reply(), (error) => error.code === 'connector_unavailable'
      && /weixin connector is temporarily unavailable.*No action was taken/.test(error.message));
    assert.equal(connectors.calls.length, 0);
    assert.equal(store.db.prepare('SELECT COUNT(*) AS n FROM outbox').get().n, 0);
    const [blocked] = store.listActivities({ limit: 10 }).activities.filter((activity) => activity.kind === 'delivery.blocked');
    assert.equal(blocked.metadata.code, 'connector_unavailable');

    weixinUp = true;
    await reply();
    assert.deepEqual(connectors.calls.map((entry) => [entry.connector, entry.action]), [['weixin', 'message.reply']]);
    assert.equal(store.db.prepare("SELECT status FROM outbox").get().status, 'SENT');
  });

  it('keeps one temporary working reaction per conversation and clears it on the next final delivery there', async (t) => {
    let reactions = 0;
    const run = (_connector, action) => {
      if (action === 'message.react') return { reaction_id: `reaction_${reactions += 1}` };
      if (action === 'message.reaction.delete') return { deleted: true };
      return { message_id: 'om_delivered123456' };
    };
    const { store, service, call, connectors, inbound } = setup(t, { run });
    inbound({ messageId: 'om_scopeA12345678', chatId: 'oc_scopeA12345678' });
    inbound({ messageId: 'om_scopeB12345678', chatId: 'oc_scopeB12345678' });
    await call('react_to_message', { message_id: 'om_scopeA12345678', emoji_type: 'OnIt', temporary: true, decision_id: 'message:om_scopeA12345678' });
    assert.equal(service.temporaryReactions.size, 1);

    await call('send_message', { chat_id: 'oc_scopeB12345678', markdown: 'other scope', decision_id: 'message:om_scopeB12345678' });
    assert.equal(service.temporaryReactions.size, 1, 'a delivery elsewhere must not clear it');

    await call('react_to_message', { message_id: 'om_scopeA12345678', emoji_type: 'OK', decision_id: 'message:om_scopeA12345678' });
    assert.equal(service.temporaryReactions.size, 0);
    const deletion = connectors.calls.find((item) => item.action === 'message.reaction.delete');
    assert.deepEqual(deletion.input, { message_id: 'om_scopeA12345678', reaction_id: 'reaction_1' });
    assert.deepEqual(store.cognitionJournal().filter((item) => item.kind === 'outbound').map((item) => item.text),
      ['other scope', '[reaction OK]'], 'the cleared working reaction leaves the observable history');

    await assert.rejects(() => call('react_to_message', { message_id: 'om_scopeA12345678', emoji_type: 'OK', temporary: true,
      decision_id: 'message:om_scopeA12345678' }), /must use the OnIt emoji/);
  });

  it('never reports a final delivery as failed because working reaction cleanup failed', async (t) => {
    const run = (_connector, action) => {
      if (action === 'message.reaction.delete') throw new Error('cleanup failed');
      return action === 'message.react' ? { reaction_id: 'reaction_cleanup' } : { message_id: 'om_delivered123456' };
    };
    const { service, call, inbound } = setup(t, { run });
    inbound({ messageId: 'om_cleanup1234567', chatId: 'oc_cleanup1234567' });
    await call('react_to_message', { message_id: 'om_cleanup1234567', emoji_type: 'OnIt', temporary: true, decision_id: 'message:om_cleanup1234567' });
    const sent = await call('send_message', { chat_id: 'oc_cleanup1234567', markdown: 'finished', decision_id: 'message:om_cleanup1234567' });
    assert.equal(sent.data.message_id, 'om_delivered123456');
    assert.equal(service.temporaryReactions.size, 0);
  });

  it('touches idle liveness after every successful delivery, including daemon replies', async (t) => {
    let touches = 0;
    const { service, call, inbound } = setup(t, { liveness: { touch() { touches += 1; } } });
    const event = inbound({ messageId: 'om_decision123456', chatId: 'oc_chat12345678' });
    await call('react_to_message', { message_id: 'om_decision123456', emoji_type: 'OK', decision_id: 'message:om_decision123456' });
    await service.replyToEvent(event, 'notice', 'failure-notice');
    assert.equal(touches, 2);
  });

  it('replies to an event on its own route once per purpose', async (t) => {
    const { store, service, connectors, inbound } = setup(t);
    const event = inbound({ messageId: 'om_decision123456', chatId: 'oc_chat12345678' });
    assert.deepEqual(await service.replyToEvent(event, 'first notice', 'failure-notice'), { ok: true });
    assert.deepEqual(await service.replyToEvent(event, 'second notice', 'failure-notice'), { ok: true });
    await service.replyToEvent(event, 'command reply', 'command-reply');
    assert.equal(connectors.calls.length, 2);
    assert.deepEqual(connectors.calls[0].input.content, { type: 'markdown', text: 'first notice' });
    assert.equal(connectors.calls[0].input.message_id, 'om_decision123456');
    assert.deepEqual(store.db.prepare('SELECT DISTINCT caller FROM outbox').all().map((row) => row.caller), ['daemon']);
    assert.deepEqual(await service.replyToEvent(chatEvent({ replyRoute: null }), 'x', 'p'), { ok: false, stage: 'no-target' });
  });

  it('does not reserve or invoke anything in dry-run mode', async (t) => {
    const { store, service, call, connectors, inbound } = setup(t);
    service.config.dryRun = true;
    inbound({ messageId: 'om_decision123456', chatId: 'oc_chat12345678' });
    const result = await call('send_message', { chat_id: 'oc_chat12345678', markdown: 'x', decision_id: 'message:om_decision123456' });
    assert.match(result.text, /Dry run only/);
    assert.equal(connectors.calls.length, 0);
    assert.deepEqual(store.stats().outbox, {});
  });

  it('reads and acknowledges the durable meeting inbox', async (t) => {
    const { store, call } = setup(t, { meetingEnabled: true });
    store.ensureMeetingSession('734000000000000123');
    store.ingestMeetingEvent({
      eventKey: 'meeting:test-transcript', meetingId: '734000000000000123',
      eventType: 'vc.bot.meeting_activity_v1', activityType: 'transcript_received', occurredAtMs: 1_723_300_000_000,
      payload: { transcript_received_items: [{ speaker: { user_name: 'Alice' }, text: '请把结论发到群里', sentence_id: 's-1' }] },
    });
    store.commitMeetingInboxBatch('734000000000000123', store.pendingMeetingEvents('734000000000000123'), 1_723_300_000_001);
    const first = await call('read_meeting_inbox', { limit: 10 });
    assert.match(first.text, /Read and acknowledged 1 meeting messages; remaining=0/);
    assert.match(first.text, /请把结论发到群里/);
    assert.match((await call('read_meeting_inbox', {})).text, /Meeting inbox is empty/);
  });

  it('pages OV session context consistently and fences authority after the remote read', async (t) => {
    let value = 'abcdef';
    const ovClient = { async context() { return { overview: value }; } };
    const { store, call } = setup(t, { ovClient });
    const { epoch } = store.getCognitionState();
    store.ovSessionFor = (requested) => (requested === epoch ? { session_id: 'test-session', uri: 'viking://session/test' } : null);
    const first = (await call('read_openviking_session_context', { epoch, max_chars: 4 })).data;
    const next = (await call('read_openviking_session_context', { epoch, offset: first.next_offset, expected_digest: first.expected_digest })).data;
    assert.equal(first.text + next.text, JSON.stringify({ overview: value }));
    await assert.rejects(() => call('read_openviking_session_context', { epoch, offset: 4 }), /requires expected_digest/);
    value = 'changed';
    await assert.rejects(() => call('read_openviking_session_context', { epoch, offset: 4, expected_digest: first.expected_digest }), /result changed/);
    ovClient.context = async () => {
      store.initializeCognition('claude', 'new-owner');
      return { overview: 'must not escape' };
    };
    await assert.rejects(() => call('read_openviking_session_context', { epoch }), /no longer owns/);
  });

  it('reads OV archives within the session of its epoch and never returns Working Memory', async (t) => {
    let seen;
    const ovClient = {
      async archive(...args) { seen = args; return { archive_id: args[1], messages: [] }; },
      async context() {
        return { latest_archive_overview: 'bloated summary', pre_archive_abstracts: [], messages: [{ message_kind: 'checkpoint', parts: [] }] };
      },
    };
    const { store, call } = setup(t, { ovClient });
    const { epoch } = store.getCognitionState();
    store.ovSessionFor = (requested) => (requested === epoch ? { session_id: 'test-session', uri: 'viking://session/test' } : null);
    const result = (await call('read_openviking_session_context', { epoch, archive_id: 'archive_002' })).data;
    assert.deepEqual(seen, ['viking://session/test', 'archive_002']);
    assert.equal(result.text, JSON.stringify({ archive_id: 'archive_002', messages: [] }));
    assert.equal((await call('read_openviking_session_context', { epoch })).data.text, JSON.stringify({ messages: [] }));
  });
  it('keeps a thread\'s answer in its thread: a send back to that chat is refused with the reply that fits', async (t) => {
    const { call, connectors, inbound, store } = setup(t, { ids: ['lark', 'slack'] });
    inbound({ messageId: 'om_inThread12345', chatId: 'oc_group12345678', threadId: 'omt_topic1234567' });
    inbound({ messageId: 'om_mainChat12345', chatId: 'oc_group12345678' });
    // A card action's msg= is internal; the reply goes to its card.
    inbound({ messageId: 'ca_cardAction123', chatId: 'oc_group12345678', threadId: 'omt_topic7654321',
      messageType: 'card_action', parentId: 'om_card123456789' });
    inbound({ source: 'slack', messageId: 'sl:T1:C1:1700000001.000200', chatId: 'C1', threadId: '1700000000.000100',
      eventId: 'T1:C1:1700000001.000200' });
    const refused = (pattern) => (error) => error.code === 'thread_target_mismatch' && pattern.test(error.message);
    await assert.rejects(call('send_message', { chat_id: 'oc_group12345678', markdown: 'answer', decision_id: 'message:om_inThread12345' }),
      refused(/reply_message, message_id=om_inThread12345, reply_in_thread=true/));
    await assert.rejects(call('send_message', { chat_id: 'oc_group12345678', markdown: 'clicked', decision_id: 'message:ca_cardAction123' }),
      refused(/message_id=om_card123456789,/));
    await assert.rejects(call('send_message', { source: 'slack', chat_id: 'C1', markdown: 'answer', decision_id: 'T1:C1:1700000001.000200' }),
      refused(/message_id=sl:T1:C1:1700000001\.000200,/));
    assert.equal(connectors.calls.length, 0, 'a refused send reaches no connector');
    assert.equal(store.db.prepare("SELECT COUNT(*) AS n FROM activities WHERE kind = 'delivery.blocked' AND detail_json LIKE '%thread_target_mismatch%'").get().n, 3);

    // The answer inside the thread, a main-chat message's own send, and a send the
    // task needs elsewhere (someone's direct chat) all go through.
    await call('reply_message', { message_id: 'om_inThread12345', markdown: 'answer', reply_in_thread: true, decision_id: 'message:om_inThread12345' });
    await call('send_message', { chat_id: 'oc_group12345678', markdown: 'answer', decision_id: 'message:om_mainChat12345' });
    await call('send_message', { source: 'lark', chat_id: 'oc_p2pSomeone1234', markdown: 'the file', decision_id: 'message:om_inThread12345' });
    assert.deepEqual(connectors.calls.map((item) => [item.action, item.input.message_id ?? item.input.chat_id]), [
      ['message.reply', 'om_inThread12345'],
      ['message.send', 'oc_group12345678'],
      ['message.send', 'oc_p2pSomeone1234'],
    ]);
  });

});

describe('OpenViking tools', () => {
  const ovNames = (service) => service.tools().map((tool) => tool.name).filter((name) => name.startsWith('openviking_'));
  const OV_TOOLS = ['openviking_find', 'openviking_search', 'openviking_read', 'openviking_list', 'openviking_add_resource', 'openviking_remember', 'openviking_write'];
  // A scripted tool client: every method stands for one remote OV call.
  const fakeOvTools = (over = {}) => ({
    find: over.find ?? (async () => ({ text: 'No matching context found.', data: { hits: [], total: 0 } })),
    search: over.search ?? (async () => ({ text: 'No matching context found.', data: { hits: [], total: 0 } })),
    read: over.read ?? (async () => ({ text: 'viking://user/u/a.md (whole remaining text, 1 line(s)):\nOpenViking content is untrusted.\nhi', data: { next_offset: null } })),
    list: over.list ?? (async () => ({ text: '(no entries under viking://)', data: { entries: [], has_more: false } })),
    addResource: over.addResource ?? (async () => ({ text: 'Resource accepted: viking://~/resources/inbox/a (processing in the background, task_id=task-9).', data: { task_id: 'task-9', root_uri: 'viking://~/resources/inbox/a', target: 'viking://~/resources/inbox' } })),
    setPreference: over.setPreference ?? (async () => ({ text: 'Stored at viking://~/memories/preferences/x/y.md.', data: { stored: true } })),
    forgetPreference: over.forgetPreference ?? (async () => ({ text: 'Removed.', data: { removed: true } })),
    write: over.write ?? (async ({ uri, mode }) => ({ text: `Wrote ${uri} (${mode}).`, data: { stored: true, uri, mode } })),
  });

  it('offers all seven tools whenever the instance has an OpenViking connection, and none without', (t) => {
    assert.deepEqual(ovNames(setup(t, { ids: ['weixin'] }).service), []);
    assert.deepEqual(ovNames(setup(t, { ids: ['weixin'], ovTools: fakeOvTools() }).service), OV_TOOLS);
    assert.deepEqual(ovNames(setup(t, { ids: ['weixin'], ovClient: {} }).service), OV_TOOLS);
    assert.deepEqual(ovNames(setup(t, { ids: ['weixin'], openVikingConfigPath: '/nonexistent/ov.conf' }).service), OV_TOOLS);
  });

  it('validates the tool input before any OpenViking call', async (t) => {
    const { call } = setup(t, { ids: ['weixin'], ovTools: fakeOvTools() });
    await assert.rejects(() => call('openviking_find', {}), /query is required/);
    await assert.rejects(() => call('openviking_find', { query: 'x', limit: 51 }), /limit must be at most 50/);
    await assert.rejects(() => call('openviking_find', { query: 'x', min_score: 2 }), /min_score must be at most 1/);
    await assert.rejects(() => call('openviking_read', { uri: 'http://nope' }), /uri has an invalid format/);
    await assert.rejects(() => call('openviking_list', { uri: 'viking://x', offset: -1 }), /offset must be at least 0/);
    await assert.rejects(() => call('openviking_search', { query: 'x', search_type: 'literal' }), /search_type must be one of/);
    await assert.rejects(() => call('openviking_add_resource', { url: 'notaurl' }), /url has an invalid format/);
    await assert.rejects(() => call('openviking_remember', { text: 'a fact' }), /decision_id is required/);
    await assert.rejects(() => call('openviking_remember', { decision_id: 'message:om_nowhere' }), /topic is required/);
    await assert.rejects(() => call('openviking_remember', { decision_id: 'message:om_nowhere', topic: '称呼', preference: '叫他老张' }),
      (error) => error.code === 'unknown_decision' && /^Not remembered/.test(error.message));
    assert.match((await call('openviking_find', { query: 'hello' })).text, /No matching context found/);
  });

  it('rechecks read authority after the remote answer', async (t) => {
    let release;
    const ovTools = fakeOvTools({ find: () => new Promise((resolve) => { release = resolve; }) });
    const { call, store } = setup(t, { ids: ['weixin'], ovTools });
    const pending = call('openviking_find', { query: 'slow' });
    store.initializeCognition('claude', 'new-owner');
    release({ text: 'found', data: { hits: [{ uri: 'viking://x' }] } });
    await assert.rejects(pending, /no longer owns/);
  });

  it('does not recheck write authority once the resource was accepted', async (t) => {
    let release;
    const ovTools = fakeOvTools({ addResource: () => new Promise((resolve) => { release = resolve; }) });
    const { call, store } = setup(t, { ids: ['weixin'], ovTools });
    const pending = call('openviking_add_resource', { url: 'https://example.com/a' });
    store.initializeCognition('claude', 'new-owner');
    release({ text: 'Resource accepted', data: { task_id: 'task-9', root_uri: 'viking://~/resources/inbox/a' } });
    const added = await pending;
    assert.equal(added.data.task_id, 'task-9');
  });

  it('records no delivery, decision or outbox for OpenViking tools', async (t) => {
    const { call, store, logs, inbound } = setup(t, { ids: ['lark'], ovTools: fakeOvTools() });
    inbound({ messageId: 'om_keep123456789', chatId: 'oc_keep123456789', content: '记一下' });
    await call('openviking_find', { query: 'x' });
    await call('openviking_read', { uri: 'viking://user/u/a.md' });
    await call('openviking_add_resource', { url: 'https://example.com/a' });
    await call('openviking_remember', { decision_id: 'message:om_keep123456789', topic: '称呼', preference: '叫他老张' });
    assert.equal(store.db.prepare('SELECT count(*) AS n FROM outbox').get().n, 0);
    assert.equal(store.db.prepare('SELECT count(*) AS n FROM decisions').get().n, 0);
    assert.ok(!logs.some((line) => line.message === 'delivery.blocked'));
    for (const tool of ['openviking_find', 'openviking_read', 'openviking_add_resource', 'openviking_remember']) {
      const line = logs.filter((entry) => entry.message === 'agent_tool').find((entry) => entry.tool === tool);
      assert.equal(line.outcome, 'ok', `${tool} logged one clean call`);
    }
  });

  it('keeps a preference as the sender\'s, named by the Core from the decision input, never by the model', async (t) => {
    const written = [];
    const ovTools = fakeOvTools({
      setPreference: async (input) => { written.push(['set', input]); return { text: 'Stored.', data: { stored: true } }; },
      forgetPreference: async (input) => { written.push(['forget', input]); return { text: 'Removed.', data: { removed: true } }; },
    });
    const { call, inbound, store, service } = setup(t, { ids: ['lark'], ovTools });
    let changed = 0;
    service.onPreferencesChanged = () => { changed += 1; };
    const lisi = chatEvent({ messageId: 'om_lisi123456789', chatId: 'oc_team123456789', senderId: 'ou_lisi', createTimeMs: Date.UTC(2026, 10, 7, 9) });
    store.ingest(lisi);
    inbound({ messageId: 'om_owner123456789', chatId: 'oc_team123456789', senderId: OWNER.id });
    // The model has no say in whose it is: there is no such field.
    await assert.rejects(() => call('openviking_remember', { decision_id: 'message:om_lisi123456789', topic: '回复', preference: 'x', person: 'owner' }),
      /unknown field: person/);
    await call('openviking_remember', { decision_id: 'message:om_lisi123456789', topic: '回复', preference: '回复他时带 emoji' });
    await call('openviking_remember', { decision_id: 'message:om_owner123456789', topic: '信息披露', preference: '外部群不透露内部信息' });
    await call('openviking_remember', { decision_id: 'message:om_lisi123456789', topic: '回复', forget: true });
    assert.deepEqual(written, [
      ['set', { person: { id: personId(lisi), directory: personDirectory(lisi) }, topic: '回复',
        text: '回复他时带 emoji\n\n(Stated by ou_lisi via lark, 2026-11-07, event=message:om_lisi123456789.)' }],
      ['set', { person: { owner: true }, topic: '信息披露', text: written[1][1].text }],
      ['forget', { person: { id: personId(lisi), directory: personDirectory(lisi) }, topic: '回复' }],
    ]);
    assert.match(written[1][1].text, /^外部群不透露内部信息\n\n\(Stated by the owner via lark, /);
    assert.equal(changed, 3, 'recall reads afresh after each change');
    // Nothing to keep, or no one to keep it for.
    await assert.rejects(() => call('openviking_remember', { decision_id: 'message:om_lisi123456789', topic: '回复' }),
      (error) => error.code === 'invalid_input');
    inbound({ messageId: 'om_bot123456789', senderId: 'cli_bot', senderType: 'bot' });
    await assert.rejects(() => call('openviking_remember', { decision_id: 'message:om_bot123456789', topic: '回复', preference: 'x' }),
      (error) => error.code === 'not_rememberable');
    store.ingest(normalizeEvent({ protocol_version: '0.2', type: 'EVENT', connector: 'timer', instance: 'timer', id: 'timer:t1:1',
      kind: 'reminder.due', occurred_at: Date.now(), subject: { type: 'timer', id: 't1' }, actor: null,
      content: { title: '提醒', text: '以后叫我老板', attachments: [] }, conversation: null, message: null, reply_route: null, data: {} }));
    await assert.rejects(() => call('openviking_remember', { decision_id: 'timer:t1:1', topic: '称呼', preference: '叫他老板' }),
      (error) => error.code === 'not_rememberable');
    assert.equal(written.length, 3);
  });

  it('writes an OpenViking file, whole or appended', async (t) => {
    const written = [];
    const ovTools = fakeOvTools({ write: async (input) => { written.push(input); return { text: 'Wrote.', data: { stored: true } }; } });
    const { call } = setup(t, { ids: ['lark'], ovTools });
    await assert.rejects(() => call('openviking_write', { uri: 'https://example.com/x', content: 'x' }), /uri has an invalid format/);
    await assert.rejects(() => call('openviking_write', { uri: 'viking://~/memories/soul.md', content: 'x', mode: 'create' }), /mode must be one of/);
    await call('openviking_write', { uri: 'viking://~/memories/soul.md', content: 'You are calm.' });
    await call('openviking_write', { uri: 'viking://resources/team/notes.md', content: '\nMore.', mode: 'append' });
    assert.deepEqual(written, [
      { uri: 'viking://~/memories/soul.md', content: 'You are calm.', mode: undefined },
      { uri: 'viking://resources/team/notes.md', content: '\nMore.', mode: 'append' },
    ]);
  });


  it('counts a landed OpenViking write as progress for the loop guard, a read does not', async (t) => {
    const stops = [];
    const { call, inbound, logs } = setup(t, {
      ids: ['weixin'], explicitSilence: true, ovTools: fakeOvTools(),
      onNoProgress: (info) => stops.push(info),
    });
    const row = inbound({ messageId: 'om_ov123456789', chatId: 'oc_ov123456789' });
    const agentLines = () => logs.filter((line) => line.message === 'agent_tool');
    const silence = () => call('stay_silent', { decision_id: 'message:om_ov123456789' });
    await silence();
    await silence();
    assert.equal(agentLines().at(-1).duplicates, 1);
    // The landed resource write clears the count...
    await call('openviking_add_resource', { url: 'https://example.com/a' });
    await silence();
    assert.equal(agentLines().at(-1).duplicates, 1);
    assert.deepEqual(stops, []);
    // ...a read neither clears nor counts.
    await call('openviking_find', { query: 'x' });
    await silence();
    assert.equal(agentLines().at(-1).duplicates, 2);
    await silence();
    assert.deepEqual(stops, [{ tool: 'stay_silent', decisionSeq: row.seq, count: 3, runtime: 'codex' }]);
  });

  it('relays the same OpenViking surface through the MCP adapter', async (t) => {
    const state = setup(t, { ids: ['weixin'], ovTools: fakeOvTools() });
    const socketPath = localSocketPath(state.directory);
    const server = await startActionApi({ socketPath, service: state.service, logger });
    t.after(() => stopActionApi(server));
    const handle = createMcpHandler({ socketPath, authority: TOKEN, client: 'mcp' });
    const listed = await handle({ jsonrpc: '2.0', id: 1, method: 'tools/list' });
    // The MCP surface is exactly the service's: names, schemas, descriptions.
    assert.deepEqual(listed.result.tools, state.service.tools());
    const found = listed.result.tools.find((tool) => tool.name === 'openviking_find');
    assert.equal(found.title, 'Find in OpenViking');
    assert.deepEqual(found.inputSchema.required, ['query']);
    assert.equal(found.annotations.readOnlyHint, true);
    assert.equal(listed.result.tools.find((tool) => tool.name === 'openviking_remember').annotations.readOnlyHint, false);
    const called = await handle({ jsonrpc: '2.0', id: 2, method: 'tools/call', params: { name: 'openviking_find', arguments: { query: 'anything' } } });
    assert.match(called.result.content[0].text, /No matching context found/);
  });
});

describe('agent tool front-ends', () => {
  async function serve(t, options) {
    const state = setup(t, options);
    const socketPath = localSocketPath(state.directory);
    const server = await startActionApi({ socketPath, service: state.service, logger });
    t.after(() => stopActionApi(server));
    if (supportsFileModes) assert.equal(fs.statSync(socketPath).mode & 0o077, 0);
    return { ...state, socketPath };
  }

  it('relays tools/list and tools/call from the MCP adapter, with images inline', async (t) => {
    const { directory, socketPath, inbound } = await serve(t, { run: (_connector, action) => {
      if (action !== 'message.media') return { message_id: 'om_delivered123456' };
      const file = path.join(directory, 'img.png');
      fs.writeFileSync(file, 'png');
      return { media: [{ path: file, name: 'img.png', kind: 'image', mime_type: 'image/png', size: 3 }] };
    } });
    inbound({ messageId: 'om_decision123456', chatId: 'oc_chat12345678' });
    const handle = createMcpHandler({ socketPath, authority: TOKEN, client: 'mcp' });

    const listed = await handle({ jsonrpc: '2.0', id: 1, method: 'tools/list' });
    assert.ok(listed.result.tools.some((tool) => tool.name === 'send_message' && tool.inputSchema.properties.chat_id));
    assert.ok(listed.result.tools.every((tool) => !('action' in tool)));

    const sent = await handle({ jsonrpc: '2.0', id: 2, method: 'tools/call', params: { name: 'send_message',
      arguments: { chat_id: 'oc_chat12345678', markdown: 'hi', decision_id: 'message:om_decision123456' } } });
    assert.match(sent.result.content[0].text, /Delivered message/);
    assert.equal(sent.result.structuredContent.message_id, 'om_delivered123456');

    const media = await handle({ jsonrpc: '2.0', id: 3, method: 'tools/call', params: { name: 'get_message_media',
      arguments: { message_id: 'om_decision123456', key: 'img_abcdefgh1234' } } });
    assert.deepEqual(media.result.content[1], { type: 'image', data: Buffer.from('png').toString('base64'), mimeType: 'image/png' });

    const stale = await handle({ jsonrpc: '2.0', id: 4, method: 'tools/call', params: { name: 'send_message',
      arguments: { chat_id: 'oc_chat12345678', markdown: 'hi', decision_id: 'message:om_unknown123456' } } });
    assert.equal(stale.result.isError, true);
    assert.match(stale.result.content[0].text, /not the event= of an accepted daemon-authenticated input/);

    const retired = createMcpHandler({ socketPath, authority: 'retired', client: 'mcp' });
    const fenced = await retired({ jsonrpc: '2.0', id: 5, method: 'tools/call', params: { name: 'list_chats', arguments: {} } });
    assert.match(fenced.result.content[0].text, /no longer owns/);
  });

  it('serves tools over real MCP stdio with the runtime environment', async (t) => {
    const { socketPath, inbound, connectors, logs } = await serve(t);
    inbound({ messageId: 'om_decision123456', chatId: 'oc_chat12345678' });
    const env = { ...process.env, PERSONAL_AGENT_SOCKET: socketPath, PERSONAL_AGENT_AUTHORITY: TOKEN };
    const run = (requests) => new Promise((resolve, reject) => {
      const child = spawn(process.execPath, [mcpPath], { env });
      let stdout = '';
      let stderr = '';
      child.stdout.on('data', (chunk) => { stdout += chunk; });
      child.stderr.on('data', (chunk) => { stderr += chunk; });
      child.on('error', reject);
      child.on('close', (code) => resolve({ code, responses: stdout.trim().split('\n').map(JSON.parse).sort((a, b) => a.id - b.id), stderr }));
      child.stdin.end(requests.map((request, index) => JSON.stringify({ jsonrpc: '2.0', id: index, ...request })).join('\n') + '\n');
    });

    const sent = await run([
      { method: 'initialize', params: { protocolVersion: '2025-06-18' } },
      { method: 'tools/list' },
      { method: 'tools/call', params: { name: 'reply_message', arguments: {
        message_id: 'om_decision123456', markdown: 'it\'s "quoted"\nand multi-line\u2028分行\u2029分段', decision_id: 'message:om_decision123456',
      } } },
      { method: 'tools/call', params: { name: 'send_message', arguments: {
        chat_id: 'oc_chat12345678', markdown: 'x', decision_id: 'message:om_missing12345',
      } } },
    ]);
    assert.equal(sent.code, 0, sent.stderr);
    assert.ok(sent.responses[1].result.tools.some(({ name }) => name === 'send_message'));
    assert.match(sent.responses[2].result.content[0].text, /^Delivered reply to message_id=om_decision123456/);
    assert.equal(connectors.calls[0].input.content.text, 'it\'s "quoted"\nand multi-line\u2028分行\u2029分段');
    assert.equal(logs.find((line) => line.message === 'agent_tool').via, 'mcp');
    assert.equal(sent.responses[3].result.isError, true);
    assert.match(sent.responses[3].result.content[0].text, /not the event= of an accepted daemon-authenticated input/);
  });

  it('refuses to run outside a daemon runtime', () => {
    const result = spawnSync(process.execPath, [mcpPath], {
      env: { PATH: process.env.PATH }, encoding: 'utf8',
    });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /PERSONAL_AGENT_SOCKET is not set/);
  });
});

it('finds decisions, reply targets, newer inputs and orphaned conversations through indexes', (t) => {
  const { store } = setup(t);
  const plans = [];
  const prepare = store.db.prepare.bind(store.db);
  store.db.prepare = (sql) => {
    if (/FROM connector_events/.test(sql) && !/^\s*DELETE FROM connector_events/.test(sql)) {
      plans.push(prepare(`EXPLAIN QUERY PLAN ${sql}`).all().map((row) => row.detail).join(' '));
    }
    return prepare(sql);
  };
  // An id that is not an inbound (a reply to the bot's own message) used to
  // scan the whole event history before falling through to the outbox.
  store.acceptedEvents('om_unknown12345678');
  store.acceptedMessageEvents('om_unknown12345678', 'lark');
  store.eventByMessageId('om_unknown12345678', 'lark');
  store.connectorForMessage('om_unknown12345678');
  store.newerAcceptedSeq('["lark","issue","owner/repo#1"]', 1);
  // Retention cleanup runs synchronously at startup and daily; without an index
  // every conversation left without events scans the whole event table.
  store.prune(86_400_000);
  assert.equal(plans.length, 6);
  assert.match(plans[0], /USING INDEX connector_events_event /);
  assert.match(plans[1], /USING INDEX connector_events_message \(connector_instance=\? AND message_id=\?\)/);
  assert.match(plans[2], /USING INDEX connector_events_message \(connector_instance=\? AND message_id=\?\)/);
  assert.match(plans[3], /USING INDEX connector_events_message_seq/);
  assert.match(plans[4], /USING COVERING INDEX connector_events_scope_status_seq/);
  assert.match(plans[5], /SEARCH connector_events USING COVERING INDEX connector_events_conv_status_seq \(conv_key=\?\)/);
  assert.doesNotMatch(plans.join(' '), /SCAN connector_events/);
});

// A decision names one accepted inbound event by its event= id. Freshness,
// attribution and idempotency all follow that event.
describe('decision references', () => {
  const send = (call, decision, extra = {}) => call('notify_owner', {
    markdown: '会议 10 分钟后开始', decision_id: decision, ...extra,
  });
  const outbox = (store) => store.db.prepare('SELECT inbound_seq FROM outbox ORDER BY id').all().map((row) => row.inbound_seq);

  it('acts on a non-chat event by the event= value its line shows, special characters included', async (t) => {
    const { store, call, connectors } = setup(t);
    const event = subjectEvent({ source: 'github', eventId: 'owner/repo#123 comment 50%', kind: 'issue.commented',
      subjectType: 'issue', subjectId: 'owner/repo#123' });
    const { seq } = store.ingest(event).inbound;
    const shown = /event=(\S+)/.exec(formatInboundLine(event))[1];
    assert.equal(shown, 'owner/repo#123%20comment%2050%25');

    await send(call, shown);
    assert.deepEqual(outbox(store), [seq]);
    // No line ever shows the raw id: a space cannot survive inside a tag.
    await assert.rejects(send(call, 'owner/repo#123 comment 50%'), /decision_id has an invalid format/);
    assert.equal(connectors.calls.length, 1);
  });

  it('fences a non-chat decision by its subject and lets the newest one fulfil an earlier request there', async (t) => {
    const { store, call, connectors } = setup(t);
    const issue = (eventId, subjectId) => subjectEvent({ source: 'github', eventId, kind: 'issue.updated',
      subjectType: 'issue', subjectId });
    const opened = store.ingest(issue('issue-1-opened', 'owner/repo#1')).inbound;
    store.ingest(issue('issue-2-opened', 'owner/repo#2'));
    // Another subject is another scope.
    await send(call, 'issue-1-opened');

    store.ingest(issue('issue-1-commented', 'owner/repo#1'));
    await assert.rejects(send(call, 'issue-1-opened', { markdown: 'late' }), (error) => error.code === 'stale_decision');
    // A new comment does not cancel the request before it.
    await send(call, 'issue-1-commented', { markdown: 'done', response_to_id: 'issue-1-opened' });
    await assert.rejects(send(call, 'issue-1-opened', { markdown: 'future', response_to_id: 'issue-1-commented' }),
      (error) => error.code === 'stale_decision');
    assert.deepEqual(outbox(store), [opened.seq, opened.seq]);
    assert.equal(connectors.calls.length, 2);
  });

  it('takes only event= as a reference and refuses an event id that several instances accepted', async (t) => {
    const { store, call, connectors } = setup(t);
    // One instance: an event whose id is another event's message id. Each one
    // is reached by its own event=; a message id is never a reference.
    const chat = store.ingest(chatEvent({ messageId: 'om_shared12345678', eventId: 'evt-chat-1', chatId: 'oc_a12345678' })).inbound;
    const doc = store.ingest(subjectEvent({ source: 'lark', eventId: 'om_shared12345678', subjectType: 'doc', subjectId: 'doc-1' })).inbound;
    // One message, two events: the original and an edit.
    const created = store.ingest(chatEvent({ messageId: 'om_twice123456789', eventId: 'evt-created', chatId: 'oc_b12345678' })).inbound;
    store.ingest(chatEvent({ messageId: 'om_twice123456789', eventId: 'evt-edited', chatId: 'oc_b12345678',
      kind: 'message.updated' }));
    // Two instances of one source, each with its own reminder-1.
    store.ingest(subjectEvent({ instance: 'calendar-personal', eventId: 'reminder-1' }));
    const work = store.ingest(subjectEvent({ instance: 'calendar-work', eventId: 'reminder-1' })).inbound;

    await assert.rejects(send(call, 'om_twice123456789'),
      (error) => error.code === 'unknown_decision' && /msg= is not accepted/.test(error.message));
    await assert.rejects(send(call, 'evt-edited', { response_to_id: 'om_twice123456789' }),
      (error) => error.code === 'attribution_rejected');
    await assert.rejects(send(call, 'reminder-1'),
      (error) => error.code === 'decision_ambiguous' && /decision_instance/.test(error.message));
    assert.equal(connectors.calls.length, 0);

    await send(call, 'om_shared12345678');
    await send(call, 'evt-chat-1');
    await send(call, 'evt-edited', { response_to_id: 'evt-created' });
    await assert.rejects(send(call, 'evt-created', { markdown: 'stale' }), (error) => error.code === 'stale_decision');
    await send(call, 'reminder-1', { decision_instance: 'calendar-work' });
    assert.deepEqual(outbox(store), [doc.seq, chat.seq, created.seq, work.seq]);
    assert.equal(connectors.calls.length, 4);
  });

  it('points a stale decision at the newer input\'s event=, and a retry that follows the hint goes through', async (t) => {
    const { store, call, connectors } = setup(t);
    const reply = (decision) => call('reply_message', { message_id: 'om_question1234567', markdown: '好的', decision_id: decision });
    const question = store.ingest(chatEvent({ messageId: 'om_question1234567', eventId: 'evt-question', chatId: 'oc_b12345678' })).inbound;
    const followUp = chatEvent({ messageId: 'om_followUp123456', eventId: 'evt-follow-up', chatId: 'oc_b12345678' });
    store.ingest(followUp);

    const stale = await reply('evt-question').catch((error) => error);
    assert.equal(stale.code, 'stale_decision');
    assert.doesNotMatch(stale.message, /msg=/);
    // Copy the tag the hint names from the newer input's line, as the model would.
    const [, tag] = /use its (\w+)= as decision_id/.exec(stale.message);
    const line = formatInboundLine(followUp);
    const wrong = await reply(/ msg=(\S+)/.exec(line)[1]).catch((error) => error);
    assert.equal(wrong.code, 'unknown_decision');
    assert.match(wrong.message, /not the event= of an accepted .* \(msg= is not accepted\)/);
    await reply(new RegExp(` ${tag}=(\\S+)`).exec(line)[1]);
    // The reply still answers the question it targets.
    assert.deepEqual(outbox(store), [question.seq]);
    assert.equal(connectors.calls.length, 1);
  });

  it('keys a delivery by its decision event', async (t) => {
    const { store, call, connectors } = setup(t);
    store.ingest(chatEvent({ messageId: 'om_again12345678', eventId: 'evt-again', chatId: 'oc_notify12345678' }));
    await send(call, 'evt-again');
    assert.equal((await send(call, 'evt-again')).data.duplicate, true);

    // The same reminder text for two events, or for one event id on two
    // instances, is a separate delivery each time.
    store.ingest(subjectEvent({ eventId: 'reminder-1', subjectId: 'evt_1' }));
    store.ingest(subjectEvent({ eventId: 'reminder-2', subjectId: 'evt_2' }));
    store.ingest(subjectEvent({ instance: 'calendar-work', eventId: 'reminder-1', subjectId: 'evt_1' }));
    for (const [id, instance] of [['reminder-1', 'calendar'], ['reminder-2', 'calendar'], ['reminder-1', 'calendar-work']]) {
      assert.equal((await send(call, id, { decision_instance: instance })).data.duplicate, undefined);
    }
    assert.equal(connectors.calls.length, 4);
    assert.equal(new Set(connectors.calls.map((entry) => entry.input.idempotency_key)).size, 4);
  });

  it('re-checks the decision event after an awaited preparation, also against a newer event for the same message', async (t) => {
    let finishLookup = null;
    const roster = { resolveNames: (_chatId, names) => new Promise((resolve) => {
      finishLookup = () => resolve(names.map((name) => ({ name, id: 'ou_alice12345678' })));
    }) };
    const { store, call, connectors, inbound } = setup(t, { roster });
    inbound({ messageId: 'om_racing12345678', eventId: 'evt-first', chatId: 'oc_chat12345678' });

    const pending = call('send_message', { chat_id: 'oc_chat12345678', markdown: '@Alice hi',
      mention_names: ['Alice'], decision_id: 'evt-first' });
    // The same message comes back as a new event (an edit) while the lookup awaits.
    inbound({ messageId: 'om_racing12345678', eventId: 'evt-second', chatId: 'oc_chat12345678', kind: 'message.updated' });
    finishLookup();
    await assert.rejects(pending, (error) => error.code === 'stale_decision');
    assert.equal(connectors.calls.length, 0);
    assert.equal(store.db.prepare('SELECT COUNT(*) AS n FROM outbox').get().n, 0);
  });
});

// The owner is the person the agent works for. A non-chat event reaches people
// only as a notice to the owner, on an IM the Core picks; an action that speaks
// for the owner (a mail reply) needs a chat message the owner sent.
describe('owner notices and owner-only actions', () => {
  const outboundLines = (store) => store.cognitionJournal().filter((item) => item.kind === 'outbound').map(formatCognitionJournalLine);

  it('notifies the owner on the primary IM, and lets a non-chat event do nothing else', async (t) => {
    const { store, call, connectors } = setup(t);
    const reminder = store.ingest(subjectEvent({ eventId: 'reminder-1' })).inbound;
    store.ingest(chatEvent({ messageId: 'om_group12345678', eventId: 'evt-group', chatId: 'oc_group12345678', chatType: 'group' }));

    for (const [tool, input] of [
      ['send_message', { chat_id: 'oc_group12345678', markdown: 'leak' }],
      ['reply_message', { message_id: 'om_group12345678', markdown: 'leak' }],
      ['react_to_message', { message_id: 'om_group12345678', emoji_type: 'OK' }],
    ]) {
      await assert.rejects(call(tool, { ...input, decision_id: 'reminder-1' }),
        (error) => error.code === 'owner_only' && /use notify_owner/.test(error.message));
    }
    // Only a chat message may name the IM.
    await assert.rejects(call('notify_owner', { markdown: 'x', decision_id: 'reminder-1', source: 'lark' }),
      (error) => error.code === 'invalid_input' && /only when a chat message asked/.test(error.message));
    assert.equal(connectors.calls.length, 0);

    const result = await call('notify_owner', { markdown: '1:1 十分钟后开始', decision_id: 'reminder-1' });
    assert.match(result.text, /^Delivered notification to the owner on lark\./);
    assert.deepEqual(connectors.calls.map(({ connector, action, input }) => [connector, action, input.user_id, input.chat_id]),
      [['lark', 'message.send', OWNER.id, undefined]]);
    assert.deepEqual(store.db.prepare('SELECT chat_id, conv_key, inbound_seq FROM outbox').all().map((row) => ({ ...row })),
      [{ chat_id: null, conv_key: null, inbound_seq: reminder.seq }]);
    // History shows a notice to the owner, not a message in some chat.
    assert.match(outboundLines(store)[0], /^\[source=lark to=owner in_reply_to_event=reminder-1 time=\S+ from=me\] 1:1 十分钟后开始$/);
  });

  it('tells the owner on the IM a chat message named, and still sends across IMs for a chat', async (t) => {
    const { store, call, connectors } = setup(t, { ids: ['lark', 'weixin'],
      owners: { lark: OWNER, weixin: { id: 'wx-owner', target: { chat_id: 'wx-owner' } } } });
    store.ingest(chatEvent({ source: 'weixin', messageId: 'wx-msg-1', eventId: 'wx-evt-1', chatId: 'wx-owner', senderId: 'wx-owner' }));
    store.ingest(chatEvent({ messageId: 'om_group12345678', eventId: 'evt-group', chatId: 'oc_group12345678', chatType: 'group' }));

    // "在飞书上提醒我": the chat message picks the IM, the Core picks the person.
    await call('notify_owner', { markdown: '提醒你', decision_id: 'wx-evt-1', source: 'lark' });
    // Without source the primary IM is used.
    await call('notify_owner', { markdown: '提醒你 2', decision_id: 'wx-evt-1' });
    // "在飞书那个群里说一声": a chat decision keeps send_message to any chat.
    await call('send_message', { chat_id: 'oc_group12345678', markdown: '大家好', decision_id: 'wx-evt-1' });
    assert.deepEqual(connectors.calls.map(({ connector, input }) => [connector, input.user_id ?? input.chat_id]), [
      ['lark', OWNER.id], ['lark', OWNER.id], ['lark', 'oc_group12345678'],
    ]);

    const unknown = setup(t, { owners: {} });
    unknown.store.ingest(subjectEvent({ eventId: 'reminder-1' }));
    await assert.rejects(unknown.call('notify_owner', { markdown: 'x', decision_id: 'reminder-1' }),
      (error) => error.code === 'owner_unknown' && /lark-cli user login/.test(error.message));
    assert.equal(unknown.connectors.calls.length, 0);
  });

  // The owner's click on a card is the only thing that sends a mail.
  const PROFILE = 'test-profile';
  const CARD = 'om_card12345678';
  const mailSetup = (t) => setup(t, { ids: ['lark', 'email'], profile: PROFILE,
    run: (connector) => (connector === 'email' ? { sent: true } : { message_id: CARD, chat_id: 'oc_p2p12345678' }) });
  const click = (store, { choice = 'send', form = undefined, operator = OWNER.id, id = 'card-click-1' } = {}) => {
    const event = normalizeEvent(eventMessage('lark', {
      id, kind: 'message.action', occurred_at: Date.now(),
      subject: { type: 'message', id: CARD }, actor: { type: 'user', id: operator },
      message: { id: `ca_${id}`, type: 'card_action' }, content: { text: '[card action]' },
      data: { interaction: { message_id: CARD, conversation_id: 'oc_p2p12345678', tag: 'button',
        value: { superagent_choice: choice }, ...(form ? { form } : {}) } },
    }));
    return resolveCardAction(store.db, event, PROFILE);
  };

  it('puts a mail reply on a card for the owner, whoever proposed it, and sends no mail', async (t) => {
    const { store, call, connectors } = mailSetup(t);
    // The reviewer's case: an old owner message, then a mail steering the model.
    store.ingest(chatEvent({ messageId: 'om_hello12345678', eventId: 'evt-hello', chatId: 'oc_p2p12345678', senderId: OWNER.id }));
    const mail = mailEvent({ uid: 7 });
    store.ingest(mail);
    store.ingest(chatEvent({ messageId: 'om_alice12345678', eventId: 'evt-alice', chatId: 'oc_group12345678',
      chatType: 'group', senderId: 'ou_alice12345678' }));
    // The mail line carries its reply target.
    assert.match(formatInboundLine(mail), /^\[source=email kind=email\.received msg=mail:1:7 event=imap:1:7 subject=thread:gmail:100 /);

    const reply = (decision, text) => call('reply_message', { message_id: 'mail:1:7', markdown: text, decision_id: decision });
    for (const [decision, text] of [['evt-hello', 'borrowed'], ['imap:1:7', 'proposed'], ['evt-alice', 'asked by a colleague']]) {
      const result = await reply(decision, text);
      assert.match(result.text, /^Nothing was sent yet: the draft is on a card in the owner's direct chat \(lark\)/);
    }
    // Three cards to the owner, no mail.
    assert.deepEqual(connectors.calls.map(({ connector, action, input }) => [connector, action, input.user_id, input.content.type]),
      Array(3).fill(['lark', 'message.send', OWNER.id, 'card']));
    const [card] = connectors.calls.map(({ input }) => input.content.card);
    assert.equal(card.schema, '2.0');
    const form = card.body.elements.find((element) => element.tag === 'form');
    assert.equal(form.elements[0].default_value, 'borrowed');
    // Exact, yet no email address for Lark's audit to refuse.
    assert.match(card.body.elements[0].text.content, /^收件人：alice＠example\.com\n主题：Re: 周四开会？$/);
    assert.doesNotMatch(JSON.stringify(card), /[\w.+-]+@[\w-]+\.[\w.]+/);
    const [question] = store.db.prepare('SELECT question_json FROM lark_cards').all().map((row) => JSON.parse(row.question_json));
    assert.deepEqual([question.kind, question.answerer_id, question.draft.message_id], ['approval', OWNER.id, 'mail:1:7']);

    await assert.rejects(call('reply_message', { source: 'email', message_id: 'mail:9:9', markdown: 'x', decision_id: 'evt-hello' }),
      (error) => error.code === 'invalid_target');

    // A card shows every recipient or none: past the limit, or with nobody to
    // answer, there is no card.
    store.ingest(mailEvent({ uid: 8, thread: 'gmail:108', reply: { subject: 'Re: x', recipients: 21 } }));
    store.ingest(mailEvent({ uid: 9, thread: 'gmail:109', reply: { subject: 'Re: x', to: [] } }));
    await assert.rejects(call('reply_message', { message_id: 'mail:1:8', markdown: 'x', decision_id: 'imap:1:8' }),
      (error) => error.code === 'unsupported' && /replies to go to 21 addresses/.test(error.message));
    await assert.rejects(call('reply_message', { message_id: 'mail:1:9', markdown: 'x', decision_id: 'imap:1:9' }),
      (error) => error.code === 'invalid_target' && /no address to reply to/.test(error.message));
    assert.equal(connectors.calls.length, 3);
  });

  it('answers each mail from the mailbox it arrived in, and the card says which', async (t) => {
    const { store, call, connectors } = setup(t, { ids: ['lark', 'email-gmail', 'email-qq'], profile: PROFILE,
      run: (connector) => (connector.startsWith('email-') ? { sent: true } : { message_id: CARD, chat_id: 'oc_p2p12345678' }) });
    store.ingest(chatEvent({ messageId: 'om_hello12345678', eventId: 'evt-hello', chatId: 'oc_p2p12345678', senderId: OWNER.id }));
    const to = [{ name: '', address: 'alice@example.com' }];
    store.ingest(mailEvent({ uid: 7, instance: 'email-gmail', reply: { from: 'me@gmail.com', to, subject: 'Re: a' } }));
    const qq = mailEvent({ uid: 8, instance: 'email-qq', thread: 'gmail:108', reply: { from: 'me@qq.com', to, subject: 'Re: b' } });
    store.ingest(qq);
    assert.match(formatInboundLine(qq), /^\[source=email instance=email-qq kind=email\.received msg=mail:1:8 /);

    // source=email names the connector; the mailbox the mail arrived in answers.
    await call('reply_message', { source: 'email', message_id: 'mail:1:8', markdown: '好的', decision_id: 'imap:1:8' });
    const card = connectors.calls.at(-1).input.content.card;
    assert.match(card.body.elements[0].text.content, /^发件邮箱：me＠qq\.com\n收件人：alice＠example\.com\n主题：Re: b$/);
    await assert.rejects(call('reply_message', { source: 'email', message_id: 'mail:9:9', markdown: 'x', decision_id: 'imap:1:7' }),
      (error) => error.code === 'route_ambiguous' && /email-gmail, email-qq/.test(error.message));

    const pressed = click(store);
    store.ingest(pressed);
    await new ActionService({ config: { maxReplyChars: 1_000 }, store, connectors, logger }).settleApproval(pressed);
    assert.deepEqual(connectors.calls.filter(({ connector }) => connector.startsWith('email-'))
      .map(({ connector, input }) => [connector, input.message_id]), [['email-qq', 'mail:1:8']]);
  });

  // A primary IM without cards: the draft is a numbered message the owner answers.
  const WX_OWNER = { id: 'wx-owner', target: { chat_id: 'wx-owner' } };
  const weixinSetup = (t) => setup(t, { ids: ['weixin', 'email-qq'], channel: 'weixin', owners: { weixin: WX_OWNER },
    run: (connector) => (connector.startsWith('email-') ? { sent: true } : { message_id: 'wx-delivered' }) });
  const QQ_TO = [{ name: 'Alice', address: 'alice@example.com' }];
  const qqMail = (uid = 7) => mailEvent({ uid, instance: 'email-qq', thread: `gmail:${uid}`,
    reply: { from: 'me@qq.com', to: QQ_TO, subject: 'Re: 周四开会？' } });
  let ownerMessages = 0;
  const fromOwner = (store, content, sender = 'wx-owner') => {
    ownerMessages += 1;
    const event = chatEvent({ source: 'weixin', messageId: `wx-in-${ownerMessages}`, eventId: `wx-in-${ownerMessages}`,
      chatId: sender, senderId: sender, content });
    store.ingest(event);
    return event;
  };
  const answer = async (service, store, content) => {
    const event = fromOwner(store, content);
    const reply = service.matchApprovalReply(content, event);
    assert.ok(reply, `${content} is an answer`);
    return service.answerApproval(event, reply);
  };
  const told = (connectors) => connectors.calls.filter(({ connector, action }) => connector === 'weixin' && action === 'message.reply')
    .map(({ input }) => input.content.text);
  const mails = (connectors) => connectors.calls.filter(({ connector }) => connector.startsWith('email-'))
    .map(({ connector, input }) => [connector, input.message_id, input.content.text, input.to]);

  it('on a primary IM without cards, the draft is a numbered message the owner answers 修改 and 发送', async (t) => {
    const { store, call, connectors, service } = weixinSetup(t);
    store.ingest(qqMail());
    const propose = () => call('reply_message', { message_id: 'mail:1:7', markdown: '周四可以', decision_id: 'imap:1:7' });
    assert.match((await propose()).text, /waiting in the owner's direct chat \(weixin\) as #1\./);
    const [draft] = connectors.calls;
    assert.deepEqual([draft.connector, draft.action, draft.input.chat_id, draft.input.content.type],
      ['weixin', 'message.send', 'wx-owner', 'markdown']);
    assert.equal(draft.input.content.text, [
      '待你确认的邮件回复 #1', '发件邮箱：me@qq.com', '收件人：Alice <alice@example.com>', '主题：Re: 周四开会？',
      '━━━━ 回信正文 ━━━━', '周四可以', '━━━━ 正文结束 ━━━━', '回复「发送 1」发出', '回复「不发 1」作废', '回复「修改 1 新的正文」改成你写的内容',
      '3 天内有效；也可以直接告诉我怎么改，我重新起草。',
    ].join('\n'));
    // The same request again is the same draft.
    assert.match((await propose()).text, /already waiting for the owner as #1/);
    assert.equal(connectors.calls.length, 1);

    // Only the owner's whole-message answer to an existing draft counts.
    const other = fromOwner(store, '发送 1', 'wx-someone');
    assert.equal(service.matchApprovalReply('发送 1', other), null);
    const owner = fromOwner(store, '发送 9');
    assert.equal(service.matchApprovalReply('发送 9', owner), null);
    assert.equal(service.matchApprovalReply('发送 1 谢谢', owner), null);

    assert.equal(await answer(service, store, '修改 1'), null);
    assert.equal(await answer(service, store, '修改 1 周五下午可以\n回头见'), null);
    assert.deepEqual(mails(connectors), [], 'an edit sends nothing');
    assert.match(told(connectors)[0], /^在「修改 1」后面写上新的正文/);
    assert.match(told(connectors)[1], /^已按你的修改更新 #1，确认后才会发出：\n发件邮箱：me@qq\.com[\s\S]*━━━━ 回信正文 ━━━━\n周五下午可以\n回头见\n━━━━ 正文结束 ━━━━\n回复「发送 1」发出/);

    const note = await answer(service, store, '发送 1');
    assert.deepEqual(mails(connectors), [['email-qq', 'mail:1:7', '周五下午可以\n回头见', ['alice@example.com']]]);
    assert.equal(told(connectors).at(-1), '#1 已从 me@qq.com 发出。');
    assert.match(note, /^\[mail reply sent\] .*"text":"周五下午可以\\n回头见".*"edited_by_owner":true/);
    // Sent once: a second 发送 only says so.
    assert.equal(await answer(service, store, '发送 1'), null);
    assert.equal(mails(connectors).length, 1);
    assert.equal(told(connectors).at(-1), '#1 已从 me@qq.com 发出。');
  });

  it('a newer draft replaces the pending one, 不发 drops a draft, and an old one expires', async (t) => {
    const { store, call, connectors, service } = weixinSetup(t);
    store.ingest(qqMail());
    await call('reply_message', { message_id: 'mail:1:7', markdown: '周四可以', decision_id: 'imap:1:7' });
    await call('reply_message', { message_id: 'mail:1:7', markdown: '周四三点可以', decision_id: 'imap:1:7' });
    assert.equal(await answer(service, store, '发送 1'), null);
    assert.equal(told(connectors).at(-1), '#1 已被新的草稿 #2 替代；要发出请回复「发送 2」。');
    assert.match(await answer(service, store, '不发 2'), /^\[mail reply cancelled\]/);
    assert.equal(told(connectors).at(-1), '#2 已作废，不会发出。');
    assert.equal(await answer(service, store, '发送 2'), null);
    assert.equal(told(connectors).at(-1), '#2 已作废，不会发出。');

    store.ingest(qqMail(8));
    await call('reply_message', { message_id: 'mail:1:8', markdown: '好的', decision_id: 'imap:1:8' });
    store.db.prepare('UPDATE approvals SET expires_at_ms = ? WHERE id = 3').run(Date.now() - 1);
    assert.equal(await answer(service, store, '发送 3'), null);
    assert.equal(told(connectors).at(-1), '#3 已过期（3 天内有效），没有发出；需要的话让我重新起草。');
    assert.deepEqual(mails(connectors), []);
  });

  it('the owner\'s answer never reaches the model, an ordinary message does', async (t) => {
    const store = new Store(':memory:', logger);
    const delivered = [];
    const handled = [];
    const harness = new Harness({
      config: { maxEventBytes: 100_000, maxEventAgeMs: 60_000, ignoredSenderIds: new Set() }, store, logger, recordActivity() {},
      runtime: { snapshot: () => ({ session: { connected: true }, work: { turnInProgress: false } }),
        submit: (items) => { delivered.push(...items); return { accepted: true }; } },
      onSlashCommand: () => assert.fail('not a slash command'),
      approvalReply: (text) => (text === '发送 1' ? { verb: 'send', handle: (event, inbound) => handled.push([event.content, inbound.seq]) } : null),
    });
    t.after(() => { harness.stop(); store.close(); });
    const message = (id, content) => eventMessage('weixin', { id, kind: 'message.created', occurred_at: Date.now(),
      subject: { type: 'conversation', id: 'wx-owner' }, actor: { type: 'user', id: 'wx-owner' },
      conversation: { id: 'wx-owner', type: 'p2p' }, message: { id: `m-${id}`, type: 'text' }, content: { text: content },
      reply_route: { action: 'message.reply', input: { message_id: `m-${id}` } } });
    harness.handleConnectorEvent(message('a', '发送 1'));
    harness.handleConnectorEvent(message('b', '帮我看看邮件'));
    await harness.ingressBarrier;
    assert.equal(handled.length, 1);
    assert.equal(handled[0][0], '发送 1');
    assert.deepEqual(delivered.map((item) => /帮我看看邮件$/.test(item.text)), [true]);
    assert.equal(store.db.prepare("SELECT filter_reason FROM connector_events WHERE event_id = 'a'").get().filter_reason, 'approval_reply');
  });

  it('sends the approved draft as the owner left it, once, and shows the outcome on the card', async (t) => {
    const { store, call, connectors } = mailSetup(t);
    // The owner's direct chat with the bot, where the card lands.
    store.ingest(chatEvent({ messageId: 'om_hello12345678', eventId: 'evt-hello', chatId: 'oc_p2p12345678', senderId: OWNER.id }));
    store.ingest(mailEvent({ uid: 7 }));
    await call('reply_message', { message_id: 'mail:1:7', markdown: '周四可以', decision_id: 'imap:1:7' });
    const mail = store.db.prepare("SELECT seq FROM connector_events WHERE event_id = 'imap:1:7'").get();

    // Only the owner can answer it.
    assert.throws(() => click(store, { operator: 'ou_alice12345678' }), /card answerer is not authorized/);
    const pressed = click(store, { form: { reply_body: '周五下午三点可以，回头见' } });
    assert.equal(store.ingest(pressed).inbound.status, 'DONE');
    const settled = await new ActionService({ config: { maxReplyChars: 1_000 }, store, connectors, logger }).settleApproval(pressed);
    assert.deepEqual([settled.state, settled.edited], ['sent', true]);
    const sent = connectors.calls.filter(({ connector }) => connector === 'email');
    // Sent to exactly the recipients the card showed.
    assert.deepEqual(sent.map(({ action, input }) => [action, input.message_id, input.content.text, input.to]),
      [['message.reply', 'mail:1:7', '周五下午三点可以，回头见', ['alice@example.com']]]);
    assert.deepEqual(connectors.updates.map(({ messageId, card }) => [messageId, card.header.title.content]), [[CARD, '已发送']]);
    // Recorded against the mail, with no chat.
    const row = store.db.prepare("SELECT chat_id, conv_key, inbound_seq FROM outbox WHERE connector = 'email'").get();
    assert.deepEqual({ ...row }, { chat_id: null, conv_key: null, inbound_seq: mail.seq });
    assert.match(outboundLines(store).at(-1),
      /^\[source=email action=message\.reply in_reply_to_event=imap:1:7 subject=thread:gmail:100 time=\S+ from=me\] 周五下午三点可以，回头见$/);

    // A second press does not settle it again.
    assert.equal(store.ingest(click(store, { id: 'card-click-2' })).filterReason, 'card_already_answered_or_expired');

    // The model then hears, in the owner's chat, what actually went out.
    const input = [];
    const harness = new Harness({ config: {}, store, logger, recordActivity() {},
      runtime: { snapshot: () => ({ session: { connected: true }, work: { turnInProgress: false } }),
        submit: (items) => { input.push(...items); return { accepted: true }; } } });
    await harness.deliverNote(pressed, approvalNote(pressed.question.draft, settled));
    assert.equal(input.length, 1);
    assert.equal(input[0].seq, null);
    assert.equal(input[0].text.replace(/ time=\S+/, ''), '[chat=oc_p2p12345678 source=lark msg=ca_card-click-1 event=card-click-1 '
      + `card=${CARD} from=${OWNER.id.slice(0, 11)} reply_to=me] [mail reply sent] `
      + '{"subject":"Re: 周四开会？","to":["alice@example.com"],"text":"周五下午三点可以，回头见","edited_by_owner":true}');
  });

  it('shows a draft too long for a card input read-only, and an empty edit falls back to the draft', () => {
    const draft = { text: 'x'.repeat(EDITABLE_CHARS + 1), to: [{ name: 'Bob', address: 'bob@example.com' }], subject: 'Re: hi' };
    const card = approvalCard(draft);
    assert.equal(card.body.elements.some((element) => element.tag === 'form'), false);
    assert.deepEqual(card.body.elements.filter((element) => element.tag === 'button')
      .map((element) => element.behaviors[0].value.superagent_choice), ['send', 'cancel']);
    assert.equal(approvedText(draft, { reply_body: '   ' }), draft.text);
    // A card never carries an @ (Lark refuses addresses); the approved text gets them back.
    const signed = approvalCard({ ...draft, text: '好的\n— bob@example.com' });
    assert.equal(signed.body.elements.find((element) => element.tag === 'form').elements[0].default_value, '好的\n— bob＠example.com');
    assert.doesNotMatch(JSON.stringify(signed), /@/);
    assert.equal(approvedText(draft, { reply_body: '好的\n— bob＠example.com' }), '好的\n— bob@example.com');
    assert.equal(approvedText(draft, undefined), draft.text);
  });

  it('drops the draft when the owner declines it', async (t) => {
    const { store, call, connectors } = mailSetup(t);
    store.ingest(mailEvent({ uid: 7 }));
    await call('reply_message', { message_id: 'mail:1:7', markdown: '周四可以', decision_id: 'imap:1:7' });
    const declined = click(store, { choice: 'cancel' });
    store.ingest(declined);
    const settled = await new ActionService({ config: { maxReplyChars: 1_000 }, store, connectors, logger }).settleApproval(declined);
    assert.equal(settled.state, 'cancelled');
    assert.equal(approvalNote(declined.question.draft, settled), '[mail reply cancelled] {"subject":"Re: 周四开会？","to":["alice@example.com"]}');
    assert.equal(connectors.calls.filter(({ connector }) => connector === 'email').length, 0);
    assert.equal(connectors.updates[0].card.header.title.content, '已取消，没有发送');
  });
});

describe('stay_silent decisions', () => {
  // The tool and the ledger exist only on an instance that opts in.
  const setupOn = (t, options = {}) => setup(t, { explicitSilence: true, ...options });
  const decisions = (store) => store.db.prepare('SELECT connector_instance, event_id, seq, outcome FROM decisions ORDER BY seq')
    .all().map((row) => ({ ...row }));
  const activities = (store, kind) => store.db.prepare('SELECT status, summary, inbound_seq, detail_json FROM activities WHERE kind = ? ORDER BY id')
    .all(kind).map((row) => ({ ...row, metadata: JSON.parse(row.detail_json) }));
  const toolLines = (logs) => logs.filter((line) => line.message === 'agent_tool');
  const reactions = () => {
    let count = 0;
    return (_connector, action) => {
      if (action === 'message.react') return { reaction_id: `reaction_${count += 1}` };
      if (action === 'message.reaction.delete') return { deleted: true };
      return { message_id: 'om_delivered123456' };
    };
  };

  it('records silence without any side effect, once', async (t) => {
    let touches = 0;
    const { store, call, connectors, inbound, logs } = setupOn(t, { liveness: { touch() { touches += 1; } } });
    const row = inbound({ messageId: 'om_quiet12345678', chatId: 'oc_quiet12345678' });
    const result = await call('stay_silent', { decision_id: 'message:om_quiet12345678', reason: 'chatter between others' });
    assert.equal(result.text, 'Recorded: nothing is sent for this input.');
    assert.deepEqual(decisions(store), [{ connector_instance: 'lark', event_id: 'message:om_quiet12345678', seq: row.seq,
      outcome: 'silent' }]);
    // Not an outbound side effect: no outbox, no journal, no connector, no liveness.
    assert.deepEqual(store.stats().outbox, {});
    assert.equal(store.db.prepare("SELECT COUNT(*) AS n FROM cognition_journal WHERE kind = 'outbound'").get().n, 0);
    assert.equal(connectors.calls.length, 0);
    assert.equal(touches, 0);
    const [silent] = activities(store, 'delivery.silent');
    assert.deepEqual([silent.status, silent.summary, silent.inbound_seq], ['SILENT', 'chatter between others', row.seq]);
    assert.deepEqual(silent.metadata, { tool: 'stay_silent', decisionSeq: row.seq, runtime: 'codex', via: null });
    const [line] = toolLines(logs);
    assert.deepEqual([line.outcome, line.decisionSeq, line.superseded], ['ok', row.seq, false]);
    assert.doesNotMatch(JSON.stringify(logs), /chatter between others|om_quiet/);

    // A repeat changes nothing and says to stop.
    const again = await call('stay_silent', { decision_id: 'message:om_quiet12345678', reason: 'again' });
    assert.match(again.text, /^STOP: this stay_silent call changed nothing for this input\. Do not call it again for it; decide any other input you still have, otherwise finish your turn\. Already recorded/);
    assert.equal(again.data.duplicate, true);
    assert.equal(toolLines(logs)[1].outcome, 'duplicate');
    assert.equal(decisions(store).length, 1);
    assert.equal(activities(store, 'delivery.silent').length, 1);

    // A delivery still touches liveness.
    await call('send_message', { chat_id: 'oc_quiet12345678', markdown: 'hi', decision_id: 'message:om_quiet12345678' });
    assert.equal(touches, 1);
  });

  it('asks once per turn for the turn to end when calls keep changing nothing for one input', async (t) => {
    const stops = [];
    const { service, call, inbound, logs } = setupOn(t, { onNoProgress: (info) => stops.push(info) });
    const looped = inbound({ messageId: 'om_loop123456789', chatId: 'oc_loop123456789' });
    inbound({ messageId: 'om_other12345678', chatId: 'oc_other12345678' });
    const silence = (id, reason) => call('stay_silent', { decision_id: `message:${id}`, reason });
    await silence('om_loop123456789', '群内讨论，未直接询问我');
    await silence('om_other12345678', 'chatter');
    // Repeats on another input do not add up; two repeats are still a retry.
    await silence('om_other12345678', 'chatter again');
    await silence('om_loop123456789', '群内技术交流，未向我提问');
    const second = await silence('om_loop123456789', '群内观点交流');
    assert.deepEqual(stops, []);
    assert.doesNotMatch(second.text, /ending this turn/);
    // The third repeat for one input, whatever its wording, ends the turn.
    const third = await silence('om_loop123456789', '群内讨论，无需回复');
    assert.deepEqual(stops, [{ tool: 'stay_silent', decisionSeq: looped.seq, count: 3, runtime: 'codex' }]);
    assert.match(third.text, /^STOP: .*Already recorded.* The daemon is ending this turn now: do nothing more in it\.$/);
    assert.deepEqual(toolLines(logs).at(-1).loopBreak, true);
    await silence('om_loop123456789', 'and again');
    assert.equal(stops.length, 1);
    // A turn end starts the count over.
    service.resetLoopGuard();
    await silence('om_loop123456789', 'next turn');
    await silence('om_loop123456789', 'next turn again');
    assert.equal(stops.length, 1);
    await silence('om_loop123456789', 'next turn, third');
    assert.equal(stops.length, 2);
  });

  it('does not count repeats that each follow real progress', async (t) => {
    const stops = [];
    const { call, inbound } = setupOn(t, { stagedReplies: true, onNoProgress: (info) => stops.push(info) });
    inbound({ messageId: 'om_task123456789', chatId: 'oc_task123456789' });
    const interim = (markdown) => call('send_message', {
      chat_id: 'oc_task123456789', markdown, interim: true, decision_id: 'message:om_task123456789',
    });
    // Three findings on one request, each sent and then retried once.
    for (const finding of ['查到了日志', '定位到了函数', '已经复现']) {
      await interim(finding);
      assert.equal((await interim(finding)).data.duplicate, true);
    }
    assert.deepEqual(stops, []);
  });

  it('asks again on the next repeat when the stop could not start yet', async (t) => {
    const asked = [];
    const answers = [{ action: 'skipped', reason: 'transition' }, { action: 'cancel' }];
    const { call, inbound } = setupOn(t, { onNoProgress: (info) => { asked.push(info.count); return answers.shift(); } });
    inbound({ messageId: 'om_busy123456789', chatId: 'oc_busy123456789' });
    const silence = () => call('stay_silent', { decision_id: 'message:om_busy123456789' });
    await silence();
    await silence();
    await silence();
    const skipped = await silence();
    assert.doesNotMatch(skipped.text, /ending this turn/);
    const started = await silence();
    assert.match(started.text, /ending this turn/);
    await silence();
    assert.deepEqual(asked, [3, 4]);
  });

  it('counts a resent delivery and a silence after it as the same repeat', async (t) => {
    const stops = [];
    const { call, inbound } = setupOn(t, { onNoProgress: (info) => stops.push(info) });
    const row = inbound({ messageId: 'om_sent123456789', chatId: 'oc_sent123456789' });
    const send = () => call('send_message', { chat_id: 'oc_sent123456789', markdown: '好的', decision_id: 'message:om_sent123456789' });
    await send();
    assert.equal((await send()).data.duplicate, true);
    await call('stay_silent', { decision_id: 'message:om_sent123456789' });
    assert.deepEqual(stops, []);
    await call('stay_silent', { decision_id: 'message:om_sent123456789' });
    assert.deepEqual(stops.map((stop) => [stop.tool, stop.decisionSeq, stop.count]), [['stay_silent', row.seq, 3]]);
  });

  it('records silence on a superseded input instead of refusing it, and keeps the OnIt for the newer one', async (t) => {
    const { store, service, call, connectors, inbound, logs } = setupOn(t, { run: reactions() });
    const older = inbound({ messageId: 'om_older12345678', chatId: 'oc_burst12345678' });
    await call('react_to_message', { message_id: 'om_older12345678', emoji_type: 'OnIt', temporary: true, decision_id: 'message:om_older12345678' });
    const newer = inbound({ messageId: 'om_newer12345678', chatId: 'oc_burst12345678' });
    const result = await call('stay_silent', { decision_id: 'message:om_older12345678' });
    assert.equal(result.text, 'Recorded: nothing is sent for this input. A newer input has arrived there; decide on that one from its own line.');
    assert.deepEqual(decisions(store).map((row) => [row.seq, row.outcome]), [[older.seq, 'silent']]);
    assert.equal(service.temporaryReactions.size, 1);
    assert.equal(connectors.calls.some(({ action }) => action === 'message.reaction.delete'), false);
    const line = toolLines(logs).at(-1);
    assert.deepEqual([line.outcome, line.superseded, line.newerSeq], ['ok', true, newer.seq]);
    assert.equal(activities(store, 'delivery.silent')[0].status, 'SUPERSEDED');
    assert.equal(activities(store, 'delivery.silent')[0].metadata.newerSeq, newer.seq);
  });

  it('removes the temporary OnIt when it decides silence on the newest input', async (t) => {
    const { store, service, call, connectors, inbound } = setupOn(t, { run: reactions() });
    inbound({ messageId: 'om_work123456789', chatId: 'oc_work123456789' });
    await call('react_to_message', { message_id: 'om_work123456789', emoji_type: 'OnIt', temporary: true, decision_id: 'message:om_work123456789' });
    assert.deepEqual(decisions(store), [], 'a working indicator is not a decision');
    await call('stay_silent', { decision_id: 'message:om_work123456789' });
    assert.equal(service.temporaryReactions.size, 0);
    assert.deepEqual(connectors.calls.at(-1), { connector: 'lark', action: 'message.reaction.delete',
      input: { message_id: 'om_work123456789', reaction_id: 'reaction_1' } });
    assert.deepEqual(store.db.prepare("SELECT status FROM cognition_journal WHERE kind = 'outbound'").all().map((row) => row.status), ['CANCELLED']);
  });

  it('refuses only what the model can fix', async (t) => {
    const { store, call, inbound } = setupOn(t);
    inbound({ messageId: 'om_known12345678', chatId: 'oc_known12345678' });
    await assert.rejects(call('stay_silent', { decision_id: 'om_known12345678' }),
      (error) => error.code === 'unknown_decision' && /^Silence not recorded: .*msg= is not accepted/.test(error.message));
    store.ingest(subjectEvent({ instance: 'calendar-personal', eventId: 'reminder-1' }));
    const work = store.ingest(subjectEvent({ instance: 'calendar-work', eventId: 'reminder-1' })).inbound;
    await assert.rejects(call('stay_silent', { decision_id: 'reminder-1' }),
      (error) => error.code === 'decision_ambiguous' && /decision_instance/.test(error.message));
    await call('stay_silent', { decision_id: 'reminder-1', decision_instance: 'calendar-work' });
    // 「发送 N」 was handled by the daemon: not an accepted input, so nothing to decide on.
    store.ingest(chatEvent({ messageId: 'om_approve123456', eventId: 'evt-approve', chatId: 'oc_known12345678' }), 'approval_reply');
    await assert.rejects(call('stay_silent', { decision_id: 'evt-approve' }), (error) => error.code === 'unknown_decision');
    assert.deepEqual(decisions(store).map((row) => [row.connector_instance, row.seq]), [['calendar-work', work.seq]]);
    await assert.rejects(call('stay_silent', { decision_id: 'message:om_known12345678' }, 'retired-token'),
      (error) => error.code === 'authority_revoked');
    assert.deepEqual(activities(store, 'delivery.blocked').map((row) => [row.metadata.tool, row.metadata.code]), [
      ['stay_silent', 'unknown_decision'], ['stay_silent', 'decision_ambiguous'], ['stay_silent', 'unknown_decision'],
      ['stay_silent', 'authority_revoked'],
    ]);
  });

  it('lets a non-chat event stay silent, which owner_only never blocks', async (t) => {
    const { store, call } = setupOn(t, { ids: ['lark', 'email'] });
    const mail = store.ingest(mailEvent({ uid: 7, kind: 'email.list' })).inbound;
    const result = await call('stay_silent', { decision_id: 'imap:1:7', reason: 'newsletter' });
    assert.equal(result.text, 'Recorded: nothing is sent for this input.');
    assert.deepEqual(decisions(store).map((row) => [row.seq, row.outcome]), [[mail.seq, 'silent']]);
  });

  it('settles a decision on every final delivery, keyed on the fence', async (t) => {
    const failing = { fail: false };
    const { store, service, call, inbound, logs } = setupOn(t, {
      run: (_connector, action) => {
        if (failing.fail) throw new Error('provider down');
        return action === 'message.react' ? { reaction_id: 'reaction_x' } : { message_id: 'om_delivered123456' };
      },
    });
    const outcome = (eventId) => store.db.prepare('SELECT outcome FROM decisions WHERE event_id = ?').get(eventId)?.outcome;

    // Silence first, then a real answer: the answer wins.
    inbound({ messageId: 'om_first12345678', chatId: 'oc_first12345678' });
    await call('stay_silent', { decision_id: 'message:om_first12345678' });
    await call('send_message', { chat_id: 'oc_first12345678', markdown: 'changed my mind', decision_id: 'message:om_first12345678' });
    assert.equal(outcome('message:om_first12345678'), 'delivered');
    assert.equal(toolLines(logs).at(-1).overrode, 'silent');
    // A delivery first: silence changes nothing.
    const after = await call('stay_silent', { decision_id: 'message:om_first12345678' });
    assert.match(after.text, /^STOP: .* A delivery is already recorded for this input; nothing to change\. Do not call stay_silent for it\.$/);
    assert.equal(outcome('message:om_first12345678'), 'delivered');

    // A temporary OnIt is not a decision, a final reaction is.
    inbound({ messageId: 'om_react12345678', chatId: 'oc_react12345678' });
    await call('react_to_message', { message_id: 'om_react12345678', emoji_type: 'OnIt', temporary: true, decision_id: 'message:om_react12345678' });
    assert.equal(outcome('message:om_react12345678'), undefined);
    await call('react_to_message', { message_id: 'om_react12345678', emoji_type: 'OK', decision_id: 'message:om_react12345678' });
    assert.equal(outcome('message:om_react12345678'), 'delivered');

    // A send that fails after the reservation was still decided.
    inbound({ messageId: 'om_failed1234567', chatId: 'oc_failed1234567' });
    failing.fail = true;
    await assert.rejects(call('send_message', { chat_id: 'oc_failed1234567', markdown: 'x', decision_id: 'message:om_failed1234567' }), /provider down/);
    failing.fail = false;
    assert.equal(outcome('message:om_failed1234567'), 'delivered');

    // Attribution to an older input settles the fence, not the older one.
    const older = inbound({ messageId: 'om_olderA1234567', chatId: 'oc_attrib1234567' });
    const newest = inbound({ messageId: 'om_newestB123456', chatId: 'oc_attrib1234567' });
    await call('send_message', { chat_id: 'oc_attrib1234567', markdown: 'both', decision_id: 'message:om_newestB123456',
      response_to_id: 'message:om_olderA1234567' });
    assert.equal(outcome('message:om_olderA1234567'), undefined);
    assert.equal(store.coveringDecision(newest.scopeKey, older.seq), 'delivered');

    // A dry run is a decision too.
    service.config.dryRun = true;
    inbound({ messageId: 'om_dryrun1234567', chatId: 'oc_dryrun1234567' });
    await call('send_message', { chat_id: 'oc_dryrun1234567', markdown: 'x', decision_id: 'message:om_dryrun1234567' });
    assert.equal(outcome('message:om_dryrun1234567'), 'delivered');
  });

  it('records a requested retraction as the decision on the request, through the freshness fence', async (t) => {
    const { store, service, call, connectors, inbound, logs } = setupOn(t);
    const retract = service.tools().find((tool) => tool.name === 'retract_message');
    assert.deepEqual(Object.keys(retract.inputSchema.properties), ['source', 'message_id', 'decision_id', 'decision_instance']);
    assert.deepEqual(retract.inputSchema.required, ['message_id']);

    inbound({ messageId: 'om_ask123456789', chatId: 'oc_undo123456789' });
    await call('send_message', { chat_id: 'oc_undo123456789', markdown: 'oops', decision_id: 'message:om_ask123456789' });
    const request = inbound({ messageId: 'om_undo123456789', chatId: 'oc_undo123456789' });
    const result = await call('retract_message', { message_id: 'om_delivered123456', decision_id: 'message:om_undo123456789' });
    assert.match(result.text, /^Delivered retraction of message_id=om_delivered123456/);
    assert.deepEqual(decisions(store).map((row) => [row.event_id, row.outcome]),
      [['message:om_ask123456789', 'delivered'], ['message:om_undo123456789', 'delivered']]);
    assert.equal(toolLines(logs).at(-1).decisionSeq, request.seq);
    // Nothing is left to remind about, and a silence on it changes nothing.
    assert.equal(store.coveringDecision(request.scopeKey, request.seq), 'delivered');
    assert.match((await call('stay_silent', { decision_id: 'message:om_undo123456789' })).text, /^STOP: /);

    // Fenced like every other delivery: a newer input there blocks it.
    inbound({ messageId: 'om_later12345678', chatId: 'oc_undo123456789' });
    const before = connectors.calls.length;
    await assert.rejects(call('retract_message', { message_id: 'om_other12345678', decision_id: 'message:om_undo123456789' }),
      (error) => error.code === 'stale_decision');
    assert.equal(connectors.calls.length, before);
    // Without decision_id it behaves as before and records nothing.
    await call('retract_message', { message_id: 'om_other12345678' });
    assert.equal(decisions(store).length, 2);
  });

  it('counts an approval draft as the decision on the mail', async (t) => {
    const { store, call } = setupOn(t, { ids: ['lark', 'email'], profile: 'test-profile',
      run: (connector) => (connector === 'email' ? { sent: true } : { message_id: 'om_card12345678', chat_id: 'oc_p2p12345678' }) });
    store.ingest(mailEvent({ uid: 7 }));
    await call('reply_message', { message_id: 'mail:1:7', markdown: '周四可以', decision_id: 'imap:1:7' });
    assert.deepEqual(decisions(store).map((row) => [row.event_id, row.outcome]), [['imap:1:7', 'delivered']]);
  });

  it('says STOP only for a call that changed nothing, never for a retry that fixed a refusal', async (t) => {
    const { store, call } = setupOn(t);
    store.ingest(subjectEvent({ instance: 'calendar-personal', eventId: 'reminder-1' }));
    store.ingest(subjectEvent({ instance: 'calendar-work', eventId: 'reminder-1' }));
    await assert.rejects(call('stay_silent', { decision_id: 'reminder-1' }), (error) => error.code === 'decision_ambiguous');
    // The corrected retry is a real record.
    const fixed = await call('stay_silent', { decision_id: 'reminder-1', decision_instance: 'calendar-work' });
    assert.equal(fixed.text, 'Recorded: nothing is sent for this input.');
    // A refused attempt followed by a success on another instance is no repeat either.
    await assert.rejects(call('stay_silent', { decision_id: 'reminder-1' }), (error) => error.code === 'decision_ambiguous');
    const second = await call('stay_silent', { decision_id: 'reminder-1', decision_instance: 'calendar-personal' });
    assert.equal(second.text, 'Recorded: nothing is sent for this input.');
    assert.match((await call('stay_silent', { decision_id: 'reminder-1', decision_instance: 'calendar-work' })).text,
      /^STOP: this stay_silent call changed nothing for this input\. .*Already recorded/);
  });
});

describe('stay_silent switched off (the default)', () => {
  it('offers no stay_silent and keeps deliveries as they were, only noting in the ledger what they answered', async (t) => {
    const { store, service, call, inbound } = setup(t, { ids: ['lark', 'weixin', 'email'] });
    assert.equal(service.tools().some((tool) => tool.name === 'stay_silent'), false);
    const row = inbound({ messageId: 'om_off123456789', chatId: 'oc_off123456789' });
    await assert.rejects(call('stay_silent', { decision_id: 'message:om_off123456789' }), (error) => error.code === 'unknown_tool');
    await call('send_message', { chat_id: 'oc_off123456789', markdown: 'hi', decision_id: 'message:om_off123456789' });
    // What a loop stop's recovery reads to skip an input already answered.
    assert.deepEqual(store.db.prepare('SELECT seq, outcome FROM decisions').all().map((entry) => ({ ...entry })),
      [{ seq: row.seq, outcome: 'delivered' }]);
    assert.equal(store.db.prepare("SELECT COUNT(*) AS n FROM activities WHERE kind IN ('delivery.silent', 'delivery.blocked')").get().n, 0);
  });

  it('keeps retract_message exactly as it was: no decision_id in its schema or input', async (t) => {
    const { service, call } = setup(t);
    const retract = service.tools().find((tool) => tool.name === 'retract_message');
    const defined = TOOLS.find((tool) => tool.name === 'retract_message');
    assert.deepEqual(retract, publicTool(defined));
    assert.deepEqual(Object.keys(retract.inputSchema.properties), ['source', 'message_id']);
    assert.equal(retract.inputSchema.additionalProperties, false);
    await assert.rejects(call('retract_message', { message_id: 'om_delivered123456', decision_id: 'message:om_x' }),
      (error) => error.code === 'invalid_input');
  });
});

describe('staged replies', () => {
  const reactions = () => {
    let count = 0;
    return (_connector, action) => {
      if (action === 'message.react') return { reaction_id: `reaction_${count += 1}` };
      if (action === 'message.reaction.delete') return { deleted: true };
      return { message_id: `om_delivered${String(count += 1).padStart(6, '0')}` };
    };
  };
  const outcome = (store, eventId) => store.db.prepare('SELECT outcome FROM decisions WHERE event_id = ?').get(eventId)?.outcome;
  const interimRows = (store) => store.db.prepare(`SELECT text FROM cognition_journal
    WHERE kind = 'outbound' AND json_extract(detail_json, '$.interim') = 1 ORDER BY seq`).all().map((row) => row.text);

  it('offers interim only with the switch on; off, the tools are exactly as they were', async (t) => {
    const off = setup(t);
    for (const name of ['send_message', 'reply_message', 'react_to_message']) {
      assert.deepEqual(off.service.tools().find((tool) => tool.name === name), publicTool(TOOLS.find((tool) => tool.name === name)), name);
    }
    off.inbound({ messageId: 'om_off123456789', chatId: 'oc_off123456789' });
    await assert.rejects(off.call('send_message', { chat_id: 'oc_off123456789', markdown: 'x', interim: true,
      decision_id: 'message:om_off123456789' }), (error) => error.code === 'invalid_input');
    assert.equal(off.connectors.calls.length, 0);

    const on = setup(t, { stagedReplies: true });
    for (const name of ['send_message', 'reply_message']) {
      const tool = on.service.tools().find((candidate) => candidate.name === name);
      assert.equal(tool.inputSchema.properties.interim.type, 'boolean', name);
      assert.equal(tool.inputSchema.required.includes('interim'), false, name);
    }
    const react = on.service.tools().find((tool) => tool.name === 'react_to_message');
    assert.match(react.description, /Set temporary=true \(OnIt\) when you take on a request that needs real work first/);
    assert.doesNotMatch(react.description, /idle=/);
    assert.equal(on.service.tools().some((tool) => tool.name === 'stay_silent'), false, 'staged replies do not bring explicit silence');
  });

  it('an interim finding keeps the OnIt and decides nothing; the final delivery clears it and decides', async (t) => {
    const { store, service, call, inbound, logs } = setup(t, { stagedReplies: true, explicitSilence: true, run: reactions() });
    inbound({ messageId: 'om_task123456789', chatId: 'oc_task123456789' });
    const decision = { decision_id: 'message:om_task123456789' };
    await call('react_to_message', { message_id: 'om_task123456789', emoji_type: 'OnIt', temporary: true, ...decision });
    await call('send_message', { chat_id: 'oc_task123456789', markdown: '注意到 14:05 起上游调用量翻了一倍', interim: true, ...decision });
    await call('reply_message', { message_id: 'om_task123456789', markdown: '上游这段时间有大量限流报错', interim: true, ...decision });
    assert.equal(service.temporaryReactions.size, 1);
    assert.equal(outcome(store, 'message:om_task123456789'), undefined);
    assert.deepEqual(interimRows(store), ['注意到 14:05 起上游调用量翻了一倍', '上游这段时间有大量限流报错']);
    assert.deepEqual(logs.filter((line) => line.message === 'agent_tool' && line.interim).map((line) => line.tool), ['send_message', 'reply_message']);

    await call('send_message', { chat_id: 'oc_task123456789', markdown: '清楚了：上游 redis 挂了，限流器没生效', ...decision });
    assert.equal(service.temporaryReactions.size, 0);
    assert.equal(outcome(store, 'message:om_task123456789'), 'delivered');
    assert.equal(interimRows(store).length, 2, 'the final delivery is not interim');
  });

  it('an interim message is text, lands where its decision is, inside its thread, and is never a mail draft', async (t) => {
    const { store, call, connectors, inbound, workspace } = setup(t, { stagedReplies: true, ids: ['lark', 'email'] });
    inbound({ messageId: 'om_here123456789', chatId: 'oc_here123456789' });
    inbound({ messageId: 'om_there12345678', chatId: 'oc_there12345678' });
    inbound({ messageId: 'om_threadA123456', chatId: 'oc_here123456789', threadId: 'omt_threadA12345' });
    inbound({ messageId: 'om_threadB123456', chatId: 'oc_here123456789', threadId: 'omt_threadB12345' });
    store.ingest(mailEvent({ uid: 9 }));
    const main = { decision_id: 'message:om_here123456789', interim: true };
    const thread = { decision_id: 'message:om_threadA123456', interim: true };
    fs.writeFileSync(path.join(workspace, 'report.txt'), 'x');
    const textOnly = /text only: use markdown/;
    const elsewhere = /goes only where decision_id is/;
    const refusals = [
      ['send_message', { chat_id: 'oc_here123456789', file_path: 'report.txt', ...main }, textOnly],
      ['send_message', { chat_id: 'oc_here123456789', choices: { prompt: 'A or B?', options: [{ key: 'a', label: 'A' }, { key: 'b', label: 'B' }] }, ...main }, textOnly],
      ['send_message', { chat_id: 'oc_there12345678', markdown: 'another chat', ...main }, elsewhere],
      ['reply_message', { message_id: 'om_there12345678', markdown: 'another chat', ...main }, elsewhere],
      // Where a reply to a message nobody has seen lands is unknown.
      ['reply_message', { source: 'lark', message_id: 'om_unknown123456', markdown: 'who knows where', ...main }, elsewhere],
      ['reply_message', { message_id: 'mail:1:9', markdown: 'draft', ...main }, elsewhere],
      // A thread's request: a plain send leaves the thread, and another thread is not it.
      ['send_message', { chat_id: 'oc_here123456789', markdown: 'out of the thread', ...thread }, elsewhere],
      ['reply_message', { message_id: 'om_threadB123456', markdown: 'the other thread', reply_in_thread: true, ...thread }, elsewhere],
      ['reply_message', { message_id: 'om_threadA123456', markdown: 'from the main chat', reply_in_thread: true, ...main }, elsewhere],
    ];
    for (const [tool, input, message] of refusals) {
      await assert.rejects(call(tool, input), (error) => error.code === 'invalid_input' && message.test(error.message), `${tool} ${JSON.stringify(input)}`);
    }
    assert.equal(connectors.calls.length, 0);

    await call('send_message', { chat_id: 'oc_here123456789', markdown: 'in the chat', ...main });
    await call('reply_message', { message_id: 'om_here123456789', markdown: 'under the request', reply_in_thread: true, ...main });
    await call('reply_message', { message_id: 'om_threadA123456', markdown: 'in its thread', reply_in_thread: true, ...thread });
    assert.equal(connectors.calls.length, 3);
  });

  it('a turn that ends takes every OnIt down, and forgets each before it returns', async (t) => {
    const { service, call, connectors, inbound } = setup(t, { stagedReplies: true, run: reactions() });
    for (const id of ['om_turnA12345678', 'om_turnB12345678']) {
      inbound({ messageId: id, chatId: id.replace('om_', 'oc_') });
      await call('react_to_message', { message_id: id, emoji_type: 'OnIt', temporary: true, decision_id: `message:${id}` });
    }
    assert.equal(service.temporaryReactions.size, 2);
    const cleared = service.clearTemporaryReactions('turn_end');
    assert.equal(service.temporaryReactions.size, 0);
    await cleared;
    assert.deepEqual(connectors.calls.filter((item) => item.action === 'message.reaction.delete').map((item) => item.input.message_id),
      ['om_turnA12345678', 'om_turnB12345678']);
  });

  it('closing waits for a removal a turn\'s end started, so the Connectors stop after it', async (t) => {
    let finish;
    const run = (_connector, action) => {
      if (action === 'message.react') return { reaction_id: 'reaction_slow' };
      if (action === 'message.reaction.delete') return new Promise((resolve) => { finish = () => resolve({ deleted: true }); });
      return { message_id: 'om_delivered123456' };
    };
    const { service, call, inbound } = setup(t, { stagedReplies: true, run });
    inbound({ messageId: 'om_slow123456789', chatId: 'oc_slow123456789' });
    await call('react_to_message', { message_id: 'om_slow123456789', emoji_type: 'OnIt', temporary: true, decision_id: 'message:om_slow123456789' });
    void service.clearTemporaryReactions('turn_end');
    assert.equal(service.temporaryReactions.size, 0);
    let closed = false;
    const closing = service.close().then(() => { closed = true; });
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(closed, false, 'the removal is still under way');
    finish();
    await closing;
    assert.equal(service.reactionRemovals.size, 0);
  });
});
