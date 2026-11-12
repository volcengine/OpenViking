// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { randomUUID } from 'node:crypto';
import { cognitionHistoryPrefix } from './cognition.mjs';
import { adaptRuntime } from './runtime-adapter.mjs';
import { createRuntimeFactories } from './runtime-factory.mjs';
import { metadataTag } from './runtime-input.mjs';
import { docCommentNotice } from './conversation-policy.mjs';
import { DOC_COMMENT_CONVERSATION } from './lark-doc-comments.mjs';
import {
  DEFAULT_RUNTIME_ID,
  MODEL_ERROR_KINDS,
  RUNTIME_IDS,
  assertModelId,
  canonicalRuntimeModel,
  classifyModelError,
  getRuntimeDefinition,
  isDirectRuntimeAlias,
  parseRuntimeModelSelector,
  resolveRuntimeModelTarget,
  staticModelEntries,
  supportedModelEfforts,
  compatibleModelEffort,
  currentModelEntry,
} from './runtime-catalog.mjs';

// Loop breaker (breakLoop): how long a cancelled turn may take to end before
// its session is replaced, and the window in which a second stop in the same
// session replaces it instead of cancelling in place again.
const LOOP_CANCEL_GRACE_MS = 30_000;
const LOOP_RESET_WINDOW_MS = 10 * 60_000;

// A batch with a document comment carries the rules for answering one, as a
// daemon notice after its last comment. Other inputs never get them.
function withDocCommentRules(items, config) {
  const last = items.findLastIndex((item) => item?.chatType === DOC_COMMENT_CONVERSATION);
  if (last < 0) return items;
  return items.map((item, index) => (index === last ? { ...item, text: `${item.text}\n${docCommentNotice(config)}` } : item));
}

// Whether a manifest model selector names an actual model. Setup writes the bare
// runtime alias (`"model": "dsh"`), which only names the runtime and leaves the
// route to the runtime's own default; a selector that names a model keeps the
// caller in charge of which model runs, so nothing may replace it.
function selectorNamesModel(value) {
  const raw = String(value ?? '').trim();
  if (!raw) return false;
  const selector = parseRuntimeModelSelector(raw);
  if (selector.qualified) return Boolean(selector.model);
  return !isDirectRuntimeAlias(raw);
}

// The provider route a model id is on. The first slash splits it: a route name
// cannot contain one while a model id may (dsh's `openrouter/deepseek/deepseek-chat`).
function routeOf(model) {
  const id = String(model ?? '');
  const slash = id.indexOf('/');
  return slash < 0 ? '' : id.slice(0, slash);
}

export class AgentRuntime {
  constructor(config, store, logger, options = {}) {
    this.config = config;
    this.store = store;
    this.logger = logger;
    this.onTurnFailed = options.onTurnFailed ?? null;
    this.onTurnSucceeded = options.onTurnSucceeded ?? null;
    this.onTurnAbandoned = options.onTurnAbandoned ?? null;
    // The end of a turn the loop breaker stopped, however the transport saw it.
    this.onTurnStopped = options.onTurnStopped ?? null;
    this.onAvailable = options.onAvailable ?? null;
    this.onAuthorityChanged = options.onAuthorityChanged ?? null;
    this.persistRuntimePreference = options.persistRuntimePreference ?? null;
    this.recordActivity = options.recordActivity ?? null;
    this.clock = options.clock ?? (() => Date.now());
    this.loopCancelGraceMs = options.loopCancelGraceMs ?? LOOP_CANCEL_GRACE_MS;
    this.loopResetWindowMs = options.loopResetWindowMs ?? LOOP_RESET_WINDOW_MS;
    // The loop stop in flight, and the sessions lately stopped in place.
    this.loopBreak = null;
    this.loopStops = [];
    this.factories = createRuntimeFactories(config, store, logger, options);
    this.backend = null;
    this.stagedBackend = null;
    this.runtime = DEFAULT_RUNTIME_ID;
    this.switching = false;
    this.transitioning = false;
    this.pendingTransitions = 0;
    this.transitionBarrier = Promise.resolve();
    this.stopping = false;
    this.revision = 0;
    this.cachedModels = [];
  }

  get threadId() { return this.backend?.threadId ?? null; }
  // `null` is a real value now: it means the model has no effort override. Only
  // an absent one falls back to the transport default.
  get effort() {
    const effort = this.backend?.effort;
    return effort === undefined ? 'medium' : effort;
  }
  get model() {
    const model = this.backend?.model ?? '';
    return model || getRuntimeDefinition(this.runtime)?.canonicalDefault || '';
  }
  get state() {
    const state = this.backend?.state ?? { connected: false, activeTurn: 0, queued: 0 };
    return { ...state, connected: state.connected && this.pendingTransitions === 0 };
  }
  get turnInProgress() { return this.pendingTransitions > 0 || Boolean(this.backend?.turnInProgress); }
  get workStatus() {
    const working = this.pendingTransitions > 0 || Boolean(this.backend?.modelWorking);
    return {
      working,
      // A turn may pause for tools and resume sampling several times. Keep the
      // user-facing clock anchored to the root turn instead of resetting it for
      // every reasoning/message item.
      startedAtMs: working ? (this.backend?.turnStartedAtMs ?? null) : null,
      connected: Boolean(this.state.connected),
      queued: this.state.queued ?? 0,
      switching: this.pendingTransitions > 0,
    };
  }

