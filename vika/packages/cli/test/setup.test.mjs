// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { GoBack, Prompter } from '../src/prompt.mjs';
import { connectLark, larkMessageListener, larkScopes, registerApp, setupCommand } from '../src/setup.mjs';
import { stripAnsi } from '../src/ui.mjs';
import { connectTelegram } from '../src/telegram.mjs';
import { TelegramState } from '../../daemon/src/telegram/state.mjs';
import { configureOpenViking } from '../src/ov-setup.mjs';
import { detectOpenViking, savedOVConfigPath } from '../src/ov.mjs';
import { updateManifest } from '../../daemon/src/deployment-manifest.mjs';

const RUNTIMES = [
  { id: 'codex', label: 'Codex', model: 'codex:', bin: '/opt/codex/bin/codex', binEnv: 'CODEX_BIN', available: true, note: '已登录' },
  { id: 'claude', label: 'Claude Code', model: 'claude:', bin: null, binEnv: 'CLAUDE_BIN', available: false, note: '未安装' },
  { id: 'pi', label: 'Pi（内置）', model: 'pi', bin: null, binEnv: null, available: true, note: 'ok' },
];

function harness(t, answers, { ov = { configPath: null, url: 'http://127.0.0.1:1933', usable: true }, runtimes = RUNTIMES, failStart = false } = {}) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-setup-'));
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
  return { home, options, calls, written: () => written, manifest: () => JSON.parse(fs.readFileSync(path.join(home, '.vika', 'config.json'), 'utf8')) };
}

test('a startup failure points to the instance logs before retrying start', async (t) => {
  const h = harness(t, ['Vika', '', '', '1', '1', '', 'y'], { failStart: true });
  assert.equal(await setupCommand(h.options), 1);
  assert.match(h.written(), /vika logs primary 查看具体错误/);
  assert.match(h.written(), /vika start primary/);
});

test('a first setup: Feishu + Weixin, Codex, OpenViking; scans run only after every answer, then it starts', async (t) => {
  const ovSource = path.join(os.tmpdir(), `vika-ov-source-${process.pid}.conf`);
  fs.writeFileSync(ovSource, JSON.stringify({ url: 'https://ov.example', api_key: 'k' }));
  t.after(() => fs.rmSync(ovSource, { force: true }));
  const ov = { configPath: ovSource, url: 'https://ov.example', usable: true, user: 'amy', authMode: 'api_key', reason: null };
  // The first instance is `primary` without asking.
  //                   name  role  workspace  runtime IMs    channel feishu confirm
  const h = harness(t, ['小V', '',   '',        '1',    '1,2', '2',    '',    'y'], { ov });
  assert.equal(await setupCommand(h.options), 0);

  const manifest = h.manifest();
  assert.equal(manifest.defaultInstance, 'primary');
  // lark-cli comes with the release; only the runtime the user installed is pinned.
  assert.deepEqual(manifest.env, { CODEX_BIN: '/opt/codex/bin/codex' });
  assert.deepEqual(manifest.agents, [{
    id: 'primary',
    displayName: '小V',
    channel: 'weixin',
    connectors: ['lark', 'weixin'],
    profile: 'vika-primary',
    port: 3738,
    dataDir: path.join(h.home, '.vika', 'data', 'primary'),
    workspace: path.join(h.home, 'vika-workspace', 'primary'),
    ovConfig: path.join(h.home, '.vika', 'ov', 'primary.json'),
    ovAutoRecall: false,
    stagedReplies: false,
    naturalWriting: true,
    explicitSilence: false,
    // The first AI colleague on Feishu stores the owner's meeting notes.
    meetingNotes: true,
    role: 'colleague',
    model: 'codex:',
  }]);
  assert.equal(JSON.parse(fs.readFileSync(manifest.agents[0].ovConfig, 'utf8')).actor_peer_id, 'primary');
  assert.ok(fs.statSync(manifest.agents[0].workspace).isDirectory());
  const dataDir = path.join(h.home, '.vika', 'data', 'primary');
  assert.deepEqual(h.calls.map((call) => call.slice(0, 2)), [
    ['lark', 'primary'],
    ['weixin', dataDir],
    ['start', 'primary'],
    ['restart', 'web'],
    ['start', 'updater'],
    ['greet', 'ou_owner'],
  ]);
  // Feishu gets a hello; Weixin delivers nothing until the owner writes, so the owner is told to.
  assert.match(h.written(), /已就绪。\n  飞书：私聊里它已经跟你打了招呼，直接回它\n  微信：先给它发一句「你好」，在那之前它没法主动找你\n  控制台：http:\/\/127\.0\.0\.1:3737/);
  assert.doesNotMatch(h.written(), /最后一步/, 'said once, in the list of what to do next');
  assert.match(h.written(), /消息渠道\s+飞书、微信，主渠道：微信/);
  assert.match(h.written(), /接下来扫码：飞书 2 次，微信 1 次/);
});

test('without a usable OV, a new instance goes no further than the OV form', async (t) => {
  //                   local default, hosted, manual, file, detect again, exit
  const h = harness(t, ['6']);
  h.options.configureOV = configureOpenViking;
  h.options.detect.openViking = detectOpenViking;
  h.options.fetchImpl = async () => { throw new Error('offline'); };
  await assert.rejects(setupCommand(h.options), { name: 'PromptAborted' });
  assert.match(h.written(), /用本机默认地址（不带凭据）\s+http:\/\/127\.0\.0\.1:1933 · OpenViking 服务不可用/);
  assert.doesNotMatch(h.written(), /机器人名字/);
  assert.deepEqual(h.calls, []);
  assert.equal(fs.existsSync(path.join(h.home, '.vika')), false);
});

test('the real OV form accepts remote credentials before any IM questions or authorization', async (t) => {
  const h = harness(t, ['3', 'https://memory.example:9443', '1', 'private-key', 'team', 'owner', '',
    'Vika', '', '', '1', '2', 'y']);
  h.options.configureOV = configureOpenViking;
  h.options.detect.openViking = detectOpenViking;
  h.options.fetchImpl = async (url, init) => {
    if (!url.startsWith('https://memory.example:9443')) throw new Error('offline');
    assert.equal(init.headers['X-API-Key'], 'private-key');
    return Response.json({ status: 'ok', auth_mode: 'api_key', result: [] });
  };
  assert.equal(await setupCommand(h.options), 0);
  const [agent] = h.manifest().agents;
  assert.deepEqual(JSON.parse(fs.readFileSync(agent.ovConfig, 'utf8')), {
    url: 'https://memory.example:9443', api_key: 'private-key', account: 'team', user: 'owner', actor_peer_id: 'primary',
  });
  assert.ok(h.written().indexOf('OpenViking 连接已验证') < h.written().indexOf('接入哪些 IM'));
  assert.doesNotMatch(h.written(), /private-key/);
  // Typed once: the next instance is offered it.
  assert.deepEqual(JSON.parse(fs.readFileSync(savedOVConfigPath(h.home), 'utf8')), {
    url: 'https://memory.example:9443', api_key: 'private-key', account: 'team', user: 'owner',
  });
});

test('editing the OV connection preserves the actor and old file; Save applies it and Discard removes the pending copy', async (t) => {
  for (const save of [true, false]) {
    const h = harness(t, ['2', '9', '4', 'https://memory.example:9443', '1', 'new-key', '', '', '', save ? '11' : '12']);
    const before = existing(h, { ovAutoRecall: true });
    fs.mkdirSync(path.dirname(before.ovConfig), { recursive: true });
    const original = JSON.stringify({ url: 'https://old.example', api_key: 'old-key', actor_peer_id: 'original-actor' });
    fs.writeFileSync(before.ovConfig, original);
    h.options.configureOV = configureOpenViking;
    h.options.detect.openViking = detectOpenViking;
    h.options.fetchImpl = async (url) => {
      if (url.startsWith('http://127.0.0.1:1933')) throw new Error('offline');
      return Response.json({ status: 'ok', auth_mode: 'api_key', result: [] });
    };
    await setupCommand(h.options);
    assert.ok(h.written().indexOf('9) OpenViking') < h.written().indexOf('选择 OpenViking 连接'));
    assert.match(h.written(), /OpenViking\s+.*（已改）/);
    assert.equal(fs.readFileSync(before.ovConfig, 'utf8'), original);
    if (save) {
      const [after] = h.manifest().agents;
      assert.notEqual(after.ovConfig, before.ovConfig);
      const config = JSON.parse(fs.readFileSync(after.ovConfig, 'utf8'));
      assert.equal(config.actor_peer_id, 'original-actor');
      assert.equal(config.url, 'https://memory.example:9443');
      assert.equal(config.api_key, 'new-key');
      assert.equal(after.ovAutoRecall, true);
      assert.deepEqual(h.calls, [['restart', 'asd']]);
      assert.equal(JSON.parse(fs.readFileSync(savedOVConfigPath(h.home), 'utf8')).url, 'https://memory.example:9443');
    } else {
      assert.deepEqual(h.manifest().agents, [before]);
      assert.deepEqual(fs.readdirSync(path.dirname(before.ovConfig)), ['asd.json']);
      assert.deepEqual(h.calls, []);
      assert.equal(fs.existsSync(savedOVConfigPath(h.home)), false);
    }
    assert.doesNotMatch(h.written(), /old-key|new-key/);
  }
});

test('adding an instance offers shared OV connections only and writes its own actor', async (t) => {
  const h = harness(t, ['1', '1', 'fresh', 'Fresh', '', '', '1', '2', 'y']);
  const before = existing(h);
  fs.mkdirSync(path.dirname(before.ovConfig), { recursive: true });
  const original = JSON.stringify({ url: 'https://other-instance.example', actor_peer_id: 'other-actor' });
  fs.writeFileSync(before.ovConfig, original);
  fs.mkdirSync(path.join(h.home, '.openviking'));
  const sharedPath = path.join(h.home, '.openviking', 'ovcli.conf');
  const shared = { url: 'http://127.0.0.1:1933', account: 'team', user: 'owner', actor_peer_id: 'cli-actor' };
  fs.writeFileSync(sharedPath, JSON.stringify(shared));
  h.options.configureOV = configureOpenViking;
  h.options.detect.openViking = detectOpenViking;
  h.options.allocate = async () => 3739;
  const checked = [];
  h.options.fetchImpl = async (url, init) => {
    checked.push({ url, headers: init.headers });
    const valid = url.startsWith(shared.url) && init.headers['X-OpenViking-Account'] === 'team'
      && init.headers['X-OpenViking-User'] === 'owner';
    return Response.json({ status: valid ? 'ok' : 'error', auth_mode: 'trusted', result: [] }, { status: valid ? 200 : 400 });
  };
  assert.equal(await setupCommand(h.options), 0);
  assert.ok(checked.every(({ url, headers }) => url.startsWith(shared.url)
    && headers['X-OpenViking-Account'] === 'team' && headers['X-OpenViking-User'] === 'owner'));
  const [unchanged, fresh] = h.manifest().agents;
  assert.deepEqual(unchanged, before);
  assert.equal(fresh.id, 'fresh');
  assert.notEqual(fresh.ovConfig, sharedPath);
  assert.notEqual(fresh.ovConfig, before.ovConfig);
  assert.deepEqual(JSON.parse(fs.readFileSync(fresh.ovConfig, 'utf8')), { ...shared, actor_peer_id: 'fresh' });
  assert.equal(fs.readFileSync(before.ovConfig, 'utf8'), original);
  assert.deepEqual(JSON.parse(fs.readFileSync(sharedPath, 'utf8')), shared);
  assert.equal(fs.existsSync(savedOVConfigPath(h.home)), false, 'ovcli.conf is found anyway; no copy to go stale');
  assert.doesNotMatch(h.written(), /实例 asd:|本机默认地址|other-instance\.example|HTTP 400/);
});

test('setup prepares and checks the instance OV identity before IM selection, email credentials or scans', async (t) => {
  //                   name role ws  runtime IMs        channel feishu another-mailbox confirm
  const h = harness(t, ['', '',  '', '1',    '1,2,3,4', '1',    '',    'n',            'y']);
  const checked = [];
  h.options.detect.openViking = async ({ configPath }) => {
    if (configPath) {
      assert.equal(JSON.parse(fs.readFileSync(configPath, 'utf8')).actor_peer_id, 'primary');
      checked.push(configPath);
    }
    return { configPath: null, url: 'http://127.0.0.1:1933', usable: true };
  };
  for (const kind of ['email', 'lark', 'weixin', 'telegram']) {
    const connect = h.options.connect[kind];
    h.options.connect[kind] = async (...args) => {
      assert.ok(checked.length >= 2, `${kind} must follow the instance check and a fresh pre-authorization check`);
      assert.ok(fs.existsSync(checked[0]), 'the verified config already exists during IM setup');
      return connect(...args);
    };
  }
  await setupCommand(h.options);
  const output = h.written();
  assert.ok(output.indexOf('OpenViking 已就绪，接下来接入 IM') < output.indexOf('接入哪些 IM'));
  assert.ok(checked.every((file) => file === h.manifest().agents[0].ovConfig));
  assert.deepEqual(h.calls.filter(([kind]) => ['email', 'lark', 'weixin', 'telegram'].includes(kind)).map(([kind]) => kind),
    ['email', 'lark', 'weixin', 'telegram']);
});

