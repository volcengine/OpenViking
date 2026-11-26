import assert from 'node:assert/strict';
import test from 'node:test';

import { createOvClient, OV_PATHS } from '@openviking/react/client';

test(
  'dispatches CR-delimited events before EOF and cancels the body on break or abort',
  { timeout: 2000 },
  async () => {
    const event = new TextEncoder().encode('data: one\r\r');
    for (const exit of ['break', 'stream abort', 'caller abort']) {
      let cancelBody;
      const cancelled = new Promise((resolve) => {
        cancelBody = resolve;
      });
      const body = new ReadableStream({
        start(controller) {
          controller.enqueue(event);
        },
        cancel: cancelBody,
      });
      const client = createOvClient({
        fetch: async () =>
          new Response(body, {
            headers: { 'content-type': 'text/event-stream' },
          }),
      });

      const controller = new AbortController();
      const stream = await client.stream({
        method: 'POST',
        path: OV_PATHS.botChatStream,
        options: { signal: controller.signal },
      });
      if (exit === 'break') {
        for await (const event of stream) {
          assert.deepEqual(event, { data: 'one' });
          break;
        }
      } else {
        const iterator = stream[Symbol.asyncIterator]();
        assert.deepEqual((await iterator.next()).value, { data: 'one' });
        const pending = iterator.next();
        if (exit === 'caller abort') controller.abort();
        else stream.abort();
        assert.deepEqual(await pending, { done: true, value: undefined });
      }
      await cancelled;
    }
  },
);
