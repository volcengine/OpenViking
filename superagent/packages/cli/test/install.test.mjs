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
const WINDOWS_INSTALLER = fileURLToPath(new URL('../../../scripts/windows/install-preview.ps1', import.meta.url));
const PLATFORM = `${process.platform === 'android' ? 'linux' : process.platform}-${process.arch}`;
const SH = execFileSync('sh', ['-c', 'command -v sh'], { encoding: 'utf8' }).trim();
const GREP = execFileSync('sh', ['-c', 'command -v grep'], { encoding: 'utf8' }).trim();

test('installer headers use the OpenViking SuperAgent brand', () => {
  for (const installer of [INSTALLER, WINDOWS_INSTALLER]) {
    const source = fs.readFileSync(installer, 'utf8');
    assert.match(source, /Open\$\{?VIOLET\}?Viking .*Super\$\{?SKY\}?Agent/);
    assert.doesNotMatch(source, /\$\{?PINK\}?V\$\{?VIOLET\}?I\$\{?LILAC\}?K\$\{?SKY\}?A/);
  }
});

// A home directory, a fake pm2 that records any call, and a release archive
// laid out like build-release.mjs makes them.
function machine(t, { userspace = 'glibc', arch = process.arch === 'arm64' ? 'aarch64' : 'x86_64', nodeVersion = '22.19.0', nodePlatform = 'android', nodeArch = 'arm64', sqliteWorks = true, larkVersion = 'lark-cli version v1.0.96-2-g1da5768f', larkStatus = 0 } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-install-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const home = path.join(root, 'home');
  const bin = path.join(root, 'bin');
  fs.mkdirSync(home, { recursive: true });
  fs.mkdirSync(bin);
  const script = (name, body) => fs.writeFileSync(path.join(bin, name), `#!${SH}\n${body}\n`, { mode: 0o755 });
  // Use real grep against synthetic shell maps: exercise libc precedence without
  // requiring Android hardware or treating inherited TERMUX_* as ABI evidence.
  const maps = path.join(root, 'maps');
  fs.writeFileSync(maps, userspace === 'bionic' ? '/apex/lib64/bionic/libc.so\n'
    : userspace === 'musl' ? '/lib/ld-musl-aarch64.so.1\n/apex/lib64/bionic/libc.so\n'
      : '/lib/libc.so.6\n/apex/lib64/bionic/libc.so\n');
  script('grep', `case "$*" in */proc/*/maps) exec '${GREP}' "$1" "$2" '${maps}' ;; esac\nexec '${GREP}' "$@"`);
  script('uname', `case "$1" in -s) echo '${process.platform === 'darwin' && userspace === 'glibc' ? 'Darwin' : 'Linux'}' ;; -m) echo '${arch}' ;; esac`);
  script('ldd', `echo '${userspace === 'musl' ? 'musl libc' : 'glibc'}'`);
  if (userspace === 'bionic') {
    fs.symlinkSync(SH, path.join(bin, 'sh'));
    const prelude = `const realProcess=globalThis.process; const process={platform:${JSON.stringify(nodePlatform)},arch:${JSON.stringify(nodeArch)},versions:{node:${JSON.stringify(nodeVersion)}},exit:(n)=>realProcess.exit(n)}; const realRequire=require; require=(id)=>{if(id!=="node:sqlite")return realRequire(id); if(!${sqliteWorks})throw Error("SQLite unavailable"); return {DatabaseSync:class {close(){}}}};`;
    script('node', `echo "$*" >> '${root}/native-node-calls'\nif [ "$1" = -e ]; then exec '${process.execPath}' -e '${prelude}'"$2"; fi\ncase "$1" in */check-ov.mjs) exit "\${TEST_OV_STATUS:-0}" ;; esac\nexit "\${TEST_NODE_STATUS:-0}"`);
    script('lark-cli', `printf '%s\\n' '${larkVersion}'\nexit ${larkStatus}`);
  }
  fs.writeFileSync(path.join(bin, 'pm2'), `#!/bin/sh\necho "$@" >> "${root}/pm2-calls"\n`, { mode: 0o755 });
  const archive = (version, { platform = PLATFORM, nodeWorks = true, ovWorks = true } = {}) => {
    const work = fs.mkdtempSync(path.join(root, 'build-'));
    const directory = path.join(work, `superagent-${version}-${platform}`);
    fs.mkdirSync(path.join(directory, 'node', 'bin'), { recursive: true });
    fs.mkdirSync(path.join(directory, 'packages', 'cli', 'bin'), { recursive: true });
    fs.mkdirSync(path.join(directory, 'bin'), { recursive: true });
    fs.writeFileSync(path.join(directory, 'bin', 'pm2'), '#!/bin/sh\nROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\nexec "$ROOT/node/bin/node" "$ROOT/node_modules/pm2/bin/pm2" "$@"\n', { mode: 0o755 });
    fs.writeFileSync(path.join(directory, 'node', 'bin', 'node'), `#!/bin/sh
echo bundled >> '${root}/bundled-node-calls'
case "$1" in */check-ov.mjs) exit ${ovWorks ? 0 : 1} ;; esac
exit ${nodeWorks ? 0 : 1}
`, { mode: 0o755 });
    fs.writeFileSync(path.join(directory, 'packages', 'cli', 'bin', 'ov-superagent.mjs'), '');
    const file = path.join(root, `superagent-${version}-${platform}.tar.gz`);
    execFileSync('tar', ['-czf', file, '-C', work, path.basename(directory)]);
    fs.writeFileSync(`${file}.sha256`, `${createHash('sha256').update(fs.readFileSync(file)).digest('hex')}  ${path.basename(file)}\n`);
    return file;
  };
  const install = (...args) => {
    const env = typeof args.at(-1) === 'object' ? args.pop() : {};
    return spawnSync('sh', [INSTALLER, '--no-setup', ...args], {
      encoding: 'utf8',
      env: { HOME: home, SHELL: '/bin/bash', PATH: `${bin}:${process.env.PATH}`, TERMUX_VERSION: 'test', PREFIX: root, ...env },
    });
  };
  const superagent = path.join(home, '.ov-superagent');
  return { root, home, bin, superagent, archive, install, pm2Calls: () => fs.existsSync(path.join(root, 'pm2-calls')) };
}