test('an unusable generated OV identity fails before IM selection and removes the new config', async (t) => {
  const h = harness(t, ['', '', '', '1']);
  h.options.detect.openViking = async ({ configPath }) => ({
    configPath: null, url: 'http://127.0.0.1:1933', usable: !configPath, reason: 'actor denied',
  });
  await assert.rejects(setupCommand(h.options), /OpenViking.*primary.*actor denied/);
  assert.doesNotMatch(h.written(), /接入哪些 IM/);
  assert.deepEqual(h.calls, []);
  assert.equal(fs.existsSync(path.join(h.home, '.vika', 'config.json')), false);
  assert.deepEqual(fs.readdirSync(path.join(h.home, '.vika', 'ov')), []);
});

test('an unwritable OV config fails before IM selection without touching existing files', async (t) => {
  const h = harness(t, ['', '', '', '1']);
  fs.mkdirSync(path.join(h.home, '.vika'), { recursive: true });
  const blocked = path.join(h.home, '.vika', 'ov');
  fs.writeFileSync(blocked, 'existing file');
  await assert.rejects(setupCommand(h.options), /EEXIST|ENOTDIR/);
  assert.equal(fs.readFileSync(blocked, 'utf8'), 'existing file');
  assert.doesNotMatch(h.written(), /接入哪些 IM/);
  assert.deepEqual(h.calls, []);
});

test('OV going down while answers are collected blocks scans and email credential prompts', async (t) => {
  for (const answers of [['', '', '', '1', '1', '', 'y'], ['', '', '', '1', '1,3', '']]) {
    const h = harness(t, answers);
    let checks = 0;
    h.options.detect.openViking = async () => ({
      configPath: null, url: 'http://127.0.0.1:1933', usable: ++checks <= 2, reason: 'went offline',
    });
    await assert.rejects(setupCommand(h.options), /OpenViking.*went offline/);
    assert.deepEqual(h.calls, []);
    assert.equal(fs.existsSync(path.join(h.home, '.vika', 'config.json')), false);
    assert.deepEqual(fs.readdirSync(path.join(h.home, '.vika', 'ov')), []);
  }
});

test('editing ordinary settings never probes OV or opens its form and preserves the identity and recall setting', async (t) => {
  const h = harness(t, ['2', '1', 'Renamed', '11']);
  const before = existing(h, { ovAutoRecall: true });
  const checked = [];
  h.options.configureOV = configureOpenViking;
  h.options.detect.openViking = async ({ configPath }) => {
    checked.push(configPath);
    return { usable: false, reason: 'offline' };
  };
  assert.equal(await setupCommand(h.options), 0);
  assert.deepEqual(checked, []);
  assert.doesNotMatch(h.written(), /选择 OpenViking 连接|OpenViking 暂不可用/);
  assert.deepEqual(h.manifest().agents, [{ ...before, displayName: 'Renamed' }]);
});

test('returning from OV configuration keeps other unsaved settings and the original OV config', async (t) => {
  const h = harness(t, ['2', '1', 'Renamed', '9', '7', '11']);
  const before = existing(h);
  fs.mkdirSync(path.dirname(before.ovConfig), { recursive: true });
  const original = JSON.stringify({ url: 'https://old.example', actor_peer_id: 'original-actor' });
  fs.writeFileSync(before.ovConfig, original);
  h.options.configureOV = configureOpenViking;
  h.options.detect.openViking = detectOpenViking;
  h.options.fetchImpl = async () => { throw new Error('offline'); };
  assert.equal(await setupCommand(h.options), 0);
  assert.match(h.written(), /返回实例设置/);
  assert.deepEqual(h.manifest().agents, [{ ...before, displayName: 'Renamed' }]);
  assert.equal(fs.readFileSync(before.ovConfig, 'utf8'), original);
  assert.deepEqual(fs.readdirSync(path.dirname(before.ovConfig)), ['asd.json']);
  assert.deepEqual(h.calls, [['restart', 'asd']]);
});

test('reopening OV edits the pending connection, preserves the actor, and discarding removes every temporary config', async (t) => {
  const h = harness(t, ['2', '9', '9', '12']);
  const before = existing(h);
  fs.mkdirSync(path.dirname(before.ovConfig), { recursive: true });
  const original = JSON.stringify({ url: 'https://old.example', actor_peer_id: 'original-actor' });
  fs.writeFileSync(before.ovConfig, original);
  let previous = before.ovConfig;
  let edits = 0;
  h.options.configureOV = async ({ preferredConfigPath, canGoBack }) => {
    assert.equal(canGoBack, true);
    if (edits) {
      assert.notEqual(preferredConfigPath, previous);
      assert.equal(JSON.parse(fs.readFileSync(preferredConfigPath)).url, 'https://edit-1.example');
      assert.equal(JSON.parse(fs.readFileSync(preferredConfigPath)).actor_peer_id, 'original-actor');
    }
    previous = preferredConfigPath;
    return { usable: true, config: { url: `https://edit-${++edits}.example`, actor_peer_id: 'foreign-actor' } };
  };
  assert.equal(await setupCommand(h.options), 0);
  assert.equal(edits, 2);
  assert.equal(fs.readFileSync(before.ovConfig, 'utf8'), original);
  assert.deepEqual(fs.readdirSync(path.dirname(before.ovConfig)), ['asd.json']);
  assert.deepEqual(h.manifest().agents, [before]);
});

test('Telegram-only setup binds a private Start challenge and saves no token in the manifest', async (t) => {
  const token = '123456:abcdefghijklmnopqrstuvwxyz_TEST';
  const h = harness(t, ['TG Agent', '', '', '3', '4', 'y', token]);
  h.options.connect.telegram = connectTelegram;
  h.options.fetchImpl = async (url, init) => {
    const method = url.split('/').at(-1);
    if (method === 'getMe') return Response.json({ ok: true, result: { id: 123456, is_bot: true, username: 'vika_test_bot' } });
    if (method === 'getWebhookInfo') return Response.json({ ok: true, result: { url: '' } });
    assert.equal(method, 'getUpdates');
    const nonce = h.written().match(/https:\/\/t\.me\/vika_test_bot\?start=([A-Za-z0-9_-]+)/)[1];
    return Response.json({ ok: true, result: [{ update_id: 1, message: { message_id: 1,
      date: Math.floor(Date.now() / 1000), from: { id: 99, is_bot: false }, chat: { id: 99, type: 'private' }, text: `/start ${nonce}` } }] });
  };
  assert.equal(await setupCommand(h.options), 0);
  const agent = h.manifest().agents[0];
  assert.equal(agent.channel, 'telegram');
  assert.deepEqual(agent.connectors, ['telegram']);
  assert.equal(agent.profile, undefined);
  assert.doesNotMatch(JSON.stringify(h.manifest()), /abcdefghijklmnopqrstuvwxyz/);
  const state = new TelegramState(agent.dataDir);
  try { assert.equal(state.account().ownerId, '99'); } finally { state.close(); }
  assert.match(h.written(), /Telegram Bot token \+ Start 绑定/);
  assert.doesNotMatch(h.written(), /Telegram undefined|abcdefghijklmnopqrstuvwxyz/);
});

test('QQ-only setup needs no Lark profile, selects QQ as primary and binds before saving', async (t) => {
  const h = harness(t, ['QQ Agent', '', '', '3', '6', 'y']);
  h.options.connect.qq = async ({ plan }) => {
    assert.equal(fs.existsSync(path.join(h.home, '.vika', 'config.json')), false);
    h.calls.push(['qq', plan.dataDir]);
  };
  assert.equal(await setupCommand(h.options), 0);
  const agent = h.manifest().agents[0];
  assert.equal(agent.channel, 'qq'); assert.deepEqual(agent.connectors, ['qq']);
  assert.equal(agent.profile, undefined);
  assert.equal(h.calls[0][0], 'qq');
  assert.match(h.written(), /QQ AppID \/ AppSecret \+ 私聊验证码绑定/);
  assert.doesNotMatch(h.written(), /undefined 次/);
});

test('adding QQ to an existing instance binds only QQ and can choose it as primary', async (t) => {
  const h = harness(t, ['2', '5', '1,6', '7', '2', '11']);
  const before = existing(h, {});
  assert.equal(await setupCommand(h.options), 0);
  assert.deepEqual(h.manifest().agents, [{ ...before, connectors: ['lark', 'qq'], channel: 'qq' }]);
  assert.deepEqual(h.calls, [['qq', before.dataDir], ['restart', 'asd']]);
  assert.match(h.written(), /接入QQ（需要 AppID \/ AppSecret 和私聊验证码）/);
});

test('adding Telegram to an existing instance verifies only the new IM and can change the primary', async (t) => {
  const h = harness(t, ['2', '5', '1,4', '7', '2', '11']);
  const before = existing(h, {});
  assert.equal(await setupCommand(h.options), 0);
  assert.deepEqual(h.manifest().agents, [{ ...before, connectors: ['lark', 'telegram'], channel: 'telegram' }]);
  assert.deepEqual(h.calls, [['telegram', before.dataDir], ['restart', 'asd']]);
  assert.match(h.written(), /接入Telegram（需要 Bot token 和 Start 绑定）/);
  assert.match(h.written(), /接下来接入：Telegram Bot token \+ Start 绑定/);
  assert.doesNotMatch(h.written(), /undefined 次|接下来扫码/);
});

test('adding Slack to an existing instance pastes tokens for the new IM only and can make it primary', async (t) => {
  const h = harness(t, ['2', '5', '1,5', '7', '2', '11']);
  const before = existing(h, {});
  h.options.connect.slack = async ({ plan }) => { h.calls.push(['slack', plan.dataDir]); };
  assert.equal(await setupCommand(h.options), 0);
  assert.deepEqual(h.manifest().agents, [{ ...before, connectors: ['lark', 'slack'], channel: 'slack' }]);
  assert.deepEqual(h.calls, [['slack', before.dataDir], ['restart', 'asd']]);
  assert.match(h.written(), /接入Slack（需要新建或选已有 App、两个 token 和私聊口令绑定）/);
  assert.match(h.written(), /接下来接入：Slack 新建或选已有 App \+ 两个 token \+ 私聊口令绑定/);
  assert.match(h.written(), /\/invite/);
});

test('setup detects a primary command that changed the manifest while it was connecting an IM', async (t) => {
  const h = harness(t, ['2', '5', '1,4', '7', '2', '11']);
  const before = existing(h, { connectors: ['lark', 'slack'] });
  h.options.connect.telegram = async () => {
    updateManifest(path.join(h.home, '.vika', 'config.json'), (value) => { value.agents[0].channel = 'slack'; });
  };
  await assert.rejects(setupCommand(h.options), /配置已被其他操作修改/);
  assert.deepEqual(h.manifest().agents, [{ ...before, channel: 'slack' }]);
  assert.deepEqual(h.calls, [], 'does not restart with a stale setup snapshot');
});

test('mixed Telegram setup describes each authorization method and keeps email available', async (t) => {
  const h = harness(t, ['', '', '', '3', '1,2,3,4', '3', '', 'n', 'y']);
  assert.equal(await setupCommand(h.options), 0);
  assert.deepEqual(h.manifest().agents[0].connectors, ['lark', 'weixin', 'telegram', 'email']);
  assert.equal(h.manifest().agents[0].channel, 'telegram');
  assert.match(h.written(), /接下来接入：飞书 2 次，微信 1 次，Telegram Bot token \+ Start 绑定/);
  assert.deepEqual(h.calls.map(([kind]) => kind), ['email', 'lark', 'weixin', 'telegram', 'start', 'restart', 'start', 'greet']);
});

test('a second instance keeps the shared env and the default instance', async (t) => {
  const h = harness(t, ['', '', '', '3', '1', '', 'y']);
  assert.equal(await setupCommand(h.options), 0);
  //                        new  id
  const second = harness(t, ['1', '', 'Second', '', '', '3', '1', '', 'y']);
  fs.mkdirSync(path.join(second.home, '.vika'), { recursive: true });
  fs.copyFileSync(path.join(h.home, '.vika', 'config.json'), path.join(second.home, '.vika', 'config.json'));
  second.options.allocate = async () => 3739;
  assert.equal(await setupCommand(second.options), 0);
  const manifest = second.manifest();
  assert.deepEqual(manifest.agents.map(({ id, model, port }) => [id, model, port]), [['primary', 'pi', 3738], ['agent2', 'pi', 3739]]);
  assert.deepEqual(manifest.env, {});
  assert.equal(manifest.defaultInstance, 'primary');
});

