// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { terminateProcessTree } from '../src/process-utils.mjs';

// A throwaway "installation" for CLI end-to-end tests: a deployment manifest
// with one or more instances, a temp HOME (so ~/.vika is never touched), and a
// fake `dsh` on PATH *and* in DSH_BIN. The CLI under test is the real entry
// point (`node packages/cli/bin/vika.mjs`), so an argument-parsing or
// dispatch regression fails here instead of in a unit test.
//
// `runCli()` executes one CLI invocation and returns its exit status and
// output; `requests()` reads the fake dsh's JSONL log, which is the only way
// to observe what the CLI actually sent over the bridge.

const SUPPORT_DIR = path.dirname(fileURLToPath(import.meta.url));
/**
 * The repository root (`vika/`) whose CLI a fixture drives. It defaults to the
 * checkout this file lives in; `VIKA_E2E_PROJECT_ROOT` points the same suite at
 * another checkout, which is how an integration branch is checked before the
 * test file itself is merged there.
 */
export const PROJECT_ROOT = path.resolve(process.env.VIKA_E2E_PROJECT_ROOT || path.resolve(SUPPORT_DIR, '..', '..', '..'));
/** The real CLI entry point. */
export const CLI_ENTRY = path.join(PROJECT_ROOT, 'packages', 'cli', 'bin', 'vika.mjs');
/** The command module the `dsh model` tests require; absent before the CLI lands. */
export const CLI_DSH_MODEL_MODULE = path.join(PROJECT_ROOT, 'packages', 'cli', 'src', 'dsh-model.mjs');
/** The reusable fake dsh executable. */
export const FAKE_DSH = path.join(SUPPORT_DIR, 'fake-dsh.mjs');

const require = createRequire(import.meta.url);
// The manifest validator always comes from the checkout under test, so a
// fixture the target product would reject fails here first.
const { loadDeploymentManifest } = require(path.join(PROJECT_ROOT, 'deployment-config.cjs'));

/** Why the CLI end-to-end suite cannot run, or null when it can. */
export function cliSkipReason() {
  if (!fs.existsSync(CLI_ENTRY)) return `the CLI entry point ${path.relative(PROJECT_ROOT, CLI_ENTRY)} is missing, so CLI end-to-end tests are skipped`;
  if (!fs.existsSync(CLI_DSH_MODEL_MODULE)) {
    return `the CLI has no dsh model command yet (${path.relative(PROJECT_ROOT, CLI_DSH_MODEL_MODULE)} is missing), so CLI end-to-end tests are skipped`;
  }
  return null;
}

function readJsonl(filePath) {
  if (!fs.existsSync(filePath)) return [];
  return fs.readFileSync(filePath, 'utf8').split('\n').filter(Boolean).flatMap((line) => {
    try { return [JSON.parse(line)]; } catch { return []; }
  });
}

function parseCredentialsDocument(text) {
  const refs = new Map();
  for (const line of String(text ?? '').split('\n')) {
    const match = /^ {2}([A-Za-z_][A-Za-z0-9_]*): (.*)$/.exec(line.replace(/\r$/, ''));
    if (!match) continue;
    let value = match[2];
    if (value.startsWith('"')) {
      try { value = JSON.parse(value); } catch { continue; }
    }
    refs.set(match[1], value);
  }
  return refs;
}

/**
 * Create an isolated CLI installation.
 * @param tests - optional node:test context; its `after` removes the root.
 * @param options.instances - instance ids to declare; the first is the default.
 */
