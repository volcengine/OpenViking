// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
// The migration half of `ov-superagent dsh model`: turning a legacy Pi `models.json`
// into native DSH routes without ever sending a request to the wrong provider.
//
// The write protocol is three invariants, and the tests assert the invariants
// rather than the current spellings of the implementation:
//
//   I1  no write ever targets a credential reference a configured route
//       resolves. A literal key always gets a NEW reference; an existing
//       reference is not touched at all. The visible consequence is deliberate
//       rotation — `_2` then `_3` — not a claim of stability (P1-a, P2-d).
//   I2  value first, then ONE settings mutate carrying endpoint and reference
//       together, then cleanup of what this run created. A refused write
//       removes those references again; an unconfirmed one keeps them and says
//       so, because the write may have landed (P1-c).
//   I3  every attempt rebuilds the plan from the state it just read. A
//       SETTINGS_CONFLICT retry may not replay a stale plan, and a second
//       conflict aborts instead of reporting success (P1-b).
//
// Two more properties live here because they are part of the same translation:
// a route whose models resolve to different endpoints or protocols is split
// instead of re-pointed (P1-3) with `modelOverrides` in the effective
// configuration (P2-3), and a model id is migrated byte for byte — never
// trimmed into a different id, never folded into a duplicate (P2-b). A variable
// the daemon strips from the DSH child is named as such (P3).
//
// Pi's own fallback is part of the contract: a model that declares no endpoint
// inherits its provider's, and when the provider declares none it inherits the
// endpoint of a sibling model (`findModelDefaults` in pi's
// provider-composer). That inheritance is unambiguous only while the models
// agree on one endpoint, which is exactly the boundary these tests draw.

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

import { deriveKeyRef, importPlan } from '../src/dsh-model.mjs';
// Imported as a namespace so this file still loads against a build that
// predates the credential-document reader: the test below then fails on the
// missing symbol instead of taking every other case down with a load error.
import * as dshModelModule from '../src/dsh-model.mjs';
import { setLanguage } from '../src/i18n.mjs';

const BIN = fileURLToPath(new URL('../bin/vika.mjs', import.meta.url));

// English diagnostics, so an assertion names the sentence it means.
setLanguage('en');

function notes(plan) {
  return plan.notes.join('\n');
}

function routesOf(plan) {
  return plan.routes.map((route) => route.route);
}

function profileOf(plan, route) {
  const op = plan.ops.find((entry) => entry.op === 'set' && entry.path.join('/') === `providers/${route}`);
  return op ? op.value : null;
}

function modelsOf(plan, route) {
  return (profileOf(plan, route)?.models ?? []).map((model) => model.id);
}

// ---------------------------------------------------------------------------
// I1: a reference a route resolves is never written, and never shared.
// ---------------------------------------------------------------------------

test('the credential document is read for its reference names only', () => {
  // The bridge has no "list credentials" call, so the import learns which names
  // already hold a value from DSH's own document. Only the top-level keys under
  // `refs:` count: the `records:` section above it belongs to other services,
  // and a value is never read, let alone printed.
  const document = [
    'version: 1',
    'records:',
    '  client-connection/browser-session:',
    '    kind: grant',
    '    payload:',
    '      secret: not-a-reference',
    'refs:',
    '  FOO_BAR_API_KEY: sk-one',
    '  FOO_BAR_API_KEY_2: sk-two',
    'other: 1',
    '',
  ].join('\n');
  assert.deepEqual([...dshModelModule.credentialRefsOf(document)].sort(), ['FOO_BAR_API_KEY', 'FOO_BAR_API_KEY_2']);
  assert.deepEqual([...dshModelModule.credentialRefsOf('version: 1\nrefs:\n')], [], 'an empty refs block declares nothing');
  assert.deepEqual([...dshModelModule.credentialRefsOf('version: 1\n')], [], 'a document without the block declares nothing');
});

test('the conventional credential reference collides across punctuation', () => {
  // The hazard itself: this is why a plain `deriveKeyRef` cannot be the whole
  // answer. All of these are legal route names and all of them map to one
  // reference, so the last write wins and the other routes authenticate with a
  // stranger's key.
  assert.equal(deriveKeyRef('foo-bar'), 'FOO_BAR_API_KEY');
  assert.equal(deriveKeyRef('foo_bar'), 'FOO_BAR_API_KEY');
  assert.equal(deriveKeyRef('foo.bar'), 'FOO_BAR_API_KEY');
  assert.equal(deriveKeyRef('FOO bar'), 'FOO_BAR_API_KEY');
});

test('import gives colliding routes distinct references and names the owner', () => {
  const plan = importPlan({ providers: {
    'foo-bar': { api: 'openai-completions', baseUrl: 'http://one.test/v1', apiKey: 'sk-one', models: [{ id: 'm-one' }] },
    foo_bar: { api: 'openai-completions', baseUrl: 'http://two.test/v1', apiKey: 'sk-two', models: [{ id: 'm-two' }] },
    'foo.bar': { api: 'openai-completions', baseUrl: 'http://three.test/v1', apiKey: 'sk-three', models: [{ id: 'm-three' }] },
  } });

  const refs = Object.fromEntries(plan.routes.map((route) => [route.route, route.apiKeyEnv]));
  assert.equal(new Set(Object.values(refs)).size, 3,
    `three routes must get three references, got ${JSON.stringify(refs)}`);
  // The readable slug survives on its first owner; the others are suffixed.
  assert.equal(refs['foo-bar'], 'FOO_BAR_API_KEY');
  assert.match(refs.foo_bar, /^FOO_BAR_API_KEY_\d+$/);
  assert.match(refs['foo.bar'], /^FOO_BAR_API_KEY_\d+$/);
  // Every stored value travels with its own reference, so no route ends up
  // holding another route's secret.
  const stored = Object.fromEntries(plan.credentials.map((entry) => [entry.ref, entry.value]));
  assert.deepEqual(stored, {
    FOO_BAR_API_KEY: 'sk-one',
    [refs.foo_bar]: 'sk-two',
    [refs['foo.bar']]: 'sk-three',
  });
  assert.match(notes(plan), /already used by foo-bar/, 'the note names the route that owns the reference');
});

test('import never reuses a reference another configured route already claims', () => {
  // `foo-bar` is already configured and owns FOO_BAR_API_KEY. A later import
  // adds `foo_bar`; taking the same reference would overwrite a live secret.
  const plan = importPlan({ providers: {
    foo_bar: { api: 'openai-completions', baseUrl: 'http://two.test/v1', apiKey: 'sk-two', models: [{ id: 'm-two' }] },
  } }, {
    providers: {
      'foo-bar': {
        api: 'openai-completions', baseURL: 'http://one.test/v1', apiKeyEnv: 'FOO_BAR_API_KEY', models: [{ id: 'm-one' }],
      },
    },
  });
  const ref = plan.routes[0].apiKeyEnv;
  assert.notEqual(ref, 'FOO_BAR_API_KEY', 'the live reference belongs to another route');
  assert.match(ref, /^FOO_BAR_API_KEY_\d+$/);
  assert.match(notes(plan), /already used by foo-bar/);
});

test('a literal key never lands on the reference its own route already resolves', () => {
  // I1: FOO_BAR_API_KEY is being resolved by a configured route, so writing the
  // migrated literal there would change what that route authenticates with.
  // The value goes to a new reference and the old one is left alone — the
  // rotation the documentation states as the contract.
  const plan = importPlan({ providers: {
    'foo-bar': { api: 'openai-completions', baseUrl: 'http://one.test/v1', apiKey: 'sk-one', models: [{ id: 'm-one' }] },
  } }, {
    providers: {
      'foo-bar': {
        api: 'openai-completions', baseURL: 'http://one.test/v1', apiKeyEnv: 'FOO_BAR_API_KEY', models: [{ id: 'm-one' }],
      },
    },
  });
  assert.equal(plan.routes[0].apiKeyEnv, 'FOO_BAR_API_KEY_2', 'the new value takes a new reference');
  assert.equal(plan.routes[0].previousRef, 'FOO_BAR_API_KEY', 'the report names what was left in place');
  assert.deepEqual(plan.credentials.map((entry) => [entry.ref, entry.value, entry.previous]),
    [['FOO_BAR_API_KEY_2', 'sk-one', 'FOO_BAR_API_KEY']]);
  assert.match(notes(plan), /never overwrites an existing reference/);
  assert.match(notes(plan), /FOO_BAR_API_KEY is kept/);
});

