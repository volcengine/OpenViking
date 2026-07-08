/**
 * Pi OpenViking Extension
 *
 * Integrates pi with an OpenViking context database for persistent,
 * cross-session memory. Syncs conversation turns to OV, recalls
 * relevant memories on each prompt, and commits sessions for long-term
 * memory extraction.
 *
 * Design informed by: OpenClaw (synchronous recall), Claude Code plugin
 * (most mature, production-hardened), Hermes (anti-pattern: stale prefetch).
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { dirname } from "node:path";
import { loadConfig, type OVConfig } from "./config.js";
import { OVClient } from "./client.js";
import { RecallManager } from "./recall.js";
import { SyncManager } from "./sync.js";
import { buildProfileBlock } from "./shared/profile-inject.mjs";
import { guardVikingUriToolCall } from "./lib/uri-guard-adapter.mjs";
import { registerTools } from "./tools.js";

export default async function (pi: ExtensionAPI) {
  // --- Load config ---
  const config = loadConfig(dirname(new URL(import.meta.url).pathname));
  if (!config.enabled) return;

  // Env overrides

  // --- Initialize modules ---
  const client = new OVClient(config);
  const recall = new RecallManager(client, config);
  const sync = new SyncManager(client, config);

  // Session state
  let connected = false;
  let bypassed = false;
  let profileBlock = "";
  let archiveOverview = "";
  let toolsRegistered = false;
  let compacted = false;

  // ================================================================
  // Event Handlers
  // ================================================================

  // --- session_start ---
  pi.on("session_start", async (event, ctx) => {
    // Bypass check
    const cwd = process.cwd();
    for (const pattern of config.bypassPatterns) {
      if (matchBypass(cwd, pattern)) {
        bypassed = true;
        return;
      }
    }

    // Health check
    connected = await client.health();
    if (!connected) {
      if (config.logLevel === "info") {
        ctx.ui.notify("OpenViking: server not reachable", "warning");
      }
      return;
    }

    // Ensure OV session
    const piSessionId = ctx.sessionManager.getSessionId();
    const ok = await sync.ensureSession(piSessionId);
    if (!ok) {
      if (config.logLevel !== "silent") {
        ctx.ui.notify("OpenViking: failed to create session", "error");
      }
      return;
    }
    await sync.replayPending();

    // Profile injection
    profileBlock = await buildSessionProfileBlock(client, config);

    // Resume rehydration — fetch archive overview if session was previously committed
    if (sync.sessionId) {
      archiveOverview = await fetchArchiveOverview(client, sync.sessionId, config);
    }

    // Register tools (also re-registered in before_agent_start for pi -c continuations)
    registerTools(pi, client, sync);
    toolsRegistered = true;
    updateStatus(ctx, connected, 0, sync.sessionId, config);

    if (config.logLevel === "info") {
      ctx.ui.notify(`OpenViking connected (${piSessionId.slice(0, 8)}...)`, "info");
    }
  });

  // --- before_agent_start ---
  pi.on("before_agent_start", async (event, _ctx) => {
    // Re-register tools on resume — session_start doesn't fire for pi -c continuations
    if (!toolsRegistered) {
      registerTools(pi, client, sync);
      toolsRegistered = true;
    }

    if (!connected || bypassed) return;

    // Synchronous recall
    await recall.searchAndCache(event.prompt);

    // Compose system prompt additions
    const parts: string[] = [];
    if (profileBlock) parts.push(profileBlock);
    if (archiveOverview && (compacted || archiveOverview.trim())) parts.push(archiveOverview);
    parts.push("OpenViking tools: viking_search, viking_read, viking_browse, viking_remember, viking_forget, viking_add_resource, viking_archive_expand.");

    const additions = parts.join("\n\n");
    if (!additions) return;

    return {
      systemPrompt: event.systemPrompt + "\n\n" + additions,
    };
  });

  // --- context ---
  pi.on("context", async (event, _ctx) => {
    if (!connected || bypassed) return;
    const messages = recall.injectRecall(event.messages);
    return { messages };
  });

  // --- tool_call ---
  pi.on("tool_call", async (event, _ctx) => {
    const decision = guardVikingUriToolCall(event);
    if (!decision) return;
    return decision;
  });

  // --- turn_end ---
  pi.on("turn_end", async (event, ctx) => {
    if (!connected || bypassed || !config.syncTurns) return;

    const branch = ctx.sessionManager.getBranch();
    const added = await sync.syncBranch(branch);
    updateStatus(ctx, connected, added, sync.sessionId, config);
  });

  // --- session_before_compact ---
  pi.on("session_before_compact", async (_event, _ctx) => {
    if (!connected || bypassed) return;

    const archiveId = await sync.commit();
    compacted = true;

    // Cache archive overview for rehydration after compaction
    if (archiveId && sync.sessionId) {
      archiveOverview = await fetchArchiveOverview(
        client, sync.sessionId, config,
      );
    }
    // Return nothing → pi proceeds with default compaction
  });

  // --- session_shutdown ---
  pi.on("session_shutdown", async (_event, ctx) => {
    if (!connected || bypassed) return;

    await sync.shutdown();
    await sync.commit();
  });

  // --- agent_end ---
  pi.on("agent_end", async (_event, _ctx) => {
    recall.invalidate();
  });

  // ================================================================
  // Commands
  // ================================================================

  pi.registerCommand("viking", {
    description: "OpenViking status and manual operations. Use 'commit' to force a sync.",
    handler: async (args, ctx) => {
      if (!connected) {
        ctx.ui.notify("OpenViking: not connected", "warning");
        return;
      }

      if (args?.trim() === "commit") {
        await sync.shutdown();
        const result = await sync.commit();
        if (result) {
          ctx.ui.notify("OpenViking: committed successfully", "info");
        } else {
          ctx.ui.notify("OpenViking: commit failed", "error");
        }
        return;
      }

      // Status
      const sid = sync.sessionId ?? "none";
      ctx.ui.notify(
        `OpenViking: ${connected ? "connected" : "disconnected"} | session: ${sid.slice(0, 12)}...`,
        "info",
      );
    },
  });
}

// ================================================================
// Helper Functions
// ================================================================

/** Simple bypass pattern matching (prefix and glob). */
function matchBypass(cwd: string, pattern: string): boolean {
  if (pattern.startsWith("*")) {
    return cwd.endsWith(pattern.slice(1));
  }
  if (pattern.endsWith("*")) {
    return cwd.startsWith(pattern.slice(0, -1));
  }
  return cwd === pattern || cwd.startsWith(pattern + "/");
}

