// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { addModelOps, modelIdProblem, parseModelId } from '../src/dsh-model.mjs';
import { setLanguage } from '../src/i18n.mjs';

// The CLI runs are spawned with OV_SUPERAGENT_LANG=en; the in-process builder assertions
// below read the messages directly, so the test process speaks English too.
setLanguage('en');

const BIN = fileURLToPath(new URL('../bin/ov-superagent.mjs', import.meta.url));

// A stand-in for `dsh`, the same protocol-speaking fixture the control client
// tests use. Every invocation is a new process, so the state it edits lives in
// FAKE_DSH_STATE and is shared with the test: the CLI is exercised end to end
// (bin → manifest → control process → bridge frames → settings document)
// without depending on a real DSH install.
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
  'superagent/llm/models', 'superagent/llm/model/info', 'superagent/session/model/select',
  'superagent/settings/describe', 'superagent/settings/mutate',
  'superagent/credentials/set', 'superagent/credentials/unset',
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
  home: process.env.DSH_HOME || '', permission: process.env.DSH_PERMISSION_MODE || '' });

const document = path.join(process.env.DSH_HOME || '', 'profiles', 'sdk', 'cordis.patch.yml');
function documentUsable() {
  try { return fs.readFileSync(document, 'utf8').includes('llm-pi-ai'); } catch { return true; }
}
// A document that IS readable but whose llm-pi-ai entry the service reports as
// missing on the first probe only: that is the state repair rewrites from, and
// the rewrite must make the second probe succeed.
const firstProbeMarker = path.join(stateDir, 'first-probe-done');
function firstProbeUnreadable() {
  if (fs.existsSync(firstProbeMarker)) return false;
  fs.writeFileSync(firstProbeMarker, '1');
  return true;
}

