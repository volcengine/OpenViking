// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {
  LOCAL_OV_URL, OVRegistrations, detectOpenViking, instanceOvConfig, instanceUserName, requestNamesUser, writeInstanceOvConfig,
} from '../src/ov.mjs';
import { detectRuntimes, which } from '../src/runtimes.mjs';

function tempDir(t, prefix) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return directory;
}

function healthServer(responses) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, headers: init.headers });
    const answer = responses[url] ?? (url.endsWith('/api/v1/system/status') ? { status: 'ok', result: { initialized: true } } : null);
    if (!answer) throw new TypeError('fetch failed');
    return Response.json(answer);
  };
  return { calls, fetchImpl };
}

test('OpenViking: the user config wins, its credentials prove who the user is', async (t) => {
  const home = tempDir(t, 'vika-ov-');
  fs.mkdirSync(path.join(home, '.openviking'));
  fs.writeFileSync(path.join(home, '.openviking', 'ovcli.conf'), JSON.stringify({
    url: 'https://ov.example', api_key: 'key-1', account: 'acme', plugin: { recall: true }, agent_id: 'legacy', actor_peer_id: 'source-actor',
  }));
  const { calls, fetchImpl } = healthServer({
    'https://ov.example/health': { status: 'ok', auth_mode: 'api_key', user_id: 'amy', version: '0.4' },
  });
  const detected = await detectOpenViking({ env: {}, homeDir: home, fetchImpl });
  assert.deepEqual(detected, {
    configPath: path.join(home, '.openviking', 'ovcli.conf'), url: 'https://ov.example', usable: true,
    user: 'amy', account: null, role: null, authMode: 'api_key', reason: null,
  });
  assert.equal(calls[0].headers['X-API-Key'], 'key-1');
  assert.equal(calls[0].headers['X-OpenViking-Account'], 'acme');

  // Under an API key the key is the identity; the copy names no peer.
  const target = path.join(home, '.vika', 'ov', 'primary.json');
  writeInstanceOvConfig({ config: await instanceOvConfig({ detected, instanceId: 'primary' }), target });
  assert.deepEqual(JSON.parse(fs.readFileSync(target, 'utf8')), {
    url: 'https://ov.example', api_key: 'key-1', account: 'acme', plugin: { recall: true },
  });
  assert.equal(fs.statSync(target).mode & 0o777, 0o600);
  assert.equal(JSON.parse(fs.readFileSync(detected.configPath, 'utf8')).actor_peer_id, 'source-actor');
});

test('OpenViking: where requests name the user, each instance is a user of its own', async (t) => {
  const home = tempDir(t, 'vika-ov-');
  const trusted = { configPath: null, url: LOCAL_OV_URL, usable: true, authMode: 'trusted',
    config: { url: LOCAL_OV_URL, api_key: 'root-key', account: 'team', user: 'owner', actor_peer_id: 'old' } };
  assert.deepEqual(await instanceOvConfig({ detected: trusted, instanceId: 'asd' }),
    { url: LOCAL_OV_URL, api_key: 'root-key', account: 'team', user: 'owner.asd' });
  // The instance's own config chosen again keeps its name.
  assert.equal((await instanceOvConfig({ detected: { ...trusted, config: { ...trusted.config, user: 'owner.asd' } }, instanceId: 'asd' })).user,
    'owner.asd');
  // A server that does not say how it authenticates: no key means requests name the user.
  assert.equal((await instanceOvConfig({ detected: { url: LOCAL_OV_URL, config: { url: LOCAL_OV_URL } }, instanceId: 'asd' })).user, 'asd');
  assert.equal(instanceUserName('', 'asd'), 'asd');
  assert.equal(instanceUserName('weihan.liu', 'asd'), 'weihan.liu.asd');
  assert.equal(instanceUserName('weihan.liu.asd', 'asd'), 'weihan.liu.asd');
  assert.equal(requestNamesUser('api_key', {}), false);
  assert.equal(requestNamesUser(null, { api_key: 'k' }), false);
  assert.ok(fs.readdirSync(home).length === 0);
});

