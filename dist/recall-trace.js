import { appendFile, mkdir, readFile, readdir, unlink } from "node:fs/promises";
import { join } from "node:path";
const ALLOWED_RESOURCE_TYPES = ["resource", "user", "agent"];
const DEFAULT_RESOURCE_TYPES = ["user", "agent"];
function toResourceTypeEntries(value) {
    if (Array.isArray(value)) {
        return value
            .filter((entry) => typeof entry === "string")
            .map((entry) => entry.trim())
            .filter(Boolean);
    }
    if (typeof value === "string") {
        return value
            .split(/[,\n]/)
            .map((entry) => entry.trim())
            .filter(Boolean);
    }
    return [];
}
export function normalizeResourceTypes(value) {
    const entries = toResourceTypeEntries(value);
    if (entries.length === 0) {
        return [...DEFAULT_RESOURCE_TYPES];
    }
    const seen = new Set();
    const normalized = [];
    const invalid = [];
    for (const entry of entries) {
        if (ALLOWED_RESOURCE_TYPES.includes(entry)) {
            const typed = entry;
            if (!seen.has(typed)) {
                seen.add(typed);
                normalized.push(typed);
            }
        }
        else {
            invalid.push(entry);
        }
    }
    if (invalid.length > 0) {
        throw new Error(`invalid resourceTypes: ${invalid.join(", ")}`);
    }
    return normalized.length > 0 ? normalized : [...DEFAULT_RESOURCE_TYPES];
}
export function resolveRecallSearchPlan(resourceTypes, _ctx) {
    const normalized = normalizeResourceTypes(resourceTypes);
    const searches = [];
    const skipped = [];
    let addedMemorySearch = false;
    for (const resourceType of normalized) {
        if (resourceType === "resource") {
            searches.push({ resourceType, contextType: "resource" });
        }
        else if ((resourceType === "user" || resourceType === "agent") && !addedMemorySearch) {
            searches.push({ resourceType: "user", contextType: "memory" });
            addedMemorySearch = true;
        }
    }
    return { resourceTypes: normalized, searches, skipped };
}
export class RecallTraceMemoryStore {
    maxEntries;
    entries = [];
    constructor(maxEntries) {
        this.maxEntries = Math.max(1, Math.floor(maxEntries));
    }
    record(entry) {
        this.entries.push(entry);
        while (this.entries.length > this.maxEntries) {
            this.entries.shift();
        }
    }
    query(query) {
        const limit = Math.max(1, Math.floor(query.limit ?? 20));
        const turn = query.turn ?? "latest";
        const resourceTypes = query.resourceTypes && query.resourceTypes.length > 0
            ? new Set(query.resourceTypes)
            : undefined;
        const filtered = this.entries
            .filter((entry) => {
            if (query.traceId && entry.traceId !== query.traceId)
                return false;
            if (query.source && entry.source !== query.source)
                return false;
            if (query.sessionId && entry.sessionId !== query.sessionId)
                return false;
            if (query.sessionKey && entry.sessionKey !== query.sessionKey)
                return false;
            if (query.ovSessionId && entry.ovSessionId !== query.ovSessionId)
                return false;
            if (typeof query.since === "number" && entry.ts < query.since)
                return false;
            if (typeof query.until === "number" && entry.ts > query.until)
                return false;
            if (resourceTypes && !entry.resourceTypes.some((resourceType) => resourceTypes.has(resourceType))) {
                return false;
            }
            return true;
        })
            .sort((a, b) => b.ts - a.ts);
        return { entries: filtered.slice(0, turn === "latest" ? 1 : limit), lookupLayer: "memory", warnings: [] };
    }
}
function jsonlFileNameForTimestamp(ts) {
    return `${new Date(ts).toISOString().slice(0, 10)}.jsonl`;
}
function startOfUtcDay(ts) {
    const d = new Date(ts);
    return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
}
function timestampFromJsonlFileName(name) {
    const match = /^(\d{4})-(\d{2})-(\d{2})\.jsonl$/.exec(name);
    if (!match) {
        return undefined;
    }
    const ts = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    return Number.isFinite(ts) ? ts : undefined;
}
function fileMayOverlapQueryWindow(name, since, until) {
    const dayStart = timestampFromJsonlFileName(name);
    if (dayStart === undefined) {
        return true;
    }
    const dayEnd = dayStart + 86_400_000 - 1;
    return dayEnd >= since && dayStart <= until;
}
function isRecallTraceEntry(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
        return false;
    }
    const candidate = value;
    return candidate.schemaVersion === "1.0" &&
        typeof candidate.traceId === "string" &&
        typeof candidate.ts === "number" &&
        typeof candidate.source === "string" &&
        typeof candidate.operationType === "string" &&
        Array.isArray(candidate.resourceTypes) &&
        !!candidate.trigger &&
        typeof candidate.trigger.query === "string";
}
export class RecallTraceJsonlStore {
    dir;
    includeRawUserPreview;
    retentionDays;
    queryMaxDays;
    pending = [];
    warnings = [];
    constructor(options) {
        this.dir = options.dir;
        this.includeRawUserPreview = options.includeRawUserPreview === true;
        this.retentionDays = Math.max(1, Math.floor(options.retentionDays ?? 14));
        this.queryMaxDays = Math.max(1, Math.floor(options.queryMaxDays ?? 14));
    }
    entryForPersistence(entry) {
        if (this.includeRawUserPreview || entry.trigger.rawUserTextPreview === undefined) {
            return entry;
        }
        return {
            ...entry,
            trigger: {
                ...entry.trigger,
                rawUserTextPreview: undefined,
            },
        };
    }
    append(entry) {
        const write = (async () => {
            await mkdir(this.dir, { recursive: true });
            await this.pruneExpiredFiles(entry.ts);
            await appendFile(join(this.dir, jsonlFileNameForTimestamp(entry.ts)), `${JSON.stringify(this.entryForPersistence(entry))}\n`, "utf8");
        })().catch((err) => {
            this.warnings.push(`Failed to append recall trace JSONL: ${err instanceof Error ? err.message : String(err)}`);
        });
        this.pending.push(write);
        return write;
    }
    async pruneExpiredFiles(nowTs) {
        const cutoff = startOfUtcDay(nowTs - this.retentionDays * 86_400_000);
        let files;
        try {
            files = await readdir(this.dir);
        }
        catch {
            return;
        }
        await Promise.all(files
            .filter((name) => name.endsWith(".jsonl"))
            .filter((name) => {
            const ts = timestampFromJsonlFileName(name);
            return ts !== undefined && ts < cutoff;
        })
            .map(async (name) => {
            try {
                await unlink(join(this.dir, name));
            }
            catch (err) {
                this.warnings.push(`Failed to prune recall trace file ${name}: ${err instanceof Error ? err.message : String(err)}`);
            }
        }));
    }
    async flush() {
        const pending = this.pending.splice(0);
        await Promise.all(pending);
        return { warnings: [...this.warnings] };
    }
    async query(query) {
        await this.flush();
        const warnings = [...this.warnings];
        const entries = [];
        let files;
        try {
            files = await readdir(this.dir);
        }
        catch (err) {
            const code = typeof err === "object" && err !== null && "code" in err ? String(err.code) : "";
            if (code === "ENOENT") {
                return { entries: [], lookupLayer: "persistent", warnings };
            }
            return {
                entries: [],
                lookupLayer: "persistent",
                warnings: [...warnings, `Failed to read recall trace directory: ${err instanceof Error ? err.message : String(err)}`],
            };
        }
        const queryStart = typeof query.since === "number"
            ? query.since
            : Date.now() - this.queryMaxDays * 86_400_000;
        const queryEnd = typeof query.until === "number" ? query.until : Date.now();
        for (const file of files
            .filter((name) => name.endsWith(".jsonl"))
            .filter((name) => fileMayOverlapQueryWindow(name, queryStart, queryEnd))
            .sort()) {
            const path = join(this.dir, file);
            let content;
            try {
                content = await readFile(path, "utf8");
            }
            catch (err) {
                warnings.push(`Failed to read recall trace file ${file}: ${err instanceof Error ? err.message : String(err)}`);
                continue;
            }
            const lines = content.split("\n");
            for (let index = 0; index < lines.length; index++) {
                const line = lines[index].trim();
                if (!line) {
                    continue;
                }
                try {
                    const parsed = JSON.parse(line);
                    if (isRecallTraceEntry(parsed)) {
                        entries.push(parsed);
                    }
                    else {
                        warnings.push(`Skipping corrupted recall trace line ${file}:${index + 1}`);
                    }
                }
                catch {
                    warnings.push(`Skipping corrupted recall trace line ${file}:${index + 1}`);
                }
            }
        }
        const memory = new RecallTraceMemoryStore(Math.max(1, entries.length));
        for (const entry of entries) {
            memory.record(entry);
        }
        const filtered = memory.query(query);
        return { entries: filtered.entries, lookupLayer: "persistent", warnings };
    }
}
export class RecallTraceRecorder {
    memory;
    persistent;
    constructor(options) {
        this.memory = new RecallTraceMemoryStore(options.memoryMaxEntries);
        this.persistent = options.persist ? new RecallTraceJsonlStore({
            dir: options.traceDir,
            includeRawUserPreview: options.includeRawUserPreview,
            retentionDays: options.retentionDays,
            queryMaxDays: options.queryMaxDays,
        }) : undefined;
    }
    record(entry) {
        this.memory.record(entry);
        void this.persistent?.append(entry);
    }
    async recordAndFlush(entry) {
        this.memory.record(entry);
        await this.persistent?.append(entry);
        return this.flush();
    }
    query(query) {
        return this.memory.query(query);
    }
    async queryWithFallback(query) {
        const memoryResult = this.memory.query(query);
        if (memoryResult.entries.length > 0 || !this.persistent) {
            return memoryResult;
        }
        const persistentResult = await this.persistent.query(query);
        return {
            entries: persistentResult.entries,
            lookupLayer: "persistent",
            warnings: [...memoryResult.warnings, ...persistentResult.warnings],
        };
    }
    async flush() {
        return this.persistent ? this.persistent.flush() : { warnings: [] };
    }
}
