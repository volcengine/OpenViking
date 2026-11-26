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
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { isCaptureEnabled } from "./shared/capture-utils.mjs";
import { createLogger } from "./shared/debug-log.mjs";
import { EXTENSION_VERSION, loadConfig, buildBridgeProxyConfig, type OVConfig } from "./config.js";
import { OVClient } from "./client.js";
import { RecallManager } from "./recall.js";
import { RecallLedger } from "./lib/recall-ledger.mjs";
import { SyncManager } from "./sync.js";
import { buildProfileBlock } from "./shared/profile-inject.mjs";
import { formatSessionHistoryPointer } from "./shared/session-history-pointer.mjs";
import { isBypassed } from "./shared/session-model.mjs";
import { guardVikingUriToolCall, noticeVikingUriToolResult } from "./lib/uri-guard-adapter.mjs";
import { createMcpBridge, DEFAULT_HANDSHAKE_BUDGET_MS } from "./lib/mcp-bridge.mjs";
import { registerMcpTools, TOOL_NAME_PREFIX } from "./tools.js";
import { createTakeoverManager } from "./takeover.js";
import { HANDLER_BUDGET_MS, describeSkip } from "./lib/takeover-core.mjs";
import { createSealHandlers, createSealPost } from "./lib/seal-adapter.mjs";
import { featureStatus, healthVersion } from "./shared/server-features.mjs";

/** This extension's directory, published for the experimental fork's probe. */
const EXTENSION_DIR = dirname(fileURLToPath(import.meta.url));

/**
 * Budget for the final commit on the exit path. The host awaits
 * session_shutdown before releasing the terminal, so this waits at most a few
 * seconds; anything not committed by then is already in the on-disk pending
 * queue and replays at the next session start.
 */
const SHUTDOWN_COMMIT_BUDGET_MS = 3000;

