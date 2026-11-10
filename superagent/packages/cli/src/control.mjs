// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { pm2Bin as platformPm2Bin } from '../../updater/src/pm2-platform.mjs';

import { capabilities } from '../../daemon/src/capabilities.mjs';

import { homeDir } from '../../daemon/src/platform/index.mjs';

import { execFile as execFileCallback, spawn } from '../../daemon/src/platform/index.mjs';
import fs from 'node:fs';
import { realpath } from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import {
  appControlEnv,
  cleanPm2ControlEnv,
  isInside,
  pm2Processes,
  resolveRelease,
  startApp,
  verifyPm2App,
  waitUntilReady,
} from '../../updater/src/pm2.mjs';
import { SHARED_WEB, defaultReleaseLink, defaultSocketPath, loadTargets } from '../../updater/src/targets.mjs';
import { ignoredFieldsNotice, imList, loadInstances } from './instances.mjs';
import { manifestPath } from './manifest.mjs';
import { t } from './i18n.mjs';
import { padEnd, spinner, styler } from './ui.mjs';

// start / stop / restart / status / logs run the release already installed
// under ~/.vika/current. Nothing here fetches code or publishes a release.

const execFile = promisify(execFileCallback);
const UPDATER_APP = 'vika-updater';
// This command's own release (or source checkout); bin/pm2 runs its pinned pm2.
const RELEASE_ROOT = fileURLToPath(new URL('../../../', import.meta.url));

export function pm2Defaults(env = process.env) {
  const releasePm2 = platformPm2Bin(RELEASE_ROOT);
  return {
    pm2Bin: env.PM2_BIN || (fs.existsSync(releasePm2) ? releasePm2 : 'pm2'),
    // Vika's own pm2 daemon, apart from any pm2 the user runs.
    pm2Env: cleanPm2ControlEnv({ ...env, PM2_HOME: env.PM2_HOME || path.join(homeDir(env) || '', '.vika', 'pm2') }),
  };
}

function defaults(options = {}) {
  const env = options.env ?? process.env;
  const pm2 = pm2Defaults(env);
  return {
    env,
    pm2Bin: options.pm2Bin ?? pm2.pm2Bin,
    execFileImpl: options.execFileImpl ?? execFile,
    fetchImpl: options.fetchImpl ?? globalThis.fetch,
    readyOptions: options.readyOptions,
    releaseLink: options.releaseLink ?? env.VIKA_UPDATER_RELEASE_LINK ?? defaultReleaseLink(homeDir(env)),
    socketPath: options.socketPath ?? env.VIKA_UPDATER_SOCKET ?? defaultSocketPath(homeDir(env)),
    pm2Env: options.pm2Env ?? pm2.pm2Env,
    stdout: options.stdout ?? process.stdout,
  };
}

// The apps a target names: one instance, `web`, `updater`, or (no target)
// every instance plus the shared Web and the updater.
export function appsFor(target, targets = loadTargets()) {
  const instances = Object.entries(targets).map(([id, spec]) => ({ id, ...spec }));
  const web = { id: 'web', ...SHARED_WEB };
  const updater = { id: 'updater', app: UPDATER_APP };
  if (target === undefined) return [...instances, web, ...(capabilities.updater ? [updater] : [])];
  if (target === 'updater' && !capabilities.updater) throw new Error('Windows source installations do not support the release updater.');
  if (target === 'web') return [web];
  if (target === 'updater') return [updater];
  const instance = instances.find((spec) => spec.id === target);
  if (!instance) {
    throw new Error(`unknown instance ${target}; configured: ${[...instances.map((spec) => spec.id), 'web', 'updater'].join(', ')}`);
  }
  return [instance];
}

async function installedRelease(releaseLink) {
  if (!fs.existsSync(releaseLink)) {
    throw new Error(`no Vika release is installed at ${releaseLink}; run the installer first (install.sh)`);
  }
  const release = await resolveRelease(releaseLink);
  const configPath = await realpath(path.join(release, 'ecosystem.config.cjs'));
  if (!isInside(release, configPath)) throw new Error('ecosystem config resolves outside the release');
  return { release, configPath };
}

function updaterReady(socketPath) {
  return new Promise((resolve) => {
    const request = http.get({ socketPath, path: '/ready', timeout: 1500 }, (response) => {
      response.resume();
      resolve(response.statusCode === 200);
    });
    request.once('timeout', () => request.destroy());
    request.once('error', () => resolve(false));
  });
}

async function waitForUpdater(socketPath, { attempts = 30, intervalMs = 1000 } = {}) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (await updaterReady(socketPath)) return;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`updater did not become ready on ${socketPath}`);
}

