// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
// A DeepSeek Harness runtime with no route that serves a model, reached through
// `ovsa setup`. The wizard adds one route, the draft stays in memory until the
// instance is saved, and the manifest names the route fully qualified.
//
// The probe and the draft check are the only pieces faked: no real `dsh` and no
// network are involved. The write itself is the real one (a fresh DSH home has
// no document to merge with, so it needs no settings service).

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { DEFAULT_RUNTIME_ID, resolveRuntimeModelTarget } from '../../daemon/src/runtime-catalog.mjs';
import { Prompter } from '../src/prompt.mjs';
import { setupCommand } from '../src/setup.mjs';
import { validateDshModelDraft, writeDshModelDraft } from '../src/dsh-model-wizard.mjs';

// A DeepSeek Harness detected in a throwaway home: the protocol works, no route
// serves a model. This is the state setup used to accept silently.
function dshRuntime(overrides = {}) {
  return {
    id: 'dsh', label: 'DeepSeek Harness', model: 'dsh',
    bin: '/opt/dsh/bin/dsh', binEnv: 'DSH_BIN', installed: true, available: true,
    route: '', fallbackRoute: 'deepseek-official/deepseek-flash',
    note: 'SDK bridge compatible (0.2.2-rc.1); the built-in route deepseek-official/deepseek-flash needs DEEPSEEK_API_KEY',
    ...overrides,
  };
}

const OTHER_RUNTIMES = [
  { id: 'codex', label: 'Codex', model: 'codex:', bin: '/opt/codex/bin/codex', binEnv: 'CODEX_BIN', available: false, note: 'not installed' },
];

// The real Prompter, driven by a script. `tty` is what the wizard reads to
// decide whether it may ask; the answers here replace the key-by-key reader so
// a test can script a terminal without a terminal.
class ScriptedPrompter extends Prompter {
  constructor({ input, output, script, onAsk = () => {} }) {
    super({ input, output });
    this.tty = true;
    this.fancy = false;
    this.script = script;
    this.questions = [];
    this.choices = new Map();
    this.onAsk = onAsk;
  }

  scripted(question, choices = []) {
    this.asked += 1;
    this.questions.push(question);
    this.choices.set(question, choices);
    this.onAsk(question);
    const hit = this.script.find((entry) => entry.match.test(question));
    if (!hit) throw new Error(`unscripted question: ${question}`);
    return typeof hit.answer === 'function' ? hit.answer(choices) : hit.answer;
  }

  async text(question, { initial = '' } = {}) {
    const value = await this.scripted(question);
    return String(value ?? '') === '' ? initial : String(value);
  }

  async confirm(question) {
    return Boolean(await this.scripted(question));
  }

  async select(question, choices) {
    const value = await this.scripted(question, choices);
    return typeof value === 'number' ? choices[value].value : value;
  }

  async multiselect(question, choices) {
    const value = await this.scripted(question, choices);
    return Array.isArray(value) ? value : [value];
  }

  async secret(question) {
    return String((await this.scripted(question)) ?? '');
  }
}

function harness(t, { runtimes, detectDsh, script = [], answers = [], onAsk = () => {} } = {}) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-setup-dsh-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const input = new PassThrough();
  const output = new PassThrough();
  let written = '';
  output.on('data', (chunk) => { written += chunk; });
  const prompter = script.length
    ? new ScriptedPrompter({ input, output, script, onAsk })
    : new Prompter({ input, output });
  if (!script.length) input.end(answers.map((line) => `${line}\n`).join(''));
  const calls = [];
  const dsh = {
    probe: async ({ env, dataDir }) => {
      calls.push(['probe', dataDir]);
      return detectDsh({ env, dataDir });
    },
    validate: async ({ draft }) => {
      calls.push(['validate', draft]);
      return { ok: true, route: `${draft.route}/${draft.model}` };
    },
  };
  const options = {
    prompter,
    env: { HOME: home, PATH: '', LARK_CLI_BIN: '/opt/lark/bin/lark-cli', VIKA_LANG: 'en' },
    homeDir: home,
    detect: {
      runtimes: async () => runtimes,
      openViking: async () => ({ configPath: null, url: 'http://127.0.0.1:1933', usable: true }),
      dsh: async (args) => dsh.probe(args),
    },
    dshModels: { validate: dsh.validate },
    configureOV: async ({ preferredConfigPath, detect, ...rest }) => {
      const detected = await detect({ ...rest, configPath: preferredConfigPath });
      return preferredConfigPath ? { ...detected, configPath: preferredConfigPath } : detected;
    },
    connect: {
      qq: async ({ plan }) => { calls.push(['qq', plan.dataDir]); },
      telegram: async ({ plan }) => { calls.push(['telegram', plan.dataDir]); },
      lark: async ({ plan, profile }) => { calls.push(['lark', plan.id, profile]); return { profile, ownerOpenId: 'ou_owner' }; },
      weixin: async ({ plan }) => { calls.push(['weixin', plan.dataDir]); },
      greet: async (message) => { calls.push(['greet', message.userId]); },
    },
    control: {
      start: async (target) => { calls.push(['start', target]); },
      restart: async (target) => { calls.push(['restart', target]); },
      remove: async (targets) => { calls.push(['remove', targets]); },
    },
    listen: async ({ port }) => ({ wait: async () => true }),
    allocate: async () => 3738,
  };
  return {
    home, options, prompter, calls, written: () => written,
    dataDir: path.join(home, '.ovsa', 'data', 'primary'),
    manifest: () => JSON.parse(fs.readFileSync(path.join(home, '.ovsa', 'config.json'), 'utf8')),
  };
}

