// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
// DSH runtime detection: the version gate, the protocol handshake, the route it
// reports, and the `*_BIN` lookup contract. Kept out of the upstream
// detect.test.mjs, which only gains the one `dsh` row its runtime list needs.
// The trailing OpenCode case lives here because it measures the same
// `locateBinary` contract the DSH case first fixed: a bare `*_BIN` is a PATH
// lookup, never `$CWD/<name>`.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { detectDsh, detectRuntimes } from '../src/runtimes.mjs';

function tempDir(t, prefix) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return directory;
}

test('DSH: requires a supported binary and a successful bridge handshake', async t => {
  const bin = tempDir(t, 'superagent-dsh-detect-');
  fs.writeFileSync(path.join(bin, 'dsh'), '#!/bin/sh\n', { mode: 0o755 });
  let probeCalls = 0;
  const options = {
    env: { PATH: bin, DEEPSEEK_API_KEY: 'test-only' },
    execFileImpl: async (_command, args) => { assert.deepEqual(args, ['--version']); return { stdout: '0.2.2-rc.1\n' }; },
    probe: async (_command, { env }) => {
      probeCalls += 1;
      assert.equal(env.DEEPSEEK_API_KEY, 'test-only');
      return { compatible: true, route: 'deepseek-official/deepseek-flash' };
    },
  };
  const runtime = await detectDsh(options);
  assert.equal(runtime.available, true);
  assert.equal(runtime.bin, path.join(bin, 'dsh'));
  assert.equal(probeCalls, 1);
  // The runtime is no longer labelled experimental: DSH is a first-class agent.
  assert.equal(runtime.label, 'DeepSeek Harness');
  assert.doesNotMatch(runtime.label, /实验|experimental/);
  assert.doesNotMatch(runtime.note, /实验|experimental/);
  assert.match(runtime.note, /deepseek-official\/deepseek-flash/);
  options.execFileImpl = async () => ({ stdout: '0.3.0\n' });
  assert.equal((await detectDsh(options)).available, false);
  assert.equal(probeCalls, 1, 'version gate precedes handshake');
  options.execFileImpl = async () => ({ stdout: '0.2.2\n' });
  options.probe = async () => ({ compatible: false, reason: 'bad bridge' });
  assert.match((await detectDsh(options)).note, /bad bridge/);
  assert.equal((await detectDsh({ env: { PATH: '' } })).installed, false);
});

test('DSH: a route in the bridge catalogue makes the runtime available without DEEPSEEK_API_KEY', async t => {
  const bin = tempDir(t, 'superagent-dsh-route-');
  fs.writeFileSync(path.join(bin, 'dsh'), '#!/bin/sh\n', { mode: 0o755 });
  const probed = [];
  const runtime = await detectDsh({
    env: { PATH: bin },
    execFileImpl: async () => ({ stdout: '0.2.2-rc.1\n' }),
    probe: async (_command, { env }) => {
      probed.push(env);
      assert.equal(env.DEEPSEEK_API_KEY, undefined, 'no placeholder key is invented in the probe environment');
      return { compatible: true, route: 'deepseek/deepseek-chat', note: '' };
    },
  });
  assert.equal(runtime.available, true, runtime.note);
  assert.equal(probed.length, 1);
  assert.match(runtime.note, /deepseek\/deepseek-chat/);
  assert.doesNotMatch(runtime.note, /实验|experimental/);
});

test('DSH: without a servable route the note stays about the protocol', async t => {
  const bin = tempDir(t, 'superagent-dsh-nokey-');
  fs.writeFileSync(path.join(bin, 'dsh'), '#!/bin/sh\n', { mode: 0o755 });
  const runtime = await detectDsh({
    env: { PATH: bin },
    execFileImpl: async () => ({ stdout: '0.2.2-rc.1\n' }),
    // The catalogue answered, but nothing in it can run a turn.
    probe: async () => ({
      compatible: true, route: '', fallbackRoute: 'deepseek-official/deepseek-flash',
      note: 'deepseek-official: 0 models (invalid api key)',
    }),
  });
  // The protocol works, so the runtime stays selectable: the probe runs in a
  // throwaway DSH home and can never see the routes a real instance has, so
  // hiding the runtime behind this catalogue would hide a configured instance.
  assert.equal(runtime.available, true);
  assert.match(runtime.note, /ov-superagent dsh model list/, 'routes are pointed at DSH, not at an OpenViking SuperAgent file');
  assert.match(runtime.note, /DEEPSEEK_API_KEY/);
  assert.match(runtime.note, /deepseek-official: 0 models/, 'the catalogue answer is surfaced');
  assert.doesNotMatch(runtime.note, /暂无可用模型|no usable model yet/);
});