test('declining before the scans writes nothing; a Weixin-only instance has no profile and no lark-cli need', async (t) => {
  const declined = harness(t, ['', '', '', '1', '2', 'n']);
  assert.equal(await setupCommand(declined.options), 1);
  assert.equal(fs.existsSync(path.join(declined.home, '.vika', 'config.json')), false);
  assert.deepEqual(fs.readdirSync(path.join(declined.home, '.vika', 'ov')), []);
  assert.deepEqual(declined.calls, []);

  const weixin = harness(t, ['', '', '', '1', '2', 'y']);
  weixin.options.env.LARK_CLI_BIN = '';
  assert.equal(await setupCommand(weixin.options), 0);
  const [agent] = weixin.manifest().agents;
  assert.equal(agent.profile, undefined);
  assert.equal(agent.channel, 'weixin');
  // OV is required and one IM leaves no primary-channel choice.
  assert.match(weixin.written(), /OpenViking 已就绪/);
  assert.doesNotMatch(weixin.written(), /没有检测到 OpenViking|主渠道是/);
  assert.deepEqual(weixin.calls.map(([kind]) => kind), ['weixin', 'start', 'restart', 'start']);
});

test('mailboxes come with an IM, are checked before the scans and saved with the manifest', async (t) => {
  //                          IMs: email alone, then Weixin + email; two mailboxes
  const h = harness(t, ['', '', '', '1', '3', '2,3', 'y', 'n', 'y']);
  const accounts = ['alice.example@gmail.com', '10001@qq.com'];
  const taken = [];
  h.options.connect.email = async ({ taken: given }) => {
    h.calls.push(['email']);
    taken.push(given);
    return { address: accounts.shift(), password: 'p', imap: { host: 'imap.x.com', port: 993 }, smtp: { host: 'smtp.x.com', port: 465 } };
  };
  assert.equal(await setupCommand(h.options), 0);
  assert.match(h.written(), /邮箱不能单独接/);
  const [agent] = h.manifest().agents;
  const file = (id) => path.join(h.home, '.vika', 'email', 'primary', `${id}.json`);
  assert.deepEqual([agent.connectors, agent.channel, agent.emailAccounts], [['weixin', 'email'], 'weixin',
    [{ id: 'email-gmail', config: file('email-gmail') }, { id: 'email-qq', config: file('email-qq') }]]);
  assert.deepEqual(taken, [[], ['alice.example@gmail.com']]);
  assert.equal(JSON.parse(fs.readFileSync(file('email-qq'), 'utf8')).address, '10001@qq.com');
  assert.equal(fs.statSync(file('email-gmail')).mode & 0o777, 0o600);
  assert.match(h.written(), /邮箱\s+alice\.example@gmail\.com、10001@qq\.com\n/);
  assert.deepEqual(h.calls.map(([kind]) => kind), ['email', 'email', 'weixin', 'start', 'restart', 'start']);

  const declined = harness(t, ['', '', '', '1', '1,3', '', 'n', 'n']);
  assert.equal(await setupCommand(declined.options), 1);
  assert.equal(fs.existsSync(path.join(declined.home, '.vika', 'email')), false, 'nothing is saved before the scans');
});

// An instance already on the machine, for setup to change.
function existing(h, agent) {
  fs.mkdirSync(path.join(h.home, '.vika'), { recursive: true });
  const full = { id: 'asd', displayName: 'ASD', channel: 'lark', connectors: ['lark'], profile: 'vika-asd', port: 3738,
    dataDir: path.join(h.home, '.vika', 'data', 'asd'), workspace: path.join(h.home, 'ws'), ovConfig: path.join(h.home, '.vika', 'ov', 'asd.json'), ovAutoRecall: false,
    stagedReplies: false, naturalWriting: true, explicitSilence: false, meetingNotes: false, model: 'codex:', ...agent };
  fs.writeFileSync(path.join(h.home, '.vika', 'config.json'),
    JSON.stringify({ version: 1, defaultInstance: full.id, env: {}, agents: [full] }));
  return full;
}

test('setup saves the safe temporary directory for /root data without moving the data or workspace', async (t) => {
  const h = harness(t, ['2', '1', 'New name', '11']);
  const before = existing(h, { dataDir: '/root/.vika/data/asd' });
  assert.equal(await setupCommand(h.options), 0);
  assert.deepEqual(h.manifest().agents, [{ ...before, displayName: 'New name', tempDir: '/tmp/vika-asd' }]);
  assert.deepEqual(h.calls, [['restart', 'asd']]);
});

test('setup keeps per-instance, manifest and shell temporary directory overrides', async (t) => {
  for (const source of ['instance', 'manifest', 'shell']) {
    const h = harness(t, ['2', '1', 'New name', '11']);
    const before = existing(h, { dataDir: '/root/.vika/data/asd',
      ...(source === 'instance' ? { tempDir: '/tmp/custom' } : {}) });
    if (source === 'shell') h.options.env.VIKA_TEMP_DIR = '/tmp/custom';
    if (source === 'manifest') {
      const manifest = h.manifest();
      manifest.env.VIKA_TEMP_DIR = '/tmp/custom';
      fs.writeFileSync(path.join(h.home, '.vika', 'config.json'), JSON.stringify(manifest));
    }
    assert.equal(await setupCommand(h.options), 0);
    assert.deepEqual(h.manifest().agents, [{ ...before, displayName: 'New name' }]);
  }
});

test('setup offers removal without requiring a usable runtime or OpenViking', async (t) => {
  const h = harness(t, ['3', '1', 'y'], { runtimes: [], ov: { usable: false } });
  const agent = existing(h, {});
  assert.equal(await setupCommand(h.options), 0);
  assert.deepEqual(h.calls, [['remove', ['updater', agent.id, 'web']]]);
  assert.equal(fs.existsSync(path.join(h.home, '.vika', 'config.json')), false);
  assert.match(h.written(), /删除一个实例/);
  assert.match(h.written(), /确认删除这个实例？.*y\/N/);
  assert.doesNotMatch(h.written(), /机器人名字|OpenViking 已就绪/);
});

test('removal confirmation defaults to cancel, in Chinese and English', async (t) => {
  for (const lang of ['zh', 'en']) {
    const h = harness(t, ['3', '1', ''], { runtimes: [] });
    h.options.lang = lang;
    const before = existing(h, {});
    assert.equal(await setupCommand(h.options), 0);
    assert.deepEqual(h.manifest().agents, [before]);
    assert.deepEqual(h.calls, []);
    assert.match(h.written(), lang === 'zh' ? /已取消，没有删除实例/ : /Cancelled; no instance was removed/);
    if (lang === 'en') assert.doesNotMatch(h.written(), /[一-龥]/);
  }
});

// The change menu: 1 名字 2 角色 3 工作目录 4 运行时 5 IM 6 飞书应用 7 主渠道 8 邮箱 9 OpenViking 10 保存 11 放弃.
test('changing an instance lists every setting; only the ones opened change', async (t) => {
  //                    change asd  IMs  lark+weixin  primary  weixin  save
  const h = harness(t, ['2',        '5', '1,2',       '7',     '2',    '11']);
  const before = existing(h, {});
  assert.equal(await setupCommand(h.options), 0);
  const [after] = h.manifest().agents;
  assert.deepEqual(after, { ...before, connectors: ['lark', 'weixin'], channel: 'weixin' });
  // The list shows every value; what was changed is marked.
  assert.match(h.written(), /1\) 机器人名字\s+ASD\n  2\) 角色\s+没有设置（只用默认规则）\n  3\) 工作目录\s+~\/ws\n  4\) Agent 运行时\s+Codex\n  5\) 接入的 IM\s+飞书\n  6\) 飞书应用\s+现在的应用\n  7\) 主渠道\s+飞书\n  8\) 邮箱\s+没有接\n  9\) OpenViking\s+~\/\.vika\/ov\/asd\.json\n  10\) 其他设置\s+开着：自然写作\n  11\) 保存\n  12\) 放弃修改/);
  assert.match(h.written(), /5\) 接入的 IM\s+飞书、微信（已改）[\s\S]*7\) 主渠道\s+微信（已改）\n[\s\S]*11\) 保存（2 项改动）/);
  assert.match(h.written(), /将修改 ASD（asd）：\n  接入微信（需要扫码）\n  主渠道：飞书 → 微信\n/);
  assert.deepEqual(h.calls.map((call) => call.slice(0, 2)), [['weixin', before.dataDir], ['restart', 'asd']]);
  assert.match(h.written(), /ASD（asd）已更新。\n  微信：先给它发一句「你好」/);

  // Saving nothing, or leaving, writes and restarts nothing.
  for (const [answers, said] of [[['2', '11'], /没有改动/], [['2', '1', '新名字', '12'], /已放弃，没有保存任何改动/]]) {
    const idle = harness(t, answers);
    const config = existing(idle, {});
    assert.equal(await setupCommand(idle.options), 0);
    assert.match(idle.written(), said);
    assert.deepEqual(idle.manifest().agents, [config]);
    assert.deepEqual(idle.calls, []);
  }
});

test('changing an instance runtime saves the setup selector before restarting it', async (t) => {
  //                    change runtime Pi save
  const h = harness(t, ['2',    '4',    '3', '11']);
  const before = existing(h, {});
  assert.equal(await setupCommand(h.options), 0);
  assert.deepEqual(h.manifest().agents, [{ ...before, model: 'pi' }]);
  assert.deepEqual(h.calls, [['restart', 'asd']]);
  assert.match(h.written(), /运行时：codex: → pi/);
});

test('其他设置 switches each setting where it is, says what changes, and saves it in the manifest', async (t) => {
  //                    change  other  staged  natural  staged  silence  done  save
  const h = harness(t, ['2',    '10',  '1',    '2',     '1',    '3',     '6',  '11']);
  const before = existing(h, {});
  assert.equal(await setupCommand(h.options), 0);
  assert.deepEqual(h.manifest().agents, [{ ...before, naturalWriting: false, explicitSilence: true }]);
  const text = h.written();
  assert.match(text, /1\) 分段回复\s+关 · 复杂任务先表态，做的过程中发阶段性结论；简单问题仍只回一条\n  2\) 自然写作\s+开 · [^\n]+\n  3\) 显式不回复\s+关 · [^\n]+\n  4\) OpenViking 自动召回\s+关 · [^\n]+\n  5\) 会议纪要入库\s+关 · [^\n]+\n  6\) 完成/);
  assert.match(text, /1\) 分段回复\s+开 · /, 'a switch shows its new value at once');
  assert.match(text, /10\) 其他设置\s+开着：显式不回复（已改）/);
  assert.match(text, /将修改 ASD（asd）：\n  自然写作：开 → 关（模型会开一次新的会话）\n  显式不回复：关 → 开（模型会开一次新的会话）\n/);
  assert.doesNotMatch(text, /分段回复：/, 'switched on and off again is no change');
  assert.deepEqual(h.calls, [['restart', 'asd']]);

  // A switch the manifest never had starts from its default; recall is not a policy change.
  const recall = harness(t, ['2', '10', '4', '6', '11']);
  const old = existing(recall, {});
  for (const key of ['stagedReplies', 'naturalWriting', 'explicitSilence']) delete old[key];
  fs.writeFileSync(path.join(recall.home, '.vika', 'config.json'), JSON.stringify({ version: 1, defaultInstance: old.id, env: {}, agents: [old] }));
  assert.equal(await setupCommand(recall.options), 0);
  assert.deepEqual(recall.manifest().agents, [{ ...old, stagedReplies: false, naturalWriting: true, explicitSilence: false, ovAutoRecall: true }]);
  assert.match(recall.written(), /将修改 ASD（asd）：\n  OpenViking 自动召回：关 → 开\n/);
});

test('a new instance takes a shipped role by what it is for', async (t) => {
  //                   name  role:assistant  workspace  runtime IMs  confirm
  const h = harness(t, ['',  '2',            '',        '1',    '2', 'y']);
  assert.equal(await setupCommand(h.options), 0);
  assert.equal(h.manifest().agents[0].role, 'assistant');
  assert.match(h.written(), /1\) AI 同事  你的 AI 分身[^\n]*\n  2\) 个人助理  只为你工作[^\n]*\n  3\) 答疑助手  [^\n]*\n  4\) 自己写 prompt/);
  assert.match(h.written(), /角色\s+个人助理\n/);
  assert.match(h.written(), /其他设置\s+开着：自然写作（以后在修改实例里调）\n/);
});