// Every answer the DSH questions need, in a terminal flow, with `weixin` as the
// only IM (no Feishu scan, so the DSH questions are the only interactive ones).
function dshAnswers({ add = true, pasteKey = 'sk-test-only' } = {}) {
  return [
    { match: /Bot name/, answer: 'OpenViking SuperAgent' },
    { match: /Its role/, answer: 'colleague' },
    { match: /Workspace/, answer: '' },
    { match: /Agent runtime/, answer: (choices) => choices.find((choice) => choice.value?.id === 'dsh').value },
    { match: /Add a model route now\?/, answer: add },
    // The route id is derived from this host (`api.deepseek.com` -> `deepseek`),
    // so the four answers below are the whole wizard.
    { match: /Gateway base URL/, answer: 'https://api.deepseek.com/v1' },
    { match: /^Protocol$/, answer: 'openai-completions' },
    { match: /Model id/, answer: 'deepseek-chat' },
    { match: /^Credential$/, answer: 'paste' },
    { match: /Key value/, answer: pasteKey },
    { match: /Write it this way\?/, answer: true },
    { match: /Which IMs/, answer: ['weixin'] },
    { match: /Start scanning\?/, answer: true },
  ];
}

test('choosing DSH with no usable route offers the add-model step and writes it at save', async (t) => {
  const h = harness(t, { runtimes: [dshRuntime(), ...OTHER_RUNTIMES], detectDsh: () => null, script: dshAnswers() });
  assert.equal(await setupCommand(h.options), 0);

  // The wizard asked what it needs, in the order the fields are needed.
  const asked = h.prompter.questions;
  assert.ok(asked.some((question) => /Add a model route now\?/.test(question)), asked.join('\n'));
  const fields = ['Gateway base URL', 'Protocol', 'Model id', 'Credential', 'Key value', 'Write it this way?'];
  const positions = fields.map((field) => asked.findIndex((question) => new RegExp(field).test(question)));
  assert.deepEqual([...positions].sort((a, b) => a - b), positions, `field order: ${asked.join(' | ')}`);

  // The draft stayed in memory until the instance was saved (see the dedicated
  // test): nothing here was written into the instance home ahead of time.
  // The manifest names the route fully qualified -- `dsh`, and a bare
  // `provider/model`, would both resolve somewhere else (see the trap test).
  const manifest = h.manifest();
  assert.equal(manifest.agents[0].model, 'dsh:deepseek/deepseek-chat');
  assert.deepEqual(manifest.env, { DSH_BIN: '/opt/dsh/bin/dsh' });

  // The Cordis patch holds the route, and the credential holds the pasted key.
  const settings = path.join(h.dataDir, 'dsh', 'home', 'profiles', 'sdk', 'cordis.patch.yml');
  const document = JSON.parse(fs.readFileSync(settings, 'utf8'));
  assert.deepEqual(document.map((row) => row.id), ['llm-pi-ai']);
  const profile = document[0].config.providers.deepseek;
  assert.equal(profile.api, 'openai-completions');
  assert.equal(profile.baseURL, 'https://api.deepseek.com/v1');
  assert.equal(profile.displayName, 'deepseek', 'the display name defaults to the derived route');
  assert.equal(profile.apiKeyEnv, 'DEEPSEEK_API_KEY');
  assert.deepEqual(profile.models.map((model) => model.id), ['deepseek-chat']);
  const credentials = path.join(h.dataDir, 'dsh', 'home', '.credentials.yaml');
  assert.match(fs.readFileSync(credentials, 'utf8'), /^version: 1\nrefs:\n {2}DEEPSEEK_API_KEY: "sk-test-only"\n$/);
  assert.equal(fs.statSync(credentials).mode & 0o777, 0o600, 'the credential document is private');

  // The draft was checked in a throwaway home before anything was written.
  const validation = h.calls.find((call) => call[0] === 'validate');
  assert.deepEqual(validation[1], {
    route: 'deepseek', model: 'deepseek-chat', api: 'openai-completions',
    baseURL: 'https://api.deepseek.com/v1', name: 'deepseek', keyEnv: 'DEEPSEEK_API_KEY', keyValue: 'sk-test-only',
  });
});

