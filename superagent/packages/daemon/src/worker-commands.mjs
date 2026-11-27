// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// /workers: the runtime, model and effort the workers scheduler's tasks run
// on, apart from the router's (worker-settings.mjs). Answered by the Core
// without the model, like the other slash commands; owner and direct chat
// only, like /tasks.
//
//   /workers                              what workers run on, tasks at work per runtime
//   /workers runtime <name | router>      the runtime new and reopened tasks take
//   /workers model <name | default>       the model on that runtime (<runtime>:<name> for another)
//   /workers effort <level | default>     the effort on that runtime
//   /workers fallback <name | off | default>  where a task goes when its runtime's quota runs out
//   /workers slots <n> [reserved] | default   turns at once, plus how many more only follow-ups may take
//
// A setting applies to work that starts after it: a new task, or a task that
// reopens for a new round whose session cannot go on. A task at work, or one
// that reopens and can go on in its session, keeps its runtime, model and
// effort, unless its runtime cannot serve (tasks.mjs, handover). Slots apply
// to the next turn the scheduler starts; a running turn is never stopped.

import { matchPrefix } from './commands.mjs';
import { RUNTIME_ALIASES, modelIdProblem, parseRuntimeModelSelector, staticModelEntries } from './runtime-catalog.mjs';
import { MAX_WORKER_SLOTS, isWorkerRuntime } from './worker-settings.mjs';
import { RESERVED_SLOTS, WORKER_SLOTS } from './tasks.mjs';
import { WORKER_RUNTIMES } from './worker-executor.mjs';

const USAGE = '用法：/workers runtime <名称|router> · /workers model <名称|default> · /workers effort <档位|default> · /workers fallback <名称|off|default> · /workers slots <数量> [跟进预留]';
const SCOPE = '只影响之后新建的任务，以及复开时原会话已续不上的任务；进行中的任务和复开后能续上原会话的任务留在原 Runtime，只有原 Runtime 额度用尽或持续失败时才交接到其他 Runtime。';
const FOLLOW = new Set(['router', 'follow', 'default', '跟随']);
// /workers slots default: back to the instance's configured counts.
const RESET = new Set(['default', '默认']);
const SLOTS_USAGE = `用法：/workers slots <1-${MAX_WORKER_SLOTS}> [跟进预留 0-${MAX_WORKER_SLOTS}] · /workers slots default`;
const SLOTS_EFFECT = '调高后排队中的任务立即开跑；调低不打断正在跑的轮次，它们跑完后自然降到新值。';
const VERBS = ['runtime', 'model', 'effort', 'fallback', 'slots'];
const GROUP_ONLY = 'worker 的 Runtime、模型和槽位设置只能在私聊里改；群里可以用 /workers 看占用情况。';

// A count typed by the owner: whole digits only, so "6x" is refused.
const count = (value) => (/^\d+$/.test(String(value ?? '')) ? Number.parseInt(value, 10) : NaN);

// The reserved slots come on top of the others (TaskManager.pump), so the
// line says both and the most turns that can run together.
function slotsLine({ slots, reservedSlots }) {
  return reservedSlots > 0
    ? `并发槽位：${slots}，另有 ${reservedSlots} 个只给跟进任务（最多同时 ${slots + reservedSlots} 轮）`
    : `并发槽位：${slots}（不另留跟进专用槽位）`;
}

function minutes(ms) {
  return Math.max(1, Math.ceil(ms / 60_000));
}

// A runtime name or alias, by unique prefix: { runtime } or { error }.
function runtimeNamed(arg) {
  const matches = matchPrefix(String(arg).toLowerCase(), Object.keys(RUNTIME_ALIASES));
  const targets = [...new Set(matches.map((alias) => RUNTIME_ALIASES[alias]))];
  if (targets.length !== 1) return { error: `未知或有歧义的 Runtime：${arg}\nworker 可用：${WORKER_RUNTIMES.join('、')}` };
  if (!isWorkerRuntime(targets[0])) return { error: `worker 不能运行在 ${targets[0]} 上，可用：${WORKER_RUNTIMES.join('、')}` };
  return { runtime: targets[0] };
}

function liveByRuntime(tasks) {
  const counts = new Map();
  for (const task of tasks?.overview({ recent: 0 }).live ?? []) counts.set(task.runtime ?? '?', (counts.get(task.runtime ?? '?') ?? 0) + 1);
  return counts;
}

