// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, it } from 'node:test';
import { PersonalAgentDaemon } from '../src/daemon.mjs';
import { startHttpApi } from '../src/http-api.mjs';
import { Store } from '../src/store.mjs';
import { chatEvent } from './event-fixture.mjs';

const projectDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const quietLogger = {
  debug() {},
  info() {},
  warn() {},
  error() {},
  child() { return this; },
};

function event(id, content = `message ${id}`, overrides = {}) {
  return chatEvent({
    type: 'im.message.receive_v1',
    eventId: `evt-${id}`,
    messageId: `om-${id}`,
    conversationKey: `profile:tenant:oc-${id}:main`,
    chatId: `oc-${id}`,
    chatType: 'group',
    messageType: 'text',
    senderId: 'ou-user',
    senderType: 'user',
    rootId: null,
    parentId: null,
    threadId: null,
    threadRootId: null,
    content,
    mentions: [],
    createTimeMs: 1_700_000_000_000,
    raw: {},
    ...overrides,
  });
}

function request(port, pathname, { method = 'GET', headers = {} } = {}) {
  return new Promise((resolve, reject) => {
    const call = http.request({
      hostname: '127.0.0.1',
      port,
      path: pathname,
      method,
      headers,
    }, (response) => {
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => resolve({
        status: response.statusCode,
        headers: response.headers,
        body: Buffer.concat(chunks).toString('utf8'),
      }));
    });
    call.on('error', reject);
    call.end();
  });
}

