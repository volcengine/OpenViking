// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

'use strict';

const fs = require('node:fs');
const path = require('node:path');

const INSTANCE_ID = /^[a-z][a-z0-9-]{0,31}$/;
// `all` is the upgrade target for every instance; vika-web and vika-updater
// are the shared control-plane pm2 apps, so these ids cannot name an instance.
const RESERVED_INSTANCE_IDS = Object.freeze(['all', 'web', 'updater']);
const CONTROL_ENV_MAX_BYTES = 262_144;
const DEPLOYMENT_MANIFEST_MAX_BYTES = 1_048_576;
const TOP_LEVEL_FIELDS = Object.freeze(['version', 'defaultInstance', 'env', 'agents']);
const DAEMON_ENV_FIELDS = Object.freeze([
  'LARK_CLI_BIN',
  'CODEX_BIN',
  'TRAEX_BIN',
  'TRAECLI_BIN',
  'CLAUDE_BIN',
  'PI_BIN',
  'VIKA_WEIXIN_LONG_POLL_TIMEOUT_MS',
  'VIKA_LOG_LEVEL',
  'VIKA_TEMP_DIR',
  'VIKA_DRY_RUN',
  'VIKA_IGNORED_SENDER_IDS',
  'VIKA_SEND_TIMEOUT_MS',
  'VIKA_SHUTDOWN_TIMEOUT_MS',
  'VIKA_MAX_REPLY_CHARS',
  'VIKA_MAX_EVENT_BYTES',
  'VIKA_MAX_EVENT_AGE_MS',
  'VIKA_IDLE_REACTION_AFTER_MS',
  'VIKA_TIMER_OCCURRENCE_CHECK_DELAY_MS',
  'VIKA_RETENTION_MS',
  'VIKA_CODEX_EFFORT',
  'VIKA_TRAEX_EFFORT',
  'VIKA_CLAUDE_EFFORT',
  'VIKA_CLAUDE_MODEL',
  'VIKA_PI_EFFORT',
  'VIKA_PI_MODEL',
  'VIKA_VOLC_SESSION_ID',
  'VIKA_VOLC_ENVIRONMENT_ID',
  'VIKA_VOLC_BASE_URL',
  'VIKA_VOLC_REQUEST_TIMEOUT_MS',
  'VIKA_VOLC_STREAM_IDLE_TIMEOUT_MS',
  'FALLBACK_MODEL',
  'VIKA_HISTORY_LIMIT',
]);
// Secrets may flow from the operator environment into daemon/updater PM2
// processes, but are deliberately excluded from the deployment manifest.
const DAEMON_SECRET_ENV_FIELDS = Object.freeze([
  'ARK_API_KEY',
]);
const DAEMON_PROCESS_ENV_FIELDS = Object.freeze([
  ...DAEMON_ENV_FIELDS,
  ...DAEMON_SECRET_ENV_FIELDS,
]);
const AGENT_FIELDS = Object.freeze([
  'id',
  'displayName',
  'channel',
  'connectors',
  'profile',
  'port',
  'dataDir',
  'tempDir',
  'workspace',
  'ovConfig',
  'ovAutoRecall',
  'explicitSilence',
  'stagedReplies',
  'role',
  'naturalWriting',
  'model',
  'volcSessionId',
  'volcEnvironmentId',
  'emailAccounts',
]);
// The roles shipped with the release (packages/daemon/agent/roles/<name>.md).
// An instance names one of them, or the absolute path of its own role file.
const ROLE_PRESETS = Object.freeze(['colleague', 'assistant', 'qa']);
// Each mailbox is its own email Connector instance; the id is how the model
// and the logs tell them apart.
const EMAIL_INSTANCE_ID = /^email-[a-z0-9][a-z0-9-]{0,39}$/;

