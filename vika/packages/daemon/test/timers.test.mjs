// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, it } from 'node:test';
import { ActionService } from '../src/actions/service.mjs';
import { normalizeEvent } from '../src/event.mjs';
import { Store } from '../src/store.mjs';
import { TimerConnector, TimerRepository } from '../src/timers.mjs';
import { chatEvent } from './event-fixture.mjs';
import { fakeConnectors } from './action-fixture.mjs';

const TOKEN = 'runtime-token';
const OWNER = { id: 'ou_owner12345678', target: { user_id: 'ou_owner12345678' } };
const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

function setup(t, { now = 1_790_000_000_000, run = null } = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'timers-'));
  const store = new Store(path.join(directory, 'state.sqlite3'), logger);
  store.initializeCognition('codex', TOKEN);
  const connectors = fakeConnectors({ run, owners: { lark: OWNER } });
  const repository = new TimerRepository(store.db);
  let wakeups = 0;
  const service = new ActionService({
    config: {
      workspace: path.join(directory, 'workspace'),
      tempDir: path.join(directory, 'tmp'),
      maxReplyChars: 1_000,
      channel: 'lark',
    },
    store,
    connectors,
    logger,
    timers: repository,
    clock: typeof now === 'function' ? now : () => now,
    onTimersChanged: () => { wakeups += 1; },
  });
  fs.mkdirSync(path.join(directory, 'workspace'));
  t.after(() => { store.close(); fs.rmSync(directory, { recursive: true, force: true }); });
  const call = (tool, input = {}) => service.call(tool, input, { authority: TOKEN });
  const acceptChat = (fields = {}) => store.ingest(chatEvent({
    messageId: 'om_timer_request123',
    eventId: 'evt-timer-request',
    chatId: 'oc_timer_setup123',
    chatType: 'p2p',
    ...fields,
  })).inbound;
  return { store, connectors, repository, service, call, acceptChat, wakeups: () => wakeups };
}

