import type { FindResultItem, OpenVikingClient } from "./client.js";
import type { MemoryOpenVikingConfig } from "./config.js";
import {
  pickMemoriesForInjection,
  postProcessMemories,
  summarizeInjectionMemories,
  toJsonLog,
} from "./memory-ranking.js";
import { withTimeout } from "./process-manager.js";
import { isTranscriptLikeIngest, sanitizeUserTextForCapture } from "./text-utils.js";

type RecallLogger = {
  warn?: (message: string) => void;
}

type RecallPrecheckResult =
  | { ok: true }
  | { ok: false; reason: string }

type RecallPromptSectionParams = {
  cfg: Required<MemoryOpenVikingConfig>;
  client: Pick<OpenVikingClient, "find" | "read">;
  logger: RecallLogger;
  queryText: string;
  agentId: string;
  precheck?: () => Promise<RecallPrecheckResult>;
  verboseLog?: (message: string) => void;
}

export type PreparedRecallQuery = {
  query: string;
  truncated: boolean;
  originalChars: number;
  finalChars: number;
}

export type BuildMemoryLinesOptions = {
  recallPreferAbstract: boolean;
  recallMaxContentChars: number;
}

export type BuildMemoryLinesWithBudgetOptions = BuildMemoryLinesOptions & {
  recallTokenBudget: number;
}

export type RecallPromptSectionResult = {
  section?: string;
  estimatedTokens: number;
  memories: FindResultItem[];
}

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

export function estimateTokenCount(text: string): number {
  if (!text) {
    return 0;
  }
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
    } catch {
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
        const [userSettled, agentSettled] = await Promise.allSettled([
          client.find(
            queryText,
            {
              targetUri: "viking://user/memories",
              limit: candidateLimit,
              scoreThreshold: 0,
            },
            agentId,
          ),
          client.find(
            queryText,
            {
              targetUri: "viking://agent/memories",
              limit: candidateLimit,
              scoreThreshold: 0,
            },
            agentId,
          ),
        ]);

        const userResult =
          userSettled.status === "fulfilled" ? userSettled.value : { memories: [] };
        const agentResult =
          agentSettled.status === "fulfilled" ? agentSettled.value : { memories: [] };

        if (userSettled.status === "rejected") {
          logger.warn?.(
            `openviking: user memories search failed: ${String(userSettled.reason)}`,
          );
        }
        if (agentSettled.status === "rejected") {
          logger.warn?.(
            `openviking: agent memories search failed: ${String(agentSettled.reason)}`,
          );
        }

        const allMemories = [
          ...(userResult.memories ?? []),
          ...(agentResult.memories ?? []),
        ];
        const uniqueMemories = allMemories.filter(
          (memory, index, self) =>
            index === self.findIndex((candidate) => candidate.uri === memory.uri),
        );
        const leafOnly = uniqueMemories.filter((item) => item.level === 2);
        const processed = postProcessMemories(leafOnly, {
          limit: candidateLimit,
          scoreThreshold: cfg.recallScoreThreshold,
        });
        const memories = pickMemoriesForInjection(
          processed,
          cfg.recallLimit,
          queryText,
        );

        if (memories.length === 0) {
          return { estimatedTokens: 0, memories: [] };
        }

        const { estimatedTokens, lines } = await buildMemoryLinesWithBudget(
          memories,
          (uri) => client.read(uri, agentId),
          {
            recallPreferAbstract: cfg.recallPreferAbstract,
            recallMaxContentChars: cfg.recallMaxContentChars,
            recallTokenBudget: cfg.recallTokenBudget,
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
            memories: summarizeInjectionMemories(memories),
          })}`,
        );

        return {
          section:
            "<relevant-memories>\nThe following OpenViking memories may be relevant:\n" +
            `${lines.join("\n")}\n` +
            "</relevant-memories>",
          estimatedTokens,
          memories,
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

export function buildIngestReplyAssistSection(
  queryText: string,
  cfg: Required<MemoryOpenVikingConfig>,
  verboseLog?: (message: string) => void,
): string | undefined {
  if (!cfg.ingestReplyAssist) {
    return undefined;
  }

  const decision = isTranscriptLikeIngest(queryText, {
    minSpeakerTurns: cfg.ingestReplyAssistMinSpeakerTurns,
    minChars: cfg.ingestReplyAssistMinChars,
  });
  if (!decision.shouldAssist) {
    return undefined;
  }

  verboseLog?.(
    `openviking: ingest-reply-assist applied (reason=${decision.reason}, speakerTurns=${decision.speakerTurns}, chars=${decision.chars})`,
  );

  return (
    "<ingest-reply-assist>\n" +
    "The latest user input looks like a multi-speaker transcript used for memory ingestion.\n" +
    "Reply with 1-2 concise sentences to acknowledge or summarize key points.\n" +
    "Do not output NO_REPLY or an empty reply.\n" +
    "Do not fabricate facts beyond the provided transcript and recalled memories.\n" +
    "</ingest-reply-assist>"
  );
}
