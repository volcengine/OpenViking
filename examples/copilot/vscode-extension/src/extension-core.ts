/**
 * VS Code-free activation core for the OpenViking Copilot extension.
 *
 * Splitting the activation logic out of `extension.ts` lets us
 * unit-test it without spinning up a VS Code instance — `extension.ts`
 * stays the thin adapter that imports `vscode` and wires through.
 *
 * Responsibilities:
 *   - Load PluginConfig (env > host overrides > ovcli.conf > ov.conf > defaults)
 *   - Build OVClient + DebugLogger
 *   - Provide a small registry so per-session CommitQueues created later
 *     by the chat participant are flushed on deactivate
 *   - No-op when the plugin is disabled (returns null from build)
 */

import {
  CommitQueue,
  createDebugLogger,
  isPluginEnabled,
  loadConfig,
  OVClient,
  type DebugLogger,
  type PluginConfig,
} from "@openviking/copilot-shared";

/**
 * Subset of CommitQueue we depend on for cleanup. Duck-typed so tests
 * can register lightweight stubs without instantiating a full queue.
 */
export interface FlushableQueue {
  flush(): Promise<void>;
}

export interface ActivationHandle {
  cfg: PluginConfig;
  client: OVClient;
  logger: DebugLogger;
  /**
   * Register a queue (typically a CommitQueue) so it gets flushed when
   * `runDeactivate` runs. Idempotent — registering the same queue
   * twice doesn't double-flush. The chat participant calls this when
   * it lazily creates per-session queues.
   */
  registerCommitQueue(queue: FlushableQueue): void;
  /** Number of registered queues (test/telemetry hook). */
  registeredCount(): number;
}

export interface BuildActivationHandleOptions {
  /**
   * Highest-priority overrides for PluginConfig — typically derived
   * from VS Code workspace + user settings by the adapter. Undefined
   * fields fall through to env/config-file/defaults.
   */
  hostOverrides?: Partial<PluginConfig>;
  /**
   * Inject a fetch implementation for tests. Defaults to global fetch
   * (Node 22+) when omitted.
   */
  fetchImpl?: typeof fetch;
  /**
   * Override the enabled check. When omitted, falls back to
   * `isPluginEnabled()` from the shared package.
   */
  enabledOverride?: boolean;
}

/**
 * Build the activation context, or return `null` when the plugin is
 * disabled (no config files + no env force-enable). The caller (the
 * VS Code adapter) treats null as a graceful no-op activation.
 */
export function buildActivationHandle(
  opts: BuildActivationHandleOptions = {},
): ActivationHandle | null {
  const enabled = opts.enabledOverride ?? isPluginEnabled();
  if (!enabled) return null;

  const cfg = loadConfig({
    agentIdDefault: "copilot-vscode",
    ...(opts.hostOverrides ? { hostOverrides: opts.hostOverrides } : {}),
  });
  const logger = createDebugLogger(cfg, { scope: "extension" });
  const client = new OVClient(cfg, {
    logger,
    ...(opts.fetchImpl ? { fetchImpl: opts.fetchImpl } : {}),
  });

  const queues = new Set<FlushableQueue>();
  const handle: ActivationHandle = {
    cfg,
    client,
    logger,
    registerCommitQueue(queue) {
      queues.add(queue);
      logger.log("commit_queue_registered", { count: queues.size });
    },
    registeredCount: () => queues.size,
  };

  // Stash the queue set on the handle privately so runDeactivate can
  // reach it without exposing internals on the public type.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (handle as any).__queues = queues;

  logger.log("activated", {
    agentId: cfg.agentId,
    baseUrl: cfg.baseUrl,
  });
  return handle;
}

/**
 * Flush every registered commit queue, in registration order.
 * Resolves once all flushes settle (whether they succeed or fail).
 * Errors are logged via the handle's logger and never thrown — the
 * host's deactivate path must always complete.
 */
export async function runDeactivate(handle: ActivationHandle | null): Promise<void> {
  if (!handle) return;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const queues: Set<FlushableQueue> | undefined = (handle as any).__queues;
  if (queues && queues.size > 0) {
    await Promise.all(
      [...queues].map(async (q) => {
        try {
          await q.flush();
        } catch (err) {
          handle.logger.log("deactivate_flush_failed", {
            message: err instanceof Error ? err.message : String(err),
          });
        }
      }),
    );
  }
  handle.logger.log("deactivated", { flushed: queues?.size ?? 0 });
}

// Re-export types the VS Code adapter and the upcoming participant need.
export type { CommitQueue, DebugLogger, OVClient, PluginConfig };