test('a reference two live routes share is never written by an import of one of them', () => {
  // The P1-a shape: `foo-bar` and `foo_bar` both resolve FOO_BAR_API_KEY.
  // Importing only `foo-bar` must not change what `foo_bar` sends.
  const plan = importPlan({ providers: {
    'foo-bar': { api: 'openai-completions', baseUrl: 'http://one.test/v1', apiKey: 'sk-NEW', models: [{ id: 'm-one' }] },
  } }, {
    providers: {
      'foo-bar': {
        api: 'openai-completions', baseURL: 'http://one.test/v1', apiKeyEnv: 'FOO_BAR_API_KEY', models: [{ id: 'm-one' }],
      },
      foo_bar: {
        api: 'openai-completions', baseURL: 'http://two.test/v1', apiKeyEnv: 'FOO_BAR_API_KEY', models: [{ id: 'm-two' }],
      },
    },
  });
  assert.notEqual(plan.routes[0].apiKeyEnv, 'FOO_BAR_API_KEY');
  const written = new Set(plan.credentials.map((entry) => entry.ref));
  assert.ok(!written.has('FOO_BAR_API_KEY'), `the shared reference must not be written: ${[...written]}`);
});

test('an import never takes a reference the credential store already holds a value under', () => {
  // A name with a value but no route is still somebody's secret: adopting it
  // would destroy it, and a failed import could then delete what it found.
  const plan = importPlan({ providers: {
    gateway: { api: 'openai-completions', baseUrl: 'http://gateway.test/v1', apiKey: 'sk-new', models: [{ id: 'm' }] },
  } }, { stored: new Set(['GATEWAY_API_KEY', 'GATEWAY_API_KEY_2']) });
  assert.equal(plan.routes[0].apiKeyEnv, 'GATEWAY_API_KEY_3');
});

test('a re-planned import reuses the reference it already wrote', () => {
  // A conflict retry rebuilds the plan; the value is already stored under the
  // name this run allocated, so the rebuild keeps it instead of writing a
  // second copy under a new name.
  const plan = importPlan({ providers: {
    gateway: { api: 'openai-completions', baseUrl: 'http://gateway.test/v1', apiKey: 'sk-new', models: [{ id: 'm' }] },
  } }, {
    stored: new Set(['GATEWAY_API_KEY', 'GATEWAY_API_KEY_2']),
    pinned: { gateway: 'GATEWAY_API_KEY_2' },
  });
  assert.equal(plan.routes[0].apiKeyEnv, 'GATEWAY_API_KEY_2');
  assert.equal(plan.credentials[0].value, 'sk-new');
});

test('a re-plan never points a route at a reference another route started using', () => {
  // The pinned name was free when this run wrote it; a concurrent writer took
  // it meanwhile. Reusing it would hand that route this import's secret, so the
  // rebuild allocates a different one and leaves both the name and its value
  // alone.
  const plan = importPlan({ providers: {
    gateway: { api: 'openai-completions', baseUrl: 'http://gateway.test/v1', apiKey: 'sk-new', models: [{ id: 'm' }] },
  } }, {
    providers: {
      other: {
        api: 'openai-completions', baseURL: 'http://other.test/v1', apiKeyEnv: 'GATEWAY_API_KEY_2', models: [{ id: 'x' }],
      },
    },
    pinned: { gateway: 'GATEWAY_API_KEY_2' },
  });
  assert.notEqual(plan.routes[0].apiKeyEnv, 'GATEWAY_API_KEY_2');
  assert.equal(plan.credentials[0].ref, plan.routes[0].apiKeyEnv);
  assert.match(notes(plan), /another route started resolving GATEWAY_API_KEY_2/);
});

test('a route that declares its own reference keeps it', () => {
  const plan = importPlan({ providers: {
    'foo-bar': { api: 'openai-completions', baseUrl: 'http://one.test/v1', apiKey: '$MY_OWN_KEY', models: [{ id: 'm-one' }] },
    foo_bar: { api: 'openai-completions', baseUrl: 'http://two.test/v1', apiKey: 'sk-two', models: [{ id: 'm-two' }] },
  } });
  const refs = Object.fromEntries(plan.routes.map((route) => [route.route, route.apiKeyEnv]));
  assert.equal(refs['foo-bar'], 'MY_OWN_KEY', 'an explicit reference is never rewritten');
  // The derived one still has to avoid the explicitly claimed name's owner.
  assert.match(refs.foo_bar, /^FOO_BAR_API_KEY(_\d+)?$/);
});

// ---------------------------------------------------------------------------
// P1-3: the endpoint decision is made on each model's effective config.
// ---------------------------------------------------------------------------

test('a model that declares no endpoint inherits the one endpoint its siblings agree on', () => {
  // Pi's own fallback: one declared endpoint on the route, so the silent model
  // is not re-pointed anywhere, it inherits the only address in play.
  const plan = importPlan({ providers: {
    gateway: { models: [
      { id: 'private-large', api: 'anthropic-messages', baseUrl: 'http://gateway.test/anthropic' },
      { id: 'private-small' },
    ] },
  } });
  assert.deepEqual(routesOf(plan), ['gateway']);
  assert.equal(profileOf(plan, 'gateway').api, 'anthropic-messages');
  assert.equal(profileOf(plan, 'gateway').baseURL, 'http://gateway.test/anthropic');
  assert.deepEqual(modelsOf(plan, 'gateway'), ['private-large', 'private-small']);
  assert.match(notes(plan), /taken from the model entries/, 'the promotion is reported, not silent');
});

test('two model-level endpoints become two routes, each with its own address', () => {
  const plan = importPlan({ providers: {
    split: { models: [
      { id: 'first', api: 'openai-completions', baseUrl: 'http://one.test/v1' },
      { id: 'second', api: 'openai-completions', baseUrl: 'http://two.test/v1' },
    ] },
  } });
  assert.deepEqual(routesOf(plan), ['split', 'split--2']);
  assert.equal(profileOf(plan, 'split').baseURL, 'http://one.test/v1');
  assert.deepEqual(modelsOf(plan, 'split'), ['first']);
  assert.equal(profileOf(plan, 'split--2').baseURL, 'http://two.test/v1');
  assert.deepEqual(modelsOf(plan, 'split--2'), ['second']);
  assert.match(notes(plan), /split into 2 routes/, 'the split is reported, not silent');
});

test('a model with no endpoint on a route that names several is refused, not guessed', () => {
  // The regression: the first model's address was promoted to the route, so
  // this model was silently sent to another provider's endpoint.
  const plan = importPlan({ providers: {
    split: { models: [
      { id: 'first', api: 'openai-completions', baseUrl: 'http://one.test/v1' },
      { id: 'second', api: 'openai-completions', baseUrl: 'http://two.test/v1' },
      { id: 'silent', api: 'openai-completions' },
    ] },
  } });
  assert.deepEqual(routesOf(plan), ['split', 'split--2']);
  assert.deepEqual(modelsOf(plan, 'split'), ['first']);
  assert.deepEqual(modelsOf(plan, 'split--2'), ['second']);
  assert.ok(!modelsOf(plan, 'split').includes('silent') && !modelsOf(plan, 'split--2').includes('silent'),
    'the silent model is not re-pointed to either provider');
  assert.match(notes(plan), /split\/silent/, 'the skipped model is named');
  assert.match(notes(plan), /declares no endpoint/, 'the reason is stated');
});

