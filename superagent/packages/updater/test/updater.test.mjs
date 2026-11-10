// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import {
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  symlink,
  writeFile,
} from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

process.env.VIKA_UPDATER_TARGETS_JSON = JSON.stringify({
  alpha: {
    app: 'vika-alpha',
    readyUrl: 'http://127.0.0.1:3738/ready',
    upgradeSharedWeb: false,
  },
  beta: {
    app: 'vika-beta',
    readyUrl: 'http://127.0.0.1:3740/ready',
    upgradeSharedWeb: false,
  },
});
const {
  cleanPm2ControlEnv,
  cleanWebPm2ControlEnv,
  createUpdaterServer,
  scheduleUpdaterRestart,
  upgradeTarget,
} = await import('../src/server.mjs');

async function fixtureTempRoot(prefix) {
  return realpath(await mkdtemp(path.join(os.tmpdir(), prefix)));
}

async function fixtureRelease() {
  const root = await fixtureTempRoot('vika-updater-test-');
  const base = path.join(root, 'vika');
  const release = path.join(base, 'releases', '0.4.0-test');
  await mkdir(release, { recursive: true });
  await writeFile(path.join(release, 'ecosystem.config.cjs'), 'module.exports = {};\n');
  await symlink(release, path.join(base, 'current'));
  return { root, release, releaseLink: path.join(base, 'current') };
}

function useFixtureRelease(fixture) {
  return async () => ({
    release: fixture.release,
    commit: '1111111111111111111111111111111111111111',
    published: false,
  });
}

async function fixtureAppRelease(fixture, name, entryRelative) {
  const release = path.join(path.dirname(fixture.release), name);
  const entry = path.join(release, entryRelative);
  await mkdir(path.dirname(entry), { recursive: true });
  await writeFile(entry, '');
  await writeFile(path.join(release, 'ecosystem.config.cjs'), 'module.exports = {};\n');
  return { release, entry, config: path.join(release, 'ecosystem.config.cjs') };
}

function replacePm2Harness({ app, initialEntry, entriesByConfig }) {
  let activeEntry = initialEntry;
  const events = [];

  return {
    events,
    get activeEntry() { return activeEntry; },
    execFileImpl: async (_command, args) => {
      events.push(['pm2', ...args]);
      const [operation] = args;
      if (operation === 'jlist') {
        return {
          stdout: JSON.stringify(activeEntry ? [{
            name: app,
            pm2_env: { status: 'online', pm_exec_path: activeEntry },
          }] : []),
        };
      }
      if (operation === 'delete') {
        assert.equal(args[1], app);
        activeEntry = null;
        return { stdout: '' };
      }
      if (operation === 'start' || operation === 'startOrReload') {
        const nextEntry = entriesByConfig.get(args[1]);
        assert.ok(nextEntry, `unexpected ecosystem config: ${args[1]}`);
        assert.deepEqual(args.slice(2), ['--only', app, '--update-env']);
        // Model PM2's problematic behavior: startOrReload preserves the old
        // pm_exec_path when an app with the same name already exists.
        if (operation === 'start' || !activeEntry) activeEntry = nextEntry;
        return { stdout: '' };
      }
      if (operation === 'save') return { stdout: '' };
      throw new Error(`unexpected PM2 operation: ${operation}`);
    },
  };
}

function post(socketPath, body) {
  const payload = JSON.stringify(body);
  return new Promise((resolve, reject) => {
    const request = http.request({
      socketPath,
      path: '/v1/upgrade',
      method: 'POST',
      headers: { 'content-length': Buffer.byteLength(payload) },
    }, (response) => {
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => resolve({
        status: response.statusCode,
        body: JSON.parse(Buffer.concat(chunks).toString('utf8')),
      }));
    });
    request.once('error', reject);
    request.end(payload);
  });
}

