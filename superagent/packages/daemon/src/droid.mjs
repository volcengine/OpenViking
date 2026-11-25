// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { randomUUID } from 'node:crypto';
import path from 'node:path';
import { activityPayload } from './activity.mjs';
import { agentToolEnv, agentToolServer } from './actions/api.mjs';
import { safeError as safe } from './logger.mjs';
import {
  DROID_API_VERSION,
  DROID_NOT_LOGGED_IN,
  DROID_PROTOCOL_VERSION,
  droidHandshakeProblem,
  normalizeDroidError,
  parseDroidModels,
} from './droid-runtime.mjs';
import { droidWorkspaceOpenVikingPieces, prepareDroidHome } from './ov-isolation.mjs';
import { RuntimeTurnBase } from './runtime-base.mjs';
import { compatibleModelEffort, currentModelEntry, getRuntimeDefinition } from './runtime-catalog.mjs';

const DROID_RUNTIME = getRuntimeDefinition('droid');
const PROMPT_REFUSED_CODES = new Set([-32700, -32600, -32601, -32602, -32000, -32002]);
const UNATTENDED_INTERACTION_METHODS = new Set([
  'droid.request_permission',
  'droid.ask_user',
  'droid.pick_options',
  'session/request_permission',
]);

function hasToolInput(value) {
  return value != null && (typeof value !== 'object' || Object.keys(value).length > 0);
}

function extractAssistantText(message) {
  if (!message || !Array.isArray(message.content)) return '';
  return message.content
    .filter((part) => part && part.type === 'text' && typeof part.text === 'string')
    .map((part) => part.text)
    .join('');
}

/**
 * Persistent Factory Droid CLI adapter (`droid exec --input-format stream-jsonrpc
 * --output-format stream-jsonrpc`). Isolates `.factory/` via FACTORY_HOME_OVERRIDE
 * while keeping HOME intact for macOS Login Keychain decryption of auth.v2.*.
 */
export class DroidAgent extends RuntimeTurnBase {
  constructor(config, store, logger, options = {}) {
    super(config, store, logger, {
      runtimeId: DROID_RUNTIME.id,
      runtimeLabel: 'droid',
      logPrefix: 'droid',
      binConfigKey: 'droidBin',
      modelConfigKey: 'droidModel',
      modelSettingKey: 'droid_model',
      effortConfigKey: 'droidEffort',
      effortSettingKey: 'droid_effort',
      policyVersion: 'droid-jsonrpc-v1',
      rpcPrefix: 'dr',
      ...options,
    });
    this.protocolVersion = DROID_PROTOCOL_VERSION;
    this.droidHomeDir = null;
    this.rawModels = [];
    this.replayCount = 0;
  }

  _agentToolEnv() {
    return agentToolEnv(this.config, this.authorityToken);
  }

  _handshakeTimeoutMessage() {
    return `${this.runtimeLabel} stream-jsonrpc start timed out`;
  }

  _transportOptions() {
    return {
      buildRequestFrame: ({ id, method, params }) => ({
        jsonrpc: '2.0',
        type: 'request',
        factoryApiVersion: DROID_API_VERSION,
        factoryProtocolVersion: this.protocolVersion,
        id,
        method,
        ...(params !== undefined ? { params } : {}),
      }),
      buildMessageFrame: (frame) => ({
        jsonrpc: '2.0',
        type: frame.type ?? (frame.id !== undefined ? 'response' : 'notification'),
        factoryApiVersion: DROID_API_VERSION,
        factoryProtocolVersion: this.protocolVersion,
        ...frame,
      }),
      onEnvelope: (message) => {
        if (typeof message.factoryProtocolVersion === 'string' && message.factoryProtocolVersion) {
          this.protocolVersion = message.factoryProtocolVersion;
        }
      },
    };
  }

  _resolveDroidHome() {
    if (this.config.dataDir) return path.join(this.config.dataDir, 'droid', 'home');
    return path.join(this._createRuntimeDir(), 'droid-home');
  }

  _writeRuntimeFiles() {
    this.droidHomeDir = prepareDroidHome(this._resolveDroidHome(), process.env, {
      workspace: this.config.workspace,
    });
    const pieces = droidWorkspaceOpenVikingPieces(this.config.workspace);
    if (pieces.length > 0) this.logger.warn('droid_workspace_openviking_pieces', { pieces });
    return this.droidHomeDir;
  }

