// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
// No top-level await: supported Node versions can require this entry from CJS.
import * as posix from './posix.mjs';
import * as win32 from './win32.mjs';
export const platformName = process.platform;
export const hostPlatformId = `${process.platform}-${process.arch}`;
export const windowsX64 = 'win32-x64';
const implementation = process.platform === 'win32' ? win32 : posix;
export const { appServerPromptArgs, appServerThreadParams, claudePromptArgs, spawn, execFile, detached, processOptions, pm2Options, supportsUpdater, normalizeEnvironment, findExecutable, executableInvocation, isNamedPipe, localSocketPath, homeDir, ecosystemPath, pm2Bin, binName, expandHome, supportsConnector, assertEmailSupported, linkConfig, onShutdown, terminateProcessTree, assertPrivateDirectory, preparePrivateDirectory, pm2ControlEnvironment } = implementation;
// Explicit platform injection is retained for the existing CLI/config tests.
export function supportsAutostart(platform = platformName) { return platform !== 'win32'; }
export function opener(env = process.env, platform = platformName) { return (platform === 'win32' ? win32 : posix).opener(env, platform); }
