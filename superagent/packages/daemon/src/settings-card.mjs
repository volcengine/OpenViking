// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Lark cards for what the agent runs on. /runtime with no argument answers
// with one card that sets the router's and the workers' runtime, model and
// reasoning effort together, instead of six commands; /status answers with a
// card of the same facts and what the agent is doing. Elsewhere both stay text.
//
// The settings card works in two steps, because a Lark form says nothing until
// it is submitted. A runtime is chosen above the form: the choice comes back
// at once, and the card is redrawn with the models and efforts of that runtime.
// The models and efforts are chosen in the form and submitted together. A
// submission is applied as the commands it stands for, in order (/runtime,
// /model, /effort, then /workers runtime, model, effort), so it goes through
// their checks and says what they say. Only what changed is applied, and
// nothing is when the settings changed since the card was drawn.
//
// The router's part follows /runtime (whoever may use it in the direct chat);
// the workers' part follows /workers (the owner only).

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { RUNTIME_IDS, compatibleModelEffort, currentModelEntry, getRuntimeDefinition, modelIdPattern, staticModelEntries } from './runtime-catalog.mjs';
import { isWorkerRuntime } from './worker-settings.mjs';
import { WORKER_RUNTIMES } from './worker-executor.mjs';

// The callback value key on this card's controls.
export const SETTINGS_CARD = 'superagent_settings';
// The runtime's default model (router), or the CLI's model or effort (workers).
const DEFAULT = 'default';
// The workers follow the router's runtime.
const FOLLOW = 'router';

function plain(content, style = {}) {
  return { tag: 'plain_text', content: String(content ?? ''), ...style };
}

const NOTE = { text_size: 'notation', text_color: 'grey' };
const note = (text) => ({ tag: 'div', text: plain(text, NOTE) });

function table(columns, rows) {
  return {
    tag: 'table', page_size: 10, row_height: 'low', header_style: { background_style: 'grey', bold: true },
    columns: columns.map(([name, display_name, width]) => ({ name, display_name, data_type: 'text', ...(width ? { width } : {}) })),
    rows,
  };
}

function select(options, initial, extra = {}) {
  const values = new Set(options.map(([value]) => value));
  return {
    tag: 'select_static', width: 'fill',
    options: options.map(([value, label]) => ({ text: plain(label), value })),
    ...(values.has(initial) ? { initial_option: initial } : {}),
    ...extra,
  };
}

// What is set now. `worker` is null where there are no workers, or for
// someone other than the owner.
export function currentSettings(snapshot, settings) {
  const router = { runtime: snapshot.runtime ?? '', model: snapshot.model || '', effort: snapshot.effort || '' };
  if (!settings) return { router, worker: null };
  const runtime = settings.preferred();
  const own = isWorkerRuntime(runtime) ? settings.own(runtime) : { model: '', effort: '' };
  return { router, worker: { runtime: settings.configuredRuntime() ?? FOLLOW, model: own.model || '', effort: own.effort || '' } };
}

// The models a runtime serves: the live catalogue for the router's runtime,
// else the runtime's own list. Each with the efforts it takes.
function modelsOf(runtime, snapshot) {
  if (snapshot.catalogs?.[runtime]?.length) return snapshot.catalogs[runtime];
  if (runtime === snapshot.runtime && snapshot.models?.length) {
    const pattern = modelIdPattern(runtime);
    return snapshot.models.filter((entry) => pattern.test(entry.id)).map((entry) => ({ id: entry.id, efforts: entry.efforts ?? [] }));
  }
  if (runtime === 'pi') {
    // Pi's configured providers are available before any worker is started.
    const configured = process.env.PI_CODING_AGENT_DIR || path.join(os.homedir(), '.pi', 'agent');
    const directory = configured.replace(/^~(?=\/|$)/, os.homedir());
    try {
      const data = JSON.parse(fs.readFileSync(path.join(directory, 'models.json'), 'utf8'));
      return Object.entries(data.providers ?? {}).flatMap(([provider, config]) =>
        (Array.isArray(config.models) ? config.models : []).filter((model) => typeof model.id === 'string' && model.id).map((model) => ({
          id: `${provider}/${model.id}`, efforts: model.reasoning ? getRuntimeDefinition('pi').efforts : ['off'],
        }))).filter(({ id }) => modelIdPattern('pi').test(id));
    } catch { return []; }
  }
  return staticModelEntries(runtime);
}

