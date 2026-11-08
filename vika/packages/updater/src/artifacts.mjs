// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { hostPlatformId } from '../../daemon/src/platform/index.mjs';

import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';

// What a Vika release is made of and where it is downloaded from, shared by
// the build and publish scripts and the updater.

// The platforms a Vika release ships for, and how Node.js and lark-cli name them.
// Source-only targets may download dependencies but cannot build releases.
// musl (Alpine) remains unsupported.

export const PLATFORMS = Object.freeze(createRequire(import.meta.url)('../artifact-platforms.json'));

export function hostPlatform() {
  const id = hostPlatformId;
  if (!PLATFORMS[id]) throw new Error(`no Vika build for ${id}`);
  return id;
}

export function platform(id) {
  const value = PLATFORMS[id];
  if (!value) throw new Error(`unknown platform ${id}; one of ${Object.keys(PLATFORMS).join(', ')}`);
  return value;
}

export function artifactName(version, id) {
  return `vika-${version}-${id}.tar.gz`;
}

// Every downloaded file is checked against a SHA-256 that came from elsewhere.

export async function download(url, target, { fetchImpl = globalThis.fetch, headers = {} } = {}) {
  const response = await fetchImpl(url, { headers, redirect: 'follow' });
  if (!response.ok || !response.body) throw new Error(`download failed (HTTP ${response.status}): ${url}`);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const temporary = `${target}.${process.pid}.part`;
  await pipeline(Readable.fromWeb(response.body), fs.createWriteStream(temporary));
  fs.renameSync(temporary, target);
  return target;
}

export async function fetchText(url, { fetchImpl = globalThis.fetch, headers = {} } = {}) {
  const response = await fetchImpl(url, { headers, redirect: 'follow' });
  if (!response.ok) throw new Error(`request failed (HTTP ${response.status}): ${url}`);
  return response.text();
}

export function sha256File(filePath) {
  return createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

export function assertSha256(filePath, expected) {
  const actual = sha256File(filePath);
  if (actual !== expected) {
    throw new Error(`SHA-256 mismatch for ${path.basename(filePath)}: expected ${expected}, got ${actual}`);
  }
}

// The entry for one file in a `sha256  name` listing (SHASUMS256.txt, checksums.txt).
export function checksumFor(listing, name) {
  for (const line of String(listing).split('\n')) {
    const [hash, file] = line.trim().split(/\s+/);
    if (file === name && /^[0-9a-f]{64}$/.test(hash)) return hash;
  }
  throw new Error(`no checksum listed for ${name}`);
}

// Where releases are published: VIKA_DOWNLOAD_BASE, else the base the
// installed release was built for (its release.json).
export function downloadBase(env = process.env, releaseMetadata = null) {
  const base = String(env.VIKA_DOWNLOAD_BASE || releaseMetadata?.downloadBase || '').replace(/\/+$/, '');
  if (!/^https?:\/\//.test(base)) throw new Error('no release download base: set VIKA_DOWNLOAD_BASE');
  return base;
}

export function releaseUrls(base, version, platformId) {
  const directory = `${base}/releases/${encodeURIComponent(version)}`;
  return {
    latest: `${base}/latest`,
    manifest: `${directory}/release.json`,
    artifact: `${directory}/${artifactName(version, platformId)}`,
  };
}

export function assertVersion(version) {
  if (typeof version !== 'string' || !/^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$/.test(version)) {
    throw new Error(`release version is not path-safe: ${JSON.stringify(version)}`);
  }
  return version;
}
