import { Type } from "@sinclair/typebox";
export function registerOpenVikingRecallTraceTools(deps) {
    deps.registerTool((ctx) => ({
        name: "ov_recall_trace",
        label: "Recall Trace (OpenViking)",
        description: "Query OpenViking recall trace records captured by auto-recall and explicit recall/search tools.",
        parameters: Type.Object({
            turn: Type.Optional(Type.String({ description: "latest or all (default: latest)" })),
            traceId: Type.Optional(Type.String({ description: "Exact trace id" })),
            sessionId: Type.Optional(Type.String({ description: "OpenClaw session id" })),
            sessionKey: Type.Optional(Type.String({ description: "OpenClaw session key" })),
            ovSessionId: Type.Optional(Type.String({ description: "OpenViking session id" })),
            source: Type.Optional(Type.String({ description: "auto_recall, memory_recall, ov_search, or ov_archive_search" })),
            resourceTypes: Type.Optional(Type.Array(Type.String({ description: "resource, user, or agent" }))),
            since: Type.Optional(Type.Number({ description: "Unix timestamp lower bound in milliseconds" })),
            until: Type.Optional(Type.Number({ description: "Unix timestamp upper bound in milliseconds" })),
            includeContent: Type.Optional(Type.Boolean({ description: "Read selected/displayed URI content previews on demand" })),
            limit: Type.Optional(Type.Number({ description: "Maximum traces to return (default: 20)" })),
        }),
        async execute(_toolCallId, params) {
            if (deps.isBypassedSession(ctx)) {
                return deps.makeBypassedToolResult("ov_recall_trace");
            }
            const session = deps.resolvePluginSessionRouting(ctx);
            const result = await deps.queryRecallTraces(params, session);
            return {
                content: [{ type: "text", text: deps.formatRecallTraceText(result) }],
                details: {
                    action: "queried",
                    count: result.entries.length,
                    lookupLayer: result.lookupLayer,
                    warnings: result.warnings,
                    entries: result.entries,
                },
            };
        },
    }), { name: "ov_recall_trace" });
}