test('OpenViking: an administrator key creates the instance its own user, and the administrator key is not kept', async () => {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, headers: init.headers, body: init.body && JSON.parse(init.body) });
    if (init.body && JSON.parse(init.body).user_id === 'taken.asd') return Response.json({ status: 'error' }, { status: 409 });
    return Response.json({ status: 'ok', result: { account_id: 'team', user_id: 'boss.asd', user_key: 'new-user-key' } });
  };
  const detected = { url: 'https://ov.example', usable: true, authMode: 'api_key', role: 'admin', account: 'team', user: 'boss',
    identity: { mode: 'register' }, config: { url: 'https://ov.example', api_key: 'admin-key', user: 'boss', plugin: { recall: true } } };
  const config = await instanceOvConfig({ detected, instanceId: 'asd', fetchImpl });
  assert.deepEqual(config, { url: 'https://ov.example', api_key: 'new-user-key', user: 'boss.asd', plugin: { recall: true } });
  assert.equal(calls[0].url, 'https://ov.example/api/v1/admin/accounts/team/users');
  assert.equal(calls[0].headers['X-API-Key'], 'admin-key');
  assert.deepEqual(calls[0].body, { user_id: 'boss.asd', role: 'user' });
  await assert.rejects(instanceOvConfig({ detected: { ...detected, user: 'taken' }, instanceId: 'asd', fetchImpl }),
    /already has a user taken\.asd|已有用户 taken\.asd/);
});

test('OpenViking: a config whose server is down is reported, not offered', async (t) => {
  const home = tempDir(t, 'vika-ov-');
  const config = path.join(home, 'custom.conf');
  fs.writeFileSync(config, JSON.stringify({ url: 'https://down.example' }));
  const detected = await detectOpenViking({ env: { OPENVIKING_CLI_CONFIG_FILE: config }, homeDir: home, fetchImpl: healthServer({}).fetchImpl });
  assert.equal(detected.usable, false);
  assert.match(detected.reason, /服务不可用/);
});

test('OpenViking: without a config only a dev-mode local server is usable', async (t) => {
  const home = tempDir(t, 'vika-ov-');
  const dev = await detectOpenViking({ env: {}, homeDir: home, fetchImpl: healthServer({
    [`${LOCAL_OV_URL}/health`]: { status: 'ok', auth_mode: 'dev' },
  }).fetchImpl });
  assert.equal(dev.usable, true);
  const target = path.join(home, 'dev.json');
  writeInstanceOvConfig({ config: await instanceOvConfig({ detected: dev, instanceId: 'primary' }), target });
  assert.deepEqual(JSON.parse(fs.readFileSync(target, 'utf8')), { url: LOCAL_OV_URL, user: 'primary' });

  const trusted = await detectOpenViking({ env: {}, homeDir: home, fetchImpl: healthServer({
    [`${LOCAL_OV_URL}/health`]: { status: 'ok', auth_mode: 'trusted' },
  }).fetchImpl });
  assert.equal(trusted.usable, false);
  assert.match(trusted.reason, /凭据/);
  assert.equal((await detectOpenViking({ env: {}, homeDir: home, fetchImpl: healthServer({}).fetchImpl })).usable, false);
});

test('OpenViking: public health never substitutes for a protected credential check', async (t) => {
  const home = tempDir(t, 'vika-ov-auth-');
  const configPath = path.join(home, 'instance.json');
  fs.writeFileSync(configPath, JSON.stringify({ url: 'https://ov.example', api_key: 'secret' }));
  const calls = [];
  const detected = await detectOpenViking({ homeDir: home, env: {}, configPath, fetchImpl: async (url, init) => {
    calls.push(url);
    assert.equal(init.headers['X-API-Key'], 'secret');
    return url.endsWith('/health')
      ? Response.json({ status: 'ok', auth_mode: 'api_key' })
      : Response.json({ status: 'error', message: 'secret remote details' }, { status: 401 });
  } });
  assert.equal(detected.usable, false);
  assert.match(detected.reason, /服务已启动.*HTTP 401/);
  assert.deepEqual(calls, ['https://ov.example/health', 'https://ov.example/api/v1/system/status']);
  assert.doesNotMatch(detected.reason, /secret/);
});

