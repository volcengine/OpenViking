// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { execFile as execFileCallback, spawn } from 'node:child_process';
import fs from 'node:fs';
import { chmod, lstat, mkdir, realpath, rm, rmdir } from 'node:fs/promises';
import http from 'node:http';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import {
  SHARED_WEB,
  assertUpgradeTarget,
  defaultReleaseLink,
  defaultSocketPath,
  getUpgradeTargets,
  loadTargets,
  shouldUpgradeSharedWeb,
} from './targets.mjs';
import { publishLatestRelease } from './publisher.mjs';
import {
  appControlEnv,
  cleanPm2ControlEnv,
  isInside,
  pm2Processes,
  resolveRelease,
  startApp,
  waitUntilReady,
} from './pm2.mjs';

export { cleanPm2ControlEnv, cleanWebPm2ControlEnv, resolveRelease, verifyPm2App } from './pm2.mjs';

const execFile = promisify(execFileCallback);
const MAX_BODY_BYTES = 4096;
// vika-<instance>; vika-web and vika-updater share the prefix but are never pruned.
const MANAGED_DAEMON_APP = /^vika-(?!(?:web|updater)$)[a-z][a-z0-9-]{0,31}$/;

function writeJson(response, statusCode, body) {
  const text = JSON.stringify(body);
  response.writeHead(statusCode, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(text),
  });
  response.end(text);
}

async function readJsonBody(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) throw new Error('request body is too large');
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
  } catch {
    throw new Error('request body must be JSON');
  }
}

async function removeStaleManagedDaemonApps({ targets, release, pm2Bin, execFileImpl, pm2Env }) {
  const configuredApps = new Set(getUpgradeTargets('all', targets).map(([, spec]) => spec.app));
  const releasesRoot = path.dirname(release);
  const processes = await pm2Processes({ pm2Bin, execFileImpl, pm2Env });
  const removedApps = [];

  for (const processInfo of processes) {
    const app = processInfo?.name;
    if (typeof app !== 'string' || !MANAGED_DAEMON_APP.test(app) || configuredApps.has(app)) continue;
    const execPath = await realpath(processInfo.pm2_env?.pm_exec_path || '').catch(() => null);
    if (!execPath || !isInside(releasesRoot, execPath)) continue;
    await execFileImpl(pm2Bin, ['delete', app], { timeout: 30_000, env: pm2Env });
    removedApps.push(app);
  }

  return removedApps;
}

async function previousConfigForProcess(processInfo, release) {
  const execPath = processInfo?.pm2_env?.pm_exec_path;
  if (!execPath) throw new Error(`${processInfo?.name || 'PM2 app'} has no executable path`);

  const baseDir = path.dirname(path.dirname(release));
  const releasesRoot = path.join(baseDir, 'releases');
  const currentLink = path.join(baseDir, 'current');
  let previousRelease;

  // PM2 preserves the literal script path it was first given. If that path
  // uses `current`, resolving it after publication points at the new release,
  // not the code the live process actually loaded. The publisher's rollback
  // link is the only trustworthy previous release in that case.
  if (isInside(currentLink, path.resolve(execPath))) {
    previousRelease = await realpath(path.join(baseDir, 'rollback')).catch(() => {
      throw new Error(`cannot safely determine the previous release for ${processInfo.name}`);
    });
  } else {
    const resolvedExec = await realpath(execPath).catch(() => {
      throw new Error(`cannot resolve the previous executable for ${processInfo.name}`);
    });
    if (!isInside(releasesRoot, resolvedExec)) {
      throw new Error(`${processInfo.name} is running outside the managed releases directory`);
    }
    const [releaseName] = path.relative(releasesRoot, resolvedExec).split(path.sep);
    previousRelease = await realpath(path.join(releasesRoot, releaseName));
  }

  if (!isInside(releasesRoot, previousRelease)) {
    throw new Error(`previous release for ${processInfo.name} is outside the managed releases directory`);
  }
  const previousConfig = await realpath(path.join(previousRelease, 'ecosystem.config.cjs'));
  if (!isInside(previousRelease, previousConfig)) {
    throw new Error(`previous ecosystem config for ${processInfo.name} resolves outside its release`);
  }
  return previousConfig;
}

