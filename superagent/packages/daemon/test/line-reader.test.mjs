// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { BoundedLineReader, lineTypeFromPrefix, stringFieldFromPrefix } from '../src/line-reader.mjs';

function reader({ maxLineBytes = 64, skip = () => false } = {}) {
  const lines = [];
  const oversized = [];
  const instance = new BoundedLineReader({
    maxLineBytes,
    label: 'test stdout line',
    onLine: (line) => lines.push(line),
    onOversized: (prefix) => { oversized.push(prefix); return skip(prefix); },
  });
  return { instance, lines, oversized };
}

describe('BoundedLineReader', () => {
  it('reassembles lines split across chunks and counts bytes, not characters', () => {
    const { instance, lines } = reader({ maxLineBytes: 12 });
    instance.push('{"a"');
    instance.push(':1}\n\n{"b":2}\n{"c"');
    instance.push(':3}\n');
    assert.deepEqual(lines, ['{"a":1}', '{"b":2}', '{"c":3}']);

    // Four CJK characters are 12 UTF-8 bytes: at the limit, one more is over it.
    instance.push('图片图片\n');
    assert.equal(lines.at(-1), '图片图片');
    assert.throws(() => instance.push('图片图片图\n'), /test stdout line exceeded local limit/);
  });

  it('skips an oversized line the adapter can lose, up to its newline, and keeps reading', () => {
    const { instance, lines, oversized } = reader({ skip: (prefix) => lineTypeFromPrefix(prefix) === 'agent_end' });
    const big = `{"type":"agent_end","messages":[{"data":"${'x'.repeat(200)}"}]}`;
    instance.push('{"type":"tool_execution_start"}\n');
    // The oversized line arrives in pieces, and the next event shares its last chunk.
    instance.push(big.slice(0, 50));
    instance.push(big.slice(50, 150));
    instance.push(`${big.slice(150)}\n{"type":"agent_settled"}\n`);

    assert.deepEqual(lines, ['{"type":"tool_execution_start"}', '{"type":"agent_settled"}']);
    assert.equal(oversized.length, 1);
    assert.equal(lineTypeFromPrefix(oversized[0]), 'agent_end');
  });

  it('fails on an oversized line the adapter needs, after delivering the lines before it', () => {
    const { instance, lines } = reader({ skip: (prefix) => lineTypeFromPrefix(prefix) === 'agent_end' });
    assert.throws(
      () => instance.push(`{"type":"ready"}\n{"type":"response","data":"${'x'.repeat(100)}"}\n`),
      /test stdout line exceeded local limit/,
    );
    assert.deepEqual(lines, ['{"type":"ready"}']);
  });

  it('reads the type only from the start of the line and string fields from the prefix', () => {
    assert.equal(lineTypeFromPrefix('{"type":"tool_execution_end","toolCallId":"c1"'), 'tool_execution_end');
    // A nested type must not pass for the line's own.
    assert.equal(lineTypeFromPrefix('{"message":{"type":"agent_end"}'), '');
    assert.equal(stringFieldFromPrefix('{"type":"x","toolName":"get_\\"media\\"","result":', 'toolName'), 'get_"media"');
    assert.equal(stringFieldFromPrefix('{"type":"x","toolName":"cut off', 'toolName'), '');
  });
});
