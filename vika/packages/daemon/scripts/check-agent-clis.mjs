#!/usr/bin/env node
// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { agentCliCommands } from '../src/agent-clis.mjs';
import { sanitizeAgentChildEnv } from '../src/process-utils.mjs';

// Release smoke: no global CLI, user profile, npm PATH, or package-root cwd.
const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-cli-smoke-'));
try {
  const workspace = path.join(home, 'workspace');
  fs.mkdirSync(workspace);
  const env = sanitizeAgentChildEnv({ HOME: home, PATH: '/usr/bin:/bin', SLACK_DISABLE_TELEMETRY: 'true' });
  for (const shell of ['/bin/sh', '/bin/bash']) {
    for (const { name, bin, versionArgs } of agentCliCommands()) {
      assert.ok(path.isAbsolute(bin), `${name} must be bundled`);
      const output = execFileSync(shell, ['-c', 'command -v "$1" && "$@"', 'vika-cli-smoke', name, ...versionArgs], {
        cwd: workspace, env, encoding: 'utf8', timeout: 15_000,
      }).trim();
      const [resolved, ...version] = output.split('\n');
      assert.equal(fs.realpathSync(resolved), fs.realpathSync(bin), `${shell} must use the pinned ${name}`);
      process.stdout.write(`${shell}: ${name} -> ${resolved}: ${version.join(' ')}\n`);
    }
  }
} finally { fs.rmSync(home, { recursive: true, force: true }); }
