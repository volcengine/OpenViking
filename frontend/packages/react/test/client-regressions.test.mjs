import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createOvClient,
  isOvClientError,
  OV_PATHS,
} from '@openviking/react/client';

/** 记录每次调用，并按 handler 返回预设响应。 */
function fakeFetch(handler) {
  const calls = [];
  const impl = async (url, init) => {
    calls.push({ url, init });
    return handler(url, init);
  };
  impl.calls = calls;
  return impl;
}

const ok = () =>
  new Response(JSON.stringify({ status: 'ok', result: null }), {
    headers: { 'content-type': 'application/json' },
  });

test('a rejected fetch becomes OvClientError with NETWORK_ERROR', async () => {
  const fetch = async () => {
    throw new TypeError('Failed to fetch');
  };
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  await assert.rejects(
    () => client.request({ method: 'GET', path: '/api/v1/x' }),
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
        path: '/api/v1/x',
        options: { signal: controller.signal },
      }),
    (error) => {
      assert.equal(error.name, 'AbortError', 'cancellation stays cancellable');
      assert.equal(isOvClientError(error), false);
      return true;
    },
  );
});

test('an explicit empty identity override removes the inherited header', async () => {
  const fetch = fakeFetch(ok);
  const client = createOvClient({
    baseURL: 'https://ov.test',
    apiKey: 'parent-key',
    accountId: 'parent-account',
    fetch,
  });

  await client.request({
    method: 'GET',
    path: '/api/v1/x',
    options: { apiKey: '', accountId: '' },
  });

  const sent = fetch.calls[0].init.headers;
  assert.equal(sent['x-api-key'], undefined, 'the credential is cleared');
  assert.equal(
    sent['x-openviking-account'],
    undefined,
    'the account is cleared',
  );
});

test('a per-request baseURL overrides the client one', async () => {
  const fetch = fakeFetch(ok);
  const client = createOvClient({ baseURL: 'https://ov.test/gateway', fetch });

  await client.request({
    method: 'GET',
    path: '/api/v1/x',
    options: { baseURL: 'https://other.test' },
  });
  await client.request({
    method: 'GET',
    path: '/api/v1/x',
    options: { baseURL: '' },
  });

  assert.equal(fetch.calls[0].url, 'https://other.test/api/v1/x');
  assert.equal(
    fetch.calls[1].url,
    '/api/v1/x',
    'an empty baseURL is same origin',
  );
});

test('requests resolve one fresh credential even with a per-request identity override', async () => {
  const fetch = fakeFetch(ok);
  let calls = 0;
  const client = createOvClient({
    baseURL: 'https://ov.test',
    apiKey: () => `token-${++calls}`,
    accountId: 'acc_1',
    resourceId: 'lib_1',
    fetch,
  });

  await client.request({ method: 'GET', path: OV_PATHS.sessions });
  await client.request({
    method: 'GET',
    path: OV_PATHS.sessions,
    options: { resourceId: 'lib_2' },
  });
  await client.request({
    method: 'GET',
    path: OV_PATHS.sessions,
    options: { accountId: 'acc_2' },
  });

  assert.equal(calls, 3);
  assert.deepEqual(
    fetch.calls.map(({ init }) => init.headers),
    [
      {
        'x-api-key': 'token-1',
        'x-openviking-account': 'acc_1',
        'v-resource-id': 'lib_1',
      },
      {
        'x-api-key': 'token-2',
        'x-openviking-account': 'acc_1',
        'v-resource-id': 'lib_2',
      },
      {
        'x-api-key': 'token-3',
        'x-openviking-account': 'acc_2',
        'v-resource-id': 'lib_1',
      },
    ],
  );
});
