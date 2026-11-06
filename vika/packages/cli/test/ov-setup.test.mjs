// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { configureOpenViking, discoverOpenViking, prepareInstanceOpenViking, saveOVConnection, takenIdentities } from '../src/ov-setup.mjs';
import { detectOpenViking, HOSTED_OV_URL, LOCAL_OV_URL, savedOVConfigPath } from '../src/ov.mjs';
import { Prompter } from '../src/prompt.mjs';
import { setLanguage } from '../src/i18n.mjs';

function fixture(t, answers) {
  setLanguage('en');
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-ov-form-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const input = new PassThrough();
  input.end(answers.join('\n') + '\n');
  const output = new PassThrough();
  let text = '';
  output.on('data', (chunk) => { text += chunk; });
  const prompter = new Prompter({ input, output });
  t.after(() => prompter.close());
  return { home, options: { prompter, env: { HOME: home }, homeDir: home }, text: () => text };
}

const remote = 'https://ov.example:9443';
function fetchOV({ local = false, key = 'test-secret', calls = [] } = {}) {
  return async (url, init) => {
    calls.push({ url, headers: init.headers });
    if (url.startsWith(LOCAL_OV_URL)) {
      if (!local) throw new Error('offline');
      return Response.json({ status: 'ok', auth_mode: 'dev', result: [] });
    }
    assert.ok(url.startsWith(remote));
    if (url.endsWith('/health')) return Response.json({ status: 'ok', auth_mode: 'api_key' });
    return init.headers['X-API-Key'] === key
      ? Response.json({ status: 'ok', result: [] })
      : Response.json({ status: 'error', message: 'sensitive server details' }, { status: 401 });
  };
}

test('manual remote URL, custom port and hidden credentials are available when nothing is detected', async (t) => {
  const h = fixture(t, ['3', remote, '1', 'test-secret', 'team', 'owner', 'worker']);
  const calls = [];
  const result = await configureOpenViking({ ...h.options, fetchImpl: fetchOV({ calls }) });
  assert.deepEqual(result.config, { url: remote, api_key: 'test-secret', account: 'team', user: 'owner', agent: 'worker' });
  assert.equal(result.usable, true);
  assert.ok(calls.some(({ url, headers }) => url === `${remote}/api/v1/system/status`
    && headers['X-API-Key'] === 'test-secret' && headers['X-OpenViking-Account'] === 'team'));
  assert.match(h.text(), /Enter or edit the address and credentials/);
  assert.match(h.text(), /Choose an OV configuration file/);
  assert.doesNotMatch(h.text(), /test-secret|sensitive server details/);
  assert.deepEqual(fs.readdirSync(h.home), [], 'selecting a connection does not save it');
});

// The hosted service checks the key even on /health and reports the key's user.
function fetchHosted({ key = 'hosted-key', user = 'test-user', calls = [] } = {}) {
  return async (url, init) => {
    calls.push({ url, headers: init.headers });
    if (url.startsWith(LOCAL_OV_URL)) throw new Error('offline');
    assert.ok(url.startsWith(HOSTED_OV_URL));
    if (init.headers['X-API-Key'] !== key) return Response.json({ status: 'error', message: 'sensitive' }, { status: 401 });
    if (url.endsWith('/health')) return Response.json({ status: 'ok', auth_mode: 'api_key', user_id: user });
    return Response.json({ status: 'ok', result: { initialized: true, user } });
  };
}

