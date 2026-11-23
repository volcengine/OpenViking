// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { randomBytes } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
import { TaskManager } from '../src/tasks.mjs';
import { MessageInbox } from '../src/inbox.mjs';
import { executeTasksCommand, taskStatusLine } from '../src/task-commands.mjs';
import { executeCommand, helpText, parseCommand } from '../src/commands.mjs';

const settle = () => new Promise((resolve) => setImmediate(resolve));
const OWNER_DM = { chatType: 'p2p', senderType: 'user', senderId: 'ou_owner' };
const connectors = { isOwner: (event) => event.senderId === 'ou_owner' };

function setup(t, { now = Date.now } = {}) {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'task-commands-'));
  t.after(() => fs.rmSync(workspace, { recursive: true, force: true }));
  const db = new DatabaseSync(':memory:');
  db.exec(`CREATE TABLE connector_events (seq INTEGER PRIMARY KEY, message_id TEXT);
    CREATE TABLE outbox (id INTEGER PRIMARY KEY, inbound_seq INTEGER, remote_message_id TEXT);
    CREATE TABLE chat_sources (chat_id TEXT PRIMARY KEY, name TEXT);
    INSERT INTO chat_sources VALUES ('oc_group', 'OpenViking 用户群');`);
  const turns = [];
  const executor = {
    start({ task }) {
      let finish;
      const turn = { task, done: new Promise((resolve) => { finish = resolve; }) };
      turn.finish = (result = { text: '' }) => finish(result);
      turns.push(turn);
      return { done: turn.done, stop: () => finish({ text: '', error: 'stopped' }) };
    },
  };
  const inbox = new MessageInbox({ db, runtime: { snapshot: () => ({ work: { turnInProgress: true } }) } });
  const tasks = new TaskManager({ db, config: { workspace }, executor, inbox, now, lineFor: (row) => `[chat=${row.chat_id} event=${row.event_id}] ${row.content}` });
  t.after(() => { tasks.shutdown(); inbox.stop(); });
  let seq = 0;
  const row = (fields = {}) => {
    seq += 1;
    return { seq, event_id: `e${seq}`, connector_instance: 'lark', source: 'lark', scope_key: 'lark:oc_group:omt_1', chat_id: 'oc_group',
      thread_id: 'omt_1', message_id: `om_${seq}`, chat_type: 'group', sender_id: 'ou_alice', content: `line ${seq}`, ...fields };
  };
  const run = (arg) => executeTasksCommand({ name: 'tasks', arg }, { event: OWNER_DM, connectors, tasks, now: now() });
  return { tasks, turns, row, run, db };
}