test('upgradeTarget cold-starts only the allowlisted Beta app from current release', async () => {
  const fixture = await fixtureRelease();
  const calls = [];
  let jlistCalls = 0;
  const daemonEntry = path.join(fixture.release, 'packages', 'daemon', 'src', 'main.mjs');
  await mkdir(path.dirname(daemonEntry), { recursive: true });
  await writeFile(daemonEntry, '');
  const result = await upgradeTarget('beta', {
    releaseLink: fixture.releaseLink,
    publishReleaseImpl: useFixtureRelease(fixture),
    pm2Bin: '/fake/pm2',
    execFileImpl: async (command, args, options) => {
      calls.push({ command, args, options });
      if (args[0] === 'jlist') {
        jlistCalls += 1;
        return {
          stdout: JSON.stringify(jlistCalls === 1 ? [] : [{
            name: 'vika-beta',
            pm2_env: { status: 'online', pm_exec_path: daemonEntry },
          }]),
        };
      }
      return { stdout: '' };
    },
    fetchImpl: async () => ({ ok: true }),
    readyOptions: { attempts: 1, intervalMs: 0 },
    pm2Env: { HOME: '/safe-home', PATH: '/safe-bin' },
  });

  assert.equal(result.app, 'vika-beta');
  assert.equal(result.release, fixture.release);
  assert.deepEqual(calls, [
    {
      command: '/fake/pm2',
      args: ['jlist'],
      options: {
        timeout: 30_000,
        env: { HOME: '/safe-home', PATH: '/safe-bin' },
      },
    },
    {
      command: '/fake/pm2',
      args: [
        'start',
        path.join(fixture.release, 'ecosystem.config.cjs'),
        '--only',
        'vika-beta',
        '--update-env',
      ],
      options: {
        timeout: 60_000,
        env: { HOME: '/safe-home', PATH: '/safe-bin' },
      },
    },
    {
      command: '/fake/pm2',
      args: ['jlist'],
      options: {
        timeout: 1_000,
        env: { HOME: '/safe-home', PATH: '/safe-bin' },
      },
    },
    {
      command: '/fake/pm2',
      args: ['save'],
      options: {
        timeout: 30_000,
        env: { HOME: '/safe-home', PATH: '/safe-bin' },
      },
    },
  ]);
});

test('upgrade all rolls both daemons and the shared web from one ecosystem config', async () => {
  const fixture = await fixtureRelease();
  const daemonEntry = path.join(fixture.release, 'packages', 'daemon', 'src', 'main.mjs');
  const webEntry = path.join(fixture.release, 'packages', 'web', 'src', 'main.mjs');
  await mkdir(path.dirname(daemonEntry), { recursive: true });
  await mkdir(path.dirname(webEntry), { recursive: true });
  await writeFile(daemonEntry, '');
  await writeFile(webEntry, '');
  const calls = [];
  const fetches = [];
  const processes = [
    { name: 'vika-alpha', pm2_env: { status: 'online', pm_exec_path: daemonEntry } },
    { name: 'vika-beta', pm2_env: { status: 'online', pm_exec_path: daemonEntry } },
    { name: 'vika-web', pm2_env: { status: 'online', pm_exec_path: webEntry } },
  ];
  const result = await upgradeTarget('all', {
    releaseLink: fixture.releaseLink,
    publishReleaseImpl: useFixtureRelease(fixture),
    pm2Bin: '/fake/pm2',
    execFileImpl: async (_command, args) => {
      calls.push(args);
      return { stdout: args[0] === 'jlist' ? JSON.stringify(processes) : '' };
    },
    fetchImpl: async (url) => { fetches.push(url); return { ok: true }; },
    readyOptions: { attempts: 1, intervalMs: 0 },
  });

  assert.deepEqual(result.apps, ['vika-alpha', 'vika-beta', 'vika-web']);
  assert.deepEqual(
    calls.filter((args) => args[0] === 'start').map((args) => [args[1], args[3]]),
    [
      [path.join(fixture.release, 'ecosystem.config.cjs'), 'vika-alpha'],
      [path.join(fixture.release, 'ecosystem.config.cjs'), 'vika-beta'],
      [path.join(fixture.release, 'ecosystem.config.cjs'), 'vika-web'],
    ],
  );
  assert.ok(!calls.some((args) => args[0] === 'startOrReload'));
  for (const app of ['vika-alpha', 'vika-beta', 'vika-web']) {
    assert.ok(calls.some((args) => args[0] === 'delete' && args[1] === app));
  }
  assert.deepEqual(fetches, [
    'http://127.0.0.1:3738/ready',
    'http://127.0.0.1:3740/ready',
    'http://127.0.0.1:3737/ready',
  ]);
  assert.deepEqual(calls.at(-1), ['save']);
});

