// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  DSH_BRIDGE_PATH, assertDshNodeVersion, assertSupportedDshVersion, dshCommand, sanitizeDshEnv, writeDshPatch,
} from '../src/dsh-runtime.mjs';
import { DshBridgeFramer } from '../src/dsh-bridge/protocol.mjs';
// The detach policy belongs to the platform adapter, exactly as in the product
// code: `terminateProcessTree` signals the group on POSIX and the child on
// Windows, so the fixture starts the child the way that signal expects.
import { spawnDetached } from '../src/platform/index.mjs';
import { terminateProcessTree } from '../src/process-utils.mjs';

// Helpers for tests that drive a real `dsh` process over the bridge.
//
// Three separate concerns, each reusable on its own:
//   - `realDshAvailability()`: is this machine's `dsh` the version vika drives?
//     Tests pass its `reason` to node:test's `skip`, so a machine without dsh
//     reports skipped tests instead of failures.
//
// The skip is silent in the one place it matters most: `node --test` reports the
// same `skipped` count whether these suites were skipped or never collected, so a
// CI runner without `dsh` loses 20 cases and still exits 0. `VIKA_REQUIRE_DSH=1`
// turns "no dsh" into a failure instead, which is what a job that is supposed to
// cover the live contract sets.
//   - `prepareLiveDshRoot()` / `hermeticDshEnv()`: a private data directory and
//     an environment with no operator DEEPSEEK_* or DSH_* values, so a test
//     never reaches the network, ~/.dsh, or ~/.vika.
//   - `startLiveDsh()`: the process, LF framing, request/response correlation,
//     JSON-RPC error mapping (the same shape dsh.mjs produces), and a cleanup
//     that kills the process group.

const DEFAULT_STARTUP_MS = 120_000;
const DEFAULT_RPC_TIMEOUT_MS = 60_000;
const STDERR_TAIL = 12_000;

function tail(text, limit = STDERR_TAIL) {
  return text.length <= limit ? text : text.slice(text.length - limit);
}

function delay(ms) {
  return new Promise((resolve) => { const timer = setTimeout(resolve, ms); timer.unref?.(); });
}

function versionProbe(bin, timeoutMs) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(bin, ['--version'], { stdio: ['ignore', 'pipe', 'pipe'] });
    } catch (error) {
      resolve({ error });
      return;
    }
    let stdout = '';
    let stderr = '';
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      terminateProcessTree(child, 'SIGKILL');
    }, timeoutMs);
    timer.unref?.();
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.once('error', (error) => { clearTimeout(timer); resolve({ error }); });
    child.once('exit', (code, signal) => {
      clearTimeout(timer);
      resolve({ code, signal, stdout, stderr, timedOut });
    });
  });
}

const availabilityCache = new Map();

/**
 * Whether this machine has a `dsh` vika can drive.
 * @param options.bin - executable name or absolute path; defaults to DSH_BIN.
 * @param options.timeoutMs - bound on the version probe.
 * @returns `{ ok: true, bin, version }`, or `{ ok: false, bin, reason }` where
 *   `reason` is a human sentence for node:test's `skip`.
 */
export async function realDshAvailability({ bin = process.env.DSH_BIN || 'dsh', timeoutMs = 30_000 } = {}) {
  const key = `${bin}\0${timeoutMs}`;
  if (availabilityCache.has(key)) return availabilityCache.get(key);
  const probe = (async () => {
    const verdict = await probeDsh(bin, timeoutMs);
    if (process.env.VIKA_REQUIRE_DSH === '1' && !verdict.ok) {
      // A job that means to cover the live contract must fail when it cannot,
      // instead of reporting the same green as a job that skipped the work.
      throw new Error(`VIKA_REQUIRE_DSH=1 but ${verdict.reason}`);
    }
    return verdict;
  })();
  availabilityCache.set(key, probe);
  return probe;
}

