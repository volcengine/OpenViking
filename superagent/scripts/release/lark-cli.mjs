// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and the OpenViking contributors.
// SPDX-License-Identifier: AGPL-3.0

import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { assertSha256, checksumFor, download, platform } from '../../packages/updater/src/artifacts.mjs';

// @larksuite/cli is a thin npm wrapper around a Go binary that its postinstall
// downloads for the machine running npm. A release needs the binary for its
// target platform instead, checked against the checksums.txt the npm package
// ships, so this fetches it directly (npm runs with --ignore-scripts).

const MIRROR = 'https://registry.npmmirror.com';

function buildTermuxBinary({ packageDir, version, arch, execFileSyncImpl }) {
  // The npm wrapper remains mandatory on desktop. Its version must agree with
  // this immutable Go source pin before CI can produce a native Termux binary.
  const pin = JSON.parse(fs.readFileSync(new URL('./lark-cli-source.json', import.meta.url), 'utf8'));
  if (version !== pin.version || !/^[a-f0-9]{40}$/.test(pin.commit)) {
    throw new Error('Update the reviewed Termux lark-cli source pin with the npm version');
  }
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-termux-lark-'));
  try {
    const run = (command, args, extra = {}) => execFileSyncImpl(command, args,
      { cwd: work, stdio: ['ignore', 'pipe', 'inherit'], encoding: 'utf8', ...extra });
    run('git', ['init', '--quiet']);
    run('git', ['fetch', '--quiet', '--depth=1', 'https://github.com/larksuite/cli.git', pin.commit]);
    run('git', ['checkout', '--quiet', '--detach', 'FETCH_HEAD']);
    if (String(run('git', ['rev-parse', 'HEAD'])).trim() !== pin.commit) throw new Error('lark-cli source commit mismatch');
    const bin = path.join(packageDir, 'bin', 'lark-cli');
    fs.mkdirSync(path.dirname(bin), { recursive: true });
    run('go', ['build', '-trimpath', '-ldflags', `-s -w -X github.com/larksuite/cli/internal/build.Version=${version}`,
      '-o', bin, '.'], { env: { ...process.env, GOOS: 'android', GOARCH: arch, CGO_ENABLED: '0', GOTOOLCHAIN: 'local' } });
    fs.chmodSync(bin, 0o755);
    fs.copyFileSync(path.join(work, 'LICENSE'), path.join(packageDir, 'LICENSE'));
    fs.writeFileSync(path.join(packageDir, 'termux-build.json'), `${JSON.stringify({ ...pin, go: String(run('go', ['version'])).trim() }, null, 2)}\n`);
    return { bin, version, commit: pin.commit };
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
  }
}

export async function installLarkCliBinary({ packageDir, platformId, fetchImpl = globalThis.fetch, mirror = process.env.OV_SUPERAGENT_LARK_CLI_MIRROR || MIRROR, execFileSyncImpl = execFileSync }) {
  const { version } = JSON.parse(fs.readFileSync(path.join(packageDir, 'package.json'), 'utf8'));
  const target = platform(platformId);
  if (target.os === 'android') return buildTermuxBinary({ packageDir, version, arch: target.arch, execFileSyncImpl });
  const windows = target.os === 'win32';
  const executable = windows ? 'lark-cli.exe' : 'lark-cli';
  const archive = `lark-cli-${version}-${target.larkCli}.${windows ? 'zip' : 'tar.gz'}`;
  const expected = checksumFor(fs.readFileSync(path.join(packageDir, 'checksums.txt'), 'utf8'), archive);
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-lark-cli-'));
  try {
    const file = path.join(work, archive);
    const urls = [
      `${mirror.replace(/\/$/, '')}/-/binary/lark-cli/v${version}/${archive}`,
      `https://github.com/larksuite/cli/releases/download/v${version}/${archive}`,
    ];
    let lastError;
    for (const url of urls) {
      try {
        await download(url, file, { fetchImpl });
        assertSha256(file, expected);
        lastError = null;
        break;
      } catch (error) {
        lastError = error;
      }
    }
    if (lastError) throw lastError;
    execFileSyncImpl('tar', [windows ? '-xf' : '-xzf', file, '-C', work, executable]);
    const bin = path.join(packageDir, 'bin', executable);
    fs.mkdirSync(path.dirname(bin), { recursive: true });
    fs.copyFileSync(path.join(work, executable), bin);
    fs.chmodSync(bin, 0o755);
    return { bin, version };
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
  }
}
