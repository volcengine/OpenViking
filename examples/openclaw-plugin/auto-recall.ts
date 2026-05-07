import type { FindResult, FindResultItem, OpenVikingClient } from "./client.js";
import type { MemoryOpenVikingConfig } from "./config.js";
import {
  clampScore,
  pickMemoriesForInjection,
  postProcessMemories,
  summarizeInjectionMemories,
  trimForLog,
  toJsonLog,
} from "./memory-ranking.js";
import { quickRecallPrecheck, withTimeout } from "./process-manager.js";
import { sanitizeUserTextForCapture } from "./text-utils.js";

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

/** Estimate token count using chars/4 heuristic for diagnostics. */
export function estimateTokenCount(text: string): number {
  if (!text) return 0;
  return Math.ceil(text.length / 4);
}

export type BuildMemoryLinesOptions = {
  recallPreferAbstract: boolean;
};

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
    lines.push(`- [${item.category ?? "memory"}] ${content}`);
  }
  return lines;
}

export type BuildMemoryLinesWithBudgetOptions = BuildMemoryLinesOptions & {
  recallMaxInjectedChars?: number;
  recallTokenBudget?: number;
};

export type AutoRecallTraceResult =
  | "disabled"
  | "empty_query"
  | "precheck_failed"
  | "no_hits"
  | "none_fit_budget"
  | "injected";

export type AutoRecallTraceMemory = {
  rank: number;
  uri: string;
  category: string | null;
  abstract: string;
  score: number;
  level: number | null;
  is_leaf: boolean;
  injected: boolean;
  lineChars?: number;
  skipReason?: "budget";
};

export type AutoRecallTrace = {
  result: AutoRecallTraceResult;
  query: {
    chars: number;
    preview: string;
  };
  settings: {
    recallLimit: number;
    recallResources: boolean;
    recallPreferAbstract: boolean;
  };
  targets: string[];
  candidateLimit: number;
  counts: {
    raw: number;
    unique: number;
    leafOnly: number;
    processed: number;
    selected: number;
    injected: number;
    skippedByBudget: number;
    searchFailures: number;
  };
  budget: {
    recallMaxInjectedChars: number;
    injectedChars: number;
    estimatedTokens: number;
  };
  memories: AutoRecallTraceMemory[];
  reason?: string;
};

function makeBaseTrace(
  cfg: Required<MemoryOpenVikingConfig>,
  queryText: string,
): AutoRecallTrace {
  const candidateLimit = Math.max(cfg.recallLimit * 4, 20);
  const targets = [
    "viking://user/memories",
    "viking://agent/memories",
    ...(cfg.recallResources ? ["viking://resources"] : []),
  ];

  return {
    result: "no_hits",
    query: {
      chars: queryText.length,
      preview: trimForLog(queryText, 180),
    },
    settings: {
      recallLimit: cfg.recallLimit,
      recallResources: cfg.recallResources,
      recallPreferAbstract: cfg.recallPreferAbstract,
    },
    targets,
    candidateLimit,
    counts: {
      raw: 0,
      unique: 0,
      leafOnly: 0,
      processed: 0,
      selected: 0,
      injected: 0,
      skippedByBudget: 0,
      searchFailures: 0,
    },
    budget: {
      recallMaxInjectedChars: cfg.recallMaxInjectedChars,
      injectedChars: 0,
      estimatedTokens: 0,
    },
    memories: [],
  };
}

