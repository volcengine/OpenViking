// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { randomUUID } from 'node:crypto';
import { safeError as safe } from './logger.mjs';
import { activityPayload } from './activity.mjs';
import { agentToolEnv } from './actions/api.mjs';
import { RuntimeTurnBase, sanitizeRuntimeEnv } from './runtime-base.mjs';

export { sanitizeRuntimeEnv as sanitizeOpencodeEnv };

// JSON-RPC parse/request/method/params errors, and ACP's auth-required and
// resource-not-found: answered without running the prompt. -32603 is excluded
// because ACP servers like OpenCode return it for provider failures after the
// user message is already in the transcript.
const PROMPT_REFUSED_CODES = new Set([-32700, -32600, -32601, -32602, -32000, -32002]);

// Updates that prove a turn ran. Others (available_commands_update,
// config_option_update) arrive on their own after session/new.
const TURN_UPDATE_KINDS = new Set([
  'user_message_chunk', 'agent_message_chunk', 'agent_thought_chunk', 'tool_call', 'tool_call_update',
]);

function hasToolInput(rawInput) {
  return rawInput != null && (typeof rawInput !== 'object' || Object.keys(rawInput).length > 0);
}

/**
 * Neutral Agent Client Protocol (ACP, nd-JSON over stdio) persistent adapter.
 * Subclasses (opencode, agy, copilot) specialize launch args/env, session params,
 * initialize identity checks, and model/effort wire calls.
 */
export class AcpAgent extends RuntimeTurnBase {
  constructor(config, store, logger, options = {}) {
    super(config, store, logger, {
      ...options,
      rpcPrefix: options.rpcPrefix ?? 'oc',
    });
    // The session/load in progress, tied to its process so that a dead
    // process's cleanup cannot end a newer load's replay.
    this.replay = null;
    this.replayedChunks = null;
  }

  _agentToolEnv() {
    return agentToolEnv(this.config, this.authorityToken);
  }

  _handshakeTimeoutMessage() {
    return `${this.runtimeLabel} ACP start timed out`;
  }

  _initializeParams() {
    return {
      protocolVersion: 1,
      clientCapabilities: { fs: { readTextFile: false, writeTextFile: false }, terminal: false },
      clientInfo: { name: 'ov-superagent', version: '1' },
    };
  }

  _assertAgent() {
    // Subclasses validate agentInfo / capabilities when required.
  }

  _sessionParams() {
    return { mcpServers: [] };
  }

  _isLoadFatal() {
    return false;
  }

  _captureModelOptions() {
    // Subclasses parse available models and effort capabilities.
  }

  async _applyModelOn() {
    // Subclasses issue session/set_config_option or session/set_model.
  }

  async _applySettingsOn(child, { model, effort }) {
    if (model && this.appliedModel !== model) {
      await this._applyModelOn(child, model);
      this.appliedModel = model;
    }
    if (typeof this._applyEffortOn === 'function' && effort !== undefined && this.appliedEffort !== effort) {
      await this._applyEffortOn(child, effort);
      this.appliedEffort = effort;
    }
  }

  async _onChildConnected(child, { model, effort, isSettled }) {
    const result = await this._requestOn(child, 'initialize', this._initializeParams(), this.startTimeoutMs);
    this._assertAgent(result);
    if (this.resumeRequested && this.sessionId) {
      try {
        const loaded = await this._loadSession(child);
        this._captureModelOptions(loaded);
        this.sessionInitialized = true;
        this.needsHistoryPrefix = await this._loadNeedsHistory();
      } catch (error) {
        if (isSettled() || this.child !== child) throw error;
        if (this._isLoadFatal(error)) throw error;
        this.logger.warn(`${this.logPrefix}_session_load_failed_fresh_start`, { error: safe(error) });
        this.sessionId = null;
        this.sessionInitialized = false;
        await this.store.prepareOVContext?.(this.cognitionEpoch);
        await this._openSession(child);
      }
    } else {
      await this._openSession(child);
    }
    // Apply configured model/effort before the stability window resolves start().
    if (model) await this._applyModelOn(child, model);
    this.appliedModel = model;
    if (typeof this._applyEffortOn === 'function' && effort) {
      await this._applyEffortOn(child, effort);
    }
    this.appliedEffort = effort;
  }

  async _loadSession(child) {
    const replay = { child, chunks: 0 };
    this.replay = replay;
    try {
      const loaded = await this._requestOn(child, 'session/load', {
        sessionId: this.sessionId,
        cwd: this.config.workspace,
        ...this._sessionParams(),
      });
      this.replayedChunks = replay.chunks;
      return loaded;
    } finally {
      if (this.replay === replay) this.replay = null;
    }
  }

