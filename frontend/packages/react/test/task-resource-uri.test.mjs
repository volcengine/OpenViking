import assert from 'node:assert/strict';
import { test } from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const hooks = process.env.OV_UI_TEST_HOOKS;
const { createTaskTracker } = await import(
  pathToFileURL(join(hooks, 'task-tracker.js'))
);
const { readTaskReferences } = await import(
  pathToFileURL(join(hooks, 'task-references.js'))
);

test('task snapshots expose the final resource URI and preserve the submitted fallback', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 1000 });
  const tracker = createTaskTracker();
  const final = 'viking://resources/final';
  const custom = 'viking://resources/custom';
  const submitted = 'viking://resources/submitted';
  const results = {
    final: {
      result: { root_uri: 'https://example.com', archive_uri: ` ${final} ` },
      uri: submitted,
    },
    custom: { uri: custom },
    fallback: { result: { to: 'javascript:alert(1)' }, uri: undefined },
  };
  const source = tracker.source([], {
    getTask: async (id) => ({ id, status: 'completed', ...results[id] }),
  });
  const stop = tracker.start();
  try {
    for (const task_id of Object.keys(results)) {
      const [reference] = readTaskReferences('add-resource', {
        task_id,
        root_uri: '',
        to: ` ${submitted} `,
      });
      tracker.track(reference, source);
    }
    assert.deepEqual(
      tracker.getSnapshot().map((task) => task.uri),
      [submitted, submitted, submitted],
    );
    t.mock.timers.tick(0);
    for (let i = 0; i < 8; i++) await Promise.resolve();
    assert.deepEqual(
      tracker.getSnapshot().map(({ status, uri }) => [status, uri]),
      [
        ['completed', final],
        ['completed', custom],
        ['completed', submitted],
      ],
    );
  } finally {
    stop();
  }
});
