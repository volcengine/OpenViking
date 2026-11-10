// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const INSTALLER = fileURLToPath(new URL('../../../scripts/install.sh', import.meta.url));
const PLATFORM = `${process.platform}-${process.arch}`;

// A home directory, a fake pm2 that records any call, and a release archive
// laid out like build-release.mjs makes them.
function machine(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-install-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const home = path.join(root, 'home');
  const bin = path.join(root, 'bin');
  fs.mkdirSync(home, { recursive: true });
  fs.mkdirSync(bin);
  fs.writeFileSync(path.join(bin, 'pm2'), `#!/bin/sh\necho "$@" >> "${root}/pm2-calls"\n`, { mode: 0o755 });
  const archive = (version, { platform = PLATFORM, nodeWorks = true, ovWorks = true } = {}) => {
    const work = fs.mkdtempSync(path.join(root, 'build-'));
    const directory = path.join(work, `vika-${version}-${platform}`);
    fs.mkdirSync(path.join(directory, 'node', 'bin'), { recursive: true });
    fs.mkdirSync(path.join(directory, 'packages', 'cli', 'bin'), { recursive: true });
    fs.writeFileSync(path.join(directory, 'node', 'bin', 'node'), `#!/bin/sh
case "$1" in */check-ov.mjs) exit ${ovWorks ? 0 : 1} ;; esac
exit ${nodeWorks ? 0 : 1}
`, { mode: 0o755 });
    fs.writeFileSync(path.join(directory, 'packages', 'cli', 'bin', 'vika.mjs'), '');
    const file = path.join(root, `vika-${version}-${platform}.tar.gz`);
    execFileSync('tar', ['-czf', file, '-C', work, path.basename(directory)]);
    fs.writeFileSync(`${file}.sha256`, `${createHash('sha256').update(fs.readFileSync(file)).digest('hex')}  ${path.basename(file)}\n`);
    return file;
  };
  const install = (...args) => {
    const env = typeof args.at(-1) === 'object' ? args.pop() : {};
    return spawnSync('sh', [INSTALLER, '--no-setup', ...args], {
      encoding: 'utf8',
      env: { HOME: home, SHELL: '/bin/bash', PATH: `${bin}:${process.env.PATH}`, ...env },
    });
  };
  const vika = path.join(home, '.ovsa');
  return { root, home, vika, archive, install, pm2Calls: () => fs.existsSync(path.join(root, 'pm2-calls')) };
}

test('installs a release under ~/.ovsa, adds the launcher and PATH, and never touches pm2', (t) => {
  const m = machine(t);
  const bashRc = path.join(m.home, process.platform === 'darwin' ? '.bash_profile' : '.bashrc');
  const first = m.install('--file', m.archive('0.5.0'));
  assert.equal(first.status, 0, first.stderr);
  assert.equal(fs.realpathSync(path.join(m.vika, 'current')), fs.realpathSync(path.join(m.vika, 'releases', '0.5.0')));
  const launcher = fs.readFileSync(path.join(m.vika, 'bin', 'ovsa'), 'utf8');
  assert.equal(fs.readFileSync(path.join(m.vika, 'bin', 'vika'), 'utf8'), launcher);
  assert.equal(fs.statSync(path.join(m.vika, 'bin', 'ovsa')).mode & 0o111, 0o111);
  assert.match(launcher, /exec "\$HOME\/\.ovsa\/current\/node\/bin\/node" "\$HOME\/\.ovsa\/current\/packages\/cli\/bin\/vika\.mjs" "\$@"/);
  assert.equal(fs.statSync(path.join(m.vika, 'bin', 'vika')).mode & 0o111, 0o111);
  assert.match(fs.readFileSync(bashRc, 'utf8'), /export PATH="\$HOME\/\.ovsa\/bin:\$PATH"/);
  assert.equal(fs.existsSync(path.join(m.vika, 'deploy.lock')), false, 'the lock is released');
  assert.match(first.stderr, /下一步：运行 ~\/\.ovsa\/bin\/ovsa setup/);
  // Without an instance, running the installer again still leads to setup.
  const retried = m.install('--file', m.archive('0.5.0'));
  assert.match(retried.stderr, /已经是 OpenViking SuperAgent 0\.5\.0[\s\S]*下一步：运行 ~\/\.ovsa\/bin\/ovsa setup/);

  fs.writeFileSync(path.join(m.vika, 'config.json'), '{}');
  const second = m.install('--file', m.archive('0.6.0'));
  assert.equal(second.status, 0, second.stderr);
  assert.equal(fs.realpathSync(path.join(m.vika, 'rollback')), fs.realpathSync(path.join(m.vika, 'releases', '0.5.0')));
  assert.match(second.stderr, /ovsa restart/);
  assert.doesNotMatch(second.stderr, /下一步/);
  const again = m.install('--file', m.archive('0.6.0'));
  assert.match(again.stderr, /已经是 OpenViking SuperAgent 0\.6\.0/);
  assert.doesNotMatch(again.stderr, /ovsa restart|下一步/);
  assert.equal(fs.readFileSync(bashRc, 'utf8').match(/\.ovsa\/bin/g).length, 1, 'PATH is added once');
  assert.equal(m.pm2Calls(), false);
});

