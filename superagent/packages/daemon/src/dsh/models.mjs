// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Model boundary: which route the agent runs, and what the menu may advertise.
// This module owns DSH's own catalogue (listModels + per-model capability
// metadata), applying a desired model/effort to the live session at an idle turn
// boundary, validating a requested effort, and the content hash of the profile
// patch this process was started from -- the signal a change needs a restart.
// The initialize-time downgrade lives with the handshake that performs it, but
// its error classification (and the route helpers below) are shared from here.
import { safeError as safe } from '../logger.mjs';
import {
  MODEL_ERROR_KINDS, classifyModelError, isModelUnavailableError,
} from '../runtime-catalog.mjs';
import {
  DSH_DEFAULT_MODEL, DSH_EFFORTS, dshProfilePatchHash, parseDshModel, retryDshNotReady,
} from '../dsh-runtime.mjs';
import { dshHostHome } from '../dsh-config.mjs';

// A catalogue query asks every route at once, and each route already carries the
// bridge's own 10s bound: this is the outer bound for the whole answer.
const MODEL_CATALOG_TIMEOUT_MS = 20_000;
// One model's capability metadata is a local lookup; a route that cannot answer
// that quickly must not hold the menu open.
const MODEL_INFO_TIMEOUT_MS = 5_000;

// The wire loses DSH's own LlmError codes (the transport answers -32603 with the
// message only), so these two classes are recognized by the text DSH emits.
const EFFORT_UNSUPPORTED_TEXT = /does not support reasoning effort|unsupported reasoning effort/i;
const UNKNOWN_ROUTE_TEXT = /no adapter registered for provider|unknown provider route/i;
// Our own transport codes, never DSH's: the request may have reached DSH before
// its answer was lost, so the pair was neither confirmed nor refused.
const UNCONFIRMED_SELECT_CODES = Object.freeze(new Set(['DSH_RPC_TIMEOUT', 'DSH_CONNECTION_LOST']));
// The one message a delivery refused for an unverifiable model carries.
const SELECTION_UNVERIFIED = 'dsh model selection could not be verified; input is refused'
  + ' until the runtime confirms its model';

// A model the runtime refuses at initialize time must surface as the one code
// the coordinator falls back on, with enough structure to tell an unknown model
// apart from an auth/route failure. The classification itself stays in
// runtime-catalog so every transport agrees on it; this only reshapes the error.
export function modelUnavailable(error) {
  if (!error || typeof error !== 'object' || !isModelUnavailableError(error)) return error;
  const originalCode = error.code;
  error.code = 'MODEL_UNAVAILABLE';
  if (error.rpcCode === undefined && typeof originalCode === 'number') error.rpcCode = originalCode;
  if (error.errorCode === undefined && error.data && typeof error.data === 'object') {
    error.errorCode = error.data.code ?? error.data.errorCode ?? undefined;
  }
  if (error.status === undefined) {
    const status = error.data?.status ?? error.data?.httpStatus ?? error.data?.statusCode;
    if (status !== undefined) error.status = status;
  }
  return error;
}

// One DSH route. `provider/model` splits at the FIRST slash: route names may not
// contain one, model ids may.
export function modelSelection(model, effort) {
  const { provider, model: id } = parseDshModel(model);
  return { provider, model: id, ...(effort ? { reasoningEffort: effort } : {}) };
}

function splitModelId(id) {
  const slash = String(id ?? '').indexOf('/');
  return slash < 0 ? { provider: '', model: String(id ?? '') } : {
    provider: id.slice(0, slash), model: id.slice(slash + 1),
  };
}

