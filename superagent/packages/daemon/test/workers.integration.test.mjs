// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { startMockEnv, textMessage } from './mock-env.mjs';

// The fake codex's 'workers' behavior: as the router it turns a TASK line
// into task_create and relays a [task report] with reply_message; as
// `codex exec` (a worker turn) it reports the request back as its draft.
describe('workers scheduler end to end', () => {
  it('hands a request to a worker and relays its report where the request was made', async () => {
    const env = await startMockEnv({ codex: { behavior: 'workers' }, config: { scheduler: 'workers' } });
    try {
      env.receive(textMessage({ messageId: 'om-task', text: 'TASK: look into the slowness', chatId: 'oc-a' }));
      await env.waitForReply(/worker result for: TASK: look into the slowness/, 20_000);

      const input = env.codexInput();
      const brief = input.find((entry) => entry.method === 'exec');
      assert.match(brief.text, /^\[task t1\] mock task\nRequested by .* in chat=oc-a \(source=lark\)\./);
      assert.match(brief.text, /msg=om-task .*TASK: look into the slowness/);
      const reports = input.filter((entry) => /\[task report\]/.test(entry.text));
      assert.equal(reports.length, 1);
      assert.match(reports[0].text, /^\[task report\] task=t1 state=done title="mock task"/);
      const reply = env.replies().find((args) => args.some((arg) => /worker result/.test(arg)));
      assert.equal(reply[reply.indexOf('--message-id') + 1], 'om-task');

      const db = env.daemon.store.db;
      assert.deepEqual(db.prepare('SELECT id, status, native_session FROM tasks').all().map((row) => ({ ...row })),
        [{ id: 't1', status: 'done', native_session: 'th-worker' }]);
      assert.equal(db.prepare('SELECT structured FROM task_reports').get().structured, 1);
      assert.deepEqual(['task.created', 'task.turn_started', 'task.turn_completed', 'task.reported']
        .map((kind) => env.activities(kind).length), [1, 1, 1, 1]);
    } finally {
      await env.stop();
    }
  });
});
