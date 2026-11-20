import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount, ok } from './helpers/console-dom.mjs';

test('grep preserves regex backslashes in quoted patterns', async () => {
  const patterns = [];
  const host = await mount(async (_url, init) => {
    patterns.push(JSON.parse(init.body).pattern);
    return ok({ matches: [] });
  });
  try {
    const phonePattern = String.raw`\d{3}-\d{3}-\d{4}`;
    await host.execute(`ov grep "${phonePattern}"`);
    await host.execute(`ov grep '${phonePattern}'`);
    await host.execute(String.raw`ov grep "\"quoted\"\\\\path\s+\bword\b\."`);
    assert.deepEqual(patterns, [
      phonePattern,
      phonePattern,
      String.raw`"quoted"\\path\s+\bword\b\.`,
    ]);
  } finally {
    await host.dispose();
  }
});