test('the hosted service needs only an API key, and the instance keeps the user that key logs in as', async (t) => {
  const h = fixture(t, ['2', 'hosted-key']);
  const calls = [];
  const result = await configureOpenViking({ ...h.options, fetchImpl: fetchHosted({ calls }) });
  assert.deepEqual(result.config, { url: HOSTED_OV_URL, api_key: 'hosted-key', user: 'test-user' });
  assert.equal(result.user, 'test-user');
  assert.ok(calls.some(({ url }) => url === `${HOSTED_OV_URL}/api/v1/system/status`));
  assert.match(h.text(), /OpenViking Service on Volcengine\s+API key only/);
  assert.doesNotMatch(h.text(), /Account \(optional|hosted-key/);
});

test('a rejected hosted key says so and can be replaced', async (t) => {
  const h = fixture(t, ['2', 'wrong-key', '2', '2', 'hosted-key']);
  const result = await configureOpenViking({ ...h.options, fetchImpl: fetchHosted() });
  assert.equal(result.config.api_key, 'hosted-key');
  assert.match(h.text(), /OpenViking rejected the API key \(HTTP 401\)/);
  assert.match(h.text(), /Keep the configured key/);
  assert.doesNotMatch(h.text(), /wrong-key|hosted-key|sensitive/);
});

test('a manual connection to another server does not inherit the detected Account and User', async (t) => {
  const answers = ['3', remote, '1', 'test-secret', '', '', ''];
  for (const [form, expected] of [
    [answers, { url: remote, api_key: 'test-secret' }],
    // The same server keeps them, as defaults to confirm.
    [['3', '', '2', '', '', ''], { url: LOCAL_OV_URL, account: 'team', user: 'owner' }],
  ]) {
    const h = fixture(t, form);
    fs.mkdirSync(path.join(h.home, '.openviking'));
    fs.writeFileSync(path.join(h.home, '.openviking', 'ovcli.conf'), JSON.stringify({ url: LOCAL_OV_URL, account: 'team', user: 'owner' }));
    const local = fetchOV();
    const result = await configureOpenViking({ ...h.options, fetchImpl: async (url, init) => (url.startsWith(LOCAL_OV_URL)
      ? Response.json({ status: 'ok', auth_mode: 'trusted', result: [] })
      : local(url, init)) });
    assert.deepEqual(result.config, expected);
  }
});

test('an API key server replaces a stale configured identity with the one the key logs in as', async (t) => {
  const h = fixture(t, ['4', '~/remote.json']);
  fs.writeFileSync(path.join(h.home, 'remote.json'), JSON.stringify({ url: remote, api_key: 'test-secret', account: 'team', user: 'stale' }));
  const result = await configureOpenViking({ ...h.options, fetchImpl: async (url, init) => {
    if (url.startsWith(LOCAL_OV_URL)) throw new Error('offline');
    if (url.endsWith('/health')) return Response.json({ status: 'ok', auth_mode: 'api_key' });
    assert.equal(init.headers['X-API-Key'], 'test-secret');
    return Response.json({ status: 'ok', result: { initialized: true, user: 'amy' } });
  } });
  assert.deepEqual(result.config, { url: remote, api_key: 'test-secret', user: 'amy' });
  // Without a reported user nothing is guessed: the configured one is not shown as the key's.
  const unknown = await detectOpenViking({ env: h.options.env, homeDir: h.home, configPath: path.join(h.home, 'remote.json'),
    fetchImpl: async (url) => (url.endsWith('/health') ? Response.json({ status: 'ok', auth_mode: 'api_key' }) : Response.json({ status: 'ok', result: [] })) });
  assert.equal(unknown.user, null);
});

test('a healthy discovery still offers manual entry and accepts a different connection', async (t) => {
  const h = fixture(t, ['3', remote, '1', 'test-secret', '', '', '']);
  const result = await configureOpenViking({ ...h.options, fetchImpl: fetchOV({ local: true }) });
  assert.equal(result.url, remote);
  // A dev server takes the user from the request: the instance would be a user of its own.
  assert.match(h.text(), /Use the local default address \(no credentials\)\s+http:\/\/127\.0\.0\.1:1933 · user=<instance name> · ready/);
  assert.match(h.text(), /Enter or edit the address and credentials/);
});

test('discovery lists each config source once, with addresses and failed checks', async (t) => {
  const h = fixture(t, []);
  const preferred = path.join(h.home, 'instance.json');
  fs.writeFileSync(preferred, JSON.stringify({ url: remote, api_key: 'test-secret' }));
  fs.mkdirSync(path.join(h.home, '.openviking'));
  fs.writeFileSync(path.join(h.home, '.openviking', 'ovcli.conf'), JSON.stringify({ url: remote, api_key: 'wrong' }));
  const entries = await discoverOpenViking({ ...h.options, preferredConfigPath: preferred,
    env: { HOME: h.home, OPENVIKING_CLI_CONFIG_FILE: preferred },
    fetchImpl: fetchOV() });
  assert.equal(entries.length, 3);
  assert.deepEqual(entries.map(({ usable }) => usable), [true, false, false]);
  assert.equal(entries[0].url, remote);
});

test('a shared config the instance already copied is not offered again; one that would change something is', async (t) => {
  const h = fixture(t, []);
  const shared = { url: remote, api_key: 'test-secret', account: 'team', user: 'owner', plugin: { recall: false } };
  const current = path.join(h.home, 'asd.json');
  fs.writeFileSync(current, JSON.stringify({ ...shared, url: `${remote}/`, actor_peer_id: 'asd' }));
  fs.mkdirSync(path.join(h.home, '.openviking'));
  const cli = path.join(h.home, '.openviking', 'ovcli.conf');
  fs.writeFileSync(cli, JSON.stringify(shared));
  const labels = async () => (await discoverOpenViking({ ...h.options, preferredConfigPath: current, fetchImpl: fetchOV() }))
    .map(({ label }) => label);
  assert.deepEqual(await labels(), ['Keep the current connection', 'Use the local default address (no credentials)']);
  fs.writeFileSync(cli, JSON.stringify({ ...shared, plugin: { recall: true } }));
  assert.deepEqual(await labels(), ['Keep the current connection', 'Use the OpenViking CLI’s config', 'Use the local default address (no credentials)']);
});

test('the menu says where each connection goes, who it logs in as, or why it cannot be used', async (t) => {
  const h = fixture(t, ['1']);
  fs.mkdirSync(path.join(h.home, '.openviking'));
  fs.writeFileSync(path.join(h.home, '.openviking', 'ovcli.conf'), JSON.stringify({ url: remote, api_key: 'wrong' }));
  fs.mkdirSync(path.dirname(savedOVConfigPath(h.home)), { recursive: true });
  fs.writeFileSync(savedOVConfigPath(h.home), JSON.stringify({ url: remote, api_key: 'test-secret' }));
  const result = await configureOpenViking({ ...h.options, fetchImpl: async (url, init) => {
    if (url.startsWith(LOCAL_OV_URL)) throw new Error('offline');
    if (url.endsWith('/health')) return Response.json({ status: 'ok', auth_mode: 'api_key' });
    return init.headers['X-API-Key'] === 'test-secret'
      ? Response.json({ status: 'ok', result: { initialized: true, user: 'amy' } })
      : Response.json({ status: 'error' }, { status: 401 });
  } });
  assert.equal(result.user, 'amy');
  const menu = h.text();
  assert.match(menu, /1\) Use the connection entered in setup last time\s+https:\/\/ov\.example:9443 · user=amy · ready/);
  assert.match(menu, /2\) Use the OpenViking CLI’s config\s+https:\/\/ov\.example:9443 · OpenViking is running, but authentication .*HTTP 401/);
  assert.match(menu, /3\) Use the local default address \(no credentials\)\s+http:\/\/127\.0\.0\.1:1933 · OpenViking is unavailable/);
  assert.doesNotMatch(menu, /ovcli\.conf|ov-connection\.json|test-secret|wrong/, 'paths and keys stay off the menu');
});

