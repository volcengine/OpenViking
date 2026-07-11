import type { FindResult, FindResultItem, FsListResult } from "../client.js";
import type { RecallResourceType } from "../registries/recall-resource-types.js";
import type { RecallTraceEntry, RecallTraceResult } from "../recall-trace.js";

export type OpenVikingSearchInput = {
  query: string;
  uri?: string;
  limit?: number;
};

export type OpenVikingReadInput = {
  uri: string;
};

export type OpenVikingMultiReadInput = {
  uris: string[];
};

export type OpenVikingListInput = {
  uri: string;
  recursive?: boolean;
  simple?: boolean;
  limit?: number;
};

export type OpenVikingQuerySession = {
  sessionId?: string;
  sessionKey?: string;
  ovSessionId?: string;
  agentId: string;
};

export type OpenVikingQueryToolResult = {
  content: Array<{ type: "text"; text: string }>;
  details?: Record<string, unknown>;
};

type OpenVikingQueryConfigOverrides = {
  ovSearchLimit?: number;
  targetUri?: string;
};

type OpenVikingQueryConfig = {
  ovSearchLimit?: number;
  targetUri?: string;
};

type OpenVikingQueryClient = {
  find: (
    query: string,
    options: { targetUri: string; limit: number; scoreThreshold?: number },
    agentId?: string,
  ) => Promise<FindResult>;
  read: (uri: string, agentId?: string) => Promise<unknown>;
  list: (
    uri: string,
    options?: { recursive?: boolean; simple?: boolean; nodeLimit?: number },
    agentId?: string,
  ) => Promise<FsListResult>;
};

export type OpenVikingQueryRuntimeDeps<TQueryConfigContext> = {
  getClient: () => Promise<OpenVikingQueryClient>;
  queryConfigStore: {
    getEffective: (ctx: TQueryConfigContext, overrides?: OpenVikingQueryConfigOverrides) => Promise<OpenVikingQueryConfig>;
  };
  toQueryConfigContext: (session: OpenVikingQuerySession) => TQueryConfigContext;
  traceRecorder?: {
    recordAndFlush?: (entry: RecallTraceEntry) => Promise<unknown>;
  };
  inferRecallResourceType: (uri: string) => RecallResourceType | undefined;
  createTraceId: (source: string) => string;
  boundTraceQuery: (query: string, maxChars: number) => { query: string; queryTruncated?: boolean };
  previewText: (value: unknown, maxChars: number) => string | undefined;
  logger: { warn?: (message: string) => void };
  cfg: {
    traceRecallMaxResultsPerSearch: number;
    traceRecallPreviewChars: number;
    traceRecallQueryMaxChars: number;
  };
};

function mergeFindResults(results: FindResult[]): FindResult {
  const deduplicate = (items: FindResultItem[]): FindResultItem[] => {
    const seen = new Map<string, FindResultItem>();
    for (const item of items) {
      if (!seen.has(item.uri)) {
        seen.set(item.uri, item);
      }
    }
    return Array.from(seen.values());
  };
  const memories = deduplicate(results.flatMap((result) => result.memories ?? []));
  const resources = deduplicate(results.flatMap((result) => result.resources ?? []));
  const skills = deduplicate(results.flatMap((result) => result.skills ?? []));
  return {
    memories,
    resources,
    skills,
    total: memories.length + resources.length + skills.length,
  };
}

function formatOVSearchRows(result: FindResult): string[] {
  const truncateSummary = (value: string, maxChars = 220): string => {
    const collapsed = value.replace(/\s+/g, " ").trim();
    if (collapsed.length <= maxChars) {
      return collapsed;
    }
    return `${collapsed.slice(0, maxChars - 3)}...`;
  };
  const items = [
    ...(result.memories ?? []).map((item) => ({ contextType: "memory", item })),
    ...(result.resources ?? []).map((item) => ({ contextType: "resource", item })),
    ...(result.skills ?? []).map((item) => ({ contextType: "skill", item })),
  ];
  if (items.length === 0) {
    return [];
  }
  const numberHeader = "no";
  const numberWidth = Math.max(numberHeader.length, String(items.length).length);
  const typeWidth = Math.max("type".length, ...items.map(({ contextType }) => contextType.length));
  const uriWidth = Math.max("uri".length, ...items.map(({ item }) => item.uri.length));
  const levelWidth = Math.max("level".length, ...items.map(({ item }) => String(item.level ?? "").length));
  const scoreWidth = Math.max(
    "score".length,
    ...items.map(({ item }) => (typeof item.score === "number" ? item.score.toFixed(2).length : 0)),
  );
  return [
    `${numberHeader.padEnd(numberWidth)}  ${"type".padEnd(typeWidth)}  ${"uri".padEnd(uriWidth)}  ${"level".padEnd(levelWidth)}  ${"score".padEnd(scoreWidth)}  abstract`,
    ...items.map(({ contextType, item }, index) => {
      const score = typeof item.score === "number" ? item.score.toFixed(2) : "";
      const summary = truncateSummary(item.abstract || item.overview || "(no summary)");
      return `${String(index + 1).padEnd(numberWidth)}  ${contextType.padEnd(typeWidth)}  ${item.uri.padEnd(uriWidth)}  ${String(item.level ?? "").padEnd(levelWidth)}  ${score.padEnd(scoreWidth)}  ${summary}`;
    }),
  ];
}