test('an instance can use its own role file, begun from a shipped role, written only when saved', async (t) => {
  const roleFile = (h) => path.join(h.home, '.vika', 'roles', 'asd.md');
  //                    change  role  own  default path  start from qa  save
  const h = harness(t, ['2',    '2',  '4', '',           '3',           '11']);
  const before = existing(h, {});
  assert.equal(await setupCommand(h.options), 0);
  assert.deepEqual(h.manifest().agents, [{ ...before, role: roleFile(h) }]);
  const text = fs.readFileSync(roleFile(h), 'utf8');
  assert.match(text, /^<!-- Started from Vika's "qa" role\. This is asd's role: edit it freely, then run vika restart asd\./);
  assert.ok(text.endsWith(fs.readFileSync(fileURLToPath(new URL('../../daemon/agent/roles/qa.md', import.meta.url)), 'utf8')));
  assert.equal(fs.statSync(roleFile(h)).mode & 0o777, 0o600);
  assert.match(h.written(), /角色：没有设置（只用默认规则） → ~\/\.vika\/roles\/asd\.md（模型会开一次新的会话）/);
  assert.deepEqual(h.calls, [['restart', 'asd']]);

  // A file that is there already is used as it is; discarding writes nothing.
  const kept = harness(t, ['2', '2', '4', '~/mine.md', '11']);
  existing(kept, { role: 'colleague' });
  fs.writeFileSync(path.join(kept.home, 'mine.md'), 'my own words\n');
  assert.equal(await setupCommand(kept.options), 0);
  assert.equal(kept.manifest().agents[0].role, path.join(kept.home, 'mine.md'));
  assert.equal(fs.readFileSync(path.join(kept.home, 'mine.md'), 'utf8'), 'my own words\n');
  assert.doesNotMatch(kept.written(), /先用哪个角色的 prompt 填上/);

  const discarded = harness(t, ['2', '2', '4', '', '1', '12']);
  const config = existing(discarded, {});
  assert.equal(await setupCommand(discarded.options), 0);
  assert.deepEqual(discarded.manifest().agents, [config]);
  assert.equal(fs.existsSync(roleFile(discarded)), false);
});

test('dropping the primary IM moves the primary to what is left; a change the daemon would reject goes back to the list', async (t) => {
  //                    change  IMs  weixin  save
  const h = harness(t, ['2',    '5', '2',    '11']);
  existing(h, { connectors: ['lark', 'weixin'], channel: 'lark' });
  assert.equal(await setupCommand(h.options), 0);
  const [after] = h.manifest().agents;
  assert.deepEqual([after.connectors, after.channel, after.profile], [['weixin'], 'weixin', undefined]);
  assert.match(h.written(), /主渠道原来是飞书，它不再接入，改成微信[\s\S]*不再接飞书\n  主渠道：飞书 → 微信/);
  assert.deepEqual(h.calls, [['restart', 'asd']]);

  //                     change IMs  +weixin save  (refused)  leave
  const ark = harness(t, ['2',  '5', '1,2',  '11',            '12']);
  const config = existing(ark, { model: 'ark', volcSessionId: 'sesn-seed12345678', volcEnvironmentId: 'env-12345678' });
  assert.equal(await setupCommand(ark.options), 0);
  assert.match(ark.written(), /4\) Agent 运行时\s+ark\n/);
  assert.match(ark.written(), /这样改不行：.*cannot use the ark runtime[\s\S]*12\) 放弃修改/);
  assert.deepEqual(ark.manifest().agents, [config]);
  assert.deepEqual(ark.calls, []);
});

test('an instance\'s mailboxes are changed from their own menu until done', async (t) => {
  //                    change  mailboxes  remove gmail  done  save
  const h = harness(t, ['2',    '8',       '5',          '1',  '11']);
  const file = (id) => path.join(h.home, '.vika', 'email', 'asd', `${id}.json`);
  for (const [id, address] of [['email-gmail', 'me@gmail.com'], ['email-qq', 'me@qq.com']]) {
    fs.mkdirSync(path.dirname(file(id)), { recursive: true });
    fs.writeFileSync(file(id), JSON.stringify({ address, password: 'p', imap: { host: 'i', port: 993 }, smtp: { host: 's', port: 465 } }), { mode: 0o600 });
  }
  existing(h, { connectors: ['lark', 'email'],
    emailAccounts: [{ id: 'email-gmail', config: file('email-gmail') }, { id: 'email-qq', config: file('email-qq') }] });
  assert.equal(await setupCommand(h.options), 0);
  assert.match(h.written(), /8\) 邮箱\s+me@gmail\.com、me@qq\.com\n/);
  assert.match(h.written(), /邮箱（已接入：me@gmail\.com、me@qq\.com）[\s\S]*邮箱（已接入：me@qq\.com）[\s\S]*8\) 邮箱\s+me@qq\.com（已改）/);
  assert.deepEqual(h.manifest().agents[0].emailAccounts, [{ id: 'email-qq', config: file('email-qq') }]);
  assert.equal(fs.existsSync(file('email-gmail')), false);
  assert.match(h.written(), /移除邮箱 me@gmail\.com（删掉保存的密码）/);
  assert.deepEqual(h.calls, [['restart', 'asd']]);
});

test('setup stops early when no runtime can run, and reports a start failure after saving', async (t) => {
  const none = harness(t, [], { runtimes: RUNTIMES.map((runtime) => ({ ...runtime, available: false })) });
  assert.equal(await setupCommand(none.options), 1);
  assert.match(none.written(), /没有可用的 agent 运行时/);

  const webDown = harness(t, ['', '', '', '1', '1', '', 'y']);
  webDown.options.control.restart = async () => { throw new Error('port 3737 is in use'); };
  assert.equal(await setupCommand(webDown.options), 0);
  assert.match(webDown.written(), /vika-web 没有起来：port 3737 is in use/);
  assert.deepEqual(webDown.calls.map(([kind]) => kind), ['lark', 'start', 'start', 'greet']);

  const failing = harness(t, ['', '', '', '1', '1', '', 'y'], { failStart: true });
  assert.equal(await setupCommand(failing.options), 1);
  assert.equal(failing.manifest().agents.length, 1);
  assert.match(failing.written(), /配置已保存，但启动没有完成：pm2 is not installed/);
});

test('with no runtime on a terminal, setup offers to log the built-in Pi in to a model, then carries on', async (t) => {
  const none = RUNTIMES.map((runtime) => ({ ...runtime, available: false }));
  const h = harness(t, [], { runtimes: none });
  const input = new PassThrough();
  Object.assign(input, { isTTY: true, setRawMode() {} });
  const output = new PassThrough();
  let written = '';
  output.on('data', (chunk) => { written += chunk; });
  h.options.prompter = new Prompter({ input, output, env: {} });
  let detections = 0;
  h.options.detect.runtimes = async () => (detections++ === 0 ? none : RUNTIMES);
  let launched = 0;
  h.options.connect.pi = async () => { launched += 1; };
  const answering = (async () => {
    const until = async (text) => { while (!written.includes(text)) await new Promise((resolve) => setTimeout(resolve, 5)); };
    await until('现在怎么办');
    input.write('\r');
    // Pi found a model: the questions go on, and stopping there saves nothing.
    await until('机器人名字');
    input.write('\x03');
  })();
  await assert.rejects(setupCommand(h.options), { name: 'PromptAborted' });
  await answering;
  assert.deepEqual([launched, detections], [1, 2]);
  assert.match(written, /没有可用的 agent 运行时[\s\S]*找到 Codex、Pi（内置）/);
  assert.equal(fs.existsSync(path.join(h.home, '.vika', 'config.json')), false);
});

// A terminal for setup: keys are typed one at a time, each once the screen
// shows what it answers.
function terminalHarness(t, options) {
  const h = harness(t, [], options);
  const input = new PassThrough();
  Object.assign(input, { isTTY: true, setRawMode() {} });
  const output = new PassThrough();
  let written = '';
  output.on('data', (chunk) => { written += chunk; });
  h.options.prompter = new Prompter({ input, output, env: {} });
  const seen = (text, from) => written.indexOf(text, from);
  // [text to wait for, ...keys]; the wait starts after the last match.
  h.drive = async (steps) => {
    let from = 0;
    for (const [text, ...keys] of steps) {
      const deadline = Date.now() + 5000;
      while (seen(text, from) < 0) {
        if (Date.now() >= deadline) throw new Error(`setup did not show ${text}: ${stripAnsi(written.slice(-2500))}`);
        await new Promise((resolve) => setTimeout(resolve, 2));
      }
      from = seen(text, from) + text.length;
      for (const key of keys) {
        input.write(key);
        await new Promise((resolve) => setTimeout(resolve, 5));
      }
    }
  };
  h.screen = () => written;
  return h;
}

const LEFT = '\x1b[D';
const RIGHT = '\x1b[C';
const DOWN = '\x1b[B';

test('QQ verification cannot go back after Slack has already bound in the same setup', async (t) => {
  const h = terminalHarness(t);
  const verified = [];
  h.options.connect.slack = async ({ canGoBack }) => { verified.push(['slack', canGoBack]); };
  h.options.connect.qq = async ({ canGoBack }) => { verified.push(['qq', canGoBack]); };
  const driving = h.drive([
    ['机器人名字', RIGHT], ['它的角色', RIGHT], ['工作目录', RIGHT], ['Agent 运行时', RIGHT],
    ['接入哪些 IM', ' ', DOWN, DOWN, DOWN, DOWN, ' ', DOWN, ' ', RIGHT],
    ['主渠道', DOWN, RIGHT], ['开始接入', '\r'],
  ]);
  assert.equal(await setupCommand(h.options), 0);
  await driving;
  assert.deepEqual(verified, [['slack', true], ['qq', false]]);
  const [agent] = h.manifest().agents;
  assert.deepEqual(agent.connectors, ['slack', 'qq']);
  assert.equal(agent.channel, 'qq');
});

test('back from removal confirmation returns to the setup menu without changing anything', async (t) => {
  const h = terminalHarness(t);
  const before = existing(h, {});
  const driving = h.drive([
    ['要做什么', DOWN, DOWN, '\r'], ['删除哪个实例', '\r'],
    ['确认删除这个实例', LEFT], ['要做什么', '\x03'],
  ]);
  await assert.rejects(setupCommand(h.options), { name: 'PromptAborted' });
  await driving;
  assert.deepEqual(h.manifest().agents, [before]);
  assert.deepEqual(h.calls, []);
});

test('back from Telegram key entry returns to setup confirmation without saving or starting', async (t) => {
  const h = terminalHarness(t);
  h.options.connect.telegram = connectTelegram;
  const driving = h.drive([
    ['机器人名字', RIGHT], ['它的角色', RIGHT], ['工作目录', RIGHT], ['Agent 运行时', RIGHT],
    ['接入哪些 IM', ' ', DOWN, DOWN, DOWN, ' ', RIGHT],
    ['开始接入', '\r'], ['Telegram Bot token', LEFT], ['开始接入', 'n', '\r'],
  ]);
  assert.equal(await setupCommand(h.options), 1);
  await driving;
  assert.equal(fs.existsSync(path.join(h.home, '.vika', 'config.json')), false);
  assert.equal(fs.existsSync(path.join(h.home, '.vika', 'data', 'primary', 'telegram')), false);
  assert.deepEqual(fs.readdirSync(path.join(h.home, '.vika', 'ov')), []);
  assert.deepEqual(h.calls, []);
});

test('back during Telegram verification cancels polling and returns an existing instance to its settings', async (t) => {
  const h = terminalHarness(t);
  const before = existing(h, {});
  h.options.connect.telegram = connectTelegram;
  let aborted = false;
  h.options.fetchImpl = async (url, { signal }) => {
    if (url.endsWith('/getMe')) return Response.json({ ok: true, result: { id: 123456, is_bot: true, username: 'vika_test_bot' } });
    if (url.endsWith('/getWebhookInfo')) return Response.json({ ok: true, result: { url: '' } });
    return new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => { aborted = true; reject(signal.reason); }, { once: true });
    });
  };
  const driving = h.drive([
    ['要做什么', DOWN, '\r'], ['放弃修改', DOWN, DOWN, DOWN, DOWN, '\r'],
    ['接入哪些 IM', DOWN, DOWN, DOWN, ' ', RIGHT],
    ['放弃修改', DOWN, DOWN, DOWN, DOWN, DOWN, DOWN, '\r'],
    ['Telegram Bot token', '123456:abcdefghijklmnopqrstuvwxyz_TEST', '\r'], ['点 Start', LEFT],
    ['放弃修改', '\x03'],
  ]);
  await assert.rejects(setupCommand(h.options), { name: 'PromptAborted' });
  await driving;
  assert.equal(aborted, true);
  assert.deepEqual(h.manifest().agents, [before]);
  assert.deepEqual(h.calls, []);
  const state = new TelegramState(before.dataDir);
  try { assert.equal(state.account(), null); state.acquire(); } finally { state.close(); }
  assert.doesNotMatch(h.screen(), /123456:abcdefghijklmnopqrstuvwxyz_TEST/);
});

