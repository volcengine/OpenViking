import { Type } from "@sinclair/typebox";
import type { OVMessage } from "../client.js";
import type { RecallTraceEntry, RecallTraceResult } from "../recall-trace.js";

export type OpenVikingArchiveToolContext = {
  sessionKey?: string;
  sessionId?: string;
  agentId?: string;
  senderId?: string;
};

export type OpenVikingArchiveSession = {
  sessionId?: string;
  sessionKey?: string;
  ovSessionId?: string;
  agentId: string;
};

type OpenVikingArchiveMatch = {
  uri: string;
  line: number;
  content: string;
};

type ArchiveSearchSource =
  | "messages.jsonl"
  | "memory_diff.json"
  | ".overview.md"
  | ".abstract.md"
  | "other";

type ClassifiedArchiveSearchMatch = OpenVikingArchiveMatch & {
  archiveTag: string;
  field?: string;
  hidden: boolean;
  index: number;
  source: ArchiveSearchSource;
};

export type OpenVikingArchiveClient = {
  grepSessionArchives: (
    sessionId: string,
    pattern: string,
    options: { archiveId?: string; caseInsensitive: boolean },
  ) => Promise<{
    count?: number;
    matches?: OpenVikingArchiveMatch[];
  }>;
  getSessionArchive: (
    sessionId: string,
    archiveId: string,
  ) => Promise<{
    archive_id: string;
    abstract?: string;
    messages: OVMessage[];
  }>;
};

export type OpenVikingArchiveToolsDeps = {
  registerTool: (toolOrFactory: unknown, opts: { name: string }) => void;
  getClient: () => Promise<OpenVikingArchiveClient>;
  rememberSessionAgentId: (ctx: OpenVikingArchiveToolContext) => void;
  toOvSessionId: (sessionId?: string, sessionKey?: string) => string;
  resolveAgentId: (sessionId?: string, sessionKey?: string, ovSessionId?: string) => string;
  resolvePluginSessionRouting: (ctx?: OpenVikingArchiveToolContext) => OpenVikingArchiveSession;
  isBypassedSession: (ctx?: OpenVikingArchiveToolContext) => boolean;
  makeBypassedToolResult: (toolName: string) => unknown;
  formatMessage: (message: OVMessage) => string;
  traceRecorder?: { recordAndFlush: (trace: RecallTraceEntry) => Promise<unknown> | unknown };
  traceRecallMaxResultsPerSearch: number;
  traceRecallPreviewChars: number;
  createTraceId: (source: string) => string;
  logger?: {
    info?: (message: string) => void;
    warn?: (message: string) => void;
    error?: (message: string) => void;
  };
};

function previewText(value: unknown, maxChars: number): string | undefined {
  const text = typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "";
  if (!text) {
    return undefined;
  }
  return text.length <= maxChars ? text : `${text.slice(0, Math.max(0, maxChars - 1))}…`;
}

function archiveTagFromUri(uri: string): string {
  return uri.match(/archive_\d+/)?.[0] ?? "unknown";
}

