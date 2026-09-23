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
import { fileURLToPath } from "node:url";
import { isCaptureEnabled } from "./shared/capture-utils.mjs";
import { createLogger } from "./shared/debug-log.mjs";
import { EXTENSION_VERSION, loadConfig, buildBridgeProxyConfig, type OVConfig } from "./config.js";
import { OVClient } from "./client.js";
import { RecallManager } from "./recall.js";
import { RecallLedger } from "./lib/recall-ledger.mjs";
import { SyncManager } from "./sync.js";
import { buildProfileBlock } from "./shared/profile-inject.mjs";
import { isBypassed } from "./shared/session-model.mjs";
import { guardVikingUriToolCall, noticeVikingUriToolResult } from "./lib/uri-guard-adapter.mjs";
import { createMcpBridge, DEFAULT_HANDSHAKE_BUDGET_MS } from "./lib/mcp-bridge.mjs";
import { registerMcpTools } from "./tools.js";
import { createTakeoverManager } from "./takeover.js";

/** This extension's directory, published for the experimental fork's probe. */
const EXTENSION_DIR = dirname(fileURLToPath(import.meta.url));

export default async function (pi: ExtensionAPI) {
  // --- Load config ---
  const cwd = process.cwd();
  const config = loadConfig(cwd);
  if (!config.enabled) return;
  // Shared key (`shared/config-schema.mjs`), already honoured by opencode: the
  // MCP tool surface is off, while recall, sync and takeover carry on.
  const mcpEnabled = (config as any).mcpEnabled !== false;

  // Env overrides

  // --- Initialize modules ---
  const client = new OVClient(config);
  const sync = new SyncManager(client, config);
  const recall = new RecallManager(
    client,
    config,
    () => sync.sessionId,
    // The ledger keeps request prefixes byte-stable for provider prompt
    // caches (#4137); it is per pi session and opened once the id is known.
    config.recallLedger ? new RecallLedger() : null,
  );
  const logger = createLogger("pi", {
    debug: Boolean(config.debugLogPath),
    debugLogPath: config.debugLogPath,
  });
  const takeover = createTakeoverManager({
    pi, client, sync, config,
    log: (message: string) => logger.log("takeover", message),
  });

  // Re-read credentials before connecting or invoking a tool.
  const bridge = mcpEnabled
    ? createMcpBridge({
      readConfig: () => buildBridgeProxyConfig(loadConfig(cwd)),
      // No "memory" in the name: every plugin identity is moving to plain
      // `openviking`, and a new identifier starts out on the new spelling.
      clientInfo: { name: "openviking-pi", version: EXTENSION_VERSION },
    })
    : null;

  // Session state
  let connected = false;
  let bypassed = false;
  let profileBlock = "";
  let archiveOverview = "";
  let toolsReady = false;
  let toolsRegistered = false;
  let closed = false;
  const marker = { dir: EXTENSION_DIR };
  const toolNames: string[] = [];
  let toolFailureNotified = false;
  let compacted = false;
  let started = false;
  let startPromise: Promise<void> | null = null;

  /**
   * Take a finished handshake: register what the server listed, or report the
   * failure once.
   *
   * The tool list is never refreshed later in the session. pi has no
   * `unregisterTool`, and adding or removing a tool mid-session rewrites the
   * system prompt's tool section, which invalidates the provider's cached
   * prefix. A server that gained or lost a tool takes effect in the next pi
   * session.
   */
  const adoptBridgeTools = (
    ctx: any,
    state: { connected: boolean; error: string | null },
  ): void => {
    if (!bridge || closed || toolsRegistered) return;
    if (state.connected) {
      toolNames.push(...registerMcpTools(pi, bridge));
      toolsRegistered = true;
      toolsReady = toolNames.length > 0;
      logger.log("mcp_tools", { registered: toolNames.length });
      return;
    }
    // A dead, unauthorized or MCP-less server costs the session its tools and
    // nothing else, so it is said once and not repeated every turn.
    if (toolFailureNotified) return;
    toolFailureNotified = true;
    if (config.logLevel === "silent") return;
    ctx?.ui?.notify?.(
      `OpenViking: no tools this session — ${state.error || "handshake failed"}${rootKeyHint(state.error)}`,
      "warning",
    );
  };

  /** The `/viking` line about the tool surface: a count, or the last failure. */
  const toolsStatusLine = (): string => {
    if (!bridge) return "tools: disabled by mcpEnabled";
    if (toolNames.length) return `tools: ${toolNames.length}`;
    const error = bridge.state.error;
    if (!error) return "tools: none (handshake not finished)";
    return `tools: none — ${error}${rootKeyHint(error)}`;
  };

  // ================================================================
  // Event Handlers
  // ================================================================

  const start = async (ctx: any): Promise<void> => {
    if (started || closed) return;
    if (startPromise) return startPromise;

    startPromise = (async () => {
      // Bypass check
      if (isBypassed(config, { cwd: process.cwd() })) {
        bypassed = true;
        started = true;
        return;
      }

      // Health check
      connected = await client.health();
      if (closed) return;
      if (!connected) {
        if (config.logLevel === "info") {
          ctx.ui.notify("OpenViking: server not reachable", "warning");
        }
        return;
      }

      // What the experimental fork probes to stand down. Set as soon as the
      // session is live, independently of any tool: a /mcp 401/403 leaves this
      // extension with zero tools while it still writes the OpenViking session,
      // and that is exactly the case the fork must not join.
      (globalThis as any).__OPENVIKING_PI_EXTENSION__ = marker;

      // Discover tools alongside REST startup; failure leaves REST available.
      const handshake = bridge ? bridge.connect(DEFAULT_HANDSHAKE_BUDGET_MS) : null;

      // Ensure OV session
      const piSessionId = ctx.sessionManager.getSessionId();
      recall.openLedger(piSessionId);
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

      const branch = typeof ctx.sessionManager.getBranch === "function"
        ? ctx.sessionManager.getBranch()
        : [];
      if (config.takeoverEnabled) {
        takeover.restore(branch);
        sync.restoreWatermark(takeover.state.syncedEntryCount);
      } else if (sync.sessionId) {
        // Resume rehydration — fetch archive overview if session was previously committed.
        archiveOverview = await fetchArchiveOverview(client, sync.sessionId, config);
      }

      // Join the handshake and publish whatever the server listed (also needed
      // for pi -c continuations, which never fire session_start).
      if (handshake) adoptBridgeTools(ctx, await handshake);
      if (closed) return;
      updateStatus(ctx, connected, 0, sync.sessionId, config, takeover.state, toolsReady);

      started = true;
      if (config.logLevel === "info") {
        ctx.ui.notify(`OpenViking connected (${piSessionId.slice(0, 8)}...)`, "info");
      }
    })().finally(() => {
      startPromise = null;
    });

    return startPromise;
  };

  // --- session_start ---
  pi.on("session_start", async (event, ctx) => {
    // Fire-and-forget: the OV chain (health check, session ensure, profile
    // build) costs ~2s against the remote server; blocking session_start on it
    // delays every pi startup. start() is memoized via startPromise, so
    // before_agent_start awaits the same in-flight chain before the first
    // provider request — the first turn still gets profile + recall.
    void start(ctx).catch((error) => {
      logger.logError("session_start", error);
    });
  });

  // --- before_agent_start ---
  pi.on("before_agent_start", async (event, ctx) => {
    // Read before awaiting, because this is what separates the retry below
    // from the attempt start() makes: on the turn that runs the startup chain
    // this is false, and from the next turn on it is true. Testing `started`
    // after the await would be true on turn one as well, and the retry would
    // fire a second handshake in the same turn — a real one, since the bridge
    // clears its in-flight promise as soon as a failed attempt settles. That
    // costs the first turn a second handshake budget before recall is even
    // queued, for an answer start() already has.
    const wasStarted = started;

    // session_start doesn't fire for pi -c continuations.
    await start(ctx);

    // Retry the handshake when the session is live but toolless — a server
    // started after pi, or a credential fixed mid-session. `bridge` is null
    // exactly when `mcpEnabled` is false, and a bypassed directory or a failed
    // health check never gets here: bypass means this directory does not touch
    // OpenViking at all. `connect()` re-handshakes only after a failure; after
    // a success it hands back the state it already has.
    if (bridge && wasStarted && connected && !bypassed && !closed && !toolsRegistered) {
      adoptBridgeTools(ctx, await bridge.connect(DEFAULT_HANDSHAKE_BUDGET_MS));
      updateStatus(ctx, connected, 0, sync.sessionId, config, takeover.state, toolsReady);
    }

    if (!connected || bypassed || closed) return;

    // Queue recall for the context hook. Pi renders the user message before
    // that hook, so recall latency does not delay the message appearing.
    recall.queueSearch(event.prompt);

    // Compose system prompt additions
    const parts: string[] = [];
    if (profileBlock) parts.push(profileBlock);
    if (!config.takeoverEnabled && archiveOverview && (compacted || archiveOverview.trim())) {
      parts.push(archiveOverview);
    }
    // Generated from what actually registered, so it can never name a tool the
    // server does not have. Omitted entirely when the handshake produced none,
    // which keeps the prefix free of a promise nothing can keep.
    if (toolNames.length) {
      parts.push(`OpenViking tools (use these for viking:// URIs): ${toolNames.join(", ")}.`);
    }

    const additions = parts.join("\n\n");
    if (!additions) return;

    return {
      systemPrompt: event.systemPrompt + "\n\n" + additions,
    };
  });

  // --- context ---
  pi.on("context", async (event, ctx) => {
    if (!connected || bypassed) return;

    // Keep recall synchronous with the provider request so the current prompt
    // still receives current-query memory, without blocking user-message UI.
    await recall.searchPending();

    // The entry IDs are an optional optimization for replaying the recall
    // ledger. Compatible hosts may omit buildContextEntries(), so fail closed
    // to nullable IDs rather than guessing from another SessionManager API.
    const sessionManager = ctx.sessionManager;
    const entries = typeof sessionManager?.buildContextEntries === "function"
      ? sessionManager.buildContextEntries()
      : [];
    const userEntryIds = entries
      .filter((entry: unknown): entry is { id?: unknown; type: "message"; message: { role: "user" } } => {
        if (!entry || typeof entry !== "object") return false;
        if (!("type" in entry) || !("message" in entry)) return false;
        const type = entry.type;
        const message = entry.message;
        return type === "message" &&
          !!message &&
          typeof message === "object" &&
          "role" in message &&
          message.role === "user";
      })
      .map((entry): string | undefined =>
        typeof entry.id === "string" ? entry.id : undefined
      );
    const messageIds = new WeakMap<object, string>();
    let userIndex = 0;
    for (const message of event.messages as any[]) {
      if (message?.role !== "user") continue;
      const entryId = userEntryIds[userIndex++];
      if (entryId && typeof message === "object") {
        messageIds.set(message, entryId);
      }
    }

    const afterTakeover = config.takeoverEnabled
      ? takeover.transformContext(event.messages as any)
      : event.messages;
    const messages = recall.injectRecall(
      afterTakeover,
      (message) => messageIds.get(message) ?? null,
    );
    return { messages };
  });

  // --- tool_call ---
  pi.on("tool_call", async (event, _ctx) => {
    const decision = guardVikingUriToolCall(event);
    if (!decision) return;
    return decision;
  });

  // --- tool_result ---
  pi.on("tool_result", async (event, _ctx) => {
    const notice = noticeVikingUriToolResult(event);
    if (!notice) return;
    return notice;
  });

  // --- turn_end ---
  pi.on("turn_end", async (event, ctx) => {
    if (!connected || bypassed || !isCaptureEnabled(config)) return;

    const branch = ctx.sessionManager.getBranch();
    const result = await sync.syncBranch(branch);
    logger.log("turn_end", { added: result.added, tokens: result.tokens });
    await takeover.onTurnSynced(result.tokens);
    updateStatus(ctx, connected, result.added, sync.sessionId, config, takeover.state, toolsReady);
  });

  // --- session_before_compact ---
  pi.on("session_before_compact", async (event, _ctx) => {
    if (!connected || bypassed) return;

    if (config.takeoverEnabled) {
      const prep = (event as any)?.preparation ?? {};
      return await takeover.handleBeforeCompact({
        firstKeptEntryId: prep.firstKeptEntryId,
        tokensBefore: prep.tokensBefore ?? 0,
      });
    }

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
    closed = true;
    if ((globalThis as any).__OPENVIKING_PI_EXTENSION__ === marker) {
      delete (globalThis as any).__OPENVIKING_PI_EXTENSION__;
    }
    // Release MCP requests without preventing the final REST commit.
    if (bridge) {
      try {
        await bridge.close();
      } catch (error) {
        logger.logError("session_shutdown", error);
      }
    }

    if (!connected || bypassed) return;

    await sync.shutdown();
    if (config.takeoverEnabled) {
      await takeover.shutdown();
    } else {
      await sync.commit();
    }
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
        const commitResult = config.takeoverEnabled ? null : await sync.commit();
        const ok = config.takeoverEnabled
          ? await takeover.commitAndAdvance()
          : commitResult !== null;
        if (ok) {
          ctx.ui.notify(
            "OpenViking: committed successfully" +
              (commitResult?.trace_id ? ` (trace_id=${commitResult.trace_id})` : ""),
            "info",
          );
        } else {
          ctx.ui.notify("OpenViking: commit failed", "error");
        }
        return;
      }

      // Status
      const sid = sync.sessionId ?? "none";
      const t = takeover.state;
      const takeoverInfo = config.takeoverEnabled
        ? ` | takeover: ${t.coveredUserTurns}/${t.lastSeenUserTurns} turns archived, ~${t.pendingTokens} tokens pending`
        : "";
      ctx.ui.notify(
        `OpenViking: ${connected ? "connected" : "disconnected"} | session: ${sid.slice(0, 12)}...`
          + `${takeoverInfo} | ${toolsStatusLine()}`,
        "info",
      );
    },
  });
}