  _backendOptions(overrides = {}) {
    const cognition = this.store.getCognitionState();
    return {
      onTurnFailed: (info) => this._turnEnded('failed', info),
      onTurnSucceeded: (info) => this._turnEnded('succeeded', info),
      onTurnAbandoned: (info) => this._turnEnded('abandoned', info),
      onAvailable: this.onAvailable,
      authorityToken: cognition.authorityToken,
      cognitionEpoch: cognition.epoch,
      ...overrides,
    };
  }

  _instantiate(runtime, overrides = {}) {
    const implementation = this.factories[runtime](this._backendOptions(overrides));
    // Whether a route the caller did not name may be adopted is the coordinator's
    // decision, and a transport is a plain object at this point: publish it before
    // start so a handshake that cannot start on its configured route knows whether
    // another one from its live catalogue is allowed. A transport without that
    // concern simply never reads the field.
    //
    // Best effort on purpose: a transport that does not accept the field keeps
    // starting exactly as it did before (no adoption), which must never become a
    // startup failure of its own.
    if (overrides.allowModelFallback !== undefined) {
      try {
        implementation.allowModelFallback = overrides.allowModelFallback === true;
      } catch {
        this.logger.warn('runtime_fallback_policy_unsettable', { runtime });
      }
    }
    return adaptRuntime(runtime, implementation);
  }

  _authorityChanged(cognition) {
    this.revision += 1;
    try {
      this.onAuthorityChanged?.({
        runtime: cognition.runtime,
        epoch: cognition.epoch,
        authorityToken: cognition.authorityToken,
      });
    } catch (error) {
      // The Store commit is the linearization point. A notification consumer
      // cannot roll it back or make the caller retry an already-applied switch.
      this.logger.warn('runtime_authority_notification_failed', { error: error.message });
    }
  }

  async start() {
    this.stopping = false;
    let cognition = this.store.getCognitionState();
    const persistedRuntime = RUNTIME_IDS.includes(cognition.runtime)
      ? cognition.runtime
      : DEFAULT_RUNTIME_ID;
    const configuredSelector = String(this.config.initialModel || '').trim();
    const freshTarget = resolveRuntimeModelTarget(configuredSelector);
    const freshCognition = this.store.hasCognitionRuntime?.() === false;
    this._assertConnectorSupport(freshTarget.runtime);
    this.runtime = freshCognition ? freshTarget.runtime : persistedRuntime;
    // A daemon generation must never inherit the previous process's authority.
    // If that process crashed with a detached MCP sidecar still alive, rotating
    // the token here fences it before the replacement backend starts.
    cognition = this.store.initializeCognition(this.runtime, randomUUID());
    if (!freshCognition && configuredSelector && freshTarget.runtime !== persistedRuntime) {
      // Setup has changed the desired runtime since SQLite last committed one.
      // Reuse the same staged handoff as /runtime. There is no old backend in
      // this daemon generation, and the fresh token above has already fenced a
      // sidecar that may have survived the previous process.
      await this._selectRuntime(freshTarget.runtime, undefined, { persistPreference: false });
      return;
    }
    this.backend = this._instantiate(this.runtime, {
      // A manifest that names only the runtime (`"model": "dsh"`) leaves the
      // route to that runtime's own default, so startup may adopt a servable one
      // when the default serves nothing. A manifest that names a model keeps it:
      // start then fails loudly instead of running something else.
      allowModelFallback: !selectorNamesModel(configuredSelector),
      ...(freshCognition ? { initialModel: freshTarget.model } : {}),
    });
    await this.backend.start();
    this._authorityChanged(cognition);
  }

  whenIdle() { return this.backend?.whenIdle() ?? Promise.resolve(); }

  // Volc's tool executor is the remote Environment Worker, which only has the
  // Lark CLI path; it cannot reach the daemon's agent tools.
  _assertConnectorSupport(runtime) {
    if (runtime === 'volc' && this.config.connectors.includes('qq')) {
      throw new Error('QQ is not supported on the Volc runtime');
    }
    if (runtime === 'volc' && this.config.connectors.includes('telegram')) {
      throw new Error('Telegram is not supported on the Volc runtime');
    }
    if (runtime === 'volc' && this.config.connectors.includes('slack')) {
      throw new Error('Slack is not supported on the Volc runtime');
    }
    if (runtime === 'volc' && this.config.connectors.includes('onebot')) {
      throw new Error('OneBot is not supported on the Volc runtime');
    }
    if (runtime === 'volc' && this.config.connectors.includes('weixin')) {
      throw new Error('Weixin is not supported on the Volc runtime');
    }
  }

  prepareInputContext() { return this.backend?.prepareInputContext?.(); }

  submit(items, { admission = 'enqueue' } = {}) {
    if (this.stopping) return { accepted: false, reason: 'stopping' };
    if (this.pendingTransitions > 0) return { accepted: false, reason: 'switching' };
    if (!this.backend) return { accepted: false, reason: 'not-started' };
    if (!Array.isArray(items) || items.length === 0) {
      return { accepted: false, reason: 'empty-batch' };
    }
    const definition = getRuntimeDefinition(this.runtime);
    if (definition?.capabilities.scopePolicy === 'single-conversation') {
      const scopes = new Set(items.map((item) => item?.scope?.key ?? item?.scopeKey
        ?? item?.scope?.conversationKey ?? item?.conversationKey));
      if (scopes.has(undefined) || scopes.has(null) || scopes.has('') || scopes.size !== 1) {
        return { accepted: false, reason: 'invalid-scope' };
      }
    }
    return this.backend.submit(withDocCommentRules(items, this.config), { admission });
  }