// The rows of the "now" table, as the commands report them.
function nowRows(snapshot, settings) {
  const rows = [{ who: 'router', runtime: snapshot.runtime ?? '—', model: snapshot.model || '默认', effort: snapshot.effort || '—' }];
  if (settings) {
    const runtime = settings.preferred();
    const model = isWorkerRuntime(runtime) ? settings.modelFor(runtime) : { model: null, effort: null };
    rows.push({ who: 'worker', runtime: runtime ?? '—',
      model: model.model || 'CLI 默认', effort: model.effort || 'CLI 默认' });
  }
  return rows;
}

// A row of columns, side by side on a phone too: [weight, elements] each.
function row(columns) {
  return { tag: 'column_set', flex_mode: 'none', horizontal_spacing: '8px',
    columns: columns.map(([weight, elements]) => ({ tag: 'column', width: 'weighted', weight, vertical_align: 'center', elements })) };
}

const label = (text) => ({ tag: 'div', text: plain(text, NOTE) });

const nowTable = (snapshot, settings) => table([['who', '', '80px'], ['runtime', 'Runtime'], ['model', '模型'], ['effort', '推理档位']],
  nowRows(snapshot, settings));

// The settings card. `draft` holds the runtimes chosen above the form; `seen`,
// what was set when the card was first drawn (a submission refuses to apply
// over a change made since). `saved(runtime)` is the model and effort the
// router last had on a runtime: what it takes again when switched back there.
export function settingsCard({ snapshot, settings = null, draft = null, seen = null, saved = () => ({}) }) {
  const now = currentSettings(snapshot, settings);
  seen ??= JSON.stringify(now);
  draft ??= { router: now.router.runtime, worker: now.worker?.runtime ?? null };
  const value = (action, extra = {}) => ({ [SETTINGS_CARD]: action, draft, seen, ...extra });

  // The router's model and effort are shown as they will be: what it has on
  // its runtime, or on another what it last had there (else that runtime's
  // default model, and the model's own default effort). Its model is always
  // offered, listed or not.
  const sameRouter = draft.router === snapshot.runtime;
  const kept = sameRouter ? { model: snapshot.model || '', effort: snapshot.effort || '' }
    : { model: saved(draft.router)?.model || '', effort: saved(draft.router)?.effort || '' };
  const listed = modelsOf(draft.router, snapshot);
  const routerModels = kept.model && !listed.some(({ id }) => id === kept.model)
    ? [{ id: kept.model, efforts: (sameRouter ? currentModelEntry(snapshot)?.efforts : null) ?? listed[0]?.efforts ?? [] }, ...listed] : listed;
  const routerModel = kept.model || DEFAULT;
  const routerEntry = sameRouter ? currentModelEntry(snapshot) : routerModels.find(({ id }) => id === kept.model) ?? routerModels[0];
  const routerEfforts = routerEntry?.efforts ?? getRuntimeDefinition(draft.router)?.efforts ?? [];
  const routerEffort = routerEfforts.includes(kept.effort) ? kept.effort
    : compatibleModelEffort({ efforts: routerEfforts, defaultEffort: routerEntry?.defaultEffort }, kept.effort) ?? '';

  // Laid out in rows, a label then its dropdowns side by side, so the card
  // stays short: the runtimes in one row above the form, then one row each
  // for the router's and the workers' model and effort.
  const routerRuntime = select(RUNTIME_IDS.map((id) => [id, id]), draft.router,
    { behaviors: [{ type: 'callback', value: value('runtime', { side: 'router' }) }] });
  const routerModel_ = select([...(kept.model ? [] : [[DEFAULT, 'Runtime 默认']]), ...routerModels.map(({ id }) => [id, id])], routerModel,
    { name: 'router_model', placeholder: plain('模型') });
  const routerEffort_ = select(routerEfforts.map((level) => [level, level]), routerEffort,
    { name: 'router_effort', placeholder: plain('推理档位'), ...(routerEfforts.length ? {} : { disabled: true }) });
  const runtimes = [[1, [label('router')]], [3, [routerRuntime]]];
  const rows = [row([[1, []], [4, [label('模型')]], [2, [label('推理档位')]]]), row([[1, [label('router')]], [4, [routerModel_]], [2, [routerEffort_]]])];
  const notes = routerModels.length ? [] : [`${draft.router} 暂未读取到模型目录，可用 /model <名称> 设置`];

  if (now.worker) {
    // The workers run on what they are set to, or on what the router will.
    const runtime = draft.worker === FOLLOW ? draft.router : draft.worker;
    const runs = isWorkerRuntime(runtime);
    const own = runs ? settings.own(runtime) : { model: '', effort: '' };
    // Unset, a worker on the router's runtime takes the router's model and
    // effort (WorkerSettings.modelFor); elsewhere the CLI's own default.
    const workerModels = runs ? modelsOf(runtime, snapshot) : [];
    if (own.model && !workerModels.some(({ id }) => id === own.model)) workerModels.unshift({ id: own.model });
    // The effort choices belong to the model this worker will run: its own,
    // else the router's on the same runtime, else the CLI's default.
    const workerModel = own.model || (runtime === draft.router ? kept.model : '');
    const efforts = runs ? settings.efforts(runtime, workerModel, workerModels) : [];
    const unset = (inherited) => (runtime === draft.router && inherited ? inherited : 'CLI 默认');
    runtimes.push([1, [label('worker')]], [3, [select(WORKER_RUNTIMES.map((id) => [draft.worker === FOLLOW && id === draft.router ? FOLLOW : id, id]), draft.worker,
      { behaviors: [{ type: 'callback', value: value('runtime', { side: 'worker' }) }] })]]);
    rows.push(row([[1, [label('worker')]],
      [4, [select([[DEFAULT, unset(kept.model)], ...workerModels.map(({ id }) => [id, id])], own.model || DEFAULT,
        { name: 'worker_model', placeholder: plain('模型'), ...(runs ? {} : { disabled: true }) })]],
      [2, [select([[DEFAULT, unset(routerEffort)], ...efforts.map((level) => [level, level])], own.effort || DEFAULT,
        { name: 'worker_effort', placeholder: plain('推理档位'), ...(runs && efforts.length ? {} : { disabled: true }) })]]]));
    if (!runs) notes.push(`worker 不能运行在 ${runtime} 上：请为 worker 另选一个 Runtime`);
    else if (!workerModels.length) notes.push(`${runtime} 未找到可选模型：可用 /workers model <名称> 设置`);
  }

  const elements = [
    { tag: 'markdown', content: '**当前**' }, nowTable(snapshot, settings),
    { tag: 'markdown', content: '**① Runtime**<font color="grey">（改选后卡片会刷新下面的选项）</font>' },
    row(runtimes),
    { tag: 'markdown', content: '**② 模型与推理档位**' },
    {
      tag: 'form', name: 'settings',
      elements: [...rows, ...notes.map(note), row([[1, [{
        tag: 'button', name: 'apply', text: plain('应用'), type: 'primary', width: 'fill', form_action_type: 'submit',
        behaviors: [{ type: 'callback', value: value('apply') }],
      }]], [1, [{
        tag: 'button', name: 'cancel', text: plain('取消'), type: 'default', width: 'fill',
        behaviors: [{ type: 'callback', value: { [SETTINGS_CARD]: 'cancel' } }],
      }]], [3, []]])],
    },
    ...(now.worker ? [note('worker 的设置只影响之后新建的任务')] : []),
  ];

  return {
    schema: '2.0', config: { update_multi: true },
    header: { template: 'blue', title: plain('Runtime 与模型'), subtitle: plain(now.worker ? 'router 与 worker 各自的 Runtime、模型和推理档位' : 'router 的 Runtime、模型和推理档位') },
    body: { elements },
  };
}

