import type { FindResult, FindResultItem, OpenVikingClient } from "./client.js";
import type { MemoryOpenVikingConfig } from "./config.js";
import {
  pickMemoriesForInjection,
  postProcessMemories,
  summarizeInjectionMemories,
  toJsonLog,
} from "./memory-ranking.js";
import { quickRecallPrecheck, withTimeout } from "./process-manager.js";
import {
  resolveRecallSearchPlan,
  type RecallResourceType,
  type RecallTraceEntry,
  type RecallTraceResult,
} from "./recall-trace.js";
import { sanitizeUserTextForCapture } from "./text-utils.js";
import { estimateTextTokens } from "./token-estimator.js";

const AUTO_RECALL_TIMEOUT_MS = 5_000;
const RECALL_QUERY_MAX_CHARS = 4_000;
export const AUTO_RECALL_SOURCE_MARKER = "Source: openviking-auto-recall";

type Logger = {
  info: (msg: string) => void;
  warn?: (msg: string) => void;
};

export type PreparedRecallQuery = {
  query: string;
  truncated: boolean;
  originalChars: number;
  finalChars: number;
};

export function prepareRecallQuery(rawText: string): PreparedRecallQuery {
  const sanitized = sanitizeUserTextForCapture(rawText).trim();
  const originalChars = sanitized.length;

  if (!sanitized) {
    return {
      query: "",
      truncated: false,
      originalChars: 0,
      finalChars: 0,
    };
  }

  const query =
    sanitized.length > RECALL_QUERY_MAX_CHARS
      ? sanitized.slice(0, RECALL_QUERY_MAX_CHARS).trim()
      : sanitized;

  return {
    query,
    truncated: sanitized.length > RECALL_QUERY_MAX_CHARS,
    originalChars,
    finalChars: query.length,
  };
}

/** Estimate token count using the shared CJK-aware fallback for diagnostics. */
export function estimateTokenCount(text: string): number {
  return estimateTextTokens(text);
}

export type BuildMemoryLinesOptions = {
  recallPreferAbstract: boolean;
  includeUri?: boolean;
};

function memoryCategory(item: FindResultItem): string {
  return item.category?.trim() || "memory";
}

function indentContent(content: string): string {
  return content
    .split("\n")
    .map((line) => `  ${line}`)
    .join("\n");
}

function formatMemoryLine(
  item: FindResultItem,
  content: string,
  options: BuildMemoryLinesOptions,
): string {
  const category = memoryCategory(item);
  if (!options.includeUri) {
    return `- [${category}] ${content}`;
  }

  return [
    `- [${category}]`,
    `  <uri>${item.uri}</uri>`,
    indentContent(content),
  ].join("\n");
}

async function resolveMemoryContent(
  item: FindResultItem,
  readFn: (uri: string) => Promise<string>,
  options: BuildMemoryLinesOptions,
): Promise<string> {
  let content: string;

  if (options.recallPreferAbstract && item.abstract?.trim()) {
    content = item.abstract.trim();
  } else if (item.level === 2) {
    try {
      const fullContent = await readFn(item.uri);
      content =
        fullContent && typeof fullContent === "string" && fullContent.trim()
          ? fullContent.trim()
          : (item.abstract?.trim() || item.uri);
    } catch {
      content = item.abstract?.trim() || item.uri;
    }
  } else {
    content = item.abstract?.trim() || item.uri;
  }

  return content;
}

export async function buildMemoryLines(
  memories: FindResultItem[],
  readFn: (uri: string) => Promise<string>,
  options: BuildMemoryLinesOptions,
): Promise<string[]> {
  const lines: string[] = [];
  for (const item of memories) {
    const content = await resolveMemoryContent(item, readFn, options);
    lines.push(formatMemoryLine(item, content, options));
  }
  return lines;
}

export type BuildMemoryLinesWithBudgetOptions = BuildMemoryLinesOptions & {
  recallMaxInjectedChars?: number;
  recallTokenBudget?: number;
};

/**
 * Build memory lines with a character budget constraint.
 *
 * Individual memories are never truncated. A memory that cannot fit within the
 * remaining character budget is skipped so only complete memory entries are
 * injected.
 */