  _interrupt() { return this.backend?.interrupt() ?? Promise.resolve(); }

  // Every transport's turn end passes here. The turn a loop stop ended goes
  // to onTurnStopped whatever the transport saw (a cancelled prompt reports as
  // failed on some, a replaced session as abandoned): nobody is told it
  // failed, and what it held is recovered there.
  _turnEnded(kind, info) {
    if (this.loopBreak) {
      this._stopEnded(kind, info);
      return;
    }
    // A failed turn may have died on the model rather than on the work. Record
    // which, so whoever reads the failure can tell an unknown model from a
    // rejected key or a gateway outage instead of parsing prose.
    if (kind === 'failed' && info?.error) {
      const classified = classifyModelError(info.error);
      if (classified.kind) {
        info.modelError = classified;
        this.logger.warn('runtime_model_error', {
          runtime: this.runtime, phase: 'turn', kind: classified.kind,
          status: classified.status || undefined, code: classified.code || undefined,
        });
      }
    }
    const callback = kind === 'failed' ? this.onTurnFailed
      : kind === 'succeeded' ? this.onTurnSucceeded : this.onTurnAbandoned;
    callback?.(info);
  }

  // Loop breaker, asked for by ActionService when the model keeps redoing a
  // decision already made: such a turn never ends and holds every later
  // input. Stop it in place: the transport cancels the live turn, which then
  // ends as a normal turn in the same session (its inputs settled, the queue
  // drained, a notice in front of the next prompt). A session already stopped
  // within loopResetWindowMs, a transport that cannot cancel in place, or a
  // cancel that has not ended the turn after loopCancelGraceMs gets a fresh
  // session that keeps the recent history instead.
  breakLoop({ tool = null, decisionSeq = null, count = 0 } = {}) {
    const details = { runtime: this.runtime, tool, decisionSeq, count };
    const busy = this.stopping ? 'stopping' : this.loopBreak ? 'stop-in-flight'
      : this.pendingTransitions > 0 ? 'transition' : !this.backend?.turnInProgress ? 'no-turn' : null;
    if (busy) {
      this.logger.info('turn_loop_stop_skipped', { ...details, reason: busy });
      return { action: 'skipped', reason: busy };
    }
    const now = this.clock();
    this.loopStops = this.loopStops.filter((entry) => now - entry.atMs < this.loopResetWindowMs);
    const sessionId = this.threadId;
    const repeated = this.loopStops.some((entry) => entry.sessionId === sessionId);
    const stop = { ...details, sessionId, startedAtMs: now, action: 'cancel', timer: null };
    this.loopBreak = stop;
    this.logger.warn('turn_loop_detected', { ...details, repeated });
    if (!repeated) {
      let cancelled = false;
      try { cancelled = this.backend.cancelTurn(); } catch (error) {
        this.logger.warn('turn_cancel_failed', { runtime: this.runtime, error: error.message });
      }
      if (cancelled) {
        stop.timer = setTimeout(() => this._loopCancelExpired(stop), this.loopCancelGraceMs);
        stop.timer.unref?.();
        return { action: 'cancel' };
      }
    }
    stop.action = 'new-session';
    void this._replaceLoopingSession(stop);
    return { action: 'new-session' };
  }

  _loopCancelExpired(stop) {
    if (this.loopBreak !== stop) return;
    // Ended without telling (no inputs to report): nothing is left to replace.
    if (!this.backend?.turnInProgress) {
      this._stopEnded('ended', { inboundSeqs: [] });
      return;
    }
    stop.cancelTimedOut = true;
    stop.action = 'new-session';
    void this._replaceLoopingSession(stop);
  }

  async _replaceLoopingSession(stop) {
    try {
      const result = await this.transition({ kind: 'loop-reset', expectedRuntime: stop.runtime, stop });
      if (result?.ok === false) this.logger.info('turn_loop_reset_skipped', { runtime: this.runtime, reason: result.reason });
    } catch (error) {
      this.logger.warn('turn_loop_reset_failed', { runtime: this.runtime, error: error.message });
    } finally {
      // Not replaced (another transition won, or it failed): release the stop
      // so a turn that still repeats itself can be stopped again.
      if (this.loopBreak === stop) {
        clearTimeout(stop.timer);
        this.loopBreak = null;
        this._notifyStopped({ inboundSeqs: [] });
      }
    }
  }

  // Every way a loop stop ends comes here, so the daemon always resets its
  // repeat counts: a stop ended without a transport callback (an interrupted
  // turn with no seq or receipt to report) would otherwise leave them armed
  // and blind to the next loop.
  _stopEnded(endedAs, info) {
    this._endLoopBreak(endedAs);
    this._notifyStopped(info);
  }

  _notifyStopped(info) {
    (this.onTurnStopped ?? this.onTurnSucceeded)?.(info);
  }