test('a model skipped for an unserved protocol does not donate its endpoint', () => {
  const plan = importPlan({ providers: {
    mixed: { models: [
      { id: 'on-foreign-api', api: 'google-generative-ai', baseUrl: 'http://foreign.test/v1' },
      { id: 'survivor', api: 'openai-completions' },
    ] },
  } });
  // The only model that declared an endpoint was dropped for its protocol, so
  // nothing is left to promote and the survivor has no address of its own.
  assert.deepEqual(plan.ops, []);
  assert.match(notes(plan), /google-generative-ai/, 'the dropped protocol is named');
  assert.match(notes(plan), /declares no endpoint/, 'the survivor is not given a stranger\'s address');
});

test('a route that declares its own endpoint is never split', () => {
  const plan = importPlan({ providers: {
    pinned: { api: 'openai-completions', baseUrl: 'http://only.test/v1', models: [
      { id: 'a' }, { id: 'b', baseUrl: 'http://only.test/v1' },
    ] },
  } });
  assert.deepEqual(routesOf(plan), ['pinned']);
  assert.deepEqual(modelsOf(plan, 'pinned'), ['a', 'b']);
  assert.equal(profileOf(plan, 'pinned').baseURL, 'http://only.test/v1');
});

test('a model on another endpoint than its route gets its own route', () => {
  const plan = importPlan({ providers: {
    pinned: { api: 'openai-completions', baseUrl: 'http://one.test/v1', models: [
      { id: 'a' }, { id: 'b', baseUrl: 'http://two.test/v1' },
    ] },
  } });
  assert.deepEqual(routesOf(plan), ['pinned', 'pinned--2']);
  assert.equal(profileOf(plan, 'pinned').baseURL, 'http://one.test/v1');
  assert.equal(profileOf(plan, 'pinned--2').baseURL, 'http://two.test/v1');
  assert.deepEqual(modelsOf(plan, 'pinned--2'), ['b']);
});

test('two protocols on one route become two routes', () => {
  const plan = importPlan({ providers: {
    dual: { baseUrl: 'http://dual.test/v1', models: [
      { id: 'chat', api: 'openai-completions' },
      { id: 'messages', api: 'anthropic-messages' },
    ] },
  } });
  assert.deepEqual(routesOf(plan), ['dual', 'dual--2']);
  assert.equal(profileOf(plan, 'dual').api, 'openai-completions');
  assert.equal(profileOf(plan, 'dual--2').api, 'anthropic-messages');
  assert.equal(profileOf(plan, 'dual--2').baseURL, 'http://dual.test/v1',
    'both halves still share the route-level endpoint');
});

test('a generated route name never collides with another route in the document', () => {
  const plan = importPlan({ providers: {
    dual: { models: [
      { id: 'chat', api: 'openai-completions', baseUrl: 'http://one.test/v1' },
      { id: 'messages', api: 'anthropic-messages', baseUrl: 'http://two.test/v1' },
    ] },
    // A real route already occupying the name the split would have taken.
    'dual--2': { api: 'openai-completions', baseUrl: 'http://three.test/v1', models: [{ id: 'own' }] },
  } });
  assert.deepEqual([...routesOf(plan)].sort(), ['dual', 'dual--2', 'dual--3']);
  assert.equal(profileOf(plan, 'dual--2').baseURL, 'http://three.test/v1', 'the existing route is untouched');
  assert.deepEqual(modelsOf(plan, 'dual--2'), ['own']);
  assert.equal(profileOf(plan, 'dual--3').baseURL, 'http://two.test/v1', 'the split takes the next free name');
});

test('a generated route name never collides with a configured route', () => {
  const plan = importPlan({ providers: {
    dual: { models: [
      { id: 'chat', api: 'openai-completions', baseUrl: 'http://one.test/v1' },
      { id: 'messages', api: 'anthropic-messages', baseUrl: 'http://two.test/v1' },
    ] },
  } }, {
    providers: {
      'dual--2': { api: 'openai-completions', baseURL: 'http://live.test/v1', models: [{ id: 'live' }] },
    },
  });
  assert.deepEqual([...routesOf(plan)].sort(), ['dual', 'dual--3']);
});

// ---------------------------------------------------------------------------
// P2-3: modelOverrides is part of the effective configuration.
// ---------------------------------------------------------------------------

test('modelOverrides is merged into the model it names', () => {
  // Pi's effective model here is non-reasoning and text-only with a larger
  // window; ignoring the override imported it as reasoning, 128k, image-capable.
  const plan = importPlan({ providers: {
    gateway: {
      api: 'openai-completions',
      baseUrl: 'http://gateway.test/v1',
      models: [{ id: 'plain', name: 'Plain', contextWindow: 128000, maxTokens: 8192 }],
      modelOverrides: {
        plain: { reasoning: false, input: ['text'], contextWindow: 200000, maxTokens: 4096, name: 'Plain (capped)' },
      },
    },
  } });
  assert.deepEqual(profileOf(plan, 'gateway').models, [{
    id: 'plain',
    name: 'Plain (capped)',
    contextWindow: 200000,
    maxTokens: 4096,
    input: ['text'],
    reasoningEfforts: false,
  }]);
});

test('a modelOverrides entry that turns reasoning on carries its levels', () => {
  const plan = importPlan({ providers: {
    gateway: {
      api: 'openai-completions',
      baseUrl: 'http://gateway.test/v1',
      models: [{ id: 'thinking' }],
      modelOverrides: { thinking: { reasoning: true, thinkingLevelMap: { minimal: null, low: 'low', high: 'high', medium: null } } },
    },
  } });
  // `minimal`/`medium` are mapped to null, so they are not offered; `off` is
  // implicit and stays empty, and `high` keeps the spelling the gateway wants.
  assert.deepEqual(profileOf(plan, 'gateway').models[0].reasoningEfforts, { off: null, low: 'low', high: 'high' });
});

test('an override DSH cannot express is reported instead of dropped silently', () => {
  const plan = importPlan({ providers: {
    gateway: {
      api: 'openai-completions',
      baseUrl: 'http://gateway.test/v1',
      models: [{ id: 'tuned' }],
      modelOverrides: {
        tuned: {
          contextWindow: 1000,
          inputLimits: { images: { maxBytes: 1 } },
          promptCache: { short: 60 },
          samplingParams: { temperature: 0.2 },
        },
      },
    },
  } });
  assert.equal(profileOf(plan, 'gateway').models[0].contextWindow, 1000, 'the expressible part lands');
  const reported = notes(plan);
  for (const field of ['inputLimits', 'promptCache', 'samplingParams']) {
    assert.ok(reported.includes(field), `the dropped override field ${field} must be named: ${reported}`);
  }
});

test('an override that names no model is reported, not silently ignored', () => {
  const plan = importPlan({ providers: {
    gateway: {
      api: 'openai-completions',
      baseUrl: 'http://gateway.test/v1',
      models: [{ id: 'known' }],
      modelOverrides: { unknown: { contextWindow: 1000 } },
    },
  } });
  assert.match(notes(plan), /unknown/);
  assert.match(notes(plan), /modelOverrides/);
});

test('modelOverrides on a route with no models list is refused with a reason', () => {
  // DSH refuses `modelOverrides` on a route its installed catalogue does not
  // describe, and a hand-declared route has no catalogue: migrating this would
  // be a write DSH rejects, so it is skipped and said out loud.
  const plan = importPlan({ providers: {
    builtinish: { modelOverrides: { 'some-model': { contextWindow: 1000 } } },
  } });
  assert.deepEqual(plan.ops, []);
  assert.match(notes(plan), /modelOverrides/);
  assert.match(notes(plan), /skipped/);
});

test('an override that sets id is reported, because the dict key is the id', () => {
  const plan = importPlan({ providers: {
    gateway: {
      api: 'openai-completions',
      baseUrl: 'http://gateway.test/v1',
      models: [{ id: 'named' }],
      modelOverrides: { named: { id: 'renamed', contextWindow: 1000 } },
    },
  } });
  assert.equal(profileOf(plan, 'gateway').models[0].id, 'named');
  assert.match(notes(plan), /"id"/);
});

