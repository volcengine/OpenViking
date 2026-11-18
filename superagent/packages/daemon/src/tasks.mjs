// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { chatTag, metadataTag, oneLine } from './runtime-input.mjs';
import { safeError } from './logger.mjs';

// Workers (agents[].scheduler = workers). The main session is the router: it
// reads every input (through the inbox), answers what is quick, and hands
// longer work to a task with task_create. Each task gets a worker: its own
// session on the same runtime, run one turn at a time (worker-executor.mjs).
// A worker never talks to people. It reports with task_report, and the
// report reaches the router as a `[task report]` input, which the router
// relays where the request was made. The router stays the only outlet, so
// every check on the delivery path (authority, freshness, placement, bot fuse,
// approvals, outbox) applies to what a worker produced.
//
// Task state lives here, in SQLite (created on first use): the task with its
// origin, the inputs routed to it (acknowledged after a successful turn), and an
// append-only list of reports. Nothing depends on the worker's native session
// surviving: a lost session is started again from the task record.
//
// A task is one piece of work, not a conversation. Only inputs that change or
// answer the work it is doing reach it, and an ended task takes none: more
// work on it is a new task that follows it (parent_id), whose worker starts
// from what the earlier one produced. A worker hands back what it is given
// that is not its task, and a task that ends with inputs it never saw hands
// those back too: nothing routed is dropped.
//
// A report is settled when the router answers for its task (a delivery or a
// silence naming it with task_ids, or made on its origin), not when the router
// was shown it: one the router was shown and left is offered again, so a
// report is neither lost to a failed router turn nor sent twice.

export const WORKER_SLOTS = 3;
// A slot only a follow-up to an existing task may take, so new work cannot
// keep a person waiting on the task they are already in.
export const RESERVED_SLOTS = 1;
// Ended tasks kept in the working set the router is shown; live ones always are.
export const RECENT_ENDED = 32;
// Tasks task_list shows for one person (for_event), newest first.
export const ASKER_TASKS = 10;
// Worker turns in a row that may run to catch up with inputs that arrived
// during the previous one before its report is relayed anyway (while_working).
const CATCH_UP_TURNS = 3;
// Times a report the router was shown and did not settle is offered again:
// once after a finished router turn (marked undelivered), and up to this many
// after failed ones.
const REOFFERS_AFTER_FAILURE = 3;
const PARENT_DRAFT_CHARS = 1_500;
const TRANSIENT_RETRIES = 2;
const SUMMARY_CHARS = 600;
const REPORT_RETRY_MS = 1_000;
const CANDIDATES = 3;

export const REPORT_STATES = Object.freeze(['progress', 'done', 'needs_user', 'blocked', 'dropped', 'failed']);
const ENDED = new Set(['done', 'dropped', 'failed', 'stopped']);
const STATUS_FOR = { done: 'done', needs_user: 'waiting', blocked: 'blocked', dropped: 'dropped', failed: 'failed' };

const SCHEMA = `
  CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    origin_seq INTEGER,
    origin_event TEXT,
    origin_instance TEXT,
    origin_scope TEXT,
    origin_chat TEXT,
    origin_thread TEXT,
    origin_message TEXT,
    origin_chat_type TEXT,
    origin_source TEXT,
    requester_id TEXT,
    requester_name TEXT,
    note TEXT NOT NULL DEFAULT '',
    runtime TEXT,
    session_id TEXT NOT NULL,
    native_session TEXT,
    workdir TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    question TEXT NOT NULL DEFAULT '',
    draft TEXT NOT NULL DEFAULT '',
    artifacts_json TEXT NOT NULL DEFAULT '[]',
    turns INTEGER NOT NULL DEFAULT 0,
    recovering INTEGER NOT NULL DEFAULT 0,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    ended_at_ms INTEGER
  );
  CREATE TABLE IF NOT EXISTS task_inputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    seq INTEGER,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    delivered_at_ms INTEGER
  );
  CREATE INDEX IF NOT EXISTS task_inputs_pending ON task_inputs(task_id, delivered_at_ms);
  CREATE TABLE IF NOT EXISTS task_input_claims (
    input_id INTEGER PRIMARY KEY,
    task_id TEXT NOT NULL,
    turn INTEGER NOT NULL
  );
  CREATE TABLE IF NOT EXISTS task_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    state TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    draft TEXT NOT NULL DEFAULT '',
    question TEXT NOT NULL DEFAULT '',
    structured INTEGER NOT NULL,
    created_at_ms INTEGER NOT NULL,
    relayed_at_ms INTEGER
  );
  -- Durable intent to deliver a selected final report, never a progress or
  -- superseded report. inbox_id: the router has it in its inbox (or was shown
  -- it); settled_at_ms: the router answered for its task; while_working: it
  -- went out past the catch-up turns, with its worker on newer input.
  CREATE TABLE IF NOT EXISTS task_report_deliveries (
    report_id INTEGER PRIMARY KEY,
    inbox_id INTEGER,
    created_at_ms INTEGER NOT NULL
  );
`;

// Columns added after the first release of this schema.
const COLUMNS = [
  ['tasks', 'parent_id', 'TEXT'],
  ['task_inputs', 'handed_back_at_ms', 'INTEGER'],
  ['task_inputs', 'seen_at_ms', 'INTEGER'],
  ['task_report_deliveries', 'settled_at_ms', 'INTEGER'],
  ['task_report_deliveries', 'offers', 'INTEGER NOT NULL DEFAULT 0'],
  // How a report was settled: the tool that answered for it, or 'unrelayed';
  // and the message a delivery sent.
  ['task_report_deliveries', 'settled_by', 'TEXT'],
  ['task_report_deliveries', 'outbox_id', 'INTEGER'],
  ['task_report_deliveries', 'while_working', 'INTEGER NOT NULL DEFAULT 0'],
  // A claim its worker had taken when it filed a final report this turn.
  ['task_input_claims', 'reported', 'INTEGER NOT NULL DEFAULT 0'],
];

const RECEIPT = /^task-report:([0-9]+)$/;

function clip(text, chars) {
  const value = String(text ?? '').trim();
  return value.length > chars ? `${value.slice(0, chars)}…` : value;
}

// Worker text shown to the router: every line quoted, so none of it can pass
// for a daemon-authenticated input line.
function quoted(text) {
  return String(text ?? '').split('\n').map((line) => `| ${line}`).join('\n');
}

// Overlapping character pairs: a crude tokenizer that works for Chinese and
// English alike, for ranking similar tasks (never for routing on its own).
function bigrams(text) {
  const chars = [...String(text ?? '').toLowerCase().replace(/[\s\p{P}\p{S}]+/gu, ' ')];
  const grams = new Map();
  for (let index = 0; index < chars.length - 1; index += 1) {
    const gram = chars[index] + chars[index + 1];
    if (gram.includes(' ')) continue;
    grams.set(gram, (grams.get(gram) ?? 0) + 1);
  }
  return grams;
}

function similarity(query, document) {
  if (query.size === 0 || document.size === 0) return 0;
  let shared = 0;
  for (const [gram, count] of query) shared += Math.min(count, document.get(gram) ?? 0);
  return shared / Math.sqrt([...query.values()].reduce((a, b) => a + b, 0) * [...document.values()].reduce((a, b) => a + b, 0));
}

