import { Type } from "typebox";
import { StringEnum } from "@earendil-works/pi-ai";
import type { OVClient, OVArchiveEntry, OVArchiveMessage } from "./client.js";
import type { OVConfig } from "./config.js";
import type { SyncManager } from "./sync.js";
import type { ContextWindowCore } from "./lib/context-window-core.mjs";
import { formatDuration, formatTokens } from "./lib/context-window-core.mjs";
import { readPiReserveTokens } from "./context-window.js";

export function registerTools(pi: any, client: OVClient, sync?: SyncManager): void {

  // --- viking_search ---
  pi.registerTool({
    name: "viking_search",
    label: "Viking Search",
    description: "Semantic search over the OpenViking knowledge base. Returns ranked results with viking:// URIs and abstracts. Use to recall past decisions, user preferences, or project-specific knowledge not in current context.",
    promptSnippet: "Search OpenViking for past decisions, preferences, and project knowledge",
    promptGuidelines: [
      "Use viking_search when you need information from previous sessions not in MEMORY.md.",
      "Use viking_search before making decisions that might conflict with past decisions.",
    ],
    parameters: Type.Object({
      query: Type.String({ description: "Search query" }),
      scope: Type.Optional(Type.String({
        // The server rejects the `viking://~` home alias with "Invalid scope '~'",
        // so the examples name the two forms it does accept.
        description:
          "Viking URI prefix to scope search, e.g. 'viking://user/<space>/memories/' " +
          "or 'viking://session/<session id>/'",
      })),
      limit: Type.Optional(Type.Number({ description: "Max results (default: 10)" })),
    }),
    async execute(
      _id: string, params: any, _signal: AbortSignal,
      _onUpdate: any, _ctx: any,
    ) {
      if (!client.connected) {
        return { content: [{ type: "text", text: "OpenViking server is not reachable." }] };
      }
      const results = await client.find(params.query, {
        targetUri: params.scope,
        topK: params.limit ?? 10,
      });
      if (results.length === 0) {
        return { content: [{ type: "text", text: "No results found." }] };
      }
      const maxChars = client.cfg.recallMaxContentChars;
      const lines = results.map(r => {
        const abs = r.abstract.length > maxChars
          ? r.abstract.slice(0, maxChars) + "..."
          : r.abstract;
        return `[${r.score.toFixed(2)}] ${r.uri}\n  ${abs}`; }
      );
      return {
        content: [{ type: "text", text: lines.join("\n\n") }],
        details: { results },
      };
    },
  });

  // --- viking_read ---
  pi.registerTool({
    name: "viking_read",
    label: "Viking Read",
    description: "Read content at a viking:// URI. Three detail levels: 'abstract' (~100 tokens), 'overview' (~2k tokens), 'full' (complete). Start with abstract, escalate when needed.",
    promptSnippet: "Read OpenViking content at a viking:// URI with tiered detail levels",
    parameters: Type.Object({
      uri: Type.String({ description: "viking:// URI to read" }),
      level: StringEnum(["abstract", "overview", "full"] as const),
    }),
    async execute(
      _id: string, params: any, _signal: AbortSignal,
      _onUpdate: any, _ctx: any,
    ) {
      if (!client.connected) {
        return { content: [{ type: "text", text: "OpenViking server is not reachable." }] };
      }
      let content: string | null = null;
      switch (params.level) {
        case "abstract": content = await client.abstract(params.uri); break;
        case "overview": content = await client.overview(params.uri); break;
        case "full":     content = await client.readContent(params.uri); break;
      }
      if (!content) {
        return { content: [{ type: "text", text: `No content at ${params.uri}` }] };
      }
      return { content: [{ type: "text", text: content }] };
    },
  });

  // --- viking_browse ---
  pi.registerTool({
    name: "viking_browse",
    label: "Viking Browse",
    description: "Browse the OpenViking knowledge store like a filesystem. List directory contents or get metadata.",
    promptSnippet: "Browse the viking:// directory tree in OpenViking",
    parameters: Type.Object({
      action: StringEnum(["list", "stat"] as const),
      uri: Type.Optional(Type.String({ description: "viking:// URI (default: 'viking://')" })),
    }),
    async execute(
      _id: string, params: any, _signal: AbortSignal,
      _onUpdate: any, _ctx: any,
    ) {
      if (!client.connected) {
        return { content: [{ type: "text", text: "OpenViking server is not reachable." }] };
      }
      const uri = params.uri ?? "viking://";
      if (params.action === "stat") {
        const info = await client.stat(uri);
        if (!info) return { content: [{ type: "text", text: `Not found: ${uri}` }] };
        return { content: [{ type: "text", text: JSON.stringify(info, null, 2) }] };
      }
      // list
      const entries = await client.ls(uri);
      if (entries.length === 0) {
        return { content: [{ type: "text", text: `Empty directory: ${uri}` }] };
      }
      const lines = entries.map(e => `${e.isDir ? "📁" : "📄"} ${e.name}`);
      return { content: [{ type: "text", text: lines.join("\n") }] };
    },
  });

  // --- viking_remember ---
  pi.registerTool({
    name: "viking_remember",
    label: "Viking Remember",
    description: "Store a fact or memory in OpenViking. Stored as a session message and extracted into long-term memory on commit. Use for important information the agent should remember: preferences, decisions, gotchas, lessons learned.",
    promptSnippet: "Store a fact in OpenViking for cross-session persistence",
    promptGuidelines: [
      "Use viking_remember for facts that should survive across sessions but don't belong in MEMORY.md.",
      "Good for: user preferences, architectural decisions, gotchas, environment details.",
    ],
    parameters: Type.Object({
      content: Type.String({ description: "The fact or observation to store" }),
      category: Type.Optional(Type.String({ description: "Category hint: 'preference', 'entity', 'event', 'case', 'pattern'" })),
    }),
    async execute(
      _id: string, params: any, _signal: AbortSignal,
      _onUpdate: any, _ctx: any,
    ) {
      if (!client.connected) {
        return { content: [{ type: "text", text: "OpenViking server is not reachable." }] };
      }
      // Store as a tagged message directly in OV — the extractor picks up [Remember — ...] prefix
      const category = params.category ?? "general";
      const tagged = `[Remember — ${category}] ${params.content}`;

      // Directly add to OV session if available
      let stored = false;
      if (sync?.sessionId) {
        stored = await client.addMessage(sync.sessionId, "user", tagged);
      }

      return {
        content: [{
          type: "text",
          text: stored
            ? `Remembered in OpenViking: "${params.content}" (${category})`
            : `Not stored: OpenViking did not accept the message and nothing was queued. ` +
              `Repeat "${params.content}" in your next handoff notes if it matters.`,
        }],
        details: { stored, category, tagged },
      };
    },
  });

  // --- viking_forget ---
  pi.registerTool({
    name: "viking_forget",
    label: "Viking Forget",
    description: "Delete a memory by URI, or search for a specific memory and remove it. Use to correct outdated or wrong information.",
    promptSnippet: "Delete a memory from OpenViking by URI or query",
    parameters: Type.Object({
      uri: Type.Optional(Type.String({ description: "Exact viking:// URI to delete" })),
      query: Type.Optional(Type.String({ description: "Search query — deletes the strongest match if score > 0.8" })),
    }),
    async execute(
      _id: string, params: any, _signal: AbortSignal,
      _onUpdate: any, _ctx: any,
    ) {
      if (!client.connected) {
        return { content: [{ type: "text", text: "OpenViking server is not reachable." }] };
      }
      if (params.uri) {
        const ok = await client.delete(params.uri);
        return {
          content: [{ type: "text", text: ok ? `Deleted: ${params.uri}` : `Failed to delete: ${params.uri}` }],
        };
      }
      if (params.query) {
        const results = await client.find(params.query, { topK: 1 });
        if (results.length > 0 && results[0].score > 0.8) {
          const ok = await client.delete(results[0].uri);
          return {
            content: [{ type: "text", text: ok ? `Deleted: ${results[0].uri}` : `Failed: ${results[0].uri}` }],
          };
        }
        return { content: [{ type: "text", text: "No strong match found (score > 0.8 required)." }] };
      }
      return { content: [{ type: "text", text: "Provide either 'uri' or 'query'." }] };
    },
  });

  // --- viking_add_resource ---
  pi.registerTool({
    name: "viking_add_resource",
    label: "Viking Add Resource",
    description: "Ingest a URL into OpenViking. The page is auto-processed into L0/L1/L2 tiers and indexed for semantic search. HTTP only — local file paths are not supported by the OV server.",
    promptSnippet: "Ingest a URL into OpenViking for indexed retrieval",
    parameters: Type.Object({
      url: Type.String({ description: "URL to ingest (HTTP only, no file paths)" }),
    }),
    async execute(
      _id: string, params: any, _signal: AbortSignal,
      _onUpdate: any, _ctx: any,
    ) {
      if (!client.connected) {
        return { content: [{ type: "text", text: "OpenViking server is not reachable." }] };
      }
      const result = await client.addResource(params.url);
      if (!result) {
        return { content: [{ type: "text", text: `Failed to ingest: ${params.url}` }] };
      }
      return {
        content: [{ type: "text", text: `Ingested: ${result.root_uri}` }],
        details: result,
      };
    },
  });
}