// ---------------------------------------------------------------------------
// I1 (second half): a route the document does not give a credential keeps the
// one it resolves. A whole-route `set` replaces the profile, so omitting
// `apiKeyEnv` silently moved an existing route from its stored key to DSH's own
// environment discovery.
// ---------------------------------------------------------------------------

test('a route the document gives no credential keeps the one it resolves', () => {
  const plan = importPlan({ providers: {
    gateway: { api: 'openai-completions', baseUrl: 'http://x.test/v1', models: [{ id: 'm' }] },
  } }, {
    providers: {
      gateway: {
        api: 'openai-completions', baseURL: 'http://old.test/v1', apiKeyEnv: 'R_API_KEY', models: [{ id: 'old' }],
      },
    },
  });
  assert.equal(plan.routes[0].apiKeyEnv, 'R_API_KEY', 'the stored reference is not dropped by the upsert');
  assert.match(notes(plan), /declares no credential/);
  assert.match(notes(plan), /R_API_KEY/, 'the note names the reference it kept');
  // Nothing is written to it: only the naming survives.
  assert.deepEqual(plan.credentials, []);
});

test('a route that had no reference stays without one', () => {
  const plan = importPlan({ providers: {
    gateway: { api: 'openai-completions', baseUrl: 'http://x.test/v1', models: [{ id: 'm' }] },
  } }, {
    providers: {
      gateway: { api: 'openai-completions', baseURL: 'http://old.test/v1', models: [{ id: 'old' }] },
    },
  });
  assert.equal(plan.routes[0].apiKeyEnv, '', 'no reference is invented');
  assert.ok(!('apiKeyEnv' in profileOf(plan, 'gateway')), 'the profile declares none');
  assert.match(notes(plan), /had none/, 'the note says which of the two cases this is');
});

test('a new split half of a route with a stored reference gets none of its own', () => {
  // The first half is the existing route and keeps R_API_KEY; the second half is
  // a route this migration creates, so there is no reference to preserve.
  const plan = importPlan({ providers: {
    dual: { baseUrl: 'http://dual.test/v1', models: [
      { id: 'chat', api: 'openai-completions' },
      { id: 'messages', api: 'anthropic-messages' },
    ] },
  } }, {
    providers: {
      dual: { api: 'openai-completions', baseURL: 'http://dual.test/v1', apiKeyEnv: 'R_API_KEY', models: [{ id: 'old' }] },
    },
  });
  assert.equal(profileOf(plan, 'dual').apiKeyEnv, 'R_API_KEY');
  assert.ok(!('apiKeyEnv' in profileOf(plan, 'dual--2')), 'a new route does not inherit the old half\'s reference');
  assert.match(notes(plan), /new split half dual--2 had none/);
});

// ---------------------------------------------------------------------------
// P2-b: a model id is migrated byte for byte, never silently rewritten.
// ---------------------------------------------------------------------------

test('a model id with surrounding whitespace is kept verbatim, and said to be unselectable', () => {
  // `"model "` used to be trimmed into `model`: a silent rename with no note.
  // DSH serves the original id, so the migration keeps it and reports that
  // `/model` cannot select it yet.
  const plan = importPlan({ providers: {
    route: { api: 'openai-completions', baseUrl: 'http://x.test/v1', models: [{ id: 'model ' }] },
  } });
  assert.deepEqual(modelsOf(plan, 'route'), ['model '], 'the id keeps its bytes');
  assert.match(notes(plan), /verbatim/, 'the note says it was migrated as-is');
  assert.match(notes(plan), /cannot select it/, 'the note says /model cannot reach it');
});

test('ids that differ only by whitespace do not collapse into a duplicate', () => {
  // Trimming folded these three into `m`, `m`, `m  x`, and DSH then refused the
  // whole route with "lists model m more than once" — a rejection that points
  // at the wrong problem and loses every other model on the route.
  const plan = importPlan({ providers: {
    route: { api: 'openai-completions', baseUrl: 'http://x.test/v1', models: [
      { id: 'm' }, { id: 'm ' }, { id: 'm  x' },
    ] },
  } });
  assert.deepEqual(modelsOf(plan, 'route'), ['m', 'm ', 'm  x']);
  assert.doesNotMatch(notes(plan), /more than once/, 'nothing was folded into a duplicate');
});

test('an id the document lists twice is reported instead of reaching DSH', () => {
  // A real duplicate cannot be served: DSH refuses the whole write, so the
  // migration keeps the first and names the problem.
  const plan = importPlan({ providers: {
    route: { api: 'openai-completions', baseUrl: 'http://x.test/v1', models: [{ id: 'm' }, { id: 'm' }] },
  } });
  assert.deepEqual(modelsOf(plan, 'route'), ['m']);
  assert.match(notes(plan), /more than once/);
});

test('a model id an external tool wrote with characters /model cannot reach is migrated', () => {
  // Spaces and `#` are legal for DSH and reachable by programmatic callers; the
  // migration must not refuse the whole document over them.
  const plan = importPlan({ providers: {
    route: { api: 'openai-completions', baseUrl: 'http://x.test/v1', models: [{ id: 'c#d' }, { id: 'has space' }] },
  } });
  assert.deepEqual(modelsOf(plan, 'route'), ['c#d', 'has space']);
  assert.match(notes(plan), /cannot select it/);
});

// ---------------------------------------------------------------------------
// P3: the withheld-environment list matches what the daemon really strips.
// ---------------------------------------------------------------------------

test('SESSION_ID is treated as a variable the DSH child does not inherit', () => {
  // process-utils.mjs strips SESSION_ID by name, so a route that names it can
  // only resolve from the credential service and the note must say so.
  const plan = importPlan({ providers: {
    route: {
      api: 'openai-completions', baseUrl: 'http://x.test/v1', apiKey: '$SESSION_ID', models: [{ id: 'm' }],
    },
  } });
  assert.equal(plan.routes[0].apiKeyEnv, 'SESSION_ID', 'the reference is kept so a stored value can resolve it');
  assert.match(notes(plan), /SESSION_ID/);
  assert.match(notes(plan), /withhold/);
  assert.match(notes(plan), /ov-superagent dsh model key set SESSION_ID/);
});

test('a variable the child does inherit stays silent', () => {
  const plan = importPlan({ providers: {
    route: { api: 'openai-completions', baseUrl: 'http://x.test/v1', apiKey: '$OPENAI_API_KEY', models: [{ id: 'm' }] },
  } });
  assert.equal(plan.routes[0].apiKeyEnv, 'OPENAI_API_KEY');
  assert.ok(!notes(plan).includes('withhold'), notes(plan));
});

// ---------------------------------------------------------------------------
// End to end through the real CLI and a protocol-speaking stand-in for `dsh`:
// the ordering of the writes is what P1-2 is about, and only a process run
// shows it.
// ---------------------------------------------------------------------------

