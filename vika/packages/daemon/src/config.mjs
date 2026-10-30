// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { bundledLarkCli } from './lark-cli-bin.mjs';
import { managedPiBin } from './pi-runtime.mjs';
import { resolveRolePromptPath } from './conversation-policy.mjs';

const sourceDir = path.dirname(fileURLToPath(import.meta.url));

// TraeX installs under any of these names; without a configured binary, use
// the first one on PATH.
function traexCommand(env = process.env) {
  const names = ['traex', 'traecli', 'trae-cli'];
  const directories = String(env.PATH || '').split(path.delimiter).filter(Boolean);
  for (const name of names) {
    for (const directory of directories) {
      try {
        fs.accessSync(path.join(directory, name), fs.constants.X_OK);
        return name;
      } catch {}
    }
  }
  return names[0];
}
export const moduleDir = path.resolve(sourceDir, '..');
export const projectDir = path.resolve(moduleDir, '..', '..');

function intEnv(name, fallback, { min = 0, max = Number.MAX_SAFE_INTEGER } = {}) {
  const raw = process.env[name];
  if (raw === undefined || raw === '') return fallback;
  const value = Number.parseInt(raw, 10);
  if (!Number.isFinite(value) || value < min || value > max) {
    throw new Error(`${name} must be an integer between ${min} and ${max}`);
  }
  return value;
}

function boolEnv(name, fallback) {
  const raw = process.env[name];
  if (raw === undefined || raw === '') return fallback;
  if (['1', 'true', 'yes', 'on'].includes(raw.toLowerCase())) return true;
  if (['0', 'false', 'no', 'off'].includes(raw.toLowerCase())) return false;
  throw new Error(`${name} must be a boolean`);
}

// VIKA_ROLE: a role shipped under agent/roles, or the absolute path of the
// owner's own role file. Unset, the agent runs on the Core's defaults alone.
function rolePromptEnv(promptDir) {
  const role = (process.env.VIKA_ROLE ?? '').trim();
  if (!role) return '';
  if (!path.isAbsolute(role) && !/^[a-z][a-z0-9-]{0,31}$/.test(role)) {
    throw new Error('VIKA_ROLE must be a role name or an absolute path');
  }
  const filePath = resolveRolePromptPath(role, promptDir);
  let info = null;
  try { info = fs.statSync(filePath); } catch {}
  if (!info?.isFile()) {
    throw new Error(path.isAbsolute(role) ? 'VIKA_ROLE names a file that does not exist' : `VIKA_ROLE ${role} is not a known role`);
  }
  return filePath;
}

function csvEnv(name) {
  return new Set(
    (process.env[name] ?? '')
      .split(',')
      .map((value) => value.trim())
      .filter(Boolean),
  );
}

function enumEnv(name, fallback, allowed) {
  const value = process.env[name] || fallback;
  if (!allowed.includes(value)) {
    throw new Error(`${name} must be one of: ${allowed.join(', ')}`);
  }
  return value;
}

function jsonStringArrayEnv(name, fallback = []) {
  const raw = process.env[name];
  if (raw === undefined || raw === '') return fallback;
  let value;
  try { value = JSON.parse(raw); } catch {
    throw new Error(`${name} must be a JSON string array`);
  }
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) {
    throw new Error(`${name} must be a JSON string array`);
  }
  return value;
}

function assertLoopbackHost(host) {
  if (!['127.0.0.1', '::1', 'localhost'].includes(host)) {
    throw new Error('VIKA_HOST must be a loopback address');
  }
}

function privateRegularFileEnv(name) {
  return privateRegularFile(process.env[name], name);
}

function privateRegularFile(value, name) {
  const raw = String(value ?? '').trim();
  if (!raw || !path.isAbsolute(raw)) throw new Error(`${name} must be an absolute path`);
  const resolved = path.normalize(raw);
  const info = fs.lstatSync(resolved);
  if (!info.isFile() || info.isSymbolicLink()) throw new Error(`${name} must be a regular file`);
  if ((info.mode & 0o077) !== 0) throw new Error(`${name} must have mode 0600`);
  if (typeof process.getuid === 'function' && info.uid !== process.getuid()) {
    throw new Error(`${name} must be owned by the daemon user`);
  }
  return resolved;
}

// The email connector's mailboxes: [{ id: 'email-<name>', config: <account file> }].
function emailAccountsEnv() {
  let value = null;
  try { value = JSON.parse(process.env.VIKA_EMAIL_ACCOUNTS_JSON ?? ''); } catch {}
  if (!Array.isArray(value) || value.length === 0) {
    throw new Error('VIKA_EMAIL_ACCOUNTS_JSON must list the mailboxes of the email connector');
  }
  const ids = new Set();
  return Object.freeze(value.map((entry) => {
    const id = String(entry?.id ?? '');
    if (!/^email-[a-z0-9][a-z0-9-]{0,39}$/.test(id) || ids.has(id)) {
      throw new Error('VIKA_EMAIL_ACCOUNTS_JSON has an invalid or repeated mailbox id');
    }
    ids.add(id);
    return Object.freeze({ id, configFile: privateRegularFile(entry.config, `email account file of ${id}`) });
  }));
}

