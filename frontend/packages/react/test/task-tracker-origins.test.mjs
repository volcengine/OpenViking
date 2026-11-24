import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';

const { createTaskTracker } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'task-tracker.js'))
);

for (const origins of [
  ['command', 'tool'],
  ['tool', 'command'],
  ['command', 'memory-commit'],
  ['memory-commit', 'command'],
  ['command', 'command'],
]) {
  test(`shared polling preserves distinct task origins: ${origins.join(', ')}`, async (t) => {
    t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 1000 });
    const tracker = createTaskTracker();
    let calls = 0;
    const source = tracker.source([], {
      getTask: async (id) => {
        calls++;
        return { id, status: 'completed' };
      },
    });
    const stop = tracker.start();
    try {
      const keys = origins.map((origin) =>
        tracker.track(
          { id: 'shared' },
          source,
          undefined,
          origin === 'tool' ? undefined : origin,
        ),
      );
      assert.equal(new Set(keys).size, 1);
      assert.equal(tracker.getSnapshot().length, 1);
      const expected = [...new Set(origins)];
      assert.deepEqual(tracker.getSnapshot()[0].origins, expected);
      t.mock.timers.tick(0);
      for (let i = 0; i < 8; i++) await Promise.resolve();
      assert.equal(calls, 1);
      assert.equal(tracker.getSnapshot()[0].status, 'completed');
      assert.deepEqual(tracker.getSnapshot()[0].origins, expected);
    } finally {
      stop();
    }
  });
}
