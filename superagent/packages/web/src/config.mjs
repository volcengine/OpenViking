// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import path from 'node:path';
import { fileURLToPath } from 'node:url';

const sourceDir = path.dirname(fileURLToPath(import.meta.url));
export const moduleDir = path.resolve(sourceDir, '..');
export const publicDir = path.join(moduleDir, 'public');

function intEnv(name, fallback, { min = 0, max = Number.MAX_SAFE_INTEGER } = {}) {
  const raw = process.env[name];
  if (raw === undefined || raw === '') return fallback;
  const value = Number.parseInt(raw, 10);
  if (!Number.isSafeInteger(value) || String(value) !== raw.trim()
    || value < min || value > max) {
    throw new Error(`${name} must be an integer between ${min} and ${max}`);
  }
  return value;
}

function enumEnv(name, fallback, allowed) {
  const value = process.env[name] || fallback;
  if (!allowed.includes(value)) {
    throw new Error(`${name} must be one of: ${allowed.join(', ')}`);
  }
  return value;
}

function assertLoopbackHost(name, host) {
  if (!['127.0.0.1', '::1', 'localhost'].includes(host)) {
    throw new Error(`${name} must be a loopback address`);
  }
}

function parseInstances(raw, fallbackHost, fallbackPort) {
  if (!raw) {
    return Object.freeze([Object.freeze({
      id: 'agent',
      label: 'Agent',
      host: fallbackHost,
      port: fallbackPort,
    })]);
  }
  if (Buffer.byteLength(raw, 'utf8') > 262_144) {
    throw new Error('VIKA_WEB_INSTANCES is too large');
  }
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error('VIKA_WEB_INSTANCES must be valid JSON');
  }
  if (!Array.isArray(parsed) || parsed.length < 1) {
    throw new Error('VIKA_WEB_INSTANCES must contain at least one instance');
  }
  const ids = new Set();
  const ports = new Set();
  return Object.freeze(parsed.map((value, index) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new Error(`VIKA_WEB_INSTANCES[${index}] must be an object`);
    }
    const id = value.id;
    const label = value.label;
    const host = value.host;
    const port = value.port;
    if (typeof id !== 'string' || !/^[a-z][a-z0-9_-]{0,31}$/.test(id)) {
      throw new Error(`VIKA_WEB_INSTANCES[${index}].id is invalid`);
    }
    if (ids.has(id)) throw new Error(`duplicate VIKA_WEB_INSTANCES id: ${id}`);
    ids.add(id);
    if (typeof label !== 'string' || label.trim() === '' || label.length > 40) {
      throw new Error(`VIKA_WEB_INSTANCES[${index}].label is invalid`);
    }
    assertLoopbackHost(`VIKA_WEB_INSTANCES[${index}].host`, host);
    if (!Number.isSafeInteger(port) || port < 1 || port > 65535) {
      throw new Error(`VIKA_WEB_INSTANCES[${index}].port must be an integer between 1 and 65535`);
    }
    if (ports.has(port)) throw new Error(`duplicate VIKA_WEB_INSTANCES port: ${port}`);
    ports.add(port);
    return Object.freeze({ id, label: label.trim(), host, port });
  }));
}

export function getConfig() {
  const host = process.env.VIKA_WEB_HOST || '127.0.0.1';
  const port = intEnv('VIKA_WEB_PORT', 3737, { min: 1, max: 65535 });
  const daemonHost = process.env.VIKA_WEB_DAEMON_HOST || '127.0.0.1';
  const daemonPort = intEnv('VIKA_WEB_DAEMON_PORT', 3738, { min: 1, max: 65535 });
  // The page has no login, so it never listens beyond this machine.
  assertLoopbackHost('VIKA_WEB_HOST', host);
  assertLoopbackHost('VIKA_WEB_DAEMON_HOST', daemonHost);
  const instances = parseInstances(process.env.VIKA_WEB_INSTANCES, daemonHost, daemonPort);
  const defaultInstance = process.env.VIKA_WEB_DEFAULT_INSTANCE || instances[0].id;
  if (!instances.some((instance) => instance.id === defaultInstance)) {
    throw new Error('VIKA_WEB_DEFAULT_INSTANCE must name an entry in VIKA_WEB_INSTANCES');
  }
  if (instances.some((instance) => instance.port === port)) {
    throw new Error('VIKA_WEB_PORT must be different from every daemon port');
  }

  return {
    host,
    port,
    instances,
    defaultInstance,
    publicDir,
    upstreamTimeoutMs: intEnv('VIKA_WEB_DAEMON_TIMEOUT_MS', 3_000, { min: 100, max: 30_000 }),
    shutdownTimeoutMs: intEnv('VIKA_WEB_SHUTDOWN_TIMEOUT_MS', 5_000, { min: 1_000, max: 30_000 }),
    logLevel: enumEnv('VIKA_WEB_LOG_LEVEL', 'info', ['debug', 'info', 'warn', 'error']),
  };
}

export function publicConfig(config) {
  return {
    host: config.host,
    port: config.port,
    instances: config.instances.map(({ id, label, port }) => ({ id, label, port })),
    defaultInstance: config.defaultInstance,
    upstreamTimeoutMs: config.upstreamTimeoutMs,
  };
}
