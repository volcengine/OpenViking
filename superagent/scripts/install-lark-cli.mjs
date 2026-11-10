#!/usr/bin/env node
// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { createRequire } from 'node:module';
import path from 'node:path';
import { installLarkCliBinary } from './release/lark-cli.mjs';
import { hostPlatform } from '../packages/updater/src/artifacts.mjs';

// `npm run setup` for a source checkout: put the pinned lark-cli binary for
// this machine where the daemon looks for it.
const require = createRequire(import.meta.url);
const packageDir = path.dirname(require.resolve('@larksuite/cli/package.json'));
const { bin, version } = await installLarkCliBinary({ packageDir, platformId: hostPlatform() });
process.stdout.write(`lark-cli ${version} installed at ${bin}\n`);
