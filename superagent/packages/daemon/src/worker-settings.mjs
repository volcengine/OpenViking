// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Which runtime, model and effort the workers scheduler's tasks run on, apart
// from the router's (/workers, worker-commands.mjs). A setting left unset
// follows the router, as workers did before they had settings of their own.
//
// A task takes its runtime, model and effort when it starts, or reopens for a
// new round whose session cannot go on, and keeps them while its session goes
// on: changing a setting, or the router's runtime, never moves a task that can
// carry on where it is. Only a runtime that cannot serve any more
// (its quota or usage limit used up) or that keeps failing moves a working
// task, to another runtime, through a handover (tasks.mjs).

import { classifyRuntimeFailure } from './notices.mjs';
import { getRuntimeDefinition } from './runtime-catalog.mjs';
import { WORKER_RUNTIMES } from './worker-executor.mjs';

// How long a runtime whose quota ran out is left out of new work and of the
// tasks about to take a turn. The first task to try it after that finds out
// whether it is back (one failed turn at most, which then moves on again).
export const WORKER_RUNTIME_COOLDOWN_MS = 30 * 60_000;

// A failure that says the account behind a runtime cannot serve more now:
// a quota, usage or billing limit, not one overloaded request. The runtime is
// out for every task, so it is not worth a retry on the same runtime.
const EXHAUSTED = new RegExp([
  'insufficient[_ ](?:quota|balance|credits?)',
  'quota[_ ](?:exceeded|exhausted|reached)',
  'exceeded your (?:current )?quota',
  'usage[_ ]limit',
  '(?:\\d+[- ]hour|weekly|daily|monthly) limit (?:reached|exceeded)',
  'hit your (?:[\\w-]+ )*limit',
  'credit balance is too low',
  'out of credits',
  'account(?:[_ ]is)?[_ ](?:overdue|suspended)',
  'AccountOverdue',
  'overdue balance',
  'payment required',
  '\\b402\\b',
  '额度(?:已)?(?:用尽|用完|不足|耗尽)',
  '配额(?:已)?(?:用尽|用完|不足|耗尽)',
  '余额不足',
  '欠费',
].join('|'), 'i');

// What a failed worker turn says about its runtime: `exhausted` (quota or
// usage used up: leave it, for every task), `recoverable` (an outage, a rate
// limit, a dropped connection: worth another runtime once retries on this one
// are spent), or neither (a cancelled turn, a rejected key, a bad request:
// another runtime would not do better, and the failure is reported).
export function classifyWorkerFailure(error) {
  const message = String(error ?? '');
  if (!message || message === 'stopped') return { exhausted: false, recoverable: false };
  if (EXHAUSTED.test(message)) return { exhausted: true, recoverable: true };
  const { blocked, recoverable } = classifyRuntimeFailure(message);
  return { exhausted: false, recoverable: !blocked && recoverable };
}

export function isWorkerRuntime(runtime) {
  return WORKER_RUNTIMES.includes(runtime);
}

export class WorkerSettings {
  // router(): the router's { runtime, model, effort } now.
  constructor({ store, config = {}, router = () => ({}), now = Date.now, logger = null }) {
    if (!store) throw new TypeError('store is required');
    Object.assign(this, { store, config, router, now, logger });
  }

  read(key) {
    return this.store.getSetting?.(`worker_${key}`) ?? '';
  }

  write(key, value) {
    this.store.setSetting(`worker_${key}`, value ?? '');
  }

  // The runtime set with /workers runtime, or null: workers follow the router.
  configuredRuntime() {
    const value = this.read('runtime');
    return isWorkerRuntime(value) ? value : null;
  }

  // The runtime new work prefers: the worker setting, else the router's.
  preferred() {
    return this.configuredRuntime() ?? this.router().runtime ?? null;
  }

  setRuntime(runtime) {
    if (runtime !== null && !isWorkerRuntime(runtime)) throw new Error(`workers do not run on ${runtime}`);
    this.write('runtime', runtime ?? '');
    // Choosing a runtime by hand is the owner saying it can serve again.
    if (runtime) this.clearUnavailable(runtime);
  }