// A runtime chosen above the form: the card again, with that choice.
export function redrawn({ interaction, snapshot, settings, saved }) {
  const { draft, seen, side } = interaction.value ?? {};
  const option = interaction.option;
  const valid = side === 'router' ? RUNTIME_IDS.includes(option) : side === 'worker' && settings ? [FOLLOW, ...WORKER_RUNTIMES].includes(option) : false;
  if (!valid || !draft) return null;
  return settingsCard({ snapshot, settings, saved, seen, draft: { ...draft, [side]: option } });
}

function firstLine(text) {
  return String(text ?? '').split('\n')[0];
}

// A submission, applied as the commands it stands for. `run(name, arg)` runs a
// router command, `runWorkers(arg)` a /workers one; each returns { ok, text }.
export async function applySettings({ interaction, snapshot, settings, run, runWorkers }) {
  const { draft, seen } = interaction.value ?? {};
  const form = interaction.form ?? {};
  if (!draft || seen !== JSON.stringify(currentSettings(snapshot, settings))) return { stale: true, steps: [] };
  const steps = [];
  const step = async (label, outcome) => steps.push({ label, ok: outcome.ok !== false, text: firstLine(outcome.text) });

  if (RUNTIME_IDS.includes(draft.router) && draft.router !== snapshot.runtime) await step('router Runtime', await run('runtime', draft.router));
  // What the router already has is no change (on the runtime it stays on).
  const stays = draft.router === snapshot.runtime;
  const routerModel = typeof form.router_model === 'string' ? form.router_model : DEFAULT;
  if (routerModel !== DEFAULT && !(stays && routerModel === snapshot.model)) await step('router 模型', await run('model', routerModel));
  const routerEffort = typeof form.router_effort === 'string' ? form.router_effort : DEFAULT;
  if (routerEffort !== DEFAULT && !(stays && routerEffort === snapshot.effort)) await step('router 推理档位', await run('effort', routerEffort));

  if (settings && [FOLLOW, ...WORKER_RUNTIMES].includes(draft.worker)) {
    if (draft.worker !== (settings.configuredRuntime() ?? FOLLOW)) await step('worker Runtime', await runWorkers(`runtime ${draft.worker}`));
    const runtime = settings.preferred();
    if (isWorkerRuntime(runtime)) {
      const own = settings.own(runtime);
      const model = typeof form.worker_model === 'string' ? form.worker_model : null;
      if (model && model !== (own.model || DEFAULT)) await step('worker 模型', await runWorkers(`model ${model}`));
      const effort = typeof form.worker_effort === 'string' ? form.worker_effort : null;
      if (effort && effort !== (own.effort || DEFAULT)) await step('worker 推理档位', await runWorkers(`effort ${effort}`));
    }
  }
  return { stale: false, steps };
}