async function startOne(spec, context) {
  const { release, configPath, pm2Bin, execFileImpl, fetchImpl, readyOptions, pm2Env, socketPath } = context;
  if (spec.app !== UPDATER_APP) {
    await startApp(spec, { release, configPath, pm2Bin, execFileImpl, fetchImpl, readyOptions, pm2Env });
    return;
  }
  await execFileImpl(pm2Bin, ['start', configPath, '--only', spec.app, '--update-env'], { timeout: 60_000, env: pm2Env });
  await verifyPm2App({ app: spec.app, release, pm2Bin, execFileImpl, pm2Env });
  await waitForUpdater(socketPath, readyOptions);
}

// One app's start, with a spinner on a terminal; the line says how it ended.
async function step(context, label, done, run) {
  const spinning = spinner(context.stdout, label, { env: context.env });
  try {
    await run();
    spinning.succeed(done);
  } catch (error) {
    spinning.fail(t(`${label}：没有成功`, `${label}: failed`));
    throw error;
  }
}

// Starts what is not running; an app that is online already is left alone.
export async function startCommand(target, options) {
  const context = defaults(options);
  const apps = appsFor(target, loadTargets(context.env));
  Object.assign(context, await installedRelease(context.releaseLink));
  const running = new Map((await pm2Processes(context)).map((processInfo) => [processInfo?.name, processInfo]));
  const s = styler(context.stdout, context.env);
  for (const spec of apps) {
    const current = running.get(spec.app);
    if (current?.pm2_env?.status === 'online') {
      await verifyPm2App({ ...context, app: spec.app });
      if (spec.app === UPDATER_APP) await waitForUpdater(context.socketPath, context.readyOptions);
      else await waitUntilReady(spec.readyUrl, context.fetchImpl, context.readyOptions);
      context.stdout.write(`${s.gray('·')} ${t(`${spec.app} 已经在运行，已就绪`, `${spec.app} is running already and ready`)}\n`);
      continue;
    }
    await step(context, t(`启动 ${spec.app}`, `Starting ${spec.app}`), t(`${spec.app} 已就绪`, `${spec.app} is ready`), async () => {
      if (current) {
        await context.execFileImpl(context.pm2Bin, ['delete', spec.app], { timeout: 30_000, env: appControlEnv(spec.app, context.pm2Env) });
      }
      await startOne(spec, context);
    });
  }
  await context.execFileImpl(context.pm2Bin, ['save'], { timeout: 30_000, env: context.pm2Env });
  return 0;
}

export async function restartCommand(target, options) {
  const context = defaults(options);
  const apps = appsFor(target, loadTargets(context.env));
  Object.assign(context, await installedRelease(context.releaseLink));
  const running = new Set((await pm2Processes(context)).map((processInfo) => processInfo?.name));
  for (const spec of apps) {
    await step(context, t(`重启 ${spec.app}`, `Restarting ${spec.app}`), t(`${spec.app} 已就绪`, `${spec.app} is ready`), async () => {
      if (running.has(spec.app)) {
        await context.execFileImpl(context.pm2Bin, ['delete', spec.app], { timeout: 30_000, env: appControlEnv(spec.app, context.pm2Env) });
      }
      await startOne(spec, context);
    });
  }
  await context.execFileImpl(context.pm2Bin, ['save'], { timeout: 30_000, env: context.pm2Env });
  return 0;
}

export async function stopCommand(target, options) {
  const context = defaults(options);
  const apps = appsFor(target, loadTargets(context.env));
  const running = new Set((await pm2Processes(context)).map((processInfo) => processInfo?.name));
  for (const spec of apps) {
    if (!running.has(spec.app)) continue;
    await step(context, t(`停止 ${spec.app}`, `Stopping ${spec.app}`), t(`${spec.app} 已停止`, `${spec.app} is stopped`), () => context.execFileImpl(context.pm2Bin, ['stop', spec.app], { timeout: 30_000, env: context.pm2Env }));
  }
  await context.execFileImpl(context.pm2Bin, ['save'], { timeout: 30_000, env: context.pm2Env });
  return 0;
}

