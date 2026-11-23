import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import {
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { planRelease, resolveRelease } from './react-release.mjs';

const push = {
  GITHUB_EVENT_NAME: 'push',
  GITHUB_REF_TYPE: 'branch',
};

test('manual stable release prepares new notes on main and publishes archived notes directly', () => {
  const manual = {
    GITHUB_EVENT_NAME: 'workflow_dispatch',
    GITHUB_REF: 'refs/heads/main',
    RELEASE_CHANNEL: 'latest',
    RELEASE_VERSION: '0.1.13',
  };
  assert.deepEqual(planRelease(manual, '## 0.1.12\n'), {
    mode: 'prepare',
    version: '0.1.13',
  });
  assert.deepEqual(planRelease(manual, '## 0.1.13\n'), { mode: 'publish' });
  assert.throws(
    () => planRelease({ ...manual, GITHUB_REF: 'refs/heads/topic' }, ''),
    /from main/,
  );
});

test('an approved main push releases the archived version matching the manifest', () => {
  const main = { ...push, GITHUB_REF: 'refs/heads/main' };
  const changelog = '## 0.1.13\n';
  assert.deepEqual(planRelease(main, changelog, '0.1.13'), {
    mode: 'publish',
    preparedVersion: '0.1.13',
  });
  const env = { ...main, PREPARED_RELEASE_VERSION: '0.1.13' };
  assert.deepEqual(resolveRelease(env, '0.1.13'), {
    channel: 'latest',
    version: '0.1.13',
  });
  assert.throws(() => resolveRelease(env, '0.1.12'), /manifest/);
  assert.throws(() => planRelease(main, '## 0.1.12\n', '0.1.13'), /archived/);
  assert.throws(
    () => resolveRelease({ ...env, GITHUB_REF: 'refs/heads/topic' }, '0.1.13'),
    /main push/,
  );
  assert.deepEqual(planRelease(main, changelog), { mode: 'publish' });
});

test('CLI only publishes a release request on the main push that adds it', () => {
  const repo = realpathSync(mkdtempSync(join(tmpdir(), 'ov-release-plan-')));
  try {
    const git = (...args) => {
      const result = spawnSync('git', args, { cwd: repo, encoding: 'utf8' });
      assert.equal(result.status, 0, result.stderr);
      return result.stdout.trim();
    };
    const root = join(repo, 'frontend');
    for (const path of ['scripts', 'packages/react', '.changeset'])
      mkdirSync(join(root, path), { recursive: true });
    const cli = join(root, 'scripts/react-release.mjs');
    copyFileSync(new URL('./react-release.mjs', import.meta.url), cli);
    writeFileSync(join(root, 'packages/react/CHANGELOG.md'), '## 0.1.12\n');
    git('init', '-q');
    git('config', 'user.name', 'Release test');
    git('config', 'user.email', 'test@example.com');
    git('add', '.');
    git('commit', '-qm', 'Baseline');
    const before = git('rev-parse', 'HEAD');
    writeFileSync(join(root, 'packages/react/CHANGELOG.md'), '## 0.1.13\n');
    writeFileSync(
      join(root, '.changeset/react-stable-release.json'),
      '{"version":"0.1.13"}\n',
    );
    git('add', '.');
    git('commit', '-qm', 'Release request');
    const planned = git('rev-parse', 'HEAD');
    const output = join(repo, 'github-output');
    const planPush = (base) => {
      writeFileSync(output, '');
      const result = spawnSync(process.execPath, [cli, '--plan'], {
        cwd: root,
        encoding: 'utf8',
        env: {
          ...process.env,
          GITHUB_EVENT_NAME: 'push',
          GITHUB_REF: 'refs/heads/main',
          GITHUB_REF_TYPE: 'branch',
          GITHUB_OUTPUT: output,
          PUSH_BEFORE: base,
        },
      });
      assert.equal(result.status, 0, result.stderr);
      return readFileSync(output, 'utf8');
    };
    assert.equal(planPush(before), 'mode=publish\npreparedVersion=0.1.13\n');
    git('commit', '--allow-empty', '-qm', 'Later push');
    assert.equal(planPush(planned), 'mode=publish\n');
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
});
