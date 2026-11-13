import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';

const require = createRequire(import.meta.url);
const args = process.argv.slice(2);
const fix = args[0] === '--fix';
const targets = fix ? args.slice(1) : args;
if (targets.length === 0) targets.push('.');

function run(cli, options) {
  const result = spawnSync(process.execPath, [cli, ...options, ...targets], {
    stdio: 'inherit',
  });
  if (result.error) console.error(result.error.message);
  return result.status ?? 1;
}

const lintStatus = run(
  join(dirname(require.resolve('@rslint/core/package.json')), 'bin/rslint.js'),
  ['--type-check', '--max-warnings', '0', ...(fix ? ['--fix'] : [])],
);
const formatStatus = run(require.resolve('prettier/bin/prettier.cjs'), [
  fix ? '--write' : '--check',
  '--ignore-unknown',
]);

process.exitCode = lintStatus || formatStatus;
