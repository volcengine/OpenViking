// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { chatTag, metadataTag, oneLine } from './runtime-input.mjs';
import { safeError } from './logger.mjs';
import { emptyTrash, formatBytes, isDirectory, isDirectoryWithin, moveToTrash, scanDir, TRASH } from './task-dirs.mjs';

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
// A task is one piece of work someone keeps pushing forward (an answer, a
// fix, a pull request, a document), not a conversation. Only inputs about
// that work reach it. Each request on it is a round: the one that created
// it, and each one that reopens it after it ended (task_rounds), in its own
// session and directory, the answer going where that round was asked. Work
// for another result is a new task, which may follow this one (parent_id) and
// start from what it produced. A worker hands back what it is given that is
// not its task (a round it hands back entirely never happened), and a task
// that ends with inputs it never saw hands those back too: nothing routed is
// dropped.
//
// A report is settled when the router answers for its task (a delivery or a
// silence naming it with task_ids, or made on its round's origin), not when
// the router was shown it: one the router was shown and left is offered again,
// so a report is neither lost to a failed router turn nor sent twice.
//
// Each task works in its own directory, <workspace>/tasks/<id>. What ended
// tasks leave there is deleted in stages (sweepDirs): dependency folders
// first, then the whole directory with its worker's sessions, sooner past a
// disk budget.

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
// Tasks of one thread an input's hint may draw on past the working set.
const THREAD_TASKS = 10;
// How much of a nearby task's current request its hint shows.
const HINT_REQUEST_CHARS = 40;
// Ended tasks' directories: dependency folders are deleted this many days
// after the task ended, the whole directory and its worker's sessions after
// DIR_REMOVE_DAYS, and past the budget the directories of the tasks that ended
// longest ago go first. The config overrides each; 0 turns it off.
export const DIR_PRUNE_DAYS = 3;
export const DIR_REMOVE_DAYS = 14;
export const DIR_BUDGET_GB = 20;
const SWEEP_DELAY_MS = 60_000;
const SWEEP_MS = 3_600_000;

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
  -- A task directory's clean-up: pruned_at_ms, its dependency folders were
  -- last deleted; removed_at_ms, the directory itself is gone.
  CREATE TABLE IF NOT EXISTS task_workdirs (
    path TEXT PRIMARY KEY,
    pruned_at_ms INTEGER,
    removed_at_ms INTEGER
  );
  -- A task's rounds: the request that created it (round 1) and each one that
  -- reopened it after it ended. A round's origin is where its answer goes and
  -- who asked. first_turn: the task's turn the round started with;
  -- reopened_from: the status it ended with before; workdir_state and
  -- session_state: what the round found of its directory (kept, pruned,
  -- removed) and of its worker's session (kept, deleted, large, none);
  -- outcome: how it ended, or handed_back when its worker handed back every
  -- request line of it.
  CREATE TABLE IF NOT EXISTS task_rounds (
    task_id TEXT NOT NULL,
    round INTEGER NOT NULL,
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
    first_turn INTEGER NOT NULL DEFAULT 1,
    reopened_from TEXT,
    workdir_state TEXT,
    session_state TEXT,
    opened_at_ms INTEGER NOT NULL,
    ended_at_ms INTEGER,
    outcome TEXT,
    PRIMARY KEY (task_id, round)
  );
  CREATE INDEX IF NOT EXISTS task_rounds_origin ON task_rounds(origin_seq);
`;

const ORIGIN = ['origin_seq', 'origin_event', 'origin_instance', 'origin_scope', 'origin_chat', 'origin_thread', 'origin_message',
  'origin_chat_type', 'origin_source', 'requester_id', 'requester_name'];

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
  // The task's current round, and the round each input and report belongs to.
  ['tasks', 'round', 'INTEGER NOT NULL DEFAULT 1'],
  ['task_inputs', 'round', 'INTEGER NOT NULL DEFAULT 1'],
  ['task_reports', 'round', 'INTEGER NOT NULL DEFAULT 1'],
];

// Lookups on every input line, so that they cost the same with ten thousand
// tasks as with ten: the working set by end time, a message's task, the tasks
// asked in a thread. Created after COLUMNS (outbox_id is a later column).
const INDEXES = `
  CREATE INDEX IF NOT EXISTS tasks_ended ON tasks(ended_at_ms);
  CREATE INDEX IF NOT EXISTS task_inputs_seq ON task_inputs(seq);
  CREATE INDEX IF NOT EXISTS task_report_deliveries_outbox ON task_report_deliveries(outbox_id);
  CREATE INDEX IF NOT EXISTS task_rounds_thread ON task_rounds(origin_chat, origin_thread);