  // Where a task goes when its runtime cannot serve: the /workers fallback
  // setting ('off' for none), else the router's backup runtime when workers
  // can run there.
  fallback() {
    const value = this.read('fallback');
    if (value === 'off') return null;
    if (isWorkerRuntime(value)) return value;
    return isWorkerRuntime(this.config.fallbackRuntime) ? this.config.fallbackRuntime : null;
  }

  // 'set', 'off', 'config' (the router's backup) or 'none'.
  fallbackSource() {
    const value = this.read('fallback');
    if (value === 'off') return 'off';
    if (isWorkerRuntime(value)) return 'set';
    return isWorkerRuntime(this.config.fallbackRuntime) ? 'config' : 'none';
  }

  // null: back to the default (the router's backup runtime); 'off': none.
  setFallback(value) {
    if (value !== null && value !== 'off' && !isWorkerRuntime(value)) throw new Error(`workers do not run on ${value}`);
    this.write('fallback', value ?? '');
    if (isWorkerRuntime(value)) this.clearUnavailable(value);
  }

  // The model and effort set with /workers for one runtime ('' when unset).
  own(runtime) {
    return { model: this.read(`model:${runtime}`), effort: this.read(`effort:${runtime}`) };
  }

  setModel(runtime, model) {
    this.write(`model:${runtime}`, model ?? '');
  }

  setEffort(runtime, effort) {
    this.write(`effort:${runtime}`, effort ?? '');
  }

  // The model and effort a task starting on `runtime` takes: what /workers
  // set for that runtime, else the router's when it runs on the same one,
  // else the CLI's own default (null).
  modelFor(runtime) {
    const own = this.own(runtime);
    const router = this.router();
    const same = router.runtime === runtime;
    const routerModel = same && router.model && router.model !== runtime ? router.model : null;
    return {
      model: own.model || routerModel || null,
      effort: own.effort || (same ? router.effort || null : null),
      // Where each value came from, for /workers.
      modelSource: own.model ? 'set' : routerModel ? 'router' : 'default',
      effortSource: own.effort ? 'set' : same && router.effort ? 'router' : 'default',
    };
  }

  // A runtime left out for now: { until, reason }, or null.
  unavailable(runtime) {
    if (!runtime) return null;
    const raw = this.read(`unavailable:${runtime}`);
    if (!raw) return null;
    try {
      const mark = JSON.parse(raw);
      return Number(mark?.until) > this.now() ? { until: Number(mark.until), reason: String(mark.reason ?? '') } : null;
    } catch {
      return null;
    }
  }

  markUnavailable(runtime, reason, forMs = WORKER_RUNTIME_COOLDOWN_MS) {
    if (!runtime) return;
    this.write(`unavailable:${runtime}`, JSON.stringify({ until: this.now() + forMs, reason: String(reason ?? '').slice(0, 300) }));
    this.logger?.warn?.('worker_runtime_unavailable', { runtime, forMs });
  }

  clearUnavailable(runtime) {
    if (this.read(`unavailable:${runtime}`)) this.write(`unavailable:${runtime}`, '');
  }

  // The runtime a new task, or a round that cannot go on in its session, starts on:
  // the preferred one, unless it is out of quota and the fallback is not.
  forNewWork() {
    const preferred = this.preferred();
    if (!this.unavailable(preferred)) return preferred;
    const backup = this.fallback();
    return backup && backup !== preferred && !this.unavailable(backup) ? backup : preferred;
  }

  // Where a task on `runtime` goes when it has to leave it: the preferred
  // runtime (the owner may already have moved workers elsewhere), else the
  // fallback; never one that is out itself. null: nowhere to go.
  switchTarget(runtime) {
    for (const candidate of [this.preferred(), this.fallback()]) {
      if (candidate && candidate !== runtime && isWorkerRuntime(candidate) && !this.unavailable(candidate)) return candidate;
    }
    return null;
  }

  efforts(runtime) {
    return getRuntimeDefinition(runtime)?.efforts ?? [];
  }
}