// Setup removes exact managed processes, including stopped ones. Check every
// app before deleting any, and use PM2 ids so a same-name app is never swept up.
export async function removeManagedApps(targets, options = {}) {
  const context = defaults(options);
  const processes = await pm2Processes(context);
  const selected = [];
  // The shared Web is checked too before setup refreshes its routes.
  for (const target of new Set([...targets, ...(options.checkTargets ?? [])])) {
    if (!/^[a-z][a-z0-9-]{0,31}$/.test(target) || target === 'all') throw new Error(`invalid removal target: ${target}`);
    const app = `vika-${target}`;
    const matches = processes.filter((info) => info?.name === app);
    if (!matches.length) continue;
    if (matches.length !== 1) throw new Error(`expected exactly one PM2 app named ${app}`);
    const [info] = matches;
    const releasesRoot = await realpath(path.join(path.dirname(context.releaseLink), 'releases'));
    const execPath = await realpath(info.pm2_env?.pm_exec_path || '');
    const component = ['web', 'updater'].includes(target) ? target : 'daemon';
    const relative = path.relative(releasesRoot, execPath).split(path.sep);
    if (!isInside(releasesRoot, execPath) || relative.length !== 5
      || relative.slice(1).join('/') !== `packages/${component}/src/main.mjs`) {
      throw new Error(`${app} is outside the managed releases or has an unexpected executable; refusing to remove it`);
    }
    if (component !== 'web') {
      const configured = info.pm2_env?.VIKA_CONFIG;
      if (!configured || await realpath(configured) !== await realpath(manifestPath(context.env))) {
        throw new Error(`${app} belongs to a different manifest; refusing to remove it`);
      }
      if (component === 'daemon' && info.pm2_env?.VIKA_INSTANCE_ID !== target) {
        throw new Error(`${app} belongs to a different instance; refusing to remove it`);
      }
    }
    if (!Number.isSafeInteger(info.pm_id) || info.pm_id < 0) throw new Error(`${app} has no valid PM2 id`);
    if (targets.includes(target)) selected.push(info);
  }
  for (const info of selected) {
    await context.execFileImpl(context.pm2Bin, ['delete', String(info.pm_id)], { timeout: 60_000, env: context.pm2Env });
  }
  // Without --force, PM2 keeps its previous dump when the list is empty and
  // resurrects the deleted instances at the next login.
  await context.execFileImpl(context.pm2Bin, ['save', '--force'], { timeout: 30_000, env: context.pm2Env });
}

async function ready(spec, context) {
  if (spec.app === UPDATER_APP) return updaterReady(context.socketPath);
  try {
    const response = await context.fetchImpl(spec.readyUrl, { signal: AbortSignal.timeout(1500) });
    return response.ok;
  } catch {
    return false;
  }
}

const STATES = () => t(
  { online: '运行中', stopped: '已停止', stopping: '停止中', launching: '启动中', errored: '出错了', 'one-launch-status': '启动中' },
  { online: 'online', stopped: 'stopped', stopping: 'stopping', launching: 'starting', errored: 'errored', 'one-launch-status': 'starting' },
);

export async function statusCommand(options) {
  const context = defaults(options);
  const apps = appsFor(undefined, loadTargets(context.env));
  const processes = new Map((await pm2Processes(context)).map((processInfo) => [processInfo?.name, processInfo]));
  let deployment = null;
  try { deployment = loadInstances(context.env); } catch {}
  const ims = new Map((deployment?.agents ?? []).map((agent) => [`vika-${agent.id}`, imList(agent.connectors, agent.emailAccounts?.length)]));
  const s = styler(context.stdout, context.env);
  const rows = [];
  for (const spec of apps) {
    const status = processes.get(spec.app)?.pm2_env?.status ?? null;
    const readiness = status === 'online' ? await ready(spec, context) : null;
    rows.push({ app: spec.app, status, readiness, im: ims.get(spec.app) ?? '' });
  }
  const width = Math.max(...rows.map(({ app }) => app.length));
  for (const { app, status, readiness, im } of rows) {
    const state = status ? STATES()[status] ?? status : t('未启动', 'not started');
    const dot = readiness ? s.green('●') : status === 'online' ? s.yellow('●') : status === 'errored' ? s.red('●') : s.gray('○');
    const answer = readiness === null ? '-' : readiness ? t('就绪', 'ready') : t('未就绪', 'not ready');
    const line = `${dot} ${padEnd(app, width)}  ${padEnd(state, t(8, 11))}  ${padEnd(answer, t(6, 9))}  ${s.gray(im)}`;
    context.stdout.write(`${line.trimEnd()}\n`);
  }
  const notice = ignoredFieldsNotice(deployment?.ignoredFields, deployment?.manifestPath);
  if (notice) context.stdout.write(`${s.yellow('⚠')} ${notice}\n`);
  return 0;
}

export function logsCommand(target, options = {}) {
  const context = defaults(options);
  const [spec] = appsFor(target, loadTargets(context.env));
  const child = spawn(context.pm2Bin, ['logs', spec.app, '--lines', '100'], { stdio: 'inherit', env: context.pm2Env });
  return new Promise((resolve) => child.once('exit', (code) => resolve(code ?? 0)));
}
