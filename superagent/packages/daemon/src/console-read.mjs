// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// The read-only RFC v1 surface the console (OVSA-CONSOLE-001, Slice A) reads
// tasks, inputs and reports from. It answers from the tables the TaskManager
// already keeps, with SELECTs only: nothing here writes, cancels, retries or
// restarts anything. Anything it cannot state truthfully (token usage, a task's
// linked activity) is left out rather than guessed, and the capability list
// says so.

const TASK_STATUSES = new Set(['queued', 'running', 'waiting', 'done', 'stopped', 'failed', 'blocked', 'dropped']);
const RUNTIMES = new Set(['pi', 'dsh', 'codex', 'claude', 'traex', 'agy', 'default']);
const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 100;

// What the console may ask of each read route. Anything else in the query is
// refused instead of ignored, so a typo cannot silently change what is shown.
const TASK_QUERY = new Set(['status', 'runtime', 'q', 'before', 'limit']);
const PAGE_QUERY = new Set(['before', 'limit']);

function fail(code, message = 'Invalid read-only console request.') {
  return { status: 400, body: { error: { code, message, retryable: false } } };
}

function notFound(message = 'Not found in this instance.') {
  return { status: 404, body: { error: { code: 'not_found', message, retryable: false } } };
}

const iso = (ms) => (Number.isFinite(ms) ? new Date(ms).toISOString() : undefined);

function limitOf(params) {
  const raw = params.get('limit');
  if (raw === null) return DEFAULT_LIMIT;
  if (!/^[1-9]\d{0,2}$/.test(raw)) return null;
  const value = Number(raw);
  return value > MAX_LIMIT ? null : value;
}

// Cursors are "<created_at_ms>:<id>" so two tasks created in the same
// millisecond paginate without repeating or skipping one.
function cursorOf(params) {
  const raw = params.get('before');
  if (raw === null) return { ok: true, value: null };
  const match = /^(\d{1,15}):([A-Za-z0-9_-]{1,64})$/.exec(raw);
  if (!match) return { ok: false };
  return { ok: true, value: { ms: Number(match[1]), id: match[2] } };
}

function reportDelivery(db, reportId) {
  const row = db.prepare(`SELECT d.inbox_id, d.settled_at_ms, d.settled_by, d.outbox_id,
      o.status AS outbox_status
    FROM task_report_deliveries d LEFT JOIN outbox o ON o.id = d.outbox_id
    WHERE d.report_id = ?`).get(reportId);
  if (!row) return null;
  return {
    relay: {
      state: row.inbox_id === null ? 'pending' : row.settled_at_ms === null ? 'offered' : 'relayed',
      inboxId: row.inbox_id === null ? null : String(row.inbox_id),
      at: iso(row.settled_at_ms),
    },
    finalDelivery: row.outbox_id === null
      ? null
      : { outboxId: String(row.outbox_id), status: row.outbox_status ?? null },
  };
}

function taskJson(db, tasks, row) {
  const running = tasks?.running?.get(row.id);
  const workerState = running
    ? 'working'
    : row.status === 'recovering' || row.recovering === 1 ? 'recovering' : row.status;
  return {
    id: row.id,
    // The console labels this "Revision": the last time the task changed. It
    // is what a page reading the row can compare against, not a version of a
    // configuration that does not exist here.
    revision: row.updated_at_ms,
    title: row.title,
    summary: row.summary || undefined,
    status: row.status,
    runtime: row.runtime || 'default',
    origin: {
      source: row.origin_source || undefined,
      sourceRef: row.origin_message || row.origin_event || undefined,
    },
    turns: row.turns,
    recovering: Boolean(row.recovering),
    worker: {
      state: workerState,
      turn: row.turns,
      startedAt: running?.startedAt ? iso(running.startedAt) : undefined,
    },
    // Token usage is not recorded per task in this daemon: the table has no
    // such columns, and a zero would read as "spent nothing".
    usage: undefined,
    createdAt: iso(row.created_at_ms),
    updatedAt: iso(row.updated_at_ms),
  };
}

function childOf(db, taskId) {
  return db.prepare('SELECT 1 FROM tasks WHERE id = ?').get(taskId) ? true : false;
}

// Inputs and reports page by their own row id, newest first: "before" is the
// id of the last row of the page just read.
function idCursorOf(params) {
  const raw = params.get('before');
  if (raw === null) return { ok: true, value: null };
  if (!/^[1-9]\d{0,15}$/.test(raw)) return { ok: false };
  return { ok: true, value: Number(raw) };
}

