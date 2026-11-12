// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import { defineExtension, section } from '@earendil-works/pi-durable';
import { CodingTools, createReadTool, createWriteTool, createEditTool, createPowerShellTool } from '@earendil-works/pi-durable/tools';
import { codingShell } from './platform/index.mjs';

// Reuse the pinned upstream executor: it invokes argv directly, prefers pwsh,
// falls back to powershell only on spawn failure, and retains output/timeout
// semantics. In particular, a failed script must not be executed a second time.
export function durableCodingTools(shell = codingShell) {
  if (shell === 'bash') return CodingTools;
  if (shell !== 'powershell') throw new Error(`Unsupported coding shell: ${shell}`);
  return defineExtension({
    name: 'coding-tools',
    tools: [createReadTool(), createWriteTool(), createEditTool(), createPowerShellTool()],
    sections: [section('native-shell', () =>
      'Use the powershell tool for commands on Windows; Bash is not required. '
      + 'It prefers pwsh (PowerShell 7) and falls back to powershell (Windows PowerShell 5.1). '
      + 'Check $PSVersionTable.PSVersion before using version-specific syntax; do not assume && is supported. '
      + 'Use read/write/edit for file changes and literal paths for names containing spaces. '
      + 'Each command starts a fresh shell without a user profile. '
      + 'For native programs, inspect $LASTEXITCODE and use exit $LASTEXITCODE when propagating failure.')],
  });
}
