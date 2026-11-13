// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { sanitizeAgentChildEnv } from './process-utils.mjs';
import { preparePrivateDirectory } from './private-temp.mjs';
import { getRuntimeDefinition } from './runtime-catalog.mjs';
import { DSH_HOME_SHARED, dshAgentsHomePath, dshHomePath, dshHostHome } from './dsh-config.mjs';

const RUNTIME = getRuntimeDefinition('dsh');
export const DSH_BRIDGE_PATH = fileURLToPath(new URL('./dsh-bridge.mjs', import.meta.url));
const DSH_SUPPORTED = Object.freeze({ min: '0.2.2-rc.1', belowMajorMinor: '0.3' });
// The full set DSH understands for a route (`llm-pi-ai/src/catalog.ts`); which
// of them a model offers is answered per model by `superagent/llm/model/info`.
export const DSH_EFFORTS = RUNTIME?.efforts
  ?? Object.freeze(['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']);
// The built-in route the adapter falls back on when a requested one is unknown.
// Operator routes live in DSH's own settings document, not in this process.
export const DSH_DEFAULT_MODEL = 'deepseek-official/deepseek-flash';

// DSH's settings service writes this file; it is the single source of truth for
// model configuration (design §1), so the daemon only ever reads it.
const DSH_PROFILE_PATCH_SEGMENTS = Object.freeze(['profiles', 'sdk', 'cordis.patch.yml']);

/**
 * The `llm-pi-ai` settings document of one instance.
 * @param {object} options
 * @param {string} options.dataDir - the instance data directory.
 * @returns {string} the absolute path, whether or not it exists yet.
 */
export function dshProfilePatchPath({ dataDir, dshHomeMode = 'isolated', homeDir } = {}) {
  const home = dshHomePath({ dataDir, mode: dshHomeMode, homeDir });
  return path.join(home, ...DSH_PROFILE_PATCH_SEGMENTS);
}

/**
 * Content hash of the settings document, never its mtime: DSH rewrites the
 * document through its settings service, and a touched-but-identical file must
 * not restart the process (design §4.4).
 * @param {object} options
 * @param {string} options.dataDir - the instance data directory.
 * @returns {string|null} lowercase sha256 hex, or null when there is no document.
 */
export function dshProfilePatchHash({ dataDir, dshHomeMode = 'isolated', homeDir } = {}) {
  try {
    return createHash('sha256').update(fs.readFileSync(dshProfilePatchPath({ dataDir, dshHomeMode, homeDir }))).digest('hex');
  } catch {
    return null;
  }
}

// The bridge's own protocol version and the methods each caller depends on.
// v2 adds the model catalogue, model selection, the `llm-pi-ai` settings
// document, and credential writes. Names live under `superagent/` so a future SDK
// method with the same purpose can never be shadowed by this bridge.
export const DSH_BRIDGE_ID = 'superagent-dsh-bridge';
export const DSH_BRIDGE_VERSION = 2;
export const DSH_BRIDGE_DAEMON_METHODS = Object.freeze([
  'session/steer', 'session/cancel',
  'superagent/llm/models', 'superagent/llm/model/info', 'superagent/session/model/select',
]);
export const DSH_BRIDGE_CLI_METHODS = Object.freeze([
  ...DSH_BRIDGE_DAEMON_METHODS,
  'superagent/settings/describe', 'superagent/settings/mutate',
  'superagent/credentials/set', 'superagent/credentials/unset',
]);

/**
 * Whether a `superagent/hello` reply is the bridge this release drives.
 * @param hello - the reply result.
 * @param methods - methods the caller will use.
 * @returns true when the bridge id, version, and every method match.
 */
export function bridgeHelloSupports(hello, methods) {
  return hello?.bridge === DSH_BRIDGE_ID
    && Number.isSafeInteger(hello?.version) && hello.version >= DSH_BRIDGE_VERSION
    && Array.isArray(hello?.methods)
    && methods.every(method => hello.methods.includes(method));
}

// JSON-RPC code the bridge answers while a DSH service has not activated yet.
// `credentials` is the one that lags: it appeared ~1s after the bridge began
// answering, so every caller retries this code and nothing else.
export const DSH_NOT_READY_CODE = -32002;
// 100/200/400 then a 400ms floor, bounded by a 3s ceiling: the observed
// activation window is ~1s, and an unbounded retry would outlive the caller's
// own timeout.
const NOT_READY_BACKOFF_MS = Object.freeze([100, 200, 400]);
const NOT_READY_CEILING_MS = 3_000;

/**
 * Whether an error is the bridge's "service not mounted yet" answer.
 * @param error - the rejection from an SDK request.
 * @returns true for the retryable NOT_READY code only.
 */
export function isDshNotReady(error) {
  return error?.code === DSH_NOT_READY_CODE || error?.rpcCode === DSH_NOT_READY_CODE;
}

/**
 * Retry a request while the bridge reports NOT_READY. Every other error is
 * rethrown on the first attempt, so a real failure never costs 3 seconds.
 * @param run - issues one request attempt.
 * @param options.backoff - backoff schedule in milliseconds.
 * @param options.ceilingMs - total retry budget.
 * @param options.sleep - injectable timer for tests.
 * @param options.now - injectable clock for tests.
 * @returns the first successful result.
 */
