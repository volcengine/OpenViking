// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { spawn as nodeSpawn, spawnDetached } from './platform/index.mjs';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { cognitionHistoryPrefix, CognitionLimitError } from './cognition.mjs';
import { safeError as safe } from './logger.mjs';
import { sanitizeAgentChildEnv, terminateProcessTree } from './process-utils.mjs';
import { agentCliCommands } from './agent-clis.mjs';
import { assembleConversationPolicy, policyHashOf } from './conversation-policy.mjs';
import { deliveryItem, receiptIds, withReceiptIds, withTurnNotice } from './runtime-input.mjs';
import { currentModelEntry, getRuntimeDefinition } from './runtime-catalog.mjs';

const DEFAULT_MAX_LINE_BYTES = 5_000_000;
const START_TIMEOUT_MS = 30_000;
const START_STABILITY_MS = 250;
const CONTROL_TIMEOUT_MS = 15_000;
const RECONNECT_BASE_MS = 1_000;
const RECONNECT_CAP_MS = 30_000;

export function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function appendBounded(current, chunk, label, maxBytes) {
  const next = current + chunk;
  if (Buffer.byteLength(next, 'utf8') > maxBytes) {
    throw new Error(`${label} exceeded local limit`);
  }
  return next;
}

/**
 * Strip daemon-sensitive credentials and inject lark profile / action-service
 * environment variables for child runtime processes.
 */
export function sanitizeRuntimeEnv(environment, profile = '', actions = null, config = {}) {
  return sanitizeAgentChildEnv(environment, {
    profile,
    actions,
    cliBins: agentCliCommands(config).map(({ bin }) => bin),
  });
}

/**
 * Shared nd-JSON over stdio RPC transport with bounded stdout/stderr buffering,
 * per-child pending request tracking, timeouts, and envelope customization.
 */
export class StdioJsonRpcTransport {
  constructor({
    runtimeLabel = 'runtime',
    logPrefix = 'runtime',
    logger,
    pendingRpc = new Map(),
    rpcPrefix = 'rpc',
    maxLineBytes = DEFAULT_MAX_LINE_BYTES,
    defaultTimeoutMs = CONTROL_TIMEOUT_MS,
    buildRequestFrame = null,
    buildMessageFrame = null,
    onEnvelope = null,
    onLine = null,
    onMessage = null,
    onStdoutLimit = null,
  } = {}) {
    this.runtimeLabel = runtimeLabel;
    this.logPrefix = logPrefix;
    this.logger = logger;
    this.pendingRpc = pendingRpc;
    this.rpcPrefix = rpcPrefix;
    this.maxLineBytes = maxLineBytes;
    this.defaultTimeoutMs = defaultTimeoutMs;
    this.buildRequestFrame = buildRequestFrame ?? (({ id, method, params }) => ({
      jsonrpc: '2.0',
      id,
      method,
      ...(params !== undefined ? { params } : {}),
    }));
    this.buildMessageFrame = buildMessageFrame ?? ((frame) => ({ jsonrpc: '2.0', ...frame }));
    this.onEnvelope = onEnvelope;
    this.onLine = onLine;
    this.onMessage = onMessage;
    this.onStdoutLimit = onStdoutLimit;
    this.rpcSeq = 0;
    this.stdoutBuffer = '';
    this.stderrBuffer = '';
  }

  resetBuffers() {
    this.stdoutBuffer = '';
    this.stderrBuffer = '';
  }

  nextRpcId() {
    this.rpcSeq += 1;
    return `${this.rpcPrefix}-${this.rpcSeq}`;
  }

  requestOn(child, method, params, timeoutMs = this.defaultTimeoutMs) {
    const id = this.nextRpcId();
    return new Promise((resolve, reject) => {
      const timer = (Number.isFinite(timeoutMs) && timeoutMs > 0)
        ? setTimeout(() => {
          this.pendingRpc.delete(id);
          reject(new Error(`${this.runtimeLabel} rpc timed out: ${method}`));
        }, timeoutMs)
        : null;
      timer?.unref?.();
      this.pendingRpc.set(id, { resolve, reject, timer, child });
      try {
        const frame = this.buildRequestFrame({ id, method, params });
        child.stdin.write(`${JSON.stringify(frame)}\n`);
      } catch (error) {
        if (timer) clearTimeout(timer);
        this.pendingRpc.delete(id);
        reject(error);
      }
    });
  }

  request(child, method, params, timeoutMs = this.defaultTimeoutMs) {
    if (!child?.stdin?.writable) {
      return Promise.reject(new Error(`${this.runtimeLabel} is disconnected`));
    }
    return this.requestOn(child, method, params, timeoutMs);
  }

  send(child, frame) {
    try {
      const encoded = this.buildMessageFrame(frame);
      child?.stdin.write(`${JSON.stringify(encoded)}\n`);
    } catch {}
  }

  rejectFor(child, error) {
    for (const [id, pending] of this.pendingRpc) {
      if (pending.child !== child) continue;
      this.pendingRpc.delete(id);
      if (pending.timer) clearTimeout(pending.timer);
      try { pending.reject(error); } catch {}
    }
  }

  rejectAll(error) {
    for (const pending of this.pendingRpc.values()) {
      if (pending.timer) clearTimeout(pending.timer);
      try { pending.reject(error); } catch {}
    }
    this.pendingRpc.clear();
  }