test('upgrade all removes only stale daemon apps owned by the managed releases directory', async () => {
  const fixture = await fixtureRelease();
  const daemonEntry = path.join(fixture.release, 'packages', 'daemon', 'src', 'main.mjs');
  const webEntry = path.join(fixture.release, 'packages', 'web', 'src', 'main.mjs');
  const foreignEntry = path.join(fixture.root, 'foreign', 'main.mjs');
  for (const entry of [daemonEntry, webEntry, foreignEntry]) {
    await mkdir(path.dirname(entry), { recursive: true });
    await writeFile(entry, '');
  }
  let processes = [
    { name: 'vika-alpha', pm2_env: { status: 'online', pm_exec_path: daemonEntry } },
    { name: 'vika-beta', pm2_env: { status: 'online', pm_exec_path: daemonEntry } },
    { name: 'vika-web', pm2_env: { status: 'online', pm_exec_path: webEntry } },
    { name: 'vika-retired', pm2_env: { status: 'online', pm_exec_path: daemonEntry } },
    { name: 'vika-foreign', pm2_env: { status: 'online', pm_exec_path: foreignEntry } },
    { name: 'unrelated-service', pm2_env: { status: 'online', pm_exec_path: daemonEntry } },
  ];
  const deleted = [];

  const result = await upgradeTarget('all', {
    releaseLink: fixture.releaseLink,
    publishReleaseImpl: useFixtureRelease(fixture),
    pm2Bin: '/fake/pm2',
    execFileImpl: async (_command, args) => {
      const [operation] = args;
      if (operation === 'jlist') return { stdout: JSON.stringify(processes) };
      if (operation === 'delete') {
        deleted.push(args[1]);
        processes = processes.filter((row) => row.name !== args[1]);
        return { stdout: '' };
      }
      if (operation === 'start') {
        const app = args[3];
        processes.push({
          name: app,
          pm2_env: {
            status: 'online',
            pm_exec_path: app === 'vika-web' ? webEntry : daemonEntry,
          },
        });
        return { stdout: '' };
      }
      if (operation === 'save') return { stdout: '' };
      throw new Error(`unexpected PM2 operation: ${operation}`);
    },
    fetchImpl: async () => ({ ok: true }),
    readyOptions: { attempts: 1, intervalMs: 0 },
  });

  assert.deepEqual(result.removedApps, ['vika-retired']);
  assert.ok(deleted.includes('vika-retired'));
  assert.ok(!deleted.includes('vika-foreign'));
  assert.ok(!deleted.includes('unrelated-service'));
  assert.ok(processes.some((row) => row.name === 'vika-foreign'));
  assert.ok(processes.some((row) => row.name === 'unrelated-service'));
});