// Which initialize downgrade an error calls for, if any. A bridge frame carries
// DSH's string code in `data.code` and that is authoritative; the stock
// initialize path answers -32603 with the message only (real dsh 0.2.2-rc.1),
// so its two known refusals are recognized by their text as a last resort.
export function initializeFailure(error) {
  const code = String(error?.data?.code ?? '');
  if (code === 'UNSUPPORTED_REASONING_EFFORT') return 'effort';
  if (code === 'NO_ADAPTER' || code === 'UNKNOWN_MODEL' || code === 'MODEL_UNAVAILABLE'
    || code === 'MODEL_NOT_FOUND' || code === 'MODEL_UNKNOWN') return 'model';
  const message = String(error?.message ?? '');
  if (EFFORT_UNSUPPORTED_TEXT.test(message)) return 'effort';
  if (UNKNOWN_ROUTE_TEXT.test(message)) return 'route';
  const classified = classifyModelError(error);
  if (classified.kind === MODEL_ERROR_KINDS.EFFORT) return 'effort';
  if (classified.kind === MODEL_ERROR_KINDS.UNKNOWN_MODEL || isModelUnavailableError(error)) return 'model';
  return null;
}

// What a select reply says the session now runs. The contract records the
// REQUESTED effort only (`null` when none was sent), never an effort DSH
// materialized from a model default: a filled-in default would become the
// user's pinned choice on the next read.
export function appliedSelection(result, attempted) {
  return {
    model: result?.provider && result?.model ? `${result.provider}/${result.model}` : attempted.model,
    effort: typeof result?.reasoningEffort === 'string' && result.reasoningEffort
      ? result.reasoningEffort : (attempted.effort || null),
  };
}

// The OpenViking SuperAgent entry for one DSH model. `isDefault` marks the route this daemon
// starts from when nothing else is persisted.
function modelEntry(provider, model, defaultModel) {
  const resolved = String(model?.id ?? '');
  const id = `${provider}/${resolved}`;
  return {
    id,
    displayName: model?.name || resolved,
    resolvedModel: resolved,
    ...(Array.isArray(model?.inputModalities) ? { input: [...model.inputModalities] } : {}),
    ...(id === defaultModel ? { isDefault: true } : {}),
  };
}