test('a shell rc file that cannot be written does not turn a completed install into a failure', (t) => {
  const m = machine(t);
  fs.mkdirSync(path.join(m.home, '.zshrc'));
  const result = m.install('--file', m.archive('0.5.0'), { SHELL: '/bin/zsh' });
  assert.equal(result.status, 0, result.stderr);
  assert.ok(fs.existsSync(path.join(m.vika, 'bin', 'vika')));
  assert.match(result.stderr, /写不了 ~\/\.zshrc；OpenViking SuperAgent 已安装完成/);
  assert.match(result.stderr, /export PATH="\$HOME\/\.ovsa\/bin:\$PATH"/);
});

test('refuses a corrupted archive, another platform, or a Node that cannot run here, leaving the install as it was', (t) => {
  const m = machine(t);
  assert.equal(m.install('--file', m.archive('0.5.0')).status, 0);
  const corrupted = m.archive('0.6.0');
  fs.writeFileSync(`${corrupted}.sha256`, `${'0'.repeat(64)}  x\n`);
  const bad = m.install('--file', corrupted);
  assert.notEqual(bad.status, 0);
  assert.match(bad.stderr, /校验失败/);

  const other = m.install('--file', m.archive('0.6.0', { platform: PLATFORM === 'linux-x64' ? 'darwin-arm64' : 'linux-x64' }));
  assert.notEqual(other.status, 0);
  assert.match(other.stderr, /不是给/);

  const broken = m.install('--file', m.archive('0.6.1', { nodeWorks: false }));
  assert.notEqual(broken.status, 0);
  assert.match(broken.stderr, /运行不了这个版本/);
  assert.equal(fs.realpathSync(path.join(m.vika, 'current')), fs.realpathSync(path.join(m.vika, 'releases', '0.5.0')));
  assert.deepEqual(fs.readdirSync(path.join(m.vika, 'releases')), ['0.5.0']);
  assert.equal(fs.existsSync(path.join(m.vika, 'deploy.lock')), false);
});

test('waits for a running upgrade instead of racing it', (t) => {
  const m = machine(t);
  fs.mkdirSync(path.join(m.vika, 'deploy.lock'), { recursive: true });
  const blocked = m.install('--file', m.archive('0.5.0'));
  assert.notEqual(blocked.status, 0);
  assert.match(blocked.stderr, /另一个安装或升级正在进行/);
  assert.ok(fs.existsSync(path.join(m.vika, 'deploy.lock')), 'someone else\'s lock is left alone');
});

