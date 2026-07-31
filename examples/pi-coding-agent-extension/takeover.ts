import type { OVClient } from "./client.js";
import type { OVConfig } from "./config.js";
import type { SyncManager } from "./sync.js";
import { TakeoverCore } from "./lib/takeover-core.mjs";

export function createTakeoverManager(opts: {
  pi: any;
  client: OVClient;
  sync: SyncManager;
  config: OVConfig;
  log?: (message: string) => void;
}): TakeoverCore {
  const { pi, client, sync, config } = opts;
  return new TakeoverCore({
    config,
    io: {
      flush: () => sync.flushForTakeover(),
      commit: (commitOpts?: { queueOnFailure?: boolean; keepRecentCount?: number }) => sync.commit(commitOpts),
      fetchOverview: async (tokenBudget?: number) => {
        if (!sync.sessionId) return "";
        const ctx = await client.getSessionContext(
          sync.sessionId,
          tokenBudget ?? config.takeoverOverviewBudget * 4,
        );
        return ctx?.latest_archive_overview ?? "";
      },
      persistEntry: (customType: string, data: any) => {
        if (typeof pi?.appendEntry === "function") {
          pi.appendEntry(customType, data);
        }
      },
      getWatermark: () => sync.syncedCount,
      log: opts.log,
    },
  });
}
