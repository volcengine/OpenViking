/**
 * Pi OpenViking Extension
 *
 * Integrates pi with an OpenViking context database for persistent,
 * cross-session memory. pi's callbacks build a payload and run it through the
 * shared flow (`shared/flow/`, host description in `host.mjs`): capture, commit,
 * recall and the session opening are decided there. What only pi has stays
 * here: compaction takeover, the MCP tool bridge, sealing and the placement of
 * recalled context in each provider request.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { isCaptureEnabled } from "./shared/capture-utils.mjs";
import { createLogger } from "./shared/debug-log.mjs";
import { runStage, type Outcome } from "./shared/flow/index.mjs";
import { EXTENSION_VERSION, loadConfig, buildBridgeProxyConfig, type OVConfig } from "./config.js";
import { OVClient } from "./client.js";
import piHost, { ovSessionIdFor } from "./host.mjs";
import { RecallManager } from "./recall.js";
import { RecallLedger } from "./lib/recall-ledger.mjs";
import { isBypassed } from "./shared/session-model.mjs";
import { guardVikingUriToolCall, noticeVikingUriToolResult } from "./lib/uri-guard-adapter.mjs";
import { createMcpBridge, DEFAULT_HANDSHAKE_BUDGET_MS } from "./lib/mcp-bridge.mjs";
import { registerMcpTools } from "./tools.js";
import { createTakeoverManager } from "./takeover.js";
import { HANDLER_BUDGET_MS, estimatePayloadTokens, type SyncBranchResult } from "./lib/takeover-core.mjs";
import { createSealHandlers, createSealPost } from "./lib/seal-adapter.mjs";
import { featureStatus, healthVersion } from "./shared/server-features.mjs";

/** This extension's directory, published for the experimental fork's probe. */
const EXTENSION_DIR = dirname(fileURLToPath(import.meta.url));

/** The turns a capture decoded, for takeover's token estimate. */
interface Probe {
  turns?: Array<{ payload?: unknown }>;
}

const host = {
  ...piHost,
  decode(input: any) {
    const decoded = piHost.decode(input);
    if (input.payload?.probe) input.payload.probe.turns = decoded.turns;
    return decoded;
  },
};

