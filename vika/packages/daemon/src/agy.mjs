// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { randomUUID } from 'node:crypto';
import path from 'node:path';
import { agentToolEnv, agentToolServer } from './actions/api.mjs';
import { agyHandshakeProblem } from './agy-runtime.mjs';
import { OpencodeAgent, sanitizeOpencodeEnv } from './opencode.mjs';
import { agyWorkspaceOpenVikingPieces, prepareAgyGeminiDir } from './ov-isolation.mjs';
import { getRuntimeDefinition } from './runtime-catalog.mjs';

const AGY_RUNTIME = getRuntimeDefinition('agy');
const NOT_LOGGED_IN = 'AGY_NOT_LOGGED_IN';

function notLoggedIn() {
  const error = new Error('agy is not logged in: run agy and sign in, or agy acp --login');
  error.code = NOT_LOGGED_IN;
  return error;
}

// Antigravity CLI (`agy acp`) speaks the same ACP shape OpencodeAgent drives.
// Where it differs from OpenCode, measured on agy 1.5.3:
// - Launch and policy travel in ACP: the daemon's MCP server and the appended
//   system prompt go in session/new and again in session/load.
// - A session/prompt sent during a live turn ends the first turn early, so
//   this adapter never sends a second one while any prompt is unsettled, even
//   after interruptTurn has abandoned the turn.
// - session/load replays the transcript as updates before it answers, and
//   answers success for an unknown id. A resumed session that replays nothing
//   is therefore treated as a lost transcript, unless nothing was acknowledged
//   yet, when it just keeps the journal catch-up.
// - Authentication required is an account problem, never a lost transcript.
export class AgyAgent extends OpencodeAgent {
  constructor(config, store, logger, options = {}) {
    super(config, store, logger, {
      ...options,
      runtimeId: AGY_RUNTIME.id,
      runtimeLabel: 'agy',
      logPrefix: 'agy',
      binConfigKey: 'agyBin',
      modelConfigKey: 'agyModel',
      modelSettingKey: 'agy_model',
      policyVersion: 'agy-acp-v1',
      strictRecovery: true,
    });
    this.geminiDir = null;
    this.openPrompts = 0;
    // The load in progress, tied to its process so a dead process's cleanup
    // cannot end a newer load's replay.
    this.replay = null;
    this.emptyReplay = false;
    // session ids survive restarts, the activity counter does not.
    this.instanceId = randomUUID();
  }

  // The config home goes in runtimeDir, so it is rebuilt on every launch;
  // the owner's token and sessions stay reachable through its links.
  _writeRuntimeFiles() {
    const directory = this._createRuntimeDir();
    this.geminiDir = prepareAgyGeminiDir(path.join(directory, 'config-home'), process.env);
    const pieces = agyWorkspaceOpenVikingPieces(this.config.workspace);
    if (pieces.length > 0) this.logger.warn('agy_workspace_openviking_pieces', { pieces });
    return null;
  }

  _spawnArgs() {
    // Unattended like Claude's bypass; the base class still declines any
    // session/request_permission that arrives anyway.
    return ['acp', '--dangerously-skip-permissions', `--gemini-dir=${this.geminiDir}`];
  }

  _spawnEnv() {
    return sanitizeOpencodeEnv(process.env, this._larkProfile(),
      agentToolEnv(this.config, this.authorityToken), this.config);
  }

  _assertAgent(result) {
    const problem = agyHandshakeProblem(result);
    if (problem) throw new Error(problem);
  }

  _sessionParams() {
    const tools = agentToolServer(this.config, this.authorityToken);
    return {
      mcpServers: [{ name: 'personal_agent', command: tools.command, args: tools.args, env: [] }],
      _meta: { antigravity: { systemPrompt: { append: this.systemPrompt } } },
    };
  }

  async _loadSession(child) {
    const replay = { child, chunks: 0 };
    this.replay = replay;
    try {
      const loaded = await super._loadSession(child);
      if (replay.chunks === 0 && this.syncedThroughSeq > 0) {
        throw new Error('session/load returned an empty transcript');
      }
      this.emptyReplay = replay.chunks === 0;
      return loaded;
    } finally {
      if (this.replay === replay) this.replay = null;
    }
  }

  // An empty transcript with no acknowledged history is a session that was
  // published before its first prompt: a runtime switch does this on purpose
  // and leaves the catch-up to the first real inbound.
  async _loadNeedsHistory() {
    if (!this.emptyReplay) return false;
    await this.store.prepareOVContext?.(this.cognitionEpoch);
    return true;
  }

  _isLoadFatal(error) {
    return error?.code === NOT_LOGGED_IN;
  }

  async _requestOn(child, method, params, timeoutMs) {
    try {
      return await super._requestOn(child, method, params, timeoutMs);
    } catch (error) {
      throw /authentication required/i.test(error?.message ?? '') ? notLoggedIn() : error;
    }
  }

  _request(method, params, timeoutMs) {
    const pending = super._request(method, params, timeoutMs);
    if (method !== 'session/prompt') return pending;
    this.openPrompts += 1;
    // Runs before the base class's handler. An abandoned turn ends here, with
    // no _completeTurn to drain what arrived while it was still settling.
    const settled = () => {
      this.openPrompts -= 1;
      if (this.turnActive) return;
      this._drain();
      this._notifyAvailable();
    };
    pending.then(settled, settled);
    return pending;
  }

  _promptBusy() {
    return this.turnActive || this.openPrompts > 0;
  }

  _handleSessionUpdate(update) {
    if (this.replay) {
      if (update.sessionUpdate === 'user_message_chunk' || update.sessionUpdate === 'agent_message_chunk') {
        this.replay.chunks += 1;
      }
      return;
    }
    super._handleSessionUpdate(update);
  }

  // The same ids `agy models` and --model use, with the effort in the suffix.
  _captureModelOptions(result) {
    const models = result?.models;
    const available = Array.isArray(models?.availableModels) ? models.availableModels : [];
    const current = typeof models?.currentModelId === 'string' ? models.currentModelId : '';
    this.modelCatalog = available
      .filter((entry) => entry && typeof entry.modelId === 'string')
      .map((entry) => ({
        id: entry.modelId,
        ...(typeof entry.name === 'string' ? { displayName: entry.name } : {}),
        ...(entry.modelId === current ? { isDefault: true } : {}),
        effortsKnown: false,
        efforts: [],
      }));
    this.sessionModel = current;
  }

  async _applyModelOn(child, model) {
    await this._requestOn(child, 'session/set_model', {
      sessionId: this.sessionId,
      modelId: model,
    }, this.controlTimeoutMs);
    this.sessionModel = model;
  }

  // name is the tool's id; title is a human summary of this one call.
  _toolName(update) {
    return String(update.name ?? update.title ?? 'unknown tool');
  }

  _toolOutcome(update) {
    return update.status === 'failed' ? 'failed' : super._toolOutcome(update);
  }

  _activityEventKey(kind) {
    return `${this.runtime}:${this.sessionId ?? 'na'}:${this.instanceId}:${kind}:${this.activitySeq}`;
  }

  _toolOutput(update) {
    return Array.isArray(update.content) && update.content.length > 0 ? update.content : update.rawOutput ?? update.content;
  }
}
