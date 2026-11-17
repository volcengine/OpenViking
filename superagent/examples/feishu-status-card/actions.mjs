// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// What a click on a status card is allowed to do.
//
// Two things are decided here, and both matter more than the rendering:
//
//   - Who may act. The callback says who clicked; the task record says who
//     asked for the work. Only the requester or the instance owner may stop or
//     retry it, so a bystander in a group cannot cancel someone else's task by
//     pressing a button.
//   - What happens once. Feishu may deliver a callback more than once, and a
//     card stays on screen after it was used. The first accepted click settles
//     the card; later ones report that nothing changed instead of stopping a
//     new task that happens to reuse the id.

import { isLive, isRetryable } from './card.mjs';

const ACTIONS = new Set(['stop', 'retry']);

// `value` is whatever the card carried. Validate it as external input: a
// button value is only ours because we wrote it, not because it is trusted.
export function parseTaskCardAction(value) {
  if (!value || typeof value !== 'object') return null;
  const action = value.superagent_task;
  const taskId = value.task_id;
  if (!ACTIONS.has(action)) return null;
  if (typeof taskId !== 'string' || !/^t[0-9]{1,6}$/.test(taskId)) return null;
  return { action, taskId };
}

export function mayActOnTask(task, { operatorId, ownerId }) {
  if (!operatorId) return false;
  if (ownerId && operatorId === ownerId) return true;
  // The scheduler's record is snake_case (`requester_id`); a caller that
  // normalised it already passes camelCase. Either names the same person.
  const requesterId = task?.requesterId ?? task?.requester_id ?? null;
  return Boolean(requesterId) && operatorId === requesterId;
}

// `getTask`, `stop` and `retry` are injected: this module decides permission,
// ordering and idempotency, the scheduler owns the task itself.
export class TaskCardActions {
  constructor({ getTask, stop, retry, now = Date.now, settleTtlMs = 10 * 60_000 }) {
    this.getTask = getTask;
    this.stop = stop;
    this.retry = retry;
    this.now = now;
    this.settleTtlMs = settleTtlMs;
    this.settled = new Map(); // `${taskId}:${action}` -> { at, outcome }
    this.events = new Map(); // eventId -> { at, outcome }, for redelivered callbacks
    this.inFlight = new Set(); // `${taskId}:${action}` being carried out right now
  }

  // Two clicks can arrive as two callbacks a few milliseconds apart: a double
  // click, or a client resending while the first answer is still on its way.
  // Neither the settle window nor the event id separates them, so the second
  // one must find the action already in flight.
  async handle({ value, operatorId, ownerId, eventId = null }) {
    const parsed = parseTaskCardAction(value);
    if (!parsed) return { outcome: 'ignored', reason: 'not_a_task_card' };

    const task = this.getTask(parsed.taskId);
    if (!task) return { outcome: 'ignored', reason: 'unknown_task' };
    if (!mayActOnTask(task, { operatorId, ownerId })) {
      return { outcome: 'rejected', reason: 'not_allowed', taskId: task.id };
    }

    this.#prune();
    const key = `${task.id}:${parsed.action}`;
    const duplicate = this.#alreadySettled(key, eventId);
    if (duplicate) return { ...duplicate, taskId: task.id };
    if (this.inFlight.has(key)) return { outcome: 'ignored', reason: 'in_flight', taskId: task.id };

    this.inFlight.add(key);
    try {
      if (parsed.action === 'stop') {
        if (!isLive(task.status)) {
          return this.#refuse({ outcome: 'ignored', reason: 'already_ended', taskId: task.id }, eventId);
        }
        this.stop(task.id, `stopped from the card by ${operatorId}`);
        return this.#remember(key, { outcome: 'stopped', taskId: task.id }, eventId);
      }

      if (!isRetryable(task.status)) {
        return this.#refuse({ outcome: 'ignored', reason: 'not_retryable', taskId: task.id }, eventId);
      }
      const created = await this.retry(task.id);
      return this.#remember(key, { outcome: 'retried', taskId: task.id, newTaskId: created?.id ?? null }, eventId);
    } finally {
      this.inFlight.delete(key);
    }
  }

  // Only an action that happened settles the card. A click that changed
  // nothing useful (an already ended task, a retry while it still runs) must
  // not spend the one click the card has.
  #remember(key, outcome, eventId) {
    this.settled.set(key, { at: this.now(), outcome });
    if (eventId) this.events.set(eventId, { at: this.now(), outcome });
    return outcome;
  }

  #refuse(outcome, eventId) {
    if (eventId) this.events.set(eventId, { at: this.now(), outcome });
    return outcome;
  }

  #alreadySettled(key, eventId) {
    const event = eventId ? this.events.get(eventId) : null;
    if (event) return { outcome: 'ignored', reason: 'duplicate_callback' };
    const settled = this.settled.get(key);
    if (settled && this.now() - settled.at < this.settleTtlMs) {
      return { outcome: 'ignored', reason: 'already_settled', settled: settled.outcome };
    }
    return null;
  }

  // Both maps are keyed by things a chat produces forever (event ids, task
  // ids). Anything older than the settle window can no longer decide anything,
  // so it goes on the next click instead of growing with the process.
  #prune() {
    const cutoff = this.now() - this.settleTtlMs;
    for (const [key, value] of this.settled) if (value.at < cutoff) this.settled.delete(key);
    for (const [eventId, value] of this.events) if (value.at < cutoff) this.events.delete(eventId);
  }
}
