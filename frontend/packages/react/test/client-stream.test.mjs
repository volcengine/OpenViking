import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createOvClient,
  isOvClientError,
  OV_PATHS,
} from '@openviking/react/client';

const request = { method: 'POST', path: OV_PATHS.botChatStream };

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
function eventStream(chunks) {
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, {
    headers: { 'content-type': 'text/event-stream' },
  });
}

async function collect(stream) {
  const events = [];
  for await (const event of stream) events.push(event);
  return events;
}

test('reads and reassembles stream events while keeping request identity', async () => {
  let credentialReads = 0;
  const fetch = fakeFetch(() =>
    eventStream([
      ': keepalive\r\n\r\n',
      'id: 7\r\nevent: ping\r\nretry: 1500\r\ndata: a\r',
      '\ndata: b\r\n\r\ndata: {"event":"con',
      'tent_delta"}\r\n',
      '\r\n',
      'data: {"last":true}',
    ]),
  );
  const client = createOvClient({
    baseURL: 'https://ov.test',
    apiKey: () => 'sk-' + ++credentialReads,
    fetch,
  });
  const stream = await client.stream({
    ...request,
    body: { message: '', Attach: true },
    headers: { 'x-api-key': 'forged', 'x-trace': 't1' },
  });

  const [{ init }] = fetch.calls;
  assert.equal(init.headers.accept, 'text/event-stream');
  assert.equal(init.headers['x-api-key'], 'sk-1');
  assert.equal(init.headers['x-trace'], 't1');
  assert.equal(init.headers['content-type'], 'application/json');
  assert.deepEqual(JSON.parse(init.body), { message: '', Attach: true });
  assert.deepEqual(await collect(stream), [
    { data: 'a\nb', event: 'ping', id: '7', retry: 1500 },
    { data: '{"event":"content_delta"}' },
    { data: '{"last":true}' },
  ]);
  assert.equal(credentialReads, 1);
});

test('distinguishes HTTP and business failures from an unexpected stream response', async () => {
  const cases = [
    [
      Response.json({ status: 'ok', result: [] }),
      'UNEXPECTED_CONTENT_TYPE',
      200,
    ],
    [
      Response.json(
        { status: 'error', error: { code: 'NOT_FOUND' } },
        { status: 404 },
      ),
      'NOT_FOUND',
      404,
    ],
    [Response.json({ status: 'error', error: { code: 'BUSY' } }), 'BUSY', 200],
  ];
  for (const [response, code, status] of cases) {
    const client = createOvClient({ fetch: async () => response });
    await assert.rejects(client.stream(request), (error) => {
      assert.ok(isOvClientError(error));
      assert.equal(error.code, code);
      assert.equal(error.statusCode, status);
      return true;
    });
  }
});

test(
  'passes the client abort signal to a pending fetch',
  { timeout: 2000 },
  async () => {
    const controller = new AbortController();
    let start;
    const started = new Promise((resolve) => {
      start = resolve;
    });
    const client = createOvClient({
      signal: controller.signal,
      fetch: async (_url, { signal }) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener('abort', () => reject(signal.reason), {
            once: true,
          });
          start();
        }),
    });

    const pending = client.stream(request);
    await started;
    controller.abort();
    await assert.rejects(pending, { name: 'AbortError' });
  },
);

test('detaches the client signal listener after a transport failure', async (t) => {
  const { signal } = new AbortController();
  const add = t.mock.method(signal, 'addEventListener');
  const remove = t.mock.method(signal, 'removeEventListener');
  const client = createOvClient({
    signal,
    fetch: async () => {
      throw new TypeError('network down');
    },
  });

  await assert.rejects(client.stream(request), { code: 'NETWORK_ERROR' });
  assert.equal(add.mock.callCount(), 1);
  assert.equal(remove.mock.callCount(), 1);
  assert.deepEqual(
    remove.mock.calls[0].arguments.slice(0, 2),
    add.mock.calls[0].arguments.slice(0, 2),
  );
});
