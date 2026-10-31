// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { bundledLarkCli } from '../src/lark-cli-bin.mjs';
import { bundledSlackCli, SLACK_CLI_VERSION } from '../src/slack/cli.mjs';
import { agentCliCommands } from '../src/agent-clis.mjs';
import { sanitizeCodexEnv } from '../src/codex.mjs';
import { sanitizeClaudeEnv } from '../src/claude.mjs';
import { sanitizePiEnv } from '../src/pi.mjs';
import { sanitizeTraexEnv } from '../src/traex.mjs';
import { sanitizeAgentChildEnv, sanitizeNonArkChildEnv } from '../src/process-utils.mjs';

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-agent-cli-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const workspace = path.join(root, 'workspace');
  const systemBin = path.join(root, 'system-bin');
  fs.mkdirSync(workspace);
  fs.mkdirSync(systemBin);
  return { root, workspace, systemBin };
}

function executable(file, output) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, `#!/bin/sh\nprintf '%s\\n' '${output}'\n`, { mode: 0o755 });
}

function shell(command, env, cwd) {
  const result = spawnSync('/bin/sh', ['-c', command], { env, cwd, encoding: 'utf8' });
  assert.ifError(result.error);
  return result;
}

for (const layout of ['source', 'release']) {
  test(`agent shell resolves the pinned CLI from a ${layout} outside the workspace`, (t) => {
    const { root, workspace, systemBin } = fixture(t);
    const install = layout === 'source' ? path.join(root, 'checkout', 'vika') : path.join(root, '.vika', 'releases', '1.0');
    const relativeBin = 'node_modules/@larksuite/cli/bin/lark-cli';
    executable(path.join(install, relativeBin), 'pinned');
    executable(path.join(install, 'bin', 'slack'), 'slack-pinned');
    const current = path.join(root, '.vika', 'current');
    if (layout === 'release') fs.symlinkSync(install, current, 'dir');
    const larkCliBin = path.join(layout === 'release' ? current : install, relativeBin);
    const slackCliBin = path.join(layout === 'release' ? current : install, 'bin', 'slack');
    const inherited = { HOME: root, PATH: systemBin };
    assert.notEqual(shell('command -v lark-cli', inherited, workspace).status, 0);
    const env = sanitizeAgentChildEnv(inherited, { cliBins: agentCliCommands({ larkCliBin, slackCliBin }).map(({ bin }) => bin) });
    const result = shell('command -v lark-cli && lark-cli --version && command -v slack && slack version', env, workspace);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout, `${larkCliBin}\npinned\n${slackCliBin}\nslack-pinned\n`);
    assert.equal(inherited.PATH, systemBin);
  });
}

test('the configured CLI takes precedence over a global CLI without duplicating its directory', (t) => {
  const { root, workspace, systemBin } = fixture(t);
  const larkCliBin = path.join(root, 'custom tools', 'lark-cli');
  executable(larkCliBin, 'configured');
  executable(path.join(systemBin, 'lark-cli'), 'global');
  const env = sanitizeAgentChildEnv({ PATH: [systemBin, path.dirname(larkCliBin)].join(path.delimiter) }, { cliBins: [larkCliBin] });
  assert.equal(env.PATH, [path.dirname(larkCliBin), systemBin].join(path.delimiter));
  assert.equal(shell('lark-cli --version', env, workspace).stdout, 'configured\n');
  assert.equal(sanitizeAgentChildEnv(env, { cliBins: [larkCliBin] }).PATH, env.PATH);
});

test('additional bundled CLIs share discovery without runtime-specific PATH changes', (t) => {
  const { root, workspace, systemBin } = fixture(t);
  const bin = path.join(root, 'future-cli', 'enterprise-chat');
  executable(bin, 'future-pinned');
  const env = sanitizeAgentChildEnv({ PATH: systemBin }, { cliBins: [bin] });
  assert.equal(shell('enterprise-chat --version', env, workspace).stdout, 'future-pinned\n');
});

for (const sanitize of [sanitizeCodexEnv, sanitizeClaudeEnv, sanitizePiEnv, sanitizeTraexEnv]) {
  test(`${sanitize.name} uses configured CLIs and the daemon profile from an unrelated workspace`, (t) => {
    const { root, workspace, systemBin } = fixture(t);
    const config = { larkCliBin: path.join(root, 'lark', 'lark-cli'), slackCliBin: path.join(root, 'slack', 'slack') };
    executable(config.larkCliBin, 'lark');
    executable(config.slackCliBin, 'slack');
    const env = sanitize({ HOME: root, PATH: systemBin, LARKSUITE_CLI_PROFILE: 'wrong' }, 'fixed', null, config);
    const result = shell('lark-cli --version && slack version; printf "%s\\n" "$LARKSUITE_CLI_PROFILE"', env, workspace);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout, 'lark\nslack\nfixed\n');
  });
}

