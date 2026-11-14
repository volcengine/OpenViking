// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { githubConfig, prepareGitHub, writeGitIsolation } from '../src/github.mjs';

function fixture(t, { tty = true } = {}) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-github-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const messages = [];
  return {
    home,
    env: { HOME: home, PATH: '/bin', GH_TOKEN: 'host-token', GITHUB_TOKEN: 'host-token-too' },
    prompter: {
      tty,
      info: (text) => messages.push(['info', text]),
      warn: (text) => messages.push(['warn', text]),
      success: (text) => messages.push(['success', text]),
      confirm: async () => true,
    },
    messages,
  };
}

test('GitHub config is private to OVSA and contains no token', (t) => {
  const f = fixture(t);
  const config = githubConfig(f.env);
  assert.equal(config.ghDir, path.join(f.home, '.ov-superagent', 'github', 'gh'));
  assert.deepEqual(config.env, {
    GH_CONFIG_DIR: config.ghDir,
    GIT_CONFIG_GLOBAL: config.gitConfig,
    GIT_CONFIG_NOSYSTEM: '1',
  });
  writeGitIsolation(config);
  const gitconfig = fs.readFileSync(config.gitConfig, 'utf8');
  assert.match(gitconfig, /helper = !gh auth git-credential/);
  assert.doesNotMatch(gitconfig, /host-token/);
  if (process.platform !== 'win32') {
    assert.equal(fs.statSync(config.root).mode & 0o777, 0o700);
    assert.equal(fs.statSync(config.ghDir).mode & 0o777, 0o700);
    assert.equal(fs.statSync(config.gitConfig).mode & 0o777, 0o600);
  }
});

test('an existing isolated login is reused without a prompt and host tokens are removed', async (t) => {
  const f = fixture(t);
  f.prompter.confirm = async () => { throw new Error('must not prompt'); };
  const calls = [];
  const result = await prepareGitHub({
    ...f,
    whichImpl: () => '/opt/gh/bin/gh',
    runImpl: async (...args) => calls.push(args),
    execFileImpl: async (_command, args, options) => {
      assert.deepEqual(args, ['api', 'user', '--jq', '.login']);
      assert.equal(options.env.GH_TOKEN, undefined);
      assert.equal(options.env.GITHUB_TOKEN, undefined);
      return { stdout: 'ovsa-bot\n' };
    },
  });
  assert.deepEqual(result, githubConfig(f.env).env);
  assert.deepEqual(calls[0].slice(0, 2), ['/opt/gh/bin/gh', ['auth', 'status', '--hostname', 'github.com']]);
  assert.equal(calls[0][2].env.GH_CONFIG_DIR, githubConfig(f.env).ghDir);
  assert.match(f.messages[0][1], /ovsa-bot/);
});

test('setup uses device flow only against the isolated gh config', async (t) => {
  const f = fixture(t);
  const calls = [];
  const result = await prepareGitHub({
    ...f,
    whichImpl: () => '/usr/bin/gh',
    runImpl: async (command, args, options) => {
      calls.push({ command, args, options });
      if (args[1] === 'status' && calls.filter((call) => call.args[1] === 'status').length === 1) throw new Error('not logged in');
    },
  });
  assert.deepEqual(result, githubConfig(f.env).env);
  const login = calls.find((call) => call.args[1] === 'login');
  assert.deepEqual(login.args, ['auth', 'login', '--hostname', 'github.com', '--git-protocol', 'https', '--web', '--skip-ssh-key']);
  assert.equal(login.options.stdio, 'inherit');
  assert.equal(login.options.env.GH_CONFIG_DIR, githubConfig(f.env).ghDir);
  assert.equal(login.options.env.GH_TOKEN, undefined);
  assert.equal(login.options.env.GITHUB_TOKEN, undefined);
  assert.equal(fs.existsSync(path.join(f.home, '.config', 'gh')), false);
});

test('missing gh, headless setup and a failed login degrade without blocking setup', async (t) => {
  const missing = fixture(t);
  assert.equal(await prepareGitHub({ ...missing, whichImpl: () => null }), null);
  assert.match(missing.messages[0][1], /没有找到 gh/);

  const headless = fixture(t, { tty: false });
  assert.equal(await prepareGitHub({ ...headless, whichImpl: () => '/usr/bin/gh',
    runImpl: async () => { throw new Error('not logged in'); } }), null);
  assert.deepEqual(headless.messages, []);

  const failed = fixture(t);
  assert.equal(await prepareGitHub({ ...failed, whichImpl: () => '/usr/bin/gh',
    runImpl: async () => { throw new Error('device authorization failed'); } }), null);
  assert.match(failed.messages.at(-1)[1], /其他设置继续/);
});