function resolveDeploymentManifestPath(env = process.env) {
  const override = typeof env.VIKA_CONFIG === 'string'
    ? env.VIKA_CONFIG.trim()
    : '';
  if (override) {
    if (!path.isAbsolute(override)) {
      throw new Error('VIKA_CONFIG must be an absolute path');
    }
    return path.normalize(override);
  }
  const home = typeof env.HOME === 'string' ? env.HOME.trim() : '';
  if (!home || !path.isAbsolute(home)) throw new Error('HOME must be an absolute path');
  return path.join(path.normalize(home), '.vika', 'config.json');
}

// Secrets (DAEMON_SECRET_ENV_FIELDS) are deliberately kept out of config.json
// so they never land in a file meant to be inspected/versioned. This sibling file
// is the deterministic fallback: whoever runs `pm2 restart`/`vika upgrade`
// no longer needs the secret exported in their own shell for it to take effect.
function resolveSecretsFilePath(env = process.env) {
  const override = typeof env.VIKA_SECRETS_FILE === 'string'
    ? env.VIKA_SECRETS_FILE.trim()
    : '';
  if (override) {
    if (!path.isAbsolute(override)) {
      throw new Error('VIKA_SECRETS_FILE must be an absolute path');
    }
    return path.normalize(override);
  }
  const home = typeof env.HOME === 'string' ? env.HOME.trim() : '';
  if (!home || !path.isAbsolute(home)) return null;
  return path.join(path.normalize(home), '.vika', 'secrets.env');
}

function loadSecretsFile(env = process.env) {
  const secretsPath = resolveSecretsFilePath(env);
  if (!secretsPath) return {};
  let raw;
  try { raw = fs.readFileSync(secretsPath, 'utf8'); } catch { return {}; }
  const allowed = new Set(DAEMON_SECRET_ENV_FIELDS);
  const result = {};
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq <= 0) continue;
    const key = trimmed.slice(0, eq).trim();
    if (!allowed.has(key)) continue;
    result[key] = trimmed.slice(eq + 1).trim();
  }
  return result;
}

// A field this release does not know is skipped and reported, never fatal: a
// manifest written for another release (or naming a retired setting) still
// loads. Known fields stay strictly validated.
function ignoreUnknownFields(value, allowed, prefix, ignored) {
  const allowedSet = new Set(allowed);
  for (const name of Object.keys(value)) {
    if (!allowedSet.has(name)) ignored.push(`${prefix}${name}`);
  }
}

function manifestString(value, name, { maxLength = 128 } = {}) {
  if (typeof value !== 'string' || value.trim() === '') throw new Error(`${name} is required`);
  const normalized = value.trim();
  if (normalized.length > maxLength || /[\u0000-\u001f\u007f]/.test(normalized)) {
    throw new Error(`${name} is invalid`);
  }
  return normalized;
}

function manifestAbsolutePath(value, name) {
  const normalized = manifestString(value, name, { maxLength: 4096 });
  if (!path.isAbsolute(normalized)) throw new Error(`${name} must be an absolute path`);
  return path.normalize(normalized);
}

function optionalManifestString(value, name) {
  if (value === undefined || value === null) return null;
  return manifestString(value, name);
}

// An unknown key is dropped, never passed on: this list is also what reaches
// the daemon and pm2 process environments.
function parseDaemonEnv(value, ignored) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('deployment manifest env must be an object');
  }
  const allowed = new Set(DAEMON_ENV_FIELDS);
  const result = {};
  for (const [key, envValue] of Object.entries(value)) {
    if (!allowed.has(key)) {
      ignored.push(`env.${key}`);
      continue;
    }
    if (typeof envValue !== 'string') throw new Error(`deployment manifest env.${key} must be a string`);
    if (envValue.length > 4096 || /[\u0000-\u001f\u007f]/.test(envValue)) {
      throw new Error(`deployment manifest env.${key} is invalid`);
    }
    result[key] = envValue;
  }
  return Object.freeze(result);
}