describe('activity store', { concurrency: false }, () => {
  let directory;
  let databasePath;
  let store;

  beforeEach(() => {
    directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-activity-'));
    databasePath = path.join(directory, 'state.sqlite3');
    store = new Store(databasePath, quietLogger);
  });

  afterEach(() => {
    try { store.close(); } catch {}
    fs.rmSync(directory, { recursive: true, force: true });
  });

  it('records message.received once, deduplicated, without leaking private ids or raw fields', () => {
    const now = 1_700_000_100_000;
    const inbound = event('private', 'show this user-authored text', {
      conversationKey: 'profile:tenant:oc-private-secret:main',
      chatId: 'oc-private-secret',
      messageId: 'om-private-message',
      eventId: 'evt-private-event',
      raw: { token: 'raw-token-sentinel' },
    });
    assert.equal(store.ingest(inbound, null, now).inserted, true);
    assert.equal(store.ingest(inbound, null, now + 1).inserted, false);

    const feed = store.listActivities({ limit: 100 }).activities;
    assert.deepEqual(feed.map((a) => a.kind), ['message.received']);
    const received = feed[0];
    assert.equal(received.summary, 'show this user-authored text');
    assert.match(received.conversationRef, /^[a-f0-9]{12}$/);

    const serialized = JSON.stringify(feed);
    for (const secret of [
      inbound.conversationKey, inbound.chatId, inbound.messageId, inbound.eventId, 'raw-token-sentinel',
    ]) {
      assert.equal(serialized.includes(secret), false, `leaked ${secret}`);
    }

    store.recordActivity({
      eventKey: 'privacy:metadata-whitelist',
      kind: 'system.maintenance',
      summary: 'safe',
      metadata: {
        chatId: 'uuid-chat-sentinel',
        conversationKey: 'uuid-conversation-sentinel',
        reasonCode: 'direct_request',
      },
    });
    const privacy = store.listActivities({ limit: 1 }).activities[0];
    assert.deepEqual(privacy.metadata, { reasonCode: 'direct_request' });
    assert.equal(JSON.stringify(privacy).includes('uuid-'), false);
  });

  it('groups main chat and thread activity under a stable private source reference', () => {
    const now = 1_700_000_150_000;
    const main = event('source-main', 'main message', {
      chatId: 'oc-shared-private-source',
      chatType: 'p2p',
      conversationKey: 'profile:tenant:oc-shared-private-source:main',
    });
    const thread = event('source-thread', 'thread message', {
      chatId: 'oc-shared-private-source',
      chatType: 'p2p',
      conversationKey: 'profile:tenant:oc-shared-private-source:thread:omt-private-thread',
      threadId: 'omt-private-thread',
    });
    const other = event('source-other', 'other message', {
      chatId: 'oc-other-private-source',
      conversationKey: 'profile:tenant:oc-other-private-source:main',
    });
    const mainResult = store.ingest(main, 'ambient', now);
    const threadResult = store.ingest(thread, 'ambient', now + 1);
    const otherResult = store.ingest(other, 'ambient', now + 2);

    const rows = store.listActivities({ limit: 20 }).activities;
    const byInbound = (seq, activityRows = rows) => (
      activityRows.find((row) => row.inboundSeq === seq)
    );
    const mainActivity = byInbound(mainResult.inbound.seq);
    const threadActivity = byInbound(threadResult.inbound.seq);
    const otherActivity = byInbound(otherResult.inbound.seq);
    assert.ok(mainActivity && threadActivity && otherActivity);
    assert.equal(mainActivity.sourceRef, threadActivity.sourceRef);
    assert.notEqual(mainActivity.conversationRef, threadActivity.conversationRef);
    assert.notEqual(mainActivity.sourceRef, otherActivity.sourceRef);
    assert.equal(mainActivity.sourceType, 'p2p');
    assert.equal(otherActivity.sourceType, 'group');
    assert.match(mainActivity.sourceRef, /^[a-f0-9]{12}$/);
    assert.equal(JSON.stringify(rows).includes('oc-shared-private-source'), false);

    const sourceRef = mainActivity.sourceRef;
    const removed = store.prune(1, now + 10);
    assert.ok(removed.inbound > 0);
    assert.equal(
      byInbound(mainResult.inbound.seq, store.listActivities({ limit: 20 }).activities).sourceRef,
      sourceRef,
    );
    store.close();
    store = new Store(databasePath, quietLogger);
    assert.equal(
      byInbound(mainResult.inbound.seq, store.listActivities({ limit: 20 }).activities).sourceRef,
      sourceRef,
    );
  });

  it('persists a resolved group name on old and new activity without exposing its chat id', () => {
    const now = 1_700_000_175_000;
    const chatId = 'oc-private-source-name-sentinel';
    const group = event('named-group', 'hello from the group', {
      chatId,
      conversationKey: `profile:tenant:${chatId}:main`,
    });
    const result = store.ingest(group, null, now);
    assert.equal(result.inserted, true);
    assert.equal(store.listActivities({ limit: 10 }).activities[0].sourceName, null);

    assert.equal(store.saveChatSourceName(chatId, '日常协作群', now + 86_400_000, now + 1), true);
    store.recordActivity({
      eventKey: 'named-group:after-resolution',
      kind: 'system.maintenance',
      conversationKey: group.conversationKey,
      sourceType: 'group',
      summary: 'after name resolution',
      occurredAtMs: now + 2,
    });
    let activities = store.listActivities({ limit: 10 }).activities;
    assert.equal(activities.every((activity) => activity.sourceName === '日常协作群'), true);
    assert.equal(JSON.stringify(activities).includes(chatId), false);

    store.close();
    store = new Store(databasePath, quietLogger);
    activities = store.listActivities({ limit: 10 }).activities;
    assert.equal(activities.every((activity) => activity.sourceName === '日常协作群'), true);
    assert.equal(JSON.stringify(activities).includes(chatId), false);
  });

  it('publishes tool input/output and token counts while masking Lark ids and secrets', () => {
    const incoming = event('tool-source', 'run a tool', {
      messageId: 'om-tool-target-private',
      chatId: 'oc-tool-chat-private',
      conversationKey: 'profile:tenant:oc-tool-chat-private:main',
    });
    const result = store.ingest(incoming, null, 1_700_000_180_000);
    store.recordActivity({
      eventKey: 'codex:tool:completed',
      kind: 'agent.tool',
      turnId: 'turn-app-server-1',
      inboundSeq: result.inbound.seq,
      status: 'COMPLETED',
      summary: 'openviking · read_resource',
      metadata: {
        itemType: 'mcpToolCall',
        toolLabel: 'openviking · read_resource',
        toolName: 'read_resource',
        toolServer: 'openviking',
        toolStatus: 'completed',
        durationMs: 42,
        input: '{"uri":"viking://user/x","message_id":"om_abcdef123456"}',
        output: 'Authorization: Bearer abc.def.ghi',
        inputTokens: 1200,
        arguments: { uri: 'viking://private/sentinel' },
      },
    }, 1_700_000_180_042);

    const tool = store.listActivities({ limit: 10 }).activities
      .find((activity) => activity.kind === 'agent.tool');
    assert.ok(tool);
    assert.equal(tool.turnId, 'turn-app-server-1');
    assert.equal(tool.sourceType, 'group');
    assert.deepEqual(tool.metadata, {
      durationMs: 42,
      input: '{"uri":"viking://user/x","message_id":"[LARK_ID]"}',
      inputTokens: 1200,
      itemType: 'mcpToolCall',
      output: 'Authorization: [REDACTED] [REDACTED]',
      toolLabel: 'openviking · read_resource',
      toolName: 'read_resource',
      toolServer: 'openviking',
      toolStatus: 'completed',
    });
    assert.doesNotMatch(JSON.stringify(tool), /viking:\/\/private|om-tool-target|oc-tool-chat/);
  });

  it('tails activities after a cursor oldest-first without skipping rows', () => {
    for (let index = 1; index <= 5; index += 1) {
      store.recordActivity({ eventKey: `tail:${index}`, kind: 'system.maintenance', occurredAtMs: index }, index);
    }
    const ids = store.db.prepare('SELECT id FROM activities ORDER BY id').all().map((row) => Number(row.id));
    const page = store.listActivities({ afterId: ids[1], limit: 2 });
    assert.deepEqual(page.activities.map((item) => item.id), [ids[3], ids[2]]);
    assert.equal(page.hasNewer, true);
    const rest = store.listActivities({ afterId: ids[3], limit: 2 });
    assert.deepEqual(rest.activities.map((item) => item.id), [ids[4]]);
    assert.equal(rest.hasNewer, false);
  });

  it('closes stale subscriber lifecycle rows so only the current listener is running', () => {
    for (let index = 1; index <= 3; index += 1) {
      store.closeRunningSubscriberActivities();
      store.recordActivity({
        eventKey: `run:${index}:subscriber-started`,
        kind: 'system.subscriber_started',
        status: 'RUNNING',
        summary: '正在接收飞书消息事件',
        occurredAtMs: index,
      }, index);
    }
    const rows = store.listActivities({ limit: 10 }).activities
      .filter((row) => row.kind === 'system.subscriber_started');
    assert.equal(rows.filter((row) => row.status === 'RUNNING').length, 1);
    assert.equal(rows.filter((row) => row.status === 'STOPPED').length, 2);
    assert.equal(rows.every((row) => row.sourceRef === 'system'), true);
  });

  it('marks a filtered message ignored with its reason and blanks the body', () => {
    const ignored = event('ignored', 'ignored-private-body');
    store.ingest(ignored, 'unsupported_message_type', 1_700_000_200_000);
    const activities = store.listActivities({ limit: 100 }).activities;
    const found = activities.find((a) => a.kind === 'message.ignored');
    assert.ok(found);
    assert.deepEqual(
      ['severity', 'priority', 'status'].map((key) => found[key]),
      ['info', 20, 'RULE_IGNORED'],
    );
    assert.equal(JSON.stringify(activities).includes('ignored-private-body'), false);
  });

  it('uses a stable keyset cursor when a newer activity arrives between pages', () => {
    for (let index = 1; index <= 5; index += 1) {
      assert.equal(store.recordActivity({
        eventKey: `manual:${index}`,
        kind: 'system.maintenance',
        summary: `old ${index}`,
        occurredAtMs: index,
      }, index), true);
    }
    const oldIds = store.db.prepare('SELECT id FROM activities ORDER BY id DESC')
      .all()
      .map((row) => Number(row.id));

    const firstPage = store.listActivities({ limit: 2 });
    assert.deepEqual(firstPage.activities.map((item) => item.id), oldIds.slice(0, 2));
    assert.equal(store.recordActivity({
      eventKey: 'manual:new-arrival',
      kind: 'system.ready',
      summary: 'new arrival',
      occurredAtMs: 10,
    }, 10), true);

    const seen = [...firstPage.activities.map((item) => item.id)];
    let cursor = firstPage.nextCursor;
    while (cursor !== null) {
      const page = store.listActivities({ limit: 2, beforeId: cursor });
      seen.push(...page.activities.map((item) => item.id));
      cursor = page.nextCursor;
    }
    assert.deepEqual(seen, oldIds);
    assert.equal(new Set(seen).size, oldIds.length);
  });

  it('keeps the append-only activity history when inbound rows are pruned', () => {
    store.ingest(event('old', 'old body'), null, 1_000);
    const before = store.listActivities({ limit: 100 }).activities;
    const removed = store.prune(1_000, 10_000);
    assert.ok(removed.inbound > 0);
    assert.equal(store.stats().activities, before.length);
    assert.deepEqual(
      store.listActivities({ limit: 100 }).activities.map((item) => item.id),
      before.map((item) => item.id),
    );
  });

  it('lists cognition epochs as selectable activity sessions without native ids', () => {
    store.initializeCognition('codex', 'tok-1');
    store.recordActivity({
      eventKey: 'boot',
      kind: 'system.starting',
      summary: 'daemon starting',
      occurredAtMs: 1_000,
    }, 1_000);
    store.appendCognitionJournal({
      kind: 'inbound', text: 'hello', atMs: 2_000, epoch: 1, inboundSeq: 1,
    });
    store.recordActivity({
      eventKey: 'msg-1',
      kind: 'message.received',
      summary: 'hello',
      occurredAtMs: 2_000,
    }, 2_000);

    store.bumpCognitionEpoch('claude', 'tok-2');
    store.appendCognitionJournal({
      kind: 'inbound', text: 'after /new', atMs: 5_000, epoch: 2, inboundSeq: 2,
    });
    store.recordActivity({
      eventKey: 'rotate',
      kind: 'agent.session_rotated',
      summary: '已开启新会话',
      occurredAtMs: 5_000,
    }, 5_000);
    store.recordActivity({
      eventKey: 'msg-2',
      kind: 'message.received',
      summary: 'after /new',
      occurredAtMs: 5_500,
    }, 5_500);

    const sessions = store.listActivitySessions();
    assert.deepEqual(sessions.map((session) => session.epoch), [2, 1]);
    assert.equal(sessions[0].current, true);
    assert.equal(sessions[0].runtime, 'claude');
    assert.equal(sessions[1].current, false);
    assert.equal(sessions[1].runtime, null);
    assert.equal(sessions[0].activityCount, 2);
    assert.equal(sessions[1].activityCount, 2);
    assert.equal(JSON.stringify(sessions).includes('tok-'), false);

    const current = store.listActivities({ limit: 10, epoch: 2 }).activities;
    assert.deepEqual(current.map((item) => item.kind), ['message.received', 'agent.session_rotated']);
    const previous = store.listActivities({ limit: 10, epoch: 1 }).activities;
    assert.deepEqual(previous.map((item) => item.kind), ['message.received', 'system.starting']);
    assert.deepEqual(store.listActivities({ limit: 10, epoch: 99 }), {
      activities: [],
      nextCursor: null,
    });
  });

  it('keeps a current session row after /new before the next journal entry', () => {
    store.initializeCognition('codex', 'tok-1');
    store.appendCognitionJournal({ kind: 'inbound', text: 'old', atMs: 10, epoch: 1 });
    store.recordActivity({
      eventKey: 'old', kind: 'message.received', summary: 'old', occurredAtMs: 10,
    }, 10);
    store.bumpCognitionEpoch('codex', 'tok-2');
    const sessions = store.listActivitySessions();
    assert.equal(sessions[0].epoch, 2);
    assert.equal(sessions[0].current, true);
    assert.equal(sessions[0].activityCount, 0);
    assert.equal(store.listActivities({ limit: 10, epoch: 1 }).activities.length, 1);
  });
});

