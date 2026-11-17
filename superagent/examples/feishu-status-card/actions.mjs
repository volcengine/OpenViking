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
  if (operatorId === ownerId) return true;
  return Boolean(task?.requesterId) && operatorId === task.requesterId;
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
    this.events = new Map(); // eventId -> outcome, for redelivered callbacks
  }

  // Only an action that happened settles the card. A click that changed
  // nothing useful (an already ended task, a retry while it still runs) must
  // not spend the one click the card has.
  #remember(key, outcome, eventId) {
    this.settled.set(key, { at: this.now(), outcome });
    if (eventId) this.events.set(eventId, outcome);
    return outcome;
  }

  #refuse(outcome, eventId) {
    if (eventId) this.events.set(eventId, outcome);
    return outcome;
  }

  #alreadySettled(key, eventId) {
    if (eventId && this.events.has(eventId)) return { outcome: 'ignored', reason: 'duplicate_callback' };
    const settled = this.settled.get(key);
    if (settled && this.now() - settled.at < this.settleTtlMs) {
      return { outcome: 'ignored', reason: 'already_settled', settled: settled.outcome };
    }
    return null;
  }

  async handle({ value, operatorId, ownerId, eventId = null }) {
    const parsed = parseTaskCardAction(value);
    if (!parsed) return { outcome: 'ignored', reason: 'not_a_task_card' };

    const task = this.getTask(parsed.taskId);
    if (!task) return { outcome: 'ignored', reason: 'unknown_task' };
    if (!mayActOnTask(task, { operatorId, ownerId })) {
      return { outcome: 'rejected', reason: 'not_allowed', taskId: task.id };
    }

    const key = `${task.id}:${parsed.action}`;
    const duplicate = this.#alreadySettled(key, eventId);
    if (duplicate) return { ...duplicate, taskId: task.id };

    if (parsed.action === 'stop') {
      if (!isLive(task.status)) return this.#refuse({ outcome: 'ignored', reason: 'already_ended', taskId: task.id }, eventId);
      this.stop(task.id, `stopped from the card by ${operatorId}`);
      return this.#remember(key, { outcome: 'stopped', taskId: task.id }, eventId);
    }

    if (!isRetryable(task.status)) return this.#refuse({ outcome: 'ignored', reason: 'not_retryable', taskId: task.id }, eventId);
    const created = await this.retry(task.id);
    const retried = { outcome: 'retried', taskId: task.id, newTaskId: created?.id ?? null };
    return this.#remember(key, retried, eventId);
  }
}
