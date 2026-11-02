// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn as nodeSpawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { cognitionHistoryPrefix, CognitionLimitError } from './cognition.mjs';
import { safeError as safe } from './logger.mjs';
import { activityPayload } from './activity.mjs';
import { sanitizeAgentChildEnv, terminateProcessTree } from './process-utils.mjs';
import { agentCliCommands } from './agent-clis.mjs';
import { agentToolEnv, agentToolServer } from './actions/api.mjs';
import { assembleConversationPolicy } from './conversation-policy.mjs';
import { assertPiVersion, piCommand, PI_MCP_EXTENSION, PI_MCP_STATUS } from './pi-runtime.mjs';
import { deliveryItem, receiptIds, withReceiptIds } from './runtime-input.mjs';
import { getRuntimeDefinition } from './runtime-catalog.mjs';

const MAX_LINE_BYTES = 5_000_000;
const START_TIMEOUT_MS = 15_000;
const RECONNECT_BASE_MS = 1_000;
const RECONNECT_CAP_MS = 30_000;
const RPC_TIMEOUT_MS = 8_000;

const PI_RUNTIME = getRuntimeDefinition('pi');
export const PI_EFFORTS = PI_RUNTIME.efforts;

const PREFLIGHT_FLAGS = [
  '--mode', '--append-system-prompt', '--thinking',
  '--session-id', '--session-dir', '--model', '--extension', '--approve',
];

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function appendBounded(current, chunk, label) {
  const next = current + chunk;
  if (Buffer.byteLength(next, 'utf8') > MAX_LINE_BYTES) {
    throw new Error(`pi ${label} exceeded local limit`);
  }
  return next;
}

export function sanitizePiEnv(environment, profile = '', actions = null, config = {}) {
  const safeEnv = sanitizeAgentChildEnv(environment, { profile, actions, cliBins: agentCliCommands(config).map(({ bin }) => bin) });
  // Pi may use the locally configured Volcengine Ark provider. Unlike the
  // other agent runtimes, its model client runs in this child process, so it
  // needs that provider credential to make inference requests.
  if (typeof environment.ARK_API_KEY === 'string' && environment.ARK_API_KEY) {
    safeEnv.ARK_API_KEY = environment.ARK_API_KEY;
  }
  return safeEnv;
}

export function parsePiModelSelector(value) {
  const raw = String(value ?? '').trim();
  if (!raw) return { provider: '', modelId: '', id: '' };
  const slash = raw.indexOf('/');
  if (slash <= 0) return { provider: '', modelId: raw, id: raw };
  return {
    provider: raw.slice(0, slash),
    modelId: raw.slice(slash + 1),
    id: raw,
  };
}

export function formatPiModelId(model) {
  if (!model || typeof model !== 'object') return '';
  if (model.provider && model.id) return `${model.provider}/${model.id}`;
  return String(model.id ?? '');
}

// Persistent RPC adapter; Pi's built-in MCP client calls the same server as
// Codex, TraeX, and Claude. The session extension only configures that client.
export class PiAgent {
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
      ?? { runtime: PI_RUNTIME.id, epoch: 1, authorityToken: null };
    this.runtime = PI_RUNTIME.id;
    this.capabilities = PI_RUNTIME.capabilities;
    this.cognitionEpoch = options.cognitionEpoch ?? cognition.epoch;
    this.authorityToken = options.authorityToken ?? cognition.authorityToken ?? '';
    this.sessionPersistence = options.sessionPersistence !== false;
    this.autoReconnect = this.sessionPersistence;
    this.allowResume = options.allowResume !== false;
    this.syncedThroughSeq = options.syncedThroughSeq ?? 0;
    this.deliveredThroughSeq = this.syncedThroughSeq;

