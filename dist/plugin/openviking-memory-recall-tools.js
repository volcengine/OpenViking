import { Type } from "@sinclair/typebox";
function toTraceResult(item, resultType, deps) {
    return {
        uri: item.uri,
        resourceType: deps.inferRecallResourceType(item.uri),
        category: item.category,
        score: item.score,
        level: item.level,
        abstractPreview: deps.previewText(item.abstract || item.overview, deps.cfg.traceRecallPreviewChars),
        resultType,
    };
}
export function registerOpenVikingMemoryRecallTools(deps) {
    deps.registerTool((ctx) => ({
        name: "memory_recall",
        label: "Memory Recall (OpenViking)",
        description: "Search long-term memories from OpenViking. Use when you need past user preferences, facts, or decisions.",
        parameters: Type.Object({
            query: Type.String({ description: "Search query" }),
            limit: Type.Optional(Type.Number({ description: "Max results (default: plugin config)" })),
            scoreThreshold: Type.Optional(Type.Number({ description: "Minimum score (0-1, default: plugin config)" })),
            targetUri: Type.Optional(Type.String({ description: "Search scope URI (default: plugin config)" })),
            resourceTypes: Type.Optional(Type.Array(Type.String({ description: "resource, user, or agent; used when targetUri is omitted" }))),
        }),
        async execute(_toolCallId, params) {
            if (deps.isBypassedSession(ctx)) {
                return deps.makeBypassedToolResult("memory_recall");
            }
            const session = deps.resolvePluginSessionRouting(ctx);
            const { query } = params;
            const queryConfig = await deps.queryConfigStore.getEffective(deps.toQueryConfigContext(session), {
                recallLimit: typeof params.limit === "number" ? params.limit : undefined,
                scoreThreshold: typeof params.scoreThreshold === "number" ? params.scoreThreshold : undefined,
                targetUri: typeof params.targetUri === "string" ? params.targetUri : undefined,
                resourceTypes: Object.prototype.hasOwnProperty.call(params, "resourceTypes")
                    ? params.resourceTypes
                    : undefined,
            });
            const limit = queryConfig.recallLimit;
            const scoreThreshold = queryConfig.scoreThreshold;
            const targetUri = typeof params.targetUri === "string"
                ? params.targetUri
                : queryConfig.targetUri;
            const requestedResourceTypes = Object.prototype.hasOwnProperty.call(params, "resourceTypes")
                ? params.resourceTypes
                : queryConfig.resourceTypes;
            const requestLimit = queryConfig.candidateLimit;
            const recallClient = await deps.getClient();
            if (deps.cfg.logFindRequests) {
                deps.logger.info?.(`openviking: memory_recall X-OpenViking-Actor-Peer="${session.agentId}" ` +
                    `(plugin defaultAgentId="${recallClient.getDefaultAgentId()}" is unused when session context is present)`);
            }
            let result;
            let memoryRecallSearches = [];
            if (targetUri) {
                result = await recallClient.find(query, {
                    targetUri,
                    limit: requestLimit,
                    scoreThreshold: 0,
                    actorPeerId: session.agentId,
                });
                const traceResults = [
                    ...(result.memories ?? []).map((item) => toTraceResult(item, "memory", deps)),
                    ...(result.resources ?? []).map((item) => toTraceResult(item, "resource", deps)),
                ].slice(0, deps.cfg.traceRecallMaxResultsPerSearch);
                memoryRecallSearches = [{
                        resourceType: deps.inferRecallResourceType(targetUri) ?? "resource",
                        targetUriInput: targetUri,
                        targetUriResolved: targetUri,
                        limit: requestLimit,
                        scoreThreshold,
                        durationMs: 0,
                        total: result.total ?? traceResults.length,
                        results: traceResults,
                    }];
            }
            else {
                const searchPlan = deps.resolveRecallSearchPlan(requestedResourceTypes ?? deps.cfg.recallTargetTypes, {
                    ovSessionId: session.ovSessionId,
                    agentId: session.agentId,
                });
                const settled = await Promise.allSettled(searchPlan.searches.map((search) => recallClient.find(query, {
                    targetUri: search.targetUri,
                    limit: requestLimit,
                    scoreThreshold: 0,
                    contextType: search.contextType,
                    actorPeerId: session.agentId,
                })));
                const allMemories = [];
                for (let index = 0; index < settled.length; index += 1) {
                    const s = settled[index];
                    const search = searchPlan.searches[index];
                    if (s.status === "fulfilled") {
                        allMemories.push(...(s.value.memories ?? []), ...(s.value.resources ?? []));
                        memoryRecallSearches.push({
                            resourceType: search.resourceType,
                            targetUriInput: search.targetUri,
                            targetUriResolved: search.targetUri,
                            limit: requestLimit,
                            scoreThreshold,
                            durationMs: 0,
                            total: s.value.total ?? ((s.value.memories ?? []).length + (s.value.resources ?? []).length),
                            results: [
                                ...(s.value.memories ?? []).map((item) => toTraceResult(item, "memory", deps)),
                                ...(s.value.resources ?? []).map((item) => toTraceResult(item, "resource", deps)),
                            ].slice(0, deps.cfg.traceRecallMaxResultsPerSearch),
                        });
                    }
                    else {
                        memoryRecallSearches.push({
                            resourceType: search.resourceType,
                            targetUriInput: search.targetUri,
                            targetUriResolved: search.targetUri,
                            limit: requestLimit,
                            scoreThreshold,
                            durationMs: 0,
                            total: 0,
                            results: [],
                            error: s.reason instanceof Error ? s.reason.message : String(s.reason),
                        });
                    }
                }
                const uniqueMemories = allMemories.filter((memory, index, self) => index === self.findIndex((m) => m.uri === memory.uri));
                const leafOnly = uniqueMemories.filter((m) => !m.level || m.level === 2);
                result = {
                    memories: leafOnly,
                    total: leafOnly.length,
                };
            }
            const leafOnly = (result.memories ?? []).filter((m) => !m.level || m.level === 2);
            const processed = deps.postProcessMemories(leafOnly, {
                limit: requestLimit,
                scoreThreshold,
            });
            const memories = deps.pickMemoriesForInjection(processed, limit, query, scoreThreshold, {
                weights: queryConfig.rankingWeights,
                categoryWeights: queryConfig.categoryWeights,
                resourceTypeWeights: queryConfig.resourceTypeWeights,
            });
            const candidateTraceResults = leafOnly
                .map((item) => toTraceResult(item, deps.inferRecallResourceType(item.uri) === "resource" ? "resource" : "memory", deps))
                .slice(0, deps.cfg.traceRecallMaxResultsPerSearch);
            const traceResourceTypes = [...new Set((targetUri ? [deps.inferRecallResourceType(targetUri)] : memoryRecallSearches.map((search) => search.resourceType))
                    .filter((resourceType) => Boolean(resourceType)))];
            const recordMemoryRecallTrace = async (injectedUris) => {
                await deps.traceRecorder?.recordAndFlush?.({
                    schemaVersion: "1.0",
                    traceId: deps.createTraceId("memory_recall"),
                    ts: Date.now(),
                    sessionId: session.sessionId,
                    sessionKey: session.sessionKey,
                    ovSessionId: session.ovSessionId,
                    agentId: session.agentId,
                    source: "memory_recall",
                    operationType: "semantic_find",
                    resourceTypes: traceResourceTypes.length > 0 ? traceResourceTypes : ["resource"],
                    trigger: deps.boundTraceQuery(query, deps.cfg.traceRecallQueryMaxChars),
                    searches: memoryRecallSearches.length > 0 ? memoryRecallSearches : [{
                            resourceType: "resource",
                            targetUriInput: targetUri,
                            targetUriResolved: targetUri ?? "viking://resources",
                            limit: requestLimit,
                            scoreThreshold,
                            durationMs: 0,
                            total: result.total ?? leafOnly.length,
                            results: candidateTraceResults,
                        }],
                    selected: memories.map((item) => ({
                        uri: item.uri,
                        resourceType: deps.inferRecallResourceType(item.uri),
                        category: item.category,
                        score: item.score,
                        abstractPreview: deps.previewText(item.abstract || item.overview, deps.cfg.traceRecallPreviewChars),
                        injected: injectedUris.has(item.uri),
                        displayed: injectedUris.has(item.uri),
                    })),
                    stats: {
                        candidateCount: leafOnly.length,
                        selectedCount: memories.length,
                        injectedCount: injectedUris.size,
                    },
                });
            };
            if (memories.length === 0) {
                await recordMemoryRecallTrace(new Set());
                return {
                    content: [{ type: "text", text: "No relevant OpenViking memories found." }],
                    details: { count: 0, total: result.total ?? 0, scoreThreshold },
                };
            }
            const { lines: memoryLines } = await deps.buildMemoryLinesWithBudget(memories, (uri) => recallClient.read(uri, session.agentId), {
                recallPreferAbstract: false,
                recallMaxInjectedChars: queryConfig.maxInjectedChars,
            });
            if (memoryLines.length === 0) {
                await recordMemoryRecallTrace(new Set());
                return {
                    content: [
                        {
                            type: "text",
                            text: `No complete OpenViking memories fit recallMaxInjectedChars=${queryConfig.maxInjectedChars}.`,
                        },
                    ],
                    details: {
                        count: 0,
                        memories,
                        total: result.total ?? memories.length,
                        scoreThreshold,
                        requestLimit,
                        recallMaxInjectedChars: queryConfig.maxInjectedChars,
                    },
                };
            }
            await recordMemoryRecallTrace(new Set(memories.slice(0, memoryLines.length).map((item) => item.uri)));
            return {
                content: [
                    {
                        type: "text",
                        text: `Found ${memoryLines.length} memories:\n\n${memoryLines.join("\n")}`,
                    },
                ],
                details: {
                    count: memoryLines.length,
                    memories,
                    total: result.total ?? memories.length,
                    scoreThreshold,
                    requestLimit,
                    recallMaxInjectedChars: queryConfig.maxInjectedChars,
                },
            };
        },
    }), { name: "memory_recall" });
}