function parseAgent(value, index, ignored) {
  const label = `agents[${index}]`;
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  ignoreUnknownFields(value, AGENT_FIELDS, `${label}.`, ignored);
  const id = manifestString(value.id, `${label}.id`);
  if (!INSTANCE_ID.test(id) || RESERVED_INSTANCE_IDS.includes(id)) {
    throw new Error(`${label}.id must match ${INSTANCE_ID} and cannot be ${RESERVED_INSTANCE_IDS.join(', ')}`);
  }
  if (!Number.isSafeInteger(value.port) || value.port < 1 || value.port > 65535) {
    throw new Error(`${label}.port must be an integer between 1 and 65535`);
  }
  const model = manifestString(value.model, `${label}.model`);
  let connectors = null;
  if (value.connectors !== undefined) {
    if (!Array.isArray(value.connectors) || value.connectors.length === 0) {
      throw new Error(`${label}.connectors must be a non-empty array`);
    }
    connectors = [...new Set(value.connectors.map((connector, connectorIndex) => (
      manifestString(connector, `${label}.connectors[${connectorIndex}]`, { maxLength: 64 })
    )))];
    if (connectors.length !== value.connectors.length
      || connectors.some((connector) => !['lark', 'weixin', 'email', 'telegram', 'slack', 'qq'].includes(connector))) {
      throw new Error(`${label}.connectors must contain unique lark, weixin, email, telegram, slack or qq values`);
    }
  }
  // The primary IM: where notices to the owner go. A mailbox is never one.
  const channel = value.channel === undefined
    ? (connectors?.find((connector) => connector !== 'email') ?? 'lark')
    : manifestString(value.channel, `${label}.channel`);
  if (!['lark', 'weixin', 'telegram', 'slack', 'qq'].includes(channel)) {
    throw new Error(`${label}.channel must be lark, weixin, telegram, slack or qq`);
  }
  connectors ??= [channel];
  if (!connectors.includes(channel)) {
    throw new Error(`${label}.connectors must include ${label}.channel`);
  }
  const volcSessionId = optionalManifestString(value.volcSessionId, `${label}.volcSessionId`);
  const volcEnvironmentId = optionalManifestString(
    value.volcEnvironmentId,
    `${label}.volcEnvironmentId`,
  );
  if (Boolean(volcSessionId) !== Boolean(volcEnvironmentId)) {
    throw new Error(`${label}.volcSessionId and ${label}.volcEnvironmentId must be configured together`);
  }
  if (model === 'ark' && !volcSessionId) {
    throw new Error(`${label} using ark must configure volcSessionId and volcEnvironmentId`);
  }
  if (connectors.includes('telegram') && model === 'ark') {
    throw new Error(`${label} using the telegram connector cannot use the ark runtime`);
  }
  if (connectors.includes('qq') && model === 'ark') {
    throw new Error(`${label} using the qq connector cannot use the ark runtime`);
  }
  if (connectors.includes('slack') && model === 'ark') {
    throw new Error(`${label} using the slack connector cannot use the ark runtime`);
  }
  if (connectors.includes('weixin') && model === 'ark') {
    throw new Error(`${label} using the weixin connector cannot use the ark runtime`);
  }
  const emailAccounts = manifestEmailAccounts(value.emailAccounts, `${label}.emailAccounts`, ignored);
  if (connectors.includes('email') !== (emailAccounts.length > 0)) {
    throw new Error(`${label}.emailAccounts and the email connector must be configured together`);
  }
  if (connectors.includes('email') && model === 'ark') {
    throw new Error(`${label} using the email connector cannot use the ark runtime`);
  }
  // The lark-cli profile belongs to the lark connector; other instances have none.
  if (!connectors.includes('lark') && value.profile !== undefined) {
    throw new Error(`${label}.profile is only used by the lark connector`);
  }
  if (value.ovAutoRecall !== undefined && typeof value.ovAutoRecall !== 'boolean') {
    throw new Error(`${label}.ovAutoRecall must be a boolean`);
  }
  if (value.explicitSilence !== undefined && typeof value.explicitSilence !== 'boolean') {
    throw new Error(`${label}.explicitSilence must be a boolean`);
  }
  if (value.naturalWriting !== undefined && typeof value.naturalWriting !== 'boolean') {
    throw new Error(`${label}.naturalWriting must be a boolean`);
  }
  if (value.stagedReplies !== undefined && typeof value.stagedReplies !== 'boolean') {
    throw new Error(`${label}.stagedReplies must be a boolean`);
  }
  return Object.freeze({
    id,
    displayName: manifestString(value.displayName, `${label}.displayName`, { maxLength: 40 }),
    channel,
    connectors: Object.freeze(connectors),
    profile: connectors.includes('lark') ? manifestString(value.profile, `${label}.profile`) : null,
    port: value.port,
    dataDir: manifestAbsolutePath(value.dataDir, `${label}.dataDir`),
    tempDir: value.tempDir == null ? null : manifestAbsolutePath(value.tempDir, `${label}.tempDir`),
    workspace: manifestAbsolutePath(value.workspace, `${label}.workspace`),
    ovConfig: manifestAbsolutePath(value.ovConfig, `${label}.ovConfig`),
    ovAutoRecall: value.ovAutoRecall ?? false,
    explicitSilence: value.explicitSilence ?? false,
    stagedReplies: value.stagedReplies ?? false,
    role: manifestRole(value.role, `${label}.role`),
    naturalWriting: value.naturalWriting ?? true,
    model,
    volcSessionId,
    volcEnvironmentId,
    emailAccounts: Object.freeze(emailAccounts),
  });
}

