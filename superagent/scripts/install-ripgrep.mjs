#!/usr/bin/env node
// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { fileURLToPath } from 'node:url';
import { hostPlatform } from '../packages/updater/src/artifacts.mjs';
import { installRipgrepBinary } from './release/ripgrep.mjs';

const { bin, version } = await installRipgrepBinary({ root: fileURLToPath(new URL('..', import.meta.url)), platformId: hostPlatform() });
process.stdout.write(`ripgrep ${version} installed at ${bin}\n`);