  _endLoopBreak(endedAs) {
    const stop = this.loopBreak;
    this.loopBreak = null;
    clearTimeout(stop.timer);
    const now = this.clock();
    if (stop.action === 'cancel') this.loopStops.push({ sessionId: stop.sessionId, atMs: now });
    this.backend?.setTurnNotice(this._loopNotice(stop));
    const fields = {
      runtime: stop.runtime, action: stop.action, endedAs, tool: stop.tool, decisionSeq: stop.decisionSeq,
      count: stop.count, durationMs: now - stop.startedAtMs, ...(stop.cancelTimedOut ? { cancelTimedOut: true } : {}),
    };
    this.logger.warn('turn_loop_stopped', fields);
    try {
      this.recordActivity?.('agent.loop_stopped', {
        inboundSeq: stop.decisionSeq ?? undefined,
        status: stop.action === 'cancel' ? 'CANCELLED' : 'NEW_SESSION',
        summary: `${stop.tool} repeated ${stop.count} times without effect; turn ${stop.action === 'cancel' ? 'cancelled in place' : 'ended in a new session'}`,
        metadata: fields,
      });
    } catch (error) {
      this.logger.warn('activity_persist_failed', { kind: 'agent.loop_stopped', error: error.message });
    }
  }

  // Says the input is done only when the ledger has its decision (a repeated
  // timer, or an instance without explicitSilence, records none).
  _loopNotice(stop) {
    let row = null;
    let decided = false;
    try {
      row = stop.decisionSeq == null ? null : this.store.inboundBySeq?.(stop.decisionSeq);
      decided = Boolean(row?.scopeKey && this.store.coveringDecision?.(row.scopeKey, row.seq));
    } catch {}
    const target = row?.eventId ? ` for event=${metadataTag(row.eventId)}` : '';
    return `[daemon notice] The daemon stopped your previous turn: it called ${stop.tool}${target} ${stop.count} times`
      + ' after that call had nothing left to change.'
      + (decided ? ' That input already has its decision; take no further action on it.' : ' Do not repeat that call.')
      + `${stop.action === 'new-session' ? ' You are now in a new session; what came before is in the history above.' : ''}`
      + ' Go on with the inputs below.';
  }

  async _listModels() {
    const backend = this.backend;
    const current = await backend?.listModels() ?? [];
    if (backend !== this.backend) return this.cachedModels;
    const models = current.length ? current : this.cachedModels.length ? this.cachedModels
      : getRuntimeDefinition(this.runtime)?.capabilities.modelCatalog === 'static'
        ? staticModelEntries(this.runtime) : [];
    const seen = new Set();
    this.cachedModels = models.filter((model) => {
      if (!model?.id || seen.has(model.id)) return false;
      seen.add(model.id);
      return true;
    });
    return this.cachedModels;
  }

  snapshot({ catalog = 'cached' } = {}) {
    if (!['cached', 'refresh'].includes(catalog)) {
      throw new TypeError(`unsupported catalog mode: ${catalog}`);
    }
    if (catalog === 'refresh') {
      return this._listModels().then((models) => this._snapshot(models));
    }
    return this._snapshot(this.cachedModels);
  }

  _snapshot(models) {
    const definition = getRuntimeDefinition(this.runtime);
    const state = this.state;
    const work = this.workStatus;
    const snapshot = {
      revision: this.revision,
      phase: this.stopping ? 'stopping' : this.pendingTransitions > 0 ? 'switching' : work.working ? 'working' : 'ready',
      runtime: this.runtime,
      session: Object.freeze({ id: this.threadId, connected: state.connected }),
      model: this.model,
      resolvedModel: this.backend?.resolvedModel ?? '',
      effort: this.effort,
      capabilities: definition?.capabilities ?? Object.freeze({}),
      queued: state.queued ?? 0,
      work: Object.freeze({
        ...work,
        turnInProgress: this.pendingTransitions > 0 || Boolean(this.backend?.turnInProgress),
      }),
      models: Object.freeze(models.map((entry) => Object.freeze({
        ...entry,
        efforts: Object.freeze([...(entry.efforts ?? [])]),
      }))),
    };
    return Object.freeze(snapshot);
  }

  transition(request) {
    this.pendingTransitions += 1;
    const run = this.transitionBarrier.then(() => this._runTransition(request));
    const settled = run.finally(() => {
      this.pendingTransitions -= 1;
      if (this.pendingTransitions === 0) {
        try { this.onAvailable?.(); } catch (error) {
          this.logger.warn('runtime_available_notification_failed', { error: error.message });
        }
      }
    });
    this.transitionBarrier = settled.catch(() => {});
    return settled;
  }

