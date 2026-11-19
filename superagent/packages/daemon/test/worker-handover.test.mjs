// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { TaskManager } from '../src/tasks.mjs';
import { MessageInbox } from '../src/inbox.mjs';
import { WorkerSettings } from '../src/worker-settings.mjs';
import { recentTranscript } from '../src/worker-transcript.mjs';

const settle = () => new Promise((resolve) => setImmediate(resolve));
const jsonl = (events) => `${events.map((event) => JSON.stringify(event)).join('\n')}\n`;

// Sessions as each runtime writes them.
const CODEX_SESSION = [
  { type: 'session_meta', payload: { id: 'th-1' } },
  { type: 'response_item', payload: { type: 'message', role: 'developer', content: [{ type: 'input_text', text: '## Worker policy' }] } },
  { type: 'response_item', payload: { type: 'message', role: 'user', content: [{ type: 'input_text', text: '<environment_context>\n  <cwd>/w</cwd>' }] } },
  { type: 'response_item', payload: { type: 'message', role: 'user', content: [{ type: 'input_text', text: '[task t1] slugify PR\nImplement slugify and open a PR' }] } },
  { type: 'response_item', payload: { type: 'reasoning', encrypted_content: 'opaque' } },
  { type: 'response_item', payload: { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'I will clone the repo first.' }] } },
  { type: 'response_item', payload: { type: 'function_call', name: 'exec_command', arguments: '{"cmd":"git clone repo"}' } },
  { type: 'response_item', payload: { type: 'function_call_output', output: 'Cloning into repo... (long output)' } },
  { type: 'response_item', payload: { type: 'custom_tool_call', name: 'apply_patch', input: '*** Begin Patch\n*** Update File: src/slugify.js' } },
  { type: 'response_item', payload: { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Implemented slugify; tests pass. Pushing the branch next.' }] } },
];

function writeSession(dir, name, events) {
  const file = path.join(dir, name);
  fs.writeFileSync(file, jsonl(events));
  return file;
}

describe('recentTranscript', () => {
  it('reads the last messages of a Codex, Pi or Claude Code session: inputs, words and one line per tool call', (t) => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'transcript-'));
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    assert.deepEqual(recentTranscript(writeSession(dir, 'codex.jsonl', CODEX_SESSION), 'codex'), [
      { role: 'input', text: '[task t1] slugify PR (…)' },
      { role: 'worker', text: 'I will clone the repo first.' },
      { role: 'tool', text: 'exec_command {"cmd":"git clone repo"}\napply_patch *** Begin Patch *** Update File: src/slugify.js' },
      { role: 'worker', text: 'Implemented slugify; tests pass. Pushing the branch next.' },
    ]);
    const pi = writeSession(dir, 'pi.jsonl', [
      { type: 'session', id: 's1' },
      { type: 'message', message: { role: 'user', content: [{ type: 'text', text: '[task t2] check the docs' }] } },
      { type: 'message', message: { role: 'assistant', content: [{ type: 'thinking', thinking: 'hidden' }, { type: 'text', text: 'Reading.' },
        { type: 'toolCall', name: 'bash', arguments: { command: 'ls docs' } }] } },
      { type: 'message', message: { role: 'toolResult', content: [{ type: 'text', text: 'a.md b.md' }] } },
    ]);
    assert.deepEqual(recentTranscript(pi, 'pi'), [
      { role: 'input', text: '[task t2] check the docs' },
      { role: 'worker', text: 'Reading.' },
      { role: 'tool', text: 'bash {"command":"ls docs"}' },
    ]);
    const claude = writeSession(dir, 'claude.jsonl', [
      { type: 'user', message: { role: 'user', content: '[task t3] fix the bug' } },
      { type: 'assistant', message: { role: 'assistant', content: [{ type: 'thinking', thinking: 'x' }, { type: 'tool_use', name: 'Bash', input: { command: 'npm test' } }] } },
      { type: 'user', message: { role: 'user', content: [{ type: 'tool_result', content: '1 failing' }] } },
      { type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text: 'One test fails; fixing it.' }] } },
    ]);
    assert.deepEqual(recentTranscript(claude, 'claude'), [
      { role: 'input', text: '[task t3] fix the bug' },
      { role: 'tool', text: 'Bash {"command":"npm test"}' },
      { role: 'worker', text: 'One test fails; fixing it.' },
    ]);
    // Bounded: the last entries, each clipped, runs of calls cut to their last four.
    const long = writeSession(dir, 'long.jsonl', [
      ...Array.from({ length: 12 }, (_, index) => ({ type: 'message', message: { role: 'assistant', content: [{ type: 'text', text: `step ${index} ${'x'.repeat(2_000)}` }] } })),
      { type: 'message', message: { role: 'assistant', content: Array.from({ length: 7 }, (_, index) => ({ type: 'toolCall', name: 'read', arguments: { path: `f${index}` } })) } },
    ]);
    const entries = recentTranscript(long, 'pi');
    assert.ok(entries.length <= 8);
    assert.ok(entries.reduce((n, entry) => n + entry.text.length, 0) <= 6_000 + 1_300);
    assert.match(entries.at(-1).text, /^\(3 earlier calls\)\nread \{"path":"f3"\}/);
    assert.ok(entries.every((entry) => entry.role !== 'worker' || entry.text.length <= 1_201));
    assert.deepEqual(recentTranscript(path.join(dir, 'missing.jsonl'), 'pi'), []);
    assert.deepEqual(recentTranscript(pi, 'dsh'), []);
  });
});

