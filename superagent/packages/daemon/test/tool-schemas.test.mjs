// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { TOOLS, validateInput } from '../src/actions/tools.mjs';

// Go's regexp limit: agy compiles every advertised pattern with it and refuses
// the whole toolbox, failing every turn, if one does not compile.
const RE2_MAX_REPEAT = 1000;

function schemaPatterns(schema, at, found = []) {
  if (!schema || typeof schema !== 'object') return found;
  if (typeof schema.pattern === 'string') found.push({ at, pattern: schema.pattern });
  for (const [name, property] of Object.entries(schema.properties ?? {})) schemaPatterns(property, `${at}.${name}`, found);
  if (schema.items) schemaPatterns(schema.items, `${at}[]`, found);
  for (const key of ['anyOf', 'oneOf', 'allOf']) {
    (schema[key] ?? []).forEach((branch, index) => schemaPatterns(branch, `${at}.${key}[${index}]`, found));
  }
  return found;
}

function re2Problem(pattern) {
  if (/\\u/.test(pattern)) return 'a \\u escape';
  if (/\(\?<?[=!]/.test(pattern)) return 'lookaround';
  if (/\\[1-9]/.test(pattern)) return 'a backreference';
  for (const [, low, high] of pattern.matchAll(/\{(\d+)(?:,(\d*))?\}/g)) {
    if (Math.max(Number(low), Number(high || 0)) > RE2_MAX_REPEAT) return `a repeat count above ${RE2_MAX_REPEAT}`;
  }
  return null;
}

const tool = (name) => TOOLS.find((entry) => entry.name === name);

describe('Core tool schemas', () => {
  it('advertise only patterns that compile as Go RE2', () => {
    const patterns = TOOLS.flatMap((entry) => schemaPatterns(entry.inputSchema, entry.name));
    assert.ok(patterns.length > 20, 'the walk reaches the tool schemas');
    const broken = patterns.filter(({ pattern }) => re2Problem(pattern))
      .map(({ at, pattern }) => `${at}: ${pattern} has ${re2Problem(pattern)}`);
    assert.deepEqual(broken, []);
  });

  it('keep the length bounds and refused characters of an input reference', () => {
    const reference = tool('send_message').inputSchema.properties.decision_id;
    validateInput(reference, 'e'.repeat(4096));
    assert.throws(() => validateInput(reference, 'e'.repeat(4097)), /too long/);
    assert.throws(() => validateInput(reference, ''), /invalid format/);
    for (const refused of ['a b', 'a[b', 'a]b', 'a\u0000b', 'a\u001fb', 'a\u007fb']) {
      assert.throws(() => validateInput(reference, refused), /invalid format/);
    }
  });

  it('keep the length bound of a viking uri, and let list take the bare root', () => {
    const read = tool('openviking_read').inputSchema.properties.uri;
    validateInput(read, `viking://${'a'.repeat(2000)}`);
    assert.throws(() => validateInput(read, `viking://${'a'.repeat(2001)}`), /too long/);
    assert.throws(() => validateInput(read, 'viking://'), /invalid format/);
    const list = tool('openviking_list').inputSchema.properties.uri;
    validateInput(list, 'viking://');
    assert.throws(() => validateInput(list, `viking://${'a'.repeat(2001)}`), /too long/);
  });
});