async function upgradeApp(spec, {
  release,
  configPath,
  pm2Bin,
  execFileImpl,
  fetchImpl,
  readyOptions,
  pm2Env,
}) {
  const appPm2Env = appControlEnv(spec.app, pm2Env);
  const processes = await pm2Processes({ pm2Bin, execFileImpl, pm2Env: appPm2Env });
  const previous = processes.find((processInfo) => processInfo?.name === spec.app) || null;
  const previousWasOnline = previous?.pm2_env?.status === 'online';
  const previousConfig = previous ? await previousConfigForProcess(previous, release) : null;
  if (previous) {
    await execFileImpl(pm2Bin, ['delete', spec.app], { timeout: 30_000, env: appPm2Env });
  }

  try {
    await startApp(spec, { release, configPath, pm2Bin, execFileImpl, fetchImpl, readyOptions, pm2Env });
  } catch (error) {
    await execFileImpl(pm2Bin, ['delete', spec.app], { timeout: 30_000, env: appPm2Env }).catch(() => {});
    if (previousConfig) {
      await execFileImpl(pm2Bin, [
        'start',
        previousConfig,
        '--only',
        spec.app,
        '--update-env',
      ], { timeout: 60_000, env: appPm2Env }).catch(() => {});
      if (previousWasOnline) {
        await waitUntilReady(spec.readyUrl, fetchImpl, readyOptions).catch(() => {});
      } else {
        await execFileImpl(pm2Bin, ['stop', spec.app], { timeout: 30_000, env: appPm2Env }).catch(() => {});
      }
    }
    throw error;
  }
}

// After an upgrade the updater still runs the old release. It cannot
// replace itself from inside its own pm2 process (pm2 kills the process tree
// on delete), so a detached shell outlives it: the inner subshell is orphaned
// before the delete, then starts vika-updater from the new release.
export function scheduleUpdaterRestart({ release, pm2Bin, pm2Env, spawnImpl = spawn }) {
  const releasePm2 = path.join(release, 'bin', 'pm2');
  const script = '(sleep 2; "$PM2" delete vika-updater >/dev/null 2>&1; '
    + '"$PM2" start "$CONFIG" --only vika-updater --update-env >/dev/null 2>&1; '
    + '"$PM2" save >/dev/null 2>&1) </dev/null >/dev/null 2>&1 &';
  const child = spawnImpl('/bin/sh', ['-c', script], {
    detached: true,
    stdio: 'ignore',
    env: {
      ...pm2Env,
      PM2: fs.existsSync(releasePm2) ? releasePm2 : pm2Bin,
      CONFIG: path.join(release, 'ecosystem.config.cjs'),
    },
  });
  child.unref();
}

export async function upgradeTarget(target, {
  releaseLink = process.env.VIKA_UPDATER_RELEASE_LINK || defaultReleaseLink(),
  pm2Bin = process.env.PM2_BIN || 'pm2',
  execFileImpl = execFile,
  fetchImpl = globalThis.fetch,
  readyOptions,
  pm2Env = cleanPm2ControlEnv(),
  publishReleaseImpl = publishLatestRelease,
  publishOptions = {},
  // Only the running updater replaces itself (createUpdaterServer turns this
  // on); a direct call, as in tests, never touches pm2's vika-updater.
  restartSelfImpl = null,
  selfEntry = fileURLToPath(import.meta.url),
} = {}) {
  // The manifest is read on every request, so an instance added since the
  // updater started is already a valid target.
  assertUpgradeTarget(target, loadTargets());
  const deployLock = path.join(path.dirname(path.resolve(releaseLink)), 'deploy.lock');
  try {
    await mkdir(deployLock, { mode: 0o700 });
  } catch (error) {
    if (error?.code === 'EEXIST') throw new Error('another vika deploy or upgrade is running');
    throw error;
  }
  try {
    // Setup may remove an instance while this request is acquiring the lock.
    // Use only the configuration observed after we own it.
    const targets = loadTargets();
    assertUpgradeTarget(target, targets);
    const publication = await publishReleaseImpl({
      baseDir: path.dirname(path.resolve(releaseLink)),
      ...publishOptions,
    });
    const release = await resolveRelease(releaseLink);
    const publishedRelease = await realpath(publication.release);
    if (publishedRelease !== release) throw new Error('publisher did not activate the returned release');
    const configPath = await realpath(path.join(release, 'ecosystem.config.cjs'));
    if (!isInside(release, configPath)) throw new Error('ecosystem config resolves outside the release');

    const upgradedApps = [];
    for (const [, spec] of getUpgradeTargets(target, targets)) {
      await upgradeApp(spec, {
        release,
        configPath,
        pm2Bin,
        execFileImpl,
        fetchImpl,
        readyOptions,
        pm2Env,
      });
      upgradedApps.push(spec.app);
    }

    // Single-instance installations roll their shared Activity Web with their
    // only daemon. Multi-instance installs keep the explicit `all` boundary.
    if (shouldUpgradeSharedWeb(target, targets)) {
      await upgradeApp(SHARED_WEB, {
        release,
        configPath,
        pm2Bin,
        execFileImpl,
        fetchImpl,
        readyOptions,
        pm2Env,
      });
      upgradedApps.push(SHARED_WEB.app);
    }
    const removedApps = target === 'all'
      ? await removeStaleManagedDaemonApps({ targets, release, pm2Bin, execFileImpl, pm2Env })
      : [];
    await execFileImpl(pm2Bin, ['save'], { timeout: 30_000, env: pm2Env });
    const realSelf = await realpath(selfEntry).catch(() => selfEntry);
    const restartsUpdater = Boolean(restartSelfImpl) && !isInside(release, realSelf);
    if (restartsUpdater) restartSelfImpl({ release, pm2Bin, pm2Env });

    return {
      target,
      ...(upgradedApps.length === 1 ? { app: upgradedApps[0] } : {}),
      apps: upgradedApps,
      removedApps,
      release,
      commit: publication.commit,
      version: publication.version,
      published: publication.published === true,
      restartsUpdater,
      status: 'ready',
    };
  } finally {
    await rmdir(deployLock).catch((error) => {
      if (error?.code !== 'ENOENT') throw error;
    });
  }
}

