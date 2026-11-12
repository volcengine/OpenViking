// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// How the short-lived DSH control process (the `ov-superagent dsh model`
// commands) starts its `dsh` binary. npm installs the CLI as a .cmd shim on
// Windows, which Node's CreateProcess cannot execute: a raw
// child_process.spawn throws EINVAL before the process exists. The session
// runtime moved to the platform launcher in #7394; the control client must use
// the same launcher or configuration commands hit the identical failure.
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { spawn as platformSpawn } from '../src/platform/index.mjs';
import * as windows from '../src/platform/win32.mjs';
import { DshControlClient } from '../src/dsh-control.mjs';

function instanceDataDir(t) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-dsh-control-spawn-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  fs.chmodSync(dataDir, 0o700);
  return dataDir;
}

// A minimal bridge peer: enough of the v2 surface for DshControlClient.open to
// finish its hello handshake, so the Windows test proves a real subprocess
// started out of the shim.
const BRIDGE_PEER = `
let buffer = '';
const METHODS = ['session/steer', 'session/cancel', 'superagent/llm/models',
  'superagent/llm/model/info', 'superagent/session/model/select',
  'superagent/settings/describe', 'superagent/settings/mutate',
  'superagent/credentials/set', 'superagent/credentials/unset'];
process.stdin.on('data', (chunk) => {
  buffer += chunk;
  for (let index; (index = buffer.indexOf('\\n')) >= 0;) {
    const line = buffer.slice(0, index);
    buffer = buffer.slice(index + 1);
    if (!line.trim()) continue;
    let frame;
    try { frame = JSON.parse(line); } catch { continue; }
    if (frame.method === 'shutdown') { process.exit(0); }
    process.stdout.write(JSON.stringify({
      jsonrpc: '2.0', id: frame.id,
      result: { bridge: 'superagent-dsh-bridge', version: 2, methods: METHODS },
    }) + '\\n');
  }
});
process.stdin.on('end', () => process.exit(0));
`;

test('the control client defaults to the platform launcher, not a raw child_process.spawn', async (t) => {
  // Resolve the spawnImpl default parameter the same way DshAgent's test does:
  // on POSIX the platform launcher re-exports child_process.spawn, so the
  // assertion pins the import indirection #7394 established and fails if a raw
  // node:child_process import returns as the default.
  const dataDir = instanceDataDir(t);
  const seen = [];
  const recordingSpawn = (command, args, options) => {
    seen.push({ command, args, options });
    // Fail the handshake immediately so open() settles without a bridge peer.
    const child = new EventEmitter();
    child.pid = 12345;
    child.kill = () => {};
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.stdin = new EventEmitter();
    child.stdin.write = () => true;
    child.stdin.end = () => {};
    child.stderr.setEncoding = () => {};
    queueMicrotask(() => child.emit('error', new Error('recorded only')));
    return child;
  };
  await assert.rejects(
    () => DshControlClient.open({ dataDir, spawnImpl: recordingSpawn }),
    /recorded only/,
  );
  assert.equal(seen.length, 1);
  assert.equal(seen[0].command, 'dsh');
  assert.deepEqual(seen[0].args.slice(0, 2), ['--profile', 'sdk']);
  // The default the module uses when no spawnImpl is supplied.
  const moduleSource = fs.readFileSync(new URL('../src/dsh-control.mjs', import.meta.url), 'utf8');
  assert.match(moduleSource, /import \{ spawn, spawnDetached \} from '\.\/platform\/index\.mjs'/);
  assert.doesNotMatch(moduleSource, /from 'node:child_process'/);
  assert.equal(typeof platformSpawn, 'function');
});

test('a Windows npm .cmd shim resolves to its Node entrypoint for the control launch', (t) => {
  // Runs on every platform: win32.executableInvocation only reads the shim
  // text and the entrypoint file, so the EINVAL fix's shim resolution is
  // guarded in Linux CI even though the CreateProcess failure itself is
  // Windows-only.
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-dsh-control-shim-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const entry = path.join(directory, 'node_modules', '@deepseek-ai', 'dsh', 'bin', 'dsh.js');
  fs.mkdirSync(path.dirname(entry), { recursive: true });
  fs.writeFileSync(entry, '// fixture dsh entrypoint\n');
  const shim = path.join(directory, 'dsh.cmd');
  const args = ['--profile', 'sdk', '--patch', 'control-patch.yml'];
  fs.writeFileSync(shim, [
    '@ECHO off',
    'SETLOCAL',
    'SET "_prog=node"',
    'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & set PATHEXT=%PATHEXT:;.JS;=;% & "%_prog%"  "%dp0%\\node_modules\\@deepseek-ai\\dsh\\bin\\dsh.js" %*',
    '',
  ].join('\r\n'));
  assert.deepEqual(
    windows.executableInvocation(shim, args, { env: process.env }),
    { command: process.execPath, args: [entry, ...args] },
  );
  // A batch file that is not an npm shim must never be handed to cmd.exe.
  fs.writeFileSync(shim, '@echo off\r\necho arbitrary script\r\n');
  assert.throws(
    () => windows.executableInvocation(shim, args, { env: process.env }),
    /Unsupported Windows command shim/,
  );
});

test('the control process starts through an npm-style .cmd shim', { skip: process.platform !== 'win32', timeout: 30_000 }, async (t) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-dsh-control-e2e-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const entry = path.join(directory, 'node_modules', 'fakedsh', 'bin', 'dsh.js');
  fs.mkdirSync(path.dirname(entry), { recursive: true });
  fs.writeFileSync(entry, BRIDGE_PEER);
  // The shim npm writes for a global CLI: the platform launcher reads the
  // entrypoint it names instead of handing the .cmd to CreateProcess, which is
  // exactly the spawn EINVAL a raw child_process.spawn produced here.
  const shim = path.join(directory, 'dsh.cmd');
  fs.writeFileSync(shim, [
    '@ECHO off',
    'SETLOCAL',
    'SET "_prog=node"',
    'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & set PATHEXT=%PATHEXT:;.JS;=;% & "%_prog%"  "%dp0%\\node_modules\\fakedsh\\bin\\dsh.js" %*',
    '',
  ].join('\r\n'));
  const dataDir = instanceDataDir(t);
  const client = await DshControlClient.open({
    dataDir,
    bin: shim,
    env: { ...process.env, PATH: `${directory}${path.delimiter}${process.env.PATH ?? ''}` },
    helloTimeoutMs: 15_000,
  });
  t.after(() => client.close());
  const hello = await client.hello();
  assert.equal(hello.bridge, 'superagent-dsh-bridge');
  assert.ok(hello.methods.includes('superagent/settings/mutate'));
  await client.close();
  assert.ok(client.exited, 'the control process started from the shim must exit cleanly');
});