test('PM2 control commands discard metadata inherited from a managed caller', () => {
  assert.deepEqual(cleanPm2ControlEnv({
    HOME: '/safe-home',
    PATH: '/safe-bin',
    PM2_HOME: '/safe-pm2',
    LANG: 'C.UTF-8',
    name: 'vika',
    pm_id: '28',
    pm_exec_path: '/old/release/main.mjs',
    cwd: '/old/release',
    VIKA_LARK_PROFILE: 'secret-profile',
    VIKA_CONFIG: '/safe-home/custom/config.json',
    VIKA_LOG_LEVEL: 'debug',
    ARK_API_KEY: 'system-only-secret',
    VIKA_IGNORED_SENDER_IDS: '',
  }), {
    HOME: '/safe-home',
    PATH: '/safe-bin',
    PM2_HOME: '/safe-pm2',
    LANG: 'C.UTF-8',
    VIKA_CONFIG: '/safe-home/custom/config.json',
    VIKA_LOG_LEVEL: 'debug',
    ARK_API_KEY: 'system-only-secret',
    VIKA_IGNORED_SENDER_IDS: '',
  });
});

test('upgradeTarget replaces an app whose PM2 exec path belongs to an old release', async () => {
  const fixture = await fixtureRelease();
  const entryRelative = path.join('packages', 'daemon', 'src', 'main.mjs');
  const previous = await fixtureAppRelease(fixture, '0.4.0-previous', entryRelative);
  const currentEntry = path.join(fixture.release, entryRelative);
  await mkdir(path.dirname(currentEntry), { recursive: true });
  await writeFile(currentEntry, '');
  const currentConfig = path.join(fixture.release, 'ecosystem.config.cjs');
  const pm2 = replacePm2Harness({
    app: 'vika-beta',
    initialEntry: previous.entry,
    entriesByConfig: new Map([
      [currentConfig, currentEntry],
      [previous.config, previous.entry],
    ]),
  });

  const result = await upgradeTarget('beta', {
    releaseLink: fixture.releaseLink,
    publishReleaseImpl: useFixtureRelease(fixture),
    execFileImpl: pm2.execFileImpl,
    fetchImpl: async (url) => {
      pm2.events.push(['ready', url, pm2.activeEntry]);
      return { ok: true };
    },
    readyOptions: { attempts: 1, intervalMs: 0 },
  });

  assert.equal(result.status, 'ready');
  assert.equal(pm2.activeEntry, currentEntry);
  assert.deepEqual(pm2.events, [
    ['pm2', 'jlist'],
    ['pm2', 'delete', 'vika-beta'],
    ['pm2', 'start', currentConfig, '--only', 'vika-beta', '--update-env'],
    ['pm2', 'jlist'],
    ['ready', 'http://127.0.0.1:3740/ready', currentEntry],
    ['pm2', 'save'],
  ]);
});

test('failed replacement restores the app from the release that owned its old exec path', async () => {
  const fixture = await fixtureRelease();
  const entryRelative = path.join('packages', 'daemon', 'src', 'main.mjs');
  const previous = await fixtureAppRelease(fixture, '0.4.0-previous', entryRelative);
  const currentEntry = path.join(fixture.release, entryRelative);
  await mkdir(path.dirname(currentEntry), { recursive: true });
  await writeFile(currentEntry, '');
  const currentConfig = path.join(fixture.release, 'ecosystem.config.cjs');
  const pm2 = replacePm2Harness({
    app: 'vika-beta',
    initialEntry: previous.entry,
    entriesByConfig: new Map([
      [currentConfig, currentEntry],
      [previous.config, previous.entry],
    ]),
  });

  await assert.rejects(upgradeTarget('beta', {
    releaseLink: fixture.releaseLink,
    publishReleaseImpl: useFixtureRelease(fixture),
    execFileImpl: pm2.execFileImpl,
    fetchImpl: async (url) => {
      pm2.events.push(['ready', url, pm2.activeEntry]);
      return { ok: pm2.activeEntry === previous.entry, status: 503 };
    },
    readyOptions: { attempts: 1, intervalMs: 0 },
  }), /did not become ready/);

  assert.equal(pm2.activeEntry, previous.entry);
  assert.deepEqual(pm2.events.filter((event) => event[1] !== 'save'), [
    ['pm2', 'jlist'],
    ['pm2', 'delete', 'vika-beta'],
    ['pm2', 'start', currentConfig, '--only', 'vika-beta', '--update-env'],
    ['pm2', 'jlist'],
    ['ready', 'http://127.0.0.1:3740/ready', currentEntry],
    ['pm2', 'delete', 'vika-beta'],
    ['pm2', 'start', previous.config, '--only', 'vika-beta', '--update-env'],
    ['ready', 'http://127.0.0.1:3740/ready', previous.entry],
  ]);
});

