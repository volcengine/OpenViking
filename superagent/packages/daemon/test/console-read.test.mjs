// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { TaskManager } from '../src/tasks.mjs';
import { MessageInbox } from '../src/inbox.mjs';
import { createConsoleRead } from '../src/console-read.mjs';
import { get as httpGet } from 'node:http';

// A worker that never runs a turn: this test only reads the rows a task leaves.
function idleExecutor() {
  return { start: () => ({ done: new Promise(() => {}), stop: () => {} }) };
}

function setup(t) {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'console-read-'));
  t.after(() => fs.rmSync(workspace, { recursive: true, force: true }));
  const db = new DatabaseSync(':memory:');
  db.exec(`CREATE TABLE connector_events (seq INTEGER PRIMARY KEY, message_id TEXT);
    CREATE TABLE outbox (id INTEGER PRIMARY KEY, inbound_seq INTEGER, remote_message_id TEXT,
      status TEXT NOT NULL DEFAULT 'pending', payload_json TEXT NOT NULL DEFAULT '{}');
    CREATE TABLE task_report_deliveries (report_id INTEGER PRIMARY KEY, inbox_id INTEGER,
      created_at_ms INTEGER NOT NULL, settled_at_ms INTEGER, settled_by TEXT, outbox_id INTEGER,
      offers INTEGER NOT NULL DEFAULT 0, while_working INTEGER NOT NULL DEFAULT 0);`);
  const inbox = new MessageInbox({ db, runtime: { snapshot: () => ({ work: { turnInProgress: true } }) } });
  const tasks = new TaskManager({
    db, config: { workspace }, executor: idleExecutor(), inbox,
    lineFor: (input) => `line ${input.seq}`,
  });
  t.after(() => { tasks.shutdown(); inbox.stop(); });
  const read = createConsoleRead({ db, instanceId: 'agent', tasks });
  return { db, tasks, read };
}

let seq = 0;
function eventRow(fields = {}) {
  seq += 1;
  return { seq, event_id: `e${seq}`, connector_instance: 'lark', source: 'lark',
    scope_key: 'lark:oc_a:', chat_id: 'oc_a', thread_id: null, message_id: `om_${seq}`,
    chat_type: 'group', sender_id: 'ou_alice', content: `line ${seq}`, ...fields };
}

function makeTask(db, tasks, { title = 'Look into the flaky test', runtime = 'pi' } = {}) {
  db.prepare('INSERT INTO connector_events(seq, message_id) VALUES (?, ?)').run(seq + 1, `om_seed${seq + 1}`);
  const created = tasks.create({
    rows: [{ ...eventRow(), seq: seq + 1 }],
    title,
    runtime,
  });
  return created.id ?? created.task?.id ?? created;
}

const params = (query = {}) => new URLSearchParams(Object.entries(query).map(([key, value]) => [key, String(value)]));

