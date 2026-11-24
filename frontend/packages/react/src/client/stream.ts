import {
  observation,
  type ObservedSpec,
} from '../shared/monitoring/request-observer.js';
import { OvClientError } from './errors.js';
import {
  isAbortError,
  parseJson,
  readText,
  throwIfErrorEnvelope,
  toClientError,
} from './response.js';
import { decodeEventStream } from './sse.js';
import type {
  OvClientOptions,
  OvRequestOptions,
  OvResolvedOptions,
  OvStream,
  OvStreamEvent,
  OvStreamSpec,
} from './types.js';

/** 流式响应要求的 content-type。The content type a streamed response must carry. */
const EVENT_STREAM = 'text/event-stream';

/**
 * `stream()` 需要的两件外部能力：发一次请求，以及解析本次请求生效的配置。
 * 抽成参数而不是复制实现，是为了让流式逻辑单独成文件、守住单文件行数上限。
 *
 * The two capabilities `stream()` needs: send one request, and resolve the
 * configuration in effect for it. They are passed in rather than duplicated so the
 * streaming logic can live in its own file and stay within the per-file line limit.
 */
export interface StreamDeps {
  send: (
    spec: OvStreamSpec,
    headers?: Record<string, string>,
    signal?: AbortSignal,
    resolved?: OvResolvedOptions,
  ) => Promise<{ response: Response; effective: OvResolvedOptions }>;
  resolveFor: (
    defaults: OvClientOptions,
    override: OvRequestOptions | undefined,
  ) => Promise<OvResolvedOptions>;
  defaults: OvClientOptions;
}

/**
 * 建立一条事件流：自己的控制器与调用方取消联动，建立阶段的失败按普通请求上报，
 * 迭代结束（正常结束、提前 `break` 或出错）都释放底层流。
 *
 * Opens an event stream: its own controller is linked to caller cancellation, setup
 * failures are reported like an ordinary request, and every way iteration ends —
 * completion, an early `break` or an error — releases the underlying stream.
 */
