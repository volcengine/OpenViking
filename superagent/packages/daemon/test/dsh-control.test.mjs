// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
// The short-lived DSH control process: patch, handshake, settings and credentials calls, teardown.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {
  CONTROL_PATCH_FILE, DshControlClient, DshControlError, controlPatchPath,
  withDshControl, writeControlPatch,
} from '../src/dsh-control.mjs';

// A stand-in for `dsh`: it speaks the vika bridge surface this client uses,
// keeps its state in FAKE_DSH_STATE, and appends every frame it receives so a
// test can assert what was asked for (and, just as important, what was not).
// It is a separate process on purpose: the client's job is a real subprocess
// protocol, so the test exercises spawn, LF framing, and process teardown.
const FAKE_DSH = `#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';

const stateDir = process.env.FAKE_DSH_STATE;
if (!stateDir) { process.stderr.write('fake dsh: FAKE_DSH_STATE is not set\\n'); process.exit(2); }
const statePath = path.join(stateDir, 'state.json');
const callsPath = path.join(stateDir, 'calls.jsonl');
const markerPath = path.join(stateDir, 'conflicted');
const mode = process.env.FAKE_DSH_MODE || 'normal';

const METHODS = [
  'session/steer', 'session/cancel',
  'vika/llm/models', 'vika/llm/model/info', 'vika/session/model/select',
  'vika/settings/describe', 'vika/settings/mutate',
  'vika/credentials/set', 'vika/credentials/unset',
];

function load() {
  try { return JSON.parse(fs.readFileSync(statePath, 'utf8')); }
  catch { return { revision: 0, providers: {}, credentials: {}, notReady: 0 }; }
}
function save(state) { fs.writeFileSync(statePath, JSON.stringify(state) + '\\n'); }
function record(entry) { fs.appendFileSync(callsPath, JSON.stringify(entry) + '\\n'); }
function send(frame) { process.stdout.write(JSON.stringify(frame) + '\\n'); }
function respond(id, result) { send({ jsonrpc: '2.0', id, result }); }
function fail(id, code, message) { send({ jsonrpc: '2.0', id, error: { code, message } }); }
function setPath(root, keys, value) {
  let node = root;
  for (const key of keys.slice(0, -1)) {
    if (!node[key] || typeof node[key] !== 'object' || Array.isArray(node[key])) node[key] = {};
    node = node[key];
  }
  node[keys[keys.length - 1]] = value;
}
function unsetPath(root, keys) {
  let node = root;
  for (const key of keys.slice(0, -1)) node = node && node[key];
  if (node && typeof node === 'object') delete node[keys[keys.length - 1]];
}

record({ event: 'start', pid: process.pid, argv: process.argv.slice(2), cwd: process.cwd(),
  home: process.env.DSH_HOME || '', permission: process.env.DSH_PERMISSION_MODE || '',
  telemetry: process.env.DSH_TELEMETRY_DISABLED || '' });

function handle(frame) {
  record({ method: frame.method, params: frame.params === undefined ? null : frame.params });
  const state = load();
  if (mode === 'silent') return;
  if (frame.method === 'shutdown') { respond(frame.id, {}); process.exit(0); }
  if (state.notReady > 0 && frame.method !== 'vika/hello' && frame.method !== 'shutdown') {
    state.notReady -= 1;
    save(state);
    fail(frame.id, -32002, 'settings is not ready');
    return;
  }
  switch (frame.method) {
    case 'vika/hello':
      respond(frame.id, mode === 'old-bridge'
        ? { bridge: 'vika-dsh-bridge', version: 1, methods: ['vika/llm/models'] }
        : { bridge: 'vika-dsh-bridge', version: 2, methods: METHODS });
      return;
    case 'vika/settings/describe':
      respond(frame.id, { ns: 'llm-pi-ai', descriptor: {
        ns: 'llm-pi-ai', autoGenerate: false, applies: 'live', schema: {},
        revision: state.revision,
        value: { providers: state.providers || {} },
        user: { providers: state.userProviders || {} },
        secrets: [],
      } });
      return;
    case 'vika/settings/mutate': {
      if (mode === 'overridden') {
        fail(frame.id, -32603, 'Configuration for "llm-pi-ai" is overridden by a home patch or command-line overlay');
        return;
      }
      if (mode === 'no-entry') { fail(frame.id, -32603, 'No configurable plugin entry "llm-pi-ai"'); return; }
      if (mode === 'conflict-once' && !fs.existsSync(markerPath)) {
        fs.writeFileSync(markerPath, '1');
        fail(frame.id, -32603, 'Configuration entry is no longer available');
        return;
      }
      if (mode === 'exit-on-mutate') { process.stderr.write('fake dsh: cannot mutate\\n'); process.exit(9); }
      const expected = frame.params ? frame.params.expectedRevision : undefined;
      if (typeof expected === 'number' && expected !== state.revision) {
        fail(frame.id, -32603, 'settings namespace "llm-pi-ai" changed since it was read (expected revision '
          + expected + ', now ' + state.revision + ')');
        return;
      }
      for (const op of (frame.params && frame.params.ops) || []) {
        const keys = Array.isArray(op.path) ? op.path.slice(1) : [];
        if (op.op === 'set') setPath(state.providers, keys, op.value);
        else if (op.op === 'unset') unsetPath(state.providers, keys);
      }
      state.revision += 1;
      save(state);
      respond(frame.id, { ok: true });
      return;
    }
    case 'vika/credentials/set':
      state.credentials[frame.params.ref] = frame.params.value;
      save(state);
      respond(frame.id, { ok: true });
      return;
    case 'vika/credentials/unset':
      delete state.credentials[frame.params.ref];
      save(state);
      respond(frame.id, { ok: true });
      return;
    case 'vika/llm/models': {
      const providers = Object.entries(state.providers || {}).map(([route, profile]) => {
        const ref = profile && typeof profile.apiKeyEnv === 'string' ? profile.apiKeyEnv : null;
        const present = ref ? Object.hasOwn(state.credentials, ref) : null;
        const declared = Array.isArray(profile && profile.models) ? profile.models : [];
        return {
          provider: route,
          name: (profile && profile.displayName) || route,
          models: ref && !present ? [] : declared.map((model) => ({
            id: model.id,
            name: model.name || model.id,
            inputModalities: Array.isArray(model.input) ? model.input : ['text'],
          })),
          error: null,
          credential: ref ? { ref, present } : null,
        };
      });
      respond(frame.id, { providers });
      return;
    }
    default:
      fail(frame.id, -32601, 'method not found: ' + frame.method);
  }
}

let buffer = '';
process.stdin.on('data', (chunk) => {
  buffer += chunk;
  for (let index; (index = buffer.indexOf('\\n')) >= 0;) {
    const line = buffer.slice(0, index);
    buffer = buffer.slice(index + 1);
    if (!line.trim()) continue;
    let frame;
    try { frame = JSON.parse(line); } catch { continue; }
    handle(frame);
  }
});
process.stdin.on('end', () => process.exit(0));
`;

