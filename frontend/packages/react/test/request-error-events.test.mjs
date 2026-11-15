import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
const load = (name) =>
  import(pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, `${name}.js`)));
const { createRequestErrors } = await load('request-errors');
const { observeRequestErrors, localRequestErrors } =
  await load('observe-errors');
const spec = { method: 'GET', path: '/example' };
const transport = (operation) => ({
  baseURL: '/api',
  request: operation,
  requestEnvelope: operation,
  stream: operation,
});

test('request observers publish one failure and preserve the exact rejection and success value', async () => {
  const error = new Error('backend failure');
  const events = createRequestErrors();
  let updates = 0;
  const stop = events.subscribe(() => updates++);
  const client = observeRequestErrors(
    transport(async () => {
      throw error;
    }),
    events.publish,
  );
  for (const method of ['request', 'requestEnvelope', 'stream']) {
    await assert.rejects(client[method](spec), (reason) => reason === error);
    assert.equal(events.getSnapshot().length, updates);
  }
  assert.equal(updates, 3);
  assert.equal(client.baseURL, '/api');
  assert.equal(
    events.wasReported(new Error('wrapped', { cause: error })),
    true,
  );
  const value = { result: 'unchanged' };
  assert.equal(
    await observeRequestErrors(
      transport(async () => value),
      events.publish,
    ).request(spec),
    value,
  );
  stop();
  events.dismiss(1);
  assert.equal(updates, 3);
  assert.equal(events.getSnapshot().length, 2);
});

test('local feedback, aborted scopes and cancellation do not publish', async () => {
  const events = createRequestErrors();
  const error = new Error('failure');
  const raw = transport(async () => {
    throw error;
  });
  const observed = observeRequestErrors(raw, events.publish);
  for (const method of ['request', 'requestEnvelope', 'stream'])
    await assert.rejects(
      localRequestErrors(observed)[method](spec),
      (reason) => reason === error,
    );
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(
    observed.request({ ...spec, options: { signal: controller.signal } }),
  );
  await assert.rejects(
    observeRequestErrors(raw, events.publish, controller.signal).request(spec),
  );
  await assert.rejects(
    observeRequestErrors(
      transport(async () => {
        throw new DOMException('cancelled', 'AbortError');
      }),
      events.publish,
    ).request(spec),
  );
  assert.deepEqual(events.getSnapshot(), []);
});

test('stream iteration failures publish once without losing abort or event values', async () => {
  const events = createRequestErrors();
  const error = new Error('stream failed');
  let aborted = false;
  const stream = {
    abort() {
      aborted = true;
    },
    async *[Symbol.asyncIterator]() {
      yield { data: 'first' };
      throw error;
    },
  };
  const client = observeRequestErrors(
    transport(async () => stream),
    events.publish,
  );
  const opened = await client.stream(spec);
  const iterator = opened[Symbol.asyncIterator]();
  assert.deepEqual((await iterator.next()).value, { data: 'first' });
  await assert.rejects(iterator.next(), (reason) => reason === error);
  opened.abort();
  assert.equal(aborted, true);
  assert.equal(events.getSnapshot().length, 1);
});

test('channels stay isolated and observer failure cannot replace an API failure', async () => {
  const first = createRequestErrors();
  const second = createRequestErrors();
  const error = new Error('original');
  first.publish({ error, request: spec });
  assert.equal(second.wasReported(error), false);
  assert.equal(second.getSnapshot().length, 0);
  await assert.rejects(
    observeRequestErrors(
      transport(async () => {
        throw error;
      }),
      () => {
        throw new Error('observer');
      },
    ).request(spec),
    (reason) => reason === error,
  );
});
