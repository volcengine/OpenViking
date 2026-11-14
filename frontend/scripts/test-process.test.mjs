import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import test from 'node:test';
import { startTests } from './test-process.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const pause = () => new Promise((resolve) => setTimeout(resolve, 20));
const alive = (pid) => {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    if (error.code !== 'ESRCH') throw error;
    return false;
  }
};

async function fixture(t, source) {
  const directory = await mkdtemp(join(tmpdir(), 'ov-test-guard-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const file = join(directory, 'fixture.test.mjs');
  await writeFile(file, source);
  // Nested runners must start outside the enclosing node:test worker context.
  const env = { ...process.env };
  delete env.NODE_TEST_CONTEXT;
  const run = startTests(['--test-reporter=tap', file], {
    cwd: root,
    env,
    timeout: 10_000,
    stdio: 'pipe',
  });
  let output = '';
  for (const stream of [run.child.stdout, run.child.stderr])
    stream.on('data', (chunk) => {
      output += chunk;
    });
  return { ...run, output: () => output };
}

test('a deliberately missed picker focus exits with a bounded assertion failure', async (t) => {
  const file = new URL(
    '../packages/react/test/viking-bot-directory-errors.test.mjs',
    import.meta.url,
  );
  const original = await readFile(file, 'utf8');
  assert.ok(original.includes('node.focus();'));
  const source = original
    .replace(
      'node.focus();',
      '// Deliberately omit the browser focus behavior.',
    )
    .replace(
      "'./helpers/module-dom.mjs'",
      JSON.stringify(new URL('helpers/module-dom.mjs', file).href),
    );
  const directory = await mkdtemp(join(tmpdir(), 'ov-focus-failure-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  await symlink(
    join(root, 'node_modules'),
    join(directory, 'node_modules'),
    process.platform === 'win32' ? 'junction' : 'dir',
  );
  const copy = join(directory, 'focus.test.mjs');
  await writeFile(copy, source);
  const run = await fixture(
    t,
    `import ${JSON.stringify(pathToFileURL(copy).href)};`,
  );
  assert.equal(await run.completed, 1);
  const output = run.output();
  assert.match(output, /focus returns to the picker trigger/);
  assert.match(output, /ERR_ASSERTION/);
  assert.doesNotMatch(output, /heap out of memory|wall-clock limit/);
  assert.ok(
    output.length < 16_384,
    `failure output was ${output.length} characters`,
  );
});

test('test workers inherit the heap cap and propagate their exit code', async (t) => {
  const run = await fixture(
    t,
    `
    import assert from 'node:assert/strict';
    import { getHeapStatistics } from 'node:v8';
    assert.ok(getHeapStatistics().heap_size_limit < 600 * 1024 ** 2);
    process.exitCode = 7;
  `,
  );
  assert.equal(await run.completed, 1);
  assert.match(run.output(), /exitCode: 7/);
});

for (const termination of ['timeout', 'SIGTERM'])
  test(
    `the external guard stops a synchronously blocked worker and its child on ${termination}`,
    { skip: process.platform === 'win32' },
    async (t) => {
      const directory = await mkdtemp(join(tmpdir(), 'ov-blocked-test-'));
      t.after(() => rm(directory, { recursive: true, force: true }));
      const pidsFile = join(directory, 'pids.json');
      const file = join(directory, 'blocked.test.mjs');
      await writeFile(
        file,
        `
    import { spawn } from 'node:child_process';
    import { writeFileSync } from 'node:fs';
    const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });
    writeFileSync(${JSON.stringify(pidsFile)}, JSON.stringify([process.pid, child.pid]));
    while (true) {}
  `,
      );
      const env = { ...process.env };
      delete env.NODE_TEST_CONTEXT;
      const started = Date.now();
      const run = startTests([file], { env, timeout: 2000, stdio: 'ignore' });
      if (termination === 'SIGTERM') {
        for (let attempt = 0; attempt < 75; attempt++) {
          try {
            await readFile(pidsFile);
            break;
          } catch {
            await pause();
          }
        }
        process.kill(process.pid, 'SIGTERM');
      }
      assert.equal(await run.completed, termination === 'timeout' ? 124 : 143);
      assert.ok(Date.now() - started < 8000);
      const pids = JSON.parse(await readFile(pidsFile, 'utf8'));
      t.after(() => {
        for (const pid of pids) if (alive(pid)) process.kill(pid, 'SIGKILL');
      });
      for (let attempt = 0; attempt < 100 && pids.some(alive); attempt++)
        await pause();
      assert.deepEqual(pids.filter(alive), []);
    },
  );