test('web upgrade and old-release rollback never inherit Ark credentials from PM2 control', async () => {
  const fixture = await fixtureRelease();
  const daemonEntry = path.join(fixture.release, 'packages', 'daemon', 'src', 'main.mjs');
  const webEntry = path.join(fixture.release, 'packages', 'web', 'src', 'main.mjs');
  const previous = await fixtureAppRelease(
    fixture,
    '0.4.0-previous-web',
    path.join('packages', 'web', 'src', 'main.mjs'),
  );
  await mkdir(path.dirname(daemonEntry), { recursive: true });
  await mkdir(path.dirname(webEntry), { recursive: true });
  await writeFile(daemonEntry, '');
  await writeFile(webEntry, '');

  const active = new Map([['vika-web', previous.entry]]);
  const starts = [];
  const pm2Env = {
    HOME: '/safe-home',
    PATH: '/safe-bin',
    ARK_API_KEY: 'sentinel-key',
    ARK_SESSION_ID: 'sentinel-ark-session',
    VOLCENGINE_API_KEY: 'sentinel-volc-key',
    VIKA_VOLC_SESSION_ID: 'sentinel-seed',
    SESSION_ID: 'sentinel-generic-session',
  };
  const execFileImpl = async (_command, args, options) => {
    const [operation] = args;
    if (operation === 'jlist') {
      return {
        stdout: JSON.stringify([...active].map(([name, entry]) => ({
          name,
          pm2_env: { status: 'online', pm_exec_path: entry },
        }))),
      };
    }
    if (operation === 'delete') {
      active.delete(args[1]);
      return { stdout: '' };
    }
    if (operation === 'start') {
      const app = args[3];
      const entry = app === 'vika-web'
        ? (args[1] === previous.config ? previous.entry : webEntry)
        : daemonEntry;
      active.set(app, entry);
      starts.push({ app, config: args[1], env: options.env });
      return { stdout: '' };
    }
    if (operation === 'save') return { stdout: '' };
    throw new Error(`unexpected PM2 operation: ${operation}`);
  };

  await assert.rejects(upgradeTarget('all', {
    releaseLink: fixture.releaseLink,
    publishReleaseImpl: useFixtureRelease(fixture),
    pm2Env,
    execFileImpl,
    fetchImpl: async (url) => ({
      ok: !url.endsWith(':3737/ready') || active.get('vika-web') === previous.entry,
      status: 503,
    }),
    readyOptions: { attempts: 1, intervalMs: 0 },
  }), /did not become ready/);

  const webStarts = starts.filter(({ app }) => app === 'vika-web');
  assert.deepEqual(webStarts.map(({ config }) => config), [
    path.join(fixture.release, 'ecosystem.config.cjs'),
    previous.config,
  ]);
  for (const { env } of webStarts) {
    assert.deepEqual(env, { HOME: '/safe-home', PATH: '/safe-bin' });
  }
  assert.ok(starts.some(({ app, env }) => app.startsWith('vika-')
    && env.ARK_API_KEY === 'sentinel-key'));
  assert.deepEqual(cleanWebPm2ControlEnv(pm2Env), {
    HOME: '/safe-home',
    PATH: '/safe-bin',
  });
});

test('upgradeTarget rejects arbitrary targets before running PM2', async () => {
  let called = false;
  await assert.rejects(
    upgradeTarget('anything', { execFileImpl: async () => { called = true; } }),
    /target must be one of/,
  );
  assert.equal(called, false);
});

