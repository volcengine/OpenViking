import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveRelease } from './opencode-release-version.mjs';
const date = '2026.9.9';
test('first date release ignores old source version', () => {
  assert.deepEqual(resolveRelease({ versions: { '0.2.4': {} } }, 'new', date), { version: date, published: false });
});
test('same day chooses unused suffix, ignoring dev releases', () => {
  assert.equal(resolveRelease({ versions: { [date]: {}, [`${date}-2`]: {}, [`${date}-dev.99`]: {} } }, 'new', date).version, `${date}-3`);
});
test('retry after successful publish reuses source commit even on another day', () => {
  assert.deepEqual(resolveRelease({ versions: { old: { version: '2026.9.8', openvikingSourceCommit: 'same' } } }, 'same', date), { version: '2026.9.8', published: true });
});
test('malformed registry data cannot allocate a version', () => {
  assert.throws(() => resolveRelease({}, 'new', date));
});
