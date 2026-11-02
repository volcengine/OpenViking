// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

function normalizeState(state) {
  return {
    connected: Boolean(state?.connected),
    activeTurn: Number.isFinite(state?.activeTurn) ? state.activeTurn : 0,
    queued: Number.isFinite(state?.queued) ? state.queued : 0,
  };
}

// Normalizes transport-specific agents behind one adapter Interface. Codex,
// Claude and managed agents keep their protocol state machines; the coordinator
// no longer probes optional lifecycle methods or mutates arbitrary fields.
export class RuntimeAdapter {
  #implementation;

  constructor(runtime, implementation) {
    if (!runtime) throw new TypeError('runtime is required');
    if (!implementation || typeof implementation !== 'object') {
      throw new TypeError('runtime implementation is required');
    }
    this.runtime = runtime;
    this.#implementation = implementation;
  }

  get threadId() { return this.#implementation.threadId ?? null; }
  get effort() { return this.#implementation.effort ?? 'medium'; }
  get model() { return this.#implementation.model ?? ''; }
  get resolvedModel() { return this.#implementation.resolvedModel ?? ''; }
  get state() { return normalizeState(this.#implementation.state); }
  get turnInProgress() { return Boolean(this.#implementation.turnInProgress); }
  get modelWorking() { return Boolean(this.#implementation.modelWorking); }
  get turnStartedAtMs() { return this.#implementation.turnStartedAtMs ?? null; }
  get sessionDescriptor() { return this.#implementation.sessionDescriptor ?? {}; }
  get policyHash() { return this.#implementation.policyHash ?? ''; }

  start() { return this.#implementation.start(); }
  stop() { return this.#implementation.stop(); }
  whenIdle() { return this.#implementation.whenIdle?.() ?? Promise.resolve(); }
  interrupt() { return this.#implementation.interruptTurn?.() ?? Promise.resolve(); }
  // Ends the live turn in place: same process, same native session, and the
  // turn then ends through the transport's own completion path (which drains
  // the queue). False when the transport cannot or there is no live turn.
  cancelTurn() { return Boolean(this.#implementation.cancelTurn?.()); }
  // A daemon line for the next prompt this transport starts; taken once.
  setTurnNotice(text) { this.#implementation.turnNotice = text || null; }
  activate() { return this.#implementation.activateSession?.(); }
  listModels() { return this.#implementation.listModels?.() ?? Promise.resolve([]); }
  selectModel(model, options) { return this.#implementation.setModel(model, options); }
  selectEffort(effort, options) { return this.#implementation.setEffort(effort, options); }

  // Desired settings are plain fields on every transport. Publish the whole
  // validated pair synchronously, after persistence, before another turn drains.
  applySettings({ model = this.model, effort = this.effort }) {
    this.#implementation.model = model;
    this.#implementation.effort = effort;
  }

  async prepareInputContext() {
    const agent = this.#implementation;
    const fresh = agent.needsHistoryPrefix ?? (agent.deliveredThroughSeq === 0);
    if (fresh) await agent.store?.prepareOVContext?.(agent.cognitionEpoch);
  }

  submit(items, { admission = 'enqueue' } = {}) {
    if (!Array.isArray(items) || items.length === 0) {
      return { accepted: false, reason: 'empty-batch' };
    }
    if (!['enqueue', 'claim-if-idle'].includes(admission)) {
      throw new TypeError(`unsupported admission policy: ${admission}`);
    }
    const method = admission === 'claim-if-idle' ? 'tryStartBatch' : 'deliverBatch';
    if (typeof this.#implementation[method] !== 'function') {
      return { accepted: false, reason: 'unsupported-admission' };
    }
    const accepted = this.#implementation[method](items) !== false;
    if (!accepted) {
      return { accepted: false, reason: admission === 'claim-if-idle' ? 'busy' : 'rejected' };
    }
    return {
      accepted: true,
      disposition: admission === 'claim-if-idle' ? 'started' : 'enqueued',
    };
  }

  async prepareSession({ resume = false, rollback = {} } = {}) {
    if (resume) return Object.freeze({ kind: 'native', resume: true, requiresIdle: false });
    if (typeof this.#implementation.prepareSessionRotation !== 'function') {
      throw new Error(`${this.runtime} runtime does not implement prepared session rotation`);
    }
    const prepared = await this.#implementation.prepareSessionRotation({ rollback });
    if (!prepared?.id) throw new Error('runtime prepared an invalid session');
    return Object.freeze({ kind: 'prepared', resume: false, requiresIdle: false, prepared });
  }

  async commitSession(plan, options = {}) {
    if (plan?.kind === 'prepared') {
      return this.#implementation.commitSessionRotation(plan.prepared, options);
    }
    return this.#implementation.rotateSession({ resume: Boolean(plan?.resume) });
  }

  cancelSession() {
    return this.#implementation.cancelSessionRotation?.();
  }

  // The runtimes keep these as plain fields; only this adapter sets them.
  applyCognition({ epoch, authorityToken, syncedThroughSeq = 0 }) {
    this.#implementation.cognitionEpoch = epoch;
    this.#implementation.authorityToken = authorityToken;
    this.#implementation.syncedThroughSeq = syncedThroughSeq;
  }
}

export function adaptRuntime(runtime, implementation) {
  return implementation instanceof RuntimeAdapter
    ? implementation
    : new RuntimeAdapter(runtime, implementation);
}
