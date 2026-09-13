/**
 * Pi OpenViking Extension — EXPERIMENTAL context management
 *
 * A fork of examples/pi-coding-agent-extension whose takeover mode is replaced
 * by agent-driven context windows: the model decides when a window ends, calls
 * `new_context`, and the extension archives the conversation to OpenViking and
 * cuts it out of the next provider request, leaving a frozen window header with
 * the Working Memory and the model's own handoff notes. `history` reads the
 * archived windows back, `get_context_remaining` reports the pressure. Turns
 * still sync to an OpenViking session and recall is still injected into the
 * newest user message.
 *
 * Do not load this together with the openviking extension — they would both
 * register `viking_*` tools and both sync the same OV session. As a backstop
 * this one stands down as soon as it sees a `viking_search` registered from
 * another extension's directory, which is re-checked on every event boundary
 * because that extension registers its tools only after a network round trip.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createLogger } from "./shared/debug-log.mjs";
import { loadConfigFromModuleUrl, type OVConfig } from "./config.js";
import { OVClient } from "./client.js";
import { RecallManager } from "./recall.js";
import { SyncManager } from "./sync.js";
import { buildProfileBlock } from "./shared/profile-inject.mjs";
import { guardVikingUriToolCall } from "./lib/uri-guard-adapter.mjs";
import { registerContextWindowTools, registerTools } from "./tools.js";
import { createContextWindowManager, readPiReserveTokens } from "./context-window.js";
import {
  buildStatusLine,
  messagesFromBranch,
  reminderText,
  REMINDER_CUSTOM_TYPE,
  STATUS_CUSTOM_TYPE,
} from "./lib/context-window-core.mjs";

/** The tool whose presence means the non-experimental extension is loaded. */
const COEXISTENCE_PROBE_TOOL = "viking_search";

/**
 * Static system-prompt guidance (plan §5). Identical for the whole session, so
 * it is appended once per turn without moving any prompt-cache boundary.
 * Unlike Codex we do not ask the model to hide this machinery from the user —
 * the demo is supposed to be visible.
 */
const CONTEXT_WINDOW_GUIDANCE = `<context-window-management>
You manage your own context window in this session. When the conversation grows it is not summarized behind your back: you decide when the current window ends, and OpenViking archives it so you can read it back afterwards.

Three tools do this:
- new_context — archive the current window and continue in a fresh one. OpenViking generates a Working Memory of the archived window, and your next window opens with that Working Memory, your handoff notes and the user's most recent request.
- history — read windows that were already archived: list_windows, list_items, read_item, search_contents. Closing a window loses nothing; it only stops being in front of you.
- get_context_remaining — how much room is left, how long this window has been open, and how long it has been since the user's last message.

Start a new window when:
- a phase of the work is finished and its details no longer matter for what comes next;
- the user switches to an unrelated topic or a different area of the code;
- the user comes back after a long idle gap with something new;
- the status line or get_context_remaining says the window is filling up — act when their advice line asks you to, and never ignore a [context-reminder]. If you wait until the window overflows, the harness compacts the conversation for you and your notes are never written.

Do not start a new window:
- in the middle of an edit sequence you have not verified;
- immediately after a reset;
- to get away from a problem you have not solved — the new window carries the same problem with less information.

Write the notes before you call new_context: the goal, the decisions and why you made them, what is finished, what is in flight, exact file paths and identifiers, and the next steps. Write them for someone who has not seen this conversation, because that is exactly what your next window is. Call new_context alone — tool results from the same batch are discarded together with the old window.

If new_context answers with "Context window NOT reset", nothing was archived and nothing was removed from your context: read the reason it gives, keep working in the current window, and do not call it again until that reason is gone.

In a new window, read the <openviking-context source="context-window"> block first: it carries the Working Memory, your notes and the request that is still pending. Use history for anything it does not cover, and never ask the user to repeat something an archived window already holds.
</context-window-management>`;

const TOOL_LIST_LINE =
  "OpenViking tools: viking_search, viking_read, viking_browse, viking_remember, viking_forget, viking_add_resource. " +
  "Context window tools: new_context, history, get_context_remaining.";

