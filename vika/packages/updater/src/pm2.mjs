// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { realpath } from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';

// pm2 control shared by the updater (upgrades) and the vika CLI (start,
// stop, status). Starting an app from the installed release never fetches or
// publishes anything; that is the updater's job alone.

import { SHARED_WEB } from './targets.mjs';

const require = createRequire(import.meta.url);
const {
  DAEMON_ENV_FIELDS,
  DAEMON_SECRET_ENV_FIELDS,
} = require('../../../deployment-config.cjs');
const DAEMON_ENV_FIELD_SET = new Set(DAEMON_ENV_FIELDS);
const WEB_FORBIDDEN_ENV_PREFIXES = Object.freeze([
  'ARK_',
  'VOLCENGINE_',
  'VIKA_VOLC_',
  'SESSION_ID',
]);
const PM2_CONTROL_ENV_KEYS = Object.freeze([
  'HOME',
  'USER',
  'LOGNAME',
  'PATH',
  'PM2_HOME',
  'LANG',
  'LC_ALL',
  'LC_CTYPE',
  'TMPDIR',
  'XDG_RUNTIME_DIR',
  'VIKA_CONFIG',
  ...DAEMON_ENV_FIELDS,
  ...DAEMON_SECRET_ENV_FIELDS,
]);

export function cleanPm2ControlEnv(source = process.env) {
  return Object.fromEntries(PM2_CONTROL_ENV_KEYS
    .filter((key) => typeof source[key] === 'string'
      && (source[key] !== '' || DAEMON_ENV_FIELD_SET.has(key)))
    .map((key) => [key, source[key]]));
}

export function cleanWebPm2ControlEnv(source = {}) {
  return Object.fromEntries(Object.entries(source).filter(([key]) => (
    !WEB_FORBIDDEN_ENV_PREFIXES.some((prefix) => key.startsWith(prefix))
  )));
}

export async function waitUntilReady(url, fetchImpl, {
  attempts = 30,
  intervalMs = 1000,
} = {}) {
  let lastError;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const response = await fetchImpl(url, { signal: AbortSignal.timeout(1500) });
      if (response.ok) return;
      lastError = new Error(`ready endpoint returned ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    if (attempt + 1 < attempts) {
      await new Promise((resolve) => setTimeout(resolve, intervalMs));
    }
  }
  throw new Error(`daemon did not become ready: ${lastError?.message || 'unknown error'}`);
}

export function isInside(parent, child) {
  const relative = path.relative(parent, child);
  return relative !== '' && !relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative);
}

// The first pm2 command on a machine spawns its daemon and prints a banner
// ahead of the JSON; the list is the last line.
export function parseJlist(stdout) {
  const line = String(stdout || '').trim().split('\n').reverse().find((text) => text.trim().startsWith('['));
  try {
    return JSON.parse(line ?? '[]');
  } catch {
    throw new Error('pm2 jlist returned invalid JSON');
  }
}

const TRANSITIONAL_PM2_STATES = new Set([
  'launching',
  'one-launch-status',
  'stopping',
  'waiting restart',
]);

function retryablePm2Error(message) {
  const error = new Error(message);
  error.retryablePm2State = true;
  return error;
}

export async function verifyPm2App({
  app,
  release,
  pm2Bin,
  execFileImpl,
  pm2Env,
  commandTimeoutMs = 30_000,
}) {
  const result = await execFileImpl(pm2Bin, ['jlist'], {
    timeout: commandTimeoutMs,
    env: pm2Env || cleanPm2ControlEnv(),
  });
  const processes = parseJlist(result?.stdout);
  const matches = processes.filter((processInfo) => processInfo?.name === app);
  if (matches.length === 0) throw retryablePm2Error(`expected exactly one PM2 app named ${app} (found 0)`);
  if (matches.length !== 1) throw new Error(`expected exactly one PM2 app named ${app} (found ${matches.length})`);
  const [processInfo] = matches;
  if (processInfo.pm2_env?.status !== 'online') {
    const state = processInfo.pm2_env ?? {};
    const details = [
      `status=${state.status ?? 'unknown'}`,
      Number.isInteger(state.exit_code) ? `exit_code=${state.exit_code}` : null,
      Number.isInteger(state.unstable_restarts) ? `unstable_restarts=${state.unstable_restarts}` : null,
      state.pm_err_log_path ? `error_log=${state.pm_err_log_path}` : null,
    ].filter(Boolean).join(', ');
    const message = `${app} is not online (${details})`;
    if (TRANSITIONAL_PM2_STATES.has(state.status)) throw retryablePm2Error(message);
    throw new Error(message);
  }
  const execPath = await realpath(processInfo.pm2_env?.pm_exec_path || '');
  if (!isInside(release, execPath)) {
    throw new Error(`${app} is running outside the current release`);
  }
  return processInfo;
}

// PM2 can answer `start` while a process is still in one-launch-status. A
// one-shot verification made setup report failure even though the same
// process became online moments later. Poll the process identity and status
// before moving on to the HTTP readiness check.
export async function waitUntilOnline(options, {
  attempts = 40,
  intervalMs = 250,
  onlineTimeoutMs = 10_000,
} = {}) {
  const deadline = Date.now() + onlineTimeoutMs;
  let lastError;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const remainingMs = deadline - Date.now();
    if (remainingMs <= 0) break;
    try {
      return await verifyPm2App({
        ...options,
        commandTimeoutMs: Math.max(1, Math.min(remainingMs, 1_000)),
      });
    } catch (error) {
      lastError = error;
      if (!error?.retryablePm2State) throw error;
    }
    if (attempt + 1 < attempts) {
      const sleepMs = Math.min(intervalMs, Math.max(0, deadline - Date.now()));
      if (sleepMs > 0) await new Promise((resolve) => setTimeout(resolve, sleepMs));
    }
  }
  throw lastError;
}

export async function pm2Processes({ pm2Bin, execFileImpl, pm2Env }) {
  const result = await execFileImpl(pm2Bin, ['jlist'], {
    timeout: 30_000,
    env: pm2Env,
  });
  return parseJlist(result?.stdout);
}


// The Web never sees Ark credentials, even when an older release's ecosystem
// file would pass them through.
export function appControlEnv(app, pm2Env) {
  return app === SHARED_WEB.app ? cleanWebPm2ControlEnv(pm2Env) : pm2Env;
}

// Start one app from a release's ecosystem file, then wait for its /ready.
export async function startApp(spec, { release, configPath, pm2Bin, execFileImpl, fetchImpl, readyOptions, pm2Env }) {
  const env = appControlEnv(spec.app, pm2Env);
  await execFileImpl(pm2Bin, ['start', configPath, '--only', spec.app, '--update-env'], { timeout: 60_000, env });
  await waitUntilOnline({ app: spec.app, release, pm2Bin, execFileImpl, pm2Env: env }, readyOptions);
  await waitUntilReady(spec.readyUrl, fetchImpl, readyOptions);
}

export async function resolveRelease(releaseLink) {
  const link = path.resolve(releaseLink);
  const release = await realpath(link);
  const releasesRoot = await realpath(path.join(path.dirname(link), 'releases')).catch((error) => {
    if (error?.code === 'ENOENT') return path.join(path.dirname(link), 'releases');
    throw error;
  });
  if (!isInside(releasesRoot, release)) {
    throw new Error(`current release must resolve inside ${releasesRoot}`);
  }
  return release;
}
