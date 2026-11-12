import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const { createChatController } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'bot-chat.js'))
);
const tick = () => new Promise((resolve) => setImmediate(resolve));

test('switching sessions keeps completed writes alive; history waits for those writes', async () => {
  let release;
  const saved = [];
  let loads = 0;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const controller = createChatController(
    {
      createSession: async () => ({ id: 's', title: 's' }),
      async *stream() {
        yield { type: 'complete', text: 'answer' };
      },
      saveMessage: async (_id, message, signal) => {
        await gate;
        signal.throwIfAborted();
        saved.push(message);
      },
      loadMessages: async () => {
        loads++;
        return [...saved];
      },
    },
    {},
  );
  const stop = controller.start();
  assert.equal(await controller.send('question'), true);
  controller.reset();
  controller.selectSession('s');
  await tick();
  assert.equal(loads, 0);
  release();
  await tick();
  assert.equal(loads, 1);
  assert.deepEqual(
    controller.getSnapshot().messages.map((item) => item.role),
    ['user', 'assistant'],
  );
  stop();
});

test('failed interrupt remains visible after local stream cleanup', async () => {
  const controller = createChatController(
    {
      createSession: async () => ({ id: 's', title: 's' }),
      loadMessages: async () => [],
      interrupt: async () => {
        throw new Error('interrupt failed');
      },
      async *stream(_input, signal) {
        yield { type: 'text', text: 'partial', delta: true };
        await new Promise((_resolve, reject) =>
          signal.addEventListener('abort', () => reject(signal.reason), {
            once: true,
          }),
        );
      },
    },
    {},
  );
  const stop = controller.start();
  const sending = controller.send('question');
  await tick();
  await controller.abort();
  assert.equal(await sending, false);
  assert.equal(controller.getSnapshot().error.message, 'interrupt failed');
  assert.equal(controller.getSnapshot().status, 'idle');
  stop();
});