test('OpenViking: the identity check costs one status call, names the user, and waits out a server still starting', async (t) => {
  const home = tempDir(t, 'vika-ov-status-');
  const config = { url: 'https://ov.example', api_key: 'secret' };
  for (const [initialized, usable] of [[true, true], [false, false]]) {
    const calls = [];
    const detected = await detectOpenViking({ homeDir: home, env: {}, config, fetchImpl: async (url) => {
      calls.push(url);
      return url.endsWith('/health')
        ? Response.json({ status: 'ok', auth_mode: 'api_key' })
        : Response.json({ status: 'ok', result: { initialized, user: 'amy' } });
    } });
    assert.deepEqual(calls, ['https://ov.example/health', 'https://ov.example/api/v1/system/status']);
    assert.equal(detected.usable, usable);
    if (usable) assert.equal(detected.user, 'amy');
    else assert.match(detected.reason, /还在初始化/);
  }
});

test('OpenViking: a running Trusted server without Account or User requires configuration', async (t) => {
  const home = tempDir(t, 'vika-ov-trusted-');
  for (const identity of [{}, { account: 'team' }, { user: 'owner' }]) {
    const result = await detectOpenViking({ env: {}, homeDir: home, config: { url: LOCAL_OV_URL, ...identity },
      fetchImpl: async (url) => url.endsWith('/health')
        ? Response.json({ status: 'ok', auth_mode: 'trusted' })
        : Response.json({ status: 'error', message: 'private server details' }, { status: 400 }),
    });
    assert.equal(result.usable, false);
    assert.equal(result.authMode, 'trusted');
    assert.match(result.reason, /服务已启动.*Account.*User/);
    assert.doesNotMatch(result.reason, /服务不可用|private server details/);
  }
});

test('OpenViking: a missing explicit config never falls back to the default identity', async (t) => {
  const home = tempDir(t, 'vika-ov-missing-');
  let calls = 0;
  const detected = await detectOpenViking({ env: {}, homeDir: home, configPath: path.join(home, 'missing'),
    fetchImpl: async () => { calls++; return Response.json({ status: 'ok' }); } });
  assert.equal(detected.usable, false);
  assert.equal(calls, 0);
});

test('runtimes: installed and logged-in CLIs are offered, the rest say what is missing', async (t) => {
  const bin = tempDir(t, 'vika-bin-');
  for (const name of ['codex', 'claude']) fs.writeFileSync(path.join(bin, name), '#!/bin/sh\n', { mode: 0o755 });
  assert.equal(which('codex', { PATH: bin }), path.join(bin, 'codex'));
  assert.equal(which('traecli', { PATH: bin }), null);

  const execFileImpl = async (command, args) => {
    if (command.endsWith('codex')) return { stdout: 'Logged in using ChatGPT\n' };
    if (command.endsWith('claude')) return { stdout: JSON.stringify({ loggedIn: false }) };
    if (command === process.execPath && args.includes('--list-models')) {
      return { stdout: 'provider        model    context\nvolcengine-ark  ep-1     1M\n' };
    }
    if (command === process.execPath && args.includes('-e')) return { stdout: '[]' };
    throw new Error(`unexpected ${command}`);
  };
  const runtimes = await detectRuntimes({ env: { PATH: bin }, execFileImpl, piBin: '/managed/pi.js' });
  assert.deepEqual(runtimes.map(({ id, available, model }) => ({ id, available, model })), [
    { id: 'codex', available: true, model: 'codex:' },
    { id: 'claude', available: false, model: 'claude:' },
    { id: 'pi', available: true, model: 'pi' },
    { id: 'durable', available: false, model: 'durable' },
    { id: 'opencode', available: false, model: 'opencode' },
    { id: 'agy', available: false, model: 'agy' },
    { id: 'dsh', available: false, model: 'dsh' },
  ]);
  assert.equal(runtimes[0].bin, path.join(bin, 'codex'));
  assert.match(runtimes[1].note, /claude auth login/);
  assert.match(runtimes[2].note, /volcengine-ark\/ep-1/);
  assert.match(runtimes[3].note, /OPENAI_API_KEY/);
});

