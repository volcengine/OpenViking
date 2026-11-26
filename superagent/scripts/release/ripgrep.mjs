// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { assertSha256, download, platform } from '../../packages/updater/src/artifacts.mjs';
import { RIPGREP_VERSION } from '../../packages/daemon/src/ripgrep-bin.mjs';

// Digests published by BurntSushi/ripgrep (<asset>.sha256) for the pinned
// 15.2.0 release. Linux takes the static musl builds.
export const RIPGREP_ASSETS = Object.freeze({
  'linux-x64': ['x86_64-unknown-linux-musl', '33e15bcf1624b25cdd2a55813a47a2f95dbe126268203e76aa6a585d1e7b149c'],
  'linux-arm64': ['aarch64-unknown-linux-musl', '800b1e7206afe799dfb5a6901f23147cfaabe0e52210538100f61e86e1740915'],
  'darwin-x64': ['x86_64-apple-darwin', 'af7825fcc69a2afc7a7aea55fc9af90e26421d8f20fe59df32e233c0b8a231c1'],
  'darwin-arm64': ['aarch64-apple-darwin', '3750b2e93f37e0c692657da574d7019a101c0084da05a790c83fd335bad973e4'],
  'win32-x64': ['x86_64-pc-windows-msvc', '71b2fef860abe467217a538ff31de02f5258807c0129f771846f87bd029aafc5'],
});

// Each archive holds ripgrep-<version>-<target>/ with rg (rg.exe on Windows)
// and its licenses. ripgrep is MIT or Unlicense; the release keeps the MIT text.
// Extract only those two entries from a verified archive.
export function extractRipgrepBinary(archive, directory, { target, windows = false }) {
  const stem = `ripgrep-${RIPGREP_VERSION}-${target}`;
  const executable = windows ? 'rg.exe' : 'rg';
  try {
    execFileSync('tar', [windows ? '-xf' : '-xzf', archive, '-C', directory, `${stem}/${executable}`, `${stem}/LICENSE-MIT`], { stdio: 'pipe' });
  } catch {
    throw new Error(`ripgrep archive has no ${stem}/${executable} with its license`);
  }
  return { bin: path.join(directory, stem, executable), license: path.join(directory, stem, 'LICENSE-MIT') };
}

export async function installRipgrepBinary({ root, platformId, fetchImpl = globalThis.fetch }) {
  const asset = RIPGREP_ASSETS[platformId];
  if (!asset) throw new Error(`Unsupported ripgrep platform: ${platformId}`);
  const [target, sha256] = asset;
  const windows = platform(platformId).os === 'win32';
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-ripgrep-install-'));
  try {
    const name = `ripgrep-${RIPGREP_VERSION}-${target}.${windows ? 'zip' : 'tar.gz'}`;
    const archive = path.join(work, name);
    await download(`https://github.com/BurntSushi/ripgrep/releases/download/${RIPGREP_VERSION}/${name}`, archive, { fetchImpl });
    assertSha256(archive, sha256);
    const extracted = extractRipgrepBinary(archive, work, { target, windows });
    const bin = path.join(root, 'bin', windows ? 'rg.exe' : 'rg');
    fs.mkdirSync(path.dirname(bin), { recursive: true });
    // Replace by rename: an agent may be running the previous binary.
    fs.copyFileSync(extracted.bin, `${bin}.tmp`);
    fs.chmodSync(`${bin}.tmp`, 0o755);
    fs.renameSync(`${bin}.tmp`, bin);
    fs.copyFileSync(extracted.license, path.join(root, 'bin', 'rg.LICENSE'));
    return { bin, version: RIPGREP_VERSION };
  } finally { fs.rmSync(work, { recursive: true, force: true }); }
}