  _spawnArgs() {
    return [
      'exec',
      '--input-format', 'stream-jsonrpc',
      '--output-format', 'stream-jsonrpc',
      '--cwd', this.config.workspace,
    ];
  }

  _spawnEnv() {
    return {
      ...super._spawnEnv(),
      // Redirects `.factory/` without overriding HOME, keeping macOS Login
      // Keychain auth.v2.loginkeychain decryption working.
      FACTORY_HOME_OVERRIDE: this.droidHomeDir,
    };
  }

  _mcpServers() {
    const tools = agentToolServer(this.config, this.authorityToken);
    return [{
      name: 'personal_agent',
      type: 'stdio',
      command: tools.command,
      args: tools.args,
      env: {},
    }];
  }

  async _requestOn(child, method, params, timeoutMs) {
    try {
      return await super._requestOn(child, method, params, timeoutMs);
    } catch (error) {
      throw normalizeDroidError(error);
    }
  }

  _captureSessionState(result) {
    const currentModel = typeof result?.settings?.modelId === 'string' ? result.settings.modelId : this.sessionModel;
    const currentEffort = typeof result?.settings?.reasoningEffort === 'string'
      ? result.settings.reasoningEffort
      : this.sessionEffort;
    if (Array.isArray(result?.availableModels)) {
      this.rawModels = result.availableModels;
    }
    this.sessionModel = currentModel;
    this.sessionEffort = currentEffort;
    this.modelCatalog = parseDroidModels(this.rawModels, currentModel);
  }

  _resolveStartupEffort(model, effort) {
    if (!effort) return undefined;
    if (!this.rawModels.length) return effort;
    const entry = currentModelEntry({
      runtime: this.runtime,
      model,
      resolvedModel: model || this.sessionModel,
      models: this.modelCatalog,
    });
    if (!entry) return effort;
    const compatible = compatibleModelEffort(entry, effort);
    if (compatible === undefined || compatible === null) return undefined;
    return compatible;
  }

  async _openSession(child, { model, effort }) {
    const created = await this._requestOn(child, 'droid.initialize_session', {
      machineId: this.instanceId,
      cwd: this.config.workspace,
      ...(model ? { modelId: model } : {}),
      ...(effort ? { reasoningEffort: effort } : {}),
      autonomyLevel: 'high',
      autoRejectPermissionRequests: true,
      disableBuiltinSkills: true,
      systemPromptOverride: this.systemPrompt,
      mcpServers: this._mcpServers(),
    }, this.startTimeoutMs);
    const problem = droidHandshakeProblem(created);
    if (problem) throw new Error(problem);
    this.sessionId = created.sessionId;
    this.sessionInitialized = true;
    this.resumeRequested = false;
    this.needsHistoryPrefix = true;
    this._captureSessionState(created);
  }

  async _onChildConnected(child, { model, effort, isSettled }) {
    if (this.resumeRequested && this.sessionId) {
      try {
        const loaded = await this._requestOn(child, 'droid.load_session', {
          sessionId: this.sessionId,
          autonomyLevel: 'high',
          autoRejectPermissionRequests: true,
          disableBuiltinSkills: true,
          systemPromptOverride: this.systemPrompt,
          mcpServers: this._mcpServers(),
        }, this.startTimeoutMs);
        this.replayedChunks = Array.isArray(loaded?.session?.messages) ? loaded.session.messages.length : 0;
        this._captureSessionState(loaded);
        this.sessionInitialized = true;
        this.needsHistoryPrefix = await this._loadNeedsHistory();
      } catch (error) {
        if (isSettled() || this.child !== child) throw error;
        if (error?.code === DROID_NOT_LOGGED_IN) throw error;
        this.logger.warn(`${this.logPrefix}_session_load_failed_fresh_start`, { error: safe(error) });
        this.sessionId = null;
        this.sessionInitialized = false;
        await this.store.prepareOVContext?.(this.cognitionEpoch);
        await this._openSession(child, { model, effort });
      }
    } else {
      await this._openSession(child, { model, effort });
    }

    const desiredModel = model || this.sessionModel;
    const compatibleEffort = this._resolveStartupEffort(desiredModel, effort);
    const needSettingsSync = (model && model !== this.sessionModel)
      || (compatibleEffort && compatibleEffort !== this.sessionEffort);
    if (needSettingsSync) {
      await this._applySettingsOn(child, {
        model: model || undefined,
        effort: compatibleEffort || undefined,
      });
    }
    this.appliedModel = model;
    this.appliedEffort = effort;
  }

