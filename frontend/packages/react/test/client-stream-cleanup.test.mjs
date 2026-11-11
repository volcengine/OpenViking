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

test('a transport rejection still detaches the caller signal listener', async () => {
  // send 抛错发生在建立阶段的检查之前，若清理只包住建立阶段，监听器就会漏掉一次
  // 摘除；长期存活的客户端因此为每条失败的流留一份引用。
  //
  // A throwing send happens before the setup checks, so a cleanup that only wraps
  // setup leaks one listener; a long-lived client then keeps a reference per failed
  // stream.
  let attached = 0;
  let removed = 0;
  const controller = new AbortController();
  const originalAdd = controller.signal.addEventListener.bind(
    controller.signal,
  );
  const originalRemove = controller.signal.removeEventListener.bind(
    controller.signal,
  );
  controller.signal.addEventListener = (...args) => {
    attached += 1;
    return originalAdd(...args);
  };
  controller.signal.removeEventListener = (...args) => {
    removed += 1;
    return originalRemove(...args);
  };

  const fetch = fakeFetch(() => Promise.reject(new TypeError('network down')));
  const client = createOvClient({
    baseURL: 'https://ov.test',
    fetch,
    signal: controller.signal,
  });

  await assert.rejects(client.stream({ method: 'POST', path: '/x' }));
  assert.equal(attached, 1);
  assert.equal(removed, 1);
});

test('abort interrupts a read pending on a stream that never closes', async () => {
  // 挂在一个不再产生数据的流上时，生成器的 `return()` 排在 pending 读取之后永远
  // 轮不到，底层连接的 `cancel()` 也就不会被调用。
  //
  // Stuck on a stream that stops producing data, the generator's `return()` queues
  // behind the pending read and never runs, so the connection's `cancel()` is never
  // called.
  let cancelled = false;
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode('data: one\r\r'));
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
  const iterator = stream[Symbol.asyncIterator]();

  assert.deepEqual((await iterator.next()).value, { data: 'one' });
  const pending = iterator.next();
  await new Promise((resolve) => setTimeout(resolve, 10));
  stream.abort();

  const settled = await Promise.race([
    pending,
    new Promise((resolve) => setTimeout(() => resolve('timeout'), 500)),
  ]);
  // 迭代结束的返回值可能带 `value: undefined`，只断言它已经 done。
  // The end result may carry `value: undefined`; assert only that it is done.
  assert.equal(settled.done, true);
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(cancelled, true);
});

test('one stream resolves the identity provider once', async () => {
  // 流式请求先解析一次用于接上 signal，再交给 send 发送；不复用就会把函数形式的
  // 凭证求值两遍，第二次可能直接失败。
  //
  // A streaming request resolves once to link the signal and then hands it to send;
  // without reuse a function-valued credential is evaluated twice and the second
  // evaluation can fail outright.
  let calls = 0;
  const fetch = fakeFetch(() => eventStream(['data: x\n\n']));
  const client = createOvClient({
    baseURL: 'https://ov.test',
    apiKey: () => {
      calls += 1;
      return `key-${calls}`;
    },
    fetch,
  });

  const stream = await client.stream({ method: 'POST', path: '/x' });
  await collect(stream);
  assert.equal(calls, 1);
});