`;

// A session larger than this is not carried on into a new round: the round
// starts a fresh one from the task record, as when the session was lost.
export const ROUND_SESSION_MAX_BYTES = 4 * 1024 * 1024;

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
    slots = WORKER_SLOTS,
    reservedSlots = RESERVED_SLOTS,
    now = Date.now,
  }) {
    if (!db) throw new TypeError('db is required');
    if (!executor) throw new TypeError('executor is required');
    if (!inbox || inbox.db !== db) throw new TypeError('inbox must share the task database');
    Object.assign(this, { db, config, executor, inbox, lineFor, logger, recordActivity, slots, reservedSlots, now });
    this.db.exec(SCHEMA);
    for (const [table, column, type] of COLUMNS) {
      if (this.db.prepare(`PRAGMA table_info(${table})`).all().some((row) => row.name === column)) continue;
      this.db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${type}`);
      // Reports an older daemon put in the inbox counted as delivered there.
      if (column === 'settled_at_ms') this.db.exec('UPDATE task_report_deliveries SET settled_at_ms = created_at_ms WHERE inbox_id IS NOT NULL');
    }
    this.db.exec(INDEXES);
    // Tasks from before rounds had one: the request that created them.
    this.db.exec(`INSERT OR IGNORE INTO task_rounds(task_id, round, ${ORIGIN.join(', ')}, opened_at_ms, ended_at_ms, outcome)
      SELECT id, 1, ${ORIGIN.join(', ')}, created_at_ms, ended_at_ms,
        CASE WHEN status IN ('done', 'dropped', 'failed', 'stopped') THEN status END
      FROM tasks WHERE round = 1 AND id NOT IN (SELECT task_id FROM task_rounds)`);
    // Worker authority tokens: token -> task id, for the turn that holds it.
    this.tokens = new Map();
    // Tasks with a turn to run, in the order they asked for it.
    this.pending = [];
    this.running = new Map();
    this.catchUp = new Map();
    this.retries = new Map();
    this.reportRetry = null;
    this.stopped = false;
    this.sweepTimers = null;
    this.sweeping = null;
    // Task directories on disk at the last sweep, after its clean-up.
    this.disk = null;
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
  // A task has an end time exactly while it is ended (setStatus, stop,
  // openRound, runTurn keep that), so both reads walk the end-time index
  // instead of every task.
  workingSet() {
    const live = this.db.prepare(`SELECT * FROM tasks WHERE ended_at_ms IS NULL
      AND status NOT IN ('done', 'dropped', 'failed', 'stopped') ORDER BY created_at_ms`).all();
    const ended = this.db.prepare(`SELECT * FROM tasks WHERE ended_at_ms IS NOT NULL
      AND status IN ('done', 'dropped', 'failed', 'stopped') ORDER BY ended_at_ms DESC LIMIT ?`).all(RECENT_ENDED);
    return [...live, ...ended];
  }

  // Tasks asked in a thread, any round, the latest first: the exact signal
  // reaches past the working set.
  threadTasks(chatId, threadId) {
    if (!chatId || !threadId) return [];
    return this.db.prepare(`SELECT task_id, MAX(opened_at_ms) AS at FROM task_rounds WHERE origin_chat = ? AND origin_thread = ?
      GROUP BY task_id ORDER BY at DESC LIMIT ?`).all(chatId, threadId, THREAD_TASKS).map((row) => row.task_id);
  }

  // One round of a task (its current one by default): where it was asked and
  // by whom. A task without the row (never, after the backfill) answers for
  // its round 1 with its own origin.
  roundOf(task, round = task.round ?? 1) {
    const row = this.db.prepare('SELECT * FROM task_rounds WHERE task_id = ? AND round = ?').get(task.id, round);
    if (row) return row;
    return Object.fromEntries([['task_id', task.id], ['round', round], ['first_turn', 1], ...ORIGIN.map((key) => [key, task[key] ?? null])]);
  }

  rounds(taskId) {
    return this.db.prepare('SELECT * FROM task_rounds WHERE task_id = ? ORDER BY round').all(taskId);
  }

  // Scopes where a task is still at work on a round: its OnIt stays up there.
  liveScopes() {
    return new Set(this.db.prepare(`SELECT COALESCE(r.origin_scope, t.origin_scope) AS scope FROM tasks t
      LEFT JOIN task_rounds r ON r.task_id = t.id AND r.round = t.round WHERE t.status IN ('queued', 'running')`).all()
      .map((row) => row.scope).filter(Boolean));
  }

  // The live task that holds this input line as part of its request, if any.
  liveHolder(seq) {
    return this.db.prepare(`SELECT t.id FROM task_inputs i JOIN tasks t ON t.id = i.task_id
      WHERE i.seq = ? AND i.role = 'owner' AND i.handed_back_at_ms IS NULL
        AND t.status NOT IN ('done', 'dropped', 'failed', 'stopped') LIMIT 1`).get(seq)?.id ?? null;
  }

  workerFor(token) {
    return typeof token === 'string' && this.tokens.has(token) ? this.tokens.get(token) : null;
  }

  card(task, { detail = false } = {}) {
    const where = task.origin_chat ? `chat=${chatTag(metadataTag(task.origin_chat), task.origin_thread)}` : `source=${task.origin_source}`;
    const delivery = this.delivery(task);
    const rounds = task.round > 1 ? ` (${task.round} rounds)` : '';
    const head = `${task.id} [${task.status}] "${oneLine(task.title)}" for ${task.requester_name ?? 'unknown'} in ${where}${rounds}${task.parent_id ? ` (follows ${task.parent_id})` : ''}${this.progressNote(task, delivery)}`;
    if (!detail) return head;
    const sent = delivery?.sent;
    return [head,
      `origin: msg=${metadataTag(task.origin_message ?? '')} event=${metadataTag(task.origin_event ?? '')}`,
      ...(task.round > 1 ? this.rounds(task.id).slice(1).map((round) => `round ${round.round}: from ${round.requester_name ?? 'unknown'}`
        + ` msg=${metadataTag(round.origin_message ?? '')} event=${metadataTag(round.origin_event ?? '')}${round.outcome ? ` (${round.outcome})` : ''}`) : []),
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
  // its way. 'settled' is a report settled before this was recorded. A round
  // its worker handed back has no result.
  delivery(task) {
    const row = this.db.prepare(`SELECT r.id, r.state, r.created_at_ms, d.inbox_id, d.settled_at_ms, d.settled_by, d.outbox_id
      FROM task_reports r LEFT JOIN task_report_deliveries d ON d.report_id = r.id
      WHERE r.task_id = ? AND r.state != 'progress' AND NOT EXISTS (SELECT 1 FROM task_rounds x
        WHERE x.task_id = r.task_id AND x.round = r.round AND x.outcome = 'handed_back')
      ORDER BY r.id DESC LIMIT 1`).get(task.id);
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

  // requester: { id, chatId, direct } lists what one person asked for (a
  // round of theirs counts), ended tasks included, the latest at work first:
  // in a group only what was asked in that chat, in a direct chat everything
  // they asked for.
  list({ taskId = null, query = '', includeEnded = false, requester = null } = {}) {
    if (taskId) {
      const task = this.get(taskId);
      return task ? [this.card(task, { detail: true })] : [];
    }
    let rows = requester
      ? this.db.prepare(`SELECT * FROM tasks WHERE id IN (SELECT task_id FROM task_rounds
            WHERE requester_id = ?${requester.direct ? '' : ' AND origin_chat = ?'})
          ORDER BY updated_at_ms DESC, rowid DESC LIMIT ?`).all(...[requester.id, ...(requester.direct ? [] : [requester.chatId]), ASKER_TASKS])
      : includeEnded ? this.db.prepare('SELECT * FROM tasks ORDER BY created_at_ms DESC LIMIT 200').all() : this.workingSet();
    if (query) {
      const grams = bigrams(query);
      rows = rows.map((task) => ({ task, score: similarity(grams, bigrams(`${task.title} ${task.summary}`)) }))
        .filter((row) => row.score > 0.15).sort((a, b) => b.score - a.score).map((row) => row.task);
    }
    return rows.map((task) => this.card(task));
  }

  // Message ids that belong to a task: its routed inputs and what the router
  // sent for it: outbox rows attributed to the origin of one of its rounds, and
  // the message that relayed one of its reports (the delivery that settled it,
  // whatever input the router decided it on).
  taskForMessage(messageId) {
    if (!messageId) return null;
    const viaInput = this.db.prepare(`SELECT i.task_id FROM task_inputs i JOIN connector_events e ON e.seq = i.seq
      WHERE e.message_id = ? ORDER BY i.id DESC LIMIT 1`).get(messageId);
    if (viaInput) return viaInput.task_id;
    const viaOrigin = this.db.prepare(`SELECT r.task_id AS id FROM task_rounds r JOIN outbox o ON o.inbound_seq = r.origin_seq
      WHERE o.remote_message_id = ? ORDER BY r.opened_at_ms DESC LIMIT 1`).get(messageId)?.id;
    if (viaOrigin) return viaOrigin;
    return this.db.prepare(`SELECT r.task_id AS id FROM task_report_deliveries d JOIN task_reports r ON r.id = d.report_id
      JOIN outbox o ON o.id = d.outbox_id WHERE o.remote_message_id = ? ORDER BY d.settled_at_ms DESC LIMIT 1`).get(messageId)?.id ?? null;
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
    // The working set, and past it the tasks an exact signal names: the one
    // this line replies to and those asked in its thread, however long ago.
    const pool = this.workingSet();
    const known = new Set(pool.map((task) => task.id));
    for (const id of [replyTask, ...this.threadTasks(event.chatId, event.threadId)]) {
      if (!id || known.has(id)) continue;
      const task = this.get(id);
      if (task) { pool.push(task); known.add(id); }
    }
    for (const task of pool) {
      // Where any of its rounds was asked; who it waits for is its round's.
      const rounds = this.rounds(task.id);
      const asked = rounds.length ? rounds : [this.roundOf(task, 1)];
      const current = asked.find((round) => round.round === task.round) ?? asked.at(-1);
      const sameChat = asked.some((round) => round.origin_chat && round.origin_chat === event.chatId);
      if (!owner && !sameChat && replyTask !== task.id) continue;
      if (replyTask === task.id) add(task, 'replies to its message', 5);
      if (event.threadId && asked.some((round) => round.origin_chat === event.chatId && round.origin_thread === event.threadId)) {
        add(task, 'same thread', 4);
      }
      if (task.status === 'waiting' && current.requester_id === event.senderId && current.origin_scope === event.scopeKey) {
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
  // for whom, or ended and how long ago, and how many rounds it had.
  state(task) {
    const rounds = task.round > 1 ? `, ${task.round} rounds` : '';
    if (task.status === 'running' || task.status === 'queued') return `working${rounds}`;
    if (task.status === 'waiting') return `waiting for ${this.roundOf(task).requester_name ?? task.requester_name ?? 'the requester'}${rounds}`;
    if (!ENDED.has(task.status)) return `${task.status}${rounds}`;
    return `${task.status} ${this.ago(task.ended_at_ms ?? task.updated_at_ms)} ago${rounds}`;
  }

  // The daemon line an input carries when tasks may relate to it: facts about
  // where the input is and what each task is doing, with the request of its
  // current round (or its last, once it ended): a title is written when a task
  // is created and says nothing of the rounds since. Whether the input belongs
  // to one is the router's call, made on what the input asks for.
  hintFor(event, options) {
    const hits = this.candidatesFor(event, options);
    if (hits.length === 0) return '';
    return `[daemon tasks] near this input: ${hits.map(({ task, reasons }) => {
      const asked = this.roundRequest(task);
      return `${task.id} [${this.state(task)}] "${oneLine(clip(task.title, 60))}"${asked ? ` ${ENDED.has(task.status) ? 'last asked' : 'now on'}: "${asked}"` : ''} (${reasons.join(', ')})`;
    }).join(' · ')}`;
  }

  // The start of what a task's current round asked, without the line's metadata.
  roundRequest(task) {
    const round = this.roundOf(task);
    const row = this.db.prepare(`SELECT text FROM task_inputs WHERE task_id = ? AND round = ? AND role = 'owner'
      AND handed_back_at_ms IS NULL ORDER BY (seq = ?) DESC, id LIMIT 1`).get(task.id, task.round ?? 1, round.origin_seq ?? -1);
    // The words after the line's metadata and any leading @mentions.
    const text = oneLine(String(row?.text ?? '').replace(/^\[[^\]]*\]\s*/, '').replace(/@_user_\d+\s*/g, '')).replace(/^(?:@\S+\s+)+/, '').trim();
    return text ? clip(text, HINT_REQUEST_CHARS).replace(/"/g, "'") : '';
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
      this.insertRound(id, 1, origin, requester, { firstTurn: 1 });
      for (const row of rows) this.addInput(id, row, 'owner', 1);
    });
    this.recordActivity('task.created', { summary: `${id} ${title}`, inboundSeq: origin.seq, metadata: { task: id, ...(parent ? { parent } : {}) } });
    this.request(id);
    return this.get(id);
  }

  addInput(taskId, row, role, round = this.get(taskId)?.round ?? 1) {
    const text = this.lineFor(row);
    this.db.prepare('INSERT INTO task_inputs(task_id, seq, role, text, created_at_ms, round) VALUES (?, ?, ?, ?, ?, ?)')
      .run(taskId, row.seq ?? null, role, text, this.now(), round);
  }

  insertRound(taskId, round, origin, requester, { firstTurn, reopenedFrom = null, workdir = null, session = null }) {
    this.db.prepare(`INSERT INTO task_rounds(task_id, round, ${ORIGIN.join(', ')}, first_turn, reopened_from, workdir_state,
      session_state, opened_at_ms) VALUES (${['?', '?', ...ORIGIN.map(() => '?'), '?', '?', '?', '?', '?'].join(', ')})`).run(
      taskId, round, origin.seq ?? null, origin.event_id ?? null, origin.connector_instance ?? null, origin.scope_key ?? null,
      origin.chat_id ?? null, origin.thread_id ?? null, origin.message_id ?? null, origin.chat_type ?? null, origin.source ?? null,
      origin.sender_id ?? null, requester, firstTurn, reopenedFrom, workdir, session, this.now(),
    );
  }

  // Input for a task: `owner` inputs change or answer the work it is doing
  // and reach its worker while it works (or wake a waiting one); `context`
  // ones are background. An owner input to a task that ended is a new
  // request on the same work: it reopens the task for a round of its own
  // (openRound). A stopped task stays stopped, and background reopens nothing.
  route(taskId, rows, role, { requester = null } = {}) {
    const task = this.get(taskId);
    if (!task) throw new Error(`no task ${taskId}`);
    const ended = ENDED.has(task.status);
    if (task.status === 'stopped') {
      throw Object.assign(new Error(`${taskId} was stopped. Work on it again is a new task: task_create with follows=${taskId}`), { code: 'task_ended' });
    }
    if (ended && role !== 'owner') {
      throw Object.assign(new Error(`${taskId} has ended (${task.status}): a context line does not reopen it`), { code: 'task_ended' });
    }
    const known = new Set(this.db.prepare('SELECT seq FROM task_inputs WHERE task_id = ? AND seq IS NOT NULL').all(taskId).map((row) => row.seq));
    rows = rows.filter((row) => !known.has(row.seq));
    if (rows.length === 0) return task;
    const opened = this.transaction(() => {
      const round = ended ? this.openRound(task, rows[0], requester) : null;
      for (const row of rows) this.addInput(taskId, row, role, round?.round ?? task.round);
      this.touch(taskId);
      if (role === 'owner' && (task.status === 'waiting' || task.status === 'blocked')) this.setStatus(taskId, 'queued');
      return round;
    });
    if (opened) {
      this.recordActivity('task.reopened', { summary: `${taskId} round ${opened.round}`, inboundSeq: rows[0].seq ?? null,
        metadata: { task: taskId, round: opened.round, from: task.status, workdir: opened.workdir_state, session: opened.session_state } });
    }
    if (role === 'owner') this.request(taskId);
    return this.get(taskId);
  }

  // A task that ended takes a new request: the next round, asked by origin.
  // Its directory is made whole again (the sweep may have pruned or removed
  // it; a live task's is never touched after this), and its session is
  // carried on only when it is still there and not too large: otherwise the
  // round starts a fresh session from the task record (brief).
  openRound(task, origin, requester) {
    const number = (task.round ?? 1) + 1;
    const dir = this.dirState(task.workdir);
    const workdir = dir?.removed_at_ms != null || !isDirectory(task.workdir) ? 'removed' : dir?.pruned_at_ms != null ? 'pruned' : 'kept';
    fs.mkdirSync(task.workdir, { recursive: true });
    this.db.prepare('DELETE FROM task_workdirs WHERE path = ?').run(task.workdir);
    const session = !task.native_session ? (workdir === 'removed' ? 'deleted' : 'none')
      : this.executor.sessionState?.(task, { maxBytes: ROUND_SESSION_MAX_BYTES }) ?? 'kept';
    this.insertRound(task.id, number, origin, requester, { firstTurn: task.turns + 1, reopenedFrom: task.status, workdir, session });
    this.db.prepare(`UPDATE tasks SET round = ?, status = 'queued', ended_at_ms = NULL, updated_at_ms = ?,
      native_session = ? WHERE id = ?`).run(number, this.now(), session === 'kept' ? task.native_session : null, task.id);
    return this.roundOf({ ...task, round: number });
  }

  note(taskId, text) {
    const task = this.get(taskId);
    if (!task) throw new Error(`no task ${taskId}`);
    this.db.prepare('INSERT INTO task_inputs(task_id, seq, role, text, created_at_ms, round) VALUES (?, NULL, ?, ?, ?, ?)')
      .run(taskId, 'note', `[note from the agent] ${oneLine(clip(text, 600))}`, this.now(), task.round ?? 1);
  }

  stop(taskId, reason = '') {
    const task = this.get(taskId);
    if (!task) throw new Error(`no task ${taskId}`);
    if (ENDED.has(task.status)) return task;
    this.pending = this.pending.filter((id) => id !== taskId);
    const slot = this.running.get(taskId);
    slot?.handle?.stop?.();
    this.db.prepare("UPDATE tasks SET status = 'stopped', ended_at_ms = ?, updated_at_ms = ? WHERE id = ?").run(this.now(), this.now(), taskId);
    this.endRound(task, 'stopped');
    this.recordActivity('task.ended', { summary: `${taskId} stopped${reason ? `: ${reason}` : ''}`, metadata: { task: taskId, status: 'stopped' } });
    this.handBackUnseen(taskId, 'the task was stopped before its worker saw them');
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
    if (ENDED.has(status)) this.endRound(this.get(taskId), status);
  }

  endRound(task, outcome) {
    if (!task) return;
    this.db.prepare('UPDATE task_rounds SET ended_at_ms = ?, outcome = ? WHERE task_id = ? AND round = ?')
      .run(this.now(), outcome, task.id, task.round ?? 1);
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
    this.db.prepare(`INSERT INTO task_reports(task_id, state, summary, draft, question, structured, created_at_ms, round)
      VALUES (?, ?, ?, ?, ?, 1, ?, ?)`).run(taskId, state, clip(summary, 4_000), clip(draft, 20_000), clip(question, 2_000), this.now(), task.round ?? 1);
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
    const round = (task.round ?? 1) > 1 ? this.roundOf(task) : null;
    // The first turn of a round that reopened the task.
    const opening = Boolean(round) && task.turns + 1 === round.first_turn;
    const recovery = task.turns > 0 && !task.native_session;
    const lost = opening && recovery && ['deleted', 'large'].includes(round.session_state);
    const prefix = task.recovering
      ? ['The daemon restarted while you were working on this task. Check your directory and what you already did (files, commits, issues, comments) before repeating any outside action.']
      : lost ? [round.session_state === 'large'
        ? 'Your session from the earlier rounds of this task grew too long to carry on: you start a fresh one from this brief, rebuilt from the task record.'
        : 'Your session from the earlier rounds of this task was deleted to save disk some days after it ended: this brief is rebuilt from the task record.']
      : recovery ? ['The previous worker session could not be safely resumed. Check existing work before repeating any outside action.'] : [];
    if (recovery) inputs = this.db.prepare('SELECT * FROM task_inputs WHERE task_id = ? AND handed_back_at_ms IS NULL ORDER BY id').all(task.id);
    const owner = inputs.filter((row) => row.role === 'owner');
    const context = inputs.filter((row) => row.role !== 'owner');
    const dirNote = opening ? this.roundDirBrief(task, round) : [];
    if (task.turns === 0 || recovery) {
      const where = `${task.origin_chat ? ` in chat=${chatTag(metadataTag(task.origin_chat), task.origin_thread)}` : ''} (source=${task.origin_source})`;
      return [
        ...prefix,
        `[task ${task.id}] ${task.title}`,
        ...(round
          ? [`Requested by ${task.requester_name ?? 'unknown'}${where}. This task has had ${task.round} rounds, each a request on the same work, answered in turn; the last is the one to do now. The requests, as the agent received them:`,
            ...this.roundLines(task, owner)]
          : [`Requested by ${task.requester_name ?? 'unknown'}${where}. The request, as the agent received it:`, ...owner.map((row) => row.text)]),
        ...(context.length ? ['Context:', ...context.map((row) => row.text)] : []),
        ...(task.note ? [`Routing note from the agent: ${task.note}`] : []),
        ...this.parentBrief(task),
        `Your directory: ${task.workdir}`,
        ...dirNote,
        ...(recovery && task.summary ? [opening ? 'What your earlier rounds found:' : 'Previous work summary (may be incomplete):', quoted(task.summary)] : []),
        ...(recovery && task.draft ? [opening ? 'Your answer to the previous round (the requester has it):' : 'Previous draft (may be incomplete):', quoted(task.draft)] : []),
        ...(recovery && task.artifacts_json !== '[]' ? [`Previous artifacts: ${task.artifacts_json}`] : []),
        round
          ? `Work on the request of round ${task.round}, then finish with task_report: its result answers that request. You cannot message anyone: the agent replies to the requester from your report.`
          : 'Work on this request, then finish with task_report. You cannot message anyone: the agent replies to the requester from your report.',
      ].join('\n');
    }
    if (opening) {
      return [
        `[task ${task.id}] Round ${task.round}: a new request on this task, from ${round.requester_name ?? 'the requester'}. Your result for the previous round went to the requester; this is more work on the same task.`,
        ...owner.map((row) => row.text),
        ...(context.length ? ['Context only (act on it only if it changes your task):', ...context.map((row) => row.text)] : []),
        ...dirNote,
        'Before you start, check the state of your directory and of what you changed outside it (a branch may have moved or been merged since). Finish with task_report: its result answers this request.',
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

  // A rebuilt brief's requests, round by round. Rounds handed back entirely
  // were never this task's.
  roundLines(task, owner) {
    return this.rounds(task.id).filter((round) => round.outcome !== 'handed_back').flatMap((round) => [
      `Round ${round.round}${round.round === task.round ? ' (now)' : ''}, from ${round.requester_name ?? 'unknown'}${round.round !== task.round && round.outcome ? ` (${round.outcome})` : ''}:`,
      ...owner.filter((row) => (row.round ?? 1) === round.round).map((row) => row.text),
    ]);
  }

  // What a round found of its directory when it reopened the task.
  roundDirBrief(task, round) {
    if (round.workdir_state === 'removed') {
      return [`Your directory (${task.workdir}) was deleted to save disk some days after the task ended, and is empty again: start from your results (artifacts, branches, pull requests, documents) and set up again only what this request needs.`];
    }
    if (round.workdir_state === 'pruned') {
      return ['The dependency folders in your directory (node_modules, virtual environments, build caches) were deleted to save disk after the task ended: install again only what you need.'];
    }
    return [];
  }

  // What a task that follows another starts from: that work's request and
  // results, and where its files are.
  parentBrief(task) {
    const parent = task.parent_id ? this.get(task.parent_id) : null;
    if (!parent) return [];
    const lines = (round) => this.db.prepare(`SELECT text FROM task_inputs WHERE task_id = ? AND role = 'owner' AND round = ?
      AND handed_back_at_ms IS NULL ORDER BY id LIMIT 3`).all(parent.id, round);
    // With several rounds, its last request says where that work stands.
    const last = this.rounds(parent.id).filter((round) => round.round > 1 && round.outcome !== 'handed_back').at(-1);
    const artifacts = JSON.parse(parent.artifacts_json);
    return [
      `This task follows ${parent.id} "${oneLine(clip(parent.title, 80))}" (${parent.status}), earlier work it builds on. Its request:`,
      ...lines(1).map((row) => quoted(clip(row.text, 600))),
      ...(last ? [`Its last request, round ${last.round}:`, ...lines(last.round).map((row) => quoted(clip(row.text, 600)))] : []),
      ...(parent.summary ? ['What that work found:', quoted(clip(parent.summary, SUMMARY_CHARS))] : []),
      ...(parent.draft ? ['Its last answer to the requester:', quoted(clip(parent.draft, PARENT_DRAFT_CHARS))] : []),
      ...(artifacts.length ? [`Its artifacts: ${artifacts.map(oneLine).join(', ')}`] : []),
      ...this.parentDirBrief(parent),
      'Do only what this task asks; check what that work already did before redoing it.',
    ];
  }

  // Where the files of the work a task follows are, after the sweep.
  parentDirBrief(parent) {
    const state = this.dirState(parent.workdir);
    if (state?.removed_at_ms != null) return ['Its directory was deleted to save disk some days after it ended: start from what it reported.'];
    return [
      `Its directory, with whatever it left there: ${parent.workdir}`,
      ...(state?.pruned_at_ms != null ? ['Its dependency folders (node_modules, virtual environments, build caches) were deleted to save disk: install again only what you need.'] : []),
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
    // A passing failure runs the turn again; so does one whose resumed session
    // was gone (its CLI cleaned it up): settleInputs dropped the checkpoint, so
    // the next turn starts a new session from the task record.
    if (failed && (result.transient || result.sessionLost) && (this.retries.get(taskId) ?? 0) < TRANSIENT_RETRIES) {
      this.retries.set(taskId, (this.retries.get(taskId) ?? 0) + 1);
      this.setStatus(taskId, 'queued');
      this.request(taskId);
      return;
    }
    this.retries.delete(taskId);
    if (this.voidRound(task)) return;
    let report = final;
    if (!report) {
      // No final report: the worker's last words are its answer, marked as
      // such; a failed turn reports the failure.
      const state = result.error ? 'failed' : 'done';
      const id = this.db.prepare(`INSERT INTO task_reports(task_id, state, summary, draft, question, structured, created_at_ms, round)
        VALUES (?, ?, ?, ?, '', 0, ?, ?)`).run(taskId, state, clip(result.error ?? structured?.summary ?? '', 4_000),
        clip(result.error ? '' : result.text, 20_000), this.now(), task.round ?? 1).lastInsertRowid;
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
    }
  }

  // A round of a task that reopened, whose every request line its worker
  // handed back: the request was not this task's, and the router decides on
  // it anew. Nothing of the round is relayed (a report its worker filed is
  // kept for the record, settled as handed back), and the task is as it was
  // before: its status, and the summary and answer of its last round.
  voidRound(task) {
    if ((task.round ?? 1) < 2) return false;
    const live = this.db.prepare(`SELECT COUNT(*) AS n FROM task_inputs WHERE task_id = ? AND round = ? AND role = 'owner'
      AND handed_back_at_ms IS NULL`).get(task.id, task.round).n;
    if (Number(live) > 0) return false;
    const round = this.roundOf(task);
    // The last real round's result: not one of an earlier round handed back too.
    const last = this.db.prepare(`SELECT summary, draft FROM task_reports r WHERE task_id = ? AND round < ? AND state != 'progress'
      AND NOT EXISTS (SELECT 1 FROM task_rounds x WHERE x.task_id = r.task_id AND x.round = r.round AND x.outcome = 'handed_back')
      ORDER BY id DESC LIMIT 1`).get(task.id, task.round);
    this.transaction(() => {
      for (const { id } of this.db.prepare("SELECT id FROM task_reports WHERE task_id = ? AND round = ? AND state != 'progress'").all(task.id, task.round)) {
        this.db.prepare(`INSERT OR IGNORE INTO task_report_deliveries(report_id, created_at_ms, settled_at_ms, settled_by)
          VALUES (?, ?, ?, 'handed_back')`).run(id, this.now(), this.now());
      }
      this.db.prepare(`UPDATE tasks SET status = ?, ended_at_ms = ?, updated_at_ms = ?, summary = ?, draft = ?, question = '' WHERE id = ?`)
        .run(ENDED.has(round.reopened_from) ? round.reopened_from : 'done', this.now(), this.now(),
          last?.summary ?? task.summary, last?.draft ?? task.draft, task.id);
      this.endRound(task, 'handed_back');
    });
    this.catchUp.delete(task.id);
    this.recordActivity('task.round_handed_back', { summary: `${task.id} round ${task.round} was handed back entirely`,
      metadata: { task: task.id, round: task.round } });
    return true;
  }

  reportText(task, report, { again = false, working = false } = {}) {
    // The answer goes where the report's round was asked.
    const origin = this.roundOf(task, report.round ?? 1);
    const round = (report.round ?? 1) > 1 || task.round > 1 ? ` round=${report.round ?? 1}` : '';
    return [
      `[task report] task=${task.id}${round} state=${report.state} title="${oneLine(clip(task.title, 80))}" requester=${metadataTag(origin.requester_name ?? task.requester_name ?? 'unknown')}${report.structured ? '' : ' unstructured=1'}${working ? ' working=1' : ''}${again ? ' undelivered=1' : ''}`,
      `origin: ${origin.origin_chat ? `chat=${chatTag(metadataTag(origin.origin_chat), origin.origin_thread)} ` : ''}source=${origin.origin_source} msg=${metadataTag(origin.origin_message ?? '')} event=${metadataTag(origin.origin_event ?? '')}`,
      ...(working ? [`The requester kept adding to ${task.id} while its worker wrote this, and the worker is on their latest input now: relay this as the result so far with task_ids=["${task.id}"], and say the rest follows in a later [task report].`] : []),
      ...(again ? [`You were shown this report before and have not answered for ${task.id}: relay it now with task_ids=["${task.id}"], or, if a reply you already sent covers it, call stay_silent on its origin event= with task_ids=["${task.id}"].`] : []),
      ...(report.summary ? ['summary:', quoted(clip(report.summary, SUMMARY_CHARS))] : []),
      ...(report.draft ? ['draft:', quoted(report.draft)] : []),
      ...(report.question ? ['question:', quoted(clip(report.question, SUMMARY_CHARS))] : []),
    ].join('\n');
  }

  relayReport(task, report) {
    const origin = this.roundOf(task, report.round ?? 1);
    const item = {
      text: '',
      seq: null,
      // Runtimes report which of these a router turn held when it ends.
      receiptId: `task-report:${report.id}`,
      scopeKey: origin.origin_scope,
      chatType: 'p2p',
      scope: { kind: 'task', key: origin.origin_scope },
    };
    const meta = {
      chatId: origin.origin_chat, threadId: origin.origin_thread, source: origin.origin_source, instance: origin.origin_instance,
      kind: 'task.report', chatType: origin.origin_chat_type, from: `task ${task.id}`,
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
  // delivery or a silence), or with a delivery or silence on a round's origin.
  // The reports it was shown are settled, of the rounds that answer covers
  // only: another round's report, asked elsewhere or still to be relayed,
  // stays offered. An answer to a later input of a round, with no task named,
  // settles nothing: it may be an acknowledgement.
  settleReports({ taskIds = [], seqs = [], by = null, outboxId = null } = {}) {
    const decided = seqs.filter((value) => value != null);
    const scopes = new Set(decided.map((seq) => {
      try { return this.db.prepare('SELECT scope_key FROM connector_events WHERE seq = ?').get(seq)?.scope_key; } catch { return null; }
    }).filter(Boolean));
    const targets = [];
    const named = new Set(taskIds.filter((id) => this.get(id)));
    for (const id of named) for (const round of this.roundsAnswered(id, decided, scopes)) targets.push([id, round]);
    for (const seq of decided) {
      for (const row of this.db.prepare('SELECT task_id, round FROM task_rounds WHERE origin_seq = ?').all(seq)) {
        if (!named.has(row.task_id)) targets.push([row.task_id, row.round]);
      }
    }
    let settled = 0;
    const settleTask = this.db.prepare(`UPDATE task_report_deliveries SET settled_at_ms = ?, settled_by = ?, outbox_id = ?
      WHERE settled_at_ms IS NULL AND inbox_id IS NOT NULL AND report_id IN (SELECT id FROM task_reports WHERE task_id = ?)`);
    const settleRound = this.db.prepare(`UPDATE task_report_deliveries SET settled_at_ms = ?, settled_by = ?, outbox_id = ?
      WHERE settled_at_ms IS NULL AND inbox_id IS NOT NULL AND report_id IN (SELECT id FROM task_reports WHERE task_id = ? AND round = ?)`);
    for (const [id, round] of targets) {
      settled += Number((round == null ? settleTask.run(this.now(), by, outboxId ?? null, id)
        : settleRound.run(this.now(), by, outboxId ?? null, id, round)).changes);
    }
    return settled;
  }

  // The rounds of a named task an answer covers: the round it was decided on
  // (its origin, or another of its inputs); else the rounds asked where it
  // went; else, an answer given somewhere no round was asked, every round.
  roundsAnswered(taskId, seqs, scopes) {
    const rounds = new Set();
    for (const seq of seqs) {
      const origin = this.db.prepare('SELECT round FROM task_rounds WHERE task_id = ? AND origin_seq = ?').get(taskId, seq);
      if (origin) rounds.add(origin.round);
      const input = this.db.prepare('SELECT round FROM task_inputs WHERE task_id = ? AND seq = ? ORDER BY id DESC LIMIT 1').get(taskId, seq);
      if (input) rounds.add(input.round);
    }
    if (rounds.size === 0) {
      for (const round of this.rounds(taskId)) if (round.origin_scope && scopes.has(round.origin_scope)) rounds.add(round.round);
    }
    return rounds.size ? [...rounds] : [null];
  }

  // Tasks among these whose last report is out of date: new input from the
  // requester's side came in and their worker is on it again. Not one whose
  // report went out past the catch-up turns: that one is the result so far.
  // Nor one with a report of an earlier round still to relay: that report
  // answers its own round's request while the task works on the next one.
  working(taskIds = []) {
    const ahead = this.db.prepare(`SELECT 1 FROM task_report_deliveries d JOIN task_reports r ON r.id = d.report_id
      WHERE r.task_id = ? AND d.while_working = 1 AND d.settled_at_ms IS NULL LIMIT 1`);
    const earlier = this.db.prepare(`SELECT 1 FROM task_report_deliveries d JOIN task_reports r ON r.id = d.report_id
      WHERE r.task_id = ? AND r.round < ? AND d.settled_at_ms IS NULL AND d.inbox_id IS NOT NULL LIMIT 1`);
    return taskIds.filter((id) => {
      const task = this.get(id);
      return ['queued', 'running'].includes(task?.status) && !ahead.get(id) && !earlier.get(id, task.round ?? 1);
    });
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
    this.transaction(() => {
      this.db.exec('DELETE FROM task_input_claims');
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
  }

  // ---- task directories -----------------------------------------------------

  dirState(dir) {
    return this.db.prepare('SELECT * FROM task_workdirs WHERE path = ?').get(dir) ?? null;
  }

  markDir(dir, column) {
    this.db.prepare(`INSERT INTO task_workdirs(path, ${column}) VALUES (?, ?)
      ON CONFLICT(path) DO UPDATE SET ${column} = excluded.${column}`).run(dir, this.now());
  }

  // Directories a task may still use: a live task's own and the one its
  // parent worked in (its brief points there), and the same two of any worker
  // whose process has not exited (a stopped task's, until it has).
  heldDirs() {
    const ids = [...this.running.keys()];
    const rows = this.db.prepare(`SELECT t.workdir AS own, p.workdir AS parent FROM tasks t LEFT JOIN tasks p ON p.id = t.parent_id
      WHERE t.status NOT IN ('done', 'dropped', 'failed', 'stopped')${ids.length ? ` OR t.id IN (${ids.map(() => '?').join(', ')})` : ''}`).all(...ids);
    return new Set(rows.flatMap((row) => [row.own, row.parent]).filter(Boolean));
  }

  // Only directories this manager made: <workspace>/tasks/<task id>.
  ownDir(dir) {
    return path.dirname(dir) === path.join(this.config.workspace, 'tasks') && /^t[0-9]+$/.test(path.basename(dir));
  }

  dirLimits() {
    const setting = (value, fallback) => (Number.isFinite(value) ? value : fallback);
    return {
      pruneMs: setting(this.config.taskDirPruneDays, DIR_PRUNE_DAYS) * 86_400_000,
      removeMs: setting(this.config.taskDirRemoveDays, DIR_REMOVE_DAYS) * 86_400_000,
      budget: setting(this.config.taskDirBudgetGb, DIR_BUDGET_GB) * 1024 ** 3,
    };
  }

  startSweeps({ delayMs = SWEEP_DELAY_MS, everyMs = SWEEP_MS } = {}) {
    if (this.sweepTimers || this.stopped) return;
    const run = () => {
      this.sweepDirs().catch((error) => this.logger?.warn('task_dirs_sweep_failed', { error: safeError(error) }));
    };
    this.sweepTimers = [setTimeout(run, delayMs), setInterval(run, everyMs)];
    for (const timer of this.sweepTimers) timer.unref?.();
  }

  // Measuring reads the disk in the background. Deciding what to delete and
  // moving it to the trash happen together, in one step with no wait (an
  // ended task's state cannot change in between), so a task never starts in
  // or reads a directory that is half deleted. The trash is emptied after.
  async sweepDirs() {
    if (this.sweeping) return this.sweeping;
    this.sweeping = (async () => {
      const trashes = [path.join(this.config.workspace, 'tasks', TRASH),
        ...(this.config.dataDir ? [path.join(this.config.dataDir, 'workers', TRASH)] : [])];
      for (const trash of trashes) await emptyTrash(trash);
      const dirs = this.db.prepare('SELECT DISTINCT workdir FROM tasks').all().map((row) => row.workdir)
        .filter((dir) => this.ownDir(dir) && isDirectory(dir));
      // What a task used while the scan ran may have changed under it: it
      // counts toward the budget, and waits for the next sweep.
      const busy = this.heldDirs();
      const sizes = new Map();
      for (const dir of dirs) {
        if (this.stopped) return null;
        sizes.set(dir, await scanDir(dir, { stopped: () => this.stopped }));
      }
      if (this.stopped) return null;
      const result = this.cleanDirs(sizes, busy);
      for (const trash of trashes) await emptyTrash(trash);
      return result;
    })().finally(() => { this.sweeping = null; });
    return this.sweeping;
  }

  cleanDirs(sizes, busy = new Set()) {
    const { pruneMs, removeMs, budget } = this.dirLimits();
    const now = this.now();
    const held = new Set([...busy, ...this.heldDirs()]);
    const endedAt = new Map(this.db.prepare(`SELECT workdir, MAX(COALESCE(ended_at_ms, updated_at_ms)) AS at FROM tasks
      GROUP BY workdir`).all().map((row) => [row.workdir, row.at]));
    const left = new Map([...sizes].map(([dir, scan]) => [dir, scan.bytes]));
    const total = () => [...left.values()].reduce((sum, bytes) => sum + bytes, 0);
    // Directories no task uses, the one that ended longest ago first.
    const idle = [...sizes.keys()].filter((dir) => !held.has(dir) && this.dirState(dir)?.removed_at_ms == null)
      .sort((a, b) => endedAt.get(a) - endedAt.get(b));
    const done = { pruned: [], removed: [], freed: 0 };
    const trash = path.join(this.config.workspace, 'tasks', TRASH);
    const prune = (dir) => {
      if (!left.has(dir)) return;
      const moved = sizes.get(dir).dependencies.filter((folder) => {
        if (!isDirectoryWithin(folder, dir)) return false;
        try { moveToTrash(folder, trash); return true; } catch (error) {
          this.logger?.warn('task_dir_prune_failed', { dir: folder, error: safeError(error) });
          return false;
        }
      });
      if (moved.length === 0) return;
      this.markDir(dir, 'pruned_at_ms');
      const freed = Math.min(left.get(dir), sizes.get(dir).dependencyBytes);
      left.set(dir, left.get(dir) - freed);
      sizes.get(dir).dependencies = [];
      done.pruned.push(path.basename(dir));
      done.freed += freed;
    };
    const remove = (dir) => {
      if (!left.has(dir)) return;
      try { moveToTrash(dir, trash); } catch (error) {
        this.logger?.warn('task_dir_remove_failed', { dir, error: safeError(error) });
        return;
      }
      this.markDir(dir, 'removed_at_ms');
      // Its worker's sessions: a task that follows it now starts from its
      // summary instead of a copy of its session, and a round that reopens it
      // from the task record.
      this.db.prepare(`UPDATE tasks SET native_session = NULL WHERE workdir = ?
        AND status IN ('done', 'dropped', 'failed', 'stopped')`).run(dir);
      if (this.config.dataDir) {
        for (const { id } of this.db.prepare('SELECT id FROM tasks WHERE workdir = ?').all(dir)) {
          const sessions = path.join(this.config.dataDir, 'workers', id);
          if (!isDirectory(sessions)) continue;
          try { moveToTrash(sessions, path.join(this.config.dataDir, 'workers', TRASH)); } catch (error) {
            this.logger?.warn('task_sessions_remove_failed', { task: id, error: safeError(error) });
          }
        }
      }
      done.removed.push(path.basename(dir));
      done.freed += left.get(dir);
      left.delete(dir);
    };
    for (const dir of idle) {
      const age = now - endedAt.get(dir);
      if (removeMs > 0 && age >= removeMs) remove(dir);
      else if (pruneMs > 0 && age >= pruneMs) prune(dir);
    }
    if (budget > 0) {
      for (const dir of idle) if (total() > budget) prune(dir);
      for (const dir of idle) if (total() > budget) remove(dir);
      if (total() > budget) this.logger?.warn('task_dirs_over_budget', { bytes: total(), budget });
    }
    this.disk = { bytes: total(), budget, at: now };
    if (done.pruned.length || done.removed.length) {
      this.logger?.info('task_dirs_cleaned', { pruned: done.pruned, removed: done.removed, freed: done.freed });
      this.recordActivity('task.dirs_cleaned', {
        summary: `freed ${formatBytes(done.freed)}: dependencies of ${done.pruned.length}, whole directories of ${done.removed.length}`,
        metadata: { pruned: done.pruned, removed: done.removed, freedBytes: done.freed, bytes: total() },
      });
    }
    return { ...done, bytes: total() };
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
      disk: this.disk,
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
      rounds: this.rounds(task.id),
      dir: this.dirState(task.workdir),
      lastError,
    };
  }

  // Worker turns in flight end with the daemon; their tasks stay `running`
  // and restore() starts them again.
  shutdown() {
    this.stopped = true;
    clearTimeout(this.reportRetry);
    this.reportRetry = null;
    for (const timer of this.sweepTimers ?? []) clearTimeout(timer);
    this.sweepTimers = null;
    for (const slot of this.running.values()) slot.handle?.stop?.();
  }
}