function summarizeTraceMemory(
  item: FindResultItem,
  index: number,
  extra: {
    injected: boolean;
    lineChars?: number;
    skipReason?: "budget";
  },
): AutoRecallTraceMemory {
  return {
    rank: index + 1,
    uri: item.uri,
    category: item.category ?? null,
    abstract: trimForLog(item.abstract?.trim() || item.overview?.trim() || item.uri, 180),
    score: clampScore(item.score),
    level: typeof item.level === "number" ? item.level : null,
    is_leaf: item.level === 2,
    injected: extra.injected,
    ...(typeof extra.lineChars === "number" ? { lineChars: extra.lineChars } : {}),
    ...(extra.skipReason ? { skipReason: extra.skipReason } : {}),
  };
}

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
): Promise<{
  lines: string[];
  estimatedTokens: number;
  injectedChars: number;
  skippedByBudget: number;
  traceMemories: AutoRecallTraceMemory[];
}> {
  const charBudget = options.recallMaxInjectedChars ?? options.recallTokenBudget ?? 0;
  const lines: string[] = [];
  const traceMemories: AutoRecallTraceMemory[] = [];
  let totalTokens = 0;
  let totalChars = 0;

  for (const [index, item] of memories.entries()) {
    if (totalChars >= charBudget) {
      traceMemories.push(summarizeTraceMemory(item, index, {
        injected: false,
        skipReason: "budget",
      }));
      continue;
    }

    const content = await resolveMemoryContent(item, readFn, options);
    const line = `- [${item.category ?? "memory"}] ${content}`;
    const separatorChars = lines.length > 0 ? 1 : 0;
    const projectedChars = totalChars + separatorChars + line.length;

    if (projectedChars > charBudget) {
      traceMemories.push(summarizeTraceMemory(item, index, {
        injected: false,
        lineChars: line.length,
        skipReason: "budget",
      }));
      continue;
    }

    const lineTokens = estimateTokenCount(line);

    lines.push(line);
    totalTokens += lineTokens;
    totalChars = projectedChars;
    traceMemories.push(summarizeTraceMemory(item, index, {
      injected: true,
      lineChars: line.length,
    }));
  }

  return {
    lines,
    estimatedTokens: totalTokens,
    injectedChars: totalChars,
    skippedByBudget: traceMemories.filter((item) => item.skipReason === "budget").length,
    traceMemories,
  };
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

export async function buildAutoRecallContext(params: {
  cfg: Required<MemoryOpenVikingConfig>;
  client: OpenVikingClient;
  agentId: string;
  queryText: string;
  logger: Logger;
  verbose?: (message: string) => void;
}): Promise<{ block?: string; memoryCount: number; estimatedTokens: number; trace: AutoRecallTrace }> {
  const { cfg, client, agentId, queryText, logger, verbose } = params;
  const baseTrace = makeBaseTrace(cfg, queryText);

  if (!cfg.autoRecall || queryText.length < 5) {
    return {
      memoryCount: 0,
      estimatedTokens: 0,
      trace: {
        ...baseTrace,
        result: cfg.autoRecall ? "empty_query" : "disabled",
      },
    };
  }

  const precheck = await quickRecallPrecheck(cfg.baseUrl);
  if (!precheck.ok) {
    verbose?.(`openviking: skipping auto-recall because precheck failed (${precheck.reason})`);
    return {
      memoryCount: 0,
      estimatedTokens: 0,
      trace: {
        ...baseTrace,
        result: "precheck_failed",
        reason: precheck.reason,
      },
    };
  }

  return withTimeout(
    (async () => {
      const candidateLimit = baseTrace.candidateLimit;
      const autoRecallPromises: Promise<FindResult>[] = [
        client.find(queryText, {
          targetUri: "viking://user/memories",
          limit: candidateLimit,
          scoreThreshold: 0,
        }, agentId),
        client.find(queryText, {
          targetUri: "viking://agent/memories",
          limit: candidateLimit,
          scoreThreshold: 0,
        }, agentId),
      ];
      if (cfg.recallResources) {
        autoRecallPromises.push(
          client.find(queryText, {
            targetUri: "viking://resources",
            limit: candidateLimit,
            scoreThreshold: 0,
          }, agentId),
        );
      }
      const autoRecallSettled = await Promise.allSettled(autoRecallPromises);

      const allMemories: FindResultItem[] = [];
      let searchFailures = 0;
      for (const s of autoRecallSettled) {
        if (s.status === "fulfilled") {
          allMemories.push(...(s.value.memories ?? []), ...(s.value.resources ?? []));
        } else {
          searchFailures += 1;
          logger.warn?.(`openviking: auto-recall search failed: ${String(s.reason)}`);
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
      const trace: AutoRecallTrace = {
        ...baseTrace,
        counts: {
          raw: allMemories.length,
          unique: uniqueMemories.length,
          leafOnly: leafOnly.length,
          processed: processed.length,
          selected: memories.length,
          injected: 0,
          skippedByBudget: 0,
          searchFailures,
        },
        memories: memories.map((memory, index) =>
          summarizeTraceMemory(memory, index, { injected: false }),
        ),
      };

      if (memories.length === 0) {
        return { memoryCount: 0, estimatedTokens: 0, trace: { ...trace, result: "no_hits" } };
      }

      const {
        lines: memoryLines,
        estimatedTokens,
        injectedChars,
        skippedByBudget,
        traceMemories,
      } = await buildMemoryLinesWithBudget(
        memories,
        (uri) => client.read(uri, agentId),
        {
          recallPreferAbstract: cfg.recallPreferAbstract,
          recallMaxInjectedChars: cfg.recallMaxInjectedChars,
        },
      );
      const budgetedTrace: AutoRecallTrace = {
        ...trace,
        counts: {
          ...trace.counts,
          injected: memoryLines.length,
          skippedByBudget,
        },
        budget: {
          recallMaxInjectedChars: cfg.recallMaxInjectedChars,
          injectedChars,
          estimatedTokens,
        },
        memories: traceMemories,
      };

      if (memoryLines.length === 0) {
        verbose?.(
          `openviking: skipping auto-recall injection; no complete memories fit maxInjectedChars=${cfg.recallMaxInjectedChars}`,
        );
        return {
          memoryCount: 0,
          estimatedTokens: 0,
          trace: { ...budgetedTrace, result: "none_fit_budget" },
        };
      }

      const block = buildRecallContextBlock(memoryLines);
      verbose?.(
        `openviking: injecting ${memoryLines.length} memories (${block.length} chars, ~${estimatedTokens} tokens, maxInjectedChars=${cfg.recallMaxInjectedChars})`,
      );
      verbose?.(
        `openviking: inject-detail ${toJsonLog({ count: memories.length, memories: summarizeInjectionMemories(memories) })}`,
      );

      return {
        block,
        memoryCount: memoryLines.length,
        estimatedTokens,
        trace: { ...budgetedTrace, result: "injected" },
      };
    })(),
    AUTO_RECALL_TIMEOUT_MS,
    "openviking: auto-recall search timeout",
  );
}
