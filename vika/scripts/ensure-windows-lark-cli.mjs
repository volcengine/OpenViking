#!/usr/bin/env node
// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

// npm may block dependency lifecycle scripts (including with --ignore-scripts).
// Invoke only the pinned Lark package's checksum-verifying installer when the
// executable is missing. Never approve all lifecycle scripts or use a global CLI.
export function ensureWindowsLarkCli({
  platform = process.platform,
  packageDir,
  environment = process.env,
  execFileSyncImpl = execFileSync,
} = {}) {
  if (platform !== 'win32') throw new Error('This Lark CLI bootstrap is Windows-only.');
  packageDir ??= path.dirname(createRequire(import.meta.url).resolve('@larksuite/cli/package.json'));
  const { version } = JSON.parse(fs.readFileSync(path.join(packageDir, 'package.json'), 'utf8'));
  if (typeof version !== 'string' || !/^\d+\.\d+\.\d+(?:-[\w.-]+)?$/.test(version)) {
    throw new Error('The installed Lark CLI package has an invalid version.');
  }
  const bin = path.join(packageDir, 'bin', 'lark-cli.exe');
  const hasBinary = () => {
    try { return fs.statSync(bin).isFile(); } catch (error) {
      if (error.code === 'ENOENT') return false;
      throw error;
    }
  };
  if (!hasBinary()) {
    const installer = path.join(packageDir, 'scripts', 'install.js');
    try {
      // LARK_CLI_RUN also prevents the package's npx-postinstall optimization
      // from silently skipping the download if npm_command=exec is inherited.
      execFileSyncImpl(process.execPath, [installer], {
        env: { ...environment, LARK_CLI_RUN: '1' },
        stdio: 'inherit', windowsHide: true, timeout: 420_000,
      });
    } catch (cause) {
      throw new Error('Could not install the bundled Windows Lark CLI; check the download/checksum error above and retry the installer.', { cause });
    }
  }
  if (!hasBinary()) throw new Error(`Lark CLI installation did not produce ${bin}`);
  return validateWindowsLarkCli({ platform, packageDir, environment, execFileSyncImpl });
}

// Release validators can call this independently: it never downloads/installs.
export function validateWindowsLarkCli({
  platform = process.platform,
  packageDir,
  environment = process.env,
  execFileSyncImpl = execFileSync,
} = {}) {
  if (platform !== 'win32') throw new Error('This Lark CLI validator is Windows-only.');
  packageDir ??= path.dirname(createRequire(import.meta.url).resolve('@larksuite/cli/package.json'));
  const { version } = JSON.parse(fs.readFileSync(path.join(packageDir, 'package.json'), 'utf8'));
  if (typeof version !== 'string' || !/^\d+\.\d+\.\d+(?:-[\w.-]+)?$/.test(version)) {
    throw new Error('The installed Lark CLI package has an invalid version.');
  }
  const bin = path.join(packageDir, 'bin', 'lark-cli.exe');
  if (!fs.existsSync(bin) || !fs.statSync(bin).isFile()) throw new Error(`Bundled Windows Lark CLI is missing: ${bin}`);
  let output;
  try {
    output = execFileSyncImpl(bin, ['--version'], {
      env: environment, encoding: 'utf8', windowsHide: true, timeout: 15_000,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
  } catch (cause) {
    throw new Error(`The bundled Windows Lark CLI cannot execute: ${bin}`, { cause });
  }
  const reported = String(output).trim().match(/^lark-cli version v?(\d+\.\d+\.\d+(?:-[\w.-]+)?)(?:\s|$)/m)?.[1];
  if (reported !== version) throw new Error(`Bundled Lark CLI version mismatch: expected ${version}, got ${reported ?? 'unrecognized output'}`);
  return { bin, version };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const { bin, version } = ensureWindowsLarkCli();
    process.stdout.write(`Bundled Windows Lark CLI ${version} is ready (${bin}).\n`);
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}
