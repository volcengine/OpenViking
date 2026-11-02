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
import { deliveryItem, receiptIds, withReceiptIds } from './runtime-input.mjs';
import { getRuntimeDefinition, isModelUnavailableError } from './runtime-catalog.mjs';

const MAX_LINE_BYTES = 5_000_000;
const START_TIMEOUT_MS = 5_000;
const START_STABILITY_MS = 250;
const RECONNECT_BASE_MS = 1_000;
const RECONNECT_CAP_MS = 30_000;

const CLAUDE_RUNTIME = getRuntimeDefinition('claude');

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function appendBounded(current, chunk, label) {
  const next = current + chunk;
  if (Buffer.byteLength(next, 'utf8') > MAX_LINE_BYTES) {
    throw new Error(`claude ${label} exceeded local limit`);
  }
  return next;
}

export function sanitizeClaudeEnv(environment, profile = '', actions = null, config = {}) {
  return sanitizeAgentChildEnv(environment, { profile, stripClaudeCode: true, actions, cliBins: agentCliCommands(config).map(({ bin }) => bin) });
}

// Persistent Anthropic stream-json adapter, adapted from zouk-daemon v0.6.0's
// MIT-licensed Claude driver. The daemon-specific state machine below keeps one
// global runtime, journal cursor, and tool authority fence; it does not reuse
// Zouk's per-agent/server topology.
export class ClaudeAgent {
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
    this.startStabilityMs = options.startStabilityMs ?? START_STABILITY_MS;
    this.reconnectBaseMs = options.reconnectBaseMs ?? RECONNECT_BASE_MS;
    this.reconnectCapMs = options.reconnectCapMs ?? RECONNECT_CAP_MS;