test('native Termux arm64 reuses linux-arm64 with PATH Node, native launchers and no desktop Node execution', (t) => {
  const m = machine(t, { userspace: 'bionic', arch: 'aarch64' });
  const result = m.install('--lang', 'en', '--file', m.archive('0.5.0', { platform: 'linux-arm64', nodeWorks: false }));
  assert.equal(result.status, 0, result.stderr);
  const release = path.join(m.superagent, 'current');
  assert.equal(fs.readlinkSync(path.join(release, 'node/bin/node')), path.join(m.bin, 'node'));
  assert.equal(fs.existsSync(path.join(m.root, 'bundled-node-calls')), false);
  for (const launcher of [path.join(m.superagent, 'bin/ov-superagent'), path.join(release, 'bin/pm2')]) {
    assert.equal(fs.readFileSync(launcher, 'utf8').split('\n')[0], `#!${m.bin}/sh`);
    const run = spawnSync(launcher, ['--version'], { encoding: 'utf8', env: { HOME: m.home, PATH: `${m.bin}:${process.env.PATH}` } });
    assert.equal(run.status, 0, run.stderr);
  }
  const calls = fs.readFileSync(path.join(m.root, 'native-node-calls'), 'utf8');
  assert.match(calls, /check-ov\.mjs/);
  assert.match(calls, /node_modules\/pm2\/bin\/pm2 --version/);
  assert.equal(fs.existsSync(path.join(m.superagent, 'deploy.lock')), false);
});

for (const [name, options, message] of [
  ['old Node', { nodeVersion: '22.18.0' }, /Node >=22\.19/],
  ['older major Node', { nodeVersion: '20.99.0' }, /Node >=22\.19/],
  ['glibc Node on PATH', { nodePlatform: 'linux' }, /process.platform android/],
  ['non-arm64 Node on PATH', { nodeArch: 'x64' }, /process.arch arm64/],
  ['Node without working SQLite', { sqliteWorks: false }, /with node:sqlite is required/],
  ['broken lark-cli', { larkStatus: 1 }, /lark-cli --version failed/],
  ['invalid lark-cli version', { larkVersion: 'not-a-version' }, /invalid version/],
  ['unsupported Android CPU', { arch: 'x86_64' }, /Only native Termux arm64/],
]) {
  test(`native Termux refuses ${name} before activation`, (t) => {
    const m = machine(t, { userspace: 'bionic', arch: 'aarch64', ...options });
    const result = m.install('--file', m.archive('0.5.0', { platform: 'linux-arm64' }));
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, message);
    assert.equal(fs.existsSync(path.join(m.superagent, 'current')), false);
    assert.equal(fs.existsSync(path.join(m.root, 'bundled-node-calls')), false);
  });
}