test('an unavailable OV blocks even --no-setup and preserves the active installation', (t) => {
  const m = machine(t);
  const missing = m.install('--file', m.archive('0.5.0', { ovWorks: false }));
  assert.notEqual(missing.status, 0);
  assert.match(missing.stderr, /OpenViking 检查未通过/);
  assert.equal(fs.existsSync(path.join(m.vika, 'current')), false);
  assert.equal(fs.existsSync(path.join(m.vika, 'bin', 'vika')), false);
  assert.equal(m.install('--file', m.archive('0.5.0')).status, 0);
  const active = fs.realpathSync(path.join(m.vika, 'current'));
  assert.notEqual(m.install('--file', m.archive('0.6.0', { ovWorks: false })).status, 0);
  assert.equal(fs.realpathSync(path.join(m.vika, 'current')), active);
  assert.deepEqual(fs.readdirSync(path.join(m.vika, 'releases')), ['0.5.0']);
});

test('no variable runs into non-ASCII text, which macOS /bin/sh would read as part of its name', () => {
  const offending = fs.readFileSync(INSTALLER, 'utf8').split('\n')
    .map((line, index) => [index + 1, line])
    .filter(([, line]) => !line.trimStart().startsWith('#') && /\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7F]/.test(line));
  assert.deepEqual(offending, []);
});

test('the installer speaks the language the vika command would: --lang or VIKA_LANG, the saved one, then Chinese', (t) => {
  const m = machine(t);
  const english = m.install('--file', m.archive('0.5.0'), { LANG: 'en_US.UTF-8', VIKA_LANG: 'en' });
  assert.equal(english.status, 0, english.stderr);
  assert.match(english.stderr, /^Installing OpenViking SuperAgent vika-0\.5\.0-[\s\S]*Verify +SHA-256 matches[\s\S]*next: run ~\/\.ovsa\/bin\/ovsa setup\n$/);
  assert.doesNotMatch(english.stderr, /[一-鿿]/);
  const flagged = m.install('--lang', 'en', '--file', m.archive('0.6.0'));
  assert.match(flagged.stderr, /^Installing OpenViking SuperAgent vika-0\.6\.0-/);
  assert.equal(fs.readFileSync(path.join(m.vika, 'language'), 'utf8'), 'en\n', '--lang is kept');
  const saved = m.install('--file', m.archive('0.6.0'), { LANG: 'zh_CN.UTF-8' });
  assert.match(saved.stderr, /OpenViking SuperAgent 0\.6\.0 is installed already/);
  const wrong = m.install('--lang', 'fr');
  assert.notEqual(wrong.status, 0);
  assert.match(wrong.stderr, /OpenViking SuperAgent install failed: --lang takes zh or en/);
});


test('a legacy home is preserved until explicitly migrated, then both launchers are installed', (t) => {
  const m = machine(t);
  const legacy = path.join(m.home, '.vika');
  fs.mkdirSync(legacy);
  fs.writeFileSync(path.join(legacy, 'config.json'), '{}');
  fs.writeFileSync(path.join(legacy, 'secrets.env'), 'TEST_SECRET=keep-me');
  const archive = m.archive('0.5.0');
  const refused = m.install('--file', archive);
  assert.notEqual(refused.status, 0);
  assert.match(refused.stderr, /Legacy ~\/\.vika installation found/);
  assert.equal(fs.existsSync(m.vika), false);
  assert.equal(fs.readFileSync(path.join(legacy, 'secrets.env'), 'utf8'), 'TEST_SECRET=keep-me');
  fs.renameSync(legacy, m.vika);
  fs.symlinkSync(m.vika, legacy, 'dir');
  const installed = m.install('--file', archive);
  assert.equal(installed.status, 0, installed.stderr);
  assert.equal(fs.readFileSync(path.join(legacy, 'secrets.env'), 'utf8'), 'TEST_SECRET=keep-me');
  for (const command of ['ovsa', 'vika']) assert.ok(fs.existsSync(path.join(m.vika, 'bin', command)));
});