  async _openSession(child) {
    const created = await this._requestOn(child, 'session/new', {
      cwd: this.config.workspace,
      ...this._sessionParams(),
    });
    if (!created?.sessionId) throw new Error(`${this.runtimeLabel} session/new returned no session id`);
    this.sessionId = created.sessionId;
    this.sessionInitialized = true;
    this.resumeRequested = false;
    this.needsHistoryPrefix = true;
    this._captureModelOptions(created);
  }

  _submitTurn(prepared, owner, { hadPrefix }) {
    // session/prompt resolves when the turn ends; a non-finite timeout avoids
    // 32-bit setTimeout overflow.
    this._request('session/prompt', {
      sessionId: this.sessionId,
      prompt: [{ type: 'text', text: prepared.body }],
    }, Infinity).then(
      (result) => {
        if (this.activeOwner !== owner) return;
        const stopReason = this.cancelledOwner === owner ? 'cancelled' : result?.stopReason;
        const failed = stopReason && stopReason !== 'end_turn'
          ? new Error(`${this.runtimeLabel} turn ended: ${stopReason}`)
          : this._turnEndError();
        this._completeTurn(failed);
      },
      (error) => {
        if (this.activeOwner !== owner) return;
        if (!this.turnAcknowledged && this._promptRefused(error)) {
          this._restoreUnacceptedCatchup(hadPrefix);
        }
        this._completeTurn(error);
      },
    );
  }

  _turnEndError() {
    return null;
  }

  _promptRefused(error) {
    return !Number.isInteger(error?.code) || PROMPT_REFUSED_CODES.has(error.code);
  }

  _sendCancelNotification() {
    this._send({ method: 'session/cancel', params: { sessionId: this.sessionId } });
  }

  _handleAgentMessage(message) {
    const { method, params } = message;
    if (message.id !== undefined && method === 'session/request_permission') {
      this.logger.warn(`${this.logPrefix}_interaction_declined`, { reason: 'unattended' });
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
    if (method !== 'session/update') {
      this._handleCustomNotification?.(method, params);
      return;
    }
    const update = params?.update;
    if (!update || typeof update !== 'object') return;
    if (params.sessionId && this.sessionId && params.sessionId !== this.sessionId) return;
    this._handleSessionUpdate(update);
  }

  _handleSessionUpdate(update) {
    const kind = update.sessionUpdate;
    if (this.replay) {
      if (kind === 'user_message_chunk' || kind === 'agent_message_chunk') this.replay.chunks += 1;
      return;
    }
    if (this.turnActive && TURN_UPDATE_KINDS.has(kind)) this.turnAcknowledged = true;
    if (kind === 'config_option_update') {
      this._handleConfigOptionUpdate?.(update);
      return;
    }
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
        name: this._toolName(update),
        atMs: Date.now(),
        recorded: false,
      };
      this.toolStarts.set(toolCallId, started);
      if (update.status !== 'pending' && hasToolInput(update.rawInput)) {
        this._recordToolCalled(toolCallId, started, update);
      }
      return;
    }
    if (kind === 'tool_call_update') {
      const toolCallId = String(update.toolCallId ?? '');
      const started = this.toolStarts.get(toolCallId);
      if (started && !started.recorded && update.status !== 'pending') {
        this._recordToolCalled(toolCallId, started, update);
      }
      const outcome = started ? this._toolOutcome(update) : null;
      if (outcome) {
        this.toolStarts.delete(toolCallId);
        const failed = outcome === 'failed';
        this._recordActivity('agent.tool_completed', {
          inboundSeq: this._activeSeqs().at(-1) ?? null,
          status: failed ? 'FAILED' : 'COMPLETED',
          summary: started.name,
          metadata: {
            runtime: this.runtime,
            toolId: toolCallId,
            toolName: started.name,
            durationMs: Date.now() - started.atMs,
            ...(failed
              ? { error: activityPayload(update.error ?? this._toolOutput(update)) }
              : { output: activityPayload(this._toolOutput(update)) }),
          },
        });
        this.modelWorking = true;
        this.modelStartedAtMs = Date.now();
      }
    }
  }

  _toolName(update) {
    return String(update.name ?? update.title ?? 'unknown tool');
  }

  _toolOutcome(update) {
    if (update.status === 'failed' || update.status === 'error' || update.error) return 'failed';
    return update.status === 'completed' ? 'completed' : null;
  }

  _toolOutput(update) {
    return Array.isArray(update.content) && update.content.length > 0 ? update.content : update.rawOutput ?? update.content;
  }

  _recordToolCalled(toolCallId, started, update) {
    started.recorded = true;
    this._recordActivity('agent.tool_called', {
      inboundSeq: this._activeSeqs().at(-1) ?? null,
      summary: String(update.title ?? started.name),
      status: 'RUNNING',
      metadata: {
        runtime: this.runtime,
        toolId: toolCallId,
        toolName: started.name,
        input: activityPayload(update.rawInput),
      },
    });
  }
}