async function probeDsh(bin, timeoutMs) {
    try {
      assertDshNodeVersion();
    } catch (error) {
      return { ok: false, bin, reason: `${error.message}, so live DSH tests are skipped` };
    }
    const result = await versionProbe(bin, timeoutMs);
    if (result.error) {
      const detail = result.error.code === 'ENOENT' ? 'not found on PATH' : result.error.message;
      return { ok: false, bin, reason: `dsh is unavailable (${bin}: ${detail}), so live DSH tests are skipped` };
    }
    if (result.timedOut) return { ok: false, bin, reason: `${bin} --version timed out after ${timeoutMs}ms, so live DSH tests are skipped` };
    if (result.code !== 0) {
      return { ok: false, bin, reason: `${bin} --version exited ${result.code}, so live DSH tests are skipped: ${tail(result.stderr, 500).trim()}` };
    }
    const version = result.stdout.trim().split('\n')[0].trim();
    try {
      assertSupportedDshVersion(version);
    } catch (error) {
      return { ok: false, bin, reason: `${error.message}, so live DSH tests are skipped` };
    }
    return { ok: true, bin, version };
}

/**
 * A private data directory in the layout `sanitizeDshEnv` expects.
 * @param options.prefix - temp-directory prefix.
 * @returns `{ root, dataDir, workspace, home }`; `root` is removed by
 *   `cleanupLiveDshRoot`.
 */
export function prepareLiveDshRoot({ prefix = 'vika-dsh-live-' } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  fs.chmodSync(root, 0o700);
  const workspace = path.join(root, 'workspace');
  const home = path.join(root, 'home');
  fs.mkdirSync(workspace, { mode: 0o700 });
  fs.mkdirSync(home, { mode: 0o700 });
  return { root, dataDir: root, workspace, home };
}

/**
 * The environment a live fixture starts dsh with: no operator DEEPSEEK_* (a
 * real key would change what the catalogue lists) or DSH_* (the fixture owns
 * the home), and a temporary HOME so nothing lands in the user's dotfiles. The
 * API key and endpoint are opt-in so a test states which scenario it exercises.
 * @param options.root - a `prepareLiveDshRoot()` result.
 * @param options.apiKey - value for DEEPSEEK_API_KEY, or null to stay keyless.
 * @param options.baseURL - value for DEEPSEEK_BASE_URL.
 * @param options.env - source environment; defaults to process.env.
 */
export function hermeticDshEnv({ root, apiKey = null, baseURL = null, env = process.env } = {}) {
  const safe = { ...env };
  for (const key of Object.keys(safe)) {
    if (/^DEEPSEEK_/i.test(key) || /^DSH_/i.test(key)) delete safe[key];
  }
  safe.HOME = root.home;
  if (apiKey !== null) safe.DEEPSEEK_API_KEY = apiKey;
  if (baseURL !== null) safe.DEEPSEEK_BASE_URL = baseURL;
  return safe;
}

/** `<dataDir>/dsh/home` — the DSH_HOME a live fixture runs with. */
export function dshHomePath(root) {
  return path.join(root, 'dsh', 'home');
}

/** The only model-configuration document: `<DSH_HOME>/profiles/sdk/cordis.patch.yml`. */
export function profilePatchPath(root) {
  return path.join(dshHomePath(root), 'profiles', 'sdk', 'cordis.patch.yml');
}

/** `<DSH_HOME>/.credentials.yaml`. */
export function credentialsPath(root) {
  return path.join(dshHomePath(root), '.credentials.yaml');
}

/** Remove a fixture root; retries because a killed process may still release files. */
export function cleanupLiveDshRoot(root) {
  if (!root) return;
  fs.rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 50 });
}

