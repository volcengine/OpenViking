// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
// Bridge v2 against a real dsh child process.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { after, before, describe, test } from 'node:test';
import {
  DSH_BRIDGE_CLI_METHODS, DSH_WORKER_METHODS, DSH_BRIDGE_ID, DSH_BRIDGE_VERSION, DSH_NOT_READY_CODE,
  bridgeHelloSupports, isDshNotReady, retryDshNotReady,
} from '../src/dsh-runtime.mjs';
import {
  cleanupLiveDshRoot, credentialsPath, dshHomePath, hermeticDshEnv, prepareLiveDshRoot,
  profilePatchPath, realDshAvailability, startLiveDsh,
} from './real-dsh.mjs';
import { startFakeProvider } from './fake-provider.mjs';

// End-to-end coverage of bridge v2 against a real `dsh` child process.
//
// These tests are the only place where the wire contract (method names, reply
// shapes, NOT_READY timing, and the files DSH writes) is checked against the
// installed product rather than a stub. A machine without `dsh`, or with a
// version superagent does not drive, reports the live suites as skipped instead of
// failing: the bridge contract is then covered only by dsh-bridge.test.mjs.
//
// The fixture is hermetic: a 0700 temp dataDir, a temp HOME (so neither
// ~/.dsh nor ~/.ov-superagent is touched), operator DEEPSEEK_*/DSH_* removed, and a
// local fake provider as the only reachable endpoint.

const availability = await realDshAvailability();
const liveSkip = availability.ok ? false : availability.reason;
// A model-level assertion the catalogue cannot answer without the account
// service: off/low/high/max is what the adapter advertises for the default
// route, so a change there is a contract change, not a fixture detail.
const DEEPSEEK_FLASH_EFFORTS = ['off', 'low', 'high', 'max'];

// `provider/baseURL` for a route the test declares through settings; the model
// list is catalog-driven, so adding it never requires a live endpoint.
function deepseekProfile(baseURL, { reasoningEfforts = { low: 'low', high: 'high', max: 'max' } } = {}) {
  return {
    api: 'openai-completions',
    baseURL,
    apiKeyEnv: 'DEEPSEEK_API_KEY',
    displayName: 'DeepSeek',
    models: [{
      id: 'deepseek-chat',
      name: 'DeepSeek Chat',
      input: ['text', 'image'],
      reasoningEfforts,
      contextWindow: 1_000_000,
      maxTokens: 384_000,
    }],
  };
}

function bridgeError(frameError) {
  const error = new Error(frameError.message);
  error.code = frameError.code;
  error.data = frameError.data;
  return error;
}

function entryOf(models, provider) {
  return models.providers.find((entry) => entry.provider === provider);
}

test('the reusable fake provider answers OpenAI completions over SSE', async (t) => {
  const provider = await startFakeProvider({ reply: 'streamed reply' });
  t.after(() => provider.close());
  const response = await fetch(`${provider.baseURL}/chat/completions`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: 'Bearer test' },
    body: JSON.stringify({ model: 'fake-model', stream: true, messages: [{ role: 'user', content: 'hi' }] }),
  });
  assert.equal(response.status, 200);
  assert.match(response.headers.get('content-type') ?? '', /text\/event-stream/);
  const text = await response.text();
  assert.match(text, /data: \[DONE\]/);
  assert.match(text, /"role":"assistant"/);
  assert.match(text, /"finish_reason":"stop"/);
  const deltas = text.split('\n\n')
    .filter((frame) => frame.startsWith('data: ') && frame !== 'data: [DONE]')
    .map((frame) => JSON.parse(frame.slice('data: '.length)))
    .flatMap((frame) => frame.choices?.map((choice) => choice.delta?.content ?? '') ?? []);
  assert.equal(deltas.join(''), 'streamed reply');
  assert.equal(provider.requests.length, 1);
  assert.equal(provider.requests[0].body.model, 'fake-model');
  const listing = await fetch(`${provider.baseURL}/models`);
  assert.deepEqual((await listing.json()).data.map((model) => model.id), ['fake-model']);
});

