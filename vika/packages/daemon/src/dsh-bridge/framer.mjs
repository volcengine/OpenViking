// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// A frame is one line ending in LF, exactly as the stock JSON-RPC transport
// frames it (`drainLines` in @deepseek-ai/dsh-sdk-protocol). `readline` is not
// usable here: it also splits on U+2028/U+2029, which JSON strings may contain
// unescaped, so one request would arrive as two and be silently lost.
const LF = 0x0a;
const CR = 0x0d;
const EMPTY = Buffer.alloc(0);

// The same bound the daemon's own DSH transport applies to a line it reads
// (MAX_LINE_BYTES in dsh/transport.mjs): a frame this bridge refuses but the
// daemon would accept would be a request dropped for no shared reason, so the
// two numbers are one contract. A longer line is dropped up to its newline so
// one bad peer cannot grow this process without bound.
export const MAX_FRAME_BYTES = 16_000_000;
const OVERSIZED_PREFIX_CHARS = 512;

// LF-only frame reader over raw bytes. Complete lines are decoded as UTF-8
// (a multi-byte character split across chunks is never decoded piecewise), a
// trailing CR is treated as the CRLF terminator, U+2028/U+2029 stay content,
// and an over-long line is dropped with `onOversized(prefix)` before its
// newline so the stream keeps working.
export class DshBridgeFramer {
  constructor({ onFrame, onOversized = () => {}, maxFrameBytes = MAX_FRAME_BYTES } = {}) {
    this.onFrame = onFrame;
    this.onOversized = onOversized;
    this.maxFrameBytes = maxFrameBytes;
    this.pending = EMPTY;
    this.pendingBytes = 0;
    this.skipping = false;
    this.closed = false;
  }

  push(chunk) {
    let rest = Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk ?? ''), 'utf8');
    while (rest.length > 0 && !this.closed) {
      if (this.skipping) {
        const newline = rest.indexOf(LF);
        if (newline < 0) return;
        this.skipping = false;
        rest = rest.subarray(newline + 1);
        continue;
      }
      const newline = rest.indexOf(LF);
      const segment = newline < 0 ? rest : rest.subarray(0, newline);
      rest = newline < 0 ? EMPTY : rest.subarray(newline + 1);
      if (this.pendingBytes + segment.length > this.maxFrameBytes) {
        const prefix = Buffer.concat([this.pending, segment.subarray(0, OVERSIZED_PREFIX_CHARS)])
          .subarray(0, OVERSIZED_PREFIX_CHARS).toString('utf8');
        this.pending = EMPTY;
        this.pendingBytes = 0;
        this.skipping = newline < 0;
        this.onOversized(prefix);
        continue;
      }
      if (newline < 0) {
        this.pending = this.pending.length === 0 ? Buffer.from(segment) : Buffer.concat([this.pending, segment]);
        this.pendingBytes += segment.length;
        continue;
      }
      const line = this.pending.length === 0 ? segment : Buffer.concat([this.pending, segment]);
      this.pending = EMPTY;
      this.pendingBytes = 0;
      this.deliver(line);
    }
  }

  // Input EOF completes a last frame the peer sent without a trailing LF, like
  // the stock transport's `decoder.end()` flush.
  end() {
    if (this.closed) return;
    this.closed = true;
    const tail = this.pending;
    const skipping = this.skipping;
    this.pending = EMPTY;
    this.pendingBytes = 0;
    this.skipping = false;
    if (!skipping && tail.length > 0) this.deliver(tail);
  }

  deliver(line) {
    const end = line.length > 0 && line[line.length - 1] === CR ? line.length - 1 : line.length;
    const text = line.toString('utf8', 0, end);
    // The stock transport skips blank lines; a frame with no text is not one.
    if (!text.trim()) return;
    this.onFrame({ text, line: line.subarray(0, end) });
  }
}