const FAKE_DSH = `#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const stateDir = process.env.FAKE_DSH_STATE;
const statePath = path.join(stateDir, 'state.json');
const callsPath = path.join(stateDir, 'calls.jsonl');
const competitorMarker = path.join(stateDir, 'competitor-ran');
const mode = process.env.FAKE_DSH_MODE || 'normal';

function load() {
  try { return JSON.parse(fs.readFileSync(statePath, 'utf8')); }
  catch { return { revision: 0, providers: {}, credentials: {} }; }
}
function save(state) { fs.writeFileSync(statePath, JSON.stringify(state) + '\\n'); }
function record(entry) { fs.appendFileSync(callsPath, JSON.stringify(entry) + '\\n'); }
function send(frame) { process.stdout.write(JSON.stringify(frame) + '\\n'); }

// The real credentials service persists into <DSH_HOME>/.credentials.yaml, and
// the import reads that document so it never overwrites a value nothing
// resolves. The fake mirrors it, so what it stores is what the CLI can see.
const credentialsFile = path.join(process.env.DSH_HOME || '', '.credentials.yaml');
function writeCredentialDocument(state) {
  if (!process.env.DSH_HOME) return;
  fs.mkdirSync(path.dirname(credentialsFile), { recursive: true, mode: 0o700 });
  const rows = Object.entries(state.credentials || {}).map(([ref, value]) => '  ' + ref + ': ' + value);
  fs.writeFileSync(credentialsFile, 'version: 1\\nrefs:\\n' + rows.map((row) => row + '\\n').join(''), { mode: 0o600 });
}

// A real second CLI process writing through the same settings service, used to
// make a conflict real: it commits a route and a credential while the import
// sits between its read and its mutate. The child speaks this same fake, in the
// plain mode, so its writes land.
//
// The environment comes from a file the test wrote: this process runs as the
// DSH child, whose environment the daemon has already sanitized (OV_SUPERAGENT_CONFIG and
// DSH_BIN are stripped there), so re-deriving them here would run a different
// instance — or a real dsh.
function runCompetitor() {
  if (fs.existsSync(competitorMarker)) return null;
  fs.writeFileSync(competitorMarker, '1');
  const plan = JSON.parse(fs.readFileSync(path.join(stateDir, 'competitor-plan.json'), 'utf8'));
  const env = { ...process.env, ...plan.env, FAKE_DSH_MODE: 'normal' };
  // A real second CLI process writing through the same settings service. The
  // add subcommand is gone, so the competitor migrates a one-route models.json:
  // it commits a route and its credential like the add wizard would.
  const run = (args, input) => spawnSync(process.execPath, [plan.cli, 'dsh', 'model', ...args], {
    env, input, encoding: 'utf8', timeout: 120_000,
  });
  fs.writeFileSync(plan.models, JSON.stringify({ providers: {
    [plan.route]: {
      api: 'openai-completions', baseUrl: 'http://extra.test/v1', apiKey: 'sk-COMPETITOR', models: [{ id: 'm-under' }],
    },
  } }));
  const imported = run(['import', plan.models]);
  fs.writeFileSync(path.join(stateDir, 'competitor.json'), JSON.stringify({
    addStatus: imported.status, addOut: imported.stdout + imported.stderr,
    keyStatus: imported.status, keyOut: imported.stdout + imported.stderr,
    stateAfter: (() => { try { return fs.readFileSync(statePath, 'utf8'); } catch { return ''; } })(),
  }) + '\\n');
  return imported.status === 0;
}
function setPath(root, keys, value) {
  let node = root;
  for (const key of keys.slice(0, -1)) {
    if (!node[key] || typeof node[key] !== 'object' || Array.isArray(node[key])) node[key] = {};
    node = node[key];
  }
  node[keys[keys.length - 1]] = value;
}

function handle(frame) {
  const state = load();
  record({ method: frame.method, params: frame.params ?? null });
  if (frame.method === 'shutdown') { send({ jsonrpc: '2.0', id: frame.id, result: {} }); process.exit(0); }
  if (frame.method === 'vika/settings/mutate' && mode === 'mutate-refused') {
    send({ jsonrpc: '2.0', id: frame.id, error: { code: -32603, message: 'the llm-pi-ai entry is not configurable',
      data: { code: 'SETTINGS_NOT_CONFIGURABLE' } } });
    return;
  }
  if (frame.method === 'vika/settings/mutate' && mode === 'mutate-uncertain') {
    // The frame reached the service and was applied, but the answer never
    // arrives: from the CLI's side the commit is unknowable.
    for (const op of (frame.params && frame.params.ops) || []) {
      if (op.op === 'set') setPath(state.providers, op.path.slice(1), op.value);
      else if (op.op === 'unset') delete state.providers[op.path[1]];
    }
    state.revision += 1;
    save(state);
    process.exit(0);
  }
  if (frame.method === 'vika/settings/mutate' && mode === 'conflict-always') {
    send({ jsonrpc: '2.0', id: frame.id, error: { code: -32603,
      message: 'Configuration entry is no longer available',
      data: { code: 'SETTINGS_CONFLICT' } } });
    return;
  }
  if (frame.method === 'vika/settings/mutate' && mode === 'conflict-after-competitor') {
    // The competitor writes exactly once, so the retry faces a clean service:
    // what it does with the new state is the behaviour under test.
    if (runCompetitor() !== null) {
      send({ jsonrpc: '2.0', id: frame.id, error: { code: -32603,
        message: 'Configuration entry is no longer available',
        data: { code: 'SETTINGS_CONFLICT' } } });
      return;
    }
  }
  switch (frame.method) {
    case 'vika/hello':
      send({ jsonrpc: '2.0', id: frame.id, result: { bridge: 'vika-dsh-bridge', version: 2,
        methods: ['session/steer', 'session/cancel', 'vika/llm/models', 'vika/llm/model/info',
          'vika/session/model/select', 'vika/settings/describe', 'vika/settings/mutate',
          'vika/credentials/set', 'vika/credentials/unset'] } });
      return;
    case 'vika/settings/describe':
      send({ jsonrpc: '2.0', id: frame.id, result: { ns: 'llm-pi-ai', descriptor: {
        ns: 'llm-pi-ai', autoGenerate: false, applies: 'live', schema: {}, revision: state.revision,
        value: { providers: state.providers || {} }, user: { providers: {} }, secrets: [],
      } } });
      return;
    case 'vika/settings/mutate': {
      // A stale plan is detectable: the revision it was built from is fenced
      // here, exactly as the real settings service does.
      const expected = frame.params ? frame.params.expectedRevision : undefined;
      if (typeof expected === 'number' && expected !== state.revision) {
        send({ jsonrpc: '2.0', id: frame.id, error: { code: -32603,
          message: 'settings namespace "llm-pi-ai" changed since it was read (expected revision '
            + expected + ', now ' + state.revision + ')',
          data: { code: 'SETTINGS_CONFLICT' } } });
        return;
      }
      for (const op of (frame.params && frame.params.ops) || []) {
        if (op.op === 'set') setPath(state.providers, op.path.slice(1), op.value);
        else if (op.op === 'unset') delete state.providers[op.path[1]];
      }
      state.revision += 1;
      save(state);
      send({ jsonrpc: '2.0', id: frame.id, result: { ok: true } });
      return;
    }
    case 'vika/credentials/set':
      // Matched as a prefix: the import allocates suffixed references, and a
      // service refusing a value refuses it under whatever name it is written.
      if (mode === 'credential-refused' && frame.params.ref.startsWith(process.env.FAKE_DSH_REFUSE_REF)) {
        send({ jsonrpc: '2.0', id: frame.id, error: { code: -32603,
          message: 'credentials service refused ' + frame.params.ref } });
        return;
      }
      state.credentials[frame.params.ref] = frame.params.value;
      save(state);
      writeCredentialDocument(state);
      send({ jsonrpc: '2.0', id: frame.id, result: { ok: true } });
      return;
    case 'vika/credentials/unset':
      delete state.credentials[frame.params.ref];
      save(state);
      writeCredentialDocument(state);
      send({ jsonrpc: '2.0', id: frame.id, result: { ok: true } });
      return;
    default:
      send({ jsonrpc: '2.0', id: frame.id, error: { code: -32601, message: 'method not found: ' + frame.method } });
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

function fixture(t, { state = null } = {}) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-dsh-migrate-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const config = path.join(home, '.superagent', 'config.json');
  fs.mkdirSync(path.dirname(config), { recursive: true, mode: 0o700 });
  fs.writeFileSync(config, `${JSON.stringify({
    version: 1,
    defaultInstance: 'primary',
    env: {},
    agents: [{
      id: 'primary',
      displayName: 'Primary',
      channel: 'telegram',
      port: 3738,
      dataDir: path.join(home, 'data', 'primary'),
      workspace: path.join(home, 'workspace', 'primary'),
      ovConfig: path.join(home, 'ov', 'primary.json'),
      model: 'dsh',
    }],
  }, null, 2)}\n`);
  const fakeDir = fs.mkdtempSync(path.join(os.tmpdir(), 'fake-dsh-'));
  t.after(() => fs.rmSync(fakeDir, { recursive: true, force: true }));
  const script = path.join(fakeDir, 'dsh');
  fs.writeFileSync(script, FAKE_DSH, { mode: 0o755 });
  const stateDir = path.join(fakeDir, 'state');
  fs.mkdirSync(stateDir, { mode: 0o700 });
  const statePath = path.join(stateDir, 'state.json');
  fs.writeFileSync(statePath, `${JSON.stringify({
    revision: 0, providers: {}, credentials: {}, ...(state ?? {}),
  })}\n`);
  // The credential document the import reads, seeded from the fixture state so
  // "this reference already holds a value" is visible before the first write.
  const credentialsFile = path.join(home, 'data', 'primary', 'dsh', 'home', '.credentials.yaml');
  fs.mkdirSync(path.dirname(credentialsFile), { recursive: true, mode: 0o700 });
  const seeded = Object.entries(state?.credentials ?? {});
  fs.writeFileSync(credentialsFile, 'version: 1\nrefs:\n' + seeded.map(([ref, value]) => `  ${ref}: ${value}\n`).join(''), { mode: 0o600 });
  // What a competing CLI process needs to reach this same instance and this
  // same fake: written out because the fake runs with the daemon's sanitized
  // environment, which no longer carries OV_SUPERAGENT_CONFIG or DSH_BIN.
  fs.writeFileSync(path.join(stateDir, 'competitor-plan.json'), `${JSON.stringify({
    cli: BIN,
    route: 'foo_bar_extra',
    models: path.join(home, 'competitor-models.json'),
    env: { HOME: home, OV_SUPERAGENT_CONFIG: config, OV_SUPERAGENT_LANG: 'en', DSH_BIN: script, FAKE_DSH_STATE: stateDir },
  })}\n`);
  return {
    home,
    statePath,
    stateDir,
    env: {
      ...process.env,
      HOME: home,
      OV_SUPERAGENT_CONFIG: config,
      OV_SUPERAGENT_LANG: 'en',
      DSH_BIN: script,
      FAKE_DSH_STATE: stateDir,
    },
    competitor: () => {
      try { return JSON.parse(fs.readFileSync(path.join(stateDir, 'competitor.json'), 'utf8')); }
      catch { return null; }
    },
    calls: () => readJsonLines(path.join(stateDir, 'calls.jsonl')),
    state: () => JSON.parse(fs.readFileSync(statePath, 'utf8')),
    stateBytes: () => fs.readFileSync(statePath),
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

function writeModels(f, providers) {
  const file = path.join(f.home, 'models.json');
  fs.writeFileSync(file, JSON.stringify({ providers }));
  return file;
}

test('end to end: three colliding routes import with three distinct credentials', (t) => {
  const f = fixture(t);
  const models = writeModels(f, {
    'foo-bar': { api: 'openai-completions', baseUrl: 'http://one.test/v1', apiKey: 'sk-for-one', models: [{ id: 'm-one' }] },
    foo_bar: { api: 'openai-completions', baseUrl: 'http://two.test/v1', apiKey: 'sk-for-two', models: [{ id: 'm-two' }] },
    'foo.bar': { api: 'openai-completions', baseUrl: 'http://three.test/v1', apiKey: 'sk-for-three', models: [{ id: 'm-three' }] },
  });
  const result = run(f, ['import', models]);
  assert.equal(result.status, 0, output(result));
  const state = f.state();
  const expected = { 'foo-bar': 'sk-for-one', foo_bar: 'sk-for-two', 'foo.bar': 'sk-for-three' };
  const seen = new Set();
  for (const [route, key] of Object.entries(expected)) {
    const ref = state.providers[route].apiKeyEnv;
    assert.ok(!seen.has(ref), `route ${route} reuses reference ${ref}`);
    seen.add(ref);
    assert.equal(state.credentials[ref], key, `route ${route} must authenticate with its own key`);
  }
  // The report names which route uses which reference.
  for (const route of Object.keys(expected)) {
    const line = result.stdout.split('\n').find((entry) => entry.trimStart().startsWith(`${route}  `));
    assert.ok(line, `the report must name route ${route}: ${result.stdout}`);
    assert.ok(line.includes(state.providers[route].apiKeyEnv), `the report must name the reference of ${route}`);
  }
  assert.match(result.stdout, /already used by foo-bar/, 'the disambiguation is explained');
  assert.ok(!output(result).includes('sk-for-'), 'a value is never printed');
});

test('end to end: a refused route write leaves every in-use credential byte-identical', (t) => {
  // The P1-2 regression: credentials used to be written before the routes, so a
  // refused route write left the existing route authenticating with the NEW key
  // against its OLD endpoint.
  const f = fixture(t, { state: {
    revision: 7,
    providers: {
      gateway: {
        api: 'openai-completions',
        baseURL: 'http://old-endpoint.test/v1',
        apiKeyEnv: 'GATEWAY_API_KEY',
        models: [{ id: 'm-one' }],
      },
    },
    credentials: { GATEWAY_API_KEY: 'sk-OLD-GOOD' },
  } });
  const before = f.stateBytes();
  const models = writeModels(f, {
    gateway: { api: 'openai-completions', baseUrl: 'http://new-endpoint.test/v1', apiKey: 'sk-NEW-WRONG', models: [{ id: 'm-one' }] },
  });
  const result = run(f, ['import', models], { env: { FAKE_DSH_MODE: 'mutate-refused' } });
  assert.notEqual(result.status, 0, output(result));

  // Byte-identical: same route, same endpoint, same reference, same value.
  assert.deepEqual(f.stateBytes(), before, 'the in-use configuration must not change at all');
  assert.equal(f.state().credentials.GATEWAY_API_KEY, 'sk-OLD-GOOD');

  // And the failure says so rather than implying a partial migration.
  const text = output(result);
  assert.match(text, /GATEWAY_API_KEY/);
  assert.match(text, /(unchanged|not written|No route was written)/i, text);
});

test('end to end: an import never changes the key a route it does not mention sends', (t) => {
  // P1-a, the shape the review reproduced: two routes deliberately share one
  // reference (a hand-edited document, or `--key-env` from an older build).
  // Importing ONE of them must leave the other authenticating with its own
  // secret — and its endpoint was never the thing at risk.
  const f = fixture(t, { state: {
    revision: 4,
    providers: {
      'foo-bar': {
        api: 'openai-completions', baseURL: 'http://one.test/v1', apiKeyEnv: 'FOO_BAR_API_KEY', models: [{ id: 'm-one' }],
      },
      foo_bar: {
        api: 'openai-completions', baseURL: 'http://two.test/v1', apiKeyEnv: 'FOO_BAR_API_KEY', models: [{ id: 'm-two' }],
      },
    },
    credentials: { FOO_BAR_API_KEY: 'sk-OLD' },
  } });
  const before = f.state().providers.foo_bar;
  const models = writeModels(f, {
    'foo-bar': { api: 'openai-completions', baseUrl: 'http://one.test/v1', apiKey: 'sk-NEW', models: [{ id: 'm-one' }] },
  });
  const result = run(f, ['import', models]);
  assert.equal(result.status, 0, output(result));
  const state = f.state();
  assert.equal(state.credentials.FOO_BAR_API_KEY, 'sk-OLD', 'the shared reference keeps its value');
  assert.deepEqual(state.providers.foo_bar, before, 'the untouched route is unchanged, endpoint included');
  assert.notEqual(state.providers['foo-bar'].apiKeyEnv, 'FOO_BAR_API_KEY', 'the imported route moved to a new reference');
  assert.equal(state.credentials[state.providers['foo-bar'].apiKeyEnv], 'sk-NEW');
  assert.ok(!output(result).includes('sk-OLD') && !output(result).includes('sk-NEW'), 'values are never printed');
});

test('end to end: a repeated import rotates the reference and keeps the old value', (t) => {
  // P2-d, now a stated contract rather than an accident: the second import
  // allocates the next free suffix, the previous reference keeps the value it
  // had, and the report says both.
  const f = fixture(t);
  const first = writeModels(f, {
    gateway: { api: 'openai-completions', baseUrl: 'http://gateway.test/v1', apiKey: 'sk-OLD', models: [{ id: 'm' }] },
  });
  const one = run(f, ['import', first]);
  assert.equal(one.status, 0, output(one));
  assert.equal(f.state().providers.gateway.apiKeyEnv, 'GATEWAY_API_KEY');
  assert.equal(f.state().credentials.GATEWAY_API_KEY, 'sk-OLD');

  fs.writeFileSync(first, JSON.stringify({ providers: {
    gateway: { api: 'openai-completions', baseUrl: 'http://gateway.test/v1', apiKey: 'sk-NEW', models: [{ id: 'm' }] },
  } }));
  const two = run(f, ['import', first]);
  assert.equal(two.status, 0, output(two));
  const state = f.state();
  assert.match(state.providers.gateway.apiKeyEnv, /^GATEWAY_API_KEY_\d+$/, 'the route rotates to a new reference');
  assert.notEqual(state.providers.gateway.apiKeyEnv, 'GATEWAY_API_KEY');
  assert.equal(state.credentials.GATEWAY_API_KEY, 'sk-OLD', 'the previous reference keeps its value');
  assert.equal(state.credentials[state.providers.gateway.apiKeyEnv], 'sk-NEW');
  assert.match(two.stdout, /GATEWAY_API_KEY → /, 'the report names the rotation');
  assert.match(two.stdout, /previous references keep their values/, 'the report says the old one is kept');
});

test('end to end: a failed route write leaves no reference this run created', (t) => {
  // A brand-new route's value is written first (the route must never exist
  // without one), so a refused route write has to take it back out — and only
  // it: the operator's own references keep their values.
  const f = fixture(t, { state: {
    revision: 1,
    providers: {},
    credentials: { OPERATOR_KEY: 'sk-OPERATOR', OPERATOR_KEY_2: 'sk-OPERATOR-2' },
  } });
  const models = writeModels(f, {
    fresh: { api: 'openai-completions', baseUrl: 'http://fresh.test/v1', apiKey: 'sk-fresh', models: [{ id: 'm' }] },
  });
  const result = run(f, ['import', models], { env: { FAKE_DSH_MODE: 'mutate-refused' } });
  assert.notEqual(result.status, 0, output(result));
  const state = f.state();
  assert.deepEqual(state.providers, {}, 'no route is written');
  assert.deepEqual(state.credentials, { OPERATOR_KEY: 'sk-OPERATOR', OPERATOR_KEY_2: 'sk-OPERATOR-2' },
    'the reference this run created is removed; nobody else\'s is');
  assert.match(output(result), /FRESH_API_KEY/, 'the report names the reference it took back');
  assert.ok(!output(result).includes('sk-fresh'), 'a value is never printed');
});

test('end to end: an import never removes a reference it did not create', (t) => {
  // P1-c by construction: the protocol no longer has a staging name, so no
  // predictable temporary is written and then deleted. A reference the operator
  // created under the old staging shape is untouched, and a successful import
  // removes nothing at all.
  const f = fixture(t, { state: {
    revision: 2,
    providers: {},
    credentials: {
      FOO_BAR_API_KEY: 'sk-OPERATOR-1',
      FOO_BAR_API_KEY__STAGING_424242: 'sk-OPERATOR-2',
      FOO_BAR_API_KEY_2: 'sk-OPERATOR-3',
    },
  } });
  const models = writeModels(f, {
    'foo-bar': { api: 'openai-completions', baseUrl: 'http://fresh.test/v1', apiKey: 'sk-fresh', models: [{ id: 'm' }] },
  });
  const result = run(f, ['import', models]);
  assert.equal(result.status, 0, output(result));
  const state = f.state();
  assert.equal(state.credentials.FOO_BAR_API_KEY__STAGING_424242, 'sk-OPERATOR-2',
    'a reference the import did not create survives byte for byte');
  assert.equal(state.credentials.FOO_BAR_API_KEY, 'sk-OPERATOR-1');
  assert.equal(state.credentials.FOO_BAR_API_KEY_2, 'sk-OPERATOR-3');
  const unsets = f.calls().filter((call) => call.method === 'vika/credentials/unset');
  assert.deepEqual(unsets, [], 'a successful import removes no credential at all');
  // A value nobody resolves is still somebody's secret: it is skipped, not
  // adopted and not overwritten.
  assert.equal(state.providers['foo-bar'].apiKeyEnv, 'FOO_BAR_API_KEY_3');
  assert.equal(state.credentials.FOO_BAR_API_KEY_3, 'sk-fresh');
});

test('end to end: values reach their reference before the routes that name it', (t) => {
  // I2's order is observable in the bridge traffic: the credential write for a
  // new reference comes before the mutate that points a route at it.
  const f = fixture(t);
  const models = writeModels(f, {
    gateway: { api: 'openai-completions', baseUrl: 'http://gateway.test/v1', apiKey: 'sk-new', models: [{ id: 'm' }] },
  });
  const result = run(f, ['import', models]);
  assert.equal(result.status, 0, output(result));
  const methods = f.calls().filter((call) => call.method).map((call) => call.method);
  const credentialAt = methods.indexOf('vika/credentials/set');
  const mutateAt = methods.indexOf('vika/settings/mutate');
  assert.ok(credentialAt >= 0 && mutateAt > credentialAt,
    `the value must exist before the route names it: ${methods.join(', ')}`);
  // And the route names exactly the reference that was written.
  const stored = f.calls().find((call) => call.method === 'vika/credentials/set');
  assert.equal(f.state().providers.gateway.apiKeyEnv, stored.params.ref);
});

test('end to end: an unconfirmed settings write keeps the new reference and says so', (t) => {
  // The one outcome the protocol must not guess about: the mutate reached DSH
  // and was applied, but the answer never came back. Deleting the new reference
  // would leave the route pointing at nothing.
  const f = fixture(t, { state: { revision: 0, providers: {}, credentials: {} } });
  const models = writeModels(f, {
    gateway: { api: 'openai-completions', baseUrl: 'http://gateway.test/v1', apiKey: 'sk-new', models: [{ id: 'm' }] },
  });
  const result = run(f, ['import', models], { env: { FAKE_DSH_MODE: 'mutate-uncertain' } });
  assert.notEqual(result.status, 0, output(result));
  const state = f.state();
  assert.equal(state.providers.gateway.apiKeyEnv, 'GATEWAY_API_KEY', 'the write landed');
  assert.equal(state.credentials.GATEWAY_API_KEY, 'sk-new', 'the value it points at is kept, not rolled back');
  const text = output(result);
  assert.match(text, /Cannot tell whether this import landed/, 'the uncertainty is stated, not hidden');
  assert.match(text, /GATEWAY_API_KEY/, 'the reference to inspect is named');
  assert.match(text, /ov-superagent dsh model list/, 'the operator is told how to check');
  assert.ok(!text.includes('sk-new'), 'a value is never printed');
});

test('end to end: a concurrent write is not overwritten by a re-planned import', (t) => {
  // P1-b: another process commits a route and a credential between the import's
  // read and its mutate. The retry must rebuild the plan from the new state —
  // the competitor's route keeps its reference and its value — instead of
  // replaying the stale operations, which silently re-pointed the competitor's
  // route and overwrote the value it had just stored.
  const f = fixture(t);
  const models = writeModels(f, {
    foo_bar: { api: 'openai-completions', baseUrl: 'http://two.test/v1', apiKey: 'sk-IMPORT', models: [{ id: 'm-under' }] },
    'foo.bar': { api: 'openai-completions', baseUrl: 'http://three.test/v1', apiKey: 'sk-DOT', models: [{ id: 'm-dot' }] },
  });
  const result = run(f, ['import', models], { env: { FAKE_DSH_MODE: 'conflict-after-competitor' } });
  assert.equal(result.status, 0, output(result));
  const competitor = f.competitor();
  assert.ok(competitor, 'the competing writer really ran');
  assert.equal(competitor.addStatus, 0, `the competitor's add must succeed: ${competitor.addOut}`);
  assert.equal(competitor.keyStatus, 0, `the competitor's key set must succeed: ${competitor.keyOut}`);

  const state = f.state();
  const competitorRef = state.providers.foo_bar_extra.apiKeyEnv;
  assert.equal(state.credentials[competitorRef], 'sk-COMPETITOR', 'the concurrent value is not overwritten');
  assert.deepEqual(state.providers.foo_bar_extra.models.map((model) => model.id), ['m-under'],
    'the concurrent route keeps its model');
  // The import still lands, on references the competitor is not using.
  for (const route of ['foo_bar', 'foo.bar']) {
    assert.ok(state.providers[route], `route ${route} was imported`);
    assert.notEqual(state.providers[route].apiKeyEnv, competitorRef,
      `route ${route} must not resolve the competitor's reference`);
  }
  assert.equal(state.credentials[state.providers.foo_bar.apiKeyEnv], 'sk-IMPORT');
  assert.equal(state.credentials[state.providers['foo.bar'].apiKeyEnv], 'sk-DOT');
});