export default async function (pi: ExtensionAPI) {
  // --- Load config ---
  const cwd = process.cwd();
  const config = loadConfig(cwd);
  if (!config.enabled) return;
  // Shared key (`shared/config-schema.mjs`), already honoured by opencode: the
  // MCP tool surface is off, while recall, sync and takeover carry on.
  const mcpEnabled = (config as any).mcpEnabled !== false;

  // Installed as an extension rather than a package, so pi does not scan
  // skills/ on its own. The skills teach the MCP tools, so they follow them.
  if (mcpEnabled) {
    pi.on("resources_discover", async () => ({ skillPaths: [join(EXTENSION_DIR, "skills")] }));
  }

  // Env overrides

  // --- Initialize modules ---
  const client = new OVClient(config);
  const sync = new SyncManager(client, config, (type, data) => pi.appendEntry(type, data));
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

  // Plugin-side sealing (`sealSecrets`). Whether the server supports it is read
  // from its own /health on the first hook that needs it, and again while it
  // cannot seal. When sealing
  // cannot be done the traffic is stopped. A bypassed directory does not touch
  // OpenViking at all, sealing included.
  const seal = (config as any).sealSecrets === true && !isBypassed(config, { cwd })
    ? createSealHandlers({
      post: createSealPost((path, init, options) => client.fetchJSON(path, init, options)),
      enrollToolSecrets: (config as any).enrollToolSecrets === true,
      check: async () => featureStatus(
        "privacy_seal",
        await healthVersion((path, init, options) => client.fetchJSON(path, init, options)),
      ),
      ownToolNames: () => toolNames,
      tell: tellUser,
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
  let historyEvent: "compact" | "resume" | null = null;
  let compactedInRun = false;
  let reloaded = false;
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
      toolNames.push(...registerMcpTools(pi, bridge, () => sync.sessionId));
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

  const historyPointer = (): string => {
    if (!historyEvent || !config.sessionHistoryPointer || !isCaptureEnabled(config)
        || !toolNames.includes(TOOL_NAME_PREFIX + "session_history")) return "";
    return formatSessionHistoryPointer(sync.sessionId, {
      event: historyEvent,
      tool: "the OpenViking tool openviking_session_history",
    }) || "";
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

      // Restore takeover state — sync watermark, pending archive and capture
      // gap — before replaying any queued messages, so recovery and replay
      // cannot disagree about what has been delivered (plan §4).
      const branch = typeof ctx.sessionManager.getBranch === "function"
        ? ctx.sessionManager.getBranch()
        : [];
      if (config.takeoverEnabled) {
        takeover.restore(branch);
      }
      sync.restoreCapture(branch, !ctx.sessionManager.getHeader?.()?.parentSession);
      // Only a branch this OV session already captured is resumed; a fork's
      // checkpoints belong to its parent's session.
      if (sync.syncedCount > 0 && !historyEvent) {
        if (!reloaded) historyEvent = "resume";
        // /reload leaves the context as it was, so only a compaction in it needs the note.
        else if (branch.some((entry: any) => entry?.type === "compaction")) historyEvent = "compact";
      }

      await sync.replayPending();
      if (config.takeoverEnabled && sync.droppedCount > 0) {
        takeover.recordCaptureGap();
      }

      // Profile injection
      profileBlock = await buildSessionProfileBlock(client, config);

      if (!config.takeoverEnabled && config.resumeArchiveInject && sync.sessionId) {
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
    reloaded = (event as any)?.reason === "reload";
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
    const deadline = Date.now() + HANDLER_BUDGET_MS;
    // Read before awaiting, because this is what separates the retry below
    // from the attempt start() makes: on the turn that runs the startup chain
    // this is false, and from the next turn on it is true. Testing `started`
    // after the await would be true on turn one as well, and the retry would
    // fire a second handshake in the same turn — a real one, since the bridge
    // clears its in-flight promise as soon as a failed attempt settles. That
    // costs the first turn a second handshake budget before recall is even
    // queued, for an answer start() already has.
    const wasStarted = started;
    // From here on the note rides in this run's system prompt.
    compactedInRun = false;

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

    // A summary that finished since the last turn — or in the last `pi -p`
    // process — trims this prompt's request already.
    if (config.takeoverEnabled) {
      await takeover.resumePending(() => ctx.sessionManager.getBranch(), { deadline });
    }

    // Queue recall for the context hook. Pi renders the user message before
    // that hook, so recall latency does not delay the message appearing.
    recall.queueSearch(event.prompt);

    // Compose system prompt additions
    const parts: string[] = [];
    if (profileBlock) parts.push(profileBlock);
    if (!config.takeoverEnabled && config.resumeArchiveInject && archiveOverview && (compacted || archiveOverview.trim())) {
      parts.push(archiveOverview);
    }
    // Generated from what actually registered, so it can never name a tool the
    // server does not have. Omitted entirely when the handshake produced none,
    // which keeps the prefix free of a promise nothing can keep.
    if (toolNames.length) {
      parts.push(`OpenViking tools (use these for viking:// URIs): ${toolNames.join(", ")}.`);
    }
    // Kept on every turn after a compaction or resume: pi rebuilds the system
    // prompt each turn, so a one-turn note would drop out of the context again.
    const pointer = historyPointer();
    if (pointer) parts.push(pointer);

    const additions = parts.join("\n\n");
    if (!additions) return;

    return {
      systemPrompt: event.systemPrompt + "\n\n" + additions,
    };
  });

  // --- sealing: input, and the session text and note of each run ---
  // After the handler above, so the session text follows the profile block.
  if (seal) {
    pi.on("input", async (event, ctx) => seal.onInput(event, ctx));
    pi.on("before_agent_start", async (event, _ctx) => seal.onBeforeAgentStart(event));
  }

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

    // Takeover's boundary is a branch entry id; it maps it onto these messages
    // through pi's context projection of the branch.
    const afterTakeover = config.takeoverEnabled
      ? takeover.transformContext(
          event.messages as any,
          typeof sessionManager?.getBranch === "function" ? sessionManager.getBranch() : [],
        )
      : event.messages;
    const messages = recall.injectRecall(
      afterTakeover,
      (message) => messageIds.get(message) ?? null,
    );
    // A compaction inside a run keeps that run's system prompt, so the rest of
    // the run gets the note right after the summary.
    const pointer = compactedInRun ? historyPointer() : "";
    if (pointer) {
      const at = messages.findIndex((message: any) => message?.role === "compactionSummary") + 1;
      messages.splice(at, 0, { role: "user", content: [{ type: "text", text: pointer }], timestamp: Date.now() });
    }
    return { messages };
  });

  // --- tool_call ---
  // The URI guard first, so it judges what the model wrote; then sealing puts
  // the real values in for the references, in place.
  pi.on("tool_call", async (event, _ctx) => {
    const decision = guardVikingUriToolCall(event);
    if (!decision) return;
    return decision;
  });
  if (seal) pi.on("tool_call", async (event, _ctx) => seal.onToolCall(event));

  // --- tool_result ---
  // Sealing first, so the notice below is added to the sealed result and is
  // kept even when the result had to be withheld.
  if (seal) pi.on("tool_result", async (event, _ctx) => seal.onToolResult(event));
  pi.on("tool_result", async (event, _ctx) => {
    const notice = noticeVikingUriToolResult(event);
    if (!notice) return;
    return notice;
  });

  // --- turn_end ---
  pi.on("turn_end", async (event, ctx) => {
    if (!connected || bypassed || !isCaptureEnabled(config)) return;

    // The host's 30s cap covers this whole handler, sync included.
    const deadline = Date.now() + HANDLER_BUDGET_MS;
    const branch = ctx.sessionManager.getBranch();
    const result = await sync.syncBranch(branch);
    logger.log("turn_end", { added: result.added, tokens: result.tokens });
    if (result.permanentFailures > 0) takeover.recordCaptureGap();
    // The branch is what the boundary is frozen against and re-confirmed on, so
    // takeover needs it, not just the token delta.
    await takeover.onTurnSynced(result.tokens, () => ctx.sessionManager.getBranch(), { deadline });
    updateStatus(ctx, connected, result.added, sync.sessionId, config, takeover.state, toolsReady);
  });

  // --- session_before_compact ---
  pi.on("session_before_compact", async (event, ctx) => {
    if (!connected || bypassed || !isCaptureEnabled(config)) return;

    if (config.takeoverEnabled) {
      const deadline = Date.now() + HANDLER_BUDGET_MS;
      const prep = (event as any)?.preparation ?? {};
      // Native compaction syncs the latest branch, archives all captured
      // history and reuses pi's own firstKeptEntryId, so hand it the branch.
      return await takeover.handleBeforeCompact({
        firstKeptEntryId: prep.firstKeptEntryId,
        tokensBefore: prep.tokensBefore ?? 0,
        signal: (event as any)?.signal,
      }, () => ctx.sessionManager.getBranch(), { deadline });
    }

    const archiveId = await sync.commit();
    compacted = true;

    // Cache archive overview for rehydration after compaction
    if (config.resumeArchiveInject && archiveId && sync.sessionId) {
      archiveOverview = await fetchArchiveOverview(
        client, sync.sessionId, config,
      );
    }
    // Return nothing → pi proceeds with default compaction
  });

  pi.on("session_compact", async () => {
    historyEvent = "compact";
    compactedInRun = true;
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
    } else if (isCaptureEnabled(config)) {
      // The host awaits this handler before releasing the terminal, so a slow
      // commit must not hold the quit. Cutting the wait short loses nothing:
      // a failed or timed-out commit is written to the on-disk pending queue
      // (sync.commit queues by default) and replayed at the next session start.
      await sync.commit({ timeoutMs: SHUTDOWN_COMMIT_BUDGET_MS });
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
        // Manual commit freezes and confirms the boundary against the current
        // branch, exactly like the automatic path.
        // A `skipped` result archived nothing; it is not a successful commit.
        const skipped = commitResult?.status === "skipped";
        const ok = config.takeoverEnabled
          ? await takeover.commitAndAdvance(() => ctx.sessionManager.getBranch())
          : commitResult !== null && !skipped;
        if (!ok && config.takeoverEnabled && takeover.state.pendingArchive) {
          ctx.ui.notify(
            "OpenViking: committed; the context is trimmed once the archive summary is ready",
            "info",
          );
        } else if (ok) {
          ctx.ui.notify(
            "OpenViking: committed successfully" +
              (commitResult?.trace_id ? ` (trace_id=${commitResult.trace_id})` : ""),
            "info",
          );
        } else {
          // Say why: a bare "commit failed" left users with nothing to act on.
          const reason = config.takeoverEnabled
            ? takeover.lastFailure
            : skipped
              ? describeSkip(String(commitResult?.reason || ""))
              : sync.lastCommitError;
          logger.log("commit", { ok: false, manual: true, reason: reason || "unknown" });
          if (reason.startsWith("nothing")) {
            // Not an error: the server already holds this history.
            ctx.ui.notify(`OpenViking: ${reason}`, "warning");
          } else {
            ctx.ui.notify(`OpenViking: commit failed${reason ? ` — ${reason}` : ""}`, "error");
          }
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
 * Reach the user when sealing stops something. A notification in the TUI,
 * stderr in print and JSON modes, where pi's notifications do nothing.
 */
function tellUser(ctx: any, message: string): void {
  if (ctx?.hasUI) {
    ctx.ui.notify(message, "error");
    return;
  }
  process.stderr.write(`${message}\n`);
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
