import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import {
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { JSDOM } from 'jsdom';
import {
  getDependencyReleaseLine,
  bilingualBlock,
  groupChangelog,
  localizeChangelog,
  parseSummary,
} from './bilingual-changelog.mjs';
import { checkChangesets, syncReleaseNotes } from './release-notes.mjs';

const summary = '## en-US\n\nFix navigation.\n\n## zh-CN\n\n修复导航。';

test('notes require exactly two nonempty language sections', () => {
  assert.deepEqual(parseSummary(summary), {
    'en-US': 'Fix navigation.',
    'zh-CN': '修复导航。',
  });
  for (const invalid of [
    'English only',
    '## en-US\nEnglish only',
    `${summary}\n## en-US\nDuplicate`,
    '## en-US\n\n## zh-CN\n中文',
    `Unscoped text\n${summary}`,
  ])
    assert.throws(() => parseSummary(invalid));
});

test('dependency-only releases retain localized notes', async () => {
  const block = await getDependencyReleaseLine(
    [],
    [{ name: 'dependency', newVersion: '1.2.0' }],
  );
  const changelog = `# @openviking/react\n\n## 0.2.0\n\n### Patch Changes\n\n${block}\n`;
  assert.match(
    localizeChangelog(changelog, 'zh-CN'),
    /更新依赖：[\s\S]*dependency@1.2.0/,
  );
  assert.doesNotMatch(localizeChangelog(changelog, 'en-US'), /更新依赖/);
  assert.throws(() =>
    localizeChangelog(
      changelog.replace('release-note:zh-CN', 'broken'),
      'en-US',
    ),
  );
});

test('real Changesets version consumes one bilingual note and generates distinct docs', async () => {
  const root = mkdtempSync(join(tmpdir(), 'ov-release-notes-'));
  try {
    for (const path of [
      '.changeset',
      'scripts',
      'packages/react',
      'docs/docs/en/guide',
      'docs/docs/zh/guide',
    ]) {
      mkdirSync(join(root, path), { recursive: true });
    }
    const write = (path, value) => writeFileSync(join(root, path), value);
    write(
      'package.json',
      JSON.stringify({ private: true, workspaces: ['packages/*'] }),
    );
    write(
      'packages/react/package.json',
      JSON.stringify({ name: '@openviking/react', version: '0.1.0' }),
    );
    copyFileSync(
      new URL('../.changeset/config.json', import.meta.url),
      join(root, '.changeset/config.json'),
    );
    copyFileSync(
      new URL('./bilingual-changelog.mjs', import.meta.url),
      join(root, 'scripts/bilingual-changelog.mjs'),
    );
    write(
      '.changeset/fix.md',
      `---\n'@openviking/react': patch\n---\n\n${summary}\n`,
    );
    write('.changeset/empty.md', '---\n---\n');
    checkChangesets(root);
    write(
      '.changeset/invalid.md',
      "---\n'@openviking/react': patch\n---\nEnglish only\n",
    );
    assert.throws(() => checkChangesets(root), /invalid.md/);
    rmSync(join(root, '.changeset/invalid.md'));
    execFileSync('git', ['init', '-q'], { cwd: root });
    const cli = fileURLToPath(
      new URL('../node_modules/@changesets/cli/bin.js', import.meta.url),
    );
    execFileSync(process.execPath, [cli, 'version'], {
      cwd: root,
      stdio: 'pipe',
    });
    const manifest = JSON.parse(
      readFileSync(join(root, 'packages/react/package.json'), 'utf8'),
    );
    assert.equal(manifest.version, '0.1.1');
    assert.throws(
      () => readFileSync(join(root, '.changeset/fix.md')),
      /ENOENT/,
    );
    await syncReleaseNotes(root);
    await syncReleaseNotes(root, true);
    const en = readFileSync(
      join(root, 'docs/docs/en/guide/changelog.md'),
      'utf8',
    );
    const zh = readFileSync(
      join(root, 'docs/docs/zh/guide/changelog.md'),
      'utf8',
    );
    assert.match(en, /### 0.1.1[\s\S]*Fix navigation/);
    assert.doesNotMatch(en, /修复导航|release-note:/);
    assert.match(zh, /### 0.1.1[\s\S]*修复与改进[\s\S]*修复导航/);
    assert.doesNotMatch(zh, /Fix navigation|release-note:/);
    write('docs/docs/zh/guide/changelog.md', 'stale');
    await assert.rejects(
      () => syncReleaseNotes(root, true),
      /sync:release-notes/,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('docs show only the current major and group releases by minor', () => {
  const block = bilingualBlock({ 'en-US': '- Updated.', 'zh-CN': '- 更新。' });
  const source =
    '# @openviking/react\n\n' +
    ['2.0.0', '1.2.1', '1.2.0', '1.1.0', '0.9.0']
      .map((version) => `## ${version}\n\n${block}`)
      .join('\n\n');
  const notes = groupChangelog(source, 'en-US', '1.2.1');
  const dom = new JSDOM(notes);
  try {
    const groups = [
      ...dom.window.document.querySelectorAll('[name="ov-release-minors"]'),
    ];
    assert.deepEqual(
      groups.map((group) => group.querySelector('summary').textContent),
      ['1.2.x', '1.1.x'],
    );
    assert.deepEqual(
      groups.map((group) => group.open),
      [true, false],
    );
    assert.equal(groups[0].querySelectorAll('details').length, 0);
    assert.equal(groups[0].querySelectorAll('summary').length, 1);
    assert.match(
      groups[0].textContent,
      /### 1.2.1[\s\S]*Updated.[\s\S]*### 1.2.0[\s\S]*Updated./,
    );
  } finally {
    dom.window.close();
  }
  assert.doesNotMatch(notes, /2.0.0|0.9.0|更新/);
  assert.match(source, /0.9.0/);
});