for (const [binary, message] of [['node', /Node >=22\.19\.0 must be installed on PATH/], ['lark-cli', /lark-cli must be installed on PATH/]]) {
  test(`native Termux requires ${binary} on PATH`, (t) => {
    const m = machine(t, { userspace: 'bionic', arch: 'aarch64' });
    const archive = m.archive('0.5.0', { platform: 'linux-arm64' });
    fs.unlinkSync(path.join(m.bin, binary));
    // No fallback to any Node/lark-cli installed on the test host.
    const result = m.install('--file', archive, { PATH: m.bin });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, message);
    assert.equal(fs.existsSync(path.join(m.superagent, 'current')), false);
  });
}

test('native Termux preserves checksum, deployment lock, runtime and OV activation gates', (t) => {
  const m = machine(t, { userspace: 'bionic', arch: 'aarch64', nodeVersion: '24.0.0' });
  const archive = m.archive('0.5.0', { platform: 'linux-arm64' });
  assert.equal(m.install('--file', archive).status, 0);
  const active = fs.realpathSync(path.join(m.superagent, 'current'));
  const next = m.archive('0.6.0', { platform: 'linux-arm64' });
  for (const [env, message] of [
    [{ TEST_NODE_STATUS: '1' }, /运行不了这个版本/],
    [{ TEST_OV_STATUS: '1' }, /OpenViking 检查未通过/],
  ]) {
    const failed = m.install('--file', next, env);
    assert.notEqual(failed.status, 0);
    assert.match(failed.stderr, message);
    assert.equal(fs.realpathSync(path.join(m.superagent, 'current')), active);
    assert.deepEqual(fs.readdirSync(path.join(m.superagent, 'releases')), ['0.5.0']);
    assert.equal(fs.existsSync(path.join(m.superagent, 'deploy.lock')), false);
  }
  fs.mkdirSync(path.join(m.superagent, 'deploy.lock'));
  assert.match(m.install('--file', next).stderr, /另一个安装或升级正在进行/);
  fs.rmdirSync(path.join(m.superagent, 'deploy.lock'));
  fs.writeFileSync(`${next}.sha256`, `${'0'.repeat(64)}  x\n`);
  assert.match(m.install('--file', next).stderr, /校验失败/);
  assert.equal(fs.realpathSync(path.join(m.superagent, 'current')), active);
  assert.equal(fs.existsSync(path.join(m.root, 'bundled-node-calls')), false);
});

for (const [command, message] of [['ln', /Cannot replace the release Node/], ['cp', /Cannot replace the release pm2 launcher/], ['chmod', /Cannot replace the release pm2 launcher/]]) {
  test(`native Termux cleans pending release when ${command} replacement fails`, (t) => {
    const m = machine(t, { userspace: 'bionic', arch: 'aarch64' });
    assert.equal(m.install('--file', m.archive('0.5.0', { platform: 'linux-arm64' })).status, 0);
    const active = fs.realpathSync(path.join(m.superagent, 'current'));
    const archive = m.archive('0.6.0', { platform: 'linux-arm64' });
    const realCommand = execFileSync('sh', ['-c', `command -v ${command}`], { encoding: 'utf8' }).trim();
    fs.writeFileSync(path.join(m.bin, command), `#!${SH}\ncase "$*" in *releases/.pending-*) exit 1 ;; esac\nexec '${realCommand}' "$@"\n`, { mode: 0o755 });
    const result = m.install('--file', archive);
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, message);
    assert.equal(fs.realpathSync(path.join(m.superagent, 'current')), active);
    assert.deepEqual(fs.readdirSync(path.join(m.superagent, 'releases')), ['0.5.0']);
    assert.equal(fs.existsSync(path.join(m.superagent, 'deploy.lock')), false);
    assert.equal(fs.existsSync(path.join(m.root, 'bundled-node-calls')), false);
  });
}

test('inherited Termux variables and Bionic mappings do not misclassify a glibc guest', (t) => {
  const m = machine(t);
  const result = m.install('--file', m.archive('0.5.0'));
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.lstatSync(path.join(m.superagent, 'current/node/bin/node')).isSymbolicLink(), false);
  assert.equal(fs.readFileSync(path.join(m.superagent, 'bin/ov-superagent'), 'utf8').split('\n')[0], '#!/bin/sh');
  assert.ok(fs.existsSync(path.join(m.root, 'bundled-node-calls')));
});

