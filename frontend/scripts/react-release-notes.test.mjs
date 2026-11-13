import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { bilingualBlock } from './bilingual-changelog.mjs';
import {
  githubReleaseNotes,
  publishGitHubRelease,
} from './react-github-release.mjs';

const entry = (version, en = '- Fixed.', zh = '- 已修复。') =>
  `## ${version}\n\n### Patch Changes\n\n${bilingualBlock({ 'en-US': en, 'zh-CN': zh })}\n`;
const expected =
  '## en-US\n\n### Patch Changes\n\n- Fixed.\n\n## zh-CN\n\n### 修复与改进\n\n- 已修复。\n';
const environment = {
  GITHUB_REPOSITORY: 'owner/repo',
  GITHUB_SHA: 'abcdef0123456789abcdef0123456789abcdef01',
  GH_TOKEN: 'test-token',
};

test('GitHub notes reuse only the exact archived version in both languages', () => {
  const source = [
    entry('0.1.12', '- New.', '- 新功能。'),
    entry('0.1.11'),
    entry('0.1.1'),
  ].join('\n');
  assert.equal(githubReleaseNotes(source, '0.1.11'), expected);
  for (const invalid of [
    entry('0.1.1'),
    entry('0.1.11') + entry('0.1.11'),
    entry('0.1.11').replace('release-note:zh-CN', 'broken'),
    entry('0.1.11', '', ''),
    entry('0.1.11', '- Fixed.', ''),
  ])
    assert.throws(() => githubReleaseNotes(invalid, '0.1.11'));
});

test('each archived language block must be nonempty beside populated notes', () => {
  for (const locale of ['en-US', 'zh-CN']) {
    const emptyBlock = bilingualBlock({
      'en-US': '- Second fix.',
      'zh-CN': '- 第二项修复。',
      [locale]: ' \n ',
    });
    for (const source of [
      entry('0.1.11') + '\n' + emptyBlock,
      `## 0.1.11\n\n${emptyBlock}\n\n${bilingualBlock({ 'en-US': '- Fixed.', 'zh-CN': '- 已修复。' })}`,
    ])
      assert.throws(
        () => githubReleaseNotes(source, '0.1.11'),
        new RegExp(`${locale} release note is empty`),
      );
  }
});

test('CLI preparation matches the archived bilingual notes', () => {
  const root = mkdtempSync(join(tmpdir(), 'ov-github-notes-'));
  try {
    const file = join(root, 'notes.md');
    execFileSync(process.execPath, [
      fileURLToPath(new URL('./react-github-release.mjs', import.meta.url)),
      'prepare',
      '0.1.11',
      file,
    ]);
    const source = readFileSync(
      new URL('../packages/react/CHANGELOG.md', import.meta.url),
      'utf8',
    );
    assert.equal(
      readFileSync(file, 'utf8'),
      githubReleaseNotes(source, '0.1.11'),
    );
    assert.match(readFileSync(file, 'utf8'), /permission[\s\S]*access/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('stable publication creates once and reruns update the same release', async () => {
  let release;
  let creations = 0;
  const request = async (url, options) => {
    if (!options.method) {
      assert.ok(url.endsWith('/tags/react%400.1.11'));
      return Response.json(release ?? {}, { status: release ? 200 : 404 });
    }
    const payload = JSON.parse(options.body);
    assert.equal(payload.make_latest, 'false');
    assert.equal(payload.prerelease, false);
    assert.equal(payload.draft, false);
    if (options.method === 'POST') {
      creations++;
      assert.equal(payload.tag_name, 'react@0.1.11');
      assert.equal(payload.target_commitish, environment.GITHUB_SHA);
      release = {
        id: 42,
        html_url: 'https://github.com/owner/repo/releases/tag/react@0.1.11',
      };
    } else {
      assert.equal(options.method, 'PATCH');
      assert.ok(url.endsWith('/releases/42'));
      assert.equal(payload.tag_name, undefined);
      assert.equal(payload.target_commitish, undefined);
    }
    Object.assign(release, payload);
    return Response.json(release, {
      status: options.method === 'POST' ? 201 : 200,
    });
  };
  assert.equal(
    await publishGitHubRelease('0.1.11', expected, environment, request),
    'https://github.com/owner/repo/releases/tag/react@0.1.11',
  );
  await publishGitHubRelease(
    '0.1.11',
    expected + '\nCorrection.\n',
    environment,
    request,
  );
  assert.equal(creations, 1);
  assert.equal(release.body, expected + '\nCorrection.\n');
});

test('lookup failures do not turn into create attempts and failed writes reject', async () => {
  for (const status of [403, 500]) {
    await assert.rejects(
      publishGitHubRelease(
        '0.1.11',
        expected,
        environment,
        async (_url, options) => {
          assert.equal(options.method, undefined);
          return Response.json({}, { status });
        },
      ),
      new RegExp(`lookup failed: ${status}`),
    );
  }
  await assert.rejects(
    publishGitHubRelease(
      '0.1.11',
      expected,
      environment,
      async (_url, options) =>
        Response.json({}, { status: options.method ? 403 : 404 }),
    ),
    /write failed: 403/,
  );
});
