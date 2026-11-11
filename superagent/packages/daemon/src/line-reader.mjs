// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Splits a child's stdout into lines without letting one line grow without
// bound. The cap only guards daemon memory: a line over it is either one the
// adapter can do without (it is skipped up to its newline and the stream goes
// on) or a fatal error, as before.
//
// Runtimes echo whole tool results on stdout, inline base64 images included,
// and sometimes repeat every message of a run on one line. Those lines are
// telemetry for the daemon, not the model's input: the model already has the
// result inside the child. So the adapter may name, from the start of the
// line, which of them it can afford to lose.

export const OVERSIZED_PREFIX_CHARS = 512;

// The top-level `type` of a JSON line, read from its first characters. Only a
// line that starts with it counts: a `type` found further in may belong to a
// nested object, and guessing wrong would drop a line the adapter needs.
export function lineTypeFromPrefix(prefix) {
  const match = /^\s*\{\s*"type"\s*:\s*"([A-Za-z0-9_.:-]{1,64})"/.exec(String(prefix ?? ''));
  return match ? match[1] : '';
}

// Reads a JSON string field that appears in the prefix, for adapters that
// want an id or a name from a skipped line. Returns '' when it is not there.
export function stringFieldFromPrefix(prefix, field) {
  const pattern = new RegExp(`"${field}"\\s*:\\s*("(?:[^"\\\\]|\\\\.){0,512}")`);
  const match = pattern.exec(String(prefix ?? ''));
  if (!match) return '';
  try { return String(JSON.parse(match[1])); } catch { return ''; }
}

export class BoundedLineReader {
  // onLine(line) gets every complete line within the limit. onOversized(prefix)
  // is asked once per line that goes over it, with the line's first characters;
  // it returns true to skip the line, false to fail. A failure throws from
  // push() with `${label} exceeded local limit`.
  constructor({ maxLineBytes, label, onLine, onOversized = () => false }) {
    this.maxLineBytes = maxLineBytes;
    this.label = label;
    this.onLine = onLine;
    this.onOversized = onOversized;
    this.reset();
  }

  reset() {
    this.buffer = '';
    this.bufferBytes = 0;
    this.skipping = false;
  }

  push(chunk) {
    let rest = String(chunk ?? '');
    while (rest.length > 0) {
      const newline = rest.indexOf('\n');
      const segment = newline < 0 ? rest : rest.slice(0, newline);
      rest = newline < 0 ? '' : rest.slice(newline + 1);

      if (this.skipping) {
        if (newline >= 0) this.skipping = false;
        continue;
      }

      const segmentBytes = Buffer.byteLength(segment, 'utf8');
      if (this.bufferBytes + segmentBytes > this.maxLineBytes) {
        const prefix = (this.buffer + segment.slice(0, OVERSIZED_PREFIX_CHARS)).slice(0, OVERSIZED_PREFIX_CHARS);
        this.buffer = '';
        this.bufferBytes = 0;
        if (this.onOversized(prefix) !== true) {
          this.skipping = newline < 0;
          throw new Error(`${this.label} exceeded local limit`);
        }
        this.skipping = newline < 0;
        continue;
      }

      if (newline < 0) {
        this.buffer += segment;
        this.bufferBytes += segmentBytes;
        continue;
      }
      const line = this.buffer + segment;
      this.buffer = '';
      this.bufferBytes = 0;
      if (line.trim()) this.onLine(line);
    }
  }
}