// The card once answered: what each step did, and what is set now.
export function settledCard({ stale = false, cancelled = false, steps = [], snapshot, settings = null }) {
  const failed = steps.filter((entry) => !entry.ok).length;
  const [title, template] = cancelled ? ['已取消，没有修改', 'grey'] : stale ? ['设置已在别处修改，没有应用', 'orange']
    : steps.length === 0 ? ['没有改动', 'grey'] : failed ? [`部分未应用（${failed} 项）`, 'orange'] : ['已应用', 'green'];
  const elements = [];
  if (steps.length) {
    elements.push({ tag: 'markdown', content: '**结果**' },
      table([['label', '项目', '120px'], ['result', '结果']], steps.map((entry) => ({ label: entry.label, result: `${entry.ok ? '✓' : '✗'} ${entry.text}` }))));
  }
  if (stale) elements.push(note('卡片发出后设置被其他命令改过：请重新发送 /runtime，在最新的设置上修改'));
  elements.push({ tag: 'markdown', content: '**当前**' }, nowTable(snapshot, settings), note('/runtime 再次修改'));
  return { schema: '2.0', config: { update_multi: true }, header: { template, title: plain(title), subtitle: plain('Runtime 与模型') }, body: { elements } };
}

// /status as a card: what the agent is doing, what it runs on, its tasks.
export function statusCard({ headline, snapshot, settings = null, queued = 0, tasksLine = '', slots = null }) {
  const working = /工作中|切换/.test(headline);
  const facts = [['状态', headline.replace(/^状态：/, '')], ...(queued > 0 ? [['排队消息', String(queued)]] : [])];
  if (settings) {
    facts.push(['后台任务', tasksLine.replace(/^后台任务：/, '').replace(/（\/tasks 查看）$/, '') || '无'],
      ...(slots ? [['worker 槽位', `${slots.used}/${slots.of}（跟进预留 ${slots.reserved}/${slots.reservedOf}）`]] : []));
  }
  return {
    schema: '2.0', config: { update_multi: true },
    header: { template: /未连接/.test(headline) ? 'red' : working ? 'blue' : 'green', title: plain('状态'), subtitle: plain(headline.replace(/^状态：/, '')) },
    body: { elements: [
      table([['key', '项目', '110px'], ['value', '内容']], facts.map(([key, val]) => ({ key, value: val }))),
      { tag: 'markdown', content: '**Runtime 与模型**' }, nowTable(snapshot, settings),
      note(`/runtime 修改 Runtime 与模型${settings ? ' · /tasks 查看任务' : ''}`),
    ] },
  };
}

// The workers' line /status adds to its text.
export function workerStatusLine(settings) {
  if (!settings) return '';
  const runtime = settings.preferred();
  if (!isWorkerRuntime(runtime)) return `worker：${runtime ?? '—'}（worker 不能运行在这里，新任务会被拒绝）`;
  const model = settings.modelFor(runtime);
  return `worker：${runtime} · 模型 ${model.model || 'CLI 默认'} · 推理档位 ${model.effort || 'CLI 默认'}`;
}
