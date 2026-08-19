import { appendFile, mkdir, readFile, readdir, unlink } from "node:fs/promises";
import { join } from "node:path";

export type RecallResourceType = "resource" | "session" | "user" | "agent";

export type RecallTraceSource = "auto_recall" | "memory_recall" | "ov_search" | "ov_archive_search";

export type RecallTraceOperationType = "semantic_find" | "archive_grep";

export type RecallTraceResult = {
  uri: string;
  resourceType?: RecallResourceType | "archive";
  category?: string;
  score?: number;
  level?: number;
  abstractPreview?: string;
  resultType: "memory" | "resource" | "skill" | "archive_match";
};

export type RecallTraceEntry = {
  schemaVersion: "1.0";
  traceId: string;
  ts: number;
  sessionId?: string;
  sessionKey?: string;
  ovSessionId?: string;
  agentId?: string;
  source: RecallTraceSource;
  operationType: RecallTraceOperationType;
  resourceTypes: RecallResourceType[];
  trigger: {
    rawUserTextPreview?: string;
    query: string;
    derivedKeywords?: string[];
    queryTruncated?: boolean;
  };
  searches: Array<{
    resourceType: RecallResourceType | "archive";
    contextType?: "memory" | "resource" | "skill";
    targetUriInput?: string;
    targetUriResolved?: string;
    limit: number;
    scoreThreshold?: number;
    durationMs: number;
    total: number;
    results: RecallTraceResult[];
    archiveId?: string;
    caseInsensitive?: boolean;
    error?: string;
  }>;
  selected: Array<{
    uri: string;
    resourceType?: RecallResourceType | "archive";
    category?: string;
    score?: number;
    line?: number;
    abstractPreview?: string;
    contentPreview?: string;
    readError?: string;
    injected?: boolean;
    displayed?: boolean;
    skippedReason?: "score_threshold" | "dedupe" | "non_leaf" | "budget" | "not_top_k" | "search_error";
  }>;
  stats: {
    candidateCount: number;
    selectedCount: number;
    injectedCount: number;
    estimatedTokens?: number;
  };
};

export type RecallTraceQuery = {
  turn?: "latest" | "all";
  traceId?: string;
  sessionId?: string;
  sessionKey?: string;
  ovSessionId?: string;
  source?: RecallTraceSource;
  resourceTypes?: RecallResourceType[];
  since?: number;
  until?: number;
  limit?: number;
};

export type RecallTraceQueryResult = {
  entries: RecallTraceEntry[];
  lookupLayer: "memory" | "persistent";
  warnings: string[];
};

export type RecallTraceFlushResult = {
  warnings: string[];
};

const ALLOWED_RESOURCE_TYPES: RecallResourceType[] = ["resource", "user", "agent"];
const DEFAULT_RESOURCE_TYPES: RecallResourceType[] = ["user", "agent"];