// ================================================================
// Context window tools (Codex bare names: new_context / history /
// get_context_remaining — plan §4)
// ================================================================

const NOT_REACHABLE = "OpenViking server is not reachable.";
const NO_SESSION = "No OpenViking session is attached to this pi session yet.";

/** Options the extension passes through so the tools stay free of pi state. */
export interface ContextWindowToolOptions {
  /**
   * The message list the `context` hook last handed to the provider — the only
   * list that reflects the current window. Without it `get_context_remaining`
   * still works, it just cannot report "time since your previous message".
   */
  getMessages?: () => any[] | undefined;
  /** pi's `compaction.reserveTokens`; read from pi's settings when omitted. */
  reserveTokens?: number;
}

function text(body: string, details?: any) {
  return { content: [{ type: "text", text: body }], details: details ?? {}, isError: false };
}

/** Truncate to `max` characters, saying so instead of trailing off silently. */
function clip(raw: string, max: number): string {
  const value = String(raw ?? "");
  if (max <= 0 || value.length <= max) return value;
  return `${value.slice(0, max)}… [truncated, ${value.length - max} more characters]`;
}

function firstLine(raw: string): string {
  const line = String(raw ?? "").split("\n").find((l) => l.trim());
  return line ? line.trim() : "";
}

/** Archives oldest first, so index 0 is the oldest window of the session. */
function orderedArchives(archives: OVArchiveEntry[]): OVArchiveEntry[] {
  return [...archives].reverse();
}

