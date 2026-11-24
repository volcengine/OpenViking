import assert from 'node:assert/strict';
import test from 'node:test';

import { createOvClient } from '@openviking/react/client';

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

test('abort ends the iteration quietly instead of rejecting', async () => {
  const fetch = fakeFetch(
    () =>
      new Response(new ReadableStream({ start() {} }), {
        status: 200,
        headers: { 'content-type': 'text/event-stream' },
      }),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  const stream = await client.stream({ method: 'POST', path: '/x' });
  const pending = (async () => {
    const events = [];
    for await (const event of stream) events.push(event);
    return events;
  })();
  stream.abort();

  // 取消是调用方的意图，迭代应正常结束而不是抛 AbortError。
  assert.deepEqual(await pending, []);
});

test('abort through the caller signal also ends the iteration', async () => {
  const controller = new AbortController();
  const fetch = fakeFetch(
    () =>
      new Response(new ReadableStream({ start() {} }), {
        status: 200,
        headers: { 'content-type': 'text/event-stream' },
      }),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  const stream = await client.stream({
    method: 'POST',
    path: '/x',
    options: { signal: controller.signal },
  });
  controller.abort();

  const events = [];
  for await (const event of stream) events.push(event);
  assert.deepEqual(events, []);
});
