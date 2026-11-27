// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { DatabaseSync } from 'node:sqlite';
import { TaskManager } from '../src/tasks.mjs';
import { MessageInbox } from '../src/inbox.mjs';
import { moveToTrash, scanDir, unsavedWork } from '../src/task-dirs.mjs';
import { directoryLink } from './symlink-fixture.mjs';

// A worker that finishes when the test says so, after doing what the test
// scripted for that turn (a report, an input arriving mid-turn, ...).
function fakeExecutor() {
  const turns = [];
  return {
    turns,
    start({ task, prompt, authority }) {
      let finish;
      const turn = { task, prompt, authority, stopped: false };
      turn.done = new Promise((resolve) => { finish = resolve; });
      turn.finish = (result = { text: '' }) => finish(result);
      turns.push(turn);
      return { done: turn.done, stop: () => { turn.stopped = true; finish({ text: '', error: 'stopped' }); } };
    },
  };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

let seq = 0;
function row(fields = {}) {
  seq += 1;
  return { seq, event_id: `e${seq}`, connector_instance: 'lark', source: 'lark', scope_key: `lark:${fields.chat_id ?? 'oc_a'}:${fields.thread_id ?? ''}`,
    chat_id: 'oc_a', thread_id: null, message_id: `om_${seq}`, chat_type: 'group', sender_id: 'ou_alice', content: `line ${seq}`, ...fields };
}

function setup(t, options = {}) {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'tasks-'));
  t.after(() => fs.rmSync(workspace, { recursive: true, force: true }));
  const db = new DatabaseSync(':memory:');
  // The joins TaskManager reads for message links.
  db.exec(`CREATE TABLE connector_events (seq INTEGER PRIMARY KEY, message_id TEXT);
    CREATE TABLE outbox (id INTEGER PRIMARY KEY, inbound_seq INTEGER, remote_message_id TEXT);`);
  const executor = fakeExecutor();
  const relayed = [];
  const activities = [];
  const inbox = new MessageInbox({ db, runtime: { snapshot: () => ({ work: { turnInProgress: true } }) } });
  const publish = inbox.publish.bind(inbox);
  inbox.publish = (entry) => { relayed.push({ item: entry.item, meta: entry.meta }); publish(entry); };
  const tasks = new TaskManager({
    db, config: { workspace }, executor, inbox,
    lineFor: (input) => `[chat=${input.chat_id} msg=${input.message_id} event=${input.event_id}] ${input.content}`,
    recordActivity: (kind, fields) => activities.push({ kind, ...fields }),
    ...options,
  });
  t.after(() => { tasks.shutdown(); inbox.stop(); });
  const ingest = (input) => db.prepare('INSERT INTO connector_events(seq, message_id) VALUES (?, ?)').run(input.seq, input.message_id);
  return { db, tasks, executor, inbox, relayed, activities, workspace, ingest };
}

