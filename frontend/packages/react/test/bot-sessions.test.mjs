import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const { createSessionsController } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'bot-sessions.js'))
);
const tick = () => new Promise((resolve) => setImmediate(resolve));

test('stale list cannot overwrite a successful creation; removal failure preserves sessions and can retry', async () => {
  let resolveList;
  let fail = true;
  const controller = createSessionsController({
    listSessions: () =>
      new Promise((resolve) => {
        resolveList = resolve;
      }),
    createSession: async () => ({ id: 'new', title: 'new' }),
    removeSession: async () => {
      if (fail) throw new Error('remove failed');
    },
  });
  const stop = controller.start();
  assert.equal((await controller.create()).id, 'new');
  resolveList([{ id: 'stale', title: 'stale' }]);
  await tick();
  assert.deepEqual(
    controller.getSnapshot().sessions.map((item) => item.id),
    ['new'],
  );
  assert.equal(await controller.remove('new'), false);
  assert.equal(controller.getSnapshot().sessions.length, 1);
  fail = false;
  assert.equal(await controller.remove('new'), true);
  assert.equal(controller.getSnapshot().sessions.length, 0);
  stop();
});