test('a configured local connection is checked with its identity without an anonymous duplicate', async (t) => {
  const h = fixture(t, ['1']);
  fs.mkdirSync(path.join(h.home, '.openviking'));
  const config = { url: `${LOCAL_OV_URL}/`, account: 'team', user: 'owner' };
  fs.writeFileSync(path.join(h.home, '.openviking', 'ovcli.conf'), JSON.stringify(config));
  const calls = [];
  const result = await configureOpenViking({ ...h.options, fetchImpl: async (url, init) => {
    calls.push(url);
    assert.equal(init.headers['X-OpenViking-Account'], 'team');
    assert.equal(init.headers['X-OpenViking-User'], 'owner');
    return Response.json({ status: 'ok', auth_mode: 'trusted', result: [] });
  } });
  assert.equal(result.usable, true);
  assert.equal(calls.length, 4, 'discovery and the selected config each check health and identity');
  // The menu shows who the instance would be, not the connection's own user.
  assert.match(h.text(), /Use the OpenViking CLI’s config\s+http:\/\/127\.0\.0\.1:1933 · user=owner\.<instance name> · ready/);
  assert.doesNotMatch(h.text(), /local default address|HTTP 400/);
  assert.match(h.text(), /Enter or edit the address and credentials/);
});

test('an unconfigured Trusted server is shown as running and can be completed manually', async (t) => {
  const h = fixture(t, ['3', '', '2', 'team', 'owner', '']);
  const result = await configureOpenViking({ ...h.options, fetchImpl: async (url, init) => {
    if (url.endsWith('/health')) return Response.json({ status: 'ok', auth_mode: 'trusted' });
    return init.headers['X-OpenViking-Account'] && init.headers['X-OpenViking-User']
      ? Response.json({ status: 'ok', result: [] })
      : Response.json({ status: 'error', message: 'private server details' }, { status: 400 });
  } });
  assert.equal(result.usable, true);
  assert.deepEqual(result.config, { url: LOCAL_OV_URL, account: 'team', user: 'owner' });
  assert.match(h.text(), /OpenViking is running; Trusted mode requires Account and User/);
  assert.doesNotMatch(h.text(), /unavailable|HTTP 400|private server details/);
});

