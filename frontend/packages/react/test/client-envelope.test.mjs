import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createOvClient,
  isOvClientError,
  OV_PATHS,
} from '@openviking/react/client';

test('directory pagination preserves has_more and plain requests return entries', async () => {
  const rows = [{ uri: 'viking://resources/docs' }];
  const responses = [
    { status: 'ok', result: rows, has_more: true },
    { status: 'ok', result: rows, has_more: false },
    { status: 'ok', result: rows },
    { status: 'ok', result: rows, has_more: true },
  ];
  const client = createOvClient({
    fetch: async () => Response.json(responses.shift()),
  });
  const spec = {
    method: 'GET',
    path: OV_PATHS.fsLs,
    query: { uri: 'viking://resources' },
  };
  for (const hasMore of [true, false, false]) {
    assert.deepEqual(await client.requestEnvelope(spec), {
      result: rows,
      hasMore,
    });
  }
  assert.deepEqual(await client.request(spec), rows);
});

test('requestEnvelope rejects a blob request instead of guessing', async () => {
  let sent = false;
  const client = createOvClient({
    fetch: async () => {
      sent = true;
      return new Response(new Uint8Array([1]));
    },
  });

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
  assert.equal(sent, false);
});
