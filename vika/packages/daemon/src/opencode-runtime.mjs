// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

const MINIMUM_OPENCODE = Object.freeze({ major: 1, minor: 15, patch: 7 });

export function parseOpencodeVersion(output) {
  const match = /(\d+)\.(\d+)\.(\d+)/.exec(String(output ?? ''));
  if (!match) return null;
  return {
    major: Number(match[1]),
    minor: Number(match[2]),
    patch: Number(match[3]),
    raw: match[0],
  };
}

// The daemon drives the Agent Client Protocol shipped with OpenCode 1.
// OpenCode 2 changed its server/MCP contracts; it is rejected until the ACP
// transport is ported.
export function assertSupportedOpencodeVersion(output) {
  const version = parseOpencodeVersion(output);
  if (!version) throw new Error('opencode preflight returned an unrecognised version');
  const minimumText = `${MINIMUM_OPENCODE.major}.${MINIMUM_OPENCODE.minor}.${MINIMUM_OPENCODE.patch}`;
  if (version.major !== MINIMUM_OPENCODE.major) {
    throw new Error(`this release supports OpenCode 1.x (${minimumText}+); found ${version.raw}`);
  }
  const numeric = version.major * 1_000_000 + version.minor * 1_000 + version.patch;
  const floor = MINIMUM_OPENCODE.major * 1_000_000
    + MINIMUM_OPENCODE.minor * 1_000 + MINIMUM_OPENCODE.patch;
  if (numeric < floor) {
    throw new Error(`opencode ${version.raw} is too old; update to ${minimumText} or newer`);
  }
  return version;
}
