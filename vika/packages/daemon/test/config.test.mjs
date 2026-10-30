// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { afterEach, describe, it } from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { getConfig, projectDir } from '../src/config.mjs';
import { managedPiBin } from '../src/pi-runtime.mjs';

const saved = new Map();

function setEnv(name, value) {
  if (!saved.has(name)) saved.set(name, process.env[name]);
  if (value === undefined) delete process.env[name];
  else process.env[name] = value;
}

afterEach(() => {
  for (const [name, value] of saved) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
  saved.clear();
});

describe('configuration safety', { concurrency: false }, () => {
  it('accepts QQ as the primary channel without putting credentials in config', () => {
    setEnv('VIKA_CHANNEL', 'qq');
    setEnv('VIKA_CONNECTORS_JSON', '["qq","telegram"]');
    const config = getConfig();
    assert.equal(config.channel, 'qq');
    assert.deepEqual(config.connectors, ['qq', 'telegram']);
    assert.equal(config.qqAppSecret, undefined);
  });

  it('moves temporary files independently of persistent data and ignores TMPDIR', () => {
    setEnv('VIKA_DATA_DIR', '/root/.local/share/vika-primary');
    setEnv('VIKA_WORKSPACE', '/work/vika-primary');
    setEnv('VIKA_TEMP_DIR', undefined);
    setEnv('TMPDIR', '/tmp/system-temp');
    const original = getConfig();
    assert.equal(original.tempDir, '/root/.local/share/vika-primary/tmp');
    setEnv('VIKA_TEMP_DIR', '/tmp/vika-primary/');
    const configured = getConfig();
    assert.equal(path.resolve(configured.tempDir), '/tmp/vika-primary');
    for (const field of ['dataDir', 'databasePath', 'lockPath', 'workspace']) {
      assert.equal(configured[field], original[field]);
    }
    setEnv('VIKA_TEMP_DIR', '');
    assert.equal(getConfig().tempDir, original.tempDir);
    setEnv('VIKA_TEMP_DIR', 'relative/tmp');
    assert.throws(() => getConfig(), /VIKA_TEMP_DIR must be an absolute path/);
  });

  it('accepts Telegram as primary and alongside existing connectors without putting keys in config', () => {
    setEnv('VIKA_CHANNEL', 'telegram');
    setEnv('VIKA_CONNECTORS_JSON', '["telegram","weixin"]');
    const config = getConfig();
    assert.equal(config.channel, 'telegram');
    assert.deepEqual(config.connectors, ['telegram', 'weixin']);
    assert.equal(config.telegramToken, undefined);
  });
  it('accepts Slack and a bot reply fuse that defaults to 20 and can be turned off', () => {
    setEnv('VIKA_CHANNEL', 'slack');
    setEnv('VIKA_CONNECTORS_JSON', '["slack","lark"]');
    setEnv('VIKA_BOT_REPLY_FUSE', undefined);
    const config = getConfig();
    assert.equal(config.channel, 'slack');
    assert.equal(config.botReplyFuse, 20);
    setEnv('VIKA_BOT_REPLY_FUSE', '0');
    assert.equal(getConfig().botReplyFuse, 0);
    setEnv('VIKA_BOT_REPLY_FUSE', '-1');
    assert.throws(() => getConfig(), /VIKA_BOT_REPLY_FUSE/);
  });
  it('defaults timer occurrence checks to five minutes and allows an override', () => {
    setEnv('VIKA_TIMER_OCCURRENCE_CHECK_DELAY_MS', undefined);
    assert.equal(getConfig().timerOccurrenceCheckDelayMs, 300_000);
    setEnv('VIKA_TIMER_OCCURRENCE_CHECK_DELAY_MS', '120000');
    assert.equal(getConfig().timerOccurrenceCheckDelayMs, 120_000);
    setEnv('VIKA_TIMER_OCCURRENCE_CHECK_DELAY_MS', '999');
    assert.throws(() => getConfig(), /VIKA_TIMER_OCCURRENCE_CHECK_DELAY_MS/);
  });
  it('keeps explicit silence off unless the instance opts in', () => {
    setEnv('VIKA_EXPLICIT_SILENCE', undefined);
    assert.equal(getConfig().explicitSilence, false);
    setEnv('VIKA_EXPLICIT_SILENCE', 'true');
    assert.equal(getConfig().explicitSilence, true);
  });
  it('keeps staged replies off unless the instance opts in', () => {
    setEnv('VIKA_STAGED_REPLIES', undefined);
    assert.equal(getConfig().stagedReplies, false);
    setEnv('VIKA_STAGED_REPLIES', 'true');
    assert.equal(getConfig().stagedReplies, true);
    setEnv('VIKA_STAGED_REPLIES', 'often');
    assert.throws(() => getConfig(), /VIKA_STAGED_REPLIES must be a boolean/);
    setEnv('VIKA_STAGED_REPLIES', undefined);
  });
  it('takes mailboxes only with private account files, and never as the primary IM', () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-email-config-'));
    const accountFile = path.join(directory, 'email.json');
    const otherFile = path.join(directory, 'other.json');
    fs.writeFileSync(accountFile, '{}', { mode: 0o644 });
    fs.writeFileSync(otherFile, '{}', { mode: 0o600 });
    setEnv('VIKA_CHANNEL', 'lark');
    setEnv('VIKA_CONNECTORS_JSON', '["lark","email"]');
    const accounts = (list) => setEnv('VIKA_EMAIL_ACCOUNTS_JSON', JSON.stringify(list));
    try {
      setEnv('VIKA_EMAIL_ACCOUNTS_JSON', undefined);
      assert.throws(() => getConfig(), /VIKA_EMAIL_ACCOUNTS_JSON must list the mailboxes/);
      accounts([{ id: 'email-gmail', config: accountFile }]);
      assert.throws(() => getConfig(), /email account file of email-gmail must have mode 0600/);
      fs.chmodSync(accountFile, 0o600);
      accounts([{ id: 'email-gmail', config: accountFile }, { id: 'email-gmail', config: otherFile }]);
      assert.throws(() => getConfig(), /invalid or repeated mailbox id/);
      accounts([{ id: 'gmail', config: accountFile }]);
      assert.throws(() => getConfig(), /invalid or repeated mailbox id/);
      accounts([{ id: 'email-gmail', config: accountFile }, { id: 'email-qq', config: otherFile }]);
      assert.deepEqual([getConfig().connectors, getConfig().emailAccounts],
        [['lark', 'email'], [{ id: 'email-gmail', configFile: accountFile }, { id: 'email-qq', configFile: otherFile }]]);
      setEnv('VIKA_CHANNEL', 'email');
      assert.throws(() => getConfig(), /VIKA_CHANNEL/);
    } finally {
      setEnv('VIKA_EMAIL_ACCOUNTS_JSON', undefined);
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it('rejects a mismatched daemon and lark-cli profile', () => {
    setEnv('VIKA_LARK_PROFILE', 'cli_expected');
    setEnv('LARKSUITE_CLI_PROFILE', 'cli_wrong');
    assert.throws(() => getConfig(), /must match LARKSUITE_CLI_PROFILE/);
  });

  it('rejects a non-loopback health server', () => {
    setEnv('VIKA_HOST', '0.0.0.0');
    assert.throws(() => getConfig(), /loopback/);
  });

  it('rejects writable workspaces that contain the daemon', () => {
    setEnv('VIKA_WORKSPACE', path.dirname(projectDir));
    assert.throws(() => getConfig(), /running daemon directory/);
  });

  it('rejects an out-of-range codex effort', () => {
    setEnv('VIKA_CODEX_EFFORT', 'turbo');
    assert.throws(() => getConfig(), /VIKA_CODEX_EFFORT/);
  });

  it('does not read the removed Codex model environment variable', () => {
    setEnv('VIKA_MODEL', undefined);
    setEnv('VIKA_CODEX_MODEL', 'gpt-5.6-sol');
    const config = getConfig();
    assert.equal(config.initialModel, '');
    assert.equal(Object.hasOwn(config, 'codexModel'), false);
  });

  it('accepts TraeX CLI path and reasoning effort', () => {
    setEnv('TRAEX_BIN', '/opt/local/bin/traecli');
    setEnv('TRAECLI_BIN', '/ignored/traecli');
    setEnv('VIKA_TRAEX_EFFORT', 'xhigh');
    const config = getConfig();
    assert.equal(config.traexBin, '/opt/local/bin/traecli');
    assert.equal(config.traexEffort, 'xhigh');

    setEnv('TRAEX_BIN', undefined);
    assert.equal(getConfig().traexBin, '/ignored/traecli');

    // Unset, the first TraeX command name on PATH wins.
    setEnv('TRAECLI_BIN', undefined);
    const bin = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-traex-path-'));
    const savedPath = process.env.PATH;
    try {
      fs.writeFileSync(path.join(bin, 'trae-cli'), '#!/bin/sh\n', { mode: 0o755 });
      process.env.PATH = bin;
      assert.equal(getConfig().traexBin, 'trae-cli');
      fs.writeFileSync(path.join(bin, 'traecli'), '#!/bin/sh\n', { mode: 0o755 });
      assert.equal(getConfig().traexBin, 'traecli');
      process.env.PATH = '';
      assert.equal(getConfig().traexBin, 'traex');
    } finally {
      process.env.PATH = savedPath;
      fs.rmSync(bin, { recursive: true, force: true });
    }

    setEnv('VIKA_TRAEX_EFFORT', 'turbo');
    assert.throws(() => getConfig(), /VIKA_TRAEX_EFFORT/);
  });

  it('accepts Pi CLI path and thinking-level effort', () => {
    setEnv('PI_BIN', undefined);
    assert.equal(getConfig().piBin, managedPiBin());
    setEnv('PI_BIN', '/opt/homebrew/bin/pi');
    setEnv('VIKA_PI_MODEL', 'openai/gpt-4o');
    setEnv('VIKA_PI_EFFORT', 'xhigh');
    const config = getConfig();
    assert.equal(config.piBin, '/opt/homebrew/bin/pi');
    assert.equal(config.piModel, 'openai/gpt-4o');
    assert.equal(config.piEffort, 'xhigh');
    setEnv('VIKA_PI_EFFORT', 'turbo');
    assert.throws(() => getConfig(), /VIKA_PI_EFFORT/);
  });

  it('loads the managed-agent seed session without exposing the API key publicly', () => {
    setEnv('ARK_API_KEY', 'secret-test-key');
    setEnv('VIKA_MODEL', 'ark');
    setEnv('VIKA_VOLC_SESSION_ID', 'sesn-test12345678');
    setEnv('VIKA_VOLC_ENVIRONMENT_ID', 'env-test12345678');
    setEnv('SESSION_ID', 'sesn-generic-wins-only-as-fallback');
    const config = getConfig();
    assert.equal(config.volcApiKey, 'secret-test-key');
    assert.equal(config.volcSessionId, 'sesn-test12345678');
    assert.equal(config.volcEnvironmentId, 'env-test12345678');
    assert.equal(config.volcBaseUrl, 'https://ark.cn-beijing.volces.com/api/v3');
    assert.equal(config.initialModel, 'ark');
  });

  it('rejects a non-HTTPS managed-agent endpoint', () => {
    setEnv('VIKA_VOLC_BASE_URL', 'http://ark.example.test/api/v3');
    assert.throws(() => getConfig(), /valid HTTPS URL/);
  });
});

describe('release-owned prompts and the instance role', () => {
  it('reads every prompt from the release and has no mutable kernel paths', () => {
    const config = getConfig();
    assert.equal(config.promptDir, path.join(projectDir, 'packages', 'daemon', 'agent'));
    assert.equal(config.rolePromptPath, '');
    assert.equal(config.naturalWriting, true);
    assert.equal('kernelInnerPath' in config, false);
    assert.equal('kernelOuterPath' in config, false);
    assert.equal('kernelOuterDataPath' in config, false);
  });

  it('resolves VIKA_ROLE to a shipped role or the owner\'s own file', () => {
    setEnv('VIKA_ROLE', 'assistant');
    assert.equal(getConfig().rolePromptPath, path.join(projectDir, 'packages', 'daemon', 'agent', 'roles', 'assistant.md'));

    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-role-'));
    const own = path.join(directory, 'role.md');
    fs.writeFileSync(own, 'You answer questions about the garden.\n');
    try {
      setEnv('VIKA_ROLE', own);
      assert.equal(getConfig().rolePromptPath, own);
      setEnv('VIKA_ROLE', path.join(directory, 'missing.md'));
      assert.throws(() => getConfig(), /VIKA_ROLE names a file that does not exist/);
    } finally {
      fs.rmSync(directory, { recursive: true, force: true });
    }
    setEnv('VIKA_ROLE', 'pirate');
    assert.throws(() => getConfig(), /VIKA_ROLE pirate is not a known role/);
    setEnv('VIKA_ROLE', '../roles/qa');
    assert.throws(() => getConfig(), /VIKA_ROLE must be a role name or an absolute path/);
  });

  it('turns natural writing off only when asked', () => {
    setEnv('VIKA_NATURAL_WRITING', 'false');
    assert.equal(getConfig().naturalWriting, false);
    setEnv('VIKA_NATURAL_WRITING', 'maybe');
    assert.throws(() => getConfig(), /VIKA_NATURAL_WRITING must be a boolean/);
  });
});