  onStdout(chunk, child) {
    try {
      this.stdoutBuffer = appendBounded(
        this.stdoutBuffer,
        chunk,
        `${this.runtimeLabel} stdout line`,
        this.maxLineBytes,
      );
    } catch (error) {
      this.onStdoutLimit?.(error, child);
      return;
    }
    let index;
    while ((index = this.stdoutBuffer.indexOf('\n')) >= 0) {
      const line = this.stdoutBuffer.slice(0, index);
      this.stdoutBuffer = this.stdoutBuffer.slice(index + 1);
      if (!line.trim()) continue;
      try {
        if (this.onLine) this.onLine(line);
        else this.handleLine(line);
      } catch (error) {
        this.logger?.warn?.(`${this.logPrefix}_line_error`, { error: safe(error) });
      }
    }
  }

  onStderr(chunk) {
    try {
      this.stderrBuffer = appendBounded(
        this.stderrBuffer,
        chunk,
        `${this.runtimeLabel} stderr line`,
        this.maxLineBytes,
      );
    } catch {
      this.stderrBuffer = this.stderrBuffer.slice(-4_000);
    }
    let index;
    while ((index = this.stderrBuffer.indexOf('\n')) >= 0) {
      const line = this.stderrBuffer.slice(0, index);
      this.stderrBuffer = this.stderrBuffer.slice(index + 1);
      if (line.trim()) this.logger?.debug?.(`${this.logPrefix}_stderr`, { detail: safe(line.slice(0, 500)) });
    }
  }

  handleLine(line) {
    let message;
    try { message = JSON.parse(line); } catch { return; }
    if (!message || typeof message !== 'object') return;
    this.onEnvelope?.(message);

    if (message.id !== undefined && message.method === undefined) {
      const pending = this.pendingRpc.get(message.id);
      if (!pending) return;
      this.pendingRpc.delete(message.id);
      if (pending.timer) clearTimeout(pending.timer);
      if (message.error) {
        const error = new Error(message.error.message || `${this.runtimeLabel} rpc error`);
        error.code = message.error.code;
        error.data = message.error.data;
        pending.reject(error);
      } else {
        pending.resolve(message.result);
      }
      return;
    }

    if (typeof message.method === 'string') {
      this.onMessage?.(message);
    }
  }
}

/**
 * Shared persistent child-process runtime base class: owns cognition/journal
 * tracking, OV context catch-up, turn queue/batching, session rotation,
 * reconnect backoff, activity telemetry, and clean adapter hooks.
 */
export class RuntimeTurnBase {
  constructor(config, store, logger, options = {}) {
    const runtime = getRuntimeDefinition(options.runtimeId);
    if (!runtime) throw new Error(`Unknown runtime id: ${options.runtimeId}`);

    this.runtime = runtime.id;
    this.runtimeLabel = options.runtimeLabel ?? runtime.id;
    this.logPrefix = options.logPrefix ?? runtime.id;
    this.binConfigKey = options.binConfigKey ?? `${runtime.id}Bin`;
    this.modelConfigKey = options.modelConfigKey ?? `${runtime.id}Model`;
    this.modelSettingKey = options.modelSettingKey ?? `${runtime.id}_model`;
    this.effortConfigKey = options.effortConfigKey ?? `${runtime.id}Effort`;
    this.effortSettingKey = options.effortSettingKey ?? `${runtime.id}_effort`;
    this.policyVersion = options.policyVersion ?? `${runtime.id}-v1`;
    this.rpcPrefix = options.rpcPrefix ?? runtime.id;
    this.maxLineBytes = options.maxLineBytes ?? DEFAULT_MAX_LINE_BYTES;

    this.instanceId = randomUUID();
    this.config = config;
    this.store = store;
    this.logger = logger;
    this._spawn = options.spawn ?? nodeSpawn;
    this._terminateProcessTree = options.terminateProcessTree ?? terminateProcessTree;
    this.onTurnFailed = options.onTurnFailed ?? null;
    this.onTurnSucceeded = options.onTurnSucceeded ?? null;
    this.onTurnAbandoned = options.onTurnAbandoned ?? null;
    this.onAvailable = options.onAvailable ?? null;
    this.startTimeoutMs = options.startTimeoutMs ?? START_TIMEOUT_MS;
    this.startStabilityMs = options.startStabilityMs ?? START_STABILITY_MS;
    this.controlTimeoutMs = options.controlTimeoutMs ?? CONTROL_TIMEOUT_MS;
    this.reconnectBaseMs = options.reconnectBaseMs ?? RECONNECT_BASE_MS;
    this.reconnectCapMs = options.reconnectCapMs ?? RECONNECT_CAP_MS;

    this.capabilities = runtime.capabilities;
    const cognition = store.getCognitionState?.()
      ?? { runtime: this.runtime, epoch: 1, authorityToken: null };
    this.cognitionEpoch = options.cognitionEpoch ?? cognition.epoch;
    this.authorityToken = options.authorityToken ?? cognition.authorityToken ?? '';
    this.sessionPersistence = options.sessionPersistence !== false;
    this.autoReconnect = this.sessionPersistence;
    this.allowResume = options.allowResume !== false;
    this.syncedThroughSeq = options.syncedThroughSeq ?? 0;
    this.deliveredThroughSeq = this.syncedThroughSeq;

    this.systemPrompt = assembleConversationPolicy(config, { logger });
    this.model = options.model
      ?? store.getSetting?.(this.modelSettingKey)
      ?? options.initialModel
      ?? config[this.modelConfigKey]
      ?? '';
    this.effort = options.effort
      ?? (this.effortSettingKey ? store.getSetting?.(this.effortSettingKey) : undefined)
      ?? config[this.effortConfigKey]
      ?? '';

    this.sessionId = null;
    this.sessionInitialized = false;
    this.resumeRequested = false;
    this.needsHistoryPrefix = false;
    this.child = null;
    this.connected = false;
    this.turnActive = false;
    this.turnStartedAtMs = null;
    this.modelWorking = false;
    this.modelStartedAtMs = null;
    this.turnError = null;
    this.turnAcknowledged = false;
    this.cancelledOwner = null;
    this.activeItems = [];
    this.activeOwner = null;
    this.queue = [];
    this.stdoutBuffer = '';
    this.stderrBuffer = '';
    this.stopping = false;
    this.startedOk = false;
    this.reconnecting = false;
    this.backoffMs = this.reconnectBaseMs;
    this.activitySeq = 0;
    this.idleWaiters = [];
    this.toolStarts = new Map();
    this.noteText = '';
    this.pendingRpc = new Map();
    this.rpcSeq = 0;
    this.runtimeDir = null;
    this.promptPath = null;
    this.configPath = null;
    this.sessionRotation = null;
    this.appliedModel = null;
    this.appliedEffort = null;
    this.sessionModel = '';
    this.sessionEffort = '';
    this.settingsUpdate = null;
    this.modelCatalog = [];
    this.turnNotice = null;
    this.allowModelFallback = false;
    this.transport = null;
  }