  async _runTransition(request) {
    if (!request || typeof request !== 'object') throw new TypeError('transition request is required');
    if (this.stopping) throw new Error('agent runtime is stopping');
    if (Number.isSafeInteger(request.expectedRevision) && request.expectedRevision !== this.revision) {
      return {
        ok: false,
        kind: request.kind,
        reason: 'stale-snapshot',
        runtime: this.runtime,
        revision: this.revision,
      };
    }
    if (request.expectedRuntime && request.expectedRuntime !== this.runtime) {
      return {
        ok: false,
        kind: request.kind,
        reason: 'runtime-changed',
        runtime: this.runtime,
        revision: this.revision,
      };
    }
    if (request.kind === 'loop-reset' && (!request.stop || this.loopBreak !== request.stop)) {
      // Another transition already ended that turn (/new, /runtime).
      return { ok: false, kind: request.kind, reason: 'stale-loop-stop', runtime: this.runtime, revision: this.revision };
    }
    this.transitioning = true;
    this.revision += 1;
    try {
      if (request.kind === 'loop-reset') {
        await this._interrupt();
        // A transport with no inputs to report on abandon ends the stop here,
        // still before the new session drains anything.
        if (this.loopBreak === request.stop) this._stopEnded('abandoned', { inboundSeqs: [] });
        if (this.stopping) throw new Error('agent runtime stopped during session transition');
        const threadId = await this._rotateSession({ resume: false, preserveContext: true });
        return { ok: true, kind: request.kind, runtime: this.runtime, threadId };
      }
      if (request.kind === 'new-session') {
        await this._interrupt();
        if (this.stopping) throw new Error('agent runtime stopped during session transition');
        const threadId = await this._rotateSession({ resume: false });
        return { ok: true, kind: request.kind, runtime: this.runtime, threadId };
      }
      if (request.kind === 'select-model') {
        const result = await this._selectModel(request.selector);
        return { ok: true, kind: request.kind, ...result };
      }
      if (request.kind === 'select-runtime') {
        const result = await this._selectRuntime(request.runtime, request.model);
        return { ok: true, kind: request.kind, ...result };
      }
      if (request.kind === 'select-effort') {
        const definition = getRuntimeDefinition(this.runtime);
        const effortControl = definition?.capabilities.effortControl;
        if (effortControl !== 'selectable') {
          return {
            ok: false,
            kind: request.kind,
            reason: effortControl === 'remote-managed'
              ? 'remote-managed'
              : 'unsupported-effort-control',
          };
        }
        const models = await this._listModels();
        if (this.stopping) throw new Error('agent runtime stopped during effort transition');
        if (!supportedModelEfforts(this._snapshot(models)).includes(request.effort)) {
          return { ok: false, kind: request.kind, reason: 'unsupported-effort' };
        }
        const unchanged = this.backend.effort === request.effort;
        // Each backend applies the setting at its next turn boundary. Changing
        // reasoning depth must not interrupt work or retire its native context.
        if (!unchanged) await this._selectEffort(request.effort);
        return { ok: true, kind: request.kind, runtime: this.runtime, effort: request.effort, unchanged };
      }
      throw new TypeError(`unsupported transition: ${request.kind}`);
    } finally {
      this.transitioning = false;
      this.revision += 1;
    }
  }

  // A bare name two catalogue routes both answer to. Deliberately not a
  // model-unavailable error: nothing is wrong with the model, the request is
  // under-specified, and a fallback would pick a route the caller did not name.
  _ambiguousModelError(model, entries) {
    const ids = entries.map((entry) => entry.id);
    const error = new Error(`ambiguous model name ${model}: matches ${ids.join(', ')}; use the full provider/model id`);
    error.code = 'AMBIGUOUS_MODEL';
    error.model = model;
    error.candidates = ids;
    return error;
  }

  // The one signal an operator gets when a configured route could not be served
  // and a different one was committed instead. The startup path has no reply to
  // carry `modelFallback` (the daemon is only starting up), so it is a warn line
  // rather than something a command text explains later.
  _noteModelFallback(runtime, from, to, source) {
    this.logger.warn('runtime_model_fallback', { runtime, from, to, source });
  }

  // A model the live catalog does not list. Structured so callers can tell it
  // from a rejected credential or a gateway outage.
  //
  // The runtime is a parameter because during a switch `this.runtime` is still the
  // PREVIOUS runtime: the failure that ended a startup on dsh was reported as
  // "durable model is unavailable", which named the runtime that just stopped
  // instead of the one that could not start.
  _unknownModelError(model, runtime = this.runtime) {
    const error = new Error(`${runtime} model is unavailable: ${model}`);
    error.code = 'MODEL_UNAVAILABLE';
    error.model = model;
    error.modelError = { kind: MODEL_ERROR_KINDS.UNKNOWN_MODEL, status: 0, code: error.code, retryable: false };
    return error;
  }

