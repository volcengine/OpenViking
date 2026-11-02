// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { spawn as nodeSpawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { cognitionHistoryPrefix, CognitionLimitError } from './cognition.mjs';
import { safeError as safe } from './logger.mjs';
import { activityPayload } from './activity.mjs';
import { terminateProcessTree } from './process-utils.mjs';
import { agentToolEnv } from './actions/api.mjs';
import { assembleConversationPolicy } from './conversation-policy.mjs';
import {
  DURABLE_VERSION,
  assertDurableVersion,
  durableCommand,
  durableHostPath,
  sanitizeDurableEnv,
} from './durable-runtime.mjs';
import { deliveryItem, receiptIds, withReceiptIds } from './runtime-input.mjs';
import { getRuntimeDefinition } from './runtime-catalog.mjs';

const MAX_LINE_BYTES = 5_000_000;
const START_TIMEOUT_MS = 20_000;
const RECONNECT_BASE_MS = 1_000;
const RECONNECT_CAP_MS = 30_000;
const RPC_TIMEOUT_MS = 10_000;

const DURABLE_RUNTIME = getRuntimeDefinition('durable');
export const DURABLE_EFFORTS = DURABLE_RUNTIME.efforts;

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function appendBounded(current, chunk, label) {
  const next = current + chunk;
  if (Buffer.byteLength(next, 'utf8') > MAX_LINE_BYTES) {
    throw new Error(`durable ${label} exceeded local limit`);
  }
  return next;
}

function modelEfforts(model) {
  return model?.reasoning ? [...DURABLE_EFFORTS] : ['off'];
}

// Adapter over the durable host child process. The host owns the pi-durable
// Harness (one SQLite storage), resumes runs a previous daemon generation left
// unfinished, and executes the Core agent tools through actions.sock.
export class DurableAgent {
  constructor(config, store, logger, options = {}) {
    this.config = config;
    this.store = store;
    this.logger = logger;
    this._spawn = options.spawn ?? nodeSpawn;
    this.onTurnFailed = options.onTurnFailed ?? null;
    this.onTurnSucceeded = options.onTurnSucceeded ?? null;
    this.onTurnAbandoned = options.onTurnAbandoned ?? null;
    this.onAvailable = options.onAvailable ?? null;
    this.startTimeoutMs = options.startTimeoutMs ?? START_TIMEOUT_MS;
    this.reconnectBaseMs = options.reconnectBaseMs ?? RECONNECT_BASE_MS;
    this.reconnectCapMs = options.reconnectCapMs ?? RECONNECT_CAP_MS;
    this.rpcTimeoutMs = options.rpcTimeoutMs ?? RPC_TIMEOUT_MS;

    const cognition = store.getCognitionState?.()
      ?? { runtime: DURABLE_RUNTIME.id, epoch: 1, authorityToken: null };
    this.runtime = DURABLE_RUNTIME.id;
    this.capabilities = DURABLE_RUNTIME.capabilities;
    this.cognitionEpoch = options.cognitionEpoch ?? cognition.epoch;
    this.authorityToken = options.authorityToken ?? cognition.authorityToken ?? '';
    this.sessionPersistence = options.sessionPersistence !== false;
    this.autoReconnect = this.sessionPersistence;
    this.allowResume = options.allowResume !== false;
    this.syncedThroughSeq = options.syncedThroughSeq ?? 0;
    this.deliveredThroughSeq = this.syncedThroughSeq;

    this.systemPrompt = assembleConversationPolicy(config);
    this.model = options.model
      ?? store.getSetting?.('durable_model')
      ?? options.initialModel
      ?? config.durableModel
      ?? '';
    this.effort = options.effort
      ?? store.getSetting?.('durable_effort')
      ?? config.durableEffort
      ?? 'medium';

    this.sessionId = null;
    this.sessionInitialized = false;
    this.resumeRequested = false;
    this.needsHistoryPrefix = false;
    this.child = null;
    this.connected = false;
    this.hostVersion = '';
    this.turnActive = false;
    this.turnStartedAtMs = null;
    this.modelWorking = false;
    this.turnError = null;
    this.turnUsage = null;
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
    this.toolStarts = new Map();
    this.rpcSeq = 0;
    this.pendingRpc = new Map();
    this.idleWaiters = [];
    this.sessionRotation = null;
    this.nativeSessionId = null;
    this.appliedModel = null;
    this.appliedEffort = null;
    this.settingsUpdate = null;
    this.resolvedModel = '';
    this.listModelsCache = null;
  }