test('← goes back a question with its answer kept, → takes the value shown, all the way to the confirmation', async (t) => {
  const h = terminalHarness(t);
  const driving = h.drive([
    ['机器人名字', '小', 'V', '\r'],
    ['它的角色', LEFT],
    ['机器人名字', RIGHT],
    ['它的角色', RIGHT],
    ['工作目录', RIGHT],
    ['Agent 运行时', RIGHT],
    ['接入哪些 IM', RIGHT],
    ['飞书机器人', RIGHT],
    ['开始扫码', LEFT],
    // Back at the last question asked, back once more into the section, then forward again.
    ['飞书机器人', LEFT],
    ['接入哪些 IM', RIGHT],
    ['飞书机器人', RIGHT],
    ['开始扫码', 'n', '\r'],
  ]);
  assert.equal(await setupCommand(h.options), 1);
  await driving;
  assert.match(h.screen(), /机器人名字（新建的飞书应用也叫这个，@它时用这个名字） \(小V\)/, 'the earlier answer is the default');
  assert.match(h.screen(), /── 确认 ──[\s\S]*── 消息渠道 ──[\s\S]*── 确认 ──/, 'a section is shown again on the way back into it');
  assert.equal(h.calls.length, 0);
  assert.equal(fs.existsSync(path.join(h.home, '.vika', 'config.json')), false);
});

test('a new instance\'s OV menu is a step like the others: ← leaves it, ← comes back to it, and a new choice rewrites the identity', async (t) => {
  const h = terminalHarness(t);
  const before = existing(h, {});
  fs.mkdirSync(path.join(h.home, '.openviking'));
  fs.writeFileSync(path.join(h.home, '.openviking', 'ovcli.conf'), JSON.stringify({ url: 'https://first.example', api_key: 'one' }));
  const saved = JSON.stringify({ url: 'https://second.example', api_key: 'two' });
  fs.writeFileSync(savedOVConfigPath(h.home), saved);
  h.options.configureOV = configureOpenViking;
  h.options.detect.openViking = detectOpenViking;
  h.options.allocate = async () => 3739;
  h.options.fetchImpl = async (url) => {
    if (url.startsWith('http://127.0.0.1:1933')) throw new Error('offline');
    return Response.json({ status: 'ok', auth_mode: 'api_key', result: { initialized: true } });
  };
  const driving = h.drive([
    ['要做什么', '\r'],
    ['选择 OpenViking 连接', LEFT],
    ['要做什么', '\r'],
    // The connection typed in setup last time is listed first.
    ['选择 OpenViking 连接', '\r'],
    ['实例名称', 'fresh', '\r'], ['机器人名字', 'Fresh', '\r'], ['它的角色', '\r'], ['工作目录', '\r'], ['Agent 运行时', '\r'],
    // The instance's OV identity is written by now; all the way back to the menu.
    ['接入哪些 IM', LEFT], ['Agent 运行时', LEFT], ['工作目录', LEFT], ['它的角色', LEFT], ['机器人名字', LEFT], ['实例名称', LEFT],
    ['选择 OpenViking 连接', DOWN, '\r'],
    ['实例名称', RIGHT], ['机器人名字', RIGHT], ['它的角色', RIGHT], ['工作目录', RIGHT], ['Agent 运行时', RIGHT],
    ['接入哪些 IM', '\r'], ['飞书机器人', '\r'], ['开始扫码', '\r'],
  ]);
  assert.equal(await setupCommand(h.options), 0);
  await driving;
  const [unchanged, fresh] = h.manifest().agents;
  assert.deepEqual(unchanged, before);
  assert.deepEqual(fs.readdirSync(path.join(h.home, '.vika', 'ov')), ['fresh.json']);
  assert.deepEqual(JSON.parse(fs.readFileSync(fresh.ovConfig, 'utf8')), { url: 'https://first.example', api_key: 'one', actor_peer_id: 'fresh' });
  assert.equal(fs.readFileSync(savedOVConfigPath(h.home), 'utf8'), saved, 'the CLI config is found anyway; nothing to save');
});

test('changing the instance name on the way back replaces the pending OV identity and cancellation cleans it up', async (t) => {
  const h = terminalHarness(t);
  existing(h, {});
  const actors = [];
  h.options.detect.openViking = async ({ configPath }) => {
    if (configPath) actors.push(JSON.parse(fs.readFileSync(configPath, 'utf8')).actor_peer_id);
    return { configPath: null, url: 'http://127.0.0.1:1933', usable: true };
  };
  const driving = h.drive([
    ['要做什么', '\r'], ['实例名称', RIGHT], ['机器人名字', RIGHT], ['它的角色', RIGHT], ['工作目录', RIGHT], ['Agent 运行时', RIGHT],
    ['接入哪些 IM', LEFT], ['Agent 运行时', LEFT], ['工作目录', LEFT], ['它的角色', LEFT], ['机器人名字', LEFT],
    ['实例名称', 'renamed', '\r'], ['机器人名字', RIGHT], ['它的角色', RIGHT], ['工作目录', RIGHT], ['Agent 运行时', RIGHT],
    ['接入哪些 IM', '\x03'],
  ]);
  await assert.rejects(setupCommand(h.options), { name: 'PromptAborted' });
  await driving;
  assert.deepEqual(actors, ['agent2', 'renamed']);
  assert.deepEqual(fs.readdirSync(path.join(h.home, '.vika', 'ov')), []);
  assert.deepEqual(h.manifest().agents.map(({ id }) => id), ['asd']);
  assert.deepEqual(h.calls, []);
});

test('in the change list, ← leaves a setting as it was; ← on the list goes back to 要做什么', async (t) => {
  const h = terminalHarness(t);
  existing(h, {});
  const driving = h.drive([
    ['要做什么', DOWN, '\r'],
    ['放弃修改', DOWN, DOWN, DOWN, DOWN, '\r'],
    ['接入哪些 IM', DOWN, ' ', LEFT],
    ['放弃修改', LEFT],
    ['要做什么', '\x03'],
  ]);
  await assert.rejects(setupCommand(h.options), { name: 'PromptAborted' });
  await driving;
  assert.match(h.screen(), /5\) 接入的 IM|接入的 IM\s+飞书\n/);
  assert.doesNotMatch(h.screen(), /（已改）/, 'nothing was changed');
  assert.equal(h.screen().split('找到 Codex').length, 2, 'the machine is checked once');
});

test('← in 其他设置 leaves every switch as it was, even ones already flipped', async (t) => {
  const h = terminalHarness(t);
  existing(h, {});
  const driving = h.drive([
    ['要做什么', DOWN, '\r'],
    ['放弃修改', ...Array(9).fill(DOWN), '\r'],
    ['选一项就切换开关', '\r'],
    ['选一项就切换开关', DOWN, '\r'],
    ['选一项就切换开关', LEFT],
    ['放弃修改', '\x03'],
  ]);
  await assert.rejects(setupCommand(h.options), { name: 'PromptAborted' });
  await driving;
  assert.match(h.screen(), /其他设置\s+开着：自然写作\n/);
  assert.doesNotMatch(h.screen(), /（已改）/, 'nothing was changed');
});

test('on a colour terminal, going back and forth leaves only the current way on screen', async (t) => {
  const h = terminalHarness(t);
  existing(h, { connectors: ['lark', 'weixin'], channel: 'lark' });
  const output = h.options.prompter.output;
  Object.assign(output, { isTTY: true, columns: 100, rows: 50, getColorDepth: () => 24 });
  const input = h.options.prompter.input;
  h.options.prompter = new Prompter({ input, output, env: {} });
  h.options.banner = async () => ({ release() {}, resume() {}, top: () => 18 });
  // The same back and forth as: change → 接入的 IM → ← → ← → 新建 → 实例名称 ← → change.
  const driving = h.drive([
    ['要做什么', DOWN, '\r'],
    ['放弃修改', DOWN, DOWN, DOWN, DOWN, '\r'],
    ['接入哪些 IM', LEFT],
    ['放弃修改', LEFT],
    ['要做什么', '\r'],
    ['实例名称', LEFT],
    ['要做什么', DOWN, '\r'],
    ['放弃修改', '\x03'],
  ]);
  await assert.rejects(setupCommand(h.options), { name: 'PromptAborted' });
  await driving;
  const record = stripAnsi(h.options.prompter.transcript.join(''));
  assert.doesNotMatch(record, /↩|新建一个实例|接入哪些 IM|实例名称/);
  assert.equal(record.match(/要做什么 · 修改 ASD（asd）/g).length, 1);
});

test('← while the Feishu code waits stops the registration and goes back; nothing is made', async () => {
  const input = new PassThrough();
  Object.assign(input, { isTTY: true, setRawMode() {} });
  const output = new PassThrough();
  output.resume();
  const prompter = new Prompter({ input, output });
  let aborted = false;
  const register = (options) => new Promise((_resolve, reject) => {
    options.onQRCodeReady({ url: 'https://open.feishu.cn/page/launcher?user_code=A', expireIn: 600 });
    options.signal.addEventListener('abort', () => { aborted = true; reject({ code: 'abort', description: 'aborted' }); });
  });
  setTimeout(() => input.write('\x1b[D'), 20);
  await assert.rejects(registerApp({ plan: { displayName: 'Vika' }, prompter, scopes: { tenant: [], user: [] }, register, canGoBack: true }),
    { name: 'GoBack' });
  assert.equal(aborted, true);
});

test('setup asks for the full permission.json scope set', () => {
  const scopes = larkScopes();
  assert.ok(scopes.tenant.includes('im:message.group_msg'));
  assert.ok(scopes.user.includes('im:message.group_msg:get_as_user'));
  assert.ok(scopes.tenant.length > 100 && scopes.user.length > 100);
});

// The Feishu/Lark connection as setup hands it over, recorded.
function recordLark(h, result = {}) {
  const given = [];
  h.options.connect.lark = async (options) => {
    given.push(options);
    h.calls.push(['lark', options.plan.id, options.profile]);
    return { profile: options.profile, ownerOpenId: 'ou_owner', ...result };
  };
  return given;
}

test('a new instance can pick an existing Feishu bot: the scan is told so, and the owner is warned about sharing it', async (t) => {
  //                   name role ws  runtime IMs  feishu: pick existing  confirm
  const h = harness(t, ['', '',  '', '1',    '1', '2',                   'y']);
  const given = recordLark(h);
  assert.equal(await setupCommand(h.options), 0);
  assert.equal(given[0].source, 'select');
  assert.equal(given[0].app, null);
  assert.match(h.written(), /飞书机器人\n  1\) 扫码新建一个机器人.*\n  2\) 扫码选择已有的机器人.*\n  3\) 手动填 App ID 和 App Secret/);
  const { tenant, user } = larkScopes();
  assert.match(h.written(), new RegExp(`确认页会列出要给它补的权限（最多 ${tenant.length + user.length} 个）[^\\n]*它要是有版本正在审核，会先被撤回。不确认就什么都不改。`));
  assert.match(h.written(), /同一个飞书应用不能两处同时收消息[\s\S]*Vika 只接用长连接接收消息的机器人：用 webhook 的，先在开放平台「事件与回调」把事件和回调都改成长连接/);
  assert.match(h.written(), /飞书机器人\s+扫码选择已有的机器人\n/);
  assert.match(h.written(), /接下来扫码：飞书 2 次/);
});

