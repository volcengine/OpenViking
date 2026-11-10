// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
// The DSH helper module: version gate, generated patch, private environment, bridge constants.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {
  DSH_BRIDGE_CLI_METHODS, DSH_BRIDGE_DAEMON_METHODS, DSH_BRIDGE_PATH, DSH_DEFAULT_MODEL,
  DSH_EFFORTS, DSH_NOT_READY_CODE, assertDshNodeVersion, assertSupportedDshVersion,
  bridgeHelloSupports, isDshNotReady, retryDshNotReady,
  buildDshPatch, compareDshVersions, dshCommand, dshProfilePatchHash, dshProfilePatchPath,
  escapeDshTemplate, parseDshModel, parseDshVersion, prepareDshEnvironment, sanitizeDshEnv,
  writeDshPatch,
} from '../src/dsh-runtime.mjs';

function temp(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-dsh-runtime-'));
  fs.chmodSync(directory, 0o700);
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return directory;
}

test('DSH semver accepts prereleases and rejects out-of-range or malformed releases', () => {
  assert.equal(assertSupportedDshVersion(' 0.2.2-rc.1\n'), '0.2.2-rc.1');
  assert.equal(assertSupportedDshVersion('0.2.99+private'), '0.2.99+private');
  assert.ok(compareDshVersions('0.2.2-rc.1', '0.2.2-alpha.11') > 0);
  assert.ok(compareDshVersions('0.2.2-rc.1', '0.2.2') < 0);
  assert.ok(compareDshVersions('0.2.2-rc.10', '0.2.2-rc.2') > 0);
  assert.deepEqual(parseDshVersion('0.2.2-alpha.1').prerelease, ['alpha', 1]);
  for (const value of ['', 'v0.2.2', '0.2', '0.3.0', '0.2.2-01', '1.0.0', '0.2.2-rc..1']) {
    assert.throws(() => assertSupportedDshVersion(value));
  }
});

