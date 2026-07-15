import { Type } from "@sinclair/typebox";

export type OpenVikingToolResultToolContext = {
  sessionKey?: string;
  sessionId?: string;
  agentId?: string;
  senderId?: string;
};

export type OpenVikingToolResultSession = {
  sessionId?: string;
  sessionKey?: string;
  ovSessionId?: string;
  agentId: string;
};

export type OpenVikingToolResultToolDefinition = {
  name: string;
  label: string;
  description: string;
  parameters: unknown;
  execute: (_toolCallId: string, params: Record<string, unknown>) => Promise<unknown>;
};

export type OpenVikingToolResultClient = {
  readToolResult: (
    sessionId: string,
    toolResultId: string,
    options: { offset: number; limit: number; includeMetadata: boolean },
  ) => Promise<{
    content: string;
    offset: number;
    limit: number;
    total_chars: number;
    has_more: boolean;
    tool_result_id: string;
    metadata?: unknown;
  }>;
  searchToolResult: (
    sessionId: string,
    toolResultId: string,
    query: string,
    options: { limit: number; contextChars: number },
  ) => Promise<{
    tool_result_id: string;
    matches?: Array<{ offset: number; snippet: string }>;
  }>;
  listToolResults: (
    sessionId: string,
    options: { toolName?: string; limit: number },
  ) => Promise<{
    tool_results?: Array<{
      storage_uri?: string;
      tool_name?: string;
      original_chars?: number;
      created_at?: string;
    }>;
  }>;
};

export type OpenVikingToolResultToolsDeps = {
  registerTool: (toolOrFactory: unknown, opts: { name: string }) => void;
  getClient: () => Promise<OpenVikingToolResultClient>;
  resolvePluginSessionRouting: (ctx?: OpenVikingToolResultToolContext) => OpenVikingToolResultSession;
  isBypassedSession: (ctx?: OpenVikingToolResultToolContext) => boolean;
  makeBypassedToolResult: (toolName: string) => unknown;
  logger?: { warn?: (message: string) => void };
};

type ToolResultRef = {
  sessionId: string;
  toolResultId: string;
  ref: string;
};