test('musl guests retain rejection even with Termux variables and Bionic mappings', (t) => {
  const m = machine(t, { userspace: 'musl' });
  const result = m.install('--file', m.archive('0.5.0'));
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /musl/);
});

test('installs a release under ~/.ov-superagent, adds the launcher and PATH, and never touches pm2', (t) => {
  const m = machine(t);
  const bashRc = path.join(m.home, process.platform === 'darwin' ? '.bash_profile' : '.bashrc');
  const first = m.install('--file', m.archive('0.5.0'));
  assert.equal(first.status, 0, first.stderr);
  assert.equal(fs.realpathSync(path.join(m.superagent, 'current')), fs.realpathSync(path.join(m.superagent, 'releases', '0.5.0')));
  const launcher = fs.readFileSync(path.join(m.superagent, 'bin', 'ov-superagent'), 'utf8');
  assert.equal(fs.statSync(path.join(m.superagent, 'bin', 'ov-superagent')).mode & 0o111, 0o111);
  assert.match(launcher, /exec "\$HOME\/\.ov-superagent\/current\/node\/bin\/node" "\$HOME\/\.ov-superagent\/current\/packages\/cli\/bin\/ov-superagent\.mjs" "\$@"/);
  for (const gone of ['vika', 'ovsa']) assert.equal(fs.existsSync(path.join(m.superagent, 'bin', gone)), false);
  assert.match(fs.readFileSync(bashRc, 'utf8'), /export PATH="\$HOME\/\.ov-superagent\/bin:\$PATH"/);
  assert.equal(fs.existsSync(path.join(m.superagent, 'deploy.lock')), false, 'the lock is released');
  assert.match(first.stderr, /下一步：运行 ~\/\.ov-superagent\/bin\/ov-superagent setup/);
  // Without an instance, running the installer again still leads to setup.
  const retried = m.install('--file', m.archive('0.5.0'));
  assert.match(retried.stderr, /已经是 OpenViking SuperAgent 0\.5\.0[\s\S]*下一步：运行 ~\/\.ov-superagent\/bin\/ov-superagent setup/);

  fs.writeFileSync(path.join(m.superagent, 'config.json'), '{}');
  const second = m.install('--file', m.archive('0.6.0'));
  assert.equal(second.status, 0, second.stderr);
  assert.equal(fs.realpathSync(path.join(m.superagent, 'rollback')), fs.realpathSync(path.join(m.superagent, 'releases', '0.5.0')));
  assert.match(second.stderr, /ov-superagent restart/);
  assert.doesNotMatch(second.stderr, /下一步/);
  const again = m.install('--file', m.archive('0.6.0'));
  assert.match(again.stderr, /已经是 OpenViking SuperAgent 0\.6\.0/);
  assert.doesNotMatch(again.stderr, /ov-superagent restart|下一步/);
  assert.equal(fs.readFileSync(bashRc, 'utf8').match(/\.ov-superagent\/bin/g).length, 1, 'PATH is added once');
  assert.equal(m.pm2Calls(), false);
});

test('a shell rc file that cannot be written does not turn a completed install into a failure', (t) => {
  const m = machine(t);
  fs.mkdirSync(path.join(m.home, '.zshrc'));
  const result = m.install('--file', m.archive('0.5.0'), { SHELL: '/bin/zsh' });
  assert.equal(result.status, 0, result.stderr);
  assert.ok(fs.existsSync(path.join(m.superagent, 'bin', 'ov-superagent')));
  assert.match(result.stderr, /写不了 ~\/\.zshrc；OpenViking SuperAgent 已安装完成/);
  assert.match(result.stderr, /export PATH="\$HOME\/\.ov-superagent\/bin:\$PATH"/);
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
  assert.equal(fs.realpathSync(path.join(m.superagent, 'current')), fs.realpathSync(path.join(m.superagent, 'releases', '0.5.0')));
  assert.deepEqual(fs.readdirSync(path.join(m.superagent, 'releases')), ['0.5.0']);
  assert.equal(fs.existsSync(path.join(m.superagent, 'deploy.lock')), false);
});

