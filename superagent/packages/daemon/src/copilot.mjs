// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { AcpAgent } from './acp-agent.mjs';
import { agentToolServer } from './actions/api.mjs';
import {
  COPILOT_NOT_LOGGED_IN,
  copilotHandshakeProblem,
  normalizeCopilotError,
  parseCopilotConfigOptions,
} from './copilot-runtime.mjs';
import { copilotWorkspaceOpenVikingPieces, prepareCopilotHome } from './ov-isolation.mjs';
import { compatibleModelEffort, getRuntimeDefinition } from './runtime-catalog.mjs';

const COPILOT_RUNTIME = getRuntimeDefinition('copilot');

/**
 * Persistent Agent Client Protocol (ACP) adapter for GitHub Copilot CLI
 * (`copilot --acp --stdio`). Runs against a SuperAgent-owned COPILOT_HOME
 * preserving login & durable session-state while filtering OpenViking pieces.
 */
export class CopilotAgent extends AcpAgent {
  constructor(config, store, logger, options = {}) {
    super(config, store, logger, {
      runtimeId: COPILOT_RUNTIME.id,
      runtimeLabel: 'copilot',
      logPrefix: 'copilot',
      binConfigKey: 'copilotBin',
      modelConfigKey: 'copilotModel',
      modelSettingKey: 'copilot_model',
      effortConfigKey: 'copilotEffort',
      effortSettingKey: 'copilot_effort',
      policyVersion: 'copilot-acp-v1',
      rpcPrefix: 'cop',
      ...options,
    });
    this.copilotHomeDir = null;
    this.mcpConfigPath = null;
    this.modelCapabilities = new Map();
    this.activeConfigOptions = [];
    this.hasEffortOption = false;
    this.supportedEfforts = [];
  }

  _resolveCopilotHome() {
    if (this.config.dataDir) return path.join(this.config.dataDir, 'copilot', 'home');
    return path.join(this.runtimeDir, 'home');
  }

  _writeRuntimeFiles() {
    const directory = this._createRuntimeDir();
    this.copilotHomeDir = prepareCopilotHome(this._resolveCopilotHome(), process.env, {
      workspace: this.config.workspace,
    });
    fs.writeFileSync(path.join(this.copilotHomeDir, 'copilot-instructions.md'), this.systemPrompt, {
      encoding: 'utf8',
      mode: 0o600,
    });
    const pieces = copilotWorkspaceOpenVikingPieces(this.config.workspace);
    if (pieces.length > 0) this.logger.warn('copilot_workspace_openviking_pieces', { pieces });

    const tools = agentToolServer(this.config, this.authorityToken);
    const mcpConfig = {
      mcpServers: {
        personal_agent: {
          type: 'stdio',
          command: tools.command,
          args: tools.args,
          env: {},
          tools: ['*'],
        },
      },
    };
    this.mcpConfigPath = path.join(directory, 'mcp-config.json');
    fs.writeFileSync(this.mcpConfigPath, JSON.stringify(mcpConfig), { encoding: 'utf8', mode: 0o600 });
    return this.mcpConfigPath;
  }

  _cleanupRuntimeFiles() {
    this.mcpConfigPath = null;
    super._cleanupRuntimeFiles();
  }

  _spawnArgs() {
    return [
      '--acp',
      '--stdio',
      '--allow-all',
      '--no-ask-user',
      '--disable-builtin-mcps',
      '--allow-all-mcp-server-instructions',
      '--disable-client-telemetry',
      '--no-auto-update',
      '--no-custom-instructions',
      '--additional-mcp-config',
      `@${this.mcpConfigPath}`,
    ];
  }

  _spawnEnv() {
    return {
      ...super._spawnEnv(),
      COPILOT_HOME: this.copilotHomeDir,
      COPILOT_DISABLE_LOGIN_SHELL_ENV: '1',
      COPILOT_AUTO_UPDATE: 'false',
      COPILOT_TELEMETRY_DISABLED: 'true',
    };
  }

  _assertAgent(result) {
    const problem = copilotHandshakeProblem(result);
    if (problem) throw new Error(problem);
  }

  _isLoadFatal(error) {
    return error?.code === COPILOT_NOT_LOGGED_IN;
  }

