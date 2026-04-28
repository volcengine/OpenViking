import type { FindResultItem, OpenVikingClient } from "./client.js";
import type { MemoryOpenVikingConfig } from "./config.js";
import {
  pickMemoriesForInjection,
  postProcessMemories,
  summarizeInjectionMemories,
  toJsonLog,
} from "./memory-ranking.js";
import { withTimeout } from "./process-manager.js";
import { sanitizeUserTextForCapture } from "./text-utils.js";

type RecallLogger = {
  warn?: (message: string) => void;
};

type RecallPrecheckResult =
  | { ok: true }
  | { ok: false; reason: string };

type RecallPromptSectionParams = {
  cfg: Required<MemoryOpenVikingConfig>;
  client: Pick<OpenVikingClient, "find" | "read">;
  logger: RecallLogger;
  queryText: string;
  agentId: string;
  precheck?: () => Promise<RecallPrecheckResult>;
  verboseLog?: (message: string) => void;
};

export type PreparedRecallQuery = {
  query: string;
  truncated: boolean;
  originalChars: number;
  finalChars: number;
};

export type BuildMemoryLinesOptions = {
  recallPreferAbstract: boolean;
  recallMaxContentChars: number;
  logger?: RecallLogger;
};

export type BuildMemoryLinesWithBudgetOptions = BuildMemoryLinesOptions & {
  recallTokenBudget: number;
};

export type RecallPromptSectionResult = {
  section?: string;
  estimatedTokens: number;
  memories: FindResultItem[];
};

const AUTO_RECALL_TIMEOUT_MS = 5_000;
const RECALL_QUERY_MAX_CHARS = 4_000;

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

/** Estimate token count using chars/4 heuristic (adequate for budget enforcement). */
export function estimateTokenCount(text: string): number {
  if (!text) return 0;
  return Math.ceil(text.length / 4);
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
    } catch (err) {
      options.logger?.warn?.(
        `openviking: memory read failed for ${item.uri}: ${String(err)}`,
      );
      content = item.abstract?.trim() || item.uri;
    }
  } else {
    content = item.abstract?.trim() || item.uri;
  }

  if (content.length > options.recallMaxContentChars) {
    content = content.slice(0, options.recallMaxContentChars) + "...";
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

/**
 * Build memory lines with a token budget constraint.
 *
 * The first memory is always included even if its token count exceeds the
 * remaining budget. This is intentional: with recallMaxContentChars=5000, a
 * single line is at most about 1250 tokens, so overshoot is bounded and
 * guarantees at least one memory is surfaced.
 */
export async function buildMemoryLinesWithBudget(
  memories: FindResultItem[],
  readFn: (uri: string) => Promise<string>,
  options: BuildMemoryLinesWithBudgetOptions,
): Promise<{ lines: string[]; estimatedTokens: number }> {
  let budgetRemaining = options.recallTokenBudget;
  const lines: string[] = [];
  let totalTokens = 0;

  for (const item of memories) {
    if (budgetRemaining <= 0) {
      break;
    }

    const content = await resolveMemoryContent(item, readFn, options);
    const line = `- [${item.category ?? "memory"}] ${content}`;
    const lineTokens = estimateTokenCount(line);

    if (lineTokens > budgetRemaining && lines.length > 0) {
      break;
    }

    lines.push(line);
    totalTokens += lineTokens;
    budgetRemaining -= lineTokens;
  }

  return { lines, estimatedTokens: totalTokens };
}

export async function buildRecallPromptSection(
  params: RecallPromptSectionParams,
): Promise<RecallPromptSectionResult> {
  const { agentId, cfg, client, logger, precheck, queryText, verboseLog } = params;

  if (!cfg.autoRecall || queryText.length < 5) {
    return { estimatedTokens: 0, memories: [] };
  }

  if (precheck) {
    const result = await precheck();
    if (!result.ok) {
      verboseLog?.(
        `openviking: skipping auto-recall because precheck failed (${result.reason})`,
      );
      return { estimatedTokens: 0, memories: [] };
    }
  }

  try {
    return await withTimeout(
      (async () => {
        const candidateLimit = Math.max(cfg.recallLimit * 4, 20);
        const recallPromises = [
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
          recallPromises.push(
            client.find(queryText, {
              targetUri: "viking://resources",
              limit: candidateLimit,
              scoreThreshold: 0,
            }, agentId),
          );
        }
        const settled = await Promise.allSettled(recallPromises);

        const allMemories: FindResultItem[] = [];
        for (const result of settled) {
          if (result.status === "fulfilled") {
            allMemories.push(...(result.value.memories ?? []), ...(result.value.resources ?? []));
          } else {
            logger.warn?.(`openviking: auto-recall search failed: ${String(result.reason)}`);
          }
        }

        const uniqueMemories = allMemories.filter((memory, index, self) =>
          index === self.findIndex((candidate) => candidate.uri === memory.uri)
        );
        const leafOnly = uniqueMemories.filter((memory) => !memory.level || memory.level === 2);
        const processed = postProcessMemories(leafOnly, {
          limit: candidateLimit,
          scoreThreshold: cfg.recallScoreThreshold,
        });
        const memories = pickMemoriesForInjection(processed, cfg.recallLimit, queryText);

        if (memories.length === 0) {
          return { estimatedTokens: 0, memories: [] };
        }

        const { lines, estimatedTokens } = await buildMemoryLinesWithBudget(
          memories,
          (uri) => client.read(uri, agentId),
          {
            recallPreferAbstract: cfg.recallPreferAbstract,
            recallMaxContentChars: cfg.recallMaxContentChars,
            recallTokenBudget: cfg.recallTokenBudget,
            logger,
          },
        );

        if (lines.length === 0) {
          return { estimatedTokens: 0, memories: [] };
        }

        verboseLog?.(
          `openviking: injecting ${lines.length} memories (~${estimatedTokens} tokens, budget=${cfg.recallTokenBudget})`,
        );
        verboseLog?.(
          `openviking: inject-detail ${toJsonLog({
            count: memories.length,
            memories: summarizeInjectionMemories(memories.slice(0, lines.length)),
          })}`,
        );

        return {
          section:
            "<relevant-memories>\nThe following OpenViking memories may be relevant:\n" +
            `${lines.join("\n")}\n` +
            "</relevant-memories>",
          estimatedTokens,
          memories: memories.slice(0, lines.length),
        };
      })(),
      AUTO_RECALL_TIMEOUT_MS,
      "openviking: auto-recall search timeout",
    );
  } catch (err) {
    logger.warn?.(`openviking: auto-recall failed: ${String(err)}`);
    return { estimatedTokens: 0, memories: [] };
  }
}
