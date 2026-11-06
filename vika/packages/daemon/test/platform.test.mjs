import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import { findExecutable, executableInvocation, npmShimEntrypoint, normalizeEnvironment, execFile, localSocketPath, isNamedPipe } from '../src/platform.mjs';

test('pipe identity is stable, case insensitive and scoped to the instance directory', () => {
  const one = localSocketPath('C:\\Users\\Alice\\.vika\\primary', 'actions', 'win32');
  assert.ok(isNamedPipe(one));
  assert.equal(one, localSocketPath('c:\\users\\alice\\.vika\\primary', 'actions', 'win32'));
  assert.notEqual(one, localSocketPath('C:\\Users\\Alice\\.vika\\other', 'actions', 'win32'));
  assert.equal(localSocketPath('/tmp/vika', 'actions', 'linux'), '/tmp/vika/actions.sock');
});

test('POSIX invocation preserves argv and executable', () => {
  const args = ['a b', '$HOME', '&|<>"'];
  assert.deepEqual(executableInvocation('/usr/bin/tool', args, { platform: 'linux' }), { command: '/usr/bin/tool', args });
});

test('execFile promisify preserves stdout/stderr object and does not evaluate arguments', async () => {
  const args = ['space value', '& echo injected', '%PATH%', 'quote"here', '$HOME'];
  const result = await promisify(execFile)(process.execPath, ['-e', 'process.stdout.write(JSON.stringify(process.argv.slice(1)))', ...args], {});
  assert.deepEqual(JSON.parse(result.stdout), args);
  assert.equal(result.stderr, '');
});

test('Windows npm shims run Node without a command shell', { skip: process.platform !== 'win32' }, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika shim '));
  try {
    fs.mkdirSync(path.join(dir, 'node_modules', 'fixture'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'node_modules', 'fixture', 'cli.js'), 'process.stdout.write(JSON.stringify(process.argv.slice(2)))');
    fs.writeFileSync(path.join(dir, 'fixture.cmd'), '@echo off\r\n"node" "%dp0%\\node_modules\\fixture\\cli.js" %*\r\n');
    // npm also writes a POSIX shim with no extension; never choose it first.
    fs.writeFileSync(path.join(dir, 'fixture'), '#!/bin/sh\nexit 1');
    const env = { ...process.env, Path: 'C:\\missing-inherited-path', PATH: dir, Pathext: '.COM', PATHEXT: '.EXE;.CMD' };
    const found = findExecutable('fixture', env);
    assert.ok(found, 'explicit PATH must override inherited Path');
    assert.ok(found.toLowerCase().endsWith('fixture.cmd'));
    assert.ok(findExecutable('fixture', { Path: dir, Pathext: '.CMD' }));
    const childEnv = await promisify(execFile)(process.execPath, ['-e', 'process.stdout.write(process.env.PATH)'], { env });
    assert.equal(childEnv.stdout, dir);
    const args = ['& echo injected', 'a b', '%USERPROFILE%', '"quoted"', '中文'];
    const { stdout } = await promisify(execFile)(found, args, { env });
    assert.deepEqual(JSON.parse(stdout), args);
    fs.copyFileSync(path.join(dir, 'node_modules', 'fixture', 'cli.js'), path.join(dir, 'node_modules', 'fixture', 'cli'));
    fs.writeFileSync(path.join(dir, 'extensionless.cmd'), '"%_prog%" "%dp0%\\node_modules\\fixture\\cli" %*');
    const extensionless = await promisify(execFile)(path.join(dir, 'extensionless.cmd'), args, { env });
    assert.deepEqual(JSON.parse(extensionless.stdout), args);
    fs.writeFileSync(path.join(dir, 'other.cmd'), '@echo off\necho arbitrary script');
    assert.throws(() => executableInvocation(path.join(dir, 'other.cmd'), [], { env }), /Unsupported Windows command shim/);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('npm shim parsing accepts extensionless Node entrypoints, not arbitrary batch scripts', () => {
  const shim = 'IF EXIST "%dp0%\\node.exe" SET "_prog=%dp0%\\node.exe"\r\n"%_prog%" "%dp0%\\node_modules\\tool\\bin\\tool" %*';
  assert.equal(npmShimEntrypoint(shim), 'node_modules\\tool\\bin\\tool');
  assert.equal(npmShimEntrypoint('"cmd.exe" "%dp0%\\arbitrary.bat" %*'), null);
  assert.equal(npmShimEntrypoint('echo "%dp0%\\tool.js"'), null);
});

test('Windows environment normalization makes lookup and child overrides consistent', () => {
  const inherited = { Path: 'C:\\old', PATH: 'C:\\new', Pathext: '.COM', PATHEXT: '.EXE;.CMD', TOKEN: 'preserved' };
  const normalized = normalizeEnvironment(inherited, 'win32');
  assert.deepEqual(normalized, { PATH: 'C:\\new', PATHEXT: '.EXE;.CMD', TOKEN: 'preserved' });
  assert.equal(inherited.Path, 'C:\\old');
  assert.deepEqual(normalizeEnvironment({ Path: 'C:\\only', Pathext: '.CMD' }, 'win32'), { PATH: 'C:\\only', PATHEXT: '.CMD' });
  assert.equal(normalizeEnvironment(inherited, 'linux'), inherited);
});
