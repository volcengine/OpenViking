// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { randomUUID } from 'node:crypto';
import { cognitionHistoryPrefix } from './cognition.mjs';
import { adaptRuntime } from './runtime-adapter.mjs';
import { createRuntimeFactories } from './runtime-factory.mjs';
import {
  DEFAULT_RUNTIME_ID,
  RUNTIME_IDS,
  getRuntimeDefinition,
  parseRuntimeModelSelector,
  resolveRuntimeModelTarget,
  staticModelEntries,
  supportedModelEfforts,
  compatibleModelEffort,
  currentModelEntry,
  isModelUnavailableError,
} from './runtime-catalog.mjs';


export class AgentRuntime {
  constructor(config, store, logger, options = {}) {
    this.config = config;
    this.store = store;
    this.logger = logger;
    this.onTurnFailed = options.onTurnFailed ?? null;
    this.onTurnSucceeded = options.onTurnSucceeded ?? null;
    this.onTurnAbandoned = options.onTurnAbandoned ?? null;
    this.onAvailable = options.onAvailable ?? null;
    this.onAuthorityChanged = options.onAuthorityChanged ?? null;
    this.persistRuntimePreference = options.persistRuntimePreference ?? null;
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
  get effort() { return this.backend?.effort ?? 'medium'; }
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
      onTurnFailed: this.onTurnFailed,
      onTurnSucceeded: this.onTurnSucceeded,
      onTurnAbandoned: this.onTurnAbandoned,
      onAvailable: this.onAvailable,
      authorityToken: cognition.authorityToken,
      cognitionEpoch: cognition.epoch,
      ...overrides,
    };
  }

  _instantiate(runtime, overrides = {}) {
    return adaptRuntime(runtime, this.factories[runtime](this._backendOptions(overrides)));
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
    this.backend = this._instantiate(this.runtime, freshCognition ? { initialModel: freshTarget.model } : {});
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
    return this.backend.submit(items, { admission });
  }

  _interrupt() { return this.backend?.interrupt() ?? Promise.resolve(); }

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
    this.transitioning = true;
    this.revision += 1;
    try {
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

  async _selectModel(value) {
    if (this.stopping) throw new Error('agent runtime is stopping');
    const selector = parseRuntimeModelSelector(value);
    const namedRuntime = getRuntimeDefinition(value);
    if ((selector.qualified && selector.runtime !== this.runtime)
      || (namedRuntime && namedRuntime.id !== this.runtime)) {
      throw new Error('Use /runtime to switch runtimes');
    }
    const model = selector.qualified ? selector.model : selector.raw;
    if (!/^[A-Za-z0-9._:\/\[\]-]{1,256}$/.test(model)) throw new Error('Invalid model name');
    const models = await this._listModels();
    if (this.stopping) throw new Error('agent runtime stopped during model transition');
    if (models.length && !models.some((entry) => entry.id === model || entry.resolvedModel === model)) {
      throw new Error(`${this.runtime} model is unavailable: ${model}`);
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
      this.store.setRuntimeSettings(this.runtime, { model, ...(selectable ? { effort } : {}) });
      this.backend.applySettings({ model, effort });
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
    const model = requestedModel ?? this.store.getSetting?.(`${definition.id}_model`)
      ?? (this.config[`${definition.id}Model`] || undefined)
      ?? (initial.runtime === definition.id ? initial.model : definition.defaultModel);
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
      const fallback = async (models = []) => {
        if (!target.allowModelFallback || modelFallback || !target.model) return false;
        modelFallback = { from: target.model, to: definition.defaultModel };
        // Resolve effort against the default before starting its replacement.
        const entry = models.find((model) => model.isDefault || model.id === definition.defaultModel);
        effort = compatibleModelEffort(entry, staged.effort);
        await staged.stop();
        target = { ...target, model: definition.defaultModel, canonical: definition.canonicalDefault };
        return true;
      };
      for (;;) {
        if (this.stopping) throw new Error('agent runtime stopped during runtime switch');
        staged = this._instantiate(target.runtime, { ...stageOptions, model: target.model,
          ...(effort === undefined ? {} : { effort }) });
        previousEffort ??= staged.effort;
        this.stagedBackend = staged;
        try {
          await staged.start();
        } catch (error) {
          if (!this.stopping && isModelUnavailableError(error) && await fallback()) continue;
          throw error;
        }
        if (this.stopping || this.stagedBackend !== staged) {
          throw new Error('agent runtime stopped during runtime switch');
        }
        if (!staged.state?.connected) throw new Error('target runtime exited before switch commit');
        if (staged.model !== target.model) throw new Error('target runtime did not apply the requested model');
        let available = [];
        try { available = await staged.listModels(); } catch {
          this.logger.info('runtime_model_list_unavailable', { runtime: target.runtime });
        }
        if (this.stopping) throw new Error('agent runtime stopped during runtime switch');
        const entry = currentModelEntry({ runtime: target.runtime, model: target.model,
          resolvedModel: staged.resolvedModel, models: available });
        if (available.length && target.model && !available.some((model) => model.id === target.model || model.resolvedModel === target.model)) {
          if (await fallback(available)) continue;
          throw new Error(`${target.runtime} model is unavailable: ${target.model}`);
        }
        if (definition.capabilities.effortControl === 'selectable') {
          const compatible = compatibleModelEffort(entry, staged.effort);
          if (compatible !== undefined) {
            effort = compatible;
            staged.applySettings({ effort });
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
        effort,
        expectedRuntime: cognition.runtime,
        expectedAuthorityToken: cognition.authorityToken,
      });
      staged.activate();
      this.backend = staged;
      this.runtime = target.runtime;
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
