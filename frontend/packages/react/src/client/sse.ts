import type { OvStreamEvent } from './types.js';

/**
 * 按行解析一个事件块。字段名按规范只认 `data` / `event` / `id` / `retry`，
 * 注释行（以 `:` 开头）忽略；`data` 多行用换行拼接，`retry` 只接受整数。
 *
 * Parses one event block line by line. Only `data` / `event` / `id` / `retry` are
 * recognised, comment lines (starting with `:`) are ignored, multi-line `data`
 * joins with newlines, and `retry` accepts integers only.
 */
function parseEvent(lines: readonly string[]): OvStreamEvent | undefined {
  let data: string | undefined;
  let event: string | undefined;
  let id: string | undefined;
  let retry: number | undefined;

  for (const line of lines) {
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
function createDecoder(
  observeBytes?: (size: number) => void,
): TransformStream<Uint8Array, string> {
  if (!observeBytes && typeof TextDecoderStream !== 'undefined') {
    return new TextDecoderStream() as unknown as TransformStream<
      Uint8Array,
      string
    >;
  }

  const decoder = new TextDecoder('utf-8');
  return new TransformStream<Uint8Array, string>({
    transform(bytes, controller) {
      observeBytes?.(bytes.byteLength);
      controller.enqueue(decoder.decode(bytes, { stream: true }));
    },
    flush(controller) {
      const rest = decoder.decode();
      if (rest) controller.enqueue(rest);
    },
  });
}

/**
 * 把事件流字节解成事件。
 *
 * 分帧按规范逐字符进行：`\r`、`\n` 与 `\r\n` 都是行终止符，行终止符之后紧跟的
 * `\n` 属于同一个 CRLF，跳过即可。一个空行结束事件块，块内按行取字段。
 *
 * 关键在于 `\r` 必须**立即**结束当前行，不能留在缓冲区等下一个 chunk：服务端
 * 用纯 CR 换行时，`data: one\r\r` 本身就已经是完整事件，等更多字节会让一个已经
 * 到达的事件永远发不出去（流保持打开时就是挂死）。
 *
 * 早先的实现在块之间找「两个终止符」的连续片段，那样必须等第二个 `\r` 之后确认
 * 没有跟 `\n` 才能分帧，因此才会出现上述挂起。
 *
 * 流结束时残留的一行与块仍会派发——规范要求丢弃，但服务端漏掉末尾空行时那样会
 * 静默吞掉最后一个事件，代价更大；残缺的 JSON 由调用方解析失败后忽略。
 *
 * Decodes event-stream bytes into events.
 *
 * Framing follows the specification character by character: `\r`, `\n` and `\r\n`
 * all terminate a line, and a `\n` immediately after a `\r` belongs to the same
 * CRLF, so it is skipped. A blank line ends an event block, and fields are read
 * line by line inside it.
 *
 * The point is that `\r` must end the current line **immediately** rather than
 * being held until the next chunk: with CR-only line endings, `data: one\r\r` is
 * already a complete event, and waiting for more bytes means an event that has
 * arrived never dispatches — which hangs while the stream stays open.
 *
 * An earlier implementation searched for a run of two terminators between blocks,
 * which required seeing past the second `\r` to know no `\n` followed, and that is
 * exactly what caused the stall.
 *
 * A trailing line and block are still dispatched on end — the specification says to
 * drop them, but a server that omits the final blank line would then lose its last
 * event silently, which costs more; truncated JSON fails to parse and is ignored by
 * the caller.
 */
export function decodeEventStream(
  body: ReadableStream<Uint8Array>,
  observeBytes?: (size: number) => void,
): ReadableStream<OvStreamEvent> {
  // 当前行、当前块与「上一个字符是 CR」的状态都跨 chunk 累积。
  // The current line, the current block and "the previous character was CR" all
  // accumulate across chunks.
  let line = '';
  let block: string[] = [];
  let skipLf = false;

  return body.pipeThrough(createDecoder(observeBytes)).pipeThrough(
    new TransformStream<string, OvStreamEvent>({
      transform(chunk, controller) {
        const finishLine = () => {
          if (line === '') {
            const parsed = parseEvent(block);
            block = [];
            if (parsed) controller.enqueue(parsed);
            return;
          }
          block.push(line);
          line = '';
        };

        for (const char of chunk) {
          if (char === '\r') {
            finishLine();
            skipLf = true;
          } else if (char === '\n') {
            // CRLF 里的 `\n` 是同一个终止符的后半段，不能算第二个终止符。
            // The `\n` of a CRLF is the second half of one terminator, not a second one.
            if (skipLf) skipLf = false;
            else finishLine();
          } else {
            skipLf = false;
            line += char;
          }
        }
      },
      flush(controller) {
        if (line !== '') block.push(line);
        line = '';
        const parsed = parseEvent(block);
        block = [];
        if (parsed) controller.enqueue(parsed);
      },
    }),
  );
}
