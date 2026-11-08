// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { spawnDetached } from './platform/index.mjs';

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn as nodeSpawn } from './platform/index.mjs';
import { createHash, randomUUID } from 'node:crypto';
import { cognitionHistoryPrefix, CognitionLimitError } from './cognition.mjs';
import { safeError as safe } from './logger.mjs';
import { activityPayload } from './activity.mjs';
import { sanitizeAgentChildEnv, terminateProcessTree } from './process-utils.mjs';
import { agentCliCommands } from './agent-clis.mjs';
import { agentToolEnv, agentToolServer } from './actions/api.mjs';
import { assembleConversationPolicy } from './conversation-policy.mjs';
import { deliveryItem, receiptIds, withReceiptIds, withTurnNotice } from './runtime-input.mjs';
import { getRuntimeDefinition } from './runtime-catalog.mjs';
import { prepareOpencodeConfigHome } from './ov-isolation.mjs';

const OPENCODE_RUNTIME = getRuntimeDefinition('opencode');
const MAX_LINE_BYTES = 5_000_000;
const START_TIMEOUT_MS = 30_000;
const START_STABILITY_MS = 250;
const CONTROL_TIMEOUT_MS = 15_000;
const RECONNECT_BASE_MS = 1_000;
const RECONNECT_CAP_MS = 30_000;

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function hasToolInput(rawInput) {
  return rawInput != null && (typeof rawInput !== 'object' || Object.keys(rawInput).length > 0);
}

function appendBounded(current, chunk, label) {
  const next = current + chunk;
  if (Buffer.byteLength(next, 'utf8') > MAX_LINE_BYTES) {
    throw new Error(`opencode ${label} exceeded local limit`);
  }
  return next;
}

export function sanitizeOpencodeEnv(environment, profile = '', actions = null, config = {}) {
  return sanitizeAgentChildEnv(environment, { profile, actions, cliBins: agentCliCommands(config).map(({ bin }) => bin) });
}

// Persistent Agent Client Protocol (ACP, nd-JSON over stdio) adapter for
// `opencode acp`. One ACP session (one native transcript) serves every inbound
// line. OpenCode serializes prompts against one session: unlike Codex's
// turn/steer, a prompt sent during a live turn runs only after it, so this
// adapter is queue-next-turn. The Core's agent tools load as a per-instance
// stdio MCP server; the conversation policy is an instruction file pinned via
// a daemon-owned OPENCODE_CONFIG.
export class OpencodeAgent {
  constructor(config, store, logger, options = {}) {
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

    const cognition = store.getCognitionState?.()
      ?? { runtime: OPENCODE_RUNTIME.id, epoch: 1, authorityToken: null };
    this.runtime = OPENCODE_RUNTIME.id;
    this.capabilities = OPENCODE_RUNTIME.capabilities;
    this.cognitionEpoch = options.cognitionEpoch ?? cognition.epoch;
    this.authorityToken = options.authorityToken ?? cognition.authorityToken ?? '';
    this.sessionPersistence = options.sessionPersistence !== false;
    this.autoReconnect = this.sessionPersistence;
    this.allowResume = options.allowResume !== false;
    this.syncedThroughSeq = options.syncedThroughSeq ?? 0;
    this.deliveredThroughSeq = this.syncedThroughSeq;

    this.systemPrompt = assembleConversationPolicy(config);
    this.model = options.model
      ?? store.getSetting?.('opencode_model')
      ?? options.initialModel
      ?? config.opencodeModel
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
    // The model the ACP session reports as current. `model` stays what the
    // daemon asked for ('' = OpenCode's own default), as in the Codex adapter.
    this.sessionModel = '';
    this.settingsUpdate = null;
    this.modelCatalog = [];
  }

  get threadId() { return this.sessionId; }

  get resolvedModel() { return this.model || this.sessionModel || ''; }