  get threadId() { return this.sessionId; }

  get policyHash() {
    return createHash('sha256')
      .update(this.systemPrompt).update('\0')
      .update(this.config.workspace).update('\0')
      .update('durable-host-v1').digest('hex');
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
      // The pi-durable conversation serving this session. The daemon persists
      // it with the session row; the host never stores it locally.
      nativeSessionId: this.nativeSessionId,
      policyHash: this.policyHash,
      epoch: this.cognitionEpoch,
      syncedThroughSeq: this.syncedThroughSeq,
      sessionReady: this.sessionInitialized,
    };
  }

  activateSession() {
    this.sessionPersistence = true;
    this.autoReconnect = true;
  }

  async start() {
    this.stopping = false;
    const global = this.store.getGlobalSession?.() ?? {};
    const resumable = this.allowResume
      && global.sessionId
      && global.sessionReady !== false
      && global.policyHash === this.policyHash
      && global.runtime === this.runtime
      && global.epoch === this.cognitionEpoch;
    this.sessionId = resumable ? global.sessionId : randomUUID();
    this.nativeSessionId = resumable ? (this.store.getNativeSessionId?.() ?? null) : null;
    this.sessionInitialized = Boolean(resumable);
    this.resumeRequested = Boolean(resumable);
    this.syncedThroughSeq = resumable ? global.syncedThroughSeq ?? 0 : this.syncedThroughSeq;
    this.deliveredThroughSeq = this.syncedThroughSeq;
    this.needsHistoryPrefix = !resumable;

    if (this.needsHistoryPrefix) await this.store.prepareOVContext?.(this.cognitionEpoch);
    await this._spawnHost();
    this.startedOk = true;
    this.backoffMs = this.reconnectBaseMs;
    this._persistSession();
    this._drain();
    this._notifyAvailable();
  }

  larkProfile() {
    return this.config.connectors.includes('lark') ? this.config.profile : '';
  }

  hostPath() {
    return durableHostPath(this.config.durableHostPath ?? process.env.VIKA_DURABLE_HOST ?? '');
  }

  _spawnHost() {
    return new Promise((resolve, reject) => {
      const command = durableCommand(this.hostPath());
      const env = sanitizeDurableEnv(
        process.env,
        this.larkProfile(),
        agentToolEnv(this.config, this.authorityToken),
        this.config,
      );
      let child;
      try {
        child = this._spawn(command.command, command.args, {
          cwd: this.config.workspace,
          env,
          shell: false,
          detached: process.platform !== 'win32',
          stdio: ['pipe', 'pipe', 'pipe'],
        });
      } catch (error) {
        reject(error);
        return;
      }
      this.child = child;
      this.stdoutBuffer = '';
      this.stderrBuffer = '';
      let ready = false;
      let settled = false;
      const failStart = (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (this.child === child) {
          this.child = null;
          this.connected = false;
        }
        terminateProcessTree(child, 'SIGKILL');
        reject(error);
      };
      const timer = setTimeout(() => failStart(new Error(
        'durable host startup timed out; check that @earendil-works/pi-durable is installed (npm ci) and a provider has credentials',
      )), this.startTimeoutMs);
      timer.unref?.();

      child.stdin?.on('error', (error) => {
        if (this.child === child) this.logger.warn('durable_stdin_error', { error: safe(error) });
      });
      child.stdout?.setEncoding('utf8');
      child.stderr?.setEncoding('utf8');
      child.stdout?.on('data', (chunk) => {
        if (this.child === child) this._onStdout(chunk, child);
      });
      child.stderr?.on('data', (chunk) => {
        if (this.child === child) this._onStderr(chunk);
      });
      child.once('error', (error) => {
        if (!ready) failStart(error);
        else this._handleExit(child, null, null, error);
      });
      child.once('exit', (code, signal) => {
        if (!ready) {
          failStart(new Error(`durable host exited during startup (code ${code}, signal ${signal})`));
          return;
        }
        this._handleExit(child, code, signal);
      });

      const id = this._nextRpcId();
      this.pendingRpc.set(id, {
        command: 'hello',
        resolve: () => {
          if (settled) return;
          clearTimeout(timer);
          settled = true;
          ready = true;
          resolve();
        },
        reject: failStart,
      });
      try {
        this._write({
          id,
          type: 'hello',
          dataDir: this.config.dataDir,
          workspace: this.config.workspace,
          policy: this.systemPrompt,
          sessionKey: this.sessionId,
          // The host opens this conversation instead of keeping its own index;
          // `ready` reports a replacement when the stored handle is stale.
          nativeSessionId: this.nativeSessionId,
          model: this.model,
          effort: this.effort,
        });
      } catch (error) {
        this.pendingRpc.delete(id);
        failStart(error);
      }
    });
  }

  _nextRpcId() {
    this.rpcSeq += 1;
    return `durable-${this.rpcSeq}`;
  }

  _write(payload) {
    if (!this.child?.stdin?.writable) throw new Error('durable host stdin is not writable');
    this.child.stdin.write(`${JSON.stringify(payload)}\n`);
  }

  _rpc(payload) {
    if (!this.connected || !this.child?.stdin?.writable) {
      return Promise.reject(new Error('durable host is not connected'));
    }
    const id = this._nextRpcId();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pendingRpc.delete(id);
        reject(new Error(`durable rpc timed out: ${payload.type}`));
      }, this.rpcTimeoutMs);
      timer.unref?.();
      this.pendingRpc.set(id, {
        command: payload.type,
        resolve: (value) => { clearTimeout(timer); resolve(value); },
        reject: (error) => { clearTimeout(timer); reject(error); },
      });
      try {
        this._write({ id, ...payload });
      } catch (error) {
        this.pendingRpc.delete(id);
        clearTimeout(timer);
        reject(error);
      }
    });
  }

  _rejectPendingRpc(error) {
    const pending = [...this.pendingRpc.values()];
    this.pendingRpc.clear();
    for (const entry of pending) entry.reject(error);
  }

  deliver(text, seq = null, receiptId = null) {
    return this.deliverBatch([deliveryItem({ text, seq, receiptId })]);
  }

  deliverBatch(items) {
    if (this.stopping || !Array.isArray(items) || items.length === 0) return false;
    const batch = items.map(deliveryItem);
    if (!this.connected || !this.child?.stdin?.writable) {
      this.queue.push(batch);
      return true;
    }
    this._writeBatch(batch);
    return true;
  }

  tryStartBatch(items) {
    if (this.stopping || !this.connected || this.turnInProgress || this.queue.length > 0
      || !Array.isArray(items) || items.length === 0) return false;
    this._writeBatch(items.map(deliveryItem));
    return true;
  }

  whenIdle() {
    if (!this.turnInProgress && this.queue.length === 0) return Promise.resolve();
    return new Promise((resolve) => this.idleWaiters.push(resolve));
  }

  _bodyFor(items) {
    const seqs = items.map((item) => item.seq).filter((seq) => seq != null);
    const beforeSeq = seqs.length > 0 ? Math.min(...seqs) : null;
    const throughSeq = this.store.cognitionJournalBeforeInbound?.(
      beforeSeq,
      this.cognitionEpoch,
    ) ?? 0;
    const needsCatchup = this.needsHistoryPrefix;
    let body = items.map((item) => item.text).join('\n');
    let prefixThroughSeq = this.deliveredThroughSeq;
    if (needsCatchup) {
      const catchup = cognitionHistoryPrefix(
        this.store,
        this.cognitionEpoch,
        throughSeq,
        this.needsHistoryPrefix ? 0 : this.deliveredThroughSeq,
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

  _writeBatch(items) {
    if (this.settingsUpdate || (!this.turnActive && this._settingsPending())) {
      this.queue.push(items);
      this._applySettings();
      return;
    }
    let prepared;
    try {
      prepared = this._bodyFor(items);
    } catch (error) {
      this._failItems(items, error);
      return;
    }
    const wasActive = this.turnActive;
    const owner = wasActive ? this.activeOwner : Object.freeze({
      runtime: this.runtime,
      authorityToken: this.authorityToken,
      sessionId: this.sessionId,
      epoch: this.cognitionEpoch,
    });
    const seqs = prepared.seqs;
    const requestId = `durable:${this.sessionId}:${seqs.length > 0 ? seqs.join('-') : randomUUID()}`;
    try {
      this._write({ type: 'submit', text: prepared.body, requestId });
    } catch (error) {
      this._failItems(items, error);
      return;
    }
    this.needsHistoryPrefix = false;
    this.deliveredThroughSeq = Math.max(this.deliveredThroughSeq, prepared.deliveredThroughSeq);
    if (!wasActive) this.activeOwner = owner;
    this.activeItems.push(...items);
    this.turnActive = true;
    if (!wasActive) this.turnStartedAtMs = Date.now();
    if (!wasActive) this.modelWorking = true;
    const input = items.map((item) => item.text).join('\n');
    this._recordActivity(wasActive ? 'agent.steered' : 'agent.turn_started', {
      inboundSeq: prepared.seqs.at(-1) ?? null,
      summary: wasActive ? input : undefined,
      metadata: {
        runtime: DURABLE_RUNTIME.id,
        input,
        historyChars: Math.max(0, prepared.body.length - input.length),
        inboundSeqs: prepared.seqs,
      },
    });
  }

  _onStdout(chunk, child) {
    try { this.stdoutBuffer = appendBounded(this.stdoutBuffer, chunk, 'stdout line'); } catch (error) {
      this.turnError = error;
      terminateProcessTree(child, 'SIGKILL');
      return;
    }
    let index;
    while ((index = this.stdoutBuffer.indexOf('\n')) >= 0) {
      const line = this.stdoutBuffer.slice(0, index);
      this.stdoutBuffer = this.stdoutBuffer.slice(index + 1);
      if (!line.trim()) continue;
      try { this._handleLine(line); } catch (error) {
        this.logger.warn('durable_line_error', { error: safe(error) });
      }
    }
  }

  _onStderr(chunk) {
    try { this.stderrBuffer = appendBounded(this.stderrBuffer, chunk, 'stderr line'); } catch {
      this.stderrBuffer = this.stderrBuffer.slice(-4_000);
    }
    let index;
    while ((index = this.stderrBuffer.indexOf('\n')) >= 0) {
      const line = this.stderrBuffer.slice(0, index);
      this.stderrBuffer = this.stderrBuffer.slice(index + 1);
      if (line.trim()) this.logger.debug('durable_stderr', { detail: safe(line.slice(0, 500)) });
    }
  }

  _handleLine(line) {
    let message;
    try { message = JSON.parse(line.replace(/\r$/, '')); } catch { return; }
    if (!message || typeof message !== 'object') return;
    if (message.id != null && (message.ok === true || message.ok === false)) {
      const pending = this.pendingRpc.get(message.id);
      if (!pending) return;
      this.pendingRpc.delete(message.id);
      if (message.ok === false) pending.reject(new Error(message.error || `durable ${pending.command} failed`));
      else pending.resolve(message);
      return;
    }
    this._handleEvent(message);
  }

  _handleEvent(message) {
    switch (message.type) {
      case 'ready': {
        this.hostVersion = String(message.version ?? '');
        try {
          assertDurableVersion(this.hostVersion);
        } catch (error) {
          // A mismatched host must not silently serve a release it was not
          // tested against: reject the pending start and stop the child.
          this._rejectPendingRpc(error);
          try { terminateProcessTree(this.child, 'SIGKILL'); } catch {}
          this.child = null;
          this.connected = false;
          return;
        }
        this.connected = true;
        this.sessionInitialized = true;
        // A replacement conversation is not an error: the stored handle was
        // stale, so adopt the one the host actually opened and publish it with
        // the session row. Publication is fenced by cognition authority, so a
        // late host cannot rewrite a session that has already moved on.
        if (Number.isSafeInteger(message.conversationId)) this.nativeSessionId = message.conversationId;
        this.appliedModel = this.model;
        this.appliedEffort = this.effort;
        this.listModelsCache = null;
        if (message.resolvedModel) this.resolvedModel = String(message.resolvedModel);
        this._persistSession();
        this._recordActivity('agent.ready', { metadata: { runtime: DURABLE_RUNTIME.id, host: this.hostVersion } });
        this._drain();
        this._notifyAvailable();
        return;
      }
      case 'turn_start': {
        this.modelWorking = true;
        if (!this.turnActive) {
          // A run resumed from storage after a daemon restart has no local
          // inbound items but is still an active turn.
          this.turnActive = true;
          this.turnStartedAtMs = Date.now();
          this.activeOwner = Object.freeze({
            runtime: this.runtime,
            authorityToken: this.authorityToken,
            sessionId: this.sessionId,
            epoch: this.cognitionEpoch,
          });
        }
        this._recordActivity('agent.turn_started', {
          metadata: { runtime: DURABLE_RUNTIME.id, resumed: this.activeItems.length === 0 },
        });
        return;
      }
      case 'note':
        if (message.text) {
          this._recordActivity('agent.note', {
            summary: String(message.text),
            metadata: { runtime: DURABLE_RUNTIME.id, delivery: 'telemetry_only' },
          });
        }
        return;
      case 'thinking':
        if (message.text) {
          this._recordActivity('agent.thinking', {
            metadata: { runtime: DURABLE_RUNTIME.id, thinking: String(message.text) },
          });
        }
        return;
      case 'tool_start': {
        this.modelWorking = false;
        const name = String(message.toolName ?? '');
        if (message.toolId) this.toolStarts.set(message.toolId, Date.now());
        this._recordActivity('agent.tool_called', {
          summary: name || 'unknown tool',
          status: 'RUNNING',
          metadata: {
            runtime: DURABLE_RUNTIME.id,
            toolId: message.toolId,
            toolName: name,
            input: activityPayload(message.input),
          },
        });
        return;
      }
      case 'tool_end': {
        const startedAtMs = this.toolStarts.get(message.toolId);
        this.toolStarts.delete(message.toolId);
        const output = activityPayload(message.output);
        this._recordActivity('agent.tool_completed', {
          status: message.isError === true ? 'FAILED' : 'COMPLETED',
          summary: String(message.toolName || (message.isError === true ? 'durable tool failed' : 'durable tool completed')),
          metadata: {
            runtime: DURABLE_RUNTIME.id,
            toolId: message.toolId,
            toolName: message.toolName,
            ...(startedAtMs ? { durationMs: Date.now() - startedAtMs } : {}),
            ...(message.isError === true ? { error: output } : { output }),
          },
        });
        this.modelWorking = true;
        return;
      }
      case 'usage': {
        const current = this.turnUsage ?? { inputTokens: 0, cachedInputTokens: 0, outputTokens: 0, costUsd: 0 };
        this.turnUsage = {
          inputTokens: current.inputTokens + (Number(message.inputTokens) || 0),
          cachedInputTokens: current.cachedInputTokens + (Number(message.cachedInputTokens) || 0),
          outputTokens: current.outputTokens + (Number(message.outputTokens) || 0),
          costUsd: current.costUsd + (Number(message.costUsd) || 0),
        };
        return;
      }
      case 'settled': {
        // An interrupt clears turnActive locally; the host still reports the
        // aborted run. Do not turn that into a successful turn callback.
        if (!this.turnActive) {
          this.logger.info('durable_settled_without_turn', { turn: message.turn });
          return;
        }
        const error = message.status === 'failed'
          ? new Error(String(message.error || 'durable run ended without an answer'))
          : null;
        this._completeTurn(error);
        return;
      }
      case 'fatal': {
        const error = new Error(String(message.error || 'durable host failed'));
        this.turnError = error;
        this.logger.warn('durable_fatal', { error: safe(error) });
        return;
      }
      case 'stopped':
        return;
      default:
        return;
    }
  }

  _activeSeqs() {
    return [...new Set(
      this.activeItems.map((item) => item.seq).filter((seq) => seq != null),
    )];
  }

  _completeTurn(error = null) {
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
    this.deliveredThroughSeq = this.syncedThroughSeq;
    this.toolStarts.clear();
    this._recordActivity('agent.turn_completed', {
      inboundSeq: seqs.at(-1) ?? null,
      status: error ? 'failed' : 'completed',
      summary: error ? safe(error) : undefined,
      metadata: {
        runtime: DURABLE_RUNTIME.id,
        inboundSeqs: seqs,
        ...(turnStartedAtMs ? { durationMs: Date.now() - turnStartedAtMs } : {}),
        ...(this.turnUsage ?? {}),
      },
    });
    this.turnUsage = null;
    if (error && this.onTurnFailed) {
      try {
        this.onTurnFailed(withReceiptIds({ inboundSeqs: seqs, error: safe(error) }, receipts));
      } catch {}
    }
    if (!error && this.onTurnSucceeded) {
      try { this.onTurnSucceeded(withReceiptIds({ inboundSeqs: seqs }, receipts)); } catch {}
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
      this.logger.info('durable_session_acceptance_stale', { acceptedThroughSeq });
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
        runtime: DURABLE_RUNTIME.id,
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
    if (this.queue.length === 0) return;
    if (this.turnActive) {
      while (this.queue.length > 0) this._writeBatch(this.queue.shift());
      return;
    }
    const merged = this.queue.splice(0).flat();
    if (merged.length > 0) this._writeBatch(merged);
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
          nativeSessionId: this.nativeSessionId,
        });
        if (!published) this.logger.info('durable_session_publish_stale');
        return;
      }
      this.store.setGlobalSession?.(this.sessionId, this.policyHash, {
        runtime: this.runtime,
        epoch: this.cognitionEpoch,
        syncedThroughSeq: this.syncedThroughSeq,
        sessionReady: this.sessionInitialized,
        nativeSessionId: this.nativeSessionId,
      });
    } catch (error) {
      this.logger.warn('durable_session_persist_failed', { error: safe(error) });
    }
  }

  _handleExit(child, code, signal, processError = null) {
    if (this.child !== child) return;
    this.child = null;
    this.connected = false;
    this._rejectPendingRpc(processError ?? new Error('durable host connection lost'));
    if (!this.stopping) terminateProcessTree(child, 'SIGKILL');
    this.modelWorking = false;
    // Unlike a transport whose accepted input is unknowable, the durable host
    // commits a submission before it asks the model. A crash is therefore
    // resumable: keep the session key, keep the turn's items, and let the
    // replacement host call harness.resume() to finish the run.
    if (this.stopping || !this.startedOk || !this.autoReconnect) {
      const items = this.activeItems.splice(0);
      const wasActive = this.turnActive;
      this.activeOwner = null;
      this.turnActive = false;
      this.turnStartedAtMs = null;
      if (wasActive) {
        this._failItems(items, processError ?? this.turnError
          ?? new Error(`durable host connection lost (code ${code}, signal ${signal})`));
      }
      this.turnError = null;
      this._resolveIdleWaiters();
      return;
    }
    this.logger.warn('durable_disconnected', { code, signal, error: processError ? safe(processError) : undefined });
    void this._reconnectLoop();
  }

  async _reconnectLoop() {
    if (this.reconnecting || this.stopping) return;
    this.reconnecting = true;
    let attempts = 0;
    while (!this.stopping && !this.connected) {
      await delay(this.backoffMs);
      if (this.stopping) break;
      try {
        await this._spawnHost();
        this.backoffMs = this.reconnectBaseMs;
        this._drain();
        this._notifyAvailable();
        break;
      } catch (error) {
        attempts += 1;
        this.logger.warn('durable_reconnect_failed', { error: safe(error), backoffMs: this.backoffMs, attempts });
        // A host that cannot start (missing model credentials, broken install)
        // will not start on the next try either. Do not leave a turn marked
        // active forever: settle it as failed after bounded retries.
        if (this.turnActive && attempts >= 3) {
          const items = this.activeItems.splice(0);
          const seqs = [...new Set(items.map((item) => item.seq).filter((seq) => seq != null))];
          this.activeOwner = null;
          this.turnActive = false;
          this.turnStartedAtMs = null;
          this.modelWorking = false;
          this._failItems(items, error);
          this.logger.warn('durable_turn_failed_after_reconnect', { inboundSeqs: seqs });
          this._resolveIdleWaiters();
          break;
        }
        this.backoffMs = Math.min(this.backoffMs * 2, this.reconnectCapMs);
      }
    }
    this.reconnecting = false;
  }

  async _terminateCurrent() {
    const child = this.child;
    if (!child?.pid) {
      this.child = null;
      this.connected = false;
      this._rejectPendingRpc(new Error('durable host replaced'));
      return;
    }
    // Ask the host to close its storage first; a stopped host is resumable.
    try { this._write({ type: 'stop' }); } catch {}
    this.child = null;
    this.connected = false;
    this._rejectPendingRpc(new Error('durable host replaced'));
    if (child.exitCode !== null || child.signalCode !== null) {
      terminateProcessTree(child, 'SIGKILL');
      return;
    }
    let exitedOk = false;
    const exited = new Promise((resolve) => child.once('exit', () => { exitedOk = true; resolve(); }));
    terminateProcessTree(child, 'SIGTERM');
    await Promise.race([exited, delay(3_000)]);
    if (!exitedOk) {
      terminateProcessTree(child, 'SIGKILL');
      await Promise.race([exited, delay(1_000)]);
    }
    // A process killed by a signal reports exitCode null and signalCode set;
    // track the exit event rather than the code.
    if (!exitedOk) throw new Error('durable host did not exit after SIGKILL');
  }

  async prepareSessionRotation({ rollback = {} } = {}) {
    while (this.settingsUpdate) await this.settingsUpdate;
    if (this.turnActive) throw new Error('durable session rotation requires an idle runtime');
    if (this.sessionRotation) throw new Error('durable session rotation is already prepared');
    const previous = Object.freeze({
      sessionId: this.sessionId,
      nativeSessionId: this.nativeSessionId,
      sessionInitialized: this.sessionInitialized,
      resumeRequested: this.resumeRequested,
      needsHistoryPrefix: this.needsHistoryPrefix,
      syncedThroughSeq: this.syncedThroughSeq,
      deliveredThroughSeq: this.deliveredThroughSeq,
      model: rollback.model ?? this.model,
      effort: rollback.effort ?? this.effort,
    });
    const nextId = randomUUID();
    // The new conversation id stays inside the prepared plan: publishing it
    // before the daemon's session CAS would let the old session own the new
    // conversation if a host restart happened in between.
    const rotation = { id: nextId, nativeSessionId: null, previous };
    this.sessionRotation = rotation;
    try {
      const ack = await this._rpc({ type: 'rotate', nextSessionKey: nextId });
      if (!Number.isSafeInteger(ack?.conversationId)) {
        throw new Error('durable host rotated without a conversation id');
      }
      rotation.nativeSessionId = ack.conversationId;
      return Object.freeze({ id: nextId, nativeSessionId: ack.conversationId, sessionReady: true });
    } catch (error) {
      this.sessionRotation = null;
      try {
        await this._rpc({ type: 'open', sessionKey: previous.sessionId, nativeSessionId: previous.nativeSessionId });
      } catch {}
      throw error;
    }
  }

  commitSessionRotation(prepared, { persist = true, preserveContext = false } = {}) {
    if (!prepared?.id || this.sessionRotation?.id !== prepared.id) {
      throw new Error('prepared durable session is no longer current');
    }
    this.sessionId = prepared.id;
    this.nativeSessionId = prepared.nativeSessionId ?? null;
    this.sessionInitialized = true;
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
    await this._rpc({
      type: 'open',
      sessionKey: rotation.previous.sessionId,
      nativeSessionId: rotation.previous.nativeSessionId,
    });
    this.sessionId = rotation.previous.sessionId;
    this.nativeSessionId = rotation.previous.nativeSessionId;
    this.sessionInitialized = rotation.previous.sessionInitialized;
    this.resumeRequested = rotation.previous.resumeRequested;
    this.needsHistoryPrefix = rotation.previous.needsHistoryPrefix;
    this.syncedThroughSeq = rotation.previous.syncedThroughSeq;
    this.deliveredThroughSeq = rotation.previous.deliveredThroughSeq;
    this.model = rotation.previous.model;
    this.effort = rotation.previous.effort;
    this._drain();
    this._notifyAvailable();
  }

  async rotateSession({ resume = false } = {}) {
    while (this.settingsUpdate) await this.settingsUpdate;
    if (this.turnActive) throw new Error('durable session rotation requires an idle runtime');
    if (resume) {
      await this._rpc({ type: 'open', sessionKey: this.sessionId, nativeSessionId: this.nativeSessionId });
      return this.sessionId;
    }
    this.syncedThroughSeq = 0;
    this.deliveredThroughSeq = 0;
    this.needsHistoryPrefix = false;
    const nextId = randomUUID();
    const ack = await this._rpc({ type: 'rotate', nextSessionKey: nextId });
    if (!Number.isSafeInteger(ack?.conversationId)) {
      throw new Error('durable host rotated without a conversation id');
    }
    this.sessionId = nextId;
    this.nativeSessionId = ack.conversationId;
    this._persistSession();
    return this.sessionId;
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
    if ((inboundSeqs.length > 0 || receipts.length > 0) && this.onTurnAbandoned) {
      try { this.onTurnAbandoned(withReceiptIds({ inboundSeqs }, receipts)); } catch (error) {
        this.logger.warn('turn_abandoned_notify_failed', { error: safe(error) });
      }
    }
    this.turnError = null;
    this.deliveredThroughSeq = this.syncedThroughSeq;
    try {
      // Fire and forget: the host aborts and settles in its own time. Waiting
      // here would block /new behind a long-running tool.
      if (this.connected) this._write({ type: 'abort' });
    } catch {}
    this._resolveIdleWaiters();
  }

  setModel(model, { persist = true } = {}) {
    if (persist) this.store.setSetting?.('durable_model', model);
    this.model = model;
  }

  _settingsPending() {
    return this.appliedModel !== this.model || this.appliedEffort !== this.effort;
  }

  setEffort(effort, { persist = true } = {}) {
    if (persist) this.store.setSetting?.('durable_effort', effort);
    this.effort = effort;
  }

  _applySettings() {
    if (this.settingsUpdate) return;
    const model = this.model;
    const effort = this.effort;
    // Apply between prompts, never while a turn (including tools) is running.
    this.settingsUpdate = (async () => {
      try {
        await this._rpc({ type: 'configure', model, effort });
        this.appliedModel = model;
        this.appliedEffort = effort;
        if (model) this.resolvedModel = model;
        this.listModelsCache = null;
      } catch (error) {
        this.logger.warn('durable_settings_update_failed', { error: safe(error) });
        this._failItems(this.queue.splice(0).flat(), error);
      } finally {
        this.settingsUpdate = null;
        this._drain();
        this._notifyAvailable();
      }
    })();
  }

  async listModels() {
    if (!this.connected) return [];
    const now = Date.now();
    if (this.listModelsCache && now - this.listModelsCache.at < 5_000) return this.listModelsCache.models;
    const response = await this._rpc({ type: 'models' });
    const models = Array.isArray(response?.models) ? response.models : [];
    const entries = models.map((model) => ({
      id: String(model.id),
      efforts: Array.isArray(model.efforts) ? model.efforts : [...DURABLE_EFFORTS],
      ...(model.isDefault ? { isDefault: true } : {}),
    })).filter((model) => model.id);
    this.listModelsCache = { at: now, models: entries };
    return entries;
  }

  _recordActivity(kind, fields = {}) {
    try {
      this.activitySeq += 1;
      this.store.recordActivity?.({
        eventKey: `durable:${this.sessionId ?? 'na'}:${kind}:${this.activitySeq}`,
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

  _resolveIdleWaiters() {
    const waiters = this.idleWaiters.splice(0);
    for (const resolve of waiters) resolve();
  }

  _notifyAvailable() {
    if (this.turnInProgress || this.queue.length > 0 || !this.connected) return;
    this._resolveIdleWaiters();
    try { this.onAvailable?.(); } catch (error) {
      this.logger.warn('durable_available_callback_failed', { error: safe(error) });
    }
  }

  async stop() {
    this.stopping = true;
    this.autoReconnect = false;
    this._rejectPendingRpc(new Error('durable stopped'));
    await this.settingsUpdate;
    const midTurn = this.turnActive;
    const queued = this.queue.flat();
    this.queue = [];
    if (receiptIds(queued).length > 0) {
      this._failItems(queued, new Error('durable stopped before queued work started'));
    }
    const items = this.activeItems.splice(0);
    this.activeOwner = null;
    this.turnActive = false;
    if (items.length > 0) this._failItems(items, new Error('durable stopped during an active turn'));
    // The host resumes an unfinished run on the next start, so acceptance is
    // knowable; keep the session resumable unless the process was replaced.
    if (midTurn) this._persistSession();
    this._resolveIdleWaiters();
    await this._terminateCurrent();
  }
}

export { DURABLE_VERSION };
