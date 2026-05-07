/**
 * Detached-write helper.
 *
 * Hot paths (Stop / SessionEnd / SubagentStop in Claude Code, the analogous
 * Copilot events) want to return to the host instantly while the actual
 * commit RTT to OpenViking happens in the background. The pattern is:
 *
 *   parent draining stdin → prints decision/approve to host → spawns a
 *   detached worker process → unrefs it → returns
 *
 * The worker takes the JSON payload via stdin, performs the HTTP work, and
 * exits. Because it's `detached: true` + `stdio: 'ignore'` (except stdin)
 * + `unref()`, it survives the parent exiting.
 *
 * This module exposes:
 *   - `spawnDetached(opts)`     — low-level primitive
 *   - `runWriteTask(opts)`      — high-level: pick async vs sync based on
 *                                 cfg.writePathAsync, with sync fallback if
 *                                 the spawn itself fails
 *
 * Both are crash-proof: failures only ever land in the optional debug
 * logger, never throw to the caller. A debug-mode logger is "off" by
 * default so silently dropping is the right behaviour for this layer —
 * the higher-level commit-queue will surface its own retries / errors.
 */

import { spawn } from "node:child_process";
import type { DebugLogger } from "../debug/logger.js";

export interface DetachedSpawnOptions {
  /** Executable to run (typically `process.execPath`). */
  command: string;
  /** Argv for the executable (path to worker script + flags). */
  args: string[];
  /** Extra env vars merged on top of `process.env`. */
  env?: Record<string, string>;
  /** JSON-serialisable payload sent to the worker on stdin and then closed. */
  stdinPayload?: unknown;
  /** Optional logger for telemetry; disabled-mode loggers no-op. */
  logger?: DebugLogger;
  /** Working directory. Default: parent's cwd. */
  cwd?: string;
}

export interface SpawnDetachedResult {
  /** True when the spawn succeeded and the parent is no longer attached. */
  detached: boolean;
  /** Populated when the spawn failed; the caller can fall back to sync. */
  error?: Error;
}

/**
 * Spawn a worker as a detached, fire-and-forget child. Returns immediately;
 * does NOT await the child. Errors during the spawn (bad command, EAGAIN,
 * etc.) are captured in the result rather than thrown.
 */
export function spawnDetached(opts: DetachedSpawnOptions): SpawnDetachedResult {
  const log = opts.logger?.child("async-writer");
  try {
    const child = spawn(opts.command, opts.args, {
      detached: true,
      // Pipe stdin so we can hand off the payload, but ignore the worker's
      // stdout/stderr so the parent isn't holding fds open on its behalf.
      stdio: ["pipe", "ignore", "ignore"],
      env: { ...process.env, ...(opts.env ?? {}) } as NodeJS.ProcessEnv,
      cwd: opts.cwd,
    });

    child.on("error", (err) => {
      log?.log("worker_error", { message: err.message });
    });

    if (opts.stdinPayload !== undefined && child.stdin) {
      try {
        child.stdin.end(JSON.stringify(opts.stdinPayload));
      } catch (err) {
        log?.log("payload_write_failed", { message: errMessage(err) });
      }
    }

    child.unref();
    log?.log("spawned", {
      command: opts.command,
      args: opts.args,
      pid: child.pid,
    });

    return { detached: true };
  } catch (err) {
    const error = err instanceof Error ? err : new Error(String(err));
    log?.log("spawn_failed", { message: error.message });
    return { detached: false, error };
  }
}

export interface RunWriteTaskOptions<TPayload> {
  /**
   * When true and `asyncSpawn` is provided, attempt detached spawn.
   * Otherwise (or on spawn failure) fall back to `syncHandler`.
   * Sourced from cfg.writePathAsync upstream.
   */
  async: boolean;
  /** Payload made available to both the worker (via stdin) and syncHandler. */
  payload: TPayload;
  /**
   * Build the spawn options for the detached path. Omit to force sync.
   * The factory exists so the host can inject its own worker-script path
   * — the shared package can't know it.
   */
  asyncSpawn?: (payload: TPayload) => DetachedSpawnOptions;
  /**
   * In-process implementation. Always provided so we can fall back when
   * async=false or when the spawn itself fails.
   */
  syncHandler: (payload: TPayload) => Promise<void>;
  /** Optional logger; disabled-mode loggers no-op. */
  logger?: DebugLogger;
}

/**
 * Run a write-side task, picking the detached or in-process path based on
 * `async` + `asyncSpawn` availability. Never throws — sync-handler errors
 * are caught and logged so the host's hot path stays safe.
 */
export async function runWriteTask<TPayload>(
  opts: RunWriteTaskOptions<TPayload>,
): Promise<void> {
  const log = opts.logger?.child("async-writer");

  if (opts.async && opts.asyncSpawn) {
    const spawnOpts = opts.asyncSpawn(opts.payload);
    const result = spawnDetached({ ...spawnOpts, logger: opts.logger });
    if (result.detached) {
      log?.log("dispatched_async");
      return;
    }
    log?.log("async_failed_falling_back_sync", { message: result.error?.message });
  }

  try {
    await opts.syncHandler(opts.payload);
    log?.log("dispatched_sync");
  } catch (err) {
    log?.log("sync_handler_failed", { message: errMessage(err) });
    // Swallow: write-path errors should never crash the host hot path.
  }
}

function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