function formatOVSearchText(query: string, uri: string | undefined, result: FindResult): string {
  if ((result.total ?? 0) <= 0) {
    const scope = uri ? ` under ${uri}` : "";
    return `No OpenViking resource or skill results found for "${query}"${scope}.`;
  }
  const scope = uri ? ` under ${uri}` : "";
  const lines = [
    `Found ${result.total ?? 0} OpenViking results for "${query}"${scope}`,
    "Tip: search results are ranked snippets. Use ov_read on exact hit URIs before answering precise questions. Use ov_list on a hit's parent URI to inspect sibling chunks or overview files before answering procedural or multi-step questions.",
    "",
    ...formatOVSearchRows(result),
    "",
    "Note: result URIs are OpenViking virtual URIs, not local file paths. Use the ov_read tool with the exact viking:// URI to read full content; do not use filesystem read tools for these URIs.",
  ].filter((line, index, all) => line || (all[index - 1] && all[index + 1]));
  return lines.join("\n");
}

function validateOpenVikingUri(toolName: string, uri: string): void {
  if (!uri) {
    throw new Error("uri is required");
  }
  if (!uri.startsWith("viking://")) {
    throw new Error(`${toolName} only accepts OpenViking viking:// URIs, not local file paths or openviking:// display aliases`);
  }
  if (uri.endsWith("...") || uri.includes("…")) {
    throw new Error(
      `${toolName} received a truncated display URI. Use the exact full viking:// URI from ov_search details/results; do not shorten it with ... or ….`,
    );
  }
}

function formatOVListEntry(entry: unknown): string {
  if (typeof entry === "string") {
    return entry;
  }
  if (!entry || typeof entry !== "object") {
    return String(entry);
  }
  const item = entry as Record<string, unknown>;
  const uri = typeof item.uri === "string" ? item.uri : "";
  const name = typeof item.name === "string" ? item.name : "";
  const isDir = item.isDir === true || item.type === "directory";
  const marker = isDir ? "[dir]" : "[file]";
  const summary =
    typeof item.abstract === "string" && item.abstract.trim()
      ? item.abstract.trim().replace(/\s+/g, " ")
      : typeof item.overview === "string" && item.overview.trim()
        ? item.overview.trim().replace(/\s+/g, " ")
        : "";
  const label = uri || name || JSON.stringify(item);
  return summary ? `${marker} ${label} - ${summary}` : `${marker} ${label}`;
}

function formatOVListText(uri: string, entries: FsListResult): string {
  if (entries.length === 0) {
    return `No OpenViking entries found under ${uri}.`;
  }
  return [
    `Listed ${entries.length} OpenViking entr${entries.length === 1 ? "y" : "ies"} under ${uri}`,
    "",
    ...entries.map((entry) => formatOVListEntry(entry)),
  ].join("\n");
}

function formatOVReadText(uri: string, content: string): string {
  const body = content || "(empty OpenViking content)";
  return [`--- START OF ${uri} ---`, body, `--- END OF ${uri} ---`].join("\n");
}

function formatOVMultiReadText(results: Array<{ uri: string; content: string; success: boolean }>): string {
  return [
    `Multi-read results for ${results.length} OpenViking resource${results.length === 1 ? "" : "s"}:`,
    "",
    ...results.flatMap((result) => [
      `--- START OF ${result.uri} ---`,
      result.success ? (result.content || "(empty OpenViking content)") : `ERROR: ${result.content}`,
      `--- END OF ${result.uri} ---`,
      "",
    ]),
  ].join("\n").trimEnd();
}