test('upgradeTarget rejects inherited object property names as targets', async () => {
  await assert.rejects(upgradeTarget('toString'), /target must be one of/);
});

test('upgradeTarget rechecks the target after acquiring the deploy lock', async () => {
  const fixture = await fixtureRelease();
  const original = process.env.VIKA_UPDATER_TARGETS_JSON;
  let published = false;
  try {
    const pending = upgradeTarget('beta', {
      releaseLink: fixture.releaseLink,
      publishReleaseImpl: async () => { published = true; throw new Error('should not publish'); },
    });
    // mkdir yields; removal can finish before the upgrade acquires its lock.
    const next = JSON.parse(original);
    delete next.beta;
    process.env.VIKA_UPDATER_TARGETS_JSON = JSON.stringify(next);
    await assert.rejects(pending, /target must be one of/);
    assert.equal(published, false);
    await mkdir(path.join(path.dirname(fixture.releaseLink), 'deploy.lock'));
  } finally {
    process.env.VIKA_UPDATER_TARGETS_JSON = original;
  }
});

test('upgradeTarget shares the deploy lock with the installer', async () => {
  const fixture = await fixtureRelease();
  await mkdir(path.join(path.dirname(fixture.releaseLink), 'deploy.lock'));
  let called = false;
  await assert.rejects(
    upgradeTarget('beta', {
      releaseLink: fixture.releaseLink,
      execFileImpl: async () => { called = true; },
    }),
    /another vika deploy or upgrade is running/,
  );
  assert.equal(called, false);
});

test('upgradeTarget publishes upstream before rolling the target', async () => {
  const fixture = await fixtureRelease();
  const daemonEntry = path.join(fixture.release, 'packages', 'daemon', 'src', 'main.mjs');
  await mkdir(path.dirname(daemonEntry), { recursive: true });
  await writeFile(daemonEntry, '');
  const order = [];
  const result = await upgradeTarget('beta', {
    releaseLink: fixture.releaseLink,
    publishReleaseImpl: async ({ baseDir }) => {
      order.push(['publish', baseDir]);
      return {
        release: fixture.release,
        commit: '2222222222222222222222222222222222222222',
        published: true,
      };
    },
    execFileImpl: async (_command, args) => {
      order.push(['pm2', args[0]]);
      return {
        stdout: args[0] === 'jlist'
          ? JSON.stringify([{
            name: 'vika-beta',
            pm2_env: { status: 'online', pm_exec_path: daemonEntry },
          }])
          : '',
      };
    },
    fetchImpl: async () => ({ ok: true }),
    readyOptions: { attempts: 1, intervalMs: 0 },
  });

  assert.deepEqual(order[0], ['publish', path.dirname(fixture.releaseLink)]);
  assert.equal(order[1][0], 'pm2');
  assert.equal(result.commit, '2222222222222222222222222222222222222222');
  assert.equal(result.published, true);
});

test('the updater replaces itself after an upgrade that moved it to another release', async () => {
  const fixture = await fixtureRelease();
  const daemonEntry = path.join(fixture.release, 'packages', 'daemon', 'src', 'main.mjs');
  await mkdir(path.dirname(daemonEntry), { recursive: true });
  await writeFile(daemonEntry, '');
  const restarts = [];
  const options = {
    releaseLink: fixture.releaseLink,
    publishReleaseImpl: async () => ({ release: fixture.release, commit: 'c', version: '0.5.0', published: true }),
    execFileImpl: async (_command, args) => ({
      stdout: args[0] === 'jlist' ? JSON.stringify([{ name: 'vika-beta', pm2_env: { status: 'online', pm_exec_path: daemonEntry } }]) : '',
    }),
    fetchImpl: async () => ({ ok: true }),
    readyOptions: { attempts: 1, intervalMs: 0 },
    restartSelfImpl: (request) => restarts.push(request.release),
  };
  // Running from an older release: schedule the replacement.
  const moved = await upgradeTarget('beta', { ...options, selfEntry: path.join(fixture.root, 'old', 'server.mjs') });
  assert.equal(moved.restartsUpdater, true);
  assert.deepEqual(restarts, [fixture.release]);
  // Already on the current release: nothing to replace.
  const updaterEntry = path.join(fixture.release, 'packages', 'updater', 'src', 'server.mjs');
  await mkdir(path.dirname(updaterEntry), { recursive: true });
  await writeFile(updaterEntry, '');
  const same = await upgradeTarget('beta', { ...options, selfEntry: updaterEntry });
  assert.equal(same.restartsUpdater, false);
  assert.equal(restarts.length, 1);
});