describe('daemon activity API', { concurrency: false }, () => {
  let directory;
  let daemon;
  let port;

  beforeEach(async () => {
    directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-activity-http-'));
    daemon = new PersonalAgentDaemon({
      dataDir: directory,
      projectDir,
      host: '127.0.0.1',
      port: 0,
    }, quietLogger);
    daemon.store = new Store(path.join(directory, 'state.sqlite3'), quietLogger);
    daemon.connectors = { subscriptionRunning: true, anySubscriptionRunning: true, snapshot: () => ({}) };
    daemon.ready = true;
    daemon.store.recordActivity({
      eventKey: 'http:test',
      kind: 'system.ready',
      summary: 'dashboard is ready',
      conversationKey: 'profile:tenant:oc-http-private:main',
      status: 'READY',
    });
    daemon.httpServer = await startHttpApi({
      config: daemon.config,
      store: daemon.store,
      logger: quietLogger,
      serviceSnapshot: (options) => daemon.serviceSnapshot(options),
    });
    port = daemon.httpServer.address().port;
  });

  afterEach(async () => {
    if (daemon?.httpServer?.listening) {
      await new Promise((resolve) => daemon.httpServer.close(resolve));
    }
    try { daemon?.store?.close(); } catch {}
    fs.rmSync(directory, { recursive: true, force: true });
  });

  it('serves only the private activity API and health endpoints with strict response headers', async () => {
    const page = await request(port, '/');
    assert.equal(page.status, 404);

    const css = await request(port, '/activity.css');
    assert.equal(css.status, 404);
    const javascript = await request(port, '/activity.js');
    assert.equal(javascript.status, 404);

    const api = await request(port, '/api/activity?limit=1');
    assert.equal(api.status, 200);
    assert.match(api.headers['content-type'], /^application\/json/);
    assert.equal(api.headers['cache-control'], 'no-store');
    assert.match(api.headers['content-security-policy'], /default-src 'self'/);
    assert.equal(api.headers['x-content-type-options'], 'nosniff');
    assert.equal(api.headers['referrer-policy'], 'no-referrer');
    const payload = JSON.parse(api.body);
    assert.equal(payload.service.ready, true);
    assert.equal(payload.service.subscriber, 'running');
    assert.equal(payload.activities.length, 1);
    assert.equal(payload.activities[0].kind, 'system.ready');
    assert.match(payload.activities[0].conversationRef, /^[a-f0-9]{12}$/);
    assert.equal(api.body.includes('profile:tenant:oc-http-private:main'), false);
    assert.equal(payload.epoch, null);
    assert.ok(Array.isArray(payload.sessions));
    assert.equal(payload.sessions[0].current, true);
    assert.equal(JSON.stringify(payload.sessions).includes('sessionId'), false);
  });

  it('filters the activity feed by cognition epoch, including epoch=current', async () => {
    daemon.store.initializeCognition('codex', 'tok-1');
    daemon.store.appendCognitionJournal({
      kind: 'inbound', text: 'old', atMs: 10, epoch: 1,
    });
    daemon.store.recordActivity({
      eventKey: 'old', kind: 'message.received', summary: 'old line', occurredAtMs: 10,
    }, 10);
    daemon.store.bumpCognitionEpoch('claude', 'tok-2');
    daemon.store.appendCognitionJournal({
      kind: 'inbound', text: 'new', atMs: 50, epoch: 2,
    });
    daemon.store.recordActivity({
      eventKey: 'new', kind: 'message.received', summary: 'new line', occurredAtMs: 50,
    }, 50);

    const current = JSON.parse((await request(port, '/api/activity?epoch=current')).body);
    assert.equal(current.epoch, 2);
    assert.equal(current.activities.some((item) => item.summary === 'new line'), true);
    assert.equal(current.activities.some((item) => item.summary === 'old line'), false);
    assert.equal(current.sessions[0].runtime, 'claude');

    const previous = JSON.parse((await request(port, '/api/activity?epoch=1')).body);
    assert.equal(previous.epoch, 1);
    assert.equal(previous.activities.some((item) => item.summary === 'old line'), true);
    assert.equal(previous.activities.some((item) => item.summary === 'new line'), false);
  });

  it('rejects invalid queries, path traversal, non-loopback hosts, and cross-origin requests', async () => {
    for (const pathname of [
      '/api/activity?limit=0',
      '/api/activity?limit=1000',
      '/api/activity?before=nope',
      '/api/activity?limit=1&limit=2',
      '/api/activity?unknown=1',
      '/api/activity?epoch=nope',
      '/api/activity?epoch=0',
      '/api/activity?epoch=current&epoch=1',
    ]) {
      const response = await request(port, pathname);
      assert.equal(response.status, 400, pathname);
      assert.equal(response.headers['cache-control'], 'no-store');
    }

    assert.equal((await request(port, '/%2e%2e/package.json')).status, 404);
    assert.equal((await request(port, '/', { headers: { host: 'example.test' } })).status, 403);
    assert.equal((await request(port, '/', {
      headers: { origin: `http://localhost:${port}` },
    })).status, 403);
    const method = await request(port, '/', { method: 'POST' });
    assert.equal(method.status, 405);
    assert.equal(method.headers.allow, 'GET');
  });

  it('returns a private 500 without stopping the daemon when an API read fails', async () => {
    const original = daemon.store.listActivities;
    daemon.store.listActivities = () => { throw new Error('private database detail'); };
    const failed = await request(port, '/api/activity');
    assert.equal(failed.status, 500);
    assert.deepEqual(JSON.parse(failed.body), { error: 'internal_error' });
    assert.equal(failed.body.includes('private database detail'), false);
    assert.equal(daemon.ready, true);

    daemon.store.listActivities = original.bind(daemon.store);
    const ready = await request(port, '/ready');
    assert.equal(ready.status, 200);
  });
});
