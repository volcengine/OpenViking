import assert from 'node:assert/strict';
import test from 'node:test';
import { copyFile, mkdtemp, rm, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const require = createRequire(import.meta.url);
test('published analytics and monitoring declarations enforce event payloads', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'ov-analytics-types-'));
  try {
    await symlink(
      fileURLToPath(new URL('../../../node_modules', import.meta.url)),
      join(directory, 'node_modules'),
      'dir',
    );
    const files = ['analytics-types.ts', 'monitoring-types.ts'];
    for (const file of files) {
      await copyFile(
        new URL(`./fixtures/${file}`, import.meta.url),
        join(directory, file),
      );
    }
    const result = spawnSync(
      process.execPath,
      [
        join(dirname(require.resolve('typescript/package.json')), 'bin', 'tsc'),
        '--noEmit',
        '--strict',
        '--skipLibCheck',
        '--target',
        'es2022',
        '--module',
        'nodenext',
        '--moduleResolution',
        'nodenext',
        ...files.map((file) => join(directory, file)),
      ],
      { encoding: 'utf8' },
    );
    assert.equal(result.status, 0, result.stdout + result.stderr);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
