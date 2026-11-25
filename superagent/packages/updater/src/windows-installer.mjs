// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { execFile as execFileCallback } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { hostPlatform } from './artifacts.mjs';
import { activateRelease, currentRelease, readReleaseMetadata } from './publisher.mjs';

// A Windows release is a ZIP preview with its own channel
// (<base>/windows-preview/latest and <base>/windows-preview/<version>), so the
// updater does not download or unpack it: the installer the release ships does,
// and it already verifies the SHA-256, rejects unsafe archive entries, checks the
// bundled runtime, keeps the previous directory, and moves `current`/`rollback`
// with recovery. The updater hands it the publication and then reads back which
// release `current` names.

const execFile = promisify(execFileCallback);

export const WINDOWS_INSTALLER = path.join('scripts', 'windows', 'install-preview.ps1');
const INSTALLER_TIMEOUT_MS = 30 * 60 * 1000;

export function defaultInstallerPath(root = fileURLToPath(new URL('../../../', import.meta.url))) {
  return path.join(root, WINDOWS_INSTALLER);
}

// Windows PowerShell is present on every supported Windows install; pwsh is not
// assumed. The installer supports both.
export function windowsPowerShell(env = process.env) {
  const root = env.SystemRoot || env.SYSTEMROOT;
  const candidate = root ? path.join(root, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe') : null;
  return candidate && fs.existsSync(candidate) ? candidate : 'powershell.exe';
}

function lastLines(value, count = 6) {
  return String(value || '').trim().split('\n').map((line) => line.trim()).filter(Boolean).slice(-count).join(' ');
}

async function runInstaller({
  installerPath,
  powershell,
  env,
  execFileImpl,
  timeoutMs,
  version,
}) {
  const args = ['-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', installerPath, '-NoSetup'];
  // `-NoSetup` keeps this to the install itself: the updater restarts the apps
  // it knows about, and setup would ask questions nobody is there to answer.
  if (version) args.push('-Version', version);
  try {
    return await execFileImpl(powershell, args, {
      timeout: timeoutMs,
      env: { ...env },
      windowsHide: true,
      maxBuffer: 8 * 1024 * 1024,
    });
  } catch (error) {
    const detail = lastLines(error?.stderr) || lastLines(error?.stdout) || lastLines(error?.message);
    throw new Error(
      `the Windows installer could not install ${version || 'the latest release'}${detail ? `: ${detail}` : ''}`,
      { cause: error },
    );
  }
}

// Installs the newest Windows preview (or `version`, for a rollback) and reports
// which release `current` names afterwards.
export async function publishWithInstaller({
  baseDir = path.join(os.homedir(), '.ov-superagent'),
  env = process.env,
  execFileImpl = execFile,
  timeoutMs = INSTALLER_TIMEOUT_MS,
  version = null,
  installerPath = defaultInstallerPath(),
  powershell = windowsPowerShell(env),
  platformId = hostPlatform(),
} = {}) {
  if (!fs.existsSync(installerPath)) {
    throw new Error(`the installed release has no Windows installer at ${installerPath}; `
      + 'install a release that ships one, or update with the PowerShell installer');
  }
  const previousRelease = await currentRelease(baseDir);
  const previousMetadata = await readReleaseMetadata(previousRelease);
  await runInstaller({ installerPath, powershell, env, execFileImpl, timeoutMs, version });
  const release = await currentRelease(baseDir);
  if (!release) throw new Error('the Windows installer finished without publishing a release under current');
  const metadata = await readReleaseMetadata(release);
  if (!metadata?.version) throw new Error(`the Windows installer published ${release} without its release metadata`);
  if (metadata.platform && metadata.platform !== platformId) {
    throw new Error(`the Windows installer published ${metadata.version} for ${metadata.platform}, not ${platformId}`);
  }
  if (version && metadata.version !== version) {
    throw new Error(`the Windows installer published ${metadata.version}, not the requested ${version}`);
  }
  return {
    release,
    version: metadata.version,
    commit: typeof metadata.commit === 'string' ? metadata.commit : undefined,
    // A same-version install is a no-op in the installer, so `current` still
    // names the release that was already running.
    published: release !== previousRelease,
    previousRelease,
    previousVersion: previousMetadata?.version,
  };
}

// Puts the publication back on the version that was running before a release
// that installed cleanly but could not start.
//
// The previous release is normally still unpacked under releases/: it is what
// `current` named a moment ago, so switching the links back is the whole job.
// Doing that locally is what lets a rollback work when the network is gone or the
// channel no longer serves that version; only when no verified directory is left
// does it go back through the installer, which downloads and checks the package
// again.
// The lock is the installer's own: a directory next to the releases, created by
// install-preview.ps1 for the whole run and by the POSIX publisher for its own
// deployments. Holding it means no install can move `current` while the links are
// switched back.
const DEPLOY_LOCK = 'deploy.lock';

async function acquireDeployLock(baseDir) {
  const lock = path.join(baseDir, DEPLOY_LOCK);
  try {
    await fs.promises.mkdir(lock, { mode: 0o700 });
  } catch (error) {
    if (error?.code === 'EEXIST') return null;
    throw error;
  }
  return async () => { await fs.promises.rmdir(lock).catch(() => {}); };
}

export async function republishPrevious({
  previousVersion,
  failedRelease = null,
  ...options
}) {
  if (!previousVersion) throw new Error('no previous version to fall back to');
  const baseDir = options.baseDir ?? path.join(os.homedir(), '.ov-superagent');
  const local = await localRelease(baseDir, previousVersion);
  if (local) {
    const releaseLock = await acquireDeployLock(baseDir);
    // An install is running: it owns `current` right now, so a rollback that
    // switched links under it would fight it. Leave both links as they are.
    if (!releaseLock) {
      return { skipped: true, reason: 'another install or upgrade is running (it holds deploy.lock)' };
    }
    try {
      const from = await currentRelease(baseDir);
      const running = from ? await fs.promises.realpath(from).catch(() => null) : null;
      const failed = failedRelease ? await fs.promises.realpath(failedRelease).catch(() => null) : null;
      // Someone (or another tool) already moved the publication: the release that
      // failed is not what would start next, so there is nothing to undo.
      if (failedRelease && running !== failed) {
        return {
          skipped: true,
          reason: `current no longer names the release that failed to start (${path.basename(failedRelease)})`,
        };
      }
      await activateRelease(baseDir, local, from);
      const metadata = await readReleaseMetadata(local);
      const fromMetadata = await readReleaseMetadata(from);
      return {
        release: local,
        version: previousVersion,
        commit: typeof metadata?.commit === 'string' ? metadata.commit : undefined,
        published: true,
        previousRelease: from,
        previousVersion: fromMetadata?.version,
      };
    } finally {
      await releaseLock();
    }
  }
  return publishWithInstaller({ ...options, version: previousVersion });
}

// What a release directory must carry to be adopted without the installer: the
// metadata naming the version asked for, and the two files a start needs. The
// installer wrote and verified this directory when it was installed, so its
// contents are not re-hashed here - a full-tree digest of 24k files is the check
// the installer itself skips when its metadata digest already matches.
const REQUIRED_RELEASE_FILES = Object.freeze([
  'ecosystem.config.cjs',
  path.join('packages', 'daemon', 'src', 'main.mjs'),
]);

function isInside(parent, child) {
  const relative = path.relative(parent, child);
  return relative !== '' && !relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative);
}

async function localRelease(baseDir, version) {
  if (typeof version !== 'string' || !/^[0-9A-Za-z][0-9A-Za-z._-]*$/.test(version)) return null;
  const releasesRoot = await fs.promises.realpath(path.join(baseDir, 'releases')).catch(() => null);
  if (!releasesRoot) return null;
  const real = await fs.promises.realpath(path.join(releasesRoot, version)).catch(() => null);
  if (!real || !isInside(releasesRoot, real)) return null;
  const metadata = await readReleaseMetadata(real);
  if (!metadata || metadata.version !== version) return null;
  for (const relative of REQUIRED_RELEASE_FILES) {
    if (!fs.existsSync(path.join(real, relative))) return null;
  }
  return real;
}