  async _requestOn(child, method, params, timeoutMs) {
    try {
      return await super._requestOn(child, method, params, timeoutMs);
    } catch (error) {
      throw normalizeCopilotError(error);
    }
  }

  _captureModelOptions(result) {
    const parsed = parseCopilotConfigOptions(result, this.modelCapabilities);
    if (parsed.models.length > 0 || parsed.configOptions.length > 0) {
      this.modelCatalog = parsed.models;
    }
    if (parsed.currentModel) this.sessionModel = parsed.currentModel;
    this.activeConfigOptions = parsed.configOptions;
    this.hasEffortOption = parsed.hasEffortOption;
    this.supportedEfforts = parsed.efforts;
    if (parsed.currentEffort) this.sessionEffort = parsed.currentEffort;
  }

  async _applyModelOn(child, model) {
    this._modelUpdatedConfig = false;
    const response = await this._requestOn(child, 'session/set_model', {
      sessionId: this.sessionId,
      modelId: model,
    }, this.controlTimeoutMs);
    this.sessionModel = model;
    if (response?.configOptions) {
      this._captureModelOptions(response);
      return;
    }
    if (!this._modelUpdatedConfig) {
      const cached = this.modelCapabilities.get(model);
      if (cached) {
        this.hasEffortOption = cached.efforts.length > 0;
        this.supportedEfforts = [...cached.efforts];
        this.sessionEffort = cached.defaultEffort || '';
      }
      this.modelCatalog = this.modelCatalog.map((entry) => ({
        ...entry,
        isDefault: entry.id === model,
      }));
    }
  }

  _handleConfigOptionUpdate(update) {
    const configOptions = update?.configOptions;
    if (!Array.isArray(configOptions)) return;
    this._modelUpdatedConfig = true;
    this._captureModelOptions({ configOptions });
  }

  _currentEffortEntry() {
    const cached = this.modelCapabilities.get(this.resolvedModel);
    if (cached) return cached;
    if (!this.hasEffortOption) return { effortsKnown: true, efforts: [] };
    return {
      effortsKnown: true,
      efforts: this.supportedEfforts,
      defaultEffort: this.sessionEffort,
    };
  }

  async _applyEffortOn(child, effort, { strict = false } = {}) {
    if (!effort) return;
    const entry = this._currentEffortEntry();
    if (!entry.efforts.length) {
      // Non-reasoning models (e.g. claude-haiku-4.5) omit reasoning_effort from
      // configOptions and reject session/set_config_option with -32602.
      this.appliedEffort = effort;
      return;
    }
    const targetEffort = compatibleModelEffort(entry, effort) ?? effort;
    try {
      const response = await this._requestOn(child, 'session/set_config_option', {
        sessionId: this.sessionId,
        configId: 'reasoning_effort',
        value: targetEffort,
      }, this.controlTimeoutMs);
      this.sessionEffort = targetEffort;
      this.effort = targetEffort;
      if (response?.configOptions) this._captureModelOptions(response);
    } catch (error) {
      if (error?.code === 'UNSUPPORTED_REASONING_EFFORT') {
        const modelId = this.resolvedModel;
        if (modelId) {
          this.modelCapabilities.set(modelId, { effortsKnown: true, efforts: [], defaultEffort: '' });
          this.modelCatalog = this.modelCatalog.map((item) => (
            item.id === modelId ? { ...item, effortsKnown: true, efforts: [] } : item
          ));
        }
        this.hasEffortOption = false;
        this.supportedEfforts = [];
        this.sessionEffort = '';
        this.appliedEffort = effort;
        if (!strict) return;
      }
      throw error;
    }
  }

  async _applySettingsOn(child, { model, effort }) {
    const modelChanged = Boolean(model) && this.appliedModel !== model;
    if (modelChanged) {
      await this._applyModelOn(child, model);
      this.appliedModel = model;
    }
    if (effort !== undefined && (modelChanged || this.appliedEffort !== effort)) {
      await this._applyEffortOn(child, effort, { strict: !modelChanged });
      this.appliedEffort = effort;
    }
  }

  _settingsPending() {
    const modelChanged = Boolean(this.model) && this.appliedModel !== this.model;
    const effortChanged = Boolean(this.effort) && this.appliedEffort !== this.effort;
    return modelChanged || (effortChanged && this.hasEffortOption && this.supportedEfforts.length > 0);
  }
}
