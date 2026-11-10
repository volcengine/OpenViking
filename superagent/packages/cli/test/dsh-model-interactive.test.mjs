// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
// The interactive surface of `vika dsh model`: the menu a bare command opens in
// a terminal, its add wizard, and the edit flow that reuses it.
//
// The prompter is scripted (a terminal without a terminal), the DSH side is the
// repository's reusable fake `dsh`, and every call goes through the real
// `dshModelCommand`, so argument dispatch, the control process and the files on
// disk are what is measured. The non-terminal cases are compared byte for byte
// against the plain list path they must keep.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { PassThrough } from 'node:stream';
import { test } from '../../daemon/test/dsh-node-gate.mjs';
import { createCliFixture } from '../../daemon/test/cli-fixture.mjs';
import { dshModelCommand, modelIdProblem } from '../src/dsh-model.mjs';
import { guessProtocol } from '../src/dsh-model-wizard.mjs';
import { setLanguage } from '../src/i18n.mjs';

setLanguage('en');

// A terminal-shaped prompter driven by a script. `answer` may be a value, a
// function of the choices, or a function of `(choices, count)` when the same
// question comes back (the menu loop). Anything unscripted is a test failure,
// and `tty` is what the command reads to decide whether it may ask at all.
class ScriptedPrompter {
  constructor(script = [], { tty = true } = {}) {
    this.tty = tty;
    this.navigable = false;
    this.script = script;
    this.questions = [];
    this.choices = new Map();
    this.options = new Map();
    this.notes = [];
    this.asked = 0;
    this.counts = new Map();
  }

  scripted(question, choices = []) {
    this.asked += 1;
    this.questions.push(question);
    this.choices.set(question, choices);
    const hit = this.script.find((entry) => entry.match.test(question));
    if (!hit) throw new Error(`unscripted question: ${question}`);
    const count = (this.counts.get(hit) ?? 0) + 1;
    this.counts.set(hit, count);
    return typeof hit.answer === 'function' ? hit.answer(choices, count) : hit.answer;
  }

  async text(question, { initial = '' } = {}) {
    const value = await this.scripted(question);
    return String(value ?? '') === '' ? initial : String(value);
  }

  async confirm(question) {
    return Boolean(await this.scripted(question));
  }

  async select(question, choices, options = {}) {
    this.options.set(question, options);
    const value = await this.scripted(question, choices);
    return typeof value === 'number' ? choices[value].value : value;
  }

  async secret(question) {
    return String((await this.scripted(question)) ?? '');
  }

  spin(text) {
    this.questions.push(`spin:${text}`);
    return { succeed: () => {}, fail: () => {}, stop: () => {}, warn: () => {}, update: () => {} };
  }

  info(text) { this.notes.push(text); }
  warn(text) { this.notes.push(text); }
  write(text) { this.notes.push(text); }
}

function streams() {
  const state = { out: '', err: '' };
  return {
    state,
    stdout: { write: (chunk) => { state.out += chunk; return true; } },
    stderr: { write: (chunk) => { state.err += chunk; return true; } },
  };
}

async function run(f, argv, { script = [], tty = true, env = {} } = {}) {
  const io = streams();
  const prompter = new ScriptedPrompter(script, { tty });
  const code = await dshModelCommand(argv, {
    env: { ...f.env, ...env },
    stdin: new PassThrough(),
    stdout: io.stdout,
    stderr: io.stderr,
    prompter,
  });
  return { code, out: io.state.out, err: io.state.err, prompter };
}

// Write an instance's settings document directly. `vika dsh model add` no
// longer exists, so tests that need a pre-existing route seed the document the
// same way a fresh home is seeded (JSON is valid YAML) instead of asking the
// wizard first.
function seedDocument(f, id, providers) {
  const file = path.join(f.dataDirOf(id), 'dsh', 'home', 'profiles', 'sdk', 'cordis.patch.yml');
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  fs.writeFileSync(file, `${JSON.stringify([{ id: 'llm-pi-ai', config: { providers } }])}\n`, { mode: 0o600 });
  return file;
}

const DEEPSEEK_PROFILE = {
  api: 'openai-completions',
  baseURL: 'https://api.deepseek.com/v1',
  apiKeyEnv: 'DEEPSEEK_API_KEY',
  displayName: 'DeepSeek',
  models: [{
    id: 'deepseek-chat',
    name: 'DeepSeek Chat',
    input: ['text', 'image'],
    contextWindow: 1000000,
    maxTokens: 384000,
  }],
};

