// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { spawn as nodeSpawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { sanitizeAgentChildEnv, terminateProcessTree } from './process-utils.mjs';
import { agentCliCommands } from './agent-clis.mjs';
import { agentToolEnv, agentToolServer } from './actions/api.mjs';
import { safeError as safe } from './logger.mjs';
import { captureStartupStderr } from './runtime-diagnostics.mjs';
import { activityPayload } from './activity.mjs';
import { cognitionHistoryPrefix, CognitionLimitError } from './cognition.mjs';
import { assembleConversationPolicy } from './conversation-policy.mjs';
import { deliveryItem, receiptIds, withReceiptIds } from './runtime-input.mjs';
import { getRuntimeDefinition } from './runtime-catalog.mjs';

const CODEX_RUNTIME = getRuntimeDefinition('codex');
const CODEX_APP_SERVER_CONFIG_ARGS = Object.freeze([
  // Fast mode is a Codex service tier. Pin both the tier and its stable
  // feature gate for this daemon-owned app-server process.
  '-c', 'service_tier="fast"',
  '-c', 'features.fast_mode=true',
  // The user's interactive ~/.codex config drags desktop-only machinery into
  // every turn: plugin marketplaces, <recommended_plugins> prompt injections,
  // plugin-suggestion tools, and multi-agent framing. None of it serves a
  // chat-reply agent, and it measurably costs prefill size plus whole model
  // round-trips (~2x wall time on short replies). Skills (~/.agents) and the
  // personal_agent MCP server configured below are unaffected.
  '-c', 'features.plugins=false',
  '-c', 'features.tool_suggest=false',
  '-c', 'features.apps=false',
]);

// turn/completed carries error as an object whose .message is often a JSON blob
// wrapping the upstream API error; unwrap to the innermost human-readable message.
export function turnErrorText(error) {
  if (error === null || error === undefined) return '';
  const raw = typeof error === 'string'
    ? error
    : typeof error.message === 'string' ? error.message : JSON.stringify(error);
  try {
    const parsed = JSON.parse(raw);
    const inner = parsed?.error?.message ?? parsed?.message;
    if (typeof inner === 'string' && inner.trim()) return safe(inner);
  } catch { /* not JSON: use as-is */ }
  return safe(raw);
}

function cleanToolPart(value, fallback) {
  const text = String(value ?? '')
    .replace(/[\u0000-\u001f\u007f]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return text ? text.slice(0, 80) : fallback;
}

const TOOL_ITEM_TYPES = new Set([
  'mcpToolCall', 'dynamicToolCall', 'commandExecution', 'fileChange', 'webSearch',
  'imageView', 'imageGeneration', 'collabAgentToolCall',
]);

// Raw tool input/output for the Activity page (redacted + capped on write).
function toolPayloads(item) {
  switch (item.type) {
    case 'mcpToolCall':
      return {
        input: activityPayload(item.arguments),
        output: activityPayload(item.result?.content ?? item.result?.structuredContent),
        error: item.error?.message,
      };
    case 'dynamicToolCall':
      return { input: activityPayload(item.arguments), output: activityPayload(item.contentItems) };
    case 'commandExecution':
      return { input: activityPayload(item.command), output: activityPayload(item.aggregatedOutput), cwd: item.cwd };
    case 'fileChange':
      return {
        input: Array.isArray(item.changes) ? item.changes.map((change) => `${change?.kind?.type ?? change?.kind ?? ''} ${change?.path ?? ''}`.trim()).join('\n') : undefined,
        output: Array.isArray(item.changes) ? item.changes.map((change) => change?.diff ?? '').join('\n') : undefined,
      };
    case 'webSearch':
      return { input: activityPayload(item.query), output: activityPayload(item.results) };
    case 'imageView':
      return { input: activityPayload(item.path) };
    case 'imageGeneration':
      return { input: activityPayload(item.revisedPrompt), output: activityPayload(item.savedPath), error: activityPayload(item.failure) };
    case 'collabAgentToolCall':
      return { input: activityPayload(item.prompt) };
    default:
      return {};
  }
}

function definedFields(fields) {
  return Object.fromEntries(Object.entries(fields).filter(([, value]) => value !== undefined && value !== null && value !== ''));
}

function completedToolActivity(item) {
  if (!item || typeof item !== 'object') return null;
  const durationMs = Number.isFinite(item.durationMs) ? Math.max(0, item.durationMs) : undefined;
  const failed = item.status === 'failed' || item.status === 'declined' || Boolean(item.error);
  const common = (itemType, toolLabel, extra = {}) => ({
    kind: 'agent.tool',
    summary: toolLabel,
    status: failed ? 'FAILED' : 'COMPLETED',
    metadata: {
      itemType,
      toolLabel,
      toolStatus: cleanToolPart(item.status, failed ? 'failed' : 'completed'),
      ...(durationMs === undefined ? {} : { durationMs }),
      ...definedFields({ toolId: item.id, ...toolPayloads(item) }),
      ...extra,
    },
  });

  if (item.type === 'mcpToolCall') {
    const toolServer = cleanToolPart(item.server, 'mcp');
    const toolName = cleanToolPart(item.tool, 'tool');
    return common('mcpToolCall', `${toolServer} · ${toolName}`, { toolName, toolServer });
  }

  if (item.type === 'dynamicToolCall') {
    const toolName = cleanToolPart(item.tool, 'tool');
    const namespace = cleanToolPart(item.namespace, 'dynamic');
    return common('dynamicToolCall', `${namespace} · ${toolName}`, {
      toolName,
      toolServer: namespace,
    });
  }

  if (item.type === 'commandExecution') {
    const actionName = item.commandActions?.find((action) => action?.name)?.name;
    const toolName = cleanToolPart(actionName, 'shell');
    return common('commandExecution', toolName === 'shell' ? '运行命令' : `运行命令 · ${toolName}`, {
      toolName,
      ...(Number.isInteger(item.exitCode) ? { exitCode: item.exitCode } : {}),
    });
  }

  if (item.type === 'fileChange') {
    const count = Array.isArray(item.changes) ? item.changes.length : 0;
    return common('fileChange', count ? `修改文件 · ${count}` : '修改文件', { toolName: 'file_change' });
  }

  if (item.type === 'webSearch') {
    return common('webSearch', '搜索网页', { toolName: 'web_search' });
  }

  if (item.type === 'imageView') {
    return common('imageView', '查看图片', { toolName: 'image_view' });
  }

  if (item.type === 'imageGeneration') {
    return common('imageGeneration', '生成图片', { toolName: 'image_generation' });
  }

  if (item.type === 'collabAgentToolCall') {
    return common('collabAgentToolCall', '协作 Agent', {
      toolName: cleanToolPart(typeof item.tool === 'string' ? item.tool : item.tool?.type, 'collaboration'),
    });
  }

  return null;
}

function delay(ms) {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    timer.unref?.();
  });
}

function tomlString(value) {
  return JSON.stringify(value);
}

