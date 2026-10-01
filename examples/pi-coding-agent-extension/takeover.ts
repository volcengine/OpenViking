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
      // Deliver the branch, then confirm the queue is empty for this session.
      syncBranch: (branch: any[]) => sync.syncBranch(branch),
      flush: (budgetMs?: number) => sync.flushForTakeover(budgetMs),
      commit: (commitOpts?: { queueOnFailure?: boolean; timeoutMs?: number }) => sync.commit(commitOpts),
      // Read the Working Memory of the exact archive this commit produced, not
      // the session's newest `/context` overview — the latter can be an older
      // archive this takeover did not create.
      readArchiveOverview: (archiveUri: string) => client.readArchiveOverview(archiveUri),
      // Whether a still-unsummarized archive can get its summary at all.
      archiveState: (archiveUri: string) => client.getArchiveState(archiveUri),
      persistEntry: (customType: string, data: any) => {
        if (typeof pi?.appendEntry === "function") {
          pi.appendEntry(customType, data);
        }
      },
      getWatermark: () => sync.syncedCount,
      droppedCount: () => sync.droppedCount,
      lastCommitError: () => sync.lastCommitError,
      availableTools: () => typeof pi?.getActiveTools === "function" ? pi.getActiveTools() : [],
      log: opts.log,
    },
  });
}
