// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { BACKGROUND_CONTEXT } from '@earendil-works/chord/context';
import { NodeExecutionEnv } from '@earendil-works/pi-durable/env/node';
import { CodingTools, createPowerShellTool } from '@earendil-works/pi-durable/tools';
import { durableCodingTools } from '../src/durable-coding-tools.mjs';
import { codingShell } from '../src/platform/index.mjs';

const tool = durableCodingTools('powershell').tools.find(t => t.name === 'powershell');
function apiFor(exec, cwd = process.cwd()) {
  let output = '';
  return {
    env: { cwd, exec },
    output(text) { output += text; },
    retainedOutput() { return { text: output, truncated: false }; },
    diagnostic() {},
  };
}
const success = (exitCode = 0) => ({ ok: true, value: { exitCode } });

test('native tool selection retains POSIX tools and exposes PowerShell without Bash', () => {
  assert.equal(durableCodingTools('bash'), CodingTools);
  assert.deepEqual(durableCodingTools('powershell').tools.map(t => t.name), ['read', 'write', 'edit', 'powershell']);
  assert.equal(durableCodingTools().tools.at(-1).name, codingShell);
});

test('PowerShell commands use direct argv; only spawn failure falls back to 5.1', async () => {
  const calls = [];
  const command = 'Write-Output "中文 空格"; exit 7';
  const api = apiFor(async (argv, options) => {
    calls.push({ argv, options });
    return calls.length === 1 ? { ok: false, error: { code: 'spawn_error' } } : success(7);
  });
  const result = await tool.execute({ command, timeout: 5 }, api, BACKGROUND_CONTEXT);
  assert.deepEqual(calls.map(c => c.argv[0]), ['pwsh', 'powershell']);
  for (const { argv, options } of calls) {
    assert.ok(Array.isArray(argv));
    assert.equal(argv.at(-1).split('\n').at(-1), command);
    assert.equal(options.cwd, process.cwd());
    assert.equal(options.timeout, 5);
  }
  assert.equal(result.isError, true);
  assert.equal(result.structuredOutput.exitCode, 7);
  let count = 0;
  await tool.execute({ command }, apiFor(async () => { count++; return success(9); }), BACKGROUND_CONTEXT);
  assert.equal(count, 1, 'a failed command must not execute twice');
});

test('timeout and missing shells fail without pretending success', async () => {
  let count = 0;
  await assert.rejects(tool.execute({ command: 'Start-Sleep 10', timeout: 1 }, apiFor(async () => {
    count++; return { ok: false, error: { code: 'timeout' } };
  }), BACKGROUND_CONTEXT), /timed out/);
  assert.equal(count, 1);
  const missing = new Error('PowerShell missing'); missing.code = 'spawn_error';
  await assert.rejects(tool.execute({ command: 'x' }, apiFor(async () => ({ ok: false, error: missing })), BACKGROUND_CONTEXT), /PowerShell missing/);
});

test('Windows native PowerShell runs with an unusable Bash shellPath', { skip: process.platform !== 'win32' }, async () => {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent 中文 shell-'));
  try {
    const env = new NodeExecutionEnv({ cwd, shellPath: path.join(cwd, 'no-bash.exe') });
    const api = apiFor(env.exec.bind(env), cwd);
    const result = await tool.execute({ command: 'Write-Output "中文 空格"; [Console]::Error.WriteLine("stderr-marker"); exit 7' }, api, BACKGROUND_CONTEXT);
    assert.equal(result.structuredOutput.exitCode, 7);
    assert.match(result.structuredOutput.output, /中文 空格/);
    assert.match(result.structuredOutput.output, /stderr-marker/);
    await assert.rejects(tool.execute({ command: 'Start-Sleep 30', timeout: 0.2 }, api, BACKGROUND_CONTEXT), /timed out/);
  } finally { fs.rmSync(cwd, { recursive: true, force: true }); }
});

// Force the legacy executable as well: a machine with pwsh installed must not
// accidentally certify only PowerShell 7 when validating the 5.1 fallback.
test('Windows 5.1 fallback executes directly without Bash', { skip: process.platform !== 'win32' }, async () => {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-legacy-shell-'));
  try {
    const env = new NodeExecutionEnv({ cwd, shellPath: path.join(cwd, 'no-bash.exe') });
    const legacy = createPowerShellTool({ programs: [path.join(cwd, 'missing-pwsh.exe'), 'powershell'] });
    const result = await legacy.execute({ command: 'Write-Output "中文"; Write-Output $PSVersionTable.PSVersion.Major' }, apiFor(env.exec.bind(env), cwd), BACKGROUND_CONTEXT);
    assert.equal(result.structuredOutput.exitCode, 0);
    assert.match(result.structuredOutput.output, /中文/);
    assert.match(result.structuredOutput.output, /5/);
  } finally { fs.rmSync(cwd, { recursive: true, force: true }); }
});
