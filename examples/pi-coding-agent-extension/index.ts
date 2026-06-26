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
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { dirname } from "node:path";
import { readFileSync, existsSync } from "node:fs";
import { loadConfig, type OVConfig } from "./config.js";
import { OVClient } from "./client.js";
import { RecallManager } from "./recall.js";
import { SyncManager, estimateTokens } from "./sync.js";
import { IndexBuilder } from "./index-builder.js";
import { registerTools } from "./tools.js";

export default async function (pi: ExtensionAPI) {
  // --- Load config ---
  const config = loadConfig(dirname(new URL(import.meta.url).pathname));
  if (!config.enabled) return;

  // Env overrides
  if (process.env.OPENVIKING_URL) config.endpoint = process.env.OPENVIKING_URL;
  if (process.env.OPENVIKING_API_KEY) config.apiKey = process.env.OPENVIKING_API_KEY;
  if (process.env.OPENVIKING_ACCOUNT) config.account = process.env.OPENVIKING_ACCOUNT;
  if (process.env.OPENVIKING_USER) config.user = process.env.OPENVIKING_USER;
  if (process.env.OPENVIKING_AGENT_ID) config.agentId = process.env.OPENVIKING_AGENT_ID;

  // --- Initialize modules ---
  const client = new OVClient(config);
  const recall = new RecallManager(client, config);
  const sync = new SyncManager(client, config);
  const indexBuilder = new IndexBuilder(client, config);

  // Session state
  let connected = false;
  let bypassed = false;
  let profileBlock = "";
  let archiveOverview = "";
  let toolsRegistered = false;

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

    // Profile injection
    profileBlock = await buildProfileBlock(client, config);

    // Resume rehydration — fetch archive overview if session was previously committed
    if (sync.sessionId) {
      archiveOverview = await fetchArchiveOverview(client, sync.sessionId, config);
    }

    // Build memory index
    await indexBuilder.buildIndex();

    // Register tools (also re-registered in before_agent_start for pi -c continuations)
    registerTools(pi, client, sync);
    toolsRegistered = true;

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
    if (archiveOverview) parts.push(archiveOverview);

    const idx = indexBuilder.getIndex();
    if (idx) parts.push(idx);

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

  // --- turn_end ---
  pi.on("turn_end", async (event, ctx) => {
    if (!connected || bypassed || !config.syncTurns) return;

    // Extract user message from session entries
    const branch = ctx.sessionManager.getBranch();
    let userText = "";
    for (let i = branch.length - 1; i >= 0; i--) {
      const entry = branch[i];
      if (entry.type === "message" && (entry as any).message?.role === "user") {
        const msg = (entry as any).message;
        userText = typeof msg.content === "string"
          ? msg.content
          : Array.isArray(msg.content)
            ? msg.content
                .filter((b: any) => b.type === "text")
                .map((b: any) => b.text)
                .join("")
            : "";
        break;
      }
    }

    // Extract assistant text
    const assistantMsg = event.message as any;
    let assistantText = "";
    const toolLines: string[] = [];
    const toolNames: string[] = [];

    if (assistantMsg?.content && Array.isArray(assistantMsg.content)) {
      for (const block of assistantMsg.content) {
        if (block.type === "text") {
          assistantText += block.text + "\n";
        } else if (block.type === "toolCall") {
          toolNames.push(block.name);
          toolLines.push(
            `[tool: ${block.name}]\n${JSON.stringify(block.arguments)}`,
          );
        }
      }
    }

    // Add tool summary line
    if (toolNames.length > 0) {
      assistantText = `[assistant used tools: ${toolNames.join(", ")}]\n` + assistantText;
    }

    await sync.syncTurn(
      userText, assistantText, toolLines, event.turnIndex,
    );
  });

  // --- session_before_compact ---
  pi.on("session_before_compact", async (_event, _ctx) => {
    if (!connected || bypassed) return;

    // Flush write queue + synchronous commit
    await sync.shutdown();
    const archiveId = await sync.commit(true);

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

    // Mirror MEMORY.md
    if (config.mirrorMemoryWrites && sync.sessionId) {
      const memoryPath = `${ctx.cwd}/.memory/MEMORY.md`;
      if (existsSync(memoryPath)) {
        try {
          const content = readFileSync(memoryPath, "utf8");
          if (content.trim()) {
            await client.addMessage(
              sync.sessionId, "user",
              `[Memory mirror]\n${content.slice(0, 50000)}`,
            );
          }
        } catch {
          // Best effort
        }
      }
    }

    // Final commit
    if (config.commitOnShutdown) {
      await sync.commit(true);
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
        const result = await sync.commit(true);
        if (result) {
          await indexBuilder.buildIndex();
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
async function buildProfileBlock(
  client: OVClient, config: OVConfig,
): Promise<string> {
  try {
    const memUri = await client.resolveTargetUri("viking://user/memories");
    const entries = await client.ls(memUri);

    // Look for profile.md
    const profileEntry = entries.find(e => e.name === "profile.md");
    let profileText = "";
    if (profileEntry) {
      const content = await client.readContent(`${memUri}/profile.md`);
      if (content) {
        // Profile elision: keep head (8 lines) + tail (fits budget)
        const lines = content.split("\n");
        if (lines.length > 20) {
          const head = lines.slice(0, 8).join("\n");
          const tailBudget = config.profileBudget - 200;
          const tail = lines.slice(-Math.max(10, tailBudget)).join("\n");
          profileText = head + "\n...\n" + tail;
        } else {
          profileText = content;
        }
      }
    }

    // List preferences and entities directories
    const prefUri = `${memUri}/preferences`;
    const entUri = `${memUri}/entities`;
    const [prefs, ents] = await Promise.all([
      client.ls(prefUri),
      client.ls(entUri),
    ]);

    const sections: string[] = ["<openviking-context>"];
    if (profileText) {
      sections.push(`<user-profile>${profileText}</user-profile>`);
    }
    if (prefs.length > 0 || ents.length > 0) {
      sections.push("<available-memories>");
      if (prefs.length > 0) {
        sections.push(`  ${prefUri}/ (${prefs.length} entries)`);
      }
      if (ents.length > 0) {
        sections.push(`  ${entUri}/ (${ents.length} entries)`);
      }
      sections.push("</available-memories>");
    }
    sections.push("</openviking-context>");

    const block = sections.join("\n");
    // Budget check
    const tokens = estimateTokens(block);
    if (tokens > config.profileBudget) {
      return block.slice(0, config.profileBudget * 3);
    }
    return block;
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

    const result = `[Session History Summary]\n${ctx.latest_archive_overview}`;
    const tokens = estimateTokens(result);
    if (tokens > config.resumeContextBudget) {
      return result.slice(0, config.resumeContextBudget * 3);
    }
    return result;
  } catch {
    return "";
  }
}
