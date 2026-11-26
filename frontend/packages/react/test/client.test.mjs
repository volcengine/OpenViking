import assert from 'node:assert/strict';
import test from 'node:test';

import { createOvClient, OV_PATHS } from '@openviking/react/client';

test('joins the gateway prefix and applies per-request URL overrides', async () => {
  const urls = [];
  const client = createOvClient({
    baseURL: 'https://ov.test/gateway/openviking',
    fetch: async (url) => {
      urls.push(url);
      return Response.json({ status: 'ok', result: [] });
    },
  });

  await client.request({
    method: 'GET',
    path: OV_PATHS.fsLs,
    query: {
      uri: 'viking://resources',
      tags: ['x', 'y'],
      include_tags: true,
      node_limit: 5,
      limit: undefined,
      offset: null,
    },
  });
  for (const baseURL of ['https://other.test', '']) {
    await client.request({
      method: 'GET',
      path: OV_PATHS.fsLs,
      options: { baseURL },
    });
  }

  assert.deepEqual(urls, [
    'https://ov.test/gateway/openviking/api/v1/fs/ls?uri=viking%3A%2F%2Fresources&tags=x&tags=y&include_tags=true&node_limit=5',
    'https://other.test/api/v1/fs/ls',
    '/api/v1/fs/ls',
  ]);
});

test('inherits, overrides, and clears identity per request with fresh credentials', async () => {
  const requests = [];
  let evaluations = 0;
  const client = createOvClient({
    baseURL: 'https://ov.test',
    apiKey: () => `token-${++evaluations}`,
    accountId: 'acc_1',
    userId: 'user_1',
    resourceId: 'lib_1',
    fetch: async (_url, init) => {
      requests.push(init.headers);
      return Response.json({ status: 'ok', result: [] });
    },
  });

  const list = { method: 'GET', path: OV_PATHS.fsLs };
  await client.request(list);
  await client.request({
    ...list,
    options: { accountId: 'acc_2', resourceId: 'lib_2' },
  });
  assert.equal(evaluations, 2);
  await client.request({ ...list, options: { apiKey: '', accountId: '' } });

  assert.deepEqual(requests, [
    {
      'x-api-key': 'token-1',
      'x-openviking-account': 'acc_1',
      'x-openviking-user': 'user_1',
      'v-resource-id': 'lib_1',
    },
    {
      'x-api-key': 'token-2',
      'x-openviking-account': 'acc_2',
      'x-openviking-user': 'user_1',
      'v-resource-id': 'lib_2',
    },
    { 'x-openviking-user': 'user_1', 'v-resource-id': 'lib_1' },
  ]);
});

test('uses browser credentials by default with no configured API key', async () => {
  const credentials = [];
  const fetch = async (_url, init) => {
    assert.deepEqual(init.headers, { 'x-openviking-account': 'acc_1' });
    credentials.push(init.credentials);
    return Response.json({ status: 'ok', result: [] });
  };

  for (const configured of [undefined, 'same-origin', 'omit']) {
    const client = createOvClient({
      fetch,
      credentials: configured,
      accountId: 'acc_1',
    });
    await client.request({ method: 'GET', path: OV_PATHS.fsLs });
  }
  assert.deepEqual(credentials, ['include', 'same-origin', 'omit']);
});

test('creating a session sends JSON and listing sessions sends no body', async () => {
  const requests = [];
  const client = createOvClient({
    fetch: async (_url, init) => {
      requests.push(init);
      return Response.json({ status: 'ok', result: null });
    },
  });

  await client.request({
    method: 'POST',
    path: OV_PATHS.sessions,
    body: { session_id: 'demo' },
  });
  await client.request({ method: 'GET', path: OV_PATHS.sessions });

  assert.equal(requests[0].headers['content-type'], 'application/json');
  assert.deepEqual(JSON.parse(requests[0].body), { session_id: 'demo' });
  assert.equal(requests[1].headers['content-type'], undefined);
  assert.equal(requests[1].body, undefined);
});
