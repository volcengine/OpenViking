// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { CodexAgent, sanitizeCodexEnv } from './codex.mjs';
import { getRuntimeDefinition } from './runtime-catalog.mjs';
import { codexIsolationArgs, traexConfigFile } from './ov-isolation.mjs';

const TRAEX_RUNTIME = getRuntimeDefinition('traex');
const TRAEX_APP_SERVER_CONFIG_ARGS = Object.freeze([]);

export function sanitizeTraexEnv(environment, profile = '', actions = null, config = {}) {
  // TraeCode installs the same OpenViking plugin and uses the same headless
  // app-server transport as Codex, so it needs the same presentation guard.
  return sanitizeCodexEnv(environment, profile, actions, config);
}

// TraeX currently speaks the same app-server JSON-RPC shape that CodexAgent
// already drives. Keep it as a separate runtime boundary so TraeX-specific
// config, persisted settings, and future protocol drift do not leak through
// the Codex runtime id.
export class TraexAgent extends CodexAgent {
  constructor(config, store, logger, options = {}) {
    super(config, store, logger, {
      ...options,
      runtimeId: TRAEX_RUNTIME.id,
      runtimeLabel: 'traex',
      logPrefix: 'traex',
      binConfigKey: 'traexBin',
      modelSettingKey: 'traex_model',
      appServerConfigArgs: TRAEX_APP_SERVER_CONFIG_ARGS,
      // TraeCode installs the same OpenViking plugin, in its own config file.
      ovIsolationArgs: () => codexIsolationArgs(traexConfigFile(process.env)),
      sanitizeEnv: sanitizeTraexEnv,
      policyVersion: 'traex-steer-v1',
    });
  }
}