/**
 * Window id per archive.
 *
 * The core records `{windowId, archiveId}` for every archive it writes, so a
 * window that closed *without* producing one (an external compaction advances
 * the index but writes nothing) cannot shift every later id. Positional
 * numbering is only the fallback for an archive the core does not know — one
 * written before the persisted state existed, or by another process.
 */
function windowIdsFor(
  ordered: OVArchiveEntry[],
  recorded: Array<{ windowId: string; archiveId: string }>,
): string[] {
  const known = new Map<string, string>();
  for (const row of Array.isArray(recorded) ? recorded : []) {
    if (row?.archiveId && row?.windowId) known.set(String(row.archiveId), String(row.windowId));
  }
  return ordered.map((entry, i) => known.get(entry.archiveId) ?? `w${i + 1}`);
}

/**
 * `"w2"`, `"2"` or `"archive_002"` → the archive it names, resolved through the
 * recorded window ids.
 */
function resolveArchive(
  spec: string,
  ordered: OVArchiveEntry[],
  windowIds: string[],
): { entry: OVArchiveEntry; windowId: string } | null {
  const raw = String(spec ?? "").trim();
  if (!raw) return null;
  const byWindow = /^w(\d+)$/i.exec(raw) || /^(\d+)$/.exec(raw);
  if (byWindow) {
    const wanted = `w${Number(byWindow[1])}`;
    const idx = windowIds.indexOf(wanted);
    if (idx < 0) return null;
    return { entry: ordered[idx], windowId: windowIds[idx] };
  }
  const idx = ordered.findIndex((a) => a.archiveId === raw);
  if (idx < 0) return null;
  return { entry: ordered[idx], windowId: windowIds[idx] };
}

