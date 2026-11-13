import assert from 'node:assert/strict';
import { test } from 'node:test';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
const { createTaskTracker } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'task-tracker.js'))
);
const { readTaskReferences } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'task-references.js'))
);
const settle = async () => {
  for (let i = 0; i < 8; i++) await Promise.resolve();
};

const clock = (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 1000 });
  return async (ms = 0) => {
    t.mock.timers.tick(ms);
    await settle();
  };
};

test('task scheduler deduplicates across producers and preserves scope isolation', async (t) => {
  const advance = clock(t);
  const requests = [];
  const tracker = createTaskTracker();
  const service = {
    getTask: async (id) => {
      requests.push(id);
      return { id, status: 'completed' };
    },
  };
  const source = tracker.source(['account-a'], service);
  assert.equal(source, tracker.source(['account-a'], service));
  const other = tracker.source(['account-b'], service);
  const stop = tracker.start();
  tracker.track({ id: 'same' }, source);
  tracker.track({ id: 'same' }, source);
  tracker.track({ id: 'same' }, other);
  await advance();
  assert.deepEqual(requests, ['same', 'same']);
  await advance(30000);
  assert.equal(requests.length, 2);
  assert.equal(tracker.getSnapshot().length, 2);
  stop();
});

test('scheduler bounds concurrency, shares one timer, and never overlaps task requests', async (t) => {
  const advance = clock(t);
  const pending = [];
  const tracker = createTaskTracker({ concurrency: 2 });
  const source = tracker.source([], {
    getTask: (id) => new Promise((resolve) => pending.push({ id, resolve })),
  });
  const stop = tracker.start();
  for (const id of ['a', 'b', 'c']) tracker.track({ id }, source);
  await advance();
  assert.deepEqual(
    pending.map((item) => item.id),
    ['a', 'b'],
  );
  await advance(10000);
  assert.equal(pending.length, 2);
  pending[0].resolve({ id: 'a', status: 'running' });
  await settle();
  await advance();
  assert.equal(pending[2].id, 'c');
  pending[1].resolve({ id: 'b', status: 'completed' });
  pending[2].resolve({ id: 'c', status: 'completed' });
  await settle();
  await advance(2999);
  assert.equal(pending.length, 3);
  await advance(1);
  assert.equal(pending[3].id, 'a');
  stop();
});

test('query failures back off, preserve running state, then recover to backend failure', async (t) => {
  const advance = clock(t);
  let calls = 0;
  const tracker = createTaskTracker();
  const source = tracker.source([], {
    getTask: async (id) => {
      calls++;
      if (calls === 1) return { id, status: 'running', stage: 'parsing' };
      if (calls === 2) throw new Error('offline');
      return { id, status: 'failed', error: 'Invalid source' };
    },
  });
  const stop = tracker.start();
  tracker.track({ id: 'import' }, source);
  await advance();
  await advance(3000);
  assert.equal(tracker.getSnapshot()[0].status, 'running');
  assert.equal(tracker.getSnapshot()[0].queryError.message, 'offline');
  await advance(5999);
  assert.equal(calls, 2);
  await advance(1);
  assert.equal(tracker.getSnapshot()[0].status, 'failed');
  assert.equal(tracker.getSnapshot()[0].queryError, undefined);
  await advance(30000);
  assert.equal(calls, 3);
  stop();
});

test('releasing an old scope aborts its request, frees capacity and rejects stale results', async (t) => {
  const advance = clock(t);
  const tracker = createTaskTracker({ concurrency: 1 });
  let oldSignal, finish;
  const old = tracker.source(['old'], {
    getTask: (_, signal) => {
      oldSignal = signal;
      return new Promise((resolve) => {
        finish = resolve;
      });
    },
  });
  const release = tracker.retain(old);
  const releaseSecond = tracker.retain(old);
  const stop = tracker.start();
  tracker.track({ id: 'same' }, old);
  await advance();
  release();
  assert.equal(oldSignal.aborted, false);
  releaseSecond();
  assert.equal(oldSignal.aborted, true);
  const next = tracker.source(['next'], {
    getTask: async (id) => ({ id, status: 'completed' }),
  });
  tracker.track({ id: 'same' }, next);
  await advance();
  finish({ id: 'same', status: 'failed' });
  await settle();
  assert.equal(tracker.getSnapshot().length, 1);
  assert.equal(tracker.getSnapshot()[0].status, 'completed');
  stop();
});

test('only known submissions with structured IDs create task references', () => {
  assert.deepEqual(
    readTaskReferences('add-resource', { status: 'success', task_id: 'a' }),
    [{ id: 'a', kind: 'resource' }],
  );
  assert.deepEqual(
    readTaskReferences('task status', { task_id: 'a', status: 'running' }),
    [],
  );
  assert.deepEqual(
    readTaskReferences('compile create', { configured: true }),
    [],
  );
  assert.deepEqual(
    readTaskReferences(
      'openviking_memory_commit',
      JSON.stringify({ task_id: 'm', task_status: 'completed' }),
    ),
    [{ id: 'm', kind: 'memory', status: 'completed' }],
  );
  assert.deepEqual(
    readTaskReferences(
      'openviking_add_resource',
      'Successfully added resource: viking://resources/a',
    ),
    [],
  );
  assert.deepEqual(
    readTaskReferences('compile', {
      task_id: 'c',
      status: 'failed',
      error: 'compiler error',
    }),
    [{ id: 'c', kind: 'compile', status: 'failed' }],
  );
  assert.deepEqual(
    readTaskReferences('add-skill', {
      results: [{ task_id: 'a' }, { task_id: 'b' }, { task_id: 'a' }],
    }).map((task) => task.id),
    ['a', 'b'],
  );
});

test('cancelling remains active until the server confirms cancelled', async (t) => {
  const advance = clock(t);
  let calls = 0;
  const tracker = createTaskTracker();
  const source = tracker.source([], {
    getTask: (id) =>
      Promise.resolve({
        id,
        status: ++calls === 1 ? 'cancelling' : 'cancelled',
      }),
  });
  const stop = tracker.start();
  tracker.track({ id: 'cancel' }, source);
  await advance();
  assert.equal(tracker.getSnapshot()[0].status, 'cancelling');
  await advance(3000);
  assert.equal(tracker.getSnapshot()[0].status, 'cancelled');
  await advance(30000);
  assert.equal(calls, 2);
  stop();
});