describe('read-only console surface', () => {
  it('reports what this daemon can answer, and what it cannot', (t) => {
    const { read } = setup(t);
    const answer = read('/api/v1/capabilities', params({ instance: 'agent' }));
    assert.equal(answer.status, 200);
    assert.equal(answer.body.instanceId, 'agent');
    assert.equal(answer.body.modules['tasks.read'].effective, true);
    assert.equal(answer.body.modules['runtime.read'].effective, false);
    assert.equal(answer.body.modules['metrics.read'].effective, false);
    assert.equal(answer.body.modules['tasks.control'].enabled, false);
    assert.equal(answer.body.modules['tasks.pause'].reasonCode, 'not_provided');
  });

  it('lists tasks newest first, filters them, and pages by cursor', (t) => {
    const { db, tasks, read } = setup(t);
    const first = makeTask(db, tasks, { title: 'alpha task' });
    const second = makeTask(db, tasks, { title: 'beta task', runtime: 'codex' });
    db.prepare("UPDATE tasks SET status = 'done', ended_at_ms = updated_at_ms WHERE id = ?").run(first);
    db.prepare('UPDATE tasks SET status = \'running\' WHERE id = ?').run(second);

    const all = read('/api/v1/instances/agent/tasks', params({ instance: 'agent' }));
    assert.equal(all.status, 200);
    assert.deepEqual(all.body.items.map((task) => task.id), [second, first]);
    assert.equal(all.body.nextCursor, null);
    // An ended task stays readable: the console shows history, not only the working set.
    assert.equal(all.body.items[1].status, 'done');

    const done = read('/api/v1/instances/agent/tasks', params({ instance: 'agent', status: 'done' }));
    assert.deepEqual(done.body.items.map((task) => task.id), [first]);

    const codex = read('/api/v1/instances/agent/tasks', params({ instance: 'agent', runtime: 'codex' }));
    assert.deepEqual(codex.body.items.map((task) => task.id), [second]);

    const searched = read('/api/v1/instances/agent/tasks', params({ instance: 'agent', q: 'alpha' }));
    assert.deepEqual(searched.body.items.map((task) => task.id), [first]);

    // A wildcard typed into the search box is matched literally, not as "all".
    const wildcard = read('/api/v1/instances/agent/tasks', params({ instance: 'agent', q: '%' }));
    assert.deepEqual(wildcard.body.items, []);

    const page = read('/api/v1/instances/agent/tasks', params({ instance: 'agent', limit: 1 }));
    assert.equal(page.body.items.length, 1);
    assert.ok(page.body.nextCursor);
    const older = read('/api/v1/instances/agent/tasks', params({ instance: 'agent', limit: 1, before: page.body.nextCursor }));
    assert.deepEqual(older.body.items.map((task) => task.id), [first]);
    assert.equal(older.body.nextCursor, null);
  });

  it('refuses a query it cannot honour instead of answering something else', (t) => {
    const { read } = setup(t);
    assert.equal(read('/api/v1/instances/agent/tasks', params({ status: 'exploded' })).status, 400);
    assert.equal(read('/api/v1/instances/agent/tasks', params({ limit: '0' })).status, 400);
    assert.equal(read('/api/v1/instances/agent/tasks', params({ limit: '999' })).status, 400);
    assert.equal(read('/api/v1/instances/agent/tasks', params({ before: 'yesterday' })).status, 400);
    assert.equal(read('/api/v1/instances/agent/tasks', params({ sort: 'id' })).status, 400);
  });

  it('shows one task, its inputs and its reports with their delivery state', (t) => {
    const { db, tasks, read } = setup(t);
    const taskId = makeTask(db, tasks);
    db.prepare(`INSERT INTO task_inputs(task_id, seq, role, text, created_at_ms, delivered_at_ms, round)
      VALUES (?, 1, 'user', 'first line', ?, NULL, 1)`).run(taskId, Date.now());
    db.prepare(`INSERT INTO task_inputs(task_id, seq, role, text, created_at_ms, delivered_at_ms, round)
      VALUES (?, 2, 'user', 'second line', ?, ?, 1)`).run(taskId, Date.now(), Date.now());
    db.prepare(`INSERT INTO task_reports(task_id, state, summary, draft, question, structured, created_at_ms, round)
      VALUES (?, 'done', 'Fixed it', 'All green.', '', 1, ?, 1)`).run(taskId, Date.now());
    const reportId = Number(db.prepare('SELECT MAX(id) AS id FROM task_reports').get().id);
    db.prepare("INSERT INTO outbox(id, status) VALUES (7, 'uncertain')").run();
    db.prepare(`INSERT INTO task_report_deliveries(report_id, inbox_id, created_at_ms, settled_at_ms, settled_by, outbox_id)
      VALUES (?, 3, ?, ?, 'relay', 7)`).run(reportId, Date.now(), Date.now());

    const detail = read(`/api/v1/instances/agent/tasks/${taskId}`, params({ instance: 'agent' }));
    assert.equal(detail.status, 200);
    assert.equal(detail.body.id, taskId);
    assert.equal(detail.body.runtime, 'pi');
    assert.equal(detail.body.title, 'Look into the flaky test');
    assert.equal(detail.body.usage, undefined, 'no token usage is invented');

    const inputs = read(`/api/v1/instances/agent/tasks/${taskId}/inputs`, params({ instance: 'agent' }));
    assert.equal(inputs.status, 200);
    // The input that created the task is listed too; the two written here come first.
    const written = inputs.body.items.filter((input) => ['first line', 'second line'].includes(input.text));
    assert.deepEqual(written.map((input) => [input.text, input.state]),
      [['second line', 'delivered'], ['first line', 'pending']]);
    assert.ok(inputs.body.items.some((input) => input.state === 'claimed'));

    const firstPage = read(`/api/v1/instances/agent/tasks/${taskId}/inputs`, params({ instance: 'agent', limit: '1' }));
    assert.equal(firstPage.body.items.length, 1);
    assert.ok(firstPage.body.nextCursor);
    const secondPage = read(`/api/v1/instances/agent/tasks/${taskId}/inputs`,
      params({ instance: 'agent', limit: '1', before: firstPage.body.nextCursor }));
    assert.notEqual(secondPage.body.items[0]?.id, firstPage.body.items[0].id);

    const reports = read(`/api/v1/instances/agent/tasks/${taskId}/reports`, params({ instance: 'agent' }));
    assert.equal(reports.status, 200);
    assert.equal(reports.body.items.length, 1);
    assert.equal(reports.body.items[0].summary, 'Fixed it');
    assert.equal(reports.body.items[0].relay.inboxId, '3');
    assert.equal(reports.body.items[0].finalDelivery.status, 'uncertain');

    assert.equal(read('/api/v1/instances/agent/tasks/t999', params({ instance: 'agent' })).status, 404);
    assert.equal(read(`/api/v1/instances/agent/tasks/${taskId}/inputs`, params({ instance: 'agent', limit: 'abc' })).status, 400);
  });

  it('says it cannot list a task\'s events rather than showing an empty list', (t) => {
    const { db, tasks, read } = setup(t);
    const taskId = makeTask(db, tasks);
    const events = read(`/api/v1/instances/agent/tasks/${taskId}/events`, params({ instance: 'agent' }));
    assert.equal(events.status, 404);
    assert.equal(events.body.error.code, 'not_provided');
  });

  it('leaves routes it does not serve to the caller', (t) => {
    const { read } = setup(t);
    assert.equal(read('/api/v1/instances/agent/metrics', params({})), null);
    assert.equal(read('/api/v1/instances/agent/overview', params({})), null);
    assert.equal(read('/health', params({})), null);
  });
});