test('end to end: a still-conflicting settings write aborts instead of reporting success', (t) => {
  // I3's other half: the retry is one attempt, and a second conflict is a
  // failure. Reporting success there is what made the pre-fix retry dangerous.
  const f = fixture(t, { state: { revision: 0, providers: {}, credentials: {} } });
  const models = writeModels(f, {
    gateway: { api: 'openai-completions', baseUrl: 'http://gateway.test/v1', apiKey: 'sk-new', models: [{ id: 'm' }] },
  });
  const result = run(f, ['import', models], { env: { FAKE_DSH_MODE: 'conflict-always' } });
  assert.notEqual(result.status, 0, output(result));
  assert.match(output(result), /still conflicted on retry/);
  assert.deepEqual(f.state().providers, {}, 'no route is written');
  assert.deepEqual(f.state().credentials, {}, 'the reference this run created is removed again');
});

test('end to end: a refused credential means no route points at a value it lacks', (t) => {
  // The invariant the old mismatch report was documenting: with the value
  // written first, a refusal cannot leave a route pointing at an empty
  // reference — the routes were never written at all.
  const f = fixture(t, { state: {
    revision: 2,
    providers: {
      gateway: {
        api: 'openai-completions', baseURL: 'http://old.test/v1', apiKeyEnv: 'GATEWAY_API_KEY', models: [{ id: 'm' }],
      },
    },
    credentials: { GATEWAY_API_KEY: 'sk-OLD' },
  } });
  const before = f.stateBytes();
  const models = writeModels(f, {
    gateway: { api: 'openai-completions', baseUrl: 'http://new.test/v1', apiKey: 'sk-NEW', models: [{ id: 'm' }] },
  });
  const result = run(f, ['import', models], {
    env: { FAKE_DSH_MODE: 'credential-refused', FAKE_DSH_REFUSE_REF: 'GATEWAY_API_KEY' },
  });
  assert.notEqual(result.status, 0, output(result));
  assert.deepEqual(f.stateBytes(), before, 'the in-use configuration is byte-identical');
  const text = output(result);
  assert.match(text, /No route was written/, 'the outcome is stated');
  assert.match(text, /GATEWAY_API_KEY_2/, 'the reference it had to take back is named');
  assert.match(text, /in-use configuration was not changed/i, 'the invariant is stated');
  assert.ok(!text.includes('sk-OLD') && !text.includes('sk-NEW'), 'values are never printed');
});