export function sanitizeCodexEnv(environment, profile = '', actions = null, config = {}) {
  return sanitizeAgentChildEnv(environment, { profile, actions, cliBins: agentCliCommands(config).map(({ bin }) => bin) });
}

// The daemon is a thin pipe: inbound lines are fed to one persistent native
// app-server thread via turn/start (idle) or turn/steer (mid-turn). The runtime
// delivers through the daemon's agent tools (MCP). This class owns the
// app-server process, the JSON-RPC handshake, the turn/steer state machine,
// and reconnect-with-resume.
export class CodexAgent {
  constructor(config, store, logger, options = {}) {
    this.config = config;
    this.store = store;
    this.logger = logger;
    this._spawn = options.spawn ?? nodeSpawn;
    const runtime = getRuntimeDefinition(options.runtimeId ?? CODEX_RUNTIME.id);
    if (!runtime) throw new Error(`unknown app-server runtime: ${options.runtimeId}`);
    this.runtime = runtime.id;
    this.capabilities = runtime.capabilities;
    this.effortControl = this.capabilities?.effortControl ?? 'unsupported';
    this.supportsEffort = this.effortControl === 'selectable';
    this.turnStartEffort = this.effortControl === 'selectable';
    this.runtimeLabel = options.runtimeLabel ?? runtime.id;
    this.logPrefix = options.logPrefix ?? runtime.id;
    this.binConfigKey = options.binConfigKey ?? 'codexBin';
    this.effortConfigKey = options.effortConfigKey ?? (this.supportsEffort ? `${runtime.id}Effort` : '');
    this.modelSettingKey = options.modelSettingKey ?? `${runtime.id}_model`;
    this.effortSettingKey = options.effortSettingKey ?? (this.supportsEffort ? `${runtime.id}_effort` : '');
    this.appServerConfigArgs = options.appServerConfigArgs ?? CODEX_APP_SERVER_CONFIG_ARGS;
    this._sanitizeEnv = options.sanitizeEnv ?? sanitizeCodexEnv;
    this.policyVersion = options.policyVersion ?? 'steer-v2';
    this.reconnectBaseMs = options.reconnectBaseMs ?? 1000;
    this.reconnectCapMs = options.reconnectCapMs ?? 30_000;
    this.handshakeTimeoutMs = options.handshakeTimeoutMs ?? 15_000;
    this.onTurnFailed = options.onTurnFailed ?? null; // ({ inboundSeqs, error }) => void
    this.onTurnSucceeded = options.onTurnSucceeded ?? null; // ({ inboundSeqs }) => void
    this.onTurnAbandoned = options.onTurnAbandoned ?? null; // ({ inboundSeqs }) => void
    this.onAvailable = options.onAvailable ?? null;   // () => void
    const cognition = store.getCognitionState?.()
      ?? { runtime: this.runtime, epoch: 1, authorityToken: null };
    this.cognitionEpoch = options.cognitionEpoch ?? cognition.epoch;
    this.authorityToken = options.authorityToken ?? cognition.authorityToken ?? '';
    this.sessionPersistence = options.sessionPersistence !== false;
    this.allowResume = options.allowResume !== false;
    this.syncedThroughSeq = options.syncedThroughSeq ?? 0;

    this.systemPrompt = assembleConversationPolicy(config);
    this.developerInstructionsText = this.systemPrompt;
    // Persisted slash-command settings win on normal startup. Provider switch
    // paths pass options.model deliberately, while first boot only passes
    // options.initialModel so a saved setting cannot be overwritten by env.
    this.model = options.model
      ?? store.getSetting?.(this.modelSettingKey)
      ?? options.initialModel
      ?? '';
    this.effort = this.supportsEffort
      ? options.effort ?? store.getSetting?.(this.effortSettingKey) ?? config[this.effortConfigKey] ?? 'medium'
      : '';

    this.child = null;
    this.threadId = null;
    this.storedThreadId = null;
    this.activeTurnId = null;
    this.startingTurn = false;      // turn/start sent, turn/started not yet observed
    this.turnStartedAtMs = null;
    this.modelWorking = false;
    this.modelStartedAtMs = null;
    this.startingItems = [];         // inbound items waiting for turn/started
    this.startingJournalThroughSeq = 0; // offered cursor, promoted on native acceptance
    this.startingOwner = null;       // immutable session authority for turn/start
    this.activeOwner = null;         // immutable session authority for the live turn
    this.activeInboundSeqs = [];     // inbound rows associated with the live app-server turn
    this.activeReceiptIds = [];      // opaque non-DB receipts associated with the live turn
    this.needsHistoryPrefix = false; // fresh thread: prepend history to the first turn
    this.queue = [];                 // texts waiting for a live idle thread
    this.pending = new Map();        // rpc id -> { resolve, reject }
    this.nextId = 0;
    this.nextQueuedBatchId = 0;
    this.stdoutBuffer = '';
    this.stopping = false;
    this.startedOk = false;
    this.reconnecting = false;
    this.backoffMs = this.reconnectBaseMs;
    this.drainTimer = null;
    this.activitySeq = 0;
    this.idleWaiters = [];
    this.primeWaiter = null;
    this.sessionRotation = null;
    this.forceFreshThread = false;
  }

  // One global thread is shared across every chat; policyHash only detects when the
  // instructions/workspace change so a stored thread is not resumed under a different
  // policy. Model/effort are deliberately not part of the hash: they are per-turn
  // overrides now, and switching them must not orphan the stored thread.
  get policyHash() {
    return createHash('sha256')
      .update(this.developerInstructionsText).update('\0')
      .update(this.config.workspace).update('\0')
      .update(this.policyVersion).digest('hex');
  }

  get state() {
    return {
      connected: Boolean(this.child && this.threadId),
      activeTurn: this.activeTurnId ? 1 : 0,
      queued: this.queue.length,
    };
  }

  get turnInProgress() {
    return Boolean(this.activeTurnId || this.startingTurn);
  }

  async start() {
    this.stopping = false;
    await this._handshake();
  }

  get sessionDescriptor() {
    return {
      runtime: this.runtime,
      sessionId: this.threadId,
      policyHash: this.policyHash,
      epoch: this.cognitionEpoch,
      syncedThroughSeq: this.syncedThroughSeq,
    };
  }

  activateSession() {
    this.sessionPersistence = true;
  }

  whenIdle() {
    if (!this.turnInProgress && this.queue.length === 0) return Promise.resolve();
    return new Promise((resolve) => this.idleWaiters.push(resolve));
  }

  // ---- outbound state machine -------------------------------------------------

  deliver(text, seq = null, receiptId = null) {
    return this.deliverBatch([deliveryItem({ text, seq, receiptId })]);
  }

