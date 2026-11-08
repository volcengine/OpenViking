// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { binName } from './platform/index.mjs';

import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

// The release pins lark-cli (the Core's event parsing is verified against
// that version); LARK_CLI_BIN still overrides it.
export function bundledLarkCli() {
  try {
    const packageDir = path.dirname(createRequire(import.meta.url).resolve('@larksuite/cli/package.json'));
    const bin = path.join(packageDir, 'bin', binName('lark-cli'));
    if (fs.existsSync(bin)) return bin;
  } catch {}
  return 'lark-cli';
}
