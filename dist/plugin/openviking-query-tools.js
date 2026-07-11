import { Type } from "@sinclair/typebox";
export function registerOpenVikingQueryTools(deps) {
    deps.registerTool((ctx) => ({
        name: "ov_search",
        label: "Search (OpenViking)",
        description: "Search OpenViking resources and skills. Use after importing, or when the user asks to search OpenViking resources or skills. " +
            "Search only returns ranked snippets; call ov_read on exact hit URIs before answering precise questions. " +
            "When a result is part of a split document or a multi-step procedure, call ov_list on the parent URI to inspect sibling chunks and overview files before answering. " +
            "Returned viking:// URIs are OpenViking virtual URIs, not local file paths.",
        parameters: Type.Object({
            query: Type.String({ description: "Search query" }),
            uri: Type.Optional(Type.String({ description: "Optional search URI. Defaults to resources plus agent skills." })),
            limit: Type.Optional(Type.Number({ description: "Max results per search scope. Default: 10" })),
        }),
        async execute(_toolCallId, params) {
            if (deps.isBypassedSession(ctx)) {
                return deps.makeBypassedToolResult("ov_search");
            }
            const session = deps.resolvePluginSessionRouting(ctx);
            return deps.searchOpenViking({
                query: String(params.query ?? ""),
                uri: typeof params.uri === "string" ? params.uri : undefined,
                limit: typeof params.limit === "number" ? params.limit : undefined,
            }, session.agentId, session);
        },
    }), { name: "ov_search" });
    deps.registerTool((ctx) => ({
        name: "ov_read",
        label: "Read (OpenViking)",
        description: "Read the full original content of one exact OpenViking viking:// URI returned by ov_search, ov_list, or recall traces. " +
            "Use after ov_search before answering precise documentation, codebase, configuration, or procedural questions. " +
            "OpenViking URIs are virtual context-database identifiers, not local file paths; do not use filesystem read tools for them. " +
            "Never pass shortened or display-truncated URIs ending with ... or containing …. Use the exact full URI.",
        parameters: Type.Object({
            uri: Type.String({ description: "Exact viking:// URI returned by ov_search; pass the full URI, e.g. viking://resources/project-docs/api.md#chunk-3; do not use shortened display text with ... or …." }),
        }),
        async execute(_toolCallId, params) {
            if (deps.isBypassedSession(ctx)) {
                return deps.makeBypassedToolResult("ov_read");
            }
            const session = deps.resolvePluginSessionRouting(ctx);
            return deps.readOpenVikingContent({
                uri: String(params.uri ?? ""),
            }, session.agentId);
        },
    }), { name: "ov_read" });
    deps.registerTool((ctx) => ({
        name: "ov_multi_read",
        label: "Multi Read (OpenViking)",
        description: "Read the full original content of multiple exact OpenViking URIs concurrently. " +
            "Use after ov_search and ov_list to read an overview plus sibling chunks for split documents or multi-step procedures.",
        parameters: Type.Object({
            uris: Type.Array(Type.String({ description: "Exact OpenViking viking:// URI to read" }), {
                description: "Exact OpenViking viking:// URIs to read",
            }),
        }),
        async execute(_toolCallId, params) {
            if (deps.isBypassedSession(ctx)) {
                return deps.makeBypassedToolResult("ov_multi_read");
            }
            const session = deps.resolvePluginSessionRouting(ctx);
            const uris = Array.isArray(params.uris)
                ? params.uris.map((uri) => String(uri))
                : [];
            return deps.multiReadOpenVikingContent({ uris }, session.agentId);
        },
    }), { name: "ov_multi_read" });
    deps.registerTool((ctx) => ({
        name: "ov_list",
        label: "List (OpenViking)",
        description: "List files and directories under an OpenViking URI. Use after ov_search to inspect a hit's parent directory, sibling chunks, or .overview.md files when search only returns ranked snippets.",
        parameters: Type.Object({
            uri: Type.String({ description: "OpenViking directory URI to list, e.g. viking://resources/project/docs" }),
            recursive: Type.Optional(Type.Boolean({ description: "List nested entries recursively. Default: false" })),
            simple: Type.Optional(Type.Boolean({ description: "Return only URI entries from OpenViking. Default: false" })),
            limit: Type.Optional(Type.Number({ description: "Maximum entries to list. Default: 100" })),
        }),
        async execute(_toolCallId, params) {
            if (deps.isBypassedSession(ctx)) {
                return deps.makeBypassedToolResult("ov_list");
            }
            const session = deps.resolvePluginSessionRouting(ctx);
            return deps.listOpenVikingDirectory({
                uri: String(params.uri ?? ""),
                recursive: typeof params.recursive === "boolean" ? params.recursive : undefined,
                simple: typeof params.simple === "boolean" ? params.simple : undefined,
                limit: typeof params.limit === "number" ? params.limit : undefined,
            }, session.agentId);
        },
    }), { name: "ov_list" });
}
