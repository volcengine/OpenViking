import assert from 'node:assert/strict';
import test from 'node:test';
import { runtime, setup, streamBody } from './helpers/monitoring.mjs';
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