function handle(frame) {
  record({ method: frame.method, params: frame.params === undefined ? null : frame.params });
  const state = load();
  if (mode === 'silent') return;
  if (frame.method === 'shutdown') { respond(frame.id, {}); process.exit(0); }
  if (state.notReady > 0 && frame.method !== 'superagent/hello') {
    state.notReady -= 1;
    save(state);
    fail(frame.id, -32002, 'settings is not ready');
    return;
  }
  switch (frame.method) {
    case 'superagent/hello':
      respond(frame.id, mode === 'old-bridge'
        ? { bridge: 'superagent-dsh-bridge', version: 1, methods: ['superagent/llm/models'] }
        : { bridge: 'superagent-dsh-bridge', version: 2, methods: METHODS });
      return;
    case 'superagent/settings/describe': {
      // A malformed document: the settings service cannot read it and says so
      // through its closed wire taxonomy instead of answering with a descriptor.
      if (mode === 'unreadable' && !documentUsable()) {
        send({ jsonrpc: '2.0', id: frame.id, error: {
          code: -32603,
          message: 'llm-pi-ai is not readable: the profile patch is malformed',
          data: { code: 'SETTINGS_INVALID' },
        } });
        return;
      }
      if (mode === 'describe-refused') {
        send({ jsonrpc: '2.0', id: frame.id, error: {
          code: -32603,
          message: 'the llm-pi-ai entry is not configurable',
          data: { code: 'SETTINGS_NOT_CONFIGURABLE' },
        } });
        return;
      }
      respond(frame.id, { ns: 'llm-pi-ai',
        descriptor: mode === 'no-entry' || (mode === 'no-entry-on-corrupt' && !documentUsable())
          || (mode === 'no-entry-first-probe' && firstProbeUnreadable()) ? null : {
        ns: 'llm-pi-ai', autoGenerate: false, applies: 'live', schema: {},
        revision: state.revision,
        value: { providers: state.providers || {} },
        user: { providers: {} },
        secrets: [],
      } });
      return;
    }
    case 'superagent/settings/mutate': {
      if (mode === 'overridden') {
        fail(frame.id, -32603, 'Configuration for "llm-pi-ai" is overridden by a home patch or command-line overlay');
        return;
      }
      if (mode === 'no-entry-mutate') { fail(frame.id, -32603, 'No configurable plugin entry "llm-pi-ai"'); return; }
      // A v2 bridge frame with no data.code: only the message carries the
      // class, which is exactly what the CLI's text fallback must classify.
      if (mode === 'no-longer-configurable') {
        fail(frame.id, -32603, 'Plugin entry "llm-pi-ai" is no longer configurable');
        return;
      }
      if (mode === 'conflict-always') {
        fail(frame.id, -32603, 'Configuration entry is no longer available');
        return;
      }
      if (mode === 'overridden-code') {
        send({ jsonrpc: '2.0', id: frame.id, error: { code: -32603, message: 'refused', data: { code: 'SETTINGS_OVERRIDDEN' } } });
        return;
      }
      if (mode === 'not-configurable-code') {
        send({ jsonrpc: '2.0', id: frame.id, error: { code: -32603, message: 'refused', data: { code: 'SETTINGS_NOT_CONFIGURABLE' } } });
        return;
      }
      if (mode === 'conflict-once-code' && !fs.existsSync(markerPath)) {
        fs.writeFileSync(markerPath, '1');
        send({ jsonrpc: '2.0', id: frame.id, error: { code: -32603, message: 'refused', data: { code: 'SETTINGS_CONFLICT' } } });
        return;
      }
      if (mode === 'conflict-once' && !fs.existsSync(markerPath)) {
        fs.writeFileSync(markerPath, '1');
        fail(frame.id, -32603, 'Configuration entry is no longer available');
        return;
      }
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
    case 'superagent/credentials/set':
      // A credential the service refuses, to prove a failure names what did
      // and did not land instead of aborting on an unnamed half-write.
      // Matched as a prefix: the migration stages every value under a
      // temporary reference, and a service refusing a value refuses it
      // whichever name it is written under.
      if (mode === 'credential-refused' && frame.params.ref.startsWith(process.env.FAKE_DSH_REFUSE_REF)) {
        fail(frame.id, -32603, 'credentials service refused ' + frame.params.ref);
        return;
      }
      state.credentials[frame.params.ref] = frame.params.value;
      save(state);
      respond(frame.id, { ok: true });
      return;
    case 'superagent/credentials/unset':
      delete state.credentials[frame.params.ref];
      save(state);
      respond(frame.id, { ok: true });
      return;
    case 'superagent/llm/models': {
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
          credential: ref ? { ref, present, ...(mode === 'unknown-credential' ? { known: false } : {}) } : null,
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

function fixture(t, { agents = ['primary'], state = null } = {}) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-dsh-model-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const config = path.join(home, '.ov-superagent', 'config.json');
  fs.mkdirSync(path.dirname(config), { recursive: true, mode: 0o700 });
  fs.writeFileSync(config, `${JSON.stringify({
    version: 1,
    defaultInstance: agents[0],
    env: {},
    agents: agents.map((id, index) => ({
      id,
      displayName: id === 'primary' ? 'Primary' : 'Secondary',
      channel: 'telegram',
      port: 3738 + index,
      dataDir: path.join(home, 'data', id),
      workspace: path.join(home, 'workspace', id),
      ovConfig: path.join(home, 'ov', `${id}.json`),
      model: 'dsh',
    })),
  }, null, 2)}\n`);

  const fakeDir = fs.mkdtempSync(path.join(os.tmpdir(), 'fake-dsh-'));
  t.after(() => fs.rmSync(fakeDir, { recursive: true, force: true }));
  const script = path.join(fakeDir, 'dsh');
  fs.writeFileSync(script, FAKE_DSH, { mode: 0o755 });
  const stateDir = path.join(fakeDir, 'state');
  fs.mkdirSync(stateDir, { mode: 0o700 });
  const statePath = path.join(stateDir, 'state.json');
  const initial = {
    revision: 0, providers: {}, credentials: {}, notReady: 0, ...(state ?? {}),
  };
  fs.writeFileSync(statePath, `${JSON.stringify(initial)}\n`);
  const env = {
    ...process.env,
    HOME: home,
    OV_SUPERAGENT_CONFIG: config,
    OV_SUPERAGENT_LANG: 'en',
    DSH_BIN: script,
    FAKE_DSH_STATE: stateDir,
  };
  return {
    home, config, env, script, stateDir,
    agent: (id = 'primary') => ({
      dataDir: path.join(home, 'data', id),
      state: statePath,
    }),
    calls: () => readJsonLines(path.join(stateDir, 'calls.jsonl')),
    state: () => JSON.parse(fs.readFileSync(statePath, 'utf8')),
    writeState: (value) => fs.writeFileSync(statePath, `${JSON.stringify(value)}\n`),
  };
}

function run(f, args, { env = {}, input = '' } = {}) {
  return spawnSync(process.execPath, [BIN, 'dsh', 'model', ...args], {
    encoding: 'utf8',
    env: { ...f.env, ...env },
    input,
    timeout: 120_000,
  });
}

function output(result) {
  return `${result.stdout}${result.stderr}`;
}

// The document `repair` is allowed to rewrite, and nothing else is.
function settingsDocument(f, instance = 'primary') {
  return path.join(f.agent(instance).dataDir, 'dsh', 'home', 'profiles', 'sdk', 'cordis.patch.yml');
}

function backupsIn(directory) {
  return fs.readdirSync(directory).filter((name) => name.includes('.bak-'));
}

// A healthy document: bytes `repair` must never touch unless the probe proves
// the settings service cannot read it.
const HEALTHY_DOCUMENT = `- id: llm-pi-ai
  config:
    providers:
      keepme:
        api: openai-completions
        baseURL: http://127.0.0.1:8080/v1
        apiKeyEnv: KEEP_KEY
        models:
          - id: deepseek-chat
`;

const DEEPSEEK = {
  api: 'openai-completions',
  baseURL: 'http://127.0.0.1:8080/v1',
  apiKeyEnv: 'DEEPSEEK_API_KEY',
  displayName: 'DeepSeek Gateway',
  models: [
    { id: 'deepseek-chat', name: 'DeepSeek Chat', input: ['text', 'image'], contextWindow: 1000000, maxTokens: 384000 },
  ],
};

test('the command group has help text, and the top-level usage names it', (t) => {
  const f = fixture(t);
  const help = run(f, ['--help']);
  assert.equal(help.status, 0);
  assert.match(help.stdout, /ov-superagent dsh model \[list\]/);
  for (const line of ['remove <route>', 'import <models.json>', 'key set <REF>', 'key unset <REF>']) {
    assert.ok(help.stdout.includes(line), `help must document ${line}`);
  }
  // Adding lives in the menu now, so the usage must not advertise an add form.
  assert.doesNotMatch(help.stdout, /dsh model add/);
  assert.match(help.stdout, /添加 model|Add a model/);
  const unknown = run(f, ['nope']);
  assert.equal(unknown.status, 2);
  assert.match(unknown.stderr, /there is no "nope" subcommand/);
  const add = run(f, ['add', 'deepseek/group/x']);
  assert.equal(add.status, 2);
  assert.match(add.stderr, /there is no "add" subcommand/);
  const usage = spawnSync(process.execPath, [BIN, 'help'], { encoding: 'utf8', env: f.env });
  assert.equal(usage.status, 0);
  assert.match(usage.stdout, /dsh model/);
});

test('without a manifest the command says how to create an instance', (t) => {
  const f = fixture(t);
  const empty = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-empty-'));
  t.after(() => fs.rmSync(empty, { recursive: true, force: true }));
  const result = run(f, ['list'], { env: { HOME: empty, OV_SUPERAGENT_CONFIG: path.join(empty, 'config.json') } });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /No instance yet: run ov-superagent setup/);
});

test('list shows declared routes, catalogue models and credential state', (t) => {
  const f = fixture(t, {
    agents: ['primary', 'secondary'],
    state: {
      revision: 3,
      providers: {
        deepseek: DEEPSEEK,
        'idle-key': {
          api: 'openai-completions',
          baseURL: 'http://127.0.0.1:9/v1',
          apiKeyEnv: 'IDLE_API_KEY',
          models: [{ id: 'not-served', name: 'Not Served' }],
        },
        'ambient-auth': {
          api: 'openai-responses',
          baseURL: 'https://example.test/v1',
          models: [{ id: 'ambient-model', name: 'Ambient Model', input: ['text'] }],
        },
      },
      credentials: { DEEPSEEK_API_KEY: 'sk-stored' },
    },
  });
  // Two instances in this manifest, so the instance must be named: without
  // `--instance` a piped command refuses instead of silently reading the
  // manifest default (which made the second instance unreachable).
  const unnamed = run(f, ['list']);
  assert.equal(unnamed.status, 1);
  assert.match(unnamed.stderr, /There are several instances; name one after the command/);

  const text = run(f, ['list', '--instance', 'primary']);
  assert.equal(text.status, 0);
  assert.match(text.stdout, /instance primary \(.*data\/primary\)/);
  assert.match(text.stdout, /deepseek {2}DeepSeek Gateway {2}openai-completions {2}http:\/\/127\.0\.0\.1:8080\/v1/);
  assert.match(text.stdout, /deepseek\/deepseek-chat {2}DeepSeek Chat {2}\[text, image\]/);
  assert.match(text.stdout, /key DEEPSEEK_API_KEY {2}credential ready/);
  // A declared route whose reference has no value lists no catalogue model,
  // but the declaration is still shown so the user can see why.
  assert.match(text.stdout, /key IDLE_API_KEY {2}credential missing {2}→ ov-superagent dsh model key set IDLE_API_KEY/);
  assert.match(text.stdout, /idle-key\/not-served .*\(not in the catalogue\)/);
  assert.match(text.stdout, /key: none \(DSH resolves its own auth\)/);

  const json = run(f, ['list', '--json', '--instance', 'primary']);
  assert.equal(json.status, 0);
  const rows = JSON.parse(json.stdout);
  assert.deepEqual(rows.map((row) => row.provider), ['ambient-auth', 'deepseek', 'idle-key']);
  const deepseek = rows.find((row) => row.provider === 'deepseek');
  assert.deepEqual(deepseek.credential, { ref: 'DEEPSEEK_API_KEY', present: true, known: true });
  assert.equal(deepseek.declared, true);
  assert.equal(deepseek.baseURL, DEEPSEEK.baseURL);
  assert.deepEqual(deepseek.models, [{
    id: 'deepseek-chat', name: 'DeepSeek Chat', input: ['text', 'image'], inCatalog: true,
  }]);
  const idle = rows.find((row) => row.provider === 'idle-key');
  assert.deepEqual(idle.credential, { ref: 'IDLE_API_KEY', present: false, known: true });
  assert.equal(idle.models[0].inCatalog, false);

  // --instance picks the other instance's data directory: the control process
  // starts on that instance's own DSH home.
  const other = run(f, ['list', '--json', '--instance', 'secondary']);
  assert.equal(other.status, 0, output(other));
  assert.ok(Array.isArray(JSON.parse(other.stdout)));
  const lastStart = f.calls().filter((call) => call.event === 'start').at(-1);
  assert.equal(lastStart.home, path.join(f.home, 'data', 'secondary', 'dsh', 'home'));
});

test('shared mode makes the model command read the host DSH home', (t) => {
  const f = fixture(t);
  const manifest = JSON.parse(fs.readFileSync(f.config, 'utf8'));
  manifest.agents[0].dshHomeMode = 'shared';
  fs.writeFileSync(f.config, JSON.stringify(manifest));
  const result = run(f, ['list', '--json']);
  assert.equal(result.status, 0, output(result));
  const lastStart = f.calls().filter(call => call.event === 'start').at(-1);
  assert.equal(lastStart.home, path.join(f.home, '.dsh'));
  assert.equal(fs.existsSync(path.join(f.agent().dataDir, 'dsh', 'home')), false);
});

// `ov-superagent dsh model add` no longer exists: the menu wizard is the only way to add
// a model, and it writes through `addModelOps`. The rules that used to be
// asserted through the flag form are asserted on that builder directly, so the
// merge/idempotency/reference-allocation behaviour keeps its coverage.
const baseSpec = {
  route: 'deepseek',
  model: 'deepseek-chat',
  api: 'openai-completions',
  baseURL: DEEPSEEK.baseURL,
  keyEnv: null,
  name: null,
  input: null,
  contextWindow: null,
  maxTokens: null,
  noReasoning: false,
};

function applyOps(providers, ops) {
  const next = structuredClone(providers);
  for (const op of ops) {
    const keys = op.path.slice(1);
    if (op.op === 'unset') {
      let node = next;
      for (const key of keys.slice(0, -1)) node = node?.[key];
      if (node) delete node[keys[keys.length - 1]];
      continue;
    }
    let node = next;
    for (const key of keys.slice(0, -1)) {
      if (!node[key] || typeof node[key] !== 'object') node[key] = {};
      node = node[key];
    }
    node[keys[keys.length - 1]] = structuredClone(op.value);
  }
  return next;
}

test('addModelOps creates a route, keeps unnamed fields, and is idempotent', () => {
  const spec = {
    ...baseSpec,
    name: 'DeepSeek Gateway',
    input: ['text', 'image'],
    contextWindow: 1000000,
    maxTokens: 384000,
  };
  const notes = [];
  const seeded = applyOps({}, addModelOps({}, spec, { notes }));
  assert.deepEqual(seeded.deepseek, {
    api: 'openai-completions',
    baseURL: DEEPSEEK.baseURL,
    apiKeyEnv: 'DEEPSEEK_API_KEY',
    displayName: 'DeepSeek Gateway',
    models: [{
      id: 'deepseek-chat', name: 'DeepSeek Gateway', input: ['text', 'image'], contextWindow: 1000000, maxTokens: 384000,
    }],
  });

  // The same spec again changes nothing; a spec that does not name a field
  // leaves it alone.
  const again = addModelOps(seeded, spec, { notes });
  assert.deepEqual(applyOps(seeded, again), seeded);
  const updated = addModelOps(seeded, { ...baseSpec, name: 'DeepSeek Chat', noReasoning: true }, { notes });
  const profile = applyOps(seeded, updated).deepseek;
  assert.equal(profile.models.length, 1, 'the same model id updates in place instead of appending');
  assert.deepEqual(profile.models[0], {
    id: 'deepseek-chat', name: 'DeepSeek Chat', input: ['text', 'image'], contextWindow: 1000000, maxTokens: 384000,
    reasoningEfforts: false,
  });
  assert.equal(profile.baseURL, DEEPSEEK.baseURL);
  assert.equal(profile.apiKeyEnv, 'DEEPSEEK_API_KEY');
  assert.equal(profile.displayName, 'DeepSeek Gateway', 'a later write does not rename the route');

  // A second model keeps the first one and the route's other fields.
  const second = applyOps(seeded, addModelOps(seeded, { ...baseSpec, model: 'group/second-model', input: ['text'] }, { notes }));
  assert.deepEqual(second.deepseek.models.map((model) => model.id), ['deepseek-chat', 'group/second-model']);
  assert.equal(second.deepseek.apiKeyEnv, 'DEEPSEEK_API_KEY', 'an existing route keeps its reference');
});

test('addModelOps defaults the protocol of a new route and honours an explicit reference', () => {
  const profile = applyOps({}, addModelOps({}, {
    ...baseSpec, route: 'gateway', model: 'model-a', api: null, baseURL: 'https://gateway.test/v1', keyEnv: 'CUSTOM_REF_KEY',
  })).gateway;
  assert.equal(profile.api, 'openai-completions', 'a new route defaults to the completion protocol');
  assert.equal(profile.apiKeyEnv, 'CUSTOM_REF_KEY');
  assert.equal(profile.models[0].id, 'model-a');
  assert.ok(!('reasoningEfforts' in profile.models[0]), 'reasoning is only declared when asked for');
});

test('addModelOps merges into an existing route instead of replacing it', () => {
  const providers = {
    deepseek: { ...DEEPSEEK, transport: 'sse', defaultInput: ['text'], models: [{ id: 'legacy-model', name: 'Legacy' }] },
  };
  const profile = applyOps(providers, addModelOps(providers, { ...baseSpec, name: 'DeepSeek Chat' })).deepseek;
  assert.equal(profile.transport, 'sse', 'an unnamed field survives the upsert');
  assert.deepEqual(profile.defaultInput, ['text']);
  assert.equal(profile.api, 'openai-completions');
  assert.deepEqual(profile.models.map((model) => model.id), ['legacy-model', 'deepseek-chat']);
});

// P1-1: a new route derives its reference from its name, and two legal names
// can derive the same one. The second route must not silently point at the
// first route's credential.
test('addModelOps does not hand a new route a reference another route already resolves', () => {
  const notes = [];
  const first = applyOps({}, addModelOps({}, { ...baseSpec, route: 'foo-bar', model: 'model-one', baseURL: 'http://one.test/v1' }, { notes }));
  const second = applyOps(first, addModelOps(first, { ...baseSpec, route: 'foo_bar', model: 'model-two', baseURL: 'http://two.test/v1' }, { notes }));
  assert.equal(second['foo-bar'].apiKeyEnv, 'FOO_BAR_API_KEY');
  assert.notEqual(second.foo_bar.apiKeyEnv, 'FOO_BAR_API_KEY',
    'the second route must not resolve the first route\'s secret');
  assert.match(second.foo_bar.apiKeyEnv, /^FOO_BAR_API_KEY_\d+$/);
  assert.ok(notes.some((note) => /already used by foo-bar/.test(note)), 'the disambiguation is reported');

  // A route that already has its own reference keeps it on a later write.
  const third = applyOps(second, addModelOps(second, { ...baseSpec, route: 'foo_bar', model: 'model-three' }, { notes }));
  assert.equal(third.foo_bar.apiKeyEnv, second.foo_bar.apiKeyEnv);
});

// The verifier found `add r1/m --key-env SHARED` twice silently gave two routes
// one reference: the same shared-secret state the migration must never write.
// An explicit reuse is the one place the operator asks for it, so it is refused
// with the sentence that explains what sharing a reference means.
test('addModelOps refuses a reference another route already resolves', () => {
  const notes = [];
  const providers = applyOps({}, addModelOps({}, {
    ...baseSpec, route: 'foo-bar', model: 'm1', baseURL: 'http://one.test/v1', keyEnv: 'SHARED_API_KEY',
  }, { notes }));
  assert.throws(
    () => addModelOps(providers, { ...baseSpec, route: 'foo_bar', model: 'm1', baseURL: 'http://two.test/v1', keyEnv: 'SHARED_API_KEY' }, { notes }),
    /SHARED_API_KEY[\s\S]*already resolved by foo-bar[\s\S]*another reference/,
  );
  // The route that owns the reference may keep naming it: that is an upsert,
  // not a share.
  const own = applyOps(providers, addModelOps(providers, { ...baseSpec, route: 'foo-bar', model: 'm2', keyEnv: 'SHARED_API_KEY' }, { notes }));
  assert.equal(own['foo-bar'].apiKeyEnv, 'SHARED_API_KEY');
});

test('addModelOps warns when an existing route already shares its reference', () => {
  // A hand-edited document can share one reference between two routes. The
  // write is an upsert and does not re-point them, but the sharing is said out
  // loud instead of staying invisible.
  const providers = {
    'foo-bar': { api: 'openai-completions', baseURL: 'http://one.test/v1', apiKeyEnv: 'SHARED_API_KEY', models: [{ id: 'm1' }] },
    foo_bar: { api: 'openai-completions', baseURL: 'http://two.test/v1', apiKeyEnv: 'SHARED_API_KEY', models: [{ id: 'm1' }] },
  };
  const notes = [];
  addModelOps(providers, { ...baseSpec, route: 'foo_bar', model: 'm2' }, { notes });
  assert.ok(notes.some((note) => /also resolved by foo-bar/.test(note)));
  assert.ok(notes.some((note) => /same secret/.test(note)));
});

test('remove drops one model, then the route, and reports the leftover reference', (t) => {
  const f = fixture(t, {
    state: {
      revision: 5,
      providers: {
        deepseek: {
          ...DEEPSEEK,
          models: [
            { id: 'deepseek-chat', name: 'DeepSeek Chat' },
            { id: 'group/second', name: 'Second' },
          ],
        },
      },
    },
  });
  const one = run(f, ['remove', 'deepseek/group/second']);
  assert.equal(one.status, 0, output(one));
  assert.match(one.stdout, /Removed model group\/second from deepseek/);
  assert.deepEqual(f.state().providers.deepseek.models.map((model) => model.id), ['deepseek-chat']);

  const last = run(f, ['remove', 'deepseek/deepseek-chat']);
  assert.equal(last.status, 0, output(last));
  assert.match(last.stdout, /Removed model deepseek-chat from deepseek/);
  // Removing the last model removes the unusable route as well.
  assert.equal(f.state().providers.deepseek, undefined);
  assert.match(last.stdout, /credential DEEPSEEK_API_KEY is left in place/);

  const missing = run(f, ['remove', 'deepseek']);
  assert.equal(missing.status, 1);
  assert.match(missing.stderr, /There is no route named deepseek/);
});

test('key set reads stdin and never echoes the value; key unset removes the reference', (t) => {
  const f = fixture(t);
  const secret = 'sk-very-secret-value-123';
  const set = run(f, ['key', 'set', 'DEEPSEEK_API_KEY'], { input: `${secret}\n` });
  assert.equal(set.status, 0, output(set));
  assert.match(set.stdout, /Stored credential DEEPSEEK_API_KEY/);
  assert.ok(!output(set).includes(secret), 'the value must never be printed');
  assert.equal(f.state().credentials.DEEPSEEK_API_KEY, secret);

  const explicit = run(f, ['key', 'set', 'DEEPSEEK_API_KEY', '--stdin'], { input: 'sk-second\n' });
  assert.equal(explicit.status, 0, output(explicit));
  assert.equal(f.state().credentials.DEEPSEEK_API_KEY, 'sk-second');

  const unset = run(f, ['key', 'unset', 'DEEPSEEK_API_KEY']);
  assert.equal(unset.status, 0, output(unset));
  assert.match(unset.stdout, /Removed credential DEEPSEEK_API_KEY/);
  assert.equal(f.state().credentials.DEEPSEEK_API_KEY, undefined);

  const empty = run(f, ['key', 'set', 'DEEPSEEK_API_KEY'], { input: '' });
  assert.equal(empty.status, 1);
  assert.match(empty.stderr, /No credential value arrived/);

  const plaintext = run(f, ['key', 'set', 'DEEPSEEK_API_KEY', 'sk-inline']);
  assert.equal(plaintext.status, 2);
  assert.match(plaintext.stderr, /takes exactly one credential reference/);
  const badRef = run(f, ['key', 'set', '9bad'], { input: 'x\n' });
  assert.equal(badRef.status, 2);
  assert.match(badRef.stderr, /Invalid credential reference/);
});

test('import --dry-run prints the operations and starts no process', (t) => {
  const f = fixture(t);
  const models = path.join(f.home, 'models.json');
  fs.writeFileSync(models, JSON.stringify({ providers: {
    deepseek: {
      name: 'DeepSeek Gateway',
      baseUrl: DEEPSEEK.baseURL,
      api: 'openai-completions',
      apiKey: 'sk-literal-secret',
      compat: { supportsStore: false, thinkingFormat: 'deepseek', nonsense: true },
      models: [{
        id: 'deepseek-chat',
        name: 'DeepSeek Chat',
        reasoning: true,
        input: ['text', 'image'],
        contextWindow: 1000000,
        maxTokens: 384000,
        thinkingLevelMap: { minimal: null, low: 'low', medium: null, high: 'high', max: 'max' },
      }],
    },
    unsupported: { api: 'google-generative-ai', models: [{ id: 'x' }] },
  } }));
  const result = run(f, ['import', models, '--dry-run']);
  assert.equal(result.status, 0, output(result));
  assert.match(result.stdout, /Would write 1 route\(s\)/);
  assert.match(result.stdout, /"path": \[\s*"providers",\s*"deepseek"\s*\]/);
  assert.match(result.stdout, /"apiKeyEnv": "DEEPSEEK_API_KEY"/);
  assert.match(result.stdout, /"reasoningEfforts"/);
  assert.match(result.stdout, /Would store credentials: DEEPSEEK_API_KEY/);
  assert.ok(!output(result).includes('sk-literal-secret'), 'a dry run must not print a credential');
  assert.match(result.stdout, /unsupported: api "google-generative-ai" is not served by DSH/);
  assert.match(result.stdout, /nonsense is not configurable/);
  assert.equal(f.calls().length, 0, 'a dry run starts no DSH process');
});

test('import translates a Pi models.json into native providers and credentials', (t) => {
  const f = fixture(t);
  const models = path.join(f.home, 'models.json');
  fs.writeFileSync(models, JSON.stringify({ providers: {
    deepseek: {
      name: 'DeepSeek Gateway',
      baseUrl: DEEPSEEK.baseURL,
      api: 'openai-completions',
      apiKey: 'sk-literal-secret',
      compat: { supportsStore: false, thinkingFormat: 'deepseek' },
      models: [{
        id: 'deepseek-chat',
        name: 'DeepSeek Chat',
        reasoning: true,
        input: ['text', 'image'],
        contextWindow: 1000000,
        maxTokens: 384000,
        thinkingLevelMap: { minimal: null, low: 'low', medium: null, high: 'high', max: 'max' },
      }],
    },
    ambient: { api: 'openai-responses', baseUrl: 'https://example.test/v1', models: [{ id: 'ambient-model', name: 'Ambient' }] },
  } }));
  const result = run(f, ['import', models]);
  assert.equal(result.status, 0, output(result));
  assert.match(result.stdout, /Imported 2 route\(s\)/);
  assert.match(result.stdout, /Stored 1 credential\(s\): DEEPSEEK_API_KEY/);
  assert.ok(!output(result).includes('sk-literal-secret'), 'the value must never be printed');
  const state = f.state();
  assert.deepEqual(state.providers.deepseek, {
    displayName: 'DeepSeek Gateway',
    api: 'openai-completions',
    baseURL: DEEPSEEK.baseURL,
    compat: { supportsStore: false, thinkingFormat: 'deepseek' },
    models: [{
      id: 'deepseek-chat',
      name: 'DeepSeek Chat',
      contextWindow: 1000000,
      maxTokens: 384000,
      input: ['text', 'image'],
      reasoningEfforts: { off: null, low: 'low', high: 'high', max: 'max' },
    }],
    apiKeyEnv: 'DEEPSEEK_API_KEY',
  });
  // A Pi model that does not declare reasoning is migrated as non-reasoning:
  // a hand-declared route has no installed catalogue to inherit from.
  assert.deepEqual(state.providers.ambient, {
    api: 'openai-responses',
    baseURL: 'https://example.test/v1',
    models: [{ id: 'ambient-model', name: 'Ambient', reasoningEfforts: false }],
  });
  assert.equal(state.credentials.DEEPSEEK_API_KEY, 'sk-literal-secret');
});

test('import reads a neighbouring auth.json when models.json names no key', (t) => {
  const f = fixture(t);
  const models = path.join(f.home, 'models.json');
  fs.writeFileSync(models, JSON.stringify({ providers: {
    deepseek: { api: 'openai-completions', baseUrl: DEEPSEEK.baseURL, models: [{ id: 'deepseek-chat' }] },
  } }));
  fs.writeFileSync(path.join(f.home, 'auth.json'), JSON.stringify({ deepseek: { type: 'api_key', key: 'sk-from-auth' } }));
  const result = run(f, ['import', models]);
  assert.equal(result.status, 0, output(result));
  assert.equal(f.state().providers.deepseek.apiKeyEnv, 'DEEPSEEK_API_KEY');
  assert.equal(f.state().credentials.DEEPSEEK_API_KEY, 'sk-from-auth');
});

test('a settings refusal becomes an instruction, and a concurrent write is retried once', (t) => {
  // `add` is gone, so the write is driven through `remove`, which goes through
  // the same settings service and the same error taxonomy.
  const f = fixture(t, { state: { providers: { deepseek: DEEPSEEK } } });
  const write = ['remove', 'deepseek/deepseek-chat'];

  const overridden = run(f, write, { env: { FAKE_DSH_MODE: 'overridden' } });
  assert.equal(overridden.status, 1);
  assert.match(overridden.stderr, /overrides llm-pi-ai/);

  const disabled = run(f, write, { env: { FAKE_DSH_MODE: 'no-entry-mutate' } });
  assert.equal(disabled.status, 1);
  assert.match(disabled.stderr, /No writable llm-pi-ai entry|no writable llm-pi-ai entry/);

  // `list` degrades: the catalogue is still worth showing, and the note says
  // why the declared routes are missing.
  const noEntry = run(f, ['list'], { env: { FAKE_DSH_MODE: 'no-entry' } });
  assert.equal(noEntry.status, 0, output(noEntry));
  assert.match(noEntry.stdout, /Could not read the settings: .*cannot read a writable llm-pi-ai entry/);
  assert.match(noEntry.stdout, /ov-superagent dsh model repair/);
  // In JSON mode the same note moves to stderr so stdout stays parseable, and the
  // exit code carries what the note says: a caller parsing the array cannot tell
  // "no routes" from "the document could not be read", and would infer the first.
  const noEntryJson = run(f, ['list', '--json'], { env: { FAKE_DSH_MODE: 'no-entry' } });
  assert.equal(noEntryJson.status, 1, 'a failed settings read is not a successful list');
  assert.ok(Array.isArray(JSON.parse(noEntryJson.stdout)), 'stdout stays a parseable array');
  assert.match(noEntryJson.stderr, /Could not read the settings/);
  // The catalogue-only view is still useful, so the rows are there.
  assert.ok(JSON.parse(noEntryJson.stdout).every((row) => row.declared === false));

  const before = f.calls().length;
  const conflicted = run(f, write, { env: { FAKE_DSH_MODE: 'conflict-once' } });
  assert.equal(conflicted.status, 0, output(conflicted));
  assert.equal(f.state().providers.deepseek, undefined, 'the retry removed the route\'s only model');
  const attempt = f.calls().slice(before);
  const mutates = attempt.filter((call) => call.method === 'superagent/settings/mutate');
  assert.equal(mutates.length, 2, 'the conflict is retried once and the retry wins');
  const describes = attempt.filter((call) => call.method === 'superagent/settings/describe');
  assert.equal(describes.length, 2, 'the retry re-reads the document before writing');
});

test('repair backs up a hand-broken document, writes the minimal one, then verifies', (t) => {
  const f = fixture(t);
  const document = path.join(f.agent().dataDir, 'dsh', 'home', 'profiles', 'sdk', 'cordis.patch.yml');
  fs.mkdirSync(path.dirname(document), { recursive: true, mode: 0o700 });
  const broken = '{"providers": [this is not yaml';
  fs.writeFileSync(document, broken);
  const result = run(f, ['repair'], { env: { FAKE_DSH_MODE: 'no-entry-on-corrupt' } });
  assert.equal(result.status, 0, output(result));
  assert.match(result.stdout, /Before repair: the llm-pi-ai entry is not writable/);
  assert.match(result.stdout, /Backed up to .*cordis\.patch\.yml\.bak-\d{14}/);
  assert.match(result.stdout, /Wrote the minimal llm-pi-ai document/);
  assert.match(result.stdout, /Verified: 0 route\(s\), revision 0/);
  // JSON is a YAML subset: the recovery document is one legal entry row.
  assert.equal(fs.readFileSync(document, 'utf8'), '[{"id":"llm-pi-ai"}]\n');
  assert.equal(fs.statSync(document).mode & 0o777, 0o600);
  const backups = fs.readdirSync(path.dirname(document)).filter((name) => name.includes('.bak-'));
  assert.equal(backups.length, 1);
  assert.equal(fs.readFileSync(path.join(path.dirname(document), backups[0]), 'utf8'), broken);
});

// B7: the rewrite is the one destructive step in this command group, so it says
// what it costs before it costs it. A document whose shape the reader cannot
// parse falls back to a byte count instead of a guessed route count.
test('repair reports how many routes the rewrite discards', (t) => {
  const f = fixture(t);
  const document = settingsDocument(f);
  fs.mkdirSync(path.dirname(document), { recursive: true, mode: 0o700 });
  fs.writeFileSync(document, HEALTHY_DOCUMENT);
  const result = run(f, ['repair'], { env: { FAKE_DSH_MODE: 'no-entry-first-probe' } });
  assert.equal(result.status, 0, output(result));
  // HEALTHY_DOCUMENT declares exactly one route, and the minimal document drops it.
  assert.match(result.stdout, /This discards 1 route\(s\) \(the minimal document has no providers section\)/);
  assert.match(result.stdout, /Backed up to /);
  // The count is printed before the write, not after it.
  assert.ok(result.stdout.indexOf('This discards') < result.stdout.indexOf('Wrote the minimal'),
    'the discarded count comes before the rewrite');
  assert.equal(fs.readFileSync(document, 'utf8'), '[{"id":"llm-pi-ai"}]\n');
});

test('repair falls back to a byte count when the document shape is not readable', (t) => {
  const f = fixture(t);
  const document = settingsDocument(f);
  fs.mkdirSync(path.dirname(document), { recursive: true, mode: 0o700 });
  const broken = '{"providers": [this is not yaml';
  fs.writeFileSync(document, broken);
  const result = run(f, ['repair'], { env: { FAKE_DSH_MODE: 'no-entry-first-probe' } });
  assert.equal(result.status, 0, output(result));
  assert.match(result.stdout,
    new RegExp(`This discards every route in the original document \\(${broken.length} bytes; its shape was not recognizable, so no count\\)`));
});

test('repair says nothing was discarded when the document declares no route', (t) => {
  const f = fixture(t);
  const document = settingsDocument(f);
  fs.mkdirSync(path.dirname(document), { recursive: true, mode: 0o700 });
  fs.writeFileSync(document, '- id: llm-pi-ai\n  config:\n    providers: {}\n');
  const result = run(f, ['repair'], { env: { FAKE_DSH_MODE: 'no-entry-first-probe' } });
  assert.equal(result.status, 0, output(result));
  assert.match(result.stdout, /This discards 0 route\(s\)/);
});

test('the help text documents where repair puts its backup', (t) => {
  const f = fixture(t);
  const help = run(f, ['--help']);
  assert.equal(help.status, 0, output(help));
  // The backup rule is part of the contract: an operator must be able to find the
  // original bytes after the rewrite without reading the source.
  assert.match(help.stdout, /<document>\.bak-<14-digit timestamp>/);
  assert.match(help.stdout, /-1, -2 \.\.\. appended if it already exists/);
  assert.match(help.stdout, /number of routes about to be\s+discarded is printed/);
});

test('repair is a no-op on a writable document', (t) => {
  const f = fixture(t, { state: { revision: 2, providers: { deepseek: DEEPSEEK }, credentials: {} } });
  const document = path.join(f.agent().dataDir, 'dsh', 'home', 'profiles', 'sdk', 'cordis.patch.yml');
  const result = run(f, ['repair']);
  assert.equal(result.status, 0, output(result));
  assert.match(result.stdout, /nothing to repair \(1 route\(s\), revision 2\)/);
  assert.equal(fs.existsSync(document), false, 'a healthy document is not touched');
});

// A1 in the second review: repair used to treat "the control process started
// but could not answer" as "the document is broken" and overwrite it. Every
// probe failure must abort before the first byte is written.
test('repair changes nothing when the probe cannot answer', (t) => {
  const f = fixture(t);
  const document = settingsDocument(f);
  fs.mkdirSync(path.dirname(document), { recursive: true, mode: 0o700 });
  fs.writeFileSync(document, HEALTHY_DOCUMENT);
  const original = fs.readFileSync(document);

  // A control process that answers `superagent/hello` but only speaks bridge v1.
  const oldBridge = run(f, ['repair'], { env: { FAKE_DSH_MODE: 'old-bridge' } });
  assert.notEqual(oldBridge.status, 0, output(oldBridge));
  assert.match(output(oldBridge), /nothing was changed/);
  assert.match(output(oldBridge), /does not expose the superagent bridge v2/);
  assert.match(output(oldBridge), /cordis\.patch\.yml/);
  assert.ok(fs.readFileSync(document).equals(original), 'the document bytes must be identical');
  assert.deepEqual(backupsIn(path.dirname(document)), []);
  assert.deepEqual(fs.readdirSync(path.dirname(document)).filter((name) => name.endsWith('.tmp')), []);

  // A DSH that starts and exits before answering at all.
  const exiting = path.join(path.dirname(f.script), 'dsh-exit');
  fs.writeFileSync(exiting, '#!/usr/bin/env node\nprocess.exit(0);\n', { mode: 0o755 });
  const before = f.calls().length;
  const crashed = run(f, ['repair'], { env: { DSH_BIN: exiting } });
  assert.notEqual(crashed.status, 0, output(crashed));
  assert.match(output(crashed), /nothing was changed/);
  assert.match(output(crashed), /before answering|before superagent\/hello|EPIPE/);
  assert.ok(fs.readFileSync(document).equals(original), 'the document bytes must be identical');
  assert.deepEqual(backupsIn(path.dirname(document)), []);
  assert.equal(f.calls().length, before, 'the crashed probe never reaches the settings call');

  // A settings refusal that is not about readability must not license a rewrite
  // either: rewriting the file cannot enable a disabled plugin or undo an
  // override.
  const refused = run(f, ['repair'], { env: { FAKE_DSH_MODE: 'describe-refused' } });
  assert.notEqual(refused.status, 0, output(refused));
  assert.match(output(refused), /nothing was changed/);
  assert.match(output(refused), /not configurable/);
  assert.ok(fs.readFileSync(document).equals(original), 'the document bytes must be identical');
  assert.deepEqual(backupsIn(path.dirname(document)), []);
});

// Only this state licenses the rewrite: the probe succeeded and said the entry
// is unreadable. The document then becomes the minimal one, the original bytes
// live on in exactly one backup, and nothing is left half-written.
test('repair rewrites the document only when the probe answers descriptor null', (t) => {
  const f = fixture(t);
  const document = settingsDocument(f);
  fs.mkdirSync(path.dirname(document), { recursive: true, mode: 0o700 });
  // A profile patch the settings service no longer reads: valid YAML, but with
  // no llm-pi-ai entry (the fixture reports `descriptor: null` while it lacks
  // one and a readable entry once repair has written it).
  const original = Buffer.from('# a hand-edited profile patch\n- id: llm-other\n  config: {}\n');
  fs.writeFileSync(document, original);
  const result = run(f, ['repair'], { env: { FAKE_DSH_MODE: 'no-entry-on-corrupt' } });
  assert.equal(result.status, 0, output(result));
  assert.match(result.stdout, /Before repair: the llm-pi-ai entry is not writable/);
  assert.match(result.stdout, /Verified: 0 route\(s\), revision 0/);
  assert.equal(fs.readFileSync(document, 'utf8'), '[{"id":"llm-pi-ai"}]\n');
  assert.equal(fs.statSync(document).mode & 0o777, 0o600);
  const backups = backupsIn(path.dirname(document));
  assert.equal(backups.length, 1, 'exactly one backup of the original');
  assert.ok(fs.readFileSync(path.join(path.dirname(document), backups[0])).equals(original),
    'the backup is byte-identical to the original document');
  assert.deepEqual(fs.readdirSync(path.dirname(document)).filter((name) => name.endsWith('.tmp')), [],
    'the atomic write leaves no temporary file behind');
});

test('repair leaves a readable document byte-identical', (t) => {
  const f = fixture(t, { state: { revision: 4, providers: { deepseek: DEEPSEEK }, credentials: {} } });
  const document = settingsDocument(f);
  fs.mkdirSync(path.dirname(document), { recursive: true, mode: 0o700 });
  fs.writeFileSync(document, HEALTHY_DOCUMENT);
  const result = run(f, ['repair']);
  assert.equal(result.status, 0, output(result));
  assert.match(result.stdout, /nothing to repair \(1 route\(s\), revision 4\)/);
  assert.ok(fs.readFileSync(document).equals(Buffer.from(HEALTHY_DOCUMENT)),
    'an existing readable document is never rewritten');
  assert.deepEqual(backupsIn(path.dirname(document)), []);
});

// The one probe failure that still licenses a rewrite: the settings service
// answered and said the document itself cannot be read. The daemon's
// end-to-end fixture models a malformed patch this way, so repair must keep
// recovering it; the service's closed `data.code` is what separates it from a
// control process that never answered.
test('repair accepts a settings answer that says the document is unreadable', (t) => {
  const f = fixture(t);
  const document = settingsDocument(f);
  fs.mkdirSync(path.dirname(document), { recursive: true, mode: 0o700 });
  const original = Buffer.from('- this: [is not, valid yaml\n');
  fs.writeFileSync(document, original);
  const result = run(f, ['repair'], { env: { FAKE_DSH_MODE: 'unreadable' } });
  assert.equal(result.status, 0, output(result));
  assert.match(result.stdout, /Before repair: llm-pi-ai is not readable: the profile patch is malformed/);
  assert.equal(fs.readFileSync(document, 'utf8'), '[{"id":"llm-pi-ai"}]\n');
  const backups = backupsIn(path.dirname(document));
  assert.equal(backups.length, 1);
  assert.ok(fs.readFileSync(path.join(path.dirname(document), backups[0])).equals(original),
    'the backup is byte-identical to the malformed original');
});

// B9 in the second review: a failed rewrite must not leave a half-written
// document, a stray temporary, or a partial backup. The backup happens before
// the atomic rename, so a directory that refuses new files leaves the original
// document exactly as it was.
test('a failed backup or write leaves the document untouched', (t) => {
  const f = fixture(t);
  const document = settingsDocument(f);
  const directory = path.dirname(document);
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  const original = Buffer.from('- this: [is not, valid yaml\n');
  fs.writeFileSync(document, original);

  // A read-only directory refuses the backup, so the rewrite never starts. The
  // mode is restored before the assertions so the fixture can still clean up.
  fs.chmodSync(directory, 0o500);
  let result;
  try {
    result = run(f, ['repair'], { env: { FAKE_DSH_MODE: 'unreadable' } });
  } finally {
    fs.chmodSync(directory, 0o700);
  }
  assert.notEqual(result.status, 0, output(result));
  assert.match(output(result), /EACCES|EPERM|permission denied/i);
  assert.ok(fs.readFileSync(document).equals(original), 'the document is untouched');
  assert.deepEqual(fs.readdirSync(directory).filter((name) => name.includes('.bak-')), [],
    'no partial backup is left behind');
  assert.deepEqual(fs.readdirSync(directory).filter((name) => name.endsWith('.tmp')), [],
    'no temporary file is left behind');
});

test('a credential whose service has not answered yet is unknown, never missing', (t) => {
  const f = fixture(t, {
    state: {
      revision: 1,
      providers: {
        deepseek: { ...DEEPSEEK, models: [{ id: 'deepseek-chat', name: 'DeepSeek Chat' }] },
      },
      credentials: {},
    },
  });
  const text = run(f, ['list'], { env: { FAKE_DSH_MODE: 'unknown-credential' } });
  assert.equal(text.status, 0, output(text));
  assert.match(text.stdout, /key DEEPSEEK_API_KEY {2}credential state unknown/);
  assert.ok(!text.stdout.includes('credential missing'), 'an unknowable state is not reported as missing');
  assert.ok(!text.stdout.includes('key set'), 'no hint is printed for a state that may be fine');
  const json = run(f, ['list', '--json'], { env: { FAKE_DSH_MODE: 'unknown-credential' } });
  const deepseek = JSON.parse(json.stdout).find((row) => row.provider === 'deepseek');
  assert.deepEqual(deepseek.credential, { ref: 'DEEPSEEK_API_KEY', present: false, known: false });
});

test('structured settings error codes pick the actionable message, not the text', (t) => {
  const f = fixture(t, { state: { providers: { deepseek: DEEPSEEK } } });
  const write = ['remove', 'deepseek/deepseek-chat'];
  const overridden = run(f, write, { env: { FAKE_DSH_MODE: 'overridden-code' } });
  assert.equal(overridden.status, 1);
  assert.match(overridden.stderr, /overrides llm-pi-ai/, 'SETTINGS_OVERRIDDEN wins over a generic message');
  const notConfigurable = run(f, write, { env: { FAKE_DSH_MODE: 'not-configurable-code' } });
  assert.equal(notConfigurable.status, 1);
  assert.match(notConfigurable.stderr, /ov-superagent dsh model repair/);
  const conflicted = run(f, write, { env: { FAKE_DSH_MODE: 'conflict-once-code' } });
  assert.equal(conflicted.status, 0, output(conflicted));
  assert.equal(f.state().providers.deepseek, undefined);
});

// B8: `no longer configurable` is SETTINGS_NOT_CONFIGURABLE on the bridge, a
// state retrying cannot fix. Treating it as a concurrent write spent the one
// retry on it and then reported the wrong cause, so a text-only v2 frame must
// reach the repair instruction without a wasted round trip.
test('a "no longer configurable" text frame is not retried as a concurrent write', (t) => {
  const f = fixture(t, { state: { providers: { deepseek: DEEPSEEK } } });
  const write = ['remove', 'deepseek/deepseek-chat'];
  const before = f.calls().length;
  const result = run(f, write, { env: { FAKE_DSH_MODE: 'no-longer-configurable' } });
  assert.equal(result.status, 1, output(result));
  assert.match(result.stderr, /ov-superagent dsh model repair/, 'the actionable instruction, not a concurrency notice');
  assert.doesNotMatch(result.stderr, /changed after it was read/);
  const mutates = f.calls().slice(before).filter((call) => call.method === 'superagent/settings/mutate');
  assert.equal(mutates.length, 1, 'no retry is spent on a state that cannot change by retrying');
});

test('a concurrent write is still retried once and still reports the conflict', (t) => {
  const f = fixture(t, { state: { providers: { deepseek: DEEPSEEK } } });
  const write = ['remove', 'deepseek/deepseek-chat'];
  const before = f.calls().length;
  const result = run(f, write, { env: { FAKE_DSH_MODE: 'conflict-always' } });
  assert.equal(result.status, 1, output(result));
  assert.match(result.stderr, /changed after it was read and still conflicted on retry/);
  const mutates = f.calls().slice(before).filter((call) => call.method === 'superagent/settings/mutate');
  assert.equal(mutates.length, 2, 'the conflict is retried exactly once');
});

// B-1: Pi lets a model entry name its own protocol and endpoint. DSH serves one
// protocol and one endpoint per route, so a value only the models declare is
// the route's value; dropping it migrated a private gateway with no endpoint.
test('import carries a model-level endpoint onto a route that declares none', (t) => {
  const f = fixture(t);
  const models = path.join(f.home, 'models.json');
  fs.writeFileSync(models, JSON.stringify({ providers: {
    gateway: { models: [
      { id: 'private-large', api: 'anthropic-messages', baseUrl: 'http://127.0.0.1:8080/anthropic' },
      { id: 'private-small' },
    ] },
  } }));
  const result = run(f, ['import', models]);
  assert.equal(result.status, 0, output(result));
  const profile = f.state().providers.gateway;
  assert.equal(profile.api, 'anthropic-messages', 'the protocol only the model declared is kept');
  assert.equal(profile.baseURL, 'http://127.0.0.1:8080/anthropic', 'the private gateway address is kept');
  assert.deepEqual(profile.models.map((model) => model.id), ['private-large', 'private-small']);
  assert.match(result.stdout, /taken from the model entries/, 'the promotion is reported, not silent');
});

// P1-3: two models on different endpoints are two routes, never one route
// re-pointed at the first model's provider. The second review showed the old
// behaviour dropped the second model instead of migrating it, and — worse — let
// a model that named no endpoint inherit whichever address was declared first.
test('import never merges two model-level endpoints into one route silently', (t) => {
  const f = fixture(t);
  const models = path.join(f.home, 'models.json');
  fs.writeFileSync(models, JSON.stringify({ providers: {
    split: { api: 'openai-completions', models: [
      { id: 'first', baseUrl: 'http://one.test/v1' },
      { id: 'second', baseUrl: 'http://two.test/v1' },
    ] },
  } }));
  const result = run(f, ['import', models]);
  assert.equal(result.status, 0, output(result));
  const state = f.state();
  assert.deepEqual(Object.keys(state.providers).sort(), ['split', 'split--2']);
  assert.equal(state.providers.split.baseURL, 'http://one.test/v1', 'one endpoint per route');
  assert.deepEqual(state.providers.split.models.map((model) => model.id), ['first']);
  assert.equal(state.providers['split--2'].baseURL, 'http://two.test/v1');
  assert.deepEqual(state.providers['split--2'].models.map((model) => model.id), ['second'],
    'the model on another endpoint gets its own route instead of being dropped or re-pointed');
  assert.match(result.stdout, /split into 2 routes/, 'the split is reported, not silent');
});

// B-6: `$ARK_API_KEY` is stripped from a DSH child by the daemon's sanitizer,
// so a route that names it can only resolve from the credential service. The
// migration has to say so instead of writing a reference that cannot resolve.
test('import warns about a credential the DSH child environment withholds', (t) => {
  const f = fixture(t);
  const models = path.join(f.home, 'models.json');
  fs.writeFileSync(models, JSON.stringify({ providers: {
    ark: { api: 'openai-completions', baseUrl: 'http://ark.test/v1', apiKey: '$ARK_API_KEY', models: [{ id: 'ep-1' }] },
    volc: { api: 'openai-completions', baseUrl: 'http://volc.test/v1', apiKey: '$VOLCENGINE_KEY', models: [{ id: 'ep-2' }] },
    openai: { api: 'openai-completions', baseUrl: 'http://oai.test/v1', apiKey: '$OPENAI_API_KEY', models: [{ id: 'gpt' }] },
  } }));
  const result = run(f, ['import', models]);
  assert.equal(result.status, 0, output(result));
  const state = f.state();
  assert.equal(state.providers.ark.apiKeyEnv, 'ARK_API_KEY');
  assert.equal(state.providers.volc.apiKeyEnv, 'VOLCENGINE_KEY');
  assert.equal(state.providers.openai.apiKeyEnv, 'OPENAI_API_KEY', 'a variable the child inherits is kept silently');
  assert.match(result.stdout, /ARK_API_KEY/, 'the withheld reference is named');
  assert.match(result.stdout, /withhold/, 'the note says why it will not resolve');
  assert.match(result.stdout, /ov-superagent dsh model key set ARK_API_KEY/, 'the note says how to store the value');
  assert.ok(!output(result).includes('sk-'), 'a migration never prints a value');
});

// I2: the values are written before the routes, each under a reference this run
// allocated. A refused value therefore leaves no route written — a complete
// no-op — and the references already created are taken back out again.
test('import reports what landed when the credentials service refuses a value', (t) => {
  const f = fixture(t);
  const models = path.join(f.home, 'models.json');
  fs.writeFileSync(models, JSON.stringify({ providers: {
    first: { api: 'openai-completions', baseUrl: 'http://a.test/v1', apiKey: 'sk-first-secret', models: [{ id: 'm1' }] },
    second: { api: 'openai-completions', baseUrl: 'http://b.test/v1', apiKey: 'sk-second-secret', models: [{ id: 'm2' }] },
  } }));
  const result = run(f, ['import', models], {
    env: { FAKE_DSH_MODE: 'credential-refused', FAKE_DSH_REFUSE_REF: 'SECOND_API_KEY' },
  });
  assert.notEqual(result.status, 0, output(result));
  assert.deepEqual(f.state().providers, {}, 'no route is written when a credential is refused');
  assert.deepEqual(f.state().credentials, {}, 'a refused import leaves no credential behind either');
  const text = output(result);
  assert.match(text, /SECOND_API_KEY/, 'the refused reference is named');
  assert.match(text, /No route was written/, 'the partial state is stated, not implied');
  assert.match(text, /references this run created were removed \(FIRST_API_KEY\)/, 'the rollback is named');
  assert.match(text, /in-use configuration was not changed/i, 'the invariant is stated');
  assert.ok(!text.includes('sk-first-secret') && !text.includes('sk-second-secret'), 'values are never printed');
});

// B-2: SETTINGS_OVERRIDDEN means a layer above the route document also defines
// llm-pi-ai. With the real dsh that layer is the home patch
// (<DSH_HOME>/cordis.patch.yml); the route document is what the command writes,
// so pointing the user there would delete every route and fix nothing.
test('an override refusal points at the home patch, not at the route document', (t) => {
  const f = fixture(t);
  const dataDir = f.agent('primary').dataDir;
  const homePatch = path.join(dataDir, 'dsh', 'home', 'cordis.patch.yml');
  fs.mkdirSync(path.dirname(homePatch), { recursive: true, mode: 0o700 });
  fs.writeFileSync(homePatch, '- id: llm-pi-ai\n  config:\n    providers: {}\n');
  f.writeState({ revision: 0, providers: { deepseek: DEEPSEEK }, credentials: {}, notReady: 0 });
  const result = run(f, ['remove', 'deepseek/deepseek-chat'], {
    env: { FAKE_DSH_MODE: 'overridden' },
  });
  assert.equal(result.status, 1, output(result));
  assert.ok(result.stderr.includes(homePatch), `the home patch is named: ${result.stderr}`);
  assert.doesNotMatch(result.stderr, /Remove the llm-pi-ai entry from .*profiles\/sdk\/cordis\.patch\.yml/,
    'the route document is not offered as the thing to delete');
  assert.match(result.stderr, /--patch|control-patch/, 'a launch overlay is named as the other possible source');
});

// The shared model-id contract (brief §4): the CLI validates the model half of
// `<route>/<model>` with the same grammar the daemon uses, which admits `@`.
test('the model id is validated with the shared grammar', () => {
  // The wizard validates the id it is given with this helper; the flags are
  // gone, so the rule is asserted where it lives.
  // A control character is what the shared grammar refuses; a space is a legal
  // id for a real dsh, so OpenViking SuperAgent must not invent a narrower rule.
  assert.match(modelIdProblem('deepseek', 'model\x01'), /Invalid model name/);
  assert.equal(modelIdProblem('deepseek', 'model@2026-01'), null);
  // A space is served by DSH but stays out of OpenViking SuperAgent's grammar: the id is refused
  // and the message says why, rather than writing an id `/model` could never
  // select.
  assert.match(modelIdProblem('deepseek', 'has space'), /Invalid model name/);
});

// --- C(2): manifest DSH_BIN, complete-id bound, removed-env notice ----------

// A stand-in that records which executable ran and then exits, so a test can
// prove *which* binary the control process resolved without speaking the
// protocol. `process.argv[1]` is the path actually executed.
const RECORDING_DSH = `#!/usr/bin/env node
import fs from 'node:fs';
fs.appendFileSync(process.env.P21_MARKER, JSON.stringify({
  self: fs.realpathSync(process.argv[1]),
  argv: process.argv.slice(2),
  codexBin: process.env.CODEX_BIN ?? null,
}) + '\\n');
process.exit(3);
`;

function recordingFake(directory, name) {
  const script = path.join(directory, name);
  fs.writeFileSync(script, RECORDING_DSH, { mode: 0o755 });
  return fs.realpathSync(script);
}

// P2-1: `DSH_BIN` is the one DSH key the deployment manifest carries
// (deployment-config.cjs DAEMON_ENV_FIELDS), so a control process must resolve
// it from there when the operator environment names none — that is what the
// daemon does (`resolvedDeploymentDaemonEnv`: shell value first, manifest
// second). Nothing else from `deployment.env` is merged into the child: the
// daemon never applies it, so a second source here would edit a document with a
// binary the instance does not run.
test('the control process runs the manifest DSH_BIN when the environment has none', (t) => {
  const f = fixture(t);
  const marker = path.join(f.home, 'p21-marker.jsonl');
  const manifestFake = recordingFake(f.home, 'manifest-dsh');
  const pathDir = path.join(f.home, 'path-bin');
  fs.mkdirSync(pathDir, { mode: 0o700 });
  const pathFake = recordingFake(pathDir, 'dsh');
  const manifest = JSON.parse(fs.readFileSync(f.config, 'utf8'));
  manifest.env = { DSH_BIN: manifestFake, CODEX_BIN: '/opt/manifest-only/codex' };
  fs.writeFileSync(f.config, `${JSON.stringify(manifest, null, 2)}\n`);
  // The environment names no DSH binary at all (spawn drops `undefined`); PATH
  // offers a different one.
  const result = run(f, ['list'], {
    env: {
      DSH_BIN: undefined,
      PATH: `${pathDir}${path.delimiter}${process.env.PATH ?? ''}`,
      P21_MARKER: marker,
    },
  });
  const records = readJsonLines(marker);
  assert.ok(records.length > 0, `the manifest binary must be executed: ${output(result)}`);
  assert.equal(records[0].self, manifestFake, 'the manifest DSH_BIN is the executable');
  assert.ok(!records.some((record) => record.self === pathFake),
    'the dsh found on PATH is not used');
  assert.notEqual(records[0].codexBin, '/opt/manifest-only/codex',
    'only DSH_BIN is taken from the manifest, not the whole manifest env');
});

// The precedence the daemon applies: an exported value wins over the manifest.
test('an exported DSH_BIN wins over the manifest one', (t) => {
  const f = fixture(t);
  const marker = path.join(f.home, 'p21-marker.jsonl');
  const envFake = recordingFake(f.home, 'env-dsh');
  const manifestFake = recordingFake(f.home, 'manifest-dsh');
  const manifest = JSON.parse(fs.readFileSync(f.config, 'utf8'));
  manifest.env = { DSH_BIN: manifestFake };
  fs.writeFileSync(f.config, `${JSON.stringify(manifest, null, 2)}\n`);
  const result = run(f, ['list'], { env: { DSH_BIN: envFake, P21_MARKER: marker } });
  const records = readJsonLines(marker);
  assert.ok(records.length > 0, `the exported binary must be executed: ${output(result)}`);
  assert.equal(records[0].self, envFake, 'the operator environment wins');
  assert.ok(!records.some((record) => record.self === manifestFake),
    'the manifest binary is only a fallback');
});

// P2-4: the length bound is the one in the shared grammar, and `/model` applies
// it to the *complete* `<route>/<model>` argument (commands.mjs tests
// `command.arg`). The wizard validates the same complete string, so measuring
// only the model half would accept an id that `/model` then refused.
test('the model-id bound applies to the complete route/model id', async (t) => {
  const { MODEL_ID_PATTERN } = await import('../../daemon/src/runtime-catalog.mjs');
  const bound = Number(/\{1,(\d+)\}/.exec(MODEL_ID_PATTERN.source)?.[1]);
  assert.equal(bound, 256, 'the shared grammar is the single source of the bound');
  const route = 'deepseek';
  const idOfLength = (length) => `m${'x'.repeat(length - 1)}`;

  // A complete id of exactly `bound` characters is the last legal one.
  const exactId = idOfLength(bound - route.length - 1);
  assert.equal(`${route}/${exactId}`.length, bound);
  assert.equal(modelIdProblem(route, exactId), null);

  // One character more is refused, and the message states the bound.
  const overId = idOfLength(bound - route.length);
  assert.equal(`${route}/${overId}`.length, bound + 1);
  assert.match(modelIdProblem(route, overId), new RegExp(String(bound)));

  // The review's case: a 256-character model half is legal on its own, but with
  // a route in front the complete id is 263 characters and cannot be selected.
  const longHalf = idOfLength(256);
  assert.equal(MODEL_ID_PATTERN.test(longHalf), true, 'the model half alone matches');
  assert.match(modelIdProblem(route, longHalf), /263|256/);

  // The model half may contain slashes; the bound counts the whole id.
  assert.equal(modelIdProblem(route, 'deepseek-chat'), null);
  assert.match(modelIdProblem(route, `group/${idOfLength(252)}`), new RegExp(String(bound)));
  assert.deepEqual(parseModelId('deepseek/deepseek-chat'), { route: 'deepseek', model: 'deepseek-chat' });
});

// The measured gap: a real DSH 0.2.2-rc.1 lists and selects `m+v1`, so the
// shared grammar admits it; the wizard validates with that same grammar.
test('the shared grammar admits the "+" a real DSH serves', () => {
  assert.equal(modelIdProblem('deepseek', 'm+v1'), null);
  assert.deepEqual(parseModelId('deepseek/m+v1'), { route: 'deepseek', model: 'm+v1' });
  assert.deepEqual(
    applyOps({}, addModelOps({}, { ...baseSpec, route: 'deepseek', model: 'm+v1' })).deepseek.models.map((model) => model.id),
    ['m+v1'],
  );
});

test('an existing id outside the grammar survives list and import untouched', (t) => {
  // The rule governs what may be WRITTEN and SELECTED. An id already in the
  // settings document -- written by DSH, an older build, or hand-edited -- must
  // keep its bytes: refusing to select it is not a reason to delete it.
  const f = fixture(t, { state: { providers: {
    legacy: {
      api: 'openai-completions',
      baseURL: 'http://legacy.test/v1',
      apiKeyEnv: 'LEGACY_API_KEY',
      models: [{ id: 'has space' }, { id: 'c#d' }, { id: 'plain' }],
    },
  } } });

  const listed = run(f, ['list', '--json']);
  assert.equal(listed.status, 0, output(listed));
  const rows = JSON.parse(listed.stdout);
  const ids = rows.find((row) => row.provider === 'legacy').models.map((model) => model.id);
  assert.deepEqual(ids, ['has space', 'c#d', 'plain'], 'list reports them as they are');

  // A migration that does not mention this route must not touch it either.
  const modelsJson = path.join(f.home, 'models.json');
  fs.writeFileSync(modelsJson, JSON.stringify({ providers: { other: {
    api: 'openai-completions', baseUrl: 'http://other.test/v1', apiKey: 'sk-other',
    models: [{ id: 'fresh' }],
  } } }));
  const imported = run(f, ['import', modelsJson]);
  assert.equal(imported.status, 0, output(imported));
  assert.deepEqual(
    f.state().providers.legacy.models.map((model) => model.id),
    ['has space', 'c#d', 'plain'],
    'the untouched route keeps every id, including the ones /model cannot select',
  );
});

// The interactive write applies the migration's ordering to the path operators
// actually use: the value lands before the route that names it, so a refused
// value leaves no route pointing at a reference with nothing behind it.
test('writeModelDraft stores the value before it writes the route', async (t) => {
  const f = fixture(t);
  const dataDir = f.agent().dataDir;
  fs.mkdirSync(path.join(dataDir, 'dsh', 'home'), { recursive: true, mode: 0o700 });
  const { writeModelDraft } = await import('../src/dsh-model.mjs');
  const draft = {
    route: 'newroute', model: 'm1', api: 'openai-completions',
    baseURL: 'http://new.test/v1', name: 'New', keyEnv: null, keyValue: 'sk-refused-value',
  };
  await assert.rejects(
    writeModelDraft({
      dataDir, draft, bin: f.script, env: { ...f.env, FAKE_DSH_MODE: 'credential-refused', FAKE_DSH_REFUSE_REF: 'NEWROUTE_API_KEY' },
    }),
    /refused NEWROUTE_API_KEY/,
  );
  // The refusal happened before the route write, so the document is untouched.
  assert.deepEqual(f.state().providers, {}, 'no route is written when the value is refused');
  assert.deepEqual(f.state().credentials, {}, 'and no value is left behind');
  // The order is observable in the request log, not only in the outcome: the
  // value was attempted and the route write never was.
  const methods = f.calls().map((entry) => entry.method).filter(Boolean);
  assert.ok(methods.includes('superagent/credentials/set'), `the value is attempted: ${methods.join(', ')}`);
  assert.ok(!methods.includes('superagent/settings/mutate'),
    `the route write never happens after a refused value: ${methods.join(', ')}`);
});

// The probe answers `descriptor: null` both for a document it cannot read and for
// an entry an upper layer disabled, and the rewrite only helps the first. Refusing
// the second keeps its routes intact instead of destroying them for nothing.
test('repair refuses to rewrite when an upper layer owns llm-pi-ai', (t) => {
  // Only the home patch: the launch overlay is `control-patch.yml`, which this very
  // command rewrites before spawning the control process, so a test cannot plant
  // an entry there and observe it.
  for (const [kind, layerFile] of [
    ['home patch', (f) => path.join(f.agent().dataDir, 'dsh', 'home', 'cordis.patch.yml')],
  ]) {
    const f = fixture(t);
    const document = settingsDocument(f);
    fs.mkdirSync(path.dirname(document), { recursive: true, mode: 0o700 });
    const original = Buffer.from(`${JSON.stringify([{ id: 'llm-pi-ai', config: { providers: {
      kept: { api: 'openai-completions', baseURL: 'http://x/v1', apiKeyEnv: 'KEPT_KEY', models: [{ id: 'm1' }] },
    } } }])}\n`);
    fs.writeFileSync(document, original);
    // The layer that takes llm-pi-ai over. Without the guard, repair would rewrite
    // the document to the minimal one and drop `kept`.
    const layer = layerFile(f);
    fs.mkdirSync(path.dirname(layer), { recursive: true, mode: 0o700 });
    fs.writeFileSync(layer, `${JSON.stringify([{ id: 'llm-pi-ai', disabled: true }])}\n`);

    // `no-entry` reports `descriptor: null` unconditionally, which is exactly the
    // state the guard exists for: the probe cannot tell this apart from a corrupt
    // document.
    const result = run(f, ['repair'], { env: { FAKE_DSH_MODE: 'no-entry' } });
    assert.notEqual(result.status, 0, `${kind}: repair must refuse`);
    assert.match(output(result), new RegExp(kind), `${kind}: the layer is named`);
    assert.match(output(result), /Nothing was changed|nothing was changed/, `${kind}: the outcome is stated`);
    assert.ok(fs.readFileSync(document).equals(original), `${kind}: the document is untouched`);
    assert.deepEqual(backupsIn(path.dirname(document)), [], `${kind}: no backup, because nothing was rewritten`);
  }
});

test('repair never under-counts the routes it is about to discard', (t) => {
  // The count is printed immediately before the one destructive step in the
  // command group. A shape this reader cannot scan must fall back to the byte
  // count, never to "0 routes": a rewrite that drops three after announcing none
  // is worse than one that names no number.
  const cases = [
    // [document, expected fragment]
    ['- id: llm-pi-ai\n  config:\n    providers: {alpha: {}, beta: {}}\n', /74|bytes|不计数|did not|无法识别|its shape/],
    ['- id: llm-pi-ai\n  config:\n    providers:\n      alpha:\n        api: x\n      beta:\n        api: y\n', /2 个 route|2 route/],
    ['- id: llm-pi-ai\n  config:\n    providers: {}\n', /0 个 route|0 route/],
  ];
  for (const [document, pattern] of cases) {
    const f = fixture(t);
    const file = settingsDocument(f);
    fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
    fs.writeFileSync(file, document);
    const result = run(f, ['repair'], { env: { FAKE_DSH_MODE: 'no-entry' } });
    const text = output(result);
    assert.match(text, pattern, `unexpected count line for ${JSON.stringify(document)}: ${text}`);
    assert.doesNotMatch(text, /将丢弃 0 个 route[\s\S]*providers: \{alpha/,
      'a flow-style document must not be reported as zero routes');
  }
});

test('a mistyped `key set REF=secret` never puts the secret on stderr', (t) => {
  // `key set REF=value` is the natural mistake, and it puts the value in the
  // reference position. Quoting the token back would print the secret.
  const f = fixture(t);
  const result = run(f, ['key', 'set', 'MY_KEY=sk-live-SECRET'], { input: '' });
  assert.equal(result.status, 2, output(result));
  assert.doesNotMatch(output(result), /sk-live-SECRET/, 'the secret must not be echoed');
  assert.match(output(result), /MY_KEY=/, 'the part before the `=` is still shown');
});

test('addModelOps refuses the shapes the prompts would have refused', (t) => {
  // The write seam, not only the wizard: `writeModelDraft` and setup both reach
  // it, so a route the CLI could no longer address must not get written.
  const base = {
    api: 'openai-completions', baseURL: 'http://127.0.0.1:9/v1', name: null,
    keyEnv: null, input: null, contextWindow: null, maxTokens: null, noReasoning: false,
  };
  const rejected = [
    [{ ...base, route: 'has space', model: 'm' }, /route (id|name)/],
    [{ ...base, route: 'a/b', model: 'm' }, /route (id|name)/],
    [{ ...base, route: 'r', model: 'has space' }, /model (id|name)/],
    [{ ...base, route: 'r', model: 'm', baseURL: 'ftp://127.0.0.1:9/v1' }, /http\(s\)/],
    [{ ...base, route: 'r', model: 'm', keyEnv: 'BAD KEY' }, /credential reference/],
  ];
  for (const [spec, pattern] of rejected) {
    assert.throws(() => addModelOps({}, spec), pattern, `should refuse ${spec.route}/${spec.model}`);
  }
  // The legitimate shape still writes.
  const ops = addModelOps({}, { ...base, route: 'ok', model: 'm' });
  assert.equal(ops[0].path[1], 'ok');
  assert.deepEqual(ops[0].value.models.map((entry) => entry.id), ['m']);
});

test('the usage text warns that an interrupted import can leave an orphan credential', (t) => {
  // Values are written before routes, which is what makes a crash safe; the cost
  // is that an interrupted import can leave a value nothing references, and `list`
  // only shows the ones routes use. Saying so is the whole remedy.
  const f = fixture(t);
  const result = run(f, ['--help']);
  const text = output(result);
  assert.match(text, /interrupted|打断/);
  assert.match(text, /key unset/);
});
