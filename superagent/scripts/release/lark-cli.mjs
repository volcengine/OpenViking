// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
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

export async function installLarkCliBinary({ packageDir, platformId, fetchImpl = globalThis.fetch, mirror = process.env.OV_SUPERAGENT_LARK_CLI_MIRROR || MIRROR, execFileSyncImpl = execFileSync }) {
  const { version } = JSON.parse(fs.readFileSync(path.join(packageDir, 'package.json'), 'utf8'));
  const target = platform(platformId);
  const windows = target.os === 'win32';
  const executable = windows ? 'lark-cli.exe' : 'lark-cli';
  const archive = `lark-cli-${version}-${target.larkCli}.${windows ? 'zip' : 'tar.gz'}`;
  const expected = checksumFor(fs.readFileSync(path.join(packageDir, 'checksums.txt'), 'utf8'), archive);
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-lark-cli-'));
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
