// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// /tasks: the owner's view of the workers scheduler's tasks, answered by the
// Core without the model, like the other slash commands. Owner and direct chat
// only: the list carries requests from every chat the agent is in.
//
//   /tasks              live tasks, the last few that ended, slots in use
//   /tasks t12          one task: request, inputs, reports and their delivery
//   /tasks stop t12 …   stop it (and hand back what its worker never saw)

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

function state(task, now) {
  if (task.status === 'running') {
    return `运行 ${task.turnStartedAt ? since(task.turnStartedAt, now) : ''}（第 ${task.turns} 次运行）`.replace(' （', '（');
  }
  if (task.status === 'queued') return `排队 ${since(task.updated_at_ms, now)}`;
  if (task.status === 'waiting') return `等 ${task.requester_name ?? '请求人'} 回复 ${since(task.updated_at_ms, now)}`;
  if (task.status === 'blocked') return `受阻 ${since(task.updated_at_ms, now)}`;
  return `${STATUS[task.status] ?? task.status} · ${since(task.ended_at_ms ?? task.updated_at_ms, now)}前`;
}

function line(task, now) {
  const delivery = task.report?.delivery && ['done', 'dropped', 'failed', 'waiting', 'blocked'].includes(task.status)
    ? ` · 结果${DELIVERY[task.report.delivery]}` : '';
  return `${task.id} ${state(task, now)}「${oneLine(task.title, 40)}」${task.requester_name ?? '未知'} · ${where(task)}${task.round > 1 ? ` · ${task.round} 次请求` : ''}${task.parent_id ? ` · 跟进 ${task.parent_id}` : ''}${delivery}`;
}

// The one line /status adds when there are tasks at work.
export function taskStatusLine(tasks) {
  if (!tasks) return '';
  const { live } = tasks.overview({ recent: 0 });
  if (live.length === 0) return '';
  const count = (status) => live.filter((task) => task.status === status).length;
  const parts = [['运行中', count('running')], ['排队', count('queued')], ['等待回复', count('waiting')], ['受阻', count('blocked')]]
    .filter(([, n]) => n > 0).map(([label, n]) => `${label} ${n}`);
  return `后台任务：${parts.join(' · ')}（/tasks 查看）`;
}

function overviewText(tasks, now) {
  const { live, ended, total, slots, disk } = tasks.overview({ recent: RECENT });
  if (total === 0) return `还没有后台任务。\n${USAGE}`;
  const lines = [`后台任务：进行中 ${live.length} · 共 ${total} 个 · worker 槽位 ${slots.used}/${slots.of}（跟进预留 ${slots.reserved}/${slots.reservedOf}）`];
  if (disk) lines.push(`任务目录占用 ${formatBytes(disk.bytes)}${disk.budget > 0 ? `（上限 ${formatBytes(disk.budget)}）` : ''}`);
  if (live.length) {
    lines.push('', '进行中');
    for (const task of live.slice(0, LIVE_SHOWN)) lines.push(line(task, now));
    if (live.length > LIVE_SHOWN) lines.push(`…另有 ${live.length - LIVE_SHOWN} 个`);
  }
  if (ended.length) {
    lines.push('', '最近结束');
    for (const task of ended) lines.push(line(task, now));
  }
  lines.push('', USAGE);
  return lines.join('\n');
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
    `状态：${state(task, now)}`,
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
  if (!verb) return { ok: true, text: overviewText(tasks, now) };
  if (TASK_ID.test(verb.toLowerCase()) && rest.length === 0) return detailText(tasks, verb.toLowerCase(), now);
  if (verb.toLowerCase() === 'stop' && TASK_ID.test(String(rest[0] ?? '').toLowerCase())) {
    const taskId = rest[0].toLowerCase();
    const task = tasks.get(taskId);
    if (!task) return { ok: false, text: `没有任务 ${taskId}。` };
    if (['done', 'dropped', 'failed', 'stopped'].includes(task.status)) {
      return { ok: false, text: `${taskId} 已结束（${STATUS[task.status] ?? task.status}），无需停止。` };
    }
    const reason = rest.slice(1).join(' ');
    tasks.stop(taskId, `owner /tasks stop${reason ? `: ${reason}` : ''}`);
    return { ok: true, text: `已停止 ${taskId}「${oneLine(task.title, 40)}」。请求人不会自动收到通知；它的 worker 还没读到的追加已交回 router 处理。` };
  }
  return { ok: false, text: USAGE };
}
