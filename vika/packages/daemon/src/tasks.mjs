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

export const WORKER_SLOTS = 3;
// A slot only a follow-up to an existing task may take, so new work cannot
// keep a person waiting on the task they are already in.
export const RESERVED_SLOTS = 1;
// Ended tasks kept in the working set the router is shown; live ones always are.
export const RECENT_ENDED = 32;
// Worker turns in a row that may run to catch up with inputs that arrived
// during the previous one before its report is relayed anyway.
const CATCH_UP_TURNS = 3;
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
  -- superseded report. Retained after delivery as the report -> inbox receipt.
  CREATE TABLE IF NOT EXISTS task_report_deliveries (
    report_id INTEGER PRIMARY KEY,
    inbox_id INTEGER,
    created_at_ms INTEGER NOT NULL
  );
`;

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

  workerFor(token) {
    return typeof token === 'string' && this.tokens.has(token) ? this.tokens.get(token) : null;
  }

  card(task, { detail = false } = {}) {
    const where = task.origin_chat ? `chat=${chatTag(metadataTag(task.origin_chat), task.origin_thread)}` : `source=${task.origin_source}`;
    const head = `${task.id} [${task.status}] "${oneLine(task.title)}" for ${task.requester_name ?? 'unknown'} in ${where}`;
    if (!detail) return head;
    return [head,
      `origin: msg=${metadataTag(task.origin_message ?? '')} event=${metadataTag(task.origin_event ?? '')}`,
      ...(task.summary ? [`summary: ${oneLine(clip(task.summary, SUMMARY_CHARS))}`] : []),
      ...(task.question ? [`question waiting: ${oneLine(clip(task.question, SUMMARY_CHARS))}`] : []),
      ...(JSON.parse(task.artifacts_json).length ? [`artifacts: ${JSON.parse(task.artifacts_json).map(oneLine).join(', ')}`] : []),
      `turns: ${task.turns}, updated ${new Date(task.updated_at_ms).toISOString().slice(0, 16)}Z`,
    ].join('\n');
  }

  list({ taskId = null, query = '', includeEnded = false } = {}) {
    if (taskId) {
      const task = this.get(taskId);
      return task ? [this.card(task, { detail: true })] : [];
    }
    let rows = includeEnded ? this.db.prepare('SELECT * FROM tasks ORDER BY created_at_ms DESC LIMIT 200').all() : this.workingSet();
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
      if (this.isLive(task) && sameChat && task.requester_id === event.senderId) add(task, 'same requester', 2);
      const score = similarity(query, bigrams(`${task.title} ${task.summary}`));
      if (score > 0.25) add(task, 'similar words', 1);
    }
    return [...hits.values()].sort((a, b) => b.weight - a.weight).slice(0, CANDIDATES);
  }

  // The daemon line an input carries when tasks may relate to it.
  hintFor(event, options) {
    const hits = this.candidatesFor(event, options);
    if (hits.length === 0) return '';
    return `[daemon tasks] may relate: ${hits.map(({ task, reasons }) => `${task.id} [${task.status}] "${oneLine(clip(task.title, 60))}" (${reasons.join(', ')})`).join(' · ')}`;
  }

  // ---- router commands ------------------------------------------------------

  nextId() {
    const row = this.db.prepare("SELECT COUNT(*) AS n FROM tasks").get();
    return `t${Number(row.n) + 1}`;
  }

  // rows: accepted connector_events rows (oldest first); the first one is the
  // origin the result answers.
  create({ rows, title, note = '', requester = null, runtime = null }) {
    if (!rows.length) throw new Error('a task needs at least one input');
    const [origin] = rows;
    const id = this.nextId();
    const time = this.now();
    const workdir = path.join(this.config.workspace, 'tasks', id);
    fs.mkdirSync(workdir, { recursive: true });
    this.transaction(() => {
      this.db.prepare(`INSERT INTO tasks(id, title, status, origin_seq, origin_event, origin_instance, origin_scope, origin_chat,
        origin_thread, origin_message, origin_chat_type, origin_source, requester_id, requester_name, note, runtime, session_id, workdir,
        created_at_ms, updated_at_ms) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).run(
        id, clip(title, 120), origin.seq, origin.event_id, origin.connector_instance, origin.scope_key, origin.chat_id ?? null,
        origin.thread_id ?? null, origin.message_id ?? null, origin.chat_type ?? null, origin.source, origin.sender_id ?? null,
        requester, clip(note, 400), runtime, randomUUID(), workdir, time, time,
      );
      for (const row of rows) this.addInput(id, row, 'owner');
    });
    this.recordActivity('task.created', { summary: `${id} ${title}`, inboundSeq: origin.seq, metadata: { task: id } });
    this.request(id);
    return this.get(id);
  }

  addInput(taskId, row, role) {
    const text = this.lineFor(row);
    this.db.prepare('INSERT INTO task_inputs(task_id, seq, role, text, created_at_ms) VALUES (?, ?, ?, ?, ?)')
      .run(taskId, row.seq ?? null, role, text, this.now());
  }

  // A follow-up: `owner` inputs are part of the request and resume an ended
  // task; `context` ones are background a worker reads with its next input.
  route(taskId, rows, role) {
    const task = this.get(taskId);
    if (!task) throw new Error(`no task ${taskId}`);
    if (task.status === 'stopped') throw new Error(`${taskId} was stopped; create a new task instead`);
    this.transaction(() => {
      for (const row of rows) this.addInput(taskId, row, role);
      this.touch(taskId);
      if (role === 'owner' && (ENDED.has(task.status) || task.status === 'waiting' || task.status === 'blocked')) this.setStatus(taskId, 'queued');
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
    this.running.get(taskId)?.handle?.stop?.();
    this.db.prepare("UPDATE tasks SET status = 'stopped', ended_at_ms = ?, updated_at_ms = ? WHERE id = ?").run(this.now(), this.now(), taskId);
    this.recordActivity('task.ended', { summary: `${taskId} stopped${reason ? `: ${reason}` : ''}`, metadata: { task: taskId, status: 'stopped' } });
    return this.get(taskId);
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
    return this.db.prepare(`SELECT * FROM task_inputs WHERE task_id = ? AND delivered_at_ms IS NULL
      AND id NOT IN (SELECT input_id FROM task_input_claims) ORDER BY id`).all(taskId);
  }

  claimInputs(task, rows) {
    const claim = this.db.prepare('INSERT INTO task_input_claims(input_id, task_id, turn) VALUES (?, ?, ?)');
    for (const row of rows) claim.run(row.id, task.id, task.turns);
  }

  settleInputs(task, result) {
    this.transaction(() => {
      if (!result.error) {
        this.db.prepare(`UPDATE task_inputs SET delivered_at_ms = ? WHERE id IN
          (SELECT input_id FROM task_input_claims WHERE task_id = ? AND turn = ?)`).run(this.now(), task.id, task.turns);
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
      if (index < 0 && reserved < this.reservedSlots) index = this.pending.findIndex((id) => (this.get(id)?.turns ?? 0) > 0);
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
        `Your directory: ${task.workdir}`,
        ...(recovery && task.summary ? ['Previous work summary (may be incomplete):', quoted(task.summary)] : []),
        ...(recovery && task.draft ? ['Previous draft (may be incomplete):', quoted(task.draft)] : []),
        ...(recovery && task.artifacts_json !== '[]' ? [`Previous artifacts: ${task.artifacts_json}`] : []),
        'Work on this request, then finish with task_report. You cannot message anyone: the agent replies to the requester from your report.',
      ].join('\n');
    }
    return [
      ...prefix,
      ...(owner.length ? [`[task ${task.id}] New input from the requester's side:`, ...owner.map((row) => row.text)] : []),
      ...(context.length ? ['Context only (act on it only if it changes your task):', ...context.map((row) => row.text)] : []),
      ...(owner.length || context.length ? [] : [`[task ${task.id}] Continue your task.`]),
      'Finish with task_report.',
    ].join('\n');
  }

  async runTurn(task, { reserved = false } = {}) {
    const token = randomUUID();
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
        task: this.get(task.id), prompt, authority: token,
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
    this.settleInputs(task, result);
    this.recordActivity('task.turn_completed', {
      summary: `${taskId} ${result.error ? `failed: ${result.error}` : 'turn ended'}`,
      status: result.error ? 'failed' : 'completed',
      metadata: { task: taskId, durationMs: this.now() - slot.startedAt, ...(result.usage ?? {}) },
    });
    if (result.error && result.transient && (this.retries.get(taskId) ?? 0) < TRANSIENT_RETRIES) {
      this.retries.set(taskId, (this.retries.get(taskId) ?? 0) + 1);
      this.setStatus(taskId, 'queued');
      this.request(taskId);
      return;
    }
    this.retries.delete(taskId);
    const structured = this.db.prepare('SELECT * FROM task_reports WHERE task_id = ? AND id > ? ORDER BY id DESC LIMIT 1')
      .get(taskId, reportsBefore);
    let report = structured && structured.state !== 'progress' ? structured : null;
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
    if (!result.error && fresh.length > 0 && caught < CATCH_UP_TURNS) {
      this.catchUp.set(taskId, caught + 1);
      this.setStatus(taskId, 'queued');
      this.request(taskId);
      return;
    }
    this.catchUp.delete(taskId);
    // Ending the task and recording the obligation to deliver its report are
    // atomic. A failed inbox write leaves this durable outbox available to retry.
    this.transaction(() => {
      this.setStatus(taskId, STATUS_FOR[report.state] ?? 'done');
      this.db.prepare('INSERT OR IGNORE INTO task_report_deliveries(report_id, created_at_ms) VALUES (?, ?)').run(report.id, this.now());
    });
    this.flushReports();
    if (!result.error && this.undelivered(taskId).some((row) => row.role === 'owner')) {
      this.setStatus(taskId, 'queued');
      this.request(taskId);
    }
  }

  relayReport(task, report) {
    const text = [
      `[task report] task=${task.id} state=${report.state} title="${oneLine(clip(task.title, 80))}" requester=${metadataTag(task.requester_name ?? 'unknown')}${report.structured ? '' : ' unstructured=1'}`,
      `origin: ${task.origin_chat ? `chat=${chatTag(metadataTag(task.origin_chat), task.origin_thread)} ` : ''}source=${task.origin_source} msg=${metadataTag(task.origin_message ?? '')} event=${metadataTag(task.origin_event ?? '')}`,
      ...(report.summary ? ['summary:', quoted(clip(report.summary, SUMMARY_CHARS))] : []),
      ...(report.draft ? ['draft:', quoted(report.draft)] : []),
      ...(report.question ? ['question:', quoted(clip(report.question, SUMMARY_CHARS))] : []),
    ].join('\n');
    const item = {
      text,
      seq: null,
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
      if (!delivery || delivery.inbox_id != null) return null;
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

  flushReports() {
    if (this.stopped) return;
    let failed = false;
    for (const report of this.db.prepare(`SELECT r.* FROM task_report_deliveries d JOIN task_reports r ON r.id = d.report_id
      WHERE d.inbox_id IS NULL ORDER BY r.id`).all()) {
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
    });
    this.flushReports();
    for (const task of this.db.prepare("SELECT * FROM tasks WHERE status IN ('queued', 'running') ORDER BY updated_at_ms").all()) {
      if (task.status === 'running') this.db.prepare("UPDATE tasks SET status = 'queued', recovering = 1 WHERE id = ?").run(task.id);
      this.request(task.id);
    }
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
