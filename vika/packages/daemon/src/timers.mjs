// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { randomUUID } from 'node:crypto';

const TIMER_ID_PREFIX = 'tm_';
export const DEFAULT_OCCURRENCE_CHECK_DELAY_MS = 5 * 60 * 1000;

function number(value) {
  return typeof value === 'bigint' ? Number(value) : value;
}

function object(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${label} must be an object`);
  return value;
}

function timerRow(row) {
  if (!row) return null;
  return {
    timerId: row.timer_id,
    title: row.title,
    text: row.text,
    timezone: row.timezone,
    nextRunAtMs: number(row.next_run_at_ms),
    schedule: JSON.parse(row.schedule_json),
    maxRuns: row.max_runs === null ? null : number(row.max_runs),
    delivery: JSON.parse(row.delivery_json),
    createdFromEventId: row.created_from_event_id,
    createdFromSource: row.created_from_source,
    createdAtMs: number(row.created_at_ms),
    lastRunAtMs: row.last_run_at_ms === null ? null : number(row.last_run_at_ms),
    runCount: number(row.run_count),
  };
}

function occurrenceCheckRow(row) {
  if (!row) return null;
  return {
    occurrenceId: row.occurrence_id,
    eventId: row.event_id,
    status: row.status,
    attempt: number(row.attempt),
    nextCheckAtMs: number(row.next_check_at_ms),
    event: JSON.parse(row.event_json),
  };
}

function validateTimezone(value) {
  const timezone = typeof value === 'string' && value.trim() ? value.trim() : Intl.DateTimeFormat().resolvedOptions().timeZone;
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: timezone }).format(new Date());
  } catch {
    throw new Error('timezone is invalid');
  }
  return timezone;
}

function parseTime(value) {
  const match = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(String(value ?? ''));
  if (!match) throw new Error('schedule.time must be HH:mm');
  return { hour: Number(match[1]), minute: Number(match[2]) };
}

function zonedParts(ms, timezone) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date(ms));
  const values = Object.fromEntries(parts.filter((part) => part.type !== 'literal').map((part) => [part.type, Number(part.value)]));
  return {
    year: values.year,
    month: values.month,
    day: values.day,
    hour: values.hour,
    minute: values.minute,
    second: values.second,
  };
}

function localDatePlusDays({ year, month, day }, days) {
  const date = new Date(Date.UTC(year, month - 1, day + days));
  return { year: date.getUTCFullYear(), month: date.getUTCMonth() + 1, day: date.getUTCDate() };
}

function localDayOfWeek({ year, month, day }) {
  return new Date(Date.UTC(year, month - 1, day)).getUTCDay();
}

function zonedTimeToUtcMs({ year, month, day, hour = 0, minute = 0, second = 0 }, timezone) {
  const guess = Date.UTC(year, month - 1, day, hour, minute, second);
  const actual = zonedParts(guess, timezone);
  const actualAsUtc = Date.UTC(actual.year, actual.month - 1, actual.day, actual.hour, actual.minute, actual.second);
  return guess - (actualAsUtc - guess);
}

function nextDaily(schedule, afterMs) {
  const timezone = validateTimezone(schedule.timezone);
  const time = parseTime(schedule.time);
  let date = zonedParts(afterMs, timezone);
  let candidate = zonedTimeToUtcMs({ ...date, ...time }, timezone);
  if (candidate <= afterMs) {
    date = localDatePlusDays(date, 1);
    candidate = zonedTimeToUtcMs({ ...date, ...time }, timezone);
  }
  return candidate;
}

function weeklyDays(schedule) {
  const raw = schedule.days ?? schedule.days_of_week ?? schedule.day_of_week;
  const list = Array.isArray(raw) ? raw : [raw];
  const days = [...new Set(list.map((item) => Number(item)))].filter((item) => Number.isInteger(item) && item >= 0 && item <= 6);
  if (days.length === 0) throw new Error('weekly schedule needs day_of_week or days');
  return days;
}

function nextWeekly(schedule, afterMs) {
  const timezone = validateTimezone(schedule.timezone);
  const time = parseTime(schedule.time);
  const today = zonedParts(afterMs, timezone);
  const currentDay = localDayOfWeek(today);
  let best = null;
  for (const day of weeklyDays(schedule)) {
    let delta = (day - currentDay + 7) % 7;
    let date = localDatePlusDays(today, delta);
    let candidate = zonedTimeToUtcMs({ ...date, ...time }, timezone);
    if (candidate <= afterMs) {
      delta += 7;
      date = localDatePlusDays(today, delta);
      candidate = zonedTimeToUtcMs({ ...date, ...time }, timezone);
    }
    if (best === null || candidate < best) best = candidate;
  }
  return best;
}

function nextInterval(schedule, afterMs) {
  const interval = Number(schedule.interval_ms);
  const anchor = Number(schedule.anchor_at_ms);
  if (!Number.isSafeInteger(interval) || interval <= 0) throw new Error('interval_ms must be a positive integer');
  if (!Number.isSafeInteger(anchor) || anchor < 0) throw new Error('anchor_at_ms must be a timestamp');
  if (anchor > afterMs) return anchor;
  const steps = Math.floor((afterMs - anchor) / interval) + 1;
  return anchor + steps * interval;
}

function timestamp(value, label) {
  if (Number.isSafeInteger(value) && value >= 0) return value;
  const parsed = Date.parse(String(value ?? ''));
  if (!Number.isFinite(parsed)) throw new Error(`${label} must be an ISO time`);
  return parsed;
}

export class TimerSchedule {
  static normalize(input, { nowMs = Date.now(), timezone = null } = {}) {
    const schedule = { ...object(input, 'schedule') };
    const type = schedule.type;
    if (type === 'once') {
      const runAtMs = timestamp(schedule.run_at ?? schedule.run_at_ms, 'schedule.run_at');
      return { schedule: { type: 'once', run_at_ms: runAtMs, run_at: new Date(runAtMs).toISOString() }, nextRunAtMs: runAtMs };
    }
    const tz = validateTimezone(schedule.timezone ?? timezone);
    if (type === 'daily') {
      const normalized = { type: 'daily', time: String(schedule.time), timezone: tz };
      return { schedule: normalized, nextRunAtMs: nextDaily(normalized, nowMs) };
    }
    if (type === 'weekly') {
      const normalized = { type: 'weekly', time: String(schedule.time), days: weeklyDays(schedule), timezone: tz };
      return { schedule: normalized, nextRunAtMs: nextWeekly(normalized, nowMs) };
    }
    if (type === 'interval') {
      const interval = Number(schedule.interval_ms ?? schedule.every_ms);
      if (!Number.isSafeInteger(interval) || interval <= 0) throw new Error('schedule.interval_ms must be a positive integer');
      const anchorAtMs = schedule.anchor_at_ms === undefined
        ? (schedule.anchor_at ? timestamp(schedule.anchor_at, 'schedule.anchor_at') : nowMs)
        : timestamp(schedule.anchor_at_ms, 'schedule.anchor_at_ms');
      const normalized = { type: 'interval', interval_ms: interval, anchor_at_ms: anchorAtMs, timezone: tz };
      return { schedule: normalized, nextRunAtMs: nextInterval(normalized, nowMs) };
    }
    throw new Error('schedule.type must be once, interval, daily, or weekly');
  }

  static nextAfter(schedule, afterMs) {
    const value = object(schedule, 'schedule');
    if (value.type === 'once') return null;
    if (value.type === 'daily') return nextDaily(value, afterMs);
    if (value.type === 'weekly') return nextWeekly(value, afterMs);
    if (value.type === 'interval') return nextInterval(value, afterMs);
    return null;
  }
}

export class TimerRepository {
  constructor(db) {
    this.db = db;
    this.migrate();
    this.prepare();
  }

  migrate() {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS timers (
        timer_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        text TEXT NOT NULL,
        timezone TEXT NOT NULL,
        next_run_at_ms INTEGER NOT NULL,
        schedule_json TEXT NOT NULL,
        max_runs INTEGER,
        delivery_json TEXT NOT NULL,
        created_from_event_id TEXT NOT NULL,
        created_from_source TEXT NOT NULL,
        created_at_ms INTEGER NOT NULL,
        last_run_at_ms INTEGER,
        run_count INTEGER NOT NULL DEFAULT 0,
        UNIQUE(created_from_source, created_from_event_id)
      );
      CREATE INDEX IF NOT EXISTS timers_next
        ON timers(next_run_at_ms);

      CREATE TABLE IF NOT EXISTS timer_occurrence_checks (
        occurrence_id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL,
        status TEXT NOT NULL,
        attempt INTEGER NOT NULL DEFAULT 0,
        next_check_at_ms INTEGER NOT NULL,
        event_json TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS timer_occurrence_checks_next
        ON timer_occurrence_checks(next_check_at_ms);
    `);
  }

  prepare() {
    this.insertTimer = this.db.prepare(`
      INSERT INTO timers(
        timer_id, title, text, timezone, next_run_at_ms, schedule_json, max_runs,
        delivery_json, created_from_event_id, created_from_source, created_at_ms,
        last_run_at_ms, run_count
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0)
    `);
    this.getTimer = this.db.prepare('SELECT * FROM timers WHERE timer_id = ?');
    this.getByDecision = this.db.prepare('SELECT * FROM timers WHERE created_from_source = ? AND created_from_event_id = ?');
    this.listActiveStatement = this.db.prepare(`
      SELECT * FROM timers ORDER BY next_run_at_ms, created_at_ms
    `);
    this.nextActiveStatement = this.db.prepare(`
      SELECT * FROM timers ORDER BY next_run_at_ms, created_at_ms LIMIT 1
    `);
    this.nextDueStatement = this.db.prepare(`
      SELECT * FROM timers WHERE next_run_at_ms <= ?
      ORDER BY next_run_at_ms, created_at_ms LIMIT 1
    `);
    this.deleteTimer = this.db.prepare('DELETE FROM timers WHERE timer_id = ?');
    this.deletePendingOccurrenceChecksForTimer = this.db.prepare(`
      DELETE FROM timer_occurrence_checks
      WHERE status = 'pending'
        AND json_extract(event_json, '$.data.timer.id') = ?
    `);
    this.advanceTimer = this.db.prepare(`
      UPDATE timers
      SET next_run_at_ms = ?, last_run_at_ms = ?, run_count = ?
      WHERE timer_id = ?
    `);
    this.insertOccurrenceCheck = this.db.prepare(`
      INSERT OR IGNORE INTO timer_occurrence_checks(
        occurrence_id, event_id, status, attempt, next_check_at_ms, event_json
      ) VALUES (?, ?, 'pending', 0, ?, ?)
    `);
    this.getOccurrenceCheckStatement = this.db.prepare(`
      SELECT * FROM timer_occurrence_checks WHERE occurrence_id = ?
    `);
    this.nextOccurrenceCheckStatement = this.db.prepare(`
      SELECT * FROM timer_occurrence_checks ORDER BY next_check_at_ms LIMIT 1
    `);
    this.nextDueOccurrenceCheckStatement = this.db.prepare(`
      SELECT * FROM timer_occurrence_checks WHERE next_check_at_ms <= ?
      ORDER BY next_check_at_ms LIMIT 1
    `);
    this.markOccurrenceCompletedStatement = this.db.prepare(`
      UPDATE timer_occurrence_checks SET status = 'completed' WHERE occurrence_id = ?
    `);
    this.deleteOccurrenceCheckStatement = this.db.prepare(`
      DELETE FROM timer_occurrence_checks WHERE occurrence_id = ?
    `);
    this.rescheduleOccurrenceCheckStatement = this.db.prepare(`
      UPDATE timer_occurrence_checks
      SET status = 'pending', attempt = ?, next_check_at_ms = ?
      WHERE occurrence_id = ?
    `);
  }

  get(timerId) {
    return timerRow(this.getTimer.get(timerId));
  }

  findByDecision(source, eventId) {
    return timerRow(this.getByDecision.get(source, eventId));
  }

  listActive() {
    return this.listActiveStatement.all().map(timerRow);
  }

  nextActive() {
    return timerRow(this.nextActiveStatement.get());
  }

  nextDue(nowMs = Date.now()) {
    return timerRow(this.nextDueStatement.get(nowMs));
  }

  getOccurrenceCheck(occurrenceId) {
    return occurrenceCheckRow(this.getOccurrenceCheckStatement.get(occurrenceId));
  }

  nextOccurrenceCheck() {
    return occurrenceCheckRow(this.nextOccurrenceCheckStatement.get());
  }

  nextDueOccurrenceCheck(nowMs = Date.now()) {
    return occurrenceCheckRow(this.nextDueOccurrenceCheckStatement.get(nowMs));
  }

  nextWakeAtMs() {
    const timer = this.nextActive();
    const check = this.nextOccurrenceCheck();
    const values = [timer?.nextRunAtMs, check?.nextCheckAtMs].filter((value) => Number.isSafeInteger(value));
    return values.length === 0 ? null : Math.min(...values);
  }

  create({
    timerId = `${TIMER_ID_PREFIX}${randomUUID().replaceAll('-', '').slice(0, 24)}`,
    title,
    text,
    timezone,
    schedule,
    nextRunAtMs,
    maxRuns,
    delivery,
    createdFromEventId,
    createdFromSource,
    createdAtMs = Date.now(),
  }) {
    this.insertTimer.run(
      timerId,
      title,
      text,
      timezone,
      nextRunAtMs,
      JSON.stringify(schedule),
      maxRuns,
      JSON.stringify(delivery),
      createdFromEventId,
      createdFromSource,
      createdAtMs,
    );
    return this.get(timerId);
  }

  cancel(timerId) {
    return number(this.deleteTimer.run(timerId).changes) > 0;
  }

  cancelWithOccurrenceChecks(timerId) {
    const deletedTimers = number(this.deleteTimer.run(timerId).changes);
    const deletedChecks = number(this.deletePendingOccurrenceChecksForTimer.run(timerId).changes);
    return deletedTimers > 0 || deletedChecks > 0;
  }

  finishOccurrence(timer, { scheduledTimeMs, completedAtMs, runCount }) {
    const remove = timer.maxRuns !== null && runCount >= timer.maxRuns;
    const nextRunAtMs = remove ? null : TimerSchedule.nextAfter(timer.schedule, completedAtMs);
    if (nextRunAtMs === null) {
      this.cancel(timer.timerId);
      return null;
    }
    this.advanceTimer.run(nextRunAtMs, scheduledTimeMs, runCount, timer.timerId);
    return this.get(timer.timerId);
  }

  createOccurrenceCheck({ occurrenceId, eventId, nextCheckAtMs, event }) {
    this.insertOccurrenceCheck.run(occurrenceId, eventId, nextCheckAtMs, JSON.stringify(event));
    return this.getOccurrenceCheck(occurrenceId);
  }

  markOccurrenceCompleted(occurrenceId) {
    const check = this.getOccurrenceCheck(occurrenceId);
    if (!check) return null;
    if (check.status !== 'completed') this.markOccurrenceCompletedStatement.run(occurrenceId);
    return this.getOccurrenceCheck(occurrenceId);
  }

  deleteOccurrenceCheck(occurrenceId) {
    return number(this.deleteOccurrenceCheckStatement.run(occurrenceId).changes) > 0;
  }

  rescheduleOccurrenceCheck(occurrenceId, { attempt, nextCheckAtMs }) {
    this.rescheduleOccurrenceCheckStatement.run(attempt, nextCheckAtMs, occurrenceId);
    return this.getOccurrenceCheck(occurrenceId);
  }
}

