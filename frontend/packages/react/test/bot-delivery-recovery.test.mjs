import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const { createChatController } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'bot-chat.js'))
);
const tick = () => new Promise((resolve) => setImmediate(resolve));

for (const generationStarted of [false, true]) {
  test(`recovered input survives pre-delivery failures but is not replayed after generation starts (started=${generationStarted})`, async () => {
    const inputs = [];
    const saved = [];
    let creations = 0;
    const controller = createChatController(
      {
        createSession: async () => {
          if (++creations === 1) throw new Error('creation failed');
          return { id: 'recovered', title: 'Recovered' };
        },
        loadMessages: async () => saved.slice(),
        saveMessage: async (_id, message) => saved.push(message),
        async *stream(input) {
          inputs.push(input);
          if (inputs.length === 1) {
            if (generationStarted) yield { type: 'iteration', iteration: 1 };
            throw new Error('connection failed');
          }
          yield { type: 'complete', text: 'answer' };
        },
      },
      {},
    );
    const stop = controller.start();
    try {
      assert.equal(await controller.send('fact-A-829c'), false);
      assert.equal(await controller.send('question-B-37fe'), false);
      assert.equal(controller.getSnapshot().sessionId, 'recovered');
      assert.equal(await controller.send('question-C-a430'), true);
      await tick();
      assert.equal(creations, 2);
      assert.equal(inputs.length, 2);
      for (const fact of ['fact-A-829c', 'question-B-37fe']) {
        assert.equal(
          inputs[1].text.split(fact).length - 1,
          generationStarted ? 0 : 1,
        );
      }
      assert.equal(inputs[1].text.split('question-C-a430').length - 1, 1);
      assert.doesNotMatch(inputs[1].text, /creation failed|connection failed/);
      assert.deepEqual(
        saved.map((message) => message.parts[0].text),
        ['fact-A-829c', 'question-B-37fe', 'question-C-a430', 'answer'],
      );
      assert.equal(new Set(saved.map((message) => message.id)).size, 4);
      await controller.reload();
      assert.deepEqual(controller.getSnapshot().messages, saved);
    } finally {
      stop();
    }
  });
}