  // Starts one turn atomically when idle. The burst router uses this to keep
  // same-conversation corrections in one prompt instead of turning the later
  // lines into steer calls during the turn/start acknowledgement window.
  deliverBatch(items) {
    if (this.stopping) return false;
    if (!Array.isArray(items) || items.length === 0) return false;
    const batch = items.map(deliveryItem);
    if (!this.child || !this.threadId) { this._enqueueBatch(batch); return true; }
    if (this.activeTurnId) {
      this._steerBatch(batch);
      return true;
    }
    if (this.startingTurn) { this._enqueueBatch(batch); return true; }
    if (this.queue.length > 0) {
      this._enqueueBatch(batch);
      this._drain();
      return true;
    }
    this._claimBatch(batch);
    return true;
  }

  // Atomic transport hand-off: a queued group burst may start only when no older
  // runtime-owned work exists. A true result means this agent owns every item.
  tryStartBatch(items) {
    if (this.stopping
      || !this.child
      || !this.threadId
      || this.turnInProgress
      || this.queue.length > 0
      || !Array.isArray(items)
      || items.length === 0) return false;
    const batch = items.map(deliveryItem);
    this._claimBatch(batch);
    return true;
  }

  _claimBatch(batch) {
    try {
      this._startTurn(batch);
    } catch (error) {
      if (error instanceof CognitionLimitError) {
        const inboundSeqs = batch.map((item) => item.seq).filter((seq) => seq != null);
        const receipts = receiptIds(batch);
        this.logger.warn(`${this.logPrefix}_cognition_limit`, { error: safe(error) });
        try {
          this.onTurnFailed?.(withReceiptIds({ inboundSeqs, error: safe(error) }, receipts));
        } catch {}
        // Let the submitting router release this batch before it can drain again.
        queueMicrotask(() => this._notifyAvailable());
        return;
      }
      // Ownership has already crossed the transport boundary. Keep the whole
      // batch in the runtime queue and retry; never leave half of it behind.
      this.logger.warn(`${this.logPrefix}_turn_start_sync_failed`, { error: safe(error) });
      this._enqueueBatch(batch, { front: true });
      this._scheduleDrain();
    }
  }

  _enqueueBatch(batch, { front = false } = {}) {
    const queuedBatchId = batch.length < 2 ? null : ++this.nextQueuedBatchId;
    const queued = queuedBatchId === null
      ? batch
      : batch.map((item) => ({ ...item, queuedBatchId }));
    if (front) this.queue.unshift(...queued);
    else this.queue.push(...queued);
  }

  _startTurn(items, { prime = false } = {}) {
    const text = items.map((item) => item.text).join('\n');
    let body = text;
    this.startingInput = { input: text, historyChars: 0 };
    const hadPrefix = this.needsHistoryPrefix;
    let journalThroughSeq = this.syncedThroughSeq;
    if (!prime) {
      const seqs = items.map((item) => item.seq).filter((seq) => seq != null);
      const beforeSeq = seqs.length > 0 ? Math.min(...seqs) : null;
      const throughSeq = this.store.cognitionJournalBeforeInbound?.(beforeSeq, this.cognitionEpoch) ?? 0;
      // Native transcripts already own their history, even when the cursor is zero.
      const needsCatchup = hadPrefix;
      if (needsCatchup) {
        const catchup = cognitionHistoryPrefix(
          this.store,
          this.cognitionEpoch,
          throughSeq,
          hadPrefix ? 0 : this.syncedThroughSeq,
        );
        journalThroughSeq = catchup.throughSeq;
        if (catchup.text) {
          body = `${catchup.text}\n${text}`;
          this.startingInput.historyChars = catchup.text.length;
        }
      }
      this.needsHistoryPrefix = false;
    }
    this.turnStartedAtMs = Date.now();
    this.modelWorking = true;
    this.modelStartedAtMs = this.turnStartedAtMs;
    this.startingTurn = true;
    this.startingItems = items.map(deliveryItem);
    this.startingJournalThroughSeq = journalThroughSeq;
    this.startingOwner = Object.freeze({
      runtime: this.runtime,
      authorityToken: this.authorityToken,
      sessionId: this.threadId,
      epoch: this.cognitionEpoch,
    });
    // Model rides on every turn/start: thread/resume does not re-apply its
    // config to an already-running thread. Effort follows the same path and
    // takes effect on the next turn without replacing the thread.
    const params = {
      threadId: this.threadId,
      input: [{ type: 'text', text: body }],
      ...(this.model ? { model: this.model } : {}),
      ...(this.turnStartEffort ? { effort: this.effort || null } : {}),
    };
    this._request('turn/start', params).catch((error) => {
      this.startingTurn = false;
      this.turnStartedAtMs = null;
      this.modelWorking = false;
      this.modelStartedAtMs = null;
      this.startingItems = [];
      this.startingJournalThroughSeq = 0;
      this.startingOwner = null;
      if (hadPrefix) this.needsHistoryPrefix = true;
      this.logger.warn(`${this.logPrefix}_turn_start_failed`, { error: safe(error) });
      if (prime && this.primeWaiter) {
        const waiter = this.primeWaiter;
        this.primeWaiter = null;
        waiter.reject(error);
        this._notifyAvailable();
      } else {
        this._enqueueBatch(items, { front: true }); // never drop a message
        this._scheduleDrain();
      }
    });
  }

  _steer(text, seq = null, receiptId = null) {
    this._steerBatch([deliveryItem({ text, seq, receiptId })]);
  }

  _steerBatch(items) {
    const expectedTurnId = this.activeTurnId;
    const owner = this.activeOwner;
    const text = items.map((item) => item.text).join('\n');
    const seqs = [...new Set(
      items.map((item) => item.seq).filter((seq) => seq !== null && seq !== undefined),
    )];
    const receipts = receiptIds(items);
    this._request('turn/steer', {
      threadId: this.threadId,
      expectedTurnId,
      input: [{ type: 'text', text }],
    }).then(() => {
      for (const seq of seqs) {
        if (!this.activeInboundSeqs.includes(seq)) this.activeInboundSeqs.push(seq);
      }
      for (const receiptId of receipts) {
        if (!this.activeReceiptIds.includes(receiptId)) this.activeReceiptIds.push(receiptId);
      }
      this._acceptJournal(owner, 0, seqs);
      this._recordActivity('agent.steered', {
        turnId: expectedTurnId,
        inboundSeq: seqs.at(-1) ?? null,
        summary: text,
        metadata: { runtime: this.runtime, input: text, inboundSeqs: [...this.activeInboundSeqs] },
      });
    }).catch((error) => {
      // Race: the turn ended just as we steered. Re-deliver so the message is not lost.
      this.logger.info(`${this.logPrefix}_steer_fell_back`, { error: safe(error) });
      this.deliverBatch(items);
    });
  }