export default async function (pi: ExtensionAPI) {
  // --- Load config ---
  const config = loadConfigFromModuleUrl(import.meta.url);
  if (!config.enabled) return;

  // --- Initialize modules ---
  const client = new OVClient(config);
  const sync = new SyncManager(client, config);
  const recall = new RecallManager(client, config, () => sync.sessionId);
  const logger = createLogger("pi", {
    debug: Boolean(config.debugLogPath),
    debugLogPath: config.debugLogPath,
  });
  const windows = createContextWindowManager({ pi, client, sync, config, logger });

  // Session state
  let connected = false;
  let bypassed = false;
  let profileBlock = "";
  let toolsRegistered = false;
  /** The offline half of start() — session id, window restore, tools. */
  let offlineInitDone = false;
  let started = false;
  let startPromise: Promise<void> | null = null;
  /**
   * The message list the `context` hook last produced. `get_context_remaining`
   * needs the *transformed* list: the untransformed one still holds the closed
   * windows and would report the session, not the window.
   */
  let lastTransformed: any[] | undefined;
  const reserveTokens = readPiReserveTokens(process.cwd());

  /**
   * The message list a status readout is built from, with the window metrics
   * refreshed against it.
   *
   * `turnsInWindow`, the window token estimate and the last-user timestamps are
   * only recorded by `transformContext`, which runs in the `context` hook — and
   * that hook has not fired yet when the first prompt of a process reaches
   * `before_agent_start`. After `pi -c` the line would then describe a restored
   * window as "0 turns" with no idle gap, however many turns it already had in
   * the previous process. Rebuilding the list from the branch fixes that, and
   * keeps the count current on later prompts too: the branch already holds the
   * assistant message the previous turn's last `context` hook could not see.
   *
   * The branch is what pi builds the next provider request from anyway, so the
   * cut here only repeats the one the `context` hook will make. `observeMessages`
   * rather than `transformContext`: it touches the in-memory metrics and nothing
   * else — no persist, no network, and no right to release the boundary from a
   * list pi may not have finished writing.
   */
  const statusMessages = (ctx: any): any[] | undefined => {
    let branch: any[] | undefined;
    try {
      branch = ctx?.sessionManager?.getBranch?.();
    } catch {
      return lastTransformed;
    }
    if (!Array.isArray(branch) || branch.length === 0) return lastTransformed;
    try {
      return windows.observeMessages(messagesFromBranch(branch) as any[]);
    } catch (error) {
      logger.logError("status-metrics", error);
      return lastTransformed;
    }
  };

  /** This extension's own directory, to tell our tools from a peer's. */
  const ownDir = dirname(fileURLToPath(import.meta.url));

  /**
   * True when a *different* extension owns `viking_search`.
   *
   * A one-shot probe at startup cannot work: the other extension registers its
   * tools only after an awaited health check, so at the moment our own start()
   * begins the tool is provably not there yet. So the probe compares the
   * registering extension's `sourceInfo.path` with ours and is re-run on every
   * event boundary — pi's tool registry is keyed by name, so once the peer
   * registers, `viking_search` carries its path and not ours.
   */
  const peerOwnsToolSurface = (): boolean => {
    try {
      const tools = (pi as any).getAllTools?.();
      if (!Array.isArray(tools)) return false;
      for (const tool of tools) {
        if (tool?.name !== COEXISTENCE_PROBE_TOOL) continue;
        const path = String(tool?.sourceInfo?.path ?? tool?.sourceInfo?.baseDir ?? "");
        // No sourceInfo (an older pi, or a synthetic tool): fall back to "any
        // viking_search we did not register ourselves is a peer's".
        if (!path) return !toolsRegistered;
        if (path !== ownDir && !path.startsWith(ownDir + "/")) return true;
      }
      return false;
    } catch {
      return false;
    }
  };

  let peerWarned = false;

  /**
   * Stand down when the other OpenViking extension turns up. Two writers on one
   * OV session double every captured message and race on the archive boundary.
   *
   * Only safe while nothing irreversible has happened: once a window is open,
   * the model's context depends on our cut, and dropping it would hand back a
   * conversation we already told the model was archived. In that case the
   * conflict is reported and this extension keeps going.
   */
  const standDownIfPeerActive = (ctx: any): boolean => {
    if (bypassed) return true;
    if (!peerOwnsToolSurface()) return false;
    if (windows.armed || windows.state.windowIndex > 1 || sync.syncedCount > 0) {
      if (!peerWarned) {
        peerWarned = true;
        ctx?.ui?.notify?.(
          "OpenViking: another OpenViking extension is active and both are writing this session — disable one",
          "warning",
        );
        logger.log("coexistence", { standDown: false, reason: "window already open" });
      }
      return false;
    }
    bypassed = true;
    started = true;
    connected = false;
    if (!peerWarned) {
      peerWarned = true;
      ctx?.ui?.notify?.(
        "OpenViking experimental extension disabled: another OpenViking extension is already active",
        "warning",
      );
    }
    logger.log("coexistence", { standDown: true });
    return true;
  };

  // ================================================================
  // Event Handlers
  // ================================================================

  const start = async (ctx: any): Promise<void> => {
    if (started) return;
    if (startPromise) return startPromise;

    startPromise = (async () => {
      // Coexistence guard, before any registration. It runs again on every
      // event boundary because the peer registers its tools asynchronously.
      if (standDownIfPeerActive(ctx)) return;

      // Bypass check
      const cwd = process.cwd();
      for (const pattern of config.bypassPatterns) {
        if (matchBypass(cwd, pattern)) {
          bypassed = true;
          started = true;
          return;
        }
      }

      // Everything up to the health check is offline and must run even when OV
      // is down: `pi -c` otherwise replays a whole archived window back to the
      // model together with the tool result that claims it was archived.
      const piSessionId = ctx.sessionManager.getSessionId();
      if (!offlineInitDone) {
        // Derives "pi-<piSessionId>" locally; no request, so it cannot fail.
        await sync.ensureSession(piSessionId);

        const branch = ctx.sessionManager.getBranch?.() ?? [];
        const restored = windows.restore(branch);
        sync.restoreWatermark(Math.max(restored.syncedEntryCount ?? 0, sync.syncedCount));

        registerTools(pi, client, sync);
        registerContextWindowTools(pi, client, sync, windows, config, {
          getMessages: () => lastTransformed,
          reserveTokens,
        });
        toolsRegistered = true;
        offlineInitDone = true;
      }

      // Health check
      connected = await client.health();
      if (!connected) {
        if (config.logLevel === "info") {
          ctx.ui.notify("OpenViking: server not reachable", "warning");
        }
        return;
      }

      await sync.replayPending();

      // Profile injection
      profileBlock = await buildSessionProfileBlock(client, config);

      updateStatus(ctx, connected, windows, sync.sessionId);

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
    // session_start doesn't fire for pi -c continuations.
    await start(ctx);
    // Re-checked here because the peer extension registers its tools from its
    // own awaited start(), which may only have finished after ours began.
    if (standDownIfPeerActive(ctx) || bypassed) return;

    // Queue recall for the context hook. Pi renders the user message before
    // that hook, so recall latency does not delay the message appearing.
    if (connected) recall.queueSearch(event.prompt);

    // Compose system prompt additions. The window guidance and the tool list
    // describe tools that are registered whether or not OV answers, so they go
    // in either way; only the profile block depends on the server.
    const parts: string[] = [];
    if (profileBlock) parts.push(profileBlock);
    parts.push(CONTEXT_WINDOW_GUIDANCE);
    parts.push(TOOL_LIST_LINE);

    const result: any = { systemPrompt: event.systemPrompt + "\n\n" + parts.join("\n\n") };

    // One status line per user prompt (not per sampling). It is derived from
    // local state only, so an unreachable server still gets the agent its
    // window id, its token pressure and the idle gap.
    if (config.contextWindow.statusEveryTurn) {
      const snapshot = windows.statusSnapshot({
        usage: (ctx as any).getContextUsage?.(),
        now: Date.now(),
        messages: statusMessages(ctx),
        reserveTokens,
      });
      result.message = {
        customType: STATUS_CUSTOM_TYPE,
        content: buildStatusLine({
          windowId: snapshot.windowId,
          turnsInWindow: snapshot.turnsInWindow,
          usedTokens: snapshot.usedTokens,
          contextWindow: snapshot.contextWindow,
          estimated: snapshot.estimated,
          sinceLastUserMs: snapshot.sinceLastUserMs,
          idleGapMs: snapshot.idleGapMs,
          idleGapMinutes: config.contextWindow.idleGapMinutes,
        }),
        display: true,
      };
    }

    return result;
  });

  // --- context ---
  pi.on("context", async (event, ctx) => {
    if (bypassed) return;

    // The window cut runs first and does not depend on the server: the anchor
    // is in the branch, and dropping it because OV is down would hand the model
    // an archived window back together with the tool result that archived it.
    const transformed = windows.transformContext(event.messages as any[]);
    lastTransformed = transformed;
    if (!connected) return { messages: transformed };

    // Everything after the cut is wrapped: pi keeps the *untransformed* list
    // when a context handler throws (runner.js emits the error and moves on),
    // so an exception in recall would undo the window cut and hand the model an
    // archived window back together with the tool result that archived it.
    try {
      // Keep recall synchronous with the provider request so the current prompt
      // still receives current-query memory, without blocking user-message UI.
      await recall.searchPending();
      const messages = recall.injectRecall(transformed);
      lastTransformed = messages;
      return { messages };
    } catch (error) {
      logger.logError("recall", error);
      return { messages: transformed };
    }
  });

  // --- tool_call ---
  pi.on("tool_call", async (event, _ctx) => {
    const decision = guardVikingUriToolCall(event);
    if (!decision) return;
    return decision;
  });

  // --- turn_end ---
  pi.on("turn_end", async (event, ctx) => {
    if (standDownIfPeerActive(ctx) || bypassed) return;

    if (connected && config.syncTurns) {
      const branch = ctx.sessionManager.getBranch();
      const result = await sync.syncBranch(branch);
      logger.log("turn_end", { added: result.added, tokens: result.tokens });
    }

    // Re-arm the reminders only for an assistant response that belongs to the
    // *current* window and actually produced usage. The turn a reset happens in
    // ends here too, and its assistant message is the pre-reset one: pi still
    // reports the whole session as used (the virtual cut never touches its
    // message state), so clearing the guard now would fire a "you are at 95%,
    // call new_context" reminder one line after the window opened — and burn
    // the level for the rest of the window. An undated message counts as
    // current, the same direction the core takes: pi stamps every real message.
    //
    // A provider error or an Esc ends the turn with a message pi stamps *now*
    // but whose usage it refuses to trust (getContextUsage skips assistants
    // with stopReason "error"/"aborted"), so such a turn must not re-arm
    // either: the reported number would still be the closed window's.
    const turnMessage = (event as any).message;
    const stopReason = String(turnMessage?.stopReason ?? "");
    const turnAssistantAt = Number(turnMessage?.timestamp);
    const lastResetAt = Number(windows.state.lastResetAt) || 0;
    const usableResponse = stopReason !== "aborted" && stopReason !== "error";
    if (usableResponse && (!Number.isFinite(turnAssistantAt) || turnAssistantAt >= lastResetAt)) {
      windows.observeAssistantResponse();
    }

    const usage = (ctx as any).getContextUsage?.();
    const level = windows.dueReminder({
      usage,
      contextWindow: usage?.contextWindow ?? 0,
      reserveTokens,
    });
    if (level) {
      const { used } = windows.usedTokens(usage);
      try {
        (pi as any).sendMessage?.(
          {
            customType: REMINDER_CUSTOM_TYPE,
            content: reminderText(level, {
              windowId: windows.windowId,
              usedTokens: used,
              contextWindow: usage?.contextWindow ?? 0,
            }),
            display: true,
          },
          // Mid-batch the reminder has to jump the queue; at the end of a turn
          // the next sampling picks it up on its own.
          { deliverAs: event.toolResults?.length ? "steer" : "nextTurn" },
        );
      } catch (error) {
        logger.logError("reminder", error);
      }
    }

    // One non-blocking retry per turn for a window that opened before its
    // Working Memory was generated.
    if (connected) await windows.refreshPendingOverview();

    updateStatus(ctx, connected, windows, sync.sessionId);
  });

  // --- session_before_compact ---
  pi.on("session_before_compact", async (event, ctx) => {
    if (bypassed) return;
    // Called even with OpenViking down: the core's recent-reset guard needs no
    // network, and it is what stops a stale usage estimate right after a reset
    // from compacting a window that just opened. The core refuses to commit on
    // its own when `io.connected()` is false, so an offline pi still falls back
    // to its own summarizer.
    //
    // Takes over pi's compaction: archive to OpenViking and hand pi our window
    // header as the summary. Returns undefined on any failure, which leaves
    // pi's own summarizer in charge.
    return await windows.handleBeforeCompact({
      preparation: (event as any).preparation,
      branchEntries: (event as any).branchEntries ?? ctx.sessionManager.getBranch?.() ?? [],
    });
  });

  // --- session_compact ---
  pi.on("session_compact", async (event, _ctx) => {
    if (bypassed) return;
    // A compaction we did not produce already cut the history natively, so the
    // virtual anchor is stale and the header no longer describes what is in
    // context.
    if (!(event as any).fromExtension) windows.absorbExternalCompaction("pi compaction");
  });

  // --- session_shutdown ---
  pi.on("session_shutdown", async (_event, _ctx) => {
    if (bypassed) return;

    // Persist the window state with the final watermark. No forced commit:
    // archive boundaries belong to the reset path, and a commit on exit would
    // split a window the next process still owns.
    await windows.shutdown();
    await sync.shutdown();
  });

  // --- agent_end ---
  pi.on("agent_end", async (_event, _ctx) => {
    recall.invalidate();
  });

  // ================================================================
  // Commands
  // ================================================================

  pi.registerCommand("viking", {
    description:
      "OpenViking status and manual operations: 'commit' to force a sync, 'window' for the context window status.",
    handler: async (args, ctx) => {
      const command = args?.trim() ?? "";

      if (command === "window") {
        const snapshot = windows.statusSnapshot({
          usage: (ctx as any).getContextUsage?.(),
          now: Date.now(),
          messages: statusMessages(ctx),
          reserveTokens,
        });
        ctx.ui.notify(
          buildStatusLine({
            windowId: snapshot.windowId,
            turnsInWindow: snapshot.turnsInWindow,
            usedTokens: snapshot.usedTokens,
            contextWindow: snapshot.contextWindow,
            estimated: snapshot.estimated,
            sinceLastUserMs: snapshot.sinceLastUserMs,
            idleGapMs: snapshot.idleGapMs,
            idleGapMinutes: config.contextWindow.idleGapMinutes,
          }) +
            `\narchive: ${snapshot.archiveId || "none"}` +
            ` · working memory ${snapshot.overviewReady ? "ready" : "not ready"}` +
            ` · ${snapshot.advice}`,
          "info",
        );
        return;
      }

      if (!connected) {
        ctx.ui.notify("OpenViking: not connected", "warning");
        return;
      }

      if (command === "commit") {
        await sync.shutdown();
        const commitResult = await sync.commit();
        if (commitResult !== null) {
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
      ctx.ui.notify(
        `OpenViking: ${connected ? "connected" : "disconnected"} | session: ${sid.slice(0, 12)}... | window: ${windows.windowId}`,
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

/** Status bar: connection, current window, pressure, latest archive, session. */
function updateStatus(
  ctx: any,
  connected: boolean,
  windows: { statusSnapshot: (opts?: any) => any },
  sessionId: string | null,
): void {
  const setter = ctx?.ui?.setStatus;
  if (typeof setter !== "function") return;
  let snapshot: any;
  try {
    snapshot = windows.statusSnapshot({ usage: ctx?.getContextUsage?.(), now: Date.now() });
  } catch {
    return;
  }
  const archive = snapshot.archiveId ? snapshot.archiveId.replace(/^archive_/, "a") : "a—";
  const status =
    `${connected ? "OV ✓" : "OV ✗"} · ${snapshot.windowId} · ${snapshot.percent}% · ${archive}` +
    ` · ${sessionId ? sessionId.slice(0, 12) : "none"}`;
  try {
    setter("openviking", status);
  } catch {
    // Best effort; pi API shape may vary across fast-moving versions.
  }
}