test('waits for a running upgrade instead of racing it', (t) => {
  const m = machine(t);
  fs.mkdirSync(path.join(m.superagent, 'deploy.lock'), { recursive: true });
  const blocked = m.install('--file', m.archive('0.5.0'));
  assert.notEqual(blocked.status, 0);
  assert.match(blocked.stderr, /另一个安装或升级正在进行/);
  assert.ok(fs.existsSync(path.join(m.superagent, 'deploy.lock')), 'someone else\'s lock is left alone');
});

test('an unavailable OV blocks even --no-setup and preserves the active installation', (t) => {
  const m = machine(t);
  const missing = m.install('--file', m.archive('0.5.0', { ovWorks: false }));
  assert.notEqual(missing.status, 0);
  assert.match(missing.stderr, /OpenViking 检查未通过/);
  assert.equal(fs.existsSync(path.join(m.superagent, 'current')), false);
  assert.equal(fs.existsSync(path.join(m.superagent, 'bin', 'vika')), false);
  assert.equal(m.install('--file', m.archive('0.5.0')).status, 0);
  const active = fs.realpathSync(path.join(m.superagent, 'current'));
  assert.notEqual(m.install('--file', m.archive('0.6.0', { ovWorks: false })).status, 0);
  assert.equal(fs.realpathSync(path.join(m.superagent, 'current')), active);
  assert.deepEqual(fs.readdirSync(path.join(m.superagent, 'releases')), ['0.5.0']);
});

test('no variable runs into non-ASCII text, which macOS /bin/sh would read as part of its name', () => {
  const offending = fs.readFileSync(INSTALLER, 'utf8').split('\n')
    .map((line, index) => [index + 1, line])
    .filter(([, line]) => !line.trimStart().startsWith('#') && /\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7F]/.test(line));
  assert.deepEqual(offending, []);
});

test('the installer speaks the language the ov-superagent command would: --lang or OV_SUPERAGENT_LANG, the saved one, then Chinese', (t) => {
  const m = machine(t);
  const english = m.install('--file', m.archive('0.5.0'), { LANG: 'en_US.UTF-8', OV_SUPERAGENT_LANG: 'en' });
  assert.equal(english.status, 0, english.stderr);
  assert.match(english.stderr, /^Installing OpenViking SuperAgent superagent-0\.5\.0-[\s\S]*Verify +SHA-256 matches[\s\S]*next: run ~\/\.ov-superagent\/bin\/ov-superagent setup\n$/);
  assert.doesNotMatch(english.stderr, /[一-鿿]/);
  const flagged = m.install('--lang', 'en', '--file', m.archive('0.6.0'));
  assert.match(flagged.stderr, /^Installing OpenViking SuperAgent superagent-0\.6\.0-/);
  assert.equal(fs.readFileSync(path.join(m.superagent, 'language'), 'utf8'), 'en\n', '--lang is kept');
  const saved = m.install('--file', m.archive('0.6.0'), { LANG: 'zh_CN.UTF-8' });
  assert.match(saved.stderr, /OpenViking SuperAgent 0\.6\.0 is installed already/);
  const wrong = m.install('--lang', 'fr');
  assert.notEqual(wrong.status, 0);
  assert.match(wrong.stderr, /OpenViking SuperAgent install failed: --lang takes zh or en/);
});


test('a legacy home is left untouched and does not block a fresh install', (t) => {
  const m = machine(t);
  for (const name of ['.vika', '.ovsa', '.superagent']) {
    const legacy = path.join(m.home, name);
    fs.mkdirSync(legacy);
    fs.writeFileSync(path.join(legacy, 'config.json'), '{}');
    fs.writeFileSync(path.join(legacy, 'secrets.env'), 'TEST_SECRET=keep-me');
  }
  const installed = m.install('--file', m.archive('0.5.0'));
  assert.equal(installed.status, 0, installed.stderr);
  assert.match(installed.stderr, /旧版安装 ~\/\.vika/);
  assert.ok(fs.existsSync(path.join(m.superagent, 'current')));
  assert.equal(fs.existsSync(path.join(m.superagent, 'config.json')), false, 'nothing is migrated');
  for (const name of ['.vika', '.ovsa', '.superagent']) {
    assert.equal(fs.readFileSync(path.join(m.home, name, 'secrets.env'), 'utf8'), 'TEST_SECRET=keep-me');
  }
  assert.equal(m.pm2Calls(), false, 'no legacy pm2 home, so pm2 is not run');
  assert.deepEqual(fs.readdirSync(path.join(m.superagent, 'bin')), ['ov-superagent']);
});

