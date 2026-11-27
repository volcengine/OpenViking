// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { applySettings, currentSettings, settingsCard } from '../src/settings-card.mjs';
import { WorkerSettings } from '../src/worker-settings.mjs';

const CATALOG = [{ id: 'ollama/deepseek-v4.1-flash:1017', efforts: ['off', 'low', 'medium', 'high', 'max'] }];

function snapshotFor(overrides = {}) {
  return {
    runtime: 'pi', model: 'pi', resolvedModel: 'ollama/deepseek-v4.1-flash:1017', effort: 'high', models: [],
    catalogs: { pi: CATALOG },
    ...overrides,
  };
}

function settingsOn(router, stored = {}) {
  const values = new Map(Object.entries(stored));
  const store = {
    getSetting: (key) => values.get(`worker_${key}`) ?? '',
    setSetting: (key, value) => values.set(`worker_${key}`, value),
  };
  return new WorkerSettings({ store, router: () => router });
}

// Every select on the card, by name, with its options and initial value.
function selects(card) {
  const found = [];
  JSON.stringify(card, (key, value) => {
    if (value && value.tag === 'select_static') {
      found.push({ name: value.name ?? null, initial: value.initial_option,
        options: value.options.map((option) => ({ value: option.value, label: option.text.content })) });
    }
    return value;
  });
  return found;
}

// The rows of every table on the card.
function tables(card) {
  const found = [];
  JSON.stringify(card, (key, value) => {
    if (value && value.tag === 'table') found.push(value.rows);
    return value;
  });
  return found;
}

const routerModelOptions = (card) => selects(card).find((select) => select.name === 'router_model');
const workerModelOptions = (card) => selects(card).find((select) => select.name === 'worker_model');
const runtimeOptions = (card, name = 'router_runtime') => selects(card).find((select) => select.name === name);

// What runtime-availability.mjs reports for an install that has the two bundled
// runtimes plus DSH, which is what a fresh install looks like.
const ONE_MACHINE = {
  pi: { available: true, reason: '' },
  durable: { available: true, reason: '' },
  dsh: { available: true, reason: '' },
  codex: { available: false, reason: 'command' },
  traex: { available: false, reason: 'command' },
  claude: { available: false, reason: 'command' },
  opencode: { available: false, reason: 'command' },
  agy: { available: false, reason: 'command' },
  volc: { available: false, reason: 'key' },
};