test('typing the App ID and App Secret is a question like the others: checked at once, one scan left', async (t) => {
  //                   name role ws  runtime IMs  feishu: by hand  App ID     secret  tenant  confirm
  const h = harness(t, ['', '',  '', '1',    '1', '3',             'cli_abc', 'sec',  '2',    'y']);
  const given = recordLark(h);
  const checked = [];
  h.options.fetchImpl = async (url, init) => {
    if (!url.endsWith('/tenant_access_token/internal')) return { json: async () => ({ code: 99991672, msg: 'no permission' }) };
    checked.push([url, JSON.parse(init.body)]);
    return { json: async () => ({ code: 0, tenant_access_token: 't' }) };
  };
  assert.equal(await setupCommand(h.options), 0);
  assert.deepEqual(checked, [['https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal', { app_id: 'cli_abc', app_secret: 'sec' }]]);
  assert.equal(given[0].source, 'manual');
  assert.deepEqual(given[0].app, { appId: 'cli_abc', appSecret: 'sec', brand: 'lark', ownerOpenId: null });
  const output = h.written();
  assert.ok(output.indexOf('App ID 和 App Secret 没问题') < output.indexOf('将添加实例'), 'checked before the confirmation');
  assert.match(output, /飞书机器人\s+应用 cli_abc\n/);
  assert.match(output, /接下来扫码：飞书 1 次/);
  assert.match(output, /同一个飞书应用不能两处同时收消息/);
  assert.doesNotMatch(output, /确认页会列出/, 'typed in, the app is not changed');
  assert.doesNotMatch(JSON.stringify(h.manifest()), /sec"/);
});

test('Feishu is offered with the account it needs; a scan by one that cannot run bots comes back to the confirmation with the answers kept', async (t) => {
  //                   name    role ws  runtime IMs  feishu  confirm  confirm again
  const h = harness(t, ['小V', '',  '', '1',    '1', '',     'y',     'y']);
  const notice = '飞书不给应用 cli_doubao 发访问凭证：豆包账号';
  let tries = 0;
  h.options.connect.lark = async ({ plan, profile }) => {
    tries += 1;
    h.calls.push(['lark', plan.id, profile]);
    if (tries === 1) throw new GoBack(notice);
    return { profile, ownerOpenId: 'ou_owner' };
  };
  assert.equal(await setupCommand(h.options), 0);
  const output = h.written();
  assert.match(output, /1\) 飞书 {2}手机号注册的飞书账号（注册时会建一个免费企业，你是管理员）或公司的飞书账号；用豆包账号登录的飞书不行\n/);
  // Said after the screen comes back to the confirmation, then asked again.
  assert.match(output, new RegExp(`⚠ ${notice}\\n[\\s\\S]*将添加实例 primary[\\s\\S]*开始扫码？`));
  assert.equal(h.manifest().agents[0].displayName, '小V');
  assert.deepEqual(h.calls.map((call) => call[0]), ['lark', 'lark', 'start', 'restart', 'start', 'greet']);
});

test('changing the app to one whose account cannot run bots comes back to the list, saying why, with nothing saved', async (t) => {
  //                    change asd  飞书应用  create  save  discard
  const h = harness(t, ['2',        '6',      '2',    '11', '12']);
  const before = existing(h, {});
  h.options.connect.lark = async () => { throw new GoBack('飞书不给应用 cli_doubao 发访问凭证'); };
  assert.equal(await setupCommand(h.options), 0);
  assert.match(h.written(), /⚠ 飞书不给应用 cli_doubao 发访问凭证\n[\s\S]*6\) 飞书应用\s+扫码新建一个机器人（已改）/);
  assert.match(h.written(), /已放弃，没有保存任何改动/);
  assert.deepEqual(h.manifest().agents, [before]);
});

test('an App ID typed in whose account cannot run bots is told so, not called wrong', async (t) => {
  //                   name role ws  runtime IMs  by hand  App ID        secret  tenant  again?
  const h = harness(t, ['', '',  '', '1',    '1', '3',     'cli_doubao', 's',    '',     'n']);
  h.options.fetchImpl = async () => ({ json: async () => ({ code: 21001, msg: 'access denied by access control policy' }) });
  await assert.rejects(setupCommand(h.options), { name: 'PromptAborted' });
  const output = h.written();
  assert.match(output, /平台不给这个应用发访问凭证\n  ⚠ 飞书不给应用 cli_doubao 发访问凭证（tenant_access_token failed: code=21001 access denied by access control policy）：它所在的飞书账号不能运行机器人，用豆包账号登录的飞书就是这样。换一个手机号注册的飞书账号（注册时会建一个免费企业，你是管理员）或公司的飞书账号下的应用/);
  assert.doesNotMatch(output, /App ID 或 App Secret 不对/);
});

test('an instance with Feishu can switch to another app; the bot takes the app\'s name unless the owner keeps theirs', async (t) => {
  //                    change asd  飞书应用  pick existing  save  call it 小助手
  const h = harness(t, ['2',        '6',      '3',           '11', 'y']);
  const before = existing(h, {});
  fs.mkdirSync(path.join(h.home, '.vika'), { recursive: true });
  const given = recordLark(h, { name: '小助手' });
  assert.equal(await setupCommand(h.options), 0);
  assert.match(h.written(), /6\) 飞书应用\s+扫码选择已有的机器人（已改）/);
  assert.match(h.written(), /将修改 ASD（asd）：\n  换飞书应用：扫码选择已有的机器人（需要扫码）\n/);
  assert.match(h.written(), /接下来扫码：飞书 2 次/);
  assert.deepEqual([given[0].source, given[0].profile], ['select', 'vika-asd']);
  assert.match(h.written(), /这个机器人在飞书里叫「小助手」，Vika 里也这么叫它？（选否就还叫「ASD」）/);
  assert.deepEqual(h.manifest().agents, [{ ...before, displayName: '小助手' }]);
  assert.deepEqual(h.calls.map((call) => call.slice(0, 2)), [['lark', 'asd'], ['restart', 'asd'], ['greet', 'ou_owner']]);

  // A bot long called something else keeps that name.
  const keeping = harness(t, ['2', '6', '3', '11', 'n']);
  const kept0 = existing(keeping, {});
  recordLark(keeping, { name: '小助手' });
  assert.equal(await setupCommand(keeping.options), 0);
  assert.deepEqual(keeping.manifest().agents, [kept0]);
  assert.match(keeping.calls.find(([kind]) => kind === 'greet')[2], /我是 ASD/);

  // Keeping the app connects nothing.
  const kept = harness(t, ['2', '6', '1', '11']);
  existing(kept, {});
  assert.equal(await setupCommand(kept.options), 0);
  assert.match(kept.written(), /没有改动/);
  assert.deepEqual(kept.calls, []);
});

test('ticking Feishu on an instance that had none asks where its bot comes from, right away', async (t) => {
  //                    change asd  IMs  lark+weixin  pick existing  save
  const h = harness(t, ['2',        '5', '1,2',       '2',           '11']);
  const before = existing(h, { channel: 'weixin', connectors: ['weixin'], profile: undefined });
  const given = recordLark(h);
  assert.equal(await setupCommand(h.options), 0);
  assert.equal(given[0].source, 'select');
  assert.deepEqual(h.manifest().agents, [{ ...before, connectors: ['lark', 'weixin'], profile: 'vika-asd' }]);
  assert.doesNotMatch(h.written(), /不换，用现在的应用/, 'there is no current app to keep');
});

test('an App ID typed in for a bot on a webhook is refused there, and another one can be typed', async (t) => {
  //                   name role ws  runtime IMs  by hand  App ID      secret  tenant  another?  App ID      secret  tenant  confirm
  const h = harness(t, ['', '',  '', '1',    '1', '3',     'cli_hook', 's1',   '',     'y',      'cli_good', 's2',   '',     'y']);
  const given = recordLark(h);
  h.options.fetchImpl = async (url, init = {}) => ({ json: async () => {
    if (url.endsWith('/tenant_access_token/internal')) return { code: 0, tenant_access_token: `t-${JSON.parse(init.body).app_id}` };
    const hooked = init.headers.authorization === 'Bearer t-cli_hook';
    if (url.includes('/applications/me?')) {
      return { code: 0, data: { app: { app_id: 'x', scopes: [],
        callback_info: { callback_type: hooked ? 'webhook' : 'websocket', subscribed_callbacks: ['card.action.trigger'] } } } };
    }
    return { code: 0, bot: { activate_status: 2 } };
  } });
  assert.equal(await setupCommand(h.options), 0);
  assert.match(h.written(), /这个机器人的卡片回调走 webhook，事件多半也是，Vika 接不了[\s\S]*换一个应用？/);
  assert.equal(given[0].app.appId, 'cli_good');
});

test('the listener sees a Feishu message that arrives after it was made, and nothing older', async () => {
  const asked = [];
  const pages = [[], [{ id: 8, kind: 'message.sent', metadata: { source: 'lark' } }],
    [{ id: 9, kind: 'message.received', metadata: { source: 'weixin' } }], [{ id: 10, kind: 'message.received', metadata: { source: 'lark' } }]];
  const fetchImpl = async (url) => {
    asked.push(new URL(url).search);
    assert.ok(url.startsWith('http://127.0.0.1:3738/api/activity?'));
    const activities = url.endsWith('limit=1') ? [{ id: 7, kind: 'message.received', metadata: { source: 'lark' } }] : pages.shift();
    return { ok: true, json: async () => ({ activities }) };
  };
  const listener = await larkMessageListener({ port: 3738, fetchImpl, intervalMs: 1 });
  assert.equal(await listener.wait(new AbortController().signal), true);
  assert.deepEqual(asked, ['?limit=1', '?after=7&limit=100', '?after=7&limit=100', '?after=8&limit=100', '?after=9&limit=100']);

  // Not answering when made: the first answer is the starting point. Aborted: false.
  let up = false;
  const late = await larkMessageListener({ port: 3738, intervalMs: 1, fetchImpl: async (url) => {
    if (!up) { up = true; throw new Error('ECONNREFUSED'); }
    return { ok: true, json: async () => ({ activities: url.endsWith('limit=1') ? [{ id: 3, kind: 'message.received', metadata: { source: 'lark' } }] : [] }) };
  } });
  const controller = new AbortController();
  setTimeout(() => controller.abort(), 30);
  assert.equal(await late.wait(controller.signal), false, 'what was there before does not count');
});

test('on a terminal, setup asks the owner to reply on Feishu and waits; s skips and leaves the checklist', async (t) => {
  for (const arrives of [true, false]) {
    const h = terminalHarness(t);
    h.options.listen = async ({ port }) => {
      h.calls.push(['listen', port]);
      return { wait: (signal) => (arrives ? Promise.resolve(true)
        : new Promise((resolve) => signal.addEventListener('abort', () => resolve(false)))) };
    };
    const driving = h.drive([
      ['机器人名字', RIGHT], ['它的角色', RIGHT], ['工作目录', RIGHT], ['Agent 运行时', RIGHT], ['接入哪些 IM', RIGHT], ['飞书机器人', RIGHT],
      ['开始扫码', '\r'], ['确认它收得到你的消息', ...(arrives ? [] : ['s'])],
    ]);
    assert.equal(await setupCommand(h.options), 0);
    await driving;
    const screen = stripAnsi(h.screen());
    assert.deepEqual(h.calls.map(([kind]) => kind), ['lark', 'start', 'restart', 'start', 'listen', 'greet']);
    assert.deepEqual(h.calls.find(([kind]) => kind === 'listen'), ['listen', 3738]);
    assert.match(screen, /在飞书里回它一句（说什么都行），确认它收得到你的消息/);
    if (arrives) {
      assert.match(screen, /收到你的消息了，飞书这条路是通的/);
      assert.match(screen, /飞书：已经收到你的消息，接着聊就行/);
    } else {
      assert.match(screen, /跳过了；之后它要是收不到你的消息，按下面查：\n {4}1\. 开放平台「事件与回调」里，事件订阅方式是长连接/);
      assert.match(screen, /4\. 运行 ~\/\.vika\/bin\/vika logs primary 看飞书连接有没有报错/);
      assert.match(screen, /飞书：私聊里它已经跟你打了招呼，直接回它/);
    }
  }
});

// lark-cli as connectLark uses it, recorded.
function fakeLarkCli(profiles = []) {
  const calls = [];
  return {
    calls,
    profiles: async () => profiles,
    configureProfile: async ({ profile, appId }) => { calls.push(['profile', profile, appId]); },
    beginUserLogin: async ({ scopes }) => { calls.push(['login', scopes]); return { url: 'https://accounts.feishu.cn/x', deviceCode: 'd' }; },
    finishUserLogin: async () => {},
    userOpenId: async () => 'ou_owner',
  };
}

// The platform as a picked app's own token sees it: some scopes, its callbacks on a webhook, no message event.
function pickedApp(granted, { pending = false, callbacks = { callback_type: 'websocket', subscribed_callbacks: [] } } = {}) {
  return async (url) => ({ json: async () => {
    if (url.endsWith('/tenant_access_token/internal')) return { code: 0, tenant_access_token: 't' };
    if (url.includes('/applications/me?')) {
      return { code: 0, data: { app: { app_id: 'cli_old', app_name: '小助手', online_version_id: 'oav_1', unaudit_version_id: pending ? 'oav_2' : '',
        scopes: granted, callback_info: callbacks } } };
    }
    if (url.includes('/app_versions/')) return { code: 0, data: { app_version: { event_infos: [] } } };
    return { code: 0, bot: { activate_status: 2, app_name: '小助手' } };
  } });
}