    const cognition = store.getCognitionState?.()
      ?? { runtime: CLAUDE_RUNTIME.id, epoch: 1, authorityToken: null };
    this.runtime = CLAUDE_RUNTIME.id;
    this.capabilities = CLAUDE_RUNTIME.capabilities;
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
      ?? store.getSetting?.('claude_model')
      ?? options.initialModel
      ?? config.claudeModel
      ?? '';
    this.effort = options.effort
      ?? store.getSetting?.('claude_effort')
      ?? config.claudeEffort
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
    this.idleWaiters = [];
    this.toolStarts = new Map(); // tool_use id -> name and Activity start time
    this.mcpTempDir = null;
    this.sessionRotation = null;
    this.appliedModel = null;
    this.appliedEffort = null;
    this.settingsUpdate = null;
    this.modelCatalog = [];
    this.catalogRequest = null;
    this.pendingControl = new Map();
    this.controlTimeoutMs = options.controlTimeoutMs ?? 15_000;
    this.actualModel = '';
  }

  get threadId() { return this.sessionId; }

  get resolvedModel() {
    if (this.appliedModel === this.model && this.actualModel) return this.actualModel;
    return this.modelCatalog.find((entry) => entry.id === (this.model || 'default'))?.resolvedModel
      || this.model;
  }

  get policyHash() {
    return createHash('sha256')
      .update(this.developerInstructionsText).update('\0')
      .update(this.config.workspace).update('\0')
      .update('claude-stream-json-v1').digest('hex');
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
    if (!/Claude Code/i.test(version.stdout)) {
      throw new Error('claude preflight returned an unrecognised version');
    }
    const help = await this._probe(['--help']);
    for (const flag of [
      '--input-format', '--output-format', '--resume', '--session-id', '--mcp-config',
      '--dangerously-skip-permissions', '--disallowed-tools', '--effort',
      '--append-system-prompt', '--no-chrome', '--model', '--print', '--permission-prompts',
    ]) {
      if (!help.stdout.includes(flag)) {
        const hint = flag === '--permission-prompts' ? '; upgrade Claude Code to 2.1.259 or newer for unattended operation' : '';
        throw new Error(`claude preflight is missing required flag ${flag}${hint}`);
      }
    }
    const auth = await this._probe(['auth', 'status', '--json']);
    let status;
    try { status = JSON.parse(auth.stdout); } catch {
      throw new Error('claude auth status returned invalid JSON');
    }
    if (status?.loggedIn !== true) throw new Error('claude is not logged in');
  }

  _probe(args) {
    return new Promise((resolve, reject) => {
      let child;
      try {
        child = this._spawn(this.config.claudeBin, args, {
          cwd: this.config.workspace,
          env: sanitizeClaudeEnv(process.env, this.config.profile, agentToolEnv(this.config, this.authorityToken), this.config),
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
        finish(new Error(`claude preflight timed out: ${args.join(' ')}`));
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
        else finish(new Error(`claude preflight failed: ${safe(stderr || stdout || `exit ${code}`)}`));
      });
    });
  }

  _writeMcpConfig() {
    this._cleanupMcpConfig();
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-claude-mcp-'));
    try { fs.chmodSync(directory, 0o700); } catch {}
    const configPath = path.join(directory, 'mcp-config.json');
    fs.writeFileSync(configPath, JSON.stringify({
      mcpServers: { personal_agent: agentToolServer(this.config, this.authorityToken) },
    }), { encoding: 'utf8', mode: 0o600 });
    this.mcpTempDir = directory;
    return configPath;
  }

  _cleanupMcpConfig() {
    const directory = this.mcpTempDir;
    this.mcpTempDir = null;
    if (!directory) return;
    try { fs.rmSync(directory, { recursive: true, force: true }); } catch {}
  }

  async _spawnPersistent() {
    if (this.needsHistoryPrefix) await this.store.prepareOVContext?.(this.cognitionEpoch);
    const model = this.model;
    const effort = this.effort;
    return new Promise((resolve, reject) => {
      const mcpConfigPath = this._writeMcpConfig();
      const args = [
        '--print',
        '--dangerously-skip-permissions',
        // Supported since Claude Code 2.1.259. Unanswered user-input and MCP
        // elicitation requests are denied/cancelled by the runtime itself.
        '--permission-prompts', 'none',
        '--verbose',
        '--output-format', 'stream-json',
        '--input-format', 'stream-json',
        // No --strict-mcp-config: user-scope skill/MCP servers (OpenViking via
        // OPENVIKING_* env) load alongside personal_agent.
        '--mcp-config', mcpConfigPath,
        '--disallowed-tools', 'EnterPlanMode,ExitPlanMode',
        '--append-system-prompt', this.developerInstructionsText,
        '--no-chrome',
      ];
      if (effort) args.push('--effort', effort);
      if (model) args.push('--model', model);
      if (this.resumeRequested && this.sessionInitialized) args.push('--resume', this.sessionId);
      else args.push('--session-id', this.sessionId);

      let child;
      try {
        child = this._spawn(this.config.claudeBin, args, {
          cwd: this.config.workspace,
          env: sanitizeClaudeEnv(process.env, this.config.profile, agentToolEnv(this.config, this.authorityToken), this.config),
          shell: false,
          detached: process.platform !== 'win32',
          stdio: ['pipe', 'pipe', 'pipe'],
        });
      } catch (error) {
        this._cleanupMcpConfig();
        reject(error);
        return;
      }

      this.child = child;
      this.catalogRequest = null;
      this.actualModel = '';
      this.stdoutBuffer = '';
      this.stderrBuffer = '';
      let spawned = false;
      let ready = false;
      let settled = false;
      let stabilityTimer = null;
      let startupStderr = '';
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        this.child = null;
        terminateProcessTree(child, 'SIGKILL');
        this._cleanupMcpConfig();
        reject(new Error('claude stream-json process start timed out'));
      }, this.startTimeoutMs);
      timer.unref?.();

      const failStart = (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (stabilityTimer) clearTimeout(stabilityTimer);
        this.child = null;
        this.connected = false;
        terminateProcessTree(child, 'SIGKILL');
        this._cleanupMcpConfig();
        reject(error);
      };

      child.stdin?.on('error', (error) => {
        if (this.child === child) this.logger.warn('claude_stdin_error', { error: safe(error) });
      });
      child.stdout?.setEncoding('utf8');
      child.stderr?.setEncoding('utf8');
      child.stdout?.on('data', (chunk) => {
        if (this.child === child) this._onStdout(chunk, child);
      });
      child.stderr?.on('data', (chunk) => {
        if (!ready) startupStderr = (startupStderr + chunk).slice(-4_000);
        if (this.child === child) this._onStderr(chunk);
      });
      child.once('spawn', () => {
        if (settled) return;
        spawned = true;
        // spawn only proves exec(2) succeeded. Hold a short no-input window so
        // invalid model/flag combinations can fail before the authority commit.
        stabilityTimer = setTimeout(() => {
          if (settled || child.exitCode !== null) return;
          ready = true;
          settled = true;
          clearTimeout(timer);
          this.connected = true;
          this.appliedModel = model;
          this.appliedEffort = effort;
          resolve();
        }, this.startStabilityMs);
      });
      child.once('error', (error) => {
        if (!ready) failStart(error);
        else this._handleExit(child, null, null, error);
      });
      child.once('exit', (code, signal) => {
        if (!ready) {
          const error = new Error(`claude exited during startup (code ${code}, signal ${signal})`);
          if (isModelUnavailableError({ message: startupStderr })) error.code = 'MODEL_UNAVAILABLE';
          failStart(error);
          return;
        }
        this._handleExit(child, code, signal);
      });
    });
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
    const message = {
      type: 'user',
      message: { role: 'user', content: [{ type: 'text', text: prepared.body }] },
    };
    if (this.sessionInitialized || this.resumeRequested) message.session_id = this.sessionId;

    const wasActive = this.turnActive;
    const owner = wasActive ? this.activeOwner : Object.freeze({
      runtime: this.runtime,
      authorityToken: this.authorityToken,
      sessionId: this.sessionId,
      epoch: this.cognitionEpoch,
    });
    try {
      this.child.stdin.write(`${JSON.stringify(message)}\n`);
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
        runtime: CLAUDE_RUNTIME.id,
        model: this.appliedModel || undefined,
        effort: this.appliedEffort || undefined,
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
        this.logger.warn('claude_line_error', { error: safe(error) });
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
      if (line.trim()) this.logger.debug('claude_stderr', { detail: safe(line.slice(0, 500)) });
    }
  }

  _handleLine(line) {
    let message;
    try { message = JSON.parse(line); } catch { return; }
    if (!message || typeof message !== 'object') return;

    if (message.type === 'control_response') {
      const response = message.response;
      const pending = this.pendingControl.get(response?.request_id);
      if (pending) {
        this.pendingControl.delete(response.request_id);
        if (response.subtype === 'error') pending.reject(new Error(response.error || 'Claude control request failed'));
        else pending.resolve(response.response);
      }
      return;
    }

    if (message.type === 'control_request' && message.request_id != null) {
      const subtype = message.request?.subtype;
      const reason = 'This unattended daemon cannot answer interactive requests. Ask the user through the messaging tools instead.';
      // No permission host is configured, but answer unexpected control
      // requests explicitly instead of silently leaving the CLI waiting.
      const response = subtype === 'can_use_tool'
        ? { subtype: 'success', request_id: message.request_id, response: { behavior: 'deny', message: reason } }
        : { subtype: 'error', request_id: message.request_id, error: reason };
      this.logger.warn('claude_control_request_declined', { subtype: safe(subtype ?? 'unknown'), reason: 'unattended' });
      this._recordActivity('agent.interaction_declined', {
        summary: safe(subtype ?? 'unknown'), metadata: { runtime: 'claude', method: safe(subtype ?? 'unknown') },
      });
      try { this.child?.stdin.write(`${JSON.stringify({ type: 'control_response', response })}\n`); } catch (error) {
        this.logger.warn('claude_control_response_failed', { error: safe(error) });
      }
      return;
    }

    // --permission-prompts none: the CLI denied a prompt itself and told the
    // model nobody can approve it.
    if (message.type === 'system' && message.subtype === 'permission_denied') {
      this.logger.warn('claude_interaction_denied', { reason: 'unattended' });
      this._recordActivity('agent.interaction_declined', {
        summary: 'permission_denied', metadata: { runtime: 'claude', method: 'permission_denied' },
      });
      return;
    }

    if (message.type === 'system' && message.subtype === 'init') {
      if (typeof message.model === 'string') this.actualModel = message.model;
      const sessionId = message.session_id ?? message.sessionId;
      if (typeof sessionId === 'string' && sessionId) {
        this.sessionId = sessionId;
        this.sessionInitialized = true;
        this.resumeRequested = true;
        this._persistSession();
      }
      return;
    }

    if (message.type === 'assistant') {
      const blocks = Array.isArray(message.message?.content) ? message.message.content : [];
      for (const block of blocks) {
        if (!block || typeof block !== 'object') continue;
        if (['thinking', 'text'].includes(block.type) && !this.modelWorking) {
          this.modelWorking = true;
          this.modelStartedAtMs = Date.now();
        }
        if (block.type === 'thinking') {
          // Hidden thinking is intentionally not persisted into the daemon journal;
          // the local Activity page still shows it.
          this._recordActivity('agent.thinking', {
            inboundSeq: this._activeSeqs().at(-1) ?? null,
            metadata: { runtime: CLAUDE_RUNTIME.id, thinking: block.thinking || undefined },
          });
        } else if (block.type === 'text' && block.text) {
          // Telemetry only. Claude must call a delivery tool to reach a user.
          this._recordActivity('agent.note', {
            inboundSeq: this._activeSeqs().at(-1) ?? null,
            summary: String(block.text),
            metadata: { runtime: CLAUDE_RUNTIME.id, delivery: 'telemetry_only' },
          });
        } else if (block.type === 'tool_use') {
          this.modelWorking = false;
          this.modelStartedAtMs = null;
          this.toolStarts.set(block.id, { name: String(block.name ?? 'unknown tool'), atMs: Date.now() });
          this._recordActivity('agent.tool_called', {
            inboundSeq: this._activeSeqs().at(-1) ?? null,
            summary: String(block.name ?? 'unknown tool'),
            status: 'RUNNING',
            metadata: { runtime: CLAUDE_RUNTIME.id, toolId: block.id, toolName: block.name, input: activityPayload(block.input) },
          });
        }
      }
      return;
    }

    if (message.type === 'user') {
      const blocks = Array.isArray(message.message?.content) ? message.message.content : [];
      for (const block of blocks) {
        if (block?.type !== 'tool_result') continue;
        const started = this.toolStarts.get(block.tool_use_id);
        this.toolStarts.delete(block.tool_use_id);
        const output = activityPayload(block.content);
        this._recordActivity('agent.tool_completed', {
          inboundSeq: this._activeSeqs().at(-1) ?? null,
          status: block.is_error === true ? 'FAILED' : 'COMPLETED',
          summary: started?.name ?? (block.is_error === true ? 'Claude tool failed' : 'Claude tool completed'),
          metadata: {
            runtime: CLAUDE_RUNTIME.id,
            toolId: block.tool_use_id,
            toolName: started?.name,
            ...(started ? { durationMs: Date.now() - started.atMs } : {}),
            ...(block.is_error === true ? { error: output } : { output }),
          },
        });
      }
      if (blocks.some((block) => block?.type === 'tool_result')) {
        this.modelWorking = true;
        this.modelStartedAtMs = Date.now();
      }
      return;
    }

    if (message.type === 'result') {
      const subtype = typeof message.subtype === 'string' ? message.subtype : '';
      let error = this.turnError;
      if (!error && subtype && subtype !== 'success') {
        const details = Array.isArray(message.errors) ? message.errors.filter(Boolean).join(' | ') : '';
        error = new Error(details || String(message.result || `Claude error: ${subtype}`));
      }
      // Claude CLI reports some hard failures (usage/session limits, API
      // errors) as subtype "success" with is_error=true. Treating them as
      // completed turns silently swallowed every failure notice.
      if (!error && message.is_error === true) {
        error = new Error(String(message.result || 'Claude error result'));
      }
      this.turnError = null;
      const usage = message.usage ?? {};
      this.turnUsage = {
        inputTokens: Number.isFinite(usage.input_tokens)
          ? usage.input_tokens + (usage.cache_read_input_tokens ?? 0) + (usage.cache_creation_input_tokens ?? 0)
          : undefined,
        cachedInputTokens: usage.cache_read_input_tokens,
        outputTokens: usage.output_tokens,
        costUsd: message.total_cost_usd,
      };
      this._completeTurn(error);
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
    this.toolStarts.clear();
    this.deliveredThroughSeq = this.syncedThroughSeq;
    this._recordActivity('agent.turn_completed', {
      inboundSeq: seqs.at(-1) ?? null,
      status: error ? 'failed' : 'completed',
      summary: error ? safe(error) : undefined,
      metadata: {
        runtime: CLAUDE_RUNTIME.id,
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
      this.logger.info('claude_session_acceptance_stale', { acceptedThroughSeq });
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
        runtime: CLAUDE_RUNTIME.id,
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
    if (this.queue.length > 0 && !this.turnActive && this._settingsPending()) {
      this._applySettings();
      return;
    }
    while (this.queue.length > 0 && !this.settingsUpdate) this._writeBatch(this.queue.shift());
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
      if (marked === false) this.logger.info('claude_session_unresumable_stale');
    } catch (error) {
      this.logger.warn('claude_session_unresumable_failed', { error: safe(error) });
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
        if (!published) this.logger.info('claude_session_publish_stale');
        return;
      }
      this.store.setGlobalSession?.(this.sessionId, this.policyHash, {
        runtime: this.runtime,
        epoch: this.cognitionEpoch,
        syncedThroughSeq: this.syncedThroughSeq,
        sessionReady: this.sessionInitialized,
      });
    } catch (error) {
      this.logger.warn('claude_session_persist_failed', { error: safe(error) });
    }
  }

  _handleExit(child, code, signal, processError = null) {
    if (this.child !== child) return;
    this._rejectControl(new Error('Claude connection closed'));
    this.child = null;
    this.connected = false;
    if (!this.stopping) terminateProcessTree(child, 'SIGKILL');
    this._cleanupMcpConfig();
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
      // No result means the CLI never proved whether the active input reached
      // the native transcript. Reusing that transcript can duplicate the input;
      // rebuild it and perform one deliberate journal catch-up instead. The
      // same fresh-identity rule also avoids pre-init UUID collisions.
      // Demote the stored session too: if this daemon dies before the fresh
      // session publishes, the next generation must not resume the transcript
      // either (resume patches the dangling tool call and rewrites the whole
      // cached prompt prefix).
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
        ?? new Error(`claude connection lost (code ${code}, signal ${signal})`));
    }
    this.turnError = null;
    if (this.stopping || !this.startedOk || !this.autoReconnect) {
      this._resolveIdleWaiters();
      return;
    }
    this.logger.warn('claude_disconnected', { code, signal, error: processError ? safe(processError) : undefined });
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
        this.logger.warn('claude_reconnect_failed', { error: safe(error), backoffMs: this.backoffMs });
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

  // Claude CLI assigns the session before the first input. Replace the idle
  // process during prepare, but do not persist or drain until the coordinator's
  // SQLite CAS publishes the new epoch/session. On CAS failure cancel restores
  // the old session under the still-current authority token.
  async prepareSessionRotation({ rollback = {} } = {}) {
    while (this.settingsUpdate) await this.settingsUpdate;
    if (this.turnActive) throw new Error('claude session rotation requires an idle runtime');
    if (this.sessionRotation) throw new Error('claude session rotation is already prepared');
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
      return Object.freeze({ id: this.sessionId, sessionReady: false });
    } catch (error) {
      try { await this._restorePreparedSession(previous); } catch (restoreError) {
        this.logger.warn('claude_session_prepare_restore_failed', { error: safe(restoreError) });
      }
      this.sessionRotation = null;
      throw error;
    }
  }

  commitSessionRotation(prepared, { persist = true, preserveContext = false } = {}) {
    if (!prepared?.id || this.sessionRotation?.id !== prepared.id || this.sessionId !== prepared.id) {
      throw new Error('prepared claude session is no longer current');
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

  // /m must not wait for a stuck turn: kill the in-flight turn's process. The
  // abandoned items' inbound rows stay in the cognition journal; queued batches
  // survive in this.queue and are written after the next respawn. Intentional
  // abandonment, so onTurnFailed is not raised for the interrupted items.
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
    await this._terminateCurrent();
    this._resolveIdleWaiters();
  }

  async rotateSession({ resume = false } = {}) {
    while (this.settingsUpdate) await this.settingsUpdate;
    if (this.turnActive) throw new Error('claude session rotation requires an idle runtime');
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
    if (persist) this.store.setSetting?.('claude_model', model);
    this.model = model;
  }

  _settingsPending() {
    return this.appliedModel !== this.model || this.appliedEffort !== this.effort;
  }

  setEffort(effort, { persist = true } = {}) {
    if (persist) this.store.setSetting?.('claude_effort', effort);
    this.effort = effort;
  }

  _applySettings() {
    if (this.settingsUpdate) return;
    // Claude receives model and effort as process arguments. Wait for the next input
    // after the current result, then resume the same transcript with new args.
    this.settingsUpdate = (async () => {
      try {
        await this._replaceProcess({ fresh: false, activate: false });
      } catch (error) {
        this.logger.warn('claude_settings_update_failed', { error: safe(error) });
        this._failItems(this.queue.splice(0).flat(), error);
        if (!this.connected && this.autoReconnect && !this.stopping) void this._reconnectLoop();
      } finally {
        this.settingsUpdate = null;
        this._drain();
        this._notifyAvailable();
      }
    })();
  }

  async listModels() {
    if (!this.connected) return this.modelCatalog;
    if (!this.catalogRequest) {
      const child = this.child;
      this.catalogRequest = this._requestControl({ subtype: 'initialize' }).then((result) => {
        if (this.child !== child) return this.modelCatalog;
        if (!Array.isArray(result?.models)) throw new Error('Claude returned no model catalog');
        this.modelCatalog = result.models.filter((entry) => entry?.value && !entry.disabled).map((entry) => ({
          id: entry.value,
          displayName: entry.displayName,
          resolvedModel: entry.resolvedModel,
          isDefault: entry.value === 'default',
          ...(entry.defaultEffort ? { defaultEffort: entry.defaultEffort } : {}),
          effortsKnown: entry.supportsEffort === false || Array.isArray(entry.supportedEffortLevels),
          efforts: entry.supportsEffort === false ? [] : (entry.supportedEffortLevels ?? []),
        }));
        return this.modelCatalog;
      }).catch((error) => {
        if (this.child === child) this.catalogRequest = null;
        this.logger.info('claude_model_list_failed', { error: safe(error) });
        return this.modelCatalog;
      });
    }
    return this.catalogRequest;
  }

  _requestControl(request) {
    if (!this.child?.stdin?.writable) return Promise.reject(new Error('Claude is disconnected'));
    const requestId = randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pendingControl.delete(requestId);
        reject(new Error('Claude control request timed out'));
      }, this.controlTimeoutMs);
      this.pendingControl.set(requestId, {
        resolve: (value) => { clearTimeout(timer); resolve(value); },
        reject: (error) => { clearTimeout(timer); reject(error); },
      });
      try {
        this.child.stdin.write(`${JSON.stringify({ type: 'control_request', request_id: requestId, request })}\n`);
      } catch (error) {
        this.pendingControl.get(requestId)?.reject(error);
        this.pendingControl.delete(requestId);
      }
    });
  }

  _rejectControl(error) {
    for (const pending of this.pendingControl.values()) pending.reject(error);
    this.pendingControl.clear();
  }

  _recordActivity(kind, fields = {}) {
    try {
      this.activitySeq += 1;
      this.store.recordActivity?.({
        eventKey: `claude:${this.sessionId ?? 'na'}:${kind}:${this.activitySeq}`,
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
      this.logger.warn('claude_available_callback_failed', { error: safe(error) });
    }
  }

  async _terminateCurrent() {
    this._rejectControl(new Error('Claude process replaced'));
    const child = this.child;
    this.child = null;
    this.connected = false;
    if (!child?.pid) {
      this._cleanupMcpConfig();
      return;
    }
    if (child.exitCode !== null) {
      terminateProcessTree(child, 'SIGKILL');
      this._cleanupMcpConfig();
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
    this._cleanupMcpConfig();
    if (child.exitCode === null) throw new Error('claude process did not exit after SIGKILL');
  }

  async stop() {
    this.stopping = true;
    this.autoReconnect = false;
    await this.settingsUpdate;
    const midTurn = this.turnActive;
    const queued = this.queue.flat();
    this.queue = [];
    if (receiptIds(queued).length > 0) {
      this._failItems(queued, new Error('claude stopped before queued work started'));
    }
    const items = this.activeItems.splice(0);
    this.activeOwner = null;
    this.turnActive = false;
    if (items.length > 0) this._failItems(items, new Error('claude stopped during an active turn'));
    // Killed mid-turn: the next daemon generation must build a fresh session.
    // Resuming this transcript makes the CLI patch the interrupted tool call,
    // which mutates the message history and forces a full-prefix cache rewrite.
    if (midTurn) this._markSessionUnresumable();
    this._resolveIdleWaiters();
    await this._terminateCurrent();
  }
}