export async function retryDshNotReady(run, {
  backoff = NOT_READY_BACKOFF_MS, ceilingMs = NOT_READY_CEILING_MS,
  sleep = defaultSleep, now = Date.now,
} = {}) {
  const deadline = now() + ceilingMs;
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await run();
    } catch (error) {
      if (!isDshNotReady(error)) throw error;
      const wait = backoff[attempt] ?? backoff[backoff.length - 1];
      if (now() + wait > deadline) throw error;
      await sleep(wait);
    }
  }
}

function defaultSleep(ms) {
  return new Promise(resolve => { const timer = setTimeout(resolve, ms); timer.unref?.(); });
}

const SEMVER = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/;

function numeric(value, version) {
  if (!/^(?:0|[1-9]\d*)$/.test(value)) throw new Error(`invalid DSH version: ${version}`);
  const number = Number(value);
  if (!Number.isSafeInteger(number)) throw new Error(`invalid DSH version: ${version}`);
  return number;
}

export function parseDshVersion(value) {
  const raw = String(value ?? '').trim();
  const match = SEMVER.exec(raw);
  if (!match) throw new Error(`invalid DSH version: ${raw || '(empty)'}; expected semver such as 0.2.2-rc.1`);
  return Object.freeze({
    raw,
    major: numeric(match[1], raw),
    minor: numeric(match[2], raw),
    patch: numeric(match[3], raw),
    prerelease: Object.freeze((match[4] ?? '').split('.').filter(Boolean).map(part => /^\d+$/.test(part) ? numeric(part, raw) : part)),
  });
}

function compareIdentifiers(left, right) {
  if (left === right) return 0;
  if (typeof left === 'number' && typeof right === 'string') return -1;
  if (typeof left === 'string' && typeof right === 'number') return 1;
  return left < right ? -1 : 1;
}

export function compareDshVersions(left, right) {
  const a = typeof left === 'string' ? parseDshVersion(left) : left;
  const b = typeof right === 'string' ? parseDshVersion(right) : right;
  for (const field of ['major', 'minor', 'patch']) {
    if (a[field] !== b[field]) return a[field] < b[field] ? -1 : 1;
  }
  if (a.prerelease.length === 0 || b.prerelease.length === 0) {
    if (a.prerelease.length === b.prerelease.length) return 0;
    return a.prerelease.length === 0 ? 1 : -1;
  }
  for (let index = 0; index < Math.max(a.prerelease.length, b.prerelease.length); index += 1) {
    if (a.prerelease[index] === undefined) return -1;
    if (b.prerelease[index] === undefined) return 1;
    const comparison = compareIdentifiers(a.prerelease[index], b.prerelease[index]);
    if (comparison) return comparison;
  }
  return 0;
}

export function assertSupportedDshVersion(value) {
  const version = parseDshVersion(value);
  if (compareDshVersions(version, DSH_SUPPORTED.min) < 0
    || version.major !== 0 || version.minor !== 2) {
    throw new Error(`unsupported DSH version ${version.raw}; requires >=${DSH_SUPPORTED.min} <${DSH_SUPPORTED.belowMajorMinor}.0`);
  }
  return version.raw;
}

export function assertDshNodeVersion(value = process.versions.node) {
  const version = parseDshVersion(value);
  if (version.major < 24 || (version.major === 24 && version.minor < 9)) {
    throw new Error(`dsh requires Node >= 24.9 (daemon runs ${version.raw})`);
  }
  return version.raw;
}

export function parseDshModel(value) {
  const raw = String(value ?? '').trim() || DSH_DEFAULT_MODEL;
  const slash = raw.indexOf('/');
  const provider = slash < 0 ? 'deepseek-official' : raw.slice(0, slash);
  const model = slash < 0 ? raw : raw.slice(slash + 1);
  if (!provider || !model) throw new Error(`invalid DSH model: ${raw}; expected provider/model`);
  return { provider, model };
}

export function prepareDshEnvironment(dataDir, { dshHomeMode = 'isolated', homeDir } = {}) {
  const root = preparePrivateDirectory(path.join(dataDir, 'dsh'));
  const home = dshHomePath({ dataDir, mode: dshHomeMode, homeDir });
  const agents = dshAgentsHomePath({ dataDir, mode: dshHomeMode, homeDir });
  return {
    root,
    // Never chmod or create the host's shared configuration roots. They belong
    // to DSH and the other agent CLIs, not to this instance.
    home: dshHomeMode === DSH_HOME_SHARED ? home : preparePrivateDirectory(home),
    agents: dshHomeMode === DSH_HOME_SHARED ? agents : preparePrivateDirectory(agents),
    run: preparePrivateDirectory(path.join(root, 'run')),
  };
}