test('runtimes: durable is offered when a provider has credentials', async () => {
  const execFileImpl = async (command, args) => {
    if (args.includes('-e')) return { stdout: '["openai/gpt-4o"]' };
    return { stdout: '' };
  };
  const runtimes = await detectRuntimes({ env: { PATH: '' }, execFileImpl, piBin: '/managed/pi.js' });
  const durable = runtimes.find(({ id }) => id === 'durable');
  assert.equal(durable.available, true);
  assert.match(durable.note, /openai\/gpt-4o/);
});

test('runtimes: the durable probe sees provider keys from ~/.vika/secrets.env', async (t) => {
  const home = tempDir(t, 'vika-home-');
  fs.mkdirSync(path.join(home, '.vika'));
  fs.writeFileSync(path.join(home, '.vika', 'secrets.env'), '# comment\nOPENAI_API_KEY=sk-test\nOTHER=ignored\n');
  let probeEnv = null;
  const execFileImpl = async (command, args, options) => {
    if (args.includes('-e')) { probeEnv = options.env; return { stdout: '["openai/gpt-4o"]' }; }
    return { stdout: '' };
  };
  const runtimes = await detectRuntimes({ env: { HOME: home }, execFileImpl, piBin: '/managed/pi.js' });
  const durable = runtimes.find(({ id }) => id === 'durable');
  assert.equal(durable.available, true);
  assert.equal(probeEnv.OPENAI_API_KEY, 'sk-test');
  assert.equal(probeEnv.OTHER, undefined);
});

test('runtimes: a Pi with no model points the user at vika pi', async () => {
  const runtimes = await detectRuntimes({
    env: { PATH: '' }, piBin: '/managed/pi.js',
    execFileImpl: async () => ({ stdout: 'No models available.\n' }),
  });
  assert.deepEqual(runtimes.map(({ id, available }) => [id, available]), [
    ['codex', false], ['claude', false], ['pi', false], ['durable', false], ['opencode', false], ['agy', false], ['dsh', false],
  ]);
  assert.match(runtimes[2].note, /vika pi/);
});

function traexFixture(t, names) {
  const bin = tempDir(t, 'vika-traex-');
  for (const name of names) fs.writeFileSync(path.join(bin, name), '#!/bin/sh\nexit 1\n', { mode: 0o755 });
  const probed = [];
  const logins = [];
  const options = {
    env: { PATH: bin }, piBin: '/managed/pi.js',
    probe: async (command) => { probed.push(command); return { compatible: true }; },
    execFileImpl: async (command, args) => {
      if (args.includes('--list-models')) return { stdout: '' };
      assert.deepEqual(args, ['login', 'status']);
      logins.push(command);
      return { stdout: '' };
    },
  };
  return { bin, options, probed, logins, detect: async () => (await detectRuntimes(options)).find(({ id }) => id === 'traex') };
}

test('TraeX: rejects an old TraeX instead of equating an executable traecli with a usable runtime', async (t) => {
  const f = traexFixture(t, ['traecli']);
  delete f.options.probe; // Exercise the real subprocess probe against exit 1.
  const runtime = await f.detect();
  assert.equal(runtime.installed, true);
  assert.equal(runtime.protocolCompatible, false);
  assert.equal(runtime.available, false);
  assert.match(runtime.note, /app-server.*TraeX/);
  assert.deepEqual(f.logins, [], 'never invoke the old CLI login flow');
});

