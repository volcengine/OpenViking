/**
 * Adapter: binds the pure `ContextWindowCore` to pi, the OpenViking client and
 * the sync manager. The core owns every decision; everything with an effect —
 * network, disk, pi APIs, the clock — arrives through this io object, which is
 * what makes the state machine testable without either side.
 */
import type { OVClient } from "./client.js";
import type { OVConfig } from "./config.js";
import type { SyncManager } from "./sync.js";
import { createRequire } from "node:module";
import { ContextWindowCore } from "./lib/context-window-core.mjs";
import { DEFAULT_RESERVE_TOKENS, readReserveTokens } from "./lib/pi-settings.mjs";
import { listPending } from "./shared/pending-queue.mjs";

/** Queued `addMessage` entries this OV session still owes the server. */
function countUndeliveredForSession(pendingEntries: any[], sid: string): number {
  if (!sid) return 0;
  let count = 0;
  for (const item of Array.isArray(pendingEntries) ? pendingEntries : []) {
    const entry =
      item && typeof item === "object" && item.entry && typeof item.entry === "object"
        ? item.entry
        : item;
    if (entry?.type === "addMessage" && entry.sessionId === sid) count++;
  }
  return count;
}

/** `sleep` that gives up as soon as the tool call is aborted. */
function abortableSleep(ms: number, signal?: AbortSignal | null): Promise<void> {
  const delay = Math.max(0, Math.floor(Number(ms) || 0));
  return new Promise<void>((resolve) => {
    if (signal?.aborted) {
      resolve();
      return;
    }
    let onAbort: (() => void) | null = null;
    const timer = setTimeout(() => {
      if (onAbort && signal) signal.removeEventListener("abort", onAbort);
      resolve();
    }, delay);
    if (signal) {
      onAbort = () => {
        clearTimeout(timer);
        resolve();
      };
      signal.addEventListener("abort", onAbort, { once: true });
    }
  });
}

export function createContextWindowManager(opts: {
  pi: any;
  client: OVClient;
  sync: SyncManager;
  config: OVConfig;
  logger?: { log?: (stage: string, data?: unknown) => void };
}): ContextWindowCore {
  const { pi, client, sync, config } = opts;

  // The core asks for the pending count synchronously (it only reports it in a
  // refusal message), so the number is refreshed whenever the barrier is asked
  // about and cached in between.
  let pendingCache = 0;
  const refreshPendingCache = async (): Promise<number> => {
    const sid = sync.sessionId;
    if (!sid) {
      pendingCache = 0;
      return 0;
    }
    try {
      pendingCache = countUndeliveredForSession(await listPending(), sid);
    } catch {
      pendingCache = 0;
    }
    return pendingCache;
  };

  return new ContextWindowCore({
    config: config.contextWindow,
    io: {
      syncBranch: (branch: any[]) => sync.syncBranch(branch),

      flush: async ({ budgetMs }: { budgetMs: number }) => {
        const ok = await sync.flushBarrier({ budgetMs });
        // Refresh here rather than in pendingCount(): this is the one moment
        // the core needs an accurate number, and it is already awaiting.
        if (ok) pendingCache = 0;
        else await refreshPendingCache();
        return ok;
      },

      // Direct write, never the queue: a handoff note that lands after the
      // commit would be archived into the *next* window instead of this one.
      postHandoff: async (text: string) => {
        const sid = sync.sessionId;
        if (!sid) return false;
        try {
          return await client.addMessage(sid, "assistant", text);
        } catch {
          return false;
        }
      },

      // `queueOnFailure:false` for the same reason: a queued commit would fire
      // later, splitting a window the model still believes it is inside.
      commit: async ({ keepRecentCount }: { keepRecentCount?: number } = {}) => {
        try {
          return await sync.commit({
            queueOnFailure: false,
            keepRecentCount: keepRecentCount ?? 0,
          });
        } catch {
          return null;
        }
      },

      readArchiveOverview: async (archiveUri: string) => {
        try {
          return await client.readArchiveOverview(archiveUri);
        } catch {
          return null;
        }
      },

      getTask: async (taskId: string) => {
        try {
          return await client.getTask(taskId);
        } catch {
          return null;
        }
      },

      persistEntry: (customType: string, data: any) => {
        if (typeof pi?.appendEntry === "function") pi.appendEntry(customType, data);
      },

      getWatermark: () => sync.syncedCount,
      pendingCount: () => pendingCache,
      droppedCount: () => sync.droppedCount,
      commitTraceId: () => sync.commitTraceId,
      now: () => Date.now(),
      sleep: (ms: number, signal?: AbortSignal | null) => abortableSleep(ms, signal),
      log: (message: string) => {
        opts.logger?.log?.("window", message);
      },
      connected: () => client.connected,
      sessionId: () => sync.sessionId,
    },
  });
}

/**
 * pi's `compaction.reserveTokens`, read from `<cwd>/.pi/settings.json` then
 * `~/.pi/agent/settings.json` (pi merges project over global). Used to clamp
 * the reminder thresholds under pi's own auto-compaction line.
 *
 * Best effort: any failure yields pi's own default, 16384.
 */
export function readPiReserveTokens(cwd?: string): number {
  let configDirName = ".pi";
  try {
    // Present since pi 0.80; the literal fallback keeps this working when the
    // package cannot be resolved from here (tests, a standalone checkout).
    const mod = createRequire(import.meta.url)("@earendil-works/pi-coding-agent");
    if (typeof mod?.CONFIG_DIR_NAME === "string" && mod.CONFIG_DIR_NAME) {
      configDirName = mod.CONFIG_DIR_NAME;
    }
  } catch {
    // keep ".pi"
  }
  try {
    return readReserveTokens({
      cwd: cwd ?? process.cwd(),
      configDirName,
      // pi's own override for the agent directory (`ENV_AGENT_DIR`).
      agentDir: process.env.PI_CODING_AGENT_DIR || "",
    });
  } catch {
    return DEFAULT_RESERVE_TOKENS;
  }
}