function inputsPage(db, taskId, params) {
  const limit = limitOf(params);
  const cursor = idCursorOf(params);
  if (limit === null || !cursor.ok) return fail('invalid_query');
  const rows = db.prepare(`SELECT id, text, role, created_at_ms, delivered_at_ms, round
    FROM task_inputs WHERE task_id = ? AND (? IS NULL OR id < ?)
    ORDER BY id DESC LIMIT ?`).all(taskId, cursor.value, cursor.value, limit + 1);
  const page = rows.slice(0, limit);
  const claims = page.length
    ? db.prepare(`SELECT input_id, turn FROM task_input_claims WHERE input_id IN (${page.map(() => '?').join(',')})`)
      .all(...page.map((row) => row.id))
    : [];
  const claimed = new Map(claims.map((row) => [row.input_id, row.turn]));
  return {
    status: 200,
    body: {
      items: page.map((row) => ({
        id: String(row.id),
        text: row.text,
        state: row.delivered_at_ms !== null ? 'delivered' : claimed.has(row.id) ? 'claimed' : 'pending',
        deliveredAt: iso(row.delivered_at_ms),
        turn: claimed.get(row.id) ?? null,
        createdAt: iso(row.created_at_ms),
      })),
      nextCursor: rows.length > limit ? String(page.at(-1).id) : null,
      generatedAt: new Date().toISOString(),
    },
  };
}

function reportsPage(db, taskId, params) {
  const limit = limitOf(params);
  const cursor = idCursorOf(params);
  if (limit === null || !cursor.ok) return fail('invalid_query');
  const rows = db.prepare(`SELECT id, state, summary, draft, question, round, created_at_ms
    FROM task_reports WHERE task_id = ? AND (? IS NULL OR id < ?)
    ORDER BY id DESC LIMIT ?`).all(taskId, cursor.value, cursor.value, limit + 1);
  const page = rows.slice(0, limit);
  return {
    status: 200,
    body: {
      items: page.map((row) => {
        const delivery = reportDelivery(db, row.id);
        return {
          id: String(row.id),
          state: row.state,
          summary: row.summary || undefined,
          draft: row.draft || undefined,
          question: row.question || undefined,
          round: row.round,
          relay: delivery?.relay,
          finalDelivery: delivery?.finalDelivery ?? null,
          // Per-report artifacts are not stored: the task keeps the list of its
          // latest one. Showing it on every report would misattribute them.
          createdAt: iso(row.created_at_ms),
        };
      }),
      nextCursor: rows.length > limit ? String(page.at(-1).id) : null,
      generatedAt: new Date().toISOString(),
    },
  };
}

function tasksPage(db, tasks, params) {
  const limit = limitOf(params);
  const cursor = cursorOf(params);
  if (limit === null || !cursor.ok) return fail('invalid_query');
  const status = params.get('status');
  if (status !== null && status !== 'all' && !TASK_STATUSES.has(status)) return fail('invalid_argument');
  const runtime = params.get('runtime');
  if (runtime !== null && runtime !== '' && !RUNTIMES.has(runtime)) return fail('invalid_argument');
  const query = params.get('q');
  if (query !== null && query.length > 200) return fail('invalid_argument');

  const clauses = [];
  const values = [];
  if (status !== null && status !== 'all') {
    clauses.push('status = ?');
    values.push(status);
  }
  if (runtime) {
    clauses.push('COALESCE(NULLIF(runtime, \'\'), \'default\') = ?');
    values.push(runtime);
  }
  if (query) {
    clauses.push("(title LIKE ? ESCAPE '\\' OR id LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\')");
    const like = `%${query.replaceAll('\\', '\\\\').replaceAll('%', '\\%').replaceAll('_', '\\_')}%`;
    values.push(like, like, like);
  }
  if (cursor.value) {
    clauses.push('(created_at_ms < ? OR (created_at_ms = ? AND id < ?))');
    values.push(cursor.value.ms, cursor.value.ms, cursor.value.id);
  }
  const where = clauses.length ? `WHERE ${clauses.join(' AND ')}` : '';
  const rows = db.prepare(`SELECT * FROM tasks ${where}
    ORDER BY created_at_ms DESC, id DESC LIMIT ?`).all(...values, limit + 1);
  const page = rows.slice(0, limit);
  return {
    status: 200,
    body: {
      items: page.map((row) => taskJson(db, tasks, row)),
      nextCursor: rows.length > limit ? `${page.at(-1).created_at_ms}:${page.at(-1).id}` : null,
      generatedAt: new Date().toISOString(),
    },
  };
}

