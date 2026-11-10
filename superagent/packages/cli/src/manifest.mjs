// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import * as platform from '../../daemon/src/platform/index.mjs';
import { list, t } from './i18n.mjs';
import { writeManifest as saveManifest } from '../../daemon/src/deployment-manifest.mjs';
export { validateManifest } from '../../daemon/src/deployment-manifest.mjs';

// Setup edits ~/.ovsa/config.json as plain JSON, and only ever saves a file
// that the daemon's own loader accepts.

const require = createRequire(import.meta.url);
const {
  INSTANCE_ID,
  RESERVED_INSTANCE_IDS,
  ROLE_PRESETS,
  loadDeploymentManifest,
  resolveDeploymentManifestPath,
} = require('../../../deployment-config.cjs');

export { ROLE_PRESETS };

/**
 * The environment runtime detection should look at: the manifest's own `env`
 * first, overridden by the caller's. A binary path pinned there (`CODEX_BIN`,
 * `DSH_BIN`, ...) is what the daemon will run, so a probe that only read
 * `process.env` would call a machine without the CLI on PATH "not installed"
 * while the daemon starts it every day. The operator's environment wins, which
 * is the same precedence the daemon applies.
 * @param {object} env - the caller's environment.
 * @param {object|null} manifest - the loaded manifest, if any.
 * @returns {object} the environment to probe with.
 */
export function detectionEnv(env, manifest) {
  return { ...(manifest?.env ?? {}), ...env };
}

export const WEB_PORT = 3737;
const FIRST_INSTANCE_PORT = 3738;

export function vikaHome(env = process.env) {
  return path.join(platform.homeDir(env) || os.homedir(), '.ovsa');
}

// setup persists the default so users can inspect/change it. A global or
// per-instance explicit choice wins; the durable data directory never moves.
export function withDefaultTempDir(agent, env = {}) {
  if (agent.tempDir != null || String(env.VIKA_TEMP_DIR ?? '').trim()) return agent;
  const dataDir = path.resolve(agent.dataDir);
  if (dataDir !== '/root' && !dataDir.startsWith('/root/')) return agent;
  return { ...agent, tempDir: `/tmp/vika-${agent.id}` };
}

// How to call the vika command from here: by name once ~/.ovsa/bin is on
// PATH, by its full path in the shell the installer ran from. Where the platform
// runs no shell scripts, that path is the batch launcher, not a ~ prefix.
export function vikaCommand(env = process.env, implementation = platform) {
  const bin = path.join(vikaHome(env), 'bin');
  const entries = String(env.PATH || '').split(implementation.pathDelimiter);
  const same = (entry) => implementation.environmentCaseSensitive
    ? entry === bin
    : entry.toLowerCase() === bin.toLowerCase();
  if (entries.some(same)) return 'ovsa';
  return implementation.supportsShellScripts ? '~/.ovsa/bin/ovsa' : path.join(bin, 'ovsa.cmd');
}

export function manifestPath(env = process.env) {
  return resolveDeploymentManifestPath(env);
}

// The manifest as JSON, or null when there is none yet. A file that exists
// but does not load is an error: setup never overwrites a broken manifest.
export function readManifest(env = process.env) {
  const filePath = manifestPath(env);
  if (!fs.existsSync(filePath)) return { path: filePath, value: null, ignored: [] };
  const { ignoredFields } = loadDeploymentManifest({ ...env, VIKA_CONFIG: filePath });
  return { path: filePath, value: JSON.parse(fs.readFileSync(filePath, 'utf8')), ignored: ignoredFields };
}

export function availableInstanceOvConfig(env, id, reserved = new Set()) {
  const base = path.join(vikaHome(env), 'ov', id);
  let target = `${base}.json`;
  for (let suffix = 2; reserved.has(target) || fs.existsSync(target); suffix += 1) {
    target = `${base}-${suffix}.json`;
  }
  return target;
}

export function instanceIdProblem(id, manifest) {
  if (!INSTANCE_ID.test(id)) return t('只能用小写字母、数字和 -，以字母开头，最多 32 个字符', 'Lowercase letters, digits and -, starting with a letter, at most 32 characters');
  if (RESERVED_INSTANCE_IDS.includes(id)) return t(`${list(RESERVED_INSTANCE_IDS)} 是保留名字`, `${list(RESERVED_INSTANCE_IDS)} are reserved`);
  if (manifest?.agents?.some((agent) => agent.id === id)) return t(`已经有叫 ${id} 的实例`, `There is an instance named ${id} already`);
  return null;
}

export function suggestInstanceId(manifest) {
  if (!manifest?.agents?.length) return 'primary';
  for (let index = 2; ; index += 1) {
    const id = `agent${index}`;
    if (!manifest.agents.some((agent) => agent.id === id)) return id;
  }
}

export function portIsFree(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once('error', () => resolve(false));
    server.listen(port, '127.0.0.1', () => server.close(() => resolve(true)));
  });
}

export async function allocatePort(manifest, isFree = portIsFree) {
  const taken = new Set([WEB_PORT, ...(manifest?.agents ?? []).map((agent) => agent.port)]);
  for (let port = FIRST_INSTANCE_PORT; port < 65_535; port += 1) {
    if (!taken.has(port) && await isFree(port)) return port;
  }
  throw new Error('no free port for a new instance');
}

// The manifest with one more instance. `env` only fills keys that are not set
// yet: other instances share it, so setup never changes what they run with.
export function withInstance(manifest, agent, env = {}) {
  const next = manifest
    ? structuredClone(manifest)
    : { version: 1, defaultInstance: agent.id, env: {}, agents: [] };
  for (const [key, value] of Object.entries(env)) {
    if (value !== undefined && next.env[key] === undefined) next.env[key] = value;
  }
  next.agents.push(agent);
  return next;
}

// The manifest with one instance changed in place.
export function withChangedInstance(manifest, agent, env = {}) {
  const next = structuredClone(manifest);
  for (const [key, value] of Object.entries(env)) {
    if (value !== undefined && next.env[key] === undefined) next.env[key] = value;
  }
  const index = next.agents.findIndex((candidate) => candidate.id === agent.id);
  if (index < 0) throw new Error(`no instance ${agent.id} in the manifest`);
  next.agents[index] = agent;
  return next;
}

export function writeManifest(filePath, value, options) {
  try { return saveManifest(filePath, value, options); } catch (error) {
    if (error.code === 'manifest_conflict') {
      error.message = t('实例配置已被其他操作修改，请重新运行 ovsa setup。', 'The instance configuration changed elsewhere; run ovsa setup again.');
    }
    throw error;
  }
}

