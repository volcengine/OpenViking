// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';

// What this vika is: an installed release says so in release.json, a source
// checkout has only package.json.
export function releaseInfo() {
  const root = new URL('../../../', import.meta.url);
  try {
    return JSON.parse(fs.readFileSync(new URL('release.json', root), 'utf8'));
  } catch {
    return { version: JSON.parse(fs.readFileSync(new URL('package.json', root), 'utf8')).version };
  }
}
