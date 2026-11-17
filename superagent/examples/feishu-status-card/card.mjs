// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// A task status card: one interactive card per task, re-rendered in place as
// the task moves. The renderer is pure: give it a task snapshot, get the card
// JSON that `client.mjs` sends and then patches.
//
// Every piece of text in the card is `plain_text`, including the task title.
// A title is a person's words, not markup: rendered as `lark_md`, a title with
// `**` or a tick would restyle the card, and escaping it with backslashes
// depends on an escape syntax the renderer does not document. Plain text cannot
// be misread that way, and nothing here needs formatting to be understood.
//
// Feishu accepts 30 KiB of card JSON per message. A long progress list would
// exceed it, so the body keeps the newest lines and drops the rest instead of
// failing the update; the card must always stay sendable.

export const MAX_CARD_BYTES = 30 * 1024;

// Template colours are part of the message a reader sees first: a card that
// went grey reads as finished before the title is read.
const STATES = {
  queued: { template: 'wathet', label: '排队中', live: true },
  running: { template: 'blue', label: '处理中', live: true },
  waiting: { template: 'orange', label: '等待补充', live: true },
  blocked: { template: 'orange', label: '受阻', live: true },
  done: { template: 'green', label: '已完成', live: false },
  failed: { template: 'red', label: '失败', live: false },
  stopped: { template: 'grey', label: '已停止', live: false },
};

const RETRYABLE = new Set(['failed', 'stopped']);
const MAX_PROGRESS_LINES = 6;
const MAX_PROGRESS_CHARS = 1_200;
const MAX_LINE_CHARS = 240;

export function stateOf(status) {
  return STATES[status] ?? { template: 'grey', label: status, live: false };
}

export function isLive(status) {
  return stateOf(status).live;
}

export function isRetryable(status) {
  return RETRYABLE.has(status);
}

// A task comes from the scheduler (`tasks.get`), whose columns are snake_case,
// or from a test or another producer with camelCase fields. Read either, so
// the wiring in the README works with the record the daemon actually has.
export function normalizeTask(task) {
  return {
    id: task.id,
    title: task.title ?? '',
    status: task.status ?? 'queued',
    requesterId: task.requesterId ?? task.requester_id ?? null,
    requesterName: task.requesterName ?? task.requester_name ?? null,
    startedAt: task.startedAt ?? task.started_at_ms ?? task.created_at_ms ?? null,
    question: task.question ?? '',
    progress: task.progress ?? (task.summary ? [task.summary] : []),
  };
}

export function oneLine(value, max = MAX_LINE_CHARS) {
  const text = String(value ?? '').replace(/\s+/g, ' ').trim();
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

// Progress arrives as lines of text, some of them multi-line. Keep the line
// breaks inside one entry, drop control characters, and stop looking back once
// the newest lines already fill the card: an old step is the first thing a
// reader can do without.
function progressText(lines) {
  const kept = [];
  let budget = MAX_PROGRESS_CHARS;
  for (const line of [...lines].reverse()) {
    const clean = String(line ?? '').replace(/[\u0000-\u0008\u000b-\u001f]/g, ' ').trim();
    if (!clean) continue;
    if (clean.length > budget && kept.length) break;
    kept.push(clean.length > budget ? `${clean.slice(0, budget - 1)}…` : clean);
    budget -= Math.min(clean.length, MAX_PROGRESS_CHARS);
    if (budget <= 0) break;
  }
  return kept.reverse().join('\n');
}

export function describeElapsed(ms) {
  if (!Number.isFinite(ms) || ms < 0) return '';
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 1) return '不到 1 分钟';
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时 ${minutes % 60} 分钟`;
  return `${Math.floor(hours / 24)} 天 ${hours % 24} 小时`;
}

function text(content) {
  return { tag: 'plain_text', content };
}

// The button carries what to do, never who may do it: the clicker's identity
// arrives in the callback, and the task record says who asked for it.
function button(label, type, action, taskId) {
  return { tag: 'button', text: text(label), type, value: { superagent_task: action, task_id: taskId } };
}

function actionsFor(task) {
  const row = [];
  if (isLive(task.status)) row.push(button('停止', 'danger', 'stop', task.id));
  if (isRetryable(task.status)) row.push(button('重试', 'primary', 'retry', task.id));
  return row;
}

// Body lines, most important first, so the trimming below removes the least
// useful tail rather than the current state.
function bodyLines(task, elapsedMs) {
  const meta = [`请求人：${oneLine(task.requesterName ?? '未知', 60)}`];
  const elapsed = describeElapsed(elapsedMs);
  if (elapsed) meta.push(`${isLive(task.status) ? '已进行' : '用时'}：${elapsed}`);
  return {
    title: oneLine(task.title, 120),
    meta: meta.join(' · '),
    question: task.question ? `等待你的回答：${oneLine(task.question)}` : '',
    progress: progressText((task.progress ?? []).slice(-MAX_PROGRESS_LINES)),
  };
}

// Renders one task. `now` and `startedAt` decide the elapsed time; the caller
// owns the clock so tests do not depend on it.
export function taskCard(task, { now = Date.now(), note = '', footer = '', settled = false } = {}) {
  const current = normalizeTask(task);
  const state = stateOf(current.status);
  const lines = bodyLines(current, current.startedAt ? now - current.startedAt : Number.NaN);
  const elements = [{ tag: 'div', text: text(lines.title) }, { tag: 'div', text: text(lines.meta) }];
  if (lines.question) elements.push({ tag: 'div', text: text(lines.question) });
  if (lines.progress) elements.push({ tag: 'div', text: text(lines.progress) });
  if (note) elements.push({ tag: 'div', text: text(oneLine(note)) });
  if (footer) {
    elements.push({ tag: 'hr' });
    elements.push({ tag: 'note', elements: [text(oneLine(footer))] });
  }
  // A card whose click was already carried out keeps no button: the action
  // did happen, and pressing it again would read as if it had not.
  const row = settled ? [] : actionsFor(current);
  if (row.length) elements.push({ tag: 'action', actions: row });
  return {
    config: {
      wide_screen_mode: true,
      // Required for im.message.patch: without it Feishu keeps the first
      // rendering and the update is accepted but not applied.
      update_multi: true,
    },
    header: {
      template: state.template,
      title: text(oneLine(`任务 ${current.id} · ${state.label}`, 60)),
    },
    elements,
  };
}

export function cardJSON(card) {
  return JSON.stringify(card);
}

export function cardBytes(card) {
  return Buffer.byteLength(cardJSON(card));
}
