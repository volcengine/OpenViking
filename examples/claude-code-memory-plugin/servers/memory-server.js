/**
 * OpenViking Memory MCP Server for Claude Code
 *
 * Exposes OpenViking long-term memory as MCP tools:
 *   - memory_recall  : semantic search across memories
 *   - memory_store   : extract and persist new memories
 *   - memory_forget  : delete memories by URI or query
 *
 * Ported from the OpenClaw context-engine plugin (openclaw-plugin/).
 * Adapted for Claude Code's MCP server interface (stdio transport).
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { createHash } from "node:crypto";
// ---------------------------------------------------------------------------
// Configuration — loaded from ov.conf (shared with OpenClaw plugin).
// Env var: OPENVIKING_CONFIG_FILE (default: ~/.openviking/ov.conf)
// Plugin-specific overrides go in the optional "claude_code" section.
// ---------------------------------------------------------------------------
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";
function loadOvConf() {
    const defaultPath = join(homedir(), ".openviking", "ov.conf");
    const configPath = resolvePath((process.env.OPENVIKING_CONFIG_FILE || defaultPath).replace(/^~/, homedir()));
    try {
        return JSON.parse(readFileSync(configPath, "utf-8"));
    }
    catch (err) {
        const code = err?.code;
        const msg = code === "ENOENT"
            ? `Config file not found: ${configPath}`
            : `Failed to read config: ${configPath}`;
        process.stderr.write(`[openviking-memory] ${msg}\n`);
        process.exit(1);
    }
}
function num(val, fallback) {
    if (typeof val === "number" && Number.isFinite(val))
        return val;
    if (typeof val === "string" && val.trim()) {
        const n = Number(val);
        if (Number.isFinite(n))
            return n;
    }
    return fallback;
}
function str(val, fallback) {
    if (typeof val === "string" && val.trim())
        return val.trim();
    return fallback;
}
const file = loadOvConf();
const serverCfg = (file.server ?? {});
const cc = (file.claude_code ?? {});
const host = str(serverCfg.host, "127.0.0.1").replace("0.0.0.0", "127.0.0.1");
const port = Math.floor(num(serverCfg.port, 1933));
const config = {
    baseUrl: `http://${host}:${port}`,
    apiKey: str(serverCfg.root_api_key, ""),
    agentId: str(cc.agentId, "claude-code"),
    timeoutMs: Math.max(1000, Math.floor(num(cc.timeoutMs, 15000))),
    recallLimit: Math.max(1, Math.floor(num(cc.recallLimit, 6))),
    scoreThreshold: Math.min(1, Math.max(0, num(cc.scoreThreshold, 0.01))),
};
// ---------------------------------------------------------------------------
// OpenViking HTTP Client (ported from openclaw-plugin/client.ts)
// ---------------------------------------------------------------------------
const MEMORY_URI_PATTERNS = [
    /^viking:\/\/user\/(?:[^/]+\/)?memories(?:\/|$)/,
    /^viking:\/\/agent\/(?:[^/]+\/)?memories(?:\/|$)/,
];
const USER_STRUCTURE_DIRS = new Set(["memories"]);
const AGENT_STRUCTURE_DIRS = new Set(["memories", "skills", "instructions", "workspaces"]);
function md5Short(input) {
    return createHash("md5").update(input).digest("hex").slice(0, 12);
}
function isMemoryUri(uri) {
    return MEMORY_URI_PATTERNS.some((p) => p.test(uri));
}
class OpenVikingClient {
    baseUrl;
    apiKey;
    agentId;
    timeoutMs;
    resolvedSpaceByScope = {};
    runtimeIdentity = null;
    constructor(baseUrl, apiKey, agentId, timeoutMs) {
        this.baseUrl = baseUrl;
        this.apiKey = apiKey;
        this.agentId = agentId;
        this.timeoutMs = timeoutMs;
    }
    async request(path, init = {}) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), this.timeoutMs);
        try {
            const headers = new Headers(init.headers ?? {});
            if (this.apiKey)
                headers.set("X-API-Key", this.apiKey);
            if (this.agentId)
                headers.set("X-OpenViking-Agent", this.agentId);
            if (init.body && !headers.has("Content-Type"))
                headers.set("Content-Type", "application/json");
            const response = await fetch(`${this.baseUrl}${path}`, {
                ...init,
                headers,
                signal: controller.signal,
            });
            const payload = (await response.json().catch(() => ({})));
            if (!response.ok || payload.status === "error") {
                const code = payload.error?.code ? ` [${payload.error.code}]` : "";
                const message = payload.error?.message ?? `HTTP ${response.status}`;
                throw new Error(`OpenViking request failed${code}: ${message}`);
            }
            return (payload.result ?? payload);
        }
        finally {
            clearTimeout(timer);
        }
    }
    async healthCheck() {
        try {
            await this.request("/health");
            return true;
        }
        catch {
            return false;
        }
    }
    async ls(uri) {
        return this.request(`/api/v1/fs/ls?uri=${encodeURIComponent(uri)}&output=original`);
    }
    async getRuntimeIdentity() {
        if (this.runtimeIdentity)
            return this.runtimeIdentity;
        const fallback = { userId: "default", agentId: this.agentId || "default" };
        try {
            const status = await this.request("/api/v1/system/status");
            const userId = typeof status.user === "string" && status.user.trim() ? status.user.trim() : "default";
            this.runtimeIdentity = { userId, agentId: this.agentId || "default" };
            return this.runtimeIdentity;
        }
        catch {
            this.runtimeIdentity = fallback;
            return fallback;
        }
    }
    async resolveScopeSpace(scope) {
        const cached = this.resolvedSpaceByScope[scope];
        if (cached)
            return cached;
        const identity = await this.getRuntimeIdentity();
        const fallbackSpace = scope === "user" ? identity.userId : md5Short(`${identity.userId}:${identity.agentId}`);
        const reservedDirs = scope === "user" ? USER_STRUCTURE_DIRS : AGENT_STRUCTURE_DIRS;
        try {
            const entries = await this.ls(`viking://${scope}`);
            const spaces = entries
                .filter((e) => e?.isDir === true)
                .map((e) => (typeof e.name === "string" ? e.name.trim() : ""))
                .filter((n) => n && !n.startsWith(".") && !reservedDirs.has(n));
            if (spaces.length > 0) {
                if (spaces.includes(fallbackSpace)) {
                    this.resolvedSpaceByScope[scope] = fallbackSpace;
                    return fallbackSpace;
                }
                if (scope === "user" && spaces.includes("default")) {
                    this.resolvedSpaceByScope[scope] = "default";
                    return "default";
                }
                if (spaces.length === 1) {
                    this.resolvedSpaceByScope[scope] = spaces[0];
                    return spaces[0];
                }
            }
        }
        catch { /* fall through */ }
        this.resolvedSpaceByScope[scope] = fallbackSpace;
        return fallbackSpace;
    }
    async normalizeTargetUri(targetUri) {
        const trimmed = targetUri.trim().replace(/\/+$/, "");
        const match = trimmed.match(/^viking:\/\/(user|agent)(?:\/(.*))?$/);
        if (!match)
            return trimmed;
        const scope = match[1];
        const rawRest = (match[2] ?? "").trim();
        if (!rawRest)
            return trimmed;
        const parts = rawRest.split("/").filter(Boolean);
        if (parts.length === 0)
            return trimmed;
        const reservedDirs = scope === "user" ? USER_STRUCTURE_DIRS : AGENT_STRUCTURE_DIRS;
        if (!reservedDirs.has(parts[0]))
            return trimmed;
        const space = await this.resolveScopeSpace(scope);
        return `viking://${scope}/${space}/${parts.join("/")}`;
    }
    async find(query, options) {
        const normalizedTargetUri = await this.normalizeTargetUri(options.targetUri);
        return this.request("/api/v1/search/find", {
            method: "POST",
            body: JSON.stringify({
                query,
                target_uri: normalizedTargetUri,
                limit: options.limit,
                score_threshold: options.scoreThreshold,
            }),
        });
    }
    async read(uri) {
        return this.request(`/api/v1/content/read?uri=${encodeURIComponent(uri)}`);
    }
    async createSession() {
        const result = await this.request("/api/v1/sessions", {
            method: "POST",
            body: JSON.stringify({}),
        });
        return result.session_id;
    }
    async addSessionMessage(sessionId, role, content) {
        await this.request(`/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`, {
            method: "POST",
            body: JSON.stringify({ role, content }),
        });
    }
    async extractSessionMemories(sessionId) {
        return this.request(`/api/v1/sessions/${encodeURIComponent(sessionId)}/extract`, { method: "POST", body: JSON.stringify({}) });
    }
    async deleteSession(sessionId) {
        await this.request(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
    }
    async deleteUri(uri) {
        await this.request(`/api/v1/fs?uri=${encodeURIComponent(uri)}&recursive=false`, {
            method: "DELETE",
        });
    }
}
// ---------------------------------------------------------------------------
// Memory ranking helpers (ported from openclaw-plugin/memory-ranking.ts)
// ---------------------------------------------------------------------------
function clampScore(value) {
    if (typeof value !== "number" || Number.isNaN(value))
        return 0;
    return Math.max(0, Math.min(1, value));
}
function normalizeDedupeText(text) {
    return text.toLowerCase().replace(/\s+/g, " ").trim();
}
function getMemoryDedupeKey(item) {
    const abstract = normalizeDedupeText(item.abstract ?? item.overview ?? "");
    const category = (item.category ?? "").toLowerCase() || "unknown";
    if (abstract)
        return `abstract:${category}:${abstract}`;
    return `uri:${item.uri}`;
}
function postProcessMemories(items, options) {
    const deduped = [];
    const seen = new Set();
    const sorted = [...items].sort((a, b) => clampScore(b.score) - clampScore(a.score));
    for (const item of sorted) {
        if (options.leafOnly && item.level !== 2)
            continue;
        if (clampScore(item.score) < options.scoreThreshold)
            continue;
        const key = getMemoryDedupeKey(item);
        if (seen.has(key))
            continue;
        seen.add(key);
        deduped.push(item);
        if (deduped.length >= options.limit)
            break;
    }
    return deduped;
}
function formatMemoryLines(items) {
    return items
        .map((item, i) => {
        const score = clampScore(item.score);
        const abstract = item.abstract?.trim() || item.overview?.trim() || item.uri;
        const category = item.category ?? "memory";
        return `${i + 1}. [${category}] ${abstract} (${(score * 100).toFixed(0)}%)`;
    })
        .join("\n");
}
// Query-aware ranking (ported from openclaw-plugin/memory-ranking.ts)
const PREFERENCE_QUERY_RE = /prefer|preference|favorite|favourite|like|偏好|喜欢|爱好|更倾向/i;
const TEMPORAL_QUERY_RE = /when|what time|date|day|month|year|yesterday|today|tomorrow|last|next|什么时候|何时|哪天|几月|几年|昨天|今天|明天/i;
const QUERY_TOKEN_RE = /[a-z0-9]{2,}/gi;
const QUERY_TOKEN_STOPWORDS = new Set([
    "what", "when", "where", "which", "who", "whom", "whose", "why", "how", "did", "does",
    "is", "are", "was", "were", "the", "and", "for", "with", "from", "that", "this", "your", "you",
]);
function buildQueryProfile(query) {
    const text = query.trim();
    const allTokens = text.toLowerCase().match(QUERY_TOKEN_RE) ?? [];
    const tokens = allTokens.filter((t) => !QUERY_TOKEN_STOPWORDS.has(t));
    return {
        tokens,
        wantsPreference: PREFERENCE_QUERY_RE.test(text),
        wantsTemporal: TEMPORAL_QUERY_RE.test(text),
    };
}
function lexicalOverlapBoost(tokens, text) {
    if (tokens.length === 0 || !text)
        return 0;
    const haystack = ` ${text.toLowerCase()} `;
    let matched = 0;
    for (const token of tokens.slice(0, 8)) {
        if (haystack.includes(token))
            matched += 1;
    }
    return Math.min(0.2, (matched / Math.min(tokens.length, 4)) * 0.2);
}
function rankForInjection(item, query) {
    const baseScore = clampScore(item.score);
    const abstract = (item.abstract ?? item.overview ?? "").trim();
    const leafBoost = item.level === 2 ? 0.12 : 0;
    const cat = (item.category ?? "").toLowerCase();
    const eventBoost = query.wantsTemporal && (cat === "events" || item.uri.includes("/events/")) ? 0.1 : 0;
    const prefBoost = query.wantsPreference && (cat === "preferences" || item.uri.includes("/preferences/")) ? 0.08 : 0;
    const overlapBoost = lexicalOverlapBoost(query.tokens, `${item.uri} ${abstract}`);
    return baseScore + leafBoost + eventBoost + prefBoost + overlapBoost;
}
function pickMemoriesForInjection(items, limit, queryText) {
    if (items.length === 0 || limit <= 0)
        return [];
    const query = buildQueryProfile(queryText);
    const sorted = [...items].sort((a, b) => rankForInjection(b, query) - rankForInjection(a, query));
    const deduped = [];
    const seen = new Set();
    for (const item of sorted) {
        const key = (item.abstract ?? item.overview ?? "").trim().toLowerCase() || item.uri;
        if (seen.has(key))
            continue;
        seen.add(key);
        deduped.push(item);
    }
    const leaves = deduped.filter((item) => item.level === 2);
    if (leaves.length >= limit)
        return leaves.slice(0, limit);
    const picked = [...leaves];
    const used = new Set(leaves.map((item) => item.uri));
    for (const item of deduped) {
        if (picked.length >= limit)
            break;
        if (used.has(item.uri))
            continue;
        picked.push(item);
    }
    return picked;
}
// ---------------------------------------------------------------------------
// Shared search helpers
// ---------------------------------------------------------------------------
async function searchBothScopes(client, query, limit) {
    const [userSettled, agentSettled] = await Promise.allSettled([
        client.find(query, { targetUri: "viking://user/memories", limit, scoreThreshold: 0 }),
        client.find(query, { targetUri: "viking://agent/memories", limit, scoreThreshold: 0 }),
    ]);
    const userResult = userSettled.status === "fulfilled" ? userSettled.value : { memories: [] };
    const agentResult = agentSettled.status === "fulfilled" ? agentSettled.value : { memories: [] };
    const all = [...(userResult.memories ?? []), ...(agentResult.memories ?? [])];
    // Deduplicate by URI and keep only leaf memories
    const unique = all.filter((m, i, self) => i === self.findIndex((o) => o.uri === m.uri));
    return unique.filter((m) => m.level === 2);
}
// ---------------------------------------------------------------------------
// MCP Server
// ---------------------------------------------------------------------------
const client = new OpenVikingClient(config.baseUrl, config.apiKey, config.agentId, config.timeoutMs);
const server = new McpServer({
    name: "openviking-memory",
    version: "0.1.0",
});
// -- Tool: memory_recall --------------------------------------------------
server.tool("memory_recall", "Search long-term memories from OpenViking. Use when you need past user preferences, facts, decisions, or any previously stored information.", {
    query: z.string().describe("Search query — describe what you want to recall"),
    limit: z.number().optional().describe("Max results to return (default: 6)"),
    score_threshold: z.number().optional().describe("Min relevance score 0-1 (default: 0.01)"),
    target_uri: z.string().optional().describe("Search scope URI, e.g. viking://user/memories"),
}, async ({ query, limit, score_threshold, target_uri }) => {
    const recallLimit = limit ?? config.recallLimit;
    const threshold = score_threshold ?? config.scoreThreshold;
    const candidateLimit = Math.max(recallLimit * 4, 20);
    let leafMemories;
    if (target_uri) {
        const result = await client.find(query, { targetUri: target_uri, limit: candidateLimit, scoreThreshold: 0 });
        leafMemories = (result.memories ?? []).filter((m) => m.level === 2);
    }
    else {
        leafMemories = await searchBothScopes(client, query, candidateLimit);
    }
    const processed = postProcessMemories(leafMemories, { limit: candidateLimit, scoreThreshold: threshold });
    const memories = pickMemoriesForInjection(processed, recallLimit, query);
    if (memories.length === 0) {
        return { content: [{ type: "text", text: "No relevant memories found in OpenViking." }] };
    }
    // Read full content for leaf memories
    const lines = await Promise.all(memories.map(async (item) => {
        if (item.level === 2) {
            try {
                const content = await client.read(item.uri);
                if (content?.trim())
                    return `- [${item.category ?? "memory"}] ${content.trim()}`;
            }
            catch { /* fallback */ }
        }
        return `- [${item.category ?? "memory"}] ${item.abstract ?? item.uri}`;
    }));
    return {
        content: [{
                type: "text",
                text: `Found ${memories.length} relevant memories:\n\n${lines.join("\n")}\n\n---\n${formatMemoryLines(memories)}`,
            }],
    };
});
// -- Tool: memory_store ---------------------------------------------------
server.tool("memory_store", "Store information into OpenViking long-term memory. Use when the user says 'remember this', shares preferences, important facts, decisions, or any information worth persisting across sessions.", {
    text: z.string().describe("The information to store as memory"),
    role: z.string().optional().describe("Message role: 'user' (default) or 'assistant'"),
}, async ({ text, role }) => {
    const msgRole = role || "user";
    let sessionId;
    try {
        sessionId = await client.createSession();
        await client.addSessionMessage(sessionId, msgRole, text);
        const extracted = await client.extractSessionMemories(sessionId);
        if (extracted.length === 0) {
            return {
                content: [{
                        type: "text",
                        text: "Memory stored but extraction returned 0 memories. The text may be too short or not contain extractable information. Check OpenViking server logs for details.",
                    }],
            };
        }
        return {
            content: [{
                    type: "text",
                    text: `Successfully extracted ${extracted.length} memory/memories from the provided text and stored them in OpenViking.`,
                }],
        };
    }
    finally {
        if (sessionId) {
            await client.deleteSession(sessionId).catch(() => { });
        }
    }
});
// -- Tool: memory_forget --------------------------------------------------
server.tool("memory_forget", "Delete a memory from OpenViking. Provide an exact URI for direct deletion, or a search query to find and delete matching memories.", {
    uri: z.string().optional().describe("Exact viking:// memory URI to delete"),
    query: z.string().optional().describe("Search query to find the memory to delete"),
    target_uri: z.string().optional().describe("Search scope URI (default: viking://user/memories)"),
}, async ({ uri, query, target_uri }) => {
    // Direct URI deletion
    if (uri) {
        if (!isMemoryUri(uri)) {
            return { content: [{ type: "text", text: `Refusing to delete non-memory URI: ${uri}` }] };
        }
        await client.deleteUri(uri);
        return { content: [{ type: "text", text: `Deleted memory: ${uri}` }] };
    }
    if (!query) {
        return { content: [{ type: "text", text: "Please provide either a uri or query parameter." }] };
    }
    // Search then delete
    const candidateLimit = 20;
    let candidates;
    if (target_uri) {
        const result = await client.find(query, { targetUri: target_uri, limit: candidateLimit, scoreThreshold: 0 });
        candidates = postProcessMemories(result.memories ?? [], {
            limit: candidateLimit,
            scoreThreshold: config.scoreThreshold,
            leafOnly: true,
        }).filter((item) => isMemoryUri(item.uri));
    }
    else {
        const leafMemories = await searchBothScopes(client, query, candidateLimit);
        candidates = postProcessMemories(leafMemories, {
            limit: candidateLimit,
            scoreThreshold: config.scoreThreshold,
            leafOnly: true,
        }).filter((item) => isMemoryUri(item.uri));
    }
    if (candidates.length === 0) {
        return { content: [{ type: "text", text: "No matching memories found. Try a more specific query." }] };
    }
    // Auto-delete if single strong match
    const top = candidates[0];
    if (candidates.length === 1 && clampScore(top.score) >= 0.85) {
        await client.deleteUri(top.uri);
        return { content: [{ type: "text", text: `Deleted memory: ${top.uri}` }] };
    }
    // List candidates for confirmation
    const list = candidates
        .map((item) => `- ${item.uri} — ${item.abstract?.trim() || "?"} (${(clampScore(item.score) * 100).toFixed(0)}%)`)
        .join("\n");
    return {
        content: [{
                type: "text",
                text: `Found ${candidates.length} candidate memories. Please specify the exact URI to delete:\n\n${list}`,
            }],
    };
});
// -- Tool: memory_health --------------------------------------------------
server.tool("memory_health", "Check whether the OpenViking memory server is reachable and healthy.", {}, async () => {
    const ok = await client.healthCheck();
    return {
        content: [{
                type: "text",
                text: ok
                    ? `OpenViking is healthy (${config.baseUrl})`
                    : `OpenViking is unreachable at ${config.baseUrl}. Please check if the server is running.`,
            }],
    };
});
// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------
const transport = new StdioServerTransport();
await server.connect(transport);
