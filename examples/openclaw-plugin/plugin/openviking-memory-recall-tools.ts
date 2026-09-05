import { Type } from "@sinclair/typebox";

import type { FindResult, FindResultItem, SearchContextEntry, SearchContextResult } from "../client.js";
import type { BuildMemoryLinesWithBudgetOptions } from "../auto-recall.js";
import type { RankingOptions } from "../memory-ranking.js";
import type { EffectiveQueryConfig, QueryConfigContext, RuntimeQueryParams } from "../query-config.js";
import type { RecallResourceType } from "../registries/recall-resource-types.js";
import type {
  RecallTraceEntry,
  RecallTraceResult,
} from "../recall-trace.js";

export type OpenVikingMemoryRecallToolContext = {
  sessionKey?: string;
  sessionId?: string;
  agentId?: string;
  senderId?: string;
  requesterSenderId?: string;
};

export type OpenVikingMemoryRecallSession = {
  sessionId?: string;
  sessionKey?: string;
  ovSessionId?: string;
  agentId: string;
  actorPeerId?: string;
};

export type OpenVikingMemoryRecallClient = {
  find: (
    query: string,
    options: {
      targetUri?: string;
      limit: number;
      scoreThreshold: number;
      contextType?: "memory" | "resource";
      actorPeerId?: string;
    },
  ) => Promise<FindResult>;
  searchContext: (
    query: string,
    options: {
      sessionId?: string;
      limit?: number;
      scoreThreshold?: number;
      contextType?: string | string[];
      queryExpansion?: "off" | "auto";
      maxTokens?: number;
      detail?: "abstract" | "overview" | "full";
      peerScope?: "actor" | "all";
      actorPeerId?: string;
    },
  ) => Promise<SearchContextResult>;
  read: (uri: string, agentId?: string) => Promise<string>;
  getDefaultAgentId: () => string;
};

export type OpenVikingMemoryRecallToolsDeps = {
  registerTool: (toolOrFactory: unknown, opts: { name: string }) => void;
  getClient: () => Promise<OpenVikingMemoryRecallClient>;
  queryConfigStore: {
    getEffective: (
      ctx: QueryConfigContext,
      request?: RuntimeQueryParams,
    ) => Promise<EffectiveQueryConfig>;
  };
  toQueryConfigContext: (session: OpenVikingMemoryRecallSession) => QueryConfigContext;
  resolvePluginSessionRouting: (
    ctx?: OpenVikingMemoryRecallToolContext,
  ) => OpenVikingMemoryRecallSession;
  isBypassedSession: (ctx?: OpenVikingMemoryRecallToolContext) => boolean;
  makeBypassedToolResult: (toolName: string) => unknown;
  resolveRecallSearchPlan: (
    resourceTypes: unknown,
    ctx: { ovSessionId?: string; agentId?: string },
  ) => {
    resourceTypes: RecallResourceType[];
    searches: Array<{ resourceType: RecallResourceType; contextType: "memory" | "resource" }>;
    skipped: Array<{ resourceType: RecallResourceType; reason: "missing_session" }>;
  };
  postProcessMemories: (
    items: FindResultItem[],
    options: { limit: number; scoreThreshold: number },
  ) => FindResultItem[];
  pickMemoriesForInjection: (
    items: FindResultItem[],
    limit: number,
    queryText: string,
    scoreThreshold: number,
    rankingOptions?: RankingOptions,
  ) => FindResultItem[];
  buildMemoryLinesWithBudget: (
    memories: FindResultItem[],
    readFn: (uri: string) => Promise<string>,
    options: BuildMemoryLinesWithBudgetOptions,
  ) => Promise<{ lines: string[]; estimatedTokens: number }>;
  inferRecallResourceType: (uri: string) => RecallResourceType | undefined;
  createTraceId: (source: string) => string;
  boundTraceQuery: (query: string, maxChars: number) => { query: string; queryTruncated?: boolean };
  previewText: (value: unknown, maxChars: number) => string | undefined;
  traceRecorder?: { recordAndFlush?: (entry: RecallTraceEntry) => Promise<unknown> };
  cfg: {
    recallTargetTypes: RecallResourceType[];
    traceRecallMaxResultsPerSearch: number;
    traceRecallPreviewChars: number;
    traceRecallQueryMaxChars: number;
    logFindRequests: boolean;
  };
  logger: {
    info?: (message: string) => void;
  };
};