test('DSH model parser preserves explicit provider routes', () => {
  assert.deepEqual(parseDshModel('deepseek-flash'), { provider: 'deepseek-official', model: 'deepseek-flash' });
  assert.deepEqual(parseDshModel('deepseek/deepseek-chat'), { provider: 'deepseek', model: 'deepseek-chat' });
  assert.deepEqual(parseDshModel(''), { provider: 'deepseek-official', model: 'deepseek-flash' });
  assert.throws(() => parseDshModel('provider/'), /provider\/model/);
  // The fallback route stays the built-in one: operator routes are DSH's own
  // settings document, so this process never lists them itself.
  assert.equal(DSH_DEFAULT_MODEL, 'deepseek-official/deepseek-flash');
  assert.deepEqual(parseDshModel(DSH_DEFAULT_MODEL), { provider: 'deepseek-official', model: 'deepseek-flash' });
  // The effort set is the union DSH understands, not a per-model subset.
  assert.deepEqual([...DSH_EFFORTS], ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']);
});

test('DSH Node gate reports the daemon interpreter', () => {
  assert.equal(assertDshNodeVersion('24.9.0'), '24.9.0');
  assert.equal(assertDshNodeVersion('25.0.0'), '25.0.0');
  assert.throws(() => assertDshNodeVersion('22.19.0'), /dsh requires Node >= 24\.9 \(daemon runs 22\.19\.0\)/);
});

test('DSH template escape leaves ordinary braces intact', () => {
  assert.equal(escapeDshTemplate('a {{unknown}} {x} {{{nested}}}'), 'a {⁠{unknown}} {x} {⁠{{nested}}}');
  assert.equal(escapeDshTemplate(''), '');
});

test('generated patch replaces SDK transport and keeps config/mcp variants', (t) => {
  const patch = buildDshPatch({ policy: 'use {{this}}', bridgePath: '/superagent/bridge.mjs', mcp: { command: '/node', args: ['/mcp.mjs', '--authority', 'private'], toolCallTimeoutMs: 30000 } });
  assert.deepEqual(patch[0], { id: 'sdk-jsonrpc-server', disabled: true });
  const rows = patch.find(row => row.insert).insert;
  assert.deepEqual(rows[0].inject, ['sdkAppStartup', 'loader']);
  assert.equal(rows[0].config.maxTokensAsSuccess, true);
  assert.equal(rows[1].config.toolCallTimeoutMs, 30000);
  assert.equal(patch.find(row => row.id === 'system-prompt').config.personaPrefix, 'use {⁠{this}}');
  const disabled = patch.filter(row => row.disabled).map(row => row.id);
  for (const id of ['tool-ask-user', 'schedule', 'tool-schedule', 'tool-plugin-manager', 'present']) assert.ok(disabled.includes(id));
  // fork inherits the parent composition and cannot take an independent
  // preset, so it is the one delegation tool a child preset cannot scope.
  assert.ok(disabled.includes('tool-subagent-fork'));
  const probe = buildDshPatch({ mcp: null });
  assert.equal(probe.find(row => row.insert).insert.length, 1);
  const native = buildDshPatch({ bridgePath: null });
  assert.equal(native.some(row => row.id === 'sdk-jsonrpc-server'), false);
  assert.equal(native.some(row => row.insert), false);
  // No `llm-pi-ai` row, ever: routes belong to DSH's own settings document, and
  // a patch entry would make settings.mutate answer SETTINGS_OVERRIDDEN.
  assert.equal(buildDshPatch({ mcp: null }).some(row => row.id === 'llm-pi-ai'), false);
  // The retired models.json arguments are not in the signature any more. A stale
  // caller that still passes them cannot reintroduce a provider section: only the
  // destructured keys are read, so the patch is the same one built without them.
  const staleCaller = buildDshPatch({
    mcp: null,
    models: null,
    provider: { test: { api: 'openai-completions', models: [{ id: 'deepseek-chat' }] } },
    modelsFile: '/nowhere/models.json',
    env: { OV_SUPERAGENT_DSH_MODELS_FILE: '/nowhere/models.json' },
  });
  assert.deepEqual(staleCaller, buildDshPatch({ mcp: null }));
  assert.equal(staleCaller.some(row => row.id === 'llm-pi-ai'), false);
  // writeDshPatch takes the same closed signature and writes that same patch.
  const written = writeDshPatch({
    dataDir: temp(t), policy: '', mcp: null, models: null, modelsFile: '/nowhere/models.json',
  });
  assert.equal(JSON.parse(fs.readFileSync(written, 'utf8')).some(row => row.id === 'llm-pi-ai'), false);
});

test('the model settings document is DSH home profiles/sdk/cordis.patch.yml', t => {
  const dataDir = temp(t);
  const expected = path.join(dataDir, 'dsh', 'home', 'profiles', 'sdk', 'cordis.patch.yml');
  assert.equal(dshProfilePatchPath({ dataDir }), expected);
  // It is DSH's own file: the daemon names it but never creates or writes it.
  assert.equal(fs.existsSync(expected), false);
  assert.equal(dshProfilePatchHash({ dataDir }), null);
  assert.throws(() => dshProfilePatchPath({}), /dataDir/);

  fs.mkdirSync(path.dirname(expected), { recursive: true });
  fs.writeFileSync(expected, '- id: llm-pi-ai\n  config:\n    providers:\n');
  const hash = dshProfilePatchHash({ dataDir });
  assert.match(hash, /^[0-9a-f]{64}$/);
  // Content, never mtime: a touched-but-identical document keeps its hash.
  const before = fs.statSync(expected).mtimeMs;
  fs.utimesSync(expected, before / 1000 + 5, before / 1000 + 5);
  assert.equal(dshProfilePatchHash({ dataDir }), hash);
  fs.writeFileSync(expected, '- id: llm-pi-ai\n  config:\n    providers: {}\n');
  assert.notEqual(dshProfilePatchHash({ dataDir }), hash);
});

test('private DSH environment removes inherited DSH settings and pins Node', t => {
  const root = temp(t);
  const env = sanitizeDshEnv({
    PATH: '/bin:/usr/bin', DSH_HOME: '/other', DSH_PROFILE: 'web',
    DEEPSEEK_API_KEY: 'not-printed', OV_SUPERAGENT_INSTANCE_ID: 'do-not-inherit',
  }, { dataDir: root, execPath: '/node24/bin/node', actions: { socketPath: '/tmp/actions.sock', authority: 'token' } });
  assert.equal(env.DSH_HOME, path.join(root, 'dsh', 'home'));
  assert.equal(env.DSH_AGENTS_HOME, path.join(root, 'dsh', 'agents'));
  assert.equal(env.DSH_PERMISSION_MODE, 'danger-full-access');
  assert.equal(env.DSH_TELEMETRY_DISABLED, '1');
  assert.equal(env.DSH_PROFILE, undefined);
  assert.equal(env.DEEPSEEK_API_KEY, 'not-printed');
  assert.equal(env.OV_SUPERAGENT_INSTANCE_ID, undefined);
  assert.equal(env.PATH, '/node24/bin:/bin:/usr/bin');
  for (const directory of Object.values(prepareDshEnvironment(root))) {
    if (typeof directory !== 'string') continue;
    assert.equal(fs.statSync(directory).mode & 0o077, 0);
  }
  assert.throws(() => sanitizeDshEnv({}, {}), /dataDir/);
});

test('patch file is private JSON-compatible YAML with an absolute bridge', t => {
  const patchPath = writeDshPatch({ dataDir: temp(t), policy: 'hello', mcp: null });
  assert.equal(patchPath.endsWith(path.join('dsh', 'run', 'patch.yml')), true);
  assert.equal(fs.statSync(patchPath).mode & 0o077, 0);
  const patch = JSON.parse(fs.readFileSync(patchPath, 'utf8'));
  assert.equal(patch.find(row => row.insert).insert[0].name, DSH_BRIDGE_PATH);
  assert.deepEqual(dshCommand({ bin: '/dsh', patchPath }), { command: '/dsh', args: ['--profile', 'sdk', '--patch', patchPath] });
});

test('bridge handshake requires the id, a v2+ version and every method the caller uses', () => {
  const hello = (overrides = {}) => ({
    bridge: 'superagent-dsh-bridge', version: 2, methods: [...DSH_BRIDGE_CLI_METHODS], ...overrides,
  });
  assert.equal(bridgeHelloSupports(hello(), DSH_BRIDGE_DAEMON_METHODS), true);
  assert.equal(bridgeHelloSupports(hello(), DSH_BRIDGE_CLI_METHODS), true);
  // A newer bridge is compatible; the caller checks for what it uses, not equality.
  assert.equal(bridgeHelloSupports(hello({ version: 3 }), DSH_BRIDGE_CLI_METHODS), true);
  // v1 is what an older release ships; it must be refused, not silently degraded.
  assert.equal(bridgeHelloSupports(hello({ version: 1 }), DSH_BRIDGE_DAEMON_METHODS), false);
  assert.equal(bridgeHelloSupports(hello({ methods: ['session/steer', 'session/cancel'] }), DSH_BRIDGE_DAEMON_METHODS), false);
  // The daemon subset is satisfied by the CLI superset.
  assert.ok(DSH_BRIDGE_DAEMON_METHODS.every(method => DSH_BRIDGE_CLI_METHODS.includes(method)));
  assert.equal(bridgeHelloSupports({ ...hello(), bridge: 'other' }, DSH_BRIDGE_DAEMON_METHODS), false);
  assert.equal(bridgeHelloSupports(null, DSH_BRIDGE_DAEMON_METHODS), false);
  assert.equal(bridgeHelloSupports(hello({ methods: 'session/steer' }), DSH_BRIDGE_DAEMON_METHODS), false);
});

test('NOT_READY is the only retried error and it stops at the budget', async () => {
  const notReady = () => Object.assign(new Error('credentials is not ready'), {
    code: DSH_NOT_READY_CODE, data: { code: 'NOT_READY', service: 'credentials' },
  });
  const sleeps = [];
  const sleep = async (ms) => { sleeps.push(ms); };

  // Three NOT_READY answers then success: backoff 100/200/400.
  let calls = 0;
  const value = await retryDshNotReady(async () => {
    calls += 1;
    if (calls <= 3) throw notReady();
    return 'ok';
  }, { sleep });
  assert.equal(value, 'ok');
  assert.equal(calls, 4);
  assert.deepEqual(sleeps, [100, 200, 400]);

  // A real failure costs one attempt and no wait.
  const failures = [];
  await assert.rejects(
    () => retryDshNotReady(async () => {
      failures.push(1);
      throw Object.assign(new Error('boom'), { code: -32603 });
    }, { sleep }),
    (error) => error.code === -32603,
  );
  assert.equal(failures.length, 1);

  // A service that never arrives ends at the ceiling instead of hanging.
  let attempts = 0;
  let clock = 0;
  await assert.rejects(
    () => retryDshNotReady(async () => {
      attempts += 1;
      clock += 50;
      throw notReady();
    }, { sleep: async (ms) => { clock += ms; }, now: () => clock, ceilingMs: 1000 }),
    (error) => isDshNotReady(error),
  );
  assert.ok(clock <= 1000, `retried past the ceiling: ${clock}ms`);
});
