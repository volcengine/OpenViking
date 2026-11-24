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

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

test('directory pagination reads the envelope and request returns entries only', async () => {
  const uri = 'viking://resources/guides';
  const firstPage = [{ uri: `${uri}/intro.md` }];
  const lastPage = [{ uri: `${uri}/setup.md` }];
  const fetch = fakeFetch((url) => {
    const offset = new URL(url).searchParams.get('offset');
    return offset === '1'
      ? json({ status: 'ok', result: lastPage, has_more: false })
      : json({ status: 'ok', result: firstPage, has_more: true });
  });
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });
  const list = {
    method: 'GET',
    path: OV_PATHS.fsLs,
    query: { uri, node_limit: 1, offset: 0 },
  };

  const first = await client.requestEnvelope(list);
  assert.deepEqual(first, { result: firstPage, hasMore: true });
  const last = await client.requestEnvelope({
    ...list,
    query: { ...list.query, offset: first.result.length },
  });
  assert.deepEqual(last, { result: lastPage, hasMore: false });
  assert.deepEqual(await client.request(list), firstPage);
});

test('hasMore is false when the response carries no flag', async () => {
  const fetch = fakeFetch(() => json({ status: 'ok', result: [] }));
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  const envelope = await client.requestEnvelope({
    method: 'GET',
    path: OV_PATHS.sessions,
  });

  assert.deepEqual(envelope.result, []);
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