function liveElsewhere(tasks, runtime) {
  let n = 0;
  for (const [name, count] of liveByRuntime(tasks)) if (name !== runtime) n += count;
  return n;
}

const SOURCE = { set: '单独设置', router: '跟随 router', default: 'CLI 默认' };
// A value and where it came from: "gpt-6-astra（单独设置）", or "CLI 默认".
const valued = (value, source) => (value ? `${value}（${SOURCE[source]}）` : SOURCE.default);

export function workersOverview(settings, tasks, now = Date.now()) {
  const configured = settings.configuredRuntime();
  const preferred = settings.preferred();
  const lines = [`后台 worker（${SCOPE}）`];
  lines.push(`Runtime：${preferred ?? '（未知）'}（${configured ? '单独设置' : '跟随 router'}）`);
  if (!isWorkerRuntime(preferred)) {
    lines.push(`worker 不能运行在 ${preferred} 上，新任务会被拒绝；请用 /workers runtime <${WORKER_RUNTIMES.join('|')}> 指定。`);
  } else {
    const model = settings.modelFor(preferred);
    lines.push(`模型：${valued(model.model, model.modelSource)}`);
    lines.push(`推理档位：${valued(model.effort, model.effortSource)}`);
  }
  const fallback = settings.fallback();
  const source = settings.fallbackSource();
  lines.push(`备用 Runtime：${fallback
    ? `${fallback}（${source === 'set' ? '单独设置' : '同 router 的备用 Runtime'}）`
    : source === 'off' ? '已关闭' : '未设置'}；额度用尽或持续失败时任务交接到这里`);
  for (const runtime of WORKER_RUNTIMES) {
    const out = settings.unavailable(runtime);
    if (out) lines.push(`暂不可用：${runtime}（额度用尽，约 ${minutes(out.until - now)} 分钟后再试；/workers runtime ${runtime} 可立即恢复）`);
  }
  const limits = tasks?.limits?.();
  if (limits) lines.push(slotsLine(limits));
  const live = liveByRuntime(tasks);
  if (live.size) lines.push(`进行中的任务：${[...live].map(([runtime, count]) => `${runtime} ${count}`).join(' · ')}`);
  lines.push(USAGE);
  return lines.join('\n');
}

// The models a runtime is known to serve: the router's live catalogue when
// workers run on the router's runtime, else the runtime's static names.
async function knownModels(runtime, router) {
  if (router && router.runtime === runtime) {
    try {
      const snapshot = await router.snapshot({ catalog: 'refresh' });
      if (snapshot.runtime === runtime) return { live: true, ids: [...new Set((snapshot.models ?? []).flatMap((entry) => [entry.id, entry.resolvedModel].filter(Boolean)))] };
    } catch { /* offline: no catalogue */ }
  }
  return { live: false, ids: staticModelEntries(runtime).map((entry) => entry.id) };
}

async function modelCommand(settings, tasks, router, arg) {
  const selector = arg ? parseRuntimeModelSelector(arg) : null;
  const runtime = selector?.qualified ? selector.runtime : settings.preferred();
  if (!isWorkerRuntime(runtime)) return { ok: false, text: `worker 不能运行在 ${runtime} 上，先用 /workers runtime 指定 worker 的 Runtime` };
  const current = settings.modelFor(runtime);
  const known = await knownModels(runtime, router);
  if (!arg) {
    return { ok: true, text: [
      `worker Runtime：${runtime}`,
      `模型：${valued(current.model, current.modelSource)}`,
      ...(known.ids.length ? [`可选：${known.ids.join('、')}`] : ['可直接输入模型名（该 Runtime 的模型目录要到 worker 运行时才能读到）']),
      '用法：/workers model <名称|default>；为其他 Runtime 设置用 /workers model <runtime>:<名称>',
    ].join('\n') };
  }
  const name = selector.qualified ? selector.model : selector.raw;
  if (FOLLOW.has(name.toLowerCase())) {
    settings.setModel(runtime, '');
    const now = settings.modelFor(runtime);
    return { ok: true, text: `worker 在 ${runtime} 上的模型已恢复默认：${now.model || 'CLI 默认'}（${SOURCE[now.modelSource]}）。${SCOPE}` };
  }
  const problem = modelIdProblem(name, runtime);
  if (problem) return { ok: false, text: problem === 'length' ? `模型名太长：${name.length} 个字符` : `无效的模型名：${name}` };
  let model = name;
  if (known.ids.length) {
    const matches = matchPrefix(name, known.ids);
    if (matches.length > 1) return { ok: false, text: `模型名有歧义：${matches.join('、')}` };
    if (matches.length === 1) model = matches[0];
    else if (known.live) return { ok: false, text: `未找到模型：${name}\n可选：${known.ids.join('、')}` };
  }
  settings.setModel(runtime, model);
  const effort = settings.own(runtime).effort;
  return { ok: true, text: `worker 在 ${runtime} 上的模型已设为 ${model}${effort ? `（推理档位 ${effort}）` : ''}。${SCOPE}`
    + (liveElsewhere(tasks, null) ? `\n进行中的任务保持原模型。` : '') };
}