// The console's module list: what this daemon can answer now, what it cannot,
// and why. A module is "effective" only when it is both enabled and available.
function capabilities(instanceId) {
  const modules = {
    'instances.read': { enabled: true, available: true, effective: true, reasonCode: null },
    'capabilities.read': { enabled: true, available: true, effective: true, reasonCode: null },
    'sessions.read': {
      enabled: true, available: true, effective: true, reasonCode: null,
      message: 'Sessions come from the activity feed, as on the legacy timeline.',
    },
    'activity.read': { enabled: true, available: true, effective: true, reasonCode: null },
    'tasks.read': { enabled: true, available: true, effective: true, reasonCode: null },
    'runtime.read': {
      enabled: true, available: false, effective: false, reasonCode: 'runtime_snapshot_unavailable',
      message: 'The daemon records each task\'s runtime, model and effort, but exposes no live runtime snapshot here.',
    },
    'metrics.read': {
      enabled: true, available: false, effective: false, reasonCode: 'metrics_not_recorded',
      message: 'This daemon records no turn or queue metrics to read.',
    },
    'audit.read': {
      enabled: true, available: false, effective: false, reasonCode: 'audit_store_unavailable',
      message: 'No audit store is kept in this version.',
    },
  };
  for (const key of ['tasks.control', 'tasks.retry', 'tasks.pause', 'runtime.control', 'sessions.control',
    'configuration.read', 'configuration.write', 'instances.setup', 'instances.process',
    'providers.read', 'providers.write', 'runtimePackages.install', 'logs.read', 'diagnostics.bundle',
    'events.stream']) {
    modules[key] = {
      enabled: false, available: false, effective: false,
      reasonCode: key === 'tasks.pause' ? 'not_provided' : 'disabled_by_owner',
      message: key === 'tasks.pause' ? 'Pause and resume need durable checkpoints and are not provided.' : undefined,
    };
  }
  return { instanceId, apiVersion: 'v1', modules, generatedAt: new Date().toISOString() };
}

// (pathname, searchParams) -> { status, body } or null when the path is not one
// of the console's read routes. The caller answers 404 for null.
export function createConsoleRead({ db, instanceId, tasks = null }) {
  const instance = /^\/api\/v1\/instances\/([^/]+)(\/.*)?$/;

  return function readConsole(pathname, params) {
    if (pathname === '/api/v1/capabilities') {
      if ([...params.keys()].some((key) => key !== 'instance')) return fail('invalid_argument');
      return { status: 200, body: capabilities(instanceId) };
    }

    const match = instance.exec(pathname);
    if (!match) return null;
    // The instance id in the path is the Shared Web's label for this daemon
    // (it validated it against its own instance list and forwarded the path
    // verbatim). This daemon answers with its own state either way, so the id
    // is only checked for shape here.
    const id = decodeURIComponent(match[1]);
    if (id.includes('/')) return notFound('Unknown instance.');
    const suffix = match[2] ?? '';

    if (suffix === '/tasks') {
      if ([...params.keys()].some((key) => key !== 'instance' && !TASK_QUERY.has(key))) return fail('invalid_argument');
      return tasksPage(db, tasks, params);
    }
    const detail = /^\/tasks\/([^/]+)$/.exec(suffix);
    if (detail) {
      if ([...params.keys()].some((key) => key !== 'instance')) return fail('invalid_argument');
      const taskId = decodeURIComponent(detail[1]);
      const row = db.prepare('SELECT * FROM tasks WHERE id = ?').get(taskId);
      if (!row) return notFound('No such task in this instance.');
      return { status: 200, body: taskJson(db, tasks, row) };
    }
    const child = /^\/tasks\/([^/]+)\/(inputs|reports|events)$/.exec(suffix);
    if (child) {
      if ([...params.keys()].some((key) => key !== 'instance' && !PAGE_QUERY.has(key))) return fail('invalid_argument');
      const taskId = decodeURIComponent(child[1]);
      if (!childOf(db, taskId)) return notFound('No such task in this instance.');
      if (child[2] === 'inputs') return inputsPage(db, taskId, params);
      if (child[2] === 'reports') return reportsPage(db, taskId, params);
      // Activity rows carry no task link in this version: claiming an empty
      // list would say "this task did nothing", which is not true.
      return {
        status: 404,
        body: {
          error: {
            code: 'not_provided',
            message: 'Activity rows are not linked to tasks in this version, so a task\'s events cannot be listed.',
            retryable: false,
          },
        },
      };
    }
    return null;
  };
}