function pathsOverlap(left, right) {
  const relative = path.relative(left, right);
  if (relative === '') return true;
  if (!relative.startsWith('..') && !path.isAbsolute(relative)) return true;
  const reverse = path.relative(right, left);
  return !reverse.startsWith('..') && !path.isAbsolute(reverse);
}

export function getConfig() {
  const dataDir = path.resolve(
    process.env.VIKA_DATA_DIR || path.join(os.homedir(), '.vika', 'data'),
  );
  const configuredTempDir = (process.env.VIKA_TEMP_DIR ?? '').trim();
  if (configuredTempDir && !path.isAbsolute(configuredTempDir)) {
    throw new Error('VIKA_TEMP_DIR must be an absolute path');
  }
  const workspace = path.resolve(
    process.env.VIKA_WORKSPACE
      || path.join(os.homedir(), 'Documents', 'work', 'vika-workspace'),
  );
  const host = process.env.VIKA_HOST || '127.0.0.1';
  assertLoopbackHost(host);
  const channel = enumEnv('VIKA_CHANNEL', 'lark', ['lark', 'weixin', 'telegram', 'slack', 'qq']);
  const configuredConnectors = jsonStringArrayEnv('VIKA_CONNECTORS_JSON');
  const connectors = configuredConnectors.length > 0 ? [...new Set(configuredConnectors)] : [channel];
  if (connectors.some((connector) => !['lark', 'weixin', 'email', 'telegram', 'slack', 'qq'].includes(connector))) {
    throw new Error('VIKA_CONNECTORS_JSON contains an unsupported connector');
  }
  if (!connectors.includes(channel)) {
    throw new Error('VIKA_CONNECTORS_JSON must include VIKA_CHANNEL');
  }
  // Each mailbox's account and client password, in a file only the daemon user can read.
  const emailAccounts = connectors.includes('email') ? emailAccountsEnv() : [];
  const promptDir = path.join(moduleDir, 'agent');
  if (pathsOverlap(workspace, projectDir)) {
    throw new Error('VIKA_WORKSPACE must not overlap the running daemon directory');
  }
  if (pathsOverlap(workspace, dataDir)) {
    throw new Error('VIKA_WORKSPACE must not overlap VIKA_DATA_DIR');
  }
  const daemonProfile = (process.env.VIKA_LARK_PROFILE ?? '').trim();
  const cliProfile = (process.env.LARKSUITE_CLI_PROFILE ?? '').trim();
  if (daemonProfile && cliProfile && daemonProfile !== cliProfile) {
    throw new Error('VIKA_LARK_PROFILE must match LARKSUITE_CLI_PROFILE');
  }
  const volcBaseUrl = (process.env.VIKA_VOLC_BASE_URL
    || 'https://ark.cn-beijing.volces.com/api/v3').replace(/\/+$/, '');
  let parsedVolcBaseUrl;
  try { parsedVolcBaseUrl = new URL(volcBaseUrl); } catch {
    throw new Error('VIKA_VOLC_BASE_URL must be a valid HTTPS URL');
  }
  if (parsedVolcBaseUrl.protocol !== 'https:') {
    throw new Error('VIKA_VOLC_BASE_URL must be a valid HTTPS URL');
  }

  return {
    projectDir,
    dataDir,
    databasePath: path.join(dataDir, 'vika.sqlite3'),
    lockPath: path.join(dataDir, 'daemon.lock'),
    tempDir: configuredTempDir ? path.normalize(configuredTempDir) : path.join(dataDir, 'tmp'),
    workspace,
    promptDir,
    rolePromptPath: rolePromptEnv(promptDir),
    // On by default; agents[].naturalWriting: false turns it off.
    naturalWriting: boolEnv('VIKA_NATURAL_WRITING', true),
    openVikingConfigPath: (process.env.OPENVIKING_CLI_CONFIG_FILE || '').trim(),
    agentName: process.env.VIKA_AGENT_NAME || process.env.VIKA_INSTANCE_ID || 'Agent',
    instanceId: process.env.VIKA_INSTANCE_ID || '',
    upgradeTarget: process.env.VIKA_UPGRADE_TARGET || process.env.VIKA_INSTANCE_ID || '',
    channel,
    connectors: Object.freeze(connectors),
    connectorWorkerPath: path.join(sourceDir, 'connectors', 'worker.mjs'),
    actionSocketPath: path.join(dataDir, 'actions.sock'),
    actionMcpPath: path.join(sourceDir, 'actions', 'mcp.mjs'),
    larkCliBin: process.env.LARK_CLI_BIN || bundledLarkCli(),
    slackCliBin: process.env.SLACK_CLI_BIN || undefined,
    emailAccounts,
    weixinLongPollTimeoutMs: intEnv('VIKA_WEIXIN_LONG_POLL_TIMEOUT_MS', 35_000, {
      min: 5_000,
      max: 120_000,
    }),
    codexBin: process.env.CODEX_BIN || 'codex',
    traexBin: process.env.TRAEX_BIN || process.env.TRAECLI_BIN || traexCommand(),
    claudeBin: process.env.CLAUDE_BIN || 'claude',
    piBin: process.env.PI_BIN || managedPiBin(),
    initialModel: process.env.VIKA_MODEL || '',
    codexEffort: enumEnv('VIKA_CODEX_EFFORT', 'medium', ['none', 'minimal', 'low', 'medium', 'high', 'xhigh']),
    traexEffort: enumEnv('VIKA_TRAEX_EFFORT', 'medium', ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']),
    claudeModel: process.env.VIKA_CLAUDE_MODEL || '',
    claudeEffort: enumEnv('VIKA_CLAUDE_EFFORT', 'medium', ['low', 'medium', 'high', 'xhigh', 'max']),
    piModel: process.env.VIKA_PI_MODEL || '',
    piEffort: enumEnv(
      'VIKA_PI_EFFORT',
      'medium',
      ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'],
    ),
    volcApiKey: process.env.ARK_API_KEY || '',
    volcSessionId: process.env.VIKA_VOLC_SESSION_ID
      || process.env.ARK_SESSION_ID
      || process.env.SESSION_ID
      || '',
    volcEnvironmentId: (process.env.VIKA_VOLC_ENVIRONMENT_ID || '').trim(),
    volcBaseUrl,
    volcRequestTimeoutMs: intEnv('VIKA_VOLC_REQUEST_TIMEOUT_MS', 30_000, {
      min: 1_000,
      max: 300_000,
    }),
    volcStreamIdleTimeoutMs: intEnv('VIKA_VOLC_STREAM_IDLE_TIMEOUT_MS', 900_000, {
      min: 30_000,
      max: 3_600_000,
    }),
    // Codex model the daemon auto-switches to when a claude turn fails.
    fallbackModel: process.env.FALLBACK_MODEL || 'gpt-5.6-sol',
    host,
    port: intEnv('VIKA_PORT', 3738, { min: 1, max: 65535 }),
    httpEnabled: boolEnv('VIKA_HTTP_ENABLED', true),
    logLevel: enumEnv('VIKA_LOG_LEVEL', 'info', ['debug', 'info', 'warn', 'error']),
    profile: daemonProfile || cliProfile,
    ignoredSenderIds: csvEnv('VIKA_IGNORED_SENDER_IDS'),
    sendTimeoutMs: intEnv('VIKA_SEND_TIMEOUT_MS', 30_000, { min: 1_000 }),
    shutdownTimeoutMs: intEnv('VIKA_SHUTDOWN_TIMEOUT_MS', 20_000, { min: 5_000, max: 25_000 }),
    maxReplyChars: intEnv('VIKA_MAX_REPLY_CHARS', 30_000, { min: 100, max: 100_000 }),
    maxEventBytes: intEnv('VIKA_MAX_EVENT_BYTES', 1_000_000, { min: 1_024, max: 10_000_000 }),
    maxEventAgeMs: intEnv('VIKA_MAX_EVENT_AGE_MS', 86_400_000, { min: 60_000 }),
    idleReactionAfterMs: intEnv('VIKA_IDLE_REACTION_AFTER_MS', 600_000, {
      min: 0,
      max: 604_800_000,
    }),
    timerOccurrenceCheckDelayMs: intEnv('VIKA_TIMER_OCCURRENCE_CHECK_DELAY_MS', 300_000, {
      min: 1_000,
      max: 86_400_000,
    }),
    // Opt-in per instance (agents[].explicitSilence): the stay_silent tool, the
    // decisions ledger and the turn-end decision check. Off, the daemon offers
    // the same tools and assembles the same policy text as without them.
    explicitSilence: boolEnv('VIKA_EXPLICIT_SILENCE', false),
    // Opt-in per instance (agents[].stagedReplies): a longer task may get an
    // OnIt and interim findings before its final delivery.
    stagedReplies: boolEnv('VIKA_STAGED_REPLIES', false),
    historyLimit: intEnv('VIKA_HISTORY_LIMIT', 30, { min: 0, max: 200 }),
    // Replies to bot messages in one chat/thread with no person writing there
    // before the safety fuse holds further ones; 0 turns the fuse off.
    botReplyFuse: intEnv('VIKA_BOT_REPLY_FUSE', 20, { min: 0, max: 1_000 }),
    retentionMs: intEnv('VIKA_RETENTION_MS', 7_776_000_000, { min: 86_400_000 }),
    dryRun: boolEnv('VIKA_DRY_RUN', false),
  };
}

export function publicConfig(config) {
  return {
    projectDir: config.projectDir,
    dataDir: config.dataDir,
    workspace: config.workspace,
    host: config.host,
    port: config.port,
    httpEnabled: config.httpEnabled,
    channel: config.channel,
    connectors: config.connectors,
    dryRun: config.dryRun,
  };
}
