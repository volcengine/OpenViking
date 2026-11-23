// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { execFile } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);

// The disk side of task directories (workers scheduler): what a directory
// holds, which of it a package manager or build tool makes again from what is
// kept beside it, and deletion that never leaves a directory half gone where
// a task could start in it.

// Folders a package manager or tool rebuilds from the files kept beside them
// (a lockfile, a manifest, the sources). A directory with `pyvenv.cfg` is a
// Python virtual environment, whatever its name.
const DEPENDENCY_DIRS = new Set([
  'node_modules', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.tox', '.nox',
  '.gradle', '.next', '.nuxt', '.turbo', '.parcel-cache',
]);
// Build output only next to the manifest of the tool that writes it there.
// A Map: a folder may have any name, `constructor` included.
const BUILD_DIRS = new Map([['target', ['Cargo.toml', 'pom.xml']]]);

export const TRASH = '.trash';

const GIT_TIMEOUT_MS = 30_000;

// What a directory takes on disk, the dependency folders in it, and the git
// repositories in it (each folder with a `.git`, a directory or the file of a
// worktree or submodule). Links are counted as themselves and never followed;
// a file with several links counts once. Nothing under `.git` or inside a
// dependency folder is a dependency folder or a repository of its own.
// `stopped()` ends the walk early (shutdown).
export async function scanDir(root, { stopped = () => false } = {}) {
  const seen = new Set();
  const result = { bytes: 0, dependencyBytes: 0, dependencies: [], repos: [] };
  const size = (info) => {
    if (info.nlink > 1 && !info.isDirectory()) {
      const key = `${info.dev}:${info.ino}`;
      if (seen.has(key)) return 0;
      seen.add(key);
    }
    return Number.isFinite(info.blocks) && info.blocks > 0 ? info.blocks * 512 : info.size;
  };
  const walk = async (dir, { inside }) => {
    if (stopped()) return 0;
    let entries;
    try { entries = await fs.promises.readdir(dir, { withFileTypes: true }); } catch { return 0; }
    const names = new Set(entries.map((entry) => entry.name));
    if (!inside && entries.some((entry) => entry.name === '.git' && (entry.isDirectory() || entry.isFile()))) result.repos.push(dir);
    let bytes = 0;
    const files = entries.filter((entry) => !entry.isDirectory());
    const stats = await Promise.all(files.map((entry) => fs.promises.lstat(path.join(dir, entry.name)).catch(() => null)));
    for (const info of stats) if (info) bytes += size(info);
    for (const entry of entries.filter((item) => item.isDirectory())) {
      const child = path.join(dir, entry.name);
      if (inside) {
        bytes += await walk(child, { inside });
        continue;
      }
      const dependency = DEPENDENCY_DIRS.has(entry.name)
        || (BUILD_DIRS.get(entry.name) ?? []).some((manifest) => names.has(manifest))
        || await fs.promises.access(path.join(child, 'pyvenv.cfg')).then(() => true, () => false);
      const childBytes = await walk(child, { inside: dependency || entry.name === '.git' });
      if (dependency) {
        result.dependencies.push(child);
        result.dependencyBytes += childBytes;
      }
      bytes += childBytes;
    }
    return bytes;
  };
  result.bytes = await walk(root, { inside: false });
  return result;
}

// The repositories among these that hold work which exists nowhere else, with
// why: `uncommitted` changes (untracked files included), or `unpushed`
// commits (on a branch, a tag, a detached HEAD or the stash) that no
// remote-tracking branch has; a repository with no remote has nothing pushed.
// A folder whose `.git` git does not take for a repository holds none. One
// git cannot answer for (a timeout, an error, no git) is `unknown`: it may
// hold such work.
export async function unsavedWork(repos, { stopped = () => false, git = runGit } = {}) {
  const found = [];
  for (const repo of repos) {
    if (stopped()) break;
    const reason = await repoWork(repo, git);
    if (reason) found.push({ repo, reason });
  }
  return found;
}

async function repoWork(repo, git) {
  try {
    if ((await git(repo, ['status', '--porcelain'])).trim()) return 'uncommitted';
    if ((await git(repo, ['log', '--all', '--not', '--remotes', '--format=%H', '-1'])).trim()) return 'unpushed';
    return null;
  } catch (error) {
    return /not a git repository/i.test(String(error?.stderr ?? '')) ? null : 'unknown';
  }
}

// Read-only, and only this repository: never one a folder above it is in
// (GIT_CEILING_DIRECTORIES), no lock a worker's git could trip on
// (GIT_OPTIONAL_LOCKS), and no command its config names (core.fsmonitor).
async function runGit(repo, args) {
  const { stdout } = await execFileAsync('git', ['-C', repo, '-c', 'core.fsmonitor=false', ...args], {
    timeout: GIT_TIMEOUT_MS,
    maxBuffer: 4 * 1024 * 1024,
    windowsHide: true,
    env: { ...process.env, GIT_CEILING_DIRECTORIES: path.dirname(repo), GIT_OPTIONAL_LOCKS: '0', GIT_TERMINAL_PROMPT: '0' },
  });
  return stdout;
}

// Move a directory out of the way in one step: whatever runs next finds it
// either whole or gone. The contents are deleted later (emptyTrash). The
// trash must be on the directory's own file system, and a real directory:
// never moved into through a link.
export function moveToTrash(target, trash) {
  try { fs.mkdirSync(trash, { mode: 0o700 }); } catch (error) { if (error.code !== 'EEXIST') throw error; }
  if (!fs.lstatSync(trash).isDirectory()) throw new Error(`${trash} is not a directory of its own`);
  const destination = path.join(trash, `${path.basename(target)}-${randomUUID().slice(0, 8)}`);
  fs.renameSync(target, destination);
  return destination;
}

// The trash goes as a whole: removing a path never follows a link, so a link
// in its place is removed, not what it points to.
export async function emptyTrash(trash) {
  await fs.promises.rm(trash, { recursive: true, force: true, maxRetries: 2 }).catch(() => {});
}

// A real directory (not a link to one) that still exists.
export function isDirectory(target) {
  try { return fs.lstatSync(target).isDirectory(); } catch { return false; }
}

// A real directory reached from root with no link on the way: what a scan
// found there may since sit behind a link to somewhere else.
export function isDirectoryWithin(target, root) {
  const relative = path.relative(root, target);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) return false;
  try {
    return fs.lstatSync(target).isDirectory() && fs.realpathSync(target) === path.join(fs.realpathSync(root), relative);
  } catch {
    return false;
  }
}

export function formatBytes(bytes) {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}
