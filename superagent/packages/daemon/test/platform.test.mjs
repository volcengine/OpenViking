import test from 'node:test';
import * as requireNative from 'node:child_process';
import * as windows from '../src/platform/win32.mjs';
import * as posix from '../src/platform/posix.mjs';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import { findExecutable, executableInvocation, normalizeEnvironment, execFile, localSocketPath, isNamedPipe } from '../src/platform/index.mjs';

test('pipe identity is stable, case insensitive and scoped to the instance directory', () => {
  const one = windows.localSocketPath('C:\\Users\\Alice\\.ov-superagent\\primary', 'actions');
  assert.ok(windows.isNamedPipe(one));
  assert.equal(one, windows.localSocketPath('c:\\users\\alice\\.ov-superagent\\primary', 'actions'));
  assert.notEqual(one, windows.localSocketPath('C:\\Users\\Alice\\.ov-superagent\\other', 'actions'));
  assert.equal(posix.localSocketPath('/tmp/superagent', 'actions'), '/tmp/superagent/actions.sock');
});

test('POSIX invocation preserves argv and executable', () => {
  const args = ['a b', '$HOME', '&|<>"'];
  assert.deepEqual(posix.executableInvocation('/usr/bin/tool', args), { command: '/usr/bin/tool', args });
});

test('execFile promisify preserves stdout/stderr object and does not evaluate arguments', async () => {
  const args = ['space value', '& echo injected', '%PATH%', 'quote"here', '$HOME'];
  const result = await promisify(execFile)(process.execPath, ['-e', 'process.stdout.write(JSON.stringify(process.argv.slice(1)))', ...args], {});
  assert.deepEqual(JSON.parse(result.stdout), args);
  assert.equal(result.stderr, '');
});

test('Windows npm shims run Node without a command shell', { skip: process.platform !== 'win32' }, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent shim '));
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

test('Windows environment normalization makes lookup and child overrides consistent', () => {
  const inherited = { Path: 'C:\\old', PATH: 'C:\\new', Pathext: '.COM', PATHEXT: '.EXE;.CMD', TOKEN: 'preserved' };
  const normalized = windows.normalizeEnvironment(inherited);
  assert.deepEqual(normalized, { PATH: 'C:\\new', PATHEXT: '.EXE;.CMD', TOKEN: 'preserved' });
  assert.equal(inherited.Path, 'C:\\old');
  assert.deepEqual(windows.normalizeEnvironment({ Path: 'C:\\only', Pathext: '.CMD' }), { PATH: 'C:\\only', PATHEXT: '.CMD' });
  assert.equal(posix.normalizeEnvironment(inherited), inherited);
});

test('POSIX process wrappers preserve native overloads and Promise child handle', { skip: process.platform === 'win32' }, async (t) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-overloads-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const command = path.join(directory, 'fixture');
  fs.writeFileSync(command, `#!${process.execPath}\nprocess.stdout.write('ok');`, { mode: 0o700 });
  const native = await import('node:child_process');
  const { spawn } = await import('../src/platform/index.mjs');
  const options = { env: { PATH: process.env.PATH }, timeout: 2000 };
  for (const args of [[], [options], [undefined, options], [[], options], [['-e', 'process.stdout.write("ok")'], options]]) {
    const expected = await promisify(native.execFile)(command, ...args);
    const pending = promisify(execFile)(command, ...args);
    assert.ok(pending.child?.pid);
    assert.deepEqual(await pending, expected);
    const callbackResult = await new Promise((resolve, reject) => execFile(command, ...args,
      (error, stdout, stderr) => error ? reject(error) : resolve({ stdout, stderr })));
    assert.deepEqual(callbackResult, expected);
  }
  const { once } = await import('node:events');
  for (const invoke of [native.spawn, spawn]) {
    const child = invoke(command, { stdio: 'ignore' });
    assert.deepEqual(await once(child, 'exit'), [0, null]);
  }
});

 test('Windows rejects unsupported direct script entrypoints before spawning', () => {
  for (const command of ['C:\\tools\\test.ps1', 'script.PS1', 'script.py', 'script.sh', 'script.vbs']) {
    assert.throws(() => windows.executableInvocation(command, [], { env: { PATH: '' } }), /Unsupported Windows executable entrypoint/);
  }
  assert.deepEqual(windows.executableInvocation('pwsh.exe', ['-File', 'test.ps1'], { env: { PATH: '' } }), { command: 'pwsh.exe', args: ['-File', 'test.ps1'] });
});

