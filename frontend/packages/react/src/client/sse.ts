import type { OvStreamEvent } from './types.js';

const EVENT_BOUNDARY = '\n\n';

/**
 * 按行解析一个事件块。字段名按规范只认 `data` / `event` / `id` / `retry`，
 * 注释行（以 `:` 开头）忽略；`data` 多行用换行拼接，`retry` 只接受整数。
 *
 * Parses one event block line by line. Only `data` / `event` / `id` / `retry` are
 * recognised, comment lines (starting with `:`) are ignored, multi-line `data`
 * joins with newlines, and `retry` accepts integers only.
 */
function parseEvent(block: string): OvStreamEvent | undefined {
  let data: string | undefined;
  let event: string | undefined;
  let id: string | undefined;
  let retry: number | undefined;

  for (const rawLine of block.split('\n')) {
    const line = rawLine.replace(/\r$/, '');
    if (!line || line.startsWith(':')) continue;

    const colon = line.indexOf(':');
    const field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);

    if (field === 'data') {
      data = data === undefined ? value : `${data}\n${value}`;
    } else if (field === 'event') {
      event = value;
    } else if (field === 'id') {
      id = value;
    } else if (field === 'retry') {
      const parsed = Number.parseInt(value, 10);
      if (Number.isFinite(parsed)) retry = parsed;
    }
  }

  // 只有注释的心跳块没有任何字段，按无事件处理。
  if (data === undefined && event === undefined && id === undefined) {
    return undefined;
  }

  const result: OvStreamEvent = { data: data ?? '' };
  if (event !== undefined) result.event = event;
  if (id !== undefined) result.id = id;
  if (retry !== undefined) result.retry = retry;
  return result;
}

/** 把字节流解成文本块；没有 `TextDecoderStream` 时退回手写解码器。 */
function createDecoder(): TransformStream<Uint8Array, string> {
  if (typeof TextDecoderStream !== 'undefined') {
    return new TextDecoderStream() as unknown as TransformStream<
      Uint8Array,
      string
    >;
  }

  const decoder = new TextDecoder('utf-8');
  return new TransformStream<Uint8Array, string>({
    transform(bytes, controller) {
      controller.enqueue(decoder.decode(bytes, { stream: true }));
    },
    flush(controller) {
      const rest = decoder.decode();
      if (rest) controller.enqueue(rest);
    },
  });
}

/**
 * 把事件流字节解成事件。分帧在这里做：事件以空行结束，块内按行取字段。
 * 流结束时残留的块仍会派发——规范要求丢弃，但服务端漏掉末尾空行时那样会
 * 静默吞掉最后一个事件，代价更大；残缺的 JSON 由调用方解析失败后忽略。
 *
 * Decodes event-stream bytes into events. Framing happens here: an event ends at
 * a blank line and fields are read line by line inside a block. A trailing block
 * is still dispatched on end — the specification says to drop it, but a server
 * that omits the final blank line would then lose its last event silently, which
 * costs more; truncated JSON fails to parse and is ignored by the caller.
 */
export function decodeEventStream(
  body: ReadableStream<Uint8Array>,
): ReadableStream<OvStreamEvent> {
  let pending = '';

  return body.pipeThrough(createDecoder()).pipeThrough(
    new TransformStream<string, OvStreamEvent>({
      transform(chunk, controller) {
        pending += chunk;
        const blocks = pending.split(EVENT_BOUNDARY);
        pending = blocks.pop() ?? '';
        for (const block of blocks) {
          const parsed = parseEvent(block);
          if (parsed) controller.enqueue(parsed);
        }
      },
      flush(controller) {
        const parsed = parseEvent(pending);
        if (parsed) controller.enqueue(parsed);
      },
    }),
  );
}