export async function openStream(
  spec: OvStreamSpec,
  { send, resolveFor, defaults }: StreamDeps,
): Promise<OvStream> {
  // 自己的控制器与调用方的 signal 联动：`abort()` 与调用方取消都能停掉连接，
  // 任一方触发即中止。
  //
  // Its own controller is linked to the caller's signal, so `abort()` and caller
  // cancellation both stop the connection.
  const controller = new AbortController();
  // 取消时唤醒正在等待的读取：底层读取不一定随 signal 结束，靠它自己返回可能
  // 永远等下去。
  //
  // Cancellation wakes a waiting read: the underlying read does not necessarily end
  // with the signal, and waiting for it to return could hang forever.
  let releaseAbort: (() => void) | undefined;
  const aborted = new Promise<'aborted'>((resolve) => {
    releaseAbort = () => resolve('aborted');
  });

  const stop = () => {
    releaseAbort?.();
    controller.abort();
  };

  // 客户端级 signal 与本次请求的覆盖都要接上，并沿用「请求覆盖优先」的常规优先
  // 级：此前只读 `spec.options.signal`，于是 `createOvClient({ signal })` 取消后
  // 传给 fetch 的 signal 仍是未中止的那个。
  //
  // Both the client-level signal and this request's override are honoured, in the
  // usual "request overrides default" precedence: reading only `spec.options.signal`
  // meant aborting `createOvClient({ signal })` left the signal handed to fetch
  // un-aborted.
  const observer = observation(spec as ObservedSpec);
  const resolved = await resolveFor(defaults, spec.options);
  const callerSignals = [resolved.signal, spec.options?.signal].filter(
    (candidate): candidate is AbortSignal => Boolean(candidate),
  );
  if (observer) observer.callerSignals = callerSignals;
  // 调用方取消与 `abort()` 走**同一条**释放路径：只中止传输层不够，响应体的
  // `cancel()` 也得调用，否则在两次 `next()` 之间（迭代空闲时）取消会留下一个仍
  // 挂着的连接。`release` 在下面定义，因此这里通过一个可替换的引用转发。
  //
  // Caller cancellation goes through the **same** release path as `abort()`: aborting
  // the transport alone is not enough, the response body's `cancel()` has to run too,
  // or a cancellation between two `next()` calls (while iteration is idle) leaves a
  // connection hanging. `release` is defined below, so it is forwarded through a
  // replaceable reference.
  let onCallerAbort: () => void = () => stop();
  const forward = () => onCallerAbort();
  for (const callerSignal of callerSignals) {
    if (callerSignal.aborted) {
      forward();
      break;
    }
    callerSignal.addEventListener('abort', forward, { once: true });
  }

  // 事件流用完之后要摘掉监听，否则一个长期存活的客户端会为每条流留下一份引用。
  // Detach once the stream is done, or a long-lived client keeps a reference per
  // stream it ever opened.
  const detach = () => {
    for (const callerSignal of callerSignals) {
      callerSignal.removeEventListener('abort', forward);
    }
  };

  // 发送本身也要在清理守卫里：网络层拒绝时 `send` 会抛出，那一步早于下面的建立
  // 阶段检查，监听器就会漏掉一次摘除。
  //
  // Sending belongs inside the cleanup guard too: a transport rejection makes `send`
  // throw, which happens before the setup checks below, and the listeners would leak.
  let response: Response;
  try {
    ({ response } = await send(
      spec,
      { accept: EVENT_STREAM, ...spec.headers },
      controller.signal,
      resolved,
    ));
  } catch (error) {
    detach();
    throw error;
  }

  // 建立阶段失败（HTTP 错误、内容类型不对）时也要摘监听：这条流不会再有消费者，
  // 留下的监听只会指向一个已经抛出的调用。
  //
  // Setup failures — an HTTP error or an unexpected content type — detach too: this
  // stream will have no consumer, and a leftover listener would only point at a call
  // that already threw.
  let body: ReadableStream<Uint8Array>;
  try {
    if (observer) observer.phase = 'business-envelope';
    if (!response.ok) {
      const raw = await readText(response, controller.signal);
      observer?.capture(raw);
      throw toClientError(response, parseJson(raw) ?? raw);
    }

    const contentType = response.headers.get('content-type') ?? '';
    if (!contentType.includes(EVENT_STREAM) || !response.body) {
      // 服务端可能用 JSON 外壳报告失败，例如代理层在建立流之前就拒绝了请求。
      const raw = await readText(response, controller.signal);
      observer?.capture(raw);
      const envelope = parseJson(raw);
      if (envelope !== undefined)
        throwIfErrorEnvelope(response, envelope, observer?.business);
      throw new OvClientError({
        code: 'UNEXPECTED_CONTENT_TYPE',
        message: `Expected ${EVENT_STREAM}, got ${contentType || 'no content type'}`,
        statusCode: response.status,
        requestId: response.headers.get('x-request-id') ?? undefined,
        responseBody: envelope,
      });
    }
    body = response.body;
  } catch (error) {
    detach();
    throw error;
  }

  // 取消的是解码后这条流的**可读端**：`body` 已经被 `pipeThrough` 锁住，直接
  // `body.cancel()` 只会抛「流已锁定」，而取消可读端会沿管道向上传播到响应体。
  //
  // What gets cancelled is the readable end of the decoded stream: `body` is already
  // locked by `pipeThrough`, so calling `body.cancel()` only throws "stream is
  // locked", while cancelling the readable end propagates up the pipe to the
  // response body.
  // 用 reader 而不是 `[Symbol.asyncIterator]()`：只有 reader 带 `cancel()`，而
  // 取消一个已被异步迭代器锁定的流会直接抛「已锁定」。`reader.cancel()` 会立即
  // 结束 pending 的 `read()` 并沿管道把取消传回响应体。
  //
  // A reader rather than `[Symbol.asyncIterator]()`: only a reader has `cancel()`,
  // and cancelling a stream already locked by an async iterator throws "locked".
  // `reader.cancel()` ends a pending `read()` immediately and passes the
  // cancellation back to the response body through the pipe.
  const reader = decodeEventStream(
    body,
    observer ? (size) => observer.bytes(size) : undefined,
  ).getReader();

  // 释放要**取消流**，而不是调用包装层生成器的 `return()`。
  //
  // 生成器的 `return()` 只是给解码器排一次关闭，它排在尚未完成的读取之后；当
  // `reader.next()` 正卡在一个不再产生数据的流上时，那个 `return()` 永远轮不到，
  // 底层连接的 `cancel()` 也就不会被调用——连接一直挂着。取消可读端会立即结束
  // pending 的读取，并把取消传回响应体。
  //
  // Release by **cancelling the stream**, not by calling `return()` on the wrapper
  // generator.
  //
  // The generator's `return()` only queues a close on the decoder, behind the read
  // still in flight; when `reader.next()` is stuck on a stream that stops producing
  // data, that `return()` never gets its turn and the underlying connection's
  // `cancel()` is never called — the connection stays open. Cancelling the readable
  // end ends the pending read immediately and passes the cancellation back to the
  // response body.
  let released = false;
  const release = () => {
    if (released) return;
    released = true;
    detach();
    void reader.cancel().catch(() => {});
  };
  // 从这里开始，调用方取消同时中止传输并释放响应体。
  // From here on, caller cancellation stops the transport and releases the body.
  onCallerAbort = () => {
    stop();
    release();
  };
  // 取消可能发生在建立阶段就已完成（signal 早已中止）：那时 `release` 还不存在，
  // 上面的循环只做了 `stop()`，这里补一次释放。
  //
  // The cancellation may have happened before setup finished (the signal was already
  // aborted): the loop above only called `stop()` then, so the release is applied here.
  if (controller.signal.aborted) release();

  // 无论正常结束、提前 `break` 还是出错，都要释放底层流并摘掉监听：只结束包装
  // 层的生成器不会取消响应体，长连接的 SSE 因此会在消费者退出后继续占着连接。
  //
  // Release the underlying stream and detach the listeners on normal completion, an
  // early `break` and an error alike: closing only the wrapper generator does not
  // cancel the response body, so a long-lived SSE connection keeps running after its
  // consumer exits.
  async function* iterate(): AsyncGenerator<OvStreamEvent> {
    try {
      for (;;) {
        const next = await Promise.race([reader.read(), aborted]);
        if (next === 'aborted') return;
        if (next.done) return;
        yield next.value;
      }
    } catch (error) {
      // 取消是调用方的意图，不是失败：迭代安静结束，调用点不必再吞一次
      // AbortError。连接已断时的读取错误也一并忽略。
      //
      // Cancellation is the caller's intent rather than a failure, so iteration ends
      // quietly and the call site need not swallow another AbortError; a read error
      // after the connection dropped is ignored the same way.
      if (isAbortError(error)) return;
      throw error;
    } finally {
      release();
    }
  }

  return {
    abort: () => {
      stop();
      release();
    },
    [Symbol.asyncIterator]: iterate,
  };
}