test('TraeX: discovers a new-only traex installation and prefers it when an old traecli coexists', async (t) => {
  for (const names of [['traex'], ['traecli', 'traex']]) {
    const f = traexFixture(t, names);
    const runtime = await f.detect();
    assert.equal(runtime.bin, path.join(f.bin, 'traex'));
    assert.equal(runtime.protocolCompatible, true);
    assert.equal(runtime.loggedIn, true);
    assert.equal(runtime.available, true);
    assert.deepEqual(f.probed, [runtime.bin]);
  }
});

test('TraeX: a compatible traecli or trae-cli alias is supported', async (t) => {
  for (const name of ['traecli', 'trae-cli']) {
    const f = traexFixture(t, [name]);
    const runtime = await f.detect();
    assert.equal(runtime.available, true);
    assert.equal(runtime.bin, path.join(f.bin, name));
  }
});

test('TraeX: reports login failure separately from protocol incompatibility', async (t) => {
  const f = traexFixture(t, ['traex']);
  const base = f.options.execFileImpl;
  f.options.execFileImpl = async (command, args) => {
    if (args[0] === 'login') throw new Error('not logged in');
    return base(command, args);
  };
  const runtime = await f.detect();
  assert.equal(runtime.protocolCompatible, true);
  assert.equal(runtime.loggedIn, false);
  assert.equal(runtime.available, false);
  assert.match(runtime.note, /登录检查未通过/);
});

test('TraeX: falls back to a compatible alias, but never overrides an explicit binary', async (t) => {
  const f = traexFixture(t, ['traex', 'traecli']);
  f.options.probe = async (bin) => ({ compatible: bin.endsWith('traecli') });
  assert.equal((await f.detect()).bin, path.join(f.bin, 'traecli'));
  f.options.env.TRAECLI_BIN = path.join(f.bin, 'traex');
  assert.equal((await f.detect()).available, false);
  f.options.env.TRAECLI_BIN = path.join(f.bin, 'missing');
  const missing = await f.detect();
  assert.equal(missing.installed, false);
  assert.match(missing.note, /找不到可执行文件/);
  f.options.env.TRAEX_BIN = path.join(f.bin, 'traecli');
  assert.equal((await f.detect()).available, true, 'legacy explicit override has the same priority as daemon config');
});

test('TraeX: resolves and deduplicates aliases before probing or pinning a binary', async (t) => {
  const f = traexFixture(t, ['actual']);
  for (const name of ['traex', 'traecli']) fs.symlinkSync(path.join(f.bin, 'actual'), path.join(f.bin, name));
  f.options.probe = async (bin) => { f.probed.push(bin); return { compatible: false }; };
  const runtime = await f.detect();
  assert.deepEqual(f.probed, [fs.realpathSync(path.join(f.bin, 'actual'))]);
  assert.equal(runtime.bin, fs.realpathSync(path.join(f.bin, 'actual')));
});

test('OpenCode: an installed supported 1.x CLI is offered after an ACP handshake', async (t) => {
  const bin = tempDir(t, 'vika-opencode-');
  fs.writeFileSync(path.join(bin, 'opencode'), '#!/bin/sh\n', { mode: 0o755 });
  const options = {
    env: { PATH: bin }, piBin: '/managed/pi.js',
    acpProbe: async () => ({ compatible: true }),
    execFileImpl: async (command, args) => {
      assert.deepEqual(args, ['--version']);
      return { stdout: 'opencode version 1.19.5\n' };
    },
  };
  const runtime = (await detectRuntimes(options)).find(({ id }) => id === 'opencode');
  assert.equal(runtime.installed, true);
  assert.equal(runtime.available, true);
  assert.equal(runtime.bin, path.join(bin, 'opencode'));
  assert.equal(runtime.binEnv, 'OPENCODE_BIN');
  assert.match(runtime.note, /1\.19\.5/);
});