test('failed credentials can be edited and retried without leaking keys or server bodies', async (t) => {
  const h = fixture(t, ['3', remote, '1', 'wrong-secret', '', '', '',
    '3', '', '2', 'test-secret', '', '', '']);
  const result = await configureOpenViking({ ...h.options, fetchImpl: fetchOV() });
  assert.equal(result.usable, true);
  assert.match(h.text(), /HTTP 401/);
  assert.doesNotMatch(h.text(), /wrong-secret|test-secret|sensitive server details/);
});

test('a configuration file can be imported after an invalid path; cancellation saves nothing', async (t) => {
  const h = fixture(t, ['4', '~/missing.json', '4', '~/remote.json']);
  fs.writeFileSync(path.join(h.home, 'remote.json'), JSON.stringify({ url: remote, api_key: 'test-secret', plugin: { memory: true } }));
  const result = await configureOpenViking({ ...h.options, fetchImpl: fetchOV() });
  assert.equal(result.configPath, path.join(h.home, 'remote.json'));
  assert.deepEqual(result.config.plugin, { memory: true });
  assert.match(h.text(), /Cannot read the OV configuration/);
  const cancel = fixture(t, ['6']);
  await assert.rejects(configureOpenViking({ ...cancel.options, fetchImpl: fetchOV() }), { name: 'PromptAborted' });
  assert.deepEqual(fs.readdirSync(cancel.home), []);
});

// An API key server that knows who each key is.
function fetchKeys(keys) {
  return async (url, init) => {
    if (url.startsWith(LOCAL_OV_URL)) throw new Error('offline');
    const who = keys[init.headers['X-API-Key']];
    if (!who) return Response.json({ status: 'error' }, { status: 401 });
    if (url.endsWith('/health')) return Response.json({ status: 'ok', auth_mode: 'api_key', account_id: 'team', user_id: who.user, role: who.role });
    return Response.json({ status: 'ok', result: { initialized: true, user: who.user } });
  };
}

