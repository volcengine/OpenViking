// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { execFileSync } from 'node:child_process';
import { SlackCli } from '../src/slack/cli.mjs';
import { ProcessRegistry } from '../src/process-utils.mjs';
import { extractSlackCliBinary, installSlackCliBinary } from '../../../scripts/release/slack-cli.mjs';

const token = 'xoxb-1111-2222-test-credential';
function executable(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'slack-cli-test-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const bin = path.join(directory, 'slack');
  fs.writeFileSync(bin, `#!${process.execPath}
    import fs from 'node:fs';
    const args = process.argv.slice(2);
    const params = Object.fromEntries(new URLSearchParams(args[args.indexOf('--data') + 1]));
    if (params.mode === 'slow') { setInterval(() => {}, 1000); }
    else if (params.mode === 'error') {
      console.error(process.env.SLACK_BOT_TOKEN);
      console.log(JSON.stringify({ ok: false, error: 'missing_scope' }));
    } else if (params.mode === 'leak') {
      console.error(process.env.SLACK_BOT_TOKEN);
      console.log(process.env.SLACK_BOT_TOKEN);
      process.exitCode = 1;
    } else if (params.mode === 'limit') {
      console.log(JSON.stringify({ ok: false, error: 'ratelimited' }));
      process.exitCode = 1;
    } else {
      console.log(JSON.stringify({ ok: true, args, params, cwd: process.cwd(),
        config: fs.realpathSync(args[args.indexOf('--config-dir') + 1]),
        tokenPresent: process.env.SLACK_BOT_TOKEN === ${JSON.stringify(token)},
        telemetry: process.env.SLACK_DISABLE_TELEMETRY,
        unwanted: Object.keys(process.env).filter(key => /^(SLACK_USER_TOKEN|SLACK_API_HOST|LARK_SECRET|NODE_OPTIONS)$/.test(key)),
        configFiles: fs.readdirSync(process.cwd()) }));
    }
  `, { mode: 0o755 });
  return bin;
}

test('CLI uses an isolated bot context, form arguments and no ambient credentials', async (t) => {
  const bin = executable(t);
  for (const key of ['SLACK_USER_TOKEN', 'SLACK_API_HOST', 'LARK_SECRET', 'NODE_OPTIONS']) {
    const previous = process.env[key];
    process.env[key] = 'ambient-secret';
    t.after(() => { if (previous === undefined) delete process.env[key]; else process.env[key] = previous; });
  }
  const cli = new SlackCli(token, { bin });
  const params = { channel: 'C012345', cursor: 'opaque=$(echo injected);=' };
  const result = await cli.call('conversations.history', params);
  assert.deepEqual(result.params, params);
  assert.deepEqual(result.args.slice(0, 2), ['api', 'conversations.history']);
  assert.equal(result.args[2], '--data', 'read APIs require form encoding even where JSON is documented');
  assert.equal(result.cwd, result.config);
  assert.equal(fs.existsSync(result.cwd), false, 'temporary CLI state was removed');
  assert.equal(result.tokenPresent, true);
  assert.equal(result.telemetry, 'true');
  assert.deepEqual(result.unwanted, []);
  assert.deepEqual(result.configFiles, []);
  assert.ok(!result.args.join(' ').includes(token));
  const members = await cli.call('conversations.members', { channel: 'C012345', limit: 10, cursor: undefined });
  assert.deepEqual(members.params, { channel: 'C012345', limit: '10' });
  await assert.rejects(cli.call('chat.postMessage', {}), /Unsupported Slack CLI query/);
});

test('CLI sanitizes failures, preserves Slack error codes and reports missing installation', async (t) => {
  const cli = new SlackCli(token, { bin: executable(t) });
  for (const [mode, code] of [['error', 'missing_scope'], ['limit', 'ratelimited'], ['leak', undefined]]) {
    await assert.rejects(cli.call('conversations.history', { mode }), error => {
      assert.equal(error.code, code);
      assert.ok(!String(error.stack).includes(token));
      assert.equal(error.cause, undefined);
      return true;
    });
  }
  await assert.rejects(new SlackCli(token, { bin: '/no-such/slack' }).call('users.info'), /Slack CLI is missing/);
});

test('CLI cancellation terminates its registered child and cleans temporary state', async (t) => {
  const registry = new ProcessRegistry({ warn() {} }, { detached: false });
  let directory;
  const register = registry.register.bind(registry);
  registry.register = child => {
    directory = child.spawnargs[child.spawnargs.indexOf('--config-dir') + 1];
    return register(child);
  };
  const cli = new SlackCli(token, { bin: executable(t), registry });
  await assert.rejects(cli.call('conversations.history', { mode: 'slow' }, AbortSignal.timeout(100)), /cancelled or timed out/);
  assert.equal(registry.children.size, 0);
  assert.equal(fs.existsSync(directory), false);
});

test('Slack CLI installer refuses unverified archives before replacing the binary', async (t) => {
  const root = path.dirname(executable(t));
  await assert.rejects(installSlackCliBinary({ root, platformId: 'unsupported' }), /Unsupported Slack CLI platform/);
  let url;
  await assert.rejects(installSlackCliBinary({ root, platformId: 'linux-x64', fetchImpl: async value => {
    url = value;
    return new Response('tampered download');
  } }), /sha.?256|checksum/i);
  assert.match(url, /github\.com\/slackapi\/slack-cli\/releases\/download\/v4\.10\.0\/slack_cli_4\.10\.0_linux_amd64\.tar\.gz$/);
  assert.equal(fs.existsSync(path.join(root, 'bin', 'slack')), false);
});

test('Slack CLI extraction supports Linux and macOS archive layouts without an embedded license', (t) => {
  const root = path.dirname(executable(t));
  const source = path.join(root, 'source');
  fs.mkdirSync(path.join(source, 'bin'), { recursive: true });
  fs.writeFileSync(path.join(source, 'bin', 'slack'), 'executable fixture', { mode: 0o755 });
  for (const [platform, entry] of [['linux', 'bin/slack'], ['macos', './bin/slack']]) {
    const archive = path.join(root, `${platform}.tar.gz`);
    const output = path.join(root, platform);
    fs.mkdirSync(output);
    execFileSync('tar', ['-czf', archive, '-C', source, entry]);
    extractSlackCliBinary(archive, output);
    assert.equal(fs.readFileSync(path.join(output, 'bin', 'slack'), 'utf8'), 'executable fixture');
    assert.ok(fs.statSync(path.join(output, 'bin', 'slack')).mode & 0o111);
  }
  fs.writeFileSync(path.join(source, 'unexpected'), 'wrong archive');
  const wrong = path.join(root, 'wrong.tar.gz');
  execFileSync('tar', ['-czf', wrong, '-C', source, 'unexpected']);
  assert.throws(() => extractSlackCliBinary(wrong, root), /no bin\/slack executable/);
});