// The legacy pm2, systemctl and launchctl are fakes that only record calls.
function legacyMachine(t) {
  const m = machine(t);
  const calls = path.join(m.root, 'legacy-calls');
  const recorder = (name, jlist = '') => `#!/bin/sh
echo "${name} PM2_HOME=\${PM2_HOME:-} $*" >> "${calls}"
${jlist ? `[ "$1" = jlist ] && printf '%s' '${jlist}'` : ''}
exit 0
`;
  const bin = path.join(m.root, 'bin');
  fs.writeFileSync(path.join(bin, 'systemctl'), recorder('systemctl'), { mode: 0o755 });
  fs.writeFileSync(path.join(bin, 'launchctl'), recorder('launchctl'), { mode: 0o755 });
  const legacy = path.join(m.home, '.vika');
  const release = path.join(legacy, 'releases', '0.4.0', 'bin');
  fs.mkdirSync(release, { recursive: true });
  fs.mkdirSync(path.join(legacy, 'pm2'));
  fs.writeFileSync(path.join(legacy, 'config.json'), '{"keep":true}');
  const jlist = JSON.stringify([
    { name: 'vika-primary', pm2_env: { name: 'vika-primary' } },
    { name: 'vika-web' }, { name: 'vika-updater' }, { name: 'vika-alpha' }, { name: 'unrelated' },
  ]);
  fs.writeFileSync(path.join(release, 'pm2'), recorder('legacy-pm2', jlist), { mode: 0o755 });
  const read = () => (fs.existsSync(calls) ? fs.readFileSync(calls, 'utf8') : '');
  return { ...m, legacy, read };
}

test('a legacy install has its pm2 apps deleted and its autostart disabled, its data kept', (t) => {
  const m = legacyMachine(t);
  const units = path.join(m.home, '.config', 'systemd', 'user');
  fs.mkdirSync(units, { recursive: true });
  fs.writeFileSync(path.join(units, 'vika.service'), 'ExecStart=/x/.vika/bin/vika start');
  fs.writeFileSync(path.join(units, 'superagent.service'), 'ExecStart=/x/.ov-superagent/bin/ov-superagent start');
  const agents = path.join(m.home, 'Library', 'LaunchAgents');
  fs.mkdirSync(agents, { recursive: true });
  fs.writeFileSync(path.join(agents, 'com.vika.agent.plist'), '<plist/>');
  const installed = m.install('--file', m.archive('0.5.0'));
  assert.equal(installed.status, 0, installed.stderr);
  const calls = m.read();
  assert.match(calls, /systemctl PM2_HOME= --user disable --now vika\.service/);
  assert.doesNotMatch(calls, /superagent\.service/, 'the current unit name is left alone');
  assert.match(calls, /launchctl PM2_HOME= bootout gui\/\d+ .*com\.vika\.agent\.plist/);
  assert.ok(fs.existsSync(path.join(agents, 'com.vika.agent.plist.disabled')));
  const pm2Home = path.join(m.legacy, 'pm2');
  for (const app of ['vika-primary', 'vika-web', 'vika-updater', 'vika-alpha']) {
    assert.ok(calls.includes(`legacy-pm2 PM2_HOME=${pm2Home} delete ${app}\n`), app);
  }
  assert.doesNotMatch(calls, /delete unrelated/);
  assert.equal(calls.match(/delete vika-primary/g).length, 1);
  assert.equal(fs.readFileSync(path.join(m.legacy, 'config.json'), 'utf8'), '{"keep":true}');
  assert.equal(m.pm2Calls(), false, 'the release pm2 is preferred over one on PATH');
});

test('a legacy pm2 failure never fails the install', (t) => {
  const m = legacyMachine(t);
  fs.writeFileSync(path.join(m.legacy, 'releases', '0.4.0', 'bin', 'pm2'), '#!/bin/sh\nexit 3\n', { mode: 0o755 });
  const installed = m.install('--file', m.archive('0.5.0'));
  assert.equal(installed.status, 0, installed.stderr);
  assert.equal(m.read(), '', 'no autostart entries, so nothing else is called');
});

test('without a legacy release pm2, the pm2 on PATH is used against the legacy PM2_HOME', (t) => {
  const m = legacyMachine(t);
  fs.rmSync(path.join(m.legacy, 'releases'), { recursive: true });
  const installed = m.install('--file', m.archive('0.5.0'));
  assert.equal(installed.status, 0, installed.stderr);
  assert.equal(m.pm2Calls(), true);
});