export function sanitizeDshEnv(environment, {
  profile = '', actions = null, cliBins = [], dataDir, execPath = process.execPath,
  dshHomeMode = 'isolated', homeDir = dshHostHome(environment),
} = {}) {
  const safeEnv = sanitizeAgentChildEnv(environment, { profile, actions, cliBins });
  for (const key of Object.keys(safeEnv)) if (/^DSH_/i.test(key)) delete safeEnv[key];
  if (!dataDir) throw new Error('dsh dataDir is required');
  const directories = prepareDshEnvironment(dataDir, { dshHomeMode, homeDir });
  Object.assign(safeEnv, {
    DSH_HOME: directories.home,
    DSH_AGENTS_HOME: directories.agents,
    DSH_PERMISSION_MODE: 'danger-full-access',
    DSH_TELEMETRY_DISABLED: '1',
  });
  const inherited = (safeEnv.PATH || '/usr/bin:/bin').split(path.delimiter).filter(Boolean);
  const nodeDirectory = path.dirname(execPath);
  safeEnv.PATH = [nodeDirectory, ...inherited.filter(entry => entry !== nodeDirectory)].join(path.delimiter);
  return safeEnv;
}

export function escapeDshTemplate(text) {
  return String(text ?? '').replaceAll('{{', '{\u2060{');
}

export function dshCommand({ bin = 'dsh', patchPath }) {
  if (!patchPath) throw new Error('dsh patchPath is required');
  return { command: bin, args: ['--profile', 'sdk', '--patch', patchPath] };
}

// JSON is a YAML subset. Keeping this file generated and private prevents a
// policy or action authority token from becoming editable shell/YAML syntax.
//
// The patch deliberately carries no `llm-pi-ai` entry: model routes live in
// `<DSH_HOME>/profiles/sdk/cordis.patch.yml`, which DSH's own settings service
// writes (design §1). A provider section here would make `superagent/settings/mutate`
// answer "overridden by a home patch or command-line overlay", so the CLI would
// be editing a document nothing reads.
//
// `mcpServers` and `skillDirs` come from `dsh-extensions.mjs`; both are optional
// so the control patch and every older caller keep the exact patch they had
// before.
export function buildDshPatch({
  policy = '', bridgePath = DSH_BRIDGE_PATH, mcp = null,
  mcpServers = [], skillDirs = [], includeDefaultSkillRoots = true,
} = {}) {
  const insert = [];
  const patch = [];
  if (bridgePath !== null) {
    patch.push({ id: 'sdk-jsonrpc-server', disabled: true });
    insert.push({
      id: 'superagent-dsh-bridge', name: bridgePath,
      inject: ['sdkAppStartup', 'loader'],
      config: { maxTokensAsSuccess: true },
    });
  }
  if (mcp !== null) insert.push({
    id: 'superagent-personal-agent', name: '@deepseek-ai/dsh-mcp-client',
    config: {
      serverName: 'personal_agent', transport: 'stdio',
      command: mcp.command, args: [...mcp.args],
      failOnStartupError: true,
      ...(mcp.toolCallTimeoutMs ? { toolCallTimeoutMs: mcp.toolCallTimeoutMs } : {}),
    },
  });
  // User MCP servers are ordinary `dsh-mcp-client` rows. They are deliberately
  // not fatal on startup: one broken server must not take the session down.
  for (const server of mcpServers) {
    insert.push({
      id: `superagent-mcp-${server.serverName}`, name: '@deepseek-ai/dsh-mcp-client',
      config: { failOnStartupError: false, ...server },
    });
  }
  if (insert.length > 0) patch.push({ insert });
  // Setup owns the six/four standard roots. Default discovery must be off when
  // it supplies a selection, including an empty one; otherwise DSH would put
  // unchecked project/user roots straight back. Older callers keep the original
  // additive customSkillDirs behaviour by leaving includeDefaultSkillRoots on.
  if (skillDirs.length > 0 || !includeDefaultSkillRoots) {
    patch.push({ id: 'skill-filesystem', config: {
      ...(!includeDefaultSkillRoots ? { includeDefaultRoots: false } : {}),
      customSkillDirs: [...skillDirs],
    } });
  }
  patch.push({ id: 'system-prompt', config: { personaPrefix: escapeDshTemplate(policy) } });
  for (const id of ['tool-ask-user', 'schedule', 'tool-schedule', 'tool-plugin-manager', 'present', 'tool-subagent-fork']) {
    patch.push({ id, disabled: true });
  }
  return patch;
}

export function writeDshPatch({
  dataDir, policy = '', bridgePath = DSH_BRIDGE_PATH, mcp = null,
  mcpServers = [], skillDirs = [], includeDefaultSkillRoots = true,
  dshHomeMode = 'isolated', homeDir,
} = {}) {
  const { run } = prepareDshEnvironment(dataDir, { dshHomeMode, homeDir });
  const patch = buildDshPatch({ policy, bridgePath, mcp, mcpServers, skillDirs, includeDefaultSkillRoots });
  const patchPath = path.join(run, 'patch.yml');
  const temporary = `${patchPath}.${process.pid}.tmp`;
  const content = `${JSON.stringify(patch, null, 2)}\n`;
  fs.writeFileSync(temporary, content, { encoding: 'utf8', mode: 0o600 });
  fs.chmodSync(temporary, 0o600);
  fs.renameSync(temporary, patchPath);
  return patchPath;
}