  get threadId() { return this.sessionId; }

  get resolvedModel() { return this.model || this.sessionModel || ''; }

  get policyHash() {
    return policyHashOf(this.systemPrompt, this.config.workspace, this.policyVersion);
  }

  get state() {
    return {
      connected: this.connected,
      activeTurn: this.turnActive ? 1 : 0,
      queued: this.queue.reduce((count, batch) => count + batch.length, 0),
    };
  }

  get turnInProgress() { return this.turnActive || Boolean(this.settingsUpdate); }

  get sessionDescriptor() {
    return {
      runtime: this.runtime,
      sessionId: this.sessionId,
      policyHash: this.policyHash,
      epoch: this.cognitionEpoch,
      syncedThroughSeq: this.syncedThroughSeq,
      sessionReady: this.sessionInitialized,
    };
  }

  // ---- RuntimeAdapter boundary methods ---------------------------------------

  activateSession() {
    this.sessionPersistence = true;
    this.autoReconnect = true;
  }

  setTurnNotice(text) {
    this.turnNotice = text || null;
  }

  setAllowModelFallback(allow) {
    this.allowModelFallback = allow === true;
  }

  takeQueued() {
    return this.queue.splice(0).flat();
  }

  applySettings({ model = this.model, effort = this.effort } = {}) {
    this.model = model;
    this.effort = effort;
  }

  async prepareInputContext() {
    const fresh = this.needsHistoryPrefix ?? (this.deliveredThroughSeq === 0);
    if (fresh) await this.store?.prepareOVContext?.(this.cognitionEpoch);
  }

  applyCognition({ epoch, authorityToken, syncedThroughSeq = 0 }) {
    this.cognitionEpoch = epoch;
    this.authorityToken = authorityToken;
    this.syncedThroughSeq = syncedThroughSeq;
  }

  whenIdle() {
    if (!this.turnInProgress && this.queue.length === 0) return Promise.resolve();
    return new Promise((resolve) => this.idleWaiters.push(resolve));
  }

  // ---- JSON-RPC transport facade (also safe on Object.create test fixtures) --

  _transportOptions() {
    return {};
  }

  _transport() {
    if (!this.transport) {
      if (!(this.pendingRpc instanceof Map)) this.pendingRpc = new Map();
      this.transport = new StdioJsonRpcTransport({
        runtimeLabel: this.runtimeLabel ?? this.runtime ?? 'runtime',
        logPrefix: this.logPrefix ?? this.runtime ?? 'runtime',
        logger: this.logger,
        pendingRpc: this.pendingRpc,
        rpcPrefix: this.rpcPrefix ?? this.runtime ?? 'rpc',
        maxLineBytes: this.maxLineBytes ?? DEFAULT_MAX_LINE_BYTES,
        defaultTimeoutMs: this.controlTimeoutMs ?? CONTROL_TIMEOUT_MS,
        onMessage: (message) => this._handleAgentMessage(message),
        onLine: (line) => this._handleLine(line),
        onStdoutLimit: (error, child) => {
          this.turnError = error;
          this._terminateProcessTree(child, 'SIGKILL');
        },
        ...this._transportOptions(),
      });
    }
    return this.transport;
  }

  _nextRpcId() {
    return this._transport().nextRpcId();
  }

  _requestOn(child, method, params, timeoutMs = this.controlTimeoutMs) {
    return this._transport().requestOn(child, method, params, timeoutMs);
  }

  _rejectRpcsOf(child, error) {
    this._transport().rejectFor(child, error);
  }

  _send(frame) {
    this._transport().send(this.child, frame);
  }

  _request(method, params, timeoutMs) {
    if (!this.child?.stdin?.writable) {
      return Promise.reject(new Error(`${this.runtimeLabel} is disconnected`));
    }
    return this._requestOn(this.child, method, params, timeoutMs);
  }

  _onStdout(chunk, child) {
    const transport = this._transport();
    transport.onStdout(chunk, child);
    this.stdoutBuffer = transport.stdoutBuffer;
  }

