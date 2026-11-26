import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
const load = (name) =>
  import(pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, `${name}.js`)));
const { createRequestErrors } = await load('request-errors');
const { observeRequestErrors } = await load('observe-errors');
const spec = { method: 'GET', path: '/example' };
const transport = (operation) => ({
  baseURL: '/api',
  request: operation,
  requestEnvelope: operation,
  stream: operation,
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
