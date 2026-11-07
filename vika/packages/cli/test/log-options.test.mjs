import test from 'node:test';
import assert from 'node:assert/strict';
import { parseLogOptions } from '../src/log-options.mjs';
test('logs retains interactive follow and accepts bounded output', () => {
  assert.deepEqual(parseLogOptions(['primary']), { target: 'primary', lines: 100, follow: true });
  assert.deepEqual(parseLogOptions(['--no-follow', 'primary', '--lines', '5']), { target: 'primary', lines: 5, follow: false });
  assert.equal(parseLogOptions(['--nostream']).follow, false);
  for (const args of [['--lines'], ['--lines', '-1'], ['--lines', '2.5'], ['--bogus'], ['one', 'two']]) assert.throws(() => parseLogOptions(args));
});
