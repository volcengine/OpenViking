// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { TaskCardActions, mayActOnTask, parseTaskCardAction } from '../actions.mjs';

const NOW = Date.UTC(2026, 10, 17, 9, 40);
const REQUESTER = 'ou_requester0001';
const OWNER = 'ou_owner0001';

function setup(status = 'running') {
  const tasks = new Map([['t331', { id: 't331', status, requesterId: REQUESTER }]]);
  const calls = [];
  const actions = new TaskCardActions({
    getTask: (taskId) => tasks.get(taskId) ?? null,
    stop: (taskId, reason) => { calls.push({ op: 'stop', taskId, reason }); tasks.get(taskId).status = 'stopped'; },
    retry: (taskId) => { calls.push({ op: 'retry', taskId }); return { id: 't332' }; },
    now: () => NOW,
  });
  return { tasks, calls, actions };
}

const value = (action = 'stop', taskId = 't331') => ({ superagent_task: action, task_id: taskId });

describe('card button values', () => {
  it('accepts only the two actions and a well-formed task id', () => {
    assert.deepEqual(parseTaskCardAction(value()), { action: 'stop', taskId: 't331' });
    assert.equal(parseTaskCardAction({ superagent_task: 'delete', task_id: 't331' }), null);
    assert.equal(parseTaskCardAction({ superagent_task: 'stop', task_id: 't331; DROP' }), null);
    assert.equal(parseTaskCardAction({ superagent_task: 'stop' }), null);
    assert.equal(parseTaskCardAction('stop'), null);
  });

  it('lets the requester and the owner act, nobody else', () => {
    const task = { requesterId: REQUESTER };
    assert.equal(mayActOnTask(task, { operatorId: REQUESTER, ownerId: OWNER }), true);
    assert.equal(mayActOnTask(task, { operatorId: OWNER, ownerId: OWNER }), true);
    assert.equal(mayActOnTask(task, { operatorId: 'ou_bystander0001', ownerId: OWNER }), false);
    assert.equal(mayActOnTask({ requesterId: null }, { operatorId: 'ou_x00000001', ownerId: OWNER }), false);
  });

  it('reads the scheduler row, whose requester column is snake_case', async () => {
    const tasks = new Map([['t331', { id: 't331', status: 'running', requester_id: REQUESTER }]]);
    const actions = new TaskCardActions({ getTask: (id) => tasks.get(id) ?? null, stop: () => {}, retry: () => ({ id: 't332' }) });
    assert.equal((await actions.handle({ value: value(), operatorId: REQUESTER })).outcome, 'stopped');
    assert.equal((await actions.handle({ value: value(), operatorId: 'ou_bystander0001' })).reason, 'not_allowed');
  });
});