function manifestRole(value, label) {
  if (value === undefined || value === null) return null;
  if (typeof value === 'string' && ROLE_PRESETS.includes(value)) return value;
  if (typeof value === 'string' && path.isAbsolute(value.trim())) return manifestAbsolutePath(value, label);
  throw new Error(`${label} must be ${ROLE_PRESETS.join(', ')} or an absolute path`);
}

function manifestEmailAccounts(value, label, ignored) {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) throw new Error(`${label} must be an array`);
  const ids = new Set();
  return value.map((entry, index) => {
    const entryLabel = `${label}[${index}]`;
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) throw new Error(`${entryLabel} must be an object`);
    ignoreUnknownFields(entry, ['id', 'config'], `${entryLabel}.`, ignored);
    const id = manifestString(entry.id, `${entryLabel}.id`, { maxLength: 46 });
    if (!EMAIL_INSTANCE_ID.test(id)) throw new Error(`${entryLabel}.id must be email-<name> in lowercase letters, digits and -`);
    if (ids.has(id)) throw new Error(`${label} has two mailboxes called ${id}`);
    ids.add(id);
    return Object.freeze({ id, config: manifestAbsolutePath(entry.config, `${entryLabel}.config`) });
  });
}

function assertUniqueAgentFields(agents) {
  for (const field of [
    'id',
    'profile',
    'port',
    'dataDir',
    'workspace',
    'ovConfig',
    'volcSessionId',
    'volcEnvironmentId',
  ]) {
    const seen = new Set();
    for (const agent of agents) {
      if (agent[field] === null) continue;
      if (seen.has(agent[field])) throw new Error(`duplicate agent ${field}: ${agent[field]}`);
      seen.add(agent[field]);
    }
  }
  // Two watchers of one account file would report every mail twice.
  const accountFiles = new Set();
  for (const { config } of agents.flatMap((agent) => agent.emailAccounts)) {
    if (accountFiles.has(config)) throw new Error(`duplicate email account file: ${config}`);
    accountFiles.add(config);
  }
}

function pathsOverlap(left, right) {
  const relative = path.relative(left, right);
  if (relative === '') return true;
  if (!relative.startsWith('..') && !path.isAbsolute(relative)) return true;
  const reverse = path.relative(right, left);
  return !reverse.startsWith('..') && !path.isAbsolute(reverse);
}

