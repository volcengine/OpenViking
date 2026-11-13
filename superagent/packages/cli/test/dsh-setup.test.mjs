// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
// DSH in the interactive setup flow. Kept out of the upstream setup.test.mjs:
// what this measures is the DSH runtime's `binEnv` contract -- the binary a
// deployment picked is pinned into the manifest env, and runtime detection then
// probes with that pinned path, so a restart does not depend on the operator
// still exporting `DSH_BIN`.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { Prompter } from '../src/prompt.mjs';
import { setupCommand } from '../src/setup.mjs';

const RUNTIMES = [
  { id: 'codex', label: 'Codex', model: 'codex:', bin: '/opt/codex/bin/codex', binEnv: 'CODEX_BIN', available: true, note: '已登录' },
  { id: 'claude', label: 'Claude Code', model: 'claude:', bin: null, binEnv: 'CLAUDE_BIN', available: false, note: '未安装' },
  { id: 'pi', label: 'Pi（内置）', model: 'pi', bin: null, binEnv: null, available: true, note: 'ok' },
];

function harness(t, answers, { ov = { configPath: null, url: 'http://127.0.0.1:1933', usable: true }, runtimes = RUNTIMES, failStart = false } = {}) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-setup-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const input = new PassThrough();
  input.end(answers.map((line) => `${line}\n`).join(''));
  const output = new PassThrough();
  let written = '';
  output.on('data', (chunk) => { written += chunk; });
  const calls = [];
  const options = {
    prompter: new Prompter({ input, output }),
    // A Chinese locale settles the language; the English run and the question have tests of their own.
    env: { HOME: home, PATH: '', LARK_CLI_BIN: '/opt/lark/bin/lark-cli', LANG: 'zh_CN.UTF-8' },
    homeDir: home,
    detect: { runtimes: async () => runtimes, openViking: async () => ov },
    // Most tests focus on IM/navigation. The actual OV form is exercised below
    // and in ov-setup.test.mjs, independently of these canned IM answers.
    configureOV: async ({ preferredConfigPath, detect, ...options }) => {
      const detected = await detect({ ...options, configPath: preferredConfigPath });
      return preferredConfigPath ? { ...detected, configPath: preferredConfigPath } : detected;
    },
    connect: {
      qq: async ({ plan }) => { calls.push(['qq', plan.dataDir]); },
      telegram: async ({ plan }) => { calls.push(['telegram', plan.dataDir]); },
      lark: async ({ plan, profile }) => { calls.push(['lark', plan.id, profile]); return { profile, ownerOpenId: 'ou_owner' }; },
      weixin: async ({ plan }) => { calls.push(['weixin', plan.dataDir]); },
      email: async () => {
        calls.push(['email']);
        return { address: 'alice.example@gmail.com', password: 'p', imap: { host: 'imap.gmail.com', port: 993 }, smtp: { host: 'smtp.gmail.com', port: 465 } };
      },
      greet: async (message) => { calls.push(['greet', message.userId, message.text]); },
    },
    control: {
      start: async (target) => { calls.push(['start', target]); if (failStart) throw new Error('pm2 is not installed'); },
      restart: async (target) => { calls.push(['restart', target]); },
      remove: async (targets) => { calls.push(['remove', targets]); },
    },
    // On a terminal setup waits for the owner's message; here it has arrived.
    listen: async ({ port }) => { calls.push(['listen', port]); return { wait: async () => true }; },
    allocate: async () => 3738,
  };
  return { home, options, calls, written: () => written, manifest: () => JSON.parse(fs.readFileSync(path.join(home, '.ov-superagent', 'config.json'), 'utf8')) };
}
test('DSH pins its binary like every other runtime and probes with the manifest env', async (t) => {
  // DSH_BIN is a deployment fact, not a DSH setting: the chosen binary is pinned
  // into the manifest exactly like CODEX_BIN, so a restart does not depend on the
  // operator still exporting it.
  const DSH = { id: 'dsh', label: 'DeepSeek Harness', model: 'dsh', bin: '/opt/dsh/bin/dsh', binEnv: 'DSH_BIN', installed: true, available: true, route: 'deepseek/deepseek-chat', note: 'SDK bridge 兼容（0.2.2-rc.1）' };
  //         name  role  workspace  runtime  DSH mode skills IMs  channel reads-as-you confirm
  const h = harness(t, ['小V', '',   '',        '1',    '1',      '',     '1',  '',     '',          'y'], { runtimes: [DSH] });
  let probeEnv = null;
  h.options.detect.runtimes = async ({ env }) => { probeEnv = env; return [DSH]; };
  assert.equal(await setupCommand(h.options), 0);

  const manifest = h.manifest();
  assert.deepEqual(manifest.env, { DSH_BIN: '/opt/dsh/bin/dsh' });
  assert.equal(manifest.agents[0].model, 'dsh');
  assert.equal(manifest.agents[0].dshHomeMode, 'isolated');
  assert.deepEqual(manifest.agents[0].dshSkillRoots, [
    'workspace-dsh', 'project-agents', 'host-agents', 'host-dsh', 'data-dsh', 'data-agents',
  ]);
  assert.equal(manifest.agents[0].displayName, '小V');
  assert.equal(probeEnv.DSH_BIN, undefined, 'nothing is pinned yet on the first run');
});
