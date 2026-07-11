function getOptionalInteger(value, fallback) {
    if (typeof value !== "number" || !Number.isFinite(value)) {
        return fallback;
    }
    return Math.floor(value);
}
function getPositiveInteger(value, fallback) {
    return Math.max(1, getOptionalInteger(value, fallback));
}
function toQueryObject(request) {
    const query = { ...(request?.query ?? {}) };
    if (request?.url) {
        const parsed = new URL(request.url, "http://openclaw.local");
        for (const [key, value] of parsed.searchParams.entries()) {
            query[key] = value;
        }
    }
    return { ...query, ...(request?.params ?? {}) };
}
function toBoolean(value) {
    if (typeof value === "boolean")
        return value;
    if (typeof value !== "string")
        return undefined;
    return ["1", "true", "yes"].includes(value.trim().toLowerCase());
}
function toNumber(value) {
    if (typeof value === "number")
        return value;
    if (typeof value === "string" && value.trim()) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : undefined;
    }
    return undefined;
}
function toString(value) {
    return typeof value === "string" && value.trim() ? value.trim() : undefined;
}
function queryValue(query, ...keys) {
    for (const key of keys) {
        if (query[key] !== undefined) {
            return query[key];
        }
    }
    return undefined;
}
function toSessionKey(query) {
    return toString(queryValue(query, "sessionKey", "sessionkey", "session_key", "session-key"));
}
function toLimitedInteger(value, fallback, min, max) {
    const parsed = toNumber(value);
    const raw = parsed === undefined ? fallback : Math.floor(parsed);
    return Number.isFinite(raw) && raw >= min && raw <= max ? raw : undefined;
}
function inferUriType(uri) {
    if (uri.startsWith("viking://resources") || uri.startsWith("viking://resource"))
        return "resource";
    if (uri.startsWith("viking://session/"))
        return "session";
    if (uri.startsWith("viking://user/skills") || uri.startsWith("viking://skills"))
        return "skill";
    if (uri.startsWith("viking://user/"))
        return "user_memory";
    if (uri.includes("/archive") || uri.includes("/history/"))
        return "archive";
    return "unknown";
}
function hasExplicitRecallTraceIdentityFilter(input) {
    const traceId = typeof input.traceId === "string" && input.traceId.trim();
    const sessionId = typeof input.sessionId === "string" && input.sessionId.trim();
    const sessionKey = typeof input.sessionKey === "string" && input.sessionKey.trim();
    const ovSessionId = typeof input.ovSessionId === "string" && input.ovSessionId.trim();
    return !!(traceId || sessionId || sessionKey || ovSessionId);
}
function findTraceItem(entry, uri) {
    const selected = entry.selected.find((item) => item.uri === uri);
    const searchResult = entry.searches.flatMap((search) => search.results).find((item) => item.uri === uri);
    if (!selected && !searchResult) {
        return undefined;
    }
    return {
        category: selected?.category ?? searchResult?.category,
        score: selected?.score ?? searchResult?.score,
        level: searchResult?.level,
        abstractPreview: selected?.abstractPreview ?? searchResult?.abstractPreview,
        resultType: searchResult?.resultType,
        sourceTraceId: entry.traceId,
        source: entry.source,
    };
}
export function createOpenVikingRecallTraceRuntime(deps) {
    const parseRecallTraceInput = (input, ctx) => {
        const traceId = typeof input.traceId === "string" && input.traceId.trim() ? input.traceId.trim() : undefined;
        const explicitSessionId = typeof input.sessionId === "string" && input.sessionId.trim() ? input.sessionId.trim() : undefined;
        const explicitSessionKey = typeof input.sessionKey === "string" && input.sessionKey.trim() ? input.sessionKey.trim() : undefined;
        const explicitOvSessionId = typeof input.ovSessionId === "string" && input.ovSessionId.trim() ? input.ovSessionId.trim() : undefined;
        const hasExplicitIdentityFilter = !!(traceId || explicitSessionId || explicitSessionKey || explicitOvSessionId);
        const defaultSessionKey = !hasExplicitIdentityFilter ? ctx.sessionKey : undefined;
        const defaultSessionId = !hasExplicitIdentityFilter && !defaultSessionKey ? ctx.sessionId : undefined;
        const defaultOvSessionId = !hasExplicitIdentityFilter && !defaultSessionKey && !defaultSessionId ? ctx.ovSessionId : undefined;
        return {
            turn: input.turn === "all" ? "all" : "latest",
            traceId,
            sessionId: explicitSessionId ?? defaultSessionId,
            sessionKey: explicitSessionKey ?? defaultSessionKey,
            ovSessionId: explicitOvSessionId ?? defaultOvSessionId,
            source: typeof input.source === "string" && input.source.trim() ? input.source : undefined,
            resourceTypes: input.resourceTypes ? deps.normalizeResourceTypes(input.resourceTypes) : undefined,
            since: typeof input.since === "number" ? input.since : undefined,
            until: typeof input.until === "number" ? input.until : undefined,
            limit: getPositiveInteger(input.limit, 20),
        };
    };
    const shouldIncludeTraceContent = (input) => typeof input?.includeContent === "boolean" ? input.includeContent : deps.cfg.traceRecallIncludeContentByDefault;
    const enrichTraceEntriesWithContent = async (result, includeContent, agentId) => {
        if (!includeContent || result.entries.length === 0) {
            return result;
        }
        const client = await deps.getClient();
        const warnings = [...result.warnings];
        const entries = await Promise.all(result.entries.map(async (entry) => {
            const selected = await Promise.all(entry.selected.map(async (item) => {
                try {
                    const content = await client.read(item.uri, agentId);
                    const text = typeof content === "string" ? content : JSON.stringify(content);
                    return { ...item, contentPreview: deps.previewText(text, deps.cfg.recallMaxContentChars) };
                }
                catch (err) {
                    const readError = err instanceof Error ? err.message : String(err);
                    warnings.push(`Failed to read recall trace content ${item.uri}: ${readError}`);
                    return { ...item, readError };
                }
            }));
            return { ...entry, selected };
        }));
        return { ...result, entries, warnings };
    };
    const queryTraceForRoute = async (query, session) => deps.traceRecorder
        ? deps.traceRecorder.queryWithFallback(parseRecallTraceInput(query, session))
        : Promise.resolve({ entries: [], lookupLayer: "memory", warnings: ["traceRecall is disabled"] });
    const queryRecallTraces = async (input, session) => {
        const query = parseRecallTraceInput(input, session);
        const base = deps.traceRecorder
            ? await deps.traceRecorder.queryWithFallback(query)
            : { entries: [], lookupLayer: "memory", warnings: ["traceRecall is disabled"] };
        if (deps.traceRecorder &&
            base.entries.length === 0 &&
            !hasExplicitRecallTraceIdentityFilter(input) &&
            query.sessionKey) {
            const fallbackIdentities = [];
            if (session.sessionId) {
                fallbackIdentities.push({ ...query, sessionKey: undefined, sessionId: session.sessionId, ovSessionId: undefined });
            }
            if (session.ovSessionId) {
                fallbackIdentities.push({ ...query, sessionKey: undefined, sessionId: undefined, ovSessionId: session.ovSessionId });
            }
            for (const fallbackQuery of fallbackIdentities) {
                const fallback = await deps.traceRecorder.queryWithFallback(fallbackQuery);
                if (fallback.entries.length > 0) {
                    return enrichTraceEntriesWithContent(fallback, shouldIncludeTraceContent(input), session.agentId);
                }
            }
        }
        return enrichTraceEntriesWithContent(base, shouldIncludeTraceContent(input), session.agentId);
    };
    const handleUriDetail = async (request) => {
        const query = toQueryObject(request);
        const uri = toString(query.uri);
        if (!uri || !uri.startsWith("viking://") || uri.endsWith("...") || uri.includes("…")) {
            return { status: 400, body: { ok: false, uri: uri ?? "", readStatus: "not_requested", warnings: [], error: { code: "invalid_uri", message: "uri must be a complete viking:// URI" } } };
        }
        const offset = toLimitedInteger(query.offset, 0, 0, 1_000_000_000);
        const contentLimit = toLimitedInteger(queryValue(query, "contentLimit", "content-limit", "content_limit"), 20_000, 1, 100_000);
        if (offset === undefined || contentLimit === undefined) {
            return { status: 400, body: { ok: false, uri, readStatus: "not_requested", warnings: [], error: { code: "invalid_param", message: "offset or contentLimit is invalid" } } };
        }
        const includeContent = toBoolean(queryValue(query, "includeContent", "include-content", "include_content")) ?? true;
        const preferTracePreview = toBoolean(queryValue(query, "preferTracePreview", "prefer-trace-preview", "prefer_trace_preview")) ?? true;
        const session = deps.resolvePluginSessionRouting(query);
        const traceId = toString(queryValue(query, "traceId", "trace-id", "trace_id"));
        let traceMetadata;
        const warnings = [];
        if (preferTracePreview && traceId && deps.traceRecorder) {
            const traceResult = await queryTraceForRoute({ traceId, turn: "latest", limit: 1 }, session);
            warnings.push(...traceResult.warnings);
            traceMetadata = traceResult.entries[0] ? findTraceItem(traceResult.entries[0], uri) : undefined;
        }
        const baseBody = {
            ok: true,
            uri,
            uriType: inferUriType(uri),
            abstractPreview: traceMetadata?.abstractPreview,
            metadata: {
                category: traceMetadata?.category,
                score: traceMetadata?.score,
                level: traceMetadata?.level,
                resultType: traceMetadata?.resultType,
                sourceTraceId: traceMetadata?.sourceTraceId,
                source: traceMetadata?.source,
            },
            readStatus: "not_requested",
            warnings,
        };
        if (!includeContent) {
            return { status: 200, body: baseBody };
        }
        try {
            const client = await deps.getClient();
            const content = await client.read(uri, toString(query.agentId) ?? session.agentId);
            const text = typeof content === "string" ? content : JSON.stringify(content, null, 2);
            const chars = Array.from(text);
            const slice = chars.slice(offset, offset + contentLimit).join("");
            return {
                status: 200,
                body: {
                    ...baseBody,
                    content: {
                        text: slice,
                        offset,
                        limit: contentLimit,
                        returnedChars: Array.from(slice).length,
                        totalChars: chars.length,
                        hasMore: offset + contentLimit < chars.length,
                    },
                    readStatus: "ok",
                },
            };
        }
        catch (err) {
            return {
                status: 502,
                body: {
                    ...baseBody,
                    ok: false,
                    readStatus: "read_failed",
                    error: { code: "read_failed", message: err instanceof Error ? err.message : String(err) },
                },
            };
        }
    };
    const handleLatestOvSearchList = async (request) => {
        const query = toQueryObject(request);
        const session = deps.resolvePluginSessionRouting(query);
        const limit = toLimitedInteger(query.limit, 20, 1, 100);
        if (limit === undefined) {
            return { status: 400, body: { ok: false, items: [], totalItems: 0, warnings: [], error: { code: "invalid_param", message: "limit is invalid" } } };
        }
        const includeSelected = toBoolean(query.includeSelected) ?? true;
        const dedupe = toBoolean(query.dedupe) ?? true;
        const includeSkills = toBoolean(query.includeSkills) ?? true;
        const strict = toBoolean(query.strict) ?? false;
        const result = await queryTraceForRoute({
            turn: "latest",
            source: "ov_search",
            sessionId: toString(query.sessionId),
            sessionKey: toSessionKey(query),
            ovSessionId: toString(query.ovSessionId),
            limit: 1,
        }, session);
        const agentId = toString(query.agentId);
        const entry = agentId ? result.entries.find((candidate) => candidate.agentId === agentId) : result.entries[0];
        if (!entry) {
            const body = {
                ok: !strict,
                lookupLayer: "none",
                fallbackUsed: false,
                query: { sessionId: toString(query.sessionId), sessionKey: toString(query.sessionKey), ovSessionId: toString(query.ovSessionId), agentId, limit, lookup: toString(query.lookup) ?? "auto" },
                items: [],
                totalItems: 0,
                warnings: [...result.warnings, "no ov_search trace found for latest interaction"],
                ...(strict ? { error: { code: "not_found", message: "no ov_search trace found for latest interaction" } } : {}),
            };
            return { status: strict ? 404 : 200, body };
        }
        const searchResultByUri = new Map();
        for (const search of entry.searches) {
            for (const item of search.results) {
                searchResultByUri.set(item.uri, { ...item, targetUri: search.targetUriResolved ?? search.targetUriInput });
            }
        }
        const items = [];
        const addItem = (item, source) => {
            const matched = searchResultByUri.get(item.uri);
            const resultType = matched?.resultType ?? (inferUriType(item.uri) === "skill" ? "skill" : inferUriType(item.uri).includes("memory") ? "memory" : "resource");
            if (!includeSkills && resultType === "skill") {
                return;
            }
            const row = {
                uri: item.uri,
                abstractPreview: item.abstractPreview ?? matched?.abstractPreview,
                resourceType: item.resourceType ?? matched?.resourceType,
                resultType,
                category: item.category ?? matched?.category,
                score: item.score ?? matched?.score,
                source,
                targetUri: matched?.targetUri,
                detailUrl: `/api/openviking/uri-detail?uri=${encodeURIComponent(item.uri)}&traceId=${encodeURIComponent(entry.traceId)}`,
            };
            if (dedupe) {
                const existing = items.findIndex((candidate) => candidate.uri === item.uri);
                if (existing >= 0) {
                    if (source === "selected")
                        items[existing] = row;
                    return;
                }
            }
            items.push(row);
        };
        if (includeSelected) {
            entry.selected.forEach((item) => addItem(item, "selected"));
        }
        for (const search of entry.searches) {
            search.results.forEach((item) => addItem(item, "search_result"));
        }
        const limited = items.slice(0, limit);
        return {
            status: 200,
            body: {
                ok: true,
                lookupLayer: result.lookupLayer,
                fallbackUsed: result.lookupLayer === "persistent",
                query: { sessionId: toString(query.sessionId), sessionKey: toString(query.sessionKey), ovSessionId: toString(query.ovSessionId), agentId, limit, lookup: toString(query.lookup) ?? "auto" },
                trace: {
                    traceId: entry.traceId,
                    ts: entry.ts,
                    isoTime: new Date(entry.ts).toISOString(),
                    triggerQuery: entry.trigger.query,
                    totalSearches: entry.searches.length,
                },
                items: limited,
                totalItems: limited.length,
                warnings: result.warnings,
            },
        };
    };
    const handleRecallTraces = async (request) => {
        const query = toQueryObject(request);
        const session = deps.resolvePluginSessionRouting(query);
        const result = await queryRecallTraces({
            turn: query.turn === "all" ? "all" : "latest",
            traceId: toString(queryValue(query, "traceId", "trace-id", "trace_id")),
            sessionId: typeof query.sessionId === "string" ? query.sessionId : undefined,
            sessionKey: toSessionKey(query),
            ovSessionId: typeof query.ovSessionId === "string" ? query.ovSessionId : undefined,
            source: typeof query.source === "string" ? query.source : undefined,
            resourceTypes: typeof query.resourceTypes === "string" ? query.resourceTypes : undefined,
            since: toNumber(query.since),
            until: toNumber(query.until),
            includeContent: toBoolean(queryValue(query, "includeContent", "include-content", "include_content")),
            limit: toNumber(query.limit),
        }, session);
        return { status: 200, body: { ok: true, ...result } };
    };
    const formatRecallTraceText = (result) => {
        if (result.entries.length === 0) {
            return `No OpenViking recall traces found (lookupLayer=${result.lookupLayer}).`;
        }
        const blocks = result.entries.map((entry, index) => {
            const selected = entry.selected.slice(0, 8)
                .map((item) => `  - ${item.uri}${item.score !== undefined ? ` (${(deps.clampScore(item.score) * 100).toFixed(0)}%)` : ""}`)
                .join("\n");
            return [
                `## Trace ${index + 1}: ${entry.source}`,
                `traceId: ${entry.traceId}`,
                `query: ${entry.trigger.query}`,
                `resourceTypes: ${entry.resourceTypes.join(", ")}`,
                `stats: candidates=${entry.stats.candidateCount}, selected=${entry.stats.selectedCount}, injected=${entry.stats.injectedCount}`,
                selected ? `selected:\n${selected}` : "selected: (none)",
            ].join("\n");
        });
        const warnings = result.warnings.length > 0
            ? `\n\nWarnings:\n${result.warnings.map((w) => `- ${w}`).join("\n")}`
            : "";
        return `${blocks.join("\n\n")}${warnings}`;
    };
    const routeHandlers = {
        handleRecallTraces,
        handleUriDetail,
        handleLatestOvSearchList,
    };
    return {
        queryRecallTraces,
        formatRecallTraceText,
        routeHandlers,
        registerRecallTraceRoutes: (ctx) => deps.registerRecallTraceRoutes(ctx, routeHandlers),
    };
}