export default async function (pi: ExtensionAPI) {
  // --- Load config ---
  const cwd = process.cwd();
  const config = loadConfig(cwd);
  if (!config.enabled) return;
  // Shared key (`shared/config-schema.mjs`), already honoured by opencode: the
  // MCP tool surface is off, while recall, sync and takeover carry on.
  const mcpEnabled = (config as any).mcpEnabled !== false;

  // What the flow is run with. Takeover owns the commits and the context after
  // a compaction when it is on (a 0 threshold turns the server's auto-commit off); without tools the session-history note would
  // name a tool the model does not have.
  const flowCfg = {
    ...config,
    ...(config.takeoverEnabled
      ? { resumeArchiveInject: false, commitTokenThreshold: 0, commitIdleSeconds: 0 }
      : {}),
    ...(mcpEnabled ? {} : { sessionHistoryPointer: false }),
  };

  // Installed as an extension rather than a package, so pi does not scan
  // skills/ on its own. The skills teach the MCP tools, so they follow them.
  if (mcpEnabled) {
    pi.on("resources_discover", async () => ({ skillPaths: [join(EXTENSION_DIR, "skills")] }));
  }

  // --- Initialize modules ---
  const client = new OVClient(config);
  // The ledger keeps request prefixes byte-stable for provider prompt caches
  // (#4137); it is per pi session and opened once the id is known.
  const recall = new RecallManager(config.recallLedger ? new RecallLedger() : null);
  const logger = createLogger("pi", {
    debug: Boolean(config.debugLogPath),
    debugLogPath: config.debugLogPath,
  });

  // Session state
  let bypassed = false;
  let piSessionId = "";
  let ovSessionId: string | null = null;
  let opening = "";
  let pendingPrompt: string | null = null;
  let toolsReady = false;
  let toolsRegistered = false;
  let closed = false;
  const marker = { dir: EXTENSION_DIR };
  const toolNames: string[] = [];
  let toolFailureNotified = false;
  let compactedInRun = false;
  let startReason = "";
  let started = false;
  let startPromise: Promise<void> | null = null;
  let watermark = 0;
  let dropped = 0;

  const branchOf = (ctx: any): any[] =>
    typeof ctx?.sessionManager?.getBranch === "function" ? ctx.sessionManager.getBranch() : [];

  const run = (ctx: any, event: string, extra: Record<string, unknown> = {}, outcome: Outcome = {}) => runStage(
    host,
    event,
    { sessionId: piSessionId || ctx?.sessionManager?.getSessionId?.(), cwd, ...extra },
    { cfg: flowCfg, outcome },
  );

  /** Capture the branch through the flow, as takeover reads the result. */
  const capture = async (branch: any[], ctx: any = null): Promise<SyncBranchResult> => {
    const probe: Probe = {};
    const outcome: Outcome = {};
    await run(ctx, "turn_end", { branch, probe }, outcome);
    const turns = probe.turns ?? [];
    const result = outcome.capture;
    if (!probe.turns) return { added: 0, tokens: 0, allDelivered: false, queued: 0, permanentFailures: 0 };
    const held = result?.held ?? 0;
    const accepted = result ? turns.length - held : turns.length;
    if (held === 0) watermark = branch.length;
    dropped += result?.failed ?? 0;
    let tokens = 0;
    for (const turn of turns.slice(0, accepted)) tokens += estimatePayloadTokens(turn.payload);
    return {
      added: result ? result.sent + result.queued : 0,
      tokens,
      allDelivered: !result || (result.queued === 0 && result.failed === 0 && held === 0),
      queued: result?.queued ?? 0,
      permanentFailures: result?.failed ?? 0,
    };
  };

  const takeover = createTakeoverManager({
    pi, client, config,
    capture: { syncBranch: (branch) => capture(branch), watermark: () => watermark, dropped: () => dropped },
    sessionId: () => ovSessionId,
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
      if (isBypassed(config, { cwd: process.cwd() })) {
        bypassed = true;
        started = true;
        return;
      }

      // What the experimental fork probes to stand down: this extension owns
      // the OpenViking session, with or without tools.
      (globalThis as any).__OPENVIKING_PI_EXTENSION__ = marker;

      // Discover tools alongside the session opening; failure leaves the rest working.
      const handshake = bridge ? bridge.connect(DEFAULT_HANDSHAKE_BUDGET_MS) : null;

      piSessionId = ctx.sessionManager.getSessionId();
      ovSessionId = ovSessionIdFor(piSessionId);
      recall.openLedger(piSessionId);

      const branch = branchOf(ctx);
      if (config.takeoverEnabled) takeover.restore(branch);
      const output = await run(ctx, "session_start", {
        branch,
        reason: startReason,
        forked: Boolean(ctx.sessionManager.getHeader?.()?.parentSession),
      });
      if (closed) return;
      opening = output.context || "";

      // Join the handshake and publish whatever the server listed (also needed
      // for pi -c continuations, which never fire session_start).
      if (handshake) adoptBridgeTools(ctx, await handshake);
      if (closed) return;
      updateStatus(ctx, 0, ovSessionId, config, takeover.state, toolsReady);

      started = true;
      if (config.logLevel === "info") {
        ctx.ui.notify(`OpenViking session ${ovSessionId}`, "info");
      }
    })().finally(() => {
      startPromise = null;
    });

    return startPromise;
  };

  // --- session_start ---
  pi.on("session_start", async (event, ctx) => {
    startReason = String((event as any)?.reason || "");
    // Fire-and-forget: blocking session_start on the session opening delays
    // every pi startup. start() is memoized via startPromise, so
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
    // clears its in-flight promise as soon as a failed attempt settles.
    const wasStarted = started;
    // From here on the note rides in this run's system prompt.
    compactedInRun = false;

    // session_start doesn't fire for pi -c continuations.
    await start(ctx);

    // Retry the handshake when the session is live but toolless — a server
    // started after pi, or a credential fixed mid-session. `bridge` is null
    // exactly when `mcpEnabled` is false; a bypassed directory never gets
    // here. `connect()` re-handshakes only after a failure; after a success it
    // hands back the state it already has.
    if (bridge && wasStarted && started && !bypassed && !closed && !toolsRegistered) {
      adoptBridgeTools(ctx, await bridge.connect(DEFAULT_HANDSHAKE_BUDGET_MS));
      updateStatus(ctx, 0, ovSessionId, config, takeover.state, toolsReady);
    }

    if (!started || bypassed || closed) return;

    // A summary that finished since the last turn — or in the last `pi -p`
    // process — trims this prompt's request already.
    if (config.takeoverEnabled) {
      await takeover.resumePending(() => ctx.sessionManager.getBranch(), { deadline });
    }

    // Recalled in the context hook: pi renders the user message before it, so
    // recall latency does not delay the message appearing.
    pendingPrompt = event.prompt;

    // pi rebuilds the system prompt each turn, so the opening (profile, and
    // after a compaction or resume the session-history note) is added every turn.
    const parts: string[] = [];
    if (opening) parts.push(opening);
    // Generated from what actually registered, so it can never name a tool the
    // server does not have.
    if (toolNames.length) {
      parts.push(`OpenViking tools (use these for viking:// URIs): ${toolNames.join(", ")}.`);
    }
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
    if (!started || bypassed) return;

    // Recall stays synchronous with the provider request so the current prompt
    // still receives current-query memory; later requests of the run reuse it.
    if (pendingPrompt !== null) {
      const prompt = pendingPrompt;
      pendingPrompt = null;
      const output = await run(ctx, "context", { prompt });
      recall.setBlock(output.context || null);
    }

    // The entry IDs are an optional optimization for replaying the recall
    // ledger. Compatible hosts may omit buildContextEntries(), so fail closed
    // to nullable IDs rather than guessing from another SessionManager API.
    const sessionManager = ctx.sessionManager;
    const entries = typeof sessionManager?.buildContextEntries === "function"
      ? sessionManager.buildContextEntries()
      : [];
    const userEntryIds = entries
      .filter((entry: any) => entry?.type === "message" && entry.message?.role === "user")
      .map((entry: any): string | undefined => typeof entry.id === "string" ? entry.id : undefined);
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
      ? takeover.transformContext(event.messages as any, branchOf(ctx))
      : event.messages;
    const messages = recall.injectRecall(
      afterTakeover,
      (message) => messageIds.get(message) ?? null,
    );
    // A compaction inside a run keeps that run's system prompt, so the rest of
    // the run gets the session-history note right after the summary.
    const note = compactedInRun ? historyNote(opening) : "";
    if (note) {
      const at = messages.findIndex((message: any) => message?.role === "compactionSummary") + 1;
      messages.splice(at, 0, { role: "user", content: [{ type: "text", text: note }], timestamp: Date.now() });
    }
    return { messages };
  });

  // --- tool_call ---
  // The URI guard first, so it judges what the model wrote; then sealing puts
  // the real values in for the references, in place.
  pi.on("tool_call", async (event, ctx) => {
    const decision = guardVikingUriToolCall(event, piSessionId || ctx?.sessionManager?.getSessionId?.() || "");
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
  pi.on("turn_end", async (_event, ctx) => {
    if (!started || bypassed || closed || !isCaptureEnabled(config)) return;

    // The host's 30s cap covers this whole handler, capture included.
    const deadline = Date.now() + HANDLER_BUDGET_MS;
    const result = await capture(branchOf(ctx), ctx);
    logger.log("turn_end", { added: result.added, tokens: result.tokens });
    if (result.permanentFailures > 0) takeover.recordCaptureGap();
    // The branch is what the boundary is frozen against and re-confirmed on, so
    // takeover needs it, not just the token delta.
    await takeover.onTurnSynced(result.tokens, () => ctx.sessionManager.getBranch(), { deadline });
    updateStatus(ctx, result.added, ovSessionId, config, takeover.state, toolsReady);
  });

  // --- session_before_compact ---
  pi.on("session_before_compact", async (event, ctx) => {
    if (!started || bypassed || !isCaptureEnabled(config)) return;

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

    await run(ctx, "session_before_compact", { branch: branchOf(ctx) });
    // Return nothing → pi proceeds with default compaction
  });

  pi.on("session_compact", async (_event, ctx) => {
    if (!started || bypassed || closed) return;
    compactedInRun = true;
    const output = await run(ctx, "session_compact", { branch: branchOf(ctx) });
    if (output.context) opening = output.context;
  });

  // --- session_shutdown ---
  pi.on("session_shutdown", async (_event, ctx) => {
    closed = true;
    if ((globalThis as any).__OPENVIKING_PI_EXTENSION__ === marker) {
      delete (globalThis as any).__OPENVIKING_PI_EXTENSION__;
    }
    // Release MCP requests without holding up the final commit.
    if (bridge) {
      try {
        await bridge.close();
      } catch (error) {
        logger.logError("session_shutdown", error);
      }
    }

    if (!ovSessionId || bypassed) return;

    if (config.takeoverEnabled) {
      await takeover.shutdown();
    } else {
      // The host awaits this handler before releasing the terminal; what does
      // not finish inside the budget is queued and replayed at the next start.
      await run(ctx, "session_shutdown", { branch: branchOf(ctx) });
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
      if (!started || bypassed) {
        ctx.ui.notify(`OpenViking: ${bypassed ? "bypassed in this directory" : "not started"}`, "warning");
        return;
      }

      if (args?.trim() === "commit") {
        if (config.takeoverEnabled) {
          // Manual commit freezes and confirms the boundary against the current
          // branch, exactly like the automatic path.
          const ok = await takeover.commitAndAdvance(() => ctx.sessionManager.getBranch());
          if (ok) {
            ctx.ui.notify("OpenViking: committed successfully", "info");
          } else if (takeover.state.pendingArchive) {
            ctx.ui.notify(
              "OpenViking: committed; the context is trimmed once the archive summary is ready",
              "info",
            );
          } else {
            // Say why: a bare "commit failed" left users with nothing to act on.
            const reason = takeover.lastFailure;
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
        const outcome: Outcome = {};
        await run(ctx, "viking_commit", { branch: branchOf(ctx) }, outcome);
        if (outcome.committed) {
          ctx.ui.notify("OpenViking: committed successfully", "info");
        } else {
          ctx.ui.notify(
            "OpenViking: nothing new to commit, or the commit is queued until the server answers",
            "warning",
          );
        }
        return;
      }

      // Status
      const sid = ovSessionId ?? "none";
      const t = takeover.state;
      const takeoverInfo = config.takeoverEnabled
        ? ` | takeover: ${t.coveredUserTurns}/${t.lastSeenUserTurns} turns archived, ~${t.pendingTokens} tokens pending`
        : "";
      ctx.ui.notify(
        `OpenViking: session ${sid.slice(0, 12)}...${takeoverInfo} | ${toolsStatusLine()}`,
        "info",
      );
    },
  });
}

// ================================================================
// Helper Functions
// ================================================================

/** The session-history note at the end of an opening, without the profile block before it. */
function historyNote(opening: string): string {
  const end = opening.lastIndexOf("</openviking-context>");
  return (end < 0 ? opening : opening.slice(end + "</openviking-context>".length)).trim();
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
  // Only worth a segment when tools were expected and are missing;
  // `mcpEnabled: false` asked for that and is not a fault.
  const tools = (config as any).mcpEnabled !== false && !toolsReady ? " · tools ✗" : "";
  const status = `OV${tools} · ↩${added}${pending} · ${sessionId ? sessionId.slice(0, 12) : "none"}`;
  try {
    setter("openviking", status);
  } catch {
    // Best effort; pi API shape may vary across fast-moving versions.
  }
}
