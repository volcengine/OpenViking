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

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

test('requestEnvelope returns has_more from outside result', async () => {
  const fetch = fakeFetch(() =>
    json({ status: 'ok', result: [{ id: 1 }], has_more: true }),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  const envelope = await client.requestEnvelope({
    method: 'GET',
    path: '/api/v1/fs/ls',
    query: { uri: 'viking://a' },
  });

  assert.deepEqual(envelope.result, [{ id: 1 }]);
  assert.equal(envelope.hasMore, true);
});

test('hasMore is false when the response carries no flag', async () => {
  const fetch = fakeFetch(() => json({ status: 'ok', result: [] }));
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  const envelope = await client.requestEnvelope({
    method: 'GET',
    path: '/api/v1/fs/ls',
  });

  assert.deepEqual(envelope.result, []);
  assert.equal(envelope.hasMore, false);
});

test('hasMore is false for an explicit false flag', async () => {
  const fetch = fakeFetch(() =>
    json({ status: 'ok', result: [], has_more: false }),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  const envelope = await client.requestEnvelope({
    method: 'GET',
    path: '/api/v1/fs/ls',
  });

  assert.equal(envelope.hasMore, false);
});

test('requestEnvelope reports the same failures as request', async () => {
  const fetch = fakeFetch(() =>
    json({ status: 'error', error: { code: 'NOT_FOUND' } }, 404),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  await assert.rejects(
    () => client.requestEnvelope({ method: 'GET', path: '/api/v1/fs/ls' }),
    (error) => {
      assert.ok(isOvClientError(error));
      assert.equal(error.code, 'NOT_FOUND');
      return true;
    },
  );
});

test('requestEnvelope rejects a blob request instead of guessing', async () => {
  const fetch = fakeFetch(
    () => new Response(new Uint8Array([1]), { status: 200 }),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  await assert.rejects(
    () =>
      client.requestEnvelope({
        method: 'GET',
        path: '/api/v1/content/download',
        responseType: 'blob',
      }),
    (error) => {
      assert.ok(isOvClientError(error));
      assert.equal(error.code, 'INVALID_REQUEST');
      return true;
    },
  );
  assert.equal(fetch.calls.length, 0, 'no request should be sent');
});

test('request still returns only result', async () => {
  const fetch = fakeFetch(() =>
    json({ status: 'ok', result: [{ id: 1 }], has_more: true }),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  const result = await client.request({ method: 'GET', path: '/api/v1/fs/ls' });
  assert.deepEqual(result, [{ id: 1 }]);
});
