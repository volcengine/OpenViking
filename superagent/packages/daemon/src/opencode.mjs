// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { AcpAgent, sanitizeOpencodeEnv } from './acp-agent.mjs';
import { agentToolServer } from './actions/api.mjs';
import { prepareOpencodeConfigHome } from './ov-isolation.mjs';
import { getRuntimeDefinition } from './runtime-catalog.mjs';

export { sanitizeOpencodeEnv };

const OPENCODE_RUNTIME = getRuntimeDefinition('opencode');

// Persistent Agent Client Protocol (ACP, nd-JSON over stdio) adapter for
// `opencode acp`. One ACP session (one native transcript) serves every inbound
// line. OpenCode serializes prompts against one session: a prompt sent during a
// live turn runs only after it, so this adapter is queue-next-turn. The Core's
// agent tools load as a per-instance stdio MCP server; the conversation policy
// is an instruction file pinned via a daemon-owned OPENCODE_CONFIG.
export class OpencodeAgent extends AcpAgent {
  constructor(config, store, logger, options = {}) {
    super(config, store, logger, {
      runtimeId: OPENCODE_RUNTIME.id,
      runtimeLabel: OPENCODE_RUNTIME.id,
      logPrefix: OPENCODE_RUNTIME.id,
      binConfigKey: 'opencodeBin',
      modelConfigKey: 'opencodeModel',
      modelSettingKey: 'opencode_model',
      policyVersion: 'opencode-acp-v1',
      ...options,
    });
  }

  _writeRuntimeFiles() {
    const directory = this._createRuntimeDir();
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

  _spawnArgs() {
    return ['acp', '--cwd', this.config.workspace];
  }

  _spawnEnv(configPath) {
    return {
      ...super._spawnEnv(configPath),
      // Pins the conversation policy instruction and the personal_agent MCP
      // server without editing the user's own opencode.json.
      OPENCODE_CONFIG: configPath,
      // OpenCode's own OpenViking plugin stays off: its global config folder
      // is an OpenViking SuperAgent-owned copy without it (ov-isolation.mjs).
      XDG_CONFIG_HOME: prepareOpencodeConfigHome(path.join(this.runtimeDir, 'config-home'), process.env),
    };
  }

  _assertAgent(result) {
    if (!/opencode/i.test(result?.agentInfo?.name ?? '')) {
      throw new Error('unrecognised ACP agent');
    }
  }

  // Fields session/new and session/load share. OpenCode takes its MCP servers
  // and instructions from OPENCODE_CONFIG instead.
  _sessionParams() {
    return { mcpServers: [] };
  }

  // Reads the session/new or session/load result.
  _captureModelOptions(result) {
    const configOptions = result?.configOptions ?? [];
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
}
