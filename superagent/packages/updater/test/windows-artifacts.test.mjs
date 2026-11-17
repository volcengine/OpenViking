// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and the OpenViking contributors.
// SPDX-License-Identifier: AGPL-3.0
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { installLarkCliBinary } from '../../../scripts/release/lark-cli.mjs';
import { PLATFORMS, platform } from '../src/artifacts.mjs';

test('Windows source dependency uses the checksum-verified zip/exe path, never a release build', async t => {
  assert.equal(platform('win32-x64').sourceOnly, true);
  assert.deepEqual(Object.keys(PLATFORMS).filter(id => !PLATFORMS[id].sourceOnly), ['termux-arm64', 'linux-x64', 'linux-arm64', 'darwin-x64', 'darwin-arm64']);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-artifact-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const archive = 'lark-cli-1.0.94-windows-amd64.zip';
  const body = Buffer.from('test archive payload');
  const hash = createHash('sha256').update(body).digest('hex');
  fs.writeFileSync(path.join(root, 'package.json'), JSON.stringify({ version: '1.0.94' }));
  fs.writeFileSync(path.join(root, 'checksums.txt'), `${hash}  ${archive}\n`);
  const requests = [];
  let extracted = 0;
  const options = {
    packageDir: root, platformId: 'win32-x64', mirror: 'https://fixture.invalid',
    fetchImpl: async url => { requests.push(url); return new Response(body); },
    execFileSyncImpl(command, args) {
      extracted++;
      assert.equal(command, 'tar');
      assert.equal(args[0], '-xf');
      assert.equal(path.basename(args[1]), archive);
      assert.equal(args[2], '-C');
      assert.equal(args[4], 'lark-cli.exe');
      assert.deepEqual(fs.readFileSync(args[1]), body);
      fs.writeFileSync(path.join(args[3], 'lark-cli.exe'), 'binary fixture');
    },
  };
  const result = await installLarkCliBinary(options);
  assert.equal(path.basename(result.bin), 'lark-cli.exe');
  assert.equal(fs.readFileSync(result.bin, 'utf8'), 'binary fixture');
  assert.equal(requests.length, 1);
  assert.ok(requests[0].endsWith(archive));
  assert.equal(extracted, 1);
  await assert.rejects(installLarkCliBinary({ ...options, fetchImpl: async () => new Response('corrupt') }), /SHA-256 mismatch/);
  assert.equal(extracted, 1, 'invalid archive must not be extracted');
  const build = spawnSync(process.execPath, [fileURLToPath(new URL('../../../scripts/build-release.mjs', import.meta.url)), '--platform', 'win32-x64'], { encoding: 'utf8' });
  assert.notEqual(build.status, 0);
  assert.match(build.stderr, /Release builds are not supported/);
});

test('Termux prebuild pins native Go source without weakening the desktop npm dependency', async t => {
  assert.equal(platform('termux-arm64').systemNode, true);
  const manifest = JSON.parse(fs.readFileSync(new URL('../../daemon/package.json', import.meta.url), 'utf8'));
  const pin = JSON.parse(fs.readFileSync(new URL('../../../scripts/release/lark-cli-source.json', import.meta.url), 'utf8'));
  assert.equal(manifest.dependencies['@larksuite/cli'], pin.version);
  assert.equal(manifest.optionalDependencies?.['@larksuite/cli'], undefined);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-termux-artifact-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.writeFileSync(path.join(root, 'package.json'), JSON.stringify({ version: pin.version }));
  const ndk = path.join(root, 'ndk');
  const compiler = path.join(ndk, 'toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android24-clang');
  fs.mkdirSync(path.dirname(compiler), { recursive: true });
  fs.writeFileSync(compiler, '', { mode: 0o755 });
  fs.writeFileSync(path.join(ndk, 'source.properties'), `Pkg.Revision = ${pin.ndk}\n`);
  const previousNdk = process.env.OV_SUPERAGENT_ANDROID_NDK;
  process.env.OV_SUPERAGENT_ANDROID_NDK = ndk;
  t.after(() => { if (previousNdk === undefined) delete process.env.OV_SUPERAGENT_ANDROID_NDK; else process.env.OV_SUPERAGENT_ANDROID_NDK = previousNdk; });
  let compiled = false;
  const options = { packageDir: root, platformId: 'termux-arm64',
    execFileSyncImpl(command, args, settings) {
      if (command === 'git' && args[0] === 'checkout') fs.writeFileSync(path.join(settings.cwd, 'LICENSE'), 'upstream MIT license');
      if (command === 'git' && args[0] === 'fetch') assert.equal(args.at(-1), pin.commit);
      if (command === 'git' && args[0] === 'rev-parse') return `${pin.commit}\n`;
      if (command === 'go' && args[0] === 'version') return 'go version fixture';
      if (command === 'go' && args[0] === 'build') {
        assert.equal(settings.env.GOOS, 'android');
        assert.equal(settings.env.GOARCH, 'arm64');
        assert.equal(settings.env.CGO_ENABLED, '1');
        assert.equal(settings.env.CC, compiler);
        assert.ok(args.includes('-buildmode=pie'));
        fs.writeFileSync(args[args.indexOf('-o') + 1], 'native Bionic binary');
        compiled = true;
      }
      return '';
    } };
  const result = await installLarkCliBinary(options);
  assert.ok(compiled);
  assert.equal(result.commit, pin.commit);
  assert.equal(fs.readFileSync(path.join(root, 'LICENSE'), 'utf8'), 'upstream MIT license');
  const metadata = JSON.parse(fs.readFileSync(path.join(root, 'termux-build.json'), 'utf8'));
  assert.equal(metadata.commit, pin.commit);
  assert.equal(metadata.ndk, pin.ndk);
  assert.equal(metadata.cgo, true);
  fs.writeFileSync(path.join(root, 'package.json'), JSON.stringify({ version: '0.0.0' }));
  await assert.rejects(installLarkCliBinary(options), /source pin/);
});