  // turn/started: the in-flight turn/start is live; steer whatever queued meanwhile.
  _onTurnStarted(turnId) {
    const owner = this.startingOwner;
    const journalThroughSeq = this.startingJournalThroughSeq;
    this.startingTurn = false;
    this.activeTurnId = turnId ?? null;
    this.activeOwner = owner;
    this.activeInboundSeqs = [...new Set(
      this.startingItems.map((item) => item.seq).filter((seq) => seq !== null && seq !== undefined),
    )];
    this.activeReceiptIds = receiptIds(this.startingItems);
    this._acceptJournal(owner, journalThroughSeq, this.activeInboundSeqs);
    this.startingItems = [];
    this.startingJournalThroughSeq = 0;
    this.startingOwner = null;
    this.turnUsage = null;
    this._recordActivity('agent.turn_started', {
      turnId: this.activeTurnId,
      inboundSeq: this.activeInboundSeqs.at(-1) ?? null,
      metadata: {
        runtime: this.runtime,
        model: this.model ?? undefined,
        effort: this.effort ?? undefined,
        ...(this.startingInput ?? {}),
        inboundSeqs: [...this.activeInboundSeqs],
      },
    });
    this.startingInput = null;
    if (!this.activeTurnId) return;
    const items = this.queue.splice(0);
    for (let index = 0; index < items.length;) {
      const queuedBatchId = items[index].queuedBatchId;
      if (queuedBatchId === undefined) {
        this._steer(items[index].text, items[index].seq, items[index].receiptId);
        index += 1;
        continue;
      }
      let end = index + 1;
      while (items[end]?.queuedBatchId === queuedBatchId) end += 1;
      this._steerBatch(items.slice(index, end));
      index = end;
    }
  }

  _associatedInboundSeqs() {
    if (this.activeInboundSeqs.length > 0) return [...this.activeInboundSeqs];
    return [...new Set(
      this.startingItems.map((item) => item.seq).filter((seq) => seq !== null && seq !== undefined),
    )];
  }

  _associatedReceiptIds() {
    if (this.activeReceiptIds.length > 0) return [...this.activeReceiptIds];
    return receiptIds(this.startingItems);
  }

  // turn/completed: idle again. Anything still queued (e.g. a very fast turn that never
  // emitted turn/started) is merged into one fresh turn/start.
  _onTurnCompleted(status, error, completedTurnId = null) {
    const turnId = this.activeTurnId ?? completedTurnId;
    const turnStartedAtMs = this.turnStartedAtMs;
    const inboundSeqs = this._associatedInboundSeqs();
    const receipts = this._associatedReceiptIds();
    if (!this.activeOwner) {
      // Some app-server versions may complete a very fast turn without a
      // turn/started notification. Completion still proves the input entered
      // the native thread, even when the turn itself failed.
      this._acceptJournal(this.startingOwner, this.startingJournalThroughSeq, inboundSeqs);
    }
    this.activeTurnId = null;
    this.activeOwner = null;
    this.startingTurn = false;
    this.turnStartedAtMs = null;
    this.modelWorking = false;
    this.modelStartedAtMs = null;
    const failed = status === 'failed';
    const errorText = error ? turnErrorText(error) : '';
    this._recordActivity('agent.turn_completed', {
      turnId,
      inboundSeq: inboundSeqs.at(-1) ?? null,
      status,
      summary: failed ? errorText : undefined,
      metadata: {
        runtime: this.runtime,
        status,
        error: errorText || undefined,
        inboundSeqs,
        ...(turnStartedAtMs ? { durationMs: Date.now() - turnStartedAtMs } : {}),
        ...(this.turnUsage ?? {}),
      },
    });
    this.turnUsage = null;
    this.activeInboundSeqs = [];
    this.activeReceiptIds = [];
    this.startingItems = [];
    this.startingJournalThroughSeq = 0;
    this.startingOwner = null;
    const wasPrime = Boolean(this.primeWaiter);
    if (this.primeWaiter) {
      const waiter = this.primeWaiter;
      this.primeWaiter = null;
      if (failed) waiter.reject(new Error(errorText || `${this.runtimeLabel} cognition catch-up failed`));
      else {
        this.syncedThroughSeq = waiter.syncedThroughSeq;
        waiter.resolve();
      }
    }
    if (failed && !wasPrime && this.onTurnFailed) {
      try {
        this.onTurnFailed(withReceiptIds(
          { inboundSeqs, error: errorText || '未知错误' },
          receipts,
        ));
      } catch (callbackError) {
        this.logger.warn('turn_failure_notify_failed', { error: safe(callbackError) });
      }
    }
    if (!failed && !wasPrime && this.onTurnSucceeded) {
      try {
        this.onTurnSucceeded(withReceiptIds({ inboundSeqs }, receipts));
      } catch (callbackError) {
        this.logger.warn('turn_success_notify_failed', { error: safe(callbackError) });
      }
    }
    this._drain();
    this._notifyAvailable();
  }

  _acceptJournal(owner, journalThroughSeq, inboundSeqs = []) {
    if (!owner?.sessionId) return false;
    let acceptedThroughSeq = Number.isSafeInteger(journalThroughSeq) ? journalThroughSeq : 0;
    for (const seq of inboundSeqs) {
      acceptedThroughSeq = Math.max(
        acceptedThroughSeq,
        this.store.cognitionJournalSeqForInbound?.(seq, owner.epoch) ?? 0,
      );
    }
    let accepted = true;
    if (this.sessionPersistence && this.store.advanceOwnedSessionAcceptance) {
      accepted = this.store.advanceOwnedSessionAcceptance({ ...owner, acceptedThroughSeq });
    }
    const stillCurrent = owner.runtime === this.runtime
      && owner.authorityToken === this.authorityToken
      && owner.sessionId === this.threadId
      && owner.epoch === this.cognitionEpoch;
    if (!accepted || !stillCurrent) {
      this.logger.info(`${this.logPrefix}_session_acceptance_stale`, { acceptedThroughSeq });
      return false;
    }
    this.syncedThroughSeq = Math.max(this.syncedThroughSeq, acceptedThroughSeq);
    return true;
  }

  // Start a turn with everything queued, if the connection is idle.
  _drain() {
    if (this.stopping || !this.child || !this.threadId) return;
    if (this.activeTurnId || this.startingTurn) return;
    if (this.queue.length === 0) return;
    this._claimBatch(this.queue.splice(0));
  }

  _scheduleDrain() {
    if (this.drainTimer) return;
    this.drainTimer = setTimeout(() => {
      this.drainTimer = null;
      this._drain();
      this._notifyAvailable();
    }, this.reconnectBaseMs);
    this.drainTimer.unref?.();
  }

  _flushQueue() {
    const items = this.queue.splice(0);
    for (let index = 0; index < items.length;) {
      const queuedBatchId = items[index].queuedBatchId;
      if (queuedBatchId === undefined) {
        this.deliver(items[index].text, items[index].seq, items[index].receiptId);
        index += 1;
        continue;
      }
      let end = index + 1;
      while (items[end]?.queuedBatchId === queuedBatchId) end += 1;
      this.deliverBatch(items.slice(index, end));
      index = end;
    }
  }

  // ---- JSON-RPC wire ----------------------------------------------------------

  _encodeRequest(method, params) {
    const id = ++this.nextId;
    return { id, line: `${JSON.stringify({ id, method, params })}\n` };
  }