// ================================================================
// Helper Functions
// ================================================================

/** Build the <openviking-context> profile block. */
async function buildSessionProfileBlock(
  client: OVClient, config: OVConfig,
): Promise<string> {
  try {
    const profile = await buildProfileBlock(
      (path, init, options) => client.fetchJSON(path, init, options),
      config.profileTokenBudget,
      config.peerId,
      config,
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

/**
 * The extra line for a failed handshake that a root key explains.
 *
 * A root key authenticates fine and is then refused on `/mcp`, so the HTTP
 * status is the only clue, and without this the operator sees a 403 with no
 * way to act on it.
 */
function rootKeyHint(error: string | null): string {
  return error && /\b403\b/.test(error)
    ? "\nA root key cannot reach /mcp — use a user or admin key."
    : "";
}

function updateStatus(
  ctx: any,
  connected: boolean,
  added: number,
  sessionId: string | null,
  config: OVConfig,
  takeoverState?: { pendingTokens?: number; coveredUserTurns?: number },
  toolsReady?: boolean,
): void {
  const setter = ctx?.ui?.setStatus;
  if (typeof setter !== "function") return;
  const threshold = config.takeoverEnabled
    ? config.takeoverTokenThreshold
    : config.commitTokenThreshold;
  const pending = config.takeoverEnabled && takeoverState
    ? ` · ctx ${takeoverState.coveredUserTurns ?? 0} · ~${takeoverState.pendingTokens ?? 0}/${threshold}`
    : ` · ✎ ${threshold}`;
  // Only worth a segment when tools were expected and are missing: `OV ✓` with
  // no tools is a working session whose OpenViking tools failed, and
  // `mcpEnabled: false` asked for that and is not a fault.
  const tools = connected && (config as any).mcpEnabled !== false && !toolsReady
    ? " · tools ✗"
    : "";
  const status = `${connected ? "OV ✓" : "OV ✗"}${tools} · ↩${added}${pending} · ${sessionId ? sessionId.slice(0, 12) : "none"}`;
  try {
    setter("openviking", status);
  } catch {
    // Best effort; pi API shape may vary across fast-moving versions.
  }
}
