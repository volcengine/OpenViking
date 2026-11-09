// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { loginCommand } from '../src/login.mjs';
import { Prompter } from '../src/prompt.mjs';
import { OWNER_MESSAGE_SCOPES, larkScopes } from '../src/setup.mjs';

function agent(home, id, overrides = {}) {
  return {
    id, displayName: id.toUpperCase(), channel: 'lark', connectors: ['lark'], profile: `vika-${id}`,
    port: 3738, dataDir: path.join(home, '.vika', 'data', id), workspace: path.join(home, 'ws', id), model: 'codex:',
    ovConfig: path.join(home, '.vika', 'ov', `${id}.json`),
    ...overrides,
  };
}

function machine(t, agents, { answers = [], tty = false } = {}) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-login-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const list = agents(home);
  if (list.length) {
    fs.mkdirSync(path.join(home, '.vika'), { recursive: true });
    fs.writeFileSync(path.join(home, '.vika', 'config.json'),
      JSON.stringify({ version: 1, defaultInstance: list[0].id, env: {}, agents: list }));
  }
  const input = new PassThrough();
  if (tty) { input.isTTY = true; input.setRawMode = () => {}; } else input.end(answers.map((line) => `${line}\n`).join(''));
  const output = new PassThrough();
  let written = '';
  output.on('data', (chunk) => { written += chunk; });
  const calls = [];
  const options = {
    prompter: new Prompter({ input, output }),
    env: { HOME: home, PATH: '', LARK_CLI_BIN: '/opt/lark-cli' },
    connect: {
      weixin: async ({ plan }) => { calls.push(['weixin', plan.id]); },
      email: async ({ fixed, taken }) => { calls.push(['email', fixed?.address ?? null, taken]); return { ...fixed, password: 'new' }; },
    },
    control: { restart: async (id) => { calls.push(['restart', id]); } },
  };
  const manifest = () => JSON.parse(fs.readFileSync(path.join(home, '.vika', 'config.json'), 'utf8'));
  return { home, input, options, calls, written: () => written, manifest };
}

test('login renews what an instance has; connecting a new IM is setup\'s', async (t) => {
  const m = machine(t, (home) => [agent(home, 'haha', { channel: 'weixin', connectors: ['weixin'], profile: undefined })]);
  const before = fs.readFileSync(path.join(m.home, '.vika', 'config.json'), 'utf8');
  assert.equal(await loginCommand(['weixin'], m.options), 0);
  assert.deepEqual(m.calls, [['weixin', 'haha']]);
  // A new login is a new conversation: the owner writes first again.
  assert.match(m.written(), /最后一步：在微信里先给机器人发一句话/);
  assert.equal(fs.readFileSync(path.join(m.home, '.vika', 'config.json'), 'utf8'), before, 'the manifest is untouched');

  await assert.rejects(loginCommand(['lark'], m.options), /还没有实例接飞书；要接入，运行 vika setup 修改实例/);
  await assert.rejects(loginCommand(['email', 'haha'], m.options), /实例 haha 没有接邮箱；要接入，运行 vika setup，选择修改 haha/);
});

test('a mailbox password is renewed for the mailbox chosen, and the instance restarts', async (t) => {
  const file = (home, id) => path.join(home, '.vika', 'email', 'wx', `${id}.json`);
  const m = machine(t, (home) => {
    for (const [id, address] of [['email-gmail', 'me@gmail.com'], ['email-qq', 'me@qq.com']]) {
      fs.mkdirSync(path.dirname(file(home, id)), { recursive: true });
      fs.writeFileSync(file(home, id), JSON.stringify({ address, password: 'old', imap: { host: 'i', port: 993 }, smtp: { host: 's', port: 465 } }), { mode: 0o600 });
    }
    return [agent(home, 'wx', { channel: 'weixin', connectors: ['weixin', 'email'], profile: undefined,
      emailAccounts: [{ id: 'email-gmail', config: file(home, 'email-gmail') }, { id: 'email-qq', config: file(home, 'email-qq') }] })];
  }, { answers: ['2'] });
  assert.equal(await loginCommand(['email'], m.options), 0);
  assert.deepEqual(m.calls, [['email', 'me@qq.com', ['me@gmail.com']], ['restart', 'wx']]);
  assert.equal(JSON.parse(fs.readFileSync(file(m.home, 'email-qq'), 'utf8')).password, 'new');
  assert.equal(JSON.parse(fs.readFileSync(file(m.home, 'email-gmail'), 'utf8')).password, 'old');
  assert.match(m.written(), /更新哪个邮箱的密码[\s\S]*的邮箱 me@qq\.com 已更新/);
});