  _onStderr(chunk) {
    const transport = this._transport();
    transport.onStderr(chunk);
    this.stderrBuffer = transport.stderrBuffer;
  }

  _handleLine(line) {
    this._transport().handleLine(line);
  }

  _handleAgentMessage() {
    // Subclasses implement protocol-specific incoming request & notification handling.
  }

  // ---- lifecycle -------------------------------------------------------------

  async start() {
    this.stopping = false;
    const global = this.store.getGlobalSession?.() ?? {};
    const resumable = this.allowResume
      && global.sessionId
      && global.sessionReady !== false
      && global.policyHash === this.policyHash
      && global.runtime === this.runtime
      && global.epoch === this.cognitionEpoch;
    this.sessionId = resumable ? global.sessionId : null;
    this.sessionInitialized = Boolean(resumable);
    this.resumeRequested = Boolean(resumable);
    this.syncedThroughSeq = resumable ? global.syncedThroughSeq ?? 0 : this.syncedThroughSeq;
    this.deliveredThroughSeq = this.syncedThroughSeq;
    this.needsHistoryPrefix = !resumable;

    await this._spawnPersistent();
    this.startedOk = true;
    this.backoffMs = this.reconnectBaseMs;
    this._persistSession();
    this._drain();
    this._notifyAvailable();
  }

  _handshakeTimeoutMessage() {
    return `${this.runtimeLabel} start timed out`;
  }

  async _spawnPersistent() {
    if (this.needsHistoryPrefix) await this.store.prepareOVContext?.(this.cognitionEpoch);
    const model = this.model;
    const configPath = this._writeRuntimeFiles();
    return new Promise((resolve, reject) => {
      const args = this._spawnArgs(configPath);
      const env = this._spawnEnv(configPath);
      let child;
      try {
        child = this._spawn(this.config[this.binConfigKey], args, {
          cwd: this.config.workspace,
          env,
          shell: false,
          detached: spawnDetached,
          stdio: ['pipe', 'pipe', 'pipe'],
        });
      } catch (error) {
        this._cleanupRuntimeFiles();
        reject(error);
        return;
      }

      this.child = child;
      this.stdoutBuffer = '';
      this.stderrBuffer = '';
      this._transport().resetBuffers();
      let ready = false;
      let settled = false;
      let stabilityTimer = null;
      let startupError = '';
      const failStart = (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (stabilityTimer) clearTimeout(stabilityTimer);
        if (this.child === child) {
          this.child = null;
          this.connected = false;
        }
        this._rejectRpcsOf(child, new Error(`${this.runtimeLabel} connection lost`));
        this._terminateProcessTree(child, 'SIGKILL');
        this._cleanupRuntimeFiles();
        reject(error);
      };
      const timer = setTimeout(() => failStart(new Error(this._handshakeTimeoutMessage())), this.startTimeoutMs);
      timer.unref?.();

      child.stdin?.on('error', (error) => {
        if (this.child === child) this.logger.warn(`${this.logPrefix}_stdin_error`, { error: safe(error) });
      });
      child.stdout?.setEncoding('utf8');
      child.stderr?.setEncoding('utf8');
      child.stdout?.on('data', (chunk) => {
        if (this.child === child) this._onStdout(chunk, child);
      });
      child.stderr?.on('data', (chunk) => {
        if (!ready) startupError = (startupError + chunk).slice(-4_000);
        if (this.child === child) this._onStderr(chunk);
      });
      child.once('error', (error) => {
        if (!ready) failStart(error);
        else this._handleExit(child, null, null, error);
      });
      child.once('exit', (code, signal) => {
        if (!ready) {
          const error = new Error(`${this.runtimeLabel} exited during startup (code ${code}, signal ${signal})`);
          if (startupError) error.message += `; stderr: ${startupError.slice(-3000)}`;
          failStart(error);
          return;
        }
        this._handleExit(child, code, signal);
      });

      this._onChildConnected(child, {
        model,
        effort: this.effort,
        configPath,
        isSettled: () => settled || this.child !== child,
      }).then(() => new Promise((innerResolve) => {
        // A short no-input window lets a bad config/model fail the start before
        // the authority handoff instead of killing the first real turn.
        stabilityTimer = setTimeout(() => {
          if (settled || child.exitCode !== null) return;
          ready = true;
          settled = true;
          clearTimeout(timer);
          this.connected = true;
          innerResolve();
        }, this.startStabilityMs);
      })).then(() => resolve()).catch(failStart);
    });
  }