function agyFixture(t, name = 'agy') {
  const bin = tempDir(t, 'vika-agy-');
  fs.writeFileSync(path.join(bin, name), '#!/bin/sh\n', { mode: 0o755 });
  return bin;
}

test('Antigravity: an installed, compatible and logged-in CLI is offered', async (t) => {
  const bin = agyFixture(t);
  const probes = [];
  const seen = [];
  const runtime = (await detectRuntimes({
    env: { PATH: bin }, piBin: '/managed/pi.js',
    agyProbe: async (command) => { probes.push(command); return { compatible: true }; },
    execFileImpl: async (command, args) => {
      if (command.endsWith('agy')) seen.push(args);
      return { stdout: args[0] === '--version' ? '1.5.3\n' : '' };
    },
  })).find(({ id }) => id === 'agy');
  assert.equal(runtime.installed, true);
  assert.equal(runtime.available, true);
  assert.equal(runtime.bin, path.join(bin, 'agy'));
  assert.equal(runtime.binEnv, 'AGY_BIN');
  assert.equal(runtime.label, 'Antigravity');
  assert.match(runtime.note, /1\.5\.3/);
  assert.deepEqual(probes, [path.join(bin, 'agy')]);
  assert.deepEqual(seen, [['--version'], ['models']]);
});

test('Antigravity: not logged in is reported and not offered', async (t) => {
  const bin = agyFixture(t);
  const runtime = (await detectRuntimes({
    env: { PATH: bin }, piBin: '/managed/pi.js',
    agyProbe: async () => ({ compatible: true }),
    execFileImpl: async (command, args) => {
      if (command.endsWith('agy') && args[0] === 'models') throw Object.assign(new Error('exit 1'), { stdout: '', stderr: 'Error: Please sign in to view available models.\n' });
      return { stdout: '1.5.3\n' };
    },
  })).find(({ id }) => id === 'agy');
  assert.equal(runtime.installed, true);
  assert.equal(runtime.available, false);
  assert.match(runtime.note, /agy/);
  assert.match(runtime.note, /(?:未登录|not logged in)/);
});

test('Antigravity: a model listing that fails for another reason is not reported as a login problem', async (t) => {
  const bin = agyFixture(t);
  const runtime = (await detectRuntimes({
    env: { PATH: bin }, piBin: '/managed/pi.js',
    agyProbe: async () => ({ compatible: true }),
    execFileImpl: async (command, args) => {
      if (command.endsWith('agy') && args[0] === 'models') throw Object.assign(new Error('exit 1'), { stdout: '', stderr: 'starting\nError: timed out waiting for available models\n' });
      return { stdout: '1.5.3\n' };
    },
  })).find(({ id }) => id === 'agy');
  assert.equal(runtime.available, false);
  assert.doesNotMatch(runtime.note, /未登录|not logged in/);
  assert.match(runtime.note, /timed out waiting for available models/);
});

test('Antigravity: the login check runs agy models with OpenViking filtered out', async (t) => {
  const bin = agyFixture(t);
  const home = tempDir(t, 'vika-agy-home-');
  const gemini = path.join(home, '.gemini');
  fs.mkdirSync(path.join(gemini, 'config'), { recursive: true });
  fs.mkdirSync(path.join(gemini, 'antigravity-cli'), { recursive: true });
  fs.writeFileSync(path.join(gemini, 'antigravity-cli', 'antigravity-oauth-token'), 'token');
  const owner = JSON.stringify({ mcpServers: { openviking: { command: 'ov' }, control: { command: 'control' } } });
  fs.writeFileSync(path.join(gemini, 'config', 'mcp_config.json'), owner);
  let seen = null;
  const runtime = (await detectRuntimes({
    env: { PATH: bin, HOME: home }, piBin: '/managed/pi.js',
    agyProbe: async () => ({ compatible: true }),
    execFileImpl: async (command, args, options) => {
      if (command.endsWith('agy') && args[0] === 'models') {
        const dir = path.join(options.env.HOME, '.gemini');
        seen = {
          home: options.env.HOME,
          servers: Object.keys(JSON.parse(fs.readFileSync(path.join(dir, 'config', 'mcp_config.json'), 'utf8')).mcpServers),
          token: fs.readFileSync(path.join(dir, 'antigravity-cli', 'antigravity-oauth-token'), 'utf8'),
        };
      }
      return { stdout: args[0] === '--version' ? '1.5.3\n' : '' };
    },
  })).find(({ id }) => id === 'agy');
  assert.equal(runtime.available, true);
  assert.notEqual(seen.home, home);
  assert.deepEqual(seen.servers, ['control']);
  assert.equal(seen.token, 'token');
  assert.equal(fs.existsSync(seen.home), false);
  assert.equal(fs.readFileSync(path.join(gemini, 'config', 'mcp_config.json'), 'utf8'), owner);
});

