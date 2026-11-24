import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createOvClient,
  isOvClientError,
  OV_PATHS,
} from '@openviking/react/client';

const request = { method: 'POST', path: OV_PATHS.botChatStream };
const eventHeaders = { 'content-type': 'text/event-stream' };
const encoder = new TextEncoder();

async function collect(stream) {
  const events = [];
  for await (const event of stream) events.push(event);
  return events;
}

function fetchStream(chunks) {
  return async () =>
    new Response(
      new ReadableStream({
        start(controller) {
          for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
          controller.close();
        },
      }),
      { headers: eventHeaders },
    );
}

for (const [name, chunks, expected] of [
  [
    'LF, comments, fields and trailing data',
    [
      ': keepalive\n\nid: 7\nevent: ping\nretry: 1500\ndata: a\ndata: ',
      'b\n\ndata: last',
    ],
    [{ data: 'a\nb', event: 'ping', id: '7', retry: 1500 }, { data: 'last' }],
  ],
  [
    'CRLF split within a field and line ending',
    ['data: one\r', '\ndata: two\r\n\r\ndata: next\r\n\r\n'],
    [{ data: 'one\ntwo' }, { data: 'next' }],
  ],
  [
    'CR only',
    ['event: ping\rdata: a\r\r', 'data: b\r\r'],
    [{ data: 'a', event: 'ping' }, { data: 'b' }],
  ],
]) {
  test(`frames SSE chunks with ${name}`, async () => {
    const client = createOvClient({ fetch: fetchStream(chunks) });
    assert.deepEqual(await collect(await client.stream(request)), expected);
  });
}

test('sends a JSON request with the refreshed credential and SSE accept header', async () => {
  let credentialReads = 0;
  let sent;
  const client = createOvClient({
    apiKey: () => `key-${++credentialReads}`,
    fetch: async (_url, init) => {
      sent = init;
      return fetchStream(['data: accepted\n\n'])();
    },
  });
  const stream = await client.stream({
    ...request,
    body: { message: '', Attach: true },
    headers: { 'x-api-key': 'forged', 'x-trace': 't1' },
  });

  assert.deepEqual(await collect(stream), [{ data: 'accepted' }]);
  assert.equal(credentialReads, 1);
  assert.equal(sent.headers['x-api-key'], 'key-1');
  assert.equal(sent.headers['x-trace'], 't1');
  assert.equal(sent.headers.accept, 'text/event-stream');
  assert.equal(sent.headers['content-type'], 'application/json');
  assert.deepEqual(JSON.parse(sent.body), { message: '', Attach: true });
});

for (const [status, envelope, code] of [
  [200, { status: 'ok', result: [] }, 'UNEXPECTED_CONTENT_TYPE'],
  [
    404,
    { status: 'error', error: { code: 'NOT_FOUND', message: 'no bot' } },
    'NOT_FOUND',
  ],
  [200, { status: 'error', error: { code: 'BUSY' } }, 'BUSY'],
]) {
  test(`reports ${code} when the response cannot start a stream`, async () => {
    const client = createOvClient({
      fetch: async () =>
        new Response(JSON.stringify(envelope), {
          status,
          headers: { 'content-type': 'application/json' },
        }),
    });
    await assert.rejects(client.stream(request), (error) => {
      assert.ok(isOvClientError(error));
      assert.equal(error.code, code);
      if (status !== 200) assert.equal(error.statusCode, status);
      return true;
    });
  });
}

test(
  'passes the client abort signal to a pending fetch',
  { timeout: 2000 },
  async () => {
    const controller = new AbortController();
    const client = createOvClient({
      signal: controller.signal,
      fetch: async (_url, { signal }) => {
        signal.throwIfAborted();
        await new Promise((_resolve, reject) => {
          signal.addEventListener('abort', () => reject(signal.reason), {
            once: true,
          });
        });
      },
    });

    const pending = client.stream(request);
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

  await assert.rejects(client.stream(request));
  assert.equal(add.mock.callCount(), 1);
  assert.deepEqual(
    remove.mock.calls[0].arguments.slice(0, 2),
    add.mock.calls[0].arguments.slice(0, 2),
  );
  assert.equal(remove.mock.callCount(), 1);
});

for (const exit of ['break', 'abort pending read']) {
  test(
    `dispatches a CR event before EOF and cancels the body on ${exit}`,
    { timeout: 2000 },
    async () => {
      let cancel;
      const cancelled = new Promise((resolve) => {
        cancel = resolve;
      });
      const body = new ReadableStream({
        start(controller) {
          controller.enqueue(encoder.encode('data: one\r\r'));
        },
        cancel,
      });
      const client = createOvClient({
        fetch: async () => new Response(body, { headers: eventHeaders }),
      });

      const stream = await client.stream(request);
      if (exit === 'break') {
        for await (const event of stream) {
          assert.deepEqual(event, { data: 'one' });
          break;
        }
      } else {
        const iterator = stream[Symbol.asyncIterator]();
        assert.deepEqual((await iterator.next()).value, { data: 'one' });
        const pending = iterator.next();
        stream.abort();
        assert.equal((await pending).done, true);
      }
      await cancelled;
    },
  );
}
