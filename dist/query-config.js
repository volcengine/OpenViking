import { mkdir, readFile, rename, stat, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { normalizeRecallResourceTypes } from "./registries/recall-resource-types.js";
const DEFAULT_CANDIDATE_MULTIPLIER = 4;
const DEFAULT_OV_SEARCH_LIMIT = 10;
const DEFAULT_RANKING_WEIGHTS = {
    baseScore: 1,
    leaf: 0.12,
    event: 0.1,
    preference: 0.08,
    lexicalOverlapMax: 0.2,
};
function toNumber(value) {
    if (typeof value === "number" && Number.isFinite(value))
        return value;
    if (typeof value === "string" && value.trim() !== "") {
        const parsed = Number(value);
        if (Number.isFinite(parsed))
            return parsed;
    }
    return undefined;
}
function clampInteger(value, min, max) {
    const n = toNumber(value);
    if (n === undefined)
        return undefined;
    return Math.max(min, Math.min(max, Math.floor(n)));
}
function clampNumber(value, min, max) {
    const n = toNumber(value);
    if (n === undefined)
        return undefined;
    return Math.max(min, Math.min(max, n));
}
function shallowNumberRecord(value, min, max) {
    if (!value || typeof value !== "object" || Array.isArray(value))
        return undefined;
    const result = {};
    for (const [key, raw] of Object.entries(value)) {
        const n = clampNumber(raw, min, max);
        if (n !== undefined)
            result[key] = n;
    }
    return result;
}
export function normalizeRuntimeQueryParams(value) {
    const warnings = [];
    const params = {};
    const recallLimit = clampInteger(value.recallLimit, 1, 50);
    if (recallLimit !== undefined)
        params.recallLimit = recallLimit;
    const candidateMultiplier = clampInteger(value.candidateMultiplier, 1, 20);
    if (candidateMultiplier !== undefined)
        params.candidateMultiplier = candidateMultiplier;
    const candidateLimit = clampInteger(value.candidateLimit, 1, 200);
    if (candidateLimit !== undefined)
        params.candidateLimit = candidateLimit;
    const scoreThreshold = clampNumber(value.scoreThreshold, 0, 1);
    if (scoreThreshold !== undefined)
        params.scoreThreshold = scoreThreshold;
    const maxInjectedChars = clampInteger(value.maxInjectedChars, 100, 50_000);
    if (maxInjectedChars !== undefined)
        params.maxInjectedChars = maxInjectedChars;
    const ovSearchLimit = clampInteger(value.ovSearchLimit, 1, 100);
    if (ovSearchLimit !== undefined)
        params.ovSearchLimit = ovSearchLimit;
    if (typeof value.recallPreferAbstract === "boolean")
        params.recallPreferAbstract = value.recallPreferAbstract;
    if (value.resourceTypes !== undefined)
        params.resourceTypes = normalizeRecallResourceTypes(value.resourceTypes);
    if (typeof value.targetUri === "string" && value.targetUri.trim()) {
        const targetUri = value.targetUri.trim();
        if (!targetUri.startsWith("viking://"))
            throw new Error("targetUri must start with viking://");
        params.targetUri = targetUri;
    }
    const rankingWeights = shallowNumberRecord(value.rankingWeights, 0, 2);
    if (rankingWeights)
        params.rankingWeights = rankingWeights;
    const resourceTypeWeights = shallowNumberRecord(value.resourceTypeWeights, -1, 2);
    if (resourceTypeWeights)
        params.resourceTypeWeights = resourceTypeWeights;
    const categoryWeights = shallowNumberRecord(value.categoryWeights, -1, 2);
    if (categoryWeights)
        params.categoryWeights = categoryWeights;
    if (params.candidateLimit !== undefined && params.recallLimit !== undefined && params.candidateLimit < params.recallLimit) {
        params.candidateLimit = params.recallLimit;
        warnings.push("candidateLimit was raised to recallLimit");
    }
    return { params, warnings };
}
export function resolveSessionQueryConfigKey(ctx) {
    if (ctx.ovSessionId)
        return `ov:${ctx.ovSessionId}`;
    if (ctx.sessionId)
        return `session:${ctx.sessionId}`;
    if (ctx.sessionKey)
        return `key:${ctx.sessionKey}`;
    return undefined;
}
function emptyRuntimeFile() {
    return { schemaVersion: "1.0", updatedAt: Date.now(), claws: {}, sessions: {} };
}
function isRuntimeFile(value) {
    return !!value && typeof value === "object" && !Array.isArray(value) && value.schemaVersion === "1.0";
}
export class RuntimeQueryConfigStore {
    options;
    data = emptyRuntimeFile();
    lastMtimeMs = 0;
    loadPromise;
    writeQueue = Promise.resolve();
    static createInMemory(staticConfig) {
        return new RuntimeQueryConfigStore({ staticConfig });
    }
    constructor(options) {
        this.options = options;
    }
    async load() {
        if (!this.options.path)
            return;
        const loadPromise = this.loadFromDisk({ resetOnFailure: true });
        this.loadPromise = loadPromise;
        try {
            await loadPromise;
        }
        finally {
            if (this.loadPromise === loadPromise)
                this.loadPromise = undefined;
        }
    }
    async loadFromDisk(options) {
        try {
            const raw = await readFile(this.options.path, "utf8");
            const parsed = JSON.parse(raw);
            if (isRuntimeFile(parsed))
                this.data = parsed;
            const s = await stat(this.options.path);
            this.lastMtimeMs = s.mtimeMs;
        }
        catch {
            if (options?.resetOnFailure)
                this.data = emptyRuntimeFile();
        }
    }
    async waitForInitialLoad() {
        if (this.loadPromise)
            await this.loadPromise;
    }
    async reloadIfChanged(options) {
        if (!this.options.path)
            return;
        await this.waitForInitialLoad();
        try {
            const s = await stat(this.options.path);
            if (!options?.force && s.mtimeMs === this.lastMtimeMs)
                return;
            const raw = await readFile(this.options.path, "utf8");
            const parsed = JSON.parse(raw);
            if (isRuntimeFile(parsed)) {
                this.data = parsed;
                this.lastMtimeMs = s.mtimeMs;
            }
        }
        catch {
            // Keep the last known-good in-memory config.
        }
    }
    async set(scope, ctx, patch) {
        await this.waitForInitialLoad();
        const { params, warnings } = normalizeRuntimeQueryParams(patch);
        const key = this.keyForScope(scope, ctx);
        const bucket = scope === "claw" ? this.data.claws : this.data.sessions;
        const previous = bucket[key];
        bucket[key] = {
            params: { ...(previous?.params ?? {}), ...params },
            updatedAt: Date.now(),
            updatedBy: "command",
            peerId: ctx.peerId,
        };
        this.data.updatedAt = Date.now();
        await this.persist();
        return { warnings };
    }
    async unset(scope, ctx, fields) {
        await this.waitForInitialLoad();
        const key = this.keyForScope(scope, ctx);
        const bucket = scope === "claw" ? this.data.claws : this.data.sessions;
        const record = bucket[key];
        if (!record)
            return;
        for (const field of fields) {
            delete record.params[field];
        }
        record.updatedAt = Date.now();
        this.data.updatedAt = Date.now();
        await this.persist();
    }
    async reset(scope, ctx) {
        await this.waitForInitialLoad();
        const key = this.keyForScope(scope, ctx);
        const bucket = scope === "claw" ? this.data.claws : this.data.sessions;
        delete bucket[key];
        this.data.updatedAt = Date.now();
        await this.persist();
    }
    async getEffective(ctx, requestOverrides) {
        await this.reloadIfChanged();
        const cfg = this.options.staticConfig;
        const warnings = [];
        const effective = {
            recallLimit: cfg.recallLimit,
            candidateMultiplier: DEFAULT_CANDIDATE_MULTIPLIER,
            candidateLimit: Math.max(cfg.recallLimit * DEFAULT_CANDIDATE_MULTIPLIER, 20),
            scoreThreshold: cfg.recallScoreThreshold,
            maxInjectedChars: cfg.recallMaxInjectedChars,
            recallPreferAbstract: cfg.recallPreferAbstract,
            resourceTypes: normalizeRecallResourceTypes(cfg.recallTargetTypes),
            ovSearchLimit: DEFAULT_OV_SEARCH_LIMIT,
            rankingWeights: { ...DEFAULT_RANKING_WEIGHTS },
            resourceTypeWeights: {},
            categoryWeights: {},
            sources: {
                recallLimit: "static",
                candidateMultiplier: "default",
                candidateLimit: "default",
                scoreThreshold: "static",
                maxInjectedChars: "static",
                recallPreferAbstract: "static",
                resourceTypes: "static",
                ovSearchLimit: "default",
                rankingWeights: "default",
            },
            warnings,
        };
        let candidateLimitExplicit = false;
        candidateLimitExplicit = this.applyLayer(effective, this.data.claws[ctx.peerId]?.params, "claw", candidateLimitExplicit);
        const sessionRecord = this.findSessionRecord(ctx);
        if (sessionRecord)
            candidateLimitExplicit = this.applyLayer(effective, sessionRecord.params, "session", candidateLimitExplicit);
        if (requestOverrides) {
            const normalized = normalizeRuntimeQueryParams(requestOverrides);
            warnings.push(...normalized.warnings);
            this.applyLayer(effective, normalized.params, "request", candidateLimitExplicit);
        }
        effective.candidateLimit = Math.max(effective.candidateLimit, effective.recallLimit);
        return effective;
    }
    applyLayer(effective, params, source, candidateLimitExplicit) {
        if (!params)
            return candidateLimitExplicit;
        if (params.recallLimit !== undefined) {
            effective.recallLimit = params.recallLimit;
            effective.sources.recallLimit = source;
            if (!candidateLimitExplicit) {
                effective.candidateLimit = Math.max(effective.recallLimit * effective.candidateMultiplier, 20);
                effective.sources.candidateLimit = source;
            }
        }
        if (params.candidateMultiplier !== undefined) {
            effective.candidateMultiplier = params.candidateMultiplier;
            effective.sources.candidateMultiplier = source;
            if (!candidateLimitExplicit) {
                effective.candidateLimit = Math.max(effective.recallLimit * params.candidateMultiplier, 20);
                effective.sources.candidateLimit = source;
            }
        }
        if (params.candidateLimit !== undefined) {
            effective.candidateLimit = params.candidateLimit;
            effective.sources.candidateLimit = source;
            candidateLimitExplicit = true;
        }
        if (params.scoreThreshold !== undefined) {
            effective.scoreThreshold = params.scoreThreshold;
            effective.sources.scoreThreshold = source;
        }
        if (params.maxInjectedChars !== undefined) {
            effective.maxInjectedChars = params.maxInjectedChars;
            effective.sources.maxInjectedChars = source;
        }
        if (params.recallPreferAbstract !== undefined) {
            effective.recallPreferAbstract = params.recallPreferAbstract;
            effective.sources.recallPreferAbstract = source;
        }
        if (params.resourceTypes !== undefined) {
            effective.resourceTypes = Array.isArray(params.resourceTypes) ? [...params.resourceTypes] : normalizeRecallResourceTypes(params.resourceTypes);
            effective.sources.resourceTypes = source;
        }
        if (params.targetUri !== undefined) {
            effective.targetUri = params.targetUri;
            effective.sources.targetUri = source;
        }
        if (params.ovSearchLimit !== undefined) {
            effective.ovSearchLimit = params.ovSearchLimit;
            effective.sources.ovSearchLimit = source;
        }
        if (params.rankingWeights) {
            effective.rankingWeights = { ...effective.rankingWeights, ...params.rankingWeights };
            effective.sources.rankingWeights = source;
        }
        if (params.resourceTypeWeights)
            effective.resourceTypeWeights = { ...effective.resourceTypeWeights, ...params.resourceTypeWeights };
        if (params.categoryWeights)
            effective.categoryWeights = { ...effective.categoryWeights, ...params.categoryWeights };
        return candidateLimitExplicit;
    }
    keyForScope(scope, ctx) {
        if (scope === "claw")
            return ctx.peerId;
        const key = resolveSessionQueryConfigKey(ctx);
        if (!key)
            throw new Error("session scope requires ovSessionId, sessionId, or sessionKey");
        return key;
    }
    findSessionRecord(ctx) {
        const keys = [
            ctx.ovSessionId ? `ov:${ctx.ovSessionId}` : undefined,
            ctx.sessionId ? `session:${ctx.sessionId}` : undefined,
            ctx.sessionKey ? `key:${ctx.sessionKey}` : undefined,
        ].filter((key) => Boolean(key));
        for (const key of keys) {
            const record = this.data.sessions[key];
            if (record)
                return record;
        }
        return undefined;
    }
    async persist() {
        if (!this.options.path)
            return;
        const operation = this.writeQueue.catch(() => undefined).then(async () => {
            await mkdir(dirname(this.options.path), { recursive: true });
            const temp = `${this.options.path}.${process.pid}.${Date.now()}.tmp`;
            await writeFile(temp, JSON.stringify(this.data, null, 2), "utf8");
            await rename(temp, this.options.path);
            const s = await stat(this.options.path);
            this.lastMtimeMs = s.mtimeMs;
        });
        this.writeQueue = operation.catch(() => undefined);
        await operation;
    }
}