function assertIsolatedAgentStorage(agents) {
  const paths = agents.flatMap((agent, index) => [
    { name: `agents[${index}].dataDir`, value: agent.dataDir },
    { name: `agents[${index}].workspace`, value: agent.workspace },
    ...(agent.tempDir ? [{ name: `agents[${index}].tempDir`, value: agent.tempDir }] : []),
  ]);
  for (let leftIndex = 0; leftIndex < paths.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < paths.length; rightIndex += 1) {
      const left = paths[leftIndex];
      const right = paths[rightIndex];
      if (pathsOverlap(left.value, right.value)) {
        throw new Error(`agent storage paths overlap: ${left.name} and ${right.name}`);
      }
    }
  }
}

function loadDeploymentManifest(env) {
  const manifestPath = resolveDeploymentManifestPath(env);
  let raw;
  try {
    raw = fs.readFileSync(manifestPath, 'utf8');
  } catch (error) {
    throw new Error(`cannot read deployment manifest ${manifestPath}: ${error.message}`);
  }
  if (Buffer.byteLength(raw) > DEPLOYMENT_MANIFEST_MAX_BYTES) {
    throw new Error(`deployment manifest exceeds ${DEPLOYMENT_MANIFEST_MAX_BYTES} bytes`);
  }
  let value;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new Error(`deployment manifest must be valid JSON: ${manifestPath}`);
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('deployment manifest must be an object');
  }
  const ignored = [];
  ignoreUnknownFields(value, TOP_LEVEL_FIELDS, '', ignored);
  if (value.version !== 1) throw new Error('deployment manifest version must be 1');
  if (!Array.isArray(value.agents) || value.agents.length === 0) {
    throw new Error('deployment manifest agents must be a non-empty array');
  }
  const defaultInstance = manifestString(value.defaultInstance, 'deployment manifest defaultInstance');
  const daemonEnv = parseDaemonEnv(value.env, ignored);
  const agents = value.agents.map((agent, index) => parseAgent(agent, index, ignored));
  assertUniqueAgentFields(agents);
  assertIsolatedAgentStorage(agents);
  const deployment = Object.freeze({
    version: 1,
    defaultInstance,
    env: daemonEnv,
    agents: Object.freeze(agents),
    manifestPath,
    // Paths such as `env.VIKA_OLD_SETTING` or `agents[0].oldField`, for a warning.
    ignoredFields: Object.freeze(ignored),
  });
  selectDeploymentAgent(deployment);
  return deployment;
}

function resolvedDeploymentDaemonEnv(deployment, sourceEnv = process.env) {
  const secrets = loadSecretsFile(sourceEnv);
  return Object.freeze(Object.fromEntries(DAEMON_PROCESS_ENV_FIELDS.flatMap((key) => {
    if (typeof sourceEnv[key] === 'string') return [[key, sourceEnv[key]]];
    if (Object.hasOwn(deployment.env, key)) return [[key, deployment.env[key]]];
    if (Object.hasOwn(secrets, key)) return [[key, secrets[key]]];
    return [];
  })));
}

function deploymentDaemonEnvOverrides(sourceEnv = process.env) {
  const secrets = loadSecretsFile(sourceEnv);
  return Object.freeze(Object.fromEntries(DAEMON_PROCESS_ENV_FIELDS.flatMap((key) => {
    if (typeof sourceEnv[key] === 'string') return [[key, sourceEnv[key]]];
    if (Object.hasOwn(secrets, key)) return [[key, secrets[key]]];
    return [];
  })));
}

function applyDeploymentDaemonEnv(env, deployment, sourceEnv = env) {
  Object.assign(env, resolvedDeploymentDaemonEnv(deployment, sourceEnv));
  return env;
}

function selectDeploymentAgent(deployment, id = deployment.defaultInstance) {
  const agent = deployment.agents.find((candidate) => candidate.id === id);
  if (!agent) throw new Error(`deployment instance is not configured: ${id}`);
  return agent;
}

