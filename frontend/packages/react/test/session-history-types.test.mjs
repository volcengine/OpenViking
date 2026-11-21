import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);

test('published VikingBot sessions accept TanStack Query fetchNextPage directly', () => {
  const cli = join(
    dirname(require.resolve('typescript/package.json')),
    'bin',
    'tsc',
  );
  const file = fileURLToPath(
    new URL('./fixtures/session-history-types.ts', import.meta.url),
  );
  const result = spawnSync(
    process.execPath,
    [
      cli,
      '--noEmit',
      '--strict',
      '--skipLibCheck',
      '--target',
      'es2022',
      '--module',
      'nodenext',
      file,
    ],
    { encoding: 'utf8' },
  );
  assert.equal(result.status, 0, result.stdout + result.stderr);
});