function pipePrompter(answers) {
  const input = new PassThrough();
  input.end(answers.map((line) => `${line}\n`).join(''));
  const output = new PassThrough();
  let written = '';
  output.on('data', (chunk) => { written += chunk; });
  return { prompter: new Prompter({ input, output }), written: () => written };
}

test('connecting a picked app: another instance on it is a warning, what it lacks is named, and only its user scopes are asked for', async () => {
  const scopes = larkScopes();
  const granted = [...scopes.tenant.slice(2).map((scope) => ({ scope, token_types: ['tenant'] })),
    ...scopes.user.slice(0, 2).map((scope) => ({ scope, token_types: ['user'] }))];
  const register = async (options) => {
    assert.equal('createOnly' in options, false);
    return { client_id: 'cli_old', client_secret: 's', user_info: { open_id: 'ou_owner' } };
  };
  const others = [{ id: 'other', displayName: 'Other', profile: 'vika-other' }, { id: 'third', displayName: 'Third', profile: 'vika-third' }];
  const larkCli = fakeLarkCli([{ name: 'vika-other', appId: 'cli_old' }, { name: 'vika-third', appId: 'cli_else' }, { name: 'mine', appId: 'cli_old' }]);
  const { prompter, written } = pipePrompter(['y']);
  const result = await connectLark({ plan: { displayName: 'Vika' }, prompter, larkCli, fetchImpl: pickedApp(granted, { pending: true }),
    profile: 'vika-new', source: 'select', others, register });
  assert.deepEqual(result, { profile: 'vika-new', ownerOpenId: 'ou_owner', name: '小助手' });
  const output = written();
  assert.match(output, /实例 Other（other）也在用应用 cli_old：同时运行时，每条消息只会随机推给其中一个实例。/);
  assert.match(output, /应用有一个新版本在等管理员审核：下面缺的可能就在里面，审核通过后生效。\n.*有 \d+ 个权限没开通/);
  assert.match(output, /没有订阅接收消息事件（im\.message\.receive_v1）：机器人收不到消息/);
  assert.match(output, /没有订阅文档评论事件（drive\.notice\.comment_add_v1）：在飞书文档的评论里 @ 机器人，它收不到/);
  assert.match(output, /卡片回调（card\.action\.trigger）没有用长连接订阅/);
  assert.match(output, /之后运行 vika setup 可以扫码补齐/);
  assert.doesNotMatch(output, /事件走长连接还是 webhook 读不到/, 'a scanned app had its events set by the platform');
  assert.deepEqual(larkCli.calls, [['profile', 'vika-new', 'cli_old'], ['login', scopes.user.slice(0, 2)]]);

  // Declining goes back to the questions, before the profile is written.
  const declined = fakeLarkCli([{ name: 'vika-other', appId: 'cli_old' }]);
  const refusing = pipePrompter(['n']);
  await assert.rejects(connectLark({ plan: { displayName: 'Vika' }, prompter: refusing.prompter, larkCli: declined,
    fetchImpl: pickedApp(granted), profile: 'vika-new', source: 'select', others, register }), { name: 'GoBack' });
  assert.deepEqual(declined.calls, []);

  // A bot on a webhook is refused before anything is asked or written; why
  // goes back with it, for setup to say where it comes back to.
  const hooked = fakeLarkCli([{ name: 'vika-other', appId: 'cli_old' }]);
  const refused = pipePrompter([]);
  await assert.rejects(connectLark({ plan: { displayName: 'Vika' }, prompter: refused.prompter, larkCli: hooked,
    fetchImpl: pickedApp(granted, { callbacks: { callback_type: 'webhook', subscribed_callbacks: ['card.action.trigger'] } }),
    profile: 'vika-new', source: 'select', others, register }),
  (error) => error.name === 'GoBack' && /这个机器人的卡片回调走 webhook，事件多半也是，Vika 接不了/.test(error.notice));
  assert.doesNotMatch(refused.written(), /仍然用这个应用/);
  assert.deepEqual(hooked.calls, []);
});

// The platform as an app of a Doubao-account Feishu sees it: the app is
// there, its token never is.
const policyRefused = async (url) => ({ json: async () => (url.endsWith('/tenant_access_token/internal')
  ? { code: 21001, msg: 'access denied by access control policy' } : { code: 0 }) });

test('an app made by an account that cannot run bots goes back to the questions, saying why; nothing is written', async () => {
  const larkCli = fakeLarkCli();
  const { prompter, written } = pipePrompter([]);
  const register = async () => ({ client_id: 'cli_doubao', client_secret: 's', user_info: { open_id: 'ou_doubao', tenant_brand: 'feishu' } });
  const error = await connectLark({ plan: { displayName: 'Vika' }, prompter, larkCli, fetchImpl: policyRefused, profile: 'vika-new',
    source: 'create', register }).then(() => null, (rejected) => rejected);
  assert.equal(error?.name, 'GoBack');
  assert.match(error.notice, /飞书不给应用 cli_doubao 发访问凭证（tenant_access_token failed: code=21001 access denied by access control policy）：它所在的飞书账号不能运行机器人，用豆包账号登录的飞书就是这样。这个应用已经建好但用不了，可以在开放平台删掉。用手机号注册的飞书账号（注册时会建一个免费企业，你是管理员）或公司的飞书账号重新扫码，或者回去不接飞书/);
  assert.match(written(), /已创建应用 cli_doubao/);
  assert.deepEqual(larkCli.calls, [], 'no profile, no owner authorization');

  // Picked, not made: there is nothing new to delete.
  const picked = await connectLark({ plan: { displayName: 'Vika' }, prompter: pipePrompter([]).prompter, larkCli: fakeLarkCli(),
    fetchImpl: policyRefused, profile: 'vika-new', source: 'select', register }).then(() => null, (rejected) => rejected);
  assert.equal(picked?.name, 'GoBack');
  assert.doesNotMatch(picked.notice, /已经建好/);
});

test('connecting an app typed in: no scan for it, and how its events arrive is left to check', async () => {
  const scopes = larkScopes();
  const granted = [...scopes.tenant, ...scopes.user].map((scope) => ({ scope, token_types: scopes.tenant.includes(scope) ? ['tenant', 'user'] : ['user'] }));
  const larkCli = fakeLarkCli();
  const { prompter, written } = pipePrompter([]);
  const register = async () => { throw new Error('no scan for an app typed in'); };
  const result = await connectLark({ plan: { displayName: 'Vika' }, prompter, larkCli, fetchImpl: pickedApp(granted), profile: 'vika-new',
    source: 'manual', app: { appId: 'cli_old', appSecret: 's', brand: 'feishu', ownerOpenId: null }, register });
  assert.equal(result.ownerOpenId, 'ou_owner');
  const output = written();
  assert.match(output, /用应用 cli_old/);
  assert.match(output, /权限核对通过/);
  assert.match(output, /事件走长连接还是 webhook 读不到/);
  assert.match(output, /缺的在飞书开放平台给应用补上；你是应用的所有者或管理员的话，也可以之后运行 vika setup 扫码补齐/);
  assert.match(output, /扫码授权 Vika 以你的身份读消息/);
  assert.doesNotMatch(output, /第 1 次扫码|第 2 次扫码/);
});

test('the vika command prints usage, and upgrade lists the configured targets', (t) => {
  const bin = fileURLToPath(new URL('../bin/vika.mjs', import.meta.url));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-help-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const env = { ...process.env, HOME: home, VIKA_LANG: 'zh' };
  const help = spawnSync(process.execPath, [bin, 'help'], { encoding: 'utf8', env });
  assert.equal(help.status, 0);
  assert.match(help.stdout, /vika <命令>[\s\S]*setup[\s\S]*upgrade/);
  const english = spawnSync(process.execPath, [bin, '--lang', 'en', 'help'], { encoding: 'utf8', env });
  assert.match(english.stdout, /vika <command>[\s\S]*setup[\s\S]*--lang <zh\|en>/);
  assert.doesNotMatch(english.stdout, /[一-龥]/);
  const wrong = spawnSync(process.execPath, [bin, '--lang', 'fr', 'help'], { encoding: 'utf8', env });
  assert.equal(wrong.status, 2);
  assert.equal(wrong.stderr, '--lang 只能是 zh 或 en\n');
  // --lang is kept; alone it only switches.
  const { VIKA_LANG, ...plain } = env;
  const switched = spawnSync(process.execPath, [bin, '--lang', 'en'], { encoding: 'utf8', env: plain });
  assert.equal(switched.stdout, 'The interface language is now English\n');
  assert.equal(fs.readFileSync(path.join(home, '.vika', 'language'), 'utf8'), 'en\n');
  assert.match(spawnSync(process.execPath, [bin, 'help'], { encoding: 'utf8', env: plain }).stdout, /vika <command>/);
  const unknown = spawnSync(process.execPath, [bin, 'nope'], { encoding: 'utf8', env });
  assert.equal(unknown.status, 2);
  const upgrade = spawnSync(process.execPath, [bin, 'upgrade', 'beta', 'extra'], {
    encoding: 'utf8',
    env: { ...process.env, VIKA_UPDATER_TARGETS_JSON: JSON.stringify({
      beta: { app: 'vika-beta', readyUrl: 'http://127.0.0.1:3740/ready', upgradeSharedWeb: false },
    }) },
  });
  assert.equal(upgrade.status, 2);
  assert.equal(upgrade.stderr, 'usage: vika upgrade <beta|all>\n');
});

test('when the scan confirmation never arrives, m switches to typing the App ID and Secret, checked with the platform', async () => {
  const input = new PassThrough();
  input.isTTY = true;
  input.setRawMode = () => {};
  const output = new PassThrough();
  let written = '';
  output.on('data', (chunk) => { written += chunk; });
  const prompter = new Prompter({ input, output });
  let polls = 0;
  let aborted = false;
  // A registration whose confirmation never arrives, until setup gives up on it.
  const register = (options) => new Promise((_resolve, reject) => {
    options.onQRCodeReady({ url: 'https://open.feishu.cn/page/launcher?user_code=A', expireIn: 600 });
    const timer = setInterval(() => { polls += 1; options.onStatusChange({ status: 'polling' }); }, 2);
    options.signal.addEventListener('abort', () => { clearInterval(timer); aborted = true; reject({ code: 'abort', description: 'aborted' }); });
  });
  const tried = [];
  const fetchImpl = async (url, init) => {
    if (!url.endsWith('/tenant_access_token/internal')) return { json: async () => ({ code: 99991672, msg: 'no permission' }) };
    const { app_id: appId, app_secret: secret } = JSON.parse(init.body);
    tried.push([url, appId]);
    return { json: async () => (secret === 'the-secret' ? { code: 0, tenant_access_token: 't' } : { code: 10014, msg: 'app secret invalid' }) };
  };
  const pause = () => new Promise((resolve) => setTimeout(resolve, 20));
  const typing = (async () => {
    while (polls < 2) await new Promise((resolve) => setTimeout(resolve, 5));
    input.write('m');
    // A wrong secret first: the platform refuses it, and the App ID is kept for the second try.
    for (const line of ['cli_abc123\r', 'wrong\r', '\r', 'y\r', '\r', 'the-secret\r', '\r']) {
      await pause();
      input.write(line);
    }
  })();
  const app = await registerApp({ plan: { displayName: 'Vika' }, prompter, scopes: { tenant: [], user: [] }, register, fetchImpl });
  await typing;
  assert.equal(aborted, true, 'the abandoned registration stops polling');
  assert.deepEqual(app, { appId: 'cli_abc123', appSecret: 'the-secret', brand: 'feishu', ownerOpenId: null });
  assert.deepEqual(tried.map(([, appId]) => appId), ['cli_abc123', 'cli_abc123']);
  assert.ok(tried.every(([url]) => url === 'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal'));
  assert.match(written, /打开刚创建的应用/);
  assert.match(written, /平台不认这组凭证（tenant_access_token failed: code=10014 app secret invalid）/);
  assert.doesNotMatch(written, /the-secret|wrong/);
});

const ENGLISH_RUNTIMES = RUNTIMES.map((runtime) => ({ ...runtime, label: runtime.id === 'pi' ? 'Pi (built in)' : runtime.label, note: 'ok' }));

test('English setup offers QQ and describes credentials and private-chat binding', async (t) => {
  const h = harness(t, ['QQ Agent', '', '', '1', '6', 'y'], { runtimes: ENGLISH_RUNTIMES });
  h.options.env.VIKA_LANG = 'en';
  assert.equal(await setupCommand(h.options), 0);
  assert.equal(h.manifest().agents[0].channel, 'qq');
  assert.equal(h.calls[0][0], 'qq');
  assert.doesNotMatch(h.written(), /[一-鿿]|undefined/);
  assert.match(h.written(), /Next, connecting: QQ AppID \/ AppSecret \+ private-chat verification code/);
  assert.match(h.written(), /QQ: Message the bot you just bound/);
});

