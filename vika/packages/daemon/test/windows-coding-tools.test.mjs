// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { BACKGROUND_CONTEXT } from '@earendil-works/chord/context';
import { NodeExecutionEnv } from '@earendil-works/pi-durable/env/node';
import { durableCodingTools } from '../src/coding-tools.mjs';
import { piPlatformTools } from '../src/pi-runtime.mjs';

test('native Windows runtimes use PowerShell without excluding extension tools', () => {
  assert.deepEqual(piPlatformTools('win32'), ['--tools', '+powershell,-bash']);
  assert.deepEqual(piPlatformTools('linux'), []);
  assert.deepEqual(durableCodingTools('win32').tools.map(({ name }) => name), ['read', 'write', 'edit', 'powershell']);
  assert.ok(durableCodingTools('linux').tools.some(({ name }) => name === 'bash'));
});

test('Durable native PowerShell tool runs without a Bash shell', { skip: process.platform !== 'win32' }, async () => {
  const tool = durableCodingTools().tools.find(({ name }) => name === 'powershell');
  let output = '';
  const api = {
    env: new NodeExecutionEnv({ cwd: process.cwd() }),
    output(text) { output += text; },
    retainedOutput() { return { text: output, truncated: false }; },
    diagnostic() {},
  };
  const result = await tool.execute({ command: "Write-Output 'vika-native-powershell'; exit 0", timeout: 10 }, api, BACKGROUND_CONTEXT);
  assert.equal(result.structuredOutput.exitCode, 0);
  assert.match(result.structuredOutput.output, /vika-native-powershell/);
});