test('the draft is not written to the instance home before the instance is saved', async (t) => {
  let existingAtSavePrompt;
  const h = harness(t, {
    runtimes: [dshRuntime(), ...OTHER_RUNTIMES],
    detectDsh: () => null,
    script: dshAnswers(),
    onAsk: (question) => {
      if (/Start scanning\?/.test(question)) existingAtSavePrompt = fs.existsSync(h.dataDir);
    },
  });
  assert.equal(await setupCommand(h.options), 0);
  assert.equal(existingAtSavePrompt, false, 'nothing under the instance dataDir before it is saved');
  assert.equal(fs.existsSync(h.dataDir), true, 'the instance exists once it is saved');
});

test('declining the offer keeps the old note and prints the menu to open later', async (t) => {
  const h = harness(t, { runtimes: [dshRuntime(), ...OTHER_RUNTIMES], detectDsh: () => null, script: dshAnswers({ add: false }) });
  assert.equal(await setupCommand(h.options), 0);

  // Nothing about a route was asked after the refusal, and nothing was written.
  assert.equal(h.prompter.questions.some((question) => /Gateway base URL/.test(question)), false);
  assert.equal(h.manifest().agents[0].model, 'dsh', 'today\'s value is kept');
  assert.equal(fs.existsSync(path.join(h.dataDir, 'dsh')), false);
  // Adding is the menu's job now: the hint is the command that opens it, with
  // the instance named, not a flag form the CLI no longer has.
  assert.match(h.written(), /dsh model --instance primary/, 'the command to open the menu is shown');
  assert.match(h.written(), /"Add a model"/, 'and which menu entry to pick');
  assert.doesNotMatch(h.written(), /dsh model add /);
});

test('without a terminal, setup prints the hint and continues instead of asking', async (t) => {
  const h = harness(t, {
    runtimes: [dshRuntime()],
    detectDsh: () => null,
    // A line-driven Prompter (no isTTY): the wizard must not add a question.
    // name, role, workspace, runtime, IMs, confirm
    answers: ['小V', '', '', '1', '2', 'y'],
  });
  assert.equal(await setupCommand(h.options), 0);
  assert.match(h.written(), /ovsa dsh model --instance/);
  assert.doesNotMatch(h.written(), /ovsa dsh model add /);
  assert.equal(h.manifest().agents[0].model, 'dsh');
});

test('editing an instance that already has DSH routes probes its own home and does not prompt', async (t) => {
  // First, a normal setup that declined the offer: an instance with no routes.
  const h = harness(t, { runtimes: [dshRuntime(), ...OTHER_RUNTIMES], detectDsh: () => null, script: dshAnswers({ add: false }) });
  assert.equal(await setupCommand(h.options), 0);
  // An instance that runs keeps its DSH home; this one never got a route.
  fs.mkdirSync(path.join(h.dataDir, 'dsh', 'home'), { recursive: true, mode: 0o700 });

  // Editing it: detection still sees a throwaway home (no route), but the
  // instance's own home serves one, so setup must not offer the wizard.
  const probes = [];
  let menuCalls = 0;
  const script = [
    { match: /What to do/, answer: (choices) => choices.find((choice) => choice.value?.id === 'primary').value },
    {
      match: /Change OpenViking SuperAgent \(primary\)/,
      answer: (choices) => {
        menuCalls += 1;
        // First the "Agent runtime" row, then give up on the changes.
        return menuCalls === 1
          ? choices.find((choice) => choice.label.trim() === 'Agent runtime').value
          : choices.find((choice) => choice.value === 'quit').value;
      },
    },
    { match: /^Agent runtime$/, answer: (choices) => choices.find((choice) => choice.value?.route).value },
    { match: /Add a model route now\?/, answer: false },
  ];
  const output = new PassThrough();
  output.on('data', () => {});
  h.options.prompter = new ScriptedPrompter({ input: new PassThrough(), output, script });
  h.options.detect.dsh = async ({ dataDir }) => {
    probes.push(dataDir);
    return dshRuntime({ route: 'deepseek/deepseek-chat', note: 'model route deepseek/deepseek-chat' });
  };
  assert.equal(await setupCommand(h.options), 0);

  assert.deepEqual(probes, [h.dataDir], 'the instance home was probed');
  assert.equal(h.options.prompter.questions.some((question) => /Add a model route now\?/.test(question)), false);
  const runtimeQuestion = h.options.prompter.questions.find((question) => /^Agent runtime$/.test(question));
  const choices = h.options.prompter.choices.get(runtimeQuestion);
  assert.match(choices.find((choice) => choice.value?.id === 'dsh').hint, /deepseek\/deepseek-chat/);
});

