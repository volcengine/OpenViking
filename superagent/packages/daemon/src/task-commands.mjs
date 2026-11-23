// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// /tasks: the owner's view of the workers scheduler's tasks, answered by the
// Core without the model, like the other slash commands. Owner and direct chat
// only: the list carries requests from every chat the agent is in.
//
//   /tasks              live tasks, the last few that ended, slots in use
//   /tasks t12          one task: request, inputs, reports and their delivery
//   /tasks stop t12 …   stop it (and hand back what its worker never saw)
//
// The overview is also a card (Card JSON 2.0, a table per group of tasks),
// sent where the connector takes cards (Lark); elsewhere, and should the card
// be refused, the text goes.

import { formatBytes } from './task-dirs.mjs';

const TASK_ID = /^t[0-9]{1,6}$/;
const USAGE = '用法：/tasks 查看任务；/tasks <编号> 查看详情；/tasks stop <编号> [原因] 停止任务';
const RECENT = 5;
const LIVE_SHOWN = 20;

const STATUS = { queued: '排队', running: '运行', waiting: '等待回复', blocked: '受阻', done: '完成', dropped: '无需处理', failed: '失败', stopped: '已停止' };
const STATE = { done: '完成', needs_user: '提问', blocked: '受阻', dropped: '无需处理', failed: '失败', progress: '进展' };
// Why a task moved to another runtime (tasks.mjs, task_handovers).
export const HANDOVER_REASON = { round: '新一轮请求，worker 已改用此 Runtime', exhausted: '原 Runtime 额度用尽', failing: '原 Runtime 持续失败' };
const DELIVERY = { sent: '已发出', silent: '未发（router 判定不必回复）', unrelayed: '未能转发', settled: '已了结', offered: '待 router 转发', pending: '待投递' };

function since(ms, now) {
  const minutes = Math.max(0, Math.floor((now - ms) / 60_000));
  if (minutes < 1) return '不到 1 分钟';
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  return hours < 48 ? `${hours} 小时${minutes % 60 ? ` ${minutes % 60} 分` : ''}` : `${Math.floor(hours / 24)} 天`;
}