describe('live dsh bridge v2 without a provider key', { skip: liveSkip }, () => {
  /** @type {ReturnType<typeof prepareLiveDshRoot>} */
  let root;
  /** @type {Awaited<ReturnType<typeof startLiveDsh>>} */
  let dsh;
  /** @type {Awaited<ReturnType<typeof startFakeProvider>>} */
  let provider;
  let coldStart;
  let sessionId;
  const coldRef = 'LIVE_COLD_REF';

  before(async () => {
    root = prepareLiveDshRoot({ prefix: 'superagent-dsh-live-' });
    provider = await startFakeProvider({ reply: 'live fixture reply' });
    dsh = await startLiveDsh({
      root,
      env: hermeticDshEnv({ root, baseURL: provider.baseURL }),
      policy: 'superagent live bridge fixture',
    });
    // The very first frame races the boot: services are resolved per request,
    // and `credentials` is the one that mounts late, so this frame is the only
    // reliable window on the documented NOT_READY contract.
    coldStart = dsh.rpcRaw('superagent/credentials/set', { ref: coldRef, value: 'cold-start-value' });
    await dsh.initialize();
    sessionId = randomUUID();
    await dsh.rpc('session/working-directory/get', { sessionId });
  });

  after(async () => {
    // A patch the loader could not apply means the fixture is not measuring
    // the bridge the product ships; fail the hook instead of hiding it.
    const stderr = dsh?.stderr() ?? '';
    await Promise.allSettled([dsh?.stop(), provider?.close()]);
    cleanupLiveDshRoot(root?.root);
    assert.doesNotMatch(stderr, /did not activate|unmatched|not matched/i, `dsh stderr: ${stderr}`);
  });

  test('initialize succeeds and the bridge advertises version 2 with every v2 method', async () => {
    const hello = await dsh.hello();
    assert.equal(hello.bridge, DSH_BRIDGE_ID);
    assert.equal(hello.version, DSH_BRIDGE_VERSION);
    assert.ok(Array.isArray(hello.methods));
    assert.equal(new Set(hello.methods).size, hello.methods.length, 'method names are unique');
    assert.deepEqual([...hello.methods].sort(), [...DSH_BRIDGE_CLI_METHODS, ...DSH_WORKER_METHODS].sort());
    assert.equal(hello.methods.length, 9);
    assert.ok(bridgeHelloSupports(hello, DSH_BRIDGE_CLI_METHODS));
    for (const name of ['superagent/settings/describe', 'superagent/settings/mutate', 'superagent/credentials/set', 'superagent/credentials/unset']) {
      assert.ok(hello.methods.includes(name), `hello must advertise ${name}`);
    }
  });

  test('a cold start answers NOT_READY and retryDshNotReady recovers', async (t) => {
    const first = await coldStart;
    if (first.error) {
      assert.equal(first.error.code, DSH_NOT_READY_CODE);
      assert.deepEqual(first.error.data, { code: 'NOT_READY', service: 'credentials' });
      assert.ok(isDshNotReady(bridgeError(first.error)));
      const value = await retryDshNotReady(
        () => dsh.rpc('superagent/credentials/set', { ref: coldRef, value: 'cold-start-value' }),
        { ceilingMs: 15_000 },
      );
      assert.deepEqual(value, { ok: true });
      return;
    }
    // The activation window is short; when the first frame happens to land
    // after it, the NOT_READY assertion has nothing to observe.
    t.skip('the credentials service was already mounted when the first frame arrived');
  });

  test('retryDshNotReady never retries a validation error', async () => {
    let sleeps = 0;
    await assert.rejects(
      () => retryDshNotReady(
        () => dsh.rpc('superagent/credentials/set', { ref: 'not a ref', value: 'do-not-log-this' }),
        { sleep: async () => { sleeps += 1; } },
      ),
      (error) => error.code === -32602 && /POSIX shell identifier/.test(error.message),
    );
    assert.equal(sleeps, 0, 'a non-NOT_READY error is rethrown on the first attempt');
  });

  test('the keyless catalogue lists the built-in routes with no models and no credential', async () => {
    const models = await dsh.rpc('superagent/llm/models');
    assert.ok(Array.isArray(models.providers));
    const official = entryOf(models, 'deepseek-official');
    const account = entryOf(models, 'deepseek-account');
    assert.ok(official && account, `expected both built-in routes: ${JSON.stringify(models)}`);
    assert.equal(official.name, 'DeepSeek');
    assert.equal(account.name, 'DeepSeek Account');
    // Without an API key the official route discovers nothing, and the account
    // route is not signed in: both are empty, which is what the daemon filters
    // on before offering them in the /model menu.
    assert.deepEqual(official.models, []);
    assert.deepEqual(account.models, []);
    assert.equal(official.credential, null, 'built-in routes declare no settings reference');
    assert.equal(account.credential, null);
    assert.equal(entryOf(models, 'deepseek'), undefined);
    assert.equal(provider.requests.length, 0, 'listing a catalogue must not call the provider');
  });

  test('settings describe exposes the llm-pi-ai document only', async () => {
    const described = await dsh.rpc('superagent/settings/describe', { ns: 'llm-pi-ai' });
    assert.equal(described.ns, 'llm-pi-ai');
    assert.ok(described.descriptor, 'the settings service must describe llm-pi-ai');
    assert.equal(described.descriptor.ns, 'llm-pi-ai');
    assert.equal(described.descriptor.revision, 0, 'a fresh profile patch has no edits');
    assert.deepEqual(described.descriptor.value.providers, {});
    assert.equal(typeof described.descriptor.schema, 'object');
    assert.deepEqual(described.descriptor.secrets, []);

    const rejected = await dsh.rpcRaw('superagent/settings/describe', { ns: 'llm' });
    assert.equal(rejected.error?.code, -32602);
    assert.match(rejected.error.message, /namespace/);
  });

  test('model info reports the efforts, modalities and context the adapter supports', async () => {
    const info = await dsh.rpc('superagent/llm/model/info', { provider: 'deepseek-official', model: 'deepseek-flash' });
    assert.equal(info.provider, 'deepseek-official');
    assert.equal(info.id, 'deepseek-flash');
    assert.equal(typeof info.name, 'string');
    assert.deepEqual(info.inputModalities, ['text', 'image']);
    assert.ok(info.context.contextWindow > 0);
    assert.deepEqual(info.reasoning.efforts.map((effort) => effort.id), DEEPSEEK_FLASH_EFFORTS);
    assert.equal(info.reasoning.defaultEffort, 'high');

    const rejected = await dsh.rpcRaw('superagent/llm/model/info', { provider: '', model: 'deepseek-flash' });
    assert.equal(rejected.error?.code, -32602);
  });

  test('select applies a model and rejects an unsupported effort without changing the selection', async () => {
    const applied = await dsh.rpc('superagent/session/model/select', {
      sessionId, provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: 'high',
    });
    assert.deepEqual(applied, { provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: 'high' });

    // Omitting the effort records the request as made — the bridge must not
    // materialize an adapter default and store it as the caller's choice.
    const omitted = await dsh.rpc('superagent/session/model/select', {
      sessionId, provider: 'deepseek-official', model: 'deepseek-flash',
    });
    assert.deepEqual(omitted, { provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: null });

    const rejected = await dsh.rpcRaw('superagent/session/model/select', {
      sessionId, provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: 'xhigh',
    });
    // JSON-RPC keeps -32603; the typed failure class rides in `data.code`, so a
    // caller routes on the class instead of matching message text.
    assert.equal(rejected.error?.code, -32603);
    assert.equal(rejected.error?.data?.code, 'UNSUPPORTED_REASONING_EFFORT', JSON.stringify(rejected.error));
    assert.match(rejected.error.message, /does not support reasoning effort "xhigh"/);

    // The rejected request must not have moved the session: a valid select
    // still resolves and reports its own value.
    const after = await dsh.rpc('superagent/session/model/select', {
      sessionId, provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: 'max',
    });
    assert.equal(after.reasoningEffort, 'max');

    const unknown = await dsh.rpcRaw('superagent/session/model/select', { sessionId, provider: 'nope', model: 'm' });
    assert.equal(unknown.error?.code, -32603);
    assert.equal(unknown.error?.data?.code, 'NO_ADAPTER', JSON.stringify(unknown.error));
    assert.match(unknown.error?.message ?? '', /no adapter registered|unknown|not found/i);
    await dsh.rpc('superagent/session/model/select', {
      sessionId, provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: 'high',
    });
  });

  test('settings mutate adds a route and the catalogue reports it as uncredentialed', async () => {
    const profile = deepseekProfile(provider.baseURL);
    const mutated = await dsh.rpc('superagent/settings/mutate', {
      ns: 'llm-pi-ai', ops: [{ op: 'set', path: ['providers', 'deepseek'], value: profile }],
    });
    assert.deepEqual(mutated, { ok: true });

    const models = await dsh.rpc('superagent/llm/models');
    const deepseek = entryOf(models, 'deepseek');
    assert.ok(deepseek, `the new route must be listed: ${JSON.stringify(models)}`);
    assert.equal(deepseek.name, 'DeepSeek');
    assert.deepEqual(deepseek.models, [], 'a settings route lists no model until its credential is present');
    // Three-state: `present` is the answer, `known` says the credentials
    // service answered at all (a not-yet-mounted service reports known:false
    // rather than a misleading absence).
    assert.deepEqual(deepseek.credential, { ref: 'DEEPSEEK_API_KEY', present: false, known: true });
    assert.equal(deepseek.error, null);
    assert.equal(provider.requests.length, 0, 'listing a declared route must not call its endpoint');

    // The settings service — not superagent — owns this file: it is the single
    // model-configuration source the design names.
    const patchFile = profilePatchPath(root.root);
    assert.ok(fs.existsSync(patchFile), `${patchFile} must exist after mutate`);
    const patchText = fs.readFileSync(patchFile, 'utf8');
    assert.match(patchText, /llm-pi-ai/);
    assert.match(patchText, /deepseek/);
    assert.match(patchText, /DEEPSEEK_API_KEY/);
    // The run patch must not overlay the namespace it writes through.
    const runPatch = fs.readFileSync(path.join(root.root, 'dsh', 'run', 'patch.yml'), 'utf8');
    assert.doesNotMatch(runPatch, /llm-pi-ai/);
    assert.equal(fs.existsSync(path.join(dshHomePath(root.root), 'models.json')), false,
      'there must be no second model-configuration channel');

    const described = await dsh.rpc('superagent/settings/describe', { ns: 'llm-pi-ai' });
    assert.equal(described.descriptor.revision, 1);
    const stored = described.descriptor.value.providers.deepseek;
    assert.equal(stored.apiKeyEnv, 'DEEPSEEK_API_KEY');
    assert.equal(stored.api, 'openai-completions');
    assert.equal(stored.baseURL, provider.baseURL);
    assert.equal(stored.models[0].id, 'deepseek-chat');
    assert.deepEqual(stored.models[0].input, ['text', 'image']);
    // The user layer is the edit itself, not the schema defaults the value has.
    assert.equal(described.descriptor.user.providers.deepseek.api, 'openai-completions');

    const empty = await dsh.rpcRaw('superagent/settings/mutate', { ns: 'llm-pi-ai', ops: [] });
    assert.equal(empty.error?.code, -32602);
  });

  test('settings failures carry their class in data.code, not in the message', async () => {
    // A schema refusal: `api` is a closed set, so the settings service rejects
    // the profile before it is persisted.
    const invalid = await dsh.rpcRaw('superagent/settings/mutate', {
      ns: 'llm-pi-ai',
      ops: [{ op: 'set', path: ['providers', 'bad-protocol'], value: {
        api: 'not-a-protocol', baseURL: provider.baseURL, models: [{ id: 'm' }],
      } }],
    });
    assert.equal(invalid.error?.code, -32603, JSON.stringify(invalid.error));
    assert.equal(invalid.error?.data?.code, 'SETTINGS_INVALID');
    assert.equal(entryOf(await dsh.rpc('superagent/llm/models'), 'bad-protocol'), undefined,
      'a refused profile must not be persisted');

    // A non-volatile path is the same class: the caller edited outside the form.
    const outside = await dsh.rpcRaw('superagent/settings/mutate', {
      ns: 'llm-pi-ai', ops: [{ op: 'set', path: ['not-a-field'], value: 1 }],
    });
    assert.equal(outside.error?.data?.code, 'SETTINGS_INVALID');

    // A stale expected revision is a conflict, and the reply names both sides
    // so a caller can retry without re-reading first.
    const current = (await dsh.rpc('superagent/settings/describe', { ns: 'llm-pi-ai' })).descriptor.revision;
    const conflict = await dsh.rpcRaw('superagent/settings/mutate', {
      ns: 'llm-pi-ai', ops: [{ op: 'set', path: ['providers', 'x'], value: {
        api: 'openai-completions', baseURL: provider.baseURL, models: [{ id: 'm' }],
      } }], expectedRevision: current + 99,
    });
    assert.equal(conflict.error?.data?.code, 'SETTINGS_CONFLICT', JSON.stringify(conflict.error));
    assert.equal(conflict.error.data.expected, current + 99);
    assert.equal(conflict.error.data.actual, current);
    assert.equal(entryOf(await dsh.rpc('superagent/llm/models'), 'x'), undefined, 'a conflicted write must not land');
  });

  test('a declared route reports its metadata and can be selected', async () => {
    const info = await dsh.rpc('superagent/llm/model/info', { provider: 'deepseek', model: 'deepseek-chat' });
    assert.equal(info.provider, 'deepseek');
    assert.equal(info.id, 'deepseek-chat');
    assert.equal(info.name, 'DeepSeek Chat');
    assert.deepEqual(info.inputModalities, ['text', 'image']);
    assert.deepEqual(info.reasoning.efforts.map((effort) => effort.id), ['low', 'high', 'max']);
    assert.equal(info.context.contextWindow, 1_000_000);
    assert.equal(info.defaultMaxTokens, 384_000);

    const selected = await dsh.rpc('superagent/session/model/select', {
      sessionId, provider: 'deepseek', model: 'deepseek-chat', reasoningEffort: 'max',
    });
    assert.deepEqual(selected, { provider: 'deepseek', model: 'deepseek-chat', reasoningEffort: 'max' });

    const unsupported = await dsh.rpcRaw('superagent/session/model/select', {
      sessionId, provider: 'deepseek', model: 'deepseek-chat', reasoningEffort: 'medium',
    });
    assert.equal(unsupported.error?.data?.code, 'UNSUPPORTED_REASONING_EFFORT', JSON.stringify(unsupported.error));
    assert.match(unsupported.error?.message ?? '', /does not support reasoning effort "medium"/);
    const supported = await dsh.rpc('superagent/session/model/select', {
      sessionId, provider: 'deepseek', model: 'deepseek-chat', reasoningEffort: 'low',
    });
    assert.equal(supported.reasoningEffort, 'low');
  });

  test('credentials set makes the route available and writes the managed files', async () => {
    assert.deepEqual(await dsh.rpc('superagent/credentials/set', { ref: 'DEEPSEEK_API_KEY', value: 'live-test-secret' }), { ok: true });

    const models = await dsh.rpc('superagent/llm/models');
    const deepseek = entryOf(models, 'deepseek');
    assert.deepEqual(deepseek.credential, { ref: 'DEEPSEEK_API_KEY', present: true, known: true });
    assert.deepEqual(deepseek.models.map((model) => model.id), ['deepseek-chat']);
    assert.equal(deepseek.models[0].name, 'DeepSeek Chat');
    assert.deepEqual(deepseek.models[0].inputModalities, ['text', 'image']);

    const file = credentialsPath(root.root);
    assert.ok(fs.existsSync(file), `${file} must exist after credentials/set`);
    assert.equal(fs.statSync(file).mode & 0o777, 0o600, 'a credentials document is owner-only');
    const text = fs.readFileSync(file, 'utf8');
    assert.match(text, /^version: 1$/m);
    assert.match(text, /DEEPSEEK_API_KEY: live-test-secret/);
    // The provider is still never contacted: discovery reads the credential,
    // it does not probe the endpoint.
    assert.equal(provider.requests.length, 0);
    assert.doesNotMatch(fs.readFileSync(profilePatchPath(root.root), 'utf8'), /live-test-secret/,
      'the profile patch must reference the credential, never contain it');
  });

  test('an invalid credential reference is rejected and the value is not echoed', async () => {
    const invalidRef = await dsh.rpcRaw('superagent/credentials/set', { ref: 'not a ref', value: 'do-not-log-this' });
    assert.equal(invalidRef.error?.code, -32602);
    assert.doesNotMatch(invalidRef.error.message, /do-not-log-this/);
    const empty = await dsh.rpcRaw('superagent/credentials/set', { ref: 'DEEPSEEK_API_KEY', value: '' });
    assert.equal(empty.error?.code, -32602);
  });

  test('credentials unset removes the reference and the route models again', async () => {
    assert.deepEqual(await dsh.rpc('superagent/credentials/unset', { ref: 'DEEPSEEK_API_KEY' }), { ok: true });
    const models = await dsh.rpc('superagent/llm/models');
    const deepseek = entryOf(models, 'deepseek');
    // Three-state: `present` is the answer, `known` says the credentials
    // service answered at all (a not-yet-mounted service reports known:false
    // rather than a misleading absence).
    assert.deepEqual(deepseek.credential, { ref: 'DEEPSEEK_API_KEY', present: false, known: true });
    assert.deepEqual(deepseek.models, []);
    const text = fs.readFileSync(credentialsPath(root.root), 'utf8');
    assert.doesNotMatch(text, /DEEPSEEK_API_KEY/);
    assert.doesNotMatch(text, /live-test-secret/);
  });
});

