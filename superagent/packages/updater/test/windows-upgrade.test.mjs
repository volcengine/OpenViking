// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { publishWithInstaller, republishPrevious } from '../src/windows-installer.mjs';

// End-to-end Windows upgrade: the real published installer (scripts/windows/
// install-preview.ps1), a real HTTP release host and real release links. This
// runs on a Windows x64 machine, the only place where junctions and the packaged
// ZIP layout exist; on every other platform the test is skipped.

const installerPath = fileURLToPath(new URL('../../../scripts/windows/install-preview.ps1', import.meta.url));
const VERSION_A = '0.5.5-preview-windows-preview-aaaaaaaaaaaa';
const VERSION_B = '0.5.5-preview-windows-preview-bbbbbbbbbbbb';
const VERSION_C = '0.5.5-preview-windows-preview-cccccccccccc';

function fixturePackage(root, version) {
  fs.mkdirSync(path.join(root, 'runtime'), { recursive: true });
  fs.writeFileSync(path.join(root, 'runtime', 'node.exe'), '');
  // The installer's own package check; a stub keeps this test off a real Node.
  fs.writeFileSync(path.join(root, 'superagent-preview.ps1'), 'param($Action)\r\n');
  fs.writeFileSync(path.join(root, 'windows-preview.json'), `${JSON.stringify({
    name: 'superagent', version, commit: version.slice(-12), platform: 'win32-x64',
    channel: 'windows-preview', builtAt: new Date().toISOString(),
  }, null, 2)}\n`);
}

const quote = (value) => `'${value.replaceAll("'", "''")}'`;

function zipDirectory(source, target) {
  execFileSync('powershell.exe', ['-NoProfile', '-Command',
    'Add-Type -AssemblyName System.IO.Compression.FileSystem; '
    + `[IO.Compression.ZipFile]::CreateFromDirectory(${quote(source)}, ${quote(target)})`], { stdio: 'pipe' });
  return fs.readFileSync(target);
}

test('a Windows install upgrades, rolls back and refuses a corrupt package', { skip: process.platform !== 'win32' }, async (t) => {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-windows-upgrade-'));
  t.after(() => fs.rmSync(work, { recursive: true, force: true }));
  const packages = {};
  for (const version of [VERSION_A, VERSION_B, VERSION_C]) {
    const root = path.join(work, `package-${version}`);
    fixturePackage(root, version);
    packages[version] = zipDirectory(root, path.join(work, `superagent-${version}-win32-x64.zip`));
  }
  const sha256 = (buffer) => createHash('sha256').update(buffer).digest('hex');
  let latest = VERSION_A;
  let corrupt = false;
  const requests = [];
  const server = http.createServer((request, response) => {
    requests.push(request.url);
    const match = request.url.match(/^\/windows-preview\/([^/]+)\/(.+)$/);
    if (request.url === '/windows-preview/latest/version') return response.end(latest);
    if (!match) { response.statusCode = 404; return response.end('not found'); }
    const version = decodeURIComponent(match[1]);
    const file = match[2];
    const archive = packages[version];
    if (!archive) { response.statusCode = 404; return response.end('not found'); }
    if (file.endsWith('.sha256')) {
      return response.end(`${corrupt ? '0'.repeat(64) : sha256(archive)}  superagent-${version}-win32-x64.zip\n`);
    }
    return response.end(archive);
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));

  const homeDir = path.join(work, '.ov-superagent');
  const env = {
    ...process.env,
    HOME: work,
    USERPROFILE: work,
    // Also keeps the installer off the runner's real user PATH.
    OV_SUPERAGENT_HOME: homeDir,
    OV_SUPERAGENT_DOWNLOAD_BASE: `http://127.0.0.1:${server.address().port}`,
    TEMP: work,
    TMP: work,
    OV_SUPERAGENT_LANG: 'en',
    NO_COLOR: '1',
  };
  const current = () => fs.realpathSync(path.join(homeDir, 'current'));
  const metadata = (version) => JSON.parse(fs.readFileSync(path.join(homeDir, 'releases', version, 'windows-preview.json'), 'utf8'));

  const first = await publishWithInstaller({ baseDir: homeDir, env, installerPath, platformId: 'win32-x64' });
  assert.equal(first.version, VERSION_A);
  assert.equal(first.published, true);
  assert.equal(first.previousRelease, null);
  assert.equal(current(), fs.realpathSync(path.join(homeDir, 'releases', VERSION_A)));
  assert.equal(metadata(VERSION_A).version, VERSION_A);

  latest = VERSION_B;
  const second = await publishWithInstaller({ baseDir: homeDir, env, installerPath, platformId: 'win32-x64' });
  assert.equal(second.version, VERSION_B);
  assert.equal(second.published, true);
  assert.equal(second.previousVersion, VERSION_A);
  assert.equal(current(), fs.realpathSync(path.join(homeDir, 'releases', VERSION_B)));
  assert.equal(fs.realpathSync(path.join(homeDir, 'rollback')), fs.realpathSync(path.join(homeDir, 'releases', VERSION_A)));

  // A package whose SHA-256 does not match must leave the install untouched.
  latest = VERSION_C;
  corrupt = true;
  await assert.rejects(
    publishWithInstaller({ baseDir: homeDir, env, installerPath, platformId: 'win32-x64' }),
    /SHA-256/,
  );
  assert.equal(current(), fs.realpathSync(path.join(homeDir, 'releases', VERSION_B)));

  // The rollback the updater performs after a release that installed but could
  // not start: pin the previous version and read `current` back.
  corrupt = false;
  const reverted = await republishPrevious({
    baseDir: homeDir, env, installerPath, platformId: 'win32-x64', previousVersion: VERSION_A,
  });
  assert.equal(reverted.version, VERSION_A);
  assert.equal(current(), fs.realpathSync(path.join(homeDir, 'releases', VERSION_A)));
  assert.ok(requests.some((url) => url === `/windows-preview/${VERSION_B}/superagent-${VERSION_B}-win32-x64.zip`));
});
