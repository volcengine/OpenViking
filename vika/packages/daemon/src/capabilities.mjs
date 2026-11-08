// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import { createRequire } from 'node:module';
import * as platform from './platform/index.mjs';
const artifactPlatforms = createRequire(import.meta.url)('../../updater/artifact-platforms.json');

// Product support policy lives here, rather than in the OS implementations.
export function capabilitiesFor(implementation) {
  return Object.freeze({
    updater: implementation.supportsShellScripts && artifactPlatforms[implementation.hostPlatformId]?.sourceOnly !== true,
    autostart: implementation.loginService !== null,
    connectors: Object.freeze({ email: true }),
  });
}
export const capabilities = capabilitiesFor(platform);
