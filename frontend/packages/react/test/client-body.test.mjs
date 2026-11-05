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

test('a failure while reading the response body is reported, not swallowed', async () => {
  const fetch = fakeFetch(
    () =>
      new Response(null, {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
  );
  // 让 response.text() 抛错，模拟连接在读完响应头后中断
  const originalText = Response.prototype.text;
  Response.prototype.text = async () => {
    throw new TypeError('terminated');
  };
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  try {
    await assert.rejects(
      () => client.request({ method: 'GET', path: '/api/v1/x' }),
      (error) => {
        assert.ok(isOvClientError(error), 'body-read failure must be reported');
        assert.equal(error.code, 'NETWORK_ERROR');
        return true;
      },
    );
  } finally {
    Response.prototype.text = originalText;
  }
});

test('a cancellation during body read keeps its AbortError', async () => {
  const fetch = fakeFetch(() => new Response(null, { status: 200 }));
  const originalText = Response.prototype.text;
  Response.prototype.text = async () => {
    throw Object.assign(new Error('aborted'), { name: 'AbortError' });
  };
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  try {
    await assert.rejects(
      () => client.request({ method: 'GET', path: '/api/v1/x' }),
      (error) => {
        assert.equal(error.name, 'AbortError');
        return true;
      },
    );
  } finally {
    Response.prototype.text = originalText;
  }
});

test('a non-JSON response body is returned as text rather than dropped', async () => {
  const fetch = fakeFetch(() => new Response('plain text', { status: 200 }));
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  const result = await client.request({ method: 'GET', path: '/api/v1/x' });
  assert.equal(result, 'plain text');
});