test('with several instances it asks which one, among those that have the IM', async (t) => {
  const three = (home) => [agent(home, 'alpha', { channel: 'weixin', connectors: ['weixin'], profile: undefined }),
    agent(home, 'beta', { port: 3739 }), agent(home, 'gamma', { port: 3740, channel: 'weixin', connectors: ['weixin'], profile: undefined })];
  const piped = machine(t, three);
  await assert.rejects(loginCommand(['weixin'], piped.options), /有多个实例[\s\S]*alpha\s+ALPHA\s+微信[\s\S]*gamma/);
  await assert.rejects(loginCommand(['weixin', 'delta'], piped.options), /没有叫 delta 的实例/);
  assert.deepEqual(piped.calls, []);

  const terminal = machine(t, three, { tty: true });
  setTimeout(() => terminal.input.write('\x1b[B\r'), 20);
  assert.equal(await loginCommand(['weixin'], terminal.options), 0);
  assert.deepEqual(terminal.calls[0], ['weixin', 'gamma'], 'beta has no Weixin, so the second choice is gamma');
});

test('usage, and nothing to renew before setup', async (t) => {
  const empty = machine(t, () => []);
  await assert.rejects(loginCommand(['weixin'], empty.options), /先运行 vika setup/);
  assert.equal(await loginCommand(['setup'], empty.options), 2);
  assert.match(empty.written(), /用法：vika login <lark\|weixin\|email\|telegram\|slack\|qq\|onebot> \[实例\]\n  登录过期或改了密码后重新授权；要接入新的 IM，运行 vika setup 修改实例/);
});

test('Telegram login stops the poller before verification and restarts with the new key', async (t) => {
  const m = machine(t, (home) => [agent(home, 'wa', { channel: 'telegram', connectors: ['telegram'], profile: undefined })]);
  m.options.control.stop = async (id) => { m.calls.push(['stop', id]); };
  m.options.connect.telegram = async ({ plan }) => { m.calls.push(['telegram', plan.id]); };
  assert.equal(await loginCommand(['telegram'], m.options), 0);
  assert.deepEqual(m.calls, [['stop', 'wa'], ['telegram', 'wa'], ['restart', 'wa']]);
});

test('Slack login stops the receiver before verification and restarts with the new tokens', async (t) => {
  const m = machine(t, (home) => [agent(home, 'sl', { channel: 'slack', connectors: ['slack'], profile: undefined })]);
  m.options.control.stop = async (id) => { m.calls.push(['stop', id]); };
  m.options.connect.slack = async ({ plan }) => { m.calls.push(['slack', plan.id]); };
  assert.equal(await loginCommand(['slack'], m.options), 0);
  assert.deepEqual(m.calls, [['stop', 'sl'], ['slack', 'sl'], ['restart', 'sl']]);
});

test('OneBot login stops the instance, so it does not take the code as a message, and restarts with the new account', async (t) => {
  const m = machine(t, (home) => [agent(home, 'ob', { channel: 'onebot', connectors: ['onebot'], profile: undefined, model: 'pi' })]);
  m.options.control.stop = async (id) => { m.calls.push(['stop', id]); };
  m.options.connect.onebot = async ({ plan }) => { m.calls.push(['onebot', plan.id]); };
  assert.equal(await loginCommand(['onebot'], m.options), 0);
  assert.deepEqual(m.calls, [['stop', 'ob'], ['onebot', 'ob'], ['restart', 'ob']]);
  assert.match(m.written(), /OB（ob）的 QQ（OneBot） 已更新/);
  await assert.rejects(loginCommand(['qq'], m.options), /还没有实例接QQ；/);
});