describe('TaskCardActions', () => {
  it('rejects a bystander before touching the task', async () => {
    const { actions, calls } = setup();
    assert.deepEqual(await actions.handle({ value: value(), operatorId: 'ou_bystander0001', ownerId: OWNER }),
      { outcome: 'rejected', reason: 'not_allowed', taskId: 't331' });
    assert.equal(calls.length, 0);
  });

  it('stops once, whatever the number of clicks or deliveries', async () => {
    const { actions, calls } = setup();
    const first = await actions.handle({ value: value(), operatorId: REQUESTER, eventId: 'ev_1' });
    assert.deepEqual(first, { outcome: 'stopped', taskId: 't331' });
    assert.deepEqual(await actions.handle({ value: value(), operatorId: REQUESTER, eventId: 'ev_1' }),
      { outcome: 'ignored', reason: 'duplicate_callback', taskId: 't331' });
    assert.deepEqual(await actions.handle({ value: value(), operatorId: REQUESTER, eventId: 'ev_2' }),
      { outcome: 'ignored', reason: 'already_settled', taskId: 't331', settled: { outcome: 'stopped', taskId: 't331' } });
    assert.deepEqual(calls, [{ op: 'stop', taskId: 't331', reason: `stopped from the card by ${REQUESTER}` }]);
  });

  it('does not stop a task that already ended, and does not spend the click on it', async () => {
    const { actions, calls, tasks } = setup('done');
    assert.deepEqual(await actions.handle({ value: value(), operatorId: REQUESTER }),
      { outcome: 'ignored', reason: 'already_ended', taskId: 't331' });
    assert.equal(calls.length, 0);
    tasks.get('t331').status = 'running';
    assert.deepEqual(await actions.handle({ value: value(), operatorId: REQUESTER, eventId: 'ev_2' }),
      { outcome: 'stopped', taskId: 't331' });
  });

  it('retries a failed task into a follow-up and nothing else', async () => {
    const { actions, tasks } = setup('failed');
    assert.deepEqual(await actions.handle({ value: value('retry'), operatorId: REQUESTER }),
      { outcome: 'retried', taskId: 't331', newTaskId: 't332' });
    tasks.get('t331').status = 'running';
    assert.equal((await actions.handle({ value: value('retry'), operatorId: REQUESTER, eventId: 'ev_9' })).reason,
      'already_settled');
    const other = setup('running');
    assert.deepEqual(await other.actions.handle({ value: value('retry'), operatorId: REQUESTER }),
      { outcome: 'ignored', reason: 'not_retryable', taskId: 't331' });
  });

  it('carries out one retry when two clicks arrive together', async () => {
    let resolve;
    const gate = new Promise((done) => { resolve = done; });
    const tasks = new Map([['t331', { id: 't331', status: 'failed', requesterId: REQUESTER }]]);
    let created = 0;
    const actions = new TaskCardActions({
      getTask: (id) => tasks.get(id) ?? null,
      stop: () => {},
      retry: async () => { created += 1; await gate; return { id: 't332' }; },
    });
    const first = actions.handle({ value: value('retry'), operatorId: REQUESTER, eventId: 'ev_a' });
    const second = actions.handle({ value: value('retry'), operatorId: REQUESTER, eventId: 'ev_b' });
    resolve();
    const [one, two] = await Promise.all([first, second]);
    assert.equal(one.outcome, 'retried');
    assert.deepEqual(two, { outcome: 'ignored', reason: 'in_flight', taskId: 't331' });
    assert.equal(created, 1, 'the second click must not start a second task');
  });

  it('prunes what is too old to decide anything', async () => {
    let clock = NOW;
    const tasks = new Map([['t331', { id: 't331', status: 'running', requesterId: REQUESTER }]]);
    const actions = new TaskCardActions({
      getTask: (id) => tasks.get(id) ?? null,
      stop: () => {},
      retry: () => ({ id: 't332' }),
      now: () => clock,
      settleTtlMs: 1_000,
    });
    await actions.handle({ value: value(), operatorId: REQUESTER, eventId: 'ev_1' });
    assert.equal(actions.events.size, 1);
    assert.equal(actions.settled.size, 1);
    clock += 60_000;
    tasks.get('t331').status = 'failed';
    await actions.handle({ value: value('retry'), operatorId: REQUESTER, eventId: 'ev_2' });
    assert.equal(actions.events.size, 1);
    assert.deepEqual([...actions.events.keys()], ['ev_2']);
  });

  it('ignores cards that are not task cards and tasks it does not know', async () => {
    const { actions } = setup();
    assert.equal((await actions.handle({ value: { superagent_choice: 'k' }, operatorId: REQUESTER })).outcome, 'ignored');
    assert.equal((await actions.handle({ value: value('stop', 't999'), operatorId: REQUESTER })).reason, 'unknown_task');
  });

  it('forgets a settled card after its window, so a reused task id starts clean', async () => {
    const tasks = new Map();
    let clock = NOW;
    const actions = new TaskCardActions({
      getTask: (taskId) => tasks.get(taskId) ?? null,
      stop: () => {},
      retry: () => ({ id: 't332' }),
      now: () => clock,
      settleTtlMs: 1_000,
    });
    tasks.set('t331', { id: 't331', status: 'running', requesterId: REQUESTER });
    await actions.handle({ value: value(), operatorId: REQUESTER, eventId: 'ev_1' });
    clock += 2_000;
    assert.deepEqual(await actions.handle({ value: value(), operatorId: REQUESTER, eventId: 'ev_2' }),
      { outcome: 'stopped', taskId: 't331' });
  });
});
