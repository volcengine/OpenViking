// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import test from 'node:test';
import { detectRuntimes } from '../src/runtimes.mjs';

test('Antigravity detection reports the probed version instead of an undefined label', async () => {
  const calls = [];
  const execFileImpl = async (command, args) => {
    calls.push(args.join(' '));
    if (args[0] === '--version') return { stdout: '1.5.3\n', stderr: '' };
    if (args[0] === 'models') return { stdout: '[]\n', stderr: '' };
    throw new Error(`unexpected ${command} ${args.join(' ')}`);
  };
  const runtimes = await detectRuntimes({
    env: { AGY_BIN: 'C:/fake/agy.exe', HOME: 'C:/fake-home', PATH: '' },
    execFileImpl,
    agyProbe: async () => ({ compatible: true }),
  });
  const agy = runtimes.find((runtime) => runtime.id === 'agy');
  assert.equal(agy.available, true);
  assert.match(agy.note, /1\.5\.3/);
  assert.ok(calls.includes('models'));
});