function deliveryText(delivery) {
  if (delivery?.mode === 'chat') {
    const target = delivery.source === 'lark' ? `Lark chat ${delivery.chat_id}` : `${delivery.source ?? 'chat'} ${delivery.chat_id}`;
    return `Delivery request: send this timer reminder to ${target}.`;
  }
  return 'Delivery request: send this timer reminder to the owner.';
}

function completionReceiptText(occurrenceId) {
  return `Completion receipt: after completing this timer occurrence, call complete_timer_occurrence with occurrence_id=${occurrenceId}.`;
}

function eventForTimer(timer, nowMs) {
  const scheduledTimeMs = timer.nextRunAtMs;
  const runCount = timer.runCount + 1;
  const occurrenceId = `${timer.timerId}:${scheduledTimeMs}`;
  return {
    protocol_version: '0.2',
    type: 'EVENT',
    connector: 'timer',
    instance: 'timer',
    id: `timer:${timer.timerId}:${scheduledTimeMs}`,
    kind: 'reminder.due',
    occurred_at: nowMs,
    subject: { type: 'timer', id: timer.timerId },
    actor: null,
    content: {
      title: timer.title,
      text: [timer.text, deliveryText(timer.delivery), completionReceiptText(occurrenceId)].filter(Boolean).join('\n\n'),
      attachments: [],
    },
    conversation: null,
    message: null,
    reply_route: null,
    data: {
      timer: {
        id: timer.timerId,
        schedule: timer.schedule,
        max_runs: timer.maxRuns,
        run_count: runCount,
        scheduled_time_ms: scheduledTimeMs,
        timezone: timer.timezone,
        occurrence_id: occurrenceId,
        delivery: timer.delivery,
      },
    },
  };
}

