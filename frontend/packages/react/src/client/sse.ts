import type { OvStreamEvent } from './types.js';

/**
 * 事件块的分隔：空行，也就是连续两个行终止符。行终止符可以是 `\r\n`、`\n` 或
 * `\r`，两两组合都合法（`\r\n\r\n`、`\r\n\n`、`\r\r\n`、`\r\r`……），因此这里
 * 按「两个终止符」匹配，而不是只找 `\n\n`——后者会把整个 CRLF 流当成一个事件。
 *
 * `\r` 必须排除后面紧跟 `\n` 的情况：否则 `\r\n` 会被回溯成 `\r` + `\n` 两个
 * 终止符，一个普通的行尾就被误判成空行，事件从中间被切开。
 *
 * `\r` excludes a following `\n`: otherwise `\r\n` backtracks into `\r` + `\n`,
 * counting one ordinary line ending as two terminators and splitting an event in
 * half.
 *
 * 只在**已完整到达**的文本里找分隔：末尾若是孤立的 `\r`，它可能是下一块的
 * `\r\n` 的前半段，先留在缓冲区等下一个 chunk，否则会把一个事件从中间切断。
 *
 * The boundary between event blocks is a blank line, meaning two consecutive line
 * terminators. A terminator may be `\r\n`, `\n` or `\r`, and every pairing is
 * valid (`\r\n\r\n`, `\r\n\n`, `\r\r\n`, `\r\r` …), so this matches "two
 * terminators" rather than looking for `\n\n` alone, which treats a whole CRLF
 * stream as one event.
 *
 * Only text that has fully arrived is searched: a trailing lone `\r` may be the
 * first half of a `\r\n` belonging to the next block, so it stays buffered until
 * the next chunk — otherwise an event would be cut in half.
 */
const BOUNDARY = /(?:\r\n|\r(?!\n)|\n){2}/;

/** 末尾孤立 `\r` 的位置：它还不能判定是不是分隔的一部分。 */
/** Position of a trailing lone `\r`, which cannot yet be judged part of a boundary. */
function trailingCarriageReturn(text: string): number {
  return text.endsWith('\r') ? text.length - 1 : -1;
}

/**
 * 按行解析一个事件块。字段名按规范只认 `data` / `event` / `id` / `retry`，
 * 注释行（以 `:` 开头）忽略；`data` 多行用换行拼接，`retry` 只接受整数。
 *
 * 块内换行可能是 `\r\n`、`\n` 或 `\r`，这里统一按三种切行，而不是只去掉行尾
 * 的 `\r`——单独 `\r` 换行的流此前会被当成一整行。
 *
 * Parses one event block line by line. Only `data` / `event` / `id` / `retry` are
 * recognised, comment lines (starting with `:`) are ignored, multi-line `data`
 * joins with newlines, and `retry` accepts integers only.
 *
 * Lines inside a block may end with `\r\n`, `\n` or `\r`; all three split here
 * rather than only stripping a trailing `\r`, which treated a `\r`-only stream as
 * one long line.
 */
function parseEvent(block: string): OvStreamEvent | undefined {
  let data: string | undefined;
  let event: string | undefined;
  let id: string | undefined;
  let retry: number | undefined;

  for (const line of block.split(/\r\n|\r|\n/)) {
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
        // 末尾孤立的 `\r` 先留在缓冲区：它可能是下一个 `\r\n` 的前半段。
        const holdFrom = trailingCarriageReturn(pending);
        const searchable = holdFrom < 0 ? pending : pending.slice(0, holdFrom);
        const blocks: string[] = [];
        let rest = searchable;
        for (;;) {
          const match = BOUNDARY.exec(rest);
          if (!match) break;
          blocks.push(rest.slice(0, match.index));
          rest = rest.slice(match.index + match[0].length);
        }
        pending = rest + (holdFrom < 0 ? '' : '\r');
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