test('failed Telegram verification resumes the existing instance and reports the failure', async (t) => {
  const m = machine(t, (home) => [agent(home, 'wa', { channel: 'telegram', connectors: ['telegram'], profile: undefined })]);
  m.options.control.stop = async (id) => { m.calls.push(['stop', id]); };
  m.options.connect.telegram = async () => { throw new Error('invalid API key'); };
  await assert.rejects(loginCommand(['telegram'], m.options), /invalid API key/);
  assert.deepEqual(m.calls, [['stop', 'wa'], ['restart', 'wa']]);
});

// A lark-cli that answers as an app with a few of permission.json's scopes, until
// `updated` is written next to it; it records what it is asked.
function fakeLarkCli(dir) {
  const bin = path.join(dir, 'lark-cli');
  const { tenant, user } = larkScopes();
  const few = { tenant: tenant.slice(0, 3), user: user.slice(0, 2) };
  fs.writeFileSync(bin, `#!${process.execPath}
const fs = require('node:fs');
const path = require('node:path');
const here = path.dirname(process.argv[1]);
const args = process.argv.slice(2);
fs.appendFileSync(path.join(here, 'calls.log'), JSON.stringify(args) + '\\n');
const updated = fs.existsSync(path.join(here, 'updated'));
const pending = fs.existsSync(path.join(here, 'pending'));
const scopes = updated ? ${JSON.stringify({ tenant, user })} : ${JSON.stringify(few)};
const out = (value) => process.stdout.write(JSON.stringify(value));
const at = args.indexOf('api');
if (at >= 0) {
  const target = args[at + 2];
  if (target.endsWith('/applications/me')) {
    out({ ok: true, data: { app: { app_id: 'cli_old', app_name: 'Old', online_version_id: 'oav_1', unaudit_version_id: pending ? 'oav_2' : '',
      scopes: [...scopes.tenant.map((scope) => ({ scope, token_types: ['tenant'] })), ...scopes.user.map((scope) => ({ scope, token_types: ['user'] }))],
      callback_info: { callback_type: 'websocket', subscribed_callbacks: ['card.action.trigger'] } } } });
  } else if (target.includes('/app_versions/')) {
    out({ ok: true, data: { app_version: { event_infos: [{ event_type: 'im.message.receive_v1' }] } } });
  } else {
    out({ ok: true, data: { activate_status: 2, app_name: 'Old' } });
  }
} else if (args.includes('--no-wait')) {
  out({ verification_uri_complete: 'https://accounts.feishu.cn/oauth?code=1', device_code: 'dc' });
}
`, { mode: 0o755 });
  const calls = () => fs.readFileSync(path.join(dir, 'calls.log'), 'utf8').trim().split('\n').map((line) => JSON.parse(line));
  return { bin, few, calls };
}

test('Feishu login checks the app first; its owner adds what it lacks with one scan, then authorizes', async (t) => {
  const m = machine(t, (home) => [agent(home, 'haha')], { answers: ['y'] });
  const lark = fakeLarkCli(m.home);
  m.options.env.LARK_CLI_BIN = lark.bin;
  let asked;
  m.options.connect.register = async (options) => {
    asked = options;
    fs.writeFileSync(path.join(m.home, 'updated'), '');
    return { client_id: 'cli_old', client_secret: 's', user_info: { open_id: 'ou_owner' } };
  };
  assert.equal(await loginCommand(['lark'], m.options), 0);
  assert.equal(asked.appId, 'cli_old');
  assert.equal('createOnly' in asked, false);
  const output = m.written();
  const { tenant, user } = larkScopes();
  assert.match(output, new RegExp(`有 ${tenant.length - 3 + user.length - 2} 个权限没开通`));
  assert.match(output, /扫码把缺的补上？要你是这个应用的所有者或管理员/);
  assert.match(output, /平台已确认[\s\S]*权限核对通过/);
  const calls = lark.calls();
  assert.ok(calls.every((args) => args[0] === '--profile' && args[1] === 'vika-haha'));
  assert.ok(calls.filter((args) => args.includes('api')).every((args) => args.at(-2) === '--as' && args.at(-1) === 'bot'));
  const login = calls.find((args) => args.includes('--no-wait'));
  assert.equal(login[login.indexOf('--scope') + 1], larkScopes().user.join(' '), 'after the update every user scope is there');
  assert.match(output, /vika-haha 已重新授权/);
  assert.deepEqual(m.calls, [], 'the running instance is not restarted');
});