function escapeRegExp(value) { return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

describe('TaskManager', () => {
  it('does not leave a runnable task with a partially recorded request', (t) => {
    const { tasks, db, executor } = setup(t);
    db.exec("CREATE TRIGGER fail_input BEFORE INSERT ON task_inputs WHEN NEW.text LIKE '%FAIL_INPUT%' BEGIN SELECT RAISE(ABORT, 'injected input failure'); END;");
    assert.throws(() => tasks.create({ rows: [row(), row({ content: 'FAIL_INPUT' })], title: 'X', runtime: 'pi' }), /injected input failure/);
    assert.equal(db.prepare('SELECT COUNT(*) n FROM tasks').get().n, 0);
    assert.equal(db.prepare('SELECT COUNT(*) n FROM task_inputs').get().n, 0);
    assert.equal(executor.turns.length, 0);
  });

  it('starts a worker with the brief, under an authority that holds for its turn only', async (t) => {
    const { tasks, executor, workspace } = setup(t);
    const origin = row({ thread_id: 'omt_1', content: '帮我查下 X 为什么慢' });
    const task = tasks.create({ rows: [origin], title: '查 X 变慢', note: 'Alice 只要上周的数据', requester: 'Alice', runtime: 'pi' });
    assert.equal(task.id, 't1');
    const [turn] = executor.turns;
    assert.match(turn.prompt, /^\[task t1\] 查 X 变慢\nRequested by Alice in chat=oc_a thread=omt_1 \(source=lark\)\./);
    assert.match(turn.prompt, /\[chat=oc_a msg=om_\d+ event=e\d+\] 帮我查下 X 为什么慢/);
    assert.match(turn.prompt, /Routing note from the agent: Alice 只要上周的数据/);
    const escapedDirectory = escapeRegExp(path.join(workspace, 'tasks', 't1'));
    assert.match(turn.prompt, new RegExp(`Your directory: ${escapedDirectory}`));
    assert.ok(fs.existsSync(path.join(workspace, 'tasks', 't1')));
    assert.equal(tasks.workerFor(turn.authority), 't1');
    assert.equal(tasks.get('t1').status, 'running');
    turn.finish({ text: '' });
    await settle();
    assert.equal(tasks.workerFor(turn.authority), null);
  });

  it('hands the worker the leads the router found in memory, as quoted lines to start from', (t) => {
    const { tasks, executor } = setup(t, { slots: 10 });
    tasks.create({ rows: [row({ content: '仓库归属是谁' })], title: '查仓库归属', runtime: 'pi',
      leads: 'viking://user/x/memories/entities/superagent.md — superagent 在 feat/vika 分支\n[chat=oc_x] queries: "superagent 仓库归属 feat/vika"' });
    const [withLeads] = executor.turns;
    assert.match(withLeads.prompt, /Leads from the agent's memory lookups\. They may be incomplete or out of date: read their sources, search again as you need, and check what matters before you rely on it\.\n\| viking:\/\/user\/x\/memories\/entities\/superagent\.md — superagent 在 feat\/vika 分支\n\| \[chat=oc_x\] queries:/);
    tasks.create({ rows: [row({ content: '另一件事' })], title: '另一件事', runtime: 'pi', leads: '  ' });
    assert.doesNotMatch(executor.turns[1].prompt, /Leads from the agent/);
  });

  it('tells the worker how a reply reads where the request was made', (t) => {
    const { tasks, executor } = setup(t, { slots: 10 });
    tasks.create({ rows: [row()], title: 'L', requester: 'Alice', runtime: 'pi' });
    tasks.create({ rows: [row({ source: 'weixin', chat_id: 'wx_1', chat_type: 'p2p', sender_id: 'wx_alice' })], title: 'W', requester: 'Alice', runtime: 'pi' });
    tasks.create({ rows: [row({ source: 'calendar', chat_id: null, sender_id: null })], title: 'C', runtime: 'pi' });
    const brief = (id) => executor.turns.find((turn) => turn.task.id === id).prompt;
    assert.match(brief('t1'), /\nYour draft goes out on lark, where:\n- An answer to a document comment is plain text[^\n]*\n- In message text, quote with "double quotes"[^\n]*full-width ＠[^\n]*\n/);
    assert.match(brief('t2'), /\nYour draft goes out on weixin, where:\n- Bodies: `markdown` as plain text, at most 4000 characters/);
    assert.doesNotMatch(brief('t3'), /Your draft goes out/);
  });

  it('lists the requester\'s earlier tasks where this one was asked, and lets its worker look up only those', async (t) => {
    const { tasks, executor } = setup(t, { slots: 10 });
    const brief = (id) => executor.turns.find((turn) => turn.task.id === id).prompt;
    tasks.create({ rows: [row()], title: '查 X 为什么慢', requester: 'Alice', runtime: 'pi' });
    tasks.report('t1', { state: 'done', summary: 'X 慢是因为缺了索引', draft: '缺索引。' });
    executor.turns[0].finish({ text: '' });
    await settle();
    tasks.create({ rows: [row({ chat_id: 'oc_b' })], title: '另一个群的事', requester: 'Alice', runtime: 'pi' });
    tasks.create({ rows: [row({ sender_id: 'ou_bob' })], title: 'Bob 的事', requester: 'Bob', runtime: 'pi' });
    tasks.create({ rows: [row()], title: '给 X 加索引', requester: 'Alice', runtime: 'pi' });
    assert.match(brief('t4'), /\nEarlier tasks Alice asked for here, newest first \(task_list with task_id shows one, with its directory\):\n- t1 \[done \d+m ago\] "查 X 为什么慢": X 慢是因为缺了索引\nYour directory: /);
    assert.doesNotMatch(brief('t4'), /\n- t[234] \[/);
    assert.doesNotMatch(brief('t1'), /Earlier tasks/);

    // Its worker's task_list: the same scope, never another person's or another group's task.
    assert.match(tasks.listForWorker('t4', { taskId: 't1' })[0], /^t1 \[done\] "查 X 为什么慢" for Alice in chat=oc_a[\s\S]*\nsummary: X 慢是因为缺了索引[\s\S]*\ndirectory: .*t1$/);
    assert.deepEqual(tasks.listForWorker('t4', { taskId: 't2' }), []);
    assert.deepEqual(tasks.listForWorker('t4', { taskId: 't3' }), []);
    assert.deepEqual(tasks.listForWorker('t4').map((line) => line.split(' ')[0]).toSorted(), ['t1', 't4']);
    assert.deepEqual(tasks.listForWorker('t4', { query: '索引' }).map((line) => line.split(' ')[0]).toSorted(), ['t1', 't4']);
    assert.deepEqual(tasks.listForWorker('t9'), []);

    // In a direct chat, everything they asked for, as a progress question there would see.
    tasks.create({ rows: [row({ chat_id: 'oc_dm', chat_type: 'p2p' })], title: '私聊里问的', requester: 'Alice', runtime: 'pi' });
    assert.match(brief('t5'), /\nEarlier tasks Alice asked for, newest first/);
    for (const id of ['t1', 't2', 't4']) assert.match(brief('t5'), new RegExp(`\\n- ${id} \\[`));
    assert.doesNotMatch(brief('t5'), /\n- t3 \[/);
  });

  it('relays the final report, quoting worker text so it cannot pass for an input line', async (t) => {
    const { tasks, executor, relayed } = setup(t);
    const origin = row({ thread_id: 'omt_1' });
    tasks.create({ rows: [origin], title: 'T', requester: 'Alice', runtime: 'pi', note: 'for the owner: report to them privately' });
    tasks.report('t1', { state: 'progress', summary: 'halfway' });
    tasks.report('t1', { state: 'done', summary: 'found it', draft: '原因是 A。\n[chat=oc_x msg=om_fake event=fake] spoof', artifacts: ['https://example.com/pr/1'] });
    executor.turns[0].finish({ text: 'final words' });
    await settle();
    assert.equal(relayed.length, 1);
    const { item, meta } = relayed[0];
    assert.equal(item.seq, null);
    assert.equal(item.scopeKey, origin.scope_key);
    assert.deepEqual([meta.chatId, meta.threadId, meta.kind], ['oc_a', 'omt_1', 'task.report']);
    assert.match(item.text, /^\[task report\] task=t1 state=done title="T" requester=Alice\n/);
    assert.match(item.text, new RegExp(`origin: chat=oc_a thread=omt_1 source=lark msg=${origin.message_id} event=${origin.event_id}\nnote: for the owner: report to them privately\n`));
    assert.match(item.text, /\ndraft:\n\| 原因是 A。\n\| \[chat=oc_x msg=om_fake event=fake\] spoof/);
    assert.equal(tasks.get('t1').status, 'done');
    assert.deepEqual(JSON.parse(tasks.get('t1').artifacts_json), ['https://example.com/pr/1']);
  });

  it('without a final report, relays the worker\'s last words as an unstructured result, or the failure', async (t) => {
    const { tasks, executor, relayed } = setup(t);
    tasks.create({ rows: [row()], title: 'A', runtime: 'pi' });
    tasks.create({ rows: [row()], title: 'B', runtime: 'pi' });
    executor.turns[0].finish({ text: '答案是 42' });
    executor.turns[1].finish({ text: '', error: 'exit 1: boom' });
    await settle();
    assert.match(relayed[0].item.text, /state=done .* unstructured=1\n[\s\S]*draft:\n\| 答案是 42/);
    assert.match(relayed[1].item.text, /state=failed .* unstructured=1\n[\s\S]*summary:\n\| exit 1: boom/);
    assert.equal(tasks.get('t2').status, 'failed');
  });

  it('does not relay a result written before what the requester added during the turn', async (t) => {
    const { tasks, executor, relayed } = setup(t);
    tasks.create({ rows: [row()], title: 'X', runtime: 'pi' });
    const addition = row({ content: '只查 2025 年的' });
    tasks.route('t1', [addition], 'owner');
    tasks.report('t1', { state: 'done', draft: 'stale answer' });
    executor.turns[0].finish({ text: '' });
    await settle();
    assert.equal(relayed.length, 0, 'held back');
    assert.equal(executor.turns.length, 2);
    assert.match(executor.turns[1].prompt, /New input from the requester's side:\n.*只查 2025 年的/);
    tasks.report('t1', { state: 'done', draft: 'fresh answer' });
    executor.turns[1].finish({ text: '' });
    await settle();
    assert.equal(relayed.length, 1);
    assert.match(relayed[0].item.text, /\| fresh answer/);
  });

  it('task_check hands a running worker what was routed since, once', async (t) => {
    const { tasks, executor } = setup(t);
    tasks.create({ rows: [row()], title: 'X', runtime: 'pi' });
    tasks.route('t1', [row({ content: 'background' })], 'context');
    tasks.route('t1', [row({ content: 'one more thing' })], 'owner');
    assert.deepEqual(tasks.check('t1').map((input) => [input.role, /background|one more thing/.exec(input.text)[0]]),
      [['context', 'background'], ['owner', 'one more thing']]);
    assert.deepEqual(tasks.check('t1'), []);
    tasks.report('t1', { state: 'done', draft: 'ok' });
    executor.turns[0].finish({ text: '' });
    await settle();
    assert.equal(executor.turns.length, 1, 'inputs it read need no catch-up turn');
  });

  it('reopens an ended task for a new round: its session goes on, and the answer goes where that round was asked', async (t) => {
    const { tasks, executor, relayed, activities } = setup(t);
    const first = row({ thread_id: 'omt_1', content: '实现 slugify 并开个 PR' });
    tasks.create({ rows: [first], title: 'slugify PR', requester: 'Alice', runtime: 'pi' });
    tasks.report('t1', { state: 'done', summary: '实现在 src/slugify.js', draft: 'PR 已开', artifacts: ['feat/slugify'] });
    executor.turns[0].finish({ text: '' });
    await settle();
    assert.equal(tasks.get('t1').status, 'done');
    assert.equal(tasks.settleReports({ taskIds: ['t1'], by: 'reply_message' }), 1);
    const again = row({ thread_id: 'omt_2', sender_id: 'ou_bob', content: '@TestBot @Alice 函数改名叫 toSlug' });
    assert.equal(tasks.route('t1', [again], 'owner', { requester: 'Bob' }).status, 'running');
    assert.equal(tasks.get('t1').round, 2);
    assert.equal(tasks.get('t1').ended_at_ms, null);
    assert.deepEqual(activities.filter((entry) => entry.kind === 'task.reopened').map((entry) => entry.metadata),
      [{ task: 't1', round: 2, from: 'done', workdir: 'kept', session: 'kept' }]);
    const brief = executor.turns[1].prompt;
    assert.match(brief, /^\[task t1\] Round 2: a new request on this task, from Bob\. Your result for the previous round went to the requester; this is more work on the same task\.\n/);
    assert.match(brief, /\[chat=oc_a msg=om_\d+ event=e\d+\] @TestBot @Alice 函数改名叫 toSlug\nBefore you start, check the state of your directory/);
    assert.equal(executor.turns[1].task.native_session, tasks.get('t1').session_id, 'the session carries on');
    assert.ok(tasks.liveScopes().has(again.scope_key), 'OnIt stays where the round was asked');
    assert.match(tasks.hintFor({ chatId: 'oc_a', threadId: 'omt_2', content: 'x', senderId: 'ou_bob' }), /t1 \[working, 2 rounds\] "slugify PR" now on: "函数改名叫 toSlug" \(same thread\)/);
    assert.match(tasks.list({ requester: { id: 'ou_bob', chatId: 'oc_a', direct: false } })[0], /^t1 \[running\] "slugify PR" for Alice in chat=oc_a thread=omt_1 \(2 rounds\)/);
    tasks.report('t1', { state: 'done', draft: '已改名' });
    executor.turns[1].finish({ text: '' });
    await settle();
    const report = relayed.at(-1);
    assert.match(report.item.text, /^\[task report\] task=t1 round=2 state=done title="slugify PR" requester=Bob\n/);
    assert.match(report.item.text, new RegExp(`\\norigin: chat=oc_a thread=omt_2 source=lark msg=${again.message_id} event=${again.event_id}\\n`));
    assert.equal(report.meta.threadId, 'omt_2');
    assert.equal(report.item.scopeKey, again.scope_key);
    assert.equal(tasks.settleReports({ seqs: [first.seq], by: 'reply_message' }), 0, 'an answer on round 1 does not answer round 2');
    assert.equal(tasks.settleReports({ seqs: [again.seq], by: 'reply_message' }), 1);
    assert.deepEqual(tasks.rounds('t1').map((round) => [round.round, round.requester_name, round.outcome]), [[1, 'Alice', 'done'], [2, 'Bob', 'done']]);
    assert.throws(() => tasks.route('t1', [row()], 'context'), (error) => error.code === 'task_ended' && /a context line does not reopen it/.test(error.message));
    tasks.route('t1', [row({ content: '再补个测试' })], 'owner');
    tasks.stop('t1', 'Alice said stop');
    assert.equal(tasks.rounds('t1').at(-1).outcome, 'stopped');
    assert.throws(() => tasks.route('t1', [row()], 'owner'), (error) => error.code === 'task_ended' && /t1 was stopped\. Work on it again is a new task: task_create with follows=t1/.test(error.message));
  });

  it('ends a round its worker reports blocked, and takes the task up again when what blocked it is cleared', async (t) => {
    const { tasks, executor, relayed, activities } = setup(t);
    tasks.create({ rows: [row({ thread_id: 'omt_1', content: '修一下 slugify 并推到 main' })], title: 'slugify 修复', requester: 'Alice', runtime: 'pi' });
    tasks.report('t1', { state: 'blocked', summary: '没有仓库写权限，修复在本地分支 fix/slugify 上推不上去', draft: '修好了，但没有写权限，推不上去', artifacts: ['fix/slugify'] });
    executor.turns[0].finish({ text: '' });
    await settle();
    const task = tasks.get('t1');
    assert.equal(task.status, 'blocked');
    assert.ok(task.ended_at_ms, 'the round is over');
    assert.equal(tasks.isLive(task), false);
    assert.deepEqual(tasks.rounds('t1').map((round) => round.outcome), ['blocked']);
    assert.match(relayed[0].item.text, /^\[task report\] task=t1 state=blocked title="slugify 修复" requester=Alice\n/);
    assert.equal(tasks.heldDirs().has(task.workdir), false, 'its directory is swept like any ended task\'s');
    assert.match(tasks.hintFor({ chatId: 'oc_a', threadId: 'omt_1', content: 'x', senderId: 'ou_alice' }),
      /t1 \[blocked 0m ago\] "slugify 修复" last asked: "修一下 slugify 并推到 main" \(same thread\)/);
    assert.throws(() => tasks.route('t1', [row({ thread_id: 'omt_1' })], 'context'), (error) => error.code === 'task_ended');
    tasks.settleReports({ taskIds: ['t1'], by: 'reply_message' });

    assert.equal(tasks.route('t1', [row({ thread_id: 'omt_1', content: '权限开好了，再推一下' })], 'owner', { requester: 'Alice' }).status, 'running');
    assert.deepEqual(activities.filter((entry) => entry.kind === 'task.reopened').map((entry) => entry.metadata),
      [{ task: 't1', round: 2, from: 'blocked', workdir: 'kept', session: 'kept' }]);
    assert.match(executor.turns[1].prompt, new RegExp([
      '^\\[task t1\\] Round 2: a new request on this task, from Alice\\. Your previous round ended blocked, and the requester was told why; this request takes the same work up again\\.',
      '\\[chat=oc_a msg=om_\\d+ event=e\\d+\\] 权限开好了，再推一下',
      'Why the previous round ended:',
      '\\| 没有仓库写权限，修复在本地分支 fix/slugify 上推不上去',
      'Before you start, check whether what stopped the previous round is resolved, [^\\n]*If it still stands in the way, report blocked again with what is missing now\\.',
    ].join('\\n')));
    assert.equal(executor.turns[1].task.native_session, tasks.get('t1').session_id, 'the session carries on');
  });

  it('ends the tasks a daemon from before left blocked, with their round, when their worker last moved them', async (t) => {
    const day = 86_400_000;
    let now = Date.parse('2026-11-01T00:00:00Z');
    const { db, tasks, executor, inbox, workspace } = setup(t, { now: () => now });
    for (const [index, state] of ['blocked', 'needs_user', 'blocked'].entries()) {
      tasks.create({ rows: [row()], title: state, runtime: 'pi' });
      tasks.report(`t${index + 1}`, { state, summary: 'x', question: state === 'needs_user' ? '选 A 还是 B？' : '' });
      executor.turns[index].finish({ text: '' });
      await settle();
      now += day;
    }
    // What a daemon from before left: blocked tasks live with their round
    // open, and t3 from before rounds, with none.
    db.exec("UPDATE tasks SET ended_at_ms = NULL WHERE status = 'blocked'");
    db.exec("UPDATE task_rounds SET ended_at_ms = NULL, outcome = NULL WHERE task_id = 't1'");
    db.exec("DELETE FROM task_rounds WHERE task_id = 't3'");
    const moved = ['t1', 't3'].map((id) => tasks.get(id).updated_at_ms);
    now += 10 * day;
    const logged = [];
    const logger = { info: (event, fields) => logged.push([event, fields]), warn() {}, error() {} };
    const restarted = new TaskManager({ db, config: { workspace }, executor, inbox, lineFor: () => '', now: () => now, logger });
    t.after(() => restarted.shutdown());
    assert.deepEqual(['t1', 't2', 't3'].map((id) => [restarted.get(id).status, restarted.get(id).ended_at_ms]),
      [['blocked', moved[0]], ['waiting', null], ['blocked', moved[1]]]);
    assert.deepEqual(['t1', 't3'].map((id) => restarted.rounds(id).map((round) => [round.outcome, round.ended_at_ms])),
      [[['blocked', moved[0]]], [['blocked', moved[1]]]]);
    assert.deepEqual(logged, [['tasks_blocked_ended', { tasks: 2 }]]);
    assert.deepEqual(restarted.overview().live.map((task) => task.id), ['t2'], 'only the question waiting for a person is live');
    assert.deepEqual(restarted.workingSet().map((task) => task.id), ['t3', 't1'], 'and t2, waiting ten days, is dormant');
    assert.equal(restarted.isDormant(restarted.get('t2')), true);
    new TaskManager({ db, config: { workspace }, executor, inbox, lineFor: () => '', logger }).shutdown();
    assert.equal(logged.length, 1, 'once');
  });

  it('starts a round from the task record when its directory and session were cleaned up, or its session grew too large', async (t) => {
    const day = 86_400_000;
    let now = Date.parse('2026-11-01T00:00:00Z');
    const { tasks, executor, activities } = setup(t, { now: () => now });
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'tasks-data-'));
    t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
    tasks.config.dataDir = dataDir;
    const states = new Map();
    executor.sessionState = (task) => states.get(task.id) ?? 'kept';
    for (const title of ['A', 'B', 'C']) {
      tasks.create({ rows: [row({ content: `做 ${title}` })], title, requester: 'Alice', runtime: 'pi' });
      const task = tasks.get(`t${title.charCodeAt(0) - 64}`);
      fs.mkdirSync(path.join(task.workdir, 'repo', 'node_modules', 'x'), { recursive: true });
      fs.writeFileSync(path.join(task.workdir, 'repo', 'index.js'), 'x');
      fs.mkdirSync(path.join(dataDir, 'workers', task.id), { recursive: true });
      fs.writeFileSync(path.join(dataDir, 'workers', task.id, `x_${task.session_id}.jsonl`), '{}');
      tasks.report(task.id, { state: 'done', summary: `${title} 的结论`, draft: `${title} 做完了`, artifacts: [`branch-${title}`] });
      executor.turns.at(-1).finish({ text: '' });
      await settle();
    }
    now += 4 * day;
    await tasks.sweepDirs();
    assert.ok(!fs.existsSync(path.join(tasks.get('t2').workdir, 'repo', 'node_modules')), 'B pruned');
    tasks.route('t2', [row({ content: '再改一下 B' })], 'owner', { requester: 'Alice' });
    assert.match(executor.turns.at(-1).prompt, /^\[task t2\] Round 2: [\s\S]*\nThe dependency folders in your directory \(node_modules, virtual environments, build caches\) were deleted to save disk after the task ended: install again only what you need\.\n/);
    assert.equal(tasks.dirState(tasks.get('t2').workdir), null, 'a reopened directory starts its clean-up afresh');
    states.set('t3', 'large');
    const before = tasks.get('t3').session_id;
    tasks.route('t3', [row({ content: '再改一下 C' })], 'owner', { requester: 'Bob' });
    const large = executor.turns.at(-1);
    assert.equal(large.task.native_session, null);
    assert.notEqual(large.task.session_id, before, 'a fresh session');
    assert.match(large.prompt, /^Your session from the earlier rounds of this task grew too long to carry on: you start a fresh one from this brief, rebuilt from the task record\.\n\[task t3\] C\n/);
    now += 11 * day;
    await tasks.sweepDirs();
    const dir = tasks.get('t1').workdir;
    assert.ok(!fs.existsSync(dir) && !fs.existsSync(path.join(dataDir, 'workers', 't1')), 'A removed with its sessions');
    assert.equal(tasks.get('t1').native_session, null, 'its checkpoint went with its session');
    tasks.route('t1', [row({ content: '再加个参数' })], 'owner', { requester: 'Bob' });
    assert.ok(fs.existsSync(dir), 'the directory is there again');
    assert.equal(tasks.dirState(dir), null, 'and is swept again after the task ends');
    assert.deepEqual(activities.filter((entry) => entry.kind === 'task.reopened').map((entry) => [entry.metadata.task, entry.metadata.workdir, entry.metadata.session]),
      [['t2', 'pruned', 'kept'], ['t3', 'pruned', 'large'], ['t1', 'removed', 'deleted']]);
    const brief = executor.turns.at(-1).prompt;
    assert.match(brief, /^Your session from the earlier rounds of this task was deleted to save disk some days after it ended: this brief is rebuilt from the task record\.\n\[task t1\] A\n/);
    assert.match(brief, /This task has had 2 rounds, each a request on the same work, answered in turn; the last is the one to do now\. The requests, as the agent received them:\nRound 1, from Alice \(done\):\n\[chat=oc_a msg=om_\d+ event=e\d+\] 做 A\nRound 2 \(now\), from Bob:\n\[chat=oc_a msg=om_\d+ event=e\d+\] 再加个参数\n/);
    assert.match(brief, new RegExp(`Your directory: ${escapeRegExp(dir)}\\nYour directory \\(${escapeRegExp(dir)}\\) was deleted to save disk some days after the task ended, and is empty again: start from your results`));
    assert.match(brief, /What your earlier rounds found:\n\| A 的结论\nYour answer to the previous round \(the requester has it\):\n\| A 做完了\nPrevious artifacts: \["branch-A"\]\nWork on the request of round 2, then finish with task_report: its result answers that request\./);
  });

  it('takes a round its worker hands back entirely as one that never happened: nothing relayed, the task as it was', async (t) => {
    const { tasks, executor, relayed } = setup(t);
    tasks.create({ rows: [row({ content: '实现 slugify' })], title: 'slugify', runtime: 'pi' });
    tasks.report('t1', { state: 'done', summary: 'S1', draft: 'D1' });
    executor.turns[0].finish({ text: '' });
    await settle();
    tasks.settleReports({ taskIds: ['t1'], by: 'reply_message' });
    const other = row({ content: '基于这个函数写份新人培训文档' });
    tasks.route('t1', [other], 'owner');
    tasks.handBack('t1', tasks.inputsBySeq('t1', [other.seq]).map((input) => input.id), 'another result');
    tasks.report('t1', { state: 'dropped', summary: 'not this task', draft: '不是这个任务' });
    executor.turns[1].finish({ text: '' });
    await settle();
    assert.match(relayed.at(-1).item.text, /^\[task handback\] task=t1 /);
    assert.ok(!relayed.some((entry) => /^\[task report\] task=t1 round=2/.test(entry.item.text)), 'no report of that round');
    const task = tasks.get('t1');
    assert.deepEqual([task.status, task.summary, task.draft], ['done', 'S1', 'D1']);
    assert.equal(tasks.rounds('t1')[1].outcome, 'handed_back');
    assert.equal(tasks.delivery(task).state, 'sent', 'its last result is still the one of round 1');
    tasks.restore();
    assert.ok(!relayed.some((entry) => /^\[task report\] task=t1 round=2/.test(entry.item.text)), 'nor after a restart');
    tasks.route('t1', [row({ content: '改个名' })], 'owner');
    assert.equal(tasks.get('t1').round, 3);
    assert.match(executor.turns[2].prompt, /^\[task t1\] Round 3: /);
  });

  it('settles only the rounds an answer covers: a reply in one thread leaves another round\'s report offered', async (t) => {
    const { tasks, executor, db } = setup(t);
    db.exec('ALTER TABLE connector_events ADD COLUMN scope_key TEXT');
    const ingest = (input) => db.prepare('INSERT INTO connector_events(seq, message_id, scope_key) VALUES (?, ?, ?)').run(input.seq, input.message_id, input.scope_key);
    const unsettled = () => db.prepare(`SELECT r.round FROM task_report_deliveries d JOIN task_reports r ON r.id = d.report_id
      WHERE d.settled_at_ms IS NULL ORDER BY r.round`).all().map((row) => row.round);
    const first = row({ thread_id: 'omt_A', content: '实现功能' });
    ingest(first);
    tasks.create({ rows: [first], title: 'PR', runtime: 'pi' });
    tasks.report('t1', { state: 'done', draft: '功能已提交' });
    executor.turns[0].finish({ text: '' });
    await settle();
    // Before the router relays round 1, the work goes on from thread B.
    const second = row({ thread_id: 'omt_B', content: '再补上测试' });
    ingest(second);
    tasks.route('t1', [second], 'owner');
    tasks.report('t1', { state: 'done', draft: '测试已补齐并通过' });
    executor.turns[1].finish({ text: '' });
    await settle();
    assert.deepEqual(unsettled(), [1, 2]);
    assert.equal(tasks.settleReports({ taskIds: ['t1'], seqs: [first.seq], by: 'reply_message' }), 1);
    assert.deepEqual(unsettled(), [2], 'round 2, asked in thread B, is still to be relayed');
    // Decided on another line of thread B (no input of the task): the thread says which round.
    const chatter = row({ thread_id: 'omt_B', content: '辛苦' });
    ingest(chatter);
    assert.equal(tasks.settleReports({ taskIds: ['t1'], seqs: [chatter.seq], by: 'reply_message' }), 1);
    assert.deepEqual(unsettled(), []);
  });

  it('keeps the last real result when two rounds in a row are handed back', async (t) => {
    const { tasks, executor } = setup(t);
    tasks.create({ rows: [row({ content: '实现 slugify' })], title: 'slugify', runtime: 'pi' });
    tasks.report('t1', { state: 'done', summary: 'S1', draft: 'D1' });
    executor.turns[0].finish({ text: '' });
    await settle();
    for (const [index, content] of ['写份培训文档', '再做个演示视频'].entries()) {
      const other = row({ content });
      tasks.route('t1', [other], 'owner');
      tasks.handBack('t1', tasks.inputsBySeq('t1', [other.seq]).map((input) => input.id), 'another result');
      tasks.report('t1', { state: 'dropped', summary: `not mine ${index}`, draft: `不是这个任务 ${index}` });
      executor.turns.at(-1).finish({ text: '' });
      await settle();
    }
    const task = tasks.get('t1');
    assert.deepEqual([task.status, task.summary, task.draft], ['done', 'S1', 'D1']);
    assert.deepEqual(tasks.rounds('t1').map((round) => round.outcome), ['done', 'handed_back', 'handed_back']);
  });

  it('starts a new session from the task record when the session a round resumes is gone', async (t) => {
    const { tasks, executor } = setup(t);
    tasks.create({ rows: [row({ content: '实现 slugify' })], title: 'slugify', runtime: 'claude' });
    tasks.report('t1', { state: 'done', summary: 'S1', draft: 'D1' });
    executor.turns[0].finish({ text: '' });
    await settle();
    tasks.route('t1', [row({ content: '改个名' })], 'owner');
    const resumed = executor.turns[1];
    assert.equal(resumed.task.native_session, tasks.get('t1').session_id, 'it tries the session first');
    resumed.finish({ text: '', error: 'error_during_execution', sessionLost: true });
    await settle();
    assert.equal(executor.turns.length, 3, 'and runs again instead of failing');
    const fresh = executor.turns[2];
    assert.equal(fresh.task.native_session, null);
    assert.notEqual(fresh.task.session_id, resumed.task.session_id);
    assert.match(fresh.prompt, /^The previous worker session could not be safely resumed\. Check existing work before repeating any outside action\.\n\[task t1\] slugify\n/);
    assert.match(fresh.prompt, /Round 2 \(now\), from unknown:\n\[chat=oc_a msg=om_\d+ event=e\d+\] 改个名/);
    tasks.report('t1', { state: 'done', draft: '改好了' });
    fresh.finish({ text: '' });
    await settle();
    assert.equal(tasks.get('t1').status, 'done');
  });

  it('lets a report of an earlier round go out while the task works on the next', async (t) => {
    const { tasks, executor } = setup(t);
    tasks.create({ rows: [row()], title: 'X', runtime: 'pi' });
    tasks.report('t1', { state: 'done', draft: 'round 1' });
    executor.turns[0].finish({ text: '' });
    await settle();
    tasks.route('t1', [row()], 'owner');
    assert.deepEqual(tasks.working(['t1']), [], 'round 1 answers its own request');
    tasks.settleReports({ taskIds: ['t1'], by: 'reply_message' });
    assert.deepEqual(tasks.working(['t1']), ['t1'], 'with nothing of an earlier round left, a delivery waits for this round');
  });

  it('starts a separate result that follows a task from that work', async (t) => {
    const { tasks, executor } = setup(t);
    tasks.create({ rows: [row({ content: '查一下 X' })], title: '查 X', runtime: 'pi' });
    tasks.report('t1', { state: 'done', summary: 'X 慢在索引', draft: 'X 慢是因为缺索引', artifacts: ['notes.md'] });
    executor.turns[0].finish({ text: '' });
    await settle();
    tasks.create({ rows: [row({ content: '基于这个写份手册' })], title: '写手册', runtime: 'pi', parent: 't1' });
    assert.equal(tasks.get('t2').parent_id, 't1');
    const brief = executor.turns[1].prompt;
    assert.match(brief, /^\[task t2\] 写手册\n/);
    assert.match(brief, /This task follows t1 "查 X" \(done\), earlier work it builds on\. Its request:\n\| \[chat=oc_a msg=om_\d+ event=e\d+\] 查一下 X/);
    assert.match(brief, /What that work found:\n\| X 慢在索引\nIts last answer to the requester:\n\| X 慢是因为缺索引\nIts artifacts: notes\.md\nIts directory, with whatever it left there: .*tasks[\\/]t1\n/);
    assert.match(tasks.list()[0], /^t2 \[running\] "写手册" for unknown in chat=oc_a \(follows t1\) · working for 0m$/);
  });

  it('deletes what ended tasks left in stages: dependency folders, then the directory and its sessions, never what a task uses', async (t) => {
    const day = 86_400_000;
    let now = Date.parse('2026-11-01T00:00:00Z');
    const { tasks, executor, workspace, activities } = setup(t, { now: () => now });
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'tasks-data-'));
    t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
    tasks.config.dataDir = dataDir;
    const write = (file) => { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, 'x'); };
    const outside = path.join(dataDir, 'outside', 'keep.txt');
    write(outside);
    tasks.create({ rows: [row()], title: 'A', runtime: 'pi' });
    const dir = tasks.get('t1').workdir;
    const gone = [['repo', 'node_modules', 'x', 'index.js'], ['repo', 'env', 'lib', 'site.py'], ['rust', 'target', 'debug', 'bin']]
      .map((parts) => path.join(dir, ...parts));
    const kept = [['repo', 'src', 'a.js'], ['repo', 'target', 'notes.md'], ['repo', '.git', 'node_modules', 'pack'], ['rust', 'Cargo.toml'],
      ['repo', 'src', 'constructor', 'index.js'], ['repo', 'src', 'hasOwnProperty', 'index.js']].map((parts) => path.join(dir, ...parts));
    for (const file of [...gone, ...kept, path.join(dir, 'repo', 'env', 'pyvenv.cfg'), path.join(dataDir, 'workers', 't1', 'session.jsonl')]) write(file);
    directoryLink(path.dirname(outside), path.join(dir, 'repo', 'src', 'node_modules'));
    await tasks.sweepDirs();
    assert.ok(gone.every((file) => fs.existsSync(file)), 'nothing of a live task');
    tasks.report('t1', { state: 'done', draft: 'a' });
    executor.turns[0].finish({ text: '' });
    await settle();

    now += 2 * day;
    assert.deepEqual((await tasks.sweepDirs()).pruned, []);
    now += day;
    const pruned = await tasks.sweepDirs();
    assert.deepEqual([pruned.pruned, pruned.removed], [['t1'], []]);
    assert.deepEqual(gone.filter((file) => fs.existsSync(file)), []);
    assert.deepEqual(kept.filter((file) => !fs.existsSync(file)), [], 'sources, a target without its manifest, .git');
    assert.ok(fs.lstatSync(path.join(dir, 'repo', 'src', 'node_modules')).isSymbolicLink() && fs.existsSync(outside), 'a link is never followed');
    assert.equal(fs.existsSync(path.join(workspace, 'tasks', '.trash')), false, 'the trash is emptied');
    assert.equal(activities.at(-1).kind, 'task.dirs_cleaned');

    // A task that follows it is told its dependencies are gone, and nothing
    // is deleted from the directory its brief points to while it works.
    tasks.create({ rows: [row()], title: 'B', runtime: 'pi', parent: 't1' });
    assert.equal(tasks.get('t2').workdir, path.join(workspace, 'tasks', 't2'));
    assert.match(executor.turns[1].prompt, new RegExp(`\\nIts directory, with whatever it left there: ${escapeRegExp(dir)}\\nIts dependency folders \\(node_modules, virtual environments, build caches\\) were deleted to save disk: install again only what you need\\.\\n`));
    write(path.join(dataDir, 'workers', 't2', 'session.jsonl'));
    now += 30 * day;
    assert.deepEqual(await tasks.sweepDirs().then(({ pruned: a, removed: b }) => [a, b]), [[], []]);
    assert.ok(fs.existsSync(kept[0]));
    tasks.report('t2', { state: 'done', draft: 'b' });
    executor.turns[1].finish({ text: '' });
    await settle();

    const removed = await tasks.sweepDirs();
    assert.deepEqual(removed.removed, ['t1'], 'once nothing reads it');
    assert.equal(fs.existsSync(dir), false);
    assert.deepEqual(fs.readdirSync(path.join(dataDir, 'workers')), ['t2']);
    assert.ok(fs.existsSync(outside));
    assert.equal(tasks.get('t1').status, 'done', 'the task records stay');
    tasks.create({ rows: [row()], title: 'C', runtime: 'pi', parent: 't1' });
    assert.match(executor.turns[2].prompt, /\nIts directory was deleted to save disk some days after it ended: start from what it reported\.\n/);
    tasks.report('t3', { state: 'done', draft: 'c' });
    executor.turns[2].finish({ text: '' });
    await settle();
    now += 14 * day;
    assert.deepEqual((await tasks.sweepDirs()).removed.sort(), ['t2', 't3']);
    assert.deepEqual(fs.readdirSync(path.join(dataDir, 'workers')), []);
  });

  it('never deletes through a link: a trash that is one, or a folder that sits behind one since the scan', async (t) => {
    const { tasks, executor, workspace } = setup(t);
    const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'tasks-outside-'));
    t.after(() => fs.rmSync(outside, { recursive: true, force: true }));
    const precious = [path.join(outside, 'keep.txt'), path.join(outside, 'node_modules', 'dep.js')];
    for (const file of precious) { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, 'x'); }
    tasks.create({ rows: [row()], title: 'A', runtime: 'pi' });
    const dir = tasks.get('t1').workdir;
    fs.mkdirSync(path.join(dir, 'repo', 'node_modules'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'repo', 'node_modules', 'dep.js'), 'x');
    tasks.report('t1', { state: 'done', draft: 'a' });
    executor.turns[0].finish({ text: '' });
    await settle();
    // A scan found repo/node_modules; then repo became a link to a directory outside.
    const scan = await scanDir(dir);
    fs.rmSync(path.join(dir, 'repo'), { recursive: true });
    directoryLink(outside, path.join(dir, 'repo'));
    tasks.config.taskDirBudgetGb = 1e-12;
    const result = tasks.cleanDirs(new Map([[dir, scan]]));
    assert.deepEqual([result.pruned, result.removed], [[], ['t1']], 'the directory goes, with the link in it');
    // A trash that is a link: nothing is moved into it, and it goes as a link.
    const trash = path.join(workspace, 'tasks', '.trash');
    fs.rmSync(trash, { recursive: true, force: true });
    directoryLink(outside, trash);
    const kept = path.join(workspace, 'tasks', 'kept');
    fs.mkdirSync(kept);
    assert.throws(() => moveToTrash(kept, trash), /not a directory of its own/);
    assert.ok(fs.existsSync(kept));
    await tasks.sweepDirs();
    assert.equal(fs.existsSync(trash), false);
    assert.deepEqual(precious.filter((file) => !fs.existsSync(file)), []);
  });

  it('keeps the directories a stopped worker used until its process has exited', async (t) => {
    const { tasks, executor } = setup(t);
    const start = executor.start.bind(executor);
    executor.start = (args) => ({ ...start(args), stop: () => {} });
    tasks.create({ rows: [row()], title: 'A', runtime: 'pi' });
    tasks.create({ rows: [row()], title: 'B', runtime: 'pi', parent: 't1' });
    for (const id of ['t1', 't2']) fs.writeFileSync(path.join(tasks.get(id).workdir, 'notes.md'), id);
    tasks.report('t1', { state: 'done', draft: 'a' });
    executor.turns[0].finish({ text: '' });
    await settle();
    tasks.stop('t2', 'owner asked');
    assert.equal(tasks.get('t2').status, 'stopped');
    tasks.config.taskDirBudgetGb = 1e-12;
    assert.deepEqual((await tasks.sweepDirs()).removed, [], 'its own, and t1\'s it reads, while it runs');
    executor.turns[1].finish({ text: '', error: 'stopped' });
    await settle();
    assert.deepEqual((await tasks.sweepDirs()).removed.sort(), ['t1', 't2']);
  });

  it('past the disk budget deletes dependency folders first, then whole directories, the longest ended first', async (t) => {
    let now = Date.parse('2026-11-01T00:00:00Z');
    const { tasks, executor } = setup(t, { now: () => now });
    const KB = 1024;
    for (const [turn, id] of ['t1', 't2', 't3'].entries()) {
      tasks.create({ rows: [row()], title: id, runtime: 'pi' });
      const dir = tasks.get(id).workdir;
      fs.mkdirSync(path.join(dir, 'node_modules'));
      fs.writeFileSync(path.join(dir, 'node_modules', 'dep.js'), randomBytes(400 * KB));
      fs.writeFileSync(path.join(dir, 'main.js'), randomBytes(60 * KB));
      tasks.report(id, { state: 'done', draft: id });
      executor.turns[turn].finish({ text: '' });
      await settle();
      now += 60_000;
    }
    tasks.config.taskDirBudgetGb = (900 * KB) / 1024 ** 3;
    const first = await tasks.sweepDirs();
    assert.deepEqual([first.pruned, first.removed], [['t1', 't2'], []]);
    tasks.config.taskDirBudgetGb = (150 * KB) / 1024 ** 3;
    const second = await tasks.sweepDirs();
    assert.deepEqual([second.pruned, second.removed], [['t3'], ['t1']]);
    assert.ok(second.bytes <= 150 * KB && tasks.disk.bytes === second.bytes);
    tasks.config.taskDirBudgetGb = 0;
    assert.deepEqual(await tasks.sweepDirs().then(({ pruned, removed }) => [pruned, removed]), [[], []], '0 turns the budget off');
  });

  it('keeps a directory whose repository holds work that exists nowhere else past its age, and deletes it last past the budget',
    { skip: spawnSync('git', ['--version']).status !== 0 && 'no git' }, async (t) => {
      const day = 86_400_000;
      let now = Date.parse('2026-11-01T00:00:00Z');
      const warned = [];
      const logger = { info() {}, warn: (event, fields) => warned.push([event, fields]), error() {} };
      const { tasks, executor, workspace } = setup(t, { now: () => now, logger });
      const git = (cwd, ...args) => {
        const result = spawnSync('git', ['-c', 'user.name=t', '-c', 'user.email=t@example.com', '-c', 'init.defaultBranch=main', ...args],
          { cwd, encoding: 'utf8', env: { ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: process.platform === 'win32' ? 'NUL' : os.devNull } });
        assert.equal(result.status, 0, result.stderr);
      };
      const remote = path.join(workspace, 'remote.git');
      const seed = path.join(workspace, 'seed');
      git(workspace, 'init', '-q', '--bare', remote);
      git(workspace, 'clone', '-q', remote, seed);
      fs.writeFileSync(path.join(seed, 'a.js'), 'a');
      fs.writeFileSync(path.join(seed, '.gitignore'), 'node_modules/\n');
      git(seed, 'add', '.');
      git(seed, 'commit', '-qm', 'a');
      git(seed, 'push', '-q', 'origin', 'HEAD:main');
      // A commit of each task's own (the same content in the same second
      // would be the commit another task pushed).
      const commit = (repo) => {
        git(repo, 'checkout', '-qb', 'fix');
        fs.writeFileSync(path.join(repo, 'b.js'), repo);
        git(repo, 'add', '.');
        git(repo, 'commit', '-qm', 'b');
      };
      // What each task left in its clone.
      const leaves = [
        ['pushed', (repo) => { commit(repo); git(repo, 'push', '-q', 'origin', 'fix'); }],
        ['unpushed', commit],
        ['uncommitted', (repo) => fs.writeFileSync(path.join(repo, 'a.js'), 'changed')],
        ['untracked', (repo) => fs.writeFileSync(path.join(repo, 'notes.md'), 'n')],
      ];
      for (const [index, [title, leave]] of leaves.entries()) {
        tasks.create({ rows: [row()], title, runtime: 'pi' });
        const repo = path.join(tasks.get(`t${index + 1}`).workdir, 'repo');
        git(workspace, 'clone', '-q', '-b', 'main', remote, repo);
        fs.mkdirSync(path.join(repo, 'node_modules'));
        fs.writeFileSync(path.join(repo, 'node_modules', 'dep.js'), 'x');
        leave(repo);
        tasks.report(`t${index + 1}`, { state: 'blocked', summary: title });
        executor.turns[index].finish({ text: '' });
        await settle();
        now += 60_000;
      }
      now += 15 * day;
      const swept = await tasks.sweepDirs();
      assert.deepEqual([swept.removed, swept.kept, swept.pruned], [['t1'], ['t2', 't3', 't4'], ['t2', 't3', 't4']],
        'only what is pushed goes for its age; the others lose their dependency folders only');
      assert.deepEqual(warned, []);
      tasks.config.taskDirBudgetGb = 1e-12;
      assert.deepEqual((await tasks.sweepDirs()).removed, ['t2', 't3', 't4'], 'past the budget, the longest ended first');
      assert.deepEqual(warned.map(([event, fields]) => [event, path.basename(fields.dir), fields.repos.map((repo) => repo.reason)]),
        [['task_dir_removed_unsaved', 't2', ['unpushed']], ['task_dir_removed_unsaved', 't3', ['uncommitted']], ['task_dir_removed_unsaved', 't4', ['uncommitted']]]);

      // A worktree or submodule is a repository too; a repository git cannot
      // answer for may hold such work, a `.git` it does not take for one holds none.
      const worktree = path.join(workspace, 'wt');
      fs.mkdirSync(worktree);
      fs.writeFileSync(path.join(worktree, '.git'), 'gitdir: /elsewhere\n');
      assert.deepEqual((await scanDir(workspace)).repos.filter((repo) => repo === worktree), [worktree]);
      const failing = (stderr) => async () => { throw Object.assign(new Error('git failed'), { stderr }); };
      assert.deepEqual(await unsavedWork(['/r'], { git: failing('') }), [{ repo: '/r', reason: 'unknown' }]);
      assert.deepEqual(await unsavedWork(['/r'], { git: failing('fatal: not a git repository (or any of the parent directories): .git') }), []);
    });

  it('answers a progress question: what one person asked for, how long it has worked and what became of its result', async (t) => {
    const { tasks, executor, db } = setup(t);
    db.exec('ALTER TABLE outbox ADD COLUMN chat_id TEXT; ALTER TABLE outbox ADD COLUMN payload_json TEXT; ALTER TABLE outbox ADD COLUMN sent_at_ms INTEGER;');
    tasks.create({ rows: [row({ content: '查一下 X' })], title: '查 X', runtime: 'pi' });
    tasks.create({ rows: [row({ chat_id: 'oc_b', content: '查一下 Y' })], title: '查 Y', runtime: 'pi' });
    tasks.create({ rows: [row({ sender_id: 'ou_bob', content: '查一下 Z' })], title: '查 Z', runtime: 'pi' });
    const alice = (chatId, direct) => tasks.list({ requester: { id: 'ou_alice', chatId, direct } }).map((line) => line.split(' ')[0]);
    assert.deepEqual(alice('oc_a', false), ['t1'], 'in a group, only what was asked there');
    assert.deepEqual(alice('oc_dm', true), ['t2', 't1'], 'in a direct chat, all of theirs, newest first');
    assert.match(tasks.list({ taskId: 't1' })[0], /^t1 \[running\] "查 X" .* · working for 0m\n/);

    tasks.report('t1', { state: 'done', summary: 'X 慢在索引', draft: 'X 慢是因为缺索引' });
    executor.turns[0].finish({ text: '' });
    await settle();
    assert.match(tasks.list({ taskId: 't1' })[0], /^t1 \[done\] .* · result not sent yet\n[\s\S]*\ndraft never sent:\n\| X 慢是因为缺索引\n/);
    db.prepare('INSERT INTO outbox(id, remote_message_id, chat_id, payload_json, sent_at_ms) VALUES (7, ?, ?, ?, ?)')
      .run('om_reply1', 'oc_a', JSON.stringify({ message_id: 'om_1', content: { type: 'markdown', text: 'X 慢是因为缺索引' } }), Date.now());
    assert.equal(tasks.settleReports({ taskIds: ['t1'], by: 'reply_message', outboxId: 7 }), 1);
    const detail = tasks.list({ taskId: 't1' })[0];
    assert.match(detail, /^t1 \[done\] .* · result sent 0m ago as msg=om_reply1\n/);
    assert.match(detail, /\nsent \d{4}-\d\d-\d\dT\d\d:\d\dZ as msg=om_reply1: X 慢是因为缺索引\n/);
    assert.doesNotMatch(detail, /draft never sent/);

    tasks.report('t2', { state: 'needs_user', question: '看哪个环境？' });
    executor.turns[1].finish({ text: '' });
    await settle();
    tasks.settleReports({ taskIds: ['t2'], by: 'stay_silent' });
    assert.match(tasks.list({ taskId: 't2' })[0], /^t2 \[waiting\] .* · question not sent: you stayed silent on it\n/);

    tasks.report('t3', { state: 'failed', summary: '没有权限', draft: '没有 Z 的权限' });
    executor.turns[2].finish({ text: '' });
    await settle();
    const receipt = `task-report:${db.prepare("SELECT id FROM task_reports WHERE task_id = 't3'").get().id}`;
    tasks.routerTurnEnded({ receiptIds: [receipt] });
    tasks.routerTurnEnded({ receiptIds: [receipt] });
    assert.match(tasks.list({ taskId: 't3' })[0], /^t3 \[failed\] .* · result never sent\n[\s\S]*draft never sent:\n\| 没有 Z 的权限/);
  });

  it('starts a task that follows another from that task\'s session, on the same runtime only', async (t) => {
    const { tasks, executor } = setup(t);
    const seen = [];
    const start = executor.start.bind(executor);
    executor.start = (args) => { seen.push(args.fork); return start(args); };
    tasks.create({ rows: [row()], title: 'A', runtime: 'pi' });
    executor.turns[0].finish({ text: 'a' });
    await settle();
    const parent = tasks.get('t1');
    tasks.create({ rows: [row()], title: 'B', runtime: 'pi', parent: 't1' });
    tasks.create({ rows: [row()], title: 'C', runtime: 'codex', parent: 't1' });
    assert.deepEqual(seen, [null, { taskId: 't1', runtime: 'pi', sessionId: parent.native_session }, null]);
    tasks.route('t2', [row({ content: '只看上周' })], 'owner');
    executor.turns[1].finish({ text: 'b' });
    await settle();
    assert.equal(executor.turns.at(-1).task.id, 't2', 'a catch-up turn');
    assert.equal(seen.at(-1), null, 'only the first turn forks');
  });

  it('keeps one slot for follow-ups: a task that ran, or one that follows another', async (t) => {
    const { tasks, executor } = setup(t, { slots: 1, reservedSlots: 1 });
    tasks.create({ rows: [row()], title: 'A', runtime: 'pi' });
    executor.turns[0].finish({ text: 'a' });
    await settle();
    tasks.create({ rows: [row()], title: 'B', runtime: 'pi' });
    tasks.create({ rows: [row()], title: 'C', runtime: 'pi' });
    assert.deepEqual(executor.turns.map((turn) => turn.task.id), ['t1', 't2'], 'C waits for a normal slot');
    tasks.create({ rows: [row({ content: 'follow-up' })], title: 'A2', runtime: 'pi', parent: 't1' });
    assert.deepEqual(executor.turns.map((turn) => turn.task.id), ['t1', 't2', 't4'], 'the follow-up takes the reserved slot');
    executor.turns[1].finish({ text: 'b' });
    await settle();
    assert.equal(executor.turns.at(-1).task.id, 't3');
  });

  it('lists the tasks near an input with what each is doing, and lets only the owner point across chats', async (t) => {
    const { tasks, ingest, executor } = setup(t);
    const origin = row({ thread_id: 'omt_9', sender_id: 'ou_alice', content: '排查飞书名字泄露' });
    ingest(origin);
    tasks.create({ rows: [origin], title: '排查飞书名字泄露问题', requester: 'Alice', runtime: 'pi' });
    tasks.report('t1', { state: 'needs_user', question: '要不要提 issue？' });
    executor.turns[0].finish({ text: '' });
    await settle();
    const inThread = { chatId: 'oc_a', threadId: 'omt_9', senderId: 'ou_alice', scopeKey: origin.scope_key, content: '要' };
    assert.equal(tasks.hintFor(inThread, {}), '[daemon tasks] near this input: t1 [waiting for Alice for 0m] "排查飞书名字泄露问题" now on: "排查飞书名字泄露" (same thread, waiting for this person)');
    const reply = { chatId: 'oc_a', threadId: null, senderId: 'ou_bob', scopeKey: 'lark:oc_a:', content: 'ok', parentId: origin.message_id };
    assert.match(tasks.hintFor(reply, {}), /t1 \[waiting for Alice for 0m\] .*\(replies to its message\)/);
    // In the thread the request opened, every reply's parent is that request:
    // it is the same thread, not a reply to the task's message.
    const inTheRequestsThread = { ...inThread, senderId: 'ou_bob', parentId: origin.message_id, threadRootId: origin.message_id };
    assert.match(tasks.hintFor(inTheRequestsThread, {}), /t1 \[waiting for Alice for 0m\] .*\(same thread\)$/);
    const sameChat = { chatId: 'oc_a', threadId: null, senderId: 'ou_alice', scopeKey: 'lark:oc_a:', content: '飞书名字泄露那个怎么样了' };
    assert.equal(tasks.hintFor(sameChat, {}), '', 'similar words alone point nowhere');
    const elsewhere = { chatId: 'oc_b', senderId: 'ou_alice', scopeKey: 'lark:oc_b:', content: '飞书名字泄露那个怎么样了' };
    assert.equal(tasks.hintFor(elsewhere, {}), '');
    assert.match(tasks.hintFor(elsewhere, { owner: true }), /t1 .*similar words/);
    tasks.report('t1', { state: 'done', draft: 'ok' });
    tasks.setStatus('t1', 'done');
    assert.match(tasks.hintFor(inThread, {}), /t1 \[done 0m ago\] "排查飞书名字泄露问题" last asked: "排查飞书名字泄露" \(same thread\)/);
  });

  it('takes a question nobody answered for days out of the working set, and finds it again by a reply, its id or its answer', async (t) => {
    const day = 86_400_000;
    let now = Date.parse('2026-11-06T10:00:00Z');
    const { tasks, executor, ingest } = setup(t, { now: () => now });
    // On a Friday Alice asks in a direct chat, and the worker asks her back.
    const dm = { chat_id: 'oc_dm', chat_type: 'p2p', scope_key: 'lark:oc_dm:' };
    const origin = row({ ...dm, content: '帮我把飞书账号改名' });
    ingest(origin);
    tasks.create({ rows: [origin], title: '飞书账号改名', requester: 'Alice', runtime: 'pi' });
    tasks.report('t1', { state: 'needs_user', question: '是哪个账号？' });
    executor.turns[0].finish({ text: '' });
    await settle();
    const fromAlice = (content, fields = {}) => ({ chatId: 'oc_dm', threadId: null, senderId: 'ou_alice', scopeKey: 'lark:oc_dm:', content, ...fields });
    now += 3 * day;
    assert.match(tasks.hintFor(fromAlice('工作号那个')), /t1 \[waiting for Alice for 3d\] "飞书账号改名" .*\(waiting for this person\)$/, 'on Monday it is still near');

    now += 2 * day + 60_000;
    assert.equal(tasks.isDormant(tasks.get('t1')), true);
    assert.deepEqual(tasks.workingSet().map((task) => task.id), []);
    assert.equal(tasks.hintFor(fromAlice('工作号那个')), '', 'not on every word she says in the chat');
    assert.equal(tasks.hintFor({ ...fromAlice('飞书账号改名那个呢'), chatId: 'oc_b', scopeKey: 'lark:oc_b:' }, { owner: true }), '', 'nor on similar words elsewhere');
    assert.match(tasks.hintFor(fromAlice('工作号那个', { parentId: origin.message_id })),
      /t1 \[waiting for Alice for 5d\] .*\(replies to its message, waiting for this person\)$/);
    assert.deepEqual(tasks.overview().live.map((task) => [task.id, task.dormant]), [['t1', true]], 'still live');
    assert.equal(tasks.heldDirs().has(tasks.get('t1').workdir), true, 'its directory stays');
    assert.deepEqual(tasks.list(), ['Also waiting, with nothing for days: t1 (5d). task_id= shows one.']);
    assert.match(tasks.list({ query: '飞书账号改名' })[0], /^t1 \[waiting\] "飞书账号改名"/, 'a search looks past the working set');
    tasks.config.taskDormantDays = 0;
    assert.deepEqual(tasks.workingSet().map((task) => task.id), ['t1'], '0 turns it off');
    delete tasks.config.taskDormantDays;

    // Her answer goes on in the same round, and the task is back.
    assert.equal(tasks.route('t1', [row({ ...dm, content: '工作号那个' })], 'owner').status, 'running');
    assert.equal(tasks.get('t1').round, 1);
    assert.deepEqual(tasks.workingSet().map((task) => task.id), ['t1']);
  });

  it('ranks tasks of equal weight by when they last moved, not when they were created', async (t) => {
    const day = 86_400_000;
    let now = Date.parse('2026-11-01T00:00:00Z');
    const { tasks, executor } = setup(t, { now: () => now });
    for (const [index, state] of ['needs_user', 'done'].entries()) {
      tasks.create({ rows: [row({ chat_id: `oc_${index}`, scope_key: `lark:oc_${index}:` })], title: '飞书账号改名', runtime: 'pi' });
      tasks.report(`t${index + 1}`, { state, question: state === 'needs_user' ? '哪个账号？' : '', draft: 'x' });
      executor.turns[index].finish({ text: '' });
      await settle();
      now += 2 * day;
    }
    const hint = tasks.hintFor({ chatId: 'oc_x', senderId: 'ou_owner', scopeKey: 'lark:oc_x:', content: '飞书账号改名' }, { owner: true });
    assert.match(hint, /near this input: t2 \[done 2d ago\] [^·]*· t1 \[waiting for the requester for 4d\] /);
  });

  it('knows a reply to the message that relayed a task\'s result, whatever input the router decided it on', async (t) => {
    const { tasks, ingest, executor, db } = setup(t);
    const origin = row({ content: '帮我查下 X 为什么慢' });
    ingest(origin);
    tasks.create({ rows: [origin], title: '查 X 变慢', runtime: 'pi' });
    const later = row({ content: '只看上周的' });
    ingest(later);
    tasks.route('t1', [later], 'owner');
    tasks.check('t1');
    tasks.report('t1', { state: 'done', draft: '慢在缺索引' });
    executor.turns[0].finish({ text: '' });
    await settle();
    // The router relayed it to the origin message, deciding on the newest input there.
    db.prepare('INSERT INTO outbox(id, inbound_seq, remote_message_id) VALUES (1, ?, ?)').run(later.seq, 'om_bot_result');
    const reply = { chatId: 'oc_a', threadId: null, senderId: 'ou_alice', scopeKey: 'lark:oc_a:', content: '那按这个结论提个 PR', parentId: 'om_bot_result' };
    assert.equal(tasks.hintFor(reply, {}), '', 'not before the delivery names it');
    tasks.settleReports({ taskIds: ['t1'], by: 'reply_message', outboxId: 1 });
    assert.equal(tasks.hintFor(reply, {}), '[daemon tasks] near this input: t1 [done 0m ago] "查 X 变慢" last asked: "帮我查下 X 为什么慢" (replies to its message)');
  });

  it('finds a task an exact signal names past the working set: its thread, or a reply to the message that relayed its result', async (t) => {
    let now = Date.parse('2026-11-01T00:00:00Z');
    const { tasks, ingest, executor, db } = setup(t, { now: () => now });
    const old = row({ thread_id: 'omt_old', content: '实现 slugify 并开个 PR' });
    ingest(old);
    tasks.create({ rows: [old], title: 'slugify PR', runtime: 'pi' });
    tasks.report('t1', { state: 'done', draft: 'PR 已开' });
    executor.turns[0].finish({ text: '' });
    await settle();
    db.prepare('INSERT INTO outbox(id, inbound_seq, remote_message_id) VALUES (7, ?, ?)').run(old.seq, 'om_bot_old');
    tasks.settleReports({ taskIds: ['t1'], by: 'reply_message', outboxId: 7 });
    // Forty tasks end after it: it is long out of the working set.
    for (let index = 0; index < 40; index += 1) {
      now += 60_000;
      const id = tasks.create({ rows: [row({ thread_id: `omt_${index}` })], title: `other ${index}`, runtime: 'pi' }).id;
      tasks.report(id, { state: 'done', draft: 'ok' });
      executor.turns.at(-1).finish({ text: '' });
      await settle();
    }
    assert.ok(!tasks.workingSet().some((task) => task.id === 't1'));
    const inThread = { chatId: 'oc_a', threadId: 'omt_old', senderId: 'ou_alice', scopeKey: old.scope_key, content: 'CI 又挂了' };
    assert.equal(tasks.hintFor(inThread, {}), '[daemon tasks] near this input: t1 [done 40m ago] "slugify PR" last asked: "实现 slugify 并开个 PR" (same thread)');
    const reply = { chatId: 'oc_a', threadId: null, senderId: 'ou_alice', scopeKey: 'lark:oc_a:', content: '那再补个测试', parentId: 'om_bot_old' };
    assert.match(tasks.hintFor(reply, {}), /^\[daemon tasks\] near this input: t1 \[done 40m ago\] "slugify PR" .*\(replies to its message\)$/);
    assert.equal(tasks.hintFor({ ...inThread, chatId: 'oc_b' }, {}), '', 'the same thread id in another chat is another thread');
  });

  it('settles a report when the router answers for its task, and offers one it was shown and left again', async (t) => {
    const { tasks, executor, relayed, db, activities } = setup(t);
    const origin = row({ content: '查 X' });
    tasks.create({ rows: [origin], title: 'X', runtime: 'pi' });
    const later = row({ content: '只看上周' });
    tasks.report('t1', { state: 'done', draft: 'X 的原因是 A' });
    executor.turns[0].finish({ text: '' });
    await settle();
    const receipt = relayed[0].item.receiptId;
    assert.equal(receipt, 'task-report:1');
    const delivery = () => db.prepare('SELECT inbox_id, settled_at_ms, offers FROM task_report_deliveries').get();
    // A router turn held it and finished without answering for t1: once more, marked.
    db.prepare('DELETE FROM inbox_pending').run();
    assert.equal(tasks.settleReports({ seqs: [later.seq] }), 0, 'an answer to some other line settles nothing');
    assert.equal(tasks.routerTurnEnded({ receiptIds: [receipt], ok: true }), 1);
    assert.equal(relayed.length, 2);
    assert.match(relayed[1].item.text, /^\[task report\] task=t1 state=done title="X" requester=unknown undelivered=1\n.*\nYou were shown this report before and have not answered for t1: relay it now with task_ids=\["t1"\]/);
    db.prepare('DELETE FROM inbox_pending').run();
    assert.equal(tasks.routerTurnEnded({ receiptIds: [receipt], ok: true }), 0, 'not a third time');
    assert.ok(delivery().settled_at_ms);
    assert.equal(activities.at(-1).kind, 'task.report_unrelayed');
    assert.match(tasks.card(tasks.get('t1'), { detail: true }), /draft never sent/);
    // It goes out later after all: a delivery naming t1 settles it as sent,
    // one that does not name it leaves it, and nothing is offered again.
    const offeredBefore = relayed.length;
    assert.equal(tasks.settleReports({ seqs: [origin.seq], by: 'reply_message' }), 0);
    db.prepare('INSERT INTO outbox(id, inbound_seq, remote_message_id) VALUES (9, ?, ?)').run(origin.seq, 'om_bot_late');
    assert.equal(tasks.settleReports({ taskIds: ['t1'], seqs: [origin.seq], by: 'reply_message', outboxId: 9 }), 1);
    assert.equal(tasks.delivery(tasks.get('t1')).state, 'sent');
    assert.doesNotMatch(tasks.card(tasks.get('t1'), { detail: true }), /never sent/);
    assert.equal(tasks.settleReports({ taskIds: ['t1'], seqs: [origin.seq], by: 'reply_message', outboxId: 10 }), 0, 'sent stays sent');
    assert.equal(db.prepare('SELECT outbox_id FROM task_report_deliveries WHERE report_id = 1').get().outbox_id, 9);
    assert.equal(relayed.length, offeredBefore);
    // Another round's unrelayed report is not the one a delivery for round 1 answered.
    const roundTwo = db.prepare("INSERT INTO task_reports(task_id, round, state, structured, created_at_ms) VALUES ('t1', 2, 'done', 1, ?)").run(Date.now()).lastInsertRowid;
    db.prepare("INSERT INTO task_report_deliveries(report_id, inbox_id, created_at_ms, settled_at_ms, settled_by) VALUES (?, 1, ?, ?, 'unrelayed')").run(roundTwo, Date.now(), Date.now());
    assert.equal(tasks.settleReports({ taskIds: ['t1'], seqs: [origin.seq], by: 'reply_message', outboxId: 11 }), 0);
    assert.equal(db.prepare('SELECT settled_by FROM task_report_deliveries WHERE report_id = ?').get(roundTwo).settled_by, 'unrelayed');

    // A failed router turn: offered again as it was, until the router answers.
    tasks.create({ rows: [row({ content: '查 Y' })], title: 'Y', runtime: 'pi' });
    tasks.report('t2', { state: 'done', draft: 'Y 的原因是 B' });
    executor.turns[1].finish({ text: '' });
    await settle();
    const second = relayed.at(-1).item.receiptId;
    db.prepare('DELETE FROM inbox_pending').run();
    assert.equal(tasks.routerTurnEnded({ receiptIds: [second], ok: false }), 1);
    db.prepare('DELETE FROM inbox_pending').run();
    const t2 = tasks.get('t2');
    assert.equal(tasks.settleReports({ seqs: [t2.origin_seq] }), 1, 'a delivery on the origin settles it');
    assert.equal(tasks.routerTurnEnded({ receiptIds: [second], ok: false }), 0, 'nothing to offer after the answer');
  });

  it('hands back the follow-ups a task never saw when it fails or is stopped, never its request or what a turn took', async (t) => {
    const { tasks, executor, relayed } = setup(t);
    tasks.create({ rows: [row({ content: '查 X' })], title: 'X', runtime: 'pi' });
    const seenLine = row({ content: '只看上周' });
    tasks.route('t1', [seenLine], 'owner');
    assert.equal(tasks.check('t1').length, 1, 'the worker took it');
    const unseenLine = row({ content: '另外帮我订个会议室' });
    tasks.route('t1', [unseenLine], 'owner');
    executor.turns[0].finish({ text: '', error: 'exit 1: boom' });
    await settle();
    assert.equal(tasks.get('t1').status, 'failed');
    const texts = relayed.map((entry) => entry.item.text);
    assert.match(texts[0], /^\[task report\] task=t1 state=failed/);
    assert.match(texts[1], /^\[task handback\] task=t1 title="X" reason="the task ended \(failed\) before its worker saw them"/);
    assert.match(texts[1], /另外帮我订个会议室$/);
    assert.doesNotMatch(texts[1], /查 X|只看上周/);
    assert.equal(relayed[1].meta.kind, 'task.handback');

    tasks.create({ rows: [row({ content: '查 Z' })], title: 'Z', runtime: 'pi' });
    tasks.route('t2', [row({ content: '顺便看下 W' })], 'owner');
    tasks.stop('t2', 'Alice said stop');
    assert.match(relayed.at(-1).item.text, /^\[task handback\] task=t2 .*stopped before its worker saw them"[\s\S]*顺便看下 W$/);
  });

  it('tells a worker catching up that its last report was never sent', async (t) => {
    const { tasks, executor, relayed } = setup(t);
    tasks.create({ rows: [row({ content: '查 X' })], title: 'X', runtime: 'pi' });
    tasks.route('t1', [row({ content: '只看上周' })], 'owner');
    tasks.report('t1', { state: 'done', draft: '全量的结论' });
    executor.turns[0].finish({ text: '' });
    await settle();
    assert.equal(relayed.length, 0);
    assert.match(executor.turns[1].prompt, /New input from the requester's side:\n.*只看上周\nYour previous report was not sent: the requester has not seen it\. Your next report replaces it, so it must stand on its own/);
    tasks.report('t1', { state: 'done', draft: '上周的结论' });
    executor.turns[1].finish({ text: '' });
    await settle();
    assert.equal(relayed.length, 1);
    assert.match(relayed[0].item.text, /上周的结论/);
    assert.doesNotMatch(relayed[0].item.text, /全量的结论/);
  });

  it('past the catch-up turns relays the result so far, deliverable while its worker goes on', async (t) => {
    const { tasks, executor, relayed, db } = setup(t);
    tasks.create({ rows: [row({ content: '写周报' })], title: '周报', runtime: 'pi' });
    // The requester adds a line during every turn: three catch-up turns, then
    // the fourth report goes out anyway.
    for (const [index, line] of ['加上项目 B', '再加个图表', '标题改一下', '检查下错别字'].entries()) {
      tasks.route('t1', [row({ content: line })], 'owner');
      tasks.report('t1', { state: 'done', draft: `第 ${index + 1} 版` });
      executor.turns[index].finish({ text: '' });
      await settle();
    }
    assert.equal(relayed.length, 1);
    assert.match(relayed[0].item.text, /^\[task report\] task=t1 state=done title="周报" requester=unknown working=1\n.*\nThe requester kept adding to t1 while its worker wrote this, and the worker is on their latest input now: relay this as the result so far with task_ids=\["t1"\]/);
    assert.match(relayed[0].item.text, /\| 第 4 版/);
    assert.equal(executor.turns.length, 5, 'the worker goes on with the last line');
    assert.match(executor.turns[4].prompt, /检查下错别字/);
    assert.equal(tasks.get('t1').status, 'running');
    assert.deepEqual(tasks.working(['t1']), [], 'the router may deliver this report while the task works');
    assert.equal(tasks.settleReports({ taskIds: ['t1'], by: 'reply_message' }), 1);
    assert.deepEqual(tasks.working(['t1']), ['t1'], 'once it is settled, a delivery naming the task waits for the next report');

    tasks.report('t1', { state: 'done', draft: '第 5 版' });
    executor.turns[4].finish({ text: '' });
    await settle();
    assert.equal(relayed.length, 2);
    assert.match(relayed[1].item.text, /^\[task report\] task=t1 state=done title="周报" requester=unknown\n/);
    assert.equal(tasks.get('t1').status, 'done');
    assert.deepEqual(db.prepare('SELECT while_working FROM task_report_deliveries ORDER BY report_id').all().map((r) => r.while_working), [1, 0]);
  });

  it('recovers the latest input if interrupted while publishing a report at the catch-up cap', async (t) => {
    const { tasks, executor, db, workspace, inbox, relayed } = setup(t);
    tasks.create({ rows: [row({ content: 'write the report' })], title: 'X', runtime: 'pi' });
    for (let index = 0; index < 4; index += 1) {
      tasks.route('t1', [row({ content: `addition ${index + 1}` })], 'owner');
      tasks.report('t1', { state: 'done', draft: `version ${index + 1}` });
      if (index === 3) {
        tasks.flushReports = () => {
          tasks.shutdown();
          throw new Error('interrupted before inbox publication');
        };
      }
      executor.turns[index].finish({ text: '' });
      await settle();
    }
    assert.equal(executor.turns.length, 4);
    assert.equal(relayed.length, 0);

    const next = fakeExecutor();
    const restored = new TaskManager({ db, config: { workspace }, executor: next, inbox, lineFor: () => '' });
    t.after(() => restored.shutdown());
    restored.restore();
    assert.equal(relayed.length, 1, 'the saved result is offered after restart');
    assert.match(relayed[0].item.text, /working=1/);
    assert.equal(next.turns.length, 1, 'the pending addition is resumed after restart');
    assert.match(next.turns[0].prompt, /addition 4/);
    restored.report('t1', { state: 'done', draft: 'all additions included' });
    next.turns[0].finish({ text: '' });
    await settle();
    assert.equal(restored.get('t1').status, 'done');
    assert.equal(restored.undelivered('t1').length, 0);
  });

  it('starts the turns a previous daemon was running again, with a recovery note', async (t) => {
    const { tasks, executor, db, workspace, inbox } = setup(t);
    tasks.create({ rows: [row({ content: 'original request: never publish' })], title: 'X', runtime: 'pi' });
    tasks.shutdown();
    executor.turns[0].finish({ text: '', error: 'stopped' });
    await settle();
    assert.equal(tasks.get('t1').status, 'running');

    const next = fakeExecutor();
    const restored = new TaskManager({ db, config: { workspace }, executor: next, inbox, lineFor: () => '' });
    t.after(() => restored.shutdown());
    restored.restore();
    assert.equal(next.turns.length, 1);
    assert.match(next.turns[0].prompt, /^The daemon restarted while you were working on this task\./);
    assert.match(next.turns[0].prompt, /original request: never publish/);
    assert.equal(next.turns[0].task.native_session, null);
    assert.notEqual(next.turns[0].task.session_id, executor.turns[0].task.session_id);
  });

  it('runs a turn that failed for a passing reason again', async (t) => {
    const { tasks, executor, relayed } = setup(t);
    tasks.create({ rows: [row({ content: 'original request: never publish' })], title: 'X', note: 'keep the evidence', runtime: 'pi' });
    executor.turns[0].finish({ text: '', error: 'HTTP 429', transient: true });
    await settle();
    assert.equal(executor.turns.length, 2);
    assert.equal(relayed.length, 0);
    assert.match(executor.turns[1].prompt, /original request: never publish/);
    assert.match(executor.turns[1].prompt, /keep the evidence/);
    assert.equal(executor.turns[1].task.native_session, null);
  });

  it('keeps a final report its worker filed before a passing failure, and does not run the turn again', async (t) => {
    const { tasks, executor, relayed, db } = setup(t);
    tasks.create({ rows: [row({ content: '修一下这个 bug 并提 PR' })], title: 'X', runtime: 'pi' });
    tasks.report('t1', { state: 'progress', summary: '定位到了' });
    tasks.report('t1', { state: 'done', draft: 'PR #42 已开好', artifacts: ['https://example.com/pr/42'] });
    // Its closing words hit a rate limit after the report.
    executor.turns[0].finish({ text: '', error: 'exit 1: 529 overloaded', transient: true });
    await settle();
    assert.equal(executor.turns.length, 1, 'no retry: it would open the PR again');
    assert.equal(relayed.length, 1);
    assert.match(relayed[0].item.text, /^\[task report\] task=t1 state=done title="X" requester=unknown\n[\s\S]*\| PR #42 已开好/);
    assert.equal(tasks.get('t1').status, 'done');
    assert.equal(db.prepare('SELECT COUNT(*) n FROM task_inputs WHERE delivered_at_ms IS NULL').get().n, 0);
    assert.equal(db.prepare('SELECT COUNT(*) n FROM task_reports WHERE task_id = ?').get('t1').n, 2, 'no failure report added');
  });

  it('after a passing failure past its report, catches up only on what the worker took after that report', async (t) => {
    const { tasks, executor, relayed, db } = setup(t);
    tasks.create({ rows: [row({ content: '查 X' })], title: 'X', runtime: 'pi' });
    tasks.route('t1', [row({ content: '只看上周' })], 'owner');
    assert.equal(tasks.check('t1').length, 1);
    tasks.report('t1', { state: 'done', draft: '上周的结论' });
    // A hook hands it one more line after the report; then the model call fails.
    tasks.route('t1', [row({ content: '也看下前一周' })], 'owner');
    assert.equal(tasks.check('t1').length, 1);
    executor.turns[0].finish({ text: '', error: 'HTTP 429', transient: true });
    await settle();
    const pending = db.prepare('SELECT text FROM task_inputs WHERE delivered_at_ms IS NULL').all().map((input) => input.text);
    assert.equal(pending.length, 1);
    assert.match(pending[0], /也看下前一周/);
    assert.equal(relayed.length, 0, 'held for the line it never acted on');
    assert.equal(executor.turns.length, 2);
    assert.match(executor.turns[1].prompt, /^The previous worker session could not be safely resumed\./);
    assert.match(executor.turns[1].prompt, /也看下前一周/);
    assert.match(executor.turns[1].prompt, /Previous draft \(may be incomplete\):\n\| 上周的结论/);
    tasks.report('t1', { state: 'done', draft: '两周的结论' });
    executor.turns[1].finish({ text: '' });
    await settle();
    assert.equal(relayed.length, 1);
    assert.match(relayed[0].item.text, /\| 两周的结论/);
  });

  it('acknowledges inputs only after success, including inputs read through task_check', async (t) => {
    const { tasks, executor, db } = setup(t);
    tasks.create({ rows: [row({ content: 'original constraints' })], title: 'X', runtime: 'pi' });
    tasks.route('t1', [row({ content: 'do not publish' })], 'owner');
    assert.equal(tasks.check('t1').length, 1);
    assert.deepEqual(tasks.check('t1'), [], 'a claimed input is not repeatedly returned in the same turn');
    assert.equal(db.prepare('SELECT COUNT(*) n FROM task_inputs WHERE delivered_at_ms IS NOT NULL').get().n, 0);
    executor.turns[0].finish({ error: 'HTTP 429', transient: true });
    await settle();
    assert.match(executor.turns[1].prompt, /original constraints/);
    assert.match(executor.turns[1].prompt, /do not publish/);
    executor.turns[1].finish({ text: 'done' });
    await settle();
    assert.equal(db.prepare('SELECT COUNT(*) n FROM task_inputs WHERE delivered_at_ms IS NOT NULL').get().n, 2);
    assert.equal(db.prepare('SELECT COUNT(*) n FROM task_input_claims').get().n, 0);
  });

  it('keeps retries bounded even though failed inputs remain unacknowledged', async (t) => {
    const { tasks, executor, relayed } = setup(t);
    tasks.create({ rows: [row()], title: 'X', runtime: 'pi' });
    for (let index = 0; index < 3; index += 1) {
      executor.turns[index].finish({ error: 'HTTP 429', transient: true });
      await settle();
    }
    assert.equal(executor.turns.length, 3);
    assert.equal(tasks.get('t1').status, 'failed');
    assert.equal(relayed.length, 1);
  });

  it('retries an inbox insert failure automatically, without duplicate delivery after consumption', async (t) => {
    const { tasks, executor, db, inbox, relayed } = setup(t);
    db.exec("CREATE TRIGGER fail_inbox BEFORE INSERT ON inbox_pending BEGIN SELECT RAISE(ABORT, 'injected write failure'); END;");
    tasks.create({ rows: [row()], title: 'X', runtime: 'pi' });
    tasks.report('t1', { state: 'done', draft: 'complete result' });
    executor.turns[0].finish({ text: '' });
    await settle();
    assert.equal(tasks.get('t1').status, 'done');
    assert.equal(db.prepare('SELECT relayed_at_ms FROM task_reports').get().relayed_at_ms, null);
    assert.equal(db.prepare('SELECT inbox_id FROM task_report_deliveries').get().inbox_id, null);
    assert.equal(inbox.size, 0);
    db.exec('DROP TRIGGER fail_inbox');
    await new Promise((resolve) => setTimeout(resolve, 1_100));
    assert.equal(inbox.size, 1);
    assert.equal(relayed.length, 1);
    assert.match(relayed[0].item.text, /complete result/);
    // A router turn takes it from the inbox; until the router answers for t1,
    // a restart offers it again, marked; once it has, nothing is offered.
    inbox.remove([...inbox.entries]);
    tasks.flushReports();
    tasks.restore();
    assert.equal(relayed.length, 2);
    assert.match(relayed[1].item.text, /^\[task report\] task=t1 state=done .* undelivered=1\n/);
    inbox.remove([...inbox.entries]);
    assert.equal(tasks.settleReports({ taskIds: ['t1'] }), 1);
    tasks.restore();
    assert.equal(relayed.length, 2);
    assert.equal(db.prepare('SELECT COUNT(*) n FROM inbox_pending').get().n, 0);
    assert.equal(executor.turns.length, 1);
  });

  it('keeps a report pending when the inbox is stopped and recovers it on restart', async (t) => {
    const { tasks, executor, db, workspace, inbox } = setup(t);
    tasks.create({ rows: [row()], title: 'X', runtime: 'pi' });
    inbox.stop();
    executor.turns[0].finish({ text: 'complete result' });
    await settle();
    assert.equal(db.prepare('SELECT relayed_at_ms FROM task_reports').get().relayed_at_ms, null);
    tasks.shutdown();
    const nextInbox = new MessageInbox({ db, runtime: { snapshot: () => ({ work: { turnInProgress: true } }) } });
    const next = fakeExecutor();
    const restored = new TaskManager({ db, config: { workspace }, executor: next, inbox: nextInbox, lineFor: () => '' });
    t.after(() => { restored.shutdown(); nextInbox.stop(); });
    restored.restore();
    assert.equal(nextInbox.size, 1);
    assert.equal(next.turns.length, 0);
  });

  it('relays the complete allowed draft, preserving its tail and quoting every line', async (t) => {
    const { tasks, executor, relayed } = setup(t);
    const tail = '\nESSENTIAL_END_OF_REPORT';
    const draft = 'x'.repeat(20_000 - tail.length) + tail;
    assert.equal(draft.length, 20_000);
    tasks.create({ rows: [row()], title: 'X', runtime: 'pi' });
    tasks.report('t1', { state: 'done', draft });
    executor.turns[0].finish({ text: '' });
    await settle();
    assert.ok(relayed[0].item.text.endsWith(`draft:\n${draft.split('\n').map((line) => `| ${line}`).join('\n')}`));
  });

  it('recovers an old unmarked final report, excluding progress and superseded reports', (t) => {
    const { tasks, executor, relayed } = setup(t);
    tasks.create({ rows: [row()], title: 'A', runtime: 'pi' });
    tasks.report('t1', { state: 'done', draft: 'superseded result' });
    tasks.report('t1', { state: 'done', draft: 'latest result' });
    tasks.setStatus('t1', 'done');
    tasks.create({ rows: [row()], title: 'B', runtime: 'pi' });
    tasks.report('t2', { state: 'progress', summary: 'still working' });
    tasks.setStatus('t2', 'blocked');
    tasks.restore();
    assert.equal(relayed.length, 1);
    assert.match(relayed[0].item.text, /latest result/);
    assert.doesNotMatch(relayed[0].item.text, /superseded result/);
    assert.equal(executor.turns.length, 2);
  });

  for (const stage of ['start', 'check', 'ended', 'insert', 'commit', 'publish']) {
    it(`recovers after SIGKILL at ${stage}`, { skip: process.platform === 'win32' }, (t) => {
      const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'task-crash-'));
      const child = spawnSync(process.execPath, [fileURLToPath(new URL('./task-crash-fixture.mjs', import.meta.url)), stage, workspace],
        { encoding: 'utf8', timeout: 15_000 });
      assert.equal(child.signal, 'SIGKILL', child.stderr);
      const db = new DatabaseSync(path.join(workspace, 'state.sqlite3'));
      const inbox = new MessageInbox({ db, runtime: { snapshot: () => ({ work: { turnInProgress: true } }) } });
      const executor = fakeExecutor();
      const tasks = new TaskManager({ db, config: { workspace }, executor, inbox, lineFor: () => '' });
      t.after(() => { tasks.shutdown(); inbox.stop(); db.close(); fs.rmSync(workspace, { recursive: true, force: true }); });
      inbox.restore();
      tasks.restore();
      if (stage === 'start' || stage === 'check') {
        assert.equal(executor.turns.length, 1);
        assert.match(executor.turns[0].prompt, /ORIGINAL_REQUEST: never publish/);
        if (stage === 'check') assert.match(executor.turns[0].prompt, /FOLLOW_UP: include test evidence/);
        assert.equal(executor.turns[0].task.native_session, null);
        assert.equal(inbox.size, 0);
      } else {
        assert.equal(executor.turns.length, 0);
        assert.equal(tasks.get('t1').status, 'done');
        assert.equal(inbox.size, 1);
        assert.match(inbox.entries[0].item.text, /COMPLETE_RESULT/);
        assert.ok(db.prepare('SELECT relayed_at_ms FROM task_reports').get().relayed_at_ms);
        inbox.restore();
        tasks.restore();
        assert.equal(inbox.size, 1, 'restoring twice does not publish the same receipt twice');
        assert.equal(db.prepare('SELECT COUNT(*) n FROM inbox_pending').get().n, 1);
      }
    });
  }

  it('takes its slot count from the worker settings, and a raised limit starts queued work at once', async (t) => {
    let limits = { slots: 1, reservedSlots: 0 };
    const { tasks, executor } = setup(t, { runtimes: { limits: () => limits, modelFor: () => ({ model: '', effort: '' }) } });
    assert.equal(tasks.overview({ recent: 0 }).slots.of, 1, 'the setting overrides the default');
    tasks.create({ rows: [row()], title: 'A', runtime: 'pi' });
    tasks.create({ rows: [row()], title: 'B', runtime: 'pi' });
    assert.deepEqual(executor.turns.map((turn) => turn.task.id), ['t1'], 'one slot, one turn');
    limits = { slots: 2, reservedSlots: 1 };
    tasks.pump();
    assert.deepEqual(executor.turns.map((turn) => turn.task.id), ['t1', 't2'], 'the next slot starts the queued task');
    assert.deepEqual(tasks.overview({ recent: 0 }).slots, { used: 2, of: 2, reserved: 0, reservedOf: 1 });
  });

  it('runs the reserved slots on top of the others, and a follow-up waits out a lowered limit too', async (t) => {
    let limits = { slots: 3, reservedSlots: 1 };
    const { tasks, executor } = setup(t, { runtimes: { limits: () => limits, modelFor: () => ({ model: '', effort: '' }) } });
    for (const title of ['A', 'B', 'C']) tasks.create({ rows: [row()], title, runtime: 'pi' });
    tasks.create({ rows: [row({ content: 'follow-up' })], title: 'A2', runtime: 'pi', parent: 't1' });
    assert.deepEqual(executor.turns.map((turn) => turn.task.id), ['t1', 't2', 't3', 't4'], 'three slots plus one reserved: four turns');
    assert.deepEqual(tasks.overview({ recent: 0 }).slots, { used: 3, of: 3, reserved: 1, reservedOf: 1 });
    limits = { slots: 1, reservedSlots: 1 };
    tasks.create({ rows: [row({ content: 'another follow-up' })], title: 'B2', runtime: 'pi', parent: 't2' });
    executor.turns[3].finish({ text: 'a2' });
    await settle();
    assert.equal(executor.turns.length, 4, 'three normal turns still running against a limit of 1 + 1: the follow-up waits');
    executor.turns[0].finish({ text: 'a' });
    await settle();
    assert.equal(executor.turns.length, 4, 'two still running: the limit of 1 + 1 is reached');
    executor.turns[2].finish({ text: 'c' });
    await settle();
    assert.equal(executor.turns.at(-1).task.id, 't5', 'one left running: the follow-up takes the reserved slot');
  });
});