    this.systemPrompt = assembleConversationPolicy(config);
    this.developerInstructionsText = this.systemPrompt;
    this.model = options.model
      ?? store.getSetting?.('pi_model')
      ?? options.initialModel
      ?? config.piModel
      ?? '';
    this.effort = options.effort
      ?? store.getSetting?.('pi_effort')
      ?? config.piEffort
      ?? 'medium';

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
    this.appliedModel = null;
    this.appliedEffort = null;
    this.settingsUpdate = null;
  }

  get threadId() { return this.sessionId; }

  get policyHash() {
    return createHash('sha256')
      .update(this.developerInstructionsText).update('\0')
      .update(this.config.workspace).update('\0')
      .update('pi-rpc-mcp-v2').digest('hex');
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
    this.sessionInitialized = Boolean(resumable);
    this.resumeRequested = Boolean(resumable);
    this.syncedThroughSeq = resumable ? global.syncedThroughSeq ?? 0 : this.syncedThroughSeq;
    this.deliveredThroughSeq = this.syncedThroughSeq;
    this.needsHistoryPrefix = !resumable;

    await this._preflight();
    await this._spawnPersistent();
    this.startedOk = true;
    this.backoffMs = this.reconnectBaseMs;
    this._persistSession();
    this._drain();
    this._notifyAvailable();
  }

  async _preflight() {
    const version = await this._probe(['--version']);
    assertPiVersion(version.stdout);
    const help = await this._probe(['--help']);
    for (const flag of PREFLIGHT_FLAGS) {
      if (!help.stdout.includes(flag)) {
        throw new Error(`pi preflight is missing required flag ${flag}`);
      }
    }
    if (!help.stdout.includes('rpc')) {
      throw new Error('pi preflight is missing required flag --mode rpc');
    }
  }

  _spawnPi(args, options) {
    const command = piCommand(this.config.piBin, args);
    return this._spawn(command.command, command.args, options);
  }

  _probe(args) {
    return new Promise((resolve, reject) => {
      let child;
      try {
        child = this._spawnPi(args, {
          cwd: this.config.workspace,
          env: sanitizePiEnv(process.env, this.larkProfile(), agentToolEnv(this.config, this.authorityToken), this.config),
          shell: false,
          detached: process.platform !== 'win32',
          stdio: ['ignore', 'pipe', 'pipe'],
        });
      } catch (error) { reject(error); return; }
      let stdout = '';
      let stderr = '';
      let outputError = null;
      let settled = false;
      const finish = (error, result = null) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (error) reject(error);
        else resolve(result);
      };
      const timer = setTimeout(() => {
        terminateProcessTree(child, 'SIGKILL');
        finish(new Error(`pi preflight timed out: ${args.join(' ')}`));
      }, this.startTimeoutMs);
      timer.unref?.();
      child.stdout?.setEncoding('utf8');
      child.stderr?.setEncoding('utf8');
      child.stdout?.on('data', (chunk) => {
        try { stdout = appendBounded(stdout, chunk, 'preflight output'); } catch (error) {
          outputError = error;
          terminateProcessTree(child, 'SIGKILL');
        }
      });
      child.stderr?.on('data', (chunk) => {
        try { stderr = appendBounded(stderr, chunk, 'preflight error'); } catch (error) {
          outputError = error;
          terminateProcessTree(child, 'SIGKILL');
        }
      });
      child.once('error', (error) => finish(error));
      child.once('exit', (code) => {
        if (outputError) { finish(outputError); return; }
        if (code === 0) finish(null, { stdout, stderr });
        else finish(new Error(`pi preflight failed: ${safe(stderr || stdout || `exit ${code}`)}`));
      });
    });
  }

  // lark-cli stays available for non-message Lark operations.
  larkProfile() {
    return this.config.connectors.includes('lark') ? this.config.profile : '';
  }

  _sessionDir() {
    const root = this.config.dataDir || os.tmpdir();
    const directory = path.join(root, 'pi-sessions');
    fs.mkdirSync(directory, { recursive: true });
    return directory;
  }

  _promptPath() {
    return path.join(this._sessionDir(), 'system-prompt.md');
  }

  async _spawnPersistent() {
    if (this.needsHistoryPrefix) await this.store.prepareOVContext?.(this.cognitionEpoch);
    const model = this.model;
    const effort = this.effort;
    return new Promise((resolve, reject) => {
      const promptPath = this._promptPath();
      fs.writeFileSync(promptPath, this.developerInstructionsText, { encoding: 'utf8', mode: 0o600 });
      const args = [
        '--mode', 'rpc',
        '--append-system-prompt', promptPath,
        '--thinking', effort,
        '--session-id', this.sessionId,
        '--session-dir', this._sessionDir(),
        '--approve',
        '--extension', PI_MCP_EXTENSION,
      ];
      if (model) args.push('--model', model);

      const env = sanitizePiEnv(process.env, this.larkProfile(), agentToolEnv(this.config, this.authorityToken), this.config);
      env.PERSONAL_AGENT_MCP_CONFIG = JSON.stringify({
        ...agentToolServer(this.config, this.authorityToken),
        timeout: Math.max(10, Math.ceil(this.config.sendTimeoutMs / 1_000) + 5),
      });

      let child;
      try {
        child = this._spawnPi(args, {
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
        // A later spawn may already own the agent; leave its state alone.
        if (this.child === child) {
          this.child = null;
          this.connected = false;
          this.onMcpStartup = null;
        }
        terminateProcessTree(child, 'SIGKILL');
        reject(error);
      };
      const timer = setTimeout(() => failStart(new Error('Pi startup timed out; check that built-in MCP is enabled, personal_agent is not overridden in mcp.json, and no extension blocks session start')), this.startTimeoutMs);
      timer.unref?.();

      child.stdin?.on('error', (error) => {
        if (this.child === child) this.logger.warn('pi_stdin_error', { error: safe(error) });
      });
      child.stdout?.setEncoding('utf8');
      child.stderr?.setEncoding('utf8');
      let mcpReady = false;
      this.onMcpStartup = (ok, error = null) => {
        if (settled || child.exitCode !== null) return;
        if (!ok) {
          failStart(error ?? new Error('Pi could not load the Core MCP tools with direct exposure; check built-in MCP and conflicting personal_agent settings'));
          return;
        }
        if (mcpReady) return;
        mcpReady = true;
        // Pi reads stdin only after every session_start handler returns, so the
        // MCP notice alone does not mean it can take prompts or dialog answers.
        // Its first command response does; until then a dialog fails the start.
        const id = this._nextRpcId();
        this.pendingRpc.set(id, {
          command: 'set_steering_mode',
          resolve: () => {
            if (settled) return;
            this.connected = true;
            this.appliedModel = model;
            this.appliedEffort = effort;
            ready = true;
            settled = true;
            clearTimeout(timer);
            this.onMcpStartup = null;
            this.sessionInitialized = true;
            resolve();
          },
          reject: failStart,
        });
        try { this._writeCommand({ id, type: 'set_steering_mode', mode: 'all' }); } catch (error) {
          this.pendingRpc.delete(id);
          failStart(error);
        }
      };
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
          failStart(new Error(`pi exited during startup (code ${code}, signal ${signal})`));
          return;
        }
        this._handleExit(child, code, signal);
      });
    });
  }

  _nextRpcId() {
    this.rpcSeq += 1;
    return `pi-${this.rpcSeq}`;
  }

  _writeCommand(payload) {
    if (!this.child?.stdin?.writable) throw new Error('pi stdin is not writable');
    const id = payload.id ?? this._nextRpcId();
    this.child.stdin.write(`${JSON.stringify({ id, ...payload })}\n`);
    return id;
  }

  _rpc(payload) {
    if (!this.connected || !this.child?.stdin?.writable) {
      return Promise.reject(new Error('pi rpc is not connected'));
    }
    const id = this._nextRpcId();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pendingRpc.delete(id);
        reject(new Error(`pi rpc timed out: ${payload.type}`));
      }, this.rpcTimeoutMs);
      timer.unref?.();
      this.pendingRpc.set(id, {
        command: payload.type,
        resolve: (value) => {
          clearTimeout(timer);
          resolve(value);
        },
        reject: (error) => {
          clearTimeout(timer);
          reject(error);
        },
      });
      try {
        this.child.stdin.write(`${JSON.stringify({ id, ...payload })}\n`);
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
    // Native transcripts already own their history, even when the cursor is zero.
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
    const command = wasActive
      ? { type: 'steer', message: prepared.body }
      : { type: 'prompt', message: prepared.body };
    try {
      this._writeCommand(command);
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
    if (!wasActive) {
      this.modelWorking = true;
      this.modelStartedAtMs = this.turnStartedAtMs;
    }
    const input = items.map((item) => item.text).join('\n');
    this._recordActivity(wasActive ? 'agent.steered' : 'agent.turn_started', {
      inboundSeq: prepared.seqs.at(-1) ?? null,
      summary: wasActive ? input : undefined,
      metadata: {
        runtime: PI_RUNTIME.id,
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
        this.logger.warn('pi_line_error', { error: safe(error) });
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
      if (line.trim()) this.logger.debug('pi_stderr', { detail: safe(line.slice(0, 500)) });
    }
  }

  _handleLine(line) {
    let message;
    try { message = JSON.parse(line.replace(/\r$/, '')); } catch { return; }
    if (!message || typeof message !== 'object') return;

    if (message.type === 'extension_ui_request') {
      if (message.method === 'notify') {
        if (message.message === `${PI_MCP_STATUS}:ready`) this.onMcpStartup?.(true);
        if (message.message === `${PI_MCP_STATUS}:failed`) this.onMcpStartup?.(false);
      } else if (['confirm', 'select', 'input', 'editor'].includes(message.method) && message.id != null) {
        this.logger.warn('pi_interaction_cancelled', { method: message.method, reason: 'unattended' });
        this._recordActivity('agent.interaction_declined', {
          summary: `extension ${message.method}`, metadata: { runtime: 'pi', method: message.method },
        });
        if (this.onMcpStartup) {
          // Pi 1.4.0 installs its stdin reader only after session_start handlers
          // finish. A startup dialog cannot consume our cancellation response.
          this.onMcpStartup(false, new Error('Pi extension requested an interactive dialog during startup; disable its startup prompt for this unattended daemon'));
          return;
        }
        // Dialog responses are fire-and-forget, not commands expecting an RPC
        // acknowledgement. Preserve the extension's id.
        this._writeCommand({ type: 'extension_ui_response', id: message.id, cancelled: true });
      }
      return;
    }

    if (message.type === 'response') {
      const pending = message.id != null ? this.pendingRpc.get(message.id) : null;
      if (pending) {
        this.pendingRpc.delete(message.id);
        if (message.success === false) {
          pending.reject(new Error(message.error || `pi ${message.command || pending.command} failed`));
        } else {
          pending.resolve(message);
        }
      }
      return;
    }

    if (message.type === 'agent_start') {
      this.modelWorking = true;
      this.modelStartedAtMs = Date.now();
      return;
    }

    if (message.type === 'message_update') {
      const event = message.assistantMessageEvent;
      const kind = event?.type;
      if (['thinking_start', 'thinking_delta', 'text_start', 'text_delta'].includes(kind) && !this.modelWorking) {
        this.modelWorking = true;
        this.modelStartedAtMs = Date.now();
      }
      // One row per finished block; per-token deltas only flooded the feed.
      if (kind === 'thinking_end') {
        this._recordActivity('agent.thinking', {
          inboundSeq: this._activeSeqs().at(-1) ?? null,
          metadata: { runtime: PI_RUNTIME.id, thinking: event.content || undefined },
        });
      } else if (kind === 'text_end' && event.content) {
        this._recordActivity('agent.note', {
          inboundSeq: this._activeSeqs().at(-1) ?? null,
          summary: String(event.content),
          metadata: { runtime: PI_RUNTIME.id, delivery: 'telemetry_only' },
        });
      } else if (kind === 'error') {
        this.turnError = new Error(String(event.reason || 'pi assistant error'));
      }
      return;
    }

    if (message.type === 'message_end') {
      const usage = message.message?.usage;
      if (usage && Number.isFinite(usage.input)) {
        this.turnUsage = {
          inputTokens: usage.input + (usage.cacheRead ?? 0) + (usage.cacheWrite ?? 0),
          cachedInputTokens: usage.cacheRead,
          outputTokens: usage.output,
          costUsd: usage.cost?.total,
        };
      }
      const stop = message.message?.stopReason;
      if (stop && !['stop', 'toolUse'].includes(stop)) {
        this.turnError = this.turnError ?? new Error(`pi stop: ${stop}`);
      }
      return;
    }

    if (message.type === 'tool_execution_start') {
      this.modelWorking = false;
      this.modelStartedAtMs = null;
      const name = String(message.toolName ?? '');
      this.toolStarts.set(message.toolCallId, Date.now());
      this._recordActivity('agent.tool_called', {
        inboundSeq: this._activeSeqs().at(-1) ?? null,
        summary: name || 'unknown tool',
        status: 'RUNNING',
        metadata: { runtime: PI_RUNTIME.id, toolId: message.toolCallId, toolName: name, input: activityPayload(message.args) },
      });
      return;
    }

    if (message.type === 'tool_execution_end') {
      const startedAtMs = this.toolStarts.get(message.toolCallId);
      this.toolStarts.delete(message.toolCallId);
      const output = activityPayload(message.result?.content ?? message.result);
      this._recordActivity('agent.tool_completed', {
        inboundSeq: this._activeSeqs().at(-1) ?? null,
        status: message.isError === true ? 'FAILED' : 'COMPLETED',
        summary: String(message.toolName || (message.isError === true ? 'Pi tool failed' : 'Pi tool completed')),
        metadata: {
          runtime: PI_RUNTIME.id,
          toolId: message.toolCallId,
          toolName: message.toolName,
          ...(startedAtMs ? { durationMs: Date.now() - startedAtMs } : {}),
          ...(message.isError === true ? { error: output } : { output }),
        },
      });
      this.modelWorking = true;
      this.modelStartedAtMs = Date.now();
      return;
    }

    if (message.type === 'agent_end' && message.willRetry === true) return;

    if (message.type === 'agent_settled') {
      const error = this.turnError;
      this.turnError = null;
      if (this.turnActive) this._completeTurn(error);
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
    this.modelStartedAtMs = null;
    this.deliveredThroughSeq = this.syncedThroughSeq;
    this.toolStarts.clear();
    this._recordActivity('agent.turn_completed', {
      inboundSeq: seqs.at(-1) ?? null,
      status: error ? 'failed' : 'completed',
      summary: error ? safe(error) : undefined,
      metadata: {
        runtime: PI_RUNTIME.id,
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
      this.logger.info('pi_session_acceptance_stale', { acceptedThroughSeq });
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
        runtime: PI_RUNTIME.id,
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

  _markSessionUnresumable() {
    if (!this.sessionPersistence || !this.sessionId || !this.sessionInitialized) return;
    try {
      const marked = this.store.markOwnedSessionUnresumable?.({
        runtime: this.runtime,
        authorityToken: this.authorityToken,
        sessionId: this.sessionId,
        epoch: this.cognitionEpoch,
      });
      if (marked === false) this.logger.info('pi_session_unresumable_stale');
    } catch (error) {
      this.logger.warn('pi_session_unresumable_failed', { error: safe(error) });
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
        if (!published) this.logger.info('pi_session_publish_stale');
        return;
      }
      this.store.setGlobalSession?.(this.sessionId, this.policyHash, {
        runtime: this.runtime,
        epoch: this.cognitionEpoch,
        syncedThroughSeq: this.syncedThroughSeq,
        sessionReady: this.sessionInitialized,
      });
    } catch (error) {
      this.logger.warn('pi_session_persist_failed', { error: safe(error) });
    }
  }

  _handleExit(child, code, signal, processError = null) {
    if (this.child !== child) return;
    this.child = null;
    this.connected = false;
    this._rejectPendingRpc(processError ?? new Error('pi connection lost'));
    if (!this.stopping) terminateProcessTree(child, 'SIGKILL');
    const wasActive = this.turnActive;
    const acceptanceUnknown = wasActive && this.sessionInitialized;
    const items = this.activeItems.splice(0);
    this.activeOwner = null;
    this.turnActive = false;
    this.turnStartedAtMs = null;
    this.modelWorking = false;
    this.modelStartedAtMs = null;
    this.deliveredThroughSeq = this.syncedThroughSeq;
    this.needsHistoryPrefix = !this.sessionInitialized;
    if ((acceptanceUnknown || !this.sessionInitialized)
      && !this.stopping && this.startedOk && this.autoReconnect) {
      // Demote the stored session before regenerating locally, so a daemon
      // crash in this window cannot resume the ambiguous transcript later.
      if (acceptanceUnknown) this._markSessionUnresumable();
      this.sessionId = randomUUID();
      this.sessionInitialized = false;
      this.resumeRequested = false;
      this.needsHistoryPrefix = true;
      this.syncedThroughSeq = 0;
      this.deliveredThroughSeq = 0;
    }
    if (wasActive) {
      this._failItems(items, processError ?? this.turnError
        ?? new Error(`pi connection lost (code ${code}, signal ${signal})`));
    }
    this.turnError = null;
    if (this.stopping || !this.startedOk || !this.autoReconnect) {
      this._resolveIdleWaiters();
      return;
    }
    this.logger.warn('pi_disconnected', { code, signal, error: processError ? safe(processError) : undefined });
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
        this._drain();
        this._notifyAvailable();
        break;
      } catch (error) {
        this.logger.warn('pi_reconnect_failed', { error: safe(error), backoffMs: this.backoffMs });
        this.backoffMs = Math.min(this.backoffMs * 2, this.reconnectCapMs);
      }
    }
    this.reconnecting = false;
  }

  async _replaceProcess({ fresh, persist = true, activate = true }) {
    await this._terminateCurrent();
    if (fresh) {
      this.sessionId = randomUUID();
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
    this.resumeRequested = previous.resumeRequested;
    this.needsHistoryPrefix = previous.needsHistoryPrefix;
    this.syncedThroughSeq = previous.syncedThroughSeq;
    this.deliveredThroughSeq = previous.deliveredThroughSeq;
    this.model = previous.model;
    this.effort = previous.effort;
    if (previous.sessionId) await this._spawnPersistent();
    this._drain();
    this._notifyAvailable();
  }

  async prepareSessionRotation({ rollback = {} } = {}) {
    while (this.settingsUpdate) await this.settingsUpdate;
    if (this.turnActive) throw new Error('pi session rotation requires an idle runtime');
    if (this.sessionRotation) throw new Error('pi session rotation is already prepared');
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
        this.logger.warn('pi_session_prepare_restore_failed', { error: safe(restoreError) });
      }
      this.sessionRotation = null;
      throw error;
    }
  }

  commitSessionRotation(prepared, { persist = true, preserveContext = false } = {}) {
    if (!prepared?.id || this.sessionRotation?.id !== prepared.id || this.sessionId !== prepared.id) {
      throw new Error('prepared pi session is no longer current');
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
      try { this.onTurnAbandoned(withReceiptIds({ inboundSeqs }, receipts)); } catch (error) {
        this.logger.warn('turn_abandoned_notify_failed', { error: safe(error) });
      }
    }
    this.turnError = null;
    this.deliveredThroughSeq = this.syncedThroughSeq;
    try {
      if (this.child?.stdin?.writable) this._writeCommand({ type: 'abort' });
    } catch {}
    await this._terminateCurrent();
    this._resolveIdleWaiters();
  }

  async rotateSession({ resume = false } = {}) {
    while (this.settingsUpdate) await this.settingsUpdate;
    if (this.turnActive) throw new Error('pi session rotation requires an idle runtime');
    if (resume) {
      await this._replaceProcess({ fresh: false });
      return this.sessionId;
    }
    this.syncedThroughSeq = 0;
    this.deliveredThroughSeq = 0;
    this.needsHistoryPrefix = false;
    await this._replaceProcess({ fresh: true });
    return this.sessionId;
  }

  setModel(model, { persist = true } = {}) {
    if (persist) this.store.setSetting?.('pi_model', model);
    this.model = model;
  }

  _settingsPending() {
    return this.appliedModel !== this.model || this.appliedEffort !== this.effort;
  }

  setEffort(effort, { persist = true } = {}) {
    if (persist) this.store.setSetting?.('pi_effort', effort);
    this.effort = effort;
  }

  _applySettings() {
    if (this.settingsUpdate) return;
    const model = this.model;
    const effort = this.effort;
    // Apply between prompts, never while an agent turn (including tools) is
    // running. Keep input queued until Pi acknowledges the in-session update.
    this.settingsUpdate = (async () => {
      try {
        if (this.appliedModel !== model) {
          const parsed = parsePiModelSelector(model);
          if (parsed.provider && parsed.modelId) {
            await this._rpc({ type: 'set_model', provider: parsed.provider, modelId: parsed.modelId });
            this.appliedModel = model;
            // Pi can change thinking level when selecting a model.
            this.appliedEffort = null;
          } else {
            await this._replaceProcess({ fresh: false, activate: false });
            return;
          }
        }
        await this._rpc({ type: 'set_thinking_level', level: effort });
        this.appliedEffort = effort;
      } catch (error) {
        this.logger.warn('pi_settings_update_failed', { error: safe(error) });
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
    if (!this.model) {
      const state = await this._rpc({ type: 'get_state' });
      this.resolvedModel = formatPiModelId(state?.data?.model);
    }
    const response = await this._rpc({ type: 'get_available_models' });
    const models = response?.data?.models;
    if (!Array.isArray(models)) return [];
    return models.map((model) => {
      const id = formatPiModelId(model);
      return {
        id,
        efforts: model?.reasoning ? [...PI_EFFORTS] : ['off'],
      };
    }).filter((model) => model.id);
  }

  _recordActivity(kind, fields = {}) {
    try {
      this.activitySeq += 1;
      this.store.recordActivity?.({
        eventKey: `pi:${this.sessionId ?? 'na'}:${kind}:${this.activitySeq}`,
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
      this.logger.warn('pi_available_callback_failed', { error: safe(error) });
    }
  }

  async _terminateCurrent() {
    const child = this.child;
    this.child = null;
    this.connected = false;
    this._rejectPendingRpc(new Error('pi process replaced'));
    if (!child?.pid) {
      return;
    }
    if (child.exitCode !== null) {
      terminateProcessTree(child, 'SIGKILL');
      return;
    }
    const exited = new Promise((resolve) => child.once('exit', resolve));
    terminateProcessTree(child, 'SIGTERM');
    await Promise.race([
      exited,
      delay(3_000),
    ]);
    if (child.exitCode === null) {
      terminateProcessTree(child, 'SIGKILL');
      await Promise.race([exited, delay(1_000)]);
    }
    if (child.exitCode === null) throw new Error('pi process did not exit after SIGKILL');
  }

  async stop() {
    this.stopping = true;
    this.autoReconnect = false;
    this._rejectPendingRpc(new Error('pi stopped'));
    await this.settingsUpdate;
    const midTurn = this.turnActive;
    const queued = this.queue.flat();
    this.queue = [];
    if (receiptIds(queued).length > 0) {
      this._failItems(queued, new Error('pi stopped before queued work started'));
    }
    const items = this.activeItems.splice(0);
    this.activeOwner = null;
    this.turnActive = false;
    if (items.length > 0) this._failItems(items, new Error('pi stopped during an active turn'));
    // Killed mid-turn: acceptance is unknowable, so the next generation must
    // not resume this transcript (duplicate-input and cache-rewrite risk).
    if (midTurn) this._markSessionUnresumable();
    this._resolveIdleWaiters();
    await this._terminateCurrent();
  }
}
