// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// A task status card: one interactive card per task, re-rendered in place as
// the task moves. The renderer is pure: give it a task snapshot, get the card
// JSON that `client.mjs` sends and then patches.
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

// Card markdown is its own dialect: a title that contains `**` or a backtick
// would otherwise break the line it sits in.
export function cardText(value) {
  return String(value ?? '').replace(/([*_`~<>\n])/g, (char) => (char === '\n' ? ' ' : `\\${char}`));
}

export function oneLine(value, max = MAX_LINE_CHARS) {
  const text = String(value ?? '').replace(/\s+/g, ' ').trim();
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
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

// The button carries what to do, never who may do it: the clicker's identity
// arrives in the callback, and the task record says who asked for it.
function button(label, type, action, taskId) {
  return {
    tag: 'button',
    text: { tag: 'plain_text', content: label },
    type,
    value: { superagent_task: action, task_id: taskId },
  };
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
  const meta = [`请求人：${cardText(task.requesterName ?? '未知')}`];
  const elapsed = describeElapsed(elapsedMs);
  if (elapsed && isLive(task.status)) meta.push(`已进行：${elapsed}`);
  if (elapsed && !isLive(task.status)) meta.push(`用时：${elapsed}`);
  const lines = [`**${cardText(oneLine(task.title, 120))}**`, meta.join(' · ')];
  if (task.question) lines.push(`**等待你的回答**：${cardText(oneLine(task.question))}`);
  const progress = (task.progress ?? []).filter(Boolean).map((line) => oneLine(line));
  if (progress.length) lines.push(progress.slice(-MAX_PROGRESS_LINES).join('\n'));
  return lines;
}

function asElements(lines) {
  return lines.map((content) => ({ tag: 'div', text: { tag: 'lark_md', content } }));
}

// Renders one task. `now` and `startedAt` decide the elapsed time; the caller
// owns the clock so tests do not depend on it.
export function taskCard(task, { now = Date.now(), note = '', footer = '', settled = false } = {}) {
  const state = stateOf(task.status);
  const elapsedMs = task.startedAt ? now - task.startedAt : Number.NaN;
  const lines = bodyLines(task, elapsedMs);
  if (note) lines.push(cardText(oneLine(note)));
  const elements = asElements(lines);
  if (footer) {
    elements.push({ tag: 'hr' });
    elements.push({ tag: 'note', elements: [{ tag: 'plain_text', content: oneLine(footer) }] });
  }
  // A card whose click was already carried out keeps no button: the action
  // did happen, and pressing it again would read as if it had not.
  const row = settled ? [] : actionsFor(task);
  if (row.length) elements.push({ tag: 'action', actions: row });
  return {
    config: {
      wide_screen_mode: true,
      // Required for im.message.patch: without it Feishu keeps the first
      // rendering and the update is accepted but not applied.
      update_multi: true,
      summary: { content: oneLine(`任务 ${task.id} · ${state.label}：${task.title}`, 80) },
    },
    header: {
      template: state.template,
      title: { tag: 'plain_text', content: oneLine(`任务 ${task.id} · ${state.label}`, 60) },
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

// The same card, trimmed until it fits what Feishu accepts. Progress lines go
// first, then the note, then a shortened title: a status card that cannot be
// sent is worse than one that shows less.
export function fitCard(task, options = {}) {
  let progress = [...(task.progress ?? [])];
  let note = options.note ?? '';
  let title = task.title;
  for (;;) {
    const card = taskCard({ ...task, title, progress }, { ...options, note });
    if (cardBytes(card) <= MAX_CARD_BYTES) return card;
    if (progress.length > 1) { progress = progress.slice(1); continue; }
    if (progress.length) { progress = []; continue; }
    if (note) { note = ''; continue; }
    if (title.length > 40) { title = `${title.slice(0, 39)}…`; continue; }
    throw new Error(`task card for ${task.id} cannot fit ${MAX_CARD_BYTES} bytes`);
  }
}
