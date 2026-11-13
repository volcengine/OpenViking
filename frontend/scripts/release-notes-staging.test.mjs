import assert from 'node:assert/strict';
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { bilingualBlock } from './bilingual-changelog.mjs';
import { resolveRelease, stageRelease } from './react-release.mjs';
import { syncReleaseNotes } from './release-notes.mjs';

test('beta staging preserves prepared stable release notes across majors', async () => {
  const root = mkdtempSync(join(tmpdir(), 'ov-notes-staging-'));
  try {
    for (const path of [
      '.changeset',
      'packages/react',
      'docs/docs/en/guide',
      'docs/docs/zh/guide',
    ])
      mkdirSync(join(root, path), { recursive: true });
    const write = (path, value) => writeFileSync(join(root, path), value);
    const manifest = { version: '1.0.0' };
    const docs = { dependencies: { '@openviking/react': '1.0.0' } };
    write('packages/react/package.json', JSON.stringify(manifest));
    write('docs/package.json', JSON.stringify(docs));
    write(
      'package-lock.json',
      JSON.stringify({ packages: { 'packages/react': manifest, docs } }),
    );
    const notes = bilingualBlock({
      'en-US': '- Updated.',
      'zh-CN': '- 更新。',
    });
    write(
      'packages/react/CHANGELOG.md',
      `# @openviking/react\n\n## 1.0.0\n\n${notes}\n\n## 0.1.10\n\n${notes}\n`,
    );
    await syncReleaseNotes(root);
    const paths = ['en', 'zh'].map(
      (language) => `docs/docs/${language}/guide/changelog.md`,
    );
    const readPages = () =>
      paths.map((path) => readFileSync(join(root, path), 'utf8'));
    const prepared = readPages();
    const release = resolveRelease(
      {
        GITHUB_EVENT_NAME: 'push',
        GITHUB_REF_TYPE: 'branch',
        GITHUB_RUN_NUMBER: '42',
        GITHUB_RUN_ATTEMPT: '1',
        GITHUB_SHA: 'a'.repeat(40),
      },
      '1.0.0',
      '0.1.10',
    );
    assert.match(release.version, /^0\.1\.11-beta\./);
    stageRelease(root, release);
    await syncReleaseNotes(root, true);
    await syncReleaseNotes(root);
    assert.deepEqual(readPages(), prepared);
    assert.match(prepared[0], /1\.0\.0/);
    assert.doesNotMatch(prepared[0], /0\.1\.10|beta/);
    write(paths[0], 'stale');
    await assert.rejects(
      () => syncReleaseNotes(root, true),
      /sync:release-notes/,
    );
    // Stable releases must still check against the selected package major.
    stageRelease(root, { version: '2.0.0' });
    await assert.rejects(
      () => syncReleaseNotes(root),
      /No release notes for major 2/,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