function eventForOccurrenceCheck(check, nowMs) {
  const attempt = check.attempt + 1;
  const original = check.event;
  const timer = original.data?.timer ?? {};
  return {
    ...original,
    id: `timer-check:${check.occurrenceId}:${attempt}`,
    occurred_at: nowMs,
    content: {
      ...original.content,
      text: [
        'The previous timer task may not be complete. Confirm and complete it, then call complete_timer_occurrence.',
        original.content?.text,
      ].filter(Boolean).join('\n\n'),
    },
    data: {
      ...original.data,
      timer: {
        ...timer,
        occurrence_id: check.occurrenceId,
        check_attempt: attempt,
      },
    },
  };
}

export class TimerConnector {
  constructor({
    repository,
    submitEvent,
    now = () => Date.now(),
    logger,
    idlePollMs = 60_000,
    occurrenceCheckDelayMs = DEFAULT_OCCURRENCE_CHECK_DELAY_MS,
    autoStart = true,
  }) {
    this.repository = repository;
    this.submitEvent = submitEvent;
    this.now = now;
    this.logger = logger;
    this.idlePollMs = idlePollMs;
    this.occurrenceCheckDelayMs = occurrenceCheckDelayMs;
    this.timer = null;
    this.stopped = false;
    this.running = false;
    if (autoStart) this.start();
  }

