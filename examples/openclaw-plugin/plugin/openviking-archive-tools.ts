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

export function registerOpenVikingArchiveTools(deps: OpenVikingArchiveToolsDeps): void {
  deps.registerTool(
    (ctx: OpenVikingArchiveToolContext) => ({
      name: "ov_archive_search",
      label: "Archive Search (OpenViking)",
      description:
        "Keyword-grep across all archived original conversation messages of the current session. " +
        "Use this whenever the [Session History Summary] does not contain the specific detail " +
        "the user is asking about. Extract 2-3 concrete entity words from the question " +
        "(names, places, objects, dates) and search each separately. " +
        "Only conclude information is unavailable after trying at least 2 different keyword variations.",
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
          const traceResults: RecallTraceResult[] = (result.matches ?? []).slice(0, deps.traceRecallMaxResultsPerSearch).map((match) => ({
            uri: match.uri,
            resourceType: "archive",
            abstractPreview: previewText(match.content, deps.traceRecallPreviewChars),
            resultType: "archive_match",
          }));

          const recordArchiveTrace = async (displayed: OpenVikingArchiveMatch[]) => {
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
                total: result.matches?.length ?? result.count ?? 0,
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
                candidateCount: result.matches?.length ?? result.count ?? 0,
                selectedCount: displayed.length,
                injectedCount: 0,
              },
            });
          };

          if (!result.matches || result.matches.length === 0) {
            await recordArchiveTrace([]);
            return {
              content: [{
                type: "text",
                text: `No matches found for "${query}". Try a different keyword — ` +
                  "the original conversation may use different wording than the question. " +
                  "Try synonyms, related terms, or shorter fragments.",
              }],
              details: { query, matchCount: 0 },
            };
          }

          const MAX_MATCHES = 12;
          const MAX_LINE_LEN = 1500;
          const shown = result.matches.slice(0, MAX_MATCHES);
          await recordArchiveTrace(shown);
          const blocks = shown.map((m, i) => {
            const archiveTag = m.uri.match(/archive_\d+/)?.[0] ?? "unknown";
            const truncated = m.content.length > MAX_LINE_LEN
              ? m.content.slice(0, MAX_LINE_LEN) + "…(truncated)"
              : m.content;
            return `## Match ${i + 1}: ${archiveTag} (line ${m.line})\n${truncated}`;
          });

          const header = `Found ${result.matches.length} match(es) for "${query}"` +
            (result.matches.length > MAX_MATCHES ? ` (showing first ${MAX_MATCHES})` : "") + ":";

          return {
            content: [{ type: "text", text: header + "\n\n" + blocks.join("\n\n") }],
            details: { query, matchCount: result.matches.length },
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
      "Check [Archive Index] to find the right archive ID.",
    parameters: Type.Object({
      archiveId: Type.String({
        description:
          'Archive ID from [Archive Index] (e.g. "archive_002")',
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
