// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import { createRequire } from 'node:module';
import * as platform from './platform/index.mjs';
const artifactPlatforms = createRequire(import.meta.url)('../../updater/artifact-platforms.json');

// Product support policy lives here, rather than in the OS implementations.
export function capabilitiesFor(implementation) {
  const artifact = artifactPlatforms[implementation.hostPlatformId];
  // `release` publishes tar.gz archives the updater installs itself, so it needs
  // the POSIX release layout; `installer` publishes Windows ZIP previews through
  // the PowerShell installer the release ships (see updater/src/windows-installer.mjs).
  const updater = artifact?.updater === 'installer'
    || (artifact?.updater === 'release' && implementation.supportsShellScripts === true);
  return Object.freeze({
    updater,
    autostart: implementation.loginService !== null,
    connectors: Object.freeze({ email: true }),
  });
}
export const capabilities = capabilitiesFor(platform);