// The four wizard questions and the summary, in the order they are asked. The
// route (`deepseek`) is derived from the host, not asked for.
function wizardAnswers({
  baseURL = 'https://api.deepseek.com/v1',
  protocol = 'openai-completions',
  model = 'deepseek-chat',
  credential = 'paste',
  key = 'sk-test-only',
  reference = 'DEEPSEEK_API_KEY',
  confirm = true,
  keepAnyway = null,
} = {}) {
  const script = [
    { match: /Gateway base URL/, answer: baseURL },
    { match: /^Protocol$/, answer: protocol },
    { match: /Model id/, answer: model },
    { match: /^Credential$/, answer: credential },
  ];
  if (credential === 'paste') script.push({ match: /Key value/, answer: key });
  else script.push({ match: /Credential reference/, answer: reference });
  // A reference with no value cannot answer yet, so the check reports it; the
  // caller decides whether the draft is kept regardless.
  if (keepAnyway !== null) script.push({ match: /Keep this route anyway\?/, answer: keepAnyway });
  script.push({ match: /Write it this way\?/, answer: confirm });
  return script;
}

// Drive one menu action and leave. The first "What to do" picks the action, the
// last one quits; the counts let the same script cover the redraw.
function menuScript(action, extra = []) {
  return [
    { match: /^What to do$/, answer: (choices, count) => (count === 1 ? action : 'quit') },
    ...extra,
  ];
}

async function menuAdd(f, { id = 'qa', ...answers } = {}) {
  return run(f, id === 'qa' ? [] : ['--instance', id], { script: menuScript('add', wizardAnswers(answers)) });
}

async function menuEdit(f, pick, answers, { id = 'qa' } = {}) {
  return run(f, id === 'qa' ? [] : ['--instance', id], {
    script: menuScript('edit', [
      { match: /^Edit which model\?$/, answer: (choices) => choices.find((choice) => choice.value
        && choice.value.route === pick.route && choice.value.model === pick.model)?.value },
      ...wizardAnswers(answers),
    ]),
  });
}

