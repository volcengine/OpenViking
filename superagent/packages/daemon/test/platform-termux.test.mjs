// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and the OpenViking contributors.
// SPDX-License-Identifier: AGPL-3.0
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import * as posix from '../src/platform/posix.mjs';
import * as termux from '../src/platform/termux.mjs';
import { bundledLarkCli } from '../src/lark-cli-bin.mjs';
import { bundledRipgrep } from '../src/ripgrep-bin.mjs';
import { bundledSlackCli } from '../src/slack/cli.mjs';
import { sanitizeAgentChildEnv } from '../src/process-utils.mjs';
import { allowedPm2Environment } from '../../updater/src/pm2-platform.mjs';

const runtimeEnv = { PREFIX: '/termux/usr', TMPDIR: '/termux/usr/tmp',
  LD_PRELOAD: '/termux/usr/lib/libtermux-exec.so', LD_LIBRARY_PATH: '/termux/usr/lib' };

test('Termux inherits POSIX semantics except identity, services, environment and URL opener', () => {
  const overrides = new Set(['hostPlatformId', 'loginService', 'systemEnvironmentKeys', 'urlOpenCommand']);
  for (const key of Object.keys(posix).filter(key => !overrides.has(key))) assert.equal(termux[key], posix[key], key);
  assert.equal(termux.hostPlatformId, `termux-${process.arch}`);
  assert.equal(termux.loginService, 'runit');
  assert.equal(termux.urlOpenCommand({}), 'termux-open-url');
  assert.deepEqual(termux.systemEnvironmentKeys, Object.keys(runtimeEnv));
  assert.ok(Object.isFrozen(termux.systemEnvironmentKeys));
});

test('Termux runtime environment survives PM2 filtering and agent sanitization', () => {
  const source = { ...runtimeEnv, PATH: '/native/bin', HOME: '/home/test', LARK_APP_SECRET: 'secret' };
  assert.deepEqual(allowedPm2Environment(source, ['PATH', 'HOME'], { implementation: termux }),
    { ...runtimeEnv, PATH: source.PATH, HOME: source.HOME });
  const child = sanitizeAgentChildEnv(source, { cliBins: [] });
  for (const [key, value] of Object.entries(runtimeEnv)) assert.equal(child[key], value);
  assert.equal(child.LARK_APP_SECRET, undefined);
});

test('Android tool defaults select executable PATH installs, never bundled Linux binaries', { skip: process.platform === 'win32' }, t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'termux-tools-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  for (const [name, select] of [['lark-cli', bundledLarkCli], ['rg', bundledRipgrep], ['slack', bundledSlackCli]]) {
    const bin = path.join(directory, name);
    fs.writeFileSync(bin, 'native executable fixture', { mode: 0o700 });
    assert.equal(select({ host: 'termux-arm64', env: { PATH: directory } }), bin);
    fs.chmodSync(bin, 0o600);
    assert.equal(select({ host: 'termux-arm64', env: { PATH: directory } }), name);
    assert.equal(select({ host: 'termux-arm64', env: { PATH: '' } }), name);
  }
});

test('Android selects the Termux facade before config loads and preserves explicit LARK_CLI_BIN', { skip: process.platform === 'win32' ? 'the Termux facade is selected by the host platform id, which this host is not' : false }, () => {
  const moduleUrl = relative => new URL(relative, import.meta.url).href;
  const script = `
    Object.defineProperty(process, 'platform', { value: 'android' });
    const assert = (await import('node:assert/strict')).default;
    const platform = await import(${JSON.stringify(moduleUrl('../src/platform/index.mjs'))});
    const { capabilitiesFor } = await import(${JSON.stringify(moduleUrl('../src/capabilities.mjs'))});
    assert.equal(platform.hostPlatformId, 'termux-' + process.arch);
    assert.equal(platform.loginService, 'runit');
    assert.equal(platform.urlOpenCommand(), 'termux-open-url');
    assert.equal(capabilitiesFor(platform).updater, false);
    assert.equal(capabilitiesFor(platform).autostart, true);
    const { getConfig } = await import(${JSON.stringify(moduleUrl('../src/config.mjs'))});
    process.env.LARK_CLI_BIN = '/custom tools/native-lark';
    assert.equal(getConfig().larkCliBin, process.env.LARK_CLI_BIN);
  `;
  execFileSync(process.execPath, ['--input-type=module', '-e', script], { env: { ...process.env } });
});

test('the PM2 POSIX wrapper falls back to PATH Node without a bundled runtime', { skip: process.platform === 'win32' }, t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'termux-pm2-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  fs.mkdirSync(path.join(directory, 'bin'));
  fs.mkdirSync(path.join(directory, 'node_modules/pm2/bin'), { recursive: true });
  fs.copyFileSync(new URL('../../../bin/pm2', import.meta.url), path.join(directory, 'bin/pm2'));
  fs.symlinkSync(process.execPath, path.join(directory, 'bin/node'));
  fs.writeFileSync(path.join(directory, 'node_modules/pm2/bin/pm2'), 'console.log(JSON.stringify(process.argv.slice(2)))');
  // Invoke the shell explicitly: Android need not provide /bin/sh or /usr/bin/env.
  const shell = posix.findExecutable('sh');
  assert.ok(shell);
  const output = execFileSync(shell, [path.join(directory, 'bin/pm2'), 'start', 'a b', '$HOME'], {
    encoding: 'utf8', env: { ...process.env, PATH: `${directory}/bin:${process.env.PATH}` },
  });
  assert.deepEqual(JSON.parse(output), ['start', 'a b', '$HOME']);
});
