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

/** 构造一条事件流响应，chunks 按顺序写入。 */
function eventStream(chunks, init = {}) {
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, {
    status: init.status ?? 200,
    headers: { 'content-type': 'text/event-stream', ...(init.headers ?? {}) },
  });
}

async function collect(stream) {
  const events = [];
  for await (const event of stream) events.push(event);
  return events;
}

test('a stream yields one event per blank-line separated block', async () => {
  const fetch = fakeFetch(() =>
    eventStream(['data: {"a":1}\n\n', 'data: {"b":2}\n\n']),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  const stream = await client.stream({
    method: 'POST',
    path: OV_PATHS.botChatStream,
    body: { message: 'hi' },
  });

  assert.deepEqual(await collect(stream), [
    { data: '{"a":1}' },
    { data: '{"b":2}' },
  ]);
});

test('an event split across chunks is reassembled', async () => {
  const fetch = fakeFetch(() =>
    eventStream(['data: {"event":"con', 'tent_delta"}\n', '\n']),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  const stream = await client.stream({ method: 'POST', path: '/x' });
  assert.deepEqual(await collect(stream), [
    { data: '{"event":"content_delta"}' },
  ]);
});

test('multi-line data joins with newlines and other fields are kept', async () => {
  const fetch = fakeFetch(() =>
    eventStream(['id: 7\nevent: ping\nretry: 1500\ndata: a\ndata: b\n\n']),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  const stream = await client.stream({ method: 'POST', path: '/x' });
  assert.deepEqual(await collect(stream), [
    { data: 'a\nb', event: 'ping', id: '7', retry: 1500 },
  ]);
});

test('comment-only heartbeat blocks produce no event', async () => {
  const fetch = fakeFetch(() =>
    eventStream([': keepalive\n\n', 'data: {"ok":true}\n\n']),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  const stream = await client.stream({ method: 'POST', path: '/x' });
  assert.deepEqual(await collect(stream), [{ data: '{"ok":true}' }]);
});

test('a trailing block without a blank line is still dispatched', async () => {
  const fetch = fakeFetch(() => eventStream(['data: {"last":true}']));
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  const stream = await client.stream({ method: 'POST', path: '/x' });
  assert.deepEqual(await collect(stream), [{ data: '{"last":true}' }]);
});

test('a stream sends the event-stream accept header and the JSON body', async () => {
  const fetch = fakeFetch(() => eventStream(['data: x\n\n']));
  const client = createOvClient({
    baseURL: 'https://ov.test',
    apiKey: 'sk-1',
    fetch,
  });

  await client.stream({
    method: 'POST',
    path: OV_PATHS.botChatStream,
    body: { message: '', Attach: true },
    headers: { 'x-trace': 't1' },
  });

  const [{ init }] = fetch.calls;
  assert.equal(init.headers.accept, 'text/event-stream');
  assert.equal(init.headers['x-api-key'], 'sk-1');
  assert.equal(init.headers['x-trace'], 't1');
  assert.equal(init.headers['content-type'], 'application/json');
  assert.deepEqual(JSON.parse(init.body), { message: '', Attach: true });
});

test('a caller header cannot replace an identity header', async () => {
  const fetch = fakeFetch(() => eventStream(['data: x\n\n']));
  const client = createOvClient({
    baseURL: 'https://ov.test',
    apiKey: 'sk-1',
    fetch,
  });

  await client.stream({
    method: 'POST',
    path: '/x',
    headers: { 'x-api-key': 'forged' },
  });

  assert.equal(fetch.calls[0].init.headers['x-api-key'], 'sk-1');
});

test('a non-event-stream response becomes OvClientError', async () => {
  const fetch = fakeFetch(
    () =>
      new Response(JSON.stringify({ status: 'ok', result: [] }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  await assert.rejects(
    () => client.stream({ method: 'POST', path: '/x' }),
    (error) => {
      assert.ok(isOvClientError(error));
      assert.equal(error.code, 'UNEXPECTED_CONTENT_TYPE');
      return true;
    },
  );
});

test('an HTTP failure before the stream starts is reported with its status', async () => {
  const fetch = fakeFetch(
    () =>
      new Response(
        JSON.stringify({
          status: 'error',
          error: { code: 'NOT_FOUND', message: 'no bot' },
        }),
        { status: 404, headers: { 'content-type': 'application/json' } },
      ),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  await assert.rejects(
    () => client.stream({ method: 'POST', path: '/x' }),
    (error) => {
      assert.ok(isOvClientError(error));
      assert.equal(error.code, 'NOT_FOUND');
      assert.equal(error.statusCode, 404);
      return true;
    },
  );
});

test('an error envelope inside a non-stream 200 response is reported', async () => {
  const fetch = fakeFetch(
    () =>
      new Response(
        JSON.stringify({ status: 'error', error: { code: 'BUSY' } }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  await assert.rejects(
    () => client.stream({ method: 'POST', path: '/x' }),
    (error) => {
      assert.ok(isOvClientError(error));
      assert.equal(error.code, 'BUSY');
      return true;
    },
  );
});
