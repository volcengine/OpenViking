#!/usr/bin/env node
// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import '../../../env-compat.cjs';

import { onShutdown } from './platform/index.mjs';

import { createRequire } from 'node:module';
import { getConfig, publicConfig } from './config.mjs';
import { createLogger } from './logger.mjs';
import { PersonalAgentDaemon } from './daemon.mjs';

const require = createRequire(import.meta.url);
const {
  applyDeploymentAgentEnv,
  applyDeploymentDaemonEnv,
  loadDeploymentManifest,
} = require('../../../deployment-config.cjs');

let config;
let deployment;
try {
  deployment = loadDeploymentManifest(process.env);
  applyDeploymentDaemonEnv(process.env, deployment);
  applyDeploymentAgentEnv(process.env, deployment);
  config = getConfig();
  config.manifestPath = deployment.manifestPath;
} catch (error) {
  process.stderr.write(`${JSON.stringify({
    time: new Date().toISOString(),
    level: 'error',
    message: 'invalid_configuration',
    error: error.message,
  })}\n`);
  process.exit(1);
}

const logger = createLogger(config.logLevel, { service: 'personal-agent-core' });
if (deployment.ignoredFields.length > 0) {
  logger.warn('config_fields_ignored', { fields: deployment.ignoredFields, manifest: deployment.manifestPath });
}
const daemon = new PersonalAgentDaemon(config, logger);
let signalHandled = false;

async function handleSignal(signal) {
  if (signalHandled) return;
  signalHandled = true;
  const forceTimer = setTimeout(() => process.exit(1), config.shutdownTimeoutMs + 2_000);
  forceTimer.unref();
  await daemon.shutdown(signal, 0);
  clearTimeout(forceTimer);
}

onShutdown(handleSignal);

process.on('SIGINT', () => void handleSignal('SIGINT'));
process.on('SIGTERM', () => void handleSignal('SIGTERM'));
process.on('SIGHUP', () => void handleSignal('SIGHUP'));
process.on('uncaughtException', (error) => {
  logger.error('uncaught_exception', { error });
  void daemon.shutdown('uncaught_exception', 1).then(() => process.exit(1));
});
process.on('unhandledRejection', (error) => {
  logger.error('unhandled_rejection', { error });
  void daemon.shutdown('unhandled_rejection', 1).then(() => process.exit(1));
});

logger.info('daemon_starting', { config: publicConfig(config) });
try {
  await daemon.start();
  if (typeof process.send === 'function') process.send('ready');
} catch (error) {
  logger.error('daemon_start_failed', { error });
  process.exit(1);
}