// Pin the runtime plugins to this instance's OV identity and recall policy.
function openVikingAgentEnv(agent) {
  return {
    OPENVIKING_CLI_CONFIG_FILE: agent.ovConfig,
    OPENVIKING_CREDENTIAL_SOURCE: 'cli',
    OPENVIKING_MEMORY_ENABLED: '1',
    OPENVIKING_AUTO_RECALL: agent.ovAutoRecall ? '1' : '0',
    OPENVIKING_AUTO_CAPTURE: '1',
    OPENVIKING_AUTO_COMMIT_ON_COMPACT: '1',
    OPENVIKING_RECALL_PEER_SCOPE: 'actor',
  };
}

function applyDeploymentAgentEnv(env, deployment, requestedId) {
  const id = requestedId || (typeof env.VIKA_INSTANCE_ID === 'string'
    ? env.VIKA_INSTANCE_ID.trim()
    : '') || deployment.defaultInstance;
  const agent = selectDeploymentAgent(deployment, id);
  Object.assign(env, {
    VIKA_INSTANCE_ID: agent.id,
    VIKA_UPGRADE_TARGET: agent.id,
    VIKA_AGENT_NAME: agent.displayName,
    VIKA_PORT: String(agent.port),
    VIKA_DATA_DIR: agent.dataDir,
    VIKA_WORKSPACE: agent.workspace,
    VIKA_MODEL: agent.model,
  });
  if (agent.tempDir) env.VIKA_TEMP_DIR = agent.tempDir;
  if (agent.profile) {
    env.VIKA_LARK_PROFILE = agent.profile;
    env.LARKSUITE_CLI_PROFILE = agent.profile;
  } else {
    delete env.VIKA_LARK_PROFILE;
    delete env.LARKSUITE_CLI_PROFILE;
  }
  Object.assign(env, openVikingAgentEnv(agent));
  // Always set, so neither the shell nor another instance's value leaks in.
  env.VIKA_EXPLICIT_SILENCE = agent.explicitSilence ? 'true' : 'false';
  env.VIKA_NATURAL_WRITING = agent.naturalWriting ? 'true' : 'false';
  env.VIKA_STAGED_REPLIES = agent.stagedReplies ? 'true' : 'false';
  if (agent.role) env.VIKA_ROLE = agent.role;
  else delete env.VIKA_ROLE;
  env.VIKA_CHANNEL = agent.channel;
  env.VIKA_CONNECTORS_JSON = JSON.stringify(agent.connectors);
  if (agent.emailAccounts.length > 0) env.VIKA_EMAIL_ACCOUNTS_JSON = JSON.stringify(agent.emailAccounts);
  else delete env.VIKA_EMAIL_ACCOUNTS_JSON;
  if (agent.volcSessionId) {
    env.VIKA_VOLC_SESSION_ID = agent.volcSessionId;
    env.VIKA_VOLC_ENVIRONMENT_ID = agent.volcEnvironmentId;
  } else {
    delete env.VIKA_VOLC_SESSION_ID;
    delete env.VIKA_VOLC_ENVIRONMENT_ID;
  }
  return agent;
}

function updaterTargetsFor(deployment) {
  const upgradeSharedWeb = deployment.agents.length === 1;
  return Object.fromEntries(deployment.agents.map((agent) => [
    agent.id,
    {
      app: `vika-${agent.id}`,
      readyUrl: `http://127.0.0.1:${agent.port}/ready`,
      upgradeSharedWeb,
    },
  ]));
}

module.exports = {
  AGENT_FIELDS,
  INSTANCE_ID,
  RESERVED_INSTANCE_IDS,
  ROLE_PRESETS,
  CONTROL_ENV_MAX_BYTES,
  DAEMON_ENV_FIELDS,
  DAEMON_SECRET_ENV_FIELDS,
  DEPLOYMENT_MANIFEST_MAX_BYTES,
  TOP_LEVEL_FIELDS,
  applyDeploymentAgentEnv,
  applyDeploymentDaemonEnv,
  deploymentDaemonEnvOverrides,
  loadDeploymentManifest,
  loadSecretsFile,
  openVikingAgentEnv,
  resolveDeploymentManifestPath,
  resolveSecretsFilePath,
  resolvedDeploymentDaemonEnv,
  selectDeploymentAgent,
  updaterTargetsFor,
};