  _request(method, params) {
    const { id, line } = this._encodeRequest(method, params);
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      try {
        this.child.stdin.write(line);
      } catch (error) {
        this.pending.delete(id);
        reject(error);
      }
    });
  }

  _notify(method, params) {
    try {
      this.child?.stdin.write(`${JSON.stringify({ method, params })}\n`);
    } catch { /* connection lost; handled by exit */ }
  }

  // Server requests have both id and method. They must get a response even in
  // unattended mode; treating them as notifications strands the server's turn.
  _handleLine(line) {
    let msg;
    try { msg = JSON.parse(line); } catch { return; }
    if (!msg || typeof msg !== 'object') return;
    if (msg.id !== undefined && typeof msg.method === 'string') {
      this._handleServerRequest(msg);
      return;
    }
    if (msg.id !== undefined && msg.method === undefined) {
      const pending = this.pending.get(msg.id);
      if (!pending) return;
      this.pending.delete(msg.id);
      if (msg.error) pending.reject(new Error(msg.error.message || `${this.runtimeLabel} rpc error`));
      else pending.resolve(msg.result);
      return;
    }
    if (typeof msg.method === 'string') this._handleNotification(msg.method, msg.params || {});
  }

  // approvalPolicy 'never' already makes Codex reject approvals, permission
  // requests and MCP elicitations itself; this covers anything that still
  // arrives. Each denial lets the turn continue, so the model can still reply.
  _handleServerRequest({ id, method }) {
    let result;
    switch (method) {
      case 'item/commandExecution/requestApproval':
      case 'item/fileChange/requestApproval':
        result = { decision: 'decline' };
        break;
      case 'execCommandApproval':
      case 'applyPatchApproval':
        result = { decision: 'denied' };
        break;
      case 'item/permissions/requestApproval':
        result = { permissions: {}, scope: 'turn' };
        break;
      case 'item/tool/requestUserInput':
        // An empty answer map means no answer, never an invented user choice.
        result = { answers: {} };
        break;
      case 'mcpServer/elicitation/request':
        result = { action: 'cancel', content: null };
        break;
      default:
        break;
    }
    const response = result === undefined
      ? { id, error: { code: -32601, message: 'This unattended daemon does not support this server request.' } }
      : { id, result };
    this.logger.warn(`${this.logPrefix}_server_request_declined`, { method: safe(method), reason: 'unattended' });
    this._recordActivity('agent.interaction_declined', {
      summary: safe(method), metadata: { runtime: this.runtime, method: safe(method) },
    });
    try { this.child?.stdin.write(`${JSON.stringify(response)}\n`); } catch (error) {
      this.logger.warn(`${this.logPrefix}_server_response_failed`, { error: safe(error) });
    }
  }

  _handleNotification(method, params) {
    // After a session rotate, the abandoned thread's dying turn may still emit
    // notifications; never let them corrupt the new thread's turn state.
    const threadId = params.threadId ?? params.turn?.threadId ?? null;
    if (threadId && this.threadId && threadId !== this.threadId) return;
    switch (method) {
      case 'turn/started':
        this._onTurnStarted(params.turn?.id ?? params.turnId ?? null);
        break;
      case 'turn/completed':
        this._onTurnCompleted(
          params.turn?.status ?? params.status,
          params.turn?.error ?? params.error,
          params.turn?.id ?? params.turnId ?? null,
        );
        break;
      case 'item/started': {
        const type = params.item?.type;
        const modelOutput = ['reasoning', 'agentMessage', 'agent_message'].includes(type);
        this.modelWorking = modelOutput;
        this.modelStartedAtMs = modelOutput ? Date.now() : null;
        if (TOOL_ITEM_TYPES.has(type)) {
          // Running tools are visible before they finish; completion reuses toolId.
          const started = completedToolActivity({ ...params.item, status: 'inProgress', error: null });
          this._recordActivity('agent.tool_called', {
            turnId: params.turnId ?? this.activeTurnId,
            inboundSeq: this._associatedInboundSeqs().at(-1) ?? null,
            summary: started.metadata.toolLabel,
            status: 'RUNNING',
            metadata: { runtime: this.runtime, ...started.metadata },
          });
        }
        break;
      }
      case 'thread/tokenUsage/updated': {
        const last = params.tokenUsage?.last;
        if (last) {
          this.turnUsage = definedFields({
            inputTokens: last.inputTokens,
            cachedInputTokens: last.cachedInputTokens,
            outputTokens: last.outputTokens,
            reasoningTokens: last.reasoningOutputTokens,
            contextWindow: params.tokenUsage.modelContextWindow,
          });
        }
        break;
      }
      case 'item/completed': {
        const item = params.item;
        if (['reasoning', 'agentMessage', 'agent_message'].includes(item?.type)) {
          this.modelWorking = false;
          this.modelStartedAtMs = null;
        }
        const text = item?.text ?? item?.message?.text;
        const associatedInboundSeqs = this._associatedInboundSeqs();
        if (item?.type === 'reasoning') {
          const thinking = [...(item.summary ?? []), ...(item.content ?? [])]
            .filter((part) => typeof part === 'string').join('\n').trim();
          this._recordActivity('agent.thinking', {
            turnId: params.turnId ?? this.activeTurnId,
            inboundSeq: associatedInboundSeqs.at(-1) ?? null,
            metadata: { runtime: this.runtime, ...(thinking ? { thinking } : {}) },
          });
          break;
        }
        if ((item?.type === 'agentMessage' || item?.type === 'agent_message') && text) {
          this._recordActivity('agent.note', {
            turnId: params.turnId ?? this.activeTurnId,
            inboundSeq: associatedInboundSeqs.at(-1) ?? null,
            summary: text,
            metadata: { runtime: this.runtime, inboundSeqs: associatedInboundSeqs },
          });
          break;
        }
        const tool = completedToolActivity(item);
        if (tool) {
          this._recordActivity(tool.kind, {
            turnId: params.turnId ?? this.activeTurnId,
            inboundSeq: associatedInboundSeqs.at(-1) ?? null,
            summary: tool.summary,
            status: tool.status,
            metadata: { runtime: this.runtime, ...tool.metadata, inboundSeqs: associatedInboundSeqs },
          });
        }
        break;
      }
      default: // thread/started, mcpServer/*, hook/*, thread/*, account/*, item/* — ignore
        break;
    }
  }

  _onStdout(chunk) {
    this.stdoutBuffer += chunk;
    let index;
    while ((index = this.stdoutBuffer.indexOf('\n')) >= 0) {
      const line = this.stdoutBuffer.slice(0, index);
      this.stdoutBuffer = this.stdoutBuffer.slice(index + 1);
      if (line.trim()) {
        try { this._handleLine(line); } catch (error) {
          this.logger.warn(`${this.logPrefix}_line_error`, { error: safe(error) });
        }
      }
    }
  }

  // ---- connection lifecycle ---------------------------------------------------

  async _handshake() {
    // Fetch before starting the app-server handshake deadline. Network/archive
    // work must not consume the native protocol's handshake timeout.
    const global = this.store.getGlobalSession();
    const resumable = (this.allowResume
      && !this.forceFreshThread
      && global.sessionId
      && global.sessionReady !== false
      && global.policyHash === this.policyHash
      && (global.runtime ?? CODEX_RUNTIME.id) === this.runtime
      && (global.epoch || this.cognitionEpoch) === this.cognitionEpoch);
    if (resumable) this.syncedThroughSeq = global.syncedThroughSeq ?? this.syncedThroughSeq;
    this.storedThreadId = resumable ? global.sessionId : null;

    if (!resumable) await this.store.prepareOVContext?.(this.cognitionEpoch);
    return new Promise((resolve, reject) => {

      const args = [
        'app-server', '--listen', 'stdio://',
        '-c', `developer_instructions=${tomlString(this.developerInstructionsText)}`,
        ...this.appServerConfigArgs,
      ];
      // The daemon's agent tools ride along as an MCP stdio adapter to the Core.
      const tools = agentToolServer(this.config, this.authorityToken);
      args.push(
        '-c', `mcp_servers.personal_agent.command=${tomlString(tools.command)}`,
        '-c', `mcp_servers.personal_agent.args=${JSON.stringify(tools.args)}`,
        '-c', 'mcp_servers.personal_agent.startup_timeout_sec=10',
        '-c', `mcp_servers.personal_agent.tool_timeout_sec=${Math.max(10, Math.ceil(this.config.sendTimeoutMs / 1_000) + 5)}`,
      );
      let child;
      try {
        child = this._spawn(this.config[this.binConfigKey], args, {
          cwd: this.config.workspace,
          env: this._sanitizeEnv(process.env, this.config.profile, agentToolEnv(this.config, this.authorityToken), this.config),
          shell: false,
          detached: process.platform !== 'win32',
          stdio: ['pipe', 'pipe', 'pipe'],
        });
      } catch (error) { reject(error); return; }

      this.child = child;
      this.threadId = null;
      this.activeTurnId = null;
      this.startingTurn = false;
      this.modelWorking = false;
      this.modelStartedAtMs = null;
      this.stdoutBuffer = '';
      this.pending.clear();
      this.nextId = 0;

      const stderr = captureStartupStderr(child.stderr);
      let settled = false;
      const timer = setTimeout(() => finish(new Error(`${this.runtimeLabel} handshake timed out`)), this.handshakeTimeoutMs);
      timer.unref?.();
      const finish = (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        const diagnostic = stderr.text();
        stderr.stop();
        if (!error) { resolve(); return; }
        // A handshake can fail while the child is still alive (timeout, rpc error
        // response); detach and kill it so a retry does not orphan a live runtime.
        this._handleExit(child);
        if (child.exitCode === null) terminateProcessTree(child, 'SIGKILL');
        const hint = this.runtime === 'traex' ? '; requires a TraeX with app-server support' : '';
        reject(new Error(`${safe(error)}${hint}${diagnostic ? `; stderr: ${diagnostic}` : ''}`));
      };

      child.stdin?.on('error', () => {});
      child.stdout?.setEncoding('utf8');
      child.stdout?.on('data', (chunk) => { if (this.child === child) this._onStdout(chunk); });
      child.once('error', (error) => { this._handleExit(child); finish(error); });
      child.once('exit', (code, signal) => {
        this._handleExit(child, code, signal);
        finish(new Error(`${this.runtimeLabel} exited during handshake (code ${code}, signal ${signal})`));
      });

      this._request('initialize', {
        clientInfo: { name: 'vika', version: '1' },
        capabilities: { experimentalApi: true },
      }).then(() => {
        this._notify('initialized', {});
        return this._startOrResumeThread();
      }).then(() => {
        this.startedOk = true;
        this.backoffMs = this.reconnectBaseMs;
        this._publishSession();
        this._flushQueue();
        this._notifyAvailable();
        finish();
      }).catch(finish);
    });
  }

  _threadParams() {
    return {
      cwd: this.config.workspace,
      approvalPolicy: 'never',
      sandbox: 'danger-full-access',
      ...(this.turnStartEffort && this.effort ? { config: { model_reasoning_effort: this.effort } } : {}),
      ...(this.model ? { model: this.model } : {}),
    };
  }

  async _startOrResumeThread() {
    const params = this._threadParams();
    if (this.storedThreadId) {
      try {
        const result = await this._request('thread/resume', { ...params, threadId: this.storedThreadId });
        this.sessionModel = result?.model ?? '';
        this.threadId = result?.thread?.id ?? this.storedThreadId;
        this.needsHistoryPrefix = false; // app-server retains the resumed thread's context
        this.forceFreshThread = false;
        return;
      } catch (error) {
        this.logger.warn(`${this.logPrefix}_resume_failed_fresh_start`, { error: safe(error) });
        await this.store.prepareOVContext?.(this.cognitionEpoch);
      }
    }
    const result = await this._request('thread/start', params);
    this.sessionModel = result?.model ?? '';
    this.threadId = result?.thread?.id;
    if (!this.threadId) throw new Error('thread/start returned no thread id');
    // A fresh native transcript has accepted none of the OV context yet. The first
    // real turn deliberately catches up from zero and advances this cursor when
    // turn/started (or turn/completed) proves acceptance.
    this.syncedThroughSeq = 0;
    this.needsHistoryPrefix = true;
    this.forceFreshThread = false;
  }

  // Prepare a fresh thread without publishing it as the active thread. The
  // app-server RPC is the only fallible step; coordinator Store state, queued
  // work and the live thread remain untouched until commitSessionRotation.
  async prepareSessionRotation() {
    if (this.turnInProgress) throw new Error(`${this.runtimeLabel} session rotation requires an idle runtime`);
    if (this.sessionRotation) throw new Error(`${this.runtimeLabel} session rotation is already prepared`);
    if (this.child && !this.threadId) {
      throw new Error(`${this.runtimeLabel} connection is still starting`);
    }
    if (!this.child) {
      // Preserve /new while a reconnect is already in progress. The placeholder
      // is explicitly not resumable; the next handshake replaces it with the
      // real app-server thread in the newly committed epoch.
      const id = `pending-${randomUUID()}`;
      this.sessionRotation = Object.freeze({ id, deferred: true });
      return Object.freeze({ id, sessionReady: false, deferred: true });
    }
    const result = await this._request('thread/start', this._threadParams());
    const id = result?.thread?.id;
    if (!id) throw new Error('thread/start returned no thread id');
    this.sessionRotation = Object.freeze({ id, model: result?.model ?? '' });
    return Object.freeze({ id, sessionReady: true });
  }

  commitSessionRotation(prepared, { persist = true, preserveContext = false } = {}) {
    if (!prepared?.id || this.sessionRotation?.id !== prepared.id) {
      throw new Error(`prepared ${this.runtimeLabel} session is no longer current`);
    }
    this.threadId = prepared.deferred ? null : prepared.id;
    this.sessionModel = this.sessionRotation.model ?? '';
    this.storedThreadId = null;
    this.needsHistoryPrefix = preserveContext;
    this.sessionRotation = null;
    if (persist) this._publishSession();
    this._recordActivity('agent.session_rotated', {
      summary: '已开启新会话',
      metadata: { resume: false },
    });
    this._drain();
    this._notifyAvailable();
    return prepared.deferred ? null : this.threadId;
  }

  cancelSessionRotation() {
    // The app-server has created an empty, unreachable thread only. Dropping the local
    // candidate restores the exact pre-prepare state; no live thread was swapped.
    this.sessionRotation = null;
  }

  // /new and runtime switches must not wait for a stuck turn: best-effort turn/interrupt on the live
  // turn, then abandon local turn state. The abandoned turn's inbound rows are
  // already in the cognition journal and the queue keeps undelivered items, so
  // no message is lost. Queued work is deliberately NOT flushed here — the
  // caller rotates or switches sessions and drains onto the new one.
  async interruptTurn() {
    if (!this.turnInProgress) return;
    const inboundSeqs = this._associatedInboundSeqs();
    const receipts = this._associatedReceiptIds();
    if (this.activeTurnId && this.child && this.threadId) {
      try {
        await this._request('turn/interrupt', {
          threadId: this.threadId,
          turnId: this.activeTurnId,
        });
      } catch { /* best effort; the session is being replaced */ }
    }
    // ponytail: an in-flight turn/start (startingTurn) has no id to interrupt
    // yet; like rotateSession, that turn runs out abandoned.
    this.activeTurnId = null;
    this.startingTurn = false;
    this.modelWorking = false;
    this.modelStartedAtMs = null;
    this.startingItems = [];
    this.startingJournalThroughSeq = 0;
    this.startingOwner = null;
    this.activeOwner = null;
    this.activeInboundSeqs = [];
    this.activeReceiptIds = [];
    if ((inboundSeqs.length > 0 || receipts.length > 0) && this.onTurnAbandoned) {
      try { this.onTurnAbandoned(withReceiptIds({ inboundSeqs }, receipts)); } catch (error) {
        this.logger.warn('turn_abandoned_notify_failed', { error: safe(error) });
      }
    }
    const waiters = this.idleWaiters.splice(0);
    for (const resolve of waiters) resolve();
  }

  // Rebuild the app-server thread without restarting the child (slash commands).
  // resume=true re-opens the current thread under the current model/effort params
  // (context kept, fresh-start fallback inside _startOrResumeThread); resume=false
  // is /new: an empty thread with no history prefix. Returns the new thread id, or
  // null while disconnected (the stored policy/settings apply on reconnect).
  async rotateSession({ resume = false } = {}) {
    if (!resume) {
      this.storedThreadId = null;
      this.store.clearGlobalSession?.();
    }
    if (!this.child || !this.threadId) return null;
    const previousThreadId = this.threadId;
    const abandonedInboundSeqs = this._associatedInboundSeqs();
    const abandonedReceiptIds = this._associatedReceiptIds();
    if (this.activeTurnId) {
      try {
        await this._request('turn/interrupt', {
          threadId: previousThreadId,
          turnId: this.activeTurnId,
        });
      } catch { /* best effort; the thread is being replaced anyway */ }
    }
    // ponytail: a turn/start still in flight (startingTurn) cannot be interrupted by
    // id yet; its turn runs out on the abandoned thread. Window is one RPC ack.
    this.activeTurnId = null;
    this.startingTurn = false;
    this.modelWorking = false;
    this.modelStartedAtMs = null;
    this.startingItems = [];
    this.startingJournalThroughSeq = 0;
    this.startingOwner = null;
    this.activeOwner = null;
    this.activeInboundSeqs = [];
    this.activeReceiptIds = [];
    if ((abandonedInboundSeqs.length > 0 || abandonedReceiptIds.length > 0)
      && this.onTurnAbandoned) {
      try {
        this.onTurnAbandoned(withReceiptIds(
          { inboundSeqs: abandonedInboundSeqs },
          abandonedReceiptIds,
        ));
      } catch (error) {
        this.logger.warn('turn_abandoned_notify_failed', { error: safe(error) });
      }
    }
    this.storedThreadId = resume ? previousThreadId : null;
    this.threadId = null; // deliver() queues while the new thread comes up
    try {
      await this._startOrResumeThread();
    } catch (error) {
      // No usable thread: recycle the child so the reconnect loop rebuilds a clean
      // connection instead of stranding the queue behind a null threadId.
      this.logger.warn(`${this.logPrefix}_rotate_failed`, { error: safe(error) });
      if (this.child) terminateProcessTree(this.child, 'SIGKILL');
      throw error;
    }
    if (!resume) this.needsHistoryPrefix = false; // /new means a clean slate
    this._publishSession();
    this._recordActivity('agent.session_rotated', {
      summary: resume ? '会话参数已更新' : '已开启新会话',
      metadata: { resume },
    });
    this._drain();
    this._notifyAvailable();
    return this.threadId;
  }

  // Model/effort values ride on the next turn/start (see _startTurn) when the
  // runtime supports them. Command policy decides whether to preserve or rotate
  // the thread after updating them.
  setModel(model, { persist = true } = {}) {
    if (persist) this.store.setSetting?.(this.modelSettingKey, model);
    this.model = model;
  }

  get resolvedModel() { return this.model || this.sessionModel || ''; }

  setEffort(effort, { persist = true } = {}) {
    if (!this.supportsEffort) throw new Error(`${this.runtimeLabel} does not support daemon-side effort control`);
    if (persist) this.store.setSetting?.(this.effortSettingKey, effort);
    this.effort = effort;
    return undefined;
  }

  // Visible models from the app-server with their supported reasoning efforts,
  // for /model discovery and /effort validation. Empty while disconnected or
  // when the server lacks model/list.
  async listModels() {
    if (!this.child) return [];
    try {
      const result = await this._request('model/list', {});
      const models = Array.isArray(result?.data) ? result.data : [];
      return models
        .filter((model) => model && !model.hidden && typeof model.id === 'string')
        .map((model) => ({
          id: model.id,
          ...(model.model ? { resolvedModel: model.model } : {}),
          ...(model.displayName ? { displayName: model.displayName } : {}),
          ...(model.isDefault ? { isDefault: true } : {}),
          ...(model.defaultReasoningEffort ? { defaultEffort: model.defaultReasoningEffort } : {}),
          effortsKnown: Array.isArray(model.supportedReasoningEfforts),
          efforts: this.supportsEffort
            ? (Array.isArray(model.supportedReasoningEfforts) ? model.supportedReasoningEfforts : [])
              .map((effort) => (typeof effort === 'string' ? effort : effort?.reasoningEffort))
              .filter((effort) => typeof effort === 'string' && effort)
            : [],
        }));
    } catch (error) {
      this.logger.info(`${this.logPrefix}_model_list_failed`, { error: safe(error) });
      return [];
    }
  }

  _handleExit(child, code, signal) {
    if (this.child !== child) return; // a stale child from a prior connection
    // An unacknowledged turn/start rejects below and its closure requeues the
    // original items. Only a receipt already owned by a live turn is terminal.
    const failedInboundSeqs = [...this.activeInboundSeqs];
    const failedReceiptIds = [...this.activeReceiptIds];
    if (this.startingTurn && this.startingItems.length > 0) {
      // turn/start may have reached the server even though no acknowledgement
      // reached us. Replaying into the same thread could duplicate the input;
      // rebuild a fresh thread and perform one intentional journal catch-up.
      // Demote the stored thread durably too, so a daemon restart in this
      // window cannot resume it either.
      this.forceFreshThread = true;
      this._markSessionUnresumable();
    }
    this.child = null;
    if (!this.stopping) terminateProcessTree(child, 'SIGKILL');
    this.threadId = null;
    this.activeTurnId = null;
    this.startingTurn = false;
    this.modelWorking = false;
    this.modelStartedAtMs = null;
    this.startingItems = [];
    this.startingJournalThroughSeq = 0;
    this.startingOwner = null;
    this.activeOwner = null;
    this.activeInboundSeqs = [];
    this.activeReceiptIds = [];
    for (const pending of this.pending.values()) {
      try { pending.reject(new Error(`${this.runtimeLabel} connection lost`)); } catch { /* ignore */ }
    }
    this.pending.clear();
    if (!this.stopping && failedReceiptIds.length > 0 && this.onTurnFailed) {
      try {
        this.onTurnFailed(withReceiptIds({
          inboundSeqs: failedInboundSeqs,
          error: `${this.runtimeLabel} connection lost`,
        }, failedReceiptIds));
      } catch (error) {
        this.logger.warn('turn_failure_notify_failed', { error: safe(error) });
      }
    }
    if (this.stopping || !this.startedOk) return;
    this.logger.warn(`${this.logPrefix}_disconnected`, { code, signal });
    void this._reconnectLoop();
  }

  async _reconnectLoop() {
    if (this.reconnecting || this.stopping) return;
    this.reconnecting = true;
    while (!this.stopping) {
      await delay(this.backoffMs);
      if (this.stopping) break;
      try {
        await this._handshake();
        break;
      } catch (error) {
        this.logger.warn(`${this.logPrefix}_reconnect_failed`, { error: safe(error), backoffMs: this.backoffMs });
        this.backoffMs = Math.min(this.backoffMs * 2, this.reconnectCapMs);
      }
    }
    this.reconnecting = false;
  }

  _recordActivity(kind, fields = {}) {
    try {
      this.activitySeq += 1;
      this.store.recordActivity({
        eventKey: `${this.runtime}:${this.threadId ?? 'na'}:${fields.turnId ?? 'na'}:${kind}:${this.activitySeq}`,
        kind,
        summary: fields.summary,
        status: fields.status,
        turnId: fields.turnId,
        inboundSeq: fields.inboundSeq,
        metadata: fields.metadata,
      });
    } catch (error) {
      this.logger.warn('activity_persist_failed', { kind, error: safe(error) });
    }
  }

  _markSessionUnresumable() {
    if (!this.sessionPersistence || !this.threadId) return;
    try {
      const marked = this.store.markOwnedSessionUnresumable?.({
        runtime: this.runtime,
        authorityToken: this.authorityToken,
        sessionId: this.threadId,
        epoch: this.cognitionEpoch,
      });
      if (marked === false) this.logger.info(`${this.logPrefix}_session_unresumable_stale`);
    } catch (error) {
      this.logger.warn(`${this.logPrefix}_session_unresumable_failed`, { error: safe(error) });
    }
  }

  _publishSession() {
    if (!this.sessionPersistence || !this.threadId) return false;
    try {
      if (this.store.publishOwnedSession) {
        const published = this.store.publishOwnedSession({
          runtime: this.runtime,
          authorityToken: this.authorityToken,
          sessionId: this.threadId,
          policyHash: this.policyHash,
          epoch: this.cognitionEpoch,
          acceptedThroughSeq: this.syncedThroughSeq,
          sessionReady: true,
        });
        if (!published) this.logger.info(`${this.logPrefix}_session_publish_stale`);
        return published;
      }
      this.store.setGlobalSession?.(this.threadId, this.policyHash, {
        runtime: this.runtime,
        epoch: this.cognitionEpoch,
        syncedThroughSeq: this.syncedThroughSeq,
      });
      return true;
    } catch (error) {
      this.logger.warn(`${this.logPrefix}_session_persist_failed`, { error: safe(error) });
      return false;
    }
  }

  _notifyAvailable() {
    if (this.turnInProgress
      || this.queue.length > 0
      || !this.state.connected) return;
    const waiters = this.idleWaiters.splice(0);
    for (const resolve of waiters) resolve();
    if (!this.onAvailable) return;
    try {
      this.onAvailable();
    } catch (error) {
      this.logger.warn(`${this.logPrefix}_available_callback_failed`, { error: safe(error) });
    }
  }

  async stop() {
    this.stopping = true;
    const midTurn = Boolean(this.activeTurnId || this.startingTurn);
    const failedItems = [
      ...this.startingItems,
      ...this.queue,
      ...this.activeReceiptIds.map((receiptId) => ({ receiptId })),
    ];
    const failedReceiptIds = receiptIds(failedItems);
    if (failedReceiptIds.length > 0 && this.onTurnFailed) {
      const inboundSeqs = [...new Set([
        ...this._associatedInboundSeqs(),
        ...this.queue.map((item) => item.seq).filter((seq) => seq != null),
      ])];
      try {
        this.onTurnFailed(withReceiptIds({
          inboundSeqs,
          error: `${this.runtimeLabel} stopped before turn completion`,
        }, failedReceiptIds));
      } catch (error) {
        this.logger.warn('turn_failure_notify_failed', { error: safe(error) });
      }
    }
    this.queue = [];
    this.startingItems = [];
    this.startingOwner = null;
    this.activeOwner = null;
    this.activeInboundSeqs = [];
    this.activeReceiptIds = [];
    // Killed mid-turn: acceptance is unknowable, so the next daemon generation
    // must open a fresh thread instead of resuming (duplicate-input and
    // prompt-cache-rewrite risk on resume of an interrupted turn).
    if (midTurn) this._markSessionUnresumable();
    if (this.primeWaiter) {
      this.primeWaiter.reject(new Error(`${this.runtimeLabel} stopped during cognition catch-up`));
      this.primeWaiter = null;
    }
    const waiters = this.idleWaiters.splice(0);
    for (const resolve of waiters) resolve();
    if (this.drainTimer) { clearTimeout(this.drainTimer); this.drainTimer = null; }
    const child = this.child;
    if (!child?.pid) return;
    terminateProcessTree(child, 'SIGTERM');
    if (child.exitCode === null) {
      await Promise.race([
        new Promise((resolve) => child.once('exit', resolve)),
        delay(3_000),
      ]);
    }
    terminateProcessTree(child, 'SIGKILL');
  }
}