  async _selectModel(value) {
    if (this.stopping) throw new Error('agent runtime is stopping');
    const selector = parseRuntimeModelSelector(value);
    const namedRuntime = getRuntimeDefinition(value);
    if ((selector.qualified && selector.runtime !== this.runtime)
      || (namedRuntime && namedRuntime.id !== this.runtime)) {
      throw new Error('Use /runtime to switch runtimes');
    }
    const requested = selector.qualified ? selector.model : selector.raw;
    // The rule of the runtime that will serve the id, not the base rule: `+` is
    // open on DSH because a real dsh route id carries it, and stays closed
    // everywhere else. The id alone is measured, never a `dsh:` qualifier.
    assertModelId(requested, this.runtime);
    const models = await this._listModels();
    if (this.stopping) throw new Error('agent runtime stopped during model transition');
    // Match the user's spelling and its canonical form (a bare name gains the
    // runtime's single catalog route) against the id AND the resolved model of
    // each entry. Mixing the two and taking the first match let an operator
    // route shadow the canonical one whenever the catalogue happened to list it
    // first: the two providers expose the same resolved model name.
    const canonical = canonicalRuntimeModel(this.runtime, requested);
    const spellings = new Set([requested, canonical]);
    const exact = models.filter((entry) => spellings.has(entry.id)
      || (entry.resolvedModel && spellings.has(entry.resolvedModel)));
    if (models.length && exact.length === 0) throw this._unknownModelError(requested);
    // The canonical id is the runtime's own answer for a bare name, so it wins
    // over a same-named model on an operator route; the user's own spelling is
    // next. Anything else that is still ambiguous is refused instead of
    // silently picking whichever the catalogue happened to list first. An empty
    // catalogue established nothing, so the canonical form is all there is.
    let model = exact.find((entry) => entry.id === canonical)?.id
      ?? exact.find((entry) => entry.id === requested)?.id;
    if (model === undefined) {
      // A spelling that names an entry's resolved model is a concrete version
      // (Claude publishes `claude-sonnet-5-5` behind the alias `sonnet`).
      // Applying the alias would follow whatever the runtime later calls current
      // — a different model than the one asked for — so an exact version keeps
      // the spelling it was given. A route-qualified id already names the same
      // model and is not affected.
      const pinned = exact.find((entry) => entry.resolvedModel === requested
        && !entry.id.endsWith(`/${requested}`));
      if (pinned) model = requested;
      else if (exact.length === 1) model = exact[0].id;
      else if (exact.length === 0) model = canonical;
      else throw this._ambiguousModelError(requested, exact);
    }
    const unchanged = this.backend.model === model;
    const previousEffort = this.effort;
    const selectable = getRuntimeDefinition(this.runtime)?.capabilities.effortControl === 'selectable';
    const entry = currentModelEntry({ runtime: this.runtime, model, models });
    const effort = selectable ? compatibleModelEffort(entry, previousEffort) : previousEffort;
    if (effort === undefined) {
      if (unchanged) return { runtimeChanged: false, runtime: this.runtime, model, threadId: this.threadId, unchanged: true };
      return { ok: false, reason: 'model-capabilities-unavailable' };
    }
    const effortChanged = effort !== previousEffort;
    if (!unchanged || effortChanged) {
      // `null` means "no effort override". It is stored as '' -- the value every
      // transport already uses for "no override", and the one a stored record
      // has to carry so a previous level is cleared. Writing the null itself
      // would store the string "null", which no runtime accepts.
      this.store.setRuntimeSettings(this.runtime, {
        model, ...(effort === undefined ? {} : { effort: effort ?? '' }),
      });
      // '' is the transports' own spelling for "no override" on the desired
      // side (and what the store keeps); dsh normalizes it to null itself.
      this.backend.applySettings({ model, effort: effort ?? '' });
    }
    return { runtimeChanged: false, runtime: this.runtime, model, threadId: this.threadId,
      unchanged: unchanged && !effortChanged, effort, previousEffort, effortChanged };
  }

  async _selectRuntime(value, requestedModel, { persistPreference = true } = {}) {
    const definition = getRuntimeDefinition(value);
    if (!definition) throw new Error(`Unknown runtime: ${value}`);
    this._assertConnectorSupport(definition.id);
    if (definition.id === this.runtime) {
      if (persistPreference) await this.persistRuntimePreference?.(definition.id);
      return { runtimeChanged: false, runtime: this.runtime, model: this.model, unchanged: true };
    }
    const initial = resolveRuntimeModelTarget(this.config.initialModel || '');
    const model = canonicalRuntimeModel(definition.id, requestedModel
      ?? this.store.getSetting?.(`${definition.id}_model`)
      ?? (this.config[`${definition.id}Model`] || undefined)
      ?? (initial.runtime === definition.id ? initial.model : definition.defaultModel));
    return this._hardSwitch({ runtime: definition.id, model, canonical: model || definition.canonicalDefault,
      allowModelFallback: requestedModel === undefined, persistPreference });
  }