/** `"w2:14"` or a bare `"14"` when `window` carries the archive. */
function parseItemRef(item: string, windowSpec?: string): { windowSpec: string; index: number } | null {
  const raw = String(item ?? "").trim();
  if (!raw) return null;
  const parts = raw.split(":");
  if (parts.length === 2) {
    const index = Number(parts[1]);
    if (!Number.isInteger(index) || index < 0) return null;
    return { windowSpec: parts[0].trim(), index };
  }
  const index = Number(raw);
  if (!Number.isInteger(index) || index < 0) return null;
  const spec = String(windowSpec ?? "").trim();
  if (!spec) return null;
  return { windowSpec: spec, index };
}

function renderItemLine(windowId: string, index: number, msg: OVArchiveMessage): string {
  const when = msg.created_at ? ` ${msg.created_at}` : "";
  return `[id: ${windowId}:${index}]  ${msg.role || "unknown"}${when}  ${clip(oneLine(msg.text), 200)}`;
}

function oneLine(raw: string): string {
  return String(raw ?? "").replace(/\s+/g, " ").trim();
}

/** `get_context_remaining` body — plan §4, derived only from the snapshot. */
function renderStatusReport(snap: any): string {
  const lines: string[] = [];
  lines.push(
    `tokens_left: ${snap.tokensLeft === null ? "unknown (the provider did not report a window size)" : formatTokens(snap.tokensLeft)}`,
  );
  lines.push(
    `used: ~${formatTokens(snap.usedTokens)}` +
      (snap.contextWindow > 0 ? ` / ${formatTokens(snap.contextWindow)} (${snap.percent}%)` : ""),
  );
  lines.push(
    `window: ${snap.windowId}` +
      (snap.windowAgeMs === null ? " (first window of this session)" : `, opened ${formatDuration(snap.windowAgeMs)} ago`),
  );
  lines.push(`turns in this window: ${snap.turnsInWindow}`);
  lines.push(
    `since the user's last message: ${snap.sinceLastUserMs === null ? "unknown" : formatDuration(snap.sinceLastUserMs)}`,
  );
  lines.push(
    `gap before that message: ${snap.idleGapMs === null ? "unknown" : formatDuration(snap.idleGapMs)}`,
  );
  // The recorded archives, not `windowId - 1`: a window closed by an external
  // compaction advances the index without producing an archive.
  const archives = Math.max(0, Number(snap.archiveCount) || 0);
  lines.push(
    `archives: ${archives}` +
      (snap.archiveId
        ? ` (latest ${snap.archiveId}, Working Memory ${snap.overviewReady ? "ready" : "not ready"})`
        : ""),
  );
  if (Number(snap.undeliveredCount) > 0) {
    lines.push(
      `missing from the archives: ${snap.undeliveredCount} message(s) OpenViking rejected; history cannot show them`,
    );
  }
  lines.push(`accuracy: ${snap.estimated ? "estimated" : "exact"}`);
  lines.push(`advice: ${snap.advice}`);
  return lines.join("\n");
}