  async _applySettingsOn(child, { model, effort }) {
    const params = {};
    if (model) params.modelId = model;
    if (effort) {
      const targetModel = model || this.resolvedModel;
      const entry = currentModelEntry({
        runtime: this.runtime,
        model: targetModel,
        resolvedModel: targetModel,
        models: this.modelCatalog,
      });
      const resolvedEffort = entry ? compatibleModelEffort(entry, effort) : effort;
      if (resolvedEffort) params.reasoningEffort = resolvedEffort;
    }
    if (Object.keys(params).length === 0) return;
    await this._requestOn(child, 'droid.update_session_settings', params, this.controlTimeoutMs);
    if (params.modelId) this.sessionModel = params.modelId;
    if (params.reasoningEffort) this.sessionEffort = params.reasoningEffort;
    if (this.rawModels.length > 0) {
      this.modelCatalog = parseDroidModels(this.rawModels, this.sessionModel);
    }
  }

  _settingsPending() {
    return (Boolean(this.model) && this.appliedModel !== this.model)
      || (Boolean(this.effort) && this.appliedEffort !== this.effort);
  }

  _promptRefused(error) {
    return !Number.isInteger(error?.code) || PROMPT_REFUSED_CODES.has(error.code);
  }

  _submitTurn(prepared, owner, { hadPrefix }) {
    this._request('droid.add_user_message', {
      messageId: randomUUID(),
      text: prepared.body,
      queuePlacement: 'end_of_turn',
    }, this.controlTimeoutMs).then(
      () => {
        if (this.activeOwner !== owner) return;
        this.turnAcknowledged = true;
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

  _sendCancelNotification() {
    if (!this.child?.stdin?.writable) return;
    void this._request('droid.interrupt_session', {}, this.controlTimeoutMs).catch(() => {});
  }

  _handleAgentMessage(message) {
    const { method, params } = message;
    if (message.id !== undefined) {
      this.logger.warn(`${this.logPrefix}_interaction_declined`, { reason: 'unattended', method });
      this._recordActivity('agent.interaction_declined', {
        summary: method,
        metadata: { runtime: this.runtime, method },
      });
      if (method === 'session/request_permission') {
        const options = params?.options ?? [];
        const rejectOption = options.find((option) => String(option.kind ?? '').includes('reject'));
        this._send({
          id: message.id,
          result: rejectOption
            ? { outcome: { outcome: 'selected', optionId: rejectOption.optionId } }
            : { outcome: { outcome: 'cancelled' } },
        });
      } else {
        this._send({
          id: message.id,
          error: {
            code: UNATTENDED_INTERACTION_METHODS.has(method) ? -32000 : -32601,
            message: 'This unattended daemon does not support interactive requests.',
          },
        });
      }
      return;
    }

    if (method !== 'droid.session_notification') return;
    if (params?.sessionId && this.sessionId && params.sessionId !== this.sessionId) return;
    const notification = params?.notification;
    if (!notification || typeof notification !== 'object') return;
    this._handleNotification(notification);
  }

  _handleNotification(notification) {
    const kind = notification.type;
    if (kind === 'settings_updated' && notification.settings) {
      this._captureSessionState({ settings: notification.settings });
      return;
    }
    if (kind === 'droid_working_state_changed') {
      const state = notification.newState;
      this.modelWorking = state === 'thinking' || state === 'streaming_assistant_message';
      if (this.modelWorking) this.modelStartedAtMs ??= Date.now();
      else this.modelStartedAtMs = null;
      if (state === 'idle' && this.turnActive && this.cancelledOwner === this.activeOwner) {
        this._completeTurn(new Error(`${this.runtimeLabel} turn ended: cancelled`));
      }
      return;
    }
    if (kind === 'assistant_text_delta') {
      if (this.turnActive) this.turnAcknowledged = true;
      this.modelWorking = true;
      this.modelStartedAtMs ??= Date.now();
      const delta = notification.textDelta ?? notification.text;
      if (delta) this.noteText += String(delta);
      return;
    }
    if (kind === 'assistant_text_complete') {
      if (this.turnActive) this.turnAcknowledged = true;
      if (!this.noteText && typeof notification.text === 'string') {
        this.noteText = notification.text;
      }
      return;
    }
    if (kind === 'create_message' && notification.message?.role === 'assistant') {
      if (this.turnActive) this.turnAcknowledged = true;
      if (!this.noteText) {
        const fallback = extractAssistantText(notification.message);
        if (fallback) this.noteText = fallback;
      }
      return;
    }
    if (kind === 'tool_call' || kind === 'tool_execution_started') {
      if (this.turnActive) this.turnAcknowledged = true;
      this._flushNote();
      this.modelWorking = false;
      this.modelStartedAtMs = null;
      const tool = notification.toolUse ?? notification;
      const toolId = String(tool.id ?? notification.toolUseId ?? notification.toolCallId ?? notification.callId ?? randomUUID());
      const toolName = String(tool.name ?? notification.toolName ?? 'unknown tool');
      const rawInput = tool.input ?? notification.input ?? notification.arguments ?? notification.parameters;
      const existing = this.toolStarts.get(toolId);
      const started = existing ?? { name: toolName, atMs: Date.now(), recorded: false, input: rawInput };
      started.name = toolName;
      if (rawInput !== undefined) started.input = rawInput;
      this.toolStarts.set(toolId, started);
      if (!started.recorded && (kind === 'tool_execution_started' || hasToolInput(rawInput))) {
        this._recordToolCalled(toolId, started);
      }
      return;
    }
    if (kind === 'tool_execution_phase_changed') {
      const toolId = String(notification.toolUseId ?? notification.toolCallId ?? '');
      const started = this.toolStarts.get(toolId);
      if (started && !started.recorded) this._recordToolCalled(toolId, started);
      return;
    }
    if (kind === 'tool_result' || kind === 'tool_execution_completed') {
      const toolId = String(notification.toolUseId ?? notification.toolCallId ?? notification.callId ?? notification.id ?? '');
      const started = this.toolStarts.get(toolId);
      if (started && !started.recorded) this._recordToolCalled(toolId, started);
      const toolName = started?.name ?? String(notification.toolName ?? notification.name ?? 'unknown tool');
      const durationMs = started ? Date.now() - started.atMs : undefined;
      if (started) this.toolStarts.delete(toolId);
      const failed = Boolean(notification.isError || notification.error);
      this._recordActivity('agent.tool_completed', {
        inboundSeq: this._activeSeqs().at(-1) ?? null,
        status: failed ? 'FAILED' : 'COMPLETED',
        summary: toolName,
        metadata: {
          runtime: this.runtime,
          toolId,
          toolName,
          ...(durationMs !== undefined ? { durationMs } : {}),
          ...(failed
            ? { error: activityPayload(notification.error ?? notification.output ?? notification.result ?? notification.content) }
            : { output: activityPayload(notification.output ?? notification.result ?? notification.content) }),
        },
      });
      this.modelWorking = true;
      this.modelStartedAtMs = Date.now();
      return;
    }
    if (kind === 'agent_turn_completed') {
      if (!this.turnActive) return;
      const cancelled = this.cancelledOwner === this.activeOwner;
      const rawError = notification.error ?? (notification.reason === 'error' ? notification.message : null);
      const failed = cancelled
        ? new Error(`${this.runtimeLabel} turn ended: cancelled`)
        : (rawError
          ? normalizeDroidError(new Error(typeof rawError === 'string' ? rawError : (rawError.message || JSON.stringify(rawError))))
          : null);
      this._completeTurn(failed);
    }
  }

  _recordToolCalled(toolId, started) {
    started.recorded = true;
    this._recordActivity('agent.tool_called', {
      inboundSeq: this._activeSeqs().at(-1) ?? null,
      summary: started.name,
      status: 'RUNNING',
      metadata: {
        runtime: this.runtime,
        toolId,
        toolName: started.name,
        input: activityPayload(started.input),
      },
    });
  }

  async listModels() {
    if (this.connected && this.child?.stdin?.writable) {
      try {
        const result = await this._request('droid.list_models', {}, this.controlTimeoutMs);
        if (Array.isArray(result?.models)) {
          this.rawModels = result.models;
          this.modelCatalog = parseDroidModels(this.rawModels, this.sessionModel);
        }
      } catch {}
    }
    return this.modelCatalog;
  }
}
