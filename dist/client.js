import { randomUUID } from "node:crypto";
import { once } from "node:events";
import { createWriteStream } from "node:fs";
import { mkdtemp, readdir, readFile, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join, relative } from "node:path";
import { Zip, ZipDeflate } from "fflate";
const DEFAULT_WAIT_REQUEST_TIMEOUT_MS = 120_000;
export const DEFAULT_PHASE2_POLL_TIMEOUT_MS = 300_000;
const WAIT_REQUEST_TIMEOUT_BUFFER_MS = 5_000;
function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}
const MEMORY_URI_PATTERNS = [
    /^viking:\/\/user\/(?:[^/]+(?:\/agent\/[^/]+)?\/)?memories(?:\/|$)/,
    /^viking:\/\/agent\/(?:[^/]+(?:\/user\/[^/]+)?\/)?memories(?:\/|$)/,
];
const USER_STRUCTURE_DIRS = new Set(["memories", "profile.md", ".abstract.md", ".overview.md"]);
const AGENT_STRUCTURE_DIRS = new Set([
    "memories",
    "skills",
    "instructions",
    "workspaces",
    ".abstract.md",
    ".overview.md",
]);
const REMOTE_RESOURCE_PREFIXES = ["http://", "https://", "git@", "ssh://", "git://"];
export function isMemoryUri(uri) {
    return MEMORY_URI_PATTERNS.some((pattern) => pattern.test(uri));
}
function isRemoteResourceSource(source) {
    return REMOTE_RESOURCE_PREFIXES.some((prefix) => source.startsWith(prefix));
}
function toBlobPart(value) {
    return value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength);
}
function resolveWaitRequestTimeoutMs(defaultTimeoutMs, waitTimeoutSeconds) {
    const requestedMs = typeof waitTimeoutSeconds === "number" && Number.isFinite(waitTimeoutSeconds) && waitTimeoutSeconds > 0
        ? Math.ceil(waitTimeoutSeconds * 1000) + WAIT_REQUEST_TIMEOUT_BUFFER_MS
        : DEFAULT_WAIT_REQUEST_TIMEOUT_MS;
    return Math.max(defaultTimeoutMs, requestedMs);
}
async function cleanupUploadTempPath(path) {
    if (!path) {
        return;
    }
    await rm(path, { force: true }).catch(() => undefined);
    await rm(dirname(path), { recursive: true, force: true }).catch(() => undefined);
}
export class OpenVikingClient {
    baseUrl;
    apiKey;
    defaultAgentId;
    timeoutMs;
    accountId;
    userId;
    routingDebugLog;
    isolateUserScopeByAgent;
    isolateAgentScopeByUser;
    identityCache = new Map();
    constructor(baseUrl, apiKey, defaultAgentId, timeoutMs, 
    /** When set, sent so ROOT keys or trusted deployments can select tenant identity. */
    accountId = "", userId = "", 
    /** When set, logs routing for find + session writes (tenant headers + paths; never apiKey). */
    routingDebugLog, isolateUserScopeByAgent = false, isolateAgentScopeByUser = true) {
        this.baseUrl = baseUrl;
        this.apiKey = apiKey;
        this.defaultAgentId = defaultAgentId;
        this.timeoutMs = timeoutMs;
        this.accountId = accountId;
        this.userId = userId;
        this.routingDebugLog = routingDebugLog;
        this.isolateUserScopeByAgent = isolateUserScopeByAgent;
        this.isolateAgentScopeByUser = isolateAgentScopeByUser;
    }
    getDefaultAgentId() {
        return this.defaultAgentId;
    }
    resolveEffectiveAgentId(agentId) {
        const explicit = agentId?.trim();
        if (explicit) {
            return explicit;
        }
        const prefix = this.defaultAgentId.trim();
        return prefix ? `${prefix}_main` : "main";
    }
    async getResolvedIdentity(agentId) {
        return this.getRuntimeIdentity(agentId);
    }
    resolveTenantHeaders() {
        const apiKey = this.apiKey.trim();
        const accountId = this.accountId.trim();
        const userId = this.userId.trim();
        return {
            ...(apiKey ? { apiKey } : {}),
            ...(accountId ? { accountId } : {}),
            ...(userId ? { userId } : {}),
        };
    }
    async emitRoutingDebug(label, detail, agentId) {
        if (!this.routingDebugLog) {
            return;
        }
        const effectiveAgentId = this.resolveEffectiveAgentId(agentId);
        const identity = await this.getRuntimeIdentity(agentId);
        const tenantHeaders = this.resolveTenantHeaders();
        this.routingDebugLog(`openviking: ${label} ` +
            JSON.stringify({
                ...detail,
                X_OpenViking_Agent: effectiveAgentId,
                X_OpenViking_Account: tenantHeaders.accountId ?? null,
                X_OpenViking_User: tenantHeaders.userId ?? null,
                resolved_user_id: identity.userId,
                session_vfs_hint: detail.sessionId
                    ? `viking://session/${String(detail.sessionId)}`
                    : undefined,
            }));
    }
    async request(path, init = {}, agentId, requestTimeoutMs) {
        const effectiveAgentId = this.resolveEffectiveAgentId(agentId);
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), requestTimeoutMs ?? this.timeoutMs);
        try {
            const headers = new Headers(init.headers ?? {});
            const tenantHeaders = this.resolveTenantHeaders();
            if (tenantHeaders.apiKey) {
                headers.set("X-API-Key", tenantHeaders.apiKey);
            }
            if (tenantHeaders.accountId) {
                headers.set("X-OpenViking-Account", tenantHeaders.accountId);
            }
            if (tenantHeaders.userId) {
                headers.set("X-OpenViking-User", tenantHeaders.userId);
            }
            if (effectiveAgentId) {
                headers.set("X-OpenViking-Agent", effectiveAgentId);
            }
            if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
                headers.set("Content-Type", "application/json");
            }
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
        await this.request("/health");
    }
    async getRuntimeIdentity(agentId) {
        const effectiveAgentId = this.resolveEffectiveAgentId(agentId);
        const cached = this.identityCache.get(effectiveAgentId);
        if (cached) {
            return cached;
        }
        const fallback = { userId: "default", agentId: effectiveAgentId };
        try {
            const status = await this.request("/api/v1/system/status", {}, agentId);
            const userId = typeof status.user === "string" && status.user.trim() ? status.user.trim() : "default";
            const identity = { userId, agentId: effectiveAgentId };
            this.identityCache.set(effectiveAgentId, identity);
            return identity;
        }
        catch {
            this.identityCache.set(effectiveAgentId, fallback);
            return fallback;
        }
    }
    async buildCanonicalRoot(scope, agentId) {
        const identity = await this.getRuntimeIdentity(agentId);
        if (scope === "user") {
            const root = this.isolateUserScopeByAgent
                ? `viking://user/${identity.userId}/agent/${identity.agentId}`
                : `viking://user/${identity.userId}`;
            return root;
        }
        const root = this.isolateAgentScopeByUser
            ? `viking://agent/${identity.agentId}/user/${identity.userId}`
            : `viking://agent/${identity.agentId}`;
        return root;
    }
    async normalizeTargetUri(targetUri, agentId) {
        const trimmed = targetUri.trim().replace(/\/+$/, "");
        const match = trimmed.match(/^viking:\/\/(user|agent)(?:\/(.*))?$/);
        if (!match) {
            return trimmed;
        }
        const scope = match[1];
        const rawRest = (match[2] ?? "").trim();
        if (!rawRest) {
            return trimmed;
        }
        const parts = rawRest.split("/").filter(Boolean);
        if (parts.length === 0) {
            return trimmed;
        }
        const reservedDirs = scope === "user" ? USER_STRUCTURE_DIRS : AGENT_STRUCTURE_DIRS;
        if (!reservedDirs.has(parts[0])) {
            return trimmed;
        }
        const root = await this.buildCanonicalRoot(scope, agentId);
        return `${root}/${parts.join("/")}`;
    }
    async find(query, options, agentId) {
        const normalizedTargetUri = await this.normalizeTargetUri(options.targetUri, agentId);
        const body = {
            query,
            target_uri: normalizedTargetUri,
            limit: options.limit,
            score_threshold: options.scoreThreshold,
        };
        const effectiveAgentId = this.resolveEffectiveAgentId(agentId);
        const identity = await this.getRuntimeIdentity(agentId);
        const tenantHeaders = this.resolveTenantHeaders();
        this.routingDebugLog?.(`openviking: find POST ${this.baseUrl}/api/v1/search/find ` +
            JSON.stringify({
                X_OpenViking_Agent: effectiveAgentId,
                X_OpenViking_Account: tenantHeaders.accountId ?? null,
                X_OpenViking_User: tenantHeaders.userId ?? null,
                resolved_user_id: identity.userId,
                target_uri: normalizedTargetUri,
                target_uri_input: options.targetUri,
                query: query.length > 4000
                    ? `${query.slice(0, 4000)}…(+${query.length - 4000} more chars)`
                    : query,
                limit: body.limit,
                score_threshold: body.score_threshold ?? null,
            }));
        return this.request("/api/v1/search/find", {
            method: "POST",
            body: JSON.stringify(body),
        }, agentId);
    }
    async read(uri, agentId) {
        return this.request(`/api/v1/content/read?uri=${encodeURIComponent(uri)}`, {}, agentId);
    }
    async readToolResult(sessionId, toolResultId, options, agentId) {
        const params = new URLSearchParams();
        if (options?.offset !== undefined)
            params.set("offset", String(options.offset));
        if (options?.limit !== undefined)
            params.set("limit", String(options.limit));
        if (options?.includeMetadata !== undefined) {
            params.set("include_metadata", String(options.includeMetadata));
        }
        const query = params.toString();
        return this.request(`/api/v1/sessions/${encodeURIComponent(sessionId)}/tool-results/${encodeURIComponent(toolResultId)}${query ? `?${query}` : ""}`, {}, agentId);
    }
    async searchToolResult(sessionId, toolResultId, queryText, options, agentId) {
        const params = new URLSearchParams({ q: queryText });
        if (options?.limit !== undefined)
            params.set("limit", String(options.limit));
        if (options?.contextChars !== undefined) {
            params.set("context_chars", String(options.contextChars));
        }
        return this.request(`/api/v1/sessions/${encodeURIComponent(sessionId)}/tool-results/${encodeURIComponent(toolResultId)}/search?${params.toString()}`, {}, agentId);
    }
    async listToolResults(sessionId, options, agentId) {
        const params = new URLSearchParams();
        if (options?.toolName)
            params.set("tool_name", options.toolName);
        if (options?.limit !== undefined)
            params.set("limit", String(options.limit));
        const query = params.toString();
        return this.request(`/api/v1/sessions/${encodeURIComponent(sessionId)}/tool-results${query ? `?${query}` : ""}`, {}, agentId);
    }
    async uploadTempFile(filePath, agentId) {
        const fileBytes = await readFile(filePath);
        const form = new FormData();
        form.append("file", new Blob([toBlobPart(fileBytes)], { type: "application/octet-stream" }), basename(filePath));
        const result = await this.request("/api/v1/resources/temp_upload", { method: "POST", body: form }, agentId);
        if (!result.temp_file_id) {
            throw new Error("OpenViking temp upload did not return temp_file_id");
        }
        return result.temp_file_id;
    }
    async zipDirectoryForUpload(dirPath) {
        const rootStats = await stat(dirPath);
        if (!rootStats.isDirectory()) {
            throw new Error(`Not a directory: ${dirPath}`);
        }
        const zipDir = await mkdtemp(join(tmpdir(), "openviking-openclaw-upload-"));
        const zipPath = join(zipDir, `${basename(dirPath).replace(/[^a-zA-Z0-9._-]/g, "_")}-${randomUUID()}.zip`);
        const output = createWriteStream(zipPath);
        const outputClosed = once(output, "close");
        const outputErrored = once(output, "error").then(([err]) => Promise.reject(err));
        const zip = new Zip((err, chunk, final) => {
            if (err) {
                output.destroy(err);
                return;
            }
            if (chunk?.length) {
                output.write(Buffer.from(chunk));
            }
            if (final) {
                output.end();
            }
        });
        const walk = async (currentDir) => {
            const entries = await readdir(currentDir, { withFileTypes: true });
            for (const entry of entries) {
                const fullPath = join(currentDir, entry.name);
                if (entry.isDirectory()) {
                    await walk(fullPath);
                    continue;
                }
                if (!entry.isFile()) {
                    continue;
                }
                const relPath = relative(dirPath, fullPath).replace(/\\/g, "/");
                if (!relPath || relPath.startsWith("../") || relPath.includes("/../")) {
                    throw new Error(`Unsafe relative path while zipping: ${relPath}`);
                }
                const file = new ZipDeflate(relPath);
                zip.add(file);
                file.push(new Uint8Array(await readFile(fullPath)), true);
            }
        };
        try {
            await walk(dirPath);
            zip.end();
            await Promise.race([outputClosed, outputErrored]);
        }
        catch (err) {
            zip.terminate();
            output.destroy(err);
            await cleanupUploadTempPath(zipPath);
            throw err;
        }
        return zipPath;
    }
    async addResource(input, agentId) {
        const pathOrUrl = input.pathOrUrl.trim();
        if (!pathOrUrl) {
            throw new Error("pathOrUrl is required");
        }
        if (input.to && input.parent) {
            throw new Error("Cannot specify both 'to' and 'parent'.");
        }
        const body = {
            to: input.to,
            parent: input.parent,
            reason: input.reason ?? "",
            instruction: input.instruction ?? "",
            wait: input.wait ?? false,
            timeout: input.timeout,
            strict: input.strict ?? false,
            ignore_dirs: input.ignoreDirs,
            include: input.include,
            exclude: input.exclude,
        };
        if (typeof input.preserveStructure === "boolean") {
            body.preserve_structure = input.preserveStructure;
        }
        let cleanupPath;
        const requestTimeoutMs = input.wait ? resolveWaitRequestTimeoutMs(this.timeoutMs, input.timeout) : undefined;
        try {
            if (isRemoteResourceSource(pathOrUrl)) {
                body.path = pathOrUrl;
            }
            else {
                const localStats = await stat(pathOrUrl);
                let uploadPath = pathOrUrl;
                if (localStats.isDirectory()) {
                    uploadPath = await this.zipDirectoryForUpload(pathOrUrl);
                    cleanupPath = uploadPath;
                    body.source_name = basename(pathOrUrl);
                }
                else if (!localStats.isFile()) {
                    throw new Error(`Path is not a file or directory: ${pathOrUrl}`);
                }
                body.temp_file_id = await this.uploadTempFile(uploadPath, agentId);
            }
            return this.request("/api/v1/resources", { method: "POST", body: JSON.stringify(body) }, agentId, requestTimeoutMs);
        }
        finally {
            await cleanupUploadTempPath(cleanupPath);
        }
    }
    async addSkill(input, agentId) {
        const hasPath = typeof input.path === "string" && input.path.trim().length > 0;
        const hasData = input.data !== undefined && input.data !== null;
        if (hasPath === hasData) {
            throw new Error("Provide exactly one of 'path' or 'data' for skill import.");
        }
        const body = {
            wait: input.wait ?? false,
            timeout: input.timeout,
        };
        let cleanupPath;
        const requestTimeoutMs = input.wait ? resolveWaitRequestTimeoutMs(this.timeoutMs, input.timeout) : undefined;
        try {
            if (hasPath) {
                const skillPath = input.path.trim();
                const localStats = await stat(skillPath);
                let uploadPath = skillPath;
                if (localStats.isDirectory()) {
                    uploadPath = await this.zipDirectoryForUpload(skillPath);
                    cleanupPath = uploadPath;
                }
                else if (!localStats.isFile()) {
                    throw new Error(`Path is not a file or directory: ${skillPath}`);
                }
                body.temp_file_id = await this.uploadTempFile(uploadPath, agentId);
            }
            else {
                body.data = input.data;
            }
            return this.request("/api/v1/skills", { method: "POST", body: JSON.stringify(body) }, agentId, requestTimeoutMs);
        }
        finally {
            await cleanupUploadTempPath(cleanupPath);
        }
    }
    async addSessionMessage(sessionId, role, parts, agentId, createdAt, roleId) {
        const body = { role, parts };
        if (createdAt) {
            body.created_at = createdAt;
        }
        if (roleId) {
            body.role_id = roleId;
        }
        await this.emitRoutingDebug("session message POST (with parts)", {
            path: `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`,
            sessionId,
            role,
            role_id: roleId ?? null,
            partCount: parts.length,
            created_at: createdAt ?? null,
        }, agentId);
        await this.request(`/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`, {
            method: "POST",
            body: JSON.stringify(body),
        }, agentId);
    }
    /** GET session — server auto-creates if absent; returns session meta including message stats and token usage. */
    async getSession(sessionId, agentId) {
        return this.request(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, { method: "GET" }, agentId);
    }
    /**
     * Commit a session: archive (Phase 1) and extract memories (Phase 2).
     *
     * wait=false (default): returns immediately after Phase 1 with task_id.
     * wait=true: after Phase 1, polls GET /tasks/{task_id} until Phase 2
     *   completes (or times out), then returns the merged result.
     */
    async commitSession(sessionId, options) {
        const keepRecentCount = options?.keepRecentCount != null && Number.isFinite(options.keepRecentCount)
            ? Math.max(0, Math.floor(options.keepRecentCount))
            : 0;
        await this.emitRoutingDebug("session commit POST (archive + memory extraction)", {
            path: `/api/v1/sessions/${encodeURIComponent(sessionId)}/commit`,
            sessionId,
            wait: options?.wait ?? false,
            keepRecentCount,
        }, options?.agentId);
        const body = {};
        if (keepRecentCount > 0) {
            body.keep_recent_count = keepRecentCount;
        }
        const result = await this.request(`/api/v1/sessions/${encodeURIComponent(sessionId)}/commit`, { method: "POST", body: JSON.stringify(body) }, options?.agentId);
        if (!options?.wait || !result.task_id) {
            return result;
        }
        // Client-side poll until Phase 2 finishes
        const deadline = Date.now() + (options.timeoutMs ?? DEFAULT_PHASE2_POLL_TIMEOUT_MS);
        const pollInterval = 500;
        while (Date.now() < deadline) {
            await sleep(pollInterval);
            const task = await this.getTask(result.task_id, options.agentId).catch(() => null);
            if (!task)
                break;
            if (task.status === "completed") {
                const taskResult = (task.result ?? {});
                const memoriesExtracted = (taskResult.memories_extracted ?? {});
                result.status = "completed";
                result.memories_extracted = memoriesExtracted;
                return result;
            }
            if (task.status === "failed") {
                result.status = "failed";
                result.error = task.error;
                return result;
            }
        }
        result.status = "timeout";
        return result;
    }
    /** Poll a background task by ID. */
    async getTask(taskId, agentId) {
        return this.request(`/api/v1/tasks/${encodeURIComponent(taskId)}`, { method: "GET" }, agentId);
    }
    async getSessionContext(sessionId, tokenBudget = 128_000, agentId) {
        return this.request(`/api/v1/sessions/${encodeURIComponent(sessionId)}/context?token_budget=${tokenBudget}`, { method: "GET" }, agentId);
    }
    async getSessionArchive(sessionId, archiveId, agentId) {
        return this.request(`/api/v1/sessions/${encodeURIComponent(sessionId)}/archives/${encodeURIComponent(archiveId)}`, { method: "GET" }, agentId);
    }
    async grepSessionArchives(sessionId, pattern, options = {}) {
        const baseUri = `viking://session/${sessionId}/history`;
        const uri = options.archiveId ? `${baseUri}/${options.archiveId}` : baseUri;
        return this.request("/api/v1/search/grep", {
            method: "POST",
            body: JSON.stringify({
                uri,
                pattern,
                case_insensitive: options.caseInsensitive ?? true,
                ...(options.nodeLimit !== undefined ? { node_limit: options.nodeLimit } : {}),
                ...(options.levelLimit !== undefined ? { level_limit: options.levelLimit } : {}),
            }),
        }, options.agentId);
    }
    async deleteSession(sessionId, agentId) {
        await this.request(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" }, agentId);
    }
    async deleteUri(uri, agentId) {
        await this.request(`/api/v1/fs?uri=${encodeURIComponent(uri)}&recursive=false`, {
            method: "DELETE",
        }, agentId);
    }
}