test('Feishu login without the update asks the owner only for the user scopes the app has', async (t) => {
  const m = machine(t, (home) => [agent(home, 'haha')], { answers: ['n'] });
  const lark = fakeLarkCli(m.home);
  m.options.env.LARK_CLI_BIN = lark.bin;
  m.options.connect.register = async () => { throw new Error('not asked for'); };
  assert.equal(await loginCommand(['lark'], m.options), 0);
  const login = lark.calls().find((args) => args.includes('--no-wait'));
  assert.equal(login[login.indexOf('--scope') + 1], lark.few.user.join(' '));
});

test('Feishu login for a bot that reads only its own chats never asks the owner for their message scopes', async (t) => {
  const m = machine(t, (home) => [agent(home, 'haha', { larkMessageAccess: 'bot' })], { answers: ['n'] });
  const lark = fakeLarkCli(m.home);
  // The app has everything, the owner's message scopes too.
  fs.writeFileSync(path.join(m.home, 'updated'), '');
  m.options.env.LARK_CLI_BIN = lark.bin;
  assert.equal(await loginCommand(['lark'], m.options), 0);
  const login = lark.calls().find((args) => args.includes('--no-wait'));
  assert.equal(login[login.indexOf('--scope') + 1], larkScopes('bot').user.join(' '));
  for (const scope of OWNER_MESSAGE_SCOPES) assert.ok(!login[login.indexOf('--scope') + 1].split(' ').includes(scope));
  assert.match(m.written(), /以你的身份用文档、日历等（不读你的消息）/);
});

test('Feishu login with a version under review says the scan would withdraw it, and does not scan by default', async (t) => {
  const m = machine(t, (home) => [agent(home, 'haha')], { answers: [''] });
  const lark = fakeLarkCli(m.home);
  fs.writeFileSync(path.join(m.home, 'pending'), '');
  m.options.env.LARK_CLI_BIN = lark.bin;
  m.options.connect.register = async () => { throw new Error('not asked for'); };
  assert.equal(await loginCommand(['lark'], m.options), 0);
  assert.match(m.written(), /应用有一个版本正在审核：扫码补齐时，飞书会先撤回它再提交新版本，审批要重新排队。/);
  assert.match(m.written(), /扫码把缺的补上？要你是这个应用的所有者或管理员 \[y\/N\]/);
  assert.match(m.written(), /vika-haha 已重新授权/);
});

for (const fail of [false, true]) test(`QQ login stops the receiver and restores the instance after verification (failure=${fail})`, async (t) => {
  const m = machine(t, (home) => [agent(home, 'qq', { channel: 'qq', connectors: ['qq'], profile: undefined })]);
  m.options.control.stop = async (id) => { m.calls.push(['stop', id]); };
  m.options.connect.qq = async ({ plan }) => { m.calls.push(['qq', plan.id]); if (fail) throw new Error('cancelled'); };
  if (fail) await assert.rejects(loginCommand(['qq'], m.options), /cancelled/);
  else assert.equal(await loginCommand(['qq'], m.options), 0);
  assert.deepEqual(m.calls, [['stop', 'qq'], ['qq', 'qq'], ['restart', 'qq']]);
});