function readJsonLines(file) {
  try {
    return fs.readFileSync(file, 'utf8').split('\n').filter(Boolean).map((line) => JSON.parse(line));
  } catch {
    return [];
  }
}

function fakeDsh(t, { mode = 'normal', state = {} } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'fake-dsh-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const script = path.join(dir, 'dsh');
  fs.writeFileSync(script, FAKE_DSH, { mode: 0o755 });
  const stateDir = path.join(dir, 'state');
  fs.mkdirSync(stateDir, { mode: 0o700 });
  const statePath = path.join(stateDir, 'state.json');
  fs.writeFileSync(statePath, `${JSON.stringify({ revision: 0, providers: {}, credentials: {}, notReady: 0, ...state })}\n`);
  return {
    dir, script, stateDir,
    env: { ...process.env, FAKE_DSH_STATE: stateDir, FAKE_DSH_MODE: mode },
    calls: () => readJsonLines(path.join(stateDir, 'calls.jsonl')),
    state: () => JSON.parse(fs.readFileSync(statePath, 'utf8')),
    writeState: (value) => fs.writeFileSync(statePath, `${JSON.stringify(value)}\n`),
  };
}

function instanceDataDir(t) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-dsh-control-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  fs.chmodSync(dataDir, 0o700);
  return dataDir;
}

function isAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

async function waitUntil(predicate, label, timeoutMs = 3000) {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${label}`);
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
}

test('the control patch carries the bridge only, never a provider or MCP section', (t) => {
  const dataDir = instanceDataDir(t);
  const patchPath = writeControlPatch({ dataDir, policy: 'model control' });
  assert.equal(patchPath, controlPatchPath(dataDir));
  assert.equal(path.basename(patchPath), CONTROL_PATCH_FILE);
  assert.equal(fs.statSync(patchPath).mode & 0o777, 0o600);
  const patch = JSON.parse(fs.readFileSync(patchPath, 'utf8'));
  const rows = patch.filter((row) => row && typeof row === 'object');
  assert.ok(rows.some((row) => Array.isArray(row.insert)
    && row.insert.some((entry) => entry.id === 'vika-dsh-bridge')), 'the bridge must be inserted');
  // A provider section here would make settings.mutate answer "overridden by a
  // home patch or command-line overlay", so the control patch must never carry
  // one — and it needs none: the document it edits is DSH's own.
  assert.ok(!rows.some((row) => row.id === 'llm-pi-ai'), 'no llm-pi-ai section');
  assert.ok(!rows.some((row) => Array.isArray(row.insert)
    && row.insert.some((entry) => entry.id === 'vika-personal-agent')), 'no MCP client');
  assert.ok(rows.some((row) => row.id === 'sdk-jsonrpc-server' && row.disabled === true));
});

test('the client verifies the bridge, edits settings and credentials, and takes the process down', async (t) => {
  const dataDir = instanceDataDir(t);
  const fake = fakeDsh(t);
  const client = await DshControlClient.open({ dataDir, bin: fake.script, env: fake.env });
  t.after(() => client.close());

  const described = await client.describeSettings();
  assert.equal(described.descriptor.revision, 0);
  assert.deepEqual(described.descriptor.value.providers, {});

  const profile = {
    api: 'openai-completions',
    baseURL: 'http://127.0.0.1:9/v1',
    apiKeyEnv: 'DEEPSEEK_API_KEY',
    displayName: 'DeepSeek',
    models: [{ id: 'deepseek-chat', name: 'DeepSeek Chat', input: ['text', 'image'] }],
  };
  await client.mutateSettings([{ op: 'set', path: ['providers', 'deepseek'], value: profile }], described.descriptor.revision);
  const after = await client.describeSettings();
  assert.equal(after.descriptor.revision, 1);
  assert.deepEqual(after.descriptor.value.providers.deepseek, profile);

  // A route naming an unresolved reference offers no model: that is the state
  // the CLI reports as "credential missing".
  const before = await client.listModels();
  const beforeDeepSeek = before.providers.find((entry) => entry.provider === 'deepseek');
  assert.deepEqual(beforeDeepSeek.models, []);
  assert.deepEqual(beforeDeepSeek.credential, { ref: 'DEEPSEEK_API_KEY', present: false });

  await client.setCredential('DEEPSEEK_API_KEY', 'sk-test-value');
  const withKey = await client.listModels();
  const deepseek = withKey.providers.find((entry) => entry.provider === 'deepseek');
  assert.deepEqual(deepseek.credential, { ref: 'DEEPSEEK_API_KEY', present: true });
  assert.deepEqual(deepseek.models.map((model) => model.id), ['deepseek-chat']);

  await client.unsetCredential('DEEPSEEK_API_KEY');
  const withoutKey = await client.listModels();
  assert.deepEqual(withoutKey.providers.find((entry) => entry.provider === 'deepseek').models, []);

  const pid = fake.calls().find((call) => call.event === 'start').pid;
  await client.close();
  assert.ok(client.exited, 'the process must have exited');
  assert.ok(!isAlive(pid), 'the process tree must be gone');
  await client.close(); // idempotent

  const calls = fake.calls();
  const started = calls.find((call) => call.event === 'start');
  assert.deepEqual(started.argv.slice(0, 2), ['--profile', 'sdk']);
  assert.equal(started.argv[3], controlPatchPath(dataDir));
  assert.equal(started.cwd, dataDir);
  assert.equal(started.home, path.join(dataDir, 'dsh', 'home'));
  assert.equal(started.permission, 'danger-full-access');
  assert.equal(started.telemetry, '1');
  // No initialize: the settings and credentials services mount at start-up,
  // and initialize would drag a provider round-trip into a config command.
  assert.ok(!calls.some((call) => call.method === 'initialize'), 'initialize must not be sent');
  const methods = calls.filter((call) => call.method).map((call) => call.method);
  assert.equal(methods[0], 'vika/hello');
  assert.ok(methods.includes('shutdown'), 'shutdown is sent on close');
});

test('a bridge without the configuration methods is refused and its process stopped', async (t) => {
  const dataDir = instanceDataDir(t);
  const fake = fakeDsh(t, { mode: 'old-bridge' });
  await assert.rejects(
    DshControlClient.open({ dataDir, bin: fake.script, env: fake.env }),
    (error) => error instanceof DshControlError && /bridge v2/.test(error.message),
  );
  const pid = fake.calls().find((call) => call.event === 'start').pid;
  await waitUntil(() => !isAlive(pid), 'the refused process to exit');
});

test('NOT_READY is retried, other bridge errors are not', async (t) => {
  const dataDir = instanceDataDir(t);
  const fake = fakeDsh(t, { state: { notReady: 2 } });
  const descriptor = await withDshControl({ dataDir, bin: fake.script, env: fake.env }, async (client) => (
    (await client.describeSettings()).descriptor
  ));
  assert.equal(descriptor.revision, 0);
  const describes = fake.calls().filter((call) => call.method === 'vika/settings/describe');
  assert.equal(describes.length, 3, 'two refusals then the answer');
});

test('a request that exceeds its bound kills the process and reports why', async (t) => {
  const dataDir = instanceDataDir(t);
  const fake = fakeDsh(t, { mode: 'silent' });
  const startedAt = Date.now();
  await assert.rejects(
    DshControlClient.open({ dataDir, bin: fake.script, env: fake.env, helloTimeoutMs: 250 }),
    (error) => error instanceof DshControlError && /did not answer vika\/hello within 250ms/.test(error.message),
  );
  assert.ok(Date.now() - startedAt < 5000, 'the bound, not the process, decides how long this takes');
  const pid = fake.calls().find((call) => call.event === 'start').pid;
  await waitUntil(() => !isAlive(pid), 'the timed-out process to be killed');
});

test('a process that dies mid-call fails the call with its exit state', async (t) => {
  const dataDir = instanceDataDir(t);
  const fake = fakeDsh(t, { mode: 'exit-on-mutate' });
  const client = await DshControlClient.open({ dataDir, bin: fake.script, env: fake.env });
  t.after(() => client.close());
  await assert.rejects(
    client.mutateSettings([{ op: 'set', path: ['providers', 'deepseek'], value: {} }], 0),
    (error) => error instanceof DshControlError && /exited \(code 9\)/.test(error.message),
  );
});

test('an unrunnable dsh binary is reported, not thrown from spawn', async (t) => {
  const dataDir = instanceDataDir(t);
  await assert.rejects(
    DshControlClient.open({ dataDir, bin: path.join(dataDir, 'missing-dsh'), env: process.env }),
    (error) => error instanceof DshControlError && /cannot run the DSH command/.test(error.message),
  );
});
