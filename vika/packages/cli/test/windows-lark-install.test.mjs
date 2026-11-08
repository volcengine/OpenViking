import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { ensureWindowsLarkCli, validateWindowsLarkCli } from '../../daemon/windows/ensure-windows-lark-cli.mjs';

function fixture(t) {
  const packageDir = fs.mkdtempSync(path.join(os.tmpdir(), "vika lark 用户 ' "));
  t.after(() => fs.rmSync(packageDir, { recursive: true, force: true }));
  fs.mkdirSync(path.join(packageDir, 'scripts'));
  fs.mkdirSync(path.join(packageDir, 'bin'));
  fs.writeFileSync(path.join(packageDir, 'package.json'), JSON.stringify({ version: '1.0.94' }));
  const bin = path.join(packageDir, 'bin', 'lark-cli.exe');
  return { packageDir, bin, platform: 'win32' };
}

test('blocked postinstall is recovered by the package installer, then the exact executable is checked', t => {
  const options = fixture(t);
  fs.writeFileSync(path.join(options.packageDir, 'scripts', 'install.js'), `
    const fs=require('node:fs');const path=require('node:path');
    if(process.env.LARK_CLI_RUN!=='1')process.exit(3);
    fs.writeFileSync(path.join(__dirname,'..','bin','lark-cli.exe'),'fixture');`);
  const calls = [];
  const result = ensureWindowsLarkCli({ ...options,
    environment: { ...process.env, npm_command: 'exec', npm_config_ignore_scripts: 'true' },
    execFileSyncImpl(command, args, opts) {
      calls.push([command, args]);
      if (command === process.execPath) return execFileSync(command, args, opts);
      assert.equal(command, options.bin);
      assert.deepEqual(args, ['--version']);
      return 'lark-cli version 1.0.94\r\n';
    },
  });
  assert.equal(result.version, '1.0.94');
  assert.equal(calls.length, 2);
  assert.equal(calls[0][0], process.execPath);
  assert.ok(fs.existsSync(options.bin));
});

test('an existing executable is validated without another download', t => {
  const options = fixture(t); fs.writeFileSync(options.bin, 'fixture');
  let calls = 0;
  assert.equal(ensureWindowsLarkCli({ ...options, execFileSyncImpl(command) {
    calls++; assert.equal(command, options.bin); return 'lark-cli version 1.0.94';
  } }).bin, options.bin);
  assert.equal(calls, 1);
});

test('download/checksum errors and successful installers that produce no executable fail closed', t => {
  const options = fixture(t);
  assert.throws(() => ensureWindowsLarkCli({ ...options, execFileSyncImpl() { throw new Error('checksum mismatch'); } }), /Could not install/);
  assert.throws(() => ensureWindowsLarkCli({ ...options, execFileSyncImpl() {} }), /did not produce/);
});

test('non-executable, wrong-version and unrecognized binary output are rejected', t => {
  const options = fixture(t); fs.writeFileSync(options.bin, 'fixture');
  assert.throws(() => ensureWindowsLarkCli({ ...options, execFileSyncImpl() { throw new Error('ENOEXEC'); } }), /cannot execute/);
  for (const output of ['lark-cli version 1.0.93', 'unrelated CLI 1.0.94', '']) {
    assert.throws(() => ensureWindowsLarkCli({ ...options, execFileSyncImpl: () => output }), /version mismatch/);
  }
});

test('non-Windows invocation never installs or modifies anything', () => {
  assert.throws(() => ensureWindowsLarkCli({ platform: 'linux', execFileSyncImpl() { assert.fail('must not run'); } }), /Windows-only/);
});

test('offline validator fails on a missing binary without invoking an installer', t => {
  const options = fixture(t);
  assert.throws(() => validateWindowsLarkCli({ ...options, execFileSyncImpl() { assert.fail('must not execute'); } }), /is missing/);
  fs.writeFileSync(options.bin, 'fixture');
  assert.equal(validateWindowsLarkCli({ ...options, execFileSyncImpl(command, args) {
    assert.equal(command, options.bin); assert.deepEqual(args, ['--version']); return 'lark-cli version 1.0.94';
  } }).version, '1.0.94');
});
