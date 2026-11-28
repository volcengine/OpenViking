import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createOvClient,
  isOvClientError,
  OV_PATHS,
} from '@openviking/react/client';

const PNG_BYTES = new Uint8Array([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
]);

test('downloads authenticated file bytes without treating them as JSON', async () => {
  const client = createOvClient({
    baseURL: 'https://ov.test',
    apiKey: 'test-key',
    resourceId: 'library',
    fetch: async (url, init) => {
      assert.equal(
        new URL(url).searchParams.get('uri'),
        'viking://resources/a.png',
      );
      const headers = new Headers(init.headers);
      assert.equal(headers.get('x-api-key'), 'test-key');
      assert.equal(headers.get('v-resource-id'), 'library');
      return new Response(PNG_BYTES);
    },
  });

  const blob = await client.request({
    method: 'GET',
    path: OV_PATHS.contentDownload,
    query: { uri: 'viking://resources/a.png' },
    responseType: 'blob',
  });
  assert.ok(blob instanceof Blob);
  assert.deepEqual(new Uint8Array(await blob.arrayBuffer()), PNG_BYTES);
});

test('a blob request reports the JSON error envelope of a failed response', async () => {
  const client = createOvClient({
    fetch: async () =>
      Response.json(
        { status: 'error', error: { code: 'NOT_FOUND', message: 'missing' } },
        { status: 404 },
      ),
  });

  await assert.rejects(
    () =>
      client.request({
        method: 'GET',
        path: OV_PATHS.contentDownload,
        query: { uri: 'viking://resources/missing.png' },
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

test('a broken response body fails the binary download', async () => {
  const client = createOvClient({
    fetch: async () =>
      new Response(
        new ReadableStream({
          start(controller) {
            controller.error(new TypeError('connection terminated'));
          },
        }),
      ),
  });

  await assert.rejects(
    client.request({
      method: 'GET',
      path: OV_PATHS.contentDownload,
      query: { uri: 'viking://resources/a.png' },
      responseType: 'blob',
    }),
    (error) => isOvClientError(error) && error.code === 'NETWORK_ERROR',
  );
});
