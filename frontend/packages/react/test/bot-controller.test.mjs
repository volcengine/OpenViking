import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const { createChatController } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'bot-chat.js'))
);
const tick = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};
const service = (overrides = {}) => ({
  createSession: async () => ({ id: 'new', title: 'new' }),
  loadMessages: async () => [],
  async *stream() {
    yield { type: 'complete', text: 'answer' };
  },
  ...overrides,
});

test('acknowledged interruption is not reported as a completed answer', async () => {
  const controller = createChatController(
    service({
      async *stream() {
        yield { type: 'reasoning', text: 'partial', delta: true };
        yield { type: 'interrupted' };
      },
    }),
    {},
  );
  const stop = controller.start();
  assert.equal(await controller.send('question'), false);
  assert.equal(controller.getSnapshot().status, 'idle');
  assert.equal(
    controller.getSnapshot().messages.at(-1).parts[0].text,
    'partial',
  );
  stop();
});

test('EOF without terminal event is failure; partial answer survives and retry is possible', async () => {
  const controller = createChatController(
    service({
      async *stream() {
        yield { type: 'text', text: 'partial', delta: true };
      },
    }),
    {},
  );
  const stop = controller.start();
  assert.equal(await controller.send('question'), false);
  assert.equal(controller.getSnapshot().error.code, 'BOT_STREAM_TRUNCATED');
  assert.equal(
    controller.getSnapshot().messages.at(-1).parts[0].text,
    'partial',
  );
  assert.equal(await controller.send('retry'), false);
  assert.equal(controller.getSnapshot().messages.length, 4);
  stop();
});

test('changing session cancels old owner and ignores late history and stream results', async () => {
  const history = deferred();
  const stream = deferred();
  const controller = createChatController(
    service({
      loadMessages: async (id) => (id === 'old' ? history.promise : []),
      async *stream() {
        await stream.promise;
        yield { type: 'complete', text: 'late' };
      },
    }),
    { sessionId: 'old' },
  );
  const stop = controller.start();
  controller.selectSession('next');
  history.resolve([{ id: 'stale', role: 'assistant', parts: [] }]);
  await tick();
  const sending = controller.send('question');
  await tick();
  controller.reset();
  stream.resolve();
  assert.equal(await sending, false);
  assert.equal(controller.getSnapshot().sessionId, undefined);
  assert.deepEqual(controller.getSnapshot().messages, []);
  stop();
});

test('parallel sends are excluded, successful answer survives persistence failure and writes stay ordered', async () => {
  const gate = deferred();
  const saves = [];
  const controller = createChatController(
    service({
      saveMessage: async (_id, message) => {
        saves.push(message.role);
        if (saves.length === 1) await gate.promise;
        if (saves.length === 4) throw new Error('save failed');
      },
    }),
    {},
  );
  const stop = controller.start();
  const first = controller.send('one');
  assert.equal(await controller.send('overlap'), false);
  assert.equal(await first, true);
  assert.equal(await controller.send('two'), true);
  assert.deepEqual(saves, ['user']);
  gate.resolve();
  await tick();
  assert.deepEqual(saves, ['user', 'assistant', 'user', 'assistant']);
  assert.equal(controller.getSnapshot().status, 'idle');
  assert.equal(
    controller.getSnapshot().messages.at(-1).parts[0].text,
    'answer',
  );
  assert.equal(
    controller.getSnapshot().persistenceError.message,
    'save failed',
  );
  stop();
});

test('stop retains partial thinking, cancels the stream and persists the stopped turn', async () => {
  const saves = [];
  const controller = createChatController(
    service({
      saveMessage: async (_id, message) => {
        saves.push(message);
      },
      interrupt: async () => ({ interrupted: true }),
      async *stream(_input, signal) {
        yield { type: 'reasoning', text: 'thinking', delta: true };
        await new Promise((_resolve, reject) =>
          signal.addEventListener('abort', () => reject(signal.reason), {
            once: true,
          }),
        );
      },
    }),
    {},
  );
  const stop = controller.start();
  const sending = controller.send('question');
  await tick();
  await controller.abort();
  assert.equal(await sending, false);
  await tick();
  assert.equal(
    controller.getSnapshot().messages.at(-1).parts[0].running,
    false,
  );
  assert.equal(controller.getSnapshot().status, 'idle');
  assert.equal(saves.length, 2);
  stop();
});