test('end to end: a split route keeps one credential shared by its parts', (t) => {
  const f = fixture(t);
  const models = writeModels(f, {
    dual: { apiKey: 'sk-shared', models: [
      { id: 'chat', api: 'openai-completions', baseUrl: 'http://one.test/v1' },
      { id: 'messages', api: 'anthropic-messages', baseUrl: 'http://two.test/v1' },
    ] },
  });
  const result = run(f, ['import', models]);
  assert.equal(result.status, 0, output(result));
  const state = f.state();
  assert.deepEqual(Object.keys(state.providers).sort(), ['dual', 'dual--2']);
  assert.equal(state.providers.dual.baseURL, 'http://one.test/v1');
  assert.equal(state.providers['dual--2'].baseURL, 'http://two.test/v1');
  assert.equal(state.providers.dual.apiKeyEnv, state.providers['dual--2'].apiKeyEnv,
    'the two halves are the same provider, so they share one reference');
  assert.equal(state.credentials[state.providers.dual.apiKeyEnv], 'sk-shared');
  assert.match(result.stdout, /split into 2 routes/);
});

test('end to end: an import keeps the reference of a route it gives no credential', (t) => {
  const f = fixture(t, { state: {
    revision: 3,
    providers: {
      gateway: {
        api: 'openai-completions', baseURL: 'http://old.test/v1', apiKeyEnv: 'R_API_KEY', models: [{ id: 'old-model' }],
      },
    },
    credentials: { R_API_KEY: 'sk-STORED' },
  } });
  const models = writeModels(f, {
    gateway: { api: 'openai-completions', baseUrl: 'http://new.test/v1', models: [{ id: 'new-model' }] },
  });
  const result = run(f, ['import', models]);
  assert.equal(result.status, 0, output(result));
  const state = f.state();
  assert.equal(state.providers.gateway.apiKeyEnv, 'R_API_KEY', 'the route keeps authenticating with its stored key');
  assert.equal(state.providers.gateway.baseURL, 'http://new.test/v1', 'and the document still updates the endpoint');
  assert.equal(state.credentials.R_API_KEY, 'sk-STORED', 'the value is not rewritten');
  assert.deepEqual(f.calls().filter((call) => call.method === 'vika/credentials/set'), [],
    'nothing is written to the kept reference');
  assert.match(result.stdout, /declares no credential/, 'the report says which case this is');
  assert.match(result.stdout, /R_API_KEY/, 'the report names the reference it kept');
});