test('an API key another instance logs in with is shared only when the owner says so', async (t) => {
  //                    manual URL     key  typed      acct user agent  back  manual URL keep acct user agent share
  const h = fixture(t, ['3', remote, '1', 'amy-key', '', '', '', '2', '3', '', '1', '', '', '', '1']);
  const other = path.join(h.home, 'asd.json');
  fs.writeFileSync(other, JSON.stringify({ url: `${remote}/`, api_key: 'amy-key', user: 'amy' }));
  const taken = takenIdentities({ agents: [{ id: 'asd', ovConfig: other }, { id: 'fresh', ovConfig: null }], exclude: 'fresh', homeDir: h.home });
  const result = await configureOpenViking({ ...h.options, taken, instanceId: 'fresh',
    fetchImpl: fetchKeys({ 'amy-key': { user: 'amy', role: 'user' } }) });
  // The first answer went back to the menu; the second shared.
  assert.equal(h.text().split('This instance’s identity in OpenViking').length, 3);
  assert.match(h.text(), /Share amy with instance asd\s+their memories are not kept apart/);
  assert.match(h.text(), /Choose another connection or key/);
  assert.deepEqual(result.identity, { mode: 'shared' });
  assert.equal(result.config.user, 'amy');
  assert.doesNotMatch(h.text(), /Create a user/, 'only an administrator can create users');
});

test('a key no one else uses is the instance identity without a question; a Trusted server derives one', async (t) => {
  const h = fixture(t, ['3', remote, '1', 'bob-key', '', '', '']);
  const result = await configureOpenViking({ ...h.options, taken: [], instanceId: 'fresh',
    fetchImpl: fetchKeys({ 'bob-key': { user: 'bob', role: 'user' } }) });
  assert.deepEqual(result.identity, { mode: 'key' });
  assert.doesNotMatch(h.text(), /identity in OpenViking/);

  const trusted = fixture(t, ['3', '', '2', 'team', 'owner', '']);
  const derived = await configureOpenViking({ ...trusted.options, taken: [{ label: 'instance asd', url: LOCAL_OV_URL, user: 'owner' }],
    fetchImpl: async () => Response.json({ status: 'ok', auth_mode: 'trusted', result: [] }) });
  assert.deepEqual(derived.identity, { mode: 'instance' });
  assert.doesNotMatch(trusted.text(), /identity in OpenViking/);
});

test('an administrator key can create the instance a user of its own, and is then not saved for the next instance', async (t) => {
  const h = fixture(t, ['3', remote, '1', 'admin-key', '', '', '', '1']);
  const result = await configureOpenViking({ ...h.options, taken: [], instanceId: 'fresh',
    fetchImpl: fetchKeys({ 'admin-key': { user: 'boss', role: 'admin' } }) });
  assert.match(h.text(), /Create a user for this instance\s+boss\.fresh, created with this administrator key, which is not kept/);
  assert.match(h.text(), /Run as boss\s+an administrator/);
  assert.deepEqual(result.identity, { mode: 'register' });
  assert.equal(result.account, 'team');
  assert.equal(saveOVConnection(result, h.options), null);
  assert.equal(fs.existsSync(savedOVConfigPath(h.home)), false);
  assert.doesNotMatch(h.text(), /admin-key/);
});

test('choosing a new user on the current admin connection writes a new config and preserves the old file until save', async (t) => {
  const h = fixture(t, ['1', '1']);
  const current = path.join(h.home, 'current.json');
  const original = JSON.stringify({ url: remote, api_key: 'admin-key', user: 'boss' });
  fs.writeFileSync(current, original);
  const registrations = [];
  const keys = fetchKeys({ 'admin-key': { user: 'boss', role: 'admin' }, 'new-key': { user: 'boss.fresh', role: 'user' } });
  const fetchImpl = async (url, init) => {
    if (url.endsWith('/users')) {
      registrations.push({ url, body: JSON.parse(init.body), key: init.headers['X-API-Key'] });
      return Response.json({ status: 'ok', result: { user_key: 'new-key' } });
    }
    return keys(url, init);
  };
  const detected = await configureOpenViking({ ...h.options, preferredConfigPath: current, instanceId: 'fresh', fetchImpl });
  assert.equal(detected.configPath, current);
  assert.equal(detected.identity.mode, 'register');
  const pending = new Set();
  const prepared = await prepareInstanceOpenViking({ ...h.options, agent: { id: 'fresh', ovConfig: current }, detected, pending, fetchImpl });
  assert.notEqual(prepared, current);
  assert.deepEqual(registrations, [{ url: `${remote}/api/v1/admin/accounts/team/users`,
    body: { user_id: 'boss.fresh', role: 'user' }, key: 'admin-key' }]);
  assert.deepEqual(JSON.parse(fs.readFileSync(prepared, 'utf8')), { url: remote, api_key: 'new-key', user: 'boss.fresh' });
  assert.equal(fs.statSync(prepared).mode & 0o777, 0o600);
  assert.deepEqual([...pending], [prepared]);
  assert.equal(fs.readFileSync(current, 'utf8'), original);
  // Keeping the existing key still reuses the connection without registration.
  assert.equal(await prepareInstanceOpenViking({ ...h.options, agent: { id: 'fresh', ovConfig: current },
    detected: { ...detected, identity: { mode: 'key' } }, pending, fetchImpl }), current);
  assert.equal(registrations.length, 1);
});

