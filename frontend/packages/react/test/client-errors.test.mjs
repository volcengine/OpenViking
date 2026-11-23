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

function json(body, init = {}) {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { 'content-type': 'application/json', ...(init.headers ?? {}) },
  });
}

const ok = () => json({ status: 'ok', result: null });

test('a native status:error becomes OvClientError even on HTTP 200', async () => {
  const fetch = fakeFetch(() =>
    json({
      status: 'error',
      error: { code: 'NOT_FOUND', message: 'no such uri' },
    }),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  await assert.rejects(
    () => client.request({ method: 'GET', path: '/api/v1/x' }),
    (error) => {
      assert.ok(isOvClientError(error));
      assert.equal(error.code, 'NOT_FOUND');
      assert.equal(error.statusCode, 200);
      return true;
    },
  );
});

test('an HTTP failure becomes OvClientError with its status and request id', async () => {
  const fetch = fakeFetch(() =>
    json(
      { error: { code: 'UNAUTHENTICATED', message: 'missing api key' } },
      { status: 401, headers: { 'x-request-id': 'req_1' } },
    ),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  await assert.rejects(
    () => client.request({ method: 'GET', path: '/api/v1/x' }),
    (error) => {
      assert.ok(isOvClientError(error));
      assert.equal(error.code, 'UNAUTHENTICATED');
      assert.equal(error.statusCode, 401);
      assert.equal(error.requestId, 'req_1');
      return true;
    },
  );
});

test('an HTTP failure without a server code falls back to HTTP_<status>', async () => {
  const fetch = fakeFetch(() => new Response('boom', { status: 500 }));
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  await assert.rejects(
    () => client.request({ method: 'GET', path: '/api/v1/x' }),
    (error) => {
      assert.equal(error.code, 'HTTP_500');
      return true;
    },
  );
});

test('query skips undefined and null and repeats array keys', async () => {
  const fetch = fakeFetch(ok);
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  await client.request({
    method: 'GET',
    path: '/api/v1/x',
    query: { a: 1, b: undefined, c: null, d: ['x', 'y'], e: true },
  });

  assert.equal(
    fetch.calls[0].url,
    'https://ov.test/api/v1/x?a=1&d=x&d=y&e=true',
  );
});

test('a body is serialised and a body-less request sends none', async () => {
  const fetch = fakeFetch(ok);
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  await client.request({ method: 'POST', path: '/api/v1/x', body: { a: 1 } });
  await client.request({ method: 'GET', path: '/api/v1/x' });

  assert.equal(fetch.calls[0].init.body, '{"a":1}');
  assert.equal(fetch.calls[1].init.body, undefined);
});