// A worker that finishes when the test says so; the session it "ran" in is a
// file the test wrote, found the way the real executor finds it.
function fakeExecutor(sessions) {
  const turns = [];
  return {
    turns,
    supports: (runtime) => ['pi', 'codex', 'traex', 'claude'].includes(runtime),
    transcriptFile: (runtime, taskId, sessionId) => sessions.get(`${runtime}:${sessionId}`) ?? null,
    start({ task, prompt }) {
      let finish;
      const turn = { task, prompt };
      turn.done = new Promise((resolve) => { finish = resolve; });
      turn.finish = (result = { text: '' }) => finish(result);
      turns.push(turn);
      return { done: turn.done, stop: () => finish({ text: '', error: 'stopped' }) };
    },
  };
}

let seq = 0;
function row(fields = {}) {
  seq += 1;
  return { seq, event_id: `e${seq}`, connector_instance: 'lark', source: 'lark', scope_key: 'lark:oc_a:', chat_id: 'oc_a',
    thread_id: null, message_id: `om_${seq}`, chat_type: 'p2p', sender_id: 'ou_owner', content: `line ${seq}`, ...fields };
}

function setup(t, { router = { runtime: 'codex', model: 'gpt-5.6-sol', effort: 'medium' }, config = { fallbackRuntime: 'pi' } } = {}) {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'handover-'));
  t.after(() => fs.rmSync(workspace, { recursive: true, force: true }));
  const db = new DatabaseSync(':memory:');
  db.exec(`CREATE TABLE connector_events (seq INTEGER PRIMARY KEY, message_id TEXT);
    CREATE TABLE outbox (id INTEGER PRIMARY KEY, inbound_seq INTEGER, remote_message_id TEXT);`);
  const sessions = new Map();
  const executor = fakeExecutor(sessions);
  const relayed = [];
  const activities = [];
  const inbox = new MessageInbox({ db, runtime: { snapshot: () => ({ work: { turnInProgress: true } }) } });
  const publish = inbox.publish.bind(inbox);
  inbox.publish = (entry) => { relayed.push(entry.item); publish(entry); };
  const map = new Map();
  const store = { getSetting: (key) => map.get(key) ?? null, setSetting: (key, value) => map.set(key, String(value)) };
  const clock = { now: 1_000_000 };
  const settings = new WorkerSettings({ store, config, router: () => router, now: () => clock.now });
  const tasks = new TaskManager({
    db, config: { workspace }, executor, inbox, runtimes: settings, now: () => clock.now,
    lineFor: (input) => `[chat=${input.chat_id} msg=${input.message_id} event=${input.event_id}] ${input.content}`,
    recordActivity: (kind, fields) => activities.push({ kind, ...fields }),
  });
  t.after(() => { tasks.shutdown(); inbox.stop(); });
  const create = (content, title = 'slugify PR') => tasks.create({ rows: [row({ content })], title, requester: 'weihan', runtime: settings.forNewWork() });
  return { db, tasks, executor, settings, sessions, relayed, activities, workspace, clock, create };
}