// Two settings states cannot be produced through the bridge, because producing
// them is exactly what the bridge refuses: an overlay that shadows the
// namespace, and an entry the operator disabled. Both are reached by writing
// the patch DSH itself reads, which is the situation `ov-superagent dsh model repair`
// exists for. Each case owns its process, so a failed assertion cannot leave the
// other case's child running.
describe('live dsh settings states that need a hand-edited patch', { skip: liveSkip }, () => {
  let root;

  before(() => { root = prepareLiveDshRoot({ prefix: 'superagent-dsh-live-override-' }); });
  after(() => { cleanupLiveDshRoot(root?.root); });

  /** Start a client for this root and stop it when the test ends, pass or fail. */
  async function start(t, options = {}) {
    const client = await startLiveDsh({ root, env: hermeticDshEnv({ root }), ...options });
    t.after(() => client.stop());
    await client.initialize();
    return client;
  }

  test('an overlay that names llm-pi-ai makes every write SETTINGS_OVERRIDDEN', async (t) => {
    // The run patch names the namespace, which is the one thing the design
    // forbids: the settings service then refuses to write it at all.
    const client = await start(t, {
      policy: 'superagent live bridge fixture (overridden)',
      // The override needs a route dict, not a single profile.
      provider: { deepseek: deepseekProfile('http://127.0.0.1:9/v1') },
    });
    // The overlay's route registers and its reference resolves from the same
    // document the settings service reads; nothing here has stored a value, so
    // the credential reports absent.
    const deepseek = entryOf(await client.rpc('superagent/llm/models'), 'deepseek');
    assert.ok(deepseek, 'a run-patch overlay still contributes its routes');
    assert.deepEqual(deepseek.credential, { ref: 'DEEPSEEK_API_KEY', present: false, known: true });

    const mutated = await client.rpcRaw('superagent/settings/mutate', {
      ns: 'llm-pi-ai', ops: [{ op: 'set', path: ['providers', 'other'], value: {
        api: 'openai-completions', baseURL: 'http://127.0.0.1:9/v2', models: [{ id: 'm' }],
      } }],
    });
    assert.equal(mutated.error?.code, -32603, JSON.stringify(mutated.error));
    assert.equal(mutated.error?.data?.code, 'SETTINGS_OVERRIDDEN', JSON.stringify(mutated.error));
    assert.match(mutated.error.message, /overridden by a home patch or command-line overlay/);
  });

  test('a disabled entry refuses writes as NOT_CONFIGURABLE', async (t) => {
    const client = await start(t, { policy: 'superagent live bridge fixture (disabled)' });
    const document = profilePatchPath(root.root);
    fs.mkdirSync(path.dirname(document), { recursive: true });
    // Disabling the entry is the state an operator reaches by hand; the write
    // path must name it rather than folding it into a schema failure.
    fs.writeFileSync(document, '- id: llm-pi-ai\n  disabled: true\n', { mode: 0o600 });

    // The settings service reloads the document on change; poll rather than
    // sleep a fixed window, so a slow machine reports the state it reached.
    const deadline = Date.now() + 10_000;
    let mutated;
    while (Date.now() < deadline) {
      mutated = await client.rpcRaw('superagent/settings/mutate', {
        ns: 'llm-pi-ai', ops: [{ op: 'set', path: ['providers', 'x'], value: {
          api: 'openai-completions', baseURL: 'http://127.0.0.1:9/v1', models: [{ id: 'm' }],
        } }],
      });
      if (mutated.error?.data?.code === 'SETTINGS_NOT_CONFIGURABLE') break;
      await new Promise((resolve) => setTimeout(resolve, 150));
    }
    assert.equal(mutated.error?.code, -32603, JSON.stringify(mutated.error ?? mutated.result));
    assert.equal(mutated.error?.data?.code, 'SETTINGS_NOT_CONFIGURABLE', JSON.stringify(mutated.error ?? mutated.result));
  });
});

