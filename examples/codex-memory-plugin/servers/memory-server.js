import { createHash } from "node:crypto";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
// Shared with scripts/*.mjs so hook surface and MCP server resolve identity identically.
// Compiled output lives in servers/memory-server.js, so the relative path stays valid.
// @ts-ignore -- pure JS module, no type declarations
import { loadConfig } from "../scripts/config.mjs";
function md5Short(value) {
    return createHash("md5").update(value).digest("hex").slice(0, 12);
}
function clampScore(value) {
    if (typeof value !== "number" || Number.isNaN(value))
        return 0;
    return Math.max(0, Math.min(1, value));
}
function isMemoryUri(uri) {
    return /^viking:\/\/(?:user|agent)\/[^/]+\/memories(?:\/|$)/.test(uri);
}
function totalCommitMemories(result) {
    return Object.values(result.memories_extracted ?? {}).reduce((sum, count) => sum + count, 0);
}
function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}
const shared = loadConfig();
const config = {
    configPath: shared.configPath,
    baseUrl: shared.baseUrl,
    apiKey: shared.apiKey,
    // The MCP server defaults missing account/user to "default" rather than "" so the
    // X-OpenViking-* headers carry a value; hooks leave them empty (server-side default).
    accountId: shared.account || "default",
    userId: shared.user || "default",
    agentId: shared.agentId || "codex",
    timeoutMs: shared.timeoutMs,
    recallLimit: shared.recallLimit,
    scoreThreshold: shared.scoreThreshold,
};
if (!config.baseUrl) {
    process.stderr.write("[openviking-memory] No OpenViking URL resolved. Set OPENVIKING_URL or provide ovcli.conf / ov.conf.\n");
    process.exit(1);
}
class OpenVikingClient {
    baseUrl;
    apiKey;
    accountId;
    userId;
    agentId;
    timeoutMs;
    runtimeIdentity = null;
    constructor(baseUrl, apiKey, accountId, userId, agentId, timeoutMs) {
        this.baseUrl = baseUrl;
        this.apiKey = apiKey;
        this.accountId = accountId;
        this.userId = userId;
        this.agentId = agentId;
        this.timeoutMs = timeoutMs;
    }
    async request(path, init = {}) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), this.timeoutMs);
        try {
            const headers = new Headers(init.headers ?? {});
            if (this.apiKey) {
                // OV Cloud only accepts Authorization: Bearer; self-hosted servers
                // still accept X-API-Key, so we emit both for transition compat.
                headers.set("Authorization", `Bearer ${this.apiKey}`);
                headers.set("X-API-Key", this.apiKey);
            }
            if (this.accountId)
                headers.set("X-OpenViking-Account", this.accountId);
            if (this.userId)
                headers.set("X-OpenViking-User", this.userId);
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
    async getRuntimeIdentity() {
        if (this.runtimeIdentity)
            return this.runtimeIdentity;
        const fallback = { userId: this.userId || "default", agentId: this.agentId || "default" };
        try {
            const status = await this.request("/api/v1/system/status");
            const userId = typeof status.user === "string" && status.user.trim() ? status.user.trim() : fallback.userId;
            this.runtimeIdentity = { userId, agentId: this.agentId || "default" };
            return this.runtimeIdentity;
        }
        catch {
            this.runtimeIdentity = fallback;
            return fallback;
        }
    }
    async normalizeMemoryTargetUri(targetUri) {
        const trimmed = targetUri.trim().replace(/\/+$/, "");
        const match = trimmed.match(/^viking:\/\/(user|agent)\/memories(?:\/(.*))?$/);
        if (!match)
            return trimmed;
        const scope = match[1];
        const rest = match[2] ? `/${match[2]}` : "";
        const identity = await this.getRuntimeIdentity();
        const space = scope === "user" ? identity.userId : md5Short(`${identity.userId}:${identity.agentId}`);
        return `viking://${scope}/${space}/memories${rest}`;
    }
    async find(query, targetUri, limit, scoreThreshold) {
        const normalizedTargetUri = await this.normalizeMemoryTargetUri(targetUri);
        return this.request("/api/v1/search/find", {
            method: "POST",
            body: JSON.stringify({
                query,
                target_uri: normalizedTargetUri,
                limit,
                score_threshold: scoreThreshold,
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
    async commitSession(sessionId) {
        const result = await this.request(`/api/v1/sessions/${encodeURIComponent(sessionId)}/commit`, { method: "POST", body: JSON.stringify({}) });
        if (!result.task_id)
            return result;
        const deadline = Date.now() + Math.max(this.timeoutMs, 30000);
        while (Date.now() < deadline) {
            await sleep(500);
            const task = await this.getTask(result.task_id).catch(() => null);
            if (!task)
                break;
            if (task.status === "completed") {
                const taskResult = (task.result ?? {});
                return {
                    ...result,
                    status: "completed",
                    memories_extracted: (taskResult.memories_extracted ?? {}),
                };
            }
            if (task.status === "failed")
                return { ...result, status: "failed", error: task.error };
        }
        return { ...result, status: "timeout" };
    }
    async getTask(taskId) {
        return this.request(`/api/v1/tasks/${encodeURIComponent(taskId)}`, { method: "GET" });
    }
    async deleteSession(sessionId) {
        await this.request(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
    }
    async deleteUri(uri) {
        await this.request(`/api/v1/fs?uri=${encodeURIComponent(uri)}&recursive=false`, { method: "DELETE" });
    }
}
function formatMemoryResults(items) {
    return items
        .map((item, index) => {
        const summary = item.abstract?.trim() || item.overview?.trim() || item.uri;
        const score = Math.round(clampScore(item.score) * 100);
        return `${index + 1}. ${summary}\n   URI: ${item.uri}\n   Score: ${score}%`;
    })
        .join("\n\n");
}
const client = new OpenVikingClient(config.baseUrl, config.apiKey, config.accountId, config.userId, config.agentId, config.timeoutMs);
const server = new McpServer({ name: "openviking-memory-codex", version: "0.1.0" });
server.tool("openviking_recall", "Find OpenViking long-term memory.", {
    query: z.string().describe("Find query"),
    target_uri: z.string().optional().describe("Find scope URI, default viking://user/memories"),
    limit: z.number().optional().describe("Max results, default 6"),
    score_threshold: z.number().optional().describe("Minimum relevance score 0-1, default 0.01"),
}, async ({ query, target_uri, limit, score_threshold }) => {
    const recallLimit = limit ?? config.recallLimit;
    const threshold = score_threshold ?? config.scoreThreshold;
    const result = await client.find(query, target_uri ?? "viking://user/memories", recallLimit, threshold);
    const items = [...(result.memories ?? []), ...(result.resources ?? []), ...(result.skills ?? [])]
        .filter((item) => clampScore(item.score) >= threshold)
        .sort((left, right) => clampScore(right.score) - clampScore(left.score))
        .slice(0, recallLimit);
    if (items.length === 0) {
        return { content: [{ type: "text", text: "No relevant OpenViking memories found." }] };
    }
    return { content: [{ type: "text", text: formatMemoryResults(items) }] };
});
server.tool("openviking_store", "Store information in OpenViking long-term memory.", {
    text: z.string().describe("Information to store"),
    role: z.string().optional().describe("Message role, default user"),
}, async ({ text, role }) => {
    let sessionId;
    try {
        sessionId = await client.createSession();
        await client.addSessionMessage(sessionId, role || "user", text);
        const result = await client.commitSession(sessionId);
        const count = totalCommitMemories(result);
        if (result.status === "failed") {
            return { content: [{ type: "text", text: `Memory extraction failed: ${String(result.error)}` }] };
        }
        if (result.status === "timeout") {
            return {
                content: [{
                        type: "text",
                        text: `Memory extraction is still running (task_id=${result.task_id ?? "unknown"}).`,
                    }],
            };
        }
        if (count === 0) {
            return {
                content: [{
                        type: "text",
                        text: "Committed session, but OpenViking extracted 0 memory item(s).",
                    }],
            };
        }
        return { content: [{ type: "text", text: `Stored memory. Extracted ${count} item(s).` }] };
    }
    finally {
        if (sessionId)
            await client.deleteSession(sessionId).catch(() => { });
    }
});
server.tool("openviking_forget", "Delete an exact OpenViking memory URI. Use openviking_recall first if you only have a query.", {
    uri: z.string().describe("Exact memory URI to delete"),
}, async ({ uri }) => {
    if (!isMemoryUri(uri)) {
        return { content: [{ type: "text", text: `Refusing to delete non-memory URI: ${uri}` }] };
    }
    await client.deleteUri(uri);
    return { content: [{ type: "text", text: `Deleted memory: ${uri}` }] };
});
server.tool("openviking_health", "Check whether the OpenViking server is reachable.", {}, async () => {
    const ok = await client.healthCheck();
    const text = ok
        ? `OpenViking is reachable at ${config.baseUrl}.`
        : `OpenViking is unreachable at ${config.baseUrl}.`;
    return { content: [{ type: "text", text }] };
});
const transport = new StdioServerTransport();
await server.connect(transport);