describe('worker runtime handover', () => {
  it('starts a task on the workers\' runtime, model and effort, and keeps them while it works', async (t) => {
    const { tasks, executor, settings, create } = setup(t);
    settings.setRuntime('pi');
    settings.setModel('pi', 'volcengine-ark/ep-1');
    create('实现 slugify 并开个 PR');
    assert.deepEqual(['runtime', 'model', 'effort'].map((key) => tasks.get('t1')[key]), ['pi', 'volcengine-ark/ep-1', '']);
    // Workers move to codex while t1 works: its next turn stays on pi.
    settings.setRuntime('codex');
    tasks.route('t1', [row({ content: '函数名叫 toSlug' })], 'owner');
    executor.turns[0].finish({ text: '' });
    await settle();
    assert.equal(executor.turns.length, 2);
    assert.equal(executor.turns[1].task.runtime, 'pi');
    assert.equal(executor.turns[1].task.model, 'volcengine-ark/ep-1');
    assert.doesNotMatch(executor.turns[1].prompt, /\[handover\]/);
    assert.equal(tasks.handovers('t1').length, 0);
  });

  it('moves a task that reopens to the runtime workers use now, from a handover of the last session', async (t) => {
    const { tasks, executor, settings, sessions, workspace, create, relayed, activities } = setup(t);
    create('实现 slugify 并开个 PR');
    const file = writeSession(workspace, 'rollout-th-1.jsonl', CODEX_SESSION);
    sessions.set('codex:th-1', file);
    tasks.report('t1', { state: 'done', draft: 'PR https://github.com/x/pull/1', artifacts: ['https://github.com/x/pull/1'] });
    executor.turns[0].finish({ text: '', nativeSession: 'th-1' });
    await settle();
    assert.equal(tasks.get('t1').status, 'done');
    assert.equal(relayed.length, 1);

    settings.setRuntime('pi');
    tasks.route('t1', [row({ content: '把函数改名叫 toSlug' })], 'owner', { requester: 'weihan' });
    const task = tasks.get('t1');
    assert.deepEqual([task.round, task.runtime, task.native_session, task.model], [2, 'pi', null, '']);
    assert.equal(tasks.roundOf(task).session_state, 'switched');
    const [handover] = tasks.handovers('t1');
    assert.equal(handover.reason, 'round');
    assert.deepEqual([handover.from_runtime, handover.from_session, handover.transcript, handover.to_runtime, handover.turn],
      ['codex', 'th-1', file, 'pi', 1]);
    assert.equal(handover.from_model, 'gpt-5.6-sol');

    const { prompt, task: running } = executor.turns[1];
    assert.equal(running.runtime, 'pi');
    assert.match(prompt, /^\[handover\] You take this task over from a worker that ran on codex \(gpt-5\.6-sol\)\. This round runs on pi: the agent's workers run there now\.\n/);
    assert.match(prompt, new RegExp(`You run on pi, in the same directory \\(${task.workdir.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\)`));
    assert.match(prompt, /The last messages of that worker's session, oldest first \(clipped; tool output left out\):\n\| \[input\] \[task t1\] slugify PR \(…\)\n\| \[worker\] I will clone the repo first\.\n\| \[tool calls\] exec_command \{"cmd":"git clone repo"\}\n\| apply_patch \*\*\* Begin Patch \*\*\* Update File: src\/slugify\.js\n\| \[worker\] Implemented slugify; tests pass\. Pushing the branch next\.\n/);
    assert.match(prompt, new RegExp(`Its whole session, if you need more of it: ${file.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} \\(Codex rollout JSONL`));
    // Then the task record: every round's request, what it found and answered.
    assert.match(prompt, /The task record follows\.\n\[task t1\] slugify PR\n/);
    assert.match(prompt, /Round 1, from weihan \(done\):\n.*实现 slugify 并开个 PR\nRound 2 \(now\), from weihan:\n.*把函数改名叫 toSlug/);
    assert.match(prompt, /Your answer to the previous round \(the requester has it\):\n\| PR https:\/\/github\.com\/x\/pull\/1/);
    assert.ok(activities.some((entry) => entry.kind === 'task.runtime_switched' && /t1 codex → pi \(round\)/.test(entry.summary)));

    // Once a turn on pi completes, the session carries on and the handover is done.
    executor.turns[1].finish({ text: '' });
    await settle();
    assert.ok(tasks.handovers('t1')[0].taken_at_ms);
    tasks.route('t1', [row({ content: '再加个测试' })], 'owner');
    assert.doesNotMatch(executor.turns[2].prompt, /\[handover\]/);
    assert.equal(executor.turns[2].task.runtime, 'pi');
  });

  it('keeps a reopened task on its runtime when workers did not move, with the model set for it now', async (t) => {
    const { tasks, executor, settings, create } = setup(t);
    create('A');
    executor.turns[0].finish({ text: 'done', nativeSession: 'th-1' });
    await settle();
    settings.setModel('codex', 'gpt-6-astra');
    tasks.route('t1', [row({ content: 'B' })], 'owner');
    assert.deepEqual(['runtime', 'native_session', 'model'].map((key) => tasks.get('t1')[key]), ['codex', 'th-1', 'gpt-6-astra']);
    assert.doesNotMatch(executor.turns[1].prompt, /\[handover\]/);
    assert.equal(tasks.handovers('t1').length, 0);
  });

  it('moves a working task off a runtime whose quota ran out, at once, and sends new work past it', async (t) => {
    const { tasks, executor, settings, sessions, workspace, create, relayed } = setup(t);
    create('实现 slugify 并开个 PR');
    sessions.set('codex:th-1', writeSession(workspace, 'rollout-th-1.jsonl', CODEX_SESSION));
    executor.turns[0].finish({ text: '', nativeSession: 'th-1', error: "You've hit your usage limit. Try again at 3:05 PM.", transient: true });
    await settle();
    // No retry on codex: the next turn runs on pi, from the handover.
    assert.equal(executor.turns.length, 2);
    assert.equal(executor.turns[1].task.runtime, 'pi');
    assert.equal(relayed.length, 0, 'nothing reported: the task goes on');
    assert.match(executor.turns[1].prompt, /^\[handover\] You take this task over from a worker that ran on codex \(gpt-5\.6-sol\)\. codex cannot serve any more for now: its quota or usage limit is used up \("You've hit your usage limit\. Try again at 3:05 PM\."\)\.\n/);
    assert.match(executor.turns[1].prompt, /\| \[worker\] Implemented slugify; tests pass\. Pushing the branch next\./);
    assert.deepEqual(tasks.handovers('t1').map((row) => [row.reason, row.from_session]), [['exhausted', 'th-1']]);
    assert.ok(settings.unavailable('codex'));
    // New work starts on pi while codex is out.
    create('另一个任务', 'other');
    assert.equal(tasks.get('t2').runtime, 'pi');
    // The task finishes on pi.
    tasks.report('t1', { state: 'done', draft: 'PR opened' });
    executor.turns[1].finish({ text: '' });
    await settle();
    assert.equal(tasks.get('t1').status, 'done');
    assert.match(relayed[0].text, /state=done/);
  });

  it('moves a task queued on a runtime that ran out before its turn, with a handover only when it has worked', async (t) => {
    const { tasks, executor, settings, create } = setup(t);
    // Three slots busy, so t4 waits in the queue on codex.
    for (const name of ['a', 'b', 'c', 'd']) create(name, name);
    assert.equal(tasks.get('t4').status, 'queued');
    settings.markUnavailable('codex', 'usage limit');
    executor.turns[0].finish({ text: '' });
    await settle();
    assert.equal(executor.turns[3].task.id, 't4');
    assert.equal(executor.turns[3].task.runtime, 'pi');
    assert.equal(tasks.handovers('t4').length, 0, 'it had not worked: nothing to hand over');
    assert.doesNotMatch(executor.turns[3].prompt, /\[handover\]/);
  });

  it('retries a passing failure on its runtime first, then moves the task', async (t) => {
    const { tasks, executor, create } = setup(t);
    create('A');
    for (let attempt = 0; attempt < 3; attempt += 1) {
      executor.turns.at(-1).finish({ text: '', error: '503 Service Unavailable', transient: true, nativeSession: 'th-1' });
      await settle();
    }
    assert.deepEqual(executor.turns.map((turn) => turn.task.runtime), ['codex', 'codex', 'codex', 'pi']);
    assert.deepEqual(tasks.handovers('t1').map((row) => row.reason), ['failing']);
    assert.match(executor.turns[3].prompt, /Its turns kept failing on codex \("503 Service Unavailable"\)\./);
  });

  it('reports the failure as before when there is nowhere to go, or the failure is not the runtime\'s', async (t) => {
    const { tasks, executor, settings, create, relayed } = setup(t);
    create('A');
    // pi is out too: nowhere to go.
    settings.markUnavailable('pi', 'quota');
    executor.turns[0].finish({ text: '', error: 'Claude AI usage limit reached|1763600400' });
    await settle();
    assert.equal(executor.turns.length, 1);
    assert.equal(tasks.get('t1').status, 'failed');
    assert.match(relayed[0].text, /state=failed/);
    assert.ok(settings.unavailable('codex'));
    // A failure another runtime would share moves nothing.
    settings.clearUnavailable('codex');
    settings.clearUnavailable('pi');
    create('B', 'B');
    executor.turns[1].finish({ text: '', error: '401 Unauthorized: invalid api key' });
    await settle();
    assert.equal(tasks.get('t2').status, 'failed');
    assert.equal(tasks.handovers('t2').length, 0);
  });

  it('lets a report filed before the quota ran out stand, and only marks the runtime out', async (t) => {
    const { tasks, executor, settings, create, relayed } = setup(t);
    create('A');
    tasks.report('t1', { state: 'done', draft: 'answer' });
    executor.turns[0].finish({ text: '', error: 'usage limit reached' });
    await settle();
    assert.equal(tasks.get('t1').status, 'done');
    assert.equal(tasks.handovers('t1').length, 0);
    assert.match(relayed[0].text, /state=done/);
    assert.ok(settings.unavailable('codex'));
  });

  it('does not move one round back and forth between failing runtimes', async (t) => {
    const { tasks, executor, create } = setup(t);
    create('A');
    const fail = () => ({ text: '', error: 'upstream overloaded', transient: true });
    // codex: three attempts, then pi; pi: three, then codex; codex: three, then it fails.
    for (let attempt = 0; attempt < 9; attempt += 1) {
      executor.turns.at(-1).finish(fail());
      await settle();
    }
    assert.deepEqual(tasks.handovers('t1').map((row) => `${row.from_runtime}→${row.to_runtime}`), ['codex→pi', 'pi→codex']);
    assert.equal(tasks.get('t1').status, 'failed');
    assert.equal(executor.turns.length, 9);
  });

  it('tells a worker that takes over after a restart both things', async (t) => {
    const { tasks, executor, create, db } = setup(t);
    create('A');
    executor.turns[0].finish({ text: '', error: 'usage limit reached', nativeSession: 'th-1' });
    await settle();
    // The daemon stops during pi's first turn, then restarts.
    tasks.shutdown();
    db.exec("UPDATE tasks SET status = 'queued', recovering = 1");
    const again = new TaskManager({ db, config: tasks.config, executor, inbox: tasks.inbox, runtimes: tasks.runtimes, lineFor: tasks.lineFor });
    t.after(() => again.shutdown());
    again.restore();
    assert.match(executor.turns.at(-1).prompt, /^\[handover\] You take this task over from a worker that ran on codex/);
    assert.match(executor.turns.at(-1).prompt, /The daemon also restarted since: the last turn may have stopped part way\./);
  });
});