describe('timer MCP tools', () => {
  it('creates one active timer per decision and lists only live timers', async (t) => {
    const { call, acceptChat, repository, wakeups } = setup(t, { now: 1_790_000_000_000 });
    acceptChat();

    const input = {
      decision_id: 'evt-timer-request',
      title: '看 PR',
      text: '提醒我看 PR #123',
      timezone: 'Asia/Shanghai',
      schedule: { type: 'once', run_at: '2026-10-20T09:00:00+08:00' },
      max_runs: 1,
      delivery: { mode: 'owner' },
    };
    const first = await call('create_timer', input);
    const repeat = await call('create_timer', input);

    assert.equal(first.data.timer_id, repeat.data.timer_id);
    assert.equal(first.data.duplicate, false);
    assert.equal(repeat.data.duplicate, true);
    assert.equal(repository.listActive().length, 1);
    assert.equal(wakeups(), 1);

    const listed = await call('list_timers');
    assert.match(listed.text, /看 PR/);
    assert.equal(listed.data.timers.length, 1);

    const cancelled = await call('cancel_timer', { timer_id: first.data.timer_id });
    assert.equal(cancelled.data.cancelled, true);
    assert.deepEqual(repository.listActive(), []);
    assert.equal((await call('list_timers')).data.timers.length, 0);
  });

  it('fires a due once timer as a reminder event and schedules the default completion check', async (t) => {
    const { repository } = setup(t);
    const submitted = [];
    const timer = repository.create({
      timerId: 'tm_once_pr_001',
      title: '看 PR',
      text: '提醒我看 PR #123',
      timezone: 'Asia/Shanghai',
      schedule: { type: 'once', run_at: '2026-10-20T09:00:00+08:00' },
      nextRunAtMs: 1_790_000_000_000,
      maxRuns: 1,
      delivery: { mode: 'owner' },
      createdFromEventId: 'evt-create',
      createdFromSource: 'lark',
      createdAtMs: 1_789_999_000_000,
    });
    assert.equal(timer.timerId, 'tm_once_pr_001');

    const connector = new TimerConnector({
      repository,
      submitEvent: (event) => submitted.push(event),
      now: () => 1_790_000_005_123,
      logger,
      autoStart: false,
    });
    await connector.fireDueOnce();

    assert.equal(submitted.length, 1);
    assert.equal(submitted[0].connector, 'timer');
    assert.equal(submitted[0].kind, 'reminder.due');
    assert.equal(submitted[0].id, 'timer:tm_once_pr_001:1790000000000');
    assert.equal(submitted[0].occurred_at, 1_790_000_005_123);
    assert.equal(submitted[0].conversation, null);
    assert.equal(submitted[0].data.timer.scheduled_time_ms, 1_790_000_000_000);
    assert.equal(submitted[0].data.timer.run_count, 1);
    assert.match(submitted[0].content.text, /complete_timer_occurrence/);
    assert.match(submitted[0].content.text, /occurrence_id=tm_once_pr_001:1790000000000/);
    assert.match(submitted[0].content.text, /提醒我看 PR #123/);
    const check = repository.getOccurrenceCheck('tm_once_pr_001:1790000000000');
    assert.equal(check.occurrenceId, 'tm_once_pr_001:1790000000000');
    assert.equal(check.eventId, 'timer:tm_once_pr_001:1790000000000');
    assert.equal(check.status, 'pending');
    assert.equal(check.attempt, 0);
    assert.equal(check.nextCheckAtMs, 1_790_000_305_123);
    assert.equal(repository.get('tm_once_pr_001'), null);
  });

  it('uses the configured completion check delay for first and follow-up checks', async (t) => {
    let nowMs = 1_790_000_000_000;
    const { repository } = setup(t, { now: () => nowMs });
    const submitted = [];
    repository.create({
      timerId: 'tm_once_pr_configured',
      title: '看 PR',
      text: '提醒我看 PR #987',
      timezone: 'Asia/Shanghai',
      schedule: { type: 'once', run_at: '2026-10-20T09:00:00+08:00' },
      nextRunAtMs: 1_790_000_000_000,
      maxRuns: 1,
      delivery: { mode: 'owner' },
      createdFromEventId: 'evt-create',
      createdFromSource: 'lark',
      createdAtMs: 1_789_999_000_000,
    });
    const connector = new TimerConnector({
      repository,
      submitEvent: (event) => submitted.push(event),
      now: () => nowMs,
      logger,
      autoStart: false,
      occurrenceCheckDelayMs: 120_000,
    });

    await connector.fireDueOnce();
    let check = repository.getOccurrenceCheck('tm_once_pr_configured:1790000000000');
    assert.equal(check.nextCheckAtMs, 1_790_000_120_000);

    nowMs = 1_790_000_120_000;
    await connector.fireDueOnce();

    assert.equal(submitted.length, 2);
    check = repository.getOccurrenceCheck('tm_once_pr_configured:1790000000000');
    assert.equal(check.attempt, 1);
    assert.equal(check.nextCheckAtMs, 1_790_000_240_000);
  });

  it('marks a timer occurrence completed and deletes its check record when the check is due', async (t) => {
    let nowMs = 1_790_000_000_000;
    const { store, call, repository } = setup(t, { now: () => nowMs });
    const submitted = [];
    repository.create({
      timerId: 'tm_once_pr_002',
      title: '看 PR',
      text: '提醒我看 PR #456',
      timezone: 'Asia/Shanghai',
      schedule: { type: 'once', run_at: '2026-10-20T09:00:00+08:00' },
      nextRunAtMs: 1_790_000_000_000,
      maxRuns: 1,
      delivery: { mode: 'owner' },
      createdFromEventId: 'evt-create',
      createdFromSource: 'lark',
      createdAtMs: 1_789_999_000_000,
    });
    const connector = new TimerConnector({
      repository,
      submitEvent: (event) => submitted.push(event),
      now: () => nowMs,
      logger,
      autoStart: false,
    });

    await connector.fireDueOnce();
    store.ingest(normalizeEvent(submitted[0]));
    await call('notify_owner', {
      markdown: '提醒我看 PR #456',
      decision_id: 'timer:tm_once_pr_002:1790000000000',
    });
    const completed = await call('complete_timer_occurrence', { occurrence_id: 'tm_once_pr_002:1790000000000' });
    assert.equal(completed.data.completed, true);
    assert.equal(repository.getOccurrenceCheck('tm_once_pr_002:1790000000000').status, 'completed');

    nowMs = 1_790_000_300_000;
    await connector.fireDueOnce();

    assert.equal(submitted.length, 1);
    assert.equal(repository.getOccurrenceCheck('tm_once_pr_002:1790000000000'), null);
  });

  it('resends an incomplete timer occurrence and schedules another completion check', async (t) => {
    let nowMs = 1_790_000_000_000;
    const { repository } = setup(t, { now: () => nowMs });
    const submitted = [];
    repository.create({
      timerId: 'tm_once_pr_003',
      title: '看 PR',
      text: '提醒我看 PR #789',
      timezone: 'Asia/Shanghai',
      schedule: { type: 'once', run_at: '2026-10-20T09:00:00+08:00' },
      nextRunAtMs: 1_790_000_000_000,
      maxRuns: 1,
      delivery: { mode: 'owner' },
      createdFromEventId: 'evt-create',
      createdFromSource: 'lark',
      createdAtMs: 1_789_999_000_000,
    });
    const connector = new TimerConnector({
      repository,
      submitEvent: (event) => submitted.push(event),
      now: () => nowMs,
      logger,
      autoStart: false,
    });

    await connector.fireDueOnce();
    nowMs = 1_790_000_300_000;
    await connector.fireDueOnce();

    assert.equal(submitted.length, 2);
    assert.equal(submitted[1].id, 'timer-check:tm_once_pr_003:1790000000000:1');
    assert.equal(submitted[1].kind, 'reminder.due');
    assert.equal(submitted[1].data.timer.occurrence_id, 'tm_once_pr_003:1790000000000');
    assert.equal(submitted[1].data.timer.check_attempt, 1);
    assert.match(submitted[1].content.text, /may not be complete/);
    assert.match(submitted[1].content.text, /complete_timer_occurrence/);

    const check = repository.getOccurrenceCheck('tm_once_pr_003:1790000000000');
    assert.equal(check.status, 'pending');
    assert.equal(check.attempt, 1);
    assert.equal(check.nextCheckAtMs, 1_790_000_600_000);
  });

  it('does not resend pending occurrence checks after the timer is cancelled', async (t) => {
    let nowMs = 1_790_000_000_000;
    const { call, repository } = setup(t, { now: () => nowMs });
    const submitted = [];
    repository.create({
      timerId: 'tm_repeating_cancelled',
      title: '报数',
      text: '报数 10 个数',
      timezone: 'Asia/Shanghai',
      schedule: { type: 'interval', interval_ms: 20_000, anchor_at_ms: nowMs, timezone: 'Asia/Shanghai' },
      nextRunAtMs: nowMs,
      maxRuns: 10,
      delivery: { mode: 'owner' },
      createdFromEventId: 'evt-create',
      createdFromSource: 'lark',
      createdAtMs: nowMs - 60_000,
    });
    const connector = new TimerConnector({
      repository,
      submitEvent: (event) => submitted.push(event),
      now: () => nowMs,
      logger,
      autoStart: false,
    });

    await connector.fireDueOnce();
    assert.equal(submitted.length, 1);
    assert.equal(repository.getOccurrenceCheck('tm_repeating_cancelled:1790000000000').status, 'pending');

    const cancelled = await call('cancel_timer', { timer_id: 'tm_repeating_cancelled' });
    assert.equal(cancelled.data.cancelled, true);
    assert.equal(repository.getOccurrenceCheck('tm_repeating_cancelled:1790000000000'), null);

    nowMs += 300_000;
    await connector.fireDueOnce();

    assert.equal(submitted.length, 1);
    assert.equal(repository.nextWakeAtMs(), null);
  });

  it('advances a daily timer to the next future planned wall-clock time', async (t) => {
    const { repository } = setup(t);
    repository.create({
      timerId: 'tm_daily_report_001',
      title: '写日报',
      text: '提醒我生成今天的日报',
      timezone: 'Asia/Shanghai',
      schedule: { type: 'daily', time: '09:30', timezone: 'Asia/Shanghai' },
      nextRunAtMs: Date.parse('2026-10-20T09:30:00+08:00'),
      maxRuns: null,
      delivery: { mode: 'chat', source: 'lark', chat_id: 'oc_report_group123' },
      createdFromEventId: 'evt-create',
      createdFromSource: 'lark',
      createdAtMs: Date.parse('2026-10-19T10:00:00+08:00'),
    });
    const connector = new TimerConnector({
      repository,
      submitEvent: () => {},
      now: () => Date.parse('2026-10-20T10:00:00+08:00'),
      logger,
      autoStart: false,
    });

    await connector.fireDueOnce();
    const row = repository.get('tm_daily_report_001');
    assert.equal(row.runCount, 1);
    assert.equal(row.lastRunAtMs, Date.parse('2026-10-20T09:30:00+08:00'));
    assert.equal(row.nextRunAtMs, Date.parse('2026-10-21T09:30:00+08:00'));
  });
});

describe('timer reminder delivery', () => {
  it('accepts a fired timer event, delivers it to the target chat, and suppresses the completion check after receipt', async (t) => {
    let nowMs = 1_790_000_000_000;
    const { store, call, connectors, repository } = setup(t, { now: () => nowMs });
    store.ingest(chatEvent({
      messageId: 'om_seen_integrated_chat123',
      eventId: 'evt-seen-integrated-chat',
      chatId: 'oc_report_group123',
      chatType: 'group',
    }));
    repository.create({
      timerId: 'tm_integrated_report_001',
      title: '写日报',
      text: '提醒我生成今天的日报',
      timezone: 'Asia/Shanghai',
      schedule: { type: 'once', run_at: '2026-10-20T09:00:00+08:00' },
      nextRunAtMs: nowMs,
      maxRuns: 1,
      delivery: { mode: 'chat', source: 'lark', chat_id: 'oc_report_group123' },
      createdFromEventId: 'evt-create',
      createdFromSource: 'lark',
      createdAtMs: nowMs - 60_000,
    });
    const connector = new TimerConnector({
      repository,
      submitEvent: (event) => store.ingest(normalizeEvent(event)),
      now: () => nowMs,
      logger,
      autoStart: false,
    });

    await connector.fireDueOnce();

    const occurrenceId = 'tm_integrated_report_001:1790000000000';
    const eventId = `timer:${occurrenceId}`;
    const timerRow = store.db.prepare(`
      SELECT event_id, kind, scope_key, content
      FROM connector_events
      WHERE connector_instance = 'timer'
    `).get();
    assert.equal(timerRow.event_id, eventId);
    assert.equal(timerRow.kind, 'reminder.due');
    assert.equal(timerRow.scope_key, JSON.stringify(['timer', 'timer', 'tm_integrated_report_001']));
    assert.match(timerRow.content, /Delivery request: send this timer reminder to Lark chat oc_report_group123/);
    assert.match(timerRow.content, /complete_timer_occurrence/);

    await call('send_message', {
      source: 'lark',
      chat_id: 'oc_report_group123',
      markdown: '写日报',
      decision_id: eventId,
    });
    assert.equal(connectors.calls.at(-1).action, 'message.send');
    assert.equal(connectors.calls.at(-1).input.chat_id, 'oc_report_group123');

    const completed = await call('complete_timer_occurrence', { occurrence_id: occurrenceId });
    assert.equal(completed.data.completed, true);

    nowMs += 300_000;
    await connector.fireDueOnce();

    const timerEvents = store.db.prepare(`
      SELECT event_id FROM connector_events
      WHERE connector_instance = 'timer'
      ORDER BY seq
    `).all().map((row) => row.event_id);
    assert.deepEqual(timerEvents, [eventId]);
    assert.equal(repository.getOccurrenceCheck(occurrenceId), null);
  });

  it('allows timer occurrences to deliver after newer timer events but completes only after a sent message', async (t) => {
    let nowMs = 1_790_000_000_000;
    const { store, call, connectors, repository } = setup(t, { now: () => nowMs });
    store.ingest(chatEvent({
      messageId: 'om_seen_timer_chat123',
      eventId: 'evt-seen-timer-chat',
      chatId: 'oc_report_group123',
      chatType: 'group',
    }));
    repository.create({
      timerId: 'tm_count_001',
      title: '报数',
      text: '报数 10 个数',
      timezone: 'Asia/Shanghai',
      schedule: { type: 'interval', interval_ms: 20_000, anchor_at_ms: nowMs, timezone: 'Asia/Shanghai' },
      nextRunAtMs: nowMs,
      maxRuns: 10,
      delivery: { mode: 'chat', source: 'lark', chat_id: 'oc_report_group123' },
      createdFromEventId: 'evt-create',
      createdFromSource: 'lark',
      createdAtMs: nowMs - 60_000,
    });
    const connector = new TimerConnector({
      repository,
      submitEvent: (event) => store.ingest(normalizeEvent(event)),
      now: () => nowMs,
      logger,
      autoStart: false,
    });

    await connector.fireDueOnce();
    nowMs += 20_000;
    await connector.fireDueOnce();

    const firstOccurrenceId = 'tm_count_001:1790000000000';
    const firstEventId = `timer:${firstOccurrenceId}`;
    const notCompleted = await call('complete_timer_occurrence', { occurrence_id: firstOccurrenceId });
    assert.equal(notCompleted.data.completed, false);
    assert.equal(repository.getOccurrenceCheck(firstOccurrenceId).status, 'pending');

    await call('send_message', {
      source: 'lark',
      chat_id: 'oc_report_group123',
      markdown: '1',
      decision_id: firstEventId,
    });
    assert.equal(connectors.calls.at(-1).action, 'message.send');
    assert.equal(connectors.calls.at(-1).input.chat_id, 'oc_report_group123');

    const completed = await call('complete_timer_occurrence', { occurrence_id: firstOccurrenceId });
    assert.equal(completed.data.completed, true);
    assert.equal(repository.getOccurrenceCheck(firstOccurrenceId).status, 'completed');

    await assert.rejects(() => call('send_message', {
      source: 'lark',
      chat_id: 'oc_other_group123',
      markdown: 'wrong target',
      decision_id: firstEventId,
    }), /configured for lark chat oc_report_group123/);
  });

  it('does not complete a timer occurrence after only an interim outbox send', async (t) => {
    let nowMs = 1_790_000_000_000;
    const { store, call, repository } = setup(t, { now: () => nowMs });
    store.ingest(chatEvent({
      messageId: 'om_seen_interim_timer_chat123',
      eventId: 'evt-seen-interim-timer-chat',
      chatId: 'oc_report_group123',
      chatType: 'group',
    }));
    repository.create({
      timerId: 'tm_interim_report_001',
      title: '写日报',
      text: '提醒我生成今天的日报',
      timezone: 'Asia/Shanghai',
      schedule: { type: 'once', run_at: '2026-10-20T09:00:00+08:00' },
      nextRunAtMs: nowMs,
      maxRuns: 1,
      delivery: { mode: 'chat', source: 'lark', chat_id: 'oc_report_group123' },
      createdFromEventId: 'evt-create',
      createdFromSource: 'lark',
      createdAtMs: nowMs - 60_000,
    });
    const connector = new TimerConnector({
      repository,
      submitEvent: (event) => store.ingest(normalizeEvent(event)),
      now: () => nowMs,
      logger,
      autoStart: false,
    });

    await connector.fireDueOnce();
    const occurrenceId = 'tm_interim_report_001:1790000000000';
    const eventId = `timer:${occurrenceId}`;
    const inbound = store.db.prepare('SELECT seq FROM connector_events WHERE event_id = ?').get(eventId);

    const interim = store.reserveOutbox({
      connector: 'lark',
      action: 'message.send',
      idempotencyKey: 'timer-interim-send',
      payload: { chat_id: 'oc_report_group123', content: { msg_type: 'text', content: { text: '我在处理' } } },
      summary: '我在处理',
      caller: 'codex',
      chatId: 'oc_report_group123',
      inboundSeq: inbound.seq,
      runtimeToken: TOKEN,
      metadata: { interim: true },
    }, nowMs);
    store.settleOutbox(interim.id, 'SENT', { result: { message_id: 'om_interim_sent123' } }, nowMs);

    const notCompleted = await call('complete_timer_occurrence', { occurrence_id: occurrenceId });
    assert.equal(notCompleted.data.completed, false);
    assert.equal(repository.getOccurrenceCheck(occurrenceId).status, 'pending');

    await call('send_message', {
      source: 'lark',
      chat_id: 'oc_report_group123',
      markdown: '日报已生成',
      decision_id: eventId,
    });
    const completed = await call('complete_timer_occurrence', { occurrence_id: occurrenceId });
    assert.equal(completed.data.completed, true);
  });

  it('allows a timer reminder to send to a chat and keeps other non-chat events owner-only', async (t) => {
    const { store, call, connectors } = setup(t);
    store.ingest(chatEvent({ messageId: 'om_seen_chat123', eventId: 'evt-seen-chat', chatId: 'oc_report_group123', chatType: 'group' }));
    const timerEvent = {
      protocol_version: '0.2',
      type: 'EVENT',
      connector: 'timer',
      instance: 'timer',
      id: 'timer:tm_daily_report_001:1790002200000',
      kind: 'reminder.due',
      occurred_at: 1_790_002_205_123,
      subject: { type: 'timer', id: 'tm_daily_report_001' },
      actor: null,
      content: { title: '写日报', text: '提醒我生成今天的日报', attachments: [] },
      conversation: null,
      message: null,
      reply_route: null,
      data: {
        timer: {
          id: 'tm_daily_report_001',
          schedule: { type: 'daily', time: '09:30' },
          max_runs: null,
          run_count: 1,
          scheduled_time_ms: 1_790_002_200_000,
          timezone: 'Asia/Shanghai',
          occurrence_id: 'tm_daily_report_001:1790002200000',
          delivery: { mode: 'chat', source: 'lark', chat_id: 'oc_report_group123' },
        },
      },
    };
    store.ingest(normalizeEvent(timerEvent));
    const timerRow = store.db.prepare('SELECT conv_key, scope_key FROM connector_events WHERE event_id = ?').get(timerEvent.id);
    assert.equal(timerRow.conv_key, null);
    assert.equal(timerRow.scope_key, JSON.stringify(['timer', 'timer', 'tm_daily_report_001']));
    store.ingest(chatEvent({
      messageId: 'om_after_timer123',
      eventId: 'evt-after-timer',
      chatId: 'oc_report_group123',
      chatType: 'group',
    }));

    await call('send_message', {
      source: 'lark',
      chat_id: 'oc_report_group123',
      markdown: '写日报',
      decision_id: 'timer:tm_daily_report_001:1790002200000',
    });
    assert.equal(connectors.calls.at(-1).input.chat_id, 'oc_report_group123');

    store.ingest(normalizeEvent({
      protocol_version: '0.2',
      type: 'EVENT',
      connector: 'calendar',
      instance: 'calendar',
      id: 'cal:evt-001',
      kind: 'calendar.reminder',
      occurred_at: 1_790_002_205_123,
      subject: { type: 'calendar', id: 'evt-001' },
      actor: null,
      content: { title: '会议', text: '开会提醒', attachments: [] },
      conversation: null,
      message: null,
      reply_route: null,
      data: {},
    }));
    await assert.rejects(() => call('send_message', {
      source: 'lark',
      chat_id: 'oc_report_group123',
      markdown: '会议',
      decision_id: 'cal:evt-001',
    }), /non-chat event can only notify the owner/);
  });
});