test('the replacement shell outlives the updater and starts it from the new release', () => {
  const calls = [];
  scheduleUpdaterRestart({
    release: '/vika/releases/0.5.0',
    pm2Bin: '/vika/releases/0.4.0/bin/pm2',
    pm2Env: { HOME: '/home/u', PM2_HOME: '/home/u/.vika/pm2' },
    spawnImpl: (command, args, options) => { calls.push({ command, args, options }); return { unref() {} }; },
  });
  const [{ command, args, options }] = calls;
  assert.equal(command, '/bin/sh');
  // Backgrounded inside the shell, so it is no longer in the updater's process tree.
  assert.match(args[1], /^\(sleep 2; "\$PM2" delete vika-updater .*\) .*&$/);
  assert.match(args[1], /start "\$CONFIG" --only vika-updater --update-env/);
  assert.equal(options.detached, true);
  assert.equal(options.env.CONFIG, '/vika/releases/0.5.0/ecosystem.config.cjs');
  assert.equal(options.env.PM2, '/vika/releases/0.4.0/bin/pm2', 'falls back to the running pm2 when the release has none');
  assert.equal(options.env.PM2_HOME, '/home/u/.vika/pm2');
});

test('server accepts one allowlisted upgrade over its private socket', async (t) => {
  const root = await fixtureTempRoot('vika-updater-socket-test-');
  const socketPath = path.join(root, 'updater.sock');
  const calls = [];
  const updater = await createUpdaterServer({
    socketPath,
    upgrade: async (target) => {
      calls.push(target);
      return { target, app: `vika-${target}`, release: '/release', status: 'ready' };
    },
  });
  t.after(() => updater.close());

  const response = await post(socketPath, { target: 'beta' });
  assert.equal(response.status, 200);
  assert.deepEqual(calls, ['beta']);
  assert.equal(response.body.status, 'ready');
});

test('request body timeout does not abort a long-running upgrade response', async (t) => {
  const root = await fixtureTempRoot('vika-updater-socket-test-');
  const socketPath = path.join(root, 'updater.sock');
  const updater = await createUpdaterServer({
    socketPath,
    requestBodyTimeoutMs: 10,
    upgrade: async (target) => {
      await new Promise((resolve) => setTimeout(resolve, 30));
      return { target, app: 'vika-beta', release: '/release/current', status: 'ready' };
    },
  });
  t.after(() => updater.close());

  const response = await post(socketPath, { target: 'beta' });
  assert.equal(response.status, 200);
  assert.equal(response.body.status, 'ready');
});

test('client disconnect does not cancel an upgrade already owned by the updater', async (t) => {
  // Keep the Unix socket path below macOS's sockaddr_un limit. os.tmpdir()
  // already expands to a long /private/var/folders path on developer Macs.
  const root = await fixtureTempRoot('lud-');
  const socketPath = path.join(root, 'updater.sock');
  let markStarted;
  let finishUpgrade;
  let markCompleted;
  const started = new Promise((resolve) => { markStarted = resolve; });
  const finish = new Promise((resolve) => { finishUpgrade = resolve; });
  const completed = new Promise((resolve) => { markCompleted = resolve; });
  const updater = await createUpdaterServer({
    socketPath,
    upgrade: async (target) => {
      markStarted();
      await finish;
      markCompleted(target);
      return { target, app: 'vika-alpha', release: '/release/current', status: 'ready' };
    },
  });
  t.after(() => updater.close());

  const payload = JSON.stringify({ target: 'alpha' });
  const request = http.request({
    socketPath,
    path: '/v1/upgrade',
    method: 'POST',
    headers: { 'content-length': Buffer.byteLength(payload) },
  });
  request.once('error', () => {});
  request.end(payload);
  await started;
  request.destroy();
  finishUpgrade();

  assert.equal(await completed, 'alpha');
});

