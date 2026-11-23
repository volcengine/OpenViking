import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const { createChatController } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'bot-chat.js'))
);
const tick = () => new Promise((resolve) => setImmediate(resolve));
const gate = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};
const service = (extra) => ({
  createSession: async () => ({ id: 's', title: 's' }),
  loadMessages: async () => [],
  ...extra,
});

test('accepted steering stays on one stream and splits persisted turns at the next iteration even when it precedes the HTTP ack', async () => {
  const advance = gate();
  const ack = gate();
  const saved = [];
  let streams = 0;
  let steers = 0;
  const controller = createChatController(
    service({
      steer: async (input) => {
        steers++;
        assert.equal(input.text, 'follow up');
        return ack.promise;
      },
      saveMessage: async (_id, message) => {
        saved.push(message);
      },
      async *stream() {
        streams++;
        yield { type: 'reasoning', text: 'before', delta: true };
        await advance.promise;
        yield { type: 'iteration', iteration: 2 };
        yield { type: 'text', text: 'after', delta: true };
        yield { type: 'complete' };
      },
    }),
    {},
  );
  const stop = controller.start();
  const first = controller.send('question');
  await tick();
  const follow = controller.send('follow up');
  assert.equal(controller.getSnapshot().isSubmitting, true);
  assert.equal(await controller.send('duplicate'), false);
  advance.resolve();
  await tick();
  assert.equal(
    controller.getSnapshot().messages.length,
    1,
    'unaccepted input is not shown',
  );
  ack.resolve({ accepted: true });
  assert.equal(await follow, true);
  assert.equal(await first, true);
  await tick();
  assert.equal(streams, 1);
  assert.equal(steers, 1);
  const messages = controller.getSnapshot().messages;
  assert.deepEqual(
    messages.map((item) => item.role),
    ['user', 'assistant', 'user', 'assistant'],
  );
  assert.deepEqual(
    messages.map((item) => item.parts[0].text),
    ['question', 'before', 'follow up', 'after'],
  );
  assert.equal(messages[0].turnId, messages[1].turnId);
  assert.equal(messages[2].turnId, messages[3].turnId);
  assert.notEqual(messages[0].turnId, messages[2].turnId);
  assert.deepEqual(
    saved.map((item) => item.parts[0].text),
    ['question', 'before', 'follow up', 'after'],
  );
  assert.equal(controller.getSnapshot().isSubmitting, false);
  stop();
});

test('rejected input leaves no phantom user turn and releases the submission lock while the stream continues', async () => {
  const advance = gate();
  let accepted = false;
  const controller = createChatController(
    service({
      steer: async () => ({ accepted }),
      async *stream() {
        yield { type: 'reasoning', text: 'before', delta: true };
        await advance.promise;
        yield { type: 'iteration', iteration: 2 };
        yield { type: 'complete', text: 'answer' };
      },
    }),
    {},
  );
  const stop = controller.start();
  const first = controller.send('question');
  await tick();
  assert.equal(await controller.send('rejected'), false);
  assert.equal(controller.getSnapshot().error.code, 'BOT_INPUT_REJECTED');
  assert.equal(controller.getSnapshot().messages.length, 1);
  assert.equal(controller.getSnapshot().isSubmitting, false);
  accepted = true;
  assert.equal(await controller.send('retry'), true);
  advance.resolve();
  await first;
  assert.deepEqual(
    controller
      .getSnapshot()
      .messages.filter((item) => item.role === 'user')
      .map((item) => item.parts[0].text),
    ['question', 'retry'],
  );
  stop();
});

test('a commercial adapter rejects in-flight input without adding a turn or calling another stream', async () => {
  const advance = gate();
  let streams = 0;
  const controller = createChatController(
    service({
      async *stream() {
        streams++;
        yield { type: 'text', text: 'partial', delta: true };
        await advance.promise;
        yield { type: 'complete' };
      },
    }),
    {},
  );
  const stop = controller.start();
  const first = controller.send('question');
  await tick();
  assert.equal(await controller.send('unsupported'), false);
  assert.equal(controller.getSnapshot().messages.length, 1);
  advance.resolve();
  await first;
  assert.equal(streams, 1);
  stop();
});

test('late steering acceptance after reset cannot enter the new session', async () => {
  const ack = gate();
  const advance = gate();
  const controller = createChatController(
    service({
      steer: async () => ack.promise,
      async *stream() {
        yield { type: 'text', text: 'partial', delta: true };
        await advance.promise;
        yield { type: 'complete' };
      },
    }),
    {},
  );
  const stop = controller.start();
  const first = controller.send('question');
  await tick();
  const follow = controller.send('late');
  controller.reset();
  ack.resolve({ accepted: true });
  advance.resolve();
  assert.equal(await follow, false);
  assert.equal(await first, false);
  assert.deepEqual(controller.getSnapshot().messages, []);
  assert.equal(controller.getSnapshot().isSubmitting, false);
  stop();
});