export class TaskManager {
  constructor({
    db,
    config,
    executor,
    inbox,
    lineFor,
    logger = null,
    recordActivity = () => {},
    releaseClaims = () => {},
    slots = WORKER_SLOTS,
    reservedSlots = RESERVED_SLOTS,
    now = Date.now,
  }) {
    if (!db) throw new TypeError('db is required');
    if (!executor) throw new TypeError('executor is required');
    if (!inbox || inbox.db !== db) throw new TypeError('inbox must share the task database');
    Object.assign(this, {
      db, config, executor, inbox, lineFor, logger, recordActivity, releaseClaims, slots, reservedSlots, now,
    });
    this.db.exec(SCHEMA);
    for (const [table, column, type] of COLUMNS) {
      if (this.db.prepare(`PRAGMA table_info(${table})`).all().some((row) => row.name === column)) continue;
      this.db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${type}`);
      // Reports an older daemon put in the inbox counted as delivered there.
      if (column === 'settled_at_ms') this.db.exec('UPDATE task_report_deliveries SET settled_at_ms = created_at_ms WHERE inbox_id IS NOT NULL');
    }
    // Worker authority tokens: token -> task id, for the turn that holds it.
    this.tokens = new Map();
    // Tasks with a turn to run, in the order they asked for it.
    this.pending = [];
    this.running = new Map();
    this.catchUp = new Map();
    this.retries = new Map();
    this.reportRetry = null;
    this.stopped = false;
  }

  transaction(fn) {
    this.db.exec('BEGIN IMMEDIATE');
    try {
      const result = fn();
      this.db.exec('COMMIT');
      return result;
    } catch (error) {
      this.db.exec('ROLLBACK');
      throw error;
    }
  }

  // ---- reads ----------------------------------------------------------------

  get(taskId) {
    return this.db.prepare('SELECT * FROM tasks WHERE id = ?').get(taskId) ?? null;
  }

  isLive(task) {
    return Boolean(task) && !ENDED.has(task.status);
  }

  // The working set the router is shown: every live task and the most recent
  // ended ones. Older tasks stay in the table for task_list and exact links.
  workingSet() {
    const live = this.db.prepare(`SELECT * FROM tasks WHERE status NOT IN ('done', 'dropped', 'failed', 'stopped')
      ORDER BY created_at_ms`).all();
    const ended = this.db.prepare(`SELECT * FROM tasks WHERE status IN ('done', 'dropped', 'failed', 'stopped')
      ORDER BY ended_at_ms DESC LIMIT ?`).all(RECENT_ENDED);
    return [...live, ...ended];
  }

  // Scopes where a task is still at work: its OnIt stays up there.
  liveScopes() {
    return new Set(this.db.prepare(`SELECT origin_scope FROM tasks WHERE status IN ('queued', 'running')`).all()
      .map((row) => row.origin_scope).filter(Boolean));
  }

  // The live task that holds this input line as part of its request, if any.
  liveHolder(seq) {
    return this.db.prepare(`SELECT t.id FROM task_inputs i JOIN tasks t ON t.id = i.task_id
      WHERE i.seq = ? AND i.role = 'owner' AND i.handed_back_at_ms IS NULL
        AND t.status NOT IN ('done', 'dropped', 'failed', 'stopped') LIMIT 1`).get(seq)?.id ?? null;
  }

  // The task that currently owns the decision for this input. Active tasks
  // claim it directly; a successfully ended task keeps the claim while its
  // final report is still waiting for the router. Failed/stopped tasks release
  // it so the original request can be offered again.
  decisionClaim(seq) {
    return this.db.prepare(`SELECT t.id FROM task_inputs i JOIN tasks t ON t.id = i.task_id
      WHERE i.seq = ? AND i.role IN ('owner', 'context') AND i.handed_back_at_ms IS NULL
        AND (t.status IN ('queued', 'running', 'waiting', 'blocked') OR (
          t.status IN ('done', 'dropped') AND EXISTS (
            SELECT 1 FROM task_reports r JOIN task_report_deliveries d ON d.report_id = r.id
            WHERE r.task_id = t.id AND d.settled_at_ms IS NULL
          )
        ))
      ORDER BY i.id DESC LIMIT 1`).get(seq)?.id ?? null;
  }

  claimSeqs(taskId) {
    return this.db.prepare(`SELECT DISTINCT seq FROM task_inputs
      WHERE task_id = ? AND role = 'owner' AND seq IS NOT NULL AND handed_back_at_ms IS NULL ORDER BY seq`)
      .all(taskId).map((row) => Number(row.seq));
  }

  releaseTaskClaims(taskId) {
    const seqs = this.claimSeqs(taskId);
    if (seqs.length) this.releaseClaims(seqs, taskId);
    return seqs.length;
  }

  workerFor(token) {
    return typeof token === 'string' && this.tokens.has(token) ? this.tokens.get(token) : null;
  }

  card(task, { detail = false } = {}) {
    const where = task.origin_chat ? `chat=${chatTag(metadataTag(task.origin_chat), task.origin_thread)}` : `source=${task.origin_source}`;
    const delivery = this.delivery(task);
    const head = `${task.id} [${task.status}] "${oneLine(task.title)}" for ${task.requester_name ?? 'unknown'} in ${where}${task.parent_id ? ` (follows ${task.parent_id})` : ''}${this.progressNote(task, delivery)}`;
    if (!detail) return head;
    const sent = delivery?.sent;
    return [head,
      `origin: msg=${metadataTag(task.origin_message ?? '')} event=${metadataTag(task.origin_event ?? '')}`,
      ...(task.summary ? [`summary: ${oneLine(clip(task.summary, SUMMARY_CHARS))}`] : []),
      ...(task.question ? [`question waiting: ${oneLine(clip(task.question, SUMMARY_CHARS))}`] : []),
      ...(JSON.parse(task.artifacts_json).length ? [`artifacts: ${JSON.parse(task.artifacts_json).map(oneLine).join(', ')}`] : []),
      ...(sent ? [`sent ${new Date(sent.at).toISOString().slice(0, 16)}Z as msg=${metadataTag(sent.messageId ?? '')}: ${oneLine(clip(sent.text, SUMMARY_CHARS))}`] : []),
      ...(delivery && !sent && ['offered', 'pending', 'unrelayed'].includes(delivery.state) && task.draft
        ? [`draft never sent:\n${clip(task.draft, PARENT_DRAFT_CHARS).split('\n').map((line) => `| ${line}`).join('\n')}`] : []),
      `turns: ${task.turns}, updated ${new Date(task.updated_at_ms).toISOString().slice(0, 16)}Z`,
    ].join('\n');
  }

  ago(ms) {
    const minutes = Math.max(0, Math.round((this.now() - ms) / 60_000));
    return minutes < 60 ? `${minutes}m` : minutes < 48 * 60 ? `${Math.round(minutes / 60)}h` : `${Math.round(minutes / 1440)}d`;
  }

  // For a progress question: how long a task has been at work, and what
  // became of its latest result.
  progressNote(task, delivery) {
    const parts = [];
    const startedAt = this.running.get(task.id)?.startedAt;
    if (task.status === 'running' && startedAt) parts.push(`working for ${this.ago(startedAt)}`);
    if (delivery) {
      const what = delivery.reportState === 'needs_user' ? 'question' : 'result';
      if (delivery.state === 'sent') parts.push(`${what} sent ${this.ago(delivery.sent?.at ?? delivery.at)} ago${delivery.sent?.messageId ? ` as msg=${metadataTag(delivery.sent.messageId)}` : ''}`);
      else if (delivery.state === 'silent') parts.push(`${what} not sent: you stayed silent on it`);
      else if (delivery.state === 'unrelayed') parts.push(`${what} never sent`);
      else if (delivery.state === 'settled') parts.push(`${what} handled ${this.ago(delivery.at)} ago`);
      else parts.push(`${what} not sent yet`);
    }
    return parts.length ? ` · ${parts.join(' · ')}` : '';
  }

  // What became of a task's latest final report: sent (when, as which
  // message, what it said), answered with silence, never relayed, or still on
  // its way. 'settled' is a report settled before this was recorded.
  delivery(task) {
    const row = this.db.prepare(`SELECT r.id, r.state, r.created_at_ms, d.inbox_id, d.settled_at_ms, d.settled_by, d.outbox_id
      FROM task_reports r LEFT JOIN task_report_deliveries d ON d.report_id = r.id
      WHERE r.task_id = ? AND r.state != 'progress' ORDER BY r.id DESC LIMIT 1`).get(task.id);
    if (!row) return null;
    const state = row.settled_at_ms == null ? (row.inbox_id != null ? 'offered' : 'pending')
      : row.settled_by === 'stay_silent' ? 'silent' : row.settled_by === 'unrelayed' ? 'unrelayed' : row.settled_by ? 'sent' : 'settled';
    let sent = null;
    if (state === 'sent' && row.outbox_id != null) {
      try {
        const out = this.db.prepare('SELECT remote_message_id, chat_id, payload_json, sent_at_ms FROM outbox WHERE id = ?').get(row.outbox_id);
        if (out) {
          const content = JSON.parse(out.payload_json || '{}').content;
          // { type: 'markdown', text } for text; a card or a file shows as its type.
          const text = typeof content === 'string' ? content : content?.text ?? (content?.type ? `[${content.type}]` : '');
          sent = { at: out.sent_at_ms ?? row.settled_at_ms, messageId: out.remote_message_id, chatId: out.chat_id, text: String(text) };
        }
      } catch { /* no outbox table (tests) */ }
    }
    return { reportId: row.id, reportState: row.state, createdAt: row.created_at_ms, state, at: row.settled_at_ms, sent };
  }

  // requester: { id, chatId, direct } lists what one person asked for, ended
  // tasks included: in a group only what was asked in that chat, in a direct
  // chat everything they asked for.
  list({ taskId = null, query = '', includeEnded = false, requester = null } = {}) {
    if (taskId) {
      const task = this.get(taskId);
      return task ? [this.card(task, { detail: true })] : [];
    }
    let rows = requester
      ? this.db.prepare(`SELECT * FROM tasks WHERE requester_id = ?${requester.direct ? '' : ' AND origin_chat = ?'}
          ORDER BY created_at_ms DESC, rowid DESC LIMIT ?`).all(...[requester.id, ...(requester.direct ? [] : [requester.chatId]), ASKER_TASKS])
      : includeEnded ? this.db.prepare('SELECT * FROM tasks ORDER BY created_at_ms DESC LIMIT 200').all() : this.workingSet();
    if (query) {
      const grams = bigrams(query);
      rows = rows.map((task) => ({ task, score: similarity(grams, bigrams(`${task.title} ${task.summary}`)) }))
        .filter((row) => row.score > 0.15).sort((a, b) => b.score - a.score).map((row) => row.task);
    }
    return rows.map((task) => this.card(task));
  }

  // Message ids that belong to a task: its routed inputs and what the router
  // sent for it (outbox rows attributed to its origin).
  taskForMessage(messageId) {
    if (!messageId) return null;
    const viaInput = this.db.prepare(`SELECT i.task_id FROM task_inputs i JOIN connector_events e ON e.seq = i.seq
      WHERE e.message_id = ? ORDER BY i.id DESC LIMIT 1`).get(messageId);
    if (viaInput) return viaInput.task_id;
    return this.db.prepare(`SELECT t.id FROM tasks t JOIN outbox o ON o.inbound_seq = t.origin_seq
      WHERE o.remote_message_id = ? ORDER BY t.created_at_ms DESC LIMIT 1`).get(messageId)?.id ?? null;
  }

  // Tasks an incoming line may belong to, with why. Exact signals first; word
  // overlap only ranks. Only the owner may point across conversations.
  candidatesFor(event, { owner = false } = {}) {
    const hits = new Map();
    const add = (task, reason, weight) => {
      const hit = hits.get(task.id) ?? { task, reasons: [], weight: 0 };
      hit.reasons.push(reason);
      hit.weight += weight;
      hits.set(task.id, hit);
    };
    const replyTask = this.taskForMessage(event.parentId);
    const query = bigrams(event.content);
    for (const task of this.workingSet()) {
      const sameChat = task.origin_chat && task.origin_chat === event.chatId;
      if (!owner && !sameChat && replyTask !== task.id) continue;
      if (replyTask === task.id) add(task, 'replies to its message', 5);
      if (sameChat && event.threadId && task.origin_thread === event.threadId) add(task, 'same thread', 4);
      if (task.status === 'waiting' && task.requester_id === event.senderId && task.origin_scope === event.scopeKey) {
        add(task, 'waiting for this person', 4);
      }
      // Similar words only rank what an exact signal found, or point the
      // owner across chats; on their own they say nothing.
      const score = similarity(query, bigrams(`${task.title} ${task.summary}`));
      if (score > 0.25 && (owner || hits.has(task.id))) add(task, 'similar words', 1);
    }
    return [...hits.values()].sort((a, b) => b.weight - a.weight).slice(0, CANDIDATES);
  }

  // What a task is doing, as the router needs it to route: working, waiting
  // for whom, or ended and how long ago.
  state(task) {
    if (task.status === 'running' || task.status === 'queued') return 'working';
    if (task.status === 'waiting') return `waiting for ${task.requester_name ?? 'the requester'}`;
    if (!ENDED.has(task.status)) return task.status;
    const minutes = Math.max(0, Math.round((this.now() - (task.ended_at_ms ?? task.updated_at_ms)) / 60_000));
    return `${task.status} ${minutes < 60 ? `${minutes}m` : `${Math.round(minutes / 60)}h`} ago`;
  }

  // The daemon line an input carries when tasks may relate to it: facts about
  // where the input is and what each task is doing. Whether it belongs to one
  // is the router's call, made on what the input asks for.
  hintFor(event, options) {
    const hits = this.candidatesFor(event, options);
    if (hits.length === 0) return '';
    return `[daemon tasks] near this input: ${hits.map(({ task, reasons }) => `${task.id} [${this.state(task)}] "${oneLine(clip(task.title, 60))}" (${reasons.join(', ')})`).join(' · ')}`;
  }

  // ---- router commands ------------------------------------------------------

  nextId() {
    const row = this.db.prepare("SELECT COUNT(*) AS n FROM tasks").get();
    return `t${Number(row.n) + 1}`;
  }

  // rows: accepted connector_events rows (oldest first); the first one is the
  // origin the result answers.
  create({ rows, title, note = '', requester = null, runtime = null, parent = null }) {
    if (!rows.length) throw new Error('a task needs at least one input');
    if (parent && !this.get(parent)) throw new Error(`no task ${parent} to follow`);
    const [origin] = rows;
    const id = this.nextId();
    const time = this.now();
    const workdir = path.join(this.config.workspace, 'tasks', id);
    fs.mkdirSync(workdir, { recursive: true });
    this.transaction(() => {
      this.db.prepare(`INSERT INTO tasks(id, title, status, origin_seq, origin_event, origin_instance, origin_scope, origin_chat,
        origin_thread, origin_message, origin_chat_type, origin_source, requester_id, requester_name, note, runtime, session_id, workdir,
        parent_id, created_at_ms, updated_at_ms) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).run(
        id, clip(title, 120), origin.seq, origin.event_id, origin.connector_instance, origin.scope_key, origin.chat_id ?? null,
        origin.thread_id ?? null, origin.message_id ?? null, origin.chat_type ?? null, origin.source, origin.sender_id ?? null,
        requester, clip(note, 400), runtime, randomUUID(), workdir, parent, time, time,
      );
      for (const row of rows) this.addInput(id, row, 'owner');
    });
    this.recordActivity('task.created', { summary: `${id} ${title}`, inboundSeq: origin.seq, metadata: { task: id, ...(parent ? { parent } : {}) } });
    this.request(id);
    return this.get(id);
  }

  addInput(taskId, row, role) {
    const text = this.lineFor(row);
    this.db.prepare('INSERT INTO task_inputs(task_id, seq, role, text, created_at_ms) VALUES (?, ?, ?, ?, ?)')
      .run(taskId, row.seq ?? null, role, text, this.now());
  }

  // A follow-up to a live task: `owner` inputs change or answer the work it is
  // doing and reach its worker while it works (or wake a waiting one);
  // `context` ones are background. An ended task takes nothing.
  route(taskId, rows, role) {
    const task = this.get(taskId);
    if (!task) throw new Error(`no task ${taskId}`);
    if (ENDED.has(task.status)) {
      throw Object.assign(new Error(`${taskId} has ended (${task.status}). More work on it is a new task: task_create with follows=${taskId}`), { code: 'task_ended' });
    }
    const known = new Set(this.db.prepare('SELECT seq FROM task_inputs WHERE task_id = ? AND seq IS NOT NULL').all(taskId).map((row) => row.seq));
    rows = rows.filter((row) => !known.has(row.seq));
    if (rows.length === 0) return task;
    this.transaction(() => {
      for (const row of rows) this.addInput(taskId, row, role);
      this.touch(taskId);
      if (role === 'owner' && (task.status === 'waiting' || task.status === 'blocked')) this.setStatus(taskId, 'queued');
    });
    if (role === 'owner') this.request(taskId);
    return this.get(taskId);
  }

  note(taskId, text) {
    const task = this.get(taskId);
    if (!task) throw new Error(`no task ${taskId}`);
    this.db.prepare('INSERT INTO task_inputs(task_id, seq, role, text, created_at_ms) VALUES (?, NULL, ?, ?, ?)')
      .run(taskId, 'note', `[note from the agent] ${oneLine(clip(text, 600))}`, this.now());
  }

  stop(taskId, reason = '') {
    const task = this.get(taskId);
    if (!task) throw new Error(`no task ${taskId}`);
    if (ENDED.has(task.status)) return task;
    this.pending = this.pending.filter((id) => id !== taskId);
    const slot = this.running.get(taskId);
    slot?.handle?.stop?.();
    this.db.prepare("UPDATE tasks SET status = 'stopped', ended_at_ms = ?, updated_at_ms = ? WHERE id = ?").run(this.now(), this.now(), taskId);
    this.recordActivity('task.ended', { summary: `${taskId} stopped${reason ? `: ${reason}` : ''}`, metadata: { task: taskId, status: 'stopped' } });
    this.handBackUnseen(taskId, 'the task was stopped before its worker saw them');
    this.releaseTaskClaims(taskId);
    return this.get(taskId);
  }

  // Follow-ups a task holds that no turn of its worker ever took. The request
  // the task was created with is the task itself.
  unseen(taskId) {
    return this.db.prepare(`SELECT i.* FROM task_inputs i JOIN tasks t ON t.id = i.task_id WHERE i.task_id = ? AND i.role = 'owner'
      AND i.seq IS NOT NULL AND i.seq != t.origin_seq AND i.seen_at_ms IS NULL AND i.handed_back_at_ms IS NULL ORDER BY i.id`).all(taskId);
  }

  handBackUnseen(taskId, reason) {
    const rows = this.unseen(taskId);
    if (rows.length) this.handBack(taskId, rows.map((row) => row.id), reason);
  }

  // Inputs a task will not handle go back to the router as one inbox item:
  // the original lines, so it can decide on each (a new task, an answer, or
  // nothing). Never dropped, never sent back to the same task.
  handBack(taskId, inputIds, reason) {
    const task = this.get(taskId);
    if (!task) throw new Error(`no task ${taskId}`);
    const rows = inputIds.map((id) => this.db.prepare(`SELECT * FROM task_inputs WHERE id = ? AND task_id = ?
      AND handed_back_at_ms IS NULL`).get(id, taskId)).filter(Boolean);
    if (rows.length === 0) return 0;
    const event = rows[0].seq == null ? null
      : this.db.prepare('SELECT * FROM connector_events WHERE seq = ?').get(rows[0].seq) ?? null;
    const scopeKey = event?.scope_key ?? task.origin_scope;
    const item = {
      text: [
        `[task handback] task=${task.id} title="${oneLine(clip(task.title, 80))}" reason="${oneLine(clip(reason, 200))}"`,
        'These inputs reached that task, but it will not handle them and nothing has answered them: decide on each as a new input (a new task, an answer, or nothing). Never route them back to that task.',
        ...rows.map((row) => row.text),
      ].join('\n'),
      seq: null,
      scopeKey,
      chatType: 'p2p',
      scope: { kind: 'task', key: scopeKey },
    };
    const meta = {
      chatId: event?.chat_id ?? task.origin_chat, threadId: event?.thread_id ?? task.origin_thread, source: event?.source ?? task.origin_source,
      instance: event?.connector_instance ?? task.origin_instance, kind: 'task.handback', chatType: event?.chat_type ?? task.origin_chat_type,
      from: `task ${task.id}`,
    };
    const entry = this.transaction(() => {
      const mark = this.db.prepare('UPDATE task_inputs SET handed_back_at_ms = ? WHERE id = ?');
      for (const row of rows) mark.run(this.now(), row.id);
      this.db.prepare(`DELETE FROM task_input_claims WHERE input_id IN (${rows.map(() => '?').join(', ')})`).run(...rows.map((row) => row.id));
      return this.inbox.persist(item, meta);
    });
    this.inbox.publish(entry);
    this.recordActivity('task.handed_back', { summary: `${task.id} handed back ${rows.length}: ${oneLine(clip(reason, 120))}`,
      inboundSeq: rows[0].seq ?? null, metadata: { task: task.id, inputs: rows.length } });
    return rows.length;
  }

  setStatus(taskId, status) {
    this.db.prepare('UPDATE tasks SET status = ?, updated_at_ms = ?, ended_at_ms = ? WHERE id = ?')
      .run(status, this.now(), ENDED.has(status) ? this.now() : null, taskId);
  }

  touch(taskId) {
    this.db.prepare('UPDATE tasks SET updated_at_ms = ? WHERE id = ?').run(this.now(), taskId);
  }

  // ---- worker side ----------------------------------------------------------

  // task_check: the inputs routed since the worker last saw any.
  check(taskId) {
    const rows = this.undelivered(taskId);
    this.transaction(() => this.claimInputs(this.get(taskId), rows));
    return rows;
  }

  undelivered(taskId) {
    return this.db.prepare(`SELECT * FROM task_inputs WHERE task_id = ? AND delivered_at_ms IS NULL AND handed_back_at_ms IS NULL
      AND id NOT IN (SELECT input_id FROM task_input_claims) ORDER BY id`).all(taskId);
  }

  // The inputs a worker may hand back: owner inputs routed to its task (the
  // request that created the task is the task itself). A context line asks
  // nothing of the worker and is already decided: it is ignored, not handed back.
  inputsBySeq(taskId, seqs) {
    const task = this.get(taskId);
    return seqs.map((seq) => (seq === task?.origin_seq ? null : this.db.prepare(`SELECT * FROM task_inputs WHERE task_id = ? AND seq = ?
      AND role = 'owner' AND handed_back_at_ms IS NULL ORDER BY id DESC LIMIT 1`).get(taskId, seq))).filter(Boolean);
  }

  claimInputs(task, rows) {
    const claim = this.db.prepare('INSERT INTO task_input_claims(input_id, task_id, turn) VALUES (?, ?, ?)');
    const seen = this.db.prepare('UPDATE task_inputs SET seen_at_ms = COALESCE(seen_at_ms, ?) WHERE id = ?');
    for (const row of rows) {
      claim.run(row.id, task.id, task.turns);
      seen.run(this.now(), row.id);
    }
  }

  // A turn that failed after its final report acknowledges what the worker
  // had taken when it filed that report, and nothing it took after.
  settleInputs(task, result, { reported = false } = {}) {
    this.transaction(() => {
      if (!result.error || reported) {
        this.db.prepare(`UPDATE task_inputs SET delivered_at_ms = ? WHERE id IN
          (SELECT input_id FROM task_input_claims WHERE task_id = ? AND turn = ?${result.error ? ' AND reported = 1' : ''})`)
          .run(this.now(), task.id, task.turns);
      }
      this.db.prepare('DELETE FROM task_input_claims WHERE task_id = ?').run(task.id);
      // Only a successful turn is a checkpoint we can resume. After an
      // interrupted/failed turn, reconstruct from our ledger in a new session.
      const session = result.error ? null : result.nativeSession ?? (task.runtime === 'codex' ? null : task.session_id);
      this.db.prepare('UPDATE tasks SET native_session = ? WHERE id = ?').run(session, task.id);
    });
  }

  report(taskId, { state, summary = '', draft = '', question = '', artifacts = [] }) {
    if (!REPORT_STATES.includes(state)) throw new Error(`state must be one of ${REPORT_STATES.join(', ')}`);
    const task = this.get(taskId);
    if (!task) throw new Error(`no task ${taskId}`);
    this.db.prepare(`INSERT INTO task_reports(task_id, state, summary, draft, question, structured, created_at_ms)
      VALUES (?, ?, ?, ?, ?, 1, ?)`).run(taskId, state, clip(summary, 4_000), clip(draft, 20_000), clip(question, 2_000), this.now());
    const known = JSON.parse(task.artifacts_json);
    const merged = [...new Set([...known, ...artifacts.map((item) => clip(item, 300))])].slice(0, 50);
    this.db.prepare(`UPDATE tasks SET summary = ?, question = ?, draft = ?, artifacts_json = ?, updated_at_ms = ? WHERE id = ?`)
      .run(clip(summary || task.summary, 4_000), state === 'needs_user' ? clip(question, 2_000) : '',
        clip(draft || task.draft, 20_000), JSON.stringify(merged), this.now(), taskId);
    if (state !== 'progress') this.db.prepare('UPDATE task_input_claims SET reported = 1 WHERE task_id = ?').run(taskId);
    return this.get(taskId);
  }

  // ---- scheduling -----------------------------------------------------------

  // A task with a turn running needs nothing more: what was routed to it
  // meanwhile is still undelivered when the turn ends (afterTurn).
  request(taskId) {
    if (this.stopped || this.pending.includes(taskId) || this.running.has(taskId)) return;
    this.pending.push(taskId);
    this.pump();
  }

  pump() {
    while (!this.stopped && this.pending.length > 0) {
      const reserved = [...this.running.values()].filter((slot) => slot.reserved).length;
      const normal = this.running.size - reserved;
      let index = normal < this.slots ? 0 : -1;
      // Beyond the normal slots only a follow-up to a task that already ran.
      if (index < 0 && reserved < this.reservedSlots) {
        index = this.pending.findIndex((id) => { const task = this.get(id); return (task?.turns ?? 0) > 0 || Boolean(task?.parent_id); });
      }
      if (index < 0) return;
      const [taskId] = this.pending.splice(index, 1);
      const task = this.get(taskId);
      if (!task || task.status === 'stopped') continue;
      void this.runTurn(task, { reserved: normal >= this.slots });
    }
  }

  brief(task, inputs) {
    const recovery = task.turns > 0 && !task.native_session;
    const prefix = task.recovering
      ? ['The daemon restarted while you were working on this task. Check your directory and what you already did (files, commits, issues, comments) before repeating any outside action.']
      : recovery ? ['The previous worker session could not be safely resumed. Check existing work before repeating any outside action.'] : [];
    if (recovery) inputs = this.db.prepare('SELECT * FROM task_inputs WHERE task_id = ? ORDER BY id').all(task.id);
    const owner = inputs.filter((row) => row.role === 'owner');
    const context = inputs.filter((row) => row.role !== 'owner');
    if (task.turns === 0 || recovery) {
      return [
        ...prefix,
        `[task ${task.id}] ${task.title}`,
        `Requested by ${task.requester_name ?? 'unknown'}${task.origin_chat ? ` in chat=${chatTag(metadataTag(task.origin_chat), task.origin_thread)}` : ''} (source=${task.origin_source}). The request, as the agent received it:`,
        ...owner.map((row) => row.text),
        ...(context.length ? ['Context:', ...context.map((row) => row.text)] : []),
        ...(task.note ? [`Routing note from the agent: ${task.note}`] : []),
        ...this.parentBrief(task),
        `Your directory: ${task.workdir}`,
        ...(recovery && task.summary ? ['Previous work summary (may be incomplete):', quoted(task.summary)] : []),
        ...(recovery && task.draft ? ['Previous draft (may be incomplete):', quoted(task.draft)] : []),
        ...(recovery && task.artifacts_json !== '[]' ? [`Previous artifacts: ${task.artifacts_json}`] : []),
        'Work on this request, then finish with task_report. You cannot message anyone: the agent replies to the requester from your report.',
      ].join('\n');
    }
    const unsent = (this.catchUp.get(task.id) ?? 0) > 0;
    return [
      ...prefix,
      ...(owner.length ? [`[task ${task.id}] New input from the requester's side:`, ...owner.map((row) => row.text)] : []),
      ...(context.length ? ['Context only (act on it only if it changes your task):', ...context.map((row) => row.text)] : []),
      ...(owner.length || context.length ? [] : [`[task ${task.id}] Continue your task.`]),
      ...(unsent ? ['Your previous report was not sent: the requester has not seen it. Your next report replaces it, so it must stand on its own: everything they need, updated for this input.'] : []),
      'Finish with task_report.',
    ].join('\n');
  }

  // What a task that follows another starts from: that work's request and
  // results, and where its files are.
  parentBrief(task) {
    const parent = task.parent_id ? this.get(task.parent_id) : null;
    if (!parent) return [];
    const request = this.db.prepare("SELECT text FROM task_inputs WHERE task_id = ? AND role = 'owner' ORDER BY id LIMIT 3").all(parent.id);
    const artifacts = JSON.parse(parent.artifacts_json);
    return [
      `This task follows ${parent.id} "${oneLine(clip(parent.title, 80))}" (${parent.status}), earlier work it builds on. Its request:`,
      ...request.map((row) => quoted(clip(row.text, 600))),
      ...(parent.summary ? ['What that work found:', quoted(clip(parent.summary, SUMMARY_CHARS))] : []),
      ...(parent.draft ? ['Its last answer to the requester:', quoted(clip(parent.draft, PARENT_DRAFT_CHARS))] : []),
      ...(artifacts.length ? [`Its artifacts: ${artifacts.map(oneLine).join(', ')}`] : []),
      `Its directory, with whatever it left there: ${parent.workdir}`,
      'Do only what this task asks; check what that work already did before redoing it.',
    ];
  }

  // The session a task that follows another may start from: that task's,
  // on the same runtime, at its last good checkpoint. The executor decides
  // whether it can be forked; the brief carries the summary either way.
  forkSource(task) {
    if (!task.parent_id || task.turns > 0) return null;
    const parent = this.get(task.parent_id);
    if (!parent || parent.runtime !== task.runtime) return null;
    const sessionId = parent.native_session ?? (parent.turns > 0 && parent.runtime !== 'codex' ? parent.session_id : null);
    return sessionId ? { taskId: parent.id, runtime: parent.runtime, sessionId } : null;
  }

  async runTurn(task, { reserved = false } = {}) {
    const token = randomUUID();
    const fork = this.forkSource(task);
    const inputs = this.undelivered(task.id);
    const prompt = this.brief(task, inputs);
    const reportsBefore = this.db.prepare('SELECT COALESCE(MAX(id), 0) AS id FROM task_reports WHERE task_id = ?').get(task.id).id;
    this.transaction(() => {
      this.db.prepare(`UPDATE tasks SET status = 'running', turns = turns + 1, recovering = 0, updated_at_ms = ?, ended_at_ms = NULL,
        session_id = ? WHERE id = ?`).run(this.now(), task.turns > 0 && !task.native_session ? randomUUID() : task.session_id, task.id);
      this.claimInputs(this.get(task.id), inputs);
    });
    this.tokens.set(token, task.id);
    const slot = { handle: null, reserved, startedAt: this.now() };
    this.running.set(task.id, slot);
    this.recordActivity('task.turn_started', { summary: `${task.id} ${task.title}`, metadata: { task: task.id, turn: task.turns + 1, inputs: inputs.length } });
    let result;
    try {
      slot.handle = this.executor.start({
        task: this.get(task.id), prompt, authority: token, fork,
      });
      result = await slot.handle.done;
    } catch (error) {
      result = { text: '', error: safeError(error) };
    } finally {
      this.tokens.delete(token);
      this.running.delete(task.id);
    }
    // Shutting down: the task stays `running`, and restore() resumes it.
    if (this.stopped) return;
    try {
      this.afterTurn(task.id, result ?? {}, reportsBefore, slot);
    } catch (error) {
      this.logger?.error('task_after_turn_failed', { task: task.id, error: safeError(error) });
    }
    this.pump();
  }

  afterTurn(taskId, result, reportsBefore, slot) {
    const task = this.get(taskId);
    if (!task || task.status === 'stopped') return;
    const structured = this.db.prepare('SELECT * FROM task_reports WHERE task_id = ? AND id > ? ORDER BY id DESC LIMIT 1')
      .get(taskId, reportsBefore);
    const final = structured && structured.state !== 'progress' ? structured : null;
    // A turn whose worker filed its final report is complete however its CLI
    // exited after that (often a rate limit on its closing words): the report
    // stands, and running the turn again could repeat what it did outside.
    const failed = Boolean(result.error) && !final;
    this.settleInputs(task, result, { reported: Boolean(final) });
    this.recordActivity('task.turn_completed', {
      summary: `${taskId} ${result.error ? `failed${final ? ' after its report' : ''}: ${result.error}` : 'turn ended'}`,
      status: result.error ? 'failed' : 'completed',
      metadata: { task: taskId, durationMs: this.now() - slot.startedAt, ...(result.usage ?? {}) },
    });
    if (failed && result.transient && (this.retries.get(taskId) ?? 0) < TRANSIENT_RETRIES) {
      this.retries.set(taskId, (this.retries.get(taskId) ?? 0) + 1);
      this.setStatus(taskId, 'queued');
      this.request(taskId);
      return;
    }
    this.retries.delete(taskId);
    let report = final;
    if (!report) {
      // No final report: the worker's last words are its answer, marked as
      // such; a failed turn reports the failure.
      const state = result.error ? 'failed' : 'done';
      const id = this.db.prepare(`INSERT INTO task_reports(task_id, state, summary, draft, question, structured, created_at_ms)
        VALUES (?, ?, ?, ?, '', 0, ?)`).run(taskId, state, clip(result.error ?? structured?.summary ?? '', 4_000),
        clip(result.error ? '' : result.text, 20_000), this.now()).lastInsertRowid;
      report = this.db.prepare('SELECT * FROM task_reports WHERE id = ?').get(id);
      if (!result.error && result.text) this.db.prepare('UPDATE tasks SET draft = ? WHERE id = ?').run(clip(result.text, 20_000), taskId);
    }
    // Task-level freshness: what the requester added during the turn must be
    // in the result before it is relayed.
    const fresh = this.undelivered(taskId).filter((row) => row.role === 'owner');
    const caught = this.catchUp.get(taskId) ?? 0;
    if (!failed && fresh.length > 0 && caught < CATCH_UP_TURNS) {
      this.catchUp.set(taskId, caught + 1);
      this.setStatus(taskId, 'queued');
      this.request(taskId);
      return;
    }
    this.catchUp.delete(taskId);
    // Past the catch-up turns the report goes out without the requester's
    // latest input, and the worker goes on with that input: a delivery naming
    // the task stays allowed until this report is settled (working()).
    const ahead = !failed && fresh.length > 0;
    // Persist the next state with the report obligation. At the catch-up cap,
    // restore() must still resume the pending input if publication is interrupted.
    this.transaction(() => {
      this.setStatus(taskId, ahead ? 'queued' : STATUS_FOR[report.state] ?? 'done');
      this.db.prepare('INSERT OR IGNORE INTO task_report_deliveries(report_id, created_at_ms, while_working) VALUES (?, ?, ?)')
        .run(report.id, this.now(), ahead ? 1 : 0);
    });
    this.flushReports();
    if (ahead) {
      this.request(taskId);
    } else if (ENDED.has(this.get(taskId)?.status)) {
      this.handBackUnseen(taskId, `the task ended (${this.get(taskId).status}) before its worker saw them`);
      if (this.get(taskId).status === 'failed') this.releaseTaskClaims(taskId);
    }
  }

  reportText(task, report, { again = false, working = false } = {}) {
    return [
      `[task report] task=${task.id} state=${report.state} title="${oneLine(clip(task.title, 80))}" requester=${metadataTag(task.requester_name ?? 'unknown')}${report.structured ? '' : ' unstructured=1'}${working ? ' working=1' : ''}${again ? ' undelivered=1' : ''}`,
      `origin: ${task.origin_chat ? `chat=${chatTag(metadataTag(task.origin_chat), task.origin_thread)} ` : ''}source=${task.origin_source} msg=${metadataTag(task.origin_message ?? '')} event=${metadataTag(task.origin_event ?? '')}`,
      ...(working ? [`The requester kept adding to ${task.id} while its worker wrote this, and the worker is on their latest input now: relay this as the result so far with task_ids=["${task.id}"], and say the rest follows in a later [task report].`] : []),
      ...(again ? [`You were shown this report before and have not answered for ${task.id}: relay it now with task_ids=["${task.id}"], or, if a reply you already sent covers it, call stay_silent on its origin event= with task_ids=["${task.id}"].`] : []),
      ...(report.summary ? ['summary:', quoted(clip(report.summary, SUMMARY_CHARS))] : []),
      ...(report.draft ? ['draft:', quoted(report.draft)] : []),
      ...(report.question ? ['question:', quoted(clip(report.question, SUMMARY_CHARS))] : []),
    ].join('\n');
  }

  relayReport(task, report) {
    const item = {
      text: '',
      seq: null,
      // Runtimes report which of these a router turn held when it ends.
      receiptId: `task-report:${report.id}`,
      scopeKey: task.origin_scope,
      chatType: 'p2p',
      scope: { kind: 'task', key: task.origin_scope },
    };
    const meta = {
      chatId: task.origin_chat, threadId: task.origin_thread, source: task.origin_source, instance: task.origin_instance,
      kind: 'task.report', chatType: task.origin_chat_type, from: `task ${task.id}`,
    };
    const entry = this.transaction(() => {
      const delivery = this.db.prepare('SELECT * FROM task_report_deliveries WHERE report_id = ?').get(report.id);
      if (!delivery || delivery.inbox_id != null || delivery.settled_at_ms != null) return null;
      item.text = this.reportText(task, report, { again: delivery.offers > 0, working: Boolean(delivery.while_working) });
      const entry = this.inbox.persist(item, meta);
      this.db.prepare('UPDATE task_reports SET relayed_at_ms = ? WHERE id = ?').run(this.now(), report.id);
      this.db.prepare('UPDATE task_report_deliveries SET inbox_id = ? WHERE report_id = ?').run(entry.id, report.id);
      return entry;
    });
    if (entry) {
      this.inbox.publish(entry);
      this.recordActivity('task.reported', { summary: `${task.id} ${report.state}`, inboundSeq: task.origin_seq,
        metadata: { task: task.id, state: report.state, structured: Boolean(report.structured) } });
    }
  }

  // The router answered for these tasks: by naming them (task_ids on a
  // delivery or a silence), or with a delivery or silence on a task's origin.
  // Reports it was shown for them are settled. An answer to a later input of
  // a task settles nothing: it may be an acknowledgement.
  settleReports({ taskIds = [], seqs = [], by = null, outboxId = null } = {}) {
    const ids = new Set(taskIds.filter((id) => this.get(id)));
    for (const seq of seqs.filter((value) => value != null)) {
      for (const row of this.db.prepare('SELECT id FROM tasks WHERE origin_seq = ?').all(seq)) ids.add(row.id);
    }
    let settled = 0;
    const settle = this.db.prepare(`UPDATE task_report_deliveries SET settled_at_ms = ?, settled_by = ?, outbox_id = ?
      WHERE settled_at_ms IS NULL AND inbox_id IS NOT NULL AND report_id IN (SELECT id FROM task_reports WHERE task_id = ?)`);
    for (const id of ids) settled += Number(settle.run(this.now(), by, outboxId ?? null, id).changes);
    return settled;
  }

  // Tasks among these whose last report is out of date: new input from the
  // requester's side came in and their worker is on it again. Not one whose
  // report went out past the catch-up turns: that one is the result so far.
  working(taskIds = []) {
    const ahead = this.db.prepare(`SELECT 1 FROM task_report_deliveries d JOIN task_reports r ON r.id = d.report_id
      WHERE r.task_id = ? AND d.while_working = 1 AND d.settled_at_ms IS NULL LIMIT 1`);
    return taskIds.filter((id) => ['queued', 'running'].includes(this.get(id)?.status) && !ahead.get(id));
  }

  // A router turn ended. A report it was shown and did not settle is offered
  // again: after a failed turn as it was, after a finished one once more,
  // marked undelivered. Past that, it is logged and left.
  routerTurnEnded({ receiptIds = [], ok = true } = {}) {
    const ids = [...new Set(receiptIds.map((value) => RECEIPT.exec(String(value))?.[1]).filter(Boolean).map(Number))];
    let offered = 0;
    for (const id of ids) {
      const delivery = this.db.prepare('SELECT * FROM task_report_deliveries WHERE report_id = ?').get(id);
      if (!delivery || delivery.settled_at_ms != null || delivery.inbox_id == null) continue;
      const limit = ok ? 1 : REOFFERS_AFTER_FAILURE;
      if (delivery.offers >= limit) {
        this.db.prepare("UPDATE task_report_deliveries SET settled_at_ms = ?, settled_by = 'unrelayed' WHERE report_id = ?").run(this.now(), id);
        const report = this.db.prepare('SELECT task_id FROM task_reports WHERE id = ?').get(id);
        this.logger?.warn('task_report_unrelayed', { report: id, task: report?.task_id });
        this.recordActivity('task.report_unrelayed', { summary: `${report?.task_id} report ${id} was never relayed`, status: 'FAILED',
          metadata: { task: report?.task_id, report: id } });
        continue;
      }
      this.db.prepare('UPDATE task_report_deliveries SET inbox_id = NULL, offers = offers + 1 WHERE report_id = ?').run(id);
      offered += 1;
    }
    if (offered) this.flushReports();
    return offered;
  }

  flushReports() {
    if (this.stopped) return;
    let failed = false;
    for (const report of this.db.prepare(`SELECT r.* FROM task_report_deliveries d JOIN task_reports r ON r.id = d.report_id
      WHERE d.inbox_id IS NULL AND d.settled_at_ms IS NULL ORDER BY r.id`).all()) {
      try {
        this.relayReport(this.get(report.task_id), report);
      } catch (error) {
        failed = true;
        this.logger?.error('task_report_relay_failed', { task: report.task_id, error: safeError(error) });
      }
    }
    if (failed && !this.reportRetry) {
      this.reportRetry = setTimeout(() => { this.reportRetry = null; this.flushReports(); }, REPORT_RETRY_MS);
      this.reportRetry.unref?.();
    }
  }

  // Tasks a previous daemon generation was running start again with a
  // recovery note; queued ones go back in line.
  restore() {
    const hasDecisions = Boolean(this.db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'decisions'").get());
    let released = [];
    this.transaction(() => {
      this.db.exec('DELETE FROM task_input_claims');
      // Versions before task-aware reminders recorded task routing as a final
      // silent decision. Replace those rows with the task claim, except where
      // the router has already settled the final report.
      if (hasDecisions) this.db.exec(`DELETE FROM decisions WHERE outcome = 'silent' AND seq IN (
        SELECT i.seq FROM task_inputs i JOIN tasks t ON t.id = i.task_id
        WHERE i.role = 'owner' AND i.seq IS NOT NULL AND i.handed_back_at_ms IS NULL
          AND NOT EXISTS (
            SELECT 1 FROM task_reports r JOIN task_report_deliveries d ON d.report_id = r.id
            WHERE r.task_id = t.id AND d.settled_at_ms IS NOT NULL AND d.settled_by != 'unrelayed'
          )
      )`);
      // A partial/corrupt old write must not eat an input forever. There is no
      // foreign key on purpose, so reconcile orphaned claims explicitly.
      const orphaned = this.db.prepare(`SELECT DISTINCT i.seq FROM task_inputs i LEFT JOIN tasks t ON t.id = i.task_id
        WHERE t.id IS NULL AND i.role = 'owner' AND i.seq IS NOT NULL AND i.handed_back_at_ms IS NULL`).all();
      const ended = this.db.prepare(`SELECT DISTINCT i.seq FROM task_inputs i JOIN tasks t ON t.id = i.task_id
        WHERE t.status IN ('failed', 'stopped') AND i.role = 'owner' AND i.seq IS NOT NULL
          AND i.handed_back_at_ms IS NULL`).all();
      released = [...new Set([...orphaned, ...ended].map((row) => Number(row.seq)))];
      this.db.exec('DELETE FROM task_inputs WHERE task_id NOT IN (SELECT id FROM tasks)');
      // Also recover final reports left unmarked by versions before the
      // durable outbox. Only the latest final report of a settled task is eligible.
      this.db.prepare(`INSERT OR IGNORE INTO task_report_deliveries(report_id, created_at_ms)
        SELECT r.id, ? FROM tasks t JOIN task_reports r ON r.task_id = t.id
        WHERE t.status IN ('done', 'waiting', 'blocked', 'dropped', 'failed') AND r.state != 'progress'
          AND r.relayed_at_ms IS NULL AND r.id = (SELECT MAX(id) FROM task_reports WHERE task_id = t.id)`).run(this.now());
      this.db.exec("UPDATE tasks SET native_session = NULL WHERE status IN ('queued', 'running')");
      // Reports a router turn of the previous daemon held and never settled.
      this.db.exec(`UPDATE task_report_deliveries SET inbox_id = NULL, offers = offers + 1
        WHERE settled_at_ms IS NULL AND inbox_id IS NOT NULL AND offers < ${REOFFERS_AFTER_FAILURE}
          AND NOT EXISTS (SELECT 1 FROM inbox_pending i WHERE i.id = task_report_deliveries.inbox_id)`);
    });
    this.flushReports();
    for (const task of this.db.prepare("SELECT * FROM tasks WHERE status IN ('queued', 'running') ORDER BY updated_at_ms").all()) {
      if (task.status === 'running') this.db.prepare("UPDATE tasks SET status = 'queued', recovering = 1 WHERE id = ?").run(task.id);
      this.request(task.id);
    }
    if (released.length) this.releaseClaims(released, 'restore');
  }

  // ---- owner command (/tasks) ------------------------------------------------

  chatName(chatId) {
    if (!chatId) return null;
    try { return this.db.prepare('SELECT name FROM chat_sources WHERE chat_id = ?').get(chatId)?.name ?? null; } catch { return null; }
  }

  // A task as /tasks shows it: the row, when its running turn started, where
  // it was asked, and what became of its latest final report.
  facts(task) {
    const delivery = this.delivery(task);
    return {
      ...task,
      turnStartedAt: this.running.get(task.id)?.startedAt ?? null,
      chatName: this.chatName(task.origin_chat),
      report: delivery ? { id: delivery.reportId, state: delivery.reportState, createdAt: delivery.createdAt,
        delivery: delivery.state, sentAt: delivery.sent?.at ?? null } : null,
    };
  }

  overview({ recent = 5 } = {}) {
    const live = this.db.prepare(`SELECT * FROM tasks WHERE status NOT IN ('done', 'dropped', 'failed', 'stopped')
      ORDER BY created_at_ms`).all();
    const ended = this.db.prepare(`SELECT * FROM tasks WHERE status IN ('done', 'dropped', 'failed', 'stopped')
      ORDER BY ended_at_ms DESC LIMIT ?`).all(recent);
    const reserved = [...this.running.values()].filter((slot) => slot.reserved).length;
    return {
      live: live.map((task) => this.facts(task)),
      ended: ended.map((task) => this.facts(task)),
      total: Number(this.db.prepare('SELECT COUNT(*) AS n FROM tasks').get().n),
      slots: { used: this.running.size - reserved, of: this.slots, reserved, reservedOf: this.reservedSlots },
    };
  }

  detail(taskId) {
    const task = this.get(taskId);
    if (!task) return null;
    let lastError = null;
    try {
      lastError = this.db.prepare(`SELECT summary, occurred_at_ms FROM activities WHERE kind = 'task.turn_completed' AND status = 'failed'
        AND detail_json LIKE ? ORDER BY id DESC LIMIT 1`).get(`%"task":"${task.id}"%`) ?? null;
    } catch { /* no activity table (tests) */ }
    return {
      ...this.facts(task),
      inputs: this.db.prepare('SELECT * FROM task_inputs WHERE task_id = ? ORDER BY id').all(task.id),
      reports: this.db.prepare(`SELECT r.*, d.inbox_id, d.settled_at_ms, d.settled_by, d.offers FROM task_reports r
        LEFT JOIN task_report_deliveries d ON d.report_id = r.id WHERE r.task_id = ? ORDER BY r.id`).all(task.id),
      children: this.db.prepare('SELECT id, title, status FROM tasks WHERE parent_id = ? ORDER BY created_at_ms').all(task.id),
      lastError,
    };
  }

  // Worker turns in flight end with the daemon; their tasks stay `running`
  // and restore() starts them again.
  shutdown() {
    this.stopped = true;
    clearTimeout(this.reportRetry);
    this.reportRetry = null;
    for (const slot of this.running.values()) slot.handle?.stop?.();
  }
}
