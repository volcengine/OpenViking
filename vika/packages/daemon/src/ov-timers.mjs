import { randomUUID } from 'node:crypto';
import { TimerSchedule } from './timers.mjs';

export class OVTimerRepository {
  constructor(client, instanceId) {
    if (!/^[a-z][a-z0-9-]{0,31}$/.test(instanceId)) throw new Error('OV timers require a valid instance id');
    this.client = client;
    const root = client.user ? `viking://user/${client.user}` : 'viking://~';
    this.uri = `${root}/resources/ld/${instanceId}/timers-state.json`;
    this.state = { timers: [], timer_occurrence_checks: [] };
    this.barrier = Promise.resolve();
  }

  async load() {
    let content;
    try {
      content = (await this.client.json(`/api/v1/content/read?uri=${encodeURIComponent(this.uri)}&offset=0&limit=-1&raw=true`)).result;
    } catch (error) {
      if (error.httpStatus === 404) return this;
      throw error;
    }
    try {
      const state = JSON.parse(content);
      if (!Array.isArray(state.timers) || !Array.isArray(state.timer_occurrence_checks)) throw new Error();
      this.state = state;
    } catch {
      throw new Error('OV timer state is malformed');
    }
    return this;
  }

  mutate(change) {
    const work = this.barrier.then(async () => {
      const state = structuredClone(this.state);
      const result = change(state);
      await this.client.json('/api/v1/content/write', {
        uri: this.uri, content: JSON.stringify(state), mode: 'replace',
        wait: false, processing_mode: 'vectors_only',
      });
      this.state = state;
      return result;
    });
    this.barrier = work.catch(() => {});
    return work;
  }

  get(timerId) {
    return this.state.timers.find((timer) => timer.timerId === timerId) ?? null;
  }

  findByDecision(source, eventId) {
    return this.state.timers.find((timer) => timer.createdFromSource === source && timer.createdFromEventId === eventId) ?? null;
  }

  listActive() {
    return [...this.state.timers].sort((left, right) => left.nextRunAtMs - right.nextRunAtMs || left.createdAtMs - right.createdAtMs);
  }

  nextActive() { return this.listActive()[0] ?? null; }
  nextDue(nowMs = Date.now()) { return this.listActive().find((timer) => timer.nextRunAtMs <= nowMs) ?? null; }

  getOccurrenceCheck(occurrenceId) {
    return this.state.timer_occurrence_checks.find((check) => check.occurrenceId === occurrenceId) ?? null;
  }

  nextOccurrenceCheck() {
    return [...this.state.timer_occurrence_checks].sort((left, right) => left.nextCheckAtMs - right.nextCheckAtMs)[0] ?? null;
  }

  nextDueOccurrenceCheck(nowMs = Date.now()) {
    const check = this.nextOccurrenceCheck();
    return check && check.nextCheckAtMs <= nowMs ? check : null;
  }

  nextWakeAtMs() {
    const values = [this.nextActive()?.nextRunAtMs, this.nextOccurrenceCheck()?.nextCheckAtMs]
      .filter((value) => Number.isSafeInteger(value));
    return values.length ? Math.min(...values) : null;
  }

  observation() {
    return {
      active: this.listActive().map(({ timerId, title, text, timezone, schedule, nextRunAtMs, runCount, maxRuns }) =>
        ({ timerId, title, text, timezone, schedule, nextRunAtMs, runCount, maxRuns })),
      pending: this.state.timer_occurrence_checks.filter((check) => check.status === 'pending')
        .sort((left, right) => left.nextCheckAtMs - right.nextCheckAtMs || left.occurrenceId.localeCompare(right.occurrenceId))
        .map((check) => ({
          occurrenceId: check.occurrenceId, title: check.event.content?.title ?? '',
          timezone: check.event.data?.timer?.timezone ?? 'UTC',
          scheduledTimeMs: check.event.data?.timer?.scheduled_time_ms ?? null,
          attempt: check.attempt, nextCheckAtMs: check.nextCheckAtMs,
        })),
    };
  }

  create({ timerId = `tm_${randomUUID().replaceAll('-', '').slice(0, 24)}`, createdAtMs = Date.now(), ...input }) {
    return this.mutate((state) => {
      const existing = state.timers.find((timer) =>
        timer.createdFromSource === input.createdFromSource && timer.createdFromEventId === input.createdFromEventId);
      if (existing) return existing;
      if (state.timers.some((timer) => timer.timerId === timerId)) throw new Error('Timer id already exists');
      const timer = { ...input, timerId, createdAtMs, lastRunAtMs: null, runCount: 0 };
      state.timers.push(timer);
      return timer;
    });
  }

  cancel(timerId) {
    return this.mutate((state) => {
      const count = state.timers.length;
      const checks = state.timer_occurrence_checks.length;
      state.timers = state.timers.filter((timer) => timer.timerId !== timerId);
      state.timer_occurrence_checks = state.timer_occurrence_checks.filter((check) => !check.occurrenceId.startsWith(`${timerId}:`));
      return state.timers.length < count || state.timer_occurrence_checks.length < checks;
    });
  }

  finishOccurrence(timer, { scheduledTimeMs, completedAtMs, runCount }) {
    return this.mutate((state) => {
      const current = state.timers.find((entry) => entry.timerId === timer.timerId);
      if (!current) return null;
      const nextRunAtMs = timer.maxRuns !== null && runCount >= timer.maxRuns
        ? null : TimerSchedule.nextAfter(timer.schedule, completedAtMs);
      if (nextRunAtMs === null) {
        state.timers = state.timers.filter((entry) => entry.timerId !== timer.timerId);
        return null;
      }
      Object.assign(current, { nextRunAtMs, lastRunAtMs: scheduledTimeMs, runCount });
      return current;
    });
  }

  createOccurrenceCheck({ occurrenceId, eventId, nextCheckAtMs, event }) {
    return this.mutate((state) => {
      let check = state.timer_occurrence_checks.find((entry) => entry.occurrenceId === occurrenceId);
      if (!check) {
        check = { occurrenceId, eventId, nextCheckAtMs, event, status: 'pending', attempt: 0 };
        state.timer_occurrence_checks.push(check);
      }
      return check;
    });
  }

  markOccurrenceCompleted(occurrenceId) {
    return this.mutate((state) => {
      const check = state.timer_occurrence_checks.find((entry) => entry.occurrenceId === occurrenceId);
      if (check) check.status = 'completed';
      return check ?? null;
    });
  }

  deleteOccurrenceCheck(occurrenceId) {
    return this.mutate((state) => {
      const count = state.timer_occurrence_checks.length;
      state.timer_occurrence_checks = state.timer_occurrence_checks.filter((check) => check.occurrenceId !== occurrenceId);
      return state.timer_occurrence_checks.length < count;
    });
  }

  rescheduleOccurrenceCheck(occurrenceId, { attempt, nextCheckAtMs }) {
    return this.mutate((state) => {
      const check = state.timer_occurrence_checks.find((entry) => entry.occurrenceId === occurrenceId);
      if (check && check.status !== 'completed') Object.assign(check, { status: 'pending', attempt, nextCheckAtMs });
      return check ?? null;
    });
  }
}