describe('/tasks', () => {
  it('is for the owner in a direct chat, and says so on an instance without workers', (t) => {
    const { tasks } = setup(t);
    for (const event of [{ ...OWNER_DM, chatType: 'group' }, { ...OWNER_DM, senderId: 'ou_alice' }, { ...OWNER_DM, senderType: 'bot' }]) {
      assert.match(executeTasksCommand({ name: 'tasks', arg: '' }, { event, connectors, tasks }).text, /^仅限已绑定的 owner 在与机器人的私聊中使用 \/tasks。$/);
    }
    assert.match(executeTasksCommand({ name: 'tasks', arg: '' }, { event: OWNER_DM, connectors, tasks: null }).text, /没有启用 workers 调度/);
  });

  it('lists live tasks with what each is doing, the last ones that ended, and the slots in use', async (t) => {
    const { tasks, turns, row, run } = setup(t);
    assert.match(run('').text, /^还没有后台任务。/);
    tasks.create({ rows: [row({ content: '排查令牌刷新失败' })], title: '排查飞书 Watch 令牌刷新失败', requester: '陈晓斌', runtime: 'pi' });
    tasks.create({ rows: [row()], title: '核实账号能否改名', requester: 'Bob', runtime: 'pi' });
    tasks.report('t2', { state: 'needs_user', question: '是哪个 account？' });
    turns[1].finish({ text: '' });
    tasks.create({ rows: [row()], title: '解读 0.5.4 更新', requester: 'Carol', runtime: 'pi', parent: 't1' });
    tasks.report('t3', { state: 'done', draft: '0.5.4 改了…' });
    turns[2].finish({ text: '' });
    await settle();
    tasks.settleReports({ taskIds: ['t3'] });
    const text = run('').text;
    assert.match(text, /^后台任务：运行中 1 · 排队 0 · 等待回复 1 · 共 3 个 · worker 槽位 1\/3（跟进预留 0\/1）\n/);
    assert.match(text, /\n进行中\nt1 运行 不到 1 分钟（第 1 次运行）「排查飞书 Watch 令牌刷新失败」陈晓斌 · OpenViking 用户群 · 话题\n\n/);
    assert.match(text, /\n等待回复\nt2 等 Bob 回复 不到 1 分钟「核实账号能否改名」Bob · OpenViking 用户群 · 话题 · 结果待 router 转发\n/);
    assert.match(text, /\n最近结束\nt3 完成 · 不到 1 分钟前「解读 0\.5\.4 更新」Carol · OpenViking 用户群 · 话题 · 跟进 t1 · 结果已了结\n/);
    assert.match(text, /\n用法：\/tasks 查看任务/);
    assert.equal(taskStatusLine(tasks), '后台任务：运行中 1 · 等待回复 1（/tasks 查看）');
    assert.equal(taskStatusLine(null), '');
  });

  it('shows a task that ended blocked among those that ended, never as one in progress', async (t) => {
    const { tasks, turns, row, run } = setup(t);
    tasks.create({ rows: [row()], title: '推送 slugify 修复', requester: 'Alice', runtime: 'pi' });
    tasks.report('t1', { state: 'blocked', summary: '没有仓库写权限', draft: '修好了，但没有写权限，推不上去' });
    turns[0].finish({ text: '' });
    await settle();
    const text = run('').text;
    assert.match(text, /^后台任务：运行中 0 · 排队 0 · 等待回复 0 · 共 1 个 · worker 槽位 0\/3/);
    assert.doesNotMatch(text, /进行中|等待回复\n/);
    assert.match(text, /\n最近结束\nt1 受阻 · 不到 1 分钟前「推送 slugify 修复」Alice · OpenViking 用户群 · 话题 · 结果待 router 转发\n/);
    assert.equal(taskStatusLine(tasks), '', '/status counts no task at work');
    assert.match(run('t1').text, /\n状态：受阻 · 不到 1 分钟前\n/);
    assert.match(run('stop t1').text, /^t1 已结束（受阻），无需停止。$/);
  });

  it('lists the questions nobody answered for days apart, in one line at the end', async (t) => {
    const day = 86_400_000;
    let at = Date.parse('2026-11-01T00:00:00Z');
    const { tasks, turns, row, run } = setup(t, { now: () => at });
    for (const [index, name] of ['Alice', 'Bob'].entries()) {
      tasks.create({ rows: [row()], title: `问 ${name}`, requester: name, runtime: 'pi' });
      tasks.report(`t${index + 1}`, { state: 'needs_user', question: '哪个？' });
      turns[index].finish({ text: '' });
      await settle();
      at += 4 * day;
    }
    const text = run('').text;
    assert.match(text, /^后台任务：运行中 0 · 排队 0 · 等待回复 1 · 久未回复 1 · 共 2 个 · /);
    assert.match(text, /\n等待回复\nt2 等 Bob 回复 4 天「问 Bob」/);
    assert.match(text, /\n久未回复（5 天以上无动静，router 的任务提示里不再出现；\/tasks stop <编号> 可关闭）\nt1 等 Alice 8 天\n\n用法：/);
    assert.equal(taskStatusLine(tasks), '后台任务：等待回复 1 · 久未回复 1（/tasks 查看）');
    assert.match(run('t1').text, /\n状态：等 Alice 回复 8 天（久未回复：router 的任务提示里不再出现/);
  });

  it('shows one task: its request, follow-ups and their fate, and its reports and their delivery', async (t) => {
    const { tasks, turns, row, run } = setup(t);
    tasks.create({ rows: [row({ content: '查一下 X 为什么慢' })], title: '查 X', requester: 'Alice', note: 'Alice 只要上周', runtime: 'pi' });
    tasks.route('t1', [row({ content: '只看上周的' })], 'owner');
    tasks.check('t1');
    tasks.route('t1', [row({ content: '帮我订个会议室' })], 'owner');
    tasks.handBack('t1', [tasks.unseen('t1')[0].id], '不是这个任务');
    tasks.route('t1', [row({ content: '我们这边也慢' })], 'context');
    tasks.report('t1', { state: 'done', draft: '慢在缺索引', artifacts: ['https://github.com/x/pull/1'] });
    turns[0].finish({ text: '' });
    await settle();
    const { ok, text } = run('t1');
    assert.equal(ok, true);
    assert.match(text, /^t1「查 X」\n状态：完成 · 不到 1 分钟前\n请求人：Alice · OpenViking 用户群 · 话题 · 创建于 \d\d-\d\d \d\d:\d\d\nrouter 备注：Alice 只要上周\nRuntime：pi\n请求：\n  查一下 X 为什么慢\n/);
    assert.match(text, /用户追加消息与额外上下文（3）：\n  （追加：[^\n]*）\n  .* 第 1 轮 · 追加 · 已读：只看上周的\n  .* 第 1 轮 · 已退回 · —：帮我订个会议室\n  .* 第 1 轮 · 背景 · 未读：我们这边也慢\n/);
    assert.match(text, /结果：\n  （每轮结束时[^\n]*）\n  .* 第 1 轮 · 完成 · 待 router 转发：慢在缺索引\n/);
    assert.match(text, /产出：https:\/\/github\.com\/x\/pull\/1\nworker 运行次数：1 · 目录：/);
    assert.match(run('t9').text, /^没有任务 t9。/);
    assert.match(run('nonsense').text, /^用法：/);
  });

  it('shows the requests that reopened a task after it ended, each with its fate', async (t) => {
    const { tasks, turns, row, run } = setup(t);
    tasks.create({ rows: [row({ content: '实现 slugify 并开个 PR' })], title: 'slugify PR', requester: 'Alice', runtime: 'pi' });
    tasks.report('t1', { state: 'done', draft: 'PR 已开' });
    turns[0].finish({ text: '' });
    await settle();
    tasks.route('t1', [row({ content: '函数改名叫 toSlug' })], 'owner', { requester: 'Bob' });
    const { text } = run('t1');
    assert.match(text, /\n请求：\n  实现 slugify 并开个 PR\n之后的请求（1）：\n  \d\d-\d\d \d\d:\d\d 第 2 次 · Bob（进行中）：函数改名叫 toSlug\n/);
    assert.doesNotMatch(text, /用户追加消息与额外上下文/, 'a request that reopened the task is not a follow-up');
    assert.match(run('').text, /\nt1 运行 不到 1 分钟（第 2 次运行）「slugify PR」Alice · OpenViking 用户群 · 话题 · 2 次请求\n/);
  });

  it('shows how much the task directories take, and a directory that was cleaned', async (t) => {
    const { tasks, turns, row, run } = setup(t);
    tasks.create({ rows: [row()], title: 'A', runtime: 'pi' });
    fs.writeFileSync(path.join(tasks.get('t1').workdir, 'a.bin'), randomBytes(2 * 1024 * 1024));
    assert.doesNotMatch(run('').text, /任务目录/, 'not before the first sweep');
    await tasks.sweepDirs();
    assert.match(run('').text, /\n任务目录占用 2 MB（上限 20\.0 GB）\n/);
    tasks.report('t1', { state: 'done', draft: 'a' });
    turns[0].finish({ text: '' });
    await settle();
    tasks.config.taskDirBudgetGb = 1e-12;
    await tasks.sweepDirs();
    assert.match(run('t1').text, /目录：.*tasks[\\/]t1（已清理）$/);
  });

  it('stops a live task, and refuses an ended one', async (t) => {
    const { tasks, turns, row, run } = setup(t);
    tasks.create({ rows: [row()], title: '长任务', runtime: 'pi' });
    tasks.route('t1', [row({ content: '顺便看下 Y' })], 'owner');
    const stopped = run('stop t1 跑太久了');
    assert.equal(stopped.ok, true);
    assert.match(stopped.text, /^已停止 t1「长任务」。请求人不会自动收到通知/);
    assert.equal(tasks.get('t1').status, 'stopped');
    assert.equal(turns[0].task.id, 't1');
    assert.match(tasks.inbox.entries.at(-1).item.text, /^\[task handback\] task=t1 [\s\S]*顺便看下 Y$/);
    assert.match(run('stop t1').text, /^t1 已结束（已停止），无需停止。$/);
    assert.match(run('stop t7').text, /^没有任务 t7。$/);
  });

  it('is a slash command: /t matches it, help lists it, and /status adds the tasks at work', async () => {
    assert.deepEqual(parseCommand('/t t3'), { name: 'tasks', arg: 't3' });
    assert.match(helpText({}), /\/tasks \[编号 \| stop 编号\] — 查看或停止后台任务/);
    const runtime = { snapshot: async () => ({ runtime: 'pi', model: 'm', work: { working: false, connected: true } }) };
    assert.deepEqual(await executeCommand({ name: 'tasks', arg: '' }, runtime, { tasks: () => ({ ok: true, text: 'listed' }) }), { ok: true, text: 'listed' });
    assert.match((await executeCommand({ name: 'tasks', arg: '' }, runtime)).text, /当前环境不支持 \/tasks。/);
    const status = await executeCommand({ name: 'status', arg: '' }, runtime, { statusExtra: () => '后台任务：运行中 1（/tasks 查看）' });
    assert.match(status.text, /^状态：空闲\n[\s\S]*\n后台任务：运行中 1（\/tasks 查看）$/);
  });
});
