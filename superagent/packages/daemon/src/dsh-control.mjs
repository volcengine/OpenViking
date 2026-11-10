// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// The one client that changes DSH model settings without a running daemon.
//
// Configuration lives in DSH's own `llm-pi-ai` settings document, so only the
// DSH settings service may write it. This module starts a short-lived DSH
// process against the instance's own `DSH_HOME`, talks to it over the v2
// bridge, and shuts it down again. The temporary patch carries no MCP client
// and no model providers: a provider section in the patch would make
// `settings.mutate` answer "overridden by a home patch or command-line
// overlay", and the CLI would then be editing a document nothing reads.
//
// `initialize` is deliberately not sent. The settings and credentials services
// mount at start-up, and initialize would drag in a provider round-trip that a
// configuration command must not depend on.

import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { DshBridgeFramer } from './dsh-bridge/framer.mjs';
import { spawnDetached } from './platform/index.mjs';
import { terminateProcessTree } from './process-utils.mjs';
import {
  DSH_BRIDGE_CLI_METHODS, DSH_BRIDGE_PATH,
  bridgeHelloSupports, buildDshPatch, dshCommand, prepareDshEnvironment,
  retryDshNotReady, sanitizeDshEnv,
} from './dsh-runtime.mjs';

// The control patch is kept beside the daemon's own run patch, never on top of
// it: `ovsa dsh model` must not disturb the process a live instance drives.
export const CONTROL_PATCH_FILE = 'control-patch.yml';

// A source-tree DSH starts through tsx and takes seconds; the packaged one is
// faster. The hello bound is generous on purpose, the per-request bounds are
// not: a stuck process is killed rather than waited on.
export const CONTROL_HELLO_TIMEOUT_MS = 60_000;
export const CONTROL_REQUEST_TIMEOUT_MS = 30_000;
// `vika/llm/models` interrogates every route, each with its own 10s bound in
// the bridge, so the answer may legitimately take a multiple of that.
export const CONTROL_MODELS_TIMEOUT_MS = 90_000;
export const CONTROL_SHUTDOWN_TIMEOUT_MS = 5_000;

// stdout must stay protocol-only; the tail is enough to explain a crash.
const STDERR_TAIL = 8_000;
const EXIT_GRACE_MS = 2_000;

export class DshControlError extends Error {
  /**
   * @param {string} message - what went wrong, safe to show a user.
   * @param {object} [detail] - numeric `code` and structured `data` from a
   *   bridge error frame, plus the stderr tail.
   */
  constructor(message, { code = null, data = undefined, stderr = '' } = {}) {
    super(message);
    this.name = 'DshControlError';
    if (code !== null) this.code = code;
    // Bridge v2.1 carries a stable string taxonomy here (SETTINGS_CONFLICT and
    // friends); the numeric JSON-RPC code alone cannot express it.
    if (data !== undefined) this.data = data;
    this.stderr = stderr;
  }
}

// The path this client's own patch is written to, for reporting and tests.
export function controlPatchPath(dataDir) {
  return path.join(dataDir, 'dsh', 'run', CONTROL_PATCH_FILE);
}

/**
 * Write the private patch the control process starts with. JSON is a YAML
 * subset, so no YAML dependency is needed anywhere in this path.
 * @param {object} options
 * @param {string} options.dataDir - instance data directory.
 * @param {string} [options.policy] - system-prompt prefix, empty by default.
 * @param {string} [options.bridgePath] - the bridge module to load.
 * @returns {string} the patch path.
 */
