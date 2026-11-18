// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
// Android/Termux uses POSIX process and filesystem semantics, but not Linux
// release artifacts or desktop service managers. Phase 1 has no runit service.
export * from './posix.mjs';
export const hostPlatformId = `termux-${process.arch}`;
export const loginService = null;
export const systemEnvironmentKeys = Object.freeze(['PREFIX', 'TMPDIR', 'LD_PRELOAD', 'LD_LIBRARY_PATH']);
export function urlOpenCommand() { return 'termux-open-url'; }
