// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { execFile as execFileCallback } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { hostPlatform } from './artifacts.mjs';
import { currentRelease, readReleaseMetadata } from './publisher.mjs';

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
// that installed cleanly but could not start. The installer compares the existing
// directory with the package it downloads and switches `current`/`rollback` back
// without touching a running instance.
export async function republishPrevious({
  previousVersion,
  ...options
}) {
  if (!previousVersion) throw new Error('no previous version to fall back to');
  return publishWithInstaller({ ...options, version: previousVersion });
}
