// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { fileURLToPath } from 'node:url';
import { binName } from './platform/index.mjs';

// ripgrep, pinned and shipped at bin/rg (scripts/release/ripgrep.mjs): an
// agent's shell searches with it rather than `grep -r`, which reads every
// dependency, build output and .git object in a large tree. Pi's own grep tool
// would download rg at run time instead, and is not enabled.
export const RIPGREP_VERSION = '15.2.0';

export const bundledRipgrep = () => fileURLToPath(new URL(`../../../bin/${binName('rg')}`, import.meta.url));