test('in English, a first setup says it all in English, and the bot says hello in English', async (t) => {
  //                   name   role  workspace  runtime IMs    channel feishu confirm
  const h = harness(t, ['Vee', '',   '',        '1',    '1,2', '1',    '',    'y'], { runtimes: ENGLISH_RUNTIMES });
  h.options.env.VIKA_LANG = 'en';
  assert.equal(await setupCommand(h.options), 0);
  const text = h.written();
  assert.doesNotMatch(text, /[一-鿿]/);
  assert.match(text, /^Vika setup: add, change or remove the agent instances on this machine\.\n\n  ✔ Found Codex, Pi \(built in\)\n/);
  assert.match(text, /Primary channel \(where the bot reaches you first, e\.g\. new-mail alerts\)/);
  assert.match(text, /Next, scanning: Feishu\/Lark 2 scans, WeChat 1 scan; the instance is saved after the scans are done/);
  assert.match(text, /Vee \(primary\) is ready\.\n  Feishu\/Lark: It said hello in your chat; just reply\n  WeChat: Say "hi" to it first/);
  const [, , hello] = h.calls.find(([kind]) => kind === 'greet');
  assert.match(hello, /^Hi, I'm Vee, now running on your computer\. .* When I need to reach you on my own/);
  // VIKA_LANG is the owner's own choice, so later commands keep it.
  assert.equal(fs.readFileSync(path.join(h.home, '.vika', 'language'), 'utf8'), 'en\n');
});

test('with a locale that is not Chinese, a terminal setup asks the language first, once, and keeps it', async (t) => {
  const h = terminalHarness(t);
  h.options.env.LANG = 'en_US.UTF-8';
  // Chinese comes first; this owner takes it.
  const driving = h.drive([['Language / 语言', '\r'], ['机器人名字', '\x03']]);
  await assert.rejects(setupCommand(h.options), { name: 'PromptAborted' });
  await driving;
  assert.match(h.screen(), /Language \/ 语言（↑↓ 移动，回车确认）\n\x1b\[\?25l❯ 中文\n {2}English/);
  assert.ok(h.screen().indexOf('Language / 语言') < h.screen().indexOf('Vika setup：'), 'asked before anything else is said');
  assert.equal(fs.readFileSync(path.join(h.home, '.vika', 'language'), 'utf8'), 'zh\n');

  const again = terminalHarness(t);
  again.options.env.LANG = 'en_US.UTF-8';
  fs.mkdirSync(path.join(again.home, '.vika'), { recursive: true });
  fs.writeFileSync(path.join(again.home, '.vika', 'language'), 'en\n');
  const drivingAgain = again.drive([['Bot name', '\x03']]);
  await assert.rejects(setupCommand(again.options), { name: 'PromptAborted' });
  await drivingAgain;
  assert.doesNotMatch(again.screen(), /Language/);

  // Without a terminal nothing is asked or kept: Chinese it is.
  const piped = harness(t, ['', '', '', '1', '1', '', 'n']);
  piped.options.env.LANG = 'en_US.UTF-8';
  assert.equal(await setupCommand(piped.options), 1);
  assert.match(piped.written(), /什么都没保存；准备好了再运行 ~\/\.vika\/bin\/vika setup。/);
  assert.equal(fs.existsSync(path.join(piped.home, '.vika', 'language')), false);
});

test('in English, changing an instance: the list, what a setting says, the changes and the result', async (t) => {
  //                    change asd  IMs  lark+weixin  primary  weixin  save
  const h = harness(t, ['2', '5', '1,2', '7', '2', '11'], { runtimes: ENGLISH_RUNTIMES });
  h.options.env.VIKA_LANG = 'en';
  const before = existing(h, {});
  assert.equal(await setupCommand(h.options), 0);
  assert.deepEqual(h.manifest().agents, [{ ...before, connectors: ['lark', 'weixin'], channel: 'weixin' }]);
  const text = h.written();
  assert.doesNotMatch(text, /[一-鿿]/);
  assert.match(text, /1\) Bot name\s+ASD\n  2\) Role\s+none \(default rules only\)\n  3\) Workspace\s+~\/ws\n[\s\S]*  6\) Feishu\/Lark app\s+the current app\n[\s\S]*  10\) Other settings\s+on: Natural writing\n  11\) Save\n  12\) Discard changes/);
  // What a setting says shows above the list drawn after it.
  assert.doesNotMatch(text, /No OpenViking found|Stop using it/);
  assert.match(text, /11\) Save \(2 changes\)/);
  assert.match(text, /Changing ASD \(asd\):\n  Connect WeChat \(needs a scan\)\n  Primary channel: Feishu\/Lark → WeChat\n/);
  assert.match(text, /ASD \(asd\) is updated\.\n  WeChat: Say "hi" to it first/);
});

test('meeting notes: on for the first assistant or colleague on Feishu, off for a Q&A bot and for a second instance', async (t) => {
  //                   name role workspace runtime IMs feishu confirm
  const h = harness(t, ['', '',  '',       '3',    '1', '',    'y']);
  assert.equal(await setupCommand(h.options), 0);
  assert.equal(h.manifest().agents[0].meetingNotes, true);
  assert.match(h.written(), /其他设置\s+开着：自然写作、会议纪要入库（以后在修改实例里调）/);

  const second = harness(t, ['1', '', 'Second', '', '', '3', '1', '', 'y']);
  fs.mkdirSync(path.join(second.home, '.vika'), { recursive: true });
  fs.copyFileSync(path.join(h.home, '.vika', 'config.json'), path.join(second.home, '.vika', 'config.json'));
  second.options.allocate = async () => 3739;
  assert.equal(await setupCommand(second.options), 0);
  assert.deepEqual(second.manifest().agents.map(({ id, meetingNotes }) => [id, meetingNotes]), [['primary', true], ['agent2', false]]);

  //                    name role:qa workspace runtime IMs feishu confirm
  const qa = harness(t, ['', '3',    '',       '3',    '1', '',    'y']);
  assert.equal(await setupCommand(qa.options), 0);
  assert.equal(qa.manifest().agents[0].meetingNotes, false);
});

test('会议纪要入库 is one instance\'s: refused while another has it, switched on like any other setting', async (t) => {
  //                    change asd  other  notes  done  discard
  const h = harness(t, ['2',        '10',  '5',   '6',  '12']);
  const asd = existing(h, {});
  const bee = { ...asd, id: 'bee', displayName: 'BEE', profile: 'vika-bee', port: 3739, dataDir: path.join(h.home, '.vika', 'data', 'bee'),
    workspace: path.join(h.home, 'ws-bee'), ovConfig: path.join(h.home, '.vika', 'ov', 'bee.json'), meetingNotes: true };
  const two = { version: 1, defaultInstance: 'asd', env: {}, agents: [asd, bee] };
  fs.writeFileSync(path.join(h.home, '.vika', 'config.json'), JSON.stringify(two));
  assert.equal(await setupCommand(h.options), 0);
  assert.match(h.written(), /会议纪要已经由 BEE（bee）入库：一台机器上只有一个实例能开，先修改 bee 把它关掉\n[\s\S]*5\) 会议纪要入库\s+关 · /);
  assert.deepEqual(h.manifest(), two);
  assert.deepEqual(h.calls, []);

  //                     change asd  other  notes  done  save
  const on = harness(t, ['2',        '10',  '5',   '6',  '11']);
  const before = existing(on, {});
  assert.equal(await setupCommand(on.options), 0);
  assert.deepEqual(on.manifest().agents, [{ ...before, meetingNotes: true }]);
  assert.deepEqual(on.calls, [['restart', 'asd']], 'the app needs nothing more for it');
  assert.match(on.written(), /会议纪要入库：关 → 开（模型会开一次新的会话）\n/);
});

test('an instance whose Feishu app lacks what this release needs says so; its 飞书应用 setting completes it on saving', async (t) => {
  //                   change asd  feishu app  add what is missing  save
  const h = harness(t, ['2',        '6',        '1',                 '11']);
  const before = existing(h, {});
  const needs = (agent) => ({ agent, count: 3, missing: { scopes: { tenant: ['im:message'], user: [] }, events: ['im.message.receive_v1', 'drive.notice.comment_add_v1'], callbacks: [] }, login: [] });
  h.options.detect.larkApps = async ({ manifest }) => [needs(manifest.agents[0])];
  h.options.connect.larkApp = async ({ agent }) => { h.calls.push(['larkApp', agent.id]); return true; };
  assert.equal(await setupCommand(h.options), 0);
  const text = h.written();
  assert.match(text, /1\) 新建一个实例\n  2\) 修改 ASD（asd）\s+飞书 · 飞书应用缺 3 项\n/);
  assert.match(text, /6\) 飞书应用\s+现在的应用（缺 3 项：1 个权限、接收消息事件、文档评论事件）\n/);
  assert.match(text, /1\) 扫码补齐缺的 3 项\s+1 个权限、接收消息事件、文档评论事件；保存时扫码，你要是这个应用的所有者或管理员\n  2\) 不换，用现在的应用/);
  assert.match(text, /6\) 飞书应用\s+现在的应用，保存时扫码补齐缺的 3 项（已改）/);
  assert.match(text, /将修改 ASD（asd）：\n  补齐飞书应用缺的权限和事件（需要扫码）\n/);
  assert.deepEqual(h.calls, [['larkApp', 'asd'], ['restart', 'asd']], 'completed before the instance restarts');
  assert.deepEqual(h.manifest().agents, [before], 'the manifest is as it was');
});

test('what an instance lacks: the app\'s scopes, events and callback, and scopes the owner\'s login does not carry yet', async (t) => {
  const scopes = { tenant: ['im:message', 'im:chat:read'], user: ['search:message', 'vc:note:read'] };
  const larkCli = (login, tenant = scopes.tenant) => ({
    calls: [],
    async get({ pathname }) {
      this.calls.push(pathname);
      if (pathname.endsWith('/applications/me')) {
        return { code: 0, data: { app: { app_id: 'cli_1', online_version_id: 'v1',
          scopes: [...tenant.map((scope) => ({ scope, token_types: ['tenant'] })), ...scopes.user.map((scope) => ({ scope, token_types: ['user'] }))],
          callback_info: { callback_type: 'websocket', subscribed_callbacks: ['card.action.trigger'] } } } };
      }
      if (pathname.includes('/app_versions/')) {
        return { code: 0, data: { app_version: { event_infos: [
          { event_type: 'im.message.receive_v1' }, { event_type: 'drive.notice.comment_add_v1' },
        ] } } };
      }
      return { code: 0, data: { activate_status: 2 } };
    },
    async userScopes() { return login; },
    async beginUserLogin(options) { this.calls.push(['login', options.scopes]); return { url: 'https://accounts.feishu.cn/x', deviceCode: 'dc' }; },
    async finishUserLogin() { this.calls.push('finished'); },
  });
  const { larkNeeds, updateLarkApp } = await import('../src/setup.mjs');
  const complete = await larkNeeds({ agent: { profile: 'vika-asd', meetingNotes: true }, larkCli: larkCli(scopes.user), scopes });
  assert.equal(complete.count, 0, 'storing meeting notes needs nothing of the app beyond the permission list');
  const short = await larkNeeds({ agent: { profile: 'vika-asd' }, larkCli: larkCli(['search:message'], ['im:message']), scopes });
  assert.deepEqual(short.missing.scopes.tenant, ['im:chat:read']);
  assert.deepEqual(short.login, ['vc:note:read']);
  assert.equal(short.count, 2);

  // Declining the scan still renews the owner's login for what the app has.
  const input = new PassThrough();
  input.end('n\n');
  const output = new PassThrough();
  let written = '';
  output.on('data', (chunk) => { written += chunk; });
  const cli = larkCli(['search:message'], ['im:message']);
  const updated = await updateLarkApp({ agent: { id: 'asd', displayName: 'ASD', profile: 'vika-asd' },
    prompter: new Prompter({ input, output }), larkCli: cli, scopes, register: async () => { throw new Error('not asked for'); } });
  assert.equal(updated, false);
  assert.match(written, /有 1 个权限没开通，相关功能会不可用：im:chat:read/);
  assert.match(written, /你给 ASD 的授权还缺 1 个权限（vc:note:read），再扫码授权一次/);
  assert.deepEqual(cli.calls.filter((call) => Array.isArray(call)), [['login', scopes.user]]);
  assert.ok(cli.calls.includes('finished'));
});
