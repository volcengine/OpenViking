import assert from 'node:assert/strict';
import test from 'node:test';

import { createOvClient, isOvClientError } from '@openviking/react/client';

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

test('a JSON body declares its content type', async () => {
  const fetch = fakeFetch(ok);
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  await client.request({ method: 'POST', path: '/api/v1/x', body: { a: 1 } });

  assert.equal(
    fetch.calls[0].init.headers['content-type'],
    'application/json',
    'a serialized body must be sent as JSON',
  );
});

test('a body-less request sends no content type', async () => {
  const fetch = fakeFetch(ok);
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  await client.request({ method: 'GET', path: '/api/v1/x' });

  assert.equal(fetch.calls[0].init.headers['content-type'], undefined);
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

test('an omitted identity field still inherits', async () => {
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
    options: { accountId: 'other-account' },
  });

  const sent = fetch.calls[0].init.headers;
  assert.equal(sent['x-api-key'], 'parent-key', 'the credential is inherited');
  assert.equal(sent['x-openviking-account'], 'other-account');
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

test('a default identity callback runs once per request', async () => {
  const fetch = fakeFetch(ok);
  let calls = 0;
  const client = createOvClient({
    baseURL: 'https://ov.test',
    apiKey: () => `token-${++calls}`,
    fetch,
  });

  await client.request({ method: 'GET', path: '/api/v1/x' });

  assert.equal(calls, 1, 'the callback must not be evaluated twice');
  assert.equal(
    fetch.calls[0].init.headers['x-api-key'],
    'token-1',
    'the value sent is the one the single evaluation produced',
  );
});

test('a default identity callback runs once even when the request overrides another field', async () => {
  const fetch = fakeFetch(ok);
  let calls = 0;
  const client = createOvClient({
    baseURL: 'https://ov.test',
    apiKey: () => `token-${++calls}`,
    accountId: 'acc_1',
    fetch,
  });

  await client.request({
    method: 'GET',
    path: '/api/v1/x',
    options: { resourceId: 'lib_2' },
  });

  assert.equal(calls, 1);
  assert.equal(fetch.calls[0].init.headers['x-api-key'], 'token-1');
  assert.equal(fetch.calls[0].init.headers['v-resource-id'], 'lib_2');
});

test('each request evaluates the callback again', async () => {
  const fetch = fakeFetch(ok);
  let calls = 0;
  const client = createOvClient({
    baseURL: 'https://ov.test',
    apiKey: () => `token-${++calls}`,
    fetch,
  });

  await client.request({ method: 'GET', path: '/api/v1/x' });
  await client.request({ method: 'GET', path: '/api/v1/x' });

  assert.equal(calls, 2, 'one evaluation per request');
  assert.equal(fetch.calls[0].init.headers['x-api-key'], 'token-1');
  assert.equal(fetch.calls[1].init.headers['x-api-key'], 'token-2');
});