  async _hardSwitch(target) {
    if (this.stopping) throw new Error('agent runtime is stopping');
    if (this.switching) throw new Error('another runtime switch is already in progress');
    this.switching = true;
    const previous = this.backend;
    const previousRuntime = this.runtime;
    let staged = null;
    let modelFallback = null;
    let effort;
    let previousEffort;
    try {
      // Never wait for a stuck in-flight turn: interrupt/abandon it. Its
      // inbound rows are already in the cognition journal, synced to OV, whose
      // recent history the target receives on its first real turn.
      await previous?.interrupt();
      if (this.stopping) throw new Error('agent runtime stopped during runtime switch');
      const cognition = this.store.getCognitionState();
      const journalThrough = this.store.cognitionJournalHead(cognition.epoch);
      const authorityToken = randomUUID();
      const definition = getRuntimeDefinition(target.runtime);
      const stageOptions = {
        authorityToken,
        cognitionEpoch: cognition.epoch,
        sessionPersistence: false,
        allowResume: false,
        // The journal is not acknowledged until the target's first real turn
        // completes. journalThrough is only the bounded staging snapshot.
        syncedThroughSeq: 0,
        onAvailable: null,
      };
      const listed = (models, id) => Boolean(id)
        && models.some((model) => model.id === id || model.resolvedModel === id);
      const fallback = async (models = []) => {
        if (!target.allowModelFallback || modelFallback || !target.model) return false;
        // Which provider route the requested model was on: the route that just
        // proved it serves nothing. A candidate from ANOTHER route is preferred,
        // because the failing route is the least likely to answer.
        const failingRoute = routeOf(target.model);
        // The runtime's own default, which is now non-empty for dsh (an empty
        // one was what made /runtime dsh unusable); '' keeps meaning "let the
        // adapter pick its default" for runtimes that never catalog one. It is
        // only a candidate when the catalogue did not establish anything, or
        // when it really lists the default -- otherwise it is the very route
        // that just failed.
        const own = canonicalRuntimeModel(definition.id, definition.defaultModel);
        const catalogue = models.filter((model) => typeof model?.id === 'string' && model.id);
        const candidates = [];
        if (own !== target.model && (!own || catalogue.length === 0 || listed(catalogue, own))) {
          candidates.push(own);
        }
        const offRoute = catalogue.filter((model) => routeOf(model.id) !== failingRoute);
        const onFailingRoute = catalogue.filter((model) => routeOf(model.id) === failingRoute);
        for (const entry of [...offRoute, ...onFailingRoute]) {
          if (entry.id !== target.model && !candidates.includes(entry.id)) candidates.push(entry.id);
        }
        const to = candidates.find((candidate) => candidate !== target.model);
        if (to === undefined) return false;
        const from = target.model;
        modelFallback = { from, to };
        // The log is the only signal an operator gets on the startup path.
        this._noteModelFallback(target.runtime, from, to, catalogue.length ? 'catalogue' : 'default');
        // Resolve effort against the chosen route before starting its replacement.
        effort = compatibleModelEffort(models.find((model) => model.id === to || model.isDefault), staged.effort);
        await staged.stop();
        target = { ...target, model: to, canonical: to };
        return true;
      };
      for (;;) {
        if (this.stopping) throw new Error('agent runtime stopped during runtime switch');
        // `null` is "no override"; the transports spell that '' on their own
        // desired side, and dsh normalizes it back to null.
        staged = this._instantiate(target.runtime, { ...stageOptions,
          allowModelFallback: target.allowModelFallback === true,
          model: target.model,
          ...(effort === undefined ? {} : { effort: effort ?? '' }) });
        previousEffort ??= staged.effort;
        this.stagedBackend = staged;
        try {
          await staged.start();
        } catch (error) {
          const classified = classifyModelError(error);
          if (classified.kind) {
            error.modelError = classified;
            this.logger.warn('runtime_model_error', {
              runtime: target.runtime, kind: classified.kind,
              status: classified.status || undefined, code: classified.code || undefined,
            });
          }
          if (!this.stopping && classified.kind === MODEL_ERROR_KINDS.UNKNOWN_MODEL && await fallback()) continue;
          throw error;
        }
        if (this.stopping || this.stagedBackend !== staged) {
          throw new Error('agent runtime stopped during runtime switch');
        }
        if (!staged.state?.connected) throw new Error('target runtime exited before switch commit');
        // The catalogue and the downgrade decision come before any consistency
        // rejection: a runtime that could not serve the requested route may have
        // started on its own default instead (dsh's initialize ladder does), and
        // that is exactly the allowed fallback. Rejecting it first failed the
        // whole switch after the previous turn had already been interrupted.
        let available = [];
        try { available = await staged.listModels(); } catch {
          this.logger.info('runtime_model_list_unavailable', { runtime: target.runtime });
        }
        if (this.stopping) throw new Error('agent runtime stopped during runtime switch');
        if (available.length && target.model && !listed(available, target.model)) {
          // The target may already run a route it adopted from its own catalogue:
          // a handshake that could not start the configured route adopts the
          // first usable one (dsh does, when the coordinator allowed it). That
          // route is the only candidate already proved to start here, so taking
          // it is the fallback -- no second process start, and nothing the live
          // catalogue has not vouched for.
          const applied = canonicalRuntimeModel(target.runtime, staged.model ?? '');
          if (target.allowModelFallback && listed(available, applied)) {
            const from = modelFallback?.from ?? target.model;
            modelFallback = { from, to: applied };
            this._noteModelFallback(target.runtime, from, applied, 'applied');
            target = { ...target, model: applied, canonical: applied };
          } else if (await fallback(available)) {
            continue;
          } else {
            throw this._unknownModelError(target.model, target.runtime);
          }
        }
        // Compare canonical ids: the adapter reports what it applied (dsh
        // prefixes a bare alias with its route), the coordinator holds what was
        // requested. Canonicalizing both keeps a provider-qualified spelling
        // from failing the switch while still rejecting a different model.
        if (canonicalRuntimeModel(target.runtime, staged.model)
          !== canonicalRuntimeModel(target.runtime, target.model)) {
          if (await fallback(available)) continue;
          throw new Error('target runtime did not apply the requested model');
        }
        const entry = currentModelEntry({ runtime: target.runtime, model: target.model,
          resolvedModel: staged.resolvedModel, models: available });
        if (definition.capabilities.effortControl === 'selectable') {
          const compatible = compatibleModelEffort(entry, staged.effort);
          if (compatible !== undefined) {
            effort = compatible;
            staged.applySettings({ effort: compatible ?? '' });
          }
        }
        if (!staged.state?.connected) throw new Error('target runtime exited before switch commit');
        break;
      }
      const catchup = cognitionHistoryPrefix(this.store, cognition.epoch, journalThrough);
      const session = staged.sessionDescriptor;
      if (!session.sessionId) throw new Error('target runtime produced no global session id');
      if (this.stopping || this.stagedBackend !== staged) {
        throw new Error('agent runtime stopped during runtime switch');
      }

      // Config is desired state, SQLite is committed state. Persist the desire
      // only after the target has proved it can start, but before the SQLite
      // authority handoff. A failed config write leaves the old runtime active;
      // a later SQLite failure leaves an explicit desired/actual mismatch that
      // startup can reconcile safely.
      if (target.persistPreference !== false) {
        await this.persistRuntimePreference?.(target.runtime);
      }

      // Commit is the authority hand-off. The target sidecar was unable to send
      // during catch-up; after this transaction the old sidecar is unable to send.
      this.store.commitRuntimeSwitch({
        runtime: target.runtime,
        authorityToken,
        sessionId: session.sessionId,
        policyHash: session.policyHash,
        epoch: cognition.epoch,
        // Catch-up is attached to the first real inbound turn. Running the
        // untrusted journal as a standalone model turn would grant historical
        // prompt injection filesystem/shell authority before a user request.
        syncedThroughSeq: 0,
        sessionReady: session.sessionReady !== false,
        // The runtime's own handle for the session it just started, published
        // in this transaction so the switch cannot leave it in a second store.
        nativeSessionId: session.nativeSessionId ?? null,
        model: target.model,
        // A runtime with no effort override commits '' (the transports' own
        // spelling for it); writing the null would store the string "null".
        effort: effort === undefined ? undefined : (effort ?? ''),
        expectedRuntime: cognition.runtime,
        expectedAuthorityToken: cognition.authorityToken,
      });
      staged.activate();
      this.backend = staged;
      this.runtime = target.runtime;
      this.loopStops = [];
      this.cachedModels = [];
      this._authorityChanged({ runtime: target.runtime, epoch: cognition.epoch, authorityToken });
      this.stagedBackend = null;
      staged = null;
      try { await previous?.stop(); } catch (error) {
        this.logger.warn('retired_runtime_stop_failed', { runtime: previousRuntime, error: error.message });
      }
      try {
        this.store.appendCognitionJournal({
          kind: 'control',
          actor: 'daemon',
          text: `global runtime switched ${previousRuntime} -> ${target.runtime}; target will catch up through journal seq ${catchup.throughSeq} on its first real turn; opaque ${previousRuntime} session retired`,
          dedupeKey: `control:runtime:${randomUUID()}`,
        });
      } catch (error) {
        // The switch is already committed. Never report it as failed and let a
        // caller retry a completed authority hand-off.
        this.logger.warn('runtime_switch_journal_write_failed', { error: error.message });
      }
      return {
        runtimeChanged: true,
        runtime: target.runtime,
        model: target.canonical,
        modelFallback,
        effort: this.effort,
        previousEffort,
        effortChanged: previousEffort !== this.effort,
        syncedThroughSeq: catchup.throughSeq,
        catchupPending: true,
      };
    } catch (error) {
      try { await staged?.stop(); } catch {}
      previous?.cancelSession?.();
      throw error;
    } finally {
      if (this.stagedBackend === staged) this.stagedBackend = null;
      this.switching = false;
    }
  }