function classifyArchiveSearchSource(uri: string): ArchiveSearchSource {
  const path = uri.split(/[?#]/, 1)[0] ?? "";
  const basename = path.split("/").filter(Boolean).at(-1) ?? "";
  if (
    basename === "messages.jsonl" ||
    basename === "memory_diff.json" ||
    basename === ".overview.md" ||
    basename === ".abstract.md"
  ) {
    return basename;
  }
  return "other";
}

function classifyArchiveSearchMatch(
  match: OpenVikingArchiveMatch,
  index: number,
): ClassifiedArchiveSearchMatch {
  const source = classifyArchiveSearchSource(match.uri);
  const field = source === "memory_diff.json"
    ? match.content.match(/^\s*"([^"]+)"\s*:/)?.[1]
    : undefined;
  return {
    ...match,
    archiveTag: archiveTagFromUri(match.uri),
    field,
    hidden: (source === "memory_diff.json" && field !== "after") || source === "other",
    index,
    source,
  };
}

function archiveSearchSourceRank(match: ClassifiedArchiveSearchMatch): number {
  switch (match.source) {
    case "messages.jsonl":
      return 0;
    case "memory_diff.json":
      return 1;
    case ".overview.md":
      return 2;
    case ".abstract.md":
      return 3;
    default:
      return 4;
  }
}

function selectArchiveSearchMatches(
  matches: ClassifiedArchiveSearchMatch[],
  maxMatches: number,
): ClassifiedArchiveSearchMatch[] {
  const sorted = [...matches].sort((a, b) => {
    const bySource = archiveSearchSourceRank(a) - archiveSearchSourceRank(b);
    return bySource || a.index - b.index;
  });
  const distinctArchives = new Set(sorted.map((match) => match.archiveTag)).size;
  const perArchiveLimit = distinctArchives > 1 ? 3 : maxMatches;
  const selected: ClassifiedArchiveSearchMatch[] = [];
  const selectedIndexes = new Set<number>();
  const perArchiveCounts = new Map<string, number>();

  for (const match of sorted) {
    if (selected.length >= maxMatches) {
      break;
    }
    const count = perArchiveCounts.get(match.archiveTag) ?? 0;
    if (count >= perArchiveLimit) {
      continue;
    }
    selected.push(match);
    selectedIndexes.add(match.index);
    perArchiveCounts.set(match.archiveTag, count + 1);
  }

  for (const match of sorted) {
    if (selected.length >= maxMatches) {
      break;
    }
    if (!selectedIndexes.has(match.index)) {
      selected.push(match);
    }
  }
  return selected;
}

function formatArchiveSearchMatch(
  match: ClassifiedArchiveSearchMatch,
  index: number,
  maxLineLength: number,
): string {
  const fieldLine = match.field ? `\nfield: ${match.field}` : "";
  const body = match.content.length > maxLineLength
    ? `${match.content.slice(0, maxLineLength)}...(truncated)`
    : match.content;
  return `## Match ${index + 1}: ${match.archiveTag}\nsource: ${match.source}${fieldLine}\nline: ${match.line}\n${body}`;
}

export function registerOpenVikingArchiveTools(deps: OpenVikingArchiveToolsDeps): void {
  deps.registerTool(
    (ctx: OpenVikingArchiveToolContext) => ({
      name: "ov_archive_search",
      label: "Archive Search (OpenViking)",
      description:
        "Keyword-grep across all archived original conversation messages of the current session. " +
        "Results are source-labeled; stale memory-diff fields such as before/uri are hidden. " +
        "Use this whenever the [Session History Summary] does not contain the specific detail " +
        "the user is asking about. Start with one high-signal query using concrete names, " +
        "places, objects, dates, or distinctive phrases. Run one follow-up search only when " +
        "the first result is empty or inconclusive and another concrete query is available.",
      parameters: Type.Object({
        query: Type.String({
          description:
            "A single keyword or short phrase to grep. Use concrete nouns, names, dates, " +
            "or distinctive phrases. Case-insensitive. Prefer entity words over full sentences.",
        }),
        archiveId: Type.Optional(
          Type.String({
            description: 'Optional: limit search to one archive (e.g. "archive_005")',
          }),
        ),
      }),
      async execute(_toolCallId: string, params: Record<string, unknown>) {
        if (deps.isBypassedSession(ctx)) {
          return deps.makeBypassedToolResult("ov_archive_search");
        }
        deps.rememberSessionAgentId(ctx);
        const sessionId = ctx.sessionId ?? "";
        const sessionKey = ctx.sessionKey ?? "";
        if (!sessionId && !sessionKey) {
          return {
            content: [{ type: "text", text: "Error: no active session." }],
            details: { error: "no_session" },
          };
        }
        const ovSessionId = deps.toOvSessionId(ctx.sessionId, ctx.sessionKey);
        const query = String((params as { query?: string }).query ?? "").trim();
        const archiveId = (params as { archiveId?: string }).archiveId;

        if (!query) {
          return {
            content: [{ type: "text", text: "Error: query is required." }],
            details: { error: "missing_param", param: "query" },
          };
        }

        const escapedQuery = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        deps.logger?.info?.(`openviking: ov_archive_search query="${query}" escaped="${escapedQuery}" archive=${archiveId ?? "all"} session=${ovSessionId}`);

        try {
          const client = await deps.getClient();
          const agentId = deps.resolveAgentId(ctx.sessionId, ctx.sessionKey);
          const started = Date.now();
          const result = await client.grepSessionArchives(ovSessionId, escapedQuery, {
            archiveId,
            caseInsensitive: true,
          });
          const rawMatches = result.matches ?? [];
          const rawMatchCount = result.count ?? rawMatches.length;
          const classifiedMatches = rawMatches.map(classifyArchiveSearchMatch);
          const usableMatches = classifiedMatches.filter((match) => !match.hidden);
          const hiddenMatchCount = classifiedMatches.length - usableMatches.length;
          const traceResults: RecallTraceResult[] = usableMatches.slice(0, deps.traceRecallMaxResultsPerSearch).map((match) => ({
            uri: match.uri,
            resourceType: "archive",
            abstractPreview: previewText(match.content, deps.traceRecallPreviewChars),
            resultType: "archive_match",
          }));

          const recordArchiveTrace = async (displayed: ClassifiedArchiveSearchMatch[]) => {
            await deps.traceRecorder?.recordAndFlush({
              schemaVersion: "1.0",
              traceId: deps.createTraceId("ov_archive_search"),
              ts: Date.now(),
              sessionId: ctx.sessionId,
              sessionKey: ctx.sessionKey,
              ovSessionId,
              agentId,
              source: "ov_archive_search",
              operationType: "archive_grep",
              resourceTypes: ["session"],
              trigger: { query, derivedKeywords: [query] },
              searches: [{
                resourceType: "archive",
                targetUriResolved: archiveId ? `viking://session/${ovSessionId}/history/${archiveId}` : `viking://session/${ovSessionId}/history`,
                limit: deps.traceRecallMaxResultsPerSearch,
                durationMs: Date.now() - started,
                total: rawMatchCount,
                results: traceResults,
                archiveId,
                caseInsensitive: true,
              }],
              selected: displayed.map((match) => ({
                uri: match.uri,
                resourceType: "archive",
                line: match.line,
                abstractPreview: previewText(match.content, deps.traceRecallPreviewChars),
                displayed: true,
              })),
              stats: {
                candidateCount: rawMatchCount,
                selectedCount: displayed.length,
                injectedCount: 0,
              },
            });
          };

          if (usableMatches.length === 0) {
            await recordArchiveTrace([]);
            const hiddenHint = hiddenMatchCount > 0
              ? ` ${hiddenMatchCount} stale or metadata match(es) were hidden.`
              : "";
            return {
              content: [{
                type: "text",
                text: `No relevant matches found for "${query}".${hiddenHint} ` +
                  "Try one more search only if another concrete name, date, place, object, " +
                  "or distinctive phrase is available.",
              }],
              details: {
                query,
                matchCount: 0,
                rawMatchCount,
                hiddenMatchCount,
                shownMatchCount: 0,
              },
            };
          }

          const MAX_MATCHES = 5;
          const MAX_LINE_LEN = 700;
          const shown = selectArchiveSearchMatches(usableMatches, MAX_MATCHES);
          await recordArchiveTrace(shown);
          const blocks = shown.map((match, index) =>
            formatArchiveSearchMatch(match, index, MAX_LINE_LEN),
          );

          const hiddenSuffix = hiddenMatchCount > 0
            ? `; ${hiddenMatchCount} stale/metadata raw match(es) hidden`
            : "";
          const header = `Found ${usableMatches.length} relevant match(es) for "${query}"` +
            ` (${rawMatchCount} raw${hiddenSuffix})` +
            (usableMatches.length > MAX_MATCHES ? ` (showing first ${MAX_MATCHES})` : "") + ":";

          return {
            content: [{ type: "text", text: header + "\n\n" + blocks.join("\n\n") }],
            details: {
              query,
              matchCount: usableMatches.length,
              rawMatchCount,
              hiddenMatchCount,
              shownMatchCount: shown.length,
              sourceCounts: usableMatches.reduce<Record<string, number>>((counts, match) => {
                const key = match.field ? `${match.source}:${match.field}` : match.source;
                counts[key] = (counts[key] ?? 0) + 1;
                return counts;
              }, {}),
            },
          };
        } catch (err: unknown) {
          const msg = err instanceof Error ? err.message : String(err);
          deps.logger?.error?.(`openviking: ov_archive_search error: ${msg}`);
          return {
            content: [{ type: "text", text: `Archive search failed: ${msg}` }],
            details: { error: msg },
          };
        }
      },
    }),
    { name: "ov_archive_search" },
  );

  deps.registerTool((ctx: OpenVikingArchiveToolContext) => ({
    name: "ov_archive_expand",
    label: "Archive Expand (OpenViking)",
    description:
      "Retrieve original messages from a compressed session archive. " +
      "Use when a session summary lacks specific details " +
      "such as exact commands, file paths, code snippets, or config values. " +
      "Use an archive ID returned by ov_archive_search.",
    parameters: Type.Object({
      archiveId: Type.String({
        description: 'Archive ID returned by ov_archive_search (e.g. "archive_002")',
      }),
    }),
    async execute(_toolCallId: string, params: Record<string, unknown>) {
      if (deps.isBypassedSession(ctx)) {
        return deps.makeBypassedToolResult("ov_archive_expand");
      }
      const session = deps.resolvePluginSessionRouting(ctx);
      const archiveId = String((params as { archiveId?: string }).archiveId ?? "").trim();
      const sessionId = session.sessionId ?? "";
      deps.logger?.info?.(`openviking: ov_archive_expand invoked (archiveId=${archiveId || "(empty)"}, sessionId=${sessionId || "(empty)"})`);

      if (!archiveId) {
        deps.logger?.warn?.("openviking: ov_archive_expand missing archiveId");
        return {
          content: [{ type: "text", text: "Error: archiveId is required." }],
          details: { error: "missing_param", param: "archiveId" },
        };
      }

      if (!session.ovSessionId) {
        return {
          content: [{ type: "text", text: "Error: no active session." }],
          details: { error: "no_session" },
        };
      }

      try {
        const client = await deps.getClient();
        const detail = await client.getSessionArchive(
          session.ovSessionId,
          archiveId,
        );

        const header = [
          `## ${detail.archive_id}`,
          detail.abstract ? `**Summary**: ${detail.abstract}` : "",
          `**Messages**: ${detail.messages.length}`,
          "",
        ].filter(Boolean).join("\n");

        const body = detail.messages
          .map((message) => deps.formatMessage(message))
          .join("\n\n");

        deps.logger?.info?.(`openviking: ov_archive_expand expanded ${detail.archive_id}, messages=${detail.messages.length}, chars=${body.length}, sessionId=${sessionId}`);
        return {
          content: [{ type: "text", text: `${header}\n${body}` }],
          details: {
            action: "expanded",
            archiveId: detail.archive_id,
            messageCount: detail.messages.length,
            sessionId,
            ovSessionId: session.ovSessionId,
          },
        };
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        deps.logger?.warn?.(`openviking: ov_archive_expand failed (archiveId=${archiveId}, sessionId=${sessionId}): ${msg}`);
        return {
          content: [{ type: "text", text: `Failed to expand ${archiveId}: ${msg}` }],
          details: { error: msg, archiveId, sessionId, ovSessionId: session.ovSessionId },
        };
      }
    },
  }), { name: "ov_archive_expand" });
}