test('missing or empty PATH retains system commands without adding the workspace to PATH', (t) => {
  const { root, workspace } = fixture(t);
  const larkCliBin = path.join(root, 'bin', 'lark-cli');
  executable(larkCliBin, 'configured');
  for (const inherited of [{}, { PATH: '' }]) {
    const env = sanitizeAgentChildEnv(inherited, { cliBins: [larkCliBin] });
    assert.ok(!env.PATH.split(path.delimiter).includes(''));
    assert.equal(shell('sh -c "lark-cli --version"', env, workspace).stdout, 'configured\n');
  }
});

test('bare CLI names do not add the current directory to PATH', () => {
  for (const larkCliBin of ['lark-cli', 'custom-lark']) {
    const env = sanitizeAgentChildEnv({ PATH: '/usr/bin:/bin' }, { cliBins: [larkCliBin] });
    assert.equal(env.PATH, '/usr/bin:/bin');
  }
});

test('CLI discovery preserves the agent credential boundary and leaves connector PATH alone', () => {
  const inherited = {
    PATH: '/usr/bin:/bin', LARK_CLI_BIN: '/ignored/lark-cli', LARK_APP_SECRET: 'secret',
    FEISHU_TOKEN: 'secret', LARKSUITE_CLI_PROFILE: 'wrong', VIKA_PRIVATE: 'secret',
    ARK_API_KEY: 'secret', VOLCENGINE_TOKEN: 'secret', SESSION_ID: 'secret', CLAUDECODE: 'nested',
    OPENAI_API_KEY: 'model-key', OPENVIKING_CLI_CONFIG_FILE: '/tmp/ov.conf',
  };
  const before = { ...inherited };
  const env = sanitizeAgentChildEnv(inherited, {
    cliBins: ['/configured/lark-cli'], profile: 'fixed', stripClaudeCode: true,
    actions: { socketPath: '/tmp/actions.sock', authority: 'authority' },
  });
  assert.deepEqual(Object.keys(env).filter(key => /^(LARK|FEISHU|LARKSUITE|VIKA|ARK|VOLCENGINE)_/.test(key)), ['LARKSUITE_CLI_PROFILE']);
  assert.equal(env.LARKSUITE_CLI_PROFILE, 'fixed');
  assert.equal(env.CLAUDECODE, undefined);
  assert.equal(env.SESSION_ID, undefined);
  assert.equal(env.OPENAI_API_KEY, 'model-key');
  assert.equal(env.OPENVIKING_CLI_CONFIG_FILE, '/tmp/ov.conf');
  assert.equal(env.PERSONAL_AGENT_SOCKET, '/tmp/actions.sock');
  assert.equal(env.PERSONAL_AGENT_AUTHORITY, 'authority');
  assert.equal(sanitizeNonArkChildEnv(inherited).PATH, inherited.PATH);
  assert.deepEqual(inherited, before);
});

const bundled = bundledLarkCli();
test('the installed bundled CLI runs --version in an agent shell', {
  skip: !path.isAbsolute(bundled) && 'run scripts/install-lark-cli.mjs to install the pinned binary',
}, (t) => {
  const { root, workspace, systemBin } = fixture(t);
  const env = sanitizeAgentChildEnv({ HOME: root, PATH: systemBin });
  const result = shell('command -v lark-cli && lark-cli --version', env, workspace);
  const { version } = createRequire(import.meta.url)('@larksuite/cli/package.json');
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), `${bundled}\nlark-cli version ${version}`);
});

test('the installed Slack CLI runs version in an agent shell', {
  skip: !fs.existsSync(bundledSlackCli()) && 'run scripts/install-slack-cli.mjs to install the pinned binary',
}, (t) => {
  const { root, workspace, systemBin } = fixture(t);
  const env = sanitizeAgentChildEnv({ HOME: root, PATH: systemBin, SLACK_DISABLE_TELEMETRY: 'true' });
  const result = shell('command -v slack && slack version --skip-update --no-color', env, workspace);
  assert.equal(result.status, 0, result.stderr);
  assert.ok(result.stdout.startsWith(`${bundledSlackCli()}\n`));
  assert.ok(result.stdout.includes(SLACK_CLI_VERSION), result.stdout);
});
