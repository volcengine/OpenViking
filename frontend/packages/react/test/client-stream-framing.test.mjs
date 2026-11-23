import assert from 'node:assert/strict';
import test from 'node:test';

import { createOvClient, OV_PATHS } from '@openviking/react/client';

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

test('a CRLF stream frames events on its blank lines', async () => {
  // 服务端用 \r\n 换行时，空行是 \r\n\r\n；只按 \n\n 分帧会把整条流当成一个事件。
  // With \r\n line endings a blank line is \r\n\r\n; framing on \n\n alone would
  // treat the whole stream as one event.
  const fetch = fakeFetch(() =>
    eventStream(['data: {"a":1}\r\n\r\n', 'data: {"b":2}\r\n\r\n']),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  const stream = await client.stream({ method: 'POST', path: '/x' });
  assert.deepEqual(await collect(stream), [
    { data: '{"a":1}' },
    { data: '{"b":2}' },
  ]);
});

test('a CRLF split across chunks is not cut in half', async () => {
  // 分块边界落在一个 \r\n 中间：末尾孤立的 \r 可能是下一个 \r\n 的前半段，不能
  // 当成行尾就地分帧，也不能让正则把 \r\n 回溯成两个终止符。
  //
  // A chunk boundary falls inside one \r\n: a trailing lone \r may be the first
  // half of the next \r\n, so it must not frame, and the pattern must not backtrack
  // \r\n into two terminators.
  const fetch = fakeFetch(() =>
    eventStream(['data: one\r', '\ndata: two\r\n\r\n']),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  const stream = await client.stream({ method: 'POST', path: '/x' });
  assert.deepEqual(await collect(stream), [{ data: 'one\ntwo' }]);
});

test('CR-only line endings split fields and frame events', async () => {
  const fetch = fakeFetch(() =>
    eventStream(['event: ping\rdata: a\r\r', 'data: b\r\r']),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  const stream = await client.stream({ method: 'POST', path: '/x' });
  assert.deepEqual(await collect(stream), [
    { data: 'a', event: 'ping' },
    { data: 'b' },
  ]);
});

test('a client-level signal aborts an in-flight stream', async () => {
  // 只读 spec.options.signal 时，createOvClient({ signal }) 取消后传给 fetch 的
  // 仍是未中止的那个，流不会结束。
  //
  // Reading only spec.options.signal left the signal handed to fetch un-aborted when
  // createOvClient({ signal }) was cancelled, so the stream never ended.
  const controller = new AbortController();
  const abortedError = () =>
    Object.assign(new Error('aborted'), { name: 'AbortError' });
  const fetch = fakeFetch(
    (_url, init) =>
      new Promise((_resolve, reject) => {
        // 取消可能已经发生在 fetch 之前：先查 `aborted`，否则监听器永远等不到事件。
        // The abort may already have happened before fetch: check `aborted` first, or
        // the listener never receives an event.
        if (init.signal?.aborted) {
          reject(abortedError());
          return;
        }
        init.signal?.addEventListener('abort', () => reject(abortedError()));
      }),
  );
  const client = createOvClient({
    baseURL: 'https://ov.test',
    fetch,
    signal: controller.signal,
  });

  const pending = client.stream({ method: 'POST', path: '/x' });
  controller.abort();
  await assert.rejects(pending, (error) => error.name === 'AbortError');
});

test('breaking out of iteration releases the underlying body', async () => {
  // 提前 break 只关闭包装层的生成器不会取消响应体，长连接的 SSE 会在消费者退出
  // 后继续占着连接。
  //
  // An early break closes only the wrapper generator, not the response body, so a
  // long-lived SSE connection keeps running after its consumer exits.
  let cancelled = false;
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode('data: one\n\n'));
    },
    cancel() {
      cancelled = true;
    },
  });
  const fetch = fakeFetch(
    () =>
      new Response(body, {
        status: 200,
        headers: { 'content-type': 'text/event-stream' },
      }),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });

  const stream = await client.stream({ method: 'POST', path: '/x' });
  for await (const event of stream) {
    assert.deepEqual(event, { data: 'one' });
    break;
  }
  // 取消传到响应体是异步的，让出一次事件循环再断言。
  // Cancellation reaches the body asynchronously; yield once before asserting.
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(cancelled, true);
});

test('a CR-only event dispatches while the stream stays open', async () => {
  // 纯 CR 换行时 `data: one\r\r` 本身已经是完整事件。若把 `\r` 留在缓冲区等下一
  // 个 chunk，流保持打开时这个事件永远发不出去。
  //
  // With CR-only endings `data: one\r\r` is already a complete event. Holding the
  // `\r` for the next chunk means it never dispatches while the stream stays open.
  const encoder = new TextEncoder();
  let controller;
  const body = new ReadableStream({
    start(c) {
      controller = c;
    },
  });
  const fetch = fakeFetch(
    () =>
      new Response(body, {
        status: 200,
        headers: { 'content-type': 'text/event-stream' },
      }),
  );
  const client = createOvClient({ baseURL: 'https://ov.test', fetch });
  const stream = await client.stream({ method: 'POST', path: '/x' });
  const iterator = stream[Symbol.asyncIterator]();

  controller.enqueue(encoder.encode('data: one\r\r'));
  // 不关闭流，直接等第一个事件：它必须已经到达。
  // Without closing the stream, await the first event: it must already be there.
  const first = await iterator.next();
  assert.deepEqual(first.value, { data: 'one' });

  controller.enqueue(encoder.encode('data: two\r\r'));
  const second = await iterator.next();
  assert.deepEqual(second.value, { data: 'two' });
  controller.close();
});