test('server rejects a non-allowlisted target without calling upgrade', async (t) => {
  const root = await fixtureTempRoot('vika-updater-reject-test-');
  const socketPath = path.join(root, 'updater.sock');
  let called = false;
  const updater = await createUpdaterServer({
    socketPath,
    upgrade: async () => { called = true; },
  });
  t.after(() => updater.close());

  const response = await post(socketPath, { target: '../../oops' });
  assert.equal(response.status, 400);
  assert.match(response.body.error, /target must be one of/);
  assert.equal(called, false);
});

test('server serializes upgrade requests', async (t) => {
  const root = await fixtureTempRoot('vika-updater-busy-test-');
  const socketPath = path.join(root, 'updater.sock');
  let releaseFirst;
  const firstGate = new Promise((resolve) => { releaseFirst = resolve; });
  const updater = await createUpdaterServer({
    socketPath,
    upgrade: async (target) => {
      await firstGate;
      return { target, app: 'vika', release: '/release', status: 'ready' };
    },
  });
  t.after(() => updater.close());

  const first = post(socketPath, { target: 'alpha' });
  await new Promise((resolve) => setImmediate(resolve));
  const second = await post(socketPath, { target: 'beta' });
  assert.equal(second.status, 409);
  releaseFirst();
  assert.equal((await first).status, 200);
});

test('a second updater cannot unlink the live updater socket', async (t) => {
  const root = await fixtureTempRoot('vika-updater-owner-test-');
  const socketPath = path.join(root, 'updater.sock');
  const first = await createUpdaterServer({
    socketPath,
    upgrade: async (target) => ({ target, status: 'ready' }),
  });
  t.after(() => first.close());

  await assert.rejects(
    createUpdaterServer({ socketPath, upgrade: async () => ({}) }),
    /already listening/,
  );
  const response = await post(socketPath, { target: 'alpha' });
  assert.equal(response.status, 200);
});

test('CLI upgrade command reaches the updater over the Unix socket', async (t) => {
  const root = await fixtureTempRoot('vika-updater-cli-test-');
  const socketPath = path.join(root, 'updater.sock');
  const updater = await createUpdaterServer({
    socketPath,
    upgrade: async (target) => ({
      target,
      app: 'vika-beta',
      release: '/release/current',
      status: 'ready',
    }),
  });
  t.after(() => updater.close());

  const bin = fileURLToPath(new URL('../../cli/bin/vika.mjs', import.meta.url));
  const child = spawn(process.execPath, [bin, 'upgrade', 'beta'], {
    env: {
      ...process.env,
      VIKA_UPDATER_SOCKET: socketPath,
      VIKA_UPDATER_TARGETS_JSON: JSON.stringify({
        beta: {
          app: 'vika-beta',
          readyUrl: 'http://127.0.0.1:3740/ready',
          upgradeSharedWeb: false,
        },
      }),
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const stdout = [];
  const stderr = [];
  child.stdout.on('data', (chunk) => stdout.push(chunk));
  child.stderr.on('data', (chunk) => stderr.push(chunk));
  const exitCode = await new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('exit', resolve);
  });
  assert.equal(exitCode, 0, Buffer.concat(stderr).toString('utf8'));
  assert.equal(
    Buffer.concat(stdout).toString('utf8'),
    'vika-beta is ready (/release/current)\n',
  );
});
