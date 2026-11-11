import assert from 'node:assert/strict';
import test from 'node:test';
import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  copyFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { resolveRelease, stageRelease } from './react-release.mjs';

const push = {
  GITHUB_EVENT_NAME: 'push',
  GITHUB_REF_TYPE: 'branch',
  GITHUB_RUN_NUMBER: '42',
  GITHUB_RUN_ATTEMPT: '1',
  GITHUB_SHA: 'abcdef0123456789abcdef0123456789abcdef01',
};

test('main pushes use npm latest plus one patch, regardless of the manifest', () => {
  assert.deepEqual(resolveRelease(push, '0.0.1', '0.1.7'), {
    channel: 'beta',
    version: '0.1.8-beta.42.1.gabcdef012345',
  });
});

test('reruns and fork commits produce distinct beta versions', () => {
  const first = resolveRelease(push, '0.1.7', '0.1.7').version;
  assert.notEqual(
    resolveRelease({ ...push, GITHUB_RUN_ATTEMPT: '2' }, '0.1.7', '0.1.7')
      .version,
    first,
  );
  assert.notEqual(
    resolveRelease({ ...push, GITHUB_SHA: 'a'.repeat(40) }, '0.1.7', '0.1.7')
      .version,
    first,
  );
});

test('manual latest requires an explicit stable version', () => {
  const manual = {
    ...push,
    GITHUB_EVENT_NAME: 'workflow_dispatch',
    RELEASE_CHANNEL: 'latest',
  };
  assert.throws(() => resolveRelease(manual, '0.1.7'));
  for (const version of ['0.1.8-beta.1', 'v0.1.8', '01.1.8', '0.1.8\n']) {
    assert.throws(() =>
      resolveRelease({ ...manual, RELEASE_VERSION: version }, '0.1.7'),
    );
  }
  assert.deepEqual(
    resolveRelease({ ...manual, RELEASE_VERSION: '0.1.8' }, '0.1.7'),
    { channel: 'latest', version: '0.1.8' },
  );
});

test('manual beta also uses npm latest plus one patch', () => {
  assert.equal(
    resolveRelease(
      {
        ...push,
        GITHUB_EVENT_NAME: 'workflow_dispatch',
        RELEASE_CHANNEL: 'beta',
      },
      '9.9.9',
      '0.2.9',
    ).version,
    '0.2.10-beta.42.1.gabcdef012345',
  );
});

test('beta cannot override or fall back from the last stable version', () => {
  for (const version of [undefined, '', '0.1.8-beta.1', '01.1.8', '0.1.8\n']) {
    assert.throws(() => resolveRelease(push, '0.1.7', version));
  }
  assert.throws(() =>
    resolveRelease(
      {
        ...push,
        GITHUB_EVENT_NAME: 'workflow_dispatch',
        RELEASE_CHANNEL: 'beta',
        RELEASE_VERSION: '1.0.0',
      },
      '0.1.7',
      '0.1.7',
    ),
  );
});

test('tags must match the stable package version', () => {
  const tag = { GITHUB_REF_TYPE: 'tag', GITHUB_REF_NAME: 'react@0.1.7' };
  assert.deepEqual(resolveRelease(tag, '0.1.7'), {
    channel: 'latest',
    version: '0.1.7',
  });
  assert.throws(() => resolveRelease(tag, '0.1.8'));
  assert.throws(() =>
    resolveRelease({ ...tag, GITHUB_REF_NAME: 'other@0.1.7' }, '0.1.7'),
  );
});

test('invalid channel or missing beta identity fails before publishing', () => {
  assert.throws(() =>
    resolveRelease({ GITHUB_EVENT_NAME: 'workflow_dispatch' }, '0.1.7'),
  );
  assert.throws(() => resolveRelease({}, '0.1.7', '0.1.7'));
});

test('release staging keeps docs and the lockfile on the beta workspace package', () => {
  const root = mkdtempSync(join(tmpdir(), 'ov-react-release-'));
  try {
    for (const path of ['packages/react', 'docs']) {
      mkdirSync(join(root, path), { recursive: true });
    }
    for (const path of [
      'packages/react/package.json',
      'docs/package.json',
      'package-lock.json',
    ]) {
      copyFileSync(new URL(`../${path}`, import.meta.url), join(root, path));
    }
    const release = resolveRelease(push, '0.1.7', '0.1.7');
    stageRelease(root, release);
    const read = (path) => JSON.parse(readFileSync(join(root, path), 'utf8'));
    const lock = read('package-lock.json');
    assert.equal(read('packages/react/package.json').version, release.version);
    assert.equal(
      read('docs/package.json').dependencies['@openviking/react'],
      release.version,
    );
    assert.equal(lock.packages['packages/react'].version, release.version);
    assert.equal(
      lock.packages.docs.dependencies['@openviking/react'],
      release.version,
    );
    assert.deepEqual(lock.packages['node_modules/@openviking/react'], {
      resolved: 'packages/react',
      link: true,
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
