// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and the OpenViking contributors.
// SPDX-License-Identifier: AGPL-3.0
// Native Termux on Android/Bionic uses POSIX APIs, not Linux binaries/services.
export * from './posix.mjs';
export const hostPlatformId = `termux-${process.arch}`;
export const loginService = 'runit';
export const systemEnvironmentKeys = Object.freeze(['PREFIX', 'TMPDIR', 'LD_PRELOAD', 'LD_LIBRARY_PATH']);
export function urlOpenCommand() { return 'termux-open-url'; }
