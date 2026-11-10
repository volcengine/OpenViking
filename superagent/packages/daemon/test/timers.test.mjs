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
import { chatEvent, mailEvent } from './event-fixture.mjs';
import { fakeConnectors } from './action-fixture.mjs';

const TOKEN = 'runtime-token';
const OWNER = { id: 'ou_owner12345678', target: { user_id: 'ou_owner12345678' } };
const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

function setup(t, { now = 1_790_000_000_000, run = null, ids = ['lark'], channel = 'lark' } = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'timers-'));
  const store = new Store(path.join(directory, 'state.sqlite3'), logger);
  store.initializeCognition('codex', TOKEN);
  const connectors = fakeConnectors({ run, ids, owners: { [channel]: OWNER } });
  const repository = new TimerRepository(store.db);
  let wakeups = 0;
  const service = new ActionService({
    config: {
      workspace: path.join(directory, 'workspace'),
      tempDir: path.join(directory, 'tmp'),
      maxReplyChars: 1_000,
      channel,
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
  it('awaits completion persistence before recording completion activity', async (t) => {
    const context = setup(t);
    const { occurrenceId } = await dueOccurrence(context);
    const complete = context.repository.markOccurrenceCompleted.bind(context.repository);
    let release;
    const pending = new Promise((resolve) => { release = resolve; });
    context.repository.markOccurrenceCompleted = async (id) => { await pending; return complete(id); };
    const result = context.call('finish_timer_occurrence', { occurrence_id: occurrenceId, reason: 'No delivery needed' });
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(context.store.db.prepare("SELECT COUNT(*) AS count FROM activities WHERE kind = 'timer.completed'").get().count, 0);
    release();
    assert.equal((await result).data.completed, true);
    assert.equal(context.repository.getOccurrenceCheck(occurrenceId).status, 'completed');
  });
  it('does not record completion when asynchronous persistence fails', async (t) => {
    const context = setup(t);
    const { occurrenceId } = await dueOccurrence(context);
    context.repository.markOccurrenceCompleted = async () => { throw new Error('OV write failed'); };
    await assert.rejects(context.call('finish_timer_occurrence', {
      occurrence_id: occurrenceId, reason: 'No delivery needed',
    }), /OV write failed/);
    assert.equal(context.repository.getOccurrenceCheck(occurrenceId).status, 'pending');
    assert.equal(context.store.db.prepare("SELECT COUNT(*) AS count FROM activities WHERE kind = 'timer.completed'").get().count, 0);
  });
  async function dueOccurrence(context) {
    context.acceptChat();
    const created = await context.call('create_timer', {
      decision_id: 'evt-timer-request',
      title: '条件提醒',
      text: '工作日不发送，其他日期发送结果',
      timezone: 'Asia/Shanghai',
      schedule: { type: 'once', run_at: new Date(1_790_000_000_000).toISOString() },
      delivery: { mode: 'owner' },
    });
    const connector = new TimerConnector({
      repository: context.repository,
      submitEvent: (event) => context.store.ingest(normalizeEvent(event)),
      now: () => 1_790_000_000_000,
      logger,
      autoStart: false,
    });
    await connector.fireDueOnce();
    return { occurrenceId: `${created.data.timer_id}:1790000000000`, connector };
  }

  it('finishes with one delivery call only after sending, even when a new input arrives during delivery', async (t) => {
    let release;
    const sending = new Promise((resolve) => { release = resolve; });
    const context = setup(t, { run: async () => { await sending; return { message_id: 'om_final123456' }; } });
    const { occurrenceId } = await dueOccurrence(context);
    const finish = context.call('finish_timer_occurrence', {
      occurrence_id: occurrenceId,
      delivery: { tool: 'notify_owner', input: { markdown: '结果', decision_id: `timer:${occurrenceId}` } },
    });
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(context.repository.getOccurrenceCheck(occurrenceId).status, 'pending');
    await assert.rejects(() => context.call('finish_timer_occurrence', {
      occurrence_id: occurrenceId,
      delivery: { tool: 'notify_owner', input: { markdown: '结果', decision_id: `timer:${occurrenceId}` } },
    }), /in progress/);
    context.acceptChat({ messageId: 'om_new_input123', eventId: 'evt-new-input' });
    release();
    assert.equal((await finish).data.completed, true);
    assert.equal(context.repository.getOccurrenceCheck(occurrenceId).status, 'completed');
    const repeat = await context.call('finish_timer_occurrence', {
      occurrence_id: occurrenceId,
      delivery: { tool: 'notify_owner', input: { markdown: '结果', decision_id: `timer:${occurrenceId}` } },
    });
    assert.equal(repeat.data.completed, true);
    assert.equal(context.store.db.prepare('SELECT COUNT(*) AS count FROM outbox').get().count, 1);
  });

  it('finishes without sending and records why, without another completion reminder', async (t) => {
    const context = setup(t);
    const { occurrenceId, connector } = await dueOccurrence(context);
    const result = await context.call('finish_timer_occurrence', {
      occurrence_id: occurrenceId, reason: '今天是工作日，按任务要求不发送',
    });
    assert.equal(result.data.completed, true);
    assert.equal(context.store.db.prepare('SELECT COUNT(*) AS count FROM outbox').get().count, 0);
    const activity = context.store.db.prepare("SELECT * FROM activities WHERE kind = 'timer.completed'").get();
    assert.match(activity.summary, /今天是工作日/);
    connector.now = () => 1_790_000_300_000;
    await connector.fireDueOnce();
    assert.equal(context.repository.getOccurrenceCheck(occurrenceId), null);
    assert.equal(context.store.db.prepare("SELECT COUNT(*) AS count FROM connector_events WHERE connector_instance = 'timer'").get().count, 1);
    await assert.rejects(() => context.call('complete_timer_occurrence', { occurrence_id: occurrenceId }), /unknown tool/);
  });

  it('keeps ordinary questions pending and requires an explicit reason for silent completion', async (t) => {
    const context = setup(t);
    const { occurrenceId } = await dueOccurrence(context);
    await context.call('notify_owner', { markdown: '请问使用哪张图片？', decision_id: `timer:${occurrenceId}` });
    assert.equal(context.repository.getOccurrenceCheck(occurrenceId).status, 'pending');
    await assert.rejects(() => context.call('finish_timer_occurrence', { occurrence_id: occurrenceId }), /reason/);
    await assert.rejects(() => context.call('finish_timer_occurrence', {
      occurrence_id: occurrenceId,
      delivery: { tool: 'notify_owner', input: {
        choices: { prompt: '选哪张？', options: [{ key: 'a', label: 'A' }, { key: 'b', label: 'B' }] },
        decision_id: `timer:${occurrenceId}`,
      } },
    }), /question|choices/);
    assert.equal(context.repository.getOccurrenceCheck(occurrenceId).status, 'pending');
  });

  it('leaves failed deliveries pending on repeated attempts', async (t) => {
    const context = setup(t, { run: async () => { throw new Error('send timed out'); } });
    const { occurrenceId } = await dueOccurrence(context);
    const input = {
      occurrence_id: occurrenceId,
      delivery: { tool: 'notify_owner', input: { markdown: '结果', decision_id: `timer:${occurrenceId}` } },
    };
    await assert.rejects(() => context.call('finish_timer_occurrence', input), /send timed out/);
    assert.equal(context.repository.getOccurrenceCheck(occurrenceId).status, 'pending');
    await assert.rejects(() => context.call('finish_timer_occurrence', input), /send timed out/);
    assert.equal(context.repository.getOccurrenceCheck(occurrenceId).status, 'pending');
    assert.equal(context.store.db.prepare('SELECT status FROM outbox').get().status, 'UNCERTAIN');
  });

  it('recovers a sent result without resending after completion persistence failed', async (t) => {
    const context = setup(t);
    const { occurrenceId } = await dueOccurrence(context);
    const input = {
      occurrence_id: occurrenceId,
      delivery: { tool: 'notify_owner', input: { markdown: '结果', decision_id: `timer:${occurrenceId}` } },
    };
    context.store.db.exec(`CREATE TRIGGER fail_timer_completion BEFORE UPDATE ON timer_occurrence_checks
      BEGIN SELECT RAISE(ABORT, 'completion write failed'); END`);
    await assert.rejects(() => context.call('finish_timer_occurrence', input), /completion write failed/);
    assert.equal(context.repository.getOccurrenceCheck(occurrenceId).status, 'pending');
    context.store.db.exec('DROP TRIGGER fail_timer_completion');
    const result = await context.call('finish_timer_occurrence', input);
    assert.equal(result.data.completed, true);
    assert.equal(result.data.duplicate, true);
    assert.equal(context.store.db.prepare('SELECT COUNT(*) AS count FROM outbox').get().count, 1);
    assert.equal(context.connectors.calls.length, 1);
  });

  it('validates the occurrence and nested tool before any delivery', async (t) => {
    const context = setup(t);
    const { occurrenceId } = await dueOccurrence(context);
    for (const delivery of [
      { tool: 'notify_owner', input: { markdown: '结果', decision_id: 'evt-timer-request' } },
      { tool: 'notify_owner', input: { markdown: '结果', decision_id: `timer:${occurrenceId}`, unexpected: true } },
      { tool: 'send_message', input: { markdown: '结果', decision_id: `timer:${occurrenceId}`, chat_id: 'oc_wrong123456', source: 'lark' } },
    ]) {
      await assert.rejects(() => context.call('finish_timer_occurrence', { occurrence_id: occurrenceId, delivery }));
    }
    await assert.rejects(() => context.call('finish_timer_occurrence', {
      occurrence_id: 'tm_missing:1790000000000', reason: '不发送',
    }));
    assert.equal(context.store.db.prepare('SELECT COUNT(*) AS count FROM outbox').get().count, 0);
    assert.equal(context.repository.getOccurrenceCheck(occurrenceId).status, 'pending');
  });

  it('does not complete a simulated delivery in dry-run mode', async (t) => {
    const context = setup(t);
    const { occurrenceId } = await dueOccurrence(context);
    context.service.config.dryRun = true;
    const result = await context.call('finish_timer_occurrence', {
      occurrence_id: occurrenceId,
      delivery: { tool: 'notify_owner', input: { markdown: '结果', decision_id: `timer:${occurrenceId}` } },
    });
    assert.equal(result.data.completed, false);
    assert.equal(context.repository.getOccurrenceCheck(occurrenceId).status, 'pending');
  });

  it('reuses reply_message for a final file result in the configured chat', async (t) => {
    const context = setup(t);
    context.acceptChat();
    const created = await context.call('create_timer', {
      decision_id: 'evt-timer-request',
      title: '文件结果',
      text: '完成后发文件',
      schedule: { type: 'once', run_at: new Date(1_790_000_000_000).toISOString() },
      delivery: { mode: 'chat', source: 'lark', chat_id: 'oc_timer_setup123' },
    });
    const connector = new TimerConnector({
      repository: context.repository,
      submitEvent: (event) => context.store.ingest(normalizeEvent(event)),
      now: () => 1_790_000_000_000,
      logger,
      autoStart: false,
    });
    await connector.fireDueOnce();
    const occurrenceId = `${created.data.timer_id}:1790000000000`;
    const result = await context.call('finish_timer_occurrence', {
      occurrence_id: occurrenceId,
      delivery: { tool: 'reply_message', input: {
        source: 'lark', message_id: 'om_timer_request123',
        file_key: 'file_result123456', decision_id: `timer:${occurrenceId}`,
      } },
    });
    assert.equal(result.data.completed, true);
    const outbox = context.store.db.prepare('SELECT action, status, payload_json FROM outbox').get();
    assert.equal(outbox.action, 'message.reply');
    assert.equal(outbox.status, 'SENT');
    assert.equal(context.repository.getOccurrenceCheck(occurrenceId).status, 'completed');
  });

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
    assert.equal(wakeups(), 2);

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
    assert.match(submitted[0].content.text, /finish_timer_occurrence/);
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
    const completed = await call('finish_timer_occurrence', {
      occurrence_id: 'tm_once_pr_002:1790000000000',
      delivery: { tool: 'notify_owner', input: {
        markdown: '提醒我看 PR #456',
        decision_id: 'timer:tm_once_pr_002:1790000000000',
      } },
    });
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
    assert.match(submitted[1].content.text, /finish_timer_occurrence/);

    const check = repository.getOccurrenceCheck('tm_once_pr_003:1790000000000');
    assert.equal(check.status, 'pending');
    assert.equal(check.attempt, 1);
    assert.equal(check.nextCheckAtMs, 1_790_000_600_000);
  });

  for (const submitFails of [false, true]) {
    it(`stops after five completion checks${submitFails ? ' even when submission fails' : ''}`, async (t) => {
      let nowMs = 1_790_000_000_000;
      const { repository, call } = setup(t);
      let submitted = 0;
      const occurrenceId = 'tm_limit:1790000000000';
      repository.createOccurrenceCheck({
        occurrenceId, eventId: `timer:${occurrenceId}`, nextCheckAtMs: nowMs,
        event: {
          id: `timer:${occurrenceId}`, content: { text: 'report' },
          data: { timer: { id: 'tm_limit', occurrence_id: occurrenceId } },
        },
      });
      const connector = new TimerConnector({
        repository, now: () => nowMs, logger, autoStart: false,
        submitEvent: () => {
          submitted += 1;
          if (submitFails) throw new Error('submission failed');
        },
      });
      for (let attempt = 0; attempt < 5; attempt += 1) {
        assert.equal(await connector.fireDueOnce(), true);
        nowMs += 300_000;
      }
      assert.equal(submitted, 5);
      assert.equal(repository.getOccurrenceCheck(occurrenceId).attempt, 5);
      assert.equal(repository.getOccurrenceCheck(occurrenceId).status, 'exhausted');
      assert.equal(repository.nextWakeAtMs(), 1_790_606_300_001);
      assert.equal(await connector.fireDueOnce(), false);
      assert.equal((await call('cancel_timer', { timer_id: 'tm_limit' })).data.cancelled, true);
      assert.equal(repository.getOccurrenceCheck(occurrenceId), null);
    });
  }

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
  function pendingTimer(repository, store, delivery = { mode: 'owner' }) {
    const occurrenceId = 'tm_retry:1790000000000';
    const event = {
      protocol_version: '0.2', type: 'EVENT', connector: 'timer', instance: 'timer',
      id: `timer:${occurrenceId}`, kind: 'reminder.due', occurred_at: 1_790_000_000_000,
      subject: { type: 'timer', id: 'tm_retry' }, actor: null,
      content: { text: 'report', attachments: [] }, conversation: null, message: null, reply_route: null,
      data: { timer: { id: 'tm_retry', occurrence_id: occurrenceId, delivery } },
    };
    store.ingest(normalizeEvent(event));
    repository.createOccurrenceCheck({ occurrenceId, eventId: event.id, nextCheckAtMs: 1_790_000_300_000, event });
    return event;
  }

  for (const uncertain of [false, true]) {
    it(`preserves ${uncertain ? 'UNCERTAIN protection' : 'SENT deduplication'} across occurrence checks`, async (t) => {
      const { store, repository, call, connectors } = setup(t, {
        run: uncertain ? () => { throw new Error('response lost'); } : null,
        ids: ['weixin'], channel: 'weixin',
      });
      const event = pendingTimer(repository, store);
      const send = (decisionId) => call('notify_owner', { decision_id: decisionId, markdown: 'report' });
      if (uncertain) await assert.rejects(send(event.id), /response lost/);
      else await send(event.id);
      const check = { ...event, id: 'timer-check:tm_retry:1790000000000:1' };
      store.ingest(normalizeEvent(check));
      if (uncertain) await assert.rejects(send(check.id), /outcome is uncertain/);
      else assert.equal((await send(check.id)).data.duplicate, true);
      assert.equal(connectors.calls.length, 1);
    });
  }

  it('blocks mail approval drafts for a chat timer', async (t) => {
    const { store, repository, call, connectors } = setup(t, { ids: ['lark', 'email'] });
    store.ingest(mailEvent());
    const event = pendingTimer(repository, store, { mode: 'chat', source: 'lark', chat_id: 'oc_report_group123' });
    await assert.rejects(call('reply_message', {
      source: 'email', message_id: 'mail:1:7', markdown: 'report', decision_id: event.id,
    }), /configured for lark chat/);
    assert.equal(connectors.calls.length, 0);
  });

  for (const tool of ['send_message', 'reply_message']) {
    it(`deduplicates ${tool} across checks but allows the next occurrence`, async (t) => {
      const { store, repository, call, connectors } = setup(t);
      store.ingest(chatEvent({ chatId: 'oc_report_group123', messageId: 'om_report123' }));
      const event = pendingTimer(repository, store, { mode: 'chat', source: 'lark', chat_id: 'oc_report_group123' });
      const send = (decisionId) => call(tool, {
        source: 'lark', markdown: 'report', decision_id: decisionId, idempotency_key: 'report',
        ...(tool === 'send_message' ? { chat_id: 'oc_report_group123' } : { message_id: 'om_report123' }),
      });
      await send(event.id);
      const check = { ...event, id: 'timer-check:tm_retry:1790000000000:1' };
      store.ingest(normalizeEvent(check));
      assert.equal((await send(check.id)).data.duplicate, true);
      assert.equal(connectors.calls.length, 1);
      const next = {
        ...event, id: 'timer:tm_retry:1790086400000',
        data: { timer: { ...event.data.timer, occurrence_id: 'tm_retry:1790086400000' } },
      };
      store.ingest(normalizeEvent(next));
      await send(next.id);
      assert.equal(connectors.calls.length, 2);
    });
  }

  it('requires the configured target and excludes approval drafts before completion', async (t) => {
    const { store, repository, call, connectors } = setup(t, { ids: ['lark', 'email'] });
    store.ingest(mailEvent());
    const event = pendingTimer(repository, store, { mode: 'chat', source: 'lark', chat_id: 'oc_report_group123' });
    await assert.rejects(() => call('finish_timer_occurrence', {
      occurrence_id: event.data.timer.occurrence_id,
      delivery: { tool: 'send_message', input: {
        source: 'lark',
        chat_id: 'oc_other_group123',
        markdown: 'wrong target',
        decision_id: event.id,
      } },
    }), /configured for lark chat oc_report_group123/);
    await assert.rejects(() => call('finish_timer_occurrence', {
      occurrence_id: event.data.timer.occurrence_id,
      delivery: { tool: 'reply_message', input: {
        source: 'email',
        message_id: 'mail:1:7',
        markdown: 'draft',
        decision_id: event.id,
      } },
    }), /configured for lark chat/);
    assert.equal(repository.getOccurrenceCheck(event.data.timer.occurrence_id).status, 'pending');
    const completed = await call('finish_timer_occurrence', {
      occurrence_id: event.data.timer.occurrence_id,
      delivery: { tool: 'send_message', input: {
        source: 'lark',
        chat_id: 'oc_report_group123',
        markdown: 'final',
        decision_id: event.id,
      } },
    });
    assert.equal(completed.data.completed, true);
    assert.equal(connectors.calls.at(-1).input.chat_id, 'oc_report_group123');
  });

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
    assert.match(timerRow.content, /Delivery target: if this task requires a message, send its final result to Lark chat oc_report_group123/);
    assert.match(timerRow.content, /finish_timer_occurrence/);

    const completed = await call('finish_timer_occurrence', {
      occurrence_id: occurrenceId,
      delivery: { tool: 'send_message', input: {
        source: 'lark',
        chat_id: 'oc_report_group123',
        markdown: '写日报',
        decision_id: eventId,
      } },
    });
    assert.equal(connectors.calls.at(-1).action, 'message.send');
    assert.equal(connectors.calls.at(-1).input.chat_id, 'oc_report_group123');

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
    await assert.rejects(() => call('finish_timer_occurrence', { occurrence_id: firstOccurrenceId }), /reason/);
    assert.equal(repository.getOccurrenceCheck(firstOccurrenceId).status, 'pending');

    const completed = await call('finish_timer_occurrence', {
      occurrence_id: firstOccurrenceId,
      delivery: { tool: 'send_message', input: {
        source: 'lark',
        chat_id: 'oc_report_group123',
        markdown: '1',
        decision_id: firstEventId,
      } },
    });
    assert.equal(connectors.calls.at(-1).action, 'message.send');
    assert.equal(connectors.calls.at(-1).input.chat_id, 'oc_report_group123');

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

    await assert.rejects(() => call('finish_timer_occurrence', { occurrence_id: occurrenceId }), /reason/);
    assert.equal(repository.getOccurrenceCheck(occurrenceId).status, 'pending');

    const completed = await call('finish_timer_occurrence', {
      occurrence_id: occurrenceId,
      delivery: { tool: 'send_message', input: {
        source: 'lark',
        chat_id: 'oc_report_group123',
        markdown: '日报已生成',
        decision_id: eventId,
      } },
    });
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