test('a dry run of a split route starts no process and prints both routes', (t) => {
  const f = fixture(t);
  const models = writeModels(f, {
    dual: { models: [
      { id: 'chat', api: 'openai-completions', baseUrl: 'http://one.test/v1', apiKey: 'sk-a' },
      { id: 'messages', api: 'anthropic-messages', baseUrl: 'http://two.test/v1', apiKey: 'sk-a' },
    ] },
  });
  const result = run(f, ['import', models, '--dry-run']);
  assert.equal(result.status, 0, output(result));
  assert.equal(f.calls().length, 0, 'a dry run starts no DSH process');
  assert.match(result.stdout, /Would write 2 route\(s\)/);
  assert.match(result.stdout, /"dual--2"/);
  assert.match(result.stdout, /allocates \*new\* references/, 'the dry run does not promise the names it prints');
  assert.ok(!output(result).includes('sk-a'), 'a dry run never prints a credential');
});

test('an import skips a route name add and remove would refuse', (t) => {
  // The asymmetry was a trap: import wrote the key, so the route served, and
  // `remove` rejected the very name it had been stored under -- the route could
  // only be deleted by hand-editing cordis.patch.yml.
  const f = fixture(t);
  const modelsJson = path.join(f.home, 'models.json');
  fs.writeFileSync(modelsJson, JSON.stringify({ providers: {
    'bad route!': { api: 'openai-completions', baseUrl: 'http://bad.test/v1', models: [{ id: 'm1' }] },
    'ok-route': { api: 'openai-completions', baseUrl: 'http://ok.test/v1', apiKey: 'sk-ok', models: [{ id: 'm2' }] },
  } }));

  const imported = run(f, ['import', modelsJson]);
  assert.equal(imported.status, 0, output(imported));
  assert.deepEqual(Object.keys(f.state().providers), ['ok-route'],
    'only the route every command can address is written');
  assert.match(imported.stdout, /bad route!/, 'the skipped route is named');
  assert.match(imported.stdout, /route id 不合法|invalid route name/, 'and the reason is given');

  // The route that was written is one `remove` accepts.
  const removed = run(f, ['remove', 'ok-route']);
  assert.equal(removed.status, 0, output(removed));
  assert.deepEqual(Object.keys(f.state().providers), []);
});