describe('live dsh bridge v2 with DEEPSEEK_API_KEY', { skip: liveSkip }, () => {
  let root;
  let dsh;

  before(async () => {
    root = prepareLiveDshRoot({ prefix: 'superagent-dsh-live-keyed-' });
    dsh = await startLiveDsh({
      root,
      // A dead local endpoint: no test may reach the network, and a keyed
      // catalogue read must not need the provider to answer.
      env: hermeticDshEnv({ root, apiKey: 'live-fixture-key', baseURL: 'http://127.0.0.1:9/v1' }),
      policy: 'superagent live bridge fixture (keyed)',
    });
    await dsh.initialize();
  });

  after(async () => {
    const stderr = dsh?.stderr() ?? '';
    await dsh?.stop();
    cleanupLiveDshRoot(root?.root);
    assert.doesNotMatch(stderr, /did not activate|unmatched|not matched/i, `dsh stderr: ${stderr}`);
  });

  test('the official route lists its two built-in models while the account route stays empty', async () => {
    const models = await dsh.rpc('superagent/llm/models');
    const official = entryOf(models, 'deepseek-official');
    const account = entryOf(models, 'deepseek-account');
    assert.deepEqual(official.models.map((model) => model.id).sort(), ['deepseek-flash', 'deepseek-v4-pro']);
    for (const model of official.models) assert.equal(typeof model.name, 'string');
    assert.deepEqual(account.models, []);
    assert.equal(official.credential, null);
    assert.equal(account.credential, null);

    // The same credential view the CLI shows: a route whose models resolve is
    // offered; an empty route is what the daemon filters out before /model.
    assert.ok(official.models.length > 0);
    assert.equal(account.models.length, 0);
  });
});
