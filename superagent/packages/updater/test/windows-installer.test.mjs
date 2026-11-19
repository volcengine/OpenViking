// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { mkdir, realpath, symlink, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { publishWithInstaller, republishPrevious } from '../src/windows-installer.mjs';
import { readReleaseMetadata } from '../src/publisher.mjs';
import { updaterMode } from '../src/artifacts.mjs';

// These run wherever the suite runs: the installer is simulated by an injected
// execFile that does what the real PowerShell installer does (unpack the new
// release, move `current`, keep `rollback`), so the orchestration around it and
// the reported result are covered on every platform. The real installer behind
// this seam has its own Windows tests (scripts/windows/test-install-*.ps1).

const VERSION_A = '0.5.5-preview-windows-preview-aaaaaaaaaaaa';
const VERSION_B = '0.5.5-preview-windows-preview-bbbbbbbbbbbb';
const COMMIT_B = 'b'.repeat(40);

async function fixture(version = VERSION_A) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-windows-installer-'));
  const baseDir = path.join(root, '.ov-superagent');
  const releasesRoot = path.join(baseDir, 'releases');
  const release = path.join(releasesRoot, version);
  await mkdir(release, { recursive: true });
  await writeFile(path.join(release, 'windows-preview.json'), `${JSON.stringify({
    name: 'superagent', version, commit: 'a'.repeat(40), platform: 'win32-x64', channel: 'windows-preview',
  })}\n`);
  await symlink(release, path.join(baseDir, 'current'));
  const installerPath = path.join(root, 'release', 'scripts', 'windows', 'install-preview.ps1');
  await mkdir(path.dirname(installerPath), { recursive: true });
  await writeFile(installerPath, '# fixture installer\n');
  return { root, baseDir, releasesRoot, release, installerPath };
}

// Stands in for install-preview.ps1: writes the release it "downloaded" and
// switches `current`, exactly as Switch-ReleaseLinks ends up doing.
function installerStub({ baseDir, releasesRoot, calls, installVersion, fail = null, platform = 'win32-x64' }) {
  return async (command, args, options) => {
    calls.push({ command, args, options });
    if (fail) throw Object.assign(new Error('PowerShell exited with code 1'), { stderr: `${fail}\n` });
    const previous = await realpath(path.join(baseDir, 'current')).catch(() => null);
    const release = path.join(releasesRoot, installVersion);
    if (!fs.existsSync(release)) {
      await mkdir(release, { recursive: true });
      await writeFile(path.join(release, 'windows-preview.json'), `${JSON.stringify({
        name: 'superagent', version: installVersion, commit: COMMIT_B, platform, channel: 'windows-preview',
      })}\n`);
    }
    if (previous && previous !== release) await symlink(previous, path.join(baseDir, 'rollback.next'));
    fs.rmSync(path.join(baseDir, 'current'), { force: true });
    await symlink(release, path.join(baseDir, 'current'));
    if (previous && previous !== release) {
      fs.rmSync(path.join(baseDir, 'rollback'), { force: true });
      fs.renameSync(path.join(baseDir, 'rollback.next'), path.join(baseDir, 'rollback'));
    }
    return { stdout: '', stderr: '' };
  };
}

test('the updater hands the Windows publication to the shipped installer', async (t) => {
  const { root, baseDir, releasesRoot, installerPath } = await fixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const calls = [];
  const publish = await publishWithInstaller({
    baseDir,
    installerPath,
    platformId: 'win32-x64',
    powershell: 'powershell.exe',
    execFileImpl: installerStub({ baseDir, releasesRoot, calls, installVersion: VERSION_B }),
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].command, 'powershell.exe');
  assert.deepEqual(calls[0].args, [
    '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
    '-File', installerPath, '-NoSetup',
  ]);
  assert.equal(calls[0].options.windowsHide, true);
  assert.equal(publish.release, path.join(releasesRoot, VERSION_B));
  assert.equal(publish.version, VERSION_B);
  assert.equal(publish.commit, COMMIT_B);
  assert.equal(publish.published, true);
  assert.equal(publish.previousRelease, path.join(releasesRoot, VERSION_A));
  assert.equal(publish.previousVersion, VERSION_A);
});

test('a pinned publication asks the installer for that version', async (t) => {
  const { root, baseDir, releasesRoot, installerPath } = await fixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const calls = [];
  const publish = await republishPrevious({
    baseDir,
    installerPath,
    platformId: 'win32-x64',
    previousVersion: VERSION_A,
    execFileImpl: installerStub({ baseDir, releasesRoot, calls, installVersion: VERSION_A }),
  });
  assert.deepEqual(calls[0].args.slice(-2), ['-Version', VERSION_A]);
  assert.equal(publish.version, VERSION_A);
  assert.equal(publish.published, false, 'a same-version install leaves current where it was');
  await assert.rejects(republishPrevious({ baseDir, installerPath, previousVersion: null }), /no previous version/);
});

test('a failed install reports what the installer said and changes nothing', async (t) => {
  const { root, baseDir, releasesRoot, release, installerPath } = await fixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const calls = [];
  await assert.rejects(
    publishWithInstaller({
      baseDir,
      installerPath,
      platformId: 'win32-x64',
      execFileImpl: installerStub({
        baseDir, releasesRoot, calls, installVersion: VERSION_B, fail: 'the package failed verification (SHA-256 mismatch)',
      }),
    }),
    (error) => {
      assert.match(error.message, /could not install the latest release/);
      assert.match(error.message, /SHA-256 mismatch/);
      return true;
    },
  );
  assert.equal(await realpath(path.join(baseDir, 'current')), await realpath(release));
  assert.equal((await readReleaseMetadata(release)).version, VERSION_A);
});

test('a package for another platform is not accepted as this machine\'s release', async (t) => {
  const { root, baseDir, releasesRoot, installerPath } = await fixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const calls = [];
  await assert.rejects(
    publishWithInstaller({
      baseDir,
      installerPath,
      platformId: 'win32-x64',
      execFileImpl: installerStub({ baseDir, releasesRoot, calls, installVersion: VERSION_B, platform: 'win32-arm64' }),
    }),
    /published .* for win32-arm64, not win32-x64/,
  );
});

test('without the shipped installer the updater says so instead of guessing', async (t) => {
  const { root, baseDir } = await fixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  await assert.rejects(
    publishWithInstaller({
      baseDir,
      installerPath: path.join(root, 'missing', 'install-preview.ps1'),
      platformId: 'win32-x64',
      execFileImpl: async () => { throw new Error('must not run'); },
    }),
    /has no Windows installer/,
  );
});

test('release metadata is read from the Windows preview package too', async (t) => {
  const { root, release } = await fixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const metadata = await readReleaseMetadata(release);
  assert.equal(metadata.version, VERSION_A);
  assert.equal(metadata.platform, 'win32-x64');
});

test('each published platform names how its builds reach a machine', () => {
  assert.equal(updaterMode('win32-x64'), 'installer');
  assert.equal(updaterMode('linux-x64'), 'release');
  assert.equal(updaterMode('darwin-arm64'), 'release');
  // Windows on ARM is not a published build, so it has no upgrade path at all.
  assert.throws(() => updaterMode('win32-arm64'), /no automatic upgrade path/);
});