export function createOpenVikingQueryRuntime<TQueryConfigContext>(deps: OpenVikingQueryRuntimeDeps<TQueryConfigContext>): {
  readOpenVikingContent: (input: OpenVikingReadInput, agentId?: string) => Promise<OpenVikingQueryToolResult>;
  multiReadOpenVikingContent: (input: OpenVikingMultiReadInput, agentId?: string) => Promise<OpenVikingQueryToolResult>;
  listOpenVikingDirectory: (input: OpenVikingListInput, agentId?: string) => Promise<OpenVikingQueryToolResult>;
  searchOpenViking: (input: OpenVikingSearchInput, agentId?: string, traceCtx?: OpenVikingQuerySession) => Promise<OpenVikingQueryToolResult>;
} {
  const toTraceResult = (
    item: FindResultItem,
    resultType: RecallTraceResult["resultType"],
  ): RecallTraceResult => ({
    uri: item.uri,
    resourceType: deps.inferRecallResourceType(item.uri),
    category: item.category,
    score: item.score,
    level: item.level,
    abstractPreview: deps.previewText(item.abstract || item.overview, deps.cfg.traceRecallPreviewChars),
    resultType,
  });

  const readOpenVikingContent = async (input: OpenVikingReadInput, agentId?: string) => {
    const uri = input.uri.trim();
    validateOpenVikingUri("ov_read", uri);
    const client = await deps.getClient();
    const content = await client.read(uri, agentId);
    const text = typeof content === "string" ? content : JSON.stringify(content, null, 2);
    return {
      content: [{ type: "text" as const, text: formatOVReadText(uri, text) }],
      details: {
        action: "read",
        uri,
        chars: text.length,
      },
    };
  };

  const multiReadOpenVikingContent = async (input: OpenVikingMultiReadInput, agentId?: string) => {
    const uris = input.uris
      .map((uri) => (typeof uri === "string" ? uri.trim() : ""))
      .filter((uri) => uri.length > 0);
    if (uris.length === 0) {
      throw new Error("uris is required");
    }
    for (const uri of uris) {
      validateOpenVikingUri("ov_multi_read", uri);
    }
    const client = await deps.getClient();
    const results = await Promise.all(
      uris.map(async (uri) => {
        try {
          const content = await client.read(uri, agentId);
          const text = typeof content === "string" ? content : JSON.stringify(content, null, 2);
          return {
            uri,
            content: text,
            success: true,
            chars: text.length,
          };
        } catch (err) {
          return {
            uri,
            content: err instanceof Error ? err.message : String(err),
            success: false,
            chars: 0,
          };
        }
      }),
    );
    return {
      content: [{ type: "text" as const, text: formatOVMultiReadText(results) }],
      details: {
        action: "multi_read",
        count: results.length,
        success_count: results.filter((result) => result.success).length,
        results,
      },
    };
  };

  const listOpenVikingDirectory = async (input: OpenVikingListInput, agentId?: string) => {
    const uri = input.uri.trim();
    validateOpenVikingUri("ov_list", uri);
    const limit = Math.max(1, Math.floor(input.limit ?? 100));
    const client = await deps.getClient();
    const entries = await client.list(uri, {
      recursive: input.recursive ?? false,
      simple: input.simple ?? false,
      nodeLimit: limit,
    }, agentId);
    return {
      content: [{ type: "text" as const, text: formatOVListText(uri, entries) }],
      details: {
        action: "listed",
        uri,
        recursive: input.recursive ?? false,
        simple: input.simple ?? false,
        count: entries.length,
        entries,
      },
    };
  };

  const searchOpenViking = async (input: OpenVikingSearchInput, agentId?: string, traceCtx?: OpenVikingQuerySession) => {
    const query = input.query.trim();
    if (!query) {
      throw new Error("query is required");
    }
    const queryConfig = traceCtx
      ? await deps.queryConfigStore.getEffective(deps.toQueryConfigContext(traceCtx), {
          ovSearchLimit: typeof input.limit === "number" ? input.limit : undefined,
          targetUri: input.uri,
        })
      : undefined;
    const limit = Math.max(1, Math.floor(input.limit ?? queryConfig?.ovSearchLimit ?? 10));
    const searchUri = input.uri ?? queryConfig?.targetUri;
    const client = await deps.getClient();
    let result: FindResult;
    const searches: RecallTraceEntry["searches"] = [];
    if (searchUri) {
      const started = Date.now();
      result = await client.find(query, { targetUri: searchUri, limit }, agentId);
      const items = [
        ...(result.memories ?? []).map((item) => toTraceResult(item, "memory")),
        ...(result.resources ?? []).map((item) => toTraceResult(item, "resource")),
        ...(result.skills ?? []).map((item) => toTraceResult(item, "skill")),
      ].slice(0, deps.cfg.traceRecallMaxResultsPerSearch);
      searches.push({
        resourceType: deps.inferRecallResourceType(searchUri) ?? "resource",
        targetUriInput: searchUri,
        targetUriResolved: searchUri,
        limit,
        durationMs: Date.now() - started,
        total: result.total ?? items.length,
        results: items,
      });
    } else {
      const runSearch = async (targetUri: string) => {
        const started = Date.now();
        try {
          const found = await client.find(query, { targetUri, limit }, agentId);
          const items = [
            ...(found.memories ?? []).map((item) => toTraceResult(item, "memory")),
            ...(found.resources ?? []).map((item) => toTraceResult(item, "resource")),
            ...(found.skills ?? []).map((item) => toTraceResult(item, "skill")),
          ].slice(0, deps.cfg.traceRecallMaxResultsPerSearch);
          searches.push({
            resourceType: deps.inferRecallResourceType(targetUri) ?? "resource",
            targetUriResolved: targetUri,
            limit,
            durationMs: Date.now() - started,
            total: found.total ?? items.length,
            results: items,
          });
          return found;
        } catch (err) {
          searches.push({
            resourceType: deps.inferRecallResourceType(targetUri) ?? "resource",
            targetUriResolved: targetUri,
            limit,
            durationMs: Date.now() - started,
            total: 0,
            results: [],
            error: err instanceof Error ? err.message : String(err),
          });
          throw err;
        }
      };
      const [resourcesSettled, skillsSettled] = await Promise.allSettled([
        runSearch("viking://resources"),
        runSearch("viking://user/skills"),
      ]);
      const successful: FindResult[] = [];
      if (resourcesSettled.status === "fulfilled") {
        successful.push(resourcesSettled.value);
      }
      if (skillsSettled.status === "fulfilled") {
        successful.push(skillsSettled.value);
      }
      if (successful.length === 0) {
        const firstError =
          resourcesSettled.status === "rejected"
            ? resourcesSettled.reason
            : skillsSettled.status === "rejected"
              ? skillsSettled.reason
              : "Both searches failed";
        throw firstError instanceof Error ? firstError : new Error(String(firstError));
      }
      if (resourcesSettled.status === "rejected") {
        deps.logger.warn?.(`openviking: resource search failed: ${String(resourcesSettled.reason)}`);
      }
      if (skillsSettled.status === "rejected") {
        deps.logger.warn?.(`openviking: skill search failed: ${String(skillsSettled.reason)}`);
      }
      result = mergeFindResults(successful);
    }
    const selected = [
      ...(result.memories ?? []).map((item) => ({
        uri: item.uri,
        resourceType: deps.inferRecallResourceType(item.uri),
        category: item.category,
        score: item.score,
        abstractPreview: deps.previewText(item.abstract || item.overview, deps.cfg.traceRecallPreviewChars),
        displayed: true,
      })),
      ...(result.resources ?? []).map((item) => ({
        uri: item.uri,
        resourceType: deps.inferRecallResourceType(item.uri),
        category: item.category,
        score: item.score,
        abstractPreview: deps.previewText(item.abstract || item.overview, deps.cfg.traceRecallPreviewChars),
        displayed: true,
      })),
      ...(result.skills ?? []).map((item) => ({
        uri: item.uri,
        resourceType: deps.inferRecallResourceType(item.uri),
        category: item.category,
        score: item.score,
        abstractPreview: deps.previewText(item.abstract || item.overview, deps.cfg.traceRecallPreviewChars),
        displayed: true,
      })),
    ];
    await deps.traceRecorder?.recordAndFlush?.({
      schemaVersion: "1.0",
      traceId: deps.createTraceId("ov_search"),
      ts: Date.now(),
      sessionId: traceCtx?.sessionId,
      sessionKey: traceCtx?.sessionKey,
      ovSessionId: traceCtx?.ovSessionId,
      agentId,
      source: "ov_search",
      operationType: "semantic_find",
      resourceTypes: [...new Set(searches.map((search) => search.resourceType).filter((resourceType): resourceType is RecallResourceType => resourceType !== "archive"))],
      trigger: deps.boundTraceQuery(query, deps.cfg.traceRecallQueryMaxChars),
      searches,
      selected,
      stats: {
        candidateCount: searches.reduce((sum, search) => sum + search.results.length, 0),
        selectedCount: selected.length,
        injectedCount: 0,
      },
    });
    return {
      content: [{ type: "text" as const, text: formatOVSearchText(query, searchUri, result) }],
      details: {
        action: "searched",
        query,
        uri: searchUri,
        memories: result.memories ?? [],
        resources: result.resources ?? [],
        skills: result.skills ?? [],
        total: result.total ?? 0,
      },
    };
  };

  return { readOpenVikingContent, multiReadOpenVikingContent, listOpenVikingDirectory, searchOpenViking };
}