function toTraceResult(
  item: SearchContextEntry,
  deps: OpenVikingMemoryRecallToolsDeps,
): RecallTraceResult {
  const resourceType = deps.inferRecallResourceType(item.uri);
  return {
    uri: item.uri,
    resourceType,
    category: item.category,
    score: item.score,
    abstractPreview: deps.previewText(item.text, deps.cfg.traceRecallPreviewChars),
    resultType: resourceType === "resource" ? "resource" : "memory",
  };
}

function toLegacyTraceResult(
  item: FindResultItem,
  resultType: RecallTraceResult["resultType"],
  deps: OpenVikingMemoryRecallToolsDeps,
): RecallTraceResult {
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

const CHARS_PER_TOKEN = 4;

export function registerOpenVikingMemoryRecallTools(
  deps: OpenVikingMemoryRecallToolsDeps,
): void {
  deps.registerTool(
    (ctx: OpenVikingMemoryRecallToolContext) => ({
      name: "memory_recall",
      label: "Memory Recall (OpenViking)",
      description:
        "Search long-term memories from OpenViking. Use when you need past user preferences, facts, or decisions.",
      parameters: Type.Object({
        query: Type.String({ description: "Search query" }),
        limit: Type.Optional(
          Type.Number({ description: "Recall result target (tool/config value, otherwise server default)" }),
        ),
        scoreThreshold: Type.Optional(
          Type.Number({ description: "Minimum score (0-1, default: plugin config)" }),
        ),
        targetUri: Type.Optional(
          Type.String({ description: "Exact search scope URI; preserves the legacy targeted recall path" }),
        ),
        resourceTypes: Type.Optional(
          Type.Array(Type.String({ description: "resource, user, or agent" })),
        ),
      }),
      async execute(_toolCallId: string, params: Record<string, unknown>) {
        if (deps.isBypassedSession(ctx)) {
          return deps.makeBypassedToolResult("memory_recall");
        }
        const session = deps.resolvePluginSessionRouting(ctx);
        const { query } = params as { query: string };
        const hasTargetUri = typeof (params as { targetUri?: unknown }).targetUri === "string";
        const queryConfig = await deps.queryConfigStore.getEffective(deps.toQueryConfigContext(session), {
          recallLimit: typeof (params as { limit?: number }).limit === "number" ? (params as { limit: number }).limit : undefined,
          scoreThreshold: typeof (params as { scoreThreshold?: number }).scoreThreshold === "number" ? (params as { scoreThreshold: number }).scoreThreshold : undefined,
          targetUri: hasTargetUri ? (params as { targetUri: string }).targetUri : undefined,
          resourceTypes: Object.prototype.hasOwnProperty.call(params, "resourceTypes")
            ? (params as { resourceTypes?: unknown }).resourceTypes as RuntimeQueryParams["resourceTypes"]
            : undefined,
        });
        const limit = queryConfig.recallLimit;
        const limitSource = queryConfig.sources?.recallLimit ?? "static";
        const limitConfigured = limitSource !== "default";
        const scoreThreshold = queryConfig.scoreThreshold;
        const requestedResourceTypes = Object.prototype.hasOwnProperty.call(params, "resourceTypes")
          ? (params as { resourceTypes?: unknown }).resourceTypes
          : queryConfig.resourceTypes;
        const searchPlan = deps.resolveRecallSearchPlan(requestedResourceTypes ?? deps.cfg.recallTargetTypes, {
          ovSessionId: session.ovSessionId,
          agentId: session.agentId,
        });
        const contextTypes = [...new Set(searchPlan.searches.map((search) => search.contextType))];
        const maxTokens = Math.min(
          32_000,
          Math.max(64, Math.round(queryConfig.maxInjectedChars / CHARS_PER_TOKEN)),
        );

        const recallClient = await deps.getClient();
        if (deps.cfg.logFindRequests) {
          deps.logger.info?.(
            `openviking: memory_recall X-OpenViking-Actor-Peer="${session.actorPeerId ?? "none"}" ` +
              `(plugin defaultAgentId="${recallClient.getDefaultAgentId()}" is unused when session context is present)`,
          );
        }

        if (hasTargetUri) {
          const targetUri = queryConfig.targetUri;
          if (!targetUri) {
            throw new Error("targetUri must be a non-empty viking:// URI");
          }
          const requestLimit = queryConfig.candidateLimit;
          const startedAt = Date.now();
          const result = await recallClient.find(query, {
            targetUri,
            limit: requestLimit,
            scoreThreshold: 0,
            actorPeerId: session.actorPeerId,
          });
          const durationMs = Date.now() - startedAt;
          const leafOnly = (result.memories ?? []).filter((item) => !item.level || item.level === 2);
          const processed = deps.postProcessMemories(leafOnly, {
            limit: requestLimit,
            scoreThreshold,
          });
          const memories = deps.pickMemoriesForInjection(processed, limit, query, scoreThreshold, {
            weights: queryConfig.rankingWeights,
            categoryWeights: queryConfig.categoryWeights,
            resourceTypeWeights: queryConfig.resourceTypeWeights,
          });
          const traceResults = [
            ...(result.memories ?? []).map((item) => toLegacyTraceResult(item, "memory", deps)),
            ...(result.resources ?? []).map((item) => toLegacyTraceResult(item, "resource", deps)),
          ].slice(0, deps.cfg.traceRecallMaxResultsPerSearch);
          const recordTargetedTrace = async (injectedUris: Set<string>) => {
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
              resourceTypes: [deps.inferRecallResourceType(targetUri) ?? "resource"],
              trigger: deps.boundTraceQuery(query, deps.cfg.traceRecallQueryMaxChars),
              searches: [{
                resourceType: deps.inferRecallResourceType(targetUri) ?? "resource",
                targetUriInput: targetUri,
                targetUriResolved: targetUri,
                limit: requestLimit,
                scoreThreshold,
                durationMs,
                total: result.total ?? traceResults.length,
                results: traceResults,
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
            await recordTargetedTrace(new Set());
            return {
              content: [{ type: "text", text: "No relevant OpenViking memories found." }],
              details: { count: 0, total: result.total ?? 0, scoreThreshold, targetUri },
            };
          }
          const { lines: memoryLines } = await deps.buildMemoryLinesWithBudget(
            memories,
            (uri) => recallClient.read(uri, session.actorPeerId),
            {
              recallPreferAbstract: false,
              recallMaxInjectedChars: queryConfig.maxInjectedChars,
            },
          );
          if (memoryLines.length === 0) {
            await recordTargetedTrace(new Set());
            return {
              content: [{
                type: "text",
                text: `No complete OpenViking memories fit recallMaxInjectedChars=${queryConfig.maxInjectedChars}.`,
              }],
              details: {
                count: 0,
                memories,
                total: result.total ?? memories.length,
                scoreThreshold,
                requestLimit,
                targetUri,
                recallMaxInjectedChars: queryConfig.maxInjectedChars,
              },
            };
          }
          await recordTargetedTrace(new Set(memories.slice(0, memoryLines.length).map((item) => item.uri)));
          return {
            content: [{
              type: "text",
              text: `Found ${memoryLines.length} memories:\n\n${memoryLines.join("\n")}`,
            }],
            details: {
              count: memoryLines.length,
              memories,
              total: result.total ?? memories.length,
              scoreThreshold,
              requestLimit,
              targetUri,
              recallMaxInjectedChars: queryConfig.maxInjectedChars,
            },
          };
        }

        const startedAt = Date.now();
        const result = await recallClient.searchContext(query, {
          sessionId: session.ovSessionId,
          ...(limitConfigured ? { limit } : {}),
          scoreThreshold,
          contextType: contextTypes.length === 1 ? contextTypes[0] : contextTypes,
          queryExpansion: "auto",
          maxTokens,
          detail: queryConfig.recallPreferAbstract ? "abstract" : undefined,
          peerScope: "actor",
          actorPeerId: session.actorPeerId,
        });
        const durationMs = Date.now() - startedAt;
        const entries = (result.entries ?? []).filter((entry) => Boolean(entry.uri));
        const candidateCount = typeof result.stats?.candidates === "number"
          ? result.stats.candidates
          : entries.length;
        const rawRetrievalErrors = result.stats?.retrieval_errors;
        const retrievalErrors = Array.isArray(rawRetrievalErrors)
          ? rawRetrievalErrors.map((error) => String(error))
          : [];
        const memoryRecallSearches: RecallTraceEntry["searches"] = searchPlan.searches.map((search) => {
          const matching = entries.filter((entry) => {
            const isResource = deps.inferRecallResourceType(entry.uri) === "resource";
            return search.contextType === "resource" ? isResource : !isResource;
          });
          return {
            resourceType: search.resourceType,
            contextType: search.contextType,
            limit,
            scoreThreshold,
            durationMs,
            total: matching.length,
            results: matching
              .map((entry) => toTraceResult(entry, deps))
              .slice(0, deps.cfg.traceRecallMaxResultsPerSearch),
            error: retrievalErrors.length > 0 ? retrievalErrors.join("; ") : undefined,
          };
        });
        const rendered = result.digest?.trim() || result.rendered?.trim() || "";
        const displayedUris = new Set(rendered ? entries.map((entry) => entry.uri) : []);
        const memories = entries.map((entry) => ({
          uri: entry.uri,
          category: entry.category,
          score: entry.score,
          abstract: entry.text,
        }));
        const recordMemoryRecallTrace = async (injectedUris: Set<string>) => {
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
            resourceTypes: searchPlan.resourceTypes,
            trigger: deps.boundTraceQuery(query, deps.cfg.traceRecallQueryMaxChars),
            searches: memoryRecallSearches,
            selected: entries.map((entry) => ({
              uri: entry.uri,
              resourceType: deps.inferRecallResourceType(entry.uri),
              category: entry.category,
              score: entry.score,
              abstractPreview: deps.previewText(entry.text, deps.cfg.traceRecallPreviewChars),
              injected: injectedUris.has(entry.uri),
              displayed: injectedUris.has(entry.uri),
            })),
            stats: {
              candidateCount,
              selectedCount: entries.length,
              injectedCount: injectedUris.size,
              estimatedTokens: typeof result.stats?.used_tokens === "number"
                ? result.stats.used_tokens
                : undefined,
            },
          });
        };
        if (entries.length === 0 || !rendered) {
          await recordMemoryRecallTrace(new Set());
          return {
            content: [{ type: "text", text: "No relevant OpenViking memories found." }],
            details: { count: 0, total: candidateCount, scoreThreshold, limitSource },
          };
        }
        await recordMemoryRecallTrace(displayedUris);
        return {
          content: [{ type: "text", text: rendered }],
          details: {
            count: entries.length,
            memories,
            total: candidateCount,
            scoreThreshold,
            requestLimit: limitConfigured ? limit : undefined,
            limitSource,
            recallMaxInjectedChars: queryConfig.maxInjectedChars,
          },
        };
      },
    }),
    { name: "memory_recall" },
  );
}
