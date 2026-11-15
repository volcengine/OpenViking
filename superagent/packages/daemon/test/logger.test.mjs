// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { it } from 'node:test';
import { createLogger } from '../src/logger.mjs';

function captured(t, write) {
  const lines = [];
  const original = process.stdout.write;
  process.stdout.write = (chunk) => { lines.push(String(chunk)); return true; };
  try { write(); } finally { process.stdout.write = original; }
  return lines.map((line) => JSON.parse(line));
}

it('redacts secrets by value in the message and in every field, top-level included', (t) => {
  const [record] = captured(t, () => createLogger('info', { appSecret: 'base-secret-value' }).info(
    'calling with sk-abcdefghijklmnop1234 and ovgm_abcdefghijklmnop',
    { apiKey: 'plain-value', token: 'x', note: 'key oc_sk_abcdefghijklmnop here', nested: { secret: 'y', ok: 'fine' } },
  ));
  const text = JSON.stringify(record);
  for (const leaked of ['sk-abcdefghijklmnop1234', 'ovgm_abcdefghijklmnop', 'plain-value', 'oc_sk_abcdefghijklmnop', 'base-secret-value']) {
    assert.ok(!text.includes(leaked), `${leaked} leaked`);
  }
  assert.equal(record.apiKey, '[REDACTED]');
  assert.equal(record.appSecret, '[REDACTED]');
  assert.equal(record.nested.ok, 'fine');
  assert.match(record.message, /^calling with \[REDACTED\] and \[REDACTED\]$/);
});

it('leaves ordinary Lark ids and text alone', (t) => {
  const [record] = captured(t, () => createLogger('info').info('event_delivered', { chatRef: 'oc_abcdef1234567890', seq: 3 }));
  assert.equal(record.chatRef, 'oc_abcdef1234567890');
  assert.equal(record.seq, 3);
});