test('saving an instance whose own home serves a route replaces the bare dsh alias with the qualified id', async (t) => {
  // The state the owner's machine was in: the manifest says `dsh`, but the
  // instance's own DSH home serves a custom route. Setup must not keep the
  // alias, because `dsh` resolves to the built-in route that serves nothing.
  const h = harness(t, { runtimes: [dshRuntime(), ...OTHER_RUNTIMES], detectDsh: () => null, script: dshAnswers({ add: false }) });
  assert.equal(await setupCommand(h.options), 0);
  assert.equal(h.manifest().agents[0].model, 'dsh');
  fs.mkdirSync(path.join(h.dataDir, 'dsh', 'home'), { recursive: true, mode: 0o700 });

  let menuCalls = 0;
  const script = [
    { match: /What to do/, answer: (choices) => choices.find((choice) => choice.value?.id === 'primary').value },
    {
      match: /Change OpenViking SuperAgent \(primary\)/,
      answer: (choices) => {
        menuCalls += 1;
        // Rename it (a change to save), then save.
        if (menuCalls === 1) return choices.find((choice) => choice.label.trim() === 'Bot name').value;
        return choices.find((choice) => choice.value === 'save').value;
      },
    },
    { match: /Bot name/, answer: 'OpenViking SuperAgent Two' },
  ];
  const output = new PassThrough();
  output.on('data', () => {});
  h.options.prompter = new ScriptedPrompter({ input: new PassThrough(), output, script });
  h.options.detect.dsh = async () => dshRuntime({ route: 'deepseek/deepseek-chat' });
  assert.equal(await setupCommand(h.options), 0);

  const manifest = h.manifest();
  assert.equal(manifest.agents[0].displayName, 'OpenViking SuperAgent Two');
  assert.equal(manifest.agents[0].model, 'dsh:deepseek/deepseek-chat');
  assert.equal(resolveRuntimeModelTarget(manifest.agents[0].model).runtime, 'dsh');
});

test('a bare provider/model would start the default runtime, so the manifest gets the qualified id', () => {
  // The second trap on this path: no runtime owns a bare `provider/model`, and
  // resolveRuntimeModelTarget falls through to the DEFAULT runtime (codex).
  assert.equal(resolveRuntimeModelTarget('deepseek/deepseek-chat').runtime, DEFAULT_RUNTIME_ID);
  const qualified = resolveRuntimeModelTarget('dsh:deepseek/deepseek-chat');
  assert.equal(qualified.runtime, 'dsh');
  assert.equal(qualified.model, 'deepseek/deepseek-chat');
});

test('the draft check seeds a throwaway DSH home, then removes it', async (t) => {
  const draft = {
    route: 'deepseek', model: 'deepseek-chat', api: 'openai-completions',
    baseURL: 'http://127.0.0.1:9/v1', name: 'DeepSeek', keyEnv: 'DEEPSEEK_API_KEY', keyValue: 'sk-test-only',
  };
  let seen = null;
  const result = await validateDshModelDraft({
    bin: '/opt/dsh/bin/dsh',
    env: { PATH: '' },
    draft,
    probe: async (bin, { dataDir }) => {
      seen = {
        bin,
        dataDir,
        settings: JSON.parse(fs.readFileSync(path.join(dataDir, 'dsh', 'home', 'profiles', 'sdk', 'cordis.patch.yml'), 'utf8')),
        credentials: fs.readFileSync(path.join(dataDir, 'dsh', 'home', '.credentials.yaml'), 'utf8'),
      };
      return { compatible: true, route: 'deepseek/deepseek-chat', routes: [{ provider: 'deepseek', models: ['deepseek-chat'], credential: true, error: '' }] };
    },
  });
  assert.equal(result.ok, true, result.reason || result.note);
  assert.equal(seen.bin, '/opt/dsh/bin/dsh');
  assert.equal(seen.settings[0].config.providers.deepseek.apiKeyEnv, 'DEEPSEEK_API_KEY');
  assert.match(seen.credentials, /DEEPSEEK_API_KEY: "sk-test-only"/);
  assert.equal(fs.existsSync(seen.dataDir), false, 'the throwaway home is removed');
});

