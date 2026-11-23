import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import {
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { bilingualBlock } from './bilingual-changelog.mjs';
import { prepareReactRelease } from './react-release-prepare.mjs';

const reactPackage = '@openviking/react';
const current = '0.1.12';
const manifest = 'packages/react/package.json';
const changelog = 'packages/react/CHANGELOG.md';

function releaseWorkspace(t, bump = 'minor', previous = current) {
  const root = mkdtempSync(join(tmpdir(), 'ov-react-prepare-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const git = (...args) =>
    execFileSync('git', args, { cwd: root, encoding: 'utf8' }).trim();
  function write(path, data) {
    const target = join(root, path);
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(
      target,
      typeof data === 'string' ? data : JSON.stringify(data, null, 2) + '\n',
    );
  }
  write('package.json', {
    name: 'release-fixture',
    private: true,
    workspaces: ['packages/*', 'docs'],
  });
  write(manifest, { name: reactPackage, version: previous });
  const docs = {
    name: 'release-docs',
    version: '0.0.0',
    private: true,
    dependencies: { [reactPackage]: previous },
  };
  write('docs/package.json', docs);
  write('package-lock.json', {
    name: 'release-fixture',
    lockfileVersion: 3,
    packages: { 'packages/react': { version: previous }, docs },
  });
  write(
    changelog,
    `# ${reactPackage}\n\n## ${previous}\n\n### Patch Changes\n\n${bilingualBlock(
      {
        'en-US': '- Prior release.',
        'zh-CN': '- 上一版本。',
      },
    )}\n`,
  );
  write(
    '.changeset/feature.md',
    `---\n'${reactPackage}': ${bump}\n---\n\n## en-US\n\nSupport more workspaces.\n\n## zh-CN\n\n支持更多工作区。\n`,
  );
  write(
    '.changeset/fix.md',
    `---\n'${reactPackage}': patch\n---\n\n## en-US\n\nFix the preview title.\n\n## zh-CN\n\n修复预览标题。\n`,
  );
  for (const language of ['en', 'zh'])
    write(`docs/docs/${language}/guide/changelog.md`, '');
  mkdirSync(join(root, 'scripts'));
  copyFileSync(
    new URL('./bilingual-changelog.mjs', import.meta.url),
    join(root, 'scripts/bilingual-changelog.mjs'),
  );
  copyFileSync(
    new URL('../.changeset/config.json', import.meta.url),
    join(root, '.changeset/config.json'),
  );
  git('init', '--quiet', '--initial-branch=main');
  git('add', '.');
  git(
    '-c',
    'user.name=Release Test',
    '-c',
    'user.email=release@example.test',
    'commit',
    '--quiet',
    '-m',
    'Pending release',
  );
  const read = (path) => readFileSync(join(root, path), 'utf8');
  return { root, read, git };
}

for (const version of ['0.1.13', '0.2.0']) {
  test(`prepares exact stable ${version} from pending minor and patch changesets`, async (t) => {
    const { root, read, git } = releaseWorkspace(t);
    const head = git('rev-parse', 'HEAD');
    await prepareReactRelease(root, version);

    assert.equal(git('rev-parse', 'HEAD'), head);
    assert.equal(git('tag', '--list'), '');
    assert.equal(git('diff', '--cached', '--name-only'), '');
    assert.deepEqual(readdirSync(join(root, '.changeset')), ['config.json']);
    assert.equal(JSON.parse(read(manifest)).version, version);
    assert.equal(
      JSON.parse(read('docs/package.json')).dependencies[reactPackage],
      version,
    );
    const { packages } = JSON.parse(read('package-lock.json'));
    assert.equal(packages['packages/react'].version, version);
    assert.equal(packages.docs.dependencies[reactPackage], version);

    const archive = read(changelog);
    assert.equal(
      archive.split('\n').filter((line) => line === `## ${version}`).length,
      1,
    );
    assert.match(archive, /### Minor Changes[\s\S]*Support more workspaces/);
    assert.match(archive, /### Patch Changes[\s\S]*Fix the preview title/);
    assert.ok(
      archive.includes(`- ${head.slice(0, 7)}: Support more workspaces.`),
    );
    assert.ok(archive.includes(`## ${current}`));
    const en = read('docs/docs/en/guide/changelog.md');
    const zh = read('docs/docs/zh/guide/changelog.md');
    for (const docs of [en, zh]) assert.ok(docs.includes(`### ${version}`));
    assert.match(en, /Support more workspaces[\s\S]*Fix the preview title/);
    assert.doesNotMatch(en, /支持更多工作区/);
    assert.match(zh, /支持更多工作区[\s\S]*修复预览标题/);
    assert.doesNotMatch(zh, /Support more workspaces/);
  });
}

test('rejects unrelated targets before consuming Changesets', async (t) => {
  const { root, git } = releaseWorkspace(t);
  for (const version of ['0.1.10', '0.1.14', '0.2.1', '0.3.0', '0.1.13-beta']) {
    await assert.rejects(
      prepareReactRelease(root, version),
      /incompatible|exact stable version/,
    );
    assert.equal(git('status', '--porcelain'), '');
  }
});

test('rejects a patch override for a pending major release', async (t) => {
  const { root, git } = releaseWorkspace(t, 'major');
  await assert.rejects(
    prepareReactRelease(root, '0.1.13'),
    /major release 1\.0\.0/,
  );
  assert.equal(git('status', '--porcelain'), '');
});

test('rejects a minor-to-patch override after React UI reaches 1.0', async (t) => {
  const { root, git } = releaseWorkspace(t, 'minor', '1.2.3');
  await assert.rejects(
    prepareReactRelease(root, '1.2.4'),
    /minor release 1\.3\.0/,
  );
  assert.equal(git('status', '--porcelain'), '');
});
