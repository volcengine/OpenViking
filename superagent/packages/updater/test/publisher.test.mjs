// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { publishLatestRelease } from '../src/publisher.mjs';

const BASE = 'https://releases.example/vika';

// A bucket with real archives built the way build-release.mjs lays them out.
function bucket(t) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'vika-publisher-')));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const objects = new Map();
  const requests = [];
  const publish = (version, { platform = 'linux-x64', corrupt = false, inner = {} } = {}) => {
    const work = path.join(root, 'build', version);
    const directory = path.join(work, `vika-${version}-${platform}`);
    fs.mkdirSync(path.join(directory, 'node', 'bin'), { recursive: true });
    fs.writeFileSync(path.join(directory, 'ecosystem.config.cjs'), 'module.exports = {};\n');
    fs.writeFileSync(path.join(directory, 'release.json'), JSON.stringify({ version, platform, commit: `c-${version}`, ...inner }));
    const archive = path.join(work, 'archive.tar.gz');
    execFileSync('tar', ['-czf', archive, '-C', work, path.basename(directory)]);
    const bytes = fs.readFileSync(archive);
    const sha256 = createHash('sha256').update(corrupt ? 'other' : bytes).digest('hex');
    objects.set(`${BASE}/releases/${version}/vika-${version}-linux-x64.tar.gz`, bytes);
    objects.set(`${BASE}/releases/${version}/release.json`, Buffer.from(JSON.stringify({
      version, commit: `c-${version}`, files: { 'linux-x64': { name: `vika-${version}-linux-x64.tar.gz`, sha256, size: bytes.length } },
    })));
    objects.set(`${BASE}/latest`, Buffer.from(`${version}\n`));
  };
  const fetchImpl = async (url, init = {}) => {
    requests.push({ url, headers: init.headers ?? {} });
    const body = objects.get(url);
    return body ? new Response(body) : new Response('missing', { status: 404 });
  };
  const baseDir = path.join(root, 'home', '.vika');
  fs.mkdirSync(baseDir, { recursive: true });
  const smoked = [];
  const options = {
    baseDir, env: { VIKA_DOWNLOAD_BASE: BASE }, platformId: 'linux-x64', fetchImpl,
    smokeTestImpl: async (release) => { smoked.push(release); },
  };
  const current = () => fs.realpathSync(path.join(baseDir, 'current'));
  return { root, baseDir, publish, options, requests, smoked, current };
}

test('installs the latest release, then reuses it until a newer one is published', async (t) => {
  const b = bucket(t);
  b.publish('0.5.0');
  const first = await publishLatestRelease(b.options);
  assert.equal(first.published, true);
  assert.equal(first.version, '0.5.0');
  assert.equal(first.commit, 'c-0.5.0');
  assert.equal(b.current(), path.join(b.baseDir, 'releases', '0.5.0'));
  assert.deepEqual(b.smoked, [first.release]);
  assert.deepEqual(fs.readdirSync(path.join(b.baseDir, 'releases')), ['0.5.0'], 'no download or pending directory is left');

  const again = await publishLatestRelease(b.options);
  assert.equal(again.published, false);
  assert.equal(b.requests.filter(({ url }) => url.endsWith('.tar.gz')).length, 1, 'the same version is not downloaded twice');

  b.publish('0.6.0');
  const next = await publishLatestRelease(b.options);
  assert.equal(next.published, true);
  assert.equal(b.current(), path.join(b.baseDir, 'releases', '0.6.0'));
  assert.equal(fs.realpathSync(path.join(b.baseDir, 'rollback')), first.release);
});

test('a release that fails its checksum or smoke test never becomes current', async (t) => {
  const b = bucket(t);
  b.publish('0.5.0');
  const good = await publishLatestRelease(b.options);

  b.publish('0.6.0', { corrupt: true });
  await assert.rejects(publishLatestRelease(b.options), /SHA-256 mismatch/);
  assert.equal(b.current(), good.release);

  b.publish('0.6.1');
  await assert.rejects(publishLatestRelease({ ...b.options, smokeTestImpl: async () => { throw new Error('node: GLIBC_2.28 not found'); } }),
    /GLIBC/);
  assert.equal(b.current(), good.release);
  assert.deepEqual(fs.readdirSync(path.join(b.baseDir, 'releases')), ['0.5.0']);

  b.publish('0.6.2', { inner: { platform: 'darwin-arm64' } });
  await assert.rejects(publishLatestRelease(b.options), /expected 0\.6\.2\/linux-x64/);
  assert.equal(b.current(), good.release);
});

test('the download base comes from the installed release', async (t) => {
  const b = bucket(t);
  b.publish('0.5.0', { inner: { downloadBase: BASE } });
  await publishLatestRelease(b.options);
  b.publish('0.6.0');
  const result = await publishLatestRelease({ ...b.options, env: {} });
  assert.equal(result.version, '0.6.0');

  const empty = bucket(t);
  await assert.rejects(publishLatestRelease({ ...empty.options, env: {} }), /set VIKA_DOWNLOAD_BASE/);
});

test('a running release without metadata is never reinstalled over itself', async (t) => {
  const b = bucket(t);
  const running = path.join(b.baseDir, 'releases', '0.5.0');
  fs.mkdirSync(running, { recursive: true });
  fs.symlinkSync(running, path.join(b.baseDir, 'current'));
  b.publish('0.5.0');
  await assert.rejects(publishLatestRelease(b.options), /cannot be reinstalled over itself/);
  assert.ok(fs.existsSync(running));
});