export function createCliFixture(tests, { instances = ['qa'] } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-cli-e2e-'));
  fs.chmodSync(root, 0o700);
  const home = path.join(root, 'home');
  fs.mkdirSync(home, { mode: 0o700 });
  const binDirectory = path.join(root, 'bin');
  fs.mkdirSync(binDirectory, { mode: 0o700 });
  // The fake keeps the executable bit and its shebang, so the CLI's own spawn
  // path (DSH_BIN or `dsh` on PATH) exercises a real executable, not a shell
  // wrapper this fixture invented.
  const fakeDsh = path.join(binDirectory, 'dsh');
  fs.copyFileSync(FAKE_DSH, fakeDsh);
  fs.chmodSync(fakeDsh, 0o755);

  const directoryOf = (id) => ({
    dataDir: path.join(root, 'data', id),
    workspace: path.join(root, 'ws', id),
    ovConfig: path.join(root, 'ov', `${id}.json`),
  });
  const agents = instances.map((id, index) => {
    const { dataDir, workspace, ovConfig } = directoryOf(id);
    for (const directory of [dataDir, workspace, path.dirname(ovConfig)]) {
      fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
    }
    fs.writeFileSync(ovConfig, '{}\n', { mode: 0o600 });
    return {
      id,
      displayName: id.toUpperCase(),
      channel: 'telegram',
      port: 3738 + index,
      dataDir,
      workspace,
      ovConfig,
      model: 'dsh',
    };
  });
  const manifestPath = path.join(root, 'config.json');
  fs.writeFileSync(manifestPath, `${JSON.stringify({
    version: 1, defaultInstance: instances[0], env: {}, agents,
  }, null, 2)}\n`, { mode: 0o600 });
  // Validated with the daemon's own loader: a fixture the product would reject
  // must fail the suite here, not inside the CLI being measured.
  loadDeploymentManifest({ VIKA_CONFIG: manifestPath, HOME: home });

  const logPath = path.join(root, 'fake-dsh.jsonl');
  const env = { ...process.env, HOME: home, VIKA_CONFIG: manifestPath, DSH_BIN: fakeDsh };
  for (const key of Object.keys(env)) {
    if (/^(?:VIKA|DEEPSEEK|DSH|FAKE_DSH)_/i.test(key)) delete env[key];
  }
  Object.assign(env, {
    HOME: home,
    VIKA_CONFIG: manifestPath,
    DSH_BIN: fakeDsh,
    PATH: `${binDirectory}${path.delimiter}${process.env.PATH ?? ''}`,
    FAKE_DSH_LOG: logPath,
    NO_COLOR: '1',
    FORCE_COLOR: '0',
  });

  const instance = (id) => {
    const agent = agents.find((candidate) => candidate.id === id);
    if (!agent) throw new Error(`no fixture instance ${id}`);
    return agent;
  };
  const dataDirOf = (id) => instance(id).dataDir;

  let cleaned = false;
  // A suite that is skipped or interrupted never reaches its `after` hook, so
  // the root is also removed when this process exits. The hook is cheap and
  // idempotent, and a leaked 0700 fixture directory is the one thing a failing
  // run must not leave behind.
  const onExit = () => fixture.cleanup();
  process.once('exit', onExit);
  const fixture = {
    root,
    home,
    binDirectory,
    fakeDsh,
    manifestPath,
    logPath,
    env,
    instances: agents.map((agent) => agent.id),
    dataDirOf,
    dshHome: (id) => path.join(dataDirOf(id), 'dsh', 'home'),
    profilePatch: (id) => path.join(dataDirOf(id), 'dsh', 'home', 'profiles', 'sdk', 'cordis.patch.yml'),
    credentialsFile: (id) => path.join(dataDirOf(id), 'dsh', 'home', '.credentials.yaml'),
    controlPatch: (id) => path.join(dataDirOf(id), 'dsh', 'run', 'control-patch.yml'),

    /** Run one `vika ...` invocation with the fixture environment. */
    runCli(args, { input = null, timeoutMs = 60_000, env: extraEnv = {} } = {}) {
      return new Promise((resolve) => {
        const child = spawn(process.execPath, [CLI_ENTRY, ...args], {
          cwd: root,
          env: { ...env, ...extraEnv },
          shell: false,
          detached: process.platform !== 'win32',
          stdio: ['pipe', 'pipe', 'pipe'],
        });
        let stdout = '';
        let stderr = '';
        let timedOut = false;
        const tail = (text, chunk) => {
          const next = `${text}${chunk}`;
          return next.length <= 20_000 ? next : next.slice(next.length - 20_000);
        };
        child.stdout.setEncoding('utf8');
        child.stderr.setEncoding('utf8');
        child.stdout.on('data', (chunk) => { stdout = tail(stdout, chunk); });
        child.stderr.on('data', (chunk) => { stderr = tail(stderr, chunk); });
        child.stdin.on('error', () => {});
        const timer = setTimeout(() => {
          timedOut = true;
          terminateProcessTree(child, 'SIGKILL');
        }, timeoutMs);
        timer.unref?.();
        child.once('error', (error) => {
          clearTimeout(timer);
          resolve({ args, code: null, signal: null, stdout, stderr: `${stderr}${error.message}`, timedOut });
        });
        child.once('exit', (code, signal) => {
          clearTimeout(timer);
          resolve({ args, code, signal, stdout, stderr, timedOut });
        });
        if (input === null) child.stdin.end();
        else child.stdin.end(input);
      });
    },

    /** Every event the fake dsh recorded, oldest first. */
    logEntries() { return readJsonl(logPath); },
    /** Requests the CLI sent, optionally filtered by bridge method. */
    requests(method = null) {
      return readJsonl(logPath)
        .filter((entry) => entry.event === 'request')
        .filter((entry) => method === null || entry.method === method);
    },
    /** The `vika/settings/mutate` ops the CLI sent, oldest first. */
    mutateOps() {
      return fixture.requests('vika/settings/mutate').flatMap((entry) => entry.params?.ops ?? []);
    },
    /** Parsed `llm-pi-ai` providers from the persisted profile patch, or null. */
    patchProviders(id = instances[0]) {
      const filePath = fixture.profilePatch(id);
      if (!fs.existsSync(filePath)) return null;
      let parsed;
      try { parsed = JSON.parse(fs.readFileSync(filePath, 'utf8')); } catch { return null; }
      const entry = Array.isArray(parsed) ? parsed.find((row) => row?.id === 'llm-pi-ai') : null;
      return entry?.config?.providers ?? null;
    },
    /** Raw credentials document text and its `refs` map. */
    credentials(id = instances[0]) {
      const filePath = fixture.credentialsFile(id);
      if (!fs.existsSync(filePath)) return { text: null, refs: new Map(), mode: null };
      const text = fs.readFileSync(filePath, 'utf8');
      return { text, refs: parseCredentialsDocument(text), mode: fs.statSync(filePath).mode & 0o777 };
    },
    /** Remove the fixture root once the suite finishes; safe to call twice. */
    cleanup() {
      if (cleaned) return;
      cleaned = true;
      process.removeListener('exit', onExit);
      fs.rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 50 });
    },
  };
  tests?.after?.(() => fixture.cleanup());
  return fixture;
}