describe('http surface', () => {
  it('answers the console routes through the daemon\'s HTTP server and leaves the rest to 404', async (t) => {
    const { db, tasks } = setup(t);
    const taskId = makeTask(db, tasks);
    const { startHttpApi } = await import('../src/http-api.mjs');
    const server = await startHttpApi({
      config: { host: '127.0.0.1', port: 0 },
      store: {},
      logger: { info() {}, warn() {}, error() {} },
      serviceSnapshot: () => ({ healthy: true, ready: true, body: {} }),
      readConsole: createConsoleRead({ db, instanceId: 'agent', tasks }),
    });
    t.after(() => new Promise((resolve) => { server.close(resolve); server.closeAllConnections?.(); }));
    const { port } = server.address();
    const get = (path) => new Promise((resolve, reject) => {
      const request = httpGet({ host: '127.0.0.1', port, path, headers: { host: `127.0.0.1:${port}` } }, (response) => {
        let body = '';
        response.on('data', (chunk) => { body += chunk; });
        response.on('end', () => resolve({ status: response.statusCode, body: body ? JSON.parse(body) : null }));
      });
      request.on('error', reject);
    });

    const capabilities = await get('/api/v1/capabilities?instance=agent');
    assert.equal(capabilities.status, 200);
    assert.equal(capabilities.body.modules['tasks.read'].effective, true);

    const list = await get('/api/v1/instances/agent/tasks?instance=agent&limit=5');
    assert.equal(list.status, 200);
    assert.deepEqual(list.body.items.map((task) => task.id), [taskId]);

    const missing = await get('/api/v1/instances/agent/metrics?instance=agent');
    assert.equal(missing.status, 404);

    const events = await get(`/api/v1/instances/agent/tasks/${taskId}/events?instance=agent`);
    assert.equal(events.status, 404);
    assert.equal(events.body.error.code, 'not_provided');
  });
});