function effortCommand(settings, arg) {
  const runtime = settings.preferred();
  if (!isWorkerRuntime(runtime)) return { ok: false, text: `worker 不能运行在 ${runtime} 上，先用 /workers runtime 指定 worker 的 Runtime` };
  const levels = settings.efforts(runtime);
  const current = settings.modelFor(runtime);
  if (!arg) {
    return { ok: true, text: `worker Runtime：${runtime}\n推理档位：${valued(current.effort, current.effortSource)}\n可选：${levels.join('、')}\n用法：/workers effort <档位|default>` };
  }
  if (FOLLOW.has(arg.toLowerCase())) {
    settings.setEffort(runtime, '');
    const now = settings.modelFor(runtime);
    return { ok: true, text: `worker 在 ${runtime} 上的推理档位已恢复默认：${now.effort || 'CLI 默认'}（${SOURCE[now.effortSource]}）。${SCOPE}` };
  }
  const matches = matchPrefix(arg.toLowerCase(), levels);
  if (matches.length !== 1) return { ok: false, text: `${matches.length ? '档位有歧义' : '不支持的档位'}：${arg}\n可选：${levels.join('、')}` };
  settings.setEffort(runtime, matches[0]);
  return { ok: true, text: `worker 在 ${runtime} 上的推理档位已设为 ${matches[0]}。${SCOPE}` };
}

function runtimeCommand(settings, tasks, arg) {
  if (!arg) {
    const preferred = settings.preferred();
    return { ok: true, text: `worker Runtime：${preferred}（${settings.configuredRuntime() ? '单独设置' : '跟随 router'}）\n可选：${WORKER_RUNTIMES.join('、')}、router（跟随 router）\n用法：/workers runtime <名称|router>` };
  }
  if (FOLLOW.has(arg.toLowerCase())) {
    settings.setRuntime(null);
    const now = settings.preferred();
    return { ok: true, text: `worker 改为跟随 router，当前是 ${now}${isWorkerRuntime(now) ? '' : `（worker 不能运行在 ${now} 上，新任务会被拒绝）`}。${SCOPE}` };
  }
  const named = runtimeNamed(arg);
  if (named.error) return { ok: false, text: named.error };
  const { runtime } = named;
  const wasOut = settings.unavailable(runtime);
  settings.setRuntime(runtime);
  const model = settings.modelFor(runtime);
  const elsewhere = liveElsewhere(tasks, runtime);
  return { ok: true, text: [
    `后台 worker 的 Runtime 已设为 ${runtime}（模型 ${model.model || 'CLI 默认'}${model.effort ? ` · 档位 ${model.effort}` : ''}）。`,
    '之后新建的任务在这里运行；复开的任务能续上原会话就留在原 Runtime，续不上时在这里接着做，并带上原 worker 的交接信息。',
    elsewhere ? `进行中的 ${elsewhere} 个任务继续在原 Runtime 上做完。` : '',
    wasOut ? `${runtime} 之前因额度用尽被暂停，现已恢复使用。` : '',
  ].filter(Boolean).join('\n') };
}