test('the draft check reports a route DSH did not offer', async () => {
  const draft = { route: 'deepseek', model: 'deepseek-chat', api: 'openai-completions', baseURL: 'http://127.0.0.1:9/v1', name: null, keyEnv: 'DEEPSEEK_API_KEY', keyValue: null };
  const result = await validateDshModelDraft({
    bin: 'dsh',
    env: {},
    draft,
    probe: async () => ({ compatible: true, route: '', routes: [{ provider: 'deepseek', models: [], credential: false, error: '' }], note: 'deepseek: 0 models' }),
  });
  assert.equal(result.ok, false);
  assert.match(result.note, /0 models/);
});

test('a fresh DSH home is seeded with both documents, and only the route it was given', async (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-setup-dsh-write-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const written = await writeDshModelDraft({
    dataDir,
    draft: { route: 'deepseek', model: 'deepseek-chat', api: 'openai-completions', baseURL: 'http://127.0.0.1:9/v1', name: 'DeepSeek', keyEnv: 'OTHER_KEY', keyValue: 'sk-test-only' },
  });
  assert.equal(written.via, 'file');
  assert.equal(written.keyRef, 'OTHER_KEY');
  assert.equal(fs.statSync(written.settings).mode & 0o777, 0o600);
  assert.equal(fs.statSync(written.credentials).mode & 0o777, 0o600);
  const document = JSON.parse(fs.readFileSync(written.settings, 'utf8'));
  assert.deepEqual(Object.keys(document[0].config.providers), ['deepseek']);
});

test('an instance home that already has a settings document keeps every other route', async (t) => {
  // The merge path: once DSH has written the document, setup must not replace
  // it. It goes through the settings service, the same upsert the model menu
  // performs, so an existing route and its key survive.
  const { FAKE_DSH } = await import('../../daemon/test/cli-fixture.mjs');
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-setup-dsh-merge-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const document = path.join(dataDir, 'dsh', 'home', 'profiles', 'sdk', 'cordis.patch.yml');
  fs.mkdirSync(path.dirname(document), { recursive: true, mode: 0o700 });
  fs.writeFileSync(document, `${JSON.stringify([{
    id: 'llm-pi-ai',
    config: { providers: { existing: { api: 'openai-completions', baseURL: 'http://127.0.0.1:8/v1', apiKeyEnv: 'EXISTING_API_KEY', models: [{ id: 'kept-model' }] } } },
  }])}\n`, { mode: 0o600 });
  const bin = path.join(dataDir, 'fake-dsh.mjs');
  fs.copyFileSync(FAKE_DSH, bin);
  fs.chmodSync(bin, 0o755);

  const written = await writeDshModelDraft({
    dataDir,
    bin,
    env: { PATH: process.env.PATH, DSH_HOME: '' },
    draft: { route: 'deepseek', model: 'deepseek-chat', api: 'openai-completions', baseURL: 'http://127.0.0.1:9/v1', name: 'DeepSeek', keyEnv: 'DEEPSEEK_API_KEY', keyValue: 'sk-test-only' },
  });
  assert.equal(written.via, 'service');
  const providers = JSON.parse(fs.readFileSync(document, 'utf8'))[0].config.providers;
  assert.deepEqual(Object.keys(providers).sort(), ['deepseek', 'existing']);
  assert.equal(providers.existing.apiKeyEnv, 'EXISTING_API_KEY');
  assert.equal(providers.deepseek.api, 'openai-completions');
  assert.equal(written.keyRef, 'DEEPSEEK_API_KEY');
  assert.match(fs.readFileSync(path.join(dataDir, 'dsh', 'home', '.credentials.yaml'), 'utf8'), /DEEPSEEK_API_KEY: "?sk-test-only"?/);
});