async function removeStaleSocket(socketPath) {
  const info = await lstat(socketPath).catch((error) => {
    if (error?.code === 'ENOENT') return null;
    throw error;
  });
  if (!info) return;
  if (!info.isSocket()) throw new Error(`refusing to replace non-socket path: ${socketPath}`);
  const stale = await new Promise((resolve, reject) => {
    const socket = net.createConnection(socketPath);
    socket.setTimeout(500);
    socket.once('connect', () => {
      socket.destroy();
      reject(new Error(`updater is already listening on ${socketPath}`));
    });
    socket.once('timeout', () => {
      socket.destroy();
      reject(new Error(`updater socket is busy: ${socketPath}`));
    });
    socket.once('error', (error) => {
      if (error?.code === 'ECONNREFUSED' || error?.code === 'ENOENT') resolve(true);
      else reject(error);
    });
  });
  if (!stale) return;
  const confirmed = await lstat(socketPath).catch((error) => {
    if (error?.code === 'ENOENT') return null;
    throw error;
  });
  if (!confirmed) return;
  if (
    !confirmed.isSocket()
    || confirmed.dev !== info.dev
    || confirmed.ino !== info.ino
    || (typeof process.getuid === 'function' && confirmed.uid !== process.getuid())
  ) {
    throw new Error(`updater socket changed during stale check: ${socketPath}`);
  }
  await rm(socketPath);
}

export async function createUpdaterServer({
  socketPath = process.env.VIKA_UPDATER_SOCKET || defaultSocketPath(),
  upgrade = (target) => upgradeTarget(target, { restartSelfImpl: scheduleUpdaterRestart }),
  requestBodyTimeoutMs = 5000,
} = {}) {
  let activeTarget = null;
  const server = http.createServer(async (request, response) => {
    request.setTimeout(requestBodyTimeoutMs, () => request.destroy(new Error('request body timed out')));
    if (request.method === 'GET' && request.url === '/ready') {
      writeJson(response, 200, { status: 'ready', activeTarget });
      return;
    }
    if (request.method !== 'POST' || request.url !== '/v1/upgrade') {
      writeJson(response, 404, { error: 'not found' });
      return;
    }
    if (activeTarget) {
      writeJson(response, 409, { error: `upgrade already running for ${activeTarget}` });
      return;
    }
    activeTarget = 'pending';
    let body;
    try {
      body = await readJsonBody(request);
      request.setTimeout(0);
      assertUpgradeTarget(body.target);
    } catch (error) {
      writeJson(response, 400, { error: error.message });
      activeTarget = null;
      return;
    }
    activeTarget = body.target;
    try {
      const result = await upgrade(body.target);
      writeJson(response, 200, result);
    } catch (error) {
      writeJson(response, 502, { error: error.message });
    } finally {
      activeTarget = null;
    }
  });

  const resolvedSocketPath = path.resolve(socketPath);
  await mkdir(path.dirname(resolvedSocketPath), { recursive: true, mode: 0o700 });
  await removeStaleSocket(resolvedSocketPath);
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(resolvedSocketPath, () => {
      server.off('error', reject);
      resolve();
    });
  });
  await chmod(resolvedSocketPath, 0o600);
  const socketIdentity = await lstat(resolvedSocketPath);

  return {
    server,
    socketPath: resolvedSocketPath,
    async close() {
      await new Promise((resolve, reject) => server.close((error) => (error ? reject(error) : resolve())));
      const current = await lstat(resolvedSocketPath).catch((error) => {
        if (error?.code === 'ENOENT') return null;
        throw error;
      });
      if (current?.isSocket() && current.dev === socketIdentity.dev && current.ino === socketIdentity.ino) {
        await rm(resolvedSocketPath);
      }
    },
  };
}
