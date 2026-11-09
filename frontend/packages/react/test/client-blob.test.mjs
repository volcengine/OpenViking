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

const PNG_BYTES = new Uint8Array([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
]);

test('responseType blob returns the raw bytes instead of parsing them', async () => {
  const fetch = fakeFetch(
    () =>
      new Response(PNG_BYTES, {
        status: 200,
        headers: { 'content-type': 'application/octet-stream' },
      }),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  const blob = await client.request({
    method: 'GET',
    path: OV_PATHS.contentDownload,
    query: { uri: 'viking://a.png' },
    responseType: 'blob',
  });

  assert.ok(blob instanceof Blob);
  assert.equal(blob.size, PNG_BYTES.length);
  assert.deepEqual(new Uint8Array(await blob.arrayBuffer()), PNG_BYTES);
  assert.equal(
    fetch.calls[0].url,
    'https://ov.test/api/v1/content/download?uri=viking%3A%2F%2Fa.png',
  );
});

test('a blob request still sends the identity headers', async () => {
  const fetch = fakeFetch(() => new Response(PNG_BYTES, { status: 200 }));
  const client = createOvClient({
    baseURL: 'https://ov.test',
    apiKey: 'sk-1',
    resourceId: 'lib_1',
    fetch,
  });

  await client.request({
    method: 'GET',
    path: OV_PATHS.contentDownload,
    responseType: 'blob',
  });

  const { init } = fetch.calls[0];
  assert.equal(init.headers['x-api-key'], 'sk-1');
  assert.equal(init.headers['v-resource-id'], 'lib_1');
  // 二进制请求没有 body，也就不该声明 JSON 内容类型。
  assert.equal(init.headers['content-type'], undefined);
});

test('a blob request reports the JSON error envelope of a failed response', async () => {
  const fetch = fakeFetch(
    () =>
      new Response(
        JSON.stringify({
          status: 'error',
          error: { code: 'NOT_FOUND', message: 'missing' },
        }),
        { status: 404, headers: { 'content-type': 'application/json' } },
      ),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  await assert.rejects(
    () =>
      client.request({
        method: 'GET',
        path: OV_PATHS.contentDownload,
        responseType: 'blob',
      }),
    (error) => {
      assert.ok(isOvClientError(error));
      assert.equal(error.code, 'NOT_FOUND');
      assert.equal(error.statusCode, 404);
      return true;
    },
  );
});

test('a blob response read failure is reported as a transport error', async () => {
  const fetch = fakeFetch(
    () =>
      new Response(null, {
        status: 200,
        headers: { 'content-type': 'application/octet-stream' },
      }),
  );
  const originalBlob = Response.prototype.blob;
  Response.prototype.blob = async () => {
    throw new TypeError('terminated');
  };
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  try {
    await assert.rejects(
      () =>
        client.request({
          method: 'GET',
          path: OV_PATHS.contentDownload,
          responseType: 'blob',
        }),
      (error) => {
        assert.ok(isOvClientError(error));
        assert.equal(error.code, 'NETWORK_ERROR');
        return true;
      },
    );
  } finally {
    Response.prototype.blob = originalBlob;
  }
});
