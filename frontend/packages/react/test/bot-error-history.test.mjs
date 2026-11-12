import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const { createChatController } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'bot-chat.js'))
);
const tick = () => new Promise((resolve) => setImmediate(resolve));
for (const truncated of [false, true])
  test(`failed steered turn persists completed segments, accepted input while keeping partial reply local (EOF=${truncated})`, async () => {
    let resume;
    const gate = new Promise((resolve) => {
      resume = resolve;
    });
    const saved = [];
    let calls = 0;
    const controller = createChatController(
      {
        createSession: async () => ({ id: 's', title: 's' }),
        loadMessages: async () => saved.slice(),
        saveMessage: async (_id, message) => {
          saved.push(message);
        },
        steer: async () => ({ accepted: true }),
        async *stream() {
          if (++calls > 1) {
            yield { type: 'complete', text: 'A3' };
            return;
          }
          yield { type: 'text', text: 'A1', delta: true };
          await gate;
          yield { type: 'iteration', iteration: 2 };
          yield { type: 'text', text: 'partial A2', delta: true };
          if (!truncated) throw new Error('network failure');
        },
      },
      {},
    );
    const stop = controller.start();
    const first = controller.send('Q1');
    await tick();
    assert.equal(await controller.send('Q2'), true);
    resume();
    assert.equal(await first, false);
    await tick();
    assert.deepEqual(
      saved.map((m) => m.parts[0].text),
      ['Q1', 'A1', 'Q2'],
    );
    assert.equal(controller.getSnapshot().status, 'error');
    assert.equal(
      controller.getSnapshot().messages.at(-1).parts[0].text,
      'partial A2',
    );
    assert.equal(await controller.send('Q3'), true);
    await tick();
    await controller.reload();
    assert.deepEqual(
      controller.getSnapshot().messages.map((m) => m.parts[0].text),
      ['Q1', 'A1', 'Q2', 'Q3', 'A3'],
    );
    stop();
  });
