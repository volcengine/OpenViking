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

function json(body, init = {}) {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { 'content-type': 'application/json', ...(init.headers ?? {}) },
  });
}

const ok = () => json({ status: 'ok', result: null });

test('joins the gateway prefix with the API path', async () => {
  const fetch = fakeFetch(() => json({ status: 'ok', result: [] }));
  const client = createOvClient({
    baseURL: 'https://ov.test/gateway/openviking',
    fetch,
  });

  await client.request({
    method: 'GET',
    path: OV_PATHS.fsLs,
    query: { uri: 'viking://a' },
  });

  assert.equal(
    fetch.calls[0].url,
    'https://ov.test/gateway/openviking/api/v1/fs/ls?uri=viking%3A%2F%2Fa',
  );
});

test('returns the native result rather than the envelope', async () => {
  const fetch = fakeFetch(() => json({ status: 'ok', result: [{ id: 1 }] }));
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  const result = await client.request({ method: 'GET', path: '/api/v1/x' });
  assert.deepEqual(result, [{ id: 1 }]);
});

test('identity fields become request headers', async () => {
  const fetch = fakeFetch(ok);
  const client = createOvClient({
    baseURL: 'https://ov.test',
    apiKey: 'sk-open-source',
    accountId: 'acc_1',
    userId: 'user_1',
    resourceId: 'lib_1',
    fetch,
  });

  await client.request({ method: 'GET', path: '/api/v1/x' });

  assert.deepEqual(fetch.calls[0].init.headers, {
    'x-api-key': 'sk-open-source',
    'x-openviking-account': 'acc_1',
    'x-openviking-user': 'user_1',
    'v-resource-id': 'lib_1',
  });
});

test('an omitted identity field sends no header', async () => {
  const fetch = fakeFetch(ok);
  const client = createOvClient({
    baseURL: 'https://ov.test',
    accountId: 'acc_1',
    userId: 'user_1',
    resourceId: 'lib_1',
    fetch,
  });

  await client.request({ method: 'GET', path: '/api/v1/x' });

  assert.deepEqual(fetch.calls[0].init.headers, {
    'x-openviking-account': 'acc_1',
    'x-openviking-user': 'user_1',
    'v-resource-id': 'lib_1',
  });
});

test('a per-request identity field replaces the client one', async () => {
  const fetch = fakeFetch(ok);
  const client = createOvClient({
    baseURL: 'https://ov.test',
    accountId: 'acc_1',
    userId: 'user_1',
    fetch,
  });

  await client.request({
    method: 'GET',
    path: '/api/v1/x',
    options: { resourceId: 'lib_2' },
  });

  const sent = fetch.calls[0].init.headers;
  assert.equal(sent['v-resource-id'], 'lib_2');
  assert.equal(sent['x-openviking-account'], 'acc_1');
  assert.equal(
    Object.keys(sent).filter((k) => k === 'v-resource-id').length,
    1,
    'the header appears once',
  );
});

test('an apiKey function is re-evaluated on every request', async () => {
  const fetch = fakeFetch(ok);
  let token = 'first';
  const client = createOvClient({
    baseURL: 'https://ov.test',
    apiKey: () => token,
    fetch,
  });

  await client.request({ method: 'GET', path: '/api/v1/x' });
  token = 'second';
  await client.request({ method: 'GET', path: '/api/v1/x' });

  assert.equal(fetch.calls[0].init.headers['x-api-key'], 'first');
  assert.equal(fetch.calls[1].init.headers['x-api-key'], 'second');
});

test('credentials default to same-origin and can be overridden', async () => {
  const fetch = fakeFetch(ok);

  await createOvClient({ baseURL: 'https://ov.test', fetch }).request({
    method: 'GET',
    path: '/api/v1/x',
  });
  await createOvClient({
    baseURL: 'https://ov.test',
    credentials: 'include',
    fetch,
  }).request({ method: 'GET', path: '/api/v1/x' });

  assert.equal(fetch.calls[0].init.credentials, 'same-origin');
  assert.equal(fetch.calls[1].init.credentials, 'include');
});