export function parseToolResultRef(value: unknown): ToolResultRef | null {
  const raw = typeof value === "string" ? value.trim() : "";
  if (!raw) {
    return null;
  }
  const match = raw.match(/^viking:\/\/session\/([^/]+)\/tool-results\/([^/?#]+)(?:[?#].*)?$/);
  if (!match) {
    return null;
  }
  const sessionId = decodeURIComponent(match[1]!);
  const toolResultId = decodeURIComponent(match[2]!);
  if (!sessionId || !toolResultId) {
    return null;
  }
  return {
    sessionId,
    toolResultId,
    ref: `viking://session/${encodeURIComponent(sessionId)}/tool-results/${encodeURIComponent(toolResultId)}`,
  };
}

function getOptionalInteger(value: unknown, fallback: number): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return fallback;
  }
  return Math.floor(value);
}

function getPositiveInteger(value: unknown, fallback: number): number {
  return Math.max(1, getOptionalInteger(value, fallback));
}

export function registerOpenVikingToolResultTools({
  registerTool,
  getClient,
  resolvePluginSessionRouting,
  isBypassedSession,
  makeBypassedToolResult,
  logger,
}: OpenVikingToolResultToolsDeps): void {
  registerTool(
    (ctx: OpenVikingToolResultToolContext): OpenVikingToolResultToolDefinition => ({
      name: "openviking_tool_result_read",
      label: "Tool Result Read (OpenViking)",
      description:
        "Restore the full original content of a tool result that was externalized by OpenViking. " +
        "Use when a previous tool result was externalized and only a preview is visible — " +
        "the preview contains a [tool-result-ref] or viking://session/.../tool-results/... URI. " +
        "\"Read\" tool returns the same truncated preview; this tool returns the complete content. " +
        "To read all content: pass offset=0 and a limit large enough to cover the whole result " +
        "(e.g. limit=100000). Use offset/limit for paging only when you need a specific section.",
      parameters: Type.Object({
        tool_output_ref: Type.String({
          description:
            "Exact OV URI from the preview, e.g. viking://session/<session_id>/tool-results/<tool_result_id>",
        }),
        offset: Type.Optional(Type.Number({ description: "Unicode character offset. Default: 0" })),
        limit: Type.Optional(Type.Number({ description: "Maximum Unicode characters to read. Default: 20000" })),
      }),
      async execute(_toolCallId: string, params: Record<string, unknown>) {
        if (isBypassedSession(ctx)) {
          return makeBypassedToolResult("openviking_tool_result_read");
        }
        const session = resolvePluginSessionRouting(ctx);
        if (!session.ovSessionId) {
          return {
            content: [{ type: "text", text: "Error: no active session." }],
            details: { error: "no_session" },
          };
        }

        const parsed = parseToolResultRef(params.tool_output_ref ?? params.ref ?? params.uri);
        if (!parsed) {
          return {
            content: [{ type: "text", text: "Error: tool_output_ref must be a viking://session/.../tool-results/... URI." }],
            details: { error: "invalid_tool_output_ref" },
          };
        }
        if (parsed.sessionId !== session.ovSessionId) {
          return {
            content: [{ type: "text", text: "Error: refusing to read a tool result from another session." }],
            details: {
              error: "session_mismatch",
              requestedSessionId: parsed.sessionId,
              currentSessionId: session.ovSessionId,
            },
          };
        }

        const offset = Math.max(0, getOptionalInteger(params.offset, 0));
        const limit = getOptionalInteger(params.limit, 20_000);
        if (limit < -1) {
          return {
            content: [{ type: "text", text: "Error: limit must be -1 or greater than or equal to 0." }],
            details: { error: "invalid_limit", limit },
          };
        }

        try {
          const client = await getClient();
          const result = await client.readToolResult(
            session.ovSessionId,
            parsed.toolResultId,
            { offset, limit, includeMetadata: true },
          );
          const returnedChars = result.content.length;
          const nextOffset = result.offset + returnedChars;
          const text = result.content || "(empty tool result chunk)";
          return {
            content: [{ type: "text", text }],
            details: {
              action: "read",
              tool_output_ref: parsed.ref,
              tool_result_id: result.tool_result_id,
              offset: result.offset,
              limit: result.limit,
              returned_chars: returnedChars,
              total_chars: result.total_chars,
              has_more: result.has_more,
              next_offset: result.has_more ? nextOffset : null,
              metadata: result.metadata ?? null,
            },
          };
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          logger?.warn?.(`openviking: openviking_tool_result_read failed: ${msg}`);
          return {
            content: [{ type: "text", text: `Failed to read tool result: ${msg}` }],
            details: { error: msg, tool_output_ref: parsed.ref },
          };
        }
      },
    }),
    { name: "openviking_tool_result_read" },
  );

  registerTool(
    (ctx: OpenVikingToolResultToolContext): OpenVikingToolResultToolDefinition => ({
      name: "openviking_tool_result_search",
      label: "Tool Result Search (OpenViking)",
      description:
        "Search inside an externalized tool result for a keyword. " +
        "Use when you need to find specific content in a large externalized result, " +
        "before reading it with openviking_tool_result_read. " +
        "Returns matching snippets with their character offsets.",
      parameters: Type.Object({
        tool_output_ref: Type.String({
          description:
            "Exact OV URI from the preview, e.g. viking://session/<session_id>/tool-results/<tool_result_id>",
        }),
        query: Type.String({ description: "Keyword or exact text to search for" }),
        limit: Type.Optional(Type.Number({ description: "Maximum matches. Default: 20" })),
        context_chars: Type.Optional(Type.Number({ description: "Characters around each match. Default: 300" })),
      }),
      async execute(_toolCallId: string, params: Record<string, unknown>) {
        if (isBypassedSession(ctx)) {
          return makeBypassedToolResult("openviking_tool_result_search");
        }
        const session = resolvePluginSessionRouting(ctx);
        if (!session.ovSessionId) {
          return {
            content: [{ type: "text", text: "Error: no active session." }],
            details: { error: "no_session" },
          };
        }

        const parsed = parseToolResultRef(params.tool_output_ref ?? params.ref ?? params.uri);
        if (!parsed) {
          return {
            content: [{ type: "text", text: "Error: tool_output_ref must be a viking://session/.../tool-results/... URI." }],
            details: { error: "invalid_tool_output_ref" },
          };
        }
        if (parsed.sessionId !== session.ovSessionId) {
          return {
            content: [{ type: "text", text: "Error: refusing to search a tool result from another session." }],
            details: {
              error: "session_mismatch",
              requestedSessionId: parsed.sessionId,
              currentSessionId: session.ovSessionId,
            },
          };
        }

        const query = String(params.query ?? "").trim();
        if (!query) {
          return {
            content: [{ type: "text", text: "Error: query is required." }],
            details: { error: "missing_param", param: "query" },
          };
        }
        const limit = getPositiveInteger(params.limit, 20);
        const contextChars = Math.max(
          0,
          getOptionalInteger(params.context_chars ?? params.contextChars, 300),
        );

        try {
          const client = await getClient();
          const result = await client.searchToolResult(
            session.ovSessionId,
            parsed.toolResultId,
            query,
            { limit, contextChars },
          );
          const matches = result.matches ?? [];
          const text = matches.length
            ? [
                `Found ${matches.length} match(es) for "${query}" in ${parsed.ref}:`,
                "",
                ...matches.map((match, index) =>
                  `## Match ${index + 1} (offset ${match.offset})\n${match.snippet}`,
                ),
              ].join("\n")
            : `No matches found for "${query}" in ${parsed.ref}.`;
          return {
            content: [{ type: "text", text }],
            details: {
              action: "searched",
              tool_output_ref: parsed.ref,
              tool_result_id: result.tool_result_id,
              query,
              match_count: matches.length,
              matches,
            },
          };
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          logger?.warn?.(`openviking: openviking_tool_result_search failed: ${msg}`);
          return {
            content: [{ type: "text", text: `Failed to search tool result: ${msg}` }],
            details: { error: msg, tool_output_ref: parsed.ref, query },
          };
        }
      },
    }),
    { name: "openviking_tool_result_search" },
  );

  registerTool(
    (ctx: OpenVikingToolResultToolContext): OpenVikingToolResultToolDefinition => ({
      name: "openviking_tool_result_list",
      label: "Tool Result List (OpenViking)",
      description:
        "List externalized tool results for the current session. " +
        "Use to discover available refs before calling openviking_tool_result_read. " +
        "Optionally filter by tool_name to narrow down results.",
      parameters: Type.Object({
        tool_name: Type.Optional(Type.String({ description: "Optional exact tool name filter" })),
        limit: Type.Optional(Type.Number({ description: "Maximum results. Default: 50" })),
      }),
      async execute(_toolCallId: string, params: Record<string, unknown>) {
        if (isBypassedSession(ctx)) {
          return makeBypassedToolResult("openviking_tool_result_list");
        }
        const session = resolvePluginSessionRouting(ctx);
        if (!session.ovSessionId) {
          return {
            content: [{ type: "text", text: "Error: no active session." }],
            details: { error: "no_session" },
          };
        }

        const toolName =
          typeof params.tool_name === "string" && params.tool_name.trim()
            ? params.tool_name.trim()
            : typeof params.toolName === "string" && params.toolName.trim()
              ? params.toolName.trim()
              : undefined;
        const limit = getPositiveInteger(params.limit, 50);

        try {
          const client = await getClient();
          const result = await client.listToolResults(
            session.ovSessionId,
            { toolName, limit },
          );
          const items = result.tool_results ?? [];
          const text = items.length
            ? [
                `Found ${items.length} externalized tool result(s) in current session:`,
                "",
                ...items.map((item, index) => {
                  const ref = typeof item.storage_uri === "string" ? item.storage_uri : "(missing ref)";
                  const name = typeof item.tool_name === "string" ? item.tool_name : "tool";
                  const chars = typeof item.original_chars === "number" ? item.original_chars : "unknown";
                  const created = typeof item.created_at === "string" ? ` created_at=${item.created_at}` : "";
                  return `${index + 1}. ${name} original_chars=${chars}${created}\nref: ${ref}`;
                }),
              ].join("\n")
            : toolName
              ? `No externalized tool results found for tool "${toolName}" in current session.`
              : "No externalized tool results found in current session.";
          return {
            content: [{ type: "text", text }],
            details: {
              action: "listed",
              session_id: session.ovSessionId,
              tool_name: toolName ?? null,
              count: items.length,
              tool_results: items,
            },
          };
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          logger?.warn?.(`openviking: openviking_tool_result_list failed: ${msg}`);
          return {
            content: [{ type: "text", text: `Failed to list tool results: ${msg}` }],
            details: { error: msg, session_id: session.ovSessionId, tool_name: toolName ?? null },
          };
        }
      },
    }),
    { name: "openviking_tool_result_list" },
  );
}