function slotsCommand(settings, tasks, arg) {
  const inForce = () => tasks?.limits?.() ?? settings.limits({ slots: WORKER_SLOTS, reservedSlots: RESERVED_SLOTS });
  if (!arg) return { ok: true, text: `${slotsLine(inForce())}\n${SLOTS_USAGE}\n${SLOTS_EFFECT}` };
  const words = arg.split(/\s+/).filter(Boolean);
  if (words.length === 1 && RESET.has(words[0].toLowerCase())) {
    settings.clearSlots();
    tasks?.pump?.();
    return { ok: true, text: `已恢复实例配置。${slotsLine(inForce())}` };
  }
  if (words.length > 2) return { ok: false, text: SLOTS_USAGE };
  // Without a reserved count, the one in force stays (setSlots).
  const set = settings.setSlots(count(words[0]), words[1] === undefined ? undefined : count(words[1]));
  if (!set.ok) return { ok: false, text: `${set.text}\n${SLOTS_USAGE}` };
  tasks?.pump?.();
  return { ok: true, text: `已设置。${slotsLine(inForce())}\n${SLOTS_EFFECT}` };
}

function fallbackCommand(settings, arg) {
  if (!arg) {
    const fallback = settings.fallback();
    return { ok: true, text: `备用 Runtime：${fallback ?? (settings.fallbackSource() === 'off' ? '已关闭' : '未设置')}\n任务的 Runtime 额度用尽或持续失败时，交接到 /workers runtime 设置的 Runtime，再不行交接到备用 Runtime。\n用法：/workers fallback <名称|off|default>` };
  }
  const value = arg.toLowerCase();
  if (value === 'off') {
    settings.setFallback('off');
    return { ok: true, text: '已关闭 worker 的备用 Runtime：额度用尽时任务只会交接到 /workers runtime 设置的 Runtime（若不同），否则按失败上报。' };
  }
  if (value === 'default') {
    settings.setFallback(null);
    return { ok: true, text: `worker 的备用 Runtime 已恢复默认：${settings.fallback() ?? '未设置'}（同 router 的备用 Runtime）。` };
  }
  const named = runtimeNamed(arg);
  if (named.error) return { ok: false, text: named.error };
  settings.setFallback(named.runtime);
  return { ok: true, text: `worker 的备用 Runtime 已设为 ${named.runtime}。` };
}

// What a group sees: how many workers are busy, queued or waiting, never the
// runtimes, models, fallbacks or slot configuration behind them.
function publicWorkersOverview(tasks) {
  const { live } = tasks.overview({ recent: 0 });
  const count = (status) => live.filter((task) => task.status === status).length;
  const parts = [`${count('running')} 个在忙`];
  if (count('queued')) parts.push(`${count('queued')} 个排队`);
  if (count('waiting')) parts.push(`${count('waiting')} 个等回复`);
  return [`后台 worker：${parts.join('，')}`, `进行中的任务：${live.length} 个`].join('\n');
}

export async function executeWorkersCommand(command, { event, connectors, settings, tasks, router = null, now = Date.now(), publicView = false }) {
  if (publicView) {
    if (event?.chatType !== 'group') return { ok: false, text: GROUP_ONLY };
    if (String(command.arg ?? '').trim()) return { ok: false, text: GROUP_ONLY };
    if (!tasks) return { ok: true, text: '这个实例没有启用后台 worker。' };
    return { ok: true, text: publicWorkersOverview(tasks) };
  }
  if (event?.chatType !== 'p2p' || event.senderType !== 'user' || !connectors?.isOwner?.(event)) {
    return { ok: false, text: '仅限已绑定的 owner 在与机器人的私聊中使用 /workers。' };
  }
  if (!settings) return { ok: false, text: '这个实例没有启用 workers 调度（agents[].scheduler = "workers"），没有后台 worker。' };
  const text = String(command.arg ?? '').trim();
  const [token = '', ...rest] = text.split(/\s+/).filter(Boolean);
  if (!token) return { ok: true, text: workersOverview(settings, tasks, now) };
  const verbs = matchPrefix(token.toLowerCase(), VERBS);
  if (verbs.length !== 1) return { ok: false, text: USAGE };
  const arg = rest.join(' ');
  try {
    if (verbs[0] === 'runtime') return runtimeCommand(settings, tasks, arg);
    if (verbs[0] === 'model') return await modelCommand(settings, tasks, router, arg);
    if (verbs[0] === 'effort') return effortCommand(settings, arg);
    if (verbs[0] === 'slots') return slotsCommand(settings, tasks, arg);
    return fallbackCommand(settings, arg);
  } catch (error) {
    return { ok: false, text: `设置失败：${error.message}` };
  }
}