test('Windows wrappers support omitted args, options-only and inherited null env', async t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-win-overloads-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const fixture = path.join(dir, 'fixture.mjs');
  fs.writeFileSync(fixture, 'process.stdout.write("ok");');
  for (const args of [[], [{ env: null }], [undefined, { env: null }], [[], null]]) {
    const pending = promisify(windows.execFile)(fixture, ...args);
    assert.ok(pending.child?.pid);
    assert.deepEqual(await pending, { stdout: 'ok', stderr: '' });
  }
});
test('pipe names differ across daemon processes, not only instance directories', () => {
  const module = new URL('../src/platform/win32.mjs', import.meta.url).href;
  const script = `import {localSocketPath} from ${JSON.stringify(module)}; console.log(localSocketPath('C:/Users/test/.ov-superagent/data'));`;
  const { execFileSync } = requireNative;
  const first = execFileSync(process.execPath, ['--input-type=module', '-e', script], { encoding: 'utf8' });
  const second = execFileSync(process.execPath, ['--input-type=module', '-e', script], { encoding: 'utf8' });
  assert.notEqual(first, second);
});

test('npm shim entrypoints remain private while invocation validates standard shims', t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-shim-parse-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const entry = path.join(directory, 'node_modules', 'tool', 'bin', 'tool');
  fs.mkdirSync(path.dirname(entry), { recursive: true });
  fs.writeFileSync(entry, '// fixture');
  const shim = path.join(directory, 'tool.cmd');
  fs.writeFileSync(shim, String.raw`IF EXIST "%dp0%\node.exe" SET "_prog=%dp0%\node.exe"
"%_prog%" "%dp0%\node_modules\tool\bin\tool" %*`);
  const args = ['a b', '%PATH%', '& whoami'];
  assert.deepEqual(windows.executableInvocation(shim, args), { command: process.execPath, args: [entry, ...args] });
  for (const unsupported of [String.raw`"cmd.exe" "%dp0%\arbitrary.bat" %*`, 'echo arbitrary script']) {
    fs.writeFileSync(shim, unsupported);
    assert.throws(() => windows.executableInvocation(shim, args), /Unsupported Windows command shim/);
  }
});

test('a release bin\\pm2.cmd runs that release\'s pm2 with that release\'s Node', t => {
  const release = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-release-shim-'));
  t.after(() => fs.rmSync(release, { recursive: true, force: true }));
  const shim = path.join(release, 'bin', 'pm2.cmd');
  const entry = path.join(release, 'node_modules', 'pm2', 'bin', 'pm2');
  fs.mkdirSync(path.dirname(shim), { recursive: true });
  fs.mkdirSync(path.dirname(entry), { recursive: true });
  fs.writeFileSync(entry, '// fixture');
  // The batch file is not executed: the arguments never pass through cmd.exe.
  fs.writeFileSync(shim, 'echo arbitrary script');
  const args = ['start', 'a b', '%PATH%', '& whoami'];
  assert.deepEqual(windows.executableInvocation(shim, args), { command: process.execPath, args: [entry, ...args] });
  // A Windows package keeps its Node in runtime/, which is preferred when present.
  const node = path.join(release, 'runtime', 'node.exe');
  fs.mkdirSync(path.dirname(node), { recursive: true });
  fs.writeFileSync(node, '');
  assert.deepEqual(windows.executableInvocation(shim, args), { command: node, args: [entry, ...args] });
  // Without the entrypoint it is still refused rather than handed to cmd.exe.
  fs.rmSync(entry);
  assert.throws(() => windows.executableInvocation(shim, args), /Unsupported Windows command shim/);
});