export async function buildMemoryLinesWithBudget(
  memories: FindResultItem[],
  readFn: (uri: string) => Promise<string>,
  options: BuildMemoryLinesWithBudgetOptions,
): Promise<{ lines: string[]; estimatedTokens: number }> {
  const charBudget = options.recallMaxInjectedChars ?? options.recallTokenBudget ?? 0;
  const lines: string[] = [];
  let totalTokens = 0;
  let totalChars = 0;

  for (const item of memories) {
    if (totalChars >= charBudget) {
      break;
    }

    const content = await resolveMemoryContent(item, readFn, options);
    const line = formatMemoryLine(item, content, options);
    const separatorChars = lines.length > 0 ? 1 : 0;
    const projectedChars = totalChars + separatorChars + line.length;

    if (projectedChars > charBudget) {
      continue;
    }

    const lineTokens = estimateTokenCount(line);

    lines.push(line);
    totalTokens += lineTokens;
    totalChars = projectedChars;
  }

  return { lines, estimatedTokens: totalTokens };
}

export function buildRecallContextBlock(memoryLines: string[]): string {
  return [
    "<relevant-memories>",
    AUTO_RECALL_SOURCE_MARKER,
    "The following OpenViking memories may be relevant:",
    ...memoryLines,
    "</relevant-memories>",
  ].join("\n");
}