/** Build the <openviking-context> profile block. */
async function buildSessionProfileBlock(
  client: OVClient, config: OVConfig,
): Promise<string> {
  try {
    const profile = await buildProfileBlock(
      (path: string, init?: any, options?: any) => client.fetchJSON(path, init, 10000),
      config.profileTokenBudget,
      config.peerId,
    );
    if (!profile?.block) return "";
    return [
      '<openviking-context source="session-start">',
      profile.block,
      "</openviking-context>",
    ].join("\n");
  } catch {
    return "";
  }
}

/** Fetch archive overview for rehydration using the session context API. */
async function fetchArchiveOverview(
  client: OVClient, sessionId: string, config: OVConfig,
): Promise<string> {
  try {
    const ctx = await client.getSessionContext(sessionId, config.resumeContextBudget);
    if (!ctx || !ctx.latest_archive_overview) return "";

    return [
      '<openviking-context source="session-archive">',
      "<session-archive>",
      ctx.latest_archive_overview,
      "</session-archive>",
      "</openviking-context>",
    ].join("\n");
  } catch {
    return "";
  }
}

function updateStatus(ctx: any, connected: boolean, added: number, sessionId: string | null, config: OVConfig): void {
  const setter = ctx?.ui?.setStatus;
  if (typeof setter !== "function") return;
  const status = `${connected ? "OV ✓" : "OV ✗"} · ↩${added} · ✎ ${config.commitTokenThreshold} · ${sessionId ? sessionId.slice(0, 12) : "none"}`;
  try {
    setter(status);
  } catch {
    // Best effort; pi API shape may vary across fast-moving versions.
  }
}
