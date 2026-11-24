import assert from 'node:assert/strict';
import test from 'node:test';
import { getEventListeners } from 'node:events';
import { runtime, setup, streamBody, settle } from './helpers/monitoring.mjs';
const make = (frame, fetch, options) =>
  runtime.bindClient(
    runtime.createOvClient({
      baseURL: 'https://private.invalid/gateway',
      fetch,
      ...options,
    }),
    frame,
  );

test('stream bytes include framing, heartbeats and UTF-8 without consuming twice', async () => {
  const { frame, events } = setup('Console');
  const text =
    ': heartbeat\r\ndata: 私密文本\r\n\r\nid: event-2\r\ndata: two\r\n\r\n';
  let calls = 0;
  const client = make(frame, async () => {
    calls++;
    return new Response(streamBody(text), {
      headers: { 'content-type': 'text/event-stream' },
    });
  });
  const stream = await client.stream({
    method: 'POST',
    path: '/bot/v1/chat/stream',
  });
  const frames = [];
  for await (const frame of stream) frames.push(frame);
  assert.equal(calls, 1);
  assert.equal(frames.length, 2);
  assert.deepEqual(
    events.map((event) => event.name),
    ['client_stream_connect_ready', 'client_stream_read_complete'],
  );
  assert.equal(
    events[1].params.bytesRead,
    new TextEncoder().encode(text).byteLength,
  );
  assert.equal(events[1].params.frameCount, 2);
  assert.equal(events[1].params.nativeEventId, 'event-2');
  assert.equal(events[0].trace.traceId, events[1].trace.traceId);
  assert.equal(events[1].trace.parentSpanId, events[0].trace.spanId);
  assert.equal(JSON.stringify(events).includes('私密文本'), false);
});

test('aborting the inherited caller signal is neither stream completion nor failure', async () => {
  const { frame, events } = setup('Console');
  const controller = new AbortController();
  const client = make(
    frame,
    async () =>
      new Response(new ReadableStream({ start() {} }), {
        headers: { 'content-type': 'text/event-stream' },
      }),
    { signal: controller.signal },
  );
  const stream = await client.stream({
    method: 'POST',
    path: '/bot/v1/chat/stream',
  });
  const iterator = stream[Symbol.asyncIterator]();
  const next = iterator.next();
  controller.abort();
  await next.catch(() => {});
  assert.deepEqual(
    events.map((event) => event.name),
    ['client_stream_connect_ready'],
  );
});

test('caller cancellation before iteration releases diagnostic bodies without unmount', async () => {
  for (const inherited of [false, true]) {
    const events = [];
    const { frame, controller } = setup('Console', {
      onEvent: (event) => events.push(event),
      diagnostics: { transform: () => ({}) },
    });
    let cancellations = 0;
    for (let i = 0; i < 5; i++) {
      const caller = new AbortController();
      const client = make(
        frame,
        async () =>
          new Response(
            new ReadableStream({
              cancel() {
                cancellations++;
              },
            }),
            { headers: { 'content-type': 'text/event-stream' } },
          ),
        inherited ? { signal: caller.signal } : undefined,
      );
      await client.stream({
        method: 'POST',
        path: '/bot/v1/chat/stream',
        body: { text: 'x'.repeat(1024 * 1024) },
        options: inherited ? undefined : { signal: caller.signal },
      });
      assert.equal(controller.rawReleases.size, 1);
      caller.abort();
      assert.equal(
        controller.rawReleases.size,
        0,
        'cancelled streams must release raw diagnostic data',
      );
    }
    await settle();
    assert.equal(cancellations, 5);
    assert.ok(
      frame.active(),
      'cleanup must not require unmounting the component',
    );
    assert.deepEqual(
      events.map((event) => event.name),
      Array(5).fill('client_stream_connect_ready'),
    );
    frame.dispose();
  }
});

test('stream read failures retain diagnostics until reporting and remove cancellation listeners', async () => {
  const events = [],
    body = { secret: 'request body' };
  const { frame, controller } = setup('Console', {
    onEvent: (event) => events.push(event),
    diagnostics: {
      transform(input) {
        assert.equal(input.requestBody, body);
        return { requestBody: { redacted: true } };
      },
    },
  });
  const caller = new AbortController();
  const client = make(
    frame,
    async () =>
      new Response(
        new ReadableStream({
          pull(stream) {
            stream.error(new Error('read failed'));
          },
        }),
        { headers: { 'content-type': 'text/event-stream' } },
      ),
  );
  const stream = await client.stream({
    method: 'POST',
    path: '/bot/v1/chat/stream',
    body,
    options: { signal: caller.signal },
  });
  await assert.rejects(async () => {
    for await (const _event of stream) {
      /* drain */
    }
  });
  const failure = events.find(
    (event) => event.name === 'client_stream_read_fail',
  );
  assert.ok(failure);
  assert.deepEqual(failure.diagnostics.requestBody, { redacted: true });
  assert.equal(controller.rawReleases.size, 0);
  assert.equal(getEventListeners(caller.signal, 'abort').length, 0);
  frame.dispose();
});

test('cancellation by the connection-ready receiver releases diagnostics before returning', async () => {
  const caller = new AbortController();
  const { frame, controller } = setup('Console', {
    onEvent(event) {
      if (event.name === 'client_stream_connect_ready') caller.abort();
    },
    diagnostics: { transform: () => ({}) },
  });
  const client = make(
    frame,
    async () =>
      new Response(new ReadableStream({}), {
        headers: { 'content-type': 'text/event-stream' },
      }),
  );
  await client.stream({
    method: 'POST',
    path: '/bot/v1/chat/stream',
    body: 'private',
    options: { signal: caller.signal },
  });
  assert.equal(controller.rawReleases.size, 0);
  assert.equal(getEventListeners(caller.signal, 'abort').length, 0);
  frame.dispose();
});