describe('runtime settings card', () => {
  it('never offers the runtime default selector as a model', () => {
    // Pi with no model chosen reports "pi", its canonical default: the
    // manifest's runtime selector, not an id (agent-runtime model getter).
    const card = settingsCard({ snapshot: snapshotFor(), settings: settingsOn({ runtime: 'pi', model: 'pi', effort: 'max' }) });

    assert.deepEqual(routerModelOptions(card).options.map((option) => option.value),
      ['default', 'ollama/deepseek-v4.1-flash:1017']);
    assert.deepEqual(routerModelOptions(card).options.map((option) => option.label),
      ['Runtime 默认', 'ollama/deepseek-v4.1-flash:1017']);
    assert.equal(routerModelOptions(card).initial, 'default');

    // The table names the model the session runs on, not the selector.
    assert.equal(tables(card)[0][0].model, '默认（ollama/deepseek-v4.1-flash:1017）');
    // The worker follows the router, so its default says so instead of
    // repeating the selector as if it were a model someone had set.
    assert.deepEqual(workerModelOptions(card).options.map((option) => option.label),
      ['CLI 默认', 'ollama/deepseek-v4.1-flash:1017']);
  });

  it('offers a model the router does have, and labels the worker with it', () => {
    const card = settingsCard({ snapshot: snapshotFor({ model: 'ollama/deepseek-v4.1-flash:1017' }),
      settings: settingsOn({ runtime: 'pi', model: 'ollama/deepseek-v4.1-flash:1017', effort: 'max' }) });

    assert.deepEqual(routerModelOptions(card).options.map((option) => option.value),
      ['ollama/deepseek-v4.1-flash:1017']);
    assert.equal(routerModelOptions(card).initial, 'ollama/deepseek-v4.1-flash:1017');
    assert.equal(tables(card)[0][0].model, 'ollama/deepseek-v4.1-flash:1017');
    // A worker on the router's runtime takes the router's model, so the
    // inherited option says which one.
    assert.deepEqual(workerModelOptions(card).options.map((option) => option.label),
      ['ollama/deepseek-v4.1-flash:1017', 'ollama/deepseek-v4.1-flash:1017']);
    assert.equal(workerModelOptions(card).initial, 'default');
  });

  it('reports no model chosen for the router without inventing one', () => {
    const settings = settingsOn({ runtime: 'pi', model: 'pi', effort: 'max' });
    const now = currentSettings(snapshotFor(), settings);
    assert.deepEqual(now.router, { runtime: 'pi', model: '', effort: 'high' });
    // The worker's inherited model comes from the router's, so the selector
    // must not arrive there as a model either.
    assert.deepEqual(now.worker, { runtime: 'router', model: '', effort: '' });
    assert.equal(settings.modelFor('pi').model, null);
  });

  it('offers only the runtimes this machine can start', () => {
    const card = settingsCard({ snapshot: snapshotFor(), settings: settingsOn({ runtime: 'pi', model: 'pi', effort: 'max' }),
      availability: ONE_MACHINE });

    // The catalogue has nine runtimes; this machine runs three, and the dropdown
    // says so instead of offering five that would fail to start.
    assert.deepEqual(runtimeOptions(card).options.map((option) => option.value), ['pi', 'durable', 'dsh']);
    // The workers run on a subset of the same catalogue; Pi is the router's runtime
    // here, so the worker list offers following it.
    assert.deepEqual(runtimeOptions(card, 'worker_runtime').options.map((option) => option.value), ['router']);
    // What was left out is named, once per reason.
    const notes = JSON.stringify(card.body);
    assert.match(notes, /未列出（这台机器上找不到命令）：codex、traex、claude、opencode、agy/);
    assert.match(notes, /未列出（没有配置 ARK_API_KEY）：volc/);
  });

  it('keeps the runtimes in use listed even when the probe no longer finds them', () => {
    // Runtimes that disappeared from PATH after the daemon started are still where
    // the agent runs and where /workers points: the card has to name them rather
    // than draw a dropdown that omits them.
    const values = new Map([['worker_runtime', 'codex']]);
    const settings = new WorkerSettings({ router: () => ({ runtime: 'claude', model: 'sonnet', effort: '' }),
      store: { getSetting: (key) => values.get(key) ?? '', setSetting: (key, value) => values.set(key, value) } });
    const card = settingsCard({ snapshot: snapshotFor({ runtime: 'claude', model: 'sonnet' }), settings,
      availability: ONE_MACHINE });

    // Catalogue order: the keep list is what the instance is set to, not where it is.
    assert.deepEqual(runtimeOptions(card).options.map((option) => option.value), ['codex', 'claude', 'pi', 'durable', 'dsh']);
    assert.equal(runtimeOptions(card).initial, 'claude');
    assert.deepEqual(runtimeOptions(card, 'worker_runtime').options.map((option) => option.value), ['pi', 'codex', 'claude']);
    assert.equal(runtimeOptions(card, 'worker_runtime').initial, 'codex');
    assert.match(JSON.stringify(card.body), /未列出（这台机器上找不到命令）：traex、opencode、agy/);
  });

  it('offers the whole catalogue when the caller knows nothing about this machine', () => {
    // Cards drawn without a probe (older callers, tests) keep the catalogue list.
    const card = settingsCard({ snapshot: snapshotFor() });
    assert.deepEqual(runtimeOptions(card).options.map((option) => option.value),
      ['codex', 'traex', 'claude', 'volc', 'pi', 'durable', 'dsh', 'opencode', 'agy']);
    assert.doesNotMatch(JSON.stringify(card.body), /未列出/);
  });

  it('does not turn a submitted selector into a model change', async () => {
    const snapshot = snapshotFor();
    const settings = settingsOn({ runtime: 'pi', model: 'pi', effort: 'max' });
    const value = { draft: { router: 'pi', worker: 'router' }, seen: JSON.stringify(currentSettings(snapshot, settings)) };
    const ran = [];
    const run = async (name, arg) => { ran.push(['router', name, arg]); return { ok: true, text: 'ok' }; };
    const runWorkers = async (arg) => { ran.push(['workers', arg]); return { ok: true, text: 'ok' }; };

    const unchanged = await applySettings({ interaction: { value, form: {} }, snapshot, settings, run, runWorkers });
    assert.equal(unchanged.stale, false);
    assert.deepEqual(unchanged.steps, []);
    assert.deepEqual(ran, []);

    // The one thing the form can send for the router's model is a real id.
    const applied = await applySettings({ interaction: { value, form: { router_model: 'ollama/deepseek-v4.1-flash:1017' } },
      snapshot, settings, run, runWorkers });
    assert.deepEqual(applied.steps.map((step) => [step.label, step.ok]), [['router 模型', true]]);
    assert.deepEqual(ran, [['router', 'model', 'ollama/deepseek-v4.1-flash:1017']]);
  });
});
