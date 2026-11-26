import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createOvClient,
  isOvClientError,
  OV_PATHS,
} from '@openviking/react/client';

test('reports business, authentication, and unstructured HTTP errors', async () => {
  const responses = [
    [
      Response.json({
        status: 'error',
        error: { code: 'NOT_FOUND', message: 'no such uri' },
      }),
      { code: 'NOT_FOUND', statusCode: 200 },
      'request',
    ],
    [
      Response.json(
        { error: { code: 'UNAUTHENTICATED', message: 'missing api key' } },
        { status: 401, headers: { 'x-request-id': 'req_1' } },
      ),
      { code: 'UNAUTHENTICATED', statusCode: 401, requestId: 'req_1' },
      'requestEnvelope',
    ],
    [
      new Response('boom', { status: 500 }),
      { code: 'HTTP_500', statusCode: 500 },
      'request',
    ],
  ];
  for (const [response, expected, method] of responses) {
    const client = createOvClient({ fetch: async () => response });
    await assert.rejects(
      client[method]({
        method: 'GET',
        path: OV_PATHS.fsLs,
      }),
      (error) => {
        assert.ok(isOvClientError(error));
        for (const [field, value] of Object.entries(expected)) {
          assert.equal(error[field], value, field);
        }
        return true;
      },
    );
  }
});

test('a rejected fetch becomes OvClientError with NETWORK_ERROR', async () => {
  const fetch = async () => {
    throw new TypeError('Failed to fetch');
  };
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  await assert.rejects(
    () => client.request({ method: 'GET', path: OV_PATHS.fsLs }),
    (error) => {
      assert.ok(isOvClientError(error), 'must be the documented error type');
      assert.equal(error.code, 'NETWORK_ERROR');
      assert.equal(error.cause?.message, 'Failed to fetch');
      return true;
    },
  );
});

test('an aborted request keeps its AbortError', async () => {
  const controller = new AbortController();
  const abort = Object.assign(new Error('aborted'), { name: 'AbortError' });
  const fetch = async () => {
    controller.abort();
    throw abort;
  };
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  await assert.rejects(
    () =>
      client.request({
        method: 'GET',
        path: OV_PATHS.fsLs,
        options: { signal: controller.signal },
      }),
    (error) => {
      assert.equal(error.name, 'AbortError', 'cancellation stays cancellable');
      assert.equal(isOvClientError(error), false);
      return true;
    },
  );
});