function newTraceId(): string {
  return `recall_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

function preview(value: string | undefined, maxChars: number): string | undefined {
  const trimmed = value?.trim();
  if (!trimmed) return undefined;
  return trimmed.length > maxChars ? trimmed.slice(0, maxChars) : trimmed;
}

function toTraceResults(items: FindResultItem[], resourceType: RecallResourceType): RecallTraceResult[] {
  return items.map((item) => ({
    uri: item.uri,
    resourceType,
    category: item.category,
    score: item.score,
    level: item.level,
    abstractPreview: preview(item.abstract ?? item.overview, 240),
    resultType: resourceType === "resource" ? "resource" : "memory",
  }));
}

function boundTraceQuery(query: string, maxChars: number): { query: string; queryTruncated?: boolean } {
  if (query.length <= maxChars) {
    return { query };
  }
  return { query: query.slice(0, maxChars), queryTruncated: true };
}

export async function buildAutoRecallContext(params: {
  cfg: Required<MemoryOpenVikingConfig>;
  client: OpenVikingClient;
  agentId: string;
  queryText: string;
  logger: Logger;
  verbose?: (message: string) => void;
  traceRecorder?: { record(entry: RecallTraceEntry): void; recordAndFlush?: (entry: RecallTraceEntry) => Promise<unknown> };
  sessionId?: string;
  sessionKey?: string;
  ovSessionId?: string;
  rawUserTextPreview?: string;
  queryTruncated?: boolean;
  resourceTypes?: RecallResourceType[];
}): Promise<{ block?: string; memoryCount: number; estimatedTokens: number }> {
  const { cfg, client, agentId, queryText, logger, verbose } = params;

  if (!cfg.autoRecall || queryText.length < 5) {
    return { memoryCount: 0, estimatedTokens: 0 };
  }

  const precheck = await quickRecallPrecheck(client, agentId);
  if (!precheck.ok) {
    verbose?.(`openviking: skipping auto-recall because precheck failed (${precheck.reason})`);
    return { memoryCount: 0, estimatedTokens: 0 };
  }

  return withTimeout(
    (async () => {
      const candidateLimit = Math.max(cfg.recallLimit * 4, 20);
      const searchPlan = resolveRecallSearchPlan(params.resourceTypes ?? cfg.recallTargetTypes, {
        ovSessionId: params.ovSessionId,
        agentId,
      });
      const traceSearches: RecallTraceEntry["searches"] = searchPlan.skipped.map((skipped) => ({
        resourceType: skipped.resourceType,
        limit: candidateLimit,
        scoreThreshold: 0,
        durationMs: 0,
        total: 0,
        results: [],
        error: skipped.reason,
      }));
      const autoRecallPromises: Promise<{
        resourceType: RecallResourceType;
        targetUri: string;
        result?: FindResult;
        durationMs: number;
      }>[] = searchPlan.searches.map(async (search) => {
        const start = Date.now();
        const result = await client.find(queryText, {
          targetUri: search.targetUri,
          limit: candidateLimit,
          scoreThreshold: 0,
        }, agentId);
        return {
          resourceType: search.resourceType,
          targetUri: search.targetUri,
          result,
          durationMs: Date.now() - start,
        };
      });
      const autoRecallSettled = await Promise.allSettled(autoRecallPromises);

      const allMemories: FindResultItem[] = [];
      for (const s of autoRecallSettled) {
        if (s.status === "fulfilled") {
          const result = s.value.result ?? {};
          const memories = result.memories ?? [];
          const resources = result.resources ?? [];
          allMemories.push(...memories, ...resources);
          traceSearches.push({
            resourceType: s.value.resourceType,
            targetUriInput: s.value.targetUri,
            limit: candidateLimit,
            scoreThreshold: 0,
            durationMs: s.value.durationMs,
            total: result.total ?? memories.length + resources.length + (result.skills?.length ?? 0),
            results: [
              ...toTraceResults(memories, s.value.resourceType),
              ...toTraceResults(resources, "resource"),
              ...(result.skills ?? []).map((item): RecallTraceResult => ({
                uri: item.uri,
                resourceType: s.value.resourceType,
                category: item.category,
                score: item.score,
                level: item.level,
                abstractPreview: preview(item.abstract ?? item.overview, 240),
                resultType: "skill",
              })),
            ].slice(0, cfg.traceRecallMaxResultsPerSearch),
          });
        } else {
          logger.warn?.(`openviking: auto-recall search failed: ${String(s.reason)}`);
          const failedIndex = traceSearches.length;
          const search = searchPlan.searches[failedIndex - searchPlan.skipped.length];
          traceSearches.push({
            resourceType: search?.resourceType ?? "user",
            targetUriInput: search?.targetUri,
            limit: candidateLimit,
            scoreThreshold: 0,
            durationMs: 0,
            total: 0,
            results: [],
            error: String(s.reason),
          });
        }
      }

      const uniqueMemories = allMemories.filter((memory, index, self) =>
        index === self.findIndex((m) => m.uri === memory.uri)
      );
      const leafOnly = uniqueMemories.filter((m) => !m.level || m.level === 2);
      const processed = postProcessMemories(leafOnly, {
        limit: candidateLimit,
        scoreThreshold: cfg.recallScoreThreshold,
      });
      const memories = pickMemoriesForInjection(processed, cfg.recallLimit, queryText);

      const recordTrace = async (injectedMemories: FindResultItem[], injectedCount: number, estimatedTokens?: number) => {
        const entry: RecallTraceEntry = {
          schemaVersion: "1.0",
          traceId: newTraceId(),
          ts: Date.now(),
          sessionId: params.sessionId,
          sessionKey: params.sessionKey,
          ovSessionId: params.ovSessionId,
          agentId,
          source: "auto_recall",
          operationType: "semantic_find",
          resourceTypes: searchPlan.resourceTypes,
          trigger: {
            rawUserTextPreview: params.rawUserTextPreview,
            ...boundTraceQuery(queryText, cfg.traceRecallQueryMaxChars),
            derivedKeywords: [],
            queryTruncated: params.queryTruncated || queryText.length > cfg.traceRecallQueryMaxChars,
          },
          searches: traceSearches,
          selected: injectedMemories.map((memory) => ({
            uri: memory.uri,
            resourceType: memory.uri.startsWith("viking://resources") ? "resource" : undefined,
            category: memory.category,
            score: memory.score,
            abstractPreview: preview(memory.abstract ?? memory.overview, cfg.traceRecallPreviewChars),
            injected: true,
          })),
          stats: {
            candidateCount: allMemories.length,
            selectedCount: injectedMemories.length,
            injectedCount,
            estimatedTokens,
          },
        };
        // Trace persistence is diagnostic best-effort; never put JSONL flush latency on the auto-recall critical path.
        params.traceRecorder?.record(entry);
      };

      if (memories.length === 0) {
        await recordTrace([], 0, 0);
        return { memoryCount: 0, estimatedTokens: 0 };
      }

      const { lines: memoryLines, estimatedTokens } = await buildMemoryLinesWithBudget(
        memories,
        (uri) => client.read(uri, agentId),
        {
          recallPreferAbstract: cfg.recallPreferAbstract,
          recallMaxInjectedChars: cfg.recallMaxInjectedChars,
          includeUri: true,
        },
      );

      if (memoryLines.length === 0) {
        verbose?.(
          `openviking: skipping auto-recall injection; no complete memories fit maxInjectedChars=${cfg.recallMaxInjectedChars}`,
        );
        await recordTrace([], 0, 0);
        return { memoryCount: 0, estimatedTokens: 0 };
      }

      const block = buildRecallContextBlock(memoryLines);
      verbose?.(
        `openviking: injecting ${memoryLines.length} memories (${block.length} chars, ~${estimatedTokens} tokens, maxInjectedChars=${cfg.recallMaxInjectedChars})`,
      );
      verbose?.(
        `openviking: inject-detail ${toJsonLog({ count: memories.length, memories: summarizeInjectionMemories(memories) })}`,
      );

      await recordTrace(memories.slice(0, memoryLines.length), memoryLines.length, estimatedTokens);
      return { block, memoryCount: memoryLines.length, estimatedTokens };
    })(),
    AUTO_RECALL_TIMEOUT_MS,
    "openviking: auto-recall search timeout",
  );
}