function toResourceTypeEntries(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value
      .filter((entry): entry is string => typeof entry === "string")
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

export function normalizeResourceTypes(value: unknown): RecallResourceType[] {
  const entries = toResourceTypeEntries(value);
  if (entries.length === 0) {
    return [...DEFAULT_RESOURCE_TYPES];
  }

  const seen = new Set<RecallResourceType>();
  const normalized: RecallResourceType[] = [];
  const invalid: string[] = [];
  for (const entry of entries) {
    if ((ALLOWED_RESOURCE_TYPES as string[]).includes(entry)) {
      const typed = entry as RecallResourceType;
      if (!seen.has(typed)) {
        seen.add(typed);
        normalized.push(typed);
      }
    } else {
      invalid.push(entry);
    }
  }

  if (invalid.length > 0) {
    throw new Error(`invalid resourceTypes: ${invalid.join(", ")}`);
  }

  return normalized.length > 0 ? normalized : [...DEFAULT_RESOURCE_TYPES];
}

export function resolveRecallSearchPlan(
  resourceTypes: unknown,
  _ctx: { ovSessionId?: string; agentId?: string },
): {
  resourceTypes: RecallResourceType[];
  searches: Array<{ resourceType: RecallResourceType; targetUri?: string; contextType: "memory" | "resource" }>;
  skipped: Array<{ resourceType: RecallResourceType; reason: "missing_session" }>;
} {
  const normalized = normalizeResourceTypes(resourceTypes);
  const searches: Array<{ resourceType: RecallResourceType; targetUri?: string; contextType: "memory" | "resource" }> = [];
  const skipped: Array<{ resourceType: RecallResourceType; reason: "missing_session" }> = [];
  let addedMemorySearch = false;

  for (const resourceType of normalized) {
    if (resourceType === "resource") {
      searches.push({ resourceType, contextType: "resource" });
    } else if ((resourceType === "user" || resourceType === "agent") && !addedMemorySearch) {
      searches.push({ resourceType: "user", contextType: "memory" });
      addedMemorySearch = true;
    }
  }

  return { resourceTypes: normalized, searches, skipped };
}

export class RecallTraceMemoryStore {
  private readonly maxEntries: number;
  private readonly entries: RecallTraceEntry[] = [];

  constructor(maxEntries: number) {
    this.maxEntries = Math.max(1, Math.floor(maxEntries));
  }

  record(entry: RecallTraceEntry): void {
    this.entries.push(entry);
    while (this.entries.length > this.maxEntries) {
      this.entries.shift();
    }
  }

  query(query: RecallTraceQuery): RecallTraceQueryResult {
    const limit = Math.max(1, Math.floor(query.limit ?? 20));
    const turn = query.turn ?? "latest";
    const resourceTypes = query.resourceTypes && query.resourceTypes.length > 0
      ? new Set(query.resourceTypes)
      : undefined;

    const filtered = this.entries
      .filter((entry) => {
        if (query.traceId && entry.traceId !== query.traceId) return false;
        if (query.source && entry.source !== query.source) return false;
        if (query.sessionId && entry.sessionId !== query.sessionId) return false;
        if (query.sessionKey && entry.sessionKey !== query.sessionKey) return false;
        if (query.ovSessionId && entry.ovSessionId !== query.ovSessionId) return false;
        if (typeof query.since === "number" && entry.ts < query.since) return false;
        if (typeof query.until === "number" && entry.ts > query.until) return false;
        if (resourceTypes && !entry.resourceTypes.some((resourceType) => resourceTypes.has(resourceType))) {
          return false;
        }
        return true;
      })
      .sort((a, b) => b.ts - a.ts);

    return { entries: filtered.slice(0, turn === "latest" ? 1 : limit), lookupLayer: "memory", warnings: [] };
  }
}

function jsonlFileNameForTimestamp(ts: number): string {
  return `${new Date(ts).toISOString().slice(0, 10)}.jsonl`;
}

function startOfUtcDay(ts: number): number {
  const d = new Date(ts);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
}

function timestampFromJsonlFileName(name: string): number | undefined {
  const match = /^(\d{4})-(\d{2})-(\d{2})\.jsonl$/.exec(name);
  if (!match) {
    return undefined;
  }
  const ts = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return Number.isFinite(ts) ? ts : undefined;
}

function fileMayOverlapQueryWindow(name: string, since: number, until: number): boolean {
  const dayStart = timestampFromJsonlFileName(name);
  if (dayStart === undefined) {
    return true;
  }
  const dayEnd = dayStart + 86_400_000 - 1;
  return dayEnd >= since && dayStart <= until;
}

function isRecallTraceEntry(value: unknown): value is RecallTraceEntry {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const candidate = value as Partial<RecallTraceEntry>;
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
  private readonly dir: string;
  private readonly includeRawUserPreview: boolean;
  private readonly retentionDays: number;
  private readonly queryMaxDays: number;
  private readonly pending: Promise<void>[] = [];
  private readonly warnings: string[] = [];

  constructor(options: { dir: string; includeRawUserPreview?: boolean; retentionDays?: number; queryMaxDays?: number }) {
    this.dir = options.dir;
    this.includeRawUserPreview = options.includeRawUserPreview === true;
    this.retentionDays = Math.max(1, Math.floor(options.retentionDays ?? 14));
    this.queryMaxDays = Math.max(1, Math.floor(options.queryMaxDays ?? 14));
  }

  private entryForPersistence(entry: RecallTraceEntry): RecallTraceEntry {
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

  append(entry: RecallTraceEntry): Promise<void> {
    const write = (async () => {
      await mkdir(this.dir, { recursive: true });
      await this.pruneExpiredFiles(entry.ts);
      await appendFile(
        join(this.dir, jsonlFileNameForTimestamp(entry.ts)),
        `${JSON.stringify(this.entryForPersistence(entry))}\n`,
        "utf8",
      );
    })().catch((err: unknown) => {
      this.warnings.push(`Failed to append recall trace JSONL: ${err instanceof Error ? err.message : String(err)}`);
    });

    this.pending.push(write);
    return write;
  }

  private async pruneExpiredFiles(nowTs: number): Promise<void> {
    const cutoff = startOfUtcDay(nowTs - this.retentionDays * 86_400_000);
    let files: string[];
    try {
      files = await readdir(this.dir);
    } catch {
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
        } catch (err: unknown) {
          this.warnings.push(`Failed to prune recall trace file ${name}: ${err instanceof Error ? err.message : String(err)}`);
        }
      }));
  }

  async flush(): Promise<RecallTraceFlushResult> {
    const pending = this.pending.splice(0);
    await Promise.all(pending);
    return { warnings: [...this.warnings] };
  }

  async query(query: RecallTraceQuery): Promise<RecallTraceQueryResult> {
    await this.flush();
    const warnings = [...this.warnings];
    const entries: RecallTraceEntry[] = [];

    let files: string[];
    try {
      files = await readdir(this.dir);
    } catch (err: unknown) {
      const code = typeof err === "object" && err !== null && "code" in err ? String((err as { code?: unknown }).code) : "";
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
      let content: string;
      try {
        content = await readFile(path, "utf8");
      } catch (err: unknown) {
        warnings.push(`Failed to read recall trace file ${file}: ${err instanceof Error ? err.message : String(err)}`);
        continue;
      }

      const lines = content.split("\n");
      for (let index = 0; index < lines.length; index++) {
        const line = lines[index]!.trim();
        if (!line) {
          continue;
        }
        try {
          const parsed: unknown = JSON.parse(line);
          if (isRecallTraceEntry(parsed)) {
            entries.push(parsed);
          } else {
            warnings.push(`Skipping corrupted recall trace line ${file}:${index + 1}`);
          }
        } catch {
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
  private readonly memory: RecallTraceMemoryStore;
  private readonly persistent?: RecallTraceJsonlStore;

  constructor(options: {
    memoryMaxEntries: number;
    persist: boolean;
    traceDir: string;
    includeRawUserPreview?: boolean;
    retentionDays?: number;
    queryMaxDays?: number;
  }) {
    this.memory = new RecallTraceMemoryStore(options.memoryMaxEntries);
    this.persistent = options.persist ? new RecallTraceJsonlStore({
      dir: options.traceDir,
      includeRawUserPreview: options.includeRawUserPreview,
      retentionDays: options.retentionDays,
      queryMaxDays: options.queryMaxDays,
    }) : undefined;
  }

  record(entry: RecallTraceEntry): void {
    this.memory.record(entry);
    void this.persistent?.append(entry);
  }

  async recordAndFlush(entry: RecallTraceEntry): Promise<RecallTraceFlushResult> {
    this.memory.record(entry);
    await this.persistent?.append(entry);
    return this.flush();
  }

  query(query: RecallTraceQuery): RecallTraceQueryResult {
    return this.memory.query(query);
  }

  async queryWithFallback(query: RecallTraceQuery): Promise<RecallTraceQueryResult> {
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

  async flush(): Promise<RecallTraceFlushResult> {
    return this.persistent ? this.persistent.flush() : { warnings: [] };
  }
}