test('the menu shows who a new instance would be: its own Trusted user, or a key and who else uses it', async (t) => {
  //                   saved, CLI, hosted, manual, file, detect again, exit
  const h = fixture(t, ['7']);
  fs.mkdirSync(path.join(h.home, '.openviking'));
  fs.writeFileSync(path.join(h.home, '.openviking', 'ovcli.conf'), JSON.stringify({ url: LOCAL_OV_URL, account: 'team', user: 'weihan.liu' }));
  fs.mkdirSync(path.dirname(savedOVConfigPath(h.home)), { recursive: true });
  fs.writeFileSync(savedOVConfigPath(h.home), JSON.stringify({ url: remote, api_key: 'nv-key', user: 'Vika-Nvidia' }));
  const nvidia = path.join(h.home, 'vika-nvidia.json');
  fs.writeFileSync(nvidia, JSON.stringify({ url: remote, api_key: 'nv-key', user: 'Vika-Nvidia' }));
  const taken = takenIdentities({ agents: [{ id: 'vika-nvidia', ovConfig: nvidia }], homeDir: h.home });
  const keys = fetchKeys({ 'nv-key': { user: 'Vika-Nvidia', role: 'user' } });
  await assert.rejects(configureOpenViking({ ...h.options, taken, instanceId: 'agent3', fetchImpl: async (url, init) => (url.startsWith(LOCAL_OV_URL)
    ? Response.json({ status: 'ok', auth_mode: 'trusted', result: { initialized: true } })
    : keys(url, init)) }), { name: 'PromptAborted' });
  assert.match(h.text(), /Use the connection entered in setup last time\s+https:\/\/ov\.example:9443 · user=Vika-Nvidia · also used by instance vika-nvidia · ready/);
  assert.match(h.text(), /Use the OpenViking CLI’s config\s+http:\/\/127\.0\.0\.1:1933 · user=weihan\.liu\.agent3 · ready/);
  assert.doesNotMatch(h.text(), /user=weihan\.liu ·/);
});

test('a typed connection is kept privately for the next instance; a file new instances find anyway is not copied', async (t) => {
  const h = fixture(t, []);
  const saved = saveOVConnection({ usable: true, configPath: null, config: {
    url: remote, api_key: 'test-secret', account: 'team', user: 'owner', actor_peer_id: 'primary', agent_id: 'old',
  } }, h.options);
  assert.equal(saved, savedOVConfigPath(h.home));
  assert.equal(fs.statSync(saved).mode & 0o777, 0o600);
  assert.deepEqual(JSON.parse(fs.readFileSync(saved, 'utf8')), { url: remote, api_key: 'test-secret', account: 'team', user: 'owner' });
  const detected = await detectOpenViking({ env: h.options.env, homeDir: h.home, fetchImpl: fetchOV() });
  assert.equal(detected.configPath, saved);
  assert.equal(detected.usable, true);

  const cli = path.join(h.home, '.openviking', 'ovcli.conf');
  fs.mkdirSync(path.dirname(cli));
  fs.writeFileSync(cli, JSON.stringify({ url: remote, api_key: 'rotated' }));
  const before = fs.readFileSync(saved, 'utf8');
  assert.equal(saveOVConnection({ usable: true, configPath: cli }, h.options), null);
  assert.equal(fs.readFileSync(saved, 'utf8'), before, 'a copy of ovcli.conf would go stale when its key rotates');
});