test('Antigravity: another ACP agent behind agy is reported and not offered', async (t) => {
  const bin = agyFixture(t);
  const runtime = (await detectRuntimes({
    env: { PATH: bin }, piBin: '/managed/pi.js',
    agyProbe: async () => ({ compatible: false, reason: 'ACP unrecognised ACP agent' }),
    execFileImpl: async (command, args) => {
      if (args[0] === 'models') assert.fail('login is checked only after the handshake');
      return { stdout: '1.5.3\n' };
    },
  })).find(({ id }) => id === 'agy');
  assert.equal(runtime.available, false);
  assert.match(runtime.note, /unrecognised ACP agent/);
});

test('Antigravity: AGY_BIN is authoritative, and a bare name is a PATH lookup', async (t) => {
  const bin = agyFixture(t, 'agy-custom');
  const located = (await detectRuntimes({
    env: { PATH: bin, AGY_BIN: 'agy-custom' }, piBin: '/managed/pi.js',
    agyProbe: async () => ({ compatible: true }),
    execFileImpl: async () => ({ stdout: '1.5.3\n' }),
  })).find(({ id }) => id === 'agy');
  assert.equal(located.bin, path.join(bin, 'agy-custom'));
  assert.equal(located.available, true);
  const missing = (await detectRuntimes({
    env: { PATH: bin, AGY_BIN: 'agy-absent' }, piBin: '/managed/pi.js',
    execFileImpl: async () => ({ stdout: '' }),
  })).find(({ id }) => id === 'agy');
  assert.equal(missing.bin, null);
  assert.equal(missing.available, false);
  assert.match(missing.note, /agy-absent/);
});

test('OpenCode: an unsupported 2.x CLI is reported and not offered', async (t) => {
  const bin = tempDir(t, 'vika-opencode2-');
  fs.writeFileSync(path.join(bin, 'opencode'), '#!/bin/sh\n', { mode: 0o755 });
  const acpProbes = [];
  const runtime = (await detectRuntimes({
    env: { PATH: bin }, piBin: '/managed/pi.js',
    acpProbe: async (command) => { acpProbes.push(command); return { compatible: true }; },
    execFileImpl: async () => ({ stdout: 'opencode v2.2.5\n' }),
  })).find(({ id }) => id === 'opencode');
  assert.equal(runtime.installed, true);
  assert.equal(runtime.available, false);
  assert.match(runtime.note, /OpenCode 1\.x/);
  assert.deepEqual(acpProbes, [], 'version gate runs before the ACP handshake');
});

