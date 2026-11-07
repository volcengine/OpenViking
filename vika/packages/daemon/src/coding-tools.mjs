// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import { defineExtension } from '@earendil-works/pi-durable';
import { CodingTools, createReadTool, createWriteTool, createEditTool, createPowerShellTool } from '@earendil-works/pi-durable/tools';

import { guardWindowsFileTool } from './windows-file-path.mjs';

export function durableCodingTools(platform = process.platform) {
  return platform === 'win32' ? defineExtension({
    name: 'coding-tools',
    tools: [...[createReadTool(), createWriteTool(), createEditTool()].map(guardWindowsFileTool), createPowerShellTool()],
  }) : CodingTools;
}
