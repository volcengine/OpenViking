import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { cliShellCommand, cliCommandCode, cliShellHint } from '../src/agent-clis.mjs';

test('Windows CLI hints use explicit PowerShell invocation and literal quoting', () => {
  const executable = 'C:\\用户 空格\\O\'Brien\\tool "$HOME`&.exe';
  assert.equal(cliShellCommand(executable, 'win32'), '& \'C:\\用户 空格\\O\'\'Brien\\tool "$HOME`&.exe\'');
  assert.match(cliShellHint('win32'), /PowerShell.*not cmd.exe/);
  assert.equal(cliShellCommand('/opt/tool', 'linux'), '/opt/tool');
  const code = cliCommandCode(executable, ' --help', 'win32');
  assert.ok(code.startsWith('``& '));
  assert.ok(code.endsWith(' --help``'));
});

test('Windows generated command executes a path containing Unicode, spaces and apostrophes', { skip: process.platform !== 'win32' }, () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "维京 O'Brien $home "));
  try {
    const script = path.join(directory, 'literal & tool.ps1');
    fs.writeFileSync(script, '\uFEFFparam([string]$Value)\r\n[Console]::Write($Value)\r\n', 'utf16le');
    const command = `${cliShellCommand(script)} 'literal $HOME & value'`;
    const result = spawnSync('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', Buffer.from(command, 'utf16le').toString('base64')], { encoding: 'utf8' });
    assert.ifError(result.error);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout, 'literal $HOME & value');
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});