export function writeControlPatch({ dataDir, policy = '', bridgePath = DSH_BRIDGE_PATH }) {
  if (typeof dataDir !== 'string' || !dataDir) throw new Error('dataDir is required to write the DSH control patch');
  const { run } = prepareDshEnvironment(dataDir);
  const patch = buildDshPatch({ policy, bridgePath, mcp: null });
  const patchPath = path.join(run, CONTROL_PATCH_FILE);
  const temporary = `${patchPath}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(patch, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 });
  fs.chmodSync(temporary, 0o600);
  fs.renameSync(temporary, patchPath);
  return patchPath;
}

/**
 * A live DSH process used only for configuration reads and writes.
 *
 * Every bridge call is retried through `retryDshNotReady`; any other failure is
 * reported once. A request that exceeds its bound kills the process tree, so a
 * wedged DSH can never outlive the command.
 */
export class DshControlClient {
  /**
   * @param {import('node:child_process').ChildProcess} child - the spawned DSH.
   * @param {object} [timeouts] - per-request bounds in milliseconds.
   */
  constructor(child, {
    helloTimeoutMs = CONTROL_HELLO_TIMEOUT_MS,
    requestTimeoutMs = CONTROL_REQUEST_TIMEOUT_MS,
    modelsTimeoutMs = CONTROL_MODELS_TIMEOUT_MS,
    shutdownTimeoutMs = CONTROL_SHUTDOWN_TIMEOUT_MS,
  } = {}) {
    this.child = child;
    this.timeouts = { helloTimeoutMs, requestTimeoutMs, modelsTimeoutMs, shutdownTimeoutMs };
    this.pending = new Map();
    this.nextId = 0;
    this.stderr = '';
    this.exited = null;
    this.closed = false;
    this.closePromise = null;
    this.failure = null;

    this.framer = new DshBridgeFramer({
      onFrame: ({ text }) => this._handleFrame(text),
      onOversized: (prefix) => {
        this.stderr = `${this.stderr}\n[control] dropped an oversized reply frame: ${prefix}`.slice(-STDERR_TAIL);
      },
    });
    child.stdout.on('data', (chunk) => this.framer.push(chunk));
    child.stdout.once('end', () => this.framer.end());
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (chunk) => {
      this.stderr = `${this.stderr}${chunk}`.slice(-STDERR_TAIL);
    });
    child.stdin.on('error', (error) => {
      this._fail(new DshControlError(`the DSH control process stopped reading requests: ${error.message}`, {
        stderr: this.stderr,
      }));
    });
    // A spawn failure emits `error` then `close`, never `exit`; a process that
    // ran emits `exit`. Both end this client, so `close()` never waits on a
    // process that cannot answer.
    const endOfProcess = (detail) => {
      if (this.exited) return;
      this.exited = { code: null, signal: null };
      this.framer.end();
      this._fail(new DshControlError(`the DSH control process ${detail} before answering`, { stderr: this.stderr }));
    };
    child.once('exit', (code, signal) => endOfProcess(`exited (${signal ? `signal ${signal}` : `code ${code}`})`));
    child.once('close', () => endOfProcess('closed'));
    child.once('error', (error) => {
      if (this.exited) return;
      this.exited = { code: null, signal: null };
      this.framer.end();
      this._fail(new DshControlError(`cannot run the DSH command: ${error.message}`, { stderr: this.stderr }));
    });
  }

  /**
   * Start a control process on one instance's DSH home.
   * @param {object} options
   * @param {string} options.dataDir - absolute instance data directory.
   * @param {string} [options.bin] - the dsh executable.
   * @param {object} [options.env] - environment to sanitize and pass on.
   * @param {Function} [options.spawnImpl] - injectable spawn for tests.
   * @param {string} [options.policy] - persona prefix of the temporary patch.
   * @param {number} [options.helloTimeoutMs] - startup bound.
   * @returns {Promise<DshControlClient>} a client whose bridge is verified.
   */
  static async open({
    dataDir, bin = 'dsh', env = process.env, spawnImpl = spawn, policy = '',
    bridgePath = DSH_BRIDGE_PATH,
    helloTimeoutMs = CONTROL_HELLO_TIMEOUT_MS,
    requestTimeoutMs = CONTROL_REQUEST_TIMEOUT_MS,
    modelsTimeoutMs = CONTROL_MODELS_TIMEOUT_MS,
    shutdownTimeoutMs = CONTROL_SHUTDOWN_TIMEOUT_MS,
  } = {}) {
    if (typeof dataDir !== 'string' || !path.isAbsolute(dataDir)) {
      throw new DshControlError('the instance data directory must be an absolute path');
    }
    const patchPath = writeControlPatch({ dataDir, policy, bridgePath });
    const command = dshCommand({ bin, patchPath });
    let child;
    try {
      child = spawnImpl(command.command, command.args, {
        cwd: dataDir,
        // No provider credentials are injected: the document being edited
        // names its own credential references and DSH resolves them from its
        // own credentials file.
        env: sanitizeDshEnv(env, { dataDir }),
        shell: false,
        detached: spawnDetached,
        stdio: ['pipe', 'pipe', 'pipe'],
      });
    } catch (error) {
      throw new DshControlError(`cannot run the DSH command: ${error.message}`);
    }
    const client = new DshControlClient(child, {
      helloTimeoutMs, requestTimeoutMs, modelsTimeoutMs, shutdownTimeoutMs,
    });
    try {
      await client.hello();
    } catch (error) {
      await client.close();
      throw error;
    }
    return client;
  }

  /**
   * Verify the process runs the bridge surface this release edits through.
   * @returns {Promise<object>} the `vika/hello` result.
   */
  async hello() {
    const hello = await this._bridge('vika/hello', {}, { timeoutMs: this.timeouts.helloTimeoutMs });
    if (!bridgeHelloSupports(hello, DSH_BRIDGE_CLI_METHODS)) {
      throw new DshControlError(
        `this DSH does not expose the vika bridge v2 configuration methods (${DSH_BRIDGE_CLI_METHODS.join(', ')}); `
        + 'upgrade DSH or check that the bridge module loads',
        { stderr: this.stderr },
      );
    }
    return hello;
  }

  /**
   * Read the `llm-pi-ai` settings document as the bridge sees it.
   * @returns {Promise<{ns: string, descriptor: object|null}>} namespace and view.
   */
  async describeSettings() {
    return this._bridge('vika/settings/describe', { ns: 'llm-pi-ai' });
  }

  /**
   * Apply path operations to the `llm-pi-ai` document.
   * @param {Array<object>} ops - `{op:'set'|'unset', path, value}` operations.
   * @param {number} [expectedRevision] - fence against a concurrent writer.
   */
  async mutateSettings(ops, expectedRevision) {
    if (!Array.isArray(ops) || ops.length === 0) throw new DshControlError('no settings operations were generated');
    return this._bridge('vika/settings/mutate', {
      ns: 'llm-pi-ai',
      ops,
      ...(expectedRevision === undefined ? {} : { expectedRevision }),
    });
  }

  /**
   * Store one credential literal under its reference.
   * @param {string} ref - credential reference name.
   * @param {string} value - the literal; never echoed.
   */
  async setCredential(ref, value) {
    return this._bridge('vika/credentials/set', { ref, value });
  }

  /**
   * Remove one credential reference. Idempotent in the bridge.
   * @param {string} ref - credential reference name.
   */
  async unsetCredential(ref) {
    return this._bridge('vika/credentials/unset', { ref });
  }

  /**
   * Ask the process for its current provider and model catalogue.
   * @returns {Promise<{providers: Array<object>}>} the catalogue.
   */
  async listModels() {
    return this._bridge('vika/llm/models', {}, { timeoutMs: this.timeouts.modelsTimeoutMs });
  }

  /**
   * Shut the process down and make sure its process tree is gone. Idempotent.
   * @param {number} [timeoutMs] - how long to wait for a graceful exit.
   */
  async close({ timeoutMs = this.timeouts.shutdownTimeoutMs } = {}) {
    if (this.closePromise) return this.closePromise;
    this.closePromise = this._shutdown(timeoutMs);
    return this.closePromise;
  }

  async _shutdown(timeoutMs) {
    if (!this.exited) {
      try {
        // Sent before `closed` is set: the graceful exit is the fast path, and
        // the tree kill below is what covers a process that ignores it.
        await this._send('shutdown', {}, timeoutMs);
      } catch {
        // A process that cannot answer is handled by the tree kill below.
      }
      if (!await this._waitExit(timeoutMs + EXIT_GRACE_MS)) {
        terminateProcessTree(this.child, 'SIGTERM');
        if (!await this._waitExit(EXIT_GRACE_MS)) {
          terminateProcessTree(this.child, 'SIGKILL');
          await this._waitExit(EXIT_GRACE_MS);
        }
      }
    }
    // The process group may outlive its leader (children), so signal it even
    // after the leader exited: that is what terminateProcessTree guarantees.
    terminateProcessTree(this.child, 'SIGTERM');
    try { this.child.stdin.end(); } catch {}
    this.closed = true;
    const error = new DshControlError('the DSH control process was closed');
    for (const [id, entry] of this.pending) {
      clearTimeout(entry.timer);
      this.pending.delete(id);
      entry.reject(error);
    }
  }

  _bridge(method, params, options = {}) {
    return retryDshNotReady(() => this._send(method, params, options.timeoutMs ?? this.timeouts.requestTimeoutMs));
  }

  _send(method, params = {}, timeoutMs = this.timeouts.requestTimeoutMs) {
    if (this.closed) return Promise.reject(new DshControlError('the DSH control process was closed'));
    if (this.failure) return Promise.reject(this.failure);
    if (this.exited) {
      return Promise.reject(new DshControlError(
        `the DSH control process exited before ${method} was sent`, { stderr: this.stderr },
      ));
    }
    const id = `vika-control-${this.nextId += 1}`;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new DshControlError(`DSH did not answer ${method} within ${timeoutMs}ms`, { stderr: this.stderr }));
        // A process that cannot answer the CLI must not keep running: the
        // caller gets its error and the instance is left unchanged.
        terminateProcessTree(this.child, 'SIGKILL');
      }, timeoutMs);
      timer.unref?.();
      this.pending.set(id, { resolve, reject, timer, method });
      try {
        this.child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`);
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(new DshControlError(`cannot send ${method} to DSH: ${error.message}`, { stderr: this.stderr }));
      }
    });
  }

  _handleFrame(text) {
    let message;
    try {
      message = JSON.parse(text);
    } catch {
      return;
    }
    if (!message || typeof message !== 'object' || message.id === undefined) return;
    const entry = this.pending.get(message.id);
    if (!entry) return;
    this.pending.delete(message.id);
    clearTimeout(entry.timer);
    if (message.error) {
      entry.reject(new DshControlError(
        `${entry.method}: ${message.error.message ?? 'the bridge refused the request'}`,
        {
          code: Number.isSafeInteger(message.error.code) ? message.error.code : null,
          data: message.error.data,
          stderr: this.stderr,
        },
      ));
      return;
    }
    entry.resolve(message.result);
  }

  _waitExit(ms) {
    if (this.exited) return Promise.resolve(true);
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.child.removeListener('exit', onExit);
        resolve(false);
      }, ms);
      timer.unref?.();
      const onExit = () => {
        clearTimeout(timer);
        resolve(true);
      };
      this.child.once('exit', onExit);
    });
  }

  // One failure settles every pending call: a stopped process answers nothing.
  _fail(error) {
    this.failure ??= error;
    for (const [id, entry] of this.pending) {
      clearTimeout(entry.timer);
      this.pending.delete(id);
      entry.reject(error);
    }
  }
}

/**
 * Open a control process, run one callback against it, and always shut it down.
 * @param {object} options - {@link DshControlClient.open} arguments.
 * @param {(client: DshControlClient) => Promise<any>} run - the work.
 * @returns {Promise<any>} what `run` returned.
 */
export async function withDshControl(options, run) {
  const client = await DshControlClient.open(options);
  try {
    return await run(client);
  } finally {
    await client.close();
  }
}