// An OV with two accounts whose administrators share a name. Deleting a user
// answers 202 and runs in the background, as a task.
function adminServer() {
  const calls = [];
  const tasks = new Map();
  const fetchImpl = async (url, init) => {
    const { pathname } = new URL(url);
    calls.push({ method: init.method ?? 'GET', path: pathname, key: init.headers['X-API-Key'] });
    const ok = (result, status = 200) => Response.json({ status: 'ok', result }, { status });
    const deleted = pathname.match(/^\/api\/v1\/admin\/accounts\/([^/]+)\/users\/(.+)$/);
    if (init.method === 'DELETE' && deleted) {
      const user = decodeURIComponent(deleted[2]);
      if (user.endsWith('.gone')) return Response.json({ status: 'error' }, { status: 404 });
      const outcome = user.endsWith('.broken') ? ['running', 'failed'] : user.endsWith('.slow') ? ['running'] : ['running', 'completed'];
      tasks.set(`task-${user}`, outcome);
      return ok({ status: 'deleting', task_id: `task-${user}` }, 202);
    }
    const task = pathname.match(/^\/api\/v1\/tasks\/(.+)$/);
    if (task) {
      const states = tasks.get(decodeURIComponent(task[1]));
      return ok({ status: states.length > 1 ? states.shift() : states[0] });
    }
    const account = pathname.split('/')[5];
    const { user_id: user } = JSON.parse(init.body);
    return ok({ user_id: user, user_key: `${account}-key-of-${user}` });
  };
  return { calls, fetchImpl };
}

const adminOf = (account) => ({ url: 'https://ov.example', usable: true, authMode: 'api_key', role: 'admin', account, user: 'boss',
  identity: { mode: 'register' }, config: { url: 'https://ov.example', api_key: `${account}-admin-key` } });

test('OpenViking: a user created during setup is reused on the way back, in its own account only', async () => {
  const { calls, fetchImpl } = adminServer();
  const registrations = new OVRegistrations();
  const first = await instanceOvConfig({ detected: adminOf('team-a'), instanceId: 'asd', fetchImpl, registrations });
  // Going back and preparing again: the same user and key, no second registration (OV would answer 409).
  assert.deepEqual(await instanceOvConfig({ detected: adminOf('team-a'), instanceId: 'asd', fetchImpl, registrations }), first);
  // Another account on the same server, whose administrator has the same name: its own user and key.
  const other = await instanceOvConfig({ detected: adminOf('team-b'), instanceId: 'asd', fetchImpl, registrations });
  assert.equal(other.api_key, 'team-b-key-of-boss.asd');
  assert.equal(first.api_key, 'team-a-key-of-boss.asd');
  assert.deepEqual(calls.filter(({ method }) => method === 'POST').map(({ path, key }) => [path, key]), [
    ['/api/v1/admin/accounts/team-a/users', 'team-a-admin-key'],
    ['/api/v1/admin/accounts/team-b/users', 'team-b-admin-key'],
  ]);
});

test('OpenViking: setup deletes the users it created and did not keep, and waits for OV to finish', async () => {
  const { calls, fetchImpl } = adminServer();
  const registrations = new OVRegistrations({ deleteWaitMs: 50, pollMs: 5 });
  const make = (instanceId) => instanceOvConfig({ detected: adminOf('team'), instanceId, fetchImpl, registrations });
  const kept = await make('alpha');
  for (const name of ['asd', 'gone', 'broken', 'slow']) await make(name);
  const saved = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'vika-ov-')), 'alpha.json');
  writeInstanceOvConfig({ config: kept, target: saved });
  registrations.keep(saved);
  const { failed, pending } = await registrations.discard({ fetchImpl });
  const deletes = calls.filter(({ method }) => method === 'DELETE');
  assert.deepEqual(deletes.map(({ path }) => path.split('/').pop()), ['boss.asd', 'boss.gone', 'boss.broken', 'boss.slow']);
  assert.ok(deletes.every(({ key }) => key === 'team-admin-key'));
  assert.ok(calls.some(({ path }) => path === '/api/v1/tasks/task-boss.asd'), 'the deletion task is followed to its end');
  assert.deepEqual(failed, ['boss.broken'], 'one already gone is fine');
  assert.deepEqual(pending, ['boss.slow'], 'one OV is still deleting is named, not taken for done');
  assert.deepEqual(await registrations.discard({ fetchImpl }), { failed: [], pending: [] }, 'nothing is deleted twice');
});
