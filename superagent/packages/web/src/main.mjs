#!/usr/bin/env node
// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0


import { onShutdown } from '../../daemon/src/platform/index.mjs';

import { getConfig, publicConfig } from './config.mjs';
import { ActivityWebServer } from './server.mjs';

function createLogger(level, base = {}) {
  const levels = ['debug', 'info', 'warn', 'error'];
  const threshold = levels.indexOf(level);
  const logger = {};
  for (const [index, name] of levels.entries()) {
    logger[name] = (message, fields = {}) => {
      if (index < threshold) return;
      const output = name === 'error' || name === 'warn' ? process.stderr : process.stdout;
      output.write(`${JSON.stringify({
        time: new Date().toISOString(),
        level: name,
        message,
        ...base,
        ...fields,
      })}\n`);
    };
  }
  return logger;
}

let config;
try {
  config = getConfig();
} catch (error) {
  process.stderr.write(`${JSON.stringify({
    time: new Date().toISOString(),
    level: 'error',
    message: 'invalid_configuration',
    error: error instanceof Error ? error.message : String(error),
  })}\n`);
  process.exit(1);
}

const logger = createLogger(config.logLevel, { service: 'superagent-web' });
const server = new ActivityWebServer(config, logger);
let stopping = false;

async function stop(signal, exitCode = 0) {
  if (stopping) return;
  stopping = true;
  logger.info('web_stopping', { signal });
  const forceTimer = setTimeout(() => process.exit(1), config.shutdownTimeoutMs + 1_000);
  forceTimer.unref();
  await server.stop();
  clearTimeout(forceTimer);
  process.exitCode = exitCode;
}

onShutdown(stop);

process.on('SIGINT', () => void stop('SIGINT'));
process.on('SIGTERM', () => void stop('SIGTERM'));
process.on('SIGHUP', () => void stop('SIGHUP'));
process.on('uncaughtException', (error) => {
  logger.error('uncaught_exception', { error: error instanceof Error ? error.message : String(error) });
  void stop('uncaught_exception', 1).then(() => process.exit(1));
});
process.on('unhandledRejection', (error) => {
  logger.error('unhandled_rejection', { error: error instanceof Error ? error.message : String(error) });
  void stop('unhandled_rejection', 1).then(() => process.exit(1));
});

logger.info('web_starting', { config: publicConfig(config) });
try {
  await server.start();
  if (typeof process.send === 'function') process.send('ready');
} catch (error) {
  logger.error('web_start_failed', { error: error instanceof Error ? error.message : String(error) });
  await server.stop();
  process.exit(1);
}