export function registerContextWindowTools(
  pi: any,
  client: OVClient,
  sync: SyncManager,
  windows: ContextWindowCore,
  config: OVConfig,
  opts: ContextWindowToolOptions = {},
): void {
  const reserveTokens = () =>
    typeof opts.reserveTokens === "number" ? opts.reserveTokens : readPiReserveTokens(process.cwd());

  // --- new_context ---
  pi.registerTool({
    name: "new_context",
    label: "New Context Window",
    description:
      "Start a new context window. Does not clear, reset, or otherwise affect environment state. " +
      "The conversation so far is archived to OpenViking (which generates a Working Memory of it) and " +
      "your next window starts with that Working Memory, your handoff notes and the user's last request. " +
      "Call it alone — tool results from the same batch are discarded with the old window — and read the " +
      '<openviking-context source="context-window"> block that follows. If instead this result says ' +
      '"Context window NOT reset", nothing was archived and nothing was removed from your context: read ' +
      "the reason, keep working in the current window, and do not call new_context again until that reason is gone.",
    promptSnippet: "Archive this context window to OpenViking and continue in a fresh one",
    promptGuidelines: [
      "Call new_context when a phase of the work is finished and its details are no longer needed, when the user switches to an unrelated topic or code area, when they come back after a long idle gap with something new, or when the context status line or a [context-reminder] says the window is filling up.",
      'A result starting with "Context window NOT reset" means nothing changed: no archive was written and your context is intact. Keep working; do not retry the call until the stated cause is gone.',
      "Write the handoff notes before you call it: goal, decisions and why, what is done, what is in flight, exact paths and identifiers, and the next steps. Write them for someone who has not seen this conversation.",
      "Do not call new_context in the middle of an unverified edit sequence, right after a previous reset, or to avoid a problem you have not solved.",
      "Call it alone, not alongside other tool calls: the results of the others are discarded together with the old window.",
    ],
    executionMode: "sequential",
    parameters: Type.Object({
      reason: Type.String({
        description: "One sentence: why this context window should end now.",
      }),
      notes: Type.String({
        description:
          "Handoff notes for the next window: goal, decisions and why, what is done, what is in flight, " +
          "exact paths and identifiers, next steps. Written for someone who has not seen this conversation.",
      }),
      next_steps: Type.Optional(
        Type.Array(Type.String(), { description: "Ordered next steps for the new window." }),
      ),
    }),
    async execute(
      toolCallId: string, params: any, signal: AbortSignal,
      onUpdate: any, ctx: any,
    ) {
      const result = await windows.requestReset({
        reason: params?.reason,
        notes: params?.notes,
        nextSteps: params?.next_steps,
        toolCallId,
        branch: ctx?.sessionManager?.getBranch?.() ?? [],
        signal,
        onProgress: (message: string) => {
          try {
            onUpdate?.({ content: [{ type: "text", text: message }], details: {} });
          } catch {
            // progress reporting must never break the reset
          }
        },
      });
      // Never `terminate`: a refusal has to leave the agent running, and a
      // successful reset is consumed by the context hook of the next sampling.
      return { content: [{ type: "text", text: result.text }], details: result.details, isError: false };
    },
  });

  // --- history ---
  pi.registerTool({
    name: "history",
    label: "Context History",
    description:
      "Read the context windows of this session that were already archived to OpenViking. " +
      "list_windows lists them, list_items lists the messages of one window, read_item reads one message " +
      "in full, and search_contents greps every archive. Window ids are the ones in the window header, " +
      "oldest first; the numbering can have gaps, because a window that ended without being archived is " +
      'not listed here. Item ids look like "w2:14" and come from list_items.',
    promptSnippet: "Read messages and tool output from earlier, already archived context windows",
    promptGuidelines: [
      "Use history when the window header or your notes do not cover a detail from an earlier window; start from search_contents or list_items rather than reading whole windows back in.",
      "Do not ask the user to repeat something that is in an archived window — look it up here first.",
    ],
    parameters: Type.Object({
      action: StringEnum(["list_windows", "list_items", "read_item", "search_contents"] as const),
      window: Type.Optional(Type.String({ description: 'Window or archive id, e.g. "w2" or "archive_002".' })),
      item: Type.Optional(Type.String({ description: 'Item id, e.g. "w2:14", or "14" together with `window`.' })),
      query: Type.Optional(Type.String({ description: "search_contents: text or regular expression to look for." })),
      offset: Type.Optional(Type.Number({ description: "list_items: first item index to show (default 0)." })),
      limit: Type.Optional(Type.Number({ description: "list_items: how many items to show (default 40)." })),
    }),
    async execute(
      _id: string, params: any, _signal: AbortSignal,
      _onUpdate: any, _ctx: any,
    ) {
      if (!client.connected) return text(NOT_REACHABLE);
      const sid = sync?.sessionId;
      if (!sid) return text(NO_SESSION);

      const archives = await client.listSessionArchives(sid);
      if (archives === null) {
        return text(
          "OpenViking did not answer the archive listing, so the archived windows are unreadable right now. " +
            "This is not the same as an empty history — do not conclude that nothing was archived, and do not " +
            "ask the user to repeat what an archived window holds. Try again later or work from the window header.",
          { error: "listing-failed" },
        );
      }
      const ordered = orderedArchives(archives);
      const windowIds = windowIdsFor(ordered, windows.archives ?? []);
      // The window the model is in right now: its own counter, which also
      // covers windows that closed without producing an archive.
      const currentWindowId = windows.windowId;

      switch (params?.action) {
        case "list_windows": {
          const lines = ordered.map((entry, i) => {
            const when = entry.modTime || "unknown time";
            const abstract = firstLine(entry.abstract) || "(Working Memory not ready)";
            return `${windowIds[i]}  ${entry.archiveId}  ${when}  ${clip(abstract, 200)}`;
          });
          lines.push(`${currentWindowId}  (current)  this window is still open and not archived`);
          // With nothing archived the only id in the list is the open window,
          // which `list_items` cannot read — pointing at it would just buy a
          // refusal and suggest the current window is readable through history.
          const trailer =
            windowIds.length > 0
              ? `Read one with history {"action":"list_items","window":"${windowIds[0]}"}.`
              : "Nothing is archived yet: everything this session has said is still in front of you, " +
                "and the current window becomes readable here only after new_context archives it.";
          return text(
            [
              `${ordered.length} archived window${ordered.length === 1 ? "" : "s"} in this session:`,
              ...lines,
              "",
              trailer,
            ].join("\n"),
            { archives: ordered, windowIds, currentWindowId },
          );
        }

        case "list_items": {
          const target = resolveArchive(params?.window, ordered, windowIds);
          if (!target) {
            return text(
              `No archived window named "${String(params?.window ?? "")}". ` +
                `Known: ${windowIds.join(", ") || "none"} (${currentWindowId} is current and not archived yet).`,
            );
          }
          const items = await client.readArchiveMessages(target.entry.uri);
          if (!items) {
            return text(
              `${target.windowId} (${target.entry.archiveId}): its messages could not be read from OpenViking right now ` +
                `(the archive itself is readable as soon as it is committed, so this is a transport, permission or ` +
                `routing failure rather than a wait). Try {"action":"search_contents"} instead, or continue from the ` +
                `window header; do not repeat this call more than once.`,
            );
          }
          const offsetRaw = Number(params?.offset);
          const offset = Number.isInteger(offsetRaw) && offsetRaw > 0 ? offsetRaw : 0;
          const limitRaw = Number(params?.limit);
          const limit = Number.isInteger(limitRaw) && limitRaw > 0 ? Math.min(limitRaw, 200) : 40;
          const slice = items.slice(offset, offset + limit);
          const lines = slice.map((msg, i) => renderItemLine(target.windowId, offset + i, msg));
          const end = offset + slice.length;
          const trailer =
            end < items.length
              ? `Showing ${offset}-${end - 1} of ${items.length} items. Next: {"action":"list_items","window":"${target.windowId}","offset":${end}}.`
              : `Showing ${offset}-${Math.max(offset, end - 1)} of ${items.length} items (end of window).`;
          return text(
            [
              `${target.windowId} (${target.entry.archiveId}), ${items.length} items:`,
              ...lines,
              "",
              trailer,
              `Read one in full with {"action":"read_item","item":"${target.windowId}:${offset}"}.`,
            ].join("\n"),
            { windowId: target.windowId, archiveId: target.entry.archiveId, total: items.length, offset, limit },
          );
        }

        case "read_item": {
          const ref = parseItemRef(params?.item, params?.window);
          if (!ref) return text('Provide an item id, e.g. {"action":"read_item","item":"w2:14"}.');
          const target = resolveArchive(ref.windowSpec, ordered, windowIds);
          if (!target) {
            return text(
              `No archived window named "${ref.windowSpec}". ` +
                `Known: ${windowIds.join(", ") || "none"} (${currentWindowId} is current and not archived yet).`,
            );
          }
          const items = await client.readArchiveMessages(target.entry.uri);
          if (!items) {
            return text(
              `${target.windowId} (${target.entry.archiveId}): its messages could not be read from OpenViking right now ` +
                `(the archive itself is readable as soon as it is committed, so this is a transport, permission or ` +
                `routing failure rather than a wait). Try {"action":"search_contents"} instead, or continue from the ` +
                `window header; do not repeat this call more than once.`,
            );
          }
          const msg = items[ref.index];
          if (!msg) {
            return text(
              `${target.windowId} has ${items.length} items (0-${Math.max(0, items.length - 1)}); ${ref.index} is out of range.`,
            );
          }
          const header = `[id: ${target.windowId}:${ref.index}]  ${msg.role || "unknown"}${msg.created_at ? `  ${msg.created_at}` : ""}`;
          return text(
            [header, clip(msg.text, config.contextWindow.historyItemMaxChars)].join("\n"),
            { windowId: target.windowId, archiveId: target.entry.archiveId, index: ref.index, role: msg.role },
          );
        }

        case "search_contents": {
          const query = String(params?.query ?? "").trim();
          if (!query) return text('Provide a query, e.g. {"action":"search_contents","query":"ZEPHYR"}.');
          // The server reads the pattern as a regular expression, and a model's
          // query is usually plain text: one stray "(" would come back as a
          // server error this tool cannot tell apart from "no match", and the
          // model would conclude the archive does not hold what it is looking
          // for. A pattern that is not a valid expression is searched verbatim.
          let literal = false;
          try {
            new RegExp(query);
          } catch {
            literal = true;
          }
          const matches = await client.grepSessionArchives(sid, query, {
            caseInsensitive: true,
            literal,
          });
          if (matches.length === 0) {
            return text(`No match for "${query}" in the ${ordered.length} archived window(s) of this session.`);
          }
          const windowOf = new Map(ordered.map((e, i) => [e.archiveId, windowIds[i]]));
          const shown = matches.slice(0, 20);
          const groups = new Map<string, string[]>();
          for (const m of shown) {
            const windowId = windowOf.get(m.archiveId) ?? "(unknown window)";
            const key = `${windowId}  ${m.archiveId}`;
            // messages.jsonl holds one message per line, so the line number is
            // the item index + 1 — enough to point read_item at the message.
            const pointer =
              m.file === "messages.jsonl" && m.line > 0 ? `  → {"action":"read_item","item":"${windowId}:${m.line - 1}"}` : "";
            const list = groups.get(key) ?? [];
            list.push(`  ${m.file}:${m.line}  ${clip(oneLine(m.content), 400)}${pointer}`);
            groups.set(key, list);
          }
          const out: string[] = [`${matches.length} match(es) for "${query}"${matches.length > shown.length ? `, showing ${shown.length}` : ""}:`];
          for (const [key, list] of groups) {
            out.push(key, ...list);
          }
          return text(out.join("\n"), { matches: shown, total: matches.length });
        }

        default:
          return text('Unknown action. Use list_windows, list_items, read_item or search_contents.');
      }
    },
  });

  // --- get_context_remaining ---
  pi.registerTool({
    name: "get_context_remaining",
    label: "Context Remaining",
    description:
      "How much of the current context window is left, how long it has been open, and how long since the " +
      "user's last message. Read-only — use it to decide whether to keep going or call new_context.",
    promptSnippet: "Check how much of the current context window is left",
    promptGuidelines: [
      "Call get_context_remaining before starting a large step, so you can open a fresh window first instead of running out halfway through.",
    ],
    parameters: Type.Object({}),
    async execute(
      _id: string, _params: any, _signal: AbortSignal,
      _onUpdate: any, ctx: any,
    ) {
      const snapshot = windows.statusSnapshot({
        usage: ctx?.getContextUsage?.(),
        now: Date.now(),
        messages: opts.getMessages?.(),
        reserveTokens: reserveTokens(),
      });
      return text(renderStatusReport(snapshot), snapshot);
    },
  });
}