  get policyHash() {
    return createHash('sha256')
      .update(this.systemPrompt).update('\0')
      .update(this.config.workspace).update('\0')
      .update('opencode-acp-v1').digest('hex');
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

  whenIdle() {
    if (!this.turnInProgress && this.queue.length === 0) return Promise.resolve();
    return new Promise((resolve) => this.idleWaiters.push(resolve));
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

  // Spawns the ACP child and completes the initialize/session handshake. The
  // child process exists synchronously after this method's first await, so
  // tests can answer its frames while start() is still pending.
  async _spawnPersistent() {
    if (this.needsHistoryPrefix) await this.store.prepareOVContext?.(this.cognitionEpoch);
    const model = this.model;
    const configPath = this._writeRuntimeFiles();
    return new Promise((resolve, reject) => {
      const args = ['acp', '--cwd', this.config.workspace];
      const env = {
        ...sanitizeOpencodeEnv(process.env, this._larkProfile(),
          agentToolEnv(this.config, this.authorityToken), this.config),
        // Pins the conversation policy instruction and the personal_agent MCP
        // server without editing the user's own opencode.json.
        OPENCODE_CONFIG: configPath,
        // OpenCode's own OpenViking plugin stays off: its global config folder
        // is a Vika-owned copy without it (ov-isolation.mjs).
        XDG_CONFIG_HOME: prepareOpencodeConfigHome(path.join(this.runtimeDir, 'config-home'), process.env),
      };
      let child;
      try {
        child = this._spawn(this.config.opencodeBin, args, {
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
        this._terminateProcessTree(child, 'SIGKILL');
        this._cleanupRuntimeFiles();
        reject(error);
      };
      const timer = setTimeout(() => failStart(new Error('opencode ACP start timed out')), this.startTimeoutMs);
      timer.unref?.();

      child.stdin?.on('error', (error) => {
        if (this.child === child) this.logger.warn('opencode_stdin_error', { error: safe(error) });
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
          const error = new Error(`opencode exited during startup (code ${code}, signal ${signal})`);
          if (startupError) error.message += `; stderr: ${startupError.slice(-3000)}`;
          failStart(error);
          return;
        }
        this._handleExit(child, code, signal);
      });

      this._requestOn(child, 'initialize', {
        protocolVersion: 1,
        clientCapabilities: { fs: { readTextFile: false, writeTextFile: false }, terminal: false },
        clientInfo: { name: 'vika', version: '1' },
      }, this.startTimeoutMs).then(async (result) => {
        if (!/opencode/i.test(result?.agentInfo?.name ?? '')) {
          throw new Error('unrecognised ACP agent');
        }
        if (this.resumeRequested && this.sessionId) {
          try {
            const loaded = await this._requestOn(child, 'session/load', {
              sessionId: this.sessionId,
              cwd: this.config.workspace,
              mcpServers: [],
            });
            this._captureModelOptions(loaded?.configOptions ?? []);
            this.sessionInitialized = true;
            this.needsHistoryPrefix = false;
          } catch (error) {
            // The stored transcript is gone; start fresh with journal catch-up.
            this.logger.warn('opencode_session_load_failed_fresh_start', { error: safe(error) });
            this.sessionId = null;
            this.sessionInitialized = false;
            await this.store.prepareOVContext?.(this.cognitionEpoch);
            await this._openSession(child);
          }
        } else {
          await this._openSession(child);
        }
        // Apply the model before the stability window so start() only resolves
        // once the runtime is ready to accept a prompt with the right model.
        if (model) await this._applyModelOn(child, model);
        this.appliedModel = model;
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

  _writeRuntimeFiles() {
    this._cleanupRuntimeFiles();
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-opencode-'));
    try { fs.chmodSync(directory, 0o700); } catch {}
    this.runtimeDir = directory;
    this.promptPath = path.join(directory, 'instructions.md');
    fs.writeFileSync(this.promptPath, this.systemPrompt, { encoding: 'utf8', mode: 0o600 });
    const tools = agentToolServer(this.config, this.authorityToken);
    const config = {
      instructions: [this.promptPath],
      mcp: {
        // OpenCode's local MCP schema takes the argv as one array and applies
        // `timeout` (ms, default 5s) to every request, tool calls included.
        personal_agent: {
          type: 'local',
          command: [tools.command, ...tools.args],
          enabled: true,
          timeout: Math.max(10_000, this.config.sendTimeoutMs + 5_000),
        },
      },
    };
    this.configPath = path.join(directory, 'opencode.json');
    fs.writeFileSync(this.configPath, JSON.stringify(config), { encoding: 'utf8', mode: 0o600 });
    return this.configPath;
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

  async _openSession(child) {
    const created = await this._requestOn(child, 'session/new', {
      cwd: this.config.workspace,
      mcpServers: [],
    });
    if (!created?.sessionId) throw new Error('opencode session/new returned no session id');
    this.sessionId = created.sessionId;
    this.sessionInitialized = true;
    this.resumeRequested = false;
    this.needsHistoryPrefix = true;
    this._captureModelOptions(created.configOptions ?? []);
  }

  _captureModelOptions(configOptions) {
    const modelOption = configOptions.find((option) => option?.id === 'model');
    const options = Array.isArray(modelOption?.options) ? modelOption.options : [];
    const current = typeof modelOption?.currentValue === 'string' ? modelOption.currentValue : '';
    this.modelCatalog = options
      .filter((option) => option && typeof option.value === 'string')
      .map((option) => ({
        id: option.value,
        ...(typeof option.name === 'string' ? { displayName: option.name } : {}),
        ...(option.value === current ? { isDefault: true } : {}),
        // OpenCode exposes reasoning as per-model variants, not an effort API.
        effortsKnown: false,
        efforts: [],
      }));
    this.sessionModel = current;
  }

  async _applyModelOn(child, model) {
    await this._requestOn(child, 'session/set_config_option', {
      sessionId: this.sessionId,
      configId: 'model',
      value: model,
    }, this.controlTimeoutMs);
    this.sessionModel = model;
  }

  _nextRpcId() {
    this.rpcSeq += 1;
    return `oc-${this.rpcSeq}`;
  }

  _requestOn(child, method, params, timeoutMs = this.controlTimeoutMs) {
    const id = this._nextRpcId();
    return new Promise((resolve, reject) => {
      const timer = (Number.isFinite(timeoutMs) && timeoutMs > 0)
        ? setTimeout(() => {
          this.pendingRpc.delete(id);
          reject(new Error(`opencode rpc timed out: ${method}`));
        }, timeoutMs)
        : null;
      timer?.unref?.();
      this.pendingRpc.set(id, { resolve, reject, timer });
      try {
        child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`);
      } catch (error) {
        if (timer) clearTimeout(timer);
        this.pendingRpc.delete(id);
        reject(error);
      }
    });
  }

  // Notifications and answers to agent requests: best effort, no reply.
  _send(frame) {
    try {
      this.child?.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', ...frame })}\n`);
    } catch {}
  }

  _request(method, params, timeoutMs) {
    if (!this.child?.stdin?.writable) return Promise.reject(new Error('opencode is disconnected'));
    return this._requestOn(this.child, method, params, timeoutMs);
  }

  // ---- inbound state machine -------------------------------------------------

  deliver(text, seq = null, receiptId = null) {
    return this.deliverBatch([deliveryItem({ text, seq, receiptId })]);
  }

  // OpenCode serializes prompts on one session: anything arriving during a
  // live turn is held here and submitted as the next prompt when it ends.
  deliverBatch(items) {
    if (this.stopping || !Array.isArray(items) || items.length === 0) return false;
    const batch = items.map(deliveryItem);
    if (this.settingsUpdate || !this.connected || !this.child?.stdin?.writable || this.turnActive) {
      this.queue.push(batch);
      return true;
    }
    this._writeBatch(batch);
    return true;
  }

  tryStartBatch(items) {
    if (this.stopping || this.settingsUpdate || !this.connected || this.turnInProgress
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

  // The model is applied during startup before the stability window. A /model
  // choice arrives as a plain field (RuntimeAdapter.applySettings) and reaches
  // the session through set_config_option before the next prompt.
  _settingsPending() {
    return Boolean(this.model) && this.appliedModel !== this.model;
  }

  _writeBatch(items) {
    if (this.settingsUpdate || (!this.turnActive && this._settingsPending())) {
      this.queue.push(items);
      this._applySettings();
      return;
    }
    // OpenCode serializes prompts on one session: concurrent session/prompt
    // calls do not steer the live turn, they only queue server-side. The state
    // machine routes mid-turn inputs into this.queue, so reaching here while a
    // turn is active is a bug: park the items and let the turn end drain them.
    if (this.turnActive) {
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
    this.needsHistoryPrefix = false;
    this.deliveredThroughSeq = Math.max(this.deliveredThroughSeq, prepared.deliveredThroughSeq);
    const input = items.map((item) => item.text).join('\n');
    this._recordActivity('agent.turn_started', {
      inboundSeq: prepared.seqs.at(-1) ?? null,
      metadata: {
        runtime: OPENCODE_RUNTIME.id,
        model: this.resolvedModel || undefined,
        input,
        historyChars: Math.max(0, prepared.body.length - input.length),
        inboundSeqs: prepared.seqs,
      },
    });
    // session/prompt resolves when the turn ends (stopReason end_turn on
    // success); a process/error rejection fails the active turn. A turn has no
    // deadline: a non-finite timeout arms no timer (a huge finite one would
    // overflow setTimeout and fire after 1 ms).
    this._request('session/prompt', {
      sessionId: this.sessionId,
      prompt: [{ type: 'text', text: prepared.body }],
    }, Infinity).then(
      (result) => {
        if (this.activeOwner !== owner) return;
        // ACP stop reasons: end_turn, max_tokens, max_turn_requests, refusal,
        // cancelled. Only end_turn is a normal finish.
        const failed = result?.stopReason && result.stopReason !== 'end_turn'
          ? new Error(`opencode turn ended: ${result.stopReason}`)
          : null;
        this._completeTurn(failed);
      },
      (error) => {
        if (this.activeOwner !== owner) return;
        this._completeTurn(error);
      },
    );
  }

  _applySettings() {
    if (this.settingsUpdate) return;
    const model = this.model;
    this.settingsUpdate = (async () => {
      try {
        if (this._settingsPending() && this.connected && this.sessionId) {
          await this._applyModelOn(this.child, model);
          this.appliedModel = model;
        }
      } catch (error) {
        this.logger.warn('opencode_settings_update_failed', { error: safe(error) });
        this._failItems(this.queue.splice(0).flat(), error);
      } finally {
        this.settingsUpdate = null;
        this._drain();
        this._notifyAvailable();
      }
    })();
  }

  _onStdout(chunk, child) {
    try {
      this.stdoutBuffer = appendBounded(this.stdoutBuffer, chunk, 'stdout line');
    } catch (error) {
      this.turnError = error;
      this._terminateProcessTree(child, 'SIGKILL');
      return;
    }
    let index;
    while ((index = this.stdoutBuffer.indexOf('\n')) >= 0) {
      const line = this.stdoutBuffer.slice(0, index);
      this.stdoutBuffer = this.stdoutBuffer.slice(index + 1);
      if (!line.trim()) continue;
      try { this._handleLine(line); } catch (error) {
        this.logger.warn('opencode_line_error', { error: safe(error) });
      }
    }
  }

  _onStderr(chunk) {
    try {
      this.stderrBuffer = appendBounded(this.stderrBuffer, chunk, 'stderr line');
    } catch {
      this.stderrBuffer = this.stderrBuffer.slice(-4_000);
    }
    let index;
    while ((index = this.stderrBuffer.indexOf('\n')) >= 0) {
      const line = this.stderrBuffer.slice(0, index);
      this.stderrBuffer = this.stderrBuffer.slice(index + 1);
      if (line.trim()) this.logger.debug('opencode_stderr', { detail: safe(line.slice(0, 500)) });
    }
  }

  _handleLine(line) {
    let message;
    try { message = JSON.parse(line); } catch { return; }
    if (!message || typeof message !== 'object') return;

    if (message.id !== undefined && message.method === undefined) {
      const pending = this.pendingRpc.get(message.id);
      if (!pending) return;
      this.pendingRpc.delete(message.id);
      if (pending.timer) clearTimeout(pending.timer);
      if (message.error) {
        const error = new Error(message.error.message || 'opencode rpc error');
        error.data = message.error.data;
        pending.reject(error);
      } else {
        pending.resolve(message.result);
      }
      return;
    }

    if (typeof message.method === 'string') this._handleAgentMessage(message);
  }

  _handleAgentMessage(message) {
    const { method, params } = message;
    if (message.id !== undefined && method === 'session/request_permission') {
      this.logger.warn('opencode_interaction_declined', { reason: 'unattended' });
      this._recordActivity('agent.interaction_declined', {
        summary: 'session/request_permission',
        metadata: { runtime: this.runtime, method: 'session/request_permission' },
      });
      const options = params?.options ?? [];
      const rejectOption = options.find((option) => String(option.kind ?? '').includes('reject'));
      this._send({
        id: message.id,
        result: rejectOption
          ? { outcome: { outcome: 'selected', optionId: rejectOption.optionId } }
          : { outcome: { outcome: 'cancelled' } },
      });
      return;
    }
    if (message.id !== undefined) {
      this._send({
        id: message.id,
        error: { code: -32601, message: 'This unattended daemon does not support this request.' },
      });
      return;
    }
    if (method !== 'session/update') return;
    const update = params?.update;
    if (!update || typeof update !== 'object') return;
    if (params.sessionId && this.sessionId && params.sessionId !== this.sessionId) return;
    this._handleSessionUpdate(update);
  }

  _handleSessionUpdate(update) {
    const kind = update.sessionUpdate;
    if (kind === 'agent_thought_chunk') {
      if (!this.modelWorking && this.turnActive) {
        this.modelWorking = true;
        this.modelStartedAtMs = Date.now();
      }
      return;
    }
    if (kind === 'agent_message_chunk') {
      if (!this.modelWorking && this.turnActive) {
        this.modelWorking = true;
        this.modelStartedAtMs = Date.now();
      }
      // ACP streams the text in token-sized chunks; keep one row per message.
      const text = update.content?.text;
      if (text) this.noteText += String(text);
      return;
    }
    if (kind === 'tool_call') {
      this._flushNote();
      this.modelWorking = false;
      this.modelStartedAtMs = null;
      const toolCallId = String(update.toolCallId ?? randomUUID());
      const started = {
        name: String(update.title ?? 'unknown tool'),
        atMs: Date.now(),
        recorded: false,
      };
      this.toolStarts.set(toolCallId, started);
      // OpenCode announces a call as pending with an empty rawInput and sends
      // the arguments in the in_progress update: record it once, with them.
      if (hasToolInput(update.rawInput)) this._recordToolCalled(toolCallId, started, update);
      return;
    }
    if (kind === 'tool_call_update') {
      const toolCallId = String(update.toolCallId ?? '');
      const started = this.toolStarts.get(toolCallId);
      if (started && !started.recorded && (update.status !== 'pending' || hasToolInput(update.rawInput))) {
        this._recordToolCalled(toolCallId, started, update);
      }
      if (started && (update.status === 'completed' || update.status === 'error' || update.error)) {
        this.toolStarts.delete(toolCallId);
        const failed = update.status === 'error' || Boolean(update.error);
        this._recordActivity('agent.tool_completed', {
          inboundSeq: this._activeSeqs().at(-1) ?? null,
          status: failed ? 'FAILED' : 'COMPLETED',
          summary: started.name,
          metadata: {
            runtime: OPENCODE_RUNTIME.id,
            toolId: toolCallId,
            toolName: started.name,
            durationMs: Date.now() - started.atMs,
            ...(failed
              ? { error: activityPayload(update.error ?? update.content) }
              : { output: activityPayload(update.content) }),
          },
        });
        this.modelWorking = true;
        this.modelStartedAtMs = Date.now();
      }
      return;
    }
    // usage_update, available_commands_update, plan_update: nothing to mirror.
  }

  _activeSeqs() {
    return [...new Set(
      this.activeItems.map((item) => item.seq).filter((seq) => seq != null),
    )];
  }

  _recordToolCalled(toolCallId, started, update) {
    started.recorded = true;
    this._recordActivity('agent.tool_called', {
      inboundSeq: this._activeSeqs().at(-1) ?? null,
      summary: String(update.title ?? started.name),
      status: 'RUNNING',
      metadata: {
        runtime: OPENCODE_RUNTIME.id,
        toolId: toolCallId,
        toolName: started.name,
        input: activityPayload(update.rawInput),
      },
    });
  }

  // Telemetry only: OpenCode must use a delivery tool to reach a person.
  _flushNote() {
    const text = this.noteText;
    this.noteText = '';
    if (!text.trim()) return;
    this._recordActivity('agent.note', {
      inboundSeq: this._activeSeqs().at(-1) ?? null,
      summary: text,
      metadata: { runtime: OPENCODE_RUNTIME.id, delivery: 'telemetry_only' },
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
        runtime: OPENCODE_RUNTIME.id,
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
      this.logger.info('opencode_session_acceptance_stale', { acceptedThroughSeq });
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
        runtime: OPENCODE_RUNTIME.id,
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
    if (this.turnActive) return;
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
      if (marked === false) this.logger.info('opencode_session_unresumable_stale');
    } catch (error) {
      this.logger.warn('opencode_session_unresumable_failed', { error: safe(error) });
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
        if (!published) this.logger.info('opencode_session_publish_stale');
        return;
      }
      this.store.setGlobalSession?.(this.sessionId, this.policyHash, {
        runtime: this.runtime,
        epoch: this.cognitionEpoch,
        syncedThroughSeq: this.syncedThroughSeq,
        sessionReady: this.sessionInitialized,
      });
    } catch (error) {
      this.logger.warn('opencode_session_persist_failed', { error: safe(error) });
    }
  }

  _handleExit(child, code, signal, processError = null) {
    if (this.child !== child) return;
    this.child = null;
    this.connected = false;
    for (const pending of this.pendingRpc.values()) {
      if (pending.timer) clearTimeout(pending.timer);
      try { pending.reject(new Error('opencode connection lost')); } catch {}
    }
    this.pendingRpc.clear();
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
        ?? new Error(`opencode connection lost (code ${code}, signal ${signal})`));
    }
    this.turnError = null;
    if (this.stopping || !this.startedOk || !this.autoReconnect) {
      this._resolveIdleWaiters();
      return;
    }
    this.logger.warn('opencode_disconnected', { code, signal, error: processError ? safe(processError) : undefined });
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
        this.logger.warn('opencode_reconnect_failed', { error: safe(error), backoffMs: this.backoffMs });
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
    for (const pending of this.pendingRpc.values()) {
      if (pending.timer) clearTimeout(pending.timer);
      try { pending.reject(new Error('opencode process replaced')); } catch {}
    }
    this.pendingRpc.clear();
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
    this.resumeRequested = previous.resumeRequested;
    this.needsHistoryPrefix = previous.needsHistoryPrefix;
    this.syncedThroughSeq = previous.syncedThroughSeq;
    this.deliveredThroughSeq = previous.deliveredThroughSeq;
    this.model = previous.model;
    if (previous.sessionId) await this._spawnPersistent();
    this._drain();
    this._notifyAvailable();
  }

  // Prepare a fresh ACP session while idle. The live process is replaced now;
  // nothing is published or drained until the coordinator's SQLite CAS commits.
  // On CAS failure cancelSessionRotation reopens the prior session.
  async prepareSessionRotation({ rollback = {} } = {}) {
    while (this.settingsUpdate) await this.settingsUpdate;
    if (this.turnActive) throw new Error('opencode session rotation requires an idle runtime');
    if (this.sessionRotation) throw new Error('opencode session rotation is already prepared');
    const previous = Object.freeze({
      sessionId: this.sessionId,
      sessionInitialized: this.sessionInitialized,
      resumeRequested: this.resumeRequested,
      needsHistoryPrefix: this.needsHistoryPrefix,
      syncedThroughSeq: this.syncedThroughSeq,
      deliveredThroughSeq: this.deliveredThroughSeq,
      model: rollback.model ?? this.model,
    });
    this.sessionRotation = { id: null, previous };
    try {
      await this._replaceProcess({ fresh: true, persist: false, activate: false });
      this.sessionRotation.id = this.sessionId;
      return Object.freeze({ id: this.sessionId, sessionReady: this.sessionInitialized });
    } catch (error) {
      try { await this._restorePreparedSession(previous); } catch (restoreError) {
        this.logger.warn('opencode_session_prepare_restore_failed', { error: safe(restoreError) });
      }
      this.sessionRotation = null;
      throw error;
    }
  }

  commitSessionRotation(prepared, { persist = true, preserveContext = false } = {}) {
    if (!prepared?.id || this.sessionRotation?.id !== prepared.id || this.sessionId !== prepared.id) {
      throw new Error('prepared opencode session is no longer current');
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

  // Loop breaker: ACP cancel on the live prompt, keeping the process, the
  // session and the turn's state. session/prompt then resolves (stopReason
  // cancelled) and _completeTurn ends the turn as usual, draining the queue
  // into the same session.
  cancelTurn() {
    if (!this.turnActive || !this.sessionId || !this.child?.stdin?.writable) return false;
    this._send({ method: 'session/cancel', params: { sessionId: this.sessionId } });
    return true;
  }

  // Never wait for a stuck turn: notify ACP cancel best-effort, then the caller
  // (runtime switch) replaces the process. Active items are abandoned, not
  // failed; their inbound rows stay in the cognition journal.
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
    if (this.sessionId) this._send({ method: 'session/cancel', params: { sessionId: this.sessionId } });
    this._resolveIdleWaiters();
  }

  async rotateSession({ resume = false } = {}) {
    while (this.settingsUpdate) await this.settingsUpdate;
    if (this.turnActive) throw new Error('opencode session rotation requires an idle runtime');
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

  // Takes effect before the next prompt (see _settingsPending).
  setModel(model, { persist = true } = {}) {
    if (persist) this.store.setSetting?.('opencode_model', model);
    this.model = model;
  }

  setEffort() {
    throw new Error('opencode does not support daemon-side effort control');
  }

  // Selectable models come from session/new's model config option.
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
      this.logger.warn('opencode_available_callback_failed', { error: safe(error) });
    }
  }

  _recordActivity(kind, fields = {}) {
    try {
      this.activitySeq += 1;
      this.store.recordActivity?.({
        eventKey: `opencode:${this.sessionId ?? 'na'}:${kind}:${this.activitySeq}`,
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
      this._failItems(queued, new Error('opencode stopped before queued work started'));
    }
    const items = this.activeItems.splice(0);
    this.activeOwner = null;
    const midTurn = this.turnActive;
    this.turnActive = false;
    if (items.length > 0) this._failItems(items, new Error('opencode stopped during an active turn'));
    if (midTurn) this._markSessionUnresumable();
    this._resolveIdleWaiters();
    await this._terminateCurrent();
  }
}
