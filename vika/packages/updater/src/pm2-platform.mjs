// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import path from 'node:path';
import * as platform from '../../daemon/src/platform/index.mjs';

export function pm2Bin(release, implementation = platform) {
  return implementation.supportsShellScripts ? path.join(release, 'bin', 'pm2') : 'pm2';
}
export function pm2Options(implementation = platform) {
  return implementation.supportsPosixSignals ? {} : { shutdown_with_message: true };
}
export function ecosystemSearchPath(env, fallback, implementation = platform) {
  const inherited = implementation.normalizeEnvironment(env).PATH || '';
  return implementation.pathDelimiter === ':' ? `${inherited}:${fallback}` : inherited;
}
export function allowedPm2Environment(source, keys, { allowEmpty = new Set(), implementation = platform } = {}) {
  const env = implementation.normalizeEnvironment(source);
  const normalized = implementation.environmentCaseSensitive ? env
    : Object.fromEntries(Object.entries(env).map(([key, value]) => [key.toUpperCase(), value]));
  const allowed = [...keys, ...implementation.systemEnvironmentKeys];
  return Object.fromEntries(allowed
    .filter(key => typeof normalized[key] === 'string' && (normalized[key] !== '' || allowEmpty.has(key)))
    .map(key => [key, normalized[key]]));
}
