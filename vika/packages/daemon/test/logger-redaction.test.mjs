import test from 'node:test';
import assert from 'node:assert/strict';
import { createLogger } from '../src/logger.mjs';
test('logger cleans messages, base fields and top-level secret keys', () => {
  const lines = [];
  const original = process.stdout.write;
  process.stdout.write = (value) => { lines.push(value); return true; };
  try {
    createLogger('info', { password: 'base-secret', detail: 'Bearer base-token' })
      .child({ credential: 'child-secret' })
      .info('Bearer message-token', { api_key: 'top-secret', nested: { token: 'nested-secret' }, detail: 'safe', count: 2 });
  } finally { process.stdout.write = original; }
  const record = JSON.parse(lines[0]);
  for (const secret of ['base-secret', 'child-secret', 'message-token', 'top-secret', 'nested-secret']) assert.ok(!lines[0].includes(secret));
  assert.equal(record.api_key, '[REDACTED]');
  assert.equal(record.detail, 'safe');
  assert.equal(record.count, 2);
});
