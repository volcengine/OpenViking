// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// How the daemon starts its `dsh` binary. npm installs the CLI as a .cmd shim on
// Windows, which Node's CreateProcess cannot execute: a raw child_process.spawn
// throws EINVAL before the process exists, so the daemon start-looped with
// `daemon_start_failed: Error: spawn EINVAL` and setup reported that the
// instance never came up. The daemon uses the platform launcher instead, as the
// Codex, Claude Code, OpenCode and Pi runtimes already do.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, it } from './dsh-node-gate.mjs';
import { spawn as platformSpawn } from '../src/platform/index.mjs';
import { DshAgent } from '../src/dsh.mjs';

const logger = { warn() {}, info() {}, error() {}, child() { return logger; } };
const config = (overrides = {}) => ({ dataDir: os.tmpdir(), workspace: os.tmpdir(), ...overrides });

describe('the DSH runtime launches its binary through the platform launcher', () => {
  it('does not default to a raw child_process.spawn', () => {
    const agent = new DshAgent(config(), {}, logger);
    assert.equal(agent._spawn, platformSpawn, 'a raw spawn cannot run an npm shim on Windows');
  });

  it('probes the version through an npm-style .cmd shim', { skip: process.platform !== 'win32', timeout: 30_000 }, async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-dsh-shim-'));
    try {
      const entry = path.join(directory, 'node_modules', 'fakedsh', 'lib', 'bin.js');
      fs.mkdirSync(path.dirname(entry), { recursive: true });
      fs.writeFileSync(entry, "process.stdout.write('0.2.2-rc.1\\n');\n");
      // The shim npm writes for a global CLI: the platform launcher reads the
      // entrypoint it names instead of handing the .cmd to CreateProcess.
      const shim = path.join(directory, 'dsh.cmd');
      fs.writeFileSync(shim, [
        '@ECHO off',
        'SETLOCAL',
        'SET "_prog=node"',
        'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & set PATHEXT=%PATHEXT:;.JS;=;% & "%_prog%"  "%dp0%\\node_modules\\fakedsh\\lib\\bin.js" %*',
        '',
      ].join('\r\n'));
      const agent = new DshAgent(
        config({ dshBin: shim, dataDir: directory, workspace: directory }), {}, logger,
      );
      assert.equal(await agent._probeVersion(process.env), '0.2.2-rc.1');
    } finally {
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });
});