  start() {
    this.stopped = false;
    this.wake();
  }

  wake() {
    if (this.stopped) return;
    clearTimeout(this.timer);
    const nextWakeAtMs = this.repository.nextWakeAtMs();
    if (nextWakeAtMs === null) {
      this.timer = null;
      return;
    }
    const nowMs = this.now();
    const delay = Math.max(0, nextWakeAtMs - nowMs);
    this.timer = setTimeout(() => void this.tick(), Math.min(delay, this.idlePollMs));
    this.timer.unref?.();
  }

  async tick() {
    if (this.running || this.stopped) return;
    this.running = true;
    try {
      while (!this.stopped && await this.fireDueOnce()) {}
    } finally {
      this.running = false;
      this.wake();
    }
  }

  async fireDueOnce() {
    const nowMs = this.now();
    const check = this.repository.nextDueOccurrenceCheck(nowMs);
    const timer = this.repository.nextDue(nowMs);
    if (check && (!timer || check.nextCheckAtMs <= timer.nextRunAtMs)) {
      return this.fireOccurrenceCheck(check, nowMs);
    }
    if (!timer) return false;
    const event = eventForTimer(timer, nowMs);
    const occurrenceId = event.data.timer.occurrence_id;
    this.repository.createOccurrenceCheck({
      occurrenceId,
      eventId: event.id,
      nextCheckAtMs: nowMs + this.occurrenceCheckDelayMs,
      event,
    });
    try {
      await this.submitEvent(event);
    } catch (error) {
      this.logger?.warn?.('timer_event_submit_failed', {
        timerId: timer.timerId,
        error: error instanceof Error ? error.message : String(error),
      });
    } finally {
      this.repository.finishOccurrence(timer, {
        scheduledTimeMs: timer.nextRunAtMs,
        completedAtMs: nowMs,
        runCount: timer.runCount + 1,
      });
    }
    return true;
  }

  async fireOccurrenceCheck(check, nowMs) {
    if (check.status === 'completed') {
      this.repository.deleteOccurrenceCheck(check.occurrenceId);
      return true;
    }
    const event = eventForOccurrenceCheck(check, nowMs);
    const attempt = check.attempt + 1;
    try {
      await this.submitEvent(event);
    } catch (error) {
      this.logger?.warn?.('timer_check_submit_failed', {
        occurrenceId: check.occurrenceId,
        error: error instanceof Error ? error.message : String(error),
      });
    } finally {
      this.repository.rescheduleOccurrenceCheck(check.occurrenceId, {
        attempt,
        nextCheckAtMs: nowMs + this.occurrenceCheckDelayMs,
      });
    }
    return true;
  }

  stop() {
    this.stopped = true;
    clearTimeout(this.timer);
    this.timer = null;
  }
}
