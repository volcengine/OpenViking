// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// The daemon's DSH runtime: one persistent SDK stdio process owning one active
// root session. This file keeps what must stay in one place -- the constructor
// that fixes the instance shape, and the read-only views of that state -- while
// the behaviour lives in ./dsh/*, one module per responsibility: transport
// (stdio/JSON-RPC), session (process and session lifetime), turn (accepted input
// and its settlement), events (what DSH reports back) and models (route
// selection). The modules only define functions; installing them here keeps a
// single class with a single public surface.
import { spawn as nodeSpawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { assembleConversationPolicy } from './conversation-policy.mjs';
import { getRuntimeDefinition } from './runtime-catalog.mjs';
import { DSH_DEFAULT_MODEL, DSH_EFFORTS } from './dsh-runtime.mjs';
import { delay, installMembers } from './dsh/shared.mjs';
import events from './dsh/events.mjs';
import models from './dsh/models.mjs';
import session from './dsh/session.mjs';
import turn from './dsh/turn.mjs';
import transport from './dsh/transport.mjs';

const START_TIMEOUT_MS = 30_000;
const CONTROL_TIMEOUT_MS = 15_000;
const VERSION_TIMEOUT_MS = 10_000;
const SHUTDOWN_TIMEOUT_MS = 5_000;
const PROCESS_EXIT_TIMEOUT_MS = 3_000;
const RECONNECT_BASE_MS = 1_000;
const RECONNECT_CAP_MS = 30_000;
// An idle bound for session/wait, not a bound on the turn: a turn may run for
// hours, but a wait that returns nothing for this long is re-issued so a stalled
// SDK becomes observable instead of hanging forever.
const WAIT_IDLE_TIMEOUT_MS = 600_000;
// Accepted-but-undeliverable input is bounded: past these caps a batch is failed
// with a callback instead of growing the queue without limit.
const MAX_QUEUE_BATCHES = 100;
const MAX_QUEUED_ITEMS = 500;
// Consecutive reconnect failures before the loop gives up; 0 means retry forever.
const RECONNECT_MAX_ATTEMPTS = 20;

// The one read point for the effort this process starts with. `''` (and a null
// that reached the field) is the store's spelling for "no effort override": a
// model without reasoning control, or a selection that deliberately left the
// level to DSH. It has to survive as `null` and reach `initialize` as an absent
// `reasoningEffort`; substituting the configured default made real DSH refuse
// the level, drop it again, and inject a "fix your model configuration" notice
// on every restart. Only an absent setting (nothing chosen yet) uses the
// configured default. Any other value must be a level DSH knows.
function storedDshEffort(value, fallback = null) {
  if (value === null || value === undefined) return fallback;
  if (value === '') return null;
  return String(value);
}

// One persistent SDK stdio process owns one active root Session. Every input
// uses session/steer; acknowledgement, not writing stdin, is the acceptance
// boundary used for crash recovery and journal advancement.
export class DshAgent {
  constructor(config, store, logger, options = {}) {
    this.config = config;
    this.store = store;
    this.logger = logger;
    this._spawn = options.spawn ?? nodeSpawn;
    this._versionProbe = options.versionProbe ?? null;
    this._environment = options.environment ?? process.env;
    this._delay = options.delay ?? delay;
    this.onTurnFailed = options.onTurnFailed ?? null;
    this.onTurnSucceeded = options.onTurnSucceeded ?? null;
    this.onTurnAbandoned = options.onTurnAbandoned ?? null;
    this.onAvailable = options.onAvailable ?? null;
    // The config value exists because a start that exceeds this bound fails the
    // daemon, and a source-tree dsh is two `node --import tsx` launches: fast when
    // the machine is idle, seconds when it is not.
    this.startTimeoutMs = options.startTimeoutMs
      ?? (Number.isSafeInteger(config.dshStartTimeoutMs) && config.dshStartTimeoutMs > 0
        ? config.dshStartTimeoutMs : START_TIMEOUT_MS);
    this.controlTimeoutMs = options.controlTimeoutMs ?? CONTROL_TIMEOUT_MS;
    this.versionTimeoutMs = options.versionTimeoutMs ?? VERSION_TIMEOUT_MS;
    this.shutdownTimeoutMs = options.shutdownTimeoutMs ?? SHUTDOWN_TIMEOUT_MS;
    this.processExitTimeoutMs = options.processExitTimeoutMs ?? PROCESS_EXIT_TIMEOUT_MS;
    this.reconnectBaseMs = options.reconnectBaseMs ?? RECONNECT_BASE_MS;
    this.reconnectCapMs = options.reconnectCapMs ?? RECONNECT_CAP_MS;
    this.reconnectMaxAttempts = options.reconnectMaxAttempts ?? RECONNECT_MAX_ATTEMPTS;
    this.waitIdleTimeoutMs = options.waitIdleTimeoutMs ?? WAIT_IDLE_TIMEOUT_MS;
    this.maxQueueBatches = options.maxQueueBatches ?? MAX_QUEUE_BATCHES;
    this.maxQueuedItems = options.maxQueuedItems ?? MAX_QUEUED_ITEMS;

    const definition = getRuntimeDefinition('dsh');
    this.runtime = 'dsh';
    this.capabilities = definition?.capabilities ?? Object.freeze({});
    const cognition = store.getCognitionState?.()
      ?? { runtime: this.runtime, epoch: 1, authorityToken: null };
    this.cognitionEpoch = options.cognitionEpoch ?? cognition.epoch;
    this.authorityToken = options.authorityToken ?? cognition.authorityToken ?? '';
    this.sessionPersistence = options.sessionPersistence !== false;
    this.autoReconnect = this.sessionPersistence;
    this.allowResume = options.allowResume !== false;
    this.syncedThroughSeq = options.syncedThroughSeq ?? 0;
    this.deliveredThroughSeq = this.syncedThroughSeq;

    this.systemPrompt = assembleConversationPolicy(config);
    // There is no environment seed for the model: the persisted setting, the
    // selection this start was asked for, and the built-in default are the whole
    // chain. Routes themselves belong to DSH's settings document.
    this.model = this._canonicalModel(options.model
      ?? store.getSetting?.('dsh_model')
      ?? options.initialModel
      ?? DSH_DEFAULT_MODEL);
    // An explicit request (a runtime switch's staging options) or a stored
    // choice wins, including a stored "no override"; only when neither exists
    // does the configured default apply. The store returns null for "never
    // set", which is the one value that falls through to it.
    const requestedEffort = options.effort ?? store.getSetting?.('dsh_effort');
    this.effort = requestedEffort === null || requestedEffort === undefined
      ? storedDshEffort(config.dshEffort ?? 'high')
      : storedDshEffort(requestedEffort);
    // `null` is a real value: it means the process initializes with no effort
    // override and DSH uses the model's own default (P2-7).
    if (this.effort !== null && !DSH_EFFORTS.includes(this.effort)) {
      throw new Error(`unsupported dsh effort: ${this.effort}`);
    }

    this.child = null;
    this.connected = false;
    this.stopping = false;
    this.startedOk = false;
    this.restarting = false;
    this.reconnecting = false;
    this.backoffMs = this.reconnectBaseMs;
    this.forceFreshSession = false;
    this.sessionId = null;
    this.sessionReady = false;
    this.needsHistoryPrefix = false;
    this.turn = null;
    this.generation = 0;
    this.queue = [];
    this.inflight = new Map();
    this.pendingRpc = new Map();
    this.rpcSeq = 0;
    this.appliedModel = null;
    this.appliedEffort = null;
    // The session the applied pair was confirmed on: a pair recorded for another
    // session is not evidence about this one (P1-4 ①).
    this.appliedSessionId = null;
    // The pair `initialize` applied for this process, which is what a session
    // created later in it inherits (and therefore that session's applied pair).
    this.processSelection = null;
    // Monotonic version of the desired pair: a refused select may roll the
    // desire back only when no newer intent arrived (P1-4 ②).
    this.selectionVersion = 0;
    // True while the session's real route is unknown (a select whose answer was
    // lost): input is refused instead of run on a guess (P1-4 ③).
    this.selectionUnresolved = false;
    // Desired settings are plain fields; this is the in-flight application of
    // them (a model/effort select, or a restart for a changed profile patch).
    this.settingsUpdate = null;
    // Last catalogue the bridge answered, served instead when it cannot answer.
    this.modelCatalog = [];
    // Content hash of the profile patch this process was started from.
    this.profilePatchHash = null;
    // The applied selection belongs to one session: a resumed or freshly rotated
    // session may run another route until it is re-asserted.
    this.selectionStale = false;
    // The model/effort pair DSH last refused, so the same request is not
    // resubmitted on every availability callback; setModel/setEffort and a fresh
    // handshake clear it.
    this.rejectedSelection = null;
    this.modelWorking = false;
    this.toolStarts = new Map();
    this.idleWaiters = [];
    this.sessionRotation = null;
    this.activitySeq = 0;
    // Process-run generation: activity keys must stay distinct when two daemon
    // generations resume the same DSH session, while repeats inside one run are
    // still deduped by the sequence number.
    this.runId = randomUUID();
    // The single source of truth for accepted-but-unsettled input batches.
    this.deliveries = new Map();
    this.deliverySeq = 0;
    // messageIds already observed through user/message; an ACK that arrives
    // afterwards must not leave a phantom pending entry.
    this.observedMessageIds = new Set();
    this.writeBlocked = null;
    this.stdoutBuffer = '';
    this.stdoutDiscarding = false;
    this.stderrBuffer = '';
    this.dshVersion = '';
    this._exitedChildren = new WeakSet();
  }

  get threadId() { return this.sessionId; }
  get turnStartedAtMs() { return this.turn?.startedAtMs ?? null; }
  get resolvedModel() { return this.appliedModel ?? ''; }

  get policyHash() {
    return createHash('sha256')
      .update(this.systemPrompt).update('\0')
      .update(this.config.workspace).update('\0')
      .update('dsh-sdk-v1').digest('hex');
  }

  get state() {
    return {
      connected: this.connected,
      activeTurn: this.turn ? 1 : 0,
      queued: this.queue.reduce((count, batch) => count + batch.length, 0),
    };
  }

  get turnInProgress() {
    return Boolean(this.turn) || this.restarting || this.inflight.size > 0;
  }

  get sessionDescriptor() {
    return {
      runtime: this.runtime,
      sessionId: this.sessionId,
      policyHash: this.policyHash,
      epoch: this.cognitionEpoch,
      syncedThroughSeq: this.syncedThroughSeq,
      sessionReady: this.sessionReady,
    };
  }
}

// The agent's behaviour is assembled from the responsibility modules, which only
// define functions. Installing them here keeps one class and one public surface:
// method names, signatures, `this` binding and property attributes stay what the
// single-file class produced.
installMembers(DshAgent.prototype, transport, events, turn, models, session);