function clock(ms) {
  const date = new Date(ms);
  return `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

function oneLine(text, chars) {
  const value = String(text ?? '').replace(/\s+/g, ' ').trim();
  return value.length > chars ? `${value.slice(0, chars)}…` : value;
}

// The text of an input line without its daemon metadata.
function said(text) {
  return String(text ?? '').replace(/^\[[^\]]*\]\s*/, '');
}

function where(task) {
  const chat = task.chatName ?? (task.origin_chat ? `chat ${task.origin_chat.slice(-6)}` : task.origin_source);
  return `${chat}${task.origin_thread ? ' · 话题' : ''}`;
}

// How long a task has been at what it is doing: its running turn, its wait
// for a slot or an answer, or since it ended.
function elapsed(task, now) {
  if (task.status === 'running') return `${task.turnStartedAt ? since(task.turnStartedAt, now) : ''}（第 ${task.turns} 次运行）`;
  if (task.status === 'queued' || task.status === 'waiting') return since(task.updated_at_ms, now);
  return `${since(task.ended_at_ms ?? task.updated_at_ms, now)}前`;
}

function state(task, now) {
  if (task.status === 'running') return `运行 ${elapsed(task, now)}`.replace(' （', '（');
  if (task.status === 'queued') return `排队 ${elapsed(task, now)}`;
  if (task.status === 'waiting') return `等 ${task.requester_name ?? '请求人'} 回复 ${elapsed(task, now)}`;
  return `${STATUS[task.status] ?? task.status} · ${elapsed(task, now)}`;
}

// What else marks a task in a list: more than one round, the task it follows.
function extras(task) {
  return [...(task.round > 1 ? [`${task.round} 次请求`] : []), ...(task.parent_id ? [`跟进 ${task.parent_id}`] : [])];
}

// What became of its latest result, for a task that has one to show.
function delivered(task, labels = DELIVERY) {
  return task.report?.delivery && ['done', 'dropped', 'failed', 'waiting', 'blocked'].includes(task.status)
    ? labels[task.report.delivery] ?? '' : '';
}

function line(task, now) {
  const result = delivered(task);
  return `${task.id} ${state(task, now)}「${oneLine(task.title, 40)}」${task.requester_name ?? '未知'} · ${where(task)}${extras(task).map((extra) => ` · ${extra}`).join('')}${result ? ` · 结果${result}` : ''}`;
}

// The tasks that have not ended, by what they are doing: a worker on them
// (running, or queued for a slot), a question waiting for a person, or one
// nobody answered for days (dormant).
function liveCounts(live) {
  const count = (test) => live.filter(test).length;
  return [
    ['运行中', count((task) => task.status === 'running')],
    ['排队', count((task) => task.status === 'queued')],
    ['等待回复', count((task) => task.status === 'waiting' && !task.dormant)],
    ['久未回复', count((task) => task.dormant)],
  ];
}

// The one line /status adds when there are tasks at work.
export function taskStatusLine(tasks) {
  if (!tasks) return '';
  const { live } = tasks.overview({ recent: 0 });
  if (live.length === 0) return '';
  const parts = liveCounts(live).filter(([, n]) => n > 0).map(([label, n]) => `${label} ${n}`);
  return `后台任务：${parts.join(' · ')}（/tasks 查看）`;
}

// The questions nobody answered for days, in one line at the end: still
// waiting, but out of the router's task hints.
function dormantLines(lines, dormant, days, now) {
  if (dormant.length === 0) return;
  const shown = dormant.slice(0, LIVE_SHOWN).map((task) => `${task.id} 等 ${task.requester_name ?? '请求人'} ${since(task.updated_at_ms, now)}`);
  lines.push('', `久未回复（${days} 天以上无动静，router 的任务提示里不再出现；/tasks stop <编号> 可关闭）`,
    `${shown.join('、')}${dormant.length > LIVE_SHOWN ? ` 等 ${dormant.length} 个` : ''}`);
}

function section(lines, title, tasks, now) {
  if (tasks.length === 0) return;
  lines.push('', title);
  for (const task of tasks.slice(0, LIVE_SHOWN)) lines.push(line(task, now));
  if (tasks.length > LIVE_SHOWN) lines.push(`…另有 ${tasks.length - LIVE_SHOWN} 个`);
}

// The overview, in the order both forms show it: tasks with a worker on them
// first, then the questions waiting for a person, the latest asked first, the
// tasks that ended last, and those nobody answered for days, in one line: a
// question nobody answers never takes the place of work being done.
function overviewParts(view) {
  const { live, ended, total, slots, disk } = view;
  const waiting = live.filter((task) => task.status === 'waiting').sort((a, b) => b.updated_at_ms - a.updated_at_ms);
  return {
    counts: liveCounts(live).filter(([label, n]) => n > 0 || label !== '久未回复'),
    slots: `共 ${total} 个 · worker 槽位 ${slots.used}/${slots.of}（跟进预留 ${slots.reserved}/${slots.reservedOf}）`,
    disk: disk ? `任务目录占用 ${formatBytes(disk.bytes)}${disk.budget > 0 ? `（上限 ${formatBytes(disk.budget)}）` : ''}` : null,
    sections: [
      ['进行中', [...live.filter((task) => task.status === 'running'), ...live.filter((task) => task.status === 'queued')]],
      ['等待回复', waiting.filter((task) => !task.dormant)],
      ['最近结束', ended],
    ],
    dormant: waiting.filter((task) => task.dormant),
  };
}

function overviewText(view, now) {
  const parts = overviewParts(view);
  const lines = [`后台任务：${parts.counts.map(([label, n]) => `${label} ${n}`).join(' · ')} · ${parts.slots}`];
  if (parts.disk) lines.push(parts.disk);
  for (const [title, tasks] of parts.sections) section(lines, title, tasks, now);
  dormantLines(lines, parts.dormant, view.dormantDays, now);
  lines.push('', USAGE);
  return lines.join('\n');
}

// ---- the overview as a card (Lark, Card JSON 2.0) ----------------------------

// A task's status as a coloured tag.
const TAG = {
  running: ['运行中', 'blue'], queued: ['排队', 'wathet'], waiting: ['等待回复', 'orange'], done: ['完成', 'green'],
  blocked: ['受阻', 'red'], failed: ['失败', 'carmine'], dropped: ['无需处理', 'violet'], stopped: ['已停止', 'violet'],
};
// A table cell is narrow: the delivery states in a few characters.
const CELL_DELIVERY = { ...DELIVERY, silent: '未发', offered: '待转发' };
const COLUMNS = {
  id: { name: 'id', display_name: '编号', data_type: 'text', width: '80px' },
  status: { name: 'status', display_name: '状态', data_type: 'options', width: '100px' },
  elapsed: { name: 'elapsed', display_name: '时长', data_type: 'text' },
  title: { name: 'title', display_name: '任务', data_type: 'text', width: '240px' },
  requester: { name: 'requester', display_name: '请求人', data_type: 'text' },
  where: { name: 'where', display_name: '位置', data_type: 'text' },
  result: { name: 'result', display_name: '结果', data_type: 'text' },
};

// Lark refuses a bot message with an email address in it (code 230028): a
// card shows every @ of what people wrote as a full-width ＠.
function cardText(value) {
  return String(value ?? '').replace(/@/g, '＠');
}

function plain(content, style = {}) {
  return { tag: 'plain_text', content: cardText(content), ...style };
}

// One group of tasks as a table, paged by Lark ten rows at a time. A task at
// work has no result yet: its group has no such column.
function table(tasks, now, { result }) {
  const keys = ['id', 'status', 'elapsed', 'title', 'requester', 'where', ...(result ? ['result'] : [])];
  return {
    tag: 'table', page_size: 10, row_height: 'low', freeze_first_column: true,
    header_style: { background_style: 'grey', bold: true },
    columns: keys.map((key) => COLUMNS[key]),
    rows: tasks.slice(0, LIVE_SHOWN).map((task) => {
      const [label, color] = TAG[task.status] ?? [task.status, 'wathet'];
      const more = extras(task);
      return {
        id: task.id,
        status: [{ text: label, color }],
        elapsed: elapsed(task, now),
        title: cardText(`${oneLine(task.title, 120)}${more.length ? `（${more.join(' · ')}）` : ''}`),
        requester: cardText(task.requester_name ?? '未知'),
        where: cardText(where(task)),
        ...(result ? { result: delivered(task, CELL_DELIVERY) } : {}),
      };
    }),
  };
}

function overviewCard(view, now) {
  const parts = overviewParts(view);
  const grey = { text_size: 'notation', text_color: 'grey' };
  const elements = [{ tag: 'markdown', content: parts.counts.map(([label, n]) => `${label} **${n}**`).join(' · ') }];
  if (parts.disk) elements.push({ tag: 'div', text: plain(parts.disk, grey) });
  for (const [title, tasks] of parts.sections) {
    if (tasks.length === 0) continue;
    elements.push({ tag: 'markdown', content: `**${title}**` }, table(tasks, now, { result: title !== '进行中' }));
    if (tasks.length > LIVE_SHOWN) elements.push({ tag: 'div', text: plain(`…另有 ${tasks.length - LIVE_SHOWN} 个`, grey) });
  }
  if (parts.dormant.length) {
    const shown = parts.dormant.slice(0, LIVE_SHOWN).map((task) => `${task.id} 等 ${task.requester_name ?? '请求人'} ${since(task.updated_at_ms, now)}`);
    elements.push({ tag: 'div', text: plain(`久未回复（${view.dormantDays} 天以上无动静，router 的任务提示里不再出现）：${shown.join('、')}`
      + `${parts.dormant.length > LIVE_SHOWN ? ` 等 ${parts.dormant.length} 个` : ''}`) });
  }
  elements.push({ tag: 'div', text: plain('/tasks <编号> 查看详情 · /tasks stop <编号> [原因] 停止任务', grey) });
  return {
    schema: '2.0', config: { update_multi: true },
    header: { template: 'blue', title: plain('后台任务'), subtitle: plain(parts.slots) },
    body: { elements },
  };
}

function overview(tasks, now) {
  const view = tasks.overview({ recent: RECENT });
  if (view.total === 0) return { ok: true, text: `还没有后台任务。\n${USAGE}` };
  return { ok: true, text: overviewText(view, now), card: overviewCard(view, now) };
}

function detailText(tasks, taskId, now) {
  const task = tasks.detail(taskId);
  if (!task) return { ok: false, text: `没有任务 ${taskId}。\n${USAGE}` };
  const owner = task.inputs.filter((input) => input.role === 'owner');
  const request = owner.filter((input) => input.seq === task.origin_seq);
  // A later round's request is listed with its round, not as a follow-up.
  const reopenedBy = new Set(task.rounds.filter((round) => round.round > 1).map((round) => round.origin_seq));
  const followUps = task.inputs.filter((input) => (input.seq !== task.origin_seq && !reopenedBy.has(input.seq)) || input.role !== 'owner');
  const lines = [
    `${task.id}「${task.title}」`,
    `状态：${state(task, now)}${task.dormant ? '（久未回复：router 的任务提示里不再出现，回复它的消息、在它的话题里或按编号仍能找到）' : ''}`,
    `请求人：${task.requester_name ?? '未知'} · ${where(task)} · 创建于 ${clock(task.created_at_ms)}`,
    ...(task.parent_id ? [`跟进自：${task.parent_id}`] : []),
    ...(task.children.length ? [`后续任务：${task.children.map((child) => `${child.id}（${STATUS[child.status] ?? child.status}）`).join('、')}`] : []),
    ...(task.note ? [`router 备注：${oneLine(task.note, 120)}`] : []),
    ...(task.runtime ? [`Runtime：${task.runtime}${task.model ? ` · 模型 ${task.model}` : ''}${task.effort ? ` · 档位 ${task.effort}` : ''}`] : []),
    ...(task.handovers ?? []).slice(-5).map((handover) => `  ${clock(handover.created_at_ms)} 第 ${handover.round} 轮从 ${handover.from_runtime} 交接到 ${handover.to_runtime}（${HANDOVER_REASON[handover.reason] ?? handover.reason}${handover.taken_at_ms ? '' : '；新 worker 还没完成一次运行'}）`),
    '请求：',
    ...request.map((input) => `  ${oneLine(said(input.text), 200)}`),
  ];
  const later = task.rounds.filter((round) => round.round > 1);
  if (later.length) {
    lines.push(`之后的请求（${later.length}）：`);
    for (const round of later.slice(-10)) {
      const text = task.inputs.find((input) => input.seq === round.origin_seq && input.role === 'owner')?.text;
      const outcome = round.outcome === 'handed_back' ? '已退回' : round.outcome ? STATUS[round.outcome] ?? round.outcome : '进行中';
      lines.push(`  ${clock(round.opened_at_ms)} 第 ${round.round} 次 · ${round.requester_name ?? '未知'}（${outcome}）：${oneLine(said(text), 80)}`);
    }
  }
  if (followUps.length) {
    lines.push(`追加（${followUps.length}）：`);
    for (const input of followUps.slice(-10)) {
      const tag = input.handed_back_at_ms ? '已退回' : input.role === 'owner' ? '追加' : input.role === 'context' ? '背景' : '备注';
      const seen = input.handed_back_at_ms || input.role !== 'owner' ? '' : input.seen_at_ms || input.delivered_at_ms ? '' : ' · worker 未读';
      lines.push(`  ${clock(input.created_at_ms)} ${tag}${seen}：${oneLine(said(input.text), 80)}`);
    }
  }
  const reports = task.reports.filter((report) => report.state !== 'progress');
  if (reports.length) {
    lines.push('结果：');
    for (const report of reports.slice(-5)) {
      const delivery = report.settled_at_ms ? DELIVERY[report.settled_by === 'stay_silent' ? 'silent' : report.settled_by === 'unrelayed' ? 'unrelayed' : report.settled_by ? 'sent' : 'settled'] : report.inbox_id ? `待 router 转发${report.offers ? `（已重投 ${report.offers} 次）` : ''}` : report.relayed_at_ms === null && report.offers === null ? '未投递（被后续结果取代）' : '待投递';
      lines.push(`  ${clock(report.created_at_ms)} ${STATE[report.state] ?? report.state} · ${delivery}：${oneLine(report.draft || report.question || report.summary, 80)}`);
    }
  }
  const artifacts = JSON.parse(task.artifacts_json || '[]');
  if (artifacts.length) lines.push(`产出：${artifacts.slice(0, 5).join('、')}`);
  if (task.lastError) lines.push(`最近一次失败（${clock(task.lastError.occurred_at_ms)}）：${oneLine(task.lastError.summary, 160)}`);
  const dir = task.dir?.removed_at_ms ? '（已清理）' : task.dir?.pruned_at_ms ? '（依赖目录已清理）' : '';
  lines.push(`worker 运行次数：${task.turns} · 目录：${task.workdir}${dir}`);
  return { ok: true, text: lines.join('\n') };
}

export function executeTasksCommand(command, { event, connectors, tasks, now = Date.now() }) {
  if (event?.chatType !== 'p2p' || event.senderType !== 'user' || !connectors?.isOwner?.(event)) {
    return { ok: false, text: '仅限已绑定的 owner 在与机器人的私聊中使用 /tasks。' };
  }
  if (!tasks) return { ok: false, text: '这个实例没有启用 workers 调度（agents[].scheduler = "workers"），没有后台任务。' };
  const [verb = '', ...rest] = String(command.arg ?? '').trim().split(/\s+/).filter(Boolean);
  if (!verb) return overview(tasks, now);
  if (TASK_ID.test(verb.toLowerCase()) && rest.length === 0) return detailText(tasks, verb.toLowerCase(), now);
  if (verb.toLowerCase() === 'stop' && TASK_ID.test(String(rest[0] ?? '').toLowerCase())) {
    const taskId = rest[0].toLowerCase();
    const task = tasks.get(taskId);
    if (!task) return { ok: false, text: `没有任务 ${taskId}。` };
    if (!tasks.isLive(task)) {
      return { ok: false, text: `${taskId} 已结束（${STATUS[task.status] ?? task.status}），无需停止。` };
    }
    const reason = rest.slice(1).join(' ');
    tasks.stop(taskId, `owner /tasks stop${reason ? `: ${reason}` : ''}`);
    return { ok: true, text: `已停止 ${taskId}「${oneLine(task.title, 40)}」。请求人不会自动收到通知；它的 worker 还没读到的追加已交回 router 处理。` };
  }
  return { ok: false, text: USAGE };
}
