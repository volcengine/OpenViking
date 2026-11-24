import assert from 'node:assert/strict';
import test from 'node:test';

import { createOvClient, OV_PATHS } from '@openviking/react/client';

test('an explicit stream abort and a caller signal both end an open iteration', async () => {
  const client = createOvClient({
    fetch: async () =>
      new Response(new ReadableStream(), {
        headers: { 'content-type': 'text/event-stream' },
      }),
  });
  for (const abortThroughSignal of [false, true]) {
    const controller = new AbortController();
    const stream = await client.stream({
      method: 'POST',
      path: OV_PATHS.botChatStream,
      options: { signal: controller.signal },
    });
    const iterator = stream[Symbol.asyncIterator]();
    const pending = iterator.next();
    if (abortThroughSignal) controller.abort();
    else stream.abort();
    assert.deepEqual(await pending, { done: true, value: undefined });
  }
});
