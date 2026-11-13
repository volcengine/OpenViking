import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const { createChatController } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'bot-chat.js'))
);
const tick = () => new Promise((resolve) => setImmediate(resolve));

test('consecutive creation failures recover every input once in one invocation, without client errors or duplicated history', async () => {
  let creations = 0;
  const inputs = [];
  const saved = [];
  const image = {
    key: 'image',
    name: 'fact.png',
    mimeType: 'image/png',
    previewUrl: 'https://demo.invalid/fact.png',
  };
  const reference = {
    uri: 'viking://resources/facts',
    name: 'facts',
    type: 'directory',
  };
  const controller = createChatController(
    {
      createSession: async () => {
        if (++creations <= 2) throw new Error('temporary creation failure');
        return { id: 'recovered', title: 'Recovered' };
      },
      loadMessages: async () => saved.slice(),
      saveMessage: async (_id, message) => saved.push(message),
      async *stream(input) {
        inputs.push(input);
        yield { type: 'complete', text: 'answer' };
      },
    },
    {},
  );
  const stop = controller.start();
  try {
    assert.equal(
      await controller.send({
        text: 'code-A-7f2d',
        attachments: [image],
        references: [reference],
      }),
      false,
    );
    assert.equal(await controller.send('code-B-9e3a'), false);
    assert.equal(controller.getSnapshot().sessionId, undefined);
    assert.equal(controller.getSnapshot().messages.length, 4);
    assert.equal(saved.length, 0);
    assert.equal(inputs.length, 0);
    assert.equal(await controller.send('What were my codes?'), true);
    await tick();
    assert.equal(creations, 3);
    assert.equal(inputs.length, 1);
    for (const code of ['code-A-7f2d', 'code-B-9e3a', 'What were my codes?'])
      assert.equal(inputs[0].text.split(code).length - 1, 1);
    assert.doesNotMatch(inputs[0].text, /temporary creation failure/);
    assert.deepEqual(inputs[0].attachments, [image]);
    assert.deepEqual(inputs[0].references, [reference]);
    assert.deepEqual(
      saved.map((message) => message.parts[0].text),
      ['code-A-7f2d', 'code-B-9e3a', 'What were my codes?', 'answer'],
    );
    assert.ok(saved.every((message) => !message.clientError));
    assert.equal(
      new Set(saved.map((message) => message.id)).size,
      saved.length,
    );
    await controller.reload();
    assert.deepEqual(controller.getSnapshot().messages, saved);
    assert.equal(await controller.send('next'), true);
    assert.equal(inputs[1].text, 'next');
    controller.reset();
    assert.deepEqual(controller.getSnapshot().messages, []);
    assert.equal(await controller.send('fresh'), true);
    assert.equal(inputs[2].text, 'fresh');
  } finally {
    stop();
  }
});

test('reset discards undelivered context and late creation failures cannot leak into the next conversation', async () => {
  let reject;
  let creations = 0;
  const inputs = [];
  const controller = createChatController(
    {
      createSession: async () => {
        if (++creations === 1)
          return new Promise((_resolve, fail) => {
            reject = fail;
          });
        return { id: 'fresh', title: 'Fresh' };
      },
      loadMessages: async () => [],
      async *stream(input) {
        inputs.push(input);
        yield { type: 'complete', text: 'ok' };
      },
    },
    {},
  );
  const stop = controller.start();
  try {
    const stale = controller.send('private old question');
    controller.reset();
    reject(new Error('late failure'));
    assert.equal(await stale, false);
    assert.equal(await controller.send('new question'), true);
    assert.equal(inputs.length, 1);
    assert.equal(inputs[0].text, 'new question');
    assert.ok(
      controller
        .getSnapshot()
        .messages.every((message) => !message.clientError),
    );
  } finally {
    stop();
  }
});