test('a bare `vika dsh model` on a terminal shows the list once, then the menu', async (t) => {
  const f = createCliFixture(t, { instances: ['qa'] });
  const result = await run(f, [], {
    script: [{ match: /^What to do$/, answer: 'quit' }],
  });
  assert.equal(result.code, 0, result.err);
  // The instance is named by the list, and the menu names it again.
  assert.match(result.out, /instance qa/);
  assert.match(result.out, /Model settings: instance qa \(QA\)/);
  // The list sits directly above the menu, so the menu has no "show the list"
  // row: it would only print the same block a second time.
  assert.equal((result.out.match(/^instance qa \(/gm) ?? []).length, 1,
    `the list is printed once: ${result.out}`);
  // Add and edit are two separate entries: the menu must not promise "add or
  // change" and then only add.
  const menu = result.prompter.choices.get('What to do');
  const labels = menu.map((choice) => choice.label);
  assert.deepEqual(labels, [
    'Add a model', 'Edit a model', 'Remove a model or route', 'Set a credential value',
    'Remove a credential', 'Quit',
  ]);
  // The rows are bare labels: every one of them is an action, and the list it
  // acts on is already above the menu.
  for (const choice of menu) {
    assert.equal(choice.hint, undefined, `${choice.label} should carry no hint`);
  }
});

test('without a terminal the bare command is exactly today\'s list and asks nothing', async (t) => {
  const f = createCliFixture(t, { instances: ['qa'] });
  const plain = await run(f, [], { tty: false });
  const list = await run(f, ['list'], { tty: false });
  assert.equal(plain.code, 0);
  assert.equal(plain.out, list.out, 'no terminal: byte-identical to `vika dsh model list`');
  assert.equal(plain.prompter.asked, 0, 'no prompt is even attempted');
  // No prompter at all (a script, a pipe): the same output, no crash.
  const io = streams();
  const code = await dshModelCommand([], { env: f.env, stdin: new PassThrough(), stdout: io.stdout, stderr: io.stderr, prompter: null });
  assert.equal(code, 0);
  assert.equal(io.state.out, list.out);
  // The flags the usage text documents belong to the bare form too: `--json`
  // must not read as a subcommand name.
  const json = await run(f, ['--json'], { tty: false });
  assert.equal(json.code, 0, json.err);
  assert.ok(Array.isArray(JSON.parse(json.out)), 'stdout is the JSON array');
  const named = await run(f, ['--instance', 'qa', '--json'], { tty: false });
  assert.equal(named.code, 0, named.err);
  assert.ok(Array.isArray(JSON.parse(named.out)), 'the same with --instance before it');
});

test('`add` is gone: the removal is a usage error and the help no longer advertises it', async (t) => {
  const f = createCliFixture(t, { instances: ['qa'] });
  const flagged = await run(f, ['add', 'deepseek/deepseek-chat', '--base-url', 'http://x.test/v1'], { tty: false });
  assert.equal(flagged.code, 2);
  assert.match(flagged.err, /there is no "add" subcommand/);
  assert.match(flagged.err, /Usage:/);
  assert.doesNotMatch(flagged.err, /add <route>\/<model>/);
  // The same refusal in a terminal: no wizard is reachable under `add` any more.
  const interactive = await run(f, ['add'], { script: [] });
  assert.equal(interactive.code, 2);
  assert.match(interactive.err, /there is no "add" subcommand/);
  assert.equal(interactive.prompter.asked, 0);
});

test('the menu\'s 添加 model runs the four questions and writes what was answered', async (t) => {
  const f = createCliFixture(t, { instances: ['qa'] });
  const result = await menuAdd(f);
  assert.equal(result.code, 0, result.err);
  // Ask order: gateway, protocol, model, credential, summary. The route and the
  // display name are not questions -- they are derived from the gateway URL.
  const asked = result.prompter.questions.filter((question) => !question.startsWith('spin:'));
  assert.deepEqual(asked, [
    'What to do',
    'Gateway base URL (e.g. https://api.deepseek.com/v1)',
    'Protocol',
    'Model id (may contain /)',
    'Credential',
    'Key value (not echoed)',
    'Write it this way?',
    'What to do',
  ]);
  const summary = result.prompter.notes.join('\n');
  assert.match(summary, /About to write:/);
  assert.match(summary, /route {4}deepseek \(derived from the gateway URL\)/);
  assert.match(summary, /model {4}deepseek\/deepseek-chat/);
  assert.ok(result.prompter.questions.some((question) => /^spin:Checking the route/.test(question)));
  const providers = f.patchProviders('qa');
  assert.deepEqual(Object.keys(providers), ['deepseek']);
  assert.equal(providers.deepseek.api, 'openai-completions');
  assert.equal(providers.deepseek.baseURL, 'https://api.deepseek.com/v1');
  assert.equal(providers.deepseek.apiKeyEnv, 'DEEPSEEK_API_KEY');
  assert.equal(providers.deepseek.displayName, 'deepseek');
  assert.deepEqual(providers.deepseek.models.map((model) => model.id), ['deepseek-chat']);
  assert.equal(f.credentials('qa').refs.get('DEEPSEEK_API_KEY'), 'sk-test-only');
  assert.match(result.out, /Wrote deepseek\/deepseek-chat/);
  assert.match(result.out, /vika restart qa/);
});

test('修改 model offers only declared entries and writes the edited values in place', async (t) => {
  const f = createCliFixture(t, { instances: ['qa'] });
  seedDocument(f, 'qa', { deepseek: DEEPSEEK_PROFILE });
  const result = await menuEdit(f, { route: 'deepseek', model: 'deepseek-chat' }, {
    baseURL: 'http://new-gateway.test/v1',
    protocol: 'openai-responses',
    model: 'deepseek-chat',
    credential: 'ref',
    reference: 'DEEPSEEK_API_KEY',
    keepAnyway: true,
  });
  assert.equal(result.code, 0, result.err);
  // The picker lists what the instance declares, not what DSH ships.
  const picker = result.prompter.choices.get('Edit which model?');
  assert.deepEqual(picker.filter((choice) => choice.value).map((choice) => choice.label), ['deepseek/deepseek-chat']);
  // The wizard was seeded from the entry, and the write reports what changed.
  assert.match(result.out, /Updated deepseek\/deepseek-chat/);
  assert.match(result.out, /gateway https:\/\/api\.deepseek\.com\/v1 → http:\/\/new-gateway\.test\/v1/);
  assert.match(result.out, /protocol openai-completions → openai-responses/);
  assert.doesNotMatch(result.out, /Wrote /, 'an edit is not reported as an add');
  const providers = f.patchProviders('qa');
  assert.deepEqual(Object.keys(providers), ['deepseek'], 'no second route');
  assert.equal(providers.deepseek.baseURL, 'http://new-gateway.test/v1');
  assert.equal(providers.deepseek.api, 'openai-responses');
  assert.equal(providers.deepseek.displayName, 'DeepSeek', 'the route display name is not touched by an edit');
  assert.deepEqual(providers.deepseek.models.map((model) => model.id), ['deepseek-chat']);
  assert.equal(providers.deepseek.models[0].name, 'DeepSeek Chat', 'the entry name survives');
  assert.equal(providers.deepseek.models[0].contextWindow, 1000000, 'fields the wizard does not ask about survive');
  assert.deepEqual(providers.deepseek.models[0].input, ['text', 'image']);
});

test('修改 model renames the model id without leaving the old id behind', async (t) => {
  const f = createCliFixture(t, { instances: ['qa'] });
  seedDocument(f, 'qa', { deepseek: DEEPSEEK_PROFILE });
  const result = await menuEdit(f, { route: 'deepseek', model: 'deepseek-chat' }, {
    model: 'deepseek-chat-v2',
    credential: 'ref',
    reference: 'DEEPSEEK_API_KEY',
    keepAnyway: true,
  });
  assert.equal(result.code, 0, result.err);
  const providers = f.patchProviders('qa');
  assert.deepEqual(providers.deepseek.models.map((model) => model.id), ['deepseek-chat-v2'],
    'exactly one entry, under the new id');
  assert.equal(providers.deepseek.models.some((model) => model.id === 'deepseek-chat'), false);
  assert.match(result.out, /Updated deepseek\/deepseek-chat-v2/);
  assert.match(result.out, /model deepseek-chat → deepseek-chat-v2/);
});

test('修改 model writes nothing when it is cancelled or nothing changed', async (t) => {
  const f = createCliFixture(t, { instances: ['qa'] });
  const file = seedDocument(f, 'qa', { deepseek: DEEPSEEK_PROFILE });
  const before = fs.readFileSync(file, 'utf8');

  // Cancelled at the picker.
  const cancelled = await run(f, [], {
    script: menuScript('edit', [
      { match: /^Edit which model\?$/, answer: null },
    ]),
  });
  assert.equal(cancelled.code, 0, cancelled.err);
  assert.equal(fs.readFileSync(file, 'utf8'), before);

  // Cancelled at the first wizard question (←).
  const { GoBack } = await import('../src/prompt.mjs');
  const backed = await run(f, [], {
    script: menuScript('edit', [
      { match: /^Edit which model\?$/, answer: (choices) => choices.find((choice) => choice.value).value },
      { match: /Gateway base URL/, answer: () => { throw new GoBack(); } },
    ]),
  });
  assert.equal(backed.code, 0, backed.err);
  assert.equal(fs.readFileSync(file, 'utf8'), before);

  // Nothing changed: the same values are submitted and the document is untouched.
  const unchanged = await menuEdit(f, { route: 'deepseek', model: 'deepseek-chat' }, {
    baseURL: 'https://api.deepseek.com/v1',
    protocol: 'openai-completions',
    model: 'deepseek-chat',
    credential: 'ref',
    reference: 'DEEPSEEK_API_KEY',
    keepAnyway: true,
  });
  assert.equal(unchanged.code, 0, unchanged.err);
  assert.match(unchanged.prompter.notes.join('\n'), /No change; nothing was written/);
  assert.equal(fs.readFileSync(file, 'utf8'), before);
});

test('修改 model treats a newly pasted key as a change instead of dropping it', async (t) => {
  const f = createCliFixture(t, { instances: ['qa'] });
  seedDocument(f, 'qa', { deepseek: DEEPSEEK_PROFILE });
  // Every field stays as it is; the only edit is the pasted value. Reporting
  // "no change" here would silently discard the one thing the operator changed.
  const result = await menuEdit(f, { route: 'deepseek', model: 'deepseek-chat' }, {
    baseURL: 'https://api.deepseek.com/v1',
    protocol: 'openai-completions',
    model: 'deepseek-chat',
    credential: 'paste',
    key: 'sk-brand-new',
    keepAnyway: true,
  });
  assert.equal(result.code, 0, result.err);
  assert.doesNotMatch(result.prompter.notes.join('\n'), /No change/);
  assert.match(result.out, /Updated deepseek\/deepseek-chat/);
  assert.match(result.out, /the value of credential .* was updated/);
  // No field moved, so the route is what it was (the settings service
  // materialises schema defaults, so the bytes may differ); the value is stored.
  assert.deepEqual(f.patchProviders('qa').deepseek.models, DEEPSEEK_PROFILE.models);
  assert.equal(f.credentials('qa').refs.get('DEEPSEEK_API_KEY'), 'sk-brand-new');
});

test('the delete picker offers the routes the instance declares and removes through the settings ops', async (t) => {
  const f = createCliFixture(t, { instances: ['qa'] });
  seedDocument(f, 'qa', { deepseek: DEEPSEEK_PROFILE });
  const result = await run(f, [], {
    script: [
      { match: /^What to do$/, answer: (choices, count) => (count === 1 ? 'remove' : 'quit') },
      { match: /^Remove which\?$/, answer: (choices) => choices.find((choice) => choice.label === 'deepseek/deepseek-chat').value },
      { match: /^Remove deepseek\/deepseek-chat\?$/, answer: true },
    ],
  });
  assert.equal(result.code, 0, result.err);
  const picker = result.prompter.choices.get('Remove which?');
  assert.ok(picker.some((choice) => choice.label === 'deepseek/deepseek-chat'), 'the real model is offered');
  assert.ok(picker.some((choice) => choice.label === 'the whole route deepseek'), 'and the route itself');
  // Removal went through the settings service, not a file rewrite.
  assert.deepEqual(f.mutateOps().filter((op) => op.op === 'unset'), [{ op: 'unset', path: ['providers', 'deepseek'] }]);
  assert.deepEqual(f.patchProviders('qa'), {});
  const [before, after] = result.out.split('Removed model');
  assert.match(before, /openai-completions/);
  assert.ok((after.match(/instance qa/g) ?? []).length >= 1, 'the list is printed again after the removal');
  assert.doesNotMatch(after, /openai-completions|deepseek\/deepseek-chat/);
});

test('the credential action stores the pasted value through the credential service', async (t) => {
  const f = createCliFixture(t, { instances: ['qa'] });
  seedDocument(f, 'qa', { deepseek: DEEPSEEK_PROFILE });
  const result = await run(f, [], {
    script: [
      { match: /^What to do$/, answer: (choices, count) => (count === 1 ? 'key-set' : 'quit') },
      { match: /^Set which credential\?$/, answer: 'DEEPSEEK_API_KEY' },
      { match: /Value for DEEPSEEK_API_KEY/, answer: 'sk-stored-by-menu' },
    ],
  });
  assert.equal(result.code, 0, result.err);
  assert.equal(f.credentials('qa').refs.get('DEEPSEEK_API_KEY'), 'sk-stored-by-menu');
  assert.ok(f.requests('vika/credentials/set').length > 0, 'the value went through the bridge');
  assert.match(result.out, /Stored credential DEEPSEEK_API_KEY/);
});

test('the credential action removes a value it no longer needs', async (t) => {
  const f = createCliFixture(t, { instances: ['qa'] });
  seedDocument(f, 'qa', { deepseek: DEEPSEEK_PROFILE });
  const result = await run(f, [], {
    script: [
      { match: /^What to do$/, answer: (choices, count) => (count === 1 ? 'key-unset' : 'quit') },
      { match: /^Remove which credential\?$/, answer: 'DEEPSEEK_API_KEY' },
      { match: /^Remove credential DEEPSEEK_API_KEY\?/, answer: true },
    ],
  });
  assert.equal(result.code, 0, result.err);
  assert.ok(f.requests('vika/credentials/unset').length > 0);
  assert.equal(f.credentials('qa').refs.has('DEEPSEEK_API_KEY'), false);
});

test('adding the same gateway twice through the menu updates the route in place', async (t) => {
  const f = createCliFixture(t, { instances: ['qa'] });
  seedDocument(f, 'qa', { deepseek: DEEPSEEK_PROFILE });
  const result = await menuAdd(f, { model: 'group/second-model' });
  assert.equal(result.code, 0, result.err);
  const notes = result.prompter.notes.join('\n');
  assert.doesNotMatch(notes, /Could not read the instance’s existing model routes/);
  assert.match(notes, /Route deepseek already exists at this URL/);
  const providers = f.patchProviders('qa');
  assert.deepEqual(Object.keys(providers), ['deepseek'], 'still one route');
  assert.deepEqual(providers.deepseek.models.map((model) => model.id), ['deepseek-chat', 'group/second-model']);
});

test('a derived route that already points elsewhere asks before it is repointed', async (t) => {
  const f = createCliFixture(t, { instances: ['qa'] });
  seedDocument(f, 'qa', {
    deepseek: { ...DEEPSEEK_PROFILE, baseURL: 'http://old.internal:8080/v1' },
  });
  const result = await run(f, [], {
    script: menuScript('add', [
      { match: /Gateway base URL/, answer: 'https://api.deepseek.com/v1' },
      { match: /^Protocol$/, answer: 'openai-completions' },
      { match: /Repoint deepseek to the new URL\?/, answer: false },
      { match: /^Another route id/, answer: 'deepseek2' },
      { match: /Model id/, answer: 'deepseek-chat' },
      { match: /^Credential$/, answer: 'paste' },
      { match: /Key value/, answer: 'sk-test-only' },
      { match: /Write it this way\?/, answer: true },
    ]),
  });
  assert.equal(result.code, 0, result.err);
  const providers = f.patchProviders('qa');
  assert.deepEqual(Object.keys(providers).sort(), ['deepseek', 'deepseek2']);
  assert.equal(providers.deepseek.baseURL, 'http://old.internal:8080/v1', 'the existing route was not repointed');
  assert.match(result.prompter.notes.join('\n'), /Route deepseek currently points at http:\/\/old\.internal:8080\/v1/);
});

test('the summary confirm is where the derived route can be corrected', async (t) => {
  const f = createCliFixture(t, { instances: ['qa'] });
  const result = await run(f, [], {
    script: menuScript('add', [
      ...wizardAnswers({ confirm: (choices, count) => count > 1 }),
      { match: /^Route id$/, answer: 'gateway2' },
    ]),
  });
  assert.equal(result.code, 0, result.err);
  assert.deepEqual(Object.keys(f.patchProviders('qa')), ['gateway2']);
  const summaries = result.prompter.notes.filter((note) => /About to write:/.test(note));
  assert.equal(summaries.length, 2);
  assert.match(summaries[0], /route {4}deepseek \(derived from the gateway URL\)/);
  assert.doesNotMatch(summaries[1], /derived from the gateway URL/);
});

test('the protocol defaults to what the gateway URL implies, and the menu explains each option', async (t) => {
  const result = createCliFixture(t, { instances: ['qa'] });
  const run1 = await menuAdd(result, { baseURL: 'http://api.anthropic.com/v1', protocol: 'anthropic-messages' });
  assert.equal(run1.code, 0, run1.err);
  const protocol = run1.prompter.choices.get('Protocol');
  assert.deepEqual(protocol.map((choice) => choice.value), ['openai-completions', 'openai-responses', 'anthropic-messages']);
  for (const choice of protocol) assert.ok(choice.hint && choice.label !== choice.value, `${choice.value} is described`);
  assert.equal(run1.prompter.options.get('Protocol').initial, 2, 'the Anthropic URL defaults to Anthropic Messages');
  assert.equal(result.patchProviders('qa').anthropic.api, 'anthropic-messages');
  // The heuristics themselves: the common cases must land on the right one.
  assert.equal(guessProtocol('https://api.deepseek.com/v1'), 'openai-completions');
  assert.equal(guessProtocol('https://api.anthropic.com/v1'), 'anthropic-messages');
  assert.equal(guessProtocol('https://gateway.test/anthropic'), 'anthropic-messages');
  assert.equal(guessProtocol('https://api.openai.com/v1'), 'openai-responses');
  assert.equal(guessProtocol('https://gateway.test/codex/v1'), 'openai-responses');
});

test('← inside the menu wizard returns to the menu instead of ending the session', async (t) => {
  const { GoBack } = await import('../src/prompt.mjs');
  const f = createCliFixture(t, { instances: ['qa'] });
  const result = await run(f, [], {
    script: [
      { match: /^What to do$/, answer: (choices, count) => (count === 1 ? 'add' : 'quit') },
      { match: /Gateway base URL/, answer: () => { throw new GoBack(); } },
    ],
  });
  assert.equal(result.code, 0, result.err);
  assert.equal((result.prompter.questions.filter((question) => /^What to do$/.test(question))).length, 2,
    'the menu came back after the cancelled action');
  assert.deepEqual(f.patchProviders('qa'), null, 'nothing was written');
});

test('several instances: the question is asked, and each answer acts on that instance\'s own document', async (t) => {
  const f = createCliFixture(t, { instances: ['primary', 'secondary'] });
  // No --instance on a terminal: the chooser asks instead of silently reading
  // the manifest default (the bug this fixes).
  const chosen = await run(f, [], {
    script: [
      { match: /^Which instance’s model routes$/, answer: (choices) => choices.find((choice) => choice.value.id === 'secondary').value },
      { match: /^What to do$/, answer: 'quit' },
    ],
  });
  assert.equal(chosen.code, 0, chosen.err);
  assert.ok(chosen.prompter.questions.some((question) => /^Which instance’s model routes$/.test(question)),
    chosen.prompter.questions.join(' | '));
  assert.match(chosen.out, /instance secondary/);
  assert.match(chosen.out, /Model settings: instance secondary/);
  assert.doesNotMatch(chosen.out, /instance primary/);

  // --instance names the target: the write lands in that instance's home.
  const named = await menuAdd(f, { id: 'secondary' });
  assert.equal(named.code, 0, named.err);
  assert.equal(named.prompter.questions.some((question) => /^Which instance’s model routes$/.test(question)), false);
  assert.deepEqual(Object.keys(f.patchProviders('secondary')), ['deepseek']);
  assert.equal(f.patchProviders('primary'), null);
});

test('without --instance and without a terminal, several instances are an error that lists them', async (t) => {
  const f = createCliFixture(t, { instances: ['primary', 'secondary'] });
  // The silent "use the default instance" is gone: this must refuse, and name
  // both instances so the operator knows what to pass.
  await assert.rejects(
    run(f, ['list'], { tty: false }),
    /There are several instances; name one after the command:[\s\S]*primary[\s\S]*secondary/,
  );
  await assert.rejects(run(f, [], { tty: false }), /There are several instances/);
  // The removed add command is a usage error, not an instance question.
  const piped = await run(f, ['add'], { tty: false });
  assert.equal(piped.code, 2);
  assert.match(piped.err, /there is no "add" subcommand/);
  // Nothing was read or written for either instance.
  assert.equal(f.patchProviders('primary'), null);
  assert.equal(f.patchProviders('secondary'), null);
});

test('the menu can switch instance, and the switch re-reads that instance\'s routes', async (t) => {
  const f = createCliFixture(t, { instances: ['primary', 'secondary'] });
  seedDocument(f, 'secondary', { deepseek: DEEPSEEK_PROFILE });
  const result = await run(f, ['--instance', 'primary'], {
    script: [
      { match: /^What to do$/, answer: (choices, count) => (count === 1 ? 'switch' : 'quit') },
      { match: /^Which instance’s model routes$/, answer: (choices) => choices.find((choice) => choice.value.id === 'secondary').value },
    ],
  });
  assert.equal(result.code, 0, result.err);
  const menus = result.prompter.questions.filter((question) => /^What to do$/.test(question));
  assert.equal(menus.length, 2);
  assert.match(result.out, /Model settings: instance primary/);
  assert.match(result.out, /Model settings: instance secondary/);
  assert.match(result.out, /deepseek\/deepseek-chat/);
  const switchEntry = result.prompter.choices.get('What to do')
    .find((choice) => choice.value === 'switch');
  assert.ok(switchEntry, 'the menu offers the switch when several instances exist');
  assert.equal(switchEntry.hint, undefined, 'the switch row is a bare label like the rest');
  assert.equal(result.prompter.choices.get('What to do').find((choice) => choice.value === 'add').hint, undefined);
});

test('the wizard asks four questions, derives the route from the host, and never asks for a display name', async (t) => {
  const f = createCliFixture(t, { instances: ['qa'] });
  const result = await menuAdd(f, { baseURL: 'https://api.acme-gateway.test/v1' });
  assert.equal(result.code, 0, result.err);
  const asked = result.prompter.questions.filter((question) => !question.startsWith('spin:'));
  assert.equal(asked.some((question) => /Route id|Display name/.test(question)), false,
    'the route and the display name are derived, not asked');
  const providers = f.patchProviders('qa');
  assert.deepEqual(Object.keys(providers), ['acme-gateway'], 'the host names the route');
  assert.equal(providers['acme-gateway'].displayName, 'acme-gateway');
  assert.match(result.out, /Wrote acme-gateway\/deepseek-chat/);
});

test('an IP gateway gets the neutral route id and still works end to end', async (t) => {
  const f = createCliFixture(t, { instances: ['qa'] });
  const result = await menuAdd(f, { baseURL: 'http://192.0.2.10:8080/v1' });
  assert.equal(result.code, 0, result.err);
  assert.deepEqual(Object.keys(f.patchProviders('qa')), ['gateway']);
  assert.equal(f.patchProviders('qa').gateway.apiKeyEnv, 'GATEWAY_API_KEY', 'the credential reference follows the route');
  assert.match(result.out, /Wrote gateway\/deepseek-chat/);
});

test('a credential reference typed at the prompt is the one stored and printed', async (t) => {
  const f = createCliFixture(t, { instances: ['qa'] });
  const result = await menuAdd(f, { credential: 'ref', reference: 'ACME_TOKEN', keepAnyway: true });
  assert.equal(result.code, 0, result.err);
  assert.equal(f.patchProviders('qa').deepseek.apiKeyEnv, 'ACME_TOKEN');
  assert.match(result.out, /vika dsh model key set ACME_TOKEN/);
  assert.equal(f.credentials('qa').refs.size, 0, 'a reference-only answer stores no value');
});

test('the wizard validates a model id with the shared grammar', () => {
  // The flags that used to carry these ids are gone, so the grammar is asserted
  // where the wizard applies it: the same helper the prompt validates with.
  assert.match(modelIdProblem('deepseek', 'has space'), /use letters, digits/);
  assert.match(modelIdProblem('deepseek', 'model\u0001'), /use letters, digits/);
  assert.equal(modelIdProblem('deepseek', 'deepseek-chat'), null);
  assert.equal(modelIdProblem('deepseek', 'model@2026-01'), null);
  assert.ok(modelIdProblem('deepseek', 'x'.repeat(300)));
});

test('two menu adds for the same gateway leave exactly one route', async (t) => {
  const f = createCliFixture(t, { instances: ['qa'] });
  const first = await menuAdd(f, { model: 'group/first-model' });
  assert.equal(first.code, 0, first.err);
  const second = await menuAdd(f, { model: 'group/second-model' });
  assert.equal(second.code, 0, second.err);
  const providers = f.patchProviders('qa');
  assert.deepEqual(Object.keys(providers), ['deepseek']);
  assert.deepEqual(providers.deepseek.models.map((model) => model.id), ['group/first-model', 'group/second-model']);
  assert.equal(f.credentials('qa').refs.get('DEEPSEEK_API_KEY'), 'sk-test-only');
  assert.doesNotMatch(second.prompter.notes.join('\n'), /Could not read the instance’s existing model routes/);
});

test('adding a model to an existing route keeps the reference it already resolves', async (t) => {
  const f = createCliFixture(t, { instances: ['qa'] });
  // An imported route may hold `_2` after a rotation. Re-deriving `_1` here would
  // re-point the route and, when only `_2` has a value, leave it unconfigured.
  seedDocument(f, 'qa', {
    'foo-bar': {
      ...DEEPSEEK_PROFILE, baseURL: 'https://api.foo-bar.test/v1', apiKeyEnv: 'FOO_BAR_API_KEY_2', displayName: 'foo-bar',
    },
  });
  // The gateway host derives the same route, so this is an add *into* it.
  // Paste a value rather than naming the reference: the wizard's own default is
  // what this test is about, and it is not overridden by an answer.
  const result = await menuAdd(f, {
    baseURL: 'https://api.foo-bar.test/v1', model: 'second-model', credential: 'paste',
    key: 'sk-second-model', keepAnyway: true,
  });
  assert.equal(result.code, 0, result.err);
  const providers = f.patchProviders('qa');
  assert.deepEqual(Object.keys(providers), ['foo-bar'], `routes: ${JSON.stringify(Object.keys(providers))} / out: ${result.out}`);
  assert.deepEqual(providers['foo-bar'].models.map((model) => model.id), ['deepseek-chat', 'second-model']);
  assert.equal(providers['foo-bar'].apiKeyEnv, 'FOO_BAR_API_KEY_2', 'the reference the route already resolved is kept');
  assert.equal(f.credentials('qa').refs.get('FOO_BAR_API_KEY_2'), 'sk-second-model', 'the value went under that reference');
});

test('renaming a model id keeps the fields the wizard never asks about', async (t) => {
  const f = createCliFixture(t, { instances: ['qa'] });
  // A hand-written entry carrying wire switches: the rename replaces the entry, so
  // anything the wizard does not prompt for has to survive on its own.
  seedDocument(f, 'qa', {
    deepseek: {
      ...DEEPSEEK_PROFILE,
      models: [{
        id: 'deepseek-chat',
        name: 'DeepSeek Chat',
        input: ['text', 'image'],
        contextWindow: 1000000,
        maxTokens: 384000,
        compat: { supportsStore: false, thinkingFormat: 'deepseek' },
        reasoningEfforts: { off: null, low: 'low', high: 'high' },
        headers: { 'x-tenant': 'acme' },
      }],
    },
  });
  const result = await menuEdit(f, { route: 'deepseek', model: 'deepseek-chat' }, {
    model: 'deepseek-chat-v2',
    credential: 'ref',
    reference: 'DEEPSEEK_API_KEY',
    keepAnyway: true,
  });
  assert.equal(result.code, 0, result.err);
  const models = f.patchProviders('qa').deepseek.models;
  assert.deepEqual(models.map((model) => model.id), ['deepseek-chat-v2']);
  assert.deepEqual(models[0].compat, { supportsStore: false, thinkingFormat: 'deepseek' },
    'compat is not something the wizard asks about, so it cannot be dropped');
  assert.deepEqual(models[0].reasoningEfforts, { off: null, low: 'low', high: 'high' });
  assert.deepEqual(models[0].headers, { 'x-tenant': 'acme' });
  assert.deepEqual(models[0].input, ['text', 'image']);
  assert.equal(models[0].contextWindow, 1000000);
});

test('a gateway route id comes from the registry label, not from a public suffix', async (t) => {
  // Two unrelated registries under the same public suffix must not collide: the
  // route id is also the model id prefix the operator types.
  const { routeFromGateway } = await import('../src/dsh-model-wizard.mjs');
  assert.equal(routeFromGateway('https://api.example.co.uk/v1'), 'example');
  assert.equal(routeFromGateway('https://api.example.com.cn/v1'), 'example');
  assert.equal(routeFromGateway('https://gw.example.com.au/v1'), 'example');
  assert.equal(routeFromGateway('https://a.b.c.d.example.co.uk/v1'), 'example');
  assert.equal(routeFromGateway('https://api.corp.example.com.cn/v1'), 'example');
  // The plain single-label suffix keeps its existing answer.
  assert.equal(routeFromGateway('https://api.deepseek.com/v1'), 'deepseek');
  // A host that is only a suffix has no registry label; the leftmost stands in
  // rather than producing an empty id.
  assert.equal(routeFromGateway('https://co.uk/v1'), 'co');
  // Non-DNS hosts keep the generic answer.
  assert.equal(routeFromGateway('http://192.0.2.10:3425/v1'), 'gateway');
  assert.equal(routeFromGateway('http://[2001:db8::1]:8080/v1'), 'gateway');
  assert.equal(routeFromGateway('not a url'), 'gateway');
});