export default {
  _canonicalModel(value) {
    const { provider, model } = parseDshModel(value);
    return `${provider}/${model}`;
  },

  // "No effort override" is `null`, never ''. The old build wrote '' into the
  // store for a model without effort control, and '' is not a DSH level, so the
  // next process refused to construct at all. This is the one normalizer every
  // read and write of the desired effort goes through.
  _normalizedEffort(value) {
    return value === null || value === undefined || value === '' ? null : value;
  },

  // The pair this process should be running. `this.model`/`this.effort` are the
  // only source of truth: the handshake's downgrade adopts the route it really
  // started with by writing them, and it takes care not to overwrite a request
  // made while that handshake was in flight.
  _desiredSelection() {
    return { model: this.model, effort: this._normalizedEffort(this.effort) };
  },

  // The pair this session really runs. The applied record is bound to the
  // session it was confirmed on: a rotated or resumed session carries its own
  // route, so a value recorded on another one is never evidence about this one.
  // `stale` is a different question -- it says the *desired* pair has not been
  // asserted on this session yet, not that the applied one is unknown -- so it
  // is only consulted by `_selectionPending()`.
  // `known: false` means nothing names the route, which is a state input must
  // not run in.
  _appliedSelection() {
    const known = typeof this.appliedModel === 'string'
      && this.appliedSessionId === this.sessionId;
    return known
      ? { known: true, model: this.appliedModel, effort: this._normalizedEffort(this.appliedEffort) }
      : { known: false, model: null, effort: null };
  },

  _recordApplied({ model, effort, sessionId = this.sessionId }) {
    this.appliedModel = model;
    this.appliedEffort = this._normalizedEffort(effort);
    this.appliedSessionId = sessionId;
  },

  _selectionPending() {
    const desired = this._desiredSelection();
    // A pair DSH just refused for this session is never submitted again while
    // the request stays the same: every availability callback asks this
    // question, so an unchanged refused pair would otherwise retry on each of
    // them without bound. The record is dropped by a new
    // setModel/setEffort/applySettings, by a rotation, or by a fresh handshake.
    const rejected = this.rejectedSelection;
    if (rejected && rejected.model === desired.model
      && rejected.effort === desired.effort) return false;
    if (this.selectionStale) return true;
    const applied = this._appliedSelection();
    if (!applied.known) return true;
    // No explicit effort is `null` on both sides: the applied side records the
    // request, never an effort DSH materialized from a model default.
    return applied.model !== desired.model || applied.effort !== desired.effort;
  },

  // Why input may not run right now even though no select is pending. A refused
  // or unconfirmed selection can leave this session's route unknown; refusing
  // delivery and reporting it is the only honest answer.
  _selectionBlocked() {
    if (this.selectionUnresolved === true) return SELECTION_UNVERIFIED;
    if (!this._appliedSelection().known && !this._selectionPending()) return SELECTION_UNVERIFIED;
    return '';
  },

  _failBlockedSelection(items) {
    this._failItems(items, new Error(SELECTION_UNVERIFIED), { endReason: 'selection-unverified' });
    // A refused delivery is also the moment to re-establish the truth: nothing
    // else would, and the state must not stay unknown forever.
    this._reestablishSelection();
  },

  /**
   * Publish a desired model/effort pair. This is the one entry point for an
   * explicit selection: the /model and /effort commands reach it through
   * RuntimeAdapter.applySettings, the local setModel/setEffort wrap it, and all
   * of them must drop a recorded refusal -- otherwise the same pair would never
   * be asked for again. Nothing is persisted here: the store is written by the
   * coordinator (setRuntimeSettings) and by setModel/setEffort's own persist.
   */
  applySettings({ model = this.model, effort = this.effort } = {}) {
    const canonical = this._canonicalModel(model);
    this.model = canonical;
    this.effort = this._normalizedEffort(effort);
    // Every explicit request gets its own version: a refusal may only roll the
    // desire back when no newer intent arrived while it was in flight, so an
    // older failure can never overwrite a later choice.
    this.selectionVersion = (this.selectionVersion ?? 0) + 1;
    // An explicit request is a reason to ask DSH again.
    this.rejectedSelection = null;
  },

  setModel(model, { persist = true } = {}) {
    const canonical = this._canonicalModel(model);
    if (persist) this.store.setSetting?.('dsh_model', canonical);
    this.applySettings({ model: canonical });
  },

  setEffort(effort, { persist = true } = {}) {
    if (!DSH_EFFORTS.includes(effort)) throw new Error(`unsupported dsh effort: ${effort}`);
    if (persist) this.store.setSetting?.('dsh_effort', effort);
    this.applySettings({ effort });
  },

  async listModels() {
    // The catalogue is DSH's own: the same routes and models the next request
    // resolves against, so the menu cannot advertise something the SDK refuses.
    // A route with no models (an account that is not logged in, a route without
    // a credential) is omitted: /model lists what can actually run.
    if (!this.connected) return this.modelCatalog;
    // A document edited outside this process -- `ov-superagent dsh model add` writes it
    // from another one -- is only read at start-up. Refresh the process before
    // answering the menu, so a route added a minute ago is not missing from it.
    // Mid-turn the restart is refused and the menu simply shows what is running.
    if (this._profilePatchChanged()) await this.restartWhenIdle('model-settings-changed');
    let reply;
    try {
      reply = await retryDshNotReady(() => this._request(
        'superagent/llm/models', {}, MODEL_CATALOG_TIMEOUT_MS,
      ));
    } catch (error) {
      this.logger.warn('dsh_model_catalog_failed', { error: safe(error) });
      return this.modelCatalog;
    }
    const entries = [];
    for (const provider of Array.isArray(reply?.providers) ? reply.providers : []) {
      const models = Array.isArray(provider?.models) ? provider.models : [];
      if (!provider?.provider || models.length === 0) continue;
      for (const model of models) {
        if (model?.id) entries.push(modelEntry(provider.provider, model, DSH_DEFAULT_MODEL));
      }
    }
    if (entries.length === 0) {
      // An empty answer that DSH really gave is an answer: every route was
      // removed, or no credential is set any more. Caching it is what keeps a
      // later `!connected` read from advertising models that no longer exist.
      this.modelCatalog = [];
      return [];
    }
    const described = await Promise.all(entries.map((entry) => this._describeModel(entry)));
    // Cache only a real answer: an unreachable catalogue must not erase what the
    // menu last advertised, while an empty-but-answered one must.
    this.modelCatalog = described;
    return described;
  },

  // Efforts and reasoning support per model, from DSH's own metadata. A route
  // that cannot answer keeps the runtime-wide union: DSH re-validates every
  // effort at request time, so the menu can be permissive but never authoritative.
  async _describeModel(entry) {
    const { provider, model } = splitModelId(entry.id);
    try {
      const info = await retryDshNotReady(() => this._request('superagent/llm/model/info', {
        provider, model,
      }, MODEL_INFO_TIMEOUT_MS));
      const efforts = (info?.reasoning?.efforts ?? [])
        .map((effort) => effort?.id)
        .filter((id) => typeof id === 'string' && id);
      const defaultEffort = info?.reasoning?.defaultEffort ?? efforts.at(-1) ?? '';
      return {
        ...entry,
        ...(Array.isArray(info?.inputModalities) ? { input: [...info.inputModalities] } : {}),
        efforts,
        defaultEffort,
        reasoning: Boolean(info?.reasoning),
        effortsKnown: true,
      };
    } catch (error) {
      this.logger.warn('dsh_model_info_failed', { model: entry.id, error: safe(error) });
      return { ...entry, efforts: [...DSH_EFFORTS], defaultEffort: 'high' };
    }
  },

  /**
   * Apply the desired model and effort to the live session. Runs only at an
   * idle turn boundary; a switch never restarts the process and never interrupts
   * a turn. Only the three fields DSH resolved are written back to the applied
   * state — a resolved maxTokens or context has no place in a settings record.
   */
  _applyPendingSelection() {
    if (this.settingsUpdate || this.stopping || this.restarting) return this.settingsUpdate;
    if (!this.connected || !this.sessionId || !this.child) return null;
    if (this.turn || this.inflight.size > 0 || this.sessionRotation) return null;
    if (!this._selectionPending()) return null;
    const attempted = this._desiredSelection();
    // The request's own version: a refusal or a success may only speak for the
    // intent that was current when it was sent.
    const version = this.selectionVersion ?? 0;
    const sessionId = this.sessionId;
    this.settingsUpdate = (async () => {
      try {
        const result = await retryDshNotReady(() => this._request('superagent/session/model/select', {
          sessionId,
          ...modelSelection(attempted.model, attempted.effort),
        }, this.controlTimeoutMs));
        const applied = appliedSelection(result, attempted);
        // This session runs that pair now: record it against the session it was
        // confirmed on, never against a later one.
        this._recordApplied({ ...applied, sessionId });
        this.selectionUnresolved = false;
        // Adopt the applied pair as the desired one so the comparison converges
        // -- unless something newer was asked for while this select was in
        // flight, which stays pending for the next boundary. It also undoes a
        // handshake downgrade that overwrote the fields during the select: the
        // value DSH accepted is the truthful one.
        if ((this.selectionVersion ?? 0) === version) {
          this.model = applied.model;
          this.effort = this._normalizedEffort(applied.effort);
        }
        this.selectionStale = false;
        this.rejectedSelection = null;
        this.logger.info('dsh_model_selected', {
          model: this.appliedModel, effort: this.appliedEffort,
        });
      } catch (error) {
        // DSH's string code (UNSUPPORTED_REASONING_EFFORT, NO_ADAPTER, ...) rides
        // in `data.code`; naming it keeps the failure class routable instead of
        // leaving only prose.
        const code = typeof error?.data?.code === 'string' ? error.data.code : '';
        this.logger.warn('dsh_model_select_failed', {
          model: attempted.model, error: safe(error), ...(code ? { code } : {}),
        });
        // No answer (a timeout, a lost connection) is not DSH's verdict on the
        // pair: it may have been recorded, so this session's route is unknown.
        // Claiming either value and running input on it is the failure mode, so
        // the desire stays, the input is refused, and a fresh handshake
        // re-establishes the truth.
        if (UNCONFIRMED_SELECT_CODES.has(String(error?.code ?? ''))) {
          this.selectionUnresolved = true;
          this.selectionStale = true;
          this.logger.warn('dsh_model_select_unconfirmed', {
            model: attempted.model, error: safe(error),
          });
          this._failItems(this.queue.splice(0).flat(), new Error(SELECTION_UNVERIFIED),
            { endReason: 'selection-unverified' });
          this._reestablishSelection();
          return;
        }
        // Remember which pair was refused, so the same request is not
        // resubmitted on every availability callback. `applySettings` and a
        // rotation drop the record.
        this.rejectedSelection = { model: attempted.model, effort: attempted.effort };
        // An intent recorded after this request was sent is not answered by it:
        // rolling the desire back here would discard the newer choice (P1-4 ②).
        if ((this.selectionVersion ?? 0) !== version) {
          this.logger.info('dsh_model_select_superseded', {
            model: attempted.model, supersededBy: this.model,
          });
          return;
        }
        const applied = this._appliedSelection();
        if (applied.known) {
          // The selection is refused, not deferred: keep running the route this
          // session actually has instead of a pair that would fail every turn.
          // The persisted choice is untouched, so the next start retries it.
          this.model = applied.model;
          this.effort = applied.effort;
          // The applied pair is known again, so nothing is stale: with the
          // refusal recorded above, `_selectionPending()` degrades to a plain
          // comparison instead of retrying this pair forever.
          this.selectionStale = false;
        } else {
          // Nothing names the route this session runs, so no delivery may claim
          // one: refuse the queued input and say why (P1-4 ③).
          this.selectionUnresolved = true;
          this._failItems(this.queue.splice(0).flat(), new Error(SELECTION_UNVERIFIED),
            { endReason: 'selection-unverified' });
          this._reestablishSelection();
          return;
        }
        this._failItems(this.queue.splice(0).flat(),
          new Error(`dsh model selection failed${code ? ` (${code})` : ''}: ${safe(error)}`),
          code ? { errorCode: code } : {});
      } finally {
        this.settingsUpdate = null;
        // The recovery handshake refuses to run while this update is in flight,
        // so it starts once the field is clear.
        if (this.selectionUnresolved === true) this._reestablishSelection();
        this._drain();
        this._notifyAvailable();
      }
    })();
    return this.settingsUpdate;
  },

  // A select has no read-back: the only way to learn what the session really
  // runs after an unconfirmed one is a fresh handshake, which initializes the
  // process with the desired pair and records what it applied. Never leave the
  // daemon running on a guess while it waits for one.
  _reestablishSelection() {
    if (this.stopping) return;
    if (this.connected && !this.turn && this.inflight.size === 0) {
      void this.restartWhenIdle('model-selection-unverified');
    }
  },

  // Watched at every turn boundary: DSH reads the `llm-pi-ai` document in this
  // file at start-up only, while credentials reload on their own. A document
  // that appeared after start-up is as much a change as an edited one, so the
  // comparison covers null (absent) on both sides. The baseline is sampled at
  // the moment a child is spawned, never when its handshake completes: a write
  // that lands during the handshake has not been read by that child and must
  // keep counting as a change. A refused restart never moves the baseline.
  _profilePatchChanged() {
    if (this.settingsUpdate || this.restarting || this.sessionRotation) return false;
    return dshProfilePatchHash({
      dataDir: this.config.dataDir,
      dshHomeMode: this.config.dshHomeMode,
      homeDir: dshHostHome(this._environment),
    }) !== this.profilePatchHash;
  },
};