/**
 * Start a real dsh process wired with the vika bridge.
 * @param options.root - a `prepareLiveDshRoot()` result (its `workspace` is cwd).
 * @param options.env - the environment; pass `hermeticDshEnv(...)`.
 * @param options.policy - the patch's persona prefix, for identifiable stderr.
 * @param options.bin - dsh executable; defaults to DSH_BIN or `dsh`.
 * @param options.provider - llm-pi-ai providers to append to the *run* patch.
 *   The product never does this (the settings service owns that document): the
 *   entry is appended here to reproduce the overlay conflict the design forbids.
 * @returns a client with `rpc`/`rpcRaw`, `events`, `stderr()`, `stop()`.
 */
export async function startLiveDsh({
  root, env, policy = '', bin = process.env.DSH_BIN || 'dsh', provider = null,
  startupMs = DEFAULT_STARTUP_MS,
} = {}) {
  if (!root) throw new Error('startLiveDsh requires a prepared root');
  // The run patch names no provider: the DSH settings service refuses to write a
  // namespace an overlay already defines, and the fixture owns that document
  // through `vika/settings/mutate`.
  const dataDir = root.dataDir ?? root;
  const workspace = root.workspace ?? path.join(dataDir, 'workspace');
  const patchPath = writeDshPatch({
    dataDir, policy, bridgePath: DSH_BRIDGE_PATH, mcp: null,
  });
  if (provider !== null) {
    // Reproduce the overlay the design forbids: a run patch that already names
    // llm-pi-ai, which makes the settings service refuse to write it.
    const patch = JSON.parse(fs.readFileSync(patchPath, 'utf8'));
    patch.push({ id: 'llm-pi-ai', config: { providers: provider } });
    fs.writeFileSync(patchPath, `${JSON.stringify(patch, null, 2)}\n`, { mode: 0o600 });
  }
  const command = dshCommand({ bin, patchPath });
  const child = spawn(command.command, command.args, {
    cwd: workspace,
    env: sanitizeDshEnv(env ?? process.env, { dataDir }),
    shell: false,
    detached: spawnDetached,
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let stderr = '';
  let stdoutRest = '';
  let exited = false;
  let exitInfo = null;
  let spawnError = null;
  const pending = new Map();
  const events = [];
  const startedAt = Date.now();
  const waiters = new Set();

  child.stderr.setEncoding('utf8');
  child.stderr.on('data', (chunk) => { stderr = tail(`${stderr}${chunk}`); });
  child.stdin.on('error', () => {});

  const framer = new DshBridgeFramer({
    onFrame({ text }) {
      let message;
      try { message = JSON.parse(text); } catch {
        stdoutRest = tail(`${stdoutRest}${text}\n`);
        return;
      }
      if (message && typeof message === 'object' && message.id !== undefined && message.method === undefined) {
        const entry = pending.get(message.id);
        if (!entry) {
          events.push({ kind: 'unmatched', message });
          return;
        }
        pending.delete(message.id);
        clearTimeout(entry.timer);
        if (message.error) entry.resolve({ error: message.error });
        else entry.resolve({ result: message.result });
        return;
      }
      if (message && typeof message.method === 'string') events.push({ kind: 'notification', message });
    },
    onOversized(prefix) {
      stderr = tail(`${stderr}\n[fixture] dropped an oversized reply frame: ${prefix}`);
    },
  });
  child.stdout.on('data', (chunk) => framer.push(chunk));
  child.stdout.once('end', () => framer.end());

  function settleWaiters() {
    for (const resolve of waiters) resolve(exitInfo);
    waiters.clear();
  }

  child.once('error', (error) => {
    spawnError = error;
    exited = true;
    exitInfo = { code: null, signal: null, error };
    for (const [id, entry] of pending) {
      clearTimeout(entry.timer);
      pending.delete(id);
      entry.reject(error);
    }
    settleWaiters();
  });
  child.once('exit', (code, signal) => {
    exited = true;
    exitInfo = { code, signal };
    for (const [id, entry] of pending) {
      clearTimeout(entry.timer);
      pending.delete(id);
      entry.reject(Object.assign(new Error(`dsh exited while ${entry.method} was pending (code ${code}, signal ${signal})`), { code: 'DSH_EXIT' }));
    }
    settleWaiters();
  });

  let nextId = 0;

  /**
   * One request, answering with the raw frame instead of throwing.
   * @returns `{ result }` or `{ error }` where `error` is the JSON-RPC error.
   */
  function rpcRaw(method, params = {}, { timeoutMs = DEFAULT_RPC_TIMEOUT_MS } = {}) {
    if (exited) {
      return Promise.reject(Object.assign(
        new Error(`dsh is not running (${spawnError?.message ?? `exit ${exitInfo?.code}/${exitInfo?.signal}`})`),
        { code: 'DSH_NOT_RUNNING' },
      ));
    }
    const id = `live-${++nextId}`;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(Object.assign(
          new Error(`dsh did not answer ${method} within ${timeoutMs}ms${stderr ? `\n${tail(stderr, 2_000)}` : ''}`),
          { code: 'DSH_RPC_TIMEOUT' },
        ));
      }, timeoutMs);
      timer.unref?.();
      pending.set(id, { resolve, reject, timer, method });
      try {
        child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`);
      } catch (error) {
        clearTimeout(timer);
        pending.delete(id);
        reject(error);
      }
    });
  }

  /** One request that rejects with the same error shape `dsh.mjs` produces. */
  async function rpc(method, params = {}, options = {}) {
    const reply = await rpcRaw(method, params, options);
    if (reply.error) {
      const error = new Error(reply.error.message || 'dsh rpc error');
      // dsh.mjs maps the numeric JSON-RPC code onto `code` and keeps the
      // structured `data`; retryDshNotReady classifies by exactly this.
      error.code = reply.error.code;
      error.data = reply.error.data;
      error.rpc = reply.error;
      throw error;
    }
    return reply.result;
  }

  /** `initialize` with the fixture's workspace, unless a test overrides fields. */
  function initialize(overrides = {}) {
    return rpc('initialize', {
      cwd: workspace, provider: 'deepseek-official', model: 'deepseek-flash',
      reasoningEffort: 'high', approvalRequests: true, ...overrides,
    });
  }

  function waitForExit(timeoutMs = 10_000) {
    if (exited) return Promise.resolve(exitInfo);
    return Promise.race([
      new Promise((resolve) => { waiters.add(resolve); }),
      delay(timeoutMs).then(() => null),
    ]);
  }

  let stopping = null;
  function stop() {
    if (stopping) return stopping;
    stopping = (async () => {
      try { framer.end(); } catch {}
      if (!exited) {
        // The SDK server exits on `shutdown`; the kill is only for a process
        // that stopped reading stdin.
        await rpcRaw('shutdown', {}, { timeoutMs: 5_000 }).catch(() => {});
        if (!await waitForExit(5_000)) {
          terminateProcessTree(child, 'SIGTERM');
          if (!await waitForExit(3_000)) {
            terminateProcessTree(child, 'SIGKILL');
            await waitForExit(2_000);
          }
        }
      } else {
        // Already exited: the group may still own stragglers.
        terminateProcessTree(child, 'SIGKILL');
      }
      try { child.stdin.end(); } catch {}
      try { child.stdout.destroy(); } catch {}
      try { child.stderr.destroy(); } catch {}
    })();
    return stopping;
  }

  return {
    child,
    bin: command.command,
    patchPath,
    root,
    dataDir,
    workspace,
    startedAt,
    pid: child.pid,
    rpc,
    rpcRaw,
    initialize,
    events,
    waitForExit,
    stop,
    stderr: () => stderr,
    stdoutRest: () => stdoutRest,
    exitInfo: () => exitInfo,
    elapsed: () => Date.now() - startedAt,
    /** Whether `vika/hello` advertises this bridge. */
    async hello() { return rpc('vika/hello'); },
  };
}