  _selectEffort(effort, options) { return this.backend.selectEffort(effort, options); }

  async _rotateSession({ resume = false, preserveContext = false, settings = {}, rollback = {} } = {}) {
    if (resume) {
      const plan = await this.backend.prepareSession({ resume: true });
      return this.backend.commitSession(plan);
    }
    // A fresh native session has no loop stops of its own, whatever id a
    // transport reuses for it.
    this.loopStops = [];
    // Every transport prepares its replacement before the SQLite CAS. Prepare
    // may perform fallible RPC/process work, but cannot persist or drain input;
    // commit is a synchronous activation after the local authority hand-off.
    let plan = null;
    if (preserveContext) await this.store.prepareOVContext?.(this.store.getCognitionState().epoch);
    try {
      plan = await this.backend.prepareSession({ resume: false, rollback });
    } catch (error) {
      await this.backend.cancelSession();
      throw error;
    }
    if (this.stopping) {
      await this.backend.cancelSession();
      throw new Error('agent runtime stopped during session preparation');
    }
    const current = this.store.getCognitionState();
    let cognition;
    const nextAuthorityToken = getRuntimeDefinition(this.runtime)?.capabilities.sessionAuthority === 'rotate'
      ? randomUUID()
      : current.authorityToken;
    try {
      cognition = this.store.commitCognitionSessionRotation({
        runtime: this.runtime,
        authorityToken: current.authorityToken,
        nextAuthorityToken,
        sessionId: plan.prepared.id,
        policyHash: this.backend.policyHash,
        expectedEpoch: current.epoch,
        preserveContext,
        syncedThroughSeq: 0,
        sessionReady: plan.prepared.sessionReady !== false,
        // Prepared, not yet owned: this transaction is the only place a new
        // runtime handle becomes the session's handle.
        nativeSessionId: plan.prepared.nativeSessionId ?? null,
        settings,
      });
    } catch (error) {
      await this.backend.cancelSession();
      throw error;
    }
    this.backend.applyCognition({ ...cognition, syncedThroughSeq: 0 });
    this._authorityChanged(cognition);
    return this.backend.commitSession(plan, { persist: false, ...(preserveContext ? { preserveContext: true } : {}) });
  }

  async stop() {
    this.stopping = true;
    const transitionPending = this.pendingTransitions > 0;
    const stopBackends = async () => {
      const backends = [...new Set([this.backend, this.stagedBackend].filter(Boolean))];
      const results = await Promise.allSettled(backends.map((backend) => backend.stop()));
      for (const result of results) {
        if (result.status === 'rejected') {
          this.logger.warn('agent_runtime_stop_failed', { error: result.reason?.message ?? String(result.reason) });
        }
      }
    };
    await stopBackends();
    if (transitionPending) {
      await this.transitionBarrier;
      // A provider operation already in flight may have installed a replacement
      // child before observing `stopping`; retire that final generation too.
      await stopBackends();
    }
    this.stagedBackend = null;
  }
}
