// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { execFile as execFileCallback } from 'node:child_process';
import { mkdir, mkdtemp, readFile, readdir, realpath, rename, rm, stat, symlink } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import {
  assertSha256, assertVersion, download, downloadBase, fetchText, hostPlatform, releaseUrls,
} from './artifacts.mjs';

// Installs the latest published release: download the archive CI built for
// this platform, check its SHA-256 against the release manifest, unpack it
// next to the other releases, smoke-test it with its own Node, then move the
// `current` link (the previous release becomes `rollback`). Nothing is
// compiled, installed from npm, or tested on this machine.

const execFile = promisify(execFileCallback);
export const RELEASE_METADATA = 'release.json';

function isInside(parent, child) {
  const relative = path.relative(parent, child);
  return relative !== '' && !relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative);
}

async function currentRelease(baseDir) {
  // `baseDir` may be reached through a symlink (for example /home ->
  // /srv/home); compare canonical paths.
  const releasesRoot = await realpath(path.join(baseDir, 'releases')).catch((error) => {
    if (error?.code === 'ENOENT') return path.resolve(baseDir, 'releases');
    throw error;
  });
  const release = await realpath(path.join(baseDir, 'current')).catch((error) => {
    if (error?.code === 'ENOENT') return null;
    throw error;
  });
  if (!release) return null;
  if (!isInside(releasesRoot, release)) throw new Error(`current release must resolve inside ${releasesRoot}`);
  return release;
}

export async function readReleaseMetadata(release) {
  if (!release) return null;
  try {
    return JSON.parse(await readFile(path.join(release, RELEASE_METADATA), 'utf8'));
  } catch (error) {
    if (error?.code === 'ENOENT' || error instanceof SyntaxError) return null;
    throw error;
  }
}

async function atomicSymlink(target, linkPath) {
  const temporary = `${linkPath}.pending-${process.pid}-${Date.now()}`;
  await symlink(target, temporary);
  try {
    await rename(temporary, linkPath);
  } catch (error) {
    await rm(temporary, { force: true });
    throw error;
  }
}

// The release runs on the Node it ships: check that Node starts on this
// machine (glibc, CPU) and that the managed Pi is the pinned version.
export async function defaultSmokeTest(release, { execFileImpl = execFile } = {}) {
  const node = path.join(release, 'node', 'bin', 'node');
  await execFileImpl(node, [path.join(release, 'packages', 'daemon', 'scripts', 'check-pi.mjs')], {
    cwd: release, timeout: 60_000,
  });
  await execFileImpl(node, [path.join(release, 'packages', 'cli', 'bin', 'vika.mjs'), '--version'], {
    cwd: release, timeout: 30_000,
  });
}

// Unpacks `archive` into releasesRoot/<version>; the archive holds one
// directory whose release.json names that version and platform.
export async function unpackRelease(archive, { releasesRoot, version, platformId, keep = null, tarBin = 'tar', execFileImpl = execFile }) {
  const target = path.join(releasesRoot, version);
  // A running release is never replaced in place, even one without metadata.
  if (keep && path.resolve(keep) === path.resolve(target)) {
    throw new Error(`release ${version} is the one running; it cannot be reinstalled over itself`);
  }
  const pending = await mkdtemp(path.join(releasesRoot, '.pending-'));
  try {
    await execFileImpl(tarBin, ['-xzf', archive, '-C', pending], { timeout: 300_000 });
    const entries = await readdir(pending);
    if (entries.length !== 1) throw new Error('release archive must contain exactly one directory');
    const unpacked = path.join(pending, entries[0]);
    const metadata = await readReleaseMetadata(unpacked);
    if (metadata?.version !== version || metadata?.platform !== platformId) {
      throw new Error(`release archive is ${metadata?.version}/${metadata?.platform}, expected ${version}/${platformId}`);
    }
    await rm(target, { recursive: true, force: true });
    await rename(unpacked, target);
    return target;
  } finally {
    await rm(pending, { recursive: true, force: true });
  }
}

export async function activateRelease(baseDir, release, previousRelease) {
  if (previousRelease && previousRelease !== release) {
    await atomicSymlink(previousRelease, path.join(baseDir, 'rollback'));
  }
  await atomicSymlink(release, path.join(baseDir, 'current'));
}

export async function publishLatestRelease({
  baseDir = path.join(os.homedir(), '.vika'),
  env = process.env,
  platformId = hostPlatform(),
  fetchImpl = globalThis.fetch,
  tarBin = env.TAR_BIN || 'tar',
  execFileImpl = execFile,
  smokeTestImpl = defaultSmokeTest,
} = {}) {
  const previousRelease = await currentRelease(baseDir);
  const previousMetadata = await readReleaseMetadata(previousRelease);
  const base = downloadBase(env, previousMetadata);
  const version = assertVersion((await fetchText(`${base}/latest`, { fetchImpl })).trim());
  if (previousMetadata?.version === version && previousMetadata?.platform === platformId) {
    return { release: previousRelease, version, commit: previousMetadata.commit, published: false, previousRelease };
  }

  const urls = releaseUrls(base, version, platformId);
  const manifest = JSON.parse(await fetchText(urls.manifest, { fetchImpl }));
  const file = manifest?.files?.[platformId];
  if (manifest?.version !== version || !file?.sha256) throw new Error(`release ${version} has no build for ${platformId}`);

  const releasesRoot = path.join(baseDir, 'releases');
  await mkdir(releasesRoot, { recursive: true, mode: 0o700 });
  const archive = path.join(releasesRoot, `.download-${process.pid}-${Date.now()}.tar.gz`);
  let release = null;
  try {
    await download(urls.artifact, archive, { fetchImpl });
    assertSha256(archive, file.sha256);
    if (Number.isInteger(file.size) && (await stat(archive)).size !== file.size) {
      throw new Error(`release archive size mismatch for ${version}`);
    }
    release = await unpackRelease(archive, { releasesRoot, version, platformId, keep: previousRelease, tarBin, execFileImpl });
    await smokeTestImpl(release, { execFileImpl });
  } catch (error) {
    // Never leave a half-checked release where `current` could later point.
    if (release && release !== previousRelease) await rm(release, { recursive: true, force: true });
    throw error;
  } finally {
    await rm(archive, { force: true });
  }
  await activateRelease(baseDir, release, previousRelease);
  return { release, version, commit: manifest.commit, published: true, previousRelease };
}