test('DSH: a secrets-file key reaches the probe without a placeholder', async t => {
  const bin = tempDir(t, 'superagent-dsh-secret-');
  fs.writeFileSync(path.join(bin, 'dsh'), '#!/bin/sh\n', { mode: 0o755 });
  const home = tempDir(t, 'superagent-dsh-home-');
  fs.mkdirSync(path.join(home, '.ov-superagent'));
  fs.writeFileSync(path.join(home, '.ov-superagent', 'secrets.env'), 'DEEPSEEK_API_KEY=sk-dsh-test\n');
  let probeEnv = null;
  const runtime = await detectDsh({
    env: { PATH: bin, HOME: home },
    execFileImpl: async () => ({ stdout: '0.2.2-rc.1\n' }),
    probe: async (_command, { env }) => { probeEnv = env; return { compatible: true, route: 'deepseek-official/deepseek-flash' }; },
  });
  assert.equal(runtime.available, true);
  assert.equal(probeEnv.DEEPSEEK_API_KEY, 'sk-dsh-test');
});

// B-3: `DSH_BIN=dsh` and `OPENCODE_BIN=opencode` are bare command names, so
// they must go through the PATH search. Resolving them against the working
// directory looked for `$CWD/dsh`, reported a broken install for a binary that
// is on PATH, and pinned that non-existent path into the manifest.
test('DSH: a bare DSH_BIN is a PATH lookup, never a path relative to the cwd', async (t) => {
  const bin = tempDir(t, 'superagent-dsh-bare-');
  fs.writeFileSync(path.join(bin, 'dsh'), '#!/bin/sh\n', { mode: 0o755 });
  const seen = [];
  const runtime = await detectDsh({
    env: { PATH: bin, DSH_BIN: 'dsh' },
    execFileImpl: async (command) => { seen.push(command); return { stdout: '0.2.2-rc.1\n' }; },
    probe: async () => ({ compatible: true, route: 'deepseek-official/deepseek-flash' }),
  });
  assert.equal(runtime.bin, path.join(bin, 'dsh'), 'the PATH hit is what gets pinned, not $CWD/dsh');
  assert.notEqual(runtime.bin, path.resolve('dsh'));
  assert.deepEqual(seen, [path.join(bin, 'dsh')]);
  assert.equal(runtime.available, true);
});

test('DSH: a bare DSH_BIN that is not on PATH is reported as not installed', async () => {
  const runtime = await detectDsh({
    env: { PATH: '', DSH_BIN: 'dsh' },
    execFileImpl: async () => { throw new Error('a missing binary must not be probed'); },
  });
  assert.equal(runtime.installed, false);
  assert.equal(runtime.bin, null);
});

test('OpenCode: a bare OPENCODE_BIN is a PATH lookup, never a path relative to the cwd', async (t) => {
  const bin = tempDir(t, 'superagent-opencode-bare-');
  fs.writeFileSync(path.join(bin, 'opencode'), '#!/bin/sh\n', { mode: 0o755 });
  const seen = [];
  const runtime = (await detectRuntimes({
    env: { PATH: bin, OPENCODE_BIN: 'opencode' }, piBin: '/managed/pi.js',
    execFileImpl: async (command) => {
      if (command.endsWith('opencode')) seen.push(command);
      return { stdout: 'opencode version 1.19.5\n' };
    },
    acpProbe: async () => ({ compatible: true }),
  })).find(({ id }) => id === 'opencode');
  assert.equal(runtime.bin, path.join(bin, 'opencode'));
  assert.notEqual(runtime.bin, path.resolve('opencode'));
  assert.deepEqual(seen, [path.join(bin, 'opencode')]);
});