  _createRuntimeDir() {
    this._cleanupRuntimeFiles();
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), `superagent-${this.runtime}-`));
    try { fs.chmodSync(directory, 0o700); } catch {}
    this.runtimeDir = directory;
    return directory;
  }

  _writeRuntimeFiles() {
    return null;
  }

  _cleanupRuntimeFiles() {
    const directory = this.runtimeDir;
    this.runtimeDir = null;
    this.promptPath = null;
    this.configPath = null;
    if (!directory) return;
    try { fs.rmSync(directory, { recursive: true, force: true }); } catch {}
  }

  _larkProfile() {
    return this.config.connectors?.includes('lark') ? this.config.profile : '';
  }

  _spawnArgs() {
    return [];
  }

  _spawnEnv() {
    return sanitizeRuntimeEnv(process.env, this._larkProfile(), this._agentToolEnv(), this.config);
  }

  _agentToolEnv() {
    return null;
  }

  async _onChildConnected() {
    throw new Error(`${this.runtimeLabel} runtime must implement _onChildConnected`);
  }

  async _loadNeedsHistory() {
    if (this.replayedChunks !== 0) return this.needsHistoryPrefix;
    await this.store.prepareOVContext?.(this.cognitionEpoch);
    return true;
  }

  // ---- inbound state machine -------------------------------------------------

  deliver(text, seq = null, receiptId = null) {
    return this.deliverBatch([deliveryItem({ text, seq, receiptId })]);
  }

  _promptBusy() {
    return this.turnActive;
  }

  deliverBatch(items) {
    if (this.stopping || !Array.isArray(items) || items.length === 0) return false;
    const batch = items.map(deliveryItem);
    if (this.settingsUpdate || !this.connected || !this.child?.stdin?.writable || this._promptBusy()) {
      this.queue.push(batch);
      return true;
    }
    this._writeBatch(batch);
    return true;
  }

  tryStartBatch(items) {
    if (this.stopping || this.settingsUpdate || !this.connected || this.turnInProgress || this._promptBusy()
      || this.queue.length > 0 || !Array.isArray(items) || items.length === 0) return false;
    this._writeBatch(items.map(deliveryItem));
    return true;
  }

  _bodyFor(items) {
    const seqs = items.map((item) => item.seq).filter((seq) => seq != null);
    const beforeSeq = seqs.length > 0 ? Math.min(...seqs) : null;
    const throughSeq = this.store.cognitionJournalBeforeInbound?.(
      beforeSeq,
      this.cognitionEpoch,
    ) ?? 0;
    let body = withTurnNotice(this, items.map((item) => item.text).join('\n'));
    let prefixThroughSeq = this.deliveredThroughSeq;
    if (this.needsHistoryPrefix) {
      const catchup = cognitionHistoryPrefix(
        this.store,
        this.cognitionEpoch,
        throughSeq,
        0,
      );
      prefixThroughSeq = catchup.throughSeq;
      if (catchup.text) body = `${catchup.text}\n${body}`;
    }
    let deliveredThroughSeq = prefixThroughSeq;
    for (const seq of seqs) {
      deliveredThroughSeq = Math.max(
        deliveredThroughSeq,
        this.store.cognitionJournalSeqForInbound?.(seq, this.cognitionEpoch) ?? 0,
      );
    }
    return { body, seqs, deliveredThroughSeq };
  }

  _settingsPending() {
    return Boolean(this.model) && this.appliedModel !== this.model;
  }

  _writeBatch(items) {
    if (this.settingsUpdate || (!this._promptBusy() && this._settingsPending())) {
      this.queue.push(items);
      this._applySettings();
      return;
    }
    if (this._promptBusy()) {
      this.queue.push(items);
      return;
    }
    let prepared;
    try {
      prepared = this._bodyFor(items);
    } catch (error) {
      this._failItems(items, error);
      return;
    }
    const owner = Object.freeze({
      runtime: this.runtime,
      authorityToken: this.authorityToken,
      sessionId: this.sessionId,
      epoch: this.cognitionEpoch,
    });
    this.activeOwner = owner;
    this.activeItems.push(...items);
    this.turnActive = true;
    this.turnStartedAtMs = Date.now();
    this.modelWorking = true;
    this.modelStartedAtMs = this.turnStartedAtMs;
    this.turnError = null;
    this.turnAcknowledged = false;
    const hadPrefix = this.needsHistoryPrefix;
    this.needsHistoryPrefix = false;
    this.deliveredThroughSeq = Math.max(this.deliveredThroughSeq, prepared.deliveredThroughSeq);
    const input = items.map((item) => item.text).join('\n');
    this._recordActivity('agent.turn_started', {
      inboundSeq: prepared.seqs.at(-1) ?? null,
      metadata: {
        runtime: this.runtime,
        model: this.resolvedModel || undefined,
        input,
        historyChars: Math.max(0, prepared.body.length - input.length),
        inboundSeqs: prepared.seqs,
      },
    });
    this._submitTurn(prepared, owner, { hadPrefix });
  }

  _submitTurn() {
    throw new Error(`${this.runtimeLabel} runtime must implement _submitTurn`);
  }

  _restoreUnacceptedCatchup(hadPrefix) {
    if (hadPrefix) this.needsHistoryPrefix = true;
    this.deliveredThroughSeq = this.syncedThroughSeq;
  }

  async _applySettingsOn() {
    // Subclasses override when model/effort can be mutated live.
  }

  _applySettings() {
    if (this.settingsUpdate) return;
    const model = this.model;
    const effort = this.effort;
    this.settingsUpdate = (async () => {
      try {
        if (this._settingsPending() && this.connected && this.sessionId) {
          await this._applySettingsOn(this.child, { model, effort });
          this.appliedModel = model;
          this.appliedEffort = effort;
        }
      } catch (error) {
        this.logger.warn(`${this.logPrefix}_settings_update_failed`, { error: safe(error) });
        this._failItems(this.queue.splice(0).flat(), error);
      } finally {
        this.settingsUpdate = null;
        this._drain();
        this._notifyAvailable();
      }
    })();
  }

  _activeSeqs() {
    return [...new Set(
      this.activeItems.map((item) => item.seq).filter((seq) => seq != null),
    )];
  }

  // Telemetry only: runtimes must use a core delivery tool to reach a person.
  _flushNote() {
    const text = this.noteText;
    this.noteText = '';
    if (!text.trim()) return;
    this._recordActivity('agent.note', {
      inboundSeq: this._activeSeqs().at(-1) ?? null,
      summary: text,
      metadata: { runtime: this.runtime, delivery: 'telemetry_only' },
    });
  }

  _completeTurn(error = null) {
    if (!this.turnActive) return;
    this._flushNote();
    const turnStartedAtMs = this.turnStartedAtMs;
    const items = this.activeItems.splice(0);
    const seqs = [...new Set(items.map((item) => item.seq).filter((seq) => seq != null))];
    const receipts = receiptIds(items);
    const owner = this.activeOwner;
    this._acceptJournal(owner, this.deliveredThroughSeq);
    this.activeOwner = null;
    this.turnActive = false;
    this.turnStartedAtMs = null;
    this.modelWorking = false;
    this.modelStartedAtMs = null;
    this.deliveredThroughSeq = this.syncedThroughSeq;
    this.toolStarts.clear();
    this._recordActivity('agent.turn_completed', {
      inboundSeq: seqs.at(-1) ?? null,
      status: error ? 'failed' : 'completed',
      summary: error ? safe(error) : undefined,
      metadata: {
        runtime: this.runtime,
        inboundSeqs: seqs,
        ...(turnStartedAtMs ? { durationMs: Date.now() - turnStartedAtMs } : {}),
      },
    });
    if (error && this.onTurnFailed) {
      try {
        this.onTurnFailed(withReceiptIds({ inboundSeqs: seqs, error: safe(error) }, receipts));
      } catch {}
    }
    if (!error && this.onTurnSucceeded) {
      try {
        this.onTurnSucceeded(withReceiptIds({ inboundSeqs: seqs }, receipts));
      } catch {}
    }
    this._drain();
    this._notifyAvailable();
  }

  _acceptJournal(owner, acceptedThroughSeq) {
    if (!owner?.sessionId || !Number.isSafeInteger(acceptedThroughSeq)) return false;
    let accepted = true;
    if (this.sessionPersistence && this.store.advanceOwnedSessionAcceptance) {
      accepted = this.store.advanceOwnedSessionAcceptance({ ...owner, acceptedThroughSeq });
    }
    const stillCurrent = owner.runtime === this.runtime
      && owner.authorityToken === this.authorityToken
      && owner.sessionId === this.sessionId
      && owner.epoch === this.cognitionEpoch;
    if (!accepted || !stillCurrent) {
      this.logger.info(`${this.logPrefix}_session_acceptance_stale`, { acceptedThroughSeq });
      return false;
    }
    this.syncedThroughSeq = Math.max(this.syncedThroughSeq, acceptedThroughSeq);
    return true;
  }

  _failItems(items, error) {
    const seqs = items.map((item) => item.seq).filter((seq) => seq != null);
    const receipts = receiptIds(items);
    this._recordActivity('agent.turn_completed', {
      inboundSeq: seqs.at(-1) ?? null,
      status: 'failed',
      summary: safe(error),
      metadata: {
        runtime: this.runtime,
        inboundSeqs: seqs,
        terminal: error instanceof CognitionLimitError,
      },
    });
    if (this.onTurnFailed) {
      try {
        this.onTurnFailed(withReceiptIds({ inboundSeqs: seqs, error: safe(error) }, receipts));
      } catch {}
    }
    this._notifyAvailable();
  }

  _drain() {
    if (this.stopping || this.settingsUpdate || !this.connected || !this.child?.stdin?.writable) return;
    if (this._promptBusy()) return;
    const merged = this.queue.splice(0).flat();
    if (merged.length > 0) this._writeBatch(merged);
  }

  _markSessionUnresumable() {
    if (!this.sessionPersistence || !this.sessionId || !this.sessionInitialized) return;
    try {
      const marked = this.store.markOwnedSessionUnresumable?.({
        runtime: this.runtime,
        authorityToken: this.authorityToken,
        sessionId: this.sessionId,
        epoch: this.cognitionEpoch,
      });
      if (marked === false) this.logger.info(`${this.logPrefix}_session_unresumable_stale`);
    } catch (error) {
      this.logger.warn(`${this.logPrefix}_session_unresumable_failed`, { error: safe(error) });
    }
  }

  _persistSession() {
    if (!this.sessionPersistence || !this.sessionId) return;
    try {
      if (this.store.publishOwnedSession) {
        const published = this.store.publishOwnedSession({
          runtime: this.runtime,
          authorityToken: this.authorityToken,
          sessionId: this.sessionId,
          policyHash: this.policyHash,
          epoch: this.cognitionEpoch,
          acceptedThroughSeq: this.syncedThroughSeq,
          sessionReady: this.sessionInitialized,
        });
        if (!published) this.logger.info(`${this.logPrefix}_session_publish_stale`);
        return;
      }
      this.store.setGlobalSession?.(this.sessionId, this.policyHash, {
        runtime: this.runtime,
        epoch: this.cognitionEpoch,
        syncedThroughSeq: this.syncedThroughSeq,
        sessionReady: this.sessionInitialized,
      });
    } catch (error) {
      this.logger.warn(`${this.logPrefix}_session_persist_failed`, { error: safe(error) });
    }
  }

  _handleExit(child, code, signal, processError = null) {
    if (this.child !== child) return;
    this.child = null;
    this.connected = false;
    this._transport().rejectAll(new Error(`${this.runtimeLabel} connection lost`));
    if (!this.stopping) this._terminateProcessTree(child, 'SIGKILL');
    this._cleanupRuntimeFiles();
    const wasActive = this.turnActive;
    const acceptanceUnknown = wasActive && this.sessionInitialized;
    const items = this.activeItems.splice(0);
    this.activeOwner = null;
    this.turnActive = false;
    if (this.settingsUpdate) this.settingsUpdate = null;
    this.turnStartedAtMs = null;
    this.modelWorking = false;
    this.modelStartedAtMs = null;
    this.deliveredThroughSeq = this.syncedThroughSeq;
    if (acceptanceUnknown && !this.stopping && this.startedOk && this.autoReconnect) {
      // The transcript state at death is unknowable; never resume it and replay
      // the journal into one fresh session instead.
      this._markSessionUnresumable();
      this.sessionId = null;
      this.sessionInitialized = false;
      this.resumeRequested = false;
      this.needsHistoryPrefix = true;
      this.syncedThroughSeq = 0;
      this.deliveredThroughSeq = 0;
    }
    if (wasActive) {
      this._failItems(items, processError ?? this.turnError
        ?? new Error(`${this.runtimeLabel} connection lost (code ${code}, signal ${signal})`));
    }
    this.turnError = null;
    if (this.stopping || !this.startedOk || !this.autoReconnect) {
      this._resolveIdleWaiters();
      return;
    }
    this.logger.warn(`${this.logPrefix}_disconnected`, { code, signal, error: processError ? safe(processError) : undefined });
    void this._reconnectLoop();
  }

  async _reconnectLoop() {
    if (this.reconnecting || this.stopping) return;
    this.reconnecting = true;
    while (!this.stopping && !this.connected) {
      await delay(this.backoffMs);
      if (this.stopping) break;
      this.resumeRequested = this.sessionInitialized;
      try {
        await this._spawnPersistent();
        this.backoffMs = this.reconnectBaseMs;
        // A rotation in progress publishes when the coordinator commits it.
        if (!this.sessionRotation) this._persistSession();
        this._drain();
        this._notifyAvailable();
        break;
      } catch (error) {
        this.logger.warn(`${this.logPrefix}_reconnect_failed`, { error: safe(error), backoffMs: this.backoffMs });
        this.backoffMs = Math.min(this.backoffMs * 2, this.reconnectCapMs);
      }
    }
    this.reconnecting = false;
  }

  // ---- session rotation, interruption ---------------------------------------

  async _terminateCurrent() {
    const child = this.child;
    this.child = null;
    this.connected = false;
    this._transport().rejectAll(new Error(`${this.runtimeLabel} process replaced`));
    if (!child?.pid) {
      this._cleanupRuntimeFiles();
      return;
    }
    if (child.exitCode !== null) {
      this._terminateProcessTree(child, 'SIGKILL');
      this._cleanupRuntimeFiles();
      return;
    }
    const exited = new Promise((resolve) => child.once('exit', resolve));
    this._terminateProcessTree(child, 'SIGTERM');
    await Promise.race([exited, delay(3_000)]);
    if (child.exitCode === null) this._terminateProcessTree(child, 'SIGKILL');
    this._cleanupRuntimeFiles();
  }

  async _replaceProcess({ fresh, persist = true, activate = true }) {
    await this._terminateCurrent();
    if (fresh) {
      this.sessionId = null;
      this.sessionInitialized = false;
      this.resumeRequested = false;
    } else {
      this.resumeRequested = this.sessionInitialized;
    }
    await this._spawnPersistent();
    if (persist) this._persistSession();
    if (activate) {
      this._drain();
      this._notifyAvailable();
    }
  }

  async _restorePreparedSession(previous) {
    await this._terminateCurrent();
    this.sessionId = previous.sessionId;
    this.sessionInitialized = previous.sessionInitialized;
    this.resumeRequested = Boolean(previous.sessionId && previous.sessionInitialized);
    this.needsHistoryPrefix = previous.needsHistoryPrefix;
    this.syncedThroughSeq = previous.syncedThroughSeq;
    this.deliveredThroughSeq = previous.deliveredThroughSeq;
    this.model = previous.model;
    this.effort = previous.effort ?? this.effort;
    if (previous.sessionId) await this._spawnPersistent();
    // A load that had to fall back opened a session nobody has published.
    if (previous.sessionId && this.sessionId !== previous.sessionId) this._persistSession();
    this._drain();
    this._notifyAvailable();
  }

  async prepareSessionRotation({ rollback = {} } = {}) {
    while (this.settingsUpdate) await this.settingsUpdate;
    if (this.turnActive) throw new Error(`${this.runtimeLabel} session rotation requires an idle runtime`);
    if (this.sessionRotation) throw new Error(`${this.runtimeLabel} session rotation is already prepared`);
    const previous = Object.freeze({
      sessionId: this.sessionId,
      sessionInitialized: this.sessionInitialized,
      resumeRequested: this.resumeRequested,
      needsHistoryPrefix: this.needsHistoryPrefix,
      syncedThroughSeq: this.syncedThroughSeq,
      deliveredThroughSeq: this.deliveredThroughSeq,
      model: rollback.model ?? this.model,
      effort: rollback.effort ?? this.effort,
    });
    this.sessionRotation = { id: null, previous };
    try {
      await this._replaceProcess({ fresh: true, persist: false, activate: false });
      this.sessionRotation.id = this.sessionId;
      return Object.freeze({ id: this.sessionId, sessionReady: this.sessionInitialized });
    } catch (error) {
      try { await this._restorePreparedSession(previous); } catch (restoreError) {
        this.logger.warn(`${this.logPrefix}_session_prepare_restore_failed`, { error: safe(restoreError) });
      }
      this.sessionRotation = null;
      throw error;
    }
  }

  commitSessionRotation(prepared, { persist = true, preserveContext = false } = {}) {
    if (!prepared?.id || this.sessionRotation?.id !== prepared.id || this.sessionId !== prepared.id) {
      throw new Error(`prepared ${this.runtimeLabel} session is no longer current`);
    }
    this.needsHistoryPrefix = preserveContext;
    this.sessionRotation = null;
    if (persist) this._persistSession();
    this._drain();
    this._notifyAvailable();
    return this.sessionId;
  }

  async cancelSessionRotation() {
    const rotation = this.sessionRotation;
    this.sessionRotation = null;
    if (!rotation?.previous) return;
    await this._restorePreparedSession(rotation.previous);
  }

  _sendCancelNotification() {
    // Subclasses send protocol-specific cancel notification.
  }

  cancelTurn() {
    if (!this.turnActive || !this.sessionId || !this.child?.stdin?.writable) return false;
    this.cancelledOwner = this.activeOwner;
    this._sendCancelNotification();
    return true;
  }

  async interruptTurn() {
    while (this.settingsUpdate) await this.settingsUpdate;
    if (!this.turnActive) return;
    const inboundSeqs = this._activeSeqs();
    const receipts = receiptIds(this.activeItems);
    this.activeItems.splice(0);
    this.activeOwner = null;
    this.turnActive = false;
    this.turnStartedAtMs = null;
    this.modelWorking = false;
    this.modelStartedAtMs = null;
    if ((inboundSeqs.length > 0 || receipts.length > 0) && this.onTurnAbandoned) {
      try {
        this.onTurnAbandoned(withReceiptIds({ inboundSeqs }, receipts));
      } catch (error) {
        this.logger.warn('turn_abandoned_notify_failed', { error: safe(error) });
      }
    }
    this.turnError = null;
    this.deliveredThroughSeq = this.syncedThroughSeq;
    if (this.sessionId) this._sendCancelNotification();
    this._resolveIdleWaiters();
  }

  async rotateSession({ resume = false } = {}) {
    while (this.settingsUpdate) await this.settingsUpdate;
    if (this.turnActive) throw new Error(`${this.runtimeLabel} session rotation requires an idle runtime`);
    if (!resume) {
      this.syncedThroughSeq = 0;
      this.deliveredThroughSeq = 0;
      this.needsHistoryPrefix = false;
      this.store.clearGlobalSession?.();
    }
    await this._replaceProcess({ fresh: !resume });
    this._persistSession();
    return this.sessionId;
  }

  // ---- model settings --------------------------------------------------------

  setModel(model, { persist = true } = {}) {
    if (persist) this.store.setSetting?.(this.modelSettingKey, model);
    this.model = model;
  }

  setEffort(effort, { persist = true } = {}) {
    if (this.capabilities.effortControl === 'unsupported') {
      throw new Error(`${this.runtimeLabel} does not support daemon-side effort control`);
    }
    this._validateEffortChoice(effort);
    if (persist && this.effortSettingKey) this.store.setSetting?.(this.effortSettingKey, effort);
    this.effort = effort;
  }

  _validateEffortChoice(effort) {
    const entry = currentModelEntry({
      runtime: this.runtime,
      model: this.resolvedModel,
      resolvedModel: this.resolvedModel,
      models: this.modelCatalog,
    });
    if (!entry?.effortsKnown) return;
    if (!entry.efforts.length) {
      throw new Error(`${this.resolvedModel || this.runtimeLabel} does not support reasoning effort control`);
    }
    if (!entry.efforts.includes(effort)) {
      throw new Error(`Unsupported effort ${effort} for ${this.resolvedModel} (supported: ${entry.efforts.join(', ')})`);
    }
  }

  async listModels() {
    return this.modelCatalog;
  }

  _resolveIdleWaiters() {
    const waiters = this.idleWaiters.splice(0);
    for (const resolve of waiters) resolve();
  }

  _notifyAvailable() {
    if (this.turnInProgress || this.queue.length > 0 || !this.connected) return;
    this._resolveIdleWaiters();
    try { this.onAvailable?.(); } catch (error) {
      this.logger.warn(`${this.logPrefix}_available_callback_failed`, { error: safe(error) });
    }
  }

  _recordActivity(kind, fields = {}) {
    try {
      this.activitySeq += 1;
      this.store.recordActivity?.({
        eventKey: `${this.runtime}:${this.sessionId ?? 'na'}:${this.instanceId}:${kind}:${this.activitySeq}`,
        kind,
        summary: fields.summary,
        status: fields.status,
        inboundSeq: fields.inboundSeq,
        metadata: fields.metadata,
      });
    } catch (error) {
      this.logger.warn('activity_persist_failed', { kind, error: safe(error) });
    }
  }

  async stop() {
    this.stopping = true;
    this.autoReconnect = false;
    await this.settingsUpdate;
    const queued = this.queue.flat();
    this.queue = [];
    if (receiptIds(queued).length > 0) {
      this._failItems(queued, new Error(`${this.runtimeLabel} stopped before queued work started`));
    }
    const items = this.activeItems.splice(0);
    this.activeOwner = null;
    const midTurn = this.turnActive;
    this.turnActive = false;
    if (items.length > 0) this._failItems(items, new Error(`${this.runtimeLabel} stopped during an active turn`));
    if (midTurn) this._markSessionUnresumable();
    this._resolveIdleWaiters();
    await this._terminateCurrent();
  }
}
