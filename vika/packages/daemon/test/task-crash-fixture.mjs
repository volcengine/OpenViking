// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Subprocess used by tasks.test.mjs: real SQLite, fake worker, hard process
// termination at the durable handoff boundaries. No model or connector calls.
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { TaskManager } from '../src/tasks.mjs';
import { MessageInbox } from '../src/inbox.mjs';

const [stage, workspace] = process.argv.slice(2);
const kill = () => process.kill(process.pid, 'SIGKILL');
const db = new DatabaseSync(path.join(workspace, 'state.sqlite3'));
const inbox = new MessageInbox({ db, runtime: { snapshot: () => ({ work: { turnInProgress: true } }) } });
const executor = { start() {
  if (stage === 'start') kill();
  return { done: stage === 'check' ? new Promise(() => {}) : Promise.resolve({ text: '' }), stop() {} };
} };
const tasks = new TaskManager({ db, config: { workspace }, executor, inbox, lineFor: (row) => row.content });
if (stage === 'ended') tasks.flushReports = kill;
if (stage === 'insert') {
  const persist = inbox.persist.bind(inbox);
  inbox.persist = (...args) => { persist(...args); kill(); };
}
if (stage === 'commit') {
  const exec = db.exec.bind(db);
  db.exec = (sql) => {
    if (sql === 'COMMIT' && db.prepare('SELECT COUNT(*) n FROM inbox_pending').get().n > 0) kill();
    return exec(sql);
  };
}
if (stage === 'publish') inbox.publish = kill;
const row = { seq: 1, event_id: 'e1', connector_instance: 'test', source: 'test', scope_key: 'test:a',
  chat_id: 'a', message_id: 'm1', sender_id: 'alice', content: 'ORIGINAL_REQUEST: never publish' };
tasks.create({ rows: [row], title: 'X', runtime: 'claude' });
if (stage === 'check') {
  tasks.route('t1', [{ ...row, seq: 2, content: 'FOLLOW_UP: include test evidence' }], 'owner');
  tasks.check('t1');
  kill();
}
tasks.report('t1', { state: 'done', draft: 'COMPLETE_RESULT' });
await new Promise((resolve) => setImmediate(resolve));
throw new Error(`fault point ${stage} was not reached`);
